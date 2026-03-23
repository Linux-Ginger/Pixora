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
import subprocess
import threading
from pathlib import Path

INSTALL_DIR  = Path.home() / ".local" / "share" / "pixora"
BIN_DIR      = Path.home() / ".local" / "bin"
DESKTOP_DIR  = Path.home() / ".local" / "share" / "applications"
REPO_URL     = "https://github.com/Linux-Ginger/Pixora.git"

# Fases met stappen: (label, actie-sleutel)
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
        ("Pixora starten",               "launch"),
    ]),
]

# Platte lijst voor voortgangsbalk
ALL_STEPS = [(label, key) for _, steps in PHASES for label, key in steps]


class InstallerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Pixora Installer")
        self.set_default_size(500, 520)
        self.set_resizable(False)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        toolbar.add_top_bar(header)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_vexpand(True)

        # ── Logo / titel ──
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        top.set_margin_top(28)
        top.set_margin_bottom(20)
        top.set_halign(Gtk.Align.CENTER)

        logo = Gtk.Image.new_from_icon_name("applications-graphics-symbolic")
        logo.set_pixel_size(56)
        top.append(logo)

        title = Gtk.Label(label="Pixora")
        title.add_css_class("title-1")
        top.append(title)

        sub = Gtk.Label(label="door LinuxGinger")
        sub.add_css_class("dim-label")
        top.append(sub)

        outer.append(top)

        # ── Fases met stappen ──
        self.step_rows = {}   # actie-sleutel → (row, stack, spinner, check)

        phases_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        phases_box.set_margin_start(32)
        phases_box.set_margin_end(32)

        for phase_label, steps in PHASES:
            # Fase-header
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

        outer.append(phases_box)

        # ── Status label ──
        self.status_lbl = Gtk.Label(label="Installatie starten…")
        self.status_lbl.add_css_class("dim-label")
        self.status_lbl.set_margin_top(16)
        self.status_lbl.set_margin_bottom(8)
        outer.append(self.status_lbl)

        # ── Voortgangsbalk ──
        self.progress = Gtk.ProgressBar()
        self.progress.set_margin_start(32)
        self.progress.set_margin_end(32)
        self.progress.set_margin_bottom(28)
        outer.append(self.progress)

        toolbar.set_content(outer)
        self.set_content(toolbar)

        GLib.timeout_add(400, self._start)

    def _start(self):
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
            ("clone",   "Pixora bestanden downloaden", self._clone_repo),
            ("apt",     "Systeem packages installeren", self._install_apt),
            ("pip",     "Python packages installeren",  self._install_pip),
            ("desktop", "Launcher aanmaken",            self._create_launcher),
            ("launch",  "Pixora starten",               self._launch_app),
        ]
        for key, label, fn in steps:
            GLib.idle_add(self._set_step_active, key, label)
            ok, err = fn()
            if not ok:
                GLib.idle_add(self._set_error, err)
                return
            GLib.idle_add(self._set_step_done, key)

        GLib.idle_add(self.progress.set_fraction, 1.0)

    # ── Installatie stappen ────────────────────────────────────────────

    def _clone_repo(self):
        try:
            if (INSTALL_DIR / ".git").exists():
                subprocess.run(["git", "-C", str(INSTALL_DIR), "pull", "-q"],
                               check=True, capture_output=True)
            else:
                if INSTALL_DIR.exists():
                    import shutil
                    shutil.rmtree(INSTALL_DIR)
                INSTALL_DIR.mkdir(parents=True, exist_ok=True)
                subprocess.run(["git", "clone", "-q", REPO_URL, str(INSTALL_DIR)],
                               check=True, capture_output=True)
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
            subprocess.run(["sudo", "apt-get", "update", "-qq"],
                           check=True, capture_output=True)
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

            icon = INSTALL_DIR / "docs" / "pixora-logo-dark.png"
            desktop = DESKTOP_DIR / "pixora.desktop"
            desktop.write_text(
                "[Desktop Entry]\n"
                "Name=Pixora\n"
                "Comment=Foto & video manager door LinuxGinger\n"
                f"Exec={launcher}\n"
                f"Icon={icon}\n"
                "Terminal=false\n"
                "Type=Application\n"
                "Categories=Graphics;Photography;\n"
                "StartupNotify=true\n"
            )
            return True, ""
        except Exception as e:
            return False, str(e)

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
