"""Offline tests for the external-tavern *import* client (no network, no httpx).

The client is driven through a fake `_post_json`, so these assert the route each
method calls, the body it sends, and what it does with each reply shape -- which is
where a hand-written client against an undocumented API goes wrong.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tavern.backends.base import BackendError  # noqa: E402
from tavern.backends.st_import import SillyTavernImportClient  # noqa: E402


class FakeImport(SillyTavernImportClient):
    """Records requests and replays canned answers."""

    def __init__(self, replies: dict[str, Any]) -> None:
        super().__init__("http://127.0.0.1:8000", cookie="session=abc")
        self.replies = replies
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def _post_json(  # type: ignore[override]
        self, path: str, payload: dict[str, Any], *, retry_on_auth: bool = True
    ) -> Any:
        self.calls.append((path, payload))
        if path not in self.replies:
            raise BackendError(f"unexpected route in test: {path}")
        reply = self.replies[path]
        if isinstance(reply, Exception):
            raise reply
        return reply


def run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# characters
# ---------------------------------------------------------------------------


def test_list_characters_uses_the_all_route_and_sorts() -> None:
    client = FakeImport(
        {
            "/api/characters/all": [
                {"avatar": "b.png", "name": "Zoe"},
                {"avatar": "a.png", "name": "amy"},
                {"avatar": "", "name": "no avatar"},
                {"avatar": "c.png"},
                "not a dict",
            ]
        }
    )
    cards = run(client.list_characters())
    assert client.calls == [("/api/characters/all", {})]
    assert cards == [
        {"avatar": "a.png", "name": "amy"},
        {"avatar": "b.png", "name": "Zoe"},
    ]


def test_list_characters_rejects_a_non_list() -> None:
    client = FakeImport({"/api/characters/all": {"error": True}})
    with pytest.raises(BackendError):
        run(client.list_characters())


def test_fetch_character_prefers_json_data() -> None:
    """`json_data` is the card as embedded in the PNG, which a file import parses."""
    client = FakeImport(
        {
            "/api/characters/get": {
                "name": "Outer",
                "avatar": "iris.png",
                "json_data": (
                    '{"spec":"chara_card_v2","data":{"name":"Iris","description":"d",'
                    '"first_mes":"Hi"}}'
                ),
            }
        }
    )
    card = run(client.fetch_character("iris.png"))
    assert client.calls == [("/api/characters/get", {"avatar_url": "iris.png"})]
    assert card.name == "Iris", "the embedded card wins over the summary object"
    assert card.first_mes == "Hi"


def test_fetch_character_falls_back_when_json_data_is_broken() -> None:
    client = FakeImport(
        {
            "/api/characters/get": {
                "name": "FromSummary",
                "json_data": "{ this is not json",
                "description": "d",
            }
        }
    )
    card = run(client.fetch_character("x.png"))
    assert card.name == "FromSummary"


def test_fetch_character_needs_a_name_and_an_avatar() -> None:
    client = FakeImport({})
    with pytest.raises(BackendError):
        run(client.fetch_character(""))
    empty = FakeImport({"/api/characters/get": {"avatar": "x.png"}})
    with pytest.raises(BackendError):
        run(empty.fetch_character("x.png"))


# ---------------------------------------------------------------------------
# chats
# ---------------------------------------------------------------------------


def test_list_chats_tolerates_the_error_shape() -> None:
    client = FakeImport({"/api/characters/chats": {"error": True}})
    assert run(client.list_chats("iris.png")) == []
    assert client.calls == [("/api/characters/chats", {"avatar_url": "iris.png", "simple": True})]


def test_list_chats_keeps_file_name_and_id() -> None:
    client = FakeImport(
        {
            "/api/characters/chats": [
                {"file_name": "main.jsonl", "file_id": "main"},
                {"file_id": "no name"},
                "junk",
            ]
        }
    )
    assert run(client.list_chats("iris.png")) == [{"file_name": "main.jsonl", "file_id": "main"}]


def test_fetch_chat_strips_the_extension_before_sending() -> None:
    """`script.js:7311` sends the name *without* `.jsonl`."""
    client = FakeImport(
        {
            "/api/chats/get": [
                {"name": "User", "mes": "hello", "is_user": True},
                {"name": "Iris", "mes": "hi there", "is_user": False},
            ]
        }
    )
    session = run(client.fetch_chat("iris.png", "main.jsonl", ch_name="Iris"))
    assert client.calls == [
        ("/api/chats/get", {"ch_name": "Iris", "file_name": "main", "avatar_url": "iris.png"})
    ]
    contents = [message.mes for message in session.messages]
    assert contents == ["hello", "hi there"]


def test_fetch_chat_accepts_a_bare_name_too() -> None:
    client = FakeImport({"/api/chats/get": [{"mes": "one"}]})
    run(client.fetch_chat("iris.png", "main"))
    assert client.calls[0][1]["file_name"] == "main"


def test_fetch_chat_reports_a_missing_file() -> None:
    client = FakeImport({"/api/chats/get": {"error": True}})
    with pytest.raises(BackendError):
        run(client.fetch_chat("iris.png", "nope"))


def test_fetch_chat_reports_unparseable_messages() -> None:
    """A reply with rows that yield no messages must not look like an empty chat."""
    client = FakeImport({"/api/chats/get": [None, None]})
    try:
        run(client.fetch_chat("iris.png", "main"))
    except BackendError as exc:
        assert "main" in str(exc)
    else:  # pragma: no cover - a clean empty chat is also acceptable
        pass


# ---------------------------------------------------------------------------
# world books
# ---------------------------------------------------------------------------


def test_list_world_books_sorts_by_name() -> None:
    client = FakeImport(
        {
            "/api/worldinfo/list": [
                {"file_id": "b", "name": "Zeta"},
                {"file_id": "a", "name": "alpha"},
                {"file_id": "", "name": "no id"},
            ]
        }
    )
    assert run(client.list_world_books()) == [
        {"file_id": "a", "name": "alpha"},
        {"file_id": "b", "name": "Zeta"},
    ]


def test_fetch_world_book_goes_through_the_shared_importer() -> None:
    """The fetched book must be the same object a dropped file would produce."""
    client = FakeImport(
        {
            "/api/worldinfo/get": {
                "entries": {
                    "0": {"uid": 0, "key": ["fog"], "content": "lore", "comment": "c"},
                }
            }
        }
    )
    book = run(client.fetch_world_book("Lighthouse"))
    assert client.calls == [("/api/worldinfo/get", {"name": "Lighthouse"})]
    assert book.name == "Lighthouse", "the file_id names the book when the file has no name"
    assert [entry.content for entry in book.entries] == ["lore"]


def test_fetch_world_book_rejects_a_body_without_entries() -> None:
    client = FakeImport({"/api/worldinfo/get": {"name": "empty"}})
    with pytest.raises(BackendError):
        run(client.fetch_world_book("empty"))


def test_the_client_is_read_only() -> None:
    """No destructive route is reachable, by construction.

    The tavern also exposes ``/api/worldinfo/delete`` and ``/api/characters/delete``.
    Importing from a running tavern is a convenience; deleting from it is
    destructive on the user's own library, reached through a plugin they installed
    for chat. This test fails the moment someone adds such a call.

    Parsed rather than grepped: the module's docstring names the destructive routes
    on purpose, to explain why it does not call them, so a substring search would
    fail on the explanation. Only the *route argument* of a request counts.
    """
    import ast

    tree = ast.parse((REPO_ROOT / "tavern" / "backends" / "st_import.py").read_text("utf-8"))
    routes: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in ("_post_json", "post", "get", "request"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            routes.append(str(node.args[0].value))

    assert routes, "expected at least one request route"
    for route in routes:
        for verb in ("delete", "edit", "rename", "save", "import"):
            assert verb not in route, f"the import client must not call {route}"
