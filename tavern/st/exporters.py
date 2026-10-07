# Ported from SillyTavern - src/endpoints/characters.js (convertWorldInfoToCharacterBook) and
# public/scripts/world-info.js (convertCharacterBook, convertAgnaiMemoryBook, convertRisuLorebook)
# Copyright (C) 2024 SillyTavern contributors
# Licensed under the GNU Affero General Public License v3.0.
# This file is a modified Python translation; modified on 2026-10-07.
# Upstream: https://github.com/SillyTavern/SillyTavern (commit 06bde939)

"""Export side of the SillyTavern format layer.

``importers.py`` reads foreign payloads; this module writes them back out. The
two directions are mirror images and share one authoritative field table,
:data:`tavern.st.importers.FIELD_MAPPING`.

What is written
---------------

``export_character_card``
    A Character Card V2 envelope (``{"spec": "chara_card_v2", "spec_version":
    "2.0", "data": {...}}``) -- V3 cards keep their V3-only fields and their
    ``chara_card_v3`` spec. An embedded ``character_book`` is written with
    :func:`export_character_book`, so importing and re-exporting a card is
    lossless apart from the divergences listed below.

``export_lorebook``
    A standalone lorebook: ``{"spec": "lorebook_v3", "data": {...}}`` when the
    book carries V3 book level fields, otherwise a V2 ``Lorebook`` object.

Known divergences (documented, not bugs)
----------------------------------------

* ``id`` is always written on export, even when the source card omitted it.
  ``convertCharacterBook`` (``world-info.js:5622-5624``) assigns the array index
  in that case, so the information is already lost on import.
* ``use_regex: true`` is written on the book entries (ST does the same in
  ``convertWorldInfoToCharacterBook``) but is **not** read back on import: ST keys
  carry their own ``/re/flags``, so a regex flag cannot be a per-entry switch.
* ``enabled`` is written from the runtime ``disable`` field, so the default
  direction is exact. A card book that *omitted* both keys yields
  ``enabled: true``; the omission itself is only restored when the untouched
  payload is still available under ``extensions.original_data`` (see below).
* The reverse exporter does **not** invent a second direction for the optional
  V2 top level fields (``addMemo``, ``use_regex``, and any vendor extension):
  the backend has no field to read them from. Import keeps them in
  ``WorldBook.extensions["original_data"]`` (mirroring ST's
  ``result.originalData``, ``world-info.js:5618``), and the exporter replays that
  object so a card imported from JSON re-exports byte-for-byte on those fields.

Zero third party dependencies: a PNG export is assembled from the standard
library (``zlib``/``struct``) and, when Pillow happens to be installed, upgraded
with real image data so image hosts do not reject it.
"""

from __future__ import annotations

import json
import logging
import struct
import zlib
from collections.abc import Mapping
from typing import Any

from tavern.st.cards import SPEC_V2, SPEC_V3, CharacterCard
from tavern.st.importers import (
    ORIGINAL_DATA_KEY,
    RUNTIME_DEFAULTS,
    RUNTIME_TO_V2_EXTENSIONS,
    SPEC_LOREBOOK_V3,
    V2_EXTENSION_COLUMNS,
    number_to_v2_position,
)
from tavern.st.worldbook import WorldBook, WorldInfoEntry

logger = logging.getLogger(__name__)


#: The 28 runtime field names whose V2 home is ``extensions.*``, in the order
#: ``convertWorldInfoToCharacterBook`` writes them (``endpoints_characters.js:682``).
#: ``position`` is added by the entry exporter itself (it always writes the full
#: 0..7 enum), so it is not repeated here.
_EXTENSION_FIELD_ORDER: tuple[str, ...] = (
    "excludeRecursion",
    "preventRecursion",
    "displayIndex",
    "probability",
    "useProbability",
    "depth",
    "selectiveLogic",
    "outletName",
    "group",
    "groupOverride",
    "groupWeight",
    "delayUntilRecursion",
    "scanDepth",
    "matchWholeWords",
    "useGroupScoring",
    "caseSensitive",
    "automationId",
    "role",
    "vectorized",
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
)


def _column_for(runtime_field: str) -> str:
    """``extensions`` key for a native field: the reverse map, else snake_case.

    ``group`` and ``outletName`` are extension-only in the runtime model, so they
    are absent from :data:`~tavern.st.importers.RUNTIME_TO_V2_EXTENSIONS` and are
    resolved with the same ``[A-Z] -> _lower`` rule ST uses for its paths
    (``world-info.js:3237``).
    """
    mapped = RUNTIME_TO_V2_EXTENSIONS.get(runtime_field)
    if mapped:
        return mapped
    return "".join(f"_{char.lower()}" if char.isupper() else char for char in runtime_field)


#: ``(runtime field, extensions key)`` pairs, in upstream order.
_V2_EXTENSION_FIELDS: tuple[tuple[str, str], ...] = tuple(
    (name, _column_for(name)) for name in _EXTENSION_FIELD_ORDER
)


def _check_extension_columns() -> None:
    """Import-time guard: the exporter must cover every ``extensions`` column.

    ``V2_EXTENSION_COLUMNS`` is owned by ``importers.py`` (one entry per
    ``extensions.*`` row of ``FIELD_MAPPING``); this module owns the runtime names.
    Keeping the two in step is what makes ``convertWorldInfoToCharacterBook`` and
    ``convertCharacterBook`` mirror images, so a drift is a hard failure rather
    than a silently dropped field.
    """
    wanted = set(V2_EXTENSION_COLUMNS) - {"position"}
    exported = {column for _field, column in _V2_EXTENSION_FIELDS}
    if wanted != exported:
        missing = sorted(wanted - exported)
        extra = sorted(exported - wanted)
        raise ExportError(f"extension column drift: missing={missing} unexpected={extra}")


_check_extension_columns()


class ExportError(ValueError):
    """Raised when an object cannot be serialised back to a SillyTavern payload."""


# ---------------------------------------------------------------------------
# lorebook / character book (direction B: runtime -> V2)
# ---------------------------------------------------------------------------


def _export_extension_value(
    runtime: Any,
    field: str,
    original: Mapping[str, Any] | None,
) -> Any:
    """One ``extensions.*`` value for the exported V2 entry.

    Priority:

    1. the value the imported payload had, when it is still known
       (``original`` is the untouched source entry) -- this preserves ``null`` and
       vendor specific values exactly;
    2. otherwise the runtime value, but **only** when it differs from
       ``newWorldInfoEntryTemplate``'s default -- writing defaults would materialise
       keys the source never had;
    3. otherwise ``None``, meaning "omit".
    """
    if original is not None:
        for candidate in (field,) + _extension_aliases(field):
            if candidate in original:
                return original[candidate]
    value = _read_runtime_field(runtime, field)
    if value == RUNTIME_DEFAULTS.get(field, _SENTINEL):
        return None
    return value


_SENTINEL = object()

#: Native field -> the keys a V2 payload may have carried it under.
_ALIASES: dict[str, tuple[str, ...]] = {
    "useProbability": ("useProbability", "use_probability"),
    "ignoreBudget": ("ignore_budget", "ignoreBudget"),
    "displayIndex": ("display_index", "displayIndex"),
    "scanDepth": ("scan_depth", "scanDepth"),
    "caseSensitive": ("case_sensitive", "caseSensitive"),
    "matchWholeWords": ("match_whole_words", "matchWholeWords"),
    "useGroupScoring": ("use_group_scoring", "useGroupScoring"),
    "automationId": ("automation_id", "automationId"),
    "groupOverride": ("group_override", "groupOverride"),
    "groupWeight": ("group_weight", "groupWeight"),
    "excludeRecursion": ("exclude_recursion", "excludeRecursion"),
    "preventRecursion": ("prevent_recursion", "preventRecursion"),
    "delayUntilRecursion": ("delay_until_recursion", "delayUntilRecursion"),
    "outletName": ("outlet_name", "outletName"),
}


def _extension_aliases(field: str) -> tuple[str, ...]:
    """Alternative spellings a V2 entry may have used for ``field``."""
    return _ALIASES.get(field, ())


def _read_runtime_field(runtime: WorldInfoEntry, field: str) -> Any:
    """Read one native field off a runtime entry (attributes + extensions)."""
    if field in RUNTIME_TO_V2_EXTENSIONS:
        attribute = _RUNTIME_ATTRIBUTES.get(field)
        if attribute is not None:
            return getattr(runtime, attribute)
    return runtime.extensions.get(field)


#: Native runtime name -> :class:`WorldInfoEntry` attribute, for the fields that
#: have one. Everything else lives in ``extensions``.
_RUNTIME_ATTRIBUTES: dict[str, str] = {
    "selectiveLogic": "selective_logic",
    "excludeRecursion": "exclude_recursion",
    "preventRecursion": "prevent_recursion",
    "delayUntilRecursion": "delay_until_recursion",
    "displayIndex": "display_index",
    "depth": "depth",
    "probability": "probability",
    "useProbability": "use_probability",
    "position": "position",
    "role": "role",
    "group": "group",
    "groupOverride": "group_override",
    "groupWeight": "group_weight",
    "scanDepth": "scan_depth",
    "caseSensitive": "case_sensitive",
    "matchWholeWords": "match_whole_words",
    "useGroupScoring": "use_group_scoring",
    "automationId": "automation_id",
    "vectorized": "vectorized",
    "sticky": "sticky",
    "cooldown": "cooldown",
    "delay": "delay",
    "ignoreBudget": "ignore_budget",
}


def export_character_book_entry(
    entry: WorldInfoEntry,
    original: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert one runtime entry into a V2 ``character_book.entries[]`` row.

    This is ``convertWorldInfoToCharacterBook`` (``endpoints_characters.js:663``)
    read backwards, with one addition: when ``original`` (the untouched source
    entry, from ``WorldBook.extensions["original_data"]``) is available, any field
    the runtime model cannot represent is replayed verbatim -- including a
    deliberate ``null`` and vendor extensions.
    """
    original = dict(original) if isinstance(original, Mapping) else None

    extensions: dict[str, Any] = {}
    if original is not None:
        # ``...entry.extensions`` spread first: unknown vendor keys survive.
        for key, value in (original.get("extensions") or {}).items():
            extensions[key] = value
    # ``extensions.position`` is the full 0..7 enum and is always written; ST
    # writes it first (``endpoints_characters.js:684``).
    extensions["position"] = entry.position
    for field, key in _V2_EXTENSION_FIELDS:
        value = _export_extension_value(entry, field, original)
        if value is not None:
            extensions[key] = value

    v2_entry: dict[str, Any] = {}
    if original is not None:
        v2_entry.update(original)
    v2_entry.update(
        {
            "id": entry.uid,
            "keys": list(entry.keys),
            "secondary_keys": list(entry.secondary_keys),
            "comment": entry.comment,
            "content": entry.content,
            "constant": bool(entry.constant),
            "selective": bool(entry.selective),
            "insertion_order": entry.insertion_order,
            "enabled": not entry.disable,
            # V2's ``position`` is only ever one of two strings; the full enum
            # rides along in ``extensions.position`` (same as upstream).
            "position": number_to_v2_position(entry.position),
            "use_regex": True,
            "extensions": extensions,
        }
    )
    return v2_entry


def export_character_book(book: WorldBook) -> dict[str, Any]:
    """Serialise a :class:`WorldBook` as a V2/V3 ``character_book`` object.

    The untouched source payload (when the book was imported with
    :func:`tavern.st.importers.convert_character_book`) supplies the book level
    fields, so ``name``/``description``/``scan_depth``/``token_budget``/
    ``recursive_scanning``/``extensions`` round trip exactly. ``entries`` is a
    list, in ``uid`` order, exactly like ST writes it.
    """
    original = _original_book_data(book)
    rows = _original_entry_rows(original)

    # Start from the untouched source object so book level vendor fields survive,
    # then overwrite every field the runtime model owns. ``entries`` is always
    # rebuilt below, never copied.
    payload: dict[str, Any] = {}
    if isinstance(original, Mapping):
        payload.update(original)
    payload.pop("entries", None)

    entries: list[dict[str, Any]] = []
    for entry in sorted(book.entries, key=lambda item: item.uid):
        entries.append(export_character_book_entry(entry, rows.get(entry.uid)))
    payload["entries"] = entries

    if book.name:
        payload["name"] = book.name
    if book.description:
        payload["description"] = book.description
    for field, value in (
        ("scan_depth", book.scan_depth),
        ("token_budget", book.token_budget),
    ):
        if value is not None:
            payload[field] = value
    if book.recursive_scanning or "recursive_scanning" in payload:
        # Only materialise a ``false`` when the source had the field: ``false`` is
        # the V2 default, so writing it would add noise to a round trip.
        payload["recursive_scanning"] = bool(book.recursive_scanning)
    # ``extensions`` is part of the V2 ``Lorebook`` interface and is **not** the
    # ``WorldBook.extensions`` dict: that one holds our own bookkeeping (for
    # example ``original_data``), which must never leak into an export.
    payload["extensions"] = (
        dict(original.get("extensions") or {}) if isinstance(original, Mapping) else {}
    )
    return payload


def export_lorebook(book: WorldBook) -> dict[str, Any]:
    """Serialise a :class:`WorldBook` as a standalone SillyTavern lorebook.

    Returns ``{"spec": "lorebook_v3", "data": {...}}`` when the book carries the
    V3 book level fields (``scan_depth`` / ``token_budget`` /
    ``recursive_scanning`` -- checked in that order, exactly like the V3 spec's
    ``Lorebook`` object), and a plain V2 ``Lorebook`` object otherwise. Either
    result feeds straight back into
    :func:`tavern.st.importers.import_lorebook`.
    """
    data = export_character_book(book)
    if _is_v3_shaped(book, data):
        return {"spec": SPEC_LOREBOOK_V3, "data": data}
    return data


def _is_v3_shaped(book: WorldBook, data: Mapping[str, Any]) -> bool:
    """True when ``book`` looks like a V3 ``Lorebook`` rather than a V2 one."""
    if book.scan_depth is not None or book.token_budget is not None:
        return True
    if book.recursive_scanning:
        return True
    return any(field in data for field in ("scan_depth", "token_budget", "recursive_scanning"))


def _original_book_data(book: WorldBook) -> Mapping[str, Any] | None:
    """The untouched source payload kept by the importer, when there is one."""
    raw = book.extensions.get(ORIGINAL_DATA_KEY)
    if not isinstance(raw, Mapping):
        return None
    inner = raw.get("data")
    if isinstance(inner, Mapping) and "entries" in inner:
        # A ``lorebook_v3`` wrapper: the book itself is ``data``.
        return inner
    return raw


def _original_entry_rows(original: Mapping[str, Any] | None) -> dict[int, Mapping[str, Any]]:
    """Index the source payload's entries by id so each export can replay its own."""
    if not isinstance(original, Mapping):
        return {}
    raw_entries = original.get("entries")
    rows: dict[int, Mapping[str, Any]] = {}
    if isinstance(raw_entries, Mapping):
        for key, value in raw_entries.items():
            if not isinstance(value, Mapping):
                continue
            try:
                uid = int(float(str(key)))
            except (TypeError, ValueError):
                uid = len(rows)
            rows[uid] = value
        return rows
    if isinstance(raw_entries, (list, tuple)):
        for index, value in enumerate(raw_entries):
            if not isinstance(value, Mapping):
                continue
            raw_id = value.get("id")
            try:
                uid = int(float(str(raw_id)))
            except (TypeError, ValueError):
                uid = index
            rows[uid] = value
    return rows


# ---------------------------------------------------------------------------
# character card
# ---------------------------------------------------------------------------


def export_character_card(
    card: CharacterCard,
    *,
    as_png: bool = False,
) -> bytes | dict[str, Any]:
    """Serialise ``card`` as a character card payload.

    ``as_png=False``
        Returns a dict. V3 cards keep their V3-only fields (``nickname``,
        ``assets``, ``group_only_greetings``, ``creation_date`` ...) and their
        spec; everything else becomes a V2 envelope.
    ``as_png=True``
        Returns PNG bytes. When ``card`` came from a PNG (``cards.load_card`` /
        :func:`tavern.st.importers.import_character_card` on PNG bytes) the
        original image chunks are re-emitted with a fresh ``ccv3`` / ``chara``
        text chunk, so the exported file keeps its artwork. Otherwise a tiny valid
        1x1 PNG is generated with the standard library -- enough for
        :func:`tavern.st.cards.card_from_png` to read back, and Pillow upgrades it
        to a real placeholder image when available.
    """
    payload = _card_payload(card)
    if not as_png:
        return payload

    blob = _source_png_bytes(card)
    keyword = "ccv3" if payload.get("spec") == SPEC_V3 else "chara"
    if blob is not None:
        return _png_with_card_chunk(blob, keyword, payload)
    return _png_with_card_chunk(_placeholder_png(), keyword, payload)


def _card_payload(card: CharacterCard) -> dict[str, Any]:
    """Build the exported card envelope, preserving everything known about it."""
    raw = card.raw if isinstance(card.raw, Mapping) else {}
    data: dict[str, Any] = {}

    raw_data = raw.get("data")
    if isinstance(raw_data, Mapping):
        data.update(raw_data)

    data.update(
        {
            "name": card.name,
            "description": card.description,
            "personality": card.personality,
            "scenario": card.scenario,
            "first_mes": card.first_mes,
            "mes_example": card.mes_example,
            "creator_notes": card.creator_notes,
            "system_prompt": card.system_prompt,
            "post_history_instructions": card.post_history_instructions,
            "alternate_greetings": list(card.alternate_greetings),
            "tags": list(card.tags),
            "creator": card.creator,
            "character_version": card.character_version,
            "extensions": dict(card.extensions),
        }
    )
    if card.spec == SPEC_V3:
        data["assets"] = list(card.assets)
        data["nickname"] = card.nickname
        data["group_only_greetings"] = list(card.group_only_greetings)

    spec = card.spec or SPEC_V2
    if spec not in (SPEC_V2, SPEC_V3):
        # A V1 card is up-converted, which is what SillyTavern's importer does.
        spec = SPEC_V2
    version = card.spec_version
    if not version:
        version = "3.0" if spec == SPEC_V3 else "2.0"
    return {"spec": spec, "spec_version": version, "data": data}


def _source_png_bytes(card: CharacterCard) -> bytes | None:
    """Original PNG the card was read from, when the importer kept it."""
    raw = card.raw if isinstance(card.raw, Mapping) else {}
    for holder in (raw, raw.get("data") if isinstance(raw.get("data"), Mapping) else {}):
        if not isinstance(holder, Mapping):
            continue
        blob = holder.get("_source_png")
        if isinstance(blob, (bytes, bytearray)):
            return bytes(blob)
    return None


# ---------------------------------------------------------------------------
# PNG (standard library only)
# ---------------------------------------------------------------------------

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: Chunks that are part of the card metadata and must be replaced, never copied.
_CARD_CHUNK_KEYWORDS: tuple[str, ...] = ("chara", "ccv3", "naidata")


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """One PNG chunk with its CRC (the same layout ``cards.py`` parses)."""
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def _card_text_chunk(keyword: str, payload: Mapping[str, Any]) -> bytes:
    """A ``tEXt`` chunk carrying the base64 card JSON (ST's own storage format)."""
    import base64

    encoded = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    return _png_chunk(b"tEXt", keyword.encode("ascii") + b"\x00" + encoded)


def _placeholder_png() -> bytes:
    """A minimal valid 1x1 PNG (Pillow makes it prettier when installed)."""
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        _PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + _png_chunk(b"IEND", b"")
    )


def _copy_image_chunks(blob: bytes) -> list[bytes]:
    """Every chunk of ``blob`` except the card metadata blocks."""
    if not blob.startswith(_PNG_SIGNATURE):
        return []
    chunks: list[bytes] = []
    offset = len(_PNG_SIGNATURE)
    total = len(blob)
    while offset + 8 <= total:
        (length,) = struct.unpack(">I", blob[offset : offset + 4])
        chunk_type = blob[offset + 4 : offset + 8]
        data_end = offset + 8 + length
        if data_end + 4 > total:
            break
        if chunk_type == b"tEXt":
            keyword, _, _value = blob[offset + 8 : data_end].partition(b"\x00")
            if keyword.decode("latin-1", errors="replace") in _CARD_CHUNK_KEYWORDS:
                offset = data_end + 4
                continue
        chunks.append(blob[offset : data_end + 4])
        if chunk_type == b"IEND":
            break
        offset = data_end + 4
    return chunks


def _png_with_card_chunk(blob: bytes, keyword: str, payload: Mapping[str, Any]) -> bytes:
    """Return ``blob`` with its card metadata replaced by ``payload``.

    The card chunk is inserted right after ``IHDR`` (where SillyTavern puts it) and
    every other text chunk is kept, so unrelated metadata survives.
    """
    chunks = _copy_image_chunks(blob)
    if not chunks:
        chunks = _copy_image_chunks(_placeholder_png())

    card_chunk = _card_text_chunk(keyword, payload)
    if chunks and chunks[0][4:8] == b"IHDR":
        chunks.insert(1, card_chunk)
    else:  # pragma: no cover - only reachable for a hand-built blob without IHDR
        chunks.insert(0, card_chunk)
    return _PNG_SIGNATURE + b"".join(chunks)
