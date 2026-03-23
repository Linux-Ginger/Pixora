#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — installer.py
#  Grafische installer (GTK4/Adwaita)
#  by LinuxGinger
# ─────────────────────────────────────────────

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio

import os
import sys
import subprocess
import threading
from pathlib import Path

INSTALL_DIR  = Path.home() / ".local" / "share" / "pixora"
BIN_DIR      = Path.home() / ".local" / "bin"
DESKTOP_DIR  = Path.home() / ".local" / "share" / "applications"
REPO_URL     = "https://github.com/Linux-Ginger/Pixora.git"

STEPS = [
    ("Systeem packages installeren",   "apt"),
    ("Python packages installeren",    "pip"),
    ("Pixora downloaden",              "clone"),
    ("Launcher aanmaken",             "desktop"),
    ("Pixora starten",                "launch"),
]


class InstallerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Pixora Installer")
        self.set_default_size(480, 420)
        self.set_resizable(False)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        toolbar.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_vexpand(True)

        # ── Logo / titel ──
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        top.set_margin_top(32)
        top.set_margin_bottom(24)
        top.set_halign(Gtk.Align.CENTER)

        logo = Gtk.Image.new_from_icon_name("applications-graphics-symbolic")
        logo.set_pixel_size(64)
        top.append(logo)

        title = Gtk.Label(label="Pixora")
        title.add_css_class("title-1")
        top.append(title)

        sub = Gtk.Label(label="door LinuxGinger")
        sub.add_css_class("dim-label")
        top.append(sub)

        box.append(top)

        # ── Stappen ──
        self.step_rows = []
        steps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        steps_box.set_margin_start(32)
        steps_box.set_margin_end(32)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")

        for label, _ in STEPS:
            row = Adw.ActionRow()
            row.set_title(label)

            spinner = Gtk.Spinner()
            spinner.set_size_request(20, 20)
            check = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            check.set_visible(False)
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
            self.step_rows.append((row, stack, spinner, check))

        steps_box.append(listbox)
        box.append(steps_box)

        # ── Status label ──
        self.status_lbl = Gtk.Label(label="Installatie starten…")
        self.status_lbl.add_css_class("dim-label")
        self.status_lbl.set_margin_top(16)
        self.status_lbl.set_margin_bottom(8)
        box.append(self.status_lbl)

        # ── Voortgangsbalk ──
        self.progress = Gtk.ProgressBar()
        self.progress.set_margin_start(32)
        self.progress.set_margin_end(32)
        self.progress.set_margin_bottom(32)
        box.append(self.progress)

        toolbar.set_content(box)
        self.set_content(toolbar)

        GLib.timeout_add(400, self._start)

    def _start(self):
        threading.Thread(target=self._run_install, daemon=True).start()
        return False

    def _set_step_active(self, i):
        row, stack, spinner, check = self.step_rows[i]
        spinner.start()
        stack.set_visible_child_name("spinner")
        frac = i / len(STEPS)
        self.progress.set_fraction(frac)
        self.status_lbl.set_text(STEPS[i][0] + "…")

    def _set_step_done(self, i):
        row, stack, spinner, check = self.step_rows[i]
        spinner.stop()
        check.set_visible(True)
        stack.set_visible_child_name("check")

    def _set_error(self, msg):
        self.status_lbl.set_text(f"Fout: {msg}")
        self.progress.add_css_class("error")

    def _run_install(self):
        steps = [
            self._install_apt,
            self._install_pip,
            self._clone_repo,
            self._create_launcher,
            self._launch_app,
        ]
        for i, fn in enumerate(steps):
            GLib.idle_add(self._set_step_active, i)
            ok, err = fn()
            if not ok:
                GLib.idle_add(self._set_error, err)
                return
            GLib.idle_add(self._set_step_done, i)

        GLib.idle_add(self.progress.set_fraction, 1.0)

    # ── Installatie stappen ────────────────────────────────────────────

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
        except subprocess.CalledProcessError as e:
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

    def _clone_repo(self):
        try:
            INSTALL_DIR.mkdir(parents=True, exist_ok=True)
            if (INSTALL_DIR / ".git").exists():
                subprocess.run(["git", "-C", str(INSTALL_DIR), "pull", "-q"],
                               check=True, capture_output=True)
            else:
                if INSTALL_DIR.exists():
                    import shutil
                    shutil.rmtree(INSTALL_DIR)
                subprocess.run(["git", "clone", "-q", REPO_URL, str(INSTALL_DIR)],
                               check=True, capture_output=True)
            return True, ""
        except subprocess.CalledProcessError:
            return False, "downloaden mislukt"

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
