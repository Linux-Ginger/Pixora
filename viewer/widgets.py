#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — widgets.py
#  Small reusable widgets: folder watcher, backup folder picker
#  by LinuxGinger
# ─────────────────────────────────────────────

import os
import threading
from pixora_i18n import _
from common import Adw, FileSystemEventHandler, GLib, Gtk, IMAGE_EXTENSIONS


class PhotoFolderHandler(FileSystemEventHandler):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self._timer   = None

    def _schedule_reload(self):
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(1.5, lambda: GLib.idle_add(self.callback))
        self._timer.start()

    def on_created(self, event):
        if not event.is_directory and os.path.splitext(event.src_path)[1].lower() in IMAGE_EXTENSIONS:
            self._schedule_reload()

    def on_deleted(self, event):
        if not event.is_directory and os.path.splitext(event.src_path)[1].lower() in IMAGE_EXTENSIONS:
            self._schedule_reload()

    def on_moved(self, event):
        self._schedule_reload()


class BackupFolderPicker(Adw.Dialog):
    """Folder picker confined to the USB root, with inline create-subfolder."""

    def __init__(self, mountpoint, current_path, on_selected):
        super().__init__()
        from pathlib import Path as _P
        self._root = _P(mountpoint).resolve()
        self._on_selected = on_selected
        try:
            start = _P(current_path).resolve() if current_path else self._root
            start.relative_to(self._root)
            if not start.is_dir():
                start = self._root
        except Exception:
            start = self._root
        self._cursor = start

        self.set_title(_("Choose backup folder"))
        self.set_content_width(480)
        self.set_content_height(520)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8,
            margin_start=12, margin_end=12, margin_top=12, margin_bottom=12,
        )

        self._path_label = Gtk.Label(xalign=0)
        self._path_label.add_css_class("dim-label")
        self._path_label.set_wrap(True)
        box.append(self._path_label)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._listbox = Gtk.ListBox()
        self._listbox.add_css_class("boxed-list")
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll.set_child(self._listbox)
        box.append(scroll)

        new_btn = Gtk.Button(label=_("New folder"))
        new_btn.set_icon_name("folder-new-symbolic")
        new_btn.connect("clicked", self._on_new_folder_clicked)
        box.append(new_btn)

        actions = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
            halign=Gtk.Align.END,
        )
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda *_a: self.close())
        use = Gtk.Button(label=_("Use this folder"))
        use.add_css_class("suggested-action")
        use.connect("clicked", self._on_use_clicked)
        actions.append(cancel)
        actions.append(use)
        box.append(actions)

        toolbar.set_content(box)
        self.set_child(toolbar)
        self._refresh()

    def _refresh(self):
        from pathlib import Path as _P
        try:
            rel = self._cursor.relative_to(self._root)
        except ValueError:
            self._cursor = self._root
            rel = _P(".")
        if str(rel) == ".":
            self._path_label.set_label(_("Root of USB drive"))
        else:
            self._path_label.set_label(f"/{rel}")

        while True:
            row = self._listbox.get_first_child()
            if row is None:
                break
            self._listbox.remove(row)

        if self._cursor != self._root:
            up = Adw.ActionRow(title=_("Back"))
            up.add_prefix(Gtk.Image.new_from_icon_name("go-up-symbolic"))
            up.set_activatable(True)
            up.connect("activated", lambda *_a: self._navigate(self._cursor.parent))
            self._listbox.append(up)

        try:
            entries = sorted(
                (e for e in self._cursor.iterdir()
                 if e.is_dir() and not e.name.startswith(".")),
                key=lambda p: p.name.lower(),
            )
        except Exception:
            entries = []

        if not entries and self._cursor == self._root:
            empty = Adw.ActionRow(
                title=_("No subfolders"),
                subtitle=_("Create one with the button below"),
            )
            empty.set_sensitive(False)
            self._listbox.append(empty)
        else:
            for e in entries:
                row = Adw.ActionRow(title=e.name)
                row.add_prefix(Gtk.Image.new_from_icon_name("folder-symbolic"))
                row.set_activatable(True)
                row.connect("activated", lambda _r, p=e: self._navigate(p))
                self._listbox.append(row)

    def _navigate(self, path):
        try:
            resolved = path.resolve()
            resolved.relative_to(self._root)
        except Exception:
            return
        if not resolved.is_dir():
            return
        self._cursor = resolved
        self._refresh()

    def _on_new_folder_clicked(self, btn):
        dlg = Adw.AlertDialog(
            heading=_("New folder"),
            body=_("Enter a name for the new folder:"),
        )
        entry = Gtk.Entry()
        entry.set_placeholder_text(_("Folder name"))
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("create", _("Create"))
        dlg.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("create")
        dlg.set_close_response("cancel")
        entry.connect("activate", lambda *_a: dlg.response("create"))
        dlg.connect("response", self._on_create_response, entry)
        dlg.present(self)

    def _on_create_response(self, dlg, response, entry):
        if response != "create":
            return
        name = entry.get_text().strip()
        if not name or "/" in name or name in (".", ".."):
            return
        new_path = self._cursor / name
        try:
            new_path.mkdir(parents=False, exist_ok=True)
        except Exception as exc:
            err = Adw.AlertDialog(
                heading=_("Could not create folder"),
                body=str(exc),
            )
            err.add_response("ok", _("OK"))
            err.present(self)
            return
        self._cursor = new_path
        self._refresh()

    def _on_use_clicked(self, btn):
        try:
            self._cursor.relative_to(self._root)
        except ValueError:
            return
        self._on_selected(str(self._cursor))
        self.close()
