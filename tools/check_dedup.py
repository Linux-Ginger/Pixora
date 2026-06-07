#!/usr/bin/env python3
"""Diagnose why duplicate detection didn't fire.

Usage:
  python3 tools/check_dedup.py                 # just check imagehash is installed
  python3 tools/check_dedup.py A.heic B.heic   # also hash files + show distance
"""
import sys

try:
    import imagehash
    from PIL import Image
    print("imagehash: INSTALLED (version %s)"
          % getattr(imagehash, "__version__", "?"))
except Exception as e:
    print("imagehash: NOT INSTALLED ->", e)
    print("\n>>> This is the bug: without imagehash, Pixora silently skips")
    print(">>> duplicate detection. Install it:  pip install --user imagehash")
    sys.exit(0)

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    print("pillow_heif: registered")
except Exception as e:
    print("pillow_heif: NOT available ->", e)

hashes = []
for p in sys.argv[1:]:
    try:
        h = imagehash.phash(Image.open(p).convert("RGB"))
        print(f"{p}\n  phash = {h}")
        hashes.append((p, h))
    except Exception as e:
        print(f"{p}\n  FAILED to hash -> {e}")

if len(hashes) >= 2:
    d = hashes[0][1] - hashes[1][1]
    print(f"\nHamming distance between the two = {d}")
    print("(Pixora's default threshold treats <= 6 as the same photo.)")
