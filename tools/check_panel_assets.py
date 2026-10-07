"""Check the management page's assets: every reference resolves, no stale ids.

    python tools/check_panel_assets.py

A plugin page is served as files, so a typo in a `src`/`href` is a 404 that shows up
as a blank page rather than an error -- and the page iframe is sandboxed, so there is
no devtools console the user is likely to open. This catches the two cheap mistakes:

* an asset the HTML references that is not there;
* an element id `app.js` looks up that the HTML never defines (or the reverse), which
  renders as `Cannot read properties of null` on load.

It does not run the page -- that needs a browser -- so it is a lint, not a test.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
PAGE_DIR = REPO / "pages" / "panel"
HTML = PAGE_DIR / "index.html"
JS = PAGE_DIR / "app.js"


def referenced_assets(html: str) -> list[str]:
    return re.findall(r'(?:src|href)="([^"]+)"', html)


def html_ids(html: str) -> set[str]:
    return set(re.findall(r'\bid="([^"]+)"', html))


def js_ids(js: str) -> set[str]:
    """Ids the script writes into its own rendered markup.

    Every view builds its DOM from a template literal, so most ids exist only in
    `app.js` and never in `index.html`. Both sets count as "declared".
    """
    return set(re.findall(r'\bid="([^"]+)"', js))


def js_element_lookups(js: str) -> set[str]:
    """Ids read through `getElementById`, both the direct and the `el()` helper form."""
    found = set(re.findall(r'getElementById\(\s*"([^"]+)"\s*\)', js))
    found |= set(re.findall(r'\bel\(\s*"([^"]+)"\s*\)', js))
    return found


def js_dynamic_view_ids(js: str) -> set[str]:
    """Ids the script builds at runtime, e.g. ``el(`view-${name}`)``."""
    out: set[str] = set()
    for match in re.finditer(r"el\(`([^`]+)`\)", js):
        out.add(match.group(1))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    problems: list[str] = []
    if not HTML.is_file() or not JS.is_file():
        print("FAIL: pages/panel/index.html or app.js is missing", file=sys.stderr)
        return 1

    html = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")

    if not args.quiet:
        print(f"--- {HTML.relative_to(REPO)}")
    for ref in referenced_assets(html):
        if ref.startswith(("http://", "https://", "//", "#", "data:")):
            continue
        target = (PAGE_DIR / ref.lstrip("./")).resolve()
        ok = target.is_file()
        if not args.quiet:
            print(f"  {'OK     ' if ok else 'MISSING'} asset {ref}")
        if not ok:
            problems.append(f"index.html references a missing asset: {ref}")

    ids = html_ids(html) | js_ids(js)
    dynamic = js_dynamic_view_ids(js)
    # `view-<name>` is generated per view; the names come from the nav's data-view.
    view_names = set(re.findall(r'data-view="([^"]+)"', html))
    generated = {pattern.replace("${name}", name) for pattern in dynamic for name in view_names}

    if not args.quiet:
        print(f"--- {JS.relative_to(REPO)}")
    for name in sorted(js_element_lookups(js)):
        if name in ids:
            ok = True
        elif name in generated:
            ok = True
        else:
            ok = False
        if not args.quiet:
            note = "OK     " if ok else "MISSING"
            print(f"  {note} id {name}")
        if not ok:
            problems.append(f"app.js looks up #{name}, which index.html does not define")

    # The reverse direction: a view container in the HTML that no nav button reaches.
    for name in sorted(html_ids(html)):
        if name.startswith("view-") and name != "view-overview":
            view = name[len("view-") :]
            if view not in view_names:
                problems.append(
                    f"index.html defines #{name} but no nav button targets the '{view}' view"
                )

    # The plugin id must match metadata.yaml, or every request answers "未找到该路由".
    id_match = re.search(r'const PLUGIN_ID = "([^"]+)"', js)
    if not id_match:
        problems.append("app.js has no `const PLUGIN_ID`")
    else:
        metadata = (REPO / "metadata.yaml").read_text(encoding="utf-8")
        name_match = re.search(r"^name:\s*(\S+)\s*$", metadata, re.M)
        declared = name_match.group(1) if name_match else ""
        if id_match.group(1) != declared:
            problems.append(
                f"app.js PLUGIN_ID is {id_match.group(1)!r} but metadata.yaml says "
                f"{declared!r}; every panel request would answer 未找到该路由"
            )
        elif not args.quiet:
            print(f"  OK      PLUGIN_ID matches metadata.yaml ({declared})")

    if problems:
        print("FAIL:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("PASS -- panel assets resolve and every element id the script uses exists")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
