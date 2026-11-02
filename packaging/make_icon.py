"""Draws clip_editor.ico (play button over timeline bars) next to clip_editor.py."""
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((40, 40, S - 40, S - 40), radius=220, fill=(91, 140, 255, 255))
# timeline clips along the bottom
d.rounded_rectangle((150, 700, 560, 800), radius=30, fill=(28, 40, 80, 255))
d.rounded_rectangle((590, 700, 874, 800), radius=30, fill=(47, 122, 85, 255))
d.rectangle((520, 640, 540, 860), fill=(255, 77, 77, 255))
# play triangle
d.polygon([(390, 230), (390, 610), (700, 420)], fill=(255, 255, 255, 255))

out = Path(__file__).resolve().parent.parent / "clip_editor.ico"
img.resize((256, 256), Image.LANCZOS).save(
    out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", out)
