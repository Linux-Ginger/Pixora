<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logos/pixora-logo-dark.svg">
    <img alt="Pixora" src="assets/logos/pixora-logo-light.svg" width="340">
  </picture>
</p>

<p align="center">
  <strong>Your photos and videos, kept tidy — on your own machine.</strong>
</p>

<p align="center">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Linux-1793D1?logo=linux&logoColor=white">
  <img alt="Built for" src="https://img.shields.io/badge/Ubuntu-24.04%2B-E95420?logo=ubuntu&logoColor=white">
  <img alt="GTK4" src="https://img.shields.io/badge/GTK4-libadwaita-4A86CF">
  <img alt="License" src="https://img.shields.io/badge/license-GPL--3.0-blue">
</p>

---

**Pixora** is an open-source photo & video manager for Linux. Plug in your iPhone or iPad and Pixora imports everything, files it into neat folders, catches duplicates by actually comparing the images, backs up to your external drive, and gives you a fast viewer with maps, favourites and a built-in editor — all offline, all on your own machine.

> ⚠️ **Under active development.** Pixora is usable but still evolving; expect rough edges.

---

## ✨ Highlights

### 📥 Import
- **Automatic iPhone & iPad detection** — plug in and Pixora takes over
- **Keeps your edits** — crops and filters made on your device come across, not the untouched original
- **HEIC → JPEG on import** *(optional)* — re-saved at maximum quality, with date and location preserved
- **Corruption review** — unreadable files are flagged before they reach your library, never imported silently

### 🗂️ Organise
- **Tidy folder structure** — flat, by year, or by year / month
- **Perceptual duplicate detection** — compares actual image content, not just filename or date; you always decide per match (skip, import anyway, or keep both)
- **Nothing is ever deleted without you** — duplicates and originals are always reviewed first

### 🖼️ Browse
- **Photo & video viewer** — grid with date headers, timeline scrollbar and filmstrip navigation
- **Inline video playback** — scrubber with frame previews, zoom and pan
- **Built-in editor** — rotate and crop without leaving the app
- **Favourites** — heart anything and filter to favourites only
- **Map view** — see where your shots were taken (GPS from photos *and* videos), set your home and saved places

### 🛟 Keep safe
- **Automatic backup** — sync or mirror to an external USB/HDD after every import
- **First-run setup wizard** — sensible defaults, up and running in minutes

---

## 🚀 Installation

```bash
curl -fsSL https://raw.githubusercontent.com/Linux-Ginger/Pixora/main/install.sh | bash
```

The installer pulls in every dependency for you. Requires **Ubuntu 24.04 LTS** (or a recent Debian-based distro).

### Updating

Pixora checks **GitHub Releases** for new versions and offers to update from inside the app — open **Settings → About → Check for updates**. The release notes for each version are shown right there under **What's new**.

### Uninstalling

```bash
curl -fsSL https://raw.githubusercontent.com/Linux-Ginger/Pixora/main/uninstall.sh | bash
```

Your photo library is never touched by the uninstaller.

---

## 🔒 Privacy

Pixora is offline-first. Your photos, videos and library never leave your computer.

- **Locations are looked up once, on demand.** When you add a home or place by address, Pixora asks OpenStreetMap (Nominatim) to turn that address into coordinates. Nothing else is sent, and the result is stored only on your machine.
- **Your settings stay local** in `~/.config/pixora/` (with sensitive files locked down to your user).
- The only other network calls are the update check and the one-time dependency install.

---

## 📋 Requirements

- Ubuntu 24.04 LTS or newer (Debian-based)
- An iPhone or iPad (Lightning or USB-C) for importing
- Python 3.12+
- GTK 4 + libadwaita (`python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`)
- `ifuse` + `libimobiledevice` (device access)
- `ffmpeg` (video thumbnails and previews)

All of the above are installed automatically by the install script.

---

## 🤖 Vibecoded

Pixora is vibecoded by **Linux Ginger** together with **Claude** (Anthropic).

---

## 📄 License

Released under the **GNU General Public License v3.0** — see [LICENSE](LICENSE). You're free to use, study, share and modify Pixora, as long as you extend those same freedoms to others.

---

## 🌐 Website

[linuxginger.com/pixora](https://linuxginger.com/pixora)
