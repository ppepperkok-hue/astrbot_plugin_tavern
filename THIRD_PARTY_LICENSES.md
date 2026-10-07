# Third party licences and provenance

This file records every third party project this plugin reuses **code** from, and
why. Format and data compatibility alone is listed under "Interoperability"
below; it does not require code attribution, but is recorded for completeness.

The project itself is licensed under **AGPL-3.0** (see [`LICENSE`](LICENSE)).

---

## Code reuse

*Nothing yet.* No file in `tavern/` currently contains code copied from another
project; the engine is an independent implementation of the documented formats.
If that changes, the file header must keep the original copyright notice
(AGPL-3.0 §5(a)) and a row must be added here with:

| File / path | Upstream project | Upstream licence | What was taken | Date |
|---|---|---|---|---|
| _(none)_ | | | | |

---

## Interoperability (no code copied)

| Project | Licence | What we honour |
|---|---|---|
| [SillyTavern](https://github.com/SillyTavern/SillyTavern) | AGPL-3.0 | Character Card V1/V2/V3 (JSON + PNG `tEXt`/`ccv3`), World Info / lorebook fields and activation semantics, chat `.jsonl` layout, prompt block ordering |

The SillyTavern project is licensed under the GNU Affero General Public License
v3.0 (its `LICENSE` is the unmodified FSF text, 34,523 bytes; `package.json`
declares `"license": "AGPL-3.0"`). Our project uses the same licence, so code may
be reused between them; when it is, the obligations are:

1. keep the upstream copyright notice and licence notice in the reused file,
2. state that the file was modified, with a date,
3. keep the whole plugin under AGPL-3.0 (it already is).

## Bundled assets

*Nothing bundled.* No upstream images, audio, preset text or other assets are
shipped. The default `main` prompt text in `tavern/st/prompt.py` is our own
wording, not SillyTavern's stock preset text.

## Development-time references

The `research/` directory contains third party documents collected for
compatibility work (SillyTavern documentation pages, specification files and
source snapshots) plus the generated analysis. It is **excluded from the
published plugin archive** through `.gitattributes` (`export-ignore`), so the
packaged plugin stays small and only contains what AstrBot needs.
