#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — common.py
#  Shared prelude: i18n, logging, GTK/WebKit setup, constants
#  by LinuxGinger
# ─────────────────────────────────────────────

import os
import sys
import json
import datetime
import urllib.request
import urllib.error
import inspect

# ── i18n (gettext) ───────────────────────────────────────────────────
from pixora_i18n import (LOCALE_DIR as _LOCALE_DIR, LANG as _lang,
                         translation as _translation, _, ngettext)
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

# Read dev_mode directly: importing main.py would re-run its module code.
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
        return
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

def reset_log():
    """Close and truncate the dev log so the next session starts fresh."""
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

def log_info(msg):  _log("INFO",  "36", msg)
def log_warn(msg):  _log("WARN",  "33", msg)
def log_error(msg): _log("ERROR", "1;31", msg)

# Dev-mode: faulthandler → thread stackdumps on hang. `kill -USR1 <pid>` dumps.
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

# Surface uncaught exceptions with file:line + traceback.
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
from gi.repository import Gtk, Adw, GLib, GdkPixbuf, Gdk, Pango, GObject
gi.require_foreign("cairo")
import cairo

# Register HEIC/HEIF support in PIL so the viewer can decode iPhone photos even
# when the GdkPixbuf stack has no HEIC loader (otherwise full images show black).
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    # Placeholders keep imports and the PhotoFolderHandler class definition
    # working; the folder watcher is simply skipped without watchdog.
    Observer = None
    FileSystemEventHandler = object
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

# Ubuntu 24.04 AppArmor blocks bwrap sandbox → disable before import.
os.environ.setdefault("WEBKIT_DISABLE_SANDBOX", "1")
os.environ.setdefault("WEBKIT_FORCE_SANDBOX", "0")
# Force GPU compositing — VMs otherwise fall back to software.
os.environ.setdefault("WEBKIT_FORCE_COMPOSITING_MODE", "1")
# EGL/DMA-BUF beats GLX in VMs (VMware SVGA3D).
os.environ.setdefault("WEBKIT_USE_EGL", "1")
os.environ.setdefault("GDK_GL", "gles")
# JSC uses SIGUSR1 for GC by default, clobbering our dev-mode faulthandler.
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
LICENSE_PATH     = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "LICENSE"
))
INSTALL_DIR      = os.path.expanduser("~/.local/share/pixora")
GITHUB_RELEASES_API = "https://api.github.com/repos/Linux-Ginger/Pixora/releases/latest"
CONFIG_PATH      = os.path.expanduser("~/.config/pixora/settings.json")
FAVORITES_PATH   = os.path.expanduser("~/.config/pixora/favorites.json")
CACHE_DIR        = os.path.expanduser("~/.cache/pixora/thumbnails")
TILE_CACHE_DIR   = os.path.expanduser("~/.cache/pixora/tiles")
THUMB_SIZE       = 200
FILM_THUMB       = 70
BATCH_SIZE       = 15

# How close a photo's GPS must sit to a saved place to count as "taken there".
# Wide enough to absorb indoor/urban GPS drift (a fix can wander 100-200 m), yet
# tight enough not to grab the next street over. ~175 m at NL latitudes.
_PLACE_TOL_LAT   = 0.0016
_PLACE_TOL_LON   = 0.0024

def _low_ram_system():
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return (pages * page_size) < (4 * 1024 * 1024 * 1024)
    except Exception:
        return False

THUMB_WORKERS = 2 if _low_ram_system() else 4
# Must match the importer's SUPPORTED_EXT, otherwise files Pixora imported
# (e.g. .heif/.dng/.m4v/.webp) sit in the library but never show in the grid.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".dng",
                    ".webp", ".gif", ".tiff", ".tif", ".bmp",
                    ".mp4", ".mov", ".m4v", ".3gp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".3gp"}
_STILL_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".dng",
                     ".webp", ".gif", ".tiff", ".tif", ".bmp"}


def live_photo_pairs(paths):
    """Return {still_path: motion_path} for Live Photos.

    A video is a Live Photo's motion half when a still image with the same stem
    sits in the same folder (IMG_1234.HEIC + IMG_1234.MOV). Standalone videos —
    no matching still — are not paired.
    """
    stills = {}
    for p in paths:
        if os.path.splitext(p)[1].lower() in _STILL_EXTENSIONS:
            key = (os.path.dirname(p),
                   os.path.splitext(os.path.basename(p))[0].lower())
            stills[key] = p
    pairs = {}
    for p in paths:
        if os.path.splitext(p)[1].lower() in VIDEO_EXTENSIONS:
            key = (os.path.dirname(p),
                   os.path.splitext(os.path.basename(p))[0].lower())
            still = stills.get(key)
            if still is not None:
                pairs[still] = p
    return pairs


def drop_live_motion(paths):
    """Hide the movie half of each Live Photo so it shows as one tile, mirroring
    the iOS Photos app. Standalone videos are kept."""
    motions = set(live_photo_pairs(paths).values())
    return [p for p in paths if p not in motions]


def is_video(path):
    return os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS



def get_logo_path(dark_mode):
    return os.path.join(ASSETS_DIR, f"pixora-logo-{'dark' if dark_mode else 'light'}.svg")

def save_settings(settings):
    # Atomic 0600 write: crash-safe + private on multi-user boxes.
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
        # Corrupt file: back it up and start fresh.
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

_MONTH_KEYS = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]

def format_date_header(dt):
    return f"{dt.day} {_(_MONTH_KEYS[dt.month])} {dt.year}"

def format_viewer_date(dt):
    # Use gettext, not strftime-locale (requires system-wide locales).
    if dt is None or dt.year < 2000:
        return _("Unknown date")
    return _("{month} {day}, {year}  {time}").format(
        day=dt.day,
        month=_(_MONTH_KEYS[dt.month]),
        year=dt.year,
        time=dt.strftime("%H:%M"),
    )


def _walk_rel_files(base):
    """Yield (abs_path, rel_path) for every file under base."""
    base = str(base)
    for root, _dirs, files in os.walk(base):
        for fn in files:
            fp = os.path.join(root, fn)
            yield fp, os.path.relpath(fp, base)


INSTALLED_VERSION_FILE = os.path.expanduser("~/.config/pixora/installed_version")


def _fetch_latest_release():
    """Latest published GitHub release as {version, body, url}.

    Raises on network failure; returns None when the repo has no release yet.
    This is the single source of truth for "is there an update" — Pixora tracks
    tagged releases, not every commit on main.
    """
    req = urllib.request.Request(
        GITHUB_RELEASES_API,
        headers={
            "User-Agent": "Pixora/1.0",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # no release published yet
        raise
    tag = (data.get("tag_name") or data.get("name") or "").strip()
    version = tag[1:] if tag[:1] in ("v", "V") else tag
    return {
        "version": version,
        "body": (data.get("body") or "").strip(),
        "url": data.get("html_url") or "",
    }


def _fetch_versions():
    """(local, remote) version strings; raises on network failure.

    remote is "" when no release is published yet.
    """
    local_version = ""
    if os.path.exists(INSTALLED_VERSION_FILE):
        with open(INSTALLED_VERSION_FILE) as f:
            local_version = f.read().strip()
    rel = _fetch_latest_release()
    return local_version, (rel["version"] if rel else "")


def format_duration(seconds):
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def human_size(n):
    """Bytes → '4.2 MB' style string."""
    try:
        n = float(n)
    except Exception:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024

