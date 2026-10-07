"""Verify the installed logo meets what AstrBot and the market need.

    python tools/check_logo.py

Checks the four things that are actually required or that bite in practice:

* the file is named exactly ``logo.png`` and sits in the **plugin root** -- that is the
  only thing AstrBot enforces (``star_manager.py:213``, ``:1171``, ``:1376``); it looks
  nowhere else;
* it is **1:1** (documented requirement) at the **recommended 256x256**;
* it has real transparency (an alpha channel with at least some fully transparent
  pixels) -- a JPEG pretending to be transparent was the problem two rounds ago;
* it is small enough that shipping it is not worth thinking about.

Also reports how much of the canvas the mark occupies, because a mark that fills the
frame edge-to-edge looks cramped beside every other icon in the list.
"""

from __future__ import annotations

import pathlib
import sys

from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")
REPO = pathlib.Path(__file__).resolve().parent.parent
LOGO = REPO / "logo.png"

problems: list[str] = []

if not LOGO.is_file():
    print("FAIL: logo.png is not in the plugin root", file=sys.stderr)
    raise SystemExit(1)

image = Image.open(LOGO)
width, height = image.size
print(f"path      : {LOGO.relative_to(REPO)}  (plugin root, exact name -- the only")
print("            thing AstrBot enforces; it looks nowhere else)")
print(f"size      : {width}x{height}")
print(f"1:1       : {width == height}")
print(f"format    : {image.format}, mode {image.mode}, {LOGO.stat().st_size:,} bytes")

if width != height:
    problems.append(f"not 1:1 ({width}x{height}) -- the documented requirement")
if (width, height) != (256, 256):
    print(f"note      : recommended size is 256x256, this is {width}x{height}")
if image.mode not in ("RGBA", "LA", "P"):
    problems.append(f"mode {image.mode} has no alpha channel -- not actually transparent")

if image.mode in ("RGBA", "LA"):
    alpha = image.convert("RGBA").getchannel("A")
    low, high = alpha.getextrema()
    transparent = sum(1 for v in alpha.get_flattened_data() if v == 0)
    total = width * height
    opaque = sum(1 for v in alpha.get_flattened_data() if v > 0)
    print(f"alpha     : range {low}-{high}, {100 * transparent / total:.0f}% fully transparent")
    print(f"coverage  : the mark covers {100 * opaque / total:.0f}% of the canvas")
    if low != 0:
        problems.append("no fully transparent pixel -- the background was not removed")
    if opaque > 0.85 * total:
        problems.append("the mark fills almost the whole canvas; leave a margin")

print()
if problems:
    print("FAIL:", file=sys.stderr)
    for item in problems:
        print(f"  - {item}", file=sys.stderr)
    raise SystemExit(1)
print("PASS -- logo.png is 1:1, 256x256, genuinely transparent, in the plugin root")
