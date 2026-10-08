"""Verify the logger really is AstrBot's, in a real AstrBot process.

    python tools/verify_logging.py

The marketplace rule is that a plugin logs through ``astrbot.api.logger`` and never
through stdlib ``logging``. Satisfying that textually is easy and satisfying it
*functionally* is not, because ``astrbot.api.logger`` is a proxy
(``_PluginContextLogger``, ``astrbot/api/__init__.py:60``) that resolves the calling
module with ``sys._getframe(1)`` and routes to that plugin's own logger.

That makes one refactor silently wrong: wrapping the logger in helper functions. Every
message would then be attributed to the helper's module and fall back to the global
``astrbot`` logger. Nothing errors; the logs simply arrive under the wrong name.

So this asserts the *routing*, not the import. AstrBot tags each record with
``plugin_tag``, computed as the logger name minus its ``astrbot.plugin.`` prefix
(``core/log.py:41``), and falls back to ``[Core]`` for the global logger. The check
plants a character card that cannot be parsed, which makes ``tavern/st/cards.py`` emit a
warning through our re-exported handle, then requires that line to be tagged
``[astrbot_plugin_tavern]``.

A text-level check for ``from astrbot.api import logger`` would pass even while every
line landed under ``[Core]``. This one cannot.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
PLUGIN_NAME = "astrbot_plugin_tavern"
WORK = REPO / ".scratch" / "logging-check"
DASHBOARD_PORT = 6455

#: A file `scan_cards` will refuse. The warning text is asserted below, so it must stay
#: in step with `tavern/st/cards.py`.
BROKEN_CARD = "{ this is not json at all"
WARNING_MARKER = "skip character card"


def build_root() -> pathlib.Path:
    root = WORK / "astrbot-root"
    shutil.rmtree(root, ignore_errors=True)
    plugin_dir = root / "data" / "plugins" / PLUGIN_NAME
    plugin_dir.parent.mkdir(parents=True)

    # Copy the working tree as the installed plugin, so this tests the current code.
    shutil.copytree(
        REPO,
        plugin_dir,
        ignore=shutil.ignore_patterns(
            ".git", ".scratch", ".tools", ".uv-cache", ".pytest_cache", "__pycache__", "data"
        ),
    )
    cards = root / "data" / "plugin_data" / PLUGIN_NAME / "cards"
    cards.mkdir(parents=True)
    (cards / "broken.json").write_text(BROKEN_CARD, encoding="utf-8")
    print(f"planted an unparsable card: {cards / 'broken.json'}")

    # `astrbot run` refuses a directory without this marker (cli/basic.py:15).
    (root / ".astrbot").write_text("", encoding="utf-8")

    from astrbot.core.config.default import DEFAULT_CONFIG

    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["log_level"] = "INFO"
    config["dashboard"]["enable"] = False
    config["wake_prefix"] = ["/"]
    config["plugin_set"] = ["*"]
    (root / "data" / "cmd_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return root


def start(root: pathlib.Path) -> tuple[subprocess.Popen, pathlib.Path]:
    env = dict(os.environ)
    env["ASTRBOT_ROOT"] = str(root)
    env["PYTHONIOENCODING"] = "utf-8"
    log = root / "astrbot.log"
    handle = log.open("w", encoding="utf-8")
    command = [
        str(pathlib.Path(sys.executable).parent / "astrbot.exe"),
        "run",
        "-p",
        str(DASHBOARD_PORT),
    ]
    process = subprocess.Popen(
        command, cwd=str(root), env=env, stdout=handle, stderr=subprocess.STDOUT
    )
    print(f"started AstrBot (pid {process.pid}) -> {log}")
    return process, log


def main() -> int:
    root = build_root()
    process, log = start(root)
    failures: list[str] = []
    try:
        deadline = time.time() + 120
        text = ""
        while time.time() < deadline:
            if log.is_file():
                text = log.read_text(encoding="utf-8", errors="replace")
                if WARNING_MARKER in text and "loaded" in text:
                    break
            time.sleep(1.0)

        print("\n--- relevant log lines ---")
        found: list[str] = []
        for line in text.splitlines():
            if WARNING_MARKER in line or "[tavern]" in line:
                found.append(line)
                print(f"  {line.strip()[:150]}")

        tagged = [line for line in found if WARNING_MARKER in line]
        if not tagged:
            failures.append(
                "the warning from tavern/st/cards.py never appeared -- the log call was "
                "discarded, so tavern.log did not reach astrbot's logger"
            )
        else:
            if PLUGIN_NAME not in tagged[0]:
                failures.append(
                    f"the warning was logged under the wrong logger: expected it tagged "
                    f"[{PLUGIN_NAME}], got {tagged[0].strip()[:120]!r} -- the re-exported "
                    f"proxy lost the caller's module, so it fell back to [Core]"
                )

        tavern_lines = [line for line in found if "[tavern]" in line]
        if len(tavern_lines) < 3:
            failures.append(
                f"only {len(tavern_lines)} startup log line(s) from the plugin; expected "
                "the load report too"
            )
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()

    print()
    if failures:
        print("FAIL:", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(
        f"PASS -- the plugin's logs reach astrbot.api.logger and are tagged "
        f"[{PLUGIN_NAME}], including from tavern/st/"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
