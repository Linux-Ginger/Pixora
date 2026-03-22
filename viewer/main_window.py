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
FILM_THUMB       = 70
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

def reverse_geocode(lat, lon):
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


# ── Kaart widget (in-app, geen apart venster) ────────────────────────
class MapWidget(Gtk.DrawingArea):
    def __init__(self, markers, open_photo_cb):
        super().__init__()
        self.markers        = markers
        self.open_photo_cb  = open_photo_cb
        self.zoom           = 10.0 if markers else 7.0
        self.tile_cache     = {}
        self._drag_start    = None
        self._mouse_x       = 450.0
        self._mouse_y       = 325.0
        self._hover_idx       = -1
        self._hover_pixbufs   = {}
        self._last_clusters   = []
        self._clusters_dirty  = True

        if markers:
            avg_lat = sum(m[0] for m in markers) / len(markers)
            avg_lon = sum(m[1] for m in markers) / len(markers)
        else:
            avg_lat, avg_lon = 52.3, 5.3

        tx, ty = lat_lon_to_tile_float(avg_lat, avg_lon, self.zoom)
        self.offset_x = tx * TILE_SIZE - 450.0
        self.offset_y = ty * TILE_SIZE - 325.0

        self.set_vexpand(True)
        self.set_hexpand(True)
        self.set_draw_func(self.on_draw)

        # Muiswiel — discreet
        scroll_discrete = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL |
            Gtk.EventControllerScrollFlags.DISCRETE
        )
        scroll_discrete.connect("scroll", self.on_scroll_discrete)
        self.add_controller(scroll_discrete)

        # Trackpad — smooth
        scroll_smooth = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll_smooth.connect("scroll", self.on_scroll_smooth)
        self.add_controller(scroll_smooth)

        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin",  self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end",    self.on_drag_end)
        self.add_controller(drag)

        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self.on_mouse_motion)
        self.add_controller(motion)

        click = Gtk.GestureClick.new()
        click.connect("released", self.on_click)
        self.add_controller(click)

        GLib.idle_add(self._request_visible_tiles)

    def _get_visible_tiles(self, width, height):
        z   = int(self.zoom)
        n   = 2 ** z
        tx0 = int(self.offset_x / TILE_SIZE)
        ty0 = int(self.offset_y / TILE_SIZE)
        px0 = -(self.offset_x % TILE_SIZE)
        py0 = -(self.offset_y % TILE_SIZE)

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
        w = self.get_width() or 900
        h = self.get_height() or 650
        for z, tx, ty, _, _ in self._get_visible_tiles(w, h):
            key = (z, tx, ty)
            if key not in self.tile_cache or self.tile_cache[key] is None:
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
        self.queue_draw()
        return False

    def on_draw(self, area, cr, width, height):
        cr.set_source_rgb(0.85, 0.87, 0.88)
        cr.paint()

        for z, tx, ty, px, py in self._get_visible_tiles(width, height):
            surface = self.tile_cache.get((z, tx, ty))
            if surface:
                cr.set_source_surface(surface, px, py)
                cr.paint()

        # Clusters + markers (alleen herberekenen na zoom/pan)
        if self._clusters_dirty:
            self._last_clusters  = self._get_clusters()
            self._clusters_dirty = False
        for mx, my, group in self._last_clusters:
            if -20 <= mx <= width + 20 and -20 <= my <= height + 20:
                count  = len(group)
                radius = 9 if count == 1 else 14

                cr.set_source_rgba(0, 0, 0, 0.25)
                cr.arc(mx + 1, my + 2, radius, 0, 2 * math.pi)
                cr.fill()

                cr.set_source_rgb(0.914, 0.329, 0.125)
                cr.arc(mx, my, radius, 0, 2 * math.pi)
                cr.fill()

                cr.set_source_rgb(1, 1, 1)
                cr.set_line_width(2)
                cr.arc(mx, my, radius, 0, 2 * math.pi)
                cr.stroke()

                if count > 1:
                    label = str(count)
                    cr.set_source_rgb(1, 1, 1)
                    cr.select_font_face("Sans", 0, 1)
                    cr.set_font_size(10)
                    extents = cr.text_extents(label)
                    cr.move_to(mx - extents.width / 2, my + extents.height / 2)
                    cr.show_text(label)

        # Hover preview
        if 0 <= self._hover_idx < len(self.markers):
            marker   = self.markers[self._hover_idx]
            lat, lon = marker[0], marker[1]
            filename = marker[2]
            datum    = marker[3]
            path     = marker[4]

            tx_f, ty_f = lat_lon_to_tile_float(lat, lon, self.zoom)
            mx = tx_f * TILE_SIZE - self.offset_x
            my = ty_f * TILE_SIZE - self.offset_y

            pixbuf = self._hover_pixbufs.get(path)
            pw     = pixbuf.get_width()  if pixbuf else 0
            ph     = pixbuf.get_height() if pixbuf else 0

            pad   = 10
            box_w = max(180, pw + pad * 2)
            box_h = (ph + pad if pixbuf else 0) + 52
            bx    = mx + 14
            by    = my - box_h - 6

            if bx + box_w > width - 4:
                bx = mx - box_w - 14
            if by < 4:
                by = my + 14
            if bx < 4:
                bx = 4

            cr.set_source_rgba(0.12, 0.12, 0.12, 0.94)
            self._rounded_rect(cr, bx, by, box_w, box_h, 10)
            cr.fill()

            if pixbuf:
                cr.save()
                self._rounded_rect(cr, bx + pad, by + pad, pw, ph, 6)
                cr.clip()
                Gdk.cairo_set_source_pixbuf(cr, pixbuf, bx + pad, by + pad)
                cr.paint()
                cr.restore()

            cr.set_source_rgb(1, 1, 1)
            cr.select_font_face("Sans", 0, 1)
            cr.set_font_size(11)
            cr.move_to(bx + pad, by + ph + pad + 18)
            cr.show_text(filename[:26] + "…" if len(filename) > 26 else filename)

            cr.set_source_rgba(1, 1, 1, 0.6)
            cr.select_font_face("Sans", 0, 0)
            cr.set_font_size(10)
            cr.move_to(bx + pad, by + ph + pad + 34)
            cr.show_text(datum)

            cr.set_source_rgba(0.914, 0.329, 0.125, 0.85)
            cr.set_font_size(9)
            cr.move_to(bx + pad, by + box_h - 10)
            cr.show_text("Klik om te openen")

        GLib.idle_add(self._request_visible_tiles)

    def _rounded_rect(self, cr, x, y, w, h, r):
        cr.move_to(x + r, y)
        cr.line_to(x + w - r, y)
        cr.arc(x + w - r, y + r, r, -1.5708, 0)
        cr.line_to(x + w, y + h - r)
        cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
        cr.line_to(x + r, y + h)
        cr.arc(x + r, y + h - r, r, 1.5708, 3.14159)
        cr.line_to(x, y + r)
        cr.arc(x + r, y + r, r, 3.14159, 4.71239)
        cr.close_path()

    def _load_hover_thumb(self, path):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 160, 120, True)
        except Exception:
            pixbuf = None
        GLib.idle_add(self._hover_thumb_loaded, path, pixbuf)

    def _hover_thumb_loaded(self, path, pixbuf):
        self._hover_pixbufs[path] = pixbuf
        self.queue_draw()
        return False

    def on_mouse_motion(self, ctrl, x, y):
        self._mouse_x = x
        self._mouse_y = y
        hover = -1
        for mx, my, group in self._last_clusters:
            radius = 9 if len(group) == 1 else 14
            if math.sqrt((x - mx) ** 2 + (y - my) ** 2) < radius + 4:
                hover = group[0]
                break
        if hover != self._hover_idx:
            self._hover_idx = hover
            if hover >= 0:
                path = self.markers[hover][4]
                if path not in self._hover_pixbufs:
                    threading.Thread(
                        target=self._load_hover_thumb,
                        args=(path,),
                        daemon=True
                    ).start()
            self.queue_draw()

    def on_click(self, gesture, n_press, x, y):
        for mx, my, group in self._last_clusters:
            radius = 9 if len(group) == 1 else 14
            if math.sqrt((x - mx) ** 2 + (y - my) ** 2) < radius + 4:
                paths = [self.markers[i][4] for i in group]
                GLib.idle_add(self.open_photo_cb, paths)
                return

    def _get_clusters(self):
        CLUSTER_RADIUS = 24
        assigned = [False] * len(self.markers)
        clusters = []
        for i, marker in enumerate(self.markers):
            if assigned[i]:
                continue
            tx_f, ty_f = lat_lon_to_tile_float(marker[0], marker[1], self.zoom)
            mx = tx_f * TILE_SIZE - self.offset_x
            my = ty_f * TILE_SIZE - self.offset_y
            group = [i]
            assigned[i] = True
            for j, other in enumerate(self.markers):
                if assigned[j]:
                    continue
                tx2, ty2 = lat_lon_to_tile_float(other[0], other[1], self.zoom)
                ox = tx2 * TILE_SIZE - self.offset_x
                oy = ty2 * TILE_SIZE - self.offset_y
                if math.sqrt((mx - ox) ** 2 + (my - oy) ** 2) < CLUSTER_RADIUS:
                    group.append(j)
                    assigned[j] = True
            clusters.append((mx, my, group))
        return clusters

    def zoom_by(self, delta, cx=None, cy=None):
        if cx is None:
            cx = self._mouse_x
        if cy is None:
            cy = self._mouse_y
        new_zoom = max(3.0, min(19.0, self.zoom + delta))
        if abs(new_zoom - self.zoom) > 0.001:
            scale         = 2 ** (new_zoom - self.zoom)
            self.offset_x = (self.offset_x + cx) * scale - cx
            self.offset_y = (self.offset_y + cy) * scale - cy
            self.zoom            = new_zoom
            self._clusters_dirty = True
            self._clamp_offset()
            GLib.idle_add(self._request_visible_tiles)
            self.queue_draw()

    def _clamp_offset(self):
        w     = self.get_width() or 900
        h     = self.get_height() or 650
        z     = int(self.zoom)
        n     = 2 ** z
        max_x = max(0, n * TILE_SIZE - w)
        max_y = max(0, n * TILE_SIZE - h)
        self.offset_x = max(0.0, min(float(max_x), self.offset_x))
        self.offset_y = max(0.0, min(float(max_y), self.offset_y))

    def on_scroll_discrete(self, ctrl, dx, dy):
        self.zoom_by(-1 if dy > 0 else 1)
        return True

    def on_scroll_smooth(self, ctrl, dx, dy):
        if abs(dy) < 1.0:
            self.zoom_by(-dy * 0.25)
            return True
        return False

    def on_drag_begin(self, gesture, x, y):
        self._drag_start = (self.offset_x, self.offset_y)

    def on_drag_update(self, gesture, dx, dy):
        if self._drag_start:
            self.offset_x        = self._drag_start[0] - dx
            self.offset_y        = self._drag_start[1] - dy
            self._clusters_dirty = True
            self._clamp_offset()
            self.queue_draw()

    def on_drag_end(self, gesture, dx, dy):
        self._drag_start = None


# ── Hoofdvenster ─────────────────────────────────────────────────────
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

        self.set_title("Pixora")
        self.set_default_size(1100, 700)

        self.style_manager = Adw.StyleManager.get_default()
        self.style_manager.connect("notify::dark", self.on_dark_mode_changed)

        self.main_stack = Gtk.Stack()
        self.main_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.main_stack.set_transition_duration(200)
        self.main_stack.add_named(self.build_grid_page(),   "grid")
        self.main_stack.add_named(self.build_viewer_page(), "viewer")
        self.main_stack.add_named(self.build_map_page(),    "map")

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(self.build_header())
        toolbar_view.set_content(self.main_stack)
        toolbar_view.add_bottom_bar(self.build_bottombar())
        self.set_content(toolbar_view)

        photo_path = self.settings.get("photo_path", "")
        if photo_path:
            os.makedirs(photo_path, exist_ok=True)

        self.set_hide_on_close(False)
        btn_layout = Gtk.Settings.get_default()
        btn_layout.set_property("gtk-decoration-layout", "icon:minimize,close")

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
        self.spinner_label = Gtk.Label(label="Foto's laden...")
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
        self.content_stack.add_named(self.scroll, "grid")

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

    # ── Kaart pagina (in-app) ─────────────────────────────────────────
    def build_map_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_vexpand(True)
        box.set_hexpand(True)

        map_header = Adw.HeaderBar()
        map_header.add_css_class("flat")

        back_btn = Gtk.Button(icon_name="go-previous-symbolic")
        back_btn.add_css_class("flat")
        back_btn.set_tooltip_text("Terug")
        back_btn.connect("clicked", self.close_map)
        map_header.pack_start(back_btn)

        zoom_in_btn = Gtk.Button(icon_name="zoom-in-symbolic")
        zoom_in_btn.add_css_class("flat")
        zoom_in_btn.connect("clicked", lambda _: self._map_widget and self._map_widget.zoom_by(1))
        map_header.pack_end(zoom_in_btn)

        zoom_out_btn = Gtk.Button(icon_name="zoom-out-symbolic")
        zoom_out_btn.add_css_class("flat")
        zoom_out_btn.connect("clicked", lambda _: self._map_widget and self._map_widget.zoom_by(-1))
        map_header.pack_end(zoom_out_btn)

        self.map_title_label = Gtk.Label(label="Kaartweergave")
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
        map_spinner_label = Gtk.Label(label="GPS locaties laden...")
        map_spinner_label.add_css_class("dim-label")
        map_spinner_box.append(self.map_spinner)
        map_spinner_box.append(map_spinner_label)
        self.map_container.add_named(map_spinner_box, "loading")

        self.map_content = Gtk.Box()
        self.map_content.set_vexpand(True)
        self.map_content.set_hexpand(True)
        self.map_container.add_named(self.map_content, "map")
        self.map_container.set_visible_child_name("loading")

        box.append(self.map_container)

        return box

    def open_map(self, btn=None):
        self.header.set_visible(False)
        self.map_btn.set_label("🗺 laden...")
        self.map_btn.set_sensitive(False)
        self.map_container.set_visible_child_name("loading")
        self.map_spinner.start()
        self.main_stack.set_visible_child_name("map")
        threading.Thread(target=self._load_gps_and_show_map, daemon=True).start()

    def _load_gps_and_show_map(self):
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
                markers.append((lat, lon, filename, datum, path))
        GLib.idle_add(self._show_map, markers)

    def _show_map(self, markers):
        if self._map_widget:
            self.map_content.remove(self._map_widget)
            self._map_widget = None

        self._map_widget = MapWidget(markers, self._open_photo_from_map)
        self.map_content.append(self._map_widget)
        self.map_spinner.stop()
        self.map_container.set_visible_child_name("map")
        self.map_title_label.set_text("Kaartweergave")
        self.map_btn.set_label("🗺")
        self.map_btn.set_sensitive(True)
        return False

    def _open_photo_from_map(self, paths):
        if isinstance(paths, str):
            paths = [paths]
        valid = [p for p in paths if p in self.photos]
        if not valid:
            return
        self.close_map()
        if len(valid) == 1:
            index = self.photos.index(valid[0])
            GLib.idle_add(self.open_photo, index)
        else:
            # Cluster: toon alleen de foto's in dit cluster
            self._photos_before_cluster = self.photos
            self.photos = valid
            GLib.idle_add(self.open_photo, 0)

    def close_map(self, btn=None):
        self.header.set_visible(True)
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

        self.edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
        self.edit_btn.add_css_class("osd")
        self.edit_btn.add_css_class("circular")
        self.edit_btn.set_halign(Gtk.Align.END)
        self.edit_btn.set_valign(Gtk.Align.START)
        self.edit_btn.set_margin_top(16)
        self.edit_btn.set_margin_end(120)
        self.edit_btn.set_size_request(40, 40)
        self.edit_btn.set_tooltip_text("Foto bewerken")
        self.edit_btn.connect("clicked", self.on_edit_current)
        viewer_area.add_overlay(self.edit_btn)

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
        rot_left_btn.set_tooltip_text("Draaien links")
        rot_left_btn.connect("clicked", self.on_editor_rotate_left)
        self.editor_bar.append(rot_left_btn)

        rot_right_btn = Gtk.Button(icon_name="object-rotate-right-symbolic")
        rot_right_btn.add_css_class("osd")
        rot_right_btn.add_css_class("circular")
        rot_right_btn.set_size_request(48, 48)
        rot_right_btn.set_tooltip_text("Draaien rechts")
        rot_right_btn.connect("clicked", self.on_editor_rotate_right)
        self.editor_bar.append(rot_right_btn)

        self.crop_toggle_btn = Gtk.ToggleButton(label="✂")
        self.crop_toggle_btn.add_css_class("osd")
        self.crop_toggle_btn.add_css_class("circular")
        self.crop_toggle_btn.set_size_request(48, 48)
        self.crop_toggle_btn.set_tooltip_text("Bijsnijden")
        self.crop_toggle_btn.connect("toggled", self.on_editor_toggle_crop)
        self.editor_bar.append(self.crop_toggle_btn)

        save_btn = Gtk.Button(icon_name="document-save-symbolic")
        save_btn.add_css_class("osd")
        save_btn.add_css_class("circular")
        save_btn.add_css_class("suggested-action")
        save_btn.set_size_request(48, 48)
        save_btn.set_tooltip_text("Opslaan")
        save_btn.connect("clicked", self.on_editor_save)
        self.editor_bar.append(save_btn)

        cancel_editor_btn = Gtk.Button(icon_name="window-close-symbolic")
        cancel_editor_btn.add_css_class("osd")
        cancel_editor_btn.add_css_class("circular")
        cancel_editor_btn.set_size_request(48, 48)
        cancel_editor_btn.set_tooltip_text("Annuleren")
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

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_box.add_css_class("osd")
        title_box.set_halign(Gtk.Align.START)
        title_box.set_valign(Gtk.Align.START)
        title_box.set_margin_top(16)
        title_box.set_margin_start(16)

        self.viewer_title = Gtk.Label(label="")
        self.viewer_title.set_halign(Gtk.Align.START)
        title_box.append(self.viewer_title)

        self.viewer_location = Gtk.Label(label="")
        self.viewer_location.add_css_class("dim-label")
        self.viewer_location.set_halign(Gtk.Align.START)
        title_box.append(self.viewer_location)

        viewer_area.add_overlay(title_box)

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
        key_ctrl.connect("key-pressed", self.on_viewer_key)
        self.add_controller(key_ctrl)

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

        return viewer_area

    # ── Onderste balk ─────────────────────────────────────────────────
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

    # ── Foto's laden ──────────────────────────────────────────────────
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
        GLib.timeout_add(800, self._update_timeline_from_positions)
        return False

    def show_empty_state(self):
        self.spinner.stop()
        self.content_stack.set_visible_child_name("empty")
        self.photo_count_label.set_text("0 foto's")
        self._loading = False

    # ── Thumbnail klik ────────────────────────────────────────────────
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

    # ── Sorteren ──────────────────────────────────────────────────────
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

    # ── Foto viewer ───────────────────────────────────────────────────
    def open_photo(self, index):
        self.current_index = index
        self.header.set_visible(False)
        self.bottom_stack.set_visible(False)
        self.photo_picture.set_pixbuf(None)
        self.viewer_location.set_text("")
        self.main_stack.set_visible_child_name("viewer")
        self._filmstrip_thumbs = {}
        self._update_filmstrip()
        GLib.timeout_add(80, self._scroll_filmstrip_to_current)
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
        location = self._photo_location.get(path)
        if location is None:
            coords = get_gps_coords(path)
            if coords:
                location = reverse_geocode(coords[0], coords[1])
            else:
                location = ""
            self._photo_location[path] = location
        if load_id == self._viewer_load_id:
            GLib.idle_add(self._show_full_photo, pixbuf, path, location)

    def _show_full_photo(self, pixbuf, path, location=""):
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
            self.current_index -= 1
            self._viewer_load_id += 1
            load_id = self._viewer_load_id
            self.viewer_location.set_text("")
            self.filmstrip_area.queue_draw()
            self._scroll_filmstrip_to_current()
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
            self.viewer_location.set_text("")
            self.filmstrip_area.queue_draw()
            self._scroll_filmstrip_to_current()
            threading.Thread(
                target=self._load_full_photo,
                args=(self.photos[self.current_index], load_id),
                daemon=True
            ).start()

    def close_viewer(self, btn=None):
        self._viewer_load_id += 1
        self.photo_picture.set_pixbuf(None)
        self.header.set_visible(True)
        self.bottom_stack.set_visible(True)
        if hasattr(self, '_photos_before_cluster') and self._photos_before_cluster is not None:
            self.photos = self._photos_before_cluster
            self._photos_before_cluster = None
        self.main_stack.set_visible_child_name("grid")

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
        for i, path in enumerate(photos):
            if load_id != self._filmstrip_load_id:
                return
            if i in self._filmstrip_thumbs:
                continue
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    path, FILM_THUMB, FILM_THUMB, True)
            except Exception:
                pb = None
            if load_id != self._filmstrip_load_id:
                return
            self._filmstrip_thumbs[i] = pb
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
        for i in range(n):
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

    def _apply_viewer_transform(self):
        z  = self._viewer_zoom
        ox = self._viewer_offset[0]
        oy = self._viewer_offset[1]
        css = Gtk.CssProvider()
        css.load_from_string(f"""
            picture {{
                transform: scale({z}) translate({ox}px, {oy}px);
                transform-origin: center center;
            }}
        """)
        self.photo_picture.get_style_context().add_provider(
            css, Gtk.STYLE_PROVIDER_PRIORITY_USER
        )
        zoomed = z > 1.0
        self.prev_btn.set_visible(not zoomed)
        self.next_btn.set_visible(not zoomed)
        self.viewer_counter.set_visible(not zoomed)
        self.filmstrip_scroll.set_visible(not zoomed)

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
        if keyval == 65361:
            self.prev_photo()
            return True
        elif keyval == 65363:
            self.next_photo()
            return True
        return False

    # ── Foto editor ───────────────────────────────────────────────────
    def on_edit_current(self, btn):
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
        self._editor_rotation = (self._editor_rotation + 90) % 360
        self._reset_crop()
        self._editor_apply_preview()

    def on_editor_rotate_right(self, btn):
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
                print(f"Editor opslaan mislukt: {e}")
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
            dialog.add_response("ok", "OK")
            dialog.present()

        threading.Thread(target=_do_save, daemon=True).start()

    # ── Verwijderen ───────────────────────────────────────────────────
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

    # ── Instellingen ──────────────────────────────────────────────────
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
