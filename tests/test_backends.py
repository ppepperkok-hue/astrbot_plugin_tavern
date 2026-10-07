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
from tavern.backends.sillytavern import SillyTavernBackend  # noqa: E402


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
