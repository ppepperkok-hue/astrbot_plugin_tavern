"""Run one provider-converter fixture against the ported implementation.

The mirror of ``tools/st-oracle/run_converters.mjs``: the same fixture drives
:mod:`tavern.st.prompt_converters`, and the outcome is written in the same shape
so ``diff_converters.py`` can compare the two.

The fixture names a function and its arguments -- the *reference's* function
names, so the port must export the same names. A name the port has not ported yet
is reported as an error rather than silently skipped; that is what makes the
fixture set a to-do list.

Usage:
    python tools/st-oracle/run_converters_python.py <fixture.json> [--out <result.json>]
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

from tavern.st import prompt_converters  # noqa: E402


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture")
    parser.add_argument("--out", default="")
    return parser.parse_args(argv)


def json_safe(value: Any) -> Any:
    """Mirror of ``run_converters.mjs``'s ``jsonSafe``.

    A function cannot cross into JSON, and ``get_prompt_names`` returns one
    (``startsWithGroupName``). Dropping it silently would let an unported
    predicate look like a match, so its *presence* is recorded as a marker and its
    *behaviour* is asserted through the fixture's ``probe``.
    """
    if callable(value) and not isinstance(value, type):
        return "<function>"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def snake_case(name: str) -> str:
    """``getPromptNames`` -> ``get_prompt_names``.

    The port follows the rest of this project's Python naming (``snake_case``
    throughout: ``populate_chat_completion`` mirrors ``populateChatCompletion``),
    while a fixture names the *reference's* export so that one fixture drives both
    sides. This is the one translation between the two spellings, and it is
    deliberately a rule rather than a table so a new function needs no wiring.

    Acronym runs are kept together: ``convertXAIMessages`` -> ``convert_xai_messages``.
    """
    out: list[str] = []
    for index, char in enumerate(name):
        if char.isupper():
            previous = name[index - 1] if index else ""
            following = name[index + 1] if index + 1 < len(name) else ""
            starts_word = previous and (previous.islower() or previous.isdigit())
            ends_acronym = previous.isupper() and following.islower()
            if (starts_word or ends_acronym) and out and out[-1] != "_":
                out.append("_")
            out.append(char.lower())
        else:
            out.append(char)
    return "".join(out)


def resolve_export(name: str) -> Any:
    """The port's implementation of a reference export, or ``None``."""
    for candidate in (name, snake_case(name)):
        target = getattr(prompt_converters, candidate, None)
        if callable(target):
            return target
    return None


def resolve_path(root: Any, dotted: str) -> tuple[bool, Any, str]:
    """Walk ``a.b.c`` through mappings (and attributes as a fallback)."""
    node = root
    for part in str(dotted).split("."):
        if node is None:
            return False, None, part
        try:
            node = node[part] if isinstance(node, dict) else getattr(node, part)
        except (KeyError, AttributeError, TypeError):
            return False, None, part
    return True, node, ""


def run_probe(fixture: dict[str, Any], root: Any) -> Any:
    """``{path, call, this_path}``: resolve, rebind, call, record.

    ``this_path`` matters for the one method-shaped predicate in this module:
    ``getPromptNames`` returns ``startsWithGroupName`` and it reads
    ``this.groupNames`` (``prompt-converters.js:54-56``), so calling it detached
    throws in the reference. The port's version closes over ``group_names``
    instead, but the probe still rebinds, so the two are exercised identically.
    """
    spec = fixture.get("probe")
    if not isinstance(spec, dict) or not spec.get("path"):
        return None

    found, node, at = resolve_path(root, spec["path"])
    if not found:
        return {"path": spec["path"], "error": f"path not found before {at!r}"}

    if not callable(node):
        return {"path": spec["path"], "value": json_safe(node)}

    if spec.get("this_path"):
        ok, receiver, at = resolve_path(root, spec["this_path"])
        if not ok:
            return {"path": spec["path"], "error": f"this_path not found before {at!r}"}
    else:
        receiver = root

    call = spec.get("call") or []
    try:
        # A bound method would already carry its receiver; `__func__` unwraps it so
        # the rebind below cannot be a silent no-op.
        raw = getattr(node, "__func__", node)
        return {
            "path": spec["path"],
            "call": call,
            "result": json_safe(raw(receiver, *copy.deepcopy(call))),
        }
    except Exception as exc:  # noqa: BLE001
        return {"path": spec["path"], "call": call, "error": f"{type(exc).__name__}: {exc}"}


def run_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    record: dict[str, Any] = {
        "fixture": fixture.get("name", ""),
        "function": fixture.get("function"),
    }

    name = fixture.get("function")
    target = resolve_export(str(name or ""))
    if target is None:
        record["error"] = (
            f"the port has not ported this function yet: {name!r} "
            f"(looked for {snake_case(str(name or ''))!r} on tavern.st.prompt_converters)"
        )
        return record

    # The config has to be applied before the call, mirroring the JS side's
    # module-scope reads. The port keeps it in a module-level mapping rather than
    # reading it at import time, so ordering is less delicate -- but apply it the
    # same way so a fixture cannot pass for the wrong reason.
    prompt_converters.set_config(fixture.get("config") or {})

    call_args = copy.deepcopy(fixture.get("args") or [])
    record["args_in"] = copy.deepcopy(call_args)

    try:
        returned = target(*call_args)
        # The reference is synchronous throughout this module; the port is too.
        # An accidental coroutine would otherwise serialise as a string and look
        # like a match, so it is a hard error here.
        if asyncio.iscoroutine(returned):
            record["error"] = f"{name} returned a coroutine, but the reference is synchronous"
            return record
    except Exception as exc:  # noqa: BLE001 - the diff must see the failure, not a traceback
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    record["returned"] = None if returned is None else json_safe(returned)
    record["mutated"] = json_safe(call_args)
    record["probe"] = run_probe(fixture, returned)
    return record


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    path = Path(args.fixture)
    if not path.is_file():
        print(f"run_converters_python: no such fixture: {path}", file=sys.stderr)
        return 2
    fixture = json.loads(path.read_text(encoding="utf-8"))
    record = run_fixture(fixture)
    payload = json.dumps(record, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
