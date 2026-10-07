# Ported from SillyTavern - public/scripts/world-info.js
# (functions: splitKeywordsAndRegexes @2797-2815, customTokenizer @2825-2878,
#  isValidRegex @2888-2890, parseRegexFromString @2901-2926,
#  WorldInfoBuffer.#transformString @268-271, WorldInfoBuffer.matchKeys @337-366;
#  helper escapeRegex @public/scripts/utils.js:14)
# Copyright (C) 2024 SillyTavern contributors
# Licensed under the GNU Affero General Public License v3.0.
# This file is a modified Python translation; modified on 2026-10-07.
# Upstream: https://github.com/SillyTavern/SillyTavern (commit 06bde939)

"""SillyTavern keyword parsing and matching, mirrored function by function.

Four JS units live here, in their original order and with their original
branching:

``splitKeywordsAndRegexes`` (:2797) / ``customTokenizer`` (:2825)
    Split one comma separated key string into keyword and ``/pattern/flags``
    tokens. The tokenizer is a two-flag state machine (``insideRegex`` /
    ``regexClosed``) that tolerates half-typed regexes and resets its index
    after every comma -- including the JS ``for``-loop quirk where the index
    reset is followed by ``i++``, so the *second* character of the remaining
    input is examined next. That quirk is reproduced on purpose.

``isValidRegex`` (:2888) / ``parseRegexFromString`` (:2901)
    ``/pattern/flags`` -> Python ``re.Pattern`` (the Python stand-in for the JS
    ``RegExp``), or ``None`` for every branch where JS returns ``null`` or
    catches a constructor error.

``WorldInfoBuffer.#transformString`` (:268) / ``WorldInfoBuffer.matchKeys`` (:337)
    Case sensitivity plus the whole-word rule: a **multi-word** key degrades to
    a plain substring test with *no* boundary check, and only a single-token key
    gets ``(?:^|\\W)(key)(?:$|\\W)``. ``C++``, ``A-1`` and ``foo.bar`` therefore
    behave differently from ``tavern/st/worldbook.py::_whole_word_match``, which
    classifies by "is every character a word character" instead.

    The boundary class is compiled with ``re.ASCII`` on purpose: JS ``\\w`` /
    ``\\W`` are ``[A-Za-z0-9_]`` based in *every* mode (the ``u`` flag only adds
    ``\\p{...}``), while Python's ``\\w`` is Unicode-aware for ``str`` patterns.
    Without it ``关键词`` inside ``中文关键词测试`` would stop matching, because
    the neighbouring ideograph counts as a word character in Python and not in JS.
    The same reasoning applies to the ``split(/\\s+/)`` that decides "multi-word",
    which uses the ECMA-262 whitespace class (:data:`_JS_WHITESPACE`) rather than
    Python's ``\\s``.

Adaptations to Python (no branching above is altered):

* the JS globals ``world_info_case_sensitive`` (:77) and
  ``world_info_match_whole_words`` (:78) are module level variables here; every
  lookup keeps JS's ``??`` semantics (an explicit ``false`` on the entry wins
  over a ``true`` global), and the keyword-only arguments of :func:`match_keys`
  let the engine pass its own config instead of mutating the globals.
* JS ``entry.caseSensitive`` / ``entry.matchWholeWords`` are read from a mapping
  using the ST camelCase name first, then the project's snake_case name, so both
  a raw ST entry dict and :class:`tavern.st.worldbook.WorldInfoEntry` work.
* ``getSelect2OptionId`` is dropped: ``splitKeywordsAndRegexes`` only keeps
  ``item.text`` and never reads the id (see :func:`custom_tokenizer`).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

#: JS ``world_info_case_sensitive`` (world-info.js:77).
world_info_case_sensitive: bool = False
#: JS ``world_info_match_whole_words`` (world-info.js:78).
world_info_match_whole_words: bool = False

#: Flag characters the slash-delimited parser accepts (world-info.js:2903).
JS_REGEX_FLAG_CHARS = "gimsuy"
#: Flags JS accepts but Python ``re`` cannot express: ``g`` (global, stateful
#: ``test``) and ``y`` (sticky, anchored ``test``). They are still accepted, so
#: "is this a valid regex" answers exactly like the engine; ``g`` is a no-op
#: (the engine builds a fresh ``RegExp`` per ``matchKeys`` call, so ``lastIndex``
#: never survives) and ``y`` degrades to a non-anchored search.
JS_ONLY_REGEX_FLAGS = frozenset("gy")

_PY_REGEX_FLAGS: dict[str, int] = {
    "i": re.IGNORECASE,
    "m": re.MULTILINE,
    "s": re.DOTALL,
    "u": re.UNICODE,
    "g": 0,
    "y": 0,
}

#: JS ``/^\/([\w\W]+?)\/([gimsuy]*)$/`` (world-info.js:2903). ``\Z`` replaces
#: ``$`` because JS ``$`` only matches at the very end of the input, while Python
#: ``$`` also matches just before a trailing newline.
_REGEX_DELIMITED = re.compile(r"^/([\w\W]+?)/([gimsuy]*)\Z")
#: JS ``/(^|[^\\])\//`` (world-info.js:2913): an unescaped slash inside the pattern.
_UNESCAPED_SLASH = re.compile(r"(^|[^\\])/")
#: JS ``String.replace('\\/', '/')`` replaces the *first* occurrence only.
_ESCAPED_SLASH = "\\/"

#: JS ``escapeRegex`` (public/scripts/utils.js:14):
#: ``String(s ?? '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&')``.
#: Note that ``/`` is *not* in the set -- that is the upstream behaviour.
_REGEX_META = re.compile(r"[.*+?^${}()|\[\]\\]")

#: JS ``\s`` (ECMA-262 *WhiteSpace* + *LineTerminator*). It is **not** Python's
#: ``\s`` for ``str``: the engine also treats U+FEFF as whitespace, and Python
#: additionally treats U+0085 / U+001C-U+001F as whitespace. Used for the
#: ``split(/\s+/)`` that decides whether a key counts as multi-word.
_JS_WHITESPACE = re.compile(
    "[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+"
)


def escape_regex(value: Any) -> str:
    """Escape every regex metacharacter of ``value`` (JS ``escapeRegex``)."""
    return _REGEX_META.sub(r"\\\g<0>", "" if value is None else str(value))


def parse_regex_from_string(input: str) -> re.Pattern[str] | None:
    """JS ``parseRegexFromString`` (world-info.js:2901-2926).

    Returns the compiled regex, or ``None`` for each ``return null`` branch:
    a string that is not delimited, a pattern holding an unescaped ``/``, a
    pattern Python cannot compile, or repeated flags (JS's ``RegExp`` constructor
    throws on ``'ii'`` and the ``catch`` returns ``null``).
    """
    # Extracting the regex pattern and flags
    match = _REGEX_DELIMITED.match(input)
    if not match:
        return None  # Not a valid regex format

    pattern, flags = match.group(1), match.group(2)

    # If we find any unescaped slash delimiter, we also exit out.
    if _UNESCAPED_SLASH.search(pattern):
        return None

    # Now we need to actually unescape the slash delimiters, because JS doesn't
    # care about delimiters. (JS ``replace`` with a string: first hit only.)
    pattern = pattern.replace(_ESCAPED_SLASH, "/", 1)

    # Then we return the regex. If it fails, it was invalid syntax.
    if len(set(flags)) != len(flags):
        return None  # JS: new RegExp(pattern, 'ii') throws -> caught -> null
    python_flags = 0
    for flag in flags:
        python_flags |= _PY_REGEX_FLAGS[flag]
    try:
        return re.compile(pattern, python_flags)
    except re.error:
        return None


def is_valid_regex(input: str) -> bool:
    """JS ``isValidRegex`` (world-info.js:2888-2890)."""
    return parse_regex_from_string(input) is not None


def custom_tokenizer(term: str, callback: Callable[[str], None]) -> str:
    """JS ``customTokenizer`` (world-info.js:2825-2878).

    ``input.term`` becomes ``term`` and the callback receives ``item.text``; the
    select2 ``id`` of the JS item is never read by the caller, so it is dropped.
    Returns the left-over, still untokenized input, exactly like JS returns
    ``{term: current}``.
    """
    current = term

    inside_regex = False
    regex_closed = False

    # Go over the input and check the current state, if we can get a token.
    # NB: JS uses ``for (let i = 0; ...; i++)`` and sets ``i = 0`` after a comma,
    # so the increment makes the *second* character of the shortened string the
    # next one inspected. ``i += 1`` at the end of the body reproduces that.
    i = 0
    while i < len(current):
        char = current[i]

        # If we find an unescaped slash, set the current regex state
        if char == "/" and (i == 0 or current[i - 1] != "\\"):
            if not inside_regex:
                inside_regex = True
            elif not regex_closed:
                regex_closed = True

        # If a comma is typed, we tokenize the input, unless we are inside a
        # possible regex, which would allow commas inside.
        if char == ",":
            # We take everything up till now and consider this a token
            token = current[:i].strip()

            # If the delimiter was opened but not closed yet, the comma belongs
            # to a half-finished regex: keep it. Validity is checked later.
            if inside_regex and not regex_closed:
                i += 1
                continue

            # So now the comma really means the token is done. Empty is skipped.
            if token:
                is_regex = is_valid_regex(token)

                # Last chance to check for a valid regex again. Because it might
                # have been valid while typing, but now is not valid anymore and
                # contains commas we need to split.
                if token.startswith("/") and not is_regex:
                    for part in token.split(","):
                        callback(part.strip())
                else:
                    callback(token)

            # Now remove the token from the current input, and the comma too
            current = current[i + 1 :]
            inside_regex = False
            regex_closed = False
            i = 0

        i += 1

    # At the end, just return the left-over input
    return current


def split_keywords_and_regexes(input: str) -> list[str]:
    """JS ``splitKeywordsAndRegexes`` (world-info.js:2797-2815).

    The trailing, still unterminated token is *not* validated -- JS pushes
    ``finalTerm`` as-is, so a half-typed regex survives as a plain keyword.
    """
    keywords_and_regexes: list[str] = []

    def add_find_callback(item_text: str) -> None:
        keywords_and_regexes.append(item_text)

    term = custom_tokenizer(input, add_find_callback)
    final_term = term.strip()
    if final_term:
        add_find_callback(final_term)

    return keywords_and_regexes


def _entry_field(entry: Any, *names: str) -> Any:
    """Read the first present field, ST camelCase name first, then snake_case.

    Missing or ``None`` means "unset" so the caller can apply JS's ``??``.
    """
    if entry is None:
        return None
    if isinstance(entry, Mapping):
        for name in names:
            if name in entry:
                return entry[name]
        return None
    for name in names:
        if hasattr(entry, name):
            return getattr(entry, name)
    return None


def transform_string(text: str, entry: Any = None, *, case_sensitive: bool | None = None) -> str:
    """JS ``WorldInfoBuffer.#transformString`` (world-info.js:268-271).

    ``entry.caseSensitive ?? world_info_case_sensitive``; pass ``case_sensitive``
    to use the engine's own config instead of the module global.
    """
    if case_sensitive is None:
        case_sensitive = _entry_field(entry, "caseSensitive", "case_sensitive")
    if case_sensitive is None:
        case_sensitive = world_info_case_sensitive
    return text if case_sensitive else text.lower()


def match_keys(
    haystack: str,
    needle: str,
    entry: Any = None,
    *,
    case_sensitive: bool | None = None,
    match_whole_words: bool | None = None,
) -> bool:
    """JS ``WorldInfoBuffer.matchKeys`` (world-info.js:337-366), branch for branch.

    A regex key overrides every other option. Otherwise the whole-word setting
    (``entry.matchWholeWords ?? world_info_match_whole_words``) decides between
    the ``(?:^|\\W)(key)(?:$|\\W)`` path for single-token keys, the plain
    ``includes`` fallback for multi-word keys, and the plain ``includes`` of the
    non-whole-word path.
    """
    # If the needle is a regex, we do regex pattern matching and override all the
    # other options. JS builds the RegExp on every call, so a ``g`` flag is inert.
    key_regex = parse_regex_from_string(needle)
    if key_regex is not None:
        return key_regex.search(haystack) is not None

    # Otherwise we do normal matching of plaintext with the chosen entry settings
    haystack = transform_string(haystack, entry, case_sensitive=case_sensitive)
    transformed_string = transform_string(needle, entry, case_sensitive=case_sensitive)
    if match_whole_words is None:
        match_whole_words = _entry_field(entry, "matchWholeWords", "match_whole_words")
    if match_whole_words is None:
        match_whole_words = world_info_match_whole_words

    if match_whole_words:
        key_words = _JS_WHITESPACE.split(transformed_string)

        if len(key_words) > 1:
            return transformed_string in haystack
        else:
            # Use custom boundaries to include punctuation and other
            # non-alphanumeric characters. ``$`` is kept as in the source:
            # Python's ``$`` also matches before a trailing newline, but that
            # position is already covered by ``\W``, so both agree.
            # ``re.ASCII`` is *required*: JS ``\w`` / ``\W`` are ASCII-only in
            # every mode (``u`` included, it only adds ``\p{...}``), while
            # Python's ``\w`` is Unicode-aware for str patterns. Without it a CJK
            # or Greek key inside CJK / Greek text would stop matching, because
            # the neighbouring ideograph counts as a word character.
            regex = re.compile(rf"(?:^|\W)({escape_regex(transformed_string)})(?:$|\W)", re.ASCII)
            if regex.search(haystack):
                return True
    else:
        return transformed_string in haystack

    return False


def any_key_matches(
    haystack: str,
    keys: Iterable[str],
    entry: Any = None,
    *,
    case_sensitive: bool | None = None,
    match_whole_words: bool | None = None,
) -> bool:
    """``entry.key.some(k => buffer.matchKeys(haystack, k, entry))`` (world-info.js:4922)."""
    for key in keys:
        if match_keys(
            haystack,
            key,
            entry,
            case_sensitive=case_sensitive,
            match_whole_words=match_whole_words,
        ):
            return True
    return False


__all__: Sequence[str] = [
    "JS_ONLY_REGEX_FLAGS",
    "JS_REGEX_FLAG_CHARS",
    "any_key_matches",
    "custom_tokenizer",
    "escape_regex",
    "is_valid_regex",
    "match_keys",
    "parse_regex_from_string",
    "split_keywords_and_regexes",
    "transform_string",
    "world_info_case_sensitive",
    "world_info_match_whole_words",
]
