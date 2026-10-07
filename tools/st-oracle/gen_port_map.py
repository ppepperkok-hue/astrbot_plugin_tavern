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
ported  a Python implementation exists and is called by the plugin
pending not ported yet (the interesting list; the oracle diffs these)
exempt  out of scope for a headless port, with the reason in ``note``
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
        "tools/st-oracle/run_python.py:main",
        "pending",
        "Prompt assembly is split: tavern/st/prompt.py renders slots, tavern/core.py "
        "build_turn() orchestrates. The st-oracle adapter is what the diff measures today; "
        "no single ported function owns this contract yet.",
    ),
    "setWorldInfoSettings": (
        "tavern/config.py:TavernConfig.from_dict",
        "ported",
        "Scan setup is derived from config + SessionBinding.worldbooks per turn.",
    ),
    "checkWorldInfo": (
        "tavern/st/worldbook.py:activate",
        "pending",
        "The orchestration loop exists but diverges: timed effects (sticky/cooldown), "
        "min-activations scans, cumulative token budget and event emission are not ported. "
        "See fixtures 06/07/08 in tools/st-oracle/out.",
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
        "Same /pattern/flags grammar. Flag letters beyond i/m/s/x are ignored.",
    ),
    "customTokenizer": ("", "exempt", "UI tokenizer for the keys input box."),
    # --- decorators --------------------------------------------------------
    "parseDecorators": (
        "",
        "pending",
        "@@activate / @@dont_activate and the leading-decorator stripping are not ported; "
        "fixture 10 shows the port emitting defunct @@dont_activate entries.",
    ),
    # --- inclusion groups --------------------------------------------------
    "filterByInclusionGroups": (
        "tavern/st/worldbook.py:_apply_group_scoring",
        "pending",
        "Different model: the port only drops lighter members of a group. The engine always "
        "yields exactly one winner per group (weighted random roll, groupOverride priority, "
        "sticky pinning). Fixture 08 pins both.",
    ),
    "filterGroupsByScoring": ("tavern/st/worldbook.py:_apply_group_scoring", "pending"),
    "filterGroupsByTimedEffects": ("tavern/st/worldbook.py:ActivationState", "pending"),
    # --- converters --------------------------------------------------------
    "convertCharacterBook": (
        "tools/st-oracle/run_python.py:convert_character_book",
        "pending",
        "The translation lives in the oracle, not in tavern/, so the plugin still cannot read "
        "embedded character_book lore. Port it to tavern/st/cards.py.",
    ),
    "convertAgnaiMemoryBook": ("", "pending", "Agnai Memory Book import path."),
    "convertRisuLorebook": ("", "pending", "RisuAI lorebook import path."),
    "convertNovelLorebook": ("", "pending", "NovelAI lorebook import path."),
    # --- lore sources / persistence ---------------------------------------
    "loadWorldInfo": (
        "tools/st-oracle/run_python.py:build_books",
        "ported",
        "The plugin loads files through tavern/st/worldbook.py:load_world_book and its own "
        "cache (reload_library); there is no /api/worldinfo/get hop.",
    ),
    "getGlobalLore": ("tavern/core.py:build_turn", "ported"),
    "getCharacterLore": ("tavern/core.py:build_turn", "ported"),
    "getChatLore": (
        "",
        "exempt",
        "Chat-bound lorebook needs chat_metadata; the plugin attaches books per chat binding instead.",
    ),
    "getPersonaLore": (
        "",
        "exempt",
        "Persona-bound lorebook: AstrBot has no persona-lorebook concept.",
    ),
    "addMissingWorldInfoFields": ("tavern/st/worldbook.py:entry_from_dict", "ported"),
    "nullWorldInfo": ("", "exempt", "Creates an empty book through the settings API."),
    "updateWorldInfoList": ("tavern/core.py:reload_library", "ported"),
    "_save": ("", "exempt", "Writes a book back through the ST server API."),
    "saveWorldInfo": ("tavern/core.py:import_worldbook_bytes", "ported"),
    "renameWorldInfo": ("", "exempt", "Rename is a DOM flow; the plugin re-imports instead."),
    "deleteWorldInfo": ("", "exempt", "File deletion, owned by the plugin library layer."),
    "createNewWorldInfo": ("", "exempt", "Creates a book from the editor UI."),
    "importWorldInfo": ("", "exempt", "Browser file-picker import."),
    "updateWorldInfoLinks": (
        "",
        "exempt",
        "Repoints character/chat/persona links after a rename; the plugin has no such links.",
    ),
    "getFreeWorldEntryUid": (
        "",
        "exempt",
        "Editor-only uid allocation; the plugin never writes books back.",
    ),
    "getFreeWorldName": ("", "exempt", "Editor-only unique-name allocation."),
    "moveWorldInfoEntry": ("", "exempt", "Drag-and-drop between books."),
    "duplicateWorldInfoEntry": ("", "exempt", "Editor clone action."),
    "createWorldInfoEntry": (
        "tavern/st/worldbook.py:entry_from_dict",
        "ported",
        "Row defaults (order 100, depth 4, selective true in ST's editor definition) are "
        "modelled by the parser instead of a template object.",
    ),
    "deleteWorldInfoEntry": ("", "exempt", "Editor delete action."),
    "getWorldEntry": (
        "tavern/st/worldbook.py:entry_from_dict",
        "pending",
        "512 lines of editor rendering plus the row->form mapping. Only the data half is "
        "covered; the port never writes entries back.",
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
        "Detects an embedded character_book; the plugin does not look for one yet.",
    ),
    "importEmbeddedWorldInfo": ("", "pending", "Imports the embedded book into the library."),
    "setWorldInfoButtonClass": ("", "exempt", "jQuery button state."),
    "charUpdatePrimaryWorld": ("", "exempt", "Writes character.data.extensions.world."),
    "charUpdateAddAuxWorld": ("", "exempt", "Writes extra character lorebook links."),
    "charSetAuxWorlds": ("", "exempt", "Persists auxiliary lorebook links."),
    "updateAuxBooks": ("", "exempt", "Refreshes auxiliary lorebook links."),
    "onWorldInfoChange": ("tavern/core.py:toggle_book,set_books", "ported"),
    "assignLorebookToChat": ("tavern/core.py:set_books", "ported"),
    # --- editor / DOM ------------------------------------------------------
    "reloadEditor": ("", "exempt", "DOM reload helper."),
    "registerWorldInfoSlashCommands": (
        "",
        "exempt",
        "Slash-command registration for the ST chat UI.",
    ),
    "showWorldEditor": ("", "exempt", "Editor modal."),
    "hideWorldEditor": ("", "exempt", "Editor modal."),
    "getWIElement": ("", "exempt", "jQuery lookup helper."),
    "updateWorldEntryKeyOptionsCache": ("", "exempt", "Autocomplete cache for the editor."),
    "clearEntryList": ("", "exempt", "DOM list reset."),
    "displayWorldEntries": ("", "exempt", "376 lines of editor rendering."),
    "verifyWorldInfoSearchSortRule": ("", "exempt", "Editor search/sort state."),
    "setWIOriginalDataValue": ("", "exempt", "Tracks original values for editor dirty-checking."),
    "deleteWIOriginalDataValue": ("", "exempt", "Editor dirty-checking."),
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
        "tavern/st/worldbook.py:entry_from_dict",
        "ported",
        "The kill switch is entry.disable; only the click handler is UI.",
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
