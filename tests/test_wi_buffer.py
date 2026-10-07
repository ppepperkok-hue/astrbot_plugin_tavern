"""Offline tests for the mirrored ``WorldInfoBuffer`` port.

These tests are pure Python: no Node, no SillyTavern snapshot, no file I/O. They
pin the behaviours that are easy to "clean up" during a later refactor and would
then silently diverge from ``world-info.js:199-474``:

* the haystack starts with ``\\x01`` and segments are joined with ``'\\n\\x01'``,
* ``^``-anchored regex keys therefore never match (engine behaviour, on purpose),
* the depth slice, ``#skew`` / ``#startDepth`` arithmetic and the ``get()``
  branch order,
* the recursion and inject buffers being independent slots,
* ``MIN_ACTIVATIONS`` scans dropping the recursion buffer.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tavern.st.wi_buffer import (  # noqa: E402
    JOINER,
    MATCHER,
    MAX_SCAN_DEPTH,
    SCAN_STATE_INITIAL,
    SCAN_STATE_MIN_ACTIVATIONS,
    WORLD_INFO_LOGIC_AND_ALL,
    WORLD_INFO_LOGIC_AND_ANY,
    WorldInfoBuffer,
    WorldInfoBufferConfig,
    build_scan_data,
    default_global_scan_data,
    parse_regex_from_string,
)

#: Depth 0 is the newest message (``messages`` is the chat, newest first).
CHAT = ["newest line", "second line", "third line", "fourth line", "oldest line"]


def entry(**overrides):
    """A SillyTavern ``WIScanEntry`` using the engine's field names."""
    base = {
        "uid": 0,
        "world": "Test",
        "key": [],
        "keysecondary": [],
        "scanDepth": None,
        "caseSensitive": None,
        "matchWholeWords": None,
        "selectiveLogic": WORLD_INFO_LOGIC_AND_ANY,
    }
    base.update(overrides)
    return base


def buffer(**kwargs):
    kwargs.setdefault("messages", CHAT)
    kwargs.setdefault("config", WorldInfoBufferConfig(default_scan_depth=2))
    return WorldInfoBuffer(**kwargs)


# --- 1. set/get round trip, \\x01 framing -------------------------------------


def test_haystack_always_starts_with_matcher() -> None:
    got = buffer().get(entry(scanDepth=2))
    assert got.startswith(MATCHER)


def test_segments_are_joined_with_newline_then_matcher() -> None:
    got = buffer().get(entry(scanDepth=2))
    # depth 0 and 1 of the default scan window
    assert got == MATCHER + "newest line" + JOINER + "second line"
    assert JOINER == "\n\x01"
    assert got.split(JOINER) == ["\x01newest line", "second line"]


def test_depth_slice_is_half_open_from_start_depth() -> None:
    buf = buffer()
    assert buf.get(entry(scanDepth=1)) == MATCHER + "newest line"
    assert (
        buf.get(entry(scanDepth=3))
        == MATCHER + "newest line" + JOINER + "second line" + JOINER + "third line"
    )
    # depth == 0 means "scan nothing", not "scan everything"
    assert buf.get(entry(scanDepth=0)) == ""


def test_messages_are_trimmed_and_empty_slots_do_not_shift_depths() -> None:
    buf = WorldInfoBuffer(["  padded  ", "", "third"])
    assert buf.get(entry(scanDepth=3)) == MATCHER + "padded" + JOINER + "" + JOINER + "third"


def test_scan_depth_none_falls_back_to_config_depth_plus_skew() -> None:
    buf = buffer(config=WorldInfoBufferConfig(default_scan_depth=2))
    assert buf.get_depth() == 2
    first = buf.get(entry())
    assert first == MATCHER + "newest line" + JOINER + "second line"
    buf.advance_scan()
    assert buf.get_depth() == 3
    assert buf.get(entry()).endswith(JOINER + "third line")


def test_start_depth_is_a_floor_that_excludes_earlier_depths() -> None:
    config = WorldInfoBufferConfig(default_scan_depth=5, start_depth=2)
    buf = WorldInfoBuffer(CHAT, config=config)
    assert buf.get(entry(scanDepth=2)) == ""
    assert buf.get(entry(scanDepth=3)) == MATCHER + "third line"


def test_negative_depth_returns_empty() -> None:
    assert buffer().get(entry(scanDepth=-1)) == ""


def test_depth_beyond_max_scan_depth_is_truncated_not_rejected() -> None:
    buf = buffer()
    got = buf.get(entry(scanDepth=MAX_SCAN_DEPTH + 500))
    assert got.startswith(MATCHER)
    assert "oldest line" in got
    assert len(buf._depth_buffer) == len(CHAT)


# --- 3. unset / pop / has: the sibling members of the JS class ----------------


def test_has_recurse_reacts_only_to_the_recurse_slot() -> None:
    buf = buffer()
    assert buf.has_recurse() is False
    buf.add_inject("injected through a prompt extension")
    assert buf.has_recurse() is False
    buf.add_recurse("recursed content")
    assert buf.has_recurse() is True


def test_recurse_buffer_round_trips_through_get() -> None:
    buf = buffer()
    buf.add_recurse("alpha")
    buf.add_recurse("beta")
    got = buf.get(entry(scanDepth=1))
    assert got.endswith(JOINER + "alpha" + JOINER + "beta")


def test_advance_scan_only_moves_skew() -> None:
    buf = buffer()
    assert buf.get(entry(scanDepth=2)).count(MATCHER) == 2
    buf.advance_scan()
    assert buf.get_depth() == 3
    assert buf.get(entry(scanDepth=2)).count(MATCHER) == 2


# --- 4. recurse and inject are independent slots ------------------------------


def test_inject_and_recurse_buffers_are_separate_slots() -> None:
    buf = buffer(config=WorldInfoBufferConfig(default_scan_depth=1))
    buf.add_inject("INJECTED")
    buf.add_recurse("RECURSED")
    got = buf.get(entry())
    assert got == MATCHER + "newest line" + JOINER + "INJECTED" + JOINER + "RECURSED"


def test_min_activations_scan_drops_the_recursion_buffer_only() -> None:
    buf = buffer(config=WorldInfoBufferConfig(default_scan_depth=1))
    buf.add_inject("INJECTED")
    buf.add_recurse("RECURSED")

    normal = buf.get(entry(), SCAN_STATE_INITIAL)
    min_activations = buf.get(entry(), SCAN_STATE_MIN_ACTIVATIONS)

    assert "RECURSED" in normal and "INJECTED" in normal
    assert "RECURSED" not in min_activations
    assert "INJECTED" in min_activations


def test_injections_are_appended_after_the_global_scan_data() -> None:
    buf = WorldInfoBuffer(
        [],
        {"scenario": "A lighthouse in the fog"},
        config=WorldInfoBufferConfig(default_scan_depth=2),
    )
    buf.add_inject("INJECTED")
    assert (
        buf.get(entry(scanDepth=1, matchScenario=True))
        == MATCHER + JOINER + "A lighthouse in the fog" + JOINER + "INJECTED"
    )


# --- 5. global scan data ------------------------------------------------------


def test_global_scan_data_is_scanned_even_without_chat_history() -> None:
    global_scan_data = {
        "personaDescription": "the user is a cartographer",
        "characterDescription": "the character is a lighthouse keeper",
        "characterPersonality": "terse",
        "characterDepthPrompt": "speaks in log entries",
        "scenario": "A lighthouse in the fog",
        "creatorNotes": "handwritten card",
    }
    buf = WorldInfoBuffer([], global_scan_data, config=WorldInfoBufferConfig(default_scan_depth=2))
    got = buf.get(
        entry(
            scanDepth=1,
            matchPersonaDescription=True,
            matchCharacterDescription=True,
            matchCharacterPersonality=True,
            matchCharacterDepthPrompt=True,
            matchScenario=True,
            matchCreatorNotes=True,
        )
    )
    assert got == MATCHER + JOINER.join(
        [
            "",
            "the user is a cartographer",
            "the character is a lighthouse keeper",
            "terse",
            "speaks in log entries",
            "A lighthouse in the fog",
            "handwritten card",
        ]
    )
    assert buf.match_keys(got, "lighthouse keeper", entry()) is True


def test_global_scan_fields_are_opt_in_per_flag() -> None:
    buf = WorldInfoBuffer(
        [],
        {"scenario": "A lighthouse in the fog", "creatorNotes": "handwritten card"},
        config=WorldInfoBufferConfig(default_scan_depth=2),
    )
    assert (
        buf.get(entry(scanDepth=1, matchScenario=True))
        == MATCHER + JOINER + "A lighthouse in the fog"
    )
    # without the flag the haystack is the bare MATCHER: the global scan data
    # contributes nothing, not even a separator.
    assert buf.get(entry(scanDepth=1)) == MATCHER
    assert "handwritten card" not in buf.get(entry(scanDepth=1, matchScenario=True))


def test_missing_global_scan_keys_become_empty_strings() -> None:
    data = build_scan_data({"scenario": "fog"})
    assert data["scenario"] == "fog"
    assert data["personaDescription"] == ""
    assert data["trigger"] == "normal"
    assert set(data) == set(default_global_scan_data)


def test_global_scan_data_never_leaks_between_buffers() -> None:
    first = buffer()
    second = buffer()
    first._global_scan_data["scenario"] = "fog"
    assert second._global_scan_data["scenario"] == ""


# --- 6. the leading \\x01 defeats ^-anchored regexes ---------------------------


def test_caret_anchored_regex_never_matches_the_haystack() -> None:
    # world-info.js:295-297 -- this is SillyTavern's real behaviour, not a bug.
    anchored = entry(key=["/^newest/"], scanDepth=2)
    assert (
        buffer().match_keys(buffer().get(anchored, SCAN_STATE_INITIAL), "/^newest/", anchored)
        is False
    )
    # ...while a plain substring key finds the very same text.
    plain = entry(key=["newest"], scanDepth=2)
    haystack = buffer().get(plain, SCAN_STATE_INITIAL)
    assert buffer().match_keys(haystack, "newest", plain) is True


def test_removing_the_leading_matcher_makes_the_anchor_match() -> None:
    """The only thing defeating ``^`` is the leading ``\\x01``, not the pattern."""
    buf = buffer()
    haystack = buf.get(entry(scanDepth=2), SCAN_STATE_INITIAL)
    anchored = parse_regex_from_string("/^newest/")
    assert anchored is not None

    assert anchored.search(haystack) is None
    assert anchored.search(haystack[len(MATCHER) :]) is not None

    # And parse_regex_from_string keeps rejecting plain-text keys outright.
    assert parse_regex_from_string("newest") is None


def test_regex_key_wins_over_whole_word_and_case_settings() -> None:
    buf = buffer()
    haystack = buf.get(entry(scanDepth=2), SCAN_STATE_INITIAL)
    # regex matching is case sensitive regardless of entry.caseSensitive
    assert buf.match_keys(haystack, "/NEWEST/", entry(scanDepth=2, caseSensitive=True)) is False
    assert buf.match_keys(haystack, "/newest/", entry(scanDepth=2, caseSensitive=True)) is True


# --- matchKeys plaintext branches --------------------------------------------


def test_plaintext_matching_is_case_insensitive_by_default() -> None:
    buf = buffer()
    haystack = buf.get(entry(scanDepth=1), SCAN_STATE_INITIAL)
    assert buf.match_keys(haystack, "NEWEST LINE", entry()) is True
    assert buf.match_keys(haystack, "SECOND LINE", entry()) is False


def test_case_sensitive_entry_overrides_the_global_default() -> None:
    buf = buffer(config=WorldInfoBufferConfig(default_scan_depth=1, case_sensitive=True))
    haystack = buf.get(entry(scanDepth=1), SCAN_STATE_INITIAL)
    assert buf.match_keys(haystack, "NEWEST", entry()) is False
    assert buf.match_keys(haystack, "newest", entry()) is True
    # entry.caseSensitive ?? global: an explicit False wins over a True global
    assert buf.match_keys(haystack, "NEWEST", entry(caseSensitive=False)) is True


def test_whole_word_keys_use_custom_boundaries() -> None:
    haystack = MATCHER + "the concatenate incident"
    single = entry(matchWholeWords=True)
    assert buffer().match_keys(haystack, "cat", single) is False
    assert buffer().match_keys(haystack, "concatenate", single) is True
    assert buffer().match_keys(haystack, "incident", single) is True


def test_whole_word_multi_token_keys_fall_back_to_includes() -> None:
    haystack = MATCHER + "the fog is thick tonight"
    multi = entry(matchWholeWords=True)
    assert buffer().match_keys(haystack, "fog is", multi) is True
    assert buffer().match_keys(haystack, "fog is  ", entry(matchWholeWords=False)) is False


def test_whole_word_global_default_comes_from_the_config() -> None:
    buf = buffer(config=WorldInfoBufferConfig(default_scan_depth=1, match_whole_words=True))
    haystack = buf.get(entry(scanDepth=1), SCAN_STATE_INITIAL)
    assert buf.match_keys(haystack, "newest line", entry()) is True
    assert buf.match_keys(haystack, "new", entry()) is False


def test_parse_regex_from_string_rejects_unparsable_keys() -> None:
    assert parse_regex_from_string("plain text") is None
    assert parse_regex_from_string("/a/b/") is None  # unescaped delimiter inside
    assert parse_regex_from_string("/a\\/b/") is not None  # escaped delimiter is fine
    assert parse_regex_from_string("/[a/") is None  # bad syntax
    assert parse_regex_from_string("/fog/i").flags & 2  # re.IGNORECASE


# --- external activations (static map) ---------------------------------------


def test_external_activations_round_trip_and_reset() -> None:
    WorldInfoBuffer.reset_external_effects()
    target = entry(uid=7, world="Test")
    assert buffer().get_externally_activated(target) is None
    WorldInfoBuffer.external_activations["Test.7"] = target
    assert buffer().get_externally_activated(target) is target
    assert buffer().get_externally_activated(entry(uid=8, world="Test")) is None
    WorldInfoBuffer.reset_external_effects()
    assert WorldInfoBuffer.external_activations == {}


# --- getScore -----------------------------------------------------------------


def test_score_counts_primary_keys_only_without_secondary_keys() -> None:
    buf = buffer(config=WorldInfoBufferConfig(default_scan_depth=2))
    scored = entry(key=["newest", "absent"], scanDepth=2)
    assert buf.get_score(scored, SCAN_STATE_INITIAL) == 1


def test_score_returns_zero_when_the_entry_has_no_primary_keys() -> None:
    buf = buffer()
    assert buf.get_score(entry(scanDepth=2), SCAN_STATE_INITIAL) == 0
    assert (
        buf.get_score(entry(key=[], keysecondary=["newest"], scanDepth=2), SCAN_STATE_INITIAL) == 0
    )


def test_score_and_any_adds_secondary_hits() -> None:
    buf = buffer()
    scored = entry(
        key=["newest"],
        keysecondary=["second", "absent"],
        selectiveLogic=WORLD_INFO_LOGIC_AND_ANY,
        scanDepth=2,
    )
    assert buf.get_score(scored, SCAN_STATE_INITIAL) == 2


def test_score_and_all_needs_every_secondary_key() -> None:
    buf = buffer()
    partial = entry(
        key=["newest"],
        keysecondary=["second", "absent"],
        selectiveLogic=WORLD_INFO_LOGIC_AND_ALL,
        scanDepth=2,
    )
    complete = entry(
        key=["newest"],
        keysecondary=["second"],
        selectiveLogic=WORLD_INFO_LOGIC_AND_ALL,
        scanDepth=2,
    )
    assert buf.get_score(partial, SCAN_STATE_INITIAL) == 1
    assert buf.get_score(complete, SCAN_STATE_INITIAL) == 2


def test_score_ignores_negative_logic_like_the_engine() -> None:
    buf = buffer()
    scored = entry(
        key=["newest"],
        keysecondary=["absent"],
        selectiveLogic=2,  # NOT_ANY -- falls through to the primary score
        scanDepth=2,
    )
    assert buf.get_score(scored, SCAN_STATE_INITIAL) == 1
