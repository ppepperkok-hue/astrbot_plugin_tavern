"""Bounded execution for regex keys imported from untrusted world books.

World-book keys are data, not code, but a key like ``/(a+)+$/`` is still a
program: matching it against a short adversarial string can burn seconds of CPU
inside CPython's backtracking engine. ``re`` has no way to interrupt a match, so
one hostile key can stall an entire poll loop.

The ``regex`` package exposes a per-call wall-clock timeout, which is the only
reliable bound available without putting every match in a subprocess. This
module is the single place that knows about it:

* :func:`compile_bounded` compiles a pattern, returning ``None`` for invalid
  syntax -- the "this key is not a regex" answer every caller already handles.
* :func:`search_bounded` runs one search with :data:`REGEX_MATCH_TIMEOUT_SECONDS`
  and turns a timeout into **no match**. A pattern that cannot finish inside
  the budget is not allowed to activate an entry, but it also must not take the
  bot down.

Fail-closed when the dependency is missing. The alternative (falling back to
``re``) would silently restore the unbounded behaviour this module exists to
remove. ``regex`` is declared in ``requirements.txt`` and ``pyproject.toml`` so
an installed plugin should never reach that branch.
"""

from __future__ import annotations

from typing import Any

from tavern.log import logger

try:  # pragma: no cover - the dependency is declared and installed
    import regex as _regex
except ImportError:  # pragma: no cover - exercised only on a broken install
    _regex = None  # type: ignore[assignment]

#: Wall-clock budget for one untrusted regex search, in seconds.
#:
#: 50 ms is deliberately far below the 3.5 s the audit measured for
#: ``/(a+)+$/``, yet above what ordinary world-book patterns need on a scan
#: window of a few kilobytes. It is per search, not per turn: a book with many
#: hostile keys pays the budget once per key instead of multiplying it.
REGEX_MATCH_TIMEOUT_SECONDS = 0.05

_missing_dependency_warned = False


def _warn_missing_dependency() -> None:
    """Warn once when ``regex`` is not importable, then keep failing closed."""
    global _missing_dependency_warned
    if _missing_dependency_warned:
        return
    _missing_dependency_warned = True
    logger.error(
        "the 'regex' package is missing; world-book regex keys are disabled "
        "until it is installed (pip install 'regex>=2023.1.1')"
    )


def compile_bounded(pattern: str, flags: int = 0) -> Any | None:
    """Compile ``pattern`` for bounded matching, or return ``None`` if invalid.

    The return value is a ``regex.Pattern``. It shares the ``pattern`` /
    ``flags`` / ``search`` surface with ``re.Pattern``, so callers do not need
    to care which engine produced it.
    """
    if _regex is None:
        _warn_missing_dependency()
        return None
    try:
        return _regex.compile(pattern, flags)
    except (_regex.error, ValueError):
        return None


def search_bounded(pattern: Any, text: str, *, timeout: float | None = None) -> bool:
    """Search ``text`` with ``pattern`` under a hard wall-clock timeout.

    ``regex`` raises :class:`TimeoutError` when the budget is exhausted; that
    is reported as "no match" and logged, because a pattern that cannot finish
    is not evidence that the entry should activate.
    """
    limit = REGEX_MATCH_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        return pattern.search(text, timeout=limit) is not None
    except TimeoutError:
        source = str(getattr(pattern, "pattern", pattern))
        if len(source) > 200:
            source = source[:200] + "..."
        logger.warning(
            "world info regex exceeded its %.3f s budget and was treated as no match: %r",
            limit,
            source,
        )
        return False


__all__ = [
    "REGEX_MATCH_TIMEOUT_SECONDS",
    "compile_bounded",
    "search_bounded",
]
