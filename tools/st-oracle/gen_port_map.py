#!/usr/bin/env python3
"""Regenerate ``tools/st-oracle/port-map.json`` from the machine-readable probes.

    python tools/st-oracle/gen_port_map.py

Each probe under ``research/_raw/dep_*.json`` is the ground truth for *which*
functions exist in the vendored SillyTavern snapshot and where they live. This
script adds the porting verdict for each one and refuses to run if a probe has
entries the table does not know about, if the table names entries a probe does not
carry, or if a probe that ``PROBE_SOURCES`` names is not on disk -- so a snapshot
bump cannot silently leave the map stale.

Probes are committed artifacts, not something this script derives: a probe
generated inside the same run that consumes it would move the map's rows under
the reader's feet. Adding a source means generating its ``dep_*.json``, committing
it, and writing its table in the same change.

One probe per ported source file, and one table per probe:

==============================  ==========================  ====
probe                           source                      step
==============================  ==========================  ====
``dep_world-info.json``         ``world-info.js``           S1
``dep_openai.json``             ``openai.js``               S2
``dep_PromptManager.json``      ``PromptManager.js``        S2
``dep_prompt-converters.json``  ``prompt-converters.js``    S4
==============================  ==========================  ====

**Every** row is hand-classified: the map is the artifact other people trust, so
a verdict is never derived from a name. Classify by reading the function body in
``research/_raw/st-src/``. (The S2 probe rows were once resolved by snake-casing
the JS name and looking for a symbol under ``tavern/``; that answered "is there a
symbol with this name", which is never the question -- it reported
``ChatCompletion`` as unimplemented because the port spells classes in
PascalCase, and it reported a settings-UI function as ported whenever some
unrelated helper shared its name.)

Status vocabulary
-----------------
ported  an implementation of this behaviour exists somewhere in the plugin
        (``tavern/`` or the oracle adapter) and is reachable; gaps are in ``note``.
        Read the note: ``ported`` means "exists", not "proven equivalent".
pending genuinely in scope for a headless engine port and genuinely not done.
        These are the rows the oracle diff punishes.
exempt  deliberately out of scope for a headless bot port; the reason is in
        ``note``. The four families that keep recurring:
        DOM / editor wiring (``tavern/`` has no DOM at all), browser settings-UI
        controls, the SillyTavern dev-server HTTP endpoints (``/api/*``) and the
        browser secret store, and behaviour the S2 port deliberately delegates
        (streaming, media inlining, tool calls, per-provider request-body
        building -- AstrBot's provider layer owns the request).

``research/07-port-map.md`` is the narrative twin of this file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RAW = REPO / "research" / "_raw"
TARGET = HERE / "port-map.json"

#: probe file -> snapshot file it was derived from. Every probe found in RAW that
#: starts with ``dep_`` must appear here, and every entry here must have its probe
#: committed under ``RAW``; the check in :func:`main` fails closed either way, so
#: neither a new source nor a vanished probe can silently leave the map stale.
#:
#: The map never derives a probe at run time. A ``dep_*.json`` is ground truth
#: that a human or a review can diff; a probe generated on the fly inside the same
#: run that consumes it would move rows under the reader's feet on a snapshot bump.
PROBE_SOURCES: dict[str, str] = {
    "dep_world-info.json": "world-info.js",
    "dep_prompt-converters.json": "prompt-converters.js",
    "dep_openai.json": "openai.js",
    "dep_PromptManager.json": "PromptManager.js",
}

#: snapshot file -> the module path it is loaded from in the shim tree, used only
#: in the report.
PROBE_NOTE: dict[str, str] = {
    "dep_world-info.json": "S1 world info",
    "dep_prompt-converters.json": "S4 provider converters",
    "dep_openai.json": "S2 chat completion",
    "dep_PromptManager.json": "S2 prompt manager",
}

#: js_name -> (python_target, status, note), for ``world-info.js`` (S1).
WORLD_INFO_TABLE: dict[str, tuple[str, str, str]] = {
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
        "tavern/st/worldbook.py:_filter_by_inclusion_groups",
        "ported",
        "The one-winner-per-group filter is now mirrored (worldbook.py:933-1045): sticky "
        "members win, cooldown/delay members are dropped, useGroupScoring (global or per entry) "
        "keeps the best key score, groupOverride is a priority tier, else a groupWeight weighted "
        "roll through settings.rng. The earlier 'only drops lighter members' note is obsolete; "
        "fixture 08 is the current verdict.",
    ),
    "filterGroupsByScoring": (
        "tavern/st/worldbook.py:_filter_by_inclusion_groups",
        "ported",
        "Scores with buffer.get_score and keeps the members at the best score "
        "(worldbook.py:1001-1017), which is what the reference does; it is folded into the "
        "inclusion-group filter instead of standing alone. The scan-state value and the "
        "entry->buffer view come from the caller (_buffer_view, worldbook.py:629).",
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


#: js_name -> (python_target, status, note), for ``prompt-converters.js`` (S4).
#:
#: ``ported`` here means only "a same-named function exists in the port and the
#: converter oracle has a fixture that passes for it". It never means "this is the
#: whole of the behaviour" -- the note carries what is missing. A row with no
#: fixture is ``pending`` even if a stub exists, because nothing has proven it.
CONVERTERS_TABLE: dict[str, tuple[str, str, str]] = {
    "PROMPT_PROCESSING_TYPE": (
        "tavern/st/prompt_converters.py:PROMPT_PROCESSING_TYPE",
        "ported",
        "The dispatch vocabulary postProcessPrompt switches on. Copied verbatim, "
        "including the deprecated CLAUDE alias that still has to behave like MERGE.",
    ),
    "getPromptNames": (
        "tavern/st/prompt_converters.py:get_prompt_names",
        "ported",
        "Fixtures 01/02. The predicate is method-shaped because the reference's "
        "reads `this.groupNames` (prompt-converters.js:54-56) -- a plain closure "
        "would be quietly more forgiving than the engine.",
    ),
    "addAssistantPrefix": (
        "tavern/st/prompt_converters.py:add_assistant_prefix",
        "ported",
        "Fixtures 03/03b/03c/04 pin every arm of the ``hasAnyTools`` disjunction "
        "(``:71``): the no-tools case sets the flag, a non-empty ``tools`` argument "
        "suppresses it, and a ``role == 'tool'`` message suppresses it with ``tools`` "
        "null. They also pin that the property name is the caller's argument rather "
        "than a hardcoded ``prefix``, and that an empty prompt returns before the "
        "trailing-message check.",
    ),
    "postProcessPrompt": (
        "tavern/st/prompt_converters.py:post_process_prompt",
        "ported",
        "Fixtures 10/11 pin the dispatch: an unknown type and NONE fall through with "
        "the messages unmutated, and STRICT routes into mergeMessages with "
        "placeholders (the splice at index 1).",
    ),
    "convertClaudePrompt": (
        "tavern/st/prompt_converters.py:convert_claude_prompt",
        "ported",
        "Fixtures 20/21/22. Deprecated upstream -- only used for token counting -- "
        "but still reachable, and they pin the two easily-missed parts: the "
        "``FixHumMsg`` role and the prefix table at :175-180, including the "
        "``excludePrefixes`` spelling that names the group member instead.",
    ),
    "convertClaudeMessages": (
        "tavern/st/prompt_converters.py:convert_claude_messages",
        "ported",
        "Fixtures 23-30, 39, 40. The largest function in the module: "
        "system-prompt extraction, tool_use/tool_result rewriting, image migration "
        "between turns, role merging and prefill. The zero-width space of :302 for "
        "an empty text part is reproduced exactly, and 39/40 pin the group-name "
        "predicate on both the system-extraction and the in-place arms.",
    ),
    "convertCohereMessages": (
        "tavern/st/prompt_converters.py:convert_cohere_messages",
        "ported",
        "Fixtures 13b/13c/13d. 13b deliberately pins the reference's own quirk: "
        "``messages.splice(index - 1, 1)`` (:397) runs *during* the forEach, so the "
        "tool-call message is revisited and its content written twice. That looked "
        "like a reference bug; it was re-read against :392-401 and pinned rather "
        "than 'fixed'. 13d pins ``tc?.function?.name`` joining to the empty string "
        "of JS ``Array.join``, not Python's ``'None'``.",
    ),
    "convertGooglePrompt": (
        "tavern/st/prompt_converters.py:convert_google_prompt",
        "ported",
        "Fixtures 40-47, 57-59. The most intricate arm, and the fixtures are "
        "one-per-branch because its behaviour is version-gated on the model name: "
        "thought-signature injection, the ``skip_thought_signature_validator`` "
        "fallback, the no-prefill model regex, the ``tryParse(args) ?? args`` "
        "fallback (43), media resolutions with ``auto`` deliberately unmapped (44), "
        "and the toolNameMap lookup falling back to ``'unknown'`` (42).",
    ),
    "convertAI21Messages": (
        "tavern/st/prompt_converters.py:convert_ai21_messages",
        "ported",
        "Fixtures 10e-10h. 10h pins a drain boundary that a Python transliteration "
        "gets wrong by default: the reference's ``for (i = 0; ...) break`` leaves "
        "``i`` at the count of leading system turns because ``break`` runs before "
        "the update expression, which is what ``splice(0, i)`` needs -- a "
        "``range``-loop ``break`` leaves it at the last index visited and duplicates "
        "the final system turn.",
    ),
    "convertMistralMessages": (
        "tavern/st/prompt_converters.py:convert_mistral_messages",
        "ported",
        "Fixtures 11b-11f. Two traps pinned: the prefix flag is read from config at "
        "call time (:709) and tool ids are sha512-truncated to nine hex characters "
        "(:715); plus the ``fixToolMessages`` fixpoint. 11e/11f pin the empty and "
        "non-array inputs, where JS yields ``undefined`` and Python would raise "
        "``IndexError``.",
    ),
    "convertXAIMessages": (
        "tavern/st/prompt_converters.py:convert_xai_messages",
        "ported",
        "Fixtures 12c/12d/12e. 12c pins the three-rule table and that the name is "
        "deleted on *every* matched rule, not only the one that added a prefix.",
    ),
    "mergeMessages": (
        "tavern/st/prompt_converters.py:merge_messages",
        "ported",
        "Fixtures 12-16, 14b-14c. Four interacting options "
        "(strict/placeholders/single/tools) plus a recursive re-entry when strict is "
        "set, so each combination has its own fixture; 16 drives the media token "
        "round trip at :910-934 and the group predicate through the sentinel. The "
        "reference's ``= {}`` default and an explicit ``null`` are different in JS "
        "and are modelled with a sentinel -- deliberately unpinnable, because a "
        "fixture that hands the reference null can never match (the differ scores "
        "the presence of a reference error, never its text).",
    ),
    "convertTextCompletionPrompt": (
        "tavern/st/prompt_converters.py:convert_text_completion_prompt",
        "ported",
        "Fixtures 10b/10c/10d: all three render rules, the empty-array result "
        "(``'\\nassistant:'``), and the string passthrough that returns its argument "
        "untouched.",
    ),
    "cachingAtDepthForClaude": (
        "tavern/st/prompt_converters.py:caching_at_depth_for_claude",
        "ported",
        "Fixtures 31/32, including the prefill-break so the ``passedThePrefill`` arm "
        "is not dead code in the port.",
    ),
    "cachingAtDepthForOpenRouterClaude": (
        "tavern/st/prompt_converters.py:caching_at_depth_for_open_router_claude",
        "ported",
        "Fixture 33. Same depth walk as the Claude arm with two differences the "
        "fixture pins: system messages are skipped without counting toward depth "
        "(:1033-1036), and the walk breaks at ``depth === cachingAtDepth + 2``.",
    ),
    "cachingSystemPromptForOpenRouter": (
        "tavern/st/prompt_converters.py:caching_system_prompt_for_open_router",
        "ported",
        "Fixtures 34/35. Returns nothing and mutates, so it is scored on the "
        "post-call arguments -- which is what the converter oracle records. 34 pins "
        "the array-content arm and that only the *first* system message is ever "
        "considered; 35 pins the string arm, where the content is replaced by a "
        "one-part array. The cache-control object omits ``ttl`` entirely when none "
        "is given, so both spellings are pinned.",
    ),
    "calculateClaudeBudgetTokens": (
        "tavern/st/prompt_converters.py:calculate_claude_budget_tokens",
        "ported",
        "Fixtures 48/49: every reasoning-effort value across both the numeric and "
        "the adaptive-model paths, plus an unknown effort and the stream and floor "
        "boundaries.",
    ),
    "calculateGoogleBudgetTokens": (
        "tavern/st/prompt_converters.py:calculate_google_budget_tokens",
        "ported",
        "Fixtures 50-56: the whole matrix. Each fixture's maxTokens was chosen to "
        "pin a cap or a floor as a side effect -- flash has no 512 floor, flash-lite "
        "does, pro caps at 32768 -- and 52 pins the dispatch order (``gemini-3.5-flash-lite`` "
        "matches the flash branch first and returns ``'medium'``, not 25000) and the "
        "``[.\\d]*`` regex allowing zero digits.",
    ),
    "embedOpenRouterMedia": (
        "tavern/st/prompt_converters.py:embed_open_router_media",
        "ported",
        "Fixture 36.",
    ),
    "addReasoningContentToToolCalls": (
        "tavern/st/prompt_converters.py:add_reasoning_content_to_tool_calls",
        "ported",
        "Fixture 37.",
    ),
    "addOpenRouterSignatures": (
        "tavern/st/prompt_converters.py:add_open_router_signatures",
        "ported",
        "Fixtures 38/38b, the second with thought signatures configured off so the "
        "``signature`` field is not carried.",
    ),
}


#: js_name -> (python_target, status, note), for ``openai.js`` (S2).
#:
#: ``openai.js`` is the browser's chat-completion module. The port keeps the
#: *assembly* half and drops the rest by decision, so this table is dominated by
#: ``exempt`` and that is honest, not a way to shrink the backlog:
#:
#: * assembly (``formatWorldInfo`` .. ``preparePromptsForChatCompletion``) is
#:   ported in ``tavern/st/prompt_build.py`` + ``tavern/st/prompt.py``;
#: * ``ChatCompletion`` / ``Message`` / ``MessageCollection`` / ``TokenHandler``
#:   are ported in ``tavern/st/chat_completion.py``;
#: * streaming (``sendOpenAIRequest``'s transport, ``getStreamingReply``), media
#:   inlining, tool calls, reasoning signatures, logprobs, per-provider
#:   request-body building and the whole settings/preset/proxy/model-list UI are
#:   out of scope -- ``tavern/`` has no DOM, no web server to call back into, and
#:   AstrBot's provider layer owns the request body (see the S2 docstring in
#:   ``tavern/st/prompt_build.py`` and ``tavern/backends/astrbot_provider.py``);
#: * the rows that stay ``pending`` are the ones a headless engine genuinely
#:   still needs. The largest is the token-budget layer: the model resolvers
#:   (``getMaxContext*``) and the budget wiring are what make
#:   ``ChatCompletion.set_token_budget`` (already ported) reachable from
#:   ``tavern/core.py::PluginCore.build_turn``, which today assembles every turn
#:   with no budget at all.
OPENAI_TABLE: dict[str, tuple[str, str, str]] = {
    # ---- assembly: messages in, messages out ----------------------------
    "setOpenAIMessages": (
        "tavern/st/prompt_build.py:populate_chat_history",
        "ported",
        "The chat -> prompt-message conversion (newest-first iteration, prepending "
        "so the result reads oldest-first) is openai.js:945-1075 and is mirrored at "
        "prompt_build.py:867-888; the media/tool branches inside the loop (962-1064) "
        "are out of scope.",
    ),
    "setOpenAIMessageExamples": (
        "tavern/st/prompt_build.py:populate_dialogue_examples + tavern/st/prompt.py:"
        "parse_dialogue_examples",
        "ported",
        "'<START>' -> '{Example Dialogue:}' plus a per-block list of named turns; "
        "the port splits with parse_dialogue_examples and re-wraps in "
        "populate_dialogue_examples. The JS result is an array of arrays, the port's "
        "is a flat list of turns -- the wrapper carries the structure.",
    ),
    "parseExampleIntoIndividual": (
        "tavern/st/prompt.py:parse_dialogue_examples",
        "ported",
        "Name-prefix detection, glueing prefix-less lines onto the current turn and "
        "the group-name branch are all present; the port takes the known names from "
        "RenderOptions instead of the page's name1/getGroupNames globals, and it has "
        "no appendNamesForGroup flag.",
    ),
    "formatWorldInfo": (
        "tavern/st/prompt_build.py:format_world_info",
        "ported",
        "{0} substitution of the already-activated blob; the reference reads "
        "power_user.wi_format when no override is passed, the port takes the format "
        "as an argument (wi_format=None returns the value untouched).",
    ),
    "populationInjectionPrompts": (
        "tavern/st/prompt_build.py:population_injection_prompts",
        "ported",
        "Relative/absolute extension prompts, the string->numeric role mapping and "
        "the descending injection_order sort are copied; the port injects "
        "get_extension_prompt/get_extension_prompt_max_depth as callables instead of "
        "reading extension_settings.",
    ),
    "populateChatHistory": (
        "tavern/st/prompt_build.py:populate_chat_history",
        "ported",
        "Budget reservation for newMainChat/groupNudge/continueNudge, the "
        "continueNudge extraction, emptyUserMessageReplacement and the names_behavior "
        "COMPLETION branch are copied. Trimmed: media inlining, tool calls, "
        "isReasoningSignatureSupported. The function signature carries cycle_prompt "
        "because a harness bug once dropped it (see STATUS-S2.md defect 10).",
    ),
    "populateDialogueExamples": (
        "tavern/st/prompt_build.py:populate_dialogue_examples",
        "ported",
        "One newChat system message per dialogue inside the loop, forced system role, "
        "setName(prompt.name), the all-or-nothing affordability gate and the break "
        "(not continue) are all copied.",
    ),
    "getPromptPosition": ("tavern/st/prompt_build.py:get_prompt_position", "ported"),
    "getPromptRole": ("tavern/st/prompt_build.py:get_prompt_role", "ported"),
    "populateChatCompletion": (
        "tavern/st/prompt_build.py:populate_chat_completion",
        "ported",
        "The assembly order of the whole prompt, pinned by the 8 diff_assembly.py "
        "fixtures. Trimmed: media, tools, reasoning signatures. The port accepts the "
        "optional prompts (impersonate/quietPrompt/bias/enhanceDefinitions) as "
        "arguments because the reference merges them in "
        "preparePromptsForChatCompletion (STATUS-S2.md defect 9).",
    ),
    "preparePromptsForChatCompletion": (
        "tavern/st/prompt_build.py:prepare_prompts_for_chat_completion",
        "ported",
        "The nine system blocks plus the user-relative extension prompts and the "
        "substituted scenario/personality/bias templates are copied; the port takes a "
        "settings mapping plus injectable callbacks instead of oai_settings, "
        "power_user and promptManager.",
    ),
    "prepareOpenAIMessages": (
        "tavern/core.py:PluginCore.build_turn + tavern/st/prompt_build.py:"
        "prepare_prompts_for_chat_completion",
        "ported",
        "The assembly it orchestrates exists and the 8 assembly fixtures pin it, "
        "assembled headlessly by build_turn. Gaps: setTokenBudget is never called, "
        "squash is left to the backend and the CHAT_COMPLETION_PROMPT_READY event has "
        "no equivalent.",
    ),
    # ---- the token-budget layer a headless turn still needs -------------
    "getMaxContextOpenAI": (
        "tavern/config.py:BackendConfig + tavern/st/prompt.py:trim_history",
        "pending",
        "A regex -> context-size table for the OpenAI models; nothing in the port "
        "resolves a provider context window. Today build_turn never sets a token "
        "budget, so trim_history is only reachable from tests and the oracle.",
    ),
    "getGeminiMaxContext": ("", "pending", "Gemini inputTokenLimit lookup from model_list."),
    "getGeminiMaxTemp": ("", "pending", "Gemini maxTemperature lookup from model_list."),
    "getMistralMaxContext": ("", "pending", "Mistral context lookup from model_list."),
    "getGroqMaxContext": ("", "pending", "Groq context map and model_list lookup."),
    "getZaiMaxContext": ("", "pending", "Static GLM model -> context map."),
    "getSiliconflowMaxContext": ("", "pending", "Static SiliconFlow model -> context map."),
    "getMoonshotMaxContext": ("", "pending", "Moonshot context lookup from model_list."),
    "getFireworksMaxContext": ("", "pending", "Fireworks context lookup from model_list."),
    "getChutesMaxContext": ("", "pending", "Chutes context lookup from model_list."),
    "getElectronHubMaxContext": ("", "pending", "ElectronHub token lookup from model_list."),
    "getNanoGptMaxContext": ("", "pending", "NanoGPT context lookup from model_list."),
    "getChatCompletionModel": (
        "tavern/config.py:BackendConfig",
        "pending",
        "source -> model-name resolution. The port has a single configured provider "
        "id and no chat_completion_source switch, so the per-source spellings "
        "(claude_model/openai_model/openrouter_model/...) have no equivalent.",
    ),
    # ---- response/error handling ---------------------------------------
    "getChatCompletionErrorMessage": (
        "tavern/backends/base.py:BackendError",
        "pending",
        "Extracting data.error / data.detail.error / message / code / type from a "
        "provider response body. The port only wraps exceptions into BackendError "
        "with str(exc); a provider payload's error object is never read.",
    ),
    "checkQuotaError": (
        "",
        "exempt",
        "Quota-error UI notification (Popup.show). Provider failures already surface "
        "as BackendError with a message; revisiting this means revisiting the "
        "deliberate error strategy, not porting one function.",
    ),
    "checkModerationError": (
        "",
        "exempt",
        "Moderation-error toastr.info (flagged text + reasons). Same call as "
        "checkQuotaError: AstrBot's provider surface reports the failure.",
    ),
    "tryParseStreamingError": (
        "",
        "exempt",
        "Streaming, out of scope: the port never consumes a stream (the AstrBot "
        "backend awaits one completion; backends/base.py::collect_stream exists only "
        "for adapters that already stream).",
    ),
    "parseChatCompletionLogprobs": (
        "",
        "exempt",
        "Logprobs, out of scope: the plugin does not display or store token "
        "probabilities, and the per-provider shapes are a debug/analysis feature.",
    ),
    "parseOpenAIChatLogprobs": ("", "exempt", "Logprobs, out of scope."),
    "parseOpenAITextLogprobs": ("", "exempt", "Logprobs, out of scope."),
    "calculateLogitBias": (
        "",
        "exempt",
        "Per-token bias values are fetched from the SillyTavern dev server "
        "(`/api/backends/chat-completions/bias`), which the plugin does not have. "
        "The `bias` prompt *block* is a separate thing and is in scope.",
    ),
    # ---- generation request / provider transport ------------------------
    "createGenerationParameters": (
        "",
        "exempt",
        "437 lines that build the provider request body (generate_data, per-provider "
        "params, reasoning signatures, media migration). The plugin never sends its "
        "own request body: astrbot_provider.py::build_kwargs hands messages to "
        "AstrBot's provider and sillytavern.py builds the ST payload. Copying it "
        "would be provider-specific work with no consumer.",
    ),
    "sendOpenAIRequest": (
        "",
        "exempt",
        "Transport (fetch + SSE + abort) plus the pre-request orchestration. The port "
        "delegates transport entirely to the backend layer.",
    ),
    "getStreamingReply": ("", "exempt", "Streaming delta parsing, out of scope."),
    "getReasoningEffort": (
        "",
        "exempt",
        "Provider-specific reasoning-effort parameter (string vs numeric per source, "
        "120 lines of source/model tables) for the request body; see "
        "createGenerationParameters.",
    ),
    "getVerbosity": (
        "",
        "exempt",
        "Same request-body family: maps the display setting to the provider's verbosity parameter.",
    ),
    # ---- the prompt-message model itself --------------------------------
    "ChatCompletion": (
        "tavern/st/chat_completion.py:ChatCompletion",
        "ported",
        "The class and its message model are ported there (Message/MessageCollection/"
        "ChatCompletion/TokenHandler, chat_completion.py:187-630) and diff_prompt.py "
        "plus the 8-diff_assembly.py fixtures pin the assembly-visible members. Note: "
        "the probe walks *top-level functions* and so lists this class under its JS "
        "camel-case spelling; the port spells classes in PascalCase, which is why a "
        "name-derived verdict could never resolve it. Not carried over: media "
        "inlining, tool-call/reasoning paths, and setTokenBudget is reachable only "
        "from tests and the oracle (see the getMaxContext* rows).",
    ),
    # ---- settings / preset / model-list UI ------------------------------
    "validateReverseProxy": ("", "exempt", "Reverse-proxy URL validation plus a jQuery warning."),
    "setupChatCompletionPromptManager": (
        "",
        "exempt",
        "Creates the page's PromptManager singleton, renders it and debounces "
        "character-change re-configuration. The port has no singleton: the caller "
        "builds a PromptCollection per turn and keeps no mutable global state.",
    ),
    "loadOpenAISettings": ("", "exempt", "Loads ST preset names into the settings dropdown."),
    "migrateChatCompletionSettings": (
        "",
        "exempt",
        "Rewrites a legacy SillyTavern *preset* (old key names, old model aliases) "
        "in place at load time. This is browser-app data migration for a preset file "
        "the port does not own; TavernConfig.from_raw reads the plugin's own config "
        "instead.",
    ),
    "getChatCompletionPreset": ("", "exempt", "Collects the preset body that gets POSTed to ST."),
    "saveOpenAIPreset": ("", "exempt", "POST /api/presets/save plus a dropdown refresh."),
    "loadProxyPresets": ("", "exempt", "Fills the proxy dropdown; global proxies array."),
    "setProxyPreset": ("", "exempt", "Mutates the in-page proxy list; no DOM-free consumer."),
    "getStatusOpen": (
        "",
        "exempt",
        "Connectivity check against the ST server plus a status toast; the port asks "
        "AstrBot's provider instead (and its own /酒馆 状态 command).",
    ),
    "getOpenRouterModelTemplate": ("", "exempt", "DOMPurify + jQuery option template."),
    "calculateOpenRouterCost": ("", "exempt", "Model cost display (jQuery + i18n)."),
    "getElectronHubModelTemplate": ("", "exempt", "DOMPurify + jQuery option template."),
    "calculateElectronHubCost": ("", "exempt", "Model cost display."),
    "getChutesModelTemplate": ("", "exempt", "DOMPurify + jQuery option template."),
    "calculateChutesCost": ("", "exempt", "Model cost display."),
    "getNanoGptModelTemplate": ("", "exempt", "DOMPurify + jQuery option template."),
    "getAimlapiModelTemplate": ("", "exempt", "DOMPurify + jQuery option template."),
    "saveModelList": ("", "exempt", "383 lines of model-list rendering and sorting UI."),
    "sortModelsBy": (
        "",
        "exempt",
        "Helper for the model-list dropdown (called only from saveModelList); pure, "
        "but it exists to order a UI list the plugin does not have.",
    ),
    "groupModelsByVendor": ("", "exempt", "Model-list dropdown grouping helper."),
    "setNamesBehaviorControls": ("", "exempt", "Radio-button state for names_behavior."),
    "setContinuePostfixControls": ("", "exempt", "Radio-button state for continue_postfix."),
    "setToolReasoningControls": ("", "exempt", "Disables a reasoning-mode <select>."),
    "onLogitBiasPresetChange": ("", "exempt", "Logit-bias preset selection (DOM + i18n)."),
    "createNewLogitBiasEntry": ("", "exempt", "Adds a bias entry to the in-page preset object."),
    "createLogitBiasListItem": ("", "exempt", "Renders one bias row."),
    "createNewLogitBiasPreset": ("", "exempt", "Popup-driven new bias preset."),
    "addLogitBiasPresetOption": ("", "exempt", "Appends an <option> to the bias-preset select."),
    "onImportPresetClick": ("", "exempt", "Triggers a hidden file input's click."),
    "onLogitBiasPresetImportClick": ("", "exempt", "Triggers a hidden file input's click."),
    "onPresetImportFileChange": (
        "",
        "exempt",
        "Reads a browser File, then POSTs the preset to /api/presets/import; the "
        "plugin imports cards and world books by message attachment instead.",
    ),
    "onExportPresetClick": ("", "exempt", "Browser download of the ST preset JSON."),
    "onLogitBiasPresetImportFileChange": ("", "exempt", "Browser File -> bias preset."),
    "onLogitBiasPresetExportClick": ("", "exempt", "Browser download of a bias preset."),
    "onDeletePresetClick": ("", "exempt", "Popup confirm + /api/presets/delete."),
    "onLogitBiasPresetDeleteClick": ("", "exempt", "Popup confirm + DOM option removal."),
    "getPresetApplicationPromise": (
        "",
        "exempt",
        "Returns the page's in-flight preset-application promise; there is no preset "
        "application lifecycle in the port to wait on.",
    ),
    "onSettingsPresetChange": (
        "",
        "exempt",
        "Reads the selected preset from the settings dropdown and applies it in the "
        "page; the port resolves its preset per turn (PluginCore.preset_for).",
    ),
    "onModelChange": (
        "",
        "exempt",
        "547 lines of model-dropdown change handling (per-source model selects, "
        "context sizes, visibility toggles).",
    ),
    "onNewPresetClick": ("", "exempt", "Popup + saveOpenAIPreset."),
    "onReverseProxyInput": ("", "exempt", "Reads a proxy text input and saves settings."),
    "onConnectButtonClick": (
        "",
        "exempt",
        "Reads API keys out of the page and writes them to the browser secret store.",
    ),
    "toggleChatCompletionForms": ("", "exempt", "Shows/hides the per-source settings forms."),
    "testApiConnection": (
        "",
        "exempt",
        "Clicks the connect button and toasts the outcome; the port surfaces provider "
        "failures through BackendError.",
    ),
    "reconnectOpenAi": ("", "exempt", "Sets the online-status dot and clicks the API button."),
    "onProxyAccessKeyShowClick": ("", "exempt", "Toggles a masked-secret input."),
    "onCustomizeParametersClick": (
        "",
        "exempt",
        "Popup form for raw custom-endpoint body/header parameters; request-body "
        "concerns belong to AstrBot's provider config.",
    ),
    "isImageInliningSupported": (
        "",
        "exempt",
        "135 lines of per-provider/per-model capability detection for image inlining; "
        "media inlining is out of scope by decision (prompt_build.py module docstring).",
    ),
    "isVideoInliningSupported": (
        "",
        "exempt",
        "Video inlining capability detection, out of scope.",
    ),
    "isAudioInliningSupported": (
        "",
        "exempt",
        "Audio inlining capability detection, out of scope.",
    ),
    "getToolReasoningMode": ("", "exempt", "Tool-call reasoning mode, out of scope."),
    "getEffectiveToolReasoningMode": (
        "",
        "exempt",
        "show_thoughts gating for the same tool-reasoning mode; out of scope.",
    ),
    "isReasoningSignatureSupported": (
        "",
        "exempt",
        "Reasoning-signature support check for the request body; out of scope by "
        "decision (prompt_build.py module docstring).",
    ),
    "onProxyPresetChange": ("", "exempt", "Proxy dropdown change handler."),
    "runProxyCallback": (
        "",
        "exempt",
        "`/proxy` slash-command autocomplete callback (Fuse + DOM).",
    ),
    "onVertexAIAuthModeChange": ("", "exempt", "Vertex AI auth-mode form toggling."),
    "onVertexAIValidateServiceAccount": (
        "",
        "exempt",
        "Validates a service-account JSON from a textarea and writes it to the "
        "browser secret store; AstrBot owns provider credentials.",
    ),
    "onVertexAIClearServiceAccount": (
        "",
        "exempt",
        "Clears the service account from the secret store.",
    ),
    "onVertexAIServiceAccountJsonChange": (
        "",
        "exempt",
        "Textarea input validation + status text.",
    ),
    "updateVertexAIServiceAccountStatus": ("", "exempt", "Service-account status badge text."),
    "updateFeatureSupportFlags": (
        "",
        "exempt",
        "Publishes inlining/tool-calling capability flags to the page.",
    ),
    "initOpenAI": (
        "",
        "exempt",
        "640 lines of page bootstrap: jQuery/template wiring, dropdowns, slash-command "
        "registration and settings-form handlers. The plugin's own command surface is "
        "tavern/main.py::_register_commands.",
    ),
}


#: js_name -> (python_target, status, note), for ``PromptManager.js`` (S2).
#:
#: The probe only carries two rows -- the file's two *exports*. ``Prompt`` and
#: ``PromptManager`` are module-private and are therefore not rows here; their
#: behaviour is tracked through the functions that use them (``getPromptPosition``
#: / ``getPromptRole`` / ``populateChatCompletion`` in :data:`OPENAI_TABLE`, and
#: ``promptManager.preparePrompt`` / ``isPromptDisabledForActiveCharacter`` /
#: ``isValidName`` / ``sanitizeName``, which the port reaches through
#: :func:`tavern.st.prompt_build.prepare_prompts_for_chat_completion`).
PROMPT_MANAGER_TABLE: dict[str, tuple[str, str, str]] = {
    "PromptCollection": (
        "tavern/st/prompt_build.py:PromptCollection",
        "ported",
        "add/get/index/has/set/override/checkPromptInstance are mirrored at "
        "prompt_build.py:495-547, including override() recording the identifier. Two "
        "deliberate differences: checkPromptInstance becomes as_prompt() coercion "
        "(the port accepts mappings and objects, not just Prompt instances), and "
        "copy() has no JS counterpart -- it exists because the reference rebuilds the "
        "collection from the saved configuration on every call.",
    ),
    "debouncePromise": (
        "",
        "pending",
        "Module-private (not exported) promise debouncer. The port has an equivalent "
        "of neither half: no call is debounced, and there is no promise-returning "
        "debounce helper anywhere under tavern/. Its only reference caller is "
        "setupChatCompletionPromptManager (exempt), so this is cheap but real: rapid "
        "preset/binding changes re-assemble without any coalescing or write "
        "de-duplication.",
    ),
}


#: Which table belongs to which probe.
TABLES: dict[str, dict[str, tuple[str, str, str]]] = {
    "dep_world-info.json": WORLD_INFO_TABLE,
    "dep_prompt-converters.json": CONVERTERS_TABLE,
    "dep_openai.json": OPENAI_TABLE,
    "dep_PromptManager.json": PROMPT_MANAGER_TABLE,
}


def main() -> int:
    # Every dep_* probe on disk must be claimed by PROBE_SOURCES, and every probe
    # this script knows about must be committed, or a source could silently enter
    # or leave the single source of truth.
    found = {path.name for path in RAW.glob("dep_*.json")}
    unclaimed = sorted(found - set(PROBE_SOURCES))
    if unclaimed:
        print("probes with no PROBE_SOURCES entry:", ", ".join(unclaimed), file=sys.stderr)
        return 2
    missing = sorted(set(PROBE_SOURCES) - found)
    if missing:
        print(
            "PROBE_SOURCES entries with no probe on disk: "
            + ", ".join(missing)
            + " (the map never derives a probe at run time: generate it and commit it)",
            file=sys.stderr,
        )
        return 2

    all_rows: list[dict] = []
    summary: dict[str, dict[str, int]] = {}

    for probe_name, table in TABLES.items():
        probe_path = RAW / probe_name
        probe = json.loads(probe_path.read_text(encoding="utf-8"))

        names = [item["name"] for item in probe]
        unknown = sorted(set(names) - set(table))
        if unknown:
            print(
                f"{probe_name}: probe has entries missing from the table: {', '.join(unknown)}",
                file=sys.stderr,
            )
            return 2
        unused = sorted(set(table) - set(names))
        if unused:
            print(
                f"{probe_name}: table lists functions absent from the probe: {', '.join(unused)}",
                file=sys.stderr,
            )
            return 2

        src = PROBE_SOURCES[probe_name]
        verdicts: dict[str, tuple[str, str, str]] = {
            item["name"]: (
                table[item["name"]][0],
                table[item["name"]][1],
                table[item["name"]][2] if len(table[item["name"]]) > 2 else "",
            )
            for item in probe
        }

        for item in probe:
            python_target, status, note = verdicts[item["name"]]
            all_rows.append(
                {
                    "js_source": f"src/{src}" if src == "prompt-converters.js" else src,
                    "js_name": item["name"],
                    "js_line": item["line"],
                    "loc": item["loc"],
                    "python_target": python_target,
                    "status": status,
                    "note": note,
                }
            )
        summary[probe_name] = {
            status: sum(1 for item in probe if verdicts[item["name"]][1] == status)
            for status in ("ported", "pending", "exempt")
        }

    TARGET.write_text(json.dumps(all_rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    counts = {
        status: sum(1 for row in all_rows if row["status"] == status)
        for status in ("ported", "pending", "exempt")
    }
    print(f"wrote {TARGET.relative_to(REPO)}: {len(all_rows)} rows")
    for probe_name, per_status in summary.items():
        rendered = " ".join(f"{status}={count}" for status, count in per_status.items())
        print(f"  {PROBE_NOTE.get(probe_name, probe_name):<24} {rendered}")
    print("  " + " ".join(f"{status}={count}" for status, count in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
