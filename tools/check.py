"""Development checks: format, lint, unit tests, AstrBot integration smoke.

Usage (no AstrBot needed for the first three):
    python tools/check.py
    python tools/check.py --astrbot "E:\\astrbot_plugin\\.tools\\uv-tools\\astrbot\\Scripts\\python.exe"

The AstrBot step is skipped when no interpreter is given or found; it loads the
plugin exactly like ``StarManager`` does and drives a real ``AstrMessageEvent``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ASTRBOT_PYTHON = REPO_ROOT / ".tools" / "uv-tools" / "astrbot" / "Scripts" / "python.exe"


def run(label: str, command: list[str]) -> bool:
    print(f"\n=== {label} ===\n$ {' '.join(command)}")
    completed = subprocess.run(command, cwd=REPO_ROOT)
    ok = completed.returncode == 0
    print(f"--- {label}: {'PASS' if ok else 'FAIL'}")
    return ok


def check_plugin_manifest() -> bool:
    """Sanity check the files AstrBot reads before it even imports the plugin."""
    print("\n=== manifest ===")
    problems: list[str] = []

    import yaml  # type: ignore[import-not-found]

    metadata_path = REPO_ROOT / "metadata.yaml"
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    for field in ("name", "desc", "version", "author", "repo"):
        if not metadata.get(field):
            problems.append(f"metadata.yaml is missing {field}")
    name = str(metadata.get("name", ""))
    if not name.isidentifier():
        problems.append(f"metadata.yaml name {name!r} is not a python identifier")
    # AstrBot installs plugins by directory name, so a mismatch is worth
    # flagging -- but a plain "astrbot_plugin" checkout is a normal development
    # layout, so only check when the directory name looks like a plugin name.
    if REPO_ROOT.name.startswith("astrbot_plugin_") and name != REPO_ROOT.name:
        problems.append(
            f"metadata.yaml name {name!r} differs from the directory name {REPO_ROOT.name!r} "
            "(AstrBot installs by directory name)"
        )

    schema_path = REPO_ROOT / "_conf_schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        problems.append(f"_conf_schema.json is not valid JSON: {exc}")
    else:
        if not isinstance(schema, dict) or not schema:
            problems.append("_conf_schema.json must be a non empty object")

    if not (REPO_ROOT / "main.py").is_file():
        problems.append("main.py is missing at the repository root")

    for problem in problems:
        print(f"  ! {problem}")
    print(f"--- manifest: {'PASS' if not problems else 'FAIL'}")
    return not problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--astrbot", type=Path, default=None, help="AstrBot python interpreter")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    results = [check_plugin_manifest()]
    results.append(run("ruff check", [sys.executable, "-m", "ruff", "check", "."]))
    results.append(
        run(
            "ruff format",
            [
                sys.executable,
                "-m",
                "ruff",
                "format",
                "--check",
                "tavern",
                "tests",
                "tools",
                "main.py",
            ],
        )
    )
    if not args.skip_tests:
        results.append(run("pytest", [sys.executable, "-m", "pytest", "tests", "-q"]))

    interpreter = args.astrbot or (
        DEFAULT_ASTRBOT_PYTHON if DEFAULT_ASTRBOT_PYTHON.is_file() else None
    )
    if interpreter is not None and Path(interpreter).is_file():
        results.append(run("astrbot smoke", [str(interpreter), "tools/astrbot_smoke.py"]))
        results.append(run("astrbot e2e", [str(interpreter), "tools/astrbot_e2e.py"]))
    else:
        print("\n=== astrbot integration: SKIPPED (no interpreter) ===")

    failed = results.count(False)
    print(f"\n{'ALL CHECKS PASSED' if not failed else f'{failed} CHECK(S) FAILED'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
