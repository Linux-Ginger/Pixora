#!/usr/bin/env python3
# Pixora — importer_page.py — by LinuxGinger

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, GdkPixbuf, Gio, Gdk, Pango

import os

# i18n
import gettext as _gt
import json as _json_i18n
try:
    _lang = _json_i18n.load(open(os.path.expanduser("~/.config/pixora/settings.json"))).get("language", "nl")
except Exception:
    _lang = "nl"
_t = _gt.translation(
    "pixora",
    localedir=os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "locale")),
    languages=[_lang], fallback=True
)
_ = _t.gettext
ngettext = _t.ngettext

import sys
import json
import shutil
import hashlib
import subprocess
import threading
import tempfile
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

try:
    from PIL import Image
    import imagehash
    HAS_IMAGEHASH = True
except ImportError:
    HAS_IMAGEHASH = False

CONFIG_PATH  = Path.home() / ".config" / "pixora" / "settings.json"
CACHE_DIR    = Path.home() / ".cache"  / "pixora"
HASH_CACHE   = CACHE_DIR / "hashes.json"
# Per-user mountpoint to avoid clashes with stale mounts or parallel instances.
MOUNT_POINT  = Path(tempfile.gettempdir()) / f"pixora_iphone_{os.getuid()}"

BACKUP_FSTYPES = {"ext4", "ext3", "ext2", "ntfs", "exfat", "fuseblk", "btrfs", "xfs", "vfat"}
SUPPORTED_EXT  = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".dng", ".mp4", ".mov", ".m4v", ".webp", ".gif", ".tiff", ".tif", ".3gp", ".bmp"}
EXCLUDED_EXT   = {".aae"}
SKIP_DIRS      = {".Trash", "Recently Deleted", "Onlangs verwijderd", ".recently-deleted"}

# Duplicate threshold -> max hash distance; 0 disables the check.
THRESHOLD_MAP = {1: 2, 2: 6, 3: 12}

IMPORT_THUMB_DIR = Path.home() / ".cache" / "pixora" / "import_thumbs"
SELECT_THUMB     = 160  # px


STATE_WAITING   = "waiting"
STATE_DETECTED  = "detected"
STATE_MOUNTING  = "mounting"
STATE_SCANNING  = "scanning"
STATE_SELECTING = "selecting"
STATE_HASHING   = "hashing"
STATE_REVIEWING = "reviewing"
STATE_IMPORTING = "importing"
STATE_DONE      = "done"
STATE_ERROR     = "error"


def load_settings() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def perceptual_hash(path: Path) -> str | None:
    if not HAS_IMAGEHASH:
        return None
    try:
        img = Image.open(path).convert("RGB")
        return str(imagehash.phash(img))
    except Exception:
        return None


def dest_path(base: Path, structure: str, filename: str, mtime: datetime) -> Path:
    if structure == "year":
        return base / str(mtime.year) / filename
    elif structure == "year_month":
        month_dir = f"{mtime.year}-{mtime.month:02d}"
        return base / str(mtime.year) / month_dir / filename
    else:
        return base / filename


def ensure_services():
    """Start usbmuxd if not running."""
    try:
        r = subprocess.run(["pgrep", "-x", "usbmuxd"],
                           capture_output=True, timeout=3)
        if r.returncode != 0:
            try:
                subprocess.run(["usbmuxd"], capture_output=True, timeout=5)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                try:
                    subprocess.run(["sudo", "-n", "usbmuxd"],
                                   capture_output=True, timeout=5)
                except Exception:
                    pass
            time.sleep(1)
    except Exception:
        pass


def detect_iphone() -> str | None:
    try:
        result = subprocess.run(
            ["idevice_id", "-l"],
            capture_output=True, text=True, timeout=3
        )
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        return lines[0] if lines else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def get_device_name(udid: str) -> str:
    try:
        result = subprocess.run(
            ["ideviceinfo", "-u", udid, "-k", "DeviceName"],
            capture_output=True, text=True, timeout=3
        )
        name = result.stdout.strip()
        return name if name else "iPhone"
    except Exception:
        return "iPhone"


def mount_iphone(udid: str, mountpoint: Path) -> bool:
    # Lazy unmount first: clears stale mounts so ifuse won't error "not empty".
    try:
        subprocess.run(["fusermount", "-uz", str(mountpoint)],
                       capture_output=True, timeout=5)
    except Exception:
        pass
    # Only rmtree when NOT mounted; rmtree on an active FUSE mount can hang.
    try:
        still_mounted = False
        try:
            still_mounted = mountpoint.is_mount()
        except Exception:
            pass
        if mountpoint.exists() and not still_mounted:
            shutil.rmtree(str(mountpoint), ignore_errors=True)
    except Exception:
        pass
    try:
        mountpoint.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False
    try:
        result = subprocess.run(
            ["ifuse", "--udid", udid, str(mountpoint)],
            capture_output=True, text=True, timeout=15
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def unmount_iphone(mountpoint: Path):
    try:
        subprocess.run(["fusermount", "-uz", str(mountpoint)], capture_output=True, timeout=5)
    except Exception:
        pass


_EXIF_DATE_TAGS = (36867, 36868, 306)  # DateTimeOriginal, Digitized, DateTime
_VIDEO_EXT = {".mp4", ".mov", ".m4v", ".3gp"}

def _get_video_date(path: Path) -> float | None:
    """Return video creation_time via ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_entries", "format_tags=creation_time", str(path)],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            info = json.loads(result.stdout)
            ct = info.get("format", {}).get("tags", {}).get("creation_time", "")
            if ct:
                dt = datetime.strptime(ct[:19], "%Y-%m-%dT%H:%M:%S")
                return dt.timestamp()
    except Exception:
        pass
    return None

def _get_video_duration(path: Path) -> str | None:
    """Return video duration as 'm:ss' via ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_entries", "format=duration", str(path)],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            info = json.loads(result.stdout)
            secs = float(info.get("format", {}).get("duration", 0))
            if secs > 0:
                mins = int(secs) // 60
                sec = int(secs) % 60
                return f"{mins}:{sec:02d}"
    except Exception:
        pass
    return None


def get_photo_date(path: Path) -> float:
    """Sort key: EXIF/ffprobe timestamp, else filename counter, else mtime."""
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg", ".heic", ".heif", ".png", ".dng", ".tiff", ".tif"):
        try:
            from PIL import Image
            with Image.open(path) as img:
                exif = img.getexif()
            for tag in _EXIF_DATE_TAGS:
                val = exif.get(tag)
                if val:
                    dt = datetime.strptime(val[:19], "%Y:%m:%d %H:%M:%S")
                    return dt.timestamp()
        except Exception:
            pass
    elif ext in _VIDEO_EXT:
        ts = _get_video_date(path)
        if ts:
            return ts
    # Fallback: iPhone names (IMG_1234) are chronological; counter beats mtime.
    m = re.search(r'(\d{4,})', path.stem)
    if m:
        return float(m.group(1))
    return path.stat().st_mtime


def apply_aae_edits(image_path: Path, aae_path: Path) -> bool:
    """Apply AAE crop/rotation edits to the imported photo."""
    try:
        import plistlib
        import zlib

        with open(aae_path, "rb") as f:
            plist = plistlib.load(f)

        raw = plist.get("adjustmentData")
        if not raw:
            return False

        # plistlib already base64-decodes; inner payload is zlib-compressed.
        try:
            json_str = zlib.decompress(raw)
        except zlib.error:
            json_str = raw  # some AAE files ship uncompressed

        data = json.loads(json_str)
        adjustments = data.get("adjustments", [])
        if not adjustments:
            return False

        from PIL import Image
        img = Image.open(image_path)
        modified = False

        for adj in adjustments:
            if not adj.get("enabled", True):
                continue
            identifier = adj.get("identifier", "")
            settings = adj.get("settings", {})

            if identifier == "Crop":
                # cropOrigin/cropSize are fractions of the original image.
                origin = settings.get("cropOrigin")
                size = settings.get("cropSize")
                angle = settings.get("straightenAngle", 0)

                if angle and angle != 0:
                    img = img.rotate(-angle, expand=True, resample=Image.BICUBIC)
                    modified = True

                if origin and size:
                    w, h = img.size
                    left = int(origin[0] * w)
                    top = int(origin[1] * h)
                    right = int((origin[0] + size[0]) * w)
                    bottom = int((origin[1] + size[1]) * h)
                    img = img.crop((left, top, right, bottom))
                    modified = True

            elif identifier == "Straighten":
                angle = settings.get("straightenAngle", 0)
                if angle and angle != 0:
                    img = img.rotate(-angle, expand=True, resample=Image.BICUBIC)
                    modified = True

        if modified:
            ext = image_path.suffix.lower()
            if ext in (".jpg", ".jpeg"):
                img.save(image_path, "JPEG", quality=95, exif=img.info.get("exif", b""))
            elif ext in (".heic", ".heif"):
                # Pillow can't write HEIC; save as JPEG next to the original.
                jpeg_path = image_path.with_suffix(".jpg")
                img.save(jpeg_path, "JPEG", quality=95)
                image_path.unlink()
                jpeg_path.rename(image_path.with_suffix(".jpg"))
            elif ext == ".png":
                img.save(image_path, "PNG")
            else:
                img.save(image_path)
            img.close()
            return True

        img.close()
    except Exception:
        pass
    return False


def scan_dcim(mountpoint: Path, progress_cb=None) -> list[Path]:
    """Recursively scan DCIM; skip SKIP_DIRS and AAE files."""
    dcim = mountpoint / "DCIM"
    if not dcim.exists():
        return []

    files: list[Path] = []

    def _walk(directory: Path) -> None:
        for attempt in range(3):
            try:
                entries = sorted(directory.iterdir())
                break
            except OSError:
                if attempt == 2:
                    return
                time.sleep(0.3)
        else:
            return

        subdirs: list[Path] = []
        for entry in entries:
            if entry.is_dir():
                if entry.name not in SKIP_DIRS:
                    subdirs.append(entry)
            elif entry.is_file():
                ext = entry.suffix.lower()
                if ext in EXCLUDED_EXT:
                    pass
                elif ext in SUPPORTED_EXT:
                    files.append(entry)
                    if progress_cb:
                        progress_cb(len(files))

        for subdir in subdirs:
            _walk(subdir)

    _walk(dcim)
    return files


def load_hash_cache() -> dict:
    if HASH_CACHE.exists():
        try:
            with open(HASH_CACHE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_hash_cache(cache: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(HASH_CACHE, "w") as f:
        json.dump(cache, f)


def build_library_hashes(photo_path: Path, progress_cb=None) -> dict:
    """Build pHash index of archive; uncached photos hashed in parallel (PIL drops GIL)."""
    cache = load_hash_cache()
    hashes = {}

    all_files = []
    for root, _, files in os.walk(photo_path):
        for fn in files:
            if Path(fn).suffix.lower() in SUPPORTED_EXT:
                all_files.append(Path(root) / fn)

    cache_keys = {}
    to_hash = []
    for fp in all_files:
        try:
            stat = fp.stat()
        except OSError:
            continue
        cache_key = f"{fp}:{int(stat.st_mtime)}:{stat.st_size}"
        cache_keys[fp] = cache_key
        if cache_key in cache:
            ph = cache[cache_key]
            if ph:
                hashes[str(fp)] = ph
        else:
            to_hash.append(fp)

    if to_hash:
        done = 0
        total = len(all_files)
        cached = total - len(to_hash)
        with ThreadPoolExecutor(max_workers=8) as pool:
            for fp, ph in zip(to_hash, pool.map(perceptual_hash, to_hash)):
                done += 1
                if progress_cb:
                    progress_cb(cached + done, total, fp.name)
                if ph:
                    cache[cache_keys[fp]] = ph
                    hashes[str(fp)] = ph
    elif progress_cb:
        progress_cb(len(all_files), len(all_files), "")

    save_hash_cache(cache)
    return hashes


def find_duplicate(ph_str: str, library_hashes: dict, max_dist: int) -> str | None:
    if not ph_str or not HAS_IMAGEHASH:
        return None
    try:
        ph = imagehash.hex_to_hash(ph_str)
        for lib_path, lib_ph_str in library_hashes.items():
            try:
                if ph - imagehash.hex_to_hash(lib_ph_str) <= max_dist:
                    return lib_path
            except Exception:
                continue
    except Exception:
        pass
    return None


def get_backup_mountpoint(uuid: str) -> Path | None:
    try:
        result = subprocess.run(
            ["lsblk", "-o", "UUID,MOUNTPOINT", "-J"],
            capture_output=True, text=True, timeout=5
        )
        data = json.loads(result.stdout)

        def search(devices):
            for dev in devices:
                if (dev.get("uuid") or "").strip() == uuid:
                    mp = (dev.get("mountpoint") or "").strip()
                    if mp:
                        return Path(mp)
                for child in dev.get("children") or []:
                    r = search([child])
                    if r:
                        return r
            return None

        return search(data.get("blockdevices", []))
    except Exception:
        return None


def _import_cache_path(photo_path: Path) -> Path:
    try:
        stat = photo_path.stat()
        key = hashlib.md5(f"{photo_path}:{int(stat.st_mtime)}:{stat.st_size}".encode()).hexdigest()
    except OSError:
        key = hashlib.md5(str(photo_path).encode()).hexdigest()
    return IMPORT_THUMB_DIR / (key + ".png")


def _crop_to_square(pixbuf) -> "GdkPixbuf.Pixbuf":
    w = pixbuf.get_width()
    h = pixbuf.get_height()
    size = min(w, h)
    x = (w - size) // 2
    y = (h - size) // 2
    return pixbuf.new_subpixbuf(x, y, size, size)


def load_select_thumb(photo_path: Path):
    """Square SELECT_THUMB thumbnail for the selection page; disk-cached."""
    IMPORT_THUMB_DIR.mkdir(parents=True, exist_ok=True)
    cache = _import_cache_path(photo_path)

    if cache.exists():
        try:
            return GdkPixbuf.Pixbuf.new_from_file(str(cache))
        except Exception:
            cache.unlink(missing_ok=True)

    try:
        ext = photo_path.suffix.lower()
        if ext in {".mp4", ".mov", ".m4v"}:
            if not _cmd_available("ffmpeg"):
                return None
            # No seek: first-frame-only is more reliable on FUSE/USB.
            tmp = cache.with_suffix(".tmp.jpg")
            result = subprocess.run(
                ["ffmpeg", "-i", str(photo_path),
                 "-frames:v", "1", str(tmp), "-y"],
                capture_output=True, timeout=30
            )
            if result.returncode != 0 or not tmp.exists():
                return None
            raw = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(tmp), SELECT_THUMB * 2, SELECT_THUMB * 2, True
            )
            tmp.unlink(missing_ok=True)
        else:
            # Load at 2x for better quality after downscale.
            raw = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(photo_path), SELECT_THUMB * 2, SELECT_THUMB * 2, True
            )

        square = _crop_to_square(raw)
        thumb = square.scale_simple(SELECT_THUMB, SELECT_THUMB, GdkPixbuf.InterpType.HYPER)
        thumb.savev(str(cache), "png", [], [])
        return thumb
    except Exception:
        return None


def _cmd_available(cmd: str) -> bool:
    return shutil.which(cmd) is not None


class ImporterPage(Gtk.Box):
    def __init__(self, on_back_cb, on_done_cb):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_back_cb = on_back_cb
        self.on_done_cb = on_done_cb
        self.settings = load_settings()
        self.state = STATE_WAITING

        self.udid: str | None = None
        self.device_name = "iPhone"
        self.iphone_files: list[Path] = []
        self.selected_files: set[str] = set()
        self.library_hashes: dict = {}
        self.duplicates: list[tuple[Path, Path]] = []
        self.to_import: list[Path] = []
        self.duplicate_decisions: dict[str, str] = {}
        self.import_count = 0

        self._poll_timer_id: int | None = None
        self._disconnect_dialog_open = False
        self._thumb_load_gen = 0  # bumped on state-switch to drop stale callbacks

        self._build_ui()

    def _build_ui(self):
        header = Adw.HeaderBar()
        header.add_css_class("flat")

        back_btn = Gtk.Button(icon_name="go-previous-symbolic")
        back_btn.add_css_class("flat")
        back_btn.set_tooltip_text(_("Back"))
        back_btn.connect("clicked", self._on_back_clicked)
        header.pack_start(back_btn)

        title_lbl = Gtk.Label(label=_("Import"))
        header.set_title_widget(title_lbl)

        self.append(header)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(200)
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)
        self.append(self.stack)

        self._build_waiting_page()
        self._build_detected_page()
        self._build_progress_page()
        self._build_selecting_page()
        self._build_review_page()
        self._build_done_page()
        self._build_error_page()

        self._show_state(STATE_WAITING)

    def activate(self):
        self.settings = load_settings()
        self._show_state(STATE_WAITING)
        threading.Thread(target=ensure_services, daemon=True).start()
        self._start_detection_poll()

    def deactivate(self):
        if self._poll_timer_id is not None:
            try:
                GLib.source_remove(self._poll_timer_id)
            except Exception:
                pass
            self._poll_timer_id = None
        # Invalidate in-flight thumb loaders so idle callbacks skip dead widgets.
        self._thumb_load_gen += 1
        unmount_iphone(MOUNT_POINT)

    def _on_back_clicked(self, _btn):
        self.deactivate()
        self.on_back_cb()

    def _build_waiting_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_vexpand(True)
        outer.set_valign(Gtk.Align.FILL)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(480)
        clamp.set_vexpand(True)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_vexpand(True)
        inner.set_valign(Gtk.Align.CENTER)
        inner.set_margin_top(12)
        inner.set_margin_bottom(12)
        inner.set_margin_start(12)
        inner.set_margin_end(12)

        icon = Gtk.Image.new_from_icon_name("computer-symbolic")
        icon.set_pixel_size(48)
        icon.set_halign(Gtk.Align.CENTER)
        inner.append(icon)

        title_lbl = Gtk.Label(label=_("Connect your iPhone or iPad"))
        title_lbl.add_css_class("title-3")
        title_lbl.set_halign(Gtk.Align.CENTER)
        title_lbl.set_margin_top(4)
        inner.append(title_lbl)

        desc_lbl = Gtk.Label(label=_("Connect your iPhone or iPad via a USB cable and unlock the screen.\nIf your device asks to trust this computer, tap 'Trust'."))
        desc_lbl.add_css_class("dim-label")
        desc_lbl.set_halign(Gtk.Align.CENTER)
        desc_lbl.set_justify(Gtk.Justification.CENTER)
        desc_lbl.set_wrap(True)
        desc_lbl.set_margin_bottom(8)
        inner.append(desc_lbl)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")

        for ico_name, title, subtitle in [
            ("drive-removable-media-symbolic", _("USB cable"),              _("Preferably use the original Apple cable")),
            ("security-medium-symbolic",       _("Trust this computer"), _("Tap 'Trust' if your device asks")),
            ("system-lock-screen-symbolic",    _("Unlocked screen"),     _("Make sure your device is unlocked during import")),
            ("media-flash-symbolic",           _("Use a blue USB port"), _("USB 3.0 (blue) is much faster than black USB 2.0 ports")),
            ("weather-overcast-symbolic",      _("iCloud photos"),          _("iCloud Photos disabled? Then all photos are stored locally on your device and will all be found.")),
            ("document-save-symbolic",         _("File format"),        _("On your device: Settings → Photos → 'Transfer to Mac or PC' → 'Keep Originals'")),
        ]:
            row = Adw.ActionRow()
            row.set_title(title)
            row.set_subtitle(subtitle)
            ic = Gtk.Image.new_from_icon_name(ico_name)
            ic.set_pixel_size(16)
            row.add_prefix(ic)
            listbox.append(row)

        inner.append(listbox)

        spinner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        spinner_box.set_halign(Gtk.Align.CENTER)
        spinner_box.set_margin_top(8)
        spin = Gtk.Spinner()
        spin.start()
        spinner_box.append(spin)
        lbl = Gtk.Label(label=_("Searching for device…"))
        lbl.add_css_class("dim-label")
        spinner_box.append(lbl)
        inner.append(spinner_box)

        clamp.set_child(inner)
        outer.append(clamp)
        self.stack.add_named(outer, "waiting")

    def _build_detected_page(self):
        status = Adw.StatusPage()
        status.set_icon_name("object-select-symbolic")
        status.set_title(_("Device found"))
        status.set_description(_("Your device is connected and ready to import."))

        clamp = Adw.Clamp()
        clamp.set_maximum_size(420)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_bottom(32)
        box.set_margin_start(12)
        box.set_margin_end(12)

        info_group = Adw.PreferencesGroup()

        self.device_row = Adw.ActionRow()
        self.device_row.set_title(_("Device"))
        self.device_row.set_subtitle("iPhone")
        ic = Gtk.Image.new_from_icon_name("computer-symbolic")
        ic.set_pixel_size(16)
        self.device_row.add_prefix(ic)
        info_group.add(self.device_row)

        self.dest_row = Adw.ActionRow()
        self.dest_row.set_title(_("Save to"))
        self.dest_row.set_subtitle(self.settings.get("photo_path") or "~")
        ic2 = Gtk.Image.new_from_icon_name("folder-symbolic")
        ic2.set_pixel_size(16)
        self.dest_row.add_prefix(ic2)
        info_group.add(self.dest_row)

        struct = self.settings.get("structure", "year_month")
        struct_labels = {
            "flat":       _("Everything in one folder"),
            "year":       _("By year"),
            "year_month": _("By year/month"),
        }
        self.struct_row = Adw.ActionRow()
        self.struct_row.set_title(_("Folder structure"))
        self.struct_row.set_subtitle(struct_labels.get(struct, struct))
        ic3 = Gtk.Image.new_from_icon_name("folder-open-symbolic")
        ic3.set_pixel_size(16)
        self.struct_row.add_prefix(ic3)
        info_group.add(self.struct_row)

        box.append(info_group)

        import_btn = Gtk.Button(label=_("Import"))
        import_btn.add_css_class("suggested-action")
        import_btn.add_css_class("pill")
        import_btn.set_halign(Gtk.Align.CENTER)
        import_btn.connect("clicked", self._on_import_clicked)
        box.append(import_btn)

        clamp.set_child(box)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        vbox.append(status)
        vbox.append(clamp)
        self.stack.add_named(vbox, "detected")

    def _build_progress_page(self):
        clamp = Adw.Clamp()
        clamp.set_maximum_size(480)
        clamp.set_valign(Gtk.Align.CENTER)
        clamp.set_vexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        box.set_margin_top(48)
        box.set_margin_bottom(48)
        box.set_margin_start(24)
        box.set_margin_end(24)

        self.progress_spinner = Gtk.Spinner()
        self.progress_spinner.set_size_request(48, 48)
        self.progress_spinner.start()
        self.progress_spinner.set_halign(Gtk.Align.CENTER)
        box.append(self.progress_spinner)

        self.progress_title = Gtk.Label()
        self.progress_title.add_css_class("title-2")
        self.progress_title.set_halign(Gtk.Align.CENTER)
        box.append(self.progress_title)

        self.progress_subtitle = Gtk.Label()
        self.progress_subtitle.add_css_class("dim-label")
        self.progress_subtitle.set_halign(Gtk.Align.CENTER)
        self.progress_subtitle.set_wrap(True)
        self.progress_subtitle.set_max_width_chars(52)
        box.append(self.progress_subtitle)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        box.append(self.progress_bar)

        self.progress_detail = Gtk.Label()
        self.progress_detail.add_css_class("dim-label")
        self.progress_detail.add_css_class("caption")
        self.progress_detail.set_halign(Gtk.Align.CENTER)
        self.progress_detail.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.progress_detail.set_max_width_chars(52)
        box.append(self.progress_detail)

        clamp.set_child(box)
        self.stack.add_named(clamp, "progress")

    def _build_selecting_page(self):
        thumb_css = Gtk.CssProvider()
        thumb_css.load_from_string(".thumb-item { border-radius: 8px; }")
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), thumb_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header_box.set_margin_top(20)
        header_box.set_margin_bottom(10)
        header_box.set_margin_start(24)
        header_box.set_margin_end(24)

        self.select_title = Gtk.Label()
        self.select_title.add_css_class("title-1")
        self.select_title.set_halign(Gtk.Align.START)
        header_box.append(self.select_title)

        self.select_subtitle = Gtk.Label()
        self.select_subtitle.add_css_class("dim-label")
        self.select_subtitle.set_halign(Gtk.Align.START)
        header_box.append(self.select_subtitle)

        outer.append(header_box)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.select_flow = Gtk.FlowBox()
        self.select_flow.set_homogeneous(True)
        self.select_flow.set_sort_func(lambda a, b, *_: 0)  # preserve insertion order
        self.select_flow.set_max_children_per_line(6)
        self.select_flow.set_min_children_per_line(2)
        self.select_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.select_flow.set_column_spacing(6)
        self.select_flow.set_row_spacing(6)
        self.select_flow.set_margin_start(12)
        self.select_flow.set_margin_end(12)
        self.select_flow.set_margin_bottom(8)

        scroll.set_child(self.select_flow)
        outer.append(scroll)

        action_bar = Gtk.ActionBar()

        sel_all_btn = Gtk.Button(label=_("Select all"))
        sel_all_btn.connect("clicked", self._on_select_all)
        action_bar.pack_start(sel_all_btn)

        desel_all_btn = Gtk.Button(label=_("Deselect all"))
        desel_all_btn.connect("clicked", self._on_deselect_all)
        action_bar.pack_start(desel_all_btn)

        self.select_count_lbl = Gtk.Label()
        self.select_count_lbl.add_css_class("dim-label")
        action_bar.set_center_widget(self.select_count_lbl)

        self.select_continue_btn = Gtk.Button(label=_("Continue"))
        self.select_continue_btn.add_css_class("suggested-action")
        self.select_continue_btn.connect("clicked", self._on_selecting_continue)
        action_bar.pack_end(self.select_continue_btn)

        outer.append(action_bar)
        self.stack.add_named(outer, "selecting")

    def _build_review_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header_box.set_margin_top(24)
        header_box.set_margin_bottom(12)
        header_box.set_margin_start(24)
        header_box.set_margin_end(24)

        title_lbl = Gtk.Label(label=_("Possible duplicates"))
        title_lbl.add_css_class("title-1")
        title_lbl.set_halign(Gtk.Align.START)
        header_box.append(title_lbl)

        self.review_subtitle = Gtk.Label()
        self.review_subtitle.add_css_class("dim-label")
        self.review_subtitle.set_halign(Gtk.Align.START)
        self.review_subtitle.set_wrap(True)
        header_box.append(self.review_subtitle)

        outer.append(header_box)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.review_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.review_box.set_margin_start(24)
        self.review_box.set_margin_end(24)
        self.review_box.set_margin_bottom(12)
        scroll.set_child(self.review_box)
        outer.append(scroll)

        action_bar = Gtk.ActionBar()

        skip_all_btn = Gtk.Button(label=_("Skip all"))
        skip_all_btn.connect("clicked", self._on_skip_all)
        action_bar.pack_start(skip_all_btn)

        import_all_btn = Gtk.Button(label=_("Import all"))
        import_all_btn.connect("clicked", self._on_import_all)
        action_bar.pack_start(import_all_btn)

        continue_btn = Gtk.Button(label=_("Continue importing"))
        continue_btn.add_css_class("suggested-action")
        continue_btn.connect("clicked", self._on_review_continue)
        action_bar.pack_end(continue_btn)

        outer.append(action_bar)
        self.stack.add_named(outer, "review")

    def _build_done_page(self):
        self.done_status = Adw.StatusPage()
        self.done_status.set_icon_name("emblem-ok-symbolic")
        self.done_status.set_title(_("Import complete"))

        clamp = Adw.Clamp()
        clamp.set_maximum_size(420)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_bottom(32)
        box.set_margin_start(12)
        box.set_margin_end(12)

        self.done_stats_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(self.done_stats_box)

        close_btn = Gtk.Button(label=_("Back to gallery"))
        close_btn.add_css_class("pill")
        close_btn.set_halign(Gtk.Align.CENTER)
        close_btn.connect("clicked", self._on_back_clicked)
        box.append(close_btn)

        clamp.set_child(box)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        vbox.append(self.done_status)
        vbox.append(clamp)
        self.stack.add_named(vbox, "done")

    def _build_error_page(self):
        self.error_status = Adw.StatusPage()
        self.error_status.set_icon_name("dialog-error-symbolic")
        self.error_status.set_title(_("An error occurred"))

        clamp = Adw.Clamp()
        clamp.set_maximum_size(420)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_bottom(32)
        box.set_margin_start(12)
        box.set_margin_end(12)

        self.error_deps_group = Adw.PreferencesGroup()
        self.error_deps_group.set_title(_("Install required packages"))
        self.error_deps_group.set_visible(False)

        for pkg, cmd in [
            ("libimobiledevice-utils", "sudo apt install libimobiledevice-utils"),
            ("ifuse",                  "sudo apt install ifuse"),
        ]:
            row = Adw.ActionRow()
            row.set_title(pkg)
            row.set_subtitle(cmd)
            row.set_subtitle_selectable(True)
            ic = Gtk.Image.new_from_icon_name("terminal-symbolic")
            ic.set_pixel_size(16)
            row.add_prefix(ic)
            self.error_deps_group.add(row)

        box.append(self.error_deps_group)

        retry_btn = Gtk.Button(label=_("Try again"))
        retry_btn.add_css_class("pill")
        retry_btn.set_halign(Gtk.Align.CENTER)
        retry_btn.connect("clicked", self._on_retry)
        box.append(retry_btn)

        clamp.set_child(box)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        vbox.append(self.error_status)
        vbox.append(clamp)
        self.stack.add_named(vbox, "error")

    def _show_state(self, state: str):
        self.state = state
        if state != STATE_SCANNING:
            try:
                self._stop_progress_pulse()
            except Exception:
                pass
        page_map = {
            STATE_WAITING:   "waiting",
            STATE_DETECTED:  "detected",
            STATE_MOUNTING:  "progress",
            STATE_SCANNING:  "progress",
            STATE_SELECTING: "selecting",
            STATE_HASHING:   "progress",
            STATE_REVIEWING: "review",
            STATE_IMPORTING: "progress",
            STATE_DONE:      "done",
            STATE_ERROR:     "error",
        }
        self.stack.set_visible_child_name(page_map.get(state, "waiting"))

    def _start_detection_poll(self):
        if self._poll_timer_id is not None:
            GLib.source_remove(self._poll_timer_id)
        self._poll_timer_id = GLib.timeout_add(2000, self._poll_iphone)

    def _poll_iphone(self) -> bool:
        if self._poll_timer_id is None:
            return False
        if self.state not in (STATE_WAITING, STATE_DETECTED, STATE_SELECTING,
                               STATE_REVIEWING, STATE_SCANNING):
            self._poll_timer_id = None
            return False
        threading.Thread(target=self._check_iphone, daemon=True).start()
        return True

    def _check_iphone(self):
        ensure_services()
        udid = detect_iphone()
        GLib.idle_add(self._on_detection_result, udid)

    def _on_detection_result(self, udid: str | None):
        if self.state == STATE_SCANNING:
            if not udid:
                self._on_iphone_disconnected()
            return
        if self.state in (STATE_SELECTING, STATE_REVIEWING):
            if not udid:
                self._on_iphone_disconnected()
            return
        if self.state not in (STATE_WAITING, STATE_DETECTED):
            return
        if udid and self.state == STATE_WAITING:
            self.udid = udid
            self.device_name = get_device_name(udid)
            self.device_row.set_subtitle(self.device_name)
            self.dest_row.set_subtitle(self.settings.get("photo_path") or "~")
            self._show_state(STATE_DETECTED)
        elif not udid and self.state == STATE_DETECTED:
            self.udid = None
            self._show_state(STATE_WAITING)

    def _on_iphone_disconnected(self):
        if self._disconnect_dialog_open:
            return
        self._disconnect_dialog_open = True
        unmount_iphone(MOUNT_POINT)

        window = self.get_root()

        dialog = Adw.MessageDialog.new(
            window,
            _("Device disconnected"),
            _("Your device was disconnected while selecting photos.\nReconnect the device and try again.")
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("retry", _("Try again"))
        dialog.set_default_response("retry")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_disconnect_response)
        dialog.present()

    def _on_disconnect_response(self, dialog, response: str):
        self._disconnect_dialog_open = False
        self._show_state(STATE_WAITING)
        self._start_detection_poll()

    def _on_import_clicked(self, _btn):
        self._set_progress(_("Mounting device…"), _("Please wait, this takes just a moment."))
        self._show_state(STATE_MOUNTING)
        threading.Thread(target=self._do_mount, daemon=True).start()

    def _do_mount(self):
        if not _cmd_available("ifuse") or not _cmd_available("idevice_id"):
            GLib.idle_add(self._show_error,
                _("ifuse or libimobiledevice is not installed. Install the required packages below."), True)
            return
        if not mount_iphone(self.udid, MOUNT_POINT):
            GLib.idle_add(self._show_error,
                _("Could not mount the device. Make sure the screen is unlocked and tap 'Trust' when asked."), False)
            return
        GLib.idle_add(self._start_scan)

    def _start_scan(self):
        self._set_progress(_("Scanning photos…"), _("Searching for photos and videos on your device."))
        self._show_state(STATE_SCANNING)
        self._start_detection_poll()
        self._start_progress_pulse()
        threading.Thread(target=self._do_scan, daemon=True).start()

    def _start_progress_pulse(self):
        """Indeterminate progress bar while total unknown."""
        self._stop_progress_pulse()
        self.progress_bar.set_show_text(False)
        self.progress_bar.set_pulse_step(0.08)
        self._pulse_timer = GLib.timeout_add(80, self._pulse_tick)

    def _pulse_tick(self):
        try:
            self.progress_bar.pulse()
        except Exception:
            return False
        return True

    def _stop_progress_pulse(self):
        if getattr(self, "_pulse_timer", None):
            try:
                GLib.source_remove(self._pulse_timer)
            except Exception:
                pass
            self._pulse_timer = None
        self.progress_bar.set_show_text(True)

    def _do_scan(self):
        def on_progress(count):
            GLib.idle_add(
                self.progress_subtitle.set_text,
                ngettext("%d file found…", "%d files found…", count) % count,
            )
        files = scan_dcim(MOUNT_POINT, progress_cb=on_progress)
        total = len(files)
        GLib.idle_add(
            self.progress_subtitle.set_text,
            ngettext(
                "%d file found, sorting by date…",
                "%d files found, sorting by date…",
                total,
            ) % total,
        )
        # Chunked progress during sort keeps the UI responsive.
        GLib.idle_add(self._begin_sort_progress, total)
        if total <= 500:
            files.sort(key=get_photo_date, reverse=True)
            GLib.idle_add(self._update_progress, 1.0, _("Sorting done ({n})").format(n=total), "")
        else:
            # as_completed gives per-item progress; pool.map would tick only
            # at the end, looking frozen on FUSE.
            date_cache = {}
            done = 0
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = {pool.submit(get_photo_date, f): f for f in files}
                for fut in as_completed(futures):
                    f = futures[fut]
                    try:
                        date_cache[f] = fut.result()
                    except Exception:
                        date_cache[f] = 0
                    done += 1
                    frac = done / total
                    GLib.idle_add(
                        self._update_progress, frac,
                        _("Sorting: {i} / {total}").format(i=done, total=total),
                        f.name,
                    )
            files.sort(key=lambda p: date_cache.get(p, 0), reverse=True)
        GLib.idle_add(self._on_scan_done, files)

    def _begin_sort_progress(self, total):
        self._stop_progress_pulse()
        self.progress_bar.set_fraction(0)
        self.progress_bar.set_text("0%")
        return False

    def _on_scan_done(self, files: list[Path]):
        self.iphone_files = files
        if not files:
            unmount_iphone(MOUNT_POINT)
            self._show_error(
                _("No photos or videos found on the device.\nAll media may already have been imported."), False)
            return
        self._show_selecting(files)

    def _start_hashing(self, files: list[Path]):
        self._set_progress(_("Checking duplicates…"),
                           _("Photos are being compared with your existing archive."))
        self._show_state(STATE_HASHING)
        threading.Thread(target=self._do_hashing, args=(files,), daemon=True).start()

    def _do_hashing(self, iphone_files: list[Path]):
        photo_path = Path(self.settings.get("photo_path") or Path.home() / "Photos")
        threshold_key = self.settings.get("duplicate_threshold", 2)
        # threshold 0 = detection off; treat everything as new.
        if threshold_key == 0:
            GLib.idle_add(self._on_hashing_done, [], list(iphone_files))
            return
        max_dist = THRESHOLD_MAP.get(threshold_key, 6)

        def lib_progress(i, total, name):
            frac = (i / total) * 0.5 if total > 0 else 0
            GLib.idle_add(self._update_progress, frac,
                          _("Scanning archive: {i}/{total}").format(i=i, total=total), name)

        library_hashes = build_library_hashes(photo_path, lib_progress)
        self.library_hashes = library_hashes

        duplicates: list[tuple[Path, Path]] = []
        new_files: list[Path] = []
        total = len(iphone_files)

        for i, fp in enumerate(iphone_files):
            frac = 0.5 + (i / total) * 0.5 if total > 0 else 0.5
            GLib.idle_add(self._update_progress, frac,
                          _("Scanning device: {i}/{total}").format(i=i + 1, total=total), fp.name)
            ph = perceptual_hash(fp)
            if ph:
                dup = find_duplicate(ph, library_hashes, max_dist)
                if dup:
                    duplicates.append((fp, Path(dup)))
                else:
                    new_files.append(fp)
            else:
                new_files.append(fp)

        GLib.idle_add(self._on_hashing_done, duplicates, new_files)

    def _on_hashing_done(self, duplicates: list, new_files: list):
        self.duplicates = duplicates
        self.to_import = new_files[:]
        self.duplicate_decisions = {}
        if duplicates:
            self._show_review(duplicates)
        else:
            self._start_import()

    def _show_selecting(self, files: list[Path]):
        n = len(files)
        self.select_title.set_text(ngettext("%d file found", "%d files found", n) % n)
        self.select_subtitle.set_text(
            _("Choose which photos and videos to import.\n💡 Tip: empty the trash on your iPhone first to exclude deleted photos.")
        )

        self.selected_files = {str(f) for f in files}
        self._update_select_count()

        # Bump gen so prior thumb-loaders skip the new widgets.
        self._thumb_load_gen += 1

        while child := self.select_flow.get_first_child():
            self.select_flow.remove(child)

        self._select_cards: dict[str, Gtk.CheckButton] = {}
        self._select_overlays: dict[str, Gtk.Overlay] = {}
        self._video_duration_labels: dict[str, Gtk.Label] = {}
        for fp in files:
            card, check, overlay = self._make_select_card(fp)
            self._select_cards[str(fp)] = check
            self._select_overlays[str(fp)] = overlay
            self.select_flow.append(card)

        self._show_state(STATE_SELECTING)

        threading.Thread(target=self._load_select_thumbs, args=(list(files),), daemon=True).start()

    def _make_select_card(self, fp: Path) -> tuple[Gtk.Widget, Gtk.CheckButton, Gtk.Overlay]:
        overlay = Gtk.Overlay()
        overlay.set_size_request(SELECT_THUMB, SELECT_THUMB)
        overlay.set_overflow(Gtk.Overflow.HIDDEN)
        overlay.add_css_class("thumb-item")

        placeholder = Gtk.Image.new_from_icon_name("image-loading-symbolic")
        placeholder.set_pixel_size(32)
        placeholder.set_size_request(SELECT_THUMB, SELECT_THUMB)
        overlay.set_child(placeholder)

        click = Gtk.GestureClick.new()
        click.connect("pressed", lambda g, n, x, y, ip=str(fp): self._on_card_click(ip))
        overlay.add_controller(click)

        # Check icon is visual-only; click handled by GestureClick above.
        check = Gtk.CheckButton()
        check.set_active(True)
        check.set_halign(Gtk.Align.START)
        check.set_valign(Gtk.Align.START)
        check.set_margin_top(4)
        check.set_margin_start(4)
        check.set_can_target(False)
        check.set_focusable(False)
        overlay.add_overlay(check)

        ext = fp.suffix.lower()
        if ext in {".mp4", ".mov", ".m4v", ".3gp"}:
            video_lbl = Gtk.Label(label="▶")
            video_lbl.add_css_class("caption")
            video_lbl.set_halign(Gtk.Align.END)
            video_lbl.set_valign(Gtk.Align.END)
            video_lbl.set_margin_end(4)
            video_lbl.set_margin_bottom(4)
            overlay.add_overlay(video_lbl)
            self._video_duration_labels[str(fp)] = video_lbl

        return overlay, check, overlay

    def _load_select_thumbs(self, files: list[Path]):
        """Sequential thumb load; faster than parallel on USB/FUSE."""
        my_gen = self._thumb_load_gen
        for fp in files:
            if my_gen != self._thumb_load_gen:
                return
            pixbuf = load_select_thumb(fp)
            if pixbuf is not None:
                GLib.idle_add(self._set_select_thumb, str(fp), pixbuf, my_gen)
            if fp.suffix.lower() in _VIDEO_EXT:
                dur = _get_video_duration(fp)
                if dur:
                    GLib.idle_add(self._set_video_duration, str(fp), dur, my_gen)

    def _set_select_thumb(self, path_str: str, pixbuf, gen: int = 0):
        if gen and gen != self._thumb_load_gen:
            return
        overlay = self._select_overlays.get(path_str)
        if overlay is None:
            return
        try:
            pic = Gtk.Picture.new_for_pixbuf(pixbuf)
            pic.set_can_shrink(False)
            pic.set_content_fit(Gtk.ContentFit.COVER)
            pic.set_size_request(SELECT_THUMB, SELECT_THUMB)
            overlay.set_child(pic)
        except Exception:
            pass

    def _set_video_duration(self, path_str: str, duration: str, gen: int = 0):
        if gen and gen != self._thumb_load_gen:
            return
        lbl = self._video_duration_labels.get(path_str)
        if lbl:
            try:
                lbl.set_text(f"▶ {duration}")
            except Exception:
                pass

    def _on_card_click(self, path_str: str):
        check = self._select_cards.get(path_str)
        if check is None:
            return
        new_state = not check.get_active()
        check.set_active(new_state)
        if new_state:
            self.selected_files.add(path_str)
        else:
            self.selected_files.discard(path_str)
        self._update_select_count()

    def _on_select_all(self, _btn):
        self.selected_files = {str(f) for f in self.iphone_files}
        for check in self._select_cards.values():
            check.set_active(True)
        self._update_select_count()

    def _on_deselect_all(self, _btn):
        self.selected_files.clear()
        for check in self._select_cards.values():
            check.set_active(False)
        self._update_select_count()

    def _update_select_count(self):
        n = len(self.selected_files)
        total = len(self.iphone_files)
        self.select_count_lbl.set_text(
            _("{n} of {total} selected").format(n=n, total=total)
        )
        self.select_continue_btn.set_sensitive(n > 0)

    def _on_selecting_continue(self, _btn):
        selected = [f for f in self.iphone_files if str(f) in self.selected_files]
        if not HAS_IMAGEHASH:
            self.duplicates = []
            self.to_import = selected
            self._start_import()
            return
        self._start_hashing(selected)

    def _show_review(self, duplicates: list[tuple[Path, Path]]):
        n = len(duplicates)
        self.review_subtitle.set_text(
            ngettext(
                "%d photo may already be in your archive. Choose what to do, or use the buttons below.",
                "%d photos may already be in your archive. Choose per photo what to do, or use the buttons below for all at once.",
                n,
            ) % n
        )

        while child := self.review_box.get_first_child():
            self.review_box.remove(child)

        for iphone_path, lib_path in duplicates:
            self.duplicate_decisions[str(iphone_path)] = "skip"
            card = self._make_dup_card(iphone_path, lib_path)
            self.review_box.append(card)

        self._show_state(STATE_REVIEWING)

    def _make_dup_card(self, iphone_path: Path, lib_path: Path) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.add_css_class("card")
        card.set_margin_bottom(4)

        name_lbl = Gtk.Label(label=iphone_path.name)
        name_lbl.add_css_class("heading")
        name_lbl.set_halign(Gtk.Align.START)
        name_lbl.set_margin_top(12)
        name_lbl.set_margin_start(14)
        name_lbl.set_margin_bottom(8)
        card.append(name_lbl)

        img_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        img_row.set_margin_start(12)
        img_row.set_margin_end(12)
        img_row.set_margin_bottom(10)

        for path, caption in [(iphone_path, _("📱 iPhone — new")),
                               (lib_path,    _("🗂️ Archive — existing"))]:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            col.set_hexpand(True)

            frame = Gtk.Frame()
            frame.add_css_class("card")
            widget = self._load_thumb(path, 240, 160)
            frame.set_child(widget)
            col.append(frame)

            cap = Gtk.Label(label=caption)
            cap.add_css_class("caption")
            cap.add_css_class("dim-label")
            cap.set_halign(Gtk.Align.CENTER)
            col.append(cap)

            img_row.append(col)

        card.append(img_row)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_margin_start(12)
        btn_row.set_margin_end(12)
        btn_row.set_margin_bottom(12)
        btn_row.set_homogeneous(True)

        keep_btn   = Gtk.ToggleButton(label=_("Keep existing"))
        import_btn = Gtk.ToggleButton(label=_("Import new"))
        both_btn   = Gtk.ToggleButton(label=_("Keep both"))
        keep_btn.set_active(True)

        def on_keep(b, ip=iphone_path):
            if b.get_active():
                import_btn.set_active(False)
                both_btn.set_active(False)
                self.duplicate_decisions[str(ip)] = "skip"
            elif not import_btn.get_active() and not both_btn.get_active():
                b.set_active(True)

        def on_import(b, ip=iphone_path):
            if b.get_active():
                keep_btn.set_active(False)
                both_btn.set_active(False)
                self.duplicate_decisions[str(ip)] = "import"
            elif not keep_btn.get_active() and not both_btn.get_active():
                b.set_active(True)

        def on_both(b, ip=iphone_path):
            if b.get_active():
                keep_btn.set_active(False)
                import_btn.set_active(False)
                self.duplicate_decisions[str(ip)] = "both"
            elif not keep_btn.get_active() and not import_btn.get_active():
                b.set_active(True)

        keep_btn.connect("toggled", on_keep)
        import_btn.connect("toggled", on_import)
        both_btn.connect("toggled", on_both)

        btn_row.append(keep_btn)
        btn_row.append(import_btn)
        btn_row.append(both_btn)
        card.append(btn_row)

        return card

    def _load_thumb(self, path: Path, w: int, h: int) -> Gtk.Widget:
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), w, h, True)
            pic = Gtk.Picture.new_for_pixbuf(pixbuf)
            pic.set_can_shrink(True)
            pic.set_content_fit(Gtk.ContentFit.CONTAIN)
            pic.set_size_request(w, h)
            return pic
        except Exception:
            ph = Gtk.Image.new_from_icon_name("image-missing-symbolic")
            ph.set_pixel_size(48)
            ph.set_size_request(w, h)
            return ph

    def _on_skip_all(self, _btn):
        for iphone_path, _ in self.duplicates:
            self.duplicate_decisions[str(iphone_path)] = "skip"
        self._on_review_continue(None)

    def _on_import_all(self, _btn):
        for iphone_path, _ in self.duplicates:
            self.duplicate_decisions[str(iphone_path)] = "import"
        self._on_review_continue(None)

    def _on_review_continue(self, _btn):
        for iphone_path, _ in self.duplicates:
            decision = self.duplicate_decisions.get(str(iphone_path), "skip")
            if decision in ("import", "both"):
                self.to_import.append(iphone_path)
        self._start_import()

    def _start_import(self):
        total = len(self.to_import)
        self._set_progress(
            _("Importing…"),
            ngettext(
                "%d file is being copied.",
                "%d files are being copied.",
                total,
            ) % total
        )
        self._show_state(STATE_IMPORTING)
        threading.Thread(target=self._do_import, daemon=True).start()

    def _do_import(self):
        photo_path = Path(self.settings.get("photo_path") or Path.home() / "Photos")
        structure = self.settings.get("structure", "year_month")
        total = len(self.to_import)
        imported = 0

        for i, src in enumerate(self.to_import):
            try:
                mtime = datetime.fromtimestamp(src.stat().st_mtime)
                dst = dest_path(photo_path, structure, src.name, mtime)

                # "keep both" case: find a unique filename.
                if dst.exists():
                    stem, suffix = dst.stem, dst.suffix
                    counter = 1
                    while dst.exists():
                        dst = dst.parent / f"{stem}_{counter}{suffix}"
                        counter += 1

                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

                aae = src.with_suffix(".AAE")
                if not aae.exists():
                    aae = src.with_suffix(".aae")
                if aae.exists() and dst.suffix.lower() in (".jpg", ".jpeg", ".heic", ".heif", ".png", ".dng"):
                    apply_aae_edits(dst, aae)

                imported += 1
            except Exception:
                pass

            frac = (i + 1) / total if total > 0 else 1.0
            GLib.idle_add(self._update_progress, frac, f"{i + 1} / {total}", src.name)

        self.import_count = imported
        GLib.idle_add(self._on_import_done)

    def _on_import_done(self):
        unmount_iphone(MOUNT_POINT)
        # Backup flow lives in main_window; on_done_cb triggers it here.
        self._finish()

    def _finish(self, note: str | None = None):
        n = self.import_count
        dup_n = len(self.duplicates)
        skipped = sum(1 for d in self.duplicate_decisions.values() if d == "skip")

        desc_parts = [
            ngettext("%d file imported", "%d files imported", n) % n
        ]
        if dup_n:
            desc_parts.append(
                ngettext("%d duplicate found", "%d duplicates found", dup_n) % dup_n
            )
        if skipped:
            desc_parts.append(
                ngettext("%d skipped", "%d skipped", skipped) % skipped
            )
        if note:
            desc_parts.append(note)

        self.done_status.set_description(" · ".join(desc_parts))
        self._show_state(STATE_DONE)
        self.on_done_cb(self.import_count)

    def _show_error(self, message: str, show_deps: bool = False):
        unmount_iphone(MOUNT_POINT)
        self.error_status.set_description(message)
        self.error_deps_group.set_visible(show_deps)
        self._show_state(STATE_ERROR)

    def _on_retry(self, _btn):
        self._show_state(STATE_WAITING)
        self._start_detection_poll()

    def _set_progress(self, title: str, subtitle: str = ""):
        self.progress_title.set_text(title)
        self.progress_subtitle.set_text(subtitle)
        self.progress_bar.set_fraction(0)
        self.progress_bar.set_text("")
        self.progress_detail.set_text("")

    def _update_progress(self, fraction: float, text: str, detail: str = ""):
        self.progress_bar.set_fraction(min(fraction, 1.0))
        self.progress_bar.set_text(f"{int(fraction * 100)}%")
        if text:
            self.progress_subtitle.set_text(text)
        if detail:
            self.progress_detail.set_text(detail)
