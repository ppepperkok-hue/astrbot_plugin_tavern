"""Tests for the external-tavern fallback.

The behaviour that matters most is not "does it fall back" but "can it fall back
*invisibly*". The whole reason the feature is opt-in with a notice is that a silent
swap of the answering model mid-conversation is worse than an error, so most of these
assert visibility rather than function.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tavern.backends.base import (  # noqa: E402
    BackendError,
    GenerationRequest,
    GenerationResult,
    PromptMessage,
)
from tavern.backends.fallback import (  # noqa: E402
    MARKER,
    REASON,
    FallbackBackend,
    annotate,
    build_fallback,
)
from tavern.config import BackendConfig  # noqa: E402


class Stub:
    """A backend that either answers or raises ``BackendError``."""

    def __init__(self, name: str, *, text: str = "", fail: str = "") -> None:
        self.name = name
        self.text = text
        self.fail = fail
        self.calls = 0
        self.closed = 0

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls += 1
        if self.fail:
            raise BackendError(self.fail)
        return GenerationResult(text=self.text, model=self.name)

    async def close(self) -> None:
        self.closed += 1


def run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


def request() -> GenerationRequest:
    return GenerationRequest(messages=[PromptMessage(role="user", content="hi")])


# ---------------------------------------------------------------------------
# it falls back
# ---------------------------------------------------------------------------


def test_a_backend_failure_falls_back() -> None:
    primary = Stub("tavern", fail="无法连接酒馆")
    secondary = Stub("astrbot", text="from astrbot")
    backend = FallbackBackend(primary, secondary)
    req = request()

    result = run(backend.generate(req))
    assert result.text == "from astrbot"
    assert primary.calls == 1
    assert secondary.calls == 1


def test_the_success_path_never_touches_the_fallback() -> None:
    primary = Stub("tavern", text="from tavern")
    secondary = Stub("astrbot", text="from astrbot")
    backend = FallbackBackend(primary, secondary)

    result = run(backend.generate(request()))
    assert result.text == "from tavern"
    assert secondary.calls == 0, "the fallback must not be called when the tavern works"


def test_a_non_backend_error_propagates() -> None:
    """A bug in the port is not "the tavern is down" and must not be swallowed."""

    class Buggy(Stub):
        async def generate(self, request: GenerationRequest) -> GenerationResult:
            raise ValueError("a real bug")

    backend = FallbackBackend(Buggy("tavern"), Stub("astrbot", text="x"))
    with pytest.raises(ValueError, match="a real bug"):
        run(backend.generate(request()))


# ---------------------------------------------------------------------------
# it cannot fall back invisibly
# ---------------------------------------------------------------------------


def test_the_fallback_is_marked_on_the_request() -> None:
    """The marker is how the caller learns it happened. Without it, silence."""
    primary = Stub("tavern", fail="超时")
    backend = FallbackBackend(primary, Stub("astrbot", text="ok"))
    req = request()

    run(backend.generate(req))
    assert req.extra.get(MARKER) is True
    assert "超时" in req.extra.get(REASON, "")


def test_the_marker_is_not_left_over_from_an_earlier_call() -> None:
    """A stale marker would announce a fallback that did not happen this turn."""
    primary = Stub("tavern", fail="down")
    backend = FallbackBackend(primary, Stub("astrbot", text="ok"))
    req = request()

    run(backend.generate(req))
    assert req.extra.get(MARKER) is True

    primary.fail = ""
    primary.text = "recovered"
    run(backend.generate(req))
    assert MARKER not in req.extra, "the marker must describe this call, not the last one"
    assert REASON not in req.extra


def test_the_success_path_leaves_no_marker() -> None:
    backend = FallbackBackend(Stub("tavern", text="fine"), Stub("astrbot"))
    req = request()
    run(backend.generate(req))
    assert MARKER not in req.extra


# ---------------------------------------------------------------------------
# the notice itself
# ---------------------------------------------------------------------------


def test_annotate_prefixes_and_names_both_sides() -> None:
    text = annotate("hello", "无法连接酒馆 http://x:8000", notice=True)
    assert text.endswith("hello")
    assert "外部酒馆不可用" in text
    assert "AstrBot" in text
    assert "无法连接酒馆" in text


def test_annotate_can_be_disabled() -> None:
    assert annotate("hello", "why", notice=False) == "hello"


def test_annotate_does_not_prefix_empty_text() -> None:
    """An empty answer with a warning glued on is still an empty answer."""
    assert annotate("", "why", notice=True) == ""


def test_annotate_flattens_and_clips_a_multiline_reason() -> None:
    reason = "line one\nline two\n" + "x" * 400
    text = annotate("body", reason, notice=True)
    head = text.split("\n\n")[0]
    assert "\n" not in head, "the notice must stay on one line"
    assert len(head) < 200, head
    assert "line one line two" in head


# ---------------------------------------------------------------------------
# construction
# ---------------------------------------------------------------------------


def test_no_wrapper_when_the_config_is_off() -> None:
    primary = Stub("tavern", text="x")
    built = build_fallback(BackendConfig(fallback_to_astrbot=False), primary, lambda: Stub("a"))
    assert built is primary, "the default path must carry no extra object"


def test_the_secondary_is_not_constructed_until_it_is_needed() -> None:
    built_factories: list[int] = []

    def factory() -> Stub:
        built_factories.append(1)
        return Stub("astrbot", text="late")

    primary = Stub("tavern", text="fine")
    built = build_fallback(BackendConfig(fallback_to_astrbot=True), primary, factory)
    assert not built_factories, "building the wrapper must not build the fallback"

    run(built.generate(request()))
    assert not built_factories, "a successful turn must not build the fallback either"

    primary.fail = "down"
    result = run(built.generate(request()))
    assert built_factories == [1], "it is built exactly once, on first need"
    assert result.text == "late"


def test_the_lazy_backend_resolves_a_provider_id_on_first_use() -> None:
    """The AstrBot backend needs a provider id resolved before it can answer."""
    resolved: list[int] = []

    class Provider(Stub):
        async def resolve_provider_id(self) -> str:
            resolved.append(1)
            return "openai/gpt-4o"

        async def generate(self, request: GenerationRequest) -> GenerationResult:
            self.calls += 1
            return GenerationResult(text=f"provider={request.extra.get('provider_id')}")

    primary = Stub("tavern", fail="down")
    built = build_fallback(
        BackendConfig(fallback_to_astrbot=True), primary, lambda: Provider("astrbot")
    )
    result = run(built.generate(request()))

    assert resolved == [1]
    assert result.text == "provider=openai/gpt-4o"


def test_close_closes_both_without_short_circuiting() -> None:
    class BadClose(Stub):
        async def close(self) -> None:
            raise RuntimeError("close failed")

    primary = BadClose("tavern")
    secondary = Stub("astrbot")
    backend = FallbackBackend(primary, secondary)
    run(backend.close())
    assert secondary.closed == 1, "one backend failing to close must not skip the other"
