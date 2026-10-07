"""Compare the JS and Python runs of the provider *converter* fixtures.

    python tools/st-oracle/diff_converters.py --all

The converter fixtures (``fixtures/converters/*.json``) drive
``research/_raw/st-src/prompt-converters.js`` -- the server-side logic that
reshapes an assembled chat into each provider's wire format. They are run by
``run_converters.mjs`` against the vendored module under Node and by
``run_converters_python.py`` against ``tavern/st/prompt_converters.py``; this
script compares the two.

What is compared, and how strictly
---------------------------------
Every comparison is **hard**. A fixture declares one call and is scored on three
things, all of them semantic rather than textual:

* ``returned`` -- the function's return value, JSON-normalised. A function that
  returns nothing records ``null``.
* ``mutated`` -- the arguments **after** the call. Most of these functions edit
  the message array in place and return nothing, so this is their only observable
  effect; a fixture that only checked the return value would let a no-op port pass.
* ``error`` -- a side reporting an error is a divergence. An unported function is
  reported by the Python side as an error on purpose: the fixture set doubles as
  the to-do list, and ``fixtures: N | match: M`` is the progress meter.
* ``probe`` -- an optional ``{path, call}`` the fixture declares. A function-typed
  value cannot cross into JSON, and ``getPromptNames`` returns one
  (``startsWithGroupName``), so ``returned`` only records its *presence*
  (``'<function>'``) and the fixture asserts its *behaviour* through the probe. A
  port that omits the predicate therefore still diverges, on the marker.

Normalisation is ``json.dumps(..., sort_keys=False, ensure_ascii=False)`` on both
sides, so key order is *not* compared (it is not part of the wire contract) but
list order, every scalar, ``null`` versus missing, and nested structures are.

Number equality is exact. Python ``json`` and JS ``JSON.stringify`` disagree on
two shapes -- ``0.0``/``0`` and very large integers -- so both sides are compared
after a round trip through the same JSON encoder, and a fixture that needs a
float is written with a float on both sides.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
FIXTURES = HERE / "fixtures" / "converters"
OUT = HERE / "out"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default="", help="fixture name without .json")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--no-run", action="store_true")
    return parser.parse_args(argv)


def fixture_paths(args: argparse.Namespace) -> list[Path]:
    if args.all or not args.fixture:
        return sorted(FIXTURES.glob("*.json"))
    path = FIXTURES / f"{args.fixture}.json"
    if not path.is_file():
        raise SystemExit(f"no such fixture: {path}")
    return [path]


def run_node(fixture: Path, out: Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["node", str(HERE / "run_converters.mjs"), str(fixture), "--out", str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
        check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def run_python(fixture: Path, out: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(HERE / "run_converters_python.py"), str(fixture), "--out", str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
        check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def normalise(value: Any) -> Any:
    """Round trip through JSON so both sides are compared in one representation."""
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=False))


def compare(js: dict[str, Any], py: dict[str, Any]) -> list[str]:
    divergences: list[str] = []

    # A reference-side failure is the harness's fault and must never read as a
    # port difference.
    if js.get("error"):
        divergences.append(f"reference error: {js['error']}")
        return divergences
    if py.get("error"):
        divergences.append(f"port error: {py['error']}")
        return divergences

    for field in ("returned", "mutated", "probe"):
        left = normalise(js.get(field))
        right = normalise(py.get(field))
        if left != right:
            divergences.append(
                f"{field}:\n      js={json.dumps(left, ensure_ascii=False)}\n"
                f"      py={json.dumps(right, ensure_ascii=False)}"
            )
    return divergences


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    OUT.mkdir(parents=True, exist_ok=True)

    paths = fixture_paths(args)
    if not paths:
        print(f"no converter fixtures in {FIXTURES.relative_to(REPO)} yet", file=sys.stderr)
        return 0

    rows: list[tuple[str, str, int]] = []
    total = 0
    for fixture in paths:
        name = fixture.stem
        js_out = OUT / f"{name}.js.json"
        py_out = OUT / f"{name}.python.json"
        if not args.no_run:
            code, output = run_node(fixture, js_out)
            if code != 0:
                print(f"node failed for {name}: {output.strip()[:400]}", file=sys.stderr)
                rows.append((name, "node-error", 0))
                continue
            code, output = run_python(fixture, py_out)
            if code != 0:
                print(f"python failed for {name}: {output.strip()[:400]}", file=sys.stderr)
                rows.append((name, "python-error", 0))
                continue
        if not js_out.is_file() or not py_out.is_file():
            rows.append((name, "missing-output", 0))
            continue

        js = json.loads(js_out.read_text(encoding="utf-8"))
        py = json.loads(py_out.read_text(encoding="utf-8"))
        divergences = compare(js, py)
        total += len(divergences)
        status = "diverged" if divergences else "match"
        rows.append((name, status, len(divergences)))
        if divergences and args.verbose:
            for line in divergences:
                print(f"    - {name}: {line}")

    width = max((len(name) for name, _s, _c in rows), default=10)
    print(f"\n{'fixture':<{width}}  {'result':<16} divergences")
    for name, status, count in rows:
        print(f"{name:<{width}}  {status:<16} {count}")

    matched = sum(1 for _n, s, _c in rows if s == "match")
    diverged = len(rows) - matched
    print(
        f"\nfixtures: {len(rows)} | match: {matched} | diverged: {diverged} | divergences: {total}"
    )
    print(f"VERDICT: {'PASS' if diverged == 0 else 'FAIL'}")
    return 0 if diverged == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
