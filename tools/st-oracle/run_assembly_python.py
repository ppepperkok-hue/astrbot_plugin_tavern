"""Run an assembly fixture against the ported prompt builder.

The mirror of ``tools/st-oracle/run_assembly.mjs``: the same fixture drives
``tavern.st.prompt_build.populate_chat_completion`` with the real
:class:`~tavern.st.chat_completion.ChatCompletion`, and the resulting identifier
sequence is written in the same shape so ``diff_assembly.py`` can compare them.

The fixture is the one the JS harness documents (``run_assembly.mjs``, "Fixture
shape"): the collection in ``prompts``, the chat in SillyTavern order in
``chat``, the page settings as top-level keys. Two pieces of the browser flow are
mirrored here because the shell calls the port earlier in the pipeline:

* :func:`set_openai_messages` reproduces ``setOpenAIMessages``
  (``openai.js:570-649``) for the fields this stage reads -- role, content and
  name -- including the reversed index (``messages[i]`` with ``i`` counting
  down), i.e. the array reaches the port **newest first**, exactly as it reaches
  the reference (``script.js:4830``). Name prefixing (``openai.js:595-612``) is
  part of the caller's formatting and out of this stage's scope, so the fixture
  turns carry their final content.
* ``PromptCollection.override`` is called for every identifier in
  ``overridden_prompts``, which is what ``preparePromptsForChatCompletion`` does
  for a character-card override (``openai.js:1502``/``1512``).

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


def build_prompts(fixture: dict[str, Any]) -> prompt_build.PromptCollection:
    """The collection the fixture declares, with the overrides applied.

    ``injection_position`` is spelled ``"relative"`` / ``"absolute"`` in the
    fixture (``run_assembly.mjs`` maps the same two words) and becomes the numeric
    ``INJECTION_POSITION`` the port compares against; a SillyTavern preset already
    stores the number.
    """
    prompts = prompt_build.PromptCollection()
    for spec in fixture.get("prompts") or []:
        injection_position = spec.get("injection_position", "relative")
        if injection_position == "absolute" or injection_position == 1:
            injection_position = prompt_build.InjectionPosition.ABSOLUTE
        else:
            injection_position = prompt_build.InjectionPosition.RELATIVE
        prompts.add(
            prompt_build.as_prompt(
                {
                    "identifier": spec["identifier"],
                    "role": spec.get("role", "system"),
                    "content": spec.get("content", ""),
                    "name": spec.get("name"),
                    "system_prompt": spec.get("system_prompt", True),
                    "position": spec.get("position"),
                    "injection_position": injection_position,
                    "injection_depth": spec.get("injection_depth", 0),
                    "injection_order": spec.get("injection_order", 100),
                    "marker": spec.get("marker", False),
                    "extension": spec.get("extension", False),
                }
            )
        )
    for identifier in fixture.get("overridden_prompts") or []:
        position = prompts.index(identifier)
        if position != -1:
            prompts.override(prompts.get(identifier), position)
    return prompts


def set_openai_messages(chat: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``setOpenAIMessages`` (``openai.js:570-649``), reduced to this stage.

    The reference walks ``i`` from the end of the chat while ``j`` walks forward,
    so ``messages[i] = chat[j]`` stores the *oldest* turn at the highest index:
    the array ``populateChatCompletion`` receives is newest first. Only the
    fields S2 reads are carried over (``role``, ``content``, ``name`` plus the
    ``injected`` marker ``populationInjectionPrompts`` uses); media, tool
    invocations and reasoning signatures are out of scope.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "", "name": "", "extra": {}} for _ in chat
    ]
    j = 0
    for i in range(len(chat) - 1, -1, -1):
        turn = chat[j]
        messages[i] = {
            "role": "user" if turn.get("is_user") else "assistant",
            "content": str(turn.get("mes") or "").replace("\r", ""),
            "name": turn.get("name") or "",
            "extra": turn.get("extra") or {},
        }
        j += 1
    return messages


def message_factory(role: str, content: str, identifier: str) -> Message:
    """Messages counted with the port's own estimate.

    ``run_assembly.mjs`` re-counts ``Message.createAsync`` / ``Message.setName``
    with the same rule (``max(1, len(non-empty parts) // 3)``), so the budget
    arithmetic of a fixture is identical on both sides and a fixture can pin which
    group wins a tight budget. :meth:`Message.create` is exactly that estimate.
    """
    return Message.create(role or "system", content or "", identifier)


async def run_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    prompts = build_prompts(fixture)
    completion = ChatCompletion()
    completion.set_token_budget(
        int(fixture.get("max_context", 1_000_000)), int(fixture.get("response", 0))
    )
    record: dict[str, Any] = {"fixture": fixture.get("name", ""), "steps": []}

    settings = dict(fixture.get("settings") or {})
    settings.setdefault("continue_prefill", bool(fixture.get("continue_prefill", False)))
    settings.setdefault("assistant_prefill", fixture.get("assistant_prefill", ""))
    if "chat_completion_source" in fixture:
        settings.setdefault("chat_completion_source", fixture["chat_completion_source"])

    try:
        await prompt_build.populate_chat_completion(
            prompts,
            completion,
            bias=fixture.get("bias", ""),
            quiet_prompt=fixture.get("quiet_prompt", ""),
            type=fixture.get("type"),
            cycle_prompt=fixture.get("cycle_prompt"),
            messages=set_openai_messages(list(fixture.get("chat") or [])),
            message_examples=list(fixture.get("examples") or []),
            settings=settings,
            pin_examples=bool(fixture.get("pin_examples", False)),
            selected_group=bool(fixture.get("selected_group", False)),
            names_behavior=fixture.get("names_behavior", 0),
            # ``script.js:4978-4979`` appends the postfix while the prompt is
            # built, i.e. before this stage sees it; the fixture supplies the
            # cycle prompt in that final form.
            continue_postfix="",
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
            "chat": [
                {
                    "role": item.get("role"),
                    "content": item.get("content"),
                    "name": item.get("name"),
                }
                for item in chat
            ],
            "overridden": completion.get_overridden_prompts(),
        }
    )
    # The port always builds its history from the fixture turns, so the chat
    # comparison is meaningful unless the run failed before populating it.
    record["comparable"] = {"chat": {"ok": not record.get("error"), "reason": ""}}
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
