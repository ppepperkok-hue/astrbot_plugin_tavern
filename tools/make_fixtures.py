"""Generate SillyTavern format fixtures used by the offline test suite.

Run with ``python tools/make_fixtures.py``. Everything is written into
``tests/fixtures/`` and is deterministic, so the fixtures can be regenerated
and diffed.
"""

from __future__ import annotations

import base64
import json
import struct
import zlib
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

CARD_V2 = {
    "spec": "chara_card_v2",
    "spec_version": "2.0",
    "data": {
        "name": "Iris",
        "description": "Iris is a lighthouse keeper on a storm-battered coast.",
        "personality": "Calm, laconic, unnervingly observant.",
        "scenario": "The player washes ashore after a shipwreck.",
        "first_mes": 'The lamp sweeps past your face. "You are awake," she says.',
        "mes_example": "<START>\n{{user}}: Where am I?\n{{char}}: The only dry rock for miles.",
        "creator_notes": "Written for a slow-burn mystery.",
        "system_prompt": "Stay in character. Never break the fourth wall.",
        "post_history_instructions": "Keep replies under four sentences.",
        "alternate_greetings": ["A second opening.", "A third opening."],
        "tags": ["mystery", "slow-burn"],
        "creator": "fixture-author",
        "character_version": "1.2.0",
        "extensions": {
            "talkativeness": "0.5",
            "depth_prompt": {"depth": 4, "prompt": "Stay terse."},
        },
    },
}

CARD_V1 = {
    "name": "Old Keeper",
    "description": "A flat V1 card.",
    "personality": "Gruff",
    "scenario": "A dusty archive",
    "first_mes": "Mind the dust.",
    "mes_example": "",
    "creatorcomment": "legacy field that the parser ignores",
    "talkativeness": "0.5",
}

CARD_V3 = {
    "spec": "chara_card_v3",
    "spec_version": "3.0",
    "data": {
        "name": "Vesper",
        "description": "A courier with too many secrets.",
        "personality": "Wry",
        "scenario": "Neon arcology",
        "first_mes": "You again.",
        "mes_example": "",
        "creator_notes": "",
        "creator_notes_multilingual": {"zh-cn": "一个话里有话的信使。"},
        "alternate_greetings": [],
        "group_only_greetings": ["Group opening."],
        "tags": ["cyberpunk"],
        "creator": "fixture-author",
        "character_version": "3.0.0",
        "nickname": "Ves",
        "assets": [{"type": "icon", "uri": "ccdefault:", "name": "main", "ext": "png"}],
        "extensions": {},
        "creation_date": 1767225600,
        "source": ["https://example.invalid/card/vesper"],
    },
}

WORLD_BOOK = {
    "name": "Lighthouse Lore",
    "description": "Fixture world book covering the documented V2 fields.",
    "scan_depth": 4,
    "token_budget": 1024,
    "recursive_scanning": True,
    "extensions": {},
    "entries": {
        "0": {
            "uid": 0,
            "key": ["lighthouse", "tower"],
            "keysecondary": [],
            "comment": "Always on: the lighthouse itself",
            "content": "The lighthouse is 44 metres tall and painted white.",
            "constant": True,
            "selective": False,
            "selectiveLogic": 0,
            "addMemo": True,
            "order": 100,
            "position": 0,
            "disable": False,
            "excludeRecursion": False,
            "preventRecursion": False,
            "delayUntilRecursion": False,
            "probability": 100,
            "useProbability": True,
            "depth": 4,
            "group": "",
            "groupOverride": False,
            "groupWeight": 100,
            "scanDepth": None,
            "caseSensitive": None,
            "matchWholeWords": None,
            "useGroupScoring": None,
            "automationId": "",
            "role": None,
            "vectorized": False,
            "sticky": 0,
            "cooldown": 0,
            "delay": 0,
            "displayIndex": 0,
        },
        "1": {
            "uid": 1,
            "key": ["shipwreck", "wreck"],
            "keysecondary": ["Sundered"],
            "comment": "Selective: the wreck of the Sundered",
            "content": "The Sundered broke on the rocks eleven winters ago.",
            "constant": False,
            "selective": True,
            "selectiveLogic": 0,
            "order": 90,
            "position": 1,
            "disable": False,
            "probability": 100,
            "useProbability": True,
            "depth": 4,
            "group": "wrecks",
            "groupWeight": 100,
            "scanDepth": None,
            "caseSensitive": False,
            "matchWholeWords": False,
            "vectorized": False,
            "sticky": 2,
            "cooldown": 3,
            "delay": 0,
            "displayIndex": 1,
        },
        "2": {
            "uid": 2,
            "key": ["/gull\\d+/i"],
            "keysecondary": [],
            "comment": "Regex keys",
            "content": "The gulls are numbered and they know it.",
            "constant": False,
            "selective": False,
            "order": 80,
            "position": 0,
            "disable": False,
            "probability": 100,
            "useProbability": True,
            "depth": 4,
            "group": "",
            "scanDepth": None,
            "caseSensitive": False,
            "matchWholeWords": False,
            "vectorized": False,
            "sticky": 0,
            "cooldown": 0,
            "delay": 0,
            "displayIndex": 2,
        },
        "3": {
            "uid": 3,
            "key": ["keep", "keeper"],
            "keysecondary": [],
            "comment": "Whole word matching demo",
            "content": "Keeper is a title here, not a name.",
            "constant": False,
            "selective": False,
            "order": 70,
            "position": 4,
            "disable": False,
            "probability": 100,
            "useProbability": True,
            "depth": 2,
            "group": "",
            "scanDepth": None,
            "caseSensitive": False,
            "matchWholeWords": True,
            "vectorized": False,
            "sticky": 0,
            "cooldown": 0,
            "delay": 0,
            "displayIndex": 3,
        },
        "4": {
            "uid": 4,
            "key": ["fog"],
            "keysecondary": [],
            "comment": "Disabled entry",
            "content": "This should never activate.",
            "constant": False,
            "selective": False,
            "order": 60,
            "position": 0,
            "disable": True,
            "probability": 100,
            "useProbability": True,
            "depth": 4,
            "group": "",
            "scanDepth": None,
            "caseSensitive": False,
            "matchWholeWords": False,
            "vectorized": False,
            "sticky": 0,
            "cooldown": 0,
            "delay": 0,
            "displayIndex": 4,
        },
        "5": {
            "uid": 5,
            "key": ["tide"],
            "keysecondary": [],
            "comment": "Probability 0 entry",
            "content": "Should never activate with useProbability at 100 percent.",
            "constant": False,
            "selective": False,
            "order": 50,
            "position": 0,
            "disable": False,
            "probability": 0,
            "useProbability": True,
            "depth": 4,
            "group": "",
            "scanDepth": None,
            "caseSensitive": False,
            "matchWholeWords": False,
            "vectorized": False,
            "sticky": 0,
            "cooldown": 0,
            "delay": 0,
            "displayIndex": 5,
        },
    },
}


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def _make_card_png(card: dict, image_size: int = 8) -> bytes:
    """Build a minimal valid PNG that carries the card in a ``chara`` tEXt chunk."""
    width = height = image_size
    raw = b"".join(b"\x00" + bytes([30, 40, 60] * width) for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    payload = base64.b64encode(json.dumps(card, ensure_ascii=False).encode("utf-8")).decode("ascii")
    text = b"chara\x00" + payload.encode("ascii")
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"tEXt", text)
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    cards_dir = FIXTURES / "cards"
    cards_dir.mkdir(exist_ok=True)

    (cards_dir / "iris.json").write_text(json.dumps(CARD_V2, ensure_ascii=False, indent=2), "utf-8")
    (cards_dir / "old_keeper.json").write_text(
        json.dumps(CARD_V1, ensure_ascii=False, indent=2), "utf-8"
    )
    (cards_dir / "vesper_v3.json").write_text(
        json.dumps(CARD_V3, ensure_ascii=False, indent=2), "utf-8"
    )
    (cards_dir / "iris.png").write_bytes(_make_card_png(CARD_V2))
    (cards_dir / "broken.json").write_text("{not json at all", "utf-8")

    books_dir = FIXTURES / "worldbooks"
    books_dir.mkdir(exist_ok=True)
    (books_dir / "lighthouse.json").write_text(
        json.dumps(WORLD_BOOK, ensure_ascii=False, indent=2), "utf-8"
    )

    print(f"fixtures written to {FIXTURES}")


if __name__ == "__main__":
    main()
