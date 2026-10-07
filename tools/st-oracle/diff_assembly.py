"""Compare the JS and Python runs of the prompt *assembly* fixtures.

    python tools/st-oracle/diff_assembly.py --all

The assembly fixtures (``fixtures/prompt/assembly-*.json``) drive
``populateChatCompletion`` -- the function that decides the final order of the
messages sent to the model. They are run by ``run_assembly.mjs`` against the
vendored ``openai.js`` under Node and by ``run_assembly_python.py`` against
``tavern/st/prompt_build.py``; this script compares the two.

Status: the reference side runs (the harness reaches the real function through
the module's ``oracleSetPromptManager`` / ``oracleSetPowerUser`` hooks), and the
identifier sequence it produces is the ground truth for the port. The port
currently diverges -- see ``STATUS.md`` for the open defect and its diagnosis.
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
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--no-run", action="store_true")
    return parser.parse_args(argv)


def fixture_paths(args: argparse.Namespace) -> list[Path]:
    if args.all or not args.fixture:
        return sorted(FIXTURES.glob("assembly-*.json"))
    path = FIXTURES / f"{args.fixture}.json"
    if not path.is_file():
        raise SystemExit(f"no such fixture: {path}")
    return [path]


def run_node(fixture: Path, out: Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["node", str(HERE / "run_assembly.mjs"), str(fixture), "--out", str(out)],
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
        [sys.executable, str(HERE / "run_assembly_python.py"), str(fixture), "--out", str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
        check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def compare(js: dict[str, Any], py: dict[str, Any]) -> list[str]:
    divergences: list[str] = []
    if js.get("error"):
        divergences.append(f"reference error: {js['error']}")
    if py.get("error"):
        divergences.append(f"port error: {py['error']}")
    js_steps = js.get("steps") or []
    py_steps = py.get("steps") or []
    if len(js_steps) != len(py_steps):
        divergences.append(f"step count: js={len(js_steps)} py={len(py_steps)}")
        return divergences
    for index, (left, right) in enumerate(zip(js_steps, py_steps, strict=True)):
        if left.get("identifiers") != right.get("identifiers"):
            divergences.append(
                f"step[{index}].identifiers:\n      js={left.get('identifiers')}\n"
                f"      py={right.get('identifiers')}"
            )
        js_chat = [item.get("content") for item in left.get("chat") or []]
        py_chat = [item.get("content") for item in right.get("chat") or []]
        if js_chat != py_chat:
            divergences.append(
                f"step[{index}].chat contents:\n      js={js_chat}\n      py={py_chat}"
            )
        if left.get("overridden") != right.get("overridden"):
            divergences.append(
                f"step[{index}].overridden: js={left.get('overridden')} py={right.get('overridden')}"
            )
    return divergences


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    OUT.mkdir(parents=True, exist_ok=True)

    rows: list[tuple[str, str, int]] = []
    total = 0
    for fixture in fixture_paths(args):
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
        rows.append((name, "match" if not divergences else "diverged", len(divergences)))
        if divergences and args.verbose:
            for line in divergences:
                print(f"    - {line}")

    width = max((len(name) for name, _s, _c in rows), default=10)
    print(f"\n{'fixture':<{width}}  {'result':<14} divergences")
    for name, status, count in rows:
        print(f"{name:<{width}}  {status:<14} {count}")

    matched = sum(1 for _n, s, _c in rows if s == "match")
    diverged = sum(1 for _n, s, _c in rows if s != "match")
    print(
        f"\nfixtures: {len(rows)} | match: {matched} | diverged: {diverged} | divergences: {total}"
    )
    print(f"VERDICT: {'PASS' if diverged == 0 else 'FAIL'}")
    return 0 if diverged == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
