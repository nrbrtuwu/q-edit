# q-edit

A small, fast desktop video editor for Windows: a multi-track timeline, a DaVinci-style inspector,
and posting straight to TikTok.

## Features

- Timeline with any number of video and audio tracks: cut, split, trim, ripple delete, snapping,
  undo / redo, filmstrip thumbnails and audio waveforms
- Drag and drop videos, images, audio files or whole folders from Explorer
- Inspector for each clip: zoom, position, rotation, flip, cropping, opacity, fades, volume, pan;
  move and scale clips right on the preview
- Crop to any ratio (16:9, 9:16, 1:1, ...) and export at any size
- Export presets (High quality, Small file, Discord under 10 MB, GPU export), MP4 / MKV / MOV / WebM,
  H.264 / H.265 / AV1 / VP9, GPU encoding on AMD, NVIDIA and Intel
- Post to TikTok: caption, cover, on-video text in TikTok's own font, privacy and disclosure settings

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

## Posting to TikTok

q-edit talks to TikTok through TikTok's official Content Posting API. To use it, register an app at
[developers.tiktok.com](https://developers.tiktok.com) with Login Kit (Desktop, redirect URI
`http://127.0.0.1:*/callback/`) and the Content Posting API (Direct Post), then paste its client key
and secret into q-edit (Post to TikTok > Keys). Until TikTok reviews the app, posts are private.

- [Terms of Service](TERMS.md)
- [Privacy Policy](PRIVACY.md)

## Third-party software

- The installer bundles [FFmpeg](https://ffmpeg.org) (GPL build from
  [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds)); its license is installed alongside it.
- [TikTok Sans](https://github.com/tiktok/TikTokSans) is used for on-video text, under the SIL Open
  Font License ([fonts/TikTokSans-OFL.txt](fonts/TikTokSans-OFL.txt)).
