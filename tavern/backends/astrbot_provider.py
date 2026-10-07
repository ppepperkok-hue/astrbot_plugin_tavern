"""Generation backend that uses AstrBot's configured model providers.

This is the default backend: no extra service is required, the model, key and
proxy settings already configured in AstrBot are reused.

The only interesting part is that ``llm_generate`` accepts *both* a flat
``prompt`` (single user turn) and ``system_prompt``/``contexts`` (pre-assembled
conversation), so the tavern pipeline can hand over exactly the messages it
built instead of letting AstrBot rebuild them from its own conversation store.
"""

from __future__ import annotations

import inspect
from typing import Any

from tavern.backends.base import (
    BackendError,
    GenerationRequest,
    GenerationResult,
    messages_to_openai,
)

_BACKEND_NAME = "astrbot"


class AstrBotProviderBackend:
    """Wraps ``Context.llm_generate`` behind the :class:`GenerationBackend` protocol."""

    name = _BACKEND_NAME

    def __init__(self, context: Any, provider_id: str | None = None) -> None:
        self._context = context
        self._provider_id = provider_id or None
        self._accepts_contexts: bool | None = None

    # -- capability probe -------------------------------------------------
    def supports_contexts(self) -> bool:
        """Detect whether this AstrBot version exposes the ``contexts`` kwarg.

        Old versions only accept a flat ``prompt``; probing once keeps the
        plugin working across 4.x releases without a version table.
        """
        if self._accepts_contexts is None:
            try:
                signature = inspect.signature(self._context.llm_generate)
            except (TypeError, ValueError):
                self._accepts_contexts = False
            else:
                self._accepts_contexts = "contexts" in signature.parameters or any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in signature.parameters.values()
                )
        return self._accepts_contexts

    async def resolve_provider_id(self, umo: str | None = None) -> str | None:
        """Return the configured provider, falling back to the session's model."""
        if self._provider_id:
            return self._provider_id
        if umo is None:
            return None
        try:
            return await self._context.get_current_chat_provider_id(umo=umo)
        except Exception as exc:  # noqa: BLE001 - degrade to AstrBot's default
            raise BackendError(f"无法获取当前会话的模型提供商: {exc}") from exc

    # -- generation -------------------------------------------------------
    def build_kwargs(self, request: GenerationRequest, provider_id: str | None) -> dict[str, Any]:
        """Translate a :class:`GenerationRequest` into ``llm_generate`` kwargs."""
        kwargs: dict[str, Any] = {}
        if provider_id:
            kwargs["chat_provider_id"] = provider_id

        if request.system_prompt:
            kwargs["system_prompt"] = request.system_prompt

        if self.supports_contexts():
            # Drop the system entry: it is passed separately above so AstrBot
            # can apply its own persona/tool handling around it.
            #
            # Do NOT also pass ``prompt``: when both are given AstrBot appends
            # ``prompt`` as one more user message, which would duplicate the
            # final turn of the assembled history.
            contexts = [
                message
                for message in messages_to_openai(request)
                if message.get("role") != "system"
            ]
            kwargs["contexts"] = contexts
        else:
            # Legacy path: flatten everything into one prompt.
            kwargs["prompt"] = "\n\n".join(
                f"{message.role}: {message.content}"
                for message in request.messages
                if message.content
            )

        for key in ("max_tokens", "temperature", "top_p"):
            value = getattr(request, key)
            if value is not None:
                kwargs[key] = value
        if request.stop:
            kwargs["stop"] = request.stop
        return kwargs

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        provider_id = request.extra.get("provider_id") or self._provider_id
        kwargs = self.build_kwargs(request, provider_id)
        if not kwargs.get("prompt") and not kwargs.get("contexts"):
            raise BackendError("没有可发送的内容：角色卡与历史都是空的。")

        try:
            response = await self._context.llm_generate(**kwargs)
        except BackendError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface a readable error
            raise BackendError(f"AstrBot 模型调用失败: {exc}") from exc

        text = getattr(response, "completion_text", None)
        if text is None:
            text = str(response)
        return GenerationResult(
            text=text or "",
            model=str(getattr(response, "model", "") or ""),
            finish_reason=str(getattr(response, "finish_reason", "") or ""),
            usage=getattr(response, "usage", {}) or {},
            raw=response,
        )

    async def close(self) -> None:
        """Nothing to release: the provider lives inside AstrBot."""
        return None
