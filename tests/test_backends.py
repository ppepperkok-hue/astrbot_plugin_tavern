"""Offline tests for the backend layer (no AstrBot, no network)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tavern.backends.astrbot_provider import (  # noqa: E402
    AstrBotProviderBackend,
)
from tavern.backends.base import (  # noqa: E402
    BackendError,
    GenerationRequest,
    PromptMessage,
    messages_to_openai,
)
from tavern.backends.sillytavern import SillyTavernBackend, error_message  # noqa: E402


def _run(coro: Any) -> Any:
    """Run a coroutine from a synchronous test (pytest-asyncio is optional)."""
    import asyncio

    return asyncio.run(coro)


class FakeResponse:
    def __init__(self, text: str = "hello") -> None:
        self.completion_text = text
        self.model = "fake-model"
        self.finish_reason = "stop"
        self.usage = {"total_tokens": 3}


class FakeContextNew:
    """Mimics AstrBot >= 4.5.7: llm_generate accepts ``contexts``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def get_current_chat_provider_id(self, umo: str | None = None) -> str:
        return "provider-from-session"

    async def llm_generate(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        return FakeResponse()


class FakeContextOld:
    """Mimics an older AstrBot whose ``llm_generate`` only takes ``prompt``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def llm_generate(self, chat_provider_id: str = "", prompt: str = "") -> FakeResponse:
        self.calls.append({"chat_provider_id": chat_provider_id, "prompt": prompt})
        return FakeResponse("legacy")


def make_request() -> GenerationRequest:
    return GenerationRequest(
        messages=[
            PromptMessage(role="user", content="first turn"),
            PromptMessage(role="assistant", content="answer"),
            PromptMessage(role="user", content="second turn"),
        ],
        system_prompt="you are Iris",
    )


def test_messages_to_openai_prepends_system() -> None:
    request = make_request()
    payload = messages_to_openai(request)
    assert payload[0] == {"role": "system", "content": "you are Iris"}
    assert [entry["content"] for entry in payload[1:]] == ["first turn", "answer", "second turn"]


def test_messages_to_openai_does_not_double_system() -> None:
    request = GenerationRequest(
        messages=[
            PromptMessage(role="system", content="inline"),
            PromptMessage(role="user", content="hi"),
        ],
        system_prompt="separate",
    )
    payload = messages_to_openai(request)
    assert [entry["role"] for entry in payload] == ["system", "user"]
    assert payload[0]["content"] == "inline"


def test_supports_contexts_probe() -> None:
    assert AstrBotProviderBackend(FakeContextNew()).supports_contexts() is True
    assert AstrBotProviderBackend(FakeContextOld()).supports_contexts() is False


def test_build_kwargs_uses_contexts_without_duplicating_prompt() -> None:
    backend = AstrBotProviderBackend(FakeContextNew())
    kwargs = backend.build_kwargs(make_request(), "provider-1")
    assert kwargs["chat_provider_id"] == "provider-1"
    assert kwargs["system_prompt"] == "you are Iris"
    # ``prompt`` must not be passed along with ``contexts``: AstrBot would append
    # it as an extra user message and duplicate the last turn.
    assert "prompt" not in kwargs
    assert [entry["content"] for entry in kwargs["contexts"]] == [
        "first turn",
        "answer",
        "second turn",
    ]


def test_build_kwargs_legacy_flattens_history() -> None:
    backend = AstrBotProviderBackend(FakeContextOld())
    kwargs = backend.build_kwargs(make_request(), "provider-1")
    assert "contexts" not in kwargs
    assert "first turn" in kwargs["prompt"]
    assert "second turn" in kwargs["prompt"]
    assert kwargs["system_prompt"] == "you are Iris"


def test_generate_returns_text_and_usage() -> None:
    context = FakeContextNew()
    backend = AstrBotProviderBackend(context)
    result = _run(backend.generate(make_request()))
    assert result.text == "hello"
    assert result.model == "fake-model"
    assert result.usage == {"total_tokens": 3}
    # The backend itself does not resolve a provider id: main.py asks the
    # context first so a non-blocking API call stays out of the backend.
    assert "chat_provider_id" not in context.calls[0]
    provider_id = _run(backend.resolve_provider_id("aiocqhttp:GroupMessage:1"))
    assert provider_id == "provider-from-session"


def test_generate_wraps_provider_errors() -> None:
    class Exploding(FakeContextNew):
        async def llm_generate(self, **kwargs: Any) -> FakeResponse:
            raise RuntimeError("model exploded")

    backend = AstrBotProviderBackend(Exploding())
    with pytest.raises(BackendError) as error:
        _run(backend.generate(make_request()))
    assert "model exploded" in str(error.value)


def test_generate_rejects_empty_request() -> None:
    backend = AstrBotProviderBackend(FakeContextNew())
    with pytest.raises(BackendError):
        _run(backend.generate(GenerationRequest()))


# ----------------------------------------------------------------------
# SillyTavern backend
# ----------------------------------------------------------------------


def test_st_backend_requires_base_url() -> None:
    with pytest.raises(BackendError):
        SillyTavernBackend("")


# ---------------------------------------------------------------------------
# SillyTavern CSRF / auth behaviour
#
# The whole `generate` path used to be untested: only `build_payload` and
# `parse_response` had coverage, so nothing checked that the CSRF token actually
# reaches the request or that the 401/403 retry exists. These drive it through a
# fake client -- no network, no httpx.
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or (str(payload) if payload is not None else "")
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


_UNSET: Any = object()


class _FakeStClient:
    """Minimal stand-in for ``httpx.AsyncClient``.

    ``reject_first_post`` makes the first ``/generate`` answer 401 (or 403) so the
    refresh-and-retry branch can be exercised; ``reject_all_posts`` makes every
    one fail so the "do not loop" path can be. ``csrf_status`` controls the token
    endpoint, and ``csrf_body=_UNSET`` is the default token response -- pass an
    explicit value (``None``) to model a non-JSON body. Every request is recorded
    for the assertions.
    """

    def __init__(
        self,
        *,
        token: str = "tok-1",
        reject_first_post: int = 0,
        reject_all_posts: int = 0,
        csrf_status: int = 200,
        csrf_body: Any = _UNSET,
        generate_body: Any = _UNSET,
    ) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, str, str]] = []
        self.token = token
        self.reject_first_post = reject_first_post
        self.reject_all_posts = reject_all_posts
        self.csrf_status = csrf_status
        self.csrf_body = {"token": token} if csrf_body is _UNSET else csrf_body
        self.generate_body = (
            {"choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}]}
            if generate_body is _UNSET
            else generate_body
        )

    async def get(self, path: str) -> _FakeResponse:
        self.calls.append(("GET", path, self.headers.get("x-csrf-token", "")))
        if self.csrf_status >= 400:
            return _FakeResponse(self.csrf_status, None, "boom")
        return _FakeResponse(200, self.csrf_body)

    async def post(self, path: str, json: Any = None) -> _FakeResponse:
        sent = self.headers.get("x-csrf-token", "")
        self.calls.append(("POST", path, sent))
        if self.reject_all_posts:
            return _FakeResponse(self.reject_all_posts, None, "expired")
        if self.reject_first_post and sum(1 for c in self.calls if c[0] == "POST") == 1:
            return _FakeResponse(self.reject_first_post, None, "expired")
        if isinstance(self.generate_body, _FakeResponse):
            return self.generate_body
        return _FakeResponse(200, self.generate_body)

    async def aclose(self) -> None:
        return None


def _st_backend(client: _FakeStClient, **kwargs: Any) -> SillyTavernBackend:
    backend = SillyTavernBackend("http://127.0.0.1:8000", cookie="session=abc", **kwargs)

    async def fake_get_client() -> _FakeStClient:
        # Mirror the real `_get_client`'s one observable side effect: the client's
        # headers carry whatever we hold when the client is built.
        if not client.headers:
            client.headers.update(backend._headers())
        backend._client = client
        return client

    backend._get_client = fake_get_client  # type: ignore[method-assign]
    return backend


def _st_request() -> GenerationRequest:
    return GenerationRequest(messages=[PromptMessage(role="user", content="hi")])


def test_st_generate_fetches_and_sends_the_csrf_token() -> None:
    """A request with no token must fetch one first, and send it."""
    client = _FakeStClient(token="tok-1")
    backend = _st_backend(client)
    result = _run(backend.generate(_st_request()))

    assert result.text == "hello"
    assert [call[0] for call in client.calls] == ["GET", "POST"]
    assert client.calls[1][2] == "tok-1", "the token the endpoint handed out must be the one sent"


def test_st_generate_refreshes_once_on_401_and_retries() -> None:
    """An expired CSRF token must be refreshed and the request retried, once."""
    client = _FakeStClient(token="tok-2", reject_first_post=403)
    backend = _st_backend(client, csrf_token="stale")
    result = _run(backend.generate(_st_request()))

    assert result.text == "hello"
    posts = [call for call in client.calls if call[0] == "POST"]
    assert len(posts) == 2, "the request must be retried exactly once"
    assert [call[0] for call in client.calls] == ["GET", "POST", "GET", "POST"]


def test_st_generate_does_not_loop_when_the_retry_also_fails() -> None:
    """A second 401 must surface, not retry forever."""
    client = _FakeStClient(reject_all_posts=401)
    backend = _st_backend(client, csrf_token="stale")
    with pytest.raises(BackendError) as info:
        _run(backend.generate(_st_request()))

    assert "401" in str(info.value)
    assert len([call for call in client.calls if call[0] == "POST"]) == 2, (
        "the original attempt plus exactly one retry"
    )


def test_st_csrf_failure_reports_the_status_not_the_cookie() -> None:
    """A 500 from the token endpoint is a server problem, and must say so.

    The old message asserted the cookie was wrong for *any* failure status, which
    sends the user to re-copy a credential that was never the issue.
    """
    client = _FakeStClient(csrf_status=500)
    backend = _st_backend(client)
    with pytest.raises(BackendError) as info:
        _run(backend.generate(_st_request()))

    message = str(info.value)
    assert "500" in message
    assert "cookie" not in message.lower(), "a 5xx is not a credential problem"
    assert not [call for call in client.calls if call[0] == "POST"], "do not generate blind"


def test_st_csrf_401_blames_the_cookie() -> None:
    """...but a 401/403 from the token endpoint really is the cookie."""
    client = _FakeStClient(csrf_status=403)
    backend = _st_backend(client)
    with pytest.raises(BackendError) as info:
        _run(backend.generate(_st_request()))

    assert "cookie" in str(info.value).lower()


def test_st_csrf_disabled_is_not_an_error() -> None:
    """``--disableCsrf`` makes ``/csrf-token`` answer non-JSON; generate must proceed."""
    client = _FakeStClient(csrf_status=200, csrf_body=None)
    backend = _st_backend(client)
    result = _run(backend.generate(_st_request()))

    assert result.text == "hello"
    assert backend._csrf_token == ""


def test_st_generate_error_body_is_rendered_readably() -> None:
    """A provider error in the body must not be shown as a structure."""
    client = _FakeStClient(
        generate_body={"error": {"message": "model overloaded", "code": "overloaded"}}
    )
    backend = _st_backend(client)
    with pytest.raises(BackendError) as info:
        _run(backend.generate(_st_request()))

    message = str(info.value)
    assert "model overloaded" in message
    assert "{" not in message and "}" not in message


def test_st_backend_payload_shape() -> None:
    backend = SillyTavernBackend(
        "http://127.0.0.1:8000/", cookie="session=abc", chat_completion_source="claude"
    )
    request = GenerationRequest(
        messages=[PromptMessage(role="user", content="hi")],
        system_prompt="be Iris",
        model="claude-3",
        max_tokens=128,
        temperature=0.7,
        stop=["</s>"],
    )
    payload = backend.build_payload(request)
    assert payload["messages"][0] == {"role": "system", "content": "be Iris"}
    assert payload["chat_completion_source"] == "claude"
    assert payload["stream"] is False
    assert payload["model"] == "claude-3"
    assert payload["max_tokens"] == 128
    assert payload["temperature"] == 0.7
    assert payload["stop"] == ["</s>"]


def test_st_backend_headers() -> None:
    backend = SillyTavernBackend("http://127.0.0.1:8000", cookie="a=b", csrf_token="tok")
    headers = backend._headers()
    assert headers["Cookie"] == "a=b"
    assert headers["x-csrf-token"] == "tok"


def test_st_parse_response_openai_shape() -> None:
    payload = {
        "model": "gpt-x",
        "usage": {"total_tokens": 5},
        "choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": "hello there"}}
        ],
    }
    text, model, finish, usage = SillyTavernBackend.parse_response(payload)
    assert (text, model, finish) == ("hello there", "gpt-x", "stop")
    assert usage == {"total_tokens": 5}


def test_st_parse_response_anthropic_blocks() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "part one "},
                        {"type": "text", "text": "part two"},
                    ]
                }
            }
        ]
    }
    text, _model, _finish, _usage = SillyTavernBackend.parse_response(payload)
    assert text == "part one part two"


def test_st_parse_response_plain_shapes() -> None:
    assert SillyTavernBackend.parse_response("just text")[0] == "just text"
    assert SillyTavernBackend.parse_response({"content": "c"})[0] == "c"
    assert SillyTavernBackend.parse_response({"completion": "d"})[0] == "d"
    assert SillyTavernBackend.parse_response(123)[0] == "123"


def test_st_base_url_normalised() -> None:
    backend = SillyTavernBackend("http://127.0.0.1:8000///")
    assert backend.base_url == "http://127.0.0.1:8000"


# ---------------------------------------------------------------------------
# error body -> message (openai.js:1635-1639)
#
# Every expectation below was produced by running the *real* engine function, via
# `node tools/st-oracle/run_error_message.mjs`, rather than by reading the source:
# three of them are counter-intuitive enough that reading it led to a wrong port
# (see the docstring). Regenerate the table with
# `python .scratch/show_error_reference.py` after any snapshot bump.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "data", "status_text", "expected"),
    [
        ("string body", "upstream exploded", "", "Unknown error"),
        ("error is a string", {"error": "bad key"}, "", "bad key"),
        ("error.message", {"error": {"message": "model overloaded"}}, "", "model overloaded"),
        (
            "error.code when message is absent",
            {"error": {"code": "rate_limit_exceeded"}},
            "",
            "rate_limit_exceeded",
        ),
        (
            "error.type when message and code are absent",
            {"error": {"type": "invalid_request"}},
            "",
            "invalid_request",
        ),
        (
            "message wins over code",
            {"error": {"code": "x", "message": "real reason"}},
            "",
            "real reason",
        ),
        (
            "nested detail.error",
            {"detail": {"error": {"message": "nested reason"}}},
            "",
            "nested reason",
        ),
        ("top-level message is not consulted", {"message": "quota exceeded"}, "", "Unknown error"),
        ("empty body uses status text", {}, "Internal Server Error", "Internal Server Error"),
        (
            "error beats status text",
            {"error": {"message": "real reason"}},
            "Bad Gateway",
            "real reason",
        ),
        ("nothing at all", {}, "", "Unknown error"),
        (
            "empty error object uses status text",
            {"error": {}},
            "Service Unavailable",
            "Service Unavailable",
        ),
        ("error is a number", {"error": 429}, "", "Unknown error"),
        (
            "detail without error uses status text",
            {"detail": {"message": "other"}},
            "Teapot",
            "Teapot",
        ),
    ],
)
def test_error_message_matches_the_reference(
    label: str, data: Any, status_text: str, expected: str
) -> None:
    assert error_message(data, status_text) == expected, label


def test_error_message_never_renders_a_dict() -> None:
    """The whole point of the function: no ``[object Object]`` / ``{'code': ...}``.

    Upstream annotates this at ``openai.js:1659``. A regression here is
    user-visible -- the error text is what the plugin hands back to the chat.

    The chosen field is ``code``, not ``type``: the reference's
    ``error.message || error.code || error.type`` is a truthiness chain, so the
    first *present* key wins. A port that preferred ``type`` would be tidier and
    wrong.
    """
    rendered = error_message({"error": {"code": 500, "type": "server_error"}})
    assert "{" not in rendered and "}" not in rendered
    assert rendered == "500"
    # ...and `type` is used only when `code` is absent or falsy.
    assert error_message({"error": {"code": "", "type": "server_error"}}) == "server_error"
