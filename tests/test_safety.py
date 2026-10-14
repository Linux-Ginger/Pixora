#!/usr/bin/env python3
"""Safety tests for the code paths that can lose photos.

Run from the repo root:  python3 -m unittest discover tests

Everything runs against a throw-away HOME, so your real library, settings and
backup drive are never touched. Needs the same packages as Pixora itself
(python3-gi with GTK 4 / libadwaita, Pillow).
"""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

# Pixora computes its config/cache paths at import time: isolate HOME first.
_TMP_HOME = tempfile.mkdtemp(prefix="pixora-test-home-")
os.environ["HOME"] = _TMP_HOME
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP_HOME, ".config")
os.environ["XDG_CACHE_HOME"] = os.path.join(_TMP_HOME, ".cache")
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP_HOME, ".local", "share")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "viewer"))

from gi.repository import GLib  # noqa: E402

import common  # noqa: E402
import importer_page  # noqa: E402
import win_backup  # noqa: E402


def _drain_main_loop():
    ctx = GLib.MainContext.default()
    while ctx.pending():
        ctx.iteration(False)


class FakeBackupWindow(win_backup.BackupMixin):
    """Just enough of MainWindow to run the backup scan logic."""

    def __init__(self, settings, drive_root):
        self.settings = settings
        self._drive_root = drive_root
        self.results = []

    def _backup_drive_mountpoint(self):
        return self._drive_root

    def _handle_scan_result(self, result):
        self.results.append(result)
        return False


class BackupSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=_TMP_HOME))
        self.drive = self.tmp / "drive"
        self.drive.mkdir()
        self.backup = self.drive / "Pixora"
        self.backup.mkdir()
        for i in range(30):
            (self.backup / f"IMG_{i:04d}.JPG").write_bytes(b"x" * 10)
        (self.drive / "OtherStuff.doc").write_bytes(b"keep me")

    def _scan(self, photo_path, mode="sync", backup_path=None):
        win = FakeBackupWindow({
            "photo_path": str(photo_path),
            "backup_mode": mode,
            "backup_path": str(backup_path or self.backup),
        }, self.drive)
        win._backup_scan_thread()
        _drain_main_loop()
        self.assertEqual(len(win.results), 1)
        return win.results[0]

    def test_missing_library_never_deletes(self):
        result = self._scan(self.tmp / "unplugged-disk" / "Photos")
        self.assertIn("error", result)

    def test_empty_library_never_deletes(self):
        empty = self.tmp / "Photos"
        empty.mkdir()
        result = self._scan(empty)
        self.assertIn("error", result)

    def test_mass_delete_is_flagged(self):
        lib = self.tmp / "Photos"
        lib.mkdir()
        (lib / "IMG_0000.JPG").write_bytes(b"x" * 10)
        result = self._scan(lib)
        self.assertEqual(result["delete"], 29)
        self.assertTrue(result["mass_delete"])

    def test_edited_photo_counts_as_changed(self):
        lib = self.tmp / "Photos"
        lib.mkdir()
        for i in range(30):
            (lib / f"IMG_{i:04d}.JPG").write_bytes(b"x" * 10)
        (lib / "IMG_0005.JPG").write_bytes(b"edited, now bigger")
        result = self._scan(lib)
        self.assertEqual(result["new"], 1)
        self.assertEqual(result["delete"], 0)

    def test_backup_folder_must_be_on_the_drive(self):
        lib = self.tmp / "Photos"
        lib.mkdir()
        (lib / "a.jpg").write_bytes(b"x")
        result = self._scan(lib, backup_path=self.tmp / "internal-disk-folder")
        self.assertIn("error", result)

    def test_library_has_files_ignores_hidden(self):
        lib = self.tmp / "Hidden"
        (lib / ".thumbnails").mkdir(parents=True)
        (lib / ".thumbnails" / "x.png").write_bytes(b"x")
        (lib / ".IMG_1.JPG.part").write_bytes(b"x")
        self.assertFalse(win_backup._library_has_files(lib))
        (lib / "IMG_1.JPG").write_bytes(b"x")
        self.assertTrue(win_backup._library_has_files(lib))

    def test_manual_sync_with_empty_source_deletes_nothing(self):
        win = FakeBackupWindow({}, self.drive)
        empty = self.tmp / "empty"
        empty.mkdir()
        win._manual_backup(empty, self.backup, delete_extraneous=True)
        _drain_main_loop()
        self.assertEqual(len(list(self.backup.iterdir())), 30)


class ImportNamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=_TMP_HOME))

    def test_unique_path_never_reuses_a_name(self):
        p = self.tmp / "IMG_1.JPG"
        self.assertEqual(importer_page.unique_path(p), p)
        p.write_bytes(b"a")
        (self.tmp / "IMG_1_1.JPG").write_bytes(b"b")
        self.assertEqual(importer_page.unique_path(p).name, "IMG_1_2.JPG")

    def test_copy_atomic_leaves_no_partial_file(self):
        src = self.tmp / "src.bin"
        src.write_bytes(b"photo")
        dst = self.tmp / "out" / "IMG.JPG"
        dst.parent.mkdir()
        importer_page.copy_atomic(src, dst)
        self.assertEqual(dst.read_bytes(), b"photo")
        self.assertEqual([p.name for p in dst.parent.iterdir()], ["IMG.JPG"])
        with self.assertRaises(OSError):
            importer_page.copy_atomic(self.tmp / "missing", self.tmp / "out" / "X.JPG")
        self.assertEqual([p.name for p in dst.parent.iterdir()], ["IMG.JPG"])

    def test_unreadable_file_is_not_called_damaged_without_tools(self):
        heic = self.tmp / "x.heic"
        heic.write_bytes(b"x" * 100)
        saved = importer_page.HAS_HEIF
        importer_page.HAS_HEIF = False
        try:
            self.assertFalse(importer_page.file_looks_corrupt(heic))
        finally:
            importer_page.HAS_HEIF = saved


class VersionTests(unittest.TestCase):
    def test_no_downgrade_offer(self):
        self.assertTrue(common.is_newer_version("v1.68.0", "1.67.0"))
        self.assertTrue(common.is_newer_version("1.100.0", "1.99.9"))
        self.assertFalse(common.is_newer_version("1.67.0", "1.68.0"))
        self.assertFalse(common.is_newer_version("1.67.0", "1.67.0"))
        self.assertFalse(common.is_newer_version("", "1.67.0"))


class LivePhotoTests(unittest.TestCase):
    def test_motion_is_paired_and_hidden(self):
        paths = ["/l/IMG_1.HEIC", "/l/IMG_1.MOV", "/l/IMG_2.MOV", "/other/IMG_1.MOV"]
        self.assertEqual(common.live_photo_pairs(paths), {"/l/IMG_1.HEIC": "/l/IMG_1.MOV"})
        self.assertEqual(common.drop_live_motion(paths),
                         ["/l/IMG_1.HEIC", "/l/IMG_2.MOV", "/other/IMG_1.MOV"])


class MetadataCacheTests(unittest.TestCase):
    def test_concurrent_puts_and_saves(self):
        import metadata
        tmp = Path(tempfile.mkdtemp(dir=_TMP_HOME))
        files = []
        for i in range(200):
            p = tmp / f"{i}.jpg"
            p.write_bytes(b"x")
            files.append(str(p))
        errors = []

        def put():
            try:
                for f in files:
                    metadata._cache_put("photo_date", f, 1.0)
            except Exception as e:  # pragma: no cover - the bug we guard
                errors.append(e)
        threads = [threading.Thread(target=put) for _ in range(4)]
        for t in threads:
            t.start()
        for _ in range(50):
            metadata.save_metadata_cache()
        for t in threads:
            t.join()
        metadata.save_metadata_cache()
        self.assertEqual(errors, [])
        self.assertTrue(os.path.exists(metadata._METADATA_CACHE_PATH))


if __name__ == "__main__":
    unittest.main()
