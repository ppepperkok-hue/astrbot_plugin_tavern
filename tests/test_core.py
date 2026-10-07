"""Offline tests for the framework independent core (no AstrBot needed)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tavern.config import TavernConfig  # noqa: E402
from tavern.core import (  # noqa: E402
    PluginCore,
    TavernError,
    apply_regex_rules,
    protect_leading_space,
    render_answer,
    split_message,
    strip_status_blocks,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def make_core(tmp_path: Path, **overrides) -> PluginCore:
    raw = {"enabled": True, **overrides}
    config = TavernConfig.from_raw(raw, data_dir=tmp_path)
    core = PluginCore(config)
    core.load()
    return core


def seed_card(core: PluginCore) -> str:
    payload = (FIXTURES / "cards" / "iris.json").read_bytes()
    return core.import_card_bytes("iris.json", payload)


def seed_book(core: PluginCore, name: str = "lighthouse.json") -> str:
    payload = (FIXTURES / "worldbooks" / name).read_bytes()
    return core.import_worldbook_bytes(name, payload)


SCOPE = "aiocqhttp:GroupMessage:123456"


def test_import_and_bind_card(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    card_id = seed_card(core)
    assert card_id == "Iris"
    assert core.card_ids() == ["Iris"]

    binding = core.bind_card(SCOPE, "iris")  # case insensitive lookup
    assert binding.card_id == "Iris"
    assert core.get_card(binding.card_id).name == "Iris"
    assert core.get_card(binding.card_id).first_mes.startswith("The lamp sweeps")


def test_import_png_card(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    payload = (FIXTURES / "cards" / "iris.png").read_bytes()
    card_id = core.import_card_bytes("iris.png", payload)
    assert card_id == "Iris"
    assert core.get_card(card_id).mes_example.startswith("<START>")


def test_import_rejects_unknown_suffix(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    with pytest.raises(TavernError):
        core.import_card_bytes("card.txt", b"whatever")


def test_duplicate_names_get_unique_ids(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    payload = (FIXTURES / "cards" / "iris.json").read_bytes()
    first = core.import_card_bytes("iris.json", payload)
    second = core.import_card_bytes("iris.json", payload)
    assert first == "Iris"
    assert second != first
    assert len(core.card_ids()) == 2


def test_binding_persists_across_instances(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    seed_card(core)
    seed_book(core)
    core.bind_card(SCOPE, "Iris")
    core.toggle_book(SCOPE, "Lighthouse Lore", True)
    core.save_state()

    reloaded = make_core(tmp_path)
    binding = reloaded.binding(SCOPE)
    assert binding.card_id == "Iris"
    assert binding.worldbooks == ["Lighthouse Lore"]


def test_build_turn_without_card_raises(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    with pytest.raises(TavernError):
        core.build_turn(SCOPE, "hello")


def test_build_turn_injects_world_info(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    seed_card(core)
    seed_book(core)
    core.bind_card(SCOPE, "Iris")
    core.toggle_book(SCOPE, "Lighthouse Lore", True)
    core.record_user(SCOPE, "the Fog is thick tonight", sender_name="路人")

    turn = core.build_turn(SCOPE, "the Fog is thick tonight", sender_name="路人")
    assert turn.card.name == "Iris"
    assert "Lighthouse Lore" in turn.world_book_names
    # constant entry 0 always fires, the disabled/zero-probability ones never do
    assert {entry.uid for entry in turn.activated} == {0}
    joined = "\n".join(message.content for message in turn.request.messages)
    # stable blocks (card + world info before char) land in the system prompt,
    # the rest of the conversation is handed over as messages
    assert "44 metres tall" in turn.request.system_prompt
    assert "Iris is a lighthouse keeper" in turn.request.system_prompt
    assert joined


def test_build_turn_uses_conversation_history_when_given(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    seed_card(core)
    core.bind_card(SCOPE, "Iris")

    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
    ]
    # the caller passes history *including* the current turn, as AstrBot does
    turn = core.build_turn(SCOPE, "first", conversation_history=history)
    contents = [message.content for message in turn.request.messages]
    assert any("first" in content for content in contents)
    # the current input must not be duplicated when AstrBot already appended it
    assert contents.count("first") == 1


def test_history_records_and_greeting(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    seed_card(core)
    core.bind_card(SCOPE, "Iris")

    greeting = core.greeting(SCOPE)
    assert greeting.startswith("The lamp sweeps")
    assert core.greeting(SCOPE) == ""  # only once

    core.record_user(SCOPE, "hello", sender_name="小明")
    core.record_assistant(SCOPE, "hi there")
    preview = core.history_preview(SCOPE)
    assert any("小明: hello" in line for line in preview)
    assert any("Iris: hi there" in line for line in preview)

    # the activation state advanced one turn per assistant reply
    assert core.activation_state(SCOPE).turn == 2


def test_reset_chat_clears_history(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    seed_card(core)
    core.bind_card(SCOPE, "Iris")
    core.record_user(SCOPE, "hello")
    assert core.history_preview(SCOPE)

    binding = core.reset_chat(SCOPE)
    assert binding.chat_name != "main"
    assert core.history_preview(SCOPE) == []


def test_toggle_book_round_trip(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    seed_book(core)
    binding, enabled = core.toggle_book(SCOPE, "lighthouse")
    assert enabled is True
    assert binding.worldbooks == ["Lighthouse Lore"]

    binding, enabled = core.toggle_book(SCOPE, "Lighthouse Lore")
    assert enabled is False
    assert binding.worldbooks == []


def test_unknown_card_or_book_lists_options(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    seed_card(core)
    with pytest.raises(TavernError) as card_error:
        core.get_card("nope")
    assert "/tavern list" in str(card_error.value)

    with pytest.raises(TavernError) as book_error:
        core.get_book("nope")
    assert "worldbook list" in str(book_error.value)


def test_worldbook_disabled_by_config(tmp_path: Path) -> None:
    core = make_core(tmp_path, worldbook={"enabled": False})
    seed_card(core)
    seed_book(core)
    core.bind_card(SCOPE, "Iris")
    core.toggle_book(SCOPE, "Lighthouse Lore", True)

    turn = core.build_turn(SCOPE, "the Fog is thick")
    assert turn.activated == []
    joined = "\n".join(message.content for message in turn.request.messages)
    assert "44 metres tall" not in joined


def test_injection_cap_limits_entries(tmp_path: Path) -> None:
    core = make_core(tmp_path, worldbook={"injection_cap": 1})
    seed_card(core)
    seed_book(core)
    core.bind_card(SCOPE, "Iris")
    core.toggle_book(SCOPE, "Lighthouse Lore", True)

    turn = core.build_turn(SCOPE, "the Sundered wreck and the Keeper")
    assert len(turn.activated) <= 1


def test_no_context_budget_keeps_every_history_message(tmp_path: Path) -> None:
    """The default is *do not trim* -- the plugin cannot discover the window.

    AstrBot owns the provider layer and exposes no model metadata, so guessing a
    window would silently delete conversation. Until the user fills in
    ``backend.max_context_tokens`` every turn must carry the whole history.
    """
    core = make_core(tmp_path)
    seed_card(core)
    core.bind_card(SCOPE, "Iris")

    history = [{"role": "user", "content": f"turn {index} " + "x" * 200} for index in range(30)]
    turn = core.build_turn(SCOPE, "now", conversation_history=history)

    contents = [message.content for message in turn.request.messages]
    kept = [content for content in contents if content.startswith("turn ")]
    assert len(kept) == 30
    assert "turn 0 " in kept[0]


def test_context_budget_trims_the_oldest_history(tmp_path: Path) -> None:
    """A configured window drops whole messages from the oldest end.

    Messages are never truncated mid-message, the newest ones survive, and the
    system prompt (which is *not* part of the history) is untouched -- the budget
    is reserved for it rather than charged against it.
    """
    core = make_core(
        tmp_path,
        backend={"max_context_tokens": 1200, "reply_reserve_tokens": 100, "keep_last_messages": 1},
    )
    seed_card(core)
    core.bind_card(SCOPE, "Iris")

    # ~135 estimated tokens each, so 30 of them cannot fit 1200 - 100 - the fixed
    # blocks; the tail must survive and the head must go.
    history = [{"role": "user", "content": f"turn {index} " + "x" * 400} for index in range(30)]
    turn = core.build_turn(SCOPE, "now", conversation_history=history)

    contents = [message.content for message in turn.request.messages]
    kept = [content for content in contents if content.startswith("turn ")]
    assert 0 < len(kept) < 30, "the budget must drop something but not everything"
    # oldest-first suffix: the last turn is present, the first is not
    assert kept[-1].startswith("turn 29 ")
    assert not any(content.startswith("turn 0 ") for content in kept)
    assert turn.request.system_prompt, "the system prompt is never dropped"


def test_context_budget_always_keeps_the_last_messages(tmp_path: Path) -> None:
    """``keep_last_messages`` wins over an impossible budget.

    A window smaller than a single exchange must not produce an empty request: the
    character has to see what was just said, even if that overruns the window.
    """
    core = make_core(
        tmp_path,
        backend={"max_context_tokens": 1, "reply_reserve_tokens": 0, "keep_last_messages": 3},
    )
    seed_card(core)
    core.bind_card(SCOPE, "Iris")

    history = [{"role": "user", "content": f"turn {index}"} for index in range(10)]
    turn = core.build_turn(SCOPE, "now", conversation_history=history)

    kept = [
        message.content for message in turn.request.messages if message.content.startswith("turn ")
    ]
    assert len(kept) == 3
    assert [content.split()[1] for content in kept] == ["7", "8", "9"]


def test_context_budget_does_not_starve_world_info_scan(tmp_path: Path) -> None:
    """Trimming affects what is *sent*, not what is *scanned*.

    The scan deliberately runs over the whole history -- a keyword mentioned forty
    messages ago should still activate its entry -- so an entry triggered by a
    message the budget then drops must still be reported as activated.
    """
    core = make_core(
        tmp_path,
        worldbook={"enabled": True},
        backend={"max_context_tokens": 1200, "reply_reserve_tokens": 100, "keep_last_messages": 1},
    )
    seed_card(core)
    seed_book(core)
    core.bind_card(SCOPE, "Iris")
    core.toggle_book(SCOPE, "Lighthouse Lore", True)

    history = [
        {"role": "user", "content": "the Sundered wreck and the Keeper"},
        *({"role": "user", "content": f"chatter {index} " + "y" * 400} for index in range(20)),
    ]
    turn = core.build_turn(SCOPE, "now", conversation_history=history)

    assert turn.activated, "the scan must still see the trimmed-away trigger message"
    kept = [message.content for message in turn.request.messages]
    assert not any("Sundered wreck" in content for content in kept), "but it is not sent"


def test_context_budget_keeps_depth_injection_coherent(tmp_path: Path) -> None:
    """The injection index is derived from the *trimmed* history.

    ``at_depth`` is inserted before ``history[index]`` inside ``build_messages``.
    Deriving the index before trimming would point past the end of the kept
    history, so the injection has to be computed against what actually survives.
    """
    core = make_core(
        tmp_path,
        worldbook={"enabled": True},
        backend={"max_context_tokens": 2500, "reply_reserve_tokens": 100, "keep_last_messages": 1},
    )
    seed_card(core)
    seed_book(core)
    core.bind_card(SCOPE, "Iris")
    core.toggle_book(SCOPE, "Lighthouse Lore", True)

    history = [
        *({"role": "user", "content": f"chatter {index} " + "z" * 400} for index in range(20)),
        {"role": "user", "content": "the Fog is thick tonight"},
    ]
    turn = core.build_turn(SCOPE, "now", conversation_history=history)

    # The turn still assembles, the system prompt exists, and the in-chat
    # injection (if any) is inside the message list rather than lost.
    assert turn.request.system_prompt
    assert turn.request.messages


def test_reload_library_picks_up_new_files(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    assert core.card_ids() == []
    (core.config.cards_dir / "iris.json").write_bytes(
        (FIXTURES / "cards" / "iris.json").read_bytes()
    )
    counts = core.reload_library()
    assert counts["cards"] == 1
    assert core.card_ids() == ["Iris"]


def test_state_file_is_valid_json(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    seed_card(core)
    core.bind_card(SCOPE, "Iris")
    payload = json.loads(core.config.state_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert SCOPE in payload["bindings"]


# ----------------------------------------------------------------------
# rendering helpers
# ----------------------------------------------------------------------


def test_strip_status_blocks() -> None:
    text = "台词\n<Status>HP 10/10</Status>\n```status\nMP: 3\n```\n结束"
    cleaned = strip_status_blocks(text)
    assert "HP" not in cleaned
    assert "MP" not in cleaned
    assert "台词" in cleaned and "结束" in cleaned


def test_apply_regex_rules() -> None:
    assert apply_regex_rules("「你好」", ["^「(.*)」$=>\\1"]) == "你好"
    # malformed rules are skipped instead of raising
    assert apply_regex_rules("abc", ["([=>"]) == "abc"
    assert apply_regex_rules("abc", ["no-arrow-here"]) == "abc"


def test_split_message_prefers_paragraphs() -> None:
    text = "first paragraph\n\nsecond paragraph\n\nthird paragraph"
    chunks = split_message(text, 20)
    assert len(chunks) >= 2
    assert all(len(chunk) <= 20 for chunk in chunks)
    assert "first paragraph" in chunks[0]


def test_split_message_keeps_short_text_whole() -> None:
    assert split_message("短回复", 100) == ["短回复"]
    assert split_message("", 100) == []


def test_split_message_hard_splits_long_line() -> None:
    chunks = split_message("x" * 25, 10)
    assert chunks == ["x" * 10, "x" * 10, "x" * 5]


def test_protect_leading_space() -> None:
    assert protect_leading_space("  indented") == "\u200b  indented"
    assert protect_leading_space("plain") == "plain"
    assert protect_leading_space("trailing \n") == "trailing \n\u200b"


def test_render_answer_applies_pipeline(tmp_path: Path) -> None:
    core = make_core(
        tmp_path,
        render={
            "max_chars_per_message": 40,
            "keep_leading_space": True,
            "strip_status_bar": True,
            "regex_rules": ["待替换=>已替换"],
        },
    )
    answer = "第一段 待替换\n\n<Status>HP 1</Status>\n\n" + "很长的第二段" * 10
    chunks = render_answer(answer, config=core.config)
    assert chunks
    joined = "".join(chunks)
    assert "已替换" in joined
    assert "HP 1" not in joined
    assert all(len(chunk) <= 41 for chunk in chunks)  # zero width space allowed
