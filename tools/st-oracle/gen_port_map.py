#!/usr/bin/env python3
"""Regenerate ``tools/st-oracle/port-map.json`` from the machine-readable probe.

    python tools/st-oracle/gen_port_map.py

The probe (``research/_raw/dep_world-info.json``) is the ground truth for *which*
81 functions exist in the vendored SillyTavern snapshot and where they live. This
script adds the porting verdict for each one and refuses to run if the probe has
entries the table does not know about -- so a snapshot bump cannot silently leave
the map stale.

Status vocabulary
-----------------
ported  an implementation of this behaviour exists somewhere in the plugin
        (``tavern/`` or the oracle adapter) and is reachable; gaps are in ``note``.
        Read the note: ``ported`` means "exists", not "proven equivalent".
pending nothing exists, or only a partial stub. These are the rows the oracle
        diff punishes.
exempt  deliberately out of scope for a headless bot port (DOM / editor wiring /
        ST-server persistence); the reason is in ``note``

``research/07-port-map.md`` is the narrative twin of this file; the two agree on
the verdict for all 81 rows.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PROBE = REPO / "research" / "_raw" / "dep_world-info.json"
TARGET = HERE / "port-map.json"

#: js_name -> (python_target, status, note)
TABLE: dict[str, tuple[str, str, str]] = {
    # --- settings / scan entry points -------------------------------------
    "getWorldInfoSettings": (
        "tavern/config.py:WorldBookConfig",
        "ported",
        "The port keeps the same knobs, but as plugin config (scan_depth/budget/recursion) "
        "instead of a global settings bag.",
    ),
    "updateWorldInfoSettings": (
        "tavern/st/worldbook.py:WorldBookSettings",
        "ported",
        "Runtime knobs travel as a WorldBookSettings value object; no global mutation.",
    ),
    "getWorldInfoPrompt": (
        "tavern/core.py:PluginCore.build_turn + tavern/st/prompt.py:build_messages",
        "ported",
        "7 of ST's 8 outputs exist as position buckets; outletEntries is missing and "
        "eventSource.emit(WORLD_INFO_ACTIVATED) has no equivalent. Oracle fixture 05 pins the "
        "assembled strings and buckets.",
    ),
    "setWorldInfoSettings": (
        "tavern/config.py:TavernConfig.from_raw",
        "ported",
        "Scan setup is derived from config plus SessionBinding.worldbooks per turn instead of "
        "global mutation. The oracle must set both the knobs and world_info.globalSelect or "
        "the engine silently scans with defaults (run.mjs guards this and exits 3).",
    ),
    "checkWorldInfo": (
        "tavern/st/worldbook.py:activate",
        "ported",
        "The orchestration loop exists but four behaviours diverge, all witnessed by the "
        "oracle: cumulative token budget with a pass-breaking overflow, min-activations depth "
        "advance, numeric delayUntilRecursion levels, and event emission.",
    ),
    "getSortedEntries": (
        "tavern/st/worldbook.py:activate + tavern/core.py:build_turn",
        "ported",
        "Book selection order (chat -> persona -> global) is not modelled; only the "
        "configured book list is walked. Ordering within a scan is (insertion_order, display_index).",
    ),
    # --- buffer / matching -------------------------------------------------
    # NOTE: WorldInfoBuffer and WorldInfoTimedEffects are classes, so the probe
    # (which walks top-level functions) does not list them; their behaviour is
    # tracked by the functions that depend on them (checkWorldInfo, setWorldInfoSettings).
    "splitKeywordsAndRegexes": (
        "tavern/st/worldbook.py:is_regex_key,compile_key",
        "ported",
    ),
    "isValidRegex": ("tavern/st/worldbook.py:compile_key", "ported"),
    "parseRegexFromString": (
        "tavern/st/worldbook.py:_REGEX_KEY,compile_key",
        "ported",
        "Same /pattern/flags grammar; flag letters beyond i/m/s/x are ignored. Oracle fixture "
        "04 shows the port matching a case-sensitive regex the engine rejects -- a whole-word "
        "interaction, not the parser.",
    ),
    "customTokenizer": (
        "tavern/st/worldbook.py:greedy_token_count",
        "pending",
        "No real tokenizer: tiktoken when installed, else len//3. ST counts with the "
        "generation backend's tokenizer, so budget arithmetic is not comparable (oracle "
        "fixture 12 is comparable:false).",
    ),
    # --- decorators --------------------------------------------------------
    "parseDecorators": (
        "",
        "pending",
        "@@activate / @@dont_activate and leading-decorator stripping are not ported; oracle "
        "fixture 10 shows the port injecting a suppressed @@dont_activate entry and keeping "
        "decorator lines in the emitted content.",
    ),
    # --- inclusion groups --------------------------------------------------
    "filterByInclusionGroups": (
        "tavern/st/worldbook.py:_apply_group_scoring",
        "pending",
        "Different model: the port only drops lighter members of a group. The engine always "
        "yields exactly one winner per group (scored filter, then weighted random roll, then "
        "groupOverride priority). Oracle fixture 08 pins both scans.",
    ),
    "filterGroupsByScoring": (
        "tavern/st/worldbook.py:_apply_group_scoring",
        "ported",
        "Only a groupWeight comparison exists; ST scores by matched-key count "
        "(buffer.getScore) and never uses groupWeight for scoring. Oracle fixture 08 witnesses "
        "the gap.",
    ),
    "filterGroupsByTimedEffects": (
        "tavern/st/worldbook.py:ActivationState",
        "ported",
        "Sticky/cooldown windows exist but are turn-counted and never pin a group winner; "
        "oracle fixture 11 witnesses the delay half of the gap.",
    ),
    # --- converters --------------------------------------------------------
    "convertCharacterBook": (
        "tools/st-oracle/run_python.py:convert_character_book",
        "pending",
        "The translation lives in the oracle, not in tavern/, so the plugin still cannot read "
        "embedded character_book lore. Oracle fixture 09 passes because both sides run the "
        "same adapter -- port it to tavern/st/cards.py.",
    ),
    "convertAgnaiMemoryBook": ("", "pending", "Agnai Memory Book import path."),
    "convertRisuLorebook": ("", "pending", "RisuAI lorebook import path."),
    "convertNovelLorebook": ("", "pending", "NovelAI lorebook import path."),
    # --- lore sources / persistence ---------------------------------------
    "loadWorldInfo": (
        "tavern/st/worldbook.py:load_world_book",
        "ported",
        "Files load from the plugin library with its own cache (reload_library); there is no "
        "/api/worldinfo/get hop.",
    ),
    "getGlobalLore": ("tavern/core.py:build_turn", "ported"),
    "getCharacterLore": ("tavern/core.py:build_turn", "ported"),
    "getChatLore": (
        "tavern/core.py:SessionBinding.worldbooks",
        "ported",
        "The plugin merges chat-bound books into the session binding instead of tracking a "
        "per-chat world name.",
    ),
    "getPersonaLore": (
        "",
        "pending",
        "Persona-bound lorebook: AstrBot has no persona-lorebook concept yet.",
    ),
    "addMissingWorldInfoFields": ("tavern/st/worldbook.py:entry_from_dict", "ported"),
    "nullWorldInfo": ("", "exempt", "Creates an empty book through the settings API."),
    "updateWorldInfoList": ("tavern/core.py:reload_library", "ported"),
    "_save": (
        "tavern/core.py:import_worldbook_bytes",
        "ported",
        "Persistence is a local file write rather than an IPC call; not covered by an oracle "
        "fixture.",
    ),
    "saveWorldInfo": ("tavern/core.py:import_worldbook_bytes", "ported"),
    "renameWorldInfo": (
        "tavern/core.py:_unique_id",
        "pending",
        "Only name de-duplication exists; there is no rename flow that keeps character and "
        "chat references consistent.",
    ),
    "deleteWorldInfo": (
        "tavern/core.py:_safe_target",
        "pending",
        "The plugin resolves and writes library files but exposes no delete path yet.",
    ),
    "createNewWorldInfo": (
        "tavern/core.py:import_worldbook_bytes",
        "ported",
        "Books enter the library by importing a file rather than through a create dialog.",
    ),
    "importWorldInfo": (
        "tavern/core.py:import_worldbook_bytes",
        "ported",
        "Import is a plugin command fed bytes by the adapter, not a browser file picker.",
    ),
    "updateWorldInfoLinks": (
        "",
        "pending",
        "Repoints character/chat/persona references after a rename; the plugin has no such "
        "reference graph yet.",
    ),
    "getFreeWorldEntryUid": (
        "",
        "pending",
        "Editor-only uid allocation; the plugin never writes books back, so nothing allocates "
        "uids.",
    ),
    "getFreeWorldName": ("tavern/core.py:_unique_id", "ported"),
    "moveWorldInfoEntry": ("", "pending", "Drag-and-drop between books; no equivalent."),
    "duplicateWorldInfoEntry": ("", "pending", "Editor clone action; no equivalent."),
    "createWorldInfoEntry": (
        "tavern/st/worldbook.py:entry_from_dict",
        "ported",
        "Row defaults (order 100, depth 4, selective true in ST's editor definition) are "
        "modelled by the parser instead of a template object.",
    ),
    "deleteWorldInfoEntry": ("", "pending", "Editor delete action; no equivalent."),
    "getWorldEntry": (
        "",
        "exempt",
        "512 lines of editor rendering plus the row->form mapping; the port never writes "
        "entries back, so the form half has no consumer.",
    ),
    "sortWorldInfoEntries": (
        "tavern/st/worldbook.py:activate",
        "ported",
        "Scan-time order is (insertion_order, display_index); the editor's sort-mode UI is exempt.",
    ),
    # --- character-book / character UI ------------------------------------
    "checkEmbeddedWorld": (
        "",
        "pending",
        "Detects an embedded character_book; the plugin does not look for one yet (see "
        "convertCharacterBook).",
    ),
    "importEmbeddedWorldInfo": (
        "",
        "pending",
        "Imports the embedded book into the library; blocked on checkEmbeddedWorld.",
    ),
    "setWorldInfoButtonClass": ("", "exempt", "jQuery button state."),
    "charUpdatePrimaryWorld": (
        "",
        "pending",
        "Binds a book to a character; the plugin only binds books per session "
        "(SessionBinding.worldbooks), and has no per-character concept.",
    ),
    "charUpdateAddAuxWorld": (
        "",
        "pending",
        "Auxiliary (additional) character books have no equivalent in the plugin.",
    ),
    "charSetAuxWorlds": (
        "",
        "pending",
        "Replaces the auxiliary book list for a character; no equivalent.",
    ),
    "updateAuxBooks": ("", "pending", "Persists auxiliary book changes; no equivalent."),
    "onWorldInfoChange": ("tavern/core.py:toggle_book,set_books", "ported"),
    "assignLorebookToChat": (
        "tavern/core.py:set_books",
        "ported",
        "The plugin stores one book list per session binding; ST's global/character/chat split "
        "and the shift/alt click variants have no equivalent.",
    ),
    # --- editor / DOM ------------------------------------------------------
    "reloadEditor": ("", "exempt", "DOM reload helper."),
    "registerWorldInfoSlashCommands": (
        "",
        "pending",
        "Slash-command registration for the ST chat UI.",
    ),
    "showWorldEditor": ("", "exempt", "Editor modal."),
    "hideWorldEditor": ("", "exempt", "Editor modal."),
    "getWIElement": ("", "exempt", "jQuery lookup helper."),
    "updateWorldEntryKeyOptionsCache": ("", "exempt", "Autocomplete cache for the editor."),
    "clearEntryList": ("", "exempt", "DOM list reset."),
    "displayWorldEntries": ("", "exempt", "376 lines of editor rendering."),
    "verifyWorldInfoSearchSortRule": ("", "exempt", "Editor search/sort state."),
    "setWIOriginalDataValue": (
        "",
        "pending",
        "Tracks original values for editor dirty-checking; relevant once the converters land, "
        "because originalData is what makes a round-trip lossless.",
    ),
    "deleteWIOriginalDataValue": (
        "",
        "pending",
        "Editor dirty-checking; pairs with setWIOriginalDataValue.",
    ),
    "enableKeysInputHelper": ("", "exempt", "Editor input widget wiring."),
    "handleMatchCheckboxHelper": ("", "exempt", "Editor checkbox wiring."),
    "updatePosOrdDisplayHelper": ("", "exempt", "Editor display counter."),
    "initCharacterFilterSelect2Helper": ("", "exempt", "Select2 widget wiring."),
    "fillCharacterAndTagOptionsHelper": ("", "exempt", "Select2 option population."),
    "handleCharacterFilterChangeHelper": ("", "exempt", "Editor change handler."),
    "handleProbabilityInputHelper": ("", "exempt", "Editor number input wiring."),
    "handleProbabilityToggleHelper": ("", "exempt", "Editor toggle wiring."),
    "handleBooleanSelectHelper": ("", "exempt", "Editor select wiring."),
    "handleNumberInputHelper": ("", "exempt", "Editor number input wiring."),
    "handleEntryStateSelectorHelper": (
        "",
        "exempt",
        "Editor state selector (constant/normal) wiring.",
    ),
    "handleEntryKillSwitchHelper": (
        "",
        "exempt",
        "Click handler only; the data side is entry.disable.",
    ),
    "setCommentPlaceholder": ("", "exempt", "i18n placeholder text."),
    "buildAutocompleteCallback": ("", "exempt", "Editor autocomplete."),
    "getInclusionGroupCallback": ("", "exempt", "Editor autocomplete data."),
    "getAutomationIdCallback": ("", "exempt", "Editor autocomplete data."),
    "getOutletNameCallback": ("", "exempt", "Editor autocomplete data."),
    "createEntryInputAutocomplete": ("", "exempt", "Editor autocomplete wiring."),
    "openWorldInfoEditor": ("", "exempt", "Editor open action."),
    "initWorldInfo": ("", "exempt", "234 lines of DOM/event bootstrap."),
}


def main() -> int:
    probe = json.loads(PROBE.read_text(encoding="utf-8"))
    names = [item["name"] for item in probe]
    unknown = sorted(set(names) - set(TABLE))
    if unknown:
        print("probe has entries missing from TABLE:", ", ".join(unknown), file=sys.stderr)
        return 2
    unused = sorted(set(TABLE) - set(names))
    if unused:
        print("TABLE lists functions absent from the probe:", ", ".join(unused), file=sys.stderr)
        return 2

    rows = []
    for item in probe:
        python_target, status, *rest = TABLE[item["name"]]
        rows.append(
            {
                "js_name": item["name"],
                "js_line": item["line"],
                "loc": item["loc"],
                "python_target": python_target,
                "status": status,
                "note": rest[0] if rest else "",
            }
        )

    TARGET.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    counts = {
        status: sum(1 for r in rows if r["status"] == status)
        for status in ("ported", "pending", "exempt")
    }
    print(f"wrote {TARGET.relative_to(REPO)}: {len(rows)} rows")
    for status, count in counts.items():
        print(f"  {status:<8} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
