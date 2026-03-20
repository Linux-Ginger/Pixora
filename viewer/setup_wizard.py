#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — setup_wizard.py
#  by LinuxGinger
# ─────────────────────────────────────────────

import os
import json
import subprocess

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

CONFIG_PATH = os.path.expanduser("~/.config/pixora/settings.json")


def get_drive_label(uuid):
    """Probeer een leesbare naam te krijgen voor een schijf via UUID."""
    try:
        result = subprocess.run(
            ["lsblk", "-o", "UUID,LABEL,SIZE,FSTYPE", "-J"],
            capture_output=True, text=True
        )
        import json as _json
        data = _json.loads(result.stdout)
        for device in data.get("blockdevices", []):
            for child in device.get("children", [device]):
                if child.get("uuid") == uuid:
                    label = child.get("label") or ""
                    size = child.get("size") or ""
                    fstype = child.get("fstype") or ""
                    if label:
                        return f"{label} ({size})"
                    else:
                        return f"Schijf {size} {fstype}".strip()
    except Exception:
        pass
    return uuid[:8] + "..."


def get_available_drives():
    """Geef lijst van (uuid, leesbare naam) tuples terug."""
    drives = []
    uuid_dir = "/dev/disk/by-uuid"
    if not os.path.exists(uuid_dir):
        return drives
    try:
        result = subprocess.run(
            ["lsblk", "-o", "UUID,LABEL,SIZE,FSTYPE,MOUNTPOINT", "-J"],
            capture_output=True, text=True
        )
        import json as _json
        data = _json.loads(result.stdout)
        for device in data.get("blockdevices", []):
            children = device.get("children", [device])
            for child in children:
                uuid = child.get("uuid")
                if not uuid:
                    continue
                label = child.get("label") or ""
                size = child.get("size") or ""
                fstype = child.get("fstype") or ""
                mountpoint = child.get("mountpoint") or ""
                if label:
                    display = f"💾  {label}  ({size})"
                elif mountpoint:
                    display = f"💾  {mountpoint}  ({size})"
                else:
                    display = f"💾  Schijf {size} {fstype}".strip()
                drives.append((uuid, display))
    except Exception:
        for uuid in os.listdir(uuid_dir):
            drives.append((uuid, uuid[:8] + "..."))
    return drives


class SetupWizard(Adw.Window):
    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.set_title("Pixora — Setup")
        self.set_default_size(560, 540)
        self.set_resizable(False)

        self.drives = []

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

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.add_css_class("flat")

        self.back_btn = Gtk.Button(label="Terug")
        self.back_btn.connect("clicked", self.go_back)
        self.back_btn.set_visible(False)
        header.pack_start(self.back_btn)

        self.next_btn = Gtk.Button(label="Volgende")
        self.next_btn.add_css_class("suggested-action")
        self.next_btn.connect("clicked", self.go_next)
        header.pack_end(self.next_btn)

        main_box.append(header)
        main_box.append(self.stack)

        self.set_content(main_box)

        self.pages = ["welcome", "folder", "structure", "backup", "duplicate"]
        self.current = 0

    # ── Pagina: Welkom ──────────────────────────────────────────────
    def build_welcome(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page.set_margin_top(40)
        page.set_margin_bottom(40)
        page.set_margin_start(48)
        page.set_margin_end(48)

        # Logo
        style_manager = Adw.StyleManager.get_default()
        dark = style_manager.get_dark()
        logo_name = "pixora-logo-dark.png" if dark else "pixora-logo-light.png"
        logo_path = os.path.join(os.path.dirname(__file__), "..", "docs", logo_name)
        if os.path.exists(logo_path):
            logo = Gtk.Picture.new_for_filename(logo_path)
            logo.set_size_request(260, 64)
            logo.set_content_fit(Gtk.ContentFit.CONTAIN)
            page.append(logo)

        title = Gtk.Label(label="Welkom bij Pixora!")
        title.add_css_class("title-1")
        title.set_halign(Gtk.Align.CENTER)
        page.append(title)

        subtitle = Gtk.Label(
            label="Pixora helpt je foto's en video's importeren\n"
                  "vanaf je iPhone, duplicaten detecteren en\n"
                  "automatisch een backup maken.\n\n"
                  "Deze wizard helpt je Pixora instellen.\nDit duurt maar een paar minuten."
        )
        subtitle.add_css_class("body")
        subtitle.set_halign(Gtk.Align.CENTER)
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
            label="Kies een map op je computer waar Pixora\nje foto's en video's naartoe kopieert."
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

        # Plat
        self.radio_flat = Gtk.CheckButton()
        flat_icon = Gtk.Image.new_from_icon_name("folder-symbolic")
        flat_row = Adw.ActionRow(
            title="Plat",
            subtitle="Alles in één map"
        )
        flat_row.add_prefix(flat_icon)
        flat_row.add_prefix(self.radio_flat)
        flat_row.set_activatable_widget(self.radio_flat)
        group.add(flat_row)

        # Per jaar
        self.radio_year = Gtk.CheckButton()
        self.radio_year.set_group(self.radio_flat)
        year_icon = Gtk.Image.new_from_icon_name("folder-open-symbolic")
        year_row = Adw.ActionRow(
            title="Per jaar",
            subtitle="2024/   2025/"
        )
        year_row.add_prefix(year_icon)
        year_row.add_prefix(self.radio_year)
        year_row.set_activatable_widget(self.radio_year)
        group.add(year_row)

        # Per jaar/maand
        self.radio_month = Gtk.CheckButton()
        self.radio_month.set_group(self.radio_flat)
        self.radio_month.set_active(True)
        month_icon = Gtk.Image.new_from_icon_name("view-list-symbolic")
        month_row = Adw.ActionRow(
            title="Per jaar/maand",
            subtitle="2024/2024-03/   2024/2024-04/"
        )
        month_row.add_prefix(month_icon)
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

        # Schijf selectie
        self.drive_model = Gtk.StringList()
        self.drives = get_available_drives()

        if self.drives:
            for uuid, label in self.drives:
                self.drive_model.append(label)
        else:
            self.drive_model.append("Geen schijven gevonden — sluit je schijf aan")

        self.drive_combo = Gtk.DropDown(model=self.drive_model)
        self.drive_combo.set_sensitive(False)
        self.drive_combo.set_size_request(220, -1)

        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.add_css_class("flat")
        refresh_btn.set_valign(Gtk.Align.CENTER)
        refresh_btn.set_tooltip_text("Vernieuwen")
        refresh_btn.connect("clicked", self.on_refresh_drives)

        self.drive_row = Adw.ActionRow(
            title="Backup schijf",
            subtitle="Selecteer je externe USB schijf of HDD"
        )
        self.drive_row.add_suffix(refresh_btn)
        self.drive_row.add_suffix(self.drive_combo)
        self.drive_row.set_sensitive(False)
        group.add(self.drive_row)

        page.append(group)
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
        strict_icon = Gtk.Image.new_from_icon_name("security-high-symbolic")
        strict_row = Adw.ActionRow(
            title="Streng",
            subtitle="Alleen exact dezelfde foto's"
        )
        strict_row.add_prefix(strict_icon)
        strict_row.add_prefix(self.radio_strict)
        strict_row.set_activatable_widget(self.radio_strict)
        group.add(strict_row)

        self.radio_normal = Gtk.CheckButton()
        self.radio_normal.set_group(self.radio_strict)
        self.radio_normal.set_active(True)
        normal_icon = Gtk.Image.new_from_icon_name("security-medium-symbolic")
        normal_row = Adw.ActionRow(
            title="Normaal",
            subtitle="Bijna identieke foto's worden gedetecteerd"
        )
        normal_row.add_prefix(normal_icon)
        normal_row.add_prefix(self.radio_normal)
        normal_row.set_activatable_widget(self.radio_normal)
        group.add(normal_row)

        self.radio_loose = Gtk.CheckButton()
        self.radio_loose.set_group(self.radio_strict)
        loose_icon = Gtk.Image.new_from_icon_name("security-low-symbolic")
        loose_row = Adw.ActionRow(
            title="Soepel",
            subtitle="Ook licht bewerkte foto's worden gedetecteerd"
        )
        loose_row.add_prefix(loose_icon)
        loose_row.add_prefix(self.radio_loose)
        loose_row.set_activatable_widget(self.radio_loose)
        group.add(loose_row)

        page.append(group)
        return page

    # ── Navigatie ───────────────────────────────────────────────────
    def go_next(self, btn):
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
        self.drive_combo.set_sensitive(active)

    def on_refresh_drives(self, btn):
        while self.drive_model.get_n_items() > 0:
            self.drive_model.remove(0)
        self.drives = get_available_drives()
        if self.drives:
            for uuid, label in self.drives:
                self.drive_model.append(label)
        else:
            self.drive_model.append("Geen schijven gevonden — sluit je schijf aan")

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
            "duplicate_threshold": self.get_threshold()
        }

        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(settings, f, indent=2)

        from main_window import MainWindow
        win = MainWindow(self.app, settings)
        win.present()
        self.close()