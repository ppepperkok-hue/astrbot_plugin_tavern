"""SillyTavern prompt *assembly* layer, mirrored function by function.

Source: ``research/_raw/st-src/openai.js``, SillyTavern 1.19.0,
commit ``06bde939fb1e9c4c8d8641d810f0a916b5bce127``. Every public function in
this module names the JS function and its line range in the docstring, and the
body keeps the line order of the original so the two can be diffed side by
side. Values that look arbitrary (``100``, ``-1``, the role order) are copied
verbatim; do **not** "tidy" them.

Function map
------------
======================================  ==========================  =====================
JS (line range)                         Python                      note
======================================  ==========================  =====================
``formatWorldInfo`` 789-801            :func:`format_world_info`    copied
``populationInjectionPrompts`` 810-875  :func:`population_injection_prompts`  copied
``populateChatHistory`` 885-1092       :func:`populate_chat_history`         copied (trimmed)
``populateDialogueExamples`` 1101-1134 :func:`populate_dialogue_examples`    copied
``getPromptPosition`` 1140-1150        :func:`get_prompt_position`           copied
``getPromptRole`` 1157-1168            :func:`get_prompt_role`               copied
``populateChatCompletion`` 1185-1347   :func:`populate_chat_completion`      copied (trimmed)
``preparePromptsForChatCompletion``    :func:`prepare_prompts_for_chat_completion`
 1367-1516
======================================  ==========================  =====================

Runtime differences that force a rewrite
----------------------------------------
The reference runs in a browser page with module-level globals; this port runs
inside AstrBot with no DOM at all. Consequences, all deliberate:

* **No globals.** ``oai_settings``, ``power_user``, ``selected_group``,
  ``this_chid`` and friends do not exist here. Every setting arrives as an
  argument and is read through :func:`_setting`, which accepts both a mapping
  (an AstrBot config dict) and an object with attributes (a SillyTavern preset
  handed in by a test harness).
* **Injectable runtime callbacks.** ``getExtensionPrompt`` /
  ``getExtensionPromptMaxDepth`` read ``extension_settings`` at call time and
  cannot be ported. They are accepted as the injectable
  ``get_extension_prompt`` / ``get_extension_prompt_max_depth`` callables and
  default to ``""`` / ``0`` -- i.e. "there are no extension prompts", which is
  exactly what a headless AstrBot turn wants. Their JS sources are
  ``script.js:8926`` (``setExtensionPrompt``) and ``script.js:8971``
  (``getExtensionPromptMaxDepth``); the ``IN_CHAT`` branch is
  ``script.js:9016-9050``.
* **Async.** ``await getExtensionPrompt(...)`` and ``await Message.createAsync
  (...)`` become :func:`_maybe_await`, which accepts a coroutine function, a
  plain function or a value. Callers may hand in sync callbacks.
* **Out of scope for this stage** (parameters are accepted and ignored, they
  are never hard-coded): streaming, tool calls (``ToolManager``), image /
  video / audio inlining, reasoning signatures, and logit bias beyond the
  ``bias`` prompt block. ``TokenHandler.countAsync`` is replaced by the
  injected synchronous counter that :mod:`tavern.st.chat_completion` owns.
* ``promptManager`` (``PromptManager.js``) is a browser singleton. Only four
  of its members are read on this path and all four are arguments here:
  ``preparePrompt`` (``PromptManager.js:1277``) -> ``prepare_prompt``,
  ``isPromptDisabledForActiveCharacter`` -> ``is_prompt_disabled_for_active_character``,
  ``isValidName`` / ``sanitizeName`` (``PromptManager.js:1343`` / ``1349``) ->
  :func:`is_valid_name` / :func:`sanitize_name`.
* ``chat_metadata`` / timed world info is **not** read here; the world info
  port owns it. ``formatWorldInfo`` is therefore called with the default
  ``wi_format`` unless the caller passes ``world_info_format``.

Known behaviour that must keep its exact literal
------------------------------------------------
* ``injection_order`` defaults to ``100`` and the sort is ``+b - +a``
  (**descending**, ``openai.js:842``).
* An empty / falsy ``content`` prompt is filtered out of the injection loop
  (``openai.js:822``) but a prompt added directly still reaches
  :class:`~tavern.st.chat_completion.ChatCompletion`, whose ``add`` drops
  messages with neither content nor tool calls.
* ``roleTypes`` maps the *string* role to the numeric
  ``extension_prompt_roles`` (``script.js:494-498``) and is passed to
  ``getExtensionPrompt`` in that numeric form (``openai.js:856``).
* ``character_names_behavior`` is ``{NONE: -1, DEFAULT: 0, COMPLETION: 1,
  CONTENT: 2}`` (``openai.js:206-211``); the brief's ``ALWAYS`` is
  ``COMPLETION`` here -- see :class:`CharacterNamesBehavior`.
* ``continue_postfix_types`` is ``{NONE: '', SPACE: ' ', NEWLINE: '\\n',
  DOUBLE_NEWLINE: '\\n\\n'}`` (``openai.js:213-218``).
* ``INJECTION_POSITION`` is ``{RELATIVE: 0, ABSOLUTE: 1}``
  (``PromptManager.js:37-40``).
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from typing import Any

from tavern.st.chat_completion import ChatCompletion, Message, MessageCollection

__all__ = [
    "DEFAULT_INJECTION_DEPTH",
    "DEFAULT_INJECTION_ORDER",
    "EXTENSION_PROMPT_ROLES",
    "INJECTION_POSITION",
    "NEW_MAIN_CHAT_IDENTIFIER",
    "PROMPT_COLLECTION_IDENTIFIERS",
    "CharacterNamesBehavior",
    "ContinuePostfix",
    "ExtensionPromptRole",
    "InjectionPosition",
    "Prompt",
    "PromptCollection",
    "apply_continue_postfix",
    "as_prompt",
    "format_world_info",
    "get_prompt_position",
    "get_prompt_role",
    "is_valid_name",
    "populate_chat_completion",
    "populate_chat_history",
    "populate_dialogue_examples",
    "population_injection_prompts",
    "prepare_prompts_for_chat_completion",
    "sanitize_name",
]

# ---------------------------------------------------------------------------
# Constants, all copied verbatim from the reference.
# ---------------------------------------------------------------------------

#: ``PromptManager.js:31``
DEFAULT_INJECTION_DEPTH = 4
#: ``PromptManager.js:32`` and ``openai.js:834``
DEFAULT_INJECTION_ORDER = 100
#: ``openai.js:829`` -- the order bucket whose extension prompt is spliced in.
EXTENSION_PROMPTS_ORDER = 100
#: ``openai.js:825``
INJECTION_SEPARATOR = "\n"
#: ``openai.js:894`` -- identifier of the free-budget "start a new chat" message.
NEW_MAIN_CHAT_IDENTIFIER = "newMainChat"

#: ``openai.js:1241`` -- ordered prompts that are always added first.
SYSTEM_PROMPTS: tuple[str, ...] = ("nsfw", "jailbreak")
#: ``openai.js:1286-1292`` -- relative extension prompts injected next to ``main``.
KNOWN_PROMPTS: tuple[str, ...] = (
    "summary",
    "authorsNote",
    "vectorsMemory",
    "vectorsDataBank",
    "smartContext",
)
#: ``openai.js:1437-1446`` -- extension prompts already handled above.
KNOWN_EXTENSION_PROMPTS: tuple[str, ...] = (
    "1_memory",
    "2_floating_prompt",
    "3_vectors",
    "4_vectors_data_bank",
    "chromadb",
    "PERSONA_DESCRIPTION",
    "QUIET_PROMPT",
    "DEPTH_PROMPT",
)
#: The identifiers :func:`prepare_prompts_for_chat_completion` knows about.
#: Not a JS constant: the JS ``PromptCollection`` is seeded from a preset, this
#: port only mirrors the identifiers the assembly path touches. When ``prompts``
#: is ``None`` the collection is built from this list, so the whole pipeline
#: runs with no SillyTavern preset at all -- the AstrBot default.
PROMPT_COLLECTION_IDENTIFIERS: tuple[str, ...] = (
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
    "bias",
    "enhanceDefinitions",
    "summary",
    "authorsNote",
    "vectorsMemory",
    "vectorsDataBank",
    "smartContext",
    "dialogueExamples",
    "chatHistory",
    "nsfw",
    "jailbreak",
)


class InjectionPosition:
    """``PromptManager.js:37-40``.

    ``RELATIVE = 0`` lives in the ordered prompt list, ``ABSOLUTE = 1`` is an
    in-chat injection. ``None`` means "no opinion" -- the state
    :func:`prepare_prompts_for_chat_completion` produces for a marker prompt that
    the preset does not mention, and what keeps ``??`` overrides honest.
    """

    RELATIVE = 0
    ABSOLUTE = 1


#: ``const INJECTION_POSITION = { RELATIVE: 0, ABSOLUTE: 1 }`` as a plain
#: mapping, for callers that prefer the JS spelling of the constant.
INJECTION_POSITION: dict[str, int] = {
    "RELATIVE": InjectionPosition.RELATIVE,
    "ABSOLUTE": InjectionPosition.ABSOLUTE,
}


class ExtensionPromptRole:
    """``script.js:494-498`` -- the numeric enum sent to ``getExtensionPrompt``."""

    SYSTEM = 0
    USER = 1
    ASSISTANT = 2


#: ``openai.js:813-817`` -- string role -> numeric ``extension_prompt_roles``.
EXTENSION_PROMPT_ROLES: dict[str, int] = {
    "system": ExtensionPromptRole.SYSTEM,
    "user": ExtensionPromptRole.USER,
    "assistant": ExtensionPromptRole.ASSISTANT,
}


class CharacterNamesBehavior:
    """``openai.js:206-211``.

    ``COMPLETION`` is what the task brief calls ``ALWAYS``; the reference name
    wins. Note ``NONE`` is ``-1``, not ``0`` -- ``0`` is ``DEFAULT``.
    """

    NONE = -1
    DEFAULT = 0
    COMPLETION = 1
    CONTENT = 2


class ContinuePostfix:
    """``openai.js:213-218``."""

    NONE = ""
    SPACE = " "
    NEWLINE = "\n"
    DOUBLE_NEWLINE = "\n\n"


#: Accepts the SillyTavern spelling and the brief's spelling of the states.
_NAMES_BEHAVIOR_ALIASES: dict[str, int] = {
    "none": CharacterNamesBehavior.NONE,
    "default": CharacterNamesBehavior.DEFAULT,
    "completion": CharacterNamesBehavior.COMPLETION,
    "always": CharacterNamesBehavior.COMPLETION,
    "content": CharacterNamesBehavior.CONTENT,
}

_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_]{1,64}$")  # PromptManager.js:1344
_NAME_SANITIZE = re.compile(r"[^a-zA-Z0-9_]")  # PromptManager.js:1350

_MISSING = object()
#: Sentinel for "the caller did not pass ``type``" (JS default is ``null``).
_UNSET = object()


def _setting(settings: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a mapping **or** an object, else ``default``.

    The AstrBot side stores configuration in a plain ``dict`` while a
    SillyTavern preset loaded from JSON is naturally accessed as attributes;
    both must work for the same ported code.
    """
    if settings is None:
        return default
    if isinstance(settings, Mapping):
        value = settings.get(key, _MISSING)
    else:
        value = getattr(settings, key, _MISSING)
    return default if value is _MISSING else value


def _get(source: Any, key: str, default: Any = None) -> Any:
    """Attribute-or-key read used for messages, prompts and prompt-ish payloads."""
    if source is None:
        return default
    if isinstance(source, Mapping):
        value = source.get(key, _MISSING)
    else:
        value = getattr(source, key, _MISSING)
    return default if value is _MISSING else value


def _normalize_names_behavior(value: Any, default: int = CharacterNamesBehavior.DEFAULT) -> int:
    """Accept both the numeric enum and its name (``"COMPLETION"`` / ``"always"``)."""
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _NAMES_BEHAVIOR_ALIASES:
            return _NAMES_BEHAVIOR_ALIASES[lowered]
        try:
            return int(lowered)
        except ValueError:
            return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` when it is awaitable, otherwise return it as-is.

    JS ``await`` is legal on non-promises; Python is not, so every injected
    callback and every message factory goes through here. This is what lets
    :func:`population_injection_prompts` accept both a plain function and a
    coroutine function for ``get_extension_prompt``.
    """
    if inspect.isawaitable(value):
        return await value
    return value


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _truthy(value: Any) -> bool:
    """JS truthiness, for the filters copied from the reference."""
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value != ""
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def _is_absolute(prompt: Any) -> bool:
    return (
        _get(prompt, "injection_position", InjectionPosition.RELATIVE) == InjectionPosition.ABSOLUTE
    )


def _is_user_relative_prompt(prompt: Any) -> bool:
    """``false === prompt.system_prompt`` (openai.js:1243) -- a **user** prompt.

    The name says what the caller filters for: a prompt marked
    ``system_prompt: false`` is placed by the user-relative loop, not by the
    fixed system block. It is deliberately not called ``_is_system_prompt`` --
    that name invites the inverted condition, which cost a debugging round.
    """
    return _get(prompt, "system_prompt") is False


def _message_name_setter(message: Any) -> Callable[[str], Any] | None:
    """``Message.setName`` / ``set_name`` -- both spellings are accepted."""
    if hasattr(message, "set_name"):
        return message.set_name
    if hasattr(message, "setName"):
        return message.setName
    return None


# ---------------------------------------------------------------------------
# promptManager helpers, copied from PromptManager.js.
# ---------------------------------------------------------------------------


def is_valid_name(name: Any) -> bool:
    """``PromptManager.js:1343-1347`` -- OpenAI's ``^[a-zA-Z0-9_]{1,64}$``."""
    return isinstance(name, str) and _NAME_PATTERN.match(name) is not None


def sanitize_name(name: Any) -> str:
    """``PromptManager.js:1349-1351`` -- replace, then truncate to 64 chars."""
    return _NAME_SANITIZE.sub("_", "" if name is None else str(name))[:64]


def format_world_info(value: Any, wi_format: str | None = None) -> str:
    """``openai.js:789-801`` -- wrap an already-activated world info blob.

    ``const format = wiFormat ?? power_user.wi_format; return format ?
    stringFormat(format, value) : value;`` The macro substitution itself is not
    done here (the reference substitutes at activation time); the default
    ``wi_format=None`` returns ``value`` untouched.
    """
    if not wi_format:
        return _as_text(value)
    return str(wi_format).replace("{0}", _as_text(value))


def apply_continue_postfix(text: Any, postfix: str | None) -> str:
    """Append ``continue_postfix`` unless it is already the tail.

    The reference appends it once in the UI layer (``script.js:4978-4979``:
    ``cyclePrompt += oai_settings.continue_postfix``) and never on the message
    that already carries it, which is the de-duplication the task brief pins
    down. ``None``/``''`` leave the text alone, so the SillyTavern default
    (``''`` in a fresh install, ``' '`` after a preset round-trip) is preserved.
    """
    value = _as_text(text)
    if not postfix or value.endswith(postfix):
        return value
    return value + postfix


# ---------------------------------------------------------------------------
# Prompt / PromptCollection -- a projection of PromptManager.js:80-298.
# ---------------------------------------------------------------------------


@dataclass
class Prompt:
    """``PromptManager.js:80-196``, only the fields this port reads.

    ``injection_order`` defaults to ``DEFAULT_ORDER`` (100) in the JS
    constructor (``PromptManager.js:193``); the constructor default is
    reproduced here so a hand-built prompt behaves like the reference.
    """

    identifier: str | None = None
    role: str | None = None
    content: str | None = None
    name: str | None = None
    system_prompt: bool | None = None
    position: str | int | None = None
    injection_depth: int | None = None
    injection_position: int | None = None
    injection_order: int = DEFAULT_INJECTION_ORDER
    forbid_overrides: bool | None = None
    extension: bool = False
    injection_trigger: list[str] = field(default_factory=list)
    enabled: bool | None = None

    def copy(self) -> Prompt:
        """``new Prompt(prompt)`` (``openai.js:953``, ``1275``, ``1279``)."""
        return Prompt(**{f.name: getattr(self, f.name) for f in dataclass_fields(self)})


def as_prompt(value: Any) -> Prompt:
    """Coerce a mapping / object / :class:`Prompt` into a :class:`Prompt`."""
    if isinstance(value, Prompt):
        return value
    if isinstance(value, Mapping):
        known = {f.name for f in dataclass_fields(Prompt)}
        return Prompt(**{key: item for key, item in value.items() if key in known})
    return Prompt(
        identifier=_get(value, "identifier"),
        role=_get(value, "role"),
        content=_get(value, "content"),
        name=_get(value, "name"),
        system_prompt=_get(value, "system_prompt"),
        position=_get(value, "position"),
        injection_depth=_get(value, "injection_depth"),
        injection_position=_get(value, "injection_position"),
        injection_order=_get(value, "injection_order", DEFAULT_INJECTION_ORDER),
        forbid_overrides=_get(value, "forbid_overrides"),
        extension=bool(_get(value, "extension", False)),
        injection_trigger=list(_get(value, "injection_trigger") or []),
        enabled=_get(value, "enabled"),
    )


class PromptCollection:
    """``PromptManager.js:201-298``.

    ``get`` / ``index`` / ``has`` are linear scans and ``add`` appends, exactly
    like the reference, because :func:`populate_chat_completion` depends on
    indices staying stable while it walks the collection.
    """

    def __init__(self, *prompts: Any) -> None:
        self.collection: list[Prompt] = []
        self.overridden_prompts: list[str | None] = []
        self.add(*prompts)

    def add(self, *prompts: Any) -> None:
        for prompt in prompts:
            if prompt is None:
                continue
            self.collection.append(as_prompt(prompt))

    def get(self, identifier: str | None) -> Prompt | None:
        for prompt in self.collection:
            if prompt.identifier == identifier:
                return prompt
        return None

    def index(self, identifier: str | None) -> int:
        for position, prompt in enumerate(self.collection):
            if prompt.identifier == identifier:
                return position
        return -1

    def has(self, identifier: str | None) -> bool:
        return self.index(identifier) != -1

    def set(self, prompt: Any, position: int) -> None:
        self.collection[position] = as_prompt(prompt)

    def override(self, prompt: Any, position: int) -> None:
        """``PromptManager.js:294-297`` -- sets *and* records the override."""
        self.set(prompt, position)
        self.overridden_prompts.append(_get(prompt, "identifier"))

    def copy(self) -> PromptCollection:
        """A collection with fresh :class:`Prompt` objects, same order.

        ``promptManager.getPromptCollection(type)`` (``openai.js:1470``) rebuilds
        the collection from the saved configuration on every call, so the caller
        must never observe this module's in-place edits.
        """
        clone = PromptCollection()
        clone.collection = [prompt.copy() for prompt in self.collection]
        clone.overridden_prompts = list(self.overridden_prompts)
        return clone


# ---------------------------------------------------------------------------
# Message factories -- replace ``Message.fromPromptAsync`` / ``createAsync``.
# ---------------------------------------------------------------------------


async def _create_message(
    role: str | None,
    content: Any,
    identifier: str | None,
    message_factory: Callable[..., Any] | None = None,
) -> Any:
    """``Message.createAsync(role, content, identifier)``.

    ``message_factory(role, content, identifier)`` is the injection point when a
    caller wants a different message class; the default is the frozen
    :class:`~tavern.st.chat_completion.Message`.
    """
    if message_factory is not None:
        return await _maybe_await(message_factory(role, content, identifier))
    return await Message.create_async(role, content, identifier)


async def _message_from_prompt(
    prompt: Any,
    message_factory: Callable[..., Any] | None = None,
) -> Any:
    """``Message.fromPromptAsync(prompt)``.

    Only ``role`` / ``content`` / ``identifier`` / ``name`` are forwarded; the
    ``tool_calls``, ``ts`` and image branches are out of scope.
    """
    message = await _create_message(
        _get(prompt, "role"), _get(prompt, "content"), _get(prompt, "identifier"), message_factory
    )
    name = _get(prompt, "name")
    if name:
        setter = _message_name_setter(message)
        if setter is not None:
            await _maybe_await(setter(name))
    return message


async def _set_message_name(message: Any, name: Any) -> None:
    setter = _message_name_setter(message)
    if setter is not None and name is not None:
        await _maybe_await(setter(name))


# ---------------------------------------------------------------------------
# populationInjectionPrompts -- openai.js:810-875
# ---------------------------------------------------------------------------


async def population_injection_prompts(
    prompts: Iterable[Any],
    messages: Sequence[Any],
    *,
    get_extension_prompt: Callable[..., Any] | None = None,
    get_extension_prompt_max_depth: Callable[[], Any] | None = None,
    injection_separator: str = INJECTION_SEPARATOR,
    wrap: bool = False,
) -> list[Any]:
    """``openai.js:810-875`` -- splice extension prompts into the chat history.

    Line by line:

    * ``811`` ``totalInsertedMessages`` -- running offset added to the injection
      depth so later splices land *after* the ones already inserted.
    * ``813-817`` ``roleTypes`` -- string role to the numeric enum, which is the
      form ``getExtensionPrompt`` expects (``openai.js:856``).
    * ``819-820`` ``for (i = 0; i <= maxDepth; i++)`` -- depth 0 is included.
    * ``822`` ``prompts.filter(prompt => prompt.injection_depth === i &&
      prompt.content)`` -- the ``content`` test is the documented filter: an
      empty / missing content never reaches the message list.
    * ``829-839`` group by ``injection_order ?? 100``.
    * ``842`` ``Object.keys(...).sort((a, b) => +b - +a)`` -- **descending**.
    * ``847`` ``['system', 'user', 'assistant']`` -- within one order group the
      roles are emitted in that fixed order, each role's contents joined with
      ``separator`` (``\\n``), and the whole set pushed as **one** message per
      role (``861``), not one message per prompt.
    * ``855-857`` the extra extension prompt is only fetched for the order
      bucket equal to ``'100'`` and is appended after the role's own text
      (``858``); either side may be empty.
    * ``867-869`` ``messages.splice(i + totalInsertedMessages, 0, ...)``.
    * ``873`` ``messages.reverse()`` -- the depth-0 block ends up **last**.

    Returns the reversed list. ``reverse()`` is in-place in the reference, so a
    ``list`` passed in is mutated and returned; any other sequence is copied
    first.
    """
    max_depth = 0
    if get_extension_prompt_max_depth is not None:
        max_depth = int(await _maybe_await(get_extension_prompt_max_depth()) or 0)
    role_types = dict(EXTENSION_PROMPT_ROLES)

    if not isinstance(messages, list):
        messages = list(messages)

    prompt_list = [as_prompt(prompt) for prompt in prompts]
    total_inserted_messages = 0

    for depth in range(max_depth + 1):
        # openai.js:822 -- `&& prompt.content`, an empty string is falsy.
        depth_prompts = [
            prompt
            for prompt in prompt_list
            if _get(prompt, "injection_depth") == depth and _truthy(_get(prompt, "content"))
        ]

        role_messages: list[dict[str, Any]] = []

        # openai.js:830-839 -- one bucket per priority, seeded with '100'.
        order_groups: dict[Any, list[Prompt]] = {EXTENSION_PROMPTS_ORDER: []}
        for prompt in depth_prompts:
            order = _get(prompt, "injection_order")
            if order is None:
                order = DEFAULT_INJECTION_ORDER
            order_groups.setdefault(order, []).append(prompt)

        # openai.js:842 -- `+b - +a` sorts low to high; iterate high to low.
        orders = sorted(order_groups.keys(), key=lambda value: -float(value))
        for order in orders:
            order_prompts = order_groups[order]

            # openai.js:846-847 -- most important role goes lower in the list.
            for role in ("system", "user", "assistant"):
                role_prompts = injection_separator.join(
                    _as_text(_get(prompt, "content"))
                    for prompt in order_prompts
                    if _get(prompt, "role") == role
                )

                # openai.js:854-857
                extension_prompt = ""
                if str(order) == str(EXTENSION_PROMPTS_ORDER) and get_extension_prompt is not None:
                    extension_prompt = _as_text(
                        await _maybe_await(
                            get_extension_prompt(
                                2, depth, injection_separator, role_types[role], wrap
                            )
                        )
                    )

                # openai.js:858 -- `[a, b].filter(x => x).map(x => x.trim())`.
                joint_prompt = injection_separator.join(
                    part.strip()
                    for part in (_as_text(role_prompts), extension_prompt)
                    if _truthy(part)
                )

                if _truthy(joint_prompt):
                    role_messages.append({"role": role, "content": joint_prompt, "injected": True})

        if role_messages:
            # openai.js:867-869
            inject_index = depth + total_inserted_messages
            messages[inject_index:inject_index] = role_messages
            total_inserted_messages += len(role_messages)

    # openai.js:873 -- the depth-0 injections end up at the tail.
    messages.reverse()
    return messages


# ---------------------------------------------------------------------------
# populateChatHistory -- openai.js:885-1092
# ---------------------------------------------------------------------------


async def populate_chat_history(
    messages: Sequence[Any],
    prompts: Any,
    chat_completion: ChatCompletion,
    type: str | None = None,  # noqa: A002 - JS parameter name, kept for the diff
    cycle_prompt: Any = None,
    *,
    settings: Any = None,
    selected_group: bool = False,
    names_behavior: Any = None,
    continue_postfix: Any = None,
    append_continue_postfix: bool = True,
    prepare_prompt: Callable[..., Any] | None = None,
    message_factory: Callable[..., Any] | None = None,
) -> None:
    """``openai.js:885-1092`` -- fill the ``chatHistory`` collection.

    Faithful parts:

    * ``886-890`` no ``chatHistory`` prompt -> no history at all; the collection
      is added at the index of that prompt.
    * ``893-895`` the ``newMainChat`` message is created **before** the loop,
      reserved against the budget, then freed and inserted at the end
      (``1078-1079``) -- so an overflowing history still keeps one trailing
      "start a new chat" system message.
    * ``900`` ``groupNudge`` is skipped for ``type === 'impersonate'`` only.
    * ``907-927`` the ``continueNudge`` path: when ``continue_prefill`` is off
      and a ``cyclePrompt`` exists, the *last non-injected* message is pulled
      out of the list, re-prepared and put back as the first member of the nudge
      collection, followed by the nudge prompt itself.
    * ``930-933`` an ``emptyUserMessageReplacement`` user message is inserted
      when the last raw prompt is an assistant turn, ``send_if_empty`` is set
      and the message is affordable.
    * ``945-948`` the pool is ``[...messages].reverse()``; each entry's
      identifier is ``chatHistory-${messages.length - index}`` (position counted
      from the end of the *original* list), and entries are prepended with
      ``insertAtStart`` so the result reads oldest-first. The loop ``break``s on
      the first unaffordable message.
    * ``957-960`` ``names_behavior === COMPLETION`` (the brief's ``ALWAYS``) is
      the only branch that sets ``message.name``: valid names pass through,
      anything else goes through :func:`sanitize_name`.
    * ``1081-1085`` ``groupNudge`` is inserted at the **end**.
    * ``1087-1091`` the ``continueNudge`` collection is appended at position
      ``-1`` (the tail).

    Rewritten / injected: ``promptManager.preparePrompt`` -> ``prepare_prompt``
    (default identity -- macro substitution is the caller's job in AstrBot),
    ``isImageInliningSupported`` / ``canUseTools`` /
    ``isReasoningSignatureSupported`` -> out of scope, and
    ``continue_postfix`` (appended in the UI at ``script.js:4978-4979``, never
    on the message that already ends with it) is exposed here through
    :func:`apply_continue_postfix` for the caller's ``cycle_prompt``; pass
    ``append_continue_postfix=False`` to leave it untouched.
    """
    if not _has(prompts, "chatHistory"):
        return

    if append_continue_postfix and cycle_prompt is not None:
        effective_postfix = (
            _setting(settings, "continue_postfix", ContinuePostfix.SPACE)
            if continue_postfix is None
            else continue_postfix
        )
        cycle_prompt = apply_continue_postfix(cycle_prompt, effective_postfix)

    chat_completion.add(MessageCollection("chatHistory"), _index(prompts, "chatHistory"))

    names_mode = _normalize_names_behavior(
        names_behavior if names_behavior is not None else _setting(settings, "names_behavior"),
        default=CharacterNamesBehavior.DEFAULT,
    )

    # openai.js:893-895 -- reserve budget for the new chat message.
    new_chat = (
        _setting(settings, "new_group_chat_prompt", "[Start a new group chat]")
        if selected_group
        else _setting(settings, "new_chat_prompt", "[Start a new Chat]")
    )
    new_chat_message = await _create_message(
        "system", new_chat, NEW_MAIN_CHAT_IDENTIFIER, message_factory
    )
    _reserve_budget(chat_completion, new_chat_message)

    # openai.js:897-903 -- reserve budget for the group nudge.
    group_nudge_message = None
    no_group_nudge_types = ("impersonate",)
    if selected_group and _has(prompts, "groupNudge") and type not in no_group_nudge_types:
        group_nudge_message = await _message_from_prompt(
            _get_prompt(prompts, "groupNudge"), message_factory
        )
        _reserve_budget(chat_completion, group_nudge_message)

    # openai.js:905-927 -- reserve budget for the continue nudge.
    continue_message_collection = None
    if type == "continue" and cycle_prompt and not _setting(settings, "continue_prefill", False):
        prompt_object = Prompt(
            identifier="continueNudge",
            role="system",
            content=_setting(
                settings,
                "continue_nudge_prompt",
                "[Continue your last message without repeating its original content.]",
            ),
            system_prompt=True,
        )
        continue_message_collection = MessageCollection("continueNudge")
        # openai.js:915 -- findLastIndex(x => !x.injected)
        continue_message_index = -1
        for position in range(len(messages) - 1, -1, -1):
            if not _get(messages[position], "injected", False):
                continue_message_index = position
                break
        if continue_message_index >= 0:
            if isinstance(messages, list):
                continue_message = messages.pop(continue_message_index)
            else:
                continue_message = messages[continue_message_index]
            chat_message = await _message_from_prompt(
                await _prepare(prepare_prompt, continue_message), message_factory
            )
            continue_message_collection.add(chat_message)
        continue_nudge_message = await _message_from_prompt(
            await _prepare(prepare_prompt, prompt_object), message_factory
        )
        continue_message_collection.add(continue_nudge_message)
        _reserve_budget(chat_completion, continue_message_collection)

    # openai.js:929-933 -- pad an assistant-final history with an empty user turn.
    last_chat_prompt = messages[len(messages) - 1] if len(messages) else None
    empty_replacement = _setting(settings, "send_if_empty", "")
    message = await _create_message(
        "user", empty_replacement, "emptyUserMessageReplacement", message_factory
    )
    if (
        last_chat_prompt is not None
        and _get(last_chat_prompt, "role") == "assistant"
        and _truthy(empty_replacement)
        and _can_afford(chat_completion, message)
    ):
        _insert_at_start(chat_completion, message, "chatHistory")

    # openai.js:945-1075 -- newest message first, prepended one by one.
    chat_pool = list(reversed(list(messages)))
    for index, chat_prompt in enumerate(chat_pool):
        # openai.js:952-955 -- never mutate the caller's prompt.
        prompt = as_prompt(chat_prompt).copy()
        prompt.identifier = f"chatHistory-{len(messages) - index}"
        chat_message = await _message_from_prompt(
            await _prepare(prepare_prompt, prompt), message_factory
        )

        # openai.js:957-960
        if names_mode == CharacterNamesBehavior.COMPLETION and prompt.name:
            message_name = prompt.name if is_valid_name(prompt.name) else sanitize_name(prompt.name)
            await _set_message_name(chat_message, message_name)

        # openai.js:962-1064 -- media inlining and tool calls: out of scope.

        # openai.js:1070-1074
        if _can_afford(chat_completion, chat_message):
            _insert_at_start(chat_completion, chat_message, "chatHistory")
        else:
            break

    # openai.js:1077-1079 -- insert and free the new chat message.
    _free_budget(chat_completion, new_chat_message)
    _insert_at_start(chat_completion, new_chat_message, "chatHistory")

    # openai.js:1081-1085
    if selected_group and group_nudge_message is not None:
        _free_budget(chat_completion, group_nudge_message)
        _insert_at_end(chat_completion, group_nudge_message, "chatHistory")

    # openai.js:1087-1091
    if type == "continue" and continue_message_collection is not None:
        _free_budget(chat_completion, continue_message_collection)
        chat_completion.add(continue_message_collection, -1)


# ---------------------------------------------------------------------------
# populateDialogueExamples -- openai.js:1101-1134
# ---------------------------------------------------------------------------


async def populate_dialogue_examples(
    prompts: Any,
    chat_completion: ChatCompletion,
    message_examples: Sequence[Any] | None,
    *,
    settings: Any = None,
    message_factory: Callable[..., Any] | None = None,
) -> None:
    """``openai.js:1101-1134`` -- the ``dialogueExamples`` collection.

    * ``1102-1106`` a missing ``dialogueExamples`` prompt means no examples.
    * ``1108`` one ``newChat`` system message (``new_example_chat_prompt``,
      default ``'[Example Chat]'``, ``openai.js:110``) is created **per
      dialogue**, inside the loop, and the affordability set
      ``[newExampleChat, ...chatMessages]`` gates that dialogue (``1124``).
    * ``1115-1121`` every example message is forced to role ``system`` and gets
      ``setName(prompt.name)``; the identifier is
      ``dialogueExamples ${dialogueIndex}-${promptIndex}`` (a space, not a dash).
    * ``1124-1126`` a dialogue that does not fit ``break``s the loop (not
      ``continue``), so later examples never sneak in.
    * ``1128-1131`` insert order per dialogue is ``newChat`` **then** each
      message, appended at the tail of the collection.
    """
    if not _has(prompts, "dialogueExamples"):
        return

    chat_completion.add(MessageCollection("dialogueExamples"), _index(prompts, "dialogueExamples"))
    if message_examples is None or len(message_examples) == 0:
        return

    for dialogue_index, dialogue in enumerate(list(message_examples)):
        new_example_chat = await _create_message(
            "system",
            _setting(settings, "new_example_chat_prompt", "[Example Chat]"),
            "newChat",
            message_factory,
        )
        chat_messages = []

        for prompt_index, prompt in enumerate(dialogue or []):
            role = "system"
            content = _get(prompt, "content") or ""
            identifier = f"dialogueExamples {dialogue_index}-{prompt_index}"

            chat_message = await _create_message(role, content, identifier, message_factory)
            await _set_message_name(chat_message, _get(prompt, "name"))
            chat_messages.append(chat_message)

        # openai.js:1124-1126
        if not _can_afford_all(chat_completion, [new_example_chat, *chat_messages]):
            break

        _insert(chat_completion, new_example_chat, "dialogueExamples")
        for chat_message in chat_messages:
            _insert(chat_completion, chat_message, "dialogueExamples")


# ---------------------------------------------------------------------------
# getPromptPosition / getPromptRole -- openai.js:1140-1168
# ---------------------------------------------------------------------------


def get_prompt_position(position: Any) -> str | bool:
    """``openai.js:1140-1150``.

    ``BEFORE_PROMPT`` (0) -> ``'start'``, ``IN_PROMPT`` (1) -> ``'end'``,
    anything else (including ``None`` and ``IN_CHAT`` 2) -> ``False``.
    """
    if position == 0:
        return "start"
    if position == 1:
        return "end"
    return False


def get_prompt_role(role: Any) -> str:
    """``openai.js:1157-1168`` -- numeric enum to OpenAI role, default ``system``."""
    if role == ExtensionPromptRole.SYSTEM:
        return "system"
    if role == ExtensionPromptRole.USER:
        return "user"
    if role == ExtensionPromptRole.ASSISTANT:
        return "assistant"
    return "system"


# ---------------------------------------------------------------------------
# populateChatCompletion -- openai.js:1185-1347
# ---------------------------------------------------------------------------


async def populate_chat_completion(
    prompts: Any,
    chat_completion: ChatCompletion,
    *,
    bias: str | None = None,
    quiet_prompt: str | None = None,
    quiet_image: Any = None,  # noqa: ARG001 - parity only, inlining is out of scope
    type: Any = _UNSET,  # noqa: A002 - JS parameter name
    cycle_prompt: Any = None,
    messages: Sequence[Any] | None = None,
    message_examples: Sequence[Any] | None = None,
    settings: Any = None,
    pin_examples: Any = None,
    selected_group: bool = False,
    names_behavior: Any = None,
    continue_postfix: Any = None,
    prepare_prompt: Callable[..., Any] | None = None,
    is_prompt_disabled_for_active_character: Callable[[str], Any] | None = None,
    message_factory: Callable[..., Any] | None = None,
    get_extension_prompt: Callable[..., Any] | None = None,
    get_extension_prompt_max_depth: Callable[[], Any] | None = None,
) -> None:
    """``openai.js:1185-1347`` -- the assembly order of the whole prompt.

    The order below **is** the specification; it is asserted entry by entry in
    ``tests/test_prompt_build.py``:

    1. ``1210`` ``reserveBudget(3)`` -- the ``<|start|>assistant<|message|>``
       priming tokens, unconditionally and before anything else.
    2. ``1212-1218`` ``worldInfoBefore``, ``main``, ``worldInfoAfter``,
       ``charDescription``, ``charPersonality``, ``scenario``,
       ``personaDescription`` -- each placed at the index of its own prompt in
       the collection (``1203-1207``), so user-defined prompts sitting between
       them keep their slots.
    3. ``1221-1238`` ``controlPrompts``: ``impersonate`` (only for
       ``type === 'impersonate'``) then ``quietPrompt`` (only when it has
       content). Reserved now, appended last (``1345-1346``).
    4. ``1241-1257`` ``['nsfw', 'jailbreak']`` followed by every prompt with
       ``system_prompt === false`` that is not absolute, in collection order.
    5. ``1260`` ``enhanceDefinitions`` when present.
    6. ``1263`` ``bias`` -- only when ``bias`` is truthy after ``trim()``. The
       ``bias`` *prompt* is role ``assistant`` (``1385``); out-of-scope logit
       bias is not involved.
    7. ``1265-1307`` ``injectToMain``: with ``main`` added to the completion its
       message is placed next to ``main`` at ``'start'`` / ``'end'``; without
       ``main`` the prompt is converted into an absolute injection (role / depth
       / order copied from the ``main`` entry, ``1275-1281``). ``knownPrompts``
       in that fixed order first, then the remaining ``extension && position``
       prompts in collection order.
    8. ``1309-1316`` tool token pre-allocation -- out of scope.
    9. ``1318-1331`` ``continuePrefill``: for ``type === 'continue'`` with
       ``continue_prefill`` on, the first message is shifted off, optionally
       prefixed with ``assistant_prefill`` (joined with ``\\n\\n``), turned into
       the ``continuePrefill`` control prompt and reserved.
    10. ``1333-1334`` in-chat injections via :func:`population_injection_prompts`
        over the **absolute** prompts.
    11. ``1336-1343`` ``pin_examples`` chooses whether dialogue examples come
        before or after the chat history.
    12. ``1345-1346`` free the control prompt budget, then append the control
        collection last when it is non-empty.

    ``quiet_image`` and tool calling are accepted and ignored; ``promptManager``
    is replaced by the injected callbacks (see the module docstring).
    """
    if type is _UNSET:
        type = None  # noqa: A001 - JS default is null
    if messages is None:
        messages = []

    # openai.js:1187-1208 -- the local addToChatCompletion helper.
    async def add_to_chat_completion(source: str, target: str | None = None) -> None:
        if not _has(prompts, source):
            return

        if is_prompt_disabled_for_active_character is not None and source != "main":
            if await _maybe_await(is_prompt_disabled_for_active_character(source)):
                return

        prompt = _get_prompt(prompts, source)

        # openai.js:1198-1201 -- absolute prompts are injected, not ordered.
        if _is_absolute(prompt):
            return

        index = _index(prompts, target) if target else _index(prompts, source)
        collection = MessageCollection(source)
        collection.add(await _message_from_prompt(prompt, message_factory))
        _add(chat_completion, collection, index)

    # openai.js:1210
    _reserve_budget(chat_completion, 3)

    # openai.js:1211-1218 -- character and world information.
    await add_to_chat_completion("worldInfoBefore")
    await add_to_chat_completion("main")
    await add_to_chat_completion("worldInfoAfter")
    await add_to_chat_completion("charDescription")
    await add_to_chat_completion("charPersonality")
    await add_to_chat_completion("scenario")
    await add_to_chat_completion("personaDescription")

    # openai.js:1220-1238 -- control prompts, always positioned last.
    _set_overridden_prompts(chat_completion, list(_get(prompts, "overridden_prompts", []) or []))
    control_prompts = MessageCollection("controlPrompts")

    impersonate_prompt = _get_prompt(prompts, "impersonate")
    impersonate_message = (
        await _message_from_prompt(impersonate_prompt, message_factory)
        if impersonate_prompt is not None
        else None
    )
    if type == "impersonate" and impersonate_message is not None:
        control_prompts.add(impersonate_message)

    quiet_prompt_entry = _get_prompt(prompts, "quietPrompt")
    quiet_prompt_message = (
        await _message_from_prompt(quiet_prompt_entry, message_factory)
        if quiet_prompt_entry is not None
        else None
    )
    if quiet_prompt_message is not None and _get(quiet_prompt_message, "content"):
        # openai.js:1231-1233 -- quiet image inlining is out of scope.
        control_prompts.add(quiet_prompt_message)

    _reserve_budget(chat_completion, control_prompts)

    # openai.js:1240-1257 -- ordered system and user prompts.
    system_prompts = list(SYSTEM_PROMPTS)
    user_relative_prompts = [
        prompt.identifier
        for prompt in _collection(prompts)
        if _is_user_relative_prompt(prompt) and not _is_absolute(prompt)
    ]
    absolute_prompts = [prompt for prompt in _collection(prompts) if _is_absolute(prompt)]

    for identifier in [*system_prompts, *user_relative_prompts]:
        await add_to_chat_completion(identifier)

    # openai.js:1259-1260
    if _has(prompts, "enhanceDefinitions"):
        await add_to_chat_completion("enhanceDefinitions")

    # openai.js:1262-1263
    if bias and _truthy(str(bias).strip()):
        await add_to_chat_completion("bias")

    # openai.js:1265-1284
    async def inject_to_main(prompt: Any, position: Any) -> None:
        if _has(chat_completion, "main"):
            _insert(
                chat_completion,
                await _message_from_prompt(prompt, message_factory),
                "main",
                position,
            )
            return
        # Convert the relative prompt into an injection next to 'main'.
        index_of_main = next(
            (i for i, item in enumerate(absolute_prompts) if _get(item, "identifier") == "main"), -1
        )
        if index_of_main < 0:
            return
        main = absolute_prompts[index_of_main]
        prompt_copy = as_prompt(prompt).copy()
        prompt_copy.role = _get(main, "role")
        prompt_copy.injection_position = _get(main, "injection_position")
        prompt_copy.injection_depth = _get(main, "injection_depth")
        prompt_copy.injection_order = _get(main, "injection_order", DEFAULT_INJECTION_ORDER)
        new_index = index_of_main + 1 if position == "end" else index_of_main
        absolute_prompts.insert(new_index, prompt_copy)

    # openai.js:1294-1302 -- known relative extension prompts.
    for key in KNOWN_PROMPTS:
        if _has(prompts, key):
            prompt = _get_prompt(prompts, key)
            if _get(prompt, "position"):
                await inject_to_main(prompt, _get(prompt, "position"))

    # openai.js:1304-1307 -- other relative extension prompts.
    for prompt in _collection(prompts):
        if _get(prompt, "extension") and _get(prompt, "position"):
            await inject_to_main(prompt, _get(prompt, "position"))

    # openai.js:1309-1316 -- tool token pre-allocation is out of scope.

    # openai.js:1318-1331 -- displace the message being continued.
    if type == "continue" and _setting(settings, "continue_prefill", False) and len(messages):
        if isinstance(messages, list):
            chat_message = messages.pop(0)
        else:
            chat_message = messages[0]
            messages = list(messages[1:])
        is_assistant_role = _get(chat_message, "role") == "assistant"
        # chat_completion_sources.CLAUDE === 'claude'
        supports_assistant_prefill = _setting(settings, "chat_completion_source") == "claude"
        names_in_completion = (
            _normalize_names_behavior(
                names_behavior
                if names_behavior is not None
                else _setting(settings, "names_behavior"),
                default=CharacterNamesBehavior.DEFAULT,
            )
            == CharacterNamesBehavior.COMPLETION
        )
        assistant_prefill = (
            _setting(settings, "assistant_prefill", "")
            if (is_assistant_role and supports_assistant_prefill)
            else ""
        )
        message_content = "\n\n".join(
            _as_text(part) for part in (assistant_prefill, _get(chat_message, "content")) if part
        )
        continue_message = await _create_message(
            _get(chat_message, "role"), message_content, "continuePrefill", message_factory
        )
        name = _get(chat_message, "name")
        if name and names_in_completion:
            await _set_message_name(continue_message, sanitize_name(name))
        control_prompts.add(continue_message)
        _reserve_budget(chat_completion, continue_message)

    # openai.js:1333-1334 -- in-chat injections.
    messages = await population_injection_prompts(
        absolute_prompts,
        messages,
        get_extension_prompt=get_extension_prompt,
        get_extension_prompt_max_depth=get_extension_prompt_max_depth,
    )

    # openai.js:1336-1343 -- do the examples come before or after the history?
    resolved_pin_examples = (
        _setting(settings, "pin_examples", False) if pin_examples is None else pin_examples
    )
    history_kwargs: dict[str, Any] = {
        "settings": settings,
        "selected_group": selected_group,
        "names_behavior": names_behavior,
        "continue_postfix": continue_postfix,
        "prepare_prompt": prepare_prompt,
        "message_factory": message_factory,
    }
    if resolved_pin_examples:
        await populate_dialogue_examples(
            prompts,
            chat_completion,
            message_examples,
            settings=settings,
            message_factory=message_factory,
        )
        await populate_chat_history(
            messages, prompts, chat_completion, type, cycle_prompt, **history_kwargs
        )
    else:
        await populate_chat_history(
            messages, prompts, chat_completion, type, cycle_prompt, **history_kwargs
        )
        await populate_dialogue_examples(
            prompts,
            chat_completion,
            message_examples,
            settings=settings,
            message_factory=message_factory,
        )

    # openai.js:1345-1346
    _free_budget(chat_completion, control_prompts)
    if len(_collection(control_prompts)):
        _add(chat_completion, control_prompts)


# ---------------------------------------------------------------------------
# preparePromptsForChatCompletion -- openai.js:1367-1516
# ---------------------------------------------------------------------------


async def prepare_prompts_for_chat_completion(
    *,
    scenario: str | None = None,
    char_personality: str | None = None,
    name2: str | None = None,  # noqa: ARG001 - parity with the JS signature
    world_info_before: Any = None,
    world_info_after: Any = None,
    char_description: str | None = None,
    quiet_prompt: str | None = None,
    bias: str | None = None,
    extension_prompts: Mapping[str, Any] | None = None,
    system_prompt_override: str | None = None,
    jailbreak_prompt_override: str | None = None,
    type: str | None = None,  # noqa: A002 - JS parameter name
    prompts: Any = None,
    settings: Any = None,
    persona_description: str | None = None,
    persona_description_position: Any = None,
    world_info_format: str | None = None,
    prepare_prompt: Callable[..., Any] | None = None,
    is_prompt_disabled_for_active_character: Callable[[str], Any] | None = None,
    get_prompt_collection: Callable[..., Any] | None = None,
) -> PromptCollection:
    """``openai.js:1367-1516`` -- merge markers, system prompts and user prompts.

    Pipeline:

    * ``1368-1369`` apply ``scenario_format`` / ``personality_format`` only when
      the text is present *and* the format is set.
    * ``1370-1371`` ``groupNudge`` and ``impersonate`` texts.
    * ``1374-1386`` the nine system prompt entries, in this exact order:
      ``worldInfoBefore``, ``worldInfoAfter``, ``charDescription``,
      ``charPersonality``, ``scenario``, ``impersonate``, ``quietPrompt``,
      ``groupNudge``, then ``bias`` -- **role ``assistant``**, the only
      non-system entry.
    * ``1388-1435`` extension blocks: ``1_memory`` -> ``summary``,
      ``2_floating_prompt`` -> ``authorsNote``, ``3_vectors`` ->
      ``vectorsMemory`` (role hard-coded ``system``, ``1409``),
      ``4_vectors_data_bank`` -> ``vectorsDataBank``, ``chromadb`` ->
      ``smartContext``, then the persona description at ``IN_PROMPT`` position
      (``1433``). Each contributes only when ``.value`` is truthy; positions go
      through :func:`get_prompt_position`.
    * ``1449-1467`` unknown extension prompts: skipped when in
      ``knownExtensionPrompts``, when ``.value`` is falsy, when the position is
      neither BEFORE nor IN prompt, or when an async ``filter()`` says no. The
      identifier is ``key.replace(/\\W/g, '_')`` and ``extension: true`` is set.
    * ``1470-1472`` the user-defined collection; the merge keeps the marker
      position (``1489-1492``), which is why the entry order of the returned
      collection is the preset order.
    * ``1473-1493`` merge: the prompt-manager entry, when it exists, may
      override ``injection_position`` / ``injection_depth`` /
      ``injection_order`` / ``role`` -- each with ``??`` semantics, i.e. only
      when the override is not ``None``. Then the entry replaces the marker in
      place, or is appended at the end.
    * ``1495-1513`` character overrides for ``main`` and ``jailbreak``. The
      override applies unless ``forbid_overrides === true`` or the character has
      that prompt disabled; the *original* content is handed to
      ``preparePrompt(prompt, original)`` on both paths.

    Returns a :class:`PromptCollection` whose ``collection`` list is the final
    order and whose ``overridden_prompts`` mirrors the JS bookkeeping (which
    :func:`populate_chat_completion` forwards to ``setOverriddenPrompts``).
    """
    extension_prompts = extension_prompts or {}

    # openai.js:1368-1371
    scenario_format = _setting(settings, "scenario_format")
    personality_format = _setting(settings, "personality_format")
    scenario_text = scenario_format if (scenario and scenario_format) else (scenario or "")
    char_personality_text = (
        personality_format
        if (char_personality and personality_format)
        else (char_personality or "")
    )
    group_nudge = _setting(
        settings, "group_nudge_prompt", "[Write the next reply only as {{char}}.]"
    )
    impersonation_prompt = _setting(settings, "impersonation_prompt", "") or ""

    # openai.js:1373-1386
    #
    # ``system_prompt`` is set on every one of them: these nine are the entries
    # ST's ``PromptManager`` marks as system prompts, which is what keeps
    # ``populate_chat_completion``'s "user-relative" filter
    # (``openai.js:1242-1243``, ``false === prompt.system_prompt``) from
    # trailing them after the user's own prompt blocks again.
    system_prompts: list[Prompt] = [
        Prompt(
            role="system",
            content=format_world_info(world_info_before, world_info_format),
            identifier="worldInfoBefore",
            system_prompt=True,
        ),
        Prompt(
            role="system",
            content=format_world_info(world_info_after, world_info_format),
            identifier="worldInfoAfter",
            system_prompt=True,
        ),
        Prompt(
            role="system",
            content=char_description,
            identifier="charDescription",
            system_prompt=True,
        ),
        Prompt(
            role="system",
            content=char_personality_text,
            identifier="charPersonality",
            system_prompt=True,
        ),
        Prompt(role="system", content=scenario_text, identifier="scenario", system_prompt=True),
        Prompt(
            role="system",
            content=impersonation_prompt,
            identifier="impersonate",
            system_prompt=True,
        ),
        Prompt(role="system", content=quiet_prompt, identifier="quietPrompt", system_prompt=True),
        Prompt(role="system", content=group_nudge, identifier="groupNudge", system_prompt=True),
        Prompt(role="assistant", content=bias, identifier="bias", system_prompt=True),
    ]

    # openai.js:1388-1395 -- Tavern Extras summary.
    summary = extension_prompts.get("1_memory")
    if summary and _value_of(summary):
        system_prompts.append(
            Prompt(
                role=get_prompt_role(_get(summary, "role")),
                content=_value_of(summary),
                identifier="summary",
                position=get_prompt_position(_get(summary, "position")),
            )
        )

    # openai.js:1397-1404 -- Author's Note.
    authors_note = extension_prompts.get("2_floating_prompt")
    if authors_note and _value_of(authors_note):
        system_prompts.append(
            Prompt(
                role=get_prompt_role(_get(authors_note, "role")),
                content=_value_of(authors_note),
                identifier="authorsNote",
                position=get_prompt_position(_get(authors_note, "position")),
            )
        )

    # openai.js:1406-1413 -- vectors memory (role is hard-coded to 'system').
    vectors_memory = extension_prompts.get("3_vectors")
    if vectors_memory and _value_of(vectors_memory):
        system_prompts.append(
            Prompt(
                role="system",
                content=_value_of(vectors_memory),
                identifier="vectorsMemory",
                position=get_prompt_position(_get(vectors_memory, "position")),
            )
        )

    # openai.js:1415-1421
    vectors_data_bank = extension_prompts.get("4_vectors_data_bank")
    if vectors_data_bank and _value_of(vectors_data_bank):
        system_prompts.append(
            Prompt(
                role=get_prompt_role(_get(vectors_data_bank, "role")),
                content=_value_of(vectors_data_bank),
                identifier="vectorsDataBank",
                position=get_prompt_position(_get(vectors_data_bank, "position")),
            )
        )

    # openai.js:1423-1430 -- Smart Context (ChromaDB).
    smart_context = extension_prompts.get("chromadb")
    if smart_context and _value_of(smart_context):
        system_prompts.append(
            Prompt(
                role="system",
                content=_value_of(smart_context),
                identifier="smartContext",
                position=get_prompt_position(_get(smart_context, "position")),
            )
        )

    # openai.js:1432-1435 -- Persona description, only when positioned IN_PROMPT.
    resolved_persona_position = (
        persona_description_position
        if persona_description_position is not None
        else _setting(settings, "persona_description_position_in_prompt", 1)
    )
    if persona_description and resolved_persona_position == 1:
        system_prompts.append(
            Prompt(role="system", content=persona_description, identifier="personaDescription")
        )

    # openai.js:1449-1467 -- extension prompts that are not known by name.
    for key, extension_prompt in extension_prompts.items():
        if key in KNOWN_EXTENSION_PROMPTS:
            continue
        if not _value_of(extension_prompt):
            continue
        position = _get(extension_prompt, "position")
        if position not in (0, 1):
            continue

        prompt_filter = _get(extension_prompt, "filter")
        if callable(prompt_filter) and not await _maybe_await(prompt_filter()):
            continue

        system_prompts.append(
            Prompt(
                identifier=re.sub(r"\W", "_", str(key)),
                position=get_prompt_position(position),
                role=get_prompt_role(_get(extension_prompt, "role")),
                content=_value_of(extension_prompt),
                extension=True,
            )
        )

    # openai.js:1469-1470 -- the prompt order defined by the user.
    prompts = await _resolve_prompt_collection(prompts, type, get_prompt_collection)

    # openai.js:1472-1493 -- merge system prompts into the collection.
    for prompt in system_prompts:
        collection_prompt = _get_prompt(prompts, prompt.identifier)

        # openai.js:1476-1486 -- `??`: an explicit None must not clobber the
        # default, but a preset that answers "no" with 0/False must win.
        if collection_prompt is not None:
            override = _get(collection_prompt, "injection_position")
            if override is not None:
                prompt.injection_position = override
            override = _get(collection_prompt, "injection_depth")
            if override is not None:
                prompt.injection_depth = override
            override = _get(collection_prompt, "injection_order")
            if override is not None:
                prompt.injection_order = override
            override = _get(collection_prompt, "role")
            if override is not None:
                prompt.role = override

        new_prompt = await _prepare(prepare_prompt, prompt)
        marker_index = _index(prompts, prompt.identifier)

        if marker_index != -1:
            _set_prompt(prompts, new_prompt, marker_index)
        else:
            _add_prompt(prompts, new_prompt)

    # openai.js:1495-1503 -- character-specific main prompt.
    system_prompt = _get_prompt(prompts, "main")
    is_system_prompt_disabled = await _prompt_disabled(
        is_prompt_disabled_for_active_character, "main"
    )
    if (
        system_prompt_override
        and system_prompt
        and _get(system_prompt, "forbid_overrides") is not True
        and not is_system_prompt_disabled
    ):
        main_original_content = _get(system_prompt, "content")
        system_prompt.content = system_prompt_override
        main_replacement = await _prepare(prepare_prompt, system_prompt, main_original_content)
        _override_prompt(prompts, main_replacement, _index(prompts, "main"))

    # openai.js:1505-1513 -- character-specific jailbreak.
    jailbreak_prompt = _get_prompt(prompts, "jailbreak")
    is_jailbreak_prompt_disabled = await _prompt_disabled(
        is_prompt_disabled_for_active_character, "jailbreak"
    )
    if (
        jailbreak_prompt_override
        and jailbreak_prompt
        and _get(jailbreak_prompt, "forbid_overrides") is not True
        and not is_jailbreak_prompt_disabled
    ):
        jb_original_content = _get(jailbreak_prompt, "content")
        jailbreak_prompt.content = jailbreak_prompt_override
        jb_replacement = await _prepare(prepare_prompt, jailbreak_prompt, jb_original_content)
        _override_prompt(prompts, jb_replacement, _index(prompts, "jailbreak"))

    return prompts


def _value_of(prompt: Any) -> Any:
    """``extensionPrompts[key].value`` -- also accepts a bare string payload."""
    if isinstance(prompt, str):
        return prompt
    return _get(prompt, "value")


async def _prompt_disabled(callback: Callable[[str], Any] | None, identifier: str) -> bool:
    if callback is None:
        return False
    return bool(await _maybe_await(callback(identifier)))


# ---------------------------------------------------------------------------
# ChatCompletion access helpers.
#
# The frozen contract exposes ``add`` / ``insert_at_start`` / ``insert_at_end``
# but not ``insert(collection, identifier, position)`` / ``reserve_budget`` /
# ``free_budget`` / ``set_overridden_prompts``. All four are used by openai.js on
# this exact path (``1268``, ``1210``, ``1078``, ``1221``), so the Lead has to
# add them; each helper below degrades to the closest contract method while they
# are missing, so the port stays runnable and the gap stays visible.
# ---------------------------------------------------------------------------


def _reserve_budget(chat_completion: Any, item: Any) -> None:
    method = getattr(chat_completion, "reserve_budget", None)
    if method is not None:
        method(item)


def _free_budget(chat_completion: Any, item: Any) -> None:
    method = getattr(chat_completion, "free_budget", None)
    if method is not None:
        method(item)


def _set_overridden_prompts(chat_completion: Any, identifiers: list[Any]) -> None:
    method = getattr(chat_completion, "set_overridden_prompts", None)
    if method is not None:
        method(identifiers)


def _can_afford(chat_completion: Any, message: Any) -> bool:
    return bool(chat_completion.can_afford(message))


def _can_afford_all(chat_completion: Any, messages: Sequence[Any]) -> bool:
    return bool(chat_completion.can_afford_all(list(messages)))


def _insert(chat_completion: Any, message: Any, identifier: str, position: Any = None) -> None:
    """``chatCompletion.insert(message, identifier, position)`` (``openai.js:1268``).

    ``identifier`` names a :class:`~tavern.st.chat_completion.MessageCollection`
    in the chat and the message is placed **inside** it: ``'start'`` before the
    group's first message, ``'end'`` after the last one, ``None`` appends.
    Without ``insert`` in the contract this degrades to ``insert_at_start`` /
    ``insert_at_end``, which mean the same two things.
    """
    method = getattr(chat_completion, "insert", None)
    if method is not None:
        method(message, identifier, "end" if position is None else position)
        return
    if position == "start":
        chat_completion.insert_at_start(message, identifier)
    else:
        chat_completion.insert_at_end(message, identifier)


def _insert_at_start(chat_completion: Any, message: Any, identifier: str) -> None:
    """``insertAtStart`` (``openai.js:4042``) -- before the group's first message."""
    chat_completion.insert_at_start(message, identifier)


def _insert_at_end(chat_completion: Any, message: Any, identifier: str) -> None:
    """``insertAtEnd`` -- after the group's last message."""
    chat_completion.insert_at_end(message, identifier)


def _add(chat_completion: Any, item: Any, position: Any = None) -> None:
    """``chatCompletion.add(collection, index)`` (``openai.js:1207``).

    The reference ``ChatCompletion.add`` assigns the slot outright
    (``openai.js:4004-4006``): an index replaces whatever occupies it, and pads
    the array when it is past the end. The port does the same, so this adapter
    stays a straight call -- inserting instead of assigning shifts every
    following prompt and duplicates entries, which the assembly oracle rejects.
    """
    chat_completion.add(item, position)


# ---------------------------------------------------------------------------
# prompts / PromptCollection duck-typing adapters.
# ---------------------------------------------------------------------------


def _collection(prompts: Any) -> list[Any]:
    if isinstance(prompts, PromptCollection):
        return prompts.collection
    if isinstance(prompts, Mapping):
        return list(prompts.values())
    collection = getattr(prompts, "collection", None)
    if collection is not None:
        return list(collection)
    if isinstance(prompts, Sequence) and not isinstance(prompts, (str, bytes)):
        return list(prompts)
    return []


def _has(prompts: Any, identifier: str | None) -> bool:
    if isinstance(prompts, PromptCollection):
        return prompts.has(identifier)
    method = getattr(prompts, "has", None)
    if method is not None:
        return bool(method(identifier))
    return any(_get(prompt, "identifier") == identifier for prompt in _collection(prompts))


def _index(prompts: Any, identifier: str | None) -> int:
    if isinstance(prompts, PromptCollection):
        return prompts.index(identifier)
    method = getattr(prompts, "index", None)
    if method is not None:
        return int(method(identifier))
    for position, prompt in enumerate(_collection(prompts)):
        if _get(prompt, "identifier") == identifier:
            return position
    return -1


def _get_prompt(prompts: Any, identifier: str | None) -> Any:
    if isinstance(prompts, PromptCollection):
        return prompts.get(identifier)
    method = getattr(prompts, "get", None)
    if method is not None:
        return method(identifier)
    for prompt in _collection(prompts):
        if _get(prompt, "identifier") == identifier:
            return prompt
    return None


def _add_prompt(prompts: Any, prompt: Any) -> None:
    if isinstance(prompts, PromptCollection):
        prompts.add(prompt)
        return
    method = getattr(prompts, "add", None)
    if method is not None:
        method(prompt)
        return
    _collection(prompts).append(prompt)


def _set_prompt(prompts: Any, prompt: Any, position: int) -> None:
    if isinstance(prompts, PromptCollection):
        prompts.set(prompt, position)
        return
    method = getattr(prompts, "set", None)
    if method is not None:
        method(prompt, position)
        return
    _collection(prompts)[position] = prompt


def _override_prompt(prompts: Any, prompt: Any, position: int) -> None:
    if isinstance(prompts, PromptCollection):
        prompts.override(prompt, position)
        return
    method = getattr(prompts, "override", None)
    if method is not None:
        method(prompt, position)
        return
    _set_prompt(prompts, prompt, position)


async def _resolve_prompt_collection(
    prompts: Any,
    type: str | None,  # noqa: A002 - JS parameter name
    get_prompt_collection: Callable[..., Any] | None,
) -> Any:
    """``promptManager.getPromptCollection(type)`` (``openai.js:1470``).

    Accepted shapes, in order: an explicit ``get_prompt_collection`` callback, an
    already-built collection (``PromptCollection`` / mapping / sequence), or
    ``None`` -- which falls back to the canonical identifier list so the whole
    pipeline runs with no SillyTavern preset at all, the AstrBot default. A
    ``PromptCollection`` argument is deep-copied first, because this function
    edits the collection in place and the caller must not see those edits.
    """
    if get_prompt_collection is not None:
        result = await _maybe_await(get_prompt_collection(type))
        return result if result is not None else PromptCollection()
    if prompts is None:
        return PromptCollection(
            *(Prompt(identifier=item) for item in PROMPT_COLLECTION_IDENTIFIERS)
        )
    if isinstance(prompts, PromptCollection):
        return prompts.copy()
    if isinstance(prompts, Mapping):
        return PromptCollection(*prompts.values())
    if isinstance(prompts, Sequence) and not isinstance(prompts, (str, bytes)):
        return PromptCollection(*prompts)
    return PromptCollection(*_collection(prompts))


async def _prepare(prepare_prompt: Callable[..., Any] | None, prompt: Any, *args: Any) -> Any:
    """``promptManager.preparePrompt(prompt[, original])`` (``PromptManager.js:1277``).

    Defaults to the identity so macro substitution / regex rewriting stays in the
    caller's hands (AstrBot does it elsewhere, the browser does it here).
    """
    if prepare_prompt is None:
        return prompt
    return await _maybe_await(prepare_prompt(prompt, *args))
