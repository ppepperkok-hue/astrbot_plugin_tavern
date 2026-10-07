# S2 status — prompt assembly

Updated: 2026-10-07. Source: `research/_raw/st-src/openai.js` (SillyTavern 1.19.0,
commit `06bde939`). Plan: `research/08-s2-prompt-brief.md`.

## What is done and verified

| Layer | File | Verification |
|---|---|---|
| Message model | `tavern/st/chat_completion.py` | `tools/st-oracle/diff_prompt.py` — fixture `01-message-model` **matches** the real `openai.js` under Node, field by field; 30 offline tests in `tests/test_chat_completion.py` |
| Injection / history / examples / assembly | `tavern/st/prompt_build.py` | identifiers **match** the reference in `tools/st-oracle/diff_assembly.py` (fixture `assembly-01-order`); the chat *contents* still differ (see below) |
| Oracle harness | `gen_adapter.py` (builds `openai.js` too, with `ENGINE_EXTRA_EXPORTS` / `ENGINE_EXTRA_CODE` hooks), `run_prompt.mjs`, `run_prompt_python.py`, `diff_prompt.py`, `run_assembly.mjs`, `run_assembly_python.py`, `diff_assembly.py` | `diff_prompt.py --all` PASS |

## The three reference behaviours the port had to reproduce (and had wrong)

1. **`ChatCompletion.add` assigns a slot, it does not insert one.**
   `openai.js:4002-4006` is `this.messages.collection[position] = collection`.
   An index **replaces** whatever occupies it and pads the array when it is past
   the end (a sparse array, i.e. holes that `getChat` skips). Inserting instead
   shifts every following prompt and duplicates entries — that was the
   `worldInfoBefore`/`bias`/`enhanceDefinitions` duplication and the double `main`
   the assembly oracle flagged. `tavern/st/chat_completion.py` now assigns and
   pads with `None`; every lookup and `get_tokens` skips `None`.
2. **`system_prompt === false` marks a *user-relative* prompt** (`openai.js:1242-1243`).
   The helper was named `_is_system_prompt` and returned `True` exactly for those
   prompts, and the caller negated it, so the filter was inverted and every
   ordinary prompt was pulled into the user-relative loop. Renamed to
   `_is_user_relative_prompt` so the name cannot be read backwards.
3. **The squash exclude list is a barrier, not a skip** (`openai.js:3935-3950`):
   a named system message is not merged and does not merge with its neighbours,
   and an empty system message is dropped. Pinned by
   `tests/test_chat_completion.py::test_squash_does_not_merge_across_a_named_system_message`.

## What is still open

| # | Item | Evidence |
|---|---|---|
| 1 | **Chat history is not populated by the reference in the oracle run.** The reference leaves the `chatHistory` slots as `None` holes while the port fills them with the fixture turns (and reverses them). The identifier sequence matches, so this is a harness/semantics question first: either the reference needs `messages` handed in differently, or `populateChatHistory` is gated on something the harness does not provide. Settle it before "fixing" the port. | `diff_assembly.py --verbose`, step[0].chat |
| 2 | **New-chat prompt string.** The reference reads `oai_settings.new_chat_prompt` / `new_group_chat_prompt` and the harness cannot yet force the non-group branch (`selected_group` is bound to the `group-chats.js` stub). The fixture passes the expected strings through `oracleSetPrompts`; verify that hook actually takes effect. | same step |
| 3 | `overridden_prompts` never reaches the completion: reference `['main','jailbreak']` vs port `[]`. `PromptCollection.set/override` ↔ `ChatCompletion.set_overridden_prompts` is not wired. | same step |
| 4 | `population_injection_prompts` needs a default for the injection depth upper bound when no `get_extension_prompt_max_depth` callback is supplied (the reference gets it from the page). | `tests/test_prompt_build.py::test_injection_depths_and_the_total_inserted_offset` |
| 5 | `names_behavior = COMPLETION` is not applied (the NONE/DEFAULT cases pass, so the branch itself is missing). | `...::test_history_names_behavior_completion_sanitizes_invalid_names` |
| 6 | `tests/test_prompt_build.py` has its own defects and must not be used to "fix" the port: its `FakeChatCompletion.insert` models an `updateInsertionIndices` / anchor scheme that does not exist in the 1.19.0 snapshot, five history cases expect `newMainChat` at the *end* of the group while `openai.js:1077-1079` `insertAtStart`s it to the **front**, one case forgets `await` on `squash_system_messages`, and `test_assembly_order_entry_by_entry` expects `main` first where the reference (and the oracle) put `worldInfoBefore` first. Port the fixes the oracle proves, not the ones that test asks for. | 15 failing cases in that file |

## Reproduce

```
python tools/st-oracle/gen_adapter.py            # build the Node shim tree
python tools/st-oracle/diff.py --all             # S1: world info engine
python tools/st-oracle/diff_prompt.py --all      # S2: message model
python tools/st-oracle/diff_assembly.py --all --verbose   # S2: assembly order
python -m pytest tests -q                        # offline suite
```

`diff_prompt.py` covers `fixtures/prompt/*.json` except `assembly-*`, which
`diff_assembly.py` owns; the two fixture families drive different runners.
