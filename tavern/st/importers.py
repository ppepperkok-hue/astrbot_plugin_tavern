# Ported from SillyTavern - src/endpoints/characters.js (convertWorldInfoToCharacterBook) and
# public/scripts/world-info.js (convertCharacterBook, convertAgnaiMemoryBook, convertRisuLorebook)
# Copyright (C) 2024 SillyTavern contributors
# Licensed under the GNU Affero General Public License v3.0.
# This file is a modified Python translation; modified on 2026-10-07.
# Upstream: https://github.com/SillyTavern/SillyTavern (commit 06bde939)

"""Import side of the SillyTavern format layer.

Everything here turns a *foreign* payload (a character card, an embedded
character book, a standalone lorebook, a chat transcript) into the runtime
objects this plugin uses: :class:`~tavern.st.cards.CharacterCard`,
:class:`~tavern.st.worldbook.WorldBook` and plain message dicts.

Why this module exists
----------------------

``tavern/st/worldbook.py`` can only read the *native* SillyTavern shape
(``{"entries": {"<uid>": {...}}}``) and ``tavern/st/cards.py`` caps a card at
its V1/V2/V3 envelope. Neither understands the shapes the rest of the ecosystem
actually ships:

* a V2 card whose embedded ``data.character_book`` uses **snake_case** fields
  while the runtime entry model uses **camelCase** (``convertCharacterBook``),
* the same mapping in reverse (``convertWorldInfoToCharacterBook``),
* a standalone ``{"spec": "lorebook_v3", "data": {...}}`` file (Character Card
  V3 spec, section "Exporting the Lorebook"),
* Agnai memory books (``{"kind": "memory", ...}``, ``convertAgnaiMemoryBook``),
* Risu lorebooks (``{"type": "risu", ...}``, ``convertRisuLorebook``),
* NovelAI lorebooks (``{"lorebookVersion": ..., ...}``, ``convertNovelLorebook``).

Field names of the two entry dialects
-------------------------------------

===========================================  ==============================
V2/V3 ``character_book`` entry (snake_case)  runtime entry (camelCase)
===========================================  ==============================
``id``                                       ``uid``
``keys``                                     ``key``
``secondary_keys``                           ``keysecondary``
``insertion_order``                          ``order``
``enabled``                                  ``disable`` (inverted)
``position`` (``before_char``/``after_char``)  ``position`` (number 0..7)
``use_regex``                                (dropped: ST keys carry regexes)
``addMemo``                                  (dropped: ST derives it)
everything else                              ``extensions.<snake_case>``
===========================================  ==============================

:data:`FIELD_MAPPING` is the authoritative table (**42 rows**, including the
``entries`` container itself); it is derived from ``endpoints_characters.js:663-722``
(native -> V2) and ``world-info.js:5617-5674`` (V2 -> native), and
``tests/test_importers.py`` asserts that neither JS function touches a field the
table does not name. Two upstream carries are deliberately *not* table rows
because they have no V2 counterpart at all: ``addMemo`` (ST derives it with
``!!entry.comment``) and ``use_regex`` (ST writes a constant ``true``, then drops
it again on import -- ST keys carry their own ``/re/flags``).

Error handling
--------------

Every failure raises a subclass of :class:`~builtins.ValueError` with a
**Chinese, human readable** message, because the plugin surfaces these to QQ
users. A malformed card must never leak a bare ``KeyError`` / ``TypeError``.

Only the standard library is used. ``PyYAML`` is optional and only needed when a
``.yaml`` / ``.yml`` payload or card is imported.
"""

from __future__ import annotations

import json
import logging
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tavern.st.cards import (
    CharacterCard,
    CharacterCardError,
    card_from_dict,
    card_from_png,
    load_card,
)
from tavern.st.worldbook import (
    POSITION_AFTER_CHAR,
    POSITION_AT_DEPTH,
    POSITION_BEFORE_CHAR,
    WorldBook,
    WorldInfoEntry,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Spec constants
# ---------------------------------------------------------------------------

#: ``spec`` value of the V2 envelope (``CharacterCard.data``).
SPEC_V2 = "chara_card_v2"
#: ``spec`` value of the V3 envelope.
SPEC_V3 = "chara_card_v3"
#: ``spec`` value of a standalone V3 lorebook file.
SPEC_LOREBOOK_V3 = "lorebook_v3"

#: The two values V2/V3 lorebooks allow in ``entry.position``.
V2_POSITION_BEFORE_CHAR = "before_char"
V2_POSITION_AFTER_CHAR = "after_char"

#: ``extension_prompt_roles`` (``script.js:494-498``); the fallback for ``role``.
ROLE_SYSTEM = 0
ROLE_USER = 1
ROLE_ASSISTANT = 2

#: ``world_info_position`` (``world-info.js:855-864``) as Python constants, so the
#: numeric spelling used by the full 8-value enum is readable in one place.
WI_POSITION_BEFORE = POSITION_BEFORE_CHAR
WI_POSITION_AFTER = POSITION_AFTER_CHAR
WI_POSITION_AT_DEPTH = POSITION_AT_DEPTH

#: Key inside :attr:`WorldBook.extensions` that keeps the untouched foreign
#: payload, mirroring ``result.originalData = characterBook``
#: (``world-info.js:5618``). It is the only way to get a lossless V2 round trip
#: for cards, because the runtime entry model cannot represent a few V2 keys.
ORIGINAL_DATA_KEY = "original_data"

#: ``newWorldInfoEntryTemplate`` defaults (``world-info.js:4127``, derived from
#: ``newWorldInfoEntryDefinition`` at ``world-info.js:4082``). Used on export to
#: avoid materialising keys the imported payload never had.
RUNTIME_DEFAULTS: dict[str, Any] = {
    "key": [],
    "keysecondary": [],
    "comment": "",
    "content": "",
    "constant": False,
    "vectorized": False,
    "selective": True,
    "selectiveLogic": 0,
    "addMemo": False,
    "order": 100,
    "position": 0,
    "disable": False,
    "ignoreBudget": False,
    "excludeRecursion": False,
    "preventRecursion": False,
    "matchPersonaDescription": False,
    "matchCharacterDescription": False,
    "matchCharacterPersonality": False,
    "matchCharacterDepthPrompt": False,
    "matchScenario": False,
    "matchCreatorNotes": False,
    "delayUntilRecursion": False,
    "probability": 100,
    "useProbability": True,
    "depth": 4,
    "outletName": "",
    "group": "",
    "groupOverride": False,
    "groupWeight": 100,
    "scanDepth": None,
    "caseSensitive": None,
    "matchWholeWords": None,
    "useGroupScoring": None,
    "automationId": "",
    "role": ROLE_SYSTEM,
    "sticky": None,
    "cooldown": None,
    "delay": None,
    "triggers": [],
}

#: The 31 ``extensions.*`` columns ``convertWorldInfoToCharacterBook`` writes
#: (``endpoints_characters.js:682-715``), in upstream order. ``position`` is the
#: duplicate: V2 carries it both as a two-value string and inside ``extensions``
#: as the full 0..7 enum. 11 core rows + 31 extension rows + the ``entries``
#: container row make up the 42 rows of :data:`FIELD_MAPPING`.
V2_EXTENSION_COLUMNS: tuple[str, ...] = (
    "position",
    "exclude_recursion",
    "display_index",
    "probability",
    "useProbability",
    "depth",
    "selectiveLogic",
    "outlet_name",
    "group",
    "group_override",
    "group_weight",
    "prevent_recursion",
    "delay_until_recursion",
    "scan_depth",
    "match_whole_words",
    "use_group_scoring",
    "case_sensitive",
    "automation_id",
    "role",
    "vectorized",
    "sticky",
    "cooldown",
    "delay",
    "match_persona_description",
    "match_character_description",
    "match_character_personality",
    "match_character_depth_prompt",
    "match_scenario",
    "match_creator_notes",
    "triggers",
    "ignore_budget",
)

#: ``originalWIDataKeyMap`` (``world-info.js:2687-2724``) read backwards:
#: ``extensions.<snake_case>`` -> camelCase runtime field. Only the 27 rows the
#: upstream map itself carries; ``group`` and ``outlet_name`` are extension-only
#: fields in the runtime model (``WorldInfoEntry`` has no attribute for them), so
#: they are absent here on purpose and are read straight out of ``extensions``.
RUNTIME_TO_V2_EXTENSIONS: dict[str, str] = {
    "excludeRecursion": "exclude_recursion",
    "preventRecursion": "prevent_recursion",
    "delayUntilRecursion": "delay_until_recursion",
    "displayIndex": "display_index",
    "depth": "depth",
    "probability": "probability",
    "position": "position",
    "role": "role",
    "matchWholeWords": "match_whole_words",
    "useGroupScoring": "use_group_scoring",
    "caseSensitive": "case_sensitive",
    "matchPersonaDescription": "match_persona_description",
    "matchCharacterDescription": "match_character_description",
    "matchCharacterPersonality": "match_character_personality",
    "matchCharacterDepthPrompt": "match_character_depth_prompt",
    "matchScenario": "match_scenario",
    "matchCreatorNotes": "match_creator_notes",
    "scanDepth": "scan_depth",
    "automationId": "automation_id",
    "vectorized": "vectorized",
    "groupOverride": "group_override",
    "groupWeight": "group_weight",
    "sticky": "sticky",
    "cooldown": "cooldown",
    "delay": "delay",
    "triggers": "triggers",
    "ignoreBudget": "ignore_budget",
    "useProbability": "useProbability",
    # ``selectiveLogic`` is the one extension-path field whose key is *not*
    # snake_cased: ``originalWIDataKeyMap`` maps it to the bare ``selectiveLogic``
    # (``world-info.js:2692``), and ``convertCharacterBook`` reads
    # ``extensions.selectiveLogic`` (``world-info.js:5646``).
    "selectiveLogic": "selectiveLogic",
}

#: Every native camelCase row key that maps onto a :class:`WorldInfoEntry`
#: attribute: the 41 rows of :data:`FIELD_MAPPING` whose ``runtime`` column names a
#: native field (``entries`` names the container, not a field), minus ``uid`` and
#: plus ``addMemo`` / ``key_vector``, which upstream writes but this plugin's entry
#: model has no attribute for.
RUNTIME_CORE_FIELDS: tuple[str, ...] = (
    "key",
    "keysecondary",
    "comment",
    "content",
    "constant",
    "selective",
    "order",
    "disable",
    "position",
    "vectorized",
    "excludeRecursion",
    "preventRecursion",
    "delayUntilRecursion",
    "probability",
    "useProbability",
    "depth",
    "selectiveLogic",
    "outletName",
    "group",
    "groupOverride",
    "groupWeight",
    "scanDepth",
    "caseSensitive",
    "matchWholeWords",
    "useGroupScoring",
    "automationId",
    "role",
    "sticky",
    "cooldown",
    "delay",
    "matchPersonaDescription",
    "matchCharacterDescription",
    "matchCharacterPersonality",
    "matchCharacterDepthPrompt",
    "matchScenario",
    "matchCreatorNotes",
    "triggers",
    "ignoreBudget",
    "displayIndex",
    "addMemo",
    "key_vector",
)


@dataclass(frozen=True)
class FieldMapping:
    """One row of the two-way V2 <-> runtime entry mapping.

    ``kind`` is one of ``"core"`` (hand written in both JS converters),
    ``"extension"`` (carried under the V2 ``extensions`` object) or ``"book"``
    (the lorebook container itself).
    """

    v2: str
    runtime: str
    kind: str
    note: str


#: **The** 42 field bidirectional map (including the ``entries`` container row).
#: Rows follow ``endpoints_characters.js:670-715`` (native -> V2) so the order is
#: auditable against the upstream literal.
FIELD_MAPPING: tuple[FieldMapping, ...] = (
    # --- container -------------------------------------------------------
    FieldMapping("entries", "entries", "book", "V2 list  <->  uid-keyed map"),
    # --- carried verbatim on both sides ----------------------------------
    FieldMapping("id", "uid", "core", "absent id falls back to the array index"),
    FieldMapping("keys", "key", "core", "renamed"),
    FieldMapping("secondary_keys", "keysecondary", "core", "renamed"),
    FieldMapping("comment", "comment", "core", "identical"),
    FieldMapping("content", "content", "core", "identical"),
    FieldMapping("constant", "constant", "core", "|| false on import"),
    FieldMapping("selective", "selective", "core", "|| false (see module notes)"),
    FieldMapping("insertion_order", "order", "core", "renamed"),
    # --- inverted / re-encoded -------------------------------------------
    FieldMapping("enabled", "disable", "core", "inverted: disable = !enabled"),
    FieldMapping(
        "position",
        "position",
        "core",
        "'before_char' <-> 0, 'after_char' (and anything else) <-> 1",
    ),
    # --- V2 extensions, snake_case around a camelCase core ---------------
    FieldMapping("extensions.position", "position", "extension", "numeric enum wins on import"),
    FieldMapping("extensions.exclude_recursion", "excludeRecursion", "extension", "?? false"),
    FieldMapping("extensions.prevent_recursion", "preventRecursion", "extension", "?? false"),
    FieldMapping(
        "extensions.delay_until_recursion", "delayUntilRecursion", "extension", "?? false"
    ),
    FieldMapping("extensions.display_index", "displayIndex", "extension", "?? array index"),
    FieldMapping("extensions.probability", "probability", "extension", "?? 100"),
    FieldMapping("extensions.useProbability", "useProbability", "extension", "?? true"),
    FieldMapping("extensions.depth", "depth", "extension", "?? 4"),
    FieldMapping("extensions.selectiveLogic", "selectiveLogic", "extension", "not snake_cased"),
    FieldMapping("extensions.outlet_name", "outletName", "extension", "?? ''"),
    FieldMapping("extensions.group", "group", "extension", "?? ''"),
    FieldMapping("extensions.group_override", "groupOverride", "extension", "?? false"),
    FieldMapping("extensions.group_weight", "groupWeight", "extension", "?? 100"),
    FieldMapping("extensions.scan_depth", "scanDepth", "extension", "?? null"),
    FieldMapping("extensions.match_whole_words", "matchWholeWords", "extension", "?? null"),
    FieldMapping("extensions.use_group_scoring", "useGroupScoring", "extension", "?? false"),
    FieldMapping("extensions.case_sensitive", "caseSensitive", "extension", "?? null"),
    FieldMapping("extensions.automation_id", "automationId", "extension", "?? ''"),
    FieldMapping("extensions.role", "role", "extension", "?? 0 (SYSTEM)"),
    FieldMapping("extensions.vectorized", "vectorized", "extension", "?? false"),
    FieldMapping("extensions.sticky", "sticky", "extension", "?? null"),
    FieldMapping("extensions.cooldown", "cooldown", "extension", "?? null"),
    FieldMapping("extensions.delay", "delay", "extension", "?? null"),
    FieldMapping(
        "extensions.match_persona_description", "matchPersonaDescription", "extension", "?? false"
    ),
    FieldMapping(
        "extensions.match_character_description",
        "matchCharacterDescription",
        "extension",
        "?? false",
    ),
    FieldMapping(
        "extensions.match_character_personality",
        "matchCharacterPersonality",
        "extension",
        "?? false",
    ),
    FieldMapping(
        "extensions.match_character_depth_prompt",
        "matchCharacterDepthPrompt",
        "extension",
        "?? false",
    ),
    FieldMapping("extensions.match_scenario", "matchScenario", "extension", "?? false"),
    FieldMapping("extensions.match_creator_notes", "matchCreatorNotes", "extension", "?? false"),
    FieldMapping("extensions.triggers", "triggers", "extension", "?? []"),
    FieldMapping("extensions.ignore_budget", "ignoreBudget", "extension", "?? false"),
)

#: ``extensions`` field that the runtime model keeps a copy of, keyed by
#: camelCase name, so ``WorldInfoBuffer``'s view (``world-info.js`` field names)
#: can read the ``match*`` flags straight out of an imported card book.
_BUFFER_VISIBLE_EXTENSIONS: tuple[str, ...] = (
    "displayIndex",
    "excludeRecursion",
    "preventRecursion",
    "delayUntilRecursion",
    "selectiveLogic",
    "matchWholeWords",
    "useGroupScoring",
    "caseSensitive",
    "scanDepth",
    "automationId",
    "vectorized",
    "groupOverride",
    "groupWeight",
    "sticky",
    "cooldown",
    "delay",
    "triggers",
    "ignoreBudget",
    "matchPersonaDescription",
    "matchCharacterDescription",
    "matchCharacterPersonality",
    "matchCharacterDepthPrompt",
    "matchScenario",
    "matchCreatorNotes",
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ImportError(ValueError):
    """Base class for every import failure in this module (message in Chinese)."""


class CharacterCardImportError(ImportError):
    """The payload could not be recognised as a character card."""


class CharacterBookImportError(ImportError):
    """A card has no usable embedded ``character_book`` (or it is malformed)."""


class LorebookImportError(ImportError):
    """The payload is not a lorebook this module understands."""


class OptionalDependencyError(ImportError):
    """A payload needs an optional dependency (only PyYAML, for now)."""


# ---------------------------------------------------------------------------
# Small coercion helpers
# ---------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    """True for int / float, but never for bool (JS ``typeof 'number'``)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _int_or(value: Any, default: int) -> int:
    """``Number(value)`` with a fallback: ``None``/``''``/garbage -> default."""
    if value is None or isinstance(value, bool) or value == "":
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else int(value)
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _bool_or(value: Any, default: bool) -> bool:
    """JS truthiness for the values that actually show up in lorebook JSON.

    ``0``/``''``/``None``/``false``/``[]``/``{}`` are falsy, everything else is
    truthy -- so the string ``"false"`` (which a JS ``entry.selective || false``
    would happily treat as true) stays true here as well.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if _is_number(value):
        return value != 0
    if isinstance(value, str):
        return value != ""
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return bool(value)


def _str_or(value: Any, default: str = "") -> str:
    """Coerce a scalar into a string without producing ``"None"``."""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if _is_number(value):
        return str(value)
    return default


def _get_int(container: Any, key: str, default: int) -> int:
    """``_int_or`` over a possibly-missing mapping key."""
    if not isinstance(container, Mapping):
        return default
    return _int_or(container.get(key), default)


def _get_bool(container: Any, key: str, default: bool) -> bool:
    """``_bool_or`` over a possibly-missing mapping key."""
    if not isinstance(container, Mapping):
        return default
    return _bool_or(container.get(key), default)


def _get_str(container: Any, key: str, default: str = "") -> str:
    """``_str_or`` over a possibly-missing mapping key."""
    if not isinstance(container, Mapping):
        return default
    return _str_or(container.get(key), default)


def _as_mapping(value: Any) -> dict[str, Any]:
    """Return ``value`` as a plain dict, or ``{}`` when it is not a mapping."""
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _as_text_list(value: Any) -> list[str]:
    """V2 ``keys`` / ``secondary_keys`` are string arrays; be forgiving anyway."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [_str_or(item) for item in value if item is not None and _str_or(item) != ""]
    return [_str_or(value)]


def _as_key_list(value: Any) -> list[str]:
    """Runtime ``key`` / ``keysecondary``: identical tolerance to :func:`_as_text_list`."""
    return _as_text_list(value)


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------


def v2_position_to_number(position: Any, extensions_position: Any = None) -> int:
    """Resolve the runtime ``position`` value of one V2 entry.

    ``convertCharacterBook`` (``world-info.js:5636``)::

        position: entry.extensions?.position
            ?? (entry.position === 'before_char' ? world_info_position.before
                                                 : world_info_position.after)

    Note the asymmetry: the *string* form only ever yields ``0`` or ``1``, while
    the ``extensions.position`` escape hatch carries the full ``0..7`` enum that
    ``convertWorldInfoToCharacterBook`` writes for native ST books
    (``endpoints_characters.js:684``).
    """
    if extensions_position is not None:
        return _int_or(extensions_position, POSITION_AFTER_CHAR)
    if isinstance(position, str) and position.strip() != "":
        stripped = position.strip()
        # A numeric string (``"4"``) is still the full-enum spelling.
        if stripped.lstrip("+-").isdigit():
            return _int_or(stripped, POSITION_AFTER_CHAR)
        return POSITION_BEFORE_CHAR if stripped == V2_POSITION_BEFORE_CHAR else POSITION_AFTER_CHAR
    if position is None or position == "":
        # JS ``undefined === 'before_char'`` is false -> after.
        return POSITION_AFTER_CHAR
    if _is_number(position):
        return int(position)
    return POSITION_AFTER_CHAR


def number_to_v2_position(position: Any) -> str:
    """Reverse of the *string* half: ``0`` -> ``before_char``, anything else -> ``after_char``.

    This mirrors ``data.entries[uid].position == 0 ? 'before_char' : 'after_char'``
    (``world-info.js:3434``). The full enum is still preserved losslessly inside
    ``extensions.position``, which is where ``convertWorldInfoToCharacterBook``
    puts it.
    """
    if position is None or position == "":
        return V2_POSITION_AFTER_CHAR
    return (
        V2_POSITION_BEFORE_CHAR
        if _int_or(position, POSITION_AFTER_CHAR) == 0
        else (V2_POSITION_AFTER_CHAR)
    )


# ---------------------------------------------------------------------------
# convertCharacterBook -- V2 character book entry -> runtime entry
# ---------------------------------------------------------------------------


def convert_character_book_entry(
    entry: Mapping[str, Any],
    fallback_id: int = 0,
    *,
    index: int | None = None,
) -> WorldInfoEntry:
    """Translate **one** V2/V3 ``character_book.entries[]`` row into a runtime entry.

    Faithful to ``convertCharacterBook`` (``world-info.js:5617-5674``). Two
    documented deviations:

    * the runtime ``WorldInfoEntry`` has no ``addMemo`` field, so that legacy
      bookkeeping flag is dropped;
    * ``selective`` is ``entry.selective || false`` (``world-info.js:5634``), not
      the native default of ``true`` -- a V2 card that omits the field is
      therefore **not** selective, while an entry created natively in ST is.

    ``fallback_id`` is SillyTavern's "not in the spec, but this is needed to find
    the entry in the original data" index (``world-info.js:5622-5624``);
    ``index`` is the array position used by ``display_index``.
    """
    raw = dict(entry) if isinstance(entry, Mapping) else {}
    where = index if index is not None else fallback_id
    runtime = _native_row_to_runtime(_runtime_row_v2(raw, fallback_id, where), fallback_id)
    # ``addMemo`` is the only runtime-visible extension ST synthesises rather than
    # copies; keep it out of ``extensions`` so an export cannot echo it back.
    runtime.extensions.pop("addMemo", None)
    return runtime


def _normalise_entries(raw_entries: Any) -> list[dict[str, Any]]:
    """Coerce any of the accepted ``entries`` shapes into a list of entry dicts."""
    if isinstance(raw_entries, Mapping):
        rows: list[dict[str, Any]] = []
        for key, value in raw_entries.items():
            if not isinstance(value, Mapping):
                continue
            row = dict(value)
            if "id" not in row:
                row["id"] = _int_or(key, len(rows))
            rows.append(row)
        return rows
    if isinstance(raw_entries, (list, tuple)):
        return [dict(item) for item in raw_entries if isinstance(item, Mapping)]
    return []


def convert_character_book(character_book: Mapping[str, Any]) -> WorldBook:
    """Translate a V2/V3 ``character_book`` object into a :class:`WorldBook`.

    Mirrors ``convertCharacterBook`` (``world-info.js:5617``) including its
    ``originalData`` bookkeeping, which is kept under
    :data:`ORIGINAL_DATA_KEY` inside :attr:`WorldBook.extensions`. ``uid`` is
    taken from ``entry.id`` and falls back to the array index; the result is a
    ``uid``-keyed book, exactly like a native ``worlds/*.json`` file.

    Raises
    ------
    CharacterBookImportError
        ``character_book`` is not a mapping, or its ``entries`` is neither a list
        nor a ``{uid: entry}`` map.
    """
    if not isinstance(character_book, Mapping):
        raise CharacterBookImportError("角色卡里的内嵌世界书不是对象，无法解析。")

    raw_entries = character_book.get("entries")
    if raw_entries is None:
        raw_entries = []
    if not isinstance(raw_entries, (list, tuple, Mapping)):
        raise CharacterBookImportError(
            "角色卡内嵌世界书的 entries 既不是数组也不是对象，无法解析。"
        )

    book = WorldBook(
        name=_str_or(character_book.get("name")),
        description=_str_or(character_book.get("description")),
        entries=[
            convert_character_book_entry(entry, fallback_id=index, index=index)
            for index, entry in enumerate(_normalise_entries(raw_entries))
        ],
        scan_depth=character_book.get("scan_depth"),
        token_budget=character_book.get("token_budget"),
        recursive_scanning=_bool_or(character_book.get("recursive_scanning"), False),
        extensions={},
    )
    book.scan_depth = _optional_number(book.scan_depth)
    book.token_budget = _optional_number(book.token_budget)
    book.extensions[ORIGINAL_DATA_KEY] = dict(character_book)
    return book


def _optional_number(value: Any) -> int | None:
    """Keep ``None`` as ``None``; everything else becomes an int."""
    if value is None or value == "":
        return None
    return _int_or(value, 0)


# ---------------------------------------------------------------------------
# Standalone lorebook dialects
# ---------------------------------------------------------------------------


def _book_from_runtime_entries(
    entries: Mapping[str | int, Mapping[str, Any]],
    *,
    name: str = "",
    description: str = "",
    scan_depth: int | None = None,
    token_budget: int | None = None,
    recursive_scanning: bool = False,
    extensions: Mapping[str, Any] | None = None,
    original_data: Any = None,
) -> WorldBook:
    """Build a :class:`WorldBook` from a ``{uid: native-entry}`` map.

    Shared by every converter in this module: each one only has to produce native
    (camelCase) rows, and this function turns them into runtime entries. Rows are
    handed to :func:`_native_row_to_runtime`, never to
    :func:`convert_character_book_entry`, because the native spelling and the V2
    spelling disagree on several defaults (``selective`` above all).
    """
    book = WorldBook(
        name=name,
        description=description,
        entries=[
            _native_row_to_runtime(entry, _int_or(entry.get("uid", uid), index))
            for index, (uid, entry) in enumerate(entries.items())
        ],
        scan_depth=scan_depth,
        token_budget=token_budget,
        recursive_scanning=recursive_scanning,
        extensions=dict(extensions or {}),
    )
    if original_data is not None:
        book.extensions[ORIGINAL_DATA_KEY] = original_data
    return book


def _native_entry(**overrides: Any) -> dict[str, Any]:
    """A native (camelCase) entry row: ``newWorldInfoEntryTemplate`` plus overrides.

    Small helpers above and below read these rows with defaults, so leaving a
    field out is equivalent to passing the template default.
    """
    return dict(overrides)


#: Extra per-entry detail the runtime model carries explicitly in dedicated
#: fields and that hence has no module-level ``RUNTIME_DEFAULTS`` row.
_EXTRA_RUNTIME_DEFAULTS: dict[str, Any] = {
    "selectiveLogic": 0,
    "outletName": "",
    "groupOverride": False,
    "groupWeight": 100,
    "ignoreBudget": False,
    "excludeRecursion": False,
    "preventRecursion": False,
    "delayUntilRecursion": False,
    "displayIndex": 0,
}

#: Fields whose value a native row carries as a plain data field, not as a
#: dedicated runtime attribute; they live in ``WorldInfoEntry.extensions``.
_MATCH_FLAG_FIELDS: tuple[str, ...] = (
    "matchPersonaDescription",
    "matchCharacterDescription",
    "matchCharacterPersonality",
    "matchCharacterDepthPrompt",
    "matchScenario",
    "matchCreatorNotes",
)

#: Camel ``extensions`` keys that a V2 book may carry (``convertWorldInfoToCharacterBook``
#: writes all of them) but that ``WorldInfoEntry`` has no attribute for. They are
#: still mirrored into ``extensions`` so the exporter can put them back untouched.
_EXTENSION_ONLY_FIELDS: tuple[str, ...] = ("outletName", "addMemo", "key_vector")


def _runtime_row_v2(entry: Mapping[str, Any], index: int, where: int) -> dict[str, Any]:
    """Convert **one V2 snake_case entry** into a native camelCase row.

    This is the explicit, auditable form of the 41 per-entry rows of
    :data:`FIELD_MAPPING`. The result is shaped like the object
    ``convertWorldInfoToCharacterBook`` (``endpoints_characters.js:663``) pushes
    into ``result.entries``: core fields at the top level, the rest spread
    inside ``extensions`` -- here additionally mirrored as camelCase keys so the
    runtime never has to know which dialect an entry came from.
    """
    raw = dict(entry)
    raw_extensions = _as_mapping(raw.get("extensions"))
    position = v2_position_to_number(raw.get("position"), raw_extensions.get("position"))

    extensions = dict(raw_extensions)
    for camel, snake in (
        ("matchPersonaDescription", "match_persona_description"),
        ("matchCharacterDescription", "match_character_description"),
        ("matchCharacterPersonality", "match_character_personality"),
        ("matchCharacterDepthPrompt", "match_character_depth_prompt"),
        ("matchScenario", "match_scenario"),
        ("matchCreatorNotes", "match_creator_notes"),
    ):
        extensions.setdefault(camel, _get_bool(raw_extensions, snake, False))

    enabled = raw.get("enabled")
    return {
        "uid": _int_or(raw.get("id"), index),
        "key": _as_key_list(raw.get("keys")),
        "keysecondary": _as_key_list(raw.get("secondary_keys")),
        "comment": _str_or(raw.get("comment")),
        "content": _str_or(raw.get("content")),
        "constant": _bool_or(raw.get("constant"), False),
        "selective": _bool_or(raw.get("selective"), False),
        "order": _int_or(raw.get("insertion_order"), RUNTIME_DEFAULTS["order"]),
        "disable": False if enabled is None else not _bool_or(enabled, True),
        "position": position,
        "depth": _get_int(raw_extensions, "depth", RUNTIME_DEFAULTS["depth"]),
        "probability": _get_int(raw_extensions, "probability", RUNTIME_DEFAULTS["probability"]),
        "useProbability": _get_bool(
            raw_extensions, "useProbability", RUNTIME_DEFAULTS["useProbability"]
        ),
        "vectorized": _get_bool(raw_extensions, "vectorized", RUNTIME_DEFAULTS["vectorized"]),
        "selectiveLogic": _get_int(
            raw_extensions, "selectiveLogic", _EXTRA_RUNTIME_DEFAULTS["selectiveLogic"]
        ),
        "sticky": raw_extensions.get("sticky"),
        "cooldown": raw_extensions.get("cooldown"),
        "delay": raw_extensions.get("delay"),
        "group": _get_str(raw_extensions, "group", RUNTIME_DEFAULTS["group"]),
        "groupWeight": _get_int(
            raw_extensions, "group_weight", _EXTRA_RUNTIME_DEFAULTS["groupWeight"]
        ),
        "outletName": _get_str(
            raw_extensions, "outlet_name", _EXTRA_RUNTIME_DEFAULTS["outletName"]
        ),
        "automationId": _get_str(raw_extensions, "automation_id", RUNTIME_DEFAULTS["automationId"]),
        "scanDepth": raw_extensions.get("scan_depth"),
        "caseSensitive": raw_extensions.get("case_sensitive"),
        "matchWholeWords": raw_extensions.get("match_whole_words"),
        "useGroupScoring": raw_extensions.get("use_group_scoring"),
        "displayIndex": _get_int(raw_extensions, "display_index", where),
        "ignoreBudget": _get_bool(
            raw_extensions, "ignore_budget", _EXTRA_RUNTIME_DEFAULTS["ignoreBudget"]
        ),
        "excludeRecursion": _get_bool(
            raw_extensions, "exclude_recursion", _EXTRA_RUNTIME_DEFAULTS["excludeRecursion"]
        ),
        "preventRecursion": _get_bool(
            raw_extensions, "prevent_recursion", _EXTRA_RUNTIME_DEFAULTS["preventRecursion"]
        ),
        "delayUntilRecursion": _get_bool(
            raw_extensions,
            "delay_until_recursion",
            _EXTRA_RUNTIME_DEFAULTS["delayUntilRecursion"],
        ),
        "groupOverride": _get_bool(
            raw_extensions, "group_override", _EXTRA_RUNTIME_DEFAULTS["groupOverride"]
        ),
        "matchPersonaDescription": extensions["matchPersonaDescription"],
        "matchCharacterDescription": extensions["matchCharacterDescription"],
        "matchCharacterPersonality": extensions["matchCharacterPersonality"],
        "matchCharacterDepthPrompt": extensions["matchCharacterDepthPrompt"],
        "matchScenario": extensions["matchScenario"],
        "matchCreatorNotes": extensions["matchCreatorNotes"],
        "extensions": extensions,
    }


def _native_row_to_runtime(row: Mapping[str, Any], index: int) -> WorldInfoEntry:
    """Build a runtime entry directly from a native camelCase row.

    This is the one place that knows the camelCase row <-> :class:`WorldInfoEntry`
    attribute mapping, so both :func:`convert_character_book_entry` (V2 import) and
    :func:`_entries_to_native` (native/Agnai/Risu/Novel import) go through it.

    ``outletName`` deserves a note: SillyTavern keeps it inside the entry's
    ``extensions`` object and :class:`WorldInfoEntry` has no attribute for it, so
    it is preserved there and can still be exported (``exporters.py`` reads it
    back with the very same key).
    """
    extensions = _as_mapping(row.get("extensions"))
    for camel in _MATCH_FLAG_FIELDS:
        if camel in row and camel not in extensions:
            extensions[camel] = _bool_or(row[camel], False)
    if isinstance(row.get("outletName"), str) or _is_number(row.get("outletName")):
        extensions["outletName"] = _str_or(row.get("outletName"))

    return WorldInfoEntry(
        uid=_int_or(row.get("uid", row.get("id")), index),
        keys=_as_key_list(row.get("key")),
        secondary_keys=_as_key_list(row.get("keysecondary")),
        content=_str_or(row.get("content")),
        comment=_str_or(row.get("comment")),
        constant=_bool_or(row.get("constant"), False),
        selective=_bool_or(row.get("selective"), RUNTIME_DEFAULTS["selective"]),
        insertion_order=_int_or(row.get("order"), RUNTIME_DEFAULTS["order"]),
        position=_int_or(row.get("position"), POSITION_BEFORE_CHAR),
        depth=_int_or(row.get("depth"), RUNTIME_DEFAULTS["depth"]),
        role=row.get("role") if "role" in row else None,
        disable=_bool_or(row.get("disable"), False),
        probability=_int_or(row.get("probability"), RUNTIME_DEFAULTS["probability"]),
        use_probability=_bool_or(row.get("useProbability"), RUNTIME_DEFAULTS["useProbability"]),
        ignore_budget=_bool_or(row.get("ignoreBudget"), False),
        case_sensitive=row.get("caseSensitive"),
        match_whole_words=row.get("matchWholeWords"),
        scan_depth=row.get("scanDepth"),
        group=_str_or(row.get("group")),
        group_weight=_int_or(row.get("groupWeight"), 100),
        group_override=_bool_or(row.get("groupOverride"), False),
        use_group_scoring=row.get("useGroupScoring"),
        automation_id=_str_or(row.get("automationId")),
        vectorized=_bool_or(row.get("vectorized"), False),
        sticky=row.get("sticky"),
        cooldown=row.get("cooldown"),
        delay=row.get("delay"),
        exclude_recursion=_bool_or(row.get("excludeRecursion"), False),
        prevent_recursion=_bool_or(row.get("preventRecursion"), False),
        delay_until_recursion=_bool_or(row.get("delayUntilRecursion"), False),
        selective_logic=_int_or(
            row.get("selectiveLogic"), _EXTRA_RUNTIME_DEFAULTS["selectiveLogic"]
        ),
        display_index=_int_or(row.get("displayIndex"), index),
        extensions=extensions,
        key_vector=[],
    )


def _runtime_row_native(runtime: WorldInfoEntry) -> dict[str, Any]:
    """Flatten a runtime entry back into a native camelCase row (uid included)."""
    return {
        "uid": runtime.uid,
        "key": list(runtime.keys),
        "keysecondary": list(runtime.secondary_keys),
        "comment": runtime.comment,
        "content": runtime.content,
        "constant": runtime.constant,
        "selective": runtime.selective,
        "order": runtime.insertion_order,
        "disable": runtime.disable,
        "position": runtime.position,
        "depth": runtime.depth,
        "probability": runtime.probability,
        "useProbability": runtime.use_probability,
        "vectorized": runtime.vectorized,
        "selectiveLogic": runtime.selective_logic,
        "sticky": runtime.sticky,
        "cooldown": runtime.cooldown,
        "delay": runtime.delay,
        "group": runtime.group,
        "groupWeight": runtime.group_weight,
        "outletName": _str_or(runtime.extensions.get("outletName")),
        "automationId": runtime.automation_id,
        "scanDepth": runtime.scan_depth,
        "caseSensitive": runtime.case_sensitive,
        "matchWholeWords": runtime.match_whole_words,
        "useGroupScoring": runtime.use_group_scoring,
        "displayIndex": runtime.display_index,
        "ignoreBudget": runtime.ignore_budget,
        "excludeRecursion": runtime.exclude_recursion,
        "preventRecursion": runtime.prevent_recursion,
        "delayUntilRecursion": runtime.delay_until_recursion,
        "groupOverride": runtime.group_override,
        "role": runtime.role,
        "matchPersonaDescription": _bool_or(
            runtime.extensions.get("matchPersonaDescription"), False
        ),
        "matchCharacterDescription": _bool_or(
            runtime.extensions.get("matchCharacterDescription"), False
        ),
        "matchCharacterPersonality": _bool_or(
            runtime.extensions.get("matchCharacterPersonality"), False
        ),
        "matchCharacterDepthPrompt": _bool_or(
            runtime.extensions.get("matchCharacterDepthPrompt"), False
        ),
        "matchScenario": _bool_or(runtime.extensions.get("matchScenario"), False),
        "matchCreatorNotes": _bool_or(runtime.extensions.get("matchCreatorNotes"), False),
        "extensions": dict(runtime.extensions),
    }


def _entry_is_native(entry: Any) -> bool:
    """True when a dict uses the camelCase native spelling (``key``/``order``)."""
    if not isinstance(entry, Mapping):
        return False
    return "key" in entry or "keysecondary" in entry


def _entry_is_v2(entry: Any) -> bool:
    """True when a dict uses the V2 snake_case spelling or its ``extensions`` escape hatch.

    Three signals, in this order of reliability:

    * the V2-only field names (``keys`` / ``secondary_keys`` / ``insertion_order``);
    * a **string** ``position`` (``before_char`` / ``after_char``) -- no native entry
      ever has that, and a library card may omit every other V2 field;
    * an ``extensions`` mapping carrying the numeric ``position`` enum.

    A native camelCase row always wins when both spellings are mixed into one dict,
    matching the documented precedence.
    """
    if not isinstance(entry, Mapping):
        return False
    if "keys" in entry or "insertion_order" in entry or "secondary_keys" in entry:
        return True
    if _entry_is_native(entry):
        return False
    if isinstance(entry.get("position"), str):
        return True
    return isinstance(_as_mapping(entry.get("extensions")).get("position"), int)


def _entries_to_native(raw_entries: Any) -> dict[str | int, dict[str, Any]]:
    """Normalise an arbitrary ``entries`` value into ``{uid: native-entry}`` rows.

    Detection is per-entry and deliberate, because no single test can separate the
    dialects: ``convertWorldInfoToCharacterBook`` writes full V2 envelopes
    (*and* keeps the camelCase runtime spellings inside ``extensions``), while
    ``convertAgnaiMemoryBook`` / ``convertRisuLorebook`` emit camelCase rows.
    V2 wins when both spellings are present, which is the documented precedence.
    """
    rows: dict[str | int, dict[str, Any]] = {}
    for index, entry in enumerate(_normalise_entries(raw_entries)):
        if _entry_is_v2(entry):
            row = _runtime_row_v2(entry, index, index)
        elif _entry_is_native(entry):
            row = dict(entry)
        else:
            row = dict(entry)
        uid = _int_or(row.get("uid", row.get("id", index)), index)
        row["uid"] = uid
        rows[uid] = row
    return rows


# -- per dialect converters (ported) ----------------------------------------


def convert_agnai_memory_book(input_obj: Mapping[str, Any]) -> WorldBook:
    """``convertAgnaiMemoryBook`` (``world-info.js:5477-5520``).

    ``inputObj.entries`` is an **array** of ``{name, entry, keywords, enabled,
    weight}``; the result is a native ``{index: entry}`` map where
    ``order = weight`` and ``disable = !enabled``.
    """
    if not isinstance(input_obj, Mapping):
        raise LorebookImportError("Agnai 记忆书不是对象，无法解析。")
    raw_entries = input_obj.get("entries")
    if not isinstance(raw_entries, (list, tuple)):
        raise LorebookImportError("Agnai 记忆书的 entries 必须是数组。")

    rows: dict[str | int, dict[str, Any]] = {}
    for index, entry in enumerate(_normalise_entries(raw_entries)):
        rows[index] = _native_entry(
            uid=index,
            key=_as_key_list(entry.get("keywords")),
            keysecondary=[],
            comment=_str_or(entry.get("name")),
            content=_str_or(entry.get("entry")),
            constant=False,
            selective=False,
            vectorized=False,
            selectiveLogic=0,
            order=_int_or(entry.get("weight"), 0),
            position=WI_POSITION_BEFORE,
            disable=not _bool_or(entry.get("enabled"), False),
            addMemo=bool(_str_or(entry.get("name"))),
            excludeRecursion=False,
            delayUntilRecursion=False,
            displayIndex=index,
            probability=100,
            useProbability=True,
            outletName="",
            group="",
            groupOverride=False,
            groupWeight=100,
            scanDepth=None,
            caseSensitive=None,
            matchWholeWords=None,
            useGroupScoring=None,
            automationId="",
            role=ROLE_SYSTEM,
            sticky=None,
            cooldown=None,
            delay=None,
            triggers=[],
            ignoreBudget=False,
        )
    return _book_from_runtime_entries(rows, original_data=dict(input_obj))


def convert_risu_lorebook(input_obj: Mapping[str, Any]) -> WorldBook:
    """``convertRisuLorebook`` (``world-info.js:5522-5565``).

    ``inputObj.data`` is an **array**; ``key`` / ``secondkey`` are comma separated
    strings (``split(',').map(trim)``), ``order = insertorder`` and
    ``probability = activationPercent ?? 100``.

    Upstream quirk kept as is: ``useProbability: entry.activationPercent ?? true``
    mixes a number into a boolean field (``world-info.js:5545``). The runtime model
    needs a real bool, so ``activationPercent`` is truthiness-tested here; the
    original value survives in :data:`ORIGINAL_DATA_KEY`.
    """
    if not isinstance(input_obj, Mapping):
        raise LorebookImportError("Risu 世界书不是对象，无法解析。")
    raw_entries = input_obj.get("data")
    if not isinstance(raw_entries, (list, tuple)):
        raise LorebookImportError("Risu 世界书的 data 必须是数组。")

    rows: dict[str | int, dict[str, Any]] = {}
    for index, entry in enumerate(_normalise_entries(raw_entries)):
        percent = entry.get("activationPercent")
        rows[index] = _native_entry(
            uid=index,
            key=[part.strip() for part in _str_or(entry.get("key")).split(",") if part.strip()],
            keysecondary=(
                [
                    part.strip()
                    for part in _str_or(entry.get("secondkey")).split(",")
                    if part.strip()
                ]
                if entry.get("secondkey")
                else []
            ),
            comment=_str_or(entry.get("comment")),
            content=_str_or(entry.get("content")),
            constant=_bool_or(entry.get("alwaysActive"), False),
            selective=_bool_or(entry.get("selective"), False),
            vectorized=False,
            selectiveLogic=0,
            order=_int_or(entry.get("insertorder"), 0),
            position=WI_POSITION_BEFORE,
            disable=False,
            addMemo=True,
            excludeRecursion=False,
            delayUntilRecursion=False,
            displayIndex=index,
            probability=100 if percent is None else _int_or(percent, 100),
            useProbability=True if percent is None else _bool_or(percent, True),
            outletName="",
            group="",
            groupOverride=False,
            groupWeight=100,
            scanDepth=None,
            caseSensitive=None,
            matchWholeWords=None,
            useGroupScoring=None,
            automationId="",
            role=ROLE_SYSTEM,
            sticky=None,
            cooldown=None,
            delay=None,
            triggers=[],
            ignoreBudget=False,
        )
    return _book_from_runtime_entries(rows, original_data=dict(input_obj))


def convert_novel_lorebook(input_obj: Mapping[str, Any]) -> WorldBook:
    """``convertNovelLorebook`` (``world-info.js:5567-5615``), included for completeness.

    ``inputObj.entries`` is an array of ``{displayName, keys, text, enabled,
    contextConfig}``; ``order = contextConfig.budgetPriority ?? 0`` and the
    display name doubles as the comment.
    """
    if not isinstance(input_obj, Mapping):
        raise LorebookImportError("NovelAI 世界书不是对象，无法解析。")
    raw_entries = input_obj.get("entries")
    if not isinstance(raw_entries, (list, tuple)):
        raise LorebookImportError("NovelAI 世界书的 entries 必须是数组。")

    rows: dict[str | int, dict[str, Any]] = {}
    for index, entry in enumerate(_normalise_entries(raw_entries)):
        display_name = _str_or(entry.get("displayName"))
        context_config = _as_mapping(entry.get("contextConfig"))
        rows[index] = _native_entry(
            uid=index,
            key=_as_key_list(entry.get("keys")),
            keysecondary=[],
            comment=display_name,
            content=_str_or(entry.get("text")),
            constant=False,
            selective=False,
            vectorized=False,
            selectiveLogic=0,
            order=_get_int(context_config, "budgetPriority", 0),
            position=WI_POSITION_BEFORE,
            disable=not _bool_or(entry.get("enabled"), False),
            addMemo=bool(display_name.strip()),
            excludeRecursion=False,
            delayUntilRecursion=False,
            displayIndex=index,
            probability=100,
            useProbability=True,
            outletName="",
            group="",
            groupOverride=False,
            groupWeight=100,
            scanDepth=None,
            caseSensitive=None,
            matchWholeWords=None,
            useGroupScoring=None,
            automationId="",
            role=ROLE_SYSTEM,
            sticky=None,
            cooldown=None,
            delay=None,
            triggers=[],
            ignoreBudget=False,
        )
    return _book_from_runtime_entries(rows, original_data=dict(input_obj))


# ---------------------------------------------------------------------------
# import_lorebook -- format sniffing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LorebookFormat:
    """One recognisable standalone-lorebook dialect."""

    name: str
    detect: Any  # Callable[[Mapping[str, Any]], bool]
    convert: Any  # Callable[[Mapping[str, Any]], WorldBook]


def _looks_like_agnai(payload: Mapping[str, Any]) -> bool:
    """``jsonData.kind === 'memory'`` (``world-info.js:5881``)."""
    return _str_or(payload.get("kind")) == "memory" and isinstance(
        payload.get("entries"), (list, tuple)
    )


def _looks_like_risu(payload: Mapping[str, Any]) -> bool:
    """``jsonData.type === 'risu'`` (``world-info.js:5887``)."""
    return _str_or(payload.get("type")) == "risu"


def _looks_like_novel(payload: Mapping[str, Any]) -> bool:
    """``jsonData.lorebookVersion !== undefined`` (``world-info.js:5875``)."""
    return "lorebookVersion" in payload


def _looks_like_lorebook_v3(payload: Mapping[str, Any]) -> bool:
    """A standard ``{"spec": "lorebook_v3", "data": Lorebook}`` file."""
    return _str_or(payload.get("spec")) == SPEC_LOREBOOK_V3 and isinstance(
        payload.get("data"), Mapping
    )


def _looks_like_v2_book(payload: Mapping[str, Any]) -> bool:
    """A V2 / V3 ``Lorebook`` object: ``entries`` **plus** book level fields."""
    if "entries" not in payload:
        return False
    book_fields = {
        "name",
        "description",
        "scan_depth",
        "token_budget",
        "recursive_scanning",
        "extensions",
        "spec",
        "spec_version",
    }
    return any(field in payload for field in book_fields)


def _looks_like_bare_entries(payload: Mapping[str, Any]) -> bool:
    """A bare ``{"entries": ...}`` wrapper, a raw ``{"0": {...}}`` uid map or one entry.

    Deliberately **not** satisfied by a lone empty ``{"entries": []}``: a payload
    that carries no book level field and no entry at all is reported as
    unrecognised instead of importing as an empty book.
    """
    entries = payload.get("entries")
    if entries is not None:
        return bool(entries)
    if _entry_is_v2(payload) or _entry_is_native(payload):
        return True
    return any(isinstance(value, Mapping) for value in payload.values())


def convert_lorebook_v3(payload: Mapping[str, Any]) -> WorldBook:
    """Unwrap ``{"spec": "lorebook_v3", "data": Lorebook}`` and parse the book."""
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise LorebookImportError("lorebook_v3 文件缺少 data 世界书对象。")
    book = _book_from_runtime_entries(
        _entries_to_native(data.get("entries") or []),
        name=_str_or(data.get("name")),
        description=_str_or(data.get("description")),
        scan_depth=_optional_number(data.get("scan_depth")),
        token_budget=_optional_number(data.get("token_budget")),
        recursive_scanning=_bool_or(data.get("recursive_scanning"), False),
        extensions=_as_mapping(data.get("extensions")),
        original_data=dict(payload),
    )
    return book


def convert_v2_lorebook(payload: Mapping[str, Any]) -> WorldBook:
    """A V2/V3 ``Lorebook`` object: ``entries`` array + optional book fields."""
    book = _book_from_runtime_entries(
        _entries_to_native(payload.get("entries") or []),
        name=_str_or(payload.get("name")),
        description=_str_or(payload.get("description")),
        scan_depth=_optional_number(payload.get("scan_depth")),
        token_budget=_optional_number(payload.get("token_budget")),
        recursive_scanning=_bool_or(payload.get("recursive_scanning"), False),
        extensions=_as_mapping(payload.get("extensions")),
        original_data=dict(payload),
    )
    return book


def convert_bare_entries(payload: Mapping[str, Any]) -> WorldBook:
    """A raw ``{"entries": ...}`` wrapper, ``{"0": {...}}`` map or ``{...}`` native entry.

    This is the last resort, so it must stay permissive: ``worldbook.book_from_dict``
    owns the final shape decision (it reads ``entries`` as a dict or list and
    otherwise keeps every top level value that looks like an entry). The book's
    name comes from the payload when present, otherwise the caller's file name.
    """
    if "entries" in payload:
        rows = _entries_to_native(payload.get("entries"))
    elif _entry_is_v2(payload) or _entry_is_native(payload):
        # A single entry object, not a container.
        rows = _entries_to_native([payload])
    else:
        rows = _entries_to_native(payload)
    return _book_from_runtime_entries(
        rows,
        name=_str_or(payload.get("name")),
        description=_str_or(payload.get("description")),
        extensions={},
        original_data=dict(payload),
    )


#: Detection order matters: the dialect markers are checked before the generic
#: ``entries`` shapes, mirroring ``importWorldInfo`` (``world-info.js:5850``) where
#: Novel/Agnai/Risu each override the upload's ``convertedData``.
LOREBOOK_FORMATS: tuple[LorebookFormat, ...] = (
    LorebookFormat("risu", _looks_like_risu, convert_risu_lorebook),
    LorebookFormat("agnai", _looks_like_agnai, convert_agnai_memory_book),
    LorebookFormat("novel", _looks_like_novel, convert_novel_lorebook),
    LorebookFormat("lorebook_v3", _looks_like_lorebook_v3, convert_lorebook_v3),
    LorebookFormat("v2", _looks_like_v2_book, convert_v2_lorebook),
    LorebookFormat("entries", _looks_like_bare_entries, convert_bare_entries),
)


def detect_lorebook_format(payload: Mapping[str, Any]) -> str:
    """Return the :class:`LorebookFormat` name that would handle ``payload``.

    Raises :class:`LorebookImportError` when nothing matches (an empty object, or
    a payload whose values are all scalars).
    """
    if not isinstance(payload, Mapping):
        return ""
    for fmt in LOREBOOK_FORMATS:
        if fmt.detect(payload):
            return fmt.name
    return ""


def import_lorebook(payload: dict | Any, *, name: str = "") -> WorldBook:
    """Import a standalone lorebook from any supported dialect.

    Accepted shapes (checked in this order, see :data:`LOREBOOK_FORMATS`):

    ``{"type": "risu", "data": [...]}``
        Risu lorebook -- :func:`convert_risu_lorebook`.
    ``{"kind": "memory", "entries": [...]}``
        Agnai memory book -- :func:`convert_agnai_memory_book`.
    ``{"lorebookVersion": N, "entries": [...]}``
        NovelAI lorebook -- :func:`convert_novel_lorebook`.
    ``{"spec": "lorebook_v3", "data": {...}}``
        Character Card V3 standalone lorebook -- :func:`convert_lorebook_v3`.
    ``{"name": ..., "entries": [...]}`` / ``{"entries": {"0": {...}}}``
        V2 lorebook or native ST book.
    ``{"0": {...}, "1": {...}}`` or a single native entry
        Raw uid map / bare entry -- :func:`convert_bare_entries`.

    ``name`` is only used as a fallback book name (the payload's own ``name``
    wins); the plugin passes its file name here so a nameless book is still
    addressable.

    Raises
    ------
    LorebookImportError
        The payload is not an object, or no dialect recognises it.
    """
    if not isinstance(payload, Mapping):
        raise LorebookImportError(f"世界书必须是 JSON 对象，收到的是 {type(payload).__name__}。")
    if not payload:
        raise LorebookImportError("世界书是空对象，里面没有任何条目。")

    for fmt in LOREBOOK_FORMATS:
        if fmt.detect(payload):
            book = fmt.convert(payload)
            if not book.name and name:
                book.name = name
            logger.debug("imported lorebook as %s: %s (%d entries)", fmt.name, book.name, len(book))
            return book
    raise LorebookImportError(
        "无法识别这个世界书格式（不是 V2/V3 lorebook、Agnai 记忆书或 Risu 世界书）。"
    )


# ---------------------------------------------------------------------------
# import_character_book -- the card's embedded book
# ---------------------------------------------------------------------------


def character_book_from_card(card: CharacterCard) -> dict[str, Any] | None:
    """Return the raw ``data.character_book`` of ``card``, or ``None``.

    ``tavern/st/cards.py`` normalises a card into flat fields and has no
    ``character_book`` attribute, so the untouched payload is read back from
    :attr:`CharacterCard.raw` -- both the V2/V3 envelope and a V1 flat card are
    handled.
    """
    payload = card.raw if isinstance(card.raw, Mapping) else {}
    data = payload.get("data")
    if isinstance(data, Mapping) and isinstance(data.get("character_book"), Mapping):
        return dict(data["character_book"])
    if isinstance(payload.get("character_book"), Mapping):
        return dict(payload["character_book"])
    return None


def import_character_book(card: CharacterCard | dict | Any) -> WorldBook:
    """Import the world book **embedded in a character card**.

    ``card`` may be a :class:`~tavern.st.cards.CharacterCard` (the usual case:
    ``import_character_card`` output, or ``cards.load_card``) or a raw card dict /
    raw ``character_book`` dict.

    The book is translated with :func:`convert_character_book`
    (``convertCharacterBook``, ``world-info.js:5617``), so the snake_case V2 entry
    fields become runtime entry fields and the untouched payload is preserved in
    :attr:`WorldBook.extensions` under :data:`ORIGINAL_DATA_KEY`.
    Returns a :class:`WorldBook` with 0 entries for a card whose book is empty --
    "this card has a book, it is just empty" is not an error.

    Raises
    ------
    CharacterBookImportError
        The card has no ``character_book`` at all, or the book is malformed.
    """
    if isinstance(card, CharacterCard):
        book = character_book_from_card(card)
        if book is None:
            raise CharacterBookImportError(
                f"角色卡「{card.name or card.source_path or '未命名'}」里没有内嵌世界书。"
            )
        return convert_character_book(book)

    if not isinstance(card, Mapping):
        raise CharacterBookImportError(f"角色卡必须是对象，收到的是 {type(card).__name__}。")

    payload = dict(card)
    data = payload.get("data")
    if isinstance(data, Mapping):
        raw_book = data.get("character_book")
    else:
        raw_book = payload.get("character_book")
    if not isinstance(raw_book, Mapping):
        raise CharacterBookImportError("这张角色卡里没有内嵌世界书（character_book）。")
    return convert_character_book(raw_book)


# ---------------------------------------------------------------------------
# import_character_card
# ---------------------------------------------------------------------------


def _decode_png_metadata(blob: bytes) -> tuple[bytes, dict[str, str]]:
    """Return ``(image-only PNG bytes, text chunks)``.

    The chunk walk is a read-only mirror of ``cards._decode_png_text_chunks``;
    it is duplicated rather than imported because this module must not modify
    ``cards.py`` (see the port brief). A blob that is not a PNG yields empty
    metadata instead of raising, so the caller can produce a readable error.
    """
    if not blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return blob, {}
    chunks: dict[str, str] = {}
    kept: list[bytes] = [b"\x89PNG\r\n\x1a\n"]
    offset = 8
    total = len(blob)
    while offset + 8 <= total:
        (length,) = struct.unpack(">I", blob[offset : offset + 4])
        chunk_type = blob[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        if data_end + 4 > total:
            break
        raw_chunk = blob[offset : data_end + 4]
        if chunk_type == b"tEXt":
            keyword, _, value = blob[data_start:data_end].partition(b"\x00")
            chunks[keyword.decode("latin-1", errors="replace")] = value.decode(
                "latin-1", errors="replace"
            )
        elif chunk_type in (b"IHDR", b"PLTE", b"IDAT", b"IEND"):
            kept.append(raw_chunk)
        offset = data_end + 4
        if chunk_type == b"IEND":
            break
    return b"".join(kept), chunks


def _sniff_text_format(text: str) -> str:
    """Guess ``json`` / ``yaml`` for a raw payload string (never raises)."""
    head = text.lstrip()
    if head[:1] in ("{", "["):
        return "json"
    lowered = text.lower()
    if "spec:" in lowered or "spec_version:" in lowered or "character_book:" in lowered:
        return "yaml"
    if text[:1].isalpha() and ("\n" in text or ":" in text):
        return "yaml"
    return "json"


def _parse_payload_text(text: str, filename: str) -> dict[str, Any]:
    """Parse a card given as raw text: JSON first, YAML when PyYAML is present."""
    cleaned = text[1:] if text[:1] == "\ufeff" else text
    if not cleaned.strip():
        raise CharacterCardImportError(f"角色卡「{filename or '未命名'}」内容为空。")

    fmt = _sniff_text_format(cleaned)
    payload: Any = None
    if fmt == "json":
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise CharacterCardImportError(
                f"角色卡「{filename or '未命名'}」不是合法 JSON：{exc.msg}（第 {exc.lineno} 行）。"
            ) from exc
    else:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as exc:
            raise OptionalDependencyError(
                "这份角色卡是 YAML 格式，需要先安装 PyYAML（pip install pyyaml）。"
            ) from exc
        try:
            payload = yaml.safe_load(cleaned)
        except Exception as exc:  # noqa: BLE001 - yaml raises many subclasses
            raise CharacterCardImportError(
                f"角色卡「{filename or '未命名'}」不是合法 YAML：{exc}。"
            ) from exc

    if not isinstance(payload, Mapping):
        raise CharacterCardImportError(
            f"角色卡「{filename or '未命名'}」的根节点必须是 JSON 对象。"
        )
    return dict(payload)


def _validate_card_payload(payload: Mapping[str, Any], filename: str) -> None:
    """Reject objects that cannot be a character card, with a readable message.

    Mirrors the *intent* of ``TavernCardValidator`` (``TavernCardValidator.js``):
    V1 requires the six flat fields, V2/V3 require ``data`` plus the twelve
    ``data.*`` fields. A card that fails both specs is refused; a V2 card whose
    ``data`` is merely incomplete is accepted (non-fatal, see
    :func:`card_validation_issues`) because SillyTavern copies such cards anyway.
    """
    data = payload.get("data")
    if isinstance(data, Mapping):
        spec = _str_or(payload.get("spec"))
        if spec in (SPEC_V2, SPEC_V3) or "spec_version" in payload:
            if not _str_or(data.get("name")):
                raise CharacterCardImportError(
                    f"角色卡「{filename or '未命名'}」的 data.name 是空的，无法导入。"
                )
            return
    if "name" in payload:
        return
    raise CharacterCardImportError(
        f"「{filename or '未命名'}」看起来不是角色卡：既没有 name，也没有 spec/data 结构。"
    )


def card_validation_issues(payload: Mapping[str, Any]) -> tuple[int, list[str]]:
    """Return ``(spec_number, non_fatal_problems)`` for a card payload.

    ``spec_number`` is ``1`` / ``2`` / ``3`` when the card matches that spec's
    required field set, else ``0``. ``non_fatal_problems`` lists the missing
    fields that SillyTavern's validator treats as fatal but that this importer
    tolerates (it fills the default instead) -- ``cards.card_from_dict`` would
    anyway coerce them to ``""``.

    Fatal problems are not listed here; :func:`_validate_card_payload` raises for
    those before a card is built.
    """
    data = payload.get("data")
    if isinstance(data, Mapping) and (
        _str_or(payload.get("spec")) in (SPEC_V2, SPEC_V3) or "spec_version" in payload
    ):
        required = (
            "name",
            "description",
            "personality",
            "scenario",
            "first_mes",
            "mes_example",
            "creator_notes",
            "system_prompt",
            "post_history_instructions",
            "alternate_greetings",
            "tags",
            "creator",
            "character_version",
            "extensions",
        )
        missing = [f"data.{field}" for field in required if field not in data]
        spec_number = 3 if _str_or(payload.get("spec")) == SPEC_V3 else 2
        return spec_number, missing

    required_v1 = ("name", "description", "personality", "scenario", "first_mes", "mes_example")
    missing_v1 = [field for field in required_v1 if field not in payload]
    if not missing_v1:
        return 1, []
    return 0, missing_v1


SOURCE_PNG_KEY = "_source_png"


def _finish_card(
    raw_payload: dict[str, Any],
    filename: str,
    source_png: bytes | None = None,
) -> CharacterCard:
    """Validate, tag and build a card from an already decoded payload.

    A V1 flat card gets an explicit ``spec`` so the model does not have to guess;
    the original PNG (if any) is stored next to the payload under
    :data:`SOURCE_PNG_KEY` so ``exporters.export_character_card(as_png=True)`` can
    keep the artwork instead of emitting a placeholder.
    """
    _validate_card_payload(raw_payload, filename)
    if not raw_payload.get("spec") and not isinstance(raw_payload.get("data"), Mapping):
        raw_payload["spec"] = "chara_card_v1"
    if source_png is not None:
        raw_payload.setdefault(SOURCE_PNG_KEY, source_png)
    return card_from_dict(raw_payload, source_path=filename)


def import_character_card(
    payload: bytes | dict | str | Path,
    filename: str = "",
) -> CharacterCard:
    """Import a character card from bytes, a dict, text or a path.

    Parameters
    ----------
    payload:
        * ``bytes`` -- a PNG (``tEXt``/``iTXt`` ``ccv3`` chunk preferred over
          ``chara``; the rule itself lives in :func:`tavern.st.cards.card_from_png`)
          or UTF-8 ``.json`` / ``.yaml`` content;
        * ``dict`` -- an already decoded V1 / V2 / V3 card object;
        * ``str`` / ``pathlib.Path`` -- a **path** to an existing ``.json`` /
          ``.png`` / ``.yaml`` file (delegated to :func:`tavern.st.cards.load_card`),
          otherwise the card's JSON text (JSON syntax beats the file name: content
          starting with ``{`` or ``[`` is always parsed as JSON).
    filename:
        Used for error messages and, when ``payload`` is ``bytes`` or ``str``, for
        picking the parser (``.yaml`` selects YAML; ``.png`` is detected by magic
        bytes, never by name).

    Returns
    -------
    CharacterCard
        Always carries the untouched payload in :attr:`CharacterCard.raw`, so
        :func:`import_character_book` can still reach an embedded
        ``character_book``.

    Raises
    ------
    CharacterCardImportError
        The payload is not a recognisable card. The message is Chinese and always
        names the offending field -- never a bare ``KeyError``.
    OptionalDependencyError
        A YAML card was given and PyYAML is missing.
    """
    if isinstance(payload, Path):
        target = payload
        label = filename or str(target)
        if not target.is_file():
            raise CharacterCardImportError(f"找不到角色卡文件：{target}。")
        try:
            return load_card(target)
        except CharacterCardError as exc:
            raise CharacterCardImportError(f"角色卡「{label}」读不了：{exc}") from exc

    suffix = Path(filename).suffix.lower()

    if isinstance(payload, (bytes, bytearray, memoryview)):
        blob = bytes(payload)
        if blob.startswith(b"\x89PNG\r\n\x1a\n"):
            _image, chunks = _decode_png_metadata(blob)
            if not chunks:
                raise CharacterCardImportError(
                    f"PNG「{filename or '未命名'}」里没有角色卡数据（需要 ccv3 或 chara 文本块）。"
                )
            try:
                card = card_from_png(blob, source_path=filename)
            except CharacterCardError as exc:
                raise CharacterCardImportError(str(exc)) from exc
            if isinstance(card.raw, dict):
                card.raw.setdefault(SOURCE_PNG_KEY, blob)
            return card
        try:
            text = blob.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CharacterCardImportError(
                f"「{filename or '未命名'}」既不是 PNG，也不是 UTF-8 文本：{exc}。"
            ) from exc
        if suffix in (".yaml", ".yml"):
            return _finish_card(_parse_payload_text(_force_yaml_marker(text), filename), filename)
        return _finish_card(_parse_payload_text(text, filename), filename)

    if isinstance(payload, Mapping):
        raw_payload = dict(payload)
        source_png = raw_payload.get(SOURCE_PNG_KEY)
        return _finish_card(
            raw_payload,
            filename,
            source_png if isinstance(source_png, (bytes, bytearray)) else None,
        )

    if isinstance(payload, str):
        candidate = Path(payload)
        try:
            is_file = "\n" not in payload and len(payload) < 4096 and candidate.is_file()
        except OSError:  # pragma: no cover - invalid path characters
            is_file = False
        if is_file:
            try:
                return load_card(candidate)
            except CharacterCardError as exc:
                raise CharacterCardImportError(str(exc)) from exc

        text = _force_yaml_marker(payload) if suffix in (".yaml", ".yml") else payload
        return _finish_card(_parse_payload_text(text, filename), filename)

    raise CharacterCardImportError(
        f"不支持的角色卡类型 {type(payload).__name__}，请给 bytes、dict 或 str。"
    )


def _force_yaml_marker(text: str) -> str:
    """Turn a YAML payload into something :func:`_parse_payload_text` will not sniff as JSON.

    A ``.yaml`` file may legitimately start with ``{`` (flow style); the caller's
    file name is the stronger signal, so the leading brace is nudged into a YAML
    document marker instead of being re-sniffed.
    """
    stripped = text.lstrip("\ufeff \t\r\n")
    if stripped[:1] in ("{", "["):
        return "---\n" + text
    return text


# ---------------------------------------------------------------------------
# import_chat_jsonl
# ---------------------------------------------------------------------------


def _strip_bom(text: str) -> str:
    """Drop a leading UTF-8 BOM that survived decoding."""
    return text[1:] if text[:1] == "\ufeff" else text


def import_chat_jsonl(text: str) -> list[dict]:
    """Import a SillyTavern ``.jsonl`` chat as raw dicts.

    This function is **format normalisation only**: it does not touch the filesystem
    and does not model the chat (``tavern/st/chat_store.py`` owns that, and is
    deliberately left alone).

    Returned shape
    --------------
    ``[header, message, ...]`` where ``header`` is index 0. An empty or unusable
    payload yields ``[]`` -- a header-only payload yields exactly one element.
    The header is filled in with ``user_name`` / ``character_name`` /
    ``chat_metadata`` keys when the file omits them, so ``chat_store`` sees the
    same shape either way.

    Tolerance
    ---------
    * CRLF / LF / CR line endings and a trailing newline (or its absence),
    * a leading UTF-8 BOM,
    * blank lines anywhere,
    * a line that is not valid JSON is skipped (logged), never fatal,
    * extra / unknown message fields are preserved verbatim,
    * ``swipes``, ``swipe_id`` and ``swipe_info`` survive untouched, so swiping
      still works after an import,
    * a numeric ``send_date`` stays numeric here (``chat_store`` normalises it
      when it builds a :class:`~tavern.st.chat_store.ChatMessage`).
    """
    if not isinstance(text, str):
        return []
    cleaned = _strip_bom(text)
    if not cleaned.strip():
        return []

    lines = cleaned.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    header: dict[str, Any] = {}
    messages: list[dict[str, Any]] = []
    header_seen = False

    for index, raw in enumerate(lines):
        if not raw.strip():
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("skip unparsable chat line %d: %r", index + 1, raw[:80])
            continue
        if not isinstance(parsed, Mapping):
            logger.debug("skip non-object chat line %d", index + 1)
            continue
        row = dict(parsed)

        if not header_seen:
            header_seen = True
            header = _normalise_chat_header(row)
            continue

        if _looks_like_chat_header(row):
            # A stray second header (some exporters repeat it): fold it in
            # without later keys overwriting the real header.
            for key, value in row.items():
                header.setdefault(key, value)
            continue
        messages.append(row)

    if not header_seen:
        return []
    return [header, *messages]


def _looks_like_chat_header(row: Mapping[str, Any]) -> bool:
    """True for a line that carries chat metadata instead of a message."""
    if "mes" in row or "is_user" in row or "is_system" in row:
        return False
    return bool({"user_name", "character_name", "chat_metadata"} & set(row))


def _normalise_chat_header(row: Mapping[str, Any]) -> dict[str, Any]:
    """Fill the three header keys ``chat_store`` expects, keeping everything else."""
    header = dict(row)
    header.setdefault("user_name", "")
    header.setdefault("character_name", _str_or(header.get("name")))
    metadata = header.get("chat_metadata")
    if not isinstance(metadata, Mapping):
        # Older exports put the metadata inline or named it "metadata".
        fallback = header.get("metadata")
        metadata = fallback if isinstance(fallback, Mapping) else {}
        for key in (
            "note_prompt",
            "note_interval",
            "note_position",
            "timedWorldInfo",
            "world_info",
            "integrity",
        ):
            if key in header and key not in metadata:
                metadata = {**metadata, key: header[key]}
    header["chat_metadata"] = dict(metadata)
    return header


# ---------------------------------------------------------------------------
# describe_import
# ---------------------------------------------------------------------------


def _preview(text: str, limit: int = 24) -> str:
    """One-line, length-capped preview of a text field."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


def describe_import(target: Any, *, entries: int | None = None) -> str:
    """Return a short **Chinese** summary of an imported card or lorebook.

    Used for QQ replies, e.g.::

        这是 V2 卡「Iris」，含 1 本内嵌世界书（12 条）

    ``target`` may be:

    * a :class:`~tavern.st.cards.CharacterCard` (or a raw card dict): described as
      a card, including its embedded book when it has one;
    * a :class:`~tavern.st.worldbook.WorldBook`: described as a lorebook;
    * a ``list`` returned by :func:`import_chat_jsonl`: described as a chat.
    """
    if isinstance(target, CharacterCard):
        return _describe_card(target)
    if isinstance(target, WorldBook):
        return _describe_book(target)
    if isinstance(target, (list, tuple)):
        return _describe_chat(list(target))
    if isinstance(target, Mapping):
        if "character_book" in target or "data" in target or "spec" in target:
            try:
                card = card_from_dict(dict(target))
            except Exception:  # noqa: BLE001 - fall through to the book branch
                logger.debug("describe_import: dict is not a card", exc_info=True)
            else:
                return _describe_card(card)
        try:
            book = import_lorebook(dict(target))
        except ImportError as exc:
            return f"无法识别的导入内容：{exc}"
        return _describe_book(book)
    return f"无法识别的导入内容（{type(target).__name__}）。"


def _spec_label(spec: str) -> str:
    """``chara_card_v2`` -> ``V2``."""
    if not spec:
        return "未知版本"
    lowered = spec.lower()
    for number in ("3", "2", "1"):
        if number in lowered:
            return f"V{number}"
    return spec


def _describe_card(card: CharacterCard) -> str:
    """Chinese one-liner for a card, including its embedded world book."""
    label = _spec_label(card.spec)
    name = card.name or "未命名"
    parts = [f"这是 {label} 卡「{name}」"]

    raw_book = character_book_from_card(card)
    if raw_book is not None:
        try:
            book = convert_character_book(raw_book)
        except ImportError as exc:
            return "，".join(parts) + f"，但内嵌世界书读不了：{exc}"
        parts.append(f"含 1 本内嵌世界书（{len(book)} 条）")
    if card.tags:
        parts.append("标签：" + "、".join(card.tags[:6]))
    if card.alternate_greetings:
        parts.append(f"{len(card.alternate_greetings)} 个备用开场白")
    if card.creator_notes:
        parts.append("作者备注：" + _preview(card.creator_notes, 16))
    return "，".join(parts)


def _describe_book(book: WorldBook) -> str:
    """Chinese one-liner for a lorebook."""
    name = book.name or "未命名世界书"
    total = len(book)
    enabled = sum(1 for entry in book.entries if not entry.disable)
    constant = sum(1 for entry in book.entries if entry.constant)
    parts = [f"这是世界书「{name}」，共 {total} 条（启用 {enabled} 条"]
    if constant:
        parts[-1] += f"，常驻 {constant} 条"
    parts[-1] += "）"
    comments = [entry.comment for entry in book.entries if entry.comment]
    if comments:
        parts.append("条目示例：" + "、".join(_preview(item, 12) for item in comments[:3]))
    return "，".join(parts)


def _describe_chat(rows: list[Any]) -> str:
    """Chinese one-liner for :func:`import_chat_jsonl` output."""
    if not rows:
        return "这是一份空聊天记录（没有可解析的行）。"
    header = rows[0] if isinstance(rows[0], Mapping) else {}
    messages = [row for row in rows[1:] if isinstance(row, Mapping)]
    character = _str_or(header.get("character_name")) or "未知角色"
    user = _str_or(header.get("user_name")) or "未知用户"
    swipes = sum(1 for row in messages if row.get("swipes"))
    parts = [f"这是 {user} ↔ {character} 的聊天记录，共 {len(messages)} 条消息"]
    if swipes:
        parts.append(f"{swipes} 条带 swipe 分支")
    return "，".join(parts)


__all__ = [
    "FIELD_MAPPING",
    "LOREBOOK_FORMATS",
    "ORIGINAL_DATA_KEY",
    "RUNTIME_CORE_FIELDS",
    "RUNTIME_DEFAULTS",
    "RUNTIME_TO_V2_EXTENSIONS",
    "SPEC_LOREBOOK_V3",
    "SPEC_V2",
    "SPEC_V3",
    "SOURCE_PNG_KEY",
    "V2_EXTENSION_COLUMNS",
    "CharacterBookImportError",
    "CharacterCardImportError",
    "FieldMapping",
    "ImportError",
    "LorebookFormat",
    "LorebookImportError",
    "OptionalDependencyError",
    "card_validation_issues",
    "character_book_from_card",
    "convert_agnai_memory_book",
    "convert_bare_entries",
    "convert_character_book",
    "convert_character_book_entry",
    "convert_lorebook_v3",
    "convert_novel_lorebook",
    "convert_risu_lorebook",
    "convert_v2_lorebook",
    "describe_import",
    "detect_lorebook_format",
    "import_character_book",
    "import_character_card",
    "import_chat_jsonl",
    "import_lorebook",
    "number_to_v2_position",
    "v2_position_to_number",
]
