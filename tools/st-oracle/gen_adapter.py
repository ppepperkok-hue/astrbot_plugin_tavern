"""Generate the Node shim tree that lets SillyTavern's World Info engine run headless.

Reads the vendored SillyTavern snapshot (``research/_raw/st_public_scripts_world-info.js``)
and materialises a runnable ``public/`` tree under ``tools/st-oracle/.build/``:

* ``public/scripts/world-info.js`` -- byte copy of the snapshot, never edited
* one stub module per import specifier, each exporting the names world-info.js
  asks for, with hand written overrides for the names that must behave

Nothing here talks to the network: the only HTTP call the engine makes
(``/api/worldinfo/get``) is answered by ``runtime.mjs`` from the fixture.

Usage:
    python tools/st-oracle/gen_adapter.py            # (re)generate .build/
    python tools/st-oracle/gen_adapter.py --check    # exit 1 if regeneration is needed
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SRC = REPO / "research" / "_raw" / "st-src" / "world-info.js"
BUILD = HERE / ".build"

#: Every module found in the shim tree is a stub, so the engine import must be
#: resolvable from a real file that we generate ourselves.
ENGINE_REL = "public/scripts/world-info.js"

#: Engine sources copied byte for byte into the shim tree. The prompt-assembly
#: stage (S2) compares against ``openai.js``, and the provider-format stage (S4)
#: against ``prompt-converters.js``, so all of them live side by side; each gets
#: the same generated stub imports.
ENGINE_FILES: dict[str, str] = {
    "public/scripts/world-info.js": "world-info.js",
    "public/scripts/openai.js": "openai.js",
    "public/scripts/prompt-converters.js": "prompt-converters.js",
}

#: Extra named exports appended to an engine copy. The browser bundle exposes
#: these as globals; a module build does not, and the assembly oracle needs
#: ``populateChatCompletion``. The appended line only *names* what is already
#: defined at module scope -- nothing in the engine is rewritten.
#: Names a specifier's generated stub must NOT re-declare, because the source
#: emitted for it already provides them (see `extract_prompt_manager_source`).
PROVIDED_BY_SOURCE: dict[str, frozenset[str]] = {
    "./PromptManager.js": frozenset(
        {
            "Prompt",
            "PromptCollection",
            "INJECTION_POSITION",
            "PromptManager",
            "chatCompletionDefaultPrompts",
            "promptManagerDefaultPromptOrders",
        }
    ),
}

ENGINE_EXTRA_EXPORTS: dict[str, tuple[str, ...]] = {
    "public/scripts/openai.js": ("populateChatCompletion",),
}

#: Module-scope private state an engine's generated shim has to declare for
#: itself. Empty today: the one piece of state in play (`_convConfig`) belongs to
#: the `./util.js` override that reads it, and every engine takes it from there.
ENGINE_EXTRA_DECLS: dict[str, dict[str, str]] = {}
ENGINE_EXTRA_CODE: dict[str, str] = {
    "public/scripts/prompt-converters.js": """
// Oracle-only hook (see gen_adapter.py). `prompt-converters.js` reads two config
// values at module scope -- `promptPlaceholder` (:4) and `gemini.thoughtSignatures`
// (:34) -- and `mistral.enablePrefix` at call time. The runner has to set them
// *before* the module is imported, and it cannot do that by importing a second
// name from `./util.js`: the engine's import list is the snapshot's own, copied
// byte for byte. So the hook lives here and forwards to the `./util.js` override's
// `setConvConfig`, which owns the single `_convConfig` object that `getConfigValue`
// reads. The values go through a copy because the runner's config object is
// shared with the fixture and must not gain the forwarding key.
export function oracleSetConvConfig(values) {
    // Copy: the runner's object is the fixture's and must not gain the key below.
    const forwarded = Object.assign({}, values);
    forwarded.__stConvConfigApply = true;
    setConvConfig(forwarded);
    delete forwarded.__stConvConfigApply;
}
// The rule below is what puts `setConvConfig` in this engine's generated import
// list -- the appended code cannot import by name itself. Keep the spelling.
import { setConvConfig } from './util.js';
""",
    "public/scripts/openai.js": """
// Oracle-only hook (see gen_adapter.py): the browser assigns this in setup(),
// and the assembly tail reads `power_user.pin_examples` -- which is a const
// object, so the fixture mutates it instead of replacing it.
export function oracleSetPromptManager(manager) { promptManager = manager; }
// `selected_group` is bound to the group-chats stub, so a fixture that is *not*
// a group chat mirrors the normal prompt onto the group variant instead.
export function oracleApplyGroupChat(isGroup) {
    if (!isGroup) oai_settings.new_group_chat_prompt = oai_settings.new_chat_prompt;
}
// The fixture supplies the prompt strings so the two sides compare like for
// like instead of depending on i18n defaults. Each key is independent: the
// example-chat banner and the continue nudge carry different literals
// (openai.js:110-111), so they must not fall back to the new-chat one.
export function oracleSetPrompts({ newChat, newGroupChat, newExampleChat, continueNudge }) {
    if (typeof newChat === 'string') oai_settings.new_chat_prompt = newChat;
    if (typeof newGroupChat === 'string') oai_settings.new_group_chat_prompt = newGroupChat;
    if (typeof newExampleChat === 'string') oai_settings.new_example_chat_prompt = newExampleChat;
    if (typeof continueNudge === 'string') oai_settings.continue_nudge_prompt = continueNudge;
}
""",
}

HEADER = """// GENERATED by tools/st-oracle/gen_adapter.py -- do not edit.
import {{ __stub }} from '{runtime}';
"""

#: import specifier -> {exported name: JS expression/statement}
#: Anything not listed falls back to a permissive Proxy stub.
OVERRIDES: dict[str, dict[str, str]] = {
    "../lib.js": {
        "Fuse": "class Fuse { constructor() {} search() { return []; } }",
    },
    "../script.js": {
        "substituteParams": "((s) => String(s ?? ''))",
        # `substituteParamsExtended` resolves the named macros a prompt may carry.
        # `populateChatHistory`'s continue nudge passes `{ lastChatMessage }`
        # (openai.js:911), so a stub that returns '' silently empties the nudge
        # and the message is then dropped as empty by getChat.
        "substituteParamsExtended": (
            "((s, args) => String(s ?? '').replace(/\\{\\{\\s*(\\w+)\\s*\\}\\}/g,"
            " (m, key) => (args && key in args ? String(args[key]) : m)))"
        ),
        # The injection loop bound and the in-chat extension prompt getter are
        # real values in the page, not stubs: getExtensionPromptMaxDepth is
        # literally "return MAX_INJECTION_DEPTH" (script.js:500; the computed
        # version at script.js:3279-3284 is commented out upstream). As stubs the
        # bound collapsed to 0 and the getter returned a truthy proxy, which made
        # populationInjectionPrompts splice three phantom "0" messages into
        # every injection depth.
        "MAX_INJECTION_DEPTH": "10000",
        "getExtensionPromptMaxDepth": "(() => 10000)",
        "getExtensionPrompt": "((..._args) => '')",
        "chat_metadata": "{}",
        "this_chid": "0",
        "characters": "[]",
        "eventSource": "({ emit: async () => [true], on: () => {}, once: () => {} })",
        "event_types": "new Proxy({}, { get: (t, p) => String(p), has: () => true })",
        "saveSettings": "(() => {})",
        "saveMetadata": "(() => {})",
        "getRequestHeaders": "(() => ({ 'Content-Type': 'application/json' }))",
        "saveCharacterDebounced": "(() => {})",
        "getExtensionPromptByName": "(async () => '')",
        "extension_prompt_roles": "({ SYSTEM: 0, USER: 1, ASSISTANT: 2 })",
        "name1": "'User'",
        "menu_type": "null",
        "getCurrentChatId": "(() => 'oracle-chat')",
        "create_save": "(() => {})",
        "createOrEditCharacter": "(() => {})",
        "getOneCharacter": "(() => null)",
        "select_selected_character": "(() => {})",
    },
    "./utils.js": {
        # Mirrors SillyTavern's own hash: the engine uses it for timed effects.
        "getStringHash": (
            "((s) => { let h = 0; const str = String(s ?? '');"
            " for (let i = 0; i < str.length; i++) { h = (h << 5) - h + str.charCodeAt(i) | 0; }"
            " return h; })"
        ),
        "debounce": "((fn) => fn)",
        "escapeRegex": "((s) => String(s ?? '').replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'))",
        "getCharaFilename": "(() => '')",
        "uuidv4": "(() => '00000000-0000-4000-8000-000000000000')",
        "onlyUnique": "((v, i, a) => a.indexOf(v) === i)",
        "normalizeArray": "((a) => a)",
        "parseStringArray": (
            "((s) => (Array.isArray(s) ? s : String(s ?? '').split(',').map(x => x.trim()).filter(Boolean)))"
        ),
        "isTrueBoolean": "((v) => v === true || v === 'true')",
        "isFalseBoolean": "((v) => v === false || v === 'false')",
        "equalsIgnoreCaseAndAccents": (
            "((a, b) => String(a).toLowerCase() === String(b).toLowerCase())"
        ),
        "getSanitizedFilename": "((s) => String(s ?? ''))",
        "structuredClone": "((v) => globalThis.structuredClone(v))",
    },
    "./extensions.js": {
        "extension_settings": (
            "({ extensionPrompts: {}, note: { allowWIScan: false, position: 0, depth: 4, role: 0 } })"
        ),
        "getContext": (
            "(() => ({ extensionPrompts: { note: { value: '', scan: false } },"
            " setExtensionPrompt: () => {}, chat: [], tagMap: {} }))"
        ),
    },
    "./authors-note.js": {
        # A plain boolean, not a thunk: world-info.js branches on it directly.
        "shouldWIAddPrompt": "false",
        "metadata_keys": "({ position: 'position', depth: 'depth', role: 'role' })",
        "NOTE_MODULE_NAME": "'note'",
    },
    "./filters.js": {
        "FilterHelper": "class FilterHelper { constructor(fn) { this.update = fn; } filter() { return true; } }",
        "FILTER_TYPES": "({})",
    },
    "./tokenizers.js": {
        "getTokenCountAsync": "((s) => globalThis.__ORACLE.countTokens(s))",
    },
    "./power-user.js": {
        "power_user": (
            "({ persona_description: '', persona_description_lorebook: '',"
            " persona_description_position: -1, console_log_prompts: false })"
        ),
    },
    "./constants.js": {
        "debounce_timeout": "({ relaxed: 1000, short: 100 })",
        "GENERATION_TYPE_TRIGGERS": "({})",
    },
    "./tags.js": {"getTagKeyForEntity": "(() => '')"},
    "./RossAscends-mods.js": {"isMobile": "false"},
    "./extensions/regex/engine.js": {
        "getRegexedString": "((s) => s)",
        "regex_placement": "({})",
    },
    "./util/StructuredCloneMap.js": {
        # The real map, so ``loadWorldInfo``'s cache behaves like production.
        "StructuredCloneMap": (
            "class StructuredCloneMap {"
            " #m = new Map();"
            " constructor({ cloneOnGet = false, cloneOnSet = false } = {})"
            " { this.cloneOnGet = cloneOnGet; this.cloneOnSet = cloneOnSet; }"
            " get size() { return this.#m.size; }"
            " has(k) { return this.#m.has(k); }"
            " get(k) { const v = this.#m.get(k); return this.cloneOnGet && v !== undefined"
            " ? globalThis.structuredClone(v) : v; }"
            " set(k, v) { this.#m.set(k, this.cloneOnSet ? globalThis.structuredClone(v) : v); return this; }"
            " delete(k) { return this.#m.delete(k); }"
            " clear() { this.#m.clear(); }"
            " keys() { return this.#m.keys(); }"
            " values() { return this.#m.values(); }"
            " [Symbol.iterator]() { return this.#m[Symbol.iterator](); } }"
        ),
    },
    "./util/AccountStorage.js": {
        "accountStorage": "({ getItem: () => '0', setItem: () => {}, removeItem: () => {} })",
    },
    "./i18n.js": {
        "t": "((strings, ...vals) => (Array.isArray(strings) ? strings.raw.join('') : String(strings)))",
    },
    "./personas.js": {
        "getOrCreatePersonaDescriptor": "(() => null)",
        "setPersonaDescription": "(() => {})",
        "user_avatar": "''",
    },
    # `prompt-converters.js` imports these two from `./util.js`. `util.js` itself
    # pulls in lodash and the whole data-root machinery, so it is not worth
    # vendoring for two functions; the overrides below reproduce the two exactly
    # as measured against the snapshot.
    #
    # `getConfigValue` (util.js:88-109) resolves `keyToEnv(key)` from the
    # environment first, then the server config file via lodash `get`, then the
    # default, and finally applies the `'number'` / `'boolean'` converter. The
    # oracle has no config file, so it exposes the environment route under the
    # one name a fixture can set: `ST_<UPPER_SNAKE>`, with dots and dashes folded
    # to underscores -- the same spelling `keyToEnv` produces.
    #
    # `tryParse` (util.js:571-577) is `JSON.parse` and `undefined` on failure. The
    # `undefined` matters: `convertGooglePrompt` writes
    # `tryParse(args) ?? args`, so a fallback to the raw string is the caller's,
    # not the parser's.
    "./util.js": {
        # The config is read *inside* `prompt-converters.js` at module scope
        # (``:4`` and ``:34``), so it has to be in place before that module is
        # evaluated -- which rules out a hook the module exports. The runner puts
        # the fixture's config on ``globalThis`` instead, and this module -- the
        # first thing the engine evaluates -- copies it in. `setConvConfig` stays
        # as the hook for anything that wants to change the values afterwards
        # (``mistral.enablePrefix`` is read at call time, ``:709``).
        "_convConfig": "(Object.assign({}, globalThis.__stConvConfig || {}))",
        "getConfigValue": (
            "((key, defaultValue = null, typeConverter = null) => {"
            " const envKey = 'ST_' + String(key).replace(/[.-]/g, '_')"
            ".replace(/([a-z0-9])([A-Z])/g, '$1_$2').toUpperCase();"
            " const raw = envKey in process.env ? process.env[envKey]"
            " : (_convConfig[key] !== undefined ? _convConfig[key] : null);"
            " const value = raw === null ? defaultValue : raw;"
            " if (typeConverter === 'number') {"
            " const parsed = parseFloat(value);"
            " return Number.isNaN(parsed) ? defaultValue : parsed; }"
            " if (typeConverter === 'boolean') {"
            " return value === true || value === 'true' || value === 1 || value === '1'; }"
            " return value; })"
        ),
        "setConvConfig": "((values) => { Object.assign(_convConfig, values); })",
        "tryParse": ("((str) => { try { return JSON.parse(str); } catch { return undefined; } })"),
    },
    # `import crypto from 'node:crypto'` -- a default import of a builtin.
    "node:crypto": {
        "default": "await import('node:crypto').then(m => m.default)",
    },
}

IMPORT_RE = re.compile(
    r"import\s*(?:\{([^}]*)\}|\*\s+as\s+(\w+)|(\w+))\s*from\s*'([^']+)'",
    re.DOTALL,
)


def parse_imports(text: str) -> dict[str, set[str]]:
    imports: dict[str, set[str]] = {}
    for match in IMPORT_RE.finditer(text):
        named, star, default, spec = match.groups()
        names = imports.setdefault(spec, set())
        if named:
            for chunk in named.split(","):
                chunk = chunk.strip()
                if not chunk:
                    continue
                names.add(chunk.split(" as ")[-1].strip())
        if star:
            names.add(f"*:{star}")
        if default:
            names.add(f"default:{default}")
    return imports


def resolve_specifier(spec: str) -> Path:
    """Resolve a relative import specifier against ``public/scripts/``."""
    base = (BUILD / "public" / "scripts").resolve()
    return (base / spec).resolve()


def generate(verbose: bool = True) -> dict:
    if not SRC.is_file():
        raise SystemExit(f"missing SillyTavern snapshot: {SRC}")

    if BUILD.exists():
        shutil.rmtree(BUILD)
    scripts = BUILD / "public" / "scripts"
    scripts.mkdir(parents=True)
    (BUILD / "package.json").write_text(
        json.dumps({"name": "st-oracle-build", "private": True, "type": "module"}, indent=2) + "\n",
        encoding="utf-8",
    )

    shutil.copyfile(HERE / "runtime.mjs", BUILD / "runtime.mjs")

    imports: dict[str, set[str]] = {}
    engines: list[tuple[str, Path]] = []
    for rel, snapshot in ENGINE_FILES.items():
        source_path = SRC.parent / snapshot
        if not source_path.is_file():
            raise SystemExit(f"missing SillyTavern snapshot: {source_path}")
        text = source_path.read_text(encoding="utf-8")
        extras = ENGINE_EXTRA_EXPORTS.get(rel, ())
        extra_code = ENGINE_EXTRA_CODE.get(rel, "")
        extra_decls = ENGINE_EXTRA_DECLS.get(rel, {})
        if extras or extra_code:
            names = ", ".join(extras)
            shim = "\n// Oracle-only shim (see gen_adapter.py).\n"
            # State the shim closes over, before the code that closes over it.
            for private_name, private_value in extra_decls.items():
                shim += f"const {private_name} = {private_value};\n"
            if extra_code:
                shim += extra_code
            if names:
                shim += f"export {{ {names} }};\n"
            text = text + shim
        target = BUILD / rel
        target.write_text(text, encoding="utf-8")
        engines.append((rel, source_path))
        for spec, names in parse_imports(text).items():
            imports.setdefault(spec, set()).update(names)

    problems: list[str] = []
    modules = 0
    names_total = 0
    real_total = 0
    report: list[str] = []

    for spec, names in sorted(imports.items()):
        # A generated module would be shadowed by node's own resolution for a
        # bare specifier, so `node:`-prefixed builtins are the only non-relative
        # imports allowed through: node resolves them and we skip them here.
        if spec.startswith("node:"):
            if spec not in OVERRIDES:
                problems.append(f"non-relative import {spec!r} has no OVERRIDES entry")
            continue
        if not spec.startswith("."):
            problems.append(f"non-relative import {spec!r} (would leave the shim tree)")
            continue
        target = resolve_specifier(spec)
        if not str(target).startswith(str(BUILD)):
            problems.append(f"import {spec!r} escapes the build tree -> {target}")
            continue

        overrides = OVERRIDES.get(spec, {})
        lines = [HEADER.format(runtime=_runtime_relpath(target))]
        extra_decls = ENGINE_EXTRA_DECLS.get(spec, {})
        # Private module state an override closes over does not appear in the
        # import list, so it has to be emitted explicitly. (`_convConfig` backs
        # getConfigValue in the `./util.js` overrides; an engine that declares the
        # same name for itself -- `prompt-converters.js` -- takes it from
        # `ENGINE_EXTRA_DECLS` instead, because its hook has to assign into the
        # very object `getConfigValue` reads, not a second copy of it.)
        for private_name, private_value in overrides.items():
            if private_name.startswith("_"):
                lines.append(f"const {private_name} = {private_value};")
        for private_name, private_value in extra_decls.items():
            if private_name not in overrides:
                lines.append(f"const {private_name} = {private_value};")
        if spec == "./PromptManager.js":
            lines.append(extract_prompt_manager_source())
        real: list[str] = []
        missing: list[str] = []

        # Prompt / PromptCollection / INJECTION_POSITION come from the extracted
        # source above; emitting the stub line too would be a duplicate export.
        provided = PROVIDED_BY_SOURCE.get(spec, frozenset())

        for name in sorted(names):
            if name in provided:
                real.append(name)
                continue
            if name.startswith("*:"):
                lines.append(
                    f"export const {name[2:]} = new Proxy({{}}, {{ get: () => __stub('{spec}') }});"
                )
                continue
            if name.startswith("default:"):
                lines.append(f"export default __stub('{spec}');")
                continue
            if name in overrides:
                lines.append(f"export const {name} = {overrides[name]};")
                real.append(name)
            else:
                lines.append(f'export const {name} = __stub("{name}");')
                missing.append(name)

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        modules += 1
        names_total += len([n for n in names if not n.startswith(("*:", "default:"))])
        real_total += len(real)
        report.append(
            f"{spec:<40} names={len(names):>3} overrides={len(real):>3}"
            + (f"  stubbed={','.join(missing)}" if missing else "")
        )

    for rel, _source_path in engines:
        if not (BUILD / rel).is_file():
            problems.append(f"engine copy missing: {rel}")

    if verbose:
        for rel, source_path in engines:
            size = (BUILD / rel).stat().st_size
            print(f"source : {source_path.relative_to(REPO)} ({size} bytes)")
        print(f"build  : {BUILD.relative_to(REPO)}")
        for line in report:
            print("  " + line)
        print(
            f"modules={modules} named={names_total} overrides={real_total} stubs={names_total - real_total}"
        )

    if problems:
        for p in problems:
            print("PROBLEM: " + p, file=sys.stderr)
        raise SystemExit(2)

    return {"modules": modules, "named": names_total, "overrides": real_total}


def extract_prompt_manager_source() -> str:
    """The parts of PromptManager.js the assembly path needs, verbatim.

    openai.js imports Prompt / PromptCollection / INJECTION_POSITION from this
    module. Rendering it as a permissive stub makes new Prompt(chatPrompt)
    produce a proxy whose every read is fake, so chat turns lose their role and
    content and insert drops them; it also makes INJECTION_POSITION.ABSOLUTE
    never match. Importing the real module is not an option either: it imports
    openai.js back, so the cycle fails to link.

    The needed declarations are therefore lifted out of the vendored snapshot:
    the two classes and the enum are copied verbatim (classes matched by brace
    depth, because the next declaration is 1.8k lines away), and the singleton
    exposes the methods the assembly calls. Nothing is hand-written from memory.
    """
    source = (SRC.parent / "PromptManager.js").read_text(encoding="utf-8")

    def slice_class(marker: str) -> str:
        start = source.index(marker)
        depth = 0
        for index in range(source.index("{", start), len(source)):
            char = source[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return source[start : index + 1].rstrip()
        raise SystemExit(f"unbalanced braces while extracting {marker!r}")

    prompt_class = slice_class("class Prompt {")
    collection_class = slice_class("export class PromptCollection {")
    return "\n\n".join(
        [
            "// --- copied verbatim from research/_raw/st-src/PromptManager.js ---",
            "export const INJECTION_POSITION = { RELATIVE: 0, ABSOLUTE: 1 };",
            # The two constants the Prompt constructor reads (PromptManager.js:96-97).
            "export const DEFAULT_DEPTH = 4;",
            "export const DEFAULT_ORDER = 100;",
            prompt_class.replace("class Prompt {", "export class Prompt {", 1),
            collection_class,
            """export class PromptManager {}

// default_settings (PromptManager.js) spreads these, so they must exist and be
// spreadable even though the oracle replaces the manager itself.
export const chatCompletionDefaultPrompts = {};
export const promptManagerDefaultPromptOrders = {};

export const promptManager = {
    serviceSettings: {},
    log: () => {},
    isPromptDisabledForActiveCharacter: () => false,
    // preparePrompt substitutes macros in the page; the oracle keeps the text and
    // lets the fixture own the content.
    preparePrompt: (prompt, content) => ({
        ...(prompt ?? {}),
        content: content ?? prompt?.content ?? '',
    }),
    isValidName: (name) => typeof name === 'string' && /^[\\w' -]+$/.test(name),
    sanitizeName: (name) => String(name ?? '').replace(/[^\\w' -]+/g, '_'),
};
""",
        ]
    )


def _runtime_relpath(target: Path) -> str:
    import os.path

    rel = os.path.relpath(BUILD / "runtime.mjs", target.parent)
    rel = rel.replace("\\", "/")
    if not rel.startswith("."):
        rel = "./" + rel
    return rel


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate into a temp tree and compare (CI-friendly smoke check)",
    )
    args = parser.parse_args()
    if args.check:
        before = (
            {p: p.read_bytes() for p in BUILD.rglob("*") if p.is_file()} if BUILD.exists() else {}
        )
        generate(verbose=False)
        after = {p: p.read_bytes() for p in BUILD.rglob("*") if p.is_file()}
        drift = sorted(set(before) ^ set(after)) + sorted(
            p for p in set(before) & set(after) if before[p] != after[p]
        )
        if drift:
            print("adapter drift:", ", ".join(str(p.relative_to(BUILD)) for p in drift))
            return 1
        print("adapter is up to date")
        return 0
    generate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
