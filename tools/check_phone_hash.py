#!/usr/bin/env python3
"""Pin down why a phone photo's perceptual hash doesn't match its archive copy.

For the given DCIM name it reports, for the phone file:
  - phash decoded directly over FUSE (the old, unreliable way)
  - phash from a full byte-read over FUSE (what dedup does now)
  - phash from a local copy (ground truth)
  - whether an edited render exists, and that render's phash
and compares each to any archive copies passed as extra args.

Usage:
  python3 tools/check_phone_hash.py IMG_3338.HEIC [~/Afbeeldingen/Pixora/IMG_3338.heic ...]
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


def ph_direct(p):
    try:
        with Image.open(p) as im:
            return imagehash.phash(im.convert("RGB"))
    except Exception as e:
        return f"FAIL({e})"


def ph_bytes(p):
    try:
        with open(p, "rb") as fh:
            data = fh.read()
        with Image.open(BytesIO(data)) as im:
            return imagehash.phash(im.convert("RGB"))
    except Exception as e:
        return f"FAIL({e})"


def find_render(mount, fp):
    # Mutations mirrors the asset's own path (DCIM/... or PhotoData/CPLAssets/...).
    rel = fp.relative_to(mount)
    adj = mount / "PhotoData" / "Mutations" / rel.parent / fp.stem / "Adjustments"
    try:
        if adj.is_dir():
            for c in sorted(adj.iterdir()):
                if c.stem.lower() == "fullsizerender" and c.is_file():
                    return c
    except OSError:
        pass
    return None


def dist(a, b):
    if str(a).startswith("FAIL") or str(b).startswith("FAIL"):
        return "n/a"
    return a - b


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "IMG_3338.HEIC"
    archives = sys.argv[2:]

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

    try:
        matches = list((mp / "DCIM").rglob(name))
        cpl = mp / "PhotoData" / "CPLAssets"
        if cpl.is_dir():
            matches += list(cpl.rglob(name))
        print(f"found {len(matches)} copy/copies of {name}\n")
        for fp in matches:
            direct = ph_direct(fp)
            tmp = Path(tempfile.gettempdir()) / ("hc_" + fp.name)
            shutil.copy2(fp, tmp)
            local = ph_bytes(tmp)
            tmp.unlink(missing_ok=True)
            over_fuse_bytes = ph_bytes(fp)

            print(f"{fp.relative_to(mp)}  ({fp.stat().st_size // 1024} KB)")
            print(f"  direct over FUSE     = {direct}")
            print(f"  byte-read over FUSE  = {over_fuse_bytes}   (dist vs local: {dist(over_fuse_bytes, local)})")
            print(f"  local copy (truth)   = {local}")

            render = find_render(mp, fp)
            if render:
                rh = ph_bytes(render)
                print(f"  EDITED render exists = {render.name}")
                print(f"    render phash       = {rh}   (dist vs master: {dist(rh, local)})")
            else:
                print("  no edited render (not an edited photo)")

            for ap in archives:
                ah = ph_bytes(ap)
                print(f"  archive {Path(ap).name} phash = {ah}")
                print(f"    dist archive vs master = {dist(ah, local)}")
                if render:
                    print(f"    dist archive vs render = {dist(ah, ph_bytes(render))}")
            print()
    finally:
        subprocess.run(["fusermount", "-uz", str(mp)], capture_output=True)


if __name__ == "__main__":
    main()
