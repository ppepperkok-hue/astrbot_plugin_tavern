"""Offline tests for importing a library out of a running SillyTavern.

The network client is replaced by a fake, so these assert the *wiring*: that a
fetched card and a fetched book go through the same import path a dropped file
takes, that a fetched chat appears as a local branch, and that a missing
configuration fails with something a user can act on.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tavern.backends.base import BackendError  # noqa: E402
from tavern.config import TavernConfig  # noqa: E402
from tavern.core import PluginCore, TavernError  # noqa: E402
from tavern.st.chat_store import ChatMessage, ChatMetadata, ChatSession  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCOPE = "aiocqhttp:GroupMessage:123456"


def make_core(tmp_path: Path, **overrides: Any) -> PluginCore:
    raw = {"enabled": True, **overrides}
    config = TavernConfig.from_raw(raw, data_dir=tmp_path)
    core = PluginCore(config)
    core.load()
    return core


class FakeRemote:
    """Stands in for :class:`SillyTavernImportClient`."""

    def __init__(self) -> None:
        self.closed = 0
        self.card = self._card()
        self.book = self._book()
        self.session = self._session()

    @staticmethod
    def _card() -> Any:
        from tavern.st.cards import card_from_dict

        return card_from_dict(
            {
                "spec": "chara_card_v2",
                "data": {
                    "name": "Remote Iris",
                    "description": "from the tavern",
                    "first_mes": "The lamp sweeps past your face.",
                    "character_book": {
                        "name": "Remote Book",
                        "entries": [
                            {"keys": ["fog"], "content": "embedded lore"},
                        ],
                    },
                },
            }
        )

    @staticmethod
    def _book() -> Any:
        from tavern.st import importers

        return importers.convert_bare_entries(
            {
                "name": "Remote Lore",
                "entries": {
                    "0": {"uid": 0, "key": ["lamp"], "content": "a lamp", "comment": "c"},
                },
            }
        )

    @staticmethod
    def _session() -> ChatSession:
        return ChatSession(
            name="main",
            character_name="Remote Iris",
            user_name="User",
            messages=[
                ChatMessage(name="User", mes="where am I?", is_user=True),
                ChatMessage(name="Remote Iris", mes="on a rock", is_user=False),
            ],
            metadata=ChatMetadata(),
        )

    async def list_characters(self) -> list[dict[str, str]]:
        return [{"avatar": "iris.png", "name": "Remote Iris"}]

    async def fetch_character(self, avatar: str) -> Any:
        assert avatar == "iris.png"
        return self.card

    async def list_world_books(self) -> list[dict[str, str]]:
        return [{"file_id": "remote", "name": "Remote Lore"}]

    async def fetch_world_book(self, name: str) -> Any:
        assert name == "Remote Lore"
        return self.book

    async def list_chats(self, avatar: str) -> list[dict[str, str]]:
        return [{"file_name": "main.jsonl", "file_id": "main"}]

    async def fetch_chat(self, avatar: str, file_name: str, *, ch_name: str = "") -> ChatSession:
        return self.session

    async def close(self) -> None:
        self.closed += 1


def wire(core: PluginCore, remote: FakeRemote) -> None:
    core.st_client = lambda: remote  # type: ignore[method-assign]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def configured(tmp_path: Path) -> PluginCore:
    return make_core(tmp_path, backend={"st_base_url": "http://127.0.0.1:8000", "st_cookie": "a=b"})


# ---------------------------------------------------------------------------


def test_st_client_requires_a_configured_address(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    with pytest.raises(TavernError) as info:
        core.st_client()
    assert "st_base_url" in str(info.value)


def test_importing_a_character_uses_the_shared_importer(tmp_path: Path) -> None:
    """The card must land in the library *and* split its embedded book.

    That split lives in `import_card_bytes`, so importing from the network by a
    different route would silently lose it -- which is why this asserts the book
    too, not just the card.
    """
    core = configured(tmp_path)
    remote = FakeRemote()
    wire(core, remote)

    card_id = run(core.st_import_character("iris.png"))
    assert card_id == "Remote Iris"
    assert core.card_ids() == ["Remote Iris"]
    assert remote.closed == 1, "the client must be closed even on success"

    book_ids = core.book_ids()
    assert "Remote Book" in book_ids, "the embedded character_book must be extracted"
    book = core.get_book("Remote Book")
    assert [entry.content for entry in book.entries] == ["embedded lore"]


def test_importing_a_world_book_uses_the_shared_importer(tmp_path: Path) -> None:
    core = configured(tmp_path)
    remote = FakeRemote()
    wire(core, remote)

    book_id = run(core.st_import_world_book("Remote Lore"))
    assert book_id == "Remote Lore"
    assert core.book_ids() == ["Remote Lore"]
    assert [entry.content for entry in core.get_book(book_id).entries] == ["a lamp"]


def test_importing_a_chat_creates_a_local_branch(tmp_path: Path) -> None:
    core = configured(tmp_path)
    remote = FakeRemote()
    wire(core, remote)

    name = run(core.st_import_chat("iris.png", "main.jsonl", character_name="Remote Iris"))
    assert name == "main"
    assert remote.closed == 1

    store = core.store
    assert "main" in store.list_chats("Remote Iris")
    loaded = store.load("Remote Iris", "main")
    assert [message.mes for message in loaded.messages] == ["where am I?", "on a rock"]


def test_importing_a_chat_refuses_to_clobber_a_branch(tmp_path: Path) -> None:
    core = configured(tmp_path)
    remote = FakeRemote()
    wire(core, remote)

    run(core.st_import_chat("iris.png", "main.jsonl", character_name="Remote Iris"))
    with pytest.raises(TavernError) as info:
        run(core.st_import_chat("iris.png", "main.jsonl", character_name="Remote Iris"))
    assert "覆盖" in str(info.value)

    # ...and overwrite is honoured when asked for.
    name = run(
        core.st_import_chat("iris.png", "main.jsonl", character_name="Remote Iris", overwrite=True)
    )
    assert name == "main"


def test_an_empty_remote_chat_is_refused(tmp_path: Path) -> None:
    core = configured(tmp_path)
    remote = FakeRemote()
    remote.session = ChatSession("main", "Remote Iris", "User", [], ChatMetadata())
    wire(core, remote)

    with pytest.raises(TavernError) as info:
        run(core.st_import_chat("iris.png", "main.jsonl", character_name="Remote Iris"))
    assert "空的" in str(info.value)


def test_a_backend_error_reaches_the_caller_readable(tmp_path: Path) -> None:
    """A network failure must surface as BackendError, not a stack trace."""
    core = configured(tmp_path)

    class Broken(FakeRemote):
        async def list_characters(self) -> list[dict[str, str]]:
            raise BackendError("无法连接酒馆 http://127.0.0.1:8000: refused")

    wire(core, Broken())
    with pytest.raises(BackendError) as info:
        run(core.st_list_characters())
    assert "拒绝" in str(info.value) or "refused" in str(info.value)


def test_listing_does_not_import_anything(tmp_path: Path) -> None:
    core = configured(tmp_path)
    wire(core, FakeRemote())

    assert run(core.st_list_characters()) == [{"avatar": "iris.png", "name": "Remote Iris"}]
    assert run(core.st_list_world_books()) == [{"file_id": "remote", "name": "Remote Lore"}]
    assert run(core.st_list_chats("iris.png")) == [{"file_name": "main.jsonl", "file_id": "main"}]
    assert core.card_ids() == []
    assert core.book_ids() == []
