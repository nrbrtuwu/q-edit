#!/usr/bin/env python3
"""
Automated video editor: trim, crop to a resolution, and add a PNG watermark
at a random spot.

Requires FFmpeg on your PATH (https://ffmpeg.org/download.html).

Examples:
  python video_edit.py input.mp4 --start 0:10 --end 0:45 --size 1080x1920 --watermark logo.png
  python video_edit.py clips/ --start 5 --end 30 --size 1080x1080 --watermark logo.png --out edited/
  python video_edit.py input.mp4 --size 1280x720 --watermark logo.png --mode fit --wm-scale 0.2 --opacity 0.6
"""

import argparse
import random
import shutil
import subprocess
import sys
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}


def parse_time(t: str) -> float:
    """Accepts '90', '1:30', '00:01:30.5' and returns seconds."""
    parts = t.strip().split(":")
    if len(parts) > 3:
        raise ValueError(f"Bad time format: {t}")
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + float(p)
    return seconds


def parse_size(s: str):
    try:
        w, h = s.lower().split("x")
        w, h = int(w), int(h)
    except ValueError:
        raise argparse.ArgumentTypeError("Size must look like 1920x1080")
    if w % 2 or h % 2:
        raise argparse.ArgumentTypeError("Width and height must be even numbers (H.264 needs it)")
    return w, h


def build_filter(w, h, mode, wm_scale, opacity, margin, rx, ry):
    if mode == "crop":
        # Center crop to exact size. Source must be at least this big.
        video = f"crop={w}:{h}:(in_w-{w})/2:(in_h-{h})/2"
    else:
        # "fit": scale up/down to cover the target, then center crop. Works on any source.
        video = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                 f"crop={w}:{h}")

    wm_w = max(2, int(w * wm_scale) // 2 * 2)
    wm = f"[1:v]format=rgba,scale={wm_w}:-1"
    if opacity < 1.0:
        wm += f",colorchannelmixer=aa={opacity}"

    # rx/ry are random fractions 0..1, so the logo always stays fully on screen.
    x = f"{margin}+(main_w-overlay_w-{2 * margin})*{rx:.4f}"
    y = f"{margin}+(main_h-overlay_h-{2 * margin})*{ry:.4f}"

    return f"[0:v]{video},setsar=1[base];{wm}[logo];[base][logo]overlay=x='{x}':y='{y}'[out]"


def process(src: Path, dst: Path, args):
    w, h = args.size
    rx, ry = random.random(), random.random()
    fc = build_filter(w, h, args.mode, args.wm_scale, args.opacity, args.margin, rx, ry)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats"]
    if args.start is not None:
        cmd += ["-ss", str(args.start)]
    cmd += ["-i", str(src), "-i", str(args.watermark)]
    if args.end is not None:
        start = args.start or 0.0
        if args.end <= start:
            sys.exit("Error: --end must be after --start")
        cmd += ["-t", f"{args.end - start:.3f}"]

    cmd += [
        "-filter_complex", fc,
        "-map", "[out]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", args.preset, "-crf", str(args.crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(dst),
    ]

    print(f"\n{src.name} -> {dst.name}  (watermark at {rx:.0%} x, {ry:.0%} y)")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"Failed on {src.name}. If you used --mode crop, the source might be "
              f"smaller than {w}x{h}. Try --mode fit.")
        return False
    return True


def main():
    p = argparse.ArgumentParser(description="Trim, crop and watermark videos.")
    p.add_argument("input", type=Path, help="Video file or a folder of videos")
    p.add_argument("--watermark", "-w", type=Path, required=True, help="PNG watermark file")
    p.add_argument("--size", "-s", type=parse_size, required=True, help="Output size, e.g. 1080x1920")
    p.add_argument("--start", type=parse_time, help="Cut start, e.g. 0:10 or 10.5")
    p.add_argument("--end", type=parse_time, help="Cut end, e.g. 1:05")
    p.add_argument("--out", "-o", type=Path, help="Output file or folder (default: next to input)")
    p.add_argument("--mode", choices=["crop", "fit"], default="fit",
                   help="crop = cut center only, fit = scale then crop (default)")
    p.add_argument("--wm-scale", type=float, default=0.15,
                   help="Watermark width as a fraction of video width (default 0.15)")
    p.add_argument("--opacity", type=float, default=1.0, help="Watermark opacity 0..1 (default 1)")
    p.add_argument("--margin", type=int, default=20, help="Min distance from edges in px (default 20)")
    p.add_argument("--crf", type=int, default=20, help="Quality, lower is better (default 20)")
    p.add_argument("--preset", default="veryfast", help="x264 speed preset (default veryfast)")
    p.add_argument("--seed", type=int, help="Random seed, for a repeatable watermark spot")
    args = p.parse_args()

    if shutil.which("ffmpeg") is None:
        sys.exit("Error: ffmpeg not found. Install it and add it to your PATH.")
    if not args.watermark.is_file():
        sys.exit(f"Error: watermark not found: {args.watermark}")
    if args.seed is not None:
        random.seed(args.seed)

    if args.input.is_dir():
        files = sorted(f for f in args.input.iterdir() if f.suffix.lower() in VIDEO_EXTS)
        if not files:
            sys.exit("No videos found in that folder.")
        out_dir = args.out or args.input / "edited"
        out_dir.mkdir(parents=True, exist_ok=True)
        jobs = [(f, out_dir / f"{f.stem}_edited.mp4") for f in files]
    else:
        if not args.input.is_file():
            sys.exit(f"Error: input not found: {args.input}")
        if args.out and args.out.suffix:
            dst = args.out
        else:
            folder = args.out or args.input.parent
            folder.mkdir(parents=True, exist_ok=True)
            dst = folder / f"{args.input.stem}_edited.mp4"
        jobs = [(args.input, dst)]

    ok = sum(process(src, dst, args) for src, dst in jobs)
    print(f"\nDone: {ok}/{len(jobs)} videos edited.")


if __name__ == "__main__":
    main()
