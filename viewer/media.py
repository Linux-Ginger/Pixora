#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — media.py
#  Thumbnails and image decoding
#  by LinuxGinger
# ─────────────────────────────────────────────

import hashlib
import os
import subprocess
import common
from common import CACHE_DIR, Gdk, GdkPixbuf, GObject, is_video


CACHE_VERSION = "v3"

def get_cache_path(photo_path, thumb_size=None):
    if thumb_size is None:
        thumb_size = common.THUMB_SIZE
    mtime = str(os.path.getmtime(photo_path))
    key   = hashlib.md5((photo_path + mtime + str(thumb_size) + CACHE_VERSION).encode()).hexdigest()
    return os.path.join(CACHE_DIR, key + ".png")


# Thumbnails that could not be made this session (unreadable files): don't
# re-run ffmpeg/PIL on them every time the grid scrolls past.
_THUMB_FAILED = set()


def _video_thumb(photo_path, thumb_size, cache_path):
    tmp = cache_path + ".tmp.png"
    # 1 s in skips black first frames; clips shorter than that (Live Photo
    # movies can be) fall back to the very first frame.
    for seek in ("00:00:01", "00:00:00"):
        try:
            # Fixed height; -2 = width divisible by 2.
            subprocess.run(
                ["ffmpeg", "-ss", seek, "-i", photo_path, "-vframes", "1",
                 "-vf", f"scale=-2:{thumb_size}", "-f", "image2", tmp, "-y"],
                capture_output=True, timeout=15
            )
        except Exception:
            continue
        if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, cache_path)
            return GdkPixbuf.Pixbuf.new_from_file(cache_path)
    try:
        os.remove(tmp)
    except OSError:
        pass
    return None


def load_thumbnail(photo_path, thumb_size=None):
    if thumb_size is None:
        thumb_size = common.THUMB_SIZE
    cache_path = get_cache_path(photo_path, thumb_size)
    if os.path.exists(cache_path):
        try:
            return GdkPixbuf.Pixbuf.new_from_file(cache_path)
        except Exception:
            pass
    if cache_path in _THUMB_FAILED:
        return None
    os.makedirs(CACHE_DIR, exist_ok=True)
    if is_video(photo_path):
        try:
            pb = _video_thumb(photo_path, thumb_size, cache_path)
        except Exception:
            pb = None
        if pb is None:
            _THUMB_FAILED.add(cache_path)
        return pb
    try:
        from PIL import Image, ImageOps
        with Image.open(photo_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            w, h = img.size
            if h > 0:
                new_w = max(1, int(w * thumb_size / h))
                img = img.resize((new_w, thumb_size), Image.LANCZOS)
            # Written whole, then swapped in: a crash never leaves a broken
            # thumbnail that would be "cached" forever.
            tmp = cache_path + ".tmp.png"
            img.save(tmp, "PNG")
            os.replace(tmp, cache_path)
        return GdkPixbuf.Pixbuf.new_from_file(cache_path)
    except Exception:
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(photo_path, thumb_size, thumb_size, True)
            tmp = cache_path + ".tmp.png"
            pixbuf.savev(tmp, "png", [], [])
            os.replace(tmp, cache_path)
            return pixbuf
        except Exception:
            _THUMB_FAILED.add(cache_path)
            return None


def decode_pixbuf(path, max_dim):
    """Load a scaled GdkPixbuf, decoding HEIC/HEIF via PIL when GdkPixbuf can't.

    The GNOME stack often ships no HEIC loader, so GdkPixbuf returns a black or
    empty image for iPhone photos even though PIL (with pillow_heif) decodes
    them fine. HEIC/HEIF go straight to PIL; other formats use the fast
    GdkPixbuf path with a PIL fallback.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".heic", ".heif"):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, max_dim, max_dim, True)
            if pb is not None:
                return pb
        except Exception:
            pass
    try:
        from PIL import Image, ImageOps
        from io import BytesIO
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)
            if img.mode not in ("RGB", "RGBA", "L"):
                img = img.convert("RGB")
            buf = BytesIO()
            img.save(buf, "PNG")
        # Go through a PNG buffer rather than new_from_bytes: GdkPixbuf rows from
        # raw RGB data aren't 4-byte aligned when width isn't a multiple of 4,
        # which makes cairo render garbled glitches. PNG decode is always aligned.
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        try:
            loader.write(buf.getvalue())
        finally:
            # Always close, else GdkPixbuf leaks the loader after a failed write.
            try:
                loader.close()
            except Exception:
                pass
        pb = loader.get_pixbuf()
        if pb is None:
            raise RuntimeError("png decode failed")
        return pb
    except Exception:
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, max_dim, max_dim, True)
        except Exception:
            return None


class _FixedSizePaintable(GObject.GObject, Gdk.Paintable):
    # GTK4 Picture sizes the HeaderBar to the paintable's intrinsic size; this
    # wraps a hi-res Texture but reports a smaller intrinsic for layout while
    # the GPU downscales the source on snapshot.
    def __init__(self, source, intrinsic_w, intrinsic_h):
        super().__init__()
        self._source = source
        self._w = intrinsic_w
        self._h = intrinsic_h

    def do_get_intrinsic_width(self):
        return self._w

    def do_get_intrinsic_height(self):
        return self._h

    def do_get_intrinsic_aspect_ratio(self):
        # Use source's true aspect so Picture's CONTAIN preserves the SVG
        # ratio inside the reported intrinsic box.
        return self._source.get_intrinsic_aspect_ratio()

    def do_snapshot(self, snapshot, width, height):
        self._source.snapshot(snapshot, width, height)
