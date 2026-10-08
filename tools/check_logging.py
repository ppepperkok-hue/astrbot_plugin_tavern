"""Forbid Python's stdlib ``logging`` in plugin code, as the marketplace requires.

    python tools/check_logging.py

The plugin marketplace rejected v0.1.0 for exactly this: the rule is that a plugin logs
through ``astrbot.api.logger`` and **must not** use the built-in ``logging`` module. The
rejection listed thirteen files calling ``logging.getLogger(__name__)``.

It is a purely textual rule, so it gets a purely textual check -- one that runs in the
gate and fails the moment a new module reaches for stdlib logging again. A rule that
only gets verified by a human reading thirteen files gets violated again by the
fourteenth.

What is enforced:

* no ``import logging`` and no ``logging.getLogger`` anywhere under ``tavern/`` or in the
  root ``main.py``;
* ``tavern/log.py`` -- the single sanctioned bridge -- really does import the object from
  ``astrbot.api``;
* nothing else imports ``astrbot.api.logger`` directly, so there is one place to audit.

Note what this check cannot see: whether the log call actually *routes* to the plugin's
own logger. Wrapping the proxy in our own functions would satisfy every rule above while
sending every line to the global ``[Core]`` logger. That is what
``tools/verify_logging.py`` exists for, and it needs a real AstrBot process.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

#: The one file allowed to mention ``astrbot.api`` logging.
BRIDGE = "tavern/log.py"

#: The bridge must contain this import.
BRIDGE_IMPORT = "from astrbot.api import logger"

STDLIB_IMPORT = re.compile(r"^\s*import\s+logging\s*$", re.M)
STDLIB_GETLOGGER = re.compile(r"\blogging\.getLogger\b")
DIRECT_ASTRBOT_LOGGER = re.compile(r"^\s*from\s+astrbot\.api\s+import\s+.*\blogger\b", re.M)


def plugin_sources() -> list[pathlib.Path]:
    files = sorted((REPO / "tavern").rglob("*.py"))
    root_main = REPO / "main.py"
    if root_main.is_file():
        files.append(root_main)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    problems: list[str] = []
    checked = 0
    for path in plugin_sources():
        rel = path.relative_to(REPO).as_posix()
        text = path.read_text(encoding="utf-8")
        checked += 1

        for match in STDLIB_IMPORT.finditer(text):
            line = text[: match.start()].count("\n") + 1
            problems.append(f"{rel}:{line} imports the stdlib logging module")
        for match in STDLIB_GETLOGGER.finditer(text):
            line = text[: match.start()].count("\n") + 1
            problems.append(f"{rel}:{line} calls logging.getLogger")

        if rel != BRIDGE and DIRECT_ASTRBOT_LOGGER.search(text):
            line = text[: DIRECT_ASTRBOT_LOGGER.search(text).start()].count("\n") + 1
            problems.append(
                f"{rel}:{line} imports astrbot.api.logger directly; import it from "
                f"{BRIDGE} instead so there is one place to audit"
            )

    bridge = REPO / BRIDGE
    if not bridge.is_file():
        problems.append(f"{BRIDGE} is missing -- it is the only sanctioned logging source")
    elif BRIDGE_IMPORT not in bridge.read_text(encoding="utf-8"):
        problems.append(f"{BRIDGE} does not contain {BRIDGE_IMPORT!r}")

    if not args.quiet:
        print(f"scanned {checked} plugin source file(s)")
    print()
    if problems:
        print("FAIL:", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(
        f"PASS -- no stdlib logging in {checked} file(s); all logging flows through "
        f"{BRIDGE} -> astrbot.api.logger"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
