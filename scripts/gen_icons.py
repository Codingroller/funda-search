"""Generate PWA icon set using Pillow.

Run once from the project root; commit the generated PNGs:

    python scripts/gen_icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

BG = (11, 108, 171)   # #0b6cab — brand blue
FG = (255, 255, 255)  # white
OUT = Path("static/icons")
OUT.mkdir(parents=True, exist_ok=True)


def _draw_icon(size: int, padding: int = 0) -> Image.Image:
    img = Image.new("RGBA", (size, size), BG)
    d = ImageDraw.Draw(img)

    s = size - 2 * padding
    cx = size // 2
    cy_roof_peak = padding + int(s * 0.30)
    cy_wall_top = padding + int(s * 0.52)
    cy_wall_bot = padding + int(s * 0.85)
    x_left = padding + int(s * 0.12)
    x_right = padding + int(s * 0.88)
    door_w = int(s * 0.16)
    door_h = int(s * 0.22)

    # Roof triangle
    roof = [(cx, cy_roof_peak), (x_left, cy_wall_top), (x_right, cy_wall_top)]
    d.polygon(roof, fill=FG)

    # Wall body
    d.rectangle([x_left, cy_wall_top, x_right, cy_wall_bot], fill=FG)

    # Door cutout (brand colour back through the white)
    door_x = cx - door_w // 2
    door_y = cy_wall_bot - door_h
    d.rectangle([door_x, door_y, door_x + door_w, cy_wall_bot], fill=BG)

    return img


def main():
    _draw_icon(192).save(OUT / "icon-192.png")
    _draw_icon(512).save(OUT / "icon-512.png")
    _draw_icon(512, padding=64).save(OUT / "maskable-512.png")
    _draw_icon(180).save(OUT / "apple-touch-icon.png")
    _draw_icon(72).save(OUT / "badge-72.png")
    print(f"Icons written to {OUT}/")


if __name__ == "__main__":
    main()
