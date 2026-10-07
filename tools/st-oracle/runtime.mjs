// Shared runtime for the generated SillyTavern shim tree.
//
// GENERATED modules import `__stub` from here; the oracle harness in run.mjs
// attaches the per-fixture state on `globalThis.__ORACLE` before it imports the
// engine. This file never imports the engine, so it is safe to load first.

/**
 * Permissive stand-in for anything the headless run cannot provide (DOM, jQuery,
 * toasts, ...). Reads, calls and constructions all return another stub instead of
 * throwing, which keeps every code path the engine touches on the hot scan loop
 * from exploding while still making accidental *use* obvious (the value stringifies
 * to the property path that produced it).
 */
export function __stub(name) {
    const target = function () { return __stub(name + '()'); };
    return new Proxy(target, {
        get(t, p) {
            if (p === Symbol.toPrimitive) return () => 0;
            if (p === 'then') return undefined;
            if (p === 'toString') return () => name;
            if (p === Symbol.iterator) return function* () {};
            if (p === 'length') return 0;
            return __stub(name + '.' + String(p));
        },
        apply() { return __stub(name + '()'); },
        construct() { return __stub('new ' + name); },
        has() { return true; },
    });
}

/**
 * Deterministic PRNG (mulberry32). The engine rolls probabilities with
 * `Math.random() * 100`; pinning the stream makes a fixture reproducible across
 * runs. The *values* are NOT comparable with Python's `random.Random` -- see
 * tools/st-oracle/STATUS.md ("不可比点").
 */
export function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
        a = (a + 0x6d2b79f5) >>> 0;
        let t = a;
        t = Math.imul(t ^ (t >>> 15), t | 1);
        t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}

/**
 * Token counter that hands Python an *exactly* comparable budget.
 *
 * SillyTavern ships several real tokenizers (tiktoken / sentencepiece) and the
 * Python port ships tiktoken as an optional dependency, so byte-for-byte budget
 * parity is impossible in general. The oracle therefore pins both sides to one
 * documented heuristic: `max(1, floor(len / 3))` for non-empty text, `0` for
 * empty text. Everything the budget arithmetic does is then visible in the diff.
 */
export function countTokens(text) {
    const s = String(text ?? '');
    if (!s) return 0;
    return Math.max(1, Math.floor(s.length / 3));
}

/** Read a fixture book by name, going through the same path as a real load. */
export function worldFor(name) {
    const worlds = globalThis.__ORACLE_WORLDS ?? {};
    return Object.prototype.hasOwnProperty.call(worlds, name) ? worlds[name] : null;
}
