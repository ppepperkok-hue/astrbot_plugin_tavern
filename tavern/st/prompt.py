"""SillyTavern compatible prompt assembly and history trimming.

This module is the *format* half of the plugin's SillyTavern compatibility
layer. Like :mod:`astrbot_plugin_tavern.st.cards` and
:mod:`astrbot_plugin_tavern.st.worldbook` it imports nothing from AstrBot and
nothing outside the standard library, so it can be unit tested offline.

Scope
-----
* :data:`DEFAULT_PROMPT_ORDER` -- the prompt block order copied verbatim from
  SillyTavern's ``default/content/presets/openai/Default.json``
  (``prompt_order``, ``character_id`` 100001 / persona aware variant).
* :func:`render_macro` -- ``{{char}}`` / ``{{user}}`` / time macros.
* :func:`build_messages` -- turn a character card, a prompt preset, the
  activated world info text and the chat history into one ordered list of
  chat messages.
* :func:`trim_history` / :func:`count_tokens` -- context budget helpers.

World info hand-off
-------------------
This module deliberately does **not** import
:mod:`astrbot_plugin_tavern.st.worldbook`: the caller runs the activation
engine, sorts the activated entries by ``position`` and hands the already
flattened text over through :class:`InChatTargets`. That keeps the two modules
independent (no import cycle) and keeps the "what is injected" decision in one
place.

Known deviations from SillyTavern are listed in the individual docstrings; the
important ones are repeated here:

* SillyTavern honours the ``role`` of each world info entry; the text only
  :class:`InChatTargets` protocol drops it, so every injection uses
  :data:`INJECTION_ROLE` (``system``).
* SillyTavern keeps its prompt *toggles* in ``prompt_order`` and the prompt
  *texts* in ``prompts``; :func:`preset_from_dict` reads the text array first
  and only consults ``prompt_order`` when explicitly asked to.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo
from typing import Any, Callable, Mapping, Protocol, Sequence

from tavern.st.cards import CharacterCard

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_DISABLED_BLOCKS",
    "DEFAULT_MAIN_PROMPT",
    "DEFAULT_PROMPT_ORDER",
    "INJECTION_ROLE",
    "MARKER_BLOCKS",
    "BuildResult",
    "ChatMessageLike",
    "InChatTargets",
    "PresetSpec",
    "PromptBlock",
    "PromptBlockSpec",
    "PromptMessage",
    "RenderOptions",
    "build_messages",
    "count_tokens",
    "default_preset",
    "parse_dialogue_examples",
    "preset_from_dict",
    "render_macro",
    "trim_history",
]

#: Prompt block order copied verbatim from SillyTavern ``Default.json``.
DEFAULT_PROMPT_ORDER: tuple[str, ...] = (
    "main",
    "worldInfoBefore",
    "personaDescription",
    "charDescription",
    "charPersonality",
    "scenario",
    "enhanceDefinitions",
    "nsfw",
    "worldInfoAfter",
    "dialogueExamples",
    "chatHistory",
    "jailbreak",
)

#: Blocks SillyTavern ships disabled: the persona slot is only used when a
#: persona is bound to the chat, and ``enhanceDefinitions`` is an opt-in
#: enhancement (``"enabled": false`` in ``Default.json``).
DEFAULT_DISABLED_BLOCKS: frozenset[str] = frozenset({"personaDescription", "enhanceDefinitions"})

#: Blocks whose content is computed from the card / chat instead of a static
#: preset string. SillyTavern marks them with ``marker: true``. Marker blocks
#: are still listed in :attr:`BuildResult.debug_blocks` when they stay empty.
MARKER_BLOCKS: frozenset[str] = frozenset(
    {
        "worldInfoBefore",
        "worldInfoAfter",
        "personaDescription",
        "charDescription",
        "charPersonality",
        "scenario",
        "dialogueExamples",
        "chatHistory",
    }
)

#: Default text of the ``main`` block. Kept deliberately generic (and original):
#: SillyTavern's own stock Main Prompt text is part of its AGPL licensed preset
#: content, so it is *not* shipped here. Point ``presets/`` at an exported
#: SillyTavern preset to use that project's own wording.
DEFAULT_MAIN_PROMPT = (
    "Write {{char}}'s next reply, staying in character as {{char}} "
    "in a scene together with {{user}}."
)

#: Role used for every in-chat injection (Author's Note, world info, examples).
INJECTION_ROLE = "system"

#: Roles :class:`PromptMessage` is allowed to carry.
VALID_ROLES: frozenset[str] = frozenset({"system", "user", "assistant"})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class PromptBlock:
    """One ordered prompt block before it is expanded into chat messages.

    ``content`` is the flattened text of the block (empty for skipped or
    disabled blocks) and ``marker`` mirrors
    :attr:`PromptBlockSpec.marker` so a debug dump can tell "empty because the
    card has no description" from "empty because the slot is a placeholder".
    """

    name: str
    content: str
    role: str | None = None
    marker: bool | None = None


@dataclass
class PromptBlockSpec:
    """One slot of a :class:`PresetSpec` order list.

    ``content`` is the static text of the slot. It is only used for blocks
    that have no better source (``main`` / ``nsfw`` / ``jailbreak`` fall back
    to the preset fields, ``charDescription`` etc. come from the card) and for
    custom identifiers that SillyTavern presets may add.
    """

    name: str
    enabled: bool = True
    marker: bool = False
    content: str = ""


@dataclass
class PresetSpec:
    """A SillyTavern style prompt preset.

    ``order`` is authoritative: blocks are emitted top to bottom and a block
    that is absent from ``order`` is never emitted, even when the card has
    content for it. ``names_as_prefix`` is the preset's declared value; the
    caller is expected to mirror it into :class:`RenderOptions`, which is what
    :func:`build_messages` actually reads.
    """

    order: list[PromptBlockSpec] = field(default_factory=list)
    main_prompt: str = ""
    nsfw_prompt: str = ""
    jailbreak_prompt: str = ""
    names_as_prefix: bool = True

    def block(self, name: str) -> PromptBlockSpec | None:
        """Return the first spec called ``name``, or ``None`` when absent."""
        for spec in self.order:
            if spec.name == name:
                return spec
        return None

    def names(self) -> list[str]:
        """Block names in send order (debug helper)."""
        return [spec.name for spec in self.order]


@dataclass
class RenderOptions:
    """Rendering knobs shared by :func:`render_macro` and :func:`build_messages`.

    ``now`` pins the clock for tests; when it is ``None`` the current UTC time
    is used. A naive ``now`` is assumed to already be in ``timezone_name``.
    ``timezone_name`` is resolved through :mod:`zoneinfo`; when the timezone
    database is unavailable (Windows without ``tzdata``) UTC is used.
    """

    username: str = "User"
    char_name: str = ""
    strip_leading_newlines: bool = True
    mes_example_separator: str = "<START>"
    names_as_prefix: bool = True
    timezone_name: str = "UTC"
    now: datetime | None = None


class ChatMessageLike(Protocol):
    """Structural type accepted anywhere a chat message is read.

    Only these three attributes are touched, so AstrBot message objects,
    dataclasses and :class:`types.SimpleNamespace` all work.
    """

    role: str
    content: str
    name: str | None


@dataclass
class PromptMessage:
    """One chat message handed to the model (``role`` in system/user/assistant)."""

    role: str
    content: str
    name: str | None = None


@dataclass
class BuildResult:
    """Result of :func:`build_messages`.

    ``debug_blocks`` lists **every** block of the preset in send order as
    ``(name, content)``, including disabled blocks and markers that produced
    nothing. It is meant for logging and tests, never for sending.
    """

    messages: list[PromptMessage] = field(default_factory=list)
    debug_blocks: list[tuple[str, str]] = field(default_factory=list)


_TARGET_FIELDS: tuple[str, ...] = (
    "at_depth",
    "an_top",
    "an_bottom",
    "em_top",
    "em_bottom",
    "before_char",
    "after_char",
)

#: Normalised key -> dataclass field, used by :meth:`InChatTargets.from_mapping`.
_TARGET_ALIASES: dict[str, str] = {
    "atdepth": "at_depth",
    "antop": "an_top",
    "anbottom": "an_bottom",
    "emtop": "em_top",
    "embottom": "em_bottom",
    "beforechar": "before_char",
    "worldinfobefore": "before_char",
    "afterchar": "after_char",
    "worldinfoafter": "after_char",
}


@dataclass
class InChatTargets:
    """World info text that is injected *inside* the chat history.

    The caller (``main.py``) runs :func:`worldbook.activate`, groups the
    activated entries by ``position`` and flattens each group to text; this
    module only decides *where* the text ends up.

    Fields, in SillyTavern ``position`` terms:

    ``at_depth``
        ``POSITION_AT_DEPTH``: inserted inside the history, at
        ``at_depth_before_index`` (see :func:`build_messages`).
    ``an_top`` / ``an_bottom``
        ``POSITION_ANT_TOP`` / ``POSITION_ANT_BOTTOM``: glued to the very
        beginning / end of the chat history block.
    ``em_top`` / ``em_bottom``
        ``POSITION_EM_TOP`` / ``POSITION_EM_BOTTOM``: wrap the example
        dialogue block.
    ``before_char`` / ``after_char``
        ``POSITION_BEFORE_CHAR`` / ``POSITION_AFTER_CHAR``: these are the
        ``worldInfoBefore`` / ``worldInfoAfter`` prompt *blocks* rather than
        in-chat injections, but SillyTavern stores them in the same activation
        result, so they live here too.

    Every field accepts a bare ``str`` or ``None`` as well (normalised in
    :meth:`__post_init__`), because a caller may hand over
    ``ActivationResult.content_for(position)`` directly.
    """

    at_depth: list[str] = field(default_factory=list)
    an_top: list[str] = field(default_factory=list)
    an_bottom: list[str] = field(default_factory=list)
    em_top: list[str] = field(default_factory=list)
    em_bottom: list[str] = field(default_factory=list)
    before_char: list[str] = field(default_factory=list)
    after_char: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for name in _TARGET_FIELDS:
            value = getattr(self, name)
            if value is None:
                setattr(self, name, [])
            elif isinstance(value, str):
                setattr(self, name, [value] if value else [])
            else:
                setattr(self, name, [str(item) for item in value if item is not None])

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> InChatTargets:
        """Build from a ``{position: content}`` mapping.

        Keys are matched case-insensitively and ignoring punctuation, so
        ``"worldInfoBefore"``, ``"world_info_before"`` and
        ``"world info before"`` all fill ``before_char``. Values may be a
        string or a sequence of strings; unknown keys are ignored.
        """
        collected: dict[str, list[str]] = {}
        for key, value in mapping.items():
            target = _TARGET_ALIASES.get(_normalise_key(key))
            if target is None:
                continue
            bucket = collected.setdefault(target, [])
            if isinstance(value, str):
                if value:
                    bucket.append(value)
            elif value is not None:
                bucket.extend(str(item) for item in value if item is not None)
        return cls(**collected)


# ---------------------------------------------------------------------------
# Macros
# ---------------------------------------------------------------------------

_MACRO_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_LEADING_NEWLINES = re.compile(r"^(?:[ \t]*\r?\n)+")


def _normalise_key(key: Any) -> str:
    """Lower-case a key and strip everything that is not ``[a-z0-9]``."""
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _as_text(value: Any) -> str:
    """Coerce a preset value to text; booleans count as "absent".

    SillyTavern exports use ``"system_prompt": true`` as a legacy marker in
    some presets, and that flag must not become the literal string ``"True"``.
    """
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, bool):
        return ""
    return str(value)


def _first_text(*values: Any) -> str:
    """Return the first non-blank text among ``values``."""
    for value in values:
        text = _as_text(value)
        if text.strip():
            return text
    return ""


def _as_bool(value: Any, default: bool) -> bool:
    """Parse a leniently typed boolean, falling back to ``default``."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def _resolve_timezone(name: str) -> tzinfo:
    """Resolve an IANA timezone name, falling back to UTC."""
    if not name or name.strip().upper() in ("UTC", "GMT", "Z"):
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception as exc:  # noqa: BLE001 - missing timezone database must not break a turn
        logger.debug("unknown timezone %r, falling back to UTC: %s", name, exc)
        return timezone.utc


def _resolve_now(options: RenderOptions) -> datetime:
    """Return ``options.now`` (or "now") in ``options.timezone_name``."""
    zone = _resolve_timezone(options.timezone_name)
    now = options.now
    if now is None:
        return datetime.now(zone)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)


def _macro_table(options: RenderOptions, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    now = _resolve_now(options)
    table = {
        "char": options.char_name,
        "user": options.username,
        "time": now.strftime("%H:%M"),
        "date": now.strftime("%Y-%m-%d"),
        "weekday": now.strftime("%A"),
        "isotime": now.strftime("%H:%M:%S"),
        "isodate": now.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if extra:
        for key, value in extra.items():
            table[_normalise_key(key)] = str(value)
    return table


def render_macro(
    text: str,
    options: RenderOptions,
    extra: dict[str, str] | None = None,
) -> str:
    """Replace SillyTavern style ``{{macro}}`` placeholders.

    Supported macros, looked up case-insensitively (``{{CHAR}}`` ==
    ``{{char}}``):

    ``{{char}}`` / ``{{user}}``
        ``char_name`` / ``username`` from ``options``.
    ``{{time}}`` / ``{{date}}`` / ``{{weekday}}``
        ``HH:MM``, ``YYYY-MM-DD`` and the English day name of
        ``options.now`` in ``options.timezone_name``.
    ``{{isotime}}`` / ``{{isodate}}``
        ISO 8601 ``HH:MM:SS`` / ``YYYY-MM-DDTHH:MM:SS`` (no microseconds, no
        offset -- SillyTavern's ``isodate`` family is a local wall clock).
    ``{{input}}``
        The current user input. This module never invents it: it is only
        substituted when the caller passes ``extra={"input": ...}``. Without
        it the placeholder is **kept verbatim**, exactly like an unknown macro.

    Any macro that is not in the table (including ``{{input}}`` without
    ``extra``) is left untouched, matching SillyTavern's "leave unknown
    placeholders alone" behaviour. Whitespace inside the braces is tolerated
    (``{{ char }}``). ``extra`` keys are normalised the same way, so
    ``{"Input": ...}`` also fills ``{{input}}``.
    """
    if not text or "{{" not in text:
        return text
    table = _macro_table(options, extra)

    def replace(match: re.Match[str]) -> str:
        value = table.get(match.group(1).lower())
        return match.group(0) if value is None else value

    return _MACRO_PATTERN.sub(replace, text)


def _strip_leading_newlines(text: str) -> str:
    """Drop leading blank lines (SillyTavern's "Strip leading newlines")."""
    return _LEADING_NEWLINES.sub("", text)


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


def _default_block_content(name: str) -> str:
    """Static text :func:`default_preset` ships for ``name``."""
    return DEFAULT_MAIN_PROMPT if name == "main" else ""


def default_preset(
    *,
    main_prompt: str = DEFAULT_MAIN_PROMPT,
    names_as_prefix: bool = True,
) -> PresetSpec:
    """Build the stock SillyTavern preset.

    The order is exactly :data:`DEFAULT_PROMPT_ORDER`; ``personaDescription``
    and ``enhanceDefinitions`` start disabled and every block listed in
    :data:`MARKER_BLOCKS` is flagged as a marker. ``main_prompt`` defaults to
    SillyTavern's stock ``Main Prompt`` text.
    """
    order = [
        PromptBlockSpec(
            name=name,
            enabled=name not in DEFAULT_DISABLED_BLOCKS,
            marker=name in MARKER_BLOCKS,
            content=_default_block_content(name),
        )
        for name in DEFAULT_PROMPT_ORDER
    ]
    return PresetSpec(
        order=order,
        main_prompt=main_prompt,
        nsfw_prompt="",
        jailbreak_prompt="",
        names_as_prefix=names_as_prefix,
    )


def preset_from_dict(payload: dict[str, Any], *, apply_prompt_order: bool = False) -> PresetSpec:
    """Parse a SillyTavern prompt preset export.

    Expected shape (the ``prompts`` array of ``Default.json`` and of any
    preset a user exports from the Prompt Manager)::

        {"prompts": [{"identifier": "main", "name": "Main Prompt",
                      "system_prompt": "Write {{char}}'s next reply...",
                      "role": "system", "marker": false}, ...]}

    Rules, in order:

    1. ``order`` follows the **array order** of ``prompts`` (SillyTavern's
       authoritative order actually lives in ``prompt_order``; see
       ``apply_prompt_order`` below).
    2. The block text is ``system_prompt``, falling back to ``content``.
       ``system_prompt``/``content`` may also be a legacy boolean flag, which
       counts as empty.
    3. A block is a *marker* when ``marker`` is truthy **or** its text is
       blank ("an empty static prompt is a placeholder").
    4. A marker defaults to ``enabled=False``; a normal prompt defaults to
       ``enabled=True``. An explicit ``enabled`` key always wins.
    5. ``main_prompt`` / ``nsfw_prompt`` / ``jailbreak_prompt`` are filled
       from the entries with those identifiers.

    ``apply_prompt_order=True`` additionally reads the ``prompt_order`` array
    (SillyTavern keeps the user's on/off toggles there) and overrides
    ``enabled`` for the identifiers it mentions. The array order of
    ``prompts`` is still what defines the send order, and rule 4 is what
    applies to identifiers missing from ``prompt_order`` -- so the default
    behaviour stays byte-for-byte predictable for the plugin layer.

    Raises:
        ValueError: when ``payload`` is not a mapping.
    """
    if not isinstance(payload, dict):
        raise ValueError("preset payload must be an object")

    raw_prompts = payload.get("prompts")
    prompts: list[Any] = raw_prompts if isinstance(raw_prompts, list) else []

    toggles: dict[str, bool] = {}
    if apply_prompt_order:
        toggles = _enabled_toggles(payload.get("prompt_order"))

    order: list[PromptBlockSpec] = []
    texts: dict[str, str] = {}
    for item in prompts:
        if not isinstance(item, dict):
            continue
        name = _first_text(item.get("identifier"), item.get("name"))
        if not name:
            continue
        text = _first_text(item.get("system_prompt"), item.get("content"))
        marker = _as_bool(item.get("marker"), False) or not text.strip()
        enabled = _as_bool(item.get("enabled"), not marker)
        if name in toggles:
            enabled = toggles[name]
        order.append(PromptBlockSpec(name=name, enabled=enabled, marker=marker, content=text))
        texts.setdefault(name, text)

    return PresetSpec(
        order=order,
        main_prompt=texts.get("main", ""),
        nsfw_prompt=texts.get("nsfw", ""),
        jailbreak_prompt=texts.get("jailbreak", ""),
        names_as_prefix=_as_bool(
            payload.get("names_as_prefix", payload.get("namesAsPrefix")),
            True,
        ),
    )


def _enabled_toggles(prompt_order: Any) -> dict[str, bool]:
    """Extract ``identifier -> enabled`` from a ``prompt_order`` array.

    SillyTavern stores one entry per ``character_id``; later entries win, and
    an entry whose ``order`` holds plain strings is skipped together with
    anything that has no explicit ``enabled`` flag.
    """
    toggles: dict[str, bool] = {}
    if not isinstance(prompt_order, list):
        return toggles
    for group in prompt_order:
        if not isinstance(group, dict):
            continue
        entries = group.get("order")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            identifier = _first_text(entry.get("identifier"), entry.get("name"))
            if identifier and "enabled" in entry:
                toggles[identifier] = _as_bool(entry.get("enabled"), True)
    return toggles


# ---------------------------------------------------------------------------
# Chat history helpers
# ---------------------------------------------------------------------------


def trim_history(
    history: Sequence[ChatMessageLike],
    max_tokens: int,
    token_counter: Callable[[str], int],
    *,
    keep_last: int = 1,
    reserve_tokens: int = 0,
) -> list[ChatMessageLike]:
    """Drop whole messages from the oldest end until the budget fits.

    Messages are never truncated in the middle and the relative order is
    preserved, so the result is always a suffix of ``history`` (oldest first).

    Args:
        history: Messages, oldest first.
        max_tokens: Context window available for the history.
        token_counter: ``counter(text) -> int``; :func:`count_tokens` by default
            in the plugin layer.
        keep_last: Minimum number of trailing messages that must survive even
            when they alone exceed the budget.
        reserve_tokens: Tokens reserved for the reply / system prompt; they are
            subtracted from ``max_tokens`` before any message is kept.

    Returns:
        The kept suffix, oldest first. An empty ``history`` yields ``[]``.
    """
    items = list(history)
    if not items:
        return []
    protected = max(1, int(keep_last))
    budget = max(0, int(max_tokens) - max(0, int(reserve_tokens)))
    counts = [max(0, int(token_counter(getattr(item, "content", "") or ""))) for item in items]

    first_protected = max(0, len(items) - protected)
    used = sum(counts[first_protected:])
    start = first_protected
    for index in range(first_protected - 1, -1, -1):
        if used + counts[index] > budget:
            # Oldest-first dropping: this message and everything older go.
            start = index + 1
            break
        used += counts[index]
        start = index
    return items[start:]


def count_tokens(text: str) -> int:
    """Count tokens, preferring ``tiktoken`` and degrading gracefully.

    Uses ``tiktoken.get_encoding("cl100k_base")`` when it can be imported and
    falls back to ``max(1, len(text) // 3)`` otherwise (or when the encoder
    throws), mirroring ``worldbook.greedy_token_count``. The encoding is
    cached in a module global, so the lookup cost is paid once per process.
    """
    if not text:
        return 0
    counter = _tiktoken_counter()
    if counter is not None:
        try:
            return len(counter(text))
        except Exception:  # noqa: BLE001 - counting must never break a turn
            pass
    return max(1, len(text) // 3)


_TIKTOKEN: Any = None
_TIKTOKEN_TRIED = False


def _tiktoken_counter() -> Any:
    global _TIKTOKEN, _TIKTOKEN_TRIED
    if _TIKTOKEN_TRIED:
        return _TIKTOKEN
    _TIKTOKEN_TRIED = True
    try:
        import tiktoken  # type: ignore[import-not-found]

        _TIKTOKEN = tiktoken.get_encoding("cl100k_base").encode
    except Exception:  # noqa: BLE001 - optional dependency
        _TIKTOKEN = None
    return _TIKTOKEN


def parse_dialogue_examples(
    text: str,
    options: RenderOptions,
    *,
    extra: dict[str, str] | None = None,
    separator: str | None = None,
    names_as_prefix: bool | None = None,
) -> list[PromptMessage]:
    """Split a ``mes_example`` blob into alternating user/assistant turns.

    ``{{user}}`` / ``{{char}}`` macros are rendered first, then the text is cut
    on ``separator`` (``options.mes_example_separator``, ``<START>`` by
    default) into example blocks. Inside a block, a line whose ``Name:`` prefix
    matches the rendered username becomes a ``user`` message and one matching
    the character name becomes an ``assistant`` message; the prefix itself is
    optional, and any line without a known prefix is glued onto the current
    turn (a block preamble is attached to its first turn).

    A block with no recognisable prefix at all is emitted as one ``assistant``
    message, and blocks that stay empty are dropped.

    ``names_as_prefix`` (default: ``options.names_as_prefix``) keeps the
    ``"Name: "`` prefix inside the emitted content; when ``False`` the prefix
    is stripped because the role already carries the speaker.
    """
    text = render_macro(text or "", options, extra)
    if not text.strip():
        return []

    cut = separator if separator is not None else options.mes_example_separator
    keep_names = options.names_as_prefix if names_as_prefix is None else names_as_prefix
    blocks = text.split(cut) if cut else [text]

    messages: list[PromptMessage] = []
    for block in blocks:
        lines = block.splitlines()
        pending: list[str] = []
        current_name: str | None = None
        current_lines: list[str] = []
        block_messages: list[PromptMessage] = []

        def flush() -> None:
            nonlocal current_name, current_lines
            if current_name is None:
                return
            content = "\n".join(current_lines)
            if options.strip_leading_newlines:
                content = _strip_leading_newlines(content)
            content = content.strip("\n")
            if content.strip():
                block_messages.append(
                    PromptMessage(
                        role=_role_for_name(current_name, options),
                        content=f"{current_name}: {content}" if keep_names else content,
                    )
                )
            current_name, current_lines = None, []

        for line in lines:
            speaker = _speaker_name(line, options)
            if speaker is None:
                (current_lines if current_name is not None else pending).append(line)
                continue
            flush()
            current_name = speaker
            current_lines = [*pending, line.partition(":")[2].lstrip()]
            pending = []
        flush()

        if not block_messages and any(line.strip() for line in lines):
            content = "\n".join(lines).strip("\n")
            if content.strip():
                block_messages.append(PromptMessage(role="assistant", content=content))
        messages.extend(block_messages)
    return messages


def _speaker_name(line: str, options: RenderOptions) -> str | None:
    """Return the speaker of a ``Name: text`` line when the name is known."""
    prefix, separator, _rest = line.partition(":")
    if not separator:
        return None
    name = prefix.strip()
    if not name or len(name) > 64:
        return None
    lowered = name.casefold()
    for candidate in (options.username, options.char_name):
        if candidate and lowered == candidate.casefold():
            return name
    return None


def _role_for_name(name: str, options: RenderOptions) -> str:
    """Map a rendered speaker name onto a chat role."""
    if options.username and name.casefold() == options.username.casefold():
        return "user"
    return "assistant"


# ---------------------------------------------------------------------------
# Message assembly
# ---------------------------------------------------------------------------


def build_messages(
    card: CharacterCard,
    preset: PresetSpec,
    target: InChatTargets | None,
    history: Sequence[ChatMessageLike],
    options: RenderOptions,
    *,
    persona: str = "",
    enhance_definitions: str = "",
    at_depth_before_index: int | None = None,
    extra_macros: dict[str, str] | None = None,
    squash_system: bool = True,
) -> BuildResult:
    """Assemble the messages sent to the model, following SillyTavern.

    ``preset.order`` decides everything:

    * ``main`` / ``worldInfoBefore`` / ``personaDescription`` /
      ``charDescription`` / ``charPersonality`` / ``scenario`` /
      ``enhanceDefinitions`` / ``nsfw`` / ``worldInfoAfter`` / ``jailbreak``
      are ``system`` text blocks, and consecutive system blocks are merged
      with ``\\n`` (SillyTavern's ``squash_system_messages``: the blocks carry
      no ``Name:`` prefixes, ``include_names=false`` style).
    * ``dialogueExamples`` becomes the ``<START>`` split user/assistant turns
      of ``card.mes_example``, wrapped by ``target.em_top`` /
      ``target.em_bottom``.
    * ``chatHistory`` becomes one message per :class:`ChatMessageLike`, wrapped
      by ``target.an_top`` / ``target.an_bottom``, with ``target.at_depth``
      injected at ``at_depth_before_index``.
    * Blocks with no content are skipped, ``enabled=False`` blocks are skipped
      without even rendering, and every block -- empty or not -- is still
      reported through :attr:`BuildResult.debug_blocks` so marker slots keep
      their position for debugging.

    Args:
        card: The character card supplying description/personality/scenario
            and the example dialogue. ``card.system_prompt`` is the fallback
            text of the ``main`` block, ``card.post_history_instructions`` the
            fallback of ``jailbreak``.
        preset: Send order and static prompt texts.
        target: Pre-flattened world info text per position; ``None`` means
            "nothing activated".
        history: Chat history, oldest first. Only ``role`` / ``content`` /
            ``name`` are read.
        options: Macro rendering options.
        persona: Content of the ``personaDescription`` block (the persona is
            not part of the character card).
        enhance_definitions: Content of the ``enhanceDefinitions`` block; the
            block stays empty unless the caller computes it.
        at_depth_before_index: Insertion point of ``target.at_depth`` inside
            ``history``: the injection goes immediately before
            ``history[at_depth_before_index]``. The caller derives it from the
            SillyTavern depth ("depth 0 = after the last message"); the value
            is clamped into ``[0, len(history)]``. ``None`` inserts before the
            whole history block, i.e. right after ``an_top``.
        extra_macros: Extra ``{{macro}}`` values, e.g. ``{"input": ...}``.
        squash_system: Merge consecutive ``system`` messages with ``\\n``.
            ``False`` keeps one message per block (needed when the backend
            rejects multiple/inline system messages differently).

    Returns:
        A :class:`BuildResult`; ``messages`` is the sendable list and
        ``debug_blocks`` the ordered ``(name, content)`` dump.
    """
    targets = target if target is not None else InChatTargets()
    macros = dict(extra_macros or {})

    rendered: list[tuple[PromptBlock, list[PromptMessage]]] = []
    for spec in preset.order:
        marker = spec.marker or spec.name in MARKER_BLOCKS
        if not spec.enabled:
            rendered.append((PromptBlock(name=spec.name, content="", role=None, marker=marker), []))
            continue
        if spec.name == "dialogueExamples":
            messages = _example_block_messages(card, targets, options, macros)
            content = "\n".join(message.content for message in messages)
            rendered.append((PromptBlock(spec.name, content, None, marker), messages))
            continue
        if spec.name == "chatHistory":
            messages = _history_block_messages(
                history, targets, options, macros, at_depth_before_index
            )
            content = "\n".join(message.content for message in messages)
            rendered.append((PromptBlock(spec.name, content, None, marker), messages))
            continue

        text = _block_text(spec, card, preset, targets, persona, enhance_definitions)
        text = render_macro(text, options, macros)
        if options.strip_leading_newlines:
            text = _strip_leading_newlines(text)
        if not text.strip():
            rendered.append((PromptBlock(spec.name, "", INJECTION_ROLE, marker), []))
            continue
        rendered.append(
            (
                PromptBlock(spec.name, text, INJECTION_ROLE, marker),
                [PromptMessage(role=INJECTION_ROLE, content=text)],
            )
        )

    messages: list[PromptMessage] = []
    for _block, block_messages in rendered:
        messages.extend(block_messages)
    if squash_system:
        messages = _squash_system_messages(messages)

    debug_blocks = [(block.name, block.content) for block, _messages in rendered]
    return BuildResult(messages=messages, debug_blocks=debug_blocks)


def _block_text(
    spec: PromptBlockSpec,
    card: CharacterCard,
    preset: PresetSpec,
    targets: InChatTargets,
    persona: str,
    enhance_definitions: str,
) -> str:
    """Static text of a non-composite block, before macro rendering."""
    name = spec.name
    if name == "main":
        return _first_text(preset.main_prompt, spec.content, card.system_prompt)
    if name == "worldInfoBefore":
        return "\n".join(targets.before_char)
    if name == "worldInfoAfter":
        return "\n".join(targets.after_char)
    if name == "personaDescription":
        return _first_text(persona, spec.content)
    if name == "charDescription":
        return card.description
    if name == "charPersonality":
        return card.personality
    if name == "scenario":
        return card.scenario
    if name == "enhanceDefinitions":
        return _first_text(enhance_definitions, spec.content)
    if name == "nsfw":
        return _first_text(preset.nsfw_prompt, spec.content)
    if name == "jailbreak":
        return _first_text(preset.jailbreak_prompt, spec.content, card.post_history_instructions)
    # Unknown identifier: whatever static text the preset carried.
    return spec.content


def _example_block_messages(
    card: CharacterCard,
    targets: InChatTargets,
    options: RenderOptions,
    macros: dict[str, str],
) -> list[PromptMessage]:
    """``dialogueExamples``: ``em_top`` + card examples + ``em_bottom``."""
    messages = _injection_messages(targets.em_top, options, macros)
    messages.extend(parse_dialogue_examples(card.mes_example, options, extra=macros))
    messages.extend(_injection_messages(targets.em_bottom, options, macros))
    return messages


def _history_block_messages(
    history: Sequence[ChatMessageLike],
    targets: InChatTargets,
    options: RenderOptions,
    macros: dict[str, str],
    at_depth_before_index: int | None,
) -> list[PromptMessage]:
    """``chatHistory``: ``an_top`` + history (+ ``at_depth``) + ``an_bottom``."""
    items = list(history)
    if at_depth_before_index is None:
        index = 0
    else:
        index = max(0, min(int(at_depth_before_index), len(items)))

    messages = _injection_messages(targets.an_top, options, macros)
    messages.extend(_history_messages(items[:index], options, macros))
    messages.extend(_injection_messages(targets.at_depth, options, macros))
    messages.extend(_history_messages(items[index:], options, macros))
    messages.extend(_injection_messages(targets.an_bottom, options, macros))
    return messages


def _history_messages(
    items: Sequence[ChatMessageLike],
    options: RenderOptions,
    macros: dict[str, str],
) -> list[PromptMessage]:
    """Render history entries, dropping empty ones and normalising roles."""
    messages: list[PromptMessage] = []
    for item in items:
        content = render_macro(getattr(item, "content", "") or "", options, macros)
        if not content.strip():
            continue
        role = str(getattr(item, "role", "") or "").strip().lower()
        if role not in VALID_ROLES:
            role = "user"
        name = getattr(item, "name", None) or None
        messages.append(PromptMessage(role=role, content=content, name=name))
    return messages


def _injection_messages(
    texts: Sequence[str],
    options: RenderOptions,
    macros: dict[str, str],
) -> list[PromptMessage]:
    """One system message per injected text (one world info entry each)."""
    messages: list[PromptMessage] = []
    for text in texts:
        rendered = render_macro(text or "", options, macros)
        if options.strip_leading_newlines:
            rendered = _strip_leading_newlines(rendered)
        if rendered.strip():
            messages.append(PromptMessage(role=INJECTION_ROLE, content=rendered))
    return messages


def _squash_system_messages(messages: list[PromptMessage]) -> list[PromptMessage]:
    """Merge runs of consecutive ``system`` messages with ``\\n``."""
    merged: list[PromptMessage] = []
    for message in messages:
        if (
            merged
            and message.role == INJECTION_ROLE
            and merged[-1].role == INJECTION_ROLE
            and message.content.strip()
        ):
            merged[-1] = PromptMessage(
                role=INJECTION_ROLE,
                content=f"{merged[-1].content}\n{message.content}",
            )
            continue
        merged.append(message)
    return merged
