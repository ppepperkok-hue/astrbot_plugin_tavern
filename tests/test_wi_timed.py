# Ported from SillyTavern - public/scripts/world-info.js (class WorldInfoTimedEffects
#  @479-793 and scan_state @43-60 with its transitions @4766-5147)
# Copyright (C) 2024 SillyTavern contributors
# Licensed under the GNU Affero General Public License v3.0.
# This file is a modified Python translation; modified on 2026-10-07.
# Upstream: https://github.com/SillyTavern/SillyTavern (commit 06bde939)
"""Offline tests for the mirrored timed effects and the scan state machine.

Pure Python: no Node, no SillyTavern snapshot, no file I/O and no AstrBot.  The
point of every case is to pin the behaviours that look like accidents and would
be "cleaned up" by a later refactor -- the chat-length clock, the ``<=`` in the
"chat did not advance" purge, the sticky->cooldown handover, hash-based identity,
and the order in which the scan loop decides to run another pass.

Pass order matters and is mirrored here: the engine calls ``checkTimedEffects()``
at the very start of a scan (:4747) and ``setTimedEffects()`` at the very end
(:5274).  A fresh effect is therefore *never* checked in the same pass that wrote
it -- it is checked by the next scan, at the next chat length.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tavern.st.wi_scan_state import (  # noqa: E402
    SCAN_STATE_INITIAL,
    SCAN_STATE_MIN_ACTIVATIONS,
    SCAN_STATE_NONE,
    SCAN_STATE_RECURSION,
    ScanStateMachine,
    ScanTurn,
    scan_delay_levels,
    scan_state,
    scan_states_are_exclusive,
)
from tavern.st.wi_timed import (  # noqa: E402
    EFFECT_TYPES,
    TimedEffect,
    WorldInfoTimedEffects,
    entry_hash,
    entry_key,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def entry(**overrides):
    """A SillyTavern ``WIScanEntry`` using the engine's own field names."""
    base = {
        "uid": 0,
        "world": "Timed",
        "content": "content",
        "hash": 1,
        "sticky": None,
        "cooldown": None,
        "delay": None,
    }
    base.update(overrides)
    return base


class EntryObject:
    """The same entry as an attribute object, to prove both shapes read alike."""

    def __init__(self, **fields):
        for name, value in fields.items():
            setattr(self, name, value)


def make(chat_length, entries, is_dry_run=False, metadata=None):
    """A manager for one scan pass, optionally restored from stored metadata."""
    return WorldInfoTimedEffects(chat_length, entries, is_dry_run, metadata)


def sticky_pass(chat_length, an_entry, saved=None):
    """One scan: check first, then register ``an_entry`` (:4747 vs :5274)."""
    effects = make(chat_length, [an_entry], metadata=saved)
    effects.check_timed_effects()
    effects.set_timed_effects([an_entry])
    return effects


# ---------------------------------------------------------------------------
# 1. sticky ends, "chat did not advance" purge, protected exception
# ---------------------------------------------------------------------------


def test_sticky_ends_at_chat_length_start_plus_duration():
    """``end = chat.length + entry.sticky`` (:608) and ``>= end`` removes (:648)."""
    e = entry(sticky=3)

    first = sticky_pass(2, e)
    assert first.metadata["sticky"]["Timed.0"] == TimedEffect(
        hash=1, start=2, end=5, protected=False
    )
    saved = first.to_metadata()

    # 3, 4 -> still inside the window, the buffer holds the entry
    for length in (3, 4):
        effects = make(length, [e], metadata=saved)
        effects.check_timed_effects()
        assert effects.is_effect_active("sticky", e) is True
        assert "Timed.0" in effects.metadata["sticky"]
        saved = effects.to_metadata()

    # 5 -> chat.length >= end, effect ends (and no cooldown is opened)
    effects = make(5, [e], metadata=saved)
    effects.check_timed_effects()
    assert effects.metadata["sticky"] == {}
    assert effects.is_effect_active("sticky", e) is False


def test_chat_not_advanced_purges_unprotected_effect():
    """``chat.length <= value.start && !value.protected`` (:626) -- inclusive."""
    e = entry(sticky=5)
    saved = sticky_pass(7, e).to_metadata()

    # Same chat length as when the effect was written: purge, even though the
    # interval (end = 12) is far from over.
    effects = make(7, [e], metadata=saved)
    effects.check_timed_effects()
    assert effects.metadata["sticky"] == {}
    assert effects.is_effect_active("sticky", e) is False

    # One message later the effect survives and would have run to its end.
    e2 = entry(sticky=5)
    effects = make(8, [e2], metadata=saved)
    effects.check_timed_effects()
    assert effects.metadata["sticky"]["Timed.0"].end == 12
    assert effects.is_effect_active("sticky", e2) is True


def test_protected_effect_survives_a_stalled_chat():
    """``protected`` is the only escape from the :626 purge."""
    e = entry(uid=4, sticky=None, cooldown=5)
    protected = TimedEffect(hash=1, start=7, end=12, protected=True)
    effects = make(7, [e], metadata={"cooldown": {"Timed.4": protected}})

    effects.check_timed_effects()
    assert effects.metadata["cooldown"]["Timed.4"] is protected
    assert effects.is_effect_active("cooldown", e) is True


# ---------------------------------------------------------------------------
# 2. a sticky that ends opens a cooldown immediately
# ---------------------------------------------------------------------------


def test_ending_sticky_immediately_opens_cooldown():
    """``#onEnded.sticky`` (:518-529): write a *protected* cooldown at once."""
    e = entry(sticky=3, cooldown=2)
    saved = sticky_pass(4, e).to_metadata()
    assert saved["sticky"]["Timed.0"] == {
        "hash": 1,
        "start": 4,
        "end": 7,
        "protected": False,
    }
    assert saved["cooldown"]["Timed.0"] == {
        "hash": 1,
        "start": 4,
        "end": 6,
        "protected": False,
    }

    # Length 7 ends the sticky.  The cooldown is rewritten from scratch, starting
    # at the moment the sticky lapsed, and marked protected.
    effects = make(7, [e], metadata=saved)
    effects.check_timed_effects()
    assert effects.metadata["sticky"] == {}
    assert effects.metadata["cooldown"]["Timed.0"] == TimedEffect(1, 7, 9, True)
    # Registered in the cooldown buffer *for this pass* (:528).
    assert effects.is_effect_active("cooldown", e) is True


def test_ending_sticky_without_cooldown_opens_nothing():
    """``if (!entry.cooldown) return;`` (:519-521)."""
    e = entry(sticky=1, cooldown=None)
    saved = sticky_pass(1, e).to_metadata()
    assert saved["cooldown"] == {}

    effects = make(2, [e], metadata=saved)
    effects.check_timed_effects()
    assert effects.metadata["sticky"] == {}
    assert effects.metadata["cooldown"] == {}


def test_zero_cooldown_is_falsy_and_opens_nothing():
    """``cooldown: 0`` is falsy in JS, exactly like ``null``."""
    e = entry(sticky=1, cooldown=0)
    saved = sticky_pass(1, e).to_metadata()

    effects = make(2, [e], metadata=saved)
    effects.check_timed_effects()
    assert effects.metadata["cooldown"] == {}
    assert e["cooldown"] == 0  # the entry itself is not rewritten


# ---------------------------------------------------------------------------
# 3. the cooldown interval
# ---------------------------------------------------------------------------


def test_cooldown_interval_boundaries():
    e = entry(cooldown=2)
    saved = sticky_pass(5, e).to_metadata()
    assert saved["cooldown"]["Timed.0"] == {
        "hash": 1,
        "start": 5,
        "end": 7,
        "protected": False,
    }

    for length in (5, 6):
        effects = make(length, [e], metadata=saved)
        effects.check_timed_effects()
        assert effects.is_effect_active("cooldown", e) is True
        assert "Timed.0" in effects.metadata["cooldown"]

    # ``chat.length >= end`` removes it (:648) -- the boundary is exclusive.
    effects = make(7, [e], metadata=saved)
    effects.check_timed_effects()
    assert effects.metadata["cooldown"] == {}
    assert effects.is_effect_active("cooldown", e) is False


def test_cooldown_start_is_not_refreshed_by_a_second_registration():
    """``#setTimedEffectOfType`` (:718-723): first writer wins."""
    e = entry(cooldown=3)
    effects = make(2, [e])
    effects.set_timed_effects([e])
    effects.set_timed_effects([e])
    assert effects.metadata["cooldown"]["Timed.0"] == TimedEffect(1, 2, 5, False)


# ---------------------------------------------------------------------------
# 4. the delay interval
# ---------------------------------------------------------------------------


def test_delay_is_measured_against_chat_length():
    """``chat.length < entry.delay`` (:672), nothing is persisted for delay."""
    e = entry(delay=3)

    for length in (0, 2):
        effects = make(length, [e])
        effects.check_timed_effects()
        assert effects.is_effect_active("delay", e) is True
        assert effects.buffers["delay"] == (e,)
        assert effects.metadata["delay"] == {}

    # 3 < 3 is false: the entry stops being suppressed at exactly the boundary.
    effects = make(3, [e])
    effects.check_timed_effects()
    assert effects.is_effect_active("delay", e) is False
    assert effects.buffers["delay"] == ()


def test_delay_zero_or_missing_skips_the_entry():
    """``if (!entry.delay) continue;`` (:668-670)."""
    for value in (None, 0):
        e = entry(delay=value)
        effects = make(0, [e])
        effects.check_timed_effects()
        assert effects.is_effect_active("delay", e) is False
        assert effects.buffers["delay"] == ()


# ---------------------------------------------------------------------------
# 5. dry runs only handle delay
# ---------------------------------------------------------------------------


def test_dry_run_registers_nothing():
    """``setTimedEffects`` returns early on a dry run (:731)."""
    e = entry(sticky=2, cooldown=2)
    effects = sticky_pass(1, e)
    assert effects.metadata["sticky"] == {}
    assert effects.metadata["cooldown"] == {}


def test_dry_run_registers_nothing_even_protected():
    e = entry(sticky=2, cooldown=2)
    effects = make(1, [e], is_dry_run=True)
    effects.set_timed_effects([e])
    effects.check_timed_effects()
    assert effects.to_metadata() == {"sticky": {}, "cooldown": {}, "delay": {}}


def test_dry_run_does_not_touch_the_sticky_table():
    """``checkTimedEffects`` skips sticky/cooldown entirely (:683-686)."""
    e = entry(sticky=5)
    stored = TimedEffect(hash=1, start=0, end=99, protected=True)
    effects = make(1, [e], is_dry_run=True, metadata={"sticky": {"Timed.0": stored}})
    effects.check_timed_effects()
    assert effects.metadata["sticky"]["Timed.0"] is stored
    assert effects.is_effect_active("sticky", e) is False


def test_dry_run_still_processes_delay():
    """``#checkDelayEffect`` is called outside the ``!isDryRun`` guard (:687)."""
    e = entry(delay=4)
    effects = make(1, [e], is_dry_run=True)
    effects.check_timed_effects()
    assert effects.is_effect_active("delay", e) is True


def test_dry_run_only_writes_a_delay_effect_with_set_timed_effect():
    """``if (this.#isDryRun && type !== 'delay') return;`` (:748-750)."""
    e = entry(uid=9, sticky=3, cooldown=3, delay=3)
    effects = make(1, [e], is_dry_run=True)

    effects.set_timed_effect("sticky", e, True)
    effects.set_timed_effect("cooldown", e, True)
    assert effects.metadata["sticky"] == {}
    assert effects.metadata["cooldown"] == {}

    effects.set_timed_effect("delay", e, True)
    assert effects.metadata["delay"]["Timed.9"] == TimedEffect(1, 1, 4, False)


def test_set_timed_effect_false_clears_the_key():
    e = entry(cooldown=4)
    effects = make(3, [e])
    effects.set_timed_effects([e])
    assert "Timed.0" in effects.metadata["cooldown"]

    effects.set_timed_effect("cooldown", e, False)
    assert effects.metadata["cooldown"] == {}


def test_effect_type_validation():
    effects = make(0, [])
    for good in ("sticky", "cooldown", "delay", " STICKY ", "Cooldown", "sticky "):
        assert effects.is_valid_effect_type(good) is True
    for bad in ("", None, 1, 0, True, "unknown", "delayy", "sticky delay"):
        assert effects.is_valid_effect_type(bad) is False
    assert effects.get_effect_metadata("nope", entry()) is None
    assert effects.get_effect_metadata("sticky", entry(uid=123)) is None


# ---------------------------------------------------------------------------
# 6. metadata round trip
# ---------------------------------------------------------------------------


def test_to_metadata_has_the_js_three_layer_shape():
    e = entry(sticky=2, cooldown=3)
    effects = make(4, [e])
    effects.set_timed_effects([e])

    assert effects.to_metadata() == {
        "sticky": {
            "Timed.0": {"hash": 1, "start": 4, "end": 6, "protected": False},
        },
        "cooldown": {
            "Timed.0": {"hash": 1, "start": 4, "end": 7, "protected": False},
        },
        "delay": {},
    }


def test_metadata_round_trip_replays_identically():
    e = entry(sticky=3, cooldown=2)

    # A persistent instance is the ground truth; a restored one must match it
    # exactly at every chat length.
    persistent = make(0, [e])
    for length in (1, 2, 3, 4, 5, 6, 7, 8, 9):
        persistent.chat_length = length  # the scan replays at a new chat length
        persistent.check_timed_effects()
        persistent.set_timed_effects([e])

        restored = WorldInfoTimedEffects.from_metadata(persistent.to_metadata(), length, [e])
        restored.check_timed_effects()
        assert restored.to_metadata() == persistent.to_metadata()
        assert restored.buffers == persistent.buffers


def test_from_metadata_null_or_empty_is_a_fresh_table():
    for data in (None, {}):
        effects = WorldInfoTimedEffects.from_metadata(data, 3, [])
        assert effects.to_metadata() == {"sticky": {}, "cooldown": {}, "delay": {}}


def test_metadata_cleanup_drops_invalid_values():
    """``#ensureChatMetadata`` (:564-576) repairs the table, it never crashes."""
    e = entry(uid=1, sticky=2)
    payload = {
        "sticky": {
            "Timed.1": {"hash": 1, "start": 0, "end": 4, "protected": False},
            "Timed.bad": "not an object",
            "Timed.none": None,
        },
        "cooldown": "not an object",
        "junk": {"Timed.1": {"hash": 1}},
    }
    effects = make(0, [e], metadata=payload)

    assert set(effects.metadata) == set(EFFECT_TYPES)
    assert "Timed.bad" not in effects.metadata["sticky"]
    assert "Timed.none" not in effects.metadata["sticky"]
    assert effects.metadata["cooldown"] == {}
    assert "junk" not in effects.metadata

    # The caller's dict is untouched: normalisation happens on a copy.
    assert payload["sticky"]["Timed.bad"] == "not an object"
    assert payload["junk"] == {"Timed.1": {"hash": 1}}


def test_metadata_without_hash_is_recovered_from_the_entries():
    """``${world}.${uid}`` (:594) is enough to find the entry's hash again (:606)."""
    e = entry(uid=6, hash=42, sticky=5)
    payload = {"sticky": {"Timed.6": {"start": 1, "end": 6, "protected": False}}}
    effects = make(2, [e], metadata=payload)

    assert effects.metadata["sticky"]["Timed.6"].hash == 42
    effects.check_timed_effects()
    assert effects.is_effect_active("sticky", e) is True


def test_numeric_strings_survive_a_json_round_trip():
    """The engine wraps ``start``/``end`` in ``Number(...)`` (:626, :648)."""
    e = entry(sticky=2)
    payload = {"sticky": {"Timed.0": {"hash": 1, "start": "0", "end": "4"}}}
    effects = make(1, [e], metadata=payload)

    assert effects.metadata["sticky"]["Timed.0"] == TimedEffect(1, 0, 4, False)
    effects.check_timed_effects()
    assert effects.is_effect_active("sticky", e) is True


def test_missing_entry_is_kept_until_its_interval_passes():
    """``!entry`` branch (:633-639): drop only once ``chat.length >= end``."""
    payload = {"sticky": {"Timed.gone": {"hash": 999, "start": 0, "end": 4, "protected": True}}}

    early = make(2, [], metadata=payload)
    early.check_timed_effects()
    assert "Timed.gone" in early.metadata["sticky"]

    late = make(4, [], metadata=payload)
    late.check_timed_effects()
    assert late.metadata["sticky"] == {}


def test_entry_no_longer_configured_is_purged():
    """``if (!entry[type])`` (:642-646)."""
    e = entry(sticky=None)
    payload = {"sticky": {"Timed.0": {"hash": 1, "start": 0, "end": 99, "protected": True}}}
    effects = make(5, [e], metadata=payload)
    effects.check_timed_effects()
    assert effects.metadata["sticky"] == {}


# ---------------------------------------------------------------------------
# 7. hash comparison is identity
# ---------------------------------------------------------------------------


def test_same_uid_different_hash_is_a_different_entry():
    """``isEffectActive`` compares hashes, never ``world.uid`` (:782)."""
    original = entry(uid=3, hash=11, sticky=4)
    first = sticky_pass(1, original)
    assert first.metadata["sticky"]["Timed.3"].hash == 11
    saved = first.to_metadata()

    # Same book and uid, different content -> a different hash -> not the entry.
    edited = entry(uid=3, hash=22, content="edited", sticky=4)
    effects = make(2, [edited], metadata=saved)
    effects.check_timed_effects()
    assert effects.is_effect_active("sticky", edited) is False
    # ... while the stored hash still matches the entry it was written for.
    assert effects.is_effect_active("sticky", original) is True


def test_hash_lookup_accepts_numeric_strings():
    """``String(getEntryHash(x)) === String(value.hash)`` (:624)."""
    e = entry(uid=1, hash="7", sticky=3)
    payload = {"sticky": {"Timed.1": {"hash": 7, "start": 0, "end": 3, "protected": True}}}
    effects = make(1, [e], metadata=payload)
    effects.check_timed_effects()
    assert effects.buffers["sticky"] == (e,)
    assert effects.is_effect_active("sticky", e) is True


def test_entry_shapes_and_key_helpers():
    as_object = EntryObject(uid=5, world="Obj", hash=3, sticky=1, cooldown=1, delay=1)
    effects = make(0, [as_object])
    effects.set_timed_effects([as_object])
    assert entry_key(as_object) == "Obj.5"
    assert entry_hash(as_object) == 3
    assert effects.metadata["sticky"]["Obj.5"].hash == 3

    # snake_case mappings are accepted too
    snake = {"uid": 5, "world": "Obj", "hash": 3, "sticky": 1}
    effects = make(1, [snake], metadata=effects.to_metadata())
    effects.check_timed_effects()
    assert effects.is_effect_active("sticky", snake) is True


def test_clean_up_empties_the_buffers():
    e = entry(sticky=5, cooldown=5, delay=5)
    others = entry(uid=1, world="Timed", hash=2, sticky=None, cooldown=None, delay=5)
    effects = make(0, [e, others])
    effects.set_timed_effects([e])
    effects.set_timed_effects([others])
    effects.check_timed_effects()

    assert effects.buffers["delay"] == (e, others)
    effects.clean_up()
    assert effects.buffers == {"sticky": (), "cooldown": (), "delay": ()}
    # The persisted table is not touched by cleanUp (:789-792).
    assert "Timed.0" in effects.metadata["sticky"]
    assert "Timed.1" in effects.metadata["sticky"]


# ---------------------------------------------------------------------------
# 8. the scan state machine
# ---------------------------------------------------------------------------


def test_scan_state_values_are_the_engine_values():
    assert scan_state == {"NONE": 0, "INITIAL": 1, "RECURSION": 2, "MIN_ACTIVATIONS": 3}
    assert SCAN_STATE_NONE == 0
    machine = ScanStateMachine()
    assert machine.state == SCAN_STATE_INITIAL
    assert machine.running is True


def test_scan_delay_levels_normalises_and_sorts():
    entries = [
        {"delayUntilRecursion": True},  # -> 1
        {"delayUntilRecursion": 3},
        {"delayUntilRecursion": 1},  # duplicate of True
        {"delayUntilRecursion": False},  # filtered out
        {"delayUntilRecursion": None},  # filtered out
        {"delayUntilRecursion": 2},
        {},
    ]
    assert scan_delay_levels(entries) == [1, 2, 3]

    # No delay-level entry at all -> currentRecursionDelayLevel is 0 (:4759)
    machine = ScanStateMachine(scan_delay_levels([{}]))
    assert machine.current_recursion_delay_level == 0
    assert machine.available_recursion_delay_levels == ()

    # The first level is consumed by the constructor: "Already preset".
    machine = ScanStateMachine(scan_delay_levels([{"delayUntilRecursion": True}]))
    assert machine.current_recursion_delay_level == 1
    assert machine.available_recursion_delay_levels == ()


def test_scan_delay_levels_accepts_objects_and_snake_case():
    class E:
        delayUntilRecursion = 2

    assert scan_delay_levels([E(), {"delay_until_recursion": 1}]) == [1, 2]
    assert scan_delay_levels([{"delay_until_recursion": 0}]) == []


def test_initial_pass_without_new_entries_stops():
    machine = ScanStateMachine()
    step = machine.step(ScanTurn(recursive=True))

    assert step.next_state == SCAN_STATE_NONE
    assert step.continues is False
    assert step.count == 1
    assert step.advance_scan is False
    assert machine.running is False


def test_new_recursion_entries_request_a_recursion_pass():
    machine = ScanStateMachine()
    step = machine.step(ScanTurn(recursive=True, successful_new_for_recursion=2))

    assert step.next_state == SCAN_STATE_RECURSION
    assert step.state_name == "RECURSION"
    assert step.continues is True
    assert machine.state == SCAN_STATE_RECURSION

    # and the recursion pass that finds nothing ends the scan
    step = machine.step(ScanTurn(recursive=True))
    assert step.next_state == SCAN_STATE_NONE
    assert step.count == 2


def test_recursion_requires_the_global_flag():
    machine = ScanStateMachine()
    step = machine.step(ScanTurn(recursive=False, successful_new_for_recursion=5))
    assert step.next_state == SCAN_STATE_NONE


def test_token_budget_overflow_blocks_recursion():
    """``!token_budget_overflowed`` guards both :5097 and :5104."""
    machine = ScanStateMachine()
    step = machine.step(
        ScanTurn(recursive=True, successful_new_for_recursion=3, token_budget_overflowed=True)
    )
    assert step.next_state == SCAN_STATE_NONE

    machine = ScanStateMachine()
    step = machine.step(
        ScanTurn(
            recursive=True,
            activated_total=0,
            min_activations=5,
            buffer_depth=1,
            chat_length=9,
        )
    )
    assert step.next_state == SCAN_STATE_MIN_ACTIVATIONS

    step = machine.step(ScanTurn(recursive=True, has_recurse=True, token_budget_overflowed=True))
    assert step.next_state == SCAN_STATE_NONE


def test_min_activations_advances_the_scan_and_continues():
    machine = ScanStateMachine()
    step = machine.step(
        ScanTurn(
            recursive=True, activated_total=1, min_activations=4, buffer_depth=2, chat_length=10
        )
    )

    assert step.next_state == SCAN_STATE_MIN_ACTIVATIONS
    assert step.advance_scan is True  # buffer.advanceScan() (:5122)
    assert machine.state == SCAN_STATE_MIN_ACTIVATIONS

    # still short, still room to grow -> keeps advancing
    step = machine.step(
        ScanTurn(
            recursive=True, activated_total=2, min_activations=4, buffer_depth=3, chat_length=10
        )
    )
    assert step.next_state == SCAN_STATE_MIN_ACTIVATIONS
    assert step.advance_scan is True

    # satisfied -> nothing keeps the scan alive
    step = machine.step(
        ScanTurn(
            recursive=True, activated_total=4, min_activations=4, buffer_depth=4, chat_length=10
        )
    )
    assert step.next_state == SCAN_STATE_NONE


def test_min_activations_beats_the_delay_level_restart():
    """Branch (4) runs before (5) (:5110 vs :5129)."""
    machine = ScanStateMachine([2])
    assert machine.current_recursion_delay_level == 2
    machine.remaining_delay_levels.append(3)

    step = machine.step(
        ScanTurn(
            recursive=False, activated_total=0, min_activations=3, buffer_depth=1, chat_length=9
        )
    )
    assert step.next_state == SCAN_STATE_MIN_ACTIVATIONS
    assert step.remaining_delay_levels == (3,)
    assert machine.current_recursion_delay_level == 2  # not shifted by this branch


def test_min_activations_depth_ceilings_stop_the_scan():
    # world_info_min_activations_depth_max (own ceiling)
    machine = ScanStateMachine()
    step = machine.step(
        ScanTurn(
            recursive=True,
            activated_total=0,
            min_activations=5,
            min_activations_depth_max=1,
            buffer_depth=2,
            chat_length=10,
        )
    )
    assert step.next_state == SCAN_STATE_NONE

    # chat.length is the hard ceiling (buffer.getDepth() > chat.length, :5117)
    machine = ScanStateMachine()
    step = machine.step(
        ScanTurn(
            recursive=True,
            activated_total=0,
            min_activations=5,
            buffer_depth=3,
            chat_length=3,
        )
    )
    assert step.next_state == SCAN_STATE_NONE

    # a depth_max of 0 means "no ceiling"
    machine = ScanStateMachine()
    step = machine.step(
        ScanTurn(
            recursive=True,
            activated_total=0,
            min_activations=5,
            min_activations_depth_max=0,
            buffer_depth=9,
            chat_length=10,
        )
    )
    assert step.next_state == SCAN_STATE_MIN_ACTIVATIONS
    assert step.advance_scan is True


def test_min_activations_with_a_recursion_buffer_forces_a_recursion_pass():
    """``scanState === MIN_ACTIVATIONS && buffer.hasRecurse()`` (:5104-5107)."""
    machine = ScanStateMachine()
    step = machine.step(
        ScanTurn(
            recursive=True, activated_total=0, min_activations=3, buffer_depth=1, chat_length=9
        )
    )
    assert step.next_state == SCAN_STATE_MIN_ACTIVATIONS

    step = machine.step(
        ScanTurn(
            recursive=True,
            activated_total=0,
            min_activations=3,
            has_recurse=True,
            buffer_depth=2,
            chat_length=9,
        )
    )
    assert step.next_state == SCAN_STATE_RECURSION
    # The depth is *not* advanced by this branch; only (4) calls advanceScan().
    assert step.advance_scan is False


def test_min_activations_never_fires_from_the_initial_state():
    """The guard reads the *current* state, so INITIAL cannot take branch (3)."""
    machine = ScanStateMachine()
    step = machine.step(
        ScanTurn(
            recursive=True,
            activated_total=0,
            min_activations=3,
            has_recurse=True,
            buffer_depth=1,
            chat_length=9,
        )
    )
    assert step.next_state == SCAN_STATE_MIN_ACTIVATIONS
    assert step.advance_scan is True

    # Only a *previous* MIN_ACTIVATIONS pass can trigger the recursion detour.
    machine = ScanStateMachine()
    machine.step(
        ScanTurn(
            recursive=True, activated_total=0, min_activations=3, buffer_depth=1, chat_length=9
        )
    )
    step = machine.step(
        ScanTurn(
            recursive=True,
            activated_total=1,
            min_activations=3,
            has_recurse=True,
            buffer_depth=2,
            chat_length=9,
        )
    )
    assert step.next_state == SCAN_STATE_RECURSION


def test_delay_levels_drive_consecutive_recursion_passes():
    machine = ScanStateMachine([1, 2, 3])
    assert machine.current_recursion_delay_level == 1
    assert machine.available_recursion_delay_levels == (2, 3)

    step = machine.step(ScanTurn(recursive=True))
    assert step.next_state == SCAN_STATE_RECURSION
    assert step.current_recursion_delay_level == 2
    assert step.remaining_delay_levels == (3,)
    assert step.advance_scan is False

    step = machine.step(ScanTurn(recursive=True))
    assert step.next_state == SCAN_STATE_RECURSION
    assert step.current_recursion_delay_level == 3
    assert step.remaining_delay_levels == ()

    # Levels exhausted and nothing new -> the scan is over.
    step = machine.step(ScanTurn(recursive=True))
    assert step.next_state == SCAN_STATE_NONE
    assert step.continues is False
    assert step.current_recursion_delay_level == 3


def test_new_entries_win_over_a_pending_delay_level():
    """Branch (2) runs before (5) (:5097 vs :5129), so the queue is untouched."""
    machine = ScanStateMachine([1, 2])
    step = machine.step(ScanTurn(recursive=True, successful_new_for_recursion=1))

    assert step.next_state == SCAN_STATE_RECURSION
    assert step.remaining_delay_levels == (2,)
    assert machine.current_recursion_delay_level == 1


def test_max_recursion_steps_caps_exactly_that_many_passes():
    machine = ScanStateMachine()
    turn = ScanTurn(recursive=True, successful_new_for_recursion=1, max_recursion_steps=5)

    for _ in range(4):
        step = machine.step(turn)
        assert step.next_state == SCAN_STATE_RECURSION
        assert step.exhausted_by_max_steps is False

    # The 5th pass would be loop #6, which the engine refuses (:4768).
    step = machine.step(turn)
    assert step.count == 6
    assert step.exhausted_by_max_steps is True


def test_unbounded_recursion_stops_on_its_own():
    """``max_recursion_steps === 0`` means "no cap" (:82, :4768)."""
    machine = ScanStateMachine()
    step = machine.step(ScanTurn(recursive=True, successful_new_for_recursion=1))
    assert step.next_state == SCAN_STATE_RECURSION
    assert step.exhausted_by_max_steps is False

    step = machine.step(ScanTurn(recursive=True))
    assert step.next_state == SCAN_STATE_NONE
    assert step.exhausted_by_max_steps is False


def test_max_recursion_steps_and_min_activations_are_exclusive():
    assert scan_states_are_exclusive(0, 0) is True
    assert scan_states_are_exclusive(3, 0) is True
    assert scan_states_are_exclusive(0, 2) is True
    assert scan_states_are_exclusive(3, 2) is False
    assert scan_states_are_exclusive(None, 2) is True

    # A machine handed both knobs still produces a verdict...
    machine = ScanStateMachine()
    step = machine.step(
        ScanTurn(
            recursive=True,
            activated_total=0,
            min_activations=3,
            max_recursion_steps=1,
            buffer_depth=1,
            chat_length=9,
        )
    )
    assert step.next_state == SCAN_STATE_MIN_ACTIVATIONS
    # ... but that pass is loop #1 with a cap of 1, so the break at :4768 already
    # applies and MIN_ACTIVATIONS never actually gets to run.
    assert step.exhausted_by_max_steps is True


def test_scan_event_override_can_stop_and_restart_the_scan():
    """``args.state.next !== scanState`` (:5179-5181)."""
    machine = ScanStateMachine()
    machine.override_next(SCAN_STATE_NONE)
    step = machine.step(ScanTurn(recursive=True, successful_new_for_recursion=1))
    assert step.next_state == SCAN_STATE_NONE
    assert machine.running is False
    # the override is one-shot
    assert machine.pending_override is None

    machine.override_next(SCAN_STATE_RECURSION)
    step = machine.step(ScanTurn(recursive=True))
    assert step.next_state == SCAN_STATE_RECURSION


def test_full_loop_invariants():
    """Drive the loop the way ``worldbook.activate`` is meant to."""
    machine = ScanStateMachine([1, 2])
    passes = 0
    while machine.running:
        step = machine.step(ScanTurn(recursive=True, max_recursion_steps=0))
        if not step.continues:
            break
        passes += 1
        assert passes <= 5
        # The two "continue" reasons are never requested at the same time: a
        # MIN_ACTIVATIONS verdict resets the recursion buffer, it does not
        # restart a recursion pass at the same depth (:5104-5107 vs :5119-5122).
        assert not (step.advance_scan and step.next_state == SCAN_STATE_RECURSION)

    # initial + two delayed recursion levels
    assert passes == 3
    assert machine.count == 3
    assert machine.current_recursion_delay_level == 2
