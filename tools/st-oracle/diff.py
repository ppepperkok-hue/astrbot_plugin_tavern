"""Field-by-field diff between the Node oracle and the Python port.

    python tools/st-oracle/diff.py --all
    python tools/st-oracle/diff.py --all --verbose
    python tools/st-oracle/diff.py --fixture 03-selective-logic
    python tools/st-oracle/diff.py --all --require js,python

Exit code 0 means every comparable fixture matched; non-zero means at least one
field diverged (or a run is missing). That is the acceptance signal for every
future porting step -- right now divergences are expected and are the point.

Reference is always the SillyTavern output (``out/<f>.json``); the port output is
``out/<f>.python.json``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
OUT = HERE / "out"
FIXTURES = HERE / "fixtures"

#: Fields compared exactly. Anything not listed is reported as skipped.
COMPARED = [
    "worldInfoString",
    "worldInfoBefore",
    "worldInfoAfter",
    "anBefore",
    "anAfter",
    "worldInfoDepth",
    "worldInfoExamples",
    "outletEntries",
    "activatedEntries",
]
SKIPPED = [
    "settings",
    "settings_applied",
    "settings_mismatched",
    "world_info",
    "worlds_loaded",
    "port_truncated",
    "timing_ms",
]


def load(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"  ! {path.name} is not valid JSON: {exc}")
        return None


def run_backend(cmd: list[str], label: str, quiet: bool, cwd: Path | None = None) -> bool:
    result = subprocess.run(
        cmd,
        cwd=str(cwd or REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"    ! {label} exited {result.returncode}")
        for stream in (result.stdout, result.stderr):
            if stream and stream.strip():
                print("      " + stream.strip().splitlines()[-1])
        return False
    if not quiet and result.stderr.strip():
        print(f"    ({label}) " + result.stderr.strip().splitlines()[-1])
    return True


def short(value: Any, limit: int = 120) -> str:
    text = (
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    )
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def canonical(value: Any) -> Any:
    """Order-insensitive view of a nested JSON value."""
    if isinstance(value, list):
        return sorted(
            (canonical(v) for v in value),
            key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False),
        )
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in sorted(value.items())}
    return value


def diff_scan(js: dict[str, Any], py: dict[str, Any], label: str, verbose: bool) -> list[str]:
    problems: list[str] = []
    for field in COMPARED:
        expected = js.get(field)
        actual = py.get(field)
        if expected == actual:
            if verbose:
                print(f"    = {label}.{field}")
            continue
        # Same content, different sequence: both engines are deterministic, they
        # just emit in opposite orders (world-info.js walks ascending and unshifts,
        # the port returns ascending). Kept as a separate, loud finding.
        if canonical(expected) == canonical(actual):
            problems.append(f"{label}.{field}: ORDER-ONLY difference (same items)")
            continue
        if isinstance(expected, list) and isinstance(actual, list):
            problems.append(
                f"{label}.{field}: {len(expected)} entries vs {len(actual)}; "
                f"js={short(expected)} py={short(actual)}"
            )
        else:
            problems.append(f"{label}.{field}: js={short(expected)} py={short(actual)}")
    for field in SKIPPED:
        if field in js or field in py:
            if verbose:
                print(f"    ~ {label}.{field} (not compared)")
    missing = set(py) - set(js) - set(SKIPPED)
    extra = set(js) - set(py) - set(SKIPPED)
    if missing:
        problems.append(f"{label}: port-only fields {sorted(missing)}")
    if extra:
        problems.append(f"{label}: js-only fields {sorted(extra)}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="check every fixture in fixtures/")
    parser.add_argument("--fixture", help="fixture stem, e.g. 03-selective-logic")
    parser.add_argument("--verbose", action="store_true", help="also print matching fields")
    parser.add_argument(
        "--require",
        default="js,port",
        help="comma separated: js,port -- which outputs must exist for a verdict (default: both)",
    )
    parser.add_argument(
        "--only",
        help="comma separated subset of js,port to execute (default: the --require set)",
    )
    parser.add_argument(
        "--no-run",
        action="store_true",
        help="never invoke the runners (run_all.py does it once); default is to run both sides so the comparison can never be stale",
    )
    parser.add_argument("--quiet", action="store_true", help="suppress runner notes")
    args = parser.parse_args()

    required = {item.strip() for item in args.require.split(",") if item.strip()}
    execute = {item.strip() for item in (args.only or args.require).split(",") if item.strip()}
    unknown = (required | execute) - {"js", "port"}
    if unknown:
        parser.error(f"unknown backend(s): {', '.join(sorted(unknown))} (expected js,port)")
    if not execute <= required:
        parser.error("--only must be a subset of --require")

    if args.fixture:
        names = [args.fixture]
    elif args.all:
        names = sorted(p.stem for p in FIXTURES.glob("*.json"))
    else:
        parser.error("pass --all or --fixture <name>")
    if not names:
        print("no fixtures found", file=sys.stderr)
        return 2

    if not args.no_run:
        node = shutil.which("node")
        if not node:
            print("node not found on PATH; pass --no-run to compare existing out/", file=sys.stderr)
            return 2
        for name in names:
            fixture = FIXTURES / f"{name}.json"
            if not fixture.is_file():
                continue
            if "js" in execute:
                run_backend(
                    [node, str(HERE / "run.mjs"), str(fixture), "--out", str(OUT / f"{name}.json")],
                    "run.mjs",
                    args.quiet,
                )
            if "port" in execute:
                run_backend(
                    [
                        sys.executable,
                        str(HERE / "run_python.py"),
                        str(fixture),
                        "--out",
                        str(OUT / f"{name}.python.json"),
                    ],
                    "run_python.py",
                    args.quiet,
                )

    all_problems: dict[str, list[str]] = {}
    results: list[tuple[str, str, int]] = []

    for name in names:
        js = load(OUT / f"{name}.json")
        py_out = load(OUT / f"{name}.python.json")
        print(f"[{name}]")
        if js is None or py_out is None:
            which = []
            if js is None:
                which.append("js")
            if py_out is None:
                which.append("python")
            if set(which) & required:
                print(f"  MISSING output: {', '.join(which)}")
                all_problems[name] = [f"missing output: {', '.join(which)}"]
            else:
                print(f"  skipped (no output yet: {', '.join(which)})")
                results.append((name, "skipped", 0))
            continue

        comparable = js.get("comparable", True) and py_out.get("comparable", True)
        js_scans = js.get("scans") or []
        py_scans = py_out.get("scans") or []
        problems: list[str] = []
        if len(js_scans) != len(py_scans):
            problems.append(f"scan count: js={len(js_scans)} py={len(py_scans)}")
        for index, (a, b) in enumerate(zip(js_scans, py_scans, strict=False)):
            problems += diff_scan(a, b, f"scan[{index}]", args.verbose)

        if not comparable:
            reason = (
                js.get("unavailable_reason")
                or py_out.get("unavailable_reason")
                or "marked not comparable"
            )
            print(f"  not comparable: {reason}")
            if problems:
                print(f"  ({len(problems)} divergence(s) recorded but not counted)")
                for item in problems:
                    print(f"    - {item}")
            results.append((name, "not-comparable", 0))
            continue

        if problems:
            all_problems[name] = problems
            print(f"  DIVERGED ({len(problems)} field(s))")
            for item in problems:
                print(f"    - {item}")
            results.append((name, "diverged", len(problems)))
        else:
            print("  match")
            results.append((name, "match", 0))

    print()
    print(f"{'fixture':<34} {'result':<16} divergences")
    for name, verdict, count in results:
        print(f"{name:<34} {verdict:<16} {count}")

    matched = sum(1 for _, v, _ in results if v == "match")
    diverged = sum(1 for _, v, _ in results if v == "diverged")
    not_comparable = sum(1 for _, v, _ in results if v == "not-comparable")
    skipped = sum(1 for _, v, _ in results if v == "skipped")
    total = sum(count for _, _, count in results)
    print()
    print(
        f"fixtures: {len(results)} | match: {matched} | diverged: {diverged} | "
        f"not-comparable: {not_comparable} | skipped: {skipped} | divergences: {total}"
    )

    if all_problems:
        print("VERDICT: FAIL")
        return 1
    if skipped:
        print("VERDICT: PARTIAL (some outputs missing)")
        return 0 if "port" not in required else 1
    print("VERDICT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
