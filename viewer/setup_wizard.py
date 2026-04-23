#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — setup_wizard.py
#  by LinuxGinger
# ─────────────────────────────────────────────

import os

# ── i18n ─────────────────────────────────────────────────────────────
import gettext as _gt
import json as _json_i18n

def _detect_system_lang():
    """Map env locale to one of nl/en/de/fr. Fallback: en."""
    for _var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        _val = os.environ.get(_var, "")
        if _val:
            _code = _val.split(":")[0].split(".")[0].split("_")[0].lower()
            if _code in ("nl", "en", "de", "fr"):
                return _code
    return "en"

_SYS_LANG = _detect_system_lang()
try:
    _lang = _json_i18n.load(open(os.path.expanduser("~/.config/pixora/settings.json"))).get("language", _SYS_LANG)
except Exception:
    _lang = _SYS_LANG
_t = _gt.translation(
    "pixora",
    localedir=os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "locale")),
    languages=[_lang], fallback=True
)
_ = _t.gettext

import json
import math
import subprocess
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio

CONFIG_PATH = os.path.expanduser("~/.config/pixora/settings.json")

BACKUP_FSTYPES = {"ext4", "ext3", "ext2", "ntfs", "exfat", "fuseblk", "btrfs", "xfs", "vfat"}


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
                if label:
                    display = f"💾  {label}  ({size})"
                elif mountpoint:
                    display = f"💾  {mountpoint}  ({size})"
                else:
                    display = f"💾  {_('External drive')}  ({size})"
                drives.append((uuid, display))

            for child in device.get("children", []):
                child["hotplug"] = hotplug
                process_device(child)

        for device in data.get("blockdevices", []):
            process_device(device)

    except Exception as e:
        print(_("Drive detection error: {err}").format(err=e))

    return drives


class SetupWizard(Adw.Window):
    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.drives = []
        self.selected_backup_path = None
        self._chosen_lang = _lang  # starts at detected/settings language
        self._chosen_thumb_size = 200

        self.set_title(_("Pixora — Setup"))
        self.set_default_size(600, 580)
        self.set_resizable(False)

        self.style_manager = Adw.StyleManager.get_default()
        self.style_manager.connect("notify::dark", self._on_dark_mode_changed)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        self.stack.set_transition_duration(250)
        self.stack.set_vexpand(False)

        self.stack.add_named(self._scrolled(self._build_welcome()),   "welcome")
        self.stack.add_named(self._scrolled(self._build_folder()),    "folder")
        self.stack.add_named(self._scrolled(self._build_backup()),    "backup")
        self.stack.add_named(self._scrolled(self._build_duplicate()), "duplicate")
        self.stack.add_named(self._scrolled(self._build_thumbnail()), "thumbnail")

        # Overlay the stack with a spinner-card we show during live language
        # switches so the rebuild flash isn't visible.
        self.stack_overlay = Gtk.Overlay()
        self.stack_overlay.set_child(self.stack)
        self.lang_spinner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.lang_spinner_box.set_halign(Gtk.Align.CENTER)
        self.lang_spinner_box.set_valign(Gtk.Align.CENTER)
        self.lang_spinner_box.add_css_class("card")
        self.lang_spinner_box.set_margin_start(40)
        self.lang_spinner_box.set_margin_end(40)
        self.lang_spinner_box.set_margin_top(40)
        self.lang_spinner_box.set_margin_bottom(40)
        _spinner = Gtk.Spinner()
        _spinner.set_spinning(True)
        _spinner.set_size_request(32, 32)
        self.lang_spinner_box.append(_spinner)
        self.lang_spinner_label = Gtk.Label(label=_("Switching language…"))
        self.lang_spinner_label.add_css_class("title-3")
        self.lang_spinner_box.append(self.lang_spinner_label)
        self.lang_spinner_box.set_visible(False)
        self.stack_overlay.add_overlay(self.lang_spinner_box)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar()
        header.add_css_class("flat")
        main_box.append(header)

        main_box.append(self.stack_overlay)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        main_box.append(sep)

        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_bar.set_margin_top(12)
        btn_bar.set_margin_bottom(12)
        btn_bar.set_margin_start(16)
        btn_bar.set_margin_end(16)

        self.back_btn = Gtk.Button(label=_("Back"))
        self.back_btn.connect("clicked", self.go_back)
        self.back_btn.set_visible(False)
        btn_bar.append(self.back_btn)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        btn_bar.append(spacer)

        self.next_btn = Gtk.Button(label=_("Next"))
        self.next_btn.add_css_class("suggested-action")
        self.next_btn.connect("clicked", self.go_next)
        btn_bar.append(self.next_btn)

        main_box.append(btn_bar)
        # Pin the overall content size so the window doesn't resize whenever
        # a live language switch changes the natural width of the pages
        # (German strings run longer than English, etc.).
        main_box.set_size_request(560, 520)
        self.set_content(main_box)

        self.pages = ["welcome", "folder", "backup", "duplicate", "thumbnail"]
        self.current = 0

    def _scrolled(self, child):
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_size_request(-1, 260)
        sw.set_child(child)
        return sw

    def _on_dark_mode_changed(self, manager, _pspec):
        # Welcome page uses the light/dark wordmark — refresh when theme flips.
        if hasattr(self, "welcome_logo"):
            path = self._logo_path()
            if path:
                self.welcome_logo.set_filename(path)

    # Native display names, NOT translated — always shown in their own tongue.
    _LANG_CODES  = ["nl", "en", "de", "fr"]
    _LANG_LABELS = ["Nederlands", "English", "Deutsch", "Français"]

    def _build_welcome(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.set_margin_top(32)
        page.set_margin_bottom(24)
        page.set_margin_start(40)
        page.set_margin_end(40)
        page.set_halign(Gtk.Align.FILL)
        page.set_valign(Gtk.Align.CENTER)
        page.set_valign(Gtk.Align.START)

        self.welcome_logo = Gtk.Picture()
        logo_path = self._logo_path()
        if logo_path:
            self.welcome_logo.set_filename(logo_path)
        self.welcome_logo.set_size_request(260, 60)
        self.welcome_logo.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.welcome_logo.set_halign(Gtk.Align.CENTER)
        # Without hexpand=False the Picture eats the full page width and
        # halign=CENTER is ignored, so the logo drifts left.
        self.welcome_logo.set_hexpand(False)
        page.append(self.welcome_logo)

        title = Gtk.Label(label=_("Welcome to Pixora!"))
        title.add_css_class("title-1")
        title.set_halign(Gtk.Align.CENTER)
        page.append(title)

        subtitle = Gtk.Label(
            label=_("Pixora imports photos and videos from your iPhone,\ndetects duplicates and makes automatic backups.\n\nThis wizard helps you set up Pixora in a few steps.")
        )
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.CENTER)
        subtitle.set_justify(Gtk.Justification.CENTER)
        page.append(subtitle)

        lang_row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lang_row_box.set_halign(Gtk.Align.CENTER)
        lang_row_box.set_margin_top(8)
        lang_label = Gtk.Label(label=_("Language:"))
        lang_label.set_valign(Gtk.Align.CENTER)
        lang_row_box.append(lang_label)
        lang_model = Gtk.StringList()
        for item in self._LANG_LABELS:
            lang_model.append(item)
        self.lang_combo = Gtk.DropDown(model=lang_model)
        try:
            self.lang_combo.set_selected(self._LANG_CODES.index(self._chosen_lang))
        except ValueError:
            self.lang_combo.set_selected(self._LANG_CODES.index("en"))
        self.lang_combo.connect("notify::selected", self._on_lang_selected)
        lang_row_box.append(self.lang_combo)
        page.append(lang_row_box)

        return page

    # ── Pagina: Foto map ─────────────────────────────────────────────

    def _build_folder(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.set_margin_top(32)
        page.set_margin_bottom(24)
        page.set_margin_start(40)
        page.set_margin_end(40)
        page.set_valign(Gtk.Align.START)

        title = Gtk.Label(label=_("Where do you want to save your photos?"))
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.START)
        page.append(title)

        subtitle = Gtk.Label(
            label=_("Choose a folder on your computer where Pixora\ncopies your photos and videos.")
        )
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.START)
        page.append(subtitle)

        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.folder_entry = Gtk.Entry()
        self.folder_entry.set_placeholder_text(_("Choose a folder…"))
        self.folder_entry.set_hexpand(True)

        browse_btn = Gtk.Button(label=_("Browse…"))
        browse_btn.connect("clicked", self._on_browse_folder)

        row_box.append(self.folder_entry)
        row_box.append(browse_btn)
        page.append(row_box)

        return page

    # ── Pagina: Backup ───────────────────────────────────────────────

    def _build_backup(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.set_margin_top(32)
        page.set_margin_bottom(24)
        page.set_margin_start(40)
        page.set_margin_end(40)
        page.set_valign(Gtk.Align.START)

        title = Gtk.Label(label=_("Automatic backup"))
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.START)
        page.append(title)

        subtitle = Gtk.Label(
            label=_("Pixora can automatically back up to an external USB drive\nor HDD after each import.")
        )
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.START)
        page.append(subtitle)

        group = Adw.PreferencesGroup()

        self.backup_switch = Gtk.Switch()
        self.backup_switch.set_valign(Gtk.Align.CENTER)
        self.backup_switch.connect("notify::active", self._on_backup_toggle)

        backup_row = Adw.ActionRow(
            title=_("Automatic backup"),
            subtitle=_("Syncs to external drive after each import"))
        backup_row.add_suffix(self.backup_switch)
        backup_row.set_activatable_widget(self.backup_switch)
        group.add(backup_row)

        self.drive_model = Gtk.StringList()
        self.drive_model.append(_("No external drives found"))

        self.drive_combo = Gtk.DropDown(model=self.drive_model)
        self.drive_combo.set_sensitive(False)
        self.drive_combo.set_size_request(200, -1)
        self.drive_combo.connect("notify::selected", self._on_drive_selected)

        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        self.refresh_btn.add_css_class("flat")
        self.refresh_btn.set_valign(Gtk.Align.CENTER)
        self.refresh_btn.set_tooltip_text(_("Refresh"))
        self.refresh_btn.connect("clicked", self._on_refresh_drives)

        self.drive_row = Adw.ActionRow(
            title=_("Backup drive"),
            subtitle=_("Only external drives are shown"))
        self.drive_row.add_suffix(self.refresh_btn)
        self.drive_row.add_suffix(self.drive_combo)
        self.drive_row.set_sensitive(False)
        group.add(self.drive_row)

        self.backup_folder_row = Adw.ActionRow(
            title=_("Folder on backup drive"),
            subtitle=_("No drive selected yet"))
        self.backup_folder_btn = Gtk.Button(label=_("Choose…"))
        self.backup_folder_btn.add_css_class("flat")
        self.backup_folder_btn.set_valign(Gtk.Align.CENTER)
        self.backup_folder_btn.connect("clicked", self._on_browse_backup_folder)
        self.backup_folder_row.add_suffix(self.backup_folder_btn)
        self.backup_folder_row.set_sensitive(False)
        group.add(self.backup_folder_row)

        self.backup_error = Gtk.Label(label=_("⚠️  Choose a backup drive to continue"))
        self.backup_error.add_css_class("error")
        self.backup_error.set_halign(Gtk.Align.START)
        self.backup_error.set_visible(False)

        page.append(group)
        page.append(self.backup_error)
        return page

    def _build_duplicate(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.set_margin_top(32)
        page.set_margin_bottom(24)
        page.set_margin_start(40)
        page.set_margin_end(40)
        page.set_valign(Gtk.Align.START)

        title = Gtk.Label(label=_("Duplicate detection"))
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.START)
        page.append(title)

        subtitle = Gtk.Label(
            label=_("Pixora visually compares new photos with your library\nand flags visual copies so you can skip them.")
        )
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.START)
        page.append(subtitle)

        group = Adw.PreferencesGroup()

        self.dup_switch = Gtk.Switch()
        self.dup_switch.set_valign(Gtk.Align.CENTER)
        self.dup_switch.set_active(True)
        dup_row = Adw.ActionRow(
            title=_("Duplicate detection"),
            subtitle=_("Strict check for near-identical photos"),
        )
        dup_row.add_prefix(Gtk.Image.new_from_icon_name("security-high-symbolic"))
        dup_row.add_suffix(self.dup_switch)
        dup_row.set_activatable_widget(self.dup_switch)
        group.add(dup_row)

        info_row = Adw.ActionRow(
            title=_("How it works"),
            subtitle=_("On a match Pixora asks per photo what you want: skip, import anyway, or keep both."),
        )
        info_row.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
        info_row.set_activatable(False)
        try:
            info_row.set_subtitle_lines(3)
        except Exception:
            pass
        group.add(info_row)

        page.append(group)
        return page

    def _build_thumbnail(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.set_margin_top(32)
        page.set_margin_bottom(24)
        page.set_margin_start(40)
        page.set_margin_end(40)
        page.set_valign(Gtk.Align.START)

        title = Gtk.Label(label=_("Thumbnail size"))
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.START)
        page.append(title)

        subtitle = Gtk.Label(
            label=_("Larger thumbnails are easier to see but use more memory.\nThe default (200 px) works well on most machines.")
        )
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.START)
        page.append(subtitle)

        group = Adw.PreferencesGroup()

        scale_row = Adw.ActionRow(title=_("Size"), subtitle=_("200–500 pixels"))
        scale_row.add_prefix(Gtk.Image.new_from_icon_name("view-grid-symbolic"))

        self.thumb_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 200, 500, 25
        )
        self.thumb_scale.set_value(self._chosen_thumb_size)
        self.thumb_scale.set_draw_value(True)
        self.thumb_scale.set_value_pos(Gtk.PositionType.RIGHT)
        self.thumb_scale.set_size_request(220, -1)
        self.thumb_scale.set_valign(Gtk.Align.CENTER)
        self.thumb_scale.connect("value-changed", self._on_thumb_scale_changed)
        scale_row.add_suffix(self.thumb_scale)
        group.add(scale_row)

        self._thumb_preview = Gtk.DrawingArea()
        self._thumb_preview.set_content_width(140)
        self._thumb_preview.set_content_height(140)
        self._thumb_preview.set_draw_func(self._draw_wizard_thumb_preview)
        preview_row = Adw.ActionRow(
            title=_("Preview"),
            subtitle=_("How large your thumbnails will be at this setting"),
        )
        preview_row.add_suffix(self._thumb_preview)
        preview_row.set_activatable(False)
        group.add(preview_row)

        page.append(group)
        return page

    def _on_thumb_scale_changed(self, scale):
        # Round to 25-px steps to match the scale's increment.
        self._chosen_thumb_size = int(round(scale.get_value() / 25) * 25)
        if hasattr(self, "_thumb_preview") and self._thumb_preview is not None:
            try:
                self._thumb_preview.queue_draw()
            except Exception:
                pass

    def _draw_wizard_thumb_preview(self, area, cr, w, h):
        """Rounded square that scales 200→500 px source to ~48→120 px canvas."""
        try:
            size = getattr(self, "_chosen_thumb_size", 200)
            # Canvas bg: subtle gray card.
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

            # 0.24 factor → 200 px renders at 48 px, 500 px at 120 px.
            tile = size * 0.24
            tile = max(24.0, min(tile, h - 16.0))
            x = (w - tile) / 2.0
            y = (h - tile) / 2.0
            tile_r = 6.0
            cr.new_sub_path()
            cr.arc(x + tile - tile_r, y + tile_r, tile_r, -math.pi / 2, 0)
            cr.arc(x + tile - tile_r, y + tile - tile_r, tile_r, 0, math.pi / 2)
            cr.arc(x + tile_r, y + tile - tile_r, tile_r, math.pi / 2, math.pi)
            cr.arc(x + tile_r, y + tile_r, tile_r, math.pi, 3 * math.pi / 2)
            cr.close_path()
            cr.set_source_rgba(0.35, 0.55, 0.85, 0.55)
            cr.fill()
        except Exception:
            pass

    # ── Live language switch ────────────────────────────────────────────

    def _on_lang_selected(self, combo, _pspec):
        idx = combo.get_selected()
        if idx >= len(self._LANG_CODES):
            return
        new_lang = self._LANG_CODES[idx]
        if new_lang == self._chosen_lang:
            return
        # Show the spinner *before* the heavy rebuild so the user sees feedback;
        # the short timeout lets GTK actually paint the overlay.
        self.lang_spinner_box.set_visible(True)
        GLib.timeout_add(80, self._apply_language_switch, new_lang)

    def _apply_language_switch(self, new_lang):
        global _, _t
        _t = _gt.translation(
            "pixora",
            localedir=os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "locale")),
            languages=[new_lang], fallback=True,
        )
        _ = _t.gettext
        self._chosen_lang = new_lang

        self._capture_wizard_state()
        current_name = self.pages[self.current]

        child = self.stack.get_first_child()
        while child is not None:
            self.stack.remove(child)
            child = self.stack.get_first_child()

        self.stack.add_named(self._scrolled(self._build_welcome()),   "welcome")
        self.stack.add_named(self._scrolled(self._build_folder()),    "folder")
        self.stack.add_named(self._scrolled(self._build_backup()),    "backup")
        self.stack.add_named(self._scrolled(self._build_duplicate()), "duplicate")
        self.stack.add_named(self._scrolled(self._build_thumbnail()), "thumbnail")
        self.stack.set_visible_child_name(current_name)

        self._apply_wizard_state()

        # Re-translate the things that live outside the stack.
        self.set_title(_("Pixora — Setup"))
        self.back_btn.set_label(_("Back"))
        self.next_btn.set_label(
            _("Finish") if self.current == len(self.pages) - 1 else _("Next")
        )
        self.lang_spinner_label.set_text(_("Switching language…"))

        self.lang_spinner_box.set_visible(False)
        return False  # don't repeat

    def _capture_wizard_state(self):
        if hasattr(self, "folder_entry"):
            self._chosen_folder = self.folder_entry.get_text()
        if hasattr(self, "backup_switch"):
            self._chosen_backup_enabled = self.backup_switch.get_active()
        if hasattr(self, "drive_combo"):
            self._chosen_drive_idx = self.drive_combo.get_selected()
        if hasattr(self, "dup_switch"):
            self._chosen_dup = self.dup_switch.get_active()
        # _chosen_thumb_size + _chosen_lang + selected_backup_path already live
        # on self and survive the rebuild on their own.

    def _apply_wizard_state(self):
        if hasattr(self, "folder_entry") and hasattr(self, "_chosen_folder"):
            self.folder_entry.set_text(self._chosen_folder)
        if hasattr(self, "backup_switch") and hasattr(self, "_chosen_backup_enabled"):
            self.backup_switch.set_active(self._chosen_backup_enabled)
        if hasattr(self, "drive_combo") and hasattr(self, "_chosen_drive_idx"):
            try:
                if self._chosen_drive_idx < len(self.drives):
                    self.drive_combo.set_selected(self._chosen_drive_idx)
            except Exception:
                pass
        if hasattr(self, "dup_switch") and hasattr(self, "_chosen_dup"):
            self.dup_switch.set_active(self._chosen_dup)

    def go_next(self, btn):
        page = self.pages[self.current]

        if page == "folder":
            if not self.folder_entry.get_text().strip():
                dialog = Adw.MessageDialog(
                    transient_for=self,
                    heading=_("No folder chosen"),
                    body=_("Choose a folder where your photos will be saved.")
                )
                dialog.add_response("ok", _("OK"))
                dialog.present()
                return

        if page == "backup":
            if self.backup_switch.get_active():
                if not self.drives or self.drive_combo.get_selected() >= len(self.drives):
                    self.backup_error.set_label(_("⚠️  Choose a backup drive to continue"))
                    self.backup_error.set_visible(True)
                    return
                if not self.selected_backup_path:
                    self.backup_error.set_label(_("⚠️  Also choose a folder on the backup drive"))
                    self.backup_error.set_visible(True)
                    return
            self.backup_error.set_visible(False)

        if self.current < len(self.pages) - 1:
            self.current += 1
            self.stack.set_visible_child_name(self.pages[self.current])
            self.back_btn.set_visible(True)
            self.next_btn.set_label(_("Finish") if self.current == len(self.pages) - 1 else _("Next"))
        else:
            self._save_and_finish()

    def go_back(self, btn):
        if self.current > 0:
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
            self.current -= 1
            self.stack.set_visible_child_name(self.pages[self.current])
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
            self.back_btn.set_visible(self.current > 0)
            self.next_btn.set_label(_("Next"))
            if hasattr(self, "backup_error"):
                self.backup_error.set_visible(False)

    def _on_browse_folder(self, btn):
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Choose photo folder"))
        dialog.select_folder(self, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                self.folder_entry.set_text(folder.get_path())
        except Exception:
            pass

    def _on_backup_toggle(self, switch, _pspec):
        active = switch.get_active()
        self.drive_row.set_sensitive(active)
        self.drive_combo.set_sensitive(active and bool(self.drives))
        if active and not self.drives:
            self._on_refresh_drives(None)

    def _on_drive_selected(self, combo, _pspec):
        selected = combo.get_selected()
        if self.drives and selected < len(self.drives):
            self.backup_folder_row.set_sensitive(True)
            self.backup_folder_row.set_subtitle(_("No folder chosen yet"))
            self.selected_backup_path = None
            self.backup_error.set_visible(False)

    def _on_browse_backup_folder(self, btn):
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Choose folder on backup drive"))

        selected = self.drive_combo.get_selected()
        if self.drives and selected < len(self.drives):
            uuid = self.drives[selected][0]
            mountpoint = self._get_mountpoint_for_uuid(uuid)
            if mountpoint:
                dialog.set_initial_folder(Gio.File.new_for_path(mountpoint))

        dialog.select_folder(self, None, self._on_backup_folder_selected)

    def _get_mountpoint_for_uuid(self, uuid):
        try:
            result = subprocess.run(
                ["lsblk", "-o", "UUID,MOUNTPOINT", "-J"],
                capture_output=True, text=True
            )
            data = json.loads(result.stdout)
            for device in data.get("blockdevices", []):
                for child in device.get("children", [device]):
                    if child.get("uuid") == uuid:
                        return child.get("mountpoint")
        except Exception:
            pass
        return None

    def _on_backup_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                chosen_path = folder.get_path()
                selected = self.drive_combo.get_selected()
                if self.drives and selected < len(self.drives):
                    uuid = self.drives[selected][0]
                    mountpoint = self._get_mountpoint_for_uuid(uuid)
                    if mountpoint and not chosen_path.startswith(mountpoint):
                        self.backup_error.set_label(_("⚠️  Choose a folder on the backup drive, not on your computer"))
                        self.backup_error.set_visible(True)
                        self.selected_backup_path = None
                        self.backup_folder_row.set_subtitle(_("No folder chosen yet"))
                        return

                self.selected_backup_path = chosen_path
                self.backup_folder_row.set_subtitle(chosen_path)
                self.backup_error.set_visible(False)
        except Exception:
            pass

    def _on_refresh_drives(self, btn):
        self.refresh_btn.set_sensitive(False)
        self.refresh_btn.set_icon_name("content-loading-symbolic")

        def do_refresh():
            drives = get_available_drives()
            GLib.idle_add(self._update_drives, drives)

        threading.Thread(target=do_refresh, daemon=True).start()

    def _update_drives(self, drives):
        self.refresh_btn.set_icon_name("view-refresh-symbolic")
        self.refresh_btn.set_sensitive(True)

        while self.drive_model.get_n_items() > 0:
            self.drive_model.remove(0)

        self.drives = drives
        # Keep combo usable when backup is on but no drives yet, so user can
        # re-click refresh.
        backup_on = self.backup_switch.get_active() if hasattr(self, "backup_switch") else True
        if drives:
            for uuid, label in drives:
                self.drive_model.append(label)
        else:
            self.drive_model.append(_("No external drives found"))
        self.drive_combo.set_sensitive(backup_on)

        return False

    def _logo_path(self):
        dark = self.style_manager.get_dark() if hasattr(self, "style_manager") else False
        variant = "dark" if dark else "light"
        base = os.path.dirname(os.path.abspath(__file__))
        for rel in (f"../assets/logos/pixora-logo-{variant}.svg",
                    f"assets/logos/pixora-logo-{variant}.svg"):
            path = os.path.normpath(os.path.join(base, rel))
            if os.path.exists(path):
                return path
        return None

    def _get_threshold(self):
        return 1 if self.dup_switch.get_active() else 0

    def _get_backup_uuid(self):
        if not self.backup_switch.get_active():
            return None
        selected = self.drive_combo.get_selected()
        if self.drives and selected < len(self.drives):
            return self.drives[selected][0]
        return None

    def _save_and_finish(self):
        settings = {
            "photo_path":          self.folder_entry.get_text(),
            "structure":           "year_month",
            "backup_enabled":      self.backup_switch.get_active(),
            "backup_uuid":         self._get_backup_uuid(),
            "backup_path":         self.selected_backup_path,
            "duplicate_threshold": self._get_threshold(),
        }
        if hasattr(self, "_chosen_lang"):
            settings["language"] = self._chosen_lang
        if hasattr(self, "_chosen_thumb_size"):
            settings["thumbnail_size"] = self._chosen_thumb_size

        # Atomic write + 0600: same pattern as save_settings in main_window.
        # Crash mid-write leaves the previous file intact (or no file, which
        # cleanly re-triggers the wizard) instead of a corrupt half-JSON.
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(settings, f, indent=2)
        os.replace(tmp, CONFIG_PATH)

        from main_window import MainWindow
        win = MainWindow(self.app, settings)
        # set_visible (not present): present() on GNOME Shell fires a
        # "Pixora is ready" notification and, combined with immediately
        # closing the wizard, can leave MainWindow registered-but-never-mapped
        # so the whole app stays alive invisibly.
        win.set_visible(True)
        # Defer our own close so the new window is fully mapped before the
        # wizard disappears from the compositor's window list.
        GLib.idle_add(self.close)
