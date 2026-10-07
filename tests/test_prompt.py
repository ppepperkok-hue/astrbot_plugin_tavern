"""Offline tests for the SillyTavern prompt assembler."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from astrbot_plugin_tavern.st.cards import CharacterCard  # noqa: E402
from astrbot_plugin_tavern.st.prompt import (  # noqa: E402
    DEFAULT_MAIN_PROMPT,
    DEFAULT_PROMPT_ORDER,
    InChatTargets,
    PresetSpec,
    PromptBlockSpec,
    RenderOptions,
    build_messages,
    count_tokens,
    default_preset,
    parse_dialogue_examples,
    preset_from_dict,
    render_macro,
    trim_history,
)

EXPECTED_ORDER = (
    "main",
    "worldInfoBefore",
    "personaDescription",
    "charDescription",
    "charPersonality",
    "scenario",
    "enhanceDefinitions",
    "nsfw",
    "worldInfoAfter",
    "dialogueExamples",
    "chatHistory",
    "jailbreak",
)


@dataclass
class Msg:
    """Stand-in for the caller's chat message type (structural protocol)."""

    role: str
    content: str
    name: str | None = None


def make_card(**overrides: Any) -> CharacterCard:
    base: dict[str, Any] = {
        "name": "Aria",
        "description": "A lighthouse keeper.",
        "personality": "Terse, warm.",
        "scenario": "A storm over the bay.",
    }
    base.update(overrides)
    return CharacterCard(**base)


def make_options(**overrides: Any) -> RenderOptions:
    base: dict[str, Any] = {
        "username": "Kai",
        "char_name": "Aria",
        "now": datetime(2024, 6, 1, 14, 30, 5, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return RenderOptions(**base)


# --- 1. default order -------------------------------------------------------


def test_default_prompt_order_is_stock_sillytavern() -> None:
    assert DEFAULT_PROMPT_ORDER == EXPECTED_ORDER
    preset = default_preset()
    assert preset.names() == list(EXPECTED_ORDER)
    assert preset.main_prompt == DEFAULT_MAIN_PROMPT
    assert preset.names_as_prefix is True


def test_default_preset_flags() -> None:
    preset = default_preset()
    # SillyTavern ships these two disabled.
    assert preset.block("personaDescription") is not None
    assert preset.block("personaDescription").enabled is False
    assert preset.block("enhanceDefinitions").enabled is False
    for name in EXPECTED_ORDER:
        if name in ("personaDescription", "enhanceDefinitions"):
            continue
        assert preset.block(name).enabled is True, name

    # marker slots are placeholders filled from the card / chat.
    for name in (
        "worldInfoBefore",
        "worldInfoAfter",
        "charDescription",
        "charPersonality",
        "scenario",
        "dialogueExamples",
        "chatHistory",
    ):
        assert preset.block(name).marker is True, name
    assert preset.block("main").marker is False
    assert preset.block("nsfw").marker is False

    assert preset.block("does-not-exist") is None


# --- 2. macros --------------------------------------------------------------


def test_render_macro_basics() -> None:
    options = make_options()
    assert render_macro("{{char}} greets {{user}}", options) == "Aria greets Kai"
    assert render_macro("{{CHAR}} / {{User}} / {{ChAr}}", options) == "Aria / Kai / Aria"
    assert render_macro("{{ char }}", options) == "Aria"
    assert render_macro("{{time}} {{date}} {{weekday}}", options) == "14:30 2024-06-01 Saturday"
    assert render_macro("{{isotime}} {{isodate}}", options) == "14:30:05 2024-06-01T14:30:05"
    assert render_macro("plain text", options) == "plain text"
    assert render_macro("", options) == ""


def test_render_macro_keeps_unknown_and_input_without_value() -> None:
    options = make_options()
    assert render_macro("{{mood}} stays", options) == "{{mood}} stays"
    assert render_macro("{{input}}", options) == "{{input}}"
    assert render_macro("{{input}}", options, {"input": "open the door"}) == "open the door"
    assert render_macro("{{Input}}", options, {"input": "mixed case"}) == "mixed case"


def test_render_macro_unknown_timezone_falls_back_to_utc() -> None:
    options = make_options(timezone_name="Not/AZone")
    assert render_macro("{{time}}", options) == "14:30"


def test_render_macro_timezone_conversion() -> None:
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo("Asia/Tokyo")
    except Exception:  # noqa: BLE001 - no tzdata on this machine
        pytest.skip("timezone database unavailable")
    options = make_options(timezone_name="Asia/Tokyo")
    assert render_macro("{{time}}", options) == "23:30"


def test_render_macro_naive_now_is_assumed_local() -> None:
    options = make_options(now=datetime(2024, 6, 1, 9, 5, 0))
    assert render_macro("{{time}}", options) == "09:05"
    assert render_macro("{{weekday}}", options) == "Saturday"


# --- 3. block ordering, markers and empty blocks ----------------------------


def test_marker_blocks_keep_their_position_without_content() -> None:
    result = build_messages(make_card(), default_preset(), None, [], make_options())

    assert [name for name, _content in result.debug_blocks] == list(EXPECTED_ORDER)
    content_by_block = dict(result.debug_blocks)
    assert content_by_block["charDescription"] == "A lighthouse keeper."
    # disabled blocks never produce content, markers without data stay empty.
    for name in (
        "personaDescription",
        "enhanceDefinitions",
        "worldInfoBefore",
        "worldInfoAfter",
        "dialogueExamples",
        "chatHistory",
    ):
        assert content_by_block[name] == "", name

    # everything that is left is system text and gets squashed into one message.
    expected_main = render_macro(DEFAULT_MAIN_PROMPT, make_options())
    assert len(result.messages) == 1
    assert result.messages[0].role == "system"
    assert result.messages[0].content == "\n".join(
        [
            expected_main,
            "A lighthouse keeper.",
            "Terse, warm.",
            "A storm over the bay.",
        ]
    )


def test_empty_blocks_are_skipped() -> None:
    preset = PresetSpec(
        order=[
            PromptBlockSpec("main"),
            PromptBlockSpec("charDescription", marker=True),
            PromptBlockSpec("scenario", marker=True),
        ],
        main_prompt="",
    )
    blank = make_card(description="", scenario="", name="")
    result = build_messages(blank, preset, None, [], make_options())

    assert result.messages == []
    assert result.debug_blocks == [("main", ""), ("charDescription", ""), ("scenario", "")]


def test_disabled_blocks_are_never_rendered() -> None:
    preset = PresetSpec(
        order=[
            PromptBlockSpec("main", enabled=False),
            PromptBlockSpec("charDescription", marker=True),
        ],
        main_prompt="{{char}} speaks",
    )
    result = build_messages(make_card(), preset, None, [], make_options())

    assert [message.role for message in result.messages] == ["system"]
    assert result.messages[0].content == "A lighthouse keeper."


def test_squash_system_can_be_disabled() -> None:
    options = make_options()
    result = build_messages(make_card(), default_preset(), None, [], options, squash_system=False)
    assert [message.content for message in result.messages] == [
        render_macro(DEFAULT_MAIN_PROMPT, options),
        "A lighthouse keeper.",
        "Terse, warm.",
        "A storm over the bay.",
    ]


def test_strip_leading_newlines_option() -> None:
    preset = PresetSpec(order=[PromptBlockSpec("charDescription", marker=True)])
    stripped = build_messages(
        make_card(description="\n\nKeeper."), preset, None, [], make_options()
    )
    assert stripped.messages[0].content == "Keeper."

    kept = build_messages(
        make_card(description="\n\nKeeper."),
        preset,
        None,
        [],
        make_options(strip_leading_newlines=False),
    )
    assert kept.messages[0].content == "\n\nKeeper."


def test_main_and_jailbreak_fallbacks() -> None:
    preset = PresetSpec(order=[PromptBlockSpec("main"), PromptBlockSpec("jailbreak")])
    result = build_messages(
        make_card(system_prompt="Be {{char}}.", post_history_instructions="Stay in character."),
        preset,
        None,
        [],
        make_options(),
        squash_system=False,
    )
    assert [message.content for message in result.messages] == [
        "Be Aria.",
        "Stay in character.",
    ]

    with_preset_text = PresetSpec(
        order=[PromptBlockSpec("jailbreak")],
        jailbreak_prompt="Preset PHI wins.",
    )
    result = build_messages(
        make_card(post_history_instructions="Card PHI."),
        with_preset_text,
        None,
        [],
        make_options(),
        squash_system=False,
    )
    assert result.messages[0].content == "Preset PHI wins."


def test_extra_macros_reach_prompt_blocks() -> None:
    preset = PresetSpec(order=[PromptBlockSpec("charDescription", marker=True)])
    result = build_messages(
        make_card(description="Mood: {{mood}}"),
        preset,
        None,
        [],
        make_options(),
        extra_macros={"mood": "calm"},
    )
    assert result.messages[0].content == "Mood: calm"


# --- 4. dialogue examples ---------------------------------------------------


def test_dialogue_examples_split_on_start() -> None:
    example = (
        "{{user}}: hello there\n"
        "{{char}}: Hello, Kai.\n"
        "<START>\n"
        "{{user}}: are you cold?\n"
        "{{char}}: The lamp keeps me warm."
    )
    messages = parse_dialogue_examples(example, make_options())

    assert [message.role for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert messages[0].content == "Kai: hello there"
    assert messages[1].content == "Aria: Hello, Kai."
    assert messages[2].content == "Kai: are you cold?"
    assert messages[3].content == "Aria: The lamp keeps me warm."


def test_dialogue_examples_can_strip_name_prefixes() -> None:
    example = "{{user}}: hi\n{{char}}: hello"
    messages = parse_dialogue_examples(example, make_options(names_as_prefix=False))
    assert [message.content for message in messages] == ["hi", "hello"]


def test_dialogue_examples_without_prefix_and_empty() -> None:
    only_prose = parse_dialogue_examples("*the lamp flickers*", make_options())
    assert [message.role for message in only_prose] == ["assistant"]
    assert only_prose[0].content == "*the lamp flickers*"

    assert parse_dialogue_examples("", make_options()) == []
    assert parse_dialogue_examples("<START>\n<START>", make_options()) == []


def test_em_top_and_bottom_wrap_dialogue_examples() -> None:
    targets = InChatTargets(em_top=["before examples"], em_bottom=["after examples"])
    result = build_messages(
        make_card(mes_example="{{char}}: Hi."),
        default_preset(),
        targets,
        [],
        make_options(),
    )
    # ``em_top`` is a system message, so it squashes into the leading system run.
    contents = [message.content for message in result.messages]
    assert contents[0].endswith("before examples")
    assert contents[1:] == ["Aria: Hi.", "after examples"]


# --- 5. world info placement and history ------------------------------------


def test_world_info_before_and_after_blocks() -> None:
    targets = InChatTargets.from_mapping(
        {"worldInfoBefore": "LORE BEFORE", "world info after": ["LORE AFTER"]}
    )
    preset = PresetSpec(
        order=[
            PromptBlockSpec("worldInfoBefore", marker=True),
            PromptBlockSpec("charDescription", marker=True),
            PromptBlockSpec("worldInfoAfter", marker=True),
        ]
    )
    result = build_messages(make_card(), preset, targets, [], make_options(), squash_system=False)
    assert [message.content for message in result.messages] == [
        "LORE BEFORE",
        "A lighthouse keeper.",
        "LORE AFTER",
    ]


def test_chat_history_order_and_injections() -> None:
    history = [Msg("user", "one"), Msg("assistant", "two"), Msg("user", "three")]
    targets = InChatTargets(
        at_depth=["depth note"],
        an_top=["top note"],
        an_bottom=["bottom note"],
    )
    result = build_messages(
        make_card(),
        default_preset(),
        targets,
        history,
        make_options(),
        at_depth_before_index=2,
    )

    # the leading system blocks squash together with ``an_top``.
    assert result.messages[0].content.endswith("top note")
    assert [message.content for message in result.messages[1:]] == [
        "one",
        "two",
        "depth note",
        "three",
        "bottom note",
    ]
    assert result.messages[3].role == "system"


def test_at_depth_defaults_to_before_the_history() -> None:
    history = [Msg("user", "one"), Msg("assistant", "two")]
    targets = InChatTargets(at_depth="depth note")
    result = build_messages(
        make_card(),
        default_preset(),
        targets,
        history,
        make_options(),
        at_depth_before_index=None,
    )
    contents = [message.content for message in result.messages]
    # the depth injection sits right before the history, so it squashes into
    # the leading system run instead of becoming its own message.
    assert len(contents) == 3
    assert contents[0].endswith("depth note")
    assert contents[1:] == ["one", "two"]


def test_at_depth_index_is_clamped() -> None:
    history = [Msg("user", "one")]
    targets = InChatTargets(at_depth="depth note")
    far = build_messages(
        make_card(),
        PresetSpec(order=[PromptBlockSpec("chatHistory", marker=True)]),
        targets,
        history,
        make_options(),
        at_depth_before_index=99,
    )
    assert [message.content for message in far.messages] == ["one", "depth note"]

    negative = build_messages(
        make_card(),
        PresetSpec(order=[PromptBlockSpec("chatHistory", marker=True)]),
        targets,
        history,
        make_options(),
        at_depth_before_index=-5,
    )
    assert [message.content for message in negative.messages] == ["depth note", "one"]


def test_chat_history_roles_names_and_macros() -> None:
    history = [
        Msg("USER", "hello {{char}}"),
        Msg("tool", "raw line"),
        Msg("assistant", "ok", name="Aria"),
        Msg("user", "   "),
    ]
    preset = PresetSpec(order=[PromptBlockSpec("chatHistory", marker=True)])
    result = build_messages(make_card(), preset, None, history, make_options())

    assert [(m.role, m.content, m.name) for m in result.messages] == [
        ("user", "hello Aria", None),
        ("user", "raw line", None),
        ("assistant", "ok", "Aria"),
    ]


def test_in_chat_targets_normalisation() -> None:
    targets = InChatTargets(at_depth="one", an_top=None, em_bottom=["a", None, "b"])  # type: ignore[list-item]
    assert targets.at_depth == ["one"]
    assert targets.an_top == []
    assert targets.em_bottom == ["a", "b"]

    mapped = InChatTargets.from_mapping(
        {
            "at depth": "depth",
            "emTop": ["top"],
            "world_info_before": None,
            "unrelated": "ignored",
        }
    )
    assert mapped.at_depth == ["depth"]
    assert mapped.em_top == ["top"]
    assert mapped.before_char == []
    assert mapped.after_char == []


# --- 6. history trimming and token counting ---------------------------------


def test_trim_history_drops_oldest_whole_messages() -> None:
    history = [Msg("user", "a" * 30), Msg("assistant", "b" * 30), Msg("user", "c" * 30)]
    counter = lambda text: len(text) // 10  # noqa: E731 - 3 tokens per message
    kept = trim_history(history, max_tokens=6, token_counter=counter)

    assert [message.content for message in kept] == ["b" * 30, "c" * 30]


def test_trim_history_keeps_the_last_messages_whatever_the_budget() -> None:
    history = [Msg("user", "x" * 100), Msg("assistant", "y" * 100), Msg("user", "z" * 100)]
    counter = lambda text: len(text) // 10  # noqa: E731 - 10 tokens per message

    kept = trim_history(history, max_tokens=5, token_counter=counter, keep_last=2)
    assert [message.content for message in kept] == ["y" * 100, "z" * 100]

    none_fit = trim_history(history, max_tokens=0, token_counter=counter, keep_last=1)
    assert [message.content for message in none_fit] == ["z" * 100]


def test_trim_history_reserve_tokens() -> None:
    history = [Msg("user", "a" * 20), Msg("assistant", "b" * 20)]
    counter = lambda text: len(text) // 10  # noqa: E731 - 2 tokens per message

    assert len(trim_history(history, 10, counter)) == 2
    assert [
        message.content for message in trim_history(history, 10, counter, reserve_tokens=7)
    ] == ["b" * 20]


def test_trim_history_empty_history() -> None:
    assert trim_history([], max_tokens=100, token_counter=len) == []


def test_count_tokens() -> None:
    assert count_tokens("") == 0
    assert count_tokens("hello world") >= 1

    try:
        import tiktoken
    except ImportError:  # pragma: no cover - tiktoken is installed here
        text = "abcd" * 10
        assert count_tokens(text) == max(1, len(text) // 3)
        return
    expected = len(tiktoken.get_encoding("cl100k_base").encode("hello, world"))
    assert count_tokens("hello, world") == expected


# --- 7. preset parsing ------------------------------------------------------

SILLYTAVERN_PRESET: dict[str, Any] = {
    "name": "Roleplay - Default",
    "temperature": 0.9,
    "prompts": [
        {
            "identifier": "main",
            "name": "Main Prompt",
            "system_prompt": (
                "Write {{char}}'s next reply in a fictional chat between {{char}} and {{user}}."
            ),
            "role": "system",
            "marker": False,
        },
        {
            "identifier": "worldInfoBefore",
            "name": "World Info (before)",
            "system_prompt": "",
            "role": "system",
            "marker": True,
        },
        {
            "identifier": "charDescription",
            "name": "Char Description",
            "system_prompt": "",
            "role": "system",
            "marker": True,
        },
        {
            "identifier": "charPersonality",
            "name": "Char Personality",
            "system_prompt": "",
            "role": "system",
            "marker": True,
        },
        {
            "identifier": "scenario",
            "name": "Scenario",
            "system_prompt": "",
            "role": "system",
            "marker": True,
        },
        {
            "identifier": "enhanceDefinitions",
            "name": "Enhance Definitions",
            "system_prompt": "Add more detail.",
            "role": "system",
            "marker": False,
            "enabled": False,
        },
        {
            "identifier": "nsfw",
            "name": "Auxiliary Prompt",
            "system_prompt": "",
            "role": "system",
            "marker": False,
        },
        {
            "identifier": "worldInfoAfter",
            "name": "World Info (after)",
            "system_prompt": "",
            "role": "system",
            "marker": True,
        },
        {
            "identifier": "dialogueExamples",
            "name": "Chat Examples",
            "system_prompt": "",
            "role": "system",
            "marker": True,
        },
        {
            "identifier": "chatHistory",
            "name": "Chat History",
            "system_prompt": "",
            "role": "system",
            "marker": True,
        },
        {
            "identifier": "jailbreak",
            "name": "Post-History Instructions",
            "system_prompt": "Stay in character.",
            "role": "system",
            "marker": False,
        },
    ],
    "prompt_order": [
        {
            "character_id": 100001,
            "order": [
                {"identifier": "main", "enabled": True},
                {"identifier": "worldInfoBefore", "enabled": True},
                {"identifier": "personaDescription", "enabled": True},
                {"identifier": "charDescription", "enabled": True},
                {"identifier": "charPersonality", "enabled": True},
                {"identifier": "scenario", "enabled": True},
                {"identifier": "enhanceDefinitions", "enabled": False},
                {"identifier": "nsfw", "enabled": True},
                {"identifier": "worldInfoAfter", "enabled": True},
                {"identifier": "dialogueExamples", "enabled": True},
                {"identifier": "chatHistory", "enabled": True},
                {"identifier": "jailbreak", "enabled": True},
            ],
        }
    ],
}


def test_preset_from_dict_parses_an_st_export() -> None:
    preset = preset_from_dict(SILLYTAVERN_PRESET)

    assert preset.names() == [
        "main",
        "worldInfoBefore",
        "charDescription",
        "charPersonality",
        "scenario",
        "enhanceDefinitions",
        "nsfw",
        "worldInfoAfter",
        "dialogueExamples",
        "chatHistory",
        "jailbreak",
    ]
    assert preset.main_prompt.startswith("Write {{char}}'s next reply")
    assert preset.jailbreak_prompt == "Stay in character."
    assert preset.names_as_prefix is True

    main = preset.block("main")
    assert main is not None
    assert main.marker is False
    assert main.enabled is True

    # marker=true blocks default to disabled (the plugin's preset contract).
    chat_history = preset.block("chatHistory")
    assert chat_history is not None
    assert chat_history.marker is True
    assert chat_history.enabled is False

    # an empty static prompt counts as a marker, and markers default off.
    nsfw = preset.block("nsfw")
    assert nsfw is not None
    assert nsfw.marker is True
    assert nsfw.enabled is False

    # an explicit enabled flag always wins.
    enhance = preset.block("enhanceDefinitions")
    assert enhance is not None
    assert enhance.enabled is False
    assert enhance.content == "Add more detail."


def test_preset_from_dict_can_apply_prompt_order_toggles() -> None:
    preset = preset_from_dict(SILLYTAVERN_PRESET, apply_prompt_order=True)

    assert preset.block("chatHistory").enabled is True
    assert preset.block("nsfw").enabled is True
    assert preset.block("enhanceDefinitions").enabled is False


def test_preset_from_dict_rejects_non_mapping() -> None:
    with pytest.raises(ValueError):
        preset_from_dict(["not", "a", "mapping"])  # type: ignore[arg-type]


def test_build_messages_with_a_parsed_preset() -> None:
    preset = preset_from_dict(SILLYTAVERN_PRESET, apply_prompt_order=True)
    result = build_messages(
        make_card(),
        preset,
        None,
        [Msg("user", "hi")],
        make_options(),
    )

    # post-history instructions stay after the chat history, like SillyTavern.
    assert [message.role for message in result.messages] == ["system", "user", "system"]
    assert result.messages[0].content.startswith("Write Aria's next reply")
    assert "A lighthouse keeper." in result.messages[0].content
    assert "Terse, warm." in result.messages[0].content
    assert "A storm over the bay." in result.messages[0].content
    assert result.messages[1].content == "hi"
    assert result.messages[2].content == "Stay in character."
