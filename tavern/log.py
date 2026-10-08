"""The plugin's logger, and the only place the stdlib `logging` module is mentioned.

The plugin marketplace requires a plugin to log through ``astrbot.api.logger`` and
forbids Python's built-in ``logging``. This module is the single point where that
requirement is satisfied, so it is the single point a reviewer has to read.

**Why this re-exports the object instead of wrapping it.** ``astrbot.api.logger`` is not
a logger -- it is a proxy (``_PluginContextLogger``, ``astrbot/api/__init__.py:60``)
whose ``__getattr__`` reads ``sys._getframe(1)`` to find the **calling module** and route
the call to that plugin's dedicated logger (``astrbot.plugin.<name>``), so each plugin's
level can be tuned independently.

That rules out the obvious refactor. A helper in this file that called
``logger.debug(...)`` would make ``tavern.log`` the caller, and every message in the
plugin would be attributed here. Re-exporting the proxy does not have that problem:
``__getattr__`` runs when the attribute is *accessed*, so whichever module writes
``logger.debug(...)`` is the module AstrBot attributes the line to. Hence
``from tavern.log import logger`` at each call site, and no functions below.

**Why there is a fallback at all.** ``tavern/st/`` is a framework-independent port of
SillyTavern that the oracle harness runs under a plain interpreter, with no AstrBot on
the path. The fallback accepts the same calls and discards them; it deliberately
**imports nothing**, and in particular not ``logging``, which is the thing the rule
forbids.

``BACKEND`` records which one is live so a check can assert that the AstrBot logger is
in use *inside* AstrBot. A silent fallback looks exactly like working logging, which is
the kind of failure this project has repeatedly been caught by.

KNOWN-ISSUE: 7 -- v0.1.0 was rejected by the marketplace for using stdlib ``logging`` in
thirteen modules. The logs still appeared, which is why nobody noticed; they simply
belonged to the global logger instead of the plugin's. Do not reintroduce a wrapper
around ``logger`` here: the proxy resolves its caller, so wrapping it would attribute
every line in the plugin to this module.
"""

from __future__ import annotations

from typing import Any

#: Which logger is live: ``"astrbot"`` inside AstrBot, ``"discard"`` outside it.
BACKEND = "discard"

#: Why the AstrBot import failed, when it did. Kept so the failure is inspectable
#: rather than merely silent.
IMPORT_ERROR: BaseException | None = None


class _Discard:
    """Accepts any log call and drops it. Used only outside AstrBot.

    Implements the stdlib ``Logger`` surface actually used by this plugin --
    ``debug``/``info``/``warning``/``error``/``exception``, each taking the printf-style
    ``(message, *args)`` form -- without importing ``logging``.
    """

    def __getattr__(self, item: str) -> Any:
        if item.startswith("__") and item.endswith("__"):
            raise AttributeError(item)

        def _ignore(*_args: Any, **_kwargs: Any) -> None:
            """Discard the call."""

        return _ignore


try:  # pragma: no cover - exercised inside AstrBot
    from astrbot.api import logger

    BACKEND = "astrbot"
except Exception as exc:  # noqa: BLE001 - standing alone is a supported mode
    IMPORT_ERROR = exc
    logger = _Discard()  # type: ignore[assignment]

__all__ = ["BACKEND", "IMPORT_ERROR", "logger"]
