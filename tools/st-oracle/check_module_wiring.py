"""Report which `tavern/st/wi_*.py` mirror modules nothing in production imports.

    python tools/st-oracle/check_module_wiring.py

A mirror module with tests but no importer is not "ported" in the sense the
porting plan means. The plan's whole point is that the *engine* behaves like
SillyTavern; a module nothing calls cannot influence a single reply, however
faithfully it is written and however green its tests are. This repository has
three such modules (`wi_keywords`, `wi_scan_state`, `wi_timed`), which is easy to
miss precisely because they look finished.

It is a *report*, not a gate: some of the three may be deliberately kept as
reference implementations, and that is a legitimate choice. What is not
legitimate is not knowing. Exit code is 0 either way; read the output.

Imports are resolved by walking the AST, not by grepping text, because a docstring
that *mentions* `WorldInfoTimedEffects` (several do, to explain which engine
behaviour a native implementation mirrors) is not an import.

    python tools/st-oracle/check_module_wiring.py
    python tools/st-oracle/check_module_wiring.py --fail-on-unwired
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
PACKAGE = REPO / "tavern"
TESTS = REPO / "tests"

#: Modules a caller may legitimately keep unwired as reference implementations.
#: Listing one here is a *claim* that the native path is the intended engine; the
#: report prints the claim so it can be argued with.
KNOWN_UNWIRED: dict[str, str] = {}


def import_targets(path: pathlib.Path) -> set[str]:
    """Module basenames this file imports."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.rsplit(".", 1)[-1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.rsplit(".", 1)[-1])
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fail-on-unwired",
        action="store_true",
        help="exit 1 when an unlisted mirror module has no production importer",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    mirror_modules = sorted(path.stem for path in (PACKAGE / "st").glob("wi_*.py"))
    prod_importers: dict[str, list[str]] = {name: [] for name in mirror_modules}
    test_importer_count: dict[str, int] = {name: 0 for name in mirror_modules}

    for path in sorted(PACKAGE.rglob("*.py")):
        for target in import_targets(path):
            if target in prod_importers and path.stem != target:
                prod_importers[target].append(str(path.relative_to(REPO)))
    for path in sorted(TESTS.rglob("*.py")):
        for target in import_targets(path):
            if target in test_importer_count:
                test_importer_count[target] += 1

    print(f"mirror modules under tavern/st: {len(mirror_modules)}\n")
    unwired: list[str] = []
    for name in mirror_modules:
        importers = prod_importers[name]
        tests = test_importer_count[name]
        if importers:
            status = "wired"
        elif tests:
            status = "UNWIRED (has tests)" if name not in KNOWN_UNWIRED else "unwired (declared)"
            if name not in KNOWN_UNWIRED:
                unwired.append(name)
        else:
            status = "unwired (no tests either)"
        joined = ", ".join(importers) or "-"
        note = f"  # {KNOWN_UNWIRED[name]}" if name in KNOWN_UNWIRED else ""
        print(f"{name:<16} {status:<22} prod={joined:<38} tests={tests}{note}")

    print(f"\nunwired with tests and no declared reason: {len(unwired)}")
    for name in unwired:
        print(f"  {name}")
    if unwired:
        print(
            "\nThese modules cannot affect a reply. Either wire them in, delete them, or"
            "\nadd them to KNOWN_UNWIRED with the reason the native path is intended."
        )
    return 1 if (unwired and args.fail_on_unwired) else 0


if __name__ == "__main__":
    raise SystemExit(main())
