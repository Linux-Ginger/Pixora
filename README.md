# Pixora
### by LinuxGinger

### Under Development!

![Pixora Logo](assets/logos/pixora-logo-light.svg)

**Pixora** is an open source photo and video manager for Linux. Import from your iPhone, automatically detect duplicates by actually comparing the images, back up to an external drive, and browse your collection — all in one app.

---

## ✨ Features

- 📱 **Automatic iPhone detection** — plug in your iPhone and Pixora starts automatically
- 📁 **Smart folder structure** — flat, per year, or per year/month
- 🔍 **Perceptual duplicate detection** — compares actual image content, not just filename or date
- 💾 **Automatic backup** — syncs to your external USB/HDD after every import
- 📊 **Progress bars** — always know what Pixora is doing
- 🖼️ **Photo & video viewer** — grid with date headers, timeline scrollbar, filmstrip navigation
- 🎬 **Video playback** — inline player with scrubber and frame previews
- ✂️ **Built-in editor** — rotate and crop without leaving the app
- 🗺️ **Map view** — see where your photos were taken, with GPS support for videos too
- ❤️ **Favorites** — heart photos and videos, filter by favorites only
- ⚙️ **First-time setup wizard** — up and running in minutes
- 🔌 **Importer add-on** — the importer can be installed separately and launched from the viewer

---

## 🚀 Installation

```bash
curl -fsSL https://raw.githubusercontent.com/Linux-Ginger/pixora/main/install.sh | bash
```

Requires **Ubuntu 24.04 LTS** or newer.

---

## 📋 Requirements

- Ubuntu 24.04 LTS (or Debian-based)
- iPhone (Lightning or USB-C)
- Python 3.12+
- GTK 4 + libadwaita (`python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`)
- ifuse + libimobiledevice
- ffmpeg (for video thumbnails and previews)

All dependencies are installed automatically by the install script.

---

## 🤖 Pixora is vibecoded

Pixora is vibecoded by **Linux Ginger** together with **Claude** (Anthropic).

---

## 📄 License

This project is licensed under the **GNU General Public License v3.0** — see the [LICENSE](LICENSE) file for details.

---

## 🌐 Website

[linuxginger.com/pixora](https://linuxginger.com/pixora)

