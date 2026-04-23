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
def _detect_system_lang():
    """Map env locale to one of nl/en/de/fr. Fallback: en."""
    for _var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        _val = os.environ.get(_var, "")
        if _val:
            _code = _val.split(":")[0].split(".")[0].split("_")[0].lower()
            if _code in ("nl", "en", "de", "fr"):
                return _code
    return "en"

_SYS_LANG = _detect_system_lang()
try:
    with open(os.path.expanduser("~/.config/pixora/settings.json"), "r") as _sf:
        _lang = json.load(_sf).get("language", _SYS_LANG)
except Exception:
    _lang = _SYS_LANG
_translation = _gettext_mod.translation(
    "pixora", localedir=_LOCALE_DIR, languages=[_lang], fallback=True
)
_ = _translation.gettext
ngettext = _translation.ngettext
_translation.install()  # makes _() available as a builtin

_MO_PATH = os.path.join(_LOCALE_DIR, _lang, "LC_MESSAGES", "pixora.mo")
_I18N_STATUS = (
    f"lang={_lang} mo={'found' if os.path.exists(_MO_PATH) else 'MISSING'} "
    f"path={_MO_PATH}"
)

# LC_TIME sync so strftime("%B") follows the chosen language, not system locale.
import locale as _locale_mod
_LC_TIME_CANDIDATES = {
    "nl": ["nl_NL.UTF-8", "nl_NL.utf8", "nl_NL", "nl"],
    "en": ["en_US.UTF-8", "en_US.utf8", "en_US", "C.UTF-8", "C"],
    "de": ["de_DE.UTF-8", "de_DE.utf8", "de_DE", "de"],
    "fr": ["fr_FR.UTF-8", "fr_FR.utf8", "fr_FR", "fr"],
}
for _cand in _LC_TIME_CANDIDATES.get(_lang, []):
    try:
        _locale_mod.setlocale(_locale_mod.LC_TIME, _cand)
        break
    except _locale_mod.Error:
        continue

# Read dev_mode directly from settings.json — importing from main.py would
# re-run its module-level code and spawn a second terminal.
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

# Dev-mode: enable faulthandler so hangs/crashes produce thread stackdumps.
# `kill -USR1 <pid>` dumps thread stacks on demand.
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

# Catch uncaught exceptions so they surface with file:line + traceback.
if _DEV_MODE:
    import traceback
    def _excepthook(exc_type, exc_val, exc_tb):
        tb_lines = traceback.format_exception(exc_type, exc_val, exc_tb)
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
from gi.repository import Gtk, Adw, GLib, GdkPixbuf, Gdk, Pango
gi.require_foreign("cairo")
import cairo

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

# Ubuntu 24.04 AppArmor blocks unprivileged user namespaces → WebKit's
# bwrap sandbox fails with "Permission denied". Disable before import.
os.environ.setdefault("WEBKIT_DISABLE_SANDBOX", "1")
os.environ.setdefault("WEBKIT_FORCE_SANDBOX", "0")
# Force GPU compositing — in some VMs WebKit otherwise falls back to software.
os.environ.setdefault("WEBKIT_FORCE_COMPOSITING_MODE", "1")
# EGL/DMA-BUF beats GLX in VMs (VMware SVGA3D).
os.environ.setdefault("WEBKIT_USE_EGL", "1")
os.environ.setdefault("GDK_GL", "gles")
# JavaScriptCore uses SIGUSR1 (=10) for GC by default, which clobbers our
# dev-mode faulthandler for `kill -USR1 <pid>`. Route JSC to SIGUSR2.
os.environ.setdefault("JSC_SIGNAL_FOR_GC", "12")

# Prefer WebKit 6.0 (GTK4-native), then WebKit2 4.1 / 4.0.
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

ASSETS_DIR       = os.path.join(os.path.dirname(__file__), "..", "assets", "logos")
VERSION_FILE     = os.path.join(os.path.dirname(__file__), "..", "version.txt")
LICENSE_PATH     = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "LICENSE"
))
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
    return os.path.join(ASSETS_DIR, f"pixora-logo-{'dark' if dark_mode else 'light'}.svg")

def save_settings(settings):
    # Atomic write + 0600: survives crash mid-write and is privacy-sensitive
    # on multi-user machines (contains photo_path).
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(settings, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def load_favorites():
    try:
        with open(FAVORITES_PATH, "r") as f:
            data = json.load(f)
        if isinstance(data, list):
            return set(data)
        return set()
    except FileNotFoundError:
        return set()
    except Exception:
        # Corrupt file: back it up and start fresh instead of re-hitting the
        # parse error on every launch.
        try:
            os.rename(FAVORITES_PATH, FAVORITES_PATH + ".corrupt")
        except Exception:
            pass
        return set()


def save_favorites(favorites):
    # Same atomic-0600 pattern as save_settings.
    try:
        os.makedirs(os.path.dirname(FAVORITES_PATH), exist_ok=True)
        tmp = FAVORITES_PATH + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(sorted(favorites), f, indent=2)
        os.replace(tmp, FAVORITES_PATH)
    except Exception:
        pass

# Module-level lsblk cache. Both get_available_drives() and
# get_mountpoint_for_uuid() hit the same JSON, so caching it once avoids
# back-to-back 5-sec subprocess calls when Settings opens (both helpers
# run during dialog construction).
_LSBLK_CACHE = {"ts": 0.0, "data": None}
# 10s TTL covers burst-calls during Settings open and usually survives
# between 8s drive-polls without a second subprocess. Polling forces a
# refresh anyway, so the cache doesn't lie about attach/detach.
_LSBLK_CACHE_TTL = 10.0

def _lsblk_fetch():
    """Run lsblk. Returns parsed JSON or None on failure/timeout."""
    try:
        result = subprocess.run(
            ["lsblk", "-o", "NAME,UUID,LABEL,SIZE,FSTYPE,MOUNTPOINT,HOTPLUG,RM,TRAN", "-J"],
            capture_output=True, text=True, timeout=5,
        )
        return json.loads(result.stdout)
    except Exception as e:
        log_error(_("Drive detection error: {err}").format(err=e))
        return None

def _lsblk_data(force=False):
    """Cached lsblk JSON. Refetches when stale (>TTL) or force=True."""
    now = time.time()
    if (not force
            and _LSBLK_CACHE["data"] is not None
            and now - _LSBLK_CACHE["ts"] < _LSBLK_CACHE_TTL):
        return _LSBLK_CACHE["data"]
    data = _lsblk_fetch()
    if data is not None:
        _LSBLK_CACHE["ts"] = now
        _LSBLK_CACHE["data"] = data
    return data

def _warm_lsblk_cache_async():
    """Fire-and-forget pre-warm so the first Settings open is instant."""
    threading.Thread(target=_lsblk_data, daemon=True).start()

def get_available_drives():
    """Return drives suitable as backup target: supported fstype, external
    (hotplug/removable/USB/mounted under /media/, /run/media/, /mnt/), and
    never a system partition (/, /boot, /home, /boot/efi, …)."""
    drives = []
    SYS_MOUNTS = {"/", "/boot", "/boot/efi", "/home", "/var", "/usr", "/etc"}
    EXTERNAL_PREFIXES = ("/media/", "/run/media/", "/mnt/")
    seen_uuids = set()
    data = _lsblk_data()
    if data is None:
        return drives

    def _is_external(mountpoint, hotplug, rm, tran):
        if hotplug or rm:
            return True
        if tran in ("usb", "ieee1394"):
            return True
        if mountpoint and any(mountpoint.startswith(p) for p in EXTERNAL_PREFIXES):
            return True
        return False

    def process_device(device, parent_hotplug=False, parent_rm=False, parent_tran=""):
        hotplug = bool(device.get("hotplug", False)) or parent_hotplug
        rm = bool(device.get("rm", False)) or parent_rm
        tran = (device.get("tran") or parent_tran or "").lower()
        uuid       = device.get("uuid")
        fstype     = (device.get("fstype") or "").lower()
        label      = (device.get("label") or "").strip()
        size       = device.get("size") or ""
        mountpoint = (device.get("mountpoint") or "").strip()
        if mountpoint in SYS_MOUNTS:
            pass  # skip system partition but still recurse into children
        elif uuid and fstype in BACKUP_FSTYPES:
            if _is_external(mountpoint, hotplug, rm, tran) and uuid not in seen_uuids:
                seen_uuids.add(uuid)
                display = (f"💾  {label}  ({size})" if label else
                           f"💾  {mountpoint}  ({size})" if mountpoint else
                           f"💾  {_('External drive')}  ({size})")
                drives.append((uuid, display))
        for child in device.get("children", []):
            process_device(child, hotplug, rm, tran)

    for device in data.get("blockdevices", []):
        process_device(device)
    return drives

def _cmd_available_bk(cmd):
    import shutil as _sh
    return _sh.which(cmd) is not None

def get_mountpoint_for_uuid(uuid):
    data = _lsblk_data()
    if data is None:
        return None
    for device in data.get("blockdevices", []):
        for child in device.get("children", [device]):
            if child.get("uuid") == uuid:
                return child.get("mountpoint")
    return None

_MONTH_KEYS = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]

def format_date_header(dt):
    return f"{dt.day} {_(_MONTH_KEYS[dt.month])} {dt.year}"

def format_viewer_date(dt):
    # Use gettext for date formatting — strftime-locale requires en_US.UTF-8
    # etc. to be installed system-wide. Bogus dates (<2000) → "unknown date".
    if dt is None or dt.year < 2000:
        return _("Unknown date")
    return _("{month} {day}, {year}  {time}").format(
        day=dt.day,
        month=_(_MONTH_KEYS[dt.month]),
        year=dt.year,
        time=dt.strftime("%H:%M"),
    )


# Metadata cache — avoids repeat EXIF/ffprobe calls on grid reload / map open.
_METADATA_CACHE_PATH = os.path.expanduser("~/.cache/pixora/metadata.json")
_metadata_cache = {
    "video_duration": {},
    "photo_date": {},
    "gps_coords": {},
    "geocode": {},
}
_metadata_dirty = False
_metadata_save_lock = threading.Lock()


_METADATA_MAX_GEOCODE = 3000


def _load_metadata_cache():
    global _metadata_dirty
    try:
        with open(_METADATA_CACHE_PATH, "r") as f:
            data = json.load(f)
        for k in _metadata_cache.keys():
            v = data.get(k)
            if isinstance(v, dict):
                _metadata_cache[k] = v
        # Drop legacy geocode entries (empty values or pre-language key format).
        geo = _metadata_cache.get("geocode", {})
        pruned = {k: v for k, v in geo.items() if v and ":" in k}
        if len(pruned) != len(geo):
            _metadata_cache["geocode"] = pruned
            _metadata_dirty = True
        # Prune stale file-based entries so the cache doesn't grow forever
        # after deletes. Startup-only: O(N) stat calls, but once.
        for bucket in ("photo_date", "video_duration", "gps_coords"):
            entries = _metadata_cache.get(bucket, {})
            stale = [p for p in entries if not os.path.exists(p)]
            for p in stale:
                entries.pop(p, None)
            if stale:
                _metadata_dirty = True
        # Geocode bucket: trim if too large. No access-timing available, so
        # pseudo-LRU via dict insertion order (Py3.7+).
        geo = _metadata_cache.get("geocode", {})
        if len(geo) > _METADATA_MAX_GEOCODE:
            keep = dict(list(geo.items())[-_METADATA_MAX_GEOCODE:])
            _metadata_cache["geocode"] = keep
            _metadata_dirty = True
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
    # Exact mtime match — a 0.5s tolerance once gave stale data right after
    # an edit, so we compare exactly.
    if entry.get("m") == mtime:
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

_geocode_failed_at = {}


def reverse_geocode(lat, lon):
    # Cache key includes language — same coords get different labels per lang
    # ("Paris, France" / "Parijs, Frankrijk" / "Paris, Frankreich").
    global _metadata_dirty
    key = f"{_lang}:{lat:.4f},{lon:.4f}"
    cached = _metadata_cache["geocode"].get(key)
    if cached:
        return cached
    # Rate-limit retries after a failed lookup (30 min) so we don't burn
    # 5s timeouts on every viewer open when offline.
    last_fail = _geocode_failed_at.get(key, 0)
    if time.time() - last_fail < 1800:
        return ""
    result = _reverse_geocode_raw(lat, lon)
    if result:
        _metadata_cache["geocode"][key] = result
        _metadata_dirty = True
        _geocode_failed_at.pop(key, None)
    else:
        _geocode_failed_at[key] = time.time()
    return result


def _reverse_geocode_raw(lat, lon):
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json"
        # Accept-Language returns city/country labels in the chosen Pixora lang.
        req = urllib.request.Request(url, headers={
            "User-Agent": "Pixora/1.0 (+https://github.com/Linux-Ginger/Pixora)",
            "Accept-Language": _lang,
        })
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
    """Timestamp from EXIF, falling back to mtime."""
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
            # Fixed height, width keeps aspect (-2 = divisible by 2).
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
            img = ImageOps.exif_transpose(img)  # honor EXIF orientation
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
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


class TimelineBar(Gtk.DrawingArea):
    """Right-side timeline bar. Entries are (label, y_px, is_year); y_px is
    the header's actual Y pixel in grid_box, mapped to the bar via max_scroll
    (= adj.upper - adj.page_size) so positions stay in sync with scrolling."""
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

    def set_data(self, entries, max_scroll):
        self._entries    = entries
        self._max_scroll = max(max_scroll, 1.0)
        self._recalc()
        self.queue_draw()

    def update_scroll(self, value, max_scroll):
        self._scroll_val = value
        self._max_scroll = max(max_scroll, 1.0)
        self._recalc()

    def _recalc(self):
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
                _("WebKitGTK failed to load.") + "\n\n"
                + _("Install one of:") + "\n"
                "  sudo apt install gir1.2-webkit-6.0\n"
                "  sudo apt install gir1.2-webkit2-4.1\n"
                + (f"\n{_('Technical error')}: {_webkit_load_error}" if _webkit_load_error else "")
            )
            log_error(_("WebKit unavailable: {err}").format(err=_webkit_load_error))
            return

        try:
            self._init_webview()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            log_error(_("WebView init crashed:\n{tb}").format(tb=tb))
            self._show_fallback(
                _("Map view failed to start.") + "\n\n"
                + _("Error: {err}").format(err=e) + "\n\n"
                + _("Check /home/beau/.cache/pixora/pixora.log for more.")
            )

    def _show_fallback(self, msg):
        lbl = Gtk.Label(label=msg)
        lbl.set_vexpand(True)
        lbl.set_hexpand(True)
        lbl.set_justify(Gtk.Justification.CENTER)
        lbl.set_wrap(True)
        self.append(lbl)

    def _init_webview(self):
        # Disable WebKit's bwrap sandbox before WebView creation — Ubuntu 24.04
        # blocks unprivileged user namespaces, so bwrap crashes the process.
        # AppArmor still protects the rest of the system.
        network_session = None
        try:
            # WebKit 6.0 API
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
            log_warn(_("NetworkSession sandbox-disable failed: {err}").format(err=e))

        try:
            # WebKit2 4.x API
            if hasattr(WebKit2, "WebContext"):
                wc = WebKit2.WebContext.get_default()
                if wc and hasattr(wc, "set_sandbox_enabled"):
                    wc.set_sandbox_enabled(False)
        except Exception as e:
            log_warn(_("WebContext sandbox-disable failed: {err}").format(err=e))

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
            log_warn(_("WebView settings not fully applied: {err}").format(err=e))

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
                log_error(_("register_script_message_handler failed with all variants"))
            ucm.connect("script-message-received::pixora", self._on_js_message)
        except Exception as e:
            log_error(_("WebView bridge setup error: {err}").format(err=e))

        self.web.connect("load-changed", self._on_load_changed)

        assets_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "assets", "leaflet"
        )
        map_html_path = os.path.abspath(os.path.join(assets_dir, "map.html"))
        if not os.path.exists(map_html_path):
            self._show_fallback(_("Leaflet assets not found:\n{p}").format(p=map_html_path))
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
            "otherInCluster": _("other photos in this cluster"),
            "clickCluster": _("Click cluster to view filtered"),
            "clickOpen": _("Click to open"),
            "offline": _("⚠ No internet connection — map tiles cannot be loaded"),
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
                log_error(_("JS push error: {err}").format(err=e))
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
            log_info(_("Map → open_photos: {n} photos").format(n=len(paths)))
            if paths:
                GLib.idle_add(self.open_photo_cb, paths)
        elif msg_type == "open_photo":
            path = payload.get("path")
            log_info(_("Map → open_photo: {p}").format(p=path))
            if path:
                GLib.idle_add(self.open_photo_cb, [path])
        elif msg_type == "map-ready":
            log_info(_("Map → first tiles loaded"))
            if self.status_cb:
                GLib.idle_add(self.status_cb, "ready")
        elif msg_type == "map-offline":
            log_warn(_("Map → offline / tile errors"))
            if self.status_cb:
                GLib.idle_add(self.status_cb, "offline")


class BackupFolderPicker(Adw.Dialog):
    """Folder picker confined to the USB root, with inline create-subfolder."""

    def __init__(self, mountpoint, current_path, on_selected):
        super().__init__()
        from pathlib import Path as _P
        self._root = _P(mountpoint).resolve()
        self._on_selected = on_selected
        try:
            start = _P(current_path).resolve() if current_path else self._root
            start.relative_to(self._root)
            if not start.is_dir():
                start = self._root
        except Exception:
            start = self._root
        self._cursor = start

        self.set_title(_("Choose backup folder"))
        self.set_content_width(480)
        self.set_content_height(520)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8,
            margin_start=12, margin_end=12, margin_top=12, margin_bottom=12,
        )

        self._path_label = Gtk.Label(xalign=0)
        self._path_label.add_css_class("dim-label")
        self._path_label.set_wrap(True)
        box.append(self._path_label)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._listbox = Gtk.ListBox()
        self._listbox.add_css_class("boxed-list")
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll.set_child(self._listbox)
        box.append(scroll)

        new_btn = Gtk.Button(label=_("New folder"))
        new_btn.set_icon_name("folder-new-symbolic")
        new_btn.connect("clicked", self._on_new_folder_clicked)
        box.append(new_btn)

        actions = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
            halign=Gtk.Align.END,
        )
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda *_a: self.close())
        use = Gtk.Button(label=_("Use this folder"))
        use.add_css_class("suggested-action")
        use.connect("clicked", self._on_use_clicked)
        actions.append(cancel)
        actions.append(use)
        box.append(actions)

        toolbar.set_content(box)
        self.set_child(toolbar)
        self._refresh()

    def _refresh(self):
        from pathlib import Path as _P
        try:
            rel = self._cursor.relative_to(self._root)
        except ValueError:
            self._cursor = self._root
            rel = _P(".")
        if str(rel) == ".":
            self._path_label.set_label(_("Root of USB drive"))
        else:
            self._path_label.set_label(f"/{rel}")

        while True:
            row = self._listbox.get_first_child()
            if row is None:
                break
            self._listbox.remove(row)

        if self._cursor != self._root:
            up = Adw.ActionRow(title=_("Back"))
            up.add_prefix(Gtk.Image.new_from_icon_name("go-up-symbolic"))
            up.set_activatable(True)
            up.connect("activated", lambda *_a: self._navigate(self._cursor.parent))
            self._listbox.append(up)

        try:
            entries = sorted(
                (e for e in self._cursor.iterdir()
                 if e.is_dir() and not e.name.startswith(".")),
                key=lambda p: p.name.lower(),
            )
        except Exception:
            entries = []

        if not entries and self._cursor == self._root:
            empty = Adw.ActionRow(
                title=_("No subfolders"),
                subtitle=_("Create one with the button below"),
            )
            empty.set_sensitive(False)
            self._listbox.append(empty)
        else:
            for e in entries:
                row = Adw.ActionRow(title=e.name)
                row.add_prefix(Gtk.Image.new_from_icon_name("folder-symbolic"))
                row.set_activatable(True)
                row.connect("activated", lambda _r, p=e: self._navigate(p))
                self._listbox.append(row)

    def _navigate(self, path):
        try:
            resolved = path.resolve()
            resolved.relative_to(self._root)
        except Exception:
            return
        if not resolved.is_dir():
            return
        self._cursor = resolved
        self._refresh()

    def _on_new_folder_clicked(self, btn):
        dlg = Adw.AlertDialog(
            heading=_("New folder"),
            body=_("Enter a name for the new folder:"),
        )
        entry = Gtk.Entry()
        entry.set_placeholder_text(_("Folder name"))
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("create", _("Create"))
        dlg.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("create")
        dlg.set_close_response("cancel")
        entry.connect("activate", lambda *_a: dlg.response("create"))
        dlg.connect("response", self._on_create_response, entry)
        dlg.present(self)

    def _on_create_response(self, dlg, response, entry):
        if response != "create":
            return
        name = entry.get_text().strip()
        if not name or "/" in name or name in (".", ".."):
            return
        new_path = self._cursor / name
        try:
            new_path.mkdir(parents=False, exist_ok=True)
        except Exception as exc:
            err = Adw.AlertDialog(
                heading=_("Could not create folder"),
                body=str(exc),
            )
            err.add_response("ok", _("OK"))
            err.present(self)
            return
        self._cursor = new_path
        self._refresh()

    def _on_use_clicked(self, btn):
        try:
            self._cursor.relative_to(self._root)
        except ValueError:
            return
        self._on_selected(str(self._cursor))
        self.close()


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, settings):
        super().__init__(application=app)
        self.settings        = settings
        # Migration: pre-v1.16 installs had no backup_enabled flag; presence
        # of backup_uuid implied backup-on.
        if "backup_enabled" not in self.settings and self.settings.get("backup_uuid"):
            self.settings["backup_enabled"] = True
        # Users who only `git pull` in .local/share/pixora never run updater.sh,
        # so their .desktop still points at an old broken icon. Repair on startup.
        self._repair_desktop_entry()
        global THUMB_SIZE
        try:
            THUMB_SIZE = max(200, min(500, int(settings.get("thumbnail_size", 200))))
        except Exception:
            THUMB_SIZE = 200
        if settings.get("dev_mode"):
            log_info(_("═══ Pixora started in Developer Mode ═══"))
            log_info(f"i18n: {_I18N_STATUS}")
            log_info(_("Config: {p}").format(p=CONFIG_PATH))
            log_info(_("Cache: {p}").format(p=CACHE_DIR))
            log_info(_("Thumbs: {px}px — favorites: {n}").format(px=THUMB_SIZE, n=len(load_favorites())))
            log_info(_("PID: {p} — on hang: 'kill -USR1 {p}' dumps thread-stacks").format(p=os.getpid()))
        log_info(_("Startup phase 1: MainWindow __init__ started"))
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
        self._filmstrip_thumbs      = {}     # visual_pos -> pixbuf
        self._filmstrip_view_order  = []     # list van self.photos-indices, nieuwste-eerst
        self._filmstrip_load_id     = 0
        self._filmstrip_order_cache = None   # (id(photos), len(photos), [sorted_indices])
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
        # Backup-manager state
        self._backup_running   = False
        self._backup_fraction  = 0.0
        self._backup_detail    = ""
        self._backup_proc      = None
        self._backup_total     = 0
        self._backup_done      = 0
        self._backup_scanning  = False
        self._backup_deduping  = False
        self._orphan_reviewing = False
        self._backup_scan_phase = 0.0
        # Orphan state from the last scan: files on USB that aren't in Pixora.
        # Used to warn after silent backup (e.g. user copied a dup folder).
        self._last_scan_orphan_count = 0
        self._last_scan_orphan_rels = []
        self._last_scan_backup_dest = None
        self._backup_scan_anim_id = None
        self._backup_scan_dialog_open = False
        self._manual_scan_requested = False
        # Reorganize gate: blocks backup/sync while the popup/reorg runs and
        # for 10s after. _reorganize_moving drives the progress donut.
        self._reorganize_active = False
        self._reorganize_block_until = 0.0
        self._reorganize_moving = False
        self._reorganize_fraction = 0.0
        self._reorganize_anim_id = None
        # Fullscreen reorganize page: thread writes, timer renders.
        self._reorganize_total_count = 0
        self._reorganize_done_count = 0
        self._reorganize_total_bytes = 0
        self._reorganize_done_bytes = 0
        self._reorganize_start_time = 0.0
        self._reorganize_current_name = ""
        self._reorganize_total_label = ""
        # Silent-mode: auto-popup runs reorganize with no dialog and no
        # fullscreen. Reset per-run so manual "Opruimen" always shows it.
        self._reorganize_silent_run = False
        self._video_paused_by_popup = False
        self._settings_dialog = None
        # Structure-scan state: detects folders outside the chosen structure,
        # works without a backup drive. Donut turns dark-blue (no backup ctx).
        self._structure_scanning = False
        self._structure_startup_scanned = False
        self._structure_popup_dismissed = False
        self._home_ready_at = None
        self._pending_update_version = None
        self._update_dialog_shown = False

        self.set_title("Pixora (Dev Mode)" if self.settings.get("dev_mode") else "Pixora")
        self.set_default_size(9999, 9999)

        self.style_manager = Adw.StyleManager.get_default()
        self.style_manager.connect("notify::dark", self.on_dark_mode_changed)

        from importer_page import ImporterPage
        self.importer_page = ImporterPage(
            on_back_cb=self.close_importer,
            on_done_cb=self.on_import_done,
        )

        log_info(_("Startup phase 2: building pages…"))
        self.main_stack = Gtk.Stack()
        self.main_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.main_stack.set_transition_duration(200)
        self.main_stack.add_named(self.build_grid_page(),   "grid")
        self.main_stack.add_named(self.build_viewer_page(), "viewer")
        self.main_stack.add_named(self.build_map_page(),    "map")
        self.main_stack.add_named(self.importer_page,       "importer")
        self.main_stack.add_named(self._build_reorganize_page(), "reorganize")
        log_info(_("Startup phase 3: pages ready"))

        self.update_banner = Adw.Banner(title="", button_label=_("Update"), use_markup=False)
        self.update_banner.set_revealed(False)
        self.update_banner.connect("button-clicked", self._on_update_banner_clicked)

        self.iphone_banner = Adw.Banner(title="", use_markup=False)
        self.iphone_banner.set_revealed(False)

        self.backup_pending_banner = Adw.Banner(title="", use_markup=False)
        self.backup_pending_banner.set_revealed(False)

        self.backup_done_banner = Adw.Banner(
            title="", button_label=_("OK"), use_markup=False
        )
        self.backup_done_banner.set_revealed(False)
        self.backup_done_banner.connect(
            "button-clicked",
            lambda _b: self.backup_done_banner.set_revealed(False)
        )

        self.toolbar_view = Adw.ToolbarView()
        self.toolbar_view.add_top_bar(self.update_banner)
        self.toolbar_view.add_top_bar(self.iphone_banner)
        self.toolbar_view.add_top_bar(self.backup_pending_banner)
        self.toolbar_view.add_top_bar(self.backup_done_banner)
        self.toolbar_view.add_top_bar(self.build_header())
        self.toolbar_view.set_content(self.main_stack)
        self.toolbar_view.add_bottom_bar(self.build_bottombar())
        toolbar_view = self.toolbar_view

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

        splash_lbl = Gtk.Label(label=_("Pixora is starting…"))
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
        log_info(_("Startup phase 4: loading photos scheduled via idle_add"))
        GLib.idle_add(self.load_photos)
        self.connect("close-request", self.on_close)
        GLib.idle_add(self._check_for_update)
        threading.Thread(target=self._start_services, daemon=True).start()
        # Pre-warm drive cache so the first Settings/backup click doesn't
        # pay a ~5s synchronous lsblk. Cheap on its own thread.
        _warm_lsblk_cache_async()
        # Apply animation preference to every Gtk.Stack we just built. When
        # animations are off, transition_duration(0) makes view switches
        # snap instantly — helps on slow/VM renderers.
        self._apply_animations_state()
        # Wait 5s so the startup-load burst doesn't poison the sample, then
        # check renderer + frame-timing. Shows a one-time popup if the app
        # appears slow.
        GLib.timeout_add_seconds(5, self._start_perf_check)
        # Same 5s mark as "startup survived" — clears main.py's crash-
        # recovery marker so a non-auto gsk_renderer keeps being applied.
        GLib.timeout_add_seconds(5, self._clear_gsk_pending)
        # If main.py just reverted a crashing renderer, tell the user
        # (once) and clear the flag.
        if self.settings.get("gsk_recent_crash"):
            GLib.idle_add(self._show_gsk_recovery_popup)
        self._ios_device_present = False
        self._recovery_prompt_active = False
        self._recovery_cooldown_until = 0.0
        GLib.idle_add(self._poll_import_device)
        GLib.timeout_add_seconds(10, self._poll_import_device)
        self._setup_usb_monitor()
        self._backup_drive_last_seen = False
        GLib.timeout_add_seconds(8, self._poll_backup_drive)
        GLib.idle_add(self._check_pending_backup)
        GLib.timeout_add_seconds(300, self._periodic_scan)
        # Periodic save — otherwise a crash loses all cache work since startup.
        def _periodic_save_cache():
            save_metadata_cache()
            return True  # keep repeating
        GLib.timeout_add_seconds(300, _periodic_save_cache)

    def _clear_gsk_pending(self):
        """Signals main.py's crash-recovery that the current gsk_renderer
        choice booted successfully. Missing file → next run assumes crash
        and reverts to 'auto'.

        Also un-blacklist the current renderer — if it survived 5s it's
        working right now, so the dropdown should stop warning."""
        try:
            path = os.path.expanduser("~/.cache/pixora/.gsk_pending")
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        current = self.settings.get("gsk_renderer", "auto")
        bl = self.settings.get("gsk_renderer_crashed") or []
        if current in bl:
            bl = [x for x in bl if x != current]
            self.settings["gsk_renderer_crashed"] = bl
            try:
                save_settings(self.settings)
            except Exception:
                pass
        return False

    def _show_gsk_recovery_popup(self):
        renderer = self.settings.get("gsk_recent_crash", "")
        # Clear the flag immediately so a refresh/re-open doesn't re-show.
        self.settings["gsk_recent_crash"] = ""
        try:
            save_settings(self.settings)
        except Exception:
            pass
        nice = {"gl": "GPU (GL)", "cairo": "Software (Cairo)", "ngl": "NGL"}.get(
            renderer, renderer or "?"
        )
        dlg = Adw.AlertDialog(
            heading=_("Pixora recovered from a crash"),
            body=_("The last startup crashed while using the '{r}' rendering backend. Pixora reverted to 'Automatic' so it can start again. You'll find this setting under Settings → Advanced → Performance → Rendering backend.").format(r=nice),
        )
        dlg.add_response("ok", _("OK"))
        dlg.set_default_response("ok")
        dlg.set_close_response("ok")
        try:
            self._present_dialog(dlg)
        except Exception:
            try:
                dlg.present(self)
            except Exception:
                pass
        return False

    def _set_toolbars_revealed(self, visible):
        """Adw.ToolbarView has no reveal-duration API, so when animations
        are off we skip set_reveal_*_bars entirely — the caller has already
        hidden the header/bottom individually, which collapses the bars
        to zero height instantly."""
        enabled = bool(self.settings.get("animations_enabled", True))
        if not enabled:
            return
        try:
            self.toolbar_view.set_reveal_top_bars(visible)
            self.toolbar_view.set_reveal_bottom_bars(visible)
        except Exception:
            pass

    def _apply_animations_state(self):
        """Gate the Adwaita transition-durations on the user's preference.
        When animations are off we set every stack to 0ms so view-swaps
        are instant — big difference on VM/software-rendered GTK."""
        enabled = bool(self.settings.get("animations_enabled", True))
        main_d = 200 if enabled else 0
        content_d = 150 if enabled else 0
        bottom_d = 150 if enabled else 0
        if hasattr(self, "main_stack"):
            self.main_stack.set_transition_duration(main_d)
        if hasattr(self, "content_stack"):
            self.content_stack.set_transition_duration(content_d)
        if hasattr(self, "bottom_stack"):
            self.bottom_stack.set_transition_duration(bottom_d)

    def _start_perf_check(self):
        """Two detection passes that share one popup:
          1) GSK renderer = Cairo (pure software fallback) — instant flag.
          2) Passive frame-timing over 30s — catches VMware-LLVMpipe-etc.
        Either hit → popup unless user already dismissed."""
        if self.settings.get("perf_warning_dismissed"):
            return False
        try:
            native = self.get_native()
            renderer = native.get_renderer() if native is not None else None
            rname = type(renderer).__name__ if renderer is not None else ""
            if "Cairo" in rname:
                self._show_perf_warning_popup()
                return False
        except Exception:
            pass
        self._perf_frame_times = []
        try:
            self._perf_tick_id = self.add_tick_callback(self._perf_sample)
        except Exception:
            self._perf_tick_id = None
            return False
        # Hard stop after 30s regardless of samples.
        GLib.timeout_add_seconds(30, self._stop_perf_sampling)
        return False

    def _perf_sample(self, widget, frame_clock):
        if self.settings.get("perf_warning_dismissed"):
            return False  # stop; user already said no-thanks
        try:
            t = frame_clock.get_frame_time()
        except Exception:
            return False
        self._perf_frame_times.append(t)
        if len(self._perf_frame_times) > 60:
            self._perf_frame_times = self._perf_frame_times[-60:]
        if len(self._perf_frame_times) >= 30:
            durations = [
                self._perf_frame_times[i] - self._perf_frame_times[i - 1]
                for i in range(1, len(self._perf_frame_times))
            ]
            # 33_000 µs = 33ms = below 30fps. Count those as "slow frames".
            slow = sum(1 for d in durations if d > 33_000)
            if slow / len(durations) > 0.6:
                self._show_perf_warning_popup()
                self._stop_perf_sampling()
                return False
        return True  # keep sampling

    def _stop_perf_sampling(self):
        tid = getattr(self, "_perf_tick_id", None)
        if tid is not None:
            try:
                self.remove_tick_callback(tid)
            except Exception:
                pass
            self._perf_tick_id = None
        return False

    def _show_perf_warning_popup(self):
        if self.settings.get("perf_warning_dismissed"):
            return
        dlg = Adw.AlertDialog(
            heading=_("Pixora is running slowly on this system"),
            body=_("This can happen without GPU acceleration. You can reduce animations under Settings → Advanced → Performance to make Pixora feel smoother."),
        )
        dlg.add_response("dismiss", _("Don't show again"))
        dlg.add_response("open", _("Open Settings"))
        dlg.set_default_response("open")
        dlg.set_close_response("dismiss")
        dlg.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", self._on_perf_warning_response)
        try:
            self._present_dialog(dlg)
        except Exception:
            try:
                dlg.present(self)
            except Exception:
                pass

    def _on_perf_warning_response(self, dlg, response):
        self.settings["perf_warning_dismissed"] = True
        try:
            save_settings(self.settings)
        except Exception as e:
            log_error(_("Failed to save perf_warning_dismissed: {e}").format(e=e))
        if response == "open":
            try:
                self.on_settings_clicked(None)
            except Exception:
                pass

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
        """Listen to udev USB events for automatic iPhone detection."""
        self._udev_client = None
        if not GUDEV_AVAILABLE:
            return
        try:
            self._udev_client = GUdev.Client(subsystems=["usb"])
            self._udev_client.connect("uevent", self._on_usb_event)
        except Exception as e:
            log_error(_("GUdev monitor failed to start: {err}").format(err=e))
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
        log_info(_("Apple USB device connected (vendor=05ac) — check after 2.5s"))
        # Wait briefly so usbmuxd sees the device before we check.
        GLib.timeout_add(2500, self._post_apple_plugin_check)

    def _post_apple_plugin_check(self):
        # Don't interrupt an open importer.
        try:
            if self.main_stack.get_visible_child_name() == "importer":
                return False
        except Exception:
            pass
        if self._recovery_prompt_active:
            return False
        self._recovery_prompt_active = True
        self._set_iphone_banner(_("📱 iPhone detected, please wait…"))
        threading.Thread(target=self._iphone_recovery_flow, daemon=True).start()
        return False

    def _iphone_recovery_flow(self):
        """Fully automatic recovery: first check, then reset usbmuxd on fail."""
        has_device = self._idevice_check()
        if has_device:
            log_info(_("iPhone directly recognised by usbmuxd"))
            GLib.idle_add(self._iphone_flow_success, False)
            return
        log_warn(_("iPhone not recognised by usbmuxd — starting auto-recovery"))
        GLib.idle_add(self._set_iphone_banner,
                      _("🔧 Restoring connection, please wait…"))
        reset_ok = False
        try:
            r = subprocess.run(
                ["pkexec", "sh", "-c",
                 "killall usbmuxd 2>/dev/null; sleep 0.5; usbmuxd"],
                capture_output=True, text=True, timeout=40
            )
            reset_ok = (r.returncode == 0)
            log_info(_("usbmuxd reset rc={rc}").format(rc=r.returncode))
        except Exception as e:
            log_error(_("usbmuxd reset error: {err}").format(err=e))
            reset_ok = False
        if not reset_ok:
            GLib.idle_add(self._iphone_flow_fail)
            return
        time.sleep(2.5)
        has_device = self._idevice_check()
        if has_device:
            log_info(_("iPhone recognised after reset"))
            GLib.idle_add(self._iphone_flow_success, True)
        else:
            log_warn(_("iPhone still unrecognised after reset"))
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
            _("✅ iPhone ready — tap Trust on your iPhone if asked")
            if was_reset else _("✅ iPhone connected")
        )
        GLib.timeout_add_seconds(4, self._clear_iphone_banner)
        return False

    def _iphone_flow_fail(self):
        self._recovery_prompt_active = False
        self._set_iphone_banner(
            _("⚠️ iPhone not recognised — try Settings > iPhone connection")
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
        # Don't poll while importer is open — interferes with libimobiledevice
        # pair/mount.
        try:
            if self.main_stack.get_visible_child_name() == "importer":
                return True
        except Exception:
            pass

        def check():
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
                _("iPhone or iPad detected — click to import")
            )
        else:
            ctx.remove_class("pixora-import-active")
            self.import_btn.set_tooltip_text(_("Import from iPhone or iPad"))
        return False

    def _prewarm_gstreamer(self):
        """Initialize GStreamer in background so first video opens fast."""
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
            # Cache-bust: raw.githubusercontent.com is Fastly-cached ~5 min
            # → unique query-string forces a fresh fetch.
            req = urllib.request.Request(
                f"https://raw.githubusercontent.com/Linux-Ginger/Pixora/main/version.txt?t={int(time.time())}",
                headers={
                    "User-Agent": "Pixora/1.0",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                remote_version = resp.read().decode().strip()
            if remote_version and remote_version != local_version:
                self._pending_update_version = remote_version
                GLib.idle_add(self._maybe_show_update_popup)
        except Exception:
            pass

    def _maybe_show_update_popup(self):
        """Defer update popup until home page has been visible ≥2s, else retry."""
        if self._update_dialog_shown or not self._pending_update_version:
            return False
        ready_at = getattr(self, "_home_ready_at", None)
        if ready_at is None or (time.time() - ready_at) < 2.0:
            GLib.timeout_add(500, self._maybe_show_update_popup)
            return False
        self._update_dialog_shown = True
        self._show_update_message_dialog(self._pending_update_version)
        return False

    def _show_update_message_dialog(self, new_version):
        dlg = Adw.AlertDialog(
            heading=_("Update available"),
            body=_("Pixora {v} is available. Update now?").format(v=new_version),
        )
        dlg.add_response("later", _("Later"))
        dlg.add_response("bijwerken", _("Update"))
        dlg.set_response_appearance("bijwerken", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", self._on_update_dialog_response, new_version)
        self._present_dialog(dlg)
        return False

    def _on_update_dialog_response(self, dlg, response, new_version):
        if response == "bijwerken":
            self._open_installer()
        else:
            self.update_banner.set_title(_("Update available: {v}").format(v=new_version))
            self.update_banner.set_revealed(True)

    def _on_update_banner_clicked(self, banner):
        self._open_installer()

    def _repair_desktop_entry(self):
        """Rewrite ~/.local/share/applications/pixora.desktop if its Icon=
        path is stale. Silent — a failure here must never block startup."""
        try:
            install_dir = os.path.expanduser("~/.local/share/pixora")
            icon = os.path.join(install_dir, "assets", "logos", "pixora-icon.svg")
            launcher = os.path.expanduser("~/.local/bin/pixora")
            desktop_dir = os.path.expanduser("~/.local/share/applications")
            desktop = os.path.join(desktop_dir, "pixora.desktop")
            if not os.path.exists(icon) or not os.path.exists(launcher):
                return  # running from dev-tree or not installed
            needs_write = True
            if os.path.exists(desktop):
                try:
                    with open(desktop) as _df:
                        content_existing = _df.read()
                    icon_ok = f"Icon={icon}" in content_existing and os.path.exists(icon)
                    wm_ok = "StartupWMClass=com.linuxginger.pixora" in content_existing
                    # StartupNotify=true triggers GNOME "Pixora is ready"
                    # notifications on every present(); must be absent.
                    startup_notify_ok = "StartupNotify=true" not in content_existing
                    if icon_ok and wm_ok and startup_notify_ok:
                        needs_write = False
                except Exception:
                    pass
            if not needs_write:
                return
            os.makedirs(desktop_dir, exist_ok=True)
            content = (
                "[Desktop Entry]\n"
                "Name=Pixora\n"
                "GenericName=Photo & Video Manager\n"
                "GenericName[nl]=Foto & Video Manager\n"
                "GenericName[de]=Foto- & Video-Manager\n"
                "GenericName[fr]=Gestionnaire de photos et vidéos\n"
                "Comment=Photo & video manager by LinuxGinger\n"
                "Comment[nl]=Foto & video manager door LinuxGinger\n"
                "Comment[de]=Foto- & Video-Manager von LinuxGinger\n"
                "Comment[fr]=Gestionnaire de photos et vidéos par LinuxGinger\n"
                f"Exec={launcher}\n"
                f"Icon={icon}\n"
                "Terminal=false\n"
                "Type=Application\n"
                "Categories=Graphics;Photography;\n"
                "StartupWMClass=com.linuxginger.pixora\n"
            )
            with open(desktop, "w") as _df:
                _df.write(content)
            try:
                subprocess.run(
                    ["update-desktop-database", desktop_dir],
                    capture_output=True, timeout=5,
                )
            except Exception:
                pass
            log_info(_("pixora.desktop repaired: Icon={p}").format(p=icon))
        except Exception:
            pass

    def _open_installer(self):
        log_info(_("GUI updater started"))
        updater_path = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "updater.py"
        ))
        try:
            subprocess.Popen([sys.executable, updater_path],
                             start_new_session=True)
        except Exception as e:
            log_error(_("GUI updater failed to start: {err}").format(err=e))
            return
        # Use on_close (with its 2s force-exit fallback) instead of app.quit()
        # — the latter leaves non-daemon threads + WebKit subprocess hanging,
        # keeping Pixora as a zombie that blocks future launches.
        GLib.idle_add(self.close)

    def _on_open_github(self, btn):
        # subprocess.Popen + DEVNULL + start_new_session — browsers spam
        # GTK warnings to stderr otherwise, and we don't want the browser
        # tied to Pixora's lifetime.
        try:
            subprocess.Popen(
                ["xdg-open", "https://github.com/Linux-Ginger/Pixora"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            log_warn(_("Failed to open GitHub: {err}").format(err=e))

    def _on_view_license(self, btn):
        """GPL-3.0 summary popup (✓ / ! / ✗) with full license text."""
        win = Adw.Window()
        win.set_title(_("License"))
        win.set_transient_for(self)
        win.set_modal(False)
        win.set_default_size(780, 760)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(Adw.HeaderBar())

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        body.set_margin_top(16)
        body.set_margin_bottom(16)
        body.set_margin_start(18)
        body.set_margin_end(18)

        heading = Gtk.Label(label=_("GNU General Public License v3.0"))
        heading.add_css_class("title-2")
        heading.set_halign(Gtk.Align.START)
        body.append(heading)

        intro = Gtk.Label(
            label=_("Pixora is free software under GPL-3.0. You may use, modify and share it — as long as you respect those same rights for others.")
        )
        intro.add_css_class("dim-label")
        intro.set_halign(Gtk.Align.START)
        intro.set_wrap(True)
        intro.set_xalign(0)
        body.append(intro)

        summary = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
            homogeneous=True,
        )
        summary.append(self._license_summary_col(
            _("Permitted"), "✓", "success",
            [
                _("Private and commercial use"),
                _("Modify the code"),
                _("Distribute (original or modified)"),
                _("Patent licenses from contributors"),
            ],
        ))
        summary.append(self._license_summary_col(
            _("Required"), "!", "warning",
            [
                _("Include source code when distributing"),
                _("Use the same GPL-3 license"),
                _("Mark modifications clearly"),
                _("Keep the copyright notice"),
            ],
        ))
        summary.append(self._license_summary_col(
            _("Not permitted"), "✗", "error",
            [
                _("Include in proprietary software"),
                _("Claim warranty (there is none)"),
                _("Hold authors liable"),
            ],
        ))
        body.append(summary)

        body.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        full_hdr = Gtk.Label(label=_("Full license text"))
        full_hdr.add_css_class("heading")
        full_hdr.set_halign(Gtk.Align.START)
        body.append(full_hdr)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_min_content_height(260)
        tv = Gtk.TextView()
        tv.set_editable(False)
        tv.set_cursor_visible(False)
        tv.set_monospace(True)
        tv.set_wrap_mode(Gtk.WrapMode.WORD)
        tv.set_left_margin(12)
        tv.set_right_margin(12)
        tv.set_top_margin(12)
        tv.set_bottom_margin(12)
        try:
            with open(LICENSE_PATH, "r", encoding="utf-8") as f:
                lic_text = f.read()
        except Exception as e:
            lic_text = _("Could not load license: {err}").format(err=e)
        tv.get_buffer().set_text(lic_text)
        scroll.set_child(tv)
        body.append(scroll)

        outer.append(body)
        win.set_content(outer)
        win.present()

    def _license_summary_col(self, title, icon_char, css_class, items):
        """icon_char is Unicode (✓ / ! / ✗) so it doesn't depend on icon-themes
        and still renders in monospace fallback."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        badge = Gtk.Label(label=icon_char)
        badge.add_css_class("heading")
        badge.add_css_class(css_class)
        header.append(badge)
        hlbl = Gtk.Label(label=title)
        hlbl.add_css_class("heading")
        hlbl.set_halign(Gtk.Align.START)
        header.append(hlbl)
        box.append(header)
        for item in items:
            lbl = Gtk.Label(label="• " + item)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_wrap(True)
            lbl.set_xalign(0)
            lbl.add_css_class("caption")
            box.append(lbl)
        return box

    def _on_settings_check_update(self, btn):
        if self._update_check_state == "available":
            self._open_installer()
            return
        self._set_update_state("checking")
        threading.Thread(target=self._do_settings_update_check, daemon=True).start()

    def _set_update_state(self, state):
        """state ∈ {'idle', 'checking', 'uptodate', 'available'}."""
        self._update_check_state = state
        if self._update_check_pulse_id is not None:
            try:
                GLib.source_remove(self._update_check_pulse_id)
            except Exception:
                pass
            self._update_check_pulse_id = None
        if self._update_check_fade_id is not None:
            try:
                GLib.source_remove(self._update_check_fade_id)
            except Exception:
                pass
            self._update_check_fade_id = None

        try:
            if state == "idle":
                self._update_btn_stack.set_visible_child_name("idle")
                self._update_check_btn.set_sensitive(True)
                self._update_check_btn.set_opacity(1.0)
                self._update_check_spinner.stop()
            elif state == "checking":
                self._update_btn_stack.set_visible_child_name("checking")
                self._update_check_spinner.start()
                self._update_check_btn.set_sensitive(False)
            elif state == "uptodate":
                self._update_btn_stack.set_visible_child_name("uptodate")
                self._update_check_spinner.stop()
                self._update_check_btn.set_sensitive(True)
                self._update_check_btn.set_opacity(1.0)
                self._update_check_fade_id = GLib.timeout_add_seconds(
                    5, self._update_uptodate_fade_done
                )
            elif state == "available":
                self._update_check_spinner.stop()
                self._update_check_btn.set_sensitive(True)
                self._update_check_btn.set_opacity(1.0)
                self._update_check_btn.set_tooltip_text(
                    _("New version available — click to update")
                )
                self._update_btn_stack.set_visible_child_name("available")
                # Pulse: alternate icon/label every 1.5s.
                self._update_pulse_on = True
                self._update_check_pulse_id = GLib.timeout_add(
                    1500, self._update_pulse_tick
                )
        except Exception:
            pass

    def _update_uptodate_fade_done(self):
        self._update_check_fade_id = None
        if self._update_check_state == "uptodate":
            self._set_update_state("idle")
        return False

    def _update_pulse_tick(self):
        if self._update_check_state != "available":
            self._update_check_pulse_id = None
            return False
        # Stop once settings dialog closed — calling set_visible_child_name on
        # a widget without a root raises Gtk-CRITICAL.
        try:
            if self._update_check_btn.get_root() is None:
                self._update_check_pulse_id = None
                return False
        except Exception:
            self._update_check_pulse_id = None
            return False
        self._update_pulse_on = not self._update_pulse_on
        try:
            self._update_btn_stack.set_visible_child_name(
                "available" if self._update_pulse_on else "available_label"
            )
        except Exception:
            self._update_check_pulse_id = None
            return False
        return True

    def _do_settings_update_check(self):
        try:
            local_version_file = os.path.join(
                os.path.expanduser("~"), ".config", "pixora", "installed_version"
            )
            local_version = ""
            if os.path.exists(local_version_file):
                with open(local_version_file) as _lvf:
                    local_version = _lvf.read().strip()
            # Cache-bust: see _do_update_check.
            req = urllib.request.Request(
                f"https://raw.githubusercontent.com/Linux-Ginger/Pixora/main/version.txt?t={int(time.time())}",
                headers={
                    "User-Agent": "Pixora/1.0",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                remote_version = resp.read().decode().strip()
        except Exception:
            GLib.idle_add(self._settings_update_result, None, None)
            return
        GLib.idle_add(self._settings_update_result, local_version, remote_version)

    def _settings_update_result(self, local_version, remote_version):
        # Dialog may have closed meanwhile → widgets disposed.
        try:
            if remote_version is None:
                self._update_check_row.set_subtitle(_("Check failed"))
                self._set_update_state("idle")
                return False
            self._update_remote_version = remote_version
            if local_version == remote_version:
                self._update_check_row.set_subtitle(_("You have the latest version"))
                self._set_update_state("uptodate")
            else:
                self._update_check_row.set_subtitle(
                    _("Version {v} available").format(v=remote_version)
                )
                self._set_update_state("available")
        except Exception:
            pass
        return False

    def is_dark(self):
        return self.style_manager.get_dark()

    def on_dark_mode_changed(self, manager, _pspec):
        logo_path = get_logo_path(self.is_dark())
        if os.path.exists(logo_path):
            self.logo_picture.set_filename(logo_path)

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
            log_error(_("Favorites save error: {err}").format(err=e))
        return False

    def on_close(self, window):
        # An open settings dialog blocks the main window's close-request
        # ("Close N windows" from GNOME Shell) — close it first.
        if self._settings_dialog is not None:
            try:
                self._settings_dialog.close()
            except Exception:
                pass
        # Guard against closing during backup/reorganize — ask to confirm.
        if not getattr(self, "_close_confirmed", False):
            if self._backup_running:
                body = _("Pixora is backing up to your USB drive. Closing now will interrupt the backup.")
            elif self._reorganize_moving:
                body = _("Pixora is cleaning up the folder structure. Closing now may leave photos half-moved.")
            else:
                body = None
            if body is not None:
                dlg = Adw.AlertDialog(
                    heading=_("Quit Pixora?"),
                    body=body,
                )
                dlg.add_response("cancel", _("Cancel"))
                dlg.add_response("close", _("Close anyway"))
                dlg.set_response_appearance(
                    "close", Adw.ResponseAppearance.DESTRUCTIVE
                )
                dlg.set_default_response("cancel")
                dlg.connect("response", self._on_close_guard_response)
                self._present_dialog(dlg)
                return True  # cancel close; dialog decides
        log_info(_("Pixora shutting down — cleaning up…"))
        # Kill rsync cleanly so the backup actually stops instead of hanging.
        if self._backup_running and self._backup_proc is not None:
            try:
                self._backup_proc.kill()
            except Exception:
                pass
        try:
            self._load_id += 1
            self._viewer_load_id += 1
            self._filmstrip_load_id += 1
        except Exception:
            pass
        # Cancel ALL GLib timers BEFORE tearing down state — otherwise a timer
        # firing mid-cleanup crashes on a None / missing attribute.
        for attr in ("_favorites_save_id", "_sort_timer", "_fade_timer_id",
                     "_fade_anim_id", "_preview_debounce_id",
                     "_video_seek_pending_id", "_video_poll_id",
                     "_thumb_size_timer", "_map_ready_fallback_id",
                     "_nav_debounce_id"):
            tid = getattr(self, attr, None)
            if tid is not None:
                try:
                    GLib.source_remove(tid)
                except Exception:
                    pass
                setattr(self, attr, None)
        try:
            save_favorites(self._favorites)
        except Exception:
            pass
        try:
            save_metadata_cache()
        except Exception:
            pass
        self.stop_watcher()
        try:
            self._udev_client = None
        except Exception:
            pass
        # Stop video + MediaFile explicitly so GStreamer threads release.
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
        # Release big structures so RAM frees promptly.
        try:
            self.photos = []
            self._filmstrip_order_cache = None
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
            log_error(_("Cleanup error: {err}").format(err=e))
        try:
            import gc
            gc.collect()
        except Exception:
            pass
        try:
            import main as _main_mod
            _main_mod.kill_dev_terminal()
        except Exception:
            pass
        # Truncate dev log so the next session starts fresh.
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
        # Force exit after 2s — lingering non-daemon threads (GStreamer, PIL,
        # gvfs-workers) otherwise keep the process in memory.
        def _force_exit():
            try:
                print(_("Pixora process forcing exit (lingering threads)"), flush=True)
            except Exception:
                pass
            os._exit(0)
        threading.Timer(2.0, _force_exit).start()
        return False

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
        for item in [_("Date (newest first)"), _("Date (oldest first)")]:
            self.sort_model.append(item)

        self.sort_combo = Gtk.DropDown(model=self.sort_model)
        self.sort_combo.set_size_request(180, -1)
        saved_sort = self.settings.get("sort_index", 0)
        if saved_sort not in (0, 1):
            saved_sort = 0
        self.sort_combo.set_selected(saved_sort)
        self.sort_combo.connect("notify::selected", self.on_sort_changed)
        self.header.pack_start(self.sort_combo)

        self.favorites_toggle = Gtk.ToggleButton()
        self.favorites_toggle.set_icon_name("starred-symbolic")
        self.favorites_toggle.add_css_class("flat")
        self.favorites_toggle.set_tooltip_text(_("Show only favorites"))
        self.favorites_toggle.connect("toggled", self.toggle_favorites_filter)
        self.header.pack_end(self.favorites_toggle)

        self.map_btn = Gtk.Button(label=_("🗺"))
        self.map_btn.add_css_class("flat")
        self.map_btn.set_tooltip_text(_("Map view"))
        self.map_btn.connect("clicked", self.open_map)
        self.header.pack_end(self.map_btn)

        self.import_btn = Gtk.Button(icon_name="phone-symbolic")
        self.import_btn.add_css_class("flat")
        self.import_btn.set_tooltip_text(_("Import from iPhone or iPad"))
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

        self.select_btn = Gtk.Button(label=_("Select"))
        self.select_btn.add_css_class("flat")
        self.select_btn.connect("clicked", self.toggle_select_mode)
        self.header.pack_end(self.select_btn)

        self.settings_btn = Gtk.Button(icon_name="preferences-system-symbolic")
        self.settings_btn.add_css_class("flat")
        self.settings_btn.set_tooltip_text(_("Settings"))
        self.settings_btn.connect("clicked", self.on_settings_clicked)
        self.header.pack_end(self.settings_btn)

        # Backup-progress donut — hidden until backup is active. Wrapped in
        # a Gtk.Button so hover/click works and opens the details popover.
        self._backup_donut = Gtk.DrawingArea()
        self._backup_donut.set_size_request(24, 24)
        self._backup_donut.set_valign(Gtk.Align.CENTER)
        self._backup_donut.set_draw_func(self._draw_backup_donut)
        self._backup_donut_btn = Gtk.Button()
        self._backup_donut_btn.add_css_class("flat")
        self._backup_donut_btn.add_css_class("circular")
        self._backup_donut_btn.set_child(self._backup_donut)
        self._backup_donut_btn.set_tooltip_text(_("Backup in progress"))
        self._backup_donut_btn.set_visible(False)
        self._backup_donut_btn.connect("clicked", self._on_backup_donut_clicked)
        self.header.pack_end(self._backup_donut_btn)

        return self.header

    def build_grid_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_vexpand(True)
        outer.set_hexpand(True)

        # Filter-info banner (visible when a cluster filter is active)
        self.filter_info_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.filter_info_bar.add_css_class("toolbar")
        self.filter_info_bar.set_margin_top(8)
        self.filter_info_bar.set_margin_start(12)
        self.filter_info_bar.set_margin_end(12)
        self.filter_info_bar.set_margin_bottom(4)
        self.filter_info_bar.set_visible(False)

        _filter_icon = Gtk.Label(label="📍")
        _filter_icon.set_valign(Gtk.Align.CENTER)
        _filter_icon_css = Gtk.CssProvider()
        _filter_icon_css.load_from_string("label { font-size: 20px; }")
        _filter_icon.get_style_context().add_provider(
            _filter_icon_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.filter_info_bar.append(_filter_icon)

        _text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        _text_box.set_hexpand(True)
        _text_box.set_valign(Gtk.Align.CENTER)
        self.filter_title_lbl = Gtk.Label(label="")
        self.filter_title_lbl.add_css_class("heading")
        self.filter_title_lbl.set_halign(Gtk.Align.START)
        self.filter_title_lbl.set_xalign(0)
        _text_box.append(self.filter_title_lbl)
        self.filter_subtitle_lbl = Gtk.Label(label="")
        self.filter_subtitle_lbl.add_css_class("caption")
        self.filter_subtitle_lbl.add_css_class("dim-label")
        self.filter_subtitle_lbl.set_halign(Gtk.Align.START)
        _text_box.append(self.filter_subtitle_lbl)
        self.filter_info_bar.append(_text_box)

        _clear_btn = Gtk.Button(icon_name="window-close-symbolic")
        _clear_btn.add_css_class("circular")
        _clear_btn.add_css_class("flat")
        _clear_btn.set_valign(Gtk.Align.CENTER)
        _clear_btn.set_tooltip_text(_("Clear filter"))
        _clear_btn.connect("clicked", self.on_clear_cluster_filter)
        self.filter_info_bar.append(_clear_btn)

        outer.append(self.filter_info_bar)

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
        self.spinner_label = Gtk.Label(label=_("Loading photos..."))
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
        status_page.set_title(_("No photos found"))
        status_page.set_description(_("Connect your iPhone or iPad to import photos"))
        status_page.set_vexpand(True)
        status_page.set_hexpand(True)
        self.content_stack.add_named(status_page, "empty")

        self.content_stack.set_visible_child_name("loading")
        outer.append(self.content_stack)
        return outer

    def build_map_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_vexpand(True)
        box.set_hexpand(True)

        map_header = Adw.HeaderBar()
        map_header.add_css_class("flat")

        back_btn = Gtk.Button(icon_name="go-previous-symbolic")
        back_btn.add_css_class("flat")
        back_btn.set_tooltip_text(_("Back"))
        back_btn.connect("clicked", self.close_map)
        map_header.pack_start(back_btn)

        self.map_title_label = Gtk.Label(label=_("Map view"))
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
        self.map_spinner_label = Gtk.Label(label=_("Compiling travel story…"))
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
        log_info(_("Map opened ({n} photos going to GPS scan)").format(n=len(self.photos)))
        self.header.set_visible(False)
        self.bottom_stack.set_visible(False)
        self._set_toolbars_revealed(False)
        self.map_btn.set_label(_("🗺 loading..."))
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
                if mtime > 0 and mtime >= 946684800:
                    dt = datetime.datetime.fromtimestamp(mtime)
                    datum = f"{dt.day} {_(_MONTH_KEYS[dt.month])} {dt.year}"
                else:
                    datum = ""
            except Exception:
                datum = ""
            return (lat, lon, filename, datum, path)

        markers = []
        seen_paths = set()
        # Live Photos are HEIC+MOV pairs with identical GPS — dedup on
        # (lat, lon, stem) so one "photo moment" doesn't count twice.
        grouped = {}  # key → (lat, lon, filename, datum, [paths])
        with ThreadPoolExecutor(max_workers=8) as pool:
            for result in pool.map(scan_one, self.photos):
                if result is None:
                    continue
                lat, lon, filename, datum, path = result
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                stem = os.path.splitext(filename)[0].lower()
                # 6-decimal round (~10cm) matches Live Photo pairs exactly.
                key = (round(lat, 6), round(lon, 6), stem)
                if key in grouped:
                    grouped[key][4].append(path)
                else:
                    grouped[key] = (lat, lon, filename, datum, [path])

        for (lat, lon, filename, datum, paths) in grouped.values():
            markers.append((lat, lon, filename, datum, paths[0]))
        log_info(
            _("Map: {m} markers from {p} photos (GPS), out of {t} total").format(
                m=len(markers), p=len(seen_paths), t=len(self.photos)
            )
        )
        GLib.idle_add(self._show_map, markers)

    def _show_map(self, markers):
        if self._map_widget:
            self.map_content.remove(self._map_widget)
            self._map_widget = None

        try:
            self.map_spinner_label.set_text(_("Connecting to map server…"))
        except Exception:
            pass

        self._map_widget = MapWidget(
            markers, self._open_photo_from_map,
            status_cb=self._on_map_status
        )
        self.map_content.append(self._map_widget)
        # Wait for map-ready status callback; 12s fallback prevents hang.
        self._map_ready_fallback_id = GLib.timeout_add_seconds(
            12, self._on_map_ready_timeout
        )
        self.map_title_label.set_text(_("Map view"))
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
            # Show the map anyway — map.html's JS banner explains the tile
            # failure, and markers remain visible.
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
        log_warn(_("Map-ready timeout — showing map anyway"))
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
            # Cluster click: filter grid to those photos.
            self._photos_before_cluster = self.photos
            self.photos = valid
            self._filmstrip_order_cache = None
            # Compute date range for the info banner.
            date_range = ""
            try:
                dates = []
                for p in valid:
                    try:
                        mt = os.path.getmtime(p)
                        # Filter epoch-0 / pre-2000 mtimes to avoid "1 Jan 1970".
                        if mt > 0 and mt >= 946684800:
                            dates.append(mt)
                    except Exception:
                        pass
                if dates:
                    dts = [datetime.datetime.fromtimestamp(t) for t in dates]
                    earliest = min(dts)
                    latest = max(dts)
                    e_str = f"{earliest.day} {_(_MONTH_KEYS[earliest.month])} {earliest.year}"
                    l_str = f"{latest.day} {_(_MONTH_KEYS[latest.month])} {latest.year}"
                    if e_str == l_str:
                        date_range = e_str
                    else:
                        date_range = f"{e_str} – {l_str}"
            except Exception:
                pass

            loc = self._photo_location.get(valid[0], "")
            if not loc:
                # Not yet cached → trigger async lookup so the banner may
                # get the resolved location later.
                threading.Thread(
                    target=self._fetch_cluster_location,
                    args=(valid[0],),
                    daemon=True,
                ).start()

            title = loc if loc else _("Filtered location")
            count_str = ngettext("%d photo", "%d photos", len(valid)) % len(valid)
            subtitle = count_str if not date_range else f"{count_str} · {date_range}"
            self.filter_title_lbl.set_text(title)
            self.filter_subtitle_lbl.set_text(subtitle)
            self.filter_info_bar.set_visible(True)

            self._cluster_location_label = title
            self.photo_count_label.set_text(count_str)
            GLib.idle_add(self.start_load)

    def _fetch_cluster_location(self, sample_path):
        """Async reverse-geocode for the cluster filter label."""
        try:
            if is_video(sample_path):
                coords = get_video_gps_coords(sample_path)
            else:
                coords = get_gps_coords(sample_path)
            if not coords:
                return
            loc = reverse_geocode(coords[0], coords[1])
            if loc:
                self._photo_location[sample_path] = loc
                GLib.idle_add(self._apply_cluster_location, loc)
        except Exception:
            pass

    def _apply_cluster_location(self, loc):
        # Only apply while the filter bar is still visible (user may have ✕'d).
        try:
            if self.filter_info_bar.get_visible():
                self.filter_title_lbl.set_text(loc)
                self._cluster_location_label = loc
        except Exception:
            pass
        return False

    def on_clear_cluster_filter(self, btn=None):
        if not hasattr(self, "_photos_before_cluster") or not self._photos_before_cluster:
            return
        log_info(_("Cluster filter off → all photos"))
        self.photos = self._photos_before_cluster
        self._filmstrip_order_cache = None
        self._photos_before_cluster = None
        self._cluster_location_label = None
        n = len(self.photos)
        self.photo_count_label.set_text(ngettext("%d photo", "%d photos", n) % n)
        try:
            self.filter_info_bar.set_visible(False)
        except Exception:
            pass
        GLib.idle_add(self.start_load)

    def close_map(self, btn=None):
        log_info(_("Map closed"))
        if getattr(self, "_map_ready_fallback_id", None):
            try:
                GLib.source_remove(self._map_ready_fallback_id)
            except Exception:
                pass
            self._map_ready_fallback_id = None
        self.header.set_visible(True)
        self.bottom_stack.set_visible(True)
        self._set_toolbars_revealed(True)
        self.main_stack.set_visible_child_name("grid")

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
        self.viewer_close_btn.set_tooltip_text(_("Close"))
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
        self.viewer_delete_btn.set_tooltip_text(_("Delete"))
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
        self.edit_btn.set_tooltip_text(_("Edit photo"))
        self.edit_btn.connect("clicked", self.on_edit_current)
        viewer_area.add_overlay(self.edit_btn)

        # Viewer-overlay donut mirrors the header donut so backup progress is
        # visible while viewing photos (header is hidden then). Shares
        # _draw_backup_donut and state so both stay in sync.
        self._viewer_donut = Gtk.DrawingArea()
        self._viewer_donut.set_size_request(24, 24)
        self._viewer_donut.set_draw_func(self._draw_backup_donut)
        self._viewer_donut_btn = Gtk.Button()
        self._viewer_donut_btn.add_css_class("osd")
        self._viewer_donut_btn.add_css_class("circular")
        self._viewer_donut_btn.set_child(self._viewer_donut)
        self._viewer_donut_btn.set_halign(Gtk.Align.END)
        self._viewer_donut_btn.set_valign(Gtk.Align.START)
        # Positioned below the delete button (margin_end=68, top=16+40+8).
        self._viewer_donut_btn.set_margin_top(64)
        self._viewer_donut_btn.set_margin_end(68)
        self._viewer_donut_btn.set_size_request(40, 40)
        self._viewer_donut_btn.set_visible(False)
        self._viewer_donut_btn.connect("clicked", self._on_backup_donut_clicked)
        viewer_area.add_overlay(self._viewer_donut_btn)

        self.favorite_btn = Gtk.Button(icon_name="non-starred-symbolic")
        self.favorite_btn.add_css_class("osd")
        self.favorite_btn.add_css_class("circular")
        self.favorite_btn.set_halign(Gtk.Align.END)
        self.favorite_btn.set_valign(Gtk.Align.START)
        self.favorite_btn.set_margin_top(16)
        self.favorite_btn.set_margin_end(172)
        self.favorite_btn.set_size_request(40, 40)
        self.favorite_btn.set_tooltip_text(_("Mark as favorite"))
        self.favorite_btn.connect("clicked", self.on_toggle_favorite)
        self._favorite_css = Gtk.CssProvider()
        self._favorite_css.load_from_string(
            "button.pixora-fav { color: #e95420; }"
        )
        self.favorite_btn.get_style_context().add_provider(
            self._favorite_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        viewer_area.add_overlay(self.favorite_btn)

        # Editor toolbar (hidden until editor mode).
        self.editor_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.editor_bar.set_halign(Gtk.Align.CENTER)
        self.editor_bar.set_valign(Gtk.Align.END)
        self.editor_bar.set_margin_bottom(FILM_THUMB + 12 + 16)
        self.editor_bar.set_visible(False)

        rot_left_btn = Gtk.Button(icon_name="object-rotate-left-symbolic")
        rot_left_btn.add_css_class("osd")
        rot_left_btn.add_css_class("circular")
        rot_left_btn.set_size_request(48, 48)
        rot_left_btn.set_tooltip_text(_("Rotate left"))
        rot_left_btn.connect("clicked", self.on_editor_rotate_left)
        self.editor_bar.append(rot_left_btn)

        rot_right_btn = Gtk.Button(icon_name="object-rotate-right-symbolic")
        rot_right_btn.add_css_class("osd")
        rot_right_btn.add_css_class("circular")
        rot_right_btn.set_size_request(48, 48)
        rot_right_btn.set_tooltip_text(_("Rotate right"))
        rot_right_btn.connect("clicked", self.on_editor_rotate_right)
        self.editor_bar.append(rot_right_btn)

        self.crop_toggle_btn = Gtk.ToggleButton(label="✂")
        self.crop_toggle_btn.add_css_class("osd")
        self.crop_toggle_btn.add_css_class("circular")
        self.crop_toggle_btn.set_size_request(48, 48)
        self.crop_toggle_btn.set_tooltip_text(_("Crop"))
        self.crop_toggle_btn.connect("toggled", self.on_editor_toggle_crop)
        self.editor_bar.append(self.crop_toggle_btn)

        save_btn = Gtk.Button(icon_name="document-save-symbolic")
        save_btn.add_css_class("osd")
        save_btn.add_css_class("circular")
        save_btn.add_css_class("suggested-action")
        save_btn.set_size_request(48, 48)
        save_btn.set_tooltip_text(_("Save"))
        save_btn.connect("clicked", self.on_editor_save)
        self.editor_bar.append(save_btn)

        cancel_editor_btn = Gtk.Button(icon_name="window-close-symbolic")
        cancel_editor_btn.add_css_class("osd")
        cancel_editor_btn.add_css_class("circular")
        cancel_editor_btn.set_size_request(48, 48)
        cancel_editor_btn.set_tooltip_text(_("Cancel"))
        cancel_editor_btn.connect("clicked", self.on_editor_cancel)
        self.editor_bar.append(cancel_editor_btn)

        viewer_area.add_overlay(self.editor_bar)

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

        self.viewer_location_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        self.viewer_location_box.set_halign(Gtk.Align.START)
        self.viewer_location_spinner = Gtk.Spinner()
        self.viewer_location_spinner.set_visible(False)
        self.viewer_location_spinner.set_valign(Gtk.Align.CENTER)
        self.viewer_location_box.append(self.viewer_location_spinner)
        self.viewer_location = Gtk.Label(label="")
        self.viewer_location.add_css_class("dim-label")
        self.viewer_location.set_halign(Gtk.Align.START)
        self.viewer_location_box.append(self.viewer_location)
        self.viewer_title_box.append(self.viewer_location_box)

        viewer_area.add_overlay(self.viewer_title_box)

        self.prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.prev_btn.add_css_class("osd")
        self.prev_btn.add_css_class("circular")
        self.prev_btn.set_halign(Gtk.Align.START)
        self.prev_btn.set_valign(Gtk.Align.CENTER)
        self.prev_btn.set_margin_start(16)
        self.prev_btn.set_margin_bottom(105)
        self.prev_btn.set_size_request(48, 48)
        self.prev_btn.set_tooltip_text(_("Previous"))
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
        self.next_btn.set_tooltip_text(_("Next"))
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
        # halign=START so a narrow filmstrip (small library) doesn't center
        # inside the viewport — thumb 1 stays pinned to the left edge.
        self.filmstrip_area.set_halign(Gtk.Align.START)

        film_click = Gtk.GestureClick()
        film_click.connect("pressed", self._on_filmstrip_click)
        self.filmstrip_area.add_controller(film_click)

        self.filmstrip_scroll.set_child(self.filmstrip_area)
        viewer_area.add_overlay(self.filmstrip_scroll)

        self.video_spinner = Gtk.Spinner()
        self.video_spinner.set_size_request(64, 64)
        self.video_spinner.set_halign(Gtk.Align.CENTER)
        self.video_spinner.set_valign(Gtk.Align.CENTER)
        self.video_spinner.set_visible(False)
        viewer_area.add_overlay(self.video_spinner)

        return viewer_area

    def build_bottombar(self):
        self.bottom_stack = Gtk.Stack()
        self.bottom_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.bottom_stack.set_transition_duration(150)

        normal_bar = Gtk.ActionBar()
        self.photo_count_label = Gtk.Label(label=ngettext("%d photo", "%d photos", 0) % 0)
        self.photo_count_label.add_css_class("dim-label")
        normal_bar.pack_start(self.photo_count_label)

        self.bottom_stack.add_named(normal_bar, "normal")

        select_bar = Gtk.ActionBar()
        self.select_count_label = Gtk.Label(label=ngettext("%d selected", "%d selected", 0) % 0)
        self.select_count_label.add_css_class("dim-label")
        select_bar.pack_start(self.select_count_label)

        delete_selected_btn = Gtk.Button(label=_("Delete"))
        delete_selected_btn.add_css_class("destructive-action")
        delete_selected_btn.add_css_class("pill")
        delete_selected_btn.connect("clicked", self.on_delete_selected)
        select_bar.pack_end(delete_selected_btn)

        self.bottom_stack.add_named(select_bar, "select")
        self.bottom_stack.set_visible_child_name("normal")

        return self.bottom_stack

    def toggle_select_mode(self, btn=None):
        self._select_mode = not self._select_mode
        log_info(_("Selection mode: {state}").format(
            state=_("on") if self._select_mode else _("off")
        ))
        self._selected.clear()
        if self._select_mode:
            self.select_btn.set_label(_("Cancel"))
            # .flat keeps the bg transparent; combined with .suggested-action
            # that paints text white, text becomes white-on-transparent.
            # Drop .flat while suggested is active.
            self.select_btn.remove_css_class("flat")
            self.select_btn.add_css_class("suggested-action")
            self.bottom_stack.set_visible_child_name("select")
            self.select_count_label.set_text(ngettext("%d selected", "%d selected", 0) % 0)
        else:
            self.select_btn.set_label(_("Select"))
            self.select_btn.remove_css_class("suggested-action")
            self.select_btn.add_css_class("flat")
            self.bottom_stack.set_visible_child_name("normal")
            self._update_all_selection_visuals()

    def _update_all_selection_visuals(self):
        for index, widget in self.thumb_widgets.items():
            self._update_thumb_visual(index, widget)

    def _update_thumb_visual(self, index, widget):
        btn, check_box = widget
        selected = index in self._selected
        if check_box is None and selected:
            if not hasattr(self, '_thumb_css'):
                return
            tc = self._thumb_css
            overlay = btn.get_child()
            if not isinstance(overlay, Gtk.Overlay):
                # Wrap the picture in an overlay so we can add check_box.
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

    def load_photos(self):
        photo_path = self.settings.get("photo_path", "")
        log_info(_("load_photos: scanning in {p}").format(p=photo_path))
        if not photo_path or not os.path.exists(photo_path):
            log_warn(_("load_photos: photo_path empty or missing — empty state"))
            self.show_empty_state()
            return False
        # os.walk over 2000+ files freezes the splash spinner if it runs on
        # the main loop — push it to a thread and finish on idle_add.
        self.content_stack.set_visible_child_name("loading")
        self.spinner.start()
        self.spinner_label.set_text(_("Sorting photos…"))

        def _scan_bg():
            photos = []
            for root, dirs, files in os.walk(photo_path):
                for file in files:
                    if os.path.splitext(file)[1].lower() in IMAGE_EXTENSIONS:
                        photos.append(os.path.join(root, file))
            GLib.idle_add(self._on_photos_scanned, photos, photo_path)

        threading.Thread(target=_scan_bg, daemon=True).start()
        return False

    def _on_photos_scanned(self, photos, photo_path):
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
        self._filmstrip_order_cache = None
        n = len(self.photos)
        count_text = ngettext("%d photo", "%d photos", n) % n
        if self._favorites_only:
            count_text = _("{count} (favorites)").format(count=count_text)
        self.photo_count_label.set_text(count_text)
        self.start_watcher(photo_path)
        threading.Thread(target=self._sort_then_load, daemon=True).start()
        return False

    def _sort_then_load(self):
        sort_idx = (self.sort_combo.get_selected()
                    if hasattr(self, "sort_combo") else 0)
        photos = list(self.photos)
        if photos:
            with ThreadPoolExecutor(max_workers=4) as pool:
                dates = list(pool.map(get_photo_date, photos))
            date_map = dict(zip(photos, dates))
            photos.sort(key=date_map.get, reverse=(sort_idx == 0))
        self.photos = photos
        self._filmstrip_order_cache = None
        GLib.idle_add(self.start_load)

    def _show_empty_favorites(self):
        self.spinner.stop()
        self.content_stack.set_visible_child_name("empty")
        self.photo_count_label.set_text(ngettext("%d favorite", "%d favorites", 0) % 0)
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
        self.spinner_label.set_text(
            _("Loading photos… {loaded} / {total}").format(loaded=0, total=len(self.photos))
        )
        thread = threading.Thread(
            target=self._load_thread,
            args=(load_id, list(self.photos)),
            daemon=True
        )
        thread.start()

    def _group_by_date(self, photos):
        groups = defaultdict(list)
        UNKNOWN = datetime.date.min  # sentinel for bogus dates
        # Must use the same date source as apply_sort / filmstrip (EXIF then
        # mtime). Mismatch would desync grid position with self.photos index,
        # breaking the viewer counter.
        for i, path in enumerate(photos):
            try:
                ts = get_photo_date(path)
                if ts <= 0 or ts < 946684800:  # < Jan 1 2000 = bogus
                    dt = UNKNOWN
                else:
                    dt = datetime.datetime.fromtimestamp(ts).date()
            except Exception:
                dt = UNKNOWN
            groups[dt].append(i)
        sort_idx = (self.sort_combo.get_selected()
                    if hasattr(self, "sort_combo") else 0)
        oldest_first = (sort_idx == 1)
        sorted_dates = sorted(groups.keys(), reverse=not oldest_first)

        def _header(dt):
            if dt == UNKNOWN:
                return _("Unknown date")
            return format_date_header(dt)

        return [(_header(dt), dt, groups[dt]) for dt in sorted_dates]

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
        GLib.timeout_add(150, self._run_viewport_hydrate)

    def _run_viewport_hydrate(self):
        self._viewport_hydrate_pending = False
        try:
            self._hydrate_viewport()
        except Exception as e:
            log_error(_("viewport hydrate error: {err}").format(err=e))
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
        # thumb_widgets insertion-order matches grid top-to-bottom, so we can
        # bail out after the visible range instead of checking all 2000 thumbs.
        seen_visible = False
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
                seen_visible = True
            elif seen_visible and btn_top > v_page + buffer_px:
                break
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
        self.spinner_label.set_text(
            _("Loading photos… {loaded} / {total}").format(loaded=loaded, total=total)
        )
        # Shared CSS providers — created once, reused per thumbnail.
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
            # check_box lazily created when selection mode enters.
            self.thumb_widgets[index] = (btn, None)
        self._schedule_viewport_hydrate()
        return False

    def _load_done(self, load_id, total):
        if load_id != self._load_id:
            return False
        log_info(_("Thumbnail load done: {n} photos — UI ready").format(n=total))
        self.spinner.stop()
        self.content_stack.set_visible_child_name("grid")
        if self._home_ready_at is None:
            self._home_ready_at = time.time()
            GLib.timeout_add(500, self._maybe_check_structure_on_startup)
        cluster_lbl = getattr(self, '_cluster_location_label', None)
        self.photo_count_label.set_text(
            cluster_lbl if cluster_lbl
            else ngettext("%d photo", "%d photos", total) % total
        )
        self._loading = False
        GLib.timeout_add(120, self._run_viewport_hydrate)
        return False

    def show_empty_state(self):
        self.spinner.stop()
        self.content_stack.set_visible_child_name("empty")
        self.photo_count_label.set_text(ngettext("%d photo", "%d photos", 0) % 0)
        self._loading = False

    def on_thumb_clicked(self, index):
        path = self.photos[index] if 0 <= index < len(self.photos) else "?"
        if self._select_mode:
            action = _("deselect") if index in self._selected else _("select")
            log_info(_("Thumbnail {action}: idx={i} path={p}").format(action=action, i=index, p=path))
            if index in self._selected:
                self._selected.discard(index)
            else:
                self._selected.add(index)
            self._update_thumb_visual(index, self.thumb_widgets[index])
            n = len(self._selected)
            self.select_count_label.set_text(ngettext("%d selected", "%d selected", n) % n)
        else:
            log_info(_("Thumbnail clicked → open photo: idx={i} path={p}").format(i=index, p=path))
            self.open_photo(index)

    def on_sort_changed(self, combo, _pspec):
        idx = combo.get_selected()
        options = [_("Date newest"), _("Date oldest")]
        log_info(_("Sorting changed: {opt}").format(
            opt=options[idx] if idx < len(options) else idx
        ))
        self.settings["sort_index"] = idx
        try:
            save_settings(self.settings)
        except Exception as e:
            log_info(_("Failed to save sort_index: {e}").format(e=e))
        if not self.photos:
            return
        # Show the loading state instantly so the user never stares at a
        # frozen grid during the debounce + background sort.
        self.content_stack.set_visible_child_name("loading")
        self.spinner.start()
        self.spinner_label.set_text(_("Sorting..."))
        if self._sort_timer:
            GLib.source_remove(self._sort_timer)
        self._sort_timer = GLib.timeout_add(400, self._do_sort)

    def _do_sort(self):
        self._sort_timer = None
        sort_index = self.sort_combo.get_selected()
        threading.Thread(target=self._do_sort_bg, args=(sort_index,), daemon=True).start()
        return False

    def _do_sort_bg(self, sort_index):
        photos = list(self.photos)
        if photos:
            with ThreadPoolExecutor(max_workers=4) as pool:
                dates = list(pool.map(get_photo_date, photos))
            date_map = dict(zip(photos, dates))
            photos.sort(key=date_map.get, reverse=(sort_index == 0))
        self.photos = photos
        self._filmstrip_order_cache = None
        GLib.idle_add(self.start_load)

    def open_photo(self, index):
        if not self.photos or not (0 <= index < len(self.photos)):
            return
        path = self.photos[index]
        kind = _("video") if is_video(path) else _("photo")
        log_info(_("open_photo: {kind} idx={i} path={p}").format(kind=kind, i=index, p=path))
        self.current_index = index
        self.header.set_visible(False)
        self.bottom_stack.set_visible(False)
        self._set_toolbars_revealed(False)
        self._stop_video()
        self.photo_picture.set_pixbuf(None)
        self._set_viewer_location("empty")
        self.main_stack.set_visible_child_name("viewer")
        self._filmstrip_thumbs = {}
        GLib.idle_add(self._update_filmstrip)
        GLib.timeout_add(80, self._scroll_filmstrip_to_current)
        self._viewer_load_id += 1
        load_id = self._viewer_load_id
        threading.Thread(
            target=self._load_full_photo,
            args=(path, load_id),
            daemon=True
        ).start()

    def _determine_initial_location(self, path):
        """Returns (label, coords_for_geocode):
          cached → (city, None): show immediately, no spinner.
          EXIF   → ("", coords): show spinner, async lookup.
          none   → ("", None): nothing to show.
        Caches "" for GPS-less photos older than 30s to skip re-parsing EXIF."""
        cached = self._photo_location.get(path)
        if cached:
            return cached, None
        if is_video(path):
            coords = get_video_gps_coords(path)
        else:
            coords = get_gps_coords(path)
        if coords:
            return "", coords
        try:
            if cached is None and time.time() - os.path.getmtime(path) > 30:
                self._photo_location[path] = ""
        except Exception:
            pass
        return "", None

    def _set_viewer_location(self, state, text=""):
        """state ∈ {'empty', 'searching', 'done'}."""
        try:
            if state == "searching":
                self.viewer_location_spinner.start()
                self.viewer_location_spinner.set_visible(True)
                self.viewer_location.set_text(_("Looking up location…"))
                self.viewer_location_box.set_visible(True)
            elif state == "done":
                self.viewer_location_spinner.stop()
                self.viewer_location_spinner.set_visible(False)
                self.viewer_location.set_text(text or "")
                self.viewer_location_box.set_visible(bool(text))
            else:  # empty
                self.viewer_location_spinner.stop()
                self.viewer_location_spinner.set_visible(False)
                self.viewer_location.set_text("")
                self.viewer_location_box.set_visible(False)
        except Exception:
            pass

    def _start_geocode_upgrade(self, path, coords, load_id):
        """Async reverse-geocode; falls back to raw coords so the spinner
        never gets stuck on failure (offline/timeout)."""
        def _bg():
            city = reverse_geocode(coords[0], coords[1])
            if city:
                resolved = f"📍 {city}"
                self._photo_location[path] = city
            else:
                resolved = f"📍 {coords[0]:.4f}, {coords[1]:.4f}"
                # Don't cache — retry once internet comes back.
            if load_id == self._viewer_load_id:
                GLib.idle_add(self._update_viewer_location, resolved, load_id)
        threading.Thread(target=_bg, daemon=True).start()

    def _load_full_photo(self, path, load_id):
        if is_video(path):
            initial, coords = self._determine_initial_location(path)
            searching = bool(coords)
            if load_id == self._viewer_load_id:
                GLib.idle_add(self._show_video, path, initial, searching)
            if coords:
                self._start_geocode_upgrade(path, coords, load_id)
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
        initial, coords = self._determine_initial_location(path)
        searching = bool(coords)
        if load_id == self._viewer_load_id:
            GLib.idle_add(self._show_full_photo, pixbuf, path, initial, searching)
            GLib.idle_add(self._preload_adjacent_photos)
        if coords:
            self._start_geocode_upgrade(path, coords, load_id)

    def _update_viewer_location(self, text, load_id):
        # Guard against stale callbacks when user has already navigated.
        if load_id != self._viewer_load_id:
            return False
        self._set_viewer_location("done", text)
        return False

    def _show_full_photo(self, pixbuf, path, location="", searching=False):
        self._stop_video()
        self._show_viewer_ui()   # reset opacity/visibility from any prior fade
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
        ts = get_photo_date(path)
        datum = format_viewer_date(datetime.datetime.fromtimestamp(ts))
        self.viewer_title.set_text(f"{os.path.basename(path)}  —  {datum}")
        if searching:
            self._set_viewer_location("searching")
        elif location:
            self._set_viewer_location("done", f"📍 {location}")
        else:
            self._set_viewer_location("empty")
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
            log_info(_("Previous photo: idx={i} → {name}").format(
                i=self.current_index, name=os.path.basename(new_path)
            ))
            self._schedule_photo_load()

    def next_photo(self, btn=None):
        if self.current_index < len(self.photos) - 1:
            self._stop_video()
            self.current_index += 1
            new_path = self.photos[self.current_index] if self.photos else "?"
            log_info(_("Next photo: idx={i} → {name}").format(
                i=self.current_index, name=os.path.basename(new_path)
            ))
            self._schedule_photo_load()

    def _schedule_photo_load(self):
        """Cache hit → show immediately; miss → thumbnail placeholder + async."""
        self._viewer_load_id += 1
        load_id = self._viewer_load_id
        # Update counter / prev-next immediately so arrow-key-held navigation
        # shows "X / N" in real time even when photo loads lag behind.
        try:
            self.viewer_counter.set_text(
                f"{self.current_index + 1} / {len(self.photos)}"
            )
            self.prev_btn.set_sensitive(self.current_index > 0)
            self.next_btn.set_sensitive(
                self.current_index < len(self.photos) - 1
            )
        except Exception:
            pass
        self._set_viewer_location("empty")
        self.filmstrip_area.queue_draw()
        self._scroll_filmstrip_to_current()

        if hasattr(self, '_nav_debounce_id') and self._nav_debounce_id:
            GLib.source_remove(self._nav_debounce_id)
            self._nav_debounce_id = None

        if not self.photos:
            return
        path = self.photos[self.current_index]

        # Cache hit (preloaded) → show instantly, no debounce.
        if not is_video(path):
            cached = self._viewer_pixbuf_cache.get(path)
            if cached is not None:
                self._viewer_pixbuf_cache.move_to_end(path)
                # Still resolve location here — without it, the location
                # label disappeared when prev/next'ing through preloaded photos.
                initial, coords = self._determine_initial_location(path)
                self._show_full_photo(cached, path, initial, searching=bool(coords))
                if coords:
                    self._start_geocode_upgrade(path, coords, load_id)
                GLib.idle_add(self._preload_adjacent_photos)
                return

        # Cache miss: show the thumbnail as instant placeholder.
        try:
            thumb_path = get_cache_path(path, THUMB_SIZE)
            if os.path.exists(thumb_path):
                thumb_pb = GdkPixbuf.Pixbuf.new_from_file(thumb_path)
                if thumb_pb:
                    self.photo_picture.set_pixbuf(thumb_pb)
        except Exception:
            pass

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
        log_info(_("Viewer closed → back to grid"))
        self._stop_video()
        self._viewer_load_id += 1
        self.header.set_visible(True)
        self.bottom_stack.set_visible(True)
        self._set_toolbars_revealed(True)
        # Don't clear the cluster filter here — user is still in a filtered
        # grid; the ✕ on the info-banner is how they exit it.
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

    def _update_filmstrip(self):
        """Filmstrip always shows newest-left → oldest-right, independent of
        grid sort — left = newer / right = older is more intuitive in viewer."""
        n = len(self.photos)
        w = n * (FILM_THUMB + 4)
        self.filmstrip_area.set_size_request(max(w, FILM_THUMB + 4), FILM_THUMB + 8)
        # Memoized view-order: avoid O(n log n) re-sort on every open_photo click.
        # Cache key = (id(self.photos), len(self.photos)); invalidated to None on
        # any self.photos replacement (see invalidation sites).
        cache = self._filmstrip_order_cache
        if cache is not None and cache[0] == id(self.photos) and cache[1] == n:
            self._filmstrip_view_order = cache[2]
        else:
            date_map = {p: get_photo_date(p) for p in self.photos}
            new_view_order = sorted(
                range(n), key=lambda i: date_map[self.photos[i]], reverse=True
            )
            self._filmstrip_view_order = new_view_order
            self._filmstrip_order_cache = (id(self.photos), n, new_view_order)
        self._filmstrip_load_id += 1
        load_id = self._filmstrip_load_id
        threading.Thread(
            target=self._load_filmstrip_bg,
            args=(list(self.photos),
                  list(self._filmstrip_view_order),
                  load_id),
            daemon=True
        ).start()

    def _load_filmstrip_bg(self, photos, view_order, load_id):
        n = len(view_order)
        if n == 0:
            return
        # Load outward from the visible position (current_index in view_order).
        try:
            center = view_order.index(self.current_index)
        except ValueError:
            center = 0
        order = [center]
        for dist in range(1, n):
            if load_id != self._filmstrip_load_id:
                return
            if center - dist >= 0:
                order.append(center - dist)
            if center + dist < n:
                order.append(center + dist)
        order = [vp for vp in order if vp not in self._filmstrip_thumbs]

        def load_one(vp):
            photo_idx = view_order[vp]
            path = photos[photo_idx]
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
            return vp, pb

        with ThreadPoolExecutor(max_workers=3) as pool:
            for count, (vp, pb) in enumerate(pool.map(load_one, order)):
                if load_id != self._filmstrip_load_id:
                    return
                self._filmstrip_thumbs[vp] = pb
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
        view = self._filmstrip_view_order
        n = len(view)
        if n == 0:
            return
        cell = FILM_THUMB + 4
        cr.set_source_rgba(0, 0, 0, 0.65)
        cr.rectangle(0, 0, width, height)
        cr.fill()
        # Only draw visible items.
        adj = self.filmstrip_scroll.get_hadjustment()
        scroll_x = adj.get_value() if adj else 0
        visible_w = adj.get_page_size() if adj else width
        first_visible = max(0, int(scroll_x / cell) - 1)
        last_visible = min(n, int((scroll_x + visible_w) / cell) + 2)
        try:
            current_visual = view.index(self.current_index)
        except ValueError:
            current_visual = -1
        for vp in range(first_visible, last_visible):
            photo_idx = view[vp]
            x = vp * cell + 2
            y = (height - FILM_THUMB) // 2
            pb = self._filmstrip_thumbs.get(vp)
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
            if 0 <= photo_idx < len(self.photos) and is_video(self.photos[photo_idx]):
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
            if vp == current_visual:
                cr.set_source_rgba(0.914, 0.329, 0.125, 1.0)
                cr.set_line_width(3)
                self._film_rounded_rect(cr, x + 1.5, y + 1.5, FILM_THUMB - 3, FILM_THUMB - 3, 5)
                cr.stroke()

    def _on_filmstrip_click(self, gesture, n_press, x, y):
        cell = FILM_THUMB + 4
        vp = int(x // cell)
        view = self._filmstrip_view_order
        if 0 <= vp < len(view):
            photo_idx = view[vp]
            if 0 <= photo_idx < len(self.photos) and photo_idx != self.current_index:
                self.current_index = photo_idx
                self._viewer_load_id += 1
                load_id = self._viewer_load_id
                self._set_viewer_location("empty")
                self.filmstrip_area.queue_draw()
                threading.Thread(
                    target=self._load_full_photo,
                    args=(self.photos[photo_idx], load_id),
                    daemon=True
                ).start()

    def _scroll_filmstrip_to_current(self):
        """Center current photo in filmstrip; the clamp keeps first/last at
        the edges instead of forcing them to center."""
        cell = FILM_THUMB + 4
        adj  = self.filmstrip_scroll.get_hadjustment()
        page = adj.get_page_size()
        if page <= 0:
            GLib.timeout_add(50, self._scroll_filmstrip_to_current)
            return False
        view = self._filmstrip_view_order
        try:
            vp = view.index(self.current_index)
        except ValueError:
            vp = 0
        center_of_current = vp * cell + cell / 2
        target = center_of_current - page / 2
        adj.set_value(max(0, min(target, adj.get_upper() - page)))
        return False

    def _show_video(self, path, location="", searching=False):
        self._stop_video()
        self._preview_cache = OrderedDict()
        self._viewer_zoom   = 1.0
        self._viewer_offset = [0.0, 0.0]
        self._show_viewer_ui()   # reset opacity/visibility from any prior fade
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
        ts = get_photo_date(path)
        datum = format_viewer_date(datetime.datetime.fromtimestamp(ts))
        self.viewer_title.set_text(f"{os.path.basename(path)}  —  {datum}")
        if searching:
            self._set_viewer_location("searching")
        elif location:
            self._set_viewer_location("done", f"📍 {location}")
        else:
            self._set_viewer_location("empty")
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
        opacity = max(0.0, 1.0 - self._fade_step / 8)  # ~400ms
        widgets = self._video_fade_widgets()
        for w in widgets:
            w.set_opacity(opacity)
        # Backup donut never fades — it's active status, not decorative OSD.
        if hasattr(self, "_viewer_donut_btn"):
            self._viewer_donut_btn.set_opacity(1.0)
        if opacity <= 0.0:
            for w in widgets:
                w.set_visible(False)
                w.set_can_target(False)
            self._fade_anim_id = None
            return False
        return True

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
        if not self.photos or not (0 <= self.current_index < len(self.photos)):
            return False
        self._preview_cache[ts_s] = None  # mark loading
        self._preview_extracting = True
        path = self.photos[self.current_index]
        # Bind extraction to the current viewer-load-id: if user navigates
        # away before ffmpeg finishes, the late callback discards its frame.
        load_id = self._viewer_load_id
        threading.Thread(
            target=self._extract_preview_frame,
            args=(path, ts_s, load_id),
            daemon=True
        ).start()
        return False

    def _extract_preview_frame(self, path, ts_s, load_id):
        import tempfile
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                tmp = f.name
            subprocess.run(
                ["ffmpeg", "-ss", str(ts_s), "-i", path,
                 "-vframes", "1", "-vf", "scale=160:-1", tmp, "-y"],
                capture_output=True, timeout=8
            )
            if load_id != self._viewer_load_id:
                # User already navigated away; discard.
                os.unlink(tmp)
                return
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(tmp, 160, 90, True)
            os.unlink(tmp)
            self._preview_cache[ts_s] = pb
            self._preview_cache.move_to_end(ts_s)
            while len(self._preview_cache) > 25:
                self._preview_cache.popitem(last=False)
            GLib.idle_add(self._apply_preview_frame, ts_s, load_id)
        except Exception:
            pass
        finally:
            self._preview_extracting = False

    def _apply_preview_frame(self, ts_s, load_id=None):
        if load_id is not None and load_id != self._viewer_load_id:
            return False
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
            _("Remove from favorites") if is_fav else _("Mark as favorite")
        )

    def on_toggle_favorite(self, btn):
        path = self._current_photo_path()
        if not path:
            return
        if path in self._favorites:
            self._favorites.discard(path)
            log_info(_("Favorite removed: {p}").format(p=path))
        else:
            self._favorites.add(path)
            log_info(_("Favorite added: {p}").format(p=path))
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
        log_info(_("Favorites filter: {state}").format(
            state=_("on") if self._favorites_only else _("off")
        ))
        self.load_photos()


    def on_edit_current(self, btn):
        path = self._current_photo_path() or "?"
        log_info(_("Editor opened for: {name}").format(name=os.path.basename(path)))
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
        log_info(_("Editor cancelled"))
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
        log_info(_("Editor: rotate left (-90°)"))
        self._editor_rotation = (self._editor_rotation + 90) % 360
        self._reset_crop()
        self._editor_apply_preview()

    def on_editor_rotate_right(self, btn):
        log_info(_("Editor: rotate right (+90°)"))
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
        log_info(_("Editor crop mode: {state}").format(
            state=_("on") if self._editor_crop_mode else _("off")
        ))
        self._crop_rect        = None
        self._crop_handle      = None
        self._crop_rect_origin = None
        self.crop_overlay_area.set_visible(self._editor_crop_mode)
        if self._editor_crop_mode:
            self.crop_overlay_area.queue_draw()

    def _get_image_display_rect(self, widget_w, widget_h):
        """Return (x, y, w, h) of the letterboxed image inside the widget."""
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
        if self._crop_rect is None:
            ix, iy, iw, ih = self._get_image_display_rect(w, h)
            self._crop_rect = [ix, iy, ix + iw, iy + ih]

        x1, y1, x2, y2 = self._crop_rect
        rw, rh = x2 - x1, y2 - y1

        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.paint()

        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.rectangle(x1, y1, rw, rh)
        cr.fill()
        cr.set_operator(cairo.OPERATOR_OVER)

        cr.set_source_rgba(1, 1, 1, 1.0)
        cr.set_line_width(2)
        cr.rectangle(x1, y1, rw, rh)
        cr.stroke()

        # Rule-of-thirds gridlines
        cr.set_source_rgba(1, 1, 1, 0.35)
        cr.set_line_width(1)
        for i in (1, 2):
            cr.move_to(x1 + rw * i / 3, y1)
            cr.line_to(x1 + rw * i / 3, y2)
            cr.move_to(x1, y1 + rh * i / 3)
            cr.line_to(x2, y1 + rh * i / 3)
        cr.stroke()

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
        HANDLE_R = 20  # detection radius
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
        log_info(_("Editor save: rotation={rot}° crop={crop} path={p}").format(
            rot=rotation, crop=bool(self._crop_rect), p=path
        ))

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

                exif = img.getexif() if is_jpeg else None

                # Normalize EXIF orientation so PIL and GdkPixbuf agree.
                img = ImageOps.exif_transpose(img)

                if rotation != 0:
                    img = img.rotate(rotation, expand=True)
                if crop_box:
                    img = img.crop(crop_box)

                if is_jpeg:
                    # Reset orientation tag to normal (1) — pixels are now physically correct.
                    if exif is not None:
                        exif[0x0112] = 1
                    img.save(path, "JPEG", quality=95,
                             exif=exif.tobytes() if exif is not None else b"")
                else:
                    img.save(path, "PNG")
                if os.path.exists(old_cache):
                    os.remove(old_cache)
                os.utime(path, (original_mtime, original_mtime))
                GLib.idle_add(_after_save)
            except Exception as e:
                log_error(_("Editor save failed: {err}").format(err=e))
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
                heading=_("Save failed"),
                body=msg
            )
            dialog.add_response("ok", _("OK"))
            dialog.present()

        threading.Thread(target=_do_save, daemon=True).start()

    def on_delete_current(self, btn):
        if not self.photos or not (0 <= self.current_index < len(self.photos)):
            return
        if getattr(self, "_shredding", False):
            return
        path = self.photos[self.current_index]
        log_info(_("Delete confirmation requested: {p}").format(p=path))
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Delete photo?"),
            body=_("Are you sure you want to delete '{name}'? This cannot be undone.").format(name=os.path.basename(path))
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_delete_current_response, path)
        dialog.present()

    def _on_delete_current_response(self, dialog, response, path):
        if response != "delete":
            log_info(_("Delete cancelled: {p}").format(p=path))
            return
        if getattr(self, "_shredding", False):
            return
        self._shredding = True
        self._play_shred_animation(path, on_done=self._finish_delete_after_shred)

    def _play_shred_animation(self, path, on_done):
        """Paper-shredder effect: split the photo into vertical strips that
        fall + fade with staggered delay, then call on_done(path). For videos
        we fall back to the cached thumbnail — otherwise delete would happen
        without animation."""
        pixbuf = getattr(self, "_viewer_pixbuf", None)
        if pixbuf is None and is_video(path):
            try:
                thumb_path = get_cache_path(path, THUMB_SIZE)
                if os.path.exists(thumb_path):
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file(thumb_path)
            except Exception:
                pixbuf = None
        if pixbuf is None:
            on_done(path)
            return

        # Render the pixbuf ONCE into a cairo ImageSurface. Otherwise each
        # strip × frame would re-upload the full pixbuf to the GPU (~14×60 =
        # 840 uploads/sec, killing fps).
        try:
            anim_pb = pixbuf
            orig_w = anim_pb.get_width()
            orig_h = anim_pb.get_height()
            # Scale to 1280px max for faster rendering.
            MAX_DIM = 1280
            if max(orig_w, orig_h) > MAX_DIM:
                scale = MAX_DIM / max(orig_w, orig_h)
                anim_pb = anim_pb.scale_simple(
                    max(1, int(orig_w * scale)),
                    max(1, int(orig_h * scale)),
                    GdkPixbuf.InterpType.BILINEAR,
                )
            pb_w = anim_pb.get_width()
            pb_h = anim_pb.get_height()
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, pb_w, pb_h)
            _tmp_ctx = cairo.Context(surface)
            Gdk.cairo_set_source_pixbuf(_tmp_ctx, anim_pb, 0, 0)
            _tmp_ctx.paint()
        except Exception as e:
            log_error(_("Shred animation pre-render error: {err}").format(err=e))
            on_done(path)
            return

        draw_area = Gtk.DrawingArea()
        draw_area.set_vexpand(True)
        draw_area.set_hexpand(True)
        draw_area.set_can_target(False)
        self.viewer_area.add_overlay(draw_area)
        # Hide picture/video under the overlay. For videos, pause and detach
        # first — otherwise it keeps playing and flickers under the animation.
        is_vid = is_video(path)
        if is_vid:
            try:
                if self._video_media is not None:
                    self._video_media.pause()
                self.video_display.set_visible(False)
            except Exception:
                pass
        self.photo_picture.set_visible(False)

        N_STRIPS = 12
        DURATION = 1.1
        state = {"progress": 0.0, "start": None, "done": False}

        def _draw_fn(area, cr, w, h):
            try:
                progress = state["progress"]
                if w <= 0 or h <= 0 or pb_w <= 0 or pb_h <= 0:
                    return
                scale = min(w / pb_w, h / pb_h)
                disp_w = pb_w * scale
                disp_h = pb_h * scale
                x0 = (w - disp_w) / 2
                y0 = (h - disp_h) / 2
                strip_w = disp_w / N_STRIPS
                for i in range(N_STRIPS):
                    delay = (i / N_STRIPS) * 0.35
                    denom = max(0.001, 1.0 - delay)
                    sp = max(0.0, min(1.0, (progress - delay) / denom))
                    ease = sp * sp * sp  # cubic ease-in for gravity feel
                    y_off = ease * h * 1.4
                    opacity = max(0.0, 1.0 - sp * 1.05)
                    rot = (sp * 0.4) * (1 if i % 2 else -1)
                    strip_x = x0 + i * strip_w

                    cr.save()
                    cr.rectangle(strip_x, 0, strip_w + 1, h)
                    cr.clip()
                    cx = strip_x + strip_w / 2
                    cy = y0 + disp_h / 2 + y_off
                    cr.translate(cx, cy)
                    cr.rotate(rot)
                    cr.translate(-cx, -cy)
                    cr.translate(x0, y0 + y_off)
                    cr.scale(scale, scale)
                    cr.set_source_surface(surface, 0, 0)
                    cr.paint_with_alpha(opacity)
                    cr.restore()
            except Exception as _e:
                log_error(_("Shred animation draw error: {err}").format(err=_e))

        draw_area.set_draw_func(_draw_fn)

        def _tick(widget, frame_clock):
            if state["done"]:
                return False
            now = frame_clock.get_frame_time() / 1_000_000.0
            if state["start"] is None:
                state["start"] = now
            elapsed = now - state["start"]
            state["progress"] = min(1.0, elapsed / DURATION)
            widget.queue_draw()
            if elapsed >= DURATION:
                state["done"] = True
                try:
                    self.viewer_area.remove_overlay(draw_area)
                except Exception:
                    pass
                # Clear the old pixbuf before setting visible — otherwise
                # photo_picture would briefly show the deleted photo before
                # on_done loads the next one.
                try:
                    self.photo_picture.set_pixbuf(None)
                    self.viewer_title.set_text("")
                    self._set_viewer_location("empty")
                except Exception:
                    pass
                # Re-show both photo_picture and video_display — on_done
                # navigates to the next file (photo or video) and
                # show_full_photo/_show_video picks which stays visible.
                self.photo_picture.set_visible(True)
                if is_vid:
                    try:
                        self.video_display.set_visible(True)
                    except Exception:
                        pass
                on_done(path)
                return False
            return True

        draw_area.add_tick_callback(_tick)

    def _cleanup_empty_parent_dirs(self, path):
        """Remove empty parent dirs up to photo_path root (which is never
        removed). Stops at the first non-empty dir."""
        photo_root = self.settings.get("photo_path")
        if not photo_root:
            return
        photo_root = os.path.abspath(photo_root)
        parent = os.path.dirname(os.path.abspath(path))
        while (parent != photo_root
               and parent.startswith(photo_root + os.sep)):
            try:
                os.rmdir(parent)  # only succeeds when empty
                log_info(_("Empty folder removed: {p}").format(p=parent))
            except OSError:
                break
            parent = os.path.dirname(parent)

    def _finish_delete_after_shred(self, path):
        """Post-animation: actually delete + navigate."""
        n_before = len(self.photos)
        try:
            os.remove(path)
            log_info(_("Photo deleted: {p}").format(p=path))
        except FileNotFoundError:
            # Already gone (prior delete / external tool / watcher race).
            log_warn(_("File already gone from disk: {p}").format(p=path))
        except Exception as e:
            log_error(_("Delete failed: {err}").format(err=e))
            self._shredding = False
            return
        try:
            cache_path = get_cache_path(path)
            if os.path.exists(cache_path):
                os.remove(cache_path)
        except Exception:
            pass
        self._cleanup_empty_parent_dirs(path)
        if path in self._favorites:
            self._favorites.discard(path)
            self._schedule_save_favorites()
        # List-comprehension (not .remove()) so watcher-reload races never
        # raise when path isn't in the list. Log count to surface bugs.
        self.photos = [p for p in self.photos if p != path]
        self._filmstrip_order_cache = None
        n_after = len(self.photos)
        log_info(_("photos list: {before} → {after}").format(before=n_before, after=n_after))
        if not self.photos:
            self._shredding = False
            self.close_viewer()
            self.show_empty_state()
            return
        if self.current_index >= len(self.photos):
            self.current_index = len(self.photos) - 1
        next_path = self.photos[self.current_index]
        # Rebuild filmstrip thumbs + redraw so the deleted photo vanishes.
        self._filmstrip_thumbs = {}
        try:
            self.filmstrip_area.queue_draw()
        except Exception:
            pass
        GLib.idle_add(self._update_filmstrip)
        GLib.timeout_add(80, self._scroll_filmstrip_to_current)
        try:
            self.viewer_counter.set_text(f"{self.current_index + 1} / {len(self.photos)}")
        except Exception:
            pass
        self._viewer_load_id += 1
        load_id = self._viewer_load_id
        threading.Thread(
            target=self._load_full_photo,
            args=(next_path, load_id),
            daemon=True
        ).start()
        GLib.timeout_add(500, self.start_load)
        self._shredding = False

    def on_delete_selected(self, btn):
        if not self._selected:
            return
        count = len(self._selected)
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=ngettext("Delete %d photo?", "Delete %d photos?", count) % count,
            body=ngettext(
                "Are you sure you want to delete %d photo? This cannot be undone.",
                "Are you sure you want to delete %d photos? This cannot be undone.",
                count,
            ) % count,
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", ngettext("Delete %d", "Delete %d", count) % count)
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
                log_error(_("Delete failed: {err}").format(err=e))
            if path in self._favorites:
                self._favorites.discard(path)
                fav_changed = True
        # Clean up empty dirs AFTER all files are gone so we don't stop early.
        for path in paths_to_delete:
            self._cleanup_empty_parent_dirs(path)
        if fav_changed:
            self._schedule_save_favorites()
        self.toggle_select_mode()
        self.load_photos()

    def on_settings_clicked(self, btn):
        if self._settings_dialog is not None:
            self._settings_dialog.present()
            return
        log_info(_("Settings opened"))
        # PreferencesWindow (own toplevel) instead of PreferencesDialog —
        # on Adw 1.5+ the latter blocks the main window's close-request, so
        # GNOME "Close N windows" can't kill Pixora while settings is open.
        dialog = Adw.PreferencesWindow()
        dialog.set_title(_("Settings"))
        dialog.set_transient_for(self)
        dialog.set_modal(False)
        dialog.set_default_size(640, 720)
        dialog.set_search_enabled(False)
        self._settings_dialog = dialog
        if hasattr(self, "settings_btn"):
            self.settings_btn.set_sensitive(False)

        def _on_settings_closed(_d):
            self._settings_dialog = None
            if hasattr(self, "settings_btn"):
                self.settings_btn.set_sensitive(True)
            return False

        dialog.connect("close-request", _on_settings_closed)

        display_page = Adw.PreferencesPage()
        display_page.set_title(_("Display"))
        display_page.set_icon_name("preferences-desktop-display-symbolic")

        import_page = Adw.PreferencesPage()
        import_page.set_title(_("Import"))
        import_page.set_icon_name("document-send-symbolic")

        advanced_page = Adw.PreferencesPage()
        advanced_page.set_title(_("Advanced"))
        advanced_page.set_icon_name("applications-engineering-symbolic")

        about_page = Adw.PreferencesPage()
        about_page.set_title(_("About"))
        about_page.set_icon_name("help-about-symbolic")

        folder_group = Adw.PreferencesGroup()
        folder_group.set_title(_("Photo folder"))
        folder_group.set_description(_("Where your photos are stored"))

        self.folder_row = Adw.ActionRow()
        self.folder_row.set_title(_("Current folder"))
        self.folder_row.set_subtitle(self.settings.get("photo_path") or _("Not configured"))

        change_folder_btn = Gtk.Button(label=_("Change"))
        change_folder_btn.add_css_class("flat")
        change_folder_btn.set_valign(Gtk.Align.CENTER)
        change_folder_btn.connect("clicked", lambda b: self.change_folder(dialog))
        self.folder_row.add_suffix(change_folder_btn)
        folder_group.add(self.folder_row)
        display_page.add(folder_group)

        display_group = Adw.PreferencesGroup()
        display_group.set_title(_("Display"))
        display_group.set_description(_("How photos are displayed in the grid"))

        thumb_row = Adw.ActionRow(
            title=_("Thumbnail size"),
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
        thumb_reset_btn.set_tooltip_text(_("Back to default (200 px)"))
        thumb_reset_btn.set_sensitive(int(thumb_adj.get_value()) != 200)
        thumb_reset_btn.connect("clicked", lambda b: thumb_adj.set_value(200.0))
        thumb_adj.connect(
            "value-changed",
            lambda a: thumb_reset_btn.set_sensitive(int(a.get_value()) != 200)
        )
        thumb_row.add_suffix(thumb_reset_btn)

        # Apply button — grid is only regenerated on click, so sliding
        # doesn't freeze the UI.
        self._thumb_apply_btn = Gtk.Button(icon_name="emblem-ok-symbolic")
        self._thumb_apply_btn.add_css_class("flat")
        self._thumb_apply_btn.add_css_class("circular")
        self._thumb_apply_btn.set_valign(Gtk.Align.CENTER)
        self._thumb_apply_btn.set_tooltip_text(_("Apply"))
        self._thumb_apply_btn.set_sensitive(False)
        self._thumb_apply_btn.connect("clicked", self._on_thumb_apply_clicked)
        thumb_row.add_suffix(self._thumb_apply_btn)

        display_group.add(thumb_row)

        # Preview updates live while sliding without touching real thumbnails.
        self._thumb_preview = Gtk.DrawingArea()
        self._thumb_preview.set_content_width(140)
        self._thumb_preview.set_content_height(140)
        self._thumb_preview.set_draw_func(self._draw_thumb_preview)
        self._pending_thumb_size = THUMB_SIZE
        preview_row = Adw.ActionRow(
            title=_("Preview"),
            subtitle=_("How large your thumbnails will be at this setting"),
        )
        preview_row.add_suffix(self._thumb_preview)
        preview_row.set_activatable(False)
        display_group.add(preview_row)

        lang_row = Adw.ActionRow(
            title=_("Language"),
            subtitle=_("Pixora must be restarted to load a new language")
        )
        lang_model = Gtk.StringList()
        self._lang_codes = ["nl", "en", "de", "fr"]
        self._lang_labels = ["🇳🇱  Nederlands", "🇬🇧  English", "🇩🇪  Deutsch", "🇫🇷  Français"]
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

        perf_group = Adw.PreferencesGroup()
        perf_group.set_title(_("Performance"))
        perf_group.set_description(
            _("Turn off animations for a faster, snappier feel on slow systems (VMs, no GPU acceleration).")
        )
        self._anim_switch = Gtk.Switch()
        self._anim_switch.set_valign(Gtk.Align.CENTER)
        self._anim_switch.set_active(
            not bool(self.settings.get("animations_enabled", True))
        )
        self._anim_switch.connect("notify::active", self._on_anim_toggle)
        anim_row = Adw.ActionRow(
            title=_("Reduce animations"),
            subtitle=_("Disables fade/slide transitions between views."),
        )
        anim_row.add_prefix(Gtk.Image.new_from_icon_name("media-playback-pause-symbolic"))
        anim_row.add_suffix(self._anim_switch)
        anim_row.set_activatable_widget(self._anim_switch)
        perf_group.add(anim_row)

        # Renderer dropdown — applies via GSK_RENDERER env var, so it only
        # takes effect after a restart. Sentinel pattern matches language.
        self._gsk_codes  = ["auto", "gl", "cairo"]
        self._gsk_labels = [_("Automatic"), _("GPU (GL)"), _("Software (Cairo)")]
        gsk_model = Gtk.StringList()
        for lbl in self._gsk_labels:
            gsk_model.append(lbl)
        self._gsk_combo = Gtk.DropDown(model=gsk_model)
        self._gsk_combo.set_valign(Gtk.Align.CENTER)
        current_gsk = self.settings.get("gsk_renderer", "auto")
        try:
            self._gsk_combo.set_selected(self._gsk_codes.index(current_gsk))
        except ValueError:
            self._gsk_combo.set_selected(0)
        self._gsk_combo.connect("notify::selected", self._on_gsk_renderer_changed)
        gsk_row = Adw.ActionRow(
            title=_("Rendering backend"),
            subtitle=_("Only change if Pixora is slow or crashes. Requires a restart."),
        )
        gsk_row.add_prefix(Gtk.Image.new_from_icon_name("video-display-symbolic"))
        gsk_row.add_suffix(self._gsk_combo)
        try:
            gsk_row.set_subtitle_lines(2)
        except Exception:
            pass
        perf_group.add(gsk_row)

        advanced_page.add(perf_group)

        dev_group = Adw.PreferencesGroup()
        dev_group.set_title(_("Advanced"))
        dev_group.set_description(
            _("Developer mode shows Pixora with terminal output and uses the terminal updater. Only enable if you know what you're doing.")
        )
        current_dev = bool(self.settings.get("dev_mode", False))
        dev_row = Adw.ActionRow(
            title=_("Developer mode"),
            subtitle=_("Active") if current_dev else _("Inactive")
        )
        dev_btn = Gtk.Button(
            label=_("Deactivate") if current_dev else _("Activate")
        )
        dev_btn.add_css_class("flat")
        dev_btn.set_valign(Gtk.Align.CENTER)
        dev_btn.connect("clicked", self._on_toggle_dev_mode, dev_row)
        dev_row.add_suffix(dev_btn)
        self._dev_btn = dev_btn
        dev_group.add(dev_row)
        advanced_page.add(dev_group)

        structure_group = Adw.PreferencesGroup()
        structure_group.set_title(_("Folder structure"))
        structure_group.set_description(
            _("Controls how Pixora saves imported photos in your library.")
        )
        current_structure = self.settings.get("structure", "year_month")

        self.radio_flat = Gtk.CheckButton()
        self.radio_flat.set_active(current_structure == "flat")
        self.radio_flat.connect("toggled", lambda b: self.on_structure_changed("flat", b))
        flat_row = Adw.ActionRow(
            title=_("All together"),
            subtitle=_("All photos go into a single folder — no subfolders."),
        )
        flat_row.add_prefix(Gtk.Image.new_from_icon_name("folder-symbolic"))
        flat_row.add_prefix(self.radio_flat)
        flat_row.set_activatable_widget(self.radio_flat)
        structure_group.add(flat_row)

        self.radio_year = Gtk.CheckButton()
        self.radio_year.set_group(self.radio_flat)
        self.radio_year.set_active(current_structure == "year")
        self.radio_year.connect("toggled", lambda b: self.on_structure_changed("year", b))
        year_row = Adw.ActionRow(
            title=_("By year"),
            subtitle=_("Separate folder per year — e.g. 2024/, 2025/."),
        )
        year_row.add_prefix(Gtk.Image.new_from_icon_name("folder-open-symbolic"))
        year_row.add_prefix(self.radio_year)
        year_row.set_activatable_widget(self.radio_year)
        structure_group.add(year_row)

        self.radio_month = Gtk.CheckButton()
        self.radio_month.set_group(self.radio_flat)
        self.radio_month.set_active(current_structure == "year_month")
        self.radio_month.connect("toggled", lambda b: self.on_structure_changed("year_month", b))
        month_row = Adw.ActionRow(
            title=_("By year and month"),
            subtitle=_("Year folder with month subfolders — e.g. 2024/2024-03/."),
        )
        month_row.add_prefix(Gtk.Image.new_from_icon_name("view-list-symbolic"))
        month_row.add_prefix(self.radio_month)
        month_row.set_activatable_widget(self.radio_month)
        structure_group.add(month_row)

        reorganize_btn = Gtk.Button(label=_("Tidy up"))
        reorganize_btn.add_css_class("flat")
        reorganize_btn.set_valign(Gtk.Align.CENTER)
        reorganize_btn.connect(
            "clicked", lambda b: self._prompt_reorganize(from_startup=False)
        )
        reorganize_row = Adw.ActionRow(
            title=_("Reorganize current folders"),
            subtitle=_("Scan the photo folder and move photos to match the chosen structure. Bit-identical duplicates are removed."),
        )
        reorganize_row.add_prefix(Gtk.Image.new_from_icon_name("view-refresh-symbolic"))
        reorganize_row.add_suffix(reorganize_btn)
        try:
            reorganize_row.set_subtitle_lines(3)
        except Exception:
            pass
        structure_group.add(reorganize_row)

        silent_row = Adw.ActionRow(
            title=_("Auto-confirm"),
            subtitle=_("Starts right away when there's work to do, without interrupting."),
        )
        silent_row.add_prefix(
            Gtk.Image.new_from_icon_name("media-playback-start-symbolic"))
        silent_switch = Gtk.Switch()
        silent_switch.set_valign(Gtk.Align.CENTER)
        silent_switch.set_active(
            bool(self.settings.get("reorganize_silent", False)))
        silent_switch.connect("notify::active", self._on_reorganize_silent_toggle)
        silent_row.add_suffix(silent_switch)
        silent_row.set_activatable_widget(silent_switch)
        try:
            silent_row.set_subtitle_lines(3)
        except Exception:
            pass
        structure_group.add(silent_row)

        import_page.add(structure_group)

        dup_group = Adw.PreferencesGroup()
        dup_group.set_title(_("Duplicate detection"))
        dup_group.set_description(_("Check for existing photos during import"))
        # Threshold 0 = off, ≥1 = on. "On" always uses strict (=1) for accuracy.
        dup_on = self.settings.get("duplicate_threshold", 2) != 0

        dup_info_row = Adw.ActionRow(
            title=_("How it works"),
            subtitle=_("Pixora visually compares each new photo with your library. On a match you pick per photo: skip, import anyway, or keep both."),
        )
        dup_info_row.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
        dup_info_row.set_activatable(False)
        try:
            dup_info_row.set_subtitle_lines(3)
        except Exception:
            pass
        dup_group.add(dup_info_row)

        self.settings_dup_switch = Gtk.Switch()
        self.settings_dup_switch.set_valign(Gtk.Align.CENTER)
        self.settings_dup_switch.set_active(dup_on)
        self.settings_dup_switch.connect("notify::active", self.on_dup_switch_toggled)
        dup_row = Adw.ActionRow(title=_("Duplicate detection"))
        dup_row.add_prefix(Gtk.Image.new_from_icon_name("security-high-symbolic"))
        dup_row.add_suffix(self.settings_dup_switch)
        dup_row.set_activatable_widget(self.settings_dup_switch)
        dup_group.add(dup_row)

        import_page.add(dup_group)

        backup_group = Adw.PreferencesGroup()
        backup_group.set_title(_("Automatic backup"))
        backup_group.set_description(_("Backup to external USB drive after each import"))

        backup_on = bool(self.settings.get("backup_enabled"))
        drive_present = self._backup_drive_mountpoint() is not None

        if backup_on and self.settings.get("backup_uuid") and not drive_present:
            backup_group.set_description(
                _("Drive not connected — plug in the USB drive to back up.")
            )

        self.settings_backup_switch = Gtk.Switch()
        self.settings_backup_switch.set_valign(Gtk.Align.CENTER)
        self.settings_backup_switch.set_active(backup_on)
        self.settings_backup_switch.connect("notify::active", self.on_settings_backup_toggle)

        backup_toggle_row = Adw.ActionRow(title=_("Automatic backup"), subtitle=_("Synchronizes after each import"))
        backup_toggle_row.add_suffix(self.settings_backup_switch)
        backup_toggle_row.set_activatable_widget(self.settings_backup_switch)
        backup_group.add(backup_toggle_row)

        self.settings_drive_model = Gtk.StringList()
        self.settings_drives = self._build_settings_drive_list()
        if self.settings_drives:
            for uuid, label in self.settings_drives:
                self.settings_drive_model.append(label)
        else:
            self.settings_drive_model.append(_("No external drives found"))

        self.settings_drive_combo = Gtk.DropDown(model=self.settings_drive_model)
        self.settings_drive_combo.set_size_request(220, -1)
        self.settings_drive_combo.set_sensitive(backup_on and drive_present)
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
        settings_refresh_btn.set_tooltip_text(_("Rescan drives"))
        settings_refresh_btn.connect("clicked", self.on_settings_refresh_drives)

        self.settings_drive_reset_btn = Gtk.Button(icon_name="edit-clear-symbolic")
        self.settings_drive_reset_btn.add_css_class("flat")
        self.settings_drive_reset_btn.set_valign(Gtk.Align.CENTER)
        self.settings_drive_reset_btn.set_tooltip_text(_("Forget saved backup drive"))
        self.settings_drive_reset_btn.set_visible(bool(self.settings.get("backup_uuid")))
        self.settings_drive_reset_btn.connect("clicked", self.on_settings_reset_drive)

        self.settings_drive_row = Adw.ActionRow(title=_("Backup drive"), subtitle=_("External drives only"))
        self.settings_drive_row.add_suffix(self.settings_drive_reset_btn)
        self.settings_drive_row.add_suffix(settings_refresh_btn)
        self.settings_drive_row.add_suffix(self.settings_drive_combo)
        self.settings_drive_row.set_sensitive(backup_on)
        backup_group.add(self.settings_drive_row)

        current_backup_path = self.settings.get("backup_path") or _("Not configured")
        self.settings_backup_folder_row = Adw.ActionRow(
            title=_("Folder on backup drive"),
            subtitle=current_backup_path
        )
        self.settings_backup_folder_row.set_sensitive(backup_on and drive_present)

        folder_btn_label = _("Change") if self.settings.get("backup_path") else _("Set up")
        self.settings_backup_folder_btn = Gtk.Button(label=folder_btn_label)
        self.settings_backup_folder_btn.add_css_class("flat")
        self.settings_backup_folder_btn.set_valign(Gtk.Align.CENTER)
        self.settings_backup_folder_btn.connect("clicked", self.on_settings_change_backup_folder)
        self.settings_backup_folder_row.add_suffix(self.settings_backup_folder_btn)
        backup_group.add(self.settings_backup_folder_row)

        current_mode = self.settings.get("backup_mode", "backup")

        self.radio_mode_backup = Gtk.CheckButton()
        self.radio_mode_backup.set_active(current_mode == "backup")
        self.radio_mode_backup.connect(
            "toggled", lambda b: self.on_backup_mode_changed("backup", b)
        )
        mode_backup_row = Adw.ActionRow(
            title=_("Backup"),
            subtitle=_("One-way copy: additions only. Photos you delete in Pixora stay on the USB as an archive."),
        )
        mode_backup_row.add_prefix(Gtk.Image.new_from_icon_name("drive-harddisk-symbolic"))
        mode_backup_row.add_prefix(self.radio_mode_backup)
        mode_backup_row.set_activatable_widget(self.radio_mode_backup)
        mode_backup_row.set_sensitive(backup_on and drive_present)
        try:
            mode_backup_row.set_subtitle_lines(3)
        except Exception:
            pass
        self.settings_mode_backup_row = mode_backup_row
        backup_group.add(mode_backup_row)

        self.radio_mode_sync = Gtk.CheckButton()
        self.radio_mode_sync.set_group(self.radio_mode_backup)
        self.radio_mode_sync.set_active(current_mode == "sync")
        self.radio_mode_sync.connect(
            "toggled", lambda b: self.on_backup_mode_changed("sync", b)
        )
        mode_sync_row = Adw.ActionRow(
            title=_("Sync"),
            subtitle=_("Exact mirror of your Pixora library. Photos you delete in Pixora are also removed from the USB on the next backup."),
        )
        mode_sync_row.add_prefix(Gtk.Image.new_from_icon_name("emblem-synchronizing-symbolic"))
        mode_sync_row.add_prefix(self.radio_mode_sync)
        mode_sync_row.set_activatable_widget(self.radio_mode_sync)
        mode_sync_row.set_sensitive(backup_on and drive_present)
        try:
            mode_sync_row.set_subtitle_lines(3)
        except Exception:
            pass
        self.settings_mode_sync_row = mode_sync_row
        backup_group.add(mode_sync_row)

        self.settings_dedup_switch = Gtk.Switch()
        self.settings_dedup_switch.set_valign(Gtk.Align.CENTER)
        # USB-dedup reuses the pHash engine; force off when main detection is off.
        if not dup_on:
            self.settings_dedup_switch.set_active(False)
            if self.settings.get("backup_dedup"):
                self.settings["backup_dedup"] = False
                save_settings(self.settings)
        else:
            self.settings_dedup_switch.set_active(bool(self.settings.get("backup_dedup")))
        self.settings_dedup_switch.connect("notify::active", self.on_backup_dedup_toggle)
        dedup_row = Adw.ActionRow(
            title=_("Backup duplicate detector"),
            subtitle=_("Skips photos already on the USB, even if they are stored there under a different name or folder. Requires duplicate detection above to be enabled."),
        )
        dedup_row.add_prefix(Gtk.Image.new_from_icon_name("edit-copy-symbolic"))
        dedup_row.add_suffix(self.settings_dedup_switch)
        try:
            dedup_row.set_subtitle_lines(3)
        except Exception:
            pass
        dedup_row.set_activatable_widget(self.settings_dedup_switch)
        dedup_row.set_sensitive(backup_on and drive_present and dup_on)
        self.settings_dedup_row = dedup_row
        backup_group.add(dedup_row)

        # Silent-mode: skip scan-dialog and auto-start backup. Errors still popup.
        self.settings_silent_switch = Gtk.Switch()
        self.settings_silent_switch.set_valign(Gtk.Align.CENTER)
        self.settings_silent_switch.set_active(bool(self.settings.get("backup_silent")))
        self.settings_silent_switch.connect("notify::active", self.on_backup_silent_toggle)
        silent_row = Adw.ActionRow(
            title=_("Auto-confirm"),
            subtitle=_("Starts right away when there's work to do, without interrupting."),
        )
        silent_row.add_prefix(Gtk.Image.new_from_icon_name("media-playback-start-symbolic"))
        silent_row.add_suffix(self.settings_silent_switch)
        silent_row.set_activatable_widget(self.settings_silent_switch)
        silent_row.set_sensitive(backup_on and drive_present)
        try:
            silent_row.set_subtitle_lines(3)
        except Exception:
            pass
        self.settings_silent_row = silent_row
        backup_group.add(silent_row)

        self.settings_manual_scan_btn = Gtk.Button()
        self.settings_manual_scan_btn.add_css_class("flat")
        self.settings_manual_scan_btn.set_valign(Gtk.Align.CENTER)
        self.settings_manual_scan_btn.set_size_request(120, 32)
        self.settings_manual_scan_btn.connect("clicked", self.on_settings_manual_scan)
        # States: idle (label), checking (spinner), uptodate (✓ 5s fade).
        self._scan_btn_stack = Gtk.Stack()
        self._scan_btn_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._scan_btn_stack.set_transition_duration(250)
        self._scan_btn_stack.add_named(Gtk.Label(label=_("Check")), "idle")
        _scan_spin_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=0,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
        )
        self._scan_check_spinner = Gtk.Spinner()
        self._scan_check_spinner.set_size_request(18, 18)
        _scan_spin_box.append(self._scan_check_spinner)
        self._scan_btn_stack.add_named(_scan_spin_box, "checking")
        _scan_ok = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        _scan_ok.add_css_class("success")
        self._scan_btn_stack.add_named(_scan_ok, "uptodate")
        self.settings_manual_scan_btn.set_child(self._scan_btn_stack)
        self._scan_btn_fade_id = None
        # If a scan/backup is already running when settings opens, show the
        # spinner immediately so the user doesn't click in vain.
        if self._backup_scanning or self._backup_running:
            self._scan_btn_stack.set_visible_child_name("checking")
            self._scan_check_spinner.start()
            self.settings_manual_scan_btn.set_sensitive(False)
        manual_scan_row = Adw.ActionRow(
            title=_("Check now"),
            subtitle=_("Scan USB for missing photos"),
        )
        manual_scan_row.add_prefix(Gtk.Image.new_from_icon_name("system-search-symbolic"))
        manual_scan_row.add_suffix(self.settings_manual_scan_btn)
        manual_scan_row.set_sensitive(backup_on and drive_present)
        self.settings_manual_scan_row = manual_scan_row
        backup_group.add(manual_scan_row)

        self.settings_backup_group = backup_group

        import_page.add(backup_group)

        about_group = Adw.PreferencesGroup()
        about_group.set_title(_("About"))

        app_row = Adw.ActionRow(
            title=_("Pixora"),
            subtitle=_("Made with ❤ by LinuxGinger"))
        icon_path = os.path.join(ASSETS_DIR, "pixora-icon.svg")
        if os.path.exists(icon_path):
            app_icon = Gtk.Image.new_from_file(icon_path)
            app_icon.set_pixel_size(32)
            app_row.add_prefix(app_icon)
        github_btn = Gtk.Button()
        gh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        gh_svg = os.path.join(ASSETS_DIR, "github-mark.svg")
        if os.path.exists(gh_svg):
            gh_img = Gtk.Image.new_from_file(gh_svg)
            gh_img.set_pixel_size(16)
            gh_box.append(gh_img)
        gh_box.append(Gtk.Label(label=_("GitHub")))
        github_btn.set_child(gh_box)
        github_btn.add_css_class("flat")
        github_btn.set_valign(Gtk.Align.CENTER)
        github_btn.set_tooltip_text(_("Open Pixora's GitHub page"))
        github_btn.connect("clicked", self._on_open_github)
        app_row.add_suffix(github_btn)
        about_group.add(app_row)

        installed_version_path = os.path.join(os.path.expanduser("~"), ".config", "pixora", "installed_version")
        try:
            with open(installed_version_path) as _ivf:
                installed_ver = _ivf.read().strip()
        except Exception:
            installed_ver = _("Unknown")
        version_row = Adw.ActionRow(title=_("Version"), subtitle=installed_ver)
        about_group.add(version_row)

        self._update_check_row = Adw.ActionRow(title=_("Check for updates"))

        # Button has 4 states in a Gtk.Stack: idle / checking / uptodate /
        # available (the pulsing variant).
        self._update_check_state = "idle"
        self._update_check_pulse_id = None
        self._update_check_fade_id = None
        self._update_remote_version = None

        self._update_check_btn = Gtk.Button()
        self._update_check_btn.add_css_class("flat")
        self._update_check_btn.set_valign(Gtk.Align.CENTER)
        self._update_check_btn.set_size_request(120, 32)
        self._update_check_btn.connect("clicked", self._on_settings_check_update)

        self._update_btn_stack = Gtk.Stack()
        self._update_btn_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._update_btn_stack.set_transition_duration(250)

        idle_lbl = Gtk.Label(label=_("Check"))
        self._update_btn_stack.add_named(idle_lbl, "idle")

        spin_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0,
                           halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self._update_check_spinner = Gtk.Spinner()
        self._update_check_spinner.set_size_request(18, 18)
        spin_box.append(self._update_check_spinner)
        self._update_btn_stack.add_named(spin_box, "checking")

        ok_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        ok_icon.add_css_class("success")
        self._update_btn_stack.add_named(ok_icon, "uptodate")

        warn_icon = Gtk.Image.new_from_icon_name("software-update-available-symbolic")
        warn_icon.add_css_class("accent")
        self._update_btn_stack.add_named(warn_icon, "available")

        update_lbl = Gtk.Label(label=_("Update"))
        update_lbl.add_css_class("accent")
        self._update_btn_stack.add_named(update_lbl, "available_label")

        self._update_check_btn.set_child(self._update_btn_stack)
        self._update_check_row.add_suffix(self._update_check_btn)

        about_group.add(self._update_check_row)
        about_page.add(about_group)

        # Auto-startup-check already found an update → button pulses now.
        if self._pending_update_version:
            self._update_remote_version = self._pending_update_version
            self._update_check_row.set_subtitle(
                _("Version {v} available").format(v=self._pending_update_version)
            )
            self._set_update_state("available")

        # Credit line below the About box. An Adw.PreferencesGroup with only
        # a description (no rows) renders as a free-standing dim-label.
        credit_group = Adw.PreferencesGroup()
        credit_group.set_description(
            _("GitHub® and the Invertocat logo are trademarks of GitHub, Inc.")
            + "\n"
            + _("© {year} Pixora — LinuxGinger").format(
                year=datetime.datetime.now().year)
        )
        about_page.add(credit_group)

        license_group = Adw.PreferencesGroup()
        license_row = Adw.ActionRow(
            title=_("License"),
            subtitle=_("GNU General Public License v3.0"),
        )
        license_row.add_prefix(
            Gtk.Image.new_from_icon_name("text-x-generic-symbolic"))
        license_btn = Gtk.Button(label=_("View"))
        license_btn.add_css_class("flat")
        license_btn.set_valign(Gtk.Align.CENTER)
        license_btn.connect("clicked", self._on_view_license)
        license_row.add_suffix(license_btn)
        license_row.set_activatable_widget(license_btn)
        license_group.add(license_row)
        about_page.add(license_group)

        dialog.add(display_page)
        dialog.add(import_page)
        dialog.add(advanced_page)
        dialog.add(about_page)
        dialog.present()

    def _on_thumb_size_changed(self, scale, row):
        new_size = int(scale.get_value())
        # Snap to steps of 20.
        new_size = (new_size // 20) * 20
        row.set_subtitle(f"{new_size} px")
        self._pending_thumb_size = new_size
        if hasattr(self, "_thumb_preview"):
            try:
                self._thumb_preview.queue_draw()
            except Exception:
                pass
        if hasattr(self, "_thumb_apply_btn"):
            self._thumb_apply_btn.set_sensitive(new_size != THUMB_SIZE)

    def _draw_thumb_preview(self, area, cr, w, h):
        """Mock home-grid (portrait+landscape mix) with mini Pixora icon."""
        try:
            size = getattr(self, "_pending_thumb_size", THUMB_SIZE)
            # Clip whole canvas to a rounded rect so bg/header/tiles stay inside.
            outer_r = 10.0
            cr.new_sub_path()
            cr.arc(w - outer_r, outer_r, outer_r, -math.pi / 2, 0)
            cr.arc(w - outer_r, h - outer_r, outer_r, 0, math.pi / 2)
            cr.arc(outer_r, h - outer_r, outer_r, math.pi / 2, math.pi)
            cr.arc(outer_r, outer_r, outer_r, math.pi, 3 * math.pi / 2)
            cr.close_path()
            cr.clip()
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.08)
            cr.rectangle(0, 0, w, h)
            cr.fill()

            header_h = 14.0
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.14)
            cr.rectangle(0, 0, w, header_h)
            cr.fill()
            if not hasattr(self, "_thumb_preview_logo"):
                try:
                    icon_path = os.path.join(ASSETS_DIR, "pixora-icon.svg")
                    if os.path.exists(icon_path):
                        self._thumb_preview_logo = (
                            GdkPixbuf.Pixbuf.new_from_file_at_scale(
                                icon_path, 10, 10, True
                            )
                        )
                    else:
                        self._thumb_preview_logo = None
                except Exception:
                    self._thumb_preview_logo = None
            if self._thumb_preview_logo is not None:
                try:
                    Gdk.cairo_set_source_pixbuf(
                        cr, self._thumb_preview_logo, 4, (header_h - 10) / 2
                    )
                    cr.paint()
                except Exception:
                    pass
            # Title-stripes next to the logo for app-feel.
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.35)
            cr.rectangle(18, header_h / 2 - 1, 28, 2)
            cr.fill()

            # 12% scale reads well in the 140-wide canvas (true range 200→500 px).
            scale = 0.12
            base = size * scale
            gap = max(3.0, base * 0.08)
            pad_x = 6.0
            pad_top = header_h + 6.0
            radius = max(3.0, base * 0.06)

            # Mix of landscape / portrait / square, repeating across rows.
            pattern = [
                (1.3, 1.0), (0.75, 1.0), (1.0, 1.0),
                (0.75, 1.0), (1.3, 1.0), (1.0, 1.0),
            ]

            inner_w = w - 2 * pad_x
            inner_h = h - pad_top - pad_x
            rows_layout = []
            current_row = []
            current_w = 0.0
            idx = 0
            row_h = base
            while True:
                fw, _fh = pattern[idx % len(pattern)]
                tile_w = base * fw
                needed = current_w + tile_w + (gap if current_row else 0)
                if needed > inner_w:
                    if current_row:
                        rows_layout.append((current_row, current_w))
                    current_row, current_w = [], 0.0
                    if (len(rows_layout)) * (row_h + gap) >= inner_h:
                        break
                    continue
                current_row.append((tile_w, row_h))
                current_w += (gap if len(current_row) > 1 else 0) + tile_w
                idx += 1
                if idx > 60:
                    break
            if current_row and \
               (len(rows_layout) + 1) * (row_h + gap) - gap <= inner_h:
                rows_layout.append((current_row, current_w))

            def rounded_rect(x, y, tw, th, r):
                cr.new_sub_path()
                cr.arc(x + tw - r, y + r, r, -math.pi / 2, 0)
                cr.arc(x + tw - r, y + th - r, r, 0, math.pi / 2)
                cr.arc(x + r, y + th - r, r, math.pi / 2, math.pi)
                cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
                cr.close_path()

            y = pad_top
            for tiles, total_w in rows_layout:
                x = pad_x + (inner_w - total_w) / 2
                for tw, th in tiles:
                    cr.set_source_rgba(0.5, 0.5, 0.5, 0.35)
                    rounded_rect(x, y, tw, th, radius)
                    cr.fill()
                    x += tw + gap
                y += row_h + gap
        except Exception:
            pass

    def _on_thumb_apply_clicked(self, btn):
        new_size = getattr(self, "_pending_thumb_size", THUMB_SIZE)
        if new_size == THUMB_SIZE:
            return
        dlg = Adw.AlertDialog(
            heading=_("Change thumbnail size?"),
            body=_("Pixora will regenerate all thumbnails at {n} px. This can take a while for large libraries.").format(n=new_size),
        )
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("apply", _("Save"))
        dlg.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("apply")
        dlg.set_close_response("cancel")
        dlg.connect("response", self._on_thumb_apply_response)
        self._present_dialog(dlg)

    def _on_thumb_apply_response(self, dlg, response):
        global THUMB_SIZE
        if response != "apply":
            return
        new_size = getattr(self, "_pending_thumb_size", THUMB_SIZE)
        if new_size == THUMB_SIZE:
            return
        log_info(_("Thumbnail size changed: {old}px → {new}px").format(
            old=THUMB_SIZE, new=new_size,
        ))
        THUMB_SIZE = new_size
        self.settings["thumbnail_size"] = new_size
        save_settings(self.settings)
        if hasattr(self, "_thumb_apply_btn"):
            self._thumb_apply_btn.set_sensitive(False)
        self.load_photos()

    def _on_reset_usbmuxd(self, btn):
        log_info(_("Reset usbmuxd invoked (settings)"))
        btn.set_sensitive(False)
        btn.set_label(_("Working…"))

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
                    result_msg = _("usbmuxd restarted. Connect your iPhone and tap Trust.")
                elif r.returncode == 126 or r.returncode == 127:
                    result_msg = _("Password cancelled or pkexec unavailable.")
                else:
                    result_msg = _("Restart failed (code {code}).\n{err}").format(
                        code=r.returncode, err=r.stderr.strip()[:200]
                    )
            except FileNotFoundError:
                result_msg = _("pkexec not found. Run manually:\n  sudo killall usbmuxd; sudo usbmuxd")
            except subprocess.TimeoutExpired:
                result_msg = _("Restart timed out.")
            except Exception as e:
                result_msg = _("Unexpected error: {err}").format(err=e)
            GLib.idle_add(self._after_usbmuxd_reset, btn, ok, result_msg)

        threading.Thread(target=do, daemon=True).start()

    def _after_usbmuxd_reset(self, btn, ok, msg):
        btn.set_label(_("Restart"))
        btn.set_sensitive(True)
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("USB connection restarted") if ok else _("Restart failed"),
            body=msg
        )
        dialog.add_response("ok", _("OK"))
        dialog.present()
        if ok:
            GLib.timeout_add(500, self._poll_import_device)
        return False

    def _on_clear_pair_records(self, btn):
        log_info(_("Clear pair records — confirmation requested"))
        confirm = Adw.MessageDialog(
            transient_for=self,
            heading=_("Clear pair records?"),
            body=_("This removes all existing iPhone pairings in /var/lib/lockdown/. Your iPhone will ask for Trust again next time.")
        )
        confirm.add_response("cancel", _("Cancel"))
        confirm.add_response("clear", _("Clear"))
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
                    result_msg = _("Pair records cleared and usbmuxd restarted. Connect your iPhone and tap Trust.")
                else:
                    result_msg = _("Clear failed (code {code}).").format(code=r.returncode)
            except FileNotFoundError:
                result_msg = _("pkexec not found.")
            except Exception as e:
                result_msg = _("Error: {err}").format(err=e)
            GLib.idle_add(self._show_info_dialog,
                          _("Done") if ok else _("Failed"), result_msg)

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
        log_info(_("Language changed to: {lang} — Pixora restarting").format(lang=new_lang))

        # Overlay-tekst in de NIEUWE taal zodat de user de switch al ziet.
        try:
            new_trans = _gettext_mod.translation(
                "pixora", localedir=_LOCALE_DIR,
                languages=[new_lang], fallback=True
            )
            msg = new_trans.gettext("Changing language…")
        except Exception:
            msg = _("Changing language…")

        overlay = Gtk.Window()
        overlay.set_modal(True)
        overlay.set_transient_for(self)
        overlay.set_title("Pixora")
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

        # Sentinel + in-place exec na app.run() returnt. Betrouwbaarder dan
        # een bash-subprocess die 1.5s wacht: geen D-Bus-race, zelfde proces.
        try:
            sentinel = os.path.expanduser("~/.cache/pixora/.restart_pending")
            os.makedirs(os.path.dirname(sentinel), exist_ok=True)
            with open(sentinel, "w") as f:
                f.write("1")
        except Exception as e:
            log_error(_("Restart sentinel write failed: {err}").format(err=e))
        GLib.timeout_add(600, lambda: (self.get_application().quit(), False)[1])

    def _on_anim_toggle(self, switch, _pspec):
        # Switch is "Reduce animations" — ACTIVE means reduce → animations_enabled=False.
        reduce = switch.get_active()
        self.settings["animations_enabled"] = not reduce
        try:
            save_settings(self.settings)
        except Exception as e:
            log_error(_("Failed to save animations_enabled: {e}").format(e=e))
        self._apply_animations_state()

    def _on_gsk_renderer_changed(self, combo, _pspec):
        idx = combo.get_selected()
        if not (0 <= idx < len(self._gsk_codes)):
            return
        new_choice = self._gsk_codes[idx]
        current = self.settings.get("gsk_renderer", "auto")
        if new_choice == current:
            return
        # Blacklist-warning: a previous attempt with this renderer crashed
        # Pixora. Ask the user before we commit + restart.
        bl = self.settings.get("gsk_renderer_crashed") or []
        if new_choice in bl:
            self._gsk_prompt_blacklisted(new_choice, current)
            return
        self._apply_gsk_renderer_choice(new_choice)

    def _gsk_prompt_blacklisted(self, new_choice, current):
        nice = {"gl": "GPU (GL)", "cairo": "Software (Cairo)", "ngl": "NGL"}.get(
            new_choice, new_choice
        )
        dlg = Adw.AlertDialog(
            heading=_("Try '{r}' again?").format(r=nice),
            body=_("A previous attempt with this backend crashed Pixora and we had to revert. It may crash again. If it does, Pixora will switch back to 'Automatic' on the next start."),
        )
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("apply", _("Try anyway"))
        dlg.set_response_appearance("apply", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")
        dlg.connect(
            "response", self._on_gsk_blacklist_response, new_choice, current
        )
        try:
            self._present_dialog(dlg)
        except Exception:
            try:
                dlg.present(self)
            except Exception:
                pass

    def _on_gsk_blacklist_response(self, dlg, response, new_choice, current):
        if response != "apply":
            # Revert dropdown to what was stored — undo the visual change.
            try:
                self._gsk_combo.handler_block_by_func(self._on_gsk_renderer_changed)
                self._gsk_combo.set_selected(self._gsk_codes.index(current))
                self._gsk_combo.handler_unblock_by_func(self._on_gsk_renderer_changed)
            except Exception:
                pass
            return
        self._apply_gsk_renderer_choice(new_choice)

    def _apply_gsk_renderer_choice(self, new_choice):
        self.settings["gsk_renderer"] = new_choice
        try:
            save_settings(self.settings)
        except Exception as e:
            log_error(_("Failed to save gsk_renderer: {e}").format(e=e))
            return
        # Same sentinel-restart pattern as _on_language_changed — avoids
        # D-Bus race of a separate process.
        try:
            sentinel = os.path.expanduser("~/.cache/pixora/.restart_pending")
            os.makedirs(os.path.dirname(sentinel), exist_ok=True)
            with open(sentinel, "w") as f:
                f.write("1")
        except Exception as e:
            log_error(_("Restart sentinel write failed: {err}").format(err=e))
        # Overlay so the user gets feedback before the app blinks away.
        overlay = Gtk.Window()
        overlay.set_modal(True)
        overlay.set_transient_for(self)
        overlay.set_title("Pixora")
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
        lbl = Gtk.Label(label=_("Restarting Pixora…"))
        lbl.add_css_class("title-3")
        box.append(lbl)
        overlay.set_child(box)
        overlay.present()
        GLib.timeout_add(600, lambda: (self.get_application().quit(), False)[1])

    def _on_toggle_dev_mode(self, btn, row):
        currently_active = bool(self.settings.get("dev_mode", False))
        target = not currently_active
        if target:
            heading = _("Activate developer mode?")
            body = _("In dev mode Pixora starts in a terminal and updates go through the terminal so you can see output. Pixora restarts immediately.")
        else:
            heading = _("Deactivate developer mode?")
            body = _("Pixora will then start without a terminal and use the GUI updater.")
        dialog = Adw.MessageDialog(
            transient_for=self, heading=heading, body=body
        )
        dialog.add_response("cancel", _("No"))
        dialog.add_response("apply", _("Yes"))
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._apply_dev_mode, target, row, btn)
        dialog.present()

    def _apply_dev_mode(self, dialog, response, target, row, btn):
        if response != "apply":
            log_info(_("Dev mode toggle cancelled (was: {was})").format(
                was=self.settings.get('dev_mode', False)
            ))
            return
        self.settings["dev_mode"] = target
        save_settings(self.settings)
        log_info(_("Dev mode {state} → restarting…").format(
            state=_("activated") if target else _("deactivated")
        ))
        row.set_subtitle(_("Active") if target else _("Inactive"))
        btn.set_label(_("Deactivate") if target else _("Activate"))
        # Herstart de app
        GLib.timeout_add(300, self._restart_app)

    def _restart_app(self):
        # Sentinel + in-place exec na app.run() returnt. main.py checkt de
        # sentinel en roept os.execvp zichzelf aan — geen D-Bus-race, geen
        # bash-subprocess, en de dev-terminal-handoff blijft werken omdat
        # main.py in het nieuwe proces helemaal opnieuw begint.
        try:
            sentinel = os.path.expanduser("~/.cache/pixora/.restart_pending")
            os.makedirs(os.path.dirname(sentinel), exist_ok=True)
            with open(sentinel, "w") as f:
                f.write("1")
            log_info(_("Restart scheduled"))
        except Exception as e:
            log_error(_("Restart error: {err}").format(err=e))
        self.get_application().quit()
        return False

    def on_structure_changed(self, value, btn):
        if btn.get_active():
            self.settings["structure"] = value
            save_settings(self.settings)

    def _on_reorganize_silent_toggle(self, switch, _pspec):
        self.settings["reorganize_silent"] = bool(switch.get_active())
        save_settings(self.settings)

    def _count_media(self, paths):
        """Return (photos, videos). Accepts bare Paths or (src, dst) tuples."""
        photos = 0
        videos = 0
        for item in paths:
            src = item[0] if isinstance(item, tuple) else item
            if is_video(str(src)):
                videos += 1
            else:
                photos += 1
        return photos, videos

    def _format_media_counts(self, photos, videos):
        """Localized "3 photos and 1 video" (ngettext)."""
        parts = []
        if photos:
            parts.append(ngettext(
                "{n} photo", "{n} photos", photos).format(n=photos))
        if videos:
            parts.append(ngettext(
                "{n} video", "{n} videos", videos).format(n=videos))
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        return _("{a} and {b}").format(a=parts[0], b=parts[1])

    def _photo_date_for_structure(self, src, st, exif_tags, video_exts,
                                  video_date_fn):
        """EXIF/ffprobe first, mtime fallback. Skips get_photo_date's iPhone-
        filename heuristic deliberately — that returns a sort key, not a date."""
        ext = src.suffix.lower()
        if ext in (".jpg", ".jpeg", ".heic", ".heif", ".png", ".dng",
                   ".tiff", ".tif"):
            try:
                from PIL import Image
                with Image.open(str(src)) as img:
                    exif = img.getexif()
                # DateTimeOriginal (36867) + DateTimeDigitized (36868) live in
                # the ExifIFD sub (pointer tag 0x8769), not the main IFD.
                # Without the sub-IFD we'd only find tag 306 (ModifyDate),
                # which updates on every edit.
                try:
                    exif_sub = exif.get_ifd(0x8769)
                except Exception:
                    exif_sub = {}
                sources = []
                for tag in exif_tags:
                    if tag in (36867, 36868):
                        sources.append((exif_sub, tag))
                    else:
                        sources.append((exif, tag))
                for ifd, tag in sources:
                    val = ifd.get(tag) if ifd else None
                    if val:
                        try:
                            return datetime.datetime.strptime(
                                val[:19], "%Y:%m:%d %H:%M:%S")
                        except (ValueError, TypeError):
                            continue
            except Exception:
                pass
        elif ext in video_exts:
            try:
                ts = video_date_fn(src)
                if ts:
                    return datetime.datetime.fromtimestamp(ts)
            except Exception:
                pass
        return datetime.datetime.fromtimestamp(st.st_mtime)

    def _scan_structure_mismatch(self):
        """Return (moves, dups). moves = [(src, dst), ...] for files at a
        wrong path; dups = bit-identical duplicates at non-target paths."""
        from pathlib import Path as _P
        from importer_page import (
            dest_path as _dest_path, SUPPORTED_EXT,
            _EXIF_DATE_TAGS, _VIDEO_EXT, _get_video_date,
        )
        photo_path = _P(self.settings.get("photo_path") or _P.home() / "Photos")
        if not photo_path.is_dir():
            return [], []
        structure = self.settings.get("structure", "year_month")
        moves = []
        dups = []
        reserved_targets = set()
        for root, _dirs, files in os.walk(str(photo_path)):
            for fn in files:
                src = _P(root) / fn
                if src.suffix.lower() not in SUPPORTED_EXT:
                    continue
                try:
                    st = src.stat()
                except OSError:
                    continue
                photo_dt = self._photo_date_for_structure(
                    src, st, _EXIF_DATE_TAGS, _VIDEO_EXT, _get_video_date,
                )
                dst = _dest_path(photo_path, structure, src.name, photo_dt)
                if src.resolve() == dst.resolve():
                    reserved_targets.add(str(dst.resolve()))
                    continue
                # Target exists or already reserved by another src.
                dst_str = str(dst.resolve() if dst.exists() else dst)
                if dst.exists() or dst_str in reserved_targets:
                    try:
                        if dst.exists():
                            dst_st = dst.stat()
                            if dst_st.st_size == st.st_size \
                                    and int(dst_st.st_mtime) == int(st.st_mtime):
                                dups.append(src)
                                continue
                    except OSError:
                        pass
                    # Not identical → find a unique suffix.
                    stem = dst.stem
                    ext = dst.suffix
                    counter = 1
                    while True:
                        cand = dst.parent / f"{stem}_{counter}{ext}"
                        cand_str = str(cand.resolve() if cand.exists() else cand)
                        if not cand.exists() and cand_str not in reserved_targets:
                            dst = cand
                            break
                        counter += 1
                reserved_targets.add(str(dst.resolve() if dst.exists() else dst))
                moves.append((src, dst))
        return moves, dups

    def _prompt_reorganize(self, from_startup=False):
        """Show confirm dialog with counts. Silent when from_startup and
        nothing to do; always shows feedback otherwise."""
        self._reorganize_active = True
        def _scan():
            moves, dups = self._scan_structure_mismatch()
            GLib.idle_add(self._on_reorganize_scan_done, moves, dups, from_startup)
        threading.Thread(target=_scan, daemon=True).start()

    def _on_reorganize_scan_done(self, moves, dups, from_startup):
        if not moves and not dups:
            self._reorganize_active = False
            if not from_startup:
                dlg = Adw.AlertDialog(
                    heading=_("Folder structure is already correct"),
                    body=_("All photos already match your chosen structure."),
                )
                dlg.add_response("ok", _("OK"))
                dlg.set_default_response("ok")
                dlg.set_close_response("ok")
                self._present_dialog(dlg)
            return False
        # Silent mode: no popup, no fullscreen, run immediately. Applies to
        # both auto-detection and a manual "Opruimen" click.
        if self.settings.get("reorganize_silent", False):
            self._reorganize_silent_run = True
            log_info(_("Silent reorganize started: {m} moves, {d} dups").format(
                m=len(moves), d=len(dups)))
            threading.Thread(
                target=self._do_reorganize, args=(moves, dups),
                daemon=True,
            ).start()
            return False
        self._reorganize_silent_run = False
        structure = self.settings.get("structure", "year_month")
        structure_label = {
            "flat": _("All together"),
            "year": _("By year"),
            "year_month": _("By year and month"),
        }.get(structure, structure)
        body_lines = [
            _("Chosen structure: {s}").format(s=structure_label),
        ]
        if moves:
            ph, vi = self._count_media(moves)
            body_lines.append(_("{c} will be moved to the right folder.").format(
                c=self._format_media_counts(ph, vi)))
        if dups:
            ph, vi = self._count_media(dups)
            body_lines.append(_("{c} are exact duplicates and will be removed.").format(
                c=self._format_media_counts(ph, vi)))
        dlg = Adw.AlertDialog(
            heading=_("Reorganize folder structure?"),
            body="\n".join(body_lines),
        )
        dlg.add_response("cancel", _("Later"))
        dlg.add_response("go", _("Organize now"))
        dlg.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("go")
        dlg.set_close_response("cancel")
        try:
            dlg.set_body_use_markup(False)
        except Exception:
            pass
        dlg.connect("response", self._on_reorganize_response, moves, dups)
        # Pause any playing viewer video while the popup is up.
        self._pause_video_for_popup()
        self._present_dialog(dlg)
        return False

    def _pause_video_for_popup(self):
        try:
            media = getattr(self, "_video_media", None)
            if media is None or not media.get_playing():
                return
            if self.main_stack.get_visible_child_name() != "viewer":
                return
            media.pause()
            self._video_paused_by_popup = True
            if hasattr(self, "video_play_btn"):
                self.video_play_btn.set_icon_name(
                    "media-playback-start-symbolic")
        except Exception:
            pass

    def _resume_video_after_popup(self):
        if not self._video_paused_by_popup:
            return
        self._video_paused_by_popup = False
        try:
            media = getattr(self, "_video_media", None)
            if media is None:
                return
            media.play()
            if hasattr(self, "video_play_btn"):
                self.video_play_btn.set_icon_name(
                    "media-playback-pause-symbolic")
        except Exception:
            pass

    def _on_reorganize_response(self, dlg, response, moves, dups):
        if response != "go":
            self._reorganize_active = False
            # "Later" → no more auto-popup this session; manual still works.
            self._structure_popup_dismissed = True
            self._resume_video_after_popup()
            return
        if self._reorganize_moving:
            # Defensive: prevent double thread-start on repeated signal.
            return
        # Leave the settings dialog open — force_close/close on Adw
        # PreferencesDialog from a signal handler crashes with SIGSEGV.
        # The fullscreen page still swaps in main_stack underneath.
        threading.Thread(
            target=self._do_reorganize, args=(moves, dups), daemon=True,
        ).start()

    def _do_reorganize(self, moves, dups):
        import shutil as _sh
        from pathlib import Path as _P
        photo_path = _P(self.settings.get("photo_path") or _P.home() / "Photos")
        # Disable the file-watcher — every move would trigger reload_photos
        # and _load_thread would crash on FileNotFoundError mid-move.
        # Re-enabled in _reorganize_done.
        try:
            self.stop_watcher()
        except Exception:
            pass
        moved = 0
        removed = 0
        errors = []
        total = max(1, len(moves) + len(dups))
        # Precompute total bytes for the GB counter. Src size is what we
        # actually "process" (move or delete).
        total_bytes = 0
        sizes_moves = []
        sizes_dups = []
        for src, _dst in moves:
            try:
                sz = src.stat().st_size
            except OSError:
                sz = 0
            sizes_moves.append(sz)
            total_bytes += sz
        for dup in dups:
            try:
                sz = dup.stat().st_size
            except OSError:
                sz = 0
            sizes_dups.append(sz)
            total_bytes += sz
        self._reorganize_moving = True
        self._reorganize_fraction = 0.0
        self._reorganize_total_count = total
        self._reorganize_done_count = 0
        self._reorganize_total_bytes = total_bytes
        self._reorganize_done_bytes = 0
        self._reorganize_start_time = time.time()
        self._reorganize_current_name = ""
        tot_ph = sum(1 for s, _d in moves if not is_video(str(s)))
        tot_vi = sum(1 for s, _d in moves if is_video(str(s)))
        tot_ph += sum(1 for d in dups if not is_video(str(d)))
        tot_vi += sum(1 for d in dups if is_video(str(d)))
        self._reorganize_total_label = self._format_media_counts(tot_ph, tot_vi)
        # Silent mode: donut-only progress (no fullscreen).
        if self._reorganize_silent_run:
            GLib.idle_add(self._on_reorganize_silent_start)
        else:
            GLib.idle_add(self._on_reorganize_progress_start)
        done = 0
        moved_photos = 0
        moved_videos = 0
        removed_photos = 0
        removed_videos = 0
        first_err_logged = False
        for (src, dst), sz in zip(moves, sizes_moves):
            self._reorganize_current_name = src.name
            is_vid = is_video(str(src))
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                _sh.move(str(src), str(dst))
                moved += 1
                if is_vid:
                    moved_videos += 1
                else:
                    moved_photos += 1
            except Exception as e:
                errors.append(f"{src.name}: {e}")
                if not first_err_logged:
                    log_info(
                        f"Reorganize move failed: {src} → {dst}: {e}"
                    )
                    first_err_logged = True
            done += 1
            self._reorganize_done_count = done
            self._reorganize_done_bytes += sz
            self._reorganize_fraction = done / total
        for dup, sz in zip(dups, sizes_dups):
            self._reorganize_current_name = dup.name
            is_vid = is_video(str(dup))
            try:
                dup.unlink()
                removed += 1
                if is_vid:
                    removed_videos += 1
                else:
                    removed_photos += 1
            except OSError as e:
                errors.append(f"{dup.name}: {e}")
                if not first_err_logged:
                    log_info(f"Reorganize unlink failed: {dup}: {e}")
                    first_err_logged = True
            done += 1
            self._reorganize_done_count = done
            self._reorganize_done_bytes += sz
            self._reorganize_fraction = done / total
        # Prune empty dirs bottom-up (never the root itself).
        for root, _dirs, _files in os.walk(str(photo_path), topdown=False):
            if root == str(photo_path):
                continue
            try:
                if not os.listdir(root):
                    os.rmdir(root)
            except OSError:
                pass
        log_info(_("Reorganize: {m} moved, {r} duplicates removed, {e} errors").format(
            m=moved, r=removed, e=len(errors),
        ))
        GLib.idle_add(
            self._reorganize_done,
            moved, removed, errors,
            moved_photos, moved_videos, removed_photos, removed_videos,
        )

    def _reorganize_done(self, moved, removed, errors,
                         moved_photos=0, moved_videos=0,
                         removed_photos=0, removed_videos=0):
        self._reorganize_moving = False
        self._reorganize_fraction = 1.0
        if not self._backup_scanning and not self._backup_running:
            self._set_donuts_visible(False)
        self._redraw_donuts()
        # Re-enable file-watcher before reload_photos so future external
        # changes are picked up again.
        try:
            self.start_watcher(self.settings.get("photo_path"))
        except Exception:
            pass
        # Silent mode: reload + cooldown, no UI.
        if self._reorganize_silent_run:
            self._reorganize_silent_run = False
            self.reload_photos()
            self._reorganize_active = False
            self._reorganize_block_until = time.time() + 10.0
            GLib.timeout_add_seconds(
                10, self._maybe_trigger_backup_after_reorganize)
            return False
        # Non-silent: fullscreen stays up with stats + Close button; cleanup
        # (back-to-grid + cooldown + backup trigger) runs on close click.
        parts = []
        if moved:
            parts.append(_("{c} moved").format(
                c=self._format_media_counts(moved_photos, moved_videos)))
        if removed:
            parts.append(_("{c} removed (duplicate)").format(
                c=self._format_media_counts(removed_photos, removed_videos)))
        if not parts:
            parts.append(_("No changes needed"))
        summary = ", ".join(parts) + "."
        if errors:
            summary += "\n" + ngettext(
                "{n} error — see dev log.",
                "{n} errors — see dev log.",
                len(errors),
            ).format(n=len(errors))
        if hasattr(self, "reorganize_subtitle"):
            self.reorganize_subtitle.set_text(summary)
        if hasattr(self, "reorganize_title"):
            self.reorganize_title.set_text(_("Folder structure updated"))
        if hasattr(self, "reorganize_bar"):
            self.reorganize_bar.set_fraction(1.0)
            self.reorganize_bar.set_text("100%")
        if hasattr(self, "reorganize_detail"):
            self.reorganize_detail.set_text("")
        if hasattr(self, "reorganize_spinner"):
            self.reorganize_spinner.stop()
            self.reorganize_spinner.set_visible(False)
        if hasattr(self, "reorganize_close_btn"):
            self.reorganize_close_btn.set_visible(True)
        return False

    def _on_reorganize_close_clicked(self, _btn):
        """Close the fullscreen reorganize page: back to grid + cooldown +
        backup trigger."""
        self.header.set_visible(True)
        self.bottom_stack.set_visible(True)
        return_to = getattr(self, "_reorganize_return_page", "grid") or "grid"
        self.main_stack.set_visible_child_name(return_to)
        if hasattr(self, "reorganize_close_btn"):
            self.reorganize_close_btn.set_visible(False)
        if hasattr(self, "reorganize_spinner"):
            self.reorganize_spinner.set_visible(True)
        self.reload_photos()
        self._reorganize_active = False
        self._reorganize_block_until = time.time() + 10.0
        GLib.timeout_add_seconds(
            10, self._maybe_trigger_backup_after_reorganize)

    def _build_reorganize_page(self):
        """Fullscreen progress page, same layout as ImporterPage."""
        clamp = Adw.Clamp()
        clamp.set_maximum_size(480)
        clamp.set_valign(Gtk.Align.CENTER)
        clamp.set_vexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        box.set_margin_top(48)
        box.set_margin_bottom(48)
        box.set_margin_start(24)
        box.set_margin_end(24)

        self.reorganize_spinner = Gtk.Spinner()
        self.reorganize_spinner.set_size_request(48, 48)
        self.reorganize_spinner.set_halign(Gtk.Align.CENTER)
        box.append(self.reorganize_spinner)

        self.reorganize_title = Gtk.Label(label=_("Updating folder structure"))
        self.reorganize_title.add_css_class("title-2")
        self.reorganize_title.set_halign(Gtk.Align.CENTER)
        box.append(self.reorganize_title)

        self.reorganize_subtitle = Gtk.Label()
        self.reorganize_subtitle.add_css_class("dim-label")
        self.reorganize_subtitle.set_halign(Gtk.Align.CENTER)
        self.reorganize_subtitle.set_wrap(True)
        self.reorganize_subtitle.set_max_width_chars(52)
        box.append(self.reorganize_subtitle)

        self.reorganize_bar = Gtk.ProgressBar()
        self.reorganize_bar.set_show_text(True)
        box.append(self.reorganize_bar)

        self.reorganize_detail = Gtk.Label()
        self.reorganize_detail.add_css_class("dim-label")
        self.reorganize_detail.add_css_class("caption")
        self.reorganize_detail.set_halign(Gtk.Align.CENTER)
        self.reorganize_detail.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.reorganize_detail.set_max_width_chars(52)
        box.append(self.reorganize_detail)

        self.reorganize_close_btn = Gtk.Button(label=_("Close"))
        self.reorganize_close_btn.add_css_class("pill")
        self.reorganize_close_btn.add_css_class("suggested-action")
        self.reorganize_close_btn.set_halign(Gtk.Align.CENTER)
        self.reorganize_close_btn.set_visible(False)
        self.reorganize_close_btn.connect(
            "clicked", self._on_reorganize_close_clicked)
        box.append(self.reorganize_close_btn)

        clamp.set_child(box)
        return clamp

    def _on_reorganize_progress_start(self):
        """Main-thread: swap to fullscreen reorganize page and start the
        progress tick that periodically refreshes the labels."""
        self._reorganize_return_page = \
            self.main_stack.get_visible_child_name() or "grid"
        self.header.set_visible(False)
        self.bottom_stack.set_visible(False)
        self.main_stack.set_visible_child_name("reorganize")
        if hasattr(self, "reorganize_title"):
            self.reorganize_title.set_text(_("Updating folder structure"))
        if hasattr(self, "reorganize_spinner"):
            self.reorganize_spinner.set_visible(True)
            self.reorganize_spinner.start()
        if hasattr(self, "reorganize_close_btn"):
            self.reorganize_close_btn.set_visible(False)
        if hasattr(self, "reorganize_bar"):
            self.reorganize_bar.set_fraction(0.0)
            self.reorganize_bar.set_text("0%")
        if hasattr(self, "reorganize_subtitle"):
            self.reorganize_subtitle.set_text("")
        if hasattr(self, "reorganize_detail"):
            self.reorganize_detail.set_text("")
        # Hide donut while fullscreen page is visible.
        if not self._backup_scanning and not self._backup_running:
            self._set_donuts_visible(False)
        if self._reorganize_anim_id is None:
            self._reorganize_anim_id = GLib.timeout_add(
                200, self._tick_reorganize_progress)
        return False

    def _on_reorganize_silent_start(self):
        """Silent mode: show only the donut (no fullscreen swap); the tick
        periodically redraws it so the arc fills."""
        tip = _("Updating folder structure…")
        self._set_donuts_visible(True)
        if hasattr(self, "_backup_donut_btn"):
            self._backup_donut_btn.set_tooltip_text(tip)
        if hasattr(self, "_viewer_donut_btn"):
            self._viewer_donut_btn.set_tooltip_text(tip)
        if self._reorganize_anim_id is None:
            self._reorganize_anim_id = GLib.timeout_add(
                200, self._tick_reorganize_progress)
        self._redraw_donuts()
        return False

    def _tick_reorganize_progress(self):
        if not self._reorganize_moving:
            self._reorganize_anim_id = None
            return False
        # Fill donut (silent) and update fullscreen labels when present.
        self._redraw_donuts()
        if not hasattr(self, "reorganize_bar") \
                or self.main_stack.get_visible_child_name() != "reorganize":
            return True
        frac = max(0.0, min(1.0, self._reorganize_fraction))
        self.reorganize_bar.set_fraction(frac)
        self.reorganize_bar.set_text(f"{int(frac * 100)}%")
        done_n = self._reorganize_done_count
        total_n = self._reorganize_total_count
        done_gb = self._reorganize_done_bytes / (1024 ** 3)
        total_gb = self._reorganize_total_bytes / (1024 ** 3)
        eta = self._format_reorganize_eta(frac)
        total_label = self._reorganize_total_label or str(total_n)
        counts = _("{done} of {total_label}").format(
            done=done_n, total_label=total_label)
        subtitle = _("{counts} · {dg:.2f} / {tg:.2f} GB · about {eta} left").format(
            counts=counts, dg=done_gb, tg=total_gb, eta=eta,
        )
        self.reorganize_subtitle.set_text(subtitle)
        self.reorganize_detail.set_text(self._reorganize_current_name or "")
        return True

    def _format_reorganize_eta(self, frac):
        if frac <= 0.001:
            return _("unknown")
        elapsed = time.time() - self._reorganize_start_time
        if elapsed < 0.5:
            return _("unknown")
        remaining = max(0.0, elapsed * (1.0 - frac) / frac)
        if remaining < 60:
            secs = int(remaining)
            return ngettext("{n} second", "{n} seconds", secs).format(n=secs)
        mins = int(remaining / 60)
        return ngettext("{n} minute", "{n} minutes", mins).format(n=mins)

    def _maybe_trigger_backup_after_reorganize(self):
        """One-shot 10s after _reorganize_done: start backup-scan if idle."""
        try:
            if self._backup_running or self._backup_scanning:
                return False
            if not (self.settings.get("backup_enabled")
                    and self.settings.get("backup_uuid")
                    and self.settings.get("backup_path")):
                return False
            if self._backup_drive_mountpoint() is None:
                return False
            log_info(_("Backup scan after reorganize cooldown"))
            self._trigger_backup_scan()
        except Exception:
            pass
        return False

    def _maybe_check_structure_on_startup(self):
        """Home-grid +2s: kick off the first structure+backup scan via
        _periodic_scan; the 60s tick handles the rest."""
        if self._structure_startup_scanned:
            return False
        ready_at = getattr(self, "_home_ready_at", None)
        if ready_at is None or (time.time() - ready_at) < 2.0:
            GLib.timeout_add(500, self._maybe_check_structure_on_startup)
            return False
        self._structure_startup_scanned = True
        self._periodic_scan()
        return False

    def _trigger_structure_scan(self):
        """Silent threaded structure-scan. Callback shows the reorganize popup
        on mismatch (unless dismissed), else triggers a backup-scan."""
        if self._structure_scanning:
            return
        self._structure_scanning = True
        self._backup_scan_phase = 0.0
        tip = _("Checking folder structure…")
        if hasattr(self, "_backup_donut_btn"):
            self._set_donuts_visible(True)
            self._backup_donut_btn.set_tooltip_text(tip)
        if hasattr(self, "_viewer_donut_btn"):
            self._viewer_donut_btn.set_tooltip_text(tip)
        if self._backup_scan_anim_id is None:
            self._backup_scan_anim_id = GLib.timeout_add(
                120, self._tick_backup_scan)
        log_info(_("Structure scan started"))

        def _scan():
            try:
                moves, dups = self._scan_structure_mismatch()
            except Exception:
                moves, dups = [], []
            GLib.idle_add(self._on_periodic_structure_done, moves, dups)
        threading.Thread(target=_scan, daemon=True).start()

    def _on_periodic_structure_done(self, moves, dups):
        self._structure_scanning = False
        # Only hide donut when nothing else is running.
        if not self._backup_scanning and not self._backup_running:
            self._set_donuts_visible(False)
        self._redraw_donuts()
        had_mismatch = bool(moves or dups)
        if had_mismatch:
            log_info(_("Structure scan done: {m} moves, {d} duplicates").format(
                m=len(moves), d=len(dups),
            ))
        else:
            log_info(_("Structure scan done: structure is correct"))
        if had_mismatch and not self._structure_popup_dismissed \
                and not self._reorganize_active:
            self._reorganize_active = True
            # Silent-check is inside _on_reorganize_scan_done so both auto
            # and manual paths respect it.
            self._on_reorganize_scan_done(moves, dups, True)
            return False
        self._maybe_trigger_backup_now()
        return False

    def _maybe_trigger_backup_now(self):
        """Start backup-scan if idle, configured, drive present. Skip when
        "all synced" was just seen and nothing has imported since — otherwise
        large USBs with multi-minute rsync dry-runs would loop."""
        try:
            if self._backup_running or self._backup_scanning:
                return
            if self._reorganize_active \
                    or time.time() < self._reorganize_block_until:
                return
            if not (self.settings.get("backup_enabled")
                    and self.settings.get("backup_uuid")
                    and self.settings.get("backup_path")):
                return
            if self._backup_drive_mountpoint() is None:
                return
            # Cooldown: last scan <10min ago and no new import since → skip.
            # Manual scan and drive-attach bypass this (they call
            # _trigger_backup_scan directly).
            last_backup = self.settings.get("last_backup_time", 0) or 0
            last_import = self.settings.get("last_import_time", 0) or 0
            if last_backup and (time.time() - last_backup) < 600 \
                    and last_import <= last_backup:
                return
            self._trigger_backup_scan()
        except Exception:
            pass

    def on_threshold_changed(self, value, btn):
        if btn.get_active():
            self.settings["duplicate_threshold"] = value
            save_settings(self.settings)

    def on_dup_switch_toggled(self, switch, _pspec):
        # On = strict (1), Off = 0 (disabled)
        active = switch.get_active()
        self.settings["duplicate_threshold"] = 1 if active else 0
        if not active and self.settings.get("backup_dedup"):
            # Main detection off → USB-dedup can't be on.
            self.settings["backup_dedup"] = False
            if hasattr(self, "settings_dedup_switch"):
                self.settings_dedup_switch.set_active(False)
        save_settings(self.settings)
        if hasattr(self, "settings_dedup_row"):
            backup_on = bool(self.settings.get("backup_enabled"))
            drive_present = self._backup_drive_mountpoint() is not None
            self.settings_dedup_row.set_sensitive(backup_on and drive_present and active)

    def on_settings_backup_toggle(self, switch, _pspec):
        active = switch.get_active()
        if not active and self.settings.get("backup_enabled") \
                and self.settings.get("backup_uuid"):
            # User disables backup while config is present → confirm.
            # Can't undo the switch during signal emit; schedule on idle.
            def _confirm():
                dlg = Adw.AlertDialog(
                    heading=_("Turn off automatic backup?"),
                    body=_("Pixora will stop backing up until you turn it back on. The current settings are preserved."),
                )
                dlg.add_response("cancel", _("Cancel"))
                dlg.add_response("disable", _("Turn off"))
                dlg.set_response_appearance("disable", Adw.ResponseAppearance.DESTRUCTIVE)
                dlg.set_close_response("cancel")
                dlg.connect("response", self._on_backup_disable_response)
                self._present_dialog(dlg)
                return False
            GLib.idle_add(_confirm)
            return
        self._apply_backup_toggle(active)

    def _apply_backup_toggle(self, active):
        drive_present = self._backup_drive_mountpoint() is not None
        self.settings_drive_row.set_sensitive(active)
        self.settings_drive_combo.set_sensitive(active and drive_present)
        self.settings_backup_folder_row.set_sensitive(active and drive_present)
        if hasattr(self, "settings_mode_backup_row"):
            self.settings_mode_backup_row.set_sensitive(active and drive_present)
            self.settings_mode_sync_row.set_sensitive(active and drive_present)
        if hasattr(self, "settings_dedup_row"):
            dup_on_now = self.settings.get("duplicate_threshold", 2) != 0
            self.settings_dedup_row.set_sensitive(active and drive_present and dup_on_now)
        if hasattr(self, "settings_silent_row"):
            self.settings_silent_row.set_sensitive(active and drive_present)
        if hasattr(self, "settings_manual_scan_row"):
            self.settings_manual_scan_row.set_sensitive(active and drive_present)
        self.settings["backup_enabled"] = active
        if active:
            if not self.settings.get("backup_uuid") and self.settings_drives:
                sel = self.settings_drive_combo.get_selected()
                if 0 <= sel < len(self.settings_drives):
                    self.settings["backup_uuid"] = self.settings_drives[sel][0]
        save_settings(self.settings)
        if active:
            GLib.idle_add(self._sync_now_if_ready)

    def _on_backup_disable_response(self, dlg, response):
        if response == "disable":
            self._apply_backup_toggle(False)
        else:
            # Revert the switch without re-firing the toggle handler.
            self.settings_backup_switch.handler_block_by_func(
                self.on_settings_backup_toggle
            )
            self.settings_backup_switch.set_active(True)
            self.settings_backup_switch.handler_unblock_by_func(
                self.on_settings_backup_toggle
            )

    def on_settings_reset_drive(self, btn):
        dlg = Adw.AlertDialog(
            heading=_("Forget backup drive?"),
            body=_("The chosen drive and folder will be unlinked from Pixora. Files on the drive are left untouched."),
        )
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("reset", _("Forget"))
        dlg.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_close_response("cancel")
        dlg.connect("response", self._on_reset_drive_response)
        self._present_dialog(dlg)

    def _on_reset_drive_response(self, dlg, response):
        if response != "reset":
            return
        self.settings["backup_uuid"] = None
        self.settings["backup_label"] = None
        self.settings["backup_path"] = None
        self.settings["backup_enabled"] = False
        save_settings(self.settings)
        if hasattr(self, "settings_backup_switch"):
            self.settings_backup_switch.set_active(False)
        if hasattr(self, "settings_drive_reset_btn"):
            self.settings_drive_reset_btn.set_visible(False)
        if hasattr(self, "settings_backup_folder_row"):
            self.settings_backup_folder_row.set_subtitle(_("Not configured"))
        if hasattr(self, "settings_backup_folder_btn"):
            self.settings_backup_folder_btn.set_label(_("Set up"))
        # Rebuild drive list to remove the forgotten drive.
        if hasattr(self, "settings_drive_model"):
            while self.settings_drive_model.get_n_items() > 0:
                self.settings_drive_model.remove(0)
            self.settings_drives = self._build_settings_drive_list()
            if self.settings_drives:
                for uuid, label in self.settings_drives:
                    self.settings_drive_model.append(label)
            else:
                self.settings_drive_model.append(_("No external drives found"))

    def on_backup_dedup_toggle(self, switch, _pspec):
        self.settings["backup_dedup"] = switch.get_active()
        save_settings(self.settings)

    def on_backup_silent_toggle(self, switch, _pspec):
        active = switch.get_active()
        if active and not self.settings.get("backup_silent"):
            # User enables → confirm first; revert on cancel.
            def _confirm():
                dlg = Adw.AlertDialog(
                    heading=_("Turn on auto-confirm?"),
                    body=_("Pixora will start backup/sync immediately without a confirmation dialog when there is work to do. Only error messages will still appear."),
                )
                dlg.add_response("cancel", _("Cancel"))
                dlg.add_response("enable", _("Turn on"))
                dlg.set_response_appearance("enable", Adw.ResponseAppearance.SUGGESTED)
                dlg.set_close_response("cancel")
                dlg.connect("response", self._on_silent_confirm_response)
                self._present_dialog(dlg)
                return False
            GLib.idle_add(_confirm)
            return
        self.settings["backup_silent"] = active
        save_settings(self.settings)

    def _on_silent_confirm_response(self, dlg, response):
        if response == "enable":
            self.settings["backup_silent"] = True
            save_settings(self.settings)
        else:
            self.settings_silent_switch.handler_block_by_func(
                self.on_backup_silent_toggle
            )
            self.settings_silent_switch.set_active(False)
            self.settings_silent_switch.handler_unblock_by_func(
                self.on_backup_silent_toggle
            )

    def on_settings_manual_scan(self, btn):
        if self._backup_running or self._backup_scanning:
            log_info("Manual scan: afgewezen — backup/scan al bezig")
            return
        if not (self.settings.get("backup_enabled")
                and self.settings.get("backup_uuid")
                and self.settings.get("backup_path")):
            log_info("Manual scan: afgewezen — backup niet geconfigureerd")
            return
        if self._backup_drive_mountpoint() is None:
            log_info("Manual scan: afgewezen — drive niet gemount")
            return
        if self._reorganize_active \
                or time.time() < self._reorganize_block_until:
            # Reorganize gate blocks even an explicit user click. Log it.
            log_info("Manual scan: afgewezen — reorganize-gate actief")
            return
        log_info(_("Backup scan started manually"))
        self._manual_scan_requested = True
        self._set_manual_scan_state("checking")
        self._trigger_backup_scan()

    def _set_manual_scan_state(self, state):
        """state ∈ {'idle', 'checking', 'uptodate'}."""
        if not hasattr(self, "_scan_btn_stack"):
            return
        if self._scan_btn_fade_id is not None:
            try:
                GLib.source_remove(self._scan_btn_fade_id)
            except Exception:
                pass
            self._scan_btn_fade_id = None
        try:
            if state == "checking":
                self._scan_btn_stack.set_visible_child_name("checking")
                self._scan_check_spinner.start()
                self.settings_manual_scan_btn.set_sensitive(False)
            elif state == "uptodate":
                self._scan_check_spinner.stop()
                self._scan_btn_stack.set_visible_child_name("uptodate")
                self.settings_manual_scan_btn.set_sensitive(True)
                self._scan_btn_fade_id = GLib.timeout_add_seconds(
                    5, self._scan_fade_done
                )
            else:  # idle
                self._scan_check_spinner.stop()
                self._scan_btn_stack.set_visible_child_name("idle")
                self.settings_manual_scan_btn.set_sensitive(True)
        except Exception:
            pass

    def _scan_fade_done(self):
        self._scan_btn_fade_id = None
        try:
            if self._scan_btn_stack.get_root() is not None:
                self._set_manual_scan_state("idle")
        except Exception:
            pass
        return False

    def on_backup_mode_changed(self, mode, btn):
        if btn.get_active():
            self.settings["backup_mode"] = mode
            save_settings(self.settings)

    def _build_settings_drive_list(self):
        """List of (uuid, label). Includes the configured drive with its
        saved label even if not connected, so the combo keeps showing the name."""
        drives = list(get_available_drives())
        saved_uuid = self.settings.get("backup_uuid")
        if saved_uuid and not any(u == saved_uuid for u, _l in drives):
            label = self.settings.get("backup_label") or _("Saved backup drive")
            drives.insert(0, (saved_uuid, label))
        # Persist label when we see the drive for the first time.
        if saved_uuid and not self.settings.get("backup_label"):
            for u, lab in drives:
                if u == saved_uuid:
                    self.settings["backup_label"] = lab
                    try:
                        save_settings(self.settings)
                    except Exception:
                        pass
                    break
        return drives

    def on_settings_drive_selected(self, combo, _pspec):
        selected = combo.get_selected()
        if self.settings_drives and selected < len(self.settings_drives):
            new_uuid, new_label = self.settings_drives[selected]
            if new_uuid != self.settings.get("backup_uuid"):
                # Different drive picked → old path no longer valid.
                self.settings["backup_path"] = None
                self.settings_backup_folder_row.set_subtitle(_("Not configured"))
                self.settings_backup_folder_btn.set_label(_("Set up"))
            self.settings["backup_uuid"] = new_uuid
            self.settings["backup_label"] = new_label
            save_settings(self.settings)
            if hasattr(self, "settings_drive_reset_btn"):
                self.settings_drive_reset_btn.set_visible(True)

    def on_settings_refresh_drives(self, btn):
        # The user explicitly asked for fresh data; bypass the cache.
        _lsblk_data(force=True)
        while self.settings_drive_model.get_n_items() > 0:
            self.settings_drive_model.remove(0)
        self.settings_drives = self._build_settings_drive_list()
        backup_on = bool(self.settings.get("backup_enabled"))
        if self.settings_drives:
            for uuid, label in self.settings_drives:
                self.settings_drive_model.append(label)
        else:
            self.settings_drive_model.append(_("No external drives found"))
        saved_uuid = self.settings.get("backup_uuid")
        if saved_uuid:
            for i, (uuid, _l) in enumerate(self.settings_drives):
                if uuid == saved_uuid:
                    self.settings_drive_combo.set_selected(i)
                    break
        drive_present = self._backup_drive_mountpoint() is not None
        self.settings_drive_combo.set_sensitive(backup_on and drive_present)

    def on_settings_change_backup_folder(self, btn):
        current_uuid = self.settings.get("backup_uuid")
        mountpoint = get_mountpoint_for_uuid(current_uuid) if current_uuid else None
        if not mountpoint:
            dlg = Adw.AlertDialog(
                heading=_("No backup drive"),
                body=_("Plug in a USB drive first and select it under 'Backup drive'."),
            )
            dlg.add_response("ok", _("OK"))
            self._present_dialog(dlg)
            return
        picker = BackupFolderPicker(
            mountpoint=mountpoint,
            current_path=self.settings.get("backup_path"),
            on_selected=self._apply_backup_folder,
        )
        self._present_dialog(picker)

    def _apply_backup_folder(self, chosen):
        self.settings["backup_path"] = chosen
        save_settings(self.settings)
        self.settings_backup_folder_row.set_subtitle(chosen)
        self.settings_backup_folder_btn.set_label(_("Change"))
        GLib.idle_add(self._sync_now_if_ready)

    def _refresh_settings_drive_state(self):
        """Live-update backup section on drive attach/detach while settings
        is open. No-op if UI not built."""
        if not hasattr(self, "settings_backup_folder_row"):
            return False
        backup_on = bool(self.settings.get("backup_enabled"))
        drive_present = self._backup_drive_mountpoint() is not None
        has_uuid = bool(self.settings.get("backup_uuid"))
        try:
            self.settings_backup_folder_row.set_sensitive(backup_on and drive_present)
            self.settings_backup_folder_row.set_subtitle(
                self.settings.get("backup_path") or _("Not configured")
            )
            if hasattr(self, "settings_drive_combo"):
                self.settings_drive_combo.set_sensitive(backup_on and drive_present)
            if hasattr(self, "settings_mode_backup_row"):
                self.settings_mode_backup_row.set_sensitive(backup_on and drive_present)
                self.settings_mode_sync_row.set_sensitive(backup_on and drive_present)
            if hasattr(self, "settings_dedup_row"):
                dup_on_now = self.settings.get("duplicate_threshold", 2) != 0
                self.settings_dedup_row.set_sensitive(backup_on and drive_present and dup_on_now)
            if hasattr(self, "settings_silent_row"):
                self.settings_silent_row.set_sensitive(backup_on and drive_present)
            if hasattr(self, "settings_manual_scan_row"):
                self.settings_manual_scan_row.set_sensitive(backup_on and drive_present)
            if hasattr(self, "settings_backup_group"):
                if backup_on and has_uuid and not drive_present:
                    self.settings_backup_group.set_description(_(
                        "Drive not connected — plug in the USB drive to back up."
                    ))
                else:
                    self.settings_backup_group.set_description(
                        _("Backup to external USB drive after each import")
                    )
        except Exception:
            pass
        return False

    def _sync_now_if_ready(self):
        """Trigger a backup-diff scan once settings are complete and the
        drive is present. Called after config changes + drive-attach."""
        if self._backup_running or self._backup_scanning:
            return False
        if not (self.settings.get("backup_enabled")
                and self.settings.get("backup_uuid")
                and self.settings.get("backup_path")):
            return False
        if self._backup_drive_mountpoint() is None:
            self._show_backup_pending_banner()
            return False
        self._trigger_backup_scan()
        return False

    def _trigger_backup_scan(self):
        if self._backup_running or self._backup_scanning:
            return
        # Reorganize takes priority: popup open, scan/moves running, or within
        # 10s cooldown → skip. Periodic tick / post-cooldown timer retries.
        if self._reorganize_active or time.time() < self._reorganize_block_until:
            return
        self._backup_scanning = True
        self._backup_scan_phase = 0.0
        # No percentage during scan — a previous time-guess was misleading.
        # Popover subtitle explains USB slowness so users don't think it hung.
        self._backup_detail = _("This may take a while on a USB drive")
        tip = _("Scanning for new photos…")
        if hasattr(self, "_backup_donut_btn"):
            self._set_donuts_visible(True)
            self._backup_donut_btn.set_tooltip_text(tip)
        if hasattr(self, "_viewer_donut_btn"):
            self._viewer_donut_btn.set_tooltip_text(tip)
        if self._backup_scan_anim_id is None:
            self._backup_scan_anim_id = GLib.timeout_add(120, self._tick_backup_scan)
        log_info(_("Backup scan started"))
        threading.Thread(target=self._backup_scan_thread, daemon=True).start()

    def _tick_backup_scan(self):
        if (not self._backup_scanning
                and not self._structure_scanning
                and not self._backup_deduping
                and not self._orphan_reviewing):
            self._backup_scan_anim_id = None
            return False
        self._backup_scan_phase = (self._backup_scan_phase + 0.18) % (2 * math.pi)
        if hasattr(self, "_backup_donut"):
            self._redraw_donuts()
        return True

    def _backup_scan_thread(self):
        from pathlib import Path as _P
        photo_path = _P(self.settings.get("photo_path") or _P.home() / "Photos")
        backup_path_str = self.settings.get("backup_path")
        drive_root = self._backup_drive_mountpoint()
        if not drive_root:
            GLib.idle_add(self._handle_scan_result, None)
            return
        backup_dest = _P(backup_path_str) if backup_path_str else drive_root / "Pixora"
        try:
            backup_dest.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        mode = self.settings.get("backup_mode", "backup")

        # Pure-Python set-diff on relative paths. Previously rsync --dry-run
        # was used, but that blocks minutes on flaky USB (rsync stat's every
        # dest file for attribute preservation — much slower than needed on
        # FAT/exFAT). rsync is still used for the real backup below.
        # Dedup happens in the backup thread so the scan always finishes fast.
        src_files = {}
        try:
            for root, _dirs, files in os.walk(str(photo_path)):
                for fn in files:
                    sf = os.path.join(root, fn)
                    rel = os.path.relpath(sf, str(photo_path))
                    try:
                        src_files[rel] = os.path.getsize(sf)
                    except OSError:
                        src_files[rel] = 0
        except Exception as e:
            log_error(_("Scan error: {err}").format(err=e))
            GLib.idle_add(self._handle_scan_result, None)
            return

        dest_rels = set()
        if backup_dest.is_dir():
            try:
                for root, _dirs, files in os.walk(str(backup_dest)):
                    for fn in files:
                        df = os.path.join(root, fn)
                        dest_rels.add(os.path.relpath(df, str(backup_dest)))
            except Exception:
                pass  # partial enumeration → missing files treated as "new"

        src_rels = set(src_files.keys())
        to_transfer_rels = sorted(src_rels - dest_rels)
        orphan_rels = sorted(dest_rels - src_rels)
        new_count = len(to_transfer_rels)
        delete_count = len(orphan_rels)
        bytes_to_transfer = sum(src_files[rel] for rel in to_transfer_rels)

        GLib.idle_add(
            self._handle_scan_result,
            {
                "new": new_count,
                "bytes": bytes_to_transfer,
                "delete": delete_count,
                "orphans": orphan_rels,
                "dup_count": 0,
                "excluded": [],
                "mode": mode,
            },
        )

    def _handle_scan_result(self, result):
        self._backup_scanning = False
        if hasattr(self, "_backup_donut"):
            self._redraw_donuts()
        # Defer donut visibility until we know if silent-mode starts a
        # backup — otherwise it'd flicker off between scan-end and backup-start.
        manual_requested = self._manual_scan_requested
        self._manual_scan_requested = False
        silent = bool(self.settings.get("backup_silent"))
        if result is None:
            log_warn(_("Backup scan did not complete"))
            if hasattr(self, "_backup_donut_btn"):
                self._set_donuts_visible(self._backup_running)
            self._set_manual_scan_state("idle")
            return False
        new_count = result["new"]
        delete_count = result["delete"]
        bytes_to_transfer = result["bytes"]
        dup_count = result.get("dup_count", 0)
        mode = result.get("mode", self.settings.get("backup_mode", "backup"))
        # Remember orphan state for both modes: backup-mode uses the count
        # for the post-backup review, sync-mode uses the rel list for the
        # pre-delete review before rsync --delete.
        orphans = result.get("orphans", [])
        self._last_scan_orphan_count = delete_count if mode == "backup" else 0
        self._last_scan_orphan_rels = list(orphans)
        # Also remember backup_dest so orphan review can locate/delete files.
        drive_root = self._backup_drive_mountpoint()
        if drive_root:
            from pathlib import Path as _P
            backup_path_str = self.settings.get("backup_path")
            self._last_scan_backup_dest = (
                _P(backup_path_str) if backup_path_str
                else drive_root / "Pixora"
            )

        # In backup mode, orphans are informational (kept). For the
        # "already synced" check we ignore them.
        actionable_delete = delete_count if mode == "sync" else 0

        if new_count == 0 and actionable_delete == 0 and dup_count == 0:
            self.settings["last_backup_time"] = time.time()
            try:
                save_settings(self.settings)
            except Exception:
                pass
            self._hide_backup_pending_banner()
            log_info(_("Backup scan: everything in sync"))
            if hasattr(self, "_backup_donut_btn"):
                self._set_donuts_visible(self._backup_running)
            # Orphans in backup-mode are still worth surfacing on a manual
            # click — user explicitly asked to check.
            if delete_count > 0 and mode == "backup" and manual_requested:
                if (self.settings.get("backup_dedup")
                        and self._last_scan_orphan_rels):
                    log_info(_("Manual scan: orphan analysis started ({n} orphans)").format(n=delete_count))
                    threading.Thread(
                        target=self._review_orphans_thread, daemon=True
                    ).start()
                else:
                    self._show_orphans_only_dialog(delete_count)
            elif not silent and manual_requested:
                dlg = Adw.AlertDialog(
                    heading=_("Already in sync"),
                    body=_("Your USB drive already has the same photos as Pixora."),
                )
                dlg.add_response("ok", _("OK"))
                dlg.set_default_response("ok")
                dlg.set_close_response("ok")
                self._present_dialog(dlg)
            self._set_manual_scan_state("uptodate")
            return False
        log_info(_("Backup scan: {n} new, {d} orphans (mode={m}), {b} bytes, {u} duplicates").format(
            n=new_count, d=delete_count, m=mode,
            b=bytes_to_transfer, u=dup_count,
        ))
        # Silent-mode: skip the dialog and start backup, even on a manual
        # check — user opted into auto-confirm.
        if silent:
            log_info(_("Silent mode: backup starts automatically without dialog"))
            if hasattr(self, "_backup_donut_btn"):
                self._set_donuts_visible(True)
            self.start_backup()
            self._set_manual_scan_state("idle")
            return False
        # Non-silent: hide donut (scan done, no backup running).
        if hasattr(self, "_backup_donut_btn"):
            self._set_donuts_visible(self._backup_running)
        self._show_backup_scan_dialog(
            new_count, delete_count, bytes_to_transfer, dup_count, mode
        )
        self._set_manual_scan_state("idle")
        return False

    def _show_orphans_only_dialog(self, orphan_count):
        dlg = Adw.AlertDialog(
            heading=_("Everything in Pixora is on the USB"),
            body=ngettext(
                "There is {n} photo on the USB that is no longer in Pixora. In Backup mode it stays as an archive.",
                "There are {n} photos on the USB that are no longer in Pixora. In Backup mode they stay as an archive.",
                orphan_count,
            ).format(n=orphan_count),
        )
        dlg.add_response("ok", _("OK"))
        dlg.set_default_response("ok")
        dlg.set_close_response("ok")
        self._present_dialog(dlg)

    def _format_bytes(self, n):
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                if unit in ("B", "KB"):
                    return f"{int(n)} {unit}"
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    def _format_eta(self, bytes_total, bytes_per_sec=30 * 1024 * 1024):
        if bytes_total <= 0:
            return _("less than a minute")
        secs = bytes_total / bytes_per_sec
        if secs < 60:
            return _("less than a minute")
        mins = int(secs / 60 + 0.5)
        if mins < 60:
            return ngettext("± {n} minute", "± {n} minutes", mins).format(n=mins)
        hours = mins // 60
        rem = mins % 60
        if rem == 0:
            return ngettext("± {n} hour", "± {n} hours", hours).format(n=hours)
        return _("± {h} h {m} min").format(h=hours, m=rem)

    def _show_backup_scan_dialog(self, new_count, delete_count, bytes_to_transfer,
                                 dup_count=0, mode="backup"):
        if self._backup_scan_dialog_open:
            return
        # Defer during startup (home-grid <2s visible) so the popup doesn't
        # land on a half-loaded UI.
        ready_at = getattr(self, "_home_ready_at", None)
        if ready_at is None or (time.time() - ready_at) < 2.0:
            GLib.timeout_add(500, self._show_backup_scan_dialog,
                             new_count, delete_count, bytes_to_transfer,
                             dup_count, mode)
            return
        self._backup_scan_dialog_open = True
        lines = []
        if new_count > 0:
            lines.append(ngettext(
                "{n} new photo (±{s})",
                "{n} new photos (±{s})",
                new_count,
            ).format(n=new_count, s=self._format_bytes(bytes_to_transfer)))
        if delete_count > 0:
            if mode == "sync":
                lines.append(ngettext(
                    "{n} stale file will be removed from USB",
                    "{n} stale files will be removed from USB",
                    delete_count,
                ).format(n=delete_count))
            else:
                # backup-mode: orphans are kept — purely informational.
                lines.append(ngettext(
                    "{n} photo on USB no longer in Pixora (kept)",
                    "{n} photos on USB no longer in Pixora (kept)",
                    delete_count,
                ).format(n=delete_count))
        if dup_count > 0:
            lines.append(ngettext(
                "{n} duplicate will be skipped",
                "{n} duplicates will be skipped",
                dup_count,
            ).format(n=dup_count))
        lines.append(_("Estimated time: {eta}").format(
            eta=self._format_eta(bytes_to_transfer)
        ))
        body = "\n".join(lines)
        if mode == "sync":
            heading = _("Sync ready to start")
            now_label = _("Sync now")
        else:
            heading = _("Backup ready to start")
            now_label = _("Back up now")
        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.add_response("later", _("Later"))
        dlg.add_response("now", now_label)
        dlg.set_response_appearance("now", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("now")
        dlg.set_close_response("later")
        dlg.connect("response", self._on_scan_dialog_response)
        self._present_dialog(dlg)

    def _on_scan_dialog_response(self, dlg, response):
        self._backup_scan_dialog_open = False
        if response == "now":
            self.start_backup()
        else:
            self._show_backup_pending_banner()

    def change_folder(self, parent_dialog):
        file_dialog = Gtk.FileDialog()
        file_dialog.set_title(_("Choose photo folder"))
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
                self._filmstrip_order_cache = None
                self.load_photos()
        except Exception:
            pass

    def open_importer(self, btn=None):
        log_info(_("Importer opened (iOS device {state})").format(
            state=_("present") if self._ios_device_present else _("not detected")
        ))
        self.header.set_visible(False)
        self.bottom_stack.set_visible(False)
        self.main_stack.set_visible_child_name("importer")
        self.importer_page.activate()

    def close_importer(self):
        log_info(_("Importer closed"))
        self.importer_page.deactivate()
        self.header.set_visible(True)
        self.bottom_stack.set_visible(True)
        self.main_stack.set_visible_child_name("grid")

    def on_import_done(self, count):
        self.close_importer()
        if count and count > 0:
            self.reload_photos()
            # Mark pending-backup by bumping last_import_time.
            self.settings["last_import_time"] = time.time()
            try:
                save_settings(self.settings)
            except Exception:
                pass
            GLib.idle_add(self._check_pending_backup)

    def _is_backup_pending(self):
        """True if there's been an import since the last backup."""
        if not self.settings.get("backup_enabled"):
            return False
        if not self.settings.get("backup_uuid"):
            return False
        last_import = self.settings.get("last_import_time", 0) or 0
        last_backup = self.settings.get("last_backup_time", 0) or 0
        return last_import > last_backup

    def _backup_drive_mountpoint(self):
        """Return Path of the backup drive when attached, else None."""
        uuid = self.settings.get("backup_uuid")
        if not uuid:
            return None
        mp = get_mountpoint_for_uuid(uuid)
        if not mp:
            return None
        try:
            from pathlib import Path as _P
            return _P(mp)
        except Exception:
            return None

    def _check_pending_backup(self):
        """After import or drive-attach: scan; the scan itself shows a dialog
        on a non-empty diff, silent otherwise."""
        if self._backup_running or self._backup_scanning:
            return False
        if not (self.settings.get("backup_enabled")
                and self.settings.get("backup_uuid")
                and self.settings.get("backup_path")):
            return False
        drive = self._backup_drive_mountpoint()
        if drive:
            self._trigger_backup_scan()
        else:
            self._show_backup_pending_banner()
        return False

    def _present_dialog(self, dlg):
        """Parent the popup on the settings dialog if open, else on self.
        Without this, the popup lands behind a modal settings and blocks
        the taskbar. Never call self.present() — GNOME Shell then fires a
        "Pixora is ready" notification and adds a taskbar badge."""
        parent = self._settings_dialog if self._settings_dialog is not None else self
        try:
            dlg.present(parent)
        except Exception:
            try:
                dlg.present(self)
            except Exception:
                pass

    def _show_backup_pending_banner(self):
        # Banner disabled — feedback now lives in the settings UI.
        try:
            self.backup_pending_banner.set_revealed(False)
        except Exception:
            pass

    def _hide_backup_pending_banner(self):
        try:
            self.backup_pending_banner.set_revealed(False)
        except Exception:
            pass

    def _periodic_scan(self):
        """60s tick: structure-scan first (works without a backup drive);
        backup-scan runs via _on_periodic_structure_done once structure's done."""
        try:
            ready_at = getattr(self, "_home_ready_at", None)
            if ready_at is None or (time.time() - ready_at) < 2.0:
                return True
            if self._reorganize_active \
                    or time.time() < self._reorganize_block_until:
                return True
            if self._structure_scanning \
                    or self._backup_scanning or self._backup_running:
                return True
            self._trigger_structure_scan()
        except Exception:
            pass
        return True

    def _poll_backup_drive(self):
        """Drive attach/detach → UI update. Attach: scan for diff; detach:
        grey out settings-backup + show banner."""
        # Force-refresh lsblk so cache never lies about attach/detach state.
        _lsblk_data(force=True)
        drive_now = self._backup_drive_mountpoint() is not None
        just_attached = drive_now and not self._backup_drive_last_seen
        just_detached = (not drive_now) and self._backup_drive_last_seen
        self._backup_drive_last_seen = drive_now
        backup_configured = bool(
            self.settings.get("backup_enabled")
            and self.settings.get("backup_uuid")
            and self.settings.get("backup_path")
        )
        if just_attached and backup_configured \
                and not self._backup_running \
                and not self._backup_scanning:
            GLib.idle_add(self._trigger_backup_scan)
        if (not drive_now and self._is_backup_pending()
                and not self._backup_running):
            self._show_backup_pending_banner()
        if just_attached or just_detached:
            GLib.idle_add(self._refresh_settings_drive_state)
        return True

    def _prompt_backup_on_insert(self):
        if self._backup_running:
            return False
        dlg = Adw.AlertDialog(
            heading=_("Backup drive detected"),
            body=_("Your USB drive is connected. New photos are ready to be backed up. Start now?"),
        )
        dlg.add_response("later", _("Later"))
        dlg.add_response("start", _("Back up now"))
        dlg.set_response_appearance("start", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", self._on_backup_prompt_response)
        self._present_dialog(dlg)
        return False

    def _on_backup_prompt_response(self, dlg, response):
        if response == "start":
            self.start_backup()

    def _on_close_guard_response(self, dlg, response):
        if response == "close":
            self._close_confirmed = True
            self.close()

    def start_backup(self):
        if self._backup_running:
            return
        self._backup_running = True
        self._backup_fraction = 0.0
        self._backup_detail = ""
        self._hide_backup_pending_banner()
        log_info(_("Backup started"))
        threading.Thread(target=self._backup_thread, daemon=True).start()

    def _check_backup_dest_writable(self, drive_root, backup_dest):
        """Return None if writable, else a clear error with a fix hint.
        Tries dest first, then drive root — detects read-only even when
        the dest folder doesn't exist yet."""
        candidates = [p for p in (backup_dest, drive_root)
                      if p is not None and os.path.isdir(str(p))]
        if not candidates:
            return None
        test_dir = str(candidates[0])
        test_path = os.path.join(test_dir, f".pixora-write-test-{os.getpid()}")
        try:
            with open(test_path, "wb") as f:
                f.write(b"ok")
            os.remove(test_path)
            return None
        except OSError as exc:
            if exc.errno == 30:  # EROFS — read-only filesystem
                return _(
                    "Backup drive is mounted read-only. This often happens with NTFS/exFAT after an unsafe eject on Windows.\n\nFix: unmount the drive in your file manager and reconnect it, or run in a terminal:\n  sudo ntfsfix /dev/sdXN\n(replace sdXN with your USB device, e.g. sdb1)"
                )
            if exc.errno == 13:  # EACCES
                return _("No write permission on {p}: {err}").format(
                    p=test_dir, err=exc
                )
            return _("Error writing to {p}: {err}").format(
                p=test_dir, err=exc
            )

    def _dedup_for_backup(self, photo_path, backup_dest):
        """Return relative paths (from photo_path) to exclude from the backup
        because they already exist on USB visually. Runs in the backup thread
        before rsync so scans stay fast; popover shows "Duplicaat-check: X/Y"."""
        if not self.settings.get("backup_dedup"):
            return []

        # Filename-based set-diff; dedup only checks files not already on USB.
        src_rels = set()
        try:
            for root, _dirs, files in os.walk(str(photo_path)):
                for fn in files:
                    src_rels.add(os.path.relpath(
                        os.path.join(root, fn), str(photo_path)))
        except Exception:
            return []
        dest_rels = set()
        if backup_dest.is_dir():
            try:
                for root, _dirs, files in os.walk(str(backup_dest)):
                    for fn in files:
                        dest_rels.add(os.path.relpath(
                            os.path.join(root, fn), str(backup_dest)))
            except Exception:
                pass
        to_check = sorted(src_rels - dest_rels)
        if not to_check or not dest_rels:
            return []

        # 10% threshold: USB must hold a substantial library before pHash
        # work is statistically worth it.
        if len(dest_rels) * 10 < len(src_rels):
            log_info(_("Duplicate check skipped: USB is too empty ({d} photos vs {s} in Pixora)").format(
                d=len(dest_rels), s=len(src_rels)))
            return []

        log_info(_("Duplicate check: {n} new photos against {u} on USB").format(n=len(to_check), u=len(dest_rels)))
        try:
            from importer_page import (
                perceptual_hash, build_library_hashes, find_duplicate,
                SUPPORTED_EXT,
            )
        except Exception as e:
            log_warn(_("Dedup check skipped: {err}").format(err=e))
            return []

        self._backup_deduping = True

        def _start_dedup_ui():
            tip = _("Duplicate check…")
            if hasattr(self, "_backup_donut_btn"):
                try:
                    self._set_donuts_visible(True)
                    self._backup_donut_btn.set_tooltip_text(tip)
                except Exception:
                    pass
            if hasattr(self, "_viewer_donut_btn"):
                try:
                    self._viewer_donut_btn.set_tooltip_text(tip)
                except Exception:
                    pass
            # Reuse the scan animation for the donut spinner.
            if self._backup_scan_anim_id is None:
                self._backup_scan_anim_id = GLib.timeout_add(
                    80, self._tick_backup_scan)
            return False
        GLib.idle_add(_start_dedup_ui)

        excluded = []
        try:
            usb_hashes = build_library_hashes(backup_dest)
            MAX_DIST = 2
            total = len(to_check)
            for i, rel in enumerate(to_check):
                if i % 25 == 0:
                    msg = _("Duplicate check: {c} / {t}").format(c=i, t=total)
                    GLib.idle_add(self._set_dedup_detail, msg)
                src_file = photo_path / rel
                if not src_file.is_file():
                    continue
                if src_file.suffix.lower() not in SUPPORTED_EXT:
                    continue
                ph = perceptual_hash(src_file)
                if ph and find_duplicate(ph, usb_hashes, MAX_DIST):
                    excluded.append(rel)
        except Exception as e:
            log_warn(_("Dedup check skipped: {err}").format(err=e))
        finally:
            self._backup_deduping = False
            GLib.idle_add(self._set_dedup_detail, "")
        if excluded:
            log_info(_("Duplicate check done: {n} duplicates will be skipped").format(n=len(excluded)))
        return excluded

    def _set_dedup_detail(self, text):
        """Thread-safe setter for popover subtitle during dedup."""
        self._backup_detail = text or ""
        if hasattr(self, "_backup_donut"):
            self._redraw_donuts()
        return False

    def _backup_thread(self):
        from pathlib import Path as _P
        photo_path = _P(self.settings.get("photo_path") or _P.home() / "Photos")
        backup_uuid = self.settings.get("backup_uuid")
        backup_path_str = self.settings.get("backup_path")
        drive_root = self._backup_drive_mountpoint()
        if not drive_root:
            GLib.idle_add(self._backup_finished, False, _("Backup drive not found."))
            return
        backup_dest = _P(backup_path_str) if backup_path_str else drive_root / "Pixora"
        # Common case: NTFS/exFAT drive mounted read-only after unsafe eject
        # on Windows. Tell the user early instead of crashing in rsync.
        ro_msg = self._check_backup_dest_writable(drive_root, backup_dest)
        if ro_msg is not None:
            GLib.idle_add(self._backup_finished, False, ro_msg)
            return
        try:
            backup_dest.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            GLib.idle_add(self._backup_finished, False, _("Error: {err}").format(err=e))
            return

        def _on_rsync_line(line):
            for part in line.split():
                if part.endswith("%"):
                    try:
                        frac = int(part[:-1]) / 100.0
                        GLib.idle_add(self._update_backup_progress, frac, part)
                    except ValueError:
                        pass

        mode = self.settings.get("backup_mode", "backup")
        # Dedup runs here (before rsync) so the scan dialog is always instant;
        # pHash runs in the backup phase with visible progress in the donut.
        excluded = self._dedup_for_backup(photo_path, backup_dest)
        exclude_file = None
        success = False
        if _cmd_available_bk("rsync"):
            proc = None
            try:
                rsync_cmd = ["rsync", "-a", "--info=progress2",
                             "--modify-window=2"]
                if mode == "sync":
                    rsync_cmd.append("--delete")
                if excluded:
                    import tempfile
                    exclude_file = tempfile.NamedTemporaryFile(
                        mode="w", suffix=".rsync-exclude", delete=False
                    )
                    for rel in excluded:
                        exclude_file.write(f"/{rel}\n")
                    exclude_file.close()
                    rsync_cmd += [f"--exclude-from={exclude_file.name}"]
                rsync_cmd += [str(photo_path) + "/", str(backup_dest) + "/"]
                proc = subprocess.Popen(
                    rsync_cmd,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True
                )
                self._backup_proc = proc
                for line in proc.stdout:
                    _on_rsync_line(line)
                _err = proc.stderr.read() if proc.stderr else ""
                proc.wait(timeout=3600)
                success = proc.returncode == 0
                rsync_err = _err.strip()
            except Exception as e:
                log_error(_("Backup error: {err}").format(err=e))
                success = False
                rsync_err = str(e)
            finally:
                if proc is not None and proc.poll() is None:
                    try:
                        proc.kill()
                        proc.wait(timeout=5)
                    except Exception:
                        pass
                self._backup_proc = None
                if exclude_file is not None:
                    try:
                        os.unlink(exclude_file.name)
                    except OSError:
                        pass
        else:
            success = self._manual_backup(
                photo_path, backup_dest, mode == "sync", excluded=set(excluded)
            )
            rsync_err = ""

        note = None
        if not success:
            err_low = (rsync_err or "").lower()
            if "read-only" in err_low or "readonly" in err_low \
                    or "permission denied" in err_low:
                note = _(
                    "USB is mounted read-only. Unmount and reconnect it, or run `sudo ntfsfix /dev/sdXN` in a terminal."
                )
            elif rsync_err:
                # First line of rsync stderr usually holds the real reason.
                first = rsync_err.splitlines()[0] if rsync_err else ""
                note = _("Backup failed: {err}").format(err=first[:200])
            else:
                note = _("Backup partially failed.")
        GLib.idle_add(self._backup_finished, success, note)

    def _manual_backup(self, src, dst, delete_extraneous=False, excluded=None):
        """Fallback without rsync: copy manually with progress.
        delete_extraneous=True → sync: remove dst files not in src.
        excluded = set of src-relative paths to skip (dedup matches)."""
        excluded = excluded or set()
        try:
            all_src = []
            for root, _, files in os.walk(src):
                for fn in files:
                    all_src.append(os.path.join(root, fn))
            total = len(all_src)
            self._backup_total = total
            src_rels = set()
            for i, sf in enumerate(all_src):
                rel = os.path.relpath(sf, str(src))
                src_rels.add(rel)
                if rel in excluded:
                    continue
                df = os.path.join(str(dst), rel)
                os.makedirs(os.path.dirname(df), exist_ok=True)
                if not os.path.exists(df):
                    import shutil as _sh
                    _sh.copy2(sf, df)
                frac = (i + 1) / total if total > 0 else 1.0
                self._backup_done = i + 1
                GLib.idle_add(self._update_backup_progress, frac,
                              f"{i + 1} / {total}")
            if delete_extraneous:
                for root, _dirs, files in os.walk(str(dst)):
                    for fn in files:
                        df = os.path.join(root, fn)
                        rel = os.path.relpath(df, str(dst))
                        if rel not in src_rels:
                            try:
                                os.remove(df)
                            except Exception:
                                pass
            return True
        except Exception as e:
            log_error(_("Backup error: {err}").format(err=e))
            return False

    def _update_backup_progress(self, fraction, detail):
        self._backup_fraction = max(0.0, min(1.0, fraction))
        self._backup_detail = detail or ""
        if hasattr(self, "_backup_donut"):
            self._redraw_donuts()
        if hasattr(self, "_backup_donut_btn"):
            try:
                self._set_donuts_visible(self._backup_running)
                tip = _("Backup: {pct}%").format(
                    pct=int(self._backup_fraction * 100)
                )
                self._backup_donut_btn.set_tooltip_text(tip)
                if hasattr(self, "_viewer_donut_btn"):
                    self._viewer_donut_btn.set_tooltip_text(tip)
            except Exception:
                pass
        return False

    def _set_donuts_visible(self, visible):
        """Keep header and viewer-overlay donut visibility in sync."""
        for name in ("_backup_donut_btn", "_viewer_donut_btn"):
            btn = getattr(self, name, None)
            if btn is not None:
                try:
                    btn.set_visible(bool(visible))
                except Exception:
                    pass

    def _redraw_donuts(self):
        for name in ("_backup_donut", "_viewer_donut"):
            area = getattr(self, name, None)
            if area is not None:
                try:
                    area.queue_draw()
                except Exception:
                    pass

    def _draw_backup_donut(self, area, cr, w, h):
        """Pie chart filling 0°→progress×360°. While scanning, a rotating
        quarter-arc serves as indeterminate indicator."""
        try:
            cx = w / 2
            cy = h / 2
            r_outer = min(w, h) / 2 - 1
            r_inner = r_outer * 0.55

            cr.set_source_rgba(0.5, 0.5, 0.5, 0.3)
            cr.arc(cx, cy, r_outer, 0, 2 * math.pi)
            cr.arc_negative(cx, cy, r_inner, 2 * math.pi, 0)
            cr.fill()

            # Orange when backup-related; dark blue for structure/reorganize
            # (no-backup context).
            if (self._backup_scanning or self._backup_running
                    or self._orphan_reviewing):
                color = (0.914, 0.329, 0.125)  # orange
            else:
                color = (0.10, 0.20, 0.55)  # dark blue

            if (self._backup_scanning or self._structure_scanning
                    or self._backup_deduping or self._orphan_reviewing):
                start = self._backup_scan_phase - math.pi / 2
                end = start + math.pi / 2  # quarter arc
                cr.set_source_rgb(*color)
                cr.move_to(cx, cy)
                cr.arc(cx, cy, r_outer, start, end)
                cr.close_path()
                cr.fill()
                cr.set_operator(cairo.OPERATOR_CLEAR)
                cr.arc(cx, cy, r_inner, 0, 2 * math.pi)
                cr.fill()
                cr.set_operator(cairo.OPERATOR_OVER)
                return

            # Progress mode: reorganize has priority, else backup.
            if self._reorganize_moving:
                frac = max(0.0, min(1.0, self._reorganize_fraction))
            else:
                frac = max(0.0, min(1.0, self._backup_fraction))
            if frac > 0.001:
                start = -math.pi / 2  # 12 o'clock
                end = start + frac * 2 * math.pi
                cr.set_source_rgb(*color)
                cr.move_to(cx, cy)
                cr.arc(cx, cy, r_outer, start, end)
                cr.close_path()
                cr.fill()
                cr.set_operator(cairo.OPERATOR_CLEAR)
                cr.arc(cx, cy, r_inner, 0, 2 * math.pi)
                cr.fill()
                cr.set_operator(cairo.OPERATOR_OVER)
        except Exception:
            pass

    def _on_backup_donut_clicked(self, btn):
        """Donut click → live-updating popover. Labels are stored as attrs
        so _refresh_donut_popover can rewrite them in place."""
        pop = Gtk.Popover()
        pop.set_parent(btn)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8); box.set_margin_bottom(8)
        box.set_margin_start(12); box.set_margin_end(12)
        self._donut_pop_title = Gtk.Label()
        self._donut_pop_title.add_css_class("heading")
        self._donut_pop_title.set_halign(Gtk.Align.START)
        box.append(self._donut_pop_title)
        self._donut_pop_pct = Gtk.Label()
        self._donut_pop_pct.set_halign(Gtk.Align.START)
        box.append(self._donut_pop_pct)
        self._donut_pop_detail = Gtk.Label()
        self._donut_pop_detail.add_css_class("caption")
        self._donut_pop_detail.add_css_class("dim-label")
        self._donut_pop_detail.set_halign(Gtk.Align.START)
        box.append(self._donut_pop_detail)
        pop.set_child(box)
        self._donut_popover = pop
        self._refresh_donut_popover()
        # Refresh every 250ms while visible.
        self._donut_pop_tick_id = GLib.timeout_add(
            250, self._refresh_donut_popover)
        def _on_closed(_p):
            tid = getattr(self, "_donut_pop_tick_id", None)
            if tid is not None:
                try:
                    GLib.source_remove(tid)
                except Exception:
                    pass
                self._donut_pop_tick_id = None
            self._donut_popover = None
        pop.connect("closed", _on_closed)
        pop.popup()

    def _refresh_donut_popover(self):
        pop = getattr(self, "_donut_popover", None)
        if pop is None or not hasattr(self, "_donut_pop_title"):
            return False
        is_sync = self.settings.get("backup_mode", "backup") == "sync"
        # Pick title + visible fields based on what's running.
        if self._reorganize_moving:
            title = _("Updating folder structure")
            pct_val = int(max(0.0, min(1.0, self._reorganize_fraction)) * 100)
            pct = f"{pct_val}%"
            detail = self._reorganize_current_name or ""
        elif self._structure_scanning:
            title = _("Checking folder structure…")
            pct = ""
            detail = ""
        elif self._backup_deduping:
            title = _("Duplicate check…")
            pct = ""
            detail = (self._backup_detail or "").strip()
        elif self._orphan_reviewing:
            title = _("Analyzing for duplicate copies…")
            pct = ""
            detail = (self._backup_detail or "").strip()
        elif self._backup_running and not self._backup_scanning:
            title = _("Sync in progress") if is_sync else _("Backup in progress")
            pct = f"{int(self._backup_fraction * 100)}%"
            d = (self._backup_detail or "").strip()
            detail = "" if (d and d.rstrip("%").strip().isdigit()) else d
        elif self._backup_scanning:
            title = _("Scanning sync…") if is_sync else _("Scanning backup…")
            pct = ""
            detail = (self._backup_detail or "").strip()
            if detail and detail.rstrip("%").strip().isdigit():
                detail = ""
        else:
            title = _("Sync in progress") if is_sync else _("Backup in progress")
            pct = ""
            detail = ""
        self._donut_pop_title.set_text(title)
        self._donut_pop_pct.set_text(pct)
        self._donut_pop_pct.set_visible(bool(pct))
        self._donut_pop_detail.set_text(detail)
        self._donut_pop_detail.set_visible(bool(detail))
        return True

    def _backup_finished(self, success, note):
        self._backup_running = False
        if success:
            self.settings["last_backup_time"] = time.time()
            try:
                save_settings(self.settings)
            except Exception:
                pass
            log_info(_("Backup complete"))
        else:
            log_warn(_("Backup failed: {note}").format(note=note or ""))
        if hasattr(self, "_backup_donut_btn"):
            try:
                self._set_donuts_visible(False)
            except Exception:
                pass
        self._show_backup_done_banner(success, note)
        return False

    def _show_backup_done_banner(self, success, note):
        # Banner disabled — done feedback is a popup (same style as start
        # dialog); heading toggles by backup_mode ("Sync voltooid" etc.).
        try:
            self.backup_done_banner.set_revealed(False)
        except Exception:
            pass
        orphan_count = self._last_scan_orphan_count
        mode = self.settings.get("backup_mode", "backup")
        dedup_on = bool(self.settings.get("backup_dedup"))
        has_orphans = (success and mode == "backup" and orphan_count > 0
                       and self._last_scan_orphan_rels)

        # Orphans + dedup on → run pHash analysis + choice dialog (replaces
        # the done popup). Without dedup, just a simple "heads up" message.
        if has_orphans and dedup_on:
            log_info(_("Backup complete, orphan analysis started ({n} orphans)").format(n=orphan_count))
            threading.Thread(
                target=self._review_orphans_thread, daemon=True
            ).start()
            return

        # Silent-mode: skip success popup, keep error popups. Exception:
        # orphans in backup-mode without dedup → still an info popup so the
        # user knows something unexpected sits on the USB.
        if success and self.settings.get("backup_silent"):
            if has_orphans:
                log_info(_("Silent mode: backup complete, {n} orphans reported").format(n=orphan_count))
                dlg = Adw.AlertDialog(
                    heading=_("Backup complete — note"),
                    body=ngettext(
                        "There is {n} photo on your USB that is not in Pixora (e.g. a manually copied folder). It is kept as an archive.",
                        "There are {n} photos on your USB that are not in Pixora (e.g. a manually copied folder). They are kept as an archive.",
                        orphan_count,
                    ).format(n=orphan_count),
                )
                dlg.add_response("ok", _("OK"))
                dlg.set_default_response("ok")
                dlg.set_close_response("ok")
                self._present_dialog(dlg)
                return
            log_info(_("Silent mode: backup completed without popup"))
            return

        if success:
            if mode == "sync":
                heading = _("Sync complete")
                body = _("Your USB drive is now identical to Pixora.")
            elif has_orphans:
                heading = _("Backup complete — note")
                body = ngettext(
                    "All photos from Pixora are on the USB. Note: there is {n} photo on your USB that is not in Pixora (kept as an archive).",
                    "All photos from Pixora are on the USB. Note: there are {n} photos on your USB that are not in Pixora (kept as an archive).",
                    orphan_count,
                ).format(n=orphan_count)
            else:
                heading = _("Backup complete")
                body = _("All photos are on the USB drive.")
            dlg = Adw.AlertDialog(heading=heading, body=body)
        else:
            if mode == "sync":
                heading = _("Sync failed")
            else:
                heading = _("Backup failed")
            dlg = Adw.AlertDialog(
                heading=heading,
                body=note or _("Unknown error."),
            )
        dlg.add_response("ok", _("OK"))
        dlg.set_default_response("ok")
        dlg.set_close_response("ok")
        self._present_dialog(dlg)

    def _categorize_orphans(self, photo_path, backup_dest, orphan_rels,
                            progress_cb=None):
        """pHash-match every orphan against the Pixora library. Returns
        (dup_rels, unique_rels). progress_cb(cur, total, phase) where
        phase='build' (hash-cache build) or phase='check' (comparing).
        On import/hash error, everything falls into unique_rels — safer
        default is not to flag as duplicate when unsure."""
        try:
            from importer_page import (
                perceptual_hash, build_library_hashes, find_duplicate,
                SUPPORTED_EXT,
            )
        except Exception as e:
            log_warn(_("Orphan analysis skipped: {err}").format(err=e))
            return [], list(orphan_rels)

        # Throttle build_library_hashes progress to every 25 for calm UI.
        def _build_progress(i, total, _name):
            if progress_cb and i % 25 == 0:
                try:
                    progress_cb(i, total, "build")
                except Exception:
                    pass
        try:
            src_hashes = build_library_hashes(
                photo_path, progress_cb=_build_progress)
        except Exception as e:
            log_warn(_("Orphan analysis skipped: {err}").format(err=e))
            return [], list(orphan_rels)

        dup_rels, unique_rels = [], []
        MAX_DIST = 2
        total = len(orphan_rels)
        for i, rel in enumerate(orphan_rels):
            if progress_cb and i % 25 == 0:
                try:
                    progress_cb(i, total, "check")
                except Exception:
                    pass
            orph_file = backup_dest / rel
            if not orph_file.is_file():
                continue
            if orph_file.suffix.lower() not in SUPPORTED_EXT:
                unique_rels.append(rel)
                continue
            ph = perceptual_hash(orph_file)
            if ph and find_duplicate(ph, src_hashes, MAX_DIST):
                dup_rels.append(rel)
            else:
                unique_rels.append(rel)
        return dup_rels, unique_rels

    def _start_orphan_review_ui(self):
        """Enable donut spinner + tooltip for the pHash phase."""
        tip = _("Analyzing for duplicate copies…")
        if hasattr(self, "_backup_donut_btn"):
            try:
                self._set_donuts_visible(True)
                self._backup_donut_btn.set_tooltip_text(tip)
            except Exception:
                pass
        if hasattr(self, "_viewer_donut_btn"):
            try:
                self._viewer_donut_btn.set_tooltip_text(tip)
            except Exception:
                pass
        if self._backup_scan_anim_id is None:
            self._backup_scan_anim_id = GLib.timeout_add(
                120, self._tick_backup_scan)
        return False

    def _review_orphans_thread(self):
        """After a backup: pHash-categorize orphans on USB, then show the
        choice dialog."""
        from pathlib import Path as _P
        photo_path = _P(self.settings.get("photo_path") or _P.home() / "Photos")
        backup_dest = self._last_scan_backup_dest
        orphan_rels = list(self._last_scan_orphan_rels)
        if not backup_dest or not orphan_rels:
            return

        # Donut spinner on during analysis so there's no "dead gap" between
        # backup-done and the choice dialog.
        self._orphan_reviewing = True
        GLib.idle_add(self._start_orphan_review_ui)

        def _progress(cur, total, phase):
            if phase == "build":
                msg = _("Building hash cache: {c} / {t}").format(c=cur, t=total)
            else:
                msg = _("Analyzing: {c} / {t}").format(c=cur, t=total)
            GLib.idle_add(self._set_dedup_detail, msg)

        try:
            dup_rels, unique_rels = self._categorize_orphans(
                photo_path, backup_dest, orphan_rels, _progress)
        finally:
            self._orphan_reviewing = False
            GLib.idle_add(self._set_dedup_detail, "")
            GLib.idle_add(self._finish_review_ui)

        log_info(_("Orphan analysis: {d} duplicate copies, {u} unique on USB").format(
            d=len(dup_rels), u=len(unique_rels)))
        GLib.idle_add(self._show_orphan_review_dialog, dup_rels, unique_rels)

    def _finish_review_ui(self):
        """Hide donut after orphan analysis completes."""
        if hasattr(self, "_backup_donut_btn"):
            try:
                self._set_donuts_visible(self._backup_running)
            except Exception:
                pass
        return False

    def _show_orphan_review_dialog(self, dup_rels, unique_rels):
        if not dup_rels and not unique_rels:
            return False
        lines = []
        if dup_rels:
            lines.append(ngettext(
                "{n} duplicate copy — same image as a photo in Pixora, but under a different name or in a different folder.",
                "{n} duplicate copies — same image as photos in Pixora, but under different names or in different folders.",
                len(dup_rels),
            ).format(n=len(dup_rels)))
        if unique_rels:
            lines.append(ngettext(
                "{n} unique photo — on USB but not in Pixora.",
                "{n} unique photos — on USB but not in Pixora.",
                len(unique_rels),
            ).format(n=len(unique_rels)))
        if dup_rels:
            lines.append("")
            lines.append(_("Do you want to remove the duplicate copies from the USB?"))
        body = "\n".join(lines)
        dlg = Adw.AlertDialog(
            heading=_("Photos on USB that are not in Pixora"),
            body=body,
        )
        if dup_rels:
            dlg.add_response("delete", _("Remove duplicate copies"))
            dlg.set_response_appearance(
                "delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.add_response("keep", _("Keep everything"))
        dlg.set_default_response("keep")
        dlg.set_close_response("keep")

        def _on_resp(_d, resp):
            if resp == "delete" and dup_rels:
                threading.Thread(
                    target=self._delete_orphans_thread,
                    args=(list(dup_rels),),
                    daemon=True,
                ).start()
        dlg.connect("response", _on_resp)
        self._present_dialog(dlg)
        return False

    def _delete_orphans_thread(self, rels):
        backup_dest = self._last_scan_backup_dest
        if not backup_dest or not backup_dest.is_dir():
            return
        deleted = 0
        for rel in rels:
            path = backup_dest / rel
            try:
                path.unlink()
                deleted += 1
            except OSError as e:
                log_warn(_("Could not delete orphan: {p} ({err})").format(p=str(path), err=e))
        # Prune empty dirs on USB bottom-up.
        try:
            for root, _dirs, _files in os.walk(
                    str(backup_dest), topdown=False):
                if root == str(backup_dest):
                    continue
                if not os.listdir(root):
                    try:
                        os.rmdir(root)
                    except OSError:
                        pass
        except Exception:
            pass
        log_info(_("Orphans removed: {n}").format(n=deleted))
        GLib.idle_add(self._show_orphans_deleted_dialog, deleted)


    def _show_orphans_deleted_dialog(self, count):
        dlg = Adw.AlertDialog(
            heading=_("Cleanup complete"),
            body=ngettext(
                "{n} duplicate copy removed from the USB.",
                "{n} duplicate copies removed from the USB.",
                count,
            ).format(n=count),
        )
        dlg.add_response("ok", _("OK"))
        dlg.set_default_response("ok")
        dlg.set_close_response("ok")
        self._present_dialog(dlg)
        return False


