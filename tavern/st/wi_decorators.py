# Ported from SillyTavern - public/scripts/world-info.js
# (functions: parseDecorators @4652-4698, isKnownDecorator closure @4658-4669,
#  decorator consumption in getSortedEntries @4627-4634, KNOWN_DECORATORS @100,
#  @@activate / @@dont_activate checks in checkWorldInfo @4875-4883,
#  world_info_position @855-864, extension_prompt_roles @public/script.js:494-498,
#  DEFAULT_DEPTH @96, DEFAULT_WEIGHT @97)
# Copyright (C) 2024 SillyTavern contributors
# Licensed under the GNU Affero General Public License v3.0.
# This file is a modified Python translation; modified on 2026-10-07.
# Upstream: https://github.com/SillyTavern/SillyTavern (commit 06bde939)

"""SillyTavern ``@@`` world info decorators, mirrored function by function.

``parse_decorators`` is the literal port of ``parseDecorators``
(world-info.js:4652-4698). Two counter-intuitive rules are preserved:

1. ``@@@foo`` is a *downgraded* ``@@foo``: ``isKnownDecorator`` strips one ``@``
   before comparing (:4659-4661) and the pushed value is ``line.substring(1)``
   (:4684), so a stored decorator never carries more than two ``@``.
2. An **unknown** ``@@`` line does *not* stop the parse: it sets
   ``fallbacked = true`` (:4687) and the loop keeps going; only the next
   *known* decorator clears the flag and is accepted again. A ``@@@`` line is
   skipped outright while ``fallbacked`` is false (:4679-4681).

Two further consequences of the literal code, both pinned by tests because they
surprise people:

* the content rewrite starts at the **first line that is not ``@@``**, so every
  leading ``@@`` line -- known, unknown or bare ``@@@`` -- disappears from the
  content, and
* if *every* line is a decorator, the loop never rewrites the content and it is
  returned unchanged even though the decorators were collected.

The known set is taken verbatim from ``KNOWN_DECORATORS`` (world-info.js:100) and
holds exactly two entries. ``isKnownDecorator`` uses ``startsWith``, so
``@@activate_only_after=2`` counts as known (prefix match) and is collected
verbatim, while ``@@depth=4`` does not and triggers the fallback path. That is
upstream behaviour, not an oversight here.

Project extensions (opt-in, never part of ``parse_decorators``)
---------------------------------------------------------------

``wi_decorators`` is the bridge the engine will call, so :func:`apply_decorators`
knows a superset of the two upstream decorators through
``EXTENDED_KNOWN_DECORATORS``. These names exist in *no* ST source file; they are
this project's spelling for rules that ST only reaches through other means. Each
one is ``@@name=value``; an unparsable value is logged and the target field is
left untouched ("invalid values fall back to the entry's own value").

============== ============================= =========================== ==========================
decorator      argument                      entry field written         invalid argument
============== ============================= =========================== ==========================
``@@activate`` (exact, no ``=``)              ``constant = True``          -- (``@@activate=1`` is
                                                                          a different string and is
                                                                          ignored, exactly like the
                                                                          engine's ``includes()``)
``@@dont_activate`` (exact, no ``=``)         ``disable = True``           -- (same as above)
``@@depth``    non-negative int              ``depth``                    ignored + warning
``@@position`` ``before|after|ANTop|``        ``position`` (0-7)           ignored + warning
               ``ANBottom|atDepth|EMTop|``
               ``EMBottom|outlet`` or ``0-7``
               (case-insensitive)
``@@role``     ``SYSTEM|USER|ASSISTANT`` or  ``role`` as ``"system"`` /     ignored + warning
               ``0|1|2``                     ``"user"`` / ``"assistant"``
``@@scan_depth`` non-negative int, or         ``scan_depth``               ignored + warning
               ``none|null|unset|`` (empty)  (``None`` for the empty form)
``@@activate_only_after``   non-negative int   ``delay_until_recursion``    ignored + warning
============== ============================= =========================== ==========================

``@@role`` writes the project's **string** role (``tavern/st/prompt.py`` uses
``VALID_ROLES = {"system", "user", "assistant"}``), while ST would store the
numeric ``extension_prompt_roles`` value; :data:`ROLE_VALUES` keeps the mapping
for callers that need the engine's integer. ``@@activate_only_after=N`` is ST's
numeric ``delayUntilRecursion`` level (see ``tools/st-oracle/STATUS.md`` finding
4), mapped onto the project's ``delay_until_recursion`` field.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: JS ``KNOWN_DECORATORS`` (world-info.js:100), verbatim. Prefix-matched with
#: ``startsWith``, see the module docstring.
KNOWN_DECORATORS: tuple[str, ...] = ("@@activate", "@@dont_activate")

#: Project-only extension decorators, accepted by :func:`apply_decorators` only.
PARAMETERIZED_DECORATORS: tuple[str, ...] = (
    "@@depth",
    "@@position",
    "@@role",
    "@@scan_depth",
    "@@activate_only_after",
)

#: The recognition set used by :func:`apply_decorators`: upstream first, then the
#: extensions. ``parse_decorators`` keeps ``KNOWN_DECORATORS`` by default.
EXTENDED_KNOWN_DECORATORS: tuple[str, ...] = KNOWN_DECORATORS + PARAMETERIZED_DECORATORS

#: JS ``DEFAULT_DEPTH`` (world-info.js:96).
DEFAULT_DEPTH = 4
#: JS ``DEFAULT_WEIGHT`` (world-info.js:97), unused here but part of the pair.
DEFAULT_WEIGHT = 100

#: JS ``world_info_position`` (world-info.js:855-864), lower-cased for lookups.
POSITION_VALUES: dict[str, int] = {
    "before": 0,
    "after": 1,
    "antop": 2,
    "anbottom": 3,
    "atdepth": 4,
    "emtop": 5,
    "embottom": 6,
    "outlet": 7,
}

#: JS ``extension_prompt_roles`` (public/script.js:494-498). The project stores
#: the name, this table is the engine's integer.
ROLE_VALUES: dict[str, int] = {"system": 0, "user": 1, "assistant": 2}

_INT_ARGUMENT = re.compile(r"[+-]?[0-9]+")
_SCAN_DEPTH_CLEAR = ("", "none", "null", "unset")


@dataclass
class DecoratorResult:
    """JS ``parseDecorators`` returns ``[decorators, content]`` (:4694)."""

    #: Decorators found, in source order, each with at most two leading ``@``.
    decorators: list[str] = field(default_factory=list)
    #: The content with the leading decorator lines removed.
    content: str = ""


def parse_decorators(
    content: str,
    known_decorators: Sequence[str] = KNOWN_DECORATORS,
) -> DecoratorResult:
    """JS ``parseDecorators`` (world-info.js:4652-4698), line for line.

    ``known_decorators`` is the module constant ``KNOWN_DECORATORS`` (:100) and
    exists only so :func:`apply_decorators` can pass the project's superset. The
    branch order below is the source's.
    """

    def is_known_decorator(data: str) -> bool:
        """JS ``isKnownDecorator`` (world-info.js:4658-4669)."""
        if data.startswith("@@@"):
            data = data[1:]

        for known in known_decorators:
            if data.startswith(known):
                return True
        return False

    if content.startswith("@@"):
        new_content = content
        splited = content.split("\n")
        decorators: list[str] = []
        fallbacked = False

        for i in range(len(splited)):
            if splited[i].startswith("@@"):
                if splited[i].startswith("@@@") and not fallbacked:
                    continue

                if is_known_decorator(splited[i]):
                    decorators.append(
                        splited[i][1:] if splited[i].startswith("@@@") else splited[i]
                    )
                    fallbacked = False
                else:
                    fallbacked = True
            else:
                new_content = "\n".join(splited[i:])
                break
        return DecoratorResult(decorators=decorators, content=new_content)

    return DecoratorResult(decorators=[], content=content)


def strip_decorators(content: str) -> str:
    """The content without its decorator lines, i.e. JS's rewritten ``content``."""
    return parse_decorators(content).content


def _warn_ignored(decorator: str, reason: str) -> None:
    logger.warning("ignoring world info decorator %r: %s", decorator, reason)


def _parse_int(argument: str) -> int | None:
    text = argument.strip()
    if not _INT_ARGUMENT.fullmatch(text):
        return None
    return int(text)


def _parse_position(argument: str) -> int | None:
    text = argument.strip()
    if _INT_ARGUMENT.fullmatch(text):
        value = int(text)
        return value if 0 <= value < len(POSITION_VALUES) else None
    return POSITION_VALUES.get(text.lower())


def _parse_role(argument: str) -> str | None:
    text = argument.strip()
    if _INT_ARGUMENT.fullmatch(text):
        value = int(text)
        for name, number in ROLE_VALUES.items():
            if number == value:
                return name
        return None
    name = text.lower()
    return name if name in ROLE_VALUES else None


def apply_decorators(
    entry_like_mapping: Mapping[str, Any],
    content: str,
) -> tuple[dict[str, Any], str]:
    """Overwrite the decorator controlled entry fields, purely.

    Returns ``(new_entry, stripped_content)``: the input mapping is never
    mutated. ``new_entry`` carries the parsed decorator list under ``decorators``
    (ST stores exactly that key on the entry, world-info.js:4630) so the engine
    can still run the engine's own ``@@activate`` / ``@@dont_activate`` checks
    (:4875-4883).
    """
    result = parse_decorators(content, EXTENDED_KNOWN_DECORATORS)
    entry: dict[str, Any] = dict(entry_like_mapping)
    entry["decorators"] = list(result.decorators)

    for decorator in result.decorators:
        # The two real decorators are matched as whole strings: the engine tests
        # ``entry.decorators.includes('@@activate')``, so ``@@activate=1`` is a
        # different string and stays without effect.
        if decorator == "@@activate":
            entry["constant"] = True
            continue
        if decorator == "@@dont_activate":
            entry["disable"] = True
            continue

        name, separator, argument = decorator.partition("=")
        if not separator:
            _warn_ignored(decorator, "missing '=' argument")
            continue

        if name == "@@depth":
            value = _parse_int(argument)
            if value is None or value < 0:
                _warn_ignored(decorator, "expected a non-negative integer")
                continue
            entry["depth"] = value
        elif name == "@@position":
            position = _parse_position(argument)
            if position is None:
                _warn_ignored(decorator, "expected a position name or an integer 0-7")
                continue
            entry["position"] = position
        elif name == "@@role":
            role = _parse_role(argument)
            if role is None:
                _warn_ignored(decorator, "expected SYSTEM|USER|ASSISTANT or 0|1|2")
                continue
            entry["role"] = role
        elif name == "@@scan_depth":
            if argument.strip().lower() in _SCAN_DEPTH_CLEAR:
                entry["scan_depth"] = None
                continue
            value = _parse_int(argument)
            if value is None or value < 0:
                _warn_ignored(decorator, "expected a non-negative integer or none/null")
                continue
            entry["scan_depth"] = value
        elif name == "@@activate_only_after":
            value = _parse_int(argument)
            if value is None or value < 0:
                _warn_ignored(decorator, "expected a non-negative recursion level")
                continue
            entry["delay_until_recursion"] = value
        else:
            _warn_ignored(decorator, "unknown decorator")

    return entry, result.content


__all__: Sequence[str] = [
    "DEFAULT_DEPTH",
    "DEFAULT_WEIGHT",
    "EXTENDED_KNOWN_DECORATORS",
    "KNOWN_DECORATORS",
    "PARAMETERIZED_DECORATORS",
    "POSITION_VALUES",
    "ROLE_VALUES",
    "DecoratorResult",
    "apply_decorators",
    "parse_decorators",
    "strip_decorators",
]
