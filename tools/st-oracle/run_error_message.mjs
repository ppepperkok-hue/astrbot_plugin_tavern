// Oracle driver: probe `getChatCompletionErrorMessage` directly.
//
// `openai.js` does not export it (it is module-private at :1635), and it is one
// function, so `gen_adapter.py` gets a one-line hook that names it rather than a
// whole new oracle family with fixtures. This runner prints the reference's answer
// for each case so the Python test file can assert the same expectations against
// the port -- the reference is the source of truth, the test file is the harness.
//
//   node tools/st-oracle/run_error_message.mjs

import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { __stub, mulberry32 } from './runtime.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ENGINE = path.join(HERE, '.build', 'public', 'scripts', 'openai.js');

// `openai.js` is a browser module: importing it runs its module-scope DOM wiring
// (the first `$('#save_proxy').on(...)` is at :6541), so the same globals the
// assembly oracle installs are required before the import.
globalThis.jQuery = __stub('jQuery');
globalThis.$ = globalThis.jQuery;
globalThis.document = __stub('document');
globalThis.window = globalThis;
globalThis.localStorage = __stub('localStorage');
globalThis.toastr = __stub('toastr');
globalThis.fetch = async () => ({ ok: true, json: async () => ({}), text: async () => '' });
Math.random = mulberry32(1337);

const { oracleGetChatCompletionErrorMessage } = await import(pathToFileURL(ENGINE).href);
if (typeof oracleGetChatCompletionErrorMessage !== 'function') {
    process.stderr.write(
        'run_error_message.mjs: the engine has no oracleGetChatCompletionErrorMessage hook; ' +
            'regenerate with tools/st-oracle/gen_adapter.py\n',
    );
    process.exit(2);
}

// One case per branch of the reference, including the two that make it worth
// porting: a structured error object (which `str()` would render as a dict) and a
// body with no message anywhere (which must fall back to the HTTP status text).
const CASES = [
    ['string body', 'upstream exploded', ''],
    ['error is a string', { error: 'bad key' }, ''],
    ['error.message', { error: { message: 'model overloaded' } }, ''],
    ['error.code when message is absent', { error: { code: 'rate_limit_exceeded' } }, ''],
    ['error.type when message and code are absent', { error: { type: 'invalid_request' } }, ''],
    ['message wins over code', { error: { code: 'x', message: 'real reason' } }, ''],
    ['nested detail.error', { detail: { error: { message: 'nested reason' } } }, ''],
    ['top-level message, no error key', { message: 'quota exceeded' }, ''],
    ['empty body falls back to status text', {}, 'Internal Server Error'],
    ['error present beats status text', { error: { message: 'real reason' } }, 'Bad Gateway'],
    ['nothing at all', {}, ''],
    ['error is an empty object, status text used', { error: {} }, 'Service Unavailable'],
    ['error is a number', { error: 429 }, ''],
    ['detail without error', { detail: { message: 'other shape' } }, 'Teapot'],
];

const out = { hook: 'getChatCompletionErrorMessage (openai.js:1635-1639)', cases: [] };
for (const [label, data, statusText] of CASES) {
    let result;
    try {
        result = oracleGetChatCompletionErrorMessage(structuredClone(data), { statusText });
    } catch (error) {
        result = `THREW ${error?.name}: ${error?.message}`;
    }
    out.cases.push({ label, data, status_text: statusText, result });
}

process.stdout.write(JSON.stringify(out, null, 2) + '\n');
