#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — win_viewer.py
#  Full-screen viewer: photos, filmstrip, zoom, favourites
#  by LinuxGinger
# ─────────────────────────────────────────────

import datetime
import json
import math
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pixora_i18n import _
from common import (FILM_THUMB, format_duration, format_viewer_date, Gdk,
    GdkPixbuf, GLib, Gtk, human_size, is_video, log_info, _PLACE_TOL_LAT,
    _PLACE_TOL_LON)
from metadata import (cached_geocode, get_gps_coords, get_photo_date,
    get_video_gps_coords, reverse_geocode)
from media import decode_pixbuf, load_thumbnail


class ViewerMixin:
    """Full-screen viewer: photos, filmstrip, zoom, favourites (mixed into MainWindow)."""

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

        # Two Pictures in a Stack so next/prev can slide. photo_picture
        # aliases the visible slot; _show_full_photo swaps the pointer.
        self._viewer_pic_a = Gtk.Picture()
        self._viewer_pic_a.set_content_fit(Gtk.ContentFit.CONTAIN)
        self._viewer_pic_a.set_vexpand(True)
        self._viewer_pic_a.set_hexpand(True)

        self._viewer_pic_b = Gtk.Picture()
        self._viewer_pic_b.set_content_fit(Gtk.ContentFit.CONTAIN)
        self._viewer_pic_b.set_vexpand(True)
        self._viewer_pic_b.set_hexpand(True)

        self._viewer_stack = Gtk.Stack()
        self._viewer_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._viewer_stack.set_transition_duration(250)
        self._viewer_stack.set_vexpand(True)
        self._viewer_stack.set_hexpand(True)
        self._viewer_stack.add_named(self._viewer_pic_a, "a")
        self._viewer_stack.add_named(self._viewer_pic_b, "b")
        self._viewer_stack.set_visible_child_name("a")

        self.photo_picture = self._viewer_pic_a
        self._active_viewer_slot = "a"
        self._nav_direction = None  # "next" / "prev" / None (first-open)
        viewer_area.add_overlay(self._viewer_stack)

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

        # Info button — directly below the close button (top-right), clear of the
        # title at top-left. Small popover opens beneath it with media details.
        self.info_btn = Gtk.Button(icon_name="dialog-information-symbolic")
        self.info_btn.add_css_class("osd")
        self.info_btn.add_css_class("circular")
        self.info_btn.set_halign(Gtk.Align.END)
        self.info_btn.set_valign(Gtk.Align.START)
        self.info_btn.set_margin_top(64)
        self.info_btn.set_margin_end(16)
        self.info_btn.set_size_request(40, 40)
        self.info_btn.set_tooltip_text(_("Photo & video details"))
        self.info_btn.connect("clicked", self.on_info_clicked)
        viewer_area.add_overlay(self.info_btn)

        self.info_popover = Gtk.Popover()
        self.info_popover.set_parent(self.info_btn)
        self.info_popover.set_position(Gtk.PositionType.BOTTOM)
        self._info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._info_box.set_margin_top(10)
        self._info_box.set_margin_bottom(10)
        self._info_box.set_margin_start(12)
        self._info_box.set_margin_end(12)
        self.info_popover.set_child(self._info_box)
        self.info_popover.connect("closed", lambda p: self._reset_fade_timer())

        # Viewer-overlay donut mirrors header donut; shares state.
        self._viewer_donut = Gtk.DrawingArea()
        self._viewer_donut.set_size_request(24, 24)
        self._viewer_donut.set_draw_func(self._draw_backup_donut)
        self._viewer_donut_btn = Gtk.Button()
        self._viewer_donut_btn.add_css_class("osd")
        self._viewer_donut_btn.add_css_class("circular")
        self._viewer_donut_btn.set_child(self._viewer_donut)
        self._viewer_donut_btn.set_halign(Gtk.Align.END)
        self._viewer_donut_btn.set_valign(Gtk.Align.START)
        # Below the delete button (margin_end=68, top=16+40+8).
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

        # Editor toolbar (hidden until editor mode opens).
        # One cohesive rounded toolbar (Pixora style) instead of loose OSD
        # circles, with consistent symbolic icons and an accent Save pill.
        self.editor_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.editor_bar.set_halign(Gtk.Align.CENTER)
        self.editor_bar.set_valign(Gtk.Align.END)
        self.editor_bar.set_margin_bottom(FILM_THUMB + 12 + 16)
        self.editor_bar.set_visible(False)
        _editbar_css = Gtk.CssProvider()
        _editbar_css.load_from_string(
            ".pixora-editbar { background-color: rgba(28,28,30,0.94);"
            " border-radius: 16px; padding: 6px 8px;"
            " box-shadow: 0 2px 12px rgba(0,0,0,0.45); }"
            # Flat buttons on the dark bar need light icons (else they look grey).
            ".pixora-editbar button { color: #ffffff; }")
        self.editor_bar.add_css_class("pixora-editbar")
        # Display-level: a per-widget provider on the box would NOT reach the
        # child buttons, so the icons stayed grey.
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), _editbar_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        def _edit_btn(icon, tip, cb, toggle=False):
            b = Gtk.ToggleButton() if toggle else Gtk.Button()
            b.set_icon_name(icon)
            b.add_css_class("flat")
            b.add_css_class("circular")
            b.set_size_request(42, 42)
            b.set_tooltip_text(tip)
            b.connect("toggled" if toggle else "clicked", cb)
            return b

        rot_left_btn = _edit_btn("object-rotate-left-symbolic",
                                 _("Rotate left"), self.on_editor_rotate_left)
        rot_right_btn = _edit_btn("object-rotate-right-symbolic",
                                  _("Rotate right"), self.on_editor_rotate_right)
        self.crop_toggle_btn = _edit_btn("edit-cut-symbolic", _("Crop"),
                                         self.on_editor_toggle_crop, toggle=True)
        self.restore_orig_btn = _edit_btn("edit-undo-symbolic",
                                          _("Restore original"),
                                          self.on_editor_restore)
        self.filter_toggle_btn = _edit_btn("color-select-symbolic", _("Filters"),
                                           self.on_editor_toggle_filters, toggle=True)
        self.editor_bar.append(rot_left_btn)
        self.editor_bar.append(rot_right_btn)
        self.editor_bar.append(self.crop_toggle_btn)
        self.editor_bar.append(self.filter_toggle_btn)
        self.editor_bar.append(self.restore_orig_btn)

        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.set_margin_start(4)
        sep.set_margin_end(4)
        sep.set_margin_top(6)
        sep.set_margin_bottom(6)
        self.editor_bar.append(sep)

        cancel_editor_btn = Gtk.Button(label=_("Cancel"))
        cancel_editor_btn.add_css_class("pill")
        cancel_editor_btn.set_valign(Gtk.Align.CENTER)
        cancel_editor_btn.connect("clicked", self.on_editor_cancel)
        self.editor_bar.append(cancel_editor_btn)

        save_btn = Gtk.Button(label=_("Save"))
        save_btn.add_css_class("suggested-action")
        save_btn.add_css_class("pill")
        save_btn.set_valign(Gtk.Align.CENTER)
        save_btn.connect("clicked", self.on_editor_save)
        self.editor_bar.append(save_btn)

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
        # Editor toolbar must sit ABOVE the crop overlay, else the overlay eats
        # the button clicks (couldn't exit crop / cancel did nothing).
        viewer_area.add_overlay(self.editor_bar)

        # Filter strip — presets + intensity, shown above the editor bar when the
        # Filters button is on. Live preview; applied full-res on Save.
        self.filter_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.filter_bar.set_halign(Gtk.Align.CENTER)
        self.filter_bar.set_valign(Gtk.Align.END)
        self.filter_bar.set_margin_bottom(FILM_THUMB + 12 + 16 + 58)
        self.filter_bar.add_css_class("pixora-editbar")
        self.filter_bar.set_visible(False)
        self._filter_preset_btns = []
        presets = [
            ("",      _("Original")),
            ("bw",    _("B&W")),
            ("sepia", _("Sepia")),
            ("warm",  _("Warm")),
            ("cool",  _("Cool")),
            ("vivid", _("Vivid")),
        ]
        group_btn = None
        for name, label in presets:
            b = Gtk.ToggleButton(label=label)
            b.add_css_class("flat")
            b.set_valign(Gtk.Align.CENTER)
            if group_btn is None:
                group_btn = b
                b.set_active(True)
            else:
                b.set_group(group_btn)
            b.connect("toggled", self._on_filter_preset, name)
            self.filter_bar.append(b)
            self._filter_preset_btns.append((name, b))
        viewer_area.add_overlay(self.filter_bar)

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
        self.video_controls.add_css_class("video-controls")
        _vc_css = Gtk.CssProvider()
        _vc_css.load_from_string(
            "box.video-controls { border-radius: 14px; padding: 4px 8px; }")
        self.video_controls.get_style_context().add_provider(
            _vc_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
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
            "scale.video-scrubber trough { border-radius: 9px; min-height: 6px; }"
            " scale.video-scrubber trough highlight { border-radius: 9px; }"
            " scale.video-scrubber trough fill { border-radius: 9px; }"
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
        # Spinner shown over the thumb area while ffmpeg extracts the frame.
        prev_overlay = Gtk.Overlay()
        prev_overlay.set_size_request(160, 90)
        prev_overlay.set_child(self._preview_picture)
        self._preview_spinner = Gtk.Spinner()
        self._preview_spinner.set_halign(Gtk.Align.CENTER)
        self._preview_spinner.set_valign(Gtk.Align.CENTER)
        self._preview_spinner.set_visible(False)
        prev_overlay.add_overlay(self._preview_spinner)
        self._preview_time_lbl = Gtk.Label()
        self._preview_time_lbl.add_css_class("caption")
        prev_box.append(prev_overlay)
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
        # halign=START: narrow filmstrip stays left-pinned.
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

        # Shown while a full-res photo is still decoding (cache miss), so the
        # user sees a loader instead of a black gap or a blurry thumbnail.
        self._photo_spinner = Gtk.Spinner()
        self._photo_spinner.set_size_request(56, 56)
        self._photo_spinner.set_halign(Gtk.Align.CENTER)
        self._photo_spinner.set_valign(Gtk.Align.CENTER)
        self._photo_spinner.set_visible(False)
        self._photo_spinner.set_can_target(False)
        viewer_area.add_overlay(self._photo_spinner)

        return viewer_area

    def _all_places(self):
        """[(name, lat, lon), …] — main home first (priority), then extras."""
        places = []
        hlat = self.settings.get("home_lat")
        hlon = self.settings.get("home_lon")
        if hlat is not None and hlon is not None:
            places.append((_("Home"), hlat, hlon))
        for p in self.settings.get("extra_places", []):
            if p.get("lat") is not None and p.get("lon") is not None:
                places.append((p.get("name") or _("Place"), p["lat"], p["lon"]))
        return places

    def _place_for_coords(self, coords):
        """Name of the first place within ~120 m of coords (main home wins)."""
        if not coords:
            return None
        # Ignore null-island coords on either side so a stray (0,0) photo or a
        # mis-saved place never matches everything.
        if abs(coords[0]) < 0.0001 and abs(coords[1]) < 0.0001:
            return None
        for name, lat, lon in self._all_places():
            if abs(lat) < 0.0001 and abs(lon) < 0.0001:
                continue
            if (abs(coords[0] - lat) < _PLACE_TOL_LAT
                    and abs(coords[1] - lon) < _PLACE_TOL_LON):
                return name
        # Street-name fallback for GPS drift (uses cached geocode only).
        geo = cached_geocode(coords[0], coords[1])
        _kind, name = self._match_place_kind_by_address(
            geo.get("street", ""), geo.get("city", ""))
        return name

    def _photo_place(self, path):
        """Place name for the photo's GPS, or None."""
        try:
            coords = (get_video_gps_coords(path) if is_video(path)
                      else get_gps_coords(path))
        except Exception:
            coords = None
        return self._place_for_coords(coords)

    @staticmethod
    def _addr_line(street, city, country):
        """'Street, City, Country' from stored parts, skipping blanks. No house no."""
        return ", ".join(x for x in (street, city, country) if x)

    def _place_location_text(self, coords):
        """The saved address of the place the coords fall in (home first), so a
        place photo shows a real street/city even offline. '' when none match."""
        if not coords:
            return ""
        if abs(coords[0]) < 0.0001 and abs(coords[1]) < 0.0001:
            return ""
        hlat = self.settings.get("home_lat")
        hlon = self.settings.get("home_lon")
        if (hlat is not None and hlon is not None
                and abs(coords[0] - hlat) < _PLACE_TOL_LAT
                and abs(coords[1] - hlon) < _PLACE_TOL_LON):
            return self._addr_line(self.settings.get("home_street", ""),
                                   self.settings.get("home_city", ""),
                                   self.settings.get("home_country", ""))
        for p in self.settings.get("extra_places", []):
            if (p.get("lat") is not None and p.get("lon") is not None
                    and abs(coords[0] - p["lat"]) < _PLACE_TOL_LAT
                    and abs(coords[1] - p["lon"]) < _PLACE_TOL_LON):
                return self._addr_line(p.get("street", ""), p.get("city", ""),
                                       p.get("country", ""))
        return ""

    def _apply_initial_location(self, location, searching, is_video=False):
        """Set the viewer location label when a photo/video opens (pre-geocode).
        A cached address shows instantly; an unlooked-up one shows the spinner;
        place photos fall back to their stored address; GPS-less photos say so."""
        if location:
            self._set_viewer_location("done", self._decorate_location(f"📍 {location}"))
        elif searching:
            # Lookup pending → show the spinner, not a half address that then
            # gets replaced (which made the street pop in and out on fast nav).
            self._set_viewer_location("searching")
        elif getattr(self, "_viewer_place", None):
            addr = getattr(self, "_viewer_place_addr", "") or ""
            base = f"📍 {addr}" if addr else ""
            self._set_viewer_location("done", self._decorate_location(base))
        else:
            none_msg = (_("No location for this video") if is_video
                        else _("No location for this photo"))
            self._set_viewer_location("done", f"📍 {none_msg}")

    def _photo_place_kind(self, path):
        """'main' (taken at the main home), 'extra' (another saved place), or None."""
        try:
            coords = (get_video_gps_coords(path) if is_video(path)
                      else get_gps_coords(path))
        except Exception:
            coords = None
        if not coords:
            return None
        hlat = self.settings.get("home_lat")
        hlon = self.settings.get("home_lon")
        if (hlat is not None and hlon is not None
                and abs(coords[0] - hlat) < _PLACE_TOL_LAT
                and abs(coords[1] - hlon) < _PLACE_TOL_LON):
            return "main"
        for p in self.settings.get("extra_places", []):
            if (p.get("lat") is not None and p.get("lon") is not None
                    and abs(coords[0] - p["lat"]) < _PLACE_TOL_LAT
                    and abs(coords[1] - p["lon"]) < _PLACE_TOL_LON):
                return "extra"
        # GPS drift can put an at-home photo outside the radius — fall back to a
        # street-name match using the already-cached geocode (no network here).
        geo = cached_geocode(coords[0], coords[1])
        kind, _name = self._match_place_kind_by_address(
            geo.get("street", ""), geo.get("city", ""))
        return kind

    def _match_place_kind_by_address(self, street, city):
        """('main'|'extra', name) when street (and city, if known) matches a saved
        place's street, else (None, None). This catches photos taken on the home
        street that GPS drift pushed outside the radius."""
        if not street:
            return None, None
        s = street.strip().lower()
        c = (city or "").strip().lower()
        hs = (self.settings.get("home_street") or "").strip().lower()
        hc = (self.settings.get("home_city") or "").strip().lower()
        if hs and s == hs and (not hc or not c or c == hc):
            return "main", _("Home")
        for p in self.settings.get("extra_places", []):
            ps = (p.get("street") or "").strip().lower()
            pc = (p.get("city") or "").strip().lower()
            if ps and s == ps and (not pc or not c or c == pc):
                return "extra", (p.get("name") or _("Place"))
        return None, None

    def open_photo(self, index):
        if not self.photos or not (0 <= index < len(self.photos)):
            return
        path = self.photos[index]
        kind = _("video") if is_video(path) else _("photo")
        log_info(_("open_photo: {kind} idx={i} path={p}").format(kind=kind, i=index, p=path))
        self.current_index = index
        # First-open: no nav direction → crossfade.
        self._nav_direction = None
        self.header.set_visible(False)
        self.bottom_stack.set_visible(False)
        self._set_toolbars_revealed(False)
        self._stop_video()
        self.photo_picture.set_pixbuf(None)
        if not is_video(path) and hasattr(self, "_photo_spinner"):
            self._photo_spinner.set_visible(True)
            self._photo_spinner.start()
        self._set_viewer_location("empty")
        self.main_stack.set_visible_child_name("viewer")
        # Bump the load id together with clearing the cache: any in-flight
        # background loader bails before it can write a stale thumb (mapped to
        # the old view order) into the fresh dict — that desync showed a
        # neighbour's frame on the wrong filmstrip cell.
        self._filmstrip_load_id += 1
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
        """Return (label, coords_for_lookup). coords is non-None only when an
        online lookup is still needed (drives the spinner); a label that's already
        cached comes back instantly so it never has to pop in via a spinner."""
        cached = self._photo_location.get(path)
        if cached:
            return cached, None
        if is_video(path):
            coords = get_video_gps_coords(path)
        else:
            coords = get_gps_coords(path)
        if coords:
            geo = cached_geocode(coords[0], coords[1])
            if geo.get("text"):
                self._photo_location[path] = geo["text"]
                return geo["text"], None  # already looked up once — show instantly
            return "", coords             # needs a lookup → spinner + async geocode
        try:
            if cached is None and time.time() - os.path.getmtime(path) > 30:
                self._photo_location[path] = ""
        except Exception:
            pass
        return "", None

    def _set_viewer_location(self, state, text=""):
        """state: empty|searching|done."""
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
        """Async geocode; sets the place tag by street match, then caches the text."""
        place_addr = getattr(self, "_viewer_place_addr", "") or ""
        had_place = bool(getattr(self, "_viewer_place", None))

        def _bg():
            geo = reverse_geocode(coords[0], coords[1])
            text = geo.get("text", "")
            # Street-name match catches a saved place that GPS drift put off-radius.
            place = None
            if not had_place:
                _kind, place = self._match_place_kind_by_address(
                    geo.get("street", ""), geo.get("city", ""))
            if text:
                resolved = f"📍 {text}"
                self._photo_location[path] = text
            elif place_addr:
                resolved = f"📍 {place_addr}"  # offline fallback for a saved place
            else:
                resolved = f"📍 {coords[0]:.4f}, {coords[1]:.4f}"
                # Don't cache — retry when online.
            if load_id == self._viewer_load_id:
                GLib.idle_add(self._update_viewer_location, resolved, load_id, place)
        threading.Thread(target=_bg, daemon=True).start()

    def _decorate_location(self, text):
        """Append a '(🏠 <place>)' tag when the open photo was taken at a saved
        place; show the tag alone if there's no place name yet."""
        place = getattr(self, "_viewer_place", None)
        if place:
            tag = f"🏠 {place}"
            return f"{text}  ({tag})" if text else tag
        return text

    def on_info_clicked(self, btn):
        path = self._current_photo_path()
        if not path:
            return
        # Keep the chrome visible while the info popover is open.
        self._cancel_fade()
        # Read metadata off the main thread (ffprobe/EXIF can be slow), then fill.
        self._fill_info_box([(_("Loading…"), "")])
        self.info_popover.popup()

        def _bg(p=path):
            rows = self._gather_media_info(p)
            GLib.idle_add(self._fill_info_box, rows, p)
        threading.Thread(target=_bg, daemon=True).start()

    def _fill_info_box(self, rows, for_path=None):
        # Drop if the user already moved to another photo.
        if for_path is not None and self._current_photo_path() != for_path:
            return False
        child = self._info_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._info_box.remove(child)
            child = nxt
        for key, value in rows:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
            k = Gtk.Label(label=key)
            k.add_css_class("dim-label")
            k.set_xalign(0)
            k.set_halign(Gtk.Align.START)
            v = Gtk.Label(label=str(value))
            v.set_xalign(1)
            v.set_halign(Gtk.Align.END)
            v.set_hexpand(True)
            v.set_selectable(True)
            v.set_wrap(True)
            v.set_max_width_chars(34)
            row.append(k)
            row.append(v)
            self._info_box.append(row)
        return False

    def _gather_media_info(self, path):
        """[(label, value), …] of media details — runs in a background thread."""
        rows = []
        is_vid = is_video(path)
        try:
            rows.append((_("File size"), human_size(os.path.getsize(path))))
        except Exception:
            pass
        ext = os.path.splitext(path)[1].lstrip(".").upper()
        if ext:
            rows.append((_("Type"), ext))
        try:
            ts = get_photo_date(path)
            dt = datetime.datetime.fromtimestamp(ts)
            # format_viewer_date already includes the time — don't append it again.
            rows.append((_("Taken"), format_viewer_date(dt)))
        except Exception:
            pass
        if is_vid:
            try:
                r = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-print_format", "json",
                     "-show_format", "-show_streams", path],
                    capture_output=True, text=True, timeout=8)
                d = json.loads(r.stdout)
                vs = next((s for s in d.get("streams", [])
                           if s.get("codec_type") == "video"), {})
                w, h = vs.get("width"), vs.get("height")
                if w and h:
                    rows.append((_("Resolution"), f"{w} × {h}"))
                    rows.append((_("Megapixels"), f"{(w * h) / 1e6:.1f} MP"))
                dur = float(d.get("format", {}).get("duration", 0) or 0)
                if dur:
                    rows.append((_("Duration"), format_duration(dur)))
                fr = vs.get("r_frame_rate", "")
                if "/" in fr:
                    a, b = fr.split("/")
                    if float(b):
                        rows.append((_("Frame rate"), f"{float(a) / float(b):.0f} fps"))
                if vs.get("codec_name"):
                    rows.append((_("Codec"), vs["codec_name"].upper()))
            except Exception:
                pass
        else:
            try:
                from PIL import Image
                with Image.open(path) as img:
                    w, h = img.size
                    exif = img.getexif()
                rows.append((_("Resolution"), f"{w} × {h}"))
                rows.append((_("Megapixels"), f"{(w * h) / 1e6:.1f} MP"))
                make = " ".join(str(exif.get(0x010F) or "").split())
                model = " ".join(str(exif.get(0x0110) or "").split())
                cam = " ".join(x for x in (make, model) if x)
                if cam:
                    rows.append((_("Camera"), cam))
                try:
                    ex = exif.get_ifd(0x8769)
                except Exception:
                    ex = {}
                iso = ex.get(0x8827)
                if isinstance(iso, (tuple, list)):
                    iso = iso[0] if iso else None
                if iso:
                    try:
                        rows.append((_("ISO"), str(int(iso))))
                    except Exception:
                        pass
                fn = ex.get(0x829D)
                if fn:
                    rows.append((_("Aperture"), f"f/{float(fn):.1f}"))
                et = ex.get(0x829A)
                if et:
                    etf = float(et)
                    if 0 < etf < 1:
                        rows.append((_("Shutter"), f"1/{round(1 / etf)} s"))
                    elif etf:
                        rows.append((_("Shutter"), f"{etf:.0f} s"))
                fl = ex.get(0x920A)
                if fl:
                    rows.append((_("Focal length"), f"{float(fl):.0f} mm"))
            except Exception:
                pass
        try:
            coords = (get_video_gps_coords(path) if is_vid else get_gps_coords(path))
            if coords:
                geo = cached_geocode(coords[0], coords[1])
                loc = (geo.get("text") if geo else "") or f"{coords[0]:.4f}, {coords[1]:.4f}"
                place = self._place_for_coords(coords)
                if place:
                    loc = f"{loc} (🏠 {place})"
                rows.append((_("Location"), loc))
        except Exception:
            pass
        rows.append((_("Filename"), os.path.basename(path)))
        return rows

    def _load_full_photo(self, path, load_id):
        if is_video(path):
            initial, coords = self._determine_initial_location(path)
            # Read GPS here (bg thread) — ffprobe on the main thread froze the UI.
            self._set_viewer_place(get_video_gps_coords(path))
            searching = bool(coords)
            if load_id == self._viewer_load_id:
                GLib.idle_add(self._show_video, path, initial, searching)
            if coords:
                self._start_geocode_upgrade(path, coords, load_id)
            return
        with self._pixbuf_cache_lock:
            pixbuf = self._viewer_pixbuf_cache.get(path)
            if pixbuf is not None:
                self._viewer_pixbuf_cache.move_to_end(path)
        if pixbuf is None:
            try:
                pixbuf = decode_pixbuf(path, 2560)
            except Exception:
                pixbuf = None
            if pixbuf is not None:
                with self._pixbuf_cache_lock:
                    self._viewer_pixbuf_cache[path] = pixbuf
                    while len(self._viewer_pixbuf_cache) > 3:
                        self._viewer_pixbuf_cache.popitem(last=False)
        initial, coords = self._determine_initial_location(path)
        self._set_viewer_place(coords if coords else get_gps_coords(path))
        searching = bool(coords)
        if load_id == self._viewer_load_id:
            GLib.idle_add(self._show_full_photo, pixbuf, path, initial, searching)
            GLib.idle_add(self._preload_adjacent_photos)
        if coords:
            self._start_geocode_upgrade(path, coords, load_id)

    def _update_viewer_location(self, text, load_id, place=None):
        # Guard against stale callbacks after nav.
        if load_id != self._viewer_load_id:
            return False
        if place and not getattr(self, "_viewer_place", None):
            self._viewer_place = place
        self._set_viewer_location("done", self._decorate_location(text))
        return False

    def _set_viewer_place(self, coords):
        """Set the place tag + stored address from already-read coords (pure, no
        I/O) so it's safe on the main thread — never reads GPS here."""
        self._viewer_place = self._place_for_coords(coords)
        self._viewer_place_addr = self._place_location_text(coords)

    def _show_full_photo(self, pixbuf, path, location="", searching=False):
        self._hide_photo_spinner()  # load finished (or failed): stop the loader
        # Clear any video immediately — even if the photo is queued below, the
        # video must never linger on top of an incoming photo.
        if self.video_display.get_visible():
            self._stop_video()
            self.video_display.set_visible(False)
            self.video_controls.set_visible(False)
            self._viewer_stack.set_visible(True)
        # Queue while a previous slide is still running — starting a new
        # transition mid-flight makes GtkStack snap to the old target first,
        # causing a visible jump. On completion the latest stashed target fires.
        try:
            running = self._viewer_stack.get_transition_running()
        except Exception:
            running = False
        if running:
            if path == getattr(self, "_viewer_current_path", None):
                # Slide is already ending on this photo; no need to re-slide.
                self._viewer_pending_show = None
            else:
                self._viewer_pending_show = (pixbuf, path, location, searching)
            self._ensure_viewer_transition_listener()
            return False

        # Same photo already on screen (upgrading a thumbnail placeholder to
        # full-res): swap the pixbuf in place — no second slide, no flash.
        if (pixbuf is not None
                and path == getattr(self, "_viewer_current_path", None)
                and self.photo_picture.get_paintable() is not None):
            self._viewer_pixbuf = pixbuf
            self._viewer_zoom = 1.0
            self._viewer_offset = [0.0, 0.0]
            self.photo_picture.set_pixbuf(pixbuf)
            self._apply_initial_location(location, searching)
            return False

        self._stop_video()
        self._show_viewer_ui()   # reset fade state
        self.video_display.set_visible(False)
        self.video_controls.set_visible(False)
        self._viewer_stack.set_visible(True)  # may have been hidden for a video
        # Put new pixbuf in inactive slot, then flip stack for slide.
        incoming_slot = "b" if self._active_viewer_slot == "a" else "a"
        incoming_pic = (self._viewer_pic_b if incoming_slot == "b"
                        else self._viewer_pic_a)
        incoming_pic.set_visible(True)
        self.edit_btn.set_visible(True)
        self.favorite_btn.set_margin_end(172)   # next to the edit button
        self._update_favorite_btn()
        self.viewer_counter.set_margin_bottom(FILM_THUMB + 12 + 16)
        self._viewer_zoom   = 1.0
        self._viewer_offset = [0.0, 0.0]
        self._reset_viewer_transform()  # clear any leftover zoom from prev media
        self._viewer_pixbuf = pixbuf
        if pixbuf:
            incoming_pic.set_pixbuf(pixbuf)
        # Transition type from nav direction + animations toggle.
        anim_on = bool(self.settings.get("animations_enabled", True))
        if not anim_on:
            self._viewer_stack.set_transition_duration(0)
            self._viewer_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        else:
            # Shorter slide when firing a queued target — arrow-key spam
            # keeps up instead of lagging behind at 250ms per hop.
            duration = 100 if getattr(self, "_viewer_firing_queued", False) else 250
            self._viewer_stack.set_transition_duration(duration)
            nav = getattr(self, "_nav_direction", None)
            if nav == "next":
                self._viewer_stack.set_transition_type(
                    Gtk.StackTransitionType.SLIDE_LEFT)
            elif nav == "prev":
                self._viewer_stack.set_transition_type(
                    Gtk.StackTransitionType.SLIDE_RIGHT)
            else:
                self._viewer_stack.set_transition_type(
                    Gtk.StackTransitionType.CROSSFADE)
        self._viewer_stack.set_visible_child_name(incoming_slot)
        self._active_viewer_slot = incoming_slot
        self.photo_picture = incoming_pic
        self._nav_direction = None
        if pixbuf:
            self._apply_viewer_transform()
        ts = get_photo_date(path)
        try:
            datum = format_viewer_date(datetime.datetime.fromtimestamp(ts))
        except (OverflowError, OSError, ValueError):
            datum = ""
        sep = "  —  " if datum else ""
        self.viewer_title.set_text(f"{os.path.basename(path)}{sep}{datum}")
        self._apply_initial_location(location, searching)
        self.viewer_counter.set_text(f"{self.current_index + 1} / {len(self.photos)}")
        self.prev_btn.set_sensitive(self.current_index > 0)
        self.next_btn.set_sensitive(self.current_index < len(self.photos) - 1)
        self.filmstrip_area.queue_draw()
        GLib.idle_add(self._scroll_filmstrip_to_current)
        self._viewer_current_path = path
        return False

    def _ensure_viewer_transition_listener(self):
        if getattr(self, "_viewer_nav_signal_hooked", False):
            return
        try:
            self._viewer_stack.connect(
                "notify::transition-running",
                self._on_viewer_transition_done,
            )
            self._viewer_nav_signal_hooked = True
        except Exception:
            pass

    def _on_viewer_transition_done(self, stack, _pspec):
        if stack.get_transition_running():
            return
        pending = getattr(self, "_viewer_pending_show", None)
        if pending is None:
            return
        self._viewer_pending_show = None
        self._viewer_firing_queued = True
        try:
            self._show_full_photo(*pending)
        finally:
            self._viewer_firing_queued = False

    def prev_photo(self, btn=None):
        if self.current_index > 0:
            self._stop_video()
            self.current_index -= 1
            self._nav_direction = "prev"
            new_path = self.photos[self.current_index] if self.photos else "?"
            log_info(_("Previous photo: idx={i} → {name}").format(
                i=self.current_index, name=os.path.basename(new_path)
            ))
            self._schedule_photo_load()

    def next_photo(self, btn=None):
        if self.current_index < len(self.photos) - 1:
            self._stop_video()
            self.current_index += 1
            self._nav_direction = "next"
            new_path = self.photos[self.current_index] if self.photos else "?"
            log_info(_("Next photo: idx={i} → {name}").format(
                i=self.current_index, name=os.path.basename(new_path)
            ))
            self._schedule_photo_load()

    def _schedule_photo_load(self):
        """Cache hit → instant; miss → thumbnail placeholder + async."""
        self._viewer_load_id += 1
        load_id = self._viewer_load_id
        # Update counter immediately so held-arrow shows X/N in real time.
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
        # Re-prioritise filmstrip thumbs around the new position so jumping
        # doesn't leave grey/empty cells where it scrolled to.
        if getattr(self, "_filmstrip_reprio_id", None):
            GLib.source_remove(self._filmstrip_reprio_id)
        self._filmstrip_reprio_id = GLib.timeout_add(
            120, self._reprioritize_filmstrip)

        if hasattr(self, '_nav_debounce_id') and self._nav_debounce_id:
            GLib.source_remove(self._nav_debounce_id)
            self._nav_debounce_id = None

        if not self.photos:
            return
        path = self.photos[self.current_index]

        # Cache hit → show instantly, no debounce.
        if not is_video(path):
            with self._pixbuf_cache_lock:
                cached = self._viewer_pixbuf_cache.get(path)
                if cached is not None:
                    self._viewer_pixbuf_cache.move_to_end(path)
            if cached is not None:
                # Resolve location — else label vanishes on prev/next through cached photos.
                initial, coords = self._determine_initial_location(path)
                # Refresh the place tag for THIS photo (not the previous one). GPS
                # is photo EXIF here (cached/fast) — no ffprobe on the main thread.
                self._set_viewer_place(coords if coords else get_gps_coords(path))
                self._show_full_photo(cached, path, initial, searching=bool(coords))
                if coords:
                    self._start_geocode_upgrade(path, coords, load_id)
                GLib.idle_add(self._preload_adjacent_photos)
                return

        # Cache miss: show a loader and keep the previous frame until the
        # full-res image is ready — no black gap, no blurry thumbnail flash.
        if not is_video(path):
            self._photo_spinner.set_visible(True)
            self._photo_spinner.start()

        self._nav_debounce_id = GLib.timeout_add(0, self._do_scheduled_load)

    def _reprioritize_filmstrip(self):
        self._filmstrip_reprio_id = None
        if not self.photos or not getattr(self, "_filmstrip_view_order", None):
            return False
        self._filmstrip_load_id += 1
        load_id = self._filmstrip_load_id
        threading.Thread(
            target=self._load_filmstrip_bg,
            args=(list(self.photos), list(self._filmstrip_view_order), load_id),
            daemon=True
        ).start()
        return False

    def _hide_photo_spinner(self):
        sp = getattr(self, "_photo_spinner", None)
        if sp is not None:
            sp.set_visible(False)
            sp.stop()

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
                    pb = decode_pixbuf(path, 2560)
                except Exception:
                    return
                if pb is None:
                    return
                with self._pixbuf_cache_lock:
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
        self._hide_photo_spinner()
        self._stop_video()
        self._viewer_load_id += 1
        self.header.set_visible(True)
        self.bottom_stack.set_visible(True)
        self._set_toolbars_revealed(True)
        # Don't clear cluster filter — the banner ✕ is how user exits.
        self.main_stack.set_visible_child_name("grid")
        GLib.idle_add(self._close_viewer_cleanup)

    def _close_viewer_cleanup(self):
        self.photo_picture.set_pixbuf(None)
        self.video_display.set_visible(False)
        self.video_controls.set_visible(False)
        self.photo_picture.set_visible(True)
        self.edit_btn.set_visible(True)
        self.favorite_btn.set_margin_end(172)
        self._show_viewer_ui()
        return False

    def _filmstrip_pad(self):
        """Leading/trailing padding so even the first/last thumb can sit centered
        in the viewport (otherwise the newest photo pins to the left edge)."""
        adj = self.filmstrip_scroll.get_hadjustment()
        page = adj.get_page_size() if adj else 0
        cell = FILM_THUMB + 4
        return max(0, (page - cell) / 2)

    def _update_filmstrip(self):
        """Filmstrip is always newest-left → oldest-right, regardless of grid sort."""
        n = len(self.photos)
        cell = FILM_THUMB + 4
        w = n * cell + 2 * int(self._filmstrip_pad())
        self.filmstrip_area.set_size_request(max(w, cell), FILM_THUMB + 8)
        # Memoized view-order; invalidated when self.photos is replaced.
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
        # Load outward from current visible position.
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
                    pb = decode_pixbuf(path, FILM_THUMB)
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
        # Draw visible items only.
        adj = self.filmstrip_scroll.get_hadjustment()
        scroll_x = adj.get_value() if adj else 0
        visible_w = adj.get_page_size() if adj else width
        pad = self._filmstrip_pad()
        first_visible = max(0, int((scroll_x - pad) / cell) - 1)
        last_visible = min(n, int((scroll_x - pad + visible_w) / cell) + 2)
        try:
            current_visual = view.index(self.current_index)
        except ValueError:
            current_visual = -1
        for vp in range(first_visible, last_visible):
            photo_idx = view[vp]
            x = int(pad) + vp * cell + 2
            y = (height - FILM_THUMB) // 2
            pb = self._filmstrip_thumbs.get(vp)
            if pb:
                pw = pb.get_width()
                ph = pb.get_height()
                cr.save()
                # Fill the square cell (cover-crop) with rounded corners, so
                # every thumb is a uniform rounded square like the iOS filmstrip.
                self._film_rounded_rect(cr, x, y, FILM_THUMB, FILM_THUMB, 6)
                cr.clip()
                scale = max(FILM_THUMB / pw, FILM_THUMB / ph) if pw and ph else 1
                ox = x + (FILM_THUMB - pw * scale) / 2
                oy = y + (FILM_THUMB - ph * scale) / 2
                cr.translate(ox, oy)
                cr.scale(scale, scale)
                Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
                cr.paint()
                cr.restore()
            else:
                cr.set_source_rgba(0.3, 0.3, 0.3, 1.0)
                self._film_rounded_rect(cr, x, y, FILM_THUMB, FILM_THUMB, 6)
                cr.fill()
            # Video: play triangle overlay.
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
            # Highlight current with orange rounded border.
            if vp == current_visual:
                cr.set_source_rgba(0.914, 0.329, 0.125, 1.0)
                cr.set_line_width(3)
                self._film_rounded_rect(cr, x + 1.5, y + 1.5, FILM_THUMB - 3, FILM_THUMB - 3, 5)
                cr.stroke()

    def _on_filmstrip_click(self, gesture, n_press, x, y):
        cell = FILM_THUMB + 4
        vp = int((x - self._filmstrip_pad()) // cell)
        view = self._filmstrip_view_order
        if 0 <= vp < len(view):
            photo_idx = view[vp]
            if 0 <= photo_idx < len(self.photos) and photo_idx != self.current_index:
                # Slide in the direction clicked (right → next, left → prev),
                # matching the arrow keys instead of a plain crossfade.
                try:
                    current_vp = view.index(self.current_index)
                except ValueError:
                    current_vp = vp
                self._nav_direction = "next" if vp > current_vp else "prev"
                # Use the exact same path as the arrow keys: stop any video,
                # then schedule the load (placeholder + dispatch photo/video).
                # The old direct _load_full_photo call left the video playing
                # and the main view out of sync with the filmstrip.
                self._stop_video()
                self.current_index = photo_idx
                self._schedule_photo_load()

    def _scroll_filmstrip_to_current(self):
        """Center current photo; clamp keeps first/last at edges."""
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
        # End-padding lets the first/last thumb reach the centre. Compute upper
        # ourselves — the widget's adjustment lags a just-set size request.
        pad = max(0, (page - cell) / 2)
        self.filmstrip_area.set_size_request(
            max(len(self.photos) * cell + 2 * int(pad), cell), FILM_THUMB + 8)
        upper = len(self.photos) * cell + 2 * pad
        center_of_current = pad + vp * cell + cell / 2
        target = center_of_current - page / 2
        target = max(0, min(target, upper - page))

        if not bool(self.settings.get("animations_enabled", True)):
            adj.set_value(target)
            return False

        # Cancel in-flight animation; start fresh.
        if getattr(self, "_filmstrip_anim_id", None) is not None:
            try:
                GLib.source_remove(self._filmstrip_anim_id)
            except Exception:
                pass
            self._filmstrip_anim_id = None
        self._filmstrip_anim_start = adj.get_value()
        self._filmstrip_anim_target = target
        self._filmstrip_anim_step = 0
        self._filmstrip_anim_total = 15  # 15 × 10ms = 150ms
        self._filmstrip_anim_id = GLib.timeout_add(10, self._filmstrip_anim_tick)
        return False

    def _filmstrip_anim_tick(self):
        self._filmstrip_anim_step += 1
        total = self._filmstrip_anim_total
        t = self._filmstrip_anim_step / total
        # Ease-out cubic.
        eased = 1 - (1 - t) ** 3
        start = self._filmstrip_anim_start
        target = self._filmstrip_anim_target
        adj = self.filmstrip_scroll.get_hadjustment()
        adj.set_value(start + (target - start) * eased)
        if self._filmstrip_anim_step >= total:
            adj.set_value(target)
            self._filmstrip_anim_id = None
            return False
        return True

    def _apply_viewer_transform(self):
        if getattr(self, '_transform_pending', False):
            return
        self._transform_pending = True
        GLib.idle_add(self._do_apply_viewer_transform)

    def _reset_viewer_transform(self):
        """Force the shared zoom provider back to identity immediately. The
        provider is shared by both photo slots and the video — without this a
        zoomed photo would bleed into the next video (and vice versa)."""
        self._viewer_zoom = 1.0
        self._viewer_offset = [0.0, 0.0]
        if hasattr(self, '_viewer_css_provider'):
            self._last_applied_transform = (1.0, 0.0, 0.0)
            self._viewer_css_provider.load_from_string(
                "picture { transform: none; }")

    def _zoom_target(self):
        """The widget being zoomed plus its natural (w, h): the visible video
        when a video is open, otherwise the photo. Returns (widget, iw, ih) or
        None when nothing zoomable is shown."""
        if getattr(self, "_video_media", None) and self.video_display.get_visible():
            iw = self._video_media.get_intrinsic_width()
            ih = self._video_media.get_intrinsic_height()
            if iw and ih:
                return self.video_display, iw, ih
            return None
        pb = getattr(self, "_viewer_pixbuf", None)
        if pb:
            return self.photo_picture, pb.get_width(), pb.get_height()
        return None

    def _clamp_viewer_offset(self):
        """Keep a zoomed photo from being dragged off-screen: bound the pan so
        the image always covers the viewport."""
        z = self._viewer_zoom
        if z <= 1.0:
            self._viewer_offset = [0.0, 0.0]
            return
        tgt = self._zoom_target()
        if not tgt:
            return
        widget, iw, ih = tgt
        W = widget.get_width()
        H = widget.get_height()
        if W <= 0 or H <= 0:
            return
        if iw <= 0 or ih <= 0:
            return
        fit = min(W / iw, H / ih)          # CONTAIN-fit size at zoom 1
        cw, ch = iw * fit, ih * fit
        # translate is pre-scale in the CSS matrix, so its viewport effect is ×z.
        max_ox = max(0.0, (cw * z - W) / 2) / z
        max_oy = max(0.0, (ch * z - H) / 2) / z
        ox, oy = self._viewer_offset
        self._viewer_offset[0] = max(-max_ox, min(max_ox, ox))
        self._viewer_offset[1] = max(-max_oy, min(max_oy, oy))

    def _do_apply_viewer_transform(self):
        self._transform_pending = False
        self._clamp_viewer_offset()
        z  = self._viewer_zoom
        ox = self._viewer_offset[0]
        oy = self._viewer_offset[1]
        # Skip the CSS reparse when nothing actually moved (e.g. dragging past a
        # clamp edge) — saves redundant work while panning.
        applied = (round(z, 4), round(ox, 2), round(oy, 2))
        if getattr(self, "_last_applied_transform", None) == applied:
            return False
        self._last_applied_transform = applied
        if not hasattr(self, '_viewer_css_provider'):
            self._viewer_css_provider = Gtk.CssProvider()
            # Attach to BOTH slide slots — self.photo_picture flips between
            # pic_a/pic_b on every nav, so a provider on just one slot leaves
            # zoom dead on half the photos (transform hits the hidden widget).
            # video_display too, so the same scroll-zoom works on videos.
            for _pic in (self._viewer_pic_a, self._viewer_pic_b, self.video_display):
                _pic.get_style_context().add_provider(
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
        old_z = self._viewer_zoom
        factor = 0.9 if dy > 0 else 1.1
        new_z = max(1.0, min(8.0, old_z * factor))
        if new_z == old_z:
            return True
        if new_z == 1.0:
            self._viewer_offset = [0.0, 0.0]
        else:
            # Zoom toward the cursor (like the map): keep the content point
            # under the pointer fixed. screen = z·(point + offset), so
            # offset' = offset + cursor_from_centre · (1/z' − 1/z).
            ptr = getattr(self, "_last_viewer_mouse", None)
            W = self.viewer_area.get_width()
            H = self.viewer_area.get_height()
            if ptr and W > 0 and H > 0:
                d = (1.0 / new_z) - (1.0 / old_z)
                self._viewer_offset[0] += (ptr[0] - W / 2.0) * d
                self._viewer_offset[1] += (ptr[1] - H / 2.0) * d
        self._viewer_zoom = new_z
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
            # Apply straight away (no idle_add hop) so panning tracks the cursor
            # without a frame of lag.
            self._do_apply_viewer_transform()

    def on_viewer_drag_end(self, gesture, dx, dy):
        self._viewer_drag = None

    def on_viewer_key(self, controller, keyval, keycode, state):
        if self.main_stack.get_visible_child_name() != "viewer":
            return False
        if keyval == 65307:  # Esc
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
            self._play_favorite_animation(added=False)
        else:
            self._favorites.add(path)
            log_info(_("Favorite added: {p}").format(p=path))
            self._play_favorite_animation(added=True)
        self._schedule_save_favorites()
        self._update_favorite_btn()
        # Refresh thumb badge if visible.
        widget = self.thumb_widgets.get(self.current_index)
        if widget:
            self._refresh_thumb_favorite(self.current_index)

    def _play_favorite_animation(self, added=True):
        """Star that pops up and fades over the photo. When un-favoriting, a red
        diagonal slash crosses the star out."""
        area = getattr(self, "viewer_area", None)
        if area is None:
            return
        star = Gtk.Label(label="★")
        star.set_halign(Gtk.Align.CENTER)
        star.set_valign(Gtk.Align.CENTER)
        star.set_can_target(False)  # never intercept clicks
        css = Gtk.CssProvider()
        star.add_css_class("fav-burst")
        star.get_style_context().add_provider(
            css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        area.add_overlay(star)

        slash = slash_css = None
        if not added:
            slash = Gtk.Box()
            slash.set_halign(Gtk.Align.CENTER)
            slash.set_valign(Gtk.Align.CENTER)
            slash.set_can_target(False)
            slash.set_size_request(150, 14)
            slash_css = Gtk.CssProvider()
            slash.add_css_class("fav-slash")
            slash.get_style_context().add_provider(
                slash_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            area.add_overlay(slash)

        state = {"i": 0}
        frames = 16

        def _step():
            t = state["i"] / frames
            if t >= 1.0:
                for w in (star, slash):
                    if w is not None:
                        try:
                            area.remove_overlay(w)
                        except Exception:
                            pass
                return False
            scale = 0.6 + 1.3 * t          # 0.6 -> ~1.9
            color = "#ffcc33" if added else "#c8ccd0"
            css.load_from_string(
                ".fav-burst { color: %s; font-size: 110px;"
                " text-shadow: 0 2px 14px rgba(0,0,0,0.5);"
                " transform: scale(%.3f); }" % (color, scale))
            if slash is not None:
                slash_css.load_from_string(
                    ".fav-slash { background: #e0392b; border-radius: 7px;"
                    " box-shadow: 0 1px 8px rgba(0,0,0,0.6);"
                    " transform: rotate(45deg) scale(%.3f); }" % scale)
            op = max(0.0, 1.0 - t)
            star.set_opacity(op)
            if slash is not None:
                slash.set_opacity(op)
            state["i"] += 1
            return True

        GLib.timeout_add(28, _step)

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
