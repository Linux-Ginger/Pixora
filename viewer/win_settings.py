#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — win_settings.py
#  Settings window
#  by LinuxGinger
# ─────────────────────────────────────────────

import datetime
import math
import os
import subprocess
import sys
from pixora_i18n import _
import common
from common import (Adw, ASSETS_DIR, CONFIG_PATH, Gdk, GdkPixbuf, GLib, Gtk,
    log_error, log_info, save_settings, _LOCALE_DIR)
from metadata import _mk


class SettingsMixin:
    """Settings window (mixed into MainWindow)."""

    def _build_settings_switcher(self, stack):
        """Custom icon-above-label tab-row driving a Gtk.Stack (keeps stack transitions)."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        bar.add_css_class("pixora-settings-tabs")
        bar.set_halign(Gtk.Align.CENTER)

        if not hasattr(self, "_settings_tabs_css"):
            self._settings_tabs_css = Gtk.CssProvider()
            # Hover uses translateY (no scale-blur); checked bumps -gtk-icon-size.
            # Separate sliding indicator handles the :checked background.
            self._settings_tabs_css.load_from_string(
                ".pixora-settings-tab {"
                "  padding: 2px 14px;"
                "  border-radius: 10px;"
                "  min-width: 84px;"
                "  background: transparent;"
                "}"
                ".pixora-settings-tab:checked {"
                "  background: transparent;"
                "  box-shadow: none;"
                "}"
                ".pixora-settings-tab image {"
                # Animate transform only — animating -gtk-icon-size re-rasterizes
                # the symbolic icon each frame and flashes its magenta fallback.
                "  transition: transform 200ms cubic-bezier(.2,.9,.3,1.2);"
                "  -gtk-icon-size: 20px;"
                # Reserve 24px (checked size) so headerbar height stays constant.
                "  min-width: 24px;"
                "  min-height: 24px;"
                "}"
                ".pixora-settings-tab:hover image {"
                "  transform: translateY(-2px);"
                "}"
                ".pixora-settings-tab:active image {"
                "  transform: scale(0.92);"
                "}"
                ".pixora-settings-tab:checked image {"
                "  -gtk-icon-size: 24px;"
                "}"
                ".pixora-settings-tab label {"
                "  font-size: 10px;"
                "  opacity: 0.7;"
                "  transition: opacity 200ms;"
                "}"
                ".pixora-settings-tab:checked label {"
                "  opacity: 1.0;"
                "}"
                ".pixora-tab-indicator {"
                # 10% fg tint (neutral, like Adwaita :checked pill); full-height.
                "  background: alpha(currentColor, 0.10);"
                "  border-radius: 10px;"
                "}"
                # .no-anim (Reduce Animations) kills hover/active transforms.
                ".pixora-settings-tabs.no-anim .pixora-settings-tab image {"
                "  transition: none;"
                "  transform: none;"
                "}"
                ".pixora-settings-tabs.no-anim .pixora-settings-tab:hover image,"
                ".pixora-settings-tabs.no-anim .pixora-settings-tab:active image {"
                "  transform: none;"
                "}"
                ".pixora-settings-tabs.no-anim .pixora-settings-tab label {"
                "  transition: none;"
                "}"
            )

        tabs = []
        first_name = None
        page_order = ["display", "import", "advanced", "about"]
        for page_name in page_order:
            child = stack.get_child_by_name(page_name)
            if child is None:
                continue
            page = stack.get_page(child)
            icon = page.get_icon_name() or ""
            title = page.get_title() or page_name.title()

            btn = Gtk.ToggleButton()
            btn.add_css_class("flat")
            btn.add_css_class("pixora-settings-tab")
            btn.get_style_context().add_provider(
                self._settings_tabs_css,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            vbox.set_halign(Gtk.Align.CENTER)
            img = Gtk.Image.new_from_icon_name(icon)
            img.get_style_context().add_provider(
                self._settings_tabs_css,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
            vbox.append(img)
            lbl = Gtk.Label(label=title)
            lbl.get_style_context().add_provider(
                self._settings_tabs_css,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
            vbox.append(lbl)
            btn.set_child(vbox)
            tabs.append((btn, page_name))
            bar.append(btn)
            if first_name is None:
                first_name = page_name

        # Group so only one is active.
        if tabs:
            for btn, _n in tabs[1:]:
                btn.set_group(tabs[0][0])

        # Sliding indicator drawn BEHIND tab row (main child), tab row as overlay.
        # set_measure_overlay sizes to tab row's natural width.
        indicator = Gtk.Box()
        indicator.add_css_class("pixora-tab-indicator")
        indicator.get_style_context().add_provider(
            self._settings_tabs_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        indicator.set_halign(Gtk.Align.START)
        indicator.set_valign(Gtk.Align.FILL)
        indicator.set_can_target(False)

        overlay = Gtk.Overlay()
        overlay.set_halign(Gtk.Align.CENTER)
        overlay.set_child(indicator)
        overlay.add_overlay(bar)
        try:
            overlay.set_measure_overlay(bar, True)
        except Exception:
            pass
        self._settings_tab_indicator = indicator
        self._settings_tab_indicator_anim_id = None
        self._settings_tabs_bar = bar
        if not bool(self.settings.get("animations_enabled", True)):
            bar.add_css_class("no-anim")

        def _on_toggled(btn, name):
            if not btn.get_active():
                return
            stack.set_visible_child_name(name)
            self._animate_settings_indicator_to(btn)

        for btn, name in tabs:
            btn.connect("toggled", _on_toggled, name)

        if tabs:
            tabs[0][0].set_active(True)
            if first_name:
                stack.set_visible_child_name(first_name)
            # First allocation arrives next idle — position indicator then.
            GLib.idle_add(self._place_settings_indicator_under, tabs[0][0])
        return overlay

    def _tab_x_in_overlay(self, btn):
        """Translate btn's parent-relative x into overlay coords."""
        try:
            bar = btn.get_parent()
            btn_alloc = btn.get_allocation()
            bar_alloc = bar.get_allocation() if bar is not None else None
        except Exception:
            return (0, 0)
        if btn_alloc.width <= 0 or bar_alloc is None or bar_alloc.width <= 0:
            return (0, 0)
        return (bar_alloc.x + btn_alloc.x, btn_alloc.width)

    def _place_settings_indicator_under(self, btn, tries=0):
        x, w = self._tab_x_in_overlay(btn)
        if w <= 0 and tries < 25:
            # Layout not ready — retry up to ~625ms.
            GLib.timeout_add(25, self._place_settings_indicator_under,
                             btn, tries + 1)
            return False
        if w > 0:
            self._settings_tab_indicator.set_margin_start(x)
            self._settings_tab_indicator.set_size_request(w, -1)
        return False

    def _animate_settings_indicator_to(self, btn):
        target_x, target_w = self._tab_x_in_overlay(btn)
        if target_w <= 0:
            # Not allocated yet; snap next tick.
            GLib.idle_add(self._place_settings_indicator_under, btn)
            return
        anim_on = bool(self.settings.get("animations_enabled", True))
        if not anim_on:
            self._settings_tab_indicator.set_margin_start(target_x)
            self._settings_tab_indicator.set_size_request(target_w, -1)
            return
        # Cancel in-flight animation.
        if self._settings_tab_indicator_anim_id is not None:
            try:
                GLib.source_remove(self._settings_tab_indicator_anim_id)
            except Exception:
                pass
            self._settings_tab_indicator_anim_id = None
        start_x = self._settings_tab_indicator.get_margin_start()
        cur_w, _ = self._settings_tab_indicator.get_size_request()
        start_w = cur_w if cur_w > 0 else target_w
        self._settings_tab_indicator_anim_start = (start_x, start_w)
        self._settings_tab_indicator_anim_target = (target_x, target_w)
        self._settings_tab_indicator_anim_step = 0
        self._settings_tab_indicator_anim_total = 22  # 22 × 10ms = 220ms
        self._settings_tab_indicator_anim_id = GLib.timeout_add(
            10, self._settings_tab_indicator_tick,
        )

    def _settings_tab_indicator_tick(self):
        self._settings_tab_indicator_anim_step += 1
        total = self._settings_tab_indicator_anim_total
        t = self._settings_tab_indicator_anim_step / total
        # Ease-out cubic.
        eased = 1 - (1 - t) ** 3
        sx, sw = self._settings_tab_indicator_anim_start
        tx, tw = self._settings_tab_indicator_anim_target
        x = int(sx + (tx - sx) * eased)
        w = int(sw + (tw - sw) * eased)
        self._settings_tab_indicator.set_margin_start(x)
        self._settings_tab_indicator.set_size_request(w, -1)
        if self._settings_tab_indicator_anim_step >= total:
            self._settings_tab_indicator.set_margin_start(tx)
            self._settings_tab_indicator.set_size_request(tw, -1)
            self._settings_tab_indicator_anim_id = None
            return False
        return True

    def _settings_new_page_container(self):
        """Mimic Adw.PreferencesPage; returns (outer_for_stack, inner_box_for_groups)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(12)
        box.set_margin_end(12)
        clamp = Adw.Clamp()
        clamp.set_maximum_size(580)
        clamp.set_tightening_threshold(400)
        clamp.set_child(box)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        scroll.set_child(clamp)
        return scroll, box

    def on_settings_clicked(self, btn):
        if self._settings_dialog is not None:
            self._settings_dialog.present()
            return
        log_info(_("Settings opened"))
        # Custom Adw.Window + ViewStack: Adw.PreferencesWindow forces NONE transitions.
        dialog = Adw.Window()
        dialog.set_title(_("Settings"))
        dialog.set_transient_for(self)
        dialog.set_modal(False)
        dialog.set_default_size(720, 720)
        self._settings_dialog = dialog
        if hasattr(self, "settings_btn"):
            self.settings_btn.set_sensitive(False)

        def _on_settings_closed(_d):
            self._settings_dialog = None
            self._settings_stack = None
            self._settings_tabs_bar = None
            self.home_row = None
            self._remove_home_btn = None
            self._places_group = None
            self._place_rows = []
            if hasattr(self, "settings_btn"):
                self.settings_btn.set_sensitive(True)
            return False

        dialog.connect("close-request", _on_settings_closed)

        display_outer, display_box = self._settings_new_page_container()
        import_outer,  import_box  = self._settings_new_page_container()
        advanced_outer, advanced_box = self._settings_new_page_container()
        about_outer,   about_box   = self._settings_new_page_container()

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
        display_box.append(folder_group)

        self._places_group = Adw.PreferencesGroup()
        self._places_group.set_title(_mk(_("Home & places")))
        self._places_group.set_description(
            _("Your address is looked up once via OpenStreetMap to get its "
              "coordinates, then stored only on this computer — never shared. "
              "Place names shown on the map and in the viewer are likewise "
              "fetched once from OpenStreetMap and cached locally."))
        self._place_rows = []
        display_box.append(self._places_group)
        self._rebuild_places_group()

        display_group = Adw.PreferencesGroup()
        display_group.set_title(_("Display"))
        display_group.set_description(_("How photos are displayed in the grid"))

        thumb_row = Adw.ActionRow(
            title=_("Thumbnail size"),
            subtitle=f"{common.THUMB_SIZE} px"
        )
        thumb_adj = Gtk.Adjustment(
            value=float(common.THUMB_SIZE),
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

        # Apply button: grid regen only on click, so sliding stays fluid.
        self._thumb_apply_btn = Gtk.Button(icon_name="emblem-ok-symbolic")
        self._thumb_apply_btn.add_css_class("flat")
        self._thumb_apply_btn.add_css_class("circular")
        self._thumb_apply_btn.set_valign(Gtk.Align.CENTER)
        self._thumb_apply_btn.set_tooltip_text(_("Apply"))
        self._thumb_apply_btn.set_sensitive(False)
        self._thumb_apply_btn.connect("clicked", self._on_thumb_apply_clicked)
        thumb_row.add_suffix(self._thumb_apply_btn)

        display_group.add(thumb_row)

        # Live preview while sliding (doesn't touch real thumbs).
        self._thumb_preview = Gtk.DrawingArea()
        self._thumb_preview.set_content_width(140)
        self._thumb_preview.set_content_height(140)
        self._thumb_preview.set_draw_func(self._draw_thumb_preview)
        self._pending_thumb_size = common.THUMB_SIZE
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

        display_box.append(display_group)

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

        # Renderer dropdown applies via GSK_RENDERER — requires restart (sentinel).
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
        # Popup rows: label + warning icon for previously-crashed backends,
        # same style as the "update available" badge in About.
        list_factory = Gtk.SignalListItemFactory()
        def _gsk_row_setup(_f, list_item):
            box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
            )
            lbl = Gtk.Label(xalign=0)
            badge = self._recommended_badge()
            badge.set_visible(False)
            spacer = Gtk.Box(hexpand=True)
            icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            icon.add_css_class("warning")
            icon.set_visible(False)
            box.append(lbl)
            box.append(badge)
            box.append(spacer)
            box.append(icon)
            list_item.set_child(box)
        def _gsk_row_bind(_f, list_item):
            box = list_item.get_child()
            lbl = box.get_first_child()
            badge = lbl.get_next_sibling()
            spacer = badge.get_next_sibling()
            icon = spacer.get_next_sibling()
            item = list_item.get_item()
            lbl.set_text(item.get_string() if item is not None else "")
            pos = list_item.get_position()
            code = self._gsk_codes[pos] if 0 <= pos < len(self._gsk_codes) else ""
            # "auto" is the recommended backend.
            badge.set_visible(code == "auto")
            bl = self.settings.get("gsk_renderer_crashed") or []
            icon.set_visible(code in bl)
        list_factory.connect("setup", _gsk_row_setup)
        list_factory.connect("bind", _gsk_row_bind)
        self._gsk_combo.set_list_factory(list_factory)
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
        # Advanced knob — only surfaced in dev mode; GPU crashes are otherwise
        # handled automatically by the renderer crash-recovery.
        gsk_row.set_visible(bool(self.settings.get("dev_mode", False)))
        perf_group.add(gsk_row)

        advanced_box.append(perf_group)

        dev_group = Adw.PreferencesGroup()
        dev_group.set_title(_("Advanced"))
        dev_group.set_description(
            _("Runs Pixora in a terminal with debug output, plus extra tools. Only enable if you know what you're doing.")
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
        advanced_box.append(dev_group)

        reset_group = Adw.PreferencesGroup()
        reset_group.set_title(_("Reset"))
        reset_btn = Gtk.Button(label=_("Reset"))
        reset_btn.add_css_class("destructive-action")
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.connect("clicked", self._on_factory_reset_clicked)
        reset_row = Adw.ActionRow(
            title=_("Reset to factory settings"),
            subtitle=_("Clears all settings and restarts in the setup wizard. Your photos are NOT touched."),
        )
        reset_row.add_prefix(Gtk.Image.new_from_icon_name("edit-clear-all-symbolic"))
        reset_row.add_suffix(reset_btn)
        try:
            reset_row.set_subtitle_lines(3)
        except Exception:
            pass
        reset_group.add(reset_row)
        advanced_box.append(reset_group)

        structure_group = Adw.PreferencesGroup()
        structure_group.set_title(_("Folder structure"))
        structure_group.set_description(
            _("How imported photos are saved in your library.")
        )

        structure_info_row = Adw.ActionRow(
            title=_("How it works"),
            subtitle=_("Photos are filed by date in the layout below."),
        )
        structure_info_row.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
        structure_info_row.set_activatable(False)
        try:
            structure_info_row.set_subtitle_lines(3)
        except Exception:
            pass
        structure_group.add(structure_info_row)

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
        month_row.add_suffix(self._recommended_badge())
        month_row.set_activatable_widget(self.radio_month)
        structure_group.add(month_row)

        # "Run now" buttons duplicate what the background pass does, so they're
        # only shown in developer mode.
        dev_mode = bool(self.settings.get("dev_mode", False))

        reorganize_btn = Gtk.Button(label=_("Tidy up"))
        reorganize_btn.add_css_class("flat")
        reorganize_btn.set_valign(Gtk.Align.CENTER)
        reorganize_btn.connect("clicked", lambda b: self._reorganize_now())
        reorganize_row = Adw.ActionRow(
            title=_("Reorganize folders now"),
            subtitle=_("Moves your library into the structure chosen above. Possible duplicates are shown for review — never deleted automatically."),
        )
        reorganize_row.add_prefix(Gtk.Image.new_from_icon_name("view-refresh-symbolic"))
        reorganize_row.add_suffix(reorganize_btn)
        reorganize_row.set_visible(dev_mode)
        try:
            reorganize_row.set_subtitle_lines(4)
        except Exception:
            pass
        structure_group.add(reorganize_row)

        import_box.append(structure_group)

        dup_group = Adw.PreferencesGroup()
        dup_group.set_title(_("Duplicate detection"))
        dup_group.set_description(_("Check for existing photos during import"))
        # Threshold 0 = off, ≥1 = on. "On" always uses strict (=1).
        dup_on = self.settings.get("duplicate_threshold", 2) != 0

        dup_info_row = Adw.ActionRow(
            title=_("How it works"),
            subtitle=_("New photos are checked against your library. On a match you choose: skip, import anyway, or keep both."),
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
        dup_row.add_suffix(self._recommended_badge())
        dup_row.add_suffix(self.settings_dup_switch)
        dup_row.set_activatable_widget(self.settings_dup_switch)
        dup_group.add(dup_row)

        self._dedup_btn = Gtk.Button(label=_("Clean up now"))
        self._dedup_btn.add_css_class("flat")
        self._dedup_btn.set_valign(Gtk.Align.CENTER)
        self._dedup_btn.set_sensitive(dup_on)
        self._dedup_btn.connect("clicked", lambda b: self._prompt_find_duplicates())
        dedup_row = Adw.ActionRow(
            title=_("Clean up duplicates"),
            subtitle=_("Finds duplicates already in your library, including edited copies. You review everything first — the original is always kept."),
        )
        dedup_row.add_prefix(Gtk.Image.new_from_icon_name("edit-copy-symbolic"))
        dedup_row.add_suffix(self._dedup_btn)
        dedup_row.set_visible(dev_mode)
        try:
            dedup_row.set_subtitle_lines(4)
        except Exception:
            pass
        dup_group.add(dedup_row)

        import_box.append(dup_group)

        convert_group = Adw.PreferencesGroup()
        convert_group.set_title(_("Improve compatibility"))
        convert_group.set_description(
            _("iPhone photos (HEIC) and videos (MOV/HEVC) are hard to open "
              "outside Apple. Pixora can convert them to JPEG and MP4 (H.264) "
              "so they work everywhere — date and location are kept."))

        self.settings_convert_switch = Gtk.Switch()
        self.settings_convert_switch.set_valign(Gtk.Align.CENTER)
        self.settings_convert_switch.set_active(
            self.settings.get("convert_heic", False))
        self.settings_convert_switch.connect(
            "notify::active", self.on_convert_switch_toggled)
        convert_row = Adw.ActionRow(
            title=_("Convert HEIC to JPEG on import"))
        convert_row.add_prefix(Gtk.Image.new_from_icon_name("image-x-generic-symbolic"))
        convert_row.add_suffix(self._recommended_badge())
        convert_row.add_suffix(self.settings_convert_switch)
        convert_row.set_activatable_widget(self.settings_convert_switch)
        try:
            convert_row.set_subtitle_lines(2)
        except Exception:
            pass
        convert_group.add(convert_row)

        convert_now_row = Adw.ActionRow(
            title=_("Convert existing photos now"))
        convert_now_row.add_prefix(
            Gtk.Image.new_from_icon_name("media-playback-start-symbolic"))
        self._convert_lib_btn = Gtk.Button(label=_("Run now"))
        self._convert_lib_btn.add_css_class("flat")
        self._convert_lib_btn.set_valign(Gtk.Align.CENTER)
        self._convert_lib_btn.set_sensitive(
            bool(self.settings.get("convert_heic", False)))
        self._convert_lib_btn.connect("clicked", self._on_convert_library_clicked)
        convert_now_row.add_suffix(self._convert_lib_btn)
        convert_now_row.set_visible(dev_mode)
        try:
            convert_now_row.set_subtitle_lines(5)
        except Exception:
            pass
        convert_group.add(convert_now_row)

        # ── Video conversion (same group) ──
        self.settings_video_switch = Gtk.Switch()
        self.settings_video_switch.set_valign(Gtk.Align.CENTER)
        self.settings_video_switch.set_active(
            self.settings.get("convert_videos", False))
        self.settings_video_switch.connect(
            "notify::active", self.on_video_convert_toggled)
        video_row = Adw.ActionRow(
            title=_("Convert MOV to MP4 (H.264) on import"),
            subtitle=_("H.264 video is repackaged instantly; HEVC is re-encoded (slower, can be larger)."))
        video_row.add_prefix(Gtk.Image.new_from_icon_name("video-x-generic-symbolic"))
        video_row.add_suffix(self.settings_video_switch)
        video_row.set_activatable_widget(self.settings_video_switch)
        try:
            video_row.set_subtitle_lines(2)
        except Exception:
            pass
        convert_group.add(video_row)

        video_now_row = Adw.ActionRow(
            title=_("Convert existing videos now"))
        video_now_row.add_prefix(
            Gtk.Image.new_from_icon_name("media-playback-start-symbolic"))
        self._video_lib_btn = Gtk.Button(label=_("Run now"))
        self._video_lib_btn.add_css_class("flat")
        self._video_lib_btn.set_valign(Gtk.Align.CENTER)
        self._video_lib_btn.set_sensitive(
            bool(self.settings.get("convert_videos", False)))
        self._video_lib_btn.connect("clicked", self._on_convert_videos_library_clicked)
        video_now_row.add_suffix(self._video_lib_btn)
        video_now_row.set_visible(dev_mode)
        convert_group.add(video_now_row)

        live_switch = Gtk.Switch()
        live_switch.set_valign(Gtk.Align.CENTER)
        live_switch.set_active(bool(self.settings.get("import_live_motion", True)))
        live_switch.connect("notify::active", self._on_live_motion_toggled)
        live_row = Adw.ActionRow(
            title=_("Live Photos: also import the short video"),
            subtitle=_("Press LIVE in the viewer to play it. Off = only the photo."))
        live_row.add_prefix(Gtk.Image.new_from_icon_name("camera-photo-symbolic"))
        live_row.add_suffix(live_switch)
        live_row.set_activatable_widget(live_switch)
        convert_group.add(live_row)

        import_box.append(convert_group)

        backup_group = Adw.PreferencesGroup()
        backup_group.set_title(_("Automatic backup"))
        backup_group.set_description(_("Backup to external USB drive after each import"))

        backup_on = bool(self.settings.get("backup_enabled"))
        drive_present = self._backup_drive_mountpoint() is not None

        if backup_on and self.settings.get("backup_uuid") and not drive_present:
            backup_group.set_description(
                _("Drive not connected — plug in the USB drive to back up.")
            )

        backup_info_row = Adw.ActionRow(
            title=_("How it works"),
            subtitle=_("After each import your new photos are copied to the USB drive in the background — so you always have a second copy."),
        )
        backup_info_row.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
        backup_info_row.set_activatable(False)
        try:
            backup_info_row.set_subtitle_lines(3)
        except Exception:
            pass
        backup_group.add(backup_info_row)

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
            subtitle=_("Exact mirror: photos you delete in Pixora are also removed from the USB on the next backup."),
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
        # USB-dedup reuses pHash; force off when main detection off.
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
            subtitle=_("Skips photos already on the USB, even under a different name or folder. Requires duplicate detection."),
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

        self.settings_manual_scan_btn = Gtk.Button()
        self.settings_manual_scan_btn.add_css_class("flat")
        self.settings_manual_scan_btn.set_valign(Gtk.Align.CENTER)
        self.settings_manual_scan_btn.set_size_request(120, 32)
        self.settings_manual_scan_btn.connect("clicked", self.on_settings_manual_scan)
        # States: idle | checking | uptodate (✓ 5s fade).
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
        # If scan/backup already running, spinner on open so click isn't wasted.
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
        manual_scan_row.set_visible(dev_mode)
        self.settings_manual_scan_row = manual_scan_row
        backup_group.add(manual_scan_row)

        self.settings_backup_group = backup_group

        import_box.append(backup_group)

        auto_group = Adw.PreferencesGroup()
        auto_group.set_title(_("Background tasks"))
        auto_group.set_description(
            _("Every few minutes Pixora runs the tasks you've enabled — "
              "folder tidy-up, USB backup and photo/video conversion. By default "
              "it asks first."))
        self._auto_confirm_switch = Gtk.Switch()
        self._auto_confirm_switch.set_valign(Gtk.Align.CENTER)
        self._auto_confirm_switch.set_active(bool(
            self.settings.get("auto_confirm")))
        self._auto_confirm_switch.connect(
            "notify::active", self._on_auto_confirm_toggle)
        auto_row = Adw.ActionRow(
            title=_("Run automatically without asking"))
        auto_row.add_prefix(
            Gtk.Image.new_from_icon_name("emblem-ok-symbolic"))
        auto_row.add_suffix(self._auto_confirm_switch)
        auto_row.set_activatable_widget(self._auto_confirm_switch)
        auto_group.add(auto_row)
        import_box.append(auto_group)

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

        # "What's new" — release notes pulled from the latest GitHub release.
        self._changelog_expander = Adw.ExpanderRow(
            title=_("What's new?"),
            subtitle=_("Release notes from the latest version"))
        self._changelog_expander.add_prefix(
            Gtk.Image.new_from_icon_name("view-list-bullet-symbolic"))
        notes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        notes_box.set_margin_top(8)
        notes_box.set_margin_bottom(8)
        notes_box.set_margin_start(12)
        notes_box.set_margin_end(12)
        self._changelog_label = Gtk.Label(label=_("Loading release notes…"))
        self._changelog_label.set_wrap(True)
        self._changelog_label.set_xalign(0)
        self._changelog_label.set_selectable(True)
        self._changelog_label.add_css_class("body")
        notes_box.append(self._changelog_label)
        self._changelog_expander.add_row(notes_box)
        about_group.add(self._changelog_expander)
        self._load_about_changelog()

        self._update_check_row = Adw.ActionRow(title=_("Check for updates"))

        # Button has 4 states: idle / checking / uptodate / available (pulsing).
        self._update_check_state = "idle"
        self._update_check_pulse_id = None
        self._update_check_fade_id = None

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

        # Testing channel: developer mode only, so normal use never gets a
        # pre-release (see common.wants_prerelease_updates).
        pre_switch = Gtk.Switch()
        pre_switch.set_valign(Gtk.Align.CENTER)
        pre_switch.set_active(bool(self.settings.get("prerelease_updates", False)))
        pre_switch.connect("notify::active", self._on_prerelease_toggled)
        pre_row = Adw.ActionRow(
            title=_("Test pre-releases"),
            subtitle=_("For testing only: updates then come from pre-releases, "
                       "never from normal releases. Off = normal updates."))
        pre_row.add_prefix(Gtk.Image.new_from_icon_name("applications-science-symbolic"))
        pre_row.add_suffix(pre_switch)
        pre_row.set_activatable_widget(pre_switch)
        try:
            pre_row.set_subtitle_lines(2)
        except Exception:
            pass
        pre_row.set_visible(bool(self.settings.get("dev_mode", False)))
        about_group.add(pre_row)
        about_box.append(about_group)

        # Startup check already found update → pulse now.
        if self._pending_update_version:
            self._update_check_row.set_subtitle(
                _("Version {v} available").format(v=self._pending_update_version)
            )
            self._set_update_state("available")

        # Credit line: PreferencesGroup with only a description renders as dim-label.
        credit_group = Adw.PreferencesGroup()
        credit_group.set_description(
            _("GitHub® and the Invertocat logo are trademarks of GitHub, Inc.")
            + "\n"
            + _("© {year} Pixora — LinuxGinger").format(
                year=datetime.datetime.now().year)
        )
        about_box.append(credit_group)

        license_group = Adw.PreferencesGroup()
        license_group.set_title(_("License"))
        license_group.set_description(
            _("Pixora is free software under GPL-3.0. You may use, modify and share it — as long as you respect those same rights for others.")
        )
        license_row = Adw.ActionRow(
            title=_("GNU General Public License v3.0"),
            subtitle=_("Open the full license text"),
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
        about_box.append(license_group)

        # Adw.ViewStack has no transitions → use Gtk.Stack.
        stack = Gtk.Stack()
        self._settings_stack = stack  # so _apply_animations_state can tune it live
        anim_on = bool(self.settings.get("animations_enabled", True))
        stack.set_transition_duration(450 if anim_on else 0)
        stack.set_transition_type(
            Gtk.StackTransitionType.SLIDE_LEFT_RIGHT if anim_on
            else Gtk.StackTransitionType.NONE
        )
        stack.set_vexpand(True)
        stack.set_hexpand(True)

        for child, name, title, icon in (
            (display_outer,  "display",  _("Display"),
                "preferences-desktop-display-symbolic"),
            (import_outer,   "import",   _("Import"),
                "document-send-symbolic"),
            (advanced_outer, "advanced", _("Advanced"),
                "applications-engineering-symbolic"),
            (about_outer,    "about",    _("About"),
                "help-about-symbolic"),
        ):
            stack.add_titled(child, name, title)
            page = stack.get_page(child)
            page.set_icon_name(icon)

        switcher = self._build_settings_switcher(stack)

        header = Adw.HeaderBar()
        header.set_title_widget(switcher)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(stack)

        dialog.set_content(toolbar)
        dialog.present()

    def _on_thumb_size_changed(self, scale, row):
        new_size = int(scale.get_value())
        # Snap to steps of 20 px.
        new_size = (new_size // 20) * 20
        row.set_subtitle(f"{new_size} px")
        self._pending_thumb_size = new_size
        if hasattr(self, "_thumb_preview"):
            try:
                self._thumb_preview.queue_draw()
            except Exception:
                pass
        if hasattr(self, "_thumb_apply_btn"):
            self._thumb_apply_btn.set_sensitive(new_size != common.THUMB_SIZE)

    def _draw_thumb_preview(self, area, cr, w, h):
        """Mock home-grid with mini Pixora icon."""
        try:
            size = getattr(self, "_pending_thumb_size", common.THUMB_SIZE)
            # Clip canvas to rounded rect.
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
            # Title-stripes next to logo.
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.35)
            cr.rectangle(18, header_h / 2 - 1, 28, 2)
            cr.fill()

            # 12% scale reads well in 140-wide canvas (true 200→500 px).
            scale = 0.12
            base = size * scale
            gap = max(3.0, base * 0.08)
            pad_x = 6.0
            pad_top = header_h + 6.0
            radius = max(3.0, base * 0.06)

            # Landscape/portrait/square mix across rows.
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
        new_size = getattr(self, "_pending_thumb_size", common.THUMB_SIZE)
        if new_size == common.THUMB_SIZE:
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
        if response != "apply":
            return
        new_size = getattr(self, "_pending_thumb_size", common.THUMB_SIZE)
        if new_size == common.THUMB_SIZE:
            return
        log_info(_("Thumbnail size changed: {old}px → {new}px").format(
            old=common.THUMB_SIZE, new=new_size,
        ))
        common.THUMB_SIZE = new_size
        self.settings["thumbnail_size"] = new_size
        save_settings(self.settings)
        if hasattr(self, "_thumb_apply_btn"):
            self._thumb_apply_btn.set_sensitive(False)
        self.load_photos()

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

        # Overlay text in the NEW language so user sees the switch already.
        try:
            import gettext as _gettext_mod
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

        # Sentinel + in-place exec after app.run() returns (no D-Bus race).
        try:
            sentinel = os.path.expanduser("~/.cache/pixora/.restart_pending")
            os.makedirs(os.path.dirname(sentinel), exist_ok=True)
            with open(sentinel, "w") as f:
                f.write("1")
        except Exception as e:
            log_error(_("Restart sentinel write failed: {err}").format(err=e))
        GLib.timeout_add(600, lambda: (self.get_application().quit(), False)[1])

    def _on_anim_toggle(self, switch, _pspec):
        # "Reduce animations" ACTIVE → animations_enabled=False.
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
        # Blacklisted renderer previously crashed Pixora — confirm before restart.
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
            heading=_("Try '{r}' anyway?").format(r=nice),
            body=_("This backend crashed Pixora last time."),
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
            # Revert dropdown to stored value.
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
        # Detached watcher handles the restart + crash-respawn. Done here
        # instead of via the .restart_pending sentinel so auto-restart on a
        # bad GSK renderer works regardless of launcher version or how
        # Pixora was invoked.
        self._spawn_gsk_restart_watcher()
        # Overlay gives user feedback before restart.
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

    def _spawn_gsk_restart_watcher(self):
        """Detached bash that waits for our PID to exit, starts Pixora,
        and reruns it once if the new GSK_RENDERER crashed startup."""
        pixora_bin = os.path.expanduser("~/.local/bin/pixora")
        if os.path.exists(pixora_bin):
            launch_cmd = f'"{pixora_bin}"'
        else:
            main_py = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "main.py")
            )
            launch_cmd = f'"{sys.executable}" "{main_py}"'
        gsk_sentinel = os.path.expanduser("~/.cache/pixora/.gsk_pending")
        log_path = os.path.expanduser("~/.cache/pixora/watcher.log")
        my_pid = os.getpid()
        watcher_bash = (
            f"set -x; "
            f"echo '--- watcher start' $(date -Iseconds) pid=$$; "
            f"for _ in $(seq 1 100); do "
            f"  kill -0 {my_pid} 2>/dev/null || break; "
            f"  sleep 0.1; "
            f"done; "
            f"echo 'old pid gone, launching'; "
            f"{launch_cmd}; rc=$?; "
            f"echo \"first run rc=$rc, sentinel exists: $([ -f '{gsk_sentinel}' ] && echo yes || echo no)\"; "
            f'if [ $rc -ne 0 ] && [ -f "{gsk_sentinel}" ]; then '
            f"  echo 'respawning'; "
            f"  {launch_cmd}; rc=$?; "
            f"  echo \"second run rc=$rc\"; "
            f"fi; "
            f"echo '--- watcher done'"
        )
        env = os.environ.copy()
        # Fresh env for the new Pixora; dev-terminal skips itself when
        # .gsk_pending is set so the watcher sees the crash directly.
        env.pop("PIXORA_IN_DEV_TERM", None)
        env.pop("PIXORA_RESTART_COUNT", None)
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            log_f = open(log_path, "a")
            subprocess.Popen(
                ["bash", "-c", watcher_bash],
                start_new_session=True,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_f,
                stderr=log_f,
            )
        except Exception as e:
            log_error(_("Restart watcher spawn failed: {err}").format(err=e))

    def _on_toggle_dev_mode(self, btn, row):
        currently_active = bool(self.settings.get("dev_mode", False))
        target = not currently_active
        if target:
            heading = _("Activate developer mode?")
            body = _("Pixora will restart in a terminal so you can see what it's doing. Updates run there too.")
        else:
            heading = _("Deactivate developer mode?")
            body = _("Pixora will then start without a terminal and use the GUI updater.")
        dialog = Adw.AlertDialog(
            heading=heading, body=body
        )
        dialog.add_response("cancel", _("No"))
        dialog.add_response("apply", _("Yes"))
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._apply_dev_mode, target, row, btn)
        self._present_dialog(dialog)

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
        # Restart the app.
        GLib.timeout_add(300, self._restart_app)

    def _on_factory_reset_clicked(self, btn):
        dlg = Adw.AlertDialog(
            heading=_("Reset to factory settings?"),
            body=_("This clears all your Pixora settings — chosen folder, backup setup, language and switches — and restarts Pixora in the setup wizard.\n\nYour photos and library on disk are NOT touched."))
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("reset", _("Reset"))
        dlg.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")
        dlg.connect("response", self._do_factory_reset)
        self._present_dialog(dlg)

    def _do_factory_reset(self, dlg, response):
        if response != "reset":
            return
        # Delete settings.json so the next start is a clean first-run (wizard).
        try:
            self.settings = {}
            for p in (CONFIG_PATH, CONFIG_PATH + ".tmp"):
                try:
                    os.remove(p)
                except FileNotFoundError:
                    pass
            log_info(_("Factory reset: settings cleared, restarting…"))
        except Exception as e:
            log_error(_("Factory reset error: {err}").format(err=e))
        GLib.timeout_add(300, self._restart_app)

    def _restart_app(self):
        # Sentinel + in-place exec; main.py re-runs itself via os.execvp.
        # Dev-terminal handoff keeps working because main.py starts fresh.
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

    def _on_prerelease_toggled(self, switch, _pspec):
        self.settings["prerelease_updates"] = switch.get_active()
        save_settings(self.settings)
        # Forget what the other channel found and look again right away.
        self._pending_update_version = None
        self._latest_release = None
        self._set_update_state("idle")  # else a pending "Update" would install
        self._on_settings_check_update(None)

    def _on_live_motion_toggled(self, switch, _pspec):
        self.settings["import_live_motion"] = switch.get_active()
        save_settings(self.settings)

    def _confirm_switch_off(self, switch, handler, heading, body, apply_off):
        """Destructive-off confirm: revert the switch unless the user confirms."""
        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.add_response("keep", _("Keep on"))
        dlg.add_response("off", _("Turn off"))
        dlg.set_response_appearance("off", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("keep")
        dlg.set_close_response("keep")

        def _resp(d, r):
            if r == "off":
                apply_off()
            else:
                switch.handler_block_by_func(handler)
                switch.set_active(True)
                switch.handler_unblock_by_func(handler)
        dlg.connect("response", _resp)
        self._present_dialog(dlg)

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
