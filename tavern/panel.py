"""Web API for the plugin's management page (``pages/panel/index.html``).

How this is wired, verified against the installed AstrBot 4.28.2
---------------------------------------------------------------
* ``Context.register_web_api(route, view_handler, methods, desc)``
  (``astrbot/core/star/context.py``). ``registered_web_apis`` is a **class**
  attribute, so it is process-global and never cleaned up: a route change needs a
  plugin reload, which is why routes are registered once in ``initialize``.
* The route is matched against ``/<metadata.name>/<subpath>`` by
  ``dashboard/api/plugins.py:_match_registered_web_api`` using
  ``re.fullmatch``. A named group such as ``(?P<name>[^/]+)`` arrives as the
  handler's **keyword argument**, because the caller does
  ``view_handler(**path_values)``.
* A handler returning a ``dict`` or ``list`` becomes a ``JSONResponse``
  (``dashboard/asgi_runtime.py:_coerce_view_result``), so plain returns are all
  this module needs -- no response helpers, no framework imports.

The page talks to these routes through the postMessage bridge, never ``fetch``: the
page iframe is sandboxed **without** ``allow-same-origin``, so it has no cookies
and a same-origin ``fetch`` would arrive unauthenticated. That is also why nothing
here reads a session cookie: the actor is identified by the bridge's own
authentication (``PluginRequest.username``), not by anything the page sends.

Secrets are never returned: ``backend.st_cookie`` is the one field that must not
leave the server, so the config view reports only whether it is *set*.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from tavern.backends.base import BackendError
from tavern.core import TavernError

#: Filled in by the plugin: the last summary produced for each session scope, so
#: the page can show what a turn actually injected without re-running a scan.
LAST_TURNS: dict[str, dict[str, Any]] = {}

#: How many characters of a card field the library list returns. The page shows a
#: preview; the full text is what the model gets, and sending it to the browser
#: buys nothing.
PREVIEW_CHARS = 400


def _core(instance: Any) -> Any:
    core = getattr(instance, "core", None)
    if core is None:
        raise TavernError("插件核心还没初始化。")
    return core


def _summarise_card(card: Any) -> dict[str, Any]:
    return {
        "id": card.name,
        "name": card.name,
        "description": (card.description or "")[:PREVIEW_CHARS],
        "first_mes": (card.first_mes or "")[:PREVIEW_CHARS],
        "has_world_book": bool(getattr(card, "character_book", None)),
        "spec": getattr(card, "spec", "") or "",
        "tags": list(getattr(card, "tags", []) or []),
    }


def _summarise_book(book: Any) -> dict[str, Any]:
    entries = list(getattr(book, "entries", []) or [])
    keys: list[str] = []
    for entry in entries:
        keys.extend(getattr(entry, "keys", []) or [])
    return {
        "id": book.name,
        "name": book.name,
        "entries": len(entries),
        "keys": sorted({key for key in keys if key})[:40],
        "constant": sum(1 for entry in entries if getattr(entry, "constant", False)),
        "timed": sum(
            1
            for entry in entries
            if any(getattr(entry, field, 0) for field in ("sticky", "cooldown", "delay"))
        ),
    }


def build_state(instance: Any) -> dict[str, Any]:
    """Everything the page's landing view needs, in one round trip."""
    core = _core(instance)
    config = instance.config
    bindings = getattr(instance, "_bindings_snapshot", None)
    if callable(bindings):
        scopes = bindings()
    else:
        scopes = list(LAST_TURNS)
    return {
        "plugin": {
            "name": "astrbot_plugin_tavern",
            "version": _plugin_version(),
        },
        "library": {
            "cards": core.card_ids(),
            "worldbooks": core.book_ids(),
            "presets": getattr(core, "preset_ids", lambda: [])()
            if hasattr(core, "preset_ids")
            else [],
        },
        "backend": {
            "type": config.backend.type,
            "provider_id": config.backend.provider_id,
            "st_base_url": config.backend.st_base_url,
            # Never the cookie itself -- only whether one is configured.
            "st_cookie_set": bool(config.backend.st_cookie),
            "st_verify_ssl": config.backend.st_verify_ssl,
            "max_context_tokens": config.backend.max_context_tokens,
            "reply_reserve_tokens": config.backend.reply_reserve_tokens,
            "fallback_to_astrbot": config.backend.fallback_to_astrbot,
            "fallback_notice": config.backend.fallback_notice,
        },
        "sessions": [
            {"scope": scope, **LAST_TURNS.get(scope, {})} for scope in sorted(set(scopes))
        ],
        "config": {
            "worldbook": {
                "scan_depth": config.worldbook.scan_depth,
                "token_budget": config.worldbook.token_budget,
                "match_whole_words": config.worldbook.match_whole_words,
                "allow_recursion": config.worldbook.allow_recursion,
            },
            "trigger": {
                "private_always": config.trigger.private_always,
                "group_at_only": config.trigger.group_at_only,
                "wake_prefixes": list(config.trigger.wake_prefixes),
            },
        },
    }


def build_library(instance: Any) -> dict[str, Any]:
    """Cards, books and presets with enough detail to browse, not to chat."""
    core = _core(instance)
    cards = []
    for card_id in core.card_ids():
        try:
            cards.append(_summarise_card(core.get_card(card_id)))
        except TavernError as exc:  # a card that vanished between scan and read
            cards.append({"id": card_id, "name": card_id, "error": str(exc)})
    books = []
    for book_id in core.book_ids():
        try:
            books.append(_summarise_book(core.get_book(book_id)))
        except TavernError as exc:
            books.append({"id": book_id, "name": book_id, "error": str(exc)})
    return {"cards": cards, "worldbooks": books}


def build_card_detail(instance: Any, card_id: str) -> dict[str, Any]:
    core = _core(instance)
    card = core.get_card(card_id)
    return {
        "id": card.name,
        "name": card.name,
        "description": card.description or "",
        "personality": card.personality or "",
        "scenario": card.scenario or "",
        "first_mes": card.first_mes or "",
        "mes_example": card.mes_example or "",
        "system_prompt": card.system_prompt or "",
        "post_history_instructions": card.post_history_instructions or "",
        "creator_notes": card.creator_notes or "",
        "tags": list(getattr(card, "tags", []) or []),
    }


def build_book_detail(instance: Any, book_id: str) -> dict[str, Any]:
    core = _core(instance)
    book = core.get_book(book_id)
    entries = []
    for entry in list(getattr(book, "entries", []) or []):
        entries.append(
            {
                "uid": getattr(entry, "uid", 0),
                "keys": list(getattr(entry, "keys", []) or []),
                "content": (getattr(entry, "content", "") or "")[:PREVIEW_CHARS],
                "constant": bool(getattr(entry, "constant", False)),
                "disable": bool(getattr(entry, "disable", False)),
                "sticky": getattr(entry, "sticky", 0) or 0,
                "cooldown": getattr(entry, "cooldown", 0) or 0,
                "delay": getattr(entry, "delay", 0) or 0,
                "order": getattr(entry, "insertion_order", 100),
            }
        )
    return {"id": book.name, "name": book.name, "entries": entries}


def _plugin_version() -> str:
    """The version the plugin reports, read from ``metadata.yaml`` when present.

    ``tavern/panel.py`` -> ``tavern/`` -> the plugin root, where ``metadata.yaml``
    sits. Missing is not an error: the page then shows no version.
    """
    candidate = Path(__file__).resolve().parent.parent / "metadata.yaml"
    if not candidate.is_file():
        return ""
    for line in candidate.read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            return line.partition(":")[2].strip().strip("'\"")
    return ""


# ---------------------------------------------------------------------------
# handlers -- return plain dicts; AstrBot wraps them in JSONResponse
# ---------------------------------------------------------------------------


def _respond(work: Any) -> dict[str, Any]:
    """Run ``work(instance)`` and always return a JSON-shaped dict.

    Every handler goes through this because the failure modes all have to arrive as
    JSON: the host turns a dict into a ``JSONResponse``, but an escaping exception
    becomes an HTML 500 that the bridge reports as a bare string, and "no plugin
    instance yet" is a normal state during startup rather than a bug. Catching here
    rather than in six places also means a new handler cannot forget to.
    """
    try:
        instance = _instance()
    except TavernError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        return {"ok": True, "data": work(instance)}
    except TavernError as exc:
        return {"ok": False, "error": str(exc)}


async def state(request: Any = None) -> dict[str, Any]:
    """Landing view: library counts, backend config, per-session summary."""
    return _respond(build_state)


async def library(request: Any = None) -> dict[str, Any]:
    """Browse every installed card and world book."""
    return _respond(build_library)


async def card_detail(name: str = "", request: Any = None) -> dict[str, Any]:
    """One card's full text. ``name`` is a named route group."""
    if not name:
        return {"ok": False, "error": "缺少角色卡名称。"}
    return _respond(lambda instance: build_card_detail(instance, name))


async def book_detail(name: str = "", request: Any = None) -> dict[str, Any]:
    """One world book's entries. ``name`` is a named route group."""
    if not name:
        return {"ok": False, "error": "缺少世界书名称。"}
    return _respond(lambda instance: build_book_detail(instance, name))


async def st_characters(request: Any = None) -> dict[str, Any]:
    """List the characters on the configured external tavern (read-only)."""
    try:
        instance = _instance()
    except TavernError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        return {"ok": True, "data": await _core(instance).st_list_characters()}
    except (TavernError, BackendError) as exc:
        return {"ok": False, "error": str(exc)}


async def st_worldbooks(request: Any = None) -> dict[str, Any]:
    """List the world books on the configured external tavern (read-only)."""
    try:
        instance = _instance()
    except TavernError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        return {"ok": True, "data": await _core(instance).st_list_world_books()}
    except (TavernError, BackendError) as exc:
        return {"ok": False, "error": str(exc)}


async def st_chats(request: Any = None) -> dict[str, Any]:
    """List one tavern character's chats. ``avatar`` comes from the query string."""
    avatar = _query(request, "avatar")
    if not avatar:
        return {"ok": False, "error": "缺少 avatar 参数。"}
    try:
        instance = _instance()
    except TavernError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        return {"ok": True, "data": await _core(instance).st_list_chats(avatar)}
    except (TavernError, BackendError) as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def _instance() -> Any:
    """The live plugin instance.

    AstrBot registers a plain function, not a bound method, so the handler needs
    the instance the same way the command handlers already do -- through the
    module-level list ``tavern.main`` keeps.
    """
    from tavern import main as main_module

    instances = getattr(main_module, "PLUGIN_INSTANCES", None) or []
    if not instances:
        raise TavernError("插件实例还没就绪，请稍后重试。")
    return instances[-1]


def _query(request: Any, key: str, default: str = "") -> str:
    """Read one query parameter, tolerating both a request and a bare mapping."""
    if request is None:
        return default
    query = getattr(request, "query", None) or getattr(request, "query_params", None)
    if query is None:
        return default
    try:
        value = query.get(key, default)
    except TypeError:  # a multivalue dict wants the kwarg form
        value = query.get(key, default)
    return str(value or default)


#: ``(subpath, handler, methods, description)``. The plugin prefixes every route
#: with its ``metadata.name`` when registering, so the subpaths here are relative.
#:
#: Path parameters use AstrBot's own **angle-bracket** syntax, not a Python regex:
#: ``dashboard/api/plugins.py:_plugin_api_route_pattern`` rewrites ``<name>`` to
#: ``(?P<name>[^/]+)`` and ``<path:name>`` to ``(?P<name>.*)`` itself, and leaves
#: everything else escaped. Writing ``(?P<name>[^/]+)`` here registers fine and then
#: never matches -- the route looks correct in the log and answers "未找到该路由",
#: which is exactly how this was found.
ROUTES: list[tuple[str, Any, list[str], str]] = [
    ("panel/state", state, ["GET"], "酒馆插件状态"),
    ("panel/library", library, ["GET"], "本地角色卡与世界书列表"),
    ("panel/card/<name>", card_detail, ["GET"], "角色卡详情"),
    ("panel/book/<name>", book_detail, ["GET"], "世界书详情"),
    ("panel/st/characters", st_characters, ["GET"], "外部酒馆角色卡列表"),
    ("panel/st/worldbooks", st_worldbooks, ["GET"], "外部酒馆世界书列表"),
    ("panel/st/chats", st_chats, ["GET"], "外部酒馆聊天列表"),
]


def register(context: Any, plugin_name: str) -> int:
    """Register every route, returning how many were registered.

    Guarded on purpose: ``tests/test_plugin_integration.py`` constructs the plugin
    with ``context=None``, and a bare ``context.register_web_api`` would make that
    test fail with ``AttributeError`` instead of testing the plugin. A host without
    the API simply gets no page backend, which is correct for an older AstrBot.
    """
    register_api = getattr(context, "register_web_api", None)
    if register_api is None:
        return 0
    if not _accepts_four_arguments(register_api):
        return 0
    count = 0
    for subpath, handler, methods, desc in ROUTES:
        route = f"/{plugin_name}/{subpath}"
        try:
            register_api(route, handler, methods, desc)
        except TypeError:  # a host with a different signature: skip, do not crash
            continue
        count += 1
    return count


def _accepts_four_arguments(func: Any) -> bool:
    """True when ``func`` can be called as ``func(route, handler, methods, desc)``."""
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):  # pragma: no cover - builtins
        return True
    params = [
        param
        for param in signature.parameters.values()
        if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD)
    ]
    if any(param.kind is param.VAR_POSITIONAL for param in signature.parameters.values()):
        return True
    return len(params) >= 4
