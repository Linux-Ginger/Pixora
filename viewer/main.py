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

PIXORA_DEV_MODE = False
_PIXORA_APP = None


def _compile_stale_mo_files():
    """Rebuild .mo files when .po is newer (git pull leaves stale .mo)."""
    try:
        if not shutil.which("msgfmt"):
            return
        locale_dir = os.path.abspath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "locale"
        ))
        if not os.path.isdir(locale_dir):
            return
        for lang in os.listdir(locale_dir):
            lc_dir = os.path.join(locale_dir, lang, "LC_MESSAGES")
            if not os.path.isdir(lc_dir):
                continue
            po = os.path.join(lc_dir, "pixora.po")
            mo = os.path.join(lc_dir, "pixora.mo")
            if not os.path.isfile(po):
                continue
            try:
                po_mtime = os.path.getmtime(po)
                mo_mtime = os.path.getmtime(mo) if os.path.exists(mo) else 0
            except OSError:
                continue
            if po_mtime > mo_mtime:
                try:
                    subprocess.run(
                        ["msgfmt", "-o", mo, po],
                        capture_output=True, timeout=10,
                    )
                except Exception:
                    pass
    except Exception:
        pass


_compile_stale_mo_files()


_GSK_PENDING_PATH = os.path.expanduser("~/.cache/pixora/.gsk_pending")


def _check_gsk_crash_recovery():
    """Revert to 'auto' renderer if prior run crashed during startup."""
    if not os.path.exists(_GSK_PENDING_PATH):
        return
    try:
        with open(_GSK_PENDING_PATH, "r") as f:
            pending = f.read().strip()
    except Exception:
        pending = ""
    try:
        os.remove(_GSK_PENDING_PATH)
    except Exception:
        pass
    if not pending or pending == "auto":
        return
    print(
        f"Pixora: previous run with GSK_RENDERER={pending!r} didn't "
        f"complete cleanly — reverting to 'auto'.",
        flush=True,
    )
    try:
        with open(CONFIG_PATH, "r") as f:
            settings = json.load(f)
    except Exception:
        return
    if settings.get("gsk_renderer") != pending:
        print(f"Pixora: user already changed renderer, not clobbering.",
              flush=True)
        return
    settings["gsk_renderer"] = "auto"
    # Mark renderer as known-bad so Settings can warn on re-selection.
    blacklist = settings.get("gsk_renderer_crashed") or []
    if isinstance(blacklist, list) and pending not in blacklist:
        blacklist.append(pending)
        settings["gsk_renderer_crashed"] = blacklist
    settings["gsk_recent_crash"] = pending
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(settings, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        pass
    # Recovery run happens inside the detached watcher subprocess, where
    # gnome-terminal DBus activation fails ("Failed to get screen"). Mark
    # so _launch_dev_terminal skips and Pixora opens directly instead.
    os.environ["PIXORA_GSK_RECOVERY_RUN"] = "1"


def _apply_gsk_renderer_env():
    """Apply GSK_RENDERER from settings before GTK imports (env sampled once)."""
    try:
        with open(CONFIG_PATH, "r") as _rf:
            choice = json.load(_rf).get("gsk_renderer", "auto")
    except Exception:
        choice = "auto"
    if choice in ("gl", "cairo", "ngl"):
        os.environ["GSK_RENDERER"] = choice
        # Sentinel; cleared on Gio.Application::shutdown so a crash/hang
        # at any point during the session leaves it for recovery.
        try:
            os.makedirs(os.path.dirname(_GSK_PENDING_PATH), exist_ok=True)
            with open(_GSK_PENDING_PATH, "w") as f:
                f.write(choice)
        except Exception:
            pass
        print(f"Pixora: GSK_RENDERER={choice} (.gsk_pending written)",
              flush=True)


_check_gsk_crash_recovery()
_apply_gsk_renderer_env()


def load_settings():
    if not os.path.exists(CONFIG_PATH):
        return None
    try:
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        try:
            os.rename(CONFIG_PATH, CONFIG_PATH + ".corrupt")
        except Exception:
            pass
    return None


def _launch_dev_terminal():
    """Dev-mode: re-spawn Pixora inside a terminal; SIGHUP on close quits cleanly."""
    global PIXORA_DEV_MODE
    settings = load_settings()
    if not settings or not settings.get("dev_mode"):
        return
    PIXORA_DEV_MODE = True
    if os.environ.get("PIXORA_IN_DEV_TERM"):
        return
    # Skip terminal when a risky GSK renderer is pending — the launcher needs
    # to watch this process directly to auto-respawn on a startup crash.
    if os.path.exists(_GSK_PENDING_PATH):
        return
    # Recovery run from the watcher: gnome-terminal DBus activation fails
    # inside a detached session. Open the window directly instead.
    if os.environ.get("PIXORA_GSK_RECOVERY_RUN"):
        return

    pixora_bin = os.path.expanduser("~/.local/bin/pixora")
    if os.path.exists(pixora_bin):
        run_cmd = f'"{pixora_bin}"'
    else:
        run_cmd = f'python3 "{os.path.abspath(__file__)}"'

    import gettext as _gt
    _locale_dir = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "locale"
    ))
    _lang = settings.get("language", "nl") if settings else "nl"
    try:
        _t = _gt.translation("pixora", localedir=_locale_dir,
                             languages=[_lang], fallback=True)
        header = _t.gettext("─── Pixora (dev mode) ───")
        hint = _t.gettext("Close this window to quit Pixora.")
    except Exception:
        header = "─── Pixora (dev mode) ───"
        hint = "Close this window to quit Pixora."

    # Set env in bash cmd; gnome-terminal-server drops Popen env.
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
                sys.exit(0)
            except Exception as e:
                try:
                    print(_t.gettext("Could not start {term}: {err}").format(term=term, err=e))
                except Exception:
                    print(f"Could not start {term}: {e}")
                continue
    try:
        print(_t.gettext("No terminal found for dev-mode; Pixora starts without."))
    except Exception:
        print("No terminal found for dev-mode; Pixora starts without.")


def _quit_pixora_app():
    if _PIXORA_APP is not None:
        _PIXORA_APP.quit()
    return False


# SIGHUP handler: closing the dev-terminal quits Pixora cleanly.
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


# Only spawn dev-terminal on real CLI startup, not on re-import as "main".
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
        # shutdown only fires on clean exit — not on SIGSEGV/SIGKILL/hang.
        # So if Pixora crashes with a custom GSK_RENDERER, .gsk_pending
        # survives and next-run crash-recovery (+ watcher respawn) triggers.
        self.connect("shutdown", self._on_shutdown)

    def _on_shutdown(self, _app):
        try:
            if os.path.exists(_GSK_PENDING_PATH):
                os.remove(_GSK_PENDING_PATH)
        except Exception:
            pass

    def on_activate(self, app):
        settings = load_settings()

        if settings is None:
            from setup_wizard import SetupWizard
            win = SetupWizard(app)
        else:
            from main_window import MainWindow
            win = MainWindow(app, settings)

        # set_visible avoids the GNOME Shell "ready" notification on every start.
        win.set_visible(True)


def _consume_restart_sentinel():
    """Exec in-place on sentinel; preserves env so dev-terminal isn't re-spawned."""
    sentinel = os.path.expanduser("~/.cache/pixora/.restart_pending")
    if not os.path.exists(sentinel):
        return
    # Loop-guard: bail out after 2 respawns to avoid infinite cycle.
    restart_count = int(os.environ.get("PIXORA_RESTART_COUNT", "0") or "0")
    if restart_count >= 2:
        print(
            f"Pixora: restart guard hit ({restart_count}× already); "
            f"aborting to avoid loop. Delete {sentinel} manually if stuck.",
            flush=True,
        )
        try:
            os.remove(sentinel)
        except Exception:
            pass
        return
    try:
        os.remove(sentinel)
    except Exception as e:
        # If we can't remove the file, exec would loop forever.
        print(
            f"Pixora: could not remove restart sentinel ({e}); aborting "
            f"to avoid a loop. Delete {sentinel} manually.",
            flush=True,
        )
        return
    print("Pixora: restart sentinel detected, exec'ing…", flush=True)
    new_env = dict(os.environ)
    new_env["PIXORA_RESTART_COUNT"] = str(restart_count + 1)
    pixora_bin = os.path.expanduser("~/.local/bin/pixora")
    try:
        if os.path.exists(pixora_bin):
            os.execvpe(pixora_bin, [pixora_bin], new_env)
        else:
            script = os.path.abspath(__file__)
            os.execvpe(sys.executable, [sys.executable, script], new_env)
    except Exception as e:
        print(f"Pixora restart failed: {e}", flush=True)


def main():
    global _PIXORA_APP
    _PIXORA_APP = PixoraApp()
    rc = _PIXORA_APP.run(sys.argv)
    try:
        if PIXORA_DEV_MODE:
            print("Pixora clean exit (rc=" + str(rc) + ")", flush=True)
    except Exception:
        pass
    _consume_restart_sentinel()
    return rc


if __name__ == "__main__":
    sys.exit(main())