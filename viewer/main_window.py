#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — main_window.py
#  by LinuxGinger
# ─────────────────────────────────────────────

import os
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, GdkPixbuf, Gio


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, settings):
        super().__init__(application=app)
        self.settings = settings
        self.photos = []
        self.current_index = 0

        self.set_title("Pixora")
        self.set_default_size(1100, 700)

        # Hoofd stack — grid / viewer
        self.main_stack = Gtk.Stack()
        self.main_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.main_stack.set_transition_duration(200)

        self.main_stack.add_named(self.build_grid_page(), "grid")
        self.main_stack.add_named(self.build_viewer_page(), "viewer")

        # Toolbar view
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(self.build_header())
        toolbar_view.set_content(self.main_stack)
        toolbar_view.add_bottom_bar(self.build_bottombar())

        self.set_content(toolbar_view)
        GLib.idle_add(self.load_photos)

    # ── Header ──────────────────────────────────────────────────────
    def build_header(self):
        header = Adw.HeaderBar()
        header.add_css_class("flat")

        # Logo
        logo_path = os.path.join(
            os.path.dirname(__file__), "..", "docs", "pixora-logo-dark.png"
        )
        if os.path.exists(logo_path):
            logo = Gtk.Picture.new_for_filename(logo_path)
            logo.set_size_request(160, 40)
            logo.set_content_fit(Gtk.ContentFit.CONTAIN)
            header.set_title_widget(logo)
        else:
            header.set_title_widget(Gtk.Label(label="Pixora"))

        # Sorteer dropdown
        self.sort_model = Gtk.StringList()
        for item in [
            "Datum (nieuwste eerst)",
            "Datum (oudste eerst)",
            "Naam (A-Z)",
            "Naam (Z-A)",
        ]:
            self.sort_model.append(item)

        self.sort_combo = Gtk.DropDown(model=self.sort_model)
        self.sort_combo.set_size_request(200, -1)
        self.sort_combo.connect("notify::selected", self.on_sort_changed)
        header.pack_start(self.sort_combo)

        # Instellingen knop
        settings_btn = Gtk.Button(icon_name="open-menu-symbolic")
        settings_btn.add_css_class("flat")
        settings_btn.set_tooltip_text("Instellingen")
        settings_btn.connect("clicked", self.on_settings_clicked)
        header.pack_end(settings_btn)

        return header

    # ── Foto grid pagina ─────────────────────────────────────────────
    def build_grid_page(self):
        self.grid_overlay = Gtk.Overlay()

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        self.flow_box = Gtk.FlowBox()
        self.flow_box.set_valign(Gtk.Align.START)
        self.flow_box.set_max_children_per_line(10)
        self.flow_box.set_min_children_per_line(2)
        self.flow_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flow_box.set_row_spacing(6)
        self.flow_box.set_column_spacing(6)
        self.flow_box.set_margin_top(12)
        self.flow_box.set_margin_bottom(12)
        self.flow_box.set_margin_start(12)
        self.flow_box.set_margin_end(12)

        scroll.set_child(self.flow_box)
        self.grid_overlay.set_child(scroll)

        # Laadspinner overlay
        self.spinner_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=16
        )
        self.spinner_box.set_halign(Gtk.Align.CENTER)
        self.spinner_box.set_valign(Gtk.Align.CENTER)

        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(48, 48)
        self.spinner.start()

        self.spinner_label = Gtk.Label(label="Foto's laden...")
        self.spinner_label.add_css_class("dim-label")

        self.spinner_box.append(self.spinner)
        self.spinner_box.append(self.spinner_label)
        self.grid_overlay.add_overlay(self.spinner_box)

        return self.grid_overlay

    # ── Foto viewer pagina ───────────────────────────────────────────
    def build_viewer_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Viewer header
        viewer_header = Adw.HeaderBar()
        viewer_header.add_css_class("flat")

        back_btn = Gtk.Button(icon_name="go-previous-symbolic")
        back_btn.add_css_class("flat")
        back_btn.set_tooltip_text("Terug naar overzicht")
        back_btn.connect("clicked", self.close_viewer)
        viewer_header.pack_start(back_btn)

        self.viewer_title = Gtk.Label(label="")
        self.viewer_title.add_css_class("title")
        viewer_header.set_title_widget(self.viewer_title)

        box.append(viewer_header)

        # Foto met pijltjes
        viewer_area = Gtk.Overlay()
        viewer_area.set_vexpand(True)

        self.photo_picture = Gtk.Picture()
        self.photo_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.photo_picture.set_vexpand(True)
        self.photo_picture.set_hexpand(True)
        viewer_area.set_child(self.photo_picture)

        # Pijl links
        self.prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.prev_btn.add_css_class("osd")
        self.prev_btn.add_css_class("circular")
        self.prev_btn.set_halign(Gtk.Align.START)
        self.prev_btn.set_valign(Gtk.Align.CENTER)
        self.prev_btn.set_margin_start(16)
        self.prev_btn.set_size_request(48, 48)
        self.prev_btn.connect("clicked", self.prev_photo)
        viewer_area.add_overlay(self.prev_btn)

        # Pijl rechts
        self.next_btn = Gtk.Button(icon_name="go-next-symbolic")
        self.next_btn.add_css_class("osd")
        self.next_btn.add_css_class("circular")
        self.next_btn.set_halign(Gtk.Align.END)
        self.next_btn.set_valign(Gtk.Align.CENTER)
        self.next_btn.set_margin_end(16)
        self.next_btn.set_size_request(48, 48)
        self.next_btn.connect("clicked", self.next_photo)
        viewer_area.add_overlay(self.next_btn)

        # Counter
        self.viewer_counter = Gtk.Label(label="")
        self.viewer_counter.add_css_class("osd")
        self.viewer_counter.set_halign(Gtk.Align.CENTER)
        self.viewer_counter.set_valign(Gtk.Align.END)
        self.viewer_counter.set_margin_bottom(12)
        viewer_area.add_overlay(self.viewer_counter)

        box.append(viewer_area)
        return box

    # ── Onderste balk ────────────────────────────────────────────────
    def build_bottombar(self):
        bar = Gtk.ActionBar()

        self.photo_count_label = Gtk.Label(label="0 foto's")
        self.photo_count_label.add_css_class("dim-label")
        bar.pack_start(self.photo_count_label)

        import_btn = Gtk.Button(label="📱  Importeer van iPhone")
        import_btn.add_css_class("suggested-action")
        import_btn.add_css_class("pill")
        import_btn.connect("clicked", self.open_importer)
        bar.pack_end(import_btn)

        return bar

    # ── Foto's laden ─────────────────────────────────────────────────
    def load_photos(self):
        photo_path = self.settings.get("photo_path", "")
        if not os.path.exists(photo_path):
            self.show_empty_state()
            return False

        extensions = {".jpg", ".jpeg", ".png", ".heic", ".mp4", ".mov"}
        self.photos = []

        for root, dirs, files in os.walk(photo_path):
            for file in files:
                if os.path.splitext(file)[1].lower() in extensions:
                    self.photos.append(os.path.join(root, file))

        if not self.photos:
            self.show_empty_state()
            return False

        self.apply_sort()
        self.spinner_box.set_visible(True)
        self.spinner.start()

        thread = threading.Thread(target=self.load_thumbnails, daemon=True)
        thread.start()
        return False

    def load_thumbnails(self):
        total = len(self.photos)
        for i, path in enumerate(self.photos):
            GLib.idle_add(
                self.spinner_label.set_text,
                f"Foto's laden... {i + 1} / {total}"
            )
            GLib.idle_add(self.add_thumbnail, i, path)
        GLib.idle_add(self.on_loading_done, total)

    def add_thumbnail(self, index, path):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 160, 160, True)
            picture = Gtk.Picture.new_for_pixbuf(pixbuf)
            picture.set_size_request(160, 160)
            picture.set_content_fit(Gtk.ContentFit.COVER)
        except Exception:
            picture = Gtk.Picture()
            picture.set_size_request(160, 160)

        btn = Gtk.Button()
        btn.set_child(picture)
        btn.add_css_class("flat")
        btn.set_overflow(Gtk.Overflow.HIDDEN)

        idx = index
        btn.connect("clicked", lambda b: self.open_photo(idx))
        self.flow_box.append(btn)
        return False

    def on_loading_done(self, total):
        self.spinner.stop()
        self.spinner_box.set_visible(False)
        self.photo_count_label.set_text(f"{total} foto's")
        return False

    def show_empty_state(self):
        self.spinner.stop()
        self.spinner_box.set_visible(False)

        status = Adw.StatusPage()
        status.set_icon_name("image-missing-symbolic")
        status.set_title("Geen foto's gevonden")
        status.set_description("Sluit je iPhone aan om foto's te importeren")
        status.set_vexpand(True)

        self.flow_box.append(status)
        self.photo_count_label.set_text("0 foto's")

    # ── Sorteren ─────────────────────────────────────────────────────
    def apply_sort(self):
        index = self.sort_combo.get_selected()
        if index == 0:
            self.photos.sort(key=os.path.getmtime, reverse=True)
        elif index == 1:
            self.photos.sort(key=os.path.getmtime)
        elif index == 2:
            self.photos.sort(key=lambda p: os.path.basename(p).lower())
        elif index == 3:
            self.photos.sort(key=lambda p: os.path.basename(p).lower(), reverse=True)

    def on_sort_changed(self, combo, _):
        if not self.photos:
            return
        self.apply_sort()
        while True:
            child = self.flow_box.get_first_child()
            if child is None:
                break
            self.flow_box.remove(child)
        self.spinner_box.set_visible(True)
        self.spinner.start()
        thread = threading.Thread(target=self.load_thumbnails, daemon=True)
        thread.start()

    # ── Foto viewer ──────────────────────────────────────────────────
    def open_photo(self, index):
        self.current_index = index
        self.update_viewer()
        self.main_stack.set_visible_child_name("viewer")

    def update_viewer(self):
        path = self.photos[self.current_index]
        self.photo_picture.set_filename(path)
        self.viewer_title.set_text(os.path.basename(path))
        self.viewer_counter.set_text(
            f"{self.current_index + 1} / {len(self.photos)}"
        )
        self.prev_btn.set_sensitive(self.current_index > 0)
        self.next_btn.set_sensitive(self.current_index < len(self.photos) - 1)

    def prev_photo(self, btn):
        if self.current_index > 0:
            self.current_index -= 1
            self.update_viewer()

    def next_photo(self, btn):
        if self.current_index < len(self.photos) - 1:
            self.current_index += 1
            self.update_viewer()

    def close_viewer(self, btn):
        self.main_stack.set_visible_child_name("grid")

    # ── Instellingen ─────────────────────────────────────────────────
    def on_settings_clicked(self, btn):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Instellingen",
            body="Instellingen komen in een volgende versie."
        )
        dialog.add_response("ok", "OK")
        dialog.present()

    # ── Importer ─────────────────────────────────────────────────────
    def open_importer(self, btn):
        import subprocess
        import sys
        importer_path = os.path.join(
            os.path.dirname(__file__), "..", "importer", "main.py"
        )
        if os.path.exists(importer_path):
            subprocess.Popen([sys.executable, importer_path])
        else:
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading="Importer niet gevonden",
                body="De Pixora Importer is niet geïnstalleerd.\nInstalleer hem via de instellingen."
            )
            dialog.add_response("ok", "OK")
            dialog.present()