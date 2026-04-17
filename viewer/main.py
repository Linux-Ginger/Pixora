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
LOG_PATH = os.path.expanduser("~/.cache/pixora/pixora.log")


def load_settings():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    return None


def _launch_dev_log_terminal():
    """In dev mode: start een apart terminal-venster dat live de log-file
    volgt via 'tail -f'. Pixora zelf start gewoon snel door."""
    settings = load_settings()
    if not settings or not settings.get("dev_mode"):
        return
    if os.environ.get("PIXORA_DEV_LOG_OPENED"):
        return
    # Zorg dat de log-file bestaat zodat tail niet meteen stopt
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        if not os.path.exists(LOG_PATH):
            open(LOG_PATH, "a").close()
    except Exception as e:
        print(f"Kon log-file niet aanmaken: {e}")
        return
    tail_cmd = f"echo '─── Pixora dev-log ({LOG_PATH}) ───'; tail -F -n 200 '{LOG_PATH}'"
    for term, args in [
        ("gnome-terminal", ["--title=Pixora dev-log", "--", "bash", "-c", tail_cmd]),
        ("konsole",        ["--title", "Pixora dev-log", "-e", "bash", "-c", tail_cmd]),
        ("xfce4-terminal", ["--title=Pixora dev-log", "-e", f"bash -c \"{tail_cmd}\""]),
        ("xterm",          ["-T", "Pixora dev-log", "-e", "bash", "-c", tail_cmd]),
    ]:
        if shutil.which(term):
            try:
                subprocess.Popen(
                    [term] + args,
                    env={**os.environ, "PIXORA_DEV_LOG_OPENED": "1"},
                    start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return
            except Exception as e:
                print(f"Kon {term} niet starten: {e}")


_launch_dev_log_terminal()


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