# 📱 Pixora Importer
### by LinuxGinger

The Importer is the core module of Pixora. It detects your iPhone, imports your photos and videos, checks for duplicates by actually comparing image content, and backs everything up to your external drive — automatically.

---

## ✨ Features

| Feature | Description |
|---|---|
| 📱 iPhone detection | Automatically detects your iPhone via udev |
| 🔌 Auto mount | Mounts via ifuse, no manual steps needed |
| 📁 Folder structure | Choose flat, per year, or per year/month |
| 🔍 Duplicate detection | Perceptual hashing — compares actual image content |
| 🖼️ Duplicate viewer | See duplicates side by side, you decide what to keep |
| 💾 Auto backup | Syncs to your external USB/HDD after import |
| 📊 Progress bars | Live progress during copy and scan |
| ⚙️ Settings | Saved automatically after first-time setup |
| 🧙 Setup wizard | First-time configuration in minutes |

---

## 🔍 How duplicate detection works

Most tools compare duplicates by filename, date or file size. These are unreliable — iPhones rename files on export, metadata changes after edits, and the same photo can have a different size depending on compression.

Pixora Importer uses **perceptual hashing**:

1. Each photo is resized to a tiny thumbnail
2. A visual fingerprint (hash) is calculated from the actual pixel content
3. Hashes are compared — the closer they are, the more similar the photos
4. Duplicates are shown side by side so you can decide which to keep

This catches duplicates even if they have different filenames, dates, sizes, or slight edits.

---

## 📁 Folder structure options

| Option | Example |
|---|---|
| Flat | `~/Photos/IMG_001.jpg` |
| Per year | `~/Photos/2024/IMG_001.jpg` |
| Per year/month | `~/Photos/2024/2024-03/IMG_001.jpg` |

---

## 💾 Backup

After every import, Pixora checks if your backup drive is connected by its **UUID** — a unique identifier that never changes, regardless of which USB port you use. If the drive is found, it syncs automatically.

---

## 🛠️ Dependencies

- `ifuse` — iPhone filesystem access
- `libimobiledevice` — iPhone communication
- `PyQt6` — GUI
- `Pillow` — image processing
- `imagehash` — perceptual hashing

All installed automatically via `install.sh`.

---

## 📄 License

GNU General Public License v3.0 — see [LICENSE](../LICENSE)

---

<sub>Part of <a href="https://github.com/Linux-Ginger/pixora">Pixora</a> by <a href="https://linuxginger.com">LinuxGinger</a></sub>
