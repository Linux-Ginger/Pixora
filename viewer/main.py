#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — main.py
#  by LinuxGinger
# ─────────────────────────────────────────────

import sys
import os
import json

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio

CONFIG_PATH = os.path.expanduser("~/.config/pixora/settings.json")


def load_settings():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    return None


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
        from gi.repository import GLib
        import time as _time

        # Op Wayland herstelt GNOME de vorige venstergrootte na present().
        # We blijven re-maximaliseren totdat het lukt (max 2 seconden).
        _start = _time.monotonic()

        def _keep_maximized():
            if not win.is_maximized():
                win.maximize()
            if _time.monotonic() - _start < 2.0:
                return True   # blijf proberen
            return False      # stop na 2s

        GLib.timeout_add(100, _keep_maximized)


def main():
    app = PixoraApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())