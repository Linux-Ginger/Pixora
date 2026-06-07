#!/usr/bin/env python3
"""Pixora phone diagnostic — shows exactly what media is reachable on a
connected iPhone/iPad over AFC (ifuse), so a "phone says N, Pixora says M"
mismatch can be explained.

Usage:  python3 tools/diag_phone.py
        (connect + unlock the device, tap Trust if asked)
"""
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

# Core Data epoch (2001-01-01 UTC) → Unix epoch offset.
COREDATA_EPOCH = 978307200

SUP = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".dng", ".mp4", ".mov",
       ".m4v", ".webp", ".gif", ".tiff", ".tif", ".3gp", ".bmp"}


def _fmt_cd(z):
    """Core Data timestamp → readable local date string."""
    if z is None:
        return "—"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S",
                             time.localtime(float(z) + COREDATA_EPOCH))
    except Exception:
        return f"?{z}"


def probe_photos_db(pd: Path):
    """Try to read PhotoData/Photos.sqlite — the source of truth for sort order."""
    db = pd / "Photos.sqlite"
    print("\n🗄️  Photos.sqlite probe:")
    if not db.exists():
        print("   ❌ Photos.sqlite not found / not exposed over AFC.")
        return
    print(f"   found, {db.stat().st_size // 1024} KB")

    # Copy DB + its WAL/SHM sidecars locally so sqlite sees a consistent file.
    tmp = Path(tempfile.mkdtemp(prefix="pixora_db_"))
    local = tmp / "Photos.sqlite"
    try:
        for suffix in ("", "-wal", "-shm"):
            s = pd / ("Photos.sqlite" + suffix)
            if s.exists():
                shutil.copy2(s, tmp / s.name)
        con = sqlite3.connect(f"file:{local}?mode=ro", uri=True)
        cur = con.cursor()

        cols = [r[1] for r in cur.execute("PRAGMA table_info(ZASSET)")]
        if not cols:
            print("   ❌ no ZASSET table — unexpected schema.")
            con.close()
            return
        # Report the columns we care about for matching files → dates.
        interesting = [c for c in cols if c in (
            "ZFILENAME", "ZDIRECTORY", "ZDATECREATED", "ZUUID",
            "ZADDEDDATE", "ZSAVEDASSETTYPE", "ZKINDSUBTYPE", "ZTRASHEDSTATE")]
        print("   ZASSET key columns present:", interesting)

        n = cur.execute("SELECT COUNT(*) FROM ZASSET").fetchone()[0]
        try:
            live = cur.execute(
                "SELECT COUNT(*) FROM ZASSET WHERE ZTRASHEDSTATE=0").fetchone()[0]
            print(f"   rows: {n} total, {live} not-trashed")
        except sqlite3.OperationalError:
            print(f"   rows: {n} total")

        print("   sample DCIM rows (filename · directory · ZDATECREATED):")
        for fn, d, z in cur.execute(
                "SELECT ZFILENAME, ZDIRECTORY, ZDATECREATED FROM ZASSET "
                "WHERE ZDIRECTORY LIKE 'DCIM%' ORDER BY ZDATECREATED DESC LIMIT 5"):
            print(f"     {fn}  ·  {d}  ·  {_fmt_cd(z)}")

        print("   sample NON-DCIM rows (the iCloud/CPL assets):")
        rows = cur.execute(
            "SELECT ZFILENAME, ZDIRECTORY, ZDATECREATED FROM ZASSET "
            "WHERE ZDIRECTORY IS NULL OR ZDIRECTORY NOT LIKE 'DCIM%' "
            "ORDER BY ZDATECREATED DESC LIMIT 8").fetchall()
        if not rows:
            print("     (none — every asset has a DCIM directory)")
        for fn, d, z in rows:
            print(f"     {fn}  ·  {d}  ·  {_fmt_cd(z)}")

        con.close()
    except Exception as e:
        print(f"   ❌ could not read DB: {e!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ids = subprocess.run(["idevice_id", "-l"],
                         capture_output=True, text=True).stdout.split()
    if not ids:
        print("❌ No iPhone found — connect, unlock, and tap Trust.")
        return
    udid = ids[0]

    mp = Path(tempfile.gettempdir()) / "pixora_diag"
    subprocess.run(["fusermount", "-uz", str(mp)], capture_output=True)
    mp.mkdir(exist_ok=True)
    r = subprocess.run(["ifuse", "--udid", udid, str(mp)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("❌ Mount failed:", r.stderr.strip())
        return
    time.sleep(1)

    try:
        print("📂 Top-level dirs on device:",
              sorted(p.name for p in mp.iterdir()))

        dcim = mp / "DCIM"
        cnt = Counter()
        dtot = 0
        for root, _, fs in os.walk(dcim):
            for fn in fs:
                e = Path(fn).suffix.lower()
                cnt[e] += 1
                if e in SUP:
                    dtot += 1
        print(f"\n📷 DCIM supported media: {dtot}")
        print("   all extensions in DCIM:", dict(cnt))

        pd = mp / "PhotoData"
        if pd.exists():
            print("\n🗂️  PhotoData — media per subdir "
                  "(big = likely originals ≥300 KB):")
            grand_big = 0
            for sub in sorted(p for p in pd.iterdir() if p.is_dir()):
                n = big = 0
                for root, _, fs in os.walk(sub):
                    for fn in fs:
                        if Path(fn).suffix.lower() in SUP:
                            n += 1
                            try:
                                if (Path(root) / fn).stat().st_size >= 300_000:
                                    big += 1
                            except OSError:
                                pass
                if n:
                    grand_big += big
                    print(f"   {sub.name:24s} media={n:5d}  big={big:5d}")
            print(f"\n   → total 'big' media in PhotoData: {grand_big}")

        # Deep-dive on CPLAssets — these are the iCloud-library originals that
        # never land in DCIM, so we need to know their layout to import them.
        cpl = pd / "CPLAssets"
        if cpl.exists():
            cnt = Counter()
            samples = []
            for root, _, fs in os.walk(cpl):
                for fn in fs:
                    e = Path(fn).suffix.lower()
                    if e in SUP:
                        cnt[e] += 1
                        if len(samples) < 10:
                            p = Path(root) / fn
                            rel = p.relative_to(cpl)
                            try:
                                kb = p.stat().st_size // 1024
                            except OSError:
                                kb = -1
                            samples.append(f"{rel}  ({kb} KB)")
            print("\n🔍 CPLAssets deep-dive:")
            print("   extensions:", dict(cnt))
            print("   sample files (path relative to CPLAssets):")
            for s in samples:
                print(f"     {s}")

        # Can we read the Photos database? It holds the authoritative capture
        # date (ZASSET.ZDATECREATED) that the iOS Photos app sorts by.
        probe_photos_db(pd)
    finally:
        subprocess.run(["fusermount", "-uz", str(mp)], capture_output=True)
        print("\n✅ done (device unmounted)")


if __name__ == "__main__":
    main()
