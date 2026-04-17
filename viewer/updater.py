#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — updater.py
#  GUI-updater (geen terminal)
# ─────────────────────────────────────────────

import os
import re
import sys
import threading
import subprocess

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio

UPDATE_URL = "https://raw.githubusercontent.com/Linux-Ginger/Pixora/main/update.sh"

ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')


class UpdaterWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Pixora updaten")
        self.set_default_size(520, 360)
        self.set_resizable(False)

        header = Adw.HeaderBar()
        header.add_css_class("flat")
        self._header_title = Adw.WindowTitle(title="Pixora updaten", subtitle="Bezig…")
        header.set_title_widget(self._header_title)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        body.set_margin_top(24)
        body.set_margin_bottom(24)
        body.set_margin_start(24)
        body.set_margin_end(24)

        self._status_label = Gtk.Label(label="Pixora wordt bijgewerkt…")
        self._status_label.add_css_class("title-3")
        self._status_label.set_halign(Gtk.Align.START)
        body.append(self._status_label)

        self._progress = Gtk.ProgressBar()
        self._progress.set_pulse_step(0.08)
        body.append(self._progress)

        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_vexpand(True)
        log_scroll.set_hexpand(True)
        log_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._log_buf = Gtk.TextBuffer()
        self._log_view = Gtk.TextView(buffer=self._log_buf)
        self._log_view.set_monospace(True)
        self._log_view.set_editable(False)
        self._log_view.set_cursor_visible(False)
        self._log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        log_scroll.set_child(self._log_view)
        body.append(log_scroll)

        self._btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._btn_box.set_halign(Gtk.Align.END)
        self._close_btn = Gtk.Button(label="Sluiten")
        self._close_btn.connect("clicked", lambda b: self.close())
        self._close_btn.set_sensitive(False)
        self._relaunch_btn = Gtk.Button(label="Pixora starten")
        self._relaunch_btn.add_css_class("suggested-action")
        self._relaunch_btn.set_visible(False)
        self._relaunch_btn.connect("clicked", self._on_relaunch)
        self._btn_box.append(self._close_btn)
        self._btn_box.append(self._relaunch_btn)
        body.append(self._btn_box)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(header)
        root.append(body)
        self.set_content(root)

        self._pulse_timer = GLib.timeout_add(100, self._pulse_tick)
        threading.Thread(target=self._run_update, daemon=True).start()

    def _pulse_tick(self):
        try:
            self._progress.pulse()
        except Exception:
            return False
        return True

    def _append_log(self, line):
        # strip ANSI escape codes (colors, clear, cursor) voor schone output
        clean = ANSI_ESCAPE.sub("", line)
        end = self._log_buf.get_end_iter()
        self._log_buf.insert(end, clean)
        # auto-scroll
        mark = self._log_buf.create_mark(None, self._log_buf.get_end_iter(), False)
        self._log_view.scroll_to_mark(mark, 0.0, False, 0.0, 0.0)
        return False

    def _run_update(self):
        # Download update.sh en draai 'm via pkexec. PKEXEC_UID vertelt het
        # script naar welke user-home het Pixora moet schrijven.
        uid = str(os.getuid())
        cmd = [
            "pkexec",
            "env",
            f"PKEXEC_UID={uid}",
            "bash", "-c",
            f"set -e; "
            f"TMP=$(mktemp -d); "
            f"cd $TMP && "
            f"curl -fsSL '{UPDATE_URL}' -o update.sh && "
            f"bash update.sh 2>&1; "
            f"RC=$?; rm -rf $TMP; exit $RC"
        ]
        GLib.idle_add(self._append_log,
                      f"→ update.sh downloaden en uitvoeren via pkexec…\n\n")
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                env={**os.environ, "TERM": "dumb"}
            )
        except FileNotFoundError:
            GLib.idle_add(self._finish,
                          False, "pkexec niet gevonden. Installeer policykit-1.")
            return

        for line in proc.stdout:
            GLib.idle_add(self._append_log, line)
        proc.wait()
        ok = (proc.returncode == 0)
        GLib.idle_add(self._finish, ok,
                      "Pixora is bijgewerkt!" if ok
                      else "Update mislukt. Zie de log hierboven.")

    def _finish(self, ok, message):
        if self._pulse_timer:
            try:
                GLib.source_remove(self._pulse_timer)
            except Exception:
                pass
            self._pulse_timer = None
        if ok:
            self._progress.set_fraction(1.0)
        self._header_title.set_subtitle("Klaar" if ok else "Mislukt")
        self._status_label.set_text(message)
        self._close_btn.set_sensitive(True)
        self._relaunch_btn.set_visible(ok)
        return False

    def _on_relaunch(self, btn):
        try:
            subprocess.Popen(
                [sys.executable,
                 os.path.join(os.path.expanduser("~/.local/share/pixora/viewer"),
                              "main.py")]
            )
        except Exception as e:
            self._append_log(f"\n[relaunch fout: {e}]\n")
            return
        self.get_application().quit()


class UpdaterApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.linuxginger.pixora.updater",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )
        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        UpdaterWindow(app).present()


def main():
    app = UpdaterApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
