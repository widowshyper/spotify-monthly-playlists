"""Generate the app icon: assets/icon.ico (multi-size) and assets/icon.png.

A calendar page with a music note on a transparent background. Outlines keep
it visible on both light and dark taskbars. Sizes below 48px use a simplified
design (just the note, with a white halo) so it stays legible.

    python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1024  # design canvas; the full design is drawn at this size then downscaled
GREEN_LIGHT = (36, 222, 104)
GREEN_DARK = (14, 120, 58)
OUTLINE = (12, 92, 46)
NOTE = (18, 150, 72)
WHITE = (255, 255, 255)
RING = (232, 238, 234)

OUT = Path(__file__).resolve().parent.parent / "assets"


def gradient(size: int) -> Image.Image:
    """Diagonal light-to-dark green, used for the calendar header."""
    small = Image.new("RGB", (64, 64))
    px = small.load()
    for y in range(64):
        for x in range(64):
            t = (x + y) / 126
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(GREEN_LIGHT, GREEN_DARK))
    return small.resize((size, size), Image.BICUBIC)


def note_mask(size: int, heads, r, stem_w, stem_h, beam_h) -> Image.Image:
    """Two beamed eighth notes as an L mask. heads = [(cx, cy), (cx, cy)]."""
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    tops = []
    for cx, cy in heads:
        d.ellipse((cx - r * 1.18, cy - r, cx + r * 1.18, cy + r), fill=255)
        x1, x2 = cx + r * 1.18 - stem_w, cx + r * 1.18
        d.rectangle((x1, cy - stem_h, x2, cy), fill=255)
        tops.append((x1, cy - stem_h, x2))
    (ax1, atop, _), (_, btop, bx2) = tops
    d.polygon([(ax1, atop), (bx2, btop), (bx2, btop + beam_h), (ax1, atop + beam_h)], fill=255)
    return mask


def full_design() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    card = (92, 168, 932, 968)
    header_h = 250
    stroke = 26

    # card body with a dark outline (visible on white backgrounds)
    d.rounded_rectangle(card, radius=120, fill=WHITE, outline=OUTLINE, width=stroke)

    # gradient header, clipped to the card's rounded top
    header_mask = Image.new("L", (S, S), 0)
    hm = ImageDraw.Draw(header_mask)
    hm.rounded_rectangle(card, radius=120, fill=255)
    hm.rectangle((0, card[1] + header_h, S, S), fill=0)
    img.paste(gradient(S), (0, 0), header_mask)
    d.rounded_rectangle(card, radius=120, outline=OUTLINE, width=stroke)  # redraw edge over header
    d.line((card[0] + stroke // 2, card[1] + header_h, card[2] - stroke // 2, card[1] + header_h), fill=OUTLINE, width=14)

    # binding rings poking out above the card
    for cx in (330, 694):
        d.rounded_rectangle((cx - 40, 86, cx + 40, 290), radius=40, fill=RING, outline=OUTLINE, width=20)

    # "date" dashes in the header
    for i, x in enumerate(range(200, 820, 128)):
        d.rounded_rectangle((x, 336, x + 84, 372), radius=18, fill=WHITE if i == 0 else (168, 230, 190))

    # music note in the page body
    note = note_mask(S, heads=[(388, 806), (650, 760)], r=66, stem_w=38, stem_h=270, beam_h=76)
    img.paste(Image.new("RGBA", (S, S), NOTE + (255,)), (0, 0), note)
    return img


def small_design() -> Image.Image:
    """Note only, green with a white halo, drawn on a 256 canvas so the halo
    survives downscaling to 16px."""
    n = 256
    note = note_mask(n, heads=[(84, 190), (182, 166)], r=34, stem_w=20, stem_h=128, beam_h=36)
    halo = note.filter(ImageFilter.MaxFilter(25))
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (n, n), WHITE + (255,)), (0, 0), halo)
    img.paste(Image.new("RGBA", (n, n), GREEN_DARK + (255,)), (0, 0), note)
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
    # transparent strip of sizes for the README
    preview = Image.new("RGBA", (16 + 32 + 48 + 256 + 50, 256), (0, 0, 0, 0))
    x = 10
    for s in (16, 32, 48, 256):
        preview.alpha_composite(at(s), (x, 256 - s))
        x += s + 10
    preview.save(OUT / "icon_preview.png")
    print(f"Wrote {OUT / 'icon.ico'} and {OUT / 'icon.png'}")


if __name__ == "__main__":
    main()
