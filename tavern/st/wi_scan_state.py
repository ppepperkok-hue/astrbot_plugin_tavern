# Ported from SillyTavern - public/scripts/world-info.js (scan_state @43-60, the
#  scanState transitions of checkWorldInfo @4766-4771, @5091-5147 and the
#  availableRecursionDelayLevels setup @4753-4762)
# Copyright (C) 2024 SillyTavern contributors
# Licensed under the GNU Affero General Public License v3.0.
# This file is a modified Python translation; modified on 2026-10-07.
# Upstream: https://github.com/SillyTavern/SillyTavern (commit 06bde939)
"""The ``checkWorldInfo`` scan state machine, extracted as pure state.

Nothing here scans entries, reads a book, or touches the prompt.  This module
answers exactly one question -- *given what the pass that just finished produced,
does the engine run another pass, and if so of which kind?* -- so the scan loop
in ``worldbook.activate`` can stay a loop and this part can be tested alone.

``scan_state`` (world-info.js:43-60), verbatim::

    NONE: 0            # "The scan will be stopped."
    INITIAL: 1         # "Initial state."
    RECURSION: 2       # "The scan is triggered by a recursion step."
    MIN_ACTIVATIONS: 3 # "The scan is triggered by a min activations depth skew."

Why four states: ``while (scanState)`` (:4766) is a truthiness loop, so ``NONE
=== 0`` is what terminates it; the other three values are *reasons* the next
pass is being requested, and several branches inside the loop branch on the
reason (``scanState === scan_state.RECURSION`` at :4865/:4870/:5104,
``!==`` at :4860).

Decision order, exactly as the engine applies it (:5096-5133)
------------------------------------------------------------

Per pass, in this order, each later branch able to overwrite the earlier one:

1. ``nextScanState = NONE`` at the top of the loop (:4780).
2. **normal recursion** (:5097-5100) --
   ``world_info_recursive && !token_budget_overflowed &&
   successfulNewEntriesForRecursion.length`` -> ``RECURSION``.  A pass that
   activated nothing leaves the state at ``NONE``, and *that* is how unbounded
   recursion terminates (``world_info_max_recursion_steps === 0``).
3. **min-activations followed by recursion** (:5104-5107) --
   ``world_info_recursive && !token_budget_overflowed &&
   scanState === MIN_ACTIVATIONS && buffer.hasRecurse()`` -> ``RECURSION``.
   Checked after (2) and can only fire when the *current* state is
   ``MIN_ACTIVATIONS``, so it is never reached from ``INITIAL``.
4. **min activations not satisfied** (:5110-5126) --
   ``world_info_min_activations > 0 && allActivatedEntries.size <
   world_info_min_activations``.  Only when ``nextScanState`` is still ``NONE``
   (the ``!nextScanState`` guard, :5111) *and* the depth is not exhausted
   (``world_info_min_activations_depth_max > 0 &&
   buffer.getDepth() > world_info_min_activations_depth_max`` or
   ``buffer.getDepth() > chat.length``) -> ``MIN_ACTIVATIONS`` **and**
   ``buffer.advanceScan()``.  This is the only branch that advances the buffer:
   compare (5), which restarts a recursion pass at the same depth.
5. **left-over recursion delay levels** (:5129-5133) --
   only when ``nextScanState === NONE`` and
   ``availableRecursionDelayLevels.length`` -> ``RECURSION`` and
   ``currentRecursionDelayLevel = availableRecursionDelayLevels.shift()``.  The
   first level was already consumed at setup (:4759), which is why a book with a
   single delay level never reaches this branch.
6. ``max recursion steps`` (:4767-4771) -- ``world_info_max_recursion_steps``
   and ``world_info_min_activations`` are **mutually exclusive**; whenever the
   step budget is non-zero, ``max_recursion_steps <= count`` breaks the loop
   *before* any of the above runs.  ``count`` has already been incremented for
   the current pass at that point (:4774), so a cap of ``N`` allows exactly ``N``
   passes.  ``0`` means "no cap" (:82).

``delayUntilRecursion`` is a **level, not a flag**
--------------------------------------------------

``availableRecursionDelayLevels`` is built once, before the loop (:4754-4757)::

    [...new Set(sortedEntries
        .filter(entry => entry.delayUntilRecursion)
        .map(entry => entry.delayUntilRecursion === true ? 1 : entry.delayUntilRecursion),
    )].sort((a, b) => a - b)

so ``True`` normalises to ``1`` (in Python, ``bool`` is an ``int`` -- the
normaliser checks ``is True`` *first*, or ``True`` would sort as ``1`` anyway but
``False`` would sneak in as ``0``), the levels are deduplicated and sorted
ascending, and the smallest one is shifted out as the starting
``currentRecursionDelayLevel`` (:4759).  Trusting :func:`scan_delay_levels` for
that normalisation is what keeps level ``0`` (which the engine's
``.filter(entry => entry.delayUntilRecursion)`` would have dropped as falsy) out
of the queue.

Deliberate divergences from the JavaScript
------------------------------------------

* The module holds no global settings: ``world_info_recursive``,
  ``world_info_max_recursion_steps``, ``world_info_min_activations`` and
  ``world_info_min_activations_depth_max`` are passed in per call, because the
  engine rebinds them every time settings are saved (:825-836).
* ``buffer.getDepth()`` / ``buffer.advanceScan()`` / ``buffer.hasRecurse()``
  are represented by the three plain values ``buffer_depth``,
  ``max_activations_depth`` and ``has_recurse``; the caller owns the buffer.
* ``eventSource.emit(WORLD_INFO_SCAN_DONE)`` (:5150-5186) is what lets an
  extension rewrite ``scanState`` before the loop continues (:5179-5181).  There
  is no event bus here; :meth:`ScanStateMachine.override_next` is the hook a
  caller can use to model that interposition.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal, NamedTuple

from tavern.log import logger

__all__ = [
    "SCAN_STATE_INITIAL",
    "SCAN_STATE_MIN_ACTIVATIONS",
    "SCAN_STATE_NONE",
    "SCAN_STATE_RECURSION",
    "ScanStateMachine",
    "ScanStep",
    "ScanTurn",
    "scan_delay_levels",
    "scan_state",
    "scan_states_are_exclusive",
]

#: ``scan_state`` (world-info.js:43-60), values kept verbatim.
scan_state: dict[str, int] = {
    "NONE": 0,
    "INITIAL": 1,
    "RECURSION": 2,
    "MIN_ACTIVATIONS": 3,
}

SCAN_STATE_NONE = scan_state["NONE"]
SCAN_STATE_INITIAL = scan_state["INITIAL"]
SCAN_STATE_RECURSION = scan_state["RECURSION"]
SCAN_STATE_MIN_ACTIVATIONS = scan_state["MIN_ACTIVATIONS"]

#: Convenience map for logging, mirrors ``Object.entries(scan_state).find(...)``
#: (world-info.js:4777, 5093).
SCAN_STATE_NAMES: dict[int, str] = {value: key for key, value in scan_state.items()}

ScanStateValue = Literal[0, 1, 2, 3]


class ScanTurn(NamedTuple):
    """What one finished pass produced, i.e. the inputs of :5096-5126.

    Field-by-field provenance:

    ``successful_new_for_recursion``
        ``successfulNewEntriesForRecursion.length`` (:5080, :5097) -- entries
        that activated *and* are not ``preventRecursion``.
    ``token_budget_overflowed``
        the flag set at :5070, guards branches (2) and (3).
    ``activated_total``
        ``allActivatedEntries.size`` (:5110) -- the whole scan so far, not this
        pass.
    ``min_activations`` / ``min_activations_depth_max``
        ``world_info_min_activations`` / ``world_info_min_activations_depth_max``
        (:70-71); ``0`` disables each.
    ``buffer_depth``
        ``buffer.getDepth()`` (:5121, the value a *new* MIN_ACTIVATIONS pass
        would scan with) -- the engine's log line prints ``getDepth() + 1`` after
        calling ``advanceScan()``.
    ``chat_length``
        ``chat.length`` (:5117) -- the depth ceiling.
    ``has_recurse``
        ``buffer.hasRecurse()`` (:5104).
    ``recursive``
        ``world_info_recursive``, the global that gates branches (2) and (3).
    ``max_recursion_steps``
        ``world_info_max_recursion_steps`` (:82); ``0`` is unbounded.
    """

    successful_new_for_recursion: int = 0
    token_budget_overflowed: bool = False
    activated_total: int = 0
    min_activations: int = 0
    min_activations_depth_max: int = 0
    buffer_depth: int = 0
    chat_length: int = 0
    has_recurse: bool = False
    recursive: bool = False
    max_recursion_steps: int = 0


class ScanStep(NamedTuple):
    """The verdict for one pass (the body of :5096-5147)."""

    #: What the *next* pass is for; ``NONE`` means "stop".
    next_state: int
    #: ``buffer.advanceScan()`` should be called before the next pass.
    advance_scan: bool
    #: ``currentRecursionDelayLevel`` after this verdict.
    current_recursion_delay_level: int
    #: Delay levels still queued behind the current one (:5132).
    remaining_delay_levels: tuple[Any, ...]
    #: ``max_recursion_steps <= count`` -- the loop breaks at :4768.
    exhausted_by_max_steps: bool
    #: Number of passes completed, counting the one being judged (:4774).
    count: int

    @property
    def continues(self) -> bool:
        """JS ``while (scanState)`` (:4766): ``NONE === 0`` is the only exit."""
        return self.next_state != SCAN_STATE_NONE

    @property
    def state_name(self) -> str:
        return SCAN_STATE_NAMES.get(self.next_state, f"UNKNOWN({self.next_state})")


def scan_states_are_exclusive(max_recursion_steps: Any, min_activations: Any) -> bool:
    """``max_recursion_steps`` and ``min_activations`` must not both be set.

    ``if world_info_max_recursion_steps is non-zero min activations are disabled,
    and vice versa`` (world-info.js:4767).  The engine enforces this in the UI by
    zeroing the other control (:6241-6244 and :6306-6309); on the scan path it is
    only ever *read* as a fact.  This helper lets the caller decide between
    logging and raising.
    """
    return not (int(max_recursion_steps or 0) != 0 and int(min_activations or 0) != 0)


def scan_delay_levels(entries: Iterable[Any]) -> list[Any]:
    """Build ``availableRecursionDelayLevels`` (world-info.js:4754-4757).

    Returns the deduplicated, ascending level list *without* consuming the first
    element; :class:`ScanStateMachine` shifts it at construction to reproduce
    ":4759 -- Already preset with the first level".

    ``entry.delayUntilRecursion`` reaches this through ``_field``-style lookup on
    mappings or objects.  ``True`` is normalised to ``1`` (:4756) and falsy
    values are dropped by the engine's ``.filter(entry => entry.delayUntilRecursion)``.
    """
    levels: list[Any] = []
    for entry in entries:
        raw = _read_delay_until_recursion(entry)
        if not raw:
            continue
        level = 1 if raw is True else raw
        if level not in levels:
            levels.append(level)
    try:
        levels.sort()
    except TypeError:
        # Mixed str/int levels exist in the wild; the engine's sort would throw
        # and abort the scan.  Keep the first-seen order instead of crashing.
        logger.debug("[WI] Unsortable delayUntilRecursion levels: %s", levels)
    return levels


def _read_delay_until_recursion(entry: Any) -> Any:
    """``entry.delayUntilRecursion`` (ST camelCase) or ``delay_until_recursion``."""
    if entry is None:
        return None
    if isinstance(entry, Mapping):
        for name in ("delayUntilRecursion", "delay_until_recursion"):
            if name in entry:
                return entry[name]
        return None
    for name in ("delayUntilRecursion", "delay_until_recursion"):
        if hasattr(entry, name):
            return getattr(entry, name)
    return None


class ScanStateMachine:
    """The ``scanState`` / ``availableRecursionDelayLevels`` bookkeeping.

    Construction consumes the first delay level exactly like the engine::

        available = scan_delay_levels(sorted_entries)   # :4754
        machine = ScanStateMachine(available)           # :4754-4762

    ``state`` starts at ``INITIAL`` (:4729) and ``current_recursion_delay_level``
    starts at the smallest level, or ``0`` when there is none (:4759).
    """

    def __init__(self, delay_levels: Sequence[Any] | None = None) -> None:
        # ``[...].shift() ?? 0`` -- copied, because the machine shifts it.
        self._delay_levels: list[Any] = list(delay_levels or ())
        self._current_delay_level: Any = self._delay_levels.pop(0) if self._delay_levels else 0
        self._state: int = SCAN_STATE_INITIAL
        self._count: int = 0
        self._override: int | None = None

    # -- read-only state -------------------------------------------------
    @property
    def state(self) -> int:
        """Current ``scanState`` (world-info.js:4729)."""
        return self._state

    @property
    def state_name(self) -> str:
        return SCAN_STATE_NAMES.get(self._state, f"UNKNOWN({self._state})")

    @property
    def count(self) -> int:
        """Passes completed so far (``count``, :4774)."""
        return self._count

    @property
    def current_recursion_delay_level(self) -> Any:
        """``currentRecursionDelayLevel`` (:4759)."""
        return self._current_delay_level

    @property
    def available_recursion_delay_levels(self) -> tuple[Any, ...]:
        """The *remaining* levels as a tuple; :meth:`step` shifts one per use."""
        return tuple(self._delay_levels)

    @property
    def remaining_delay_levels(self) -> list[Any]:
        """Mutable view of the queue, for the ``WORLD_INFO_SCAN_DONE`` payload
        (:5165-5168) which passes the live array to listeners.
        """
        return self._delay_levels

    @property
    def running(self) -> bool:
        """``while (scanState)`` (world-info.js:4766)."""
        return self._state != SCAN_STATE_NONE

    @property
    def pending_override(self) -> int | None:
        """The unapplied :meth:`override_next` value, if any."""
        return self._override

    # -- the loop hook ---------------------------------------------------
    def override_next(self, state: int | None) -> None:
        """Model ``eventSource`` rewriting ``args.state.next`` (:5179-5181).

        Pass ``None`` to clear.  A listener that sets ``NONE`` stops the scan, so
        :meth:`step` applies the override *after* its own verdict and re-derives
        :attr:`continues` from it.
        """
        self._override = state

    def step(self, turn: ScanTurn | None = None) -> ScanStep:
        """Judge one pass and advance the machine (:4768-5147).

        Call once per loop iteration, with the outcome of the pass that just ran.
        The machine increments its own pass counter for the pass being judged, so
        a caller that breaks on :attr:`ScanStep.exhausted_by_max_steps` before
        running another pass gets the engine's exact pass budget.
        """
        turn = turn or ScanTurn()
        self._count += 1

        max_steps = int(turn.max_recursion_steps or 0)
        exhausted = bool(max_steps) and max_steps <= self._count

        next_state = SCAN_STATE_NONE
        advance_scan = False

        # (2) normal recursion (:5097-5100)
        if (
            turn.recursive
            and not turn.token_budget_overflowed
            and turn.successful_new_for_recursion
        ):
            next_state = SCAN_STATE_RECURSION

        # (3) a MIN_ACTIVATIONS pass with a recursion buffer owes one recursion
        #     pass before the depth moves again (:5104-5107)
        if (
            turn.recursive
            and not turn.token_budget_overflowed
            and self._state == SCAN_STATE_MIN_ACTIVATIONS
            and turn.has_recurse
        ):
            next_state = SCAN_STATE_RECURSION

        # (4) min activations not satisfied (:5110-5126)
        min_activations = int(turn.min_activations or 0)
        not_satisfied = min_activations > 0 and turn.activated_total < min_activations
        if next_state == SCAN_STATE_NONE and not turn.token_budget_overflowed and not_satisfied:
            over_max = (
                turn.min_activations_depth_max > 0
                and turn.buffer_depth > turn.min_activations_depth_max
            ) or (turn.buffer_depth > turn.chat_length)
            if not over_max:
                next_state = SCAN_STATE_MIN_ACTIVATIONS
                advance_scan = True  # buffer.advanceScan() (:5122)
            else:
                logger.debug(
                    "[WI] Min activations not reached (%s/%s), but reached end of depth. Stopping",
                    turn.activated_total,
                    min_activations,
                )

        # (5) left-over delay levels restart a recursion pass (:5129-5133)
        if next_state == SCAN_STATE_NONE and self._delay_levels:
            next_state = SCAN_STATE_RECURSION
            self._current_delay_level = self._delay_levels.pop(0)

        # event interposition (:5179-5181)
        if self._override is not None:
            next_state = self._override
            self._override = None

        self._state = next_state
        return ScanStep(
            next_state=next_state,
            advance_scan=advance_scan,
            current_recursion_delay_level=self._current_delay_level,
            remaining_delay_levels=tuple(self._delay_levels),
            exhausted_by_max_steps=exhausted,
            count=self._count,
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"ScanStateMachine(state={self.state_name}, count={self._count}, "
            f"delay_level={self._current_recursion_delay_level!r}, "
            f"pending_levels={self._delay_levels!r})"
        )
