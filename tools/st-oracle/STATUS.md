# st-oracle STATUS — S1 (World Info)

> ### This page covers S1 only, and it has been brought up to date
>
> The repository now has **three** oracle families. This file is the S1 (World
> Info) one; `STATUS-S2.md` owns the prompt-assembly judge, and `diff_prompt.py`
> reports the message model.
>
> **Current S1 verdict** (`python tools/st-oracle/diff.py --all`):
> **15 fixtures, 12 match, 0 diverged, 0 skipped, 3 not-comparable.**
>
> The 12-fixture comparison table, the "still diverge" list and its root causes in
> the next two sections **describe the port as it was before S1 was finished.**
> Every divergence listed there has since been fixed. Those sections are kept for
> the findings they encode — each one is a coupling only a run could reveal, and
> they explain why the port behaves as it does today — but they are **not**
> current state. The "不可比点" section and the `port-map.json` counts still are.

**One page: what the judge measures, what the port currently gets right, and the
places where the two engines genuinely cannot be compared.**

Everything below is produced by `python tools/st-oracle/diff.py --all` (which runs
both engines itself). Run it after every porting change; `run_all.py` adds timings.

## Environment (recorded so a rerun is meaningful)

| item | value |
| --- | --- |
| Node | `node v22.22.1` (`node --version`) |
| Python | `3.11.9` |
| SillyTavern snapshot | `research/_raw/st-src/world-info.js` — `sillytavern 1.19.0`, AGPL-3.0 |
| snapshot identity | byte-identical to `public/scripts/world-info.js` at commit (tag `1.19.0`) `06bde939fb1e9c4c8d8641d810f0a916b5bce127`, `sha256 111c7f47…cd9a5`, 265081 bytes |
| a full `diff.py --all` run | ~3.5 s wall clock (15 fixtures × 2 engines, one process each) |
| one Node scan | ~110 ms/fixture including process start + engine import (~20 ms import, ~5 ms scan) |

## Porting progress

`port-map.json` is the only source of truth; regenerate with `gen_port_map.py`
(it fails if the probe and the table drift apart). Current counts over the 81
probe entries:

| status | count | meaning |
| --- | --- | --- |
| `ported` | 26 | an implementation exists somewhere in the plugin and is reachable |
| `pending` | 24 | not ported, or only a stub; this is the backlog the diff keeps printing |
| `exempt` | 31 | UI / DOM / editor-only, reason recorded per row |

`research/07-port-map.md` is the narrative twin of this file (per-function
rationale). The two now agree on all but three rows, where this file is the more
generous one because it counts a behaviour that exists under a different name:
`splitKeywordsAndRegexes`, `onWorldInfoChange`, `createNewWorldInfo`. `ported`
means "an implementation exists", never "proven equivalent" — the note on each row
names the fixture that witnesses a remaining gap.

## Comparison result as recorded before S1 was finished

`12 fixtures | match 3 | diverged 8 | not-comparable 1 | 24 diverging fields`

**This is a historical snapshot, not the current verdict.** The live run is
`15 fixtures | match 12 | diverged 0 | not-comparable 3 | 0 diverging fields`
(PASS). The table below is the task list that was open at the time; all of it
is closed now.

The three matching fixtures (`01-constant-minimal`, `02-scan-depth-boundary`,
`09-character-book`) are the ones whose entries the port already handles exactly.
Every divergence is a porting task, not a fixture defect — the list below is the
task list.

| fixture | diverging fields | root cause |
| --- | --- | --- |
| 03-selective-logic | 2 | insertion-order reversal only (same two entries, opposite order) |
| 04-regex-keys | 3 | whole-word matching: the port matched a case-sensitive regex that the engine rejects |
| 05-position-and-depth | 1 | `worldInfoDepth` bucket ordering only (same buckets) |
| 06-order-and-probability | 3 | probability: engine rolled a pass, port's `randint(1,100)` rolled a fail |
| 07-recursion-flags | 6 | `preventRecursion` / `delayUntilRecursion` semantics (both scans) |
| 08-inclusion-groups | 3 | inclusion groups: port emitted 4 entries where the engine picks 1 per group |
| 10-decorators | 3 | decorators not parsed: `@@dont_activate` injected, `@@activate` not stripped |
| 11-sticky-cooldown-delay | 3 | `delay` measured in turns instead of chat length |
| 12-budget-overflow | (0 counted) | marked `comparable: false` — tokenizer |

## Findings that only a run could reveal (highest porting value)

These are the couplings that the SillyTavern docs do not state and that the
dependency probe cannot show. Each was verified by running the engine, not by
reading it.

1. **Insertion order is inverted, not merely "sorted".** `checkWorldInfo` walks
   the activated map in *ascending* `order` and `unshift`s each piece into the
   final array (world-info.js:5203), so `worldInfoBefore` reads **descending** by
   `order`. The port returns ascending. Two entries are enough to diverge; every
   real chat has more. This affects `worldInfoString` on every multi-entry turn.
2. **Inclusion groups always elect exactly one winner.** Group scoring off is not
   "no filtering": `filterGroupsByInclusionGroups` still runs a weighted random
   roll (`Math.random() * totalWeight`) and removes every other member. Scoring on
   only pre-removes the *lower-scoring* members (`buffer.getScore`), and
   `groupOverride` picks a priority winner instead. The port's
   `_apply_group_scoring` keeps the heavier member and otherwise emits the whole
   group, so with scoring off it injects four entries where the engine injects
   two.
3. **Group scoring uses key-match *score*, never `groupWeight`.** `groupWeight`
   only feeds the random roll. A port that "picks the heaviest entry" passes the
   weighted case by accident and fails every scored case.
4. **`preventRecursion` and `delayUntilRecursion` are control flags on the
   recursion *pass*, not filters on the entry.** `preventRecursion` (5080) only
   keeps the entry's content out of the recursion buffer — the entry itself may
   perfectly well be activated *by* a recursion pass. And `delayUntilRecursion` is
   not a boolean in the engine: `true` normalises to delay level `1`, and entries
   fire on every recursion level `>= currentRecursionDelayLevel` (4754–4762,
   4865). The port reads `delayUntilRecursion` as "only fire while recursing" and
   blocks `preventRecursion` entries from recursion outright, which inverts both.
5. **`delay` is measured in chat length, not turns** (4849, 666–677,
   672: `chat.length < entry.delay`). Runs also share one `chat_metadata` across
   scans in the same process, so timed effects leak between scans — the oracle
   documents that instead of hiding it. The port counts turns, which only agrees
   when every turn appends exactly one message.
6. **Unbounded recursion is the code path a real install takes.**
   `world_info_max_recursion_steps === 0` (the shipped default) means "no cap";
   the loop stops only when a pass activates nothing (4768). The port has no such
   mode — `activate()` always uses a finite `max_recursion_steps` — so the oracle
   substitutes 256. A port that keeps a small default (3) will under-recursive
   long chains.
7. **The token budget is cumulative and cannot be expressed for one entry.**
   `textToScanTokens` is computed from `allActivatedText` once per pass (5010) and
   `newContent` accumulates every entry already accepted in the same pass
   (5059–5061), so a later entry can overflow a budget that would have fit it
   alone. Worse, the budget check follows
   `if (token_budget_overflowed && !entry.ignoreBudget) { … break; }` (5021–5025)
   — a single overflow **breaks out of the whole remaining pass**, so entries that
   would have fit are silently dropped. The port has neither the accumulation nor
   `ignoreBudget`.
8. **Whole-word keys are matched with custom boundaries, not `\b`.** A single-token
   key becomes `(?:^|\W)(key)(?:$|\W)` (356); multi-word keys fall back to plain
   `includes`. `\b` is wrong for keys that start/end with `-`/`.` and for CJK,
   which is exactly where a naive port diverges. (Fixture 04 pins the regex-key
   case where the port currently matches too much.)
9. **SetWorldInfoSettings silently ignores and rewrites.** Unknown keys are
   dropped, `world_info_budget > 100` is migrated to 25, and the active books come
   from `settings.world_info.globalSelect` at a *different* place than the numeric
   knobs. A fixture that gets this wrong still produces a well-formed result — it
   just silently scans with engine defaults. Both runners now read the settings
   back and refuse to call such a scan meaningful (`settings_mismatched`), and
   `run.mjs` exits 3 when it happens.
10. **Score-based group filtering happens before the roll, and the roll's
    tie-break is RNG-visible.** `filterGroupsByScoring` runs first and mutates the
    group in place while it iterates, then the main loop `removeAllBut`s the roll
    winner. With equal weights the winner is decided purely by where the roll
    lands (verified across five seeds: 3 of them flip the winner), so any port
    must expose the same injectable RNG or its ties are luck.

## 不可比点 (known non-comparable points) — do not chase these

1. **Token counting / budget arithmetic.** SillyTavern counts with the generation
   backend's tokenizer (`getTokenCountAsync`); the port has `greedy_token_count`
   (`tiktoken` when installed, else `len // 3`). Budget arithmetic therefore
   diverges by construction. Fixture `12-budget-overflow` is marked
   `"comparable": false`; both sides are pinned to the same `len // 3` heuristic
   (`.build/runtime.mjs:countTokens` ↔ `run_python.py:count_tokens`) so that the
   *only* remaining difference is the modelling described in finding 7.
2. **Probability RNG streams.** The engine rolls `Math.random() * 100 <=
   probability`; the port rolls `randint(1, 100) > probability`. Both are pinned by
   seed (mulberry32 in Node, `random.Random` in Python) and are reproducible
   *per backend*, but the two streams are different sequences, so a fixture can
   legitimately show one side passing and the other failing. Fixture 06 is the
   witness; a port can only match by adopting the engine's comparison operator and
   an injectable RNG.
3. **Timed effects across scans in one process.** The engine keeps
   `chat_metadata.timedWorldInfo` for the whole process, so fixture 11's later
   scans inherit earlier effects. A fresh process is the only isolation, which is
   why `run.mjs` runs one fixture per process.
4. **Wall-clock and import timings.** `timing_ms` in the port's output is
   single-scan; the Node side reports process-wide import + scan. `diff.py`
   deliberately skips `timing*`, `settings*`, `world_info`, `worlds_loaded` and
   `port_truncated`.
5. **`outlet` positions.** `position: 7` needs `outletName` and a
   `{{outlet::name}}` macro at prompt-build time; the port has the position
   constant but no outlet rendering, and the oracle reports `outletEntries` as an
   empty object. No fixture pins it yet — add one when outlets are ported.
6. ~~**`\\x01` separator.**~~ **Fixed, and this entry was stale.** The referenced
   concern was real — a key must not match across two messages — and the port now
   does what the engine does: `wi_buffer.MATCHER` is `'\\x01'` and `JOINER` is
   `'\\n' + MATCHER`, so the haystack opens with `\\x01` and messages are separated
   by it. Two consequences are documented there: a key cannot span a message
   boundary, and a `^`-anchored regex key never matches because of the leading
   marker. No fixture pins it yet; the behaviour is exercised by every
   multi-message fixture implicitly.

7. **Whole-word matching is ASCII-class based, and that was a real bug.**
   `WorldInfoBuffer.matchKeys` compiles the boundary as
   `(?:^|\\W)(key)(?:$|\\W)` and JS `\\w` / `\\W` are ASCII-only in every mode
   (`u` included — it only adds `\\p{...}`). Python's `\\w` is Unicode-aware for
   `str` patterns, so the port's `re.UNICODE` made the neighbours of a CJK key
   count as word characters and `关键词` inside `中文关键词测试` stopped matching
   while the reference matched it. `tavern/st/wi_buffer.py` now compiles with
   `re.ASCII`, and fixture `15-word-boundaries` pins the pair: two entries per case,
   same key and message, `matchWholeWords` true and false, over `C++`, `New York`,
   `关键词`, `A-1`, `flat-earth` and `fog`. The counter-intuitive rows that fixture
   records, all confirmed against the engine rather than reasoned about: `C++`
   matches inside `abcC++def` (both neighbours are `\\W`), a multi-word key degrades
   to a plain substring search, and a CJK key matches between ideographs. Reverting
   the flag to `re.UNICODE` turns the fixture red on three fields, so it bites.

## How to use this in the porting loop

1. pick a `pending` row (or a divergence above),
2. port it in `tavern/`,
3. `python tools/st-oracle/diff.py --all` — the field count must go down,
4. update `port-map.json` via `gen_port_map.py` (status + `python_target` + note),
5. if a divergence is genuinely not comparable, mark the fixture
   `"comparable": false` **and** add the reason to this file. Never lower the
   fixture to make the port look done.
