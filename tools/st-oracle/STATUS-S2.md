# S2 status — prompt assembly

> ## Repair state of the assembly oracle: **repair complete**
>
> The harness was lost (an uncommitted `run_assembly.mjs` plus the `gen_adapter.py`
> additions were overwritten by a `git checkout` of mine) and has been rebuilt and
> finished. The port itself was never affected.
>
> **Current numbers** (`python tools/st-oracle/diff_assembly.py --all`):
> **8 fixtures, 8 match, 0 diverged, 0 not-comparable, 0 divergences** — from
> `node-error` on all eight, via 3/5/6 and 4/4/5.
>
> **No port change was needed.** The whole of the remaining divergence was the
> harness. `04-continue-prefill` was the one the handoff expected to be a real
> port difference (`openai.js:1318-1331` gates `assistant_prefill` on the
> completion source); the port already does exactly that
> (`prompt_build.py:1194-1209`), and once the reference could run to the end the
> two agreed.
>
> **Harness defects found and fixed** — every one of them a false divergence the
> port was being blamed for. The first eight are the ones the rebuild had already
> closed:
> 1. `PromptManager.js` rendered as a stub, so `new Prompt(chatPrompt)` produced a
>    proxy and every turn lost its role and content.
> 2. `PromptCollection.override(prompt, position)` takes a **Prompt**, not an
>    identifier (`PromptManager.js:294-297`).
> 3. `preparePrompt` returning the prompt unchanged left a turn's body empty; a
>    chat turn carries it in `mes`, so that is mapped onto `content`.
> 4. `Message.fromPromptAsync` (3792-3794) dereferences its argument immediately,
>    so an absent optional prompt (`impersonate`, `quietPrompt`) threw and aborted
>    the whole assembly. A missing prompt now yields null, which is what the
>    browser's always-present prompts amount to.
> 5. The fixture spells a turn body `mes`; the runner read `turn.content`.
> 6. The fixture lists chat turns oldest-first, but `setOpenAIMessages`
>    (openai.js:570-649, driven by `script.js:4830`) hands them over **newest
>    first**; the runner now reverses, like the Python side's `set_openai_messages`.
> 7. `messageExamples` are **blocks of messages**, and a block entry carries its
>    text in `content`; the runner was reading a flat `example.content`.
> 8. `isValidName` / `sanitizeName` were approximated; they are now the literal
>    `^[a-zA-Z0-9_]{1,64}$` and `[^a-zA-Z0-9_] -> _` plus a 64-char cut
>    (`PromptManager.js:1343-1351`).
>
> The six closed after the rebuild:
> 9. **The optional system prompts were never in the collection.** Four entries of
>    `preparePromptsForChatCompletion` (openai.js:1374-1493) are not fixture inputs
>    -- `impersonate` (1382), `quietPrompt` (1383), `bias` (1385),
>    `enhanceDefinitions` (2049) -- and a fixture declares only what it exercises.
>    `populateChatCompletion` reads the first two with a bare `prompts.get()`
>    (1224/1229), so the *reference itself* threw
>    `TypeError: Cannot read properties of undefined (reading 'role')` right after
>    `main`, and the truncated snapshot looked like the port emitting more.
>    `OPTIONAL_SYSTEM_PROMPTS` now appends the absent two, with empty content,
>    which is what a browser prompt holds unless the user typed something.
>    **This was the whole of fixtures 01/03/04/07/08.**
> 10. **No `cyclePrompt` was passed at all**, so the `continueNudge` branch
>    (907-927) never ran on the reference side while the port's did. Fixture 08.
> 11. **`new_example_chat_prompt` was being set to `'[Start a new Chat]'`.** The
>    harness copied the new-chat string onto the example banner, changing the
>    banner's token count and letting the 31-token budget in 03/07 drop a group
>    the reference keeps. `oracleSetPrompts` now takes the four banner strings
>    independently (`openai.js:108-111`), and a fixture may override any of them.
> 12. **`Message.createAsync` was left on the stub tokenizer**, so the dialogue
>    examples were built with 0 tokens and, because the engine also fills the
>    content there, empty text. The harness counted in `fromPromptAsync` and
>    `setName` but not in `createAsync` (3558-3566).
> 13. **The example body was passed as `mes`**, but
>    `populateDialogueExamples` reads `content` (1116); the reference's entries
>    came out empty and `getChat()` dropped them (4125).
> 14. **The token counter was the engine's stub, a different order of magnitude
>    from the port's.** The harness now bills the port's rule verbatim
>    (`chat_completion.py` `count_tokens`: non-empty parts joined by one space,
>    `len // 3`, floor 1) at every count site. Without this the two "budget"
>    fixtures compared a 31-token ceiling under two different currencies and could
>    not have agreed on which group to drop.
>
> **Also added:** `run_assembly.mjs` now publishes `comparable.chat.ok` (with a
> reason) for the first time. `diff_assembly.py` had been reading that key since
> the rebuild but nothing ever wrote it, so the guard was dead: a reference that
> threw partway was compared against a full port run and the port was blamed.
> It is exercised — with a stand-in disabled the reference throws and the key
> comes back `false` with the TypeError in the reason.
>
> **Not affected:** `tavern/st/chat_completion.py`, `tavern/st/prompt_build.py`,
> `tests/test_prompt_build.py` (`458 passed, 1 skipped`, ruff clean); the S1 oracle
> (`diff.py --all` → 14 fixtures, 11 match, 0 diverged, 3 not-comparable); the
> message-model oracle (`diff_prompt.py --all` → PASS).

Source of truth: `research/_raw/st-src/openai.js` +
`research/_raw/st-src/PromptManager.js` (SillyTavern 1.19.0, commit
`06bde939fb1e9c4c8d8641d810f0a9165bce127`, byte identical to tag `1.19.0`). Plan
and source map: `research/08-s2-prompt-brief.md`. Line numbers below are measured
against the snapshot in this repo, not copied from the brief.

## 1. What the rebuilt harness verifies (re-derived)

| Command | Result |
|---|---|
| `python tools/st-oracle/diff_assembly.py --all` | 8 fixtures, **8 match**, 0 diverged, 0 not-comparable |
| `python tools/st-oracle/diff_prompt.py --all` | 01-message-model match — PASS |
| `python -m pytest tests -q` | **458 passed, 1 skipped** (the skip is `tests/test_prompt.py:151`, no timezone database) |
| `python -m ruff check .` / `ruff format --check tavern tests tools main.py` | clean / formatted |
| `python tools/st-oracle/gen_adapter.py --check` | adapter up to date |
| `python tools/check.py` | ALL CHECKS PASSED |

The pre-loss run claimed the same 8 match; the rebuilt harness reaches it by a
different route (§0), so the agreement is a re-derivation rather than a restored
number.

The eight assembly fixtures are `assembly-01-order`, `02-injection-depths`,
`03-pin-examples`, `04-continue-prefill`, `05-disabled-prompts`,
`06-history-names`, `07-examples-budget`, `08-continue-nudge`. Each is compared
on the identifier sequence, every message's `(role, content, name)` and the
overridden-prompt list.

## 2. Per-function source map (measured)

| JS (line range) | Python | Handling |
|---|---|---|
| `formatWorldInfo` 789-801 | `format_world_info` | copied |
| `populationInjectionPrompts` 810-875 | `population_injection_prompts` | copied |
| `populateChatHistory` 885-1092 | `populate_chat_history` | copied; media/tool/reasoning inlining and the capability probes are out of scope |
| `populateDialogueExamples` 1101-1134 | `populate_dialogue_examples` | copied |
| `getPromptPosition` 1140-1150 / `getPromptRole` 1157-1168 | `get_prompt_position` / `get_prompt_role` | copied |
| `populateChatCompletion` 1185-1347 | `populate_chat_completion` | copied order; the browser lookups became injected callbacks |
| `preparePromptsForChatCompletion` 1367-1516 | `prepare_prompts_for_chat_completion` | copied; the nine system blocks get an explicit `system_prompt=True` |
| `PromptManager.js` `Prompt` 80-196 / `PromptCollection` 201-298 | `Prompt` / `PromptCollection` | copied projection (`get`/`index`/`has`/`add`/`set`/`override`) |
| `PromptManager.js` `isValidName` 1343-1347 / `sanitizeName` 1349-1351 | `is_valid_name` / `sanitize_name` | copied regex |
| `Message.fromPromptAsync` 3792-3794 | `_message_from_prompt` | copied |
| `ChatCompletion.*` 3917-4268 | `tavern/st/chat_completion.py` | Lead's file, verified by `diff_prompt.py` |

## 3. Copied vs rewritten

Copied line by line: everything marked copied above, including the literals that
look arbitrary (the `100` order default, the `-1` `NONE`, the role order,
`+b - +a` for the descending sort).

Rewritten because the browser cannot come along, all through injected parameters:
`oai_settings` / `power_user` / `chat_metadata` → `settings` (a mapping or an
object, read through `_setting`); the `promptManager` singleton → the
`prepare_prompt` / `is_prompt_disabled_for_active_character` / `is_valid_name` /
`sanitize_name` callbacks; `getExtensionPrompt` / `getExtensionPromptMaxDepth` →
callbacks; `await` on a non-promise → `_maybe_await`;
`Message.fromPromptAsync` / `createAsync` → `message_factory`; `continue_postfix`
(the page appends it at `script.js:4978-4979`) → `apply_continue_postfix`.

Out of scope, deliberately: DOM / jQuery / i18n / toastr / the slash commands and
the prompt-manager UI, streaming, tool calls, image/video/audio inlining,
reasoning signatures, `quiet_image`, logit bias beyond the `bias` prompt block,
and the real tokenizer.

## 4. Reference behaviours worth knowing

* `ChatCompletion.add(collection, index)` **assigns the slot**
  (`openai.js:4002-4006`) — it replaces whatever occupies it and pads a sparse
  array when the index is past the end. Inserting instead shifts every following
  prompt and duplicates entries; that was the original assembly divergence.
* A consequence: a collection appended to the end can be silently **overwritten**
  by a later assignment at a lower index. Fixture 08 therefore orders
  `dialogueExamples` before `chatHistory`, or the `continueNudge` collection is
  replaced. The port matches the reference here on purpose.
* `system_prompt === false` marks a **user-relative** prompt (`openai.js:1242-1243`).
  The helper is named `_is_user_relative_prompt`; the older `_is_system_prompt`
  invited the inverted condition and pulled every ordinary prompt into the
  user-relative loop.
* `squashSystemMessages` treats a named system message as a **barrier**
  (openai.js:3935-3950): it neither merges nor is merged, and an empty system
  message is dropped.
* `Message.fromPromptAsync` does **not** carry `prompt.name` onto the message;
  only `names_behavior = COMPLETION` (957-960) and the dialogue examples
  (1119-1121) call `setName`. Copying the name broke the squash predicate.
* `getExtensionPromptMaxDepth()` is literally `return MAX_INJECTION_DEPTH`
  (`script.js:500`, value 10000); the computed version at `script.js:3279-3284`
  is commented out upstream.

## 5. Still open (honest list)

1. Group chats: `selected_group` is bound to the generated `group-chats.js` stub
   and is always truthy, so the `groupNudge` branch (900-903, 1082-1085) has no
   assembly fixture. It does have offline unit tests. `setOpenAIMessages`'
   DEFAULT/CONTENT name prefixing (595-612) is upstream of this port and not
   covered.
2. Token counting is the port's estimate, not the browser tokenizer. The harness
   now bills the port's rule (`len // 3`) at every count site, so the two sides
   are comparable *within the estimate* — which is what makes the budget fixtures
   meaningful — but the absolute counts are still not the model's. The estimate
   itself is what `token_divisor` exists to stress.
3. `squashSystemMessages` is not part of this comparison (the page runs it after
   `populateChatCompletion`, `openai.js:1608`); it is covered by
   `diff_prompt.py` and `tests/test_chat_completion.py`.
4. `preparePromptsForChatCompletion` has offline tests only — the assembly
   fixtures hand the collection straight to `populateChatCompletion`, which is
   what `prepareOpenAIMessages` does, so the Node side never calls it directly.
   The two entries it contributes that the assembly reads blindly (`impersonate`,
   `quietPrompt`) are therefore supplied by `run_assembly.mjs` itself, with the
   empty content a browser prompt holds unless the user typed one.
5. Not covered at all: streaming, tool calls, media inlining, reasoning
   signatures, logit bias.
6. `Prompt.marker` (`PromptManager.js:159-163`) is never assigned by the real
   constructor (182-195); the field is kept for parity and nothing reads it.
7. `run_assembly.mjs` publishes `comparable.chat.ok`. It is `false` only when the
   reference threw; `diff_assembly.py` then reports the fixture as
   `match (partial)` with the reason, and `record.error` still diverges it, so a
   broken reference can never pass as agreement.

## 6. Reproduce

```
python tools/st-oracle/gen_adapter.py     # build the Node shim tree
python tools/st-oracle/diff.py --all      # S1: world info engine   -> PASS
python tools/st-oracle/diff_prompt.py --all   # S2: message model   -> PASS
python tools/st-oracle/diff_assembly.py --all # S2: assembly order  -> PASS
python -m pytest tests -q                 # 458 passed, 1 skipped
```

`diff_prompt.py` covers `fixtures/prompt/*.json` except `assembly-*`, which
`diff_assembly.py` owns; the two families drive different runners.
