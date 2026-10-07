"""Offline tests for the World Info (lorebook) engine."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tavern.st.worldbook import (  # noqa: E402
    LOGIC_AND_ALL,
    LOGIC_AND_ANY,
    LOGIC_NOT_ANY,
    POSITION_AT_DEPTH,
    POSITION_BEFORE_CHAR,
    POSITION_NAMES,
    POSITION_OUTLET,
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


def test_prevent_recursion_only_stops_the_buffer_feed() -> None:
    """``preventRecursion`` keeps an entry out of the recursion buffer, not out of
    the scan: the entry itself still activates once its level is reached
    (world-info.js:5080 vs :4830)."""
    first = entry_from_dict(0, {"key": ["anchor"], "content": "secret word kraken"})
    second = entry_from_dict(
        1,
        {
            "key": ["kraken"],
            "content": "Kraken lore mentioning leviathan.",
            "preventRecursion": True,
        },
    )
    third = entry_from_dict(2, {"key": ["leviathan"], "content": "Leviathan lore."})
    book = WorldBook(name="chain", entries=[first, second, third])
    result = activate([book], ["the anchor"], settings=WorldBookSettings(allow_recursion=True))
    # entry 1 activates by recursion; its own content never reaches the buffer,
    # so entry 2 (which would need it) stays out
    assert ids(result) == {0, 1}


def test_delay_until_recursion_is_a_level() -> None:
    """``delayUntilRecursion`` waits for the Nth recursion pass, and a plain
    ``true`` means level 1 (world-info.js:4754-4759)."""
    seed = entry_from_dict(0, {"key": ["anchor"], "content": "mentions alpha and beta"})
    level1 = entry_from_dict(
        1, {"key": ["alpha"], "content": "level one", "delayUntilRecursion": True}
    )
    level2 = entry_from_dict(2, {"key": ["beta"], "content": "level two", "delayUntilRecursion": 2})
    book = WorldBook(name="levels", entries=[seed, level1, level2])

    # a single recursion pass reaches level 1 only
    one_pass = activate(
        [book],
        ["the anchor"],
        settings=WorldBookSettings(allow_recursion=True, max_recursion_steps=2),
    )
    assert ids(one_pass) == {0, 1}

    # without recursion neither delayed entry may fire
    no_recursion = activate(
        [book], ["the anchor"], settings=WorldBookSettings(allow_recursion=False)
    )
    assert ids(no_recursion) == {0}


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
    # The activation order in the rendered prompt is ascending ``order``
    # because SillyTavern sorts descending and then unshifts
    # (world-info.js:88 + :5214); oracle fixture 06 confirms it. Here the orders
    # are uid3=70, uid1=90, uid0=100, so the prompt reads 3, 1, 0.
    assert order == [3, 1, 0]
    assert [entry.insertion_order for entry in result.activated] == [70, 90, 100]
    assert result.by_position[POSITION_AT_DEPTH][0].uid == 3
    assert "Keeper is a title" in result.content_for(POSITION_AT_DEPTH)


def test_token_budget_is_a_hard_cutoff() -> None:
    """SillyTavern keeps activating until the budget is reached, then drops the rest.

    This is a walk over the activation order with a running total
    (world-info.js:5061-5073), not a best-effort packing: once an entry does not
    fit, every later entry is dropped as well.
    """
    book = WorldBook(
        name="budget",
        entries=[
            entry_from_dict(0, {"key": ["aaa"], "content": "x" * 400, "order": 10}),
            entry_from_dict(1, {"key": ["bbb"], "content": "y" * 400, "order": 99}),
        ],
    )
    one_entry = greedy_token_count("x" * 400)
    budget = one_entry + 5
    result = activate(
        [book],
        ["aaa bbb"],
        settings=WorldBookSettings(default_scan_depth=2),
        token_budget=budget,
    )
    # ascending ``order``: entry 0 fits, entry 1 no longer does
    assert ids(result) == {0}
    assert result.truncated is True

    roomy = activate(
        [book],
        ["aaa bbb"],
        settings=WorldBookSettings(default_scan_depth=2),
        token_budget=greedy_token_count("x" * 400) + greedy_token_count("y" * 400) + 10,
    )
    assert ids(roomy) == {0, 1}
    assert roomy.truncated is False


def test_ignore_budget_entries_survive_the_cutoff() -> None:
    """``ignoreBudget`` entries keep activating after the budget was exceeded."""
    book = WorldBook(
        name="budget",
        entries=[
            entry_from_dict(0, {"key": ["aaa"], "content": "x" * 400, "order": 10}),
            entry_from_dict(1, {"key": ["bbb"], "content": "y" * 400, "order": 50}),
            entry_from_dict(
                2, {"key": ["ccc"], "content": "z" * 400, "order": 90, "ignoreBudget": True}
            ),
        ],
    )
    result = activate(
        [book],
        ["aaa bbb ccc"],
        settings=WorldBookSettings(default_scan_depth=2),
        token_budget=greedy_token_count("x" * 400),
    )
    # entry 0 fills the budget, entry 1 is dropped, entry 2 ignores the budget
    assert ids(result) == {0, 2}


def test_group_scoring_keeps_the_higher_key_score() -> None:
    """Group scoring ranks by matched keys, not by weight (world-info.js:5310)."""
    book = WorldBook(
        name="groups",
        entries=[
            entry_from_dict(
                0, {"key": ["aaa"], "content": "one key", "group": "wrecks", "groupWeight": 10}
            ),
            entry_from_dict(
                1,
                {
                    "key": ["bbb", "ccc"],
                    "content": "two keys",
                    "group": "wrecks",
                    "groupWeight": 90,
                },
            ),
        ],
    )
    result = activate(
        [book],
        ["aaa bbb ccc"],
        settings=WorldBookSettings(default_scan_depth=2, group_scoring=True),
    )
    # entry 1 matches two keys, entry 0 only one -> entry 1 wins the group
    assert ids(result) == {1}
    # without scoring the group keeps the highest order/uid tie-break winner
    plain = activate(
        [book],
        ["aaa bbb ccc"],
        settings=WorldBookSettings(default_scan_depth=2),
    )
    assert len(plain.activated) == 1


def test_group_override_wins_without_rolling() -> None:
    """``groupOverride`` is a deterministic priority tier (world-info.js:5444)."""
    book = WorldBook(
        name="groups",
        entries=[
            entry_from_dict(
                0,
                {"key": ["aaa"], "content": "loser", "group": "g", "groupWeight": 900},
            ),
            entry_from_dict(
                1,
                {
                    "key": ["bbb"],
                    "content": "priority",
                    "group": "g",
                    "groupWeight": 10,
                    "groupOverride": True,
                },
            ),
        ],
    )
    result = activate(
        [book],
        ["aaa bbb"],
        settings=WorldBookSettings(default_scan_depth=2),
    )
    assert ids(result) == {1}


def test_book_without_metadata_still_parses() -> None:
    book = book_from_dict({"0": {"key": ["x"], "content": "y"}})
    assert book.name == ""
    assert len(book.entries) == 1
    assert book.entries[0].content == "y"


# ----------------------------------------------------------------------
# SillyTavern conformance details verified against world-info.js
# ----------------------------------------------------------------------


def test_selective_defaults_to_true() -> None:
    """ST's newWorldInfoEntryDefinition defaults ``selective`` to true."""
    entry = entry_from_dict(0, {"key": ["alpha"], "content": "x"})
    assert entry.selective is True
    assert entry_from_dict(1, {"key": ["a"], "selective": False}).selective is False


def test_match_whole_words_uses_the_global_default() -> None:
    entry = entry_from_dict(0, {"key": ["alpha"], "content": "x"})
    assert entry.match_whole_words is None  # unset on the entry

    # the plugin default keeps substring matching (Chinese friendly)
    loose = activate([WorldBook(name="w", entries=[entry])], ["alphabet"])
    assert ids(loose) == {0}

    # ST's own default is whole words, which refuses the inner hit
    strict = activate(
        [WorldBook(name="w", entries=[entry])],
        ["alphabet"],
        settings=WorldBookSettings(match_whole_words=True),
    )
    assert ids(strict) == set()


def test_scan_depth_zero_does_not_scan_the_chat() -> None:
    """Depth 0 = only recursed entries and Author's Note are evaluated."""
    entry = entry_from_dict(0, {"key": ["bell"], "content": "ring"})
    book = WorldBook(name="w", entries=[entry])
    assert ids(activate([book], ["a bell rang"])) == {0}

    # a per entry scan_depth of 0 disables the chat scan for that entry
    entry.scan_depth = 0
    assert ids(activate([book], ["a bell rang"])) == set()


def test_outlet_position_is_defined() -> None:
    assert POSITION_OUTLET == 7
    assert POSITION_NAMES[POSITION_OUTLET] == "outlet"
