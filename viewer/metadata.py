#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — metadata.py
#  Cached photo metadata: GPS, capture date, geocoding
#  by LinuxGinger
# ─────────────────────────────────────────────

import datetime
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from common import _lang


# Metadata cache: avoids repeat EXIF/ffprobe on reload/map-open.
_METADATA_CACHE_PATH = os.path.expanduser("~/.cache/pixora/metadata.json")
_metadata_cache = {
    "video_duration": {},
    "photo_date": {},
    "gps_coords": {},
    "geocode": {},
}
_metadata_dirty = False
_metadata_save_lock = threading.Lock()

# Bump when the GPS reader changes so cached (possibly negative) results from an
# older reader are discarded — e.g. HEIC photos that wrongly cached "no GPS".
_GPS_READER_VERSION = "v3"


_METADATA_MAX_GEOCODE = 3000


def _load_metadata_cache():
    global _metadata_dirty
    try:
        with open(_METADATA_CACHE_PATH, "r") as f:
            data = json.load(f)
        for k in _metadata_cache.keys():
            v = data.get(k)
            if isinstance(v, dict):
                _metadata_cache[k] = v
        # GPS reader changed → drop the old gps_coords cache once so HEIC
        # photos that cached "no location" get re-read with the new reader.
        if data.get("_gps_reader") != _GPS_READER_VERSION:
            _metadata_cache["gps_coords"] = {}
            _metadata_dirty = True
        _metadata_cache["_gps_reader"] = _GPS_READER_VERSION
        # Drop legacy geocode entries (empty values or pre-lang key format).
        geo = _metadata_cache.get("geocode", {})
        pruned = {k: v for k, v in geo.items() if v and ":" in k}
        if len(pruned) != len(geo):
            _metadata_cache["geocode"] = pruned
            _metadata_dirty = True
        # Prune stale entries so the cache doesn't grow forever after deletes.
        for bucket in ("photo_date", "video_duration", "gps_coords"):
            entries = _metadata_cache.get(bucket, {})
            stale = [p for p in entries if not os.path.exists(p)]
            for p in stale:
                entries.pop(p, None)
            if stale:
                _metadata_dirty = True
        # Geocode bucket: pseudo-LRU trim via dict insertion order (Py3.7+).
        geo = _metadata_cache.get("geocode", {})
        if len(geo) > _METADATA_MAX_GEOCODE:
            keep = dict(list(geo.items())[-_METADATA_MAX_GEOCODE:])
            _metadata_cache["geocode"] = keep
            _metadata_dirty = True
    except Exception:
        pass


def save_metadata_cache():
    global _metadata_dirty
    if not _metadata_dirty:
        return
    with _metadata_save_lock:
        try:
            os.makedirs(os.path.dirname(_METADATA_CACHE_PATH), exist_ok=True)
            tmp = _METADATA_CACHE_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(_metadata_cache, f)
            os.replace(tmp, _METADATA_CACHE_PATH)
            _metadata_dirty = False
        except Exception:
            pass


def _cache_fresh(bucket, path):
    entry = _metadata_cache[bucket].get(path)
    if not entry:
        return None
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        return None
    # Exact mtime match — 0.5s tolerance once gave stale data post-edit.
    if entry.get("m") == mtime:
        return entry.get("v")
    return None


def _cache_put(bucket, path, value):
    global _metadata_dirty
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        return
    _metadata_cache[bucket][path] = {"m": mtime, "v": value}
    _metadata_dirty = True


_load_metadata_cache()


def get_gps_coords(photo_path):
    cached = _cache_fresh("gps_coords", photo_path)
    if cached is not None:
        return tuple(cached) if cached else None
    result = _get_gps_coords_raw(photo_path)
    _cache_put("gps_coords", photo_path, list(result) if result else None)
    return result


def _get_gps_coords_raw(photo_path):
    try:
        from PIL import Image
        from PIL.ExifTags import GPSTAGS
        img = Image.open(photo_path)
        if img.mode == "P" and "transparency" in img.info:
            img = img.convert("RGBA")
        # getexif().get_ifd(GPSInfo) works across JPEG *and* HEIC; the old
        # _getexif() silently returns no GPS for HEIF, which is why locations
        # only showed up after a HEIC→JPEG conversion.
        gps_info = {}
        try:
            gps_ifd = img.getexif().get_ifd(0x8825)  # 0x8825 = GPSInfo IFD
            for gps_tag, gps_value in (gps_ifd or {}).items():
                gps_info[GPSTAGS.get(gps_tag, gps_tag)] = gps_value
        except Exception:
            gps_info = {}
        if not gps_info:
            # Fallback for odd files the IFD path misses.
            from PIL.ExifTags import TAGS
            exif = img._getexif()
            for tag, value in (exif or {}).items():
                if TAGS.get(tag, tag) == "GPSInfo":
                    for gt, gv in value.items():
                        gps_info[GPSTAGS.get(gt, gt)] = gv
        if not gps_info:
            return None
        def to_decimal(coords, ref):
            d, m, s = coords
            decimal = float(d) + float(m) / 60 + float(s) / 3600
            if ref in ["S", "W"]:
                decimal = -decimal
            return decimal
        lat = to_decimal(gps_info["GPSLatitude"],  gps_info.get("GPSLatitudeRef",  "N"))
        lon = to_decimal(gps_info["GPSLongitude"], gps_info.get("GPSLongitudeRef", "E"))
        # "Null island" (0,0) is what cameras write when there's no real fix —
        # treat it as no location so such photos don't get tagged with a place.
        if abs(lat) < 0.0001 and abs(lon) < 0.0001:
            return None
        return (lat, lon)
    except Exception:
        return None

_geocode_failed_at = {}


def _geocode_key(lat, lon):
    # Key includes language — same coords get different labels per lang.
    return f"g3:{_lang}:{lat:.4f},{lon:.4f}"


def cached_geocode(lat, lon):
    """Already-cached geocode dict ({text,street,city}) or {} — never hits network."""
    return _metadata_cache["geocode"].get(_geocode_key(lat, lon)) or {}


def reverse_geocode(lat, lon):
    """Geocode dict {text, street, city}; looked up once then cached forever."""
    global _metadata_dirty
    key = _geocode_key(lat, lon)
    cached = _metadata_cache["geocode"].get(key)
    if cached:
        return cached
    # Rate-limit failed lookups (30 min) — avoids 5s timeouts when offline.
    last_fail = _geocode_failed_at.get(key, 0)
    if time.time() - last_fail < 1800:
        return {}
    result = _reverse_geocode_raw(lat, lon)
    if result and result.get("text"):
        _metadata_cache["geocode"][key] = result
        _metadata_dirty = True
        _geocode_failed_at.pop(key, None)
    else:
        _geocode_failed_at[key] = time.time()
    return result or {}


def _reverse_geocode_raw(lat, lon):
    try:
        # zoom=18 + addressdetails gives street/neighbourhood level, not just city.
        url = (f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}"
               f"&format=json&addressdetails=1&zoom=18")
        # Accept-Language picks localized labels.
        req = urllib.request.Request(url, headers={
            "User-Agent": "Pixora/1.0 (+https://github.com/Linux-Ginger/Pixora)",
            "Accept-Language": _lang,
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        addr = data.get("address", {})
        # Street name only — no house number (we don't want to broadcast it).
        street = addr.get("road") or addr.get("pedestrian") or addr.get("footway") or ""
        suburb = (addr.get("suburb") or addr.get("neighbourhood")
                  or addr.get("quarter") or addr.get("city_district") or "")
        city = (addr.get("city") or addr.get("town") or addr.get("village")
                or addr.get("municipality") or "")
        country = addr.get("country", "")
        # Street, neighbourhood, city, country — skip blanks and repeats.
        out = []
        for part in (street, suburb, city, country):
            if part and (not out or out[-1] != part):
                out.append(part)
        return {"text": ", ".join(out), "street": street, "city": city}
    except Exception:
        return {}

# English country names for the home dialog; Nominatim matches these reliably.
COUNTRIES = [
    "Australia", "Austria", "Belgium", "Brazil", "Bulgaria", "Canada",
    "Croatia", "Czechia", "Denmark", "Estonia", "Finland", "France",
    "Germany", "Greece", "Hungary", "Iceland", "Ireland", "Italy", "Japan",
    "Latvia", "Lithuania", "Luxembourg", "Malta", "Mexico", "Morocco",
    "Netherlands", "New Zealand", "Norway", "Poland", "Portugal", "Romania",
    "Slovakia", "Slovenia", "South Africa", "Spain", "Sweden", "Switzerland",
    "Turkey", "Ukraine", "United Kingdom", "United States",
]
_LOCALE_COUNTRY = {"nl": "Netherlands", "de": "Germany",
                   "fr": "France", "en": "United Kingdom"}


def _mk(s):
    """Escape text for Adw/Pango markup titles (an '&' otherwise breaks them)."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _titlecase(s):
    """Capitalise the first letter of each word, leaving the rest as typed
    ('rotterdam' → 'Rotterdam', 'den haag' → 'Den Haag')."""
    return " ".join(w[:1].upper() + w[1:] if w else w for w in (s or "").split(" "))


def geocode_address(street=None, city=None, postcode=None, country=None,
                    loose=False):
    """Structured address → (lat, lon, display_name) via Nominatim, or None.
    loose=True drops the city, so a wrong/missing city can still resolve to a
    'did you mean…' suggestion. One-off online lookup; only the result is kept."""
    try:
        import urllib.parse
        params = {"format": "json", "limit": "1", "addressdetails": "0"}
        if street:
            params["street"] = street
        if city and not loose:
            params["city"] = city
        if postcode:
            params["postalcode"] = postcode
        if country:
            params["country"] = country
        if len(params) == 3:        # nothing useful supplied
            return None
        url = ("https://nominatim.openstreetmap.org/search?"
               + urllib.parse.urlencode(params))
        req = urllib.request.Request(url, headers={
            "User-Agent": "Pixora/1.0 (+https://github.com/Linux-Ginger/Pixora)",
            "Accept-Language": _lang,
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        if data:
            return (float(data[0]["lat"]), float(data[0]["lon"]),
                    data[0].get("display_name", ""))
    except Exception:
        pass
    return None


_EXIF_DATE_TAGS = (36867, 36868, 306)  # DateTimeOriginal, DateTimeDigitized, DateTime

def get_photo_date(path: str) -> float:
    cached = _cache_fresh("photo_date", path)
    if cached is not None:
        return float(cached)
    value = _get_photo_date_raw(path)
    _cache_put("photo_date", path, value)
    return value


def _get_photo_date_raw(path: str) -> float:
    """Timestamp from EXIF, falling back to mtime."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg", ".heic", ".png", ".dng"):
        try:
            from PIL import Image
            with Image.open(path) as img:
                exif = img._getexif()
            if exif:
                for tag in _EXIF_DATE_TAGS:
                    val = exif.get(tag)
                    if val:
                        dt = datetime.datetime.strptime(val[:19], "%Y:%m:%d %H:%M:%S")
                        return dt.timestamp()
        except Exception:
            pass
    return os.path.getmtime(path)


def get_video_duration(path):
    cached = _cache_fresh("video_duration", path)
    if cached is not None:
        return float(cached)
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True, timeout=5
        )
        data = json.loads(result.stdout)
        dur = float(data.get("format", {}).get("duration", 0))
    except Exception:
        dur = 0.0
    _cache_put("video_duration", path, dur)
    return dur


def get_video_gps_coords(path):
    import re
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True, timeout=5
        )
        data = json.loads(result.stdout)
        tags = data.get("format", {}).get("tags", {})
        loc = (tags.get("location") or
               tags.get("com.apple.quicktime.location.ISO6709") or "")
        if loc:
            m = re.match(r'([+-]\d+\.\d+)([+-]\d+\.\d+)', loc)
            if m:
                lat, lon = float(m.group(1)), float(m.group(2))
                if abs(lat) < 0.0001 and abs(lon) < 0.0001:
                    return None  # null island = no real location
                return (lat, lon)
    except Exception:
        pass
    return None
