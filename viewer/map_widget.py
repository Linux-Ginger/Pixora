#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — map_widget.py
#  Leaflet map in a WebKit view
#  by LinuxGinger
# ─────────────────────────────────────────────

import json
import math
import os
import subprocess
from pixora_i18n import _
import common
from common import (GdkPixbuf, GLib, Gtk, log_error, log_info, log_warn,
    TILE_CACHE_DIR, WebKit2, WEBKIT_AVAILABLE, _webkit_load_error)
from media import get_cache_path


class MapWidget(Gtk.Box):
    def __init__(self, markers, open_photo_cb, status_cb=None, tile_url=None,
                 initial_view=None, home=None, places=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.markers = markers
        self.open_photo_cb = open_photo_cb
        self.status_cb = status_cb
        self._tile_url = tile_url
        self._initial_view = initial_view
        self._home = home              # (lat, lon) or None
        self._places = places or []    # [{name, lat, lon}, …] extra places
        self.set_vexpand(True)
        self.set_hexpand(True)
        self._pending_markers = list(markers) if markers else []

        if not WEBKIT_AVAILABLE:
            self._show_fallback(
                _("WebKitGTK failed to load.") + "\n\n"
                + _("Install one of:") + "\n"
                "  sudo apt install gir1.2-webkit-6.0\n"
                "  sudo apt install gir1.2-webkit2-4.1\n"
                + (f"\n{_('Technical error')}: {_webkit_load_error}" if _webkit_load_error else "")
            )
            log_error(_("WebKit unavailable: {err}").format(err=_webkit_load_error))
            return

        try:
            self._init_webview()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            log_error(_("WebView init crashed:\n{tb}").format(tb=tb))
            self._show_fallback(
                _("Map view failed to start.") + "\n\n"
                + _("Error: {err}").format(err=e) + "\n\n"
                + _("Check /home/beau/.cache/pixora/pixora.log for more.")
            )

    def _show_fallback(self, msg):
        lbl = Gtk.Label(label=msg)
        lbl.set_vexpand(True)
        lbl.set_hexpand(True)
        lbl.set_justify(Gtk.Justification.CENTER)
        lbl.set_wrap(True)
        self.append(lbl)

    def _init_webview(self):
        # Disable bwrap sandbox before WebView creation (Ubuntu 24.04 AppArmor).
        network_session = None
        try:
            # WebKit 6.0 API
            if hasattr(WebKit2, "NetworkSession"):
                # Own session (sandbox toggle below). Map tiles are served by
                # tile_proxy from disk, so this cache only holds Leaflet's JS/CSS.
                try:
                    os.makedirs(TILE_CACHE_DIR, exist_ok=True)
                    network_session = WebKit2.NetworkSession.new(
                        os.path.join(TILE_CACHE_DIR, "data"), TILE_CACHE_DIR)
                except Exception:
                    network_session = None
                if network_session is None:
                    try:
                        network_session = WebKit2.NetworkSession.get_default()
                    except Exception:
                        try:
                            network_session = WebKit2.NetworkSession.new_ephemeral()
                        except Exception:
                            network_session = None
                if network_session and hasattr(network_session, "set_sandbox_enabled"):
                    network_session.set_sandbox_enabled(False)
        except Exception as e:
            log_warn(_("NetworkSession sandbox-disable failed: {err}").format(err=e))

        try:
            # WebKit2 4.x API
            if hasattr(WebKit2, "WebContext"):
                wc = WebKit2.WebContext.get_default()
                if wc and hasattr(wc, "set_sandbox_enabled"):
                    wc.set_sandbox_enabled(False)
        except Exception as e:
            log_warn(_("WebContext sandbox-disable failed: {err}").format(err=e))

        self.web = None
        if network_session is not None and hasattr(WebKit2, "WebView"):
            try:
                self.web = WebKit2.WebView.new_with_network_session(network_session)
            except Exception:
                self.web = None
        if self.web is None:
            self.web = WebKit2.WebView()

        self.web.set_vexpand(True)
        self.web.set_hexpand(True)

        try:
            wk_settings = self.web.get_settings()
            wk_settings.set_enable_javascript(True)
            wk_settings.set_javascript_can_access_clipboard(False)
            wk_settings.set_enable_developer_extras(False)
            # OSM's tile policy requires apps to identify themselves; the
            # default WebKit UA risks being rate-limited (slow tiles).
            if hasattr(wk_settings, "set_user_agent"):
                wk_settings.set_user_agent(
                    "Pixora/1.0 (+https://github.com/Linux-Ginger/Pixora)")
        except Exception as e:
            log_warn(_("WebView settings not fully applied: {err}").format(err=e))

        try:
            ucm = self.web.get_user_content_manager()
            registered = False
            for args in (("pixora",), ("pixora", None), ("pixora", "")):
                try:
                    ucm.register_script_message_handler(*args)
                    registered = True
                    break
                except Exception:
                    continue
            if not registered:
                log_error(_("register_script_message_handler failed with all variants"))
            ucm.connect("script-message-received::pixora", self._on_js_message)
        except Exception as e:
            log_error(_("WebView bridge setup error: {err}").format(err=e))

        self.web.connect("load-changed", self._on_load_changed)
        # Kill the WebKit right-click menu (Reload/Inspect have no place here).
        self.web.connect("context-menu", lambda *a: True)
        # Open attribution links in the system browser, not inside the map.
        try:
            self.web.connect("decide-policy", self._on_decide_policy)
        except Exception:
            pass

        assets_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "assets", "leaflet"
        )
        map_html_path = os.path.abspath(os.path.join(assets_dir, "map.html"))
        if not os.path.exists(map_html_path):
            self._show_fallback(_("Leaflet assets not found:\n{p}").format(p=map_html_path))
            return

        self.web.load_uri("file://" + map_html_path)
        self.append(self.web)

    def _on_load_changed(self, web, event):
        try:
            finished = (event == WebKit2.LoadEvent.FINISHED)
        except Exception:
            finished = False
        if finished:
            GLib.timeout_add(80, self._push_markers)

    def _push_markers(self):
        # Group photos shot at essentially the same spot into one site marker
        # (count + all paths), so GPS jitter doesn't scatter them into lone pins
        # even at max zoom. Grouped by REAL distance (not a grid) so points either
        # side of a cell edge can't split; a coarse bucket index keeps it fast.
        THRESH_M = 50.0   # GPS (esp. indoors) drifts well past 20 m for one spot
        BUCKET = 0.0020  # ~220 m cell — wider than the threshold, so neighbours cover all candidates
        clusters = []     # list of member-lists
        anchors = []      # (lat, lon) per cluster, index-aligned with clusters
        buckets = {}      # (bi, bj) -> [cluster index, …]
        for m in self._pending_markers:
            lat, lon = m[0], m[1]
            bi, bj = int(lat / BUCKET), int(lon / BUCKET)
            found = None
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for gi in buckets.get((bi + di, bj + dj), ()):
                        alat, alon = anchors[gi]
                        dy = (lat - alat) * 111000.0
                        dx = (lon - alon) * 111000.0 * math.cos(math.radians(alat))
                        if dx * dx + dy * dy <= THRESH_M * THRESH_M:
                            found = gi
                            break
                    if found is not None:
                        break
                if found is not None:
                    break
            if found is not None:
                clusters[found].append(m)
            else:
                clusters.append([m])
                anchors.append((lat, lon))
                buckets.setdefault((bi, bj), []).append(len(clusters) - 1)
        data = []
        for grp in clusters:
            first = grp[0]
            path = first[4]
            thumb_w = None
            try:
                thumb = get_cache_path(path, common.THUMB_SIZE)
                if not os.path.exists(thumb):
                    thumb = None
                else:
                    # Display width at the popup's fixed 140px height — sent so the
                    # popup reserves the right space BEFORE the thumb loads (else the
                    # black background is mis-sized on first hover). Header-only read.
                    info = GdkPixbuf.Pixbuf.get_file_info(thumb)
                    tw, th = (info[1], info[2]) if info else (0, 0)
                    if th and th > 0:
                        thumb_w = max(1, min(240, round(tw * 140 / th)))
            except Exception:
                thumb = None
            data.append({
                "lat": first[0], "lon": first[1],
                "filename": first[2], "date": first[3],
                "path": path, "thumb": thumb, "w": thumb_w,
                "count": len(grp),
                "paths": [g[4] for g in grp],
            })
        labels = {
            "otherInCluster": _("other photos in this cluster"),
            "clickCluster": _("Click cluster to view filtered"),
            "clickOpen": _("Click to open"),
            "offline": _("⚠ No internet connection — map tiles cannot be loaded"),
            "retry": _("Try again"),
            "loadingTiles": _("Loading map…"),
            "home": _("My home"),
            "atHome": _("Home"),
        }
        # Separate calls so a failure in one (e.g. labels) can't stop the
        # markers from being drawn.
        if self._tile_url:
            self._run_js(
                f"if(window.pixoraSetTileUrl){{window.pixoraSetTileUrl("
                f"{json.dumps(self._tile_url)});}}", "tile")
        self._run_js(
            f"if(window.pixoraSetLabels){{window.pixoraSetLabels("
            f"{json.dumps(labels)});}}", "labels")
        # Home BEFORE markers so the popups can show a "Home" badge for nearby
        # photos (pixoraSetMarkers builds the popups).
        if self._home:
            self._run_js(
                f"if(window.pixoraSetHome){{window.pixoraSetHome("
                f"{float(self._home[0])},{float(self._home[1])});}}", "home")
        self._run_js(
            f"if(window.pixoraSetPlaces){{window.pixoraSetPlaces("
            f"{json.dumps(self._places)});}}", "places")
        self._run_js(
            f"if(window.pixoraSetMarkers){{window.pixoraSetMarkers("
            f"{json.dumps(data)},{json.dumps(self._initial_view)});}}", "markers")
        return False

    def set_places(self, places):
        self._places = places or []
        self._run_js(
            f"if(window.pixoraSetPlaces){{window.pixoraSetPlaces("
            f"{json.dumps(self._places)});}}")

    def _on_decide_policy(self, web, decision, decision_type):
        try:
            t = WebKit2.PolicyDecisionType
            if decision_type not in (t.NAVIGATION_ACTION, t.NEW_WINDOW_ACTION):
                return False
            uri = decision.get_navigation_action().get_request().get_uri() or ""
        except Exception:
            return False
        # Only intercept real web links (attribution); file:// + the localhost
        # tile proxy must keep loading inside the view.
        if uri.startswith("http://127.0.0.1") or uri.startswith("file://"):
            return False
        if uri.startswith("http://") or uri.startswith("https://"):
            try:
                subprocess.Popen(["xdg-open", uri])
            except Exception:
                pass
            try:
                decision.ignore()
            except Exception:
                pass
            return True
        return False

    def _run_js(self, js, label=""):
        """Run JS in the map, across both WebKit API generations. Logs JS-side
        exceptions via the async callback (so silent failures become visible)."""
        if not getattr(self, "web", None):
            return

        def _cb(web, result, *_a):
            try:
                web.evaluate_javascript_finish(result)
            except Exception as e:
                log_warn("JS error [%s]: %r" % (label, e))

        try:
            self.web.evaluate_javascript(js, -1, None, None, None, _cb, None)
            return
        except Exception:
            pass
        try:
            self.web.run_javascript(js, None, None, None)
        except Exception as e:
            log_error(_("JS run error: {err}").format(err=e))

    def go_home(self, lat, lon, zoom=None):
        z = int(zoom) if zoom else 15
        self._run_js(f"if(window.pixoraGoHome)"
                     f"{{window.pixoraGoHome({float(lat)},{float(lon)},{z});}}")

    def refresh(self):
        self._run_js("if(window.pixoraRefresh){window.pixoraRefresh();}")

    def set_home(self, lat, lon):
        if lat is None or lon is None:
            self._home = None
            self._run_js("if(window.pixoraSetHome){window.pixoraSetHome(null,null);}")
            return
        self._home = (lat, lon)
        self._run_js(f"if(window.pixoraSetHome)"
                     f"{{window.pixoraSetHome({float(lat)},{float(lon)});}}")

    def _on_js_message(self, ucm, message):
        try:
            if hasattr(message, "to_string"):
                raw = message.to_string()
            else:
                raw = message.get_js_value().to_string()
            payload = json.loads(raw)
        except Exception:
            return
        msg_type = payload.get("type")
        if msg_type == "open_photos":
            paths = payload.get("paths") or []
            log_info(_("Map → open_photos: {n} photos").format(n=len(paths)))
            if paths:
                GLib.idle_add(self.open_photo_cb, paths)
        elif msg_type == "open_photo":
            path = payload.get("path")
            log_info(_("Map → open_photo: {p}").format(p=path))
            if path:
                GLib.idle_add(self.open_photo_cb, [path])
        elif msg_type == "map-ready":
            log_info(_("Map → first tiles loaded"))
            if self.status_cb:
                GLib.idle_add(self.status_cb, "ready")
        elif msg_type == "map-offline":
            log_warn(_("Map → offline / tile errors"))
            if self.status_cb:
                GLib.idle_add(self.status_cb, "offline")
        elif msg_type == "js-error":
            log_error("Map JS error: %s" % payload.get("msg", ""))
