"""Framework independent brain of the tavern plugin.

``main.py`` only does AstrBot plumbing (decorators, message components,
permissions); every decision lives here so it can be unit tested without an
AstrBot installation. The responsibilities are:

* **library** - what character cards and world books exist on disk,
* **binding** - which card + world books a given ``unified_msg_origin`` talks to,
* **history** - the per binding chat log (SillyTavern compatible ``.jsonl``),
* **assembly** - run the world info engine and the prompt builder for one turn,
* **rendering** - split the answer for chat platforms and clean it up.

Nothing here imports ``astrbot``; the only framework touch point is the optional
``StarTools`` lookup inside :mod:`tavern.config`.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tavern.backends.base import GenerationRequest, PromptMessage, messages_to_openai
from tavern.config import TavernConfig, as_str
from tavern.st import chat_store, worldbook
from tavern.st.cards import CharacterCard, card_from_dict, scan_cards
from tavern.st.prompt import (
    BuildResult,
    InChatTargets,
    PresetSpec,
    RenderOptions,
    build_messages,
    default_preset,
    preset_from_dict,
)

#: Bumped whenever the on-disk state layout changes.
STATE_VERSION = 1

DEFAULT_CHAT_NAME = "main"

_STATUS_BLOCK = re.compile(r"<(?:status|Status|STATUS)>.*?</(?:status|Status|STATUS)>", re.DOTALL)
_FENCED_STATUS = re.compile(r"```(?:status|Status|STATUS)\b.*?```", re.DOTALL)


class TavernError(RuntimeError):
    """User facing error; the message is shown in chat."""


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _scope_key(unified_msg_origin: str) -> str:
    return unified_msg_origin.strip() or "unknown"


@dataclass
class SessionBinding:
    """What one chat session (group or private) is currently role playing."""

    scope: str
    card_id: str = ""
    card_name: str = ""
    worldbooks: list[str] = field(default_factory=list)
    chat_name: str = DEFAULT_CHAT_NAME
    preset: str = ""
    greeting_sent: bool = False
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "card_id": self.card_id,
            "card_name": self.card_name,
            "worldbooks": list(self.worldbooks),
            "chat_name": self.chat_name,
            "preset": self.preset,
            "greeting_sent": self.greeting_sent,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SessionBinding:
        return cls(
            scope=as_str(payload.get("scope")),
            card_id=as_str(payload.get("card_id")),
            card_name=as_str(payload.get("card_name")),
            worldbooks=[as_str(item) for item in payload.get("worldbooks") or [] if as_str(item)],
            chat_name=as_str(payload.get("chat_name"), DEFAULT_CHAT_NAME) or DEFAULT_CHAT_NAME,
            preset=as_str(payload.get("preset")),
            greeting_sent=bool(payload.get("greeting_sent")),
            created_at=as_str(payload.get("created_at"), _now_iso()) or _now_iso(),
            updated_at=as_str(payload.get("updated_at"), _now_iso()) or _now_iso(),
            extra=dict(payload.get("extra") or {}),
        )


@dataclass
class Turn:
    """One assembled generation request plus the provenance for debugging."""

    scope: str
    binding: SessionBinding
    card: CharacterCard
    request: GenerationRequest
    build: BuildResult
    activated: list[worldbook.WorldInfoEntry] = field(default_factory=list)
    world_book_names: list[str] = field(default_factory=list)
    messages_for_scan: list[str] = field(default_factory=list)

    @property
    def debug_lines(self) -> list[str]:
        lines = [
            f"scope: {self.scope}",
            f"card: {self.card.name} ({len(self.card.description)} chars description)",
            f"world books: {', '.join(self.world_book_names) or '(none)'}",
            f"activated entries: {len(self.activated)}",
        ]
        for entry in self.activated[:20]:
            label = entry.comment or ", ".join(entry.keys[:3]) or f"uid {entry.uid}"
            position = worldbook.POSITION_NAMES.get(entry.position, entry.position)
            lines.append(f"  - [{position}] {label}")
        lines.append("blocks:")
        for name, content in self.build.debug_blocks:
            preview = content.replace("\n", " / ")
            if len(preview) > 80:
                preview = preview[:80] + "..."
            lines.append(f"  - {name}: {preview or '(empty)'}")
        lines.append(f"messages: {len(self.request.messages)}")
        return lines


class PluginCore:
    """Card/world book library, session bindings and per turn assembly."""

    def __init__(self, config: TavernConfig) -> None:
        self.config = config
        self.config.ensure_dirs()
        self._cards: dict[str, CharacterCard] = {}
        self._books: dict[str, worldbook.WorldBook] = {}
        self._presets: dict[str, PresetSpec] = {}
        self._bindings: dict[str, SessionBinding] = {}
        self._activation: dict[str, worldbook.ActivationState] = {}
        self._card_files: dict[str, str] = {}
        self._book_files: dict[str, str] = {}
        self._store: chat_store.ChatStore | None = None

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    @property
    def store(self) -> chat_store.ChatStore:
        if self._store is None:
            self._store = chat_store.ChatStore(self.config.chats_dir)
        return self._store

    def load(self) -> None:
        """Load bindings from disk and (re)scan the card/world book libraries."""
        self._load_state()
        self.reload_library()

    def _load_state(self) -> None:
        path = self.config.state_path
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        bindings = payload.get("bindings") if isinstance(payload, dict) else None
        if not isinstance(bindings, dict):
            return
        for key, value in bindings.items():
            if isinstance(value, dict):
                value.setdefault("scope", key)
                self._bindings[as_str(key)] = SessionBinding.from_dict(value)

    def save_state(self) -> None:
        payload = {
            "version": STATE_VERSION,
            "bindings": {key: binding.to_dict() for key, binding in self._bindings.items()},
        }
        _atomic_write_json(self.config.state_path, payload)

    # ------------------------------------------------------------------
    # library
    # ------------------------------------------------------------------
    def reload_library(self) -> dict[str, int]:
        """Rescan ``cards/``, ``worldbooks/`` and ``presets/`` from disk."""
        self._cards.clear()
        self._books.clear()
        self._presets.clear()
        self._card_files.clear()
        self._book_files.clear()

        for card in scan_cards(self.config.cards_dir):
            card_id = self._unique_id(card.name or Path(card.source_path).stem, self._cards)
            self._cards[card_id] = card
            self._card_files[card_id] = card.source_path
        for book in worldbook.scan_world_books(self.config.worldbooks_dir):
            name = book.name or Path(book.source_path).stem
            book_id = self._unique_id(name, self._books)
            self._books[book_id] = book
            self._book_files[book_id] = book.source_path
        for path in sorted(self.config.presets_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                self._presets[path.stem] = preset_from_dict(payload, apply_prompt_order=True)
            except (OSError, json.JSONDecodeError, ValueError):
                continue
        return {
            "cards": len(self._cards),
            "worldbooks": len(self._books),
            "presets": len(self._presets),
        }

    @staticmethod
    def _unique_id(name: str, existing: dict[str, Any]) -> str:
        base = name.strip() or "unnamed"
        if base not in existing:
            return base
        index = 2
        while f"{base} ({index})" in existing:
            index += 1
        return f"{base} ({index})"

    def card_ids(self) -> list[str]:
        return sorted(self._cards)

    def book_ids(self) -> list[str]:
        return sorted(self._books)

    def get_card(self, card_id: str) -> CharacterCard:
        card = self._cards.get(card_id)
        if card is None:
            lowered = card_id.strip().lower()
            for key, value in self._cards.items():
                if key.lower() == lowered:
                    return value
            raise TavernError(
                f"\u627e\u4e0d\u5230\u89d2\u8272\u5361\u300c{card_id}\u300d\u3002\u8bf7\u7528 /tavern list \u67e5\u770b\u53ef\u7528\u5361\u7247\u3002"
            )
        return card

    def get_book(self, book_id: str) -> worldbook.WorldBook:
        book = self._books.get(book_id)
        if book is not None:
            return book
        lowered = book_id.strip().lower()
        for key, value in self._books.items():
            if key.lower() == lowered:
                return value
        # Also accept the source file name, e.g. "lighthouse" for a book whose
        # ``name`` field is "Lighthouse Lore".
        for key, value in self._books.items():
            stem = Path(self._book_files.get(key, "")).stem.lower()
            if stem and stem == lowered:
                return value
        raise TavernError(
            f"\u627e\u4e0d\u5230\u4e16\u754c\u4e66\u300c{book_id}\u300d\u3002\u8bf7\u7528 /tavern worldbook list \u67e5\u770b\u3002"
        )

    def preset_for(self, binding: SessionBinding) -> PresetSpec:
        if binding.preset and binding.preset in self._presets:
            return self._presets[binding.preset]
        return default_preset()

    # ------------------------------------------------------------------
    # import
    # ------------------------------------------------------------------
    def import_card_bytes(self, filename: str, payload: bytes, *, overwrite: bool = False) -> str:
        """Persist a card file (``.png``/``.json``/``.yaml``) into the library."""
        suffix = Path(filename).suffix.lower()
        if suffix not in (".png", ".json", ".yaml", ".yml"):
            raise TavernError(
                f"\u4e0d\u652f\u6301\u7684\u5361\u7247\u683c\u5f0f\u300c{suffix or filename}\u300d\uff0c\u8bf7\u7528 .png/.json/.yaml\u3002"
            )
        target = _safe_target(self.config.cards_dir, filename, overwrite=overwrite)
        if suffix == ".png":
            card = _card_from_png_bytes(payload)
        elif suffix in (".yaml", ".yml"):
            card = _card_from_yaml_bytes(payload)
        else:
            card = card_from_dict(json.loads(payload.decode("utf-8")))
        if not card.name:
            raise TavernError(
                "\u8fd9\u5f20\u89d2\u8272\u5361\u6ca1\u6709\u540d\u5b57\uff0c\u65e0\u6cd5\u5bfc\u5165\u3002"
            )
        target.write_bytes(payload)
        card.source_path = str(target)
        card_id = self._unique_id(card.name, self._cards)
        self._cards[card_id] = card
        self._card_files[card_id] = str(target)
        self.save_state()
        return card_id

    def import_worldbook_bytes(
        self, filename: str, payload: bytes, *, overwrite: bool = False
    ) -> str:
        """Persist a world book file into the library."""
        suffix = Path(filename).suffix.lower()
        if suffix not in (".json", ".yaml", ".yml"):
            raise TavernError("\u4e16\u754c\u4e66\u8bf7\u7528 .json\uff08\u6216 .yaml\uff09\u3002")
        target = _safe_target(self.config.worldbooks_dir, filename, overwrite=overwrite)
        target.write_bytes(payload)
        book = worldbook.load_world_book(target)
        book_id = self._unique_id(book.name or target.stem, self._books)
        self._books[book_id] = book
        self._book_files[book_id] = str(target)
        self.save_state()
        return book_id

    def _session(self, character_name: str, chat_name: str) -> chat_store.ChatSession:
        """Load a chat, creating it on first use (SillyTavern's implicit new chat)."""
        try:
            return self.store.load(character_name, chat_name)
        except chat_store.ChatNotFound:
            return self.store.create(character_name, chat_name)

    # ------------------------------------------------------------------
    # bindings
    # ------------------------------------------------------------------
    def binding(self, unified_msg_origin: str) -> SessionBinding:
        key = _scope_key(unified_msg_origin)
        binding = self._bindings.get(key)
        if binding is None:
            binding = SessionBinding(scope=key)
            self._bindings[key] = binding
        return binding

    def bind_card(self, unified_msg_origin: str, card_id: str) -> SessionBinding:
        card = self.get_card(card_id)
        binding = self.binding(unified_msg_origin)
        binding.card_id = self._card_key(card)
        binding.card_name = card.name
        binding.greeting_sent = False
        binding.chat_name = DEFAULT_CHAT_NAME
        binding.updated_at = _now_iso()
        self.save_state()
        return binding

    def _card_key(self, card: CharacterCard) -> str:
        for key, value in self._cards.items():
            if value is card:
                return key
        return card.name

    def toggle_book(
        self, unified_msg_origin: str, book_id: str, enabled: bool | None = None
    ) -> tuple[SessionBinding, bool]:
        book = self.get_book(book_id)
        canonical = next((key for key, value in self._books.items() if value is book), book_id)
        binding = self.binding(unified_msg_origin)
        current = canonical in binding.worldbooks
        target = (not current) if enabled is None else bool(enabled)
        if target and not current:
            binding.worldbooks.append(canonical)
        elif not target and current:
            binding.worldbooks.remove(canonical)
        binding.updated_at = _now_iso()
        self.save_state()
        return binding, target

    def set_books(self, unified_msg_origin: str, book_ids: list[str]) -> SessionBinding:
        binding = self.binding(unified_msg_origin)
        canonical: list[str] = []
        for book_id in book_ids:
            book = self.get_book(book_id)
            key = next((k for k, v in self._books.items() if v is book), book_id)
            if key not in canonical:
                canonical.append(key)
        binding.worldbooks = canonical
        binding.updated_at = _now_iso()
        self.save_state()
        return binding

    def reset_chat(self, unified_msg_origin: str, *, new_name: str | None = None) -> SessionBinding:
        """Start a fresh chat for this session (SillyTavern's "new chat")."""
        binding = self.binding(unified_msg_origin)
        binding.chat_name = new_name or f"chat-{time.strftime('%Y%m%d-%H%M%S')}"
        binding.greeting_sent = False
        binding.updated_at = _now_iso()
        self._activation.pop(binding.scope, None)
        self.save_state()
        return binding

    def activation_state(self, unified_msg_origin: str) -> worldbook.ActivationState:
        key = _scope_key(unified_msg_origin)
        state = self._activation.get(key)
        if state is None:
            state = worldbook.ActivationState()
            self._activation[key] = state
        return state

    # ------------------------------------------------------------------
    # assembly
    # ------------------------------------------------------------------
    def build_turn(
        self,
        unified_msg_origin: str,
        user_input: str,
        *,
        sender_name: str = "\u7528\u6237",
        provider_id: str | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
        extra_macros: dict[str, str] | None = None,
    ) -> Turn:
        """Assemble one generation request for this session."""
        binding = self.binding(unified_msg_origin)
        if not binding.card_id:
            default = self.card_ids()
            if not default:
                raise TavernError(
                    "\u8fd8\u6ca1\u6709\u89d2\u8272\u5361\u3002\u628a\u5361\u7247\u653e\u8fdb\u6570\u636e\u76ee\u5f55\u7684 cards/ \u540e\u6267\u884c /tavern reload\u3002"
                )
            self.bind_card(unified_msg_origin, default[0])
            binding = self.binding(unified_msg_origin)

        card = self.get_card(binding.card_id)
        session = self._session(binding.card_name or card.name, binding.chat_name)
        history_messages = list(session.messages)

        scan_texts = [message.mes for message in history_messages if message.mes]
        scan_texts.append(user_input)

        activated: list[worldbook.WorldInfoEntry] = []
        book_names: list[str] = []
        if self.config.worldbook.enabled and binding.worldbooks:
            books = [self.get_book(name) for name in binding.worldbooks]
            book_names = [book.name or key for key, book in zip(binding.worldbooks, books)]
            settings = worldbook.WorldBookSettings(
                default_scan_depth=self.config.worldbook.scan_depth,
                allow_recursion=self.config.worldbook.allow_recursion,
                max_recursion_steps=self.config.worldbook.max_recursion_steps,
            )
            result = worldbook.activate(
                books,
                scan_texts,
                settings=settings,
                state=self.activation_state(unified_msg_origin),
                token_budget=self.config.worldbook.token_budget or None,
            )
            activated = result.activated
            if self.config.worldbook.injection_cap:
                activated = activated[: self.config.worldbook.injection_cap]
            targets = _targets_from_entries(activated)
        else:
            targets = InChatTargets()

        history: list[Any]
        if conversation_history:
            history = [_HistoryMessage.from_dict(item) for item in conversation_history]
        else:
            history = _history_from_store(history_messages, card, sender_name)

        options = RenderOptions(
            username=sender_name or "\u7528\u6237",
            char_name=card.name,
            names_as_prefix=True,
            timezone_name="Asia/Shanghai",
        )
        build = build_messages(
            card,
            self.preset_for(binding),
            targets,
            history,
            options,
            extra_macros=extra_macros,
            squash_system=False,
        )

        messages = [
            PromptMessage(
                role=message.role,
                content=message.content,
                name=getattr(message, "name", None),
            )
            for message in build.messages
            if message.content
        ]
        # Leading system blocks (main prompt, world info before char, character
        # definition, scenario, ...) are stable for the whole chat, so they are
        # handed over as ``system_prompt`` and dropped from the message list.
        # Later system blocks (post history instructions, in-chat injections)
        # keep their position inside the message list.
        system_parts: list[str] = []
        while messages and messages[0].role == "system":
            system_parts.append(messages.pop(0).content)
        system_prompt = "\n\n".join(part for part in system_parts if part)

        request = GenerationRequest(
            messages=messages,
            system_prompt=system_prompt,
            model=self.config.backend.provider_id or None,
            extra={"provider_id": provider_id or self.config.backend.provider_id or None},
        )
        return Turn(
            scope=binding.scope,
            binding=binding,
            card=card,
            request=request,
            build=build,
            activated=activated,
            world_book_names=book_names,
            messages_for_scan=scan_texts,
        )

    # ------------------------------------------------------------------
    # history writing
    # ------------------------------------------------------------------
    def record_user(
        self, unified_msg_origin: str, text: str, *, sender_name: str = "\u7528\u6237"
    ) -> None:
        binding = self.binding(unified_msg_origin)
        session = self._session(binding.card_name or "unknown", binding.chat_name)
        session.messages.append(
            chat_store.ChatMessage(name=sender_name, mes=text, is_user=True, send_date=_now_iso())
        )
        self.store.save(session)

    def record_assistant(self, unified_msg_origin: str, text: str) -> None:
        binding = self.binding(unified_msg_origin)
        card = self.get_card(binding.card_id)
        session = self._session(binding.card_name or card.name, binding.chat_name)
        session.messages.append(
            chat_store.ChatMessage(name=card.name, mes=text, is_user=False, send_date=_now_iso())
        )
        self.store.save(session)
        self.activation_state(unified_msg_origin).next_turn()

    def greeting(self, unified_msg_origin: str) -> str:
        """Return the card's opening message the first time a chat starts."""
        binding = self.binding(unified_msg_origin)
        if binding.greeting_sent or not binding.card_id:
            return ""
        card = self.get_card(binding.card_id)
        text = card.first_mes
        if not text:
            return ""
        binding.greeting_sent = True
        self.save_state()
        self.record_assistant(unified_msg_origin, text)
        return text

    def history_preview(self, unified_msg_origin: str, limit: int = 6) -> list[str]:
        binding = self.binding(unified_msg_origin)
        if not binding.card_name:
            return []
        try:
            session = self.store.load(binding.card_name, binding.chat_name)
        except chat_store.ChatStoreError:
            return []
        preview = []
        for message in session.messages[-limit:]:
            speaker = message.name or ("\u4f60" if message.is_user else "\u89d2\u8272")
            text = message.mes.replace("\n", " ")
            if len(text) > 60:
                text = text[:60] + "..."
            preview.append(f"{speaker}: {text}")
        return preview


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


@dataclass
class _HistoryMessage:
    """Minimal adapter so stored messages satisfy the prompt protocol."""

    role: str
    content: str
    name: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> _HistoryMessage:
        role = as_str(payload.get("role"), "user") or "user"
        content = as_str(payload.get("content"))
        name = payload.get("name")
        return cls(role=role, content=content, name=as_str(name) or None)


def _history_from_store(
    messages: list[Any], card: CharacterCard, sender_name: str
) -> list[_HistoryMessage]:
    history: list[_HistoryMessage] = []
    for message in messages:
        is_user = bool(getattr(message, "is_user", False))
        history.append(
            _HistoryMessage(
                role="user" if is_user else "assistant",
                content=getattr(message, "mes", "") or "",
                name=sender_name if is_user else card.name,
            )
        )
    return history


def _targets_from_entries(entries: list[worldbook.WorldInfoEntry]) -> InChatTargets:
    grouped: dict[int, list[str]] = {}
    for entry in entries:
        if entry.content:
            grouped.setdefault(entry.position, []).append(entry.content)
    return InChatTargets(
        at_depth=grouped.get(worldbook.POSITION_AT_DEPTH, []),
        an_top=grouped.get(worldbook.POSITION_ANT_TOP, []),
        an_bottom=grouped.get(worldbook.POSITION_ANT_BOTTOM, []),
        em_top=grouped.get(worldbook.POSITION_EM_TOP, []),
        em_bottom=grouped.get(worldbook.POSITION_EM_BOTTOM, []),
        before_char=grouped.get(worldbook.POSITION_BEFORE_CHAR, []),
        after_char=grouped.get(worldbook.POSITION_AFTER_CHAR, []),
    )


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _safe_target(directory: Path, filename: str, *, overwrite: bool = False) -> Path:
    stem = chat_store.sanitize_filename(Path(filename).stem) or "unnamed"
    suffix = Path(filename).suffix.lower()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{stem}{suffix}"
    if target.exists() and not overwrite:
        index = 2
        while (directory / f"{stem}-{index}{suffix}").exists():
            index += 1
        target = directory / f"{stem}-{index}{suffix}"
    return target


def _card_from_png_bytes(payload: bytes) -> CharacterCard:
    from tavern.st.cards import card_from_png

    return card_from_png(payload)


def _card_from_yaml_bytes(payload: bytes) -> CharacterCard:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise TavernError(
            "\u5bfc\u5165 YAML \u89d2\u8272\u5361\u9700\u8981 PyYAML\uff0c\u8bf7\u5b89\u88c5\u540e\u91cd\u8bd5\u3002"
        ) from exc
    data = yaml.safe_load(payload.decode("utf-8"))
    if not isinstance(data, dict):
        raise TavernError("YAML \u89d2\u8272\u5361\u5185\u5bb9\u4e0d\u662f\u5bf9\u8c61\u3002")
    return card_from_dict(data)


# ----------------------------------------------------------------------
# rendering
# ----------------------------------------------------------------------


def strip_status_blocks(text: str) -> str:
    """Remove self-inserted status panels models like to append."""
    cleaned = _STATUS_BLOCK.sub("", text)
    cleaned = _FENCED_STATUS.sub("", cleaned)
    return cleaned.strip("\n")


def apply_regex_rules(text: str, rules: list[str]) -> str:
    """Apply ``pattern=>replacement`` rules in order.

    Rule syntax matches the plugin config: the left side is a Python regex, the
    right side the replacement (back references work). Malformed rules are
    skipped so one bad entry cannot break every reply.
    """
    for rule in rules:
        if "=>" not in rule:
            continue
        pattern, _, replacement = rule.partition("=>")
        try:
            text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
        except re.error:
            continue
    return text


def split_message(text: str, max_chars: int) -> list[str]:
    """Split an answer into chat sized chunks, preferring paragraph borders."""
    text = text.strip("\n")
    if max_chars <= 0 or len(text) <= max_chars:
        return [text] if text else []

    chunks: list[str] = []
    buffer = ""
    for paragraph in re.split(r"(\n\s*\n)", text):
        if not paragraph:
            continue
        if len(buffer) + len(paragraph) <= max_chars:
            buffer += paragraph
            continue
        if buffer.strip():
            chunks.append(buffer.strip("\n"))
        buffer = ""
        if len(paragraph) <= max_chars:
            buffer = paragraph
            continue
        for line in paragraph.splitlines(keepends=True):
            if len(buffer) + len(line) <= max_chars:
                buffer += line
                continue
            if buffer.strip():
                chunks.append(buffer.strip("\n"))
            buffer = ""
            while len(line) > max_chars:
                chunks.append(line[:max_chars])
                line = line[max_chars:]
            buffer = line
    if buffer.strip():
        chunks.append(buffer.strip("\n"))
    return [chunk for chunk in chunks if chunk.strip()]


def protect_leading_space(text: str) -> str:
    """Guard leading/trailing whitespace against the platform's strip()."""
    if not text:
        return text
    prefix = "\u200b" if text[:1] in " \t\n" else ""
    suffix = "\u200b" if text[-1:] in " \t\n" else ""
    return f"{prefix}{text}{suffix}"


def render_answer(text: str, *, config: TavernConfig) -> list[str]:
    """Clean and split a model answer into sendable chunks."""
    result = text.strip()
    if config.render.strip_status_bar:
        result = strip_status_blocks(result)
    if config.render.regex_rules:
        result = apply_regex_rules(result, config.render.regex_rules)
    chunks = split_message(result, config.render.max_chars_per_message)
    if config.render.keep_leading_space:
        chunks = [protect_leading_space(chunk) for chunk in chunks]
    return chunks


def request_preview(turn: Turn, limit: int = 12) -> str:
    """Human readable dump of what is about to be sent (``/tavern preview``)."""
    lines = turn.debug_lines
    lines.append("messages (first %d):" % limit)
    for index, message in enumerate(messages_to_openai(turn.request)[:limit], start=1):
        content = as_str(message.get("content")).replace("\n", " / ")
        if len(content) > 160:
            content = content[:160] + "..."
        lines.append(f"  {index}. [{message.get('role')}] {content}")
    return "\n".join(lines)


__all__ = [
    "PluginCore",
    "SessionBinding",
    "TavernError",
    "Turn",
    "apply_regex_rules",
    "protect_leading_space",
    "render_answer",
    "request_preview",
    "split_message",
    "strip_status_blocks",
]
