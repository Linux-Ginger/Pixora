#!/usr/bin/env python3

# ─────────────────────────────────────────────
#  Pixora — installer.py
#  Grafische installer (GTK4/Adwaita)
#  by LinuxGinger
# ─────────────────────────────────────────────

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

import os

# ── i18n ─────────────────────────────────────────────────────────────
import gettext as _gt
import json as _json_i18n
try:
    _lang = _json_i18n.load(open(os.path.expanduser("~/.config/pixora/settings.json"))).get("language", "nl")
except Exception:
    _lang = "nl"
_t = _gt.translation(
    "pixora",
    localedir=os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "locale")),
    languages=[_lang], fallback=True
)
_ = _t.gettext

import sys
import json
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

INSTALL_DIR  = Path.home() / ".local" / "share" / "pixora"
ICON_PATH    = Path(__file__).parent / "assets" / "logos" / "pixora-icon.svg"
BIN_DIR      = Path.home() / ".local" / "bin"
DESKTOP_DIR  = Path.home() / ".local" / "share" / "applications"
REPO_URL     = "https://github.com/Linux-Ginger/Pixora.git"
RELEASES_API = "https://api.github.com/repos/Linux-Ginger/Pixora/releases"


def _ensure_icon_installed():
    """Install the Pixora icon AND a minimal .desktop for the installer
    so GNOME Shell can match the running window to our logo instead of
    the default gear. On Wayland set_icon_name() alone doesn't work —
    Shell resolves the icon by application-id via a .desktop file."""
    try:
        if not ICON_PATH.exists():
            return
        icons_dir = (Path.home() / ".local" / "share" / "icons"
                     / "hicolor" / "scalable" / "apps")
        icons_dir.mkdir(parents=True, exist_ok=True)
        # Two icon names: pixora-icon (generic) + app-id name (GNOME Shell
        # matches this against StartupWMClass of the running window).
        for name in ("pixora-icon.svg",
                     "com.linuxginger.pixora.installer.svg"):
            dest = icons_dir / name
            if (not dest.exists()
                    or dest.stat().st_mtime < ICON_PATH.stat().st_mtime):
                shutil.copy(ICON_PATH, dest)
    except Exception:
        pass
    # Minimal .desktop — NoDisplay hides it from the app-grid; Shell only
    # uses it for window→icon mapping.
    try:
        desktop_dir = Path.home() / ".local" / "share" / "applications"
        desktop_dir.mkdir(parents=True, exist_ok=True)
        desktop_file = desktop_dir / "com.linuxginger.pixora.installer.desktop"
        # Absolute Icon-path is betrouwbaarder dan theme-name lookup —
        # GNOME Shell leest het bestand direct, onafhankelijk van de
        # hicolor-cache state. Geen NoDisplay=true omdat sommige Shell-
        # versies .desktops met NoDisplay=true uit de window-match-map
        # filteren.
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Pixora Installer\n"
            f"Icon={ICON_PATH}\n"
            f"Exec=python3 {Path(__file__).resolve()}\n"
            "Terminal=false\n"
            "StartupWMClass=com.linuxginger.pixora.installer\n"
            "StartupNotify=true\n"
            "NoDisplay=true\n"
            "Categories=System;\n"
        )
        if (not desktop_file.exists()
                or desktop_file.read_text() != content):
            desktop_file.write_text(content)
    except Exception:
        pass


_ensure_icon_installed()

PHASES = [
    (_("Downloading"), [
        (_("Downloading Pixora files"), "clone"),
    ]),
    (_("Installing"), [
        (_("Installing system packages"), "apt"),
        (_("Installing Python packages"),  "pip"),
        (_("Creating launcher"),            "desktop"),
    ]),
    (_("Starting"), [
        (_("Activating services"),           "services"),
        (_("Start Pixora"),               "launch"),
    ]),
]

ALL_STEPS = [(label, key) for _, steps in PHASES for label, key in steps]


class InstallerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title(_("Pixora Installer"))
        # Match the application-id so the installed .desktop resolves
        # this window to our icon (Shell uses app-id for lookup).
        self.set_icon_name("com.linuxginger.pixora.installer")
        self.set_default_size(460, 360)
        self.set_resizable(False)

        self.selected_version = None   # None = main/latest

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        toolbar.add_top_bar(header)

        # Twee schermen: versie-kiezer en installatie-voortgang
        self.main_stack = Gtk.Stack()
        self.main_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.main_stack.set_transition_duration(200)
        self.main_stack.add_named(self._build_select_page(), "select")
        self.main_stack.add_named(self._build_install_page(), "install")

        toolbar.set_content(self.main_stack)
        self.set_content(toolbar)

        # Releases ophalen op achtergrond
        threading.Thread(target=self._fetch_releases, daemon=True).start()

    # ── Versie-kiezer pagina ───────────────────────────────────────────

    def _build_select_page(self):
        version_file = Path.home() / ".config" / "pixora" / "installed_version"
        current_version = None
        if version_file.exists():
            try:
                current_version = version_file.read_text().strip() or None
            except Exception:
                current_version = None
        already_installed = current_version is not None
        self._already_installed = already_installed
        self._local_version = current_version

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroll.set_child(page)

        # Logo / titel
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        top.set_margin_top(20)
        top.set_margin_bottom(16)
        top.set_halign(Gtk.Align.CENTER)

        top.append(self._make_logo(48))

        title = Gtk.Label(label=_("Pixora"))
        title.add_css_class("title-1")
        top.append(title)

        sub = Gtk.Label(label=_("by LinuxGinger"))
        sub.add_css_class("dim-label")
        top.append(sub)

        page.append(top)

        # Versie selectie
        ver_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        ver_box.set_margin_start(24)
        ver_box.set_margin_end(24)
        ver_box.set_margin_top(24)

        ver_lbl = Gtk.Label(label=_("Version"))
        ver_lbl.add_css_class("heading")
        ver_lbl.set_halign(Gtk.Align.START)
        ver_box.append(ver_lbl)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")

        if already_installed:
            self.installed_row = Adw.ActionRow(
                title=_("Pixora is already installed"),
                subtitle=current_version if current_version else ""
            )
            self.installed_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            self.installed_icon.add_css_class("success")
            self.installed_row.add_prefix(self.installed_icon)
            listbox.append(self.installed_row)

        self.version_model = Gtk.StringList()
        self.version_model.append(_("Latest version"))

        self.version_combo = Gtk.DropDown(model=self.version_model)
        self.version_combo.set_size_request(220, -1)
        self.version_combo.set_valign(Gtk.Align.CENTER)

        ver_row = Adw.ActionRow(
            title=_("Choose version"),
            subtitle=_("Select which version to install"))
        ver_row.add_suffix(self.version_combo)
        listbox.append(ver_row)

        ver_box.append(listbox)
        page.append(ver_box)

        # Installeren / bijwerken knop
        btn_box = Gtk.Box()
        btn_box.set_margin_start(24)
        btn_box.set_margin_end(24)
        btn_box.set_margin_top(8)
        btn_box.set_margin_bottom(20)

        btn_label = _("Update") if already_installed else _("Installing")  # bijgewerkt door _fetch_releases
        self.install_btn = Gtk.Button(label=btn_label)
        self.install_btn.add_css_class("suggested-action")
        self.install_btn.add_css_class("pill")
        self.install_btn.set_hexpand(True)
        self.install_btn.set_size_request(-1, 48)
        self.install_btn.connect("clicked", self._on_install_clicked)
        btn_box.append(self.install_btn)

        page.append(btn_box)
        return scroll

    # ── Installatie-voortgang pagina ───────────────────────────────────

    def _build_install_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.set_vexpand(True)

        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        top.set_margin_top(16)
        top.set_margin_bottom(12)
        top.set_halign(Gtk.Align.CENTER)

        top.append(self._make_logo(40))

        title = Gtk.Label(label=_("Pixora"))
        title.add_css_class("title-1")
        top.append(title)

        sub = Gtk.Label(label=_("by LinuxGinger"))
        sub.add_css_class("dim-label")
        top.append(sub)

        page.append(top)

        self.step_rows = {}

        phases_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        phases_box.set_margin_start(24)
        phases_box.set_margin_end(24)

        for phase_label, steps in PHASES:
            phase_lbl = Gtk.Label(label=_(phase_label))
            phase_lbl.add_css_class("heading")
            phase_lbl.set_halign(Gtk.Align.START)
            phase_lbl.set_margin_top(4)
            phases_box.append(phase_lbl)

            listbox = Gtk.ListBox()
            listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            listbox.add_css_class("boxed-list")

            for step_label, key in steps:
                row = Adw.ActionRow()
                row.set_title(_(step_label))

                spinner = Gtk.Spinner()
                spinner.set_size_request(20, 20)

                check = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
                check.set_pixel_size(16)

                stack = Gtk.Stack()
                stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
                stack.set_transition_duration(150)
                stack.add_named(Gtk.Box(), "empty")
                stack.add_named(spinner,   "spinner")
                stack.add_named(check,     "check")
                stack.set_visible_child_name("empty")

                row.add_suffix(stack)
                listbox.append(row)
                self.step_rows[key] = (row, stack, spinner, check)

            phases_box.append(listbox)

        page.append(phases_box)

        self.status_lbl = Gtk.Label(label="")
        self.status_lbl.add_css_class("dim-label")
        self.status_lbl.set_margin_top(16)
        self.status_lbl.set_margin_bottom(8)
        page.append(self.status_lbl)

        self.progress = Gtk.ProgressBar()
        self.progress.set_margin_start(24)
        self.progress.set_margin_end(24)
        self.progress.set_margin_bottom(16)
        page.append(self.progress)

        return page

    # ── Releases ophalen ──────────────────────────────────────────────

    def _make_logo(self, size):
        if ICON_PATH.exists():
            pic = Gtk.Picture.new_for_filename(str(ICON_PATH))
            pic.set_size_request(size, size)
            pic.set_content_fit(Gtk.ContentFit.CONTAIN)
            pic.set_halign(Gtk.Align.CENTER)
            return pic
        img = Gtk.Image.new_from_icon_name("applications-graphics-symbolic")
        img.set_pixel_size(size)
        return img

    def _fetch_releases(self):
        tags = []
        remote_version = None
        try:
            req = urllib.request.Request(
                RELEASES_API,
                headers={"User-Agent": "Pixora-Installer"}
            )
            with urllib.request.urlopen(req, timeout=6) as r:
                releases = json.loads(r.read())
            tags = [rel["tag_name"] for rel in releases if not rel.get("draft")]
        except Exception:
            pass

        try:
            req = urllib.request.Request(
                "https://raw.githubusercontent.com/Linux-Ginger/Pixora/refs/heads/main/version.txt",
                headers={"User-Agent": "Pixora-Installer"}
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                remote_version = r.read().decode().strip()
        except Exception:
            pass

        GLib.idle_add(self._update_version_list, tags)
        if self._already_installed and remote_version:
            GLib.idle_add(self._update_install_status, remote_version)

    def _update_install_status(self, remote_version):
        local = self._local_version or ""
        if local == remote_version:
            self.installed_row.set_title(_("Pixora is already installed"))
            self.installed_row.set_subtitle(_("Version {v} — up to date").format(v=local))
            self.install_btn.set_label(_("Reinstall"))
            self.installed_icon.set_from_icon_name("emblem-ok-symbolic")
            self.installed_icon.remove_css_class("warning")
            self.installed_icon.add_css_class("success")
        else:
            self.installed_row.set_title(_("Update available"))
            self.installed_row.set_subtitle(_("Update available: {local} → {remote}").format(
                local=local, remote=remote_version
            ))
            self.install_btn.set_label(_("Update"))
            self.installed_icon.set_from_icon_name("software-update-urgent-symbolic")
            self.installed_icon.remove_css_class("success")
            self.installed_icon.add_css_class("warning")
        return False

    def _update_version_list(self, tags):
        while self.version_model.get_n_items() > 0:
            self.version_model.remove(0)

        self.version_model.append(_("Latest version"))
        for tag in tags:
            self.version_model.append(tag)

        self.version_combo.set_selected(0)
        return False

    # ── Install starten ───────────────────────────────────────────────

    def _on_install_clicked(self, btn):
        selected = self.version_combo.get_selected()
        if selected == 0:
            self.selected_version = None   # clone main
        else:
            label = self.version_model.get_string(selected)
            self.selected_version = label

        self.main_stack.set_visible_child_name("install")
        GLib.timeout_add(300, self._start_install)

    def _start_install(self):
        threading.Thread(target=self._run_install, daemon=True).start()
        return False

    def _set_step_active(self, key, label):
        row, stack, spinner, check = self.step_rows[key]
        spinner.start()
        stack.set_visible_child_name("spinner")
        idx = next(i for i, (_, k) in enumerate(ALL_STEPS) if k == key)
        self.progress.set_fraction(idx / len(ALL_STEPS))
        self.status_lbl.set_text(_(label) + "…")

    def _set_step_done(self, key):
        row, stack, spinner, check = self.step_rows[key]
        spinner.stop()
        stack.set_visible_child_name("check")

    def _set_error(self, msg):
        self.status_lbl.set_text(_("Error: {err}").format(err=msg))
        self.progress.add_css_class("error")

    def _run_install(self):
        steps = [
            ("clone",    "Downloading Pixora files", self._clone_repo),
            ("apt",      "Installing system packages", self._install_apt),
            ("pip",      "Installing Python packages",  self._install_pip),
            ("desktop",  "Creating launcher",            self._create_launcher),
            ("services", "Activating services",           self._start_services),
            ("launch",   "Start Pixora",               self._launch_app),
        ]
        for key, label, fn in steps:
            GLib.idle_add(self._set_step_active, key, label)
            ok, err = fn()
            if not ok:
                GLib.idle_add(self._set_error, err)
                return
            GLib.idle_add(self._set_step_done, key)

        GLib.idle_add(self.progress.set_fraction, 1.0)

    # ── Installatie stappen ───────────────────────────────────────────

    def _clone_repo(self):
        try:
            if (INSTALL_DIR / ".git").exists():
                subprocess.run(["git", "-C", str(INSTALL_DIR), "fetch", "--tags", "-q"],
                               check=True, capture_output=True)
                ref = self.selected_version if self.selected_version else "origin/main"
                subprocess.run(["git", "-C", str(INSTALL_DIR), "reset", "--hard", ref, "-q"],
                               check=True, capture_output=True)
            else:
                if INSTALL_DIR.exists():
                    import shutil
                    shutil.rmtree(INSTALL_DIR)
                if self.selected_version:
                    subprocess.run(
                        ["git", "clone", "-q", "--branch", self.selected_version,
                         "--depth", "1", REPO_URL, str(INSTALL_DIR)],
                        check=True, capture_output=True
                    )
                else:
                    subprocess.run(["git", "clone", "-q", REPO_URL, str(INSTALL_DIR)],
                                   check=True, capture_output=True)
            return True, ""
        except subprocess.CalledProcessError:
            return False, _("download failed")

    def _install_apt(self):
        packages = [
            "python3-gi", "python3-gi-cairo",
            "gir1.2-gtk-4.0", "gir1.2-adw-1",
            "ifuse", "libimobiledevice-utils", "usbmuxd",
            "ffmpeg", "python3-pip",
        ]
        try:
            subprocess.run(["sudo", "apt-get", "install", "-y", "-qq"] + packages,
                           check=True, capture_output=True)
        except subprocess.CalledProcessError:
            return False, _("apt failed")
        # WebKit typelib — probeer 6.0 eerst, valt terug op 4.1
        for wk in ("gir1.2-webkit-6.0", "gir1.2-webkit2-4.1"):
            try:
                subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", wk],
                               check=True, capture_output=True)
                break
            except subprocess.CalledProcessError:
                continue
        return True, ""

    def _install_pip(self):
        packages = ["Pillow", "pillow-heif", "imagehash", "watchdog"]
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q",
                 "--break-system-packages"] + packages,
                check=True, capture_output=True
            )
            return True, ""
        except subprocess.CalledProcessError:
            return False, _("pip failed")

    def _create_launcher(self):
        try:
            BIN_DIR.mkdir(parents=True, exist_ok=True)
            DESKTOP_DIR.mkdir(parents=True, exist_ok=True)

            launcher = BIN_DIR / "pixora"
            launcher.write_text(
                "#!/bin/bash\n"
                "# Run met AppArmor-profile 'pixora' als beschikbaar, zodat\n"
                "# WebKit's bwrap-sandbox werkt op Ubuntu 24.04+. Anders\n"
                "# gewoon direct — werkt op systemen zonder AppArmor-restrictie.\n"
                "if command -v aa-exec >/dev/null 2>&1 && "
                "[ -r /etc/apparmor.d/pixora ]; then\n"
                f"  exec aa-exec -p pixora -- python3 {INSTALL_DIR}/viewer/main.py \"$@\"\n"
                "else\n"
                f"  exec python3 {INSTALL_DIR}/viewer/main.py \"$@\"\n"
                "fi\n"
            )
            launcher.chmod(0o755)

            icon = INSTALL_DIR / "assets" / "logos" / "pixora-icon.svg"
            desktop = DESKTOP_DIR / "pixora.desktop"
            # .desktop-file i18n: GenericName[xx]= / Comment[xx]= worden door de
            # desktop-environment gekozen op basis van $LANG van de gebruiker.
            desktop.write_text(
                "[Desktop Entry]\n"
                "Name=Pixora\n"
                "GenericName=Photo & Video Manager\n"
                "GenericName[nl]=Foto & Video Manager\n"
                "GenericName[de]=Foto- & Video-Manager\n"
                "GenericName[fr]=Gestionnaire de photos et vidéos\n"
                "Comment=Photo & video manager by LinuxGinger\n"
                "Comment[nl]=Foto & video manager door LinuxGinger\n"
                "Comment[de]=Foto- & Video-Manager von LinuxGinger\n"
                "Comment[fr]=Gestionnaire de photos et vidéos par LinuxGinger\n"
                f"Exec={launcher}\n"
                f"Icon={icon}\n"
                "Terminal=false\n"
                "Type=Application\n"
                "Categories=Graphics;Photography;\n"
                "StartupWMClass=com.linuxginger.pixora\n"
            )
            return True, ""
        except Exception as e:
            return False, str(e)

    def _start_services(self):
        try:
            subprocess.run(["sudo", "systemctl", "enable", "--now", "usbmuxd"],
                           check=True, capture_output=True)
        except subprocess.CalledProcessError:
            pass
        self._install_apparmor_profile()
        return True, ""

    def _install_apparmor_profile(self):
        # Ubuntu 24.04+ blokkeert unprivileged user-namespaces via AppArmor,
        # waardoor WebKit's sandbox (kaart-weergave) faalt. Dit profile geeft
        # alleen Pixora de userns-permission; de system-wide AppArmor-
        # restrictie blijft intact voor andere apps.
        if not os.path.isdir("/etc/apparmor.d"):
            return
        profile_path = "/etc/apparmor.d/pixora"
        profile_content = (
            "abi <abi/4.0>,\n"
            "include <tunables/global>\n\n"
            "profile pixora flags=(unconfined) {\n"
            "  userns,\n"
            "  include if exists <local/pixora>\n"
            "}\n"
        )
        try:
            tmp = Path("/tmp/pixora-apparmor-profile")
            tmp.write_text(profile_content)
            subprocess.run(
                ["sudo", "install", "-m", "0644", str(tmp), profile_path],
                check=True, capture_output=True
            )
            tmp.unlink(missing_ok=True)
            subprocess.run(
                ["sudo", "systemctl", "reload", "apparmor"],
                check=False, capture_output=True
            )
        except Exception:
            pass

    def _launch_app(self):
        try:
            version_src = INSTALL_DIR / "version.txt"
            installed_version_file = Path.home() / ".config" / "pixora" / "installed_version"
            if version_src.exists():
                installed_version_file.parent.mkdir(parents=True, exist_ok=True)
                installed_version_file.write_text(version_src.read_text())

            # Prefer the installed launcher when it exists; otherwise run
            # main.py directly.
            pixora_bin = Path.home() / ".local" / "bin" / "pixora"
            if pixora_bin.exists():
                cmd = [str(pixora_bin)]
            else:
                main_py = INSTALL_DIR / "viewer" / "main.py"
                cmd = [sys.executable, str(main_py)]
            # Detach from the installer's process group and redirect IO —
            # without start_new_session + DEVNULL pipes, the installer's
            # own quit() (1.5s later) sends SIGHUP to the child Pixora
            # before it finishes its splash, and Pixora dies silently.
            # PIXORA_IN_DEV_TERM=1 voorkomt dat main.py een gnome-terminal
            # probeert te spawnen als dev_mode aanstaat (die spawn faalt
            # vaak in virtualized setups → Pixora opent dan nooit).
            child_env = dict(os.environ)
            child_env["PIXORA_IN_DEV_TERM"] = "1"
            child_env.pop("PIXORA_DEV_LOG_OPENED", None)
            subprocess.Popen(
                cmd,
                start_new_session=True,
                env=child_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            GLib.timeout_add(1500, self.get_application().quit)
            return True, ""
        except Exception as e:
            return False, str(e)


class InstallerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.linuxginger.pixora.installer")
        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        win = InstallerWindow(app)
        win.present()


if __name__ == "__main__":
    app = InstallerApp()
    sys.exit(app.run(sys.argv))
