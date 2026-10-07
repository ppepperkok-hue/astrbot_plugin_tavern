"""Compare the JS and Python runs of the prompt-assembly fixtures.

    python tools/st-oracle/diff_prompt.py --all
    python tools/st-oracle/diff_prompt.py --fixture 01-message-model --verbose

Each fixture is run twice -- once by ``run_prompt.mjs`` against the vendored
``openai.js`` under Node, once by ``run_prompt_python.py`` against the port --
and every step's fields are compared. Exit status is 1 when any comparable
fixture diverges, so this can gate a porting step the same way ``diff.py`` does
for the world info engine.
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
FIXTURES = HERE / "fixtures" / "prompt"
OUT = HERE / "out"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default="", help="fixture name without .json")
    parser.add_argument("--all", action="store_true", help="run every fixture")
    parser.add_argument("--verbose", action="store_true", help="print every divergence")
    parser.add_argument("--no-run", action="store_true", help="reuse existing outputs")
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
        ["node", str(HERE / "run_prompt.mjs"), str(fixture), "--out", str(out)],
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
        [sys.executable, str(HERE / "run_prompt_python.py"), str(fixture), "--out", str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
        check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def walk(path: str, left: Any, right: Any, out: list[str], ignore: set[str]) -> None:
    """Field-by-field difference between the JS and Python step results."""
    if path in ignore:
        return
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            walk(f"{path}.{key}" if path else key, left.get(key), right.get(key), out, ignore)
        return
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            out.append(f"{path}: {len(left)} item(s) vs {len(right)}")
            return
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            walk(f"{path}[{index}]", a, b, out, ignore)
        return
    if path.endswith("tokens") and (left in (None, 0) or right in (None, 0)):
        # A message built without ``createAsync`` has no token count in JS, which
        # arithmetic treats as 0; the port's 0 is the same value.
        left = left or 0
        right = right or 0
    if left != right:
        out.append(f"{path}: js={left!r} py={right!r}")


def compare(js: dict[str, Any], py: dict[str, Any], ignore: set[str]) -> list[str]:
    divergences: list[str] = []
    js_steps = js.get("steps") or []
    py_steps = py.get("steps") or []
    if len(js_steps) != len(py_steps):
        return [f"step count: js={len(js_steps)} py={len(py_steps)}"]
    for index, (left, right) in enumerate(zip(js_steps, py_steps, strict=True)):
        kind = left.get("type")
        if kind != right.get("type"):
            divergences.append(f"step[{index}]: type js={kind} py={right.get('type')}")
            continue
        walk(f"step[{index}]({kind})", left, right, divergences, ignore)
    return divergences


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    OUT.mkdir(parents=True, exist_ok=True)

    rows: list[tuple[str, str, int]] = []
    total_divergences = 0
    for fixture in fixture_paths(args):
        name = fixture.stem
        js_out = OUT / f"prompt-{name}.js.json"
        py_out = OUT / f"prompt-{name}.python.json"
        fixture_json = json.loads(fixture.read_text(encoding="utf-8"))
        comparable = bool(fixture_json.get("comparable", True))

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
        ignore = set(fixture_json.get("ignore_fields") or [])
        divergences = compare(js, py, ignore)
        if not comparable:
            rows.append((name, "not-comparable", 0))
            if args.verbose:
                for line in divergences:
                    print(f"    ~ {line}")
            continue
        total_divergences += len(divergences)
        rows.append((name, "match" if not divergences else "diverged", len(divergences)))
        if divergences and args.verbose:
            for line in divergences:
                print(f"    - {line}")

    width = max((len(name) for name, _status, _count in rows), default=10)
    print(f"\n{'fixture':<{width}}  {'result':<14} divergences")
    for name, status, count in rows:
        print(f"{name:<{width}}  {status:<14} {count}")

    matched = sum(1 for _n, status, _c in rows if status == "match")
    diverged = sum(1 for _n, status, _c in rows if status == "diverged")
    skipped = sum(1 for _n, status, _c in rows if status == "not-comparable")
    print(
        f"\nfixtures: {len(rows)} | match: {matched} | diverged: {diverged} "
        f"| not-comparable: {skipped} | divergences: {total_divergences}"
    )
    verdict = "PASS" if diverged == 0 else "FAIL"
    print(f"VERDICT: {verdict}")
    return 0 if diverged == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
