#!/usr/bin/env python3
"""Pixora phone diagnostic — shows exactly what media is reachable on a
connected iPhone/iPad over AFC (ifuse), so a "phone says N, Pixora says M"
mismatch can be explained.

Usage:  python3 tools/diag_phone.py
        (connect + unlock the device, tap Trust if asked)
"""
import os
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

SUP = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".dng", ".mp4", ".mov",
       ".m4v", ".webp", ".gif", ".tiff", ".tif", ".3gp", ".bmp"}


def main():
    ids = subprocess.run(["idevice_id", "-l"],
                         capture_output=True, text=True).stdout.split()
    if not ids:
        print("❌ No iPhone found — connect, unlock, and tap Trust.")
        return
    udid = ids[0]

    mp = Path(tempfile.gettempdir()) / "pixora_diag"
    subprocess.run(["fusermount", "-uz", str(mp)], capture_output=True)
    mp.mkdir(exist_ok=True)
    r = subprocess.run(["ifuse", "--udid", udid, str(mp)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("❌ Mount failed:", r.stderr.strip())
        return
    time.sleep(1)

    try:
        print("📂 Top-level dirs on device:",
              sorted(p.name for p in mp.iterdir()))

        dcim = mp / "DCIM"
        cnt = Counter()
        dtot = 0
        for root, _, fs in os.walk(dcim):
            for fn in fs:
                e = Path(fn).suffix.lower()
                cnt[e] += 1
                if e in SUP:
                    dtot += 1
        print(f"\n📷 DCIM supported media: {dtot}")
        print("   all extensions in DCIM:", dict(cnt))

        pd = mp / "PhotoData"
        if pd.exists():
            pc = Counter()
            for root, _, fs in os.walk(pd):
                for fn in fs:
                    e = Path(fn).suffix.lower()
                    if e in SUP:
                        pc[e] += 1
            print(f"\n🗂️  PhotoData media (outside DCIM): {sum(pc.values())}",
                  dict(pc))
            subs = [p.name for p in pd.iterdir() if p.is_dir()]
            print("   PhotoData subdirs:", sorted(subs))
    finally:
        subprocess.run(["fusermount", "-uz", str(mp)], capture_output=True)
        print("\n✅ done (device unmounted)")


if __name__ == "__main__":
    main()
