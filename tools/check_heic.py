#!/usr/bin/env python3
"""Decode a HEIC the way Pixora does and save a JPEG copy, to tell whether a
glitchy photo is a bad file (decoder output is wrong) or a display issue.

Usage:  python3 tools/check_heic.py /path/to/IMG.heic
Then open the saved *_check.jpg in a normal image viewer:
        - JPEG looks green/broken  -> the file/decoder is the problem
        - JPEG looks fine          -> Pixora's display pipeline is the problem
"""
import sys
from pathlib import Path

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception as e:
    print("pillow_heif not available:", e)

from PIL import Image, ImageOps


def main():
    if len(sys.argv) < 2:
        print("usage: python3 tools/check_heic.py /path/to/file.heic")
        return
    p = Path(sys.argv[1])
    if not p.exists():
        print("not found:", p)
        return

    im = Image.open(p)
    print("format:", im.format, "| mode:", im.mode, "| size:", im.size)

    im = ImageOps.exif_transpose(im).convert("RGB")
    im.load()

    # Average channel values on a small sample — a strongly green-dominant
    # average is a hint the decode produced garbage chroma planes.
    small = im.resize((64, 64))
    px = list(small.getdata())
    n = len(px)
    r = sum(c[0] for c in px) / n
    g = sum(c[1] for c in px) / n
    b = sum(c[2] for c in px) / n
    print(f"avg colour  R={r:.0f}  G={g:.0f}  B={b:.0f}")

    out = p.with_name(p.stem + "_check.jpg")
    im.save(out, "JPEG", quality=95)
    print("saved:", out)
    print("now run:  xdg-open", f'"{out}"')


if __name__ == "__main__":
    main()
