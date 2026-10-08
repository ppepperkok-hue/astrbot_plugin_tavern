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
    notes: list[str] = []

    import yaml  # type: ignore[import-not-found]

    metadata_path = REPO_ROOT / "metadata.yaml"
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    for field in ("name", "desc", "version", "author", "repo"):
        if not metadata.get(field):
            problems.append(f"metadata.yaml is missing {field}")
    name = str(metadata.get("name", ""))
    if not name.isidentifier():
        problems.append(f"metadata.yaml name {name!r} is not a python identifier")
    # AstrBot installs plugins by directory name. A checkout whose directory
    # name differs still loads (the plugin is identified by metadata.name), so
    # this is reported as a note: the folder in data/plugins/ should use the
    # plugin name so updates and the marketplace stay consistent.
    if name != REPO_ROOT.name:
        notes.append(
            f"checkout directory is {REPO_ROOT.name!r} while metadata.yaml name is {name!r}; "
            f"install it as data/plugins/{name}/"
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
    for note in notes:
        print(f"  i {note}")
    print(f"--- manifest: {'PASS' if not problems else 'FAIL'}")
    return not problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--astrbot", type=Path, default=None, help="AstrBot python interpreter")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    results = [check_plugin_manifest()]
    # The market rejects archives above 16 MB and requires `metadata.yaml` inside
    # them, so the *published* archive -- not the working tree -- is part of the
    # gate. It is measured from HEAD, so it also proves the export-ignore rules are
    # committed rather than only present locally.
    results.append(run("plugin size", [sys.executable, "tools/check_plugin_size.py", "--quiet"]))
    # AstrBot looks for a plugin logo at exactly `logo.png` in the **plugin root** and
    # nowhere else (`star_manager.py:213`, `:1171`, `:1376`), and the documented shape is
    # 1:1 at a recommended 256x256. A replacement image that is 16:9, or a JPEG whose
    # "transparency" is a drawn checkerboard, violates that silently -- the file still
    # exists, so nothing else complains.
    results.append(run("logo", [sys.executable, "tools/check_logo.py"]))
    # The marketplace rule: log through `astrbot.api.logger`, never stdlib `logging`.
    # v0.1.0 was rejected for exactly this, in thirteen files, so the rule gets a check
    # that fails the moment a fourteenth module reaches for `logging` again. A rule
    # verified only by a human reading thirteen files is violated again by the
    # fourteenth.
    results.append(run("logging rule", [sys.executable, "tools/check_logging.py", "--quiet"]))
    # A README pointing at a file that does not exist reads as carelessness and is
    # invisible to anyone who does not click it. Cheap enough to always check.
    results.append(run("readme links", [sys.executable, "tools/check_readme_links.py", "--quiet"]))
    # Links resolve, but do they *render*? An unclosed code fence turns the rest of the
    # page into code, and an unescaped `|` inside a table cell silently adds a column.
    # Neither is visible in the source, and both are visible to every visitor.
    results.append(
        run("readme render", [sys.executable, "tools/check_readme_structure.py", "--quiet"])
    )
    # The pre-release sweep: secrets in tracked files, version strings that disagree
    # across the four places a version lives, config keys missing from the README, and
    # placeholder text that would ship. A leaked key in a public repo is the one mistake
    # a follow-up push cannot undo.
    results.append(run("preflight", [sys.executable, "tools/preflight.py", "--quiet"]))
    # The management page is served as files, so a typo in a `src`/`href` or an element
    # id the script looks up but the markup never defines is a blank page rather than an
    # error -- and the page iframe is sandboxed, so there is no console the user will
    # find. Also asserts `PLUGIN_ID` equals `metadata.yaml`'s `name`, since a mismatch
    # makes every panel request answer "未找到该路由".
    results.append(run("panel assets", [sys.executable, "tools/check_panel_assets.py", "--quiet"]))
    # A bug fixed quietly teaches nothing: the reason it was missed is what recurs.
    # `docs/known-issues.md` is the register and code names an entry with
    # `KNOWN-ISSUE: <id>`. Checked in both directions so neither half rots.
    results.append(run("known issues", [sys.executable, "tools/check_known_issues.py", "--quiet"]))
    # A mirror module with tests but no production importer cannot affect a reply, so
    # "it has tests" is not evidence it works. The three currently-unwired modules are
    # declared in KNOWN_UNWIRED with the reasoning; `--fail-on-unwired` turns a *new*
    # undeclared one into a failure, which is the point of declaring them.
    results.append(
        run(
            "module wiring",
            [
                sys.executable,
                "tools/st-oracle/check_module_wiring.py",
                "--fail-on-unwired",
                "--quiet",
            ],
        )
    )
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
        # The management page's backend registers routes through the host's own
        # `Context.register_web_api` and is matched by the host's own route matcher.
        # Both are versioned host behaviour, so this runs against the installed
        # AstrBot: a route string that registers but never matches answers only
        # "未找到该路由" and is invisible until someone opens the page.
        results.append(run("panel api", [str(interpreter), "tools/verify_panel.py"]))
        # AstrBot fills command arguments from the handler's signature, through a
        # partial binding and a two-parameter skip. When that alignment is wrong the
        # command still registers and still replies -- just with truncated or missing
        # arguments -- so the end-to-end script, which calls `on_message` directly,
        # cannot see it. This drives the real `CommandFilter`.
        results.append(run("command params", [str(interpreter), "tools/verify_cmd_params.py"]))
        # `astrbot.api.logger` is a proxy that resolves the *calling module* to route each
        # line to the plugin's own logger. Wrapping it in a helper satisfies every textual
        # rule while sending everything to the global `[Core]` logger, so the static
        # `logging rule` check cannot see it. This runs a real AstrBot, makes a port
        # module log, and requires the line to come out tagged with the plugin's name.
        results.append(run("logging routing", [str(interpreter), "tools/verify_logging.py"]))
        # The release gate: unpack the *published archive* into a clean AstrBot root and
        # run the plugin from there. Every other check runs against the working tree,
        # where `tests/`, `tools/` and a `data/plugins/` junction are all importable;
        # the archive has none of them, so this is the only check that proves what
        # actually ships starts on someone else's machine.
        results.append(run("release archive", [str(interpreter), "tools/release_check.py"]))
    else:
        print("\n=== astrbot integration: SKIPPED (no interpreter) ===")

    failed = results.count(False)
    print(f"\n{'ALL CHECKS PASSED' if not failed else f'{failed} CHECK(S) FAILED'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
