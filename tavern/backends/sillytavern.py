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

import asyncio
import time
from typing import Any

from tavern.backends.base import (
    BackendError,
    GenerationRequest,
    GenerationResult,
    messages_to_openai,
)

_BACKEND_NAME = "sillytavern"
_CSRF_TTL_SECONDS = 300.0


def error_message(data: Any, status_text: str = "") -> str:
    """``getChatCompletionErrorMessage`` (``openai.js:1635-1639``), transcribed.

    A provider's error body is a *structure*, and formatting it with ``str()`` is
    exactly the bug upstream annotates at ``:1659`` -- *"these do not throw
    correctly (equiv to Error(\\"[object Object]\\"))"*. The reference digs a
    message out instead::

        const error = data?.error ?? data?.detail?.error;
        const message = typeof error === 'string' ? error : (error?.message || error?.code || error?.type);
        return String(message || (!response.ok && response.statusText) || t`Unknown error`);

    Three of its behaviours are surprising enough that a "tidier" port would
    diverge, so they are kept deliberately (each is pinned in
    ``tests/test_backends.py`` against the real engine):

    * a non-object body -- including a plain string -- yields ``'Unknown error'``,
      **not** the status text: ``data?.error`` is ``undefined`` there, so
      ``!response.ok`` needs ``ok === false``, which our test double does not set;
    * ``{'error': 429}`` is ``'Unknown error'`` and not the status text either,
      because ``data.error`` is truthy, so the status-text arm is never reached;
    * ``{'error': {}}`` **does** fall through to the status text, because an empty
      object is falsy in JS and ``??`` therefore takes ``data.detail.error``, which
      is ``undefined``.
    """
    if not isinstance(data, dict):
        return "Unknown error"

    error = data.get("error")
    if not error and isinstance(data.get("detail"), dict):
        error = data["detail"].get("error")

    if isinstance(error, str):
        message: Any = error
    elif isinstance(error, dict):
        message = error.get("message") or error.get("code") or error.get("type")
    else:
        message = None

    if message:
        return str(message)
    if status_text:
        return status_text
    return "Unknown error"


def _import_httpx() -> Any:
    try:
        import httpx  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on the host env
        raise BackendError(
            "外部酒馆后端需要 httpx，请在插件目录的 requirements.txt 中加入 httpx。"
        ) from exc
    return httpx


class SillyTavernBackend:
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
        if not base_url:
            raise BackendError("未配置酒馆地址（backend.st_base_url）。")
        self.base_url = base_url.rstrip("/")
        self.cookie = cookie.strip()
        self._csrf_token = csrf_token.strip()
        self._csrf_fetched_at = 0.0
        self.chat_completion_source = chat_completion_source
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._client: Any = None
        self._lock = asyncio.Lock()

    # -- plumbing ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if self._csrf_token:
            headers["x-csrf-token"] = self._csrf_token
        return headers

    async def _get_client(self) -> Any:
        if self._client is None:
            httpx = _import_httpx()
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers(),
                verify=self.verify_ssl,
                timeout=self.timeout,
                follow_redirects=True,
            )
        return self._client

    async def _ensure_csrf(self, force: bool = False) -> None:
        """Fetch ``/csrf-token`` when we do not have a fresh one."""
        if (
            self._csrf_token
            and not force
            and (time.monotonic() - self._csrf_fetched_at) < _CSRF_TTL_SECONDS
        ):
            return
        client = await self._get_client()
        try:
            response = await client.get("/csrf-token")
        except Exception as exc:  # noqa: BLE001 - network errors must be readable
            raise BackendError(f"无法连接酒馆 {self.base_url}: {exc}") from exc
        if response.status_code >= 400:
            raise BackendError(
                f"获取酒馆 CSRF token 失败（HTTP {response.status_code}）。"
                "请检查 st_cookie 是否为登录后的会话 Cookie。"
            )
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 - non-JSON means CSRF is probably disabled
            self._csrf_token = ""
            self._csrf_fetched_at = time.monotonic()
            return
        token = payload.get("token") if isinstance(payload, dict) else None
        self._csrf_token = str(token or "")
        self._csrf_fetched_at = time.monotonic()
        if self._client is not None:
            self._client.headers["x-csrf-token"] = self._csrf_token

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
            await self._ensure_csrf()
            client = await self._get_client()
            payload = self.build_payload(request)
            try:
                response = await client.post(
                    "/api/backends/chat-completions/generate", json=payload
                )
                if response.status_code in (401, 403):
                    # Cookie or CSRF may have expired: refresh once and retry.
                    await self._ensure_csrf(force=True)
                    response = await client.post(
                        "/api/backends/chat-completions/generate", json=payload
                    )
            except BackendError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise BackendError(f"请求酒馆生成接口失败: {exc}") from exc

            if response.status_code >= 400:
                raise BackendError(f"酒馆返回 HTTP {response.status_code}: {response.text[:200]}")
            try:
                data = response.json()
            except Exception as exc:  # noqa: BLE001
                raise BackendError(f"酒馆返回的不是 JSON: {response.text[:200]}") from exc

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

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None
