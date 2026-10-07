// Oracle driver: run SillyTavern's provider prompt converters over one fixture.
//
//   node tools/st-oracle/run_converters.mjs <fixture.json> [--out <result.json>]
//
// The reference path is `public/scripts/prompt-converters.js` (SillyTavern
// 1.19.0): the pure server-side logic that reshapes an assembled chat into each
// provider's wire format. The fixture names a function and its arguments; this
// harness supplies what a request would (the config values the module reads at
// import time, and the `PromptNames` object) and records the result.
//
// Deliberately dumb. There is no per-function logic here: the function is looked
// up by name and called with the fixture's arguments, and both its return value
// and the arguments *after* the call are recorded. Several of these functions
// mutate their input in place and return nothing (`cachingSystemPromptForOpenRouter`,
// `addReasoningContentToToolCalls`, …), so the post-call arguments are the only
// observable result for them. Anything cleverer than "call and observe" would be
// the harness inventing behaviour, which is exactly how the S2 oracle spent six
// rounds blaming the port for its own bugs (see STATUS-S2.md).
//
// The generated shim tree provides the real module (copied verbatim by
// gen_adapter.py). Fix a wrong stub there, never by patching the build tree here.
//
// Fixture shape
// -------------
// ```json
// {
//   "name": "27-convert-claude-messages-image-migration",
//   "function": "convertClaudeMessages",   // the reference's export, verbatim
//   "description": "what this pins and why",
//   "config": { "gemini.thoughtSignatures": false },
//   "names": { "charName": "Iris", "userName": "User", "groupNames": ["Seraphina"] },
//   "args": [ [ ...messages... ], "", false, false, "$promptNames" ],
//   "probe": { "path": "startsWithGroupName", "call": ["Seraphina: hi"], "this_path": "" }
// }
// ```
//
// * `function` -- looked up on the module by exact name. An unknown name is a
//   hard error, not a skip.
// * `config` -- applied before the module is imported, because two of its
//   constants are read at module scope (`:4`, `:34`).
// * `args` -- the call's positional arguments. Recorded before and after the call.
// * `"$promptNames"` -- a sentinel. Any `args` slot holding it is replaced by a
//   real `PromptNames` object built from `names`. Required **only when the call
//   can reach the group-name predicate**: that predicate is a function reading
//   `this.groupNames` (`:54-56`), so it cannot be written in JSON, and an inline
//   dict would make the reference call a real function while the port sees a
//   missing key -- a silent divergence. When a fixture only sets scalars
//   (`charName` / `userName` / `groupNames`) and passes an inline dict, that is
//   fine and simpler; nothing can consult the predicate there.
// * `probe` -- `{ path, call, this_path }`. Resolves `path` on the return value
//   and, when it is a function, calls it. `this_path` rebinds the receiver
//   (default: the return value), which the one method-shaped predicate needs.
//
// Determinism
// -----------
// `crypto.randomBytes(32)` (`:846`) hides a media part behind a token while
// flattening content; the token is unguessable by design, so it is pinned to a
// fixed byte here and in `run_converters_python.py`. Without the pin any fixture
// whose media survives the merge could never match. The sha512 path (`:715`) is
// deterministic already and is not touched.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BUILD = path.join(HERE, '.build');
const MODULE = path.join(BUILD, 'public', 'scripts', 'prompt-converters.js');
const UTIL = path.join(BUILD, 'public', 'scripts', 'util.js');

function parseArgs(argv) {
    const out = { fixture: null, out: null };
    for (let i = 0; i < argv.length; i++) {
        const a = argv[i];
        if (a === '--out') out.out = argv[++i];
        else if (!out.fixture) out.fixture = a;
    }
    return out;
}

function fail(message) {
    process.stderr.write(`run_converters.mjs: ${message}\n`);
    process.exit(2);
}

const args = parseArgs(process.argv.slice(2));
if (!args.fixture) fail('usage: node run_converters.mjs <fixture.json> [--out <result.json>]');
if (!fs.existsSync(MODULE)) {
    fail(`converter shim missing (${MODULE}); run tools/st-oracle/gen_adapter.py`);
}

const fixture = JSON.parse(fs.readFileSync(args.fixture, 'utf8'));

//: Byte the media-token generator is pinned to (see the `randomBytes` override
//: below). Any fixed value works; it must be the same on both sides.
const PINNED_BYTE = 7;

//: The sentinel a fixture writes in an `args` slot that has to become the
//: `PromptNames` object. A names object carries a *function*
//: (`startsWithGroupName`), so it cannot be written in JSON at all -- and a
//: fixture that passes a plain dict instead makes the two sides take different
//: paths, because the reference calls the predicate while the port would see a
//: missing key. Substituting the real object here means a fixture can drive the
//: group-name branches identically on both sides.
const PROMPT_NAMES_SENTINEL = '$promptNames';

function resolveArgs(values) {
    return (values ?? []).map((value) => {
        if (value !== PROMPT_NAMES_SENTINEL) {
            return structuredClone(value);
        }
        const names = fixture.names ?? {};
        return {
            charName: names.charName ?? '',
            userName: names.userName ?? '',
            groupNames: Array.isArray(names.groupNames) ? [...names.groupNames] : [],
            // Method-shaped, like the reference's: it reads `this.groupNames`
            // (prompt-converters.js:54-56), so a detached call still throws.
            startsWithGroupName: function startsWithGroupName(message) {
                return this.groupNames.some((name) => String(message).startsWith(`${name}: `));
            },
        };
    });
}

// `PROMPT_PLACEHOLDER` and `enableThoughtSignatures` are read from the config at
// module scope (prompt-converters.js:4 and :34), so the config has to be in place
// *before* the module is imported. `setConvConfig` is the hook gen_adapter.py
// generates for that; the same call covers `mistral.enablePrefix`, which is read
// lazily at call time (:709).
const util = await import(pathToFileURL(UTIL).href);
if (typeof util.setConvConfig === 'function') {
    util.setConvConfig(fixture.config ?? {});
}

const converters = await import(pathToFileURL(MODULE).href);

// `crypto.randomBytes(32)` (prompt-converters.js:846) hides a media part behind a
// token while `mergeMessages` flattens content. The token is unguessable by
// design, so its *value* is not part of the contract -- but it is observable, and
// two random 44-character base64 strings never match. Pin it so a fixture that
// mixes media with text can be compared at all. The sha512 path (:715) is
// deterministic already and is untouched.
const nodeCrypto = await import('node:crypto');
try {
    nodeCrypto.default.randomBytes = (size) => Buffer.alloc(size, PINNED_BYTE);
} catch {
    Object.defineProperty(nodeCrypto.default, 'randomBytes', {
        value: (size) => Buffer.alloc(size, PINNED_BYTE),
        writable: true,
        configurable: true,
    });
}

const record = {
    fixture: fixture.name ?? path.basename(args.fixture, '.json'),
    function: fixture.function,
};

// A function cannot cross into JSON, and `getPromptNames` returns one
// (`startsWithGroupName`). Rather than let JSON silently drop it -- which would
// make an unported predicate look like a match -- every function-typed value is
// recorded as the marker `'<function>'` (so its *presence* is still asserted),
// and a fixture that wants the predicate's behaviour spells it out in `probe`.
function jsonSafe(value) {
    if (value === null || typeof value !== 'object') {
        return typeof value === 'function' ? '<function>' : value;
    }
    if (Array.isArray(value)) {
        return value.map(jsonSafe);
    }
    const out = {};
    for (const [key, item] of Object.entries(value)) {
        out[key] = jsonSafe(item);
    }
    return out;
}

// `probe` is `{ path, call, this_path }`: resolve `path` against the return
// value, call it with `call` when it is a function, and record both the call and
// its result. `this_path` rebinds the receiver to whatever that path resolves to
// (default: the return value itself), which is needed for the one method-shaped
// predicate in this module -- `getPromptNames` returns `startsWithGroupName` and
// it reads `this.groupNames` (`prompt-converters.js:54-56`), so calling it
// detached throws a TypeError.
function resolvePath(root, dotted) {
    let node = root;
    for (const part of String(dotted).split('.')) {
        if (node === null || node === undefined) {
            return { found: false, at: part };
        }
        node = node[part];
    }
    return { found: true, value: node };
}

function runProbe(root) {
    const spec = fixture.probe;
    if (!spec || !spec.path) {
        return null;
    }

    const where = resolvePath(root, spec.path);
    if (!where.found) {
        return { path: spec.path, error: `path not found before ${JSON.stringify(where.at)}` };
    }

    const node = where.value;
    if (typeof node !== 'function') {
        return { path: spec.path, value: jsonSafe(node) };
    }

    const receiver = spec.this_path ? resolvePath(root, spec.this_path) : { found: true, value: root };
    const call = spec.call ?? [];
    try {
        return {
            path: spec.path,
            call,
            result: jsonSafe(node.apply(receiver.value, structuredClone(call))),
        };
    } catch (error) {
        return { path: spec.path, call, error: `${error?.name ?? 'Error'}: ${error?.message ?? ''}` };
    }
}

const target = converters[fixture.function];
if (typeof target !== 'function') {
    record.error = `no such export in prompt-converters.js: ${JSON.stringify(fixture.function)}`;
}

if (!record.error) {
    const callArgs = resolveArgs(fixture.args ?? []);
    // `jsonSafe`, not a bare clone: the names object carries a function-valued
    // property, and JSON.stringify drops it silently on the JS side while Python
    // would have recorded a marker -- so the two sides would disagree about a
    // field neither is testing.
    record.args_in = jsonSafe(callArgs);

    try {
        const returned = await target(...callArgs);
        record.returned = returned === undefined ? null : jsonSafe(returned);
        record.mutated = jsonSafe(callArgs);
        record.probe = runProbe(returned);
    } catch (error) {
        record.error = `${error?.name ?? 'Error'}: ${error?.message ?? ''}`;
    }
}

const payload = JSON.stringify(record, null, 2);
if (args.out) {
    fs.writeFileSync(args.out, payload + '\n');
    process.stdout.write(`wrote ${args.out}\n`);
} else {
    process.stdout.write(payload + '\n');
}
