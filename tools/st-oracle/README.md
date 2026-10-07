# st-oracle — headless consistency judge for the port

Same input, two engines: SillyTavern's real JavaScript running under Node, and the
Python port under `tavern/`. Every fixture is a JSON file that both sides consume,
and every divergence is printed field by field. This is the acceptance gate for the
whole "酒馆引擎移植" effort — if the diff prints a difference, the port is not done
for that behaviour.

The Oracle does not modify `tavern/`: it only reads it. A fixture is never marked
passing by editing the port.

Three families of fixtures are driven by three runners, one per porting step:

| step | Node runner | Python runner | fixtures | diff |
| --- | --- | --- | --- | --- |
| S1 World Info | `run.mjs` | `run_python.py` | `fixtures/*.json` (14) | `diff.py` |
| S2 message model | `run_prompt.mjs` | `run_prompt_python.py` | `fixtures/prompt/01-message-model.json` | `diff_prompt.py` |
| S2 assembly | `run_assembly.mjs` | `run_assembly_python.py` | `fixtures/prompt/assembly-*.json` (8) | `diff_assembly.py` |

## Commands

Requires **Node ≥ 18** (developed and pinned against `node v22.22.1`) and any
Python 3.10+ (developed on `python 3.11.9`); standard library only, no pip
installs, no network.

```bash
python tools/st-oracle/gen_adapter.py          # 1. build the Node shim tree in .build/ (from the vendored snapshot)
python tools/st-oracle/diff.py --all           # 2. S1: run BOTH engines over every world-info fixture, then diff
python tools/st-oracle/diff_prompt.py --all    #    S2: message model
python tools/st-oracle/diff_assembly.py --all  #    S2: assembly order
python tools/st-oracle/gen_adapter.py --check  #    does .build/ still match the generator?
```

Each `diff_*.py --all` runs its runners itself, so it can never compare stale
output, and exits 0 when every comparable fixture matched. `run_all.py` does the
same for S1 but also prints per-fixture timings.

To drive one side only:

```bash
node   tools/st-oracle/run.mjs       tools/st-oracle/fixtures/03-selective-logic.json --out tools/st-oracle/out/03-selective-logic.json
python tools/st-oracle/run_python.py tools/st-oracle/fixtures/03-selective-logic.json --out tools/st-oracle/out/03-selective-logic.python.json
python tools/st-oracle/diff.py --all --only port --require js,port
```

**Current verdict: all three families PASS.** S1 is 14 fixtures / 11 match /
0 diverged / 3 not-comparable (the three genuinely incomparable points are listed in
`STATUS.md`); S2 message model is 1/1; S2 assembly is 8/8. Counts and the repair
history live in `STATUS.md` (S1) and `STATUS-S2.md` (S2) — check those before
quoting any number, because this file deliberately does not repeat them.

## Layout

| path | what it is |
| --- | --- |
| `gen_adapter.py` | generates `.build/` (stub modules + byte copies of the engine files) |
| `runtime.mjs` | the Proxy stub, the seeded PRNG and the shared token counter |
| `run.mjs` / `run_python.py` | S1: the engine and `tavern/st/worldbook.py` over one fixture, same JSON shape |
| `run_prompt.mjs` / `run_prompt_python.py` | S2: the message model |
| `run_assembly.mjs` / `run_assembly_python.py` | S2: `populateChatCompletion`, the assembly order |
| `gen_fixtures.py` | regenerates the S1 fixtures (the fixtures are checked in) |
| `diff.py` / `diff_prompt.py` / `diff_assembly.py` | field-by-field comparison, human output, meaningful exit code |
| `run_all.py` | runs S1 end to end, reports timings, then calls `diff.py` |
| `gen_port_map.py` | regenerates `port-map.json` from the dependency probe |
| `port-map.json` | **the single source of truth for porting progress** (the 81 probe entries) |
| `STATUS.md` / `STATUS-S2.md` / `STATUS-S4.md` | current counts, how to use it, known non-comparable points |
| `check_converter_coverage.py` | parses the reference's export list and fails unless every export resolves on the port |
| `check_module_wiring.py` | reports which `tavern/st/wi_*.py` mirror modules nothing in production imports |
| `run_error_message.mjs` | probes `getChatCompletionErrorMessage` (module-private) for the test table |
| `.build/` | generated, git-ignored |
| `out/` | generated results, git-ignored |

## Two checks that are not diffs

`check_converter_coverage.py` and `check_module_wiring.py` exist because a diff can
only judge behaviour that actually runs:

* the coverage check closes the gap where "no fixture calls that export" is
  indistinguishable from "that export is missing";
* the wiring check closes the gap where a mirror module with green tests looks
  finished while nothing imports it, so it cannot affect a single reply. Three of
  them are in that state today (`wi_keywords`, `wi_scan_state`, `wi_timed`); the
  check reports rather than fails, because keeping one as a reference
  implementation is a legitimate choice -- inventing `KNOWN_UNWIRED` entries
  silently is not.

When a fixture and the real engine disagree, the fixture is the suspect. That
lesson cost this repository six rounds on the S2 assembly harness (see
`STATUS-S2.md`), and half a round again on the converter runner.

## Provenance of the engine under test

* SillyTavern snapshot directory: `research/_raw/st-src/` (see `PROVENANCE.md`
  there for how each file was fetched and verified)
* engine file: `research/_raw/st-src/world-info.js`
* `st_package.json` says `sillytavern 1.19.0`, AGPL-3.0
* byte-identical to `public/scripts/world-info.js` at tag `1.19.0`
  `06bde939fb1e9c4c8d8641d810f0a916b5bce127`
  (`sha256 111c7f47945839e75b021e09bdbb112a54f0a9b7857d2b95435f573efc7cd9a5`,
  265081 bytes) — verified with `gh api .../contents/...` and a local sha256 compare.
* `gen_adapter.py` copies the engine files verbatim; it never edits them. The
  assembly oracle additionally extracts `Prompt`, `PromptCollection` and
  `INJECTION_POSITION` out of `PromptManager.js` by brace matching, which is why
  that one file is transformed rather than copied.

## Adding a fixture

1. add a case to `gen_fixtures.py` and run it (or drop a hand-written JSON file in
   `fixtures/` — the S2 fixtures are all hand-written),
2. run the matching `diff_*.py --all`,
3. read the new divergences — each one is either a porting task (add it to
   `port-map.json`) or a documented non-comparable point (see `STATUS.md`).

The S1 fixture schema is documented at the top of `gen_fixtures.py` and in the
comments of `run.mjs`; the S2 fixture shapes are documented in `run_assembly.mjs`
and `run_assembly_python.py`. Keep the two runners of a family in sync if you
extend its schema — and remember that a divergence is at least as likely to be a
runner defect as a port defect: see `STATUS-S2.md`, where fourteen consecutive
"port bugs" turned out to be harness bugs.
