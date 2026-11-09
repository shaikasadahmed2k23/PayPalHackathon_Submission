"""Builds every app icon from public/leashpay-icon.png.  Run from frontend/:  python scripts/make_icons.py
Needs Pillow (pip install pillow). Re-run whenever the source image changes.

The source has a lot of empty margin and an off-centre mark, so we first cut the mark out onto a
transparent layer (its colours are all far from the near-white background, so the red channel is a clean
"how much mark is here" signal), find its bounding box, and then:
  * favicon.ico (16/32/48) and the header logo: tight square crop, ~6% padding, transparent
  * icon-192/512 and apple-touch-icon: mark on #F5F7FA with ~12% padding
  * maskable 192/512: mark scaled so every mark pixel sits inside the central 80% safe circle
"""
import math
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "public" / "leashpay-icon.png"
PUBLIC, APP = ROOT / "public", ROOT / "app"
BG = (245, 247, 250)  # app background #F5F7FA


def cut_out_mark(src: Image.Image) -> Image.Image:
    """RGBA copy of the source with the background removed (soft edges kept, colours un-blended)."""
    rgb = src.convert("RGB")
    w, h = rgb.size
    corners = [rgb.getpixel(p) for p in [(2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3)]]
    bg = tuple(sum(c[i] for c in corners) / 4 for i in range(3))
    out = Image.new("RGBA", (w, h))
    src_px, dst = rgb.load(), out.load()
    for y in range(h):
        for x in range(w):
            r, g, b = src_px[x, y]
            a = max(0.0, min(1.0, (bg[0] - r) / bg[0]))  # mark colours have R ~ 0, background R ~ 241
            if a < 0.08:
                continue  # background (also swallows compression noise)
            if a > 0.97:
                a = 1.0
            px = (r, g, b)
            col = tuple(max(0, min(255, round((px[i] - bg[i] * (1 - a)) / a))) for i in range(3))
            dst[x, y] = (*col, round(a * 255))
    return out


def square_crop(mark: Image.Image, pad: float) -> Image.Image:
    """Square around the mark's bounding box, centred on it, with `pad` of the box on each side."""
    left, top, right, bottom = mark.getchannel("A").point(lambda v: 255 if v > 100 else 0).getbbox()
    side = max(right - left, bottom - top)
    full = round(side * (1 + 2 * pad))
    cx, cy = (left + right) / 2, (top + bottom) / 2
    canvas = Image.new("RGBA", (full, full), (0, 0, 0, 0))
    canvas.alpha_composite(mark, (round(full / 2 - cx), round(full / 2 - cy)))
    return canvas


def on_background(tile: Image.Image, size: int) -> Image.Image:
    out = Image.new("RGBA", (size, size), (*BG, 255))
    out.alpha_composite(tile.resize((size, size), Image.LANCZOS))
    return out.convert("RGB")


def maskable_tile(mark: Image.Image) -> Image.Image:
    """Square tile where the farthest mark pixel is within 40% of the side from the centre (safe circle)."""
    left, top, right, bottom = mark.getchannel("A").point(lambda v: 255 if v > 100 else 0).getbbox()
    cx, cy = (left + right) / 2, (top + bottom) / 2
    far = max(math.hypot(x - cx, y - cy) for x in (left, right) for y in (top, bottom))  # corner of bbox: safe upper bound
    full = round(2 * far / 0.80)  # radius 0.4 of the tile
    canvas = Image.new("RGBA", (full, full), (0, 0, 0, 0))
    canvas.alpha_composite(mark, (round(full / 2 - cx), round(full / 2 - cy)))
    return canvas


def main() -> None:
    mark = cut_out_mark(Image.open(SRC))
    tight = square_crop(mark, 0.06)
    roomy = square_crop(mark, 0.12)
    safe = maskable_tile(mark)

    for name, size in [("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)]:
        on_background(roomy, size).save(PUBLIC / name, optimize=True)
        print("wrote public/" + name)
    for size in (192, 512):
        on_background(safe, size).save(PUBLIC / f"icon-maskable-{size}.png", optimize=True)
        print(f"wrote public/icon-maskable-{size}.png")

    tight.resize((128, 128), Image.LANCZOS).save(PUBLIC / "logo-mark.png", optimize=True)  # header logo (shown at ~28px)
    print("wrote public/logo-mark.png")
    tight.resize((256, 256), Image.LANCZOS).save(APP / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print("wrote app/favicon.ico (16, 32, 48)")


if __name__ == "__main__":
    main()
