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
// openai.js:1224/1229 call Message.fromPromptAsync(prompts.get(id)), and the
// real romPromptAsync (3792-3794) dereferences its argument immediately, so an
// absent optional prompt (impersonate, quietPrompt) throws and aborts the
// whole assembly. In the browser those prompts always exist; the fixture only
// declares the ones it exercises, so the harness models the same thing by
// returning null for a missing prompt. The port guards this itself.

const { ChatCompletion, Message, populateChatCompletion, oracleSetPromptManager } = engine;
if (!ChatCompletion || !Message || !populateChatCompletion || !oracleSetPromptManager) {
    fail('openai.js is missing ChatCompletion / Message / populateChatCompletion / the oracle hook');
}

// The prompt classes must come from the *same module instance* the engine gets,
// or `new PromptCollection(...)` rejects our `Prompt` objects on a class-identity
// check. A static import of the same file resolved to a second instance on
// Windows paths, so the stub is reached through one shared promise that keys off
// the engine's own URL base -- the same base `openai.js` uses for its relative
// import.
const promptModule = await import(
    new URL('PromptManager.js', pathToFileURL(ENGINE)).href
);
const { INJECTION_POSITION, Prompt, PromptCollection } = promptModule;
if (!Prompt || !PromptCollection || !INJECTION_POSITION) {
    fail('the generated PromptManager.js is missing Prompt / PromptCollection / INJECTION_POSITION');
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
    // The prompt-manager banner strings are engine *defaults* (openai.js:108-111),
    // not fixture inputs, so they are read off the engine -- exactly the literals
    // the port falls back to -- and published to the result. A fixture may still
    // override any of them explicitly. `newExampleChat` in particular must not
    // inherit `newChat`: the example banner is '[Example Chat]', and treating it
    // as '[Start a new Chat]' changed its token count and let the budget drop a
    // group the reference keeps.
    engine.oracleSetPrompts({
        newChat: fixture.new_chat_prompt ?? null,
        newGroupChat: fixture.new_group_chat_prompt ?? null,
        newExampleChat: fixture.new_example_chat_prompt ?? null,
        continueNudge: fixture.continue_nudge_prompt ?? null,
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
    // Top-level fixture keys that land on `oai_settings` rather than staying in
    // the runner. `run_assembly_python.py` spells the same three out, so the two
    // sides read the same settings instead of the JS side silently keeping the
    // engine default.
    if (fixture.continue_prefill !== undefined) {
        oaiSettings.continue_prefill = Boolean(fixture.continue_prefill);
    }
    if (fixture.assistant_prefill !== undefined) {
        oaiSettings.assistant_prefill = fixture.assistant_prefill;
    }
    if (fixture.chat_completion_source !== undefined) {
        oaiSettings.chat_completion_source = fixture.chat_completion_source;
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
    // `PromptCollection`'s constructor validates `instanceof Prompt`, and the
    // check is brittle across module instances. The collection is built the way
    // `add()` does it instead (PromptManager.js:201-298): append and record the
    // identifier, so the emitted object is exactly the shape the engine sees.
    const collection = Object.create(PromptCollection.prototype);
    collection.collection = [];
    collection.overriddenPrompts = [];
    for (const prompt of specs.map(makePrompt)) {
        collection.collection.push(prompt);
    }
    return collection;
}

// --- pinned token counter ----------------------------------------------------
// The engine's own counter is the browser tokenizer (`countTokensOpenAIAsync`,
// which is a stub here and reports a different order of magnitude). Both sides
// must bill the same arithmetic for the fixtures' budgets to mean the same
// thing, so this rule is the port's, verbatim -- `tavern/st/chat_completion.py`
// `count_tokens`: non-empty parts joined by one space, `len // 3`, floor 1.
// `token_divisor` pins the divisor for a fixture that wants to stress it.
//
// The counter is installed on the two places the engine counts at -- and the
// assembly reaches both through them: `Message.createAsync` (3558-3566) builds
// the new-chat banner, every history turn and every dialogue example, and
// `Message.prototype.setName` (3599-3602) re-counts once a name is attached.
// `fromPromptAsync` (3792-3794) delegates to `createAsync`, so it is covered.
const divisor = Number(fixture.token_divisor ?? 3);
function countFor(role, content, name) {
    const parts = [role, content, name].filter(
        (value) => typeof value === 'string' && value.length > 0,
    );
    const text = parts.join(' ');
    return text.length ? Math.max(1, Math.floor(text.length / divisor)) : 0;
}

// `preparePromptsForChatCompletion` always merges its own system prompts into
// the collection (openai.js:1374-1493). Four of them carry no character data and
// so are not "inputs" a fixture would bother to declare:
//
//     { role: 'system',    content: impersonationPrompt, identifier: 'impersonate' }   1382
//     { role: 'system',    content: quietPrompt,         identifier: 'quietPrompt' }   1383
//     { role: 'assistant', content: bias,                identifier: 'bias' }          1385
//     { role: 'system',    content: DEFAULT,             identifier: 'enhanceDefinitions' } 2049
//
// The last two are already read through `prompts.has()`, so only the first two
// are reachable as a bare `prompts.get()`. `populateChatCompletion` does exactly
// that at 1224/1229 and 3792-3794 dereferences the result immediately: with the
// identifier missing the *reference itself* throws a TypeError before it ever
// reaches `populateChatHistory`, and the truncated snapshot then looks like the
// port "emitting more". That single gap was the whole of fixtures 01/03/04/07/08.
//
// Appending a stand-in is what the page amounts to here: a registered
// `impersonate` / `quietPrompt` holds only the quick-edit text (empty unless the
// user typed one), and an empty `quietPrompt` is dropped by the `content` guard
// at 1230 anyway. The value is a constant, not fixture input, so the two sides
// stay comparable. Collection order matters because `add(id, index)` assigns
// slots, but the extra entries land *after* every declared prompt and the
// reference only ever reads them by identifier.
const OPTIONAL_SYSTEM_PROMPTS = [
    { role: 'system', content: '', identifier: 'impersonate' },
    { role: 'system', content: '', identifier: 'quietPrompt' },
];

const declaredPrompts = fixture.prompts ?? [];
const optionalPrompts = OPTIONAL_SYSTEM_PROMPTS.filter(
    (spec) => !declaredPrompts.some((prompt) => prompt.identifier === spec.identifier),
);
const prompts = makeCollection([...declaredPrompts, ...optionalPrompts]);
for (const identifier of fixture.overridden_prompts ?? []) {
    // `PromptCollection.override(prompt, position)` takes a *Prompt*, not an
    // identifier (PromptManager.js:294-297); the fixture names the identifier for
    // readability, so it is looked up here.
    const prompt = prompts.get(identifier);
    if (prompt) {
        prompts.override(prompt, prompts.index(identifier));
    }
}
oracleSetPromptManager({
    serviceSettings: { names_behavior: namesBehavior(fixture.names_behavior) },
    log: () => {},
    isPromptDisabledForActiveCharacter: (identifier) =>
        (fixture.disabled_prompts ?? []).includes(identifier),
    // The page substitutes macros here. Prompt keeps a chat turn's text in
    // mes, so that is mapped onto content -- otherwise every history message
    // is built empty and dropped by insert (openai.js:4047).
    preparePrompt: (prompt) => ({
        ...(prompt ?? {}),
        content: prompt?.content ?? prompt?.mes ?? '',
    }),
    // PromptManager.js:1343-1347 -- {1,64} and the ASCII class, verbatim.
    isValidName: (name) => /^[a-zA-Z0-9_]{1,64}$/.test(String(name)),
    // PromptManager.js:1349-1351 -- strips everything outside the class, then
    // truncates to 64; a space becomes an underscore, it is not kept.
    sanitizeName: (name) =>
        String(name ?? '').replace(/[^a-zA-Z0-9_]/g, '_').substring(0, 64),
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

const originalFromPrompt = Message.fromPromptAsync;
Message.fromPromptAsync = async function fromPromptAsync(prompt) {
    if (!prompt) {
        // `populateChatCompletion` calls this with `prompts.get('impersonate')` /
        // `prompts.get('quietPrompt')` (1224/1229), and the real 3792-3794
        // dereferences its argument immediately. An absent optional prompt is
        // modelled as null, which is what the `?? null` at both call sites then
        // means; the absent prompts themselves are supplied by
        // `OPTIONAL_SYSTEM_PROMPTS` above.
        return null;
    }
    return originalFromPrompt.call(Message, prompt);
};

// Every place the engine counts, pointed at `countFor`. `createAsync` is the one
// that matters most: it builds the new-chat banner, the history turns and every
// dialogue example, so leaving it on the stub made the examples free (0 tokens)
// and its content empty, and a budget fixture then kept a group the reference
// drops. `setName` re-counts after a name is attached (3599-3602).
const originalCreateAsync = Message.createAsync;
Message.createAsync = async function createAsync(role, content, identifier) {
    const message = await originalCreateAsync.call(Message, role, content, identifier);
    message.tokens = countFor(message.role, message.content, '');
    return message;
};

// `setName` (3599-3602) only assigns the name and re-counts, so the override
// needs nothing from the original.
Message.prototype.setName = async function setName(name) {
    this.name = name;
    this.tokens = countFor(this.role, this.content, this.name);
};

const record = { fixture: fixture.name ?? path.basename(args.fixture, '.json'), steps: [] };

// The banner literals the run actually used, so a comparison or a bug report can
// see which strings the budget arithmetic was based on (openai.js:108-111).
record.prompts = {
    newChat: oaiSettings.new_chat_prompt,
    newExampleChat: oaiSettings.new_example_chat_prompt,
    continueNudge: oaiSettings.continue_nudge_prompt,
};

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

// Did `populateChatCompletion` get all the way through? `diff_assembly.py` only
// trusts the chat contents when the reference says so: a run that threw partway
// leaves a truncated collection, and comparing the port's full one against it
// would blame the port for the harness's own failure. `record.error` already
// makes the fixture diverge; this marks *why* the chat comparison is meaningless
// so the two are never confused.
let completed = false;

try {
    await populateChatCompletion(prompts, completion, {
        bias: fixture.bias ?? '',
        quietPrompt: fixture.quiet_prompt ?? '',
        type: fixture.type ?? null,
        // `script.js:4978-4979` appends `continue_postfix` while the prompt is
        // built, i.e. before this stage sees it, so the fixture already supplies
        // the cycle prompt in its final form. Omitting it here left the reference
        // without a `cyclePrompt`, so the `continueNudge` branch (907-927) was
        // never taken while the port's was -- the nudge then looked like a port
        // invention.
        cyclePrompt: fixture.cycle_prompt ?? null,
        // The fixture mirrors what `setOpenAIMessages` (openai.js:644) hands
        // over: `{ role, content, name, ... }`. Fixtures spell the body as `mes`
        // (the chat entry field) for readability, so both are accepted here.
        // `setOpenAIMessages` (openai.js:570-649) walks the chat backwards and
        // writes `messages[i]` with `i` counting down, so the array that reaches
        // `populateChatCompletion` is NEWEST FIRST (script.js:4830). The fixture
        // lists the turns oldest-first for readability, and the Python runner
        // mirrors that reversal with `set_openai_messages` -- without it here the
        // two sides feed opposite orders and every history comparison diverges.
        messages: [...(fixture.chat ?? [])].reverse().map((turn) => ({
            role: turn.role ?? (turn.is_user ? 'user' : 'assistant'),
            content: turn.content ?? turn.mes ?? '',
            name: turn.name ?? '',
            extra: {},
        })),
        // `setOpenAIMessageExamples` (openai.js:656-667) produces an array of
        // *blocks*, each an array of `{ role, content, name }` -- and
        // `populateDialogueExamples` reads a block entry's body from `content`
        // (1116), never from `mes`. The fixture spells it `mes` for readability
        // the same way a chat turn is spelled, so it is mapped here; leaving it
        // as `mes` gave the reference empty example text, and `getChat()` drops
        // an empty message (`openai.js:4125`), which read as "the reference
        // emits fewer messages".
        messageExamples: (fixture.examples ?? []).map((block) =>
            (Array.isArray(block) ? block : [block]).map((example) => ({
                content: example.content ?? example.mes ?? '',
                name: example.name ?? '',
            })),
        ),
    });
    completed = true;
} catch (error) {
    record.error = `the reference itself failed: ${error?.name ?? error}: ${error?.message ?? ''}`;
}

if (fixture.dump_internals) {
    record.internals = {
        promptCount: prompts.collection.length,
        hasChatHistory: prompts.has('chatHistory'),
        hasDialogueExamples: prompts.has('dialogueExamples'),
        chatHistoryIndex: prompts.index('chatHistory'),
        identifiers: prompts.collection.map((item) => item.identifier),
    };
}

await snapshot('final');

record.comparable = {
    chat: {
        ok: completed,
        reason: completed
            ? ''
            : `the reference threw before finishing the assembly: ${
                  record.error ?? 'unknown error'
              }`,
    },
};

const payload = JSON.stringify(record, null, 2);
if (args.out) {
    fs.writeFileSync(args.out, payload + '\n');
    process.stdout.write(`wrote ${args.out}\n`);
} else {
    process.stdout.write(payload + '\n');
}
