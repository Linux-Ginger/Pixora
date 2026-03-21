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
from collections import defaultdict

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

IMPORTER_PATH    = os.path.join(os.path.dirname(__file__), "..", "importer", "main.py")
DOCS_DIR         = os.path.join(os.path.dirname(__file__), "..", "docs")
CONFIG_PATH      = os.path.expanduser("~/.config/pixora/settings.json")
CACHE_DIR        = os.path.expanduser("~/.cache/pixora/thumbnails")
TILE_CACHE_DIR   = os.path.expanduser("~/.cache/pixora/tiles")
THUMB_SIZE       = 180
BATCH_SIZE       = 30
TILE_SIZE        = 256
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".mp4", ".mov"}
BACKUP_FSTYPES   = {"ext4","ext3","ext2","ntfs","exfat","fuseblk","btrfs","xfs","vfat"}

MONTHS_NL_FULL = [
    "", "januari", "februari", "maart", "april", "mei", "juni",
    "juli", "augustus", "september", "oktober", "november", "december"
]
MONTHS_NL = [
    "", "jan", "feb", "mrt", "apr", "mei", "jun",
    "jul", "aug", "sep", "okt", "nov", "dec"
]


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

def format_date_header(dt):
    return f"{dt.day} {MONTHS_NL_FULL[dt.month]} {dt.year}"

# ── GPS EXIF uitlezen ────────────────────────────────────────────────
def get_gps_coords(photo_path):
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS, GPSTAGS
        img  = Image.open(photo_path)
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

# ── Kaart tile helper ────────────────────────────────────────────────
def lat_lon_to_tile_float(lat, lon, zoom):
    n     = 2 ** zoom
    x     = (lon + 180) / 360 * n
    lat_r = math.radians(lat)
    y     = (1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n
    return x, y

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


# ── Tijdlijn scrollbar ───────────────────────────────────────────────
class TimelineBar(Gtk.ScrolledWindow):
    def __init__(self, scroll_callback):
        super().__init__()
        self.scroll_callback = scroll_callback
        self.entries  = []
        self._buttons = []

        self.set_size_request(60, -1)
        self.set_vexpand(True)
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.box.set_valign(Gtk.Align.FILL)
        self.box.set_vexpand(True)
        self.set_child(self.box)

    def set_entries(self, entries):
        self.entries  = entries
        self._buttons = []

        while True:
            child = self.box.get_first_child()
            if child is None:
                break
            self.box.remove(child)

        for label_str, frac in entries:
            is_year = label_str.isdigit() and len(label_str) == 4

            btn = Gtk.Button(label=label_str)
            btn.add_css_class("flat")
            btn.set_vexpand(True)
            btn.set_valign(Gtk.Align.CENTER)

            btn_css = Gtk.CssProvider()
            if is_year:
                btn_css.load_from_string("""
                    button { font-size: 10px; font-weight: bold;
                             color: @window_fg_color; padding: 2px 4px; min-height: 0; }
                """)
            else:
                btn_css.load_from_string("""
                    button { font-size: 9px; color: alpha(@window_fg_color, 0.5);
                             padding: 1px 4px; min-height: 0; }
                """)
            btn.get_style_context().add_provider(btn_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

            f = frac
            btn.connect("clicked", lambda b, fr=f: self.scroll_callback(fr))
            self.box.append(btn)
            self._buttons.append(btn)

    def highlight(self, frac):
        if not self.entries or not self._buttons:
            return
        closest = min(range(len(self.entries)), key=lambda i: abs(self.entries[i][1] - frac))
        for i, btn in enumerate(self._buttons):
            css = Gtk.CssProvider()
            if i == closest:
                css.load_from_string("""
                    button { color: #e95420; font-weight: bold;
                             font-size: 10px; padding: 2px 4px; min-height: 0; }
                """)
            else:
                is_year = self.entries[i][0].isdigit() and len(self.entries[i][0]) == 4
                if is_year:
                    css.load_from_string("""
                        button { font-size: 10px; font-weight: bold;
                                 color: @window_fg_color; padding: 2px 4px; min-height: 0; }
                    """)
                else:
                    css.load_from_string("""
                        button { font-size: 9px; color: alpha(@window_fg_color, 0.5);
                                 padding: 1px 4px; min-height: 0; }
                    """)
            btn.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_USER)


# ── Kaartvenster ─────────────────────────────────────────────────────
class MapWindow(Adw.Window):
    def __init__(self, parent, markers):
        super().__init__()
        self.set_title("Kaartweergave")
        self.set_default_size(900, 650)
        self.set_transient_for(parent)
        self.set_modal(False)

        self.markers    = markers
        self.zoom       = 10 if markers else 7
        self.tile_cache = {}
        self._drag_start = None

        if markers:
            avg_lat = sum(m[0] for m in markers) / len(markers)
            avg_lon = sum(m[1] for m in markers) / len(markers)
        else:
            avg_lat, avg_lon = 52.3, 5.3

        tx, ty = lat_lon_to_tile_float(avg_lat, avg_lon, self.zoom)
        self.offset_x = tx * TILE_SIZE - 450
        self.offset_y = ty * TILE_SIZE - 325

        self.draw_area = Gtk.DrawingArea()
        self.draw_area.set_draw_func(self.on_draw)
        self.draw_area.set_vexpand(True)
        self.draw_area.set_hexpand(True)

        scroll_ctrl = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL |
            Gtk.EventControllerScrollFlags.DISCRETE
        )
        scroll_ctrl.connect("scroll", self.on_scroll)
        self.draw_area.add_controller(scroll_ctrl)

        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin",  self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end",    self.on_drag_end)
        self.draw_area.add_controller(drag)

        self._mouse_x = 450
        self._mouse_y = 325
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self.on_mouse_motion)
        self.draw_area.add_controller(motion)

        header = Adw.HeaderBar()

        zoom_in_btn = Gtk.Button(icon_name="zoom-in-symbolic")
        zoom_in_btn.add_css_class("flat")
        zoom_in_btn.set_tooltip_text("Inzoomen")
        zoom_in_btn.connect("clicked", lambda _: self.zoom_by(1))
        header.pack_end(zoom_in_btn)

        zoom_out_btn = Gtk.Button(icon_name="zoom-out-symbolic")
        zoom_out_btn.add_css_class("flat")
        zoom_out_btn.set_tooltip_text("Uitzoomen")
        zoom_out_btn.connect("clicked", lambda _: self.zoom_by(-1))
        header.pack_end(zoom_out_btn)

        count_label = Gtk.Label(label=f"{len(markers)} foto's met GPS")
        count_label.add_css_class("dim-label")
        header.set_title_widget(count_label)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(self.draw_area)
        self.set_content(box)

        GLib.idle_add(self._request_visible_tiles)

    def _get_visible_tiles(self, width, height):
        z    = self.zoom
        n    = 2 ** z
        tx0  = int(self.offset_x / TILE_SIZE)
        ty0  = int(self.offset_y / TILE_SIZE)
        px0  = -(self.offset_x % TILE_SIZE)
        py0  = -(self.offset_y % TILE_SIZE)

        tiles = []
        py, ty = py0, ty0
        while py < height + TILE_SIZE:
            px, tx = px0, tx0
            while px < width + TILE_SIZE:
                if 0 <= tx < n and 0 <= ty < n:
                    tiles.append((z, tx, ty, px, py))
                px += TILE_SIZE
                tx += 1
            py += TILE_SIZE
            ty += 1
        return tiles

    def _request_visible_tiles(self):
        w = self.draw_area.get_width() or 900
        h = self.draw_area.get_height() or 650
        for z, tx, ty, _, _ in self._get_visible_tiles(w, h):
            key = (z, tx, ty)
            if key not in self.tile_cache:
                self.tile_cache[key] = None
                threading.Thread(target=self._load_tile, args=(z, tx, ty), daemon=True).start()
        return False

    def _load_tile(self, z, tx, ty):
        import io
        if not CAIRO_AVAILABLE:
            return

        key        = (z, tx, ty)
        cache_path = os.path.join(TILE_CACHE_DIR, f"{z}_{tx}_{ty}.png")

        if os.path.exists(cache_path):
            try:
                surface = cairo.ImageSurface.create_from_png(cache_path)
                GLib.idle_add(self._tile_loaded, key, surface)
                return
            except Exception:
                pass

        try:
            url = f"https://tile.openstreetmap.org/{z}/{tx}/{ty}.png"
            req = urllib.request.Request(url, headers={"User-Agent": "Pixora/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            os.makedirs(TILE_CACHE_DIR, exist_ok=True)
            with open(cache_path, "wb") as f:
                f.write(data)
            surface = cairo.ImageSurface.create_from_png(io.BytesIO(data))
            GLib.idle_add(self._tile_loaded, key, surface)
        except Exception:
            GLib.idle_add(self._tile_loaded, key, None)

    def _tile_loaded(self, key, surface):
        self.tile_cache[key] = surface
        self.draw_area.queue_draw()
        return False

    def on_draw(self, area, cr, width, height):
        # Achtergrond
        cr.set_source_rgb(0.85, 0.87, 0.88)
        cr.paint()

        for z, tx, ty, px, py in self._get_visible_tiles(width, height):
            surface = self.tile_cache.get((z, tx, ty))
            if surface:
                cr.set_source_surface(surface, px, py)
                cr.paint()

        # Markers
        for lat, lon, filename, datum in self.markers:
            tx_f, ty_f = lat_lon_to_tile_float(lat, lon, self.zoom)
            mx = tx_f * TILE_SIZE - self.offset_x
            my = ty_f * TILE_SIZE - self.offset_y
            if -20 <= mx <= width + 20 and -20 <= my <= height + 20:
                # Schaduw
                cr.set_source_rgba(0, 0, 0, 0.25)
                cr.arc(mx + 1, my + 2, 9, 0, 2 * math.pi)
                cr.fill()
                # Rode stip
                cr.set_source_rgb(0.914, 0.329, 0.125)
                cr.arc(mx, my, 9, 0, 2 * math.pi)
                cr.fill()
                # Witte rand
                cr.set_source_rgb(1, 1, 1)
                cr.set_line_width(2)
                cr.arc(mx, my, 9, 0, 2 * math.pi)
                cr.stroke()

        GLib.idle_add(self._request_visible_tiles)

    def zoom_by(self, delta, cx=None, cy=None):
        w = self.draw_area.get_width() or 900
        h = self.draw_area.get_height() or 650
        if cx is None:
            cx = getattr(self, "_mouse_x", w / 2)
        if cy is None:
            cy = getattr(self, "_mouse_y", h / 2)
        new_zoom = max(3, min(19, self.zoom + delta))
        if new_zoom != self.zoom:
            scale         = 2 ** (new_zoom - self.zoom)
            self.offset_x = (self.offset_x + cx) * scale - cx
            self.offset_y = (self.offset_y + cy) * scale - cy
            self.zoom     = new_zoom
            self.draw_area.queue_draw()

    def on_scroll(self, ctrl, dx, dy):
        self.zoom_by(-1 if dy > 0 else 1)
        return True

    def on_drag_begin(self, gesture, x, y):
        self._drag_start = (self.offset_x, self.offset_y)

    def on_drag_update(self, gesture, dx, dy):
        if self._drag_start:
            w = self.draw_area.get_width() or 900
            h = self.draw_area.get_height() or 650
            n = 2 ** self.zoom
            max_x = n * TILE_SIZE - w
            max_y = n * TILE_SIZE - h
            self.offset_x = max(0, min(max_x, self._drag_start[0] - dx))
            self.offset_y = max(0, min(max_y, self._drag_start[1] - dy))
            self.draw_area.queue_draw()

    def on_drag_end(self, gesture, dx, dy):
        self._drag_start = None

    def on_mouse_motion(self, ctrl, x, y):
        self._mouse_x = x
        self._mouse_y = y


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, settings):
        super().__init__(application=app)
        self.settings        = settings
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
        self.load_photos()
        return False

    def on_close(self, window):
        self.stop_watcher()
        return False

    # ── Header ──────────────────────────────────────────────────────
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
        for item in ["Datum (nieuwste eerst)", "Datum (oudste eerst)", "Naam (A-Z)", "Naam (Z-A)"]:
            self.sort_model.append(item)

        self.sort_combo = Gtk.DropDown(model=self.sort_model)
        self.sort_combo.set_size_request(180, -1)
        self.sort_combo.connect("notify::selected", self.on_sort_changed)
        self.header.pack_start(self.sort_combo)

        self.map_btn = Gtk.Button(label="🗺")
        self.map_btn.add_css_class("flat")
        self.map_btn.set_tooltip_text("Kaartweergave")
        self.map_btn.connect("clicked", self.open_map)
        self.header.pack_end(self.map_btn)

        self.select_btn = Gtk.Button(label="Selecteren")
        self.select_btn.add_css_class("flat")
        self.select_btn.connect("clicked", self.toggle_select_mode)
        self.header.pack_end(self.select_btn)

        settings_btn = Gtk.Button(icon_name="preferences-system-symbolic")
        settings_btn.add_css_class("flat")
        settings_btn.set_tooltip_text("Instellingen")
        settings_btn.connect("clicked", self.on_settings_clicked)
        self.header.pack_end(settings_btn)

        return self.header

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

        grid_with_timeline = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        grid_with_timeline.set_vexpand(True)
        grid_with_timeline.set_hexpand(True)

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
        self.scroll.get_vadjustment().connect("value-changed", self._on_scroll_changed)
        grid_with_timeline.append(self.scroll)

        self.timeline = TimelineBar(self._on_timeline_scroll)
        grid_with_timeline.append(self.timeline)

        self.content_stack.add_named(grid_with_timeline, "grid")

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

    # ── Kaart ────────────────────────────────────────────────────────
    def open_map(self, btn=None):
        if hasattr(self, "_map_open") and self._map_open:
            return
        self._map_open = True
        self.map_btn.set_sensitive(False)
        threading.Thread(target=self._load_gps_and_open_map, daemon=True).start()

    def _load_gps_and_open_map(self):
        markers = []
        for path in self.photos:
            coords = get_gps_coords(path)
            if coords:
                lat, lon = coords
                filename = os.path.basename(path)
                try:
                    mtime = os.path.getmtime(path)
                    datum = datetime.datetime.fromtimestamp(mtime).strftime("%-d %B %Y")
                except Exception:
                    datum = ""
                markers.append((lat, lon, filename, datum))
        GLib.idle_add(self._show_map_window, markers)

    def _show_map_window(self, markers):
        map_win = MapWindow(self, markers)
        map_win.connect("close-request", self._on_map_closed)
        map_win.present()
        return False

    def _on_map_closed(self, win):
        self._map_open = False
        self.map_btn.set_sensitive(True)
        return False

    # ── Viewer pagina ────────────────────────────────────────────────
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

        delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
        delete_btn.add_css_class("osd")
        delete_btn.add_css_class("circular")
        delete_btn.set_halign(Gtk.Align.END)
        delete_btn.set_valign(Gtk.Align.START)
        delete_btn.set_margin_top(16)
        delete_btn.set_margin_end(68)
        delete_btn.set_size_request(40, 40)
        delete_btn.connect("clicked", self.on_delete_current)
        viewer_area.add_overlay(delete_btn)

        self.viewer_title = Gtk.Label(label="")
        self.viewer_title.add_css_class("osd")
        self.viewer_title.set_halign(Gtk.Align.START)
        self.viewer_title.set_valign(Gtk.Align.START)
        self.viewer_title.set_margin_top(20)
        self.viewer_title.set_margin_start(16)
        viewer_area.add_overlay(self.viewer_title)

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

        self.viewer_counter = Gtk.Label(label="")
        self.viewer_counter.add_css_class("osd")
        self.viewer_counter.set_halign(Gtk.Align.CENTER)
        self.viewer_counter.set_valign(Gtk.Align.END)
        self.viewer_counter.set_margin_bottom(20)
        viewer_area.add_overlay(self.viewer_counter)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self.on_viewer_key)
        self.add_controller(key_ctrl)

        return viewer_area

    # ── Onderste balk ────────────────────────────────────────────────
    def build_bottombar(self):
        self.bottom_stack = Gtk.Stack()
        self.bottom_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.bottom_stack.set_transition_duration(150)

        normal_bar = Gtk.ActionBar()
        self.photo_count_label = Gtk.Label(label="0 foto's")
        self.photo_count_label.add_css_class("dim-label")
        normal_bar.pack_start(self.photo_count_label)

        if importer_installed():
            import_btn = Gtk.Button(label="📱  Importeer van iPhone")
            import_btn.add_css_class("suggested-action")
            import_btn.add_css_class("pill")
            import_btn.connect("clicked", self.open_importer)
            normal_bar.pack_end(import_btn)

        self.bottom_stack.add_named(normal_bar, "normal")

        select_bar = Gtk.ActionBar()
        self.select_count_label = Gtk.Label(label="0 geselecteerd")
        self.select_count_label.add_css_class("dim-label")
        select_bar.pack_start(self.select_count_label)

        delete_selected_btn = Gtk.Button(label="Verwijderen")
        delete_selected_btn.add_css_class("destructive-action")
        delete_selected_btn.add_css_class("pill")
        delete_selected_btn.connect("clicked", self.on_delete_selected)
        select_bar.pack_end(delete_selected_btn)

        self.bottom_stack.add_named(select_bar, "select")
        self.bottom_stack.set_visible_child_name("normal")

        return self.bottom_stack

    # ── Tijdlijn ─────────────────────────────────────────────────────
    def _on_timeline_scroll(self, fraction):
        adj   = self.scroll.get_vadjustment()
        total = adj.get_upper() - adj.get_lower() - adj.get_page_size()
        if total > 0:
            adj.set_value(adj.get_lower() + fraction * total)

    def _on_scroll_changed(self, adj):
        total = adj.get_upper() - adj.get_lower() - adj.get_page_size()
        if total > 0:
            frac = (adj.get_value() - adj.get_lower()) / total
            self.timeline.highlight(frac)

    def _update_timeline_from_positions(self):
        if not self.date_widgets:
            return False
        adj   = self.scroll.get_vadjustment()
        total = adj.get_upper()
        if total == 0:
            return False

        entries = []
        for date_str, label in self.date_widgets.items():
            coords = label.translate_coordinates(self.grid_box, 0, 0)
            if coords is None:
                continue
            x, y = coords
            frac = max(0.0, min(1.0, y / total))
            entries.append((date_str, frac))

        entries.sort(key=lambda e: e[1])
        self.timeline.set_entries(entries)
        return False

    def _build_timeline_entries(self, groups):
        if not groups:
            return []
        total_photos = sum(len(indices) for _, _, indices in groups)
        if total_photos == 0:
            return []

        entries    = []
        cumulative = 0
        last_year  = None

        for date_str, date_obj, indices in groups:
            frac     = cumulative / total_photos
            year_str = str(date_obj.year)
            if year_str != last_year:
                entries.append((year_str, frac))
                last_year = year_str
            entries.append((MONTHS_NL[date_obj.month], frac))
            cumulative += len(indices)

        return entries

    # ── Selectie modus ───────────────────────────────────────────────
    def toggle_select_mode(self, btn=None):
        self._select_mode = not self._select_mode
        self._selected.clear()

        if self._select_mode:
            self.select_btn.set_label("Annuleren")
            self.select_btn.add_css_class("suggested-action")
            self.bottom_stack.set_visible_child_name("select")
            self.select_count_label.set_text("0 geselecteerd")
        else:
            self.select_btn.set_label("Selecteren")
            self.select_btn.remove_css_class("suggested-action")
            self.bottom_stack.set_visible_child_name("normal")
            self._update_all_selection_visuals()

    def _update_all_selection_visuals(self):
        for index, widget in self.thumb_widgets.items():
            self._update_thumb_visual(index, widget)

    def _update_thumb_visual(self, index, widget):
        btn, check_box = widget
        check_box.set_visible(index in self._selected)

    # ── Foto's laden ─────────────────────────────────────────────────
    def load_photos(self):
        photo_path = self.settings.get("photo_path", "")
        if not photo_path or not os.path.exists(photo_path):
            self.show_empty_state()
            return False

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
        self.photo_count_label.set_text(f"{len(self.photos)} foto's")
        self.start_load()
        self.start_watcher(photo_path)
        return False

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

        timeline_entries = self._build_timeline_entries(groups)
        GLib.idle_add(self.timeline.set_entries, timeline_entries)

        loaded = 0
        for date_str, date_obj, indices in groups:
            if load_id != self._load_id:
                return

            GLib.idle_add(self._add_date_group, load_id, date_str)

            batch = []
            for idx in indices:
                if load_id != self._load_id:
                    return
                pixbuf = load_thumbnail(photos[idx])
                batch.append((idx, photos[idx], pixbuf))
                loaded += 1

                if len(batch) >= BATCH_SIZE:
                    GLib.idle_add(self._apply_batch, load_id, list(batch), loaded, total)
                    batch = []
                    time.sleep(0.005)

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

        flow = Gtk.FlowBox()
        flow.set_valign(Gtk.Align.START)
        flow.set_max_children_per_line(12)
        flow.set_min_children_per_line(1)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_row_spacing(4)
        flow.set_column_spacing(4)
        flow.set_homogeneous(True)

        self._current_flow = flow
        self.grid_box.append(flow)

        if self.content_stack.get_visible_child_name() == "loading":
            self.spinner.stop()
            self.content_stack.set_visible_child_name("grid")

        return False

    def _apply_batch(self, load_id, batch, loaded, total):
        if load_id != self._load_id:
            return False

        self.spinner_label.set_text(f"Foto's laden... {loaded} / {total}")

        for index, path, pixbuf in batch:
            if pixbuf:
                picture = Gtk.Picture.new_for_pixbuf(pixbuf)
            else:
                picture = Gtk.Picture()
            picture.set_size_request(THUMB_SIZE, THUMB_SIZE)
            picture.set_content_fit(Gtk.ContentFit.COVER)

            overlay = Gtk.Overlay()
            overlay.set_size_request(THUMB_SIZE, THUMB_SIZE)
            overlay.set_child(picture)

            check_box = Gtk.Box()
            check_box.set_size_request(22, 22)
            check_box.set_halign(Gtk.Align.END)
            check_box.set_valign(Gtk.Align.END)
            check_box.set_margin_end(6)
            check_box.set_margin_bottom(6)
            check_box.set_visible(False)

            check_css = Gtk.CssProvider()
            check_css.load_from_string("""
                box { background-color: #e95420; border-radius: 6px;
                      min-width: 22px; min-height: 22px; }
            """)
            check_box.get_style_context().add_provider(check_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

            check_icon = Gtk.Image.new_from_icon_name("object-select-symbolic")
            check_icon.set_pixel_size(14)
            check_icon.set_halign(Gtk.Align.CENTER)
            check_icon.set_valign(Gtk.Align.CENTER)
            check_icon.set_hexpand(True)
            check_icon.set_vexpand(True)

            check_icon_css = Gtk.CssProvider()
            check_icon_css.load_from_string("image { color: white; }")
            check_icon.get_style_context().add_provider(check_icon_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

            check_box.append(check_icon)
            overlay.add_overlay(check_box)

            btn = Gtk.Button()
            btn.set_child(overlay)
            btn.set_overflow(Gtk.Overflow.HIDDEN)
            btn.set_size_request(THUMB_SIZE, THUMB_SIZE)

            css = Gtk.CssProvider()
            css.load_from_string("""
                button { border-radius: 8px; padding: 0; }
                button picture { border-radius: 8px; }
                button:hover { outline: 2px solid #e95420; outline-offset: -2px; border-radius: 8px; }
            """)
            btn.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

            idx = index
            btn.connect("clicked", lambda b, i=idx: self.on_thumb_clicked(i))

            self._current_flow.append(btn)
            self.thumb_widgets[index] = (btn, check_box)

        return False

    def _load_done(self, load_id, total):
        if load_id != self._load_id:
            return False
        self.spinner.stop()
        self.content_stack.set_visible_child_name("grid")
        self.photo_count_label.set_text(f"{total} foto's")
        self._loading = False
        GLib.timeout_add(300, self._update_timeline_from_positions)
        return False

    def show_empty_state(self):
        self.spinner.stop()
        self.content_stack.set_visible_child_name("empty")
        self.photo_count_label.set_text("0 foto's")
        self._loading = False

    # ── Thumbnail klik ───────────────────────────────────────────────
    def on_thumb_clicked(self, index):
        if self._select_mode:
            if index in self._selected:
                self._selected.discard(index)
            else:
                self._selected.add(index)
            self._update_thumb_visual(index, self.thumb_widgets[index])
            self.select_count_label.set_text(f"{len(self._selected)} geselecteerd")
        else:
            self.open_photo(index)

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
        if self._sort_timer:
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
        self._viewer_load_id += 1
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
            self._viewer_load_id += 1
            load_id = self._viewer_load_id
            threading.Thread(
                target=self._load_full_photo,
                args=(self.photos[self.current_index], load_id),
                daemon=True
            ).start()

    def next_photo(self, btn=None):
        if self.current_index < len(self.photos) - 1:
            self.current_index += 1
            self._viewer_load_id += 1
            load_id = self._viewer_load_id
            threading.Thread(
                target=self._load_full_photo,
                args=(self.photos[self.current_index], load_id),
                daemon=True
            ).start()

    def close_viewer(self, btn=None):
        self._viewer_load_id += 1
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

    # ── Verwijderen ──────────────────────────────────────────────────
    def on_delete_current(self, btn):
        path = self.photos[self.current_index]
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Foto verwijderen?",
            body=f"Weet je zeker dat je '{os.path.basename(path)}' wilt verwijderen? Dit kan niet ongedaan worden gemaakt."
        )
        dialog.add_response("cancel", "Annuleren")
        dialog.add_response("delete", "Verwijderen")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_delete_current_response, path)
        dialog.present()

    def _on_delete_current_response(self, dialog, response, path):
        if response != "delete":
            return
        try:
            os.remove(path)
            cache_path = get_cache_path(path)
            if os.path.exists(cache_path):
                os.remove(cache_path)
        except Exception as e:
            print(f"Verwijderen mislukt: {e}")
            return

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
        dialog.add_response("cancel", "Annuleren")
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

        for path in paths_to_delete:
            try:
                os.remove(path)
                cache_path = get_cache_path(path)
                if os.path.exists(cache_path):
                    os.remove(cache_path)
            except Exception as e:
                print(f"Verwijderen mislukt: {e}")

        self.toggle_select_mode()
        self.load_photos()

    # ── Instellingen ─────────────────────────────────────────────────
    def on_settings_clicked(self, btn):
        dialog = Adw.PreferencesDialog()
        dialog.set_title("Instellingen")

        page = Adw.PreferencesPage()
        page.set_title("Algemeen")
        page.set_icon_name("preferences-system-symbolic")

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
