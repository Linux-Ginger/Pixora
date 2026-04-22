#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — setup_wizard.py
#  by LinuxGinger
# ─────────────────────────────────────────────

import os

# ── i18n ─────────────────────────────────────────────────────────────
import gettext as _gt
import json as _json_i18n
try:
    _lang = _json_i18n.load(open(os.path.expanduser("~/.config/pixora/settings.json"))).get("language", "nl")
except Exception:
    _lang = "nl"
_t = _gt.translation(
    "pixora",
    localedir=os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "locale")),
    languages=[_lang], fallback=True
)
_ = _t.gettext

import json
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
                    display = f"💾  {_('Externe schijf')}  ({size})"
                drives.append((uuid, display))

            for child in device.get("children", []):
                child["hotplug"] = hotplug
                process_device(child)

        for device in data.get("blockdevices", []):
            process_device(device)

    except Exception as e:
        print(_("Drive detectie fout: {err}").format(err=e))

    return drives


class SetupWizard(Adw.Window):
    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.drives = []
        self.selected_backup_path = None

        self.set_title(_("Pixora — Instellen"))
        self.set_default_size(480, 400)
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

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar()
        header.add_css_class("flat")
        main_box.append(header)

        main_box.append(self.stack)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        main_box.append(sep)

        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_bar.set_margin_top(12)
        btn_bar.set_margin_bottom(12)
        btn_bar.set_margin_start(16)
        btn_bar.set_margin_end(16)

        self.back_btn = Gtk.Button(label=_("Terug"))
        self.back_btn.connect("clicked", self.go_back)
        self.back_btn.set_visible(False)
        btn_bar.append(self.back_btn)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        btn_bar.append(spacer)

        self.next_btn = Gtk.Button(label=_("Volgende"))
        self.next_btn.add_css_class("suggested-action")
        self.next_btn.connect("clicked", self.go_next)
        btn_bar.append(self.next_btn)

        main_box.append(btn_bar)
        self.set_content(main_box)

        self.pages = ["welcome", "folder", "backup", "duplicate"]
        self.current = 0

    def _scrolled(self, child):
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_size_request(-1, 260)
        sw.set_child(child)
        return sw

    def _on_dark_mode_changed(self, manager, _pspec):
        pass  # SVG icon works in both themes

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
        self.welcome_logo.set_size_request(64, 64)
        self.welcome_logo.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.welcome_logo.set_halign(Gtk.Align.CENTER)
        page.append(self.welcome_logo)

        title = Gtk.Label(label=_("Welkom bij Pixora!"))
        title.add_css_class("title-1")
        title.set_halign(Gtk.Align.CENTER)
        page.append(title)

        subtitle = Gtk.Label(
            label=_("Pixora importeert foto's en video's van je iPhone,\n"
                    "detecteert duplicaten en maakt automatisch backups.\n\n"
                    "Deze wizard helpt je Pixora instellen in een paar stappen.")
        )
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.CENTER)
        subtitle.set_justify(Gtk.Justification.CENTER)
        page.append(subtitle)

        return page

    # ── Pagina: Foto map ─────────────────────────────────────────────

    def _build_folder(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.set_margin_top(32)
        page.set_margin_bottom(24)
        page.set_margin_start(40)
        page.set_margin_end(40)
        page.set_valign(Gtk.Align.START)

        title = Gtk.Label(label=_("Waar wil je je foto's opslaan?"))
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.START)
        page.append(title)

        subtitle = Gtk.Label(
            label=_("Kies een map op je computer waar Pixora\n"
                    "je foto's en video's naartoe kopieert.")
        )
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.START)
        page.append(subtitle)

        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.folder_entry = Gtk.Entry()
        self.folder_entry.set_placeholder_text(_("Kies een map…"))
        self.folder_entry.set_hexpand(True)

        browse_btn = Gtk.Button(label=_("Bladeren…"))
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

        title = Gtk.Label(label=_("Automatische backup"))
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.START)
        page.append(title)

        subtitle = Gtk.Label(
            label=_("Pixora kan na elke import automatisch een backup\n"
                    "maken naar een externe USB schijf of HDD.")
        )
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.START)
        page.append(subtitle)

        group = Adw.PreferencesGroup()

        self.backup_switch = Gtk.Switch()
        self.backup_switch.set_valign(Gtk.Align.CENTER)
        self.backup_switch.connect("notify::active", self._on_backup_toggle)

        backup_row = Adw.ActionRow(
            title=_("Automatische backup"),
            subtitle=_("Synchroniseert na elke import naar externe schijf"))
        backup_row.add_suffix(self.backup_switch)
        backup_row.set_activatable_widget(self.backup_switch)
        group.add(backup_row)

        self.drive_model = Gtk.StringList()
        self.drive_model.append(_("Geen externe schijven gevonden"))

        self.drive_combo = Gtk.DropDown(model=self.drive_model)
        self.drive_combo.set_sensitive(False)
        self.drive_combo.set_size_request(200, -1)
        self.drive_combo.connect("notify::selected", self._on_drive_selected)

        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        self.refresh_btn.add_css_class("flat")
        self.refresh_btn.set_valign(Gtk.Align.CENTER)
        self.refresh_btn.set_tooltip_text(_("Vernieuwen"))
        self.refresh_btn.connect("clicked", self._on_refresh_drives)

        self.drive_row = Adw.ActionRow(
            title=_("Backup schijf"),
            subtitle=_("Alleen externe schijven worden getoond"))
        self.drive_row.add_suffix(self.refresh_btn)
        self.drive_row.add_suffix(self.drive_combo)
        self.drive_row.set_sensitive(False)
        group.add(self.drive_row)

        self.backup_folder_row = Adw.ActionRow(
            title=_("Map op backup schijf"),
            subtitle=_("Nog geen schijf geselecteerd"))
        self.backup_folder_btn = Gtk.Button(label=_("Kiezen…"))
        self.backup_folder_btn.add_css_class("flat")
        self.backup_folder_btn.set_valign(Gtk.Align.CENTER)
        self.backup_folder_btn.connect("clicked", self._on_browse_backup_folder)
        self.backup_folder_row.add_suffix(self.backup_folder_btn)
        self.backup_folder_row.set_sensitive(False)
        group.add(self.backup_folder_row)

        self.backup_error = Gtk.Label(label=_("⚠️  Kies een backup schijf om door te gaan"))
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

        title = Gtk.Label(label=_("Duplicaat detectie"))
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.START)
        page.append(title)

        subtitle = Gtk.Label(
            label=_("Pixora vergelijkt nieuwe foto's visueel met je bibliotheek\n"
                    "en markeert visuele kopieën zodat je ze kunt overslaan.")
        )
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.START)
        page.append(subtitle)

        group = Adw.PreferencesGroup()

        self.dup_switch = Gtk.Switch()
        self.dup_switch.set_valign(Gtk.Align.CENTER)
        self.dup_switch.set_active(True)
        dup_row = Adw.ActionRow(
            title=_("Duplicaat-detectie"),
            subtitle=_("Strikte controle op bijna-identieke foto's"),
        )
        dup_row.add_prefix(Gtk.Image.new_from_icon_name("security-high-symbolic"))
        dup_row.add_suffix(self.dup_switch)
        dup_row.set_activatable_widget(self.dup_switch)
        group.add(dup_row)

        info_row = Adw.ActionRow(
            title=_("Hoe het werkt"),
            subtitle=_("Bij een match vraagt Pixora per foto wat je wilt: "
                       "overslaan, toch importeren of beide bewaren."),
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

    def go_next(self, btn):
        page = self.pages[self.current]

        if page == "folder":
            if not self.folder_entry.get_text().strip():
                dialog = Adw.MessageDialog(
                    transient_for=self,
                    heading=_("Geen map gekozen"),
                    body=_("Kies een map waar je foto's opgeslagen worden.")
                )
                dialog.add_response("ok", _("OK"))
                dialog.present()
                return

        if page == "backup":
            if self.backup_switch.get_active():
                if not self.drives or self.drive_combo.get_selected() >= len(self.drives):
                    self.backup_error.set_label(_("⚠️  Kies een backup schijf om door te gaan"))
                    self.backup_error.set_visible(True)
                    return
                if not self.selected_backup_path:
                    self.backup_error.set_label(_("⚠️  Kies ook een map op de backup schijf"))
                    self.backup_error.set_visible(True)
                    return
            self.backup_error.set_visible(False)

        if self.current < len(self.pages) - 1:
            self.current += 1
            self.stack.set_visible_child_name(self.pages[self.current])
            self.back_btn.set_visible(True)
            self.next_btn.set_label(_("Voltooien") if self.current == len(self.pages) - 1 else _("Volgende"))
        else:
            self._save_and_finish()

    def go_back(self, btn):
        if self.current > 0:
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
            self.current -= 1
            self.stack.set_visible_child_name(self.pages[self.current])
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
            self.back_btn.set_visible(self.current > 0)
            self.next_btn.set_label(_("Volgende"))
            if hasattr(self, "backup_error"):
                self.backup_error.set_visible(False)

    def _on_browse_folder(self, btn):
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Kies foto map"))
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
            self.backup_folder_row.set_subtitle(_("Nog geen map gekozen"))
            self.selected_backup_path = None
            self.backup_error.set_visible(False)

    def _on_browse_backup_folder(self, btn):
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Kies map op backup schijf"))

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
                        self.backup_error.set_label(_("⚠️  Kies een map op de backup schijf, niet op je computer"))
                        self.backup_error.set_visible(True)
                        self.selected_backup_path = None
                        self.backup_folder_row.set_subtitle(_("Nog geen map gekozen"))
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
            self.drive_model.append(_("Geen externe schijven gevonden"))
        self.drive_combo.set_sensitive(backup_on)

        return False

    def _logo_path(self):
        base = os.path.dirname(os.path.abspath(__file__))
        for rel in ("../assets/logos/pixora-icon.svg",
                    "assets/logos/pixora-icon.svg"):
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
            "backup_uuid":         self._get_backup_uuid(),
            "backup_path":         self.selected_backup_path,
            "duplicate_threshold": self._get_threshold(),
        }

        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(settings, f, indent=2)

        from main_window import MainWindow
        win = MainWindow(self.app, settings)
        win.present()
        self.close()
