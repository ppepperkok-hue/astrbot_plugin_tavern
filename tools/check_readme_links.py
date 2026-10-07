"""Check that every local link, image and **anchor** in the plugin's Markdown resolves.

    python tools/check_readme_links.py

Two classes of dead reference, both invisible until someone clicks:

* a path to a file that does not exist -- this caught `README_EN.md` being linked before
  it existed, on the very commit that set out to remove exactly that kind of dead
  reference;
* a `#anchor` that matches no heading. GitHub slugs a heading by lowercasing it, dropping
  punctuation **including emoji and the U+FE0F variation selector**, and turning spaces
  into hyphens. That last part is why `#️-已知边界` (with an invisible variation selector
  pasted in) and `#-已知边界` look identical in a diff but only one of them works.

Anchors are checked against the headings in the **same** file, which is where a table of
contents points. Both top-level READMEs are scanned, because the language switcher makes
each one a target of the other.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import unicodedata

REPO = pathlib.Path(__file__).resolve().parent.parent

#: Markdown files a reader is expected to land on directly.
TARGETS = ("README.md", "README_EN.md")

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.M)
LINK = re.compile(r"\]\(([^)]+)\)")
IMAGE_SRC = re.compile(r'src="([^"]+)"')


def slugify(heading: str) -> str:
    """GitHub's heading anchor for ``heading``.

    Lowercase; drop anything that is not a letter, digit, space, hyphen or underscore
    (which is what removes emoji and the invisible U+FE0F); spaces become hyphens.

    **Do not strip the result before replacing spaces.** A heading that starts with an
    emoji -- ``## ✨ 功能`` -- keeps the space the emoji left behind, and GitHub turns
    that into a *leading hyphen*: the anchor is ``-功能``, not ``功能``. Stripping early
    made this checker report eight working anchors as broken, which is the failure mode
    of a checker that is wrong rather than a document that is.
    """
    kept: list[str] = []
    for char in heading.strip().lower():
        if char in (" ", "-", "_") or char.isalnum():
            kept.append(char)
        elif unicodedata.category(char).startswith("M"):
            continue  # combining marks, including U+FE0F, never survive
    return "".join(kept).replace(" ", "-")


def anchors(text: str) -> set[str]:
    return {slugify(match.group(2)) for match in HEADING.finditer(text)}


def local_targets(text: str) -> list[tuple[str, str]]:
    """``(kind, target)`` for every link and image src, fragments included."""
    found: list[tuple[str, str]] = []
    for match in LINK.finditer(text):
        found.append(("link", match.group(1)))
    for match in IMAGE_SRC.finditer(text):
        found.append(("image", match.group(1)))
    out: list[tuple[str, str]] = []
    for kind, raw in found:
        raw = raw.strip()
        if not raw or raw.startswith(("http://", "https://", "mailto:")):
            continue
        out.append((kind, raw))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="only print problems")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    missing: list[str] = []
    checked = 0
    for name in TARGETS:
        path = REPO / name
        if not path.is_file():
            print(f"FAIL: {name} does not exist")
            return 1
        text = path.read_text(encoding="utf-8")
        known = anchors(text)
        if not args.quiet:
            print(f"--- {name}  ({len(known)} heading anchor(s))")

        seen: set[str] = set()
        for kind, raw in local_targets(text):
            if raw in seen:
                continue
            seen.add(raw)
            checked += 1

            target, _, fragment = raw.partition("#")
            ok = True
            detail = ""
            if target:
                ok = (REPO / target).exists()
                detail = f"missing file {target}"
            if ok and fragment:
                ok = fragment in known
                detail = f"no heading slugs to #{fragment}"
            if not args.quiet:
                print(f"  {'OK     ' if ok else 'MISSING'} [{kind}] {raw}")
            if not ok:
                missing.append(f"{name} -> {raw} ({detail})")

    if not args.quiet:
        print()
        print(f"local targets checked: {checked}")
    if missing:
        print("MISSING:", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(f"PASS -- {checked} local link/image/anchor target(s) resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
