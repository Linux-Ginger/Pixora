#!/usr/bin/env python3
"""Diagnose why a specific duplicate wasn't detected by 'Clean up duplicates'.

Usage:
  python3 tools/check_dedup_scan.py 3338

Scans your real Pixora library and reports:
  * how many media files have NO perceptual hash (those are SILENTLY skipped
    by duplicate detection — usually HEICs libheif failed to decode, or videos)
  * for files whose name contains the given text, their hashes + the pairwise
    Hamming distance vs. your threshold, so you can see exactly why a pair did
    or didn't match.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "viewer"))

from pathlib import Path
import imagehash
from importer_page import (
    load_settings, perceptual_hash, SUPPORTED_EXT, THRESHOLD_MAP, _VIDEO_EXT,
)

sub = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
s = load_settings() or {}
photo_path = s.get("photo_path") or os.path.expanduser("~/Photos")
thr_key = s.get("duplicate_threshold", 2)
threshold = THRESHOLD_MAP.get(thr_key, 6)

print(f"Library:           {photo_path}")
print(f"Threshold setting: {thr_key}  ->  max distance {threshold}")
print()

files = []
for root, _dirs, fns in os.walk(photo_path):
    for fn in fns:
        if Path(fn).suffix.lower() in SUPPORTED_EXT:
            files.append(Path(root) / fn)

no_hash = []
match = []
for fp in files:
    h = perceptual_hash(fp)          # fresh hash, no cache — tests decodability
    if not h:
        no_hash.append(fp)
    if sub and sub in fp.name.lower():
        match.append((fp, h))

print(f"Total media files:                 {len(files)}")
print(f"Files with NO perceptual hash:     {len(no_hash)}  "
      f"(these are skipped by dedup)")
for fp in no_hash[:50]:
    why = " [video — normal]" if fp.suffix.lower() in _VIDEO_EXT else " [PHOTO — this is the problem]"
    print("    NO HASH:", fp.name, why)

if sub:
    print(f"\nFiles matching '{sub}':")
    for fp, h in match:
        print(f"    {'OK  ' if h else 'FAIL'} {fp.name}   hash={h}")
    hashed = [(fp, h) for fp, h in match if h]
    if len(hashed) >= 2:
        print()
        for i in range(len(hashed)):
            for j in range(i + 1, len(hashed)):
                d = (imagehash.hex_to_hash(hashed[i][1])
                     - imagehash.hex_to_hash(hashed[j][1]))
                verdict = "DUPLICATE" if d <= threshold else "NOT matched (distance > threshold)"
                print(f"    {hashed[i][0].name}  vs  {hashed[j][0].name}"
                      f"  ->  distance {d}  [{verdict}]")
    elif len(hashed) < 2:
        print("\n    Fewer than 2 of these files have a hash -> can't be paired,"
              " so dedup can't flag them.")
