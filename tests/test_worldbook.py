"""Offline tests for the World Info (lorebook) engine."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from astrbot_plugin_tavern.st.worldbook import (  # noqa: E402
    LOGIC_AND_ALL,
    LOGIC_AND_ANY,
    LOGIC_NOT_ANY,
    POSITION_AT_DEPTH,
    POSITION_BEFORE_CHAR,
    ActivationState,
    WorldBook,
    WorldBookSettings,
    activate,
    book_from_dict,
    entry_from_dict,
    greedy_token_count,
    is_regex_key,
    key_matches,
    load_world_book,
    scan_world_books,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def book():
    return load_world_book(FIXTURES / "worldbooks" / "lighthouse.json")


def ids(result) -> set[int]:
    return {entry.uid for entry in result.activated}


def test_book_parsing(book) -> None:
    assert book.name == "Lighthouse Lore"
    assert book.scan_depth == 4
    assert book.token_budget == 1024
    assert book.recursive_scanning is True
    assert len(book.entries) == 6
    by_uid = {entry.uid: entry for entry in book.entries}
    assert by_uid[0].constant is True
    assert by_uid[1].secondary_keys == ["Sundered"]
    assert by_uid[1].sticky == 2 and by_uid[1].cooldown == 3 and by_uid[1].delay == 0
    assert by_uid[3].match_whole_words is True
    assert by_uid[3].position == POSITION_AT_DEPTH
    assert by_uid[4].disable is True
    assert by_uid[0].display_index == 0


def test_scan_world_books_skips_broken(tmp_path: Path) -> None:
    (tmp_path / "ok.json").write_text('{"name": "ok", "entries": {}}', "utf-8")
    (tmp_path / "bad.json").write_text("{oops", "utf-8")
    books = scan_world_books(tmp_path)
    assert [book.name for book in books] == ["ok"]


def test_constant_entry_always_fires(book) -> None:
    result = activate([book], ["hello there"], settings=WorldBookSettings())
    assert 0 in ids(result)
    assert result.by_position[POSITION_BEFORE_CHAR][0].uid == 0


def test_keyword_and_scan_depth(book) -> None:
    history = [
        "filler",
        "filler",
        "old talk of the Sundered wreck",
        "filler",
        "filler",
    ]
    # ``scan_depth`` on the book overrides the runtime default, and the
    # precedence is entry > book > settings (matching SillyTavern).
    book.scan_depth = 10
    assert 1 in ids(activate([book], history, settings=WorldBookSettings()))

    # scan_depth only looks at the tail: the keyword has scrolled away.
    book.scan_depth = 2
    assert 1 not in ids(activate([book], history, settings=WorldBookSettings()))

    # and the per entry value wins over the book value
    entry = next(e for e in book.entries if e.uid == 1)
    entry.scan_depth = 10
    assert 1 in ids(activate([book], history, settings=WorldBookSettings()))
    entry.scan_depth = None
    book.scan_depth = 4


def test_selective_secondary_key(book) -> None:
    settings = WorldBookSettings()
    without = activate([book], ["a shipwreck indeed"], settings=settings)
    assert 1 not in ids(without)

    with_secondary = activate([book], ["the shipwreck of the Sundered"], settings=settings)
    assert 1 in ids(with_secondary)


def test_regex_key_detection_and_match() -> None:
    assert is_regex_key(r"/gull\d+/i") is True
    assert is_regex_key("lighthouse") is False
    assert key_matches("gull12 circled", [r"/gull\d+/i"], False, False) is True
    assert key_matches("seagull", ["gull"], False, True) is False
    assert key_matches("a gull, then", ["gull"], False, True) is True


def test_case_sensitivity() -> None:
    assert key_matches("Lighthouse", ["lighthouse"], False, False) is True
    assert key_matches("Lighthouse", ["lighthouse"], True, False) is False


def test_disabled_and_zero_probability_entries_never_fire(book) -> None:
    result = activate(
        [book],
        ["FOG everywhere, the tide is high"],
        settings=WorldBookSettings(default_scan_depth=8),
    )
    assert 4 not in ids(result)
    assert 5 not in ids(result)


def test_probability_can_suppress_entries(book) -> None:
    settings = WorldBookSettings()
    settings.rng.randint = lambda a, b: 100  # type: ignore[method-assign]
    book.entries[1].probability = 1
    result = activate([book], ["the shipwreck of the Sundered"], settings=settings)
    assert 1 not in ids(result)


def test_selective_logic_modes() -> None:
    def single(logic: int) -> WorldBook:
        entry = entry_from_dict(
            0,
            {
                "key": ["alpha"],
                "keysecondary": ["beta", "gamma"],
                "selective": True,
                "selectiveLogic": logic,
                "content": "hit",
            },
        )
        return WorldBook(name=f"logic{logic}", entries=[entry])

    assert ids(activate([single(LOGIC_AND_ANY)], ["alpha beta"])) == {0}
    assert ids(activate([single(LOGIC_NOT_ANY)], ["alpha delta"])) == {0}
    assert ids(activate([single(LOGIC_NOT_ANY)], ["alpha beta"])) == set()
    assert ids(activate([single(LOGIC_AND_ALL)], ["alpha beta gamma"])) == {0}
    assert ids(activate([single(LOGIC_AND_ALL)], ["alpha beta"])) == set()


def test_min_activations_extension() -> None:
    entry = entry_from_dict(
        0,
        {
            "key": ["alpha"],
            "keysecondary": ["beta", "gamma"],
            "selective": True,
            "selectiveLogic": LOGIC_AND_ANY,
            "content": "hit",
            "extensions": {"min_activations": 2},
        },
    )
    book = WorldBook(name="minact", entries=[entry])
    assert ids(activate([book], ["alpha beta"])) == set()
    assert ids(activate([book], ["alpha beta gamma"])) == {0}


def test_recursion_pulls_in_chained_entries() -> None:
    first = entry_from_dict(
        0, {"key": ["anchor"], "content": "The anchor holds a secret word: kraken."}
    )
    second = entry_from_dict(1, {"key": ["kraken"], "content": "Kraken lore."})
    book = WorldBook(name="chain", entries=[first, second])

    off = activate([book], ["the anchor"], settings=WorldBookSettings(allow_recursion=False))
    assert ids(off) == {0}

    on = activate([book], ["the anchor"], settings=WorldBookSettings(allow_recursion=True))
    assert ids(on) == {0, 1}


def test_prevent_recursion_marks_entry_excluded() -> None:
    first = entry_from_dict(0, {"key": ["anchor"], "content": "secret word kraken"})
    second = entry_from_dict(
        1,
        {
            "key": ["kraken"],
            "content": "Kraken lore.",
            "preventRecursion": True,
            "delayUntilRecursion": True,
        },
    )
    book = WorldBook(name="chain", entries=[first, second])
    result = activate([book], ["the anchor"], settings=WorldBookSettings(allow_recursion=True))
    # delayUntilRecursion + preventRecursion: the chained entry stays out.
    assert ids(result) == {0}


def test_sticky_cooldown_delay_state_machine() -> None:
    entry = entry_from_dict(
        0, {"key": ["bell"], "content": "The bell rang.", "sticky": 2, "cooldown": 3, "delay": 1}
    )
    book = WorldBook(name="state", entries=[entry])
    state = ActivationState()
    settings = WorldBookSettings(default_scan_depth=1)

    assert ids(activate([book], ["bell"], settings=settings, state=state)) == set()  # delay 1
    state.next_turn()
    assert ids(activate([book], ["bell"], settings=settings, state=state)) == {0}  # turn 1 fires
    assert state.is_sticky(book, entry) is True

    # sticky: 2 -> the very next turn holds the entry even without the keyword.
    state.next_turn()
    assert ids(activate([book], ["silence"], settings=settings, state=state)) == {0}
    assert state.is_sticky(book, entry) is True

    # turn 3: sticky window expired, cooldown (3 < 4) blocks the keyword hit.
    state.next_turn()
    assert ids(activate([book], ["bell"], settings=settings, state=state)) == set()
    assert state.is_sticky(book, entry) is False

    # turn 4: cooldown has expired, but nothing matches (no sticky left).
    state.next_turn()
    assert ids(activate([book], ["silence"], settings=settings, state=state)) == set()

    # turn 5: fires again and opens fresh windows.
    state.next_turn()
    assert ids(activate([book], ["bell"], settings=settings, state=state)) == {0}
    assert state.is_sticky(book, entry) is True


def test_insertion_order_and_positions(book) -> None:
    result = activate(
        [book],
        ["the shipwreck of the Sundered", "a keeper of the light"],
        settings=WorldBookSettings(default_scan_depth=8),
    )
    order = [entry.uid for entry in result.activated]
    # order field descending priority: 3 (order=70), 1 (90), 0 (100)
    assert order == [3, 1, 0]
    assert result.by_position[POSITION_AT_DEPTH][0].uid == 3
    assert "Keeper is a title" in result.content_for(POSITION_AT_DEPTH)


def test_token_budget_truncates_lowest_priority() -> None:
    book = WorldBook(
        name="budget",
        entries=[
            entry_from_dict(0, {"key": ["aaa"], "content": "x" * 400, "order": 10}),
            entry_from_dict(1, {"key": ["bbb"], "content": "y" * 400, "order": 99}),
        ],
    )
    budget = greedy_token_count("y" * 400) + 5
    result = activate(
        [book],
        ["aaa bbb"],
        settings=WorldBookSettings(default_scan_depth=2),
        token_budget=budget,
    )
    assert ids(result) == {1}
    assert result.truncated is True

    roomy = activate(
        [book],
        ["aaa bbb"],
        settings=WorldBookSettings(default_scan_depth=2),
        token_budget=greedy_token_count("x" * 400) + greedy_token_count("y" * 400) + 10,
    )
    assert ids(roomy) == {0, 1}
    assert roomy.truncated is False


def test_group_scoring_keeps_heaviest() -> None:
    book = WorldBook(
        name="groups",
        entries=[
            entry_from_dict(
                0, {"key": ["aaa"], "content": "light", "group": "wrecks", "groupWeight": 10}
            ),
            entry_from_dict(
                1, {"key": ["bbb"], "content": "heavy", "group": "wrecks", "groupWeight": 90}
            ),
        ],
    )
    result = activate(
        [book],
        ["aaa bbb"],
        settings=WorldBookSettings(default_scan_depth=2, group_scoring=True),
    )
    assert ids(result) == {1}


def test_book_without_metadata_still_parses() -> None:
    book = book_from_dict({"0": {"key": ["x"], "content": "y"}})
    assert book.name == ""
    assert len(book.entries) == 1
    assert book.entries[0].content == "y"
