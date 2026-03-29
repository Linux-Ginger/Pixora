#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — installer.py
#  Grafische installer (GTK4/Adwaita)
#  by LinuxGinger
# ─────────────────────────────────────────────

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

import os
import sys
import json
import subprocess
import threading
import urllib.request
from pathlib import Path

INSTALL_DIR  = Path.home() / ".local" / "share" / "pixora"
ICON_PATH    = Path(__file__).parent / "docs" / "pixora-icon.svg"
BIN_DIR      = Path.home() / ".local" / "bin"
DESKTOP_DIR  = Path.home() / ".local" / "share" / "applications"
REPO_URL     = "https://github.com/Linux-Ginger/Pixora.git"
RELEASES_API = "https://api.github.com/repos/Linux-Ginger/Pixora/releases"

PHASES = [
    ("Downloaden", [
        ("Pixora bestanden downloaden", "clone"),
    ]),
    ("Installeren", [
        ("Systeem packages installeren", "apt"),
        ("Python packages installeren",  "pip"),
        ("Launcher aanmaken",            "desktop"),
    ]),
    ("Starten", [
        ("Services activeren",           "services"),
        ("Pixora starten",               "launch"),
    ]),
]

ALL_STEPS = [(label, key) for _, steps in PHASES for label, key in steps]


class InstallerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Pixora Installer")
        self.set_default_size(460, 360)
        self.set_resizable(False)

        self.selected_version = None   # None = main/latest

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        toolbar.add_top_bar(header)

        # Twee schermen: versie-kiezer en installatie-voortgang
        self.main_stack = Gtk.Stack()
        self.main_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.main_stack.set_transition_duration(200)
        self.main_stack.add_named(self._build_select_page(), "select")
        self.main_stack.add_named(self._build_install_page(), "install")

        toolbar.set_content(self.main_stack)
        self.set_content(toolbar)

        # Releases ophalen op achtergrond
        threading.Thread(target=self._fetch_releases, daemon=True).start()

    # ── Versie-kiezer pagina ───────────────────────────────────────────

    def _build_select_page(self):
        install_dir = Path.home() / ".local" / "share" / "pixora"
        already_installed = (install_dir / ".git").exists()
        version_file = install_dir / "version.txt"
        current_version = version_file.read_text().strip() if already_installed and version_file.exists() else None
        self._already_installed = already_installed
        self._local_version = current_version

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroll.set_child(page)

        # Logo / titel
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        top.set_margin_top(20)
        top.set_margin_bottom(16)
        top.set_halign(Gtk.Align.CENTER)

        top.append(self._make_logo(48))

        title = Gtk.Label(label="Pixora")
        title.add_css_class("title-1")
        top.append(title)

        sub = Gtk.Label(label="door LinuxGinger")
        sub.add_css_class("dim-label")
        top.append(sub)

        page.append(top)

        # Versie selectie
        ver_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        ver_box.set_margin_start(24)
        ver_box.set_margin_end(24)
        ver_box.set_margin_top(24)

        ver_lbl = Gtk.Label(label="Versie")
        ver_lbl.add_css_class("heading")
        ver_lbl.set_halign(Gtk.Align.START)
        ver_box.append(ver_lbl)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")

        if already_installed:
            self.installed_row = Adw.ActionRow(
                title="Pixora is al geïnstalleerd",
                subtitle=current_version if current_version else ""
            )
            self.installed_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            self.installed_icon.add_css_class("success")
            self.installed_row.add_prefix(self.installed_icon)
            listbox.append(self.installed_row)

        self.version_model = Gtk.StringList()
        self.version_model.append("Nieuwste versie")

        self.version_combo = Gtk.DropDown(model=self.version_model)
        self.version_combo.set_size_request(220, -1)
        self.version_combo.set_valign(Gtk.Align.CENTER)

        ver_row = Adw.ActionRow(
            title="Versie kiezen",
            subtitle="Selecteer welke versie je wilt installeren"
        )
        ver_row.add_suffix(self.version_combo)
        listbox.append(ver_row)

        ver_box.append(listbox)
        page.append(ver_box)

        # Installeren / bijwerken knop
        btn_box = Gtk.Box()
        btn_box.set_margin_start(24)
        btn_box.set_margin_end(24)
        btn_box.set_margin_top(8)
        btn_box.set_margin_bottom(20)

        btn_label = "Bijwerken" if already_installed else "Installeren"  # bijgewerkt door _fetch_releases
        self.install_btn = Gtk.Button(label=btn_label)
        self.install_btn.add_css_class("suggested-action")
        self.install_btn.add_css_class("pill")
        self.install_btn.set_hexpand(True)
        self.install_btn.set_size_request(-1, 48)
        self.install_btn.connect("clicked", self._on_install_clicked)
        btn_box.append(self.install_btn)

        page.append(btn_box)
        return scroll

    # ── Installatie-voortgang pagina ───────────────────────────────────

    def _build_install_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.set_vexpand(True)

        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        top.set_margin_top(16)
        top.set_margin_bottom(12)
        top.set_halign(Gtk.Align.CENTER)

        top.append(self._make_logo(40))

        title = Gtk.Label(label="Pixora")
        title.add_css_class("title-1")
        top.append(title)

        sub = Gtk.Label(label="door LinuxGinger")
        sub.add_css_class("dim-label")
        top.append(sub)

        page.append(top)

        self.step_rows = {}

        phases_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        phases_box.set_margin_start(24)
        phases_box.set_margin_end(24)

        for phase_label, steps in PHASES:
            phase_lbl = Gtk.Label(label=phase_label)
            phase_lbl.add_css_class("heading")
            phase_lbl.set_halign(Gtk.Align.START)
            phase_lbl.set_margin_top(4)
            phases_box.append(phase_lbl)

            listbox = Gtk.ListBox()
            listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            listbox.add_css_class("boxed-list")

            for step_label, key in steps:
                row = Adw.ActionRow()
                row.set_title(step_label)

                spinner = Gtk.Spinner()
                spinner.set_size_request(20, 20)

                check = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
                check.set_pixel_size(16)

                stack = Gtk.Stack()
                stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
                stack.set_transition_duration(150)
                stack.add_named(Gtk.Box(), "empty")
                stack.add_named(spinner,   "spinner")
                stack.add_named(check,     "check")
                stack.set_visible_child_name("empty")

                row.add_suffix(stack)
                listbox.append(row)
                self.step_rows[key] = (row, stack, spinner, check)

            phases_box.append(listbox)

        page.append(phases_box)

        self.status_lbl = Gtk.Label(label="")
        self.status_lbl.add_css_class("dim-label")
        self.status_lbl.set_margin_top(16)
        self.status_lbl.set_margin_bottom(8)
        page.append(self.status_lbl)

        self.progress = Gtk.ProgressBar()
        self.progress.set_margin_start(24)
        self.progress.set_margin_end(24)
        self.progress.set_margin_bottom(16)
        page.append(self.progress)

        return page

    # ── Releases ophalen ──────────────────────────────────────────────

    def _make_logo(self, size):
        if ICON_PATH.exists():
            pic = Gtk.Picture.new_for_filename(str(ICON_PATH))
            pic.set_size_request(size, size)
            pic.set_content_fit(Gtk.ContentFit.CONTAIN)
            pic.set_halign(Gtk.Align.CENTER)
            return pic
        img = Gtk.Image.new_from_icon_name("applications-graphics-symbolic")
        img.set_pixel_size(size)
        return img

    def _fetch_releases(self):
        tags = []
        remote_version = None
        try:
            req = urllib.request.Request(
                RELEASES_API,
                headers={"User-Agent": "Pixora-Installer"}
            )
            with urllib.request.urlopen(req, timeout=6) as r:
                releases = json.loads(r.read())
            tags = [rel["tag_name"] for rel in releases if not rel.get("draft")]
        except Exception:
            pass

        try:
            req = urllib.request.Request(
                "https://raw.githubusercontent.com/Linux-Ginger/Pixora/refs/heads/main/version.txt",
                headers={"User-Agent": "Pixora-Installer"}
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                remote_version = r.read().decode().strip()
        except Exception:
            pass

        GLib.idle_add(self._update_version_list, tags)
        if self._already_installed and remote_version:
            GLib.idle_add(self._update_install_status, remote_version)

    def _update_install_status(self, remote_version):
        local = self._local_version or ""
        if local == remote_version:
            self.installed_row.set_title("Pixora is al geïnstalleerd")
            self.installed_row.set_subtitle(f"Versie {local} — up to date")
            self.install_btn.set_label("Opnieuw installeren")
            self.installed_icon.set_from_icon_name("emblem-ok-symbolic")
            self.installed_icon.remove_css_class("warning")
            self.installed_icon.add_css_class("success")
        else:
            self.installed_row.set_title("Update beschikbaar")
            self.installed_row.set_subtitle(f"Update beschikbaar: {local} → {remote_version}")
            self.install_btn.set_label("Bijwerken")
            self.installed_icon.set_from_icon_name("software-update-urgent-symbolic")
            self.installed_icon.remove_css_class("success")
            self.installed_icon.add_css_class("warning")
        return False

    def _update_version_list(self, tags):
        while self.version_model.get_n_items() > 0:
            self.version_model.remove(0)

        self.version_model.append("Nieuwste versie")
        for tag in tags:
            self.version_model.append(tag)

        self.version_combo.set_selected(0)
        return False

    # ── Install starten ───────────────────────────────────────────────

    def _on_install_clicked(self, btn):
        selected = self.version_combo.get_selected()
        if selected == 0:
            self.selected_version = None   # clone main
        else:
            label = self.version_model.get_string(selected)
            self.selected_version = label

        self.main_stack.set_visible_child_name("install")
        GLib.timeout_add(300, self._start_install)

    def _start_install(self):
        threading.Thread(target=self._run_install, daemon=True).start()
        return False

    def _set_step_active(self, key, label):
        row, stack, spinner, check = self.step_rows[key]
        spinner.start()
        stack.set_visible_child_name("spinner")
        idx = next(i for i, (_, k) in enumerate(ALL_STEPS) if k == key)
        self.progress.set_fraction(idx / len(ALL_STEPS))
        self.status_lbl.set_text(label + "…")

    def _set_step_done(self, key):
        row, stack, spinner, check = self.step_rows[key]
        spinner.stop()
        stack.set_visible_child_name("check")

    def _set_error(self, msg):
        self.status_lbl.set_text(f"Fout: {msg}")
        self.progress.add_css_class("error")

    def _run_install(self):
        steps = [
            ("clone",    "Pixora bestanden downloaden", self._clone_repo),
            ("apt",      "Systeem packages installeren", self._install_apt),
            ("pip",      "Python packages installeren",  self._install_pip),
            ("desktop",  "Launcher aanmaken",            self._create_launcher),
            ("services", "Services activeren",           self._start_services),
            ("launch",   "Pixora starten",               self._launch_app),
        ]
        for key, label, fn in steps:
            GLib.idle_add(self._set_step_active, key, label)
            ok, err = fn()
            if not ok:
                GLib.idle_add(self._set_error, err)
                return
            GLib.idle_add(self._set_step_done, key)

        try:
            version_src = INSTALL_DIR / "version.txt"
            installed_version_file = Path.home() / ".config" / "pixora" / "installed_version"
            if version_src.exists():
                installed_version_file.parent.mkdir(parents=True, exist_ok=True)
                installed_version_file.write_text(version_src.read_text())
        except Exception:
            pass

        GLib.idle_add(self.progress.set_fraction, 1.0)

    # ── Installatie stappen ───────────────────────────────────────────

    def _clone_repo(self):
        try:
            if (INSTALL_DIR / ".git").exists():
                subprocess.run(["git", "-C", str(INSTALL_DIR), "fetch", "--tags", "-q"],
                               check=True, capture_output=True)
                ref = self.selected_version if self.selected_version else "origin/main"
                subprocess.run(["git", "-C", str(INSTALL_DIR), "reset", "--hard", ref, "-q"],
                               check=True, capture_output=True)
            else:
                if INSTALL_DIR.exists():
                    import shutil
                    shutil.rmtree(INSTALL_DIR)
                if self.selected_version:
                    subprocess.run(
                        ["git", "clone", "-q", "--branch", self.selected_version,
                         "--depth", "1", REPO_URL, str(INSTALL_DIR)],
                        check=True, capture_output=True
                    )
                else:
                    subprocess.run(["git", "clone", "-q", REPO_URL, str(INSTALL_DIR)],
                                   check=True, capture_output=True)
            version_src = INSTALL_DIR / "version.txt"
            installed_version_file = Path.home() / ".config" / "pixora" / "installed_version"
            if version_src.exists():
                installed_version_file.parent.mkdir(parents=True, exist_ok=True)
                installed_version_file.write_text(version_src.read_text())
            return True, ""
        except subprocess.CalledProcessError:
            return False, "downloaden mislukt"

    def _install_apt(self):
        packages = [
            "python3-gi", "python3-gi-cairo",
            "gir1.2-gtk-4.0", "gir1.2-adw-1",
            "ifuse", "libimobiledevice-utils", "usbmuxd",
            "ffmpeg", "python3-pip",
        ]
        try:
            subprocess.run(["sudo", "apt-get", "install", "-y", "-qq"] + packages,
                           check=True, capture_output=True)
            return True, ""
        except subprocess.CalledProcessError:
            return False, "apt mislukt"

    def _install_pip(self):
        packages = ["Pillow", "imagehash", "watchdog"]
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q",
                 "--break-system-packages"] + packages,
                check=True, capture_output=True
            )
            return True, ""
        except subprocess.CalledProcessError:
            return False, "pip mislukt"

    def _create_launcher(self):
        try:
            BIN_DIR.mkdir(parents=True, exist_ok=True)
            DESKTOP_DIR.mkdir(parents=True, exist_ok=True)

            launcher = BIN_DIR / "pixora"
            launcher.write_text(
                f"#!/bin/bash\npython3 {INSTALL_DIR}/viewer/main.py\n"
            )
            launcher.chmod(0o755)

            icon = INSTALL_DIR / "docs" / "pixora-icon.svg"
            desktop = DESKTOP_DIR / "pixora.desktop"
            desktop.write_text(
                "[Desktop Entry]\n"
                "Name=Pixora\n"
                "GenericName=Foto & Video Manager\n"
                "Comment=Foto & video manager door LinuxGinger\n"
                f"Exec={launcher}\n"
                f"Icon={icon}\n"
                "Terminal=false\n"
                "Type=Application\n"
                "Categories=Graphics;Photography;\n"
                "StartupNotify=true\n"
                "StartupWMClass=com.linuxginger.pixora\n"
            )
            return True, ""
        except Exception as e:
            return False, str(e)

    def _start_services(self):
        try:
            subprocess.run(["sudo", "systemctl", "enable", "--now", "usbmuxd"],
                           check=True, capture_output=True)
        except subprocess.CalledProcessError:
            pass
        return True, ""

    def _launch_app(self):
        try:
            main_py = INSTALL_DIR / "viewer" / "main.py"
            subprocess.Popen([sys.executable, str(main_py)])
            GLib.timeout_add(1500, self.get_application().quit)
            return True, ""
        except Exception as e:
            return False, str(e)


class InstallerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.linuxginger.pixora.installer")
        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        win = InstallerWindow(app)
        win.present()


if __name__ == "__main__":
    app = InstallerApp()
    sys.exit(app.run(sys.argv))
