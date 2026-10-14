#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — win_library.py
#  Library tools: reorganize, duplicates, conversions
#  by LinuxGinger
# ─────────────────────────────────────────────

import datetime
import json
import os
import threading
import time
from pixora_i18n import _, ngettext
from common import (Adw, GLib, Gtk, is_video, log_info, log_warn, Pango, trash_file,
    save_settings)
from media import get_cache_path, load_thumbnail
import originals


_CONVERT_FAILED_PATH = os.path.expanduser("~/.cache/pixora/conversion_failed.json")


def _file_key(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return f"{path}|{st.st_size}|{int(st.st_mtime)}"


def _load_failed_conversions():
    try:
        with open(_CONVERT_FAILED_PATH) as f:
            data = json.load(f)
        return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()


def _remember_failed_conversions(paths):
    """Files that failed to convert are skipped by later automatic sweeps (the
    same broken file would otherwise be retried — and re-encoded — forever)."""
    if not paths:
        return
    known = _load_failed_conversions()
    known.update(k for k in (_file_key(p) for p in paths) if k)
    try:
        os.makedirs(os.path.dirname(_CONVERT_FAILED_PATH), exist_ok=True)
        tmp = _CONVERT_FAILED_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(sorted(known), f)
        os.replace(tmp, _CONVERT_FAILED_PATH)
    except OSError:
        pass


def _collect_library_files(photo_path, exts, skip_failed):
    from pathlib import Path as _P
    out = []
    if not photo_path or not os.path.isdir(photo_path):
        return out
    failed = _load_failed_conversions() if skip_failed else set()
    for root, dirs, fnames in os.walk(photo_path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in fnames:
            if fn.startswith("."):
                continue
            if os.path.splitext(fn)[1].lower() in exts:
                p = os.path.join(root, fn)
                if failed and _file_key(p) in failed:
                    continue
                out.append(_P(p))
    return out


class LibraryMixin:
    """Library tools: reorganize, duplicates, conversions (mixed into MainWindow)."""

    def on_structure_changed(self, value, btn):
        if btn.get_active():
            self.settings["structure"] = value
            save_settings(self.settings)

    def _count_media(self, paths):
        """(photos, videos). Accepts Paths or (src, dst) tuples."""
        photos = 0
        videos = 0
        for item in paths:
            src = item[0] if isinstance(item, tuple) else item
            if is_video(str(src)):
                videos += 1
            else:
                photos += 1
        return photos, videos

    def _format_media_counts(self, photos, videos):
        """Localized "3 photos and 1 video"."""
        parts = []
        if photos:
            parts.append(ngettext(
                "{n} photo", "{n} photos", photos).format(n=photos))
        if videos:
            parts.append(ngettext(
                "{n} video", "{n} videos", videos).format(n=videos))
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        return _("{a} and {b}").format(a=parts[0], b=parts[1])

    def _photo_date_for_structure(self, src, st, exif_tags, video_exts,
                                  video_date_fn):
        """EXIF/ffprobe first, mtime fallback. Skips iPhone-filename heuristic."""
        ext = src.suffix.lower()
        if ext in (".jpg", ".jpeg", ".heic", ".heif", ".png", ".dng",
                   ".tiff", ".tif"):
            try:
                from PIL import Image
                with Image.open(str(src)) as img:
                    exif = img.getexif()
                # DateTimeOriginal/Digitized live in ExifIFD sub (0x8769).
                # Main IFD only has 306 (ModifyDate) — updates on every edit.
                try:
                    exif_sub = exif.get_ifd(0x8769)
                except Exception:
                    exif_sub = {}
                sources = []
                for tag in exif_tags:
                    if tag in (36867, 36868):
                        sources.append((exif_sub, tag))
                    else:
                        sources.append((exif, tag))
                for ifd, tag in sources:
                    val = ifd.get(tag) if ifd else None
                    if val:
                        try:
                            return datetime.datetime.strptime(
                                val[:19], "%Y:%m:%d %H:%M:%S")
                        except (ValueError, TypeError):
                            continue
            except Exception:
                pass
        elif ext in video_exts:
            try:
                ts = video_date_fn(src)
                if ts:
                    return datetime.datetime.fromtimestamp(ts)
            except Exception:
                pass
        return datetime.datetime.fromtimestamp(st.st_mtime)

    def _scan_structure_mismatch(self):
        """(moves, dups): moves=[(src,dst)] wrong paths; dups=bit-identical duplicates."""
        from pathlib import Path as _P
        from importer_page import (
            dest_path as _dest_path, SUPPORTED_EXT,
            _EXIF_DATE_TAGS, _VIDEO_EXT, _get_video_date,
        )
        from common import live_photo_pairs
        photo_path = _P(self.settings.get("photo_path") or _P.home() / "Photos")
        if not photo_path.is_dir():
            return [], []
        structure = self.settings.get("structure", "year_month")
        moves = []
        dups = []
        reserved_targets = set()
        all_files = []
        for root, dirs, files in os.walk(str(photo_path)):
            # Hidden folders/files (.thumbnails, half-written .part files)
            # are not library content: never move or flatten them.
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fn in files:
                if fn.startswith("."):
                    continue
                src = _P(root) / fn
                if src.suffix.lower() in SUPPORTED_EXT:
                    all_files.append(src)
        # A Live Photo's movie must stay next to its still under the same
        # name, so it follows the still instead of its own (slightly
        # different) video date.
        motion_of = {_P(m): _P(s) for s, m in
                     live_photo_pairs([str(f) for f in all_files]).items()}
        all_files.sort(key=lambda f: f in motion_of)  # stills first
        still_dst = {}
        for src in all_files:
            try:
                st = src.stat()
            except OSError:
                continue
            still = motion_of.get(src)
            if still is not None and still in still_dst:
                dst = still_dst[still].with_suffix(src.suffix)
            else:
                photo_dt = self._photo_date_for_structure(
                    src, st, _EXIF_DATE_TAGS, _VIDEO_EXT, _get_video_date,
                )
                dst = _dest_path(photo_path, structure, src.name, photo_dt)
            if src.resolve() == dst.resolve():
                reserved_targets.add(str(dst.resolve()))
                still_dst[src] = dst
                continue
            # Target exists or reserved by another src.
            dst_str = str(dst.resolve() if dst.exists() else dst)
            if dst.exists() or dst_str in reserved_targets:
                try:
                    if dst.exists():
                        dst_st = dst.stat()
                        if dst_st.st_size == st.st_size \
                                and int(dst_st.st_mtime) == int(st.st_mtime):
                            # Keeper = the file already filed correctly (dst).
                            dups.append((dst, src))
                            continue
                except OSError:
                    pass
                # Not identical → unique suffix.
                stem = dst.stem
                ext = dst.suffix
                counter = 1
                while True:
                    cand = dst.parent / f"{stem}_{counter}{ext}"
                    cand_str = str(cand.resolve() if cand.exists() else cand)
                    if not cand.exists() and cand_str not in reserved_targets:
                        dst = cand
                        break
                    counter += 1
            reserved_targets.add(str(dst.resolve() if dst.exists() else dst))
            still_dst[src] = dst
            moves.append((src, dst))
        return moves, dups

    # ── Archive duplicate cleanup ─────────────────────────────────────────
    @staticmethod
    def _dup_base_name(path_str):
        """Stem with a trailing _N counter stripped, lowercased."""
        import re as _re
        return _re.sub(r"_\d+$", "", os.path.splitext(os.path.basename(path_str))[0]).lower()

    def _find_archive_duplicates(self):
        """Group near-identical archive photos. Perceptual hash leads; a shared
        base name (IMG_1234 vs IMG_1234_1) loosens the match as confirmation."""
        try:
            from importer_page import (
                build_library_hashes, THRESHOLD_MAP, HAS_IMAGEHASH, SUPPORTED_EXT)
            import imagehash
        except Exception:
            return []
        if not HAS_IMAGEHASH:
            return []
        photo_path = self.settings.get("photo_path") or os.path.join(
            os.path.expanduser("~"), "Photos")
        if not os.path.isdir(photo_path):
            return []
        hashes = build_library_hashes(photo_path)
        threshold = THRESHOLD_MAP.get(self.settings.get("duplicate_threshold", 2), 6)
        items = []
        for p, h in hashes.items():
            try:
                items.append((p, imagehash.hex_to_hash(h)))
            except Exception:
                continue
        # Deterministic clustering via union-find: every pair within the
        # threshold (or sharing a base name, loosened) is an edge, and each
        # connected component becomes one group. This is order-independent —
        # the old greedy pass could "absorb" a copy into the wrong group or
        # drop it, so the same photo was sometimes missed.
        n = len(items)
        parent = list(range(n))

        def _find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def _union(a, b):
            ra, rb = _find(a), _find(b)
            if ra != rb:
                parent[ra] = rb

        bases = [self._dup_base_name(p) for p, _h in items]
        for i in range(n):
            for j in range(i + 1, n):
                d = items[i][1] - items[j][1]
                if d <= threshold or (bases[i] == bases[j] and d <= threshold + 6):
                    _union(i, j)

        comps = {}
        for i in range(n):
            comps.setdefault(_find(i), []).append(items[i][0])

        _sortkey = self._dup_rank  # keeper first: least copy-like name

        groups = []
        for members in comps.values():
            if len(members) > 1:
                members.sort(key=_sortkey)  # keeper first: no _N suffix, largest
                groups.append(members)

        # Exact-duplicate pass for everything that has no perceptual hash —
        # videos (never perceptually hashed) and the odd photo that failed to
        # decode. Byte-identical files share a content signature, so a copied
        # video is caught here. 100% safe: only exact copies match.
        from pathlib import Path as _P
        hashed = set(hashes.keys())
        sigs = {}
        for root, _dirs, fns in os.walk(photo_path):
            for fn in fns:
                if _P(fn).suffix.lower() not in SUPPORTED_EXT:
                    continue
                p = str(_P(root) / fn)
                if p in hashed:
                    continue  # already covered by the perceptual pass
                sig = self._content_signature(p)
                if sig:
                    sigs.setdefault(sig, []).append(p)
        for members in sigs.values():
            if len(members) > 1:
                members.sort(key=_sortkey)
                groups.append(members)
        return groups

    def _content_signature(self, path):
        """Fast fingerprint for exact-duplicate detection (size + head/tail
        bytes). Byte-identical files match; distinct files practically never do.
        Used for videos and any file without a perceptual hash."""
        import hashlib
        try:
            size = os.path.getsize(path)
        except OSError:
            return None
        h = hashlib.sha1()
        h.update(str(size).encode())
        chunk = 2 * 1024 * 1024
        try:
            with open(path, "rb") as f:
                h.update(f.read(chunk))
                if size > 2 * chunk:
                    f.seek(-chunk, os.SEEK_END)
                    h.update(f.read(chunk))
        except OSError:
            return None
        return h.hexdigest()

    def _open_dup_window(self, scanning=False):
        """Build the duplicate-review window shell. Shared by the manual
        cleanup and the background tidy-up — nothing is ever deleted without
        the user picking copies here first."""
        win = Adw.Window()
        win.set_title(_("Review duplicates"))
        win.set_transient_for(self._settings_dialog or self)
        win.set_modal(True)
        win.set_default_size(760, 680)
        self._dup_win = win

        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())

        self._dup_win_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._dup_win_box.set_margin_top(12)
        self._dup_win_box.set_margin_bottom(12)
        self._dup_win_box.set_margin_start(16)
        self._dup_win_box.set_margin_end(16)

        if scanning:
            sp = Gtk.Spinner()
            sp.start()
            sp.set_size_request(-1, 60)
            lbl = Gtk.Label(label=_("Scanning your archive for duplicates…"))
            lbl.add_css_class("dim-label")
            self._dup_win_box.append(sp)
            self._dup_win_box.append(lbl)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self._dup_win_box)
        tv.set_content(scroll)

        bar = Gtk.ActionBar()
        self._dup_selall_btn = Gtk.Button(label=_("Select all"))
        self._dup_selall_btn.set_sensitive(False)
        self._dup_selall_btn.connect("clicked", lambda b: self._dup_set_all(True))
        self._dup_deselall_btn = Gtk.Button(label=_("Deselect all"))
        self._dup_deselall_btn.set_sensitive(False)
        self._dup_deselall_btn.connect("clicked", lambda b: self._dup_set_all(False))
        bar.pack_start(self._dup_selall_btn)
        bar.pack_start(self._dup_deselall_btn)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda b: win.close())
        bar.pack_end(cancel)
        self._dup_delete_btn = Gtk.Button(label=_("Move selected to trash"))
        self._dup_delete_btn.add_css_class("destructive-action")
        self._dup_delete_btn.set_sensitive(False)
        self._dup_delete_btn.connect("clicked", self._on_delete_dupes)
        bar.pack_end(self._dup_delete_btn)
        tv.add_bottom_bar(bar)

        win.set_content(tv)
        # Closing without deleting = "not now": the background tidy-up must
        # not reopen this window every few minutes for the same files.
        win.connect("close-request", self._on_dup_window_closed)
        self._dup_deleted_any = False
        win.present()

    def _on_dup_window_closed(self, _win):
        if not getattr(self, "_dup_deleted_any", False):
            self._dup_review_dismissed = True
        return False

    def _prompt_find_duplicates(self, btn=None):
        self._open_dup_window(scanning=True)
        threading.Thread(target=self._scan_dupes_bg, daemon=True).start()

    def _scan_dupes_bg(self):
        groups = self._find_archive_duplicates()
        GLib.idle_add(self._render_dup_groups, groups, None)

    def _review_reorganize_dups(self, pairs):
        """Open the review window for (keeper, duplicate) pairs found by tidy-up."""
        if not pairs:
            return False
        by_keeper = {}
        order = []
        for keeper, dup in pairs:
            k = str(keeper)
            if k not in by_keeper:
                by_keeper[k] = [str(keeper)]
                order.append(k)
            by_keeper[k].append(str(dup))
        groups = [by_keeper[k] for k in order]
        self._open_dup_window(scanning=False)
        self._render_dup_groups(
            groups,
            _("While tidying up, Pixora found photos that look like duplicates. Nothing has been deleted — pick which copies to remove. The original is always kept."))
        return False

    def _dup_set_all(self, active):
        for chk in getattr(self, "_dup_checks", {}).values():
            chk.set_active(active)

    def _render_dup_groups(self, groups, intro):
        if not getattr(self, "_dup_win", None):
            return False
        while child := self._dup_win_box.get_first_child():
            self._dup_win_box.remove(child)
        self._dup_checks = {}  # path -> CheckButton (checked = delete)
        if not groups:
            empty = Adw.StatusPage()
            empty.set_icon_name("emblem-ok-symbolic")
            empty.set_title(_("No duplicates found"))
            empty.set_description(_("Your archive looks clean."))
            self._dup_win_box.append(empty)
            return False
        head = Gtk.Label()
        head.add_css_class("dim-label")
        head.set_xalign(0)
        head.set_wrap(True)
        if intro:
            head.set_text(intro)
        else:
            total = sum(len(g) - 1 for g in groups)
            head.set_text(ngettext(
                "Found %d possible duplicate. The original (the one without a _number) is kept; checked copies are deleted.",
                "Found %d possible duplicates. The original (the one without a _number) of each set is kept; checked copies are deleted.",
                total) % total)
        self._dup_win_box.append(head)
        photo_n = 0
        video_n = 0
        for group in groups:
            if all(is_video(p) for p in group):
                video_n += 1
                title = _("Video {n}").format(n=video_n)
            else:
                photo_n += 1
                title = _("Photo {n}").format(n=photo_n)
            self._dup_win_box.append(self._make_dup_group_card(group, title))
        self._dup_delete_btn.set_sensitive(True)
        self._dup_selall_btn.set_sensitive(True)
        self._dup_deselall_btn.set_sensitive(True)
        return False

    def _dup_rank(self, path):
        """Lower = more likely the original. Copy markers (kopie/copy, a _N or
        (N) suffix) push a name down; ties broken by larger size, then shorter
        name. Used to pick the keeper and order each group."""
        import re as _re
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        score = 0
        if _re.search(r"_\d+$", stem):
            score += 2                       # IMG_1234_1
        if _re.search(r"\(\d+\)\s*$", stem):
            score += 2                       # name (1)
        if "kopie" in stem or "copy" in stem or "copia" in stem:
            score += 3                       # file-manager copy
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        return (score, -size, len(stem))

    def _dup_group_keeper(self, group):
        """The 'main' file is the least copy-like name (see _dup_rank)."""
        return min(group, key=self._dup_rank)

    def _make_dup_group_card(self, group, title):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("card")
        card.set_margin_bottom(8)

        header = Gtk.Label(label=title)
        header.add_css_class("heading")
        header.set_xalign(0)
        header.set_margin_top(10)
        header.set_margin_start(12)
        card.append(header)

        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(4)
        flow.set_min_children_per_line(2)
        flow.set_column_spacing(8)
        flow.set_row_spacing(8)
        flow.set_margin_top(6)
        flow.set_margin_bottom(10)
        flow.set_margin_start(10)
        flow.set_margin_end(10)

        keeper = self._dup_group_keeper(group)
        for path in group:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            try:
                pb = load_thumbnail(path)
            except Exception:
                pb = None
            if pb is not None:
                pic = Gtk.Picture.new_for_pixbuf(pb)
                pic.set_content_fit(Gtk.ContentFit.COVER)
                pic.set_size_request(150, 150)
                pic.set_overflow(Gtk.Overflow.HIDDEN)
                pic.add_css_class("card")
            else:
                pic = Gtk.Image.new_from_icon_name("image-missing-symbolic")
                pic.set_pixel_size(48)
                pic.set_size_request(150, 150)
            if path == keeper:
                col.append(pic)
                tag = Gtk.Label(label=_("✅ Keep (original)"))
                tag.add_css_class("caption")
                col.append(tag)
            else:
                # Tick to pick — same feel as choosing photos during import.
                # Checked = delete.
                overlay = Gtk.Overlay()
                overlay.set_child(pic)
                chk = Gtk.CheckButton()
                chk.set_active(True)
                chk.set_halign(Gtk.Align.START)
                chk.set_valign(Gtk.Align.START)
                chk.set_margin_top(6)
                chk.set_margin_start(6)
                self._dup_checks[path] = chk
                overlay.add_overlay(chk)
                col.append(overlay)
                tag = Gtk.Label(label=_("Delete"))
                tag.add_css_class("caption")
                tag.add_css_class("dim-label")
                col.append(tag)
            flow.append(col)

        card.append(flow)
        return card

    def _on_delete_dupes(self, _btn):
        to_delete = [p for p, chk in getattr(self, "_dup_checks", {}).items()
                     if chk.get_active()]
        if not to_delete:
            if getattr(self, "_dup_win", None):
                self._dup_win.close()
            return
        # Show a "deleting…" spinner and run the removal off-thread so the
        # window doesn't freeze while many (large) files are deleted.
        for b in (getattr(self, "_dup_delete_btn", None),
                  getattr(self, "_dup_selall_btn", None),
                  getattr(self, "_dup_deselall_btn", None)):
            if b is not None:
                b.set_sensitive(False)
        while child := self._dup_win_box.get_first_child():
            self._dup_win_box.remove(child)
        sp = Gtk.Spinner()
        sp.start()
        sp.set_size_request(-1, 60)
        lbl = Gtk.Label(label=_("Deleting…"))
        lbl.add_css_class("dim-label")
        self._dup_win_box.append(sp)
        self._dup_win_box.append(lbl)
        self._dup_deleted_any = True
        threading.Thread(
            target=self._delete_dupes_thread, args=(to_delete,), daemon=True).start()

    def _delete_dupes_thread(self, to_delete):
        deleted = []
        for p in to_delete:
            # Cache key uses the file's mtime, so resolve it before the move.
            try:
                cache = get_cache_path(p)
            except OSError:
                cache = None
            try:
                trash_file(p)  # recoverable from the trash
                deleted.append(p)
                originals.drop(p)
                if cache and os.path.exists(cache):
                    os.remove(cache)
            except Exception as e:
                log_warn(_("Duplicate delete failed for {p}: {e}").format(p=p, e=e))
        GLib.idle_add(self._after_delete_dupes, to_delete, deleted)

    def _after_delete_dupes(self, to_delete, deleted):
        fav_changed = False
        for p in deleted:
            if p in self._favorites:
                self._favorites.discard(p)
                fav_changed = True
        if fav_changed:
            self._schedule_save_favorites()
        log_info(_("Duplicate cleanup: moved {n} files to the trash").format(n=len(deleted)))
        if getattr(self, "_dup_win", None):
            self._dup_win.close()
            self._dup_win = None
        self.reload_photos()
        return False

    def _reorganize_now(self):
        """Dev 'run now' button: tidy up without a confirmation popup (moves run
        silently; duplicates still always go through the review window)."""
        self._reorganize_from_button = True
        self._prompt_reorganize(from_startup=False)

    def _prompt_reorganize(self, from_startup=False):
        """Confirm dialog with counts. Silent if from_startup + nothing to do."""
        self._reorganize_active = True
        def _scan():
            moves, dups = self._scan_structure_mismatch()
            GLib.idle_add(self._on_reorganize_scan_done, moves, dups, from_startup)
        threading.Thread(target=_scan, daemon=True).start()

    def _on_reorganize_scan_done(self, moves, dups, from_startup):
        if not moves and not dups:
            self._reorganize_active = False
            if not from_startup:
                dlg = Adw.AlertDialog(
                    heading=_("Folder structure is already correct"),
                    body=_("All photos already match your chosen structure."),
                )
                dlg.add_response("ok", _("OK"))
                dlg.set_default_response("ok")
                dlg.set_close_response("ok")
                self._present_dialog(dlg)
            return False
        # Duplicates are NEVER auto-deleted — always reviewed. Moves may be
        # silent (auto-confirm or a dev "run now" click); duplicates skip to
        # the review window regardless.
        from_button = getattr(self, "_reorganize_from_button", False)
        self._reorganize_from_button = False
        silent_moves = self.settings.get("reorganize_silent", False) or from_button
        if not moves:
            self._reorganize_active = False
            if from_startup and getattr(self, "_dup_review_dismissed", False):
                return False  # already reviewed (and skipped) this session
            self._review_reorganize_dups(dups)
            return False
        if silent_moves:
            self._reorganize_silent_run = True
            log_info(_("Silent reorganize started: {m} moves, {d} possible duplicates").format(
                m=len(moves), d=len(dups)))
            threading.Thread(
                target=self._do_reorganize, args=(moves, dups),
                daemon=True,
            ).start()
            return False
        self._reorganize_silent_run = False
        structure = self.settings.get("structure", "year_month")
        structure_label = {
            "flat": _("All together"),
            "year": _("By year"),
            "year_month": _("By year and month"),
        }.get(structure, structure)
        body_lines = [
            _("Chosen structure: {s}").format(s=structure_label),
        ]
        if moves:
            ph, vi = self._count_media(moves)
            body_lines.append(_("{c} will be moved to the right folder.").format(
                c=self._format_media_counts(ph, vi)))
        if dups:
            body_lines.append(ngettext(
                "%d possible duplicate will be shown afterwards so you can review it — nothing is deleted automatically.",
                "%d possible duplicates will be shown afterwards so you can review them — nothing is deleted automatically.",
                len(dups)) % len(dups))
        dlg = Adw.AlertDialog(
            heading=_("Reorganize folder structure?"),
            body="\n".join(body_lines),
        )
        dlg.add_response("cancel", _("Later"))
        dlg.add_response("go", _("Organize now"))
        dlg.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("go")
        dlg.set_close_response("cancel")
        try:
            dlg.set_body_use_markup(False)
        except Exception:
            pass
        dlg.connect("response", self._on_reorganize_response, moves, dups)
        # Pause playing video while popup is up.
        self._pause_video_for_popup()
        self._present_dialog(dlg)
        return False

    def _on_reorganize_response(self, dlg, response, moves, dups):
        if response != "go":
            self._reorganize_active = False
            # "Later" → no more auto-popup this session.
            self._structure_popup_dismissed = True
            self._resume_video_after_popup()
            return
        if self._reorganize_moving:
            return  # prevent double thread-start
        # Leave settings open — force_close on Adw dialog from signal → SIGSEGV.
        threading.Thread(
            target=self._do_reorganize, args=(moves, dups), daemon=True,
        ).start()

    def _do_reorganize(self, moves, dups):
        import shutil as _sh
        from pathlib import Path as _P
        photo_path = _P(self.settings.get("photo_path") or _P.home() / "Photos")
        # Disable watcher — mid-move FileNotFoundError crashes _load_thread.
        try:
            self.stop_watcher()
        except Exception:
            pass
        moved = 0
        removed = 0
        errors = []
        total = max(1, len(moves) + len(dups))
        # Precompute total bytes for GB counter (src size = what we process).
        total_bytes = 0
        sizes_moves = []
        sizes_dups = []
        for src, _dst in moves:
            try:
                sz = src.stat().st_size
            except OSError:
                sz = 0
            sizes_moves.append(sz)
            total_bytes += sz
        for _keeper, dup in dups:
            try:
                sz = dup.stat().st_size
            except OSError:
                sz = 0
            sizes_dups.append(sz)
            total_bytes += sz
        self._reorganize_moving = True
        self._reorganize_fraction = 0.0
        self._reorganize_total_count = total
        self._reorganize_done_count = 0
        self._reorganize_total_bytes = total_bytes
        self._reorganize_done_bytes = 0
        self._reorganize_start_time = time.time()
        self._reorganize_current_name = ""
        tot_ph = sum(1 for s, _d in moves if not is_video(str(s)))
        tot_vi = sum(1 for s, _d in moves if is_video(str(s)))
        tot_ph += sum(1 for _k, d in dups if not is_video(str(d)))
        tot_vi += sum(1 for _k, d in dups if is_video(str(d)))
        self._reorganize_total_label = self._format_media_counts(tot_ph, tot_vi)
        # Silent mode: donut only, no fullscreen.
        if self._reorganize_silent_run:
            GLib.idle_add(self._on_reorganize_silent_start)
        else:
            GLib.idle_add(self._on_reorganize_progress_start)
        done = 0
        moved_photos = 0
        moved_videos = 0
        removed_photos = 0
        removed_videos = 0
        first_err_logged = False
        renames = []
        touched_dirs = set()
        for (src, dst), sz in zip(moves, sizes_moves):
            self._reorganize_current_name = src.name
            is_vid = is_video(str(src))
            try:
                if not src.exists():
                    raise FileNotFoundError(str(src))
                # The plan was made earlier (the dialog may have sat open while
                # an import ran): re-check, never move onto an existing file.
                if dst.exists():
                    from importer_page import unique_path
                    dst = unique_path(dst)
                dst.parent.mkdir(parents=True, exist_ok=True)
                _sh.move(str(src), str(dst))
                renames.append((str(src), str(dst)))
                touched_dirs.add(str(src.parent))
                originals.rekey(str(src), str(dst))
                moved += 1
                if is_vid:
                    moved_videos += 1
                else:
                    moved_photos += 1
            except Exception as e:
                errors.append(f"{src.name}: {e}")
                if not first_err_logged:
                    log_info(
                        f"Reorganize move failed: {src} → {dst}: {e}"
                    )
                    first_err_logged = True
            done += 1
            self._reorganize_done_count = done
            self._reorganize_done_bytes += sz
            self._reorganize_fraction = done / total
        # Duplicates are NOT deleted here anymore — they're handed to the review
        # window so the user decides. We only advance the progress bar past them.
        for (_keeper, dup), sz in zip(dups, sizes_dups):
            self._reorganize_current_name = dup.name
            done += 1
            self._reorganize_done_count = done
            self._reorganize_done_bytes += sz
            self._reorganize_fraction = done / total
        # Prune folders this run emptied (and their emptied parents); folders
        # the user made themselves, even empty ones, are left alone.
        root_str = str(photo_path)
        for d in sorted(touched_dirs, key=len, reverse=True):
            while d != root_str and d.startswith(root_str + os.sep):
                try:
                    os.rmdir(d)  # only succeeds when empty
                except OSError:
                    break
                d = os.path.dirname(d)
        if renames:
            GLib.idle_add(self._follow_renames, renames)
        log_info(_("Reorganize: {m} moved, {d} possible duplicates to review, {e} errors").format(
            m=moved, d=len(dups), e=len(errors),
        ))
        GLib.idle_add(
            self._reorganize_done,
            moved, removed, errors,
            moved_photos, moved_videos, removed_photos, removed_videos,
        )
        # Hand any possible duplicates to the review window (never auto-deleted).
        if dups and not getattr(self, "_dup_review_dismissed", False):
            GLib.idle_add(self._review_reorganize_dups, dups)

    def _follow_renames(self, renames):
        """Favourites are keyed by path: carry them over to the new location."""
        changed = False
        for old, new in renames:
            if old in self._favorites:
                self._favorites.discard(old)
                self._favorites.add(new)
                changed = True
        if changed:
            self._schedule_save_favorites()
        return False

    def _reorganize_done(self, moved, removed, errors,
                         moved_photos=0, moved_videos=0,
                         removed_photos=0, removed_videos=0):
        self._reorganize_moving = False
        self._reorganize_fraction = 1.0
        if not self._backup_scanning and not self._backup_running:
            self._set_donuts_visible(False)
        self._redraw_donuts()
        # Re-enable watcher before reload_photos.
        try:
            self.start_watcher(self.settings.get("photo_path"))
        except Exception:
            pass
        # Silent: reload + cooldown, no UI.
        if self._reorganize_silent_run:
            self._reorganize_silent_run = False
            self.reload_photos()
            self._reorganize_active = False
            self._reorganize_block_until = time.time() + 10.0
            GLib.timeout_add_seconds(
                10, self._maybe_trigger_backup_after_reorganize)
            return False
        # Non-silent: fullscreen with Close; cleanup runs on close click.
        parts = []
        if moved:
            parts.append(_("{c} moved").format(
                c=self._format_media_counts(moved_photos, moved_videos)))
        if removed:
            parts.append(_("{c} removed (duplicate)").format(
                c=self._format_media_counts(removed_photos, removed_videos)))
        if not parts:
            parts.append(_("No changes needed"))
        summary = ", ".join(parts) + "."
        if errors:
            summary += "\n" + ngettext(
                "{n} error — see dev log.",
                "{n} errors — see dev log.",
                len(errors),
            ).format(n=len(errors))
        if hasattr(self, "reorganize_subtitle"):
            self.reorganize_subtitle.set_text(summary)
        if hasattr(self, "reorganize_title"):
            self.reorganize_title.set_text(_("Folder structure updated"))
        if hasattr(self, "reorganize_bar"):
            self.reorganize_bar.set_fraction(1.0)
            self.reorganize_bar.set_text("100%")
        if hasattr(self, "reorganize_detail"):
            self.reorganize_detail.set_text("")
        if hasattr(self, "reorganize_spinner"):
            self.reorganize_spinner.stop()
            self.reorganize_spinner.set_visible(False)
        if hasattr(self, "reorganize_close_btn"):
            self.reorganize_close_btn.set_visible(True)
        return False

    def _on_reorganize_close_clicked(self, _btn):
        """Close fullscreen reorg: back-to-grid + cooldown + backup trigger."""
        self.header.set_visible(True)
        self.bottom_stack.set_visible(True)
        return_to = getattr(self, "_reorganize_return_page", "grid") or "grid"
        self.main_stack.set_visible_child_name(return_to)
        if hasattr(self, "reorganize_close_btn"):
            self.reorganize_close_btn.set_visible(False)
        if hasattr(self, "reorganize_spinner"):
            self.reorganize_spinner.set_visible(True)
        self.reload_photos()
        self._reorganize_active = False
        self._reorganize_block_until = time.time() + 10.0
        GLib.timeout_add_seconds(
            10, self._maybe_trigger_backup_after_reorganize)

    def _build_reorganize_page(self):
        """Fullscreen progress page (mirrors ImporterPage)."""
        clamp = Adw.Clamp()
        clamp.set_maximum_size(480)
        clamp.set_valign(Gtk.Align.CENTER)
        clamp.set_vexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        box.set_margin_top(48)
        box.set_margin_bottom(48)
        box.set_margin_start(24)
        box.set_margin_end(24)

        self.reorganize_spinner = Gtk.Spinner()
        self.reorganize_spinner.set_size_request(48, 48)
        self.reorganize_spinner.set_halign(Gtk.Align.CENTER)
        box.append(self.reorganize_spinner)

        self.reorganize_title = Gtk.Label(label=_("Updating folder structure"))
        self.reorganize_title.add_css_class("title-2")
        self.reorganize_title.set_halign(Gtk.Align.CENTER)
        box.append(self.reorganize_title)

        self.reorganize_subtitle = Gtk.Label()
        self.reorganize_subtitle.add_css_class("dim-label")
        self.reorganize_subtitle.set_halign(Gtk.Align.CENTER)
        self.reorganize_subtitle.set_wrap(True)
        self.reorganize_subtitle.set_max_width_chars(52)
        box.append(self.reorganize_subtitle)

        self.reorganize_bar = Gtk.ProgressBar()
        self.reorganize_bar.set_show_text(True)
        box.append(self.reorganize_bar)

        self.reorganize_detail = Gtk.Label()
        self.reorganize_detail.add_css_class("dim-label")
        self.reorganize_detail.add_css_class("caption")
        self.reorganize_detail.set_halign(Gtk.Align.CENTER)
        self.reorganize_detail.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.reorganize_detail.set_max_width_chars(52)
        box.append(self.reorganize_detail)

        self.reorganize_close_btn = Gtk.Button(label=_("Close"))
        self.reorganize_close_btn.add_css_class("pill")
        self.reorganize_close_btn.add_css_class("suggested-action")
        self.reorganize_close_btn.set_halign(Gtk.Align.CENTER)
        self.reorganize_close_btn.set_visible(False)
        self.reorganize_close_btn.connect(
            "clicked", self._on_reorganize_close_clicked)
        box.append(self.reorganize_close_btn)

        clamp.set_child(box)
        return clamp

    def _on_reorganize_progress_start(self):
        """Main-thread: swap to fullscreen + start label-refresh tick."""
        self._reorganize_return_page = \
            self.main_stack.get_visible_child_name() or "grid"
        self.header.set_visible(False)
        self.bottom_stack.set_visible(False)
        self.main_stack.set_visible_child_name("reorganize")
        if hasattr(self, "reorganize_title"):
            self.reorganize_title.set_text(_("Updating folder structure"))
        if hasattr(self, "reorganize_spinner"):
            self.reorganize_spinner.set_visible(True)
            self.reorganize_spinner.start()
        if hasattr(self, "reorganize_close_btn"):
            self.reorganize_close_btn.set_visible(False)
        if hasattr(self, "reorganize_bar"):
            self.reorganize_bar.set_fraction(0.0)
            self.reorganize_bar.set_text("0%")
        if hasattr(self, "reorganize_subtitle"):
            self.reorganize_subtitle.set_text("")
        if hasattr(self, "reorganize_detail"):
            self.reorganize_detail.set_text("")
        # Hide donut while fullscreen page visible.
        if not self._backup_scanning and not self._backup_running:
            self._set_donuts_visible(False)
        if self._reorganize_anim_id is None:
            self._reorganize_anim_id = GLib.timeout_add(
                200, self._tick_reorganize_progress)
        return False

    def _on_reorganize_silent_start(self):
        """Silent: show only donut; tick redraws it."""
        tip = _("Updating folder structure…")
        self._set_donuts_visible(True)
        if hasattr(self, "_backup_donut_btn"):
            self._backup_donut_btn.set_tooltip_text(tip)
        if hasattr(self, "_viewer_donut_btn"):
            self._viewer_donut_btn.set_tooltip_text(tip)
        if self._reorganize_anim_id is None:
            self._reorganize_anim_id = GLib.timeout_add(
                200, self._tick_reorganize_progress)
        self._redraw_donuts()
        return False

    def _tick_reorganize_progress(self):
        if not self._reorganize_moving:
            self._reorganize_anim_id = None
            return False
        # Fill donut; update fullscreen labels when present.
        self._redraw_donuts()
        if not hasattr(self, "reorganize_bar") \
                or self.main_stack.get_visible_child_name() != "reorganize":
            return True
        frac = max(0.0, min(1.0, self._reorganize_fraction))
        self.reorganize_bar.set_fraction(frac)
        self.reorganize_bar.set_text(f"{int(frac * 100)}%")
        done_n = self._reorganize_done_count
        total_n = self._reorganize_total_count
        done_gb = self._reorganize_done_bytes / (1024 ** 3)
        total_gb = self._reorganize_total_bytes / (1024 ** 3)
        eta = self._format_reorganize_eta(frac)
        total_label = self._reorganize_total_label or str(total_n)
        counts = _("{done} of {total_label}").format(
            done=done_n, total_label=total_label)
        subtitle = _("{counts} · {dg:.2f} / {tg:.2f} GB · about {eta} left").format(
            counts=counts, dg=done_gb, tg=total_gb, eta=eta,
        )
        self.reorganize_subtitle.set_text(subtitle)
        self.reorganize_detail.set_text(self._reorganize_current_name or "")
        return True

    def _format_reorganize_eta(self, frac):
        if frac <= 0.001:
            return _("unknown")
        elapsed = time.time() - self._reorganize_start_time
        if elapsed < 0.5:
            return _("unknown")
        remaining = max(0.0, elapsed * (1.0 - frac) / frac)
        if remaining < 60:
            secs = int(remaining)
            return ngettext("{n} second", "{n} seconds", secs).format(n=secs)
        mins = int(remaining / 60)
        return ngettext("{n} minute", "{n} minutes", mins).format(n=mins)

    def _maybe_trigger_backup_after_reorganize(self):
        """One-shot 10s after _reorganize_done: start backup-scan."""
        try:
            if self._backup_running or self._backup_scanning:
                return False
            if not (self.settings.get("backup_enabled")
                    and self.settings.get("backup_uuid")
                    and self.settings.get("backup_path")):
                return False
            if self._backup_drive_mountpoint() is None:
                return False
            log_info(_("Backup scan after reorganize cooldown"))
            self._trigger_backup_scan()
        except Exception:
            pass
        return False

    def _maybe_check_structure_on_startup(self):
        """Home-grid +2s: first structure+backup scan; 60s tick takes over."""
        if self._structure_startup_scanned:
            return False
        ready_at = getattr(self, "_home_ready_at", None)
        if ready_at is None or (time.time() - ready_at) < 2.0:
            GLib.timeout_add(500, self._maybe_check_structure_on_startup)
            return False
        self._structure_startup_scanned = True
        self._periodic_scan()
        # Sweep leftover HEICs a bit later so it doesn't fight the startup load;
        # video sweep is staggered after it (they share the conversion slot).
        GLib.timeout_add_seconds(8, self._maybe_run_heic_sweep)
        GLib.timeout_add_seconds(12, self._maybe_run_video_sweep)
        return False

    def _trigger_structure_scan(self):
        """Silent threaded structure-scan; on mismatch shows reorg popup."""
        if self._structure_scanning:
            return
        if self._heic_converting:
            return
        if self._importer_open():
            return  # never move files around while an import writes them
        self._structure_scanning = True
        self._backup_scan_phase = 0.0
        tip = _("Checking folder structure…")
        if hasattr(self, "_backup_donut_btn"):
            self._set_donuts_visible(True)
            self._backup_donut_btn.set_tooltip_text(tip)
        if hasattr(self, "_viewer_donut_btn"):
            self._viewer_donut_btn.set_tooltip_text(tip)
        if self._backup_scan_anim_id is None:
            self._backup_scan_anim_id = GLib.timeout_add(
                120, self._tick_backup_scan)
        log_info(_("Structure scan started"))

        def _scan():
            try:
                moves, dups = self._scan_structure_mismatch()
            except Exception:
                moves, dups = [], []
            GLib.idle_add(self._on_periodic_structure_done, moves, dups)
        threading.Thread(target=_scan, daemon=True).start()

    def _on_periodic_structure_done(self, moves, dups):
        self._structure_scanning = False
        # Only hide donut if nothing else running.
        if not self._backup_scanning and not self._backup_running:
            self._set_donuts_visible(False)
        self._redraw_donuts()
        had_mismatch = bool(moves or dups)
        if had_mismatch:
            log_info(_("Structure scan done: {m} moves, {d} duplicates").format(
                m=len(moves), d=len(dups),
            ))
        else:
            log_info(_("Structure scan done: structure is correct"))
        if had_mismatch and not self._structure_popup_dismissed \
                and not self._reorganize_active:
            self._reorganize_active = True
            # Silent-check lives in _on_reorganize_scan_done.
            self._on_reorganize_scan_done(moves, dups, True)
            return False
        self._maybe_trigger_backup_now()
        # Last step of the maintenance pass: convert leftover HEICs/videos, but
        # only once backup isn't running — so it's one donut at a time.
        if not self._backup_scanning and not self._backup_running:
            self._maybe_run_heic_sweep()
            self._maybe_run_video_sweep()
        return False

    def _maybe_trigger_backup_now(self):
        """Start backup-scan if idle + configured. Cooldown skips re-scanning if synced."""
        try:
            if self._backup_running or self._backup_scanning \
                    or self._heic_converting:
                return
            if self._reorganize_active \
                    or time.time() < self._reorganize_block_until:
                return
            if not (self.settings.get("backup_enabled")
                    and self.settings.get("backup_uuid")
                    and self.settings.get("backup_path")):
                return
            if self._backup_drive_mountpoint() is None:
                return
            # Cooldown: last scan <10min + no new import → skip.
            last_backup = self.settings.get("last_backup_time", 0) or 0
            last_import = self.settings.get("last_import_time", 0) or 0
            if last_backup and (time.time() - last_backup) < 600 \
                    and last_import <= last_backup:
                return
            self._trigger_backup_scan()
        except Exception:
            pass

    def on_convert_switch_toggled(self, switch, _pspec):
        if not switch.get_active():
            def _off():
                self.settings["convert_heic"] = False
                save_settings(self.settings)
                if hasattr(self, "_convert_lib_btn"):
                    self._convert_lib_btn.set_sensitive(False)
            self._confirm_switch_off(
                switch, self.on_convert_switch_toggled,
                _("Turn off HEIC conversion?"),
                _("New imports will be kept as the original HEIC files. HEIC opens fine in Pixora, but can't easily be opened outside it — many other apps and devices don't support it. Photos already converted to JPEG are not changed back."),
                _off)
            return
        self.settings["convert_heic"] = True
        # Turning conversion back on → allow the sweep to re-check this session.
        self._heic_sweep_dismissed = False
        save_settings(self.settings)
        if hasattr(self, "_convert_lib_btn"):
            self._convert_lib_btn.set_sensitive(True)

    def on_video_convert_toggled(self, switch, _pspec):
        if not switch.get_active():
            def _off():
                self.settings["convert_videos"] = False
                save_settings(self.settings)
                if hasattr(self, "_video_lib_btn"):
                    self._video_lib_btn.set_sensitive(False)
            self._confirm_switch_off(
                switch, self.on_video_convert_toggled,
                _("Turn off video conversion?"),
                _("New imports will keep the iPhone's original MOV videos. They play fine in Pixora, but some other apps and devices can't open them. Videos already converted to MP4 are not changed back."),
                _off)
            return
        self.settings["convert_videos"] = True
        # Turning conversion back on → allow the sweep to re-check this session.
        self._video_sweep_dismissed = False
        save_settings(self.settings)
        if hasattr(self, "_video_lib_btn"):
            self._video_lib_btn.set_sensitive(True)

    def _collect_library_videos(self, skip_failed=True):
        """Every .mov/.m4v/.3gp file currently in the photo library."""
        photo_path = self.settings.get("photo_path") or os.path.join(
            os.path.expanduser("~"), "Photos")
        return _collect_library_files(photo_path, (".mov", ".m4v", ".3gp"),
                                      skip_failed)

    def _on_convert_videos_library_clicked(self, btn):
        if self._heic_converting:   # shared 'library conversion busy' flag
            return

        def _scan():
            # "Run now" retries files that failed before.
            files = self._collect_library_videos(skip_failed=False)
            GLib.idle_add(self._start_video_library_conversion, files, False)
        threading.Thread(target=_scan, daemon=True).start()

    def _start_video_library_conversion(self, files, silent=False):
        if self._heic_converting or not files:
            return False
        if self._reorganize_moving or self._backup_running:
            return False  # files are being moved/copied right now
        self._heic_converting = True
        self._conversion_kind = "video"
        self._heic_fraction = 0.0
        self._heic_detail = ""
        if hasattr(self, "_video_lib_btn"):
            self._video_lib_btn.set_sensitive(False)
            self._video_lib_btn.set_label(_("Converting…"))
        tip = _("Converting videos to MP4…")
        if hasattr(self, "_backup_donut_btn"):
            self._set_donuts_visible(True)
            self._backup_donut_btn.set_tooltip_text(tip)
        if hasattr(self, "_viewer_donut_btn"):
            self._viewer_donut_btn.set_tooltip_text(tip)
        self._redraw_donuts()
        log_info("Video library conversion started: {} files".format(len(files)))
        threading.Thread(
            target=self._convert_video_library_thread,
            args=(files, silent), daemon=True).start()
        return False

    def _convert_video_library_thread(self, files, silent=False):
        from importer_page import convert_video, unique_path
        total = len(files)
        done = converted = failed = 0
        renames, failed_paths = [], []
        for fp in files:
            try:
                ts = fp.stat().st_mtime
            except OSError:
                ts = None
            dst = unique_path(fp.with_suffix(".mp4"))
            ok = False
            try:
                ok = convert_video(fp, dst)
            except Exception:
                ok = False
            if ok and dst.exists():
                if ts is not None:
                    try:
                        os.utime(dst, (ts, ts))
                    except OSError:
                        pass
                try:
                    fp.unlink()
                except OSError:
                    pass
                renames.append((str(fp), str(dst)))
                originals.rekey(str(fp), str(dst))
                converted += 1
            else:
                try:
                    if dst.exists():
                        dst.unlink()
                except OSError:
                    pass
                failed += 1
                failed_paths.append(str(fp))
            done += 1
            GLib.idle_add(self._set_heic_progress, done, total, fp.name)
        _remember_failed_conversions(failed_paths)
        if renames:
            GLib.idle_add(self._follow_renames, renames)
        GLib.idle_add(self._finish_video_library_conversion, converted, failed, silent)

    def _finish_video_library_conversion(self, converted, failed, silent=False):
        self._heic_converting = False
        self._heic_fraction = 0.0
        self._heic_detail = ""
        if hasattr(self, "_video_lib_btn"):
            self._video_lib_btn.set_sensitive(True)
            self._video_lib_btn.set_label(_("Run now"))
        if hasattr(self, "_backup_donut_btn"):
            self._set_donuts_visible(self._backup_running)
        self._redraw_donuts()
        log_info("Video library conversion done: {} converted, {} failed".format(
            converted, failed))
        try:
            self.reload_photos()
        except Exception:
            pass
        if not silent and (converted + failed) > 0:
            if failed:
                body = _("Converted %(c)d of %(t)d videos. %(f)d could not be converted and were left as-is.") % {
                    "c": converted, "t": converted + failed, "f": failed}
            else:
                body = ngettext(
                    "Converted %d video to MP4.",
                    "Converted %d videos to MP4.",
                    converted) % converted
            dlg = Adw.AlertDialog(heading=_("Conversion complete"), body=body)
            dlg.add_response("ok", _("OK"))
            dlg.set_default_response("ok")
            dlg.set_close_response("ok")
            self._present_dialog(dlg)
        return False

    def _on_auto_confirm_toggle(self, switch, _pspec):
        """One master switch drives the per-task silent flags, so the existing
        backup/structure logic keeps reading its own keys unchanged."""
        val = bool(switch.get_active())
        self.settings["auto_confirm"] = val
        self.settings["reorganize_silent"] = val
        self.settings["backup_silent"] = val
        save_settings(self.settings)

    def _recommended_badge(self):
        """Small blue accent pill marking the recommended choice on an option."""
        badge = Gtk.Label(label=_("Recommended"))
        badge.set_valign(Gtk.Align.CENTER)
        badge.add_css_class("recommended-badge")
        css = Gtk.CssProvider()
        css.load_from_string(
            ".recommended-badge {"
            " background-color: @accent_bg_color;"
            " color: @accent_fg_color;"
            " border-radius: 9999px;"
            " padding: 1px 9px;"
            " font-size: 0.72em;"
            " font-weight: 800;"
            " letter-spacing: 0.4px;"
            "}"
        )
        badge.get_style_context().add_provider(
            css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        return badge

    # ── HEIC → JPEG library conversion ───────────────────────────────
    def _collect_library_heics(self, skip_failed=True):
        """Every .heic/.heif file currently in the photo library."""
        photo_path = self.settings.get("photo_path") or os.path.join(
            os.path.expanduser("~"), "Photos")
        return _collect_library_files(photo_path, (".heic", ".heif"), skip_failed)

    def _on_convert_library_clicked(self, btn):
        # No confirmation popup (same feel as the update button): just start,
        # with the donut showing progress. Scan off-thread to keep the UI snappy.
        if self._heic_converting:
            return
        self._heic_sweep_dismissed = False

        def _scan():
            files = self._collect_library_heics(skip_failed=False)
            GLib.idle_add(self._start_library_conversion, files, True)
        threading.Thread(target=_scan, daemon=True).start()

    def _maybe_run_heic_sweep(self):
        """Periodic/startup sweep: convert leftover library HEICs. Defers while
        other maintenance runs; prompts first unless auto-confirm is on."""
        if (self._heic_converting or self._heic_sweep_dismissed
                or self._structure_scanning or self._backup_scanning
                or self._backup_running or self._reorganize_active
                or self._importer_open()):
            return False
        if not self.settings.get("convert_heic", False):
            return False
        threading.Thread(
            target=self._heic_sweep_scan_thread, daemon=True).start()
        return False

    def _heic_sweep_scan_thread(self):
        files = self._collect_library_heics()
        GLib.idle_add(self._on_heic_sweep_scanned, files)

    def _on_heic_sweep_scanned(self, files):
        if not files:
            # Clean — stop walking the library every cycle this session.
            self._heic_sweep_dismissed = True
            return False
        if self._heic_converting:
            return False
        if self.settings.get("auto_confirm"):
            self._start_library_conversion(files, silent=True)
            return False
        n = len(files)
        dlg = Adw.AlertDialog(
            heading=_("Convert HEIC photos?"),
            body=ngettext(
                "Pixora found %d HEIC photo to convert to JPEG. Convert it now?",
                "Pixora found %d HEIC photos to convert to JPEG. Convert them now?",
                n) % n)
        dlg.add_response("later", _("Later"))
        dlg.add_response("go", _("Convert"))
        dlg.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("go")
        dlg.set_close_response("later")

        def _resp(d, r):
            if r == "go":
                self._start_library_conversion(files, silent=False)
            else:
                self._heic_sweep_dismissed = True  # don't nag again this session
        dlg.connect("response", _resp)
        self._present_dialog(dlg)
        return False

    def _maybe_run_video_sweep(self):
        """Periodic/startup sweep: convert leftover library videos to MP4. Defers
        while other maintenance runs; prompts first unless auto-confirm is on."""
        if (self._heic_converting or self._video_sweep_dismissed
                or self._structure_scanning or self._backup_scanning
                or self._backup_running or self._reorganize_active
                or self._importer_open()):
            return False
        if not self.settings.get("convert_videos", False):
            return False
        threading.Thread(
            target=self._video_sweep_scan_thread, daemon=True).start()
        return False

    def _video_sweep_scan_thread(self):
        files = self._collect_library_videos()
        GLib.idle_add(self._on_video_sweep_scanned, files)

    def _on_video_sweep_scanned(self, files):
        if not files:
            # Clean — stop walking the library every cycle this session.
            self._video_sweep_dismissed = True
            return False
        if self._heic_converting:
            return False
        if self.settings.get("auto_confirm"):
            self._start_video_library_conversion(files, silent=True)
            return False
        n = len(files)
        dlg = Adw.AlertDialog(
            heading=_("Convert videos?"),
            body=ngettext(
                "Pixora found %d video to convert to MP4. Convert it now?",
                "Pixora found %d videos to convert to MP4. Convert them now?",
                n) % n)
        dlg.add_response("later", _("Later"))
        dlg.add_response("go", _("Convert"))
        dlg.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("go")
        dlg.set_close_response("later")

        def _resp(d, r):
            if r == "go":
                self._start_video_library_conversion(files, silent=False)
            else:
                self._video_sweep_dismissed = True  # don't nag again this session
        dlg.connect("response", _resp)
        self._present_dialog(dlg)
        return False

    def _start_library_conversion(self, files, silent=False):
        if self._heic_converting or not files:
            return False
        if self._reorganize_moving or self._backup_running:
            return False  # files are being moved/copied right now
        self._heic_converting = True
        self._conversion_kind = "heic"
        self._heic_fraction = 0.0
        self._heic_detail = ""
        if hasattr(self, "_convert_lib_btn"):
            self._convert_lib_btn.set_sensitive(False)
            self._convert_lib_btn.set_label(_("Converting…"))
        tip = _("Converting HEIC to JPEG…")
        if hasattr(self, "_backup_donut_btn"):
            self._set_donuts_visible(True)
            self._backup_donut_btn.set_tooltip_text(tip)
        if hasattr(self, "_viewer_donut_btn"):
            self._viewer_donut_btn.set_tooltip_text(tip)
        self._redraw_donuts()
        log_info(_("HEIC library conversion started: {n} files").format(n=len(files)))
        threading.Thread(
            target=self._convert_library_thread,
            args=(files, silent), daemon=True).start()
        return False

    def _set_heic_progress(self, done, total, name):
        self._heic_fraction = (done / total) if total else 0.0
        self._heic_detail = name or ""
        if hasattr(self, "_backup_donut"):
            self._redraw_donuts()
        self._refresh_donut_popover()
        return False

    def _convert_library_thread(self, files, silent):
        from importer_page import convert_image, unique_path
        total = len(files)
        done = converted = failed = 0
        renames, failed_paths = [], []
        for fp in files:
            try:
                ts = fp.stat().st_mtime
            except OSError:
                ts = None
            # Don't clobber an existing JPEG that shares the stem.
            dst = unique_path(fp.with_suffix(".jpg"))
            ok = False
            try:
                ok = convert_image(fp, dst, "jpeg")
            except Exception:
                ok = False
            if ok and dst.exists():
                if ts is not None:
                    try:
                        os.utime(dst, (ts, ts))
                    except OSError:
                        pass
                try:
                    fp.unlink()  # replace the original, per user's choice
                except OSError:
                    pass
                renames.append((str(fp), str(dst)))
                originals.rekey(str(fp), str(dst))
                converted += 1
            else:
                try:
                    if dst.exists():
                        dst.unlink()
                except OSError:
                    pass
                failed += 1
                failed_paths.append(str(fp))
            done += 1
            GLib.idle_add(self._set_heic_progress, done, total, fp.name)
        _remember_failed_conversions(failed_paths)
        if renames:
            GLib.idle_add(self._follow_renames, renames)
        GLib.idle_add(self._finish_library_conversion, converted, failed, silent)

    def _finish_library_conversion(self, converted, failed, silent):
        self._heic_converting = False
        self._heic_fraction = 0.0
        self._heic_detail = ""
        if hasattr(self, "_convert_lib_btn"):
            self._convert_lib_btn.set_sensitive(True)
            self._convert_lib_btn.set_label(_("Run now"))
        if hasattr(self, "_backup_donut_btn"):
            self._set_donuts_visible(self._backup_running)
        self._redraw_donuts()
        log_info(_("HEIC library conversion done: {c} converted, {f} failed").format(
            c=converted, f=failed))
        try:
            self.reload_photos()
        except Exception:
            pass
        if not silent and (converted + failed) > 0:
            if failed:
                body = _("Converted %(c)d of %(t)d photos. %(f)d could not be converted and were left as HEIC.") % {
                    "c": converted, "t": converted + failed, "f": failed}
            else:
                body = ngettext(
                    "Converted %d photo to JPEG.",
                    "Converted %d photos to JPEG.",
                    converted) % converted
            dlg = Adw.AlertDialog(heading=_("Conversion complete"), body=body)
            dlg.add_response("ok", _("OK"))
            dlg.set_default_response("ok")
            dlg.set_close_response("ok")
            self._present_dialog(dlg)
        return False

    def on_dup_switch_toggled(self, switch, _pspec):
        # On = strict (1), Off = 0.
        if not switch.get_active():
            self._confirm_switch_off(
                switch, self.on_dup_switch_toggled,
                _("Turn off duplicate detection?"),
                _("During import Pixora will no longer warn you about photos you already have, so the same photo can be imported twice. Photos already in your library are not affected."),
                lambda: self._apply_dup_detection(False))
            return
        self._apply_dup_detection(True)

    def _apply_dup_detection(self, active):
        self.settings["duplicate_threshold"] = 1 if active else 0
        if not active and self.settings.get("backup_dedup"):
            # Main detection off → USB-dedup off too.
            self.settings["backup_dedup"] = False
            if hasattr(self, "settings_dedup_switch"):
                self.settings_dedup_switch.set_active(False)
        save_settings(self.settings)
        if hasattr(self, "settings_dedup_row"):
            backup_on = bool(self.settings.get("backup_enabled"))
            drive_present = self._backup_drive_mountpoint() is not None
            self.settings_dedup_row.set_sensitive(backup_on and drive_present and active)
        if hasattr(self, "_dedup_btn"):
            self._dedup_btn.set_sensitive(active)
