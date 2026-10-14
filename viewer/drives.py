#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — drives.py
#  External drive discovery (lsblk) for backups
#  by LinuxGinger
# ─────────────────────────────────────────────

import json
import subprocess
import threading
import time
from pixora_i18n import _
from common import log_error


BACKUP_FSTYPES   = {"ext4","ext3","ext2","ntfs","exfat","fuseblk","btrfs","xfs","vfat"}

# Module-level lsblk cache: avoids back-to-back 5s subprocess calls on Settings open.
_LSBLK_CACHE = {"ts": 0.0, "data": None}
# 10s TTL covers Settings burst; 8s drive-poll forces a refresh anyway.
_LSBLK_CACHE_TTL = 10.0

def _lsblk_fetch():
    """Run lsblk. Returns parsed JSON or None on failure/timeout."""
    try:
        result = subprocess.run(
            ["lsblk", "-o", "NAME,UUID,LABEL,SIZE,FSTYPE,MOUNTPOINT,HOTPLUG,RM,TRAN", "-J"],
            capture_output=True, text=True, timeout=5,
        )
        return json.loads(result.stdout)
    except Exception as e:
        log_error(_("Drive detection error: {err}").format(err=e))
        return None

def _lsblk_data(force=False):
    """Cached lsblk JSON. Refetches when stale (>TTL) or force=True."""
    now = time.time()
    if (not force
            and _LSBLK_CACHE["data"] is not None
            and now - _LSBLK_CACHE["ts"] < _LSBLK_CACHE_TTL):
        return _LSBLK_CACHE["data"]
    data = _lsblk_fetch()
    if data is not None:
        _LSBLK_CACHE["ts"] = now
        _LSBLK_CACHE["data"] = data
    return data

def _warm_lsblk_cache_async():
    """Fire-and-forget pre-warm so the first Settings open is instant."""
    threading.Thread(target=_lsblk_data, daemon=True).start()

def get_available_drives():
    """Return external drives suitable as backup target (never system partitions)."""
    drives = []
    SYS_MOUNTS = {"/", "/boot", "/boot/efi", "/home", "/var", "/usr", "/etc"}
    EXTERNAL_PREFIXES = ("/media/", "/run/media/", "/mnt/")
    seen_uuids = set()
    data = _lsblk_data()
    if data is None:
        return drives

    def _is_external(mountpoint, hotplug, rm, tran):
        if hotplug or rm:
            return True
        if tran in ("usb", "ieee1394"):
            return True
        if mountpoint and any(mountpoint.startswith(p) for p in EXTERNAL_PREFIXES):
            return True
        return False

    def process_device(device, parent_hotplug=False, parent_rm=False, parent_tran=""):
        hotplug = bool(device.get("hotplug", False)) or parent_hotplug
        rm = bool(device.get("rm", False)) or parent_rm
        tran = (device.get("tran") or parent_tran or "").lower()
        uuid       = device.get("uuid")
        fstype     = (device.get("fstype") or "").lower()
        label      = (device.get("label") or "").strip()
        size       = device.get("size") or ""
        mountpoint = (device.get("mountpoint") or "").strip()
        if mountpoint in SYS_MOUNTS:
            pass  # skip but still recurse into children
        elif uuid and fstype in BACKUP_FSTYPES:
            if _is_external(mountpoint, hotplug, rm, tran) and uuid not in seen_uuids:
                seen_uuids.add(uuid)
                display = (f"💾  {label}  ({size})" if label else
                           f"💾  {mountpoint}  ({size})" if mountpoint else
                           f"💾  {_('External drive')}  ({size})")
                drives.append((uuid, display))
        for child in device.get("children", []):
            process_device(child, hotplug, rm, tran)

    for device in data.get("blockdevices", []):
        process_device(device)
    return drives

def _cmd_available_bk(cmd):
    import shutil as _sh
    return _sh.which(cmd) is not None

def get_mountpoint_for_uuid(uuid):
    data = _lsblk_data()
    if data is None or not uuid:
        return None

    # Walk the whole tree: on encrypted (LUKS) or LVM drives the filesystem
    # sits two or more levels below the disk, just like get_available_drives
    # finds it.
    def _walk(devices):
        for dev in devices or ():
            if dev.get("uuid") == uuid and dev.get("mountpoint"):
                return dev.get("mountpoint")
            found = _walk(dev.get("children"))
            if found:
                return found
        return None
    return _walk(data.get("blockdevices", []))
