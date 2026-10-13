"""Generate the app icon: assets/icon.ico (multi-size) and assets/icon.png.

A green tile with a calendar page and a music note. Sizes below 48px use a
simplified design (just the note) so it stays legible in the taskbar/title bar.

    python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

S = 1024  # design canvas; everything is drawn at this size then downscaled
GREEN_LIGHT = (36, 222, 104)
GREEN_DARK = (14, 120, 58)
HEADER = (16, 84, 46)
NOTE = (18, 150, 72)
WHITE = (255, 255, 255)
RING = (226, 232, 228)

OUT = Path(__file__).resolve().parent.parent / "assets"


def tile() -> Image.Image:
    """Rounded square with a diagonal green gradient."""
    grad = Image.new("RGB", (S, S))
    px = grad.load()
    for y in range(S):
        for x in range(S):
            t = (x + y) / (2 * (S - 1))
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(GREEN_LIGHT, GREEN_DARK))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((32, 32, S - 32, S - 32), radius=220, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    return img


def beamed_note(d: ImageDraw.ImageDraw, heads, r, stem_w, stem_h, beam_h, color):
    """Two eighth notes joined by a beam. heads = [(cx, cy), (cx, cy)]."""
    tops = []
    for cx, cy in heads:
        d.ellipse((cx - r * 1.18, cy - r, cx + r * 1.18, cy + r), fill=color)
        x1 = cx + r * 1.18 - stem_w
        top = cy - stem_h
        d.rectangle((x1, top, cx + r * 1.18, cy), fill=color)
        tops.append((x1, top, cx + r * 1.18))
    (ax1, atop, _), (_, btop, bx2) = tops
    d.polygon([(ax1, atop), (bx2, btop), (bx2, btop + beam_h), (ax1, atop + beam_h)], fill=color)


def full_design() -> Image.Image:
    img = tile()
    d = ImageDraw.Draw(img)
    # calendar card
    card = (222, 262, 802, 832)
    d.rounded_rectangle(card, radius=72, fill=WHITE)
    d.rounded_rectangle((card[0], card[1], card[2], card[1] + 190), radius=72, fill=HEADER)
    d.rectangle((card[0], card[1] + 110, card[2], card[1] + 190), fill=HEADER)
    # binding rings
    for cx in (382, 642):
        d.rounded_rectangle((cx - 26, 206, cx + 26, 330), radius=26, fill=RING)
    # header "date" dashes
    for i, x in enumerate(range(300, 730, 92)):
        d.rounded_rectangle((x, 392, x + 58, 418), radius=13, fill=WHITE if i == 0 else (96, 150, 118))
    # music note in the page body
    beamed_note(d, heads=[(418, 728), (624, 692)], r=50, stem_w=28, stem_h=200, beam_h=56, color=NOTE)
    return img


def small_design() -> Image.Image:
    img = tile()
    d = ImageDraw.Draw(img)
    beamed_note(d, heads=[(355, 720), (680, 640)], r=118, stem_w=72, stem_h=440, beam_h=130, color=WHITE)
    return img


def main() -> None:
    OUT.mkdir(exist_ok=True)
    big, small = full_design(), small_design()

    def at(size: int) -> Image.Image:
        return (big if size >= 48 else small).resize((size, size), Image.LANCZOS)

    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    frames = [at(s) for s in sizes]
    frames[-1].save(OUT / "icon.ico", format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    at(512).save(OUT / "icon.png")
    # side-by-side preview for reviewing the design (not used by the app)
    preview = Image.new("RGBA", (16 + 32 + 48 + 256 + 50, 256), (245, 245, 245, 255))
    x = 10
    for s in (16, 32, 48, 256):
        preview.paste(at(s), (x, 256 - s), at(s))
        x += s + 10
    preview.save(OUT / "icon_preview.png")
    print(f"Wrote {OUT / 'icon.ico'} and {OUT / 'icon.png'}")


if __name__ == "__main__":
    main()
