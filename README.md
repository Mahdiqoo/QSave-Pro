<div align="center">

# 💾 QSave Pro

**A polished, universal quicksave and autosave manager for any PC game.**

Rotating quicksaves, global hotkeys, undo-restore safety nets, and a slick gallery UI — for the games that never bothered to add a quicksave slot of their own.

[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-informational)](#-installation)
[![License: Custom](https://img.shields.io/badge/license-custom%20(no%20redistribution)-red.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-Mahdiqoo-181717?logo=github)](https://github.com/Mahdiqoo)
[![Nexus Mods](https://img.shields.io/badge/Nexus%20Mods-Mahdi2401-D98F40?logo=nexusmods&logoColor=white)](https://www.nexusmods.com/users/Mahdi2401)

</div>

---

## Why QSave Pro?

Some games save whenever they feel like it. Some only let you save at checkpoints. Some let you save anywhere, but one bad autosave can still wipe out an hour of progress. **QSave Pro sits on top of any game's save folder** and gives it the quicksave/quickload system it should have shipped with — no mods, no memory editing, no game-specific integration required. If a game writes its save data to a folder on disk, QSave Pro can protect it.

Hit **End**, your save folder is snapshotted. Hit **Insert**, the latest snapshot is restored. Everything else — history, undo, screenshots, pinning — happens automatically in the background.

## ✨ Features

| | |
|---|---|
| 🎮 **Global hotkeys** | Quicksave and restore-latest from anywhere, even while the game has focus (default `End` / `Insert`, fully rebindable) |
| 🔁 **Rotating save slots** | Configurable max slots per game — old quicksaves roll off automatically so disk usage stays bounded |
| 🧷 **Pins & permanent archives** | Lock a save so it's immune to rotation, or archive it out of the rotating pool entirely |
| ↩️ **Undo Restore** | QSave silently snapshots your *live* save before every restore, so a bad quickload is never a dead end |
| ⏱️ **Autosave timer** | Optional global or per-preset autosave interval, with its own max-slot count |
| 🖼️ **Screenshot gallery** | Every save can carry a thumbnail, label, note, and file size — sortable and filterable in a visual grid |
| 📦 **Export / import** | Package slots as `.zip` for backup or sharing, with configurable exclude patterns |
| 🗂️ **Multi-game presets** | Unlimited per-game profiles, each with its own folder, hotkeys, colour, and autosave rules |
| 🔔 **Toasts & sounds** | Distinct on-screen notifications and audio cues for save / restore / error, no extra audio deps |
| 🕹️ **Auto-focus on launch** | Link a preset to a game's `.exe` and QSave Pro will pop back to the front the moment that game starts |
| 🧊 **System tray mode** | Minimize to tray, auto-minimize on close, or run fully in the background |
| 🌗 **Modern themed UI** | A clean, dark, hand-rolled Tkinter interface — no bloated framework required |

## 🚀 Installation

### Requirements
- Python **3.9+**
- Windows or Linux (macOS mostly works, but global hotkeys and screenshots are less reliable there)

### 1. Clone the repository
```bash
git clone https://github.com/Mahdiqoo/qsave-pro.git
cd qsave-pro
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```
Tkinter ships with most Python installs. Every dependency in `requirements.txt` is optional and QSave Pro degrades gracefully without it — but for the full experience:

| Package | Enables |
|---|---|
| `keyboard` | Global hotkeys |
| `Pillow` | Screenshots + thumbnail gallery |
| `pystray` | System tray icon |
| `psutil` | Auto-focus when a linked game launches |

### 3. Run it
```bash
python qsave.py
```

> **Global hotkeys need elevated input access.** On Windows, run as Administrator if hotkeys don't register. On Linux, run with `sudo` or add your user to the `input` group.
>
> **Screenshots on Linux/X11** require `scrot` to be installed (`sudo apt install scrot`).

### Building a standalone executable
QSave Pro auto-detects when it's frozen and keeps its data folder next to the `.exe` instead of a temp directory, so it's PyInstaller-ready out of the box:
```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name QSavePro qsave.py
```

## 🕹️ Usage

1. Launch QSave Pro and create a **preset** for your game — point it at the folder your game writes save data to.
2. Set (or keep) the quicksave / restore-latest hotkeys for that preset.
3. Play normally. Press your quicksave key any time you want a checkpoint.
4. If something goes wrong, press restore — your last save comes back, and the save that was overwritten is kept as an Undo Restore step in case you change your mind.
5. Open the gallery to browse, label, pin, or export any past save.

All data — config, save history, logs — lives in plain files next to `qsave.py` (or next to the `.exe` if packaged), so backing up or moving your setup is just copying a folder.

## 🗺️ Roadmap

- [ ] Cloud-sync-friendly save folder support
- [ ] Per-preset custom sound packs
- [ ] Steam/Epic library auto-detection for faster preset setup
- [ ] Linux packaging (AppImage / Flatpak)

## 🤝 Contributing

Issues and pull requests are welcome. If you're adding a feature, please open an issue first so we can talk through the approach — this is a single-file Tkinter app and keeping it organized matters. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for details.

## 📄 License

QSave Pro is source-available under a [custom license](LICENSE): you're free to use and run it, and to modify it for your own personal use, but you may **not** publish, redistribute, or share modified or unmodified copies elsewhere (GitHub forks that redistribute, NexusMods re-uploads, bundling into other projects, etc.) without written permission. Want to redistribute or build on it publicly? Reach out via GitHub or NexusMods — happy to talk about it.

## 🙏 Credits

Built and maintained by **[Mahdiqoo](https://github.com/Mahdiqoo)** ([NexusMods](https://www.nexusmods.com/eldenring/mods/10507)).

If QSave Pro saved your run, a ⭐ on the repo goes a long way.
