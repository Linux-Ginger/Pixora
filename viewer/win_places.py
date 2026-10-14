#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — win_places.py
#  Home and saved places
#  by LinuxGinger
# ─────────────────────────────────────────────

import threading
from pixora_i18n import _
from common import Adw, GLib, Gtk, log_error, save_settings, _lang
from metadata import (COUNTRIES, geocode_address, _LOCALE_COUNTRY, _mk,
    _titlecase)


class PlacesMixin:
    """Home and saved places (mixed into MainWindow)."""

    def _accent_badge(self, text):
        """Small blue accent pill (same look as the 'Recommended' badge)."""
        badge = Gtk.Label(label=text)
        badge.set_valign(Gtk.Align.CENTER)
        badge.add_css_class("recommended-badge")
        css = Gtk.CssProvider()
        css.load_from_string(
            ".recommended-badge { background-color: @accent_bg_color;"
            " color: @accent_fg_color; border-radius: 9999px; padding: 1px 9px;"
            " font-size: 0.72em; font-weight: 800; letter-spacing: 0.4px; }")
        badge.get_style_context().add_provider(
            css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        return badge

    def _on_remove_home(self, btn=None):
        if self.settings.get("home_lat") is None:
            return
        dlg = Adw.MessageDialog(
            transient_for=self,
            heading=_("Remove your main home?"),
            body=_("Your main home will be removed. Your photos are not affected."))
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("remove", _("Remove"))
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.connect("response", self._do_remove_home)
        dlg.present()

    def _do_remove_home(self, dlg, response):
        if response != "remove":
            return
        for k in ("home_lat", "home_lon", "home_zoom", "home_country",
                  "home_city", "home_postcode", "home_street", "home_addition"):
            self.settings.pop(k, None)
        try:
            save_settings(self.settings)
        except Exception as e:
            log_error(_("Could not remove home: {err}").format(err=e))
        self._rebuild_places_group()
        if self._map_widget:
            self._map_widget.set_home(None, None)

    def _home_address_text(self):
        parts = []
        street = " ".join(x for x in (
            self.settings.get('home_street', ''),
            self.settings.get('home_house_number', ''),
            self.settings.get('home_addition', ''),
        ) if x).strip()
        if street:
            parts.append(street)
        pc_city = (f"{self.settings.get('home_postcode','')} "
                   f"{self.settings.get('home_city','')}").strip()
        if pc_city:
            parts.append(pc_city)
        country = self.settings.get("home_country", "")
        if country:
            parts.append(country)
        return ", ".join(parts) if parts else _("Not set")

    def _place_address_text(self, p):
        parts = []
        street = " ".join(x for x in (
            p.get('street', ''), p.get('house_number', ''), p.get('addition', '')
        ) if x).strip()
        if street:
            parts.append(street)
        pc_city = f"{p.get('postcode','')} {p.get('city','')}".strip()
        if pc_city:
            parts.append(pc_city)
        if p.get("country"):
            parts.append(p["country"])
        return ", ".join(parts) if parts else _("Not set")

    def _rebuild_places_group(self):
        grp = getattr(self, "_places_group", None)
        if grp is None:
            return
        for row in getattr(self, "_place_rows", []):
            try:
                grp.remove(row)
            except Exception:
                pass
        self._place_rows = []

        def _suffix_btn(label, cb):
            b = Gtk.Button(label=label)
            b.add_css_class("flat")
            b.set_valign(Gtk.Align.CENTER)
            b.connect("clicked", cb)
            return b

        def _verified_check():
            # Green check = address was found/geocoded (verified).
            ic = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            ic.add_css_class("success")
            ic.set_tooltip_text(_("Address verified"))
            return ic

        # Main home (priority) — flagged with a 'Main home' accent badge.
        home_set = self.settings.get("home_lat") is not None
        main_row = Adw.ActionRow(title=_("My home"))
        main_row.set_subtitle(self._home_address_text())
        main_row.add_suffix(self._accent_badge(_("Main home")))
        if home_set:
            main_row.add_prefix(_verified_check())
            rm = _suffix_btn(_("Remove"), self._on_remove_home)
            main_row.add_suffix(rm)
            main_row.add_suffix(_suffix_btn(
                _("Change"), lambda b: self._open_place_dialog(None)))
            self._remove_home_btn = rm
        else:
            # Nothing to remove or change yet — just an Add action.
            main_row.add_suffix(_suffix_btn(
                _("Add"), lambda b: self._open_place_dialog(None)))
            self._remove_home_btn = None
        grp.add(main_row)
        self._place_rows.append(main_row)
        self.home_row = main_row

        # Extra named places.
        for i, p in enumerate(self.settings.get("extra_places", [])):
            row = Adw.ActionRow(title=_mk(p.get("name") or _("Place")))
            row.set_subtitle(_mk(self._place_address_text(p)))
            row.add_prefix(_verified_check())
            row.add_suffix(_suffix_btn(
                _("Remove"), lambda b, idx=i: self._on_remove_place(idx)))
            row.add_suffix(_suffix_btn(
                _("Change"), lambda b, idx=i: self._open_place_dialog(idx)))
            grp.add(row)
            self._place_rows.append(row)

        # Add-a-place row.
        add_row = Adw.ActionRow(title=_("Add a place"))
        add_row.set_subtitle(
            _mk(_("e.g. Grandma & Grandpa — labels photos taken there")))
        addb = Gtk.Button(icon_name="list-add-symbolic")
        addb.add_css_class("flat")
        addb.set_valign(Gtk.Align.CENTER)
        addb.connect("clicked", lambda b: self._open_place_dialog("new"))
        add_row.add_suffix(addb)
        add_row.set_activatable_widget(addb)
        grp.add(add_row)
        self._place_rows.append(add_row)

    def _open_home_address_dialog(self):
        # Back-compat shim (map home button) → main home editor.
        self._open_place_dialog(None)

    def _open_place_dialog(self, index=None, prefill=None):
        """index: None = main home, 'new' = add a place, int = edit a place.
        prefill keeps the just-typed fields when reopening after 'Edit'."""
        is_main = index is None
        if prefill is not None:
            data = prefill
        elif is_main:
            data = {"name": "", "country": self.settings.get("home_country"),
                    "city": self.settings.get("home_city", ""),
                    "postcode": self.settings.get("home_postcode", ""),
                    "street": self.settings.get("home_street", ""),
                    "house_number": self.settings.get("home_house_number", ""),
                    "addition": self.settings.get("home_addition", "")}
        elif index == "new":
            data = {"name": "", "country": None, "city": "", "postcode": "",
                    "street": "", "house_number": "", "addition": ""}
        else:
            data = self.settings.get("extra_places", [])[index]

        dlg = Adw.MessageDialog(
            transient_for=self,
            heading=_("My home") if is_main else _("Place"),
            body=_("Fill in your address so it can be looked up exactly. It’s "
                   "looked up once via OpenStreetMap, then stored only on this "
                   "computer."))

        # Three blocks: top (name/country/street), a side-by-side house number +
        # addition pair, then postcode/city — reads cleaner than one long column.
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        top = Adw.PreferencesGroup()
        name_row = None
        if not is_main:
            name_row = Adw.EntryRow(title=_mk(_("Name (e.g. Grandma & Grandpa)")))
            name_row.set_text(data.get("name", ""))
            top.add(name_row)

        country_row = Adw.ComboRow(title=_("Country"))
        model = Gtk.StringList()
        for name in COUNTRIES:
            model.append(name)
        country_row.set_model(model)
        saved_country = data.get("country") or _LOCALE_COUNTRY.get(_lang, "Netherlands")
        if saved_country in COUNTRIES:
            country_row.set_selected(COUNTRIES.index(saved_country))
        top.add(country_row)

        street_row = Adw.EntryRow(title=_("Street"))
        street_row.set_text(data.get("street", ""))
        top.add(street_row)

        # House number + addition side by side (two small cards).
        pair = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                       homogeneous=True)
        hn_group = Adw.PreferencesGroup()
        hn_group.set_hexpand(True)
        housenr_row = Adw.EntryRow(title=_("House number"))
        housenr_row.set_text(data.get("house_number", ""))
        hn_group.add(housenr_row)
        add_group = Adw.PreferencesGroup()
        add_group.set_hexpand(True)
        addition_row = Adw.EntryRow(title=_("Addition (optional)"))
        addition_row.set_text(data.get("addition", ""))
        add_group.add(addition_row)
        pair.append(hn_group)
        pair.append(add_group)

        bottom = Adw.PreferencesGroup()
        postcode_row = Adw.EntryRow(title=_("Postcode"))
        postcode_row.set_text(data.get("postcode", ""))
        bottom.add(postcode_row)
        city_row = Adw.EntryRow(title=_("City"))
        city_row.set_text(data.get("city", ""))
        bottom.add(city_row)

        box.append(top)
        box.append(pair)
        box.append(bottom)
        dlg.set_extra_child(box)
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("save", _("Save"))
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("save")
        dlg.set_close_response("cancel")
        rows = (name_row, country_row, city_row, postcode_row, street_row,
                housenr_row, addition_row)
        dlg.connect("response", self._on_place_response, index, rows)
        dlg.present()

    def _on_place_response(self, dlg, response, index, rows):
        if response != "save":
            return
        (name_row, country_row, city_row, postcode_row, street_row,
         housenr_row, addition_row) = rows
        country = COUNTRIES[country_row.get_selected()]
        # Auto-tidy capitalisation: postcode/addition uppercase, city/street
        # title-cased — so "1234bb" → "1234BB", "rotterdam" → "Rotterdam".
        city = _titlecase(city_row.get_text().strip())
        postcode = postcode_row.get_text().strip().upper()
        street = _titlecase(street_row.get_text().strip())
        house_number = housenr_row.get_text().strip()
        addition = addition_row.get_text().strip().upper()
        name = name_row.get_text().strip() if name_row is not None else ""
        if not street and not city and not postcode:
            return
        # Number + addition form one unit ("12" + "A" → "12A"); keeping them
        # together lets Nominatim resolve the exact address instead of snapping
        # to a neighbouring house (which made the addition look like it changed).
        number = f"{house_number}{addition}".strip()
        full_street = " ".join(x for x in (number, street) if x).strip()
        fields = {"name": name, "country": country, "city": city,
                  "postcode": postcode, "street": street,
                  "house_number": house_number, "addition": addition}

        def _lookup():
            res = geocode_address(street=full_street, city=city,
                                  postcode=postcode, country=country)
            loose = False
            if res is None:
                # Wrong/missing city? Retry without it to suggest a match.
                res = geocode_address(street=full_street, postcode=postcode,
                                      country=country, loose=True)
                loose = res is not None
            GLib.idle_add(self._confirm_place, index, res, fields, loose)

        threading.Thread(target=_lookup, daemon=True).start()

    def _confirm_place(self, index, result, fields, was_loose):
        if not result:
            dlg = Adw.MessageDialog(
                transient_for=self,
                heading=_("Address not found"),
                body=_("Couldn’t find that address. Check the fields and "
                       "try again."))
            dlg.add_response("edit", _("Edit"))
            dlg.add_response("ok", _("OK"))
            dlg.connect("response", lambda d, r: (
                self._open_place_dialog(index, prefill=fields)
                if r == "edit" else None))
            dlg.present()
            return False
        lat, lon, display = result
        heading = _("Did you mean this?") if was_loose else _("Confirm location")
        dlg = Adw.MessageDialog(
            transient_for=self, heading=heading, body=display or "")
        dlg.add_response("edit", _("Edit"))
        dlg.add_response("save", _("Save here"))
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("save")

        def _resp(d, r):
            if r == "save":
                self._save_place(index, (lat, lon), fields)
            elif r == "edit":
                self._open_place_dialog(index, prefill=fields)
        dlg.connect("response", _resp)
        dlg.present()
        return False

    def _save_place(self, index, coords, fields):
        lat, lon = coords
        # Stored only in the local 0600 settings file — never sent anywhere.
        if index is None:
            self.settings["home_lat"] = lat
            self.settings["home_lon"] = lon
            self.settings["home_zoom"] = 16
            self.settings["home_country"] = fields["country"]
            self.settings["home_city"] = fields["city"]
            self.settings["home_postcode"] = fields["postcode"]
            self.settings["home_street"] = fields["street"]
            self.settings["home_house_number"] = fields["house_number"]
            self.settings["home_addition"] = fields["addition"]
            if self._map_widget:
                self._map_widget.set_home(lat, lon)
                self._map_widget.go_home(lat, lon, 16)
        else:
            entry = dict(fields)
            entry["lat"] = lat
            entry["lon"] = lon
            if not entry.get("name"):
                entry["name"] = fields["city"] or _("Place")
            places = self.settings.setdefault("extra_places", [])
            if index == "new":
                places.append(entry)
            else:
                places[index] = entry
        try:
            save_settings(self.settings)
        except Exception as e:
            log_error(_("Could not save place: {err}").format(err=e))
            return False
        self._rebuild_places_group()
        if self._map_widget:
            self._map_widget.set_places(self._map_places_list())
        return False

    def _map_places_list(self):
        return [{"name": p.get("name", ""), "lat": p["lat"], "lon": p["lon"]}
                for p in self.settings.get("extra_places", [])
                if p.get("lat") is not None and p.get("lon") is not None]

    def _on_remove_place(self, index):
        places = self.settings.get("extra_places", [])
        if not (0 <= index < len(places)):
            return
        name = places[index].get("name") or _("this place")
        dlg = Adw.MessageDialog(
            transient_for=self,
            heading=_("Remove place?"),
            body=_("Remove “{name}”? Your photos are not affected.").format(name=name))
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("remove", _("Remove"))
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.connect("response",
                    lambda d, r, idx=index: self._do_remove_place(idx)
                    if r == "remove" else None)
        dlg.present()

    def _do_remove_place(self, index):
        places = self.settings.get("extra_places", [])
        if 0 <= index < len(places):
            places.pop(index)
            try:
                save_settings(self.settings)
            except Exception:
                pass
            self._rebuild_places_group()
            if self._map_widget:
                self._map_widget.set_places(self._map_places_list())
