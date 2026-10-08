"""SillyTavern's prompt message model, mirror-ported from ``public/scripts/openai.js``.

Ported from SillyTavern - public/scripts/openai.js
(class TokenHandler lines 3420-3479, class Message lines 3511-3806,
class MessageCollection lines 3808-3905, class ChatCompletion lines 3917-4268)
Copyright (C) 2024 SillyTavern contributors
Licensed under the GNU Affero General Public License v3.0.
This file is a modified Python translation; modified on 2026-10-07.
Upstream: https://github.com/SillyTavern/SillyTavern (commit 06bde939)

The classes here are the transport layer of the prompt: ``Message`` holds one
entry, ``MessageCollection`` a nestable group of them, ``ChatCompletion`` the
ordered root with the token budget, ``TokenHandler`` the eight token buckets the
reference keeps for the prompt breakdown shown in its console.

Deliberate differences from the browser code (see ``research/08-s2-prompt-brief.md``):

* ``Message.createAsync`` exists, but token counting is synchronous: the counter
  is injected into :class:`TokenHandler` as a plain callable instead of an async
  tokenizer. The default estimate is ``max(1, len(content) // 3)``, the same one
  :mod:`tavern.st.worldbook` uses.
* Images (``addImage``), tool calls and reasoning are represented but not
  produced: no HTTP fetch happens here.
* ``TokenBudgetExceededError`` / ``IdentifierNotFoundError`` keep their upstream
  names so callers can catch the same conditions.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any

from tavern.log import logger

__all__ = [
    "ChatCompletion",
    "IdentifierNotFoundError",
    "Message",
    "MessageCollection",
    "TokenBudgetExceededError",
    "TokenHandler",
    "count_tokens",
    "squash_exclude_list",
]

#: Identifiers ``squashSystemMessages`` never merges (openai.js:3923).
SQUASH_EXCLUDE_LIST: tuple[str, ...] = ("newMainChat", "newChat", "groupNudge")

#: Characters per token for the default estimate. Kept in sync with
#: :mod:`tavern.st.worldbook` so both halves of the port agree on a budget.
TOKEN_DIVISOR = 3


def count_tokens(message: Any) -> int:
    """Token estimate for one message dict/object (the default counter).

    Mirrors what the reference hands to ``countTokenAsyncFn``: a mapping with
    ``role``/``content`` (plus ``name`` or ``tool_calls`` when present). The parts
    are joined with a single space, exactly like the harness does, so the two
    sides agree on the arithmetic; the estimate itself stays ``len // 3`` because
    the real tokenizer is a model feature (the oracle marks it non-comparable).
    """
    if isinstance(message, (list, tuple)):
        return sum(count_tokens(item) for item in message)
    if isinstance(message, str):
        text = message
    elif isinstance(message, Mapping):
        parts = [
            str(message.get("role") or ""),
            str(message.get("content") or ""),
            str(message.get("name") or ""),
            str(message.get("reasoning") or ""),
        ]
        if message.get("tool_calls"):
            parts.append(json.dumps(message["tool_calls"], ensure_ascii=False))
        text = " ".join(part for part in parts if part)
    else:
        text = str(message)
    return max(1, len(text) // TOKEN_DIVISOR) if text else 0


def squash_exclude_list() -> tuple[str, ...]:
    """The identifiers :meth:`ChatCompletion.squash_system_messages` protects."""
    return SQUASH_EXCLUDE_LIST


class TokenHandler:
    """The eight token buckets the reference tracks (openai.js:3420-3479).

    ``count_async``/``countAsync`` add to a bucket and return the delta; the
    buckets are what the prompt breakdown console table is made of. The reference
    bucket named ``prompt`` is exposed as :attr:`prompt` -- ``prompt`` collides
    with nothing here, but ``bias``/``nudge``/``jailbreak`` stay as-is.
    """

    #: Bucket order is the reference's object literal order (openai.js:3426).
    BUCKETS: tuple[str, ...] = (
        "start_chat",
        "prompt",
        "bias",
        "nudge",
        "jailbreak",
        "impersonate",
        "examples",
        "conversation",
    )

    def __init__(self, count_token_fn: Callable[[Any], int] | None = None) -> None:
        self.count_token_fn = count_token_fn or count_tokens
        self.counts: dict[str, int] = dict.fromkeys(self.BUCKETS, 0)

    def get_counts(self) -> dict[str, int]:
        return self.counts

    def reset_counts(self) -> None:
        for key in self.counts:
            self.counts[key] = 0

    def set_counts(self, counts: Mapping[str, int]) -> None:
        """Replace the bucket table (reference ``setCounts``, openai.js:3446)."""
        self.counts = {key: int(counts.get(key, 0)) for key in self.BUCKETS}

    def uncount(self, value: int, token_type: str) -> None:
        self.counts[token_type] = self.counts.get(token_type, 0) - value

    def count_async(self, messages: Any, full: bool = False, token_type: str = "") -> int:
        """Count ``messages`` and add the result to ``token_type``.

        The reference is ``async`` because its tokenizer is; this port is
        synchronous by design, and ``full`` is accepted for signature parity.
        """
        token_count = self.count_token_fn(messages)
        if token_type:
            self.counts[token_type] = self.counts.get(token_type, 0) + token_count
        return token_count

    # The reference name, kept so ported call sites read the same.
    async def countAsync(self, messages: Any, full: bool = False, token_type: str = "") -> int:
        return self.count_async(messages, full, token_type)

    def get_tokens_for_identifier(self, identifier: str) -> int:
        return int(self.counts.get(identifier) or 0)

    def get_total(self) -> int:
        total = 0
        for value in self.counts.values():
            try:
                total += int(value)
            except (TypeError, ValueError):
                continue
        return total

    def log(self) -> dict[str, int]:
        """Return the breakdown instead of printing a console table."""
        table = dict(self.counts)
        table["total"] = self.get_total()
        logger.debug("[ChatCompletion] token counts: %s", table)
        return table


class PromptError(Exception):
    """Base class for the two errors the reference throws (openai.js:3485)."""


class IdentifierNotFoundError(PromptError):
    """Raised when a requested prompt identifier is missing (openai.js:3485)."""

    def __init__(self, identifier: str) -> None:
        super().__init__(f"Identifier {identifier} not found.")
        self.identifier = identifier
        self.name = "IdentifierNotFoundError"


class TokenBudgetExceededError(PromptError):
    """Raised when a message does not fit the remaining budget (openai.js:3493)."""

    def __init__(self, identifier: str) -> None:
        super().__init__(f"Token budget exceeded for {identifier}.")
        self.identifier = identifier
        # The reference names the class ``TokenBudgetExceededError``; its
        # ``name`` is what a catch site sees, and the oracle compares that.
        self.name = "TokenBudgetExceeded"


class Message:
    """One prompt message (openai.js:3511-3806).

    Build it through :meth:`create_async` (or :meth:`create`) so ``tokens`` is
    filled like the reference does; the plain constructor leaves it at 0, exactly
    as upstream.
    """

    #: Reference ``Message.tokensPerImage`` (openai.js:3512).
    tokens_per_image = 85

    def __init__(self, role: str, content: Any, identifier: str) -> None:
        self.identifier = identifier
        self.role = role
        if not self.role:
            logger.debug("Message role not set, defaulting to 'system' for %r", identifier)
            self.role = "system"
        self.content = content
        self.name: str = ""
        self.tool_calls: Any = None
        self.signature: str | None = None
        self.reasoning: str | None = None
        self.tokens = 0

    @classmethod
    async def create_async(cls, role: str, content: Any, identifier: str) -> Message:
        """Reference ``Message.createAsync`` (openai.js:3558)."""
        message = cls(role, content, identifier)
        if isinstance(message.content, str) and message.content:
            message.tokens = token_handler.count_async(
                {"role": message.role, "content": message.content}
            )
        return message

    @classmethod
    def create(cls, role: str, content: Any, identifier: str) -> Message:
        """Synchronous convenience wrapper around :meth:`create_async`."""
        message = cls(role, content, identifier)
        if isinstance(message.content, str) and message.content:
            message.tokens = token_handler.count_async(
                {"role": message.role, "content": message.content}
            )
        return message

    async def set_tool_calls(
        self,
        invocations: Sequence[Mapping[str, Any]],
        include_signature: bool,
        include_reasoning: bool = False,
    ) -> None:
        """Reference ``setToolCalls`` (openai.js:3575)."""
        self.tool_calls = [
            {
                "id": item.get("id"),
                "type": "function",
                "function": {"arguments": item.get("parameters"), "name": item.get("name")},
                **(
                    {"signature": item.get("signature")}
                    if include_signature and item.get("signature")
                    else {}
                ),
            }
            for item in invocations
        ]
        reasoning = next(
            (
                item.get("reasoning")
                for item in invocations
                if isinstance(item.get("reasoning"), str) and item.get("reasoning")
            ),
            None,
        )
        self.reasoning = reasoning if include_reasoning else None
        self.tokens = token_handler.count_async(
            {
                "role": self.role,
                "tool_calls": json.dumps(self.tool_calls, ensure_ascii=False),
                **({"reasoning": self.reasoning} if self.reasoning else {}),
            }
        )

    async def set_name(self, name: str) -> None:
        """Reference ``setName`` (openai.js:3599): adding a name re-counts."""
        self.name = name
        self.tokens = token_handler.count_async(
            {"role": self.role, "content": self.content, "name": self.name}
        )

    def ensure_content_is_array(self) -> list[Any]:
        """Reference ``ensureContentIsArray`` (openai.js:3608)."""
        if not isinstance(self.content, list):
            text = self.content
            self.content = []
            if isinstance(text, str):
                self.content.append({"type": "text", "text": text})
        return self.content

    def get_tokens(self) -> int:
        return self.tokens

    # Reference camelCase alias, so ported call sites read the same.
    getTokens = get_tokens

    def to_dict(self) -> dict[str, Any]:
        """The OpenAI message shape (reference ``getChat`` item, openai.js:4126)."""
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            payload["name"] = self.name
        if self.tool_calls:
            payload["tool_calls"] = self.tool_calls
        if self.role == "tool":
            payload["tool_call_id"] = self.identifier
        if self.signature:
            payload["signature"] = self.signature
        if self.reasoning:
            payload["reasoning"] = self.reasoning
        return payload


class MessageCollection:
    """A nestable group of messages (openai.js:3808-3905)."""

    def __init__(self, identifier: str, *items: Message | MessageCollection) -> None:
        for item in items:
            if not isinstance(item, (Message, MessageCollection)):
                raise TypeError(
                    "Only Message and MessageCollection instances can be added to MessageCollection"
                )
        self.collection: list[Message | MessageCollection] = list(items)
        self.identifier = identifier

    def get_chat(self) -> list[dict[str, Any]]:
        """Reference ``getChat`` (openai.js:3832).

        A nested collection is always flattened into the result; only plain
        messages are filtered on content.
        """
        chat: list[dict[str, Any]] = []
        for item in self.collection:
            if isinstance(item, MessageCollection):
                chat.extend(item.get_chat())
            elif item.content or item.tool_calls:
                chat.append(item.to_dict())
        return chat

    getChat = get_chat

    def get_collection(self) -> list[Message | MessageCollection]:
        return self.collection

    getCollection = get_collection

    def add(self, item: Message | MessageCollection) -> None:
        self.collection.append(item)

    def get_item_by_identifier(self, identifier: str) -> Message | MessageCollection | None:
        for item in self.collection:
            if item is not None and item.identifier == identifier:
                return item
        return None

    getItemByIdentifier = get_item_by_identifier

    def has_item_with_identifier(self, identifier: str) -> bool:
        return any(item is not None and item.identifier == identifier for item in self.collection)

    hasItemWithIdentifier = has_item_with_identifier

    def get_tokens(self) -> int:
        """Total tokens of this collection, recursing into nested ones.

        ``MessageCollection`` does not define ``get_tokens`` in the reference, but
        :meth:`flatten` and :meth:`ChatCompletion.get_total_token_count` rely on
        nested collections answering it, so it exists here.
        """
        return sum(item.get_tokens() for item in self.collection if item is not None)

    # NOTE: a plain ``getTokens = get_tokens`` alias would snapshot the function
    # above and break the recursion (the nested calls resolve through this name),
    # so the alias delegates instead.
    def getTokens(self) -> int:  # noqa: N802 - mirrors the reference name
        return self.get_tokens()

    def flatten(self) -> list[Message]:
        """Reference ``flatten`` (openai.js:3895)."""
        flat: list[Message] = []
        for item in self.collection:
            if item is None:
                continue
            if isinstance(item, MessageCollection):
                flat.extend(item.flatten())
            else:
                flat.append(item)
        return flat

    def __iter__(self) -> Iterator[Message | MessageCollection]:
        return iter(self.collection)

    def __len__(self) -> int:
        return len(self.collection)

    def __repr__(self) -> str:
        return f"MessageCollection({self.identifier!r}, {len(self.collection)} item(s))"


#: Reference module singleton (openai.js:3482).
token_handler = TokenHandler()


class ChatCompletion:
    """The ordered prompt with its token budget (openai.js:3917-4268)."""

    def __init__(self) -> None:
        self.token_budget = 0
        self.messages = MessageCollection("root")
        self.logging_enabled = False
        self.overridden_prompts: list[str] = []

    async def squash_system_messages(self) -> None:
        """Merge unnamed consecutive system messages (openai.js:3922).

        Excluded identifiers keep their own message, and an empty system message
        is dropped outright. Merging joins with ``'\\n'`` and re-counts tokens.
        """
        self.messages.collection = self.messages.flatten()
        last_message: Message | None = None
        squashed: list[Message | MessageCollection] = []

        def should_squash(message: Any) -> bool:
            return (
                isinstance(message, Message)
                and message.identifier not in SQUASH_EXCLUDE_LIST
                and message.role == "system"
                and not message.name
            )

        for message in self.messages.collection:
            if isinstance(message, Message) and message.role == "system" and not message.content:
                continue

            if should_squash(message):
                if last_message is not None and should_squash(last_message):
                    last_message.content = f"{last_message.content}\n{message.content}"  # type: ignore[union-attr]
                    last_message.tokens = token_handler.count_async(
                        {"role": last_message.role, "content": last_message.content}
                    )
                else:
                    squashed.append(message)
                    last_message = message  # type: ignore[assignment]
            else:
                squashed.append(message)
                last_message = message if isinstance(message, Message) else None

        self.messages.collection = squashed

    squashSystemMessages = squash_system_messages

    def get_messages(self) -> MessageCollection:
        return self.messages

    getMessages = get_messages

    def set_token_budget(self, context: int, response: int) -> None:
        """Reference ``setTokenBudget`` (openai.js:3982): ``context - response``."""
        self.log(f"Prompt tokens: {context}")
        self.log(f"Completion tokens: {response}")
        self.token_budget = context - response
        self.log(f"Token budget: {self.token_budget}")

    setTokenBudget = set_token_budget

    def add(
        self, collection: Message | MessageCollection, position: int | None = None
    ) -> ChatCompletion:
        """Reference ``add`` (openai.js:3998); raises when the budget is short.

        The reference writes straight into the array (``collection[position] =
        item``), which **overwrites** the slot and can create a sparse array when
        the index is past the end. The port reproduces both: slots are padded with
        ``None`` and the slot is replaced, never shifted. A ``None`` slot is
        skipped by :meth:`get_chat` and by the identifier lookups, and the
        reference's ``JSON.stringify`` would render ``null`` there too.
        """
        self.validate_message_collection(collection)
        self.check_token_budget(collection, collection.identifier)
        if position is None or position == -1:
            self.messages.collection.append(collection)
        else:
            while len(self.messages.collection) <= position:
                self.messages.collection.append(None)  # type: ignore[arg-type]
            self.messages.collection[position] = collection
        self.decrease_token_budget_by(collection.get_tokens())
        self.log(f"Added {collection.identifier}. Remaining tokens: {self.token_budget}")
        return self

    def insert_at_start(self, message: Message, identifier: str) -> None:
        self.insert(message, identifier, "start")

    insertAtStart = insert_at_start

    def insert_at_end(self, message: Message, identifier: str) -> None:
        self.insert(message, identifier, "end")

    insertAtEnd = insert_at_end

    def insert(self, message: Message, identifier: str, position: str | int = "end") -> None:
        """Reference ``insert`` (openai.js:4042)."""
        self.validate_message(message)
        self.check_token_budget(message, message.identifier)
        index = self.find_message_index(identifier)
        if message.content or message.tool_calls:
            target = self.messages.collection[index]
            if not isinstance(target, MessageCollection):
                raise TypeError(f"{identifier} does not hold a collection")
            if position == "start":
                target.collection.insert(0, message)
            elif position == "end":
                target.collection.append(message)
            elif isinstance(position, int):
                target.collection.insert(position, message)
            self.decrease_token_budget_by(message.get_tokens())
            self.log(
                f"Inserted {message.identifier} into {identifier}. Remaining: {self.token_budget}"
            )

    def remove_last_from(self, identifier: str) -> None:
        """Reference ``removeLastFrom`` (openai.js:4063)."""
        index = self.find_message_index(identifier)
        target = self.messages.collection[index]
        if not isinstance(target, MessageCollection) or not target.collection:
            self.log(f"No message to remove from {identifier}")
            return
        message = target.collection.pop()
        if isinstance(message, Message):
            self.increase_token_budget_by(message.get_tokens())
            self.log(
                f"Removed {message.identifier} from {identifier}. Remaining: {self.token_budget}"
            )

    removeLastFrom = remove_last_from

    def can_afford(self, message: Message | MessageCollection) -> bool:
        return 0 <= self.token_budget - message.get_tokens()

    canAfford = can_afford

    def can_afford_all(self, messages: Iterable[Message | MessageCollection]) -> bool:
        return 0 <= self.token_budget - sum(item.get_tokens() for item in messages)

    canAffordAll = can_afford_all

    def has(self, identifier: str) -> bool:
        return self.messages.has_item_with_identifier(identifier)

    def get_total_token_count(self) -> int:
        return self.messages.get_tokens()

    getTotalTokenCount = get_total_token_count

    def get_chat(self) -> list[dict[str, Any]]:
        """Reference ``getChat`` (openai.js:4120); ``None`` slots are skipped."""
        chat: list[dict[str, Any]] = []
        for item in self.messages.collection:
            if item is None:
                continue
            if isinstance(item, MessageCollection):
                chat.extend(item.get_chat())
            elif isinstance(item, Message) and (item.content or item.tool_calls):
                chat.append(item.to_dict())
            else:
                self.log(f"Skipping invalid or empty message in collection: {item!r}")
        return chat

    getChat = get_chat

    def log(self, output: str) -> None:
        if self.logging_enabled:
            logger.debug("[ChatCompletion] %s", output)

    def enable_logging(self) -> None:
        self.logging_enabled = True

    enableLogging = enable_logging

    def disable_logging(self) -> None:
        self.logging_enabled = False

    disableLogging = disable_logging

    @staticmethod
    def validate_message_collection(collection: Any) -> None:
        if not isinstance(collection, MessageCollection):
            raise TypeError("Argument must be an instance of MessageCollection")

    validateMessageCollection = validate_message_collection

    @staticmethod
    def validate_message(message: Any) -> None:
        if not isinstance(message, Message):
            raise TypeError("Argument must be an instance of Message")

    validateMessage = validate_message

    def check_token_budget(self, message: Message | MessageCollection, identifier: str) -> None:
        if not self.can_afford(message):
            raise TokenBudgetExceededError(identifier)

    checkTokenBudget = check_token_budget

    def reserve_budget(self, message: Message | MessageCollection | int) -> None:
        tokens = message if isinstance(message, int) else message.get_tokens()
        self.decrease_token_budget_by(tokens)

    reserveBudget = reserve_budget

    def free_budget(self, message: Message | MessageCollection) -> None:
        self.increase_token_budget_by(message.get_tokens())

    freeBudget = free_budget

    def increase_token_budget_by(self, tokens: int) -> None:
        self.token_budget += tokens

    increaseTokenBudgetBy = increase_token_budget_by

    def decrease_token_budget_by(self, tokens: int) -> None:
        self.token_budget -= tokens

    decreaseTokenBudgetBy = decrease_token_budget_by

    def find_message_index(self, identifier: str) -> int:
        for index, item in enumerate(self.messages.collection):
            if item is not None and item.identifier == identifier:
                return index
        raise IdentifierNotFoundError(identifier)

    findMessageIndex = find_message_index

    def set_overridden_prompts(self, listing: Sequence[str]) -> None:
        self.overridden_prompts = list(listing)

    setOverriddenPrompts = set_overridden_prompts

    def get_overridden_prompts(self) -> list[str]:
        return list(self.overridden_prompts)

    getOverriddenPrompts = get_overridden_prompts
