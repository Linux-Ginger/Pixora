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
_PIXORA_APP = None


def load_settings():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    return None


def _launch_dev_terminal():
    """Dev-mode: spawn een terminal die Pixora zelf aanroept via
    ~/.local/bin/pixora. Originele process exit — de terminal "neemt
    het over". Pixora's stdout/stderr gaat direct naar die terminal.

    Pixora sluiten → python exits → bash exits → terminal sluit.
    Terminal sluiten → bash krijgt SIGHUP → propageert naar python →
    GTK-app eindigt netjes.
    """
    global PIXORA_DEV_MODE
    settings = load_settings()
    if not settings or not settings.get("dev_mode"):
        return
    PIXORA_DEV_MODE = True
    # Al in de dev-terminal? Niet nogmaals spawnen.
    if os.environ.get("PIXORA_IN_DEV_TERM"):
        return

    pixora_bin = os.path.expanduser("~/.local/bin/pixora")
    if os.path.exists(pixora_bin):
        run_cmd = f'"{pixora_bin}"'
    else:
        run_cmd = f'python3 "{os.path.abspath(__file__)}"'

    # Dev-terminal tekst lokaliseren o.b.v. gekozen taal in settings
    import gettext as _gt
    _locale_dir = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "locale"
    ))
    _lang = settings.get("language", "nl") if settings else "nl"
    try:
        _t = _gt.translation("pixora", localedir=_locale_dir,
                             languages=[_lang], fallback=True)
        header = _t.gettext("─── Pixora (dev mode) ───")
        hint = _t.gettext("Sluit dit venster om Pixora te sluiten.")
    except Exception:
        header = "─── Pixora (dev mode) ───"
        hint = "Sluit dit venster om Pixora te sluiten."

    # Zet PIXORA_IN_DEV_TERM ín het bash-commando i.p.v. via Popen env —
    # gnome-terminal-server neemt env niet altijd mee naar z'n child-shell.
    bash_cmd = (
        "export PIXORA_IN_DEV_TERM=1; "
        f"echo '{header}'; "
        f"echo '{hint}'; "
        "echo; "
        f"exec {run_cmd}"
    )

    for term, args in [
        ("gnome-terminal", ["--title=Pixora (dev)", "--",
                            "bash", "-c", bash_cmd]),
        ("konsole",        ["--title", "Pixora (dev)", "-e",
                            "bash", "-c", bash_cmd]),
        ("xfce4-terminal", ["--disable-server", "--title=Pixora (dev)",
                            "-x", "bash", "-c", bash_cmd]),
        ("xterm",          ["-T", "Pixora (dev)", "-e",
                            "bash", "-c", bash_cmd]),
    ]:
        if shutil.which(term):
            try:
                subprocess.Popen(
                    [term] + args,
                    start_new_session=True,
                )
                # Origineel proces sluit af; de terminal neemt het over.
                sys.exit(0)
            except Exception as e:
                print(f"Kon {term} niet starten: {e}")
                continue
    # Geen terminal gevonden: val terug op directe start met waarschuwing
    print("Geen terminal gevonden voor dev-mode; Pixora start zonder.")


def _quit_pixora_app():
    if _PIXORA_APP is not None:
        _PIXORA_APP.quit()
    return False


def kill_dev_terminal():
    """Compat-stub — oude flow had een aparte tail-terminal die we nu
    niet meer gebruiken. Behouden omdat main_window.on_close nog
    main.kill_dev_terminal() aanroept."""
    return


# Install SIGHUP handler in de child-process (dev-terminal) zodat het
# sluiten van de terminal Pixora netjes afsluit ipv een hard crash.
def _install_dev_term_signal_handler():
    if not os.environ.get("PIXORA_IN_DEV_TERM"):
        return
    def _on_sighup(signum, frame):
        try:
            from gi.repository import GLib
            GLib.idle_add(_quit_pixora_app)
        except Exception:
            os._exit(0)
    try:
        signal.signal(signal.SIGHUP, _on_sighup)
    except Exception:
        pass


_install_dev_term_signal_handler()


# Alleen de échte opstart (command-line) mag de terminal openen.
# Bij `from main import ...` (geïmporteerd vanuit main_window) wordt deze
# module opnieuw uitgevoerd onder de naam "main"; dan skippen we dit.
if __name__ == "__main__":
    _launch_dev_terminal()


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
    rc = _PIXORA_APP.run(sys.argv)
    # Als we hier komen zonder _force_exit, is dat goed. Log het zodat je
    # in dev-mode ziet dat Pixora cleanly afgesloten is.
    try:
        if PIXORA_DEV_MODE:
            print("Pixora clean exit (rc=" + str(rc) + ")", flush=True)
    except Exception:
        pass
    return rc


if __name__ == "__main__":
    sys.exit(main())