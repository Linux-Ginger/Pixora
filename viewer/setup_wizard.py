#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — setup_wizard.py
#  by LinuxGinger
# ─────────────────────────────────────────────

import os
import json
import subprocess
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio

CONFIG_PATH = os.path.expanduser("~/.config/pixora/settings.json")

BACKUP_FSTYPES = {"ext4", "ext3", "ext2", "ntfs", "exfat", "fuseblk", "btrfs", "xfs", "vfat"}

# ── Vertalingen ─────────────────────────────────────────────────────────────

TR = {
    "nl": {
        "next":             "Volgende",
        "back":             "Terug",
        "finish":           "Voltooien",
        "close":            "Sluiten",
        "window_title":     "Pixora — Instellen",

        # Taal pagina
        "lang_title":       "Kies je taal",
        "lang_subtitle":    "Je kunt dit later wijzigen in de instellingen.",
        "lang_nl":          "Nederlands",
        "lang_en":          "English",

        # Welkom pagina
        "welcome_title":    "Welkom bij Pixora!",
        "welcome_body":     (
            "Pixora helpt je foto's en video's importeren\n"
            "vanaf je iPhone, duplicaten detecteren en\n"
            "automatisch een backup maken.\n\n"
            "Deze wizard helpt je Pixora instellen.\n"
            "Dit duurt maar een paar minuten."
        ),

        # Foto map pagina
        "folder_title":     "Waar wil je je foto's opslaan?",
        "folder_subtitle":  "Kies een map op je computer waar Pixora\nje foto's en video's naartoe kopieert.",
        "folder_browse":    "Bladeren…",
        "folder_dialog":    "Kies foto map",
        "folder_placeholder": "Kies een map…",
        "folder_error_heading": "Geen map gekozen",
        "folder_error_body":    "Kies een map waar je foto's opgeslagen worden.",
        "folder_error_ok":      "OK",

        # Backup pagina
        "backup_title":     "Automatische backup",
        "backup_subtitle":  "Pixora kan na elke import automatisch een backup\nmaken naar een externe USB schijf of HDD.",
        "backup_row_title": "Automatische backup",
        "backup_row_sub":   "Synchroniseert na elke import naar externe schijf",
        "backup_drive_title": "Backup schijf",
        "backup_drive_sub":   "Alleen externe schijven met ext4/ntfs/exfat worden getoond",
        "backup_folder_title": "Map op backup schijf",
        "backup_folder_none":  "Nog geen schijf geselecteerd",
        "backup_folder_choose": "Kiezen…",
        "backup_folder_dialog": "Kies map op backup schijf",
        "backup_no_drives":     "Geen externe schijven gevonden",
        "backup_external_disk": "Externe schijf",
        "backup_err_drive":     "⚠️  Kies een backup schijf om door te gaan",
        "backup_err_folder":    "⚠️  Kies ook een map op de backup schijf",
        "backup_err_wrong":     "⚠️  Kies een map op de backup schijf, niet op je computer",
        "backup_refresh_tip":   "Vernieuwen",
        "backup_folder_none2":  "Nog geen map gekozen",

        # Duplicaten pagina
        "dup_title":        "Duplicaat detectie",
        "dup_subtitle":     "Pixora vergelijkt foto's visueel op inhoud.\nStel in hoe streng de detectie moet zijn.",
        "dup_strict":       "Streng",
        "dup_strict_sub":   "Alleen exact dezelfde foto's",
        "dup_normal":       "Normaal",
        "dup_normal_sub":   "Bijna identieke foto's worden gedetecteerd",
        "dup_loose":        "Soepel",
        "dup_loose_sub":    "Ook licht bewerkte foto's worden gedetecteerd",
    },
    "en": {
        "next":             "Next",
        "back":             "Back",
        "finish":           "Finish",
        "close":            "Close",
        "window_title":     "Pixora — Setup",

        # Language page
        "lang_title":       "Choose your language",
        "lang_subtitle":    "You can change this later in settings.",
        "lang_nl":          "Nederlands",
        "lang_en":          "English",

        # Welcome page
        "welcome_title":    "Welcome to Pixora!",
        "welcome_body":     (
            "Pixora helps you import photos and videos\n"
            "from your iPhone, detect duplicates and\n"
            "automatically create backups.\n\n"
            "This wizard will help you set up Pixora.\n"
            "It only takes a few minutes."
        ),

        # Photo folder page
        "folder_title":     "Where do you want to save your photos?",
        "folder_subtitle":  "Choose a folder on your computer where Pixora\nwill copy your photos and videos.",
        "folder_browse":    "Browse…",
        "folder_dialog":    "Choose photo folder",
        "folder_placeholder": "Choose a folder…",
        "folder_error_heading": "No folder chosen",
        "folder_error_body":    "Please choose a folder where your photos will be saved.",
        "folder_error_ok":      "OK",

        # Backup page
        "backup_title":     "Automatic backup",
        "backup_subtitle":  "Pixora can automatically create a backup\nto an external USB drive or HDD after each import.",
        "backup_row_title": "Automatic backup",
        "backup_row_sub":   "Syncs to external drive after each import",
        "backup_drive_title": "Backup drive",
        "backup_drive_sub":   "Only external drives with ext4/ntfs/exfat are shown",
        "backup_folder_title": "Folder on backup drive",
        "backup_folder_none":  "No drive selected yet",
        "backup_folder_choose": "Choose…",
        "backup_folder_dialog": "Choose folder on backup drive",
        "backup_no_drives":     "No external drives found",
        "backup_external_disk": "External drive",
        "backup_err_drive":     "⚠️  Please choose a backup drive to continue",
        "backup_err_folder":    "⚠️  Please also choose a folder on the backup drive",
        "backup_err_wrong":     "⚠️  Choose a folder on the backup drive, not on your computer",
        "backup_refresh_tip":   "Refresh",
        "backup_folder_none2":  "No folder chosen yet",

        # Duplicates page
        "dup_title":        "Duplicate detection",
        "dup_subtitle":     "Pixora compares photos visually by content.\nSet how strict the detection should be.",
        "dup_strict":       "Strict",
        "dup_strict_sub":   "Only exactly identical photos",
        "dup_normal":       "Normal",
        "dup_normal_sub":   "Nearly identical photos are detected",
        "dup_loose":        "Loose",
        "dup_loose_sub":    "Even lightly edited photos are detected",
    },
}


def get_available_drives(lang="nl"):
    t = TR[lang]
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
            uuid     = device.get("uuid")
            fstype   = (device.get("fstype") or "").lower()
            label    = (device.get("label") or "").strip()
            size     = device.get("size") or ""
            mountpoint = (device.get("mountpoint") or "").strip()

            if uuid and fstype in BACKUP_FSTYPES:
                if label:
                    display = f"💾  {label}  ({size})"
                elif mountpoint:
                    display = f"💾  {mountpoint}  ({size})"
                else:
                    display = f"💾  {t['backup_external_disk']}  ({size})"
                drives.append((uuid, display))

            for child in device.get("children", []):
                child["hotplug"] = hotplug
                process_device(child)

        for device in data.get("blockdevices", []):
            process_device(device)

    except Exception as e:
        print(f"Drive detectie fout: {e}")

    return drives


class SetupWizard(Adw.Window):
    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.lang = "nl"
        self.drives = []
        self.selected_backup_path = None
        self.style_manager = Adw.StyleManager.get_default()
        self.style_manager.connect("notify::dark", self._on_dark_mode_changed)

        self.set_default_size(560, 580)
        self.set_resizable(False)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        self.stack.set_transition_duration(250)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self.header = Adw.HeaderBar()
        self.header.set_show_end_title_buttons(False)
        self.header.add_css_class("flat")

        self.back_btn = Gtk.Button()
        self.back_btn.connect("clicked", self.go_back)
        self.back_btn.set_visible(False)
        self.header.pack_start(self.back_btn)

        close_btn = Gtk.Button(icon_name="window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.connect("clicked", lambda b: self.close())
        self.header.pack_end(close_btn)

        self.next_btn = Gtk.Button()
        self.next_btn.add_css_class("suggested-action")
        self.next_btn.connect("clicked", self.go_next)
        self.header.pack_end(self.next_btn)

        main_box.append(self.header)
        main_box.append(self.stack)
        self.set_content(main_box)

        # Taal pagina altijd eerst bouwen
        self.stack.add_named(self._build_language_page(), "language")
        self.pages = ["language"]
        self.current = 0
        self._apply_lang_to_buttons()
        self.set_title(TR[self.lang]["window_title"])

    # ── Taal toepassen ───────────────────────────────────────────────

    def _apply_lang_to_buttons(self):
        t = TR[self.lang]
        self.back_btn.set_label(t["back"])
        is_last = self.current == len(self.pages) - 1
        self.next_btn.set_label(t["finish"] if is_last else t["next"])

    def _build_remaining_pages(self):
        """Bouw de overige pagina's na taal-keuze."""
        for name in ["welcome", "folder", "backup", "duplicate"]:
            if self.stack.get_child_by_name(name) is None:
                builders = {
                    "welcome":   self._build_welcome,
                    "folder":    self._build_folder,
                    "backup":    self._build_backup,
                    "duplicate": self._build_duplicate,
                }
                self.stack.add_named(builders[name](), name)

        self.pages = ["language", "welcome", "folder", "backup", "duplicate"]

    # ── Dark mode ────────────────────────────────────────────────────

    def _on_dark_mode_changed(self, manager, _):
        dark = manager.get_dark()
        logo_name = "pixora-logo-dark.png" if dark else "pixora-logo-light.png"
        logo_path = os.path.join(os.path.dirname(__file__), "..", "docs", logo_name)
        if os.path.exists(logo_path) and hasattr(self, "welcome_logo"):
            self.welcome_logo.set_filename(logo_path)

    # ── Pagina: Taal ─────────────────────────────────────────────────

    def _build_language_page(self):
        t = TR[self.lang]
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        page.set_margin_top(48)
        page.set_margin_bottom(48)
        page.set_margin_start(48)
        page.set_margin_end(48)
        page.set_valign(Gtk.Align.CENTER)

        title = Gtk.Label(label=t["lang_title"])
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.CENTER)
        page.append(title)

        subtitle = Gtk.Label(label=t["lang_subtitle"])
        subtitle.add_css_class("dim-label")
        subtitle.set_halign(Gtk.Align.CENTER)
        page.append(subtitle)

        group = Adw.PreferencesGroup()

        self.radio_nl = Gtk.CheckButton()
        self.radio_nl.set_active(True)
        nl_row = Adw.ActionRow(title="🇳🇱  " + t["lang_nl"])
        nl_row.add_prefix(self.radio_nl)
        nl_row.set_activatable_widget(self.radio_nl)
        group.add(nl_row)

        self.radio_en = Gtk.CheckButton()
        self.radio_en.set_group(self.radio_nl)
        en_row = Adw.ActionRow(title="🇬🇧  " + t["lang_en"])
        en_row.add_prefix(self.radio_en)
        en_row.set_activatable_widget(self.radio_en)
        group.add(en_row)

        page.append(group)
        return page

    # ── Pagina: Welkom ───────────────────────────────────────────────

    def _build_welcome(self):
        t = TR[self.lang]
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page.set_margin_top(40)
        page.set_margin_bottom(40)
        page.set_margin_start(48)
        page.set_margin_end(48)
        page.set_halign(Gtk.Align.FILL)

        dark = self.style_manager.get_dark()
        logo_name = "pixora-logo-dark.png" if dark else "pixora-logo-light.png"
        logo_path = os.path.join(os.path.dirname(__file__), "..", "docs", logo_name)

        self.welcome_logo = Gtk.Picture()
        if os.path.exists(logo_path):
            self.welcome_logo.set_filename(logo_path)
        self.welcome_logo.set_size_request(260, 64)
        self.welcome_logo.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.welcome_logo.set_halign(Gtk.Align.CENTER)
        self.welcome_logo.set_hexpand(True)
        page.append(self.welcome_logo)

        title = Gtk.Label(label=t["welcome_title"])
        title.add_css_class("title-1")
        title.set_halign(Gtk.Align.CENTER)
        title.set_hexpand(True)
        page.append(title)

        subtitle = Gtk.Label(label=t["welcome_body"])
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.CENTER)
        subtitle.set_hexpand(True)
        subtitle.set_justify(Gtk.Justification.CENTER)
        page.append(subtitle)

        return page

    # ── Pagina: Foto map ─────────────────────────────────────────────

    def _build_folder(self):
        t = TR[self.lang]
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page.set_margin_top(40)
        page.set_margin_bottom(40)
        page.set_margin_start(48)
        page.set_margin_end(48)

        title = Gtk.Label(label=t["folder_title"])
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.START)
        page.append(title)

        subtitle = Gtk.Label(label=t["folder_subtitle"])
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.START)
        page.append(subtitle)

        folder_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.folder_entry = Gtk.Entry()
        self.folder_entry.set_placeholder_text(t["folder_placeholder"])
        self.folder_entry.set_hexpand(True)

        browse_btn = Gtk.Button(label=t["folder_browse"])
        browse_btn.connect("clicked", self._on_browse_folder)

        folder_box.append(self.folder_entry)
        folder_box.append(browse_btn)
        page.append(folder_box)

        return page

    # ── Pagina: Backup ───────────────────────────────────────────────

    def _build_backup(self):
        t = TR[self.lang]
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page.set_margin_top(40)
        page.set_margin_bottom(40)
        page.set_margin_start(48)
        page.set_margin_end(48)

        title = Gtk.Label(label=t["backup_title"])
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.START)
        page.append(title)

        subtitle = Gtk.Label(label=t["backup_subtitle"])
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.START)
        page.append(subtitle)

        group = Adw.PreferencesGroup()

        self.backup_switch = Gtk.Switch()
        self.backup_switch.set_valign(Gtk.Align.CENTER)
        self.backup_switch.connect("notify::active", self._on_backup_toggle)

        backup_row = Adw.ActionRow(title=t["backup_row_title"], subtitle=t["backup_row_sub"])
        backup_row.add_suffix(self.backup_switch)
        backup_row.set_activatable_widget(self.backup_switch)
        group.add(backup_row)

        self.drive_model = Gtk.StringList()
        self.drive_model.append(t["backup_no_drives"])

        self.drive_combo = Gtk.DropDown(model=self.drive_model)
        self.drive_combo.set_sensitive(False)
        self.drive_combo.set_size_request(220, -1)
        self.drive_combo.connect("notify::selected", self._on_drive_selected)

        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        self.refresh_btn.add_css_class("flat")
        self.refresh_btn.set_valign(Gtk.Align.CENTER)
        self.refresh_btn.set_tooltip_text(t["backup_refresh_tip"])
        self.refresh_btn.connect("clicked", self._on_refresh_drives)

        self.drive_row = Adw.ActionRow(
            title=t["backup_drive_title"],
            subtitle=t["backup_drive_sub"]
        )
        self.drive_row.add_suffix(self.refresh_btn)
        self.drive_row.add_suffix(self.drive_combo)
        self.drive_row.set_sensitive(False)
        group.add(self.drive_row)

        self.backup_folder_row = Adw.ActionRow(
            title=t["backup_folder_title"],
            subtitle=t["backup_folder_none"]
        )
        self.backup_folder_btn = Gtk.Button(label=t["backup_folder_choose"])
        self.backup_folder_btn.add_css_class("flat")
        self.backup_folder_btn.set_valign(Gtk.Align.CENTER)
        self.backup_folder_btn.connect("clicked", self._on_browse_backup_folder)
        self.backup_folder_row.add_suffix(self.backup_folder_btn)
        self.backup_folder_row.set_sensitive(False)
        group.add(self.backup_folder_row)

        self.backup_error = Gtk.Label(label=t["backup_err_drive"])
        self.backup_error.add_css_class("error")
        self.backup_error.set_halign(Gtk.Align.START)
        self.backup_error.set_visible(False)

        page.append(group)
        page.append(self.backup_error)
        return page

    # ── Pagina: Duplicaten ───────────────────────────────────────────

    def _build_duplicate(self):
        t = TR[self.lang]
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page.set_margin_top(40)
        page.set_margin_bottom(40)
        page.set_margin_start(48)
        page.set_margin_end(48)

        title = Gtk.Label(label=t["dup_title"])
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.START)
        page.append(title)

        subtitle = Gtk.Label(label=t["dup_subtitle"])
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.START)
        page.append(subtitle)

        group = Adw.PreferencesGroup()

        self.radio_strict = Gtk.CheckButton()
        strict_row = Adw.ActionRow(title=t["dup_strict"], subtitle=t["dup_strict_sub"])
        strict_row.add_prefix(Gtk.Image.new_from_icon_name("security-high-symbolic"))
        strict_row.add_prefix(self.radio_strict)
        strict_row.set_activatable_widget(self.radio_strict)
        group.add(strict_row)

        self.radio_normal = Gtk.CheckButton()
        self.radio_normal.set_group(self.radio_strict)
        self.radio_normal.set_active(True)
        normal_row = Adw.ActionRow(title=t["dup_normal"], subtitle=t["dup_normal_sub"])
        normal_row.add_prefix(Gtk.Image.new_from_icon_name("security-medium-symbolic"))
        normal_row.add_prefix(self.radio_normal)
        normal_row.set_activatable_widget(self.radio_normal)
        group.add(normal_row)

        self.radio_loose = Gtk.CheckButton()
        self.radio_loose.set_group(self.radio_strict)
        loose_row = Adw.ActionRow(title=t["dup_loose"], subtitle=t["dup_loose_sub"])
        loose_row.add_prefix(Gtk.Image.new_from_icon_name("security-low-symbolic"))
        loose_row.add_prefix(self.radio_loose)
        loose_row.set_activatable_widget(self.radio_loose)
        group.add(loose_row)

        page.append(group)
        return page

    # ── Navigatie ────────────────────────────────────────────────────

    def go_next(self, btn):
        page_name = self.pages[self.current]
        t = TR[self.lang]

        # Taal pagina: pas taal toe en bouw rest van de wizard
        if page_name == "language":
            self.lang = "en" if self.radio_en.get_active() else "nl"
            self.set_title(TR[self.lang]["window_title"])
            self._build_remaining_pages()

        # Validatie foto map
        if page_name == "folder":
            if not self.folder_entry.get_text().strip():
                dialog = Adw.MessageDialog(
                    transient_for=self,
                    heading=t["folder_error_heading"],
                    body=t["folder_error_body"]
                )
                dialog.add_response("ok", t["folder_error_ok"])
                dialog.present()
                return

        # Validatie backup
        if page_name == "backup":
            if self.backup_switch.get_active():
                if not self.drives or self.drive_combo.get_selected() >= len(self.drives):
                    self.backup_error.set_label(t["backup_err_drive"])
                    self.backup_error.set_visible(True)
                    return
                if not self.selected_backup_path:
                    self.backup_error.set_label(t["backup_err_folder"])
                    self.backup_error.set_visible(True)
                    return
            self.backup_error.set_visible(False)

        if self.current < len(self.pages) - 1:
            self.current += 1
            self.stack.set_visible_child_name(self.pages[self.current])
            self.back_btn.set_visible(True)
            self._apply_lang_to_buttons()
        else:
            self._save_and_finish()

    def go_back(self, btn):
        if self.current > 0:
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
            self.current -= 1
            self.stack.set_visible_child_name(self.pages[self.current])
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
            if self.current == 0:
                self.back_btn.set_visible(False)
            self._apply_lang_to_buttons()
            if hasattr(self, "backup_error"):
                self.backup_error.set_visible(False)

    # ── Acties ───────────────────────────────────────────────────────

    def _on_browse_folder(self, btn):
        t = TR[self.lang]
        dialog = Gtk.FileDialog()
        dialog.set_title(t["folder_dialog"])
        dialog.select_folder(self, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                self.folder_entry.set_text(folder.get_path())
        except Exception:
            pass

    def _on_backup_toggle(self, switch, _):
        active = switch.get_active()
        self.drive_row.set_sensitive(active)
        self.drive_combo.set_sensitive(active and bool(self.drives))
        if active and not self.drives:
            self._on_refresh_drives(None)

    def _on_drive_selected(self, combo, _):
        selected = combo.get_selected()
        if self.drives and selected < len(self.drives):
            self.backup_folder_row.set_sensitive(True)
            self.backup_folder_row.set_subtitle(TR[self.lang]["backup_folder_none2"])
            self.selected_backup_path = None
            self.backup_error.set_visible(False)

    def _on_browse_backup_folder(self, btn):
        t = TR[self.lang]
        dialog = Gtk.FileDialog()
        dialog.set_title(t["backup_folder_dialog"])

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
        t = TR[self.lang]
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                chosen_path = folder.get_path()
                selected = self.drive_combo.get_selected()
                if self.drives and selected < len(self.drives):
                    uuid = self.drives[selected][0]
                    mountpoint = self._get_mountpoint_for_uuid(uuid)
                    if mountpoint and not chosen_path.startswith(mountpoint):
                        self.backup_error.set_label(t["backup_err_wrong"])
                        self.backup_error.set_visible(True)
                        self.selected_backup_path = None
                        self.backup_folder_row.set_subtitle(t["backup_folder_none2"])
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
            drives = get_available_drives(self.lang)
            GLib.idle_add(self._update_drives, drives)

        threading.Thread(target=do_refresh, daemon=True).start()

    def _update_drives(self, drives):
        t = TR[self.lang]
        self.refresh_btn.set_icon_name("view-refresh-symbolic")
        self.refresh_btn.set_sensitive(True)

        while self.drive_model.get_n_items() > 0:
            self.drive_model.remove(0)

        self.drives = drives
        if drives:
            for uuid, label in drives:
                self.drive_model.append(label)
            self.drive_combo.set_sensitive(True)
        else:
            self.drive_model.append(t["backup_no_drives"])
            self.drive_combo.set_sensitive(False)

        return False

    # ── Helpers ──────────────────────────────────────────────────────

    def _get_threshold(self):
        if self.radio_strict.get_active():
            return 1
        elif self.radio_normal.get_active():
            return 2
        return 3

    def _get_backup_uuid(self):
        if not self.backup_switch.get_active():
            return None
        selected = self.drive_combo.get_selected()
        if self.drives and selected < len(self.drives):
            return self.drives[selected][0]
        return None

    def _save_and_finish(self):
        settings = {
            "language":            self.lang,
            "photo_path":          self.folder_entry.get_text(),
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
