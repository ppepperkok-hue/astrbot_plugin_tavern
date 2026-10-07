"""Read characters, world books and chats out of a running SillyTavern.

The other half of the external-tavern integration
----------------------------------------------
:mod:`tavern.backends.sillytavern` proxies *generation*: it hands an assembled
``messages[]`` to the tavern and gets a reply. It never touches the tavern's
library, because the generation endpoint does not read
``characters/``/``worlds/``/``chats/`` at all.

This module is the file half. It imports over the same routes the tavern's own
frontend uses, so a user who already has a library there can pull it into the
plugin instead of re-importing every file by hand:

===============================  ==================================================
route                            source
===============================  ==================================================
``POST /api/characters/all``     ``endpoints_characters.js:1466``
``POST /api/characters/get``     ``endpoints_characters.js:1480``
``POST /api/characters/chats``   ``endpoints_characters.js:1499``
``POST /api/worldinfo/list``     ``endpoints_worldinfo.js:39``
``POST /api/worldinfo/get``      ``endpoints_worldinfo.js:71``
``POST /api/chats/get``          ``script.js:7311``
===============================  ==================================================

**Read-only, deliberately.** The tavern also exposes ``/delete`` and ``/edit`` for
world books, ``/delete`` and ``/rename`` for characters. This module does not call
them, and should not: importing *from* a running tavern is a convenience, while
deleting from it is destructive on the user's own library, reached through a
plugin they installed for chat. Anything that writes belongs behind an explicit
confirmation the plugin does not have yet.

Beyond the routes, there is no transformation here: a fetched card goes through
:func:`tavern.st.cards.card_from_dict` and a fetched book through
:func:`tavern.st.importers.convert_bare_entries`, which is the same code path a
user-uploaded file takes. That is the point -- importing from the tavern must not
be a second, subtly different importer.
"""

from __future__ import annotations

import json

from tavern.backends.auth import SillyTavernSession
from tavern.backends.base import BackendError
from tavern.st import importers
from tavern.st.cards import CharacterCard, card_from_dict
from tavern.st.chat_store import ChatMessage, ChatMetadata, ChatSession
from tavern.st.worldbook import WorldBook

__all__ = ["SillyTavernImportClient"]


class SillyTavernImportClient(SillyTavernSession):
    """Read-only file access to one SillyTavern instance."""

    #: The tavern serves a character list of every ``.png`` in ``characters/``;
    #: ``/api/characters/chats`` and ``/api/chats/get`` key off that PNG name.
    CHARACTER_SUFFIX = ".png"

    # -- characters -------------------------------------------------------
    async def list_characters(self) -> list[dict[str, str]]:
        """``avatar`` + ``name`` for every card, sorted by name.

        ``/api/characters/all`` returns full character objects and can be large;
        only the two fields a caller needs to choose one are kept. The tavern's
        own list route returns them unsorted, so they are sorted here for a stable
        ``/tavern st characters`` listing.
        """
        data = await self._post_json("/api/characters/all", {})
        if not isinstance(data, list):
            raise BackendError("酒馆 /api/characters/all 返回的不是列表。")
        cards: list[dict[str, str]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            avatar = str(item.get("avatar") or "")
            name = str(item.get("name") or "")
            if avatar and name:
                cards.append({"avatar": avatar, "name": name})
        return sorted(cards, key=lambda entry: entry["name"].lower())

    async def fetch_character(self, avatar: str) -> CharacterCard:
        """One card, through the same importer a dropped file uses."""
        if not avatar:
            raise BackendError("需要提供角色卡的 avatar 文件名。")
        data = await self._post_json("/api/characters/get", {"avatar_url": avatar})
        if not isinstance(data, dict):
            raise BackendError(f"酒馆 /api/characters/get 对 {avatar!r} 返回的不是对象。")
        # `json_data` is the raw card as embedded in the PNG; prefer it when the
        # tavern provided it, because it is exactly what a file import would parse.
        raw = data.get("json_data")
        if isinstance(raw, str) and raw.strip():
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = data
        else:
            payload = data
        if not isinstance(payload, dict):
            raise BackendError(f"角色卡 {avatar!r} 的内容无法解析。")
        card = card_from_dict(payload, source_path=f"st:{avatar}")
        if not card.name:
            raise BackendError(f"角色卡 {avatar!r} 没有 name 字段，无法导入。")
        return card

    async def list_chats(self, avatar: str) -> list[dict[str, str]]:
        """``file_name`` / ``file_id`` for every chat of one character."""
        data = await self._post_json(
            "/api/characters/chats", {"avatar_url": avatar, "simple": True}
        )
        if isinstance(data, dict) and data.get("error"):
            return []
        if not isinstance(data, list):
            raise BackendError(f"酒馆 /api/characters/chats 对 {avatar!r} 返回的不是列表。")
        chats: list[dict[str, str]] = []
        for item in data:
            if isinstance(item, dict) and item.get("file_name"):
                chats.append(
                    {
                        "file_name": str(item["file_name"]),
                        "file_id": str(item.get("file_id") or item["file_name"]),
                    }
                )
        return chats

    async def fetch_chat(self, avatar: str, file_name: str, *, ch_name: str = "") -> ChatSession:
        """One chat, parsed by :meth:`ChatSession.from_jsonl`.

        ``file_name`` is the name **without** the ``.jsonl`` extension, which is
        what ``script.js:7311`` sends.
        """
        if not avatar or not file_name:
            raise BackendError("需要同时提供 avatar 文件名与聊天文件名。")
        stem = file_name[:-6] if file_name.endswith(".jsonl") else file_name
        data = await self._post_json(
            "/api/chats/get",
            {"ch_name": ch_name, "file_name": stem, "avatar_url": avatar},
        )
        if not isinstance(data, list):
            # The tavern answers `{error: true}` for a missing file on some
            # versions and an empty array on others.
            raise BackendError(f"酒馆没有找到聊天 {stem!r}（角色 {avatar!r}）。")
        # `/api/chats/get` returns the *parsed* chat: a list of message objects, not
        # JSONL text. Each one goes through `ChatMessage.from_st_dict`, the same
        # parser a file import uses, so a fetched chat and a read `.jsonl` cannot
        # disagree about what a message is. (An earlier version re-serialised the
        # rows to JSONL and re-parsed them, which quietly treated the *first
        # message* as the file header.)
        messages = [ChatMessage.from_st_dict(item) for item in data if isinstance(item, dict)]
        if data and not messages:
            raise BackendError(
                f"聊天 {stem!r} 返回了 {len(data)} 行但都不是消息对象，"
                "可能是酒馆版本的消息格式变了。"
            )
        return ChatSession(
            name=stem,
            character_name=ch_name or stem,
            user_name="",
            messages=messages,
            metadata=ChatMetadata(),
        )

    # -- world books ------------------------------------------------------
    async def list_world_books(self) -> list[dict[str, str]]:
        """``file_id`` + ``name`` for every world book, as the tavern lists them."""
        data = await self._post_json("/api/worldinfo/list", {})
        if not isinstance(data, list):
            raise BackendError("酒馆 /api/worldinfo/list 返回的不是列表。")
        books: list[dict[str, str]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            file_id = str(item.get("file_id") or "")
            if file_id:
                books.append({"file_id": file_id, "name": str(item.get("name") or file_id)})
        return sorted(books, key=lambda entry: entry["name"].lower())

    async def fetch_world_book(self, name: str) -> WorldBook:
        """One world book, through the same importer a dropped file uses."""
        if not name:
            raise BackendError("需要提供世界书名。")
        data = await self._post_json("/api/worldinfo/get", {"name": name})
        if not isinstance(data, dict) or "entries" not in data:
            raise BackendError(f"酒馆里没有名为 {name!r} 的世界书，或者它的内容不含 entries。")
        book = importers.convert_bare_entries(data)
        if not book.name:
            book.name = name
        return book
