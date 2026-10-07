"""Unit tests for the ported SillyTavern prompt message model.

The behavioural reference is ``tools/st-oracle/run_prompt.mjs``: the fixtures in
``tools/st-oracle/fixtures/prompt/`` are run against the real ``openai.js`` and
against this port, and ``tools/st-oracle/diff_prompt.py`` compares them. These
tests pin the same behaviour offline (no Node needed) so a regression is caught
by ``pytest`` alone.
"""

from __future__ import annotations

import asyncio

import pytest

from tavern.st.chat_completion import (
    SQUASH_EXCLUDE_LIST,
    ChatCompletion,
    IdentifierNotFoundError,
    Message,
    MessageCollection,
    TokenBudgetExceededError,
    TokenHandler,
    count_tokens,
    token_handler,
)


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------------
# count_tokens
# ----------------------------------------------------------------------
def test_count_tokens_joins_the_same_parts_as_the_harness() -> None:
    # role + content joined with one space, then // 3
    assert count_tokens({"role": "user", "content": "abcdef"}) == 3
    assert count_tokens({"role": "user", "content": "abcdef", "name": "Narrator"}) == 6
    assert count_tokens({"role": "user", "content": ""}) == 1
    assert count_tokens({"role": "", "content": ""}) == 0
    assert count_tokens("abcdef") == 2
    assert count_tokens([{"role": "user", "content": "abcdef"}]) == 3


def test_count_tokens_includes_tool_calls() -> None:
    payload = {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]}
    assert count_tokens(payload) > 0


# ----------------------------------------------------------------------
# TokenHandler
# ----------------------------------------------------------------------
def test_token_handler_buckets_and_total() -> None:
    handler = TokenHandler()
    assert set(handler.get_counts()) == set(TokenHandler.BUCKETS)
    assert handler.get_total() == 0

    assert handler.count_async({"role": "user", "content": "abcdef"}) == 3
    assert handler.count_async({"role": "user", "content": "abcdef"}, token_type="prompt") == 3
    assert handler.get_tokens_for_identifier("prompt") == 3
    assert handler.get_total() == 3

    handler.uncount(1, "prompt")
    assert handler.get_tokens_for_identifier("prompt") == 2

    handler.set_counts({"prompt": 7})
    assert handler.get_tokens_for_identifier("prompt") == 7
    assert handler.get_tokens_for_identifier("bias") == 0

    handler.reset_counts()
    assert handler.get_total() == 0


def test_token_handler_async_alias_matches() -> None:
    async def scenario() -> int:
        handler = TokenHandler()
        return await handler.countAsync(
            {"role": "user", "content": "abcdef"}, token_type="conversation"
        )

    assert run(scenario()) == 3


def test_module_token_handler_is_the_shared_instance() -> None:
    """``count_async`` accumulates in the named bucket of the shared singleton."""
    token_handler.reset_counts()
    delta = token_handler.count_async({"role": "system", "content": "hello"}, token_type="examples")
    assert delta == 4  # "system hello" -> 12 // 3
    assert token_handler.get_tokens_for_identifier("examples") == delta
    token_handler.reset_counts()
    assert token_handler.get_total() == 0


# ----------------------------------------------------------------------
# Message
# ----------------------------------------------------------------------
def test_message_defaults_role_to_system_and_counts_tokens() -> None:
    message = Message("", "hello there", "greeting")
    assert message.role == "system"
    assert message.tokens == 0  # the plain constructor never counts

    counted = Message.create("user", "abcdef", "turn")
    assert counted.get_tokens() == 3
    assert counted.to_dict() == {"role": "user", "content": "abcdef"}


def test_empty_content_is_not_counted() -> None:
    assert Message.create("system", "", "empty").get_tokens() == 0


def test_set_name_requotes_the_token_count() -> None:
    message = Message.create("user", "abcdef", "turn")
    before = message.get_tokens()
    run(message.set_name("Narrator"))
    assert message.get_tokens() > before
    assert message.to_dict()["name"] == "Narrator"


def test_ensure_content_is_array_wraps_text() -> None:
    message = Message.create("user", "some text", "turn")
    content = message.ensure_content_is_array()
    assert content == [{"type": "text", "text": "some text"}]
    # calling it twice is idempotent
    assert message.ensure_content_is_array() is content


def test_tool_role_carries_the_call_id() -> None:
    message = Message("tool", "42", "call-1")
    assert message.to_dict()["tool_call_id"] == "call-1"


# ----------------------------------------------------------------------
# MessageCollection
# ----------------------------------------------------------------------
def test_collection_flattens_nested_groups_and_sums_tokens() -> None:
    inner = MessageCollection(
        "inner",
        Message.create("system", "first", "one"),
        Message.create("system", "second", "two"),
    )
    outer = MessageCollection("outer", inner, Message.create("user", "hello", "three"))
    assert [item.identifier for item in outer.flatten()] == ["one", "two", "three"]
    assert outer.get_tokens() == sum(item.get_tokens() for item in outer.flatten())
    assert outer.getTokens() == outer.get_tokens()
    assert outer.get_collection()[0] is inner
    assert outer.has_item_with_identifier("inner") is True
    assert outer.get_item_by_identifier("inner") is inner
    assert outer.get_item_by_identifier("nope") is None


def test_collection_rejects_foreign_objects() -> None:
    with pytest.raises(TypeError):
        MessageCollection("bad", "not a message")  # type: ignore[arg-type]


def test_collection_chat_skips_empty_messages_but_keeps_groups() -> None:
    collection = MessageCollection(
        "root",
        MessageCollection("group", Message("system", "", "empty")),
        Message("user", "kept", "kept"),
    )
    assert collection.get_chat() == [{"role": "user", "content": "kept"}]


# ----------------------------------------------------------------------
# ChatCompletion budget and squashing
# ----------------------------------------------------------------------
def test_token_budget_is_context_minus_response() -> None:
    completion = ChatCompletion()
    completion.set_token_budget(1000, 100)
    assert completion.token_budget == 900


def test_add_refuses_when_the_budget_is_short() -> None:
    completion = ChatCompletion()
    completion.set_token_budget(20, 19)
    collection = MessageCollection(
        "tooBig", Message.create("system", "this will not fit at all", "big")
    )
    with pytest.raises(TokenBudgetExceededError) as excinfo:
        completion.add(collection)
    assert excinfo.value.name == "TokenBudgetExceeded"
    assert completion.messages.collection == []


def test_add_and_remove_move_the_budget() -> None:
    completion = ChatCompletion()
    completion.set_token_budget(1000, 100)
    collection = MessageCollection("start", Message.create("system", "hello world", "main"))
    cost = collection.get_tokens()
    completion.add(collection)
    assert completion.token_budget == 900 - cost
    assert completion.has("start") is True

    run(completion.squash_system_messages())  # squash flattens the groups away
    assert completion.has("start") is False


def test_insert_start_and_end_place_the_message() -> None:
    completion = ChatCompletion()
    completion.set_token_budget(1000, 100)
    completion.add(MessageCollection("start", Message.create("system", "middle", "middleMessage")))
    completion.insert_at_start(Message.create("system", "first", "firstMessage"), "start")
    completion.insert_at_end(Message.create("system", "last", "lastMessage"), "start")
    assert [item["content"] for item in completion.get_chat()] == ["first", "middle", "last"]


def test_remove_last_from_drops_the_tail_and_frees_budget() -> None:
    completion = ChatCompletion()
    completion.set_token_budget(1000, 100)
    completion.add(
        MessageCollection(
            "start",
            Message.create("system", "keep", "keepMessage"),
            Message.create("system", "drop", "dropMessage"),
        )
    )
    before = completion.token_budget
    completion.remove_last_from("start")
    assert [item["content"] for item in completion.get_chat()] == ["keep"]
    assert completion.token_budget > before


def test_remove_last_from_an_empty_collection_is_a_noop() -> None:
    completion = ChatCompletion()
    completion.set_token_budget(1000, 100)
    completion.add(MessageCollection("start"))
    completion.remove_last_from("start")  # must not raise


def test_unknown_identifier_raises() -> None:
    completion = ChatCompletion()
    completion.set_token_budget(1000, 100)
    with pytest.raises(IdentifierNotFoundError) as excinfo:
        completion.insert_at_end(Message.create("user", "x", "x"), "missing")
    assert excinfo.value.name == "IdentifierNotFoundError"
    assert str(excinfo.value) == "Identifier not found."[:0] + "Identifier missing not found."


def test_can_afford_helpers() -> None:
    completion = ChatCompletion()
    completion.set_token_budget(20, 10)
    small = Message.create("user", "hi", "small")
    big = Message.create("user", "x" * 300, "big")
    assert completion.can_afford(small) is True
    assert completion.can_afford(big) is False
    assert completion.can_afford_all([small]) is True
    assert completion.can_afford_all([small, big]) is False


def test_squash_merges_unnamed_consecutive_system_messages() -> None:
    completion = ChatCompletion()
    completion.set_token_budget(1000, 100)
    completion.add(
        MessageCollection(
            "all",
            Message.create("system", "first", "one"),
            Message.create("system", "second", "two"),
            Message.create("user", "hello", "userTurn"),
            Message.create("system", "third", "three"),
        )
    )
    run(completion.squash_system_messages())
    chat = completion.get_chat()
    assert [item["content"] for item in chat] == ["first\nsecond", "hello", "third"]


def test_squash_drops_empty_and_protects_the_exclude_list() -> None:
    assert "newMainChat" in SQUASH_EXCLUDE_LIST
    completion = ChatCompletion()
    completion.set_token_budget(1000, 100)
    completion.add(
        MessageCollection(
            "all",
            Message.create("system", "", "emptySystem"),
            Message.create("system", "protected", "newMainChat"),
            Message.create("system", "also protected", "newChat"),
            Message.create("system", "groupNudge protected", "groupNudge"),
            Message.create("system", "merges", "mergeMe"),
        )
    )
    run(completion.squash_system_messages())
    assert [item["content"] for item in completion.get_chat()] == [
        "protected",
        "also protected",
        "groupNudge protected",
        "merges",
    ]


def test_squash_does_not_merge_across_a_named_system_message() -> None:
    """A named system message is a barrier: it only merges its unnamed neighbours."""
    completion = ChatCompletion()
    completion.set_token_budget(1000, 100)
    named = Message.create("system", "named block", "named")
    run(named.set_name("Narrator"))
    completion.add(
        MessageCollection(
            "all",
            Message.create("system", "plain block", "plain"),
            named,
            Message.create("system", "tail block", "tail"),
        )
    )
    run(completion.squash_system_messages())
    assert [item["content"] for item in completion.get_chat()] == [
        "plain block",
        "named block",
        "tail block",
    ]


def test_squash_recounts_the_merged_message() -> None:
    completion = ChatCompletion()
    completion.set_token_budget(1000, 100)
    first = Message.create("system", "aaaa", "one")
    second = Message.create("system", "bbbbbbbb", "two")
    completion.add(MessageCollection("all", first, second))
    run(completion.squash_system_messages())
    merged = completion.messages.collection[0]
    assert merged.content == "aaaa\nbbbbbbbb"
    assert merged.get_tokens() == count_tokens({"role": "system", "content": merged.content})


def test_overridden_prompts_round_trip() -> None:
    completion = ChatCompletion()
    assert completion.get_overridden_prompts() == []
    completion.set_overridden_prompts(["main", "jailbreak"])
    assert completion.get_overridden_prompts() == ["main", "jailbreak"]


def test_invalid_collection_and_message_are_rejected() -> None:
    completion = ChatCompletion()
    completion.set_token_budget(100, 10)
    with pytest.raises(TypeError):
        completion.add(Message.create("user", "x", "x"))  # type: ignore[arg-type]
    completion.add(MessageCollection("ok"))
    with pytest.raises(TypeError):
        completion.insert(MessageCollection("nope"), "ok")  # type: ignore[arg-type]


def test_logging_toggle_does_not_raise() -> None:
    completion = ChatCompletion()
    completion.enable_logging()
    completion.log("noise")
    completion.disable_logging()
    completion.log("silence")


def test_camel_case_aliases_preserve_recursion() -> None:
    """A nested group must contribute its own tokens through the JS-style name."""
    inner = MessageCollection("inner", Message.create("system", "aaaa", "one"))
    outer = MessageCollection("outer", inner)
    assert outer.getTokens() == inner.getTokens()
    assert outer.get_tokens() == outer.getTokens()


def test_readme_example_matches_the_reference_fixture() -> None:
    """The shape of the reference fixture, asserted without running Node."""
    completion = ChatCompletion()
    completion.set_token_budget(1000, 100)
    completion.add(
        MessageCollection(
            "start", Message.create("system", "You are a careful assistant.", "mainPrompt")
        )
    )
    completion.add(
        MessageCollection(
            "inner",
            Message.create("system", "first squashed block", "blockOne"),
            Message.create("system", "second squashed block", "blockTwo"),
            Message.create("system", "", "emptySystem"),
            Message.create("system", "excluded from squashing", "newMainChat"),
            Message.create("system", "kept separately", "newChat"),
            Message.create("user", "hello there", "userTurn"),
            Message.create("assistant", "general kenobi", "assistantTurn"),
        )
    )
    run(completion.squash_system_messages())
    assert [item["content"] for item in completion.get_chat()] == [
        "You are a careful assistant.\nfirst squashed block\nsecond squashed block",
        "excluded from squashing",
        "kept separately",
        "hello there",
        "general kenobi",
    ]
