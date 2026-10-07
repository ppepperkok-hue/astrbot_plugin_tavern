"""Shared HTTP and authentication plumbing for talking to an external SillyTavern.

Why this module exists
----------------------
Two clients need the same three things, and before this module they were one
implementation plus an argument about copying it:

* the login/CSRF handshake (every ``/api/*`` route except ``/api/users`` sits behind
  a login middleware, and CSRF protection applies to non-GET requests);
* a lazily created ``httpx.AsyncClient`` carrying the cookie and the CSRF header;
* the one-liner that turns a failed response into a readable :class:`BackendError`.

``tavern/backends/sillytavern.py`` uses it to *generate* (proxy a completion), and
``tavern/backends/st_import.py`` uses it to *read* (pull cards, world books and
chats out of a running tavern). Keeping the handshake in one place is the point:
the CSRF refresh path is the part that is easy to get subtly wrong and hard to
notice, and it is now exercised once rather than twice.

The error-message extraction lives here too, because both clients surface provider
errors and both must render them the way the engine does --
``getChatCompletionErrorMessage`` (``openai.js:1635-1639``).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from tavern.backends.base import BackendError

#: How long a fetched CSRF token is trusted before it is re-fetched.
CSRF_TTL_SECONDS = 300.0


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


def import_httpx() -> Any:
    """Import ``httpx`` lazily, with a message a user can act on."""
    try:
        import httpx  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on the host env
        raise BackendError(
            "外部酒馆后端需要 httpx，请在插件目录的 requirements.txt 中加入 httpx。"
        ) from exc
    return httpx


class SillyTavernSession:
    """A cookie + CSRF session against one SillyTavern instance.

    Subclasses add the requests they care about; this class owns connecting,
    authenticating and failing readably. It is deliberately not a
    ``GenerationBackend``: the import client is not a backend and must not be
    mistaken for one.
    """

    def __init__(
        self,
        base_url: str,
        *,
        cookie: str = "",
        csrf_token: str = "",
        verify_ssl: bool = True,
        timeout: float = 120.0,
    ) -> None:
        if not base_url:
            raise BackendError("未配置酒馆地址（backend.st_base_url）。")
        self.base_url = base_url.rstrip("/")
        self.cookie = cookie.strip()
        self._csrf_token = csrf_token.strip()
        self._csrf_fetched_at = 0.0
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
            httpx = import_httpx()
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers(),
                verify=self.verify_ssl,
                timeout=self.timeout,
                follow_redirects=True,
            )
        return self._client

    async def _ensure_csrf(self, force: bool = False) -> None:
        """Fetch ``/csrf-token`` when we do not have a fresh one.

        A non-JSON body means CSRF is disabled on the ST side (``--disableCsrf``),
        so the empty token is not an error. A *failure* status is: every ``/api/*``
        route sits behind the login middleware, so the usual cause is a missing or
        expired ``st_cookie`` -- but the message reports the actual status and body
        rather than blaming the cookie, because a 500 or a 502 from a proxy in
        front of ST is not a credential problem and saying so sends the user to fix
        the wrong thing.
        """
        if (
            self._csrf_token
            and not force
            and (time.monotonic() - self._csrf_fetched_at) < CSRF_TTL_SECONDS
        ):
            return
        client = await self._get_client()
        try:
            response = await client.get("/csrf-token")
        except Exception as exc:  # noqa: BLE001 - network errors must be readable
            raise BackendError(f"无法连接酒馆 {self.base_url}: {exc}") from exc
        if response.status_code >= 400:
            detail = ""
            if response.status_code in (401, 403):
                detail = "（401/403 通常表示 st_cookie 缺失或已过期，请复制登录后的会话 Cookie）"
            elif response.status_code >= 500:
                detail = "（服务端错误，不是凭据问题；请检查酒馆或其反向代理的日志）"
            body = (getattr(response, "text", "") or "").strip()[:200]
            raise BackendError(
                f"获取酒馆 CSRF token 失败（HTTP {response.status_code}）{detail}"
                + (f"，响应：{body}" if body else "")
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

    async def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        retry_on_auth: bool = True,
    ) -> Any:
        """``POST`` a JSON body, refreshing the CSRF token once on 401/403.

        The refresh-and-retry is the whole reason a tavern restart does not
        require the user to touch the plugin: a still-valid session cookie plus a
        stale CSRF token is the common failure, and one retry fixes it. Copied from
        the generation path, which had this and nothing else did.
        """
        await self._ensure_csrf()
        client = await self._get_client()
        try:
            response = await client.post(path, json=payload)
            if retry_on_auth and response.status_code in (401, 403):
                await self._ensure_csrf(force=True)
                response = await client.post(path, json=payload)
        except BackendError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BackendError(f"请求酒馆 {path} 失败: {exc}") from exc

        if response.status_code >= 400:
            body = (getattr(response, "text", "") or "").strip()[:200]
            raise BackendError(
                f"酒馆 {path} 返回 HTTP {response.status_code}" + (f"：{body}" if body else "")
            )
        try:
            return response.json()
        except Exception as exc:  # noqa: BLE001
            body = (getattr(response, "text", "") or "").strip()[:200]
            raise BackendError(f"酒馆 {path} 返回的不是 JSON：{body}") from exc

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None
