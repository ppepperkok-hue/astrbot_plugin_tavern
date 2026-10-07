"""Generate `logo.png` for the repository and the plugin market listing.

    python tools/make_logo.py

Committed as a script so the asset is reproducible rather than a binary nobody can
regenerate or adjust. Uses Pillow, which is a *developer* dependency only -- the
plugin itself does not need it (the card importer treats Pillow as optional).

The mark is a lantern: the plugin's own fixtures are about a lighthouse, and a
lantern reads as "a small light you carry with you", which is the point of running a
character card inside a chat app instead of a separate browser window.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "logo.png"
SIZE = 512

#: Warm ink background, amber light. Kept deliberately few, because a logo is read at
#: 40px in a market list before it is ever seen large.
BG = (26, 24, 32, 255)
FRAME = (214, 170, 96, 255)
GLOW = (255, 198, 120, 46)
FLAME = (255, 236, 190, 255)
INK = (58, 52, 66, 255)


def glow_layer() -> Image.Image:
    """The lantern's halo, drawn once and blurred as a whole.

    Drawing translucent discs directly onto the mark produced a flat white disc: the
    rings stack past the point where they still read as light. One blurred layer,
    composited under the lantern, keeps the halo *behind* the silhouette where it
    belongs.
    """
    layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    cx, cy = SIZE / 2, SIZE / 2
    for step in range(26, 0, -1):
        radius = 92 + step * 9
        alpha = int(GLOW[3] * (step / 26) ** 2)
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=(GLOW[0], GLOW[1], GLOW[2], alpha),
        )
    return layer.filter(ImageFilter.GaussianBlur(26))


def draw_mark(image: Image.Image) -> None:
    cx, cy = SIZE / 2, SIZE / 2

    # Rounded background first, then the halo inside it.
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=96, fill=BG)
    image.alpha_composite(glow_layer())

    # Everything crisp goes on the same drawing context as the background.
    draw = ImageDraw.Draw(image, "RGBA")

    # Lantern silhouette: a body, a lid, a handle.
    body = [cx - 86, cy - 70, cx + 86, cy + 116]
    draw.rounded_rectangle(body, radius=26, fill=INK, outline=FRAME, width=10)

    # Lid.
    draw.rounded_rectangle(
        [cx - 104, cy - 104, cx + 104, cy - 66], radius=16, fill=INK, outline=FRAME, width=10
    )

    # Handle: an arc above the lid.
    draw.arc(
        [cx - 66, cy - 186, cx + 66, cy - 84],
        start=200,
        end=340,
        fill=FRAME,
        width=12,
    )

    # Flame, a teardrop made of two arcs plus an ellipse.
    flame_w, flame_h = 42, 78
    flame_box = [cx - flame_w, cy + 4 - flame_h, cx + flame_w, cy + 4 + flame_h]
    draw.ellipse(flame_box, fill=FLAME)
    draw.polygon(
        [
            (cx, cy + 4 - flame_h - 34),
            (cx - flame_w * 0.62, cy + 4 - flame_h * 0.30),
            (cx + flame_w * 0.62, cy + 4 - flame_h * 0.30),
        ],
        fill=FLAME,
    )

    # The wick's cool core, so the flame does not read as a plain blob.
    core = 13
    draw.ellipse([cx - core, cy + 22 - core, cx + core, cy + 22 + core], fill=(255, 250, 225, 255))

    # Base, a thin plinth that also anchors the glow.
    draw.rounded_rectangle(
        [cx - 98, cy + 116, cx + 98, cy + 142], radius=12, fill=INK, outline=FRAME, width=9
    )

    # Two light motes for a little life at large sizes.
    for angle, distance, radius in ((-0.62, 150, 7), (0.85, 168, 5)):
        mx = cx + math.cos(angle) * distance * 1.1
        my = cy + math.sin(angle) * distance * 0.72
        draw.ellipse([mx - radius, my - radius, mx + radius, my + radius], fill=GLOW)


def main() -> None:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw_mark(image)
    image.save(OUT)
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes, {SIZE}x{SIZE})")

    # The market shows this at roughly 40px; check the small size is still legible by
    # writing a preview next to the source (git-ignored scratch, not shipped).
    scratch = REPO / ".scratch" / "logo-40.png"
    scratch.parent.mkdir(exist_ok=True)
    image.resize((40, 40), Image.LANCZOS).save(scratch)
    print(f"wrote {scratch} (40px preview for a legibility check)")


if __name__ == "__main__":
    main()
