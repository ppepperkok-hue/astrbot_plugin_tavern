# Ported from SillyTavern - public/scripts/world-info.js (class WorldInfoTimedEffects, lines 479-794)
# Copyright (C) 2024 SillyTavern contributors
# Licensed under the GNU Affero General Public License v3.0.
# This file is a modified Python translation; modified on 2026-10-07.
# Upstream: https://github.com/SillyTavern/SillyTavern (commit 06bde939)
"""Structural mirror of SillyTavern's ``WorldInfoTimedEffects``.

One instance is one World Info evaluation (``world-info.js:479``).  It owns the
three timed-effect buffers the scan asks about -- ``sticky``, ``cooldown`` and
``delay`` -- and the persisted per-chat effect table that used to live in
``chat_metadata.timedWorldInfo``.

Facts that look like accidents but are load-bearing (do not "unify" them):

* The **clock is the chat length**, not a turn counter.  ``#getEntryTimedEffect``
  writes ``start = this.#chat.length`` and ``end = start + entry[type]``
  (:607-608), and the "is it over" test is ``chat.length >= value.end`` (:648).
* The "chat did not advance" purge is ``chat.length <= value.start &&
  !value.protected`` (:626) -- note ``<=``, so an effect written *this* pass is
  already a candidate on the next one, and ``protected`` is the only escape.
* A ``sticky`` that ends **immediately opens a cooldown** for the same entry if
  it has one, and pushes that entry straight into this pass's cooldown buffer
  (:518-529).  That is why a sticky entry cannot re-fire the moment it lapses.
* ``isEffectActive`` compares the entry **hash**, never the ``world.uid`` key
  (:782).  Same uid with different content is a different entry.
* The effect key is ``${world}.${uid}`` (:594) while the lookup is by hash
  (:624) -- two different identities living in one table, on purpose.
* ``delay`` is **not** part of that table.  It is recomputed from scratch every
  pass: ``chat.length < entry.delay`` pushes the entry into the delay buffer
  (:666-677).  ``checkTimedEffects`` therefore **always** runs the delay half,
  even on a dry run (:682-688).
* ``chat.length <= value.start`` also applies to entries that vanished from the
  current book set, so an orphaned effect survives exactly until the chat grows.

Deliberate divergences from the JavaScript (all forced by the host language or
by the fact that this module is a dependency-free leaf):

* There is no global ``chat_metadata``.  The table is an instance attribute and
  is persisted with :meth:`WorldInfoTimedEffects.to_metadata` /
  :meth:`WorldInfoTimedEffects.from_metadata`; the module imports nothing but
  the standard library, so the AstrBot storage layer stays out of it.
* ``console.log`` becomes ``logging.debug`` on the module logger.
* ``#getEntryHash`` reads the entry's ``hash`` field (as upstream does) and this
  module never invents one; :func:`entry_hash` is offered for callers that need
  the same read.
* ``this.#buffer`` is exposed read-only as :attr:`WorldInfoTimedEffects.buffers`.
* The entry lookup by hash normalises both sides with ``str()``, because the
  engine's ``String(...) === String(...)`` comparison (:624) is the only reason
  a JSON round trip through metadata keeps working.
* ``check_timed_effects`` does **not** re-read the metadata after a sticky ends;
  the freshly opened cooldown therefore only takes effect on the next pass,
  which is what the engine does too (the callback only writes, :525-528).

Integration contract (the one thing a caller must not get wrong)
----------------------------------------------------------------

``chat_length`` must be the real message count of the chat **at the moment of the
pass**, and it must have grown before the next pass runs.  ``:626`` treats
``chat.length <= start`` as "the chat did not advance", so a harness that replays
the same chat twice without growth silently loses every effect it just wrote --
that is up to date with the engine, and it is why a fixture walks the chat
forward one message at a time.  Both halves of the lifecycle belong to the
caller: :meth:`check_timed_effects` at the start of a scan (:4747) and
:meth:`set_timed_effects` at the end (:5274).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple

from tavern.log import logger

__all__ = [
    "EFFECT_TYPES",
    "LazyMetadata",
    "TimedEffect",
    "WorldInfoTimedEffects",
    "entry_hash",
    "entry_key",
]

#: ``TimedEffectType`` (world-info.js:502, 767) -- insertion order preserved.
EFFECT_TYPES: tuple[str, ...] = ("sticky", "cooldown", "delay")

#: The three effects the engine knows; ``isValidEffectType`` compares after
#: ``trim().toLowerCase()`` (world-info.js:768).
TimedEffectType = Literal["sticky", "cooldown", "delay"]


def _field(entry: Any, *names: str) -> Any:
    """Read the first present field, ST camelCase name first, then snake_case.

    Missing or ``None`` means "unset" so the caller can apply JS truthiness the
    way the engine does.  Entries may be mappings *or* plain objects, which is
    what lets this module sit under either the oracle's dict entries or
    ``worldbook.WorldInfoEntry``.
    """
    if entry is None:
        return None
    if isinstance(entry, Mapping):
        for name in names:
            if name in entry:
                return entry[name]
        return None
    for name in names:
        if hasattr(entry, name):
            return getattr(entry, name)
    return None


def entry_key(entry: Any) -> str:
    """JS ``#getEntryKey`` (world-info.js:593-595): ``${entry.world}.${entry.uid}``.

    The JS template literal renders ``undefined`` for a missing part; ``str()``
    of ``None`` is the closest Python spelling.
    """
    return f"{_field(entry, 'world')}.{_field(entry, 'uid')}"


def entry_hash(entry: Any) -> Any:
    """JS ``#getEntryHash`` (world-info.js:584-586): ``return entry.hash``.

    SillyTavern fills ``hash`` in ``getSortedEntries`` (``world-info.js:4632``);
    this module never computes one.
    """
    return _field(entry, "hash")


def _truthy(value: Any) -> bool:
    """JS truthiness for the values that reach these fields."""
    if value is None or value is False:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0:
        return False
    return bool(value)


def _number(value: Any, *, default: int = 0) -> int:
    """JS ``Number(value)`` where the engine applies it to an int."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class TimedEffect:
    """``WITimedEffect`` (world-info.js:604-611).

    ``start`` / ``end`` are **chat lengths**: ``start`` is the length at which the
    effect was written and ``end`` is ``start + duration``.  ``protected`` means
    "do not purge me just because the chat did not advance"
    (world-info.js:626).
    """

    hash: Any = None
    start: int = 0
    end: int = 0
    protected: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, hash_hint: Any = None) -> TimedEffect:
        """Build from persisted JSON, applying JS ``Number(...)`` coercions.

        ``start`` and ``end`` are read with ``Number(...)`` in the engine
        (world-info.js:626, 634, 648), so the numeric strings a JSON round trip
        can produce are accepted here too.
        """
        raw_hash = value.get("hash")
        if raw_hash is None:
            raw_hash = hash_hint
        return cls(
            hash=raw_hash,
            start=_number(value.get("start"), default=0),
            end=_number(value.get("end"), default=0),
            protected=bool(value.get("protected", False)),
        )

    def to_mapping(self) -> dict[str, Any]:
        """The JSON shape the engine writes into ``chat_metadata`` (:605-610)."""
        return {
            "hash": self.hash,
            "start": self.start,
            "end": self.end,
            "protected": self.protected,
        }


def _to_effect(value: Any, *, hash_hint: Any = None) -> TimedEffect | None:
    """Coerce persisted metadata into a :class:`TimedEffect`, or ``None``.

    ``#ensureChatMetadata`` (world-info.js:559-577) deletes every value that is
    not an object, so a malformed payload is dropped, never repaired.
    """
    if isinstance(value, TimedEffect):
        return value
    if not isinstance(value, Mapping):
        return None
    return TimedEffect.from_mapping(value, hash_hint=hash_hint)


class LazyMetadata(NamedTuple):
    """Outcome of :meth:`WorldInfoTimedEffects.ensure_chat_metadata`."""

    #: Effect types whose table did not exist and was created as ``{}``.
    created: tuple[str, ...]
    #: ``(type, key)`` pairs deleted because the value was not an object.
    removed: tuple[tuple[str, str], ...]


class WorldInfoTimedEffects:
    """Mirror of ``WorldInfoTimedEffects`` (world-info.js:479-793).

    Constructor shape::

        WorldInfoTimedEffects(chat_length, entries=(), is_dry_run=False, metadata=None)

    ``chat_length`` is ``chat.length`` -- the number of messages -- and *is the
    clock*, exactly as in the engine.  ``entries`` is the full set of entries the
    current scan considers (``sortedEntries``, :490).  ``metadata`` is an optional
    already-persisted three-layer table; it is copied in, so the instance never
    mutates the caller's object (and vice versa).
    """

    def __init__(
        self,
        chat_length: int,
        entries: Sequence[Any] = (),
        is_dry_run: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._chat_length: int = int(chat_length)
        self._entries: list[Any] = list(entries)
        self._is_dry_run: bool = bool(is_dry_run)
        self._buffer: dict[str, list[Any]] = {name: [] for name in EFFECT_TYPES}
        self._metadata: dict[str, dict[str, TimedEffect]] = {name: {} for name in EFFECT_TYPES}
        self._hash_to_entry: dict[str, Any] = self._index_entries()
        if metadata is not None:
            self._adopt_metadata(metadata)
        self.ensure_chat_metadata()

    # ------------------------------------------------------------------
    # construction helpers
    # ------------------------------------------------------------------
    def _index_entries(self) -> dict[str, Any]:
        """Hash -> entry, mirroring the JS ``find`` by ``String(hash)`` (:624).

        JavaScript keeps the **first** match; later duplicates lose.
        """
        index: dict[str, Any] = {}
        for entry in self._entries:
            index.setdefault(str(entry_hash(entry)), entry)
        return index

    def _adopt_metadata(self, metadata: Mapping[str, Any]) -> None:
        """Copy a persisted table into ``self`` (unknown types are ignored).

        ``_merge_layers`` is fed a snapshot of the raw values so the caller's
        object is never mutated -- only :meth:`to_metadata` and this instance's
        own table are.
        """
        layers: dict[str, dict[str, Any]] = {}
        for source in (metadata, getattr(metadata, "timed_world_info", None)):
            if source is None or not isinstance(source, Mapping):
                continue
            for name in EFFECT_TYPES:
                layer = source.get(name)
                if isinstance(layer, Mapping):
                    layers.setdefault(name, {}).update(dict(layer))
        self._merge_layers(layers)

    def _merge_layers(self, layers: Mapping[str, Mapping[str, Any]]) -> None:
        """Normalise raw three-layer data into ``{type: {key: TimedEffect}}``."""
        for name, layer in layers.items():
            if name not in self._metadata:
                continue
            table = self._metadata[name]
            for key, value in layer.items():
                text = str(key)
                effect = _to_effect(value, hash_hint=self._hash_for_key(text))
                if effect is None:
                    logger.debug("[WI] Dropping invalid %s entry %s from timed metadata", name, key)
                    table.pop(text, None)
                    continue
                table[text] = effect

    def _hash_for_key(self, key: str) -> Any:
        """Lazy migration: recover a missing ``hash`` from the live entries.

        ``#getEntryKey`` is ``${world}.${uid}`` (:594), which is enough to find
        the entry again, and ``#checkTimedEffectOfType`` always stores the
        entry's own hash (:606) -- so filling it in from the entry set restores
        exactly the value the engine would have written.
        """
        for entry in self._entries:
            if entry_key(entry) == key:
                return entry_hash(entry)
        return None

    # ------------------------------------------------------------------
    # WorldInfoTimedEffects API
    # ------------------------------------------------------------------
    @property
    def chat_length(self) -> int:
        """The clock: ``this.#chat.length`` (world-info.js:484).

        Settable because a harness routinely replays the same chat at a growing
        length (``this.#chat`` is just the array whose length is read).
        """
        return self._chat_length

    @chat_length.setter
    def chat_length(self, value: int) -> None:
        self._chat_length = int(value)

    @property
    def is_dry_run(self) -> bool:
        """``this.#isDryRun`` (world-info.js:496)."""
        return self._is_dry_run

    @property
    def entries(self) -> tuple[Any, ...]:
        """The entry set this evaluation sees (``this.#entries``, :490)."""
        return tuple(self._entries)

    @property
    def metadata(self) -> dict[str, dict[str, TimedEffect]]:
        """The live effect table (``chat_metadata.timedWorldInfo``, :560-576)."""
        return self._metadata

    @property
    def buffers(self) -> dict[str, tuple[Any, ...]]:
        """The three per-pass buffers, read-only (``this.#buffer``, :502-506)."""
        return {name: tuple(entries) for name, entries in self._buffer.items()}

    def ensure_chat_metadata(self) -> LazyMetadata:
        """JS ``#ensureChatMetadata`` (world-info.js:559-577).

        Creates a missing ``{ }`` for ``sticky`` and ``cooldown`` (but **not**
        ``delay``, :564) and deletes every entry whose value is not an object.
        Returns what it did instead of touching a global.
        """
        created: list[str] = []
        removed: list[tuple[str, str]] = []
        for effect_type in ("sticky", "cooldown"):
            layer = self._metadata.get(effect_type)
            if layer is None:
                self._metadata[effect_type] = {}
                created.append(effect_type)
                continue
            for key in list(layer):
                if not isinstance(layer[key], TimedEffect):
                    logger.debug(
                        "[WI] Removing invalid %s entry %s from timed metadata",
                        effect_type,
                        key,
                    )
                    del layer[key]
                    removed.append((effect_type, key))
        if "delay" not in self._metadata:
            self._metadata["delay"] = {}
        return LazyMetadata(created=tuple(created), removed=tuple(removed))

    # -- identity -------------------------------------------------------
    def _get_entry_hash(self, entry: Any) -> Any:
        """JS ``#getEntryHash`` (world-info.js:584-586)."""
        return entry_hash(entry)

    def _get_entry_key(self, entry: Any) -> str:
        """JS ``#getEntryKey`` (world-info.js:593-595)."""
        return entry_key(entry)

    def _get_entry_timed_effect(
        self,
        effect_type: str,
        entry: Any,
        is_protected: bool = False,
    ) -> TimedEffect:
        """JS ``#getEntryTimedEffect`` (world-info.js:604-611).

        ``end = chat.length + Number(entry[type])`` -- a falsy duration is *not*
        rejected here, it simply produces ``end == start``.  ``Number()`` is the
        engine's own coercion, so a numeric string duration works too, and
        ``Number(null) === 0``.
        """
        duration = _field(entry, effect_type)
        return TimedEffect.from_mapping(
            {
                "hash": self._get_entry_hash(entry),
                "start": self._chat_length,
                "end": self._chat_length + (0 if duration is None else _number(duration)),
                "protected": bool(is_protected),
            }
        )

    # -- end-of-effect callbacks ---------------------------------------
    def _on_sticky_ended(self, entry: Any) -> None:
        """JS ``#onEnded.sticky`` (world-info.js:518-529).

        A lapsed sticky entry with a cooldown is put on cooldown **immediately**,
        and the same entry is pushed into the cooldown buffer so the current pass
        already treats it as cooling down.
        """
        if not _truthy(_field(entry, "cooldown")):
            return

        key = self._get_entry_key(entry)
        effect = self._get_entry_timed_effect("cooldown", entry, True)
        self._metadata["cooldown"][key] = effect
        logger.debug(
            "[WI] Adding cooldown entry %s on ended sticky: start=%s, end=%s, protected=%s",
            key,
            effect.start,
            effect.end,
            effect.protected,
        )
        self._buffer["cooldown"].append(entry)

    def _on_cooldown_ended(self, entry: Any) -> None:
        """JS ``#onEnded.cooldown`` (world-info.js:536-538): a no-op."""
        logger.debug("[WI] Cooldown ended for entry %s", _field(entry, "uid"))

    def _on_delay_ended(self, entry: Any) -> None:
        """JS ``#onEnded.delay`` (world-info.js:540): an empty function."""
        return None

    # -- per-type scan --------------------------------------------------
    def _check_timed_effect_of_type(
        self,
        effect_type: str,
        buffer: list[Any],
        on_ended: Any = None,
    ) -> None:
        """JS ``#checkTimedEffectOfType`` (world-info.js:619-660), branch for branch.

        Order matters and is pinned by tests:

        1. ``chat.length <= value.start && !value.protected`` -> delete, skip
           (:626-630).  Runs *before* the entry is even looked up.
        2. entry missing -> delete only when ``chat.length >= value.end``
           (:633-639).
        3. entry no longer configured for this effect -> delete (:642-646).
        4. ``chat.length >= value.end`` -> delete **and** fire ``on_ended``
           (:648-655).
        5. otherwise -> push into the buffer (:657).
        """
        for key, effect in list(self._metadata.get(effect_type, {}).items()):
            logger.debug("[WI] Processing %s entry %s %s", effect_type, key, effect)
            entry = self._find_entry_by_hash(effect.hash)

            if self._chat_length <= effect.start and not effect.protected:
                logger.debug(
                    "[WI] Removing %s entry %s from timed metadata: chat not advanced",
                    effect_type,
                    key,
                )
                del self._metadata[effect_type][key]
                continue

            # Missing entries (they could be from another character's lorebook)
            if entry is None:
                if self._chat_length >= effect.end:
                    logger.debug(
                        "[WI] Removing %s entry from timed metadata: "
                        "entry not found and interval passed",
                        effect_type,
                    )
                    del self._metadata[effect_type][key]
                continue

            # Ignore invalid entries (not configured for timed effects)
            if not _truthy(_field(entry, effect_type)):
                logger.debug(
                    "[WI] Removing %s entry from timed metadata: entry not %s",
                    effect_type,
                    effect_type,
                )
                del self._metadata[effect_type][key]
                continue

            if self._chat_length >= effect.end:
                logger.debug(
                    "[WI] Removing %s entry from timed metadata: %s interval passed",
                    effect_type,
                    effect_type,
                )
                del self._metadata[effect_type][key]
                if callable(on_ended):
                    on_ended(entry)
                continue

            buffer.append(entry)
            logger.debug('[WI] Timed effect "%s" applied to entry', effect_type)

    def _find_entry_by_hash(self, value: Any) -> Any:
        """``this.#entries.find(x => String(getEntryHash(x)) === String(value.hash))``
        (world-info.js:624), first match wins.
        """
        return self._hash_to_entry.get(str(value))

    def _check_delay_effect(self, buffer: list[Any]) -> None:
        """JS ``#checkDelayEffect`` (world-info.js:666-677).

        ``delay`` is **not** persisted: every pass recomputes it from the live
        entry, and ``chat.length < entry.delay`` means "not yet".  A falsy
        ``delay`` skips the entry entirely (:668-670).
        """
        for entry in self._entries:
            delay = _field(entry, "delay")
            if not _truthy(delay):
                continue
            # ``chat.length < delay`` with the engine's ``Number()`` coercion (:672).
            if self._chat_length < _number(delay):
                buffer.append(entry)
                logger.debug('[WI] Timed effect "delay" applied to entry')

    def check_timed_effects(self) -> None:
        """JS ``checkTimedEffects`` (world-info.js:682-688).

        On a dry run only the ``delay`` half runs; ``sticky`` and ``cooldown``
        are neither purged nor populated.
        """
        if not self._is_dry_run:
            self._check_timed_effect_of_type(
                "sticky", self._buffer["sticky"], self._on_sticky_ended
            )
            self._check_timed_effect_of_type(
                "cooldown", self._buffer["cooldown"], self._on_cooldown_ended
            )
        self._check_delay_effect(self._buffer["delay"])

    # -- metadata access ------------------------------------------------
    def get_effect_metadata(self, effect_type: str, entry: Any) -> TimedEffect | None:
        """JS ``getEffectMetadata`` (world-info.js:696-703).

        Returns ``None`` (JS ``null``) for an unknown type and for a key that was
        never registered.  The lookup is by ``${world}.${uid}``, *not* by hash.
        """
        name = self._normalise_type(effect_type)
        if name is None:
            return None
        # JS reads `timedWorldInfo[type][key]` unguarded; a missing layer is an
        # empty table here so the same miss yields null instead of a crash.
        return self._metadata.get(name, {}).get(self._get_entry_key(entry))

    def _set_timed_effect_of_type(self, effect_type: str, entry: Any) -> None:
        """JS ``#setTimedEffectOfType`` (world-info.js:710-724).

        First writer wins: an effect already on file for this key is left alone,
        so ``start`` is the *first* activation and never refreshes.
        """
        if not _truthy(_field(entry, effect_type)):
            return

        key = self._get_entry_key(entry)
        table = self._metadata.setdefault(effect_type, {})
        if not table.get(key):
            effect = self._get_entry_timed_effect(effect_type, entry, False)
            table[key] = effect
            logger.debug(
                "[WI] Adding %s entry %s: start=%s, end=%s, protected=%s",
                effect_type,
                key,
                effect.start,
                effect.end,
                effect.protected,
            )

    def set_timed_effects(self, activated_entries: Iterable[Any]) -> None:
        """JS ``setTimedEffects`` (world-info.js:730-736).

        A dry run writes nothing at all.  ``sticky`` is registered before
        ``cooldown``, same as the engine (:733-734).
        """
        if self._is_dry_run:
            return
        for entry in activated_entries:
            self._set_timed_effect_of_type("sticky", entry)
            self._set_timed_effect_of_type("cooldown", entry)

    def set_timed_effect(self, effect_type: str, entry: Any, new_state: Any) -> None:
        """JS ``setTimedEffect`` (world-info.js:744-760) -- force set / clear.

        ``delay`` is the one type a dry run may still write (:748-750).
        """
        name = self._normalise_type(effect_type)
        if name is None:
            return
        if self._is_dry_run and name != "delay":
            return

        key = self._get_entry_key(entry)
        self._metadata.setdefault(name, {}).pop(key, None)

        if _truthy(new_state):
            effect = self._get_entry_timed_effect(name, entry, False)
            self._metadata[name][key] = effect
            logger.debug(
                "[WI] Adding %s entry %s: start=%s, end=%s, protected=%s",
                name,
                key,
                effect.start,
                effect.end,
                effect.protected,
            )

    def is_valid_effect_type(self, effect_type: Any) -> bool:
        """JS ``isValidEffectType`` (world-info.js:767-769).

        Non-strings are rejected and surrounding whitespace plus case are ignored
        (:768).  :meth:`_normalise_type` folds that whole expression.
        """
        return self._normalise_type(effect_type) is not None

    @staticmethod
    def _normalise_type(effect_type: Any) -> str | None:
        """``typeof type === 'string' && ['sticky','cooldown','delay']
        .includes(type.trim().toLowerCase())`` (world-info.js:768).
        """
        if not isinstance(effect_type, str):
            return None
        name = effect_type.strip().lower()
        return name if name in EFFECT_TYPES else None

    def is_effect_active(self, effect_type: str, entry: Any) -> bool:
        """JS ``isEffectActive`` (world-info.js:777-783).

        Compares the entry **hash** against the hashes currently sitting in the
        buffer for that type -- the key is never consulted.  The JS ``?.``/``??``
        pair means an unknown type is simply ``false``.
        """
        name = self._normalise_type(effect_type)
        if name is None:
            return False
        buffer = self._buffer.get(name)
        if not buffer:
            return False
        wanted = str(self._get_entry_hash(entry))
        return any(str(self._get_entry_hash(item)) == wanted for item in buffer)

    def sort_by_sticky(self, entries: Sequence[Any], order_index: Mapping[Any, int]) -> list[Any]:
        """The sticky-first sort from ``checkWorldInfo`` (world-info.js:4995-5003).

        Sticky entries come first, then the pre-existing ``sortedEntries`` order
        (``sortedEntriesIndex.get(a) ?? -1``).  It lives here because this is the
        only object that can answer the sticky question mid-pass.
        """
        return sorted(
            entries,
            key=lambda entry: (
                0 if self.is_effect_active("sticky", entry) else 1,
                order_index.get(entry, -1),
            ),
        )

    def clean_up(self) -> None:
        """JS ``cleanUp`` (world-info.js:789-792): empty all three buffers."""
        for buffer in self._buffer.values():
            del buffer[:]

    # -- persistence ----------------------------------------------------
    def to_metadata(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Serialise the effect table in the JS three-layer shape.

        ``{type: {key: {hash, start, end, protected}}}`` -- the exact structure
        ``chat_metadata.timedWorldInfo`` carries (:560-576, :720, :757).  Only
        ``sticky`` and ``cooldown`` can have content, because ``delay`` is
        recomputed per pass and never written to the table.
        """
        return {
            name: {key: effect.to_mapping() for key, effect in layer.items()}
            for name, layer in self._metadata.items()
        }

    @classmethod
    def from_metadata(
        cls,
        data: Mapping[str, Any] | None,
        chat_length: int,
        entries: Sequence[Any] = (),
        is_dry_run: bool = False,
    ) -> WorldInfoTimedEffects:
        """Restore an instance from :meth:`to_metadata` output.

        ``data`` may be ``None`` (a fresh chat) or a partly malformed payload:
        anything that is not an object is dropped, exactly like
        ``#ensureChatMetadata`` does at load time.  When a stored effect lost its
        ``hash`` (an export that stripped it), the live ``entries`` are searched
        by ``${world}.${uid}`` to recover it.
        """
        instance = cls(chat_length, entries, is_dry_run)
        if data:
            instance._adopt_metadata(data)
            instance.ensure_chat_metadata()
        return instance

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"WorldInfoTimedEffects(chat_length={self._chat_length}, "
            f"entries={len(self._entries)}, dry_run={self._is_dry_run}, "
            f"sticky={len(self._buffer['sticky'])}, "
            f"cooldown={len(self._buffer['cooldown'])}, "
            f"delay={len(self._buffer['delay'])})"
        )
