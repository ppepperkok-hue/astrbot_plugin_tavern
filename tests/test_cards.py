"""Offline tests for the SillyTavern character card parser."""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tavern.st.cards import (  # noqa: E402
    CharacterCardError,
    card_from_dict,
    card_from_png,
    load_card,
    scan_cards,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CARDS = FIXTURES / "cards"


@pytest.fixture(scope="session", autouse=True)
def _fixtures() -> None:
    if not CARDS.is_dir():
        subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "make_fixtures.py")],
            check=True,
            cwd=REPO_ROOT,
        )


def test_v2_card_fields() -> None:
    card = load_card(CARDS / "iris.json")
    assert card.spec == "chara_card_v2"
    assert card.name == "Iris"
    assert card.personality.startswith("Calm")
    assert card.system_prompt.startswith("Stay in character")
    assert card.post_history_instructions.startswith("Keep replies")
    assert card.alternate_greetings == ["A second opening.", "A third opening."]
    assert card.tags == ["mystery", "slow-burn"]
    assert card.nickname == "Iris"  # falls back to name when absent
    assert card.extensions["talkativeness"] == "0.5"


def test_v1_card_is_upgraded() -> None:
    card = load_card(CARDS / "old_keeper.json")
    assert card.spec == "chara_card_v1"
    assert card.name == "Old Keeper"
    assert card.first_mes == "Mind the dust."
    assert card.system_prompt == ""
    exported = card.to_v2_dict()
    assert exported["spec"] == "chara_card_v2"
    assert exported["data"]["name"] == "Old Keeper"


def test_v3_card_extra_fields() -> None:
    card = load_card(CARDS / "vesper_v3.json")
    assert card.spec == "chara_card_v3"
    assert card.nickname == "Ves"
    assert card.group_only_greetings == ["Group opening."]
    assert card.assets and card.assets[0]["type"] == "icon"
    assert card.is_group_only is True
    exported = card.to_v2_dict()
    assert exported["data"]["nickname"] == "Ves"


def test_greeting_selection() -> None:
    card = load_card(CARDS / "iris.json")
    assert card.greeting(0) == card.first_mes
    assert card.greeting(1) == "A second opening."
    assert card.greeting(99) == card.first_mes  # out of range falls back


def test_png_card_round_trip() -> None:
    card = load_card(CARDS / "iris.png")
    assert card.name == "Iris"
    assert card.first_mes == load_card(CARDS / "iris.json").first_mes


def test_png_without_metadata_raises() -> None:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00\x00\x00\x00"
    blob = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", len(ihdr))
        + b"IHDR"
        + ihdr
        + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr) & 0xFFFFFFFF)
        + struct.pack(">I", len(zlib.compress(raw)))
        + b"IDAT"
        + zlib.compress(raw)
        + struct.pack(">I", 0)
        + b"IEND"
        + struct.pack(">I", 0)
    )
    with pytest.raises(CharacterCardError):
        card_from_png(blob)


def test_scan_cards_skips_broken_files() -> None:
    cards = scan_cards(CARDS)
    names = {card.name for card in cards}
    assert {"Iris", "Old Keeper", "Vesper"} <= names


def test_card_from_dict_rejects_non_dict() -> None:
    with pytest.raises(CharacterCardError):
        card_from_dict(["not", "a", "card"])  # type: ignore[arg-type]


def test_list_fields_are_joined_not_crashed() -> None:
    card = card_from_dict({"name": "X", "description": ["line one", "line two"]})
    assert card.description == "line one\nline two"
    assert json.loads(json.dumps(card.to_v2_dict()))["data"]["name"] == "X"
