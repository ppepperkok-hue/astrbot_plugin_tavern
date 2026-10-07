"""Offline tests for the SillyTavern import/export format layer.

The fixtures under ``tests/fixtures/imports/`` are hand written from the upstream
field lists (``endpoints_characters.js:663-722`` and ``world-info.js:5617-5674``)
so that a mapping regression fails a test instead of silently losing a field.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tavern.st import exporters as exporters  # noqa: E402
from tavern.st import importers as importers  # noqa: E402
from tavern.st.cards import card_from_png  # noqa: E402
from tavern.st.importers import (  # noqa: E402
    FIELD_MAPPING,
    ORIGINAL_DATA_KEY,
    RUNTIME_CORE_FIELDS,
    RUNTIME_TO_V2_EXTENSIONS,
    CharacterBookImportError,
    CharacterCardImportError,
    LorebookImportError,
    card_validation_issues,
    convert_character_book,
    describe_import,
    detect_lorebook_format,
    import_character_book,
    import_character_card,
    import_chat_jsonl,
    import_lorebook,
    v2_position_to_number,
)
from tavern.st.worldbook import POSITION_AT_DEPTH, WorldBook, entry_from_dict  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "imports"

#: The 41 native camelCase names the two upstream converters touch, by hand.
#: ``addMemo`` (``!!entry.comment``) and ``key_vector`` are written by upstream but
#: have no counterpart in this plugin's ``WorldInfoEntry``, so the 42-row mapping
#: table documents them in its docstring instead of giving them a column.
EXPECTED_NATIVE_FIELDS: frozenset[str] = frozenset(
    {
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
        "uid",
    }
)

#: Native fields that have no row in :data:`FIELD_MAPPING` at all: ``addMemo``
#: (upstream writes ``!!entry.comment``) and the vector-index name.
UNMAPPED_NATIVE_FIELDS: frozenset[str] = frozenset({"addMemo", "key_vector"})

#: The 31 ``extensions.*`` columns the two upstream converters carry: 30 distinct
#: keys, with ``position`` appearing both as a V2 core *string* and inside
#: ``extensions`` as the full numeric enum.
EXPECTED_V2_EXTENSION_KEYS: frozenset[str] = frozenset(
    {
        "position",
        "exclude_recursion",
        "prevent_recursion",
        "delay_until_recursion",
        "display_index",
        "probability",
        "useProbability",
        "depth",
        "selectiveLogic",
        "outlet_name",
        "group",
        "group_override",
        "group_weight",
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
    }
)

#: The 13 core (non ``extensions``) columns of the same converter.
EXPECTED_V2_CORE_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "keys",
        "secondary_keys",
        "comment",
        "content",
        "constant",
        "selective",
        "insertion_order",
        "enabled",
        "position",
        "use_regex",
        "extensions",
        "entries",
    }
)


def load_fixture(name: str) -> dict[str, Any]:
    with (FIXTURES / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def field_differences(expected: dict[str, Any], actual: dict[str, Any], label: str) -> list[str]:
    """Field-by-field comparison that names the offending field."""
    problems: list[str] = []
    for key in sorted(set(expected) | set(actual)):
        if key not in expected:
            problems.append(f"{label}.{key}: unexpected in export ({actual[key]!r})")
        elif key not in actual:
            problems.append(f"{label}.{key}: missing from export ({expected[key]!r})")
        elif expected[key] != actual[key]:
            problems.append(f"{label}.{key}: {expected[key]!r} -> {actual[key]!r}")
    return problems


def test_field_mapping_table_covers_every_upstream_field() -> None:
    """The 42-row table is the contract; prove it names all 41 + the container."""
    assert len(FIELD_MAPPING) == 42

    runtime_fields = {mapping.runtime for mapping in FIELD_MAPPING}
    # 41 per-entry columns plus the ``entries`` container row, nothing else...
    assert runtime_fields == (EXPECTED_NATIVE_FIELDS | {"key_vector"}) - UNMAPPED_NATIVE_FIELDS | {
        "entries"
    }
    # ...and together with the two unmapped names that is every native field.
    assert runtime_fields | UNMAPPED_NATIVE_FIELDS == EXPECTED_NATIVE_FIELDS | {
        "entries",
        "key_vector",
    }
    assert len(EXPECTED_NATIVE_FIELDS) == 41

    extension_columns = {
        mapping.v2.split(".", 1)[1]
        for mapping in FIELD_MAPPING
        if mapping.v2.startswith("extensions.")
    }
    assert extension_columns == EXPECTED_V2_EXTENSION_KEYS

    core_columns = {
        mapping.v2 for mapping in FIELD_MAPPING if mapping.kind == "core" and mapping.v2 != "(none)"
    }
    # Three V2 core keys have no runtime counterpart row: the container itself,
    # the spread ``extensions`` object and the constant ``use_regex`` flag.
    assert core_columns == EXPECTED_V2_CORE_FIELDS - {"entries", "extensions", "use_regex"}
    assert core_columns | {"entries", "extensions", "use_regex"} == EXPECTED_V2_CORE_FIELDS

    # Every ``extensions.*`` row must also exist in the reverse key map. Two
    # runtime names are deliberately absent from that map because
    # ``WorldInfoEntry`` keeps them only inside ``extensions``: ``group`` and
    # ``outletName``.
    assert "outletName" not in RUNTIME_TO_V2_EXTENSIONS
    assert "group" not in RUNTIME_TO_V2_EXTENSIONS
    for mapping in FIELD_MAPPING:
        if mapping.kind != "extension" or mapping.v2 == "(none)":
            continue
        column = mapping.v2.split(".", 1)[1]
        if mapping.runtime in ("group", "outletName"):
            continue
        assert RUNTIME_TO_V2_EXTENSIONS[mapping.runtime] == column
    assert RUNTIME_TO_V2_EXTENSIONS["selectiveLogic"] == "selectiveLogic"
    # The reverse map covers exactly the columns it can name.
    assert set(RUNTIME_TO_V2_EXTENSIONS.values()) == EXPECTED_V2_EXTENSION_KEYS - {
        "group",
        "outlet_name",
    }


def test_native_row_fields_match_the_mapping_table() -> None:
    """``RUNTIME_CORE_FIELDS`` is the row key list; it must not drift from the table."""
    assert len(RUNTIME_CORE_FIELDS) == 41
    # ``uid`` is the id column, not a row key; the two unmapped native names are
    # still listed so an export cannot silently forget them.
    assert set(RUNTIME_CORE_FIELDS) == (EXPECTED_NATIVE_FIELDS - {"uid"}) | {"key_vector"}
    assert {"addMemo", "key_vector"} <= set(RUNTIME_CORE_FIELDS)


# ---------------------------------------------------------------------------
# 1. the 42 field mapping, both directions
# ---------------------------------------------------------------------------


def test_character_book_round_trip_is_field_for_field() -> None:
    """V2 card -> runtime entry -> back to V2: every mapped field is byte equal."""
    payload = load_fixture("card_v2_with_book.json")
    original_book = payload["data"]["character_book"]
    original_entries = {entry["id"]: entry for entry in original_book["entries"]}

    book = convert_character_book(original_book)
    exported = exporters.export_character_book(book)

    assert [entry["id"] for entry in exported["entries"]] == [0, 3]
    for entry in exported["entries"]:
        source = original_entries[entry["id"]]
        problems = field_differences(source, entry, f"entry[{entry['id']}]")
        assert problems == []
        assert entry["extensions"] != source.get("extensions", {}) or (
            entry["extensions"] == source["extensions"]
        )

    # Book level fields round trip too.
    assert exported["name"] == original_book["name"]
    assert exported["description"] == original_book["description"]
    assert exported["scan_depth"] == original_book["scan_depth"]
    assert exported["token_budget"] == original_book["token_budget"]
    assert exported["recursive_scanning"] == original_book["recursive_scanning"]
    assert exported["extensions"] == original_book["extensions"]

    # ...and a second import produces the very same runtime entries.
    again = convert_character_book(exported)
    assert [entry.uid for entry in again.entries] == [entry.uid for entry in book.entries]
    for first, second in zip(book.entries, again.entries, strict=True):
        assert vars(first) == vars(second)


def test_every_native_field_survives_the_round_trip() -> None:
    """A V2 entry that populates all 41 fields comes back with all 41 populated."""
    source_entry: dict[str, Any] = {
        "id": 11,
        "keys": ["alpha", "beta"],
        "secondary_keys": ["gamma"],
        "comment": "Comment",
        "content": "Content",
        "constant": True,
        "selective": True,
        "insertion_order": 77,
        "enabled": False,
        "position": "after_char",
        "use_regex": True,
        "extensions": {
            "position": 4,
            "exclude_recursion": True,
            "display_index": 5,
            "probability": 33,
            "useProbability": False,
            "depth": 2,
            "selectiveLogic": 3,
            "outlet_name": "inner",
            "group": "g1,g2",
            "group_override": True,
            "group_weight": 250,
            "prevent_recursion": True,
            "delay_until_recursion": True,
            "scan_depth": 6,
            "match_whole_words": True,
            "use_group_scoring": True,
            "case_sensitive": True,
            "automation_id": "auto-1",
            "role": 1,
            "vectorized": True,
            "sticky": 4,
            "cooldown": 5,
            "delay": 6,
            "match_persona_description": True,
            "match_character_description": True,
            "match_character_personality": True,
            "match_character_depth_prompt": True,
            "match_scenario": True,
            "match_creator_notes": True,
            "triggers": ["normal", "continue"],
            "ignore_budget": True,
        },
    }
    book = convert_character_book({"name": "All fields", "entries": [source_entry]})
    runtime = book.entries[0]

    # Runtime-side spot checks: the two foreign dialects disagree on defaults.
    assert runtime.selective is True
    assert runtime.disable is True
    assert runtime.position == POSITION_AT_DEPTH
    assert runtime.display_index == 5
    assert runtime.extensions["match_creator_notes"] is True
    assert runtime.extensions["outletName"] == "inner"

    exported = exporters.export_character_book(book)["entries"][0]
    assert field_differences(source_entry, exported, "entry") == []


def test_position_string_and_number_both_directions() -> None:
    """``position`` is a string in V2 and a 0..7 enum at runtime."""
    assert v2_position_to_number("before_char") == 0
    assert v2_position_to_number("after_char") == 1
    assert v2_position_to_number("before_an") == 1  # everything else -> after
    assert v2_position_to_number(None) == 1
    assert v2_position_to_number("4") == POSITION_AT_DEPTH  # numeric escape hatch
    assert v2_position_to_number("after_char", 6) == 6  # extensions.position wins
    assert v2_position_to_number(None, 4) == POSITION_AT_DEPTH

    assert importers.number_to_v2_position(0) == "before_char"
    assert importers.number_to_v2_position(4) == "after_char"

    # A full enum survives because ``extensions.position`` carries it.
    entry = convert_character_book(
        {"entries": [{"keys": ["k"], "position": "after_char", "extensions": {"position": 2}}]}
    ).entries[0]
    assert entry.position == 2
    exported = exporters.export_character_book(WorldBook(entries=[entry]))["entries"][0]
    assert exported["position"] == "after_char"
    assert exported["extensions"]["position"] == 2


# ---------------------------------------------------------------------------
# 2. the embedded character_book
# ---------------------------------------------------------------------------


def test_character_book_string_and_number_fields() -> None:
    """``enabled`` <-> ``disable`` and ``insertion_order`` <-> ``order``."""
    book = convert_character_book(
        {
            "entries": [
                {"id": 0, "keys": ["a"], "content": "x", "enabled": True, "insertion_order": 10},
                {"id": 1, "keys": ["b"], "content": "y", "enabled": False, "insertion_order": 20},
                {"id": 2, "keys": ["c"], "content": "z", "insertion_order": "30"},
            ]
        }
    )
    assert [(entry.disable, entry.insertion_order) for entry in book.entries] == [
        (False, 10),
        (True, 20),
        (False, 30),
    ]
    exported = exporters.export_character_book(book)
    assert [entry["enabled"] for entry in exported["entries"]] == [True, False, True]
    assert [entry["insertion_order"] for entry in exported["entries"]] == [10, 20, 30]


def test_import_character_book_from_card_and_raw_dict() -> None:
    card = import_character_card(
        FIXTURES / "card_v2_with_book.json", filename="card_v2_with_book.json"
    )
    book = import_character_book(card)
    assert isinstance(book, WorldBook)
    assert book.name == "Iris Lore"
    assert len(book) == 2
    assert book.scan_depth == 3
    assert book.token_budget == 512
    assert book.recursive_scanning is True
    # ``original_data`` keeps the untouched object, exactly like ST's originalData.
    assert book.extensions[ORIGINAL_DATA_KEY]["name"] == "Iris Lore"

    # The same conversion works straight off the raw card dict...
    raw_book = import_character_book(load_fixture("card_v2_with_book.json"))
    assert [entry.uid for entry in raw_book.entries] == [0, 3]

    # ...and an empty book is a book, not an error.
    empty = import_character_book(
        {"spec": "chara_card_v2", "data": {"character_book": {"entries": []}}}
    )
    assert len(empty) == 0


def test_embedded_book_and_native_entry_defaults_differ() -> None:
    """Known fact: ``convertCharacterBook`` uses ``selective || false``.

    After the runtime has a book, the "selective is on" default is that of the
    runtime model (``entry_from_dict`` defaults it to true).
    """
    book = convert_character_book({"entries": [{"keys": ["k"], "content": "c"}]})
    assert book.entries[0].selective is False

    bare = entry_from_dict(0, {"key": ["k"], "content": "c"})
    assert bare.selective is True

    # Export materialises the runtime default into the V2 field.
    exported = exporters.export_character_book(book)["entries"][0]
    assert exported["selective"] is False


def test_import_character_book_without_book_raises() -> None:
    card = import_character_card({"name": "Plain", "description": "no book"})
    with pytest.raises(CharacterBookImportError):
        import_character_book(card)
    with pytest.raises(CharacterBookImportError):
        import_character_book({"spec": "chara_card_v2", "data": {"name": "Plain"}})


# ---------------------------------------------------------------------------
# 3. standalone lorebook dialects
# ---------------------------------------------------------------------------


def test_lorebook_v3_fixture() -> None:
    payload = load_fixture("lorebook_v3.json")
    assert detect_lorebook_format(payload) == "lorebook_v3"
    book = import_lorebook(payload, name="lorebook_v3")
    assert book.name == "Harbour Notes"
    assert book.scan_depth == 5
    assert book.token_budget == 900
    assert book.recursive_scanning is False
    assert book.extensions["fixture"] == "lorebook_v3"
    assert [entry.uid for entry in book.entries] == [0, 1]
    assert book.entries[0].keys == ["harbour", "pier"]
    assert book.entries[0].selective is True
    assert book.entries[1].selective is False
    assert book.entries[1].comment == "Gulls"
    # A V3 book re-exports as a V3 file.
    again = exporters.export_lorebook(book)
    assert again["spec"] == "lorebook_v3"
    assert import_lorebook(again).name == "Harbour Notes"


def test_v2_lorebook_fixture() -> None:
    payload = load_fixture("v2_lorebook.json")
    assert detect_lorebook_format(payload) == "v2"
    book = import_lorebook(payload)
    assert book.name == "Worlds Apart"
    assert book.token_budget == 256
    assert book.recursive_scanning is True
    assert [(entry.uid, entry.disable, entry.position) for entry in book.entries] == [
        (0, False, 0),
        (1, True, 3),
    ]
    assert book.entries[0].secondary_keys == ["night"]


def test_agnai_memory_book_fixture() -> None:
    payload = load_fixture("agnai_memory.json")
    assert detect_lorebook_format(payload) == "agnai"
    book = importers.convert_agnai_memory_book(payload)
    assert [entry.uid for entry in book.entries] == [0, 1]
    first, second = book.entries
    assert first.keys == ["meeting", "tuesday"]
    assert first.comment == "First Meeting"
    assert first.content.startswith("They met on a rainy Tuesday")
    assert first.insertion_order == 30  # weight -> order
    assert first.disable is False
    assert first.selective is False  # AgnAI hard-codes it
    assert first.display_index == 0
    assert second.disable is True  # enabled: false
    assert second.insertion_order == 90
    assert import_lorebook(payload).entries[0].comment == "First Meeting"


def test_risu_lorebook_fixture() -> None:
    payload = load_fixture("risu_lorebook.json")
    assert detect_lorebook_format(payload) == "risu"
    book = import_lorebook(payload)
    first, second = book.entries
    # ``entry.key.split(',').map(trim)`` -- spaces are trimmed off.
    assert first.keys == ["dragon", "wyrm", "drake"]
    assert first.secondary_keys == ["ancient", "old"]
    assert first.constant is True  # alwaysActive
    assert first.selective is True
    assert first.insertion_order == 7
    assert first.probability == 25
    assert first.use_probability is True
    assert first.disable is False
    assert first.display_index == 0
    assert second.keys == ["village"]
    assert second.secondary_keys == []
    assert second.probability == 100


def test_bare_entry_map_and_single_entry() -> None:
    """The catch-all: a raw ``{"0": {...}}`` map, a V2 wrapper and a lone entry."""
    raw = {"0": {"key": ["a"], "content": "one", "order": 5}, "1": {"key": ["b"], "content": "two"}}
    assert detect_lorebook_format(raw) == "entries"
    book = import_lorebook(raw)
    assert [entry.content for entry in book.entries] == ["one", "two"]
    assert book.entries[0].insertion_order == 5

    # A bare ``entries`` wrapper with no book level fields at all.
    wrapped = import_lorebook({"entries": {"0": {"keys": ["w"], "content": "wrapped"}}})
    assert wrapped.entries[0].keys == ["w"]

    single = import_lorebook({"key": ["solo"], "content": "only"})
    assert len(single) == 1
    assert single.entries[0].content == "only"

    # V2 snake_case wins over the native spelling when both are present.
    mixed = import_lorebook(
        {"keys": ["v2"], "key": ["native"], "content": "mixed", "insertion_order": 3}
    )
    assert mixed.entries[0].keys == ["v2"]
    assert mixed.entries[0].insertion_order == 3


def test_export_lorebook_v2_shape_for_a_bare_book() -> None:
    book = WorldBook(name="Bare", entries=[entry_from_dict(0, {"key": ["k"], "content": "c"})])
    exported = exporters.export_lorebook(book)
    assert "spec" not in exported
    assert exported["name"] == "Bare"
    assert exported["entries"][0]["keys"] == ["k"]
    assert import_lorebook(exported).entries[0].content == "c"


def test_unknown_lorebook_format_raises_readable_error() -> None:
    with pytest.raises(LorebookImportError) as excinfo:
        import_lorebook({"something": 1, "else": "x"})
    assert "无法识别" in str(excinfo.value)
    with pytest.raises(LorebookImportError):
        import_lorebook({"entries": []})  # empty and shapeless
    with pytest.raises(LorebookImportError):
        import_lorebook([])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 4. chat JSONL tolerance
# ---------------------------------------------------------------------------


def test_import_chat_jsonl_round_trip_keeps_swipes() -> None:
    text = (
        json.dumps({"user_name": "You", "character_name": "Iris", "chat_metadata": {"a": 1}})
        + "\n"
        + json.dumps(
            {
                "name": "Iris",
                "is_user": False,
                "is_system": False,
                "send_date": "2026-10-07T00:00:00Z",
                "mes": "You are late.",
                "swipes": ["You are late.", "You are early."],
                "swipe_id": 1,
                "swipe_info": [{"send_date": "x", "gen_started": "", "gen_finished": ""}],
                "extra": {"api": "test"},
                "unknown_field": {"deep": [1, 2]},
            }
        )
        + "\n"
        + json.dumps({"name": "You", "is_user": True, "mes": "Sorry.", "extra": {}})
        + "\n"
    )
    rows = import_chat_jsonl(text)
    assert len(rows) == 3
    header, first, second = rows
    assert header["character_name"] == "Iris"
    assert header["chat_metadata"] == {"a": 1}
    assert first["swipes"] == ["You are late.", "You are early."]
    assert first["swipe_id"] == 1
    assert first["swipe_info"] == [{"send_date": "x", "gen_started": "", "gen_finished": ""}]
    assert first["unknown_field"] == {"deep": [1, 2]}
    assert second["is_user"] is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "\n",
        "\ufeff",
        "\n\n\n",
    ],
)
def test_import_chat_jsonl_empty_is_empty_list(text: str) -> None:
    assert import_chat_jsonl(text) == []


def test_import_chat_jsonl_header_only() -> None:
    rows = import_chat_jsonl(json.dumps({"user_name": "U", "character_name": "C"}))
    assert len(rows) == 1
    assert rows[0]["user_name"] == "U"
    assert rows[0]["chat_metadata"] == {}


def test_import_chat_jsonl_crlf_bom_and_missing_final_newline() -> None:
    header = json.dumps({"user_name": "U", "character_name": "C"})
    message = json.dumps({"name": "C", "is_user": False, "mes": "no trailing newline"})
    text = "\ufeff" + header + "\r\n" + message  # no trailing newline, CRLF, BOM
    rows = import_chat_jsonl(text)
    assert len(rows) == 2
    assert rows[1]["mes"] == "no trailing newline"

    # Plain CR (classic Mac) and stray blank lines are tolerated as well.
    rows = import_chat_jsonl("\r".join([header, "", message, "\r\n", ""]))
    assert len(rows) == 2


def test_import_chat_jsonl_skips_broken_lines_and_keeps_extras() -> None:
    header = json.dumps({"user_name": "U", "character_name": "C"})
    good = json.dumps({"name": "C", "mes": "kept", "is_user": False, "extra": {"x": 1}})
    text = "\n".join([header, "{not json", "42", json.dumps({"foo": "bar"}), good])
    rows = import_chat_jsonl(text)
    # The unparsable line and the bare number are dropped; the unknown *object*
    # is kept verbatim (dropping unknown fields would lose data).
    assert rows[-1]["mes"] == "kept"
    assert {"foo": "bar"} in rows[1:]
    assert len(rows) == 3

    # A stray second header is folded in instead of becoming a message.
    rows = import_chat_jsonl(
        "\n".join([header, json.dumps({"user_name": "U2", "note_prompt": "hi"}), good])
    )
    assert len(rows) == 2
    assert rows[0]["note_prompt"] == "hi"
    assert rows[0]["user_name"] == "U"  # never overwritten by the stray header


def test_import_chat_jsonl_normalises_header_for_chat_store() -> None:
    """The header must already carry the three keys ``chat_store`` reads."""
    rows = import_chat_jsonl(json.dumps({"note_prompt": "p", "note_interval": 3}))
    assert rows[0]["user_name"] == ""
    assert rows[0]["character_name"] == ""
    # Inline note fields are collected into ``chat_metadata`` (where SillyTavern
    # keeps them) rather than left at the top level of the header.
    assert rows[0]["chat_metadata"] == {"note_prompt": "p", "note_interval": 3}

    # ``metadata`` (the older spelling) is moved into ``chat_metadata``.
    rows = import_chat_jsonl(json.dumps({"metadata": {"integrity": "abc"}}))
    assert rows[0]["chat_metadata"] == {"integrity": "abc"}

    # ``name`` on a header line becomes the character name.
    rows = import_chat_jsonl(json.dumps({"name": "Iris", "chat_metadata": {}}))
    assert rows[0]["character_name"] == "Iris"


def test_import_chat_jsonl_feeds_chat_store() -> None:
    from tavern.st.chat_store import ChatSession

    header = json.dumps({"user_name": "U", "character_name": "C"})
    message = json.dumps({"name": "C", "is_user": False, "mes": "hello"})
    rows = import_chat_jsonl(header + "\n" + message + "\n")
    session = ChatSession.from_jsonl("\n".join(json.dumps(row) for row in rows) + "\n", name="c")
    assert session.character_name == "C"
    assert [msg.mes for msg in session.messages] == ["hello"]


# ---------------------------------------------------------------------------
# 5. cards: readable errors, PNG, validation semantics
# ---------------------------------------------------------------------------


def test_import_character_card_from_dict_text_and_path() -> None:
    from_dict = import_character_card(load_fixture("card_v2_with_book.json"), "card.json")
    assert from_dict.name == "Iris"
    assert from_dict.spec == "chara_card_v2"
    assert from_dict.character_version == "1.2"

    text = json.dumps(load_fixture("card_v2_with_book.json"), ensure_ascii=False)
    assert import_character_card(text, "card.json").name == "Iris"
    assert import_character_card(FIXTURES / "card_v2_with_book.json").name == "Iris"

    # A V1 flat card is up-converted, not rejected.
    v1 = dict.fromkeys(
        ["name", "description", "personality", "scenario", "first_mes", "mes_example"], ""
    )
    v1["name"] = "Old Keeper"
    card = import_character_card(v1)
    assert card.spec == "chara_card_v1"
    assert card.name == "Old Keeper"


def test_import_character_card_errors_are_readable() -> None:
    with pytest.raises(CharacterCardImportError) as excinfo:
        import_character_card({"totally": "unrelated"}, "weird.json")
    message = str(excinfo.value)
    assert "weird.json" in message
    assert "name" in message

    with pytest.raises(CharacterCardImportError) as excinfo:
        import_character_card('{"spec": "chara_card_v2", "spec_version": "2.0"}', "c.json")
    assert "data" in str(excinfo.value)

    with pytest.raises(CharacterCardImportError):
        import_character_card("not json at all", "c.json")
    with pytest.raises(CharacterCardImportError):
        import_character_card("", "empty.json")
    with pytest.raises(CharacterCardImportError):
        import_character_card(b"\xff\xfe\x00\x01binary", "binary.bin")
    with pytest.raises(CharacterCardImportError):
        import_character_card(12345)  # type: ignore[arg-type]

    # None of those may leak a bare KeyError / TypeError.
    for payload in ({"data": {"spec": "chara_card_v2"}}, {"spec": "chara_card_v2"}, []):
        with pytest.raises(CharacterCardImportError):
            import_character_card(payload, "x.json")  # type: ignore[arg-type]

    # YAML without PyYAML is reported as a missing optional dependency.
    try:
        import yaml  # noqa: F401
    except ImportError:  # pragma: no cover - PyYAML is installed in this env
        with pytest.raises(importers.OptionalDependencyError):
            import_character_card("name: X\n", "x.yaml")


def test_import_character_card_from_png_prefers_ccv3() -> None:
    import base64
    import struct
    import zlib

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + chunk_type
            + data
            + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
        )

    def card_chunk(keyword: str, payload: dict[str, Any]) -> bytes:
        encoded = base64.b64encode(json.dumps(payload).encode("utf-8"))
        return chunk(b"tEXt", keyword.encode("ascii") + b"\x00" + encoded)

    v2 = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {"name": "Old"}}
    v3 = {"spec": "chara_card_v3", "spec_version": "3.0", "data": {"name": "New"}}
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    blob = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + card_chunk("chara", v2)
        + card_chunk("ccv3", v3)
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )
    card = import_character_card(blob, "both.png")
    assert card.name == "New"
    assert card.spec == "chara_card_v3"

    # Re-exporting as PNG keeps the card readable by cards.card_from_png.
    exported = exporters.export_character_card(card, as_png=True)
    assert isinstance(exported, bytes)
    assert card_from_png(exported).name == "New"

    # A PNG with no card metadata is a readable error, not a crash.
    bare = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )
    with pytest.raises(CharacterCardImportError) as excinfo:
        import_character_card(bare, "bare.png")
    assert "ccv3" in str(excinfo.value)


def test_export_character_card_keeps_book_and_v3_fields() -> None:
    card = import_character_card(FIXTURES / "card_v2_with_book.json")
    payload = exporters.export_character_card(card)
    assert payload["spec"] == "chara_card_v2"
    assert payload["spec_version"] == "2.0"
    assert payload["data"]["name"] == "Iris"
    assert payload["data"]["character_book"]["name"] == "Iris Lore"
    assert len(payload["data"]["character_book"]["entries"]) == 2

    # Round trip through export -> import -> book.
    again = import_character_card(payload, "again.json")
    assert again.name == "Iris"
    assert len(import_character_book(again)) == 2

    v3 = import_character_card(
        {
            "spec": "chara_card_v3",
            "spec_version": "3.0",
            "data": {
                "name": "Vesper",
                "nickname": "Ves",
                "assets": [{"type": "icon", "uri": "ccdefault:", "name": "main", "ext": "png"}],
                "group_only_greetings": ["Group opening."],
            },
        }
    )
    exported = exporters.export_character_card(v3)
    assert exported["spec"] == "chara_card_v3"
    assert exported["data"]["nickname"] == "Ves"
    assert exported["data"]["group_only_greetings"] == ["Group opening."]
    assert exported["data"]["assets"][0]["type"] == "icon"


def test_card_validation_issues_report_missing_fields() -> None:
    spec, problems = card_validation_issues(
        {"spec": "chara_card_v2", "spec_version": "2.0", "data": {"name": "X"}}
    )
    assert spec == 2
    assert "data.description" in problems
    assert "data.extensions" in problems

    spec, problems = card_validation_issues({"name": "X", "description": "d"})
    assert spec == 0
    assert problems == ["personality", "scenario", "first_mes", "mes_example"]


# ---------------------------------------------------------------------------
# 6. describe_import
# ---------------------------------------------------------------------------


def test_describe_import_card_with_embedded_book() -> None:
    card = import_character_card(FIXTURES / "card_v2_with_book.json")
    summary = describe_import(card)
    assert summary.startswith("这是 V2 卡「Iris」")
    assert "含 1 本内嵌世界书（2 条）" in summary
    assert "标签：mystery、slow-burn" in summary
    assert "2 个备用开场白" in summary


def test_describe_import_card_without_book() -> None:
    summary = describe_import(import_character_card({"name": "Plain"}))
    assert "V1" in summary
    assert "Plain" in summary
    assert "内嵌世界书" not in summary


def test_describe_import_lorebook_and_chat() -> None:
    book = import_lorebook(load_fixture("v2_lorebook.json"))
    summary = describe_import(book)
    assert summary.startswith("这是世界书「Worlds Apart」")
    assert "共 2 条" in summary
    assert "启用 1 条" in summary
    assert "条目示例：Tides、The Bell" in summary

    # A raw dict goes through the same auto detection.
    assert "Worlds Apart" in describe_import(load_fixture("v2_lorebook.json"))
    # An unusable dict gets an explanation instead of an exception.
    assert describe_import({"nonsense": True}).startswith("无法识别的导入内容")

    header = json.dumps({"user_name": "You", "character_name": "Iris"})
    message = json.dumps({"name": "Iris", "mes": "hi", "swipes": ["hi", "hello"]})
    summary = describe_import(import_chat_jsonl(header + "\n" + message))
    assert "You ↔ Iris" in summary
    assert "1 条带 swipe 分支" in summary
    assert describe_import(import_chat_jsonl("")).startswith("这是一份空聊天记录")
