#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QSave Pro — Quick Save Manager for Games
========================================

A polished desktop utility that keeps rotating "quicksaves" for any game whose
save data lives in a folder on disk.

Per game preset you configure:
  * the save folder to back up
  * how many rotating slots to keep
  * a quicksave hotkey (default: End) and a restore-latest hotkey (default: Insert)

Highlights
----------
  * Global hotkeys, screenshots per save, on-screen toasts, distinct sounds
  * Pinned saves + permanent archives that never get rotated away
  * Undo Restore (a safety snapshot of your live save is taken before restoring)
  * Autosave timer, rewind/forward through your save history
  * Grid gallery with thumbnails, labels, notes, sizes, sorting/filtering
  * Export/import slots as ZIP, exclude patterns, per-game colours
  * Optional system-tray mode — hide/show on demand from the header button,
    or auto-minimise on close
  * Per-preset autosave switch that can override the global autosave setting
    in either direction, and an optional linked game .exe that brings QSave
    back to the front the moment that game starts running

Install
-------
    pip install keyboard Pillow          # recommended
    pip install pystray                  # optional: system tray
    pip install psutil                   # optional: auto-open on game launch

Notes
-----
  * Global hotkeys read raw input system-wide: on Windows you may need to run
    as Administrator; on Linux run with sudo or be in the `input` group.
  * Screenshots use PIL.ImageGrab (Windows/macOS out of the box; on Linux/X11
    install `scrot`). Everything still works without Pillow, just without images.
  * Sounds are tiny WAV files generated on first run and played with the OS
    player, so there are no extra audio dependencies.
"""

from __future__ import annotations

import fnmatch
import json
import math
import os
import platform
import queue
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave
import webbrowser
import zipfile
from collections import deque
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import font as tkfont

try:
    import keyboard
except Exception:                                    # pragma: no cover
    keyboard = None

try:
    from PIL import Image, ImageGrab, ImageTk, ImageDraw
except Exception:                                    # pragma: no cover
    Image = ImageGrab = ImageTk = ImageDraw = None

try:
    import pystray
except Exception:                                    # pragma: no cover
    pystray = None

try:
    import psutil
except Exception:                                    # pragma: no cover
    psutil = None


# ===========================================================================
# 1. CONSTANTS & PATHS
# ===========================================================================

APP_NAME = "QSave Pro"
APP_VERSION = "2.0"
APP_DEVELOPER = "Mahdiqoo"
APP_DEVELOPER_GITHUB = "Mahdiqoo"
APP_DEVELOPER_NEXUSMODS = "Mahdi2401"

# Optional ways for people to support development. The Donate dialog below
# builds one card per entry automatically, so adding another payment
# provider later is just another dict here — no UI code to touch.
#
# "pay_links" (optional, per option) are wallet deep links that open a
# crypto wallet app/site with the token and address already filled in, so
# a donor just has to pick an amount and hit Send — the dialog shows only
# the site's name as a button, never the raw URL. If the donation address
# above ever changes, update it inside these URLs too (the "address="
# query parameter in each link).
_USDT_BEP20_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"  # official Tether contract on BNB Smart Chain
_USDT_BEP20_ADDRESS = "0x860cCD3dBf5037f2880161a8bD6f97BC3eBbea81"

DONATE_OPTIONS = [
    {
        "icon": "₮",
        "label": "Tether (USDT)",
        "network": "BEP20 · BNB Smart Chain",
        "note": "Deposit address on BitPin",
        "address": _USDT_BEP20_ADDRESS,
        "pay_links": [
            {
                # Trust Wallet's official "Send Payment" deep link — opens
                # the app with USDT (BEP20) and this address pre-filled,
                # or a download page if it isn't installed yet.
                "name": "Trust Wallet",
                "url": (
                    "https://link.trustwallet.com/send"
                    "?asset=c20000714_t" + _USDT_BEP20_CONTRACT +
                    "&address=" + _USDT_BEP20_ADDRESS +
                    "&memo=Donation%20for%20QSave%20Pro"
                ),
            },
            {
                # MetaMask's universal deep link for an ERC-20/BEP-20
                # transfer (EIP-681 style), chain 56 = BNB Smart Chain.
                "name": "MetaMask",
                "url": (
                    "https://metamask.app.link/send/" + _USDT_BEP20_CONTRACT +
                    "@56/transfer?address=" + _USDT_BEP20_ADDRESS
                ),
            },
        ],
    },
]


def _get_app_dir():
    """Folder QSave should read/write its data in.

    - Normal `python qsave.py` run: folder containing this script.
    - Packaged as a single .exe (PyInstaller/cx_Freeze/etc.): `__file__`
      resolves inside a temporary extraction folder (e.g. `sys._MEIPASS`)
      that gets wiped when the app closes, so config/saves/log would
      silently disappear every run. Use the folder next to the actual
      .exe instead, so data persists alongside it.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = _get_app_dir()
CONFIG_FILE = os.path.join(APP_DIR, "qsave_config.json")
QSAVES_DIR = os.path.join(APP_DIR, "QSaves")
SOUND_DIR = os.path.join(APP_DIR, ".qsave_sounds")
LOG_FILE = os.path.join(APP_DIR, "qsave_activity.log")

SND_SAVE = os.path.join(SOUND_DIR, "save.wav")
SND_RESTORE = os.path.join(SOUND_DIR, "restore.wav")
SND_ERROR = os.path.join(SOUND_DIR, "error.wav")

IMG_SUBDIR = "img"
UNDO_DIR = "_undo"
UNDO_KEEP = 3                 # how many Undo Restore steps are kept
ORIGINAL_DIR = "_original"    # untouched, pre-QSave copy of the save folder

DEFAULT_SAVE_KEY = "end"
DEFAULT_RESTORE_KEY = "insert"
DEFAULT_MAX_SAVES = 8

CARD_W, CARD_H = 244, 208
THUMB_W, THUMB_H = 242, 122

PRESET_COLORS = [
    "#CC785C", "#6A8CAF", "#7A9E7E", "#B08968",
    "#9C6B9E", "#C4903D", "#5E8C87", "#B3543F",
]

SORT_MODES = ["Newest first", "Oldest first", "Slot number", "Largest first", "Pinned first"]

KIND_LABELS = {
    "quick": ("QUICK", "ACCENT"),
    "auto": ("AUTO", "WARN"),
    "manual": ("ARCHIVE", "OK"),
    "import": ("IMPORT", "TEXT_MUTED"),
}

DEFAULT_SETTINGS = {
    "sounds": True,
    "toasts": True,
    "toast_position": "bottom-right",
    "screenshots": True,
    "hide_window_for_capture": True,
    "capture_delay_ms": 250,
    "screenshot_max_width": 1600,
    "confirm_restore": False,
    "confirm_delete": True,
    "safety_snapshot": True,
    "autosave_enabled": False,
    "autosave_minutes": 10,
    "autosave_max_default": 5,
    "hotkeys_paused": False,
    "ignore_when_focused": True,
    "always_on_top": False,
    "close_to_tray": False,
    "view_mode": "grid",
    "sort_mode": "Newest first",
    "geometry": "1220x780",
    "window_maximized": False,
    "last_preset": "",
    "onboarding_seen": False,
    "global_hotkeys": {
        "quicksave_active": "ctrl+f5",
        "restore_active": "ctrl+f9",
        "rewind": "ctrl+page down",
        "forward": "ctrl+page up",
        "pause_toggle": "ctrl+alt+p",
    },
}


# ===========================================================================
# 2. THEME
# ===========================================================================

PALETTE = {
    "BG": "#F3EEE6",
    "BG_ALT": "#EAE3D7",
    "PANEL": "#FBF9F5",
    "PANEL_ALT": "#F2ECE2",
    "CARD": "#FFFFFF",
    "CARD_HOVER": "#FFFCF8",
    "SIDEBAR": "#EDE6DA",
    "SIDEBAR_HOVER": "#E4DBCB",
    "FIELD": "#FFFFFF",
    "BORDER": "#E2DACB",
    "BORDER_STRONG": "#C9BDA3",
    "DIVIDER": "#E6DFD1",
    "SHADOW": "#DDD3BE",
    "TEXT": "#241F16",
    "TEXT_SOFT": "#54503F",
    "TEXT_MUTED": "#8D8672",
    "ACCENT": "#C96B4A",
    "ACCENT_HOVER": "#B15A3B",
    "ACCENT_STRONG": "#9C4B30",
    "ACCENT_SOFT": "#F4E1D4",
    "ACCENT_SOFT_HOVER": "#EDD1BC",
    "ACCENT_TEXT": "#FFFFFF",
    "OK": "#3C7A54",
    "OK_SOFT": "#E1EDE4",
    "WARN": "#94690A",
    "WARN_SOFT": "#F6E9CE",
    "DANGER": "#AE4A34",
    "DANGER_SOFT": "#F6DFD7",
    "SCROLL": "#D6CCB9",
    "TOAST_BG": "#241F16",
    "TOAST_TEXT": "#FBF9F5",
}

# Consistent spacing scale (px) used by the reorganised layout code below.
SPACE = {"xs": 4, "sm": 8, "md": 14, "lg": 20, "xl": 28}


class Theme:
    """Global theme holder. Attributes are (re)assigned by apply()."""

    # placeholders (filled by apply/init_fonts)
    BG = PANEL = CARD = TEXT = ACCENT = "#000000"
    BASE_FAMILY = "TkDefaultFont"

    @classmethod
    def apply(cls):
        for k, v in PALETTE.items():
            setattr(cls, k, v)

    @classmethod
    def color(cls, key, fallback=None):
        return getattr(cls, key, fallback or cls.TEXT)

    @classmethod
    def init_fonts(cls, root):
        try:
            families = set(tkfont.families(root))
        except Exception:
            families = set()
        prefs = ("Segoe UI Variable Text", "Segoe UI", "Inter", "SF Pro Text",
                 "Helvetica Neue", "Ubuntu", "Noto Sans", "DejaVu Sans")
        base = next((f for f in prefs if f in families), None)
        if base is None:
            try:
                base = tkfont.nametofont("TkDefaultFont").actual("family")
            except Exception:
                base = "Helvetica"
        mono_prefs = ("Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono", "Courier New")
        mono = next((f for f in mono_prefs if f in families), base)

        cls.BASE_FAMILY = base
        cls.F_LOGO = (base, 18, "bold")
        cls.F_H1 = (base, 21, "bold")
        cls.F_H2 = (base, 15, "bold")
        cls.F_H3 = (base, 12, "bold")
        cls.F_BODY = (base, 10)
        cls.F_BODY_B = (base, 10, "bold")
        cls.F_SMALL = (base, 9)
        cls.F_SMALL_B = (base, 9, "bold")
        cls.F_TINY = (base, 8)
        cls.F_TINY_B = (base, 8, "bold")
        cls.F_GLYPH = (base, 13)
        cls.F_BIG_GLYPH = (base, 32)
        cls.F_MONO = (mono, 9)
        cls.F_EYEBROW = (base, 9, "bold")   # small caps-style section labels


def setup_ttk(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(
        "QS.Vertical.TScrollbar",
        background=Theme.SCROLL, troughcolor=Theme.PANEL, bordercolor=Theme.PANEL,
        darkcolor=Theme.SCROLL, lightcolor=Theme.SCROLL, arrowcolor=Theme.TEXT_MUTED,
        relief="flat", gripcount=0, width=11,
    )
    style.map("QS.Vertical.TScrollbar",
              background=[("active", Theme.BORDER_STRONG)],
              troughcolor=[("active", Theme.PANEL)])


# ===========================================================================
# 3. SMALL UTILITIES
# ===========================================================================

def sanitize(name):
    keep = "-_ ()"
    cleaned = "".join(c if c.isalnum() or c in keep else "_" for c in str(name))
    return cleaned.strip() or "preset"


def human_size(n):
    if n is None:
        return "—"
    units = ("B", "KB", "MB", "GB", "TB")
    f = float(n)
    i = 0
    while f >= 1024 and i < len(units) - 1:
        f /= 1024.0
        i += 1
    if i == 0:
        return f"{int(f)} {units[i]}"
    return f"{f:.1f} {units[i]}" if f < 100 else f"{f:.0f} {units[i]}"


def dir_stats(path, excludes=None):
    """Return (total_bytes, file_count) for a folder tree."""
    total, count = 0, 0
    excludes = excludes or []
    for root, dirs, files in os.walk(path):
        if excludes:
            dirs[:] = [d for d in dirs if not any(fnmatch.fnmatch(d, p) for p in excludes)]
        for fn in files:
            if excludes and any(fnmatch.fnmatch(fn, p) for p in excludes):
                continue
            try:
                total += os.path.getsize(os.path.join(root, fn))
                count += 1
            except OSError:
                pass
    return total, count


def parse_excludes(text):
    if not text:
        return []
    raw = text.replace("\n", ";").replace(",", ";").split(";")
    return [r.strip() for r in raw if r.strip()]


def fmt_ts(iso_ts, fmt="%b %d, %Y · %H:%M:%S"):
    try:
        return datetime.fromisoformat(iso_ts).strftime(fmt)
    except Exception:
        return str(iso_ts or "—")


def rel_time(iso_ts):
    try:
        dt = datetime.fromisoformat(iso_ts)
    except Exception:
        return "—"
    secs = (datetime.now() - dt).total_seconds()
    if secs < 0:
        secs = 0
    if secs < 10:
        return "just now"
    if secs < 60:
        return f"{int(secs)}s ago"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    if secs < 86400 * 7:
        return f"{int(secs // 86400)}d ago"
    return dt.strftime("%b %d")


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def win_documents_dir():
    """Resolve the OS's *actual current* Documents folder on Windows.

    A plain os.path.join(home, "Documents") guess misses two very common
    real-world cases: the user relocated Documents to another drive/folder
    via its Properties > Location tab, or a sync client moved it under a
    differently-named path. Both leave the default guess pointing at a
    folder that's empty (or doesn't exist), which is exactly why auto-detect
    can come up empty even though "many saves are there" in the real
    Documents folder. SHGetFolderPathW asks Windows for the current path
    directly, so relocations are handled automatically.
    """
    if platform.system() != "Windows":
        return None
    try:
        import ctypes
        CSIDL_PERSONAL = 5        # "My Documents"
        SHGFP_TYPE_CURRENT = 0    # current value, not the default
        buf = ctypes.create_unicode_buffer(1024)
        res = ctypes.windll.shell32.SHGetFolderPathW(0, CSIDL_PERSONAL, 0, SHGFP_TYPE_CURRENT, buf)
        if res == 0 and buf.value and os.path.isdir(buf.value):
            return buf.value
    except Exception:
        pass
    return None


def open_in_file_manager(path):
    if not path:
        return False, "No path."
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(path)                                     # noqa: attr on Windows only
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True, "Opened folder."
    except Exception as e:
        return False, f"Could not open folder: {e}"


def center_on(win, parent=None):
    win.update_idletasks()
    w, h = win.winfo_width(), win.winfo_height()
    if parent is not None and parent.winfo_viewable():
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        x, y = px + (pw - w) // 2, py + max(20, (ph - h) // 3)
    else:
        x = (win.winfo_screenwidth() - w) // 2
        y = (win.winfo_screenheight() - h) // 3
    win.geometry(f"+{max(0, x)}+{max(0, y)}")


def bind_tree(widget, sequence, func, add="+"):
    widget.bind(sequence, func, add=add)
    for child in widget.winfo_children():
        bind_tree(child, sequence, func, add)


# ===========================================================================
# 4. CONFIG
# ===========================================================================

def normalize_preset(p, settings=None):
    p.setdefault("id", uuid.uuid4().hex[:10])
    p.setdefault("name", "Game")
    p.setdefault("save_path", "")
    p.setdefault("max_saves", DEFAULT_MAX_SAVES)
    p.setdefault("save_key", DEFAULT_SAVE_KEY)
    p.setdefault("restore_key", DEFAULT_RESTORE_KEY)
    p.setdefault("color", PRESET_COLORS[0])
    p.setdefault("exclude", "")
    p.setdefault("notes", "")
    p.setdefault("enabled", True)
    p.setdefault("clean_restore", False)
    # Per-preset autosave switch. It is fully independent of the global
    # "autosave_enabled" setting once created — the global setting is only
    # used as the starting value for brand-new presets (see PresetDialog).
    p.setdefault("autosave", bool((settings or {}).get("autosave_enabled", False)))
    # Per-preset overrides for the autosave interval/cap. 0 means "use the
    # global default from Settings → Safety" for that value.
    p.setdefault("autosave_minutes", 0)
    p.setdefault("autosave_max", 0)
    # Optional path to the game's .exe — when set, QSave watches for that
    # process and brings itself back up when the game is launched.
    p.setdefault("game_exe", "")
    p.setdefault("created", now_iso())
    if not isinstance(p.get("stats"), dict):
        p["stats"] = {}
    stats = p["stats"]
    stats.setdefault("saves", 0)
    stats.setdefault("restores", 0)
    stats.setdefault("last_save", "")
    return p


def merge_settings(user):
    out = json.loads(json.dumps(DEFAULT_SETTINGS))
    for k, v in (user or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def atomic_write_json(path, data):
    """Write JSON to `path` without ever leaving a truncated/corrupt file
    behind if writing is interrupted (crash, power loss, disk full, etc).

    Writes to a temporary file in the same directory first, flushes it to
    disk, then renames it over the real path. A rename onto an existing
    file is atomic on every mainstream OS/filesystem, so `path` always
    ends up holding either its old contents or its complete new contents
    — never something half-written in between. Without this, a config or
    metadata file interrupted mid-write becomes invalid JSON, and every
    read site in this file treats that as "no data" (see load_config,
    load_meta), silently losing whatever it described.
    """
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".qsave_tmp_", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def load_config():
    data = {"presets": [], "settings": {}}
    is_new_install = not os.path.exists(CONFIG_FILE)
    if not is_new_install:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or data
        except Exception:
            pass
    data.setdefault("presets", [])
    raw_settings = data.get("settings") or {}
    data["settings"] = merge_settings(raw_settings)
    if not is_new_install and "onboarding_seen" not in raw_settings:
        # Existing installs predate the first-run tutorial, so an absent key
        # here just means "settings file older than this feature" — not
        # "brand new user". Don't retroactively nag someone who's already
        # been using the app.
        data["settings"]["onboarding_seen"] = True
    data["presets"] = [normalize_preset(p, data["settings"]) for p in data["presets"] if isinstance(p, dict)]

    known_ids = {p["id"] for p in data["presets"]}
    recovered = discover_presets_from_disk(known_ids)
    if recovered:
        data["presets"] += [normalize_preset(p, data["settings"]) for p in recovered]
        save_config(data)  # persist right away so the recovery sticks and isn't redone every launch

    return data


def save_config(config):
    try:
        atomic_write_json(CONFIG_FILE, config)
    except Exception as e:
        print("Could not write config:", e)


def log_to_file(text):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{now_iso()}  {text}\n")
    except Exception:
        pass


# ===========================================================================
# 5. SOUND & SCREENSHOTS
# ===========================================================================

def generate_tone_wav(path, tones, framerate=44100, amp=13000):
    if os.path.exists(path):
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with wave.open(path, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(framerate)
            frames = bytearray()
            for freq, dur in tones:
                n = int(framerate * dur)
                for i in range(n):
                    t = i / framerate
                    # short attack + decay so there is no click
                    env = min(1.0, i / max(1, n * 0.08)) * (1.0 - (i / n) ** 1.5)
                    frames += struct.pack("<h", int(amp * env * math.sin(2 * math.pi * freq * t)))
            wf.writeframes(bytes(frames))
    except Exception as e:
        print("Could not create sound:", e)


def generate_sounds():
    generate_tone_wav(SND_SAVE, [(784, 0.06), (1175, 0.11)])
    generate_tone_wav(SND_RESTORE, [(1175, 0.06), (784, 0.12)])
    generate_tone_wav(SND_ERROR, [(320, 0.10), (240, 0.16)])


def play_wav(path):
    if not path or not os.path.isfile(path):
        return

    def _play():
        try:
            system = platform.system()
            if system == "Windows":
                import winsound
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            elif system == "Darwin":
                subprocess.Popen(["afplay", path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                for player in ("paplay", "aplay", "ffplay"):
                    if shutil.which(player):
                        args = [player, path]
                        if player == "ffplay":
                            args = [player, "-nodisp", "-autoexit", "-loglevel", "quiet", path]
                        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        break
                else:
                    print("\a", end="", flush=True)
        except Exception:
            pass

    threading.Thread(target=_play, daemon=True).start()


def _grab_screen():
    # all_screens=True is not accepted by every Pillow build, so fall back.
    if ImageGrab is None:
        return None
    try:
        try:
            return ImageGrab.grab(all_screens=True)
        except TypeError:
            return ImageGrab.grab()
    except Exception as e:
        print("Screen grab failed:", e)
        return None


def _is_blank_capture(img, tolerance=6):
    # A totally flat frame (classically pure black) means the grab caught
    # nothing useful: the desktop had not been re-composited yet after we
    # hid our window, or the display driver had not handed over a frame.
    # That is exactly what made the very first quicksave come out black.
    try:
        small = img.convert("RGB").resize((32, 32))
        return all((hi - lo) <= tolerance for lo, hi in small.getextrema())
    except Exception:
        return False


def take_screenshot(path, max_width=1600, attempts=3, retry_delay=0.2):
    if ImageGrab is None:
        return False
    img = None
    for _attempt in range(max(1, attempts)):
        candidate = _grab_screen()
        if candidate is not None:
            img = candidate
            if not _is_blank_capture(candidate):
                break
        # Wait a beat and take a completely fresh frame rather than reusing
        # the blank one we just got.
        time.sleep(retry_delay)
    if img is None:
        return False
    try:
        if img.width > max_width:
            ratio = max_width / float(img.width)
            img = img.resize((max_width, max(1, int(img.height * ratio))), Image.LANCZOS)
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        tmp = path + ".part"
        img.convert("RGB").save(tmp, "PNG")
        os.replace(tmp, path)
        return True
    except Exception as e:
        print("Screenshot failed:", e)
        return False


def fit_cover(img, size):
    tw, th = size
    if img.width == 0 or img.height == 0:
        return img
    scale = max(tw / img.width, th / img.height)
    nw, nh = max(1, int(img.width * scale)), max(1, int(img.height * scale))
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def fit_contain(img, size):
    img = img.copy()
    img.thumbnail(size, Image.LANCZOS)
    return img


# ===========================================================================
# 6. CORE SAVE / RESTORE ENGINE
# ===========================================================================

PRESET_META_FILE = "preset.json"
PRESET_META_FIELDS = ["id", "name", "save_path", "max_saves", "save_key", "restore_key",
                      "color", "exclude", "notes", "enabled", "clean_restore", "autosave",
                      "autosave_minutes", "autosave_max", "game_exe", "created"]


def preset_dir(preset):
    return os.path.join(QSAVES_DIR, sanitize(preset["name"]))


def preset_img_dir(preset):
    return os.path.join(preset_dir(preset), IMG_SUBDIR)


def write_preset_meta(preset):
    """Create the preset's own folder (with its image subfolder) under QSaves
    right away, and drop a small preset.json into it mirroring the preset's
    config. The actual quicksave slots already live on disk under this same
    folder — this just makes the folder itself self-describing, so QSave can
    rebuild the preset (see discover_presets_from_disk) if qsave_config.json
    is ever lost or deleted."""
    d = preset_dir(preset)
    try:
        os.makedirs(d, exist_ok=True)
        os.makedirs(preset_img_dir(preset), exist_ok=True)
        payload = {k: preset.get(k) for k in PRESET_META_FIELDS}
        atomic_write_json(os.path.join(d, PRESET_META_FILE), payload)
    except Exception as e:
        print("Could not write preset meta:", e)


def discover_presets_from_disk(known_ids):
    """Scan QSaves/ for preset.json files that don't belong to any preset
    already loaded from qsave_config.json, and rebuild preset entries from
    them. The folders under QSaves (and each one's preset.json / meta.json)
    are the real source of truth for what got saved, so a deleted or
    corrupted config file doesn't have to mean losing the presets list too."""
    recovered = []
    if not os.path.isdir(QSAVES_DIR):
        return recovered
    try:
        entries = os.listdir(QSAVES_DIR)
    except OSError:
        return recovered
    for name in entries:
        folder = os.path.join(QSAVES_DIR, name)
        meta_path = os.path.join(folder, PRESET_META_FILE)
        if not os.path.isfile(meta_path):
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                p = json.load(f)
        except Exception:
            continue
        if not isinstance(p, dict) or not p.get("id") or p.get("id") in known_ids:
            continue
        recovered.append(p)
    return recovered


def slot_folder(preset, slot_num):
    return os.path.join(preset_dir(preset), f"quick_{slot_num}")


def undo_root(preset):
    return os.path.join(preset_dir(preset), UNDO_DIR)


def undo_folder(preset, entry=None):
    # Kept for compatibility: with no entry this is the folder that holds the
    # whole undo stack, otherwise the folder of one specific snapshot.
    if entry is None:
        return undo_root(preset)
    name = entry.get("folder") if isinstance(entry, dict) else str(entry)
    return os.path.join(undo_root(preset), name or "")


def original_folder(preset):
    return os.path.join(preset_dir(preset), ORIGINAL_DIR)


def _migrate_legacy_undo(preset, meta):
    # Older versions kept exactly one snapshot, dumped straight into _undo/.
    # Move it into the new stack layout so nothing is lost on upgrade.
    root = undo_root(preset)
    if not os.path.isdir(root) or meta.get("undo_stack"):
        return meta
    try:
        entries = [e for e in os.listdir(root) if not e.startswith("u_")]
    except OSError:
        return meta
    if not entries:
        return meta
    name = f"u_{uuid.uuid4().hex[:8]}"
    dest = os.path.join(root, name)
    try:
        os.makedirs(dest, exist_ok=True)
        for e in entries:
            shutil.move(os.path.join(root, e), os.path.join(dest, e))
    except Exception:
        return meta
    ts = (meta.get("undo") or {}).get("timestamp") or now_iso()
    meta["undo_stack"] = [{"folder": name, "timestamp": ts}]
    meta["undo"] = meta["undo_stack"][0]
    save_meta(preset, meta)
    return meta


def normalize_meta(meta):
    if not isinstance(meta, dict):
        meta = {}
    meta.setdefault("slots", [])
    meta.setdefault("undo", None)
    meta.setdefault("original", None)
    if not isinstance(meta.get("undo_stack"), list):
        meta["undo_stack"] = []
    clean = []
    for s in meta["slots"]:
        if not isinstance(s, dict) or "slot" not in s:
            continue
        s.setdefault("timestamp", now_iso())
        s.setdefault("screenshot", "")
        s.setdefault("label", "")
        s.setdefault("note", "")
        s.setdefault("pinned", False)
        s.setdefault("kind", "quick")
        s.setdefault("size", None)
        s.setdefault("files", None)
        clean.append(s)
    meta["slots"] = clean
    return meta


def load_meta(preset):
    path = os.path.join(preset_dir(preset), "meta.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return _migrate_legacy_undo(preset, normalize_meta(json.load(f)))
        except Exception:
            return normalize_meta({})
    return normalize_meta({})


def save_meta(preset, meta):
    d = preset_dir(preset)
    try:
        os.makedirs(d, exist_ok=True)
        atomic_write_json(os.path.join(d, "meta.json"), normalize_meta(meta))
    except Exception as e:
        print("Could not write metadata:", e)


def next_free_slot_number(slots):
    used = {s["slot"] for s in slots}
    n = 1
    while n in used:
        n += 1
    return n


KIND_TITLES = {
    "quick": "Quicksave",
    "auto": "Autosave",
    "manual": "Archive",
    "import": "Import",
}


def slot_kind_name(slot):
    return KIND_TITLES.get(slot.get("kind", "quick"), "Save")


def slot_title(slot):
    # Every save now announces what it is first, e.g. "Quicksave Slot 1" or
    # "Autosave Slot 3"; a custom label is appended after it.
    base = f"{slot_kind_name(slot)} Slot {slot['slot']}"
    label = (slot.get("label") or "").strip()
    return f"{base} - {label}" if label else base


def prune_excess_slots(preset):
    """Drop oldest *unpinned* slots if max_saves was lowered."""
    meta = load_meta(preset)
    slots = meta["slots"]
    max_saves = max(1, int(preset.get("max_saves", DEFAULT_MAX_SAVES)))
    unpinned = sorted([s for s in slots if not s.get("pinned")], key=lambda s: s["timestamp"])
    victims = []
    while len(unpinned) > max_saves:
        victims.append(unpinned.pop(0))
    if not victims:
        return 0
    victim_nums = {v["slot"] for v in victims}
    # Persist the metadata change *before* touching anything on disk. If we
    # get interrupted right after this, the worst case is a harmless
    # orphaned folder still sitting on disk — not a slot the app still
    # believes exists whose files have already been deleted.
    meta["slots"] = [s for s in slots if s["slot"] not in victim_nums]
    save_meta(preset, meta)
    for victim in victims:
        _erase_slot_files(preset, victim)
    return len(victims)


def prune_excess_autosaves(preset, max_auto):
    """Drop oldest *unpinned* auto-kind slots beyond max_auto.

    This is independent of (and enforced in addition to) the general
    rotating-slots limit — it exists so autosaves specifically don't crowd
    out manual quicksaves/archives even when the overall slot cap is high.
    """
    meta = load_meta(preset)
    slots = meta["slots"]
    max_auto = max(1, int(max_auto))
    autos = sorted([s for s in slots if s.get("kind") == "auto" and not s.get("pinned")],
                   key=lambda s: s["timestamp"])
    victims = []
    while len(autos) > max_auto:
        victims.append(autos.pop(0))
    if not victims:
        return 0
    victim_nums = {v["slot"] for v in victims}
    # Same ordering rationale as prune_excess_slots: metadata first, then
    # files, so a mid-operation interruption can't leave a dangling slot
    # reference pointing at files that no longer exist.
    meta["slots"] = [s for s in slots if s["slot"] not in victim_nums]
    save_meta(preset, meta)
    for victim in victims:
        _erase_slot_files(preset, victim)
    return len(victims)


def _erase_slot_files(preset, slot):
    shutil.rmtree(slot_folder(preset, slot["slot"]), ignore_errors=True)
    shot = slot.get("screenshot")
    if shot:
        p = os.path.join(preset_dir(preset), shot)
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass


def _scratch_path_next_to(path, tag):
    """A path in the same parent directory as `path`, guaranteed not to
    currently exist, for use as scratch space by the swap-based copy
    helpers below. Keeping it alongside `path` guarantees it's on the same
    filesystem, so the final swap is a cheap atomic rename rather than a
    second slow file-by-file copy."""
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    return os.path.join(parent, f".{os.path.basename(path)}.{tag}.{uuid.uuid4().hex}")


def _replace_dir(new_dir, dst):
    """Make `dst` become `new_dir`, without ever leaving `dst` half-written.

    A directory rename is atomic on the same filesystem, so instead of
    copying files into `dst` in place (which can leave it half-updated if
    interrupted partway through), we rename whatever currently lives at
    `dst` out of the way and then rename the fully-prepared `new_dir` into
    its place. If the second rename fails for some reason, we try to put
    the original back rather than leaving `dst` simply missing.
    """
    dst = os.path.abspath(dst)
    old_backup = None
    if os.path.lexists(dst):
        old_backup = _scratch_path_next_to(dst, "old")
        os.replace(dst, old_backup)
    try:
        os.replace(new_dir, dst)
    except Exception:
        if old_backup is not None and os.path.lexists(old_backup) and not os.path.lexists(dst):
            try:
                os.replace(old_backup, dst)
                old_backup = None
            except Exception:
                pass
        raise
    finally:
        if old_backup is not None:
            shutil.rmtree(old_backup, ignore_errors=True)


def copy_tree(src, dst, excludes=None):
    """Copy the full contents of `src` (including all subfolders, at any
    depth) into `dst`, replacing anything already there.

    The copy is staged in a temporary sibling directory first and only
    swapped into `dst` once it has fully succeeded. This means a failure
    partway through — a locked file, a permissions error, the disk
    filling up, the source vanishing mid-copy — can never destroy an
    existing `dst` or leave it half-written: `dst` is left exactly as it
    was before the call, and the exception simply propagates to the
    caller so it can report the failure instead of silently losing data.
    """
    ignore = shutil.ignore_patterns(*excludes) if excludes else None
    tmp = _scratch_path_next_to(dst, "tmp")
    try:
        shutil.copytree(src, tmp, ignore=ignore, symlinks=True)
        _replace_dir(tmp, dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # no-op once already swapped in


def copy_into(src_dir, dst_dir, clean=False):
    """Copy every item (including nested subfolders) from `src_dir` into
    `dst_dir`.

    If `clean` is True, `dst_dir` ends up an exact mirror of `src_dir`.
    If False, `dst_dir` keeps whatever else it already had and only the
    items also present in `src_dir` get replaced (a merge) — matching the
    "clean restore" preset option.

    The complete target state is built in a temporary staging directory
    first and only swapped into `dst_dir` at the very end via `_replace_dir`.
    `dst_dir` is typically the game's *live* save folder, so this matters a
    lot: a failure partway through (a file the game still has open, a full
    disk, a subfolder that disappears mid-copy) leaves the live save folder
    completely untouched instead of half-restored, and every subfolder that
    *does* make it into the staging copy is guaranteed to be included in
    what finally lands in `dst_dir` — nothing gets applied piecemeal.
    """
    tmp = _scratch_path_next_to(dst_dir, "tmp")
    os.makedirs(tmp, exist_ok=True)
    try:
        if not clean and os.path.isdir(dst_dir):
            # Seed the staging copy with everything already at dst_dir, so
            # a merge preserves items that aren't part of this restore.
            for item in os.listdir(dst_dir):
                s = os.path.join(dst_dir, item)
                d = os.path.join(tmp, item)
                if os.path.isdir(s) and not os.path.islink(s):
                    shutil.copytree(s, d, symlinks=True)
                else:
                    shutil.copy2(s, d)
        # Overlay src_dir on top, replacing any same-named entry so the
        # restored data always wins over whatever was seeded above.
        for item in os.listdir(src_dir):
            s = os.path.join(src_dir, item)
            d = os.path.join(tmp, item)
            if os.path.isdir(s) and not os.path.islink(s):
                shutil.rmtree(d, ignore_errors=True)
                shutil.copytree(s, d, symlinks=True)
            else:
                if os.path.isdir(d):
                    shutil.rmtree(d, ignore_errors=True)
                shutil.copy2(s, d)
        _replace_dir(tmp, dst_dir)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # no-op once already swapped in


def perform_quicksave(preset, kind="quick", pinned=False, label="",
                      screenshot_fn=None, excludes=None):
    """Copy the game's save folder into the next / oldest rotating slot.

    Returns (ok, message, slot_dict_or_None).
    """
    save_path = preset.get("save_path")
    if not save_path or not os.path.isdir(save_path):
        return False, f"'{preset['name']}': save folder is not set or missing.", None

    d = preset_dir(preset)
    os.makedirs(d, exist_ok=True)

    # Take the screenshot FIRST, before any file copying happens, so the
    # image shows the moment the hotkey was pressed instead of whatever is
    # on screen a second or two later - and so every single save (quick or
    # auto) gets its own fresh frame rather than reusing an older file.
    shot_tmp = None
    if screenshot_fn is not None:
        try:
            fd, shot_tmp = tempfile.mkstemp(prefix=".qsave_shot_", suffix=".png", dir=d)
            os.close(fd)
            if not screenshot_fn(shot_tmp):
                try:
                    os.remove(shot_tmp)
                except OSError:
                    pass
                shot_tmp = None
        except Exception:
            shot_tmp = None

    # The very first time this game is saved, stash an untouched copy of the
    # live save folder for the "Restore Original Save" button.
    try:
        ensure_original_backup(preset, excludes)
    except Exception as e:
        print("Original-save backup failed:", e)

    meta = load_meta(preset)
    slots = meta["slots"]
    max_saves = max(1, int(preset.get("max_saves", DEFAULT_MAX_SAVES)))

    unpinned = [s for s in slots if not s.get("pinned")]
    rotate_victim = None
    if pinned or len(unpinned) < max_saves:
        slot_num = next_free_slot_number(slots)
    else:
        rotate_victim = min(unpinned, key=lambda s: s["timestamp"])
        slot_num = rotate_victim["slot"]

    target = slot_folder(preset, slot_num)
    try:
        # copy_tree stages the new copy and only swaps it into `target`
        # once fully successful (see copy_tree). That means if this raises,
        # whatever was previously at `target` — the backup we're about to
        # rotate out, below — is still fully intact on disk; we haven't
        # lost it in exchange for a failed new save.
        copy_tree(save_path, target, excludes)
    except Exception as e:
        return False, f"Failed to copy save data: {e}", None

    if rotate_victim is not None:
        # Only now that the new copy is confirmed safely in place do we
        # drop the rotated-out slot's old bookkeeping and screenshot.
        shot = rotate_victim.get("screenshot")
        if shot:
            p = os.path.join(preset_dir(preset), shot)
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        slots = [s for s in slots if s["slot"] != slot_num]

    size, files = dir_stats(target)

    shot_rel = ""
    if shot_tmp and os.path.isfile(shot_tmp):
        # Give every screenshot its own unique filename instead of reusing
        # "quick_{slot_num}.png". Slot numbers get recycled as saves rotate,
        # and a reused filename means this save's image only looks correct
        # if every cache (ours in-memory, and any OS-level thumbnail cache)
        # is invalidated perfectly on every code path. A unique name per
        # save removes that dependency entirely: a new save can never end
        # up pointing at - or being confused with - an old save's frame.
        rel = os.path.join(IMG_SUBDIR, f"{kind}_{slot_num}_{uuid.uuid4().hex[:8]}.png")
        os.makedirs(preset_img_dir(preset), exist_ok=True)
        try:
            os.replace(shot_tmp, os.path.join(d, rel))
            shot_rel = rel
        except Exception:
            try:
                os.remove(shot_tmp)
            except OSError:
                pass
            shot_rel = ""

    slot = {
        "slot": slot_num,
        "timestamp": now_iso(),
        "screenshot": shot_rel,
        "label": label,
        "note": "",
        "pinned": bool(pinned),
        "kind": kind,
        "size": size,
        "files": files,
    }
    slots.append(slot)
    meta["slots"] = slots
    save_meta(preset, meta)

    stats = preset.setdefault("stats", {})
    stats["saves"] = int(stats.get("saves", 0)) + 1
    stats["last_save"] = slot["timestamp"]

    verb = {"quick": "Quicksaved", "auto": "Autosaved", "manual": "Archived"}.get(kind, "Saved")
    return True, f"{verb} '{preset['name']}' → {slot_title(slot)} ({human_size(size)})", slot


def undo_entries(preset, meta=None):
    # Newest first. Only entries whose folder really still exists count.
    meta = meta or load_meta(preset)
    out = []
    for e in meta.get("undo_stack") or []:
        if not isinstance(e, dict) or not e.get("folder"):
            continue
        folder = undo_folder(preset, e)
        try:
            if os.path.isdir(folder) and os.listdir(folder):
                out.append(e)
        except OSError:
            pass
    return out


def make_undo_snapshot(preset, excludes=None):
    save_path = preset.get("save_path")
    if not save_path or not os.path.isdir(save_path):
        return False
    name = f"u_{uuid.uuid4().hex[:8]}"
    dest = os.path.join(undo_root(preset), name)
    try:
        copy_tree(save_path, dest, excludes)
    except Exception as e:
        print("Undo snapshot failed:", e)
        return False
    meta = load_meta(preset)
    stack = [e for e in (meta.get("undo_stack") or []) if isinstance(e, dict) and e.get("folder")]
    stack.insert(0, {"folder": name, "timestamp": now_iso()})
    victims = stack[UNDO_KEEP:]
    stack = stack[:UNDO_KEEP]
    meta["undo_stack"] = stack
    meta["undo"] = stack[0]
    save_meta(preset, meta)
    for v in victims:
        shutil.rmtree(undo_folder(preset, v), ignore_errors=True)
    # Sweep up any folder no longer referenced by the stack.
    keep = {e["folder"] for e in stack}
    try:
        for entry in os.listdir(undo_root(preset)):
            if entry.startswith("u_") and entry not in keep:
                shutil.rmtree(os.path.join(undo_root(preset), entry), ignore_errors=True)
    except OSError:
        pass
    return True


def has_undo(preset):
    return bool(undo_entries(preset))


def undo_count(preset):
    return len(undo_entries(preset))


def has_original_backup(preset):
    d = original_folder(preset)
    try:
        return os.path.isdir(d) and bool(os.listdir(d))
    except OSError:
        return False


def ensure_original_backup(preset, excludes=None):
    # Taken exactly once, the first time this game is ever saved: a pristine
    # copy of the live save folder that is never rotated or overwritten.
    if has_original_backup(preset):
        return False
    save_path = preset.get("save_path")
    if not save_path or not os.path.isdir(save_path):
        return False
    try:
        copy_tree(save_path, original_folder(preset), excludes)
    except Exception as e:
        print("Original-save backup failed:", e)
        return False
    size, files = dir_stats(original_folder(preset))
    meta = load_meta(preset)
    meta["original"] = {"timestamp": now_iso(), "size": size, "files": files}
    save_meta(preset, meta)
    return True


def restore_original(preset, make_undo=True):
    src = original_folder(preset)
    save_path = preset.get("save_path")
    if not has_original_backup(preset):
        return False, f"No original backup exists for '{preset['name']}' yet."
    if not save_path or not os.path.isdir(save_path):
        return False, f"'{preset['name']}': save folder is not set or missing."
    if make_undo:
        excludes = parse_excludes(preset.get("exclude", ""))
        if not make_undo_snapshot(preset, excludes):
            return False, ("Restore stopped: couldn't create the safety snapshot, "
                           "so nothing was overwritten.")
    try:
        copy_into(src, save_path, clean=bool(preset.get("clean_restore")))
    except Exception as e:
        return False, f"Restore failed: {e}"
    return True, f"Restored the original (pre-QSave) save for '{preset['name']}'."


def restore_slot(preset, slot_num=None, make_undo=True):
    meta = load_meta(preset)
    slots = meta["slots"]
    if not slots:
        return False, f"No quicksaves for '{preset['name']}' yet.", None

    if slot_num is None:
        target = max(slots, key=lambda s: s["timestamp"])
    else:
        target = next((s for s in slots if s["slot"] == slot_num), None)
        if target is None:
            return False, "That quicksave no longer exists.", None

    folder = slot_folder(preset, target["slot"])
    save_path = preset.get("save_path")
    if not os.path.isdir(folder):
        return False, "That quicksave's folder is missing on disk.", None
    if not save_path or not os.path.isdir(save_path):
        # Previously this only checked for an empty string, so a save
        # folder that had been deleted, renamed, or lives on a currently
        # unmounted drive would silently be recreated as an empty folder
        # by copy_into below — restoring into the wrong (brand new, empty)
        # location instead of telling the user something is wrong.
        return False, f"'{preset['name']}': save folder is not set or missing.", None

    if make_undo:
        excludes = parse_excludes(preset.get("exclude", ""))
        if not make_undo_snapshot(preset, excludes):
            # Don't silently restore over the live save with no safety net
            # just because the snapshot happened to fail — stop and tell
            # the user, so a restore never has a hidden chance of being
            # unrecoverable when Settings says it should be undoable.
            return False, (f"Restore stopped: couldn't create the safety snapshot for "
                          f"'{preset['name']}', so nothing was overwritten. Check that the "
                          f"save folder is accessible and try again."), None

    try:
        copy_into(folder, save_path, clean=bool(preset.get("clean_restore")))
    except Exception as e:
        return False, f"Restore failed: {e}", None

    stats = preset.setdefault("stats", {})
    stats["restores"] = int(stats.get("restores", 0)) + 1
    return True, f"Restored '{preset['name']}' from {slot_title(target)}", target


def undo_restore(preset):
    entries = undo_entries(preset)
    save_path = preset.get("save_path")
    if not entries:
        return False, "Nothing to undo — no safety snapshot found."
    if not save_path or not os.path.isdir(save_path):
        return False, f"'{preset['name']}': save folder is not set or missing."
    entry = entries[0]
    src = undo_folder(preset, entry)
    try:
        copy_into(src, save_path, clean=bool(preset.get("clean_restore")))
    except Exception as e:
        return False, f"Undo failed: {e}"
    meta = load_meta(preset)
    stack = [e for e in (meta.get("undo_stack") or [])
             if isinstance(e, dict) and e.get("folder") != entry.get("folder")]
    meta["undo_stack"] = stack
    meta["undo"] = stack[0] if stack else None
    save_meta(preset, meta)
    shutil.rmtree(src, ignore_errors=True)
    left = len(undo_entries(preset, meta))
    tail = f" ({left} undo step(s) left)" if left else ""
    return True, f"Undid the last restore for '{preset['name']}'.{tail}"


def delete_slot(preset, slot_num):
    meta = load_meta(preset)
    target = next((s for s in meta["slots"] if s["slot"] == slot_num), None)
    if target is None:
        return False, "That quicksave no longer exists."
    meta["slots"] = [s for s in meta["slots"] if s["slot"] != slot_num]
    save_meta(preset, meta)
    _erase_slot_files(preset, target)
    return True, f"Deleted {slot_title(target)} from '{preset['name']}'."


def update_slot(preset, slot_num, **fields):
    meta = load_meta(preset)
    for s in meta["slots"]:
        if s["slot"] == slot_num:
            s.update(fields)
            save_meta(preset, meta)
            return True, s
    return False, None


def export_slot_zip(preset, slot, dest):
    folder = slot_folder(preset, slot["slot"])
    if not os.path.isdir(folder):
        return False, "Slot folder missing."
    try:
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _dirs, files in os.walk(folder):
                for fn in files:
                    full = os.path.join(root, fn)
                    z.write(full, os.path.join("save", os.path.relpath(full, folder)))
            shot = slot.get("screenshot")
            if shot:
                p = os.path.join(preset_dir(preset), shot)
                if os.path.isfile(p):
                    z.write(p, "screenshot.png")
            z.writestr("qsave_slot.json", json.dumps(
                {"game": preset["name"], "slot": slot, "exported": now_iso()}, indent=2))
        return True, f"Exported to {os.path.basename(dest)}"
    except Exception as e:
        return False, f"Export failed: {e}"


def _safe_extract_zip(zip_path, dest_dir):
    """Extract `zip_path` into `dest_dir`, refusing any entry whose path
    would land outside `dest_dir`.

    A ZIP entry name containing '..' segments or an absolute path (a
    "zip slip") can otherwise make zipfile.extractall write files anywhere
    on disk that the process has permission to touch — a real risk here
    since imported ZIPs are files the user picked up from wherever
    (a friend, a mod site, cloud storage), not something QSave produced
    itself.
    """
    dest_dir = os.path.abspath(dest_dir)
    with zipfile.ZipFile(zip_path) as z:
        for member in z.infolist():
            target_path = os.path.abspath(os.path.join(dest_dir, member.filename))
            if target_path != dest_dir and not target_path.startswith(dest_dir + os.sep):
                raise ValueError(f"Refusing to extract unsafe path in ZIP: {member.filename!r}")
        z.extractall(dest_dir)


def import_zip_as_slot(preset, zip_path):
    meta = load_meta(preset)
    slot_num = next_free_slot_number(meta["slots"])
    target = slot_folder(preset, slot_num)
    tmp = target + "__tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        _safe_extract_zip(zip_path, tmp)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return False, f"Could not read ZIP: {e}", None

    inner = os.path.join(tmp, "save")
    source = inner if os.path.isdir(inner) else tmp
    try:
        copy_tree(source, target)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return False, f"Import failed: {e}", None

    shot_rel = ""
    shot_src = os.path.join(tmp, "screenshot.png")
    if os.path.isfile(shot_src):
        os.makedirs(preset_img_dir(preset), exist_ok=True)
        shot_rel = os.path.join(IMG_SUBDIR, f"quick_{slot_num}.png")
        try:
            shutil.copy2(shot_src, os.path.join(preset_dir(preset), shot_rel))
        except Exception:
            shot_rel = ""
    shutil.rmtree(tmp, ignore_errors=True)

    size, files = dir_stats(target)
    slot = {
        "slot": slot_num, "timestamp": now_iso(), "screenshot": shot_rel,
        "label": os.path.splitext(os.path.basename(zip_path))[0][:40],
        "note": "Imported from ZIP", "pinned": True, "kind": "import",
        "size": size, "files": files,
    }
    meta["slots"].append(slot)
    save_meta(preset, meta)
    return True, f"Imported as {slot_title(slot)}", slot


def preset_usage(preset, meta=None):
    meta = meta or load_meta(preset)
    return sum(int(s.get("size") or 0) for s in meta["slots"])


def suggest_save_dirs(query, stop_event=None, limit=60):
    """Heuristic scan of common save-game roots for folders matching `query`.
    Returns (hits, candidate_roots) — candidate_roots is every folder we
    considered (whether or not it turned out to exist), so callers can show
    it for transparency/debugging when nothing turns up."""
    home = os.path.expanduser("~")
    # os.path.expanduser("~") can fail to resolve to the real profile folder
    # in some launch contexts (e.g. HOME/USERPROFILE not populated the way
    # it normally is in an interactive shell) and silently return "~"
    # unexpanded — which then makes every single root below bogus. Fall back
    # to USERPROFILE directly on Windows if that happens.
    if platform.system() == "Windows" and (not home or home == "~" or not os.path.isdir(home)):
        home = os.environ.get("USERPROFILE", home)
    system = platform.system()
    doc_candidates = []   # Documents-family roots — get a bigger scan budget
    other_candidates = []  # AppData/Local etc. — everyone else's data lives here too
    if system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        local = os.environ.get("LOCALAPPDATA", "")
        # OneDrive's "Known Folder Move" silently redirects a user's real
        # Documents folder into their OneDrive tree, which is where a lot of
        # games actually end up writing saves these days.
        onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer", "")
        # Ask Windows for the *actual current* Documents path rather than
        # assuming ~/Documents — the user may have relocated it (Properties >
        # Location), which leaves the naive guess empty even when saves are
        # sitting right there in the real Documents folder.
        documents = win_documents_dir() or os.path.join(home, "Documents")
        doc_candidates += [documents, os.path.join(documents, "My Games"),
                            os.path.join(home, "Saved Games")]
        if onedrive:
            doc_candidates += [os.path.join(onedrive, "Documents"),
                                os.path.join(onedrive, "Documents", "My Games")]
        # Also always try the conventional "<home>\OneDrive\Documents" path
        # directly, even when the OneDrive env var isn't set/visible to this
        # process — this is where OneDrive almost always lands.
        doc_candidates += [os.path.join(home, "OneDrive", "Documents"),
                            os.path.join(home, "OneDrive", "Documents", "My Games")]
        # And don't stop at the literal name "OneDrive" — work accounts get
        # folders like "OneDrive - CompanyName", and personal accounts on
        # some builds get "OneDrive - Personal". Scan the profile folder
        # directly for anything OneDrive-prefixed rather than guessing.
        try:
            with os.scandir(home) as it:
                for e in it:
                    if e.is_dir() and e.name.lower().startswith("onedrive"):
                        doc_candidates += [os.path.join(e.path, "Documents"),
                                            os.path.join(e.path, "Documents", "My Games")]
        except OSError:
            pass
        other_candidates += [appdata, local, os.path.join(local, "Low"),
                              os.path.join(home, "AppData", "Roaming")]
    elif system == "Darwin":
        doc_candidates.append(os.path.join(home, "Documents"))
        other_candidates += [os.path.join(home, "Library", "Application Support"),
                              os.path.join(home, "Library", "Containers")]
    else:
        # Some Linux setups (especially non-English locales) localize the
        # Documents folder name via xdg-user-dirs rather than using "Documents"
        # literally, so prefer that when it's set.
        xdg_docs = os.environ.get("XDG_DOCUMENTS_DIR", "") or os.path.join(home, "Documents")
        doc_candidates.append(xdg_docs)
        other_candidates += [os.path.join(home, "Games"), os.path.join(home, ".local", "share"),
                              os.path.join(home, ".config"),
                              os.path.join(home, ".steam", "steam", "steamapps", "compatdata")]

    doc_candidates = list(dict.fromkeys(r for r in doc_candidates if r))
    other_candidates = list(dict.fromkeys(r for r in other_candidates if r and r not in doc_candidates))
    doc_roots = set(doc_candidates)
    roots = doc_candidates + other_candidates

    active_roots = [r for r in roots if os.path.isdir(r)]
    tokens = [t for t in query.lower().replace("-", " ").replace("_", " ").split() if len(t) > 1]
    hits = []

    if not active_roots:
        # Report every candidate we considered, not just the ones that
        # existed — with nothing existing this is the only way to actually
        # see what was tried and debug why.
        return [], roots

    # Each root gets its own fair share of the hit/visited budget. Without
    # this, a huge folder scanned early (AppData, ~/.config, Application
    # Support — anywhere every app on the system keeps its data) can eat the
    # entire shared budget before a later root like Documents is ever looked
    # at, so it silently contributes zero results even though it's "in" the
    # roots list. Documents-related roots get a bigger share still — that's
    # where the bulk of "My Games"-style saves actually live, often nested
    # under a publisher/studio folder (Documents\Larian Studios\<Game>\...).
    base_hit_cap = max(8, -(-limit // len(active_roots)))  # ceil division
    base_visited_cap = max(2500, 14000 // len(active_roots))

    def hit_cap_for(r):
        return limit if r in doc_roots else base_hit_cap

    def visited_cap_for(r):
        return 20000 if r in doc_roots else base_visited_cap

    def walk(root, hit_cap, visited_cap):
        # Breadth-first, not depth-first. The old version recursed straight
        # down into each entry as soon as it was found, so a handful of
        # large/deep folders scanned early (a synced cloud folder, a dev
        # project, anything with thousands of files) could burn through the
        # entire visited budget before their *siblings* were ever looked at.
        # Everything after those early folders — 11th, 12th, whatever came
        # next in scan order — silently never got visited at all, regardless
        # of whether it matched the query. Scanning level-by-level instead
        # means every folder at a given depth gets checked before we dive
        # deep into any single one of them, so budget exhaustion trims off
        # unexplored depth rather than unexplored siblings.
        root_hits = []
        visited = 0
        queue = deque([(root, 0)])
        while queue:
            if (len(hits) >= limit or len(root_hits) >= hit_cap or visited > visited_cap):
                return root_hits
            if stop_event is not None and stop_event.is_set():
                return root_hits
            path, depth = queue.popleft()
            try:
                entries = os.scandir(path)
            except OSError:
                continue
            with entries:
                for e in entries:
                    visited += 1
                    if (len(hits) >= limit or len(root_hits) >= hit_cap
                            or visited > visited_cap):
                        break
                    try:
                        if not e.is_dir(follow_symlinks=False) or e.name.startswith("."):
                            continue
                    except OSError:
                        continue
                    low = e.name.lower()
                    # Require every typed word to appear, not just any single one.
                    # A loose "any token" match lets a generic short word (like
                    # "and" or "ii" in a multi-word game name) match unrelated
                    # folders all over Documents — those false positives were
                    # eating into the per-root hit budget below and starving out
                    # the real match before it was ever reached.
                    if not tokens or all(t in low for t in tokens):
                        hits.append(e.path)
                        root_hits.append(e.path)
                    if depth + 1 <= 4:
                        queue.append((e.path, depth + 1))
        return root_hits

    for r in active_roots:
        if len(hits) >= limit:
            break
        walk(r, hit_cap_for(r), visited_cap_for(r))

    def score(p):
        base = os.path.basename(p).lower()
        s = 0
        if tokens and all(t in base for t in tokens):
            s -= 10
        if any(k in p.lower() for k in ("save", "savegame", "saved")):
            s -= 6
        s += p.count(os.sep) * 0.1
        return s

    uniq = sorted(set(hits), key=score)
    return uniq[:limit], roots


# ===========================================================================
# 7. CUSTOM WIDGETS
# ===========================================================================

def round_rect(canvas, x1, y1, x2, y2, r, **kwargs):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kwargs)


class Tooltip:
    def __init__(self, widget, text, delay=520):
        self.widget, self.text, self.delay = widget, text, delay
        self.tip = None
        self._job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._job = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._job:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _show(self):
        if self.tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        except Exception:
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.overrideredirect(True)
        self.tip.configure(bg=Theme.BORDER_STRONG)
        tk.Label(self.tip, text=self.text, bg=Theme.TOAST_BG, fg=Theme.TOAST_TEXT,
                 font=Theme.F_SMALL, padx=9, pady=5, justify="left").pack(padx=1, pady=1)
        self.tip.geometry(f"+{x}+{y}")
        try:
            self.tip.attributes("-topmost", True)
        except Exception:
            pass

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


class RoundButton(tk.Canvas):
    """Flat, rounded, hover-aware button drawn on a canvas."""

    KINDS = ("primary", "secondary", "ghost", "danger", "soft")

    def __init__(self, parent, text="", command=None, kind="secondary", icon="",
                 width=None, height=34, radius=9, font=None, pad=15, bg=None,
                 tooltip=None, enabled=True):
        self._parent_bg = bg or parent.cget("background")
        super().__init__(parent, bg=self._parent_bg, highlightthickness=0, bd=0,
                         height=height, takefocus=0)
        self.command = command
        self.kind = kind if kind in self.KINDS else "secondary"
        self.radius = radius
        self.pad = pad
        self.height = height
        self.font = font or Theme.F_BODY_B
        self.icon = icon
        self.text = text
        self._enabled = enabled
        self._hover = False
        self._fixed_width = width
        self._render()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        if tooltip:
            Tooltip(self, tooltip)

    # ---------- looks ----------
    def _palette(self):
        t = Theme
        if not self._enabled:
            return t.PANEL_ALT, t.PANEL_ALT, t.TEXT_MUTED, t.TEXT_MUTED, t.BORDER
        if self.kind == "primary":
            return t.ACCENT, t.ACCENT_HOVER, t.ACCENT_TEXT, t.ACCENT_TEXT, t.ACCENT
        if self.kind == "secondary":
            return t.CARD, t.PANEL_ALT, t.TEXT, t.TEXT, t.BORDER
        if self.kind == "soft":
            return t.ACCENT_SOFT, t.ACCENT_SOFT_HOVER, t.ACCENT_STRONG, t.ACCENT_STRONG, ""
        if self.kind == "danger":
            return self._parent_bg, t.DANGER_SOFT, t.DANGER, t.DANGER, t.BORDER
        return self._parent_bg, t.PANEL_ALT, t.TEXT_SOFT, t.TEXT, ""   # ghost

    def _label(self):
        if self.icon and self.text:
            return f"{self.icon}  {self.text}"
        return self.icon or self.text

    def _render(self):
        self.delete("all")
        label = self._label()
        f = tkfont.Font(font=self.font)
        w = self._fixed_width or (f.measure(label) + self.pad * 2)
        w = max(w, self.height)
        self.configure(width=w, height=self.height, bg=self._parent_bg,
                       cursor="hand2" if self._enabled else "arrow")
        fill, hover, fg, fg_hover, outline = self._palette()
        use_fill = hover if self._hover else fill
        use_fg = fg_hover if self._hover else fg
        self._shape = round_rect(self, 1, 1, w - 1, self.height - 1, self.radius,
                                 fill=use_fill, outline=outline or use_fill)
        self._label_id = self.create_text(w / 2, self.height / 2 + 1, text=label,
                                          fill=use_fg, font=self.font)

    def update_look(self, text=None, icon=None, kind=None, enabled=None, tooltip=None):
        if text is not None:
            self.text = text
        if icon is not None:
            self.icon = icon
        if kind is not None:
            self.kind = kind
        if enabled is not None:
            self._enabled = bool(enabled)
        self._hover = False
        self._render()

    # ---------- events ----------
    def _on_enter(self, _e):
        if not self._enabled:
            return
        self._hover = True
        self._render()

    def _on_leave(self, _e):
        self._hover = False
        self._render()

    def _on_press(self, _e):
        if not self._enabled:
            return "break"
        self.move(self._label_id, 0, 1)
        # Stop the click from also reaching a parent row/card's own click
        # handler (e.g. a slot card that opens its details dialog on click) —
        # otherwise pressing a button inside a clickable row fires both.
        return "break"

    def _on_release(self, e):
        if not self._enabled:
            return "break"
        self.move(self._label_id, 0, -1)
        if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            if callable(self.command):
                self.command()
        return "break"


class DropButton(RoundButton):
    """Rounded button that opens a themed dropdown menu."""

    def __init__(self, parent, options, variable, on_change=None, **kw):
        self.options = list(options)
        self.variable = variable
        self.on_change = on_change
        kw.setdefault("kind", "secondary")
        super().__init__(parent, text=f"{variable.get()}  ▾", command=self._popup, **kw)

    def _popup(self):
        m = tk.Menu(self, tearoff=0, bg=Theme.PANEL, fg=Theme.TEXT,
                    activebackground=Theme.ACCENT, activeforeground=Theme.ACCENT_TEXT,
                    bd=0, relief="flat", font=Theme.F_BODY)
        for opt in self.options:
            m.add_command(label=opt, command=lambda o=opt: self._choose(o))
        try:
            m.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height() + 2)
        finally:
            m.grab_release()

    def _choose(self, opt):
        self.variable.set(opt)
        self.update_look(text=f"{opt}  ▾")
        if callable(self.on_change):
            self.on_change(opt)


class Switch(tk.Canvas):
    def __init__(self, parent, variable=None, command=None, width=46, height=24, bg=None):
        pbg = bg or parent.cget("background")
        super().__init__(parent, width=width, height=height, bg=pbg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.var = variable if variable is not None else tk.BooleanVar(value=False)
        self.command = command
        self.w, self.h = width, height
        self._draw()
        self.bind("<Button-1>", self._toggle)
        try:
            self.var.trace_add("write", lambda *_: self._draw())
        except Exception:
            pass

    def _draw(self):
        self.delete("all")
        on = bool(self.var.get())
        track = Theme.ACCENT if on else Theme.BORDER_STRONG
        round_rect(self, 1, 3, self.w - 1, self.h - 3, (self.h - 6) / 2,
                   fill=track, outline=track)
        r = (self.h - 10) / 2
        cx = (self.w - r - 6) if on else (r + 6)
        cy = self.h / 2
        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=Theme.CARD, outline="")

    def _toggle(self, _e=None):
        self.var.set(not bool(self.var.get()))
        self._draw()
        if callable(self.command):
            self.command(bool(self.var.get()))


class Input(tk.Frame):
    def __init__(self, parent, textvariable=None, width=22, placeholder="",
                 font=None, show=None, bg=None):
        super().__init__(parent, bg=Theme.FIELD, highlightthickness=1,
                         highlightbackground=Theme.BORDER, highlightcolor=Theme.ACCENT, bd=0)
        self.var = textvariable if textvariable is not None else tk.StringVar()
        self.placeholder = placeholder
        self._ph = False
        self.entry = tk.Entry(self, textvariable=self.var, width=width, bd=0, relief="flat",
                              bg=Theme.FIELD, fg=Theme.TEXT, insertbackground=Theme.ACCENT,
                              font=font or Theme.F_BODY, highlightthickness=0,
                              show=show or "", selectbackground=Theme.ACCENT,
                              selectforeground=Theme.ACCENT_TEXT)
        self.entry.pack(fill="both", expand=True, padx=9, pady=7)
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        if placeholder and not self.var.get():
            self._show_ph()

    def _show_ph(self):
        self._ph = True
        self.entry.configure(fg=Theme.TEXT_MUTED)
        self.var.set(self.placeholder)

    def _focus_in(self, _e=None):
        self.configure(highlightbackground=Theme.ACCENT)
        if self._ph:
            self._ph = False
            self.var.set("")
            self.entry.configure(fg=Theme.TEXT)

    def _focus_out(self, _e=None):
        self.configure(highlightbackground=Theme.BORDER)
        if self.placeholder and not self.var.get():
            self._show_ph()

    def get(self):
        return "" if self._ph else self.var.get()

    def set(self, value):
        self._ph = False
        self.entry.configure(fg=Theme.TEXT)
        self.var.set(value)
        if self.placeholder and not value:
            self._show_ph()

    def focus(self):
        self.entry.focus_set()


class ScrollArea(tk.Frame):
    def __init__(self, parent, bg=None):
        bg = bg or parent.cget("background")
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                 style="QS.Vertical.TScrollbar")
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self._on_set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _on_set(self, lo, hi):
        try:
            if float(lo) <= 0.0 and float(hi) >= 1.0:
                self.vsb.pack_forget()
            else:
                self.vsb.pack(side="right", fill="y", padx=(2, 0))
        except Exception:
            pass
        self.vsb.set(lo, hi)

    def _on_inner(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)

    def _bind_wheel(self, _e=None):
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Button-4>", self._wheel)
        self.canvas.bind_all("<Button-5>", self._wheel)

    def _unbind_wheel(self, _e=None):
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            try:
                self.canvas.unbind_all(seq)
            except Exception:
                pass

    def _wheel(self, e):
        if self.inner.winfo_reqheight() <= self.canvas.winfo_height():
            return
        if getattr(e, "num", None) == 4:
            delta = -3
        elif getattr(e, "num", None) == 5:
            delta = 3
        else:
            step = e.delta
            delta = -3 if step > 0 else 3
            if abs(step) < 100:                     # macOS trackpads
                delta = -1 if step > 0 else 1
        self.canvas.yview_scroll(delta, "units")

    def scroll_top(self):
        self.canvas.yview_moveto(0)


def chip(parent, text, fg=None, bg=None, font=None, padx=8, pady=2):
    return tk.Label(parent, text=text, fg=fg or Theme.TEXT_SOFT,
                    bg=bg or Theme.PANEL_ALT, font=font or Theme.F_TINY_B,
                    padx=padx, pady=pady)


class ToastManager:
    """Small borderless notification window that fades away."""

    def __init__(self, app):
        self.app = app
        self.win = None
        self._job = None

    def show(self, title, message="", kind="info", duration=2600):
        if not self.app.settings.get("toasts", True):
            return
        self._destroy()
        colors = {"info": Theme.ACCENT, "ok": Theme.OK,
                  "warn": Theme.WARN, "error": Theme.DANGER}
        stripe = colors.get(kind, Theme.ACCENT)

        w = tk.Toplevel(self.app)
        self.win = w
        w.overrideredirect(True)
        try:
            w.attributes("-topmost", True)
            w.attributes("-alpha", 0.0)
        except Exception:
            pass
        w.configure(bg=Theme.TOAST_BG)

        width = 360
        height = 84 if message else 60
        cv = tk.Canvas(w, width=width, height=height, bg=Theme.TOAST_BG,
                       highlightthickness=0, bd=0)
        cv.pack()
        round_rect(cv, 1, 1, width - 1, height - 1, 12, fill=Theme.TOAST_BG, outline=stripe)
        cv.create_rectangle(1, 12, 6, height - 12, fill=stripe, outline=stripe)
        cv.create_text(22, 24 if message else height / 2, anchor="w", text=title,
                       fill=Theme.TOAST_TEXT, font=Theme.F_BODY_B)
        if message:
            cv.create_text(22, 52, anchor="w", text=message[:64],
                           fill=Theme.TOAST_TEXT, font=Theme.F_SMALL)
        cv.bind("<Button-1>", lambda _e: self._destroy())

        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        pos = self.app.settings.get("toast_position", "bottom-right")
        margin = 28
        x = sw - width - margin if "right" in pos else margin
        y = sh - height - margin - 40 if "bottom" in pos else margin + 20
        if pos == "center":
            x, y = (sw - width) // 2, (sh - height) // 2
        w.geometry(f"{width}x{height}+{x}+{y}")

        self._fade(0.0, 0.96, 1)
        self._job = self.app.after(duration, lambda: self._fade(0.96, 0.0, -1))

    def _fade(self, start, end, direction):
        if self.win is None:
            return
        step = 0.12 * direction
        val = start

        def tick():
            nonlocal val
            if self.win is None:
                return
            val += step
            done = val >= end if direction > 0 else val <= end
            try:
                self.win.attributes("-alpha", max(0.0, min(0.96, val)))
            except Exception:
                done = True
            if done:
                if direction < 0:
                    self._destroy()
                return
            self.app.after(20, tick)

        tick()

    def _destroy(self):
        if self._job:
            try:
                self.app.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None


class ThumbLoader:
    """Loads & resizes screenshots on a worker thread; creates Tk images on main."""

    def __init__(self, root, max_cache=260):
        self.root = root
        self.cache = {}
        self.order = deque()
        self.max_cache = max_cache
        self._in = queue.Queue()
        self._out = queue.Queue()
        self._stop = False
        threading.Thread(target=self._worker, daemon=True).start()
        self.root.after(50, self._poll)

    def request(self, path, size, cover=True, callback=None):
        if Image is None or ImageTk is None or not path or not os.path.isfile(path):
            if callback:
                callback(None)
            return
        try:
            st = os.stat(path)
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            if callback:
                callback(None)
            return
        key = (path, size, cover, stamp)
        if key in self.cache:
            if callback:
                callback(self.cache[key])
            return
        self._in.put((key, path, size, cover, callback))

    def invalidate(self, path=None):
        # A rotated slot reuses the same image filename, so its cached
        # thumbnail has to be dropped or the gallery keeps showing the
        # previous save's picture.
        if path is None:
            self.cache.clear()
            self.order.clear()
            return
        target = os.path.abspath(path)
        for key in [k for k in list(self.cache) if os.path.abspath(k[0]) == target]:
            self.cache.pop(key, None)
        self.order = deque(k for k in self.order if k in self.cache)

    def _worker(self):
        while not self._stop:
            try:
                key, path, size, cover, cb = self._in.get(timeout=0.5)
            except queue.Empty:
                continue
            img = None
            try:
                with Image.open(path) as im:
                    im = im.convert("RGB")
                    img = fit_cover(im, size) if cover else fit_contain(im, size)
            except Exception:
                img = None
            self._out.put((key, img, cb))

    def _poll(self):
        try:
            while True:
                key, img, cb = self._out.get_nowait()
                photo = None
                if img is not None:
                    try:
                        photo = ImageTk.PhotoImage(img)
                    except Exception:
                        photo = None
                if photo is not None:
                    self.cache[key] = photo
                    self.order.append(key)
                    while len(self.order) > self.max_cache:
                        self.cache.pop(self.order.popleft(), None)
                if cb:
                    try:
                        cb(photo)
                    except Exception:
                        pass
        except queue.Empty:
            pass
        self.root.after(50, self._poll)


# ===========================================================================
# 8. DIALOG BASE + SIMPLE DIALOGS
# ===========================================================================

class Dialog(tk.Toplevel):
    def __init__(self, parent, title, resizable=False):
        super().__init__(parent)
        self.withdraw()
        self.parent = parent
        self.result = None
        self.title(f"{title} — {APP_NAME}")
        self.configure(bg=Theme.PANEL)
        self.transient(parent)
        self.resizable(resizable, resizable)
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.bind("<Escape>", lambda _e: self.on_cancel())
        # A thin accent strip along the top edge gives every dialog a
        # consistent, branded frame instead of a flat panel-coloured box.
        tk.Frame(self, bg=Theme.ACCENT, height=3).pack(fill="x")
        self.body = tk.Frame(self, bg=Theme.PANEL)
        self.body.pack(fill="both", expand=True)

    def header(self, title, subtitle=""):
        h = tk.Frame(self.body, bg=Theme.PANEL)
        h.pack(fill="x", padx=28, pady=(24, 8))
        tk.Label(h, text=title, font=Theme.F_H2, bg=Theme.PANEL, fg=Theme.TEXT).pack(anchor="w")
        if subtitle:
            tk.Label(h, text=subtitle, font=Theme.F_SMALL, bg=Theme.PANEL,
                     fg=Theme.TEXT_MUTED, justify="left", wraplength=520).pack(anchor="w", pady=(4, 0))
        tk.Frame(self.body, bg=Theme.DIVIDER, height=1).pack(fill="x", padx=28, pady=(14, 0))
        return h

    def footer(self):
        tk.Frame(self.body, bg=Theme.DIVIDER, height=1).pack(fill="x", pady=(14, 0))
        f = tk.Frame(self.body, bg=Theme.PANEL)
        f.pack(fill="x", padx=28, pady=18)
        return f

    def finish(self):
        center_on(self, self.parent)
        self.deiconify()
        try:
            self.grab_set()
        except Exception:
            pass
        self.focus_set()

    def on_cancel(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


def ask_confirm(parent, title, message, confirm_text="Confirm", danger=True, checkbox=None):
    """Themed yes/no dialog. Returns (confirmed, checkbox_value)."""
    d = Dialog(parent, title)
    d.header(title, message)
    cb_var = tk.BooleanVar(value=False)
    if checkbox:
        row = tk.Frame(d.body, bg=Theme.PANEL)
        row.pack(fill="x", padx=26, pady=(8, 0))
        Switch(row, variable=cb_var).pack(side="left")
        tk.Label(row, text=checkbox, bg=Theme.PANEL, fg=Theme.TEXT_SOFT,
                 font=Theme.F_SMALL).pack(side="left", padx=8)
    state = {"ok": False}

    def ok():
        state["ok"] = True
        d.on_cancel()

    f = d.footer()
    RoundButton(f, text=confirm_text, kind="danger" if danger else "primary",
                command=ok).pack(side="right")
    RoundButton(f, text="Cancel", kind="secondary", command=d.on_cancel).pack(side="right", padx=(0, 8))
    d.finish()
    parent.wait_window(d)
    return state["ok"], bool(cb_var.get())


def prompt_text(parent, title, message, initial="", ok_text="Save", multiline=False):
    d = Dialog(parent, title)
    d.header(title, message)
    holder = tk.Frame(d.body, bg=Theme.PANEL)
    holder.pack(fill="x", padx=26, pady=(6, 0))
    var = tk.StringVar(value=initial)
    if multiline:
        wrap = tk.Frame(holder, bg=Theme.FIELD, highlightthickness=1,
                        highlightbackground=Theme.BORDER)
        wrap.pack(fill="both", expand=True)
        txt = tk.Text(wrap, height=6, width=52, bd=0, relief="flat", bg=Theme.FIELD,
                      fg=Theme.TEXT, insertbackground=Theme.ACCENT, font=Theme.F_BODY,
                      highlightthickness=0, wrap="word")
        txt.pack(fill="both", expand=True, padx=8, pady=6)
        txt.insert("1.0", initial)
        txt.focus_set()
    else:
        inp = Input(holder, textvariable=var, width=46)
        inp.pack(fill="x")
        inp.focus()

    def ok():
        d.result = txt.get("1.0", "end").strip() if multiline else var.get().strip()
        d.on_cancel()

    f = d.footer()
    RoundButton(f, text=ok_text, kind="primary", command=ok).pack(side="right")
    RoundButton(f, text="Cancel", kind="secondary", command=d.on_cancel).pack(side="right", padx=(0, 8))
    if not multiline:
        d.bind("<Return>", lambda _e: ok())
    d.finish()
    parent.wait_window(d)
    return d.result


class HotkeyDialog(Dialog):
    """Captures a key combination using the keyboard library."""

    def __init__(self, parent, current=""):
        super().__init__(parent, "Record hotkey")
        self.header("Press a key combination",
                    "Hold modifiers (Ctrl / Alt / Shift) and press a key.\n"
                    "The combination is captured as soon as you release it.")
        self.display = tk.Label(self.body, text=current.upper() or "…", font=(Theme.BASE_FAMILY, 18, "bold"),
                                bg=Theme.ACCENT_SOFT, fg=Theme.ACCENT, padx=18, pady=14)
        self.display.pack(padx=26, pady=(10, 0), fill="x")
        f = self.footer()
        RoundButton(f, text="Cancel", kind="secondary", command=self.on_cancel).pack(side="right")
        RoundButton(f, text="Clear", kind="ghost",
                    command=lambda: self._done("")).pack(side="right", padx=(0, 8))
        self.finish()
        if keyboard is None:
            self.display.configure(text="keyboard lib missing", fg=Theme.DANGER)
        else:
            threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self):
        try:
            combo = keyboard.read_hotkey(suppress=False)
        except Exception:
            combo = None
        if combo:
            self.after(0, lambda: self._done(combo))

    def _done(self, combo):
        self.result = combo
        self.on_cancel()


def record_hotkey(parent, current=""):
    d = HotkeyDialog(parent, current)
    parent.wait_window(d)
    return d.result


class FolderScanDialog(Dialog):
    """Suggests likely save folders based on the game name."""

    def __init__(self, parent, query):
        super().__init__(parent, "Find save folder", resizable=True)
        self.query = query
        self.stop_event = threading.Event()
        self.header("Auto-detect save folder",
                    f"Scanning common save locations for “{query or 'games'}”. "
                    "Pick the folder that contains the actual save files.")
        self.status = tk.Label(self.body, text="Scanning…", bg=Theme.PANEL,
                               fg=Theme.TEXT_MUTED, font=Theme.F_SMALL)
        self.status.pack(anchor="w", padx=26)
        wrap = tk.Frame(self.body, bg=Theme.PANEL)
        wrap.pack(fill="both", expand=True, padx=26, pady=(8, 0))
        self.area = ScrollArea(wrap, bg=Theme.PANEL)
        self.area.pack(fill="both", expand=True)
        self.geometry("720x520")
        f = self.footer()
        RoundButton(f, text="Cancel", kind="secondary", command=self.on_cancel).pack(side="right")
        self.finish()
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        try:
            results, scanned_roots = suggest_save_dirs(self.query, self.stop_event)
        except Exception as e:
            # A silent crash here previously meant the dialog just sat at
            # "Scanning…" forever with no results and no explanation — show
            # the actual error instead so this is never a dead end.
            self.after(0, lambda err=e: self._show_error(err))
            return
        self.after(0, lambda: self._show(results, scanned_roots))

    def _show_error(self, err):
        if not self.winfo_exists():
            return
        self.status.configure(text="Scan couldn't finish — see details below.")
        tk.Label(self.area.inner, text=f"Auto-detect ran into an error:\n{err}",
                 bg=Theme.PANEL, fg=Theme.TEXT_MUTED, font=Theme.F_BODY,
                 justify="left", wraplength=640).pack(anchor="w", pady=12)
        self.area.inner.update_idletasks()
        self.area.canvas.configure(scrollregion=self.area.canvas.bbox("all"))

    def _show(self, results, scanned_roots=None):
        if not self.winfo_exists():
            return
        self.status.configure(text=f"{len(results)} candidate folder(s) found — click one to use it.")
        if not results:
            tk.Label(self.area.inner, text="Nothing obvious found. Use “Browse…” instead.",
                     bg=Theme.PANEL, fg=Theme.TEXT_MUTED, font=Theme.F_BODY).pack(anchor="w", pady=12)
            if scanned_roots:
                tk.Label(self.area.inner, text="Folders checked:", bg=Theme.PANEL,
                         fg=Theme.TEXT_MUTED, font=Theme.F_SMALL).pack(anchor="w", pady=(10, 2))
                for r in scanned_roots:
                    mark = "✓ found  " if os.path.isdir(r) else "✗ not found  "
                    tk.Label(self.area.inner, text=mark + r, bg=Theme.PANEL, fg=Theme.TEXT_SOFT,
                             font=Theme.F_TINY, anchor="w", justify="left").pack(anchor="w")
            self.area.inner.update_idletasks()
            self.area.canvas.configure(scrollregion=self.area.canvas.bbox("all"))
            return
        for path in results:
            size, files = 0, 0
            try:
                with os.scandir(path) as it:
                    for e in it:
                        if e.is_file():
                            files += 1
                            try:
                                size += e.stat().st_size
                            except OSError:
                                pass
            except OSError:
                pass
            row = tk.Frame(self.area.inner, bg=Theme.CARD, highlightthickness=1,
                           highlightbackground=Theme.BORDER, cursor="hand2")
            row.pack(fill="x", pady=3, padx=2)
            inner = tk.Frame(row, bg=Theme.CARD)
            inner.pack(fill="x", padx=12, pady=9)
            tk.Label(inner, text=os.path.basename(path) or path, bg=Theme.CARD, fg=Theme.TEXT,
                     font=Theme.F_BODY_B, anchor="w").pack(anchor="w")
            tk.Label(inner, text=path, bg=Theme.CARD, fg=Theme.TEXT_MUTED,
                     font=Theme.F_TINY, anchor="w").pack(anchor="w")
            tk.Label(inner, text=f"{files} file(s) directly inside · {human_size(size)}",
                     bg=Theme.CARD, fg=Theme.TEXT_SOFT, font=Theme.F_TINY).pack(anchor="w", pady=(2, 0))
            bind_tree(row, "<Button-1>", lambda _e, p=path: self._pick(p))
            bind_tree(row, "<Enter>", lambda _e, r=row: r.configure(highlightbackground=Theme.ACCENT))
            bind_tree(row, "<Leave>", lambda _e, r=row: r.configure(highlightbackground=Theme.BORDER))

        # Without this, rows built into a ScrollArea can sit outside its
        # canvas's scrollregion until some unrelated event forces a redraw —
        # the same issue that made the sidebar appear empty at first launch.
        # This dialog is a fresh window every time it opens, so force it here.
        self.area.inner.update_idletasks()
        self.area.canvas.configure(scrollregion=self.area.canvas.bbox("all"))

    def _pick(self, path):
        self.result = path
        self.on_cancel()

    def on_cancel(self):
        self.stop_event.set()
        super().on_cancel()


# ===========================================================================
# 9. PRESET DIALOG
# ===========================================================================

class PresetDialog(Dialog):
    def __init__(self, parent, on_save, preset=None, existing=None):
        super().__init__(parent, "Edit game" if preset else "Add game")
        self.on_save = on_save
        self.preset = preset
        self.existing = existing or []

        self.name_var = tk.StringVar(value=preset["name"] if preset else "")
        self.path_var = tk.StringVar(value=preset["save_path"] if preset else "")
        self.max_var = tk.IntVar(value=preset["max_saves"] if preset else DEFAULT_MAX_SAVES)
        self.save_key_var = tk.StringVar(value=preset["save_key"] if preset else DEFAULT_SAVE_KEY)
        self.restore_key_var = tk.StringVar(value=preset["restore_key"] if preset else DEFAULT_RESTORE_KEY)
        self.exclude_var = tk.StringVar(value=preset.get("exclude", "") if preset else "")
        self.color_var = tk.StringVar(value=preset.get("color", PRESET_COLORS[0]) if preset else PRESET_COLORS[0])
        self.enabled_var = tk.BooleanVar(value=preset.get("enabled", True) if preset else True)
        self.clean_var = tk.BooleanVar(value=preset.get("clean_restore", False) if preset else False)

        global_autosave = bool(getattr(self.parent, "settings", {}).get("autosave_enabled", False))
        self.autosave_var = tk.BooleanVar(
            value=preset.get("autosave", global_autosave) if preset else global_autosave)
        self.autosave_minutes_var = tk.IntVar(value=int(preset.get("autosave_minutes", 0)) if preset else 0)
        self.autosave_max_var = tk.IntVar(value=int(preset.get("autosave_max", 0)) if preset else 0)
        self.exe_var = tk.StringVar(value=preset.get("game_exe", "") if preset else "")

        self.header("Game preset",
                    "QSave copies everything inside the save folder into rotating slots.")
        self._build()
        self.finish()

    # ---------- ui helpers ----------
    def _section(self, title):
        f = tk.Frame(self.body, bg=Theme.PANEL)
        f.pack(fill="x", padx=28, pady=(16, 0))
        tk.Label(f, text=title.upper(), bg=Theme.PANEL, fg=Theme.ACCENT,
                 font=Theme.F_EYEBROW).pack(anchor="w", pady=(0, 7))
        return f

    def _row(self, parent, label, hint=""):
        r = tk.Frame(parent, bg=Theme.PANEL)
        r.pack(fill="x", pady=4)
        lab = tk.Frame(r, bg=Theme.PANEL, width=150)
        lab.pack(side="left", fill="y")
        lab.pack_propagate(False)
        tk.Label(lab, text=label, bg=Theme.PANEL, fg=Theme.TEXT_SOFT,
                 font=Theme.F_BODY, anchor="w").pack(anchor="w", pady=(6, 0))
        right = tk.Frame(r, bg=Theme.PANEL)
        right.pack(side="left", fill="x", expand=True)
        if hint:
            Tooltip(lab, hint)
        return right

    def _build(self):
        # --- identity ---
        s = self._section("Identity")
        r = self._row(s, "Game name")
        Input(r, textvariable=self.name_var, width=34).pack(side="left", fill="x", expand=True)

        r = self._row(s, "Colour tag")
        sw = tk.Frame(r, bg=Theme.PANEL)
        sw.pack(side="left", pady=3)
        self._swatches = {}
        for c in PRESET_COLORS:
            cv = tk.Canvas(sw, width=26, height=26, bg=Theme.PANEL, highlightthickness=0,
                           bd=0, cursor="hand2")
            cv.pack(side="left", padx=2)
            cv.bind("<Button-1>", lambda _e, col=c: self._set_color(col))
            self._swatches[c] = cv
        self._paint_swatches()

        # --- location ---
        s = self._section("Save location")
        r = self._row(s, "Folder", "The folder the game writes its saves into.")
        Input(r, textvariable=self.path_var, width=30).pack(side="left", fill="x", expand=True)
        RoundButton(r, text="Browse…", kind="secondary", height=32,
                    command=self._browse).pack(side="left", padx=(6, 0))
        RoundButton(r, text="Auto-detect", kind="soft", height=32,
                    command=self._autodetect).pack(side="left", padx=(6, 0))

        self.path_info = tk.Label(s, text="", bg=Theme.PANEL, fg=Theme.TEXT_MUTED,
                                  font=Theme.F_TINY, anchor="w")
        self.path_info.pack(fill="x", pady=(4, 0))
        self.path_var.trace_add("write", lambda *_: self._update_path_info())
        self._update_path_info()

        r = self._row(s, "Exclude patterns", "Semicolon separated globs, e.g.  *.log; cache*")
        Input(r, textvariable=self.exclude_var, width=34).pack(side="left", fill="x", expand=True)

        # --- rotation & hotkeys ---
        s = self._section("Rotation & hotkeys")
        r = self._row(s, "Rotating slots")
        sp = tk.Spinbox(r, from_=1, to=99, textvariable=self.max_var, width=5,
                        font=Theme.F_BODY, relief="flat", bg=Theme.FIELD, fg=Theme.TEXT,
                        buttonbackground=Theme.PANEL_ALT, highlightthickness=1,
                        highlightbackground=Theme.BORDER, insertbackground=Theme.TEXT)
        sp.pack(side="left", pady=3)
        tk.Label(r, text="  pinned saves and archives never count against this limit",
                 bg=Theme.PANEL, fg=Theme.TEXT_MUTED, font=Theme.F_TINY).pack(side="left")

        r = self._row(s, "Quicksave key")
        Input(r, textvariable=self.save_key_var, width=16).pack(side="left")
        RoundButton(r, text="Record", kind="secondary", height=32,
                    command=lambda: self._record(self.save_key_var)).pack(side="left", padx=6)

        r = self._row(s, "Restore-latest key")
        Input(r, textvariable=self.restore_key_var, width=16).pack(side="left")
        RoundButton(r, text="Record", kind="secondary", height=32,
                    command=lambda: self._record(self.restore_key_var)).pack(side="left", padx=6)

        r = self._row(s, "Hotkeys active")
        Switch(r, variable=self.enabled_var).pack(side="left", pady=3)
        tk.Label(r, text="  turn off to keep the preset but silence its hotkeys",
                 bg=Theme.PANEL, fg=Theme.TEXT_MUTED, font=Theme.F_TINY).pack(side="left")

        r = self._row(s, "Clean restore")
        Switch(r, variable=self.clean_var).pack(side="left", pady=3)
        tk.Label(r, text="  wipe the save folder before restoring (exact match, riskier)",
                 bg=Theme.PANEL, fg=Theme.TEXT_MUTED, font=Theme.F_TINY).pack(side="left")

        # --- autosave & launch ---
        s = self._section("Autosave & launch")
        r = self._row(s, "Autosave this game",
                     "Independent of the global switch: turning this on autosaves this game even if the "
                     "global autosave is off, and turning it off keeps this game out of autosave even if "
                     "the global autosave is on.")
        Switch(r, variable=self.autosave_var, command=lambda _v=None: self._toggle_autosave_extra()).pack(
              side="left", pady=3)
        global_state = "on" if getattr(self.parent, "settings", {}).get("autosave_enabled", False) else "off"
        tk.Label(r, text=f"  global autosave is currently {global_state} (Settings → Safety)",
                bg=Theme.PANEL, fg=Theme.TEXT_MUTED, font=Theme.F_TINY).pack(side="left")

        global_minutes = int(getattr(self.parent, "settings", {}).get("autosave_minutes", 10))
        global_max = int(getattr(self.parent, "settings", {}).get("autosave_max_default", 5))
        self.autosave_extra = tk.Frame(s, bg=Theme.PANEL)

        r2 = self._row(self.autosave_extra, "Autosave interval",
                      "How often this game autosaves while it's the selected game. "
                      "0 uses the global interval from Settings → Safety.")
        tk.Spinbox(r2, from_=0, to=1440, textvariable=self.autosave_minutes_var, width=6,
                  font=Theme.F_BODY, relief="flat", bg=Theme.FIELD, fg=Theme.TEXT,
                  buttonbackground=Theme.PANEL_ALT, highlightthickness=1,
                  highlightbackground=Theme.BORDER, insertbackground=Theme.TEXT).pack(side="left", pady=3)
        tk.Label(r2, text=f"  minutes  ·  0 = use global default ({global_minutes} min)",
                bg=Theme.PANEL, fg=Theme.TEXT_MUTED, font=Theme.F_TINY).pack(side="left")

        r3 = self._row(self.autosave_extra, "Maximum autosaves",
                      "Caps how many autosave slots this game keeps before the oldest is recycled — "
                      "separate from the rotating-slots limit above, so autosaves don't crowd out your "
                      "manual quicksaves. 0 uses the global default.")
        tk.Spinbox(r3, from_=0, to=99, textvariable=self.autosave_max_var, width=6,
                  font=Theme.F_BODY, relief="flat", bg=Theme.FIELD, fg=Theme.TEXT,
                  buttonbackground=Theme.PANEL_ALT, highlightthickness=1,
                  highlightbackground=Theme.BORDER, insertbackground=Theme.TEXT).pack(side="left", pady=3)
        tk.Label(r3, text=f"  autosave(s)  ·  0 = use global default ({global_max})",
                bg=Theme.PANEL, fg=Theme.TEXT_MUTED, font=Theme.F_TINY).pack(side="left")

        self._toggle_autosave_extra()

        r = self._row(s, "Game executable", "Optional — QSave will pop back up automatically when this .exe starts running.")
        Input(r, textvariable=self.exe_var, width=26).pack(side="left", fill="x", expand=True)
        RoundButton(r, text="Browse…", kind="secondary", height=32,
                    command=self._browse_exe).pack(side="left", padx=(6, 0))
        RoundButton(r, text="Clear", kind="ghost", height=32,
                    command=lambda: self.exe_var.set("")).pack(side="left", padx=(6, 0))

        if keyboard is None:
            tk.Label(self.body, text="⚠  keyboard module missing — hotkeys cannot be recorded or used.",
                     bg=Theme.PANEL, fg=Theme.DANGER, font=Theme.F_SMALL).pack(anchor="w", padx=26, pady=(10, 0))
        if psutil is None:
            tk.Label(self.body, text="⚠  psutil module missing — QSave can't detect the game launching "
                                     "(pip install psutil).",
                     bg=Theme.PANEL, fg=Theme.DANGER, font=Theme.F_SMALL).pack(anchor="w", padx=26, pady=(4, 0))

        f = self.footer()
        RoundButton(f, text="Save preset", kind="primary", command=self._save).pack(side="right")
        RoundButton(f, text="Cancel", kind="secondary",
                    command=self.on_cancel).pack(side="right", padx=(0, 8))

    # ---------- actions ----------
    def _paint_swatches(self):
        for c, cv in self._swatches.items():
            cv.delete("all")
            sel = (c == self.color_var.get())
            round_rect(cv, 2, 2, 24, 24, 7, fill=c, outline=Theme.TEXT if sel else c, width=2)
            if sel:
                cv.create_text(13, 13, text="✓", fill="#FFFFFF", font=Theme.F_SMALL_B)

    def _set_color(self, c):
        self.color_var.set(c)
        self._paint_swatches()

    def _toggle_autosave_extra(self, *_):
        if self.autosave_var.get():
            self.autosave_extra.pack(fill="x")
        else:
            self.autosave_extra.pack_forget()

    def _update_path_info(self):
        p = self.path_var.get().strip()
        if not p:
            self.path_info.configure(text="No folder selected yet.", fg=Theme.TEXT_MUTED)
        elif not os.path.isdir(p):
            self.path_info.configure(text="⚠  This folder does not exist.", fg=Theme.DANGER)
        else:
            def work():
                size, files = dir_stats(p, parse_excludes(self.exclude_var.get()))
                txt = f"✓  {files} file(s) · {human_size(size)} will be copied per slot"
                if self.winfo_exists():
                    self.after(0, lambda: self.path_info.configure(text=txt, fg=Theme.OK))
            threading.Thread(target=work, daemon=True).start()

    def _browse(self):
        d = filedialog.askdirectory(title="Select the game's save folder", parent=self)
        if d:
            self.path_var.set(d)

    def _autodetect(self):
        dlg = FolderScanDialog(self, self.name_var.get().strip())
        self.wait_window(dlg)
        if dlg.result:
            self.path_var.set(dlg.result)

    def _browse_exe(self):
        filetypes = [("Executable", "*.exe"), ("All files", "*.*")] if platform.system() == "Windows" \
            else [("All files", "*.*")]
        f = filedialog.askopenfilename(title="Select the game's executable", parent=self, filetypes=filetypes)
        if f:
            self.exe_var.set(f)

    def _record(self, var):
        combo = record_hotkey(self, var.get())
        if combo is not None:
            var.set(combo)

    def _save(self):
        name = self.name_var.get().strip()
        path = self.path_var.get().strip()
        try:
            max_saves = int(self.max_var.get())
        except Exception:
            messagebox.showerror("Invalid input", "Rotating slots must be a number.", parent=self)
            return
        try:
            autosave_minutes = max(0, int(self.autosave_minutes_var.get()))
            autosave_max = max(0, int(self.autosave_max_var.get()))
        except Exception:
            messagebox.showerror("Invalid input", "Autosave interval/max must be numbers.", parent=self)
            return
        save_key = self.save_key_var.get().strip().lower()
        restore_key = self.restore_key_var.get().strip().lower()

        if not name:
            messagebox.showerror("Missing name", "Please name this game preset.", parent=self)
            return
        if not path or not os.path.isdir(path):
            messagebox.showerror("Invalid folder", "Choose an existing save folder.", parent=self)
            return
        if max_saves < 1:
            messagebox.showerror("Invalid input", "Keep at least one rotating slot.", parent=self)
            return
        old_name = self.preset["name"] if self.preset else None
        others = [n for n in self.existing if n != old_name]
        if name in others:
            messagebox.showerror("Duplicate name", "A preset with that name already exists.", parent=self)
            return
        if sanitize(name) in {sanitize(n) for n in others}:
            # Different display names can still collide once sanitized down
            # to a folder name (e.g. "Save: A" and "Save/A" both become
            # "Save_ A"/"Save_A"-like strings) — presets are stored on disk
            # under sanitize(name), so an unnoticed collision here would
            # mean two presets silently sharing (and corrupting) the same
            # folder and metadata.
            messagebox.showerror(
                "Name too similar",
                "That name looks the same as an existing preset once turned into a folder "
                "name (punctuation is simplified). Please pick something more distinct.",
                parent=self)
            return
        if save_key and save_key == restore_key:
            messagebox.showerror("Hotkey clash", "Quicksave and restore keys must differ.", parent=self)
            return

        result = normalize_preset({
            "id": self.preset["id"] if self.preset else uuid.uuid4().hex[:10],
            "name": name,
            "save_path": path,
            "max_saves": max_saves,
            "save_key": save_key,
            "restore_key": restore_key,
            "color": self.color_var.get(),
            "exclude": self.exclude_var.get().strip(),
            "enabled": bool(self.enabled_var.get()),
            "clean_restore": bool(self.clean_var.get()),
            "autosave": bool(self.autosave_var.get()),
            "autosave_minutes": autosave_minutes,
            "autosave_max": autosave_max,
            "game_exe": self.exe_var.get().strip(),
            "notes": self.preset.get("notes", "") if self.preset else "",
            "created": self.preset.get("created", now_iso()) if self.preset else now_iso(),
            "stats": self.preset.get("stats") if self.preset else None,
        }, getattr(self.parent, "settings", None))
        self.on_save(result, old_name)
        self.on_cancel()


# ===========================================================================
# 10. SETTINGS DIALOG
# ===========================================================================

class Tabs(tk.Frame):
    def __init__(self, parent, names, bg=None):
        bg = bg or parent.cget("background")
        super().__init__(parent, bg=bg)
        self.bar = tk.Frame(self, bg=bg)
        self.bar.pack(fill="x")
        tk.Frame(self, bg=Theme.DIVIDER, height=1).pack(fill="x", pady=(8, 0))
        self.holder = tk.Frame(self, bg=bg)
        self.holder.pack(fill="both", expand=True, pady=(12, 0))
        self.frames, self.buttons = {}, {}
        for n in names:
            b = RoundButton(self.bar, text=n, kind="ghost", height=30,
                            command=lambda nn=n: self.show(nn))
            b.pack(side="left", padx=(0, 4))
            self.buttons[n] = b
            self.frames[n] = tk.Frame(self.holder, bg=bg)
        self.current = None
        self.show(names[0])

    def show(self, name):
        for n, f in self.frames.items():
            f.pack_forget()
            self.buttons[n].update_look(kind="ghost")
        self.frames[name].pack(fill="both", expand=True)
        self.buttons[name].update_look(kind="soft")
        self.current = name


class SettingsDialog(Dialog):
    def __init__(self, parent, app):
        super().__init__(parent, "Settings")
        self.app = app
        s = dict(app.settings)
        self.vars = {
            "sounds": tk.BooleanVar(value=s["sounds"]),
            "toasts": tk.BooleanVar(value=s["toasts"]),
            "toast_position": tk.StringVar(value=s["toast_position"]),
            "screenshots": tk.BooleanVar(value=s["screenshots"]),
            "hide_window_for_capture": tk.BooleanVar(value=s["hide_window_for_capture"]),
            "capture_delay_ms": tk.IntVar(value=s["capture_delay_ms"]),
            "screenshot_max_width": tk.IntVar(value=s["screenshot_max_width"]),
            "confirm_restore": tk.BooleanVar(value=s["confirm_restore"]),
            "confirm_delete": tk.BooleanVar(value=s["confirm_delete"]),
            "safety_snapshot": tk.BooleanVar(value=s["safety_snapshot"]),
            "autosave_enabled": tk.BooleanVar(value=s["autosave_enabled"]),
            "autosave_minutes": tk.IntVar(value=s["autosave_minutes"]),
            "autosave_max_default": tk.IntVar(value=s.get("autosave_max_default", 5)),
            "ignore_when_focused": tk.BooleanVar(value=s["ignore_when_focused"]),
            "always_on_top": tk.BooleanVar(value=s["always_on_top"]),
            "close_to_tray": tk.BooleanVar(value=s["close_to_tray"]),
            "hotkeys_paused": tk.BooleanVar(value=s["hotkeys_paused"]),
        }
        self.gh = {k: tk.StringVar(value=v) for k, v in s["global_hotkeys"].items()}

        self.header("Settings", "Everything is stored next to the script in qsave_config.json.")
        wrap = tk.Frame(self.body, bg=Theme.PANEL)
        wrap.pack(fill="both", expand=True, padx=26, pady=(6, 0))
        self.tabs = Tabs(wrap, ["Appearance", "Capture", "Hotkeys", "Safety"], bg=Theme.PANEL)
        self.tabs.pack(fill="both", expand=True)
        self._appearance(self.tabs.frames["Appearance"])
        self._capture(self.tabs.frames["Capture"])
        self._hotkeys(self.tabs.frames["Hotkeys"])
        self._safety(self.tabs.frames["Safety"])

        f = self.footer()
        RoundButton(f, text="Apply", kind="primary", command=self._apply).pack(side="right")
        RoundButton(f, text="Cancel", kind="secondary",
                    command=self.on_cancel).pack(side="right", padx=(0, 8))
        RoundButton(f, text="Open config folder", kind="ghost",
                    command=lambda: open_in_file_manager(APP_DIR)).pack(side="left")
        self.finish()

    # ---------- rows ----------
    def _toggle_row(self, parent, key, title, desc=""):
        r = tk.Frame(parent, bg=Theme.PANEL)
        r.pack(fill="x", pady=6)
        Switch(r, variable=self.vars[key]).pack(side="left")
        txt = tk.Frame(r, bg=Theme.PANEL)
        txt.pack(side="left", padx=10, fill="x", expand=True)
        tk.Label(txt, text=title, bg=Theme.PANEL, fg=Theme.TEXT,
                 font=Theme.F_BODY_B, anchor="w").pack(anchor="w")
        if desc:
            tk.Label(txt, text=desc, bg=Theme.PANEL, fg=Theme.TEXT_MUTED,
                     font=Theme.F_TINY, anchor="w", justify="left").pack(anchor="w")

    def _num_row(self, parent, key, title, lo, hi, suffix=""):
        r = tk.Frame(parent, bg=Theme.PANEL)
        r.pack(fill="x", pady=6)
        tk.Spinbox(r, from_=lo, to=hi, textvariable=self.vars[key], width=7,
                   font=Theme.F_BODY, relief="flat", bg=Theme.FIELD, fg=Theme.TEXT,
                   buttonbackground=Theme.PANEL_ALT, highlightthickness=1,
                   highlightbackground=Theme.BORDER, insertbackground=Theme.TEXT).pack(side="left")
        tk.Label(r, text=f"  {title} {suffix}", bg=Theme.PANEL, fg=Theme.TEXT,
                 font=Theme.F_BODY).pack(side="left")

    def _hotkey_row(self, parent, key, title):
        r = tk.Frame(parent, bg=Theme.PANEL)
        r.pack(fill="x", pady=5)
        lab = tk.Frame(r, bg=Theme.PANEL, width=210)
        lab.pack(side="left", fill="y")
        lab.pack_propagate(False)
        tk.Label(lab, text=title, bg=Theme.PANEL, fg=Theme.TEXT_SOFT,
                 font=Theme.F_BODY, anchor="w").pack(anchor="w", pady=(6, 0))
        Input(r, textvariable=self.gh[key], width=20).pack(side="left")
        RoundButton(r, text="Record", kind="secondary", height=32,
                    command=lambda k=key: self._rec(k)).pack(side="left", padx=6)

    def _rec(self, key):
        combo = record_hotkey(self, self.gh[key].get())
        if combo is not None:
            self.gh[key].set(combo)

    # ---------- tabs ----------
    def _appearance(self, p):
        self._toggle_row(p, "toasts", "On-screen notifications",
                         "Floating confirmation popups — visible even over windowed games.")
        r = tk.Frame(p, bg=Theme.PANEL)
        r.pack(fill="x", pady=4)
        tk.Label(r, text="Notification position", bg=Theme.PANEL, fg=Theme.TEXT_SOFT,
                 font=Theme.F_BODY).pack(side="left")
        DropButton(r, ["top-left", "top-right", "bottom-left", "bottom-right", "center"],
                   self.vars["toast_position"], height=30).pack(side="left", padx=10)
        self._toggle_row(p, "sounds", "Sound feedback", "Distinct chimes for save, restore and errors.")
        self._toggle_row(p, "always_on_top", "Keep QSave window on top")
        if pystray is not None and Image is not None:
            self._toggle_row(p, "close_to_tray", "Close button minimises to the system tray")

    def _capture(self, p):
        self._toggle_row(p, "screenshots", "Capture a screenshot with every save",
                         "Screenshots make it far easier to recognise a save later.")
        self._toggle_row(p, "hide_window_for_capture", "Hide the QSave window before capturing",
                         "Prevents capturing QSave itself instead of the game.")
        self._num_row(p, "capture_delay_ms", "delay before capture", 0, 3000, "ms")
        self._num_row(p, "screenshot_max_width", "max screenshot width", 320, 3840, "px")
        if ImageGrab is None:
            tk.Label(p, text="⚠  Pillow is not installed — screenshots are unavailable.",
                     bg=Theme.PANEL, fg=Theme.DANGER, font=Theme.F_SMALL).pack(anchor="w", pady=8)

    def _hotkeys(self, p):
        tk.Label(p, text="These work for whichever game is currently selected.",
                 bg=Theme.PANEL, fg=Theme.TEXT_MUTED, font=Theme.F_SMALL).pack(anchor="w", pady=(0, 6))
        self._hotkey_row(p, "quicksave_active", "Quicksave active game")
        self._hotkey_row(p, "restore_active", "Restore latest (active game)")
        self._hotkey_row(p, "rewind", "Rewind — restore older save")
        self._hotkey_row(p, "forward", "Forward — restore newer save")
        self._hotkey_row(p, "pause_toggle", "Pause / resume all hotkeys")
        tk.Frame(p, bg=Theme.BORDER, height=1).pack(fill="x", pady=10)
        self._toggle_row(p, "ignore_when_focused", "Ignore hotkeys while the QSave window has focus",
                         "So typing End / Insert inside QSave does not trigger a save.")
        self._toggle_row(p, "hotkeys_paused", "Hotkeys paused")

    def _safety(self, p):
        self._toggle_row(p, "safety_snapshot", "Snapshot the live save before restoring",
                         "Enables the Undo Restore button — highly recommended.")
        self._toggle_row(p, "confirm_restore", "Ask before restoring (button clicks only)")
        self._toggle_row(p, "confirm_delete", "Ask before deleting a save")
        tk.Frame(p, bg=Theme.BORDER, height=1).pack(fill="x", pady=10)
        self._toggle_row(p, "autosave_enabled", "Autosave — default for new presets",
                         "Each game preset has its own autosave switch (Add/Edit game). This only decides "
                         "what a brand-new preset starts with; it does not affect existing presets.")
        self._num_row(p, "autosave_minutes", "autosave interval", 1, 240, "minutes")
        self._num_row(p, "autosave_max_default", "maximum autosaves to keep", 1, 99)
        tk.Label(p, text="Per-game overrides for both of these live on each game's Add/Edit screen "
                        "(0 there means \"use this default\").",
                bg=Theme.PANEL, fg=Theme.TEXT_MUTED, font=Theme.F_TINY,
                justify="left", wraplength=440).pack(anchor="w", pady=(4, 0))

    def _apply(self):
        for k, v in self.vars.items():
            try:
                self.app.settings[k] = v.get()
            except Exception:
                pass
        self.app.settings["global_hotkeys"] = {k: v.get().strip().lower() for k, v in self.gh.items()}
        self.app.save_all()
        self.app.apply_window_flags()
        self.app.rebind_hotkeys()
        self.app.refresh_pills()
        self.app.render_content()
        self.app.toast.show("Settings applied", kind="ok")
        self.on_cancel()


class AboutDialog(Dialog):
    def __init__(self, parent):
        super().__init__(parent, "About")
        self.header(APP_NAME, "Quick Save Manager for Games")

        body = tk.Frame(self.body, bg=Theme.PANEL)
        body.pack(fill="both", expand=True, padx=26, pady=(6, 4))

        tk.Label(body, text=f"Version {APP_VERSION}", bg=Theme.PANEL, fg=Theme.TEXT,
                font=Theme.F_BODY_B, anchor="w").pack(anchor="w", pady=(0, 14))

        tk.Label(body, text="DEVELOPER", bg=Theme.PANEL, fg=Theme.TEXT_MUTED,
                font=Theme.F_TINY_B, anchor="w").pack(anchor="w")
        tk.Label(body, text=f"{APP_DEVELOPER_GITHUB}  (GitHub)", bg=Theme.PANEL, fg=Theme.TEXT,
                font=Theme.F_BODY, anchor="w").pack(anchor="w", pady=(3, 0))
        tk.Label(body, text=f"{APP_DEVELOPER_NEXUSMODS}  (NexusMods)", bg=Theme.PANEL, fg=Theme.TEXT,
                font=Theme.F_BODY, anchor="w").pack(anchor="w")

        tk.Frame(body, bg=Theme.BORDER, height=1).pack(fill="x", pady=(16, 10))
        tk.Label(body, text="All rights reserved.", bg=Theme.PANEL, fg=Theme.TEXT_MUTED,
                font=Theme.F_TINY, anchor="w").pack(anchor="w", pady=(0, 6))

        f = self.footer()
        RoundButton(f, text="Close", kind="primary", command=self.on_cancel).pack(side="right")
        self.finish()


class DonateDialog(Dialog):
    """A warm, low-pressure "support the developer" dialog.

    Every provider card is generated from DONATE_OPTIONS (near the top of
    the file) — add another {icon, label, network, note, address} dict
    there and a matching card shows up here automatically. Cards may also
    include a "pay_links" list, which renders as one button per wallet
    site (name only, never the raw URL) that opens straight to a pre-filled
    payment screen for this address.
    """

    def __init__(self, parent):
        super().__init__(parent, "Support QSave Pro")
        self.header(
            "Enjoying QSave Pro? ♥",
            "QSave Pro is free and built solo in spare time. If it's saved your "
            "progress (and your sanity) once or twice, a small donation goes a "
            "long way — completely optional, and never required to use the app.",
        )

        body = tk.Frame(self.body, bg=Theme.PANEL)
        body.pack(fill="both", expand=True, padx=26, pady=(12, 4))

        tk.Label(body, text="WAYS TO DONATE", bg=Theme.PANEL, fg=Theme.TEXT_MUTED,
                 font=Theme.F_TINY_B, anchor="w").pack(anchor="w", pady=(0, 8))

        if DONATE_OPTIONS:
            for opt in DONATE_OPTIONS:
                self._provider_card(body, opt)
        else:
            tk.Label(body, text="No donation methods are configured right now.",
                     bg=Theme.PANEL, fg=Theme.TEXT_MUTED, font=Theme.F_BODY,
                     anchor="w").pack(anchor="w")

        tk.Label(body, text="Crypto is offered because it reaches the developer "
                             "directly from anywhere, with no card networks or "
                             "banking rails in between. The wallet buttons above open "
                             "that wallet's own app or website in your browser, with "
                             "this address already filled in — nothing here ever asks "
                             "for your keys or seed phrase. Double-check the address "
                             "and network before sending, since transfers can't be reversed.",
                 bg=Theme.PANEL, fg=Theme.TEXT_MUTED, font=Theme.F_TINY,
                 justify="left", wraplength=460).pack(anchor="w", pady=(6, 0))

        f = self.footer()
        tk.Label(f, text="Thank you for even considering it — truly.", bg=Theme.PANEL,
                 fg=Theme.ACCENT_STRONG, font=Theme.F_SMALL_B).pack(side="left")
        RoundButton(f, text="Close", kind="primary", command=self.on_cancel).pack(side="right")
        self.finish()

    def _provider_card(self, parent, opt):
        card = tk.Frame(parent, bg=Theme.CARD, highlightthickness=1,
                         highlightbackground=Theme.BORDER, bd=0)
        card.pack(fill="x", pady=(0, 12))
        inner = tk.Frame(card, bg=Theme.CARD)
        inner.pack(fill="x", padx=16, pady=14)

        top = tk.Frame(inner, bg=Theme.CARD)
        top.pack(fill="x")
        badge = tk.Canvas(top, width=34, height=34, bg=Theme.CARD, highlightthickness=0)
        badge.pack(side="left", padx=(0, 12))
        round_rect(badge, 1, 1, 33, 33, 10, fill=Theme.ACCENT_SOFT, outline="")
        badge.create_text(17, 18, text=opt.get("icon", "₮"), fill=Theme.ACCENT_STRONG,
                          font=(Theme.BASE_FAMILY, 14, "bold"))
        text_col = tk.Frame(top, bg=Theme.CARD)
        text_col.pack(side="left", fill="x", expand=True)
        tk.Label(text_col, text=opt["label"], bg=Theme.CARD, fg=Theme.TEXT,
                 font=Theme.F_BODY_B, anchor="w").pack(anchor="w")
        meta_bits = [b for b in (opt.get("network"), opt.get("note")) if b]
        if meta_bits:
            tk.Label(text_col, text="  ·  ".join(meta_bits), bg=Theme.CARD,
                     fg=Theme.TEXT_MUTED, font=Theme.F_TINY, anchor="w").pack(anchor="w")

        addr_row = tk.Frame(inner, bg=Theme.FIELD, highlightthickness=1,
                            highlightbackground=Theme.BORDER, bd=0)
        addr_row.pack(fill="x", pady=(12, 0))
        addr_var = tk.StringVar(value=opt["address"])
        addr_entry = tk.Entry(addr_row, textvariable=addr_var, bd=0, relief="flat",
                              bg=Theme.FIELD, fg=Theme.TEXT_SOFT, font=Theme.F_MONO,
                              readonlybackground=Theme.FIELD, state="readonly", justify="left")
        addr_entry.pack(side="left", fill="both", expand=True, padx=(10, 6), pady=8)
        RoundButton(addr_row, text="Copy", icon="⧉", kind="soft", height=28,
                   command=lambda a=opt["address"], lbl=opt["label"]: self._copy(a, lbl),
                   tooltip="Copy address to clipboard").pack(side="right", padx=6, pady=5)

        pay_links = opt.get("pay_links") or []
        if pay_links:
            tk.Label(inner, text="PAY INSTANTLY WITH", bg=Theme.CARD, fg=Theme.TEXT_MUTED,
                     font=Theme.F_TINY_B, anchor="w").pack(anchor="w", pady=(14, 6))
            links_row = tk.Frame(inner, bg=Theme.CARD)
            links_row.pack(fill="x")
            for link in pay_links:
                RoundButton(links_row, text=link["name"], icon="⚡", kind="primary",
                           bg=Theme.CARD,
                           command=lambda u=link["url"]: self._open_link(u),
                           tooltip=f"Opens {link['name']} with the address above "
                                   "already filled in").pack(side="left", padx=(0, 8))

    def _open_link(self, url):
        try:
            webbrowser.open_new_tab(url)
        except Exception:
            try:
                self.parent.toast.show("Couldn't open the link", kind="error")
            except Exception:
                pass

    def _copy(self, address, label="Address"):
        try:
            self.clipboard_clear()
            self.clipboard_append(address)
            self.update()
        except Exception:
            pass
        try:
            self.parent.toast.show(f"{label} address copied", kind="ok")
        except Exception:
            pass


# ===========================================================================
# 10B. FIRST-RUN TUTORIAL
# ===========================================================================

ONBOARDING_STEPS = [
    {
        "icon": "logo",
        "title": "Welcome to QSave Pro",
        "body": "Rotating quicksaves for any game, on a hotkey — so a bad "
                "run, a crash, or an overwritten file never costs you real "
                "progress.",
    },
    {
        "icon": "folder",
        "title": "Point it at a save folder",
        "body": "Add a game and tell QSave where it writes its saves. "
                "Browse manually, or click Auto-detect to scan common "
                "locations for you — Documents, AppData, Saved Games and more.",
    },
    {
        "icon": "keys",
        "title": "Quicksave & restore, instantly",
        "body": "Every game defaults to End for quicksave and Insert for "
                "restore-latest — change them any time in the preset's "
                "settings. Press quicksave any time to rotate a fresh copy "
                "in; press restore to instantly bring the latest one back. "
                "This is especially handy for games with hardcore or "
                "punishing save systems — Souls-likes with no manual saves, "
                "or Exanima's permadeath — where QSave gives you a real "
                "safety net underneath.",
    },
    {
        "icon": "rotate",
        "title": "Rotating slots, pins & archives",
        "body": "Old quicksaves cycle out automatically once you hit your "
                "slot limit. Pin a save to keep it forever, or archive one "
                "with a label for a specific moment worth keeping.",
    },
    {
        "icon": "shield",
        "title": "Autosave & a safety net",
        "body": "Turn on autosave per game for hands-free backups. Every "
                "restore keeps a safety snapshot first, so you can always "
                "undo it if it wasn't the one you wanted.",
    },
]


class OnboardingDialog(Dialog):
    """Beautiful, hand-illustrated first-run walkthrough — shown once, the
    first time the app is opened (see load_config / App._maybe_show_onboarding)."""

    def __init__(self, parent):
        super().__init__(parent, "Welcome", resizable=False)
        self.step = 0
        self.geometry("560x540")
        self.protocol("WM_DELETE_WINDOW", self._finish)
        self.bind("<Escape>", lambda _e: self._finish())
        self.bind("<Right>", lambda _e: self._next())
        self.bind("<Left>", lambda _e: self._back())
        self._render_step()
        self.finish()

    # ---------- hand-drawn hero icons ----------
    def _hero(self, parent, kind):
        cv = tk.Canvas(parent, width=132, height=132, bg=Theme.PANEL,
                       highlightthickness=0, bd=0)
        cv.pack(pady=(4, 18))
        cx, cy, r = 66, 66, 60
        cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=Theme.ACCENT_SOFT, outline="")
        col = Theme.ACCENT

        if kind == "logo":
            cv.create_text(cx, cy + 2, text="Q", fill=col,
                           font=(Theme.BASE_FAMILY, 54, "bold"))
        elif kind == "folder":
            round_rect(cv, cx - 32, cy - 22, cx - 2, cy - 10, 4,
                      fill=Theme.ACCENT_HOVER, outline="")
            round_rect(cv, cx - 32, cy - 12, cx + 32, cy + 26, 8, fill=col, outline="")
        elif kind == "keys":
            round_rect(cv, cx - 38, cy - 6, cx + 38, cy + 26, 9, fill=col, outline="")
            round_rect(cv, cx - 30, cy - 26, cx - 6, cy - 2, 7, fill=col, outline="")
            round_rect(cv, cx + 6, cy - 26, cx + 30, cy - 2, 7, fill=col, outline="")
            cv.create_line(cx, cy - 2, cx, cy + 12, fill="#FFFFFF", width=4,
                           arrow="last", capstyle="round")
        elif kind == "rotate":
            cv.create_arc(cx - 34, cy - 34, cx + 34, cy + 34, start=25, extent=260,
                          style="arc", outline=col, width=7)
            cv.create_polygon(cx + 28, cy - 27, cx + 44, cy - 19, cx + 24, cy - 6,
                              fill=col, outline="")
        elif kind == "shield":
            cv.create_polygon(cx, cy - 32, cx + 27, cy - 19, cx + 27, cy + 9,
                              cx, cy + 33, cx - 27, cy + 9, cx - 27, cy - 19,
                              fill=col, outline="", smooth=False)
            cv.create_line(cx - 12, cy - 1, cx - 2, cy + 10, cx + 15, cy - 13,
                           fill="#FFFFFF", width=4, capstyle="round", joinstyle="round")
        return cv

    # ---------- step rendering ----------
    def _render_step(self):
        for w in self.body.winfo_children():
            w.destroy()

        data = ONBOARDING_STEPS[self.step]
        n = len(ONBOARDING_STEPS)

        wrap = tk.Frame(self.body, bg=Theme.PANEL)
        wrap.pack(fill="both", expand=True, padx=36, pady=(28, 0))

        self._hero(wrap, data["icon"])
        tk.Label(wrap, text=data["title"], font=Theme.F_H1, bg=Theme.PANEL,
                fg=Theme.TEXT, wraplength=460, justify="center").pack()
        tk.Label(wrap, text=data["body"], font=Theme.F_BODY, bg=Theme.PANEL,
                fg=Theme.TEXT_SOFT, wraplength=430, justify="center").pack(pady=(10, 0))

        dot_w = 18
        dots = tk.Canvas(self.body, width=dot_w * n, height=16, bg=Theme.PANEL,
                         highlightthickness=0, bd=0)
        dots.pack(pady=(22, 0))
        for i in range(n):
            dx = dot_w * i + dot_w / 2
            if i == self.step:
                dots.create_oval(dx - 5, 3, dx + 5, 13, fill=Theme.ACCENT, outline="")
            else:
                dots.create_oval(dx - 4, 4, dx + 4, 12, fill="",
                                 outline=Theme.BORDER_STRONG, width=1.5)

        f = self.footer()
        RoundButton(f, text="Skip", kind="ghost", command=self._finish).pack(side="left")
        last = self.step == n - 1
        RoundButton(f, text="Get started" if last else "Next", kind="primary",
                   command=self._finish if last else self._next).pack(side="right")
        if self.step > 0:
            RoundButton(f, text="Back", kind="secondary",
                       command=self._back).pack(side="right", padx=(0, 8))

    # ---------- actions ----------
    def _next(self):
        if self.step < len(ONBOARDING_STEPS) - 1:
            self.step += 1
            self._render_step()

    def _back(self):
        if self.step > 0:
            self.step -= 1
            self._render_step()

    def _finish(self):
        self.parent.settings["onboarding_seen"] = True
        save_config(self.parent.config_data)
        self.on_cancel()


# ===========================================================================
# 11. SLOT DETAIL DIALOG
# ===========================================================================

class SlotDialog(Dialog):
    def __init__(self, parent, app, preset, slots, index):
        super().__init__(parent, "Save details", resizable=True)
        self.app = app
        self.preset = preset
        self.slots = slots
        self.index = index
        self.geometry("980x740")
        self.minsize(760, 560)
        self._photo = None
        self._build()
        self.bind("<Left>", lambda _e: self._nav(-1))
        self.bind("<Right>", lambda _e: self._nav(1))
        self.finish()

    @property
    def slot(self):
        return self.slots[self.index]

    def _build(self):
        for w in self.body.winfo_children():
            w.destroy()
        slot = self.slot

        head = tk.Frame(self.body, bg=Theme.PANEL)
        head.pack(fill="x", padx=22, pady=(18, 6))
        tk.Label(head, text=slot_title(slot), bg=Theme.PANEL, fg=Theme.TEXT,
                 font=Theme.F_H2).pack(side="left")
        kind_txt, kind_col = KIND_LABELS.get(slot.get("kind", "quick"), ("QUICK", "ACCENT"))
        chip(head, kind_txt, fg=Theme.color(kind_col), bg=Theme.PANEL_ALT).pack(side="left", padx=8)
        if slot.get("pinned"):
            chip(head, "★ PINNED", fg=Theme.WARN, bg=Theme.WARN_SOFT).pack(side="left")
        nav = tk.Frame(head, bg=Theme.PANEL)
        nav.pack(side="right")
        RoundButton(nav, text="›", kind="ghost", width=36, height=30,
                    command=lambda: self._nav(1)).pack(side="right")
        RoundButton(nav, text="‹", kind="ghost", width=36, height=30,
                    command=lambda: self._nav(-1)).pack(side="right")
        tk.Label(nav, text=f"{self.index + 1} / {len(self.slots)}  ", bg=Theme.PANEL,
                 fg=Theme.TEXT_MUTED, font=Theme.F_SMALL).pack(side="right")

        # image
        holder = tk.Frame(self.body, bg=Theme.ACCENT_SOFT, height=430)
        holder.pack(fill="both", expand=True, padx=22, pady=6)
        holder.pack_propagate(False)
        self.img_label = tk.Label(holder, bg=Theme.ACCENT_SOFT, fg=Theme.TEXT_MUTED,
                                  text="No screenshot for this save", font=Theme.F_BODY)
        self.img_label.pack(fill="both", expand=True)
        shot = slot.get("screenshot")
        if shot:
            path = os.path.join(preset_dir(self.preset), shot)
            self.app.thumbs.request(path, (900, 430), cover=False, callback=self._set_img)

        # facts
        facts = tk.Frame(self.body, bg=Theme.PANEL)
        facts.pack(fill="x", padx=22, pady=(8, 0))
        text = (f"{fmt_ts(slot['timestamp'])}   ·   {rel_time(slot['timestamp'])}   ·   "
                f"{human_size(slot.get('size'))}   ·   {slot.get('files') or '?'} file(s)   ·   "
                f"folder: quick_{slot['slot']}")
        tk.Label(facts, text=text, bg=Theme.PANEL, fg=Theme.TEXT_MUTED,
                 font=Theme.F_SMALL, anchor="w").pack(fill="x")

        # editable label + note
        edit = tk.Frame(self.body, bg=Theme.PANEL)
        edit.pack(fill="x", padx=22, pady=(10, 0))
        self.label_var = tk.StringVar(value=slot.get("label", ""))
        row = tk.Frame(edit, bg=Theme.PANEL)
        row.pack(fill="x")
        tk.Label(row, text="Name", bg=Theme.PANEL, fg=Theme.TEXT_SOFT,
                 font=Theme.F_SMALL_B, width=8, anchor="w").pack(side="left")
        Input(row, textvariable=self.label_var, width=34).pack(side="left", fill="x", expand=True)
        RoundButton(row, text="Save name", kind="secondary", height=32,
                    command=self._save_label).pack(side="left", padx=6)

        row2 = tk.Frame(edit, bg=Theme.PANEL)
        row2.pack(fill="x", pady=(8, 0))
        tk.Label(row2, text="Note", bg=Theme.PANEL, fg=Theme.TEXT_SOFT,
                 font=Theme.F_SMALL_B, width=8, anchor="nw").pack(side="left", anchor="n")
        wrap = tk.Frame(row2, bg=Theme.FIELD, highlightthickness=1, highlightbackground=Theme.BORDER)
        wrap.pack(side="left", fill="x", expand=True)
        self.note = tk.Text(wrap, height=3, bd=0, relief="flat", bg=Theme.FIELD, fg=Theme.TEXT,
                            insertbackground=Theme.ACCENT, font=Theme.F_BODY,
                            highlightthickness=0, wrap="word")
        self.note.pack(fill="both", expand=True, padx=8, pady=6)
        self.note.insert("1.0", slot.get("note", ""))
        RoundButton(row2, text="Save note", kind="secondary", height=32,
                    command=self._save_note).pack(side="left", padx=6, anchor="n")

        f = self.footer()
        RoundButton(f, text="Restore this save", kind="primary",
                    command=self._restore).pack(side="right")
        RoundButton(f, text="Delete", kind="danger",
                    command=self._delete).pack(side="right", padx=(0, 8))
        RoundButton(f, text="Export ZIP…", kind="secondary",
                    command=self._export).pack(side="right", padx=(0, 8))
        pin_text = "Unpin" if slot.get("pinned") else "Pin"
        RoundButton(f, text=pin_text, icon="★", kind="soft" if slot.get("pinned") else "secondary",
                    command=self._toggle_pin).pack(side="left")
        RoundButton(f, text="Open folder", kind="ghost",
                    command=lambda: open_in_file_manager(slot_folder(self.preset, slot["slot"]))
                    ).pack(side="left", padx=(8, 0))

    # ---------- helpers ----------
    def _set_img(self, photo):
        if not self.winfo_exists():
            return
        self._photo = photo
        if photo is not None:
            self.img_label.configure(image=photo, text="")
        else:
            self.img_label.configure(image="", text="No screenshot for this save")

    def _nav(self, delta):
        new_index = self.index + delta
        if 0 <= new_index < len(self.slots):
            self.index = new_index
            self._photo = None
            self._build()

    def _save_label(self):
        label = self.label_var.get().strip()
        ok, slot = update_slot(self.preset, self.slot["slot"], label=label)
        if ok:
            self.slots[self.index] = slot
            self.app.toast.show("Name updated", kind="ok")
            self.app.render_content()
            self._build()

    def _save_note(self):
        note = self.note.get("1.0", "end").strip()
        ok, slot = update_slot(self.preset, self.slot["slot"], note=note)
        if ok:
            self.slots[self.index] = slot
            self.app.toast.show("Note saved", kind="ok")

    def _toggle_pin(self):
        slot = self.slot
        ok, updated = update_slot(self.preset, slot["slot"], pinned=not slot.get("pinned"))
        if ok:
            self.slots[self.index] = updated
            self.app.toast.show("Pinned" if updated["pinned"] else "Unpinned", kind="ok")
            self.app.render_content()
            self._build()

    def _restore(self):
        self.app.do_restore(self.preset, self.slot["slot"])
        self.on_cancel()

    def _export(self):
        slot = self.slot
        default = f"{sanitize(self.preset['name'])}_{slot_title(slot)}.zip".replace(" ", "_")
        dest = filedialog.asksaveasfilename(
            parent=self, title="Export quicksave", defaultextension=".zip",
            initialfile=default, filetypes=[("ZIP archive", "*.zip")])
        if not dest:
            return
        ok, msg = export_slot_zip(self.preset, slot, dest)
        self.app.toast.show(msg, kind="ok" if ok else "error")
        self.app.set_status(msg)

    def _delete(self):
        slot = self.slot
        if self.app.settings.get("confirm_delete", True):
            confirmed, _ = ask_confirm(
                self, "Delete quicksave",
                f"Delete {slot_title(slot)} for '{self.preset['name']}'? This cannot be undone.",
                confirm_text="Delete")
            if not confirmed:
                return
        ok, msg = delete_slot(self.preset, slot["slot"])
        self.app.toast.show(msg, kind="ok" if ok else "error")
        self.app.set_status(msg)
        self.app.render_content()
        if not ok:
            return
        # Close back to the gallery rather than jumping to another slot's
        # details, which looked like an unrelated details window popping open.
        self.on_cancel()


# ===========================================================================
# 12. MAIN APPLICATION
# ===========================================================================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        os.makedirs(QSAVES_DIR, exist_ok=True)
        generate_sounds()

        self.config_data = load_config()
        self.presets = self.config_data["presets"]
        self.settings = self.config_data["settings"]
        # Presets created before this recovery feature existed only got their
        # QSaves folder lazily, on first quicksave, with no preset.json in it
        # at all — backfill it now so they're recoverable too.
        for p in self.presets:
            write_preset_meta(p)

        Theme.apply()
        Theme.init_fonts(self)
        setup_ttk(self)

        self.title(f"{APP_NAME}")
        try:
            self.geometry(self.settings.get("geometry") or "1220x780")
        except Exception:
            self.geometry("1220x780")
        self.minsize(980, 620)
        self.configure(bg=Theme.BG)
        # Track the last known *non*-maximized geometry separately. If the
        # window is closed while maximized, self.geometry() reports the
        # full-screen size (sometimes with negative offsets on Windows) —
        # saving that verbatim and re-applying it as a plain geometry string
        # on next launch is what makes the window reopen "stuck" at
        # fullscreen size instead of properly maximized. We restore real
        # maximized state via self.state('zoomed') instead (see below).
        self._normal_geometry = self.settings.get("geometry") or "1220x780"
        self.bind("<Configure>", self._on_root_configure)

        self.selected_id = self.settings.get("last_preset") or None
        self.search_var = tk.StringVar()
        self.sort_var = tk.StringVar(value=self.settings.get("sort_mode", SORT_MODES[0]))
        self.filter_var = tk.StringVar(value="All kinds")
        self.view_var = tk.StringVar(value=self.settings.get("view_mode", "grid"))

        self.hotkey_handles = []
        self.global_hotkey_handles = []
        self.autosave_job = None
        self._last_autosave_at = {}      # preset_id -> time.time() of its last autosave
        self._app_start_time = time.time()
        self.tray_icon = None
        self.tray_thread = None
        self.game_watch_job = None
        self._exe_was_running = {}       # preset_id -> bool, last known running state
        self.rewind_cursor = {}          # preset_id -> index into history (0 = latest)
        self._activity_log = deque(maxlen=200)

        self._window_has_focus = False
        self._focus_grace_until = 0.0    # ignore our own focus right after a capture
        self._hotkey_last_fire = {}

        self.toast = ToastManager(self)
        self.thumbs = ThumbLoader(self)

        self._build_ui()
        self.apply_window_flags()
        self.rebind_hotkeys()
        self.bind_global_hotkeys()
        if self.settings.get("window_maximized"):
            self.after(60, self._restore_maximized)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<FocusIn>", self._on_window_focus_in, add="+")
        self.bind("<FocusOut>", self._on_window_focus_out, add="+")
        self.search_var.trace_add("write", lambda *_: self.refresh_sidebar())
        self._start_autosave_loop()
        self._start_game_watch_loop()
        self.set_status("Ready.")
        self.after(200, self._maybe_show_onboarding)

        # Populating the sidebar/content here, synchronously inside __init__,
        # runs *before* Tk has ever mapped the window or processed an idle
        # cycle — the scroll canvas hasn't had a real <Configure> event yet,
        # so the rows get built into a not-yet-realized layout and sit
        # invisible until something else later happens to force a redraw
        # (e.g. focusing the search field, which changes search_var and
        # re-triggers refresh_sidebar — that later call works because by
        # then the window really is mapped). Scheduling this for the moment
        # the event loop actually starts sidesteps the whole issue.
        self.after(0, self._initial_render)

    def _initial_render(self):
        self.refresh_sidebar()
        self.render_content()

    def _maybe_show_onboarding(self):
        if not self.settings.get("onboarding_seen", False):
            OnboardingDialog(self)

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        for w in self.winfo_children():
            w.destroy()

        # ---- header ----
        header_wrap = tk.Frame(self, bg=Theme.BG)
        header_wrap.pack(fill="x")
        header = tk.Frame(header_wrap, bg=Theme.BG)
        header.pack(fill="x", padx=22, pady=(18, 14))

        brand = tk.Frame(header, bg=Theme.BG)
        brand.pack(side="left")
        mark = tk.Canvas(brand, width=30, height=30, bg=Theme.BG, highlightthickness=0, bd=0)
        mark.pack(side="left", padx=(0, 10))
        round_rect(mark, 1, 1, 29, 29, 8, fill=Theme.ACCENT, outline="")
        mark.create_text(15, 16, text="⤓", fill=Theme.ACCENT_TEXT, font=(Theme.BASE_FAMILY, 14, "bold"))
        title_col = tk.Frame(brand, bg=Theme.BG)
        title_col.pack(side="left")
        title_row = tk.Frame(title_col, bg=Theme.BG)
        title_row.pack(anchor="w")
        tk.Label(title_row, text=APP_NAME, font=Theme.F_LOGO, bg=Theme.BG,
                 fg=Theme.TEXT).pack(side="left")
        tk.Label(title_row, text=f"  v{APP_VERSION}", font=Theme.F_TINY, bg=Theme.BG,
                 fg=Theme.TEXT_MUTED).pack(side="left", pady=(6, 0))
        pause_chip_fg = Theme.DANGER if self.settings.get("hotkeys_paused") else Theme.OK
        pause_chip_bg = Theme.DANGER_SOFT if self.settings.get("hotkeys_paused") else Theme.OK_SOFT
        self.pause_chip = chip(
            title_col, "⏸  Hotkeys paused" if self.settings.get("hotkeys_paused") else "●  Hotkeys live",
            fg=pause_chip_fg, bg=pause_chip_bg, padx=8, pady=1)
        self.pause_chip.pack(anchor="w", pady=(2, 0))

        warnings = []
        if keyboard is None:
            warnings.append("hotkeys need: pip install keyboard")
        if ImageGrab is None:
            warnings.append("screenshots need: pip install Pillow")
        if psutil is None and any((p.get("game_exe") or "").strip() for p in self.presets):
            warnings.append("game-launch detection needs: pip install psutil")
        if warnings:
            chip(header, "⚠  " + "   ·   ".join(warnings), fg=Theme.DANGER,
                 bg=Theme.DANGER_SOFT).pack(side="left", padx=18)

        # Right-side actions are visually grouped: a "views" cluster and a
        # separate, lighter-weight utility cluster, split by a thin divider
        # so the header reads as organised rather than a flat row of buttons.
        right = tk.Frame(header, bg=Theme.BG)
        right.pack(side="right")
        utility = tk.Frame(right, bg=Theme.BG)
        utility.pack(side="right")
        RoundButton(utility, text="About", icon="ⓘ", kind="ghost",
                    command=self.open_about, tooltip="About QSave").pack(side="right")
        RoundButton(utility, text="Donate", icon="♥", kind="soft",
                    command=self.open_donate, tooltip="Support the developer").pack(
                    side="right", padx=(0, 4))
        RoundButton(utility, text="Hide to tray", icon="▾", kind="ghost",
                    command=self.hide_to_tray, tooltip="Minimise QSave to the system tray").pack(
                    side="right", padx=(0, 4))
        tk.Frame(right, bg=Theme.DIVIDER, width=1).pack(side="right", fill="y", padx=10, pady=4)
        views = tk.Frame(right, bg=Theme.BG)
        views.pack(side="right")
        RoundButton(views, text="Settings", icon="⚙", kind="secondary",
                    command=self.open_settings, tooltip="Settings").pack(side="right")
        RoundButton(views, text="Dashboard", icon="⌂", kind="secondary",
                    command=self.show_dashboard, tooltip="Back to the games dashboard").pack(
                    side="right", padx=(0, 8))

        tk.Frame(header_wrap, bg=Theme.DIVIDER, height=1).pack(fill="x")

        # ---- body ----
        body = tk.Frame(self, bg=Theme.BG)
        body.pack(fill="both", expand=True, padx=18, pady=(14, 10))

        sidebar = tk.Frame(body, bg=Theme.SIDEBAR, width=264, highlightthickness=1,
                           highlightbackground=Theme.BORDER)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        srow = tk.Frame(sidebar, bg=Theme.SIDEBAR)
        srow.pack(fill="x", padx=16, pady=(18, 10))
        tk.Label(srow, text="YOUR GAMES", bg=Theme.SIDEBAR, fg=Theme.TEXT_MUTED,
                 font=Theme.F_EYEBROW).pack(anchor="w")
        Input(sidebar, textvariable=self.search_var, placeholder="Search games…",
              width=20, bg=Theme.SIDEBAR).pack(fill="x", padx=16, pady=(0, 10))

        tk.Frame(sidebar, bg=Theme.DIVIDER, height=1).pack(fill="x", padx=16)

        self.sidebar_area = ScrollArea(sidebar, bg=Theme.SIDEBAR)
        self.sidebar_area.pack(fill="both", expand=True, padx=8, pady=(8, 0))

        RoundButton(sidebar, text="Add game", icon="+", kind="primary",
                    command=self.add_preset).pack(fill="x", padx=16, pady=16)

        self.content = tk.Frame(body, bg=Theme.PANEL, highlightthickness=1,
                                highlightbackground=Theme.BORDER)
        self.content.pack(side="left", fill="both", expand=True, padx=(16, 0))

        # ---- status bar ----
        status_wrap = tk.Frame(self, bg=Theme.BG)
        status_wrap.pack(fill="x")
        tk.Frame(status_wrap, bg=Theme.DIVIDER, height=1).pack(fill="x")
        status = tk.Frame(status_wrap, bg=Theme.BG)
        status.pack(fill="x", padx=22, pady=(10, 14))
        self.status_var = tk.StringVar(value="Ready.")
        tk.Label(status, textvariable=self.status_var, bg=Theme.BG, fg=Theme.TEXT_MUTED,
                 font=Theme.F_SMALL, anchor="w").pack(side="left", fill="x", expand=True)
        RoundButton(status, text="Activity log", kind="ghost", height=26,
                    command=self.show_activity_log).pack(side="right")
        RoundButton(status, text="Tutorial", icon="?", kind="ghost", height=26,
                    command=self.show_tutorial, tooltip="Replay the welcome walkthrough").pack(
                    side="right", padx=(0, 8))

    # ------------------------------------------------------------------ #
    # Sidebar / preset list
    # ------------------------------------------------------------------ #
    def find_preset(self, pid):
        return next((p for p in self.presets if p["id"] == pid), None)

    def refresh_sidebar(self):
        for w in self.sidebar_area.inner.winfo_children():
            w.destroy()
        query = self.search_var.get().strip().lower()
        shown = [p for p in self.presets if not query or query in p["name"].lower()]
        if not shown:
            msg = "No games yet — click “Add game”." if not self.presets else "No matches."
            tk.Label(self.sidebar_area.inner, text=msg, bg=Theme.SIDEBAR, fg=Theme.TEXT_MUTED,
                     font=Theme.F_SMALL, wraplength=210, justify="left").pack(anchor="w", pady=10, padx=4)
        for p in shown:
            self._build_sidebar_row(p)

        # Without this, a freshly-added row can sit outside the canvas's
        # scrollregion until something else (e.g. focusing the search field)
        # happens to trigger a redraw — the row exists but isn't visible
        # until then. Forcing it here makes it show up immediately.
        self.sidebar_area.inner.update_idletasks()
        self.sidebar_area.canvas.configure(scrollregion=self.sidebar_area.canvas.bbox("all"))

    def _build_sidebar_row(self, preset):
        meta = load_meta(preset)
        n = len(meta["slots"])
        selected = preset["id"] == self.selected_id
        row_bg = Theme.ACCENT_SOFT if selected else Theme.SIDEBAR
        outer = tk.Frame(self.sidebar_area.inner, bg=Theme.SIDEBAR)
        outer.pack(fill="x", pady=2, padx=2)
        row = tk.Frame(outer, bg=row_bg, cursor="hand2")
        row.pack(fill="x")
        accent_bar = tk.Frame(row, bg=Theme.ACCENT if selected else row_bg, width=3)
        accent_bar.pack(side="left", fill="y")
        inner = tk.Frame(row, bg=row_bg)
        inner.pack(fill="x", expand=True, padx=(9, 10), pady=9)
        dot = tk.Canvas(inner, width=10, height=10, bg=row_bg, highlightthickness=0, bd=0)
        dot.pack(side="left", padx=(0, 9))
        dot.create_oval(1, 1, 9, 9, fill=preset.get("color", PRESET_COLORS[0]), outline="")
        txt = tk.Frame(inner, bg=row_bg)
        txt.pack(side="left", fill="x", expand=True)
        name_fg = Theme.TEXT if preset.get("enabled", True) else Theme.TEXT_MUTED
        tk.Label(txt, text=preset["name"], bg=row_bg, fg=name_fg, font=Theme.F_BODY_B,
                 anchor="w").pack(anchor="w", fill="x")
        sub = f"{n} save{'s' if n != 1 else ''}" + ("" if preset.get("enabled", True) else "  ·  disabled")
        tk.Label(txt, text=sub, bg=row_bg, fg=Theme.TEXT_MUTED, font=Theme.F_TINY,
                 anchor="w").pack(anchor="w")
        if selected:
            tk.Label(inner, text="›", bg=row_bg, fg=Theme.ACCENT, font=Theme.F_H3).pack(side="right")

        def on_enter(_e=None):
            if not selected:
                for w in (row, inner, dot, txt) + tuple(txt.winfo_children()):
                    w.configure(bg=Theme.SIDEBAR_HOVER)

        def on_leave(_e=None):
            if not selected:
                for w in (row, inner, dot, txt) + tuple(txt.winfo_children()):
                    w.configure(bg=row_bg)

        # bind_tree recurses through all of row's descendants, so one call
        # per event is enough to cover accent_bar/inner/dot/txt/labels.
        bind_tree(row, "<Enter>", on_enter)
        bind_tree(row, "<Leave>", on_leave)
        bind_tree(row, "<Button-1>", lambda _e, pid=preset["id"]: self.select_preset(pid))
        bind_tree(row, "<Button-3>", lambda e, p=preset: self._sidebar_menu(e, p))

    def _sidebar_menu(self, event, preset):
        m = tk.Menu(self, tearoff=0, bg=Theme.PANEL, fg=Theme.TEXT,
                   activebackground=Theme.ACCENT, activeforeground=Theme.ACCENT_TEXT,
                   bd=0, relief="flat", font=Theme.F_BODY)
        m.add_command(label="Select", command=lambda: self.select_preset(preset["id"]))
        m.add_command(label="Edit…", command=lambda: self.edit_preset(preset))
        m.add_command(label="Duplicate", command=lambda: self.duplicate_preset(preset))
        m.add_command(label="Open save folder", command=lambda: open_in_file_manager(preset["save_path"]))
        m.add_command(label="Open quicksaves folder", command=lambda: open_in_file_manager(preset_dir(preset)))
        m.add_separator()
        label = "Disable hotkeys" if preset.get("enabled", True) else "Enable hotkeys"
        m.add_command(label=label, command=lambda: self._toggle_preset_enabled(preset))
        m.add_separator()
        m.add_command(label="Delete…", command=lambda: self.delete_preset(preset))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _toggle_preset_enabled(self, preset):
        preset["enabled"] = not preset.get("enabled", True)
        self.save_all()
        self.refresh_sidebar()
        self.rebind_hotkeys()
        self.set_status(f"{'Enabled' if preset['enabled'] else 'Disabled'} hotkeys for '{preset['name']}'.")

    def select_preset(self, pid):
        self.selected_id = pid
        self.settings["last_preset"] = pid
        self.rewind_cursor.pop(pid, None)
        self.refresh_sidebar()
        self.render_content()

    def show_dashboard(self):
        self.selected_id = None
        self.settings["last_preset"] = ""
        self.refresh_sidebar()
        self.render_content()

    # ------------------------------------------------------------------ #
    # Preset CRUD
    # ------------------------------------------------------------------ #
    def add_preset(self):
        names = [p["name"] for p in self.presets]
        PresetDialog(self, on_save=self._on_preset_saved, preset=None, existing=names)

    def edit_preset(self, preset):
        names = [p["name"] for p in self.presets]
        PresetDialog(self, on_save=self._on_preset_saved, preset=preset, existing=names)

    def _on_preset_saved(self, result, old_name):
        if old_name and old_name != result["name"] and sanitize(old_name) != sanitize(result["name"]):
            old_dir = os.path.join(QSAVES_DIR, sanitize(old_name))
            new_dir = os.path.join(QSAVES_DIR, sanitize(result["name"]))
            if os.path.isdir(old_dir):
                if os.path.exists(new_dir):
                    # A folder for the new name already exists on disk.
                    # Silently keeping the old folder would disconnect this
                    # preset from all of its existing quicksaves the moment
                    # its metadata gets written under the new name below —
                    # so refuse the rename instead of losing that history.
                    messagebox.showerror(
                        "Rename failed",
                        f"A folder for '{result['name']}' already exists under QSaves/, so "
                        f"'{old_name}' keeps its original name to avoid losing its existing "
                        "quicksaves. Pick a different name, or merge the folders manually "
                        "first, then rename again.",
                        parent=self)
                    result["name"] = old_name
                else:
                    try:
                        shutil.move(old_dir, new_dir)
                    except Exception as e:
                        # Same rationale: if the move fails partway (a file
                        # locked by another process, a permissions issue),
                        # keep the preset pointed at its real, existing
                        # folder rather than silently creating an empty new
                        # one under the new name and orphaning the old data.
                        messagebox.showerror(
                            "Rename failed",
                            f"Could not move the save folder for '{old_name}' to its new name: "
                            f"{e}\n\nThe name change was cancelled so it doesn't get "
                            "disconnected from its existing quicksaves.",
                            parent=self)
                        result["name"] = old_name

        existing = self.find_preset(result["id"])
        if existing:
            self.presets[self.presets.index(existing)] = result
        else:
            self.presets.append(result)

        write_preset_meta(result)
        self.save_all()
        prune_excess_slots(result)
        prune_excess_autosaves(result, self._effective_autosave_max(result))
        self.selected_id = result["id"]
        self.settings["last_preset"] = result["id"]
        self.refresh_sidebar()
        self.render_content()
        self.rebind_hotkeys()
        self.toast.show("Preset saved", kind="ok")
        self.set_status(f"Saved preset '{result['name']}'.")

    def duplicate_preset(self, preset):
        clone = json.loads(json.dumps(preset))
        clone["id"] = uuid.uuid4().hex[:10]
        base = preset["name"]
        n = 2
        names = {p["name"] for p in self.presets}
        new_name = f"{base} copy"
        while new_name in names:
            new_name = f"{base} copy {n}"
            n += 1
        clone["name"] = new_name
        clone["save_key"] = ""
        clone["restore_key"] = ""
        clone["stats"] = {"saves": 0, "restores": 0, "last_save": ""}
        clone = normalize_preset(clone)
        self.presets.append(clone)
        write_preset_meta(clone)
        self.save_all()
        self.refresh_sidebar()
        self.rebind_hotkeys()
        self.toast.show(f"Duplicated as '{new_name}'", kind="ok")

    def delete_preset(self, preset):
        confirmed, also_files = ask_confirm(
            self, "Delete preset", f"Remove '{preset['name']}' from QSave?",
            confirm_text="Delete", checkbox="Also delete its quicksave files on disk")
        if not confirmed:
            return
        self.presets.remove(preset)
        self.save_all()
        if also_files:
            d = preset_dir(preset)
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
        if self.selected_id == preset["id"]:
            self.selected_id = None
        self.refresh_sidebar()
        self.render_content()
        self.rebind_hotkeys()
        self.set_status(f"Deleted preset '{preset['name']}'.")

    # ------------------------------------------------------------------ #
    # Content panel
    # ------------------------------------------------------------------ #
    def render_content(self):
        for w in self.content.winfo_children():
            w.destroy()

        if not self.presets:
            wrap = tk.Frame(self.content, bg=Theme.PANEL)
            wrap.place(relx=0.5, rely=0.42, anchor="center")
            mark = tk.Canvas(wrap, width=64, height=64, bg=Theme.PANEL, highlightthickness=0, bd=0)
            mark.pack(pady=(0, 16))
            round_rect(mark, 2, 2, 62, 62, 16, fill=Theme.ACCENT_SOFT, outline="")
            mark.create_text(32, 34, text="⤓", fill=Theme.ACCENT, font=(Theme.BASE_FAMILY, 26, "bold"))
            tk.Label(wrap, text="Welcome to QSave Pro",
                    font=Theme.F_H1, bg=Theme.PANEL, fg=Theme.TEXT).pack()
            tk.Label(wrap, text="Add your first game to start keeping rotating quicksaves.",
                    font=Theme.F_BODY, bg=Theme.PANEL, fg=Theme.TEXT_MUTED).pack(pady=(4, 20))
            RoundButton(wrap, text="Add your first game", icon="+", kind="primary",
                       command=self.add_preset).pack()
            return

        preset = self.find_preset(self.selected_id) if self.selected_id else None
        if preset is None:
            self._render_dashboard()
            return
        self._render_preset(preset)

    def _render_dashboard(self):
        wrap = tk.Frame(self.content, bg=Theme.PANEL)
        wrap.pack(fill="both", expand=True, padx=26, pady=24)
        tk.Label(wrap, text="DASHBOARD", font=Theme.F_EYEBROW, bg=Theme.PANEL,
                 fg=Theme.ACCENT).pack(anchor="w")
        tk.Label(wrap, text="All games", font=Theme.F_H1, bg=Theme.PANEL, fg=Theme.TEXT).pack(
                 anchor="w", pady=(2, 4))
        total_usage = sum(preset_usage(p) for p in self.presets)
        total_slots = sum(len(load_meta(p)["slots"]) for p in self.presets)
        tk.Label(wrap, text=f"{len(self.presets)} game{'s' if len(self.presets) != 1 else ''}  ·  "
                            f"{total_slots} quicksave(s)  ·  {human_size(total_usage)} on disk",
                font=Theme.F_SMALL, bg=Theme.PANEL, fg=Theme.TEXT_MUTED).pack(anchor="w", pady=(0, 16))
        area = ScrollArea(wrap, bg=Theme.PANEL)
        area.pack(fill="both", expand=True)
        for p in self.presets:
            meta = load_meta(p)
            card = tk.Frame(area.inner, bg=Theme.CARD, highlightthickness=1,
                            highlightbackground=Theme.BORDER, cursor="hand2")
            card.pack(fill="x", pady=5)
            accent = tk.Frame(card, bg=p.get("color", PRESET_COLORS[0]), width=4)
            accent.pack(side="left", fill="y")
            inner = tk.Frame(card, bg=Theme.CARD)
            inner.pack(side="left", fill="both", expand=True, padx=16, pady=13)
            txt = tk.Frame(inner, bg=Theme.CARD)
            txt.pack(side="left", fill="x", expand=True)
            tk.Label(txt, text=p["name"], bg=Theme.CARD, fg=Theme.TEXT, font=Theme.F_BODY_B,
                    anchor="w").pack(anchor="w")
            last = meta["slots"] and max(meta["slots"], key=lambda s: s["timestamp"])
            sub = f"{len(meta['slots'])} save(s)  ·  {human_size(preset_usage(p, meta))}"
            if last:
                sub += f"  ·  last: {rel_time(last['timestamp'])}"
            tk.Label(txt, text=sub, bg=Theme.CARD, fg=Theme.TEXT_MUTED, font=Theme.F_TINY,
                    anchor="w").pack(anchor="w", pady=(2, 0))
            tk.Label(inner, text="Open ›", bg=Theme.CARD, fg=Theme.ACCENT,
                    font=Theme.F_SMALL_B).pack(side="right")
            bind_tree(card, "<Enter>", lambda _e, c=card: c.configure(highlightbackground=Theme.ACCENT))
            bind_tree(card, "<Leave>", lambda _e, c=card: c.configure(highlightbackground=Theme.BORDER))
            bind_tree(card, "<Button-1>", lambda _e, pid=p["id"]: self.select_preset(pid))

    def _render_preset(self, preset):
        meta = load_meta(preset)
        slots = list(meta["slots"])

        top = tk.Frame(self.content, bg=Theme.PANEL)
        top.pack(fill="x", padx=22, pady=(20, 10))
        title_row = tk.Frame(top, bg=Theme.PANEL)
        title_row.pack(fill="x")
        title_left = tk.Frame(title_row, bg=Theme.PANEL)
        title_left.pack(side="left")
        dot = tk.Canvas(title_left, width=14, height=14, bg=Theme.PANEL, highlightthickness=0, bd=0)
        dot.pack(side="left", padx=(0, 10))
        dot.create_oval(1, 1, 13, 13, fill=preset.get("color", PRESET_COLORS[0]), outline="")
        tk.Label(title_left, text=preset["name"], font=Theme.F_H1, bg=Theme.PANEL,
                fg=Theme.TEXT).pack(side="left")
        if not preset.get("enabled", True):
            chip(title_left, "HOTKEYS OFF", fg=Theme.TEXT_MUTED, bg=Theme.PANEL_ALT).pack(side="left", padx=10)
        btns = tk.Frame(title_row, bg=Theme.PANEL)
        btns.pack(side="right")
        RoundButton(btns, text="Edit", icon="✎", kind="secondary", height=32,
                   command=lambda: self.edit_preset(preset)).pack(side="left", padx=3)
        RoundButton(btns, text="Delete", icon="🗑", kind="ghost", height=32,
                   command=lambda: self.delete_preset(preset)).pack(side="left", padx=3)

        info_row = tk.Frame(top, bg=Theme.PANEL)
        info_row.pack(fill="x", pady=(8, 0))
        info_chips = [
            f"📁  {preset['save_path']}",
            f"quicksave [{(preset['save_key'] or '—').upper()}]",
            f"restore [{(preset['restore_key'] or '—').upper()}]",
            f"autosave {'on' if preset.get('autosave') else 'off'}",
            human_size(preset_usage(preset, meta)) + " used",
        ]
        if (preset.get("game_exe") or "").strip():
            info_chips.append(f"auto-opens with {os.path.basename(preset['game_exe'])}")
        for i, txt in enumerate(info_chips):
            chip(info_row, txt, fg=Theme.TEXT_SOFT, bg=Theme.PANEL_ALT, padx=8, pady=3).pack(
                side="left", padx=(0 if i == 0 else 5, 0))

        tk.Frame(self.content, bg=Theme.DIVIDER, height=1).pack(fill="x", padx=22, pady=(4, 0))

        toolbar = tk.Frame(self.content, bg=Theme.PANEL)
        toolbar.pack(fill="x", padx=22, pady=(12, 6))
        actions = tk.Frame(toolbar, bg=Theme.PANEL)
        actions.pack(side="left")
        RoundButton(actions, text="Quicksave", icon="⤓", kind="primary", height=32,
                   command=lambda: self.do_quicksave(preset, kind="quick")).pack(side="left")
        RoundButton(actions, text="Restore latest", icon="⤒", kind="secondary", height=32,
                   command=lambda: self.do_restore(preset)).pack(side="left", padx=6)
        RoundButton(actions, text="Archive…", icon="⭐", kind="soft", height=32,
                   command=lambda: self.do_archive(preset)).pack(side="left", padx=6)
        tk.Frame(toolbar, bg=Theme.DIVIDER, width=1).pack(side="left", fill="y", padx=10, pady=3)
        undo_left = undo_count(preset)
        undo_enabled = undo_left > 0
        RoundButton(toolbar,
                   text=f"Undo restore ({undo_left})" if undo_enabled else "Undo restore",
                   icon="⤺", kind="secondary" if undo_enabled else "ghost",
                   height=32, enabled=undo_enabled,
                   tooltip=f"Step back through the last {UNDO_KEEP} safety snapshots",
                   command=lambda: self.do_undo_restore(preset)).pack(side="left")
        orig_enabled = has_original_backup(preset)
        RoundButton(toolbar, text="Restore Original Save", icon="⏮",
                   kind="secondary" if orig_enabled else "ghost", height=32,
                   enabled=orig_enabled,
                   tooltip="Put back the untouched save folder as it was before "
                           "QSave's very first save of this game",
                   command=lambda: self.do_restore_original(preset)).pack(side="left", padx=6)
        RoundButton(toolbar, text="Import ZIP…", icon="⇩", kind="ghost", height=32,
                   command=lambda: self.do_import_zip(preset)).pack(side="left", padx=6)

        view_group = tk.Frame(toolbar, bg=Theme.PANEL)
        view_group.pack(side="right")
        RoundButton(view_group, text="☰", kind="soft" if self.view_var.get() == "list" else "ghost",
                   width=36, height=32, command=lambda: self._set_view("list")).pack(side="right")
        RoundButton(view_group, text="▦", kind="soft" if self.view_var.get() == "grid" else "ghost",
                   width=36, height=32, command=lambda: self._set_view("grid")).pack(side="right", padx=(0, 4))
        tk.Frame(toolbar, bg=Theme.DIVIDER, width=1).pack(side="right", fill="y", padx=10, pady=3)
        sort_group = tk.Frame(toolbar, bg=Theme.PANEL)
        sort_group.pack(side="right")
        DropButton(sort_group, SORT_MODES, self.sort_var, height=32,
                  on_change=lambda _v: self.render_content()).pack(side="right")
        DropButton(sort_group, ["All kinds", "Quick", "Auto", "Archive", "Import", "Pinned only"],
                  self.filter_var, height=32,
                  on_change=lambda _v: self.render_content()).pack(side="right", padx=6)

        slots = self._filter_slots(slots)
        slots = self._sort_slots(slots)

        area_wrap = tk.Frame(self.content, bg=Theme.PANEL)
        area_wrap.pack(fill="both", expand=True, padx=22, pady=(4, 16))
        area = ScrollArea(area_wrap, bg=Theme.PANEL)
        area.pack(fill="both", expand=True)

        if not slots:
            empty = tk.Frame(area.inner, bg=Theme.PANEL)
            empty.pack(pady=50)
            has_any = bool(list(meta["slots"]))
            msg = "No quicksaves match this filter." if has_any else \
                "No quicksaves yet — hit Quicksave to create your first one."
            tk.Label(empty, text=msg, bg=Theme.PANEL, fg=Theme.TEXT_MUTED,
                    font=Theme.F_BODY).pack()
            if not has_any:
                RoundButton(empty, text="Quicksave now", icon="⤓", kind="primary",
                           command=lambda: self.do_quicksave(preset, kind="quick")).pack(pady=(14, 0))
            return

        if self.view_var.get() == "grid":
            self._render_grid(area.inner, preset, slots)
        else:
            self._render_list(area.inner, preset, slots)

    def _set_view(self, mode):
        self.view_var.set(mode)
        self.settings["view_mode"] = mode
        self.render_content()

    def _filter_slots(self, slots):
        f = self.filter_var.get()
        if f == "Pinned only":
            return [s for s in slots if s.get("pinned")]
        mapping = {"Quick": "quick", "Auto": "auto", "Archive": "manual", "Import": "import"}
        if f in mapping:
            return [s for s in slots if s.get("kind") == mapping[f]]
        return slots

    def _sort_slots(self, slots):
        mode = self.sort_var.get()
        if mode == "Oldest first":
            return sorted(slots, key=lambda s: s["timestamp"])
        if mode == "Slot number":
            return sorted(slots, key=lambda s: s["slot"])
        if mode == "Largest first":
            return sorted(slots, key=lambda s: s.get("size") or 0, reverse=True)
        if mode == "Pinned first":
            return sorted(slots, key=lambda s: (not s.get("pinned"), -_ts(s)))
        return sorted(slots, key=lambda s: s["timestamp"], reverse=True)

    def _render_grid(self, parent, preset, slots):
        cols = max(2, min(6, (self.content.winfo_width() or 900) // (CARD_W + 18)))
        for i, slot in enumerate(slots):
            card = self._build_slot_card(parent, preset, slots, i)
            card.grid(row=i // cols, column=i % cols, padx=8, pady=8, sticky="n")
        for c in range(cols):
            parent.grid_columnconfigure(c, weight=1)

    def _build_slot_card(self, parent, preset, slots, index):
        slot = slots[index]
        card = tk.Frame(parent, bg=Theme.CARD, highlightthickness=1,
                        highlightbackground=Theme.BORDER, width=CARD_W, height=CARD_H, cursor="hand2")
        card.pack_propagate(False)

        thumb = tk.Label(card, bg=Theme.ACCENT_SOFT, fg=Theme.TEXT_MUTED, text="No image",
                         font=Theme.F_TINY, width=THUMB_W, height=1)
        thumb.place(x=1, y=1, width=CARD_W - 2, height=THUMB_H)
        shot = slot.get("screenshot")
        if shot:
            path = os.path.join(preset_dir(preset), shot)
            self.thumbs.request(path, (CARD_W - 2, THUMB_H), cover=True,
                                callback=lambda photo, lbl=thumb: self._set_thumb(lbl, photo))

        body = tk.Frame(card, bg=Theme.CARD)
        body.place(x=12, y=THUMB_H + 10, width=CARD_W - 24, height=CARD_H - THUMB_H - 16)
        head = tk.Frame(body, bg=Theme.CARD)
        head.pack(fill="x")
        kind_txt, kind_col = KIND_LABELS.get(slot.get("kind", "quick"), ("QUICK", "ACCENT"))
        chip(head, kind_txt, fg=Theme.color(kind_col), bg=Theme.PANEL_ALT, padx=7, pady=2).pack(side="left")
        if slot.get("pinned"):
            tk.Label(head, text="★", bg=Theme.CARD, fg=Theme.WARN, font=Theme.F_SMALL_B).pack(side="right")
        tk.Label(body, text=slot_title(slot), bg=Theme.CARD, fg=Theme.TEXT, font=Theme.F_SMALL_B,
                anchor="w").pack(fill="x", pady=(6, 0))
        tk.Label(body, text=f"{rel_time(slot['timestamp'])} · {human_size(slot.get('size'))}",
                bg=Theme.CARD, fg=Theme.TEXT_MUTED, font=Theme.F_TINY, anchor="w").pack(fill="x")

        bind_tree(card, "<Button-1>", lambda _e, i=index: self.open_slot_dialog(preset, slots, i))
        bind_tree(card, "<Button-3>", lambda e, s=slot: self._slot_menu(e, preset, s))
        bind_tree(card, "<Enter>", lambda _e, c=card: c.configure(highlightbackground=Theme.ACCENT))
        bind_tree(card, "<Leave>", lambda _e, c=card: c.configure(highlightbackground=Theme.BORDER))
        return card

    def _set_thumb(self, label, photo):
        if not label.winfo_exists():
            return
        if photo is not None:
            label.configure(image=photo, text="")
            label.image = photo
        else:
            label.configure(text="No image")

    def _render_list(self, parent, preset, slots):
        for i, slot in enumerate(slots):
            row = tk.Frame(parent, bg=Theme.CARD, highlightthickness=1,
                           highlightbackground=Theme.BORDER, cursor="hand2")
            row.pack(fill="x", pady=4)
            inner = tk.Frame(row, bg=Theme.CARD)
            inner.pack(fill="x", padx=16, pady=11)
            kind_txt, kind_col = KIND_LABELS.get(slot.get("kind", "quick"), ("QUICK", "ACCENT"))
            chip(inner, kind_txt, fg=Theme.color(kind_col), bg=Theme.PANEL_ALT, padx=7, pady=2).pack(side="left")
            if slot.get("pinned"):
                tk.Label(inner, text="★", bg=Theme.CARD, fg=Theme.WARN, font=Theme.F_SMALL_B).pack(
                    side="left", padx=(7, 0))
            txt = tk.Frame(inner, bg=Theme.CARD)
            txt.pack(side="left", fill="x", expand=True, padx=12)
            tk.Label(txt, text=slot_title(slot), bg=Theme.CARD, fg=Theme.TEXT, font=Theme.F_BODY_B,
                    anchor="w").pack(anchor="w")
            tk.Label(txt, text=f"{fmt_ts(slot['timestamp'])} · {rel_time(slot['timestamp'])} · "
                              f"{human_size(slot.get('size'))} · {slot.get('files') or '?'} file(s)",
                    bg=Theme.CARD, fg=Theme.TEXT_MUTED, font=Theme.F_TINY, anchor="w").pack(anchor="w", pady=(2, 0))
            RoundButton(inner, text="Restore", icon="⤒", kind="secondary", height=28,
                       command=lambda sn=slot["slot"]: self.do_restore(preset, sn)).pack(side="right")
            bind_tree(row, "<Button-1>", lambda _e, i=i: self.open_slot_dialog(preset, slots, i))
            bind_tree(row, "<Button-3>", lambda e, s=slot: self._slot_menu(e, preset, s))
            bind_tree(row, "<Enter>", lambda _e, r=row: r.configure(highlightbackground=Theme.BORDER_STRONG))
            bind_tree(row, "<Leave>", lambda _e, r=row: r.configure(highlightbackground=Theme.BORDER))

    def _slot_menu(self, event, preset, slot):
        m = tk.Menu(self, tearoff=0, bg=Theme.PANEL, fg=Theme.TEXT,
                   activebackground=Theme.ACCENT, activeforeground=Theme.ACCENT_TEXT,
                   bd=0, relief="flat", font=Theme.F_BODY)
        m.add_command(label="Restore", command=lambda: self.do_restore(preset, slot["slot"]))
        m.add_command(label="Unpin" if slot.get("pinned") else "Pin",
                      command=lambda: self._toggle_slot_pin(preset, slot))
        m.add_command(label="Export ZIP…", command=lambda: self._export_from_menu(preset, slot))
        m.add_command(label="Open folder", command=lambda: open_in_file_manager(slot_folder(preset, slot["slot"])))
        m.add_separator()
        m.add_command(label="Delete…", command=lambda: self._delete_from_menu(preset, slot))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _toggle_slot_pin(self, preset, slot):
        update_slot(preset, slot["slot"], pinned=not slot.get("pinned"))
        self.render_content()

    def _export_from_menu(self, preset, slot):
        default = f"{sanitize(preset['name'])}_{slot_title(slot)}.zip".replace(" ", "_")
        dest = filedialog.asksaveasfilename(parent=self, title="Export quicksave", defaultextension=".zip",
                                            initialfile=default, filetypes=[("ZIP archive", "*.zip")])
        if not dest:
            return
        ok, msg = export_slot_zip(preset, slot, dest)
        self.toast.show(msg, kind="ok" if ok else "error")
        self.set_status(msg)

    def _delete_from_menu(self, preset, slot):
        if self.settings.get("confirm_delete", True):
            confirmed, _ = ask_confirm(self, "Delete quicksave",
                                       f"Delete {slot_title(slot)} for '{preset['name']}'?",
                                       confirm_text="Delete")
            if not confirmed:
                return
        ok, msg = delete_slot(preset, slot["slot"])
        self.toast.show(msg, kind="ok" if ok else "error")
        self.set_status(msg)
        self.render_content()

    def open_slot_dialog(self, preset, slots, index):
        SlotDialog(self, self, preset, slots, index)

    def do_import_zip(self, preset):
        path = filedialog.askopenfilename(parent=self, title="Import quicksave ZIP",
                                          filetypes=[("ZIP archive", "*.zip")])
        if not path:
            return
        ok, msg, _slot = import_zip_as_slot(preset, path)
        self.toast.show(msg, kind="ok" if ok else "error")
        self.set_status(msg)
        self.render_content()

    def do_archive(self, preset):
        label = prompt_text(self, "Archive save", "Optional name for this permanent archive:", "")
        if label is None:
            return
        self.do_quicksave(preset, kind="manual", pinned=True, label=label)

    # ------------------------------------------------------------------ #
    # Save / Restore actions (hotkey-safe: always hop to the main thread)
    # ------------------------------------------------------------------ #
    def do_quicksave(self, preset, kind="quick", pinned=False, label=""):
        self.after(0, lambda: self._do_quicksave_main(preset["id"], kind, pinned, label))

    def _do_quicksave_main(self, pid, kind, pinned, label):
        preset = self.find_preset(pid)
        if preset is None:
            return
        screenshot_fn = None
        if self.settings.get("screenshots", True) and ImageGrab is not None:
            screenshot_fn = self._capture_screenshot_hidden
        excludes = parse_excludes(preset.get("exclude", ""))
        ok, msg, slot = perform_quicksave(preset, kind=kind, pinned=pinned, label=label,
                                          screenshot_fn=screenshot_fn, excludes=excludes)
        quiet = (kind == "auto")   # autosave runs silently: no sound, no toast popup
        if ok:
            prune_excess_slots(preset)
            if kind == "auto":
                prune_excess_autosaves(preset, self._effective_autosave_max(preset))
            # Pruning above can delete other slots' screenshot files on disk,
            # and this save just wrote a brand-new one of its own. Rather than
            # tracking down every individual path that might now be stale,
            # clear the whole thumbnail cache so nothing from a previous
            # moment can ever be shown in place of this (or a later) save's
            # own frame.
            self.thumbs.invalidate()
            self.save_all()
            # A new slot changes what the rewind history even looks like,
            # and the live save has moved on — any previous rewind/forward
            # position is no longer meaningful, so drop it. Without this,
            # a later rewind press could skip taking a fresh safety
            # snapshot because it still looked like we were mid-rewind.
            self.rewind_cursor.pop(pid, None)
            if self.settings.get("sounds", True) and not quiet:
                play_wav(SND_SAVE)
        elif self.settings.get("sounds", True) and not quiet:
            play_wav(SND_ERROR)
        if not quiet:
            self.toast.show(msg, kind="ok" if ok else "error")
        self.set_status(msg)
        if self.selected_id == pid:
            self.render_content()

    def do_restore(self, preset, slot_num=None):
        self.after(0, lambda: self._do_restore_main(preset["id"], slot_num))

    def _do_restore_main(self, pid, slot_num):
        preset = self.find_preset(pid)
        if preset is None:
            return
        if self.settings.get("confirm_restore", False):
            confirmed, _ = ask_confirm(self, "Restore quicksave",
                                       f"Overwrite the live save for '{preset['name']}'?",
                                       confirm_text="Restore", danger=False)
            if not confirmed:
                return
        make_undo = self.settings.get("safety_snapshot", True)
        ok, msg, _slot = restore_slot(preset, slot_num, make_undo=make_undo)
        if ok:
            self.save_all()
            # This is a direct restore, not a rewind step — whatever
            # position the rewind cursor was at no longer reflects reality.
            self.rewind_cursor.pop(pid, None)
            if self.settings.get("sounds", True):
                play_wav(SND_RESTORE)
        elif self.settings.get("sounds", True):
            play_wav(SND_ERROR)
        self.toast.show(msg, kind="ok" if ok else "error")
        self.set_status(msg)
        if self.selected_id == pid:
            self.render_content()

    def do_undo_restore(self, preset):
        self.after(0, lambda: self._do_undo_restore_main(preset["id"]))

    def _do_undo_restore_main(self, pid):
        preset = self.find_preset(pid)
        if preset is None:
            return
        ok, msg = undo_restore(preset)
        if ok:
            self.rewind_cursor.pop(pid, None)
        self.toast.show(msg, kind="ok" if ok else "error")
        self.set_status(msg)
        if self.selected_id == pid:
            self.render_content()

    def do_restore_original(self, preset):
        self.after(0, lambda: self._do_restore_original_main(preset["id"]))

    def _do_restore_original_main(self, pid):
        preset = self.find_preset(pid)
        if preset is None:
            return
        if not has_original_backup(preset):
            self.toast.show("No original backup for this game yet.",
                            "It is created automatically the first time you save.", kind="warn")
            return
        info = (load_meta(preset).get("original") or {})
        when = fmt_ts(info["timestamp"]) if info.get("timestamp") else "an earlier session"
        confirmed, _ = ask_confirm(
            self, "Restore original save",
            f"Put the save folder for '{preset['name']}' back exactly as it was before "
            f"QSave's first save ({when})?\n\n"
            "Your current live save is snapshotted first, so this is still undoable.",
            confirm_text="Restore original", danger=True)
        if not confirmed:
            return
        ok, msg = restore_original(preset, make_undo=self.settings.get("safety_snapshot", True))
        if ok:
            self.rewind_cursor.pop(pid, None)
            if self.settings.get("sounds", True):
                play_wav(SND_RESTORE)
        elif self.settings.get("sounds", True):
            play_wav(SND_ERROR)
        self.toast.show(msg, kind="ok" if ok else "error")
        self.set_status(msg)
        if self.selected_id == pid:
            self.render_content()

    def rewind(self, preset):
        """Restore the next-older save relative to the current rewind cursor."""
        self.after(0, lambda: self._shift_history(preset["id"], 1))

    def forward(self, preset):
        """Restore the next-newer save relative to the current rewind cursor."""
        self.after(0, lambda: self._shift_history(preset["id"], -1))

    def _shift_history(self, pid, direction):
        preset = self.find_preset(pid)
        if preset is None:
            return
        meta = load_meta(preset)
        history = sorted(meta["slots"], key=lambda s: s["timestamp"], reverse=True)
        if not history:
            self.toast.show("No quicksave history yet.", kind="warn")
            return
        cur = self.rewind_cursor.get(pid, -1)
        new_cur = max(0, min(len(history) - 1, cur + direction))
        self.rewind_cursor[pid] = new_cur
        target = history[new_cur]
        ok, msg, _slot = restore_slot(preset, target["slot"],
                                      make_undo=self.settings.get("safety_snapshot", True) and cur == -1)
        self.toast.show(msg, kind="ok" if ok else "error")
        self.set_status(msg)
        if self.selected_id == pid:
            self.render_content()

    def _capture_screenshot_hidden(self, path):
        hide = self.settings.get("hide_window_for_capture", True)
        try:
            visible = self.state() not in ("withdrawn", "iconic")
        except Exception:
            visible = False
        # Only hide when QSave is on screen AND focused. If the game has
        # focus our window is already behind it, so the withdraw/deiconify
        # dance would achieve nothing except stealing focus from the game
        # (which is what caused black first captures and swallowed hotkeys).
        was_visible = bool(hide and visible and self._window_focused())
        try:
            if was_visible:
                self.withdraw()
                self.update_idletasks()
                self.update()
                time.sleep(0.12)   # let the desktop repaint what was behind us
            delay = max(0, int(self.settings.get("capture_delay_ms", 250))) / 1000.0
            if delay:
                time.sleep(delay)
            return take_screenshot(path, max_width=int(self.settings.get("screenshot_max_width", 1600)))
        finally:
            if was_visible:
                self.deiconify()
                self.update()
                # Showing the window again pulls input focus back to QSave for
                # a moment - give the hotkey guard a grace window so the very
                # next keypress is not mistaken for typing inside QSave.
                self._focus_grace_until = time.time() + 1.2

    # ------------------------------------------------------------------ #
    # Settings / theme / misc chrome
    # ------------------------------------------------------------------ #
    def open_settings(self):
        SettingsDialog(self, self)

    def open_about(self):
        AboutDialog(self)

    def open_donate(self):
        DonateDialog(self)

    def show_tutorial(self):
        OnboardingDialog(self)

    def refresh_pills(self):
        self.refresh_sidebar()

    def save_all(self):
        save_config(self.config_data)

    def set_status(self, text):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {text}"
        self.status_var.set(line)
        self._activity_log.appendleft(line)
        log_to_file(text)

    def show_activity_log(self):
        d = Dialog(self, "Activity log", resizable=True)
        d.geometry("560x420")
        d.header("Recent activity")
        area = ScrollArea(d.body, bg=Theme.PANEL)
        area.pack(fill="both", expand=True, padx=26, pady=(4, 10))
        if not self._activity_log:
            tk.Label(area.inner, text="Nothing logged yet.", bg=Theme.PANEL,
                    fg=Theme.TEXT_MUTED, font=Theme.F_BODY).pack(pady=20)
        for line in self._activity_log:
            tk.Label(area.inner, text=line, bg=Theme.PANEL, fg=Theme.TEXT_SOFT,
                    font=Theme.F_MONO, anchor="w", justify="left").pack(fill="x", pady=1)
        f = d.footer()
        RoundButton(f, text="Open log file", kind="secondary",
                   command=lambda: open_in_file_manager(APP_DIR)).pack(side="left")
        RoundButton(f, text="Close", kind="primary", command=d.on_cancel).pack(side="right")
        d.finish()

    def apply_window_flags(self):
        try:
            self.attributes("-topmost", bool(self.settings.get("always_on_top", False)))
        except Exception:
            pass

    def _on_root_configure(self, event):
        # Only remember geometry while the window is in its plain "normal"
        # (not maximized/withdrawn/iconic) state — that's the only state
        # whose geometry string is safe to reapply verbatim on next launch.
        if event.widget is not self:
            return
        try:
            if self.state() == "normal":
                self._normal_geometry = self.geometry()
        except Exception:
            pass

    def _restore_maximized(self):
        try:
            self.state("zoomed")
        except Exception:
            try:
                self.attributes("-zoomed", True)   # Linux window managers
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Hotkeys
    # ------------------------------------------------------------------ #
    def _on_window_focus_in(self, _e=None):
        self._window_has_focus = True

    def _on_window_focus_out(self, _e=None):
        self._window_has_focus = False

    def _window_focused(self):
        # focus_get()/focus_displayof() are queried from the keyboard hook
        # thread and are unreliable there; worse, hiding + re-showing the
        # window for a screenshot briefly hands focus back to QSave, which
        # used to make the NEXT hotkey press get swallowed (the "press End
        # twice" bug). Real FocusIn/FocusOut events plus a short grace
        # period after a capture fix both problems.
        if time.time() < getattr(self, "_focus_grace_until", 0.0):
            return False
        try:
            if self.state() in ("withdrawn", "iconic"):
                return False
        except Exception:
            pass
        return bool(getattr(self, "_window_has_focus", False))

    def _accept_hotkey(self, name, min_gap=0.25):
        # Swallow a duplicate delivery of the same physical keypress without
        # ever blocking a genuine second press.
        now = time.time()
        if now - self._hotkey_last_fire.get(name, 0.0) < min_gap:
            return False
        self._hotkey_last_fire[name] = now
        return True

    def _hotkeys_should_fire(self):
        if self.settings.get("hotkeys_paused"):
            return False
        if self.settings.get("ignore_when_focused", True) and self._window_focused():
            return False
        return True

    def rebind_hotkeys(self):
        if keyboard is None:
            return
        for h in self.hotkey_handles:
            try:
                keyboard.remove_hotkey(h)
            except Exception:
                pass
        self.hotkey_handles = []

        seen = {}
        conflicts = []
        for preset in self.presets:
            if not preset.get("enabled", True):
                continue
            pid = preset["id"]
            for key_field, action in (("save_key", "quicksave"), ("restore_key", "restore")):
                key = (preset.get(key_field) or "").strip().lower()
                if not key:
                    continue
                if key in seen and seen[key] != pid:
                    conflicts.append(key)
                seen[key] = pid
                try:
                    if action == "quicksave":
                        h = keyboard.add_hotkey(key, self._make_quicksave_cb(pid))
                    else:
                        h = keyboard.add_hotkey(key, self._make_restore_cb(pid))
                    self.hotkey_handles.append(h)
                except Exception as e:
                    print(f"Failed to bind '{key}' for '{preset['name']}': {e}")
        if conflicts:
            self.toast.show("Hotkey conflict",
                            f"Duplicate key(s): {', '.join(sorted(set(conflicts)))}", kind="warn")

    def _make_quicksave_cb(self, pid):
        def cb():
            if not self._hotkeys_should_fire():
                return
            if not self._accept_hotkey("quicksave:" + pid):
                return
            preset = self.find_preset(pid)
            if preset:
                self.do_quicksave(preset, kind="quick")
        return cb

    def _make_restore_cb(self, pid):
        def cb():
            if not self._hotkeys_should_fire():
                return
            if not self._accept_hotkey("restore:" + pid):
                return
            preset = self.find_preset(pid)
            if preset:
                self.do_restore(preset, None)
        return cb

    def bind_global_hotkeys(self):
        if keyboard is None:
            return
        for h in self.global_hotkey_handles:
            try:
                keyboard.remove_hotkey(h)
            except Exception:
                pass
        self.global_hotkey_handles = []
        gh = self.settings.get("global_hotkeys", {})

        def active_preset():
            return self.find_preset(self.selected_id) if self.selected_id else None

        bindings = {
            "quicksave_active": lambda: active_preset() and self.do_quicksave(active_preset(), kind="quick"),
            "restore_active": lambda: active_preset() and self.do_restore(active_preset(), None),
            "rewind": lambda: active_preset() and self.rewind(active_preset()),
            "forward": lambda: active_preset() and self.forward(active_preset()),
            "pause_toggle": self.toggle_hotkeys_paused,
        }
        for key_name, fn in bindings.items():
            key = (gh.get(key_name) or "").strip().lower()
            if not key:
                continue
            try:
                h = keyboard.add_hotkey(key, self._guarded(fn, key_name == "pause_toggle"))
                self.global_hotkey_handles.append(h)
            except Exception as e:
                print(f"Failed to bind global hotkey '{key}': {e}")

    def _guarded(self, fn, always=False):
        def cb():
            if not always and not self._hotkeys_should_fire():
                return
            self.after(0, fn)
        return cb

    def toggle_hotkeys_paused(self):
        self.settings["hotkeys_paused"] = not self.settings.get("hotkeys_paused", False)
        self.save_all()
        self.pause_chip.configure(
            text="⏸  Hotkeys paused" if self.settings["hotkeys_paused"] else "●  Hotkeys live",
            fg=Theme.DANGER if self.settings["hotkeys_paused"] else Theme.OK,
            bg=Theme.DANGER_SOFT if self.settings["hotkeys_paused"] else Theme.OK_SOFT)
        self.toast.show("Hotkeys paused" if self.settings["hotkeys_paused"] else "Hotkeys resumed", kind="warn")

    # ------------------------------------------------------------------ #
    # Autosave
    # ------------------------------------------------------------------ #
    AUTOSAVE_POLL_MS = 20_000   # how often we check whether it's time to autosave

    def _effective_autosave_minutes(self, preset):
        """This preset's own interval override, or the global default."""
        own = int(preset.get("autosave_minutes", 0) or 0)
        if own > 0:
            return own
        return max(1, int(self.settings.get("autosave_minutes", 10)))

    def _effective_autosave_max(self, preset):
        """This preset's own autosave-count cap, or the global default."""
        own = int(preset.get("autosave_max", 0) or 0)
        if own > 0:
            return own
        return max(1, int(self.settings.get("autosave_max_default", 5)))

    def _start_autosave_loop(self):
        if self.autosave_job:
            try:
                self.after_cancel(self.autosave_job)
            except Exception:
                pass
        self.autosave_job = self.after(self.AUTOSAVE_POLL_MS, self._autosave_tick)

    def _autosave_tick(self):
        # Each preset's own "autosave" switch is authoritative: it starts out
        # matching the global switch when the preset is created, but can then
        # be flipped independently in either direction for that one game.
        # Only the currently-selected game can autosave (there's nothing to
        # copy from for a game you aren't looking at), but each preset can
        # have its own interval, so we poll frequently and check elapsed
        # time against that preset's own effective interval.
        if self.selected_id:
            preset = self.find_preset(self.selected_id)
            if preset and preset.get("enabled", True) and preset.get("autosave", False):
                interval_s = self._effective_autosave_minutes(preset) * 60
                last = self._last_autosave_at.get(preset["id"], self._app_start_time)
                if time.time() - last >= interval_s:
                    self._last_autosave_at[preset["id"]] = time.time()
                    self.do_quicksave(preset, kind="auto")
        self._start_autosave_loop()

    # ------------------------------------------------------------------ #
    # Game-launch detection
    # ------------------------------------------------------------------ #
    def _start_game_watch_loop(self):
        if self.game_watch_job:
            try:
                self.after_cancel(self.game_watch_job)
            except Exception:
                pass
        self.game_watch_job = self.after(4000, self._game_watch_tick)

    def _game_watch_tick(self):
        if psutil is not None:
            try:
                self._check_game_processes()
            except Exception:
                pass
        self._start_game_watch_loop()

    def _check_game_processes(self):
        for preset in self.presets:
            pid = preset["id"]
            exe = (preset.get("game_exe") or "").strip()
            if not exe:
                self._exe_was_running.pop(pid, None)
                continue
            is_running = self._is_exe_running(exe)
            was_running = self._exe_was_running.get(pid, False)
            self._exe_was_running[pid] = is_running
            if is_running and not was_running:
                self.after(0, lambda pr=preset: self._on_game_launch_detected(pr))

    def _is_exe_running(self, exe_path):
        target_path = os.path.normcase(os.path.abspath(exe_path))
        target_name = os.path.basename(exe_path).lower()
        try:
            for proc in psutil.process_iter(["name", "exe"]):
                try:
                    info = proc.info
                except Exception:
                    continue
                full = info.get("exe") or ""
                if full and os.path.normcase(full) == target_path:
                    return True
                name = (info.get("name") or "").lower()
                if name and name == target_name:
                    return True
        except Exception:
            pass
        return False

    def _on_game_launch_detected(self, preset):
        try:
            if self.tray_icon is not None or self.state() == "withdrawn":
                self._restore_from_tray()
            else:
                self.deiconify()
            self.lift()
        except Exception:
            pass
        self.select_preset(preset["id"])
        self.toast.show(f"{preset['name']} launched",
                        "QSave is back and ready for quicksave / restore.", kind="info")

    # ------------------------------------------------------------------ #
    # System tray / close
    # ------------------------------------------------------------------ #
    def hide_to_tray(self):
        if pystray is None or Image is None:
            messagebox.showinfo(
                "Tray unavailable",
                "System tray support needs the 'pystray' and 'Pillow' packages.\n\n"
                "Install with:  pip install pystray Pillow",
                parent=self)
            return
        self._ensure_tray()
        self.withdraw()
        self.toast.show("QSave hidden", "Still running in the tray — click the icon to bring it back.",
                        kind="info")

    def _ensure_tray(self):
        if pystray is None or Image is None or self.tray_icon is not None:
            return
        def show(_icon=None, _item=None):
            self.after(0, self._restore_from_tray)

        def quit_app(_icon=None, _item=None):
            self.after(0, self._quit_from_tray)

        img = Image.new("RGB", (64, 64), Theme.ACCENT)
        draw = ImageDraw.Draw(img)
        draw.ellipse((14, 14, 50, 50), fill=Theme.ACCENT_TEXT)
        menu = pystray.Menu(pystray.MenuItem("Show QSave", show, default=True),
                            pystray.MenuItem("Quit", quit_app))
        self.tray_icon = pystray.Icon("qsave", img, APP_NAME, menu)
        self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        self.tray_thread.start()

    def _restore_from_tray(self):
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
        self.deiconify()
        self.lift()

    def _quit_from_tray(self):
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
        self._shutdown()

    def on_close(self):
        if self.settings.get("close_to_tray") and pystray is not None and Image is not None:
            self._ensure_tray()
            self.withdraw()
            self.toast.show("QSave is still running in the tray", kind="info")
            return
        self._shutdown()

    def _shutdown(self):
        try:
            state = self.state()
        except Exception:
            state = "normal"
        self.settings["window_maximized"] = (state == "zoomed")
        try:
            if state == "normal":
                self.settings["geometry"] = self.geometry()
            elif self._normal_geometry:
                # Maximized (or otherwise not "normal") — keep the last known
                # normal-size/position instead of the maximized geometry, so
                # a future un-maximize has something sane to fall back to.
                self.settings["geometry"] = self._normal_geometry
        except Exception:
            pass
        self.settings["sort_mode"] = self.sort_var.get()
        self.settings["view_mode"] = self.view_var.get()
        self.save_all()
        if keyboard is not None:
            try:
                keyboard.unhook_all_hotkeys()
            except Exception:
                pass
        self.destroy()


def _ts(slot):
    try:
        return datetime.fromisoformat(slot["timestamp"]).timestamp()
    except Exception:
        return 0.0


# ===========================================================================
# 13. ENTRY POINT
# ===========================================================================

def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()