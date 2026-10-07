# S2 status — prompt assembly

> ## Repair state of the assembly oracle
>
> The harness was lost (an uncommitted `run_assembly.mjs` plus the `gen_adapter.py`
> additions were overwritten by a `git checkout` of mine) and has been rebuilt. The
> port itself was never affected.
>
> **Running again.** `gen_adapter.py` emits the real `Prompt` / `PromptCollection`
> / `INJECTION_POSITION` (class bodies brace-matched out of the vendored snapshot,
> plus the `DEFAULT_DEPTH` / `DEFAULT_ORDER` they read) and real
> `getExtensionPrompt` / `getExtensionPromptMaxDepth` values, all declared through
> `PROVIDED_BY_SOURCE` / `OVERRIDES`; `run_assembly.mjs` no longer patches the
> build tree. `diff_assembly.py --all` now reports **1 match, 7 diverged, 8
> divergences** (it was `node-error` for all eight before).
>
> **Five harness defects were found and fixed on the way**, every one of them a
> false divergence the port was being blamed for:
> 1. `PromptManager.js` rendered as a stub, so `new Prompt(chatPrompt)` produced a
>    proxy and every turn lost its role and content.
> 2. `PromptCollection.override(prompt, position)` takes a **Prompt**, not an
>    identifier (`PromptManager.js:294-297`).
> 3. `preparePrompt` returning the prompt unchanged left a turn's body empty; a
>    chat turn carries it in `mes`, so that is mapped onto `content`.
> 4. `Message.fromPromptAsync` (3792-3794) dereferences its argument immediately,
>    so an absent optional prompt (`impersonate`, `quietPrompt`) threw and aborted
>    the whole assembly. The harness now returns null for a missing prompt, which
>    is what the browser's always-present prompts amount to.
> 5. The fixture spells a turn body `mes`; the runner read `turn.content`.
>
> **What is left (1 divergence on 7 of the 8 fixtures).** The chat turns come out
> **in the opposite order**: the reference gives
> `[assistant 'I was say…', user 'hello there']`, the port gives
> `[user 'hello there', assistant 'I was say…']`. `assembly-05-disabled-prompts`
> already matches, so the loop itself is right; the difference is the order of the
> `messages` array the port is handed. `populateChatHistory` reverses the list and
> prepends (`openai.js:945-948` + `:1071`), so the reference's input must be
> oldest-first while the port's is newest-first (or the reverse) — check how
> `run_assembly_python.py` and `tests/test_prompt_build.py` build `messages`
> against `setOpenAIMessages` (openai.js:644) before touching `prompt_build.py`.
> `assembly-08` additionally still differs on the `continueNudge` entry, which is
> the same ordering question reached through the `type: "continue"` path.
>
> **Do not quote the old "8 match" as current.** The result in §1 was produced by
> the pre-loss harness; the current numbers are the 1/7 above.
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

## 1. What the pre-loss harness verified (must be re-derived)

| Command | Result |
|---|---|
| `python tools/st-oracle/diff_assembly.py --all` | 8 fixtures, **8 match**, 0 diverged, 0 not-comparable |
| `python tools/st-oracle/diff_prompt.py --all` | 01-message-model match — PASS |
| `python -m pytest tests -q` | **458 passed, 1 skipped** (the skip is `tests/test_prompt.py:151`, no timezone database) |
| `python -m ruff check .` / `ruff format --check tavern tests tools main.py` | clean / 54 files formatted |
| `python tools/st-oracle/gen_adapter.py --check` | adapter up to date |

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

1. **The assembly harness must be finished** (see the box at the top). Everything
   in §1 has to be re-derived once it runs.
2. Group chats: `selected_group` is bound to the generated `group-chats.js` stub
   and is always truthy, so the `groupNudge` branch (900-903, 1082-1085) has no
   assembly fixture. It does have offline unit tests. `setOpenAIMessages`'
   DEFAULT/CONTENT name prefixing (595-612) is upstream of this port and not
   covered.
3. Token counting is the port's estimate, not the browser tokenizer — the
   documented non-comparable point. Both sides use the same rule, so the budget
   *order* is comparable while the absolute counts are not.
4. `squashSystemMessages` is not part of this comparison (the page runs it after
   `populateChatCompletion`, `openai.js:1608`); it is covered by
   `diff_prompt.py` and `tests/test_chat_completion.py`.
5. `preparePromptsForChatCompletion` has offline tests only — the assembly
   fixtures hand the collection straight to `populateChatCompletion`, which is
   what `prepareOpenAIMessages` does, so the Node side never calls it directly.
6. Not covered at all: streaming, tool calls, media inlining, reasoning
   signatures, logit bias.
7. `Prompt.marker` (`PromptManager.js:159-163`) is never assigned by the real
   constructor (182-195); the field is kept for parity and nothing reads it.

## 6. Reproduce

```
python tools/st-oracle/gen_adapter.py     # build the Node shim tree
python tools/st-oracle/diff.py --all      # S1: world info engine   -> PASS
python tools/st-oracle/diff_prompt.py --all   # S2: message model   -> PASS
python tools/st-oracle/diff_assembly.py --all # S2: assembly order  -> FAIL, harness being repaired
python -m pytest tests -q                 # 458 passed, 1 skipped
```

`diff_prompt.py` covers `fixtures/prompt/*.json` except `assembly-*`, which
`diff_assembly.py` owns; the two families drive different runners.
