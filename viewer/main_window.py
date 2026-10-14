#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — main_window.py
#  Main window: startup, header, services, shutdown
#  by LinuxGinger
# ─────────────────────────────────────────────

import os
import subprocess
import threading
import time
from collections import OrderedDict
from version import __version__ as APP_VERSION
from pixora_i18n import _
import common
from common import (Adw, ASSETS_DIR, CACHE_DIR, CONFIG_PATH, Gdk, GdkPixbuf,
    get_logo_path, GLib, Gtk, GUdev, GUDEV_AVAILABLE, load_favorites,
    log_error, log_info, log_warn, Observer, save_favorites, save_settings,
    WATCHDOG_AVAILABLE, reset_log, _I18N_STATUS)
from drives import _warm_lsblk_cache_async
from metadata import save_metadata_cache
from media import _FixedSizePaintable
from widgets import PhotoFolderHandler
from win_grid import GridMixin
from win_viewer import ViewerMixin
from win_video import VideoMixin
from win_editor import EditorMixin
from win_delete import DeleteMixin
from win_map import MapMixin
from win_places import PlacesMixin
from win_settings import SettingsMixin
from win_library import LibraryMixin
from win_backup import BackupMixin
from win_about import AboutMixin


class MainWindow(GridMixin,
                 ViewerMixin,
                 VideoMixin,
                 EditorMixin,
                 DeleteMixin,
                 MapMixin,
                 PlacesMixin,
                 SettingsMixin,
                 LibraryMixin,
                 BackupMixin,
                 AboutMixin,
                 Adw.ApplicationWindow):
    """Pixora's main window. Feature code lives in the win_*.py mixins."""

    def __init__(self, app, settings):
        super().__init__(application=app)
        self.settings        = settings
        # Pre-v1.16 migration: backup_uuid implied backup_enabled.
        if "backup_enabled" not in self.settings and self.settings.get("backup_uuid"):
            self.settings["backup_enabled"] = True
        # One "run without asking" switch drives the per-task flags; older
        # setups (and the setup wizard) only set backup_silent.
        if "auto_confirm" not in self.settings:
            auto = bool(self.settings.get("backup_silent")
                        or self.settings.get("reorganize_silent"))
            self.settings["auto_confirm"] = auto
            self.settings["backup_silent"] = auto
            self.settings["reorganize_silent"] = auto
        # Repair .desktop on every start (git-pull users skip updater.sh).
        # Off the main thread: it may run update-desktop-database.
        threading.Thread(target=self._repair_desktop_entry, daemon=True).start()
        try:
            common.THUMB_SIZE = max(200, min(500, int(settings.get("thumbnail_size", 200))))
        except Exception:
            common.THUMB_SIZE = 200
        if settings.get("dev_mode"):
            log_info(_("═══ Pixora started in Developer Mode ═══"))
            log_info(f"i18n: {_I18N_STATUS}")
            log_info(_("Config: {p}").format(p=CONFIG_PATH))
            log_info(_("Cache: {p}").format(p=CACHE_DIR))
            log_info(_("Thumbs: {px}px — favorites: {n}").format(px=common.THUMB_SIZE, n=len(load_favorites())))
            log_info(_("PID: {p} — on hang: 'kill -USR1 {p}' dumps thread-stacks").format(p=os.getpid()))
        log_info(_("Startup phase 1: MainWindow __init__ started"))
        self.photos          = []
        self.thumb_widgets   = {}
        self.date_widgets    = {}
        self.current_index   = 0
        self.settings_drives = []
        self.observer        = None
        self._loading        = False
        self._load_id        = 0
        self._viewer_load_id = 0
        self._sort_timer     = None
        self._select_mode    = False
        self._selected       = set()
        self._photo_location = {}
        self._viewer_zoom    = 1.0
        self._viewer_offset  = [0.0, 0.0]
        self._viewer_drag    = None
        self._map_widget     = None
        self._viewer_pixbuf  = None
        self._editor_active         = False
        self._editor_rotation       = 0
        self._editor_crop_mode      = False
        self._editor_display_pixbuf = None
        self._crop_rect             = None   # [x1, y1, x2, y2] widget coords
        self._crop_handle           = None   # 'tl','tr','bl','br','move'
        self._crop_rect_origin      = None   # rect state at drag start
        self._filmstrip_thumbs      = {}     # visual_pos -> pixbuf
        self._filmstrip_view_order  = []     # self.photos indices, newest-first
        self._filmstrip_load_id     = 0
        self._filmstrip_order_cache = None   # (id(photos), len(photos), [sorted_indices])
        self._video_media           = None
        self._video_poll_id         = None
        self._video_scrubbing_lock  = False
        self._video_seek_pending_id = None
        self._preview_cache         = OrderedDict()  # LRU: (path, ts_s) -> pixbuf
        self._viewer_pixbuf_cache   = OrderedDict()  # LRU: path -> pixbuf, max 3
        # Both LRUs are rebalanced from preload/preview threads and the main
        # thread at once; OrderedDict mutation isn't atomic.
        self._pixbuf_cache_lock     = threading.Lock()
        self._preview_debounce_id   = None
        self._preview_extracting    = False
        self._preview_pending       = None           # (path, ts_s) wanted next
        self._fade_timer_id         = None
        self._fade_anim_id          = None
        self._favorites             = load_favorites()
        self._favorites_save_id     = None
        self._favorites_only        = False
        self._place_filter          = set()   # names of places to filter on
        self._current_flow          = None
        self._current_row_hbox      = None
        self._current_row_width     = 0
        # Backup-manager state
        self._backup_running   = False
        self._backup_fraction  = 0.0
        self._backup_detail    = ""
        self._backup_proc      = None
        self._backup_scanning  = False
        self._backup_deduping  = False
        self._orphan_reviewing = False
        self._backup_scan_phase = 0.0
        # HEIC→JPEG library conversion state (purple donut).
        self._heic_converting = False
        self._heic_fraction = 0.0
        self._heic_detail = ""
        # Stop re-checking/nagging this session once swept clean or dismissed.
        self._heic_sweep_dismissed = False
        self._video_sweep_dismissed = False
        # Orphan state: files on USB not in Pixora. Warn after silent backup.
        self._last_scan_orphan_count = 0
        self._last_scan_orphan_rels = []
        self._last_scan_backup_dest = None
        self._backup_scan_anim_id = None
        self._backup_scan_dialog_open = False
        self._manual_scan_requested = False
        # Reorganize gate: blocks backup/sync during + 10s after reorg.
        self._reorganize_active = False
        self._reorganize_block_until = 0.0
        self._reorganize_moving = False
        self._reorganize_fraction = 0.0
        self._reorganize_anim_id = None
        # Fullscreen reorg page: thread writes, timer renders.
        self._reorganize_total_count = 0
        self._reorganize_done_count = 0
        self._reorganize_total_bytes = 0
        self._reorganize_done_bytes = 0
        self._reorganize_start_time = 0.0
        self._reorganize_current_name = ""
        self._reorganize_total_label = ""
        # Silent-mode: auto-reorg with no dialog/fullscreen. Reset per-run.
        self._reorganize_silent_run = False
        self._video_paused_by_popup = False
        self._settings_dialog = None
        self._settings_stack = None
        self._settings_tabs_bar = None
        # Structure-scan: detects mis-placed folders. Donut → dark-blue.
        self._structure_scanning = False
        self._structure_startup_scanned = False
        self._structure_popup_dismissed = False
        self._home_ready_at = None
        self._pending_update_version = None
        self._latest_release = None
        self._update_dialog_shown = False

        self.set_title(_("Pixora (Dev Mode)") if self.settings.get("dev_mode") else "Pixora")
        # Opens filling the screen. maximize() (not a giant fixed size) lets
        # every window manager — GNOME, Cinnamon, Xfce, LXQt — fit it to the
        # work area instead of pushing the title buttons off-screen.
        self.set_default_size(1280, 820)
        self.maximize()

        self.style_manager = Adw.StyleManager.get_default()
        self.style_manager.connect("notify::dark", self.on_dark_mode_changed)

        from importer_page import ImporterPage
        self.importer_page = ImporterPage(
            on_back_cb=self.close_importer,
            on_done_cb=self.on_import_done,
        )

        log_info(_("Startup phase 2: building pages…"))
        self.main_stack = Gtk.Stack()
        self.main_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.main_stack.set_transition_duration(200)
        self.main_stack.add_named(self.build_grid_page(),   "grid")
        self.main_stack.add_named(self.build_viewer_page(), "viewer")
        self.main_stack.add_named(self.build_map_page(),    "map")
        self.main_stack.add_named(self.importer_page,       "importer")
        self.main_stack.add_named(self._build_reorganize_page(), "reorganize")
        log_info(_("Startup phase 3: pages ready"))

        self.iphone_banner = Adw.Banner(title="", use_markup=False)
        self.iphone_banner.set_revealed(False)

        self.backup_pending_banner = Adw.Banner(title="", use_markup=False)
        self.backup_pending_banner.set_revealed(False)

        self.backup_done_banner = Adw.Banner(
            title="", button_label=_("OK"), use_markup=False
        )
        self.backup_done_banner.set_revealed(False)
        self.backup_done_banner.connect(
            "button-clicked",
            lambda _b: self.backup_done_banner.set_revealed(False)
        )

        # Makes it obvious you're viewing favorites only (so photos don't seem
        # "missing"); the button clears the filter.
        self.favorites_banner = Adw.Banner(
            title=_("⭐ Showing favorites only"),
            button_label=_("Show all"), use_markup=False
        )
        self.favorites_banner.set_revealed(False)
        self.favorites_banner.connect("button-clicked", self._on_show_all_favorites)

        self.toolbar_view = Adw.ToolbarView()
        self.toolbar_view.add_top_bar(self.iphone_banner)
        self.toolbar_view.add_top_bar(self.backup_pending_banner)
        self.toolbar_view.add_top_bar(self.backup_done_banner)
        self.toolbar_view.add_top_bar(self.favorites_banner)
        self.toolbar_view.add_top_bar(self.build_header())
        self.toolbar_view.set_content(self.main_stack)
        self.toolbar_view.add_bottom_bar(self.build_bottombar())
        toolbar_view = self.toolbar_view

        root_overlay = Gtk.Overlay()
        root_overlay.set_child(toolbar_view)

        splash = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        splash.set_halign(Gtk.Align.FILL)
        splash.set_valign(Gtk.Align.FILL)
        splash_css = Gtk.CssProvider()
        splash_css.load_from_string("""
            box.splash { background-color: @window_bg_color; }
            @keyframes splash-rotate {
                from { transform: rotate(0deg); }
                to   { transform: rotate(360deg); }
            }
            picture.splash-icon { animation: splash-rotate 24s linear infinite; }
            progressbar.splash-progress trough,
            progressbar.splash-progress progress {
                min-height: 6px;
                border-radius: 9999px;
            }
        """)
        splash.add_css_class("splash")
        splash.get_style_context().add_provider(splash_css, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        splash_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        splash_inner.set_halign(Gtk.Align.CENTER)
        splash_inner.set_valign(Gtk.Align.CENTER)
        splash_inner.set_vexpand(True)

        # Hi-res render + fixed intrinsic keeps the SVG crisp at 128px.
        try:
            _icon_pb = GdkPixbuf.Pixbuf.new_from_file_at_size(
                os.path.join(ASSETS_DIR, "pixora-icon.svg"), 512, 512)
            splash_icon = Gtk.Picture.new_for_paintable(
                _FixedSizePaintable(Gdk.Texture.new_for_pixbuf(_icon_pb), 128, 128))
        except Exception:
            splash_icon = Gtk.Image.new_from_icon_name("image-x-generic-symbolic")
            splash_icon.set_pixel_size(96)
        splash_icon.add_css_class("splash-icon")
        splash_icon.set_halign(Gtk.Align.CENTER)
        splash_icon.get_style_context().add_provider(splash_css, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        splash_lbl = Gtk.Label(label=_("Pixora is starting…"))
        splash_lbl.add_css_class("dim-label")

        self._splash_bar = Gtk.ProgressBar()
        self._splash_bar.set_size_request(300, -1)
        self._splash_bar.set_halign(Gtk.Align.CENTER)
        self._splash_bar.set_margin_top(10)
        self._splash_bar.add_css_class("splash-progress")
        self._splash_bar.get_style_context().add_provider(splash_css, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        splash_inner.append(splash_icon)
        splash_inner.append(self._splash_bar)
        splash_inner.append(splash_lbl)
        splash.append(splash_inner)

        _splash_ver = APP_VERSION
        if _splash_ver:
            splash_ver_lbl = Gtk.Label(label=f"Pixora {_splash_ver}")
            splash_ver_lbl.add_css_class("dim-label")
            splash_ver_lbl.add_css_class("caption")
            splash_ver_lbl.set_margin_bottom(16)
            splash.append(splash_ver_lbl)

        root_overlay.add_overlay(splash)
        self._splash = splash
        self._splash_start = time.time()
        self._splash_prewarm_done = False
        GLib.timeout_add(80, self._update_splash)
        threading.Thread(target=self._prewarm_gstreamer, daemon=True).start()

        self.set_content(root_overlay)

        photo_path = self.settings.get("photo_path", "")
        if photo_path and not os.path.isdir(photo_path):
            parent = os.path.dirname(os.path.abspath(photo_path))
            # Create a missing library folder only on a disk that is there.
            # If its drive is unplugged, the parent is missing too: don't
            # create an empty look-alike under /media (load_photos then
            # shows the empty state instead of crashing).
            if os.path.isdir(parent):
                try:
                    os.makedirs(photo_path, exist_ok=True)
                except OSError as e:
                    log_warn(_("Could not create the photo folder: {err}").format(err=e))

        self.set_hide_on_close(False)
        Gtk.Settings.get_default().set_property("gtk-decoration-layout", "icon:minimize,close")

        log_info(_("Startup phase 4: loading photos scheduled via idle_add"))
        GLib.idle_add(self.load_photos)
        # Welcome tour is scheduled from _load_done (2s after the grid appears),
        # so it never shows on top of the loading spinner.
        self.connect("close-request", self.on_close)
        GLib.idle_add(self._check_for_update)
        threading.Thread(target=self._start_services, daemon=True).start()
        # Pre-warm drive cache so first Settings/backup click skips the 5s lsblk.
        _warm_lsblk_cache_async()
        # Apply animation pref to every Gtk.Stack (0ms when disabled).
        self._apply_animations_state()
        # 5s delay: skip startup-load burst, then check renderer + frame-timing.
        GLib.timeout_add_seconds(5, self._start_perf_check)
        # 5s = "startup survived" → un-blacklist current renderer. The
        # .gsk_pending sentinel itself is cleared on Gio.Application::shutdown
        # (clean exit only), so post-startup crashes still trigger recovery.
        GLib.timeout_add_seconds(5, self._unblacklist_current_gsk)
        # If main.py reverted a crashing renderer, notify user once.
        if self.settings.get("gsk_recent_crash"):
            GLib.idle_add(self._show_gsk_recovery_popup)
        self._ios_device_present = False
        self._recovery_prompt_active = False
        # _poll_import_device returns True to rearm the 10s timer; idle_add
        # would re-fire it as fast as possible, fork-bombing the main thread.
        GLib.idle_add(self._poll_import_device_once)
        GLib.timeout_add_seconds(10, self._poll_import_device)
        self._setup_usb_monitor()
        self._backup_drive_last_seen = False
        GLib.timeout_add_seconds(8, self._poll_backup_drive)
        GLib.idle_add(self._check_pending_backup)
        GLib.timeout_add_seconds(300, self._periodic_scan)
        # Periodic save — otherwise a crash loses all cache work.
        def _periodic_save_cache():
            save_metadata_cache()
            return True
        GLib.timeout_add_seconds(300, _periodic_save_cache)

    def _unblacklist_current_gsk(self):
        """Survived 5s startup → drop current renderer from the crash blacklist."""
        current = self.settings.get("gsk_renderer", "auto")
        bl = self.settings.get("gsk_renderer_crashed") or []
        if current in bl:
            bl = [x for x in bl if x != current]
            self.settings["gsk_renderer_crashed"] = bl
            try:
                save_settings(self.settings)
            except Exception:
                pass
        return False

    def _show_gsk_recovery_popup(self):
        renderer = self.settings.get("gsk_recent_crash", "") or "?"
        # Clear flag immediately so a refresh doesn't re-show it.
        self.settings["gsk_recent_crash"] = ""
        try:
            save_settings(self.settings)
        except Exception:
            pass
        dlg = Adw.AlertDialog(
            heading=_("Pixora has crashed"),
            body=_("The “{r}” rendering backend made Pixora crash, so it has "
                   "been switched back to Automatic.").format(r=renderer),
        )
        dlg.add_response("ok", _("OK"))
        dlg.set_default_response("ok")
        dlg.set_close_response("ok")
        try:
            self._present_dialog(dlg)
        except Exception:
            try:
                dlg.present(self)
            except Exception:
                pass
        return False

    def _set_toolbars_revealed(self, visible):
        """Skip reveal API when animations off; caller already hid header/bottom."""
        enabled = bool(self.settings.get("animations_enabled", True))
        if not enabled:
            return
        try:
            self.toolbar_view.set_reveal_top_bars(visible)
            self.toolbar_view.set_reveal_bottom_bars(visible)
        except Exception:
            pass

    def _apply_animations_state(self):
        """Gate Adwaita transition-durations on user preference; 0ms when off."""
        enabled = bool(self.settings.get("animations_enabled", True))
        # GTK-wide switch kills ALL default CSS transitions when False.
        try:
            gtk_settings = Gtk.Settings.get_default()
            if gtk_settings is not None:
                gtk_settings.set_property("gtk-enable-animations", enabled)
        except Exception:
            pass
        main_d = 200 if enabled else 0
        content_d = 150 if enabled else 0
        bottom_d = 150 if enabled else 0
        if hasattr(self, "main_stack"):
            self.main_stack.set_transition_duration(main_d)
        if hasattr(self, "content_stack"):
            self.content_stack.set_transition_duration(content_d)
        if hasattr(self, "bottom_stack"):
            self.bottom_stack.set_transition_duration(bottom_d)
        # If Settings is open, live-update its tab-switch stack too.
        stack = getattr(self, "_settings_stack", None)
        if stack is not None:
            try:
                stack.set_transition_duration(450 if enabled else 0)
                stack.set_transition_type(
                    Gtk.StackTransitionType.SLIDE_LEFT_RIGHT if enabled
                    else Gtk.StackTransitionType.NONE
                )
            except Exception:
                pass
        # Toggle the CSS class disabling hover/active transforms on the tab bar.
        bar = getattr(self, "_settings_tabs_bar", None)
        if bar is not None:
            try:
                if enabled:
                    bar.remove_css_class("no-anim")
                else:
                    bar.add_css_class("no-anim")
            except Exception:
                pass
        # viewer_stack re-reads animations_enabled each swap — no live push needed.

    def _start_perf_check(self):
        """Detect slow render: Cairo renderer OR slow frame-timing over 30s."""
        if self.settings.get("perf_warning_dismissed"):
            return False
        try:
            native = self.get_native()
            renderer = native.get_renderer() if native is not None else None
            rname = type(renderer).__name__ if renderer is not None else ""
            if "Cairo" in rname:
                self._show_perf_warning_popup()
                return False
        except Exception:
            pass
        self._perf_frame_times = []
        try:
            self._perf_tick_id = self.add_tick_callback(self._perf_sample)
        except Exception:
            self._perf_tick_id = None
            return False
        # Hard stop after 30s regardless of samples.
        GLib.timeout_add_seconds(30, self._stop_perf_sampling)
        return False

    def _perf_sample(self, widget, frame_clock):
        if self.settings.get("perf_warning_dismissed"):
            return False
        try:
            t = frame_clock.get_frame_time()
        except Exception:
            return False
        self._perf_frame_times.append(t)
        if len(self._perf_frame_times) > 60:
            self._perf_frame_times = self._perf_frame_times[-60:]
        if len(self._perf_frame_times) >= 30:
            durations = [
                self._perf_frame_times[i] - self._perf_frame_times[i - 1]
                for i in range(1, len(self._perf_frame_times))
            ]
            # >33ms = below 30fps = "slow frame".
            slow = sum(1 for d in durations if d > 33_000)
            if slow / len(durations) > 0.6:
                self._show_perf_warning_popup()
                self._stop_perf_sampling()
                return False
        return True

    def _stop_perf_sampling(self):
        tid = getattr(self, "_perf_tick_id", None)
        if tid is not None:
            try:
                self.remove_tick_callback(tid)
            except Exception:
                pass
            self._perf_tick_id = None
        return False

    def _show_perf_warning_popup(self):
        if self.settings.get("perf_warning_dismissed"):
            return
        dlg = Adw.AlertDialog(
            heading=_("Pixora is running slowly on this system"),
            body=_("This can happen without GPU acceleration. You can reduce animations under Settings → Advanced → Performance to make Pixora feel smoother."),
        )
        dlg.add_response("dismiss", _("Don't show again"))
        dlg.add_response("open", _("Open Settings"))
        dlg.set_default_response("open")
        dlg.set_close_response("dismiss")
        dlg.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", self._on_perf_warning_response)
        try:
            self._present_dialog(dlg)
        except Exception:
            try:
                dlg.present(self)
            except Exception:
                pass

    def _on_perf_warning_response(self, dlg, response):
        self.settings["perf_warning_dismissed"] = True
        try:
            save_settings(self.settings)
        except Exception as e:
            log_error(_("Failed to save perf_warning_dismissed: {e}").format(e=e))
        if response == "open":
            try:
                self.on_settings_clicked(None)
            except Exception:
                pass

    def _start_services(self):
        # usbmuxd is started by udev/systemd (as root) when an iPhone is
        # plugged in; starting it ourselves as a normal user can't work.
        pass

    def _setup_usb_monitor(self):
        """Listen to udev USB events for automatic iPhone detection."""
        self._udev_client = None
        if not GUDEV_AVAILABLE:
            return
        try:
            self._udev_client = GUdev.Client(subsystems=["usb"])
            self._udev_client.connect("uevent", self._on_usb_event)
        except Exception as e:
            log_error(_("GUdev monitor failed to start: {err}").format(err=e))
            self._udev_client = None

    def _on_usb_event(self, client, action, device):
        if action == "add":
            try:
                vendor = device.get_property("ID_VENDOR_ID")
            except Exception:
                vendor = None
            if vendor != "05ac":  # Apple
                return
            # Apple keyboards, mice and chargers share the vendor id; iPhones
            # and iPads use product ids 0x12xx. Unknown id → assume a device.
            try:
                model = (device.get_property("ID_MODEL_ID") or "").lower()
            except Exception:
                model = ""
            if model and not model.startswith("12"):
                return
            log_info(_("Apple USB device connected (vendor=05ac) — check after 2.5s"))
            # Wait briefly so usbmuxd sees the device before we check.
            GLib.timeout_add(2500, self._post_apple_plugin_check)
        elif action in ("remove", "unbind"):
            # Device properties are usually gone on removal, so just re-check
            # promptly; the poll clears the button and shows the disconnect
            # banner if the iPhone really left.
            GLib.timeout_add(800, self._poll_import_device_once)

    def _post_apple_plugin_check(self):
        # Don't interrupt an open importer.
        try:
            if self.main_stack.get_visible_child_name() == "importer":
                return False
        except Exception:
            pass
        if self._recovery_prompt_active:
            return False
        self._recovery_prompt_active = True
        self._set_iphone_banner(_("📱 iPhone or iPad detected…"))
        threading.Thread(target=self._iphone_recovery_flow, daemon=True).start()
        return False

    def _iphone_recovery_flow(self):
        """Wait patiently for the device and the Trust handshake.

        A freshly-plugged iPhone/iPad appears on the USB bus before it is
        trusted, and the user needs time to unlock and tap Trust. We NEVER reset
        usbmuxd while a device is visible — that kills the in-progress trust
        handshake (the old 'unplug and replug to make it work' bug). usbmuxd is
        only reset when nothing is on the bus at all.
        """
        try:
            from importer_page import pair_state

            # Wait through the Trust handshake for a device already on the bus.
            def _wait_for_trust(udid):
                trust_prompted = False
                deadline = time.time() + 60
                while time.time() < deadline:
                    state = pair_state(udid)
                    if state == "paired":
                        GLib.idle_add(self._iphone_flow_success, False)
                        return True
                    if state == "nodevice":
                        return False  # fell off the bus → let caller reset
                    if not trust_prompted:
                        trust_prompted = True
                        GLib.idle_add(
                            self._set_iphone_banner,
                            _("📱 Tap “Trust” on your device to continue"))
                    time.sleep(1.5)
                    udid = self._idevice_udid() or udid
                # Plugged but never trusted — stand down quietly; the importer's
                # own Trust loop guides the user when they start an import.
                GLib.idle_add(self._iphone_flow_idle)
                return True

            # Phase 1 — is the device already on the bus (~9s grace)?
            for _attempt in range(6):
                udid = self._idevice_udid()
                if udid:
                    if _wait_for_trust(udid):
                        return
                    break
                time.sleep(1.5)

            # Phase 2 — nothing on the bus: usbmuxd may be wedged. Resetting
            # needs the admin password, so offer it — never pop a password
            # prompt the user didn't ask for.
            log_warn("No device on the bus — offering a usbmuxd reset")
            self._wait_for_trust = _wait_for_trust
            GLib.idle_add(self._offer_connection_repair)
        except Exception as e:
            log_error("recovery flow error: {}".format(e))
            GLib.idle_add(self._iphone_flow_fail)

    def _offer_connection_repair(self):
        self._recovery_prompt_active = False
        self.iphone_banner.set_title(
            _("⚠️ Your device isn't showing up. Repair the connection? "
              "(asks for your password)"))
        self.iphone_banner.set_button_label(_("Repair"))
        if not getattr(self, "_repair_hid", None):
            self._repair_hid = self.iphone_banner.connect(
                "button-clicked", self._on_repair_connection_clicked)
        self.iphone_banner.set_revealed(True)
        return False

    def _on_repair_connection_clicked(self, _banner):
        self.iphone_banner.set_button_label(None)
        if self._recovery_prompt_active:
            return
        self._recovery_prompt_active = True
        self._set_iphone_banner(_("🔧 Restoring connection, please wait…"))
        threading.Thread(target=self._repair_connection_thread, daemon=True).start()

    def _repair_connection_thread(self):
        try:
            r = subprocess.run(
                ["pkexec", "sh", "-c",
                 "killall usbmuxd 2>/dev/null; sleep 0.5; usbmuxd"],
                capture_output=True, text=True, timeout=60
            )
            reset_ok = (r.returncode == 0)
        except Exception as e:
            log_error("usbmuxd reset error: {}".format(e))
            reset_ok = False
        if not reset_ok:
            GLib.idle_add(self._iphone_flow_fail)
            return
        time.sleep(2.5)
        udid = self._idevice_udid()
        wait = getattr(self, "_wait_for_trust", None)
        if udid and wait is not None and wait(udid):
            return  # wait() already showed "connected" (or stood down)
        GLib.idle_add(self._iphone_flow_fail)

    def _idevice_udid(self):
        """First connected device's UDID, or '' when none is on the bus."""
        try:
            result = subprocess.run(
                ["idevice_id", "-l"],
                capture_output=True, text=True, timeout=4
            )
            for line in result.stdout.splitlines():
                if line.strip():
                    return line.strip()
        except Exception:
            pass
        return ""

    def _iphone_flow_success(self, was_reset):
        self._recovery_prompt_active = False
        self._update_import_btn_state(True)
        self._set_iphone_banner(
            _("✅ Connected — tap Trust on your device if asked")
            if was_reset else _("✅ Connected — your device is ready")
        )
        GLib.timeout_add_seconds(4, self._clear_iphone_banner)
        return False

    def _iphone_flow_idle(self):
        # Device is plugged but not trusted yet: keep the import button lit and
        # drop the banner — no error, the importer guides Trust on import.
        self._recovery_prompt_active = False
        self._update_import_btn_state(True)
        self._clear_iphone_banner()
        return False

    def _iphone_flow_fail(self):
        self._recovery_prompt_active = False
        self._set_iphone_banner(
            _("⚠️ Device not recognised — unplug it, reconnect, and tap Trust")
        )
        GLib.timeout_add_seconds(8, self._clear_iphone_banner)
        return False

    def _set_iphone_banner(self, text):
        self.iphone_banner.set_button_label(None)
        self.iphone_banner.set_title(text)
        self.iphone_banner.set_revealed(True)
        return False

    def _clear_iphone_banner(self):
        self.iphone_banner.set_revealed(False)
        return False

    def _poll_import_device_once(self):
        self._poll_import_device()
        return False

    def _poll_import_device(self):
        # Skip while importer open (conflicts with libimobiledevice pair/mount).
        try:
            if self.main_stack.get_visible_child_name() == "importer":
                return True
        except Exception:
            pass

        def check():
            from importer_page import detect_iphone_state
            state, _udid = detect_iphone_state()
            if state == "unknown":
                return  # a slow/failed check is not an unplugged iPhone
            GLib.idle_add(self._update_import_btn_state, state == "found")
        threading.Thread(target=check, daemon=True).start()
        return True

    def _update_import_btn_state(self, has_device):
        if has_device == self._ios_device_present:
            return False
        self._ios_device_present = has_device
        ctx = self.import_btn.get_style_context()
        if has_device:
            ctx.add_class("pixora-import-active")
            self.import_btn.set_tooltip_text(
                _("iPhone or iPad detected — click to import")
            )
        else:
            ctx.remove_class("pixora-import-active")
            self.import_btn.set_tooltip_text(_("Import from iPhone or iPad"))
            # Tell the user the device left (skip while the importer is open;
            # it has its own disconnect flow).
            try:
                in_importer = self.main_stack.get_visible_child_name() == "importer"
            except Exception:
                in_importer = False
            if not in_importer:
                self._set_iphone_banner(_("🔌 iPhone disconnected"))
                GLib.timeout_add_seconds(4, self._clear_iphone_banner)
        return False

    def _prewarm_gstreamer(self):
        """Init GStreamer in background so first video opens fast."""
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            Gst.init(None)
            pipeline = Gst.parse_launch("audiotestsrc wave=4 num-buffers=1 ! fakesink")
            pipeline.set_state(Gst.State.PLAYING)
            pipeline.get_bus().timed_pop_filtered(
                Gst.CLOCK_TIME_NONE,
                Gst.MessageType.EOS | Gst.MessageType.ERROR
            )
            pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass
        finally:
            GLib.idle_add(self._on_prewarm_done)

    def _on_prewarm_done(self):
        self._splash_prewarm_done = True
        return False

    def _update_splash(self):
        elapsed = time.time() - self._splash_start
        min_time = 2.0
        max_time = 30.0
        if self._splash_prewarm_done:
            # Fill bar quickly then close.
            self._splash_bar.set_fraction(min(elapsed / min_time, 1.0))
            if elapsed >= min_time:
                self._fade_out_splash()
                return False
        else:
            # Pulse toward ~80%; last 20% reserved for prewarm completion.
            self._splash_bar.set_fraction(min(elapsed / max_time * 0.8, 0.8))
            if elapsed >= max_time:
                self._fade_out_splash()
                return False
        return True

    def _fade_out_splash(self):
        if getattr(self, "_splash_fading", False):
            return
        self._splash_fading = True

        def _step():
            op = self._splash.get_opacity() - 0.06
            if op <= 0.0:
                self._splash.set_visible(False)
                return False
            self._splash.set_opacity(op)
            return True
        GLib.timeout_add(16, _step)

    def is_dark(self):
        return self.style_manager.get_dark()

    def _load_header_logo_paintable(self):
        # Render SVG at 4× display size for crisp downscale; wrap in a paintable
        # that reports 140×36 intrinsic so the HeaderBar doesn't grow to the
        # texture's pixel dimensions.
        logo_path = get_logo_path(self.is_dark())
        if not os.path.exists(logo_path):
            return None
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(logo_path, 560, 144)
            tex = Gdk.Texture.new_for_pixbuf(pb)
            return _FixedSizePaintable(tex, 140, 36)
        except Exception:
            return None

    def on_dark_mode_changed(self, manager, _pspec):
        paintable = self._load_header_logo_paintable()
        if paintable is not None:
            self.logo_picture.set_paintable(paintable)

    def start_watcher(self, path):
        if not WATCHDOG_AVAILABLE or not os.path.exists(path):
            return
        self.stop_watcher()
        handler       = PhotoFolderHandler(self.reload_photos)
        self.observer = Observer()
        self.observer.schedule(handler, path, recursive=True)
        self.observer.start()

    def stop_watcher(self):
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None

    def reload_photos(self):
        self._photo_location.clear()
        self.load_photos()
        return False

    def _schedule_save_favorites(self):
        if self._favorites_save_id is not None:
            try:
                GLib.source_remove(self._favorites_save_id)
            except Exception:
                pass
        self._favorites_save_id = GLib.timeout_add(250, self._flush_save_favorites)

    def _flush_save_favorites(self):
        self._favorites_save_id = None
        try:
            save_favorites(self._favorites)
        except Exception as e:
            log_error(_("Favorites save error: {err}").format(err=e))
        return False

    def on_close(self, window):
        # Open settings blocks close-request from GNOME Shell; close it first.
        if self._settings_dialog is not None:
            try:
                self._settings_dialog.close()
            except Exception:
                pass
        # Confirm close during backup/reorganize.
        if not getattr(self, "_close_confirmed", False):
            if self._backup_running:
                body = _("Pixora is backing up to your USB drive. Closing now will interrupt the backup.")
            elif self._reorganize_moving:
                body = _("Pixora is cleaning up the folder structure. Closing now may leave photos half-moved.")
            elif self._heic_converting:
                body = _("Pixora is converting photos or videos. Closing now stops the conversion; files not yet converted stay as they are.")
            elif (self._importer_open()
                  and self.importer_page.state in self.importer_page._BUSY_STATES):
                body = _("Pixora is importing from your device. Closing now stops the import; files already copied stay in your library.")
            else:
                body = None
            if body is not None:
                dlg = Adw.AlertDialog(
                    heading=_("Quit Pixora?"),
                    body=body,
                )
                dlg.add_response("cancel", _("Cancel"))
                dlg.add_response("close", _("Close anyway"))
                dlg.set_response_appearance(
                    "close", Adw.ResponseAppearance.DESTRUCTIVE
                )
                dlg.set_default_response("cancel")
                dlg.connect("response", self._on_close_guard_response)
                self._present_dialog(dlg)
                return True  # dialog decides
        log_info(_("Pixora shutting down — cleaning up…"))
        # Stop an import and release the phone's FUSE mount.
        try:
            self.importer_page.deactivate()
        except Exception:
            pass
        # Kill rsync cleanly so backup stops instead of hanging.
        if self._backup_running and self._backup_proc is not None:
            try:
                self._backup_proc.kill()
            except Exception:
                pass
        try:
            self._load_id += 1
            self._viewer_load_id += 1
            self._filmstrip_load_id += 1
        except Exception:
            pass
        # Cancel ALL GLib timers BEFORE teardown — stray fire would crash.
        for attr in ("_favorites_save_id", "_sort_timer", "_fade_timer_id",
                     "_fade_anim_id", "_preview_debounce_id",
                     "_video_seek_pending_id", "_video_poll_id",
                     "_thumb_size_timer", "_map_ready_fallback_id",
                     "_nav_debounce_id"):
            tid = getattr(self, attr, None)
            if tid is not None:
                try:
                    GLib.source_remove(tid)
                except Exception:
                    pass
                setattr(self, attr, None)
        try:
            save_favorites(self._favorites)
        except Exception:
            pass
        try:
            save_metadata_cache()
        except Exception:
            pass
        self.stop_watcher()
        try:
            self._udev_client = None
        except Exception:
            pass
        # Stop video + MediaFile so GStreamer threads release.
        try:
            self._stop_video()
            if getattr(self, "_video_media", None):
                try:
                    self._video_media.unprepare() if hasattr(self._video_media, "unprepare") else None
                except Exception:
                    pass
                self._video_media = None
        except Exception:
            pass
        # Release big structures so RAM frees.
        try:
            self.photos = []
            self._filmstrip_order_cache = None
            self.thumb_widgets = {}
            self.date_widgets = {}
            self._filmstrip_thumbs = {}
            with self._pixbuf_cache_lock:
                self._preview_cache.clear()
                self._viewer_pixbuf_cache.clear()
            self._photo_location.clear()
            if hasattr(self, "_viewer_pixbuf"):
                self._viewer_pixbuf = None
            if hasattr(self, "_editor_display_pixbuf"):
                self._editor_display_pixbuf = None
            if hasattr(self, "_map_widget") and self._map_widget:
                self._map_widget = None
        except Exception as e:
            log_error(_("Cleanup error: {err}").format(err=e))
        try:
            import gc
            gc.collect()
        except Exception:
            pass
        # Truncate dev log for fresh next session.
        reset_log()
        # Force-exit 2s: GStreamer/PIL/gvfs threads otherwise keep the proc alive.
        def _force_exit():
            try:
                print(_("Pixora process forcing exit (lingering threads)"), flush=True)
            except Exception:
                pass
            os._exit(0)
        threading.Timer(2.0, _force_exit).start()
        return False

    def build_header(self):
        self.header = Adw.HeaderBar()
        self.header.add_css_class("flat")

        self.logo_picture = Gtk.Picture()
        paintable = self._load_header_logo_paintable()
        if paintable is not None:
            self.logo_picture.set_paintable(paintable)
        self.logo_picture.set_size_request(140, 36)
        self.logo_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.header.pack_start(self.logo_picture)

        self.sort_model = Gtk.StringList()
        for item in [_("Date (newest first)"), _("Date (oldest first)")]:
            self.sort_model.append(item)

        self.sort_combo = Gtk.DropDown(model=self.sort_model)
        self.sort_combo.set_size_request(180, -1)
        saved_sort = self.settings.get("sort_index", 0)
        if saved_sort not in (0, 1):
            saved_sort = 0
        self.sort_combo.set_selected(saved_sort)
        self.sort_combo.connect("notify::selected", self.on_sort_changed)
        self.header.pack_start(self.sort_combo)

        # Filters grouped in one menu so the header doesn't overflow.
        filter_btn = Gtk.MenuButton()
        filter_btn.set_label(_("Filter"))   # icon themes vary; a label always shows
        filter_btn.add_css_class("flat")
        filter_btn.set_tooltip_text(_("Filter"))
        self._filter_pop = Gtk.Popover()
        self._filter_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._filter_box.set_margin_top(8)
        self._filter_box.set_margin_bottom(8)
        self._filter_box.set_margin_start(10)
        self._filter_box.set_margin_end(10)
        self._filter_pop.set_child(self._filter_box)
        # Rebuilt each time it opens so it always reflects the current places.
        self._filter_pop.connect(
            "notify::visible",
            lambda p, _ps: self._populate_filter_menu() if p.get_visible() else None)
        filter_btn.set_popover(self._filter_pop)
        self._populate_filter_menu()
        self.header.pack_end(filter_btn)

        self.map_btn = Gtk.Button(label=_("🗺"))
        self.map_btn.add_css_class("flat")
        self.map_btn.set_tooltip_text(_("Map view"))
        self.map_btn.connect("clicked", self.open_map)
        self.header.pack_end(self.map_btn)

        self.import_btn = Gtk.Button(icon_name="phone-symbolic")
        self.import_btn.add_css_class("flat")
        self.import_btn.set_tooltip_text(_("Import from iPhone or iPad"))
        self.import_btn.connect("clicked", self.open_importer)
        self._import_btn_css = Gtk.CssProvider()
        self._import_btn_css.load_from_string(
            "button.pixora-import-active {"
            "  background-color: #e95420;"
            "  color: white;"
            "}"
            "button.pixora-import-active:hover {"
            "  background-color: #d84a15;"
            "}"
        )
        self.import_btn.get_style_context().add_provider(
            self._import_btn_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.header.pack_end(self.import_btn)

        self.select_btn = Gtk.Button(label=_("Select"))
        self.select_btn.add_css_class("flat")
        self.select_btn.connect("clicked", self.toggle_select_mode)
        self.header.pack_end(self.select_btn)

        self.settings_btn = Gtk.Button(icon_name="preferences-system-symbolic")
        self.settings_btn.add_css_class("flat")
        self.settings_btn.set_tooltip_text(_("Settings"))
        self.settings_btn.connect("clicked", self.on_settings_clicked)
        self.header.pack_end(self.settings_btn)

        # Backup-progress donut; wrapped in a Button so hover/click opens popover.
        self._backup_donut = Gtk.DrawingArea()
        self._backup_donut.set_size_request(24, 24)
        self._backup_donut.set_valign(Gtk.Align.CENTER)
        self._backup_donut.set_draw_func(self._draw_backup_donut)
        self._backup_donut_btn = Gtk.Button()
        self._backup_donut_btn.add_css_class("flat")
        self._backup_donut_btn.add_css_class("circular")
        self._backup_donut_btn.set_child(self._backup_donut)
        self._backup_donut_btn.set_tooltip_text(_("Backup in progress"))
        self._backup_donut_btn.set_visible(False)
        self._backup_donut_btn.connect("clicked", self._on_backup_donut_clicked)
        self.header.pack_end(self._backup_donut_btn)

        return self.header

    def _show_welcome(self):
        pages = [
            ("🎉", _("Welcome to Pixora"),
             _("All your photos and videos in one place — neatly organised and "
               "private on your own computer.")),
            ("📥", _("Import"),
             _("Plug in your iPhone or iPad and import in one go. Your edits "
               "come along, and duplicates are skipped automatically.")),
            ("🗂️", _("Automatically organised"),
             _("Every photo is filed by date automatically. HEIC photos open "
               "everywhere — and your originals stay untouched.")),
            ("🗺️", _("Map"),
             _("See on the map where your photos were taken. Mark the places "
               "that matter, like home, and jump back with one click.")),
            ("💾", _("Backup"),
             _("Plug in a USB drive and Pixora backs up after every import — so "
               "you always have a second copy.")),
            ("✨", _("You’re all set"),
             _("You can change everything later in Settings. Enjoy Pixora!")),
        ]

        win = Adw.Window()
        win.set_title(_("Welcome"))
        win.set_transient_for(self)
        win.set_modal(True)
        win.set_default_size(460, 480)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        carousel = Adw.Carousel()
        carousel.set_vexpand(True)
        page_widgets = []
        for emoji, title, body in pages:
            pg = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
            pg.set_valign(Gtk.Align.CENTER)
            pg.set_halign(Gtk.Align.CENTER)
            pg.set_margin_start(36); pg.set_margin_end(36)
            icon = Gtk.Label(label=emoji)
            ic_css = Gtk.CssProvider()
            ic_css.load_from_string("label { font-size: 64px; }")
            icon.get_style_context().add_provider(
                ic_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            ttl = Gtk.Label(label=title)
            ttl.add_css_class("title-1")
            ttl.set_justify(Gtk.Justification.CENTER)
            ttl.set_wrap(True)
            txt = Gtk.Label(label=body)
            txt.add_css_class("dim-label")
            txt.set_justify(Gtk.Justification.CENTER)
            txt.set_wrap(True)
            pg.append(icon); pg.append(ttl); pg.append(txt)
            carousel.append(pg)
            page_widgets.append(pg)
        outer.append(carousel)

        dots = Adw.CarouselIndicatorDots()
        dots.set_carousel(carousel)
        dots.set_margin_top(8)
        outer.append(dots)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_margin_top(12); bar.set_margin_bottom(16)
        bar.set_margin_start(16); bar.set_margin_end(16)
        skip_btn = Gtk.Button(label=_("Skip"))
        skip_btn.add_css_class("flat")
        skip_btn.connect("clicked", lambda b: self._finish_welcome(win))
        spacer = Gtk.Box(); spacer.set_hexpand(True)
        next_btn = Gtk.Button(label=_("Next"))
        next_btn.add_css_class("suggested-action")

        def _on_next(_b):
            idx = int(carousel.get_position())
            if idx >= len(page_widgets) - 1:
                self._finish_welcome(win)
            else:
                carousel.scroll_to(page_widgets[idx + 1], True)
        next_btn.connect("clicked", _on_next)

        def _on_page(_c, _i):
            last = int(carousel.get_position()) >= len(page_widgets) - 1
            next_btn.set_label(_("Got it") if last else _("Next"))
            skip_btn.set_visible(not last)
        carousel.connect("page-changed", _on_page)

        bar.append(skip_btn); bar.append(spacer); bar.append(next_btn)
        outer.append(bar)

        win.set_content(outer)
        win.present()
        return False

    def _finish_welcome(self, win):
        self.settings["welcome_shown"] = True
        try:
            save_settings(self.settings)
        except Exception:
            pass
        try:
            win.close()
        except Exception:
            pass

    def _importer_open(self):
        try:
            return self.main_stack.get_visible_child_name() == "importer"
        except Exception:
            return False

    def open_importer(self, btn=None):
        log_info(_("Importer opened (iOS device {state})").format(
            state=_("present") if self._ios_device_present else _("not detected")
        ))
        self.header.set_visible(False)
        self.bottom_stack.set_visible(False)
        self.main_stack.set_visible_child_name("importer")
        self.importer_page.activate()

    def close_importer(self):
        log_info(_("Importer closed"))
        self.importer_page.deactivate()
        self.header.set_visible(True)
        self.bottom_stack.set_visible(True)
        self.main_stack.set_visible_child_name("grid")

    def on_import_done(self, count):
        # The importer stays on its summary page (incl. any files that failed)
        # until the user goes back; the library refreshes underneath.
        if count and count > 0:
            self.reload_photos()
            # Mark pending-backup by bumping last_import_time.
            self.settings["last_import_time"] = time.time()
            try:
                save_settings(self.settings)
            except Exception:
                pass
            GLib.idle_add(self._check_pending_backup)
