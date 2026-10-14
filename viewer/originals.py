#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — originals.py
#  Untouched copies of edited photos, for "Restore original"
#  by LinuxGinger
# ─────────────────────────────────────────────

import hashlib
import json
import os
import shutil
import threading

from common import INSTALL_DIR

_lock = threading.Lock()

# User data, so it lives next to — not inside — the app's git checkout
# (~/.local/share/pixora), which installs and updates may replace.
_DIR = os.path.expanduser("~/.local/share/pixora-originals")
_OLD_DIR = os.path.join(INSTALL_DIR, "originals")


def originals_dir():
    d = _DIR
    if not os.path.isdir(d) and os.path.isdir(_OLD_DIR):
        try:
            os.rename(_OLD_DIR, d)  # one-time move from older versions
        except OSError:
            return _OLD_DIR
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o700)  # keep originals private to the user
    except OSError:
        pass
    return d


def _index_path():
    return os.path.join(originals_dir(), "index.json")


def load_index():
    try:
        with open(_index_path()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_index(idx):
    # Atomic: a crash mid-write must never lose the map to every backup.
    path = _index_path()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(idx, f)
    os.replace(tmp, path)


def backup_path_for(path):
    """Absolute path of the stored original for `path`, or None."""
    entry = load_index().get(path)
    if not entry:
        return None
    p = os.path.join(originals_dir(), entry["backup"])
    return p if os.path.exists(p) else None


def has_backup(path):
    return backup_path_for(path) is not None


def backup_once(path):
    """Stash the untouched original the first time a photo is edited.

    Raises on failure: the caller must not overwrite a photo whose original
    could not be kept."""
    with _lock:
        if backup_path_for(path):
            return
        idx = load_index()
        backup_name = hashlib.md5(path.encode()).hexdigest() + \
            os.path.splitext(path)[1]
        dst = os.path.join(originals_dir(), backup_name)
        tmp = dst + ".part"
        shutil.copy2(path, tmp)
        os.replace(tmp, dst)
        try:
            os.chmod(dst, 0o600)
        except OSError:
            pass
        idx[path] = {"backup": backup_name, "orig": os.path.basename(path)}
        _save_index(idx)


def entry_for(path):
    return load_index().get(path)


def rekey(old_path, new_path):
    """Follow a photo that was moved or renamed (tidy-up, HEIC → JPG)."""
    with _lock:
        idx = load_index()
        if old_path in idx:
            idx[new_path] = idx.pop(old_path)
            _save_index(idx)


def drop(path):
    """Forget (and delete) the stored original of `path`."""
    with _lock:
        idx = load_index()
        entry = idx.pop(path, None)
        if not entry:
            return
        try:
            os.remove(os.path.join(originals_dir(), entry["backup"]))
        except OSError:
            pass
        _save_index(idx)
