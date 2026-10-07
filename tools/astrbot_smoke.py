"""Smoke test: load the plugin the way AstrBot does, using the real AstrBot.

Run with the AstrBot tool environment so ``astrbot`` is importable:
    E:\\astrbot_plugin\\.tools\\uv-tools\\astrbot\\Scripts\\python.exe tools\\astrbot_smoke.py

It imports ``data.plugins.<dir>.main`` exactly like ``StarManager.load`` does,
instantiates the plugin with a real ``AstrBotConfig`` and calls ``initialize``,
which scans the card/world book directories. No bot, no network.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PLUGIN_DIR_NAME = "astrbot_plugin_tavern"
MODULE_PATH = f"data.plugins.{PLUGIN_DIR_NAME}.main"


def main() -> int:
    print("astrbot import check ...", end=" ")
    import astrbot  # noqa: F401

    print("ok")

    print(f"importing {MODULE_PATH} ...", end=" ")
    module = __import__(MODULE_PATH, fromlist=["main"])
    print("ok")

    plugin_class = getattr(module, "TavernPlugin", None)
    if plugin_class is None:
        print("FAIL: TavernPlugin not found")
        return 1
    print(
        f"plugin class: {plugin_class.__name__} (registered_as={getattr(plugin_class, 'registered_as', None)})"
    )

    from astrbot.api import AstrBotConfig  # noqa: F401
    from astrbot.core.config import AstrBotConfig as CoreConfig
    from astrbot.core.star.star_handler import star_handlers_registry

    # Every handler the plugin registered, straight from AstrBot's registry.
    groups: dict[str, set[str]] = {}
    subcommands: dict[str, set[str]] = {}
    for md in star_handlers_registry:
        for star_filter in md.event_filters:
            kind = type(star_filter).__name__
            if kind == "CommandGroupFilter":
                groups.setdefault(star_filter.group_name, set()).update(
                    star_filter.get_complete_command_names()
                )
            elif kind == "CommandFilter":
                names = set(star_filter.get_complete_command_names())
                subcommands[md.handler_name] = names

    if "tavern" not in groups:
        print("FAIL: /tavern command group was not registered")
        return 1
    print(f"command group: {sorted(groups['tavern'])}")
    if len(subcommands) < 11:
        print(f"FAIL: expected 11 subcommands, found {len(subcommands)}: {sorted(subcommands)}")
        return 1
    print(f"subcommands: {sorted(name for names in subcommands.values() for name in names)}")

    # A real config object keeps the plugin on its normal code path.
    config = CoreConfig()
    context = _NullContext()
    plugin = plugin_class(context, config)
    print(f"plugin instance: config data dir = {plugin.config.data_dir}")

    subcommand_methods = sorted(
        name[4:]
        for name in dir(plugin_class)
        if name.startswith("cmd_") and callable(getattr(plugin_class, name))
    )
    print(f"handlers on the class: {', '.join(subcommand_methods)}")

    asyncio.run(plugin.initialize())
    print("initialize(): ok")

    asyncio.run(plugin.terminate())
    print("terminate(): ok")
    print("SMOKE TEST PASSED")
    return 0


class _NullContext:
    """Minimal stand-in for ``astrbot.api.star.Context``."""


if __name__ == "__main__":
    raise SystemExit(main())
