#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — win_delete.py
#  Deleting photos and videos
#  by LinuxGinger
# ─────────────────────────────────────────────

import os
import threading
from pixora_i18n import _, ngettext
import common
from common import (Adw, cairo, Gdk, GdkPixbuf, GLib, Gtk, is_video,
    log_error, log_info, log_warn, trash_file)
from media import get_cache_path
import originals


class DeleteMixin:
    """Deleting photos and videos (mixed into MainWindow)."""

    def on_delete_current(self, btn):
        if not self.photos or not (0 <= self.current_index < len(self.photos)):
            return
        if getattr(self, "_shredding", False):
            return
        path = self.photos[self.current_index]
        log_info(_("Delete confirmation requested: {p}").format(p=path))
        dialog = Adw.AlertDialog(
            heading=_("Move to trash?"),
            body=_("'{name}' will be moved to the trash. You can restore it "
                   "from there.").format(name=os.path.basename(path))
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Move to trash"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_delete_current_response, path)
        dialog.present(self)

    def _on_delete_current_response(self, dialog, response, path):
        if response != "delete":
            log_info(_("Delete cancelled: {p}").format(p=path))
            return
        if getattr(self, "_shredding", False):
            return
        # Trash first, animate after: if the trash refuses, the photo simply
        # stays on screen with an explanation (no black viewer, no data loss).
        err = self._trash_media(path)
        if err:
            self._show_delete_error([os.path.basename(path)], err)
            return
        self._shredding = True
        self._play_shred_animation(path, on_done=self._finish_delete_after_shred)

    def _trash_media(self, path):
        """Trash one library file and its leftovers; None on success, else error text."""
        # Cache key uses the file's mtime, so resolve it before the move.
        try:
            cache_path = get_cache_path(path)
        except OSError:
            cache_path = None
        try:
            trash_file(path)
            log_info(_("Moved to trash: {p}").format(p=path))
        except GLib.Error as e:
            if not os.path.exists(path):
                # Already gone (external tool / watcher race).
                log_warn(_("File already gone from disk: {p}").format(p=path))
            else:
                log_error(_("Delete failed: {err}").format(err=e.message))
                return e.message
        try:
            if cache_path and os.path.exists(cache_path):
                os.remove(cache_path)
        except OSError:
            pass
        # The private "Restore original" copy goes too, or a deleted photo
        # would quietly live on in ~/.local/share/pixora/originals.
        originals.drop(path)
        # A Live Photo's movie belongs to the still: it goes to the trash too.
        motion = getattr(self, "_live_pairs", {}).pop(path, None)
        if motion and os.path.exists(motion):
            try:
                trash_file(motion)
            except GLib.Error as e:
                log_warn(_("Could not move the Live Photo movie to the trash: "
                           "{err}").format(err=e.message))
        return None

    def _show_delete_error(self, names, err):
        shown = ", ".join(names[:5]) + (" …" if len(names) > 5 else "")
        dlg = Adw.AlertDialog(
            heading=_("Could not move to trash"),
            body=_("{names} could not be moved to the trash, so nothing was "
                   "deleted.\n\n{err}").format(names=shown, err=err),
        )
        dlg.add_response("ok", _("OK"))
        dlg.present(self)

    def _play_shred_animation(self, path, on_done):
        """Shredder effect → on_done(path). Videos use cached thumbnail."""
        pixbuf = getattr(self, "_viewer_pixbuf", None)
        if pixbuf is None and is_video(path):
            try:
                thumb_path = get_cache_path(path, common.THUMB_SIZE)
                if os.path.exists(thumb_path):
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file(thumb_path)
            except Exception:
                pixbuf = None
        if pixbuf is None:
            on_done(path)
            return

        # Render pixbuf ONCE to ImageSurface — else 14 strips × 60fps kills GPU.
        try:
            anim_pb = pixbuf
            orig_w = anim_pb.get_width()
            orig_h = anim_pb.get_height()
            # Cap at 1280px for speed.
            MAX_DIM = 1280
            if max(orig_w, orig_h) > MAX_DIM:
                scale = MAX_DIM / max(orig_w, orig_h)
                anim_pb = anim_pb.scale_simple(
                    max(1, int(orig_w * scale)),
                    max(1, int(orig_h * scale)),
                    GdkPixbuf.InterpType.BILINEAR,
                )
            pb_w = anim_pb.get_width()
            pb_h = anim_pb.get_height()
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, pb_w, pb_h)
            _tmp_ctx = cairo.Context(surface)
            Gdk.cairo_set_source_pixbuf(_tmp_ctx, anim_pb, 0, 0)
            _tmp_ctx.paint()
        except Exception as e:
            log_error(_("Shred animation pre-render error: {err}").format(err=e))
            on_done(path)
            return

        draw_area = Gtk.DrawingArea()
        draw_area.set_vexpand(True)
        draw_area.set_hexpand(True)
        draw_area.set_can_target(False)
        self.viewer_area.add_overlay(draw_area)
        # Hide + pause video before overlay so it doesn't flicker underneath.
        is_vid = is_video(path)
        if is_vid:
            try:
                if self._video_media is not None:
                    self._video_media.pause()
                self.video_display.set_visible(False)
            except Exception:
                pass
        # Hide the whole stack so the falling strips reveal black, not the photo
        # underneath; _show_full_photo restores it when the next item loads.
        self._viewer_stack.set_visible(False)
        self.photo_picture.set_visible(False)

        N_STRIPS = 12
        DURATION = 1.1
        state = {"progress": 0.0, "start": None, "done": False}

        def _draw_fn(area, cr, w, h):
            try:
                progress = state["progress"]
                if w <= 0 or h <= 0 or pb_w <= 0 or pb_h <= 0:
                    return
                scale = min(w / pb_w, h / pb_h)
                disp_w = pb_w * scale
                disp_h = pb_h * scale
                x0 = (w - disp_w) / 2
                y0 = (h - disp_h) / 2
                strip_w = disp_w / N_STRIPS
                for i in range(N_STRIPS):
                    delay = (i / N_STRIPS) * 0.35
                    denom = max(0.001, 1.0 - delay)
                    sp = max(0.0, min(1.0, (progress - delay) / denom))
                    ease = sp * sp * sp  # cubic ease-in ≈ gravity
                    y_off = ease * h * 1.4
                    opacity = max(0.0, 1.0 - sp * 1.05)
                    rot = (sp * 0.4) * (1 if i % 2 else -1)
                    strip_x = x0 + i * strip_w

                    cr.save()
                    cr.rectangle(strip_x, 0, strip_w + 1, h)
                    cr.clip()
                    cx = strip_x + strip_w / 2
                    cy = y0 + disp_h / 2 + y_off
                    cr.translate(cx, cy)
                    cr.rotate(rot)
                    cr.translate(-cx, -cy)
                    cr.translate(x0, y0 + y_off)
                    cr.scale(scale, scale)
                    cr.set_source_surface(surface, 0, 0)
                    cr.paint_with_alpha(opacity)
                    cr.restore()
            except Exception as _e:
                log_error(_("Shred animation draw error: {err}").format(err=_e))

        draw_area.set_draw_func(_draw_fn)

        def _tick(widget, frame_clock):
            if state["done"]:
                return False
            now = frame_clock.get_frame_time() / 1_000_000.0
            if state["start"] is None:
                state["start"] = now
            elapsed = now - state["start"]
            state["progress"] = min(1.0, elapsed / DURATION)
            widget.queue_draw()
            if elapsed >= DURATION:
                state["done"] = True
                try:
                    self.viewer_area.remove_overlay(draw_area)
                except Exception:
                    pass
                # Clear old pixbuf before show, else deleted photo flashes briefly.
                try:
                    self.photo_picture.set_pixbuf(None)
                    self.viewer_title.set_text("")
                    self._set_viewer_location("empty")
                except Exception:
                    pass
                # Leave the stack hidden — the next item's load (_show_full_photo
                # or _show_video) makes the right surface visible. Until then the
                # black background shows, never the deleted photo.
                self.photo_picture.set_visible(True)
                on_done(path)
                return False
            return True

        draw_area.add_tick_callback(_tick)

    def _cleanup_empty_parent_dirs(self, path):
        """Remove empty parent dirs up to (not including) photo_path root."""
        photo_root = self.settings.get("photo_path")
        if not photo_root:
            return
        photo_root = os.path.abspath(photo_root)
        parent = os.path.dirname(os.path.abspath(path))
        while (parent != photo_root
               and parent.startswith(photo_root + os.sep)):
            try:
                os.rmdir(parent)  # succeeds only when empty
                log_info(_("Empty folder removed: {p}").format(p=parent))
            except OSError:
                break
            parent = os.path.dirname(parent)

    def _finish_delete_after_shred(self, path):
        """Post-animation (file already in the trash): navigate on."""
        n_before = len(self.photos)
        self._cleanup_empty_parent_dirs(path)
        if path in self._favorites:
            self._favorites.discard(path)
            self._schedule_save_favorites()
        # List-comp avoids .remove() raising on watcher races.
        self.photos = [p for p in self.photos if p != path]
        self._filmstrip_order_cache = None
        n_after = len(self.photos)
        log_info(_("photos list: {before} → {after}").format(before=n_before, after=n_after))
        if not self.photos:
            self._shredding = False
            self.close_viewer()
            self.show_empty_state()
            return
        if self.current_index >= len(self.photos):
            self.current_index = len(self.photos) - 1
        next_path = self.photos[self.current_index]
        # Rebuild filmstrip thumbs so the deleted photo vanishes.
        self._filmstrip_thumbs = {}
        try:
            self.filmstrip_area.queue_draw()
        except Exception:
            pass
        GLib.idle_add(self._update_filmstrip)
        GLib.timeout_add(80, self._scroll_filmstrip_to_current)
        try:
            self.viewer_counter.set_text(f"{self.current_index + 1} / {len(self.photos)}")
        except Exception:
            pass
        self._viewer_load_id += 1
        load_id = self._viewer_load_id
        threading.Thread(
            target=self._load_full_photo,
            args=(next_path, load_id),
            daemon=True
        ).start()
        GLib.timeout_add(500, self.start_load)
        self._shredding = False

    def on_delete_selected(self, btn):
        if not self._selected:
            return
        count = len(self._selected)
        dialog = Adw.AlertDialog(
            heading=ngettext("Move %d item to trash?", "Move %d items to trash?",
                             count) % count,
            body=_("You can restore them from the trash."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Move to trash"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_delete_selected_response)
        dialog.present(self)

    def _on_delete_selected_response(self, dialog, response):
        if response != "delete":
            return
        paths_to_delete = [self.photos[i] for i in self._selected if i < len(self.photos)]
        fav_changed = False
        failed, last_err = [], ""
        for path in paths_to_delete:
            err = self._trash_media(path)
            if err:
                failed.append(os.path.basename(path))
                last_err = err
                continue  # still in the library: keep it a favourite
            if path in self._favorites:
                self._favorites.discard(path)
                fav_changed = True
        # Clean up empty dirs after all files are gone.
        for path in paths_to_delete:
            self._cleanup_empty_parent_dirs(path)
        if fav_changed:
            self._schedule_save_favorites()
        self.toggle_select_mode()
        self.load_photos()
        if failed:
            self._show_delete_error(failed, last_err)
