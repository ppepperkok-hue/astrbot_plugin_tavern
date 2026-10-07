// Oracle driver: run SillyTavern's prompt assembly over one fixture.
//
//   node tools/st-oracle/run_assembly.mjs <fixture.json> [--out <result.json>]
//
// The reference path is `populateChatCompletion()` (openai.js:1185-1347): it
// decides the final order of the messages the model receives. The fixture
// declares the prompt collection and the inputs; this harness supplies the
// pieces a browser would provide (a real PromptCollection, a token counter that
// both backends share, and the callbacks the function accepts) and records the
// resulting identifier sequence.
//
// The assembly *order* is the deliverable and is compared field by field by
// diff_assembly.py. Token counts are pinned by the fixture (`tokens`), so budget
// arithmetic stays comparable instead of depending on the browser tokenizer.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { __stub, mulberry32 } from './runtime.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BUILD = path.join(HERE, '.build');
const ENGINE = path.join(BUILD, 'public', 'scripts', 'openai.js');

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
// `selected_group` is bound to the `group-chats.js` stub, so the only honest way
// to express "not a group chat" is to make the group variant evaluate to the
// same string the normal variant would (openai.js:893 picks between the two).
globalThis.__ORACLE_GROUP_CHAT = Boolean(fixture.group_chat);

const module = await import(pathToFileURL(ENGINE).href);
const { ChatCompletion, populateChatCompletion, oracleSetPromptManager } = module;
if (!ChatCompletion || !populateChatCompletion || !oracleSetPromptManager) {
    fail('openai.js is missing ChatCompletion / populateChatCompletion / the oracle hook');
}
if (typeof module.oracleApplyGroupChat === 'function') {
    module.oracleApplyGroupChat(Boolean(fixture.group_chat));
}
if (typeof module.oracleSetPrompts === 'function') {
    // The prompt strings come from the fixture so both backends compare the same
    // text; the reference would otherwise read module-level i18n defaults.
    module.oracleSetPrompts({
        newChat: fixture.new_chat_prompt ?? '[Start a new Chat]',
        newGroupChat: fixture.new_group_chat_prompt ?? '[Start a new group chat. Group members: {{group}}]',
    });
    module.oracleApplyGroupChat(Boolean(fixture.group_chat));
}
// `power_user` is a const object in the shim tree, so the fixture's flags are
// merged into it rather than replacing it.
const powerUser = (await import(
    pathToFileURL(path.join(BUILD, 'public', 'scripts', 'power-user.js')).href
)).power_user;
if (powerUser && typeof powerUser === 'object') {
    Object.assign(powerUser, {
        pin_examples: Boolean(fixture.pin_examples),
        names_behavior: Number(fixture.names_behavior ?? 0),
    });
}

// `PromptManager` is a browser singleton the module assigns during setup
// (openai.js:682). The oracle supplies its own through the module's hook; the
// fixture decides which prompts are disabled for the active character.
const disabledPrompts = new Set(fixture.disabled_prompts ?? []);
const promptManager = {
    serviceSettings: {},
    log: () => {},
    isPromptDisabledForActiveCharacter: (identifier) => disabledPrompts.has(identifier),
    preparePrompt: (prompt, content) => content ?? prompt?.content ?? '',
    isValidName: (name) => typeof name === 'string' && /^[\w' -]+$/.test(name),
    sanitizeName: (name) => String(name ?? '').replace(/[^\w' -]+/g, '_'),
    getPromptCollection: () => prompts,
};

// --- prompt collection -------------------------------------------------------
// `PromptCollection` is module-private too, so the fixture's prompts are turned
// into the plain shape `populateChatCompletion` duck-types: an object with
// `identifier`, `role`, `content`, `system_prompt`, `injection_position`,
// `injection_depth`, `injection_order`, `marker` and `extension`.
const INJECTION_POSITION = { RELATIVE: 0, ABSOLUTE: 1 };

function makePrompt(spec) {
    return {
        identifier: spec.identifier,
        role: spec.role ?? 'system',
        content: spec.content ?? '',
        system_prompt: spec.system_prompt ?? true,
        injection_position:
            spec.injection_position === 'absolute'
                ? INJECTION_POSITION.ABSOLUTE
                : INJECTION_POSITION.RELATIVE,
        injection_depth: spec.injection_depth ?? 0,
        injection_order: spec.injection_order ?? 100,
        marker: spec.marker ?? false,
        extension: spec.extension ?? false,
    };
}

function makeCollection(specs) {
    const collection = specs.map(makePrompt);
    const byIdentifier = new Map(collection.map((prompt) => [prompt.identifier, prompt]));
    return {
        collection,
        overriddenPrompts: fixture.overridden_prompts ?? [],
        has: (identifier) => byIdentifier.has(identifier),
        get: (identifier) => byIdentifier.get(identifier),
        index: (identifier) => collection.findIndex((item) => item.identifier === identifier),
        add: (prompt) => collection.push(prompt),
    };
}

// --- pinned token counter ----------------------------------------------------
// `Message.fromPromptAsync` counts through the module's private tokenHandler,
// whose tokenizer is a stub headless. The assembly order does not depend on the
// counts, only on the budget being large enough, so the fixture sets a budget
// from `max_context` and the driver reports the count it saw instead of
// asserting it.
const prompts = makeCollection(fixture.prompts ?? []);
oracleSetPromptManager(promptManager);
const completion = new ChatCompletion();
completion.setTokenBudget(Number(fixture.max_context ?? 1_000_000), Number(fixture.response ?? 0));

const madeMessages = [];
const messageFactory = async (role, content, identifier) => {
    const Message = module.Message;
    const message = Object.create(Message.prototype);
    message.identifier = identifier;
    message.role = role || 'system';
    message.content = content ?? '';
    message.name = '';
    message.tool_calls = null;
    message.signature = null;
    message.reasoning = null;
    message.tokens = 1;
    madeMessages.push(message);
    return message;
};

const record = { fixture: fixture.name ?? path.basename(args.fixture, '.json'), steps: [] };
// `selected_group` comes from `script.js`, which the shim renders as a stub. The
// fixture value is published on the global object, which is the only place a
// headless run can influence it.
globalThis.selected_group = Boolean(fixture.selected_group);
globalThis.groupId = fixture.selected_group ? 'oracle-group' : undefined;

async function snapshot(label) {
    const chat = completion.getChat();
    record.steps.push({
        label,
        identifiers: completion.getMessages().collection.map((item) => item.identifier),
        chat: chat.map((item) => ({ role: item.role, content: item.content })),
        overridden: completion.getOverriddenPrompts(),
    });
}

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
        messageExamples: fixture.examples ?? [],
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
