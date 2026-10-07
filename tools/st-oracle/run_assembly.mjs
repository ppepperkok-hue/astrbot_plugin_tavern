// Oracle driver: run SillyTavern's prompt assembly over one fixture.
//
//   node tools/st-oracle/run_assembly.mjs <fixture.json> [--out <result.json>]
//
// The reference path is `populateChatCompletion()` (openai.js:1185-1347): it
// decides the final order of the messages the model receives. The fixture
// declares the prompt collection and the inputs; this harness supplies the
// pieces a browser would provide (a real PromptCollection, a token counter both
// backends share, and the callbacks the function accepts) and records the
// resulting identifier sequence, message shapes and overridden prompts.
//
// The assembly *order* is the deliverable and is compared field by field by
// diff_assembly.py. Token counts are pinned by the fixture, so budget arithmetic
// stays comparable instead of depending on the browser tokenizer.
//
// The generated shim tree provides the real `Prompt` / `PromptCollection` /
// `INJECTION_POSITION` (copied verbatim from the vendored snapshot) and the
// `getExtensionPrompt*` helpers -- see `PROVIDED_BY_SOURCE` / `OVERRIDES` in
// gen_adapter.py. Fix a wrong stub there, never by patching the build tree here.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { __stub, mulberry32 } from './runtime.mjs';
import {
    INJECTION_POSITION,
    Prompt,
    PromptCollection,
} from './.build/public/scripts/PromptManager.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BUILD = path.join(HERE, '.build');
const ENGINE = path.join(BUILD, 'public', 'scripts', 'openai.js');
const POWER_USER = path.join(BUILD, 'public', 'scripts', 'power-user.js');

//: ``openai.js:206-211`` -- the enum is module-private, so it is spelled out.
const NAMES_BEHAVIOR = { NONE: -1, DEFAULT: 0, COMPLETION: 1, CONTENT: 2 };

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
    process.stderr.write(`run_assembly.mjs: ${message}\n`);
    process.exit(2);
}

const args = parseArgs(process.argv.slice(2));
if (!args.fixture) fail('usage: node run_assembly.mjs <fixture.json> [--out <result.json>]');
if (!fs.existsSync(ENGINE)) fail(`engine shim missing (${ENGINE}); run tools/st-oracle/gen_adapter.py`);

const fixture = JSON.parse(fs.readFileSync(args.fixture, 'utf8'));

globalThis.jQuery = __stub('jQuery');
globalThis.$ = globalThis.jQuery;
globalThis.document = __stub('document');
globalThis.window = globalThis;
globalThis.localStorage = __stub('localStorage');
globalThis.toastr = __stub('toastr');
globalThis.fetch = async () => ({ ok: true, json: async () => ({}), text: async () => '' });
Math.random = mulberry32(Number(fixture.seed ?? 1337));

const engine = await import(pathToFileURL(ENGINE).href);
const { ChatCompletion, Message, populateChatCompletion, oracleSetPromptManager } = engine;
if (!ChatCompletion || !Message || !populateChatCompletion || !oracleSetPromptManager) {
    fail('openai.js is missing ChatCompletion / Message / populateChatCompletion / the oracle hook');
}

// `power_user` is a const object in the shim, so the fixture's flags are merged
// into it rather than replacing it.
const powerUser = (await import(pathToFileURL(POWER_USER).href)).power_user;
if (powerUser && typeof powerUser === 'object') {
    Object.assign(powerUser, {
        pin_examples: Boolean(fixture.pin_examples),
        names_behavior: Number(fixture.names_behavior ?? NAMES_BEHAVIOR.DEFAULT),
    });
}
if (typeof engine.oracleSetPrompts === 'function') {
    // The prompt strings come from the fixture so both backends compare the same
    // text; the reference would otherwise read module-level i18n defaults.
    engine.oracleSetPrompts({
        newChat: fixture.new_chat_prompt ?? '[Start a new Chat]',
        newGroupChat: fixture.new_group_chat_prompt,
    });
}
if (typeof engine.oracleApplyGroupChat === 'function') {
    // `selected_group` is bound to the `group-chats.js` stub (always truthy), so
    // the only honest way to express "not a group chat" is to mirror the normal
    // new-chat prompt onto the group variant (openai.js:893 picks between them).
    engine.oracleApplyGroupChat(Boolean(fixture.group_chat));
}

const oaiSettings = engine.oai_settings;

function applyFixtureSettings() {
    for (const [key, value] of Object.entries(fixture.settings ?? {})) {
        oaiSettings[key] = value;
    }
    if (typeof fixture.names_behavior === 'number') {
        oaiSettings.names_behavior = fixture.names_behavior;
    }
}

function namesBehavior(value) {
    if (typeof value === 'string') {
        return NAMES_BEHAVIOR[value.trim().toUpperCase()] ?? NAMES_BEHAVIOR.DEFAULT;
    }
    return typeof value === 'number' ? value : NAMES_BEHAVIOR.DEFAULT;
}

// --- prompt collection -------------------------------------------------------
function makePrompt(spec) {
    const prompt = new Prompt();
    prompt.identifier = spec.identifier;
    prompt.role = spec.role ?? 'system';
    prompt.content = spec.content ?? '';
    prompt.system_prompt = spec.system_prompt ?? true;
    prompt.injection_position =
        spec.injection_position === 'absolute'
            ? INJECTION_POSITION.ABSOLUTE
            : INJECTION_POSITION.RELATIVE;
    prompt.injection_depth = spec.injection_depth ?? 0;
    prompt.injection_order = spec.injection_order ?? 100;
    prompt.marker = spec.marker ?? false;
    prompt.extension = spec.extension ?? false;
    return prompt;
}

function makeCollection(specs) {
    // The `PromptCollection` constructor validates its arguments, so the prompt
    // objects have to come from the same module instance as the collection --
    // which is why the stub is imported statically above.
    const made = specs.map(makePrompt);
    return new PromptCollection(...made);
}

// --- pinned token counter ----------------------------------------------------
// The engine's own counter is the browser tokenizer (`countTokensOpenAIAsync`,
// a stub here). Both backends therefore count by the same rule, pinned by the
// fixture; `token_divisor` mirrors the port's estimate.
const divisor = Number(fixture.token_divisor ?? 3);
function countFor(role, content, name) {
    const parts = [role, content, name].filter(
        (value) => typeof value === 'string' && value.length > 0,
    );
    const text = parts.join(' ');
    return text.length ? Math.max(1, Math.floor(text.length / divisor)) : 0;
}

const prompts = makeCollection(fixture.prompts ?? []);
for (const identifier of fixture.overridden_prompts ?? []) {
    prompts.override(identifier);
}
oracleSetPromptManager({
    serviceSettings: { names_behavior: namesBehavior(fixture.names_behavior) },
    log: () => {},
    isPromptDisabledForActiveCharacter: (identifier) =>
        (fixture.disabled_prompts ?? []).includes(identifier),
    preparePrompt: (prompt) => prompt,
    isValidName: (name) => typeof name === 'string' && /^[\w' -]+$/.test(name),
    sanitizeName: (name) => String(name ?? '').replace(/[^\w' -]+/g, '_'),
    getPromptCollection: () => prompts,
});

const completion = new ChatCompletion();
completion.setTokenBudget(Number(fixture.max_context ?? 1_000_000), Number(fixture.response ?? 0));

const messageFactory = async (role, content, identifier) => {
    const message = Object.create(Message.prototype);
    message.identifier = identifier;
    message.role = role || 'system';
    message.content = content ?? '';
    message.name = '';
    message.tool_calls = null;
    message.signature = null;
    message.reasoning = null;
    message.tokens = countFor(message.role, message.content, '');
    return message;
};

const record = { fixture: fixture.name ?? path.basename(args.fixture, '.json'), steps: [] };

async function snapshot(label) {
    const chat = completion.getChat();
    record.steps.push({
        label,
        identifiers: completion
            .getMessages()
            .collection.map((item) => (item ? item.identifier : null)),
        chat: chat.map((item) => ({
            role: item.role,
            content: item.content,
            name: item.name ?? null,
        })),
        overridden: completion.getOverriddenPrompts(),
    });
}

applyFixtureSettings();

try {
    await populateChatCompletion(prompts, completion, {
        bias: fixture.bias ?? '',
        quietPrompt: fixture.quiet_prompt ?? '',
        type: fixture.type ?? null,
        messages: (fixture.chat ?? []).map((turn) => ({
            role: turn.role,
            mes: turn.content,
            name: turn.name ?? '',
            extra: {},
        })),
        messageExamples: (fixture.examples ?? []).map((example) => ({
            mes: example.content,
            name: example.name ?? '',
        })),
    });
} catch (error) {
    record.error = `the reference itself failed: ${error?.name ?? error}: ${error?.message ?? ''}`;
}

await snapshot('final');

const payload = JSON.stringify(record, null, 2);
if (args.out) {
    fs.writeFileSync(args.out, payload + '\n');
    process.stdout.write(`wrote ${args.out}\n`);
} else {
    process.stdout.write(payload + '\n');
}
