#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — win_map.py
#  Map page
#  by LinuxGinger
# ─────────────────────────────────────────────

import datetime
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pixora_i18n import _, ngettext
from common import (Adw, GLib, Gtk, is_video, log_info, log_warn,
    save_settings, TILE_CACHE_DIR, _MONTH_KEYS)
from metadata import get_gps_coords, get_video_gps_coords, reverse_geocode
from map_widget import MapWidget


class MapMixin:
    """Map page (mixed into MainWindow)."""

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

        # Home button: one click flies to your home. Set/change the address in
        # Settings (or it opens the form the first time if no home exists yet).
        home_btn = Gtk.Button(icon_name="go-home-symbolic")
        home_btn.add_css_class("flat")
        home_btn.set_tooltip_text(_("Go to my home"))
        home_btn.connect("clicked", self._on_home_clicked)

        # Refresh button: rescan photos and reload the map.
        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.add_css_class("flat")
        refresh_btn.set_tooltip_text(_("Refresh map"))
        refresh_btn.connect("clicked", self._on_refresh_map)

        map_header.pack_end(home_btn)
        map_header.pack_end(refresh_btn)

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
        # Live Photos are HEIC+MOV pairs → dedup on (lat, lon, stem).
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
                # 6-decimal (~10cm) matches Live Photo pairs exactly.
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
        # Warm the tile cache around photo locations so the first view is fast.
        try:
            from tile_proxy import start_proxy, deg2tile
            _base, cache = start_proxy(TILE_CACHE_DIR)
            tiles = []
            for (lat, lon, *_rest) in markers:
                for z in (3, 6, 9, 11, 13):
                    tx, ty = deg2tile(lat, lon, z)
                    tiles.append((z, tx, ty))
            cache.prefetch(tiles)
        except Exception as e:
            log_warn(_("Tile prefetch skipped: {err}").format(err=e))
        GLib.idle_add(self._show_map, markers)

    def _show_map(self, markers):
        if self._map_widget:
            self.map_content.remove(self._map_widget)
            self._map_widget = None

        try:
            self.map_spinner_label.set_text(_("Connecting to map server…"))
        except Exception:
            pass

        # Local tile cache (fast re-open, offline fallback); OSM if it won't start.
        tile_url = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        try:
            from tile_proxy import start_proxy
            base, _cache = start_proxy(TILE_CACHE_DIR)
            tile_url = base + "/{z}/{x}/{y}.png"
        except Exception as e:
            log_warn(_("Tile proxy unavailable, using OSM directly: {err}").format(err=e))

        # Initial camera: just frame all the photos — reliable and always shows
        # them. Only center on home when there are no GPS photos at all. Home is
        # a pin + the home button regardless.
        initial_view = None
        hlat = self.settings.get("home_lat")
        hlon = self.settings.get("home_lon")
        if not markers and hlat is not None and hlon is not None:
            initial_view = {"center": [hlat, hlon],
                            "zoom": self.settings.get("home_zoom") or 13}

        home = ([hlat, hlon] if hlat is not None and hlon is not None else None)
        places = self._map_places_list()
        self._map_widget = MapWidget(
            markers, self._open_photo_from_map,
            status_cb=self._on_map_status, tile_url=tile_url,
            initial_view=initial_view, home=home, places=places
        )
        self.map_content.append(self._map_widget)
        # Wait for map-ready; 12s fallback prevents hang.
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
                self._maybe_show_map_intro()
            except Exception:
                pass
        elif status == "offline":
            # Show map anyway; map.html's JS banner explains tile failure.
            if getattr(self, "_map_ready_fallback_id", None):
                try:
                    GLib.source_remove(self._map_ready_fallback_id)
                except Exception:
                    pass
                self._map_ready_fallback_id = None
            try:
                self.map_spinner.stop()
                self.map_container.set_visible_child_name("map")
                self._maybe_show_map_intro()
            except Exception:
                pass
        return False

    def _on_map_ready_timeout(self):
        log_warn(_("Map-ready timeout — showing map anyway"))
        self._map_ready_fallback_id = None
        try:
            self.map_spinner.stop()
            self.map_container.set_visible_child_name("map")
            self._maybe_show_map_intro()
        except Exception:
            pass
        return False

    def _on_refresh_map(self, btn):
        # Full refresh: rescan photos + rebuild the map (reloads tiles too).
        self.map_container.set_visible_child_name("loading")
        try:
            self.map_spinner.start()
        except Exception:
            pass
        threading.Thread(target=self._load_gps_and_show_map, daemon=True).start()

    def _on_home_clicked(self, btn):
        lat = self.settings.get("home_lat")
        lon = self.settings.get("home_lon")
        if lat is None or lon is None:
            # No home yet → open the address form so it's easy to set.
            self._open_home_address_dialog()
            return
        if self._map_widget:
            log_info(_("Map → go home ({lat}, {lon})").format(lat=lat, lon=lon))
            self._map_widget.go_home(lat, lon, self.settings.get("home_zoom"))

    def _maybe_show_map_intro(self):
        """First time the map is shown: offer to add home + places, once."""
        if self.settings.get("map_intro_shown"):
            return
        self.settings["map_intro_shown"] = True
        try:
            save_settings(self.settings)
        except Exception:
            pass
        # Defer so the dialog lands over the now-visible map, not the spinner.
        GLib.timeout_add(400, self._show_map_intro)

    def _show_map_intro(self):
        dlg = Adw.MessageDialog(
            transient_for=self,
            heading=_("Welcome to the map"),
            body=_("This is your first time here. The map shows where your "
                   "photos were taken. Add your home and the places that matter "
                   "to you — they get marked here and you can filter your photos "
                   "by them. That’s it!"))
        dlg.add_response("later", _("Maybe later"))
        dlg.add_response("add", _("Add my home"))
        dlg.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("add")
        dlg.set_close_response("later")
        dlg.connect("response", self._on_map_intro_response)
        dlg.present()
        return False

    def _on_map_intro_response(self, dlg, response):
        if response == "add":
            self._open_place_dialog()  # main home

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
            # Cluster click → filter grid.
            self._photos_before_cluster = self.photos
            self.photos = valid
            self._filmstrip_order_cache = None
            # Date range for the info banner.
            date_range = ""
            try:
                dates = []
                for p in valid:
                    try:
                        mt = os.path.getmtime(p)
                        # Filter pre-2000 mtimes to avoid "1 Jan 1970".
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
                # Not cached → async lookup may resolve later.
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
            self.photo_count_label.set_text(self._media_count_text(valid))
            GLib.idle_add(self.start_load)

    def _fetch_cluster_location(self, sample_path):
        """Async geocode for cluster-filter label."""
        try:
            if is_video(sample_path):
                coords = get_video_gps_coords(sample_path)
            else:
                coords = get_gps_coords(sample_path)
            if not coords:
                return
            loc = reverse_geocode(coords[0], coords[1]).get("text", "")
            if loc:
                self._photo_location[sample_path] = loc
                GLib.idle_add(self._apply_cluster_location, loc)
        except Exception:
            pass

    def _apply_cluster_location(self, loc):
        # Only apply while filter bar visible (user may have ✕'d).
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
        self.photo_count_label.set_text(self._media_count_text())
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
