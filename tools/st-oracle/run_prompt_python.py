"""Run one prompt-assembly fixture against the Python port of ``openai.js``.

The mirror of ``tools/st-oracle/run_prompt.mjs``: same fixture, same steps, same
result shape, so ``diff_prompt.py`` can compare the two runs field by field.
Steps that the fixture pins with ``tokens`` bypass the estimate on both sides,
which is what keeps the budget arithmetic comparable.

Usage:
    python tools/st-oracle/run_prompt_python.py <fixture.json> [--out <result.json>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

from tavern.st.chat_completion import (  # noqa: E402
    ChatCompletion,
    Message,
    MessageCollection,
    PromptError,
    count_tokens,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture")
    parser.add_argument("--out", default="")
    return parser.parse_args(argv)


def error_name(exc: BaseException) -> str:
    """The JS ``error.name`` equivalent, so both sides name the same failure."""
    return getattr(exc, "name", "") or type(exc).__name__


def count_for(spec: dict[str, Any], divisor: int) -> int:
    """Pinned token count for one fixture message (mirrors ``countFor`` in JS)."""
    if isinstance(spec.get("tokens"), int):
        return int(spec["tokens"])
    parts = [
        value
        for value in (spec.get("role"), spec.get("content"), spec.get("name"))
        if isinstance(value, str) and value
    ]
    text = " ".join(parts)
    return max(1, len(text) // divisor) if text else 0


def make_message(spec: dict[str, Any], divisor: int) -> Message:
    message = Message(
        str(spec.get("role") or ""),
        spec.get("content") or "",
        str(spec.get("identifier") or "anonymous"),
    )
    message.tokens = count_for(spec, divisor)
    if spec.get("tool_calls"):
        message.tool_calls = spec["tool_calls"]
    if spec.get("name"):
        message.name = str(spec["name"])
    return message


def make_collection(spec: dict[str, Any], divisor: int) -> MessageCollection:
    collection = MessageCollection(str(spec.get("identifier") or "collection"))
    for item in spec.get("items") or []:
        if isinstance(item, dict) and "collection" in item:
            collection.add(make_collection(item["collection"], divisor))
        else:
            collection.add(make_message(item, divisor))
    return collection


def run(fixture: dict[str, Any], divisor: int) -> dict[str, Any]:
    result: dict[str, Any] = {"fixture": fixture.get("name", ""), "steps": []}
    completions: dict[str, ChatCompletion] = {}

    for step in fixture.get("steps") or []:
        record: dict[str, Any] = {"type": step["type"]}
        kind = step["type"]

        if kind == "new":
            completions[str(step.get("id", "default"))] = ChatCompletion()
            record["ok"] = True
            result["steps"].append(record)
            continue

        completion = completions.get(str(step.get("id", "default")))
        if completion is None:
            raise SystemExit(f"run_prompt_python: unknown completion id: {step.get('id')}")

        if kind == "budget":
            completion.set_token_budget(int(step["context"]), int(step["response"]))
            record["budget"] = completion.token_budget
        elif kind == "add":
            try:
                completion.add(make_collection(step["collection"], divisor))
                record["added"] = step["collection"].get("identifier")
            except PromptError as exc:
                record["error"] = error_name(exc)
            record["budget"] = completion.token_budget
        elif kind in ("insert_start", "insert_end"):
            message = make_message(step["message"], divisor)
            try:
                if kind == "insert_start":
                    completion.insert_at_start(message, step["identifier"])
                else:
                    completion.insert_at_end(message, step["identifier"])
                record["inserted"] = message.identifier
            except PromptError as exc:
                record["error"] = error_name(exc)
            record["budget"] = completion.token_budget
        elif kind == "remove_last":
            try:
                completion.remove_last_from(step["identifier"])
                record["removed"] = True
            except PromptError as exc:
                record["error"] = error_name(exc)
            record["budget"] = completion.token_budget
        elif kind == "squash":
            import asyncio

            asyncio.run(completion.squash_system_messages())
            record["ok"] = True
        elif kind == "snapshot":
            record["chat"] = completion.get_chat()
            record["budget"] = completion.token_budget
            record["total_tokens"] = completion.get_total_token_count()
            record["has"] = [
                [identifier, completion.has(identifier)]
                for identifier in step.get("identifiers") or []
            ]
            if step.get("dump_collection"):
                record["collection"] = [
                    {
                        "kind": type(item).__name__,
                        "identifier": item.identifier,
                        "role": item.role if isinstance(item, Message) else None,
                        "name": (item.name or None) if isinstance(item, Message) else None,
                        "content": (
                            item.content
                            if isinstance(item, Message) and isinstance(item.content, str)
                            else None
                        ),
                        "tokens": item.get_tokens(),
                    }
                    for item in completion.messages.collection
                ]
        elif kind == "count":
            record["counted"] = count_tokens(step["message"])
        else:
            raise SystemExit(f"run_prompt_python: unknown step type: {kind}")

        result["steps"].append(record)

    return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    path = Path(args.fixture)
    if not path.is_file():
        print(f"run_prompt_python: no such fixture: {path}", file=sys.stderr)
        return 2
    fixture = json.loads(path.read_text(encoding="utf-8"))
    result = run(fixture, int(fixture.get("token_divisor", 3)))
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
