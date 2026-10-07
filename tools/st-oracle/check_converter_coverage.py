"""Does the port export everything the reference module exports?

    python tools/st-oracle/check_converter_coverage.py
    python tools/st-oracle/check_converter_coverage.py --verbose
    python tools/st-oracle/check_converter_coverage.py --port-file .scratch/port_copy.py

Why this exists
---------------
``diff_converters.py --all`` can only compare what a *fixture* names. A fixture
declares one reference export, the Python runner looks that name up on
``tavern.st.prompt_converters`` and reports "the port has not ported this function
yet" when it is absent -- but only for the names a fixture mentions. An export
nobody wrote a fixture for is therefore invisible, and ``fixtures: N | match: N``
is a statement about the fixture set, not about the module. That is the same
blind spot S2 spent six rounds on (``STATUS-S2.md``: a guard that was read but
never written, plus fourteen "port bugs" that were all harness bugs).

So this script asks the other half of the question, and asks the *source* rather
than a list:

* the reference export list is **parsed** out of ``prompt-converters.js``, never
  hardcoded -- a reference bump that adds a function turns this red, which is the
  point;
* each name is resolved on the port with the runner's own camelCase ->
  snake_case rule, imported from ``run_converters_python`` so the two cannot
  drift, and against the same module object the runner imports;
* a ``function``/``class`` export must be present **and** callable; a ``value``
  export (``PROMPT_PROCESSING_TYPE``) must be present. One line is printed per
  unresolved export, and the exit code is non-zero.

It additionally reports which exports no fixture names, because that is the other
half of the same blind spot: a fixture-less export is unmeasured even once it is
ported. That section is informational -- the exit code depends on resolution
only, so this can go green while a ported export is still unmeasured (the S4 port
tasks land those fixtures).

``--port-file`` points the check at a copy of the module instead of the port.
That exists so the guard itself can be tested: point it at a scratch copy, rename
one function there, and it must name that export as missing. A guard that has
never fired is not evidence of anything.

Deliberately not wired into ``tools/check.py`` yet: the port tasks are landing
concurrently and ``check.py`` has to stay green while they do. See
``STATUS-S4.md`` for the intended follow-up.

Exit codes: ``0`` every reference export resolves; ``1`` a gap (missing or
non-callable); ``2`` this script could not do its job -- no reference snapshot,
nothing parsed out of it, or an unloadable port file.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
REFERENCE = REPO / "research" / "_raw" / "st-src" / "prompt-converters.js"
FIXTURES = HERE / "fixtures" / "converters"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# The name rule *and* the module under test, both taken from the runner rather
# than re-spelled here: a second copy of the rule could drift, and then this
# guard would bless a port the oracle cannot reach. `prompt_converters` is the
# module object `run_converters_python` resolves fixtures against.
from run_converters_python import prompt_converters, snake_case  # noqa: E402

#: `export function name(`, `export async function name(`, `export class Name`,
#: `export const/let/var name = ...`. Anchored at the declaration so a mention in
#: a comment or a string cannot be mistaken for an export.
DECL_RE = re.compile(
    r"^export\s+(?:(?:async)\s+)?(function|class|const|let|var)\s+([A-Za-z_$][\w$]*)([^\n]*)",
    re.MULTILINE,
)

#: The aggregate form, `export { a, b as c };` (optionally re-exported `from`).
EXPORT_LIST_RE = re.compile(
    r"^export\s*\{([^}]*)\}\s*(?:from\s*'[^']*'\s*)?;?",
    re.MULTILINE | re.DOTALL,
)

#: `export default ...`: an export no fixture can address by name (``DECL_RE``
#: does not match it, because `default` is not a declaration keyword).
DEFAULT_RE = re.compile(r"^export\s+default\b", re.MULTILINE)

#: Name reported for the above. It can never resolve, on purpose.
DEFAULT_NAME = "<default export>"


@dataclass(frozen=True)
class Export:
    """One named export of the reference module."""

    name: str
    callable_required: bool
    line: int

    @property
    def kind(self) -> str:
        return "function" if self.callable_required else "value"


def is_callable_declaration(keyword: str, rest_of_line: str) -> bool:
    """Whether an ``export const``-style declaration holds a function.

    A heuristic, but a conservative one: only the part of the initialiser before
    the first ``{`` or ``[`` is inspected, so an object literal that happens to
    contain an arrow function (``export const map = { f: () => 1 }``) is still a
    value. Misreading the kind either way is loud, not silent: it turns into a
    ``non-callable`` line the next time the check runs.
    """
    if keyword in ("function", "class"):
        return True
    rhs = rest_of_line.split("=", 1)[1] if "=" in rest_of_line else ""
    head = re.split(r"[{\[]", rhs, maxsplit=1)[0].strip()
    return "=>" in head or head.startswith(("(", "function", "async function"))


def parse_reference(path: Path) -> list[Export]:
    """The module's named exports, parsed -- never hardcoded."""
    text = path.read_text(encoding="utf-8")
    found: dict[str, Export] = {}

    for match in DECL_RE.finditer(text):
        keyword, name, rest = match.group(1), match.group(2), match.group(3)
        found[name] = Export(
            name=name,
            callable_required=is_callable_declaration(keyword, rest),
            line=text.count("\n", 0, match.start()) + 1,
        )

    for match in EXPORT_LIST_RE.finditer(text):
        for entry in match.group(1).split(","):
            parts = entry.strip().split()
            if not parts or parts[0] == "default":
                continue
            name = parts[-1] if len(parts) >= 3 and parts[-2] == "as" else parts[0]
            line = text.count("\n", 0, match.start()) + 1
            found.setdefault(name, Export(name=name, callable_required=False, line=line))

    # A default export is reported as an always-unresolved row rather than
    # dropped: an export form the parser silently ignores is exactly the blind
    # spot this guard exists to close, and the port cannot satisfy it by name.
    for match in DEFAULT_RE.finditer(text):
        found[DEFAULT_NAME] = Export(
            name=DEFAULT_NAME,
            callable_required=False,
            line=text.count("\n", 0, match.start()) + 1,
        )

    return [found[name] for name in sorted(found)]


def resolve(module: object, export: Export) -> tuple[str | None, object]:
    """``(attribute name, value)`` for a reference export, or ``(None, None)``.

    The candidate order is the runner's (``run_converters_python.resolve_export``):
    the reference spelling first, then the snake_case one. That helper is not
    called directly because it is bound to the real port module and rejects
    non-callables, so it cannot answer "present but not callable" -- and it
    cannot follow ``--port-file``. The *rule* (``snake_case``) is imported.
    """
    for candidate in (export.name, snake_case(export.name)):
        if hasattr(module, candidate):
            return candidate, getattr(module, candidate)
    return None, None


def load_port_module(path: Path) -> object | None:
    """Import a module from a file, for ``--port-file``."""
    spec = importlib.util.spec_from_file_location("_coverage_port", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_functions(directory: Path) -> tuple[dict[str, list[str]], int]:
    """Reference export -> fixtures that name it in their ``function`` field."""
    used: dict[str, list[str]] = {}
    files = 0
    for path in sorted(directory.glob("*.json")):
        files += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = payload.get("function") if isinstance(payload, dict) else None
        if isinstance(name, str) and name:
            used.setdefault(name, []).append(path.stem)
    return used, files


def display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return str(path)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check that the port exports every export of prompt-converters.js."
    )
    parser.add_argument(
        "--port-file",
        default="",
        help="check this module file instead of tavern/st/prompt_converters.py (self-test hook)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="also list the fixtures covering each resolved export",
    )
    return parser.parse_args(argv)


def fail(message: str) -> int:
    print(f"check_converter_coverage: {message}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if not REFERENCE.is_file():
        return fail(f"missing reference snapshot: {REFERENCE}")
    exports = parse_reference(REFERENCE)
    if not exports:
        return fail(f"parsed no exports out of {REFERENCE} -- the parser is stale, not the port")

    if args.port_file:
        path = Path(args.port_file)
        if not path.is_file():
            return fail(f"no such port file: {path}")
        module = load_port_module(path)
        if module is None:
            return fail(f"cannot load {path} as a module")
        port_label = f"{display(path)} (loaded from file)"
    else:
        module = prompt_converters
        port_label = "tavern.st.prompt_converters"

    fixture_uses, fixture_files = fixture_functions(FIXTURES)

    resolved: list[tuple[Export, str]] = []
    missing: list[Export] = []
    not_callable: list[tuple[Export, str, object]] = []
    for export in exports:
        attribute, value = resolve(module, export)
        if attribute is None:
            missing.append(export)
        elif export.callable_required and not callable(value):
            not_callable.append((export, attribute, value))
        else:
            resolved.append((export, attribute))

    callable_count = sum(1 for export in exports if export.callable_required)
    print(
        f"reference : {display(REFERENCE)} "
        f"({len(exports)} named exports: {callable_count} callable, "
        f"{len(exports) - callable_count} value)"
    )
    print(f"port      : {port_label}")
    print(f"fixtures  : {fixture_files} file(s) under {display(FIXTURES)}")

    width = max((len(export.name) for export in exports), default=10)
    print(f"\nresolved ({len(resolved)}/{len(exports)})")
    for export, attribute in resolved:
        print(f"  ok          {export.name:<{width}}  -> {attribute}")
        if args.verbose:
            covered_by = ", ".join(fixture_uses.get(export.name, [])) or "no fixture"
            print(f"                      fixtures: {covered_by}")

    if missing:
        print(f"\nmissing ({len(missing)}/{len(exports)})")
        for export in missing:
            print(
                f"  MISSING     {export.name:<{width}}  expected {snake_case(export.name)}"
                " -- neither spelling exists on the port"
            )
    else:
        print(f"\nmissing (0/{len(exports)})")

    if not_callable:
        print(f"\nnon-callable ({len(not_callable)}/{len(exports)})")
        for export, attribute, value in not_callable:
            print(
                f"  NOT-CALLABLE {export.name:<{width}}  -> {attribute}"
                f" is a {type(value).__name__}, expected callable"
            )
    else:
        print(f"\nnon-callable (0/{len(exports)})")

    measured = [export for export in exports if export.name in fixture_uses]
    print(
        f"\nfixture coverage: {len(measured)}/{len(exports)} exports named by a fixture's "
        "`function` field"
    )
    for export in exports:
        if export.name not in fixture_uses:
            print(f"  unmeasured  {export.name:<{width}}  no fixture calls it")
    for name in sorted(set(fixture_uses) - {export.name for export in exports}):
        print(f"  unknown     {name:<{width}}  named by {', '.join(fixture_uses[name])}")

    failures = len(missing) + len(not_callable)
    if failures:
        print(
            f"\nVERDICT: FAIL -- {failures} of {len(exports)} reference exports unresolved "
            f"({len(missing)} missing, {len(not_callable)} non-callable)"
        )
        return 1
    print(f"\nVERDICT: PASS -- all {len(exports)} reference exports resolve on the port")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
