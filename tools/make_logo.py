"""Generate `logo.png` for the repository and the plugin market listing.

    python tools/make_logo.py

**This is the placeholder, not the final asset.** It draws a lantern from primitives --
a shape chosen for "a small light you carry with you", which is the point of running a
character card inside a chat app rather than a separate browser window. It reads as an
unidentifiable gold object at small sizes, so it is meant to be replaced by a real
design; when that happens, delete this script rather than leaving a generator that no
longer matches the file it claims to produce.

AstrBot's own requirement for a plugin logo (`star_manager.py:213`, `:1171`, `:1376`):
the file must be named exactly ``logo.png`` and live in the **plugin root**; nothing
about its contents is enforced. The documented guidance is a **1:1 aspect ratio** with a
**recommended size of 256x256**, which is what this writes -- rendering at that size
rather than scaling a larger canvas down, so the committed bytes are the real asset.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "logo.png"

#: The documented recommendation, used as the actual canvas size. A square is required;
#: the number is not, but matching the recommendation removes a question.
SIZE = 256

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
    centre = SIZE / 2
    for step in range(26, 0, -1):
        radius = SIZE * 0.18 + step * SIZE * 0.018
        alpha = int(GLOW[3] * (step / 26) ** 2)
        draw.ellipse(
            [centre - radius, centre - radius, centre + radius, centre + radius],
            fill=(GLOW[0], GLOW[1], GLOW[2], alpha),
        )
    return layer.filter(ImageFilter.GaussianBlur(SIZE * 0.05))


def draw_mark(image: Image.Image) -> None:
    centre = SIZE / 2
    unit = SIZE / 512  # the original geometry was authored at 512; scale it

    # Rounded background first, then the halo inside it.
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=96 * unit, fill=BG)
    image.alpha_composite(glow_layer())
    draw = ImageDraw.Draw(image, "RGBA")

    cx = cy = centre

    def box(*values: float) -> list[float]:
        return [value * unit for value in values]

    # Lantern silhouette: a body, a lid, a handle.
    draw.rounded_rectangle(
        box(cx - 86, cy - 70, cx + 86, cy + 116),
        radius=26 * unit,
        fill=INK,
        outline=FRAME,
        width=max(1, round(10 * unit)),
    )
    draw.rounded_rectangle(
        box(cx - 104, cy - 104, cx + 104, cy - 66),
        radius=16 * unit,
        fill=INK,
        outline=FRAME,
        width=max(1, round(10 * unit)),
    )
    draw.arc(
        box(cx - 66, cy - 186, cx + 66, cy - 84),
        start=200,
        end=340,
        fill=FRAME,
        width=max(1, round(12 * unit)),
    )

    # Flame, a teardrop made of an ellipse plus a triangle.
    flame_w, flame_h = 42 * unit, 78 * unit
    draw.ellipse(
        [cx - flame_w, cy + 4 * unit - flame_h, cx + flame_w, cy + 4 * unit + flame_h],
        fill=FLAME,
    )
    draw.polygon(
        [
            (cx, cy + 4 * unit - flame_h - 34 * unit),
            (cx - flame_w * 0.62, cy + 4 * unit - flame_h * 0.30),
            (cx + flame_w * 0.62, cy + 4 * unit - flame_h * 0.30),
        ],
        fill=FLAME,
    )
    core = 13 * unit
    draw.ellipse(
        [cx - core, cy + 22 * unit - core, cx + core, cy + 22 * unit + core],
        fill=(255, 250, 225, 255),
    )

    # Base, a thin plinth that also anchors the glow.
    draw.rounded_rectangle(
        box(cx - 98, cy + 116, cx + 98, cy + 142),
        radius=12 * unit,
        fill=INK,
        outline=FRAME,
        width=max(1, round(9 * unit)),
    )

    # Two light motes, for a little life at large sizes.
    for angle, distance, radius in ((-0.62, 150, 7), (0.85, 168, 5)):
        mx = cx + math.cos(angle) * distance * unit * 1.1
        my = cy + math.sin(angle) * distance * unit * 0.72
        r = radius * unit
        draw.ellipse([mx - r, my - r, mx + r, my + r], fill=GLOW)


def main() -> None:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw_mark(image)
    image.save(OUT, optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes, {SIZE}x{SIZE}, 1:1)")

    # The market shows this at roughly 40px; check the small size is still legible by
    # writing a preview next to the source (git-ignored scratch, not shipped).
    scratch = REPO / ".scratch" / "logo-40.png"
    scratch.parent.mkdir(exist_ok=True)
    image.resize((40, 40), Image.LANCZOS).save(scratch)
    print(f"wrote {scratch} (40px preview for a legibility check)")


if __name__ == "__main__":
    main()
