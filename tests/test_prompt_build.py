"""Offline tests for :mod:`tavern.st.prompt_build`.

Everything here runs without Node and without SillyTavern: the reference
semantics are encoded as explicit expectations copied from
``research/_raw/st-src/openai.js`` with the line number in the test name or the
comment. The two doubles at the top stand in for
:class:`tavern.st.chat_completion.ChatCompletion` / ``Message`` so the *call
sequence* (which identifier, which position, which order) can be asserted
without re-implementing the Lead's budget arithmetic.

Order-sensitive claims are written as full identifier sequences, never as spot
checks: the prompt-assembly oracle in ``tools/st-oracle/`` compares the same
sequences against the real ``openai.js`` under Node.
"""

from __future__ import annotations

import asyncio
from typing import Any

from tavern.st.chat_completion import ChatCompletion, MessageCollection
from tavern.st.prompt_build import (
    DEFAULT_INJECTION_ORDER,
    EXTENSION_PROMPT_ROLES,
    CharacterNamesBehavior,
    ContinuePostfix,
    Prompt,
    PromptCollection,
    apply_continue_postfix,
    format_world_info,
    get_prompt_position,
    get_prompt_role,
    is_valid_name,
    populate_chat_completion,
    populate_chat_history,
    populate_dialogue_examples,
    population_injection_prompts,
    prepare_prompts_for_chat_completion,
    sanitize_name,
)


def run(coro):
    """Drive one coroutine without needing ``pytest-asyncio``."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Test doubles.
# ---------------------------------------------------------------------------


class FakeMessage:
    """A ``Message`` with only what this module touches.

    ``identifier`` and ``name`` are public, exactly like the reference class.
    """

    def __init__(self, role: str | None, content: Any = "", identifier: str | None = None) -> None:
        self.role = role
        self.content = content
        self.identifier = identifier
        self.name: str | None = None
        self.injected = False
        self.tool_calls: Any = None

    def set_name(self, name: Any) -> None:
        self.name = name

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "name": self.name,
            "identifier": self.identifier,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        name = f"/{self.name}" if self.name else ""
        return f"FakeMessage({self.role!r}, {self.identifier!r}{name})"


class FakeChatCompletion:
    """Records the reference call sequence; the budget is always affordable.

    It holds the **real** :class:`~tavern.st.chat_completion.MessageCollection`
    objects that :mod:`tavern.st.prompt_build` creates, only the messages are
    :class:`FakeMessage`. The order semantics mirror the reference exactly:

    * ``add(collection, index)`` (``openai.js:3998-4004``) splices at the index,
      so an occupied slot *shifts* rather than being overwritten; ``None`` /
      ``-1`` / an out-of-range index append (JS ``splice`` clamps).
    * ``insert*`` (``openai.js:4042``) looks the identifier up as a *group* and
      inserts into it -- ``'end'`` is an append, ``'start'`` an ``insert(0)``.
      This is what makes ``injectToMain`` land inside the ``main`` group.
    """

    def __init__(self) -> None:
        self.messages = MessageCollection("root")
        self.reserved: list[Any] = []
        self.freed: list[Any] = []
        self.overridden_prompts: list[str] = []

    # -- maintenance ------------------------------------------------------
    def set_overridden_prompts(self, identifiers: list[str]) -> None:
        self.overridden_prompts = list(identifiers)

    def set_token_budget(self, context: int, response: int) -> None:  # pragma: no cover
        self.budget = (context, response)

    def reserve_budget(self, item: Any) -> None:
        self.reserved.append(item)

    def free_budget(self, item: Any) -> None:
        self.freed.append(item)

    def can_afford(self, item: Any) -> bool:
        return True

    def can_afford_all(self, items: list[Any]) -> bool:
        return True

    def get_total_token_count(self) -> int:  # pragma: no cover
        return 0

    def squash_system_messages(self) -> None:  # pragma: no cover
        return None

    # -- assembly ---------------------------------------------------------
    def add(self, item: Any, position: Any = None) -> None:
        """``chatCompletion.add(collection, index)`` (``openai.js:3998-4006``).

        The reference **assigns the slot** -- ``this.messages.collection[position]
        = collection`` -- and pads the array when the index is past the end, so
        an index that is already occupied is *replaced* and a gap can open up.
        This is deliberately not a splice: the Lead's ``ChatCompletion`` does the
        assignment, and the assembly oracle compares both sides entry by entry.
        """
        if item is None:
            return
        top = self.messages.collection
        if position is None or position == -1:
            top.append(item)
            return
        while len(top) <= position:
            top.append(None)
        top[position] = item

    def insert(self, message: Any, identifier: str, position: Any = None) -> None:
        """``openai.js:4042-4061`` -- place ``message`` inside a group.

        ``identifier`` names the :class:`MessageCollection` to insert into (all
        call sites pass a prompt-block identifier, never a message one), and the
        message goes inside it: ``'end'`` appends, ``'start'`` prepends, an
        integer is a plain list index. Raises like the reference when the
        identifier is not a collection.
        """
        if message is None:
            return
        group = self._find_collection(identifier)
        if group is None:
            raise KeyError(f"Identifier {identifier} not found.")
        if position == "start":
            group.collection.insert(0, message)
        elif isinstance(position, int):
            group.collection.insert(position, message)
        else:
            group.collection.append(message)

    def insert_at_start(self, message: Any, identifier: str) -> None:
        """``openai.js:4021`` -- ``insert(message, identifier, 'start')``."""
        self.insert(message, identifier, "start")

    def insert_at_end(self, message: Any, identifier: str) -> None:
        """``openai.js:4028`` -- ``insert(message, identifier, 'end')``."""
        self.insert(message, identifier, "end")

    def has(self, identifier: str) -> bool:
        """``openai.js:4088`` -- is there a **collection** with that identifier?"""
        return self._find_collection(identifier) is not None

    # -- helpers ----------------------------------------------------------
    def _find_collection(self, identifier: str) -> MessageCollection | None:
        for item in self.flatten_groups():
            if item.identifier == identifier:
                return item
        return None

    def flatten_groups(self) -> list[MessageCollection]:
        """Every ``MessageCollection`` in the tree, depth first (holes skipped)."""
        out: list[MessageCollection] = []

        def visit(container: MessageCollection) -> None:
            for item in container.collection:
                if isinstance(item, MessageCollection):
                    out.append(item)
                    visit(item)

        visit(self.messages)
        return out

    def flatten(self) -> list[Any]:
        """Every non-``None`` item, group boundaries removed."""

        def visit(container: MessageCollection) -> list[Any]:
            out: list[Any] = []
            for item in container.collection:
                if isinstance(item, MessageCollection):
                    out.extend(visit(item))
                elif item is not None:
                    out.append(item)
            return out

        return visit(self.messages)

    def get_chat(self) -> list[dict[str, Any]]:
        return [
            message.to_dict() for message in self.flatten() if message.content or message.tool_calls
        ]

    def identifiers(self) -> list[str]:
        """Top-level identifiers, i.e. one entry per non-empty direct child.

        ``add`` can leave a ``None`` hole (``openai.js:4002-4006``), which has no
        identifier and is skipped by the reference ``getChat`` as well.
        """
        return [item.identifier for item in self.messages.collection if item is not None]

    def flat_names(self) -> list[str]:
        """Every message identifier in chat order, with its name when set."""
        out: list[str] = []
        for message in self.flatten():
            out.append(
                f"{message.identifier}@{message.name}" if message.name else str(message.identifier)
            )
        return out


def message_factory(role: str | None, content: Any, identifier: str | None):
    return FakeMessage(role, content, identifier)


def chat_prompt(
    role: str, content: str, identifier: str | None = None, name: str | None = None
) -> dict:
    return {"role": role, "content": content, "identifier": identifier, "name": name}


def chat(completion: FakeChatCompletion) -> list[dict[str, Any]]:
    """The flattened chat payload; group boundaries removed, dicts like ``getChat``."""
    return completion.get_chat()


def history_messages(completion: FakeChatCompletion) -> list[dict[str, Any]]:
    """The chat-history messages only, oldest first.

    ``insertAtStart`` prepends, so the raw order is newest-first; this helper
    also drops the ``newMainChat`` marker, which is not a conversation turn.
    """
    turns = [
        message
        for message in chat(completion)
        if str(message["identifier"]).startswith("chatHistory-")
    ]
    return list(reversed(turns))


def only(completion: FakeChatCompletion, identifier: str) -> Any:
    """The single *top-level* group/message with that identifier."""
    matches = [
        item
        for item in completion.messages.collection
        if getattr(item, "identifier", None) == identifier
    ]
    assert len(matches) == 1, f"expected exactly one {identifier!r}, got {matches!r}"
    return matches[0]


def content_at(completion: FakeChatCompletion, index: int) -> Any:
    return chat(completion)[index]["content"]


def collection_with_history(extra: list[dict] | None = None) -> PromptCollection:
    entries = [*(extra or []), {"identifier": "chatHistory"}, {"identifier": "dialogueExamples"}]
    return PromptCollection(*entries)


# ---------------------------------------------------------------------------
# populationInjectionPrompts -- openai.js:810-875
# ---------------------------------------------------------------------------


def contents(items: list) -> list:
    """Read ``content`` from the mixed list the injection pass returns.

    :func:`population_injection_prompts` pushes **plain dicts** for the injected
    blocks (``openai.js:861``) next to whatever message objects the caller
    passed, so both shapes show up in one list.
    """
    return [item["content"] if isinstance(item, dict) else item.content for item in items]


def test_injection_depth_zero_lands_after_the_chat():
    """openai.js:873 -- the reverse puts the depth-0 block at the tail."""
    prompts = [
        Prompt(identifier="i0", injection_depth=0, injection_order=100, role="system", content="I0")
    ]
    messages = [FakeMessage("user", "A"), FakeMessage("assistant", "B")]

    result = run(population_injection_prompts(prompts, messages))

    assert contents(result) == ["B", "A", "I0"]


def test_injection_depths_and_the_total_inserted_offset():
    """openai.js:811,867-869 -- ``i + totalInsertedMessages``.

    Non-empty depths 0 and 1 already push the effective index of depth 1 from 1
    to 2; the empty depth 2 in between must not shift anything.
    """
    prompts = [
        Prompt(
            identifier="d1", injection_depth=1, injection_order=100, role="system", content="D1"
        ),
        Prompt(identifier="d2", injection_depth=2, injection_order=100, role="system", content=""),
        Prompt(
            identifier="d0", injection_depth=0, injection_order=100, role="system", content="D0"
        ),
        Prompt(
            identifier="d3", injection_depth=3, injection_order=100, role="system", content="D3"
        ),
    ]
    messages = [FakeMessage("user", "A")]

    result = run(
        population_injection_prompts(prompts, messages, get_extension_prompt_max_depth=lambda: 3)
    )

    # splice(0) -> [D0, A]; splice(1 + 1) -> [D0, A, D1]; depth 2 empty;
    # splice(3 + 2) clamps to the end -> [D0, A, D1, D3]; then reverse().
    assert contents(result) == ["D3", "D1", "A", "D0"]
    # Without the max-depth callback only depth 0 is visited (openai.js:819).
    only_depth_zero = run(population_injection_prompts(prompts, [FakeMessage("user", "A")]))
    assert contents(only_depth_zero) == ["A", "D0"]


def test_injection_uses_the_max_depth_callback():
    """openai.js:819-820 -- ``getExtensionPromptMaxDepth()`` is the loop bound."""
    prompts = [
        Prompt(
            identifier="deep", injection_depth=1, injection_order=100, role="system", content="X"
        )
    ]
    messages = [FakeMessage("user", "A")]

    without = run(population_injection_prompts(prompts, list(messages)))
    assert contents(without) == ["A"]

    with_callback = run(
        population_injection_prompts(
            prompts, list(messages), get_extension_prompt_max_depth=lambda: 1
        )
    )
    # The injected block is spliced at depth 1 and then the whole list is
    # reversed, so it ends up in front of the single chat message.
    assert contents(with_callback) == ["X", "A"]


def test_injection_priority_is_descending_then_role_order():
    """openai.js:842 (``+b - +a``, high first) and 847/873 (roles, then reverse).

    Before the final reverse the blocks read
    ``[system:200, system:100, user:100, assistant:100, system:10]``; the reverse
    is what turns the role order into ``assistant, user, system`` inside one
    priority group.
    """
    prompts = [
        Prompt(
            identifier="low", injection_depth=0, injection_order=10, role="system", content="LOW"
        ),
        Prompt(
            identifier="same-a",
            injection_depth=0,
            injection_order=100,
            role="assistant",
            content="A",
        ),
        Prompt(
            identifier="same-u", injection_depth=0, injection_order=100, role="user", content="U"
        ),
        Prompt(
            identifier="same-s", injection_depth=0, injection_order=100, role="system", content="S"
        ),
        Prompt(
            identifier="high", injection_depth=0, injection_order=200, role="system", content="HIGH"
        ),
    ]

    result = run(population_injection_prompts(prompts, []))

    assert contents(result) == ["LOW", "A", "U", "S", "HIGH"]
    assert [message["role"] for message in result] == [
        "system",
        "assistant",
        "user",
        "system",
        "system",
    ]
    assert all(message["injected"] is True for message in result)


def test_injection_default_order_is_one_hundred():
    """openai.js:834 -- ``prompt.injection_order ?? 100``."""
    prompts = [
        Prompt(
            identifier="implicit",
            injection_depth=0,
            injection_order=None,
            role="system",
            content="IMPLICIT",
        ),
        Prompt(
            identifier="explicit",
            injection_depth=0,
            injection_order=100,
            role="system",
            content="EXPLICIT",
        ),
    ]

    result = run(population_injection_prompts(prompts, []))

    # Both land in the same priority bucket and the same role, so the reference
    # joins them with '\n' into a single system message (openai.js:849-852).
    assert contents(result) == ["IMPLICIT\nEXPLICIT"]
    assert DEFAULT_INJECTION_ORDER == 100


def test_injection_joins_one_message_per_role_with_newline():
    """openai.js:849-852,861 -- contents are joined, one message per role."""
    prompts = [
        Prompt(
            identifier="s1", injection_depth=0, injection_order=100, role="system", content="S1"
        ),
        Prompt(
            identifier="s2", injection_depth=0, injection_order=100, role="system", content="S2"
        ),
        Prompt(identifier="u1", injection_depth=0, injection_order=100, role="user", content="U1"),
    ]

    result = run(population_injection_prompts(prompts, []))

    assert [(message["role"], message["content"]) for message in result] == [
        ("user", "U1"),
        ("system", "S1\nS2"),
    ]


def test_injection_empty_content_is_dropped():
    """openai.js:822 -- the ``prompt.content`` filter."""
    prompts = [
        Prompt(
            identifier="empty", injection_depth=0, injection_order=100, role="system", content=""
        ),
        Prompt(
            identifier="none", injection_depth=0, injection_order=100, role="system", content=None
        ),
        Prompt(
            identifier="real", injection_depth=0, injection_order=100, role="system", content="REAL"
        ),
    ]

    result = run(population_injection_prompts(prompts, []))

    assert contents(result) == ["REAL"]


def test_injection_extension_prompt_only_for_order_100():
    """openai.js:855-858 -- fetched for the ``'100'`` bucket only, appended after."""
    seen: list[tuple] = []

    def get_extension_prompt(position, depth, separator, role, wrap):
        seen.append((position, depth, separator, role, wrap))
        return "EXT"

    prompts = [
        Prompt(
            identifier="at100",
            injection_depth=0,
            injection_order=100,
            role="system",
            content="ROLE",
        ),
        Prompt(
            identifier="at200",
            injection_depth=0,
            injection_order=200,
            role="system",
            content="HIGH",
        ),
    ]

    result = run(
        population_injection_prompts(
            prompts,
            [],
            get_extension_prompt=get_extension_prompt,
            get_extension_prompt_max_depth=lambda: 0,
        )
    )

    # One call per role at depth 0 (three roles iterate per group), but only for
    # the group whose key is '100'. The numeric extension role is passed through
    # in the enum form (openai.js:813-817).
    assert [call[3] for call in seen] == [EXTENSION_PROMPT_ROLES["system"], 1, 2]
    assert all(
        call[0] == 2 and call[1] == 0 and call[2] == "\n" and call[4] is False for call in seen
    )
    # Order 200 is processed first and gets no extension text; the order-100
    # group then emits one block per role, each carrying the extension text.
    assert contents(result) == ["EXT", "EXT", "ROLE\nEXT", "HIGH"]


def test_injection_extension_prompt_accepts_a_coroutine():
    """``await getExtensionPrompt`` -- the Python side allows both shapes."""

    async def get_extension_prompt(position, depth, separator, role, wrap):
        return "ASYNC-EXT"

    prompts = [
        Prompt(
            identifier="s", injection_depth=0, injection_order=100, role="system", content="ROLE"
        )
    ]

    result = run(
        population_injection_prompts(
            prompts,
            [],
            get_extension_prompt=get_extension_prompt,
            get_extension_prompt_max_depth=lambda: 0,
        )
    )

    assert result[0]["role"] == "assistant"
    assert result[0]["content"] == "ASYNC-EXT"
    assert result[-1]["role"] == "system"
    assert result[-1]["content"] == "ROLE\nASYNC-EXT"


def test_injection_reverses_the_passed_list_in_place():
    """openai.js:873 -- ``messages.reverse()`` mutates the caller's array."""
    messages = [FakeMessage("user", "A"), FakeMessage("assistant", "B")]

    result = run(population_injection_prompts([], messages))

    assert result is messages
    assert contents(messages) == ["B", "A"]


def test_injection_does_not_mutate_the_prompt_objects():
    prompts = [
        Prompt(identifier="i0", injection_depth=0, injection_order=100, role="system", content="I0")
    ]

    run(population_injection_prompts(prompts, []))

    assert prompts[0].content == "I0"


# ---------------------------------------------------------------------------
# populateChatHistory -- openai.js:885-1092
# ---------------------------------------------------------------------------


def test_history_returns_early_without_the_chat_history_prompt():
    completion = FakeChatCompletion()
    run(
        populate_chat_history(
            [chat_prompt("user", "hi")],
            PromptCollection(),
            completion,
            message_factory=message_factory,
        )
    )

    assert completion.get_chat() == []
    assert completion.reserved == []


def test_history_message_order_and_identifiers():
    """openai.js:948-955 -- reversed pool, ``chatHistory-${length - index}``.

    The pool is walked newest-first (``948``) and every message is prepended
    (``1071``), so ``chatHistory-3`` is the last one prepended and ends up
    first; the ``newMainChat`` message is prepended after the loop (``1079``)
    and therefore sits in front of the whole history.
    """
    messages = [
        chat_prompt("user", "one"),
        chat_prompt("assistant", "two"),
        chat_prompt("user", "three"),
    ]
    completion = FakeChatCompletion()

    run(
        populate_chat_history(
            messages, collection_with_history(), completion, message_factory=message_factory
        )
    )

    assert completion.identifiers() == ["chatHistory"]
    assert completion.flat_names() == [
        "newMainChat",
        "chatHistory-1",
        "chatHistory-2",
        "chatHistory-3",
    ]
    assert content_at(completion, 0) == "[Start a new Chat]"
    assert [message["content"] for message in chat(completion)[1:]] == ["one", "two", "three"]
    # The reserved new-chat message is freed before it is inserted (1078-1079).
    assert completion.freed == [completion.flatten()[0]]


def test_history_new_chat_prompt_uses_the_group_variant():
    """openai.js:893 -- ``selected_group`` swaps the setting."""
    completion = FakeChatCompletion()
    run(
        populate_chat_history(
            [],
            collection_with_history(),
            completion,
            settings={"new_group_chat_prompt": "GROUP START"},
            selected_group=True,
            message_factory=message_factory,
        )
    )

    assert content_at(completion, -1) == "GROUP START"
    assert chat(completion)[-1]["identifier"] == "newMainChat"
    assert chat(completion)[-1]["role"] == "system"


def test_history_names_behavior_none_never_sets_a_name():
    """openai.js:957 -- only ``COMPLETION`` sets ``name``."""
    messages = [chat_prompt("assistant", "hi", name="Iris Vale")]
    completion = FakeChatCompletion()

    run(
        populate_chat_history(
            messages,
            collection_with_history(),
            completion,
            settings={"names_behavior": CharacterNamesBehavior.NONE},
            message_factory=message_factory,
        )
    )

    assert history_messages(completion)[0]["name"] is None


def test_history_names_behavior_default_never_sets_a_name():
    messages = [chat_prompt("assistant", "hi", name="Iris")]
    completion = FakeChatCompletion()

    run(
        populate_chat_history(
            messages,
            collection_with_history(),
            completion,
            settings={"names_behavior": CharacterNamesBehavior.DEFAULT},
            message_factory=message_factory,
        )
    )

    assert history_messages(completion)[0]["name"] is None


def test_history_names_behavior_completion_sanitizes_invalid_names():
    """PromptManager.js:1343-1351 -- valid passes through, invalid is rewritten."""
    messages = [
        chat_prompt("assistant", "valid", name="Iris_01"),
        chat_prompt("user", "invalid", name="Iris Vale!"),
    ]
    completion = FakeChatCompletion()

    run(
        populate_chat_history(
            messages,
            collection_with_history(),
            completion,
            settings={"names_behavior": CharacterNamesBehavior.COMPLETION},
            message_factory=message_factory,
        )
    )

    newest, oldest = history_messages(completion)[::-1]
    assert oldest["name"] == "Iris_01"
    assert newest["name"] == "Iris_Vale_"
    # The two helper functions the branch is built from.
    assert is_valid_name("Iris_01") is True
    assert is_valid_name("Iris Vale") is False
    assert is_valid_name("") is False
    assert is_valid_name("a" * 65) is False
    assert sanitize_name("a" * 70) == "a" * 64


def test_history_names_behavior_accepts_the_name_string():
    """The brief calls ``COMPLETION`` "ALWAYS"; both spellings are accepted."""
    messages = [chat_prompt("assistant", "hi", name="a b")]
    completion = FakeChatCompletion()

    run(
        populate_chat_history(
            messages,
            collection_with_history(),
            completion,
            settings={"names_behavior": "always"},
            message_factory=message_factory,
        )
    )

    assert history_messages(completion)[-1]["name"] == "a_b"


def test_history_send_if_empty_pads_an_assistant_final_chat():
    """openai.js:929-933 -- ``emptyUserMessageReplacement``."""
    messages = [chat_prompt("assistant", "tail")]
    completion = FakeChatCompletion()

    run(
        populate_chat_history(
            messages,
            collection_with_history(),
            completion,
            settings={"send_if_empty": "..."},
            message_factory=message_factory,
        )
    )

    # The pad message is prepended *before* the loop (openai.js:932), so the
    # history that is prepended afterwards sits in front of it; newMainChat is
    # prepended last and therefore leads the group.
    assert completion.flat_names() == [
        "newMainChat",
        "chatHistory-1",
        "emptyUserMessageReplacement",
    ]
    # The pad is a user turn holding the configured text (openai.js:930).
    pad = chat(completion)[-1]
    assert (pad["role"], pad["content"]) == ("user", "...")


def test_history_send_if_empty_skipped_for_a_user_final_chat():
    messages = [chat_prompt("user", "tail")]
    completion = FakeChatCompletion()

    run(
        populate_chat_history(
            messages,
            collection_with_history(),
            completion,
            settings={"send_if_empty": "..."},
            message_factory=message_factory,
        )
    )

    assert "emptyUserMessageReplacement" not in completion.flat_names()


def test_history_group_nudge_goes_last_and_needs_a_selected_group():
    """openai.js:900,1082-1085."""
    collection = collection_with_history([{"identifier": "groupNudge", "content": "[nudge]"}])
    messages = [chat_prompt("user", "hi")]

    with_group = FakeChatCompletion()
    run(
        populate_chat_history(
            messages, collection, with_group, selected_group=True, message_factory=message_factory
        )
    )
    # newMainChat is prepended, the nudge is appended (openai.js:1079,1084).
    assert with_group.flat_names() == ["newMainChat", "chatHistory-1", "groupNudge"]
    # The nudge is the reserved message inserted at the end of the group.
    assert with_group.flatten()[-1] is with_group.reserved[-1]

    without_group = FakeChatCompletion()
    run(
        populate_chat_history(
            messages,
            collection_with_history([{"identifier": "groupNudge", "content": "[nudge]"}]),
            without_group,
            message_factory=message_factory,
        )
    )
    assert "groupNudge" not in without_group.flat_names()


def test_history_group_nudge_is_skipped_for_impersonate():
    """openai.js:899 -- ``noGroupNudgeTypes = ['impersonate']``."""
    collection = collection_with_history([{"identifier": "groupNudge", "content": "[nudge]"}])
    completion = FakeChatCompletion()

    run(
        populate_chat_history(
            [chat_prompt("user", "hi")],
            collection,
            completion,
            type="impersonate",
            selected_group=True,
            message_factory=message_factory,
        )
    )

    assert "groupNudge" not in completion.flat_names()


def test_history_continue_nudge_collection_is_appended():
    """openai.js:907-927 -- the last non-injected message, then the nudge."""
    messages = [
        chat_prompt("user", "one"),
        chat_prompt("assistant", "two"),
    ]
    completion = FakeChatCompletion()

    run(
        populate_chat_history(
            messages,
            collection_with_history(),
            completion,
            type="continue",
            cycle_prompt="two",
            message_factory=message_factory,
        )
    )

    # The continue branch pops the assistant message off the list, so only 'one'
    # survives into the history pool; the nudge collection is added at the tail
    # (openai.js:1090 -- position -1). The dialogueExamples group of the same
    # collection was never populated, which is why it is not in the chat.
    assert completion.identifiers() == ["chatHistory", "continueNudge"]
    assert completion.flat_names() == [
        "newMainChat",
        "chatHistory-1",
        "continueNudge",
    ]
    nudge = only(completion, "continueNudge")
    assert nudge.collection[-1].content == (
        "[Continue your last message without repeating its original content.]"
    )
    assert nudge.collection[-1].role == "system"
    assert messages == [chat_prompt("user", "one")]


def test_history_continue_is_skipped_when_prefill_is_enabled():
    completion = FakeChatCompletion()
    run(
        populate_chat_history(
            [chat_prompt("assistant", "two")],
            collection_with_history(),
            completion,
            type="continue",
            cycle_prompt="two",
            settings={"continue_prefill": True},
            message_factory=message_factory,
        )
    )

    assert "continueNudge" not in completion.flat_names()


def test_continue_postfix_is_not_duplicated():
    """``script.js:4978-4979`` appends once; an existing tail is left alone."""
    assert apply_continue_postfix("text", " ") == "text "
    assert apply_continue_postfix("text ", " ") == "text "
    assert apply_continue_postfix("text", "") == "text"
    assert apply_continue_postfix("text", None) == "text"
    assert apply_continue_postfix("a\n", "\n") == "a\n"
    assert apply_continue_postfix("a", "\n\n") == "a\n\n"
    assert apply_continue_postfix("", " ") == " "


def test_continue_postfix_defaults_are_the_reference_literals():
    assert ContinuePostfix.NONE == ""
    assert ContinuePostfix.SPACE == " "
    assert ContinuePostfix.NEWLINE == "\n"
    assert ContinuePostfix.DOUBLE_NEWLINE == "\n\n"


def test_history_does_not_mutate_the_caller_prompt_objects():
    messages = [{"role": "user", "content": "one"}]
    completion = FakeChatCompletion()

    run(
        populate_chat_history(
            messages, collection_with_history(), completion, message_factory=message_factory
        )
    )

    assert messages == [{"role": "user", "content": "one"}]


# ---------------------------------------------------------------------------
# populateDialogueExamples -- openai.js:1101-1134
# ---------------------------------------------------------------------------


def test_examples_insert_marker_before_each_dialogue():
    """openai.js:1108-1131 -- ``newChat`` then the messages, per dialogue."""
    examples = [
        [
            {"role": "user", "content": "e1u", "name": "User"},
            {"role": "assistant", "content": "e1a", "name": "Iris"},
        ],
        [{"role": "user", "content": "e2u", "name": "User"}],
    ]
    completion = FakeChatCompletion()

    run(
        populate_dialogue_examples(
            collection_with_history(), completion, examples, message_factory=message_factory
        )
    )

    assert completion.identifiers() == ["dialogueExamples"]
    assert completion.flat_names() == [
        "newChat",
        "dialogueExamples 0-0@User",
        "dialogueExamples 0-1@Iris",
        "newChat",
        "dialogueExamples 1-0@User",
    ]
    assert chat(completion)[0]["content"] == "[Example Chat]"
    assert all(message["role"] == "system" for message in chat(completion))


def test_examples_returns_early_without_the_dialogue_examples_prompt():
    completion = FakeChatCompletion()
    run(
        populate_dialogue_examples(
            PromptCollection({"identifier": "chatHistory"}),
            completion,
            [[{"content": "x"}]],
            message_factory=message_factory,
        )
    )

    assert completion.get_chat() == []


def test_examples_empty_list_only_adds_its_own_collection():
    completion = FakeChatCompletion()
    run(
        populate_dialogue_examples(
            collection_with_history(), completion, [], message_factory=message_factory
        )
    )

    # Only this function ran, so only its collection is in the chat.
    assert completion.identifiers() == ["dialogueExamples"]
    assert completion.flatten() == []


def test_examples_all_messages_are_system_role_even_when_asked_otherwise():
    examples = [[{"role": "assistant", "content": "e", "name": "Iris"}]]
    completion = FakeChatCompletion()

    run(
        populate_dialogue_examples(
            collection_with_history(), completion, examples, message_factory=message_factory
        )
    )

    assert chat(completion)[-1]["role"] == "system"
    assert chat(completion)[-1]["name"] == "Iris"


# ---------------------------------------------------------------------------
# getPromptPosition / getPromptRole -- openai.js:1140-1168
# ---------------------------------------------------------------------------


def test_prompt_position_and_role_mapping():
    assert get_prompt_position(0) == "start"
    assert get_prompt_position(1) == "end"
    assert get_prompt_position(2) is False
    assert get_prompt_position(None) is False

    assert get_prompt_role(0) == "system"
    assert get_prompt_role(1) == "user"
    assert get_prompt_role(2) == "assistant"
    assert get_prompt_role(99) == "system"
    assert get_prompt_role(None) == "system"
    assert EXTENSION_PROMPT_ROLES == {"system": 0, "user": 1, "assistant": 2}


def test_format_world_info_passes_through_without_a_format():
    assert format_world_info("blob") == "blob"
    assert format_world_info(None) == ""
    assert format_world_info("blob", "[WI]{0}") == "[WI]blob"


# ---------------------------------------------------------------------------
# populateChatCompletion -- openai.js:1185-1347 (the S2 core: order)
# ---------------------------------------------------------------------------


def test_assembly_order_entry_by_entry():
    """The whole assembly order, in one readable identifier sequence."""
    entries = [
        {
            "identifier": "worldInfoBefore",
            "system_prompt": True,
            "role": "system",
            "content": "WI_BEFORE",
        },
        {"identifier": "main", "system_prompt": True, "role": "system", "content": "MAIN"},
        {
            "identifier": "worldInfoAfter",
            "system_prompt": True,
            "role": "system",
            "content": "WI_AFTER",
        },
        {
            "identifier": "charDescription",
            "system_prompt": True,
            "role": "system",
            "content": "DESC",
        },
        {
            "identifier": "charPersonality",
            "system_prompt": True,
            "role": "system",
            "content": "PERS",
        },
        {"identifier": "scenario", "system_prompt": True, "role": "system", "content": "SCEN"},
        {
            "identifier": "personaDescription",
            "system_prompt": True,
            "role": "system",
            "content": "PERSONA",
        },
        {"identifier": "nsfw", "system_prompt": True, "role": "system", "content": "NSFW"},
        {"identifier": "jailbreak", "system_prompt": True, "role": "system", "content": "JB"},
        {
            "identifier": "myChatPrompt",
            "role": "user",
            "content": "USER_BLOCK",
            "system_prompt": False,
        },
        {"identifier": "bias", "system_prompt": True, "role": "assistant", "content": "BIAS"},
        {
            "identifier": "enhanceDefinitions",
            "system_prompt": True,
            "role": "system",
            "content": "ENHANCE",
        },
        {"identifier": "quietPrompt", "system_prompt": True, "role": "system", "content": "QUIET"},
        {"identifier": "dialogueExamples"},
        {"identifier": "chatHistory"},
    ]
    completion = FakeChatCompletion()

    run(
        populate_chat_completion(
            PromptCollection(*entries),
            completion,
            bias="BIAS",
            quiet_prompt="QUIET",
            messages=[chat_prompt("user", "hi")],
            message_factory=message_factory,
        )
    )

    assert completion.identifiers() == [
        "main",
        "worldInfoBefore",
        "worldInfoAfter",
        "charDescription",
        "charPersonality",
        "scenario",
        "personaDescription",
        "enhanceDefinitions",
        "nsfw",
        "jailbreak",
        "myChatPrompt",
        "bias",
        "chatHistory",
        "dialogueExamples",
        "controlPrompts",
    ]
    assert completion.flat_names()[-4:] == [
        "chatHistory-1",
        "newMainChat",
        "quietPrompt",
        "quietPrompt",
    ]
    # openai.js:1210 -- the priming reservation happens before anything else.
    assert completion.reserved[0] == 3
    # openai.js:1346 -- the control collection is added once, as a group.
    assert only(completion, "controlPrompts").flatten()[-1].content == "QUIET"


def test_assembly_order_without_bias():
    """openai.js:1263 -- ``addToChatCompletion('bias')`` needs a non-empty bias."""
    entries = [
        {"identifier": "main", "system_prompt": True, "role": "system", "content": "MAIN"},
        {"identifier": "bias", "system_prompt": True, "role": "assistant", "content": "BIAS"},
        {"identifier": "chatHistory"},
    ]
    completion = FakeChatCompletion()

    run(
        populate_chat_completion(
            PromptCollection(*entries),
            completion,
            messages=[chat_prompt("user", "hi")],
            message_factory=message_factory,
        )
    )

    assert completion.identifiers() == ["main", "chatHistory"]
    assert "bias" not in completion.flat_names()


def test_assembly_control_prompts_carry_impersonate_and_quiet_prompt():
    """openai.js:1224-1236 -- ``impersonate`` only for ``type === 'impersonate'``."""
    entries = [
        {"identifier": "main", "system_prompt": True, "role": "system", "content": "MAIN"},
        {"identifier": "impersonate", "system_prompt": True, "role": "system", "content": "IMP"},
        {"identifier": "quietPrompt", "system_prompt": True, "role": "system", "content": "QUIET"},
        {"identifier": "chatHistory"},
    ]
    completion = FakeChatCompletion()

    run(
        populate_chat_completion(
            PromptCollection(*entries),
            completion,
            type="impersonate",
            quiet_prompt="QUIET",
            messages=[chat_prompt("user", "hi")],
            message_factory=message_factory,
        )
    )

    control = only(completion, "controlPrompts")
    assert [message.content for message in control.flatten()] == ["IMP", "QUIET"]
    # Both are reserved up front (openai.js:1238) and freed before the add.
    assert control in completion.reserved
    assert control in completion.freed
    assert completion.identifiers() == ["main", "chatHistory", "controlPrompts"]


def test_assembly_bias_only_when_the_text_is_non_empty():
    entries = [
        {"identifier": "main", "system_prompt": True, "role": "system", "content": "MAIN"},
        {"identifier": "bias", "system_prompt": True, "role": "assistant", "content": "BIAS"},
        {"identifier": "chatHistory"},
    ]

    blank = FakeChatCompletion()
    run(
        populate_chat_completion(
            PromptCollection(*entries),
            blank,
            bias="   ",
            messages=[chat_prompt("user", "hi")],
            message_factory=message_factory,
        )
    )
    assert "bias" not in blank.flat_names()

    present = FakeChatCompletion()
    run(
        populate_chat_completion(
            PromptCollection(*entries),
            present,
            bias="BIAS",
            messages=[chat_prompt("user", "hi")],
            message_factory=message_factory,
        )
    )
    bias = only(present, "bias")
    assert bias.collection[-1].role == "assistant"


def test_assembly_nsfw_and_jailbreak_always_precede_user_prompts():
    entries = [
        {"identifier": "nsfw", "system_prompt": True, "role": "system", "content": "NSFW"},
        {"identifier": "jailbreak", "system_prompt": True, "role": "system", "content": "JB"},
        {"identifier": "customA", "role": "user", "content": "A", "system_prompt": False},
        {"identifier": "customB", "role": "user", "content": "B", "system_prompt": False},
        {"identifier": "chatHistory"},
    ]
    completion = FakeChatCompletion()

    run(
        populate_chat_completion(
            PromptCollection(*entries), completion, messages=[], message_factory=message_factory
        )
    )

    assert completion.identifiers() == ["nsfw", "jailbreak", "customA", "customB", "chatHistory"]


def test_assembly_relative_prompt_is_inserted_next_to_main():
    """openai.js:1268 -- ``position`` is honoured when ``main`` is in the chat."""
    entries = [
        {"identifier": "main", "system_prompt": True, "role": "system", "content": "MAIN"},
        {
            "identifier": "authorsNote",
            "system_prompt": True,
            "role": "system",
            "content": "AN",
            "position": "start",
        },
        {
            "identifier": "summary",
            "system_prompt": True,
            "role": "system",
            "content": "SUM",
            "position": "end",
        },
        {"identifier": "chatHistory"},
    ]
    completion = FakeChatCompletion()

    run(
        populate_chat_completion(
            PromptCollection(*entries), completion, messages=[], message_factory=message_factory
        )
    )

    # Both relative prompts land *inside* the 'main' group: 'start' in front,
    # 'end' behind (openai.js:1268 -> MessageCollection.insert).
    main = only(completion, "main")
    assert [message.content for message in main.flatten()] == ["AN", "MAIN", "SUM"]
    assert completion.identifiers() == ["main", "chatHistory"]


def test_assembly_relative_prompt_without_main_becomes_an_injection():
    """openai.js:1269-1283 -- copied role/depth/order, inserted into ``absolutePrompts``."""
    entries = [
        {
            "identifier": "main",
            "role": "user",
            "content": "MAIN",
            "injection_position": 1,
            "injection_depth": 0,
            "injection_order": 100,
        },
        {
            "identifier": "authorsNote",
            "system_prompt": True,
            "role": "system",
            "content": "AN",
            "position": "start",
        },
        {"identifier": "chatHistory"},
    ]
    completion = FakeChatCompletion()

    run(
        populate_chat_completion(
            PromptCollection(*entries),
            completion,
            messages=[chat_prompt("user", "hi")],
            message_factory=message_factory,
        )
    )

    # 'main' is absolute, so it never enters the ordered chat; the note is
    # promoted into the same injection bucket (role copied from main) and lands
    # before the history because the injection list is reversed at the end.
    assert completion.identifiers() == ["chatHistory"]
    assert completion.flat_names() == ["newMainChat", "chatHistory-1", "chatHistory-2"]
    # The promoted prompts are merged per role, newest-first like every other
    # injected block (openai.js:849-861,873).
    assert [message["content"] for message in chat(completion)][1:] == ["hi", "AN\nMAIN"]


def test_assembly_absolute_prompts_are_not_added_in_order():
    """openai.js:1198-1201 -- absolute prompts are skipped by ``addToChatCompletion``."""
    entries = [
        {"identifier": "main", "system_prompt": True, "role": "system", "content": "MAIN"},
        {
            "identifier": "injected",
            "role": "system",
            "content": "INJ",
            "injection_position": 1,
            "injection_depth": 0,
            "injection_order": 100,
        },
        {"identifier": "chatHistory"},
    ]
    completion = FakeChatCompletion()

    run(
        populate_chat_completion(
            PromptCollection(*entries),
            completion,
            messages=[chat_prompt("user", "hi")],
            message_factory=message_factory,
        )
    )

    assert completion.identifiers() == ["main", "chatHistory"]
    assert completion.flat_names() == ["main", "newMainChat", "chatHistory-1", "chatHistory-2"]


def test_assembly_pin_examples_puts_examples_before_the_history():
    """openai.js:1336-1343."""
    entries = [{"identifier": "main", "system_prompt": True, "role": "system", "content": "MAIN"}]
    examples = [[{"role": "user", "content": "e", "name": "User"}]]

    unpinned = FakeChatCompletion()
    run(
        populate_chat_completion(
            collection_with_history(entries),
            unpinned,
            messages=[chat_prompt("user", "hi")],
            message_examples=examples,
            message_factory=message_factory,
        )
    )
    assert unpinned.identifiers() == ["main", "chatHistory", "dialogueExamples"]
    assert unpinned.flat_names() == [
        "main",
        "newMainChat",
        "chatHistory-1",
        "newChat",
        "dialogueExamples 0-0@User",
    ]

    pinned = FakeChatCompletion()
    run(
        populate_chat_completion(
            collection_with_history(entries),
            pinned,
            messages=[chat_prompt("user", "hi")],
            message_examples=examples,
            pin_examples=True,
            message_factory=message_factory,
        )
    )
    assert pinned.identifiers() == ["main", "chatHistory", "dialogueExamples"]
    assert pinned.flat_names() == [
        "main",
        "newMainChat",
        "chatHistory-1",
        "newChat",
        "dialogueExamples 0-0@User",
    ]


def test_assembly_skips_prompts_disabled_for_the_active_character():
    """openai.js:1191-1194 -- ``main`` is exempt from the check."""
    entries = [
        {"identifier": "main", "system_prompt": True, "role": "system", "content": "MAIN"},
        {"identifier": "nsfw", "system_prompt": True, "role": "system", "content": "NSFW"},
        {"identifier": "chatHistory"},
    ]
    completion = FakeChatCompletion()

    run(
        populate_chat_completion(
            PromptCollection(*entries),
            completion,
            messages=[],
            is_prompt_disabled_for_active_character=lambda identifier: identifier == "nsfw",
            message_factory=message_factory,
        )
    )

    assert completion.identifiers() == ["main", "chatHistory"]


def test_assembly_forwards_overridden_prompts():
    """openai.js:1221 -- ``setOverriddenPrompts(prompts.overriddenPrompts)``."""
    collection = PromptCollection(
        {"identifier": "main", "system_prompt": True, "role": "system", "content": "MAIN"},
        {"identifier": "chatHistory"},
    )
    collection.override(
        {"identifier": "main", "system_prompt": True, "role": "system", "content": "MAIN2"}, 0
    )
    completion = FakeChatCompletion()

    run(
        populate_chat_completion(
            collection, completion, messages=[], message_factory=message_factory
        )
    )

    assert completion.overridden_prompts == ["main"]


def test_assembly_continue_prefill_displaces_the_first_message():
    """openai.js:1320-1331 -- Claude only, and only with ``continue_prefill``."""
    entries = [
        {"identifier": "main", "system_prompt": True, "role": "system", "content": "MAIN"},
        {"identifier": "chatHistory"},
    ]
    messages = [chat_prompt("assistant", "tail"), chat_prompt("user", "more")]
    completion = FakeChatCompletion()

    run(
        populate_chat_completion(
            PromptCollection(*entries),
            completion,
            type="continue",
            messages=messages,
            settings={
                "continue_prefill": True,
                "chat_completion_source": "claude",
                "assistant_prefill": "PRE",
            },
            message_factory=message_factory,
        )
    )

    control = only(completion, "controlPrompts")
    prefill = control.flatten()[0]
    assert prefill.identifier == "continuePrefill"
    assert prefill.content == "PRE\n\ntail"
    assert prefill.role == "assistant"
    assert completion.flat_names()[-3:] == ["newMainChat", "chatHistory-1", "continuePrefill"]
    assert messages == [chat_prompt("user", "more")]


def test_assembly_continue_prefill_is_not_applied_for_other_sources():
    entries = [
        {"identifier": "main", "system_prompt": True, "role": "system", "content": "MAIN"},
        {"identifier": "chatHistory"},
    ]
    completion = FakeChatCompletion()

    run(
        populate_chat_completion(
            PromptCollection(*entries),
            completion,
            type="continue",
            cycle_prompt="",
            messages=[chat_prompt("assistant", "tail")],
            settings={"continue_prefill": True, "chat_completion_source": "openai"},
            message_factory=message_factory,
        )
    )

    prefill = only(completion, "controlPrompts").flatten()[0]
    assert prefill.content == "tail"


# ---------------------------------------------------------------------------
# preparePromptsForChatCompletion -- openai.js:1367-1516
# ---------------------------------------------------------------------------


def test_prepare_default_system_prompt_order():
    """openai.js:1373-1386 -- the append order of the nine system entries.

    The default collection (``PROMPT_COLLECTION_IDENTIFIERS``) already contains
    every marker, so nothing is appended twice and the returned order is the
    canonical one -- which is exactly the default SillyTavern preset order.
    """
    collection = run(
        prepare_prompts_for_chat_completion(
            world_info_before="WIB",
            world_info_after="WIA",
            char_description="DESC",
            char_personality="PERS",
            scenario="SCEN",
            quiet_prompt="QUIET",
            bias="BIAS",
        )
    )

    assert [prompt.identifier for prompt in collection.collection][:10] == [
        "main",
        "worldInfoBefore",
        "worldInfoAfter",
        "charDescription",
        "charPersonality",
        "scenario",
        "personaDescription",
        "impersonate",
        "quietPrompt",
        "groupNudge",
    ]
    # Every entry that carries a role is a system prompt except 'bias', which is
    # the only assistant-role member of the nine (openai.js:1385). Entries with
    # no role at all are the preset-only markers (main, personaDescription).
    roles = [prompt.role for prompt in collection.collection if prompt.role]
    assert roles[:8] == ["system"] * 8
    assert set(roles) == {"system", "assistant"}
    assert collection.get("bias").role == "assistant"
    assert collection.get("charDescription").content == "DESC"


def test_prepare_appends_the_markers_of_an_empty_collection():
    """The nine markers, in the reference order, when the preset has none."""
    collection = run(
        prepare_prompts_for_chat_completion(
            prompts=PromptCollection(),
            quiet_prompt="Q",
            char_description="D",
        )
    )

    assert [prompt.identifier for prompt in collection.collection] == [
        "worldInfoBefore",
        "worldInfoAfter",
        "charDescription",
        "charPersonality",
        "scenario",
        "impersonate",
        "quietPrompt",
        "groupNudge",
        "bias",
    ]


def test_prepare_prompt_fields_for_injection():
    """The fields ``prompts[]`` must expose for the injection pass."""
    collection = run(
        prepare_prompts_for_chat_completion(
            prompts=PromptCollection(
                {
                    "identifier": "quietPrompt",
                    "injection_position": 1,
                    "injection_depth": 3,
                    "injection_order": 42,
                    "role": "user",
                }
            ),
            quiet_prompt="QUIET",
        )
    )

    quiet = collection.get("quietPrompt")
    assert quiet is not None
    assert (quiet.identifier, quiet.role, quiet.content) == ("quietPrompt", "user", "QUIET")
    assert (quiet.injection_position, quiet.injection_depth, quiet.injection_order) == (1, 3, 42)


def test_prepare_marker_configuration_does_not_leak_into_the_next_run():
    """``getPromptCollection`` rebuilds every call in the reference (1470)."""
    source = PromptCollection({"identifier": "main", "system_prompt": True, "injection_depth": 7})
    first = run(prepare_prompts_for_chat_completion(prompts=source, char_description="A"))
    second = run(prepare_prompts_for_chat_completion(prompts=source, char_description="A"))

    assert first.get("main").injection_depth == 7
    assert second.get("main").injection_depth == 7
    assert source.get("main").content is None


def test_prepare_extension_prompt_identifiers_positions_and_roles():
    extension_prompts = {
        "1_memory": {"value": "SUM", "role": 1, "position": 0},
        "2_floating_prompt": {"value": "AN", "role": 0, "position": 1},
        "3_vectors": {"value": "VM", "role": 2, "position": 0},
        "4_vectors_data_bank": {"value": "VDB", "role": 2, "position": 1},
        "chromadb": {"value": "SC", "position": 0},
    }

    collection = run(
        prepare_prompts_for_chat_completion(
            prompts=PromptCollection(), extension_prompts=extension_prompts
        )
    )

    assert [
        (prompt.identifier, prompt.role, prompt.content, prompt.position)
        for prompt in collection.collection
    ][-5:] == [
        ("summary", "user", "SUM", "start"),
        ("authorsNote", "system", "AN", "end"),
        ("vectorsMemory", "system", "VM", "start"),
        ("vectorsDataBank", "assistant", "VDB", "end"),
        ("smartContext", "system", "SC", "start"),
    ]


def test_prepare_extension_prompt_without_a_value_is_dropped():
    collection = run(
        prepare_prompts_for_chat_completion(
            prompts=PromptCollection(),
            extension_prompts={"1_memory": {"value": "", "role": 0, "position": 0}},
        )
    )

    assert collection.get("summary") is None


def test_prepare_unknown_extension_prompt_gets_a_sanitized_identifier():
    """openai.js:1449-1466 -- ``key.replace(/\\W/g, '_')`` and ``extension: true``."""
    extension_prompts = {
        "my-ext prompt": {"value": "X", "role": 1, "position": 1},
        "in-chat-only": {"value": "Y", "role": 0, "position": 2},
        "no-value": {"value": "", "role": 0, "position": 0},
        "filtered-out": {"value": "Z", "role": 0, "position": 0, "filter": lambda: False},
    }

    collection = run(
        prepare_prompts_for_chat_completion(
            prompts=PromptCollection(), extension_prompts=extension_prompts
        )
    )

    unknown = [prompt for prompt in collection.collection if prompt.extension]
    assert [(prompt.identifier, prompt.role, prompt.content) for prompt in unknown] == [
        ("my_ext_prompt", "user", "X"),
    ]


def test_prepare_persona_description_needs_the_in_prompt_position():
    in_prompt = run(
        prepare_prompts_for_chat_completion(
            prompts=PromptCollection(),
            persona_description="PERSONA",
            persona_description_position=1,
        )
    )
    assert in_prompt.get("personaDescription").content == "PERSONA"

    elsewhere = run(
        prepare_prompts_for_chat_completion(
            prompts=PromptCollection(),
            persona_description="PERSONA",
            persona_description_position=0,
        )
    )
    assert elsewhere.get("personaDescription") is None


def test_prepare_keeps_the_marker_position_of_the_preset():
    """openai.js:1489-1492 -- replace in place, or append."""
    preset = PromptCollection(
        {"identifier": "main"},
        {"identifier": "custom"},
        {"identifier": "charDescription"},
    )

    collection = run(
        prepare_prompts_for_chat_completion(
            prompts=preset, char_description="DESC", scenario="SCEN"
        )
    )

    assert [prompt.identifier for prompt in collection.collection] == [
        "main",
        "custom",
        "charDescription",
        "worldInfoBefore",
        "worldInfoAfter",
        "charPersonality",
        "scenario",
        "impersonate",
        "quietPrompt",
        "groupNudge",
        "bias",
    ]
    assert collection.get("charDescription").content == "DESC"


def test_prepare_applies_the_format_overrides():
    collection = run(
        prepare_prompts_for_chat_completion(
            prompts=PromptCollection(),
            settings={"scenario_format": "SCENARIO: {0}", "personality_format": "PERS: {0}"},
            scenario="RAW",
            char_personality="RAWP",
        )
    )

    assert collection.get("scenario").content == "SCENARIO: {0}"
    assert collection.get("charPersonality").content == "PERS: {0}"


def test_prepare_system_prompt_override_records_the_original():
    """openai.js:1495-1503 -- ``preparePrompt(prompt, mainOriginalContent)``."""
    seen: list[Any] = []

    def prepare_prompt(prompt, *args):
        seen.append((prompt.identifier, prompt.content, args))
        return prompt

    collection = run(
        prepare_prompts_for_chat_completion(
            prompts=PromptCollection(
                {"identifier": "main", "system_prompt": True, "content": "OLD"}
            ),
            system_prompt_override="NEW",
            prepare_prompt=prepare_prompt,
        )
    )

    assert collection.get("main").content == "NEW"
    assert collection.overridden_prompts == ["main"]
    assert ("main", "NEW", ("OLD",)) in seen


def test_prepare_system_prompt_override_respects_forbid_overrides():
    collection = run(
        prepare_prompts_for_chat_completion(
            prompts=PromptCollection(
                {
                    "identifier": "main",
                    "system_prompt": True,
                    "content": "OLD",
                    "forbid_overrides": True,
                }
            ),
            system_prompt_override="NEW",
        )
    )

    assert collection.get("main").content == "OLD"
    assert collection.overridden_prompts == []


def test_prepare_jailbreak_override():
    collection = run(
        prepare_prompts_for_chat_completion(
            prompts=PromptCollection(
                {"identifier": "jailbreak", "system_prompt": True, "content": "OLD-JB"}
            ),
            jailbreak_prompt_override="NEW-JB",
        )
    )

    assert collection.get("jailbreak").content == "NEW-JB"
    assert collection.overridden_prompts == ["jailbreak"]


def test_prepare_respects_a_disabled_main_prompt():
    collection = run(
        prepare_prompts_for_chat_completion(
            prompts=PromptCollection(
                {"identifier": "main", "system_prompt": True, "content": "OLD"}
            ),
            system_prompt_override="NEW",
            is_prompt_disabled_for_active_character=lambda identifier: identifier == "main",
        )
    )

    assert collection.get("main").content == "OLD"
    assert collection.overridden_prompts == []


def test_prepare_uses_the_injected_prompt_collection_callback():
    def get_prompt_collection(type):  # noqa: A002 - mirrors the JS signature
        return PromptCollection(
            {"identifier": "main", "system_prompt": True, "content": f"MAIN-{type}"}
        )

    collection = run(
        prepare_prompts_for_chat_completion(
            prompts=PromptCollection(), type="continue", get_prompt_collection=get_prompt_collection
        )
    )

    assert collection.get("main").content == "MAIN-continue"
    assert collection.get("worldInfoBefore") is not None


# ---------------------------------------------------------------------------
# ChatCompletion integration -- uses the real frozen interface.
# ---------------------------------------------------------------------------


def test_real_chat_completion_accepts_the_whole_pipeline():
    """Smoke test against :class:`tavern.st.chat_completion.ChatCompletion`."""
    completion = ChatCompletion()
    completion.set_token_budget(context=4096, response=256)

    prompts = run(
        prepare_prompts_for_chat_completion(
            char_description="Iris is a lighthouse keeper.",
            scenario="A storm is coming.",
            world_info_before="WI",
            prompts=PromptCollection(
                {"identifier": "main", "system_prompt": True, "content": "Write as {{char}}."},
                {"identifier": "chatHistory"},
                {"identifier": "dialogueExamples"},
            ),
        )
    )

    run(
        populate_chat_completion(
            prompts,
            completion,
            messages=[chat_prompt("user", "hello"), chat_prompt("assistant", "hi")],
            message_examples=[],
        )
    )

    assert completion.has("main")
    assert completion.has("chatHistory")
    chat = completion.get_chat()
    assert chat
    assert all(message.get("role") for message in chat)


def test_real_chat_completion_squash_keeps_the_new_main_chat():
    """The ``newMainChat`` identifier stays in the squash exclusion list."""
    completion = ChatCompletion()
    completion.set_token_budget(context=4096, response=256)
    prompts = run(
        prepare_prompts_for_chat_completion(
            prompts=PromptCollection(
                {"identifier": "main", "system_prompt": True, "content": "MAIN"},
                {"identifier": "chatHistory"},
            )
        )
    )

    run(populate_chat_completion(prompts, completion, messages=[]))
    completion.squash_system_messages()

    assert completion.has("main")
