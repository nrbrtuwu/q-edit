#!/usr/bin/env python3
"""
q-edit: a small dark-mode multi-track video editor (cut, splice, crop, export).

Needs:  Python 3, FFmpeg on PATH (ffplay too, for preview sound), Pillow, tkinterdnd2 (optional,
        for dragging files in from Explorer)
        winget install ffmpeg
        pip install pillow tkinterdnd2
Run:    python clip_editor.py

Shortcuts:
  Ctrl+N        new project (blank or from a template)
  Ctrl+O        open a project, or start one from a video
  Ctrl+S        save project   (Ctrl+Shift+S: save as)
  Space         play / pause
  Left/Right    step 1 s   (hold Shift for 5 s)
  I / O         mark in / out at the playhead (export and loop range)
  Home / End    jump to in / out
  Ctrl+B        split clips at the playhead
  Ctrl+C / X / V  copy / cut / paste clips (paste goes in at the playhead)
  Del           delete selected clips   (Shift+Del: ripple delete, closes the gap)
  Ctrl+Z / Y    undo / redo
  Ctrl+wheel    zoom timeline   (wheel scrolls it)
"""

import copy
from array import array
import hashlib
import json
import math
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
import wave
import tkinter.font as tkfont
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
except ImportError:
    print("Pillow is missing. Install it with:  pip install pillow")
    sys.exit(1)

try:   # drag and drop from Explorer (pip install tkinterdnd2); the app works without it
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    TkinterDnD = None


NOWIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
# background work (thumbnails, waveforms) yields the CPU to the UI and playback
BGWIN = NOWIN | (subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0)
# project resolutions offered in the Projects window and Timeline > Project resolution
RESOLUTIONS = ["1920x1080   16:9 Full HD", "1080x1920   9:16 vertical (TikTok, Reels, Shorts)",
               "3840x2160   16:9 4K", "2560x1440   16:9 1440p", "1280x720   16:9 HD",
               "1080x1080   1:1 square", "1080x1350   4:5 portrait", "1440x1080   4:3"]
RATIOS = {"Free": None, "16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0, "4:5": 4 / 5, "4:3": 4 / 3}
HANDLE = 6
MIN_CROP = 32
PREVIEW_FPS_CAP = 30
SOUND_LEAD = 0.12   # s: ffplay needs about this long to open the device, so its sound starts ahead
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
MEDIA_EXT = IMAGE_EXT | {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mp3", ".wav", ".m4a",
                         ".aac", ".flac", ".ogg"}
IMAGE_DUR = 5.0
MIN_CLIP = 0.05
MEDIA_TYPES = [("Media", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mp3 *.wav *.m4a *.aac *.flac "
                         "*.ogg *.png *.jpg *.jpeg *.bmp *.webp"), ("All files", "*.*")]
APP_VERSION = "1.11.0"
FROZEN = getattr(sys, "frozen", False)  # True when running from the installed .exe
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).parent
DATA_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
ICON_FILE = DATA_DIR / "clip_editor.ico"
# the install folder is read-only, so the installed app keeps settings in %APPDATA%
SETTINGS_FILE = (Path(os.environ.get("APPDATA", Path.home())) / "q-edit" / "settings.json"
                 if FROZEN else APP_DIR / "clip_editor_settings.json")
PROJECT_EXT = ".qedit"
PROJECT_TYPES = [("q-edit project", "*.qedit")]
TEMPLATES_DIR = SETTINGS_FILE.parent / "templates"   # one folder per template, media copied in
# auto captions: whisper.cpp's whisper-cli turns speech into timed words; the speech model is
# downloaded the first time (shared by the script and the installed app, as it is large)
USER_DIR = Path(os.environ.get("APPDATA", Path.home())) / "q-edit"
MODELS_DIR = USER_DIR / "models"
CACHE_DIR = USER_DIR / "cache"   # caption .ass files, the caption font, the clips' audio for whisper
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
WHISPER_MODELS = {"Fast": ("ggml-small-q5_1.bin", 190), "Accurate": ("ggml-large-v3-turbo-q5_0.bin", 574)}
DTW_PRESETS = {"ggml-small-q5_1.bin": "small", "ggml-large-v3-turbo-q5_0.bin": "large.v3.turbo"}
# whisper's DTW word times come out this late; measured against speech with known word times
# (with the snap to the sound's onset after it: typical error 50 ms, none over 0.3 s)
DTW_SHIFT = 0.17
CAPTION_LANGS = {"English": "en", "Hungarian": "hu", "Detect automatically": "auto"}
CAPTION_LENGTHS = {"Short (a few words, TikTok style)": 18, "Long (up to two lines)": 42}
FONTS_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
CAPTION_FONT = "DFP King Gothic Medium"           # default; used when it is installed
FALLBACK_FONTS = ["Segoe UI Bold", "Arial Bold"]   # otherwise, and for letters it doesn't have
# a few of the most common words, to tell English and Hungarian sentences apart
EN_WORDS = set("""the a an and or but of to in on at for with is are was were be been it this that
these those you your i we they he she my our me us not no do does did have has had will would can
could just so what if there here all about from up out get got like know""".split())
HU_WORDS = set("""a az és hogy nem is egy de ez az meg van volt lesz csak már még mert ha mint vagy
itt ott én te ő mi ti ők nekem neked engem téged ezt azt sem pedig akkor most nagyon kell lehet
igen jó mit miért hol hogyan vagyok vagy vagyunk szia sziasztok köszönöm""".split())
HU_LETTERS = set("áéíóöőúüű")
HL_COLORS = {"Yellow": "#FFE000", "Green": "#3CFF6A", "Cyan": "#3CDCFF", "Pink": "#FF50D2",
             "Orange": "#FF9A1E", "Red": "#FF3C3C"}   # the spoken word in a caption


def migrate_settings_dir():
    """Before 1.1.1 the app was called Clip Editor and kept its settings in %APPDATA%\\Clip Editor."""
    old = SETTINGS_FILE.parent.parent / "Clip Editor"
    if FROZEN and old.is_dir() and not SETTINGS_FILE.parent.exists():
        try:
            old.rename(SETTINGS_FILE.parent)
        except OSError:
            pass


def load_settings():
    """{"export": {...last export settings}, "preview_volume": int, "recent": [project paths]}"""
    try:
        s = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(s, dict):
        return {}
    return {"export": s} if "preset" in s else s   # 1.0.0 stored the export settings bare


def save_settings(s):
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    except OSError:
        pass

# ---------------------------------------------------------------- theme
BG, PANEL, FIELD, BORDER = "#16161a", "#1e1e24", "#2a2a33", "#383844"
FG, MUTED, ACCENT, ACCENT_H = "#e8e8ee", "#8c8c9a", "#5b8cff", "#7ca3ff"
HOVER, CANVAS_BG, CUT = "#34343f", "#0e0e11", "#2f4f9e"
UI_FONT = ("Segoe UI", 10)
SMALL_FONT = ("Segoe UI", 9)
# timeline
HEAD_W, RULER_H, VT_H, AT_H = 116, 24, 58, 44
EDGE, SNAP_PX = 6, 8
LANE, HEAD_BG, PLAYHEAD = "#1a1a20", "#25252d", "#ff4d4d"
CLIP_V, CLIP_V_D, CLIP_A, CLIP_A_D = "#3b5f8f", "#2a4468", "#2f7a55", "#1f553b"
CLIP_C, CLIP_C_D = "#9a6a2a", "#6e4b1c"   # captions
TICKS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800]
TRACK_MIN_H, TRACK_MAX_H = 24, 160
# per-clip settings edited in the inspector; stored flat on the clip dict, missing = default
CLIP_DEFAULTS = {"zoom": 1.0, "pos_x": 0.0, "pos_y": 0.0, "rotation": 0.0, "flip_h": False,
                 "flip_v": False, "crop_l": 0.0, "crop_r": 0.0, "crop_t": 0.0, "crop_b": 0.0,
                 "opacity": 100.0, "fade_in": 0.0, "fade_out": 0.0, "volume": 0.0, "pan": 0.0,
                 "speed": 100.0, "keep_pitch": True, "highlight": True, "hl_color": "Yellow",
                 "font": "DFP King Gothic Medium"}
THUMB_H = 72          # height thumbnails are extracted at
PEAKS_PER_SEC = 100   # waveform resolution
# glyphs from Segoe Fluent Icons / Segoe MDL2 Assets (built into Windows 10/11)
ICON = {k: chr(int(v, 16)) for k, v in dict(
    open="E8E5", add="E710", image="EB9F", video="E714", music="E8D6", cut="E8C6",
    delete="E74D", ripple="E750", undo="E7A7", redo="E7A6", zin="E8A3", zout="E71F", fit="E9A6",
    export="E898", vol="E767", mute="E74F", link="E71B", loop="E8EE", play="E768", pause="E769",
    prev="E892", next="E893", cancel="E711", eye="E7B3", hide="ED1A").items()}
ICON_FONT = "Segoe MDL2 Assets"  # replaced by Segoe Fluent Icons in apply_theme when installed


def apply_theme(root):
    global ICON_FONT
    if "Segoe Fluent Icons" in tkfont.families(root):
        ICON_FONT = "Segoe Fluent Icons"
    st = ttk.Style(root)
    st.theme_use("clam")
    root.configure(bg=BG)
    st.configure(".", background=BG, foreground=FG, fieldbackground=FIELD, bordercolor=BORDER,
                 lightcolor=BG, darkcolor=BG, troughcolor=FIELD, focuscolor=BG,
                 selectbackground=ACCENT, selectforeground="white", insertcolor=FG, font=UI_FONT)
    for name, bg in (("TFrame", BG), ("Panel.TFrame", PANEL)):
        st.configure(name, background=bg)
    st.configure("TLabel", background=BG, foreground=FG)
    st.configure("Panel.TLabel", background=PANEL, foreground=FG)
    st.configure("Muted.TLabel", background=BG, foreground=MUTED)
    st.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED)
    st.configure("Head.TLabel", background=BG, foreground=ACCENT, font=("Segoe UI Semibold", 10))
    st.configure("Time.TLabel", background=PANEL, foreground=FG, font=("Consolas", 10))

    st.configure("TButton", background=FIELD, foreground=FG, bordercolor=BORDER, lightcolor=FIELD,
                 darkcolor=FIELD, padding=(12, 6), relief="flat")
    st.map("TButton",
           background=[("disabled", PANEL), ("pressed", BORDER), ("active", HOVER)],
           lightcolor=[("pressed", BORDER), ("active", HOVER)],
           darkcolor=[("pressed", BORDER), ("active", HOVER)],
           bordercolor=[("active", ACCENT)], foreground=[("disabled", MUTED)])
    st.configure("Accent.TButton", background=ACCENT, foreground="white", bordercolor=ACCENT,
                 lightcolor=ACCENT, darkcolor=ACCENT, font=("Segoe UI Semibold", 10))
    st.map("Accent.TButton",
           background=[("disabled", BORDER), ("pressed", "#4a76e0"), ("active", ACCENT_H)],
           lightcolor=[("pressed", "#4a76e0"), ("active", ACCENT_H)],
           darkcolor=[("pressed", "#4a76e0"), ("active", ACCENT_H)],
           bordercolor=[("active", ACCENT_H)])
    # compact flat toolbar buttons; ToolOn = toggled on
    for sty, bg, fg in (("Tool.TButton", PANEL, FG), ("ToolOn.TButton", "#2d3f66", "white"),
                        ("Bar.TButton", BG, FG)):
        st.configure(sty, background=bg, foreground=fg, bordercolor=bg, lightcolor=bg,
                     darkcolor=bg, padding=(7, 3), font=(ICON_FONT, 11))
        st.map(sty, background=[("disabled", bg), ("pressed", BORDER), ("active", HOVER)],
               lightcolor=[("pressed", BORDER), ("active", HOVER)],
               darkcolor=[("pressed", BORDER), ("active", HOVER)],
               bordercolor=[("active", HOVER)], foreground=[("disabled", MUTED)])
    for sty in ("ToolText.TButton", "ToolTextOn.TButton"):
        st.configure(sty, padding=(6, 3), font=SMALL_FONT)
    st.configure("ToolText.TButton", background=PANEL, bordercolor=PANEL, lightcolor=PANEL,
                 darkcolor=PANEL)
    st.configure("ToolTextOn.TButton", background="#2d3f66", foreground="white",
                 bordercolor="#2d3f66", lightcolor="#2d3f66", darkcolor="#2d3f66")
    st.configure("PlayTool.TButton", background=ACCENT, foreground="white", bordercolor=ACCENT,
                 lightcolor=ACCENT, darkcolor=ACCENT, padding=(10, 3), font=(ICON_FONT, 11))
    st.map("PlayTool.TButton", background=[("active", ACCENT_H)], lightcolor=[("active", ACCENT_H)],
           darkcolor=[("active", ACCENT_H)], bordercolor=[("active", ACCENT_H)])
    st.configure("Small.Accent.TButton", padding=(12, 3))
    for sty in ("Tool.TButton", "ToolOn.TButton", "Bar.TButton", "ToolText.TButton",
                "ToolTextOn.TButton", "PlayTool.TButton"):
        st.configure(sty, width=0)
    st.configure("Bar.TFrame", background=BG)
    st.configure("Small.TCombobox", padding=2)
    st.configure("Small.TEntry", padding=3)

    st.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=FG, arrowcolor=FG,
                 bordercolor=BORDER, lightcolor=FIELD, darkcolor=FIELD, padding=4)
    st.map("TCombobox", fieldbackground=[("readonly", FIELD), ("disabled", PANEL)],
           foreground=[("readonly", FG), ("disabled", MUTED)],
           selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)],
           bordercolor=[("focus", ACCENT), ("hover", ACCENT)], background=[("active", HOVER)])
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "white")
    root.option_add("*TCombobox*Listbox.font", UI_FONT)
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    st.configure("TEntry", fieldbackground=FIELD, foreground=FG, bordercolor=BORDER,
                 lightcolor=FIELD, darkcolor=FIELD, padding=5, insertcolor=FG)
    st.map("TEntry", bordercolor=[("focus", ACCENT)], fieldbackground=[("disabled", PANEL)],
           foreground=[("disabled", MUTED)])
    for sty, bg in (("Horizontal.TScale", BG), ("Panel.Horizontal.TScale", PANEL)):
        st.configure(sty, background=ACCENT, troughcolor=FIELD, bordercolor=bg,
                     lightcolor=ACCENT, darkcolor=ACCENT, gripcount=0, sliderthickness=14)
        st.map(sty, background=[("active", ACCENT_H)])
    st.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=FIELD, bordercolor=PANEL,
                 lightcolor=ACCENT, darkcolor=ACCENT, thickness=8)
    for sty, bg in (("TCheckbutton", BG), ("Panel.TCheckbutton", PANEL),
                    ("TRadiobutton", BG)):
        st.configure(sty, background=bg, foreground=FG, indicatorbackground=FIELD,
                     indicatorforeground="white", upperbordercolor=BORDER, lowerbordercolor=BORDER)
        st.map(sty, background=[("active", bg)],
               indicatorbackground=[("selected", ACCENT), ("active", HOVER)])
    st.configure("TSeparator", background=BORDER)
    for sty in ("Horizontal.TScrollbar", "Vertical.TScrollbar"):
        st.configure(sty, background=FIELD, troughcolor=PANEL, bordercolor=PANEL,
                     lightcolor=FIELD, darkcolor=FIELD, arrowcolor=FG)
        st.map(sty, background=[("active", HOVER)])


def dark_titlebar(win):
    if os.name != "nt":
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass


# ---------------------------------------------------------------- ffmpeg helpers
def cp(clip, key):
    return clip.get(key, CLIP_DEFAULTS[key])


def fmt_time(t):
    m, s = divmod(max(t, 0), 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{s:05.2f}" if h else f"{m}:{s:05.2f}"


def probe(path):
    """Returns {"dur", "fps", "audio", "video", "size"}."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,avg_frame_rate,r_frame_rate,width,height:"
         "stream_tags=rotate:stream_side_data=rotation",
         "-of", "json", path],
        capture_output=True, text=True, creationflags=NOWIN)
    try:
        info = json.loads(r.stdout)
    except ValueError:
        return {"dur": 0.0, "fps": 30.0, "audio": False, "video": False}
    try:
        dur = float(info.get("format", {}).get("duration") or 0)
    except ValueError:
        dur = 0.0
    fps, audio, got_video, size = 30.0, False, False, None
    for s in info.get("streams", []):
        if s.get("codec_type") == "audio":
            audio = True
        elif s.get("codec_type") == "video" and not got_video:
            got_video = True
            if s.get("width") and s.get("height"):
                size = (int(s["width"]), int(s["height"]))
                # phone videos are often stored sideways with a rotation flag; FFmpeg shows them
                # upright, so the size has to be turned too
                rot = (s.get("tags") or {}).get("rotate") or next(
                    (d.get("rotation") for d in s.get("side_data_list") or [] if "rotation" in d), 0)
                try:
                    if round(float(rot)) % 180 == 90:
                        size = size[::-1]
                except (TypeError, ValueError):
                    pass
            for key in ("avg_frame_rate", "r_frame_rate"):
                n, _, d = (s.get(key) or "0/0").partition("/")
                try:
                    v = float(n) / float(d or 1)
                except (ValueError, ZeroDivisionError):
                    continue
                if 1 < v <= 240:
                    fps = v
                    break
    return {"dur": dur, "fps": fps, "audio": audio, "video": got_video, "size": size}


def read_media(path):
    """Media info for the timeline, or None if FFmpeg can't use it. Safe to call from a thread."""
    if Path(path).suffix.lower() in IMAGE_EXT:
        try:
            with Image.open(path) as im:
                size = im.size
        except OSError:
            return None
        return {"dur": 0.0, "fps": 30.0, "audio": False, "video": True, "image": True, "size": size}
    info = probe(path)
    if info["dur"] > 0 and (info["video"] or info["audio"]):
        return dict(info, image=False)
    return None


def extract_thumbs(path, m):
    """(seconds per thumbnail, [images THUMB_H high]) for a clip's filmstrip; step 0 = a still."""
    if m["image"]:
        try:
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((THUMB_H * 4, THUMB_H))
                return 0, [im]
        except OSError:
            return 0, []
    step = max(0.25, m["dur"] / 240)
    # long files: decode keyframes only, which is many times faster
    cmd = (["ffmpeg", "-v", "error"] + (["-skip_frame", "nokey"] if m["dur"] > 120 else []) +
           ["-i", path, "-an", "-vf", f"fps=1/{step:.4f},scale=-2:{THUMB_H}",
            "-f", "image2pipe", "-c:v", "bmp", "-"])
    out = subprocess.run(cmd, capture_output=True, creationflags=BGWIN).stdout
    imgs, i = [], 0
    while i + 6 <= len(out):   # concatenated BMPs; each header holds its own size
        size = int.from_bytes(out[i + 2:i + 6], "little")
        if size <= 0:
            break
        try:
            imgs.append(Image.open(BytesIO(out[i:i + size])).convert("RGB"))
        except OSError:
            break
        i += size
    return step, imgs


def extract_peaks(path):
    """Waveform: loudest sample per 1/PEAKS_PER_SEC s, 0-255."""
    rate = 8000
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(rate),
                          "-f", "s16le", "-"], capture_output=True, creationflags=BGWIN).stdout
    a = array("h")
    a.frombytes(raw[:len(raw) // 2 * 2])
    n = rate // PEAKS_PER_SEC
    return bytes(min(255, max(-min(a[i:i + n]), max(a[i:i + n])) * 255 // 32767)
                 for i in range(0, len(a) - n + 1, n))


def grab_frame(path, t):
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
           "-f", "image2pipe", "-c:v", "bmp", "-"]
    return run_bmp(cmd)


def run_bmp(cmd):
    out = subprocess.run(cmd, capture_output=True, creationflags=NOWIN).stdout
    if not out:
        return None
    return Image.open(BytesIO(out)).convert("RGB")


def whisper_exe():
    """whisper-cli: next to the installed app, from packaging/whisper_dl when running the
    script, or on PATH."""
    for p in (APP_DIR / "whisper" / "whisper-cli.exe",
              APP_DIR / "packaging" / "whisper_dl" / "bin" / "Release" / "whisper-cli.exe"):
        if p.exists():
            return str(p)
    return shutil.which("whisper-cli")


_cap_fonts, _font_list, _glyphs = {}, None, {}


def installed_fonts():
    """{font name: (file, index in a .ttc)} of the fonts installed for everyone and for this user,
    as Windows lists them (e.g. "Segoe UI Bold")."""
    global _font_list
    if _font_list is None:
        _font_list = {}
        try:
            import winreg
        except ImportError:
            return _font_list
        key = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for i in range(winreg.QueryInfoKey(k)[1]):
                        name, value, _ = winreg.EnumValue(k, i)
                        path = Path(value) if os.path.isabs(value) else FONTS_DIR / value
                        ext = path.suffix.lower()
                        if ext not in (".ttf", ".otf", ".ttc"):
                            continue
                        name = re.sub(r"\s*\((TrueType|OpenType)\)$", "", name)
                        for n, part in enumerate(name.split(" & ")):
                            _font_list[part.strip()] = (str(path), n if ext == ".ttc" else 0)
            except OSError:
                pass
    return _font_list


def font_face(name):
    """An installed font's name for `name` (spaces and case don't matter), else the fallback."""
    fonts = installed_fonts()
    if name in fonts:
        return name
    squash = {k.lower().replace(" ", ""): k for k in fonts}
    hit = squash.get((name or "").lower().replace(" ", ""))
    return hit or next((f for f in FALLBACK_FONTS if f in fonts), None)


def win_em(path, index=0):
    """em size / line height the way libass sizes a font: unitsPerEm / (usWinAscent +
    usWinDescent) from the font file's head and OS/2 tables (index = face in a .ttc). None if
    the file can't be read that way."""
    try:
        with open(path, "rb") as f:
            def at(pos, fmt):
                f.seek(pos)
                return struct.unpack(fmt, f.read(struct.calcsize(fmt)))
            base = 0
            if at(0, ">4s")[0] == b"ttcf":
                base = at(12 + 4 * index, ">I")[0]
            tables = {}
            for i in range(at(base + 4, ">H")[0]):
                tag, _, off, _ = at(base + 12 + 16 * i, ">4sIII")
                tables[tag] = off
            upem = at(tables[b"head"] + 18, ">H")[0]
            asc, desc = at(tables[b"OS/2"] + 74, ">HH")
        return upem / (asc + desc) if asc + desc else None
    except (OSError, KeyError, struct.error):
        return None


def caption_font(size, name=CAPTION_FONT):
    """The caption font at a libass font size: libass sizes a font by its line height (ascent
    + descent), PIL by its em, so the PIL size is converted to make both draw the same text."""
    size, face = max(4, int(round(size))), font_face(name)
    key = (size, face)
    if key not in _cap_fonts:
        if len(_cap_fonts) > 64:
            _cap_fonts.clear()
        try:
            path, index = installed_fonts()[face]
            em = win_em(path, index)
            if not em:
                a, d = ImageFont.truetype(path, 1000, index=index).getmetrics()
                em = 1000 / (a + d)
            _cap_fonts[key] = ImageFont.truetype(path, max(4, round(size * em)), index=index)
        except (KeyError, OSError):
            _cap_fonts[key] = ImageFont.load_default()
    return _cap_fonts[key]


def has_glyphs(name, text):
    """False when the font draws any letter of text as its "missing" box (e.g. ő in a font
    made for another alphabet)."""
    font = caption_font(48, name)
    if "notdef" not in _glyphs.setdefault(name, {}):
        _glyphs[name]["notdef"] = bytes(font.getmask("\U000F0000"))
    seen = _glyphs[name]
    for ch in set(text) - set(" \n"):
        if ch not in seen:
            seen[ch] = bytes(font.getmask(ch)) != seen["notdef"]
        if not seen[ch]:
            return False
    return True


def cap_font(c):
    """The installed font a caption is drawn with: its own, or the fallback when that isn't
    installed or lacks some of the caption's letters."""
    face = font_face(cp(c, "font"))
    if face and not has_glyphs(face, c["caption"]):
        face = font_face(FALLBACK_FONTS[0])
    return face


def caption_style(vw, vh):
    """(font size, outline) of captions in frame pixels: about 8.5% of the short side."""
    fs = max(12, round(min(vw, vh) * 0.085))
    return fs, max(1, round(fs * 0.09))


def caption_lines(text, vw, vh, face=CAPTION_FONT):
    """Wraps a caption to 88% of the frame width (explicit line breaks are kept). Done here, not
    by libass, so the preview's own drawing breaks the lines at the same words."""
    fs, _ = caption_style(vw, vh)
    font, out = caption_font(fs, face), []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            test = f"{line} {word}".strip()
            if line and font.getlength(test) > vw * 0.88:
                out.append(line)
                line = word
            else:
                line = test
        if line:
            out.append(line)
    return out or [""]


def caption_image(text, vw, vh, scale, active=None, color="#FFE000", face=CAPTION_FONT):
    """The caption drawn like the export draws it (white, black outline, centered lines), at
    `scale` times frame pixels, as RGBA. active = index of the word drawn in `color`."""
    fs, bord = caption_style(vw, vh)
    font = caption_font(fs * scale, face)
    lines = caption_lines(text, vw, vh, face)
    lh, b = max(1, round(fs * scale)), max(1, round(bord * scale))
    widths = [font.getlength(t) for t in lines]
    W, H = int(max(widths) + 2 * b + 2), int(lh * len(lines) + 2 * b)
    im = Image.new("RGBA", (max(W, 2), max(H, 2)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    top = b + (lh - sum(font.getmetrics())) / 2
    n = 0   # words before this line
    for i, (t, w) in enumerate(zip(lines, widths)):
        x, y = (W - w) / 2, top + i * lh
        d.text((x, y), t, font=font, fill="white", stroke_width=b, stroke_fill="black")
        words = t.split(" ")
        if active is not None and n <= active < n + len(words):
            j = active - n
            pre = " ".join(words[:j]) + (" " if j else "")
            d.text((x + font.getlength(pre), y), words[j], font=font, fill=color,
                   stroke_width=b, stroke_fill="black")
        n += len(words)
    return im


def caption_words(c):
    """[(start, end, word)] of a caption in its own time (inp-based, so trims and splits keep
    them in place). The words recognized with the caption, or, once its text was edited into a
    different number of words, the text spread evenly over the clip."""
    words = c["caption"].split()
    timed = c.get("words") or []
    if len(timed) == len(words):
        return [(a, b, w) for (a, b, _), w in zip(timed, words)]
    n = max(len(words), 1)
    a0, d = c["inp"], c["dur"]
    return [(a0 + d * i / n, a0 + d * (i + 1) / n, w) for i, w in enumerate(words)]


def active_word(c, t):
    """Index of the word being said at timeline time t (the last one that has started)."""
    local = c["inp"] + (t - c["start"])
    idx = None
    for i, (a, _, _) in enumerate(caption_words(c)):
        if a <= local + 1e-6:
            idx = i
    return idx


def ass_color(hexcolor):
    """'#RRGGBB' -> ASS '&HBBGGRR&'."""
    h = hexcolor.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def ass_time(t):
    cs = max(0, int(round(t * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def ff_path(path):
    """A path inside a filtergraph option ('C\\:/dir/file')."""
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def audio_envelope(wav):
    """Loudness in dB of a 16 kHz mono WAV, per 10 ms."""
    with wave.open(str(wav)) as w:
        a = array("h", w.readframes(w.getnframes()))
    return [10 * math.log10(sum(v * v for v in a[i:i + 160]) / 160 + 1)
            for i in range(0, len(a) - 159, 160)]


def snap_onsets(starts, env, back=0.25, fwd=0.12, rise_db=12):
    """Moves each word start to the strongest rise in loudness near it (where the word's sound
    begins), never past its neighbours; a start with no clear rise nearby stays."""
    out = []
    for i, t in enumerate(starts):
        lo, hi = int((t - back) * 100), int((t + fwd) * 100)
        if out:
            lo = max(lo, int(out[-1] * 100) + 5)
        if i + 1 < len(starts):
            hi = min(hi, int(starts[i + 1] * 100) - 3)
        lo, hi = max(lo, 3), min(hi, len(env) - 1)
        best, bt = 0.0, t
        for k in range(lo, hi + 1):
            rise = env[k] - min(env[k - 3:k])
            if rise > best:
                best, bt = rise, k / 100
        out.append(bt if best >= rise_db else t)
    return out


def whisper_sentences(data, env):
    """whisper-cli -ojf -dtw output -> sentences [(start, end, text, [(start, end, word)])] in
    seconds of the WAV. Word starts: DTW token times, DTW_SHIFT earlier, snapped to the onset of
    the sound; a word ends where the next one starts, or earlier at a silence."""
    sents = []
    for seg in data.get("transcription", []):
        words = []
        for t in seg.get("tokens", []):
            txt = t.get("text", "")
            if txt.startswith("[_") or not txt.strip():
                continue
            at = t["t_dtw"] / 100 if t.get("t_dtw", -1) >= 0 else t["offsets"]["from"] / 1000
            if txt.startswith(" ") or not words:
                words.append([at - DTW_SHIFT, txt.strip()])
            else:
                words[-1][1] += txt   # the rest of a word split into several tokens
        if words:
            sents.append([seg["offsets"]["from"] / 1000, seg["offsets"]["to"] / 1000,
                          seg.get("text", "").strip(), words])
    flat = [w for s in sents for w in s[3]]
    for w, t in zip(flat, snap_onsets([max(0.0, w[0]) for w in flat], env)):
        w[0] = t
    srt = sorted(env)
    quiet = srt[len(srt) // 10] + max(6.0, (srt[len(srt) * 9 // 10] - srt[len(srt) // 10]) * 0.25) \
        if srt else 0.0
    out, i = [], -1
    for s in sents:
        ws = []
        for w in s[3]:
            i += 1
            nxt = flat[i + 1][0] if i + 1 < len(flat) else w[0] + 1.0
            end, k, run = nxt, int((w[0] + 0.12) * 100), 0
            while k < min(int(nxt * 100), len(env)):   # 0.25 s of quiet ends the word
                run = run + 1 if env[k] < quiet else 0
                if run >= 25:
                    end = (k - 24) / 100
                    break
                k += 1
            ws.append((w[0], max(end, w[0] + 0.05), w[1]))
        loud = max(env[int(ws[0][0] * 100):int(ws[-1][1] * 100) + 1] or [0.0])
        out.append((ws[0][0], ws[-1][1], s[2], ws, loud > quiet))
    return out


def text_language(text):
    """'en' or 'hu' when the words clearly say so, else None."""
    words = re.findall(r"[^\W\d_]+", text.lower())
    hu = sum(w in HU_WORDS for w in words) + 2 * sum(any(ch in HU_LETTERS for ch in w) for w in words)
    en = sum(w in EN_WORDS for w in words)
    if en >= 2 and en > hu:
        return "en"
    if hu >= 2 and hu > en:
        return "hu"
    return None


def group_words(words, max_chars):
    """Timed words -> captions [(start, end, text, words)]: a caption ends at a sentence end, a
    pause, or when it would get longer than max_chars."""
    caps, cur = [], []

    def flush():
        """A phrase too long for one caption is cut into even parts, so no caption is left
        with one or two stray words."""
        if not cur:
            return
        n = -(-len(" ".join(w for _, _, w in cur)) // max_chars)
        target = len(" ".join(w for _, _, w in cur)) / max(n, 1)
        part, size = [], 0
        for item in cur:
            add = len(item[2]) + (1 if part else 0)
            if part and n > 1 and size + add / 2 > target:
                caps.append([part[0][0], part[-1][1], " ".join(w for _, _, w in part), part])
                part, size, n, add = [], 0, n - 1, len(item[2])
            part.append(item)
            size += add
        caps.append([part[0][0], part[-1][1], " ".join(w for _, _, w in part), part])
        cur.clear()
    for a, b, w in words:
        if cur:
            text = " ".join(x for _, _, x in cur)
            if (a - cur[-1][1] > 0.7 or text[-1] in ".?!"
                    or b - cur[0][0] > (3.0 if max_chars < 30 else 6.0) * 2):
                flush()
        cur.append((a, b, w))
    flush()
    for i, c in enumerate(caps):   # stay up a moment after the last word, but not into the next
        nxt = caps[i + 1][0] if i + 1 < len(caps) else c[1] + 0.4
        c[1] = max(min(c[1] + 0.3, nxt), min(c[0] + 0.4, nxt))
    return [tuple(c) for c in caps if c[1] - c[0] >= 0.05 and c[2].strip()]


def spd(c):
    """A clip's speed as a factor (2.0 = twice as fast): seconds of file per timeline second."""
    return cp(c, "speed") / 100


def speed_filters(c):
    """Audio filters for a clip's speed: atempo keeps the pitch (it takes 0.5-100 per
    instance, so slow speeds chain it), asetrate shifts the pitch along like a tape."""
    k = spd(c)
    if abs(k - 1) < 1e-6:
        return []
    if not cp(c, "keep_pitch"):
        return [f"asetrate={round(48000 * k)}", "aresample=48000"]
    out = []
    while k < 0.5:
        out.append("atempo=0.5")
        k /= 0.5
    return out + [f"atempo={k:.5f}"]


def size_of(text):
    """'1920x1080   16:9 Full HD' or '1920 x 1080' -> (1920, 1080), even; None if not a size."""
    m = re.match(r"\s*(\d+)\s*[x×*]\s*(\d+)", text or "")
    if not m:
        return None
    w, h = int(m.group(1)) // 2 * 2, int(m.group(2)) // 2 * 2
    return (w, h) if 16 <= w <= 8192 and 16 <= h <= 8192 else None


def parse_size(text):
    text = text.strip().lower()
    if not text:
        return None
    w, h = (int(v) for v in text.split("x"))
    if w < 16 or h < 16:
        raise ValueError
    return w // 2 * 2, h // 2 * 2


# ---------------------------------------------------------------- encoders
# label, ffmpeg encoder, family, codec kind
CODECS = [
    ("H.264  (CPU, x264)", "libx264", "x26x", "h264"),
    ("H.265 / HEVC  (CPU, x265)", "libx265", "x26x", "hevc"),
    ("AV1  (CPU, SVT-AV1)", "libsvtav1", "svtav1", "av1"),
    ("VP9  (CPU)", "libvpx-vp9", "vp9", "vp9"),
    ("H.264  (AMD GPU)", "h264_amf", "amf", "h264"),
    ("H.265  (AMD GPU)", "hevc_amf", "amf", "hevc"),
    ("AV1  (AMD GPU)", "av1_amf", "amf", "av1"),
    ("H.264  (NVIDIA GPU)", "h264_nvenc", "nvenc", "h264"),
    ("H.265  (NVIDIA GPU)", "hevc_nvenc", "nvenc", "hevc"),
    ("AV1  (NVIDIA GPU)", "av1_nvenc", "nvenc", "av1"),
    ("H.264  (Intel GPU)", "h264_qsv", "qsv", "h264"),
    ("H.265  (Intel GPU)", "hevc_qsv", "qsv", "hevc"),
]
HW_FAMILIES = {"amf", "nvenc", "qsv"}
# (best, worst) quality value per encoder or family
QRANGE = {"libx264": (14, 34), "libx265": (16, 36), "libsvtav1": (18, 52),
          "libvpx-vp9": (15, 50), "amf": (14, 38), "nvenc": (16, 40), "qsv": (16, 40)}
QNAME = {"x26x": "CRF", "svtav1": "CRF", "vp9": "CRF", "amf": "QP", "nvenc": "CQ", "qsv": "ICQ"}
CONTAINERS = {"mp4": {"h264", "hevc", "av1", "vp9"}, "mkv": {"h264", "hevc", "av1", "vp9"},
              "mov": {"h264", "hevc"}, "webm": {"vp9", "av1"}}
AUDIO = {"AAC": "aac", "Opus": "opus", "No audio": "none"}
AUDIO_OK = {"mp4": {"aac", "opus", "none"}, "mkv": {"aac", "opus", "none"},
            "mov": {"aac", "none"}, "webm": {"opus", "none"}}
SPEEDS = ["Fast", "Balanced", "Slow (smaller file)"]
FPS_OPTS = ["Source", "24", "25", "30", "50", "60"]
AUDIO_BR = ["96", "128", "160", "192", "256", "320"]


def detect_encoders():
    """Returns (usable video codecs, opus encoder name or None)."""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True,
                             text=True, creationflags=NOWIN).stdout
    except OSError:
        return [], None
    names = {m.group(2) for m in re.finditer(r"^\s*([VA])[\w.]{5}\s+(\S+)", out, re.M)}
    found = [c for c in CODECS if c[1] in names]

    def works(c):
        pix = "nv12" if c[2] == "qsv" else "yuv420p"
        try:
            r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                                "color=black:s=320x240:d=0.2", "-frames:v", "3", "-pix_fmt", pix,
                                "-c:v", c[1], "-f", "null", "-"],
                               capture_output=True, creationflags=NOWIN, timeout=25)
            return r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    hw = [c for c in found if c[2] in HW_FAMILIES]
    with ThreadPoolExecutor(max_workers=4) as ex:
        ok = dict(zip([c[1] for c in hw], ex.map(works, hw)))
    usable = [c for c in found if c[2] not in HW_FAMILIES or ok.get(c[1])]
    opus = "libopus" if "libopus" in names else ("opus" if "opus" in names else None)
    return usable, opus


def quality_value(codec, quality):
    lo, hi = QRANGE.get(codec[1], QRANGE.get(codec[2], (18, 34)))
    return round(lo + (hi - lo) * (1 - quality / 100))


def video_args(codec, speed, mode, quality, kbps, container):
    _, enc, fam, kind = codec
    q = quality_value(codec, quality)
    a = ["-c:v", enc]
    if fam == "x26x":
        a += ["-preset", ["veryfast", "medium", "slow"][speed]]
    elif fam == "svtav1":
        a += ["-preset", ["10", "8", "6"][speed]]
    elif fam == "vp9":
        a += ["-deadline", "good", "-cpu-used", ["5", "3", "1"][speed], "-row-mt", "1"]
    elif fam == "amf":
        a += ["-quality", ["speed", "balanced", "quality"][speed]]
    elif fam == "nvenc":
        a += ["-preset", ["p2", "p5", "p7"][speed]]
    elif fam == "qsv":
        a += ["-preset", ["veryfast", "medium", "slower"][speed]]

    if mode == "quality":
        if fam in ("x26x", "svtav1"):
            a += ["-crf", str(q)]
        elif fam == "vp9":
            a += ["-crf", str(q), "-b:v", "0"]
        elif fam == "amf":
            a += ["-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q)]
        elif fam == "nvenc":
            a += ["-rc", "vbr", "-cq", str(q), "-b:v", "0"]
        elif fam == "qsv":
            a += ["-global_quality", str(q)]
    else:
        k = max(100, int(kbps))
        a += ["-b:v", f"{k}k"]
        if fam != "svtav1":
            a += ["-maxrate", f"{int(k * 1.5)}k", "-bufsize", f"{k * 2}k"]
        if fam == "amf":
            a += ["-rc", "vbr_peak"]
        elif fam == "nvenc":
            a += ["-rc", "vbr"]

    a += ["-pix_fmt", "nv12" if fam == "qsv" else "yuv420p"]
    if kind == "hevc" and container in ("mp4", "mov"):
        a += ["-tag:v", "hvc1"]
    if enc == "libx265":
        a += ["-x265-params", "log-level=error"]
    return a


def audio_args(audio, abr, opus_enc):
    if audio == "none":
        return ["-an"]
    if audio == "opus":
        a = ["-c:a", opus_enc or "libopus", "-b:a", f"{abr}k"]
        return a + (["-strict", "-2"] if opus_enc == "opus" else [])
    return ["-c:a", "aac", "-b:a", f"{abr}k"]


# ---------------------------------------------------------------- playback
class Player:
    """Reads raw RGB frames from an ffmpeg command in a thread.
    Sound: a second ffmpeg mixes the audio to WAV and pipes it into ffplay. The picture's
    graph can take a while to start (many clips, sped-up clips), so ffplay is only started
    once the first frame is there; the sound's ffmpeg starts right away and waits in its pipe.
    acmd starts SOUND_LEAD later than the picture, which covers ffplay's own start-up."""

    def __init__(self, vcmd, acmd, size, fps, volume=100):
        self.w, self.h = size
        self.fps = fps
        self.stopped = False
        self.first_at = None   # perf_counter() when the first frame came in: the clock's zero
        self.frames = queue.Queue(maxsize=8)
        self.lock = threading.Lock()
        self.proc = subprocess.Popen(vcmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     stdin=subprocess.DEVNULL, creationflags=NOWIN)
        self.feed = self.sound = None
        self.volume = volume
        self._open_feed(acmd)
        threading.Thread(target=self._read, daemon=True).start()

    def _open_feed(self, acmd):
        if acmd and self.volume >= 1 and shutil.which("ffplay"):
            self.feed = subprocess.Popen(acmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         stdin=subprocess.DEVNULL, creationflags=NOWIN)

    def _start_sound(self):
        with self.lock:
            if self.feed and not self.sound and not self.stopped:
                self.sound = subprocess.Popen(
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                     "-volume", str(int(min(max(self.volume, 0), 100))), "-i", "-"],
                    stdin=self.feed.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=NOWIN)
                self.feed.stdout.close()

    def set_sound(self, acmd, volume):
        """(Re)starts the sound; used for a volume change while the picture keeps playing."""
        with self.lock:
            for p in (self.feed, self.sound):
                if p:
                    try:
                        p.kill()
                    except OSError:
                        pass
            self.feed = self.sound = None
            self.volume = volume
            self._open_feed(acmd)
        if self.first_at is not None:
            self._start_sound()

    def _put(self, item):
        while not self.stopped:
            try:
                self.frames.put(item, timeout=0.1)
                return
            except queue.Full:
                pass

    def _read(self):
        n = self.w * self.h * 3
        while not self.stopped:
            buf = self.proc.stdout.read(n)
            if len(buf) < n:
                break
            if self.first_at is None:
                self.first_at = time.perf_counter()
                self._start_sound()
            self._put(buf)
        self._start_sound()   # no picture at all: play the sound anyway
        self._put(None)

    def stop(self):
        with self.lock:
            self.stopped = True
        for p in (self.proc, self.feed, self.sound):
            if p:
                try:
                    p.kill()
                except OSError:
                    pass


# ---------------------------------------------------------------- export dialog
class ExportDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Export")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app.root)
        self.codecs = app.codecs or [CODECS[0]]
        self.by_label = {c[0]: c for c in self.codecs}
        self.by_enc = {c[1]: c for c in self.codecs}
        self.applying = False

        s = app.exp_settings
        self.v = {
            "preset": tk.StringVar(value=s.get("preset", "Balanced")),
            "container": tk.StringVar(value=s.get("container", "mp4")),
            "codec": tk.StringVar(value=self.label_for(s.get("codec", "libx264"))),
            "speed": tk.StringVar(value=SPEEDS[s.get("speed", 1)]),
            "mode": tk.StringVar(value=s.get("mode", "quality")),
            "quality": tk.DoubleVar(value=s.get("quality", 70)),
            "target": tk.StringVar(value=s.get("target", "9.5")),
            "fps": tk.StringVar(value=s.get("fps", "Source")),
            "audio": tk.StringVar(value=s.get("audio", "AAC")),
            "abr": tk.StringVar(value=s.get("abr", "192")),
        }

        body = ttk.Frame(self, padding=18)
        body.pack(fill="both")
        body.columnconfigure(1, weight=1)
        r = 0

        def head(text):
            nonlocal r
            ttk.Label(body, text=text, style="Head.TLabel").grid(
                row=r, column=0, columnspan=3, sticky="w", pady=(12 if r else 0, 4))
            r += 1

        def row(label, widget, extra=None):
            nonlocal r
            ttk.Label(body, text=label).grid(row=r, column=0, sticky="w", padx=(0, 14), pady=4)
            widget.grid(row=r, column=1, sticky="ew", pady=4)
            if extra:
                extra.grid(row=r, column=2, sticky="w", padx=(8, 0))
            r += 1

        def combo(var, values, width=30):
            return ttk.Combobox(body, textvariable=var, values=values, state="readonly", width=width)

        head("Preset")
        row("Preset", combo(self.v["preset"], list(self.presets()) + ["Custom"]))

        head("Video")
        row("Format", combo(self.v["container"], list(CONTAINERS)))
        self.codec_cb = combo(self.v["codec"], [])
        row("Codec", self.codec_cb)
        row("Encoder speed", combo(self.v["speed"], SPEEDS))
        modes = ttk.Frame(body)
        ttk.Radiobutton(modes, text="Constant quality", value="quality",
                        variable=self.v["mode"], takefocus=False).pack(side="left")
        ttk.Radiobutton(modes, text="Target file size", value="size",
                        variable=self.v["mode"], takefocus=False).pack(side="left", padx=(14, 0))
        row("Rate control", modes)
        self.q_scale = ttk.Scale(body, from_=0, to=100, variable=self.v["quality"], takefocus=False)
        self.q_lbl = ttk.Label(body, style="Muted.TLabel", width=16)
        row("Quality", self.q_scale, self.q_lbl)
        self.size_entry = ttk.Entry(body, textvariable=self.v["target"], width=10)
        row("Target size", self.size_entry, ttk.Label(body, text="MB", style="Muted.TLabel"))
        row("Frame rate", combo(self.v["fps"], FPS_OPTS))

        head("Audio")
        self.audio_cb = combo(self.v["audio"], [])
        row("Audio codec", self.audio_cb)
        self.abr_cb = combo(self.v["abr"], AUDIO_BR)
        row("Bitrate", self.abr_cb, ttk.Label(body, text="kbps", style="Muted.TLabel"))

        ttk.Separator(body).grid(row=r, column=0, columnspan=3, sticky="ew", pady=(14, 8))
        r += 1
        self.summary = ttk.Label(body, style="Muted.TLabel", justify="left")
        self.summary.grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1

        btns = ttk.Frame(body)
        btns.grid(row=r, column=0, columnspan=3, sticky="e", pady=(16, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy, takefocus=False).pack(side="left")
        ttk.Button(btns, text="Export", style="Accent.TButton", command=self.go,
                   takefocus=False).pack(side="left", padx=(8, 0))

        self.v["preset"].trace_add("write", lambda *a: self.apply_preset())
        self.v["container"].trace_add("write", lambda *a: self.refresh())
        for k in ("codec", "speed", "mode", "quality", "target", "fps", "audio", "abr"):
            self.v[k].trace_add("write", lambda *a: self.changed())
        self.refresh()

        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self.go())
        dark_titlebar(self)
        self.update_idletasks()
        x = app.root.winfo_rootx() + (app.root.winfo_width() - self.winfo_width()) // 2
        y = app.root.winfo_rooty() + (app.root.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()

    def label_for(self, enc):
        c = self.by_enc.get(enc)
        return c[0] if c else self.codecs[0][0]

    def presets(self):
        x265 = "libx265" if "libx265" in self.by_enc else "libx264"
        p = {
            "High quality": dict(container="mp4", codec="libx264", speed=2, mode="quality",
                                 quality=85, fps="Source", audio="AAC", abr="256"),
            "Balanced": dict(container="mp4", codec="libx264", speed=1, mode="quality",
                             quality=70, fps="Source", audio="AAC", abr="192"),
            "Small file": dict(container="mp4", codec=x265, speed=1, mode="quality",
                               quality=50, fps="Source", audio="AAC", abr="128"),
            "Discord (under 10 MB)": dict(container="mp4", codec="libx264", speed=1, mode="size",
                                          target="9.5", fps="30", audio="AAC", abr="96"),
        }
        gpu = next((c for fam in ("amf", "nvenc", "qsv") for c in self.codecs
                    if c[2] == fam and c[3] == "h264"), None)
        if gpu:
            p["Fast export (GPU)"] = dict(container="mp4", codec=gpu[1], speed=1, mode="quality",
                                          quality=72, fps="Source", audio="AAC", abr="192")
        return p

    def apply_preset(self):
        p = self.presets().get(self.v["preset"].get())
        if not p:
            return
        self.applying = True
        for k, val in p.items():
            if k == "codec":
                val = self.label_for(val)
            elif k == "speed":
                val = SPEEDS[val]
            self.v[k].set(val)
        self.applying = False
        self.refresh()

    def changed(self):
        if not self.applying and self.v["preset"].get() != "Custom":
            self.applying = True
            self.v["preset"].set("Custom")
            self.applying = False
        self.update_labels()

    def refresh(self):
        cont = self.v["container"].get()
        codecs = [c[0] for c in self.codecs if c[3] in CONTAINERS[cont]]
        self.codec_cb.config(values=codecs)
        auds = [k for k, v in AUDIO.items() if v in AUDIO_OK[cont]
                and not (v == "opus" and not self.app.opus_enc)
                and not (v != "none" and not self.app.any_audio())]
        self.audio_cb.config(values=auds)
        was = self.applying
        self.applying = True
        if codecs and self.v["codec"].get() not in codecs:
            self.v["codec"].set(codecs[0])
        if self.v["audio"].get() not in auds:
            self.v["audio"].set(auds[0])
        self.applying = was
        self.update_labels()

    def update_labels(self):
        codec = self.by_label.get(self.v["codec"].get(), self.codecs[0])
        size_mode = self.v["mode"].get() == "size"
        self.q_scale.state(["disabled"] if size_mode else ["!disabled"])
        self.size_entry.state(["!disabled"] if size_mode else ["disabled"])
        q = self.v["quality"].get()
        self.q_lbl.config(text=f"{q:.0f}%   {QNAME[codec[2]]} {quality_value(codec, q)}")
        aud = AUDIO.get(self.v["audio"].get(), "none")
        self.abr_cb.state(["disabled"] if aud == "none" else ["!disabled", "readonly"])

        ow, oh = self.app.output_dims()
        length = self.app.render_len()
        lines = f"Output  {ow} x {oh}   ·   length {fmt_time(length)}   ·   .{self.v['container'].get()}"
        if size_mode:
            kb = self.video_kbps()
            lines += (f"\nVideo bitrate about {kb} kbps" if kb and kb >= 100
                      else "\nTarget size is too small for this length")
        if codec[2] in HW_FAMILIES:
            lines += "\nGPU encoding: much faster, slightly bigger files at the same quality"
        self.summary.config(text=lines)

    def video_kbps(self):
        try:
            mb = float(self.v["target"].get().replace(",", "."))
        except ValueError:
            return None
        length = max(self.app.render_len(), 0.1)
        aud = AUDIO.get(self.v["audio"].get(), "none")
        a_kbps = 0 if aud == "none" else int(self.v["abr"].get())
        return int(mb * 8192 / length * 0.94 - a_kbps)

    def go(self):
        size_mode = self.v["mode"].get() == "size"
        kbps = self.video_kbps() if size_mode else 0
        if size_mode and (not kbps or kbps < 100):
            messagebox.showerror("Export", "That target size is too small for this clip length.",
                                 parent=self)
            return
        codec = self.by_label.get(self.v["codec"].get(), self.codecs[0])
        settings = {
            "preset": self.v["preset"].get(), "container": self.v["container"].get(),
            "codec": codec[1], "speed": SPEEDS.index(self.v["speed"].get()),
            "mode": self.v["mode"].get(), "quality": round(self.v["quality"].get()),
            "target": self.v["target"].get(), "fps": self.v["fps"].get(),
            "audio": self.v["audio"].get(), "abr": self.v["abr"].get(),
        }
        ext = settings["container"]
        first = Path(min(self.app.clips, key=lambda c: c["start"])["path"])
        out = filedialog.asksaveasfilename(
            parent=self, defaultextension=f".{ext}",
            initialdir=self.app.last_dir or str(first.parent),
            initialfile=f"{first.stem}_edited.{ext}",
            filetypes=[(ext.upper(), f"*.{ext}")])
        if not out:
            return
        self.app.exp_settings = settings
        self.app.settings["export"] = settings
        self.app.settings.pop("tiktok_after", None)
        save_settings(self.app.settings)
        self.destroy()
        self.app.start_export(out, settings, codec, kbps)


# ---------------------------------------------------------------- projects and templates
def center_on(win, parent):
    win.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_width()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_height()) // 3
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")


def list_templates():
    """[(folder, data)] of saved templates, by name."""
    out = []
    if TEMPLATES_DIR.is_dir():
        for d in TEMPLATES_DIR.iterdir():
            f = d / ("template" + PROJECT_EXT)
            try:
                out.append((d, json.loads(f.read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                continue
    return sorted(out, key=lambda x: x[1].get("template", {}).get("name", x[0].name).lower())


class TemplateSaveDialog(tk.Toplevel):
    """Save the current project as a template: a name, which clips to keep, and whether the
    kept clips stretch to the length of the video added later."""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Save as template")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app.root)
        body = ttk.Frame(self, padding=18)
        body.pack(fill="both")
        ttk.Label(body, text="Template name", style="Head.TLabel").pack(anchor="w")
        self.name = tk.StringVar(value="My template")
        en = ttk.Entry(body, textvariable=self.name, width=40)
        en.pack(fill="x", pady=(4, 12))
        en.select_range(0, "end")
        en.focus_set()

        ttk.Label(body, text="Keep these clips", style="Head.TLabel").pack(anchor="w")
        ttk.Label(body, text="Leave out the clips you will replace each time, like your main video.",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 4))
        clips = sorted(app.clips, key=lambda c: (c["kind"] != "v", -c["track"] if c["kind"] == "v"
                                                 else c["track"], c["start"]))
        # by default keep what isn't on V1 / A1 (logos, overlays, music), or everything if that's all
        upper = [c for c in clips if c["track"] > 0]
        self.keep = {}
        box = ttk.Frame(body)
        box.pack(fill="x")
        for c in clips:
            var = tk.BooleanVar(value=(c in upper) if upper else True)
            self.keep[c["id"]] = var
            ttk.Checkbutton(box, variable=var, takefocus=False,
                            text=f"{c['kind'].upper()}{c['track'] + 1}   {Path(c['path']).name}   "
                                 f"{fmt_time(c['start'])} – {fmt_time(c['start'] + c['dur'])}"
                            ).pack(anchor="w")
        self.stretch = tk.BooleanVar(value=True)
        ttk.Checkbutton(body, text="Stretch kept clips to the length of the video you add",
                        variable=self.stretch, takefocus=False).pack(anchor="w", pady=(12, 0))
        ttk.Label(body, text=f"Frame size {app.vw} x {app.vh}, crop, output size and export "
                             f"settings are saved too. Media files are copied into the template.",
                  style="Muted.TLabel", wraplength=420, justify="left").pack(anchor="w", pady=(8, 0))
        btns = ttk.Frame(body)
        btns.pack(anchor="e", pady=(16, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy, takefocus=False).pack(side="left")
        ttk.Button(btns, text="Save template", style="Accent.TButton", command=self.save,
                   takefocus=False).pack(side="left", padx=(8, 0))
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self.save())
        dark_titlebar(self)
        center_on(self, app.root)
        self.grab_set()

    def save(self):
        name = self.name.get().strip()
        folder = re.sub(r'[<>:"/\\|?*]', "", name).strip(" .")
        if not folder:
            messagebox.showerror("Save as template", "Give the template a name.", parent=self)
            return
        kept = [c for c in self.app.clips if self.keep[c["id"]].get()]
        dest = TEMPLATES_DIR / folder
        if dest.exists() and not messagebox.askyesno(
                "Save as template", f"A template called {name} already exists. Replace it?",
                parent=self):
            return
        if self.app.save_template(dest, name, kept, self.stretch.get()):
            self.destroy()


class ProjectPicker(tk.Toplevel):
    """The project window (also shown at startup): recent projects, Open, and a new project
    that is blank or made from one of the saved templates."""
    TW, TH = 192, 108

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Projects")
        self.configure(bg=BG)
        self.transient(app.root)
        self.geometry("830x640")
        self.minsize(520, 380)
        self.choice = None          # None = blank, else template folder
        self.tiles, self.photos, self.frames = {}, [], {}
        body = ttk.Frame(self, padding=(18, 16, 18, 14))
        body.pack(fill="both", expand=True)
        recent = [p for p in app.settings.get("recent", []) if os.path.exists(p)][:5]
        if recent:
            ttk.Label(body, text="Recent projects", style="Head.TLabel").pack(anchor="w")
            for path in recent:
                row = tk.Label(body, text=f"{Path(path).stem}", bg=BG, fg=FG, cursor="hand2",
                               font=("Segoe UI Semibold", 10), anchor="w")
                row.pack(anchor="w", fill="x")
                folder = str(Path(path).parent)
                if len(folder) > 80:   # keep the start (drive) and the end (the project's folder)
                    folder = folder[:30] + " … " + folder[-46:]
                where = tk.Label(body, text=folder, bg=BG, fg=MUTED,
                                 font=SMALL_FONT, anchor="w", cursor="hand2")
                where.pack(anchor="w", fill="x", pady=(0, 4))
                for w in (row, where):
                    w.bind("<Button-1>", lambda e, p=path: self.open(p))
                    w.bind("<Enter>", lambda e, r=row: r.config(fg=ACCENT))
                    w.bind("<Leave>", lambda e, r=row: r.config(fg=FG))
            ttk.Separator(body).pack(fill="x", pady=(6, 12))
        ttk.Label(body, text="Start a new project", style="Head.TLabel").pack(anchor="w")
        ttk.Label(body, text="Pick a template, or start blank. Save any project as a template "
                             "with File > Save as template.", style="Muted.TLabel",
                  wraplength=640, justify="left").pack(anchor="w", pady=(0, 10))
        btns = ttk.Frame(body)
        btns.pack(side="bottom", fill="x", pady=(12, 0))
        res = ttk.Frame(body)
        res.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Label(res, text="Resolution").pack(side="left", padx=(0, 8))
        last = app.settings.get("frame") or (1920, 1080)
        self.res = tk.StringVar(value=next((r for r in RESOLUTIONS if size_of(r) == tuple(last)),
                                           f"{last[0]}x{last[1]}"))
        ttk.Combobox(res, textvariable=self.res, values=RESOLUTIONS, width=44).pack(side="left")
        ttk.Label(res, text="pick one or type any size, like 1440x1800", style="Muted.TLabel"
                  ).pack(side="left", padx=(10, 0))
        self.del_btn = ttk.Button(btns, text="Delete template", command=self.delete,
                                  takefocus=False)
        self.del_btn.pack(side="left")
        ttk.Button(btns, text="Open project…", command=lambda: self.open(None),
                   takefocus=False).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Create", style="Accent.TButton", command=self.create,
                   takefocus=False).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy, takefocus=False).pack(side="right",
                                                                                   padx=(0, 8))
        wrap = ttk.Frame(body)
        wrap.pack(fill="both", expand=True)
        self.cv = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.cv.yview)
        self.cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.cv.pack(side="left", fill="both", expand=True)
        self.grid = ttk.Frame(self.cv)
        self.cv.create_window(0, 0, window=self.grid, anchor="nw")
        self.cv.bind("<Configure>", lambda e: self.layout())
        self.cv.bind("<MouseWheel>", lambda e: self.cv.yview_scroll(-e.delta // 120, "units"))
        self.fill()
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self.create())
        dark_titlebar(self)
        center_on(self, app.root)
        self.grab_set()

    def fill(self):
        for w in self.grid.winfo_children():
            w.destroy()
        self.tiles, self.photos, self.frames = {}, [], {}
        self.tile(None, "Blank project", "Empty timeline", None)
        for folder, d in list_templates():
            t = d.get("template", {})
            w, h = d.get("frame") or (0, 0)
            if w:
                self.frames[folder] = (int(w), int(h))
            n = len(d.get("clips", []))
            info = (f"{w} x {h}   ·   " if w else "") + f"{n} clip{'' if n == 1 else 's'}"
            self.tile(folder, t.get("name", folder.name), info, folder / "preview.png")
        self.select(self.choice if self.choice in self.tiles else None)
        self.layout()

    def tile(self, key, title, info, thumb):
        f = tk.Frame(self.grid, bg=PANEL, highlightthickness=2, highlightbackground=PANEL,
                     cursor="hand2", padx=8, pady=8)
        img = Image.new("RGB", (self.TW, self.TH), "#0e0e11")
        if thumb is not None:
            try:
                with Image.open(thumb) as im:
                    im = im.convert("RGB")
                    im.thumbnail((self.TW, self.TH))
                    img.paste(im, ((self.TW - im.width) // 2, (self.TH - im.height) // 2))
            except OSError:
                pass
        ph = ImageTk.PhotoImage(img)
        self.photos.append(ph)
        pic = tk.Label(f, image=ph, bg=PANEL, bd=0)
        if key is None:
            pic.config(text="+", compound="center", fg=MUTED, font=("Segoe UI", 28))
        pic.pack()
        t = tk.Label(f, text=title, bg=PANEL, fg=FG, font=("Segoe UI Semibold", 10), anchor="w",
                     width=24)
        t.pack(fill="x", pady=(6, 0))
        i = tk.Label(f, text=info, bg=PANEL, fg=MUTED, font=SMALL_FONT, anchor="w")
        i.pack(fill="x")
        for w in (f, pic, t, i):
            w.bind("<Button-1>", lambda e, k=key: self.select(k))
            w.bind("<Double-Button-1>", lambda e, k=key: (self.select(k), self.create()))
            w.bind("<MouseWheel>", lambda e: self.cv.yview_scroll(-e.delta // 120, "units"))
        self.tiles[key] = f

    def layout(self):
        tw = max((f.winfo_reqwidth() for f in self.tiles.values()), default=self.TW) + 12
        cols = max(1, (self.cv.winfo_width() + 12) // tw)
        for n, f in enumerate(self.tiles.values()):
            f.grid(row=n // cols, column=n % cols, padx=(0, 12), pady=(0, 12), sticky="nw")
        self.grid.update_idletasks()
        self.cv.configure(scrollregion=(0, 0, 0, self.grid.winfo_reqheight()))

    def select(self, key):
        if key != self.choice and key is not None:   # a template suggests its own size
            frame = self.frames.get(key)
            if frame:
                self.res.set(next((r for r in RESOLUTIONS if size_of(r) == frame),
                                  f"{frame[0]}x{frame[1]}"))
        self.choice = key
        for k, f in self.tiles.items():
            f.config(highlightbackground=ACCENT if k == key else PANEL)
        self.del_btn.state(["disabled"] if key is None else ["!disabled"])

    def delete(self):
        if self.choice is None:
            return
        if messagebox.askyesno("Delete template", f"Delete the template {self.choice.name}?\n"
                                                  "Projects made from it are not affected.",
                               parent=self):
            shutil.rmtree(self.choice, ignore_errors=True)
            self.choice = None
            self.fill()

    def create(self):
        size = size_of(self.res.get())
        if not size:
            messagebox.showerror("Resolution", "Type a size like 1920x1080 (16 to 8192 px).",
                                 parent=self)
            return
        choice = self.choice
        self.destroy()
        self.app.settings["frame"] = list(size)
        save_settings(self.app.settings)
        self.app.start_project(choice, size)

    def open(self, path):
        """A recent project, or (None) pick one / a video with the Open dialog."""
        self.destroy()
        if path:
            self.app.open_project(path)
        else:
            self.app.open_any()


class CaptionDialog(tk.Toplevel):
    """Auto captions: language, model (Fast / Accurate) and caption length."""

    def __init__(self, app, clips):
        super().__init__(app.root)
        self.app, self.clips = app, clips
        self.title("Auto captions")
        self.configure(bg=BG)
        self.transient(app.root)
        self.resizable(False, False)
        prev = app.settings.get("captions", {})
        body = ttk.Frame(self, padding=(18, 16, 18, 14))
        body.pack(fill="both", expand=True)
        names = sorted({Path(c["path"]).name for c in clips})
        ttk.Label(body, text="Auto captions", style="Head.TLabel").grid(row=0, column=0,
                                                                       columnspan=2, sticky="w")
        ttk.Label(body, text=", ".join(names)[:70], style="Muted.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self.lang = tk.StringVar(value=prev.get("lang", "English"))
        self.model = tk.StringVar(value=prev.get("model", "Accurate"))
        self.length = tk.StringVar(value=prev.get("length", next(iter(CAPTION_LENGTHS))))
        ttk.Label(body, text="Language").grid(row=2, column=0, sticky="w", padx=(0, 14), pady=4)
        ttk.Combobox(body, textvariable=self.lang, values=list(CAPTION_LANGS), state="readonly",
                     width=34).grid(row=2, column=1, sticky="w")
        ttk.Label(body, text="Caption length").grid(row=3, column=0, sticky="w", padx=(0, 14), pady=4)
        ttk.Combobox(body, textvariable=self.length, values=list(CAPTION_LENGTHS),
                     state="readonly", width=34).grid(row=3, column=1, sticky="w")
        ttk.Label(body, text="Speech model").grid(row=4, column=0, sticky="nw", padx=(0, 14), pady=4)
        models = ttk.Frame(body)
        models.grid(row=4, column=1, sticky="w", pady=4)
        for name, desc in (("Accurate", "best for Hungarian, slower"), ("Fast", "quick, best for clear English")):
            file, mb = WHISPER_MODELS[name]
            have = (MODELS_DIR / file).exists()
            ttk.Radiobutton(models, text=f"{name}: {desc}", value=name, variable=self.model,
                            takefocus=False).pack(anchor="w")
            ttk.Label(models, text=("downloaded" if have else f"downloads {mb} MB once"),
                      style="Muted.TLabel").pack(anchor="w", padx=(22, 0), pady=(0, 4))
        ttk.Label(body, text="Captions go on a new video track at the top. Click one to fix "
                             "its text, move or size it in the inspector.", style="Muted.TLabel",
                  wraplength=420, justify="left").grid(row=5, column=0, columnspan=2, sticky="w",
                                                       pady=(8, 0))
        btns = ttk.Frame(body)
        btns.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        ttk.Button(btns, text="Create captions", style="Accent.TButton", command=self.ok,
                   takefocus=False).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy, takefocus=False).pack(side="right",
                                                                                   padx=(0, 8))
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.destroy())
        dark_titlebar(self)
        center_on(self, app.root)
        self.grab_set()

    def ok(self):
        choice = {"lang": self.lang.get(), "model": self.model.get(), "length": self.length.get()}
        self.app.settings["captions"] = choice
        save_settings(self.app.settings)
        self.destroy()
        self.app.start_captions(self.clips, CAPTION_LANGS[choice["lang"]],
                                WHISPER_MODELS[choice["model"]][0], CAPTION_LENGTHS[choice["length"]])


def ask_resolution(parent, initial):
    """Small dialog: a resolution from RESOLUTIONS or typed. (w, h) or None."""
    win = tk.Toplevel(parent)
    win.title("Project resolution")
    win.configure(bg=BG)
    win.transient(parent)
    win.resizable(False, False)
    body = ttk.Frame(win, padding=(18, 16, 18, 14))
    body.pack(fill="both", expand=True)
    ttk.Label(body, text="Every clip is fit into this frame; positions scale along.",
              style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
    var = tk.StringVar(value=next((r for r in RESOLUTIONS if size_of(r) == size_of(initial)),
                                  initial))
    box = ttk.Combobox(body, textvariable=var, values=RESOLUTIONS, width=44)
    box.pack(fill="x")
    out = []

    def ok():
        size = size_of(var.get())
        if not size:
            messagebox.showerror("Resolution", "Type a size like 1920x1080 (16 to 8192 px).",
                                 parent=win)
            return
        out.append(size)
        win.destroy()
    btns = ttk.Frame(body)
    btns.pack(fill="x", pady=(14, 0))
    ttk.Button(btns, text="OK", style="Accent.TButton", command=ok).pack(side="right")
    ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 8))
    win.bind("<Return>", lambda e: ok())
    win.bind("<Escape>", lambda e: win.destroy())
    dark_titlebar(win)
    center_on(win, parent)
    box.focus_set()
    win.grab_set()
    parent.wait_window(win)
    return out[0] if out else None


# ---------------------------------------------------------------- inspector control
class SlideField:
    """DaVinci-style number control on one grid row: label, optional slider, value box, reset.
    Drag the value box sideways to change it (Shift = fine, Ctrl = coarse), click it to type,
    use the wheel to nudge, double-click the slider or click the arrow to reset."""

    def __init__(self, parent, row, label, lo, hi, step, fmt, default, on_change, on_end,
                 slider=True):
        self.lo, self.hi, self.step, self.fmt, self.default = lo, hi, step, fmt, default
        self.on_change, self.on_end = on_change, on_end
        self.value, self.editor = default, None
        ttk.Label(parent, text=label, style="PanelMuted.TLabel").grid(row=row, column=0,
                                                                      sticky="w", padx=(0, 8))
        self.cv = None
        if slider:
            self.cv = tk.Canvas(parent, height=22, bg=PANEL, highlightthickness=0, cursor="hand2")
            self.cv.grid(row=row, column=1, sticky="ew", padx=(0, 8))
            self.cv.bind("<Configure>", lambda e: self.draw())
            self.cv.bind("<ButtonPress-1>", self.slide)
            self.cv.bind("<B1-Motion>", self.slide)
            self.cv.bind("<ButtonRelease-1>", lambda e: self.on_end())
            self.cv.bind("<Double-Button-1>", lambda e: self.set(self.default, end=True))
            self.cv.bind("<MouseWheel>", self.wheel)
        self.box = tk.Label(parent, bg=FIELD, fg=FG, font=("Consolas", 10), width=9, anchor="e",
                            padx=6, pady=2, cursor="sb_h_double_arrow", highlightthickness=1,
                            highlightbackground=BORDER)
        self.box.grid(row=row, column=2, pady=2, sticky="e")
        self.box.bind("<ButtonPress-1>", self.press)
        self.box.bind("<B1-Motion>", self.scrub)
        self.box.bind("<ButtonRelease-1>", self.release)
        self.box.bind("<MouseWheel>", self.wheel)
        self.box.bind("<Enter>", lambda e: self.box.config(highlightbackground=ACCENT))
        self.box.bind("<Leave>", lambda e: self.box.config(highlightbackground=BORDER))
        reset = tk.Label(parent, text=ICON["undo"], bg=PANEL, fg=MUTED, font=(ICON_FONT, 9),
                         cursor="hand2", padx=4)
        reset.grid(row=row, column=3, padx=(4, 0))
        reset.bind("<Button-1>", lambda e: self.set(self.default, end=True))
        reset.bind("<Enter>", lambda e: reset.config(fg=FG))
        reset.bind("<Leave>", lambda e: reset.config(fg=MUTED))
        Tip(reset, f"Reset {label.lower()}")
        self.show(default)

    def show(self, v):
        """Displays a value from the model (no callbacks)."""
        self.value = v
        self.box.config(text=self.fmt.format(v))
        self.draw()

    def set(self, v, end=False):
        v = min(max(v, self.lo), self.hi)
        if v != self.value:
            self.show(v)
            self.on_change(v)
        if end:
            self.on_end()

    def draw(self):
        if not self.cv:
            return
        c = self.cv
        c.delete("all")
        w, mid = c.winfo_width(), 11
        if w < 24:
            return
        x0, x1 = 8, w - 8

        def x_of(v):
            return x0 + (x1 - x0) * (min(max(v, self.lo), self.hi) - self.lo) / (self.hi - self.lo)
        c.create_line(x0, mid, x1, mid, fill=BORDER, width=4, capstyle="round")
        x, xd = x_of(self.value), x_of(self.default)
        if abs(x - xd) >= 1:   # filled from the default, so a changed value stands out
            c.create_line(xd, mid, x, mid, fill=ACCENT, width=4)
        c.create_line(xd, mid - 5, xd, mid + 5, fill=MUTED)
        c.create_oval(x - 7, mid - 7, x + 7, mid + 7, fill="white", outline=ACCENT, width=2)

    def slide(self, e):
        w = self.cv.winfo_width()
        v = self.lo + (self.hi - self.lo) * (e.x - 8) / max(w - 16, 1)
        if abs(v - self.default) <= (self.hi - self.lo) * 0.012:   # sticks to the default
            v = self.default
        self.set(round(v / self.step) * self.step)

    def press(self, e):
        self.px, self.pv, self.moved = e.x_root, self.value, False

    def scrub(self, e):
        dx = e.x_root - self.px
        if not self.moved and abs(dx) < 3:
            return
        self.moved = True
        mul = 0.1 if e.state & 0x1 else (10 if e.state & 0x4 else 1)
        self.set(self.pv + dx * self.step * mul)

    def release(self, e):
        if self.moved:
            self.on_end()
        else:
            self.edit()

    def wheel(self, e):
        mul = 0.1 if e.state & 0x1 else (10 if e.state & 0x4 else 1)
        self.set(self.value + (1 if e.delta > 0 else -1) * self.step * 4 * mul, end=True)
        return "break"

    def edit(self):
        """Type a value: Enter applies, Esc cancels."""
        if self.editor:
            return
        en = self.editor = tk.Entry(self.box.master, bg=FIELD, fg=FG, insertbackground=FG,
                                    relief="flat", font=("Consolas", 10), justify="right",
                                    highlightthickness=1, highlightcolor=ACCENT,
                                    highlightbackground=ACCENT)
        en.insert(0, re.sub(r"[^0-9.+-]", "", self.fmt.format(self.value)))
        en.select_range(0, "end")
        en.place(in_=self.box, x=0, y=0, relwidth=1, relheight=1)
        en.focus_set()

        def done(apply):
            if self.editor is None:
                return
            text, self.editor = en.get(), None
            en.destroy()
            if apply:
                try:
                    self.set(float(re.sub(r"[^0-9.+-]", "", text.replace(",", "."))), end=True)
                except ValueError:
                    pass
        en.bind("<Return>", lambda e: done(True))
        en.bind("<KP_Enter>", lambda e: done(True))
        en.bind("<Escape>", lambda e: done(False))
        en.bind("<FocusOut>", lambda e: done(True))


# ---------------------------------------------------------------- main window
class Tip:
    """Small hover tooltip."""

    def __init__(self, widget, text):
        self.w, self.text, self.win, self.job = widget, text, None, None
        widget.bind("<Enter>", lambda e: self.arm(), add="+")
        widget.bind("<Leave>", lambda e: self.hide(), add="+")
        widget.bind("<ButtonPress>", lambda e: self.hide(), add="+")

    def arm(self):
        self.hide()
        self.job = self.w.after(450, self.show)

    def show(self):
        self.job = None
        self.win = tk.Toplevel(self.w)
        self.win.wm_overrideredirect(True)
        self.win.attributes("-topmost", True)
        tk.Label(self.win, text=self.text, bg="#0b0b0e", fg=FG, font=SMALL_FONT, padx=7, pady=3,
                 highlightthickness=1, highlightbackground=BORDER).pack()
        self.win.geometry(f"+{self.w.winfo_rootx()}+{self.w.winfo_rooty() + self.w.winfo_height() + 4}")

    def hide(self):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None
        if self.win:
            self.win.destroy()
            self.win = None


def tc(t, step):
    m, s = divmod(t + 1e-6, 60)
    return f"{int(m)}:{s:04.1f}" if step < 1 else f"{int(m)}:{int(s):02d}"


class App:
    def __init__(self, root):
        self.root = root
        root.title("q-edit")
        if ICON_FILE.exists():
            try:
                root.iconbitmap(default=str(ICON_FILE))
            except tk.TclError:
                pass
        root.geometry("1180x880")
        root.minsize(820, 600)
        apply_theme(root)

        self.q = queue.Queue()
        # timeline: clips sit on tracks. Higher video tracks cover lower ones, audio tracks mix.
        # clip = {"id", "kind" ("v"/"a"), "track", "path", "start", "inp", "dur", "link"}
        self.media = {}
        self.vtracks = [{"hidden": False}]
        self.atracks = [{"muted": False}]
        self.clips = []
        self.sel = set()
        self.next_id = 1
        self.undo_stack, self.redo_stack = [], []
        self.vw = self.vh = 0
        self.pfps = 30.0
        self.cur = 0.0
        self.mark_in = self.mark_out = None
        self.pps, self.view_t, self.yoff = 40.0, 0.0, 0.0
        self.tdrag = self.hover = None
        self.drop = None   # files being dragged in from Explorer: {"key", "paths", "target"}
        self.tl_job = None   # pending draw_timeline_soon()
        self.clipboard = []   # copied clips, start relative to the earliest one
        self.thumbs, self.peaks, self.tiles = {}, {}, {}   # timeline filmstrips and waveforms
        self.crop = [0.0, 0.0, 0.0, 0.0]
        self.frame = self.frame_photo = self.photo_key = None
        self.req = 0
        self.after_id = self.tick_id = None
        # live inspector preview: source frames decoded once, then edited in memory
        self.src_cache, self.src_pending = {}, set()
        self.live_job = self.live_wanted = self.replay_job = self.vol_job = None
        self.drag = None
        self.s, self.ox, self.oy, self.dw, self.dh = 1.0, 0, 0, 1, 1
        self.player = None
        self.resume = False
        self.export_proc = None
        self.export_out = None
        self.cancelled = False
        self.codecs, self.opus_enc = [c for c in CODECS if c[1] == "libx264"], "libopus"
        self.settings = load_settings()
        self.exp_settings = self.settings.get("export", {})
        self.last_dir = None
        self.project_path = None     # the .qedit file, None while untitled
        self.saved_key = None        # project state at the last save / open (for "unsaved")
        self.title_tick = 0

        self.build_ui()
        self.saved_key = self.state_key()
        self.update_title()
        dark_titlebar(root)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(50, self.poll)
        threading.Thread(target=lambda: self.q.put(("encoders", *detect_encoders())),
                         daemon=True).start()

    # ---------- UI ----------
    def btn(self, parent, text, cmd, style="TButton", tip=None, **kw):
        b = ttk.Button(parent, text=text, command=cmd, style=style, takefocus=False, **kw)
        if tip:
            Tip(b, tip)
        return b

    def tool(self, parent, icon, cmd, tip, pad=0):
        b = self.btn(parent, ICON[icon], cmd, style="Tool.TButton", tip=tip)
        b.pack(side="left", padx=(pad, 0))
        return b

    def toggle(self, parent, var, text, tip, icons=None, command=None):
        """Flat button bound to a BooleanVar; highlighted while on. icons = (on, off) glyphs."""
        b = self.btn(parent, "", None, tip=tip)

        def show(*_):
            on = var.get()
            if icons:
                b.config(text=ICON[icons[0] if on else icons[1]],
                         style="ToolOn.TButton" if on else "Tool.TButton")
            else:
                b.config(text=text, style="ToolTextOn.TButton" if on else "ToolText.TButton")

        def flip():
            var.set(not var.get())
            if command:
                command()
        b.config(command=flip)
        var.trace_add("write", show)
        show()
        return b

    def sep(self, parent):
        ttk.Separator(parent, orient="vertical").pack(side="left", fill="y", padx=5, pady=3)

    def build_menu(self):
        bar = ttk.Frame(self.root, style="Bar.TFrame", padding=(4, 3, 10, 3))
        bar.pack(fill="x")

        def menu(label, items):
            mb = tk.Menubutton(bar, text=label, bg=BG, fg=FG, activebackground=HOVER,
                               activeforeground=FG, relief="flat", bd=0, padx=10, pady=3,
                               font=UI_FONT, highlightthickness=0)
            m = tk.Menu(mb, tearoff=0, bg=FIELD, fg=FG, activebackground=ACCENT,
                        activeforeground="white", bd=0, relief="flat", font=UI_FONT,
                        selectcolor=FG)
            for it in items:
                if it is None:
                    m.add_separator()
                elif it[0] == "check":
                    m.add_checkbutton(label=it[1], accelerator=it[2], variable=it[3],
                                      command=it[4] if len(it) > 4 else None)
                else:
                    m.add_command(label=it[0], accelerator=it[1], command=it[2])
            mb["menu"] = m
            mb.pack(side="left")
            return m

        file_menu = menu("File", [("New project…", "Ctrl+N", self.new_project),
                                  ("Open…", "Ctrl+O", self.open_any), None,
                                  ("Save", "Ctrl+S", self.save_project),
                                  ("Save as…", "Ctrl+Shift+S", lambda: self.save_project(True)),
                                  ("Save as template…", "", self.save_as_template), None,
                                  ("Import media…", "Ctrl+I", self.add_media), None,
                                  ("Export…", "Ctrl+E", self.export), None,
                                  ("Exit", "Alt+F4", self.on_close)])
        self.recent_menu = tk.Menu(file_menu, tearoff=0, bg=FIELD, fg=FG, activebackground=ACCENT,
                                   activeforeground="white", bd=0, relief="flat", font=UI_FONT,
                                   postcommand=self.fill_recent)
        file_menu.insert_cascade(2, label="Open recent", menu=self.recent_menu)
        menu("Edit", [("Undo", "Ctrl+Z", self.undo), ("Redo", "Ctrl+Y", self.redo), None,
                      ("Cut", "Ctrl+X", lambda: self.copy_sel(True)),
                      ("Copy", "Ctrl+C", self.copy_sel),
                      ("Paste at playhead", "Ctrl+V", self.paste), None,
                      ("Auto captions…", "", self.auto_captions), None,
                      ("Split at playhead", "Ctrl+B", self.split),
                      ("Delete", "Del", self.delete_sel),
                      ("Ripple delete", "Shift+Del", lambda: self.delete_sel(True)),
                      ("Link / unlink clips", "", self.toggle_link), None,
                      ("Select all", "Ctrl+A", lambda: self.select(c["id"] for c in self.clips)),
                      ("Deselect", "Esc", lambda: self.select(set())), None,
                      ("Mark in", "I", self.set_start), ("Mark out", "O", self.set_end),
                      ("Clear marks", "", self.clear_marks)])
        menu("Timeline", [("Add video track", "", lambda: self.add_track("v")),
                          ("Add audio track", "", lambda: self.add_track("a")), None,
                          ("Zoom in", "+", lambda: self.zoom(1.5)),
                          ("Zoom out", "−", lambda: self.zoom(1 / 1.5)),
                          ("Zoom to fit", "Shift+Z", self.zoom_fit), None,
                          ("Project resolution…", "", self.change_resolution), None,
                          ("check", "Snapping", "N", self.snap),
                          ("check", "Loop playback", "", self.loop),
                          ("check", "Mute preview", "", self.mute, self.restart_if_playing)])
        menu("Help", [("Keyboard shortcuts", "", self.show_shortcuts)])

        self.export_btn = self.btn(bar, "Export", self.export, style="Small.Accent.TButton",
                                   tip="Export video  (Ctrl+E)")
        self.export_btn.pack(side="right")
        self.outsize = tk.StringVar()
        self.outsize.trace_add("write", lambda *a: self.on_outsize())
        out_entry = ttk.Entry(bar, textvariable=self.outsize, width=10, style="Small.TEntry")
        out_entry.pack(side="right", padx=(4, 14))
        out_entry.bind("<Return>", lambda e: self.root.focus_set())
        Tip(out_entry, "Output size, e.g. 1080x1920. Blank = crop size")
        ttk.Label(bar, text="Size", style="Muted.TLabel").pack(side="right")
        self.ratio = tk.StringVar(value="Free")
        cb = ttk.Combobox(bar, textvariable=self.ratio, values=list(RATIOS), width=5,
                          state="readonly", takefocus=False, style="Small.TCombobox")
        cb.pack(side="right", padx=(4, 12))
        cb.bind("<<ComboboxSelected>>", lambda e: (self.apply_ratio(), self.root.focus_set()))
        ttk.Label(bar, text="Crop", style="Muted.TLabel").pack(side="right")

    def show_shortcuts(self):
        messagebox.showinfo("Keyboard shortcuts", __doc__.split("Shortcuts:")[1].strip("\n"),
                            parent=self.root)

    def build_ui(self):
        self.snap = tk.BooleanVar(value=True)
        self.loop = tk.BooleanVar(value=True)
        self.mute = tk.BooleanVar(value=False)
        self.build_menu()

        # status bar at the very bottom: export progress
        st = ttk.Frame(self.root, style="Panel.TFrame", padding=(10, 4))
        st.pack(side="bottom", fill="x", padx=6, pady=(4, 6))
        self.prog = ttk.Progressbar(st, length=160, maximum=100)
        self.prog.pack(side="left")
        self.cap_btn = self.btn(st, "Cancel captions", self.cancel_captions,
                                style="ToolText.TButton")   # shown while captions are made
        self.status = ttk.Label(st, text="Detecting encoders...", style="PanelMuted.TLabel")
        self.status.pack(side="left", padx=10)
        self.cap_proc = None   # None = idle, False = starting, a Popen while whisper runs
        self.cap_cancel = False
        self.cap_gpu = None    # False once the GPU failed this session (then only the CPU)
        # preview above, timeline below; drag the bar between them to resize
        self.paned = tk.PanedWindow(self.root, orient="vertical", bg=BG, bd=0, sashwidth=6,
                                    sashrelief="flat", sashcursor="sb_v_double_arrow",
                                    opaqueresize=True)
        self.paned.pack(fill="both", expand=True, padx=6)
        # top: preview with the inspector on its right (drag the bar to resize)
        self.hpane = tk.PanedWindow(self.paned, orient="horizontal", bg=BG, bd=0, sashwidth=6,
                                    sashrelief="flat", sashcursor="sb_h_double_arrow",
                                    opaqueresize=True)
        self.paned.add(self.hpane, minsize=120, stretch="always")
        self.cv = tk.Canvas(self.hpane, bg=CANVAS_BG, highlightthickness=0)
        self.hpane.add(self.cv, minsize=200, stretch="always")
        self.build_inspector()
        self.cv.bind("<Configure>", lambda e: (self.redraw(), self.schedule_frame(200)))
        self.cv.bind("<ButtonPress-1>", self.on_press)
        self.cv.bind("<B1-Motion>", self.on_drag)
        self.cv.bind("<ButtonRelease-1>", self.on_release)
        self.cv.bind("<Motion>", self.on_hover)

        panel = ttk.Frame(self.paned, style="Panel.TFrame", padding=(6, 4, 6, 6))
        self.paned.add(panel, minsize=170, stretch="never")
        self.root.after(100, lambda: self.paned.sash_place(
            0, 0, max(120, self.paned.winfo_height() - panel.winfo_reqheight() - 40)))

        # one toolbar row: transport, edit tools, track tools, view toggles
        tb = ttk.Frame(panel, style="Panel.TFrame")
        tb.pack(fill="x", pady=(0, 4))
        self.tool(tb, "prev", lambda: self.seek(self.render_range()[0]), "Go to in  (Home)")
        self.play_btn = self.btn(tb, ICON["play"], self.toggle_play, style="PlayTool.TButton",
                                 tip="Play / pause  (Space)")
        self.play_btn.pack(side="left", padx=3)
        self.tool(tb, "next", lambda: self.seek(self.render_range()[1]), "Go to out  (End)")
        self.time_lbl = ttk.Label(tb, text="0:00.00 / 0:00.00", style="Time.TLabel")
        self.time_lbl.pack(side="left", padx=(10, 4))
        self.sep(tb)
        for text, cmd, tip in (("In", self.set_start, "Mark in  (I)"),
                               ("Out", self.set_end, "Mark out  (O)"),
                               ("Clear", self.clear_marks, "Clear in / out marks")):
            self.btn(tb, text, cmd, style="ToolText.TButton", tip=tip).pack(side="left", padx=(0, 2))
        self.sep(tb)
        self.tool(tb, "cut", self.split, "Split at playhead  (Ctrl+B)")
        self.tool(tb, "delete", self.delete_sel, "Delete  (Del)")
        self.tool(tb, "ripple", lambda: self.delete_sel(True), "Ripple delete, closes the gap  (Shift+Del)")
        self.tool(tb, "link", self.toggle_link, "Link / unlink selected clips")
        self.sep(tb)
        self.tool(tb, "undo", self.undo, "Undo  (Ctrl+Z)")
        self.tool(tb, "redo", self.redo, "Redo  (Ctrl+Y)")
        self.sep(tb)
        self.tool(tb, "video", lambda: self.add_track("v"), "Add video track")
        self.tool(tb, "music", lambda: self.add_track("a"), "Add audio track")
        self.tool(tb, "add", self.add_media, "Import media  (Ctrl+I)")

        for w in (self.btn(tb, ICON["fit"], self.zoom_fit, style="Tool.TButton",
                           tip="Zoom to fit  (Shift+Z)"),
                  self.btn(tb, ICON["zin"], lambda: self.zoom(1.5), style="Tool.TButton",
                           tip="Zoom in  (+)"),
                  self.btn(tb, ICON["zout"], lambda: self.zoom(1 / 1.5), style="Tool.TButton",
                           tip="Zoom out  (−)")):
            w.pack(side="right")
        ttk.Separator(tb, orient="vertical").pack(side="right", fill="y", padx=8, pady=3)
        self.pvol = tk.DoubleVar(value=self.settings.get("preview_volume", 100))
        self.vol_cv = tk.Canvas(tb, width=64, height=24, bg=PANEL, highlightthickness=0,
                                cursor="hand2")
        self.vol_cv.pack(side="right", padx=(0, 4))
        for ev in ("<ButtonPress-1>", "<B1-Motion>"):
            self.vol_cv.bind(ev, lambda e: self.set_pvol(100 * (e.x - 7) / 50))
        self.vol_cv.bind("<MouseWheel>",
                         lambda e: self.set_pvol(self.pvol.get() + (5 if e.delta > 0 else -5)))
        self.draw_pvol()
        Tip(self.vol_cv, "Playback volume (preview only, not the export)")
        for w in (self.toggle(tb, self.mute, None, "Mute preview sound", ("mute", "vol"),
                              self.restart_if_playing),
                  self.toggle(tb, self.loop, None, "Loop playback (in / out range)", ("loop", "loop")),
                  self.toggle(tb, self.snap, "Snap", "Snap clips to edges and playhead  (N)")):
            w.pack(side="right", padx=(2, 0))
        self.range_lbl = ttk.Label(tb, text="", style="PanelMuted.TLabel", font=SMALL_FONT)
        self.range_lbl.pack(side="right", padx=(0, 8))

        self.hs = ttk.Scrollbar(panel, orient="horizontal", command=self.on_hscroll)
        self.hs.pack(side="bottom", fill="x", pady=(2, 0))
        self.vs = ttk.Scrollbar(panel, orient="vertical", command=self.on_vscroll)
        self.vs.pack(side="right", fill="y", padx=(2, 0))
        self.tl = tk.Canvas(panel, height=self.rows()[-1][3] + 40, bg=LANE, highlightthickness=0)
        self.tl.pack(fill="both", expand=True)
        self.tl.bind("<Configure>", lambda e: self.draw_timeline())
        self.tl.bind("<ButtonPress-1>", self.on_tl_press)
        self.tl.bind("<B1-Motion>", self.on_tl_drag)
        self.tl.bind("<ButtonRelease-1>", self.on_tl_release)
        self.tl.bind("<ButtonPress-3>", self.on_tl_right)
        self.tl.bind("<Motion>", self.on_tl_hover)
        self.tl.bind("<Leave>", lambda e: self.set_hover(None))
        self.tl.bind("<MouseWheel>", self.on_tl_wheel)

        for key, fn in (("<space>", lambda e: self.toggle_play()),
                        ("<Left>", lambda e: self.step(-1)), ("<Right>", lambda e: self.step(1)),
                        ("<Shift-Left>", lambda e: self.step(-5)),
                        ("<Shift-Right>", lambda e: self.step(5)),
                        ("<KeyPress-i>", lambda e: self.set_start()),
                        ("<KeyPress-o>", lambda e: self.set_end()),
                        ("<KeyPress-n>", lambda e: self.snap.set(not self.snap.get())),
                        ("<KeyPress-Z>", lambda e: self.zoom_fit()),
                        ("<KeyPress-plus>", lambda e: self.zoom(1.5)),
                        ("<KeyPress-equal>", lambda e: self.zoom(1.5)),
                        ("<KeyPress-minus>", lambda e: self.zoom(1 / 1.5)),
                        ("<Home>", lambda e: self.seek(self.render_range()[0])),
                        ("<End>", lambda e: self.seek(self.render_range()[1])),
                        ("<Control-o>", lambda e: self.open_any()),
                        ("<Control-n>", lambda e: self.new_project()),
                        ("<Control-s>", lambda e: self.save_project()),
                        ("<Control-S>", lambda e: self.save_project(True)),
                        ("<Control-i>", lambda e: self.add_media()),
                        ("<Control-e>", lambda e: self.export()),
                        ("<Control-a>", lambda e: self.select(c["id"] for c in self.clips)),
                        ("<Control-b>", lambda e: self.split()),
                        ("<Control-c>", lambda e: self.copy_sel()),
                        ("<Control-x>", lambda e: self.copy_sel(True)),
                        ("<Control-v>", lambda e: self.paste()),
                        ("<Control-B>", lambda e: self.split()),
                        ("<Delete>", lambda e: self.delete_sel()),
                        ("<BackSpace>", lambda e: self.delete_sel()),
                        ("<Shift-Delete>", lambda e: self.delete_sel(True)),
                        ("<Shift-BackSpace>", lambda e: self.delete_sel(True)),
                        ("<Control-z>", lambda e: self.undo()),
                        ("<Control-y>", lambda e: self.redo()),
                        ("<Control-Z>", lambda e: self.redo()),
                        ("<Escape>", lambda e: self.select(set()))):
            self.root.bind(key, self.guard(fn))
        self.update_range()
        self.setup_dnd()

    def guard(self, fn):
        def handler(e):
            if isinstance(e.widget, (tk.Entry, ttk.Entry)) or self.export_proc:
                return None
            fn(e)
            return "break"
        return handler

    # ---------- coordinates ----------
    def layout(self):
        cw, ch = max(self.cv.winfo_width(), 1), max(self.cv.winfo_height(), 1)
        self.s = min(cw / self.vw, ch / self.vh)
        self.dw, self.dh = max(2, int(self.vw * self.s)), max(2, int(self.vh * self.s))
        self.ox, self.oy = (cw - self.dw) // 2, (ch - self.dh) // 2

    def to_cv(self, x, y):
        return self.ox + x * self.s, self.oy + y * self.s

    def to_src(self, x, y):
        return (x - self.ox) / self.s, (y - self.oy) / self.s

    # ---------- drawing ----------
    def redraw(self):
        cv = self.cv
        cv.delete("all")
        if not self.vw:
            cx, cy = cv.winfo_width() // 2, cv.winfo_height() // 2
            cv.create_text(cx, cy - 12, text="Open a video to start", fill=FG,
                           font=("Segoe UI Semibold", 15))
            cv.create_text(cx, cy + 16, text="Add media puts more videos, images or audio on the "
                                             "timeline. Drag clips onto V2 to layer them.",
                           fill=MUTED, font=UI_FONT)
            return
        self.layout()

        if not self.clips:
            cv.create_rectangle(self.ox, self.oy, self.ox + self.dw, self.oy + self.dh,
                                fill="#000000", outline="")
            cv.create_text(self.ox + self.dw / 2, self.oy + self.dh / 2,
                           text=f"{self.vw} x {self.vh}\nDrop or import media", fill=MUTED,
                           font=UI_FONT, justify="center")
        elif self.frame is not None:
            key = (id(self.frame), self.dw, self.dh)
            if key != self.photo_key:
                img = self.frame
                if img.size != (self.dw, self.dh):
                    img = img.resize((self.dw, self.dh), Image.BILINEAR)
                self.frame_photo = ImageTk.PhotoImage(img)
                self.photo_key = key
            cv.create_image(self.ox, self.oy, image=self.frame_photo, anchor="nw")

        x, y, w, h = self.crop
        x1, y1 = self.to_cv(x, y)
        x2, y2 = self.to_cv(x + w, y + h)
        L, T, R, B = self.ox, self.oy, self.ox + self.dw, self.oy + self.dh
        for r in ((L, T, R, y1), (L, y2, R, B), (L, y1, x1, y2), (x2, y1, R, y2)):
            if r[2] > r[0] and r[3] > r[1]:
                cv.create_rectangle(*r, fill="black", stipple="gray50", outline="")

        tgt = self.preview_target()
        if tgt:   # on-screen transform box of the selected clip (drag inside / corners)
            pts = self.clip_corners(tgt)
            cv.create_polygon(*[v for p in pts for v in p], outline="#ffffff", fill="", width=1,
                              dash=(6, 3))
            for hx, hy in pts:
                cv.create_rectangle(hx - 5, hy - 5, hx + 5, hy + 5, fill="#ffffff",
                                    outline="#000000")
            cx, cy = self.to_cv(*self.clip_geom(tgt)[:2])
            cv.create_line(cx - 7, cy, cx + 8, cy, fill="#ffffff")
            cv.create_line(cx, cy - 7, cx, cy + 8, fill="#ffffff")
        cv.create_rectangle(x1, y1, x2, y2, outline=ACCENT, width=2)
        for hx, hy in ((x1, y1), (x2, y1), (x1, y2), (x2, y2)):
            cv.create_rectangle(hx - HANDLE, hy - HANDLE, hx + HANDLE, hy + HANDLE,
                                fill=ACCENT, outline="white")
        ow, oh = self.output_dims()
        tag = f"{int(w) // 2 * 2} x {int(h) // 2 * 2}"
        if (ow, oh) != (int(w) // 2 * 2, int(h) // 2 * 2):
            tag += f"  →  {ow} x {oh}"
        t = cv.create_text(x1 + 10, y1 + 10, anchor="nw", fill="white", text=tag, font=UI_FONT)
        bx = cv.bbox(t)
        bg = cv.create_rectangle(bx[0] - 5, bx[1] - 3, bx[2] + 5, bx[3] + 3, fill="#000000",
                                 stipple="gray50", outline="")
        cv.tag_lower(bg, t)

    def update_time(self):
        self.time_lbl.config(text=f"{fmt_time(self.cur)} / {fmt_time(self.tl_end())}")

    def update_range(self):
        text = ""
        if self.clips and (self.mark_in is not None or self.mark_out is not None):
            rs, re = self.render_range()
            text = f"{fmt_time(rs)} – {fmt_time(re)}  ({fmt_time(re - rs)})"
        self.range_lbl.config(text=text)

    # ---------- timeline model ----------
    def new_id(self):
        self.next_id += 1
        return self.next_id

    def tracks(self, kind):
        return self.vtracks if kind == "v" else self.atracks

    def frame_t(self):
        """Time of the preview still: the playhead, kept just inside the last clip when it sits
        on its very end (past the end there is nothing, so the frame is black)."""
        end = self.tl_end()
        return min(self.cur, max(end - 0.05, 0.0)) if end - 0.05 <= self.cur <= end else self.cur

    def tl_end(self):
        return max((c["start"] + c["dur"] for c in self.clips), default=0.0)

    def render_range(self):
        end = self.tl_end()
        rs = self.mark_in if self.mark_in is not None and self.mark_in < end else 0.0
        re = self.mark_out if self.mark_out is not None and self.mark_out > rs else end
        return rs, min(re, end)

    def render_len(self):
        rs, re = self.render_range()
        return re - rs

    def any_audio(self):
        rs, re = self.render_range()
        return any(c["kind"] == "a" and not self.atracks[c["track"]]["muted"]
                   and c["start"] < re and c["start"] + c["dur"] > rs for c in self.clips)

    def linked(self, c):
        if not c["link"]:
            return {c["id"]}
        return {o["id"] for o in self.clips if o["link"] == c["link"]}

    def by_id(self, i):
        return next(c for c in self.clips if c["id"] == i)

    def snapshot(self):
        return copy.deepcopy((self.clips, self.vtracks, self.atracks, (self.vw, self.vh, self.crop)))

    def restore(self, snap):
        self.clips, self.vtracks, self.atracks, frame = copy.deepcopy(snap)
        if frame[:2] != (self.vw, self.vh):
            self.vw, self.vh, self.crop = frame
            self.frame = None
            self.redraw()
        self.after_edit()

    def commit(self, before):
        self.undo_stack.append(before)
        del self.undo_stack[:-100]
        self.redo_stack.clear()
        self.after_edit()

    def undo(self):
        if self.undo_stack:
            self.pause()
            self.redo_stack.append(self.snapshot())
            self.restore(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.pause()
            self.undo_stack.append(self.snapshot())
            self.restore(self.redo_stack.pop())

    def after_edit(self):
        self.sel &= {c["id"] for c in self.clips}
        self.cur = max(self.cur, 0.0)
        self.draw_timeline()
        self.update_range()
        self.update_time()
        if not self.clips:
            self.frame = None
            self.redraw()
        self.schedule_frame()

    def clear_range(self, kind, track, s, e, keep):
        """Overwrite: cut away whatever else sits on this track between s and e."""
        for c in list(self.clips):
            if c["kind"] != kind or c["track"] != track or c["id"] in keep:
                continue
            cs, ce = c["start"], c["start"] + c["dur"]
            if ce <= s + 1e-6 or cs >= e - 1e-6:
                continue
            if cs >= s - 1e-6 and ce <= e + 1e-6:
                self.clips.remove(c)
            elif cs < s and ce > e:
                self.clips.append(dict(c, id=self.new_id(), start=e,
                                       inp=c["inp"] + (e - cs) * spd(c), dur=ce - e, link=None))
                c["dur"] = s - cs
            elif cs < s:
                c["dur"] = s - cs
            else:
                c["inp"] += (e - cs) * spd(c)
                c["dur"] = ce - e
                c["start"] = e

    # ---------- timeline editing ----------
    def add_track(self, kind):
        before = self.snapshot()
        self.tracks(kind).append({"hidden": False} if kind == "v" else {"muted": False})
        self.commit(before)

    def delete_track(self, kind, i):
        if len(self.tracks(kind)) < 2:
            return
        self.pause()
        before = self.snapshot()
        del self.tracks(kind)[i]
        self.clips = [c for c in self.clips if not (c["kind"] == kind and c["track"] == i)]
        for c in self.clips:
            if c["kind"] == kind and c["track"] > i:
                c["track"] -= 1
        self.commit(before)

    def split(self):
        t = self.cur
        hit = [c for c in self.clips if c["start"] + 0.01 < t < c["start"] + c["dur"] - 0.01]
        targets = [c for c in hit if c["id"] in self.sel] or hit
        if not targets:
            return
        self.pause()
        before = self.snapshot()
        newlink = {}
        for c in targets:
            k = t - c["start"]
            link = newlink.setdefault(c["link"], self.new_id()) if c["link"] else None
            self.clips.append(dict(c, id=self.new_id(), start=t, inp=c["inp"] + k * spd(c),
                                   dur=c["dur"] - k, link=link))
            c["dur"] = k
        self.commit(before)

    def delete_sel(self, ripple=False):
        gone = [c for c in self.clips if c["id"] in self.sel]
        if not gone:
            return
        self.pause()
        before = self.snapshot()
        self.clips = [c for c in self.clips if c["id"] not in self.sel]
        if ripple:
            for g in sorted(gone, key=lambda c: c["start"], reverse=True):
                for c in self.clips:
                    if (c["kind"], c["track"]) == (g["kind"], g["track"]) \
                            and c["start"] >= g["start"] + g["dur"] - 1e-6:
                        c["start"] -= g["dur"]
            self.cur = min(gone, key=lambda c: c["start"])["start"]
        self.sel.clear()
        self.commit(before)

    def copy_sel(self, cut=False):
        """Ctrl+C / Ctrl+X: the selected clips (with their settings) go to the clipboard."""
        chosen = [c for c in self.clips if c["id"] in self.sel]
        if not chosen:
            return
        t0 = min(c["start"] for c in chosen)
        self.clipboard = [dict(copy.deepcopy(c), start=c["start"] - t0) for c in chosen]
        n = f"{len(chosen)} clip{'' if len(chosen) == 1 else 's'}"
        if cut:
            self.delete_sel()
        self.status.config(text=f"{'Cut' if cut else 'Copied'} {n}   ·   Ctrl+V pastes at the "
                                f"playhead")

    def paste(self, t=None):
        """Ctrl+V: the clipboard goes in at the playhead (or t) on the same tracks, over what's
        there, like a drop. The pasted clips are selected and the playhead moves to their end,
        so pasting again puts the next copy right after."""
        if not self.clipboard:
            return
        self.pause()
        t = self.cur if t is None else max(t, 0.0)
        new, links = [], {}
        for c in self.clipboard:
            if not self.load_media(c["path"]):
                continue
            n = copy.deepcopy(c)
            n.update(id=self.new_id(), start=t + c["start"],
                     link=links.setdefault(c["link"], self.new_id()) if c["link"] else None)
            n.pop("fill", None)
            new.append(n)
        if not new:
            return
        before = self.snapshot()
        if not self.vw:   # pasted into an empty project: the first picture sets the frame
            m = next((self.media[n["path"]] for n in new if n["kind"] == "v"), None)
            self.vw, self.vh = (m and m.get("size")) or (1920, 1080)
            self.crop = [0.0, 0.0, float(self.vw), float(self.vh)]
            self.apply_ratio()
        for n in new:
            tracks = self.tracks(n["kind"])
            while len(tracks) <= n["track"]:
                tracks.append({"hidden": False} if n["kind"] == "v" else {"muted": False})
            self.clear_range(n["kind"], n["track"], n["start"], n["start"] + n["dur"], set())
        self.clips += new
        self.sel = {n["id"] for n in new}
        self.cur = max(n["start"] + n["dur"] for n in new)
        self.commit(before)
        self.follow_playhead()
        self.status.config(text=f"Pasted {len(new)} clip{'' if len(new) == 1 else 's'} at "
                                f"{fmt_time(t)}")

    def toggle_link(self):
        chosen = [c for c in self.clips if c["id"] in self.sel]
        if not chosen:
            return
        before = self.snapshot()
        if any(c["link"] for c in chosen):
            for c in chosen:
                c["link"] = None
        else:
            link = self.new_id()
            for c in chosen:
                c["link"] = link
        self.commit(before)

    def select(self, ids):
        self.sel = set(ids)
        self.draw_timeline()

    # ---------- inspector ----------
    # (key, label, low, high, step per pixel when dragging the value, format, slider?)
    INSPECTOR = {
        "v": [("Transform", [("zoom", "Zoom", 0.05, 4.0, 0.005, "{:.3f}", True),
                             ("pos_x", "Position X", -10000, 10000, 1, "{:.0f}", False),
                             ("pos_y", "Position Y", -10000, 10000, 1, "{:.0f}", False),
                             ("rotation", "Rotation", -180, 180, 0.5, "{:.1f}°", True)]),
              ("Cropping", [("crop_l", "Left", 0, 49, 0.2, "{:.1f}%", True),
                            ("crop_r", "Right", 0, 49, 0.2, "{:.1f}%", True),
                            ("crop_t", "Top", 0, 49, 0.2, "{:.1f}%", True),
                            ("crop_b", "Bottom", 0, 49, 0.2, "{:.1f}%", True)]),
              ("Composite", [("opacity", "Opacity", 0, 100, 0.5, "{:.0f}%", True)]),
              ("Fade", [("fade_in", "Fade in", 0, 10, 0.02, "{:.2f} s", True),
                        ("fade_out", "Fade out", 0, 10, 0.02, "{:.2f} s", True)]),
              ("Speed", [("speed", "Speed", 10, 400, 0.5, "{:.0f}%", True)])],
        "c": [("Caption", []),
              ("Transform", [("zoom", "Size", 0.2, 4.0, 0.005, "{:.3f}", True),
                             ("pos_x", "Position X", -10000, 10000, 1, "{:.0f}", False),
                             ("pos_y", "Position Y", -10000, 10000, 1, "{:.0f}", False),
                             ("rotation", "Rotation", -180, 180, 0.5, "{:.1f}°", True)]),
              ("Composite", [("opacity", "Opacity", 0, 100, 0.5, "{:.0f}%", True)]),
              ("Fade", [("fade_in", "Fade in", 0, 10, 0.02, "{:.2f} s", True),
                        ("fade_out", "Fade out", 0, 10, 0.02, "{:.2f} s", True)])],
        "a": [("Audio", [("volume", "Volume", -60, 12, 0.1, "{:+.1f} dB", True),
                         ("pan", "Pan", -1, 1, 0.01, "{:+.2f}", True)]),
              ("Fade", [("fade_in", "Fade in", 0, 10, 0.02, "{:.2f} s", True),
                        ("fade_out", "Fade out", 0, 10, 0.02, "{:.2f} s", True)]),
              ("Speed", [("speed", "Speed", 10, 400, 0.5, "{:.0f}%", True)])],
    }

    def build_inspector(self):
        box = ttk.Frame(self.hpane, style="Panel.TFrame", padding=(12, 8, 4, 8))
        self.hpane.add(box, minsize=280, width=380, stretch="never")
        self.insp_kind = "v"          # tab shown: "v" (video / image), "a" or "c" (caption)
        self.insp_focus = None        # id of the clip last clicked
        self.insp_before = self.insp_job = self.insp_sig = self.insp_shown = None
        head = ttk.Frame(box, style="Panel.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text="Inspector", style="Panel.TLabel",
                  font=("Segoe UI Semibold", 11)).pack(side="left")
        self.insp_tabs = {}
        for kind in ("a", "v", "c"):
            self.insp_tabs[kind] = self.btn(head, "", lambda k=kind: self.set_insp_tab(k),
                                            style="ToolText.TButton")
        self.insp_name = ttk.Label(box, text="", style="PanelMuted.TLabel", font=SMALL_FONT)
        self.insp_name.pack(anchor="w", pady=(2, 6))
        self.insp_empty = ttk.Label(box, text="Click a clip on the timeline or in the preview "
                                              "to edit it.",
                                    style="PanelMuted.TLabel", wraplength=300, justify="left")
        self.insp_empty.pack(anchor="w")
        wrap = ttk.Frame(box, style="Panel.TFrame")
        wrap.pack(fill="both", expand=True)
        self.insp_cv = tk.Canvas(wrap, bg=PANEL, highlightthickness=0, width=300)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.insp_cv.yview)
        self.insp_cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.insp_cv.pack(side="left", fill="both", expand=True)
        self.insp_body, self.fields = {}, {}
        for kind, groups in self.INSPECTOR.items():
            body = ttk.Frame(self.insp_cv, style="Panel.TFrame", padding=(0, 0, 10, 0))
            body.columnconfigure(1, weight=1)
            r = 0
            for title, rows in groups:
                ttk.Label(body, text=title, style="Panel.TLabel", foreground=ACCENT,
                          font=("Segoe UI Semibold", 10)).grid(row=r, column=0, columnspan=4,
                                                               sticky="w", pady=(10 if r else 0, 2))
                r += 1
                if kind == "c" and title == "Caption":   # the text, edited in place
                    self.cap_text = tk.Text(body, height=3, width=30, wrap="word", bg=FIELD,
                                            fg=FG, insertbackground=FG, relief="flat",
                                            font=UI_FONT, padx=6, pady=4, undo=False)
                    self.cap_text.grid(row=r, column=0, columnspan=4, sticky="ew", pady=(2, 2))
                    self.cap_text.bind("<KeyRelease>", lambda e: self.cap_typed())
                    self.cap_text.bind("<Control-z>", lambda e: (self.undo(), "break")[1])
                    r += 1
                    ttk.Label(body, text="Enter makes a new line; Ctrl+Z undoes.",
                              style="PanelMuted.TLabel", font=SMALL_FONT).grid(
                        row=r, column=0, columnspan=4, sticky="w")
                    r += 1
                    hl = ttk.Frame(body, style="Panel.TFrame")
                    hl.grid(row=r, column=0, columnspan=4, sticky="w", pady=(6, 0))
                    var = tk.BooleanVar(value=True)
                    ttk.Checkbutton(hl, text="Highlight the spoken word", variable=var,
                                    style="Panel.TCheckbutton", takefocus=False,
                                    command=lambda v=var: (self.insp_set("c", "highlight", v.get()),
                                                           self.insp_end())).pack(side="left")
                    self.fields[("c", "highlight")] = var
                    self.hl_var = tk.StringVar(value="Yellow")
                    box = ttk.Combobox(hl, textvariable=self.hl_var, values=list(HL_COLORS),
                                       state="readonly", width=8)
                    box.pack(side="left", padx=(10, 0))
                    box.bind("<<ComboboxSelected>>", lambda e: (
                        self.insp_set("c", "hl_color", self.hl_var.get()), self.insp_end()))
                    r += 1
                    ttk.Label(body, text="Font", style="PanelMuted.TLabel").grid(
                        row=r, column=0, sticky="w", pady=(6, 0))
                    self.font_var = tk.StringVar()
                    self.font_box = ttk.Combobox(body, textvariable=self.font_var,
                                                 state="readonly", width=26, height=20)
                    self.font_box.grid(row=r, column=1, columnspan=3, sticky="ew", pady=(6, 0))
                    self.font_box.bind("<<ComboboxSelected>>", lambda e: self.pick_font())
                    r += 1
                for key, label, lo, hi, step, fmt, slider in rows:
                    self.fields[(kind, key)] = SlideField(
                        body, r, label, lo, hi, step, fmt, CLIP_DEFAULTS[key],
                        lambda v, k=kind, key=key: self.insp_set(k, key, v), self.insp_end,
                        slider)
                    r += 1
                if kind == "v" and title == "Transform":
                    ttk.Label(body, text="Flip", style="PanelMuted.TLabel").grid(row=r, column=0,
                                                                                 sticky="w")
                    fl = ttk.Frame(body, style="Panel.TFrame")
                    fl.grid(row=r, column=1, columnspan=3, sticky="w", pady=(2, 0))
                    for key, text in (("flip_h", "Horizontal"), ("flip_v", "Vertical")):
                        var = tk.BooleanVar()
                        ttk.Checkbutton(fl, text=text, variable=var, style="Panel.TCheckbutton",
                                        takefocus=False,
                                        command=lambda k=key, v=var: (self.insp_set("v", k, v.get()),
                                                                      self.insp_end())
                                        ).pack(side="left", padx=(0, 12))
                        self.fields[("v", key)] = var
                    r += 1
                if title == "Speed":   # pitch follows the sound of the clip and its linked partner
                    var = tk.BooleanVar(value=True)
                    ttk.Checkbutton(body, text="Keep pitch (no chipmunk / slow-mo voice)",
                                    variable=var, style="Panel.TCheckbutton", takefocus=False,
                                    command=lambda k=kind, v=var: (
                                        self.insp_set(k, "keep_pitch", v.get()), self.insp_end())
                                    ).grid(row=r, column=0, columnspan=4, sticky="w", pady=(2, 0))
                    self.fields[(kind, "keep_pitch")] = var
                    r += 1
            self.btn(body, "Reset all", lambda k=kind: self.insp_reset(k),
                     style="ToolText.TButton").grid(row=r, column=0, columnspan=4, sticky="w",
                                                    pady=(12, 4))
            if kind in ("v", "a"):
                self.btn(body, "Auto captions…", lambda: self.auto_captions(),
                         style="ToolText.TButton").grid(row=r + 1, column=0, columnspan=4,
                                                        sticky="w", pady=(0, 4))
            self.insp_body[kind] = body
        self.insp_win = self.insp_cv.create_window(0, 0, anchor="nw")
        self.insp_cv.bind("<Configure>",
                          lambda e: self.insp_cv.itemconfig(self.insp_win, width=e.width))
        self.insp_cv.bind_all("<MouseWheel>", self.on_insp_wheel, add="+")
        self.refresh_inspector(force=True)

    def on_insp_wheel(self, e):
        w = e.widget
        while w is not None and w is not self.insp_cv:
            if isinstance(w, tk.Canvas) and w is not self.insp_cv or isinstance(w, tk.Label) \
                    and str(w.cget("cursor")) == "sb_h_double_arrow":
                return   # a slider / value box under the mouse takes the wheel itself
            w = getattr(w, "master", None)
        if w is self.insp_cv:
            self.insp_cv.yview_scroll(-e.delta // 120, "units")

    @staticmethod
    def ikind(c):
        """Inspector tab of a clip: "c" for captions, else its kind."""
        return "c" if "caption" in c else c["kind"]

    def insp_clips(self, kind):
        """Selected clips the inspector edits on this tab; the clicked one comes first."""
        chosen = [c for c in self.clips if c["id"] in self.sel and self.ikind(c) == kind]
        chosen.sort(key=lambda c: c["id"] != self.insp_focus)
        return chosen

    def set_insp_tab(self, kind):
        self.insp_kind = kind
        self.refresh_inspector(force=True)

    def insp_signature(self):
        clips = self.insp_clips(self.insp_kind)
        return (tuple(sorted(self.sel)), self.insp_kind, self.insp_focus,
                tuple(sorted(clips[0].items())) if clips else None)

    def refresh_inspector(self, force=False):
        kinds = [k for k in ("c", "v", "a") if self.insp_clips(k)]
        if kinds and self.insp_kind not in kinds:
            self.insp_kind = kinds[0]
        sig = self.insp_signature()
        if sig == self.insp_sig and not force:
            return
        self.insp_sig = sig
        clips = self.insp_clips(self.insp_kind)
        for kind, b in self.insp_tabs.items():
            if kind in kinds:
                vc = self.insp_clips("v")
                image = kind == "v" and self.media.get(vc[0]["path"], {}).get("image")
                b.config(text="Caption" if kind == "c" else "Image" if image else
                         ("Video" if kind == "v" else "Audio"),
                         style="ToolTextOn.TButton" if kind == self.insp_kind else "ToolText.TButton")
                b.pack(side="right", padx=(2, 0))
            else:
                b.pack_forget()
        if not clips:
            if self.insp_shown is not None:
                self.insp_cv.itemconfig(self.insp_win, window="")
                self.insp_shown = None
            self.insp_name.config(text="")
            self.insp_empty.pack(anchor="w", after=self.insp_name)
            return
        self.insp_empty.pack_forget()
        c = clips[0]
        more = f"   (+{len(clips) - 1} more)" if len(clips) > 1 else ""
        self.insp_name.config(text=("Caption at " + fmt_time(c["start"]) if "caption" in c
                                    else Path(c["path"]).name[:40]) + more)
        if self.insp_shown != self.insp_kind:   # layout work only when the tab changes
            body = self.insp_body[self.insp_kind]
            self.insp_cv.itemconfig(self.insp_win, window=body)
            body.update_idletasks()
            self.insp_cv.configure(scrollregion=(0, 0, 0, body.winfo_reqheight()))
            self.insp_shown = self.insp_kind
        for (kind, key), f in self.fields.items():
            if kind != self.insp_kind:
                continue
            if isinstance(f, tk.BooleanVar):
                f.set(bool(cp(c, key)))
                continue
            if key in ("fade_in", "fade_out"):
                f.hi = max(0.1, min(10.0, c["dur"]))
            f.show(cp(c, key))
        if self.insp_kind == "c":
            self.hl_var.set(cp(c, "hl_color"))
            names = sorted(installed_fonts(), key=str.lower)
            if CAPTION_FONT not in installed_fonts():
                names.insert(0, f"{CAPTION_FONT} (not installed)")
            self.font_box.config(values=names)
            want = cp(c, "font")
            self.font_var.set(want if want in installed_fonts() else f"{want} (not installed)")
        if self.insp_kind == "c" and self.root.focus_get() is not self.cap_text:
            if self.cap_text.get("1.0", "end-1c") != c["caption"]:
                self.cap_text.delete("1.0", "end")
                self.cap_text.insert("1.0", c["caption"])

    def set_prop(self, c, key, value):
        if value == CLIP_DEFAULTS[key]:
            c.pop(key, None)
        else:
            c[key] = value

    def insp_set(self, kind, key, value):
        """Writes a setting to the selected clips of this kind. A run of changes (a slider drag)
        becomes one undo step, closed by insp_end() or 500 ms of quiet."""
        clips = self.insp_clips(kind)
        if not clips:
            return
        if isinstance(CLIP_DEFAULTS[key], float):
            value = float(value)
            if key in ("fade_in", "fade_out"):
                value = min(max(value, 0.0), min(c["dur"] for c in clips))
        if self.insp_before is None:
            self.insp_before = self.snapshot()
        if key in ("speed", "keep_pitch"):   # a clip's picture and sound stay in sync
            ids = {i for c in clips for i in self.linked(c)}
            clips = [c for c in self.clips if c["id"] in ids and not self.media[c["path"]]["image"]]
        if key == "speed":
            self.set_speed(clips, value)
        for c in clips:
            self.set_prop(c, key, value)
        self.insp_sig = self.insp_signature()   # the field already shows it; no full refresh
        if self.insp_job:
            self.root.after_cancel(self.insp_job)
        self.insp_job = self.root.after(500, self.insp_commit)
        if key in ("volume", "fade_in", "fade_out", "speed"):   # the ones drawn on the timeline
            self.draw_timeline_soon()
        if self.player:
            # restarting playback on every slider step would stall it; wait until it rests
            if self.replay_job:
                self.root.after_cancel(self.replay_job)
            self.replay_job = self.root.after(300, self.replay)
        elif kind in ("v", "c"):
            self.queue_live()

    def pick_font(self):
        name = self.font_var.get().replace(" (not installed)", "")
        self.insp_set("c", "font", name)
        self.insp_end()
        self.draw_timeline_soon()

    def cap_typed(self):
        """Typing in the Caption box: the focused caption takes the text (one undo step per
        run of typing, like a slider)."""
        clips = self.insp_clips("c")
        text = self.cap_text.get("1.0", "end-1c").strip()
        if not clips or not text or clips[0]["caption"] == text:
            return
        if self.insp_before is None:
            self.insp_before = self.snapshot()
        clips[0]["caption"] = text
        self.insp_sig = self.insp_signature()
        if self.insp_job:
            self.root.after_cancel(self.insp_job)
        self.insp_job = self.root.after(800, self.insp_commit)
        self.draw_timeline_soon()
        self.queue_live()
        self.schedule_frame(500)

    def set_speed(self, clips, pct):
        """A new speed (%) keeps the same part of the file, so the clip gets shorter or longer;
        what comes after it on its track moves along, so nothing is cut or overlapped."""
        for c in sorted(clips, key=lambda c: c["start"]):
            if self.media[c["path"]]["image"]:
                continue
            old, new = spd(c), pct / 100
            self.set_prop(c, "speed", float(pct))
            if abs(old - new) < 1e-9:
                continue
            end = c["start"] + c["dur"]
            dur = c["dur"] * old / new
            for o in self.clips:
                if o is not c and (o["kind"], o["track"]) == (c["kind"], c["track"]) \
                        and o["start"] >= end - 1e-6:
                    o["start"] += dur - c["dur"]
            c["dur"] = dur
            for k in ("fade_in", "fade_out"):
                if cp(c, k) > dur:
                    c[k] = dur

    def insp_end(self):
        """A slider / value drag ended: close the undo step and fetch the exact frame."""
        if self.insp_job:
            self.root.after_cancel(self.insp_job)
        self.insp_commit()
        if not self.player:
            self.schedule_frame(150)

    def queue_live(self):
        if not self.live_job:
            self.live_job = self.root.after_idle(self.run_live)

    def replay(self):
        self.replay_job = None
        self.restart_if_playing()

    def run_live(self):
        """Instant preview while something moves; the exact FFmpeg frame follows when it rests."""
        self.live_job = None
        self.live_preview()
        self.schedule_frame(450)

    def insp_commit(self):
        self.insp_job = None
        if self.insp_before is not None:
            before, self.insp_before = self.insp_before, None
            self.undo_stack.append(before)
            del self.undo_stack[:-100]
            self.redo_stack.clear()

    def insp_reset(self, kind):
        clips = self.insp_clips(kind)
        if not clips:
            return
        before = self.snapshot()
        keys = {k for _, rows in self.INSPECTOR[kind] for k, *_ in rows} | {"keep_pitch"}
        keys |= {"highlight", "hl_color", "font"} if kind == "c" else set()
        keys |= {"flip_h", "flip_v"} if kind == "v" else set()
        ids = {i for c in clips for i in self.linked(c)}
        self.set_speed([c for c in self.clips if c["id"] in ids], 100.0)   # back to full length
        for c in clips:
            for k in keys:
                c.pop(k, None)
        self.commit(before)
        self.refresh_inspector(force=True)

    # ---------- timeline geometry ----------
    def rows(self):
        """(kind, track, y0, y1) top to bottom: video tracks highest first, then audio.
        Scrolled by yoff; rows above the ruler are hidden under it."""
        out, y = [], RULER_H - self.yoff
        for kind, i in ([("v", i) for i in range(len(self.vtracks) - 1, -1, -1)] +
                        [("a", i) for i in range(len(self.atracks))]):
            h = self.track_h(kind, i)
            out.append((kind, i, y, y + h))
            y += h
        return out

    def track_h(self, kind, i):
        return self.tracks(kind)[i].get("h", VT_H if kind == "v" else AT_H)

    def height_edge(self, x, y):
        """Track whose bottom border in the header column is under the mouse (drag to resize)."""
        if x >= HEAD_W or y < RULER_H:
            return None
        return next((r for r in self.rows() if abs(y - r[3]) <= 3), None)

    def row_at(self, y):
        if y < RULER_H:
            return None
        return next((r for r in self.rows() if r[2] <= y < r[3]), None)

    def max_yoff(self):
        content = sum(r[3] - r[2] for r in self.rows())
        return max(0.0, content - (self.tl.winfo_height() - RULER_H))

    def row_of(self, kind, i):
        return next(r for r in self.rows() if r[0] == kind and r[1] == i)

    def t2x(self, t):
        return HEAD_W + (t - self.view_t) * self.pps

    def x2t(self, x):
        return self.view_t + (x - HEAD_W) / self.pps

    def vis_secs(self):
        return max(self.tl.winfo_width() - HEAD_W, 1) / self.pps

    def pill_box(self, y0, y1):
        mid, r = (y0 + y1) // 2, min(10, (y1 - y0 - 6) // 2)
        return HEAD_W - 36, mid - r, HEAD_W - 8, mid + r

    def clip_at(self, x, y):
        r = self.row_at(y)
        if not r or x < HEAD_W:
            return None, None
        # exact hit first, so at a cut the clip under the mouse wins over its neighbour
        for slack in (0, 2):
            for c in reversed(self.clips):
                if (c["kind"], c["track"]) != r[:2]:
                    continue
                x0, x1 = self.t2x(c["start"]), self.t2x(c["start"] + c["dur"])
                if x0 - slack <= x <= x1 + slack:
                    edge = min(EDGE, (x1 - x0) / 4)
                    if x - x0 <= edge:
                        return c, "left"
                    if x1 - x <= edge:
                        return c, "right"
                    return c, "move"
        return None, None

    # ---------- timeline drawing ----------
    def draw_timeline_soon(self):
        """For mouse moves: one redraw when Tk is idle, however many events came in."""
        if not self.tl_job:
            self.tl_job = self.root.after_idle(self.draw_timeline)

    def draw_timeline(self):
        if self.tl_job:
            self.root.after_cancel(self.tl_job)
            self.tl_job = None
        c = self.tl
        c.delete("all")
        w, h = max(c.winfo_width(), 1), max(c.winfo_height(), 1)
        self.yoff = min(max(self.yoff, 0.0), self.max_yoff())
        rows = self.rows()
        for kind, i, y0, y1 in rows:
            c.create_line(HEAD_W, y1 - 1, w, y1 - 1, fill=BORDER)
        c.create_line(HEAD_W, rows[len(self.vtracks) - 1][3] - 1, w,
                      rows[len(self.vtracks) - 1][3] - 1, fill=MUTED)

        for cl in self.clips:
            x0, x1 = self.t2x(cl["start"]), self.t2x(cl["start"] + cl["dur"])
            if x1 < HEAD_W or x0 > w:
                continue
            _, _, y0, y1 = self.row_of(cl["kind"], cl["track"])
            x0 = max(x0, HEAD_W)
            video = cl["kind"] == "v"
            caption = "caption" in cl
            body, dark = ((CLIP_C, CLIP_C_D) if caption else (CLIP_V, CLIP_V_D) if video
                          else (CLIP_A, CLIP_A_D))
            tr = self.tracks(cl["kind"])[cl["track"]]
            off = tr.get("hidden") or tr.get("muted")
            sel = cl["id"] in self.sel
            X0 = self.t2x(cl["start"])
            cy0, cy1 = y0 + 2, y1 - 3
            c.create_rectangle(x0 + 1, cy0, x1 - 1, cy1, fill=body, outline="")
            name_h = 14 if cy1 - cy0 >= 30 and not caption else 0
            if caption:
                pass   # the text is the label
            elif video:
                self.draw_thumbs(cl, X0, x0 + 1, min(x1 - 1, w), cy0, cy1 - name_h)
            else:
                self.draw_wave(cl, X0, x0 + 1, min(x1 - 1, w), cy0, cy1 - name_h)
            for fade, a, b in (("fade_in", cl["start"], cl["start"] + cp(cl, "fade_in")),
                               ("fade_out", cl["start"] + cl["dur"],
                                cl["start"] + cl["dur"] - cp(cl, "fade_out"))):
                if cp(cl, fade):
                    xa, xb = self.t2x(a), self.t2x(b)
                    c.create_polygon(xa, cy0, xb, cy0, xa, cy1, fill="#000000", stipple="gray50",
                                     outline="")
            if name_h:
                c.create_rectangle(x0 + 1, cy1 - name_h, x1 - 1, cy1, fill=dark, outline="")
            if off:
                c.create_rectangle(x0 + 1, cy0, x1 - 1, cy1, fill="#000000", stipple="gray50",
                                   outline="")
            c.create_rectangle(x0 + 1, cy0, x1 - 1, cy1, outline="white" if sel else dark,
                               width=2 if sel else 1)
            if self.hover and self.hover[0] == cl["id"]:
                # trim handles: drag either end to make the clip shorter / longer
                for side, hx in (("left", x0 + 1), ("right", x1 - 6)):
                    if side == "left" and x0 > self.t2x(cl["start"]) + 1:
                        continue
                    hot = self.hover[1] == side
                    c.create_rectangle(hx, y0 + 2, hx + 5, y1 - 3, outline="",
                                       fill="#ffcc00" if hot else "#d8d8e0")
            room = int((x1 - x0 - 12) / 6.5)
            if room > 2 and y1 - y0 >= 20:
                label = f"{Path(cl['path']).name}   {fmt_time(cl['dur'])}"
                if caption:
                    label = cl["caption"].replace("\n", " ")
                if cp(cl, "speed") != 100:
                    label += f"   {cp(cl, 'speed'):.0f}%"
                if cl["link"]:
                    label = "🔗 " + label
                if len(label) > room:
                    label = label[:max(room - 1, 1)] + "…"
                c.create_text(x0 + 6, cy1 - (name_h or cy1 - cy0) / 2, anchor="w", text=label,
                              fill="white", font=SMALL_FONT)

        self.draw_drop(rows)
        self.refresh_inspector()

        c.create_rectangle(0, 0, HEAD_W, h, fill=HEAD_BG, outline="", tags="hdrbase")
        for kind, i, y0, y1 in rows:
            tr = self.tracks(kind)[i]
            mid = (y0 + y1) // 2
            c.create_line(0, y1 - 1, HEAD_W, y1 - 1, fill=BORDER)
            tall = y1 - y0 >= 34
            c.create_text(10, mid - 7 if tall else mid, anchor="w", text=f"{kind.upper()}{i + 1}",
                          fill=FG, font=("Segoe UI Semibold", 10))
            if tall:
                n = sum(1 for cl in self.clips if (cl["kind"], cl["track"]) == (kind, i))
                c.create_text(10, mid + 8, anchor="w", text=f"{n} clip{'' if n == 1 else 's'}",
                              fill=MUTED, font=SMALL_FONT)
            px0, py0, px1, py1 = self.pill_box(y0, y1)
            if kind == "v":
                off = tr["hidden"]
                fill, icon = ("#5a2a2a", ICON["hide"]) if off else (FIELD, ICON["eye"])
            else:
                off = tr["muted"]
                fill, icon = ("#b04040", ICON["mute"]) if off else (FIELD, ICON["vol"])
            c.create_rectangle(px0, py0, px1, py1, fill=fill, outline=BORDER)
            c.create_text((px0 + px1) / 2, (py0 + py1) / 2, text=icon, fill=FG, font=(ICON_FONT, 9))
        c.create_line(HEAD_W, 0, HEAD_W, h, fill=BORDER)

        # ruler last, so tracks scrolled up slide under it
        c.create_rectangle(0, 0, w, RULER_H, fill=BG, outline="")
        step = next((s for s in TICKS if s * self.pps >= 80), TICKS[-1])
        k = math.floor(self.view_t / step)
        while True:
            t = k * step
            x = self.t2x(t)
            if x > w:
                break
            if x >= HEAD_W:
                c.create_line(x, RULER_H - 8, x, RULER_H, fill=MUTED)
                c.create_text(x + 3, 2, anchor="nw", text=tc(t, step), fill=MUTED, font=SMALL_FONT)
            xm = self.t2x(t + step / 2)
            if HEAD_W <= xm <= w:
                c.create_line(xm, RULER_H - 4, xm, RULER_H, fill=BORDER)
            k += 1
        if self.clips and (self.mark_in is not None or self.mark_out is not None):
            rs, re = self.render_range()
            xa, xb = max(self.t2x(rs), HEAD_W), min(self.t2x(re), w)
            if xb > xa:
                c.create_rectangle(xa, RULER_H - 5, xb, RULER_H, fill=ACCENT, outline="")
        c.create_rectangle(0, 0, HEAD_W, RULER_H, fill=HEAD_BG, outline="")
        c.create_line(0, RULER_H - 1, w, RULER_H - 1, fill=BORDER)
        c.create_text(10, RULER_H // 2, anchor="w", text=fmt_time(self.cur), fill=FG,
                      font=("Consolas", 10), tags="tc")
        self.draw_playhead()
        self.update_scrollbar()

    def draw_thumbs(self, cl, X0, x0, x1, y0, y1):
        """Filmstrip: tiles anchored to the clip start, each showing the frame under its middle."""
        info = self.thumbs.get(cl["path"])
        th = int(y1 - y0)
        if not info or not info[1] or th < 14:
            return
        step, imgs = info
        tw = max(8, round(th * imgs[0].width / imgs[0].height))
        x = X0 + max(0, int((x0 - X0) // tw)) * tw
        while x < x1:
            if x + tw <= x0:
                x += tw
                continue
            t = cl["inp"] + (x + tw / 2 - X0) / self.pps * spd(cl)
            idx = 0 if not step else min(len(imgs) - 1, max(0, int(t / step)))
            left, right = max(0, int(x0 - x)), int(min(tw, x1 - x))
            if right - left >= 2:
                key = (cl["path"], idx, tw, th, left, right)
                ph = self.tiles.get(key)
                if ph is None:
                    if len(self.tiles) > 800:
                        self.tiles.clear()
                    im = imgs[idx].resize((tw, th), Image.BILINEAR).crop((left, 0, right, th))
                    ph = self.tiles[key] = ImageTk.PhotoImage(im)
                self.tl.create_image(x + left, y0, image=ph, anchor="nw")
            x += tw

    def draw_wave(self, cl, X0, x0, x1, y0, y1):
        """Mirrored waveform, scaled by the clip's volume."""
        pk = self.peaks.get(cl["path"])
        if not pk or y1 - y0 < 8:
            return
        mid, half = (y0 + y1) / 2, (y1 - y0) / 2 - 1
        gain = 10 ** (cp(cl, "volume") / 20)
        k = spd(cl)
        top, bottom = [], []
        for x in range(int(x0), int(x1) + 1, 2):
            ta = cl["inp"] + (x - X0) / self.pps * k
            i0 = max(0, int(ta * PEAKS_PER_SEC))
            i1 = max(i0 + 1, int((ta + 2 / self.pps * k) * PEAKS_PER_SEC))
            v = min(1.0, max(pk[i0:i1], default=0) / 255 * gain) * half
            top += (x, mid - v - 0.5)
            bottom[:0] = (x, mid + v + 0.5)
        if len(top) >= 4:
            self.tl.create_polygon(*top, *bottom, fill="#86e0ad", outline="")

    def draw_drop(self, rows):
        """Where dragged-in files will land: one ghost clip per file and track, plus an insert line."""
        if not (self.drop and self.drop.get("target")):
            return
        c = self.tl
        t, track = self.drop["target"]
        c.create_line(self.t2x(t), RULER_H, self.t2x(t), c.winfo_height(), fill=ACCENT, width=2,
                      tags="drop")
        for path in self.drop["paths"]:
            m = self.media.get(path)
            dur = self.drop_dur(path)
            kinds = ([k for k, has in (("v", m["video"]), ("a", m["audio"])) if has] if m
                     else [self.drop["kind"]])
            for kind in kinds:
                if track < len(self.tracks(kind)):
                    _, _, y0, y1 = self.row_of(kind, track)
                elif kind == "v":   # a new track will be added above the top one
                    y1 = rows[0][2]
                    y0 = y1 - VT_H
                else:               # ... or below the last audio track
                    y0 = rows[-1][3]
                    y1 = y0 + AT_H
                x0, x1 = self.t2x(t), self.t2x(t + dur)
                c.create_rectangle(x0 + 1, y0 + 2, x1 - 1, y1 - 3, fill=ACCENT, stipple="gray25",
                                   outline=ACCENT, width=2, dash=(4, 2), tags="drop")
                if x1 - x0 > 30 and y1 - y0 >= 20:
                    c.create_text(x0 + 8, y0 + 5, anchor="nw", fill="white", font=SMALL_FONT,
                                  text=Path(path).name if m else Path(path).name + "  …",
                                  tags="drop")
            t += dur

    def draw_drop_layer(self):
        """Redraws only the ghost clips of a drag from Explorer (under the track headers and the
        ruler, like draw_timeline puts them), so dragging over the window stays smooth."""
        self.tl.delete("drop")
        self.draw_drop(self.rows())
        if self.tl.find_withtag("hdrbase"):
            self.tl.tag_lower("drop", "hdrbase")

    def draw_playhead(self):
        c = self.tl
        c.delete("ph")
        c.itemconfig("tc", text=fmt_time(self.cur))
        x = self.t2x(self.cur)
        if not self.clips or x < HEAD_W:
            return
        c.create_line(x, RULER_H - 4, x, c.winfo_height(), fill=PLAYHEAD, width=2, tags="ph")
        c.create_polygon(x - 9, 1, x + 9, 1, x + 9, RULER_H - 9, x, RULER_H, x - 9, RULER_H - 9,
                         fill=PLAYHEAD, outline="#ffffff", width=1, tags="ph")

    def follow_playhead(self):
        x = self.t2x(self.cur)
        if x < HEAD_W or x > self.tl.winfo_width() - 20:
            self.view_t = max(0.0, self.cur - self.vis_secs() * 0.1)
            self.draw_timeline()
        else:
            self.draw_playhead()

    def scroll_total(self):
        return max(self.tl_end(), self.view_t, self.cur) + self.vis_secs()

    def update_scrollbar(self):
        total = self.scroll_total()
        self.hs.set(self.view_t / total, (self.view_t + self.vis_secs()) / total)
        vis = max(self.tl.winfo_height() - RULER_H, 1)
        content = vis + self.max_yoff()
        self.vs.set(self.yoff / content, (self.yoff + vis) / content)

    def on_vscroll(self, *args):
        vis = max(self.tl.winfo_height() - RULER_H, 1)
        if args[0] == "moveto":
            self.yoff = float(args[1]) * (vis + self.max_yoff())
        elif args[0] == "scroll":
            self.yoff += int(args[1]) * (AT_H if args[2] == "units" else vis * 0.9)
        self.draw_timeline_soon()

    def on_hscroll(self, *args):
        vis = self.vis_secs()
        if args[0] == "moveto":
            self.view_t = float(args[1]) * self.scroll_total()
        elif args[0] == "scroll":
            self.view_t += int(args[1]) * vis * (0.1 if args[2] == "units" else 0.9)
        self.view_t = max(0.0, self.view_t)
        self.draw_timeline_soon()

    def zoom(self, f, x=None):
        x = x if x is not None else (self.t2x(self.cur) if self.clips else HEAD_W)
        t = self.x2t(x)
        self.pps = min(max(self.pps * f, 1.0), 600.0)
        self.view_t = max(0.0, t - (x - HEAD_W) / self.pps)
        self.draw_timeline()

    def zoom_fit(self):
        self.pps = min(max((self.tl.winfo_width() - HEAD_W - 30) / max(self.tl_end(), 1.0), 1.0),
                       600.0)
        self.view_t = 0.0
        self.draw_timeline()

    def on_tl_wheel(self, e):
        if e.state & 0x0004:
            self.zoom(1.25 if e.delta > 0 else 0.8, max(e.x, HEAD_W))
        elif e.state & 0x0001 or e.x < HEAD_W:
            self.yoff -= e.delta / 120 * AT_H
            self.draw_timeline_soon()
        else:
            self.view_t = max(0.0, self.view_t - e.delta / 120 * 60 / self.pps)
            self.draw_timeline_soon()

    # ---------- timeline mouse ----------
    def on_tl_hover(self, e):
        if self.tdrag:
            return
        cursor, hover = "", None
        if self.height_edge(e.x, e.y):
            cursor = "sb_v_double_arrow"
        elif e.y >= RULER_H and e.x < HEAD_W:
            r = self.row_at(e.y)
            if r:
                px0, py0, px1, py1 = self.pill_box(r[2], r[3])
                cursor = "hand2" if px0 <= e.x <= px1 and py0 <= e.y <= py1 else ""
        elif e.y < RULER_H:
            cursor = "hand2"
        else:
            cl, part = self.clip_at(e.x, e.y)
            cursor = {"left": "sb_h_double_arrow", "right": "sb_h_double_arrow",
                      "move": "fleur"}.get(part, "")
            hover = cl and (cl["id"], part)
        self.tl.config(cursor=cursor)
        self.set_hover(hover)

    def set_hover(self, hover):
        if hover != self.hover and not self.tdrag:
            self.hover = hover
            self.draw_timeline_soon()

    def on_tl_press(self, e):
        self.root.focus_set()
        self.tdrag = None
        r = self.height_edge(e.x, e.y)
        if r:
            self.tdrag = {"mode": "height", "kind": r[0], "i": r[1], "y": e.y,
                          "h0": r[3] - r[2]}
            return
        if not self.clips:
            return
        if e.y < RULER_H:
            if e.x >= HEAD_W:
                self.resume = self.player is not None
                self.pause()
                self.tdrag = {"mode": "scrub"}
                self.seek(self.scrub_t(e))
            return
        if e.x < HEAD_W:
            r = self.row_at(e.y)
            if r:
                px0, py0, px1, py1 = self.pill_box(r[2], r[3])
                if px0 <= e.x <= px1 and py0 <= e.y <= py1:
                    tr = self.tracks(r[0])[r[1]]
                    key = "hidden" if r[0] == "v" else "muted"
                    tr[key] = not tr[key]
                    self.restart_if_playing()
                    self.after_edit()
            return
        cl, part = self.clip_at(e.x, e.y)
        additive = e.state & 0x0005
        if not cl:
            if not additive:
                self.sel.clear()
            self.resume = self.player is not None
            self.pause()
            self.tdrag = {"mode": "scrub"}
            self.seek(self.scrub_t(e))
            self.draw_timeline()
            return
        if cl["id"] != self.insp_focus:
            self.insp_commit()   # edits to the previous clip are their own undo step
        self.insp_focus, self.insp_kind = cl["id"], self.ikind(cl)
        self.root.after_idle(self.prefetch_sources)
        group = {cl["id"]} if e.state & 0x20000 else self.linked(cl)
        if additive:
            if cl["id"] in self.sel:
                self.sel -= group
                self.draw_timeline()
                return
            self.sel |= group
        elif cl["id"] not in self.sel:
            self.sel = set(group)
        self.draw_timeline()
        self.tdrag = {"mode": part, "id": cl["id"], "x": e.x, "y": e.y, "moved": False,
                      "before": self.snapshot(),
                      "orig": {i: dict(self.by_id(i)) for i in self.sel}}

    def scrub_t(self, e):
        """Playhead time under the mouse; with snapping on it jumps onto cuts (clip starts /
        ends) and the in / out marks within a few pixels. Shift = no snapping."""
        t = max(self.x2t(e.x), 0.0)
        if not self.snap.get() or e.state & 0x0001:
            return t
        pts = [0.0] + [m for m in (self.mark_in, self.mark_out) if m is not None]
        for c in self.clips:
            pts += [c["start"], c["start"] + c["dur"]]
        best = min(pts, key=lambda p: abs(p - t))
        return best if abs(best - t) * self.pps <= SNAP_PX else t

    def snap_points(self, skip):
        pts = [0.0, self.cur]
        pts += [m for m in (self.mark_in, self.mark_out) if m is not None]
        for c in self.clips:
            if c["id"] not in skip:
                pts += [c["start"], c["start"] + c["dur"]]
        return pts

    def snap_delta(self, edges, skip):
        if not self.snap.get():
            return 0.0
        best = None
        for p in self.snap_points(skip):
            for edge in edges:
                d = p - edge
                if abs(d) * self.pps <= SNAP_PX and (best is None or abs(d) < abs(best)):
                    best = d
        return best or 0.0

    def on_tl_drag(self, e):
        d = self.tdrag
        if not d:
            return
        if d["mode"] == "scrub":
            self.seek(self.scrub_t(e))
            return
        if d["mode"] == "height":
            h = min(max(d["h0"] + e.y - d["y"], TRACK_MIN_H), TRACK_MAX_H)
            self.tracks(d["kind"])[d["i"]]["h"] = h
            self.draw_timeline_soon()
            return
        if not d["moved"] and abs(e.x - d["x"]) < 3 and abs(e.y - d["y"]) < 3:
            return
        if not d["moved"]:
            d["moved"] = True
            self.pause()
        dt = (e.x - d["x"]) / self.pps
        orig = d["orig"]
        me = orig[d["id"]]
        if d["mode"] == "move":
            lo = -min(o["start"] for o in orig.values())
            dt = max(dt, lo)
            dt += self.snap_delta((me["start"] + dt, me["start"] + me["dur"] + dt), orig)
            dt = max(dt, lo)
            r = self.row_at(e.y)
            dtrack = r[1] - me["track"] if r and r[0] == me["kind"] else 0
            n = len(self.tracks(me["kind"]))
            if any(not 0 <= o["track"] + dtrack < n for o in orig.values() if o["kind"] == me["kind"]):
                dtrack = 0
            for i, o in orig.items():
                c = self.by_id(i)
                c["start"] = o["start"] + dt
                c["track"] = o["track"] + (dtrack if o["kind"] == me["kind"] else 0)
        else:
            ids = [i for i in self.linked(self.by_id(d["id"])) if i in orig] or [d["id"]]
            lo, hi = -1e9, 1e9
            for i in ids:
                o = orig[i]
                image = self.media[o["path"]]["image"]
                if d["mode"] == "left":
                    hi = min(hi, o["dur"] - MIN_CLIP)
                    lo = max(lo, -o["start"], -1e9 if image else -o["inp"] / spd(o))
                else:
                    lo = max(lo, MIN_CLIP - o["dur"])
                    if not image:
                        hi = min(hi, (self.media[o["path"]]["dur"] - o["inp"]) / spd(o) - o["dur"])
            edge = me["start"] if d["mode"] == "left" else me["start"] + me["dur"]
            dt += self.snap_delta((edge + dt,), orig)
            dt = min(max(dt, lo), hi)
            for i in ids:
                o, c = orig[i], self.by_id(i)
                if d["mode"] == "left":
                    c["start"], c["inp"], c["dur"] = (o["start"] + dt, o["inp"] + dt * spd(o),
                                                      o["dur"] - dt)
                else:
                    c["dur"] = o["dur"] + dt
        self.draw_timeline_soon()

    def on_tl_release(self, e):
        d, self.tdrag = self.tdrag, None
        if not d:
            return
        if d["mode"] == "scrub":
            if self.resume:
                self.resume = False
                self.play()
            return
        if d.get("moved"):
            if d["mode"] in ("left", "right"):   # trimmed by hand: stop stretching it
                for i in d["orig"]:
                    self.by_id(i).pop("fill", None)
            keep = set(d["orig"])
            for i in d["orig"]:
                c = self.by_id(i)
                self.clear_range(c["kind"], c["track"], c["start"], c["start"] + c["dur"], keep)
            self.commit(d["before"])

    def on_tl_right(self, e):
        if e.y < RULER_H or not (self.clips or self.clipboard) and e.x >= HEAD_W:
            return
        menu = tk.Menu(self.root, tearoff=0, bg=FIELD, fg=FG, activebackground=ACCENT,
                       activeforeground="white", bd=0, font=UI_FONT)
        r = self.row_at(e.y)
        if e.x < HEAD_W:
            if not r:
                return
            kind, i = r[0], r[1]
            menu.add_command(label=f"Delete track {kind.upper()}{i + 1}",
                             command=lambda: self.delete_track(kind, i),
                             state="normal" if len(self.tracks(kind)) > 1 else "disabled")
        else:
            cl, _ = self.clip_at(e.x, e.y)
            at = max(self.x2t(e.x), 0.0)
            paste_here = ("normal" if self.clipboard else "disabled")
            if not cl:
                menu.add_command(label=f"Paste here   ({fmt_time(at)})",
                                 command=lambda: self.paste(at), state=paste_here)
                menu.tk_popup(e.x_root, e.y_root)
                return
            if cl["id"] not in self.sel:
                self.select(self.linked(cl))
            menu.add_command(label="Cut   Ctrl+X", command=lambda: self.copy_sel(True))
            menu.add_command(label="Copy   Ctrl+C", command=self.copy_sel)
            menu.add_command(label="Paste at playhead   Ctrl+V", command=self.paste, state=paste_here)
            menu.add_separator()
            menu.add_command(label="Split at playhead   Ctrl+B", command=self.split)
            menu.add_command(label="Delete   Del", command=self.delete_sel)
            menu.add_command(label="Ripple delete   Shift+Del", command=lambda: self.delete_sel(True))
            menu.add_separator()
            menu.add_command(label="Unlink audio / video" if cl["link"] else "Link selected clips",
                             command=self.toggle_link)
            menu.add_command(label="Auto captions…", command=self.auto_captions,
                             state="normal" if self.caption_sources() else "disabled")
            menu.add_separator()
            kind, i = cl["kind"], cl["track"]
            menu.add_command(label=f"Delete track {kind.upper()}{i + 1}",
                             command=lambda: self.delete_track(kind, i),
                             state="normal" if len(self.tracks(kind)) > 1 else "disabled")
        menu.tk_popup(e.x_root, e.y_root)

    # ---------- render graph ----------
    def graph(self, t0, t1, W, H, fps, video=True, audio=True):
        """FFmpeg inputs + filter parts that render the timeline from t0 to t1 at W x H.
        Returns (inputs, parts, video label, audio label, number of inputs)."""
        inputs, parts, n = [], [], 0
        length = t1 - t0
        vout = aout = None

        def spans(kind, i):
            for c in sorted((c for c in self.clips if (c["kind"], c["track"]) == (kind, i)),
                            key=lambda c: c["start"]):
                s, e = max(c["start"], t0), min(c["start"] + c["dur"], t1)
                if e - s >= 0.01:
                    yield c, s, e

        if video:
            parts.append(f"color=c=black:s={W}x{H}:r={fps}:d={length:.3f},format=yuv420p[base]")
            vout = "[base]"
            for ti, tr in enumerate(self.vtracks):
                if tr["hidden"]:
                    continue
                caps = []
                for c, s, e in spans("v", ti):
                    if "caption" in c:   # drawn by one ass filter per track, below
                        caps.append((c, s, e))
                        continue
                    if self.media[c["path"]]["image"]:
                        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{e - s:.3f}"]
                    else:
                        inputs += ["-ss", f"{c['inp'] + (s - c['start']) * spd(c):.3f}",
                                   "-t", f"{(e - s) * spd(c):.3f}"]
                    inputs += ["-i", c["path"]]
                    parts.append(f"[{n}:v]{self.video_chain(c, s, t0, W, H, fps)}[v{n}]")
                    # position is in frame pixels (Y up, like DaVinci); scaled for small previews
                    k = W / max(self.vw, 1)
                    parts.append(f"{vout}[v{n}]overlay=(W-w)/2+{cp(c, 'pos_x') * k:.2f}:"
                                 f"(H-h)/2-{cp(c, 'pos_y') * k:.2f}:eof_action=pass[o{n}]")
                    vout = f"[o{n}]"
                    n += 1
                if caps:
                    ass, fonts = self.write_ass(caps, t0)
                    parts.append(f"{vout}ass=filename='{ff_path(ass)}':fontsdir='{ff_path(fonts)}'"
                                 f"[cap{ti}]")
                    vout = f"[cap{ti}]"
        if audio:
            labels = []
            for ti, tr in enumerate(self.atracks):
                if tr["muted"]:
                    continue
                for c, s, e in spans("a", ti):
                    inputs += ["-ss", f"{c['inp'] + (s - c['start']) * spd(c):.3f}",
                               "-t", f"{(e - s) * spd(c):.3f}", "-i", c["path"]]
                    parts.append(f"[{n}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts="
                                 f"stereo,{self.audio_chain(c, s)}apad,atrim=duration={e - s:.3f},"
                                 f"adelay={int(round((s - t0) * 1000))}:all=1[a{n}]")
                    labels.append(f"[a{n}]")
                    n += 1
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={length:.3f}[asil]")
            aout = "[asil]"
            if labels:
                parts.append(f"[asil]{''.join(labels)}amix=inputs={len(labels) + 1}:"
                             f"duration=first:normalize=0[amix]")
                aout = "[amix]"
        return inputs, parts, vout, aout, n

    def write_ass(self, caps, t0):
        """An .ass file with these caption clips, timed from t0, in frame pixels (libass scales
        it to the render size). Each clip's inspector settings become override tags. Returns
        (file, fonts folder); files are named by their content, so renders running at the same
        time never share a file that changes."""
        fs, bord = caption_style(self.vw, self.vh)
        fonts = CACHE_DIR / "fonts"
        lines, used = [], set()
        for c, s, e in caps:
            face = cap_font(c)
            used.add(face)
            # by full name ("Yu Gothic Medium"), so libass takes that exact face and not another
            # weight of the family; \b0 so it doesn't thicken a bold face again
            family, style = caption_font(fs, face).getname()
            full = family if style in (None, "", "Regular") else f"{family} {style}"
            x, y = self.vw / 2 + cp(c, "pos_x"), self.vh / 2 - cp(c, "pos_y")
            z = cp(c, "zoom") * 100
            base = (f"\\fn{full}\\b0\\an5\\pos({x:.1f},{y:.1f})"
                    f"\\fscx{z:.1f}\\fscy{z:.1f}")
            if cp(c, "rotation"):
                base += f"\\frz{-cp(c, 'rotation'):.2f}"   # libass turns counterclockwise
            if cp(c, "opacity") < 100:
                base += f"\\alpha&H{round(255 * (1 - cp(c, 'opacity') / 100)):02X}&"
            fi, fo = cp(c, "fade_in"), cp(c, "fade_out")
            rows = [[w.replace("{", "(").replace("}", ")") for w in line.split(" ")]
                    for line in caption_lines(c["caption"], self.vw, self.vh, face)]
            # spoken-word highlight: one event per word, from its start to the next one's
            spans = [(s, e, None)]
            if cp(c, "highlight"):
                cuts = [c["start"] + a - c["inp"] for a, _, _ in caption_words(c)]
                spans, prev = [], s
                for i, a in enumerate(cuts + [e]):
                    a = min(max(a, s), e)
                    if a > prev + 1e-3:   # a cut before s just moves the active word on
                        spans.append((prev, a, i - 1 if i else None))
                        prev = a
            hl = ass_color(HL_COLORS.get(cp(c, "hl_color"), HL_COLORS["Yellow"]))
            for a, b, active in spans:
                tags = base
                if fi or fo:   # times from this event's start, which may be inside the clip
                    t1 = -round((a - c["start"]) * 1000)
                    d = round(c["dur"] * 1000)
                    tags += (f"\\fade(255,0,255,{t1},{t1 + round(fi * 1000)},"
                             f"{t1 + d - round(fo * 1000)},{t1 + d})")
                out, n = [], 0
                for row in rows:
                    words = []
                    for w in row:
                        words.append(f"{{\\c{hl}}}{w}{{\\c&HFFFFFF&}}" if n == active else w)
                        n += 1
                    out.append(" ".join(words))
                lines.append(f"Dialogue: 0,{ass_time(a - t0)},{ass_time(b - t0)},Cap,,0,0,0,,"
                             f"{{{tags}}}" + "\\N".join(out))
        data = ("[Script Info]\nScriptType: v4.00+\n"
                f"PlayResX: {self.vw}\nPlayResY: {self.vh}\nWrapStyle: 2\n"
                "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
                "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
                "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                f"Style: Cap,Arial,{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
                f"-1,0,0,0,100,100,0,0,1,{bord},0,5,0,0,0,1\n\n[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                + "\n".join(lines) + "\n")
        path = CACHE_DIR / f"cap_{hashlib.md5(data.encode('utf-8')).hexdigest()[:16]}.ass"
        try:
            for face in used:   # only the fonts in use, so libass starts fast
                src = Path(installed_fonts().get(face, ("",))[0])
                if src.name and not (fonts / src.name).exists():
                    fonts.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, fonts / src.name)
            if not path.exists():
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
                tmp.write_text(data, encoding="utf-8")
                os.replace(tmp, path)
        except OSError:
            pass
        return path, fonts

    def video_chain(self, c, s, t0, W, H, fps):
        """Filters for one visual clip: crop, flip, fit * zoom, rotate, opacity, fades, timing.
        The rgba path is only taken when a setting needs alpha, to keep playback fast."""
        f = []
        if abs(spd(c) - 1) > 1e-6:   # speed: file time -> timeline time
            f.append(f"setpts=(PTS-STARTPTS)/{spd(c):.5f}")
        cl, cr, ct, cb = (cp(c, k) / 100 for k in ("crop_l", "crop_r", "crop_t", "crop_b"))
        if cl or cr or ct or cb:
            f.append(f"crop=w=iw*{max(1 - cl - cr, 0.02):.4f}:h=ih*{max(1 - ct - cb, 0.02):.4f}:"
                     f"x=iw*{cl:.4f}:y=ih*{ct:.4f}")
        if cp(c, "flip_h"):
            f.append("hflip")
        if cp(c, "flip_v"):
            f.append("vflip")
        z = cp(c, "zoom")
        f.append(f"fps={fps},scale={max(2, round(W * z))}:{max(2, round(H * z))}:"
                 f"force_original_aspect_ratio=decrease:force_divisible_by=2")
        rot, op = math.radians(cp(c, "rotation")), cp(c, "opacity") / 100
        fi, fo = cp(c, "fade_in"), cp(c, "fade_out")
        if rot or op < 1 or fi or fo:
            f.append("format=rgba")
            if rot:
                f.append(f"rotate=a={rot:.5f}:c=none:ow=rotw({rot:.5f}):oh=roth({rot:.5f})")
            if op < 1:
                f.append(f"colorchannelmixer=aa={op:.3f}")
            if fi or fo:   # fades run on clip time (fade can't start before 0)
                f.append(f"setpts=PTS-STARTPTS+{s - c['start']:.3f}/TB")
            if fi:
                f.append(f"fade=t=in:st=0:d={fi:.3f}:alpha=1")
            if fo:
                f.append(f"fade=t=out:st={c['dur'] - fo:.3f}:d={fo:.3f}:alpha=1")
        else:
            f.append("format=yuva420p")
        f.append(f"setpts=PTS-STARTPTS+{s - t0:.3f}/TB")
        return ",".join(f)

    def audio_chain(self, c, s):
        """Speed, volume, pan and fades for one audio clip ('' or filters ending in a comma)."""
        f = speed_filters(c)
        if cp(c, "volume"):
            f.append(f"volume={cp(c, 'volume'):.2f}dB")
        pan = cp(c, "pan")
        if pan:
            f.append(f"pan=stereo|c0={min(1.0, 1 - pan):.3f}*c0|c1={min(1.0, 1 + pan):.3f}*c1")
        fi, fo = cp(c, "fade_in"), cp(c, "fade_out")
        if fi or fo:   # on clip time, then back to 0 so adelay / amix line up as before
            f.append(f"asetpts=PTS-STARTPTS+{s - c['start']:.3f}/TB")
            if fi:
                f.append(f"afade=t=in:st=0:d={fi:.3f}")
            if fo:
                f.append(f"afade=t=out:st={c['dur'] - fo:.3f}:d={fo:.3f}")
            f.append("asetpts=PTS-STARTPTS")
        return "".join(x + "," for x in f)

    def fps_str(self, fps):
        return f"{fps:.3f}".rstrip("0").rstrip(".")

    def preview_size(self):
        self.layout()
        return max(2, self.dw // 2 * 2), max(2, self.dh // 2 * 2)

    # ---------- still frames ----------
    def schedule_frame(self, delay=60):
        if not self.clips or self.player:
            return
        if self.after_id:
            self.root.after_cancel(self.after_id)
        self.after_id = self.root.after(delay, self.fetch_frame)

    # ---------- live preview ----------
    def visible_layers(self, t):
        """Visual clips under time t, bottom track first: what graph() overlays there."""
        return [c for ti, tr in enumerate(self.vtracks) if not tr["hidden"]
                for c in self.clips if c["kind"] == "v" and c["track"] == ti
                and c["start"] <= t < c["start"] + c["dur"]]

    def src_key(self, c, t):
        image = self.media[c["path"]]["image"]
        return c["path"], 0.0 if image else round(c["inp"] + (t - c["start"]) * spd(c), 3)

    def live_preview(self):
        """Composites the preview frame in memory from cached source frames, following graph():
        crop, flip, fit * zoom, rotate, opacity and fades, then overlay at the position. Takes a
        few ms, so sliders update while they move. Returns False while a source frame loads."""
        if not self.clips or self.player:
            return False
        t = self.frame_t()
        W, H = self.preview_size()
        layers = self.visible_layers(t)
        missing = [c for c in layers if "caption" not in c and self.src_key(c, t) not in self.src_cache]
        if missing:
            self.live_wanted = True
            self.fetch_sources(missing, t, W, H)
            return False
        self.live_wanted = False
        out = Image.new("RGB", (W, H), "black")
        k = W / max(self.vw, 1)
        for c in layers:
            if "caption" in c:   # drawn with the export's font, size and outline
                im = caption_image(c["caption"], self.vw, self.vh, k * cp(c, "zoom"),
                                   active_word(c, t) if cp(c, "highlight") else None,
                                   HL_COLORS.get(cp(c, "hl_color"), HL_COLORS["Yellow"]),
                                   cap_font(c))
            else:
                im = self.src_cache[self.src_key(c, t)]
                cl, cr, ct, cb = (cp(c, x) / 100 for x in ("crop_l", "crop_r", "crop_t", "crop_b"))
                if cl or cr or ct or cb:
                    iw, ih = im.size
                    x0, y0 = round(iw * cl), round(ih * ct)
                    im = im.crop((x0, y0, max(x0 + 1, round(iw * (1 - cr))),
                                  max(y0 + 1, round(ih * (1 - cb)))))
                if cp(c, "flip_h"):
                    im = im.transpose(Image.FLIP_LEFT_RIGHT)
                if cp(c, "flip_v"):
                    im = im.transpose(Image.FLIP_TOP_BOTTOM)
                z = cp(c, "zoom")
                r = min(W * z / im.width, H * z / im.height)
                im = im.resize((max(2, round(im.width * r) // 2 * 2),
                                max(2, round(im.height * r) // 2 * 2)), Image.BILINEAR)
            local = t - c["start"]
            alpha = cp(c, "opacity") / 100
            fi, fo = cp(c, "fade_in"), cp(c, "fade_out")
            if fi and local < fi:
                alpha *= local / fi
            if fo and local > c["dur"] - fo:
                alpha *= max(0.0, (c["dur"] - local) / fo)
            rot, mask = cp(c, "rotation"), None
            if rot or alpha < 1:
                im = im.convert("RGBA")
                if rot:   # FFmpeg's rotate turns clockwise for positive angles, PIL the other way
                    im = im.rotate(-rot, resample=Image.BILINEAR, expand=True)
                if alpha < 1:
                    im.putalpha(im.getchannel("A").point(lambda v: int(v * alpha)))
                mask = im
            elif im.mode == "RGBA":
                mask = im
            out.paste(im, (round((W - im.width) / 2 + cp(c, "pos_x") * k),
                           round((H - im.height) / 2 - cp(c, "pos_y") * k)), mask)
        self.req += 1   # an FFmpeg frame still on its way has the old settings; drop it
        self.frame = out
        self.redraw()
        return True

    def fetch_sources(self, clips, t, W, H):
        """Decodes the untouched source frame of each clip at t (up to 2x the preview, so
        zooming in stays sharp) in the background."""
        todo = []
        for c in clips:
            if "caption" in c:
                continue
            key = self.src_key(c, t)
            if key not in self.src_pending and key not in self.src_cache:
                self.src_pending.add(key)
                todo.append((key, self.media[c["path"]]["image"]))
        if not todo:
            return
        bw, bh = W * 2, H * 2

        def work():
            for key, image in todo:
                img = None
                if image:
                    try:
                        with Image.open(key[0]) as im:
                            img = im.convert("RGB")
                            img.thumbnail((bw, bh))
                    except OSError:
                        pass
                else:
                    img = run_bmp(["ffmpeg", "-v", "error", "-ss", f"{key[1]:.3f}", "-i", key[0],
                                   "-frames:v", "1", "-vf",
                                   f"scale='min({bw},iw)':'min({bh},ih)':force_original_aspect_ratio="
                                   f"decrease", "-f", "image2pipe", "-c:v", "bmp", "-"])
                self.q.put(("src", key, img))
        threading.Thread(target=work, daemon=True).start()

    def prefetch_sources(self):
        """Warms the cache for the clips under the playhead, so the first slider move is live."""
        if self.clips and not self.player and self.sel:
            t = self.frame_t()
            W, H = self.preview_size()
            self.fetch_sources(self.visible_layers(t), t, W, H)

    def fetch_frame(self):
        self.after_id = None
        if not self.clips:
            return
        self.prefetch_sources()
        self.req += 1
        W, H = self.preview_size()
        t = self.frame_t()
        inputs, parts, vout, _, _ = self.graph(t, t + 0.2, W, H, self.fps_str(self.pfps),
                                               audio=False)
        parts.append(f"{vout}format=rgb24[pv]")
        cmd = (["ffmpeg", "-v", "error"] + inputs +
               ["-filter_complex", ";".join(parts), "-map", "[pv]", "-frames:v", "1",
                "-f", "image2pipe", "-c:v", "bmp", "-"])
        token = self.req
        threading.Thread(target=lambda: self.q.put(("frame", token, run_bmp(cmd))),
                         daemon=True).start()

    def poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "frame":
                    _, token, img = msg
                    if token == self.req and img is not None and not self.player:
                        self.frame = img
                        self.redraw()
                elif kind == "dropinfo":
                    if msg[2]:
                        self.remember_media(msg[1], msg[2])
                    if self.drop:   # the ghost clips get their real length
                        self.draw_drop_layer()
                elif kind in ("thumbs", "peaks"):
                    (self.thumbs if kind == "thumbs" else self.peaks)[msg[1]] = msg[2]
                    self.draw_timeline()
                elif kind == "src":
                    _, key, img = msg
                    self.src_pending.discard(key)
                    if img is not None:
                        self.src_cache[key] = img
                        while len(self.src_cache) > 40:   # oldest first
                            self.src_cache.pop(next(iter(self.src_cache)))
                        if self.live_wanted:
                            self.live_preview()
                elif kind == "encoders":
                    _, codecs, opus = msg
                    if codecs:
                        self.codecs, self.opus_enc = codecs, opus
                    gpus = sorted({c[0].split("(")[1].rstrip(")") for c in codecs
                                   if c[2] in HW_FAMILIES})
                    if not self.export_proc:
                        self.status.config(text="Ready" + (f"   ·   GPU encoding: {', '.join(gpus)}"
                                                           if gpus else "") +
                                           (f"   ·   {self.dnd_off}" if self.dnd_off else ""))
                elif kind == "cap":
                    self.status.config(text=msg[1])
                    if msg[2] is not None:
                        self.prog["value"] = msg[2] * 100
                elif kind == "capclips":
                    self.add_captions(msg[1], msg[2])
                elif kind == "capdone":
                    self.finish_captions(msg[1], msg[2])
                elif kind == "progress":
                    self.prog["value"] = msg[1] * 100
                    self.status.config(text=f"Exporting...  {msg[1]:.0%}")
                elif kind == "done":
                    self.finish_export(msg[1], msg[2])
        except queue.Empty:
            pass
        self.title_tick += 1
        if self.title_tick % 12 == 0:   # about twice a second
            self.update_title()
        self.root.after(40, self.poll)

    # ---------- playback ----------
    def toggle_play(self):
        if self.player:
            self.pause()
        else:
            self.play()

    def play(self):
        if not self.clips or self.player:
            return
        rs, re = self.render_range()
        loop = self.loop.get()
        end = re if loop else self.tl_end()
        if loop and not (rs <= self.cur < re - 0.05):
            self.cur = rs
        if self.cur >= end - 0.05:
            self.cur = 0.0
        if end - self.cur < 0.05:
            return
        W, H = self.preview_size()
        fps = min(self.pfps, PREVIEW_FPS_CAP)
        inputs, parts, vout, _, _ = self.graph(self.cur, end, W, H, self.fps_str(fps), audio=False)
        parts.append(f"{vout}format=rgb24[pv]")
        vcmd = (["ffmpeg", "-v", "error"] + inputs +
                ["-filter_complex", ";".join(parts), "-map", "[pv]",
                 "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
        self.play_end = end
        self.player = Player(vcmd, self.sound_cmd(min(self.cur + SOUND_LEAD, end), end), (W, H),
                             float(self.fps_str(fps)), self.pvol.get())
        self.play_from, self.shown, self.t0 = self.cur, 0, None
        self.play_btn.config(text=ICON["pause"])
        self.tick()

    def sound_cmd(self, start, end):
        """ffmpeg command that mixes the timeline's audio from start to end as WAV, or None."""
        if self.mute.get() or not any(c["kind"] == "a" and not self.atracks[c["track"]]["muted"]
                                      and c["start"] < end and c["start"] + c["dur"] > start
                                      for c in self.clips):
            return None
        inputs, parts, _, aout, _ = self.graph(start, end, 2, 2, "30", video=False)
        return (["ffmpeg", "-v", "error"] + inputs +
                ["-filter_complex", ";".join(parts), "-map", aout, "-f", "wav", "-"])

    def set_pvol(self, v):
        self.pvol.set(round(min(max(v, 0), 100)))
        self.draw_pvol()
        self.on_pvol()

    def draw_pvol(self):
        c, v = self.vol_cv, self.pvol.get()
        c.delete("all")
        x = 7 + 50 * v / 100
        c.create_line(7, 12, 57, 12, fill=BORDER, width=4, capstyle="round")
        if v:
            c.create_line(7, 12, x, 12, fill=ACCENT, width=4, capstyle="round")
        c.create_oval(x - 6, 6, x + 6, 18, fill="white", outline=ACCENT, width=2)

    def on_pvol(self):
        """Volume slider: saved, and applied to the running sound once the slider rests."""
        if self.vol_job:
            self.root.after_cancel(self.vol_job)
        self.vol_job = self.root.after(250, self.apply_pvol)

    def apply_pvol(self):
        self.vol_job = None
        self.settings["preview_volume"] = round(self.pvol.get())
        save_settings(self.settings)
        if self.player:
            # the new sound starts a little ahead, as ffplay needs a moment to open the device
            t = min(self.cur + SOUND_LEAD, self.play_end)
            self.player.set_sound(self.sound_cmd(t, self.play_end), self.pvol.get())

    def pause(self):
        if self.tick_id:
            self.root.after_cancel(self.tick_id)
            self.tick_id = None
        if self.player:
            self.player.stop()
            self.player = None
            self.play_btn.config(text=ICON["play"])
            self.redraw()

    def restart_if_playing(self):
        if self.player:
            self.pause()
            self.play()
        else:
            self.schedule_frame()

    def tick(self):
        self.tick_id = None
        p = self.player
        if not p:
            return
        latest, ended = None, False
        due = 1 if self.t0 is None else int((time.perf_counter() - self.t0) * p.fps) + 1
        while self.shown < due:
            try:
                d = p.frames.get_nowait()
            except queue.Empty:
                break
            if d is None:
                ended = True
                break
            latest = d
            self.shown += 1
            if self.t0 is None:
                self.t0 = p.first_at or time.perf_counter()
        if latest is not None:
            self.frame = Image.frombuffer("RGB", (p.w, p.h), latest, "raw", "RGB", 0, 1)
            self.cur = min(self.tl_end(), self.play_from + (self.shown - 1) / p.fps)
            self.redraw()
            self.follow_playhead()
            self.update_time()
        if ended:
            self.pause()
            if self.loop.get():
                self.cur = self.render_range()[0]
                self.play()
            return
        self.tick_id = self.root.after(5, self.tick)

    def seek(self, t):
        if not self.clips:
            return
        playing = self.player is not None
        if playing:
            self.pause()
        self.cur = max(t, 0.0)   # anywhere, also past the last clip (the preview is black there)
        self.update_time()
        self.draw_playhead()
        if playing:
            self.play()
        else:
            self.schedule_frame()

    def step(self, secs):
        self.seek(self.cur + secs)
        self.follow_playhead()

    # ---------- files ----------
    def open_video(self, path):
        """Starts a new, untitled project with this media file."""
        if self.clips and not messagebox.askyesno(
                "q-edit", "Start a new project with this file?\n"
                               "(Use Import media to add it to the current one.)"):
            return
        if not self.confirm_discard():
            return
        self.reset_project()
        self.add_media([path])
        self.undo_stack.clear()
        self.redo_stack.clear()

    def load_media(self, path):
        if path in self.media:
            return self.media[path]
        self.status.config(text=f"Loading {Path(path).name}...")
        self.root.update_idletasks()
        m = read_media(path)
        if not m:
            messagebox.showerror("Error", f"FFmpeg could not read {Path(path).name}.")
            return None
        self.remember_media(path, m)
        return m

    def remember_media(self, path, m):
        """Caches a probe and reads the clip's thumbnails / waveform in the background."""
        if path in self.media:
            return
        self.media[path] = m

        def work():
            if m["video"]:
                self.q.put(("thumbs", path, extract_thumbs(path, m)))
            if m["audio"]:
                self.q.put(("peaks", path, extract_peaks(path)))
        threading.Thread(target=work, daemon=True).start()

    def add_media(self, paths=None, drop=None):
        """Puts media on the timeline. Without drop: appended at the end of V1/A1 (or the tracks of
        the selected clip). drop = (time, track index): placed there one after another, over
        whatever is in the way, like a drop in any editor; missing tracks are created."""
        if paths is None:
            paths = filedialog.askopenfilenames(initialdir=self.last_dir, filetypes=MEDIA_TYPES)
        if not paths:
            return
        self.pause()
        before = self.snapshot()
        was_empty = not self.clips
        chosen = [c for c in self.clips if c["id"] in self.sel]
        vt = next((c["track"] for c in chosen if c["kind"] == "v"), 0)
        at = next((c["track"] for c in chosen if c["kind"] == "a"), 0)
        if drop:
            vt = at = drop[1]
            cursor = 0.0 if was_empty else max(drop[0], 0.0)
        added = []
        for path in paths:
            m = self.load_media(path)
            if not m:
                continue
            self.last_dir = str(Path(path).parent)
            if not self.vw and m["video"]:
                img = None if m["image"] else grab_frame(path, 0)
                size = m["size"] if m["image"] else (img.size if img else None)
                if not size:
                    messagebox.showerror("Error", f"FFmpeg could not read {Path(path).name}.")
                    continue
                self.vw, self.vh = size
                if not m["image"]:
                    self.pfps = m["fps"]
                self.crop = [0.0, 0.0, float(self.vw), float(self.vh)]
                self.apply_ratio()

            def end_of(kind, i):
                return max((c["start"] + c["dur"] for c in self.clips
                            if (c["kind"], c["track"]) == (kind, i)), default=0.0)
            dur = IMAGE_DUR if m["image"] else m["dur"]
            if drop:
                start = cursor
                cursor += dur
            else:
                start = max(end_of("v", vt) if m["video"] else 0.0,
                            end_of("a", at) if m["audio"] else 0.0)
            link = self.new_id() if m["video"] and m["audio"] else None
            for kind, track, has in (("v", vt, m["video"]), ("a", at, m["audio"])):
                if has:
                    while len(self.tracks(kind)) <= track:
                        self.tracks(kind).append({"hidden": False} if kind == "v" else {"muted": False})
                    c = {"id": self.new_id(), "kind": kind, "track": track, "path": path,
                         "start": start, "inp": 0.0, "dur": dur, "link": link}
                    if drop:
                        self.clear_range(kind, track, start, start + dur, set(added))
                    self.clips.append(c)
                    added.append(c["id"])
        if not added:
            self.status.config(text="Ready")
            return
        if not self.vw:
            self.vw, self.vh = 1920, 1080
            self.crop = [0.0, 0.0, 1920.0, 1080.0]
            self.apply_ratio()
        self.sel.clear()
        if was_empty:
            self.cur = 0.0
        self.apply_fills()
        self.commit(before)
        if was_empty:
            self.zoom_fit()
        self.status.config(text=f"{len(self.clips)} clips on timeline   ·   {self.vw}x{self.vh}   ·   "
                                f"{self.pfps:.2f} fps   ·   {fmt_time(self.tl_end())}")
        self.redraw()

    # ---------- drag and drop from Explorer ----------
    def setup_dnd(self):
        # said in the status bar, since a missing package otherwise just looks like a broken drop
        self.dnd_off = (f"drag & drop off: run  py -{sys.version_info[0]}.{sys.version_info[1]} -m pip install tkinterdnd2"
                        if TkinterDnD is None else "drag & drop off: tkdnd failed to load")
        if TkinterDnD is None:
            return
        try:
            TkinterDnD._require(self.root)
        except (tk.TclError, RuntimeError):
            return
        self.dnd_off = None
        # every widget in the window takes drops (a plain tk.Tk root has no dnd methods);
        # the mouse position, not the widget, decides what a drop does
        todo = list(self.root.winfo_children())
        while todo:
            w = todo.pop()
            todo += w.winfo_children()
            if isinstance(w, tk.Menu) or not hasattr(w, "drop_target_register"):
                continue
            w.drop_target_register(DND_FILES)
            for ev in ("<<DropEnter>>", "<<DropPosition>>"):
                w.dnd_bind(ev, self.on_drop_move)
            w.dnd_bind("<<DropLeave>>", self.on_drop_leave)
            w.dnd_bind("<<Drop>>", self.on_drop)

    def over_timeline(self, e):
        w = self.root.winfo_containing(e.x_root, e.y_root)
        return w is not None and str(w) == str(self.tl)

    def drop_paths(self, data):
        """Media files in a drop; folders give the media files inside them."""
        out = []
        for p in self.root.tk.splitlist(data or ""):
            p = Path(p)
            if p.is_dir():
                out += sorted(str(f) for f in p.iterdir() if f.suffix.lower() in MEDIA_EXT)
            elif p.suffix.lower() in MEDIA_EXT:
                out.append(str(p))
        return out

    def drop_dur(self, path):
        m = self.media.get(path)
        if not m:
            return 3.0   # still being read
        return IMAGE_DUR if m["image"] else m["dur"]

    def on_drop_move(self, e):
        timeline = self.over_timeline(e)
        if self.export_proc:
            return "refuse_drop"
        if not self.drop or (e.data and e.data != self.drop["key"]):
            paths = self.drop_paths(e.data)
            self.drop = {"key": e.data, "paths": paths, "target": None, "kind": "v"}
            todo = [p for p in paths if p not in self.media]
            if todo:   # read durations in the background so the ghost clips get their real length
                threading.Thread(target=lambda: [self.q.put(("dropinfo", p, read_media(p)))
                                                 for p in todo], daemon=True).start()
        paths = self.drop["paths"]
        # Windows often sends the file list only with the drop itself, so an empty list while
        # dragging is normal; refusing it would block the drop. Refuse only non-media files.
        if e.data and not paths:
            return "refuse_drop"
        # this runs for every mouse move of the drag: only redraw the ghost clips, and only
        # when where they'd land actually changed
        n = (f"{len(paths)} files" if len(paths) > 1 else Path(paths[0]).name) if paths else "files"
        old = (self.drop["target"], self.drop["kind"])
        if not timeline or not self.clips:
            self.drop["target"] = None
            text = f"Drop to add {n} to the end of the timeline"
        else:
            x = e.x_root - self.tl.winfo_rootx()
            y = e.y_root - self.tl.winfo_rooty()
            rows = self.rows()
            r = self.row_at(y) or (rows[0] if y < RULER_H else rows[-1])
            t = max(0.0, self.x2t(max(x, HEAD_W)))
            total = sum(self.drop_dur(p) for p in paths)
            t = max(0.0, t + self.snap_delta((t, t + total), ()))
            t = round(t * self.pps) / self.pps   # whole pixels: no redraw for sub-pixel moves
            self.drop.update(target=(t, r[1]), kind=r[0])
            text = f"Drop to place {n} at {fmt_time(t)} on V{r[1] + 1} / A{r[1] + 1}"
        if self.status.cget("text") != text:
            self.status.config(text=text)
        if (self.drop["target"], self.drop["kind"]) != old:
            self.draw_drop_layer()
        return "copy"

    def on_drop_leave(self, e):
        self.drop = None
        self.status.config(text="Ready")
        self.tl.delete("drop")

    def on_drop(self, e):
        timeline = self.over_timeline(e)
        paths = self.drop_paths(e.data)
        target = self.drop and self.drop.get("target") if timeline else None
        self.drop = None
        self.tl.delete("drop")
        if not paths or self.export_proc:
            return "refuse_drop"
        # add after the drop returns, so Explorer isn't kept waiting while files are read
        self.root.after(10, lambda: self.add_media(paths, drop=target))
        return "copy"

    # ---------- crop ----------
    def get_ratio(self):
        try:
            size = parse_size(self.outsize.get())
        except ValueError:
            size = None
        if size:
            return size[0] / size[1]
        return RATIOS[self.ratio.get()]

    def on_outsize(self):
        try:
            if parse_size(self.outsize.get()):
                self.apply_ratio()
                return
        except ValueError:
            pass
        self.redraw()

    def output_dims(self):
        try:
            size = parse_size(self.outsize.get())
        except ValueError:
            size = None
        if size:
            return size
        return max(2, int(self.crop[2]) // 2 * 2), max(2, int(self.crop[3]) // 2 * 2)

    def apply_ratio(self):
        if not self.vw:
            return
        r = self.get_ratio()
        if r is None:
            self.redraw()
            return
        if self.vw / self.vh > r:
            h = self.vh
            w = h * r
        else:
            w = self.vw
            h = w / r
        self.crop = [(self.vw - w) / 2, (self.vh - h) / 2, w, h]
        self.redraw()

    # ---------- transform on the preview ----------
    def preview_target(self):
        """The selected visual clip under the playhead (it gets the on-screen handles)."""
        if self.player or not self.clips:
            return None
        t = self.frame_t()
        sel = [c for c in self.visible_layers(t) if c["id"] in self.sel]
        if not sel:
            return None
        return next((c for c in sel if c["id"] == self.insp_focus), sel[-1])

    def clip_geom(self, c):
        """(center x, center y, width, height, rotation) of a visual clip in frame pixels,
        placed the way graph() places it."""
        if "caption" in c:
            w, h = caption_image(c["caption"], self.vw, self.vh, 1.0, face=cap_font(c)).size
            z = cp(c, "zoom")
            return (self.vw / 2 + cp(c, "pos_x"), self.vh / 2 - cp(c, "pos_y"), w * z, h * z,
                    cp(c, "rotation"))
        m = self.media.get(c["path"]) or {}
        iw, ih = m.get("size") or (self.vw, self.vh)
        iw *= max(1 - (cp(c, "crop_l") + cp(c, "crop_r")) / 100, 0.02)
        ih *= max(1 - (cp(c, "crop_t") + cp(c, "crop_b")) / 100, 0.02)
        z = cp(c, "zoom")
        r = min(self.vw * z / iw, self.vh * z / ih)
        return (self.vw / 2 + cp(c, "pos_x"), self.vh / 2 - cp(c, "pos_y"), iw * r, ih * r,
                cp(c, "rotation"))

    def clip_corners(self, c):
        cx, cy, w, h, rot = self.clip_geom(c)
        a = math.radians(rot)   # FFmpeg rotates clockwise on screen for positive angles
        ca, sa = math.cos(a), math.sin(a)
        return [self.to_cv(cx + x * ca - y * sa, cy + x * sa + y * ca)
                for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]

    def in_clip(self, c, ex, ey):
        cx, cy, w, h, rot = self.clip_geom(c)
        sx, sy = self.to_src(ex, ey)
        a = -math.radians(rot)
        dx, dy = sx - cx, sy - cy
        x, y = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
        return abs(x) <= w / 2 and abs(y) <= h / 2

    def hit(self, ex, ey):
        """What a click on the preview grabs: the selected clip's corners (scale), the crop
        corners, the topmost clip's picture under the mouse (move), or the crop box's edge
        (move the crop)."""
        tgt = self.preview_target()
        if tgt:
            for px, py in self.clip_corners(tgt):
                if abs(ex - px) <= HANDLE + 3 and abs(ey - py) <= HANDLE + 3:
                    return "scale", tgt
        x, y, w, h = self.crop
        for sx, sy, ax, ay in ((x, y, x + w, y + h), (x + w, y, x, y + h),
                               (x, y + h, x + w, y), (x + w, y + h, x, y)):
            px, py = self.to_cv(sx, sy)
            if abs(ex - px) <= HANDLE + 3 and abs(ey - py) <= HANDLE + 3:
                return "resize", (ax, ay)
        # the topmost clip under the mouse, even when another one is selected: a selected clip
        # underneath (often the full-frame video) must not swallow clicks on what's on top
        if not self.player and self.clips:
            t = self.frame_t()
            for c in reversed(self.visible_layers(t)):
                if self.in_clip(c, ex, ey):
                    return "clip", c
        x1, y1 = self.to_cv(x, y)
        x2, y2 = self.to_cv(x + w, y + h)
        if ((abs(ex - x1) <= 5 or abs(ex - x2) <= 5) and y1 - 5 <= ey <= y2 + 5) or \
                ((abs(ey - y1) <= 5 or abs(ey - y2) <= 5) and x1 - 5 <= ex <= x2 + 5):
            return "move", None
        return None, None

    def on_hover(self, e):
        if not self.vw or self.drag:
            return
        kind, _ = self.hit(e.x, e.y)
        self.cv.config(cursor={"resize": "crosshair", "move": "fleur", "clip": "fleur",
                               "scale": "sizing"}.get(kind, ""))

    def on_press(self, e):
        self.root.focus_set()
        if not self.vw:
            return
        kind, obj = self.hit(e.x, e.y)
        sx, sy = self.to_src(e.x, e.y)
        if kind in ("clip", "scale"):
            c = obj
            if c["id"] not in self.sel:
                self.insp_commit()
                self.sel = set(self.linked(c))
            self.insp_focus, self.insp_kind = c["id"], self.ikind(c)
            self.draw_timeline()
            movers = [x for x in self.clips if x["id"] in self.sel and x["kind"] == "v"]
            cx, cy = self.clip_geom(c)[:2]
            self.drag = {"mode": kind, "sx": sx, "sy": sy, "moved": False, "center": (cx, cy),
                         "d0": max(math.hypot(sx - cx, sy - cy), 1.0), "before": self.snapshot(),
                         "orig": {x["id"]: (cp(x, "pos_x"), cp(x, "pos_y"), cp(x, "zoom"))
                                  for x in movers}}
            self.prefetch_sources()
            self.redraw()
        elif kind:
            self.drag = {"mode": kind, "anchor": obj, "sx": sx, "sy": sy, "crop": list(self.crop)}

    def on_drag(self, e):
        d = self.drag
        if not d:
            return
        sx, sy = self.to_src(e.x, e.y)
        dx, dy = sx - d["sx"], sy - d["sy"]
        if d["mode"] in ("clip", "scale"):
            if not d["moved"] and abs(dx) * self.s < 3 and abs(dy) * self.s < 3:
                return
            d["moved"] = True
            free = e.state & 0x1   # Shift: no snapping
            cx, cy = d["center"]
            for i, (px, py, z) in d["orig"].items():
                c = self.by_id(i)
                if d["mode"] == "clip":
                    nx, ny = px + dx, py - dy
                    if not free:   # snaps to the center lines
                        nx = 0.0 if abs(nx) < 8 / self.s else nx
                        ny = 0.0 if abs(ny) < 8 / self.s else ny
                    self.set_prop(c, "pos_x", round(nx))
                    self.set_prop(c, "pos_y", round(ny))
                else:
                    nz = min(max(z * math.hypot(sx - cx, sy - cy) / d["d0"], 0.05), 8.0)
                    if not free and abs(nz - 1.0) < 0.02:
                        nz = 1.0
                    self.set_prop(c, "zoom", round(nz, 3))
            self.refresh_inspector()
            self.queue_live()
            return
        if d["mode"] == "move":
            oc = d["crop"]
            self.crop[0] = min(max(oc[0] + dx, 0), self.vw - oc[2])
            self.crop[1] = min(max(oc[1] + dy, 0), self.vh - oc[3])
        elif d["mode"] == "resize":
            self.resize_crop(d["anchor"], sx, sy)
        self.redraw()

    def on_release(self, e):
        d, self.drag = self.drag, None
        if d and d["mode"] in ("clip", "scale") and d["moved"]:
            self.commit(d["before"])
            self.refresh_inspector(force=True)
        elif d:
            self.redraw()

    def resize_crop(self, anchor, sx, sy):
        ax, ay = anchor
        sx = min(max(sx, 0), self.vw)
        sy = min(max(sy, 0), self.vh)
        right, down = sx >= ax, sy >= ay
        maxw = self.vw - ax if right else ax
        maxh = self.vh - ay if down else ay
        w, h = abs(sx - ax), abs(sy - ay)
        r = self.get_ratio()
        if r:
            if w / r > h:
                h = w / r
            else:
                w = h * r
            w = max(w, MIN_CROP)
            h = w / r
            if w > maxw:
                w, h = maxw, maxw / r
            if h > maxh:
                h, w = maxh, maxh * r
        else:
            w = min(max(w, MIN_CROP), maxw)
            h = min(max(h, MIN_CROP), maxh)
        self.crop = [ax if right else ax - w, ay if down else ay - h, w, h]

    # ---------- in / out marks ----------
    def set_start(self):
        if not self.clips:
            return
        self.mark_in = self.cur
        if self.mark_out is not None and self.mark_out <= self.cur:
            self.mark_out = None
        self.update_range()
        self.draw_timeline()

    def set_end(self):
        if not self.clips:
            return
        self.mark_out = self.cur
        if self.mark_in is not None and self.mark_in >= self.cur:
            self.mark_in = None
        self.update_range()
        self.draw_timeline()

    def clear_marks(self):
        self.mark_in = self.mark_out = None
        self.update_range()
        self.draw_timeline()

    # ---------- auto captions ----------
    def caption_sources(self):
        """Selected clips with sound to caption: one per linked video / audio pair."""
        out, links = [], set()
        for c in self.clips:
            if c["id"] not in self.sel or "caption" in c:
                continue
            m = self.media.get(c["path"]) or {}
            if not m.get("audio") or m.get("image"):
                continue
            if c["link"]:
                if c["link"] in links:
                    continue
                links.add(c["link"])
            out.append(c)
        return out

    def auto_captions(self):
        clips = self.caption_sources()
        if not clips:
            messagebox.showinfo("Auto captions", "Select a video or audio clip with sound first.")
            return
        if self.cap_proc is not None:
            messagebox.showinfo("Auto captions", "Captions are being made already.")
            return
        if not whisper_exe():
            messagebox.showerror("Auto captions", "whisper-cli was not found. Reinstall q-edit, "
                                 "or put whisper.cpp's whisper-cli.exe on PATH.")
            return
        self.pause()
        CaptionDialog(self, clips)

    def start_captions(self, clips, lang, model, max_chars):
        jobs = [dict(c) for c in clips]
        self.cap_proc = False   # running (a Popen while whisper works)
        self.cap_cancel = False
        self.cap_btn.pack(side="left", padx=(0, 10))
        self.prog["value"] = 0
        threading.Thread(target=self.run_captions, args=(jobs, lang, model, max_chars),
                         daemon=True).start()

    def cancel_captions(self):
        self.cap_cancel = True
        if self.cap_proc:
            try:
                self.cap_proc.kill()
            except OSError:
                pass

    def run_captions(self, jobs, lang, model, max_chars):
        """Background thread: download the model if needed, then per clip: its sound as 16 kHz
        WAV -> whisper-cli (word timestamps as JSON) -> captions on timeline time."""
        def say(text, frac=None):
            self.q.put(("cap", text, frac))
        try:
            path = MODELS_DIR / model
            if not path.exists():
                MODELS_DIR.mkdir(parents=True, exist_ok=True)
                part = path.with_suffix(".part")
                with urllib.request.urlopen(MODEL_URL + model, timeout=30) as r, open(part, "wb") as f:
                    total, got = int(r.headers.get("Content-Length") or 0), 0
                    while True:
                        if self.cap_cancel:
                            raise InterruptedError
                        block = r.read(1 << 20)
                        if not block:
                            break
                        f.write(block)
                        got += len(block)
                        say(f"Downloading the speech model...  {got / 1048576:.0f} / "
                            f"{total / 1048576:.0f} MB", got / total if total else None)
                if total and got != total:
                    raise OSError("the download was cut off")
                os.replace(part, path)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            wav = CACHE_DIR / "captions_audio.wav"
            out = CACHE_DIR / "captions_words"
            for n, c in enumerate(jobs):
                if self.cap_cancel:
                    raise InterruptedError
                name = Path(c["path"]).name
                say(f"Captions {n + 1}/{len(jobs)}: reading the sound of {name}...", 0)
                k = spd(c)
                r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{c['inp']:.3f}",
                                    "-t", f"{c['dur'] * k:.3f}", "-i", c["path"], "-vn", "-ac", "1",
                                    "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
                                   capture_output=True, text=True, creationflags=NOWIN)
                if r.returncode:
                    raise OSError(r.stderr.strip()[-300:] or "FFmpeg could not read the sound")
                say(f"Captions {n + 1}/{len(jobs)}: listening to {name}...", 0)
                try:
                    os.remove(out.with_suffix(".json"))
                except OSError:
                    pass
                # GPU first (the Vulkan backend next to whisper-cli: AMD, NVIDIA, Intel); if
                # that fails (old driver, no GPU memory) the same run goes again on the CPU
                for gpu in ([True, False] if self.cap_gpu is not False else [False]):
                    where = "GPU" if gpu else "CPU"
                    self.cap_proc = subprocess.Popen(
                        [whisper_exe(), "-m", str(path), "-f", str(wav), "-l", lang, "-ojf",
                         "-of", str(out), "-dtw", DTW_PRESETS.get(model, "large.v3.turbo"),
                         "-nfa", "-sns", "-pp",   # DTW word times need flash attention off
                         "-t", str(max(2, (os.cpu_count() or 4) - 2))] + ([] if gpu else ["-ng"]),
                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                        stdin=subprocess.DEVNULL, creationflags=BGWIN, text=True,
                        errors="replace")
                    tail, dev = [], ""
                    for line in self.cap_proc.stderr:
                        if "ggml_vulkan: 0 = " in line:   # the card's name
                            dev = line.split("= ", 1)[1].split(" (")[0].split(" |")[0].strip()
                        if gpu and "whisper_backend_init_gpu: using" in line:
                            where = f"GPU ({dev})" if dev else "GPU"
                        m = re.search(r"progress\s*=\s*(\d+)%", line)
                        if m:
                            say(f"Captions {n + 1}/{len(jobs)}: listening to {name} on the "
                                f"{where}...  {m.group(1)}%", int(m.group(1)) / 100)
                        elif line.strip():
                            tail = (tail + [line.strip()])[-4:]
                    rc = self.cap_proc.wait()
                    if self.cap_cancel:
                        raise InterruptedError
                    if not rc:
                        self.cap_gpu = gpu and where.startswith("GPU")
                        break
                    if gpu:
                        say(f"Captions {n + 1}/{len(jobs)}: the GPU failed, using the CPU...", 0)
                if rc:
                    raise OSError("whisper failed: " + " ".join(tail)[-300:])
                data = json.loads(out.with_suffix(".json").read_text(encoding="utf-8",
                                                                      errors="replace"))
                say(f"Captions {n + 1}/{len(jobs)}: checking the timing of every word...", 1)
                sents = whisper_sentences(data, audio_envelope(wav))
                keep = self.check_sentences(sents, lang, wav, path, say, f"{n + 1}/{len(jobs)}")
                words = []
                for sent, ok in zip(sents, keep):
                    for a, b, w in sent[3] if ok else []:
                        if not re.fullmatch(r"[\[(].*[\])]|[♪♫\s]+", w):   # [BLANK_AUDIO], (music)
                            words.append((c["start"] + a / k, c["start"] + b / k, w))
                end = c["start"] + c["dur"]
                caps = [(a, min(b, end), t, ws) for a, b, t, ws in group_words(words, max_chars)
                        if a < end]
                self.q.put(("capclips", c, caps))
            self.q.put(("capdone", True, ""))
        except InterruptedError:
            self.q.put(("capdone", False, ""))
        except (OSError, ValueError, KeyError) as e:
            self.q.put(("capdone", False, str(e) or e.__class__.__name__))

    def check_sentences(self, sents, lang, wav, model, say, job):
        """Which sentences to keep: not the ones made up over silence, and with a language
        picked, only that language. Each doubtful sentence's own sound is checked by whisper's
        language detection (all in one run), plus what its words look like."""
        keep = [loud for *_, loud in sents]
        if lang not in ("en", "hu"):
            return keep
        other = "hu" if lang == "en" else "en"
        doubt = []
        for i, (a, b, text, ws, loud) in enumerate(sents):
            if not keep[i]:
                continue
            said = text_language(text)
            if said == other:
                keep[i] = False
            elif b - a >= 0.8 and len(ws) >= 2 and (
                    self.cap_gpu or said is None or lang == "en"):
                doubt.append(i)   # on the CPU only the ones the words don't settle
        if not doubt:
            return keep
        say(f"Captions {job}: checking the language of {len(doubt)} sentences...", None)
        files = []
        try:
            with wave.open(str(wav)) as w:
                rate, frames = w.getframerate(), w.readframes(w.getnframes())
            for n, i in enumerate(doubt):
                a, b = sents[i][0], sents[i][1]
                f = CACHE_DIR / f"captions_sentence{n}.wav"
                with wave.open(str(f), "wb") as o:
                    o.setnchannels(1)
                    o.setsampwidth(2)
                    o.setframerate(rate)
                    o.writeframes(frames[int(a * rate) * 2:int(b * rate) * 2])
                files.append(f)
            self.cap_proc = subprocess.Popen(
                [whisper_exe(), "-m", str(model), "-l", "auto", "-dl"] +
                [x for f in files for x in ("-f", str(f))] + ([] if self.cap_gpu else ["-ng"]),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                creationflags=BGWIN, text=True, errors="replace")
            found = re.findall(r"auto-detected language: (\w+) \(p = ([\d.]+)\)",
                               self.cap_proc.communicate()[0])
        except OSError:
            found = []
        if self.cap_cancel:
            raise InterruptedError
        for i, (code, prob) in zip(doubt, found):
            text = sents[i][2]
            hu_text = any(ch in HU_LETTERS for ch in text.lower())
            if code != lang and float(prob) >= (0.7 if lang == "en" else 0.6) and not (
                    lang == "hu" and hu_text):
                keep[i] = False
        return keep

    def caption_media(self):
        """Caption clips point at a small transparent picture, so everything that handles image
        clips (moving, trimming, saving, templates) handles them; the text is drawn by graph()."""
        path = CACHE_DIR / "caption.png"
        if not path.exists():
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(path)
        path = str(path)
        if path not in self.media:
            self.media[path] = {"dur": IMAGE_DUR, "fps": 30.0, "audio": False, "video": True,
                                "image": True, "size": (16, 16)}
            self.thumbs[path] = (0, [])
        return path

    def add_captions(self, src, caps):
        """Puts captions on a new video track at the top (one undo step per clip captioned)."""
        if not caps:
            self.status.config(text=f"No speech found in {Path(src['path']).name}")
            return
        before = self.snapshot()
        if not self.vw:
            self.set_frame((1920, 1080))
        path = self.caption_media()
        self.vtracks.append({"hidden": False})
        track = len(self.vtracks) - 1
        new = []
        for a, b, text, ws in caps:
            c = {"id": self.new_id(), "kind": "v", "track": track, "path": path, "start": a,
                 "inp": 0.0, "dur": max(b - a, MIN_CLIP), "link": None, "caption": text,
                 "pos_y": float(-round(self.vh * 0.27)),
                 "words": [[round(wa - a, 3), round(wb - a, 3), w] for wa, wb, w in ws]}
            new.append(c)
        self.clips += new
        self.sel = {c["id"] for c in new}
        self.insp_focus, self.insp_kind = new[0]["id"], "c"
        self.commit(before)

    def finish_captions(self, ok, err):
        self.cap_proc = None
        self.cap_btn.pack_forget()
        self.prog["value"] = 100 if ok else 0
        if ok:
            n = sum(1 for c in self.clips if "caption" in c)
            self.status.config(text=f"Captions done   ·   {n} on the timeline   ·   click one to "
                                    f"fix its text")
        elif err:
            self.status.config(text="Captions failed")
            messagebox.showerror("Auto captions", err)
        else:
            self.status.config(text="Captions cancelled")

    # ---------- export ----------
    def export(self):
        if self.export_proc:
            self.cancelled = True
            try:
                self.export_proc.kill()
            except OSError:
                pass
            return
        if not self.clips:
            messagebox.showinfo("q-edit", "Open a video first.")
            return
        if self.render_len() < 0.1:
            messagebox.showerror("q-edit", "The range to export is too short. "
                                                "Check the in / out marks.")
            return
        try:
            parse_size(self.outsize.get())
        except ValueError:
            messagebox.showerror("q-edit", "Output size must look like 1080x1920, or be blank.")
            return
        self.pause()
        ExportDialog(self)

    def build_cmd(self, out, settings, codec, kbps):
        x, y, w, h = self.crop
        cw, ch = max(2, int(w) // 2 * 2), max(2, int(h) // 2 * 2)
        W, H = (self.vw + 1) // 2 * 2, (self.vh + 1) // 2 * 2
        cx = max(0, min(int(round(x)), W - cw))
        cy = max(0, min(int(round(y)), H - ch))
        rs, re = self.render_range()
        length = re - rs
        container = settings["container"]
        audio = AUDIO[settings["audio"]] if self.any_audio() else "none"
        fps = settings["fps"] if settings["fps"] != "Source" else self.fps_str(self.pfps)

        inputs, parts, vout, aout, _ = self.graph(rs, re, W, H, fps, audio=audio != "none")
        cmd = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1"] + inputs
        fc = ";".join(parts) + f";{vout}crop={cw}:{ch}:{cx}:{cy}"
        ow, oh = self.output_dims()
        if (ow, oh) != (cw, ch):
            fc += f",scale={ow}:{oh}"
        fc += ",setsar=1[out]"

        cmd += ["-filter_complex", fc, "-map", "[out]"]
        if audio != "none":
            cmd += ["-map", aout]
        cmd += video_args(codec, settings["speed"], settings["mode"], settings["quality"],
                          kbps, container)
        cmd += audio_args(audio, settings["abr"], self.opus_enc)
        if container in ("mp4", "mov"):
            cmd += ["-movflags", "+faststart"]
        cmd.append(out)
        return cmd, length

    def start_export(self, out, settings, codec, kbps):
        cmd, length = self.build_cmd(out, settings, codec, kbps)
        self.export_out = out
        self.cancelled = False
        self.prog["value"] = 0
        self.export_btn.config(text="Cancel export")
        self.status.config(text=f"Exporting with {codec[0]}...")
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 stdin=subprocess.DEVNULL, text=True, creationflags=NOWIN)
        except OSError as e:
            self.finish_export(False, str(e))
            return
        self.export_proc = p
        threading.Thread(target=self.run_export, args=(p, length), daemon=True).start()

    def run_export(self, p, length):
        err = []
        threading.Thread(target=lambda: err.append(p.stderr.read()), daemon=True).start()
        for line in p.stdout:
            if line.startswith("out_time_us="):
                try:
                    self.q.put(("progress", min(1.0, int(line.split("=", 1)[1]) / 1e6 / length)))
                except ValueError:
                    pass
        p.wait()
        time.sleep(0.1)
        self.q.put(("done", p.returncode == 0, ("".join(err)).strip()[-700:]))

    def finish_export(self, ok, err):
        self.export_proc = None
        self.export_btn.config(text="Export")
        if ok:
            self.prog["value"] = 100
            size = os.path.getsize(self.export_out) / 1048576
            self.status.config(text=f"Done!  Saved {Path(self.export_out).name}  ({size:.1f} MB)")
        elif self.cancelled:
            self.prog["value"] = 0
            self.status.config(text="Export cancelled")
            try:
                os.remove(self.export_out)
            except OSError:
                pass
        else:
            self.prog["value"] = 0
            self.status.config(text="Export failed")
            messagebox.showerror("Export failed", err or "Unknown FFmpeg error")

    # ---------- projects ----------
    def project_data(self, base=None):
        """Everything needed to reopen the edit. With base, each clip also stores its path
        relative to base, so a project folder can be moved or copied as a whole."""
        def rel(path):
            try:
                return os.path.relpath(path, base)
            except ValueError:   # on another drive
                return None
        return {"app": "q-edit", "format": 1, "frame": [self.vw, self.vh], "fps": self.pfps,
                "crop": [round(float(v), 2) for v in self.crop], "ratio": self.ratio.get(),
                "outsize": self.outsize.get(), "vtracks": self.vtracks, "atracks": self.atracks,
                "clips": [dict(c, rel=rel(c["path"])) if base else dict(c) for c in self.clips],
                "marks": [self.mark_in, self.mark_out]}

    def state_key(self):
        return json.dumps(self.project_data(), sort_keys=True)

    def is_dirty(self):
        if not self.clips and not self.project_path:
            return False
        return self.state_key() != self.saved_key

    def update_title(self):
        name = Path(self.project_path).stem if self.project_path else "Untitled"
        title = f"{name}{' *' if self.is_dirty() else ''}  -  q-edit"
        if self.root.title() != title:
            self.root.title(title)

    def confirm_discard(self):
        """Asks to save unsaved changes. False = the user cancelled."""
        if not self.is_dirty():
            return True
        self.pause()
        name = Path(self.project_path).name if self.project_path else "this project"
        answer = messagebox.askyesnocancel("q-edit", f"Save changes to {name}?")
        if answer is None:
            return False
        return self.save_project() if answer else True

    def reset_project(self):
        self.pause()
        self.clips, self.sel = [], set()
        self.vtracks, self.atracks = [{"hidden": False}], [{"muted": False}]
        self.vw = self.vh = 0
        self.pfps = 30.0
        self.cur, self.mark_in, self.mark_out = 0.0, None, None
        self.crop = [0.0, 0.0, 0.0, 0.0]
        self.ratio.set("Free")
        self.outsize.set("")
        self.frame = None
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.project_path = None
        self.after_edit()
        self.redraw()
        self.saved_key = self.state_key()

    def save_project(self, as_new=False):
        """True when saved."""
        path = self.project_path
        if as_new or not path:
            first = min(self.clips, key=lambda c: c["start"])["path"] if self.clips else None
            path = filedialog.asksaveasfilename(
                title="Save project", defaultextension=PROJECT_EXT, filetypes=PROJECT_TYPES,
                initialdir=str(Path(path).parent) if path else (self.last_dir or None),
                initialfile=Path(path).name if path else (Path(first).stem if first else "Untitled")
                + PROJECT_EXT)
            if not path:
                return False
        data = self.project_data(Path(path).parent)
        try:
            tmp = path + ".tmp"   # write next to it first, so a failed save can't eat the old one
            Path(tmp).write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as e:
            messagebox.showerror("Save project", f"Could not save the project:\n{e}")
            return False
        self.project_path = path
        self.saved_key = self.state_key()
        self.add_recent(path)
        self.update_title()
        self.status.config(text=f"Saved {Path(path).name}")
        return True

    def open_any(self, path=None):
        """Ctrl+O: a project opens as is, a media file starts a new project."""
        if path is None:
            exts = " ".join(f"*{e}" for e in sorted(MEDIA_EXT))
            path = filedialog.askopenfilename(
                initialdir=self.last_dir,
                filetypes=[("Projects and media", f"*{PROJECT_EXT} {exts}")] + PROJECT_TYPES +
                MEDIA_TYPES)
            if not path:
                return
        if Path(path).suffix.lower() == PROJECT_EXT:
            self.open_project(path)
        else:
            self.open_video(path)

    def open_project(self, path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if data.get("app") != "q-edit":
                raise ValueError
        except (OSError, ValueError):
            messagebox.showerror("Open project", f"{Path(path).name} is not a q-edit project.")
            self.drop_recent(path)
            return
        if not self.confirm_discard():
            return
        self.load_project_data(data, Path(path).parent)
        self.project_path = str(path)
        self.saved_key = self.state_key()
        self.last_dir = str(Path(path).parent)
        self.add_recent(path)
        self.update_title()
        self.status.config(text=f"Opened {Path(path).name}   ·   {len(self.clips)} clips")

    def load_project_data(self, data, base):
        """Rebuilds the timeline from project / template data. Clips whose file is gone (and
        not found next to the project either) are left out, with a warning."""
        self.reset_project()
        clips, missing = [], []
        for c in data.get("clips", []):
            c = dict(c)
            rel = c.pop("rel", None)
            if "caption" in c:   # the picture is only a stand-in; it lives in the cache
                c["path"] = self.caption_media()
            if not os.path.exists(c["path"]) and rel and (base / rel).exists():
                c["path"] = str((base / rel).resolve())
            if os.path.exists(c["path"]) and self.load_media(c["path"]):
                clips.append(c)
            else:
                missing.append(Path(c["path"]).name)
        self.vtracks = data.get("vtracks") or [{"hidden": False}]
        self.atracks = data.get("atracks") or [{"muted": False}]
        for c in clips:   # a damaged file shouldn't point at a track that isn't there
            tracks = self.tracks(c["kind"])
            while len(tracks) <= c["track"]:
                tracks.append({"hidden": False} if c["kind"] == "v" else {"muted": False})
        self.clips = clips
        self.vw, self.vh = (int(v) for v in data.get("frame") or (0, 0))
        self.pfps = float(data.get("fps") or 30.0)
        self.ratio.set(data.get("ratio", "Free"))
        self.outsize.set(data.get("outsize", ""))   # this resets the crop, so the crop comes after
        crop = data.get("crop")
        self.crop = [float(v) for v in crop] if crop and len(crop) == 4 and self.vw \
            else [0.0, 0.0, float(self.vw), float(self.vh)]
        self.mark_in, self.mark_out = (data.get("marks") or [None, None])[:2]
        self.next_id = max([c["id"] for c in clips] + [c.get("link") or 0 for c in clips] + [0]) + 1
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.after_edit()
        self.update_range()
        self.zoom_fit()
        self.redraw()
        if missing:
            messagebox.showwarning("Missing files", "These files were not found and were left "
                                   "out:\n\n" + "\n".join(sorted(set(missing))[:15]))

    def add_recent(self, path):
        path = str(Path(path).resolve())
        recent = [p for p in self.settings.get("recent", []) if p != path]
        self.settings["recent"] = [path] + recent[:7]
        save_settings(self.settings)

    def drop_recent(self, path):
        path = str(Path(path).resolve())
        self.settings["recent"] = [p for p in self.settings.get("recent", []) if p != path]
        save_settings(self.settings)

    def fill_recent(self):
        m = self.recent_menu
        m.delete(0, "end")
        recent = self.settings.get("recent", [])
        for p in recent:
            m.add_command(label=f"{Path(p).stem}     {Path(p).parent}", command=lambda p=p: (
                self.open_project(p) if os.path.exists(p) else
                (messagebox.showerror("Open recent", f"{p} no longer exists."), self.drop_recent(p))))
        if not recent:
            m.add_command(label="No recent projects", state="disabled")

    # ---------- templates ----------
    def new_project(self):
        ProjectPicker(self)

    def start_project(self, template, size=None):
        """From the Projects window: None = blank, else a template folder; size = the project's
        resolution, which stays whatever is added (other sizes are fit into it)."""
        if not self.confirm_discard():
            return
        if template is None:
            self.reset_project()
            if size:
                self.set_frame(size)
                self.saved_key = self.state_key()
            self.status.config(text=f"New project   ·   {self.vw}x{self.vh}" if self.vw
                               else "New project")
            return
        try:
            data = json.loads((template / ("template" + PROJECT_EXT)).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            messagebox.showerror("New project", "That template could not be read.")
            return
        self.load_project_data(data, template)
        if size:
            self.set_frame(size)
            self.undo_stack.clear()
        if data.get("export"):
            self.exp_settings = dict(data["export"])
        self.project_path = None
        self.saved_key = self.state_key()
        self.update_title()
        name = data.get("template", {}).get("name", template.name)
        self.status.config(text=f"Template {name}: drop or import your video")

    def save_as_template(self):
        if not self.clips:
            messagebox.showinfo("Save as template", "Put something on the timeline first, like a "
                                                    "logo on V2 placed and sized the way you want.")
            return
        TemplateSaveDialog(self)

    def save_template(self, dest, name, kept, stretch):
        """Writes a template folder: template.qedit, copies of the kept clips' media, and a
        preview picture. True when saved."""
        tmp = dest.with_name(dest.name + ".saving")
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            tmp.mkdir(parents=True)
            copies = {}
            for c in kept:
                if c["path"] not in copies:
                    src = Path(c["path"])
                    target = src.name
                    while target in copies.values():
                        target = f"{src.stem}_{len(copies)}{src.suffix}"
                    shutil.copy2(src, tmp / target)
                    copies[c["path"]] = target
            data = self.project_data()
            data["clips"] = [dict(c, path=str(dest / copies[c["path"]]), rel=copies[c["path"]],
                                  **({"fill": True} if stretch else {})) for c in kept]
            data["marks"] = [None, None]
            data["export"] = dict(self.exp_settings)
            data["template"] = {"name": name, "stretch": stretch,
                                "created": time.strftime("%Y-%m-%d %H:%M")}
            (tmp / ("template" + PROJECT_EXT)).write_text(json.dumps(data, indent=1),
                                                         encoding="utf-8")
            self.template_preview(kept, tmp / "preview.png")
            shutil.rmtree(dest, ignore_errors=True)
            tmp.rename(dest)
        except OSError as e:
            shutil.rmtree(tmp, ignore_errors=True)
            messagebox.showerror("Save as template", f"Could not save the template:\n{e}")
            return False
        self.status.config(text=f"Saved template {name}   ·   File > New project to use it")
        return True

    def template_preview(self, kept, out):
        """A small picture of only the kept clips, for the New project picker."""
        if not self.vw or not any(c["kind"] == "v" for c in kept):
            return
        W = 320
        H = max(2, round(W * self.vh / self.vw) // 2 * 2)
        t = min(c["start"] for c in kept if c["kind"] == "v") + 0.05
        saved, self.clips = self.clips, kept
        try:
            inputs, parts, vout, _, _ = self.graph(t, t + 0.2, W, H, "30", audio=False)
        finally:
            self.clips = saved
        parts.append(f"{vout}format=rgb24[pv]")
        img = run_bmp(["ffmpeg", "-v", "error"] + inputs +
                      ["-filter_complex", ";".join(parts), "-map", "[pv]", "-frames:v", "1",
                       "-f", "image2pipe", "-c:v", "bmp", "-"])
        if img:
            img.save(out)

    def set_frame(self, size):
        """Sets the project's resolution. Clip positions and the crop box scale along, so a
        layout (like a template's logo in the corner) stays in place; zoom is relative to the
        frame already."""
        size = tuple(size)
        if not self.vw:
            self.vw, self.vh = size
            self.crop = [0.0, 0.0, float(self.vw), float(self.vh)]
            self.apply_ratio()
            return
        if size == (self.vw, self.vh):
            return
        sx, sy = size[0] / self.vw, size[1] / self.vh
        for c in self.clips:
            for key, f in (("pos_x", sx), ("pos_y", sy)):
                if cp(c, key):
                    c[key] = float(round(cp(c, key) * f))
        x, y, w, h = self.crop
        self.vw, self.vh = size
        full = (abs(x) < 1 and abs(y) < 1 and abs(w * sx - self.vw) < 2 and abs(h * sy - self.vh) < 2)
        self.crop = [x * sx, y * sy, w * sx, h * sy]
        if full:
            self.crop = [0.0, 0.0, float(self.vw), float(self.vh)]
        if self.get_ratio() and abs(sx - sy) > 1e-3:   # a new shape: re-fit the locked crop
            self.apply_ratio()
        self.frame = None
        self.src_cache.clear()
        self.redraw()

    def change_resolution(self):
        """Timeline > Project resolution."""
        size = ask_resolution(self.root, f"{self.vw}x{self.vh}" if self.vw else RESOLUTIONS[0])
        if not size or size == (self.vw, self.vh):
            return
        before = self.snapshot()
        self.set_frame(size)
        self.settings["frame"] = list(size)
        save_settings(self.settings)
        self.commit(before)
        self.status.config(text=f"Project resolution {self.vw}x{self.vh}")

    def apply_fills(self):
        """Template clips marked "fill" stretch to the end of everything else (an image to any
        length, a video or sound up to the end of its file)."""
        fills = [c for c in self.clips if c.get("fill")]
        end = max((c["start"] + c["dur"] for c in self.clips if not c.get("fill")), default=0.0)
        if not fills or end <= 0:
            return
        for c in fills:
            m = self.media[c["path"]]
            dur = end - c["start"]
            if not m["image"]:
                dur = min(dur, (m["dur"] - c["inp"]) / spd(c))
            if dur >= MIN_CLIP:
                c["dur"] = dur

    def on_close(self):
        self.pause()
        if not self.confirm_discard():
            return
        if self.export_proc:
            if not messagebox.askyesno("q-edit", "An export is running. Quit anyway?"):
                return
            try:
                self.export_proc.kill()
            except OSError:
                pass
        self.root.destroy()


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    # the installer puts its own FFmpeg in an "ffmpeg" folder next to the app; prefer it
    bundled = APP_DIR / "ffmpeg"
    if (bundled / "ffmpeg.exe").exists():
        os.environ["PATH"] = str(bundled) + os.pathsep + os.environ.get("PATH", "")
    migrate_settings_dir()
    root = tk.Tk()
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        root.withdraw()
        messagebox.showerror("q-edit",
                             "FFmpeg was not found.\nInstall it (winget install ffmpeg) "
                             "and restart your terminal.")
        return
    app = App(root)
    root.after(200, app.new_project)   # start with the project window
    root.mainloop()


if __name__ == "__main__":
    main()
