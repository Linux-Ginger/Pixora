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

import re
from pathlib import Path
import imagehash
from importer_page import (
    load_settings, perceptual_hash, SUPPORTED_EXT, THRESHOLD_MAP, _VIDEO_EXT,
    build_library_hashes,
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

# ── Replicate EXACTLY what the app does: cached build_library_hashes + grouping
print("\n" + "=" * 60)
print("Replicating the app (cache-based hashes + grouping):")
app_hashes = build_library_hashes(Path(photo_path))
print(f"   build_library_hashes returned {len(app_hashes)} hashes")


def base_name(p):
    return re.sub(r"_\d+$", "", os.path.splitext(os.path.basename(p))[0]).lower()


items = []
for p, h in app_hashes.items():
    try:
        items.append((p, imagehash.hex_to_hash(h)))
    except Exception:
        pass

# Show the app's hash for the matching files (vs the fresh hash above).
if sub:
    print(f"\n   App/cache hashes for '{sub}':")
    found_in_app = False
    for p, h in app_hashes.items():
        if sub in os.path.basename(p).lower():
            found_in_app = True
            print(f"      {os.path.basename(p)}   app_hash={h}")
    if not found_in_app:
        print(f"      (none matching '{sub}' are in the app's hash index!)")

n = len(items)
parent = list(range(n))


def _find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _union(a, b):
    ra, rb = _find(a), _find(b)
    if ra != rb:
        parent[ra] = rb


bases = [base_name(p) for p, _h in items]
for i in range(n):
    for j in range(i + 1, n):
        d = items[i][1] - items[j][1]
        if d <= threshold or (bases[i] == bases[j] and d <= threshold + 6):
            _union(i, j)
comps = {}
for i in range(n):
    comps.setdefault(_find(i), []).append(items[i][0])
groups = [g for g in comps.values() if len(g) > 1]

# Exact-duplicate pass (videos + any unhashed file), via content signature.
import hashlib


def _content_sig(p):
    try:
        size = os.path.getsize(p)
    except OSError:
        return None
    h = hashlib.sha1()
    h.update(str(size).encode())
    chunk = 2 * 1024 * 1024
    try:
        with open(p, "rb") as f:
            h.update(f.read(chunk))
            if size > 2 * chunk:
                f.seek(-chunk, os.SEEK_END)
                h.update(f.read(chunk))
    except OSError:
        return None
    return h.hexdigest()


hashed = set(app_hashes.keys())
sigs = {}
for root, _dirs, fns in os.walk(photo_path):
    for fn in fns:
        if Path(fn).suffix.lower() not in SUPPORTED_EXT:
            continue
        p = str(Path(root) / fn)
        if p in hashed:
            continue
        sg = _content_sig(p)
        if sg:
            sigs.setdefault(sg, []).append(p)
for members in sigs.values():
    if len(members) > 1:
        groups.append(members)

print(f"\n   Groups the app would show: {len(groups)}")
for gi, g in enumerate(groups, 1):
    mark = "  <-- contains your search" if sub and any(
        sub in os.path.basename(p).lower() for p in g) else ""
    print(f"   Group {gi}{mark}:")
    for p in g:
        print(f"        {os.path.basename(p)}")
