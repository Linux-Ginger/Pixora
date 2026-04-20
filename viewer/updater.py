#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — updater.py
#  Grafische updater (GTK4/Adwaita)
#  Stijl: identiek aan installer.py
# ─────────────────────────────────────────────

import os

# ── i18n ─────────────────────────────────────────────────────────────
import gettext as _gt
import json as _json_i18n
try:
    _lang = _json_i18n.load(open(os.path.expanduser("~/.config/pixora/settings.json"))).get("language", "nl")
except Exception:
    _lang = "nl"
_t = _gt.translation(
    "pixora",
    localedir=os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "locale")),
    languages=[_lang], fallback=True
)
_ = _t.gettext

import re
import sys
import threading
import subprocess
import urllib.request
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio

INSTALL_DIR = Path.home() / ".local" / "share" / "pixora"
ICON_PATH = INSTALL_DIR / "assets" / "logos" / "pixora-icon.svg"
UPDATE_URL = "https://raw.githubusercontent.com/Linux-Ginger/Pixora/main/updater.sh"

# Stappen die updater.sh emit via "STEP:<key>:<label>" regels
PHASES = [
    ("Bijwerken", [
        ("Dependencies installeren", "deps"),
        ("Pixora ophalen van GitHub", "clone"),
        ("Configuratie + services", "finalize"),
    ]),
]
ALL_STEPS = [(label, key) for _, steps in PHASES for label, key in steps]

ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
STEP_RE = re.compile(r'^STEP:([^:]+):(.*)$')


class UpdaterWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title(_("Pixora Updater"))
        self.set_default_size(460, 420)
        self.set_resizable(False)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        toolbar.add_top_bar(header)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.set_vexpand(True)

        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        top.set_margin_top(16)
        top.set_margin_bottom(12)
        top.set_halign(Gtk.Align.CENTER)
        top.append(self._make_logo(40))
        title = Gtk.Label(label=_("Pixora bijwerken"))
        title.add_css_class("title-1")
        top.append(title)
        sub = Gtk.Label(label=_("door LinuxGinger"))
        sub.add_css_class("dim-label")
        top.append(sub)
        page.append(top)

        self.step_rows = {}
        phases_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        phases_box.set_margin_start(24)
        phases_box.set_margin_end(24)

        for phase_label, steps in PHASES:
            phase_lbl = Gtk.Label(label=_(phase_label))
            phase_lbl.add_css_class("heading")
            phase_lbl.set_halign(Gtk.Align.START)
            phase_lbl.set_margin_top(4)
            phases_box.append(phase_lbl)

            listbox = Gtk.ListBox()
            listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            listbox.add_css_class("boxed-list")

            for step_label, key in steps:
                row = Adw.ActionRow()
                row.set_title(_(step_label))
                spinner = Gtk.Spinner()
                spinner.set_size_request(20, 20)
                check = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
                check.set_pixel_size(16)
                stack = Gtk.Stack()
                stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
                stack.set_transition_duration(150)
                stack.add_named(Gtk.Box(), "empty")
                stack.add_named(spinner, "spinner")
                stack.add_named(check, "check")
                stack.set_visible_child_name("empty")
                row.add_suffix(stack)
                listbox.append(row)
                self.step_rows[key] = (row, stack, spinner, check)

            phases_box.append(listbox)

        page.append(phases_box)

        self.status_lbl = Gtk.Label(label=_("Wachten op sudo…"))
        self.status_lbl.add_css_class("dim-label")
        self.status_lbl.set_margin_top(16)
        self.status_lbl.set_margin_bottom(8)
        page.append(self.status_lbl)

        self.progress = Gtk.ProgressBar()
        self.progress.set_margin_start(24)
        self.progress.set_margin_end(24)
        self.progress.set_margin_bottom(8)
        page.append(self.progress)

        # Knoppen (sluiten + Pixora starten) — hidden tot klaar
        self._btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._btn_box.set_halign(Gtk.Align.CENTER)
        self._btn_box.set_margin_bottom(16)
        self._close_btn = Gtk.Button(label=_("Sluiten"))
        self._close_btn.add_css_class("pill")
        self._close_btn.set_size_request(140, 40)
        self._close_btn.set_sensitive(False)
        self._close_btn.connect("clicked", lambda b: self.close())
        self._relaunch_btn = Gtk.Button(label=_("Pixora starten"))
        self._relaunch_btn.add_css_class("suggested-action")
        self._relaunch_btn.add_css_class("pill")
        self._relaunch_btn.set_size_request(160, 40)
        self._relaunch_btn.set_visible(False)
        self._relaunch_btn.connect("clicked", self._on_relaunch)
        self._btn_box.append(self._close_btn)
        self._btn_box.append(self._relaunch_btn)
        page.append(self._btn_box)

        toolbar.set_content(page)
        self.set_content(toolbar)

        self._pulse_timer = GLib.timeout_add(120, self._pulse_tick)
        threading.Thread(target=self._run_update, daemon=True).start()

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

    def _pulse_tick(self):
        try:
            self.progress.pulse()
        except Exception:
            return False
        return True

    def _set_step_active(self, key, label):
        entry = self.step_rows.get(key)
        if not entry:
            return
        row, stack, spinner, check = entry
        spinner.start()
        stack.set_visible_child_name("spinner")
        self.status_lbl.set_text(_(label) + "…")

    def _set_step_done(self, key):
        entry = self.step_rows.get(key)
        if not entry:
            return
        row, stack, spinner, check = entry
        spinner.stop()
        stack.set_visible_child_name("check")
        done = sum(1 for k, (_, s, _, _) in self.step_rows.items()
                   if s.get_visible_child_name() == "check")
        total = len(self.step_rows)
        if self._pulse_timer and total > 0:
            try:
                GLib.source_remove(self._pulse_timer)
            except Exception:
                pass
            self._pulse_timer = None
        self.progress.set_fraction(done / total)
        if done < total:
            self._pulse_timer = GLib.timeout_add(120, self._pulse_tick)

    def _run_update(self):
        # Script eerst lokaal downloaden zodat de pkexec-prompt een kort,
        # leesbaar commando toont ("pkexec bash /tmp/pixora-updater.sh")
        # i.p.v. een bash -c met curl-pipe — minder eng voor de gebruiker.
        import tempfile
        script_path = None
        try:
            req = urllib.request.Request(
                UPDATE_URL, headers={"User-Agent": "Pixora-Updater/1.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                script_bytes = resp.read()
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix="pixora-updater-", suffix=".sh", delete=False
            ) as f:
                f.write(script_bytes)
                script_path = f.name
            os.chmod(script_path, 0o755)
        except Exception as e:
            GLib.idle_add(self._finish, False,
                          _("Updater-script downloaden mislukt: {err}").format(err=e))
            return

        uid = str(os.getuid())
        cmd = [
            "pkexec", "env", f"PKEXEC_UID={uid}",
            "bash", script_path,
        ]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                env={**os.environ, "TERM": "dumb"}
            )
        except FileNotFoundError:
            try:
                os.unlink(script_path)
            except Exception:
                pass
            GLib.idle_add(self._finish, False,
                          _("pkexec niet gevonden. Installeer policykit-1."))
            return

        for line in proc.stdout:
            clean = ANSI_ESCAPE.sub("", line).strip()
            m = STEP_RE.match(clean)
            if m:
                key, label = m.group(1), m.group(2)
                if clean.startswith("STEP:") and ":DONE" in clean:
                    GLib.idle_add(self._set_step_done, key)
                else:
                    GLib.idle_add(self._set_step_active, key, label)
        proc.wait()
        ok = (proc.returncode == 0)
        try:
            os.unlink(script_path)
        except Exception:
            pass
        GLib.idle_add(self._finish, ok,
                      _("Pixora is bijgewerkt.") if ok else _("Update mislukt."))

    def _finish(self, ok, message):
        if self._pulse_timer:
            try:
                GLib.source_remove(self._pulse_timer)
            except Exception:
                pass
            self._pulse_timer = None
        if ok:
            self.progress.set_fraction(1.0)
            # Mark alle steps als done
            for key in self.step_rows.keys():
                self._set_step_done(key)
        else:
            self.progress.add_css_class("error")
        self.status_lbl.set_text(message)
        self._close_btn.set_sensitive(True)
        self._relaunch_btn.set_visible(ok)
        return False

    def _on_relaunch(self, btn):
        btn.set_sensitive(False)
        self._close_btn.set_sensitive(False)
        pixora_bin = os.path.expanduser("~/.local/bin/pixora")
        if os.path.exists(pixora_bin):
            cmd = [pixora_bin]
        else:
            cmd = [sys.executable, str(INSTALL_DIR / "viewer" / "main.py")]
        try:
            subprocess.Popen(
                cmd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        except Exception:
            pass
        # Wacht 400ms voordat de updater sluit zodat de child process
        # echt losgekoppeld is en niet door onze close-signalen wordt geraakt.
        def _delayed_quit():
            self.get_application().quit()
            return False
        GLib.timeout_add(400, _delayed_quit)


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
