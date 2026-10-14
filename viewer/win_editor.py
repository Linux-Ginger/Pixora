#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — win_editor.py
#  Photo editor: rotate, crop, filters, restore original
#  by LinuxGinger
# ─────────────────────────────────────────────

import hashlib
import json
import math
import os
import shutil
import threading
from pixora_i18n import _
from common import (Adw, cairo, GdkPixbuf, GLib, INSTALL_DIR, is_video,
    log_error, log_info)
from media import get_cache_path


class EditorMixin:
    """Photo editor: rotate, crop, filters, restore original (mixed into MainWindow)."""

    def on_edit_current(self, btn):
        path = self._current_photo_path() or "?"
        # Editing is photo-only; videos can't be rotated/cropped here.
        if path != "?" and is_video(path):
            return
        log_info(_("Editor opened for: {name}").format(name=os.path.basename(path)))
        self._editor_active         = True
        self._editor_rotation       = 0
        self._editor_crop_mode      = False
        self._editor_display_pixbuf = self._viewer_pixbuf
        self._crop_rect             = None
        self._crop_handle           = None
        self._crop_rect_origin      = None
        self._editor_filter         = ""
        self._editor_filtered_pixbuf = None
        self._reset_filter_ui()
        self.crop_toggle_btn.set_active(False)
        self.crop_overlay_area.set_visible(False)
        # Clean editor mode: hide all normal viewer chrome (filmstrip, counter,
        # arrows, edit/fav/close/delete) so only the photo + edit toolbar show.
        self._cancel_fade()
        for w in self._video_fade_widgets():
            w.set_visible(False)
        self.editor_bar.set_visible(True)
        # Show "Restore original" only when this photo has an edit to undo.
        self.restore_orig_btn.set_visible(
            path != "?" and self._has_original_backup(path))
        self.prev_btn.set_sensitive(False)
        self.next_btn.set_sensitive(False)

    def on_editor_cancel(self, btn=None):
        log_info(_("Editor cancelled"))
        self._editor_active         = False
        self._editor_rotation       = 0
        self._editor_crop_mode      = False
        self._editor_display_pixbuf = None
        self._crop_rect             = None
        self._crop_handle           = None
        self._crop_rect_origin      = None
        self._editor_filter         = ""
        self._editor_filtered_pixbuf = None
        self.crop_toggle_btn.set_active(False)
        self.crop_overlay_area.set_visible(False)
        self.filter_toggle_btn.set_active(False)
        self.filter_bar.set_visible(False)
        self.editor_bar.set_visible(False)
        if self._viewer_pixbuf:
            self.photo_picture.set_pixbuf(self._viewer_pixbuf)
            self._apply_viewer_transform()
        self.prev_btn.set_sensitive(self.current_index > 0)
        self.next_btn.set_sensitive(self.current_index < len(self.photos) - 1)
        # Bring the normal viewer chrome back.
        self._show_viewer_ui()
        self._reset_fade_timer()

    # ── Original backup / restore ────────────────────────────────────
    def _originals_dir(self):
        d = os.path.join(INSTALL_DIR, "originals")
        os.makedirs(d, exist_ok=True)
        try:
            os.chmod(d, 0o700)  # keep originals private to the user
        except OSError:
            pass
        return d

    def _load_originals_index(self):
        try:
            with open(os.path.join(self._originals_dir(), "index.json")) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_originals_index(self, idx):
        try:
            with open(os.path.join(self._originals_dir(), "index.json"), "w") as f:
                json.dump(idx, f)
        except Exception:
            pass

    def _has_original_backup(self, path):
        entry = self._load_originals_index().get(path)
        return bool(entry and os.path.exists(
            os.path.join(self._originals_dir(), entry["backup"])))

    def _backup_original_once(self, path):
        """Stash the untouched original the first time a photo is edited so it can
        be restored later. No-op once a backup for this photo already exists."""
        if self._has_original_backup(path):
            return
        try:
            idx = self._load_originals_index()
            backup_name = hashlib.md5(path.encode()).hexdigest() + \
                os.path.splitext(path)[1]
            dst = os.path.join(self._originals_dir(), backup_name)
            shutil.copy2(path, dst)
            try:
                os.chmod(dst, 0o600)
            except OSError:
                pass
            idx[path] = {"backup": backup_name, "orig": os.path.basename(path)}
            self._save_originals_index(idx)
        except Exception as e:
            log_error("original backup failed: {}".format(e))

    def on_editor_restore(self, btn=None):
        cur = self._current_photo_path()
        if not cur or not self._has_original_backup(cur):
            return
        dlg = Adw.MessageDialog(
            transient_for=self,
            heading=_("Restore the original?"),
            body=_("This undoes every edit and brings the photo back exactly as it was imported."),
        )
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("restore", _("Restore"))
        dlg.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")
        dlg.connect("response",
                    lambda d, r: self._do_restore_original(cur) if r == "restore" else None)
        dlg.present()

    def _do_restore_original(self, cur):
        idx = self._load_originals_index()
        entry = idx.get(cur)
        if not entry:
            return
        backup = os.path.join(self._originals_dir(), entry["backup"])
        if not os.path.exists(backup):
            return
        try:
            restored = os.path.join(os.path.dirname(cur), entry["orig"])
            for p in (cur, restored):
                try:
                    c = get_cache_path(p)
                    if os.path.exists(c):
                        os.remove(c)
                except Exception:
                    pass
            shutil.copy2(backup, restored)
            # Drop the edited file if its name differs (e.g. HEIC was saved as JPG).
            if os.path.abspath(restored) != os.path.abspath(cur) and os.path.exists(cur):
                os.remove(cur)
            os.remove(backup)
            idx.pop(cur, None)
            self._save_originals_index(idx)
        except Exception as e:
            log_error("restore original failed: {}".format(e))
            return
        # Move references over and refresh viewer + grid.
        self.photos = [restored if p == cur else p for p in self.photos]
        self._filmstrip_order_cache = None
        if cur in self._favorites:
            self._favorites.discard(cur)
            self._favorites.add(restored)
            self._schedule_save_favorites()
        with self._pixbuf_cache_lock:
            self._viewer_pixbuf_cache.pop(cur, None)
            self._viewer_pixbuf_cache.pop(restored, None)
        self.on_editor_cancel()
        self._viewer_load_id += 1
        load_id = self._viewer_load_id
        threading.Thread(target=self._load_full_photo,
                         args=(restored, load_id), daemon=True).start()
        GLib.timeout_add(300, self.start_load)

    def _reset_crop(self):
        self._editor_crop_mode = False
        self.crop_toggle_btn.set_active(False)
        self.crop_overlay_area.set_visible(False)
        self._crop_rect        = None
        self._crop_handle      = None
        self._crop_rect_origin = None

    def on_editor_rotate_left(self, btn):
        log_info(_("Editor: rotate left (-90°)"))
        self._editor_rotation = (self._editor_rotation + 90) % 360
        self._reset_crop()
        self._editor_apply_preview()

    def on_editor_rotate_right(self, btn):
        log_info(_("Editor: rotate right (+90°)"))
        self._editor_rotation = (self._editor_rotation - 90) % 360
        self._reset_crop()
        self._editor_apply_preview()

    def _editor_apply_preview(self):
        if not self._viewer_pixbuf:
            return
        rotation = self._editor_rotation % 360
        gdk_rot_map = {
            0:   GdkPixbuf.PixbufRotation.NONE,
            90:  GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE,
            180: GdkPixbuf.PixbufRotation.UPSIDEDOWN,
            270: GdkPixbuf.PixbufRotation.CLOCKWISE,
        }
        gdk_rot = gdk_rot_map.get(rotation, GdkPixbuf.PixbufRotation.NONE)
        if gdk_rot == GdkPixbuf.PixbufRotation.NONE:
            self._editor_display_pixbuf = self._viewer_pixbuf
        else:
            self._editor_display_pixbuf = self._viewer_pixbuf.rotate_simple(gdk_rot)
        # Rebuild the filtered version of the (rotated) base, then show preview.
        if getattr(self, "_editor_filter", ""):
            self._rebuild_filter_preview()
        else:
            self._editor_filtered_pixbuf = None
            self.photo_picture.set_pixbuf(self._editor_display_pixbuf)

    # ── Filters ──────────────────────────────────────────────────────
    def _reset_filter_ui(self):
        for name, b in getattr(self, "_filter_preset_btns", []):
            b.set_active(name == "")   # "Original" selected

    def on_editor_toggle_filters(self, btn):
        self.filter_bar.set_visible(btn.get_active())

    def _on_filter_preset(self, btn, name):
        if not btn.get_active():
            return  # only react to the newly-selected one
        self._editor_filter = name
        if not name:
            self._editor_filtered_pixbuf = None
            self._hide_photo_spinner()
            self.photo_picture.set_pixbuf(self._editor_display_pixbuf)
            return
        self._rebuild_filter_preview()

    def _rebuild_filter_preview(self):
        """Build the filtered preview off the main thread (PIL decode + filter +
        re-encode is slow) and show a spinner while it runs."""
        base = self._editor_display_pixbuf
        name = getattr(self, "_editor_filter", "")
        if base is None or not name:
            return
        self._photo_spinner.set_visible(True)
        self._photo_spinner.start()
        self._filter_gen = getattr(self, "_filter_gen", 0) + 1
        gen = self._filter_gen

        def _bg():
            pb = self._make_filtered_pixbuf(base, name)
            GLib.idle_add(self._apply_filtered_preview, pb, gen, name)
        threading.Thread(target=_bg, daemon=True).start()

    def _apply_filtered_preview(self, pb, gen, name):
        # Drop if a newer preset was picked or the editor was closed meanwhile.
        if gen != getattr(self, "_filter_gen", 0) or not self._editor_active:
            return False
        self._hide_photo_spinner()
        if pb is not None and self._editor_filter == name:
            self._editor_filtered_pixbuf = pb
            self.photo_picture.set_pixbuf(pb)
        return False

    def _make_filtered_pixbuf(self, pixbuf, name):
        try:
            pil = self._pixbuf_to_pil(pixbuf)
            return self._pil_to_pixbuf(self._apply_named_filter(pil, name))
        except Exception as e:
            log_error("filter preview failed: {}".format(e))
            return None

    def _pixbuf_to_pil(self, pb):
        from PIL import Image
        w, h = pb.get_width(), pb.get_height()
        mode = "RGBA" if pb.get_has_alpha() else "RGB"
        img = Image.frombytes(mode, (w, h), pb.get_pixels(),
                              "raw", mode, pb.get_rowstride())
        return img.convert("RGB")

    def _pil_to_pixbuf(self, img):
        from io import BytesIO
        buf = BytesIO()
        img.convert("RGB").save(buf, "PNG")
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        try:
            loader.write(buf.getvalue())
        finally:
            loader.close()
        return loader.get_pixbuf()

    def _apply_named_filter(self, img, name):
        """Apply a named preset to a PIL image (full-strength). Used for both the
        preview and the on-save full-resolution render."""
        from PIL import Image, ImageOps, ImageEnhance
        img = img.convert("RGB")
        if name == "bw":
            return ImageOps.grayscale(img).convert("RGB")
        if name == "sepia":
            g = ImageOps.grayscale(img)
            return ImageOps.colorize(g, black=(28, 16, 0),
                                     white=(255, 232, 184)).convert("RGB")
        if name == "warm":
            r, g, b = img.split()
            r = r.point(lambda v: min(255, int(v * 1.12)))
            b = b.point(lambda v: int(v * 0.90))
            return Image.merge("RGB", (r, g, b))
        if name == "cool":
            r, g, b = img.split()
            b = b.point(lambda v: min(255, int(v * 1.12)))
            r = r.point(lambda v: int(v * 0.90))
            return Image.merge("RGB", (r, g, b))
        if name == "vivid":
            img = ImageEnhance.Color(img).enhance(1.5)
            return ImageEnhance.Contrast(img).enhance(1.12)
        return img

    def on_editor_toggle_crop(self, btn):
        self._editor_crop_mode = btn.get_active()
        log_info(_("Editor crop mode: {state}").format(
            state=_("on") if self._editor_crop_mode else _("off")
        ))
        self._crop_rect        = None
        self._crop_handle      = None
        self._crop_rect_origin = None
        self.crop_overlay_area.set_visible(self._editor_crop_mode)
        if self._editor_crop_mode:
            self.crop_overlay_area.queue_draw()

    def _get_image_display_rect(self, widget_w, widget_h):
        """(x, y, w, h) of the letterboxed image inside the widget."""
        pixbuf = self._editor_display_pixbuf or self._viewer_pixbuf
        if not pixbuf:
            return (0, 0, widget_w, widget_h)
        img_w  = pixbuf.get_width()
        img_h  = pixbuf.get_height()
        scale  = min(widget_w / img_w, widget_h / img_h)
        disp_w = img_w * scale
        disp_h = img_h * scale
        return ((widget_w - disp_w) / 2, (widget_h - disp_h) / 2, disp_w, disp_h)

    def on_crop_draw(self, area, cr, w, h):
        if self._crop_rect is None:
            ix, iy, iw, ih = self._get_image_display_rect(w, h)
            self._crop_rect = [ix, iy, ix + iw, iy + ih]

        x1, y1, x2, y2 = self._crop_rect
        rw, rh = x2 - x1, y2 - y1

        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.paint()

        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.rectangle(x1, y1, rw, rh)
        cr.fill()
        cr.set_operator(cairo.OPERATOR_OVER)

        cr.set_source_rgba(1, 1, 1, 1.0)
        cr.set_line_width(2)
        cr.rectangle(x1, y1, rw, rh)
        cr.stroke()

        # Rule-of-thirds gridlines.
        cr.set_source_rgba(1, 1, 1, 0.35)
        cr.set_line_width(1)
        for i in (1, 2):
            cr.move_to(x1 + rw * i / 3, y1)
            cr.line_to(x1 + rw * i / 3, y2)
            cr.move_to(x1, y1 + rh * i / 3)
            cr.line_to(x2, y1 + rh * i / 3)
        cr.stroke()

        HANDLE_R = 7
        for hx, hy in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
            cr.set_source_rgba(0, 0, 0, 0.5)
            cr.arc(hx, hy, HANDLE_R + 2, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, 1.0)
            cr.arc(hx, hy, HANDLE_R, 0, 2 * math.pi)
            cr.fill()

    def on_crop_drag_begin(self, gesture, x, y):
        if self._crop_rect is None:
            return
        self._crop_rect_origin = list(self._crop_rect)
        HANDLE_R = 20  # detection radius
        x1, y1, x2, y2 = self._crop_rect
        for name, (hx, hy) in [('tl', (x1, y1)), ('tr', (x2, y1)),
                                ('bl', (x1, y2)), ('br', (x2, y2))]:
            if (x - hx) ** 2 + (y - hy) ** 2 < HANDLE_R ** 2:
                self._crop_handle = name
                return
        if x1 <= x <= x2 and y1 <= y <= y2:
            self._crop_handle = 'move'
        else:
            self._crop_handle = None

    def on_crop_drag_update(self, gesture, dx, dy):
        if not self._crop_handle or not self._crop_rect_origin:
            return
        ox1, oy1, ox2, oy2 = self._crop_rect_origin
        MIN = 40
        aw = self.crop_overlay_area.get_width()
        ah = self.crop_overlay_area.get_height()
        ix, iy, iw, ih = self._get_image_display_rect(aw, ah)
        ix2, iy2 = ix + iw, iy + ih

        r = list(self._crop_rect)
        h = self._crop_handle
        if h == 'tl':
            r[0] = max(ix, min(ox2 - MIN, ox1 + dx))
            r[1] = max(iy, min(oy2 - MIN, oy1 + dy))
        elif h == 'tr':
            r[2] = min(ix2, max(ox1 + MIN, ox2 + dx))
            r[1] = max(iy, min(oy2 - MIN, oy1 + dy))
        elif h == 'bl':
            r[0] = max(ix, min(ox2 - MIN, ox1 + dx))
            r[3] = min(iy2, max(oy1 + MIN, oy2 + dy))
        elif h == 'br':
            r[2] = min(ix2, max(ox1 + MIN, ox2 + dx))
            r[3] = min(iy2, max(oy1 + MIN, oy2 + dy))
        elif h == 'move':
            rw = ox2 - ox1
            rh = oy2 - oy1
            nx1 = max(ix, min(ix2 - rw, ox1 + dx))
            ny1 = max(iy, min(iy2 - rh, oy1 + dy))
            r = [nx1, ny1, nx1 + rw, ny1 + rh]
        self._crop_rect = r
        self.crop_overlay_area.queue_draw()

    def on_crop_drag_end(self, gesture, dx, dy):
        self._crop_handle      = None
        self._crop_rect_origin = None

    def _widget_to_image_coords(self, wx, wy, widget_w, widget_h):
        pixbuf = self._editor_display_pixbuf or self._viewer_pixbuf
        if not pixbuf:
            return None
        img_w  = pixbuf.get_width()
        img_h  = pixbuf.get_height()
        scale  = min(widget_w / img_w, widget_h / img_h)
        disp_w = img_w * scale
        disp_h = img_h * scale
        x_off  = (widget_w - disp_w) / 2
        y_off  = (widget_h - disp_h) / 2
        ix = max(0, min(img_w, (wx - x_off) / scale))
        iy = max(0, min(img_h, (wy - y_off) / scale))
        return (int(ix), int(iy))

    def on_editor_save(self, btn):
        path     = self.photos[self.current_index]
        rotation = self._editor_rotation
        # Capture filter now — on_editor_cancel() below resets the editor state.
        filt     = getattr(self, "_editor_filter", "")
        log_info(_("Editor save: rotation={rot}° crop={crop} path={p}").format(
            rot=rotation, crop=bool(self._crop_rect), p=path
        ))

        # Crop is captured as fractions of the displayed pixbuf, not pixels:
        # the viewer pixbuf is capped at 2560px, so pixel coords would crop the
        # wrong region of the full-resolution original. Fractions scale cleanly.
        crop_frac = None
        if self._editor_crop_mode and self._crop_rect:
            aw = self.crop_overlay_area.get_width()
            ah = self.crop_overlay_area.get_height()
            p1 = self._widget_to_image_coords(self._crop_rect[0], self._crop_rect[1], aw, ah)
            p2 = self._widget_to_image_coords(self._crop_rect[2], self._crop_rect[3], aw, ah)
            if p1 and p2 and abs(p2[0] - p1[0]) > 4 and abs(p2[1] - p1[1]) > 4:
                disp = self._editor_display_pixbuf or self._viewer_pixbuf
                dw = max(1, disp.get_width())
                dh = max(1, disp.get_height())
                crop_frac = (min(p1[0], p2[0]) / dw, min(p1[1], p2[1]) / dh,
                             max(p1[0], p2[0]) / dw, max(p1[1], p2[1]) / dh)

        self.on_editor_cancel()

        def _do_save():
            try:
                from PIL import Image
                original_mtime = os.path.getmtime(path)
                # Keep the untouched original so edits can be undone later.
                self._backup_original_once(path)
                old_cache = get_cache_path(path)
                from PIL import ImageOps
                img = Image.open(path)
                if img.mode == "P" and "transparency" in img.info:
                    img = img.convert("RGBA")
                ext = os.path.splitext(path)[1].lower()
                is_jpeg = ext in (".jpg", ".jpeg")
                is_png  = ext == ".png"

                # Carry the original EXIF across the edit (GPS + capture date +
                # orientation). Preload the GPS and Exif sub-IFDs so tobytes()
                # keeps them — plain getexif().tobytes() drops those, which lost
                # the photo's location and shuffled its date order after editing.
                exif_out = b""
                if not is_png:
                    try:
                        src_exif = img.info.get("exif")
                        if src_exif:
                            ex = Image.Exif()
                            ex.load(src_exif)
                            ex.get_ifd(0x8825)  # GPS IFD
                            ex.get_ifd(0x8769)  # Exif sub-IFD (DateTimeOriginal)
                            ex[0x0112] = 1      # pixels become upright below
                            exif_out = ex.tobytes()
                    except Exception:
                        exif_out = b""

                # Bake EXIF orientation into the pixels (align PIL and GdkPixbuf).
                img = ImageOps.exif_transpose(img)

                if rotation != 0:
                    img = img.rotate(rotation, expand=True)
                if crop_frac:
                    iw, ih = img.size
                    cb = (max(0, int(round(crop_frac[0] * iw))),
                          max(0, int(round(crop_frac[1] * ih))),
                          min(iw, int(round(crop_frac[2] * iw))),
                          min(ih, int(round(crop_frac[3] * ih))))
                    if cb[2] - cb[0] > 1 and cb[3] - cb[1] > 1:
                        img = img.crop(cb)
                if filt:
                    img = self._apply_named_filter(img, filt)

                out_path = path
                if is_jpeg:
                    img.save(path, "JPEG", quality=95, exif=exif_out)
                elif is_png:
                    img.save(path, "PNG")
                else:
                    # PIL can't write HEIC & co. — saving PNG bytes under the old
                    # extension corrupts the file. Replace with a max-quality JPEG
                    # (date/GPS kept), matching the JPEG-only HEIC policy.
                    out_path = os.path.splitext(path)[0] + ".jpg"
                    counter = 1
                    while os.path.exists(out_path):
                        out_path = os.path.splitext(path)[0] + f"_{counter}.jpg"
                        counter += 1
                    if img.mode not in ("RGB", "L"):
                        img = img.convert("RGB")
                    img.save(out_path, "JPEG", quality=95, exif=exif_out)
                    os.remove(path)
                if os.path.exists(old_cache):
                    os.remove(old_cache)
                os.utime(out_path, (original_mtime, original_mtime))
                GLib.idle_add(_after_save, out_path)
            except Exception as e:
                log_error(_("Editor save failed: {err}").format(err=e))
                GLib.idle_add(_save_error, str(e))

        def _after_save(new_path):
            if new_path != path:
                # Extension changed (e.g. HEIC → JPG): move every reference over.
                self.photos = [new_path if p == path else p for p in self.photos]
                self._filmstrip_order_cache = None
                # Re-key the original backup so Restore still finds it.
                idx = self._load_originals_index()
                if path in idx:
                    idx[new_path] = idx.pop(path)
                    self._save_originals_index(idx)
                if path in self._favorites:
                    self._favorites.discard(path)
                    self._favorites.add(new_path)
                    self._schedule_save_favorites()
            # Drop the stale (pre-edit) full-res pixbuf, else the big image keeps
            # showing the old orientation while the thumbnail is already rotated.
            with self._pixbuf_cache_lock:
                self._viewer_pixbuf_cache.pop(path, None)
                self._viewer_pixbuf_cache.pop(new_path, None)
            self._viewer_load_id += 1
            load_id = self._viewer_load_id
            threading.Thread(
                target=self._load_full_photo,
                args=(new_path, load_id),
                daemon=True
            ).start()
            GLib.timeout_add(300, self.start_load)
            return False

        def _save_error(msg):
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading=_("Save failed"),
                body=msg
            )
            dialog.add_response("ok", _("OK"))
            dialog.present()

        threading.Thread(target=_do_save, daemon=True).start()
