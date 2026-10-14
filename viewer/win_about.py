#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — win_about.py
#  Update check, release notes and license (About tab)
#  by LinuxGinger
# ─────────────────────────────────────────────

import os
import subprocess
import sys
import threading
import time
from pixora_i18n import _
from common import (Adw, Gdk, GLib, Gtk, INSTALLED_VERSION_FILE,
    LICENSE_PATH, is_newer_version, log_error, log_info, log_warn,
    _fetch_latest_release)


class AboutMixin:
    """Update check, release notes and license (About tab) (mixed into MainWindow)."""

    def _check_for_update(self):
        threading.Thread(target=self._do_update_check, daemon=True).start()
        return False

    def _do_update_check(self):
        try:
            # Dev runs have no installed_version — skip the network round-trip.
            if not os.path.exists(INSTALLED_VERSION_FILE):
                return
            with open(INSTALLED_VERSION_FILE) as f:
                local_version = f.read().strip()
            rel = _fetch_latest_release()
            if rel and is_newer_version(rel["version"], local_version):
                self._pending_update_version = rel["version"]
                self._latest_release = rel
                GLib.idle_add(self._maybe_show_update_popup)
        except Exception:
            pass

    def _maybe_show_update_popup(self):
        """Defer until home page has been visible ≥2s."""
        if self._update_dialog_shown or not self._pending_update_version:
            return False
        ready_at = getattr(self, "_home_ready_at", None)
        if ready_at is None or (time.time() - ready_at) < 2.0:
            GLib.timeout_add(500, self._maybe_show_update_popup)
            return False
        self._update_dialog_shown = True
        self._show_update_message_dialog(self._pending_update_version)
        return False

    def _start_settings_update_pulse(self):
        """Crossfade the header settings icon between the gear and the
        update-available icon to signal a pending update — a smooth fade, like
        the update button, swapping the icon at the dim point."""
        if getattr(self, "_settings_pulse_id", None):
            return
        self._settings_pulse_op = 1.0
        self._settings_pulse_dir = -1
        self._settings_pulse_update_icon = False
        # Orange tint for the update icon (display-level so it reaches the icon).
        if not getattr(self, "_settings_pulse_css", None):
            self._settings_pulse_css = Gtk.CssProvider()
            self._settings_pulse_css.load_from_string(
                ".pixora-update-flash { color: #e95420; }")
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), self._settings_pulse_css,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        def _tick():
            self._settings_pulse_op += self._settings_pulse_dir * 0.05
            if self._settings_pulse_op <= 0.2:
                self._settings_pulse_op = 0.2
                self._settings_pulse_dir = 1
                # Swap at the trough → reads as a crossfade between the two icons.
                self._settings_pulse_update_icon = not self._settings_pulse_update_icon
                try:
                    if self._settings_pulse_update_icon:
                        self.settings_btn.set_icon_name(
                            "software-update-available-symbolic")
                        self.settings_btn.add_css_class("pixora-update-flash")
                    else:
                        self.settings_btn.set_icon_name("preferences-system-symbolic")
                        self.settings_btn.remove_css_class("pixora-update-flash")
                except Exception:
                    pass
            elif self._settings_pulse_op >= 1.0:
                self._settings_pulse_op = 1.0
                self._settings_pulse_dir = -1
            try:
                self.settings_btn.set_opacity(self._settings_pulse_op)
            except Exception:
                pass
            return True

        self._settings_pulse_id = GLib.timeout_add(45, _tick)

    def _make_release_notes_widget(self, body):
        """Scrollable, read-only view of a release's notes (markdown shown as-is)."""
        label = Gtk.Label(label=body)
        label.set_wrap(True)
        label.set_xalign(0)
        label.set_yalign(0)
        label.set_selectable(True)
        label.add_css_class("body")
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(140)
        scroller.set_max_content_height(260)
        scroller.set_child(label)
        return scroller

    def _show_update_message_dialog(self, new_version):
        self._start_settings_update_pulse()
        dlg = Adw.AlertDialog(
            heading=_("Update available"),
            body=_("Pixora {v} is available. Update now?").format(v=new_version),
        )
        rel = self._latest_release
        if rel and rel.get("body"):
            dlg.set_extra_child(self._make_release_notes_widget(rel["body"]))
        dlg.add_response("later", _("Later"))
        dlg.add_response("bijwerken", _("Update"))
        dlg.set_response_appearance("bijwerken", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", self._on_update_dialog_response, new_version)
        self._present_dialog(dlg)
        return False

    def _on_update_dialog_response(self, dlg, response, new_version):
        if response == "bijwerken":
            self._open_installer()
        # On "later" we no longer show the top banner — the flashing settings
        # icon is the persistent reminder.

    def _repair_desktop_entry(self):
        """Rewrite pixora.desktop if stale. Silent — never blocks startup."""
        try:
            install_dir = os.path.expanduser("~/.local/share/pixora")
            icon = os.path.join(install_dir, "assets", "logos", "pixora-icon.svg")
            launcher = os.path.expanduser("~/.local/bin/pixora")
            desktop_dir = os.path.expanduser("~/.local/share/applications")
            desktop = os.path.join(desktop_dir, "pixora.desktop")
            if not os.path.exists(icon) or not os.path.exists(launcher):
                return  # dev-tree or not installed
            needs_write = True
            if os.path.exists(desktop):
                try:
                    with open(desktop) as _df:
                        content_existing = _df.read()
                    icon_ok = f"Icon={icon}" in content_existing and os.path.exists(icon)
                    wm_ok = "StartupWMClass=com.linuxginger.pixora" in content_existing
                    # StartupNotify=true triggers GNOME "ready" notifications; must be absent.
                    startup_notify_ok = "StartupNotify=true" not in content_existing
                    if icon_ok and wm_ok and startup_notify_ok:
                        needs_write = False
                except Exception:
                    pass
            if not needs_write:
                return
            os.makedirs(desktop_dir, exist_ok=True)
            content = (
                "[Desktop Entry]\n"
                "Name=Pixora\n"
                "GenericName=Photo & Video Manager\n"
                "GenericName[nl]=Foto & Video Manager\n"
                "GenericName[de]=Foto- & Video-Manager\n"
                "GenericName[fr]=Gestionnaire de photos et vidéos\n"
                "Comment=Photo & video manager by LinuxGinger\n"
                "Comment[nl]=Foto & video manager door LinuxGinger\n"
                "Comment[de]=Foto- & Video-Manager von LinuxGinger\n"
                "Comment[fr]=Gestionnaire de photos et vidéos par LinuxGinger\n"
                f"Exec={launcher}\n"
                f"Icon={icon}\n"
                "Terminal=false\n"
                "Type=Application\n"
                "Categories=Graphics;Photography;\n"
                "StartupWMClass=com.linuxginger.pixora\n"
            )
            with open(desktop, "w") as _df:
                _df.write(content)
            try:
                subprocess.run(
                    ["update-desktop-database", desktop_dir],
                    capture_output=True, timeout=5,
                )
            except Exception:
                pass
            log_info(_("pixora.desktop repaired: Icon={p}").format(p=icon))
        except Exception:
            pass

    def _open_installer(self):
        log_info(_("GUI updater started"))
        updater_path = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "updater.py"
        ))
        try:
            subprocess.Popen([sys.executable, updater_path],
                             start_new_session=True)
        except Exception as e:
            log_error(_("GUI updater failed to start: {err}").format(err=e))
            return
        # Use on_close, not app.quit(): quit leaks WebKit subprocess into a zombie.
        GLib.idle_add(self.close)

    def _on_open_github(self, btn):
        # DEVNULL + start_new_session: silence browser stderr and detach lifetime.
        try:
            subprocess.Popen(
                ["xdg-open", "https://github.com/Linux-Ginger/Pixora"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            log_warn(_("Failed to open GitHub: {err}").format(err=e))

    def _on_view_license(self, btn):
        """GPL-3.0 summary popup (✓ / ! / ✗) with full license text."""
        win = Adw.Window()
        win.set_title(_("License"))
        win.set_transient_for(self)
        win.set_modal(False)
        win.set_default_size(780, 760)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(Adw.HeaderBar())

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        body.set_margin_top(16)
        body.set_margin_bottom(16)
        body.set_margin_start(18)
        body.set_margin_end(18)

        heading = Gtk.Label(label=_("GNU General Public License v3.0"))
        heading.add_css_class("title-2")
        heading.set_halign(Gtk.Align.START)
        body.append(heading)

        intro = Gtk.Label(
            label=_("Pixora is free software under GPL-3.0. You may use, modify and share it — as long as you respect those same rights for others.")
        )
        intro.add_css_class("dim-label")
        intro.set_halign(Gtk.Align.START)
        intro.set_wrap(True)
        intro.set_xalign(0)
        body.append(intro)

        summary = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
            homogeneous=True,
        )
        summary.append(self._license_summary_col(
            _("Permitted"), "✓", "success",
            [
                _("Private and commercial use"),
                _("Modify the code"),
                _("Distribute (original or modified)"),
                _("Patent licenses from contributors"),
            ],
        ))
        summary.append(self._license_summary_col(
            _("Required"), "!", "warning",
            [
                _("Include source code when distributing"),
                _("Use the same GPL-3 license"),
                _("Mark modifications clearly"),
                _("Keep the copyright notice"),
            ],
        ))
        summary.append(self._license_summary_col(
            _("Not permitted"), "✗", "error",
            [
                _("Include in proprietary software"),
                _("Claim warranty (there is none)"),
                _("Hold authors liable"),
            ],
        ))
        body.append(summary)

        body.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        full_hdr = Gtk.Label(label=_("Full license text"))
        full_hdr.add_css_class("heading")
        full_hdr.set_halign(Gtk.Align.START)
        body.append(full_hdr)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_min_content_height(260)
        tv = Gtk.TextView()
        tv.set_editable(False)
        tv.set_cursor_visible(False)
        tv.set_monospace(True)
        tv.set_wrap_mode(Gtk.WrapMode.WORD)
        tv.set_left_margin(12)
        tv.set_right_margin(12)
        tv.set_top_margin(12)
        tv.set_bottom_margin(12)
        try:
            with open(LICENSE_PATH, "r", encoding="utf-8") as f:
                lic_text = f.read()
        except Exception as e:
            lic_text = _("Could not load license: {err}").format(err=e)
        tv.get_buffer().set_text(lic_text)
        scroll.set_child(tv)
        body.append(scroll)

        outer.append(body)
        win.set_content(outer)
        win.present()

    def _license_summary_col(self, title, icon_char, css_class, items):
        """Unicode icon_char avoids icon-theme dependency."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        badge = Gtk.Label(label=icon_char)
        badge.add_css_class("heading")
        badge.add_css_class(css_class)
        header.append(badge)
        hlbl = Gtk.Label(label=title)
        hlbl.add_css_class("heading")
        hlbl.set_halign(Gtk.Align.START)
        header.append(hlbl)
        box.append(header)
        for item in items:
            lbl = Gtk.Label(label="• " + item)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_wrap(True)
            lbl.set_xalign(0)
            lbl.add_css_class("caption")
            box.append(lbl)
        return box

    def _on_settings_check_update(self, btn):
        if self._update_check_state == "available":
            self._open_installer()
            return
        self._set_update_state("checking")
        threading.Thread(target=self._do_settings_update_check, daemon=True).start()

    def _set_update_state(self, state):
        """state ∈ {'idle', 'checking', 'uptodate', 'available'}."""
        self._update_check_state = state
        if self._update_check_pulse_id is not None:
            try:
                GLib.source_remove(self._update_check_pulse_id)
            except Exception:
                pass
            self._update_check_pulse_id = None
        if self._update_check_fade_id is not None:
            try:
                GLib.source_remove(self._update_check_fade_id)
            except Exception:
                pass
            self._update_check_fade_id = None

        try:
            if state == "idle":
                self._update_btn_stack.set_visible_child_name("idle")
                self._update_check_btn.set_sensitive(True)
                self._update_check_btn.set_opacity(1.0)
                self._update_check_spinner.stop()
            elif state == "checking":
                self._update_btn_stack.set_visible_child_name("checking")
                self._update_check_spinner.start()
                self._update_check_btn.set_sensitive(False)
            elif state == "uptodate":
                self._update_btn_stack.set_visible_child_name("uptodate")
                self._update_check_spinner.stop()
                self._update_check_btn.set_sensitive(True)
                self._update_check_btn.set_opacity(1.0)
                self._update_check_fade_id = GLib.timeout_add_seconds(
                    5, self._update_uptodate_fade_done
                )
            elif state == "available":
                self._update_check_spinner.stop()
                self._update_check_btn.set_sensitive(True)
                self._update_check_btn.set_opacity(1.0)
                self._update_check_btn.set_tooltip_text(
                    _("New version available — click to update")
                )
                self._update_btn_stack.set_visible_child_name("available")
                # Pulse icon/label every 1.5s.
                self._update_pulse_on = True
                self._update_check_pulse_id = GLib.timeout_add(
                    1500, self._update_pulse_tick
                )
        except Exception:
            pass

    def _update_uptodate_fade_done(self):
        self._update_check_fade_id = None
        if self._update_check_state == "uptodate":
            self._set_update_state("idle")
        return False

    def _update_pulse_tick(self):
        if self._update_check_state != "available":
            self._update_check_pulse_id = None
            return False
        # Stop once settings closed (set_visible_child_name on rootless widget → Gtk-CRITICAL).
        try:
            if self._update_check_btn.get_root() is None:
                self._update_check_pulse_id = None
                return False
        except Exception:
            self._update_check_pulse_id = None
            return False
        self._update_pulse_on = not self._update_pulse_on
        try:
            self._update_btn_stack.set_visible_child_name(
                "available" if self._update_pulse_on else "available_label"
            )
        except Exception:
            self._update_check_pulse_id = None
            return False
        return True

    def _do_settings_update_check(self):
        local_version = ""
        try:
            if os.path.exists(INSTALLED_VERSION_FILE):
                with open(INSTALLED_VERSION_FILE) as f:
                    local_version = f.read().strip()
            rel = _fetch_latest_release()
        except Exception:
            GLib.idle_add(self._settings_update_result, None, None, None)
            return
        GLib.idle_add(self._settings_update_result, local_version,
                      (rel["version"] if rel else ""), rel)

    def _settings_update_result(self, local_version, remote_version, rel):
        # Dialog may have closed → widgets disposed.
        try:
            if remote_version is None:
                self._update_check_row.set_subtitle(_("Check failed"))
                self._set_update_state("idle")
                return False
            if rel:
                self._latest_release = rel
                self._populate_changelog(rel.get("body", ""), rel.get("version", ""))
            # No release yet (remote "") reads as up to date.
            if not is_newer_version(remote_version, local_version):
                self._update_check_row.set_subtitle(_("You have the latest version"))
                self._set_update_state("uptodate")
            else:
                self._update_check_row.set_subtitle(
                    _("Version {v} available").format(v=remote_version)
                )
                self._set_update_state("available")
        except Exception:
            pass
        return False

    def _populate_changelog(self, body, version=""):
        label = getattr(self, "_changelog_label", None)
        expander = getattr(self, "_changelog_expander", None)
        if label is None or expander is None:
            return
        label.set_text(body or _("No release notes available."))
        if version:
            expander.set_subtitle(
                _("What changed in Pixora {v}").format(v=version))
        else:
            expander.set_subtitle(_("Release notes from the latest version"))

    def _load_about_changelog(self):
        """Fetch the latest release notes in the background for the About page."""
        def _work():
            try:
                rel = _fetch_latest_release()
            except Exception:
                rel = None
            GLib.idle_add(self._apply_about_changelog, rel)
        threading.Thread(target=_work, daemon=True).start()

    def _apply_about_changelog(self, rel):
        try:
            if rel and rel.get("body"):
                self._latest_release = rel
                self._populate_changelog(rel["body"], rel.get("version", ""))
            else:
                self._populate_changelog(None, "")
        except Exception:
            pass
        return False
