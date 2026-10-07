"""Run an assembly fixture against the ported prompt builder.

The mirror of ``tools/st-oracle/run_assembly.mjs``: the same fixture drives
``tavern.st.prompt_build.populate_chat_completion`` with the real
:class:`~tavern.st.chat_completion.ChatCompletion`, and the resulting identifier
sequence is written in the same shape so ``diff_assembly.py`` can compare them.

Usage:
    python tools/st-oracle/run_assembly_python.py <fixture.json> [--out <result.json>]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

from tavern.st import prompt_build  # noqa: E402
from tavern.st.chat_completion import ChatCompletion, Message  # noqa: E402


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture")
    parser.add_argument("--out", default="")
    return parser.parse_args(argv)


def build_prompts(specs: list[dict[str, Any]]) -> prompt_build.PromptCollection:
    prompts = prompt_build.PromptCollection()
    for spec in specs:
        prompts.add(
            prompt_build.as_prompt(
                {
                    "identifier": spec["identifier"],
                    "role": spec.get("role", "system"),
                    "content": spec.get("content", ""),
                    "system_prompt": spec.get("system_prompt", True),
                    "injection_position": spec.get("injection_position", "relative"),
                    "injection_depth": spec.get("injection_depth", 0),
                    "injection_order": spec.get("injection_order", 100),
                    "marker": spec.get("marker", False),
                    "extension": spec.get("extension", False),
                }
            )
        )
    return prompts


def message_factory(role: str, content: str, identifier: str) -> Message:
    """Deterministic messages: the assembly order does not depend on token counts."""
    message = Message(role or "system", content or "", identifier)
    message.tokens = 1
    return message


async def run_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    prompts = build_prompts(fixture.get("prompts") or [])
    # The collection slot list mirrors the reference's `PromptCollection`: a slot
    # for every entry, `None` when the fixture only wants it to *exist* (so
    # ``prompts.has('chatHistory')`` is true) without providing content.
    for identifier in ("dialogueExamples", "chatHistory"):
        if not prompts.has(identifier):
            prompts.add(prompt_build.as_prompt({"identifier": identifier, "content": ""}))
    completion = ChatCompletion()
    completion.set_token_budget(
        int(fixture.get("max_context", 1_000_000)), int(fixture.get("response", 0))
    )
    record: dict[str, Any] = {"fixture": fixture.get("name", ""), "steps": []}

    class _Chat:
        """A chat message stand-in for the prompts builder."""

        def __init__(self, payload: dict[str, Any]) -> None:
            self.role = payload.get("role", "user")
            self.content = payload.get("content", "")
            self.name = payload.get("name", "")
            self.extra: dict[str, Any] = {}

        @property
        def mes(self) -> str:
            return self.content

    try:
        await prompt_build.populate_chat_completion(
            prompts,
            completion,
            bias=fixture.get("bias", ""),
            quiet_prompt=fixture.get("quiet_prompt", ""),
            type=fixture.get("type"),
            messages=[_Chat(turn) for turn in fixture.get("chat") or []],
            message_examples=list(fixture.get("examples") or []),
            pin_examples=bool(fixture.get("pin_examples", False)),
            selected_group=bool(fixture.get("selected_group", False)),
            names_behavior=fixture.get("names_behavior", 0),
            continue_postfix=fixture.get("continue_postfix", ""),
            is_prompt_disabled_for_active_character=lambda identifier: identifier
            in set(fixture.get("disabled_prompts") or []),
            message_factory=message_factory,
        )
    except Exception as exc:  # noqa: BLE001 - the diff must see the failure, not a traceback
        record["error"] = f"the port itself failed: {type(exc).__name__}: {exc}"

    chat = completion.get_chat()
    record["steps"].append(
        {
            "label": "final",
            # `None` marks a sparse slot (the reference has a hole there too).
            "identifiers": [
                item.identifier if item is not None else None
                for item in completion.messages.collection
            ],
            "chat": [{"role": item["role"], "content": item["content"]} for item in chat],
            "overridden": completion.get_overridden_prompts(),
        }
    )
    return record


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    path = Path(args.fixture)
    if not path.is_file():
        print(f"run_assembly_python: no such fixture: {path}", file=sys.stderr)
        return 2
    fixture = json.loads(path.read_text(encoding="utf-8"))
    record = asyncio.run(run_fixture(fixture))
    payload = json.dumps(record, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
