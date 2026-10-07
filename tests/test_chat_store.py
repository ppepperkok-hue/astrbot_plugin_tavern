"""Offline tests for the SillyTavern compatible chat store."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from astrbot_plugin_tavern.st.chat_store import (  # noqa: E402
    ChatMessage,
    ChatMetadata,
    ChatNotFound,
    ChatSession,
    ChatStore,
    ChatStoreError,
    sanitize_filename,
    session_to_prompt_messages,
)

CHARACTER = "Vesper"


def make_message(mes: str, is_user: bool = False, is_system: bool = False) -> ChatMessage:
    """Helper building a message with the sender name implied by its flags."""
    if is_system:
        name = "System"
    else:
        name = "User" if is_user else CHARACTER
    return ChatMessage(name=name, mes=mes, is_user=is_user, is_system=is_system)


@pytest.fixture()
def store(tmp_path: Path) -> ChatStore:
    return ChatStore(tmp_path / "plugin_data", user_name="Tester")


# --- message level ---------------------------------------------------------


def test_message_round_trip_keeps_unknown_fields() -> None:
    message = ChatMessage(
        name=CHARACTER,
        mes="hello",
        is_user=False,
        is_system=False,
        send_date="2026-01-01T00:00:00.000Z",
        extra={"swipes": ["a", "b"], "reasoning": "because"},
    )
    parsed = ChatMessage.from_st_dict(message.to_st_dict())
    assert parsed.to_st_dict() == message.to_st_dict()
    assert parsed.extra["swipes"] == ["a", "b"]
    assert parsed.send_date == "2026-01-01T00:00:00.000Z"


def test_message_unknown_top_level_fields_are_kept_in_extra() -> None:
    parsed = ChatMessage.from_st_dict(
        {
            "name": CHARACTER,
            "mes": "hi",
            "is_user": False,
            "send_date": 1767225600000,  # epoch milliseconds
            "swipe_id": 1,
        }
    )
    assert parsed.extra["swipe_id"] == 1
    assert parsed.send_date.startswith("2026-01-01")
    assert "1767225600000" not in parsed.send_date


def test_message_missing_send_date_defaults_to_now() -> None:
    parsed = ChatMessage.from_st_dict({"name": "User", "mes": "hi", "is_user": True})
    assert parsed.send_date
    assert "T" in parsed.send_date


def test_message_role_mapping() -> None:
    assert make_message("a").role == "assistant"
    assert make_message("a").is_assistant is True
    assert make_message("a", is_user=True).role == "user"
    assert make_message("a", is_user=True).is_assistant is False
    system = make_message("a", is_system=True)
    assert system.role == "system"
    assert system.is_assistant is False
    # is_system wins over is_user in ST exports of hidden messages
    assert make_message("a", is_user=True, is_system=True).role == "system"


# --- session / header ------------------------------------------------------


def test_header_line_field_names_round_trip(store: ChatStore) -> None:
    session = ChatSession(
        name="first_chat",
        character_name=CHARACTER,
        user_name="Tester",
        messages=[make_message("greeting"), make_message("hello", is_user=True)],
        metadata=ChatMetadata(integrity=""),
    )
    text = session.to_jsonl()
    assert text.endswith("\n")
    header = json.loads(text.splitlines()[0])
    assert set(header) == {"user_name", "character_name", "chat_metadata"}
    assert header["user_name"] == "Tester"
    assert header["character_name"] == CHARACTER
    assert isinstance(header["chat_metadata"], dict)

    store.save(session)
    reloaded = store.load(CHARACTER, "first_chat")
    assert reloaded.user_name == "Tester"
    assert reloaded.character_name == CHARACTER
    assert reloaded.chat_items == 2  # total lines - 1
    assert [m.mes for m in reloaded.messages] == ["greeting", "hello"]
    assert reloaded.messages[1].is_user is True
    assert reloaded.messages[0].is_system is False


# --- chat metadata ---------------------------------------------------------


def test_timed_world_info_uses_camel_case_and_nested_buckets() -> None:
    metadata = ChatMetadata()
    metadata.mark_timed("lighthouse", 3, start=2, end=6, hash_="abc")
    payload = metadata.to_st_dict()
    assert "timedWorldInfo" in payload
    assert "timed_world_info" not in payload
    assert payload["timedWorldInfo"] == {
        "sticky": {
            "lighthouse.3": {
                "hash": "abc",
                "start": 2,
                "end": 6,
                "protected": False,
            }
        }
    }
    assert set(payload["timedWorldInfo"]["sticky"]["lighthouse.3"]) == {
        "hash",
        "start",
        "end",
        "protected",
    }

    parsed = ChatMetadata.from_st_dict(payload)
    assert parsed.timed_world_info == metadata.timed_world_info
    assert parsed.to_st_dict()["timedWorldInfo"] == payload["timedWorldInfo"]


def test_timed_world_info_accepts_flat_and_mixed_layout() -> None:
    flat = ChatMetadata.from_st_dict({"timedWorldInfo": {"book.1": {"start": 0, "end": 3}}})
    assert flat.timed_world_info["book.1"]["bucket"] == "sticky"
    assert flat.timed_world_info["book.1"]["protected"] is False

    mixed = ChatMetadata.from_st_dict(
        {
            "timedWorldInfo": {
                "sticky": {"book.1": {"start": 0, "end": 3, "hash": "h", "protected": True}},
                "cooldown": {"book.2": {"start": 4, "end": 9}},
                "book.9": {"start": 1, "end": 2},
            }
        }
    )
    assert set(mixed.timed_world_info) == {"book.1", "book.2", "book.9"}
    assert mixed.timed_world_info["book.1"]["protected"] is True
    assert mixed.timed_world_info["book.2"]["bucket"] == "cooldown"
    assert mixed.to_st_dict()["timedWorldInfo"]["cooldown"]["book.2"]["start"] == 4


def test_is_timed_active_half_open_interval() -> None:
    metadata = ChatMetadata()
    metadata.mark_timed("book", 7, start=3, end=5)
    assert metadata.is_timed_active("book", 7, 3) is True  # start inclusive
    assert metadata.is_timed_active("book", 7, 4) is True
    assert metadata.is_timed_active("book", 7, 5) is False  # end exclusive
    assert metadata.is_timed_active("book", 7, 2) is False
    assert metadata.is_timed_active("book", 8, 4) is False  # other uid
    assert metadata.is_timed_active("other", 7, 4) is False  # other book

    empty = ChatMetadata()
    empty.mark_timed("book", 1, start=4, end=4)
    assert empty.is_timed_active("book", 1, 4) is False


def test_metadata_preserves_unknown_keys_and_world_info() -> None:
    parsed = ChatMetadata.from_st_dict(
        {"integrity": "x", "persona": "Alice", "world_info": "lighthouse"}
    )
    assert parsed.world_info == "lighthouse"
    assert parsed.extra == {"persona": "Alice"}
    payload = parsed.to_st_dict()
    assert payload["persona"] == "Alice"
    assert payload["world_info"] == "lighthouse"
    assert payload["note_prompt"] == "" and payload["note_interval"] == 0


def test_prune_timed_drops_expired_unprotected_only() -> None:
    metadata = ChatMetadata()
    metadata.mark_timed("book", 1, start=0, end=2)
    metadata.mark_timed("book", 2, start=0, end=2, protected=True)
    metadata.prune_timed(turn=5)
    assert "book.1" not in metadata.timed_world_info
    assert "book.2" in metadata.timed_world_info


# --- tolerant import -------------------------------------------------------


def test_from_jsonl_empty_file() -> None:
    session = ChatSession.from_jsonl("", name="empty")
    assert session.name == "empty"
    assert session.messages == []
    assert session.character_name == ""
    assert session.integrity_ok is True


def test_from_jsonl_header_only() -> None:
    header = {"user_name": "U", "character_name": "C", "chat_metadata": {}}
    session = ChatSession.from_jsonl(json.dumps(header) + "\n")
    assert session.user_name == "U"
    assert session.character_name == "C"
    assert session.messages == []


def test_from_jsonl_crlf_bom_and_missing_trailing_newline() -> None:
    text = (
        "\ufeff"
        + '{"user_name":"U","character_name":"C","chat_metadata":{}}\r\n'
        + '{"name":"C","is_user":false,"mes":"one","send_date":"2026-01-01T00:00:00Z"}\r\n'
        + '{"name":"U","is_user":true,"mes":"two"}'
    )
    session = ChatSession.from_jsonl(text)
    assert session.character_name == "C"
    assert len(session.messages) == 2
    assert session.messages[1].mes == "two"
    assert session.messages[0].is_system is False  # is_system omitted entirely


def test_from_jsonl_extra_message_fields_and_blank_lines() -> None:
    text = (
        '{"user_name":"U","character_name":"C","chat_metadata":{"timedWorldInfo":{}}}\n'
        "\n"
        '{"name":"C","is_user":false,"mes":"deep","swipe_id":2,"extra":{"swipes":["a"]},'
        '"api":"koboldcpp","model":"m"}\n'
        '{"name":"C","is_user":false,"mes":"third","send_date":1767225600000}\n'
    )
    session = ChatSession.from_jsonl(text)
    assert len(session.messages) == 2
    first = session.messages[0]
    assert first.extra["swipe_id"] == 2
    assert first.extra["swipes"] == ["a"]
    assert first.extra["api"] == "koboldcpp"
    assert session.messages[1].send_date.startswith("2026-01-01")


def test_from_jsonl_skips_unparsable_lines() -> None:
    text = (
        '{"user_name":"U","character_name":"C","chat_metadata":{}}\n'
        "not json at all\n"
        '{"name":"C","is_user":false,"mes":"survives"}\n'
    )
    session = ChatSession.from_jsonl(text)
    assert [m.mes for m in session.messages] == ["survives"]


def test_from_jsonl_detects_integrity_mismatch_without_raising() -> None:
    session = ChatSession(
        name="c",
        character_name=CHARACTER,
        user_name="Tester",
        messages=[make_message("secret")],
        metadata=ChatMetadata(),
    )
    text = session.to_jsonl()
    header, *rest = text.splitlines()
    payload = json.loads(header)
    payload["chat_metadata"]["integrity"] = "deadbeef"
    tampered = "\n".join([json.dumps(payload), *rest]) + "\n"
    parsed = ChatSession.from_jsonl(tampered)
    assert parsed.integrity_ok is False
    assert [m.mes for m in parsed.messages] == ["secret"]


def test_estimated_tokens_uses_injected_counter() -> None:
    session = ChatSession(
        name="c",
        character_name=CHARACTER,
        user_name="Tester",
        messages=[make_message("abcd"), make_message("ef", is_user=True)],
        metadata=ChatMetadata(),
    )
    assert session.estimated_tokens(counter=len) == 6
    assert session.estimated_tokens() > 0


# --- store io --------------------------------------------------------------


def test_save_and_load_are_atomic_and_leave_no_tmp(store: ChatStore) -> None:
    session = store.create(CHARACTER, "atomic")
    session.messages.append(make_message("first"))
    path = store.save(session)
    assert path == store.root / "chats" / CHARACTER / "atomic.jsonl"
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == session.to_jsonl()
    assert list(path.parent.iterdir()) == [path]  # no .tmp leftovers
    assert store.load(CHARACTER, "atomic").messages[0].mes == "first"


def test_save_sets_integrity_and_load_validates_it(store: ChatStore) -> None:
    session = store.create(CHARACTER, "guarded")
    session.messages.append(make_message("keep me"))
    path = store.save(session)
    assert session.metadata.integrity == session.content_hash()
    loaded = store.load(CHARACTER, "guarded")
    assert loaded.integrity_ok is True

    # Tamper with the message body on disk, keep the stale slug.
    tampered = path.read_text(encoding="utf-8").replace("keep me", "changed")
    path.write_text(tampered, encoding="utf-8")
    after = store.load(CHARACTER, "guarded")
    assert after.integrity_ok is False  # flagged, never raised
    assert after.messages[0].mes == "changed"
    assert after.metadata.integrity  # stored slug is preserved verbatim


def test_save_failure_keeps_previous_file_and_raises(store: ChatStore) -> None:
    session = store.create(CHARACTER, "keep")
    session.messages.append(make_message("good content"))
    path = store.save(session)
    original = path.read_text(encoding="utf-8")

    # A directory sitting on the target path makes os.replace() fail.
    path.rename(path.with_name("keep.jsonl.bak"))
    path.mkdir()
    session.messages.append(make_message("never landed"))
    with pytest.raises(ChatStoreError):
        store.save(session)
    assert path.is_dir()  # the half-finished write never replaced anything
    assert not list(path.parent.glob("*.tmp"))

    path.rmdir()
    path.with_name("keep.jsonl.bak").rename(path)
    assert path.read_text(encoding="utf-8") == original
    assert [m.mes for m in store.load(CHARACTER, "keep").messages] == ["good content"]


def test_io_errors_are_wrapped_in_chat_store_error(store: ChatStore) -> None:
    (store.root / "chats" / CHARACTER).mkdir(parents=True)
    (store.root / "chats" / "Broken").write_text("not a directory", encoding="utf-8")
    with pytest.raises(ChatStoreError):
        store.create("Broken", "nope")


def test_load_missing_chat_raises_chat_not_found(store: ChatStore) -> None:
    with pytest.raises(ChatNotFound):
        store.load(CHARACTER, "ghost")
    assert issubclass(ChatNotFound, ChatStoreError)


def test_load_invalid_chat_name_is_rejected(store: ChatStore) -> None:
    store.create(CHARACTER, "real")
    with pytest.raises(ChatStoreError):
        store.load(CHARACTER, "../escape")
    with pytest.raises(ChatStoreError):
        store.load(CHARACTER, "")


def test_load_empty_and_header_only_files(store: ChatStore) -> None:
    directory = store.character_dir(CHARACTER)
    directory.mkdir(parents=True)
    (directory / "blank.jsonl").write_text("", encoding="utf-8")
    (directory / "headeronly.jsonl").write_text(
        '{"user_name":"T","character_name":"Vesper","chat_metadata":{}}\n', encoding="utf-8"
    )
    blank = store.load(CHARACTER, "blank")
    assert blank.messages == []
    assert blank.character_name == CHARACTER  # filled from the directory name
    assert blank.integrity_ok is True
    only_header = store.load(CHARACTER, "headeronly")
    assert only_header.messages == []
    assert only_header.character_name == CHARACTER


def test_append_message_persists_and_missing_chat_raises(store: ChatStore) -> None:
    store.create(CHARACTER, "log")
    session = store.append_message(CHARACTER, "log", make_message("one"))
    store.append_message(CHARACTER, "log", make_message("two", is_user=True))
    assert len(session.messages) == 1  # returned snapshot reflects that call
    reloaded = store.load(CHARACTER, "log")
    assert [m.mes for m in reloaded.messages] == ["one", "two"]
    with pytest.raises(ChatNotFound):
        store.append_message(CHARACTER, "missing", make_message("x"))


def test_list_chats_is_newest_first(store: ChatStore) -> None:
    import os
    import time

    assert store.list_chats(CHARACTER) == []
    for name in ("old", "new", "middle"):
        store.create(CHARACTER, name)
        time.sleep(0.01)
    os.utime(store.path_for(CHARACTER, "old"), (1000, 1000))
    os.utime(store.path_for(CHARACTER, "middle"), (2000, 2000))
    os.utime(store.path_for(CHARACTER, "new"), (3000, 3000))
    assert store.list_chats(CHARACTER) == ["new", "middle", "old"]
    assert store.list_chats("Nobody") == []


def test_delete_and_rename(store: ChatStore) -> None:
    store.create(CHARACTER, "one")
    store.create(CHARACTER, "two")
    assert store.delete(CHARACTER, "one") is True
    assert store.delete(CHARACTER, "one") is False
    renamed = store.rename(CHARACTER, "two", "renamed")
    assert renamed.name == "renamed.jsonl"
    assert store.list_chats(CHARACTER) == ["renamed"]
    with pytest.raises(ChatNotFound):
        store.rename(CHARACTER, "gone", "x")
    store.create(CHARACTER, "clash")
    with pytest.raises(ChatStoreError):
        store.rename(CHARACTER, "renamed", "clash")


def test_fork_truncates_at_index(store: ChatStore) -> None:
    session = store.create(CHARACTER, "origin")
    for index in range(5):
        session.messages.append(make_message(f"m{index}", is_user=index % 2 == 1))
    store.save(session)

    full = store.fork(CHARACTER, "origin", "full")
    assert [m.mes for m in full.messages] == ["m0", "m1", "m2", "m3", "m4"]

    partial = store.fork(CHARACTER, "origin", "partial", at_index=3)
    assert [m.mes for m in partial.messages] == ["m0", "m1", "m2"]
    assert partial.name == "partial"
    assert store.load(CHARACTER, "partial").chat_items == 3
    # the original is untouched
    assert store.load(CHARACTER, "origin").chat_items == 5

    empty = store.fork(CHARACTER, "origin", "empty", at_index=0)
    assert empty.messages == []
    assert store.load(CHARACTER, "empty").messages == []

    with pytest.raises(ChatStoreError):
        store.fork(CHARACTER, "origin", "bad", at_index=99)
    with pytest.raises(ChatStoreError):
        store.fork(CHARACTER, "origin", "bad", at_index=-1)


def test_search_returns_file_line_numbers(store: ChatStore) -> None:
    first = store.create(CHARACTER, "chat_a")
    first.messages.append(make_message("the lighthouse is dark"))
    first.messages.append(make_message("nothing here", is_user=True))
    store.save(first)

    second = store.create(CHARACTER, "chat_b")
    second.messages.append(make_message("sunny day"))
    second.messages.append(make_message("lighthouse keeper speaks", is_user=True))
    store.save(second)

    hits = store.search(CHARACTER, "lighthouse")
    assert [(name, line) for name, line, _ in hits] == [("chat_b", 3), ("chat_a", 2)]
    assert hits[0][2].is_user is True
    assert store.search(CHARACTER, "absent") == []
    assert store.search(CHARACTER, "") == []
    # line numbers really point at the message inside the file
    lines = store.path_for(CHARACTER, "chat_b").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[2])["mes"] == "lighthouse keeper speaks"


# --- file name handling ----------------------------------------------------


def test_sanitize_filename_removes_windows_illegal_chars() -> None:
    assert sanitize_filename('a\\b/c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert sanitize_filename("  spaced  ") == "spaced"
    assert sanitize_filename("dots...") == "dots"
    assert sanitize_filename("keep.dots.in.name") == "keep.dots.in.name"
    assert sanitize_filename("line\nbreak") == "line_break"
    assert sanitize_filename("") == ""
    assert sanitize_filename("///") == ""


def test_store_sanitizes_directory_and_rejects_empty_names(store: ChatStore) -> None:
    session = store.create("Vesper: The | Keeper?", "chat")
    path = store.save(session)
    assert path.parent.name == sanitize_filename("Vesper: The | Keeper?")
    assert path.parent.name == "Vesper_ The _ Keeper"
    assert "/" not in path.parent.name and ":" not in path.parent.name
    assert store.list_chats("Vesper: The | Keeper?") == ["chat"]

    with pytest.raises(ChatStoreError):
        store.create("///", "x")
    with pytest.raises(ChatStoreError):
        store.create(CHARACTER, "")


# --- prompt mapping --------------------------------------------------------


def test_session_to_prompt_messages_role_mapping() -> None:
    session = ChatSession(
        name="c",
        character_name=CHARACTER,
        user_name="Tester",
        messages=[
            make_message("system note", is_system=True),
            make_message("hi there"),
            make_message("hello", is_user=True),
            make_message("hidden", is_user=True, is_system=True),
        ],
        metadata=ChatMetadata(),
    )
    messages = session_to_prompt_messages(session)
    assert messages == [
        {"role": "system", "content": "system note", "name": "System"},
        {"role": "assistant", "content": "hi there", "name": CHARACTER},
        {"role": "user", "content": "hello", "name": "User"},
        {"role": "system", "content": "hidden", "name": "System"},
    ]
    assert messages == session.to_prompt_messages()
    assert session_to_prompt_messages(ChatSession("c", "n", "u", [], ChatMetadata())) == []
