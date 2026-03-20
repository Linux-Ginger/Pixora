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
from gi.repository import Gtk, Adw, GLib

CONFIG_PATH = os.path.expanduser("~/.config/pixora/settings.json")

# Alleen deze bestandssystemen zijn bruikbaar als backup schijf
BACKUP_FSTYPES = {"ext4", "ext3", "ext2", "ntfs", "exfat", "fuseblk", "btrfs", "xfs"}


def get_available_drives():
    """Geef lijst van (uuid, leesbare naam) tuples terug.
    Alleen hotplug schijven met bruikbaar bestandssysteem."""
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

            uuid = device.get("uuid")
            fstype = (device.get("fstype") or "").lower()
            label = (device.get("label") or "").strip()
            size = device.get("size") or ""
            mountpoint = (device.get("mountpoint") or "").strip()

            if uuid and fstype in BACKUP_FSTYPES:
                if label:
                    display = f"💾  {label}  ({size})"
                elif mountpoint:
                    display = f"💾  {mountpoint}  ({size})"
                else:
                    display = f"💾  Externe schijf  ({size})"
                drives.append((uuid, display))

            # Kinderen ook checken
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
        self.set_title("Pixora — Setup")
        self.set_default_size(560, 560)
        self.set_resizable(False)

        self.drives = []
        self.selected_backup_path = None
        self.style_manager = Adw.StyleManager.get_default()
        self.style_manager.connect("notify::dark", self.on_dark_mode_changed)

        # Stack voor pagina's
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        self.stack.set_transition_duration(250)

        self.stack.add_named(self.build_welcome(),   "welcome")
        self.stack.add_named(self.build_folder(),    "folder")
        self.stack.add_named(self.build_structure(), "structure")
        self.stack.add_named(self.build_backup(),    "backup")
        self.stack.add_named(self.build_duplicate(), "duplicate")

        # Hoofd layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.add_css_class("flat")

        self.back_btn = Gtk.Button(label="Terug")
        self.back_btn.connect("clicked", self.go_back)
        self.back_btn.set_visible(False)
        header.pack_start(self.back_btn)

        # Sluitknop
        close_btn = Gtk.Button(icon_name="window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.set_tooltip_text("Sluiten")
        close_btn.connect("clicked", lambda b: self.close())
        header.pack_end(close_btn)

        self.next_btn = Gtk.Button(label="Volgende")
        self.next_btn.add_css_class("suggested-action")
        self.next_btn.connect("clicked", self.go_next)
        header.pack_end(self.next_btn)

        main_box.append(header)
        main_box.append(self.stack)

        self.set_content(main_box)

        self.pages = ["welcome", "folder", "structure", "backup", "duplicate"]
        self.current = 0

    # ── Dark mode ────────────────────────────────────────────────────
    def on_dark_mode_changed(self, manager, _):
        dark = manager.get_dark()
        logo_name = "pixora-logo-dark.png" if dark else "pixora-logo-light.png"
        logo_path = os.path.join(os.path.dirname(__file__), "..", "docs", logo_name)
        if os.path.exists(logo_path) and hasattr(self, "welcome_logo"):
            self.welcome_logo.set_filename(logo_path)

    # ── Pagina: Welkom ──────────────────────────────────────────────
    def build_welcome(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page.set_margin_top(40)
        page.set_margin_bottom(40)
        page.set_margin_start(48)
        page.set_margin_end(48)
        page.set_halign(Gtk.Align.FILL)

        # Logo gecentreerd
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

        title = Gtk.Label(label="Welkom bij Pixora!")
        title.add_css_class("title-1")
        title.set_halign(Gtk.Align.CENTER)
        title.set_hexpand(True)
        page.append(title)

        subtitle = Gtk.Label(
            label="Pixora helpt je foto's en video's importeren\n"
                  "vanaf je iPhone, duplicaten detecteren en\n"
                  "automatisch een backup maken.\n\n"
                  "Deze wizard helpt je Pixora instellen.\n"
                  "Dit duurt maar een paar minuten."
        )
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.CENTER)
        subtitle.set_hexpand(True)
        subtitle.set_justify(Gtk.Justification.CENTER)
        page.append(subtitle)

        return page

    # ── Pagina: Foto map ────────────────────────────────────────────
    def build_folder(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page.set_margin_top(40)
        page.set_margin_bottom(40)
        page.set_margin_start(48)
        page.set_margin_end(48)

        title = Gtk.Label(label="Waar wil je je foto's opslaan?")
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.START)
        page.append(title)

        subtitle = Gtk.Label(
            label="Kies een map op je computer waar Pixora\n"
                  "je foto's en video's naartoe kopieert."
        )
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.START)
        page.append(subtitle)

        folder_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.folder_entry = Gtk.Entry()
        self.folder_entry.set_text(os.path.expanduser("~/Pictures/Pixora"))
        self.folder_entry.set_hexpand(True)
        self.folder_entry.set_placeholder_text("Kies een map...")

        browse_btn = Gtk.Button(label="Bladeren...")
        browse_btn.connect("clicked", self.on_browse_folder)

        folder_box.append(self.folder_entry)
        folder_box.append(browse_btn)
        page.append(folder_box)

        return page

    # ── Pagina: Mapstructuur ────────────────────────────────────────
    def build_structure(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page.set_margin_top(40)
        page.set_margin_bottom(40)
        page.set_margin_start(48)
        page.set_margin_end(48)

        title = Gtk.Label(label="Hoe wil je je foto's organiseren?")
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.START)
        page.append(title)

        group = Adw.PreferencesGroup()

        self.radio_flat = Gtk.CheckButton()
        flat_row = Adw.ActionRow(title="Plat", subtitle="Alles in één map")
        flat_row.add_prefix(Gtk.Image.new_from_icon_name("folder-symbolic"))
        flat_row.add_prefix(self.radio_flat)
        flat_row.set_activatable_widget(self.radio_flat)
        group.add(flat_row)

        self.radio_year = Gtk.CheckButton()
        self.radio_year.set_group(self.radio_flat)
        year_row = Adw.ActionRow(title="Per jaar", subtitle="2024/   2025/")
        year_row.add_prefix(Gtk.Image.new_from_icon_name("folder-open-symbolic"))
        year_row.add_prefix(self.radio_year)
        year_row.set_activatable_widget(self.radio_year)
        group.add(year_row)

        self.radio_month = Gtk.CheckButton()
        self.radio_month.set_group(self.radio_flat)
        self.radio_month.set_active(True)
        month_row = Adw.ActionRow(title="Per jaar/maand", subtitle="2024/2024-03/   2024/2024-04/")
        month_row.add_prefix(Gtk.Image.new_from_icon_name("view-list-symbolic"))
        month_row.add_prefix(self.radio_month)
        month_row.set_activatable_widget(self.radio_month)
        group.add(month_row)

        page.append(group)
        return page

    # ── Pagina: Backup ──────────────────────────────────────────────
    def build_backup(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page.set_margin_top(40)
        page.set_margin_bottom(40)
        page.set_margin_start(48)
        page.set_margin_end(48)

        title = Gtk.Label(label="Automatische backup")
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.START)
        page.append(title)

        subtitle = Gtk.Label(
            label="Pixora kan na elke import automatisch een backup\n"
                  "maken naar een externe USB schijf of HDD."
        )
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.START)
        page.append(subtitle)

        group = Adw.PreferencesGroup()

        # Backup schakelaar
        self.backup_switch = Gtk.Switch()
        self.backup_switch.set_valign(Gtk.Align.CENTER)
        self.backup_switch.connect("notify::active", self.on_backup_toggle)

        backup_row = Adw.ActionRow(
            title="Automatische backup",
            subtitle="Synchroniseert na elke import naar externe schijf"
        )
        backup_row.add_suffix(self.backup_switch)
        backup_row.set_activatable_widget(self.backup_switch)
        group.add(backup_row)

        # Schijf selectie met refresh spinner
        self.drive_model = Gtk.StringList()
        self.drives = []
        self.drive_model.append("Geen externe schijven gevonden")

        self.drive_combo = Gtk.DropDown(model=self.drive_model)
        self.drive_combo.set_sensitive(False)
        self.drive_combo.set_size_request(220, -1)
        self.drive_combo.connect("notify::selected", self.on_drive_selected)

        # Refresh knop met spinner
        self.refresh_stack = Gtk.Stack()
        self.refresh_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.refresh_stack.set_transition_duration(150)

        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        self.refresh_btn.add_css_class("flat")
        self.refresh_btn.set_valign(Gtk.Align.CENTER)
        self.refresh_btn.set_tooltip_text("Vernieuwen")
        self.refresh_btn.connect("clicked", self.on_refresh_drives)

        self.refresh_spinner = Gtk.Spinner()
        self.refresh_spinner.set_size_request(24, 24)

        self.refresh_stack.add_named(self.refresh_btn, "btn")
        self.refresh_stack.add_named(self.refresh_spinner, "spinner")
        self.refresh_stack.set_visible_child_name("btn")

        self.drive_row = Adw.ActionRow(
            title="Backup schijf",
            subtitle="Alleen externe schijven met ext4/ntfs/exfat worden getoond"
        )
        self.drive_row.add_suffix(self.refresh_stack)
        self.drive_row.add_suffix(self.drive_combo)
        self.drive_row.set_sensitive(False)
        group.add(self.drive_row)

        # Map op backup schijf
        self.backup_folder_row = Adw.ActionRow(
            title="Map op backup schijf",
            subtitle="Nog geen schijf geselecteerd"
        )
        self.backup_folder_btn = Gtk.Button(label="Kiezen...")
        self.backup_folder_btn.add_css_class("flat")
        self.backup_folder_btn.set_valign(Gtk.Align.CENTER)
        self.backup_folder_btn.connect("clicked", self.on_browse_backup_folder)
        self.backup_folder_row.add_suffix(self.backup_folder_btn)
        self.backup_folder_row.set_sensitive(False)
        group.add(self.backup_folder_row)

        # Foutmelding als geen schijf gekozen
        self.backup_error = Gtk.Label(label="⚠️  Kies een backup schijf om door te gaan")
        self.backup_error.add_css_class("error")
        self.backup_error.set_halign(Gtk.Align.START)
        self.backup_error.set_visible(False)

        page.append(group)
        page.append(self.backup_error)
        return page

    # ── Pagina: Duplicate detectie ──────────────────────────────────
    def build_duplicate(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page.set_margin_top(40)
        page.set_margin_bottom(40)
        page.set_margin_start(48)
        page.set_margin_end(48)

        title = Gtk.Label(label="Duplicate detectie")
        title.add_css_class("title-2")
        title.set_halign(Gtk.Align.START)
        page.append(title)

        subtitle = Gtk.Label(
            label="Pixora vergelijkt foto's visueel op inhoud.\n"
                  "Stel in hoe streng de detectie moet zijn."
        )
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.START)
        page.append(subtitle)

        group = Adw.PreferencesGroup()

        self.radio_strict = Gtk.CheckButton()
        strict_row = Adw.ActionRow(title="Streng", subtitle="Alleen exact dezelfde foto's")
        strict_row.add_prefix(Gtk.Image.new_from_icon_name("security-high-symbolic"))
        strict_row.add_prefix(self.radio_strict)
        strict_row.set_activatable_widget(self.radio_strict)
        group.add(strict_row)

        self.radio_normal = Gtk.CheckButton()
        self.radio_normal.set_group(self.radio_strict)
        self.radio_normal.set_active(True)
        normal_row = Adw.ActionRow(title="Normaal", subtitle="Bijna identieke foto's worden gedetecteerd")
        normal_row.add_prefix(Gtk.Image.new_from_icon_name("security-medium-symbolic"))
        normal_row.add_prefix(self.radio_normal)
        normal_row.set_activatable_widget(self.radio_normal)
        group.add(normal_row)

        self.radio_loose = Gtk.CheckButton()
        self.radio_loose.set_group(self.radio_strict)
        loose_row = Adw.ActionRow(title="Soepel", subtitle="Ook licht bewerkte foto's worden gedetecteerd")
        loose_row.add_prefix(Gtk.Image.new_from_icon_name("security-low-symbolic"))
        loose_row.add_prefix(self.radio_loose)
        loose_row.set_activatable_widget(self.radio_loose)
        group.add(loose_row)

        page.append(group)
        return page

    # ── Navigatie ───────────────────────────────────────────────────
    def go_next(self, btn):
        # Validatie backup pagina
        if self.pages[self.current] == "backup":
            if self.backup_switch.get_active():
                if not self.drives or self.drive_combo.get_selected() >= len(self.drives):
                    self.backup_error.set_visible(True)
                    return
                if not self.selected_backup_path:
                    self.backup_error.set_label("⚠️  Kies ook een map op de backup schijf")
                    self.backup_error.set_visible(True)
                    return
            self.backup_error.set_visible(False)

        if self.current < len(self.pages) - 1:
            self.current += 1
            self.stack.set_visible_child_name(self.pages[self.current])
            self.back_btn.set_visible(True)
            if self.current == len(self.pages) - 1:
                self.next_btn.set_label("Voltooien")
        else:
            self.save_and_finish()

    def go_back(self, btn):
        if self.current > 0:
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
            self.current -= 1
            self.stack.set_visible_child_name(self.pages[self.current])
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
            if self.current == 0:
                self.back_btn.set_visible(False)
            self.next_btn.set_label("Volgende")
            self.backup_error.set_visible(False)

    # ── Acties ──────────────────────────────────────────────────────
    def on_browse_folder(self, btn):
        dialog = Gtk.FileDialog()
        dialog.set_title("Kies foto map")
        dialog.select_folder(self, None, self.on_folder_selected)

    def on_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                self.folder_entry.set_text(folder.get_path())
        except Exception:
            pass

    def on_backup_toggle(self, switch, _):
        active = switch.get_active()
        self.drive_row.set_sensitive(active)
        self.drive_combo.set_sensitive(active and bool(self.drives))
        if active and not self.drives:
            self.on_refresh_drives(None)

    def on_drive_selected(self, combo, _):
        selected = combo.get_selected()
        if self.drives and selected < len(self.drives):
            self.backup_folder_row.set_sensitive(True)
            self.backup_folder_row.set_subtitle("Nog geen map gekozen")
            self.selected_backup_path = None
            self.backup_error.set_visible(False)

    def on_browse_backup_folder(self, btn):
        dialog = Gtk.FileDialog()
        dialog.set_title("Kies map op backup schijf")
        dialog.select_folder(self, None, self.on_backup_folder_selected)

    def on_backup_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                self.selected_backup_path = folder.get_path()
                self.backup_folder_row.set_subtitle(self.selected_backup_path)
                self.backup_error.set_visible(False)
        except Exception:
            pass

    def on_refresh_drives(self, btn):
        # Spinner tonen
        self.refresh_stack.set_visible_child_name("spinner")
        self.refresh_spinner.start()

        def do_refresh():
            drives = get_available_drives()
            GLib.idle_add(self.update_drives, drives)

        threading.Thread(target=do_refresh, daemon=True).start()

    def update_drives(self, drives):
        # Spinner stoppen
        self.refresh_spinner.stop()
        self.refresh_stack.set_visible_child_name("btn")

        # Model updaten
        while self.drive_model.get_n_items() > 0:
            self.drive_model.remove(0)

        self.drives = drives
        if drives:
            for uuid, label in drives:
                self.drive_model.append(label)
            self.drive_combo.set_sensitive(True)
        else:
            self.drive_model.append("Geen externe schijven gevonden")
            self.drive_combo.set_sensitive(False)

        return False

    def get_structure(self):
        if self.radio_flat.get_active():
            return "flat"
        elif self.radio_year.get_active():
            return "year"
        return "year_month"

    def get_threshold(self):
        if self.radio_strict.get_active():
            return 1
        elif self.radio_normal.get_active():
            return 2
        return 3

    def get_backup_uuid(self):
        if not self.backup_switch.get_active():
            return None
        selected = self.drive_combo.get_selected()
        if self.drives and selected < len(self.drives):
            return self.drives[selected][0]
        return None

    def save_and_finish(self):
        settings = {
            "photo_path":          self.folder_entry.get_text(),
            "structure":           self.get_structure(),
            "backup_uuid":         self.get_backup_uuid(),
            "backup_path":         self.selected_backup_path,
            "duplicate_threshold": self.get_threshold()
        }

        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(settings, f, indent=2)

        from main_window import MainWindow
        win = MainWindow(self.app, settings)
        win.present()
        self.close()