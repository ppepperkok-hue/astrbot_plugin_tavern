# Ported from SillyTavern - public/scripts/world-info.js (class WorldInfoBuffer, lines 199-478)
# Copyright (C) 2024 SillyTavern contributors
# Licensed under the GNU Affero General Public License v3.0.
# This file is a modified Python translation; modified on 2026-10-07.
# Upstream: https://github.com/SillyTavern/SillyTavern (commit 06bde939)
"""Structural mirror of SillyTavern's ``WorldInfoBuffer``.

One instance represents *one* evaluation of World Info (``world-info.js:199``).
It owns the chat scan window and returns the haystack every key is matched
against, so it is the single place where the "what text can a key see" rule
lives.

Faithful details that look like accidents but are on purpose:

* ``MATCHER`` is ``'\\x01'`` and ``JOINER`` is ``'\\n' + MATCHER``, so the
  haystack **starts with** ``'\\x01'`` and a key can never match across two
  messages (``world-info.js:295-297``).
* That leading ``'\\x01'`` also means a ``^``-anchored regex key never matches.
  SillyTavern behaves this way; do not "fix" it (``world-info.js:297``).
* ``MIN_ACTIVATIONS`` passes deliberately skip the recursion buffer
  (``world-info.js:322-325``).

Deliberate divergences from the JavaScript (all forced by the host language or
by the fact that this module is a dependency-free leaf):

* The class is *not* exported from ``tavern/st/__init__.py``; import the module
  directly.
* ``entry`` is a plain ``dict`` of SillyTavern field names instead of a JS
  object, and ``WorldInfoBuffer.externalActivations`` is
  ``WorldInfoBuffer.external_activations``.
* The constructor is written ``WorldInfoBuffer(messages, global_scan_data=...)``
  with both halves optional so the buffer can be built incrementally;
  ``world-info.js:240`` requires both arguments.
* The globals ``world_info_depth`` and ``world_info_case_sensitive`` are read
  from :class:`WorldInfoBufferConfig` instead of module-level variables.
* ``parseRegexFromString`` / ``escapeRegex`` (``world-info.js:2901``) are
  reimplemented here so the module stays import-free. The Python flag set cannot
  express ``g`` / ``y`` (JS-only), so those are accepted silently while ``u``
  and ``s`` keep their Python meaning.
* ``re.error`` replaces ``RegExp`` construction failures; Python rejects a few
  patterns JavaScript accepts (e.g. ``a{2,1}``) and vice versa (e.g. ``(?<name>)``
  is a lookbehind in JavaScript and a group name in Python).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "JOINER",
    "MATCHER",
    "MAX_SCAN_DEPTH",
    "SCAN_STATE_INITIAL",
    "SCAN_STATE_MIN_ACTIVATIONS",
    "SCAN_STATE_NONE",
    "SCAN_STATE_RECURSION",
    "WORLD_INFO_LOGIC_AND_ALL",
    "WORLD_INFO_LOGIC_AND_ANY",
    "WorldInfoBuffer",
    "WorldInfoBufferConfig",
    "build_scan_data",
    "default_global_scan_data",
    "parse_regex_from_string",
    "scan_state",
    "world_info_logic",
]

# --- Constants (world-info.js) ----------------------------------------------

# world-info.js:33-38
world_info_logic: dict[str, int] = {
    "AND_ANY": 0,
    "NOT_ALL": 1,
    "NOT_ANY": 2,
    "AND_ALL": 3,
}

# world-info.js:43-60
scan_state: dict[str, int] = {
    "NONE": 0,
    "INITIAL": 1,
    "RECURSION": 2,
    "MIN_ACTIVATIONS": 3,
}

#: Convenience aliases; :attr:`scan_state` stays the canonical mapping.
SCAN_STATE_NONE = scan_state["NONE"]
SCAN_STATE_INITIAL = scan_state["INITIAL"]
SCAN_STATE_RECURSION = scan_state["RECURSION"]
SCAN_STATE_MIN_ACTIVATIONS = scan_state["MIN_ACTIVATIONS"]

WORLD_INFO_LOGIC_AND_ANY = world_info_logic["AND_ANY"]
WORLD_INFO_LOGIC_AND_ALL = world_info_logic["AND_ALL"]

#: world-info.js:98 -- hard ceiling of the scan window.
MAX_SCAN_DEPTH = 1000

#: world-info.js:295-296 -- segment separator, kept byte for byte.
MATCHER = "\x01"
JOINER = "\n" + MATCHER

#: world-info.js:186-194
default_global_scan_data: dict[str, str] = {
    "trigger": "normal",
    "personaDescription": "",
    "characterDescription": "",
    "characterPersonality": "",
    "characterDepthPrompt": "",
    "scenario": "",
    "creatorNotes": "",
}

_GLOBAL_SCAN_DATA_KEYS = tuple(default_global_scan_data)
_REGEX_KEY = re.compile(r"^\/([\w\W]+?)\/([gimsuy]*)$")
_UNESCAPED_SLASH = re.compile(r"(^|[^\\])/")
_REGEX_SPECIALS = re.compile(r"[.*+?^${}()|\[\]\\]")
_FLAG_CHARS = re.compile(r"[a-zA-Z]")
_WHITESPACE = re.compile(r"\s+")

_REGEX_FLAGS: dict[str, int] = {
    "i": re.IGNORECASE,
    "m": re.MULTILINE,
    "s": re.DOTALL,
    "u": re.UNICODE,
}
#: Accepted by the JS engine but meaningless / unsupported in Python.
_REGEX_FLAGS_IGNORED = frozenset("gyA")


def escape_regex(text: str) -> str:
    """Port of ``escapeRegex`` (``utils.js``), used by ``matchKeys``."""
    return _REGEX_SPECIALS.sub(r"\\\g<0>", str(text))


def parse_regex_from_string(text: str) -> re.Pattern[str] | None:
    """Port of ``parseRegexFromString`` (``world-info.js:2901-2926``).

    Returns ``None`` for anything that is not a valid ``/pattern/flags`` key,
    including patterns carrying an unescaped ``/`` delimiter
    (``world-info.js:2913``).
    """
    match = _REGEX_KEY.match(str(text))
    if not match:
        return None

    pattern, flags = match.group(1), match.group(2)

    # JS does not care about delimiters inside the pattern, but the engine
    # refuses keys that other engines could not parse back.
    if _UNESCAPED_SLASH.search(pattern):
        return None

    pattern = pattern.replace("\\/", "/")

    compiled = 0
    for letter in flags:
        if letter in _REGEX_FLAGS:
            compiled |= _REGEX_FLAGS[letter]
        elif letter not in _REGEX_FLAGS_IGNORED:
            return None

    try:
        return re.compile(pattern, compiled)
    except re.error:
        return None


def build_scan_data(global_scan_data: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Normalise a partial ``WIGlobalScanData`` against the engine defaults.

    Missing keys become ``''`` (the values :data:`default_global_scan_data` uses
    for every text field), so callers may pass only what they actually have.
    ``None`` values are coerced to ``''`` as well: a falsy text is skipped by
    ``get()`` anyway, but an explicit ``None`` would break the string
    concatenation.
    """
    data: dict[str, str] = dict(default_global_scan_data)
    if global_scan_data:
        for key in _GLOBAL_SCAN_DATA_KEYS:
            if key in global_scan_data:
                value = global_scan_data[key]
                data[key] = "" if value is None else str(value)
    return data


class WorldInfoBufferConfig:
    """Mutable stand-in for the module-level ``world_info_*`` globals.

    Only the two knobs ``WorldInfoBuffer`` actually reads are modelled:
    ``world_info_depth`` (``world-info.js:69``) and
    ``world_info_case_sensitive`` (``world-info.js:77``). The engine treats them
    as process-wide variables that the settings UI overwrites; here the caller
    owns one instance so tests and AstrBot sessions stay isolated.

    ``start_depth`` exists because ``#startDepth`` (``world-info.js:233``) has no
    public setter -- ``checkWorldInfo`` only ever mutates ``#skew``.
    """

    __slots__ = ("case_sensitive", "default_scan_depth", "match_whole_words", "start_depth")

    def __init__(
        self,
        default_scan_depth: int = 2,
        case_sensitive: bool = False,
        start_depth: int = 0,
        match_whole_words: bool = False,
    ) -> None:
        #: ``world_info_depth`` (world-info.js:69)
        self.default_scan_depth = default_scan_depth
        #: ``world_info_case_sensitive`` (world-info.js:77)
        self.case_sensitive = case_sensitive
        #: ``world_info_match_whole_words`` (world-info.js:78)
        self.match_whole_words = match_whole_words
        #: ``#startDepth`` (world-info.js:233)
        self.start_depth = start_depth


class WorldInfoBuffer:
    """Scanning buffer for one evaluation of World Info.

    Mirrors ``class WorldInfoBuffer`` at ``world-info.js:199-474``.
    """

    #: world-info.js:203 -- ``${world}.${uid}`` of entries forced active from
    #: outside the scan (``world-info.js:1025``).
    external_activations: dict[str, Any] = {}

    def __init__(
        self,
        messages: Sequence[str] | None = None,
        global_scan_data: Mapping[str, Any] | None = None,
        config: WorldInfoBufferConfig | None = None,
    ) -> None:
        self.config = config if config is not None else WorldInfoBufferConfig()
        # world-info.js:208
        self._global_scan_data = build_scan_data(global_scan_data)
        # world-info.js:213
        self._depth_buffer: list[str] = []
        # world-info.js:218
        self._recurse_buffer: list[str] = []
        # world-info.js:223
        self._inject_buffer: list[str] = []
        # world-info.js:228
        self._skew = 0
        # world-info.js:233
        self._start_depth = int(self.config.start_depth)
        self.init_depth_buffer(messages)

    # world-info.js:250-260
    def init_depth_buffer(self, messages: Sequence[str] | None) -> None:
        """Populate the buffer with the given messages (``#initDepthBuffer``).

        The ``depth === messages.length - 1`` break exists in the engine too:
        an explicit ``None``/empty message *before* the end of the chat is kept
        as ``''`` at its depth, it just does not accept new ones.
        """
        messages = messages or []
        self._depth_buffer = []
        for depth in range(MAX_SCAN_DEPTH):
            if depth < len(messages) and messages[depth]:
                self._depth_buffer.append(str(messages[depth]).strip())
            else:
                self._depth_buffer.append("")
            # break if last message is reached
            if depth == len(messages) - 1:
                break

    # world-info.js:268-271
    def _transform_string(self, text: str, entry: Mapping[str, Any]) -> str:
        """Lower-case ``text`` unless the entry (or the config) is case sensitive."""
        case_sensitive = entry.get("caseSensitive")
        if case_sensitive is None:
            case_sensitive = self.config.case_sensitive
        return str(text) if case_sensitive else str(text).lower()

    # world-info.js:279-328
    def get(self, entry: Mapping[str, Any], scan_state_value: int = SCAN_STATE_INITIAL) -> str:
        """Return the haystack for ``entry``, sliced to its scan depth.

        Branch order is copied verbatim: ``scanDepth ?? getDepth()``, then the
        ``<= startDepth`` early return, then the negative guard, then the
        ``MAX_SCAN_DEPTH`` truncation.
        """
        depth = entry.get("scanDepth")
        if depth is None:
            depth = self.get_depth()

        if depth <= self._start_depth:
            return ""

        if depth < 0:
            logger.error("[WI] Invalid WI scan depth %s. Must be >= 0", depth)
            return ""

        if depth > MAX_SCAN_DEPTH:
            logger.warning("[WI] Invalid WI scan depth %s. Truncating to %s", depth, MAX_SCAN_DEPTH)
            depth = MAX_SCAN_DEPTH

        scan_data = self._global_scan_data
        result = MATCHER + JOINER.join(self._depth_buffer[self._start_depth : depth])

        if entry.get("matchPersonaDescription") and scan_data["personaDescription"]:
            result += JOINER + scan_data["personaDescription"]
        if entry.get("matchCharacterDescription") and scan_data["characterDescription"]:
            result += JOINER + scan_data["characterDescription"]
        if entry.get("matchCharacterPersonality") and scan_data["characterPersonality"]:
            result += JOINER + scan_data["characterPersonality"]
        if entry.get("matchCharacterDepthPrompt") and scan_data["characterDepthPrompt"]:
            result += JOINER + scan_data["characterDepthPrompt"]
        if entry.get("matchScenario") and scan_data["scenario"]:
            result += JOINER + scan_data["scenario"]
        if entry.get("matchCreatorNotes") and scan_data["creatorNotes"]:
            result += JOINER + scan_data["creatorNotes"]

        if self._inject_buffer:
            result += JOINER + JOINER.join(self._inject_buffer)

        # Min activations should not include the recursion buffer
        if self._recurse_buffer and scan_state_value != SCAN_STATE_MIN_ACTIVATIONS:
            result += JOINER + JOINER.join(self._recurse_buffer)

        return result

    # world-info.js:337-366
    def match_keys(self, haystack: str, needle: str, entry: Mapping[str, Any]) -> bool:
        """Match one key against ``haystack``, regex keys first."""
        # If the needle is a regex, we do regex pattern matching and override all
        # the other options
        key_regex = parse_regex_from_string(needle)
        if key_regex:
            return key_regex.search(haystack) is not None

        # Otherwise we do normal matching of plaintext with the chosen entry settings
        haystack = self._transform_string(haystack, entry)
        transformed_string = self._transform_string(needle, entry)
        match_whole_words = entry.get("matchWholeWords")
        if match_whole_words is None:
            # ``entry.matchWholeWords ?? world_info_match_whole_words``
            match_whole_words = self.config.match_whole_words

        if match_whole_words:
            key_words = _WHITESPACE.split(transformed_string)

            if len(key_words) > 1:
                return transformed_string in haystack

            # Use custom boundaries to include punctuation and other
            # non-alphanumeric characters.
            #
            # ``re.ASCII`` is *required*, and was missing here while the
            # unreachable twin in ``wi_keywords.py`` had it right. JS ``\w`` /
            # ``\W`` are ASCII-only in every mode (``u`` included -- it only adds
            # ``\p{...}``), whereas Python's ``\w`` is Unicode-aware for ``str``
            # patterns. With ``re.UNICODE`` the neighbours of a CJK key are word
            # characters, so `关键词` inside `中文关键词测试` stops matching, while
            # the reference matches it. Oracle fixture ``15-word-boundaries`` pins
            # exactly that pair (and the ASCII cases that already agreed).
            #
            # KNOWN-ISSUE: 3 -- changing this to `re.UNICODE` breaks every Chinese
            # keyword under whole-word matching; fixture 15 goes red when you do.
            regex = re.compile(f"(?:^|\\W)({escape_regex(transformed_string)})(?:$|\\W)", re.ASCII)
            if regex.search(haystack):
                return True
        else:
            return transformed_string in haystack

        return False

    # world-info.js:372-374
    def add_recurse(self, message: str) -> None:
        """Add a message to the recursion buffer."""
        self._recurse_buffer.append(message)

    # world-info.js:380-382
    def add_inject(self, message: str) -> None:
        """Add an injection to the inject buffer."""
        self._inject_buffer.append(message)

    # world-info.js:388-390
    def has_recurse(self) -> bool:
        """True when the recursion buffer is not empty."""
        return len(self._recurse_buffer) > 0

    # world-info.js:395-397
    def advance_scan(self) -> None:
        """Increment skew to advance the scan range."""
        self._skew += 1

    # world-info.js:402-404
    def get_depth(self) -> int:
        """Settings' depth + current skew."""
        return self.config.default_scan_depth + self._skew

    # world-info.js:411-413
    def get_externally_activated(self, entry: Mapping[str, Any]) -> Any:
        """The externally activated version of the entry, if there is one."""
        key = f"{entry.get('world')}.{entry.get('uid')}"
        return WorldInfoBuffer.external_activations.get(key)

    # world-info.js:418-420
    @classmethod
    def reset_external_effects(cls) -> None:
        """Clean up the external effects for entries."""
        cls.external_activations = {}

    # world-info.js:428-473
    def get_score(self, entry: Mapping[str, Any], scan_state_value: int) -> int:
        """Number of key activations for ``entry`` (used by group scoring)."""
        buffer_state = self.get(entry, scan_state_value)
        number_of_primary_keys = 0
        number_of_secondary_keys = 0
        primary_score = 0
        secondary_score = 0

        # Increment score for every key found in the buffer
        primary_keys = entry.get("key")
        if isinstance(primary_keys, (list, tuple)):
            number_of_primary_keys = len(primary_keys)
            for key in primary_keys:
                if self.match_keys(buffer_state, key, entry):
                    primary_score += 1

        # Increment score for every secondary key found in the buffer
        secondary_keys = entry.get("keysecondary")
        if isinstance(secondary_keys, (list, tuple)):
            number_of_secondary_keys = len(secondary_keys)
            for key in secondary_keys:
                if self.match_keys(buffer_state, key, entry):
                    secondary_score += 1

        # No keys == no score
        if not number_of_primary_keys:
            return 0

        # Only positive logic influences the score
        if number_of_secondary_keys > 0:
            logic = entry.get("selectiveLogic")
            if logic == WORLD_INFO_LOGIC_AND_ANY:
                # AND_ANY: Add both scores
                return primary_score + secondary_score
            if logic == WORLD_INFO_LOGIC_AND_ALL:
                # AND_ALL: Add both scores if all secondary keys are found,
                # otherwise only primary score
                if secondary_score == number_of_secondary_keys:
                    return primary_score + secondary_score
                return primary_score

        return primary_score
