#!/usr/bin/env python3
"""
generate_icon.py - Builds resources/icon.png: a paper-badge icon with a paw
print and a pen nib accent, matching the app's pen-and-paper theme.
Run once at build time; the output PNG is what actually ships with the app.
Requires Pillow (only needed here, not at app runtime).
"""
import os
from PIL import Image, ImageDraw

SIZE = 512
OUT = os.path.join(os.path.dirname(__file__), "..", "resources", "icon.png")

PAPER = (247, 238, 214, 255)      # cream page
PAPER_EDGE = (196, 173, 130, 255)  # border
INK = (58, 47, 34, 255)            # dark brown ink
INK_SOFT = (120, 98, 70, 255)
ACCENT = (150, 60, 55, 255)        # muted red ink accent

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# --- Paper badge background (rounded square, like a torn notebook page) ---
margin = 24
draw.rounded_rectangle(
    [margin, margin, SIZE - margin, SIZE - margin],
    radius=64, fill=PAPER, outline=PAPER_EDGE, width=6
)

# faint ruled lines across the badge
for y in range(int(SIZE * 0.30), int(SIZE * 0.86), 34):
    draw.line([(margin + 30, y), (SIZE - margin - 30, y)], fill=(210, 190, 150, 120), width=2)

# left margin rule (classic notebook red line)
draw.line([(SIZE * 0.24, margin + 20), (SIZE * 0.24, SIZE - margin - 20)], fill=ACCENT, width=4)


def paw_print(cx, cy, scale, color):
    """Draw a paw print centered at (cx, cy)."""
    # main pad
    pad_w, pad_h = 110 * scale, 90 * scale
    draw.ellipse(
        [cx - pad_w / 2, cy - pad_h / 2 + 20 * scale, cx + pad_w / 2, cy + pad_h / 2 + 20 * scale],
        fill=color,
    )
    # four toes
    toe_positions = [
        (-70 * scale, -70 * scale, 42 * scale, 52 * scale, -18),
        (-28 * scale, -100 * scale, 42 * scale, 56 * scale, -6),
        (28 * scale, -100 * scale, 42 * scale, 56 * scale, 6),
        (70 * scale, -70 * scale, 42 * scale, 52 * scale, 18),
    ]
    for dx, dy, tw, th, angle in toe_positions:
        toe_img = Image.new("RGBA", (int(tw * 2), int(th * 2)), (0, 0, 0, 0))
        toe_draw = ImageDraw.Draw(toe_img)
        toe_draw.ellipse([tw * 0.5, th * 0.5, tw * 1.5, th * 1.5], fill=color)
        toe_img = toe_img.rotate(angle, resample=Image.BICUBIC, expand=False)
        img.alpha_composite(toe_img, (int(cx + dx - tw), int(cy + dy - th)))


paw_print(SIZE * 0.58, SIZE * 0.56, 1.15, INK)

# --- Pen nib accent, diagonally crossing the top-right corner ---
nib_color = ACCENT
nib_len = 150
nib_w = 26
cx, cy = SIZE * 0.76, SIZE * 0.30
angle = -40  # degrees

nib_img = Image.new("RGBA", (nib_len * 2, nib_w * 3), (0, 0, 0, 0))
nib_draw = ImageDraw.Draw(nib_img)
# nib body (triangle-ish quill shape)
nib_draw.polygon(
    [(0, nib_w * 1.5), (nib_len * 1.3, nib_w * 0.6), (nib_len * 1.3, nib_w * 2.4)],
    fill=nib_color,
)
# nib slit line
nib_draw.line(
    [(nib_len * 0.5, nib_w * 1.5), (nib_len * 1.25, nib_w * 1.5)],
    fill=PAPER, width=3,
)
nib_img = nib_img.rotate(angle, resample=Image.BICUBIC, expand=True)
paste_x = int(cx - nib_img.width / 2)
paste_y = int(cy - nib_img.height / 2)
img.alpha_composite(nib_img, (paste_x, paste_y))

os.makedirs(os.path.dirname(OUT), exist_ok=True)
img.save(OUT)
print(f"Wrote {OUT} ({img.size[0]}x{img.size[1]})")

# Windows shortcut icons need a .ico, not a .png
ico_path = OUT.replace(".png", ".ico")
img.save(ico_path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(f"Wrote {ico_path}")

# Also emit a couple of smaller sizes some launchers / trays prefer
for sz in (256, 128, 64):
    small = img.resize((sz, sz), Image.LANCZOS)
    small_path = OUT.replace(".png", f"_{sz}.png")
    small.save(small_path)
    print(f"Wrote {small_path}")
