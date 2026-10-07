"""SillyTavern provider prompt converters, mirror-ported from ``prompt-converters.js``.

Source of truth: ``research/_raw/st-src/prompt-converters.js`` (SillyTavern
1.19.0, commit ``06bde939fb1e9c4c8d8641d810f0a9165bce127``, byte identical to tag
``1.19.0``). Every function below is a line-by-line translation of the same-named
export at the line range named in its docstring; the line numbers are measured
against the snapshot in this repository, never copied from a brief.

Spectrum: this is the pure server-side half of the "run SillyTavern inside the
plugin" effort (porting plan step S4). It takes the assembled message array --
whatever :mod:`tavern.st.prompt_build` produced -- and reshapes it into the wire
format a given provider expects: Anthropic's Messaging API, Google's
``contents``/``system_instruction``, Cohere's ``chatHistory``, AI21, Mistral, xAI,
or a flat text-completion prompt. Nothing here talks to a model or touches the
network.

What differs from the reference, and why
---------------------------------------
* ``crypto.randomBytes`` (used only to hide media behind a token while merging,
  ``830-852``) becomes an injected ``random_bytes`` callable. The reference's
  token is unguessable by design, so the *value* is not part of the contract --
  only the round trip is. The oracle pins the generator so both sides agree.
* ``getConfigValue`` (``util.js:88-109``) is replaced by :func:`get_config_value`
  plus :func:`set_config`, a module-level mapping. The reference reads the server
  config file and the environment; a plugin has neither, so the caller supplies
  the values. The two overridable keys are ``promptPlaceholder`` and
  ``gemini.thoughtSignatures`` (read at ``:4`` and ``:34``) and
  ``mistral.enablePrefix`` (read at call time, ``:709``).
* ``_.get(config, key, default)`` (lodash) becomes a plain nested-dict lookup.
* The arguments are plain ``dict``/``list`` structures, exactly as the reference
  treats them. The reference mutates its arguments in place and several exports
  return nothing; this port does the same, because callers upstream depend on it.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from typing import Any

# ---------------------------------------------------------------------------
# Config -- the reference's getConfigValue (util.js:88-109)
# ---------------------------------------------------------------------------

#: Defaults for the keys this module reads. Kept as literals so a missing config
#: behaves exactly like the reference's `defaultValue` argument.
DEFAULT_CONFIG: dict[str, Any] = {
    "promptPlaceholder": "Let's get started.",
    "gemini.thoughtSignatures": True,
    "mistral.enablePrefix": False,
}

_config: dict[str, Any] = dict(DEFAULT_CONFIG)


def set_config(values: dict[str, Any] | None) -> None:
    """Replace the config the module reads (the reference reads a file instead).

    Called by the oracle before a fixture runs. Unknown keys are kept, so a
    fixture can add one the reference would have read from its config file.
    """
    _config.clear()
    _config.update(DEFAULT_CONFIG)
    if values:
        _config.update(values)


def get_config_value(key: str, default: Any = None, type_converter: str | None = None) -> Any:
    """``getConfigValue`` (``util.js:88-109``).

    The environment route (``keyToEnv``) is dropped: a plugin has no
    ``ST_*`` environment contract. The lodash ``_.get`` path becomes a dotted
    lookup so a fixture can set ``{"gemini": {"thoughtSignatures": false}}`` as
    well as the flat key, exactly as lodash would resolve both.
    """
    value = _lookup(key)
    if value is None:
        value = default

    if type_converter == "number":
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if type_converter == "boolean":
        # `toBoolean` (util.js): anything but the false-ish literals is true.
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in {"", "false", "0", "off", "null", "undefined"}
    return value


def _lookup(key: str) -> Any:
    """The config value for ``key``, read **literally**, exactly as the oracle does.

    This used to walk a dotted path and to accept a nested spelling. Both were
    wrong for this project, and neither was detectable through a fixture: the
    oracle's ``getConfigValue`` shim reads ``_convConfig[key]`` and nothing else,
    so a nested config like ``{"gemini": {"thoughtSignatures": false}}`` resolves
    on this side and stays ``None`` on the reference side. Every fixture happens to
    use the flat spelling, so the divergence sat there unpinned -- which is the
    failure mode the oracle exists to prevent, so it is removed rather than
    documented.

    A plugin's config is a flat bag of keys anyway; there is no config file with
    nested sections to mirror here.
    """
    return _config[key] if key in _config else None


#: ``PROMPT_PLACEHOLDER`` (``:4``) -- resolved once, like the reference's
#: module-scope ``const``. Re-resolved by :func:`set_config`.
def prompt_placeholder() -> str:
    return str(get_config_value("promptPlaceholder", "Let's get started."))


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"", "false", "0", "off", "none", "null"}


def _flag(name: str, default: bool = False) -> bool:
    return _to_bool(get_config_value(name, default, "boolean"))


# ``enableThoughtSignatures`` (:34).
def enable_thought_signatures() -> bool:
    return _flag("gemini.thoughtSignatures", True)


#: ``PROMPT_PROCESSING_TYPE`` (:15-26). ``CLAUDE`` is deprecated in favour of
#: ``MERGE`` and kept for parity -- callers still pass it.
PROMPT_PROCESSING_TYPE: dict[str, str] = {
    "NONE": "",
    "CLAUDE": "claude",
    "MERGE": "merge",
    "MERGE_TOOLS": "merge_tools",
    "SEMI": "semi",
    "SEMI_TOOLS": "semi_tools",
    "STRICT": "strict",
    "STRICT_TOOLS": "strict_tools",
    "SINGLE": "single",
}

#: ``REASONING_EFFORT`` (:6-13).
REASONING_EFFORT: dict[str, str] = {
    "auto": "auto",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "min": "min",
    "max": "max",
}

#: ``GEMINI_MEDIA_RESOLUTION`` (:29-32). ``auto`` is intentionally unmapped.
GEMINI_MEDIA_RESOLUTION: dict[str, str] = {
    "low": "media_resolution_low",
    "high": "media_resolution_high",
}


#: Injected byte source for `mergeMessages`' media token (:846). The reference
#: uses `crypto.randomBytes(32).toString('base64')`; the oracle pins this so both
#: sides produce the same token.
def _default_random_bytes(count: int) -> bytes:
    return b""


random_bytes: Callable[[int], bytes] = _default_random_bytes


def _media_token() -> str:
    import base64
    import secrets

    raw = random_bytes(32)
    if not raw:
        raw = secrets.token_bytes(32)
    return base64.b64encode(raw).decode("ascii")


def _json_compact(value: Any) -> str:
    """``JSON.stringify(value)`` (no indent, no spaces) for the stringify sites.

    ``convertClaudePrompt`` (``:129``) and ``convertClaudeMessages`` (``:360``)
    both stringify with the JS default, so the port has to emit the same
    separators -- ``json.dumps``' defaults add spaces after ``:`` and ``,``,
    which the oracle compares byte for byte.
    """
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _cache_control(ttl: Any) -> dict[str, Any]:
    """The ``cache_control`` object the caching helpers stamp.

    Every one of them writes a raw ``ttl`` -- ``{type: 'ephemeral', ttl}`` -- and
    ``ttl`` is ``undefined`` whenever the caller omitted it, which
    ``JSON.stringify`` drops. So an omitted ttl has to produce a dict *without*
    the key; writing ``None`` here would serialise as ``"ttl": null`` and diverge.
    """
    stamp: dict[str, Any] = {"type": "ephemeral"}
    if ttl is not None:
        stamp["ttl"] = ttl
    return stamp


# ---------------------------------------------------------------------------
# getPromptNames -- prompt-converters.js:49-58
# ---------------------------------------------------------------------------


class _GroupNamePredicate:
    """The method ``getPromptNames`` hands back as ``startsWithGroupName``.

    The reference returns an object literal whose ``startsWithGroupName`` reads
    ``this.groupNames`` (``prompt-converters.js:54-56``), so the predicate is a
    *method of the returned object*, not a free function. A plain closure would be
    quietly more forgiving: calling it detached throws in the reference and would
    succeed here. This shape keeps the contract, and ``contains`` lets the object
    double as its own ``this`` so the oracle can exercise either calling
    convention.
    """

    def __init__(self, group_names: list[str]) -> None:
        self.group_names = list(group_names)

    def __call__(self, message: Any, *rest: Any) -> bool:
        # Bind the receiver the way Python binds a method, from the left.
        if rest:
            receiver, message = message, rest[0]
            names = getattr(receiver, "group_names", None) or self.group_names
        else:
            names = self.group_names
        text = str(message)
        return any(text.startswith(f"{name}: ") for name in names)


# ---------------------------------------------------------------------------
# getPromptNames -- prompt-converters.js:49-58
# ---------------------------------------------------------------------------


def get_prompt_names(request: Any) -> dict[str, Any]:
    """``getPromptNames`` (``:49-58``). ``request`` is a mapping with the body keys.

    ``startsWithGroupName`` is a mapping key whose value is callable, exactly like
    the reference's object property; it also carries ``group_names`` so a caller
    can rebind it.
    """
    body = request.get("body") if isinstance(request, dict) else None
    body = body if isinstance(body, dict) else {}

    char_name = str(body.get("char_name") or "")
    user_name = str(body.get("user_name") or "")
    raw_group = body.get("group_names")
    group_names = [str(name) for name in raw_group] if isinstance(raw_group, list) else []

    predicate = _GroupNamePredicate(group_names)

    # Exactly the three data fields the reference's object literal carries; the
    # predicate is the fourth, and `group_names` lives on it rather than here, so
    # the recorded shape stays the reference's.
    return {
        "charName": char_name,
        "userName": user_name,
        "groupNames": group_names,
        "startsWithGroupName": predicate,
    }


# ---------------------------------------------------------------------------
# addAssistantPrefix -- prompt-converters.js:67-76
# ---------------------------------------------------------------------------


def add_assistant_prefix(prompt: list[Any], tools: Any, prop: str) -> list[Any]:
    """``addAssistantPrefix`` (``:67-76``).

    Sets ``prompt[-1][prop] = True`` when there are no tools in play and the last
    message is an assistant turn. ``tools`` may be omitted/null, matching the
    reference's ``Array.isArray(tools) && tools.length > 0`` guard.
    """
    if not prompt:
        return prompt

    has_any_tools = (isinstance(tools, list) and len(tools) > 0) or any(
        isinstance(item, dict) and item.get("role") == "tool" for item in prompt
    )
    if not has_any_tools and isinstance(prompt[-1], dict) and prompt[-1].get("role") == "assistant":
        prompt[-1][prop] = True
    return prompt


# ---------------------------------------------------------------------------
# convertClaudePrompt -- prompt-converters.js:120-186 (deprecated)
# ---------------------------------------------------------------------------


def convert_claude_prompt(
    messages: list[Any],
    add_assistant_postfix: Any = False,
    add_assistant_prefill: Any = "",
    with_sys_prompt_support: Any = False,
    use_system_prompt: Any = False,
    add_sys_human_msg: Any = "",
    exclude_prefixes: Any = False,
) -> str:
    """``convertClaudePrompt`` (``:120-186``) -- **deprecated**, kept for token counting.

    Translates the whole conversation into the single ``\\n\\nHuman: `` /
    ``\\n\\nAssistant: `` prompt string the legacy Claude completion endpoint ate.
    The reference is unused by the modern providers; the port keeps it because
    the token counter still calls it.

    Both ``messages`` and its ``content`` values are edited in place, like the
    reference: ``content`` is defaulted to ``''``, a ``tool_calls`` array is
    stringified onto the end of it, and the last-message role survives as
    ``'FixHumMsg'`` -- a role that exists only for the prefix table at ``:175-180``.
    """
    # messages.length > 0 (:123)
    if len(messages) > 0:
        # Prepare messages for claude (:124-131).
        for message in messages:
            # `if (!m.content) m.content = ''` -- falsy, so 0/None/'' all become ''.
            if not message.get("content"):
                message["content"] = ""
            if message.get("tool_calls"):
                # JS `+=` stringifies; `str` of a NaN/None never happens here
                # because the guard above made `content` a string.
                message["content"] += _json_compact(message["tool_calls"])

        if exclude_prefixes:
            # Everything but the last message becomes a system message (:132-134).
            for message in messages[:-1]:
                message["role"] = "system"
        else:
            # (:135) Direct assignment, so a missing key is created here.
            messages[0]["role"] = "system"

        # Add the assistant's message to the end of messages (:137-143).
        if add_assistant_postfix:
            messages.append({"role": "assistant", "content": add_assistant_prefill or ""})

        # Find the index of the first message with an assistant role and check for
        # a "'user' role/Human:" before it (:144-151). The callback's side effect
        # runs for the message it *stops on* too, hence `i > 0` rather than a
        # pre-check: that is how `hasUser` ever becomes true for a first message
        # that is already an assistant turn.
        has_user = False
        first_assistant_index = -1
        index = 0
        # `for` is wrong here: Python leaves the loop variable at the last value it
        # took, while the reference's `var i` is a counter, and `findIndex` stops
        # *on* the match. An index-driven while keeps both readings.
        while index < len(messages):
            message = messages[index]
            # `message.content.includes` would throw on a non-string; every path
            # above has already made it a string.
            if message.get("role") == "user" or "\n\nHuman: " in str(message.get("content")):
                has_user = True
            if message.get("role") == "assistant" and index > 0:
                first_assistant_index = index
                break
            index += 1

        # When 2.1+ and 'Use system prompt' checked, switches to the system prompt
        # format by setting the first message's role to 'system' (:154-161).
        if with_sys_prompt_support and use_system_prompt:
            messages[0]["role"] = "system"
            if first_assistant_index > 0 and add_sys_human_msg and not has_user:
                messages.insert(
                    first_assistant_index,
                    {"role": "user", "content": add_sys_human_msg},
                )
        else:
            # Otherwise use the default message format, compatible with every
            # Claude model including 2.1 (:162-168).
            messages[0]["role"] = "user"
            # Fix the message order when the context was trimmed: two messages with
            # "\n\nHuman: " prefixes get merged into one before the first assistant
            # turn. The role here is rewritten to 'FixHumMsg', which only the prefix
            # table below understands.
            if first_assistant_index > 0 and not exclude_prefixes:
                previous = messages[first_assistant_index - 1]
                if first_assistant_index - 1 != 0 and previous.get("role") == "user":
                    previous["role"] = "FixHumMsg"

    # Convert messages to the prompt (:173-183).
    parts: list[str] = []
    for index, message in enumerate(messages):
        # Set the prefix according to the role (:175-180). A role outside the
        # table falls back to '' via `?? ''`.
        role = message.get("role")
        name = message.get("name")
        if role == "assistant":
            prefix = "\n\nAssistant: "
        elif role == "user":
            prefix = "\n\nHuman: "
        elif role == "system":
            if index == 0:
                prefix = ""
            elif name == "example_assistant":
                prefix = "\n\nA: "
            elif name == "example_user":
                prefix = "\n\nH: "
            elif exclude_prefixes and name:
                prefix = f"\n\n{name}: "
            else:
                prefix = "\n\n"
        elif role == "FixHumMsg":
            prefix = "\n\nFirst message: "
        else:
            prefix = ""

        # Claude doesn't support message names, so they are folded into the
        # content -- except on system turns, whose name was the prefix (:182).
        name_part = f"{name}: " if name and role != "system" else ""
        parts.append(f"{prefix}{name_part}{message.get('content')}")

    return "".join(parts)


# ---------------------------------------------------------------------------
# convertClaudeMessages -- prompt-converters.js:197-376
# ---------------------------------------------------------------------------


def convert_claude_messages(
    messages: list[Any],
    prefill_string: Any = "",
    use_sys_prompt: Any = False,
    use_tools: Any = False,
    names: Any = None,
) -> dict[str, Any]:
    """``convertClaudeMessages`` (``:197-376``) -- the Anthropic Messaging API shape.

    The largest converter in the module, and the only one whose ``names`` argument
    matters: group-chat example turns are prefixed with the speaker's name and the
    ``startsWithGroupName`` predicate (`:54-56`) decides whether that was done
    already by the frontend.

    Deliberate port notes, all of them observable:

    * ``names`` may be ``None`` (the reference would throw on ``names.userName``,
      so no fixture exercises it); the accessors below reproduce the reference's
      falsy guard ``names.userName &&``.
    * ``content[content.length - 1]`` shape aside, every mutation is in place:
      ``messages`` is spliced down to the non-system tail, each turn's ``content``
      is rewritten into Anthropic content parts, and ``name`` / ``tool_calls`` /
      ``tool_call_id`` are deleted.
    * Empty text parts become ``'\\u200b'`` (``:302``) -- a zero-width space, not
      an empty string.
    * ``role in message`` style lookups are ``.get()`` here: a missing key and an
      explicit ``null`` both read as absent, which is what the reference's truthy
      checks do.
    """
    names = names if isinstance(names, dict) else {}
    user_name = names.get("userName") or ""
    char_name = names.get("charName") or ""
    starts_with_group_name = names.get("startsWithGroupName")

    def _group_prefixed(text: Any) -> bool:
        return bool(starts_with_group_name(text)) if callable(starts_with_group_name) else False

    def _parse(value: Any) -> Any:
        """``const parse`` (:233): JSON.loads a string, pass anything else through."""
        if isinstance(value, str):
            return json.loads(value)
        return value

    system_prompt: list[Any] = []
    if use_sys_prompt:
        # Collect every system message up to the first non-system one, then remove
        # them from the array (:200-220). `i` is the splice count, so it has to
        # survive the loop.
        index = 0
        # An index-driven while, not `for index in range(...)`: the reference's `i`
        # is the number of system turns consumed and is spliced on after the loop,
        # so it has to end at `messages.length` when every turn was a system one.
        # Python's `for` would leave it at `length - 1` and the splice would keep
        # the final system turn.
        while index < len(messages):
            if messages[index].get("role") != "system":
                break

            # Append example names if the frontend has not done it already, e.g.
            # for group chats (:206-216).
            if user_name and messages[index].get("name") == "example_user":
                if not str(messages[index]["content"]).startswith(f"{user_name}: "):
                    messages[index]["content"] = f"{user_name}: {messages[index]['content']}"
            if char_name and messages[index].get("name") == "example_assistant":
                content = messages[index]["content"]
                if not str(content).startswith(f"{char_name}: ") and not _group_prefixed(content):
                    messages[index]["content"] = f"{char_name}: {content}"

            system_prompt.append({"type": "text", "text": messages[index]["content"]})
            index += 1

        del messages[:index]

        # Prevent erroring out if the array is empty after the extraction (:222-229).
        if len(messages) == 0:
            messages.insert(0, {"role": "user", "content": prompt_placeholder()})

    # Replace every remaining 'system' role with 'user' -- all of them when no
    # system prompt is used at all (:232-313).
    for message in messages:
        if message.get("role") == "assistant" and message.get("tool_calls"):
            message["content"] = [
                {
                    "type": "tool_use",
                    "id": tool_call.get("id"),
                    "name": (tool_call.get("function") or {}).get("name"),
                    "input": _parse((tool_call.get("function") or {}).get("arguments")),
                }
                for tool_call in message["tool_calls"]
            ]

        if message.get("role") == "tool":
            message["role"] = "user"
            message["content"] = [
                {
                    "type": "tool_result",
                    "tool_use_id": message.get("tool_call_id"),
                    "content": message["content"],
                }
            ]

        if message.get("role") == "system":
            if user_name and message.get("name") == "example_user":
                if not str(message["content"]).startswith(f"{user_name}: "):
                    message["content"] = f"{user_name}: {message['content']}"
            if char_name and message.get("name") == "example_assistant":
                content = message["content"]
                if not str(content).startswith(f"{char_name}: ") and not _group_prefixed(content):
                    message["content"] = f"{char_name}: {content}"
            message["role"] = "user"

            # Delete `name` here so it does not get added later (:266-267).
            message.pop("name", None)

        # Convert the content to an array of parts, it is easier to work with (:270-307).
        content = message.get("content")
        if isinstance(content, str):
            # Fold the name in, Claude messages don't support it (:272-275).
            if message.get("name"):
                content = f"{message['name']}: {content}"

            message["content"] = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            parts: list[Any] = []
            for part in content:
                if not isinstance(part, dict):
                    # `content.type` would throw; a scalar part is impossible in
                    # practice and is passed through like the reference's fallback.
                    parts.append(part)
                    continue

                if part.get("type") == "image_url":
                    image_entry = part.get("image_url")
                    image_data = image_entry.get("url") if isinstance(image_entry, dict) else None
                    # `url.split(';')[0].split(':')[1]` -- the media type of a
                    # data: URL, undefined for anything else.
                    mime_type = None
                    base64_data = None
                    if isinstance(image_data, str):
                        header = image_data.split(";")[0]
                        header_parts = header.split(":")
                        mime_type = header_parts[1] if len(header_parts) > 1 else None
                        data_parts = image_data.split(",")
                        base64_data = data_parts[1] if len(data_parts) > 1 else None

                    parts.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": base64_data,
                            },
                        }
                    )
                    continue

                if part.get("type") == "text":
                    if message.get("name"):
                        part["text"] = f"{message['name']}: {part.get('text')}"

                    # An empty text is replaced with a zero-width space (:301-302).
                    parts.append({"type": "text", "text": part.get("text") or "\u200b"})
                    continue

                parts.append(part)

            message["content"] = parts

        # Remove the offending properties (:309-312).
        message.pop("name", None)
        message.pop("tool_calls", None)
        message.pop("tool_call_id", None)

    # Images in assistant turns move to the next user turn (:315-333).
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.get("role") == "assistant" and any(
            isinstance(part, dict) and part.get("type") == "image"
            for part in message.get("content")
        ):
            # Find the next user message.
            following = index + 1
            while following < len(messages) and messages[following].get("role") != "user":
                following += 1

            # If there is no user message after it, make one.
            if following >= len(messages):
                messages.insert(index + 1, {"role": "user", "content": []})

            messages[following]["content"].extend(
                part
                for part in message["content"]
                if isinstance(part, dict) and part.get("type") == "image"
            )
            message["content"] = [
                part
                for part in message["content"]
                if not (isinstance(part, dict) and part.get("type") == "image")
            ]
        index += 1

    # The messages API expects the last role to be user unless explicitly prefilling (:335-342).
    if prefill_string:
        messages.append(
            {
                "role": "assistant",
                # Dangling whitespace is not allowed while prefilling.
                "content": [{"type": "text", "text": str(prefill_string).rstrip()}],
            }
        )

    # The endpoint only supports user/assistant turns, so consecutive messages with
    # the same role are merged (:344-353).
    merged_messages: list[Any] = []
    for message in messages:
        if merged_messages and merged_messages[-1].get("role") == message.get("role"):
            merged_messages[-1]["content"].extend(message["content"])
        else:
            merged_messages.append(message)

    if not use_tools:
        # Tools are not in play: their parts degrade to plain text (:355-373).
        for message in merged_messages:
            for content in message["content"]:
                if content.get("type") == "tool_use":
                    content["type"] = "text"
                    content["text"] = _json_compact(content.get("input"))
                    content.pop("id", None)
                    content.pop("name", None)
                    content.pop("input", None)
                if content.get("type") == "tool_result":
                    content["type"] = "text"
                    content["text"] = content.get("content")
                    content.pop("tool_use_id", None)
                    content.pop("content", None)

    return {"messages": merged_messages, "systemPrompt": system_prompt}


# ---------------------------------------------------------------------------
# cachingAtDepthForClaude -- prompt-converters.js:985-1011
# ---------------------------------------------------------------------------


def caching_at_depth_for_claude(
    messages: list[Any], caching_at_depth: Any, ttl: Any = None
) -> None:
    """``cachingAtDepthForClaude`` (``:985-1011``). Mutates; returns nothing.

    Walks the array backwards, ignoring the trailing assistant prefill turn(s)
    until the first non-assistant message, and stamps ``cache_control`` onto the
    *last* content part of the message at ``depth == cachingAtDepth`` and at
    ``depth == cachingAtDepth + 2`` (the reference's two breakpoints: the cached
    prefix, and the tail it wants kept warm). ``depth`` counts role switches, not
    messages, which is why consecutive same-role turns share a depth.

    Note ``content[content.length - 1]``: a message whose ``content`` is a string
    or empty crashes there, in the reference too.

    ``ttl`` is optional: the reference declares no default, so an omitted argument
    is ``undefined`` and the stamp is ``{type: 'ephemeral', ttl: undefined}`` --
    which ``JSON.stringify`` drops to the bare ``{type: 'ephemeral'}``. A JSON
    ``null`` behaves the same way on both sides.
    """
    passed_the_prefill = False
    depth = 0
    previous_role_name = ""

    # `for (let i = messages.length - 1; i >= 0; i--)` with a `continue` -- an
    # index-driven while, so the decrement happens on the continue path exactly
    # like the JS update expression.
    index = len(messages) - 1
    while index >= 0:
        if not passed_the_prefill and messages[index].get("role") == "assistant":
            index -= 1
            continue

        passed_the_prefill = True

        if messages[index].get("role") != previous_role_name:
            if depth == caching_at_depth or depth == caching_at_depth + 2:
                content = messages[index]["content"]
                content[len(content) - 1]["cache_control"] = _cache_control(ttl)

            if depth == caching_at_depth + 2:
                break

            depth += 1
            previous_role_name = messages[index].get("role")

        index -= 1


# ---------------------------------------------------------------------------
# cachingAtDepthForOpenRouterClaude -- prompt-converters.js:1020-1063
# ---------------------------------------------------------------------------


def caching_at_depth_for_open_router_claude(
    messages: list[Any], caching_at_depth: Any, ttl: Any = None
) -> None:
    """``cachingAtDepthForOpenRouterClaude`` (``:1020-1063``). Mutates; returns nothing.

    The OpenRouter sibling of :func:`caching_at_depth_for_claude`, with two
    differences copied from the reference: a ``system`` message is skipped
    entirely -- it neither counts as a role switch nor receives a breakpoint
    (``:1033-1036``) -- and a string ``content`` is *converted* into a one-part
    array rather than being indexed, so a string turn does not crash here.

    ``ttl`` is optional here, unlike in the Claude variant: the reference declares
    no default, so an omitted argument is ``undefined``, and the cache control it
    writes is ``{type: 'ephemeral', ttl: undefined}`` -- which ``JSON.stringify``
    drops, leaving the bare ``{type: 'ephemeral'}``. A JSON ``null`` behaves the
    same way on both sides.
    """
    # caching the prefill is a terrible idea in general (:1021)
    passed_the_prefill = False
    # depth here is the number of message role switches (:1023)
    depth = 0
    previous_role_name = ""
    index = len(messages) - 1
    while index >= 0:
        if not passed_the_prefill and messages[index].get("role") == "assistant":
            index -= 1
            continue

        passed_the_prefill = True

        # Skip system messages so they don't affect depth counting or receive
        # cache breakpoints (:1033-1036).
        if messages[index].get("role") == "system":
            index -= 1
            continue

        if messages[index].get("role") != previous_role_name:
            if depth == caching_at_depth or depth == caching_at_depth + 2:
                content = messages[index].get("content")
                if isinstance(content, str):
                    messages[index]["content"] = [
                        {
                            "type": "text",
                            "text": content,
                            "cache_control": _cache_control(ttl),
                        }
                    ]
                elif isinstance(content, list) and len(content) > 0:
                    content[len(content) - 1]["cache_control"] = _cache_control(ttl)

            if depth == caching_at_depth + 2:
                break

            depth += 1
            previous_role_name = messages[index].get("role")

        index -= 1


# ---------------------------------------------------------------------------
# cachingSystemPromptForOpenRouter -- prompt-converters.js:1072-1113
# ---------------------------------------------------------------------------


def caching_system_prompt_for_open_router(messages: Any, ttl: Any = None) -> None:
    """``cachingSystemPromptForOpenRouter`` (``:1072-1113``). Mutates; returns nothing.

    Puts one ``cache_control`` breakpoint on the *first* system message, at the
    very end of its content: the last text part when the content is already an
    array, or a converted one-part array when it is a string. Every early exit in
    the reference is a silent ``return`` -- a non-array, an empty array, no system
    message, an existing ``cache_control`` on the message, or one on any part.

    The ``ttl`` default is ``undefined`` in the reference, which is the same as
    omitting the key: ``{type: 'ephemeral'}`` vs ``{type: 'ephemeral', ttl}``.
    """
    if not isinstance(messages, list) or len(messages) == 0:
        return

    # Find the first system message (:1077-1081).
    system_message = next(
        (message for message in messages if message.get("role") == "system"), None
    )
    if not system_message:
        return

    # Check whether it already has cache_control, at the message level (:1083-1086).
    if system_message.get("cache_control"):
        return

    cache_control = {"type": "ephemeral", "ttl": ttl} if ttl else {"type": "ephemeral"}

    content = system_message.get("content")
    if isinstance(content, list):
        # Any existing part-level breakpoint means this prompt is already cached (:1093-1096).
        if any(isinstance(part, dict) and part.get("cache_control") for part in content):
            return

        # The last text part, scanning from the end (:1098-1103).
        for part_index in range(len(content) - 1, -1, -1):
            part = content[part_index]
            if isinstance(part, dict) and part.get("type") == "text":
                part["cache_control"] = cache_control
                return
    elif isinstance(content, str):
        system_message["content"] = [
            {
                "type": "text",
                "text": content,
                "cache_control": cache_control,
            }
        ]


# ---------------------------------------------------------------------------
# embedOpenRouterMedia -- prompt-converters.js:1336-1370
# ---------------------------------------------------------------------------

#: ``formatMap`` (``:1352-1355``).
_AUDIO_FORMAT_MAP = {
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
}

#: ``header.match(/data:([^;]+)/)?.[1] || 'audio/mpeg'`` (``:1358``).
_DATA_MIME_RE = re.compile(r"data:([^;]+)")


def embed_open_router_media(messages: Any, options: Any = None) -> None:
    """``embedOpenRouterMedia`` (``:1336-1370``). Mutates; returns nothing.

    Rewrites data-URL audio parts into OpenRouter's ``input_audio`` shape (base64
    only; a non-data URL is left alone) and, for video, re-asserts the type it
    already has. The reference's video branch is a visible no-op -- it assigns
    ``contentPart.type = 'video_url'`` inside a check that already proved it --
    and the port keeps it, because a fixture that also carries a ``video_url``
    object is exactly what that branch covers.

    ``options`` is destructured with defaults, so an absent, ``null`` or partial
    object means ``{audio: true, video: true}``.
    """
    if not isinstance(messages, list):
        return

    options = options if isinstance(options, dict) else {}
    # `{ audio = true, video = true } = {}` -- an explicit `false` is the only off.
    audio = options.get("audio")
    video = options.get("video")
    audio = True if audio is None else audio
    video = True if video is None else video

    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            continue

        for content_part in message["content"]:
            if not isinstance(content_part, dict):
                continue

            if video and content_part.get("type") == "video_url":
                video_url = content_part.get("video_url")
                url = video_url.get("url") if isinstance(video_url, dict) else None
                if isinstance(url, str) and url.startswith("data:"):
                    content_part["type"] = "video_url"

            if audio and content_part.get("type") == "audio_url":
                audio_url = content_part.get("audio_url")
                url = audio_url.get("url") if isinstance(audio_url, dict) else None
                if isinstance(url, str) and url.startswith("data:"):
                    # `const [header, base64Data] = url.split(',')` (:1357).
                    pieces = url.split(",")
                    header = pieces[0]
                    base64_data = pieces[1] if len(pieces) > 1 else None
                    found = _DATA_MIME_RE.search(header)
                    mime_type = found.group(1) if found else "audio/mpeg"

                    content_part["type"] = "input_audio"
                    content_part["input_audio"] = {
                        "format": _AUDIO_FORMAT_MAP.get(mime_type, "mp3"),
                        "data": base64_data,
                    }

                    content_part.pop("audio_url", None)


# ---------------------------------------------------------------------------
# addReasoningContentToToolCalls -- prompt-converters.js:1377-1389
# ---------------------------------------------------------------------------


def add_reasoning_content_to_tool_calls(messages: Any) -> None:
    """``addReasoningContentToToolCalls`` (``:1377-1389``). Mutates; returns nothing.

    DeepSeek's reasoner rejects a replayed assistant turn that carries
    ``tool_calls`` but no ``reasoning_content``, so every such turn gets an empty
    string -- but only when the key is *absent*: ``'reasoning_content' in message``
    is a presence check, so an explicit ``null`` is left alone.
    """
    if not isinstance(messages, list):
        return

    for message in messages:
        if not isinstance(message, dict):
            continue
        if not isinstance(message.get("tool_calls"), list) or "reasoning_content" in message:
            continue

        message["reasoning_content"] = ""


# ---------------------------------------------------------------------------
# addOpenRouterSignatures -- prompt-converters.js:1397-1451
# ---------------------------------------------------------------------------


def add_open_router_signatures(messages: Any, model: Any) -> None:
    """``addOpenRouterSignatures`` (``:1397-1451``). Mutates; returns nothing.

    Converts the module's ``signature`` strings into OpenRouter's
    ``reasoning_details`` entries, then *deletes* the originals. Two sources feed
    the list, in this order: the message's own ``signature``, then one per
    ``tool_calls`` entry that has one (using the tool call's ``id`` as the
    detail's ``id``). A message whose signature is dropped by the
    ``gemini.thoughtSignatures`` config still loses the property -- the delete is
    outside the config gate (``:1437``).

    Every branch of ``getFormatForModel`` is a ``RegExp.test`` with no anchors, so
    the pattern only has to appear anywhere in the model id.
    """
    # getFormatForModel (:1398-1412).
    if re.search(r"google/gemini", str(model)):
        signature_format = "google-gemini-v1"
    elif re.search(r"anthropic/claude", str(model)):
        signature_format = "anthropic-claude-v1"
    elif re.search(r"openai/gpt", str(model)):
        signature_format = "openai-responses-v1"
    elif re.search(r"x-ai/grok", str(model)):
        signature_format = "xai-responses-v1"
    else:
        signature_format = "unknown"

    if not isinstance(messages, list):
        return

    for message in messages:
        if not isinstance(message, dict):
            continue

        details: list[Any] = []

        def add_detail(data: Any, detail_id: Any = None, *, _details=details) -> None:
            """``addDetail`` (:1420-1432) -- anything but a non-empty string is skipped."""
            if not isinstance(data, str) or len(data) == 0:
                return
            _details.append(
                {
                    "index": len(_details),
                    "id": detail_id or f"signature-{len(_details)}",
                    "type": "reasoning.encrypted",
                    "data": data,
                    "format": signature_format,
                }
            )

        if isinstance(message.get("signature"), str):
            if enable_thought_signatures():
                add_detail(message["signature"])
            message.pop("signature", None)

        if isinstance(message.get("tool_calls"), list):
            for tool_call in message["tool_calls"]:
                if isinstance(tool_call, dict) and isinstance(tool_call.get("signature"), str):
                    add_detail(tool_call["signature"], tool_call.get("id"))
                    tool_call.pop("signature", None)

        if details:
            message["reasoning_details"] = details


# ---------------------------------------------------------------------------
# postProcessPrompt -- prompt-converters.js:85-105
# ---------------------------------------------------------------------------


def post_process_prompt(messages: list[Any], type: str, names: Any) -> Any:  # noqa: A002
    """``postProcessPrompt`` (``:85-105``) -- the dispatch table over type.

    Every branch is ``mergeMessages`` with one of eight frozen option sets, so the
    table is reproduced literally rather than folded: ``MERGE`` and the deprecated
    ``CLAUDE`` share one call (``:87-89``), and the unlisted types -- including
    ``NONE`` (the empty string) -- fall through to ``default``, which returns the
    array **by reference**, unmutated. A caller can tell that apart from a merge,
    because a merge rewrites content in place.

    The third argument of each call is the reference's options object written out
    as a dict, exactly as ``:89-101`` spells it, because the fixture passes the
    same shape positionally to both sides.
    """
    # `type` shadows the builtin exactly as the reference calls the parameter.
    if type in (PROMPT_PROCESSING_TYPE["MERGE"], PROMPT_PROCESSING_TYPE["CLAUDE"]):
        return merge_messages(
            messages,
            names,
            {"strict": False, "placeholders": False, "single": False, "tools": False},
        )
    if type == PROMPT_PROCESSING_TYPE["MERGE_TOOLS"]:
        return merge_messages(
            messages,
            names,
            {"strict": False, "placeholders": False, "single": False, "tools": True},
        )
    if type == PROMPT_PROCESSING_TYPE["SEMI"]:
        return merge_messages(
            messages,
            names,
            {"strict": True, "placeholders": False, "single": False, "tools": False},
        )
    if type == PROMPT_PROCESSING_TYPE["SEMI_TOOLS"]:
        return merge_messages(
            messages, names, {"strict": True, "placeholders": False, "single": False, "tools": True}
        )
    if type == PROMPT_PROCESSING_TYPE["STRICT"]:
        return merge_messages(
            messages, names, {"strict": True, "placeholders": True, "single": False, "tools": False}
        )
    if type == PROMPT_PROCESSING_TYPE["STRICT_TOOLS"]:
        return merge_messages(
            messages, names, {"strict": True, "placeholders": True, "single": False, "tools": True}
        )
    if type == PROMPT_PROCESSING_TYPE["SINGLE"]:
        return merge_messages(
            messages, names, {"strict": True, "placeholders": False, "single": True, "tools": False}
        )
    # default (:102-104)
    return messages


# ---------------------------------------------------------------------------
# convertCohereMessages -- prompt-converters.js:384-422
# ---------------------------------------------------------------------------


def _optional_member(node: Any, *path: str) -> str:
    """``node?.a?.b`` as a string, with JS ``undefined`` semantics.

    Only used for the Cohere tool primer (:399), where the reference maps
    ``tc?.function?.name`` and joins the result: a missing member becomes the empty
    string in JS ``Array.join`` (``[undefined].join(', ')`` is ``''``), never the
    text ``'None'``.
    """
    for part in path:
        if not isinstance(node, dict):
            return ""
        node = node.get(part)
    return "" if node is None else str(node)


def convert_cohere_messages(messages: list[Any], names: Any) -> dict[str, Any]:
    """``convertCohereMessages`` (``:384-422``) -- Cohere's ``chatHistory``.

    Deliberate port notes, all of them observable:

    * The empty-input branch unshifts the placeholder into the *caller's* array and
      the returned ``chatHistory`` **is** that array, so the placeholder shows up in
      both the return value and the mutated arguments. That is the reference's
      behaviour, not a copy.
    * ``messages.splice(index - 1, 1)`` (``:397``) runs while ``forEach`` is
      iterating, so the current ``msg`` shifts one slot left and the next iteration
      revisits it -- and then skips the message that was pulled forward. The port
      walks the live list by index to keep that (``:392``), because a snapshot
      iteration would visit one message more than the reference does.
    * ``messages[index - 1]`` is only read when ``index > 0``, so that read is not
      the reference's crash; ``msg.content`` is read unguarded (``:396``), so a
      tool-call message with no ``content`` throws in the reference too.
    """
    names = names if isinstance(names, dict) else {}
    user_name = names.get("userName") or ""
    char_name = names.get("charName") or ""
    starts_with_group_name = names.get("startsWithGroupName")

    def _group_prefixed(text: Any) -> bool:
        return bool(starts_with_group_name(text)) if callable(starts_with_group_name) else False

    # Prevent erroring out if the messages array is empty (:385-390).
    if len(messages) == 0:
        messages.insert(0, {"role": "user", "content": prompt_placeholder()})

    # `forEach((msg, index) => ...)` over an array the body splices (:392).
    index = 0
    while index < len(messages):
        msg = messages[index]

        # Tool calls require an assistant primer (:394-401).
        if isinstance(msg.get("tool_calls"), list):
            if index > 0 and messages[index - 1].get("role") == "assistant":
                msg["content"] = messages[index - 1]["content"]
                messages.pop(index - 1)
            else:
                # `msg.tool_calls.map(tc => tc?.function?.name).join(', ')` (:399):
                # an unreadable name contributes the empty string JS `Array.join`
                # gives `undefined`/`null`, not Python's 'None'.
                tool_names = ", ".join(
                    _optional_member(tool, "function", "name") for tool in msg["tool_calls"]
                )
                msg["content"] = f"I'm going to call a tool for that: {tool_names}"
        if msg.get("name"):
            _prefix_example_name(msg, user_name, char_name, _group_prefixed)
            if msg.get("role") != "system" and not str(msg["content"]).startswith(
                f"{msg['name']}: "
            ):
                msg["content"] = f"{msg['name']}: {msg['content']}"
            del msg["name"]
        index += 1

    return {"chatHistory": messages}


# ---------------------------------------------------------------------------
# convertAI21Messages -- prompt-converters.js:631-695
# ---------------------------------------------------------------------------


def convert_ai21_messages(messages: Any, names: Any) -> list[Any]:
    """``convertAI21Messages`` (``:631-695``) -- AI21's squashed prompt.

    The non-array guard is the first line of the reference (``:632-634``): a
    non-array argument returns a fresh empty list rather than throwing, and is *not*
    mutated. In Python anything that is not a list (``None``, a dict, a string)
    takes that exit.

    Then the leading run of system messages is drained into one string joined by
    ``'\\n\\n'`` with a **trailing** separator, which ``trim()`` removes at ``:670``;
    the loop index doubles as the splice count (``:657``), so it is tracked outside
    the loop exactly as the reference does with ``let i``.

    ``'name' in msg`` (``:676``) is a key test, not a truthiness test: a present but
    empty ``name`` is still consumed (``delete``). The final pass merges consecutive
    same-role turns unconditionally -- unlike ``mergeMessages``, it does not require
    non-empty content (``:687``).
    """
    if not isinstance(messages, list):
        return []

    names = names if isinstance(names, dict) else {}
    user_name = names.get("userName") or ""
    char_name = names.get("charName") or ""
    starts_with_group_name = names.get("startsWithGroupName")

    def _group_prefixed(text: Any) -> bool:
        return bool(starts_with_group_name(text)) if callable(starts_with_group_name) else False

    # Collect the leading system messages, then remove them (:636-657).
    # `for (i = 0; i < messages.length; i++)` with a `break` on the first
    # non-system turn leaves `i` equal to the number of leading system turns,
    # because the `break` fires BEFORE the update expression. A Python
    # `for i in range(...)` cannot reproduce that (its `break` never runs an update
    # expression and leaves `i` at the last index it visited), so the reference's
    # two-part condition is written out as a while loop.
    i = 0
    system_prompt = ""
    while i < len(messages):
        if messages[i].get("role") != "system":
            break
        # Append example names if the frontend has not done it already (:643-653).
        if user_name and messages[i].get("name") == "example_user":
            if not str(messages[i]["content"]).startswith(f"{user_name}: "):
                messages[i]["content"] = f"{user_name}: {messages[i]['content']}"
        if char_name and messages[i].get("name") == "example_assistant":
            if not str(messages[i]["content"]).startswith(f"{char_name}: ") and not _group_prefixed(
                messages[i]["content"]
            ):
                messages[i]["content"] = f"{char_name}: {messages[i]['content']}"
        system_prompt += f"{messages[i]['content']}\n\n"
        i += 1

    del messages[:i]

    # Prevent erroring out if the messages array is empty (:659-665).
    if len(messages) == 0:
        messages.insert(0, {"role": "user", "content": prompt_placeholder()})

    if system_prompt:
        messages.insert(0, {"role": "system", "content": system_prompt.strip()})

    # No completion-name support, so prefix the speaker's name (:674-682).
    for msg in messages:
        if "name" in msg:
            if msg.get("role") != "system" and not str(msg["content"]).startswith(
                f"{msg['name']}: "
            ):
                msg["content"] = f"{msg['name']}: {msg['content']}"
            del msg["name"]

    # The endpoint only supports alternating turns (:684-692).
    merged_messages: list[Any] = []
    for message in messages:
        if len(merged_messages) > 0 and merged_messages[-1].get("role") == message.get("role"):
            merged_messages[-1]["content"] += "\n\n" + message["content"]
        else:
            merged_messages.append(message)

    return merged_messages


# ---------------------------------------------------------------------------
# convertMistralMessages -- prompt-converters.js:703-777
# ---------------------------------------------------------------------------


def _sha512_id(value: Any) -> str:
    """``sanitizeToolId`` (``:715``): first 9 hex chars of the sha512 of ``id``.

    ``crypto.createHash('sha512').update(id)`` requires a Buffer, string or typed
    array; Node throws on a number, and the port's ``str()`` accepts one, so a
    fixture must not hand either side a numeric id.
    """
    import hashlib

    return hashlib.sha512(str(value).encode()).hexdigest()[:9]


def convert_mistral_messages(messages: Any, names: Any) -> list[Any]:
    """``convertMistralMessages`` (``:703-777``) -- Mistral/OpenRouter shape.

    Deliberate port notes, all of them observable:

    * ``prefixEnabled`` is read through the module's ``_flag`` (the reference reads
      ``getConfigValue('mistral.enablePrefix', false, 'boolean')`` at ``:709``), so
      it is a **call-time** read, not an import-time one: a fixture's ``config``
      block can turn it on per run.
    * ``lastMsg?.role === 'assistant'`` dereferences ``messages[messages.length-1]``
      *before* the length check. On an empty array with the prefix enabled that is
      ``undefined.role`` and throws in the reference, so the order is kept
      (``:710-713``) and no fixture passes an empty array with the prefix enabled.
    * ``fixToolMessages`` (``:748-766``) is a fixpoint loop: the inner ``forEach``
      splices the array it is walking, ``rerun`` forces another pass, and the
      ``i === messages.length - 1`` early return (``:753-755``) is what stops it
      from reading past the end. ``findLastIndex`` (``:757``) is modelled as a
      reverse scan over the live list, like the reference's ``slice(0, i)``.
    * The last pass (``:770-774``) runs to ``messages.length - 1``, so a system
      message that *is* the last message keeps its role.
    """
    if not isinstance(messages, list):
        return []

    names = names if isinstance(names, dict) else {}
    user_name = names.get("userName") or ""
    char_name = names.get("charName") or ""
    starts_with_group_name = names.get("startsWithGroupName")

    def _group_prefixed(text: Any) -> bool:
        return bool(starts_with_group_name(text)) if callable(starts_with_group_name) else False

    # Make the last assistant message a prefill (:708-713).
    prefix_enabled = _flag("mistral.enablePrefix")
    # `messages[messages.length - 1]` -- `undefined` on an empty array in JS, an
    # IndexError in Python. Nothing reads the result while the list is empty (the
    # length check on the next line short-circuits), so the empty case is written
    # as an empty mapping rather than allowed to throw. With the flag ON over an
    # empty array the reference then reads `undefined.role` and throws; that
    # difference is only reachable through a fixture whose args make both sides
    # fail, so it is not pinned here.
    last_msg = messages[len(messages) - 1] if len(messages) > 0 else {}
    if prefix_enabled and len(messages) > 0 and last_msg.get("role") == "assistant":
        last_msg["prefix"] = True

    # Prefix the speaker's name; tool ids must not leak (:718-745).
    for msg in messages:
        if "tool_calls" in msg and isinstance(msg["tool_calls"], list):
            for tool in msg["tool_calls"]:
                tool["id"] = _sha512_id(tool["id"])
        if "tool_call_id" in msg and msg.get("role") == "tool":
            msg["tool_call_id"] = _sha512_id(msg["tool_call_id"])
        if msg.get("role") == "system" and msg.get("name") == "example_assistant":
            if (
                char_name
                and not str(msg["content"]).startswith(f"{char_name}: ")
                and not _group_prefixed(msg["content"])
            ):
                msg["content"] = f"{char_name}: {msg['content']}"
            del msg["name"]
        if msg.get("role") == "system" and msg.get("name") == "example_user":
            if user_name and not str(msg["content"]).startswith(f"{user_name}: "):
                msg["content"] = f"{user_name}: {msg['content']}"
            del msg["name"]
        if (
            msg.get("name")
            and msg.get("role") != "system"
            and not str(msg["content"]).startswith(f"{msg['name']}: ")
        ):
            msg["content"] = f"{msg['name']}: {msg['content']}"
            del msg["name"]

    # If a user message directly follows a tool message, fold it into the last user
    # message (:747-767).
    rerun = True
    while rerun:
        rerun = False
        for index, message in enumerate(messages):
            if index == len(messages) - 1:
                continue
            if message.get("role") == "tool" and messages[index + 1].get("role") == "user":
                last_user_message = -1
                for earlier in range(index - 1, -1, -1):
                    if messages[earlier].get("role") == "user" and messages[earlier].get("content"):
                        last_user_message = earlier
                        break
                if last_user_message != -1:
                    messages[last_user_message]["content"] += (
                        "\n\n" + messages[index + 1]["content"]
                    )
                    messages.pop(index + 1)
                    rerun = True

    # A system message directly after an assistant message becomes a user message
    # (:769-774).
    for index in range(len(messages) - 1):
        if (
            messages[index].get("role") == "assistant"
            and messages[index + 1].get("role") == "system"
        ):
            messages[index + 1]["role"] = "user"

    return messages


# ---------------------------------------------------------------------------
# convertXAIMessages -- prompt-converters.js:785-814
# ---------------------------------------------------------------------------


def convert_xai_messages(messages: Any, names: Any) -> list[Any]:
    """``convertXAIMessages`` (``:785-814``) -- xAI's name handling.

    The guard skips a message when it has no ``name`` **or** its role is ``user``
    (``:791-793``); for the rest a three-entry rule table is searched in order
    (``:795-803``) and the first match wins. Two consequences worth pinning:

    * The assistant rule and the ``example_assistant`` rule differ only in the name
      test, and the ``delete`` at ``:810`` runs for every message that reaches the
      body -- matching or not -- so **every** non-user named message loses ``name``,
      including one whose prefix was already correct.
    * The prefix picked (``:806``) is ``userName`` only for the
      ``system`` + ``example_user`` pair, otherwise ``charName``.
    """
    if not isinstance(messages, list):
        return []

    names = names if isinstance(names, dict) else {}
    user_name = names.get("userName") or ""
    char_name = names.get("charName") or ""
    starts_with_group_name = names.get("startsWithGroupName")

    def _group_prefixed(text: Any) -> bool:
        return bool(starts_with_group_name(text)) if callable(starts_with_group_name) else False

    for msg in messages:
        if not msg.get("name") or msg.get("role") == "user":
            continue

        content = msg["content"]
        needs_char_name_prefix = [
            {
                "role": "assistant",
                "condition": bool(char_name)
                and not str(content).startswith(f"{char_name}: ")
                and not _group_prefixed(content),
            },
            {
                "role": "system",
                "name": "example_assistant",
                "condition": bool(char_name)
                and not str(content).startswith(f"{char_name}: ")
                and not _group_prefixed(content),
            },
            {
                "role": "system",
                "name": "example_user",
                "condition": bool(user_name) and not str(content).startswith(f"{user_name}: "),
            },
        ]
        # `rules.find(...)`: every entry is an object, so only the three-part test
        # can reject one; the first match is the answer.
        matching_rule = None
        for rule in needs_char_name_prefix:
            if (
                msg.get("role") == rule["role"]
                and (not rule.get("name") or msg.get("name") == rule["name"])
                and rule["condition"]
            ):
                matching_rule = rule
                break

        if matching_rule:
            prefix = (
                user_name
                if msg.get("role") == "system" and msg.get("name") == "example_user"
                else char_name
            )
            msg["content"] = f"{prefix}: {msg['content']}"

        del msg["name"]

    return messages


# ---------------------------------------------------------------------------
# mergeMessages -- prompt-converters.js:827-954
# ---------------------------------------------------------------------------

#: Sentinel for mergeMessages' absent options object. The reference's third
#: parameter has a `= {}` default, so an OMITTED argument and an explicit `null`
#: are different: `null` is destructured and throws (`:827`), the omission is not.
_OPTIONS_ABSENT: Any = object()


def _prefix_example_name(
    msg: dict[str, Any], user_name: str, char_name: str, group_prefixed: Any
) -> None:
    """The example-name rules ``mergeMessages`` and ``convertCohereMessages`` share
    verbatim (``:854-863`` == ``:404-413``).

    ``example_assistant`` is prefixed with the character name and ``example_user``
    with the user name, both only when the frontend has not done it already; the
    assistant half also accepts a group member's own prefix. Neither rule touches a
    turn whose ``name`` is something else.

    ``group_prefixed`` is the caller's ``names.startsWithGroupName``. The reference
    calls that method unconditionally and throws when it is absent, while each
    caller here substitutes an always-false predicate for a names mapping that lacks
    it. That is a deliberate deviation, and it is unreachable through a fixture: a
    fixture can only deliver the predicate through the ``$promptNames`` sentinel,
    and the runner would report the reference's own TypeError as a harness error
    rather than let the two sides be compared.
    """
    if msg.get("role") == "system" and msg.get("name") == "example_assistant":
        if (
            char_name
            and not str(msg["content"]).startswith(f"{char_name}: ")
            and not group_prefixed(msg["content"])
        ):
            msg["content"] = f"{char_name}: {msg['content']}"
    if msg.get("role") == "system" and msg.get("name") == "example_user":
        if user_name and not str(msg["content"]).startswith(f"{user_name}: "):
            msg["content"] = f"{user_name}: {msg['content']}"


def merge_messages(messages: list[Any], names: Any, options: Any = _OPTIONS_ABSENT) -> list[Any]:
    """``mergeMessages`` (``:827-954``) -- the workhorse behind ``postProcessPrompt``.

    The third argument is the reference's options object ``{ strict = false,
    placeholders = false, single = false, tools = false } = {}``. It has to be a
    positional dict rather than keyword-only parameters, because a fixture calls both
    sides with the same positional slots.

    The default is the module's explicit absent marker rather than ``None``, because
    JS distinguishes the two: an ABSENT third argument takes the ``= {}`` default,
    while an explicit ``null`` is destructured as-is and throws
    ``Cannot read properties of null (reading 'strict')`` (`:827`). A port that gave
    both the same meaning would silently accept a null a real caller cannot pass.
    A dict is still read key-by-key, so ``{}`` and a partial dict behave like the
    reference's destructuring defaults.

    Deliberate port notes, all of them observable:

    * ``message.content`` is coerced to ``''`` when falsy (``:835-837``). The port
      uses ``.get('content', '')``, so a *missing* key becomes ``''`` and an explicit
      ``None`` survives -- the latter crashes on ``.startswith`` exactly like the
      reference's ``null.startsWith``. No fixture passes an explicit ``null``.
    * Every non-text content part flattens to ``''`` (``:850``), then the parts are
      joined with ``'\\n\\n'``; the three media types are replaced by a
      ``crypto.randomBytes(32).toString('base64')`` token (``:846``) that the port
      routes through :func:`_media_token`, because the *value* is not part of the
      contract -- the round trip is.
    * The token substitution at the end (``:910-934``) is deliberately partial: a
      token that ended up *inside* a larger text chunk (media glued to text by the
      squash at ``:896``) stays an opaque string, and that message keeps a string
      ``content``. Only a whole ``'\\n\\n'``-delimited chunk that *is* a token
      becomes the original object again.
    * ``single`` rewrites every role to ``user`` (``:872-885``) *before* the squash
      pass, so with ``single`` the squash always produces one message; the name
      delete at ``:886`` runs for every message regardless of what matched.
    * ``tools`` false deletes ``tool_calls`` / ``tool_call_id`` (``:887-890``) and
      demotes ``tool`` turns to ``user`` (``:869-871``).
    * The squash condition (``:895``) requires ``message.content`` to be **truthy**:
      a consecutive same-role turn with empty content is pushed as its own message
      instead of being merged. Unlike the AI21 merge, this one skips that case.
    * ``strict`` converts mid-prompt system turns to user (``:937-942``), then
      re-enters itself with ``strict: false`` and the *same* ``placeholders`` /
      ``tools`` but ``single: false`` (``:950``). The second pass re-runs the media
      flattening, which is why a strict merge over media produces a **second** token
      round trip.
    """
    if options is _OPTIONS_ABSENT:
        # `= {}`: an absent argument takes the empty object.
        options = {}
    # `const { strict, placeholders, single, tools } = null` throws in the
    # reference, so a null/undefined options object raises here too.
    strict = bool(options.get("strict", False))
    placeholders = bool(options.get("placeholders", False))
    single = bool(options.get("single", False))
    tools = bool(options.get("tools", False))

    names = names if isinstance(names, dict) else {}
    user_name = names.get("userName") or ""
    char_name = names.get("charName") or ""
    starts_with_group_name = names.get("startsWithGroupName")

    def _group_prefixed(text: Any) -> bool:
        return bool(starts_with_group_name(text)) if callable(starts_with_group_name) else False

    merged_messages: list[Any] = []
    content_tokens: dict[str, Any] = {}

    # Remove names from the messages, flattening array content (:833-891).
    for message in messages:
        if not message.get("content"):
            message["content"] = ""
        # Flatten contents and replace media URLs with random tokens (:838-853).
        if isinstance(message["content"], list):
            parts: list[str] = []
            for content in message["content"]:
                if content.get("type") == "text":
                    parts.append(content["text"])
                elif content.get("type") in ("image_url", "video_url", "audio_url"):
                    token = _media_token()
                    content_tokens[token] = content
                    parts.append(token)
                # Could be extended with other non-text types (:850).
                else:
                    parts.append("")
            message["content"] = "\n\n".join(parts)
        _prefix_example_name(message, user_name, char_name, _group_prefixed)
        if message.get("name") and message.get("role") != "system":
            if not str(message["content"]).startswith(f"{message['name']}: "):
                message["content"] = f"{message['name']}: {message['content']}"
        if message.get("role") == "tool" and not tools:
            message["role"] = "user"
        if single:
            if message.get("role") == "assistant":
                if (
                    char_name
                    and not str(message["content"]).startswith(f"{char_name}: ")
                    and not _group_prefixed(message["content"])
                ):
                    message["content"] = f"{char_name}: {message['content']}"
            if message.get("role") == "user":
                if user_name and not str(message["content"]).startswith(f"{user_name}: "):
                    message["content"] = f"{user_name}: {message['content']}"
            message["role"] = "user"
        if "name" in message:  # `delete` (:886) -- a key test, not a truthiness one
            del message["name"]
        if not tools:
            message.pop("tool_calls", None)
            message.pop("tool_call_id", None)

    # Squash consecutive messages with the same role (:893-900).
    for message in messages:
        if (
            len(merged_messages) > 0
            and merged_messages[-1].get("role") == message.get("role")
            and message.get("content")
            and message.get("role") != "tool"
        ):
            merged_messages[-1]["content"] += "\n\n" + message["content"]
        else:
            merged_messages.append(message)

    # Prevent erroring out if the mergedMessages array is empty (:902-908).
    if len(merged_messages) == 0:
        merged_messages.insert(0, {"role": "user", "content": prompt_placeholder()})

    # Put the flattened media back where the tokens are (:910-934).
    if len(content_tokens) > 0:
        for message in merged_messages:
            has_valid_token = any(token in message["content"] for token in content_tokens)
            if not has_valid_token:
                continue
            split_content = message["content"].split("\n\n")
            merged_content: list[Any] = []
            for content in split_content:
                if content in content_tokens:
                    merged_content.append(content_tokens[content])
                elif len(merged_content) > 0 and merged_content[-1].get("type") == "text":
                    merged_content[-1]["text"] += f"\n\n{content}"
                else:
                    merged_content.append({"type": "text", "text": content})
            message["content"] = merged_content

    if strict:
        for i in range(len(merged_messages)):
            # Force mid-prompt system messages to be user messages (:938-942).
            if i > 0 and merged_messages[i].get("role") == "system":
                merged_messages[i]["role"] = "user"
        if len(merged_messages) > 0 and placeholders:
            if merged_messages[0].get("role") == "system" and (
                len(merged_messages) == 1 or merged_messages[1].get("role") != "user"
            ):
                merged_messages.insert(1, {"role": "user", "content": prompt_placeholder()})
            elif (
                merged_messages[0].get("role") != "system"
                and merged_messages[0].get("role") != "user"
            ):
                merged_messages.insert(0, {"role": "user", "content": prompt_placeholder()})
        return merge_messages(
            merged_messages,
            names,
            # `{ strict: false, placeholders, single: false, tools }` (:950)
            {"strict": False, "placeholders": placeholders, "single": False, "tools": tools},
        )

    return merged_messages


# ---------------------------------------------------------------------------
# convertTextCompletionPrompt -- prompt-converters.js:961-977
# ---------------------------------------------------------------------------


def convert_text_completion_prompt(messages: Any) -> str:
    """``convertTextCompletionPrompt`` (``:961-977``) -- the flat text prompt.

    The string passthrough at ``:962-964`` is why the parameter is untyped here: a
    caller may hand over an already-rendered prompt. Otherwise each turn renders as
    ``System: content`` for an unnamed system message, ``name: content`` for a named
    one, and ``role: content`` for everything else, joined with a single newline and
    finished with ``'\\nassistant:'`` -- which is also what an **empty** input
    produces (``'' + '\\nassistant:'``), not ``''``.

    ``m.name === undefined`` is a property test that distinguishes a *missing* key
    from an explicit ``null``: a system turn with ``"name": null`` renders through
    the named branch, without throwing. No fixture passes an explicit null name.
    """
    if isinstance(messages, str):
        return messages

    message_strings: list[str] = []
    for m in messages:
        if m.get("role") == "system" and m.get("name") is None:
            message_strings.append("System: " + m["content"])
        elif m.get("role") == "system" and m.get("name") is not None:
            message_strings.append(m["name"] + ": " + m["content"])
        else:
            message_strings.append(m.get("role") + ": " + m["content"])
    return "\n".join(message_strings) + "\nassistant:"


# ---------------------------------------------------------------------------
# tryParse -- util.js:571-577
# ---------------------------------------------------------------------------


def _js_string(value: Any) -> str:
    """``String(value)`` for the two coercions inside ``convertGooglePrompt``.

    Only ``:480`` and ``:483`` call it, and both have already collapsed
    ``null``/``undefined`` through ``?? ''``, so ``value`` is never ``None``
    there. The one value where JavaScript and Python disagree is the integral
    float: ``String(5.0)`` is ``'5'``, not ``'5.0'``.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    if value is None:
        return "null"
    return str(value)


def _reject_json_constant(name: str) -> Any:
    """``JSON.parse`` rejects ``NaN``/``Infinity``; ``json.loads`` accepts them."""
    raise ValueError(f"unexpected JSON constant {name!r}")


def try_parse(value: Any) -> Any:
    """``tryParse`` (``util.js:571-577``): ``JSON.parse``, ``undefined`` on failure.

    ``undefined`` is ``None`` here. At this module's one call site (``:554``,
    ``tryParse(toolCall.function.arguments) ?? toolCall.function.arguments``) the
    difference is load-bearing: the raw-string fallback belongs to the *caller*'s
    nullish coalescing, so a JSON ``null`` argument falls back to the raw string
    instead of becoming ``None``.
    """
    if not isinstance(value, str):
        # `JSON.parse` coerces its argument with `String(...)` (util.js:573).
        if value is None:
            return None
        value = _js_string(value)
    try:
        return json.loads(value, parse_constant=_reject_json_constant)
    except ValueError:
        return None


def _starts_with_group_name(names: Any, message: Any) -> bool:
    """``names.startsWithGroupName(message)`` (``:444`` and ``:499``).

    The predicate is a *method* of the names object and reads its receiver's
    ``groupNames`` (``:54-56``), so it is called through the mapping, never
    detached. A names mapping that lacks it is a caller error in the reference
    too -- ``names.startsWithGroupName(...)`` throws there -- so it raises here
    rather than quietly answering ``False``.
    """
    predicate = names.get("startsWithGroupName") if isinstance(names, dict) else None
    if not callable(predicate):
        raise AttributeError("the names mapping has no callable 'startsWithGroupName'")
    return bool(predicate(message))


# ---------------------------------------------------------------------------
# convertGooglePrompt -- prompt-converters.js:432-623
# ---------------------------------------------------------------------------


def convert_google_prompt(
    messages: list[Any], model: Any, use_sys_prompt: Any, names: Any
) -> dict[str, Any]:
    """``convertGooglePrompt`` (``:432-623``) -- Google's ``contents`` request body.

    Everything happens in the reference's order: the leading-system-message
    collection that *shifts the input array* (``:436-450``), the example-name
    prefixing (``:438-447`` and ``:489-510``), the role fixing that turns a
    trailing assistant turn into a user turn on the no-prefill models
    (``:462-467``), the per-part wire shapes (``:512-573``), the Gemini 3
    thought-signature injection with its ``skip_thought_signature_validator``
    fallback (``:577-597``), and the consecutive-same-role merge that appends
    text with ``'\\n\\n'`` and re-pushes a part that carries a signature
    (``:599-619``).

    The port both *returns* a new object and *mutates* ``messages``, exactly like
    the reference: consumed system messages are shifted off, roles are rewritten,
    ``content`` is replaced by a part array and ``name`` is deleted.
    """
    sys_prompt: list[str] = []  # :433

    if use_sys_prompt:  # :435
        # `while (messages.length > 1 && messages[0].role === 'system')` (:436)
        while len(messages) > 1 and messages[0].get("role") == "system":
            first = messages[0]
            # :438-442 -- example_user gets the user's name glued on
            if names.get("userName") and first.get("name") == "example_user":
                prefix = f"{names['userName']}: "
                if not first["content"].startswith(prefix):
                    first["content"] = prefix + first["content"]
            # :443-447 -- example_assistant gets the character's, unless a group
            # member is already speaking (the predicate reads `this.groupNames`)
            if names.get("charName") and first.get("name") == "example_assistant":
                prefix = f"{names['charName']}: "
                if not first["content"].startswith(prefix) and not _starts_with_group_name(
                    names, first["content"]
                ):
                    first["content"] = prefix + first["content"]
            sys_prompt.append(first["content"])  # :448
            del messages[0]  # :449 -- `messages.shift()`

    # :453 -- `sysPrompt.map(text => ({ text }))`
    system_instruction = {"parts": [{"text": text} for text in sys_prompt]}

    tool_name_map: dict[Any, Any] = {}  # :454

    # :456-457 -- the newest models reject a prefilled model turn
    no_prefill_model = bool(re.search(r"gemini-3\.[67]-flash|gemini-3\.5-flash-lite", str(model)))

    contents: list[dict[str, Any]] = []  # :459

    for index, message in enumerate(messages):  # :460
        # :461-467 -- fix the roles
        if message.get("role") in ("system", "tool"):
            message["role"] = "user"
        elif message.get("role") == "assistant":
            message["role"] = "user" if no_prefill_model and index == len(messages) - 1 else "model"

        # :469-486 -- convert the content to an array of parts
        if not isinstance(message.get("content"), list):
            # `String(message.content ?? '')` (:480, :483)
            text_content = "" if message.get("content") is None else _js_string(message["content"])
            has_tool_calls = (
                isinstance(message.get("tool_calls"), list) and len(message["tool_calls"]) > 0
            )
            tool_call_id = message.get("tool_call_id")
            has_tool_call_id = isinstance(tool_call_id, str) and len(tool_call_id) > 0

            if has_tool_calls:  # :475-477
                message["content"] = [{"type": "tool_calls", "tool_calls": message["tool_calls"]}]
            elif has_tool_call_id:  # :479-481
                message["content"] = [
                    {
                        "type": "tool_call_id",
                        "tool_call_id": tool_call_id,
                        "content": text_content,
                    }
                ]
            else:  # :483
                message["content"] = [{"type": "text", "text": text_content}]

        # :488-510 -- "similar story as claude"
        message_name = message.get("name")
        if message_name:
            for part in message["content"]:
                if part.get("type") != "text":
                    continue
                if message_name == "example_user":  # :494-497
                    example_prefix = f"{names['userName']}: "
                    if names.get("userName") and not part["text"].startswith(example_prefix):
                        part["text"] = example_prefix + part["text"]
                elif message_name == "example_assistant":  # :498-501
                    example_prefix = f"{names['charName']}: "
                    if (
                        names.get("charName")
                        and not part["text"].startswith(example_prefix)
                        and not _starts_with_group_name(names, part["text"])
                    ):
                        part["text"] = example_prefix + part["text"]
                else:  # :502-506
                    example_prefix = f"{message_name}: "
                    if not part["text"].startswith(example_prefix):
                        part["text"] = example_prefix + part["text"]
            del message["name"]  # :509

        # :512-573 -- create the prompt parts
        parts: list[dict[str, Any]] = []

        def optional_member(node: Any, key: str) -> Any:
            """``node?.key`` (``:562-563``): ``undefined`` unless node is an object."""
            return node.get(key) if isinstance(node, dict) else None

        def add_data_url_part(
            target: list[Any], url: Any, default_mime_type: str, detail: Any = None
        ) -> None:
            """``addDataUrlPart`` (``:515-537``), nested inside the loop as upstream.

            The reference's closure pushes into the surrounding ``parts`` array;
            the port takes the list as an argument instead, because the definition
            sits inside a loop and a closure over a loop-local is what B023 warns
            about.
            """
            if not url or not (isinstance(url, str) and url.startswith("data:")):
                return
            chunks = url.split(",")  # :517 -- `const [header, base64Data] = ...`
            header = chunks[0]
            match = re.search(r"data:([^;]+)", header)  # :518
            mime_type = match.group(1) if match else default_mime_type
            media_resolution = (
                GEMINI_MEDIA_RESOLUTION.get(detail) if isinstance(detail, str) else None
            )

            inline_data: dict[str, Any] = {"mimeType": mime_type}  # :522-525
            # `base64Data` is `undefined` when the URL carries no comma at all,
            # and `JSON.stringify` drops the key -- so the port omits it too.
            if len(chunks) > 1:
                inline_data["data"] = chunks[1]
            media_part: dict[str, Any] = {"inlineData": inline_data}

            # :528-533 -- the media resolution is a Gemini 3-only field
            if re.search(r"gemini-3", str(model)) and media_resolution:
                media_part["mediaResolution"] = {"level": media_resolution}

            target.append(media_part)  # :535

        for part in message["content"]:
            if part.get("type") == "text":  # :539-540
                parts.append({"text": part.get("text")})
            elif part.get("type") == "tool_call_id":  # :541-548
                # `toolNameMap[part.tool_call_id] ?? 'unknown'` (:542)
                tool_name = tool_name_map.get(part.get("tool_call_id"))
                if tool_name is None:
                    tool_name = "unknown"
                parts.append(
                    {
                        "functionResponse": {
                            "name": tool_name,
                            "response": {"name": tool_name, "content": part.get("content")},
                        }
                    }
                )
            elif part.get("type") == "tool_calls":  # :549-560
                for tool_call in part.get("tool_calls") or []:
                    function = tool_call.get("function") or {}
                    # `tryParse(toolCall.function.arguments) ?? ...arguments` (:554)
                    arguments = try_parse(function.get("arguments"))
                    if arguments is None:
                        arguments = function.get("arguments")
                    function_call: dict[str, Any] = {
                        "functionCall": {"name": function.get("name"), "args": arguments}
                    }
                    if tool_call.get("signature"):  # :556
                        function_call["thoughtSignature"] = tool_call["signature"]
                    parts.append(function_call)
                    tool_name_map[tool_call.get("id")] = function.get("name")  # :559
            elif part.get("type") == "image_url":  # :561-564
                image_url = part.get("image_url")
                add_data_url_part(
                    parts,
                    optional_member(image_url, "url"),
                    "image/png",
                    optional_member(image_url, "detail"),
                )
            elif part.get("type") == "video_url":  # :565-568
                video_url = part.get("video_url")
                add_data_url_part(
                    parts,
                    optional_member(video_url, "url"),
                    "video/mp4",
                    optional_member(video_url, "detail"),
                )
            elif part.get("type") == "audio_url":  # :569-572
                # No detail argument here, so audio never carries a resolution.
                add_data_url_part(
                    parts, optional_member(part.get("audio_url"), "url"), "audio/mpeg"
                )

        # :575-597 -- inject stored thought signatures, or the Gemini 3 bypass
        if re.search(r"gemini-3", str(model)) or re.search(r"gemini-2\.5", str(model)):
            skip_signature_magic = "skip_thought_signature_validator"  # :578
            text_signature = message.get("signature")  # :579

            for part in parts:
                if (
                    enable_thought_signatures()  # :582
                    and text_signature
                    and isinstance(part.get("text"), str)
                ):
                    part["thoughtSignature"] = text_signature  # :583
                elif re.search(r"gemini-3", str(model)):  # :584
                    # :586-588 -- the bypass is mandatory for function calls
                    if part.get("functionCall") and not part.get("thoughtSignature"):
                        part["thoughtSignature"] = skip_signature_magic
                    # :589-593 -- and for text/inline data on an *image* model
                    if re.search(r"-image", str(model)) and message.get("role") == "model":
                        if isinstance(part.get("text"), str) or part.get("inlineData"):
                            part["thoughtSignature"] = skip_signature_magic
                # :595 -- Gemini 2.5 without stored signatures needs no bypass

        # :599-619 -- merge consecutive messages with the same role
        if index > 0 and message.get("role") == contents[-1].get("role"):
            for part in parts:
                if part.get("text"):  # :602 -- truthiness, so an empty text is dropped
                    text_part = None
                    for candidate in contents[-1]["parts"]:  # :603 -- `parts.find(...)`
                        if isinstance(candidate.get("text"), str):
                            text_part = candidate
                            break
                    if text_part is not None:
                        text_part["text"] += "\n\n" + part["text"]  # :605
                    else:
                        contents[-1]["parts"].append(part)  # :607
                # :610 -- a part that carries more than text is appended as well
                if (
                    part.get("inlineData")
                    or part.get("functionCall")
                    or part.get("functionResponse")
                    or part.get("thoughtSignature")
                    or part.get("mediaResolution")
                ):
                    contents[-1]["parts"].append(part)  # :611
        else:
            contents.append({"role": message.get("role"), "parts": parts})  # :615-618

    return {"contents": contents, "system_instruction": system_instruction}  # :622


# ---------------------------------------------------------------------------
# calculateClaudeBudgetTokens -- prompt-converters.js:1124-1173
# ---------------------------------------------------------------------------


def calculate_claude_budget_tokens(
    max_tokens: Any, reasoning_effort: Any, stream: Any, is_adaptive_model: Any
) -> Any:
    """``calculateClaudeBudgetTokens`` (``:1124-1173``).

    Three return types: an effort string on an adaptive model (``:1126-1142``), a
    ``None`` for ``auto`` and for an unrecognised effort, and a token number
    otherwise. The non-adaptive path applies its 1024 floor *after* the switch
    (``:1166``), so an unrecognised effort lands on 1024 rather than 0, and the
    non-streaming cap of 21333 (``:1169``) is applied last.
    """
    # :1126-1142 -- adaptive thinking for Opus 4.6+: return the effort string
    if is_adaptive_model:
        if reasoning_effort == REASONING_EFFORT["auto"]:
            return None
        if reasoning_effort == REASONING_EFFORT["min"]:
            return "low"
        if reasoning_effort == REASONING_EFFORT["low"]:
            return "low"
        if reasoning_effort == REASONING_EFFORT["medium"]:
            return "medium"
        if reasoning_effort == REASONING_EFFORT["high"]:
            return "high"
        if reasoning_effort == REASONING_EFFORT["max"]:
            return "max"
        return None

    budget_tokens = 0  # :1144

    if reasoning_effort == REASONING_EFFORT["auto"]:  # :1147-1148
        return None
    if reasoning_effort == REASONING_EFFORT["min"]:  # :1149-1151
        budget_tokens = 1024
    elif reasoning_effort == REASONING_EFFORT["low"]:  # :1152-1154
        budget_tokens = math.floor(max_tokens * 0.1)
    elif reasoning_effort == REASONING_EFFORT["medium"]:  # :1155-1157
        budget_tokens = math.floor(max_tokens * 0.25)
    elif reasoning_effort == REASONING_EFFORT["high"]:  # :1158-1160
        budget_tokens = math.floor(max_tokens * 0.5)
    elif reasoning_effort == REASONING_EFFORT["max"]:  # :1161-1163
        budget_tokens = math.floor(max_tokens * 0.95)

    budget_tokens = max(budget_tokens, 1024)  # :1166 -- `Math.max`

    if not stream:  # :1168-1170 -- `Math.min`
        budget_tokens = min(budget_tokens, 21333)

    return budget_tokens  # :1172


# ---------------------------------------------------------------------------
# calculateGoogleBudgetTokens -- prompt-converters.js:1182-1326
# ---------------------------------------------------------------------------


def calculate_google_budget_tokens(max_tokens: Any, reasoning_effort: Any, model: Any) -> Any:
    """``calculateGoogleBudgetTokens`` (``:1182-1326``).

    Five inner calculators behind a model-name dispatch whose *order* is part of
    the contract (``:1305-1325``): ``gemini-3*-pro``, then ``gemini-3*-flash``,
    then ``flash-lite``, ``flash``, ``pro``, and ``None`` for anything else. The
    order has a consequence worth stating: ``gemini-3.5-flash-lite`` matches
    ``gemini-3[.\\d]*-flash`` first, so ``getFlashLiteBudget`` is only reachable
    for a *non*-Gemini-3 ``flash-lite`` model -- and ``getProBudget`` is
    unreachable for any ``gemini-3`` pro model for the same reason.

    Two return types again: ``-1``/tokens from the legacy families, effort
    strings from the Gemini 3 families, ``None`` for an unknown model.
    """
    model_name = str(model)  # the reference's regexes coerce with `String(model)`

    def get_flash_budget() -> Any:
        """``getFlashBudget`` (``:1183-1208``): 10/25/50% of max, capped at 24576."""
        budget_tokens = 0
        if reasoning_effort == REASONING_EFFORT["auto"]:
            return -1
        if reasoning_effort == REASONING_EFFORT["min"]:
            return 0
        if reasoning_effort == REASONING_EFFORT["low"]:
            budget_tokens = math.floor(max_tokens * 0.1)
        elif reasoning_effort == REASONING_EFFORT["medium"]:
            budget_tokens = math.floor(max_tokens * 0.25)
        elif reasoning_effort == REASONING_EFFORT["high"]:
            budget_tokens = math.floor(max_tokens * 0.5)
        elif reasoning_effort == REASONING_EFFORT["max"]:
            budget_tokens = max_tokens
        budget_tokens = min(budget_tokens, 24576)  # :1205
        return budget_tokens

    def get_flash_lite_budget() -> Any:
        """``getFlashLiteBudget`` (``:1210-1235``): the flash caps, floor of 512."""
        budget_tokens = 0
        if reasoning_effort == REASONING_EFFORT["auto"]:
            return -1
        if reasoning_effort == REASONING_EFFORT["min"]:
            return 0  # :1217 -- the early return skips the 512 floor below
        if reasoning_effort == REASONING_EFFORT["low"]:
            budget_tokens = math.floor(max_tokens * 0.1)
        elif reasoning_effort == REASONING_EFFORT["medium"]:
            budget_tokens = math.floor(max_tokens * 0.25)
        elif reasoning_effort == REASONING_EFFORT["high"]:
            budget_tokens = math.floor(max_tokens * 0.5)
        elif reasoning_effort == REASONING_EFFORT["max"]:
            budget_tokens = max_tokens
        budget_tokens = max(min(budget_tokens, 24576), 512)  # :1232
        return budget_tokens

    def get_pro_budget() -> Any:
        """``getProBudget`` (``:1237-1263``): cap 32768, floor 128, ``min`` is 128."""
        budget_tokens = 0
        if reasoning_effort == REASONING_EFFORT["auto"]:
            return -1
        if reasoning_effort == REASONING_EFFORT["min"]:
            budget_tokens = 128  # :1244 -- 128, not 0
        elif reasoning_effort == REASONING_EFFORT["low"]:
            budget_tokens = math.floor(max_tokens * 0.1)
        elif reasoning_effort == REASONING_EFFORT["medium"]:
            budget_tokens = math.floor(max_tokens * 0.25)
        elif reasoning_effort == REASONING_EFFORT["high"]:
            budget_tokens = math.floor(max_tokens * 0.5)
        elif reasoning_effort == REASONING_EFFORT["max"]:
            budget_tokens = max_tokens
        budget_tokens = max(min(budget_tokens, 32768), 128)  # :1260
        return budget_tokens

    def get_gemini3_flash_budget() -> Any:
        """``getGemini3FlashBudget`` (``:1265-1284``): effort strings, not tokens."""
        # :1267 -- 3.7 flash has no 'minimal' level, so `min` becomes 'low'
        no_minimal_thinking = bool(re.search(r"gemini-3\.7-flash", model_name))
        if reasoning_effort == REASONING_EFFORT["auto"]:
            return None
        if reasoning_effort == REASONING_EFFORT["min"]:
            return "low" if no_minimal_thinking else "minimal"
        if reasoning_effort == REASONING_EFFORT["low"]:
            return "low"
        if reasoning_effort == REASONING_EFFORT["medium"]:
            return "medium"
        if reasoning_effort == REASONING_EFFORT["high"]:
            return "high"
        if reasoning_effort == REASONING_EFFORT["max"]:
            return "high"  # :1280 -- max maps to 'high', there is no 'max' level
        return None

    def get_gemini3_pro_budget() -> Any:
        """``getGemini3ProBudget`` (``:1286-1303``): only 'low' and 'high' exist."""
        if reasoning_effort == REASONING_EFFORT["auto"]:
            return None
        if reasoning_effort == REASONING_EFFORT["min"]:
            return "low"
        if reasoning_effort == REASONING_EFFORT["low"]:
            return "low"
        if reasoning_effort == REASONING_EFFORT["medium"]:
            return "low"  # :1295 -- medium is 'low' for the pro family
        if reasoning_effort == REASONING_EFFORT["high"]:
            return "high"
        if reasoning_effort == REASONING_EFFORT["max"]:
            return "high"
        return None

    # :1305-1325 -- the dispatch order is part of the contract
    if re.search(r"gemini-3[.\d]*-pro", model_name):
        return get_gemini3_pro_budget()
    if re.search(r"gemini-3[.\d]*-flash", model_name):
        return get_gemini3_flash_budget()
    if re.search(r"flash-lite", model_name):
        return get_flash_lite_budget()
    if re.search(r"flash", model_name):
        return get_flash_budget()
    if re.search(r"pro", model_name):
        return get_pro_budget()
    return None  # :1325
