"""Check that every local link and image in the plugin's Markdown resolves.

    python tools/check_readme_links.py

A README that points at a file which does not exist is a small thing that reads as
carelessness, and it is invisible to everyone who does not happen to click it. This
caught `README_EN.md` being linked before it existed, on the very commit that set out
to remove exactly that kind of dead reference.

Scans the top-level READMEs. Both languages are checked, because the language
switcher makes each one a target of the other.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

#: Markdown files a reader is expected to land on directly.
TARGETS = ("README.md", "README_EN.md")


def local_targets(text: str) -> list[tuple[str, str]]:
    """``(kind, target)`` for every link and image src that is not external."""
    found: list[tuple[str, str]] = []
    for match in re.finditer(r"\]\(([^)]+)\)", text):
        found.append(("link", match.group(1)))
    for match in re.finditer(r'src="([^"]+)"', text):
        found.append(("image", match.group(1)))
    out: list[tuple[str, str]] = []
    for kind, raw in found:
        raw = raw.split("#", 1)[0].strip()
        if not raw or raw.startswith(("http://", "https://", "mailto:", "#")):
            continue
        out.append((kind, raw))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="only print problems")
    args = parser.parse_args(argv if argv is not None else [])

    missing: list[str] = []
    checked = 0
    for name in TARGETS:
        path = REPO / name
        if not path.is_file():
            print(f"FAIL: {name} does not exist")
            return 1
        if not args.quiet:
            print(f"--- {name}")
        seen: set[str] = set()
        for kind, target in local_targets(path.read_text(encoding="utf-8")):
            if target in seen:
                continue
            seen.add(target)
            checked += 1
            ok = (REPO / target).exists()
            if not args.quiet:
                print(f"  {'OK     ' if ok else 'MISSING'} [{kind}] {target}")
            if not ok:
                missing.append(f"{name} -> {target}")

    if not args.quiet:
        print()
        print(f"local targets checked: {checked}")
    if missing:
        print("MISSING:", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(f"PASS -- {checked} local link/image target(s) resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
