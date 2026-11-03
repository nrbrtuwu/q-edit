# q-edit

A small, fast desktop video editor for Windows: a multi-track timeline and a DaVinci-style
inspector.

## Features

- Timeline with any number of video and audio tracks: cut, split, trim, ripple delete, snapping,
  undo / redo, filmstrip thumbnails and audio waveforms
- Drag and drop videos, images, audio files or whole folders from Explorer
- Inspector for each clip: zoom, position, rotation, flip, cropping, opacity, fades, volume, pan,
  speed (10-400%, with or without pitch correction); move and scale clips right on the preview
- Auto captions (English, Hungarian or auto-detect) with [whisper.cpp](https://github.com/ggml-org/whisper.cpp),
  offline, on the GPU (AMD, NVIDIA, Intel via Vulkan) or the CPU: right-click a clip or use the
  inspector; captions are editable clips on their own track, and the spoken word lights up like in
  CapCut. Word times are checked against the sound (typically within 50 ms), and with a
  language picked only that language is captioned. Any installed font (default DFP King Gothic
  Medium when installed). The speech model (Fast 190 MB or Accurate 574 MB) downloads on first use
- Projects (`.qedit`) with a fixed resolution you pick (any size, e.g. 1080x1920), save, open and
  recent files; reusable templates, like a logo already
  placed on V2 that stretches to whatever video you drop in
- Crop to any ratio (16:9, 9:16, 1:1, ...) and export at any size
- Export presets (High quality, Small file, Discord under 10 MB, GPU export), MP4 / MKV / MOV / WebM,
  H.264 / H.265 / AV1 / VP9, GPU encoding on AMD, NVIDIA and Intel

## Run from source

Needs Python 3, [FFmpeg](https://ffmpeg.org) on PATH (`winget install ffmpeg`), and:

```
pip install pillow tkinterdnd2
python clip_editor.py
```

`tkinterdnd2` is optional; without it everything works except dragging files in from Explorer.

## Build the installer

See the comments at the top of [packaging/build.ps1](packaging/build.ps1). It builds
`packaging/Output/q-edit-Setup-<version>.exe` with PyInstaller and Inno Setup and bundles FFmpeg.

## Third-party software

The installer bundles [FFmpeg](https://ffmpeg.org) (GPL build from
[BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds)) and
[whisper.cpp](https://github.com/ggml-org/whisper.cpp)'s whisper-cli (MIT); their licenses are installed
alongside them. Speech models come from [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp)
on Hugging Face. When running from source, put whisper-cli.exe on PATH (or unzip whisper-bin-x64.zip into
`packaging/whisper_dl/bin`) for auto captions.
