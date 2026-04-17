#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — main.py
#  by LinuxGinger
# ─────────────────────────────────────────────

import sys
import os
import json
import time
import signal
import shutil
import threading
import subprocess

CONFIG_PATH = os.path.expanduser("~/.config/pixora/settings.json")
LOG_PATH = os.path.expanduser("~/.cache/pixora/pixora.log")

# Global zodat MainWindow weet of dev-mode actief is (logs conditioneren)
PIXORA_DEV_MODE = False
# Handle naar het tail-terminal subprocess zodat we kunnen mee-quitten
_DEV_TERM_PROC = None
_PIXORA_APP = None


def load_settings():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    return None


def _launch_dev_log_terminal():
    """In dev mode: start een apart terminal-venster dat live de log-file
    volgt via 'tail -F'. Pixora zelf start gewoon snel door, maar wacht
    ~0.4s zodat de terminal eerst visueel verschijnt. Als de terminal
    gesloten wordt, quit Pixora automatisch mee."""
    global PIXORA_DEV_MODE, _DEV_TERM_PROC
    settings = load_settings()
    if not settings or not settings.get("dev_mode"):
        return
    PIXORA_DEV_MODE = True
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
    # --wait (gnome-terminal) en default behaviour voor anderen zorgt dat
    # het subprocess pas eindigt als het laatste tabblad sluit.
    for term, args in [
        ("gnome-terminal", ["--wait", "--title=Pixora dev-log", "--",
                            "bash", "-c", tail_cmd]),
        ("konsole",        ["--title", "Pixora dev-log", "-e",
                            "bash", "-c", tail_cmd]),
        ("xfce4-terminal", ["--disable-server", "--title=Pixora dev-log",
                            "-x", "bash", "-c", tail_cmd]),
        ("xterm",          ["-T", "Pixora dev-log", "-e",
                            "bash", "-c", tail_cmd]),
    ]:
        if shutil.which(term):
            try:
                _DEV_TERM_PROC = subprocess.Popen(
                    [term] + args,
                    env={**os.environ, "PIXORA_DEV_LOG_OPENED": "1"},
                    start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                # Start waiter die Pixora laat stoppen als de terminal dicht gaat
                threading.Thread(
                    target=_watch_dev_terminal, daemon=True
                ).start()
                # Laat de terminal even de tijd om op het scherm te komen
                # zodat hij visueel voor Pixora verschijnt.
                time.sleep(0.45)
                return
            except Exception as e:
                print(f"Kon {term} niet starten: {e}")


def _watch_dev_terminal():
    if _DEV_TERM_PROC is None:
        return
    _DEV_TERM_PROC.wait()
    # Terminal is gesloten -> quit Pixora
    try:
        from gi.repository import GLib
        GLib.idle_add(_quit_pixora_app)
    except Exception:
        os._exit(0)


def _quit_pixora_app():
    if _PIXORA_APP is not None:
        _PIXORA_APP.quit()
    return False


def kill_dev_terminal():
    """Zet de tail-terminal stop — aangeroepen als Pixora afsluit.
    Kill de hele process-group zodat tail + bash + terminal-emulator
    samen sluiten (anders blijft gnome-terminal-server tail in leven)."""
    global _DEV_TERM_PROC
    if _DEV_TERM_PROC is None:
        return
    try:
        if _DEV_TERM_PROC.poll() is None:
            try:
                os.killpg(os.getpgid(_DEV_TERM_PROC.pid), signal.SIGTERM)
            except Exception:
                _DEV_TERM_PROC.terminate()
            try:
                _DEV_TERM_PROC.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(_DEV_TERM_PROC.pid), signal.SIGKILL)
                except Exception:
                    _DEV_TERM_PROC.kill()
    except Exception:
        pass
    _DEV_TERM_PROC = None


import atexit
atexit.register(kill_dev_terminal)


# Alleen de échte opstart (command-line) mag de terminal openen.
# Bij `from main import ...` (geïmporteerd vanuit main_window) wordt deze
# module opnieuw uitgevoerd onder de naam "main"; dan skippen we dit.
if __name__ == "__main__":
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
    global _PIXORA_APP
    _PIXORA_APP = PixoraApp()
    return _PIXORA_APP.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())