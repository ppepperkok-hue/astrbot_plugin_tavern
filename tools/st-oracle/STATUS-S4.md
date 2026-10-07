# S4 status — provider wire-format converters

> ## Export coverage: **the guard exists, and it has been seen to fire**
>
> `diff_converters.py --all` compares only the exports a *fixture* names. An export
> nobody wrote a fixture for is indistinguishable from one that was never ported, so
> the progress meter could read "all match" while the module was still incomplete —
> the blind spot S2 paid for with fourteen phantom "port bugs" and one guard that
> was read but never written (`STATUS-S2.md`).
>
> `check_converter_coverage.py` closes it from the other side:
>
> * it **parses** the export list out of
>   `research/_raw/st-src/prompt-converters.js` — 21 named exports at 1.19.0
>   (20 callable, 1 value) — never a hardcoded list, so a reference bump that adds a
>   function turns it red, which is the point;
> * it resolves every name on `tavern/st/prompt_converters.py` with the runner's own
>   camelCase → snake_case rule (`snake_case` is imported from
>   `run_converters_python.py`, never re-spelled here) and against the same module
>   object the runner imports;
> * a `function`/`class` export must be present **and** callable; a value export
>   (`PROMPT_PROCESSING_TYPE`) must be present. Missing and non-callable are
>   different rows, one line per export;
> * **exit 0 only when all 21 resolve.**
>
> It also prints which exports **no fixture names**: the same blind spot from the
> other end, since a fixture-less export stays unmeasured after it is ported. That
> section is informational — the exit code depends on resolution only.
>
> **Current numbers**, observed `2026-10-07 20:36` while the three S4 port tasks
> (task-2/3/4) were still landing — they move as those tasks land, so treat this
> table as a snapshot and the reproduce block as the live answer:
>
> | command | result |
> | --- | --- |
> | `check_converter_coverage.py` | 21 exports: **19 resolved, 2 missing, 0 non-callable** → FAIL (missing: `cachingAtDepthForOpenRouterClaude`, `cachingSystemPromptForOpenRouter`, both task-3's) |
> | fixture coverage (same run) | 17/21 exports named by a fixture's `function` field |
> | `diff_converters.py --all` | fixtures 49, match 39, **10 diverged, 12 divergences** → FAIL (the port tasks are still landing) |
> | `python -m pytest tests` | **458 passed, 1 skipped** |
> | `python -m ruff check .` | **red at observation time, never in this task's files**: the root-level throwaway `.tmp_map_dump.py` (F401) and `tools/st-oracle/gen_port_map.py`, both being edited by other workers. `check_converter_coverage.py` and `diff_converters.py` are clean |
> | `python -m ruff format --check tavern tests tools main.py` | red: would reformat `tavern/st/prompt_converters.py` (mid-flight port work) and `tools/st-oracle/gen_port_map.py`; this task's two scripts are already formatted |
>
> **One trap when reading the diff.** `diff_converters.py` scores every status except
> `match` as "diverged", including `node-error`, so a broken `.build/` tree reads as a
> wall of divergences with a *zero* divergence count. That happened during this review
> (34 of 44 rows, 0 divergences) because `gen_adapter.py --check` regenerates
> `.build/` **in place** — `generate()` opens with `shutil.rmtree(BUILD)` — while
> another worker was running it. Every non-`match` row carrying 0 divergences is an
> infrastructure failure, not a port difference. The coverage guard never reads
> `.build/`, so its numbers are the stable ones; run the diff when nobody is
> regenerating the adapter.

## 1. It has been proven to fire, per name

A guard that has never reported a failure is not evidence of anything. It fires on
the live port right now (2 of 21 unresolved, above); to prove it is *per-name*
sensitive rather than "everything looks missing", it was pointed at scratch copies
of the port through `--port-file` (the self-test hook; the `.scratch/` tree is
git-ignored throwaway and nothing in `tavern/` was touched):

```text
> python tools/st-oracle/check_converter_coverage.py --port-file .scratch/coverage/port_complete.py
port      : .scratch/coverage/port_complete.py (loaded from file)
resolved (21/21)
missing (0/21)
non-callable (0/21)
VERDICT: PASS -- all 21 reference exports resolve on the port        (exit 0)

> python tools/st-oracle/check_converter_coverage.py --port-file .scratch/coverage/port_broken.py
port      : .scratch/coverage/port_broken.py (loaded from file)
resolved (20/21)
missing (1/21)
  MISSING     addAssistantPrefix                 expected add_assistant_prefix -- neither spelling exists on the port
non-callable (0/21)
VERDICT: FAIL -- 1 of 21 reference exports unresolved (1 missing, 0 non-callable)   (exit 1)

> python tools/st-oracle/check_converter_coverage.py --port-file .scratch/coverage/port_complete.py
resolved (21/21)
missing (0/21)
VERDICT: PASS -- all 21 reference exports resolve on the port        (exit 0)
```

`port_broken.py` is `port_complete.py` with exactly one definition renamed
(`add_assistant_prefix` → `add_assistant_prefix_renamed`); `port_complete.py` is the
real port plus generated no-op stubs for whatever it still lacks, so the clean path
(21/21, exit 0) could be exercised before the port tasks landed. A third copy,
`port_copy.py`, is an untouched copy of the port and reports exactly the same
numbers as the real module — that is what makes the scratch copies a fair stand-in.

`.scratch/coverage/guard_selftest.py` proves three more things the normal run cannot:

* the reference gaining one `export function` is reported by name
  (`MISSING convertSomethingNew`, exit 1) — the "future reference bump" case that
  parsing the snapshot instead of listing it exists for;
* `export { a, b as c }` and `export default ...` are parsed rather than silently
  dropped; a default export can never resolve by name, so it stays a red row;
* a fixture naming an export the reference does not have is called out
  (`unknown convertTypo named by 99-typo`).

## 2. What is not covered

* **Behaviour.** This proves an export *resolves*, not that it is right. Correctness
  is `diff_converters.py`'s job, and it can only reach what a fixture names — hence
  the fixture-coverage section, which is where the remaining blind spot now lives
  (the four exports still without a fixture at observation time were
  `PROMPT_PROCESSING_TYPE`, `addAssistantPrefix`, `calculateClaudeBudgetTokens` and
  `calculateGoogleBudgetTokens`; the command prints the live list).
* **Extra port-side names.** Helpers the port adds (`set_config`,
  `get_config_value`, …) are not reference exports and are not checked.
* **Nested and probe-level predicates.** `startsWithGroupName` is a value
  *returned* by `getPromptNames`, not an export; it is asserted through a fixture's
  `probe` in `run_converters_python.py`, not here.
* **The reference's own imports.** If `util.js`'s surface changes, what breaks is
  the generated Node shim, and `diff_converters.py` sees that as `node-error`.

## 3. Deliberately not wired into `tools/check.py` yet

`check.py` has to stay green while task-2/3/4 land, and this check is red until the
last export lands. Intended follow-up: once `check_converter_coverage.py` exits 0
against the real port, add it to `tools/check.py` (and to the command block in
`tools/st-oracle/README.md`) so a missing export fails the one-shot gate instead of
only the S4 review.

## 4. There is no `00-export-coverage.json` fixture, and the numbering skips 00

The runner contract calls exactly one reference export per fixture, so a fixture
cannot assert a *set* of exports without new harness logic — and a fixture whose
`function` is a new oracle-side entry point would be the harness inventing
behaviour, which is how S2 lost six rounds. A `00-` fixture would also be picked up
by `diff_converters.py --all` and read as a port error, poisoning the meter it is
supposed to protect. The check is therefore a dedicated script and the fixture
contract is unchanged.

## 5. Reproduce

```powershell
cd E:\astrbot_plugin
python tools/st-oracle/check_converter_coverage.py             # exports: resolved / missing / non-callable
python tools/st-oracle/check_converter_coverage.py --verbose   # + which fixtures cover each resolved export
python tools/st-oracle/diff_converters.py --all                # behaviour, per fixture
python -m ruff check .
python -m ruff format --check tavern tests tools main.py
python -m pytest tests
```

Run the coverage check **first**: it answers the question the diff structurally
cannot, and it needs no `.build/` tree. Do not overlap these with another worker's
`gen_adapter.py --check`, which rebuilds `.build/` in place and turns every fixture
into `node-error` for the duration.

Source of truth: `research/_raw/st-src/prompt-converters.js` (SillyTavern 1.19.0,
commit `06bde939fb1e9c4c8d8641d810f0a9165bce127`, byte identical to tag `1.19.0`).
Plan: `research/PORTING-PLAN.md`. Line numbers in the port are measured against the
snapshot in this repo, never copied from a brief.
