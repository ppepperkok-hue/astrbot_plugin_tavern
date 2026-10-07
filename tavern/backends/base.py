"""Generation backends: one interface, two implementations.

The plugin never talks to a model directly. Everything goes through
:class:`GenerationBackend`, so the same character card / world info pipeline can
be served either by AstrBot's own model providers or by an already deployed
SillyTavern instance.

Only the *input* is passed in: the backend receives the fully assembled message
list and returns text. Prompt assembly, world info activation and history
management stay in the ``st`` layer, because SillyTavern's server side does not
do any of that (its world info and prompt managers run in the browser).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

DEFAULT_TIMEOUT = 120.0


@dataclass(slots=True)
class PromptMessage:
    """One message in the list a backend has to generate from.

    Kept structurally identical to ``st.prompt.PromptMessage`` without
    importing it, so the backend layer can be tested on its own.
    """

    role: str
    content: str
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            payload["name"] = self.name
        return payload


@dataclass(slots=True)
class GenerationRequest:
    """Everything a backend may need for a single turn."""

    #: Full message list, oldest first. The first entry is normally the system
    #: message carrying the character definition and the stable world info.
    messages: list[PromptMessage] = field(default_factory=list)
    #: Stable system prompt extracted from the assembled blocks.
    system_prompt: str = ""
    #: Provider / model override coming from the plugin config.
    model: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] = field(default_factory=list)
    #: Free-form extras handed to the concrete backend (for example the
    #: SillyTavern ``chat_completion_source`` or AstrBot provider id).
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GenerationResult:
    """The backend's answer."""

    text: str = ""
    model: str = ""
    finish_reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    raw: Any = None


class BackendError(RuntimeError):
    """Any backend failure; message is safe to log and to show to the user."""


@runtime_checkable
class GenerationBackend(Protocol):
    """Minimal surface the plugin depends on."""

    #: Human readable identifier, also used in ``/酒馆 状态`` output.
    name: str

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...

    async def close(self) -> None: ...


def messages_to_openai(request: GenerationRequest) -> list[dict[str, Any]]:
    """Convert a request into the OpenAI ``messages`` array.

    ``system_prompt`` is prepended when it is not already the first message, so
    callers that keep the system prompt separately still get it delivered.
    """
    messages = [message.to_dict() for message in request.messages]
    if request.system_prompt and not (messages and messages[0].get("role") == "system"):
        messages.insert(0, {"role": "system", "content": request.system_prompt})
    return messages


def merge_stream_chunks(chunks: Sequence[str]) -> str:
    """Join streamed text chunks (helper shared by the tests and backends)."""
    return "".join(chunks)


async def collect_stream(stream: AsyncIterator[str]) -> str:
    parts: list[str] = []
    async for chunk in stream:
        parts.append(chunk)
    return "".join(parts)
