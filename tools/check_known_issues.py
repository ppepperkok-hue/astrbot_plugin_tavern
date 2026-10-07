"""Keep the known-issues register and the code honest with each other.

    python tools/check_known_issues.py

A bug fixed quietly teaches nothing: the commit message is only found by someone who
already knows to look for it, and the *reason it was missed* is what tends to recur.
`docs/known-issues.md` is the register, and code that depends on a past mistake can
name it with a marker:

    KNOWN-ISSUE: <id> -- <why this line exists>

This script checks the link in **both** directions, because each half rots on its own:

* a marker naming an id that is not in the register is a dangling reference;
* a register entry that no marker names has been orphaned by a refactor -- the code it
  described may no longer exist, and nobody will notice a stale warning.

It deliberately does not require *every* entry to have a marker in code (some are
purely behavioural), so the reverse check only reports entries whose id looks like a
code marker. Entries are keyed by their `## <n>.` heading number.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
REGISTER = REPO / "docs" / "known-issues.md"

#: Where markers may appear. `docs/` itself is excluded -- the register describes the
#: issues, so it would trivially satisfy the reverse check for every id.
SEARCH_DIRS = ("tavern", "tools", "tests", "pages")
MARKER = re.compile(r"KNOWN-ISSUE:\s*([0-9]+)")

#: Entries that are policy rather than a code-level trap, so no marker is expected.
#: Keyed by the heading number in the register.
POLICY_ONLY = {"6"}


def register_entries() -> dict[str, str]:
    """``{id: heading}`` from the register's ``## <n>. <title>`` headings."""
    if not REGISTER.is_file():
        return {}
    text = REGISTER.read_text(encoding="utf-8")
    return {
        match.group(1): match.group(2).strip()
        for match in re.finditer(r"^##\s+([0-9]+)\.\s*(.+)$", text, re.M)
    }


def code_markers() -> dict[str, list[str]]:
    """``{id: ["path:line", ...]}`` for every marker in the source tree."""
    found: dict[str, list[str]] = {}
    for directory in SEARCH_DIRS:
        root = REPO / directory
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix not in (".py", ".js", ".html", ".css") or not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for number, line in enumerate(lines, start=1):
                for match in MARKER.finditer(line):
                    found.setdefault(match.group(1), []).append(
                        f"{path.relative_to(REPO)}:{number}"
                    )
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv if argv is not None else [])

    entries = register_entries()
    markers = code_markers()
    problems: list[str] = []

    if not entries:
        print(f"FAIL: {REGISTER.relative_to(REPO)} has no `## <n>.` entries", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"register: {len(entries)} entries in {REGISTER.relative_to(REPO)}")
        print(f"markers : {len(markers)} distinct id(s) in code\n")

    for ident in sorted(markers, key=int):
        where = ", ".join(markers[ident])
        if ident in entries:
            if not args.quiet:
                print(f"  OK      [{ident}] {entries[ident]}  <- {where}")
        else:
            problems.append(f"KNOWN-ISSUE marker [{ident}] at {where} is not in the register")

    orphaned = sorted(set(entries) - set(markers) - POLICY_ONLY, key=int)
    for ident in orphaned:
        problems.append(
            f"register entry [{ident}] {entries[ident]!r} is named by no code marker; "
            "either add one where the code relies on it, or list it in POLICY_ONLY"
        )

    if not markers and entries:
        problems.append(
            "no KNOWN-ISSUE markers found anywhere; the register is not wired to the code"
        )

    if problems:
        print("FAIL:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(
        f"PASS -- {len(markers)} marker(s) resolve to the register and "
        f"{len(entries) - len(POLICY_ONLY)} non-policy entr(ies) are referenced"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
