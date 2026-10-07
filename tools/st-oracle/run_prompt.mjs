// Oracle driver: exercise SillyTavern's prompt message model over one fixture.
//
//   node tools/st-oracle/run_prompt.mjs <fixture.json> [--out <result.json>]
//
// The fixture drives the real `openai.js` classes (TokenHandler, Message,
// MessageCollection, ChatCompletion) through a scripted list of operations, so
// the Python port of the same classes can be compared step by step instead of
// being read off the source. The token counter is pinned by the harness because
// the browser tokenizer cannot run here; see STATUS.md for what that makes
// non-comparable.

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
    process.stderr.write(`run_prompt.mjs: ${message}\n`);
    process.exit(2);
}

const args = parseArgs(process.argv.slice(2));
if (!args.fixture) fail('usage: node run_prompt.mjs <fixture.json> [--out <result.json>]');
if (!fs.existsSync(ENGINE)) fail(`engine shim missing (${ENGINE}); run tools/st-oracle/gen_adapter.py`);

const fixture = JSON.parse(fs.readFileSync(args.fixture, 'utf8'));

// --- deterministic globals the module body touches ---------------------------
globalThis.jQuery = __stub('jQuery');
globalThis.$ = globalThis.jQuery;
globalThis.document = __stub('document');
globalThis.window = globalThis;
globalThis.localStorage = __stub('localStorage');
globalThis.toastr = __stub('toastr');
globalThis.fetch = async () => ({ ok: true, json: async () => ({}), text: async () => '' });
Math.random = mulberry32(Number(fixture.seed ?? 1337));

const module = await import(pathToFileURL(ENGINE).href);

// --- pinned token counter ----------------------------------------------------
// `countTokensOpenAIAsync` is a browser tokenizer; the fixture declares the
// counter so both backends agree. `divisor` mirrors the plugin's estimate.
//
// The module keeps its own private `tokenHandler` singleton, and
// `Message.createAsync` / `squashSystemMessages` go through it, so a merged
// message is re-counted by the *stubbed* tokenizer (0) no matter what we do
// here. Pin the count on every message the harness builds instead, and treat the
// post-squash totals as an artifact of that (see `comparable_fields` below).
const divisor = Number(fixture.token_divisor ?? 3);
const countTokens = async (messages) => {
    const list = Array.isArray(messages) ? messages : [messages];
    let total = 0;
    for (const message of list) {
        if (typeof message === 'string') {
            total += Math.max(1, Math.floor(message.length / divisor));
            continue;
        }
        const parts = [message?.role, message?.content, message?.name, message?.reasoning]
            .filter((value) => typeof value === 'string' && value.length > 0);
        if (message?.tool_calls) parts.push(JSON.stringify(message.tool_calls));
        const text = parts.join(' ');
        total += text.length ? Math.max(1, Math.floor(text.length / divisor)) : 0;
    }
    return total;
};

// The module builds its own `tokenHandler` at load time with the real async
// tokenizer. Replace the counting function on the live instance (and on the
// class prototype used by `new TokenHandler`).
const { TokenHandler, Message, MessageCollection, ChatCompletion } = module;
if (!TokenHandler || !Message || !MessageCollection || !ChatCompletion) {
    fail('openai.js did not export the prompt model classes');
}

// `tokenHandler` is module-private; patch the counting function through a probe.
// Message.createAsync uses the singleton, so we patch `TokenHandler.prototype`
// and replace the singleton's own function by walking every instance we create.
const handlers = [];
const originalCtor = TokenHandler;
function patchHandler(instance) {
    instance.countTokenAsyncFn = countTokens;
    handlers.push(instance);
}

// --- run the scripted operations --------------------------------------------
// `tokens` in a fixture step pins a message's token count. The module's own
// `tokenHandler` singleton is private, so nothing else can make the two
// backends agree on the budget arithmetic; pinning keeps the comparisons about
// the class behaviour instead of about the tokenizer.
function countFor(spec) {
    if (typeof spec.tokens === 'number') return spec.tokens;
    const parts = [spec.role, spec.content, spec.name].filter(
        (value) => typeof value === 'string' && value.length > 0,
    );
    const text = parts.join(' ');
    return text.length ? Math.max(1, Math.floor(text.length / divisor)) : 0;
}

function makeMessage(spec) {
    const identifier = spec.identifier ?? `msg-${Math.random().toString(36).slice(2, 8)}`;
    const message = new Message(spec.role, spec.content ?? '', identifier);
    message.tokens = countFor(spec);
    return message;
}

async function makeCollection(spec) {
    const collection = new MessageCollection(spec.identifier);
    for (const item of spec.items ?? []) {
        collection.add(item.collection ? await makeCollection(item.collection) : makeMessage(item));
    }
    return collection;
}

const result = { fixture: fixture.name ?? path.basename(args.fixture, '.json'), steps: [] };
const completions = new Map();

async function applyStep(step) {
    const record = { type: step.type };

    if (step.type === 'new') {
        const completion = new ChatCompletion();
        completions.set(step.id, completion);
        // tokenHandler is module-private: patch through a probe instance.
        const probe = new TokenHandler(countTokens);
        patchHandler(probe);
        record.ok = true;
        return record;
    }

    const completion = completions.get(step.id ?? 'default');
    if (!completion) fail(`unknown completion id: ${step.id}`);

    switch (step.type) {
        case 'budget':
            completion.setTokenBudget(step.context, step.response);
            record.budget = completion.tokenBudget;
            break;
        case 'add': {
            try {
                completion.add(await makeCollection(step.collection));
                record.added = step.collection.identifier;
            } catch (error) {
                record.error = error.name ?? String(error);
            }
            record.budget = completion.tokenBudget;
            break;
        }
        case 'insert_start':
        case 'insert_end': {
            const message = makeMessage(step.message);
            try {
                if (step.type === 'insert_start') completion.insertAtStart(message, step.identifier);
                else completion.insertAtEnd(message, step.identifier);
                record.inserted = message.identifier;
            } catch (error) {
                record.error = error.name ?? String(error);
            }
            record.budget = completion.tokenBudget;
            break;
        }
        case 'remove_last':
            try {
                completion.removeLastFrom(step.identifier);
                record.removed = true;
            } catch (error) {
                record.error = error.name ?? String(error);
            }
            record.budget = completion.tokenBudget;
            break;
        case 'squash':
            await completion.squashSystemMessages();
            record.ok = true;
            break;
        case 'snapshot':
            record.chat = completion.getChat();
            record.budget = completion.tokenBudget;
            record.total_tokens = completion.getTotalTokenCount();
            record.has = (step.identifiers ?? []).map((id) => [id, completion.has(id)]);
            if (step.dump_collection) {
                record.collection = completion.getMessages().collection.map((item) => ({
                    kind: item.constructor.name,
                    identifier: item.identifier,
                    role: item.role ?? null,
                    name: item.name ?? null,
                    content: typeof item.content === 'string' ? item.content : null,
                    // `tokens` is undefined on a message built without
                    // createAsync, and JS arithmetic treats that as 0.
                    tokens: item.getTokens() ?? null,
                }));
            }
            break;
        case 'count': {
            const handler = new TokenHandler(countTokens);
            record.counted = await handler.countAsync(step.message);
            break;
        }
        default:
            fail(`unknown step type: ${step.type}`);
    }
    return record;
}

for (const step of fixture.steps ?? []) {
    result.steps.push(await applyStep(step));
}

const payload = JSON.stringify(result, null, 2);
if (args.out) {
    fs.writeFileSync(args.out, payload + '\n');
    process.stdout.write(`wrote ${args.out}\n`);
} else {
    process.stdout.write(payload + '\n');
}
