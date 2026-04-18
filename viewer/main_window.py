#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — main_window.py
#  by LinuxGinger
# ─────────────────────────────────────────────

import os
import math
import threading
import subprocess
import sys
import json
import hashlib
import time
import datetime
import urllib.request
import inspect
from collections import defaultdict, OrderedDict
from concurrent.futures import ThreadPoolExecutor

# ── i18n (gettext) ───────────────────────────────────────────────────
import gettext as _gettext_mod
_LOCALE_DIR = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "locale"
))
try:
    with open(os.path.expanduser("~/.config/pixora/settings.json"), "r") as _sf:
        _lang = json.load(_sf).get("language", "nl")
except Exception:
    _lang = "nl"
_translation = _gettext_mod.translation(
    "pixora", localedir=_LOCALE_DIR, languages=[_lang], fallback=True
)
_ = _translation.gettext
_translation.install()  # maakt _() ook als builtin beschikbaar

# ── Dev-mode logging ─────────────────────────────────────────────────
# Lees dev_mode direct uit settings.json — we importeren NIET uit main.py
# omdat dat main's module-level code opnieuw zou uitvoeren (en een tweede
# tail-terminal zou openen).
_CFG = os.path.expanduser("~/.config/pixora/settings.json")
try:
    with open(_CFG, "r") as _f:
        _DEV_MODE = bool(json.load(_f).get("dev_mode", False))
except Exception:
    _DEV_MODE = False
_LOG_COLOR = sys.stdout.isatty()
_LOG_PATH = os.path.expanduser("~/.cache/pixora/pixora.log")
_LOG_FILE = None

def _ensure_log_file():
    global _LOG_FILE
    if _LOG_FILE is not None:
        return
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        _LOG_FILE = open(_LOG_PATH, "a", buffering=1)
    except Exception:
        _LOG_FILE = False

def _log(level, color_code, msg):
    if not _DEV_MODE:
        return  # no-op in normal mode
    frame = inspect.currentframe().f_back.f_back
    loc = f"{os.path.basename(frame.f_code.co_filename)}:{frame.f_lineno}"
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    plain = f"{ts} [{level}] {loc}  {msg}"
    if _LOG_COLOR:
        print(
            f"\033[2m{ts}\033[0m "
            f"\033[{color_code}m[{level}]\033[0m "
            f"\033[2m{loc}\033[0m  {msg}",
            flush=True,
        )
    else:
        print(plain, flush=True)
    _ensure_log_file()
    if _LOG_FILE:
        try:
            _LOG_FILE.write(plain + "\n")
        except Exception:
            pass

def log_info(msg):  _log("INFO",  "36", msg)
def log_warn(msg):  _log("WARN",  "33", msg)
def log_error(msg): _log("ERROR", "1;31", msg)

# In dev mode: enable faulthandler zodat hangs/crashes altijd een thread-
# stackdump produceren. Gebruiker kan 'kill -USR1 <pid>' uitvoeren als
# Pixora vastzit om te zien waar.
if _DEV_MODE:
    import faulthandler
    try:
        _fh_log = open(_LOG_PATH, "a", buffering=1)
        faulthandler.enable(file=_fh_log, all_threads=True)
        try:
            faulthandler.register(
                __import__("signal").SIGUSR1,
                file=_fh_log, all_threads=True
            )
        except Exception:
            pass
    except Exception:
        faulthandler.enable(all_threads=True)

# Vang ongevangen uitzonderingen zodat ze in de terminal/log opvallen met
# bestand:regel + traceback.
if _DEV_MODE:
    import traceback
    def _excepthook(exc_type, exc_val, exc_tb):
        tb_lines = traceback.format_exception(exc_type, exc_val, exc_tb)
        # Zoek de laatste frame uit ons eigen project voor file:line
        frames = traceback.extract_tb(exc_tb)
        loc = "?"
        if frames:
            f = frames[-1]
            loc = f"{os.path.basename(f.filename)}:{f.lineno}"
        header = f"{exc_type.__name__}: {exc_val}"
        if _LOG_COLOR:
            print(f"\033[1;31m[ERROR] {loc}  {header}\033[0m", flush=True)
        else:
            print(f"[ERROR] {loc}  {header}", flush=True)
        for line in tb_lines:
            print(line.rstrip(), flush=True)
        _ensure_log_file()
        if _LOG_FILE:
            try:
                _LOG_FILE.write(f"[ERROR] {loc}  {header}\n")
                for line in tb_lines:
                    _LOG_FILE.write(line)
            except Exception:
                pass
    sys.excepthook = _excepthook

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, GdkPixbuf, Gio, Gdk
gi.require_foreign("cairo")
import cairo

try:
    import cairo
    CAIRO_AVAILABLE = True
except ImportError:
    CAIRO_AVAILABLE = False

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

try:
    gi.require_version("GUdev", "1.0")
    from gi.repository import GUdev
    GUDEV_AVAILABLE = True
except (ValueError, ImportError):
    GUDEV_AVAILABLE = False

WEBKIT_AVAILABLE = False
WebKit2 = None
_webkit_load_error = None

# Ubuntu 24.04's AppArmor blokkeert unprivileged user-namespaces, waardoor
# WebKit's bwrap-sandbox faalt met "Permission denied" en WebKit crasht.
# Zet sandbox-disable VOOR de WebKit-import zodat de env-var effect heeft.
os.environ.setdefault("WEBKIT_DISABLE_SANDBOX", "1")
os.environ.setdefault("WEBKIT_FORCE_SANDBOX", "0")
# Forceer GPU-compositing voor Leaflet (anders valt WebKit terug op
# software rendering in sommige VMs, wat pan/zoom traag maakt).
os.environ.setdefault("WEBKIT_FORCE_COMPOSITING_MODE", "1")
# EGL/DMA-BUF renderer werkt vaak beter in VMs (VMware SVGA3D) dan GLX.
os.environ.setdefault("WEBKIT_USE_EGL", "1")
os.environ.setdefault("GDK_GL", "gles")

# Probeer eerst WebKit 6.0 (GTK4-native, nieuwste). Daarna WebKit2 4.1 / 4.0.
try:
    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit as _WK
    WebKit2 = _WK
    WEBKIT_AVAILABLE = True
except Exception as _e:
    _webkit_load_error = repr(_e)

if not WEBKIT_AVAILABLE:
    for _webkit_version in ("4.1", "4.0"):
        try:
            gi.require_version("WebKit2", _webkit_version)
            from gi.repository import WebKit2 as _WK2
            WebKit2 = _WK2
            WEBKIT_AVAILABLE = True
            _webkit_load_error = None
            break
        except Exception as _e:
            _webkit_load_error = repr(_e)
            continue

DOCS_DIR         = os.path.join(os.path.dirname(__file__), "..", "docs")
VERSION_FILE     = os.path.join(os.path.dirname(__file__), "..", "version.txt")
INSTALL_DIR      = os.path.expanduser("~/.local/share/pixora")
GITHUB_RELEASES_API = "https://api.github.com/repos/Linux-Ginger/pixora/releases/latest"
CONFIG_PATH      = os.path.expanduser("~/.config/pixora/settings.json")
FAVORITES_PATH   = os.path.expanduser("~/.config/pixora/favorites.json")
CACHE_DIR        = os.path.expanduser("~/.cache/pixora/thumbnails")
TILE_CACHE_DIR   = os.path.expanduser("~/.cache/pixora/tiles")
THUMB_SIZE       = 200
FILM_THUMB       = 70
BATCH_SIZE       = 15

def _low_ram_system():
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return (pages * page_size) < (4 * 1024 * 1024 * 1024)
    except Exception:
        return False

THUMB_WORKERS = 2 if _low_ram_system() else 4
TILE_SIZE        = 256
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".mp4", ".mov"}
VIDEO_EXTENSIONS = {".mp4", ".mov"}


def is_video(path):
    return os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS
BACKUP_FSTYPES   = {"ext4","ext3","ext2","ntfs","exfat","fuseblk","btrfs","xfs","vfat"}

MONTHS_NL_FULL = [
    "", "januari", "februari", "maart", "april", "mei", "juni",
    "juli", "augustus", "september", "oktober", "november", "december"
]
MONTHS_NL = [
    "", "jan", "feb", "mrt", "apr", "mei", "jun",
    "jul", "aug", "sep", "okt", "nov", "dec"
]


def get_logo_path(dark_mode):
    return os.path.join(DOCS_DIR, f"pixora-logo-{'dark' if dark_mode else 'light'}.png")

def save_settings(settings):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(settings, f, indent=2)


def load_favorites():
    try:
        with open(FAVORITES_PATH, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_favorites(favorites):
    try:
        os.makedirs(os.path.dirname(FAVORITES_PATH), exist_ok=True)
        with open(FAVORITES_PATH, "w") as f:
            json.dump(sorted(favorites), f, indent=2)
    except Exception:
        pass

def get_available_drives():
    drives = []
    try:
        result = subprocess.run(
            ["lsblk", "-o", "NAME,UUID,LABEL,SIZE,FSTYPE,MOUNTPOINT,HOTPLUG", "-J"],
            capture_output=True, text=True
        )
        data = json.loads(result.stdout)
        def process_device(device):
            hotplug = device.get("hotplug", False)
            if not hotplug:
                return
            uuid       = device.get("uuid")
            fstype     = (device.get("fstype") or "").lower()
            label      = (device.get("label") or "").strip()
            size       = device.get("size") or ""
            mountpoint = (device.get("mountpoint") or "").strip()
            if uuid and fstype in BACKUP_FSTYPES:
                display = (f"💾  {label}  ({size})" if label else
                           f"💾  {mountpoint}  ({size})" if mountpoint else
                           f"💾  Externe schijf  ({size})")
                drives.append((uuid, display))
            for child in device.get("children", []):
                child["hotplug"] = hotplug
                process_device(child)
        for device in data.get("blockdevices", []):
            process_device(device)
    except Exception as e:
        log_error(f"Drive detectie fout: {e}")
    return drives

def get_mountpoint_for_uuid(uuid):
    try:
        result = subprocess.run(["lsblk", "-o", "UUID,MOUNTPOINT", "-J"], capture_output=True, text=True)
        data = json.loads(result.stdout)
        for device in data.get("blockdevices", []):
            for child in device.get("children", [device]):
                if child.get("uuid") == uuid:
                    return child.get("mountpoint")
    except Exception:
        pass
    return None

_MONTH_KEYS = [
    "", "januari", "februari", "maart", "april", "mei", "juni",
    "juli", "augustus", "september", "oktober", "november", "december"
]

def format_date_header(dt):
    return f"{dt.day} {_(_MONTH_KEYS[dt.month])} {dt.year}"


# ── Metadata-cache (video-duur, foto-datum, GPS, geocode) ─────────────
# Spaart zware EXIF- en ffprobe-calls bij iedere grid-reload/map-open.
_METADATA_CACHE_PATH = os.path.expanduser("~/.cache/pixora/metadata.json")
_metadata_cache = {
    "video_duration": {},
    "photo_date": {},
    "gps_coords": {},
    "geocode": {},
}
_metadata_dirty = False
_metadata_save_lock = threading.Lock()


def _load_metadata_cache():
    try:
        with open(_METADATA_CACHE_PATH, "r") as f:
            data = json.load(f)
        for k in _metadata_cache.keys():
            v = data.get(k)
            if isinstance(v, dict):
                _metadata_cache[k] = v
    except Exception:
        pass


def save_metadata_cache():
    global _metadata_dirty
    if not _metadata_dirty:
        return
    with _metadata_save_lock:
        try:
            os.makedirs(os.path.dirname(_METADATA_CACHE_PATH), exist_ok=True)
            tmp = _METADATA_CACHE_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(_metadata_cache, f)
            os.replace(tmp, _METADATA_CACHE_PATH)
            _metadata_dirty = False
        except Exception:
            pass


def _cache_fresh(bucket, path):
    entry = _metadata_cache[bucket].get(path)
    if not entry:
        return None
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        return None
    if abs(entry.get("m", 0) - mtime) < 0.5:
        return entry.get("v")
    return None


def _cache_put(bucket, path, value):
    global _metadata_dirty
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        return
    _metadata_cache[bucket][path] = {"m": mtime, "v": value}
    _metadata_dirty = True


_load_metadata_cache()


def get_gps_coords(photo_path):
    cached = _cache_fresh("gps_coords", photo_path)
    if cached is not None:
        return tuple(cached) if cached else None
    result = _get_gps_coords_raw(photo_path)
    _cache_put("gps_coords", photo_path, list(result) if result else None)
    return result


def _get_gps_coords_raw(photo_path):
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS, GPSTAGS
        img  = Image.open(photo_path)
        if img.mode == "P" and "transparency" in img.info:
            img = img.convert("RGBA")
        exif = img._getexif()
        if not exif:
            return None
        gps_info = {}
        for tag, value in exif.items():
            tag_name = TAGS.get(tag, tag)
            if tag_name == "GPSInfo":
                for gps_tag, gps_value in value.items():
                    gps_info[GPSTAGS.get(gps_tag, gps_tag)] = gps_value
        if not gps_info:
            return None
        def to_decimal(coords, ref):
            d, m, s = coords
            decimal = float(d) + float(m) / 60 + float(s) / 3600
            if ref in ["S", "W"]:
                decimal = -decimal
            return decimal
        lat = to_decimal(gps_info["GPSLatitude"],  gps_info.get("GPSLatitudeRef",  "N"))
        lon = to_decimal(gps_info["GPSLongitude"], gps_info.get("GPSLongitudeRef", "E"))
        return (lat, lon)
    except Exception:
        return None

def reverse_geocode(lat, lon):
    global _metadata_dirty
    key = f"{lat:.4f},{lon:.4f}"
    cached = _metadata_cache["geocode"].get(key)
    if cached is not None:
        return cached
    result = _reverse_geocode_raw(lat, lon)
    _metadata_cache["geocode"][key] = result or ""
    _metadata_dirty = True
    return result


def _reverse_geocode_raw(lat, lon):
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "Pixora/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        addr = data.get("address", {})
        city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality") or ""
        country = addr.get("country", "")
        if city and country:
            return f"{city}, {country}"
        elif country:
            return country
        return ""
    except Exception:
        return ""

def lat_lon_to_tile_float(lat, lon, zoom):
    n     = 2 ** zoom
    x     = (lon + 180) / 360 * n
    lat_r = math.radians(lat)
    y     = (1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n
    return x, y

_EXIF_DATE_TAGS = (36867, 36868, 306)  # DateTimeOriginal, DateTimeDigitized, DateTime

def get_photo_date(path: str) -> float:
    cached = _cache_fresh("photo_date", path)
    if cached is not None:
        return float(cached)
    value = _get_photo_date_raw(path)
    _cache_put("photo_date", path, value)
    return value


def _get_photo_date_raw(path: str) -> float:
    """Geeft de fotodatum als timestamp. Probeert EXIF eerst, valt terug op mtime."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg", ".heic", ".png", ".dng"):
        try:
            from PIL import Image
            with Image.open(path) as img:
                exif = img._getexif()
            if exif:
                for tag in _EXIF_DATE_TAGS:
                    val = exif.get(tag)
                    if val:
                        dt = datetime.datetime.strptime(val[:19], "%Y:%m:%d %H:%M:%S")
                        return dt.timestamp()
        except Exception:
            pass
    return os.path.getmtime(path)


CACHE_VERSION = "v3"

def get_cache_path(photo_path, thumb_size=None):
    if thumb_size is None:
        thumb_size = THUMB_SIZE
    mtime = str(os.path.getmtime(photo_path))
    key   = hashlib.md5((photo_path + mtime + str(thumb_size) + CACHE_VERSION).encode()).hexdigest()
    return os.path.join(CACHE_DIR, key + ".png")

def get_video_duration(path):
    cached = _cache_fresh("video_duration", path)
    if cached is not None:
        return float(cached)
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True, timeout=5
        )
        data = json.loads(result.stdout)
        dur = float(data.get("format", {}).get("duration", 0))
    except Exception:
        dur = 0.0
    _cache_put("video_duration", path, dur)
    return dur


def format_duration(seconds):
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def get_video_gps_coords(path):
    import re
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True, timeout=5
        )
        data = json.loads(result.stdout)
        tags = data.get("format", {}).get("tags", {})
        loc = (tags.get("location") or
               tags.get("com.apple.quicktime.location.ISO6709") or "")
        if loc:
            m = re.match(r'([+-]\d+\.\d+)([+-]\d+\.\d+)', loc)
            if m:
                return (float(m.group(1)), float(m.group(2)))
    except Exception:
        pass
    return None


def load_thumbnail(photo_path, thumb_size=None):
    if thumb_size is None:
        thumb_size = THUMB_SIZE
    cache_path = get_cache_path(photo_path, thumb_size)
    if os.path.exists(cache_path):
        try:
            return GdkPixbuf.Pixbuf.new_from_file(cache_path)
        except Exception:
            pass
    os.makedirs(CACHE_DIR, exist_ok=True)
    if is_video(photo_path):
        try:
            # Vaste hoogte, breedte volgt aspect (-2 = deelbaar door 2)
            subprocess.run(
                ["ffmpeg", "-i", photo_path, "-ss", "00:00:01", "-vframes", "1",
                 "-vf", f"scale=-2:{thumb_size}",
                 cache_path, "-y"],
                capture_output=True, timeout=15
            )
            return GdkPixbuf.Pixbuf.new_from_file(cache_path)
        except Exception:
            return None
    try:
        from PIL import Image, ImageOps
        with Image.open(photo_path) as img:
            img = ImageOps.exif_transpose(img)  # Respecteer EXIF-rotatie
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            # Schaal naar vaste hoogte, breedte volgt aspect
            w, h = img.size
            if h > 0:
                new_w = max(1, int(w * thumb_size / h))
                img = img.resize((new_w, thumb_size), Image.LANCZOS)
            img.save(cache_path, "PNG")
        return GdkPixbuf.Pixbuf.new_from_file(cache_path)
    except Exception:
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(photo_path, thumb_size, thumb_size, True)
            pixbuf.savev(cache_path, "png", [], [])
            return pixbuf
        except Exception:
            return None


# ── File watcher ─────────────────────────────────────────────────────
class PhotoFolderHandler(FileSystemEventHandler):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self._timer   = None

    def _schedule_reload(self):
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(1.5, lambda: GLib.idle_add(self.callback))
        self._timer.start()

    def on_created(self, event):
        if not event.is_directory and os.path.splitext(event.src_path)[1].lower() in IMAGE_EXTENSIONS:
            self._schedule_reload()

    def on_deleted(self, event):
        if not event.is_directory and os.path.splitext(event.src_path)[1].lower() in IMAGE_EXTENSIONS:
            self._schedule_reload()

    def on_moved(self, event):
        self._schedule_reload()


# ── Tijdlijn scrollbar ───────────────────────────────────────────────
class TimelineBar(Gtk.DrawingArea):
    """Right-side timeline bar.

    Entries are stored as (label, y_px, is_year) where y_px is the actual
    Y pixel position of that date header inside the grid_box.  The bar maps
    those pixel values to proportional positions using max_scroll
    (= adj.upper - adj.page_size), so everything stays consistent with the
    scroll adjustment — no fractions, no conversion errors.
    """
    _ORANGE = (0.914, 0.329, 0.125)

    def __init__(self, scroll_cb, style_manager=None):
        super().__init__()
        self._scroll_cb    = scroll_cb
        self.style_manager = style_manager
        self._entries      = []   # [(label, y_px, is_year), ...]  sorted by y_px
        self._active       = 0
        self._scroll_val   = 0.0
        self._max_scroll   = 1.0

        self.set_size_request(52, -1)
        self.set_vexpand(True)
        self.set_draw_func(self._draw)

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click)
        self.add_controller(click)

    # ── Public API ────────────────────────────────────────────────────

    def set_data(self, entries, max_scroll):
        """Replace all entries and redraw.  Called after loading finishes."""
        self._entries    = entries
        self._max_scroll = max(max_scroll, 1.0)
        self._recalc()
        self.queue_draw()

    def update_scroll(self, value, max_scroll):
        """Called on every scroll-position change."""
        self._scroll_val = value
        self._max_scroll = max(max_scroll, 1.0)
        self._recalc()

    # ── Internal ──────────────────────────────────────────────────────

    def _recalc(self):
        """Find the active entry: last one whose y_px <= current scroll."""
        if not self._entries:
            return
        new_active = 0
        for i, (_, y_px, _) in enumerate(self._entries):
            if y_px <= self._scroll_val:
                new_active = i
        if new_active != self._active:
            self._active = new_active
            self.queue_draw()

    def _draw(self, area, cr, width, height):
        if not self._entries or height == 0:
            return
        is_dark    = self.style_manager and self.style_manager.get_dark()
        last_bot   = -99.0
        n          = len(self._entries)
        max_scroll = max(self._max_scroll, 1.0)

        for i, (label, y_px, is_year) in enumerate(self._entries):
            active = (i == self._active)

            # Font
            if active or is_year:
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                font_size = 10 if active else 8
            else:
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
                font_size = 9

            cr.set_font_size(font_size)
            ext = cr.text_extents(label)

            # Ideal position proportional to y_px in the grid
            ideal_y = min(y_px / max_scroll, 1.0) * height
            ty = max(last_bot + 2, ideal_y)
            ty = max(ext.height, min(height - 2, ty))

            # Skip crowded labels except the very last one
            if ty >= height - 2 and i < n - 1:
                continue

            # Colour
            if active:
                cr.set_source_rgb(*self._ORANGE)
            elif is_year:
                v = 0.85 if is_dark else 0.3
                cr.set_source_rgba(v, v, v, 0.8)
            else:
                v = 0.75 if is_dark else 0.4
                cr.set_source_rgba(v, v, v, 0.65)

            cr.move_to(max(2, width - ext.width - 4), ty)
            cr.show_text(label)
            last_bot = ty + font_size + 1

    def _on_click(self, gesture, n_press, x, y):
        height = self.get_height()
        if height > 0:
            self._scroll_cb((y / height) * self._max_scroll)


# ── Kaart widget (Leaflet in WebView) ────────────────────────────────
class MapWidget(Gtk.Box):
    def __init__(self, markers, open_photo_cb, status_cb=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.markers = markers
        self.open_photo_cb = open_photo_cb
        self.status_cb = status_cb
        self.set_vexpand(True)
        self.set_hexpand(True)
        self._pending_markers = list(markers) if markers else []

        if not WEBKIT_AVAILABLE:
            self._show_fallback(
                "WebKitGTK kon niet geladen worden.\n\n"
                "Installeer een van:\n"
                "  sudo apt install gir1.2-webkit-6.0\n"
                "  sudo apt install gir1.2-webkit2-4.1\n"
                + (f"\nTechnische fout: {_webkit_load_error}" if _webkit_load_error else "")
            )
            log_error(f"WebKit niet beschikbaar: {_webkit_load_error}")
            return

        try:
            self._init_webview()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            log_error(f"WebView init crashte:\n{tb}")
            self._show_fallback(
                "Kaart-weergave kon niet starten.\n\n"
                f"Fout: {e}\n\nCheck /home/beau/.cache/pixora/pixora.log voor meer."
            )

    def _show_fallback(self, msg):
        lbl = Gtk.Label(label=msg)
        lbl.set_vexpand(True)
        lbl.set_hexpand(True)
        lbl.set_justify(Gtk.Justification.CENTER)
        lbl.set_wrap(True)
        self.append(lbl)

    def _init_webview(self):
        # Sandbox uit VOOR WebView-creation. Ubuntu 24.04 blokkeert
        # unprivileged user-namespaces systeem-wijd; WebKit's bwrap-sandbox
        # faalt dan met "Permission denied" en crasht het proces. Door
        # WebKit's eigen sandbox uit te zetten wordt bwrap niet aangeroepen;
        # de kernel-AppArmor-restrictie blijft intact voor andere apps.
        network_session = None
        try:
            # WebKit 6.0: NetworkSession heeft set_sandbox_enabled
            if hasattr(WebKit2, "NetworkSession"):
                try:
                    network_session = WebKit2.NetworkSession.get_default()
                except Exception:
                    try:
                        network_session = WebKit2.NetworkSession.new_ephemeral()
                    except Exception:
                        network_session = None
                if network_session and hasattr(network_session, "set_sandbox_enabled"):
                    network_session.set_sandbox_enabled(False)
        except Exception as e:
            log_warn(f"NetworkSession sandbox-disable faalde: {e}")

        try:
            # WebKit2 4.x: WebContext heeft set_sandbox_enabled
            if hasattr(WebKit2, "WebContext"):
                wc = WebKit2.WebContext.get_default()
                if wc and hasattr(wc, "set_sandbox_enabled"):
                    wc.set_sandbox_enabled(False)
        except Exception as e:
            log_warn(f"WebContext sandbox-disable faalde: {e}")

        # Probeer WebView te maken mét een custom NetworkSession (WebKit 6.0)
        self.web = None
        if network_session is not None and hasattr(WebKit2, "WebView"):
            try:
                self.web = WebKit2.WebView.new_with_network_session(network_session)
            except Exception:
                self.web = None
        if self.web is None:
            self.web = WebKit2.WebView()

        self.web.set_vexpand(True)
        self.web.set_hexpand(True)

        try:
            wk_settings = self.web.get_settings()
            wk_settings.set_enable_javascript(True)
            wk_settings.set_javascript_can_access_clipboard(False)
            wk_settings.set_enable_developer_extras(False)
        except Exception as e:
            log_warn(f"WebView settings niet volledig gezet: {e}")

        try:
            ucm = self.web.get_user_content_manager()
            registered = False
            for args in (("pixora",), ("pixora", None), ("pixora", "")):
                try:
                    ucm.register_script_message_handler(*args)
                    registered = True
                    break
                except Exception:
                    continue
            if not registered:
                log_error(_("register_script_message_handler mislukt met alle varianten"))
            ucm.connect("script-message-received::pixora", self._on_js_message)
        except Exception as e:
            log_error(f"WebView bridge setup fout: {e}")

        self.web.connect("load-changed", self._on_load_changed)

        assets_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "assets", "leaflet"
        )
        map_html_path = os.path.abspath(os.path.join(assets_dir, "map.html"))
        if not os.path.exists(map_html_path):
            self._show_fallback(f"Leaflet-assets niet gevonden:\n{map_html_path}")
            return

        self.web.load_uri("file://" + map_html_path)
        self.append(self.web)

    def _on_load_changed(self, web, event):
        try:
            finished = (event == WebKit2.LoadEvent.FINISHED)
        except Exception:
            finished = False
        if finished:
            GLib.timeout_add(80, self._push_markers)

    def _push_markers(self):
        data = []
        for m in self._pending_markers:
            path = m[4]
            try:
                thumb = get_cache_path(path, THUMB_SIZE)
                if not os.path.exists(thumb):
                    thumb = None
            except Exception:
                thumb = None
            data.append({
                "lat": m[0], "lon": m[1],
                "filename": m[2], "date": m[3],
                "path": path, "thumb": thumb,
            })
        labels = {
            "otherInCluster": _("andere foto's in deze cluster"),
            "clickCluster": _("Klik cluster om gefilterd te bekijken"),
            "clickOpen": _("Klik om te openen"),
            "offline": _("⚠ Geen internetverbinding — kaart-tiles kunnen niet geladen worden"),
        }
        js = (
            f"if(window.pixoraSetLabels){{window.pixoraSetLabels({json.dumps(labels)});}}"
            f"if(window.pixoraSetMarkers){{window.pixoraSetMarkers({json.dumps(data)});}}"
        )
        ran = False
        try:
            self.web.evaluate_javascript(js, -1, None, None, None, None, None)
            ran = True
        except Exception:
            pass
        if not ran:
            try:
                self.web.run_javascript(js, None, None, None)
            except Exception as e:
                log_error(f"JS push fout: {e}")
        return False

    def _on_js_message(self, ucm, message):
        try:
            if hasattr(message, "to_string"):
                raw = message.to_string()
            else:
                raw = message.get_js_value().to_string()
            payload = json.loads(raw)
        except Exception:
            return
        msg_type = payload.get("type")
        if msg_type == "open_photos":
            paths = payload.get("paths") or []
            log_info(f"Kaart → open_photos: {len(paths)} foto's")
            if paths:
                GLib.idle_add(self.open_photo_cb, paths)
        elif msg_type == "open_photo":
            path = payload.get("path")
            log_info(f"Kaart → open_photo: {path}")
            if path:
                GLib.idle_add(self.open_photo_cb, [path])
        elif msg_type == "map-ready":
            log_info(_("Kaart → eerste tiles geladen"))
            if self.status_cb:
                GLib.idle_add(self.status_cb, "ready")
        elif msg_type == "map-offline":
            log_warn(_("Kaart → offline / tile-errors"))
            if self.status_cb:
                GLib.idle_add(self.status_cb, "offline")


# ── Hoofdvenster ─────────────────────────────────────────────────────
class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, settings):
        super().__init__(application=app)
        self.settings        = settings
        # Thumbnail-grootte uit instellingen (globale constante wordt hier overschreven)
        global THUMB_SIZE
        try:
            THUMB_SIZE = max(200, min(500, int(settings.get("thumbnail_size", 200))))
        except Exception:
            THUMB_SIZE = 200
        if settings.get("dev_mode"):
            log_info(_("═══ Pixora gestart in Developer Mode ═══"))
            log_info(_("Config: {p}").format(p=CONFIG_PATH))
            log_info(_("Cache: {p}").format(p=CACHE_DIR))
            log_info(_("Thumbs: {px}px — favorites: {n}").format(px=THUMB_SIZE, n=len(load_favorites())))
            log_info(_("PID: {p} — on hang: 'kill -USR1 {p}' dumps thread-stacks").format(p=os.getpid()))
        log_info(_("Startup fase 1: MainWindow __init__ begonnen"))
        self.photos          = []
        self.thumb_widgets   = {}
        self.date_widgets    = {}
        self.current_index   = 0
        self.settings_drives = []
        self.observer        = None
        self._loading        = False
        self._load_id        = 0
        self._viewer_load_id = 0
        self._sort_timer     = None
        self._select_mode    = False
        self._selected       = set()
        self._photo_location = {}
        self._viewer_zoom    = 1.0
        self._viewer_offset  = [0.0, 0.0]
        self._viewer_drag    = None
        self._map_widget     = None
        self._viewer_pixbuf  = None
        self._editor_active         = False
        self._editor_rotation       = 0
        self._editor_crop_mode      = False
        self._editor_display_pixbuf = None
        self._crop_rect             = None   # [x1, y1, x2, y2] widget coords
        self._crop_handle           = None   # 'tl','tr','bl','br','move'
        self._crop_rect_origin      = None   # rect state at drag start
        self._filmstrip_thumbs      = {}     # index -> pixbuf
        self._filmstrip_load_id     = 0
        self._video_media           = None
        self._video_poll_id         = None
        self._video_scrubbing_lock  = False
        self._video_seek_pending_id = None
        self._preview_cache         = OrderedDict()  # LRU: timestamp_s -> pixbuf | None
        self._viewer_pixbuf_cache   = OrderedDict()  # LRU: path -> pixbuf, max 3
        self._preview_debounce_id   = None
        self._preview_extracting    = False
        self._preview_pending_ts    = None
        self._fade_timer_id         = None
        self._fade_anim_id          = None
        self._favorites             = load_favorites()
        self._favorites_save_id     = None
        self._favorites_only        = False
        self._current_flow          = None
        self._current_row_hbox      = None
        self._current_row_width     = 0

        self.set_title("Pixora (Dev Mode)" if self.settings.get("dev_mode") else "Pixora")
        self.set_default_size(9999, 9999)

        self.style_manager = Adw.StyleManager.get_default()
        self.style_manager.connect("notify::dark", self.on_dark_mode_changed)

        from importer_page import ImporterPage
        self.importer_page = ImporterPage(
            on_back_cb=self.close_importer,
            on_done_cb=self.on_import_done,
        )

        log_info(_("Startup fase 2: pages opbouwen…"))
        self.main_stack = Gtk.Stack()
        self.main_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.main_stack.set_transition_duration(200)
        self.main_stack.add_named(self.build_grid_page(),   "grid")
        self.main_stack.add_named(self.build_viewer_page(), "viewer")
        self.main_stack.add_named(self.build_map_page(),    "map")
        self.main_stack.add_named(self.importer_page,       "importer")
        log_info(_("Startup fase 3: pages klaar"))

        self.update_banner = Adw.Banner(title="", button_label="Bijwerken", use_markup=False)
        self.update_banner.set_revealed(False)
        self.update_banner.connect("button-clicked", self._on_update_banner_clicked)

        self.iphone_banner = Adw.Banner(title="", use_markup=False)
        self.iphone_banner.set_revealed(False)

        self.toolbar_view = Adw.ToolbarView()
        self.toolbar_view.add_top_bar(self.update_banner)
        self.toolbar_view.add_top_bar(self.iphone_banner)
        self.toolbar_view.add_top_bar(self.build_header())
        self.toolbar_view.set_content(self.main_stack)
        self.toolbar_view.add_bottom_bar(self.build_bottombar())
        toolbar_view = self.toolbar_view

        # ── Startup splash overlay ────────────────────────────────────
        root_overlay = Gtk.Overlay()
        root_overlay.set_child(toolbar_view)

        splash = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        splash.set_halign(Gtk.Align.FILL)
        splash.set_valign(Gtk.Align.FILL)
        splash_css = Gtk.CssProvider()
        splash_css.load_from_string("box.splash { background-color: @window_bg_color; }")
        splash.add_css_class("splash")
        splash.get_style_context().add_provider(splash_css, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        splash_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        splash_inner.set_halign(Gtk.Align.CENTER)
        splash_inner.set_valign(Gtk.Align.CENTER)
        splash_inner.set_vexpand(True)

        splash_spinner = Gtk.Spinner()
        splash_spinner.set_size_request(48, 48)
        splash_spinner.set_halign(Gtk.Align.CENTER)
        splash_spinner.start()

        splash_lbl = Gtk.Label(label=_("Pixora wordt gestart…"))
        splash_lbl.add_css_class("title-2")

        self._splash_bar = Gtk.ProgressBar()
        self._splash_bar.set_size_request(280, -1)
        self._splash_bar.set_halign(Gtk.Align.CENTER)

        splash_inner.append(splash_spinner)
        splash_inner.append(splash_lbl)
        splash_inner.append(self._splash_bar)
        splash.append(splash_inner)

        root_overlay.add_overlay(splash)
        self._splash = splash
        self._splash_start = time.time()
        self._splash_prewarm_done = False
        GLib.timeout_add(80, self._update_splash)
        threading.Thread(target=self._prewarm_gstreamer, daemon=True).start()

        self.set_content(root_overlay)

        photo_path = self.settings.get("photo_path", "")
        if photo_path:
            os.makedirs(photo_path, exist_ok=True)

        self.set_hide_on_close(False)
        Gtk.Settings.get_default().set_property("gtk-decoration-layout", "icon:minimize,close")

        self.set_resizable(False)
        log_info(_("Startup fase 4: foto's laden gepland via idle_add"))
        GLib.idle_add(self.load_photos)
        self.connect("close-request", self.on_close)
        GLib.idle_add(self._check_for_update)
        threading.Thread(target=self._start_services, daemon=True).start()
        self._ios_device_present = False
        self._recovery_prompt_active = False
        self._recovery_cooldown_until = 0.0
        GLib.idle_add(self._poll_import_device)
        GLib.timeout_add_seconds(10, self._poll_import_device)
        self._setup_usb_monitor()

    def _start_services(self):
        try:
            r = subprocess.run(["pgrep", "-x", "usbmuxd"],
                               capture_output=True, timeout=3)
            if r.returncode != 0:
                subprocess.Popen(["usbmuxd"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def _setup_usb_monitor(self):
        """Luister naar udev USB-events voor automatische iPhone-detectie."""
        self._udev_client = None
        if not GUDEV_AVAILABLE:
            return
        try:
            self._udev_client = GUdev.Client(subsystems=["usb"])
            self._udev_client.connect("uevent", self._on_usb_event)
        except Exception as e:
            log_error(f"GUdev monitor kon niet starten: {e}")
            self._udev_client = None

    def _on_usb_event(self, client, action, device):
        if action != "add":
            return
        try:
            vendor = device.get_property("ID_VENDOR_ID")
        except Exception:
            vendor = None
        if vendor != "05ac":  # Apple
            return
        log_info(_("Apple USB-device aangesloten (vendor=05ac) — check na 2.5s"))
        # Wacht kort zodat usbmuxd het device kan zien, daarna controleren
        GLib.timeout_add(2500, self._post_apple_plugin_check)

    def _post_apple_plugin_check(self):
        # Niet storen als importer open staat
        try:
            if self.main_stack.get_visible_child_name() == "importer":
                return False
        except Exception:
            pass
        if self._recovery_prompt_active:
            return False
        self._recovery_prompt_active = True
        self._set_iphone_banner("📱 iPhone gedetecteerd, even geduld…")
        threading.Thread(target=self._iphone_recovery_flow, daemon=True).start()
        return False

    def _iphone_recovery_flow(self):
        """Volledig automatische recovery: eerst check, dan reset bij falen."""
        # Eerste check
        has_device = self._idevice_check()
        if has_device:
            log_info(_("iPhone direct herkend door usbmuxd"))
            GLib.idle_add(self._iphone_flow_success, False)
            return
        # Niet herkend — automatisch reset
        log_warn(_("iPhone niet herkend door usbmuxd — start auto-recovery"))
        GLib.idle_add(self._set_iphone_banner,
                      "🔧 Verbinding herstellen, even geduld…")
        reset_ok = False
        try:
            r = subprocess.run(
                ["pkexec", "sh", "-c",
                 "killall usbmuxd 2>/dev/null; sleep 0.5; usbmuxd"],
                capture_output=True, text=True, timeout=40
            )
            reset_ok = (r.returncode == 0)
            log_info(f"usbmuxd reset rc={r.returncode}")
        except Exception as e:
            log_error(f"usbmuxd reset fout: {e}")
            reset_ok = False
        if not reset_ok:
            GLib.idle_add(self._iphone_flow_fail)
            return
        # Wacht opnieuw tot usbmuxd + device klaar zijn
        time.sleep(2.5)
        has_device = self._idevice_check()
        if has_device:
            log_info(_("iPhone herkend na reset"))
            GLib.idle_add(self._iphone_flow_success, True)
        else:
            log_warn(_("iPhone blijft onherkenbaar na reset"))
            GLib.idle_add(self._iphone_flow_fail)

    def _idevice_check(self):
        try:
            result = subprocess.run(
                ["idevice_id", "-l"],
                capture_output=True, text=True, timeout=4
            )
            return any(l.strip() for l in result.stdout.splitlines())
        except Exception:
            return False

    def _iphone_flow_success(self, was_reset):
        self._recovery_prompt_active = False
        self._update_import_btn_state(True)
        self._set_iphone_banner(
            "✅ iPhone klaar — tap Trust op je iPhone indien gevraagd"
            if was_reset else "✅ iPhone verbonden"
        )
        GLib.timeout_add_seconds(4, self._clear_iphone_banner)
        return False

    def _iphone_flow_fail(self):
        self._recovery_prompt_active = False
        self._set_iphone_banner(
            "⚠️ iPhone niet herkend — probeer Instellingen > iPhone-verbinding"
        )
        GLib.timeout_add_seconds(8, self._clear_iphone_banner)
        return False

    def _set_iphone_banner(self, text):
        self.iphone_banner.set_title(text)
        self.iphone_banner.set_revealed(True)
        return False

    def _clear_iphone_banner(self):
        self.iphone_banner.set_revealed(False)
        return False

    def _poll_import_device(self):
        # Niet pollen terwijl de importer open is — voorkomt interferentie
        # met pair/mount van libimobiledevice
        try:
            if self.main_stack.get_visible_child_name() == "importer":
                return True
        except Exception:
            pass

        def check():
            # Zorg dat usbmuxd loopt — als die is gecrasht werkt de iPhone niet
            try:
                r = subprocess.run(["pgrep", "-x", "usbmuxd"],
                                   capture_output=True, timeout=2)
                if r.returncode != 0:
                    subprocess.Popen(["usbmuxd"], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
            except Exception:
                pass
            has_device = False
            try:
                result = subprocess.run(
                    ["idevice_id", "-l"],
                    capture_output=True, text=True, timeout=3
                )
                has_device = any(l.strip() for l in result.stdout.splitlines())
            except Exception:
                has_device = False
            GLib.idle_add(self._update_import_btn_state, has_device)
        threading.Thread(target=check, daemon=True).start()
        return True

    def _update_import_btn_state(self, has_device):
        if has_device == self._ios_device_present:
            return False
        self._ios_device_present = has_device
        ctx = self.import_btn.get_style_context()
        if has_device:
            ctx.add_class("pixora-import-active")
            self.import_btn.set_tooltip_text(
                "iPhone of iPad gedetecteerd — klik om te importeren"
            )
        else:
            ctx.remove_class("pixora-import-active")
            self.import_btn.set_tooltip_text(_("Importeer van iPhone of iPad"))
        return False

    # ── Startup splash ───────────────────────────────────────────────
    def _prewarm_gstreamer(self):
        """Initialize GStreamer pipeline in background so first video opens fast."""
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            Gst.init(None)
            pipeline = Gst.parse_launch("audiotestsrc wave=4 num-buffers=1 ! fakesink")
            pipeline.set_state(Gst.State.PLAYING)
            pipeline.get_bus().timed_pop_filtered(
                Gst.CLOCK_TIME_NONE,
                Gst.MessageType.EOS | Gst.MessageType.ERROR
            )
            pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass
        finally:
            GLib.idle_add(self._on_prewarm_done)

    def _on_prewarm_done(self):
        self._splash_prewarm_done = True
        return False

    def _update_splash(self):
        elapsed = time.time() - self._splash_start
        min_time = 2.0   # always show at least 2 seconds
        max_time = 30.0  # hard cap
        if self._splash_prewarm_done:
            # fill up progress bar quickly then close
            self._splash_bar.set_fraction(min(elapsed / min_time, 1.0))
            if elapsed >= min_time:
                self._splash.set_visible(False)
                return False
        else:
            # pulse toward ~80%, reserve last 20% for when prewarm completes
            self._splash_bar.set_fraction(min(elapsed / max_time * 0.8, 0.8))
            if elapsed >= max_time:
                self._splash.set_visible(False)
                return False
        return True

    # ── Update systeem ───────────────────────────────────────────────
    def _check_for_update(self):
        threading.Thread(target=self._do_update_check, daemon=True).start()
        return False

    def _do_update_check(self):
        try:
            local_version_file = os.path.join(
                os.path.expanduser("~"), ".config", "pixora", "installed_version"
            )
            if not os.path.exists(local_version_file):
                return
            with open(local_version_file) as f:
                local_version = f.read().strip()
            req = urllib.request.Request(
                "https://raw.githubusercontent.com/Linux-Ginger/Pixora/main/version.txt",
                headers={"User-Agent": "Pixora/1.0"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                remote_version = resp.read().decode().strip()
            if remote_version and remote_version != local_version:
                GLib.idle_add(self._show_update_message_dialog, remote_version)
        except Exception:
            pass

    def _show_update_message_dialog(self, new_version):
        dlg = Adw.AlertDialog(
            heading="Update beschikbaar",
            body=f"Pixora {new_version} is beschikbaar. Wil je nu bijwerken?",
        )
        dlg.add_response("later", _("Later"))
        dlg.add_response("bijwerken", _("Bijwerken"))
        dlg.set_response_appearance("bijwerken", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", self._on_update_dialog_response, new_version)
        dlg.present(self)
        return False

    def _on_update_dialog_response(self, dlg, response, new_version):
        if response == "bijwerken":
            self._open_installer()
        else:
            self.update_banner.set_title(f"Update beschikbaar: {new_version}")
            self.update_banner.set_revealed(True)

    def _on_update_banner_clicked(self, banner):
        self._open_installer()

    def _open_installer(self):
        log_info(_("GUI-updater gestart"))
        updater_path = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "updater.py"
        ))
        try:
            subprocess.Popen([sys.executable, updater_path],
                             start_new_session=True)
        except Exception as e:
            log_error(f"GUI-updater kon niet starten: {e}")
            return
        # Trigger on_close (met z'n 2s force-exit fallback) i.p.v.
        # app.quit() dat de GTK-loop wel stopt maar non-daemon threads +
        # WebKit-subprocess kan laten hangen. Als dat gebeurt blijft
        # Pixora als zombie in process-lijst en blokkeert nieuwe launches.
        GLib.idle_add(self.close)

    def _on_settings_check_update(self, btn):
        self._update_check_btn.set_visible(False)
        self._update_check_spinner.set_visible(True)
        self._update_check_spinner.start()
        threading.Thread(target=self._do_settings_update_check, daemon=True).start()

    def _do_settings_update_check(self):
        try:
            local_version_file = os.path.join(
                os.path.expanduser("~"), ".config", "pixora", "installed_version"
            )
            local_version = open(local_version_file).read().strip() if os.path.exists(local_version_file) else ""
            req = urllib.request.Request(
                "https://raw.githubusercontent.com/Linux-Ginger/Pixora/main/version.txt",
                headers={"User-Agent": "Pixora/1.0"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                remote_version = resp.read().decode().strip()
        except Exception:
            GLib.idle_add(self._settings_update_result, None, None)
            return
        GLib.idle_add(self._settings_update_result, local_version, remote_version)

    def _settings_update_result(self, local_version, remote_version):
        self._update_check_spinner.stop()
        self._update_check_spinner.set_visible(False)

        # verwijder eerder toegevoegde suffix widgets (behalve spinner/knop)
        for w in list(self._update_check_row._extra_suffixes if hasattr(self._update_check_row, "_extra_suffixes") else []):
            self._update_check_row.remove(w)
        self._update_check_row._extra_suffixes = []

        if remote_version is None:
            self._update_check_row.set_subtitle(_("Controleren mislukt"))
            self._update_check_btn.set_visible(True)
            return False

        if local_version == remote_version:
            self._update_check_row.set_subtitle(_("Je hebt de nieuwste versie"))
            ok_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            ok_icon.add_css_class("success")
            ok_icon.set_valign(Gtk.Align.CENTER)
            self._update_check_row.add_suffix(ok_icon)
            self._update_check_row._extra_suffixes = [ok_icon]
        else:
            self._update_check_row.set_subtitle(f"Versie {remote_version} beschikbaar")
            warn_icon = Gtk.Image.new_from_icon_name("emblem-important-symbolic")
            warn_icon.set_valign(Gtk.Align.CENTER)
            self._update_check_row.add_suffix(warn_icon)
            update_btn = Gtk.Button(label=_("Bijwerken"))
            update_btn.add_css_class("suggested-action")
            update_btn.set_valign(Gtk.Align.CENTER)
            update_btn.connect("clicked", lambda b: self._open_installer())
            self._update_check_row.add_suffix(update_btn)
            self._update_check_row._extra_suffixes = [warn_icon, update_btn]
        return False

    # ── Dark mode ────────────────────────────────────────────────────
    def is_dark(self):
        return self.style_manager.get_dark()

    def on_dark_mode_changed(self, manager, _):
        logo_path = get_logo_path(self.is_dark())
        if os.path.exists(logo_path):
            self.logo_picture.set_filename(logo_path)

    # ── File watcher ─────────────────────────────────────────────────
    def start_watcher(self, path):
        if not WATCHDOG_AVAILABLE or not os.path.exists(path):
            return
        self.stop_watcher()
        handler       = PhotoFolderHandler(self.reload_photos)
        self.observer = Observer()
        self.observer.schedule(handler, path, recursive=True)
        self.observer.start()

    def stop_watcher(self):
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None

    def reload_photos(self):
        self._photo_location.clear()
        self.load_photos()
        return False

    def _schedule_save_favorites(self):
        if self._favorites_save_id is not None:
            try:
                GLib.source_remove(self._favorites_save_id)
            except Exception:
                pass
        self._favorites_save_id = GLib.timeout_add(250, self._flush_save_favorites)

    def _flush_save_favorites(self):
        self._favorites_save_id = None
        try:
            save_favorites(self._favorites)
        except Exception as e:
            log_error(f"Favorites save fout: {e}")
        return False

    def on_close(self, window):
        log_info(_("Pixora wordt afgesloten — opruimen…"))
        try:
            self._load_id += 1
            self._viewer_load_id += 1
            self._filmstrip_load_id += 1
        except Exception:
            pass
        # Persist alle pending caches/writes
        try:
            if self._favorites_save_id is not None:
                GLib.source_remove(self._favorites_save_id)
                self._favorites_save_id = None
                save_favorites(self._favorites)
        except Exception:
            pass
        try:
            save_metadata_cache()
        except Exception:
            pass
        self.stop_watcher()
        # Stop USB-monitor
        try:
            self._udev_client = None
        except Exception:
            pass
        # Stop video playback + MediaFile explicitly (GStreamer-threads loslaten)
        try:
            self._stop_video()
            if getattr(self, "_video_media", None):
                try:
                    self._video_media.unprepare() if hasattr(self._video_media, "unprepare") else None
                except Exception:
                    pass
                self._video_media = None
        except Exception:
            pass
        # Laat grote structuren los zodat RAM vrijkomt
        try:
            self.photos = []
            self.thumb_widgets = {}
            self.date_widgets = {}
            self._filmstrip_thumbs = {}
            self._preview_cache.clear()
            self._viewer_pixbuf_cache.clear()
            self._photo_location.clear()
            if hasattr(self, "_viewer_pixbuf"):
                self._viewer_pixbuf = None
            if hasattr(self, "_editor_display_pixbuf"):
                self._editor_display_pixbuf = None
            if hasattr(self, "_map_widget") and self._map_widget:
                self._map_widget = None
        except Exception as e:
            log_error(f"Cleanup-fout: {e}")
        # Trigger garbage collect
        try:
            import gc
            gc.collect()
        except Exception:
            pass
        # Sluit dev-terminal als die open staat
        try:
            import main as _main_mod
            _main_mod.kill_dev_terminal()
        except Exception:
            pass
        # Wis de dev-log file zodat volgende sessie fris begint
        global _LOG_FILE
        try:
            if _LOG_FILE:
                try:
                    _LOG_FILE.close()
                except Exception:
                    pass
                _LOG_FILE = None
            if os.path.exists(_LOG_PATH):
                open(_LOG_PATH, "w").close()
        except Exception:
            pass
        # Forceer exit als Python niet binnen 2s stopt — voorkomt dat
        # lingering non-daemon-threads (GStreamer, PIL, gvfs-workers) het
        # proces in geheugen houden.
        def _force_exit():
            try:
                print("Pixora proces forceert exit (lingering threads)", flush=True)
            except Exception:
                pass
            os._exit(0)
        threading.Timer(2.0, _force_exit).start()
        return False

    # ── Header ───────────────────────────────────────────────────────
    def build_header(self):
        self.header = Adw.HeaderBar()
        self.header.add_css_class("flat")

        logo_path = get_logo_path(self.is_dark())
        self.logo_picture = Gtk.Picture()
        if os.path.exists(logo_path):
            self.logo_picture.set_filename(logo_path)
        self.logo_picture.set_size_request(140, 36)
        self.logo_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.header.pack_start(self.logo_picture)

        self.sort_model = Gtk.StringList()
        for item in [_("Datum (nieuwste eerst)"), _("Datum (oudste eerst)"),
                     _("Naam (A-Z)"), _("Naam (Z-A)")]:
            self.sort_model.append(item)

        self.sort_combo = Gtk.DropDown(model=self.sort_model)
        self.sort_combo.set_size_request(180, -1)
        self.sort_combo.connect("notify::selected", self.on_sort_changed)
        self.header.pack_start(self.sort_combo)

        self.favorites_toggle = Gtk.ToggleButton()
        self.favorites_toggle.set_icon_name("starred-symbolic")
        self.favorites_toggle.add_css_class("flat")
        self.favorites_toggle.set_tooltip_text(_("Alleen favorieten tonen"))
        self.favorites_toggle.connect("toggled", self.toggle_favorites_filter)
        self.header.pack_end(self.favorites_toggle)

        self.map_btn = Gtk.Button(label=_("🗺"))
        self.map_btn.add_css_class("flat")
        self.map_btn.set_tooltip_text(_("Kaartweergave"))
        self.map_btn.connect("clicked", self.open_map)
        self.header.pack_end(self.map_btn)

        self.import_btn = Gtk.Button(icon_name="phone-symbolic")
        self.import_btn.add_css_class("flat")
        self.import_btn.set_tooltip_text(_("Importeer van iPhone of iPad"))
        self.import_btn.connect("clicked", self.open_importer)
        self._import_btn_css = Gtk.CssProvider()
        self._import_btn_css.load_from_string(
            "button.pixora-import-active {"
            "  background-color: #e95420;"
            "  color: white;"
            "}"
            "button.pixora-import-active:hover {"
            "  background-color: #d84a15;"
            "}"
        )
        self.import_btn.get_style_context().add_provider(
            self._import_btn_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.header.pack_end(self.import_btn)

        self.select_btn = Gtk.Button(label=_("Selecteren"))
        self.select_btn.add_css_class("flat")
        self.select_btn.connect("clicked", self.toggle_select_mode)
        self.header.pack_end(self.select_btn)

        settings_btn = Gtk.Button(icon_name="preferences-system-symbolic")
        settings_btn.add_css_class("flat")
        settings_btn.set_tooltip_text(_("Instellingen"))
        settings_btn.connect("clicked", self.on_settings_clicked)
        self.header.pack_end(settings_btn)

        return self.header

    # ── Grid pagina ───────────────────────────────────────────────────
    def build_grid_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_vexpand(True)
        outer.set_hexpand(True)

        self.content_stack = Gtk.Stack()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_transition_duration(150)
        self.content_stack.set_vexpand(True)
        self.content_stack.set_hexpand(True)

        spinner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        spinner_box.set_halign(Gtk.Align.CENTER)
        spinner_box.set_valign(Gtk.Align.CENTER)
        spinner_box.set_vexpand(True)

        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(48, 48)
        self.spinner_label = Gtk.Label(label=_("Foto's laden..."))
        self.spinner_label.add_css_class("dim-label")
        spinner_box.append(self.spinner)
        spinner_box.append(self.spinner_label)
        self.content_stack.add_named(spinner_box, "loading")

        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_vexpand(True)
        self.scroll.set_hexpand(True)

        self.grid_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.grid_box.set_margin_top(8)
        self.grid_box.set_margin_bottom(8)
        self.grid_box.set_margin_start(8)
        self.grid_box.set_margin_end(8)

        self.scroll.set_child(self.grid_box)
        try:
            vadj = self.scroll.get_vadjustment()
            vadj.connect("value-changed", lambda *_: self._schedule_viewport_hydrate())
            vadj.connect("changed", lambda *_: self._schedule_viewport_hydrate())
        except Exception:
            pass
        self.content_stack.add_named(self.scroll, "grid")

        status_page = Adw.StatusPage()
        status_page.set_icon_name("image-missing-symbolic")
        status_page.set_title(_("Geen foto's gevonden"))
        status_page.set_description(_("Sluit je iPhone of iPad aan om foto's te importeren"))
        status_page.set_vexpand(True)
        status_page.set_hexpand(True)
        self.content_stack.add_named(status_page, "empty")

        self.content_stack.set_visible_child_name("loading")
        outer.append(self.content_stack)
        return outer

    # ── Kaart pagina (in-app) ─────────────────────────────────────────
    def build_map_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_vexpand(True)
        box.set_hexpand(True)

        map_header = Adw.HeaderBar()
        map_header.add_css_class("flat")

        back_btn = Gtk.Button(icon_name="go-previous-symbolic")
        back_btn.add_css_class("flat")
        back_btn.set_tooltip_text(_("Terug"))
        back_btn.connect("clicked", self.close_map)
        map_header.pack_start(back_btn)

        self.map_title_label = Gtk.Label(label=_("Kaartweergave"))
        self.map_title_label.add_css_class("dim-label")
        map_header.set_title_widget(self.map_title_label)

        box.append(map_header)

        self.map_container = Gtk.Stack()
        self.map_container.set_vexpand(True)
        self.map_container.set_hexpand(True)

        map_spinner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        map_spinner_box.set_halign(Gtk.Align.CENTER)
        map_spinner_box.set_valign(Gtk.Align.CENTER)
        map_spinner_box.set_vexpand(True)
        self.map_spinner = Gtk.Spinner()
        self.map_spinner.set_size_request(48, 48)
        self.map_spinner_label = Gtk.Label(label=_("Reisverhaal samenstellen…"))
        self.map_spinner_label.add_css_class("dim-label")
        map_spinner_box.append(self.map_spinner)
        map_spinner_box.append(self.map_spinner_label)
        self.map_container.add_named(map_spinner_box, "loading")

        self.map_content = Gtk.Box()
        self.map_content.set_vexpand(True)
        self.map_content.set_hexpand(True)
        self.map_container.add_named(self.map_content, "map")
        self.map_container.set_visible_child_name("loading")

        box.append(self.map_container)

        return box

    def open_map(self, btn=None):
        log_info(f"Kaart geopend ({len(self.photos)} foto's gaan naar GPS-scan)")
        self.header.set_visible(False)
        self.bottom_stack.set_visible(False)
        try:
            self.toolbar_view.set_reveal_top_bars(False)
            self.toolbar_view.set_reveal_bottom_bars(False)
        except Exception:
            pass
        self.map_btn.set_label(_("🗺 laden..."))
        self.map_btn.set_sensitive(False)
        self.map_container.set_visible_child_name("loading")
        self.map_spinner.start()
        self.main_stack.set_visible_child_name("map")
        threading.Thread(target=self._load_gps_and_show_map, daemon=True).start()

    def _load_gps_and_show_map(self):
        def scan_one(path):
            if is_video(path):
                coords = get_video_gps_coords(path)
            else:
                coords = get_gps_coords(path)
            if not coords:
                return None
            lat, lon = coords
            filename = os.path.basename(path)
            try:
                mtime = os.path.getmtime(path)
                datum = datetime.datetime.fromtimestamp(mtime).strftime("%-d %B %Y")
            except Exception:
                datum = ""
            return (lat, lon, filename, datum, path)

        markers = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            for result in pool.map(scan_one, self.photos):
                if result is not None:
                    markers.append(result)
        GLib.idle_add(self._show_map, markers)

    def _show_map(self, markers):
        if self._map_widget:
            self.map_content.remove(self._map_widget)
            self._map_widget = None

        # Spinner blijft, label naar "Verbinden..." tot eerste tiles binnen zijn.
        try:
            self.map_spinner_label.set_text(_("Verbinden met kaart-server…"))
        except Exception:
            pass

        self._map_widget = MapWidget(
            markers, self._open_photo_from_map,
            status_cb=self._on_map_status
        )
        self.map_content.append(self._map_widget)
        # NIET switchen naar "map" view; wacht op map-ready status-callback.
        # Als er na 12s nog niks is → fallback zodat Pixora niet oneindig hangt.
        self._map_ready_fallback_id = GLib.timeout_add_seconds(
            12, self._on_map_ready_timeout
        )
        self.map_title_label.set_text(_("Kaartweergave"))
        self.map_btn.set_label(_("🗺"))
        self.map_btn.set_sensitive(True)
        return False

    def _on_map_status(self, status):
        if status == "ready":
            if getattr(self, "_map_ready_fallback_id", None):
                try:
                    GLib.source_remove(self._map_ready_fallback_id)
                except Exception:
                    pass
                self._map_ready_fallback_id = None
            try:
                self.map_spinner.stop()
                self.map_container.set_visible_child_name("map")
            except Exception:
                pass
        elif status == "offline":
            # Toon alsnog de kaart — de JS-banner in map.html vertelt de user
            # dat tiles niet geladen kunnen. Markers blijven zichtbaar.
            if getattr(self, "_map_ready_fallback_id", None):
                try:
                    GLib.source_remove(self._map_ready_fallback_id)
                except Exception:
                    pass
                self._map_ready_fallback_id = None
            try:
                self.map_spinner.stop()
                self.map_container.set_visible_child_name("map")
            except Exception:
                pass
        return False

    def _on_map_ready_timeout(self):
        # Fallback: na 12s tonen we de kaart sowieso, ook zonder tile-load.
        log_warn(_("Kaart-ready timeout — toon kaart alsnog"))
        self._map_ready_fallback_id = None
        try:
            self.map_spinner.stop()
            self.map_container.set_visible_child_name("map")
        except Exception:
            pass
        return False

    def _open_photo_from_map(self, paths):
        if isinstance(paths, str):
            paths = [paths]
        photos_set = set(self.photos)
        valid = [p for p in paths if p in photos_set]
        if not valid:
            return
        self.close_map()
        if len(valid) == 1:
            index = self.photos.index(valid[0])
            GLib.idle_add(self.open_photo, index)
        else:
            # Cluster: toon gefilterde grid met alleen die foto's
            self._photos_before_cluster = self.photos
            self.photos = valid
            # Toon locatienaam in header als beschikbaar
            loc = self._photo_location.get(valid[0], "")
            if loc:
                self._cluster_location_label = f"Gefilterd op locatie: {loc}"
            else:
                self._cluster_location_label = f"Gefilterd ({len(valid)} foto's)"
            self.photo_count_label.set_text(self._cluster_location_label)
            try:
                self.clear_filter_btn.set_visible(True)
            except Exception:
                pass
            GLib.idle_add(self.start_load)

    def on_clear_cluster_filter(self, btn=None):
        if not hasattr(self, "_photos_before_cluster") or not self._photos_before_cluster:
            return
        log_info(_("Cluster-filter uitgezet → alle foto's"))
        self.photos = self._photos_before_cluster
        self._photos_before_cluster = None
        self._cluster_location_label = None
        self.photo_count_label.set_text(f"{len(self.photos)} foto's")
        try:
            self.clear_filter_btn.set_visible(False)
        except Exception:
            pass
        GLib.idle_add(self.start_load)

    def close_map(self, btn=None):
        log_info(_("Kaart gesloten"))
        self.header.set_visible(True)
        self.bottom_stack.set_visible(True)
        try:
            self.toolbar_view.set_reveal_top_bars(True)
            self.toolbar_view.set_reveal_bottom_bars(True)
        except Exception:
            pass
        self.main_stack.set_visible_child_name("grid")

    # ── Viewer pagina ─────────────────────────────────────────────────
    def build_viewer_page(self):
        viewer_area = Gtk.Overlay()
        viewer_area.set_vexpand(True)
        viewer_area.set_hexpand(True)

        bg = Gtk.Box()
        bg.set_vexpand(True)
        bg.set_hexpand(True)
        css = Gtk.CssProvider()
        css.load_from_string("box { background-color: black; }")
        bg.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        viewer_area.set_child(bg)

        self.photo_picture = Gtk.Picture()
        self.photo_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.photo_picture.set_vexpand(True)
        self.photo_picture.set_hexpand(True)
        viewer_area.add_overlay(self.photo_picture)

        self.video_display = Gtk.Picture()
        self.video_display.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.video_display.set_vexpand(True)
        self.video_display.set_hexpand(True)
        self.video_display.set_visible(False)
        viewer_area.add_overlay(self.video_display)

        self.viewer_close_btn = Gtk.Button(icon_name="window-close-symbolic")
        self.viewer_close_btn.add_css_class("osd")
        self.viewer_close_btn.add_css_class("circular")
        self.viewer_close_btn.set_halign(Gtk.Align.END)
        self.viewer_close_btn.set_valign(Gtk.Align.START)
        self.viewer_close_btn.set_margin_top(16)
        self.viewer_close_btn.set_margin_end(16)
        self.viewer_close_btn.set_size_request(40, 40)
        self.viewer_close_btn.connect("clicked", self.close_viewer)
        viewer_area.add_overlay(self.viewer_close_btn)

        self.viewer_delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
        self.viewer_delete_btn.add_css_class("osd")
        self.viewer_delete_btn.add_css_class("circular")
        self.viewer_delete_btn.set_halign(Gtk.Align.END)
        self.viewer_delete_btn.set_valign(Gtk.Align.START)
        self.viewer_delete_btn.set_margin_top(16)
        self.viewer_delete_btn.set_margin_end(68)
        self.viewer_delete_btn.set_size_request(40, 40)
        self.viewer_delete_btn.connect("clicked", self.on_delete_current)
        viewer_area.add_overlay(self.viewer_delete_btn)

        self.edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
        self.edit_btn.add_css_class("osd")
        self.edit_btn.add_css_class("circular")
        self.edit_btn.set_halign(Gtk.Align.END)
        self.edit_btn.set_valign(Gtk.Align.START)
        self.edit_btn.set_margin_top(16)
        self.edit_btn.set_margin_end(120)
        self.edit_btn.set_size_request(40, 40)
        self.edit_btn.set_tooltip_text(_("Foto bewerken"))
        self.edit_btn.connect("clicked", self.on_edit_current)
        viewer_area.add_overlay(self.edit_btn)

        self.favorite_btn = Gtk.Button(icon_name="non-starred-symbolic")
        self.favorite_btn.add_css_class("osd")
        self.favorite_btn.add_css_class("circular")
        self.favorite_btn.set_halign(Gtk.Align.END)
        self.favorite_btn.set_valign(Gtk.Align.START)
        self.favorite_btn.set_margin_top(16)
        self.favorite_btn.set_margin_end(172)
        self.favorite_btn.set_size_request(40, 40)
        self.favorite_btn.set_tooltip_text(_("Markeer als favoriet (F)"))
        self.favorite_btn.connect("clicked", self.on_toggle_favorite)
        self._favorite_css = Gtk.CssProvider()
        self._favorite_css.load_from_string(
            "button.pixora-fav { color: #e95420; }"
        )
        self.favorite_btn.get_style_context().add_provider(
            self._favorite_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        viewer_area.add_overlay(self.favorite_btn)

        # ── Editor toolbar (verborgen tot editor modus) ────────────────
        self.editor_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.editor_bar.set_halign(Gtk.Align.CENTER)
        self.editor_bar.set_valign(Gtk.Align.END)
        self.editor_bar.set_margin_bottom(FILM_THUMB + 12 + 16)
        self.editor_bar.set_visible(False)

        rot_left_btn = Gtk.Button(icon_name="object-rotate-left-symbolic")
        rot_left_btn.add_css_class("osd")
        rot_left_btn.add_css_class("circular")
        rot_left_btn.set_size_request(48, 48)
        rot_left_btn.set_tooltip_text(_("Draaien links"))
        rot_left_btn.connect("clicked", self.on_editor_rotate_left)
        self.editor_bar.append(rot_left_btn)

        rot_right_btn = Gtk.Button(icon_name="object-rotate-right-symbolic")
        rot_right_btn.add_css_class("osd")
        rot_right_btn.add_css_class("circular")
        rot_right_btn.set_size_request(48, 48)
        rot_right_btn.set_tooltip_text(_("Draaien rechts"))
        rot_right_btn.connect("clicked", self.on_editor_rotate_right)
        self.editor_bar.append(rot_right_btn)

        self.crop_toggle_btn = Gtk.ToggleButton(label="✂")
        self.crop_toggle_btn.add_css_class("osd")
        self.crop_toggle_btn.add_css_class("circular")
        self.crop_toggle_btn.set_size_request(48, 48)
        self.crop_toggle_btn.set_tooltip_text(_("Bijsnijden"))
        self.crop_toggle_btn.connect("toggled", self.on_editor_toggle_crop)
        self.editor_bar.append(self.crop_toggle_btn)

        save_btn = Gtk.Button(icon_name="document-save-symbolic")
        save_btn.add_css_class("osd")
        save_btn.add_css_class("circular")
        save_btn.add_css_class("suggested-action")
        save_btn.set_size_request(48, 48)
        save_btn.set_tooltip_text(_("Opslaan"))
        save_btn.connect("clicked", self.on_editor_save)
        self.editor_bar.append(save_btn)

        cancel_editor_btn = Gtk.Button(icon_name="window-close-symbolic")
        cancel_editor_btn.add_css_class("osd")
        cancel_editor_btn.add_css_class("circular")
        cancel_editor_btn.set_size_request(48, 48)
        cancel_editor_btn.set_tooltip_text(_("Annuleren"))
        cancel_editor_btn.connect("clicked", self.on_editor_cancel)
        self.editor_bar.append(cancel_editor_btn)

        viewer_area.add_overlay(self.editor_bar)

        # ── Crop overlay DrawingArea ───────────────────────────────────
        self.crop_overlay_area = Gtk.DrawingArea()
        self.crop_overlay_area.set_draw_func(self.on_crop_draw)
        self.crop_overlay_area.set_vexpand(True)
        self.crop_overlay_area.set_hexpand(True)
        self.crop_overlay_area.set_visible(False)

        crop_drag = Gtk.GestureDrag()
        crop_drag.connect("drag-begin",  self.on_crop_drag_begin)
        crop_drag.connect("drag-update", self.on_crop_drag_update)
        crop_drag.connect("drag-end",    self.on_crop_drag_end)
        self.crop_overlay_area.add_controller(crop_drag)
        viewer_area.add_overlay(self.crop_overlay_area)

        self.viewer_title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.viewer_title_box.add_css_class("osd")
        self.viewer_title_box.set_halign(Gtk.Align.START)
        self.viewer_title_box.set_valign(Gtk.Align.START)
        self.viewer_title_box.set_margin_top(16)
        self.viewer_title_box.set_margin_start(16)

        self.viewer_title = Gtk.Label(label="")
        self.viewer_title.set_halign(Gtk.Align.START)
        self.viewer_title_box.append(self.viewer_title)

        self.viewer_location = Gtk.Label(label="")
        self.viewer_location.add_css_class("dim-label")
        self.viewer_location.set_halign(Gtk.Align.START)
        self.viewer_title_box.append(self.viewer_location)

        viewer_area.add_overlay(self.viewer_title_box)

        self.prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.prev_btn.add_css_class("osd")
        self.prev_btn.add_css_class("circular")
        self.prev_btn.set_halign(Gtk.Align.START)
        self.prev_btn.set_valign(Gtk.Align.CENTER)
        self.prev_btn.set_margin_start(16)
        self.prev_btn.set_margin_bottom(105)
        self.prev_btn.set_size_request(48, 48)
        self.prev_btn.connect("clicked", self.prev_photo)
        viewer_area.add_overlay(self.prev_btn)

        self.next_btn = Gtk.Button(icon_name="go-next-symbolic")
        self.next_btn.add_css_class("osd")
        self.next_btn.add_css_class("circular")
        self.next_btn.set_halign(Gtk.Align.END)
        self.next_btn.set_valign(Gtk.Align.CENTER)
        self.next_btn.set_margin_end(16)
        self.next_btn.set_margin_bottom(105)
        self.next_btn.set_size_request(48, 48)
        self.next_btn.connect("clicked", self.next_photo)
        viewer_area.add_overlay(self.next_btn)

        self.viewer_counter = Gtk.Label(label="")
        self.viewer_counter.add_css_class("osd")
        self.viewer_counter.set_halign(Gtk.Align.CENTER)
        self.viewer_counter.set_valign(Gtk.Align.END)
        self.viewer_counter.set_margin_bottom(FILM_THUMB + 12 + 16)
        viewer_area.add_overlay(self.viewer_counter)

        scroll_ctrl = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL |
            Gtk.EventControllerScrollFlags.DISCRETE
        )
        scroll_ctrl.connect("scroll", self.on_viewer_scroll)
        viewer_area.add_controller(scroll_ctrl)

        drag_ctrl = Gtk.GestureDrag.new()
        drag_ctrl.connect("drag-begin",  self.on_viewer_drag_begin)
        drag_ctrl.connect("drag-update", self.on_viewer_drag_update)
        drag_ctrl.connect("drag-end",    self.on_viewer_drag_end)
        viewer_area.add_controller(drag_ctrl)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_ctrl.connect("key-pressed", self.on_viewer_key)
        self.add_controller(key_ctrl)

        viewer_motion = Gtk.EventControllerMotion()
        viewer_motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        viewer_motion.connect("motion", self._on_viewer_motion)
        viewer_area.add_controller(viewer_motion)
        self.viewer_area = viewer_area

        # ── Video controls ───────────────────────────────────────────
        self.video_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.video_controls.add_css_class("osd")
        self.video_controls.set_halign(Gtk.Align.FILL)
        self.video_controls.set_valign(Gtk.Align.END)
        self.video_controls.set_margin_bottom(FILM_THUMB + 12 + 8)
        self.video_controls.set_margin_start(16)
        self.video_controls.set_margin_end(16)
        self.video_controls.set_visible(False)

        self.video_play_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.video_play_btn.add_css_class("flat")
        self.video_play_btn.add_css_class("circular")
        self.video_play_btn.set_can_focus(False)
        self.video_play_btn.connect("clicked", self._on_video_play_pause)
        self.video_controls.append(self.video_play_btn)

        self.video_scrubber = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.001)
        self.video_scrubber.set_hexpand(True)
        self.video_scrubber.set_draw_value(False)
        self.video_scrubber.add_css_class("video-scrubber")
        scrubber_css = Gtk.CssProvider()
        scrubber_css.load_from_string(
            "scale.video-scrubber trough { border-radius: 6px; min-height: 5px; }"
            " scale.video-scrubber fill { border-radius: 6px; }"
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            scrubber_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.video_scrubber.connect("value-changed", self._on_video_scrub)

        # Scrubber preview popover
        self._preview_popover = Gtk.Popover()
        self._preview_popover.set_parent(self.video_scrubber)
        self._preview_popover.set_autohide(False)
        self._preview_popover.set_has_arrow(True)
        self._preview_popover.set_position(Gtk.PositionType.TOP)
        prev_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        prev_box.set_margin_top(4)
        prev_box.set_margin_bottom(4)
        prev_box.set_margin_start(4)
        prev_box.set_margin_end(4)
        self._preview_picture = Gtk.Picture()
        self._preview_picture.set_size_request(160, 90)
        self._preview_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        prev_pic_css = Gtk.CssProvider()
        prev_pic_css.load_from_string("picture { border-radius: 8px; }")
        self._preview_picture.get_style_context().add_provider(
            prev_pic_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._preview_time_lbl = Gtk.Label()
        self._preview_time_lbl.add_css_class("caption")
        prev_box.append(self._preview_picture)
        prev_box.append(self._preview_time_lbl)
        self._preview_popover.set_child(prev_box)

        scrubber_motion = Gtk.EventControllerMotion()
        scrubber_motion.connect("motion", self._on_scrubber_hover)
        scrubber_motion.connect("leave",  self._on_scrubber_leave)
        self.video_scrubber.add_controller(scrubber_motion)

        self.video_controls.append(self.video_scrubber)

        self.video_time_label = Gtk.Label(label=_("0:00 / 0:00"))
        self.video_time_label.set_width_chars(13)
        self.video_controls.append(self.video_time_label)

        sep1 = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep1.set_margin_top(8)
        sep1.set_margin_bottom(8)
        self.video_controls.append(sep1)

        self.video_mute_btn = Gtk.Button(icon_name="audio-volume-high-symbolic")
        self.video_mute_btn.add_css_class("flat")
        self.video_mute_btn.add_css_class("circular")
        self.video_mute_btn.set_can_focus(False)
        self.video_mute_btn.connect("clicked", self._on_video_mute)
        self.video_controls.append(self.video_mute_btn)

        self.video_vol_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.05)
        self.video_vol_scale.set_value(1.0)
        self.video_vol_scale.set_size_request(90, -1)
        self.video_vol_scale.set_draw_value(False)
        self.video_vol_scale.connect("value-changed", self._on_video_volume)
        self.video_controls.append(self.video_vol_scale)

        viewer_area.add_overlay(self.video_controls)

        # ── Filmstrip ────────────────────────────────────────────────
        self.filmstrip_scroll = Gtk.ScrolledWindow()
        self.filmstrip_scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.filmstrip_scroll.set_halign(Gtk.Align.FILL)
        self.filmstrip_scroll.set_valign(Gtk.Align.END)
        self.filmstrip_scroll.set_margin_bottom(8)
        self.filmstrip_scroll.set_margin_start(60)
        self.filmstrip_scroll.set_margin_end(60)
        self.filmstrip_scroll.set_size_request(-1, FILM_THUMB + 12)
        film_css = Gtk.CssProvider()
        film_css.load_from_string("""
            scrolledwindow { border-radius: 12px; }
            scrollbar { opacity: 0; min-width: 0; min-height: 0; }
        """)
        self.filmstrip_scroll.get_style_context().add_provider(
            film_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.filmstrip_scroll.set_overflow(Gtk.Overflow.HIDDEN)

        self.filmstrip_area = Gtk.DrawingArea()
        self.filmstrip_area.set_draw_func(self._draw_filmstrip)
        self.filmstrip_area.set_size_request(FILM_THUMB + 4, FILM_THUMB + 8)

        film_click = Gtk.GestureClick()
        film_click.connect("pressed", self._on_filmstrip_click)
        self.filmstrip_area.add_controller(film_click)

        self.filmstrip_scroll.set_child(self.filmstrip_area)
        viewer_area.add_overlay(self.filmstrip_scroll)

        # ── Video loading spinner ─────────────────────────────────────
        self.video_spinner = Gtk.Spinner()
        self.video_spinner.set_size_request(64, 64)
        self.video_spinner.set_halign(Gtk.Align.CENTER)
        self.video_spinner.set_valign(Gtk.Align.CENTER)
        self.video_spinner.set_visible(False)
        viewer_area.add_overlay(self.video_spinner)

        return viewer_area

    # ── Onderste balk ─────────────────────────────────────────────────
    def build_bottombar(self):
        self.bottom_stack = Gtk.Stack()
        self.bottom_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.bottom_stack.set_transition_duration(150)

        normal_bar = Gtk.ActionBar()
        self.photo_count_label = Gtk.Label(label=_("0 foto's"))
        self.photo_count_label.add_css_class("dim-label")
        normal_bar.pack_start(self.photo_count_label)

        self.clear_filter_btn = Gtk.Button(label=_("✕ Toon alle foto's"))
        self.clear_filter_btn.add_css_class("flat")
        self.clear_filter_btn.set_visible(False)
        self.clear_filter_btn.connect("clicked", self.on_clear_cluster_filter)
        normal_bar.pack_end(self.clear_filter_btn)

        self.bottom_stack.add_named(normal_bar, "normal")

        select_bar = Gtk.ActionBar()
        self.select_count_label = Gtk.Label(label=_("0 geselecteerd"))
        self.select_count_label.add_css_class("dim-label")
        select_bar.pack_start(self.select_count_label)

        delete_selected_btn = Gtk.Button(label=_("Verwijderen"))
        delete_selected_btn.add_css_class("destructive-action")
        delete_selected_btn.add_css_class("pill")
        delete_selected_btn.connect("clicked", self.on_delete_selected)
        select_bar.pack_end(delete_selected_btn)

        self.bottom_stack.add_named(select_bar, "select")
        self.bottom_stack.set_visible_child_name("normal")

        return self.bottom_stack

    # ── Tijdlijn ──────────────────────────────────────────────────────

    def _on_timeline_click(self, scroll_px):
        """Scroll the grid to the requested pixel position."""
        adj     = self.scroll.get_vadjustment()
        max_val = adj.get_upper() - adj.get_page_size()
        adj.set_value(max(0.0, min(scroll_px, max(0.0, max_val))))

    def _on_scroll_changed(self, adj):
        pass  # tijdlijn tijdelijk uitgeschakeld voor beta

    def _update_timeline_from_positions(self):
        return False  # tijdlijn tijdelijk uitgeschakeld voor beta

    # ── Selectie modus ────────────────────────────────────────────────
    def toggle_select_mode(self, btn=None):
        self._select_mode = not self._select_mode
        log_info(f"Selectie-modus: {'aan' if self._select_mode else 'uit'}")
        self._selected.clear()
        if self._select_mode:
            self.select_btn.set_label(_("Annuleren"))
            self.select_btn.add_css_class("suggested-action")
            self.bottom_stack.set_visible_child_name("select")
            self.select_count_label.set_text("0 geselecteerd")
        else:
            self.select_btn.set_label(_("Selecteren"))
            self.select_btn.remove_css_class("suggested-action")
            self.bottom_stack.set_visible_child_name("normal")
            self._update_all_selection_visuals()

    def _update_all_selection_visuals(self):
        for index, widget in self.thumb_widgets.items():
            self._update_thumb_visual(index, widget)

    def _update_thumb_visual(self, index, widget):
        btn, check_box = widget
        selected = index in self._selected
        if check_box is None and selected:
            # Lazy: maak check_box pas aan wanneer nodig
            if not hasattr(self, '_thumb_css'):
                return
            tc = self._thumb_css
            overlay = btn.get_child()
            if not isinstance(overlay, Gtk.Overlay):
                # Wrap picture in overlay voor check_box
                picture = btn.get_child()
                btn.set_child(None)
                overlay = Gtk.Overlay()
                overlay.set_size_request(THUMB_SIZE, THUMB_SIZE)
                overlay.set_child(picture)
                btn.set_child(overlay)
            check_box = Gtk.Box()
            check_box.set_size_request(22, 22)
            check_box.set_halign(Gtk.Align.END)
            check_box.set_valign(Gtk.Align.END)
            check_box.set_margin_end(6)
            check_box.set_margin_bottom(6)
            check_box.get_style_context().add_provider(tc['check_box'], Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            check_icon = Gtk.Image.new_from_icon_name("object-select-symbolic")
            check_icon.set_pixel_size(14)
            check_icon.set_halign(Gtk.Align.CENTER)
            check_icon.set_valign(Gtk.Align.CENTER)
            check_icon.set_hexpand(True)
            check_icon.set_vexpand(True)
            check_icon.get_style_context().add_provider(tc['white_icon'], Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            check_box.append(check_icon)
            overlay.add_overlay(check_box)
            self.thumb_widgets[index] = (btn, check_box)
        if check_box is not None:
            check_box.set_visible(selected)

    # ── Foto's laden ──────────────────────────────────────────────────
    def load_photos(self):
        photo_path = self.settings.get("photo_path", "")
        log_info(_("load_photos: scanning in {p}").format(p=photo_path))
        if not photo_path or not os.path.exists(photo_path):
            log_warn(_("load_photos: photo_path leeg of bestaat niet — empty state"))
            self.show_empty_state()
            return False
        photos = []
        for root, dirs, files in os.walk(photo_path):
            for file in files:
                if os.path.splitext(file)[1].lower() in IMAGE_EXTENSIONS:
                    photos.append(os.path.join(root, file))
        log_info(_("load_photos: {n} files found").format(n=len(photos)))
        if self._favorites_only:
            photos = [p for p in photos if p in self._favorites]
        if not photos:
            if self._favorites_only:
                self._show_empty_favorites()
            else:
                self.show_empty_state()
            self.start_watcher(photo_path)
            return False
        self.photos = photos
        self.apply_sort()
        suffix = " (favorieten)" if self._favorites_only else ""
        self.photo_count_label.set_text(f"{len(self.photos)} foto's{suffix}")
        self.start_load()
        self.start_watcher(photo_path)
        return False

    def _show_empty_favorites(self):
        self.spinner.stop()
        self.content_stack.set_visible_child_name("empty")
        self.photo_count_label.set_text("0 favorieten")
        self._loading = False

    def start_load(self):
        self._load_id += 1
        load_id = self._load_id
        self._loading = True
        while True:
            child = self.grid_box.get_first_child()
            if child is None:
                break
            self.grid_box.remove(child)
        self.thumb_widgets = {}
        self.date_widgets  = {}
        self._hydrated_indices = set()
        self._selected.clear()
        self.content_stack.set_visible_child_name("loading")
        self.spinner.start()
        self.spinner_label.set_text(f"Foto's laden... 0 / {len(self.photos)}")
        thread = threading.Thread(
            target=self._load_thread,
            args=(load_id, list(self.photos)),
            daemon=True
        )
        thread.start()

    def _group_by_date(self, photos):
        groups = defaultdict(list)
        for i, path in enumerate(photos):
            try:
                mtime = os.path.getmtime(path)
                dt    = datetime.datetime.fromtimestamp(mtime).date()
            except Exception:
                dt = datetime.date(1970, 1, 1)
            groups[dt].append(i)
        sorted_dates = sorted(groups.keys(), reverse=True)
        return [(format_date_header(dt), dt, groups[dt]) for dt in sorted_dates]

    def _load_thread(self, load_id, photos):
        total  = len(photos)
        groups = self._group_by_date(photos)
        loaded = 0

        def fetch(idx):
            path = photos[idx]
            pb = load_thumbnail(path)
            if pb is not None:
                w, h = pb.get_width(), pb.get_height()
            else:
                w, h = THUMB_SIZE, THUMB_SIZE
            pb = None
            cache_path = get_cache_path(path, THUMB_SIZE)
            dur = get_video_duration(path) if is_video(path) else 0.0
            return (idx, path, cache_path, w, h, dur)

        with ThreadPoolExecutor(max_workers=THUMB_WORKERS) as pool:
            for date_str, date_obj, indices in groups:
                if load_id != self._load_id:
                    return
                GLib.idle_add(self._add_date_group, load_id, date_str)
                batch = []
                for result in pool.map(fetch, indices):
                    if load_id != self._load_id:
                        return
                    batch.append(result)
                    loaded += 1
                    if len(batch) >= BATCH_SIZE:
                        GLib.idle_add(self._apply_batch, load_id, list(batch), loaded, total)
                        batch = []
                if batch and load_id == self._load_id:
                    GLib.idle_add(self._apply_batch, load_id, list(batch), loaded, total)
        GLib.idle_add(self._load_done, load_id, total)

    def _add_date_group(self, load_id, date_str):
        if load_id != self._load_id:
            return False
        label = Gtk.Label(label=date_str)
        label.add_css_class("title-4")
        label.set_halign(Gtk.Align.START)
        label.set_margin_top(16)
        label.set_margin_bottom(8)
        label.set_margin_start(4)
        self.grid_box.append(label)
        self.date_widgets[date_str] = label
        rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        rows_box.set_halign(Gtk.Align.CENTER)
        rows_box.set_hexpand(True)
        self._current_flow = rows_box
        self._current_row_hbox = None
        self._current_row_width = 0
        self.grid_box.append(rows_box)
        if self.content_stack.get_visible_child_name() == "loading":
            self.spinner.stop()
            self.content_stack.set_visible_child_name("grid")
        return False

    def _available_grid_width(self):
        try:
            w = self.scroll.get_width()
        except Exception:
            w = 0
        if w <= 0:
            try:
                w = self.get_width()
            except Exception:
                w = 0
        if w <= 0:
            w = 1280
        # marge + scrollbar-reserve
        return max(200, w - 40)

    def _append_thumb_to_row(self, btn, thumb_width):
        spacing = 4
        avail = self._available_grid_width()
        needed = thumb_width + (spacing if self._current_row_hbox is not None else 0)
        if self._current_row_hbox is None or self._current_row_width + needed > avail:
            self._current_row_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)
            self._current_row_hbox.set_halign(Gtk.Align.CENTER)
            self._current_flow.append(self._current_row_hbox)
            self._current_row_width = 0
            needed = thumb_width
        self._current_row_hbox.append(btn)
        self._current_row_width += needed

    def _schedule_viewport_hydrate(self):
        if getattr(self, "_viewport_hydrate_pending", False):
            return
        self._viewport_hydrate_pending = True
        GLib.timeout_add(80, self._run_viewport_hydrate)

    def _run_viewport_hydrate(self):
        self._viewport_hydrate_pending = False
        try:
            self._hydrate_viewport()
        except Exception as e:
            log_error(f"viewport hydrate fout: {e}")
        return False

    def _hydrate_viewport(self):
        if not hasattr(self, "scroll") or self.scroll is None:
            return
        try:
            vadj = self.scroll.get_vadjustment()
            v_page = vadj.get_page_size()
        except Exception:
            return
        if v_page <= 0:
            v_page = self.scroll.get_height() or 600
        buffer_px = max(int(v_page), 400)
        if not hasattr(self, "_hydrated_indices"):
            self._hydrated_indices = set()
        want = set()
        for index, (btn, _check) in self.thumb_widgets.items():
            if not hasattr(btn, "_pixora_cache_path"):
                continue
            try:
                res = btn.translate_coordinates(self.scroll, 0, 0)
            except Exception:
                continue
            if not res:
                continue
            ok = res[0]
            if not ok:
                continue
            dst_y = res[2] if len(res) >= 3 else 0
            h = btn.get_height() or THUMB_SIZE
            btn_top = dst_y
            btn_bottom = dst_y + h
            if btn_bottom >= -buffer_px and btn_top <= v_page + buffer_px:
                want.add(index)
        to_hydrate = want - self._hydrated_indices
        to_drop = self._hydrated_indices - want
        for idx in to_hydrate:
            self._hydrate_thumb(idx)
        for idx in to_drop:
            self._dehydrate_thumb(idx)
        self._hydrated_indices = want

    def _hydrate_thumb(self, index):
        item = self.thumb_widgets.get(index)
        if not item:
            return
        btn, _check = item
        cache_path = getattr(btn, "_pixora_cache_path", None)
        picture = getattr(btn, "_pixora_picture", None)
        if not cache_path or picture is None:
            return
        if getattr(btn, "_pixora_has_pixbuf", False):
            return
        if getattr(btn, "_pixora_hydrating", False):
            return
        btn._pixora_hydrating = True
        load_id = self._load_id
        if not hasattr(self, "_hydrate_pool"):
            self._hydrate_pool = ThreadPoolExecutor(max_workers=2)

        def _load_in_thread():
            try:
                if cache_path and os.path.exists(cache_path):
                    pb = GdkPixbuf.Pixbuf.new_from_file(cache_path)
                else:
                    pb = None
            except Exception:
                pb = None
            GLib.idle_add(self._apply_hydrated_pixbuf, index, load_id, pb)

        self._hydrate_pool.submit(_load_in_thread)

    def _apply_hydrated_pixbuf(self, index, load_id, pb):
        if load_id != self._load_id:
            return False
        item = self.thumb_widgets.get(index)
        if not item:
            return False
        btn, _check = item
        btn._pixora_hydrating = False
        if pb is None:
            return False
        picture = getattr(btn, "_pixora_picture", None)
        if picture is None:
            return False
        try:
            picture.set_pixbuf(pb)
            btn._pixora_has_pixbuf = True
        except Exception:
            pass
        return False

    def _dehydrate_thumb(self, index):
        item = self.thumb_widgets.get(index)
        if not item:
            return
        btn, _check = item
        picture = getattr(btn, "_pixora_picture", None)
        if picture is None:
            return
        try:
            picture.set_paintable(None)
        except Exception:
            pass
        btn._pixora_has_pixbuf = False

    def _apply_batch(self, load_id, batch, loaded, total):
        if load_id != self._load_id:
            return False
        self.spinner_label.set_text(f"Foto's laden... {loaded} / {total}")
        # Shared CSS providers — created once, reused for every thumbnail
        if not hasattr(self, '_thumb_css'):
            tc = {}
            p = Gtk.CssProvider()
            p.load_from_string("box { background-color: rgba(0,0,0,0.5); border-radius: 50%; padding: 8px; }")
            tc['play_box'] = p
            p = Gtk.CssProvider()
            p.load_from_string("image { color: white; }")
            tc['white_icon'] = p
            p = Gtk.CssProvider()
            p.load_from_string("box { background-color: rgba(0,0,0,0.65); border-radius: 4px; padding: 2px 5px; }")
            tc['dur_box'] = p
            p = Gtk.CssProvider()
            p.load_from_string("label { color: white; font-size: 10px; font-weight: bold; }")
            tc['dur_label'] = p
            p = Gtk.CssProvider()
            p.load_from_string("box { background-color: #e95420; border-radius: 6px; min-width: 22px; min-height: 22px; }")
            tc['check_box'] = p
            p = Gtk.CssProvider()
            p.load_from_string(
                "button { border-radius: 8px; padding: 0; background: rgba(128,128,128,0.18); }"
                "button picture { border-radius: 8px; }"
                "button:hover { outline: 2px solid #e95420; outline-offset: -2px; border-radius: 8px; }"
            )
            tc['btn'] = p
            self._thumb_css = tc
        tc = self._thumb_css
        for index, path, cache_path, pb_w, pb_h, duration in batch:
            if pb_h > 0:
                width_at_thumb = max(1, int(pb_w * THUMB_SIZE / pb_h))
            else:
                width_at_thumb = THUMB_SIZE
            picture = Gtk.Picture()
            picture.set_size_request(width_at_thumb, THUMB_SIZE)
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            picture.set_hexpand(False)
            picture.set_vexpand(False)

            overlay = Gtk.Overlay()
            overlay.set_size_request(width_at_thumb, THUMB_SIZE)
            overlay.set_hexpand(False)
            overlay.set_vexpand(False)
            overlay.set_child(picture)

            if duration > 0:
                play_box = Gtk.Box()
                play_box.set_halign(Gtk.Align.CENTER)
                play_box.set_valign(Gtk.Align.CENTER)
                play_box.get_style_context().add_provider(tc['play_box'], Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                play_icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
                play_icon.set_pixel_size(24)
                play_icon.get_style_context().add_provider(tc['white_icon'], Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                play_box.append(play_icon)
                overlay.add_overlay(play_box)

                dur_box = Gtk.Box()
                dur_box.set_halign(Gtk.Align.END)
                dur_box.set_valign(Gtk.Align.END)
                dur_box.set_margin_end(6)
                dur_box.set_margin_bottom(6)
                dur_box.get_style_context().add_provider(tc['dur_box'], Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                dur_label = Gtk.Label(label=format_duration(duration))
                dur_label.get_style_context().add_provider(tc['dur_label'], Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                dur_box.append(dur_label)
                overlay.add_overlay(dur_box)

            if 'fav_box' not in tc:
                p = Gtk.CssProvider()
                p.load_from_string(
                    "box { background-color: rgba(0,0,0,0.55); border-radius: 50%; padding: 3px 6px; }"
                )
                tc['fav_box'] = p
            fav_badge = Gtk.Box()
            fav_badge.set_halign(Gtk.Align.START)
            fav_badge.set_valign(Gtk.Align.START)
            fav_badge.set_margin_start(6)
            fav_badge.set_margin_top(6)
            fav_badge.set_can_target(False)
            fav_badge.set_can_focus(False)
            fav_badge.get_style_context().add_provider(
                tc['fav_box'], Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            fav_label = Gtk.Label()
            fav_label.set_use_markup(True)
            fav_label.set_markup(
                '<span foreground="#ffb300" size="large" weight="bold">★</span>'
            )
            fav_badge.append(fav_label)
            fav_badge.set_visible(path in self._favorites)
            overlay.add_overlay(fav_badge)

            btn = Gtk.Button()
            btn.set_child(overlay)
            btn.set_overflow(Gtk.Overflow.HIDDEN)
            btn.set_size_request(width_at_thumb, THUMB_SIZE)
            btn.set_hexpand(False)
            btn.set_vexpand(False)
            btn.get_style_context().add_provider(tc['btn'], Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

            idx = index
            btn.connect("clicked", lambda b, i=idx: self.on_thumb_clicked(i))
            btn._pixora_cache_path = cache_path
            btn._pixora_picture = picture
            btn._pixora_index = index
            btn._fav_badge = fav_badge
            self._append_thumb_to_row(btn, width_at_thumb)
            # check_box wordt lazy aangemaakt bij selectie-modus
            self.thumb_widgets[index] = (btn, None)
        self._schedule_viewport_hydrate()
        return False

    def _load_done(self, load_id, total):
        if load_id != self._load_id:
            return False
        log_info(_("Thumbnail load done: {n} photos — UI ready").format(n=total))
        self.spinner.stop()
        self.content_stack.set_visible_child_name("grid")
        cluster_lbl = getattr(self, '_cluster_location_label', None)
        self.photo_count_label.set_text(cluster_lbl if cluster_lbl else f"{total} foto's")
        self._loading = False
        GLib.timeout_add(800, self._update_timeline_from_positions)
        GLib.timeout_add(120, self._run_viewport_hydrate)
        return False

    def show_empty_state(self):
        self.spinner.stop()
        self.content_stack.set_visible_child_name("empty")
        self.photo_count_label.set_text("0 foto's")
        self._loading = False

    # ── Thumbnail klik ────────────────────────────────────────────────
    def on_thumb_clicked(self, index):
        path = self.photos[index] if 0 <= index < len(self.photos) else "?"
        if self._select_mode:
            action = "deselecteer" if index in self._selected else "selecteer"
            log_info(f"Thumbnail {action}: idx={index} path={path}")
            if index in self._selected:
                self._selected.discard(index)
            else:
                self._selected.add(index)
            self._update_thumb_visual(index, self.thumb_widgets[index])
            self.select_count_label.set_text(f"{len(self._selected)} geselecteerd")
        else:
            log_info(f"Thumbnail geklikt → open foto: idx={index} path={path}")
            self.open_photo(index)

    # ── Sorteren ──────────────────────────────────────────────────────
    def apply_sort(self):
        index = self.sort_combo.get_selected()
        if index in (0, 1):
            # Pre-compute alle datums eenmaal zodat sort() niet O(n log n)
            # EXIF-reads triggert maar O(n) + O(n log n) compares.
            date_map = {p: get_photo_date(p) for p in self.photos}
            self.photos.sort(key=date_map.get, reverse=(index == 0))
        elif index == 2:
            self.photos.sort(key=lambda p: os.path.basename(p).lower())
        elif index == 3:
            self.photos.sort(key=lambda p: os.path.basename(p).lower(), reverse=True)

    def on_sort_changed(self, combo, _):
        if not self.photos:
            return
        options = ["Datum nieuwste", "Datum oudste", "Naam A-Z", "Naam Z-A"]
        idx = combo.get_selected()
        log_info(f"Sortering gewijzigd: {options[idx] if idx < len(options) else idx}")
        if self._sort_timer:
            GLib.source_remove(self._sort_timer)
        self._sort_timer = GLib.timeout_add(400, self._do_sort)

    def _do_sort(self):
        self._sort_timer = None
        self.content_stack.set_visible_child_name("loading")
        self.spinner.start()
        self.spinner_label.set_text(_("Sorteren..."))
        sort_index = self.sort_combo.get_selected()
        threading.Thread(target=self._do_sort_bg, args=(sort_index,), daemon=True).start()
        return False

    def _do_sort_bg(self, sort_index):
        photos = list(self.photos)
        if sort_index == 0:
            photos.sort(key=get_photo_date, reverse=True)
        elif sort_index == 1:
            photos.sort(key=get_photo_date)
        elif sort_index == 2:
            photos.sort(key=lambda p: os.path.basename(p).lower())
        elif sort_index == 3:
            photos.sort(key=lambda p: os.path.basename(p).lower(), reverse=True)
        self.photos = photos
        GLib.idle_add(self.start_load)

    # ── Foto viewer ───────────────────────────────────────────────────
    def open_photo(self, index):
        path = self.photos[index] if 0 <= index < len(self.photos) else "?"
        kind = "video" if is_video(path) else "foto"
        log_info(f"open_photo: {kind} idx={index} path={path}")
        self.current_index = index
        self.header.set_visible(False)
        self.bottom_stack.set_visible(False)
        try:
            self.toolbar_view.set_reveal_top_bars(False)
            self.toolbar_view.set_reveal_bottom_bars(False)
        except Exception:
            pass
        self._stop_video()
        self.photo_picture.set_pixbuf(None)
        self.viewer_location.set_text("")
        self.main_stack.set_visible_child_name("viewer")
        self._filmstrip_thumbs = {}
        GLib.idle_add(self._update_filmstrip)
        GLib.timeout_add(80, self._scroll_filmstrip_to_current)
        self._viewer_load_id += 1
        load_id = self._viewer_load_id
        threading.Thread(
            target=self._load_full_photo,
            args=(self.photos[index], load_id),
            daemon=True
        ).start()

    def _load_full_photo(self, path, load_id):
        if is_video(path):
            # Toon video direct, geocode daarna
            if load_id == self._viewer_load_id:
                GLib.idle_add(self._show_video, path, self._photo_location.get(path) or "")
            location = self._photo_location.get(path)
            if location is None:
                coords = get_video_gps_coords(path)
                if coords:
                    location = reverse_geocode(coords[0], coords[1])
                    if location:
                        self._photo_location[path] = location
                else:
                    location = ""
                    try:
                        if time.time() - os.path.getmtime(path) > 30:
                            self._photo_location[path] = location
                    except Exception:
                        pass
            if location and load_id == self._viewer_load_id:
                GLib.idle_add(self.viewer_location.set_text, f"📍 {location}")
            return
        pixbuf = self._viewer_pixbuf_cache.get(path)
        if pixbuf is not None:
            self._viewer_pixbuf_cache.move_to_end(path)
        else:
            try:
                max_dim = 2560
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, max_dim, max_dim, True)
            except Exception:
                pixbuf = None
            if pixbuf is not None:
                self._viewer_pixbuf_cache[path] = pixbuf
                while len(self._viewer_pixbuf_cache) > 3:
                    self._viewer_pixbuf_cache.popitem(last=False)
        if load_id == self._viewer_load_id:
            GLib.idle_add(self._show_full_photo, pixbuf, path, self._photo_location.get(path) or "")
            GLib.idle_add(self._preload_adjacent_photos)
        # Geocode daarna, update label als de gebruiker nog steeds deze foto bekijkt
        location = self._photo_location.get(path)
        if location is None:
            coords = get_gps_coords(path)
            if coords:
                location = reverse_geocode(coords[0], coords[1])
                if location:
                    self._photo_location[path] = location
            else:
                location = ""
                try:
                    if time.time() - os.path.getmtime(path) > 30:
                        self._photo_location[path] = location
                except Exception:
                    pass
        if location and load_id == self._viewer_load_id:
            GLib.idle_add(self.viewer_location.set_text, f"📍 {location}")

    def _show_full_photo(self, pixbuf, path, location=""):
        self._stop_video()
        self._show_viewer_ui()   # reset opacity/visibility from any previous fade
        self.video_display.set_visible(False)
        self.video_controls.set_visible(False)
        self.photo_picture.set_visible(True)
        self.edit_btn.set_visible(True)
        self._update_favorite_btn()
        self.viewer_counter.set_margin_bottom(FILM_THUMB + 12 + 16)
        self._viewer_zoom   = 1.0
        self._viewer_offset = [0.0, 0.0]
        self._viewer_pixbuf = pixbuf
        if pixbuf:
            self.photo_picture.set_pixbuf(pixbuf)
            self._apply_viewer_transform()
        mtime = os.path.getmtime(path)
        datum = datetime.datetime.fromtimestamp(mtime).strftime("%-d %B %Y  %H:%M")
        self.viewer_title.set_text(f"{os.path.basename(path)}  —  {datum}")
        self.viewer_location.set_text(f"📍 {location}" if location else "")
        self.viewer_counter.set_text(f"{self.current_index + 1} / {len(self.photos)}")
        self.prev_btn.set_sensitive(self.current_index > 0)
        self.next_btn.set_sensitive(self.current_index < len(self.photos) - 1)
        self.filmstrip_area.queue_draw()
        GLib.idle_add(self._scroll_filmstrip_to_current)
        return False

    def prev_photo(self, btn=None):
        if self.current_index > 0:
            self._stop_video()
            self.current_index -= 1
            new_path = self.photos[self.current_index] if self.photos else "?"
            log_info(f"Vorige foto: idx={self.current_index} → {os.path.basename(new_path)}")
            self._schedule_photo_load()

    def next_photo(self, btn=None):
        if self.current_index < len(self.photos) - 1:
            self._stop_video()
            self.current_index += 1
            new_path = self.photos[self.current_index] if self.photos else "?"
            log_info(f"Volgende foto: idx={self.current_index} → {os.path.basename(new_path)}")
            self._schedule_photo_load()

    def _schedule_photo_load(self):
        """Direct tonen bij cache-hit, anders thumbnail-placeholder + async load."""
        self._viewer_load_id += 1
        load_id = self._viewer_load_id
        self.viewer_location.set_text("")
        self.filmstrip_area.queue_draw()
        self._scroll_filmstrip_to_current()

        if hasattr(self, '_nav_debounce_id') and self._nav_debounce_id:
            GLib.source_remove(self._nav_debounce_id)
            self._nav_debounce_id = None

        if not self.photos:
            return
        path = self.photos[self.current_index]

        # 1. Cache-hit (foto al gepreloaded) → direct tonen, geen debounce
        if not is_video(path):
            cached = self._viewer_pixbuf_cache.get(path)
            if cached is not None:
                self._viewer_pixbuf_cache.move_to_end(path)
                self._show_full_photo(cached, path,
                                      self._photo_location.get(path) or "")
                GLib.idle_add(self._preload_adjacent_photos)
                return

        # 2. Cache-miss: toon thumbnail als placeholder (instant feedback)
        try:
            thumb_path = get_cache_path(path, THUMB_SIZE)
            if os.path.exists(thumb_path):
                thumb_pb = GdkPixbuf.Pixbuf.new_from_file(thumb_path)
                if thumb_pb:
                    self.photo_picture.set_pixbuf(thumb_pb)
        except Exception:
            pass

        # 3. Full-res async; debounce 0ms (geen extra wachttijd)
        self._nav_debounce_id = GLib.timeout_add(0, self._do_scheduled_load)

    def _preload_adjacent_photos(self):
        if not hasattr(self, "current_index") or not self.photos:
            return False
        for delta in (1, -1):
            idx = self.current_index + delta
            if idx < 0 or idx >= len(self.photos):
                continue
            p = self.photos[idx]
            if is_video(p) or p in self._viewer_pixbuf_cache:
                continue
            def _bg_load(path=p):
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 2560, 2560, True)
                except Exception:
                    return
                if pb is None:
                    return
                self._viewer_pixbuf_cache[path] = pb
                while len(self._viewer_pixbuf_cache) > 3:
                    self._viewer_pixbuf_cache.popitem(last=False)
            threading.Thread(target=_bg_load, daemon=True).start()
        return False

    def _do_scheduled_load(self):
        self._nav_debounce_id = None
        load_id = self._viewer_load_id
        threading.Thread(
            target=self._load_full_photo,
            args=(self.photos[self.current_index], load_id),
            daemon=True
        ).start()
        return False

    def close_viewer(self, btn=None):
        log_info(_("Viewer gesloten → terug naar grid"))
        self._stop_video()
        self._viewer_load_id += 1
        self.header.set_visible(True)
        self.bottom_stack.set_visible(True)
        try:
            self.toolbar_view.set_reveal_top_bars(True)
            self.toolbar_view.set_reveal_bottom_bars(True)
        except Exception:
            pass
        if hasattr(self, '_photos_before_cluster') and self._photos_before_cluster is not None:
            self.photos = self._photos_before_cluster
            self._photos_before_cluster = None
            self._cluster_location_label = None
            self.photo_count_label.set_text(f"{len(self.photos)} foto's")
            try:
                self.clear_filter_btn.set_visible(False)
            except Exception:
                pass
        self.main_stack.set_visible_child_name("grid")
        GLib.idle_add(self._close_viewer_cleanup)

    def _close_viewer_cleanup(self):
        self.photo_picture.set_pixbuf(None)
        self.video_display.set_visible(False)
        self.video_controls.set_visible(False)
        self.photo_picture.set_visible(True)
        self.edit_btn.set_visible(True)
        self._show_viewer_ui()
        return False

    # ── Filmstrip ─────────────────────────────────────────────────────
    def _update_filmstrip(self):
        """Resize the DrawingArea and start loading thumbnails for all photos."""
        n = len(self.photos)
        w = n * (FILM_THUMB + 4)
        self.filmstrip_area.set_size_request(max(w, FILM_THUMB + 4), FILM_THUMB + 8)
        self._filmstrip_load_id += 1
        load_id = self._filmstrip_load_id
        threading.Thread(
            target=self._load_filmstrip_bg,
            args=(list(self.photos), load_id),
            daemon=True
        ).start()

    def _load_filmstrip_bg(self, photos, load_id):
        n = len(photos)
        if n == 0:
            return
        # Laad eerst rond de huidige foto, dan naar buiten toe
        center = min(self.current_index, n - 1)
        order = [center]
        for dist in range(1, n):
            if load_id != self._filmstrip_load_id:
                return
            if center - dist >= 0:
                order.append(center - dist)
            if center + dist < n:
                order.append(center + dist)
        order = [i for i in order if i not in self._filmstrip_thumbs]

        def load_one(i):
            path = photos[i]
            try:
                if is_video(path):
                    pb = load_thumbnail(path)
                    if pb:
                        pb = pb.scale_simple(
                            min(FILM_THUMB, pb.get_width()),
                            min(FILM_THUMB, pb.get_height()),
                            GdkPixbuf.InterpType.BILINEAR)
                else:
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        path, FILM_THUMB, FILM_THUMB, True)
            except Exception:
                pb = None
            return i, pb

        with ThreadPoolExecutor(max_workers=3) as pool:
            for count, (i, pb) in enumerate(pool.map(load_one, order)):
                if load_id != self._filmstrip_load_id:
                    return
                self._filmstrip_thumbs[i] = pb
                if count % 5 == 0 and self.filmstrip_scroll.get_visible():
                    GLib.idle_add(self.filmstrip_area.queue_draw)
        if self.filmstrip_scroll.get_visible():
            GLib.idle_add(self.filmstrip_area.queue_draw)

    @staticmethod
    def _film_rounded_rect(cr, x, y, w, h, r=6):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r,     r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r,  0,            math.pi / 2)
        cr.arc(x + r,     y + h - r, r,  math.pi / 2,  math.pi)
        cr.arc(x + r,     y + r,     r,  math.pi,      3 * math.pi / 2)
        cr.close_path()

    def _draw_filmstrip(self, area, cr, width, height):
        n = len(self.photos)
        if n == 0:
            return
        cell = FILM_THUMB + 4
        # dark background
        cr.set_source_rgba(0, 0, 0, 0.65)
        cr.rectangle(0, 0, width, height)
        cr.fill()
        # Alleen zichtbare items tekenen
        adj = self.filmstrip_scroll.get_hadjustment()
        scroll_x = adj.get_value() if adj else 0
        visible_w = adj.get_page_size() if adj else width
        first_visible = max(0, int(scroll_x / cell) - 1)
        last_visible = min(n, int((scroll_x + visible_w) / cell) + 2)
        for i in range(first_visible, last_visible):
            x = i * cell + 2
            y = (height - FILM_THUMB) // 2
            pb = self._filmstrip_thumbs.get(i)
            if pb:
                pw = pb.get_width()
                ph = pb.get_height()
                dx = x + (FILM_THUMB - pw) // 2
                dy = y + (FILM_THUMB - ph) // 2
                cr.save()
                self._film_rounded_rect(cr, dx, dy, pw, ph, 6)
                cr.clip()
                Gdk.cairo_set_source_pixbuf(cr, pb, dx, dy)
                cr.paint()
                cr.restore()
            else:
                cr.set_source_rgba(0.3, 0.3, 0.3, 1.0)
                self._film_rounded_rect(cr, x, y, FILM_THUMB, FILM_THUMB, 6)
                cr.fill()
            # video: draw play triangle
            if i < len(self.photos) and is_video(self.photos[i]):
                cx = x + FILM_THUMB // 2
                cy = y + FILM_THUMB // 2
                cr.set_source_rgba(0, 0, 0, 0.45)
                cr.arc(cx, cy, 13, 0, 2 * math.pi)
                cr.fill()
                cr.set_source_rgba(1, 1, 1, 0.95)
                cr.move_to(cx - 4, cy - 7)
                cr.line_to(cx - 4, cy + 7)
                cr.line_to(cx + 9, cy)
                cr.close_path()
                cr.fill()
            # highlight current with orange rounded border
            if i == self.current_index:
                cr.set_source_rgba(0.914, 0.329, 0.125, 1.0)
                cr.set_line_width(3)
                self._film_rounded_rect(cr, x + 1.5, y + 1.5, FILM_THUMB - 3, FILM_THUMB - 3, 5)
                cr.stroke()

    def _on_filmstrip_click(self, gesture, n_press, x, y):
        cell = FILM_THUMB + 4
        idx = int(x // cell)
        if 0 <= idx < len(self.photos) and idx != self.current_index:
            self.current_index = idx
            self._viewer_load_id += 1
            load_id = self._viewer_load_id
            self.viewer_location.set_text("")
            self.filmstrip_area.queue_draw()
            threading.Thread(
                target=self._load_full_photo,
                args=(self.photos[idx], load_id),
                daemon=True
            ).start()

    def _scroll_filmstrip_to_current(self):
        """Scroll the filmstrip so the current photo is always centered."""
        cell = FILM_THUMB + 4
        adj  = self.filmstrip_scroll.get_hadjustment()
        page = adj.get_page_size()
        if page <= 0:
            GLib.timeout_add(50, self._scroll_filmstrip_to_current)
            return False
        center_of_current = self.current_index * cell + cell / 2
        target = center_of_current - page / 2
        adj.set_value(max(0, min(target, adj.get_upper() - page)))
        return False

    # ── Video speler ──────────────────────────────────────────────────

    def _show_video(self, path, location=""):
        self._stop_video()
        self._preview_cache = OrderedDict()
        self._viewer_zoom   = 1.0
        self._viewer_offset = [0.0, 0.0]
        self._show_viewer_ui()   # reset opacity/visibility from any previous fade
        self.photo_picture.set_visible(False)
        self.edit_btn.set_visible(False)
        self._update_favorite_btn()
        self.video_display.set_visible(True)
        self.video_controls.set_visible(True)
        self.viewer_counter.set_margin_bottom(FILM_THUMB + 12 + 8 + 56)
        self.viewer_counter.set_visible(True)
        self.filmstrip_scroll.set_visible(True)
        self.video_spinner.set_visible(True)
        self.video_spinner.start()
        self._video_media = Gtk.MediaFile.new_for_filename(path)
        self._video_media.set_loop(False)
        self.video_display.set_paintable(self._video_media)
        self._video_media.play()
        self.video_play_btn.set_icon_name("media-playback-pause-symbolic")
        self.video_mute_btn.set_icon_name("audio-volume-high-symbolic")
        self.video_vol_scale.set_value(1.0)
        self.video_scrubber.set_value(0.0)
        self.video_time_label.set_text("0:00 / 0:00")
        mtime = os.path.getmtime(path)
        datum = datetime.datetime.fromtimestamp(mtime).strftime("%-d %B %Y  %H:%M")
        self.viewer_title.set_text(f"{os.path.basename(path)}  —  {datum}")
        self.viewer_location.set_text(f"📍 {location}" if location else "")
        self.viewer_counter.set_text(f"{self.current_index + 1} / {len(self.photos)}")
        self.prev_btn.set_sensitive(self.current_index > 0)
        self.next_btn.set_sensitive(self.current_index < len(self.photos) - 1)
        self.filmstrip_area.queue_draw()
        GLib.idle_add(self._scroll_filmstrip_to_current)
        self._start_video_poll()
        self._reset_fade_timer()
        return False

    def _stop_video(self):
        self._stop_video_poll()
        self._cancel_fade()
        if self._video_seek_pending_id:
            try:
                GLib.source_remove(self._video_seek_pending_id)
            except Exception:
                pass
            self._video_seek_pending_id = None
        self.video_spinner.set_visible(False)
        self.video_spinner.stop()
        if self._video_media:
            self._video_media.pause()
            self._video_media = None

    def _start_video_poll(self):
        self._stop_video_poll()
        self._video_poll_id = GLib.timeout_add(500, self._update_video_position)

    def _stop_video_poll(self):
        if self._video_poll_id is not None:
            try:
                GLib.source_remove(self._video_poll_id)
            except Exception:
                pass
            self._video_poll_id = None

    def _update_video_position(self):
        if not self._video_media:
            self._video_poll_id = None
            return False
        dur = self._video_media.get_duration()
        pos = self._video_media.get_timestamp()
        if dur > 0:
            if self.video_spinner.get_visible():
                self.video_spinner.set_visible(False)
                self.video_spinner.stop()
            self._video_scrubbing_lock = True
            self.video_scrubber.set_value(pos / dur)
            self._video_scrubbing_lock = False
        pos_s = pos // 1_000_000
        dur_s = dur // 1_000_000
        new_text = f"{format_duration(pos_s)} / {format_duration(dur_s)}"
        if getattr(self, '_last_video_time_text', None) != new_text:
            self.video_time_label.set_text(new_text)
            self._last_video_time_text = new_text
        if self._video_media.get_ended():
            self.video_play_btn.set_icon_name("media-playback-start-symbolic")
            self._video_poll_id = None
            return False
        return True

    def _on_video_play_pause(self, btn):
        if not self._video_media:
            return
        if self._video_media.get_playing():
            self._video_media.pause()
            if btn:
                btn.set_icon_name("media-playback-start-symbolic")
            else:
                self.video_play_btn.set_icon_name("media-playback-start-symbolic")
            self._stop_video_poll()
            self._cancel_fade()
            self._show_viewer_ui()
        else:
            self._video_media.play()
            if btn:
                btn.set_icon_name("media-playback-pause-symbolic")
            else:
                self.video_play_btn.set_icon_name("media-playback-pause-symbolic")
            self._start_video_poll()
            self._reset_fade_timer()

    def _on_video_scrub(self, scale):
        if self._video_scrubbing_lock or not self._video_media:
            return
        self._trigger_scrub_preview(scale.get_value())
        if self._video_seek_pending_id:
            try:
                GLib.source_remove(self._video_seek_pending_id)
            except Exception:
                pass
        self._video_seek_pending_id = GLib.timeout_add(80, self._do_video_seek)

    def _do_video_seek(self):
        self._video_seek_pending_id = None
        if not self._video_media:
            return False
        dur = self._video_media.get_duration()
        if dur > 0:
            self._video_media.seek(int(self.video_scrubber.get_value() * dur))
        return False

    def _on_video_mute(self, btn):
        if not self._video_media:
            return
        muted = not self._video_media.get_muted()
        self._video_media.set_muted(muted)
        btn.set_icon_name(
            "audio-volume-muted-symbolic" if muted else "audio-volume-high-symbolic"
        )

    def _on_video_volume(self, scale):
        if not self._video_media:
            return
        self._video_media.set_volume(scale.get_value())

    # ── Auto-fade viewer UI ───────────────────────────────────────────

    def _on_viewer_motion(self, ctrl, x, y):
        if self.main_stack.get_visible_child_name() != "viewer":
            return
        new_pos = (round(x), round(y))
        if getattr(self, '_last_viewer_mouse', None) == new_pos:
            return
        self._last_viewer_mouse = new_pos
        self._show_viewer_ui()
        self._reset_fade_timer()

    def _show_viewer_ui(self):
        self._cancel_fade()
        for w in self._video_fade_widgets():
            w.set_visible(True)
            w.set_opacity(1.0)
            w.set_can_target(True)
        # video_controls only shown in video mode
        if self._video_media is None:
            self.video_controls.set_visible(False)
        # restore zoom-driven visibility
        zoomed = getattr(self, '_viewer_zoom', 1.0) > 1.0
        if zoomed:
            self.prev_btn.set_visible(False)
            self.next_btn.set_visible(False)
            self.viewer_counter.set_visible(False)
            self.filmstrip_scroll.set_visible(False)

    def _video_fade_widgets(self):
        return [
            self.video_controls,
            self.filmstrip_scroll,
            self.viewer_counter,
            self.viewer_title_box,
            self.viewer_close_btn,
            self.viewer_delete_btn,
            self.edit_btn,
            self.favorite_btn,
            self.prev_btn,
            self.next_btn,
        ]

    def _reset_fade_timer(self):
        if self._fade_timer_id:
            try:
                GLib.source_remove(self._fade_timer_id)
            except Exception:
                pass
        delay = 800 if self._video_media else 10_000
        self._fade_timer_id = GLib.timeout_add(delay, self._start_fade)

    def _cancel_fade(self):
        if self._fade_timer_id:
            try:
                GLib.source_remove(self._fade_timer_id)
            except Exception:
                pass
            self._fade_timer_id = None
        if self._fade_anim_id:
            try:
                GLib.source_remove(self._fade_anim_id)
            except Exception:
                pass
            self._fade_anim_id = None

    def _start_fade(self):
        self._fade_timer_id = None
        self._fade_step = 0
        self._fade_anim_id = GLib.timeout_add(50, self._fade_tick)
        return False

    def _fade_tick(self):
        self._fade_step += 1
        opacity = max(0.0, 1.0 - self._fade_step / 8)  # ~400ms, minder stappen
        widgets = self._video_fade_widgets()
        for w in widgets:
            w.set_opacity(opacity)
        if opacity <= 0.0:
            for w in widgets:
                w.set_visible(False)
                w.set_can_target(False)
            self._fade_anim_id = None
            return False
        return True

    # ── Scrubber preview ──────────────────────────────────────────────

    def _trigger_scrub_preview(self, fraction):
        if not self._video_media:
            return
        dur = self._video_media.get_duration()
        if dur <= 0:
            return
        ts_s = (int(fraction * dur / 1_000_000) // 2) * 2  # round to 2s
        self._preview_time_lbl.set_text(format_duration(ts_s))
        w = self.video_scrubber.get_width()
        if w > 0:
            rect = Gdk.Rectangle()
            rect.x = int(fraction * w); rect.y = 0
            rect.width = 1; rect.height = self.video_scrubber.get_height()
            self._preview_popover.set_pointing_to(rect)
            if not self._preview_popover.get_visible():
                self._preview_popover.popup()
        pb = self._preview_cache.get(ts_s, "missing")
        if pb is not None and pb != "missing":
            self._preview_picture.set_pixbuf(pb)
            return
        self._preview_pending_ts = ts_s
        if self._preview_debounce_id:
            GLib.source_remove(self._preview_debounce_id)
        self._preview_debounce_id = GLib.timeout_add(120, self._do_debounced_preview)

    def _on_scrubber_hover(self, ctrl, x, y):
        if not self._video_media:
            return
        w = self.video_scrubber.get_width()
        if w <= 0:
            return
        self._trigger_scrub_preview(max(0.0, min(1.0, x / w)))

    def _on_scrubber_leave(self, ctrl):
        if self._preview_debounce_id:
            GLib.source_remove(self._preview_debounce_id)
            self._preview_debounce_id = None
        self._preview_popover.popdown()

    def _do_debounced_preview(self):
        self._preview_debounce_id = None
        ts_s = self._preview_pending_ts
        if ts_s is None or self._preview_extracting:
            return False
        if ts_s in self._preview_cache:
            return False
        self._preview_cache[ts_s] = None  # mark loading
        self._preview_extracting = True
        path = self.photos[self.current_index]
        threading.Thread(
            target=self._extract_preview_frame,
            args=(path, ts_s),
            daemon=True
        ).start()
        return False

    def _extract_preview_frame(self, path, ts_s):
        import tempfile
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                tmp = f.name
            subprocess.run(
                ["ffmpeg", "-ss", str(ts_s), "-i", path,
                 "-vframes", "1", "-vf", "scale=160:-1", tmp, "-y"],
                capture_output=True, timeout=8
            )
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(tmp, 160, 90, True)
            os.unlink(tmp)
            self._preview_cache[ts_s] = pb
            self._preview_cache.move_to_end(ts_s)
            while len(self._preview_cache) > 25:
                self._preview_cache.popitem(last=False)
            GLib.idle_add(self._apply_preview_frame, ts_s)
        except Exception:
            pass
        finally:
            self._preview_extracting = False

    def _apply_preview_frame(self, ts_s):
        pb = self._preview_cache.get(ts_s)
        if pb:
            self._preview_cache.move_to_end(ts_s)
            if self._preview_popover.get_visible():
                self._preview_picture.set_pixbuf(pb)
        return False

    def _apply_viewer_transform(self):
        if getattr(self, '_transform_pending', False):
            return
        self._transform_pending = True
        GLib.idle_add(self._do_apply_viewer_transform)

    def _do_apply_viewer_transform(self):
        self._transform_pending = False
        z  = self._viewer_zoom
        ox = self._viewer_offset[0]
        oy = self._viewer_offset[1]
        if not hasattr(self, '_viewer_css_provider'):
            self._viewer_css_provider = Gtk.CssProvider()
            self.photo_picture.get_style_context().add_provider(
                self._viewer_css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
            )
        self._viewer_css_provider.load_from_string(f"""
            picture {{
                transform: scale({z}) translate({ox}px, {oy}px);
                transform-origin: center center;
            }}
        """)
        zoomed = z > 1.0
        self.prev_btn.set_visible(not zoomed)
        self.next_btn.set_visible(not zoomed)
        self.viewer_counter.set_visible(not zoomed)
        self.filmstrip_scroll.set_visible(not zoomed)
        return False

    def on_viewer_scroll(self, ctrl, dx, dy):
        if self.main_stack.get_visible_child_name() != "viewer":
            return False
        factor = 0.9 if dy > 0 else 1.1
        self._viewer_zoom = max(1.0, min(8.0, self._viewer_zoom * factor))
        if self._viewer_zoom == 1.0:
            self._viewer_offset = [0.0, 0.0]
        self._apply_viewer_transform()
        return True

    def on_viewer_drag_begin(self, gesture, x, y):
        if self._viewer_zoom > 1.0:
            self._viewer_drag = (x, y, self._viewer_offset[0], self._viewer_offset[1])

    def on_viewer_drag_update(self, gesture, dx, dy):
        if self._viewer_drag and self._viewer_zoom > 1.0:
            _, _, ox, oy = self._viewer_drag
            self._viewer_offset[0] = ox + dx / self._viewer_zoom
            self._viewer_offset[1] = oy + dy / self._viewer_zoom
            self._apply_viewer_transform()

    def on_viewer_drag_end(self, gesture, dx, dy):
        self._viewer_drag = None

    def on_viewer_key(self, controller, keyval, keycode, state):
        if self.main_stack.get_visible_child_name() != "viewer":
            return False
        if keyval == 65307:  # Escape
            if self._editor_active:
                self.on_editor_cancel()
            else:
                self.close_viewer()
            return True
        if self._editor_active:
            return False
        if keyval == Gdk.KEY_space:
            if self._video_media:
                self._on_video_play_pause(None)
            return True
        if keyval == 65361:
            self.prev_photo()
            return True
        elif keyval == 65363:
            self.next_photo()
            return True
        if keyval in (Gdk.KEY_f, Gdk.KEY_F):
            self.on_toggle_favorite(None)
            return True
        return False

    # ── Favorieten ────────────────────────────────────────────────────
    def _current_photo_path(self):
        if 0 <= self.current_index < len(self.photos):
            return self.photos[self.current_index]
        return None

    def _update_favorite_btn(self):
        path = self._current_photo_path()
        if not path:
            return
        is_fav = path in self._favorites
        self.favorite_btn.set_icon_name(
            "starred-symbolic" if is_fav else "non-starred-symbolic"
        )
        ctx = self.favorite_btn.get_style_context()
        if is_fav:
            ctx.add_class("pixora-fav")
        else:
            ctx.remove_class("pixora-fav")
        self.favorite_btn.set_tooltip_text(
            "Verwijder uit favorieten (F)" if is_fav else "Markeer als favoriet (F)"
        )

    def on_toggle_favorite(self, btn):
        path = self._current_photo_path()
        if not path:
            return
        if path in self._favorites:
            self._favorites.discard(path)
            log_info(f"Favoriet verwijderd: {path}")
        else:
            self._favorites.add(path)
            log_info(f"Favoriet toegevoegd: {path}")
        self._schedule_save_favorites()
        self._update_favorite_btn()
        # refresh thumbnail badge if visible
        widget = self.thumb_widgets.get(self.current_index)
        if widget:
            self._refresh_thumb_favorite(self.current_index)

    def _refresh_thumb_favorite(self, index):
        entry = self.thumb_widgets.get(index)
        if not entry:
            return
        btn, _cb = entry
        path = self.photos[index] if index < len(self.photos) else None
        if not path:
            return
        badge = getattr(btn, "_fav_badge", None)
        if badge is None:
            return
        badge.set_visible(path in self._favorites)

    def toggle_favorites_filter(self, btn):
        self._favorites_only = btn.get_active()
        log_info(f"Favorieten-filter: {'aan' if self._favorites_only else 'uit'}")
        self.load_photos()


    # ── Foto editor ───────────────────────────────────────────────────
    def on_edit_current(self, btn):
        path = self._current_photo_path() or "?"
        log_info(f"Editor geopend voor: {os.path.basename(path)}")
        self._editor_active         = True
        self._editor_rotation       = 0
        self._editor_crop_mode      = False
        self._editor_display_pixbuf = self._viewer_pixbuf
        self._crop_rect             = None
        self._crop_handle           = None
        self._crop_rect_origin      = None
        self.crop_toggle_btn.set_active(False)
        self.crop_overlay_area.set_visible(False)
        self.editor_bar.set_visible(True)
        self.prev_btn.set_sensitive(False)
        self.next_btn.set_sensitive(False)

    def on_editor_cancel(self, btn=None):
        log_info(_("Editor geannuleerd"))
        self._editor_active         = False
        self._editor_rotation       = 0
        self._editor_crop_mode      = False
        self._editor_display_pixbuf = None
        self._crop_rect             = None
        self._crop_handle           = None
        self._crop_rect_origin      = None
        self.crop_toggle_btn.set_active(False)
        self.crop_overlay_area.set_visible(False)
        self.editor_bar.set_visible(False)
        if self._viewer_pixbuf:
            self.photo_picture.set_pixbuf(self._viewer_pixbuf)
            self._apply_viewer_transform()
        self.prev_btn.set_sensitive(self.current_index > 0)
        self.next_btn.set_sensitive(self.current_index < len(self.photos) - 1)

    def _reset_crop(self):
        self._editor_crop_mode = False
        self.crop_toggle_btn.set_active(False)
        self.crop_overlay_area.set_visible(False)
        self._crop_rect        = None
        self._crop_handle      = None
        self._crop_rect_origin = None

    def on_editor_rotate_left(self, btn):
        log_info(_("Editor: draaien links (-90°)"))
        self._editor_rotation = (self._editor_rotation + 90) % 360
        self._reset_crop()
        self._editor_apply_preview()

    def on_editor_rotate_right(self, btn):
        log_info(_("Editor: draaien rechts (+90°)"))
        self._editor_rotation = (self._editor_rotation - 90) % 360
        self._reset_crop()
        self._editor_apply_preview()

    def _editor_apply_preview(self):
        if not self._viewer_pixbuf:
            return
        rotation = self._editor_rotation % 360
        gdk_rot_map = {
            0:   GdkPixbuf.PixbufRotation.NONE,
            90:  GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE,
            180: GdkPixbuf.PixbufRotation.UPSIDEDOWN,
            270: GdkPixbuf.PixbufRotation.CLOCKWISE,
        }
        gdk_rot = gdk_rot_map.get(rotation, GdkPixbuf.PixbufRotation.NONE)
        if gdk_rot == GdkPixbuf.PixbufRotation.NONE:
            self._editor_display_pixbuf = self._viewer_pixbuf
        else:
            self._editor_display_pixbuf = self._viewer_pixbuf.rotate_simple(gdk_rot)
        self.photo_picture.set_pixbuf(self._editor_display_pixbuf)

    def on_editor_toggle_crop(self, btn):
        self._editor_crop_mode = btn.get_active()
        log_info(f"Editor crop-modus: {'aan' if self._editor_crop_mode else 'uit'}")
        self._crop_rect        = None
        self._crop_handle      = None
        self._crop_rect_origin = None
        self.crop_overlay_area.set_visible(self._editor_crop_mode)
        if self._editor_crop_mode:
            self.crop_overlay_area.queue_draw()

    def _get_image_display_rect(self, widget_w, widget_h):
        """Geeft (x, y, w, h) van de foto binnen de widget (letterbox)."""
        pixbuf = self._editor_display_pixbuf or self._viewer_pixbuf
        if not pixbuf:
            return (0, 0, widget_w, widget_h)
        img_w  = pixbuf.get_width()
        img_h  = pixbuf.get_height()
        scale  = min(widget_w / img_w, widget_h / img_h)
        disp_w = img_w * scale
        disp_h = img_h * scale
        return ((widget_w - disp_w) / 2, (widget_h - disp_h) / 2, disp_w, disp_h)

    def on_crop_draw(self, area, cr, w, h):
        # Initialiseer crop rect op de foto-grenzen
        if self._crop_rect is None:
            ix, iy, iw, ih = self._get_image_display_rect(w, h)
            self._crop_rect = [ix, iy, ix + iw, iy + ih]

        x1, y1, x2, y2 = self._crop_rect
        rw, rh = x2 - x1, y2 - y1

        # Donkere overlay
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.paint()

        # Snijgebied uitsparen
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.rectangle(x1, y1, rw, rh)
        cr.fill()
        cr.set_operator(cairo.OPERATOR_OVER)

        # Rand
        cr.set_source_rgba(1, 1, 1, 1.0)
        cr.set_line_width(2)
        cr.rectangle(x1, y1, rw, rh)
        cr.stroke()

        # Derde-lijn rasters
        cr.set_source_rgba(1, 1, 1, 0.35)
        cr.set_line_width(1)
        for i in (1, 2):
            cr.move_to(x1 + rw * i / 3, y1)
            cr.line_to(x1 + rw * i / 3, y2)
            cr.move_to(x1, y1 + rh * i / 3)
            cr.line_to(x2, y1 + rh * i / 3)
        cr.stroke()

        # Hoekpunten (witte gevulde cirkels)
        HANDLE_R = 7
        for hx, hy in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
            cr.set_source_rgba(0, 0, 0, 0.5)
            cr.arc(hx, hy, HANDLE_R + 2, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, 1.0)
            cr.arc(hx, hy, HANDLE_R, 0, 2 * math.pi)
            cr.fill()

    def on_crop_drag_begin(self, gesture, x, y):
        if self._crop_rect is None:
            return
        self._crop_rect_origin = list(self._crop_rect)
        HANDLE_R = 20  # detectie-straal
        x1, y1, x2, y2 = self._crop_rect
        for name, (hx, hy) in [('tl', (x1, y1)), ('tr', (x2, y1)),
                                ('bl', (x1, y2)), ('br', (x2, y2))]:
            if (x - hx) ** 2 + (y - hy) ** 2 < HANDLE_R ** 2:
                self._crop_handle = name
                return
        if x1 <= x <= x2 and y1 <= y <= y2:
            self._crop_handle = 'move'
        else:
            self._crop_handle = None

    def on_crop_drag_update(self, gesture, dx, dy):
        if not self._crop_handle or not self._crop_rect_origin:
            return
        ox1, oy1, ox2, oy2 = self._crop_rect_origin
        MIN = 40
        aw = self.crop_overlay_area.get_width()
        ah = self.crop_overlay_area.get_height()
        ix, iy, iw, ih = self._get_image_display_rect(aw, ah)
        ix2, iy2 = ix + iw, iy + ih

        r = list(self._crop_rect)
        h = self._crop_handle
        if h == 'tl':
            r[0] = max(ix, min(ox2 - MIN, ox1 + dx))
            r[1] = max(iy, min(oy2 - MIN, oy1 + dy))
        elif h == 'tr':
            r[2] = min(ix2, max(ox1 + MIN, ox2 + dx))
            r[1] = max(iy, min(oy2 - MIN, oy1 + dy))
        elif h == 'bl':
            r[0] = max(ix, min(ox2 - MIN, ox1 + dx))
            r[3] = min(iy2, max(oy1 + MIN, oy2 + dy))
        elif h == 'br':
            r[2] = min(ix2, max(ox1 + MIN, ox2 + dx))
            r[3] = min(iy2, max(oy1 + MIN, oy2 + dy))
        elif h == 'move':
            rw = ox2 - ox1
            rh = oy2 - oy1
            nx1 = max(ix, min(ix2 - rw, ox1 + dx))
            ny1 = max(iy, min(iy2 - rh, oy1 + dy))
            r = [nx1, ny1, nx1 + rw, ny1 + rh]
        self._crop_rect = r
        self.crop_overlay_area.queue_draw()

    def on_crop_drag_end(self, gesture, dx, dy):
        self._crop_handle      = None
        self._crop_rect_origin = None

    def _widget_to_image_coords(self, wx, wy, widget_w, widget_h):
        pixbuf = self._editor_display_pixbuf or self._viewer_pixbuf
        if not pixbuf:
            return None
        img_w  = pixbuf.get_width()
        img_h  = pixbuf.get_height()
        scale  = min(widget_w / img_w, widget_h / img_h)
        disp_w = img_w * scale
        disp_h = img_h * scale
        x_off  = (widget_w - disp_w) / 2
        y_off  = (widget_h - disp_h) / 2
        ix = max(0, min(img_w, (wx - x_off) / scale))
        iy = max(0, min(img_h, (wy - y_off) / scale))
        return (int(ix), int(iy))

    def on_editor_save(self, btn):
        path     = self.photos[self.current_index]
        rotation = self._editor_rotation
        log_info(f"Editor opslaan: rotation={rotation}° crop={bool(self._crop_rect)} path={path}")

        crop_box = None
        if self._editor_crop_mode and self._crop_rect:
            aw = self.crop_overlay_area.get_width()
            ah = self.crop_overlay_area.get_height()
            p1 = self._widget_to_image_coords(self._crop_rect[0], self._crop_rect[1], aw, ah)
            p2 = self._widget_to_image_coords(self._crop_rect[2], self._crop_rect[3], aw, ah)
            if p1 and p2 and abs(p2[0] - p1[0]) > 4 and abs(p2[1] - p1[1]) > 4:
                crop_box = (min(p1[0], p2[0]), min(p1[1], p2[1]),
                            max(p1[0], p2[0]), max(p1[1], p2[1]))

        self.on_editor_cancel()

        def _do_save():
            try:
                from PIL import Image
                original_mtime = os.path.getmtime(path)
                old_cache = get_cache_path(path)
                from PIL import ImageOps
                img = Image.open(path)
                if img.mode == "P" and "transparency" in img.info:
                    img = img.convert("RGBA")
                ext = os.path.splitext(path)[1].lower()
                is_jpeg = ext in (".jpg", ".jpeg")

                # Bewaar alle EXIF-metadata (GPS, cameragegevens, etc.)
                exif = img.getexif() if is_jpeg else None

                # Normaliseer EXIF-oriëntatie zodat PIL en GdkPixbuf overeenkomen
                img = ImageOps.exif_transpose(img)

                if rotation != 0:
                    img = img.rotate(rotation, expand=True)
                if crop_box:
                    img = img.crop(crop_box)

                if is_jpeg:
                    # Zet oriëntatie op normaal (1) — pixels zijn nu fysiek correct
                    if exif is not None:
                        exif[0x0112] = 1
                    img.save(path, "JPEG", quality=95,
                             exif=exif.tobytes() if exif is not None else b"")
                else:
                    img.save(path, "PNG")
                # Verwijder oude thumbnail-cache en herstel originele datum
                if os.path.exists(old_cache):
                    os.remove(old_cache)
                os.utime(path, (original_mtime, original_mtime))
                GLib.idle_add(_after_save)
            except Exception as e:
                log_error(f"Editor opslaan mislukt: {e}")
                GLib.idle_add(_save_error, str(e))

        def _after_save():
            self._viewer_load_id += 1
            load_id = self._viewer_load_id
            threading.Thread(
                target=self._load_full_photo,
                args=(path, load_id),
                daemon=True
            ).start()
            GLib.timeout_add(300, self.start_load)

        def _save_error(msg):
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading="Opslaan mislukt",
                body=msg
            )
            dialog.add_response("ok", _("OK"))
            dialog.present()

        threading.Thread(target=_do_save, daemon=True).start()

    # ── Verwijderen ───────────────────────────────────────────────────
    def on_delete_current(self, btn):
        path = self.photos[self.current_index]
        log_info(f"Verwijder bevestiging gevraagd: {path}")
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Foto verwijderen?",
            body=f"Weet je zeker dat je '{os.path.basename(path)}' wilt verwijderen? Dit kan niet ongedaan worden gemaakt."
        )
        dialog.add_response("cancel", _("Annuleren"))
        dialog.add_response("delete", _("Verwijderen"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_delete_current_response, path)
        dialog.present()

    def _on_delete_current_response(self, dialog, response, path):
        if response != "delete":
            log_info(f"Verwijderen geannuleerd: {path}")
            return
        try:
            os.remove(path)
            cache_path = get_cache_path(path)
            if os.path.exists(cache_path):
                os.remove(cache_path)
            log_info(f"Foto verwijderd: {path}")
        except Exception as e:
            log_error(f"Verwijderen mislukt: {e}")
            return
        if path in self._favorites:
            self._favorites.discard(path)
            self._schedule_save_favorites()
        self.photos.remove(path)
        if not self.photos:
            self.close_viewer()
            self.show_empty_state()
            return
        if self.current_index >= len(self.photos):
            self.current_index = len(self.photos) - 1
        self._viewer_load_id += 1
        load_id = self._viewer_load_id
        threading.Thread(
            target=self._load_full_photo,
            args=(self.photos[self.current_index], load_id),
            daemon=True
        ).start()
        GLib.timeout_add(500, self.start_load)

    def on_delete_selected(self, btn):
        if not self._selected:
            return
        count = len(self._selected)
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"{count} foto's verwijderen?",
            body=f"Weet je zeker dat je {count} foto's wilt verwijderen? Dit kan niet ongedaan worden gemaakt."
        )
        dialog.add_response("cancel", _("Annuleren"))
        dialog.add_response("delete", f"{count} verwijderen")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_delete_selected_response)
        dialog.present()

    def _on_delete_selected_response(self, dialog, response):
        if response != "delete":
            return
        paths_to_delete = [self.photos[i] for i in self._selected if i < len(self.photos)]
        fav_changed = False
        for path in paths_to_delete:
            try:
                os.remove(path)
                cache_path = get_cache_path(path)
                if os.path.exists(cache_path):
                    os.remove(cache_path)
            except Exception as e:
                log_error(f"Verwijderen mislukt: {e}")
            if path in self._favorites:
                self._favorites.discard(path)
                fav_changed = True
        if fav_changed:
            self._schedule_save_favorites()
        self.toggle_select_mode()
        self.load_photos()

    # ── Instellingen ──────────────────────────────────────────────────
    def on_settings_clicked(self, btn):
        log_info(_("Instellingen geopend"))
        dialog = Adw.PreferencesDialog()
        dialog.set_title(_("Instellingen"))

        display_page = Adw.PreferencesPage()
        display_page.set_title(_("Weergave"))
        display_page.set_icon_name("preferences-desktop-display-symbolic")

        import_page = Adw.PreferencesPage()
        import_page.set_title(_("Importeren"))
        import_page.set_icon_name("document-send-symbolic")

        advanced_page = Adw.PreferencesPage()
        advanced_page.set_title(_("Geavanceerd"))
        advanced_page.set_icon_name("applications-engineering-symbolic")

        about_page = Adw.PreferencesPage()
        about_page.set_title(_("Over"))
        about_page.set_icon_name("help-about-symbolic")

        folder_group = Adw.PreferencesGroup()
        folder_group.set_title(_("Foto map"))
        folder_group.set_description(_("Waar worden je foto's opgeslagen"))

        self.folder_row = Adw.ActionRow()
        self.folder_row.set_title(_("Huidige map"))
        self.folder_row.set_subtitle(self.settings.get("photo_path", "Niet ingesteld"))

        change_folder_btn = Gtk.Button(label=_("Wijzigen"))
        change_folder_btn.add_css_class("flat")
        change_folder_btn.set_valign(Gtk.Align.CENTER)
        change_folder_btn.connect("clicked", lambda b: self.change_folder(dialog))
        self.folder_row.add_suffix(change_folder_btn)
        folder_group.add(self.folder_row)
        display_page.add(folder_group)

        display_group = Adw.PreferencesGroup()
        display_group.set_title(_("Weergave"))
        display_group.set_description(_("Hoe foto's in het grid worden getoond"))

        thumb_row = Adw.ActionRow(
            title="Thumbnail grootte",
            subtitle=f"{THUMB_SIZE} px"
        )
        thumb_adj = Gtk.Adjustment(
            value=float(THUMB_SIZE),
            lower=200.0, upper=500.0,
            step_increment=20.0, page_increment=40.0
        )
        thumb_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            adjustment=thumb_adj
        )
        thumb_scale.set_size_request(200, -1)
        thumb_scale.set_draw_value(False)
        thumb_scale.set_valign(Gtk.Align.CENTER)
        thumb_scale.connect("value-changed", self._on_thumb_size_changed, thumb_row)
        thumb_row.add_suffix(thumb_scale)

        thumb_reset_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        thumb_reset_btn.add_css_class("flat")
        thumb_reset_btn.add_css_class("circular")
        thumb_reset_btn.set_valign(Gtk.Align.CENTER)
        thumb_reset_btn.set_tooltip_text(_("Terug naar standaard (200 px)"))
        thumb_reset_btn.set_sensitive(int(thumb_adj.get_value()) != 200)
        thumb_reset_btn.connect("clicked", lambda b: thumb_adj.set_value(200.0))
        thumb_adj.connect(
            "value-changed",
            lambda a: thumb_reset_btn.set_sensitive(int(a.get_value()) != 200)
        )
        thumb_row.add_suffix(thumb_reset_btn)

        display_group.add(thumb_row)

        # Taal-keuze
        lang_row = Adw.ActionRow(
            title=_("Taal"),
            subtitle=_("Herstart van Pixora is nodig om een nieuwe taal te laden")
        )
        lang_model = Gtk.StringList()
        self._lang_codes = ["nl", "en"]
        self._lang_labels = ["🇳🇱  Nederlands", "🇬🇧  English"]
        for label in self._lang_labels:
            lang_model.append(label)
        lang_combo = Gtk.DropDown(model=lang_model)
        lang_combo.set_valign(Gtk.Align.CENTER)
        current_lang = self.settings.get("language", "nl")
        try:
            lang_combo.set_selected(self._lang_codes.index(current_lang))
        except ValueError:
            lang_combo.set_selected(0)
        lang_combo.connect("notify::selected", self._on_language_changed)
        lang_row.add_suffix(lang_combo)
        display_group.add(lang_row)

        display_page.add(display_group)

        dev_group = Adw.PreferencesGroup()
        dev_group.set_title(_("Geavanceerd"))
        dev_group.set_description(
            "Developer mode toont Pixora met terminal-output en gebruikt "
            "de terminal-updater. Alleen aanzetten als je weet wat je doet."
        )
        current_dev = bool(self.settings.get("dev_mode", False))
        dev_row = Adw.ActionRow(
            title="Developer mode",
            subtitle=_("Actief") if current_dev else _("Inactief")
        )
        dev_btn = Gtk.Button(
            label=_("Deactiveren") if current_dev else _("Activeren")
        )
        dev_btn.add_css_class("flat")
        dev_btn.set_valign(Gtk.Align.CENTER)
        dev_btn.connect("clicked", self._on_toggle_dev_mode, dev_row)
        dev_row.add_suffix(dev_btn)
        self._dev_btn = dev_btn
        dev_group.add(dev_row)
        advanced_page.add(dev_group)

        structure_group = Adw.PreferencesGroup()
        structure_group.set_title(_("Mapstructuur"))
        structure_group.set_description(_("Hoe worden je foto's georganiseerd"))
        current_structure = self.settings.get("structure", "year_month")

        self.radio_flat = Gtk.CheckButton()
        self.radio_flat.set_active(current_structure == "flat")
        self.radio_flat.connect("toggled", lambda b: self.on_structure_changed("flat", b))
        flat_row = Adw.ActionRow(title=_("Plat"), subtitle=_("Alles in één map"))
        flat_row.add_prefix(Gtk.Image.new_from_icon_name("folder-symbolic"))
        flat_row.add_prefix(self.radio_flat)
        flat_row.set_activatable_widget(self.radio_flat)
        structure_group.add(flat_row)

        self.radio_year = Gtk.CheckButton()
        self.radio_year.set_group(self.radio_flat)
        self.radio_year.set_active(current_structure == "year")
        self.radio_year.connect("toggled", lambda b: self.on_structure_changed("year", b))
        year_row = Adw.ActionRow(title=_("Per jaar"), subtitle=_("2024/   2025/"))
        year_row.add_prefix(Gtk.Image.new_from_icon_name("folder-open-symbolic"))
        year_row.add_prefix(self.radio_year)
        year_row.set_activatable_widget(self.radio_year)
        structure_group.add(year_row)

        self.radio_month = Gtk.CheckButton()
        self.radio_month.set_group(self.radio_flat)
        self.radio_month.set_active(current_structure == "year_month")
        self.radio_month.connect("toggled", lambda b: self.on_structure_changed("year_month", b))
        month_row = Adw.ActionRow(title=_("Per jaar/maand"), subtitle=_("2024/2024-03/   2024/2024-04/"))
        month_row.add_prefix(Gtk.Image.new_from_icon_name("view-list-symbolic"))
        month_row.add_prefix(self.radio_month)
        month_row.set_activatable_widget(self.radio_month)
        structure_group.add(month_row)
        import_page.add(structure_group)

        dup_group = Adw.PreferencesGroup()
        dup_group.set_title(_("Duplicate detectie"))
        dup_group.set_description(_("Hoe streng worden duplicaten gedetecteerd"))
        current_threshold = self.settings.get("duplicate_threshold", 2)

        self.radio_strict = Gtk.CheckButton()
        self.radio_strict.set_active(current_threshold == 1)
        self.radio_strict.connect("toggled", lambda b: self.on_threshold_changed(1, b))
        strict_row = Adw.ActionRow(title=_("Streng"), subtitle=_("Alleen exact dezelfde foto's"))
        strict_row.add_prefix(Gtk.Image.new_from_icon_name("security-high-symbolic"))
        strict_row.add_prefix(self.radio_strict)
        strict_row.set_activatable_widget(self.radio_strict)
        dup_group.add(strict_row)

        self.radio_normal = Gtk.CheckButton()
        self.radio_normal.set_group(self.radio_strict)
        self.radio_normal.set_active(current_threshold == 2)
        self.radio_normal.connect("toggled", lambda b: self.on_threshold_changed(2, b))
        normal_row = Adw.ActionRow(title=_("Normaal"), subtitle=_("Bijna identieke foto's worden gedetecteerd"))
        normal_row.add_prefix(Gtk.Image.new_from_icon_name("security-medium-symbolic"))
        normal_row.add_prefix(self.radio_normal)
        normal_row.set_activatable_widget(self.radio_normal)
        dup_group.add(normal_row)

        self.radio_loose = Gtk.CheckButton()
        self.radio_loose.set_group(self.radio_strict)
        self.radio_loose.set_active(current_threshold == 3)
        self.radio_loose.connect("toggled", lambda b: self.on_threshold_changed(3, b))
        loose_row = Adw.ActionRow(title=_("Soepel"), subtitle=_("Ook licht bewerkte foto's worden gedetecteerd"))
        loose_row.add_prefix(Gtk.Image.new_from_icon_name("security-low-symbolic"))
        loose_row.add_prefix(self.radio_loose)
        loose_row.set_activatable_widget(self.radio_loose)
        dup_group.add(loose_row)
        import_page.add(dup_group)

        backup_group = Adw.PreferencesGroup()
        backup_group.set_title(_("Automatische backup"))
        backup_group.set_description(_("Backup naar externe USB schijf na elke import"))

        self.settings_backup_switch = Gtk.Switch()
        self.settings_backup_switch.set_valign(Gtk.Align.CENTER)
        self.settings_backup_switch.set_active(bool(self.settings.get("backup_uuid")))
        self.settings_backup_switch.connect("notify::active", self.on_settings_backup_toggle)

        backup_toggle_row = Adw.ActionRow(title=_("Automatische backup"), subtitle=_("Synchroniseert na elke import"))
        backup_toggle_row.add_suffix(self.settings_backup_switch)
        backup_toggle_row.set_activatable_widget(self.settings_backup_switch)
        backup_group.add(backup_toggle_row)

        self.settings_drive_model = Gtk.StringList()
        self.settings_drives = get_available_drives()
        if self.settings_drives:
            for uuid, label in self.settings_drives:
                self.settings_drive_model.append(label)
        else:
            self.settings_drive_model.append("Geen externe schijven gevonden")

        self.settings_drive_combo = Gtk.DropDown(model=self.settings_drive_model)
        self.settings_drive_combo.set_size_request(220, -1)
        self.settings_drive_combo.set_sensitive(bool(self.settings.get("backup_uuid")))
        self.settings_drive_combo.connect("notify::selected", self.on_settings_drive_selected)

        current_uuid = self.settings.get("backup_uuid")
        if current_uuid and self.settings_drives:
            for i, (uuid, label) in enumerate(self.settings_drives):
                if uuid == current_uuid:
                    self.settings_drive_combo.set_selected(i)
                    break

        settings_refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        settings_refresh_btn.add_css_class("flat")
        settings_refresh_btn.set_valign(Gtk.Align.CENTER)
        settings_refresh_btn.connect("clicked", self.on_settings_refresh_drives)

        self.settings_drive_row = Adw.ActionRow(title=_("Backup schijf"), subtitle=_("Alleen externe schijven"))
        self.settings_drive_row.add_suffix(settings_refresh_btn)
        self.settings_drive_row.add_suffix(self.settings_drive_combo)
        self.settings_drive_row.set_sensitive(bool(self.settings.get("backup_uuid")))
        backup_group.add(self.settings_drive_row)

        current_backup_path = self.settings.get("backup_path", "Niet ingesteld")
        self.settings_backup_folder_row = Adw.ActionRow(
            title="Map op backup schijf",
            subtitle=current_backup_path or "Niet ingesteld"
        )
        self.settings_backup_folder_row.set_sensitive(bool(self.settings.get("backup_uuid")))

        change_backup_folder_btn = Gtk.Button(label=_("Wijzigen"))
        change_backup_folder_btn.add_css_class("flat")
        change_backup_folder_btn.set_valign(Gtk.Align.CENTER)
        change_backup_folder_btn.connect("clicked", self.on_settings_change_backup_folder)
        self.settings_backup_folder_row.add_suffix(change_backup_folder_btn)
        backup_group.add(self.settings_backup_folder_row)
        import_page.add(backup_group)

        about_group = Adw.PreferencesGroup()
        about_group.set_title(_("Over"))

        # App info row
        app_row = Adw.ActionRow(
            title=_("Pixora"),
            subtitle=_("Foto &amp; video manager door LinuxGinger"))
        icon_path = os.path.join(DOCS_DIR, "pixora-icon.svg")
        if os.path.exists(icon_path):
            app_icon = Gtk.Image.new_from_file(icon_path)
            app_icon.set_pixel_size(32)
            app_row.add_prefix(app_icon)
        about_group.add(app_row)

        # Versie row
        installed_version_path = os.path.join(os.path.expanduser("~"), ".config", "pixora", "installed_version")
        try:
            installed_ver = open(installed_version_path).read().strip()
        except Exception:
            installed_ver = "Onbekend"
        version_row = Adw.ActionRow(title="Versie", subtitle=installed_ver)
        about_group.add(version_row)

        # Controleer op updates row
        self._update_check_row = Adw.ActionRow(title=_("Controleer op updates"))

        self._update_check_btn = Gtk.Button(label=_("Controleer"))
        self._update_check_btn.add_css_class("flat")
        self._update_check_btn.set_valign(Gtk.Align.CENTER)
        self._update_check_btn.connect("clicked", self._on_settings_check_update)
        self._update_check_row.add_suffix(self._update_check_btn)

        self._update_check_spinner = Gtk.Spinner()
        self._update_check_spinner.set_valign(Gtk.Align.CENTER)
        self._update_check_spinner.set_visible(False)
        self._update_check_row.add_suffix(self._update_check_spinner)

        about_group.add(self._update_check_row)
        about_page.add(about_group)

        dialog.add(display_page)
        dialog.add(import_page)
        dialog.add(advanced_page)
        dialog.add(about_page)
        dialog.present(self)

    def _on_thumb_size_changed(self, scale, row):
        new_size = int(scale.get_value())
        # Snap naar stappen van 20
        new_size = (new_size // 20) * 20
        row.set_subtitle(f"{new_size} px")
        self._pending_thumb_size = new_size
        if hasattr(self, '_thumb_size_timer') and self._thumb_size_timer:
            try:
                GLib.source_remove(self._thumb_size_timer)
            except Exception:
                pass
        self._thumb_size_timer = GLib.timeout_add(400, self._apply_thumb_size_change)

    def _apply_thumb_size_change(self):
        self._thumb_size_timer = None
        new_size = self._pending_thumb_size
        global THUMB_SIZE
        if new_size == THUMB_SIZE:
            return False
        log_info(f"Thumbnail-grootte gewijzigd: {THUMB_SIZE}px → {new_size}px")
        THUMB_SIZE = new_size
        self.settings["thumbnail_size"] = new_size
        save_settings(self.settings)
        self.load_photos()
        return False

    def _on_reset_usbmuxd(self, btn):
        log_info(_("Reset usbmuxd aangeroepen (settings)"))
        btn.set_sensitive(False)
        btn.set_label(_("Bezig…"))

        def do():
            result_msg = ""
            ok = False
            try:
                r = subprocess.run(
                    ["pkexec", "sh", "-c",
                     "killall usbmuxd 2>/dev/null; sleep 0.5; usbmuxd"],
                    capture_output=True, text=True, timeout=30
                )
                if r.returncode == 0:
                    ok = True
                    result_msg = "usbmuxd opnieuw gestart. Sluit je iPhone aan en tap Trust."
                elif r.returncode == 126 or r.returncode == 127:
                    result_msg = "Wachtwoord geannuleerd of pkexec niet beschikbaar."
                else:
                    result_msg = f"Herstart mislukt (code {r.returncode}).\n{r.stderr.strip()[:200]}"
            except FileNotFoundError:
                result_msg = ("pkexec niet gevonden. Voer handmatig uit:\n"
                              "  sudo killall usbmuxd; sudo usbmuxd")
            except subprocess.TimeoutExpired:
                result_msg = "Herstart duurde te lang (timeout)."
            except Exception as e:
                result_msg = f"Onverwachte fout: {e}"
            GLib.idle_add(self._after_usbmuxd_reset, btn, ok, result_msg)

        threading.Thread(target=do, daemon=True).start()

    def _after_usbmuxd_reset(self, btn, ok, msg):
        btn.set_label(_("Herstart"))
        btn.set_sensitive(True)
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="USB-verbinding herstart" if ok else "Herstart mislukt",
            body=msg
        )
        dialog.add_response("ok", _("OK"))
        dialog.present()
        if ok:
            GLib.timeout_add(500, self._poll_import_device)
        return False

    def _on_clear_pair_records(self, btn):
        log_info(_("Pair-records wissen — bevestiging gevraagd"))
        confirm = Adw.MessageDialog(
            transient_for=self,
            heading="Pair-records wissen?",
            body=("Dit verwijdert alle bestaande iPhone-koppelingen in "
                  "/var/lib/lockdown/. Je iPhone vraagt de volgende keer "
                  "opnieuw om Trust.")
        )
        confirm.add_response("cancel", _("Annuleren"))
        confirm.add_response("clear", _("Wissen"))
        confirm.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        confirm.set_default_response("cancel")
        confirm.connect("response", self._do_clear_pair_records)
        confirm.present()

    def _do_clear_pair_records(self, dialog, response):
        if response != "clear":
            return

        def do():
            result_msg = ""
            ok = False
            try:
                r = subprocess.run(
                    ["pkexec", "sh", "-c",
                     "rm -rf /var/lib/lockdown/* && "
                     "killall usbmuxd 2>/dev/null; sleep 0.5; usbmuxd"],
                    capture_output=True, text=True, timeout=30
                )
                if r.returncode == 0:
                    ok = True
                    result_msg = ("Pair-records gewist en usbmuxd opnieuw gestart. "
                                  "Sluit je iPhone aan en tap Trust.")
                else:
                    result_msg = f"Wissen mislukt (code {r.returncode})."
            except FileNotFoundError:
                result_msg = "pkexec niet gevonden."
            except Exception as e:
                result_msg = f"Fout: {e}"
            GLib.idle_add(self._show_info_dialog,
                          "Klaar" if ok else "Mislukt", result_msg)

        threading.Thread(target=do, daemon=True).start()

    def _show_info_dialog(self, heading, body):
        d = Adw.MessageDialog(transient_for=self, heading=heading, body=body)
        d.add_response("ok", _("OK"))
        d.present()
        return False

    def _on_language_changed(self, combo, _pspec):
        idx = combo.get_selected()
        if not (0 <= idx < len(self._lang_codes)):
            return
        new_lang = self._lang_codes[idx]
        current_lang = self.settings.get("language", "nl")
        if new_lang == current_lang:
            return
        self.settings["language"] = new_lang
        try:
            save_settings(self.settings)
        except Exception:
            pass
        log_info(f"Taal gewijzigd naar: {new_lang} — Pixora wordt herstart")

        # Laad translation in NIEUWE taal voor de overlay-tekst
        try:
            new_trans = _gettext_mod.translation(
                "pixora", localedir=_LOCALE_DIR,
                languages=[new_lang], fallback=True
            )
            msg = new_trans.gettext("Taal wordt gewijzigd…")
        except Exception:
            msg = _("Taal wordt gewijzigd…")

        # Modale overlay met spinner + tekst — niet sluitbaar, user
        # moet wachten tot relaunch.
        overlay = Gtk.Window()
        overlay.set_modal(True)
        overlay.set_transient_for(self)
        overlay.set_title(_("Pixora"))
        overlay.set_default_size(320, 140)
        overlay.set_resizable(False)
        overlay.set_deletable(False)
        overlay.connect("close-request", lambda *_: True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        spinner = Gtk.Spinner()
        spinner.set_size_request(36, 36)
        spinner.start()
        box.append(spinner)
        lbl = Gtk.Label(label=msg)
        lbl.add_css_class("title-3")
        box.append(lbl)
        overlay.set_child(box)
        overlay.present()

        def _relaunch():
            pixora_bin = os.path.expanduser("~/.local/bin/pixora")
            cmd = [pixora_bin] if os.path.exists(pixora_bin) else \
                  [sys.executable, os.path.join(INSTALL_DIR, "viewer", "main.py")]
            # Strip PIXORA_IN_DEV_TERM zodat de relaunch opnieuw een
            # dev-terminal kan spawnen als dev-mode actief is.
            child_env = {k: v for k, v in os.environ.items()
                         if k != "PIXORA_IN_DEV_TERM"}
            try:
                subprocess.Popen(
                    cmd, start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    env=child_env,
                )
            except Exception as e:
                log_error(f"Relaunch na taal-wissel fout: {e}")
            self.close()
            return False
        GLib.timeout_add(1000, _relaunch)

    def _on_toggle_dev_mode(self, btn, row):
        currently_active = bool(self.settings.get("dev_mode", False))
        target = not currently_active
        if target:
            heading = "Developer mode activeren?"
            body = ("Bij dev mode start Pixora in een terminal en gaan "
                    "updates via de terminal zodat je output kunt zien. "
                    "Pixora herstart direct.")
        else:
            heading = "Developer mode deactiveren?"
            body = "Pixora start daarna zonder terminal en gebruikt de GUI-updater."
        dialog = Adw.MessageDialog(
            transient_for=self, heading=heading, body=body
        )
        dialog.add_response("cancel", _("Nee"))
        dialog.add_response("apply", _("Ja"))
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._apply_dev_mode, target, row, btn)
        dialog.present()

    def _apply_dev_mode(self, dialog, response, target, row, btn):
        if response != "apply":
            log_info(f"Dev-mode toggle geannuleerd (was: {self.settings.get('dev_mode', False)})")
            return
        self.settings["dev_mode"] = target
        save_settings(self.settings)
        log_info(f"Dev-mode {'geactiveerd' if target else 'gedeactiveerd'} → herstarten…")
        row.set_subtitle(_("Actief") if target else _("Inactief"))
        btn.set_label(_("Deactiveren") if target else _("Activeren"))
        # Herstart de app
        GLib.timeout_add(300, self._restart_app)

    def _restart_app(self):
        try:
            script = os.path.abspath(os.path.join(
                os.path.dirname(__file__), "main.py"
            ))
            # Wacht 1.2s zodat de unique GApplication volledig verdwenen is
            # voor de nieuwe instance zich via D-Bus registreert
            env = {
                k: v for k, v in os.environ.items()
                if k != "PIXORA_DEV_LOG_OPENED"  # nieuw main.py mag tail-venster opnieuw openen
            }
            subprocess.Popen(
                ["bash", "-c",
                 f"sleep 1.2 && exec {sys.executable!s} {script!s}"],
                env=env,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log_info(_("Restart gepland (1.2s delay voor GApplication unregister)"))
        except Exception as e:
            log_error(f"Restart fout: {e}")
        self.get_application().quit()
        return False

    def on_structure_changed(self, value, btn):
        if btn.get_active():
            self.settings["structure"] = value
            save_settings(self.settings)

    def on_threshold_changed(self, value, btn):
        if btn.get_active():
            self.settings["duplicate_threshold"] = value
            save_settings(self.settings)

    def on_settings_backup_toggle(self, switch, _):
        active = switch.get_active()
        self.settings_drive_row.set_sensitive(active)
        self.settings_drive_combo.set_sensitive(active)
        self.settings_backup_folder_row.set_sensitive(active)
        if not active:
            self.settings["backup_uuid"] = None
            self.settings["backup_path"] = None
            save_settings(self.settings)

    def on_settings_drive_selected(self, combo, _):
        selected = combo.get_selected()
        if self.settings_drives and selected < len(self.settings_drives):
            self.settings["backup_uuid"] = self.settings_drives[selected][0]
            save_settings(self.settings)

    def on_settings_refresh_drives(self, btn):
        while self.settings_drive_model.get_n_items() > 0:
            self.settings_drive_model.remove(0)
        self.settings_drives = get_available_drives()
        if self.settings_drives:
            for uuid, label in self.settings_drives:
                self.settings_drive_model.append(label)
            self.settings_drive_combo.set_sensitive(True)
        else:
            self.settings_drive_model.append("Geen externe schijven gevonden")
            self.settings_drive_combo.set_sensitive(False)

    def on_settings_change_backup_folder(self, btn):
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Kies map op backup schijf"))
        current_uuid = self.settings.get("backup_uuid")
        if current_uuid:
            mountpoint = get_mountpoint_for_uuid(current_uuid)
            if mountpoint:
                dialog.set_initial_folder(Gio.File.new_for_path(mountpoint))
        dialog.select_folder(self, None, self.on_settings_backup_folder_selected)

    def on_settings_backup_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                chosen = folder.get_path()
                current_uuid = self.settings.get("backup_uuid")
                if current_uuid:
                    mountpoint = get_mountpoint_for_uuid(current_uuid)
                    if mountpoint and not chosen.startswith(mountpoint):
                        return
                self.settings["backup_path"] = chosen
                save_settings(self.settings)
                self.settings_backup_folder_row.set_subtitle(chosen)
        except Exception:
            pass

    def change_folder(self, parent_dialog):
        file_dialog = Gtk.FileDialog()
        file_dialog.set_title(_("Kies foto map"))
        file_dialog.select_folder(self, None, self.on_folder_changed)

    def on_folder_changed(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                new_path = folder.get_path()
                self.settings["photo_path"] = new_path
                save_settings(self.settings)
                self.folder_row.set_subtitle(new_path)
                os.makedirs(new_path, exist_ok=True)
                self.photos = []
                self.load_photos()
        except Exception:
            pass

    def open_importer(self, btn=None):
        log_info(f"Importer geopend (iOS device {'aanwezig' if self._ios_device_present else 'niet gedetecteerd'})")
        self.header.set_visible(False)
        self.bottom_stack.set_visible(False)
        self.main_stack.set_visible_child_name("importer")
        self.importer_page.activate()

    def close_importer(self):
        log_info("Importer gesloten")
        self.importer_page.deactivate()
        self.header.set_visible(True)
        self.bottom_stack.set_visible(True)
        self.main_stack.set_visible_child_name("grid")

    def on_import_done(self, count):
        self.close_importer()
        if count and count > 0:
            self.reload_photos()


