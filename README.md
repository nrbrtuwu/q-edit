# q-edit

A small, fast desktop video editor for Windows: a multi-track timeline and a DaVinci-style
inspector.

## Features

- Timeline with any number of video and audio tracks: cut, split, trim, ripple delete, snapping,
  undo / redo, filmstrip thumbnails and audio waveforms
- Drag and drop videos, images, audio files or whole folders from Explorer
- Inspector for each clip: zoom, position, rotation, flip, cropping, opacity, fades, volume, pan;
  move and scale clips right on the preview
- Projects (`.qedit`) with save, open and recent files; reusable templates, like a logo already
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
[BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds)); its license is installed alongside it.
