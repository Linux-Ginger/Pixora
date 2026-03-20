# 🖼️ Pixora Viewer
### by LinuxGinger

The Viewer is the browsing module of Pixora. It reads directly from your photo folders — no library imports, no databases, no nonsense. Just your photos, organised the way you set them up.

---

## ✨ Features

| Feature | Description |
|---|---|
| 🖼️ Browse by folder | Reads directly from your photo directories |
| 📅 Organised view | Navigate by year and month |
| 🔍 Fast thumbnails | Quick thumbnail generation and caching |
| 📱 Import button | Launch Pixora Importer directly from the Viewer |
| ⚙️ Shared settings | Uses the same settings as the Importer |

---

## 🔌 Importer integration

The Viewer and Importer are two separate apps but work together seamlessly. The Viewer has an **"Import from iPhone"** button that launches the Importer directly.

On first launch, the Viewer checks if the Importer is installed. If not, it offers to install it for you.

---

## 📁 How it works

The Viewer reads directly from the folder structure that the Importer creates:

```
~/Photos/
├── 2024/
│   ├── 2024-01/
│   ├── 2024-02/
│   └── 2024-03/
└── 2025/
    └── 2025-01/
```

No imports, no copying, no database. Just your files — always in sync with what's on disk.

---

## 🛠️ Dependencies

- `PyQt6` — GUI
- `Pillow` — thumbnail generation

---

## 📄 License

GNU General Public License v3.0 — see [LICENSE](../LICENSE)

---

<sub>Part of <a href="https://github.com/Linux-Ginger/pixora">Pixora</a> by <a href="https://linuxginger.com">LinuxGinger</a></sub>
