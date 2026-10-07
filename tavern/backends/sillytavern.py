"""Optional backend that proxies generation to an already deployed SillyTavern.

Why this exists
---------------
SillyTavern's own world info and prompt manager run **in the browser**, and its
server exposes only file access plus a provider proxy. The one endpoint that can
produce a reply is::

    POST /api/backends/chat-completions/generate

and it expects an **already assembled** ``messages[]`` array; it does not read
``characters/``/``worlds/``/``chats/`` at all. So using this backend saves the
model call (ST keeps the API keys, presets and provider routing) but never saves
the assembly work.

That is the *generation* half. The *file* half -- pulling cards, world books and
chats out of a running tavern -- is
:mod:`tavern.backends.st_import`, which imports the routes it needs from
``endpoints_characters.js`` / ``endpoints_worldinfo.js``. Both halves share the
login/CSRF handshake in :mod:`tavern.backends.auth`.

Authentication
--------------
Every ``/api/*`` route except ``/api/users`` sits behind a login middleware, and
CSRF protection applies to non-GET requests, so a client needs:

* a session cookie (copied from the logged-in browser, or
  ``--disableCsrf``/basic-auth configured on the ST side), and
* the ``x-csrf-token`` header, obtained from ``GET /csrf-token``.

``httpx`` is used (the plugin rule is "no ``requests``"); it is an optional
dependency, so the import is deferred and reported politely.
"""

from __future__ import annotations

from typing import Any

from tavern.backends.auth import SillyTavernSession, error_message
from tavern.backends.base import (
    BackendError,
    GenerationRequest,
    GenerationResult,
    messages_to_openai,
)

__all__ = ["SillyTavernBackend", "error_message"]

_BACKEND_NAME = "sillytavern"


class SillyTavernBackend(SillyTavernSession):
    """``POST /api/backends/chat-completions/generate`` behind the backend protocol."""

    name = _BACKEND_NAME

    def __init__(
        self,
        base_url: str,
        *,
        cookie: str = "",
        csrf_token: str = "",
        chat_completion_source: str = "custom",
        verify_ssl: bool = True,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(
            base_url,
            cookie=cookie,
            csrf_token=csrf_token,
            verify_ssl=verify_ssl,
            timeout=timeout,
        )
        self.chat_completion_source = chat_completion_source

    # -- generation -------------------------------------------------------
    def build_payload(self, request: GenerationRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messages": messages_to_openai(request),
            "chat_completion_source": request.extra.get(
                "chat_completion_source", self.chat_completion_source
            ),
            "stream": False,
        }
        if request.model:
            payload["model"] = request.model
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.stop:
            payload["stop"] = request.stop
        payload.update({k: v for k, v in request.extra.items() if k not in payload})
        return payload

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        async with self._lock:  # one in-flight request: ST sessions are stateful
            payload = self.build_payload(request)
            data = await self._post_json("/api/backends/chat-completions/generate", payload)

        # A structured error is still an error even at HTTP 200: the proxy reports
        # provider failures in the body.
        if isinstance(data, dict) and (data.get("error") or data.get("detail")):
            raise BackendError(f"酒馆返回错误: {error_message(data)}")
        text, model, finish_reason, usage = self.parse_response(data)
        return GenerationResult(
            text=text, model=model, finish_reason=finish_reason, usage=usage, raw=data
        )

    @staticmethod
    def parse_response(data: Any) -> tuple[str, str, str, dict[str, Any]]:
        """Normalise the several shapes ST returns for chat completions."""
        if isinstance(data, str):
            return data, "", "", {}
        if not isinstance(data, dict):
            return str(data), "", "", {}

        model = str(data.get("model", "") or "")
        usage = data.get("usage") or {}

        if "choices" in data and data["choices"]:
            choice = data["choices"][0]
            finish_reason = str(choice.get("finish_reason", "") or "")
            message = choice.get("message") or {}
            content = message.get("content")
            if content is None:
                content = choice.get("text", "")
            if isinstance(content, list):  # Anthropic style content blocks
                content = "".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            return str(content or ""), model, finish_reason, usage

        for key in ("content", "completion", "text", "message"):
            if key in data:
                value = data[key]
                if isinstance(value, dict):
                    value = value.get("content", "")
                return str(value or ""), model, "", usage
        return "", model, "", usage
