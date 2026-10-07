"""Run the whole oracle: Node engine + Python port + diff, over every fixture.

    python tools/st-oracle/run_all.py              # run and diff
    python tools/st-oracle/run_all.py --skip-node  # reuse existing js outputs
    python tools/st-oracle/run_all.py --quiet      # only the summary

Each fixture is executed in its own Node process on purpose: the engine keeps
per-chat state (world info cache, timed effects, external activations) and only a
fresh process guarantees fixtures cannot leak into one another.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
FIXTURES = HERE / "fixtures"
OUT = HERE / "out"


def run(cmd: list[str], quiet: bool) -> float:
    started = time.perf_counter()
    result = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, encoding="utf-8")
    elapsed = time.perf_counter() - started
    if result.returncode != 0:
        sys.stderr.write(f"command failed ({result.returncode}): {' '.join(cmd)}\n")
        sys.stderr.write((result.stdout or "")[-2000:])
        sys.stderr.write((result.stderr or "")[-2000:])
        raise SystemExit(result.returncode)
    if not quiet and result.stderr.strip():
        sys.stderr.write(result.stderr[-2000:])
    return elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-node", action="store_true", help="reuse existing out/*.json")
    parser.add_argument(
        "--skip-python", action="store_true", help="reuse existing out/*.python.json"
    )
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--node",
        default=shutil.which("node") or "node",
        help="node executable (run `node --version` to record it)",
    )
    args = parser.parse_args()

    if not FIXTURES.is_dir() or not list(FIXTURES.glob("*.json")):
        print("no fixtures; run tools/st-oracle/gen_fixtures.py", file=sys.stderr)
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    fixtures = sorted(FIXTURES.glob("*.json"))
    js_times: list[float] = []
    py_times: list[float] = []

    for fixture in fixtures:
        target = OUT / f"{fixture.stem}.json"
        if args.skip_node:
            if not target.is_file():
                print(f"missing {target.name} while --skip-node", file=sys.stderr)
                return 2
        else:
            elapsed = run(
                [args.node, str(HERE / "run.mjs"), str(fixture), "--out", str(target)],
                args.quiet,
            )
            js_times.append(elapsed)
            if not args.quiet:
                print(f"node   {fixture.stem:<32} {elapsed * 1000:8.1f} ms")

    for fixture in fixtures:
        target = OUT / f"{fixture.stem}.python.json"
        if args.skip_python:
            if not target.is_file():
                print(f"missing {target.name} while --skip-python", file=sys.stderr)
                return 2
        else:
            elapsed = run(
                [sys.executable, str(HERE / "run_python.py"), str(fixture), "--out", str(target)],
                args.quiet,
            )
            py_times.append(elapsed)
            if not args.quiet:
                print(f"python {fixture.stem:<32} {elapsed * 1000:8.1f} ms")

    print()
    if js_times:
        print(
            f"node   wall clock: {sum(js_times):.2f} s total, "
            f"{sum(js_times) / len(js_times) * 1000:.1f} ms/fixture "
            f"(includes a fresh process + engine import each time)"
        )
    if py_times:
        print(
            f"python wall clock: {sum(py_times):.2f} s total, "
            f"{sum(py_times) / len(py_times) * 1000:.1f} ms/fixture"
        )
    print()

    return run(
        [
            sys.executable,
            str(HERE / "diff.py"),
            "--all",
            "--require",
            "js,port",
            "--no-run",
            "--quiet",
        ],
        args.quiet,
    )


if __name__ == "__main__":
    raise SystemExit(main())
