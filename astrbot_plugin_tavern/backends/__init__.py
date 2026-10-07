"""Backend package for the tavern plugin.

``base``      - the interface and shared dataclasses (no astrbot import).
``astrbot``   - AstrBot provider backend (uses ``context.llm_generate``).
``sillytavern`` - optional backend that proxies generation to SillyTavern.
"""

from .base import (
    BackendError,
    GenerationBackend,
    GenerationRequest,
    GenerationResult,
    PromptMessage,
    messages_to_openai,
)

__all__ = [
    "BackendError",
    "GenerationBackend",
    "GenerationRequest",
    "GenerationResult",
    "PromptMessage",
    "messages_to_openai",
]
