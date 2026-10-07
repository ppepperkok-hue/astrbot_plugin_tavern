// Oracle driver: run SillyTavern's World Info engine over one fixture file.
//
//   node tools/st-oracle/run.mjs <fixture.json> [--out <result.json>] [--trace]
//
// The engine module is imported *dynamically* on purpose: every global stub
// (jQuery, document, fetch, Math.random) has to be installed before the module
// body evaluates, and static ESM imports would run first.
//
// One fixture per process. The engine keeps per-chat state (world info cache,
// timed effects on `chat_metadata`, external activations), and a fresh process is
// the only honest way to guarantee fixtures cannot leak into each other.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { __stub, mulberry32, countTokens, worldFor } from './runtime.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BUILD = path.join(HERE, '.build');
const ENGINE = path.join(BUILD, 'public', 'scripts', 'world-info.js');

function parseArgs(argv) {
    const out = { fixture: null, out: null, trace: false };
    for (let i = 0; i < argv.length; i++) {
        const a = argv[i];
        if (a === '--out') out.out = argv[++i];
        else if (a === '--trace') out.trace = true;
        else if (!out.fixture) out.fixture = a;
    }
    return out;
}

function fail(message) {
    process.stderr.write(`run.mjs: ${message}\n`);
    process.exit(2);
}

const args = parseArgs(process.argv.slice(2));
if (!args.fixture) fail('usage: node run.mjs <fixture.json> [--out <result.json>] [--trace]');
if (!fs.existsSync(ENGINE)) fail(`engine shim missing (${ENGINE}); run tools/st-oracle/gen_adapter.py`);

const fixture = JSON.parse(fs.readFileSync(args.fixture, 'utf8'));
const seed = Number(fixture.settings?.seed ?? 1337);
const bookScanDepth = fixture.book_scan_depth ?? {};

// --- fixture world books ----------------------------------------------------
// `books`        : SillyTavern-native {"entries": {...}} containers (as stored on disk)
// `character_books`: V2 character-book shape, converted through convertCharacterBook()
//
// `book_scan_depth` is oracle bookkeeping, not SillyTavern data: the JS engine
// keeps ONE global scan depth (world_info_depth) while the Python port attaches
// the depth to the book. The oracle applies the recorded depth to the raw book
// so both backends scan the same window; see run_python.py for the mirror side.
const worlds = {};
for (const [name, book] of Object.entries(fixture.books ?? {})) {
    const depth = bookScanDepth[name];
    worlds[name] = depth === undefined ? book : { ...book, scan_depth: depth };
}

// --- globals the engine reads at import time -------------------------------
// The engine is extremely chatty (console.debug on every step). Silence it so
// stdout stays machine readable; use --trace for the JSON on stderr.
for (const level of ['log', 'debug', 'info', 'warn', 'error', 'trace']) {
    console[level] = () => {};
}
globalThis.$ = __stub('$');
globalThis.jQuery = globalThis.$;
globalThis.window = globalThis;
globalThis.document = __stub('document');
globalThis.toastr = __stub('toastr');
globalThis.localStorage = __stub('localStorage');
globalThis.__ORACLE_WORLDS = worlds;
globalThis.__ORACLE = { countTokens };
globalThis.__ORACLE_SEED = seed;
globalThis.__ORACLE_RANDOM = mulberry32(seed);

// The engine only rolls probabilities via Math.random(); pin the stream so a
// fixture's probability outcome is reproducible.
Math.random = globalThis.__ORACLE_RANDOM;

const requests = [];
globalThis.fetch = async (url, opts = {}) => {
    const body = opts?.body ? JSON.parse(opts.body) : {};
    const target = String(url);
    requests.push({ url: target, body });
    if (target.includes('/api/worldinfo/get')) {
        const data = worldFor(body.name);
        return data
            ? { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(data)) }
            : { ok: false, status: 404, json: async () => ({}) };
    }
    if (target.includes('/api/settings/get')) {
        return { ok: true, status: 200, json: async () => ({ world_names: Object.keys(worlds) }) };
    }
    return { ok: false, status: 404, json: async () => ({}) };
};

// --- engine -----------------------------------------------------------------
const t0 = process.hrtime.bigint();
const wi = await import(pathToFileURL(ENGINE).href);
const importMs = Number(process.hrtime.bigint() - t0) / 1e6;

// Character books go through the real convertCharacterBook(), so fixture 09
// measures the converter, not a hand-written translation.
for (const [name, characterBook] of Object.entries(fixture.character_books ?? {})) {
    worlds[name] = wi.convertCharacterBook(JSON.parse(JSON.stringify(characterBook)));
}

const messages = (fixture.chat ?? []).map((m) => (typeof m === 'string' ? m : String(m.mes ?? '')));
// getWorldInfoPrompt / checkWorldInfo want the chat in *reverse* chronological
// order: index 0 is the newest message.
const chat = [...messages].reverse();

/** A scan may replay a different (shorter) chat; sticky/cooldown windows are
 * measured against the chat length, so shortening it is how a fixture walks
 * through "the chat grew by one message". */
function chatFor(scan) {
    if (!scan.chat) return chat;
    return scan.chat.map((m) => (typeof m === 'string' ? m : String(m.mes ?? ''))).reverse();
}

const globalScanData = {
    trigger: 'normal',
    personaDescription: '',
    characterDescription: '',
    characterPersonality: '',
    characterDepthPrompt: '',
    scenario: '',
    creatorNotes: '',
    ...(fixture.global_scan_data ?? {}),
};

const t1 = process.hrtime.bigint();
const scans = [];
for (const scan of fixture.scans ?? []) {
    const settings = scan.settings ?? {};
    // setWorldInfoSettings() reads the knobs off the top level AND the active
    // books off `settings.world_info.globalSelect`, so both have to be present in
    // one object. Dropping either silently pins the engine to its own defaults
    // (depth 2, recursion off) and makes every comparison meaningless.
    const pass = { ...settings, world_info: { globalSelect: scan.worlds ?? [] } };
    wi.setWorldInfoSettings(pass, { world_names: Object.keys(worlds) });

    // setWorldInfoSettings() silently ignores keys it does not know and silently
    // rewrites some values (e.g. a budget above 100 is migrated to 25). Read the
    // effective settings back and refuse to pretend a scan used knobs it did not,
    // because a fixture that scans with engine defaults still produces a
    // well-formed result -- just a meaningless one.
    const applied = wi.getWorldInfoSettings();
    const mismatched = [];
    for (const [key, requested] of Object.entries(settings)) {
        if (!(key in applied)) continue;
        const effective = applied[key];
        if (effective === requested) continue;
        if (key === 'world_info_max_recursion_steps' && requested === 0) continue; // 0 = unbounded
        mismatched.push({ key, requested, applied: effective });
    }

    const prompt = await wi.getWorldInfoPrompt(
        chatFor(scan),
        Number(scan.max_context ?? 4096),
        Boolean(scan.is_dry_run ?? true),
        globalScanData,
    );

    // Which entries did the engine activate? getWorldInfoPrompt() returns only
    // assembled strings, so recover the identity of everything that reached the
    // prompt by matching the emitted content back to its source entry. This is
    // deliberately read-only: a second checkWorldInfo() call would advance the
    // engine's timed-effect state and emit an extra event.
    const activated = collectActivated(worlds, prompt);
    activated.sort((a, b) => a.order - b.order || a.uid - b.uid);

    scans.push({
        settings,
        settings_applied: applied,
        settings_mismatched: mismatched,
        world_info: { globalSelect: scan.worlds ?? [] },
        worlds_loaded: (scan.worlds ?? []).filter((w) => worlds[w] !== undefined && worlds[w] !== null),
        worldInfoString: prompt.worldInfoString ?? '',
        worldInfoBefore: prompt.worldInfoBefore ?? '',
        worldInfoAfter: prompt.worldInfoAfter ?? '',
        anBefore: prompt.anBefore ?? [],
        anAfter: prompt.anAfter ?? [],
        outletEntries: prompt.outletEntries ?? {},
        worldInfoDepth: normalizeDepth(prompt.worldInfoDepth),
        worldInfoExamples: (prompt.worldInfoExamples ?? []).map((e) => ({
            position: e?.position ?? null,
            content: e?.content ?? '',
        })),
        activatedEntries: activated,
    });
}
const scanMs = Number(process.hrtime.bigint() - t1) / 1e6;

for (const scan of scans) {
    if (scan.settings_mismatched.length) {
        for (const item of scan.settings_mismatched) {
            process.stderr.write(
                `run.mjs: fixture asked ${item.key}=${item.requested} but the engine runs with ` +
                `${item.applied}; the comparison for this scan is meaningless\n`,
            );
        }
        process.exitCode = 3;
    }
}

/**
 * Match every piece of emitted content back to its source entry, so the diff can
 * talk about "book.uid" instead of opaque strings. Read-only: it never re-runs
 * the engine. The emitted record carries the same six fields on both backends so
 * `diff.py` can compare the activated set directly; ordering is normalised by
 * (order, uid) because the two engines emit in opposite insertion orders.
 */
function collectActivated(worlds, prompt) {
    const index = new Map();
    const remember = (content, record) => {
        if (!content) return;
        if (!index.has(content)) index.set(content, []);
        index.get(content).push(record);
    };
    for (const [world, book] of Object.entries(worlds)) {
        for (const [key, entry] of Object.entries(book?.entries ?? {})) {
            const content = String(entry?.content ?? '');
            if (!content) continue;
            const record = {
                uid: Number(entry.uid ?? key),
                world,
                order: Number(entry.order ?? 100),
                position: Number(entry.position ?? 0),
                depth: Number(entry.depth ?? 4),
                role: Number(entry.role ?? 0),
            };
            remember(content, record);
            // getSortedEntries() strips decorators before scanning, so the string
            // the engine emits may not equal the raw book content. Index the
            // stripped variant too (same algorithm as parseDecorators()).
            const stripped = stripDecorators(content);
            if (stripped !== content) remember(stripped, record);
        }
    }
    const found = [];
    const seen = new Set();
    const consider = (content) => {
        for (const hit of index.get(String(content)) ?? []) {
            const key = `${hit.world}.${hit.uid}`;
            if (seen.has(key)) continue;
            seen.add(key);
            found.push({ ...hit });
        }
    };
    for (const list of Object.values(prompt.outletEntries ?? {})) {
        for (const item of list ?? []) consider(item);
    }
    for (const group of prompt.worldInfoDepth ?? []) {
        for (const item of group?.entries ?? []) consider(item);
    }
    for (const list of [prompt.anBefore, prompt.anAfter]) {
        for (const item of list ?? []) consider(item);
    }
    for (const item of prompt.worldInfoExamples ?? []) consider(item?.content);
    for (const text of [prompt.worldInfoBefore, prompt.worldInfoAfter]) {
        for (const piece of String(text ?? '').split('\n')) consider(piece);
    }
    return found;
}

/**
 * Mirror of world-info.js `parseDecorators()` line handling: only the two
 * KNOWN_DECORATORS are stripped, and only while the leading block is unbroken.
 */
function stripDecorators(content) {
    const KNOWN = ['@@activate', '@@dont_activate'];
    if (!content.startsWith('@@')) return content;
    const lines = content.split('\n');
    let fallbacked = false;
    for (let i = 0; i < lines.length; i++) {
        if (lines[i].startsWith('@@')) {
            if (lines[i].startsWith('@@@') && !fallbacked) continue;
            const probe = lines[i].startsWith('@@@') ? lines[i].substring(1) : lines[i];
            if (KNOWN.some((known) => probe.startsWith(known))) fallbacked = false;
            else fallbacked = true;
        } else {
            return lines.slice(i).join('\n');
        }
    }
    return content;
}

function normalizeDepth(entries) {    return (entries ?? []).map((e) => ({
        depth: e.depth ?? null,
        role: e.role ?? null,
        entries: (e.entries ?? []).map(String),
    }));
}

const result = {
    fixture: path.basename(args.fixture, '.json'),
    engine: 'sillytavern',
    oracle: 'node',
    node_version: process.version,
    seed,
    comparable: fixture.comparable !== false,
    unavailable_reason: fixture.unavailable_reason ?? null,
    timing: { import_ms: round(importMs), scan_ms: round(scanMs) },
    requests,
    scans,
};

function round(n) {
    return Math.round(n * 1000) / 1000;
}

const json = JSON.stringify(result, null, 2) + '\n';
if (args.out) {
    fs.mkdirSync(path.dirname(args.out), { recursive: true });
    fs.writeFileSync(args.out, json, 'utf8');
}
if (args.trace) process.stderr.write(json);
else process.stdout.write(json);
