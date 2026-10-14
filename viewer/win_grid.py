#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — win_grid.py
#  Photo grid: loading, date groups, selection, sort, filters
#  by LinuxGinger
# ─────────────────────────────────────────────

import datetime
import os
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from version import __version__ as APP_VERSION
from pixora_i18n import _, ngettext
import common
from common import (Adw, BATCH_SIZE, drop_live_motion, format_date_header,
    format_duration, Gdk, GdkPixbuf, GLib, Gtk, IMAGE_EXTENSIONS, is_video,
    log_error, log_info, log_warn, save_settings, THUMB_WORKERS)
from metadata import get_photo_date, get_video_duration
from media import get_cache_path, load_thumbnail


class GridMixin:
    """Photo grid: loading, date groups, selection, sort, filters (mixed into MainWindow)."""

    def build_grid_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_vexpand(True)
        outer.set_hexpand(True)

        # Filter-info banner (visible on active cluster filter).
        self.filter_info_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.filter_info_bar.add_css_class("toolbar")
        self.filter_info_bar.set_margin_top(8)
        self.filter_info_bar.set_margin_start(12)
        self.filter_info_bar.set_margin_end(12)
        self.filter_info_bar.set_margin_bottom(4)
        self.filter_info_bar.set_visible(False)

        self._filter_icon = Gtk.Label(label="📍")
        self._filter_icon.set_valign(Gtk.Align.CENTER)
        _filter_icon_css = Gtk.CssProvider()
        _filter_icon_css.load_from_string("label { font-size: 20px; }")
        self._filter_icon.get_style_context().add_provider(
            _filter_icon_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.filter_info_bar.append(self._filter_icon)

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
        _clear_btn.connect("clicked", self._on_clear_filter)
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

    def _media_count_text(self, photos=None):
        """'6 photos & 2 videos' — counts stills vs videos separately."""
        photos = self.photos if photos is None else photos
        vids = sum(1 for p in photos if is_video(p))
        pics = len(photos) - vids
        parts = []
        if pics:
            parts.append(ngettext("%d photo", "%d photos", pics) % pics)
        if vids:
            parts.append(ngettext("%d video", "%d videos", vids) % vids)
        if not parts:
            parts.append(ngettext("%d photo", "%d photos", 0) % 0)
        return " & ".join(parts)

    def build_bottombar(self):
        self.bottom_stack = Gtk.Stack()
        self.bottom_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.bottom_stack.set_transition_duration(150)

        normal_bar = Gtk.ActionBar()
        self.photo_count_label = Gtk.Label(label=ngettext("%d photo", "%d photos", 0) % 0)
        self.photo_count_label.add_css_class("dim-label")
        normal_bar.pack_start(self.photo_count_label)

        # Pixora version on the right of the footer.
        _ver = APP_VERSION
        if _ver:
            ver_lbl = Gtk.Label(label=f"Pixora {_ver}")
            ver_lbl.add_css_class("dim-label")
            ver_lbl.add_css_class("caption")
            normal_bar.pack_end(ver_lbl)

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
            # Drop .flat so .suggested-action can paint its colored bg.
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
                # Wrap picture in overlay to host check_box.
                picture = btn.get_child()
                btn.set_child(None)
                overlay = Gtk.Overlay()
                overlay.set_size_request(common.THUMB_SIZE, common.THUMB_SIZE)
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
        # os.walk on 2000+ files blocks splash spinner — push to thread.
        self.content_stack.set_visible_child_name("loading")
        self.spinner.start()
        self.spinner_label.set_text(_("Sorting photos…"))

        def _scan_bg():
            photos = []
            for root, dirs, files in os.walk(photo_path):
                for file in files:
                    if os.path.splitext(file)[1].lower() in IMAGE_EXTENSIONS:
                        photos.append(os.path.join(root, file))
            photos = drop_live_motion(photos)
            GLib.idle_add(self._on_photos_scanned, photos, photo_path)

        threading.Thread(target=_scan_bg, daemon=True).start()
        return False

    def _on_photos_scanned(self, photos, photo_path):
        log_info(_("load_photos: {n} files found").format(n=len(photos)))
        filtering = self._favorites_only or bool(self._place_filter)
        if filtering:
            # Union: each ticked filter ADDS its photos (favorites and/or each
            # selected place), so ticking more shows more.
            def _keep(p):
                if self._favorites_only and p in self._favorites:
                    return True
                if self._place_filter and self._photo_place(p) in self._place_filter:
                    return True
                return False
            photos = [p for p in photos if _keep(p)]
        if not photos:
            if filtering:
                self._show_empty_favorites()
            else:
                self.show_empty_state()
            self.start_watcher(photo_path)
            return False
        self.photos = photos
        self._filmstrip_order_cache = None
        # Filters are shown by the banner now, so the footer is just counts.
        self.photo_count_label.set_text(self._media_count_text())
        if filtering:
            self.filter_subtitle_lbl.set_text(self._media_count_text())
        self.start_watcher(photo_path)
        # Read the combo here (main thread) — GTK isn't thread-safe.
        sort_idx = (self.sort_combo.get_selected()
                    if hasattr(self, "sort_combo") else 0)
        threading.Thread(target=self._sort_then_load, args=(sort_idx,),
                         daemon=True).start()
        return False

    def _sort_then_load(self, sort_idx):
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
        # Must use same date source as filmstrip — mismatch would desync indices.
        for i, path in enumerate(photos):
            try:
                ts = get_photo_date(path)
                if ts <= 0 or ts < 946684800:  # pre-2000 = bogus
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

        has_places = (self.settings.get("home_lat") is not None
                      or bool(self.settings.get("extra_places")))
        def fetch(idx):
            path = photos[idx]
            pb = load_thumbnail(path)
            if pb is not None:
                w, h = pb.get_width(), pb.get_height()
            else:
                w, h = common.THUMB_SIZE, common.THUMB_SIZE
            pb = None
            cache_path = get_cache_path(path, common.THUMB_SIZE)
            dur = get_video_duration(path) if is_video(path) else 0.0
            place_kind = self._photo_place_kind(path) if has_places else None
            return (idx, path, cache_path, w, h, dur, place_kind)

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
        # margin + scrollbar reserve
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
        # Insertion-order = grid top-to-bottom; bail after visible range.
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
            h = btn.get_height() or common.THUMB_SIZE
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
        # Shared CSS providers: created once, reused per thumb.
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
        for index, path, cache_path, pb_w, pb_h, duration, place_kind in batch:
            if pb_h > 0:
                width_at_thumb = max(1, int(pb_w * common.THUMB_SIZE / pb_h))
            else:
                width_at_thumb = common.THUMB_SIZE
            picture = Gtk.Picture()
            picture.set_size_request(width_at_thumb, common.THUMB_SIZE)
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            picture.set_hexpand(False)
            picture.set_vexpand(False)

            overlay = Gtk.Overlay()
            overlay.set_size_request(width_at_thumb, common.THUMB_SIZE)
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

            # Home badge: top-right (star is top-left). Orange circle = main
            # home, blue circle = another saved place (matches the map pins).
            if 'home_box_main' not in tc:
                for key, colour in (("home_box_main", "#e95420"),
                                    ("home_box_extra", "#4C7BE1")):
                    p = Gtk.CssProvider()
                    p.load_from_string(
                        "box { background-color: %s; border-radius: 50%%;"
                        " min-width: 26px; min-height: 26px; }" % colour)
                    tc[key] = p
            home_badge = Gtk.Box()
            home_badge.set_halign(Gtk.Align.END)
            home_badge.set_valign(Gtk.Align.START)
            home_badge.set_margin_end(6)
            home_badge.set_margin_top(6)
            home_badge.set_can_target(False)
            home_badge.set_can_focus(False)
            home_badge.get_style_context().add_provider(
                tc['home_box_extra' if place_kind == "extra" else 'home_box_main'],
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            home_label = Gtk.Label()
            home_label.set_halign(Gtk.Align.CENTER)
            home_label.set_valign(Gtk.Align.CENTER)
            home_label.set_hexpand(True)
            home_label.set_vexpand(True)
            home_label.set_use_markup(True)
            home_label.set_markup('<span size="small">🏠</span>')
            home_badge.append(home_label)
            home_badge.set_visible(place_kind is not None)
            overlay.add_overlay(home_badge)

            btn = Gtk.Button()
            btn.set_child(overlay)
            btn.set_overflow(Gtk.Overflow.HIDDEN)
            btn.set_size_request(width_at_thumb, common.THUMB_SIZE)
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
            self.thumb_widgets[index] = (btn, None)  # check_box lazy
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
            cluster_lbl if cluster_lbl else self._media_count_text())
        self._loading = False
        GLib.timeout_add(120, self._run_viewport_hydrate)
        # First run after setup: show the welcome tour once, 2s after the grid
        # is up (never over the loading spinner).
        if (not self.settings.get("welcome_shown")
                and not getattr(self, "_welcome_scheduled", False)):
            self._welcome_scheduled = True
            GLib.timeout_add(2000, self._show_welcome)
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
        # Show loading state so the grid doesn't look frozen during sort.
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

    def _populate_filter_menu(self):
        """(Re)build the filter popover: favorites + one row per saved place.
        Favorites + main home tick orange; extra places tick blue (matching the
        badges/pins)."""
        if not getattr(self, "_filter_check_css", None):
            self._filter_check_css = Gtk.CssProvider()
            self._filter_check_css.load_from_string(
                ".pixora-check-orange check:checked {"
                " background-color: #e95420; border-color: #e95420; }"
                ".pixora-check-blue check:checked {"
                " background-color: #4C7BE1; border-color: #4C7BE1; }")
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), self._filter_check_css,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        box = self._filter_box
        child = box.get_first_child()
        while child:
            box.remove(child)
            child = box.get_first_child()
        self._fav_check = Gtk.CheckButton(label=_("Only favorites"))
        self._fav_check.add_css_class("pixora-check-orange")
        self._fav_check.set_active(self._favorites_only)
        self._fav_check.connect("toggled", self._on_filter_changed)
        box.append(self._fav_check)
        self._place_checks = []
        places = self._all_places()
        home_set = self.settings.get("home_lat") is not None
        if places:
            box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            for i, (name, _lat, _lon) in enumerate(places):
                is_main = home_set and i == 0   # main home is always first
                c = Gtk.CheckButton(label=name)
                c.add_css_class("pixora-check-orange" if is_main
                                else "pixora-check-blue")
                c.set_active(name in self._place_filter)
                c.connect("toggled", self._on_filter_changed)
                box.append(c)
                self._place_checks.append((name, c))

    def _on_filter_changed(self, _btn=None):
        self._favorites_only = getattr(self, "_fav_check", None) is not None \
            and self._fav_check.get_active()
        self._place_filter = {name for name, c in getattr(self, "_place_checks", [])
                              if c.get_active()}
        self._apply_filter_banner()
        self.load_photos()

    def _apply_filter_banner(self):
        """Inline banner (same look as the location filter) for active filters."""
        parts = []
        if self._favorites_only:
            parts.append(_("Favorites"))
        parts.extend(sorted(self._place_filter))
        if parts:
            self._filter_icon.set_text("⭐" if self._favorites_only else "🏠")
            self.filter_title_lbl.set_text(" · ".join(parts))
            self.filter_subtitle_lbl.set_text("")
            self.filter_info_bar.set_visible(True)
        elif not getattr(self, "_photos_before_cluster", None):
            self.filter_info_bar.set_visible(False)
            self._filter_icon.set_text("📍")

    def _clear_all_filters(self):
        self._favorites_only = False
        self._place_filter = set()
        self._apply_filter_banner()
        self.load_photos()

    def _on_clear_filter(self, btn=None):
        # One ✕ for every filter kind.
        if self._favorites_only or self._place_filter:
            self._clear_all_filters()
        else:
            self.on_clear_cluster_filter()

    def _on_show_all_favorites(self, _banner):
        self._clear_all_filters()
