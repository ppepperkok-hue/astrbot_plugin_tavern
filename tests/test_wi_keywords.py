"""Offline tests for ``tavern/st/wi_keywords.py`` and ``tavern/st/wi_decorators.py``.

No Node and no SillyTavern checkout is needed: the expected values are either
hand-derived from ``research/_raw/st-src/world-info.js`` or taken from the
recorded Node run of the real engine in
``tools/st-oracle/out/10-decorators.json`` (fixture
``tools/st-oracle/fixtures/10-decorators.json``).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tavern.st import wi_keywords  # noqa: E402
from tavern.st.wi_decorators import (  # noqa: E402
    EXTENDED_KNOWN_DECORATORS,
    KNOWN_DECORATORS,
    DecoratorResult,
    apply_decorators,
    parse_decorators,
    strip_decorators,
)
from tavern.st.wi_keywords import (  # noqa: E402
    any_key_matches,
    custom_tokenizer,
    escape_regex,
    is_valid_regex,
    match_keys,
    parse_regex_from_string,
    split_keywords_and_regexes,
    transform_string,
)

# ---------------------------------------------------------------------------
# splitKeywordsAndRegexes / customTokenizer (world-info.js:2797-2878)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("", [], id="empty"),
        pytest.param("   ", [], id="blank"),
        pytest.param("foo", ["foo"], id="single-plain-word"),
        pytest.param("foo, bar", ["foo", "bar"], id="comma-separated"),
        pytest.param("foo, /bar/i, baz", ["foo", "/bar/i", "baz"], id="mixed-with-flags"),
        pytest.param("/foo,bar/i, x", ["/foo,bar/i", "x"], id="comma-inside-a-closed-regex"),
        pytest.param("/a\\/b/, y", ["/a\\/b/", "y"], id="escaped-slash-inside-regex"),
        pytest.param("/a/b", ["/a/b"], id="trailing-token-is-not-validated"),
        pytest.param("/[/,x", ["/[/", "x"], id="invalid-regex-with-comma-is-split"),
        pytest.param("one,two,three", ["one", "two", "three"], id="three-words"),
        pytest.param("a,,b", ["a", ",b"], id="empty-token-then-js-index-quirk"),
        pytest.param(
            "a,/re,gex/i", ["a", "/re", "gex/i"], id="index-zero-of-a-reset-iteration-is-skipped"
        ),
        pytest.param("  padded  ,  key  ", ["padded", "key"], id="tokens-are-trimmed"),
    ],
)
def test_split_keywords_and_regexes(text: str, expected: list[str]) -> None:
    assert split_keywords_and_regexes(text) == expected


def test_custom_tokenizer_returns_the_leftover_term() -> None:
    seen: list[str] = []
    leftover = custom_tokenizer("a, /b/i, c", seen.append)
    assert seen == ["a", "/b/i"]
    assert leftover == " c"


def test_custom_tokenizer_keeps_commas_of_a_half_typed_regex() -> None:
    seen: list[str] = []
    leftover = custom_tokenizer("/open,still", seen.append)
    # No comma is consumed while the delimiter is open, so nothing is tokenized.
    assert seen == []
    assert leftover == "/open,still"


# ---------------------------------------------------------------------------
# parseRegexFromString / isValidRegex (world-info.js:2888-2926)
# ---------------------------------------------------------------------------


def test_parse_regex_without_flags() -> None:
    pattern = parse_regex_from_string("/abc/")
    assert pattern is not None
    assert pattern.pattern == "abc"


def test_parse_regex_with_flags() -> None:
    pattern = parse_regex_from_string("/abc/i")
    assert pattern is not None
    assert pattern.flags & re.IGNORECASE
    assert pattern.search("ABC") is not None


def test_every_js_flag_is_accepted() -> None:
    pattern = parse_regex_from_string("/a.b/gimsuy")
    assert pattern is not None
    assert pattern.flags & re.MULTILINE
    assert pattern.flags & re.DOTALL
    # ``g`` and ``y`` have no Python counterpart and are inert.
    assert pattern.search("A\nB") is not None


def test_escaped_slash_is_unescaped_once() -> None:
    pattern = parse_regex_from_string("/a\\/b\\/c/")
    assert pattern is not None
    # JS ``String.replace`` with a string replaces the first hit only.
    assert pattern.pattern == "a/b\\/c"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("abc", id="not-delimited"),
        pytest.param(" /abc/", id="leading-space"),
        pytest.param("/abc", id="no-closing-slash"),
        pytest.param("abc/", id="no-opening-slash"),
        pytest.param("//", id="empty-pattern"),
        pytest.param("/a/b/", id="unescaped-slash-inside"),
        pytest.param("/x/z", id="unknown-flag-falls-back-to-a-slash"),
        pytest.param("/a/ii", id="duplicate-flags-throw-in-js"),
        pytest.param("/(/", id="unbalanced-group"),
        pytest.param("/[/", id="unterminated-character-class"),
    ],
)
def test_parse_regex_returns_none(text: str) -> None:
    assert parse_regex_from_string(text) is None
    assert is_valid_regex(text) is False


def test_is_valid_regex_wraps_parse_regex() -> None:
    assert is_valid_regex("/re/") is True
    assert is_valid_regex("re") is False


def test_escape_regex_matches_the_engine_helper() -> None:
    assert escape_regex("C++") == "C\\+\\+"
    assert escape_regex("a.b") == "a\\.b"
    assert escape_regex("$^()[]{}|*?+") == "\\$\\^\\(\\)\\[\\]\\{\\}\\|\\*\\?\\+"
    assert escape_regex("") == ""
    assert escape_regex(None) == ""
    # ``/`` is not part of the upstream character class.
    assert escape_regex("/a/") == "/a/"


# ---------------------------------------------------------------------------
# matchKeys: whole-word boundaries (world-info.js:337-366)
# ---------------------------------------------------------------------------

WHOLE_WORDS = {"matchWholeWords": True}


def test_multi_word_key_falls_back_to_plain_includes() -> None:
    # The task's example: a two-token key matches inside a longer phrase.
    assert match_keys("I love New York City", "New York", WHOLE_WORDS) is True
    # ... because this branch does no boundary check at all.
    assert match_keys("aNew Yorkb", "New York", WHOLE_WORDS) is True


def test_single_word_key_uses_custom_boundaries() -> None:
    assert match_keys("I use C++ daily", "C++", WHOLE_WORDS) is True
    assert match_keys("abcC++def", "C++", WHOLE_WORDS) is False


def test_key_ending_in_a_punctuation_run_needs_a_boundary() -> None:
    # ``A-1`` is a single token, so ``(?:^|\W)(A\-1)(?:$|\W)`` applies.
    assert match_keys("model A-1 rocks", "A-1", WHOLE_WORDS) is True
    assert match_keys("A-1", "A-1", WHOLE_WORDS) is True
    assert match_keys("XA-1Y", "A-1", WHOLE_WORDS) is False
    assert match_keys("A-1B", "A-1", WHOLE_WORDS) is False


def test_key_containing_a_dot_needs_a_boundary() -> None:
    assert match_keys("a foo.bar here", "foo.bar", WHOLE_WORDS) is True
    assert match_keys("foo.bar", "foo.bar", WHOLE_WORDS) is True
    assert match_keys("xfoo.barx", "foo.bar", WHOLE_WORDS) is False


def test_word_class_is_ascii_like_the_engine() -> None:
    # JS ``\w`` / ``\W`` are ASCII-only in every mode, so an ideograph is a
    # non-word character and provides the boundary.
    assert match_keys("中文关键词测试", "关键词", WHOLE_WORDS) is True
    assert match_keys("中关键词文", "关键词", WHOLE_WORDS) is True
    assert match_keys("αβγ", "β", WHOLE_WORDS) is True
    assert match_keys("xβy", "β", WHOLE_WORDS) is False
    # ``_`` is a word character, so ``flat`` does not match inside ``flat_earth``.
    assert match_keys("flat_earth", "flat", WHOLE_WORDS) is False
    assert match_keys("flat-earth", "flat", WHOLE_WORDS) is True


def test_whole_words_off_is_a_plain_includes() -> None:
    assert match_keys("abcC++def", "C++", {"matchWholeWords": False}) is True
    assert match_keys("xa-1b", "A-1", {}) is True  # module default is False
    assert match_keys("NEW YORK", "New York", {"matchWholeWords": False}) is True


def test_whole_word_matching_is_case_insensitive_by_default() -> None:
    assert match_keys("i use c++", "C++", WHOLE_WORDS) is True


def test_regex_key_overrides_the_word_settings() -> None:
    assert match_keys("I use C++", "/c\\+\\+/i", WHOLE_WORDS) is True
    assert match_keys("I use c++", "/C\\+\\+/", WHOLE_WORDS) is False
    assert match_keys("I use C++", "/C\\+\\+/", {"matchWholeWords": False}) is True
    assert any_key_matches("hello world", ["nope", "/wor/"], {}) is True
    assert any_key_matches("hello world", ["nope", "/xyz/"], {}) is False


def test_case_sensitivity_follows_the_entry_then_the_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert match_keys("I USE C++", "c++", {}) is True
    assert match_keys("I USE C++", "c++", {"caseSensitive": True}) is False
    assert match_keys("I USE C++", "c++", {"case_sensitive": True}) is False

    monkeypatch.setattr(wi_keywords, "world_info_case_sensitive", True)
    assert match_keys("I USE C++", "c++", {}) is False
    # JS ``??``: an explicit false on the entry beats a true global.
    assert match_keys("I USE C++", "c++", {"caseSensitive": False}) is True


def test_global_whole_word_default_and_entry_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wi_keywords, "world_info_match_whole_words", True)
    assert match_keys("xa-1b3", "A-1", {}) is False
    assert match_keys("a A-1 b", "A-1", {}) is True
    # The same ``??`` rule: an explicit entry value wins over the global.
    assert match_keys("xa-1b3", "A-1", {"matchWholeWords": False}) is True
    assert match_keys("xa-1b3", "A-1", {"match_whole_words": False}) is True


def test_entry_can_be_any_object_with_attributes() -> None:
    entry = SimpleNamespace(case_sensitive=True, match_whole_words=True)
    assert match_keys("A-1", "A-1", entry) is True
    assert match_keys("a-1", "A-1", entry) is False


def test_keyword_only_overrides_skip_the_globals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wi_keywords, "world_info_match_whole_words", True)
    assert match_keys("xa-1b", "A-1", {}, match_whole_words=False) is True
    monkeypatch.setattr(wi_keywords, "world_info_case_sensitive", True)
    assert match_keys("ABC", "abc", {}, case_sensitive=False) is True


def test_transform_string_lowercases_unless_case_sensitive() -> None:
    assert transform_string("AbC") == "abc"
    assert transform_string("AbC", {"caseSensitive": True}) == "AbC"
    assert transform_string("AbC", {}, case_sensitive=True) == "AbC"
    assert transform_string("AbC", {"case_sensitive": True}) == "AbC"


# ---------------------------------------------------------------------------
# parseDecorators (world-info.js:4652-4698)
# ---------------------------------------------------------------------------

#: Recorded JS oracle (tools/st-oracle/out/10-decorators.json): the content is
#: the first non-``@@`` line onwards, ``@@@`` alone is skipped, and the unknown
#: decorator is dropped from the content while not being collected.
JS_ORACLE_RESULTS = [
    pytest.param(
        "@@activate\nThe forced line appears without its decorator.",
        ["@@activate"],
        "The forced line appears without its decorator.",
        id="activate",
    ),
    pytest.param(
        "@@dont_activate\nThis suppressed line must never appear.",
        ["@@dont_activate"],
        "This suppressed line must never appear.",
        id="dont-activate",
    ),
    pytest.param(
        "@@unknown_decorator\nContent keeps the unknown decorator line.",
        [],
        "Content keeps the unknown decorator line.",
        id="unknown-decorator-falls-back",
    ),
    pytest.param(
        "@@@\nTriple at line stays inside the content.",
        [],
        "Triple at line stays inside the content.",
        id="bare-triple-at",
    ),
]


@pytest.mark.parametrize(("content", "decorators", "stripped"), JS_ORACLE_RESULTS)
def test_parse_decorators_matches_the_node_oracle(
    content: str, decorators: list[str], stripped: str
) -> None:
    result = parse_decorators(content)
    assert result == DecoratorResult(decorators=decorators, content=stripped)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("plain text", id="no-at-signs"),
        pytest.param("@activate\nbody", id="single-at"),
        pytest.param(" @@activate\nbody", id="leading-space"),
        pytest.param("text\n@@activate", id="decorator-not-first"),
    ],
)
def test_content_without_a_decorator_block_is_returned_unchanged(content: str) -> None:
    assert parse_decorators(content) == DecoratorResult(decorators=[], content=content)


def test_a_triple_at_is_skipped_while_not_fallbacked() -> None:
    result = parse_decorators("@@@activate\nbody")
    assert result.decorators == []
    assert result.content == "body"


def test_a_triple_at_is_downgraded_after_a_fallback() -> None:
    result = parse_decorators("@@nope\n@@@activate\nbody")
    assert result.decorators == ["@@activate"]
    assert result.content == "body"


def test_an_unknown_decorator_does_not_stop_the_parse() -> None:
    result = parse_decorators("@@nope\n@@activate\nbody")
    assert result.decorators == ["@@activate"]
    assert result.content == "body"


def test_only_the_next_known_decorator_resumes_the_block() -> None:
    result = parse_decorators("@@activate\n@@nope\n@@another_unknown\n@@dont_activate\nbody")
    assert result.decorators == ["@@activate", "@@dont_activate"]
    assert result.content == "body"


def test_the_known_set_is_prefix_matched() -> None:
    # ``@@activate_only_after`` counts as known because of the ``@@activate``
    # prefix (world-info.js:4664) and is stored verbatim.
    result = parse_decorators("@@activate_only_after=2\nbody")
    assert result.decorators == ["@@activate_only_after=2"]
    assert result.content == "body"


def test_a_block_of_only_decorators_leaves_the_content_alone() -> None:
    # The rewrite only happens at the first non-decorator line, so a content that
    # is nothing but decorators keeps its text (upstream quirk).
    assert parse_decorators("@@activate") == DecoratorResult(
        decorators=["@@activate"], content="@@activate"
    )
    assert parse_decorators("@@activate\n@@dont_activate") == DecoratorResult(
        decorators=["@@activate", "@@dont_activate"],
        content="@@activate\n@@dont_activate",
    )


def test_a_blank_line_ends_the_decorator_block() -> None:
    result = parse_decorators("@@activate\n\nbody")
    assert result.decorators == ["@@activate"]
    assert result.content == "\nbody"


def test_strip_decorators() -> None:
    assert strip_decorators("@@activate\nx") == "x"
    assert strip_decorators("plain") == "plain"
    # An unknown decorator line is consumed by the same rewrite.
    assert strip_decorators("@@nope\nbody") == "body"
    assert strip_decorators("@@depth=4\nbody") == "body"


def test_known_decorator_sets() -> None:
    assert KNOWN_DECORATORS == ("@@activate", "@@dont_activate")
    assert set(EXTENDED_KNOWN_DECORATORS) >= set(KNOWN_DECORATORS)
    # Only the project extension set knows about ``@@depth``.
    assert parse_decorators("@@depth=4\nbody") == DecoratorResult(decorators=[], content="body")


# ---------------------------------------------------------------------------
# apply_decorators
# ---------------------------------------------------------------------------


def test_apply_decorators_overwrites_the_entry_fields() -> None:
    entry = {"depth": 4, "position": 0, "role": None, "scan_depth": None, "constant": False}
    new_entry, content = apply_decorators(
        entry,
        "@@depth=8\n@@position=atDepth\n@@role=assistant\n@@scan_depth=2\nbody",
    )
    assert new_entry["depth"] == 8
    assert new_entry["position"] == 4
    assert new_entry["role"] == "assistant"
    assert new_entry["scan_depth"] == 2
    assert new_entry["decorators"] == [
        "@@depth=8",
        "@@position=atDepth",
        "@@role=assistant",
        "@@scan_depth=2",
    ]
    assert content == "body"
    assert entry == {"depth": 4, "position": 0, "role": None, "scan_depth": None, "constant": False}


@pytest.mark.parametrize(
    ("decorator", "field", "expected"),
    [
        pytest.param("@@depth=0", "depth", 0, id="depth-zero"),
        pytest.param("@@depth=20", "depth", 20, id="depth-number"),
        pytest.param("@@position=1", "position", 1, id="position-number"),
        pytest.param("@@position=ANTop", "position", 2, id="position-name"),
        pytest.param("@@position=embottom", "position", 6, id="position-lowercase-name"),
        pytest.param("@@position=outlet", "position", 7, id="position-outlet"),
        pytest.param("@@role=0", "role", "system", id="role-number"),
        pytest.param("@@role=USER", "role", "user", id="role-name"),
        pytest.param("@@role=2", "role", "assistant", id="role-number-2"),
        pytest.param("@@scan_depth=0", "scan_depth", 0, id="scan-depth-zero"),
        pytest.param(
            "@@activate_only_after=3", "delay_until_recursion", 3, id="activate-only-after"
        ),
    ],
)
def test_apply_decorators_parses_names_and_numbers(decorator: str, field: str, expected) -> None:
    new_entry, content = apply_decorators({}, f"{decorator}\nbody")
    assert new_entry[field] == expected
    assert content == "body"


@pytest.mark.parametrize(
    "decorator",
    [
        pytest.param("@@depth=abc", id="depth-not-a-number"),
        pytest.param("@@depth=-1", id="depth-negative"),
        pytest.param("@@depth=", id="depth-empty"),
        pytest.param("@@depth", id="depth-without-argument"),
        pytest.param("@@position=9", id="position-out-of-range"),
        pytest.param("@@position=bogus", id="position-unknown-name"),
        pytest.param("@@position=", id="position-empty"),
        pytest.param("@@role=zzz", id="role-unknown-name"),
        pytest.param("@@role=3", id="role-out-of-range"),
        pytest.param("@@scan_depth=abc", id="scan-depth-not-a-number"),
        pytest.param("@@scan_depth=-2", id="scan-depth-negative"),
        pytest.param("@@activate_only_after=", id="activate-only-after-empty"),
        pytest.param("@@activate_only_after=-1", id="activate-only-after-negative"),
        pytest.param("@@depthfoo=1", id="known-by-prefix-but-unknown-name"),
    ],
)
def test_apply_decorators_ignores_invalid_arguments(decorator: str) -> None:
    entry = {
        "depth": 7,
        "position": 1,
        "role": "user",
        "scan_depth": 5,
        "constant": False,
        "disable": False,
        "delay_until_recursion": False,
    }
    new_entry, content = apply_decorators(entry, f"{decorator}\nbody")
    assert new_entry == {**entry, "decorators": [decorator]}
    assert content == "body"


def test_a_totally_unknown_decorator_is_neither_collected_nor_applied() -> None:
    new_entry, content = apply_decorators({"depth": 7}, "@@totally_unknown=1\nbody")
    assert new_entry == {"depth": 7, "decorators": []}
    assert content == "body"


def test_apply_decorators_maps_the_two_real_decorators() -> None:
    new_entry, content = apply_decorators({}, "@@activate\n@@dont_activate\nbody")
    assert new_entry["constant"] is True
    assert new_entry["disable"] is True
    assert new_entry["decorators"] == ["@@activate", "@@dont_activate"]
    assert content == "body"


def test_the_st_decorators_are_whole_string_matches() -> None:
    # The engine checks ``decorators.includes('@@activate')``, so an argument
    # makes it a different string with no effect.
    new_entry, _ = apply_decorators({}, "@@activate=1\nbody")
    assert "constant" not in new_entry
    new_entry, _ = apply_decorators({}, "@@dont_activate=x\nbody")
    assert "disable" not in new_entry


def test_apply_decorators_accepts_the_downgraded_triple_at() -> None:
    new_entry, content = apply_decorators({}, "@@nope\n@@@activate\nbody")
    assert new_entry["constant"] is True
    assert content == "body"


@pytest.mark.parametrize("argument", ["", "none", "NULL", "unset"])
def test_scan_depth_can_be_cleared(argument: str) -> None:
    new_entry, _ = apply_decorators({"scan_depth": 9}, f"@@scan_depth={argument}\nbody")
    assert new_entry["scan_depth"] is None


def test_the_last_decorator_wins() -> None:
    new_entry, _ = apply_decorators({}, "@@depth=1\n@@depth=2\nbody")
    assert new_entry["depth"] == 2


def test_an_unknown_decorator_does_not_block_the_next_known_one() -> None:
    new_entry, content = apply_decorators({}, "@@nope\n@@depth=6\nbody")
    assert new_entry["depth"] == 6
    assert new_entry["decorators"] == ["@@depth=6"]
    assert content == "body"


def test_apply_decorators_without_decorators() -> None:
    entry = {"depth": 4}
    new_entry, content = apply_decorators(entry, "just text")
    assert new_entry == {"depth": 4, "decorators": []}
    assert content == "just text"
    assert entry == {"depth": 4}


def test_apply_decorators_does_not_mutate_the_input_mapping() -> None:
    entry = {"depth": 4}
    apply_decorators(entry, "@@depth=9\nbody")
    assert entry == {"depth": 4}


def test_apply_decorators_reads_many_decorators_from_one_content() -> None:
    new_entry, content = apply_decorators(
        {"depth": 4, "position": 0},
        "@@activate\n@@depth=12\n@@position=atDepth\n@@role=SYSTEM\n@@scan_depth=none\nbody",
    )
    assert new_entry == {
        "depth": 12,
        "position": 4,
        "role": "system",
        "scan_depth": None,
        "constant": True,
        "decorators": [
            "@@activate",
            "@@depth=12",
            "@@position=atDepth",
            "@@role=SYSTEM",
            "@@scan_depth=none",
        ],
    }
    assert content == "body"
