"""Check the READMEs the way a Markdown renderer reads them.

    python tools/check_readme_structure.py

`check_readme_links.py` covers broken targets; this covers broken *rendering*, which is
invisible in the source and obvious on the page:

* **unbalanced code fences** -- everything after the stray fence renders as code;
* **tables whose rows disagree on column count** -- the classic cause is a `|` inside a
  cell that was not escaped, which silently splits the row into extra columns;
* **unbalanced `<div>`/`<details>`/`<table>`** -- the rest of the document nests inside
  them;
* **table header separator** -- a table without `| --- |` is not a table at all.

Cell counting follows the real rule (``\\|`` is a literal pipe inside a cell) rather than
``line.count("|")``, because the naive version reports four escaped pipes as four extra
columns. That mistake was made while writing this file, which is why the rule is spelled
out here.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
TARGETS = ("README.md", "README_EN.md")

#: Tags that must balance. `img`/`br` are void and are not listed.
PAIRED_TAGS = ("div", "details", "summary", "table", "pre", "a")

WINDOWS_PATH = re.compile(r"[A-Za-z]:\\")
SEPARATOR_ROW = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def cells(line: str) -> list[str]:
    """Split a table row into cells, honouring `\\|` as a literal pipe."""
    body = line.strip()
    body = body[1:-1] if body.startswith("|") and body.endswith("|") else body
    out: list[str] = []
    buf = ""
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body):
            buf += body[index : index + 2]
            index += 2
            continue
        if char == "|":
            out.append(buf)
            buf = ""
            index += 1
            continue
        buf += char
        index += 1
    out.append(buf)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    problems: list[str] = []
    for name in TARGETS:
        path = REPO / name
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if not args.quiet:
            print(f"--- {name}: {len(lines)} lines")

        fences = len(re.findall(r"^\s*```", text, re.M))
        if fences % 2:
            problems.append(f"{name}: {fences} code fence markers (odd) -- one is unclosed")

        for tag in PAIRED_TAGS:
            opened = len(re.findall(rf"<{tag}[\s>]", text))
            closed = len(re.findall(rf"</{tag}>", text))
            if opened != closed:
                problems.append(f"{name}: <{tag}> unbalanced ({opened} open, {closed} close)")

        index = 0
        tables = 0
        while index < len(lines):
            if lines[index].strip().startswith("|"):
                start = index
                block: list[list[str]] = []
                while index < len(lines) and lines[index].strip().startswith("|"):
                    block.append(cells(lines[index]))
                    index += 1
                tables += 1
                widths = {len(row) for row in block}
                if len(block) < 2:
                    problems.append(f"{name}:{start + 1} a table with no separator row")
                elif not SEPARATOR_ROW.match(lines[start + 1]):
                    problems.append(
                        f"{name}:{start + 2} table has no `| --- |` separator row, "
                        "so it will not render as a table"
                    )
                if len(widths) > 1:
                    problems.append(
                        f"{name}:{start + 1} table rows disagree on column count "
                        f"{sorted(widths)} -- an unescaped `|` inside a cell is the usual cause"
                    )
            else:
                index += 1
        if not args.quiet:
            print(f"    tables: {tables}, fences: {fences}")

        for number, line in enumerate(lines, start=1):
            if WINDOWS_PATH.search(line):
                problems.append(f"{name}:{number} contains a Windows path: {line.strip()[:70]}")

    print()
    if problems:
        print("FAIL:", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("PASS -- both READMEs render: fences, tag pairs and table columns all balance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
