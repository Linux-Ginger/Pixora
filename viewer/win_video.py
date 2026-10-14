#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — win_video.py
#  Video playback and scrubber previews in the viewer
#  by LinuxGinger
# ─────────────────────────────────────────────

import datetime
import os
import subprocess
import threading
from collections import OrderedDict
from pixora_i18n import _
from common import (FILM_THUMB, format_duration, format_viewer_date, Gdk,
    GdkPixbuf, GLib, Gtk, log_warn)
from metadata import get_photo_date, get_video_duration


class VideoMixin:
    """Video playback and scrubber previews in the viewer (mixed into MainWindow)."""

    def _show_video(self, path, location="", searching=False):
        self._hide_photo_spinner()
        self._stop_video()
        # The previous photo's pixels must not be mistaken for this video's
        # (the delete animation shreds _viewer_pixbuf).
        self._viewer_pixbuf = None
        self._update_live_badge(None)
        self._preview_cache = OrderedDict()
        self._preview_pending = None
        self._viewer_zoom   = 1.0
        self._viewer_offset = [0.0, 0.0]
        self._reset_viewer_transform()  # clear any leftover zoom from prev media
        self._show_viewer_ui()   # reset fade state
        # Hide the whole photo stack (not just one slot) so a portrait video's
        # letterbox shows the black background, never the previous photo.
        self._viewer_stack.set_visible(False)
        self.photo_picture.set_visible(False)
        self.edit_btn.set_visible(False)
        self.favorite_btn.set_margin_end(120)   # close the gap left by hidden edit
        self._update_favorite_btn()
        self.video_display.set_visible(True)
        self.video_controls.set_visible(True)
        self.viewer_counter.set_margin_bottom(FILM_THUMB + 12 + 8 + 56)
        self.viewer_counter.set_visible(True)
        self.filmstrip_scroll.set_visible(True)
        self.video_spinner.set_visible(True)
        self.video_spinner.start()
        self._video_media = Gtk.MediaFile.new_for_filename(path)
        self._video_media.set_loop(False)
        # Reset cleanly at end-of-stream instead of hanging on a spinner.
        self._video_ended_hid = self._video_media.connect(
            "notify::ended", self._on_video_ended)
        self.video_display.set_paintable(self._video_media)
        self._video_media.play()
        self.video_play_btn.set_icon_name("media-playback-pause-symbolic")
        self.video_mute_btn.set_icon_name("audio-volume-high-symbolic")
        self.video_vol_scale.set_value(1.0)
        self.video_scrubber.set_value(0.0)
        self.video_time_label.set_text("0:00 / 0:00")
        ts = get_photo_date(path)
        try:
            datum = format_viewer_date(datetime.datetime.fromtimestamp(ts))
        except (OverflowError, OSError, ValueError):
            datum = ""
        sep = "  —  " if datum else ""
        self.viewer_title.set_text(f"{os.path.basename(path)}{sep}{datum}")
        self._apply_initial_location(location, searching, is_video=True)
        self.viewer_counter.set_text(f"{self.current_index + 1} / {len(self.photos)}")
        self.prev_btn.set_sensitive(self.current_index > 0)
        self.next_btn.set_sensitive(self.current_index < len(self.photos) - 1)
        self.filmstrip_area.queue_draw()
        GLib.idle_add(self._scroll_filmstrip_to_current)
        self._start_video_poll()
        self._reset_fade_timer()
        # Pre-extract scrubber preview frames so hovering rarely shows a spinner.
        threading.Thread(
            target=self._preload_preview_frames,
            args=(path, self._viewer_load_id),
            daemon=True
        ).start()
        return False

    def _preload_preview_frames(self, path, load_id):
        """One ffmpeg pass dumps the 2s-bin preview frames into the cache up
        front, so timeline hover is instant; the spinner is just a fallback."""
        import tempfile
        import glob as _glob
        try:
            dur = get_video_duration(path)
        except Exception:
            dur = 0
        if dur <= 0:
            return
        bin_s = 2
        n = min(int(dur // bin_s) + 1, 300)   # cap work on very long videos
        tmpdir = tempfile.mkdtemp(prefix="pixora_prev_")
        try:
            subprocess.run(
                ["ffmpeg", "-i", path, "-an",
                 "-vf", f"fps=1/{bin_s},scale=160:-1",
                 "-frames:v", str(n),
                 os.path.join(tmpdir, "o_%05d.jpg"), "-y"],
                capture_output=True, timeout=120
            )
            if load_id != self._viewer_load_id:
                return
            for i, fp in enumerate(sorted(_glob.glob(os.path.join(tmpdir, "o_*.jpg")))):
                if load_id != self._viewer_load_id:
                    break
                key = (path, i * bin_s)
                if key in self._preview_cache:
                    continue
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(fp, 160, 90, True)
                except Exception:
                    continue
                with self._pixbuf_cache_lock:
                    self._preview_cache[key] = pb
            # If the user is already hovering a now-ready frame, show it.
            GLib.idle_add(self._service_pending_preview)
        except Exception:
            pass
        finally:
            try:
                for fp in _glob.glob(os.path.join(tmpdir, "*")):
                    os.unlink(fp)
                os.rmdir(tmpdir)
            except OSError:
                pass

    # ── Live Photos ──────────────────────────────────────────────────
    def _build_live_photo_ui(self, viewer_area):
        """'LIVE' button under the title; click plays the still's movie once."""
        self._live_media = None
        self._current_live_motion = None
        if not hasattr(self, "_live_pairs"):
            self._live_pairs = {}
        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.set_halign(Gtk.Align.START)
        btn.set_tooltip_text(_("Play Live Photo"))
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.append(Gtk.Image.new_from_icon_name("media-playback-start-symbolic"))
        box.append(Gtk.Label(label=_("LIVE")))
        btn.set_child(box)
        btn.set_visible(False)
        btn.connect("clicked", self._play_live_motion)
        self.live_btn = btn
        self.viewer_title_box.append(btn)

    def _update_live_badge(self, path):
        motion = self._live_pairs.get(path) if path else None
        if motion and not os.path.exists(motion):
            motion = None
        self._current_live_motion = motion
        self.live_btn.set_visible(bool(motion)
                                  and not getattr(self, "_editor_active", False))

    def _play_live_motion(self, *_args):
        motion = self._current_live_motion
        if not motion or self._live_media is not None:
            return
        try:
            media = Gtk.MediaFile.new_for_filename(motion)
        except Exception as e:
            log_warn(_("Live Photo could not be played: {err}").format(err=e))
            return
        media.set_loop(False)
        media.set_muted(True)  # like Photos on a quick tap: motion, no sound
        self._live_media = media
        self._live_ended_hid = media.connect("notify::ended", self._on_live_ended)
        self._live_error_hid = media.connect("notify::error", self._on_live_error)
        self.live_picture.set_paintable(media)
        self.live_picture.set_visible(True)
        self.live_btn.set_sensitive(False)
        media.play()

    def _on_live_ended(self, media, _pspec):
        if media.get_ended():
            self._stop_live_motion()

    def _on_live_error(self, media, _pspec):
        err = media.get_error()
        if err is not None:
            log_warn(_("Live Photo could not be played: {err}").format(err=err.message))
            self._stop_live_motion()

    def _stop_live_motion(self):
        media = getattr(self, "_live_media", None)
        if media is None:
            return
        self._live_media = None
        for attr in ("_live_ended_hid", "_live_error_hid"):
            hid = getattr(self, attr, None)
            if hid is not None:
                try:
                    media.disconnect(hid)
                except Exception:
                    pass
                setattr(self, attr, None)
        try:
            media.pause()
        except Exception:
            pass
        self.live_picture.set_visible(False)
        self.live_picture.set_paintable(None)
        self.live_btn.set_sensitive(True)

    def _stop_video(self):
        self._stop_live_motion()
        self._stop_video_poll()
        self._cancel_fade()
        if self._video_seek_pending_id:
            try:
                GLib.source_remove(self._video_seek_pending_id)
            except Exception:
                pass
            self._video_seek_pending_id = None
        self.video_spinner.set_visible(False)
        self.video_spinner.stop()
        if self._video_media:
            hid = getattr(self, "_video_ended_hid", None)
            if hid is not None:
                try:
                    self._video_media.disconnect(hid)
                except Exception:
                    pass
                self._video_ended_hid = None
            self._video_media.pause()
            self._video_media = None
            # Detach the paused stream so its decoder can be freed.
            try:
                self.video_display.set_paintable(None)
            except Exception:
                pass

    def _start_video_poll(self):
        self._stop_video_poll()
        self._video_poll_id = GLib.timeout_add(500, self._update_video_position)

    def _stop_video_poll(self):
        if self._video_poll_id is not None:
            try:
                GLib.source_remove(self._video_poll_id)
            except Exception:
                pass
            self._video_poll_id = None

    def _update_video_position(self):
        if not self._video_media:
            self._video_poll_id = None
            return False
        dur = self._video_media.get_duration()
        pos = self._video_media.get_timestamp()
        if dur > 0:
            if self.video_spinner.get_visible():
                self.video_spinner.set_visible(False)
                self.video_spinner.stop()
            self._video_scrubbing_lock = True
            self.video_scrubber.set_value(pos / dur)
            self._video_scrubbing_lock = False
        pos_s = pos // 1_000_000
        dur_s = dur // 1_000_000
        new_text = f"{format_duration(pos_s)} / {format_duration(dur_s)}"
        if getattr(self, '_last_video_time_text', None) != new_text:
            self.video_time_label.set_text(new_text)
            self._last_video_time_text = new_text
        if self._video_media.get_ended():
            self.video_play_btn.set_icon_name("media-playback-start-symbolic")
            self._video_poll_id = None
            return False
        return True

    def _on_video_ended(self, media, _pspec):
        """At end-of-stream: rewind to the start and pause, so the video shows
        its first frame and the play button works — never an endless spinner."""
        if not media.get_ended():
            return
        try:
            media.seek(0)
            media.pause()
        except Exception:
            pass
        self._stop_video_poll()
        self.video_play_btn.set_icon_name("media-playback-start-symbolic")
        self.video_spinner.set_visible(False)
        self.video_spinner.stop()
        self._video_scrubbing_lock = True
        self.video_scrubber.set_value(0.0)
        self._video_scrubbing_lock = False
        self._show_viewer_ui()  # keep controls visible to replay

    def _on_video_play_pause(self, btn):
        if not self._video_media:
            return
        if self._video_media.get_playing():
            self._video_media.pause()
            if btn:
                btn.set_icon_name("media-playback-start-symbolic")
            else:
                self.video_play_btn.set_icon_name("media-playback-start-symbolic")
            self._flash_playpause("media-playback-pause-symbolic")
            self._stop_video_poll()
            self._cancel_fade()
            self._show_viewer_ui()
        else:
            # Replaying after it ended: rewind first, else play() is a no-op.
            if self._video_media.get_ended():
                try:
                    self._video_media.seek(0)
                except Exception:
                    pass
            self._video_media.play()
            if btn:
                btn.set_icon_name("media-playback-pause-symbolic")
            else:
                self.video_play_btn.set_icon_name("media-playback-pause-symbolic")
            self._flash_playpause("media-playback-start-symbolic")
            self._start_video_poll()
            self._reset_fade_timer()

    def _flash_playpause(self, icon_name):
        """Brief round play/pause icon in the center, fading out (like a player)."""
        area = getattr(self, "viewer_area", None)
        if area is None:
            return
        box = Gtk.Box()
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_can_target(False)
        box.set_size_request(92, 92)
        bcss = Gtk.CssProvider()
        bcss.load_from_string(
            "box { background-color: rgba(0,0,0,0.55); border-radius: 9999px; }")
        box.get_style_context().add_provider(
            bcss, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        img = Gtk.Image.new_from_icon_name(icon_name)
        img.set_pixel_size(46)
        img.set_halign(Gtk.Align.CENTER)
        img.set_valign(Gtk.Align.CENTER)
        img.set_hexpand(True)
        img.set_vexpand(True)
        icss = Gtk.CssProvider()
        icss.load_from_string("image { color: #ffffff; }")
        img.get_style_context().add_provider(
            icss, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box.append(img)
        area.add_overlay(box)
        state = {"i": 0}
        frames = 14

        def _step():
            t = state["i"] / frames
            if t >= 1.0:
                try:
                    area.remove_overlay(box)
                except Exception:
                    pass
                return False
            box.set_opacity(max(0.0, 1.0 - t * t))   # hold briefly, then fade
            state["i"] += 1
            return True

        GLib.timeout_add(35, _step)

    def _on_video_scrub(self, scale):
        if self._video_scrubbing_lock or not self._video_media:
            return
        self._trigger_scrub_preview(scale.get_value())
        if self._video_seek_pending_id:
            try:
                GLib.source_remove(self._video_seek_pending_id)
            except Exception:
                pass
        self._video_seek_pending_id = GLib.timeout_add(80, self._do_video_seek)

    def _do_video_seek(self):
        self._video_seek_pending_id = None
        if not self._video_media:
            return False
        dur = self._video_media.get_duration()
        if dur > 0:
            self._video_media.seek(int(self.video_scrubber.get_value() * dur))
        return False

    def _on_video_mute(self, btn):
        if not self._video_media:
            return
        muted = not self._video_media.get_muted()
        self._video_media.set_muted(muted)
        btn.set_icon_name(
            "audio-volume-muted-symbolic" if muted else "audio-volume-high-symbolic"
        )

    def _on_video_volume(self, scale):
        if not self._video_media:
            return
        self._video_media.set_volume(scale.get_value())

    def _on_viewer_motion(self, ctrl, x, y):
        if self.main_stack.get_visible_child_name() != "viewer":
            return
        new_pos = (round(x), round(y))
        if getattr(self, '_last_viewer_mouse', None) == new_pos:
            return
        self._last_viewer_mouse = new_pos
        self._show_viewer_ui()
        self._reset_fade_timer()

    def _show_viewer_ui(self):
        self._cancel_fade()
        # In the editor only the edit toolbar is shown — don't let mouse motion
        # bring the filmstrip/arrows/etc. back over it.
        if getattr(self, "_editor_active", False):
            for w in self._video_fade_widgets():
                w.set_visible(False)
            self.editor_bar.set_visible(True)
            return
        for w in self._video_fade_widgets():
            w.set_visible(True)
            w.set_opacity(1.0)
            w.set_can_target(True)
        if self._video_media is None:
            self.video_controls.set_visible(False)
        else:
            # No edit button on videos — it can't edit them and just no-ops.
            self.edit_btn.set_visible(False)
            self.favorite_btn.set_margin_end(120)   # close the edit-button gap
        # Restore zoom-driven visibility.
        zoomed = getattr(self, '_viewer_zoom', 1.0) > 1.0
        if zoomed:
            self.prev_btn.set_visible(False)
            self.next_btn.set_visible(False)
            self.viewer_counter.set_visible(False)
            self.filmstrip_scroll.set_visible(False)

    def _video_fade_widgets(self):
        return [
            self.video_controls,
            self.filmstrip_scroll,
            self.viewer_counter,
            self.viewer_title_box,
            self.viewer_close_btn,
            self.viewer_delete_btn,
            self.edit_btn,
            self.info_btn,
            self.favorite_btn,
            self.prev_btn,
            self.next_btn,
        ]

    def _reset_fade_timer(self):
        if self._fade_timer_id:
            try:
                GLib.source_remove(self._fade_timer_id)
            except Exception:
                pass
        delay = 800 if self._video_media else 10_000
        self._fade_timer_id = GLib.timeout_add(delay, self._start_fade)

    def _cancel_fade(self):
        if self._fade_timer_id:
            try:
                GLib.source_remove(self._fade_timer_id)
            except Exception:
                pass
            self._fade_timer_id = None
        if self._fade_anim_id:
            try:
                GLib.source_remove(self._fade_anim_id)
            except Exception:
                pass
            self._fade_anim_id = None

    def _start_fade(self):
        self._fade_timer_id = None
        # Keep the chrome (filmstrip, buttons) up while the info popover is open —
        # re-arm so it fades normally once the popover closes.
        if getattr(self, "info_popover", None) is not None \
                and self.info_popover.get_visible():
            self._reset_fade_timer()
            return False
        self._fade_step = 0
        self._fade_anim_id = GLib.timeout_add(50, self._fade_tick)
        return False

    def _fade_tick(self):
        self._fade_step += 1
        opacity = max(0.0, 1.0 - self._fade_step / 8)  # ~400ms total
        widgets = self._video_fade_widgets()
        for w in widgets:
            w.set_opacity(opacity)
        # Backup donut never fades — it's status, not decoration.
        if hasattr(self, "_viewer_donut_btn"):
            self._viewer_donut_btn.set_opacity(1.0)
        if opacity <= 0.0:
            for w in widgets:
                w.set_visible(False)
                w.set_can_target(False)
            self._fade_anim_id = None
            return False
        return True

    def _trigger_scrub_preview(self, fraction):
        if not self._video_media:
            return
        dur = self._video_media.get_duration()
        if dur <= 0:
            return
        if not self.photos or not (0 <= self.current_index < len(self.photos)):
            return
        path = self.photos[self.current_index]
        ts_s = (int(fraction * dur / 1_000_000) // 2) * 2  # 2s bins
        self._preview_time_lbl.set_text(format_duration(ts_s))
        w = self.video_scrubber.get_width()
        if w > 0:
            rect = Gdk.Rectangle()
            rect.x = int(fraction * w)
            rect.y = -10           # lift the popover clear of the timeline/cursor
            rect.width = 1
            rect.height = 0
            self._preview_popover.set_pointing_to(rect)
            if not self._preview_popover.get_visible():
                self._preview_popover.popup()
        # Cache keyed by (video, time) so frames never leak between videos.
        key = (path, ts_s)
        pb = self._preview_cache.get(key)
        if pb is not None:
            self._preview_spinner.stop()
            self._preview_spinner.set_visible(False)
            self._preview_picture.set_pixbuf(pb)
            return
        # Frame not ready: spinner instead of a stale/other-time frame.
        self._preview_picture.set_pixbuf(None)
        self._preview_spinner.set_visible(True)
        self._preview_spinner.start()
        self._preview_pending = key
        if self._preview_debounce_id:
            GLib.source_remove(self._preview_debounce_id)
        self._preview_debounce_id = GLib.timeout_add(120, self._do_debounced_preview)

    def _on_scrubber_hover(self, ctrl, x, y):
        if not self._video_media:
            return
        w = self.video_scrubber.get_width()
        if w <= 0:
            return
        self._trigger_scrub_preview(max(0.0, min(1.0, x / w)))

    def _on_scrubber_leave(self, ctrl):
        if self._preview_debounce_id:
            GLib.source_remove(self._preview_debounce_id)
            self._preview_debounce_id = None
        self._preview_spinner.stop()
        self._preview_spinner.set_visible(False)
        self._preview_popover.popdown()

    def _do_debounced_preview(self):
        self._preview_debounce_id = None
        pending = self._preview_pending
        if pending is None or self._preview_extracting:
            return False
        if pending in self._preview_cache:
            self._apply_preview_frame(pending)
            return False
        path, ts_s = pending
        self._preview_extracting = True
        # Bind to viewer-load-id so late ffmpeg output is discarded after nav.
        load_id = self._viewer_load_id
        threading.Thread(
            target=self._extract_preview_frame,
            args=(path, ts_s, load_id),
            daemon=True
        ).start()
        return False

    def _extract_preview_frame(self, path, ts_s, load_id):
        import tempfile
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                tmp = f.name
            # -ss before -i = fast keyframe seek; -an skips audio for speed.
            subprocess.run(
                ["ffmpeg", "-ss", str(ts_s), "-i", path, "-an",
                 "-vframes", "1", "-vf", "scale=160:-1", tmp, "-y"],
                capture_output=True, timeout=8
            )
            if load_id == self._viewer_load_id:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(tmp, 160, 90, True)
                key = (path, ts_s)
                with self._pixbuf_cache_lock:
                    self._preview_cache[key] = pb
                    self._preview_cache.move_to_end(key)
                    while len(self._preview_cache) > 400:
                        self._preview_cache.popitem(last=False)
                GLib.idle_add(self._apply_preview_frame, key, load_id)
        except Exception:
            pass
        finally:
            if tmp:
                try:
                    os.unlink(tmp)  # also when ffmpeg/pixbuf loading failed
                except OSError:
                    pass
            self._preview_extracting = False
            # A newer hover may have arrived while ffmpeg ran — service it now,
            # otherwise the preview would freeze on the old frame.
            GLib.idle_add(self._service_pending_preview)

    def _service_pending_preview(self):
        pending = self._preview_pending
        if pending is None:
            return False
        if pending in self._preview_cache:
            self._apply_preview_frame(pending)
        elif not self._preview_extracting:
            if self._preview_debounce_id:
                GLib.source_remove(self._preview_debounce_id)
                self._preview_debounce_id = None
            self._do_debounced_preview()
        return False

    def _apply_preview_frame(self, key, load_id=None):
        if load_id is not None and load_id != self._viewer_load_id:
            return False
        # Skip stale frames: only show the one currently being hovered.
        if self._preview_pending is not None and key != self._preview_pending:
            return False
        with self._pixbuf_cache_lock:
            pb = self._preview_cache.get(key)
            if pb:
                self._preview_cache.move_to_end(key)
        if pb:
            if self._preview_popover.get_visible():
                self._preview_spinner.stop()
                self._preview_spinner.set_visible(False)
                self._preview_picture.set_pixbuf(pb)
        return False

    def _pause_video_for_popup(self):
        try:
            media = getattr(self, "_video_media", None)
            if media is None or not media.get_playing():
                return
            if self.main_stack.get_visible_child_name() != "viewer":
                return
            media.pause()
            self._video_paused_by_popup = True
            if hasattr(self, "video_play_btn"):
                self.video_play_btn.set_icon_name(
                    "media-playback-start-symbolic")
        except Exception:
            pass

    def _resume_video_after_popup(self):
        if not self._video_paused_by_popup:
            return
        self._video_paused_by_popup = False
        try:
            media = getattr(self, "_video_media", None)
            if media is None:
                return
            media.play()
            if hasattr(self, "video_play_btn"):
                self.video_play_btn.set_icon_name(
                    "media-playback-pause-symbolic")
        except Exception:
            pass
