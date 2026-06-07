#!/usr/bin/env python3
"""Diagnose a glitchy HEIC: report bit depth, and compare the plain decode
(what Pixora currently does) against an HDR-aware decode.

Usage:  python3 tools/check_heic.py /path/to/IMG.heic
Saves IMG_plain.jpg and IMG_hdr.jpg next to it, and prints each one's average
colour. A green-dominant average (G ≫ R,B) means that decode is broken.
"""
import sys
from pathlib import Path

import pillow_heif

from PIL import Image, ImageOps

print("pillow_heif version:", getattr(pillow_heif, "__version__", "?"))


def avg(im):
    s = im.convert("RGB").resize((64, 64))
    px = list(s.getdata())
    n = len(px)
    return (sum(c[0] for c in px) / n,
            sum(c[1] for c in px) / n,
            sum(c[2] for c in px) / n)


def main():
    if len(sys.argv) < 2:
        print("usage: python3 tools/check_heic.py /path/to/file.heic")
        return
    p = Path(sys.argv[1])
    if not p.exists():
        print("not found:", p)
        return

    print("file size:", p.stat().st_size, "bytes")

    # Raw HEIF info: bit depth tells us if it's a 10-bit HDR image.
    try:
        hf = pillow_heif.open_heif(str(p), convert_hdr_to_8bit=False)
        print("HEIF mode:", hf.mode, "| bit_depth:", getattr(hf, "bit_depth", "?"),
              "| size:", hf.size)
    except Exception as e:
        print("open_heif(raw) failed:", e)

    # Method 1 — plain Image.open (what Pixora does now, via register opener).
    try:
        pillow_heif.register_heif_opener()
        im1 = ImageOps.exif_transpose(Image.open(str(p))).convert("RGB")
        r, g, b = avg(im1)
        print(f"[plain]  avg R={r:.0f} G={g:.0f} B={b:.0f}")
        out1 = p.with_name(p.stem + "_plain.jpg")
        im1.save(out1, "JPEG", quality=95)
        print("  saved:", out1)
    except Exception as e:
        print("plain decode failed:", e)

    # Method 2 — HDR-aware decode (convert 10-bit HDR down to 8-bit properly).
    try:
        hf2 = pillow_heif.open_heif(str(p), convert_hdr_to_8bit=True)
        im2 = hf2.to_pillow() if hasattr(hf2, "to_pillow") else Image.frombytes(
            hf2.mode, hf2.size, hf2.data, "raw", hf2.mode, hf2.stride)
        im2 = ImageOps.exif_transpose(im2).convert("RGB")
        r, g, b = avg(im2)
        print(f"[hdr8]   avg R={r:.0f} G={g:.0f} B={b:.0f}")
        out2 = p.with_name(p.stem + "_hdr.jpg")
        im2.save(out2, "JPEG", quality=95)
        print("  saved:", out2)
    except Exception as e:
        print("hdr-aware decode failed:", e)


if __name__ == "__main__":
    main()
