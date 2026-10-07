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


def make_filter(plugin_cls: type, name: str, command: str) -> CommandFilter:
    """A filter for `name`, bound exactly the way the loader binds it.

    The metadata holder is a ``SimpleNamespace``, matching AstrBot's
    ``StarHandlerMetadata``. Do not hold the handler in a ``type()`` class dict: a
    ``functools.partial`` stored as a class attribute is bound as a method on access,
    consuming a second parameter, and every parse then comes back empty.
    """
    bound = functools.partial(getattr(plugin_cls, name), plugin_cls)
    return CommandFilter(
        command_name=command,
        alias=None,
        handler_md=SimpleNamespace(handler=bound),
        parent_command_names=["tavern"],
    )


def parse(plugin_cls: type, name: str, command: str, typed: str) -> dict:
    filter_ = make_filter(plugin_cls, name, command)
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

    bound = functools.partial(raw, plugin_cls)
    params = make_filter(plugin_cls, "cmd_st", "st").handler_params
    print(f"published signature : {inspect.signature(raw)}")
    print(f"bound signature     : {inspect.signature(bound)}")
    print(f"handler_params      : {params}")
    # AstrBot's own test is `param_type_or_default_val is GreedyStr` -- identity
    # against the class -- so that is what is asserted. An instance there would make
    # the parameter one-word, and the only symptom would be truncated text.
    if params.get("rest") is not GreedyStr:
        failures.append(
            f"`rest` default is {params.get('rest')!r}, not the GreedyStr class: "
            "the remainder would be truncated to one word"
        )
    print()

    for name, command, typed, expected in CASES:
        try:
            parsed = parse(plugin_cls, name, command, typed)
        except Exception as exc:  # noqa: BLE001 - reporting is the point
            failures.append(f"/tavern {typed!r} raised {type(exc).__name__}: {exc}")
            print(f"  RAISED  /tavern {typed!r}: {type(exc).__name__}: {exc}")
            continue
        print(f"  /tavern {typed!r:<38} -> {parsed}")
        for key, want in expected.items():
            got = str(parsed.get(key, ""))
            if got != want:
                failures.append(f"/tavern {typed!r}: {key}={got!r}, expected {want!r}")

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
