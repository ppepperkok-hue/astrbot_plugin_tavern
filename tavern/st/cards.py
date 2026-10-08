"""SillyTavern character card parsing utilities.

This module is intentionally free of any AstrBot import so it can be unit
tested offline. It focuses on the data layer only: reading SillyTavern
character cards from ``.json`` files and from PNG ``tEXt``/``iTXt`` metadata
chunks, and normalising V1 / V2 / V3 cards into one dataclass.
"""

from __future__ import annotations

import base64
import binascii
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tavern.log import logger

#: PNG keywords used by SillyTavern to store the base64 encoded card JSON.
#: Character Card V3 says an application that finds both chunks SHOULD prefer
#: ``ccv3``; ``chara`` (V2) is only the fallback.
PNG_CARD_KEYWORDS: tuple[str, ...] = ("ccv3", "chara")

#: Values accepted in the ``spec`` field of V2 / V3 cards.
SPEC_V2 = "chara_card_v2"
SPEC_V3 = "chara_card_v3"

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_TEXT_CHUNK_TYPES = (b"tEXt", b"iTXt", b"zTXt")


class CharacterCardError(ValueError):
    """Raised when a file cannot be recognised as a character card."""


@dataclass
class CharacterCard:
    """Normalised view over a SillyTavern character card (V1 / V2 / V3)."""

    name: str = ""
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    # V1 only: the V1 ``system_prompt`` lived inside
    # ``data.system_prompt`` while ``mes_example`` etc. stayed top level.
    system_prompt: str = ""
    post_history_instructions: str = ""
    creator_notes: str = ""
    alternate_greetings: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    creator: str = ""
    character_version: str = ""
    #: V3 additions. Kept as raw dicts because we only pass them through.
    assets: list[Any] = field(default_factory=list)
    nickname: str = ""
    group_only_greetings: list[str] = field(default_factory=list)
    extensions: dict[str, Any] = field(default_factory=dict)
    #: Bookkeeping, not part of the card itself.
    spec: str = "chara_card_v1"
    spec_version: str = ""
    source_path: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_group_only(self) -> bool:
        """True when the card declares greetings meant for group chats only."""
        return bool(self.group_only_greetings)

    def greeting(self, index: int = 0) -> str:
        """Return the greeting at ``index``; falls back to the first one."""
        greetings = [self.first_mes, *self.alternate_greetings]
        greetings = [g for g in greetings if g]
        if not greetings:
            return ""
        if 0 <= index < len(greetings):
            return greetings[index]
        return greetings[0]

    def to_v2_dict(self) -> dict[str, Any]:
        """Export the card in Character Card V2 shape (round-trip friendly)."""
        data: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "personality": self.personality,
            "scenario": self.scenario,
            "first_mes": self.first_mes,
            "mes_example": self.mes_example,
            "creator_notes": self.creator_notes,
            "system_prompt": self.system_prompt,
            "post_history_instructions": self.post_history_instructions,
            "alternate_greetings": list(self.alternate_greetings),
            "tags": list(self.tags),
            "creator": self.creator,
            "character_version": self.character_version,
            "extensions": dict(self.extensions),
        }
        if self.spec == SPEC_V3:
            data.update(
                {
                    "assets": list(self.assets),
                    "nickname": self.nickname,
                    "group_only_greetings": list(self.group_only_greetings),
                }
            )
        return {"spec": SPEC_V2, "spec_version": "2.0", "data": data}


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "\n".join(_as_str(item) for item in value if item is not None)
    return json.dumps(value, ensure_ascii=False)


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [line for line in value.splitlines() if line.strip()]
    if isinstance(value, list):
        return [_as_str(item) for item in value if _as_str(item)]
    return [_as_str(value)]


def card_from_dict(payload: dict[str, Any], source_path: str = "") -> CharacterCard:
    """Build a :class:`CharacterCard` from an already decoded card dictionary.

    Accepts V1 flat cards, V2 ``{"spec": ..., "data": {...}}`` cards and V3
    cards (which use the same envelope with extra fields inside ``data``).
    """
    if not isinstance(payload, dict):
        raise CharacterCardError("character card payload must be a JSON object")

    spec = _as_str(payload.get("spec")) or "chara_card_v1"
    spec_version = _as_str(payload.get("spec_version"))

    if "data" in payload and isinstance(payload["data"], dict):
        data = payload["data"]
    else:
        data = payload
        spec = "chara_card_v1" if not spec else spec

    card = CharacterCard(
        name=_as_str(data.get("name")),
        description=_as_str(data.get("description")),
        personality=_as_str(data.get("personality")),
        scenario=_as_str(data.get("scenario")),
        first_mes=_as_str(data.get("first_mes")),
        mes_example=_as_str(data.get("mes_example")),
        system_prompt=_as_str(data.get("system_prompt") or payload.get("system_prompt")),
        post_history_instructions=_as_str(
            data.get("post_history_instructions") or payload.get("post_history_instructions")
        ),
        creator_notes=_as_str(data.get("creator_notes")),
        alternate_greetings=_as_str_list(data.get("alternate_greetings")),
        tags=_as_str_list(data.get("tags")),
        creator=_as_str(data.get("creator")),
        character_version=_as_str(data.get("character_version")),
        assets=list(data.get("assets") or []),
        nickname=_as_str(data.get("nickname")),
        group_only_greetings=_as_str_list(data.get("group_only_greetings")),
        extensions=data.get("extensions") if isinstance(data.get("extensions"), dict) else {},
        spec=spec,
        spec_version=spec_version,
        source_path=source_path,
        raw=payload,
    )
    if not card.nickname:
        card.nickname = card.name
    return card


def _decode_png_text_chunks(blob: bytes) -> dict[str, str]:
    """Extract ``tEXt`` / ``iTXt`` / ``zTXt`` chunks from a PNG byte string."""
    import zlib

    if not blob.startswith(_PNG_SIGNATURE):
        raise CharacterCardError("not a PNG file (bad signature)")

    chunks: dict[str, str] = {}
    offset = len(_PNG_SIGNATURE)
    total = len(blob)
    while offset + 8 <= total:
        (length,) = struct.unpack(">I", blob[offset : offset + 4])
        chunk_type = blob[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        if data_end + 4 > total:
            break
        if chunk_type in _TEXT_CHUNK_TYPES:
            data = blob[data_start:data_end]
            try:
                keyword, value = _parse_text_chunk(chunk_type, data, zlib)
            except Exception as exc:  # noqa: BLE001 - malformed card must not crash
                logger.debug("skip malformed text chunk %s: %s", chunk_type, exc)
            else:
                if keyword and value is not None:
                    chunks[keyword] = value
        offset = data_end + 4  # skip CRC
        if chunk_type == b"IEND":
            break
    return chunks


def _parse_text_chunk(chunk_type: bytes, data: bytes, zlib_module: Any) -> tuple[str, str]:
    if chunk_type == b"tEXt":
        keyword, _, value = data.partition(b"\x00")
        return keyword.decode("latin-1"), value.decode("latin-1")
    if chunk_type == b"zTXt":
        keyword, _, rest = data.partition(b"\x00")
        if not rest:
            return keyword.decode("latin-1"), ""
        value = zlib_module.decompress(rest[1:])
        return keyword.decode("latin-1"), value.decode("latin-1")
    # iTXt: keyword \0 compression_flag compression_method language \0 translated \0 text
    keyword, _, rest = data.partition(b"\x00")
    if len(rest) < 2:
        return keyword.decode("latin-1"), ""
    compression_flag, _, rest = rest[0], rest[1], rest[2:]
    _, _, rest = rest.partition(b"\x00")  # language tag
    _, _, text = rest.partition(b"\x00")  # translated keyword
    if compression_flag == 1:
        text = zlib_module.decompress(text)
    return keyword.decode("latin-1"), text.decode("utf-8", errors="replace")


def card_from_png(blob: bytes, source_path: str = "") -> CharacterCard:
    """Build a :class:`CharacterCard` from PNG bytes carrying card metadata."""
    chunks = _decode_png_text_chunks(blob)
    payload_b64: str | None = None
    for keyword in PNG_CARD_KEYWORDS:
        if keyword in chunks:
            payload_b64 = chunks[keyword]
            break
    if payload_b64 is None:
        raise CharacterCardError(
            f"PNG has no character card metadata (expected one of {', '.join(PNG_CARD_KEYWORDS)})"
        )

    try:
        decoded = base64.b64decode(payload_b64, validate=False)
        payload = json.loads(decoded.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CharacterCardError(
            f"character card metadata is not valid base64 JSON: {exc}"
        ) from exc
    return card_from_dict(payload, source_path=source_path)


def load_card(path: str | Path) -> CharacterCard:
    """Load a character card from ``.json`` or ``.png``.

    ``.yaml`` / ``.yml`` cards are supported when PyYAML is importable.
    """
    path = Path(path)
    if not path.is_file():
        raise CharacterCardError(f"character card not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".png":
        return card_from_png(path.read_bytes(), source_path=str(path))

    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise CharacterCardError(
                "YAML character cards need PyYAML installed (pip install pyyaml)"
            ) from exc
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(payload, dict):
        raise CharacterCardError("character card root must be a JSON object")
    # Some exporters wrap the card one more time.
    if "data" not in payload and "character" in payload and isinstance(payload["character"], dict):
        payload = payload["character"]
    return card_from_dict(payload, source_path=str(path))


def scan_cards(directory: str | Path) -> list[CharacterCard]:
    """Load every character card found in ``directory`` (non recursive).

    Files that fail to parse are skipped, so one broken card cannot take down
    the whole card library.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []

    cards: list[CharacterCard] = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in (".json", ".png", ".yaml", ".yml"):
            continue
        try:
            cards.append(load_card(path))
        except Exception as exc:  # noqa: BLE001 - keep scanning
            logger.warning("skip character card %s: %s", path.name, exc)
    return cards


# Backwards/forwards friendly aliases used by the plugin layer.
parse_card_dict = card_from_dict
parse_card_file = load_card
