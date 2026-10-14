#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — win_backup.py
#  Backup to an external drive
#  by LinuxGinger
# ─────────────────────────────────────────────

import math
import os
import subprocess
import threading
import time
from pixora_i18n import _, ngettext
from common import (Adw, cairo, GLib, Gtk, log_error, log_info, log_warn,
    save_settings, _walk_rel_files)
from drives import (get_available_drives, get_mountpoint_for_uuid,
    _cmd_available_bk, _lsblk_data)
from widgets import BackupFolderPicker


class BackupMixin:
    """Backup to an external drive (mixed into MainWindow)."""

    def on_settings_backup_toggle(self, switch, _pspec):
        active = switch.get_active()
        if not active and self.settings.get("backup_enabled") \
                and self.settings.get("backup_uuid"):
            # Confirm disable (can't undo switch during signal emit).
            def _confirm():
                dlg = Adw.AlertDialog(
                    heading=_("Turn off automatic backup?"),
                    body=_("Pixora will stop backing up until you turn it back on. The current settings are preserved."),
                )
                dlg.add_response("cancel", _("Cancel"))
                dlg.add_response("disable", _("Turn off"))
                dlg.set_response_appearance("disable", Adw.ResponseAppearance.DESTRUCTIVE)
                dlg.set_close_response("cancel")
                dlg.connect("response", self._on_backup_disable_response)
                self._present_dialog(dlg)
                return False
            GLib.idle_add(_confirm)
            return
        self._apply_backup_toggle(active)

    def _apply_backup_toggle(self, active):
        drive_present = self._backup_drive_mountpoint() is not None
        self.settings_drive_row.set_sensitive(active)
        self.settings_drive_combo.set_sensitive(active and drive_present)
        self.settings_backup_folder_row.set_sensitive(active and drive_present)
        if hasattr(self, "settings_mode_backup_row"):
            self.settings_mode_backup_row.set_sensitive(active and drive_present)
            self.settings_mode_sync_row.set_sensitive(active and drive_present)
        if hasattr(self, "settings_dedup_row"):
            dup_on_now = self.settings.get("duplicate_threshold", 2) != 0
            self.settings_dedup_row.set_sensitive(active and drive_present and dup_on_now)
        if hasattr(self, "settings_silent_row"):
            self.settings_silent_row.set_sensitive(active and drive_present)
        if hasattr(self, "settings_manual_scan_row"):
            self.settings_manual_scan_row.set_sensitive(active and drive_present)
        self.settings["backup_enabled"] = active
        if active:
            if not self.settings.get("backup_uuid") and self.settings_drives:
                sel = self.settings_drive_combo.get_selected()
                if 0 <= sel < len(self.settings_drives):
                    self.settings["backup_uuid"] = self.settings_drives[sel][0]
        save_settings(self.settings)
        if active:
            GLib.idle_add(self._sync_now_if_ready)

    def _on_backup_disable_response(self, dlg, response):
        if response == "disable":
            self._apply_backup_toggle(False)
        else:
            # Revert switch without re-firing handler.
            self.settings_backup_switch.handler_block_by_func(
                self.on_settings_backup_toggle
            )
            self.settings_backup_switch.set_active(True)
            self.settings_backup_switch.handler_unblock_by_func(
                self.on_settings_backup_toggle
            )

    def on_settings_reset_drive(self, btn):
        dlg = Adw.AlertDialog(
            heading=_("Forget backup drive?"),
            body=_("The chosen drive and folder will be unlinked from Pixora. Files on the drive are left untouched."),
        )
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("reset", _("Forget"))
        dlg.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_close_response("cancel")
        dlg.connect("response", self._on_reset_drive_response)
        self._present_dialog(dlg)

    def _on_reset_drive_response(self, dlg, response):
        if response != "reset":
            return
        self.settings["backup_uuid"] = None
        self.settings["backup_label"] = None
        self.settings["backup_path"] = None
        self.settings["backup_enabled"] = False
        save_settings(self.settings)
        if hasattr(self, "settings_backup_switch"):
            self.settings_backup_switch.set_active(False)
        if hasattr(self, "settings_drive_reset_btn"):
            self.settings_drive_reset_btn.set_visible(False)
        if hasattr(self, "settings_backup_folder_row"):
            self.settings_backup_folder_row.set_subtitle(_("Not configured"))
        if hasattr(self, "settings_backup_folder_btn"):
            self.settings_backup_folder_btn.set_label(_("Set up"))
        # Rebuild drive list to drop the forgotten drive.
        if hasattr(self, "settings_drive_model"):
            while self.settings_drive_model.get_n_items() > 0:
                self.settings_drive_model.remove(0)
            self.settings_drives = self._build_settings_drive_list()
            if self.settings_drives:
                for uuid, label in self.settings_drives:
                    self.settings_drive_model.append(label)
            else:
                self.settings_drive_model.append(_("No external drives found"))

    def on_backup_dedup_toggle(self, switch, _pspec):
        self.settings["backup_dedup"] = switch.get_active()
        save_settings(self.settings)

    def on_backup_silent_toggle(self, switch, _pspec):
        active = switch.get_active()
        if active and not self.settings.get("backup_silent"):
            # Enable: confirm first, revert on cancel.
            def _confirm():
                dlg = Adw.AlertDialog(
                    heading=_("Turn on auto-confirm?"),
                    body=_("Pixora will start backup/sync immediately without a confirmation dialog when there is work to do. Only error messages will still appear."),
                )
                dlg.add_response("cancel", _("Cancel"))
                dlg.add_response("enable", _("Turn on"))
                dlg.set_response_appearance("enable", Adw.ResponseAppearance.SUGGESTED)
                dlg.set_close_response("cancel")
                dlg.connect("response", self._on_silent_confirm_response)
                self._present_dialog(dlg)
                return False
            GLib.idle_add(_confirm)
            return
        self.settings["backup_silent"] = active
        save_settings(self.settings)

    def _on_silent_confirm_response(self, dlg, response):
        if response == "enable":
            self.settings["backup_silent"] = True
            save_settings(self.settings)
        else:
            self.settings_silent_switch.handler_block_by_func(
                self.on_backup_silent_toggle
            )
            self.settings_silent_switch.set_active(False)
            self.settings_silent_switch.handler_unblock_by_func(
                self.on_backup_silent_toggle
            )

    def on_settings_manual_scan(self, btn):
        if self._backup_running or self._backup_scanning:
            log_info("Manual scan: rejected — backup/scan already running")
            return
        if not (self.settings.get("backup_enabled")
                and self.settings.get("backup_uuid")
                and self.settings.get("backup_path")):
            log_info("Manual scan: rejected — backup not configured")
            return
        if self._backup_drive_mountpoint() is None:
            log_info("Manual scan: rejected — drive not mounted")
            return
        if self._reorganize_active \
                or time.time() < self._reorganize_block_until:
            # Reorganize gate blocks even explicit user click.
            log_info("Manual scan: rejected — reorganize-gate active")
            return
        log_info(_("Backup scan started manually"))
        self._manual_scan_requested = True
        self._set_manual_scan_state("checking")
        self._trigger_backup_scan()

    def _set_manual_scan_state(self, state):
        """state: idle|checking|uptodate."""
        if not hasattr(self, "_scan_btn_stack"):
            return
        if self._scan_btn_fade_id is not None:
            try:
                GLib.source_remove(self._scan_btn_fade_id)
            except Exception:
                pass
            self._scan_btn_fade_id = None
        try:
            if state == "checking":
                self._scan_btn_stack.set_visible_child_name("checking")
                self._scan_check_spinner.start()
                self.settings_manual_scan_btn.set_sensitive(False)
            elif state == "uptodate":
                self._scan_check_spinner.stop()
                self._scan_btn_stack.set_visible_child_name("uptodate")
                self.settings_manual_scan_btn.set_sensitive(True)
                self._scan_btn_fade_id = GLib.timeout_add_seconds(
                    5, self._scan_fade_done
                )
            else:  # idle
                self._scan_check_spinner.stop()
                self._scan_btn_stack.set_visible_child_name("idle")
                self.settings_manual_scan_btn.set_sensitive(True)
        except Exception:
            pass

    def _scan_fade_done(self):
        self._scan_btn_fade_id = None
        try:
            if self._scan_btn_stack.get_root() is not None:
                self._set_manual_scan_state("idle")
        except Exception:
            pass
        return False

    def on_backup_mode_changed(self, mode, btn):
        if btn.get_active():
            self.settings["backup_mode"] = mode
            save_settings(self.settings)

    def _build_settings_drive_list(self):
        """[(uuid, label)]; includes saved drive even if disconnected."""
        drives = list(get_available_drives())
        saved_uuid = self.settings.get("backup_uuid")
        if saved_uuid and not any(u == saved_uuid for u, _l in drives):
            label = self.settings.get("backup_label") or _("Saved backup drive")
            drives.insert(0, (saved_uuid, label))
        # Persist label on first sighting.
        if saved_uuid and not self.settings.get("backup_label"):
            for u, lab in drives:
                if u == saved_uuid:
                    self.settings["backup_label"] = lab
                    try:
                        save_settings(self.settings)
                    except Exception:
                        pass
                    break
        return drives

    def on_settings_drive_selected(self, combo, _pspec):
        selected = combo.get_selected()
        if self.settings_drives and selected < len(self.settings_drives):
            new_uuid, new_label = self.settings_drives[selected]
            if new_uuid != self.settings.get("backup_uuid"):
                # Different drive → old path invalid.
                self.settings["backup_path"] = None
                self.settings_backup_folder_row.set_subtitle(_("Not configured"))
                self.settings_backup_folder_btn.set_label(_("Set up"))
            self.settings["backup_uuid"] = new_uuid
            self.settings["backup_label"] = new_label
            save_settings(self.settings)
            if hasattr(self, "settings_drive_reset_btn"):
                self.settings_drive_reset_btn.set_visible(True)

    def on_settings_refresh_drives(self, btn):
        # User asked for fresh data; bypass cache.
        _lsblk_data(force=True)
        while self.settings_drive_model.get_n_items() > 0:
            self.settings_drive_model.remove(0)
        self.settings_drives = self._build_settings_drive_list()
        backup_on = bool(self.settings.get("backup_enabled"))
        if self.settings_drives:
            for uuid, label in self.settings_drives:
                self.settings_drive_model.append(label)
        else:
            self.settings_drive_model.append(_("No external drives found"))
        saved_uuid = self.settings.get("backup_uuid")
        if saved_uuid:
            for i, (uuid, _l) in enumerate(self.settings_drives):
                if uuid == saved_uuid:
                    self.settings_drive_combo.set_selected(i)
                    break
        drive_present = self._backup_drive_mountpoint() is not None
        self.settings_drive_combo.set_sensitive(backup_on and drive_present)

    def on_settings_change_backup_folder(self, btn):
        current_uuid = self.settings.get("backup_uuid")
        mountpoint = get_mountpoint_for_uuid(current_uuid) if current_uuid else None
        if not mountpoint:
            dlg = Adw.AlertDialog(
                heading=_("No backup drive"),
                body=_("Plug in a USB drive first and select it under 'Backup drive'."),
            )
            dlg.add_response("ok", _("OK"))
            self._present_dialog(dlg)
            return
        picker = BackupFolderPicker(
            mountpoint=mountpoint,
            current_path=self.settings.get("backup_path"),
            on_selected=self._apply_backup_folder,
        )
        self._present_dialog(picker)

    def _apply_backup_folder(self, chosen):
        self.settings["backup_path"] = chosen
        save_settings(self.settings)
        self.settings_backup_folder_row.set_subtitle(chosen)
        self.settings_backup_folder_btn.set_label(_("Change"))
        GLib.idle_add(self._sync_now_if_ready)

    def _refresh_settings_drive_state(self):
        """Live-update backup section on drive attach/detach; no-op if UI unbuilt."""
        if not hasattr(self, "settings_backup_folder_row"):
            return False
        backup_on = bool(self.settings.get("backup_enabled"))
        drive_present = self._backup_drive_mountpoint() is not None
        has_uuid = bool(self.settings.get("backup_uuid"))
        try:
            self.settings_backup_folder_row.set_sensitive(backup_on and drive_present)
            self.settings_backup_folder_row.set_subtitle(
                self.settings.get("backup_path") or _("Not configured")
            )
            if hasattr(self, "settings_drive_combo"):
                self.settings_drive_combo.set_sensitive(backup_on and drive_present)
            if hasattr(self, "settings_mode_backup_row"):
                self.settings_mode_backup_row.set_sensitive(backup_on and drive_present)
                self.settings_mode_sync_row.set_sensitive(backup_on and drive_present)
            if hasattr(self, "settings_dedup_row"):
                dup_on_now = self.settings.get("duplicate_threshold", 2) != 0
                self.settings_dedup_row.set_sensitive(backup_on and drive_present and dup_on_now)
            if hasattr(self, "settings_silent_row"):
                self.settings_silent_row.set_sensitive(backup_on and drive_present)
            if hasattr(self, "settings_manual_scan_row"):
                self.settings_manual_scan_row.set_sensitive(backup_on and drive_present)
            if hasattr(self, "settings_backup_group"):
                if backup_on and has_uuid and not drive_present:
                    self.settings_backup_group.set_description(_(
                        "Drive not connected — plug in the USB drive to back up."
                    ))
                else:
                    self.settings_backup_group.set_description(
                        _("Backup to external USB drive after each import")
                    )
        except Exception:
            pass
        return False

    def _sync_now_if_ready(self):
        """Backup-diff scan when settings complete + drive present."""
        if self._backup_running or self._backup_scanning:
            return False
        if not (self.settings.get("backup_enabled")
                and self.settings.get("backup_uuid")
                and self.settings.get("backup_path")):
            return False
        if self._backup_drive_mountpoint() is None:
            self._show_backup_pending_banner()
            return False
        self._trigger_backup_scan()
        return False

    def _trigger_backup_scan(self):
        if self._backup_running or self._backup_scanning:
            return
        # Reorganize takes priority; skip if active or within 10s cooldown.
        if self._reorganize_active or time.time() < self._reorganize_block_until:
            return
        self._backup_scanning = True
        self._backup_scan_phase = 0.0
        # No percentage: earlier time-guess was misleading.
        self._backup_detail = _("This may take a while on a USB drive")
        tip = _("Scanning for new photos…")
        if hasattr(self, "_backup_donut_btn"):
            self._set_donuts_visible(True)
            self._backup_donut_btn.set_tooltip_text(tip)
        if hasattr(self, "_viewer_donut_btn"):
            self._viewer_donut_btn.set_tooltip_text(tip)
        if self._backup_scan_anim_id is None:
            self._backup_scan_anim_id = GLib.timeout_add(120, self._tick_backup_scan)
        log_info(_("Backup scan started"))
        threading.Thread(target=self._backup_scan_thread, daemon=True).start()

    def _tick_backup_scan(self):
        if (not self._backup_scanning
                and not self._structure_scanning
                and not self._backup_deduping
                and not self._orphan_reviewing):
            self._backup_scan_anim_id = None
            return False
        self._backup_scan_phase = (self._backup_scan_phase + 0.18) % (2 * math.pi)
        if hasattr(self, "_backup_donut"):
            self._redraw_donuts()
        return True

    def _backup_scan_thread(self):
        from pathlib import Path as _P
        photo_path = _P(self.settings.get("photo_path") or _P.home() / "Photos")
        backup_path_str = self.settings.get("backup_path")
        drive_root = self._backup_drive_mountpoint()
        if not drive_root:
            GLib.idle_add(self._handle_scan_result, None)
            return
        backup_dest = _P(backup_path_str) if backup_path_str else drive_root / "Pixora"
        try:
            backup_dest.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        mode = self.settings.get("backup_mode", "backup")

        # Python set-diff on rel paths (rsync --dry-run blocks minutes on FAT/exFAT).
        # Dedup runs in backup thread so scan stays fast.
        src_files = {}
        try:
            for sf, rel in _walk_rel_files(photo_path):
                try:
                    src_files[rel] = os.path.getsize(sf)
                except OSError:
                    src_files[rel] = 0
        except Exception as e:
            log_error(_("Scan error: {err}").format(err=e))
            GLib.idle_add(self._handle_scan_result, None)
            return

        dest_rels = set()
        if backup_dest.is_dir():
            try:
                dest_rels = {rel for _fp, rel in _walk_rel_files(backup_dest)}
            except Exception:
                pass  # partial walk → missing treated as "new"

        src_rels = set(src_files.keys())
        to_transfer_rels = sorted(src_rels - dest_rels)
        orphan_rels = sorted(dest_rels - src_rels)
        new_count = len(to_transfer_rels)
        delete_count = len(orphan_rels)
        bytes_to_transfer = sum(src_files[rel] for rel in to_transfer_rels)

        GLib.idle_add(
            self._handle_scan_result,
            {
                "new": new_count,
                "bytes": bytes_to_transfer,
                "delete": delete_count,
                "orphans": orphan_rels,
                "dup_count": 0,
                "excluded": [],
                "mode": mode,
            },
        )

    def _handle_scan_result(self, result):
        self._backup_scanning = False
        if hasattr(self, "_backup_donut"):
            self._redraw_donuts()
        # Defer donut visibility — else it flickers between scan-end and backup-start.
        manual_requested = self._manual_scan_requested
        self._manual_scan_requested = False
        # The dev "Check now" button runs update-style: no confirmation dialog,
        # just back up if there's work to do.
        silent = bool(self.settings.get("backup_silent")) or manual_requested
        if result is None:
            log_warn(_("Backup scan did not complete"))
            if hasattr(self, "_backup_donut_btn"):
                self._set_donuts_visible(self._backup_running)
            self._set_manual_scan_state("idle")
            return False
        new_count = result["new"]
        delete_count = result["delete"]
        bytes_to_transfer = result["bytes"]
        dup_count = result.get("dup_count", 0)
        mode = result.get("mode", self.settings.get("backup_mode", "backup"))
        # Remember orphans: backup-mode uses count for post-review, sync uses rels.
        orphans = result.get("orphans", [])
        self._last_scan_orphan_count = delete_count if mode == "backup" else 0
        self._last_scan_orphan_rels = list(orphans)
        # Remember backup_dest for orphan review.
        drive_root = self._backup_drive_mountpoint()
        if drive_root:
            from pathlib import Path as _P
            backup_path_str = self.settings.get("backup_path")
            self._last_scan_backup_dest = (
                _P(backup_path_str) if backup_path_str
                else drive_root / "Pixora"
            )

        # Backup-mode orphans are informational (kept); ignore for "synced" check.
        actionable_delete = delete_count if mode == "sync" else 0

        if new_count == 0 and actionable_delete == 0 and dup_count == 0:
            self.settings["last_backup_time"] = time.time()
            try:
                save_settings(self.settings)
            except Exception:
                pass
            self._hide_backup_pending_banner()
            log_info(_("Backup scan: everything in sync"))
            if hasattr(self, "_backup_donut_btn"):
                self._set_donuts_visible(self._backup_running)
            # Surface backup-mode orphans on manual click.
            if delete_count > 0 and mode == "backup" and manual_requested:
                if (self.settings.get("backup_dedup")
                        and self._last_scan_orphan_rels):
                    log_info(_("Manual scan: orphan analysis started ({n} orphans)").format(n=delete_count))
                    threading.Thread(
                        target=self._review_orphans_thread, daemon=True
                    ).start()
                else:
                    self._show_orphans_only_dialog(delete_count)
            elif not silent and manual_requested:
                dlg = Adw.AlertDialog(
                    heading=_("Already in sync"),
                    body=_("Your USB drive already has the same photos as Pixora."),
                )
                dlg.add_response("ok", _("OK"))
                dlg.set_default_response("ok")
                dlg.set_close_response("ok")
                self._present_dialog(dlg)
            self._set_manual_scan_state("uptodate")
            return False
        log_info(_("Backup scan: {n} new, {d} orphans (mode={m}), {b} bytes, {u} duplicates").format(
            n=new_count, d=delete_count, m=mode,
            b=bytes_to_transfer, u=dup_count,
        ))
        # Silent-mode: start backup w/o dialog even on manual check.
        if silent:
            log_info(_("Silent mode: backup starts automatically without dialog"))
            if hasattr(self, "_backup_donut_btn"):
                self._set_donuts_visible(True)
            self.start_backup()
            self._set_manual_scan_state("idle")
            return False
        # Non-silent: hide donut.
        if hasattr(self, "_backup_donut_btn"):
            self._set_donuts_visible(self._backup_running)
        self._show_backup_scan_dialog(
            new_count, delete_count, bytes_to_transfer, dup_count, mode
        )
        self._set_manual_scan_state("idle")
        return False

    def _show_orphans_only_dialog(self, orphan_count):
        dlg = Adw.AlertDialog(
            heading=_("Everything in Pixora is on the USB"),
            body=ngettext(
                "There is {n} photo on the USB that is no longer in Pixora. In Backup mode it stays as an archive.",
                "There are {n} photos on the USB that are no longer in Pixora. In Backup mode they stay as an archive.",
                orphan_count,
            ).format(n=orphan_count),
        )
        dlg.add_response("ok", _("OK"))
        dlg.set_default_response("ok")
        dlg.set_close_response("ok")
        self._present_dialog(dlg)

    def _format_bytes(self, n):
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                if unit in ("B", "KB"):
                    return f"{int(n)} {unit}"
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    def _format_eta(self, bytes_total, bytes_per_sec=30 * 1024 * 1024):
        if bytes_total <= 0:
            return _("less than a minute")
        secs = bytes_total / bytes_per_sec
        if secs < 60:
            return _("less than a minute")
        mins = int(secs / 60 + 0.5)
        if mins < 60:
            return ngettext("± {n} minute", "± {n} minutes", mins).format(n=mins)
        hours = mins // 60
        rem = mins % 60
        if rem == 0:
            return ngettext("± {n} hour", "± {n} hours", hours).format(n=hours)
        return _("± {h} h {m} min").format(h=hours, m=rem)

    def _show_backup_scan_dialog(self, new_count, delete_count, bytes_to_transfer,
                                 dup_count=0, mode="backup"):
        if self._backup_scan_dialog_open:
            return
        # Defer during startup (home-grid <2s) so popup avoids half-loaded UI.
        ready_at = getattr(self, "_home_ready_at", None)
        if ready_at is None or (time.time() - ready_at) < 2.0:
            GLib.timeout_add(500, self._show_backup_scan_dialog,
                             new_count, delete_count, bytes_to_transfer,
                             dup_count, mode)
            return
        self._backup_scan_dialog_open = True
        lines = []
        if new_count > 0:
            lines.append(ngettext(
                "{n} new photo (±{s})",
                "{n} new photos (±{s})",
                new_count,
            ).format(n=new_count, s=self._format_bytes(bytes_to_transfer)))
        if delete_count > 0:
            if mode == "sync":
                lines.append(ngettext(
                    "{n} stale file will be removed from USB",
                    "{n} stale files will be removed from USB",
                    delete_count,
                ).format(n=delete_count))
            else:
                # Backup-mode: orphans kept, informational only.
                lines.append(ngettext(
                    "{n} photo on USB no longer in Pixora (kept)",
                    "{n} photos on USB no longer in Pixora (kept)",
                    delete_count,
                ).format(n=delete_count))
        if dup_count > 0:
            lines.append(ngettext(
                "{n} duplicate will be skipped",
                "{n} duplicates will be skipped",
                dup_count,
            ).format(n=dup_count))
        lines.append(_("Estimated time: {eta}").format(
            eta=self._format_eta(bytes_to_transfer)
        ))
        body = "\n".join(lines)
        if mode == "sync":
            heading = _("Sync ready to start")
            now_label = _("Sync now")
        else:
            heading = _("Backup ready to start")
            now_label = _("Back up now")
        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.add_response("later", _("Later"))
        dlg.add_response("now", now_label)
        dlg.set_response_appearance("now", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("now")
        dlg.set_close_response("later")
        dlg.connect("response", self._on_scan_dialog_response)
        self._present_dialog(dlg)

    def _on_scan_dialog_response(self, dlg, response):
        self._backup_scan_dialog_open = False
        if response == "now":
            self.start_backup()
        else:
            self._show_backup_pending_banner()

    def _is_backup_pending(self):
        """True if import happened since last backup."""
        if not self.settings.get("backup_enabled"):
            return False
        if not self.settings.get("backup_uuid"):
            return False
        last_import = self.settings.get("last_import_time", 0) or 0
        last_backup = self.settings.get("last_backup_time", 0) or 0
        return last_import > last_backup

    def _backup_drive_mountpoint(self):
        """Path of backup drive when attached, else None."""
        uuid = self.settings.get("backup_uuid")
        if not uuid:
            return None
        mp = get_mountpoint_for_uuid(uuid)
        if not mp:
            return None
        try:
            from pathlib import Path as _P
            return _P(mp)
        except Exception:
            return None

    def _check_pending_backup(self):
        """Scan after import/drive-attach; scan opens dialog on non-empty diff."""
        if self._backup_running or self._backup_scanning:
            return False
        if not (self.settings.get("backup_enabled")
                and self.settings.get("backup_uuid")
                and self.settings.get("backup_path")):
            return False
        drive = self._backup_drive_mountpoint()
        if drive:
            self._trigger_backup_scan()
        else:
            self._show_backup_pending_banner()
        return False

    def _present_dialog(self, dlg):
        """Parent popup on settings dialog if open, else self.
        Never call self.present() — GNOME Shell fires a "ready" notification."""
        parent = self._settings_dialog if self._settings_dialog is not None else self
        try:
            dlg.present(parent)
        except Exception:
            try:
                dlg.present(self)
            except Exception:
                pass

    def _show_backup_pending_banner(self):
        # Banner disabled — feedback lives in settings UI now.
        try:
            self.backup_pending_banner.set_revealed(False)
        except Exception:
            pass

    def _hide_backup_pending_banner(self):
        try:
            self.backup_pending_banner.set_revealed(False)
        except Exception:
            pass

    def _periodic_scan(self):
        """60s tick: structure-scan first, then backup-scan via callback."""
        try:
            ready_at = getattr(self, "_home_ready_at", None)
            if ready_at is None or (time.time() - ready_at) < 2.0:
                return True
            if self._reorganize_active \
                    or time.time() < self._reorganize_block_until:
                return True
            if self._structure_scanning \
                    or self._backup_scanning or self._backup_running \
                    or self._heic_converting:
                return True
            self._trigger_structure_scan()
        except Exception:
            pass
        return True

    def _poll_backup_drive(self):
        """Drive attach → scan for diff; detach → grey settings + banner."""
        # Force-refresh lsblk so cache can't lie about attach state.
        _lsblk_data(force=True)
        drive_now = self._backup_drive_mountpoint() is not None
        just_attached = drive_now and not self._backup_drive_last_seen
        just_detached = (not drive_now) and self._backup_drive_last_seen
        self._backup_drive_last_seen = drive_now
        backup_configured = bool(
            self.settings.get("backup_enabled")
            and self.settings.get("backup_uuid")
            and self.settings.get("backup_path")
        )
        if just_attached and backup_configured \
                and not self._backup_running \
                and not self._backup_scanning:
            GLib.idle_add(self._trigger_backup_scan)
        if (not drive_now and self._is_backup_pending()
                and not self._backup_running):
            self._show_backup_pending_banner()
        if just_attached or just_detached:
            GLib.idle_add(self._refresh_settings_drive_state)
        return True

    def _on_close_guard_response(self, dlg, response):
        if response == "close":
            self._close_confirmed = True
            self.close()

    def start_backup(self):
        if self._backup_running:
            return
        self._backup_running = True
        self._backup_fraction = 0.0
        self._backup_detail = ""
        self._hide_backup_pending_banner()
        log_info(_("Backup started"))
        threading.Thread(target=self._backup_thread, daemon=True).start()

    def _check_backup_dest_writable(self, drive_root, backup_dest):
        """None if writable, else error with hint. Tries dest then drive root."""
        candidates = [p for p in (backup_dest, drive_root)
                      if p is not None and os.path.isdir(str(p))]
        if not candidates:
            return None
        test_dir = str(candidates[0])
        test_path = os.path.join(test_dir, f".pixora-write-test-{os.getpid()}")
        try:
            with open(test_path, "wb") as f:
                f.write(b"ok")
            os.remove(test_path)
            return None
        except OSError as exc:
            if exc.errno == 30:  # EROFS
                return _(
                    "Backup drive is mounted read-only. This often happens with NTFS/exFAT after an unsafe eject on Windows.\n\nFix: unmount the drive in your file manager and reconnect it, or run in a terminal:\n  sudo ntfsfix /dev/sdXN\n(replace sdXN with your USB device, e.g. sdb1)"
                )
            if exc.errno == 13:  # EACCES
                return _("No write permission on {p}: {err}").format(
                    p=test_dir, err=exc
                )
            return _("Error writing to {p}: {err}").format(
                p=test_dir, err=exc
            )

    def _dedup_for_backup(self, photo_path, backup_dest):
        """Relative paths to exclude from backup (visual dupes already on USB)."""
        if not self.settings.get("backup_dedup"):
            return []

        # Dedup only on files not already on USB.
        try:
            src_rels = {rel for _fp, rel in _walk_rel_files(photo_path)}
        except Exception:
            return []
        dest_rels = set()
        if backup_dest.is_dir():
            try:
                dest_rels = {rel for _fp, rel in _walk_rel_files(backup_dest)}
            except Exception:
                pass
        to_check = sorted(src_rels - dest_rels)
        if not to_check or not dest_rels:
            return []

        # 10% threshold: skip pHash unless USB has substantial lib.
        if len(dest_rels) * 10 < len(src_rels):
            log_info(_("Duplicate check skipped: USB is too empty ({d} photos vs {s} in Pixora)").format(
                d=len(dest_rels), s=len(src_rels)))
            return []

        log_info(_("Duplicate check: {n} new photos against {u} on USB").format(n=len(to_check), u=len(dest_rels)))
        try:
            from importer_page import (
                perceptual_hash, build_library_hashes, find_duplicate,
                SUPPORTED_EXT,
            )
        except Exception as e:
            log_warn(_("Dedup check skipped: {err}").format(err=e))
            return []

        self._backup_deduping = True

        def _start_dedup_ui():
            tip = _("Duplicate check…")
            if hasattr(self, "_backup_donut_btn"):
                try:
                    self._set_donuts_visible(True)
                    self._backup_donut_btn.set_tooltip_text(tip)
                except Exception:
                    pass
            if hasattr(self, "_viewer_donut_btn"):
                try:
                    self._viewer_donut_btn.set_tooltip_text(tip)
                except Exception:
                    pass
            # Reuse scan animation for donut.
            if self._backup_scan_anim_id is None:
                self._backup_scan_anim_id = GLib.timeout_add(
                    80, self._tick_backup_scan)
            return False
        GLib.idle_add(_start_dedup_ui)

        excluded = []
        try:
            usb_hashes = build_library_hashes(backup_dest)
            MAX_DIST = 2
            total = len(to_check)
            for i, rel in enumerate(to_check):
                if i % 25 == 0:
                    msg = _("Duplicate check: {c} / {t}").format(c=i, t=total)
                    GLib.idle_add(self._set_dedup_detail, msg)
                src_file = photo_path / rel
                if not src_file.is_file():
                    continue
                if src_file.suffix.lower() not in SUPPORTED_EXT:
                    continue
                ph = perceptual_hash(src_file)
                if ph and find_duplicate(ph, usb_hashes, MAX_DIST):
                    excluded.append(rel)
        except Exception as e:
            log_warn(_("Dedup check skipped: {err}").format(err=e))
        finally:
            self._backup_deduping = False
            GLib.idle_add(self._set_dedup_detail, "")
        if excluded:
            log_info(_("Duplicate check done: {n} duplicates will be skipped").format(n=len(excluded)))
        return excluded

    def _set_dedup_detail(self, text):
        """Thread-safe popover subtitle during dedup."""
        self._backup_detail = text or ""
        if hasattr(self, "_backup_donut"):
            self._redraw_donuts()
        return False

    def _backup_thread(self):
        from pathlib import Path as _P
        photo_path = _P(self.settings.get("photo_path") or _P.home() / "Photos")
        backup_uuid = self.settings.get("backup_uuid")
        backup_path_str = self.settings.get("backup_path")
        drive_root = self._backup_drive_mountpoint()
        if not drive_root:
            GLib.idle_add(self._backup_finished, False, _("Backup drive not found."))
            return
        backup_dest = _P(backup_path_str) if backup_path_str else drive_root / "Pixora"
        # NTFS/exFAT read-only after unsafe eject: warn early instead of rsync crash.
        ro_msg = self._check_backup_dest_writable(drive_root, backup_dest)
        if ro_msg is not None:
            GLib.idle_add(self._backup_finished, False, ro_msg)
            return
        try:
            backup_dest.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            GLib.idle_add(self._backup_finished, False, _("Error: {err}").format(err=e))
            return

        def _on_rsync_line(line):
            for part in line.split():
                if part.endswith("%"):
                    try:
                        frac = int(part[:-1]) / 100.0
                        GLib.idle_add(self._update_backup_progress, frac, part)
                    except ValueError:
                        pass

        mode = self.settings.get("backup_mode", "backup")
        # Dedup before rsync: scan dialog stays instant; pHash shows donut progress.
        excluded = self._dedup_for_backup(photo_path, backup_dest)
        exclude_file = None
        success = False
        if _cmd_available_bk("rsync"):
            proc = None
            try:
                rsync_cmd = ["rsync", "-a", "--info=progress2",
                             "--modify-window=2"]
                if mode == "sync":
                    rsync_cmd.append("--delete")
                if excluded:
                    import tempfile
                    exclude_file = tempfile.NamedTemporaryFile(
                        mode="w", suffix=".rsync-exclude", delete=False
                    )
                    for rel in excluded:
                        exclude_file.write(f"/{rel}\n")
                    exclude_file.close()
                    rsync_cmd += [f"--exclude-from={exclude_file.name}"]
                rsync_cmd += [str(photo_path) + "/", str(backup_dest) + "/"]
                proc = subprocess.Popen(
                    rsync_cmd,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True
                )
                self._backup_proc = proc
                # Drain stderr on its own thread: reading stdout first and stderr
                # after can deadlock when rsync fills the stderr pipe buffer.
                _err_chunks = []
                _err_thread = threading.Thread(
                    target=lambda: _err_chunks.extend(proc.stderr or []),
                    daemon=True)
                _err_thread.start()
                for line in proc.stdout:
                    _on_rsync_line(line)
                proc.wait(timeout=3600)
                _err_thread.join(timeout=5)
                success = proc.returncode == 0
                rsync_err = "".join(_err_chunks).strip()
            except Exception as e:
                log_error(_("Backup error: {err}").format(err=e))
                success = False
                rsync_err = str(e)
            finally:
                if proc is not None and proc.poll() is None:
                    try:
                        proc.kill()
                        proc.wait(timeout=5)
                    except Exception:
                        pass
                self._backup_proc = None
                if exclude_file is not None:
                    try:
                        os.unlink(exclude_file.name)
                    except OSError:
                        pass
        else:
            success = self._manual_backup(
                photo_path, backup_dest, mode == "sync", excluded=set(excluded)
            )
            rsync_err = ""

        note = None
        if not success:
            err_low = (rsync_err or "").lower()
            if "read-only" in err_low or "readonly" in err_low \
                    or "permission denied" in err_low:
                note = _(
                    "USB is mounted read-only. Unmount and reconnect it, or run `sudo ntfsfix /dev/sdXN` in a terminal."
                )
            elif rsync_err:
                # First line of rsync stderr = real reason.
                first = rsync_err.splitlines()[0] if rsync_err else ""
                note = _("Backup failed: {err}").format(err=first[:200])
            else:
                note = _("Backup partially failed.")
        GLib.idle_add(self._backup_finished, success, note)

    def _manual_backup(self, src, dst, delete_extraneous=False, excluded=None):
        """Fallback manual copy; delete_extraneous=sync mode; excluded=dedup skips."""
        excluded = excluded or set()
        try:
            all_src = list(_walk_rel_files(src))
            total = len(all_src)
            src_rels = set()
            for i, (sf, rel) in enumerate(all_src):
                src_rels.add(rel)
                if rel in excluded:
                    continue
                df = os.path.join(str(dst), rel)
                os.makedirs(os.path.dirname(df), exist_ok=True)
                if not os.path.exists(df):
                    import shutil as _sh
                    _sh.copy2(sf, df)
                frac = (i + 1) / total if total > 0 else 1.0
                GLib.idle_add(self._update_backup_progress, frac,
                              f"{i + 1} / {total}")
            if delete_extraneous:
                for df, rel in _walk_rel_files(dst):
                    if rel not in src_rels:
                        try:
                            os.remove(df)
                        except Exception:
                            pass
            return True
        except Exception as e:
            log_error(_("Backup error: {err}").format(err=e))
            return False

    def _update_backup_progress(self, fraction, detail):
        self._backup_fraction = max(0.0, min(1.0, fraction))
        self._backup_detail = detail or ""
        if hasattr(self, "_backup_donut"):
            self._redraw_donuts()
        if hasattr(self, "_backup_donut_btn"):
            try:
                self._set_donuts_visible(self._backup_running)
                tip = _("Backup: {pct}%").format(
                    pct=int(self._backup_fraction * 100)
                )
                self._backup_donut_btn.set_tooltip_text(tip)
                if hasattr(self, "_viewer_donut_btn"):
                    self._viewer_donut_btn.set_tooltip_text(tip)
            except Exception:
                pass
        return False

    def _set_donuts_visible(self, visible):
        """Sync header + viewer-overlay donut visibility."""
        for name in ("_backup_donut_btn", "_viewer_donut_btn"):
            btn = getattr(self, name, None)
            if btn is not None:
                try:
                    btn.set_visible(bool(visible))
                except Exception:
                    pass

    def _redraw_donuts(self):
        for name in ("_backup_donut", "_viewer_donut"):
            area = getattr(self, name, None)
            if area is not None:
                try:
                    area.queue_draw()
                except Exception:
                    pass

    def _draw_backup_donut(self, area, cr, w, h):
        """Pie chart 0°→progress; scanning = rotating quarter-arc."""
        try:
            cx = w / 2
            cy = h / 2
            r_outer = min(w, h) / 2 - 1
            r_inner = r_outer * 0.55

            cr.set_source_rgba(0.5, 0.5, 0.5, 0.3)
            cr.arc(cx, cy, r_outer, 0, 2 * math.pi)
            cr.arc_negative(cx, cy, r_inner, 2 * math.pi, 0)
            cr.fill()

            # Orange = backup (kept recognizable); dark blue = routine
            # maintenance (structure check + HEIC conversion).
            if (self._backup_scanning or self._backup_running
                    or self._orphan_reviewing):
                color = (0.914, 0.329, 0.125)  # orange
            else:
                color = (0.10, 0.20, 0.55)  # dark blue

            if (self._backup_scanning or self._structure_scanning
                    or self._backup_deduping or self._orphan_reviewing):
                start = self._backup_scan_phase - math.pi / 2
                end = start + math.pi / 2
                cr.set_source_rgb(*color)
                cr.move_to(cx, cy)
                cr.arc(cx, cy, r_outer, start, end)
                cr.close_path()
                cr.fill()
                cr.set_operator(cairo.OPERATOR_CLEAR)
                cr.arc(cx, cy, r_inner, 0, 2 * math.pi)
                cr.fill()
                cr.set_operator(cairo.OPERATOR_OVER)
                return

            # Progress: reorganize wins priority.
            if self._reorganize_moving:
                frac = max(0.0, min(1.0, self._reorganize_fraction))
            elif self._heic_converting:
                frac = max(0.0, min(1.0, self._heic_fraction))
            else:
                frac = max(0.0, min(1.0, self._backup_fraction))
            if frac > 0.001:
                start = -math.pi / 2  # 12 o'clock origin
                end = start + frac * 2 * math.pi
                cr.set_source_rgb(*color)
                cr.move_to(cx, cy)
                cr.arc(cx, cy, r_outer, start, end)
                cr.close_path()
                cr.fill()
                cr.set_operator(cairo.OPERATOR_CLEAR)
                cr.arc(cx, cy, r_inner, 0, 2 * math.pi)
                cr.fill()
                cr.set_operator(cairo.OPERATOR_OVER)
        except Exception:
            pass

    def _on_backup_donut_clicked(self, btn):
        """Live-updating popover; labels stored as attrs for in-place refresh."""
        pop = Gtk.Popover()
        pop.set_parent(btn)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8); box.set_margin_bottom(8)
        box.set_margin_start(12); box.set_margin_end(12)
        self._donut_pop_title = Gtk.Label()
        self._donut_pop_title.add_css_class("heading")
        self._donut_pop_title.set_halign(Gtk.Align.START)
        box.append(self._donut_pop_title)
        self._donut_pop_pct = Gtk.Label()
        self._donut_pop_pct.set_halign(Gtk.Align.START)
        box.append(self._donut_pop_pct)
        self._donut_pop_detail = Gtk.Label()
        self._donut_pop_detail.add_css_class("caption")
        self._donut_pop_detail.add_css_class("dim-label")
        self._donut_pop_detail.set_halign(Gtk.Align.START)
        box.append(self._donut_pop_detail)
        pop.set_child(box)
        self._donut_popover = pop
        self._refresh_donut_popover()
        # 250ms refresh while visible.
        self._donut_pop_tick_id = GLib.timeout_add(
            250, self._refresh_donut_popover)
        def _on_closed(_p):
            tid = getattr(self, "_donut_pop_tick_id", None)
            if tid is not None:
                try:
                    GLib.source_remove(tid)
                except Exception:
                    pass
                self._donut_pop_tick_id = None
            self._donut_popover = None
        pop.connect("closed", _on_closed)
        pop.popup()

    def _refresh_donut_popover(self):
        pop = getattr(self, "_donut_popover", None)
        if pop is None or not hasattr(self, "_donut_pop_title"):
            return False
        is_sync = self.settings.get("backup_mode", "backup") == "sync"
        # Pick title/fields by what's running.
        if self._reorganize_moving:
            title = _("Updating folder structure")
            pct_val = int(max(0.0, min(1.0, self._reorganize_fraction)) * 100)
            pct = f"{pct_val}%"
            detail = self._reorganize_current_name or ""
        elif self._heic_converting:
            title = _("Converting HEIC to JPEG…")
            pct = f"{int(max(0.0, min(1.0, self._heic_fraction)) * 100)}%"
            detail = (self._heic_detail or "").strip()
        elif self._structure_scanning:
            title = _("Checking folder structure…")
            pct = ""
            detail = ""
        elif self._backup_deduping:
            title = _("Duplicate check…")
            pct = ""
            detail = (self._backup_detail or "").strip()
        elif self._orphan_reviewing:
            title = _("Analyzing for duplicate copies…")
            pct = ""
            detail = (self._backup_detail or "").strip()
        elif self._backup_running and not self._backup_scanning:
            title = _("Sync in progress") if is_sync else _("Backup in progress")
            pct = f"{int(self._backup_fraction * 100)}%"
            d = (self._backup_detail or "").strip()
            detail = "" if (d and d.rstrip("%").strip().isdigit()) else d
        elif self._backup_scanning:
            title = _("Scanning sync…") if is_sync else _("Scanning backup…")
            pct = ""
            detail = (self._backup_detail or "").strip()
            if detail and detail.rstrip("%").strip().isdigit():
                detail = ""
        else:
            title = _("Sync in progress") if is_sync else _("Backup in progress")
            pct = ""
            detail = ""
        self._donut_pop_title.set_text(title)
        self._donut_pop_pct.set_text(pct)
        self._donut_pop_pct.set_visible(bool(pct))
        self._donut_pop_detail.set_text(detail)
        self._donut_pop_detail.set_visible(bool(detail))
        return True

    def _backup_finished(self, success, note):
        self._backup_running = False
        if success:
            self.settings["last_backup_time"] = time.time()
            try:
                save_settings(self.settings)
            except Exception:
                pass
            log_info(_("Backup complete"))
        else:
            log_warn(_("Backup failed: {note}").format(note=note or ""))
        if hasattr(self, "_backup_donut_btn"):
            try:
                self._set_donuts_visible(False)
            except Exception:
                pass
        self._show_backup_done_banner(success, note)
        return False

    def _show_backup_done_banner(self, success, note):
        # Banner disabled — done feedback is now a popup.
        try:
            self.backup_done_banner.set_revealed(False)
        except Exception:
            pass
        orphan_count = self._last_scan_orphan_count
        mode = self.settings.get("backup_mode", "backup")
        dedup_on = bool(self.settings.get("backup_dedup"))
        has_orphans = (success and mode == "backup" and orphan_count > 0
                       and self._last_scan_orphan_rels)

        # Orphans + dedup → pHash analysis + choice dialog. No dedup → info popup.
        if has_orphans and dedup_on:
            log_info(_("Backup complete, orphan analysis started ({n} orphans)").format(n=orphan_count))
            threading.Thread(
                target=self._review_orphans_thread, daemon=True
            ).start()
            return

        # Silent-mode: skip success popup, keep errors. Orphans still warn.
        if success and self.settings.get("backup_silent"):
            if has_orphans:
                log_info(_("Silent mode: backup complete, {n} orphans reported").format(n=orphan_count))
                dlg = Adw.AlertDialog(
                    heading=_("Backup complete — note"),
                    body=ngettext(
                        "There is {n} photo on your USB that is not in Pixora (e.g. a manually copied folder). It is kept as an archive.",
                        "There are {n} photos on your USB that are not in Pixora (e.g. a manually copied folder). They are kept as an archive.",
                        orphan_count,
                    ).format(n=orphan_count),
                )
                dlg.add_response("ok", _("OK"))
                dlg.set_default_response("ok")
                dlg.set_close_response("ok")
                self._present_dialog(dlg)
                return
            log_info(_("Silent mode: backup completed without popup"))
            return

        if success:
            if mode == "sync":
                heading = _("Sync complete")
                body = _("Your USB drive is now identical to Pixora.")
            elif has_orphans:
                heading = _("Backup complete — note")
                body = ngettext(
                    "All photos from Pixora are on the USB. Note: there is {n} photo on your USB that is not in Pixora (kept as an archive).",
                    "All photos from Pixora are on the USB. Note: there are {n} photos on your USB that are not in Pixora (kept as an archive).",
                    orphan_count,
                ).format(n=orphan_count)
            else:
                heading = _("Backup complete")
                body = _("All photos are on the USB drive.")
            dlg = Adw.AlertDialog(heading=heading, body=body)
        else:
            if mode == "sync":
                heading = _("Sync failed")
            else:
                heading = _("Backup failed")
            dlg = Adw.AlertDialog(
                heading=heading,
                body=note or _("Unknown error."),
            )
        dlg.add_response("ok", _("OK"))
        dlg.set_default_response("ok")
        dlg.set_close_response("ok")
        self._present_dialog(dlg)

    def _categorize_orphans(self, photo_path, backup_dest, orphan_rels,
                            progress_cb=None):
        """pHash-match orphans vs library → (dup_rels, unique_rels).
        progress_cb(cur, total, phase); errors default everything to unique."""
        try:
            from importer_page import (
                perceptual_hash, build_library_hashes, find_duplicate,
                SUPPORTED_EXT,
            )
        except Exception as e:
            log_warn(_("Orphan analysis skipped: {err}").format(err=e))
            return [], list(orphan_rels)

        # Throttle progress to every 25 for calm UI.
        def _build_progress(i, total, _name):
            if progress_cb and i % 25 == 0:
                try:
                    progress_cb(i, total, "build")
                except Exception:
                    pass
        try:
            src_hashes = build_library_hashes(
                photo_path, progress_cb=_build_progress)
        except Exception as e:
            log_warn(_("Orphan analysis skipped: {err}").format(err=e))
            return [], list(orphan_rels)

        dup_rels, unique_rels = [], []
        MAX_DIST = 2
        total = len(orphan_rels)
        for i, rel in enumerate(orphan_rels):
            if progress_cb and i % 25 == 0:
                try:
                    progress_cb(i, total, "check")
                except Exception:
                    pass
            orph_file = backup_dest / rel
            if not orph_file.is_file():
                continue
            if orph_file.suffix.lower() not in SUPPORTED_EXT:
                unique_rels.append(rel)
                continue
            ph = perceptual_hash(orph_file)
            if ph and find_duplicate(ph, src_hashes, MAX_DIST):
                dup_rels.append(rel)
            else:
                unique_rels.append(rel)
        return dup_rels, unique_rels

    def _start_orphan_review_ui(self):
        """Donut spinner + tooltip for pHash phase."""
        tip = _("Analyzing for duplicate copies…")
        if hasattr(self, "_backup_donut_btn"):
            try:
                self._set_donuts_visible(True)
                self._backup_donut_btn.set_tooltip_text(tip)
            except Exception:
                pass
        if hasattr(self, "_viewer_donut_btn"):
            try:
                self._viewer_donut_btn.set_tooltip_text(tip)
            except Exception:
                pass
        if self._backup_scan_anim_id is None:
            self._backup_scan_anim_id = GLib.timeout_add(
                120, self._tick_backup_scan)
        return False

    def _review_orphans_thread(self):
        """After backup: pHash-categorize USB orphans, then choice dialog."""
        from pathlib import Path as _P
        photo_path = _P(self.settings.get("photo_path") or _P.home() / "Photos")
        backup_dest = self._last_scan_backup_dest
        orphan_rels = list(self._last_scan_orphan_rels)
        if not backup_dest or not orphan_rels:
            return

        # Donut on during analysis so no dead gap before choice dialog.
        self._orphan_reviewing = True
        GLib.idle_add(self._start_orphan_review_ui)

        def _progress(cur, total, phase):
            if phase == "build":
                msg = _("Building hash cache: {c} / {t}").format(c=cur, t=total)
            else:
                msg = _("Analyzing: {c} / {t}").format(c=cur, t=total)
            GLib.idle_add(self._set_dedup_detail, msg)

        try:
            dup_rels, unique_rels = self._categorize_orphans(
                photo_path, backup_dest, orphan_rels, _progress)
        finally:
            self._orphan_reviewing = False
            GLib.idle_add(self._set_dedup_detail, "")
            GLib.idle_add(self._finish_review_ui)

        log_info(_("Orphan analysis: {d} duplicate copies, {u} unique on USB").format(
            d=len(dup_rels), u=len(unique_rels)))
        GLib.idle_add(self._show_orphan_review_dialog, dup_rels, unique_rels)

    def _finish_review_ui(self):
        """Hide donut after orphan analysis."""
        if hasattr(self, "_backup_donut_btn"):
            try:
                self._set_donuts_visible(self._backup_running)
            except Exception:
                pass
        return False

    def _show_orphan_review_dialog(self, dup_rels, unique_rels):
        if not dup_rels and not unique_rels:
            return False
        lines = []
        if dup_rels:
            lines.append(ngettext(
                "{n} duplicate copy — same image as a photo in Pixora, but under a different name or in a different folder.",
                "{n} duplicate copies — same image as photos in Pixora, but under different names or in different folders.",
                len(dup_rels),
            ).format(n=len(dup_rels)))
        if unique_rels:
            lines.append(ngettext(
                "{n} unique photo — on USB but not in Pixora.",
                "{n} unique photos — on USB but not in Pixora.",
                len(unique_rels),
            ).format(n=len(unique_rels)))
        if dup_rels:
            lines.append("")
            lines.append(_("Do you want to remove the duplicate copies from the USB?"))
        body = "\n".join(lines)
        dlg = Adw.AlertDialog(
            heading=_("Photos on USB that are not in Pixora"),
            body=body,
        )
        if dup_rels:
            dlg.add_response("delete", _("Remove duplicate copies"))
            dlg.set_response_appearance(
                "delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.add_response("keep", _("Keep everything"))
        dlg.set_default_response("keep")
        dlg.set_close_response("keep")

        def _on_resp(_d, resp):
            if resp == "delete" and dup_rels:
                threading.Thread(
                    target=self._delete_orphans_thread,
                    args=(list(dup_rels),),
                    daemon=True,
                ).start()
        dlg.connect("response", _on_resp)
        self._present_dialog(dlg)
        return False

    def _delete_orphans_thread(self, rels):
        backup_dest = self._last_scan_backup_dest
        if not backup_dest or not backup_dest.is_dir():
            return
        deleted = 0
        for rel in rels:
            path = backup_dest / rel
            try:
                path.unlink()
                deleted += 1
            except OSError as e:
                log_warn(_("Could not delete orphan: {p} ({err})").format(p=str(path), err=e))
        # Prune USB empty dirs bottom-up.
        try:
            for root, _dirs, _files in os.walk(
                    str(backup_dest), topdown=False):
                if root == str(backup_dest):
                    continue
                if not os.listdir(root):
                    try:
                        os.rmdir(root)
                    except OSError:
                        pass
        except Exception:
            pass
        log_info(_("Orphans removed: {n}").format(n=deleted))
        GLib.idle_add(self._show_orphans_deleted_dialog, deleted)


    def _show_orphans_deleted_dialog(self, count):
        dlg = Adw.AlertDialog(
            heading=_("Cleanup complete"),
            body=ngettext(
                "{n} duplicate copy removed from the USB.",
                "{n} duplicate copies removed from the USB.",
                count,
            ).format(n=count),
        )
        dlg.add_response("ok", _("OK"))
        dlg.set_default_response("ok")
        dlg.set_close_response("ok")
        self._present_dialog(dlg)
        return False
