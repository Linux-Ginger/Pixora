#!/usr/bin/env python3
# Pixora — tile_proxy.py — local OSM tile cache served over loopback.
#
# Leaflet points at http://127.0.0.1:<port>/{z}/{x}/{y}.png instead of OSM.
# Each tile is kept on disk and re-served instantly; tiles older than the TTL
# are refreshed from OSM, so the map stays current without re-downloading
# everything. When OSM is unreachable we fall back to the cached tile (even if
# stale) and, for tiles we never saw, an upscaled crop of the nearest parent
# tile — so an offline map looks blurry, never like a broken gap.

import os
import re
import math
import time
import threading
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

TILE_TTL_SECONDS = 30 * 86400          # refresh tiles older than 30 days
OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
USER_AGENT = "Pixora/1.0 (+https://github.com/Linux-Ginger/Pixora)"
_FETCH_TIMEOUT = 6                     # seconds per OSM request
_MAX_CONCURRENT_FETCH = 4             # be gentle: OSM forbids bulk hammering
_OFFLINE_COOLDOWN = 15               # after repeated failures, skip the network

_TILE_RE = re.compile(r"^/(\d{1,2})/(\d{1,7})/(\d{1,7})\.png$")


def _log(msg):
    # Lazy import so this module stays standalone-testable.
    try:
        from main_window import log_info
        log_info(msg)
    except Exception:
        pass


def deg2tile(lat, lon, zoom):
    """(lat, lon) → (x, y) tile index at the given zoom."""
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    x = min(max(x, 0), n - 1)
    y = min(max(y, 0), n - 1)
    return x, y


class TileCache:
    def __init__(self, cache_dir):
        self.dir = os.path.join(cache_dir, "tiles")
        os.makedirs(self.dir, exist_ok=True)
        self._fetch_sem = threading.Semaphore(_MAX_CONCURRENT_FETCH)
        self._locks = {}                 # (z,x,y) → Lock, dedupe concurrent fetch
        self._locks_guard = threading.Lock()
        # Offline heuristic: after consecutive failures, stop trying for a while
        # so offline panning serves from cache instantly instead of timing out.
        self._fail_count = 0
        self._offline_until = 0.0
        self._state_lock = threading.Lock()

    # ── disk helpers ────────────────────────────────────────────────
    def _path(self, z, x, y):
        return os.path.join(self.dir, str(z), str(x), f"{y}.png")

    def _read(self, z, x, y):
        try:
            with open(self._path(z, x, y), "rb") as f:
                return f.read()
        except OSError:
            return None

    def _fresh(self, z, x, y):
        try:
            return (time.time() - os.path.getmtime(self._path(z, x, y))) < TILE_TTL_SECONDS
        except OSError:
            return False

    def _write(self, z, x, y, data):
        p = self._path(z, x, y)
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, p)
        except OSError:
            pass

    # ── network ─────────────────────────────────────────────────────
    def _tile_lock(self, key):
        with self._locks_guard:
            lk = self._locks.get(key)
            if lk is None:
                lk = threading.Lock()
                self._locks[key] = lk
            return lk

    def _assume_offline(self):
        with self._state_lock:
            return time.time() < self._offline_until

    def _note_result(self, ok):
        with self._state_lock:
            if ok:
                self._fail_count = 0
                self._offline_until = 0.0
            else:
                self._fail_count += 1
                if self._fail_count >= 4:
                    self._offline_until = time.time() + _OFFLINE_COOLDOWN

    def _fetch(self, z, x, y):
        """Download one tile from OSM; return bytes or None. Caches on success."""
        if self._assume_offline():
            return None
        with self._fetch_sem:
            req = urllib.request.Request(
                OSM_URL.format(z=z, x=x, y=y),
                headers={"User-Agent": USER_AGENT},
            )
            try:
                with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
                    data = resp.read()
                self._note_result(True)
                if data:
                    self._write(z, x, y, data)
                return data
            except (urllib.error.URLError, OSError, Exception):
                self._note_result(False)
                return None

    # ── parent fallback ─────────────────────────────────────────────
    def _parent_fallback(self, z, x, y):
        """Upscaled crop of the nearest cached ancestor tile, or None."""
        if not _HAS_PIL:
            return None
        for dz in range(1, min(z, 6) + 1):
            pz, px, py = z - dz, x >> dz, y >> dz
            data = self._read(pz, px, py)
            if data is None:
                continue
            try:
                scale = 1 << dz
                size = 256 // scale
                fx = (x - (px << dz)) * size
                fy = (y - (py << dz)) * size
                with Image.open(BytesIO(data)) as im:
                    im = im.convert("RGB")
                    crop = im.crop((fx, fy, fx + size, fy + size))
                    crop = crop.resize((256, 256), Image.BILINEAR)
                    out = BytesIO()
                    crop.save(out, "PNG")
                    return out.getvalue()
            except Exception:
                continue
        return None

    # ── main entry ──────────────────────────────────────────────────
    def get(self, z, x, y):
        """Return tile bytes for serving, or None if nothing can be shown."""
        if self._fresh(z, x, y):
            cached = self._read(z, x, y)
            if cached:
                return cached
        # Stale or missing → try the network (single-flight per tile).
        with self._tile_lock((z, x, y)):
            if self._fresh(z, x, y):       # another thread just refreshed it
                cached = self._read(z, x, y)
                if cached:
                    return cached
            fetched = self._fetch(z, x, y)
            if fetched:
                return fetched
        # Offline / fetch failed → stale copy, else parent fallback.
        stale = self._read(z, x, y)
        if stale:
            return stale
        return self._parent_fallback(z, x, y)

    def prefetch(self, tiles, cap=400):
        """Background-warm a list of (z,x,y) tiles, skipping fresh ones."""
        todo = []
        seen = set()
        for t in tiles:
            if t in seen:
                continue
            seen.add(t)
            z, x, y = t
            if not self._fresh(z, x, y):
                todo.append(t)
            if len(todo) >= cap:
                break
        if not todo:
            return

        def worker(batch):
            for (z, x, y) in batch:
                if self._assume_offline():
                    return
                with self._tile_lock((z, x, y)):
                    if not self._fresh(z, x, y):
                        self._fetch(z, x, y)
        # Two gentle workers; OSM dislikes bulk parallelism.
        n = 2
        chunks = [todo[i::n] for i in range(n)]
        for c in chunks:
            threading.Thread(target=worker, args=(c,), daemon=True).start()
        _log("tile_proxy: prefetching %d tiles around photo locations" % len(todo))


class _Handler(BaseHTTPRequestHandler):
    cache = None  # set by start_proxy

    def do_GET(self):
        m = _TILE_RE.match(self.path)
        if not m:
            self.send_error(404)
            return
        z, x, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        data = self.cache.get(z, x, y) if self.cache else None
        if not data:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        # Loopback only; canvas rendering needs an explicit CORS allow.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass  # silence default stderr access log


_proxy_lock = threading.Lock()
_proxy = None  # (base_url, TileCache)


def start_proxy(cache_dir):
    """Start the loopback tile server once; return (base_url, TileCache)."""
    global _proxy
    with _proxy_lock:
        if _proxy is not None:
            return _proxy
        cache = TileCache(cache_dir)
        _Handler.cache = cache
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}"
        _proxy = (base, cache)
        _log("tile_proxy: serving cached OSM tiles on %s" % base)
        return _proxy
