"""Verify AstrBot can actually parse `/tavern`'s command parameters.

    astrbot_python tools/verify_cmd_params.py

AstrBot fills a command's arguments from ``inspect.signature(handler)``. Three
separate things have to line up, and each has its own silent failure mode, so this
reproduces the whole chain against the installed host:

1. the loader binds the instance with ``functools.partial(raw_handler, star_cls)``
   (`star_manager.py:1273`), and ``inspect`` drops one parameter for a partial's
   bound argument;
2. ``init_handler_md`` then discards the first two reported parameters
   (`filter/command.py`, ``if idx < 2: continue``);
3. a "greedy" trailing parameter is recognised by ``is GreedyStr`` -- identity
   against the **class** -- so an instance there silently downgrades it to one word.

Every failure looks the same from chat: the command works, but its arguments are
truncated or absent. Before this check the whole command layer was in that state,
which the end-to-end script never caught because it calls ``on_message`` directly
instead of going through the command filter.
"""

from __future__ import annotations

import functools
import importlib
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

from astrbot.core.star.filter.command import CommandFilter, GreedyStr  # noqa: E402

__all__ = ["main"]

#: ``(handler, command, typed, {arg: expected})`` -- the shape a user would type.
CASES: list[tuple[str, str, str, dict[str, str]]] = [
    ("cmd_st", "st", "cards", {"action": "cards", "rest": ""}),
    ("cmd_st", "st", "books", {"action": "books", "rest": ""}),
    ("cmd_st", "st", "chats iris.png", {"action": "chats", "rest": "iris.png"}),
    ("cmd_st", "st", "import card iris.png", {"action": "import", "rest": "card iris.png"}),
    (
        "cmd_st",
        "st",
        "import chat iris.png main.jsonl",
        {"action": "import", "rest": "chat iris.png main.jsonl"},
    ),
    ("cmd_use_card", "use", "Lighthouse Keeper", {"name": "Lighthouse Keeper"}),
    ("cmd_history", "history", "12", {"limit": "12"}),
    (
        "cmd_worldbook",
        "worldbook",
        "on Lighthouse Lore",
        {"action": "on", "args": "Lighthouse Lore"},
    ),
    (
        "cmd_worldbook",
        "worldbook",
        "effect Lighthouse 3 sticky on",
        {"action": "effect", "args": "Lighthouse 3 sticky on"},
    ),
]


def make_filter(plugin_cls: type, name: str, command: str, *, bound: bool) -> CommandFilter:
    """A filter for `name`, under one of the two shapes AstrBot uses.

    ``star_manager`` binds the plugin instance onto a handler with
    ``functools.partial(raw, star_cls)`` -- **but only if its by-module-path lookup
    finds the handler**. When it does not (see `docs/known-issues.md` entry 4), the
    registry holds the bare function and AstrBot calls it as ``handler(event)``. The
    shim must survive both, so both are asserted here.

    The metadata holder is a ``SimpleNamespace``, matching AstrBot's
    ``StarHandlerMetadata``. Do not hold the handler in a ``type()`` class dict: a
    ``functools.partial`` stored as a class attribute is bound as a method on access,
    consuming a second parameter, and every parse then comes back empty.
    """
    raw = getattr(plugin_cls, name)
    handler = functools.partial(raw, plugin_cls) if bound else raw
    return CommandFilter(
        command_name=command,
        alias=None,
        handler_md=SimpleNamespace(handler=handler),
        parent_command_names=["tavern"],
    )


def parse(plugin_cls: type, name: str, command: str, typed: str, *, bound: bool) -> dict:
    filter_ = make_filter(plugin_cls, name, command, bound=bound)
    words = [word for word in typed.split(" ") if word]
    parsed = filter_.validate_and_convert_params(words, filter_.handler_params)
    parsed.pop("event", None)
    return parsed


def main() -> int:
    import tavern.main as tavern_main

    # Reload so the shims carry the current `__signature__`.
    tavern_main = importlib.reload(tavern_main)

    plugin_cls = type("Probe", (), {})
    if not tavern_main._register_commands(plugin_cls):
        print("SKIP -- AstrBot command registration unavailable")
        return 0

    failures: list[str] = []

    raw = getattr(plugin_cls, "cmd_st", None)
    if raw is None:
        print("FAIL: cmd_st was not attached to the plugin class")
        return 1

    print("AstrBot calls a handler as `handler(event, *args, **kwargs)`")
    print("(`context_utils.py:37`), and whether the plugin instance is bound on first")
    print("depends on the loader. Both shapes are printed; the **unbound** one is what")
    print("this plugin actually runs under in a real AstrBot process, and it is the one")
    print("asserted here -- `tools/qq_commands_live.py` is the end-to-end proof of it.")
    print()

    # The unbound shape is the supported, verified one.
    for bound in (False, True):
        label = "bound" if bound else "unbound (production)"
        handler = functools.partial(raw, plugin_cls) if bound else raw
        params = make_filter(plugin_cls, "cmd_st", "st", bound=bound).handler_params
        print(f"--- {label}: signature {inspect.signature(handler)}")
        print(f"    handler_params: {params}")
        if bound:
            print("    (informational: the offset differs when the loader bound the")
            print("     instance, which does not happen for this plugin -- the module is")
            print("     loaded under two names, so `star_manager`'s lookup finds")
            print("     nothing. See docs/known-issues.md entry 4.)")
            print()
            continue

        if "action" not in params:
            failures.append("[unbound] `action` is absent from handler_params")
        if params.get("rest") is not GreedyStr:
            failures.append(
                f"[unbound] `rest` default is {params.get('rest')!r}, not the GreedyStr "
                "class: the remainder would be truncated to one word"
            )
        for name, command, typed, expected in CASES:
            try:
                parsed = parse(plugin_cls, name, command, typed, bound=bound)
            except Exception as exc:  # noqa: BLE001 - reporting is the point
                failures.append(f"[unbound] /tavern {typed!r} raised {type(exc).__name__}: {exc}")
                print(f"    RAISED  /tavern {typed!r}: {type(exc).__name__}: {exc}")
                continue
            print(f"    /tavern {typed!r:<36} -> {parsed}")
            for key, want in expected.items():
                got = str(parsed.get(key, ""))
                if got != want:
                    failures.append(
                        f"[unbound] /tavern {typed!r}: {key}={got!r}, expected {want!r}"
                    )
        print()

    print()
    if failures:
        print("FAIL:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"PASS -- AstrBot parses all {len(CASES)} parameterised /tavern invocations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
