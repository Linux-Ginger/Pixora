#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — main_window.py
#  by LinuxGinger
# ─────────────────────────────────────────────

import os
import threading
import subprocess
import sys
import json
import hashlib
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, GdkPixbuf, Gio

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

IMPORTER_PATH    = os.path.join(os.path.dirname(__file__), "..", "importer", "main.py")
DOCS_DIR         = os.path.join(os.path.dirname(__file__), "..", "docs")
CONFIG_PATH      = os.path.expanduser("~/.config/pixora/settings.json")
CACHE_DIR        = os.path.expanduser("~/.cache/pixora/thumbnails")
THUMB_SIZE       = 180
BATCH_SIZE       = 30
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".mp4", ".mov"}
BACKUP_FSTYPES   = {"ext4","ext3","ext2","ntfs","exfat","fuseblk","btrfs","xfs","vfat"}


def importer_installed():
    return os.path.exists(IMPORTER_PATH)

def get_logo_path(dark_mode):
    return os.path.join(DOCS_DIR, f"pixora-logo-{'dark' if dark_mode else 'light'}.png")

def save_settings(settings):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(settings, f, indent=2)

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
        print(f"Drive detectie fout: {e}")
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

# ── Thumbnail cache ──────────────────────────────────────────────────
def get_cache_path(photo_path):
    mtime = str(os.path.getmtime(photo_path))
    key   = hashlib.md5((photo_path + mtime).encode()).hexdigest()
    return os.path.join(CACHE_DIR, key + ".png")

def load_thumbnail(photo_path):
    cache_path = get_cache_path(photo_path)
    if os.path.exists(cache_path):
        try:
            return GdkPixbuf.Pixbuf.new_from_file(cache_path)
        except Exception:
            pass
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(photo_path, THUMB_SIZE, THUMB_SIZE, True)
        os.makedirs(CACHE_DIR, exist_ok=True)
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


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, settings):
        super().__init__(application=app)
        self.settings        = settings
        self.photos          = []       # Gesorteerde lijst van paden
        self.thumb_cache     = {}       # index → GdkPixbuf
        self.thumb_widgets   = {}       # index → Gtk.Button widget in grid
        self.current_index   = 0
        self.settings_drives = []
        self.observer        = None
        self._loading        = False
        self._load_id        = 0        # Versie ID om oude loads te negeren

        self.set_title("Pixora")
        self.set_default_size(1100, 700)

        self.style_manager = Adw.StyleManager.get_default()
        self.style_manager.connect("notify::dark", self.on_dark_mode_changed)

        self.main_stack = Gtk.Stack()
        self.main_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.main_stack.set_transition_duration(200)
        self.main_stack.add_named(self.build_grid_page(),   "grid")
        self.main_stack.add_named(self.build_viewer_page(), "viewer")

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(self.build_header())
        toolbar_view.set_content(self.main_stack)
        toolbar_view.add_bottom_bar(self.build_bottombar())
        self.set_content(toolbar_view)

        photo_path = self.settings.get("photo_path", "")
        if photo_path:
            os.makedirs(photo_path, exist_ok=True)

        GLib.idle_add(self.load_photos)
        self.connect("close-request", self.on_close)

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
        self.start_load()
        return False

    def on_close(self, window):
        self.stop_watcher()
        return False

    # ── Header ──────────────────────────────────────────────────────
    def build_header(self):
        self.header = Adw.HeaderBar()
        header = self.header
        header.add_css_class("flat")

        logo_path = get_logo_path(self.is_dark())
        self.logo_picture = Gtk.Picture()
        if os.path.exists(logo_path):
            self.logo_picture.set_filename(logo_path)
        self.logo_picture.set_size_request(140, 36)
        self.logo_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        header.pack_start(self.logo_picture)

        self.sort_model = Gtk.StringList()
        for item in ["Datum (nieuwste eerst)", "Datum (oudste eerst)", "Naam (A-Z)", "Naam (Z-A)"]:
            self.sort_model.append(item)

        self.sort_combo = Gtk.DropDown(model=self.sort_model)
        self.sort_combo.set_size_request(180, -1)
        self.sort_combo.connect("notify::selected", self.on_sort_changed)
        header.pack_start(self.sort_combo)

        settings_btn = Gtk.Button(icon_name="preferences-system-symbolic")
        settings_btn.add_css_class("flat")
        settings_btn.set_tooltip_text("Instellingen")
        settings_btn.connect("clicked", self.on_settings_clicked)
        header.pack_end(settings_btn)

        return header

    # ── Grid pagina ──────────────────────────────────────────────────
    def build_grid_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_vexpand(True)
        outer.set_hexpand(True)

        self.content_stack = Gtk.Stack()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_transition_duration(150)
        self.content_stack.set_vexpand(True)
        self.content_stack.set_hexpand(True)

        # Laadscherm
        spinner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        spinner_box.set_halign(Gtk.Align.CENTER)
        spinner_box.set_valign(Gtk.Align.CENTER)
        spinner_box.set_vexpand(True)

        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(48, 48)

        self.spinner_label = Gtk.Label(label="Foto's laden...")
        self.spinner_label.add_css_class("dim-label")

        spinner_box.append(self.spinner)
        spinner_box.append(self.spinner_label)
        self.content_stack.add_named(spinner_box, "loading")

        # Foto grid
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        self.flow_box = Gtk.FlowBox()
        self.flow_box.set_valign(Gtk.Align.START)
        self.flow_box.set_max_children_per_line(12)
        self.flow_box.set_min_children_per_line(2)
        self.flow_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flow_box.set_row_spacing(4)
        self.flow_box.set_column_spacing(4)
        self.flow_box.set_margin_top(8)
        self.flow_box.set_margin_bottom(8)
        self.flow_box.set_margin_start(8)
        self.flow_box.set_margin_end(8)

        scroll.set_child(self.flow_box)
        self.content_stack.add_named(scroll, "grid")

        # Lege staat
        status_page = Adw.StatusPage()
        status_page.set_icon_name("image-missing-symbolic")
        status_page.set_title("Geen foto's gevonden")
        status_page.set_description("Sluit je iPhone aan om foto's te importeren")
        status_page.set_vexpand(True)
        status_page.set_hexpand(True)
        self.content_stack.add_named(status_page, "empty")

        self.content_stack.set_visible_child_name("loading")
        outer.append(self.content_stack)
        return outer

    # ── Viewer pagina ────────────────────────────────────────────────
    def build_viewer_page(self):
        viewer_area = Gtk.Overlay()
        viewer_area.set_vexpand(True)
        viewer_area.set_hexpand(True)

        # Zwarte achtergrond
        bg = Gtk.Box()
        bg.set_vexpand(True)
        bg.set_hexpand(True)
        bg.set_css_classes(["pixora-viewer-bg"])

        css = Gtk.CssProvider()
        css.load_from_string(".pixora-viewer-bg { background-color: black; }")
        bg.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        viewer_area.set_child(bg)

        # Foto — geladen op achtergrond thread
        self.photo_picture = Gtk.Picture()
        self.photo_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.photo_picture.set_vexpand(True)
        self.photo_picture.set_hexpand(True)
        viewer_area.add_overlay(self.photo_picture)

        # Sluiten knop rechtsboven
        close_btn = Gtk.Button(icon_name="window-close-symbolic")
        close_btn.add_css_class("osd")
        close_btn.add_css_class("circular")
        close_btn.set_halign(Gtk.Align.END)
        close_btn.set_valign(Gtk.Align.START)
        close_btn.set_margin_top(16)
        close_btn.set_margin_end(16)
        close_btn.set_size_request(40, 40)
        close_btn.connect("clicked", self.close_viewer)
        viewer_area.add_overlay(close_btn)

        # Bestandsnaam linksboven
        self.viewer_title = Gtk.Label(label="")
        self.viewer_title.add_css_class("osd")
        self.viewer_title.set_halign(Gtk.Align.START)
        self.viewer_title.set_valign(Gtk.Align.START)
        self.viewer_title.set_margin_top(20)
        self.viewer_title.set_margin_start(16)
        viewer_area.add_overlay(self.viewer_title)

        # Pijl links — iets hoger dan midden
        self.prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.prev_btn.add_css_class("osd")
        self.prev_btn.add_css_class("circular")
        self.prev_btn.set_halign(Gtk.Align.START)
        self.prev_btn.set_valign(Gtk.Align.CENTER)
        self.prev_btn.set_margin_start(16)
        self.prev_btn.set_margin_bottom(60)
        self.prev_btn.set_size_request(48, 48)
        self.prev_btn.connect("clicked", self.prev_photo)
        viewer_area.add_overlay(self.prev_btn)

        # Pijl rechts
        self.next_btn = Gtk.Button(icon_name="go-next-symbolic")
        self.next_btn.add_css_class("osd")
        self.next_btn.add_css_class("circular")
        self.next_btn.set_halign(Gtk.Align.END)
        self.next_btn.set_valign(Gtk.Align.CENTER)
        self.next_btn.set_margin_end(16)
        self.next_btn.set_margin_bottom(60)
        self.next_btn.set_size_request(48, 48)
        self.next_btn.connect("clicked", self.next_photo)
        viewer_area.add_overlay(self.next_btn)

        # Counter onderaan midden
        self.viewer_counter = Gtk.Label(label="")
        self.viewer_counter.add_css_class("osd")
        self.viewer_counter.set_halign(Gtk.Align.CENTER)
        self.viewer_counter.set_valign(Gtk.Align.END)
        self.viewer_counter.set_margin_bottom(20)
        viewer_area.add_overlay(self.viewer_counter)

        # Keyboard navigatie
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self.on_viewer_key)
        self.add_controller(key_ctrl)

        return viewer_area

    # ── Onderste balk ────────────────────────────────────────────────
    def build_bottombar(self):
        bar = Gtk.ActionBar()

        self.photo_count_label = Gtk.Label(label="0 foto's")
        self.photo_count_label.add_css_class("dim-label")
        bar.pack_start(self.photo_count_label)

        if importer_installed():
            import_btn = Gtk.Button(label="📱  Importeer van iPhone")
            import_btn.add_css_class("suggested-action")
            import_btn.add_css_class("pill")
            import_btn.connect("clicked", self.open_importer)
            bar.pack_end(import_btn)

        return bar

    # ── Foto's laden ─────────────────────────────────────────────────
    def load_photos(self):
        photo_path = self.settings.get("photo_path", "")
        if not photo_path or not os.path.exists(photo_path):
            self.show_empty_state()
            return False

        # Scan bestanden
        photos = []
        for root, dirs, files in os.walk(photo_path):
            for file in files:
                if os.path.splitext(file)[1].lower() in IMAGE_EXTENSIONS:
                    photos.append(os.path.join(root, file))

        if not photos:
            self.show_empty_state()
            self.start_watcher(photo_path)
            return False

        self.photos = photos
        self.apply_sort()

        # Direct teller zetten
        self.photo_count_label.set_text(f"{len(self.photos)} foto's")

        self.start_load()
        self.start_watcher(photo_path)
        return False

    def start_load(self):
        """Start een nieuwe laadoperatie — annuleert eventuele vorige."""
        self._load_id += 1
        load_id = self._load_id
        self._loading = True

        # Grid leegmaken
        while True:
            child = self.flow_box.get_first_child()
            if child is None:
                break
            self.flow_box.remove(child)

        self.thumb_widgets = {}
        self.content_stack.set_visible_child_name("loading")
        self.spinner.start()
        self.spinner_label.set_text(f"Foto's laden... 0 / {len(self.photos)}")

        thread = threading.Thread(
            target=self._load_thread,
            args=(load_id, list(self.photos)),
            daemon=True
        )
        thread.start()

    def _load_thread(self, load_id, photos):
        """Achtergrond thread — laadt thumbnails en stuurt batches naar UI."""
        total = len(photos)
        batch = []

        for i, path in enumerate(photos):
            if load_id != self._load_id:
                return  # Geannuleerd

            pixbuf = load_thumbnail(path)
            batch.append((i, path, pixbuf))

            if len(batch) >= BATCH_SIZE:
                GLib.idle_add(self._apply_batch, load_id, list(batch), i + 1, total)
                batch = []
                time.sleep(0.005)

        if batch and load_id == self._load_id:
            GLib.idle_add(self._apply_batch, load_id, list(batch), total, total)

        GLib.idle_add(self._load_done, load_id, total)

    def _apply_batch(self, load_id, batch, loaded, total):
        if load_id != self._load_id:
            return False

        # Na eerste batch: toon grid
        if self.content_stack.get_visible_child_name() == "loading":
            self.spinner.stop()
            self.content_stack.set_visible_child_name("grid")

        self.spinner_label.set_text(f"Foto's laden... {loaded} / {total}")

        for index, path, pixbuf in batch:
            if pixbuf:
                picture = Gtk.Picture.new_for_pixbuf(pixbuf)
            else:
                picture = Gtk.Picture()
            picture.set_size_request(THUMB_SIZE, THUMB_SIZE)
            picture.set_content_fit(Gtk.ContentFit.COVER)

            pic_css = Gtk.CssProvider()
            pic_css.load_from_string("picture { border-radius: 8px; overflow: hidden; }")
            picture.get_style_context().add_provider(pic_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

            btn = Gtk.Button()
            btn.set_child(picture)
            btn.add_css_class("flat")
            btn.set_overflow(Gtk.Overflow.HIDDEN)
            btn.set_size_request(THUMB_SIZE, THUMB_SIZE)

            # Afgeronde hoeken via CSS
            css = Gtk.CssProvider()
            css.load_from_string("""
                button { border-radius: 16px; }
                button picture { border-radius: 16px; }
            """)
            btn.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

            idx = index
            btn.connect("clicked", lambda b, i=idx: self.open_photo(i))
            self.flow_box.append(btn)
            self.thumb_widgets[index] = btn

        return False

    def _load_done(self, load_id, total):
        if load_id != self._load_id:
            return False
        self.spinner.stop()
        self.content_stack.set_visible_child_name("grid")
        self.photo_count_label.set_text(f"{total} foto's")
        self._loading = False
        return False

    def show_empty_state(self):
        self.spinner.stop()
        self.content_stack.set_visible_child_name("empty")
        self.photo_count_label.set_text("0 foto's")
        self._loading = False

    # ── Sorteren ─────────────────────────────────────────────────────
    def apply_sort(self):
        index = self.sort_combo.get_selected()
        if index == 0:
            self.photos.sort(key=os.path.getmtime, reverse=True)
        elif index == 1:
            self.photos.sort(key=os.path.getmtime)
        elif index == 2:
            self.photos.sort(key=lambda p: os.path.basename(p).lower())
        elif index == 3:
            self.photos.sort(key=lambda p: os.path.basename(p).lower(), reverse=True)

    def on_sort_changed(self, combo, _):
        if not self.photos:
            return
        if hasattr(self, "_sort_timer") and self._sort_timer:
            GLib.source_remove(self._sort_timer)
        self._sort_timer = GLib.timeout_add(400, self._do_sort)

    def _do_sort(self):
        self._sort_timer = None
        self.apply_sort()
        self.start_load()
        return False

    # ── Foto viewer ──────────────────────────────────────────────────
    def open_photo(self, index):
        self.current_index = index
        self.header.set_visible(False)
        self.photo_picture.set_pixbuf(None)
        self.main_stack.set_visible_child_name("viewer")
        self._viewer_load_id = getattr(self, "_viewer_load_id", 0) + 1
        load_id = self._viewer_load_id
        threading.Thread(
            target=self._load_full_photo,
            args=(self.photos[index], load_id),
            daemon=True
        ).start()

    def _load_full_photo(self, path, load_id):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        except Exception:
            pixbuf = None
        if load_id == self._viewer_load_id:
            GLib.idle_add(self._show_full_photo, pixbuf, path)

    def _show_full_photo(self, pixbuf, path):
        if pixbuf:
            self.photo_picture.set_pixbuf(pixbuf)
        import datetime
        mtime = os.path.getmtime(path)
        datum = datetime.datetime.fromtimestamp(mtime).strftime("%-d %B %Y  %H:%M")
        self.viewer_title.set_text(f"{os.path.basename(path)}  —  {datum}")
        self.viewer_counter.set_text(f"{self.current_index + 1} / {len(self.photos)}")
        self.prev_btn.set_sensitive(self.current_index > 0)
        self.next_btn.set_sensitive(self.current_index < len(self.photos) - 1)
        return False

    def prev_photo(self, btn=None):
        if self.current_index > 0:
            self.current_index -= 1
            self._viewer_load_id = getattr(self, "_viewer_load_id", 0) + 1
            load_id = self._viewer_load_id
            threading.Thread(
                target=self._load_full_photo,
                args=(self.photos[self.current_index], load_id),
                daemon=True
            ).start()

    def next_photo(self, btn=None):
        if self.current_index < len(self.photos) - 1:
            self.current_index += 1
            self._viewer_load_id = getattr(self, "_viewer_load_id", 0) + 1
            load_id = self._viewer_load_id
            threading.Thread(
                target=self._load_full_photo,
                args=(self.photos[self.current_index], load_id),
                daemon=True
            ).start()

    def close_viewer(self, btn=None):
        self._viewer_load_id = getattr(self, "_viewer_load_id", 0) + 1
        self.photo_picture.set_pixbuf(None)
        self.header.set_visible(True)
        self.main_stack.set_visible_child_name("grid")

    def on_viewer_key(self, controller, keyval, keycode, state):
        if self.main_stack.get_visible_child_name() != "viewer":
            return False
        if keyval == 65361:
            self.prev_photo()
            return True
        elif keyval == 65363:
            self.next_photo()
            return True
        elif keyval == 65307:
            self.close_viewer()
            return True
        return False

    # ── Instellingen ─────────────────────────────────────────────────
    def on_settings_clicked(self, btn):
        dialog = Adw.PreferencesDialog()
        dialog.set_title("Instellingen")

        page = Adw.PreferencesPage()
        page.set_title("Algemeen")
        page.set_icon_name("preferences-system-symbolic")

        # Foto map
        folder_group = Adw.PreferencesGroup()
        folder_group.set_title("Foto map")
        folder_group.set_description("Waar worden je foto's opgeslagen")

        self.folder_row = Adw.ActionRow()
        self.folder_row.set_title("Huidige map")
        self.folder_row.set_subtitle(self.settings.get("photo_path", "Niet ingesteld"))

        change_folder_btn = Gtk.Button(label="Wijzigen")
        change_folder_btn.add_css_class("flat")
        change_folder_btn.set_valign(Gtk.Align.CENTER)
        change_folder_btn.connect("clicked", lambda b: self.change_folder(dialog))
        self.folder_row.add_suffix(change_folder_btn)
        folder_group.add(self.folder_row)
        page.add(folder_group)

        # Mapstructuur
        structure_group = Adw.PreferencesGroup()
        structure_group.set_title("Mapstructuur")
        structure_group.set_description("Hoe worden je foto's georganiseerd")
        current_structure = self.settings.get("structure", "year_month")

        self.radio_flat = Gtk.CheckButton()
        self.radio_flat.set_active(current_structure == "flat")
        self.radio_flat.connect("toggled", lambda b: self.on_structure_changed("flat", b))
        flat_row = Adw.ActionRow(title="Plat", subtitle="Alles in één map")
        flat_row.add_prefix(Gtk.Image.new_from_icon_name("folder-symbolic"))
        flat_row.add_prefix(self.radio_flat)
        flat_row.set_activatable_widget(self.radio_flat)
        structure_group.add(flat_row)

        self.radio_year = Gtk.CheckButton()
        self.radio_year.set_group(self.radio_flat)
        self.radio_year.set_active(current_structure == "year")
        self.radio_year.connect("toggled", lambda b: self.on_structure_changed("year", b))
        year_row = Adw.ActionRow(title="Per jaar", subtitle="2024/   2025/")
        year_row.add_prefix(Gtk.Image.new_from_icon_name("folder-open-symbolic"))
        year_row.add_prefix(self.radio_year)
        year_row.set_activatable_widget(self.radio_year)
        structure_group.add(year_row)

        self.radio_month = Gtk.CheckButton()
        self.radio_month.set_group(self.radio_flat)
        self.radio_month.set_active(current_structure == "year_month")
        self.radio_month.connect("toggled", lambda b: self.on_structure_changed("year_month", b))
        month_row = Adw.ActionRow(title="Per jaar/maand", subtitle="2024/2024-03/   2024/2024-04/")
        month_row.add_prefix(Gtk.Image.new_from_icon_name("view-list-symbolic"))
        month_row.add_prefix(self.radio_month)
        month_row.set_activatable_widget(self.radio_month)
        structure_group.add(month_row)
        page.add(structure_group)

        # Duplicate detectie
        dup_group = Adw.PreferencesGroup()
        dup_group.set_title("Duplicate detectie")
        dup_group.set_description("Hoe streng worden duplicaten gedetecteerd")
        current_threshold = self.settings.get("duplicate_threshold", 2)

        self.radio_strict = Gtk.CheckButton()
        self.radio_strict.set_active(current_threshold == 1)
        self.radio_strict.connect("toggled", lambda b: self.on_threshold_changed(1, b))
        strict_row = Adw.ActionRow(title="Streng", subtitle="Alleen exact dezelfde foto's")
        strict_row.add_prefix(Gtk.Image.new_from_icon_name("security-high-symbolic"))
        strict_row.add_prefix(self.radio_strict)
        strict_row.set_activatable_widget(self.radio_strict)
        dup_group.add(strict_row)

        self.radio_normal = Gtk.CheckButton()
        self.radio_normal.set_group(self.radio_strict)
        self.radio_normal.set_active(current_threshold == 2)
        self.radio_normal.connect("toggled", lambda b: self.on_threshold_changed(2, b))
        normal_row = Adw.ActionRow(title="Normaal", subtitle="Bijna identieke foto's worden gedetecteerd")
        normal_row.add_prefix(Gtk.Image.new_from_icon_name("security-medium-symbolic"))
        normal_row.add_prefix(self.radio_normal)
        normal_row.set_activatable_widget(self.radio_normal)
        dup_group.add(normal_row)

        self.radio_loose = Gtk.CheckButton()
        self.radio_loose.set_group(self.radio_strict)
        self.radio_loose.set_active(current_threshold == 3)
        self.radio_loose.connect("toggled", lambda b: self.on_threshold_changed(3, b))
        loose_row = Adw.ActionRow(title="Soepel", subtitle="Ook licht bewerkte foto's worden gedetecteerd")
        loose_row.add_prefix(Gtk.Image.new_from_icon_name("security-low-symbolic"))
        loose_row.add_prefix(self.radio_loose)
        loose_row.set_activatable_widget(self.radio_loose)
        dup_group.add(loose_row)
        page.add(dup_group)

        # Backup
        backup_group = Adw.PreferencesGroup()
        backup_group.set_title("Automatische backup")
        backup_group.set_description("Backup naar externe USB schijf na elke import")

        self.settings_backup_switch = Gtk.Switch()
        self.settings_backup_switch.set_valign(Gtk.Align.CENTER)
        self.settings_backup_switch.set_active(bool(self.settings.get("backup_uuid")))
        self.settings_backup_switch.connect("notify::active", self.on_settings_backup_toggle)

        backup_toggle_row = Adw.ActionRow(title="Automatische backup", subtitle="Synchroniseert na elke import")
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

        self.settings_drive_row = Adw.ActionRow(title="Backup schijf", subtitle="Alleen externe schijven")
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

        change_backup_folder_btn = Gtk.Button(label="Wijzigen")
        change_backup_folder_btn.add_css_class("flat")
        change_backup_folder_btn.set_valign(Gtk.Align.CENTER)
        change_backup_folder_btn.connect("clicked", self.on_settings_change_backup_folder)
        self.settings_backup_folder_row.add_suffix(change_backup_folder_btn)
        backup_group.add(self.settings_backup_folder_row)
        page.add(backup_group)

        dialog.add(page)
        dialog.present(self)

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
        dialog.set_title("Kies map op backup schijf")
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
        file_dialog.set_title("Kies foto map")
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

    def open_importer(self, btn):
        subprocess.Popen([sys.executable, IMPORTER_PATH])