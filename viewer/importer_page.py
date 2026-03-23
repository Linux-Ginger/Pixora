#!/usr/bin/env python3
# ─────────────────────────────────────────────
#  Pixora — importer_page.py
#  Importer als ingebedde pagina (niet los venster)
#  by LinuxGinger
# ─────────────────────────────────────────────

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, GdkPixbuf, Gio, Gdk, Pango

import os
import sys
import json
import shutil
import hashlib
import subprocess
import threading
import tempfile
from pathlib import Path
from datetime import datetime

try:
    from PIL import Image
    import imagehash
    HAS_IMAGEHASH = True
except ImportError:
    HAS_IMAGEHASH = False

# ─── Paden ───────────────────────────────────────────────────────────────────

CONFIG_PATH  = Path.home() / ".config" / "pixora" / "settings.json"
CACHE_DIR    = Path.home() / ".cache"  / "pixora"
HASH_CACHE   = CACHE_DIR / "hashes.json"
MOUNT_POINT  = Path(tempfile.gettempdir()) / "pixora_iphone"

BACKUP_FSTYPES = {"ext4", "ext3", "ext2", "ntfs", "exfat", "fuseblk", "btrfs", "xfs", "vfat"}
SUPPORTED_EXT  = {".jpg", ".jpeg", ".png", ".heic", ".dng", ".mp4", ".mov", ".m4v", ".webp"}

# Duplicate threshold → maximale hash-afstand
THRESHOLD_MAP = {1: 2, 2: 6, 3: 12}

# Eigen vierkante thumbnail-cache voor de importer
IMPORT_THUMB_DIR = Path.home() / ".cache" / "pixora" / "import_thumbs"
SELECT_THUMB     = 160  # vierkant, pixels

# ─── States ──────────────────────────────────────────────────────────────────

STATE_WAITING   = "waiting"
STATE_DETECTED  = "detected"
STATE_MOUNTING  = "mounting"
STATE_SCANNING  = "scanning"
STATE_SELECTING = "selecting"
STATE_HASHING   = "hashing"
STATE_REVIEWING = "reviewing"
STATE_IMPORTING = "importing"
STATE_BACKUP    = "backup"
STATE_DONE      = "done"
STATE_ERROR     = "error"

# ─── Hulpfuncties ────────────────────────────────────────────────────────────

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
    else:  # flat
        return base / filename


def detect_iphone() -> str | None:
    """Geeft UDID terug als een iPhone verbonden is, anders None."""
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
    mountpoint.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["fusermount", "-uz", str(mountpoint)], capture_output=True, timeout=5)
    except Exception:
        pass
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


def scan_dcim(mountpoint: Path, progress_cb=None) -> list[Path]:
    """
    Scan de DCIM-map van de iPhone.
    Gebruikt expliciete 2-level iteratie (DCIM/100APPLE/files) in plaats van
    os.walk zodat een trage of falende submap de andere niet blokkeert.
    """
    dcim = mountpoint / "DCIM"
    if not dcim.exists():
        return []

    # Verzamel submappen (bijv. 100APPLE, 101APPLE, ...)
    try:
        subdirs = sorted(p for p in dcim.iterdir() if p.is_dir())
    except OSError:
        return []

    files = []
    for subdir in subdirs:
        # Retry tot 3× per submap bij FUSE-lees-errors
        for attempt in range(3):
            try:
                entries = sorted(subdir.iterdir())
                for entry in entries:
                    if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXT:
                        files.append(entry)
                    if progress_cb:
                        progress_cb(len(files))
                break  # Gelukt, ga naar volgende submap
            except OSError:
                if attempt == 2:
                    pass  # Geef op na 3 pogingen, ga door met de rest
                else:
                    import time as _time
                    _time.sleep(0.3)  # Kort wachten voor retry

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
    """Bouw een hash-index van alle foto's in het archief."""
    cache = load_hash_cache()
    hashes = {}

    all_files = []
    for root, _, files in os.walk(photo_path):
        for fn in files:
            if Path(fn).suffix.lower() in SUPPORTED_EXT:
                all_files.append(Path(root) / fn)

    for i, fp in enumerate(all_files):
        if progress_cb:
            progress_cb(i, len(all_files), fp.name)
        try:
            stat = fp.stat()
        except OSError:
            continue
        cache_key = f"{fp}:{int(stat.st_mtime)}:{stat.st_size}"
        if cache_key in cache:
            ph = cache[cache_key]
        else:
            ph = perceptual_hash(fp)
            if ph:
                cache[cache_key] = ph
        if ph:
            hashes[str(fp)] = ph

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
    """Zoek het mountpoint van een schijf op UUID."""
    try:
        result = subprocess.run(
            ["lsblk", "-o", "UUID,MOUNTPOINT", "-J"],
            capture_output=True, text=True
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
    """Cache-sleutel op basis van pad + mtime + grootte."""
    try:
        stat = photo_path.stat()
        key = hashlib.md5(f"{photo_path}:{int(stat.st_mtime)}:{stat.st_size}".encode()).hexdigest()
    except OSError:
        key = hashlib.md5(str(photo_path).encode()).hexdigest()
    return IMPORT_THUMB_DIR / (key + ".png")


def _crop_to_square(pixbuf) -> "GdkPixbuf.Pixbuf":
    """Snijd het midden van een pixbuf bij tot een vierkant."""
    w = pixbuf.get_width()
    h = pixbuf.get_height()
    size = min(w, h)
    x = (w - size) // 2
    y = (h - size) // 2
    return pixbuf.new_subpixbuf(x, y, size, size)


def load_select_thumb(photo_path: Path):
    """
    Laad een vierkante thumbnail (SELECT_THUMB × SELECT_THUMB) voor de selectiepagina.
    Sla het resultaat op in een eigen cache zodat de volgende keer direct geladen kan worden.
    """
    IMPORT_THUMB_DIR.mkdir(parents=True, exist_ok=True)
    cache = _import_cache_path(photo_path)

    # Cache-hit: direct laden (al vierkant en goede kwaliteit)
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
            # Eerste frame pakken zonder seek — betrouwbaarder op FUSE/USB
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
            # Laad op dubbele grootte voor betere kwaliteit na downscale
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


# ─── ImporterPage ─────────────────────────────────────────────────────────────

class ImporterPage(Gtk.Box):
    def __init__(self, on_back_cb, on_done_cb):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_back_cb = on_back_cb
        self.on_done_cb = on_done_cb
        self.settings = load_settings()
        self.state = STATE_WAITING

        # Import-staat
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

        self._build_ui()

    # ─── UI opbouw ───────────────────────────────────────────────────────────

    def _build_ui(self):
        # Eigen header bar (flat) met terugknop
        header = Adw.HeaderBar()
        header.add_css_class("flat")

        back_btn = Gtk.Button(icon_name="go-previous-symbolic")
        back_btn.add_css_class("flat")
        back_btn.set_tooltip_text("Terug")
        back_btn.connect("clicked", self._on_back_clicked)
        header.pack_start(back_btn)

        title_lbl = Gtk.Label(label="Importeren")
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

    # ─── Activeren / deactiveren ──────────────────────────────────────────────

    def activate(self):
        """Wordt aangeroepen als de pagina zichtbaar wordt."""
        self.settings = load_settings()
        self._show_state(STATE_WAITING)
        self._start_detection_poll()

    def deactivate(self):
        """Wordt aangeroepen als de pagina verborgen wordt."""
        if self._poll_timer_id is not None:
            GLib.source_remove(self._poll_timer_id)
            self._poll_timer_id = None
        unmount_iphone(MOUNT_POINT)

    def _on_back_clicked(self, _btn):
        self.deactivate()
        self.on_back_cb()

    # ─── Pagina's bouwen ─────────────────────────────────────────────────────

    def _build_waiting_page(self):
        status = Adw.StatusPage()
        status.set_icon_name("computer-symbolic")
        status.set_title("Verbind je iPhone")
        status.set_description(
            "Sluit je iPhone aan via een USB-kabel en ontgrendel het scherm.\n"
            "Als je iPhone vraagt om deze computer te vertrouwen, tik dan op 'Vertrouw'."
        )

        clamp = Adw.Clamp()
        clamp.set_maximum_size(420)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_bottom(32)
        box.set_margin_start(12)
        box.set_margin_end(12)

        header_lbl = Gtk.Label(label="Controleer")
        header_lbl.set_halign(Gtk.Align.START)
        header_lbl.add_css_class("heading")
        box.append(header_lbl)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")

        for icon, title, subtitle in [
            ("drive-removable-media-symbolic", "USB-kabel",              "Gebruik bij voorkeur de originele Apple-kabel"),
            ("security-medium-symbolic",       "Vertrouw deze computer", "Tik op 'Vertrouw' als je iPhone dat vraagt"),
            ("system-lock-screen-symbolic",    "Ontgrendeld scherm",     "Zorg dat je iPhone ontgrendeld is tijdens de import"),
            ("media-flash-symbolic",           "Gebruik een blauwe USB-poort", "USB 3.0 (blauw) is veel sneller dan zwarte USB 2.0 poorten"),
            ("weather-overcast-symbolic",      "iCloud foto's",          "iCloud Foto's uitgeschakeld? Dan staan alle foto's lokaal op je toestel en worden ze allemaal gevonden."),
            ("document-save-symbolic",         "Bestandsformaat",        "Zet op je iPhone: Instellingen → Foto's → 'Zet over naar Mac of pc' → 'Behoud originelen'"),
        ]:
            row = Adw.ActionRow()
            row.set_title(title)
            row.set_subtitle(subtitle)
            ic = Gtk.Image.new_from_icon_name(icon)
            ic.set_pixel_size(16)
            row.add_prefix(ic)
            listbox.append(row)

        box.append(listbox)

        spinner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        spinner_box.set_halign(Gtk.Align.CENTER)
        spinner_box.set_margin_top(8)
        spin = Gtk.Spinner()
        spin.start()
        spinner_box.append(spin)
        lbl = Gtk.Label(label="Zoeken naar iPhone…")
        lbl.add_css_class("dim-label")
        spinner_box.append(lbl)
        box.append(spinner_box)

        clamp.set_child(box)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        vbox.append(status)
        vbox.append(clamp)
        self.stack.add_named(vbox, "waiting")

    def _build_detected_page(self):
        status = Adw.StatusPage()
        status.set_icon_name("object-select-symbolic")
        status.set_title("iPhone gevonden")
        status.set_description("Je iPhone is verbonden en klaar om te importeren.")

        clamp = Adw.Clamp()
        clamp.set_maximum_size(420)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_bottom(32)
        box.set_margin_start(12)
        box.set_margin_end(12)

        info_group = Adw.PreferencesGroup()

        self.device_row = Adw.ActionRow()
        self.device_row.set_title("Apparaat")
        self.device_row.set_subtitle("iPhone")
        ic = Gtk.Image.new_from_icon_name("computer-symbolic")
        ic.set_pixel_size(16)
        self.device_row.add_prefix(ic)
        info_group.add(self.device_row)

        self.dest_row = Adw.ActionRow()
        self.dest_row.set_title("Opslaan in")
        self.dest_row.set_subtitle(self.settings.get("photo_path") or "~")
        ic2 = Gtk.Image.new_from_icon_name("folder-symbolic")
        ic2.set_pixel_size(16)
        self.dest_row.add_prefix(ic2)
        info_group.add(self.dest_row)

        struct = self.settings.get("structure", "year_month")
        struct_labels = {
            "flat":       "Alles in één map",
            "year":       "Per jaar",
            "year_month": "Per jaar/maand",
        }
        self.struct_row = Adw.ActionRow()
        self.struct_row.set_title("Mapstructuur")
        self.struct_row.set_subtitle(struct_labels.get(struct, struct))
        ic3 = Gtk.Image.new_from_icon_name("folder-open-symbolic")
        ic3.set_pixel_size(16)
        self.struct_row.add_prefix(ic3)
        info_group.add(self.struct_row)

        box.append(info_group)

        import_btn = Gtk.Button(label="Importeren")
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
        # CSS voor ronde hoeken op thumbnails
        thumb_css = Gtk.CssProvider()
        thumb_css.load_from_string(".thumb-item { border-radius: 8px; }")
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), thumb_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Koptekst
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

        # Foto-grid
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.select_flow = Gtk.FlowBox()
        self.select_flow.set_homogeneous(True)
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

        # Onderbalk
        action_bar = Gtk.ActionBar()

        sel_all_btn = Gtk.Button(label="Selecteer alles")
        sel_all_btn.connect("clicked", self._on_select_all)
        action_bar.pack_start(sel_all_btn)

        desel_all_btn = Gtk.Button(label="Deselecteer alles")
        desel_all_btn.connect("clicked", self._on_deselect_all)
        action_bar.pack_start(desel_all_btn)

        self.select_count_lbl = Gtk.Label()
        self.select_count_lbl.add_css_class("dim-label")
        action_bar.set_center_widget(self.select_count_lbl)

        self.select_continue_btn = Gtk.Button(label="Doorgaan")
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

        title_lbl = Gtk.Label(label="Mogelijke duplicaten")
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

        skip_all_btn = Gtk.Button(label="Alle overslaan")
        skip_all_btn.connect("clicked", self._on_skip_all)
        action_bar.pack_start(skip_all_btn)

        import_all_btn = Gtk.Button(label="Alle importeren")
        import_all_btn.connect("clicked", self._on_import_all)
        action_bar.pack_start(import_all_btn)

        continue_btn = Gtk.Button(label="Doorgaan met importeren")
        continue_btn.add_css_class("suggested-action")
        continue_btn.connect("clicked", self._on_review_continue)
        action_bar.pack_end(continue_btn)

        outer.append(action_bar)
        self.stack.add_named(outer, "review")

    def _build_done_page(self):
        self.done_status = Adw.StatusPage()
        self.done_status.set_icon_name("emblem-ok-symbolic")
        self.done_status.set_title("Import voltooid")

        clamp = Adw.Clamp()
        clamp.set_maximum_size(420)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_bottom(32)
        box.set_margin_start(12)
        box.set_margin_end(12)

        self.done_stats_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(self.done_stats_box)

        close_btn = Gtk.Button(label="Terug naar galerij")
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
        self.error_status.set_title("Er is een fout opgetreden")

        clamp = Adw.Clamp()
        clamp.set_maximum_size(420)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_bottom(32)
        box.set_margin_start(12)
        box.set_margin_end(12)

        self.error_deps_group = Adw.PreferencesGroup()
        self.error_deps_group.set_title("Installeer vereiste pakketten")
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

        retry_btn = Gtk.Button(label="Opnieuw proberen")
        retry_btn.add_css_class("pill")
        retry_btn.set_halign(Gtk.Align.CENTER)
        retry_btn.connect("clicked", self._on_retry)
        box.append(retry_btn)

        clamp.set_child(box)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        vbox.append(self.error_status)
        vbox.append(clamp)
        self.stack.add_named(vbox, "error")

    # ─── Scherm wisselen ─────────────────────────────────────────────────────

    def _show_state(self, state: str):
        self.state = state
        page_map = {
            STATE_WAITING:   "waiting",
            STATE_DETECTED:  "detected",
            STATE_MOUNTING:  "progress",
            STATE_SCANNING:  "progress",
            STATE_SELECTING: "selecting",
            STATE_HASHING:   "progress",
            STATE_REVIEWING: "review",
            STATE_IMPORTING: "progress",
            STATE_BACKUP:    "progress",
            STATE_DONE:      "done",
            STATE_ERROR:     "error",
        }
        self.stack.set_visible_child_name(page_map.get(state, "waiting"))

    # ─── iPhone detectie ─────────────────────────────────────────────────────

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

        # Zoek het bovenliggende venster op voor de dialog
        window = self.get_root()

        dialog = Adw.MessageDialog.new(
            window,
            "iPhone losgekoppeld",
            "Je iPhone is losgekoppeld tijdens het selecteren van foto's.\n"
            "Sluit de iPhone opnieuw aan en probeer het opnieuw."
        )
        dialog.add_response("cancel", "Annuleren")
        dialog.add_response("retry", "Opnieuw proberen")
        dialog.set_default_response("retry")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_disconnect_response)
        dialog.present()

    def _on_disconnect_response(self, dialog, response: str):
        self._disconnect_dialog_open = False
        self._show_state(STATE_WAITING)
        self._start_detection_poll()

    # ─── Import flow ─────────────────────────────────────────────────────────

    def _on_import_clicked(self, _btn):
        self._set_progress("iPhone koppelen…", "Even geduld, dit duurt maar even.")
        self._show_state(STATE_MOUNTING)
        threading.Thread(target=self._do_mount, daemon=True).start()

    def _do_mount(self):
        if not _cmd_available("ifuse") or not _cmd_available("idevice_id"):
            GLib.idle_add(self._show_error,
                "ifuse of libimobiledevice is niet geïnstalleerd. "
                "Installeer de vereiste pakketten hieronder.", True)
            return
        if not mount_iphone(self.udid, MOUNT_POINT):
            GLib.idle_add(self._show_error,
                "Kon de iPhone niet koppelen. Zorg dat het scherm ontgrendeld is "
                "en tik op 'Vertrouw' als dat wordt gevraagd.", False)
            return
        GLib.idle_add(self._start_scan)

    def _start_scan(self):
        self._set_progress("Foto's scannen…", "Zoeken naar foto's en video's op je iPhone.")
        self._show_state(STATE_SCANNING)
        self._start_detection_poll()
        threading.Thread(target=self._do_scan, daemon=True).start()

    def _do_scan(self):
        def on_progress(count):
            GLib.idle_add(self.progress_subtitle.set_text, f"{count} bestanden gevonden…")
        files = scan_dcim(MOUNT_POINT, progress_cb=on_progress)
        GLib.idle_add(self._on_scan_done, files)

    def _on_scan_done(self, files: list[Path]):
        self.iphone_files = files
        if not files:
            unmount_iphone(MOUNT_POINT)
            self._show_error(
                "Geen foto's of video's gevonden op de iPhone.\n"
                "Mogelijk zijn alle media al eerder geïmporteerd.", False)
            return
        self._show_selecting(files)

    def _start_hashing(self, files: list[Path]):
        self._set_progress("Duplicaten controleren…",
                           "Foto's worden vergeleken met je bestaande archief.")
        self._show_state(STATE_HASHING)
        threading.Thread(target=self._do_hashing, args=(files,), daemon=True).start()

    def _do_hashing(self, iphone_files: list[Path]):
        photo_path = Path(self.settings.get("photo_path") or Path.home() / "Photos")
        threshold_key = self.settings.get("duplicate_threshold", 2)
        max_dist = THRESHOLD_MAP.get(threshold_key, 6)

        def lib_progress(i, total, name):
            frac = (i / total) * 0.5 if total > 0 else 0
            GLib.idle_add(self._update_progress, frac, f"Archief scannen: {i}/{total}", name)

        library_hashes = build_library_hashes(photo_path, lib_progress)
        self.library_hashes = library_hashes

        duplicates: list[tuple[Path, Path]] = []
        new_files: list[Path] = []
        total = len(iphone_files)

        for i, fp in enumerate(iphone_files):
            frac = 0.5 + (i / total) * 0.5 if total > 0 else 0.5
            GLib.idle_add(self._update_progress, frac, f"iPhone scannen: {i + 1}/{total}", fp.name)
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

    # ─── Selectiepagina ──────────────────────────────────────────────────────

    def _show_selecting(self, files: list[Path]):
        n = len(files)
        self.select_title.set_text(f"{n} bestand{'en' if n != 1 else ''} gevonden")
        self.select_subtitle.set_text("Kies welke foto's en video's je wilt importeren.")

        # Alles standaard geselecteerd
        self.selected_files = {str(f) for f in files}
        self._update_select_count()

        # Verwijder oude kaarten
        while child := self.select_flow.get_first_child():
            self.select_flow.remove(child)

        # Voeg kaarten toe en laad thumbnails in achtergrond
        self._select_cards: dict[str, Gtk.CheckButton] = {}
        self._select_overlays: dict[str, Gtk.Overlay] = {}
        for fp in files:
            card, check, overlay = self._make_select_card(fp)
            self._select_cards[str(fp)] = check
            self._select_overlays[str(fp)] = overlay
            self.select_flow.append(card)

        self._show_state(STATE_SELECTING)

        # Thumbnails asynchroon laden
        threading.Thread(target=self._load_select_thumbs, args=(list(files),), daemon=True).start()

    def _make_select_card(self, fp: Path) -> tuple[Gtk.Widget, Gtk.CheckButton, Gtk.Overlay]:
        """Maakt een vierkante thumbnail-kaart met vinkje. Geeft (widget, checkbutton, overlay) terug."""
        overlay = Gtk.Overlay()
        overlay.set_size_request(SELECT_THUMB, SELECT_THUMB)
        overlay.set_overflow(Gtk.Overflow.HIDDEN)
        overlay.add_css_class("thumb-item")

        # Placeholder terwijl thumbnail laadt
        placeholder = Gtk.Image.new_from_icon_name("image-loading-symbolic")
        placeholder.set_pixel_size(32)
        placeholder.set_size_request(SELECT_THUMB, SELECT_THUMB)
        overlay.set_child(placeholder)

        # Klik op de kaart zelf togglet het vinkje
        click = Gtk.GestureClick.new()
        click.connect("pressed", lambda g, n, x, y, ip=str(fp): self._on_card_click(ip))
        overlay.add_controller(click)

        # Vinkje linksboven — alleen visueel, klik wordt afgehandeld door GestureClick
        check = Gtk.CheckButton()
        check.set_active(True)
        check.set_halign(Gtk.Align.START)
        check.set_valign(Gtk.Align.START)
        check.set_margin_top(4)
        check.set_margin_start(4)
        check.set_can_target(False)
        check.set_focusable(False)
        overlay.add_overlay(check)

        # Video-indicator rechtsonder
        ext = fp.suffix.lower()
        if ext in {".mp4", ".mov", ".m4v"}:
            video_lbl = Gtk.Label(label="▶")
            video_lbl.add_css_class("caption")
            video_lbl.set_halign(Gtk.Align.END)
            video_lbl.set_valign(Gtk.Align.END)
            video_lbl.set_margin_end(4)
            video_lbl.set_margin_bottom(4)
            overlay.add_overlay(video_lbl)

        return overlay, check, overlay

    def _load_select_thumbs(self, files: list[Path]):
        """Laad thumbnails één voor één — sneller op USB/FUSE dan parallel."""
        for fp in files:
            pixbuf = load_select_thumb(fp)
            if pixbuf is not None:
                GLib.idle_add(self._set_select_thumb, str(fp), pixbuf)

    def _set_select_thumb(self, path_str: str, pixbuf):
        """Vervang placeholder door echte thumbnail in de selectiekaart."""
        overlay = self._select_overlays.get(path_str)
        if overlay is None:
            return
        pic = Gtk.Picture.new_for_pixbuf(pixbuf)
        pic.set_can_shrink(False)
        pic.set_content_fit(Gtk.ContentFit.COVER)
        pic.set_size_request(SELECT_THUMB, SELECT_THUMB)
        overlay.set_child(pic)

    def _on_card_click(self, path_str: str):
        """Klik ergens op de kaart togglet het vinkje (enige toggle-handler)."""
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
        self.select_count_lbl.set_text(f"{n} van {total} geselecteerd")
        self.select_continue_btn.set_sensitive(n > 0)

    def _on_selecting_continue(self, _btn):
        selected = [f for f in self.iphone_files if str(f) in self.selected_files]
        if not HAS_IMAGEHASH:
            self.duplicates = []
            self.to_import = selected
            self._start_import()
            return
        self._start_hashing(selected)

    # ─── Duplicate review ────────────────────────────────────────────────────

    def _show_review(self, duplicates: list[tuple[Path, Path]]):
        n = len(duplicates)
        self.review_subtitle.set_text(
            f"{n} foto{'\'s' if n != 1 else ''} lijken al in je archief te staan. "
            "Kies per foto wat je wilt doen, of gebruik de knoppen onderaan voor alles tegelijk."
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

        # Bestandsnaam kop
        name_lbl = Gtk.Label(label=iphone_path.name)
        name_lbl.add_css_class("heading")
        name_lbl.set_halign(Gtk.Align.START)
        name_lbl.set_margin_top(12)
        name_lbl.set_margin_start(14)
        name_lbl.set_margin_bottom(8)
        card.append(name_lbl)

        # Twee thumbnails naast elkaar
        img_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        img_row.set_margin_start(12)
        img_row.set_margin_end(12)
        img_row.set_margin_bottom(10)

        for path, caption in [(iphone_path, "📱 iPhone — nieuw"),
                               (lib_path,    "🗂️ Archief — bestaand")]:
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

        # Knoppen
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_margin_start(12)
        btn_row.set_margin_end(12)
        btn_row.set_margin_bottom(12)
        btn_row.set_homogeneous(True)

        keep_btn   = Gtk.ToggleButton(label="Bestaande behouden")
        import_btn = Gtk.ToggleButton(label="Nieuwe importeren")
        both_btn   = Gtk.ToggleButton(label="Beide bewaren")
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

    # ─── Kopiëren ────────────────────────────────────────────────────────────

    def _start_import(self):
        total = len(self.to_import)
        self._set_progress(
            "Importeren…",
            f"{total} bestand{'en' if total != 1 else ''} worden gekopieerd."
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

                # Bij "beide bewaren": unieke naam
                if dst.exists():
                    stem, suffix = dst.stem, dst.suffix
                    counter = 1
                    while dst.exists():
                        dst = dst.parent / f"{stem}_{counter}{suffix}"
                        counter += 1

                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                imported += 1
            except Exception:
                pass

            frac = (i + 1) / total if total > 0 else 1.0
            GLib.idle_add(self._update_progress, frac, f"{i + 1} / {total}", src.name)

        self.import_count = imported
        GLib.idle_add(self._on_import_done)

    def _on_import_done(self):
        unmount_iphone(MOUNT_POINT)
        backup_uuid = self.settings.get("backup_uuid")
        if backup_uuid:
            self._start_backup()
        else:
            self._finish()

    # ─── Back-up ─────────────────────────────────────────────────────────────

    def _start_backup(self):
        self._set_progress("Back-up maken…",
                           "Foto's worden gesynchroniseerd naar je externe schijf.")
        self._show_state(STATE_BACKUP)
        threading.Thread(target=self._do_backup, daemon=True).start()

    def _do_backup(self):
        backup_uuid = self.settings.get("backup_uuid")
        backup_path_str = self.settings.get("backup_path")
        photo_path = Path(self.settings.get("photo_path") or Path.home() / "Photos")

        drive_root = get_backup_mountpoint(backup_uuid)
        if not drive_root:
            GLib.idle_add(self._finish, "Back-upschijf niet gevonden. Sluit de schijf aan en probeer opnieuw via de instellingen.")
            return

        backup_dest = Path(backup_path_str) if backup_path_str else drive_root / "Pixora"
        backup_dest.mkdir(parents=True, exist_ok=True)

        def rsync_progress(line):
            for part in line.split():
                if part.endswith("%"):
                    try:
                        frac = int(part[:-1]) / 100
                        GLib.idle_add(self._update_progress, frac, f"Back-up: {part}", "")
                    except ValueError:
                        pass

        success = False
        if _cmd_available("rsync"):
            try:
                proc = subprocess.Popen(
                    ["rsync", "-a", "--info=progress2",
                     str(photo_path) + "/", str(backup_dest) + "/"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True
                )
                for line in proc.stdout:
                    rsync_progress(line)
                proc.wait(timeout=600)
                success = proc.returncode == 0
            except Exception:
                success = False
        else:
            success = self._manual_backup(photo_path, backup_dest)

        GLib.idle_add(self._finish, None if success else
                      "Back-up gedeeltelijk mislukt. De import zelf is wel geslaagd.")

    def _manual_backup(self, src: Path, dst: Path) -> bool:
        try:
            all_src = []
            for root, _, files in os.walk(src):
                for fn in files:
                    all_src.append(Path(root) / fn)
            total = len(all_src)
            for i, sf in enumerate(all_src):
                rel = sf.relative_to(src)
                df = dst / rel
                df.parent.mkdir(parents=True, exist_ok=True)
                if not df.exists():
                    shutil.copy2(sf, df)
                frac = (i + 1) / total if total > 0 else 1.0
                GLib.idle_add(self._update_progress, frac, f"{i + 1} / {total}", sf.name)
            return True
        except Exception:
            return False

    # ─── Afgerond / fout ─────────────────────────────────────────────────────

    def _finish(self, note: str | None = None):
        n = self.import_count
        dup_n = len(self.duplicates)
        skipped = sum(1 for d in self.duplicate_decisions.values() if d == "skip")

        desc_parts = [f"{n} bestand{'en' if n != 1 else ''} geïmporteerd"]
        if dup_n:
            desc_parts.append(f"{dup_n} duplicaat{'s' if dup_n != 1 else ''} gevonden")
        if skipped:
            desc_parts.append(f"{skipped} overgeslagen")
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

    # ─── Voortgang helpers ───────────────────────────────────────────────────

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
