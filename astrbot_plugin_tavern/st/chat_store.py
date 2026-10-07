"""SillyTavern compatible chat (JSONL) storage.

This module implements the *chat file* half of SillyTavern's data layer so that
AstrBot can keep its conversations in a shape that SillyTavern (and the wider
ecosystem) can read back unchanged. Like the rest of ``astrbot_plugin_tavern.st``
it contains **no AstrBot import** and uses the standard library only, so it can
be unit tested completely offline.

On-disk layout (verified against SillyTavern ``release`` 1.19.0)
----------------------------------------------------------------

``<root>/chats/<character_name>/<file>.jsonl``
    One chat. The very first line is the *header*::

        {"user_name": "...", "character_name": "...", "chat_metadata": {...}}

    Every following line is one message::

        {"name": "...", "is_user": false, "is_system": false,
         "send_date": "...", "mes": "...", "extra": {...}}

    so ``chat_items == total lines - 1``.

``<root>/group chats/<id>.jsonl`` / ``<root>/groups/<id>.json``
    Group chat transcript plus the group record (which lists ``chats[]``).
    Not written by this module, but the directory names are exposed as
    :attr:`ChatStore.groups_dir` and :attr:`ChatStore.group_chats_dir` so the
    plugin layer places them exactly where SillyTavern expects.

``chat_metadata.timedWorldInfo``
    The *only* place SillyTavern persists ``sticky`` / ``cooldown`` / ``delay``
    world-info state; there is no separate ``.metadata`` file. It is stored as
    ``{bucket: {"<world>.<uid>": {"hash": ..., "start": ..., "end": ...,
    "protected": ...}}}`` and is exposed here through
    :class:`ChatMetadata.timed_world_info`.

``chat_metadata.integrity``
    An integrity slug. SillyTavern refuses to overwrite a chat whose slug does
    not match. This implementation is deliberately softer: on read a mismatch
    only sets :attr:`ChatSession.integrity_ok` to ``False`` and never raises,
    because losing a chat to a stale slug is worse than a stale write.

Known deviations from SillyTavern
---------------------------------

* ``integrity`` is a SHA-256 over the *canonical* chat payload (every message
  line, serialised with ``sort_keys``). SillyTavern's slug is its own hash of the
  chat array, so a file written here will look "mismatched" to SillyTavern and
  vice versa. :attr:`ChatSession.integrity_ok` is therefore advisory.
* A numeric ``send_date`` (SillyTavern sometimes writes epoch milliseconds) is
  normalised to an ISO-8601 string, so :class:`ChatMessage.send_date` is always
  a ``str``; a missing or empty one becomes "now" at construction time.
* Unknown ``chat_metadata`` keys are preserved verbatim in
  :attr:`ChatMetadata.extra` instead of being dropped.
* :func:`sanitize_filename` also trims the replacement underscores it just
  introduced, so a name made only of illegal characters collapses to ``""``
  (and is rejected) rather than becoming ``"___"``.

All I/O failures are wrapped in :class:`ChatStoreError`; the more specific
:class:`ChatNotFound` (a subclass) is raised when a chat file is missing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .worldbook import greedy_token_count

logger = logging.getLogger(__name__)

#: Characters Windows forbids in a file name.
ILLEGAL_FILENAME_CHARS: tuple[str, ...] = ("\\", "/", ":", "*", "?", '"', "<", ">", "|")

#: Suffix used by SillyTavern chat files.
CHAT_SUFFIX = ".jsonl"

#: ``timedWorldInfo`` bucket names. SillyTavern uses ``sticky`` and ``cooldown``
#: and occasionally ``delay``; the two official ones are written by default.
TIMED_BUCKETS: tuple[str, ...] = ("sticky", "cooldown")

#: Key inside a ``timedWorldInfo`` entry that records which bucket it came from.
_BUCKET_KEY = "bucket"


class ChatStoreError(RuntimeError):
    """Raised for any chat storage failure (I/O, corrupt JSONL, bad name)."""


class ChatNotFound(ChatStoreError):
    """Raised by :meth:`ChatStore.load` when the requested chat file is absent."""


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string (SillyTavern style)."""
    return datetime.now(timezone.utc).isoformat()


def _as_str(value: Any) -> str:
    """Best effort string conversion that never raises."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _as_bool(value: Any, default: bool = False) -> bool:
    """Lenient boolean parsing (``1`` / ``"yes"`` / ``"true"`` all work)."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def _as_int(value: Any, default: int = 0) -> int:
    """Lenient int parsing; unusable values fall back to ``default``."""
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _normalise_send_date(value: Any) -> str:
    """Coerce a ``send_date`` into an ISO string.

    SillyTavern writes ISO strings, but imported chats may carry epoch
    milliseconds. Numbers are therefore converted, everything else is stringified
    (or replaced by "now" when empty).
    """
    if value is None or value == "":
        return _now_iso()
    if isinstance(value, bool):
        return _now_iso()
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:  # milliseconds
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return _as_str(value)
    return _as_str(value)


def _coerce_send_date(value: Any, default: str) -> str:
    """Coerce a ``send_date`` argument into an ISO string at construction time.

    A timestamp is generated **once**, when the message is created, instead of
    on every serialisation: an empty ``send_date`` must not change between two
    reads of the same message, or the integrity slug could never match.
    """
    return _normalise_send_date(value) if value else default


def sanitize_filename(name: str) -> str:
    """Return ``name`` as a safe single path component.

    Windows illegal characters (``\\ / : * ? " < > |``) plus control characters
    are replaced by ``_``, and the surrounding whitespace, replacement
    underscores and dots (all of which Windows or SillyTavern would silently
    strip) are trimmed away. A name made only of illegal characters therefore
    collapses to ``""`` and :func:`_safe_component` rejects it.
    """
    cleaned = _as_str(name)
    for char in ILLEGAL_FILENAME_CHARS:
        cleaned = cleaned.replace(char, "_")
    cleaned = "".join("_" if ord(char) < 32 else char for char in cleaned)
    return cleaned.strip().strip("_").strip().strip(".").strip()


def _safe_component(name: str, what: str) -> str:
    """Sanitize a path component, rejecting values that collapse to nothing."""
    cleaned = sanitize_filename(name)
    if not cleaned:
        raise ChatStoreError(f"invalid {what}: {name!r}")
    return cleaned


@dataclass
class ChatMessage:
    """One chat message line of a SillyTavern JSONL chat."""

    name: str
    mes: str
    is_user: bool = False
    is_system: bool = False
    #: Fractional-second ISO string generated at construction. Use :func:`_now_iso`
    #: semantics: an empty value becomes "now" exactly once, never per save.
    send_date: str = field(default_factory=_now_iso)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_st_dict(self) -> dict[str, Any]:
        """Serialise to the exact SillyTavern message line shape."""
        return {
            "name": self.name,
            "is_user": bool(self.is_user),
            "is_system": bool(self.is_system),
            "send_date": self.send_date,
            "mes": self.mes,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_st_dict(cls, payload: dict[str, Any]) -> ChatMessage:
        """Parse one SillyTavern message line.

        Unknown keys are preserved inside ``extra`` so a round trip through this
        module cannot silently drop swipe / reasoning / media metadata.
        """
        data = dict(payload) if isinstance(payload, dict) else {}
        extra = data.get("extra")
        extra = dict(extra) if isinstance(extra, dict) else {}
        known = {"name", "mes", "is_user", "is_system", "send_date", "extra"}
        for key, value in data.items():
            if key not in known:
                extra.setdefault(key, value)
        return cls(
            name=_as_str(data.get("name")),
            mes=_as_str(data.get("mes")),
            is_user=_as_bool(data.get("is_user")),
            is_system=_as_bool(data.get("is_system")),
            send_date=_coerce_send_date(data.get("send_date"), _now_iso()),
            extra=extra,
        )

    @property
    def is_assistant(self) -> bool:
        """True when the message was written by the character (not the user)."""
        return not self.is_user and not self.is_system

    @property
    def role(self) -> str:
        """Chat-completion role: ``"system"``, ``"user"`` or ``"assistant"``."""
        if self.is_system:
            return "system"
        return "user" if self.is_user else "assistant"

    def text(self) -> str:
        """The display text; ``extra.display_text`` wins when present."""
        display = self.extra.get("display_text")
        if isinstance(display, str) and display:
            return display
        return self.mes


def _parse_timed_state(value: Any, fallback_bucket: str) -> dict[str, Any] | None:
    """Normalise one ``timedWorldInfo`` entry into the canonical state dict."""
    if not isinstance(value, dict):
        return None
    state: dict[str, Any] = {
        _BUCKET_KEY: _as_str(value.get(_BUCKET_KEY)) or fallback_bucket,
        "hash": _as_str(value.get("hash")),
        "start": _as_int(value.get("start"), 0),
        "end": _as_int(value.get("end"), 0),
        "protected": _as_bool(value.get("protected")),
    }
    known = {_BUCKET_KEY, "hash", "start", "end", "protected"}
    leftover = {key: item for key, item in value.items() if key not in known}
    if leftover:
        state["extra"] = leftover
    return state


def _flatten_timed(payload: Any) -> dict[str, dict[str, Any]]:
    """Flatten a ST ``timedWorldInfo`` payload into ``{"<world>.<uid>": state}``.

    Both layouts are accepted:

    * the canonical SillyTavern shape
      ``{"sticky": {"book.0": {...}}, "cooldown": {...}}``;
    * an already flattened ``{"book.0": {...}}`` map (older exports).
    """
    if not isinstance(payload, dict):
        return {}
    flattened: dict[str, dict[str, Any]] = {}
    for key, value in payload.items():
        if key in TIMED_BUCKETS and isinstance(value, dict):
            for entry_key, state in value.items():
                parsed = _parse_timed_state(state, key)
                if parsed is not None:
                    flattened.setdefault(_as_str(entry_key), parsed)
        else:
            parsed = _parse_timed_state(value, TIMED_BUCKETS[0])
            if parsed is not None:
                flattened.setdefault(_as_str(key), parsed)
    return flattened


def _nested_timed(flat: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Rebuild the SillyTavern ``{bucket: {"<world>.<uid>": state}}`` layout."""
    nested: dict[str, dict[str, Any]] = {}
    for key, state in flat.items():
        if not isinstance(state, dict):
            continue
        bucket = _as_str(state.get(_BUCKET_KEY)) or TIMED_BUCKETS[0]
        entry = {
            name: state[name] for name in ("hash", "start", "end", "protected") if name in state
        }
        if isinstance(state.get("extra"), dict):
            entry.update(state["extra"])
        nested.setdefault(bucket, {})[key] = entry
    return nested


@dataclass
class ChatMetadata:
    """The ``chat_metadata`` object stored in the first JSONL line."""

    world_info: dict[str, Any] | str | None = None
    timed_world_info: dict[str, dict[str, Any]] = field(default_factory=dict)
    integrity: str = ""
    note_prompt: str = ""
    note_interval: int = 0
    note_position: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_st_dict(self) -> dict[str, Any]:
        """Serialise to SillyTavern's camelCase ``chat_metadata`` shape."""
        data = dict(self.extra)
        data["integrity"] = self.integrity
        if self.timed_world_info:
            data["timedWorldInfo"] = _nested_timed(self.timed_world_info)
        # SillyTavern always carries these three, so emit them unconditionally.
        data["note_prompt"] = self.note_prompt
        data["note_interval"] = self.note_interval
        data["note_position"] = self.note_position
        if self.world_info is not None:
            data["world_info"] = self.world_info
        else:
            data.pop("world_info", None)
        return data

    @classmethod
    def from_st_dict(cls, payload: dict[str, Any] | None) -> ChatMetadata:
        """Parse a SillyTavern ``chat_metadata`` object (lenient)."""
        data = dict(payload) if isinstance(payload, dict) else {}
        known = {
            "integrity",
            "timedWorldInfo",
            "timed_world_info",
            "note_prompt",
            "note_interval",
            "note_position",
            "world_info",
        }
        extra = {key: value for key, value in data.items() if key not in known}
        world_info = data.get("world_info")
        if not isinstance(world_info, (dict, str)):
            world_info = None
        raw_timed = data.get("timedWorldInfo", data.get("timed_world_info"))
        return cls(
            world_info=world_info,
            timed_world_info=_flatten_timed(raw_timed),
            integrity=_as_str(data.get("integrity")),
            note_prompt=_as_str(data.get("note_prompt")),
            note_interval=_as_int(data.get("note_interval"), 0),
            note_position=_as_int(data.get("note_position"), 0),
            extra=extra,
        )

    def mark_timed(
        self,
        book: str,
        uid: int,
        start: int,
        end: int,
        protected: bool = False,
        hash_: str = "",
    ) -> None:
        """Record a ``sticky`` / ``cooldown`` window for one world info entry.

        The key is SillyTavern's ``"<world>.<uid>"``. The value carries the
        half-open turn range ``[start, end)`` plus a content hash and a
        ``protected`` flag; SillyTavern drops unprotected effects whose range
        has passed, and this class does the same on save through
        :meth:`prune_timed`.
        """
        self.timed_world_info[f"{book}.{uid}"] = {
            _BUCKET_KEY: TIMED_BUCKETS[0],
            "hash": hash_,
            "start": int(start),
            "end": int(end),
            "protected": bool(protected),
        }

    def is_timed_active(self, book: str, uid: int, turn: int) -> bool:
        """True when ``turn`` falls inside a recorded ``[start, end)`` window.

        ``start`` is inclusive and ``end`` is exclusive, matching SillyTavern's
        ``currentTurn <= end && currentTurn >= start`` guard (which is why a
        window with ``end == start`` is empty). Every bucket is searched.
        """
        key = f"{book}.{uid}"
        state = self.timed_world_info.get(key)
        if not state:
            return False
        start = _as_int(state.get("start"), 0)
        end = _as_int(state.get("end"), start)
        return start <= turn < end

    def prune_timed(self, turn: int | None = None, remove_expired: bool = True) -> None:
        """Drop expired, unprotected windows (SillyTavern does this on load)."""
        if not remove_expired or turn is None:
            return
        expired = [
            key
            for key, state in self.timed_world_info.items()
            if _as_int(state.get("end"), 0) <= turn and not _as_bool(state.get("protected"))
        ]
        for key in expired:
            del self.timed_world_info[key]


@dataclass
class ChatSession:
    """A whole chat: header names, messages and chat metadata."""

    name: str
    character_name: str
    user_name: str
    messages: list[ChatMessage]
    metadata: ChatMetadata
    #: Path the session was loaded from / saved to, when known.
    path: str = ""
    #: False when the on-disk ``integrity`` slug did not match the content.
    integrity_ok: bool = True

    def to_jsonl(self) -> str:
        """Serialise as a SillyTavern JSONL chat (header first, trailing ``\\n``)."""
        header = {
            "user_name": self.user_name,
            "character_name": self.character_name,
            "chat_metadata": self.metadata.to_st_dict(),
        }
        lines = [json.dumps(header, ensure_ascii=False)]
        lines.extend(json.dumps(msg.to_st_dict(), ensure_ascii=False) for msg in self.messages)
        return "\n".join(lines) + "\n"

    @classmethod
    def from_jsonl(cls, text: str, name: str = "", path: str = "") -> ChatSession:
        """Parse a SillyTavern JSONL chat.

        Tolerates a BOM, CRLF or LF endings, a missing trailing newline, an
        empty file, a header-only file, blank lines, missing ``is_system`` and
        unknown message fields (kept in ``extra``).
        """
        header, messages = _iter_chat_lines(text)
        metadata = ChatMetadata.from_st_dict(header.get("chat_metadata"))
        session = cls(
            name=name,
            character_name=_as_str(header.get("character_name")),
            user_name=_as_str(header.get("user_name")),
            messages=messages,
            metadata=metadata,
            path=path,
        )
        stored = metadata.integrity
        session.integrity_ok = (not stored) or stored == session.content_hash()
        return session

    def content_hash(self) -> str:
        """SHA-256 over the canonical payload (message lines only, stable)."""
        payload = json.dumps(
            [msg.to_st_dict() for msg in self.messages],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def estimated_tokens(self, counter: Callable[[str], int] | None = None) -> int:
        """Approximate token cost of every message (header excluded).

        ``counter`` defaults to :func:`astrbot_plugin_tavern.st.worldbook.greedy_token_count`,
        which uses ``tiktoken`` when installed and a length heuristic otherwise.
        """
        count = counter or greedy_token_count
        return sum(count(msg.mes) for msg in self.messages)

    @property
    def chat_items(self) -> int:
        """Number of messages (``total lines - 1`` in SillyTavern terms)."""
        return len(self.messages)

    def to_prompt_messages(self) -> list[dict[str, str]]:
        """Shortcut for :func:`session_to_prompt_messages`."""
        return session_to_prompt_messages(self)


def _strip_bom(text: str) -> str:
    """Remove a leading UTF-8 BOM if the decoder kept one."""
    return text[1:] if text[:1] == "\ufeff" else text


def _header_payload(raw: str) -> dict[str, Any] | None:
    """Parse one JSON object from ``raw``; ``None`` when it is not usable."""
    if not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _parse_header(raw: str) -> dict[str, Any]:
    """Parse the first line, returning an empty header when it is unusable."""
    payload = _header_payload(raw)
    if payload is None:
        return {}
    if "user_name" in payload or "character_name" in payload or "chat_metadata" in payload:
        return payload
    # Older exports sometimes put the character name in ``name``.
    if "name" in payload and "is_user" not in payload and "mes" not in payload:
        return {"character_name": payload.get("name")}
    return {}


def _iter_chat_lines(text: str) -> tuple[dict[str, Any], list[ChatMessage]]:
    """Split raw text into ``(header, messages)`` tolerantly."""
    lines = _strip_bom(text).splitlines()
    if not lines:
        return {}, []
    header = _parse_header(lines[0])
    messages: list[ChatMessage] = []
    for raw in lines[1:]:
        if not raw.strip():
            continue
        payload = _header_payload(raw)
        if payload is None:
            logger.debug("skip unparsable chat line: %r", raw[:80])
            continue
        if "mes" not in payload and "is_user" not in payload:
            # A stray second header object: fold it into the header.
            for key, value in payload.items():
                header.setdefault(key, value)
            continue
        messages.append(ChatMessage.from_st_dict(payload))
    return header, messages


class ChatStore:
    """File backed chat storage laid out exactly like SillyTavern's ``data/``.

    Parameters
    ----------
    root:
        Plugin data directory, e.g. ``data/plugin_data/<plugin>/``. Chats live in
        ``<root>/chats/<character_name>/<file>.jsonl``.
    user_name:
        Fallback persona name written into the header of new chats.

    Notes
    -----
    Every method wraps filesystem errors in :class:`ChatStoreError`;
    :class:`ChatNotFound` signals a missing chat. Writes are atomic (write to a
    sibling ``.tmp`` file, then :func:`os.replace`), so a crash can never leave
    a half written chat behind.
    """

    def __init__(self, root: str | Path, user_name: str = "user") -> None:
        self.root = Path(root)
        self.user_name = user_name
        self.chats_dir = self.root / "chats"
        self.group_chats_dir = self.root / "group chats"
        self.groups_dir = self.root / "groups"

    # -- paths ---------------------------------------------------------------

    def character_dir(self, character_name: str) -> Path:
        """Directory holding every chat of ``character_name``."""
        return self.chats_dir / _safe_component(character_name, "character name")

    def path_for(self, character_name: str, chat_name: str) -> Path:
        """Absolute path of one chat file (chat names are single components)."""
        return self.character_dir(character_name) / f"{_validate_chat_name(chat_name)}{CHAT_SUFFIX}"

    def list_chats(self, character_name: str) -> list[str]:
        """Chat names of a character, newest first (extension stripped)."""
        directory = self.character_dir(character_name)
        if not directory.is_dir():
            return []
        try:
            files = [path for path in directory.iterdir() if path.suffix.lower() == CHAT_SUFFIX]
            files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        except OSError as exc:  # pragma: no cover - platform dependent
            raise ChatStoreError(f"cannot list chats in {directory}: {exc}") from exc
        return [path.stem for path in files]

    # -- reading -------------------------------------------------------------

    def load(self, character_name: str, chat_name: str) -> ChatSession:
        """Read one chat from disk.

        Raises
        ------
        ChatNotFound
            The chat file does not exist.
        ChatStoreError
            The file exists but cannot be read or parsed.
        """
        path = self.path_for(character_name, chat_name)
        if not path.is_file():
            raise ChatNotFound(f"chat not found: {path}")
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            raise ChatStoreError(f"cannot read chat {path}: {exc}") from exc
        try:
            session = ChatSession.from_jsonl(text, name=chat_name, path=str(path))
        except ChatStoreError:
            raise
        except Exception as exc:  # noqa: BLE001 - never leak a raw parser error
            raise ChatStoreError(f"cannot parse chat {path}: {exc}") from exc
        if not session.character_name:
            session.character_name = character_name
        return session

    def search(self, character_name: str, keyword: str) -> list[tuple[str, int, ChatMessage]]:
        """Find ``keyword`` in every chat of a character.

        Returns ``(chat_name, line_number, message)`` tuples, where
        ``line_number`` is the 1-based **file** line (the header counts as line
        1, so the first message is line 2). Chats are scanned newest first and
        an empty keyword matches nothing.
        """
        if not keyword:
            return []
        hits: list[tuple[str, int, ChatMessage]] = []
        for chat_name in self.list_chats(character_name):
            for offset, message in enumerate(self._read_raw_messages(character_name, chat_name)):
                if keyword in message.mes:
                    hits.append((chat_name, offset + 2, message))
        return hits

    def _read_raw_messages(self, character_name: str, chat_name: str) -> list[ChatMessage]:
        """Messages of a chat without integrity bookkeeping (used by search)."""
        session = self.load(character_name, chat_name)
        return session.messages

    # -- writing -------------------------------------------------------------

    def save(self, session: ChatSession) -> Path:
        """Atomically write ``session`` and return the file path.

        The ``integrity`` slug is recomputed from the current message content
        right before writing, then the text is written to a ``.tmp`` sibling and
        moved into place with :func:`os.replace`. A failed write therefore never
        truncates the existing chat and never leaves a ``.tmp`` file behind. The
        metadata is updated in place so the in-memory session keeps matching its
        file.
        """
        path = self.path_for(session.character_name, session.name)
        session.metadata.integrity = session.content_hash()
        text = session.to_jsonl()
        tmp_path = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(text, encoding="utf-8", newline="\n")
            os.replace(tmp_path, path)
        except OSError as exc:
            self._discard_temp(tmp_path)
            raise ChatStoreError(f"cannot save chat {path}: {exc}") from exc
        session.path = str(path)
        session.integrity_ok = True
        return path

    @staticmethod
    def _discard_temp(tmp_path: Path) -> None:
        """Best effort removal of a leftover ``.tmp`` after a failed write."""
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - nothing else we can do
            logger.warning("could not remove stale temp file %s", tmp_path)

    def delete(self, character_name: str, chat_name: str) -> bool:
        """Delete a chat file; returns False when it did not exist."""
        path = self.path_for(character_name, chat_name)
        try:
            if not path.is_file():
                return False
            path.unlink()
        except OSError as exc:
            raise ChatStoreError(f"cannot delete chat {path}: {exc}") from exc
        return True

    def rename(self, character_name: str, chat_name: str, new_name: str) -> Path:
        """Rename (or retitle) a chat file. Returns the new path.

        Raises
        ------
        ChatNotFound
            The source chat does not exist.
        ChatStoreError
            The destination already exists, or the move failed.
        """
        source = self.path_for(character_name, chat_name)
        target = self.path_for(character_name, new_name)
        if not source.is_file():
            raise ChatNotFound(f"chat not found: {source}")
        if target.exists():
            raise ChatStoreError(f"chat already exists: {target}")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
        except OSError as exc:
            raise ChatStoreError(f"cannot rename chat {source} -> {target}: {exc}") from exc
        return target

    def create(
        self,
        character_name: str,
        chat_name: str,
        metadata: ChatMetadata | None = None,
    ) -> ChatSession:
        """Create (and persist) an empty chat for ``character_name``."""
        session = ChatSession(
            name=_validate_chat_name(chat_name),
            character_name=character_name,
            user_name=self.user_name,
            messages=[],
            metadata=metadata or ChatMetadata(),
        )
        self.save(session)
        return session

    def append_message(
        self,
        character_name: str,
        chat_name: str,
        message: ChatMessage,
    ) -> ChatSession:
        """Append one message to an existing chat and save it.

        Raises :class:`ChatNotFound` when the chat does not exist (use
        :meth:`create` first); this mirrors SillyTavern, which only appends to
        a chat it already opened.
        """
        session = self.load(character_name, chat_name)
        session.messages.append(message)
        self.save(session)
        return session

    def fork(
        self,
        character_name: str,
        chat_name: str,
        new_name: str,
        at_index: int | None = None,
    ) -> ChatSession:
        """Branch a chat at ``at_index`` ("swipe" / "checkpoint" semantics).

        ``at_index`` is a 0-based message index. ``None`` keeps every message,
        ``0`` yields an empty chat (only the greeting is *not* copied, matching
        "branch before the first reply"). Negative or too large values raise
        :class:`ChatStoreError`. The result is persisted under ``new_name`` and
        returned.
        """
        source = self.load(character_name, chat_name)
        if at_index is None:
            keep = list(source.messages)
        else:
            if at_index < 0 or at_index > len(source.messages):
                raise ChatStoreError(
                    f"fork index {at_index} out of range for {chat_name} "
                    f"({len(source.messages)} messages)"
                )
            keep = list(source.messages[:at_index])
        branched = ChatSession(
            name=_validate_chat_name(new_name),
            character_name=source.character_name or character_name,
            user_name=source.user_name,
            messages=keep,
            metadata=ChatMetadata.from_st_dict(source.metadata.to_st_dict()),
        )
        self.save(branched)
        return branched

    # -- explicit ST import / export ----------------------------------------

    def export_st_jsonl(self, session: ChatSession) -> str:
        """Return ``session`` as raw SillyTavern JSONL text."""
        return session.to_jsonl()

    def import_st_jsonl(self, text: str, name: str = "") -> ChatSession:
        """Parse raw SillyTavern JSONL text (same rules as
        :meth:`ChatSession.from_jsonl`, exposed as an explicit API)."""
        return ChatSession.from_jsonl(text, name=name)

    def export_to_file(self, session: ChatSession, path: str | Path) -> Path:
        """Write ``session`` as JSONL to an arbitrary path (for user exports)."""
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(session.to_jsonl(), encoding="utf-8", newline="\n")
        except OSError as exc:
            raise ChatStoreError(f"cannot export chat to {target}: {exc}") from exc
        return target


def _validate_chat_name(chat_name: str) -> str:
    """Reject chat names that are empty or contain path separators."""
    name = _as_str(chat_name).strip()
    if not name:
        raise ChatStoreError("chat name must not be empty")
    if any(sep in name for sep in ("/", "\\")) or name in (".", ".."):
        raise ChatStoreError(f"invalid chat name: {chat_name!r}")
    return name


def session_to_prompt_messages(session: ChatSession) -> list[dict[str, str]]:
    """Map a :class:`ChatSession` onto generic chat-completion messages.

    The result is ``[{"role": ..., "content": ..., "name": ...}, ...]`` where
    ``role`` comes from :attr:`ChatMessage.role` (``system`` for
    ``is_system``, ``user`` for ``is_user``, ``assistant`` otherwise). This is a
    pure format mapping: the prompt/persona layer stays in ``st.prompt``, which
    this module deliberately does not import.
    """
    return [
        {"role": message.role, "content": message.mes, "name": message.name}
        for message in session.messages
    ]


def iter_prompt_messages(messages: Sequence[ChatMessage]) -> Iterator[dict[str, str]]:
    """Lazy variant of :func:`session_to_prompt_messages` over a message list."""
    for message in messages:
        yield {"role": message.role, "content": message.mes, "name": message.name}


__all__ = [
    "CHAT_SUFFIX",
    "ILLEGAL_FILENAME_CHARS",
    "TIMED_BUCKETS",
    "ChatMessage",
    "ChatMetadata",
    "ChatNotFound",
    "ChatSession",
    "ChatStore",
    "ChatStoreError",
    "iter_prompt_messages",
    "sanitize_filename",
    "session_to_prompt_messages",
]
