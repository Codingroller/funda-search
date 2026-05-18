"""Generate Funda Search PWA icon set.

Run from project root:
    python scripts/gen_icons.py

Technique: draw at SCALE × the target size (supersampling), then
downscale with LANCZOS for smooth, sub-pixel-accurate edges.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

SCALE = 8          # supersampling factor (8 × = excellent quality)
OUT = Path("static/icons")
OUT.mkdir(parents=True, exist_ok=True)

# ── Palette ───────────────────────────────────────────────────────────────
BG_TL  = ( 20,  32,  78)   # deep indigo-blue   (top-left)
BG_TR  = ( 11,  19,  54)   # darker navy         (top-right)
BG_BL  = (  9,  15,  46)   # dark navy           (bottom-left)
BG_BR  = (  4,   7,  22)   # near-black          (bottom-right)

HOUSE  = (255, 255, 255)    # pure white mark
CUTOUT = ( 10,  13,  34)    # dark navy for door / windows (reads as interior)
AMBER  = (255, 163,  51)    # warm amber accent (location dot)


# ── Background helpers ────────────────────────────────────────────────────

def _gradient_bg(size: int) -> Image.Image:
    """4-corner gradient via 2×2 → bilinear resize (O(1), no pixel loop)."""
    tiny = Image.new("RGB", (2, 2))
    tiny.putpixel((0, 0), BG_TL)
    tiny.putpixel((1, 0), BG_TR)
    tiny.putpixel((0, 1), BG_BL)
    tiny.putpixel((1, 1), BG_BR)
    return tiny.resize((size, size), Image.BILINEAR)


def _radial_vignette(size: int, strength: float = 0.35) -> Image.Image:
    """Subtle radial darkening towards the corners."""
    small = Image.new("L", (3, 3))
    small.putpixel((1, 1), int(255 * (1 - strength)))   # center: lighter
    small.putpixel((0, 0), 255)                          # corners: darkened
    small.putpixel((2, 0), 255)
    small.putpixel((0, 2), 255)
    small.putpixel((2, 2), 255)
    small.putpixel((1, 0), int(255 * (1 - strength * 0.5)))
    small.putpixel((0, 1), int(255 * (1 - strength * 0.5)))
    small.putpixel((1, 2), int(255 * (1 - strength * 0.5)))
    small.putpixel((2, 1), int(255 * (1 - strength * 0.5)))
    return small.resize((size, size), Image.BILINEAR)


# ── Icon drawing ──────────────────────────────────────────────────────────

def _draw(final_size: int, maskable: bool = False) -> Image.Image:
    """Render one icon at SCALE × final_size, then downscale."""
    s = final_size * SCALE

    # Background
    img = _gradient_bg(s).convert("RGBA")

    # Radial vignette overlay
    vig = _radial_vignette(s, strength=0.28)
    vig_img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    vig_img.paste(Image.new("RGB", (s, s), (0, 0, 0)), mask=vig)
    img = Image.alpha_composite(img, vig_img)

    draw = ImageDraw.Draw(img)

    # ── Layout geometry ──────────────────────────────────────────────────
    # For maskable icons the safe zone is the inner 80%; use more padding.
    pad_pct = 0.22 if maskable else 0.155
    pad = int(s * pad_pct)

    avail_w = s - 2 * pad
    avail_h = s - 2 * pad
    cx = s / 2
    cy = s / 2 + avail_h * 0.03   # nudge house very slightly below centre

    # House dimensions
    hw = avail_w * 0.76   # house width
    hh = avail_h * 0.72   # house total height (roof peak to foundation)

    # Body (rectangle)
    body_h     = hh * 0.50
    body_left  = cx - hw / 2
    body_right = cx + hw / 2
    body_top   = cy + (hh / 2 - body_h)   # roof baseline
    body_bot   = cy + hh / 2              # foundation

    # Roof (triangle)
    roof_over  = hw * 0.09              # eave overhang
    roof_peak  = cy - hh / 2 + hh * 0.04   # just inside top padding
    roof_eave  = body_top + hh * 0.03       # overlaps body slightly

    # ── Amber glow behind the accent dot ─────────────────────────────────
    acc_r  = hw * 0.095
    acc_x  = cx
    acc_y  = roof_peak - acc_r * 1.4

    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd   = ImageDraw.Draw(glow)
    gr   = acc_r * 5.5
    gd.ellipse(
        [acc_x - gr, acc_y - gr, acc_x + gr, acc_y + gr],
        fill=(*AMBER, 55),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=s // 18))
    img  = Image.alpha_composite(img, glow)
    draw = ImageDraw.Draw(img)

    # ── Draw the house ───────────────────────────────────────────────────
    # Body rectangle (drawn first so roof covers the seam)
    draw.rectangle(
        [body_left, body_top, body_right, body_bot],
        fill=HOUSE,
    )

    # Roof polygon
    draw.polygon(
        [
            (cx,                   roof_peak),   # peak
            (body_right + roof_over, roof_eave), # right eave
            (body_left - roof_over,  roof_eave), # left eave
        ],
        fill=HOUSE,
    )

    # ── Door (arched) ────────────────────────────────────────────────────
    door_w   = hw * 0.195
    door_h   = body_h * 0.50
    door_cx  = cx
    door_top = body_bot - door_h
    dl = door_cx - door_w / 2
    dr = door_cx + door_w / 2

    # Arch cap (ellipse)
    arch_h = door_w * 0.85
    draw.ellipse(
        [dl, door_top, dr, door_top + arch_h],
        fill=CUTOUT,
    )
    # Rectangular trunk
    draw.rectangle(
        [dl, door_top + arch_h / 2, dr, body_bot],
        fill=CUTOUT,
    )

    # ── Windows (two arched) ─────────────────────────────────────────────
    if final_size >= 128:   # skip at tiny sizes
        win_w  = hw * 0.17
        win_h  = body_h * 0.34
        win_r  = win_w / 2
        win_y  = (body_top + door_top) / 2 - win_h * 0.10
        for wx in [cx - hw * 0.24, cx + hw * 0.24]:
            wl = wx - win_r
            wr = wx + win_r
            # Arched top
            draw.ellipse([wl, win_y, wr, win_y + win_w], fill=CUTOUT)
            # Rectangular trunk
            draw.rectangle([wl, win_y + win_w / 2, wr, win_y + win_h], fill=CUTOUT)

    # ── Chimney (small rectangle top-right of roof) ───────────────────────
    if final_size >= 192:
        ch_w   = hw * 0.07
        ch_h   = hh * 0.12
        ch_x   = cx + hw * 0.22
        ch_top = roof_peak + (ch_x - cx) / (body_right + roof_over - cx) * (roof_eave - roof_peak)
        draw.rectangle(
            [ch_x - ch_w / 2, ch_top - ch_h, ch_x + ch_w / 2, ch_top + ch_h * 0.4],
            fill=HOUSE,
        )

    # ── Amber location dot ───────────────────────────────────────────────
    draw.ellipse(
        [acc_x - acc_r, acc_y - acc_r, acc_x + acc_r, acc_y + acc_r],
        fill=(*AMBER, 255),
    )
    # Tiny bright specular highlight on the amber dot
    spec_r = acc_r * 0.28
    spec_x = acc_x - acc_r * 0.22
    spec_y = acc_y - acc_r * 0.28
    draw.ellipse(
        [spec_x - spec_r, spec_y - spec_r, spec_x + spec_r, spec_y + spec_r],
        fill=(255, 230, 180, 200),
    )

    # ── Downscale ────────────────────────────────────────────────────────
    return img.resize((final_size, final_size), Image.LANCZOS).convert("RGBA")


# ── Output ────────────────────────────────────────────────────────────────

def _save(size: int, name: str, maskable: bool = False) -> None:
    icon = _draw(size, maskable=maskable)
    # Convert RGBA → RGB for PNG (PNG handles transparency but manifests
    # expect opaque icons; RGBA is fine too, keeping it).
    icon.save(OUT / name, format="PNG", optimize=False)
    print(f"  {name}  ({size}×{size})")


def main() -> None:
    print("Generating icons …")
    _save(192, "icon-192.png")
    _save(512, "icon-512.png")
    _save(512, "maskable-512.png", maskable=True)
    _save(180, "apple-touch-icon.png")
    _save(72,  "badge-72.png")
    print(f"Done — icons written to {OUT}/")


if __name__ == "__main__":
    main()
