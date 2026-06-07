#!/usr/bin/env python3
"""Compare a phone photo's perceptual hash (read over FUSE, and again from a
local copy) against the archive copy — to find why duplicate detection misses it.

Usage:
  python3 tools/check_phone_hash.py IMG_3469.HEIC [archive_copy.heic ...]
"""
import shutil
import subprocess
import sys
import tempfile
import time
from io import BytesIO
from pathlib import Path

import pillow_heif
pillow_heif.register_heif_opener()
from PIL import Image
import imagehash


def phash_direct(p):
    try:
        with Image.open(p) as im:
            return imagehash.phash(im.convert("RGB"))
    except Exception as e:
        return f"FAIL({e})"


def phash_bytes(p):
    """Read the whole file first, then decode from memory (avoids FUSE seeks)."""
    try:
        with open(p, "rb") as fh:
            data = fh.read()
        with Image.open(BytesIO(data)) as im:
            return imagehash.phash(im.convert("RGB"))
    except Exception as e:
        return f"FAIL({e})"


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "IMG_3469.HEIC"
    archive_args = sys.argv[2:]

    ids = subprocess.run(["idevice_id", "-l"],
                         capture_output=True, text=True).stdout.split()
    if not ids:
        print("no device — connect + unlock")
        return
    mp = Path(tempfile.gettempdir()) / "pixora_hashchk"
    subprocess.run(["fusermount", "-uz", str(mp)], capture_output=True)
    mp.mkdir(exist_ok=True)
    r = subprocess.run(["ifuse", "--udid", ids[0], str(mp)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("mount failed:", r.stderr.strip())
        return
    time.sleep(1)

    results = {}
    try:
        matches = list((mp / "DCIM").rglob(name))
        print(f"found {len(matches)} copy/copies of {name} on the phone:")
        for fp in matches:
            direct = phash_direct(fp)
            tmp = Path(tempfile.gettempdir()) / ("hc_" + fp.name)
            shutil.copy2(fp, tmp)
            local = phash_bytes(tmp)
            tmp.unlink(missing_ok=True)
            sz = fp.stat().st_size // 1024
            print(f"  {fp.relative_to(mp)}  ({sz} KB)")
            print(f"    over-FUSE (direct)  = {direct}")
            print(f"    local copy (bytes)  = {local}")
            if not str(direct).startswith("FAIL") and not str(local).startswith("FAIL"):
                print(f"    FUSE-vs-local distance = {direct - local}")
                results[str(fp)] = local
    finally:
        subprocess.run(["fusermount", "-uz", str(mp)], capture_output=True)

    for ap in archive_args:
        ah = phash_bytes(ap)
        print(f"\narchive {ap}\n    phash = {ah}")
        if not str(ah).startswith("FAIL"):
            for fp, ph in results.items():
                print(f"    distance to phone {Path(fp).name} = {ph - ah}")


if __name__ == "__main__":
    main()
