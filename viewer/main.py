#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — main.py
#  by LinuxGinger
# ─────────────────────────────────────────────

import sys
import os
import json
import shutil
import subprocess

CONFIG_PATH = os.path.expanduser("~/.config/pixora/settings.json")


def load_settings():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    return None


def _maybe_relaunch_in_terminal():
    """In dev mode: herstart Pixora binnen een terminal zodat stdout/stderr
    zichtbaar zijn. Wordt alleen gedaan als we niet al in een terminal-context
    zitten (PIXORA_IN_TERMINAL env var)."""
    if os.environ.get("PIXORA_IN_TERMINAL"):
        return False
    settings = load_settings()
    if not settings or not settings.get("dev_mode"):
        return False
    # Zoek een beschikbare terminal
    for term, args in [
        ("gnome-terminal", ["--", sys.executable, os.path.abspath(__file__)]),
        ("konsole", ["-e", sys.executable, os.path.abspath(__file__)]),
        ("xterm", ["-e", sys.executable, os.path.abspath(__file__)]),
        ("xfce4-terminal", ["-e", f"{sys.executable} {os.path.abspath(__file__)}"]),
    ]:
        if shutil.which(term):
            env = {**os.environ, "PIXORA_IN_TERMINAL": "1"}
            try:
                subprocess.Popen([term] + args, env=env)
                return True
            except Exception as e:
                print(f"Kon {term} niet starten: {e}")
    # Geen terminal gevonden — gewoon doorstarten zonder
    return False


if _maybe_relaunch_in_terminal():
    sys.exit(0)


import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio


class PixoraApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.linuxginger.pixora",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )
        self.connect("activate", self.on_activate)

    def on_activate(self, app):
        settings = load_settings()

        if settings is None:
            # Eerste keer opstarten → setup wizard
            from setup_wizard import SetupWizard
            win = SetupWizard(app)
        else:
            # Instellingen gevonden → hoofdscherm
            from main_window import MainWindow
            win = MainWindow(app, settings)

        win.present()


def main():
    app = PixoraApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())