"""Fall back to AstrBot's own model when an external tavern fails.

Why this is opt-in, and why it announces itself
-----------------------------------------------
Falling back means the reply comes from a *different* model: different weights,
different preset, different API key and different billing. Doing that silently in the
middle of a conversation is worse than an error, because the user reads a coherent
answer and has no way to know it was not the character's usual model.

So this module exists to make the trade-off *available* without making it invisible:

* ``backend.fallback_to_astrbot`` (default **off**) turns it on;
* ``backend.fallback_notice`` (default on) prefixes the reply with a one-line note
  naming what failed and which backend answered.

The primary failure is always logged at warning level as well: a fallback that only
appears in chat text is easy to miss in logs, and the log is where the cause lives.

Only genuine backend failures trigger it. A ``BackendError`` covers connection
problems, HTTP failures and provider-side error bodies; a bug in the port, a
cancellation or a ``KeyboardInterrupt`` does not, because those are not "the tavern
is down" and swallowing them would hide real faults.
"""

from __future__ import annotations

from typing import Any

from tavern.backends.base import (
    BackendError,
    GenerationBackend,
    GenerationRequest,
    GenerationResult,
)
from tavern.log import logger

__all__ = ["FallbackBackend", "FALLBACK_NOTE_TEMPLATE", "annotate", "build_fallback"]

#: What the user sees instead of a bare error. Deliberately names both sides: which
#: backend failed and which one answered.
FALLBACK_NOTE_TEMPLATE = "⚠️ 外部酒馆不可用（{reason}），本条回复由 AstrBot 的模型生成。\n\n"

#: Key the wrapper sets in ``request.extra`` so the caller can react. It is *left in
#: place* -- the caller reads it after ``generate`` returns, and clears it itself.
MARKER = "fallback_used"
REASON = "fallback_reason"


class FallbackBackend:
    """Try ``primary``; on a backend failure, use ``secondary`` and mark the request.

    Satisfies :class:`~tavern.backends.base.GenerationBackend`, so a caller can only
    tell what happened by reading :data:`MARKER` off the request after the call.
    """

    def __init__(self, primary: GenerationBackend, secondary: GenerationBackend) -> None:
        self.primary = primary
        self.secondary = secondary
        self.name = f"{getattr(primary, 'name', 'primary')}+fallback"

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        # Clear first: the marker must describe *this* call, not an earlier one.
        request.extra.pop(MARKER, None)
        request.extra.pop(REASON, None)
        try:
            return await self.primary.generate(request)
        except BackendError as exc:
            reason = str(exc)
            request.extra[MARKER] = True
            request.extra[REASON] = reason
            logger.warning("外部酒馆失败，回退到 AstrBot 模型：%s", reason)
            return await self.secondary.generate(request)

    async def close(self) -> None:
        """Close both, without letting one failure skip the other."""
        for backend in (self.primary, self.secondary):
            try:
                await backend.close()
            except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                logger.warning("closing %s failed: %s", getattr(backend, "name", backend), exc)


def annotate(text: str, reason: str, *, notice: bool) -> str:
    """Prepend the fallback note, when notices are on and there is text to prefix."""
    if not notice or not text:
        return text
    return FALLBACK_NOTE_TEMPLATE.format(reason=_shorten(reason)) + text


def build_fallback(
    config: Any,
    primary: GenerationBackend,
    secondary_factory: Any,
) -> GenerationBackend:
    """Wrap ``primary`` only when ``config.fallback_to_astrbot`` asks for it.

    Takes the ``BackendConfig`` itself, and returns the primary unchanged when the
    fallback is off, so the common path carries no extra object. ``secondary_factory``
    is wrapped lazily: a fallback that is off must not construct a provider backend,
    and neither must one that never fires.
    """
    if not getattr(config, "fallback_to_astrbot", False):
        return primary
    return FallbackBackend(primary, _LazyBackend(secondary_factory))


class _LazyBackend:
    """Construct the secondary backend on first use.

    Lazy because :class:`~tavern.backends.astrbot_provider.AstrBotProviderBackend`
    resolves a provider id against the host -- a network-touching operation that has
    no business running on a path the user may never take.
    """

    def __init__(self, factory: Any) -> None:
        self._factory = factory
        self._backend: Any = None
        self.name = "astrbot"

    def _resolve(self) -> Any:
        if self._backend is None:
            self._backend = self._factory()
        return self._backend

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        backend = self._resolve()
        if not request.extra.get("provider_id"):
            resolver = getattr(backend, "resolve_provider_id", None)
            if resolver is not None:
                request.extra["provider_id"] = await resolver()
        return await backend.generate(request)

    async def close(self) -> None:
        if self._backend is not None:
            await self._backend.close()
            self._backend = None


def _shorten(reason: str, limit: int = 120) -> str:
    """One line for a chat message; the full text stays in the log."""
    flat = " ".join(str(reason).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
