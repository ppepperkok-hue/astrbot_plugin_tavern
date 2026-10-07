"""Compare the JS and Python runs of the prompt *assembly* fixtures.

    python tools/st-oracle/diff_assembly.py --all

The assembly fixtures (``fixtures/prompt/assembly-*.json``) drive
``populateChatCompletion`` -- the function that decides the final order of the
messages sent to the model. They are run by ``run_assembly.mjs`` against the
vendored ``openai.js`` under Node and by ``run_assembly_python.py`` against
``tavern/st/prompt_build.py``; this script compares the two.

What is compared, and how strictly
---------------------------------
* ``identifiers`` -- the top-level prompt blocks, **hard**: a hole (``null``) is
  part of the sequence because ``ChatCompletion.add`` assigns slots.
* ``overridden`` -- the list ``setOverriddenPrompts`` received, **hard**
  (``openai.js:1221``).
* ``chat`` -- ``(role, content, name)`` of every message the completion would
  send, in order, covering the contents of each group.

The chat comparison is only made when the *reference* declares it comparable. A
fixture whose reference side never reached ``populateChatHistory`` would
otherwise look like a port failure: the history group is empty, the port's is
not, and the diff would blame the wrong side. ``run_assembly.mjs`` publishes
``comparable.chat.ok`` (with a reason) for exactly that case; when it is false the
fixture is reported as ``match (chat not comparable: ...)`` -- the hard
comparisons still gate the verdict, and the reason is printed so the gap cannot
be mistaken for agreement. This is a harness guard: as of the current snapshot the
reference populates the history group, so every fixture is comparable.
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


def chat_entry(item: dict[str, Any]) -> tuple[Any, Any, Any]:
    """``(role, content, name)`` -- one chat message, group boundaries removed."""
    return (item.get("role"), item.get("content"), item.get("name"))


def compare(js: dict[str, Any], py: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return ``(divergences, notes)``; notes are skipped-but-recorded checks."""
    divergences: list[str] = []
    notes: list[str] = []
    if js.get("error"):
        divergences.append(f"reference error: {js['error']}")
    if py.get("error"):
        divergences.append(f"port error: {py['error']}")
    js_steps = js.get("steps") or []
    py_steps = py.get("steps") or []
    if len(js_steps) != len(py_steps):
        divergences.append(f"step count: js={len(js_steps)} py={len(py_steps)}")
        return divergences, notes
    for index, (left, right) in enumerate(zip(js_steps, py_steps, strict=True)):
        if left.get("identifiers") != right.get("identifiers"):
            divergences.append(
                f"step[{index}].identifiers:\n      js={left.get('identifiers')}\n"
                f"      py={right.get('identifiers')}"
            )
        if left.get("overridden") != right.get("overridden"):
            divergences.append(
                f"step[{index}].overridden: js={left.get('overridden')} py={right.get('overridden')}"
            )
        comparable = (js.get("comparable") or {}).get("chat") or {}
        if comparable.get("ok", True) is False:
            # The reference did not produce a history to compare against; saying
            # "match" for this check would be dishonest, so it is recorded as a
            # note instead of a divergence (see the module docstring).
            reason = comparable.get("reason") or "the reference declared it not comparable"
            notes.append(f"step[{index}].chat contents: not comparable -- {reason}")
            continue
        js_chat = [chat_entry(item) for item in left.get("chat") or []]
        py_chat = [chat_entry(item) for item in right.get("chat") or []]
        if js_chat != py_chat:
            divergences.append(
                f"step[{index}].chat contents:\n      js={js_chat}\n      py={py_chat}"
            )
    return divergences, notes


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    OUT.mkdir(parents=True, exist_ok=True)

    rows: list[tuple[str, str, int]] = []
    total = 0
    not_comparable = 0
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
        divergences, notes = compare(js, py)
        total += len(divergences)
        not_comparable += len(notes)
        if divergences:
            status = "diverged"
        elif notes:
            status = "match (partial)"
        else:
            status = "match"
        rows.append((name, status, len(divergences)))
        if notes:
            for note in notes:
                print(f"    ! {name}: {note}")
        if divergences and args.verbose:
            for line in divergences:
                print(f"    - {line}")

    width = max((len(name) for name, _s, _c in rows), default=10)
    print(f"\n{'fixture':<{width}}  {'result':<16} divergences")
    for name, status, count in rows:
        print(f"{name:<{width}}  {status:<16} {count}")

    matched = sum(1 for _n, s, _c in rows if s.startswith("match"))
    diverged = sum(1 for _n, s, _c in rows if s != "match" and s != "match (partial)")
    print(
        f"\nfixtures: {len(rows)} | match: {matched} | diverged: {diverged} "
        f"| not-comparable: {not_comparable} | divergences: {total}"
    )
    print(f"VERDICT: {'PASS' if diverged == 0 else 'FAIL'}")
    return 0 if diverged == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
