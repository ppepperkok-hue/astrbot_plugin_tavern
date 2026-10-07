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
    """Flat key first (the oracle's shape), then a dotted walk (lodash's)."""
    if key in _config:
        return _config[key]
    node: Any = _config
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


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
