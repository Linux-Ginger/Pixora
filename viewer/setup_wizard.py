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

import datetime
import json
import math
import subprocess
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio

try:
    gi.require_version("GUdev", "1.0")
    from gi.repository import GUdev
    _GUDEV_AVAILABLE = True
except (ValueError, ImportError):
    _GUDEV_AVAILABLE = False

CONFIG_PATH = os.path.expanduser("~/.config/pixora/settings.json")

BACKUP_FSTYPES = {"ext4", "ext3", "ext2", "ntfs", "exfat", "fuseblk", "btrfs", "xfs", "vfat"}


def get_available_drives():
    """Return drives suitable as backup target. Mirrors main_window's
    detector: accepts any of hotplug / removable / TRAN=usb / mount under
    /media|/run/media|/mnt, so plain-hotplug-false USBs in virtualized
    environments also show up."""
    drives = []
    SYS_MOUNTS = {"/", "/boot", "/boot/efi", "/home", "/var", "/usr", "/etc"}
    EXTERNAL_PREFIXES = ("/media/", "/run/media/", "/mnt/")
    seen_uuids = set()
    try:
        result = subprocess.run(
            ["lsblk", "-o", "NAME,UUID,LABEL,SIZE,FSTYPE,MOUNTPOINT,HOTPLUG,RM,TRAN", "-J"],
            capture_output=True, text=True, timeout=5,
        )
        data = json.loads(result.stdout)

        def _is_external(mountpoint, hotplug, rm, tran):
            if hotplug or rm:
                return True
            if tran in ("usb", "ieee1394"):
                return True
            if mountpoint and any(mountpoint.startswith(p) for p in EXTERNAL_PREFIXES):
                return True
            return False

        def process_device(device, parent_hotplug=False, parent_rm=False, parent_tran=""):
            hotplug = bool(device.get("hotplug", False)) or parent_hotplug
            rm = bool(device.get("rm", False)) or parent_rm
            tran = (device.get("tran") or parent_tran or "").lower()
            uuid = device.get("uuid")
            fstype = (device.get("fstype") or "").lower()
            label = (device.get("label") or "").strip()
            size = device.get("size") or ""
            mountpoint = (device.get("mountpoint") or "").strip()
            if mountpoint in SYS_MOUNTS:
                pass  # skip system partition but still recurse into children
            elif uuid and fstype in BACKUP_FSTYPES:
                if _is_external(mountpoint, hotplug, rm, tran) and uuid not in seen_uuids:
                    seen_uuids.add(uuid)
                    display = (f"💾  {label}  ({size})" if label else
                               f"💾  {mountpoint}  ({size})" if mountpoint else
                               f"💾  {_('External drive')}  ({size})")
                    drives.append((uuid, display))
            for child in device.get("children", []):
                process_device(child, hotplug, rm, tran)

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
        self._chosen_structure = "year_month"
        self._chosen_backup_mode = "backup"  # vs "sync"
        self._chosen_backup_dedup = True
        self._chosen_backup_silent = False

        self.set_title(_("Pixora — Setup"))
        self.set_default_size(720, 660)
        self.set_resizable(False)

        self.style_manager = Adw.StyleManager.get_default()
        self.style_manager.connect("notify::dark", self._on_dark_mode_changed)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        self.stack.set_transition_duration(250)
        self.stack.set_vexpand(False)

        self.stack.add_named(self._scrolled(self._build_welcome()),   "welcome")
        self.stack.add_named(self._scrolled(self._build_folder()),    "folder")
        self.stack.add_named(self._scrolled(self._build_structure()), "structure")
        self.stack.add_named(self._scrolled(self._build_backup()),    "backup")
        self.stack.add_named(self._scrolled(self._build_duplicate()), "duplicate")
        self.stack.add_named(self._scrolled(self._build_thumbnail()), "thumbnail")
        self.stack.add_named(self._scrolled(self._build_license()),   "license")

        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        # Overlay the stack with a spinner-card we show during live language
        # switches so the rebuild flash isn't visible.
        self.stack_overlay = Gtk.Overlay()
        self.stack_overlay.set_hexpand(True)
        self.stack_overlay.set_vexpand(True)
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
        # Pin a generous min content size so the window doesn't resize when
        # language switches change the natural width of the pages.
        main_box.set_size_request(680, 600)
        self.set_content(main_box)

        self.pages = ["welcome", "folder", "structure", "backup",
                      "duplicate", "thumbnail", "license"]
        self.current = 0

        # Live drive-detection: fire a refresh ~1s after any block event so
        # newly plugged USBs show up without having to click refresh.
        self._udev_client = None
        self._udev_refresh_id = None
        if _GUDEV_AVAILABLE:
            try:
                self._udev_client = GUdev.Client(subsystems=["block"])
                self._udev_client.connect("uevent", self._on_block_event)
            except Exception:
                self._udev_client = None
        self.connect("close-request", self._on_wizard_close)

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
    # Flag emojis match the settings dialog (main_window.py:5340).
    _LANG_CODES  = ["nl", "en", "de", "fr"]
    _LANG_LABELS = ["🇳🇱  Nederlands", "🇬🇧  English", "🇩🇪  Deutsch", "🇫🇷  Français"]

    def _build_welcome(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.set_margin_top(32)
        page.set_margin_bottom(24)
        page.set_margin_start(40)
        page.set_margin_end(40)
        page.set_halign(Gtk.Align.FILL)
        page.set_valign(Gtk.Align.CENTER)
        page.set_valign(Gtk.Align.START)

        # SVG rendered at native size (340x120) so there's no downscale blur.
        logo_center = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        logo_center.set_halign(Gtk.Align.CENTER)
        self.welcome_logo = Gtk.Picture()
        logo_path = self._logo_path()
        if logo_path:
            self.welcome_logo.set_filename(logo_path)
        self.welcome_logo.set_size_request(340, 120)
        self.welcome_logo.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.welcome_logo.set_can_shrink(False)
        self.welcome_logo.set_hexpand(False)
        logo_center.append(self.welcome_logo)
        page.append(logo_center)

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
            label=_("Pick a folder where your photos will be kept.")
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

    # ── Pagina: Mappen-structuur ─────────────────────────────────────

    def _build_structure(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.set_margin_top(32)
        page.set_margin_bottom(24)
        page.set_margin_start(40)
        page.set_margin_end(40)
        page.set_valign(Gtk.Align.START)

        title = Gtk.Label(label=_("Folder structure"))
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.START)
        page.append(title)

        subtitle = Gtk.Label(
            label=_("Choose how Pixora sorts your photos into folders.")
        )
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.START)
        page.append(subtitle)

        group = Adw.PreferencesGroup()

        self.radio_flat = Gtk.CheckButton()
        self.radio_flat.set_active(self._chosen_structure == "flat")
        self.radio_flat.connect("toggled",
            lambda b: self._on_structure_radio("flat", b))
        flat_row = Adw.ActionRow(
            title=_("All together"),
            subtitle=_("All photos go into a single folder — no subfolders."),
        )
        flat_row.add_prefix(Gtk.Image.new_from_icon_name("folder-symbolic"))
        flat_row.add_prefix(self.radio_flat)
        flat_row.set_activatable_widget(self.radio_flat)
        group.add(flat_row)

        self.radio_year = Gtk.CheckButton()
        self.radio_year.set_group(self.radio_flat)
        self.radio_year.set_active(self._chosen_structure == "year")
        self.radio_year.connect("toggled",
            lambda b: self._on_structure_radio("year", b))
        year_row = Adw.ActionRow(
            title=_("By year"),
            subtitle=_("Separate folder per year — e.g. 2024/, 2025/."),
        )
        year_row.add_prefix(Gtk.Image.new_from_icon_name("folder-open-symbolic"))
        year_row.add_prefix(self.radio_year)
        year_row.set_activatable_widget(self.radio_year)
        group.add(year_row)

        self.radio_month = Gtk.CheckButton()
        self.radio_month.set_group(self.radio_flat)
        self.radio_month.set_active(self._chosen_structure == "year_month")
        self.radio_month.connect("toggled",
            lambda b: self._on_structure_radio("year_month", b))
        month_row = Adw.ActionRow(
            title=_("By year and month"),
            subtitle=_("Year folder with month subfolders — e.g. 2024/2024-03/."),
        )
        month_row.add_prefix(Gtk.Image.new_from_icon_name("view-list-symbolic"))
        month_row.add_prefix(self.radio_month)
        month_row.set_activatable_widget(self.radio_month)
        group.add(month_row)

        page.append(group)
        return page

    def _on_structure_radio(self, value, btn):
        if btn.get_active():
            self._chosen_structure = value

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
            label=_("Automatically save a copy to an external drive.")
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

        # Backup-mode radios (same copy as the Settings dialog).
        self.radio_mode_backup = Gtk.CheckButton()
        self.radio_mode_backup.set_active(self._chosen_backup_mode == "backup")
        self.radio_mode_backup.connect(
            "toggled", lambda b: self._on_backup_mode_radio("backup", b)
        )
        self._mode_backup_row = Adw.ActionRow(
            title=_("Backup"),
            subtitle=_("One-way copy: additions only. Photos you delete in Pixora stay on the USB as an archive."),
        )
        self._mode_backup_row.add_prefix(Gtk.Image.new_from_icon_name("drive-harddisk-symbolic"))
        self._mode_backup_row.add_prefix(self.radio_mode_backup)
        self._mode_backup_row.set_activatable_widget(self.radio_mode_backup)
        try:
            self._mode_backup_row.set_subtitle_lines(3)
        except Exception:
            pass
        self._mode_backup_row.set_sensitive(False)
        group.add(self._mode_backup_row)

        self.radio_mode_sync = Gtk.CheckButton()
        self.radio_mode_sync.set_group(self.radio_mode_backup)
        self.radio_mode_sync.set_active(self._chosen_backup_mode == "sync")
        self.radio_mode_sync.connect(
            "toggled", lambda b: self._on_backup_mode_radio("sync", b)
        )
        self._mode_sync_row = Adw.ActionRow(
            title=_("Sync"),
            subtitle=_("Exact mirror of your Pixora library. Photos you delete in Pixora are also removed from the USB on the next backup."),
        )
        self._mode_sync_row.add_prefix(Gtk.Image.new_from_icon_name("emblem-synchronizing-symbolic"))
        self._mode_sync_row.add_prefix(self.radio_mode_sync)
        self._mode_sync_row.set_activatable_widget(self.radio_mode_sync)
        try:
            self._mode_sync_row.set_subtitle_lines(3)
        except Exception:
            pass
        self._mode_sync_row.set_sensitive(False)
        group.add(self._mode_sync_row)

        # Backup-dedup toggle (only effective once main duplicate-check is on,
        # but we let the user set it here; MainWindow enforces at runtime).
        self.backup_dedup_switch = Gtk.Switch()
        self.backup_dedup_switch.set_valign(Gtk.Align.CENTER)
        self.backup_dedup_switch.set_active(self._chosen_backup_dedup)
        self.backup_dedup_switch.connect(
            "notify::active", self._on_backup_dedup_toggle
        )
        self._dedup_row = Adw.ActionRow(
            title=_("Backup duplicate detector"),
            subtitle=_("Skips photos already on the USB, even if they are stored there under a different name or folder. Requires duplicate detection above to be enabled."),
        )
        self._dedup_row.add_prefix(Gtk.Image.new_from_icon_name("edit-copy-symbolic"))
        self._dedup_row.add_suffix(self.backup_dedup_switch)
        self._dedup_row.set_activatable_widget(self.backup_dedup_switch)
        try:
            self._dedup_row.set_subtitle_lines(3)
        except Exception:
            pass
        self._dedup_row.set_sensitive(False)
        group.add(self._dedup_row)

        # Auto-confirm (silent) toggle.
        self.backup_silent_switch = Gtk.Switch()
        self.backup_silent_switch.set_valign(Gtk.Align.CENTER)
        self.backup_silent_switch.set_active(self._chosen_backup_silent)
        self.backup_silent_switch.connect(
            "notify::active", self._on_backup_silent_toggle
        )
        self._silent_row = Adw.ActionRow(
            title=_("Auto-confirm"),
            subtitle=_("Starts right away when there's work to do, without interrupting."),
        )
        self._silent_row.add_prefix(Gtk.Image.new_from_icon_name("media-playback-start-symbolic"))
        self._silent_row.add_suffix(self.backup_silent_switch)
        self._silent_row.set_activatable_widget(self.backup_silent_switch)
        try:
            self._silent_row.set_subtitle_lines(3)
        except Exception:
            pass
        self._silent_row.set_sensitive(False)
        group.add(self._silent_row)

        self.backup_error = Gtk.Label(label=_("⚠️  Choose a backup drive to continue"))
        self.backup_error.add_css_class("error")
        self.backup_error.set_halign(Gtk.Align.START)
        self.backup_error.set_visible(False)

        page.append(group)
        page.append(self.backup_error)
        return page

    def _on_backup_mode_radio(self, value, btn):
        if btn.get_active():
            self._chosen_backup_mode = value

    def _on_backup_dedup_toggle(self, switch, _pspec):
        self._chosen_backup_dedup = switch.get_active()

    def _on_backup_silent_toggle(self, switch, _pspec):
        self._chosen_backup_silent = switch.get_active()

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
            label=_("Recognize photos you already have.")
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
            label=_("How big photos appear in the grid.")
        )
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.START)
        page.append(subtitle)

        group = Adw.PreferencesGroup()

        scale_row = Adw.ActionRow(
            title=_("Size"),
            subtitle=_("200–500 pixels"),
        )
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

        # Reset-to-default button (same icon as Settings).
        self.thumb_reset_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        self.thumb_reset_btn.add_css_class("flat")
        self.thumb_reset_btn.add_css_class("circular")
        self.thumb_reset_btn.set_valign(Gtk.Align.CENTER)
        self.thumb_reset_btn.set_tooltip_text(_("Back to default (200 px)"))
        self.thumb_reset_btn.set_sensitive(self._chosen_thumb_size != 200)
        self.thumb_reset_btn.connect(
            "clicked", lambda b: self.thumb_scale.set_value(200.0)
        )
        scale_row.add_suffix(self.thumb_reset_btn)

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
        if hasattr(self, "thumb_reset_btn"):
            try:
                self.thumb_reset_btn.set_sensitive(self._chosen_thumb_size != 200)
            except Exception:
                pass

    def _draw_wizard_thumb_preview(self, area, cr, w, h):
        """Mirror of main_window's _draw_thumb_preview: mock home-grid with
        header strip and Pixora icon so the wizard and Settings show the
        same visualization."""
        try:
            size = getattr(self, "_chosen_thumb_size", 200)
            # Clip to rounded card.
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
                    from gi.repository import GdkPixbuf
                    icon_path = os.path.abspath(os.path.join(
                        os.path.dirname(os.path.abspath(__file__)),
                        "..", "assets", "logos", "pixora-icon.svg",
                    ))
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
                    from gi.repository import Gdk
                    Gdk.cairo_set_source_pixbuf(
                        cr, self._thumb_preview_logo, 4, (header_h - 10) / 2
                    )
                    cr.paint()
                except Exception:
                    pass
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.35)
            cr.rectangle(18, header_h / 2 - 1, 28, 2)
            cr.fill()

            # 12% scale reads well in the 140-wide canvas (true range 200→500 px).
            scale = 0.12
            base = size * scale
            gap = max(3.0, base * 0.08)
            pad_x = 6.0
            pad_top = header_h + 6.0
            radius = max(3.0, base * 0.06)

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

    # ── Pagina: Licentie ─────────────────────────────────────────────

    def _build_license(self):
        """Mirror of main_window._on_view_license: ✓/!/✗ summary plus the
        full LICENSE text embedded inline."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_margin_top(20)
        page.set_margin_bottom(20)
        page.set_margin_start(28)
        page.set_margin_end(28)
        page.set_hexpand(True)
        page.set_vexpand(True)

        heading = Gtk.Label(label=_("GNU General Public License v3.0"))
        heading.add_css_class("title-2")
        heading.set_halign(Gtk.Align.START)
        page.append(heading)

        year_now = datetime.datetime.now().year
        try:
            version_file = os.path.abspath(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "version.txt",
            ))
            with open(version_file, "r", encoding="utf-8") as _vf:
                version = _vf.read().strip()
        except Exception:
            version = ""
        product = f"Pixora {version}" if version else "Pixora"
        if year_now > 2024:
            cr_text = f"© 2024 – {year_now} {product} · LinuxGinger"
        else:
            cr_text = f"© 2024 {product} · LinuxGinger"
        cr_lbl = Gtk.Label(label=cr_text)
        cr_lbl.add_css_class("dim-label")
        cr_lbl.set_halign(Gtk.Align.START)
        page.append(cr_lbl)

        summary = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
            homogeneous=True,
        )
        summary.append(self._license_summary_col(
            _("Permitted"), "✓", "success",
            [
                _("Private and commercial use"),
                _("Modify the code"),
                _("Distribute (original or modified)"),
                _("Patent licenses from contributors"),
            ],
        ))
        summary.append(self._license_summary_col(
            _("Required"), "!", "warning",
            [
                _("Include source code when distributing"),
                _("Use the same GPL-3 license"),
                _("Mark modifications clearly"),
                _("Keep the copyright notice"),
            ],
        ))
        summary.append(self._license_summary_col(
            _("Not permitted"), "✗", "error",
            [
                _("Include in proprietary software"),
                _("Claim warranty (there is none)"),
                _("Hold authors liable"),
            ],
        ))
        page.append(summary)

        full_hdr = Gtk.Label(label=_("Full license text"))
        full_hdr.add_css_class("heading")
        full_hdr.set_halign(Gtk.Align.START)
        full_hdr.set_margin_top(6)
        page.append(full_hdr)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        scroll.set_min_content_height(320)
        tv = Gtk.TextView()
        tv.set_editable(False)
        tv.set_cursor_visible(False)
        tv.set_monospace(True)
        tv.set_wrap_mode(Gtk.WrapMode.WORD)
        tv.set_left_margin(10)
        tv.set_right_margin(10)
        tv.set_top_margin(8)
        tv.set_bottom_margin(8)
        license_path = os.path.abspath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "LICENSE"
        ))
        try:
            with open(license_path, "r", encoding="utf-8") as f:
                lic_text = f.read()
        except Exception as e:
            lic_text = _("Could not load license: {err}").format(err=e)
        tv.get_buffer().set_text(lic_text)
        scroll.set_child(tv)
        page.append(scroll)

        return page

    def _license_summary_col(self, title, icon_char, css_class, items):
        """Same layout as main_window._license_summary_col — Unicode badge
        (✓ / ! / ✗) so we don't depend on an icon theme."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        badge = Gtk.Label(label=icon_char)
        badge.add_css_class("heading")
        badge.add_css_class(css_class)
        header.append(badge)
        hlbl = Gtk.Label(label=title)
        hlbl.add_css_class("heading")
        hlbl.set_halign(Gtk.Align.START)
        header.append(hlbl)
        box.append(header)
        for item in items:
            lbl = Gtk.Label(label="• " + item)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_wrap(True)
            lbl.set_xalign(0)
            lbl.add_css_class("caption")
            box.append(lbl)
        return box

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
        self.stack.add_named(self._scrolled(self._build_structure()), "structure")
        self.stack.add_named(self._scrolled(self._build_backup()),    "backup")
        self.stack.add_named(self._scrolled(self._build_duplicate()), "duplicate")
        self.stack.add_named(self._scrolled(self._build_thumbnail()), "thumbnail")
        self.stack.add_named(self._scrolled(self._build_license()),   "license")
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
        # Folder-row stays off until a drive is actually selected; without
        # a drive there's nothing valid to browse. _on_drive_selected
        # enables it.
        has_drive = (
            bool(self.drives)
            and self.drive_combo.get_selected() < len(self.drives)
        )
        if hasattr(self, "backup_folder_row"):
            self.backup_folder_row.set_sensitive(active and has_drive)
        for row_attr in ("_mode_backup_row", "_mode_sync_row",
                         "_dedup_row", "_silent_row"):
            row = getattr(self, row_attr, None)
            if row is not None:
                row.set_sensitive(active)
        if active and not self.drives:
            self._on_refresh_drives(None)

    def _on_drive_selected(self, combo, _pspec):
        selected = combo.get_selected()
        if self.drives and selected < len(self.drives):
            # Only unlock the folder row if backup-mode itself is enabled.
            self.backup_folder_row.set_sensitive(self.backup_switch.get_active())
            self.backup_folder_row.set_subtitle(_("No folder chosen yet"))
            self.selected_backup_path = None
            self.backup_error.set_visible(False)

    def _on_browse_backup_folder(self, btn):
        """Use the same custom picker as the Settings dialog instead of the
        GNOME file chooser — it's confined to the USB root, has inline
        'create subfolder', and sidesteps the GNOME dialog's quirks."""
        selected = self.drive_combo.get_selected()
        if not (self.drives and selected < len(self.drives)):
            return
        uuid = self.drives[selected][0]
        mountpoint = self._get_mountpoint_for_uuid(uuid)
        if not mountpoint:
            self.backup_error.set_label(
                _("⚠️  The selected drive is not mounted")
            )
            self.backup_error.set_visible(True)
            return
        from main_window import BackupFolderPicker
        picker = BackupFolderPicker(
            mountpoint=mountpoint,
            current_path=self.selected_backup_path,
            on_selected=self._apply_backup_folder_choice,
        )
        picker.present(self)

    def _apply_backup_folder_choice(self, chosen):
        self.selected_backup_path = chosen
        self.backup_folder_row.set_subtitle(chosen)
        self.backup_error.set_visible(False)

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

    def _on_refresh_drives(self, btn):
        self.refresh_btn.set_sensitive(False)
        self.refresh_btn.set_icon_name("content-loading-symbolic")

        def do_refresh():
            try:
                drives = get_available_drives()
            except Exception:
                drives = []
            GLib.idle_add(self._update_drives, drives)

        threading.Thread(target=do_refresh, daemon=True).start()

    def _on_block_event(self, client, action, device):
        # 'add' = new device registered; 'change' = partition table rescanned
        # (e.g. after auto-mount assigns a mountpoint). Debounce a refresh
        # a second later — lsblk needs a beat after udev before UUID/fstype
        # are filled in.
        if action not in ("add", "change"):
            return
        if self._udev_refresh_id is not None:
            try:
                GLib.source_remove(self._udev_refresh_id)
            except Exception:
                pass
        self._udev_refresh_id = GLib.timeout_add(1000, self._udev_trigger_refresh)

    def _udev_trigger_refresh(self):
        self._udev_refresh_id = None
        # Only refresh if the backup page has been built — otherwise
        # there's nothing to update.
        if hasattr(self, "refresh_btn"):
            self._on_refresh_drives(None)
        return False

    def _on_wizard_close(self, *_a):
        if self._udev_refresh_id is not None:
            try:
                GLib.source_remove(self._udev_refresh_id)
            except Exception:
                pass
            self._udev_refresh_id = None
        self._udev_client = None
        return False  # allow close

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
        self.drive_combo.set_sensitive(backup_on and bool(drives))
        # Folder-row follows the drive-selected state. A fresh drives list
        # re-selects index 0, which may or may not fire notify::selected —
        # update here explicitly so the row doesn't stay locked out.
        if hasattr(self, "backup_folder_row"):
            self.backup_folder_row.set_sensitive(backup_on and bool(drives))

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
            "structure":           self._chosen_structure,
            "backup_enabled":      self.backup_switch.get_active(),
            "backup_uuid":         self._get_backup_uuid(),
            "backup_path":         self.selected_backup_path,
            "backup_mode":         self._chosen_backup_mode,
            "backup_dedup":        self._chosen_backup_dedup,
            "backup_silent":       self._chosen_backup_silent,
            "duplicate_threshold": self._get_threshold(),
            "language":            self._chosen_lang,
            "thumbnail_size":      self._chosen_thumb_size,
        }

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
