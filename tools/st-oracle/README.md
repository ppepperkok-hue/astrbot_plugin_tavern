# st-oracle — headless consistency judge for the World Info port

Same world book, same chat, two engines: SillyTavern's real `world-info.js` running
under Node, and `tavern/st/worldbook.py`. Every fixture is a JSON file that both
sides consume, and every divergence is printed field by field. This is the
acceptance gate for the whole "酒馆引擎移植" effort — if the diff prints a
difference, the port is not done for that behaviour.

The Oracle does not modify `tavern/`: it only reads it. A fixture is never marked
passing by editing the port.

## Three commands

Requires **Node ≥ 18** (developed and pinned against `node v22.22.1`) and any
Python 3.10+ (developed on `python 3.11.9`); standard library only, no pip
installs, no network.

```bash
python tools/st-oracle/gen_adapter.py     # 1. build the Node shim tree in .build/ (from the vendored snapshot)
python tools/st-oracle/run_all.py         # 2. run both engines over every fixture, then diff  (exit 0 = all match)
```

To do it by hand instead of all at once:

```bash
node   tools/st-oracle/run.mjs       tools/st-oracle/fixtures/03-selective-logic.json --out tools/st-oracle/out/03-selective-logic.json
python tools/st-oracle/run_python.py tools/st-oracle/fixtures/03-selective-logic.json --out tools/st-oracle/out/03-selective-logic.python.json
python tools/st-oracle/diff.py --all --require js,port
```

`diff.py` exits 0 when every comparable fixture matches, non-zero otherwise.
Currently it exits 1 on purpose: seven fixtures still diverge, and those
divergences are the porting backlog. See `STATUS.md`.

## Layout

| path | what it is |
| --- | --- |
| `gen_adapter.py` | generates `.build/` (24 stub modules + a byte copy of `world-info.js`) |
| `runtime.mjs` | the Proxy stub, the seeded PRNG and the shared token counter |
| `run.mjs` | runs the engine over one fixture, prints machine-readable JSON |
| `run_python.py` | runs `tavern/st/worldbook.py` over the same fixture, same JSON shape |
| `gen_fixtures.py` | regenerates `fixtures/*.json` (the fixtures are checked in) |
| `diff.py` | field-by-field comparison, human output, meaningful exit code |
| `run_all.py` | runs everything, reports timings, then calls `diff.py` |
| `gen_port_map.py` | regenerates `port-map.json` from the dependency probe |
| `port-map.json` | **the single source of truth for porting progress** (all 81 probe entries) |
| `STATUS.md` | current counts, how to use it, known non-comparable points |
| `.build/` | generated, git-ignored |
| `out/` | generated results, git-ignored |

## Provenance of the engine under test

* SillyTavern snapshot directory: `research/_raw/`
* engine file: `research/_raw/st_public_scripts_world-info.js`
* `st_package.json` says `sillytavern 1.19.0`, AGPL-3.0
* byte-identical to `public/scripts/world-info.js` at `release` commit
  `06bde939fb1e9c4c8d8641d810f0a916b5bce127`
  (`sha256 111c7f47945839e75b021e09bdbb112a54f0a9b7857d2b95435f573efc7cd9a5`,
  265081 bytes) — verified with `gh api .../contents/...` and a local sha256 compare.
* `gen_adapter.py` copies that file verbatim; it never edits it.

## Adding a fixture

1. add a case to `gen_fixtures.py` and run it (or drop a hand-written JSON file in
   `fixtures/`),
2. `python tools/st-oracle/run_all.py`,
3. read the new divergences — each one is either a porting task (add it to
   `port-map.json`) or a documented non-comparable point (see `STATUS.md`).

The fixture schema is documented at the top of `gen_fixtures.py` and in the
comments of `run.mjs`; keep the two runners in sync if you extend it.
