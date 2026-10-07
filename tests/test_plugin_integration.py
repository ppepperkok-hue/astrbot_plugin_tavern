"""Integration tests for the plugin entry point with a stubbed AstrBot.

AstrBot is not required: the modules the plugin imports are injected into
``sys.modules`` before ``main`` is loaded. This exercises the real decorator
path (``filter.command_group`` and its ``.command()`` sub-registration) and the
message handler end to end, without starting a bot.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

#: Imported at module level, not inside a function: several tests raise it from a
#: nested class body, where a function-local import is not in scope.
from tavern.backends.base import BackendError  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ----------------------------------------------------------------------
# AstrBot stub
# ----------------------------------------------------------------------
class _Handler:
    """Mimics ``astrbot.core.star.register`` command/command_group decorators."""

    def __init__(self, kind: str, name: str, alias: set | None = None) -> None:
        self.kind = kind
        self.name = name
        self.alias = alias or set()
        self.sub: list[Any] = []
        self.handler = None

    def __call__(self, fn: Any) -> Any:
        if self.kind == "group":

            def wrapper(*args: Any, **kwargs: Any) -> None:
                return None

            wrapper.__name__ = getattr(fn, "__name__", "group")
            wrapper.handler = self  # type: ignore[attr-defined]
            self.handler = wrapper
            return wrapper
        fn.handler = self  # type: ignore[attr-defined]
        self.handler = fn
        return fn

    def command(self, name: str, alias: set | None = None) -> _Handler:
        child = _Handler("command", name, alias)
        self.sub.append(child)
        return child


class _RegisteringCommandable:
    """Mirrors ``astrbot.core.star.register.RegisteringCommandable``."""

    def __init__(self, group: _Handler) -> None:
        self.group = group
        self.registered: list[tuple[str, set[str]]] = []

    def command(self, name: str, alias: set | None = None) -> Any:
        self.registered.append((name, alias or set()))

        def decorator(fn: Any) -> Any:
            fn.handler = _Handler("command", name, alias)  # type: ignore[attr-defined]
            return fn

        return decorator


def _build_stub() -> dict[str, Any]:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event_mod = types.ModuleType("astrbot.api.event")
    star_mod = types.ModuleType("astrbot.api.star")
    components = types.ModuleType("astrbot.api.message_components")
    core = types.ModuleType("astrbot.core")
    platform = types.ModuleType("astrbot.core.platform")
    sources = types.ModuleType("astrbot.core.platform.sources")
    aiocqhttp = types.ModuleType("astrbot.core.platform.sources.aiocqhttp")
    aiocqhttp_event = types.ModuleType(
        "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event"
    )

    class Logger:
        def __init__(self) -> None:
            self.records: list[tuple[str, str]] = []

        def _record(self, level: str, message: str) -> None:
            self.records.append((level, message))

        def info(self, message: str) -> None:
            self._record("info", message)

        def warning(self, message: str) -> None:
            self._record("warning", message)

        def error(self, message: str) -> None:
            self._record("error", message)

    class AstrBotConfig(dict):
        pass

    class Context:
        pass

    class Star:
        def __init__(self, context: Any = None) -> None:
            self.context = context

    class EventMessageType:
        ALL = "all"
        PRIVATE_MESSAGE = "private"
        GROUP_MESSAGE = "group"

    _EVENT_MESSAGE_TYPE = EventMessageType

    class _Filter:
        EventMessageType = _EVENT_MESSAGE_TYPE

        def command(self, name: str, alias: set | None = None) -> _Handler:
            return _Handler("command", name, alias)

        def command_group(self, name: str, alias: set | None = None):
            handler = _Handler("group", name, alias)

            def decorator(_fn: Any) -> _RegisteringCommandable:
                return _RegisteringCommandable(handler)

            return decorator

        def event_message_type(self, _kind: Any):  # pragma: no cover - decorator only
            return lambda fn: fn

    class Plain:
        def __init__(self, text: str = "") -> None:
            self.text = text

        def __repr__(self) -> str:  # pragma: no cover - debug helper
            return f"Plain({self.text!r})"

    class Chain:
        def __init__(self) -> None:
            self.chain: list[Any] = []

    class MessageChain(Chain):
        pass

    def register(*args: Any, **kwargs: Any):
        def decorator(cls: Any) -> Any:
            cls.registered_as = args[0]
            return cls

        return decorator

    class AstrMessageEvent:
        pass

    api.logger = Logger()
    api.AstrBotConfig = AstrBotConfig
    event_mod.filter = _Filter()
    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageChain = MessageChain
    star_mod.Context = Context
    star_mod.Star = Star
    star_mod.register = register

    class StarTools:
        @staticmethod
        def get_data_dir(plugin_name: str | None = None) -> Path:
            base = Path(REPO_ROOT) / ".tmp-test-data" / (plugin_name or "unknown")
            base.mkdir(parents=True, exist_ok=True)
            return base

    star_mod.StarTools = StarTools
    components.Plain = Plain
    components.At = type("At", (), {})
    components.Image = type("Image", (), {})
    aiocqhttp_event.AiocqhttpMessageEvent = type("AiocqhttpMessageEvent", (), {})

    return {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event_mod,
        "astrbot.api.star": star_mod,
        "astrbot.api.message_components": components,
        "astrbot.core": core,
        "astrbot.core.platform": platform,
        "astrbot.core.platform.sources": sources,
        "astrbot.core.platform.sources.aiocqhttp": aiocqhttp,
        "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event": aiocqhttp_event,
    }


@pytest.fixture(scope="module")
def tavern():
    """Import the plugin with a stubbed AstrBot and return the module."""
    stub = _build_stub()
    saved = {name: sys.modules.get(name) for name in stub}
    sys.modules.update(stub)
    for name in list(sys.modules):
        if name == "tavern.main":
            del sys.modules[name]
    try:
        import tavern.main as module
    finally:
        pass
    yield module
    for name, previous in saved.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    sys.modules.pop("tavern.main", None)


# ----------------------------------------------------------------------
# fake platform objects
# ----------------------------------------------------------------------
class At:
    def __init__(self, qq: str) -> None:
        self.qq = qq


class FakeFile:
    """Stand-in for ``astrbot``'s ``File`` message segment."""

    def __init__(self, name: str, payload: bytes) -> None:
        self.name = name
        self.file_ = ""
        self.url = ""
        self.payload = payload

    async def get_file(self) -> bytes:
        return self.payload


class FakeMessage:
    def __init__(
        self,
        text: str,
        self_id: str = "10000",
        at: bool = False,
        files: list[FakeFile] | None = None,
    ) -> None:
        self.message = [At(self_id)] if at else []
        self.message.extend(files or [])
        self.message_str = text
        self.self_id = self_id


class FakeResult:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeEvent:
    def __init__(
        self,
        text: str,
        *,
        umo: str = "aiocqhttp:GroupMessage:123",
        private: bool = False,
        at: bool = False,
        admin: bool = False,
        sender: str = "小明",
        files: list[FakeFile] | None = None,
    ) -> None:
        self.message_str = text
        self.unified_msg_origin = umo
        self.message_obj = FakeMessage(text, at=at, files=files)
        self._private = private
        self._admin = admin
        self._sender = sender
        self.stopped = False
        self.sent: list[FakeResult] = []

    # API surface used by the plugin
    def is_private_chat(self) -> bool:
        return self._private

    def get_group_id(self) -> str:
        return "" if self._private else "123"

    def get_sender_name(self) -> str:
        return self._sender

    def get_sender_role(self) -> str:
        return "admin" if self._admin else "member"

    def get_self_id(self) -> str:
        return self.message_obj.self_id

    def chain_result(self, chain: Any) -> FakeResult:
        """``chain_result`` receives a list of components in real AstrBot."""
        text = "".join(getattr(part, "text", "") for part in chain)
        result = FakeResult(text)
        self.sent.append(result)
        return result

    def plain_result(self, text: str) -> FakeResult:
        return self.chain_result(type("C", (), {"chain": [type("P", (), {"text": text})]})())

    def stop_event(self) -> None:
        self.stopped = True


class FakeBackend:
    name = "fake"

    def __init__(self, answers: list[str] | None = None) -> None:
        self.answers = answers or ["回答一"]
        self.requests: list[Any] = []

    async def generate(self, request: Any) -> Any:
        from tavern.backends.base import GenerationResult

        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.answers) - 1)
        return GenerationResult(text=self.answers[index], model="fake")

    async def close(self) -> None:
        return None


# ----------------------------------------------------------------------
# tests
# ----------------------------------------------------------------------
def test_module_registers_and_builds_command_tree(tavern) -> None:
    """The plugin class exposes every handler the group registered."""
    handlers = sorted(
        name[4:]
        for name in dir(tavern.TavernPlugin)
        if name.startswith("cmd_") and callable(getattr(tavern.TavernPlugin, name))
    )
    assert handlers == [
        "card_detail",
        "help",
        "history",
        "import",
        "list_cards",
        "new_chat",
        "preview",
        "reload",
        "st",
        "status",
        "use_card",
        "worldbook",
    ]
    assert tavern._REGISTERED_SUBCOMMANDS is True

    # the message handler is decorated as well
    assert hasattr(tavern.TavernPlugin.on_message, "__wrapped__") or callable(
        tavern.TavernPlugin.on_message
    )


def _make_plugin(tavern, tmp_path: Path, config: dict[str, Any] | None = None):
    raw = {
        "enabled": True,
        "trigger": {"cooldown_seconds": 0},
        **(config or {}),
    }
    config_obj = tavern.AstrBotConfig(raw)
    plugin = tavern.TavernPlugin(context=None, config=config_obj)
    # point the data dir at the test directory and reload the library
    plugin.config.data_dir = tmp_path
    plugin.config.ensure_dirs()
    plugin.core.config.data_dir = tmp_path
    plugin.core._store = None
    plugin.core.load()
    return plugin


def _seed(plugin) -> None:
    plugin.core.import_card_bytes("iris.json", (FIXTURES / "cards" / "iris.json").read_bytes())
    plugin.core.import_worldbook_bytes(
        "lighthouse.json", (FIXTURES / "worldbooks" / "lighthouse.json").read_bytes()
    )
    plugin.core.bind_card("aiocqhttp:GroupMessage:123", "Iris")
    plugin.core.toggle_book("aiocqhttp:GroupMessage:123", "Lighthouse Lore", True)


async def _collect(async_gen):
    results = []
    async for item in async_gen:
        results.append(item)
    return results


def test_trigger_rules(tavern, tmp_path: Path) -> None:
    plugin = _make_plugin(tavern, tmp_path)
    group_at = FakeEvent("你好", at=True)
    group_plain = FakeEvent("你好", at=False)
    group_wake = FakeEvent("酒馆 你好", at=False)
    private = FakeEvent("你好", private=True, umo="aiocqhttp:PrivateMessage:1")

    assert plugin.is_triggered(group_at) is True
    assert plugin.is_triggered(group_plain) is False
    assert plugin.is_triggered(group_wake) is True
    assert plugin.is_triggered(private) is True
    assert plugin.strip_wake_prefix("酒馆 你好") == "你好"


def test_disabled_plugin_never_triggers(tavern, tmp_path: Path) -> None:
    plugin = _make_plugin(tavern, tmp_path, {"enabled": False})
    assert plugin.is_triggered(FakeEvent("你好", at=True)) is False


def test_message_flow_sends_greeting_then_answer(tavern, tmp_path: Path) -> None:
    plugin = _make_plugin(tavern, tmp_path)
    _seed(plugin)
    plugin._backend = FakeBackend(["第一段\n\n第二段"])

    event = FakeEvent("你好", at=True)
    results = plugin.core  # keep the linter honest about unused imports
    assert results is not None
    sent = _run(_collect(plugin.handle_message(event)))

    texts = [item.text for item in sent]
    assert any("The lamp sweeps" in text for text in texts)  # greeting
    assert any("第一段" in text for text in texts)  # model answer

    # history holds greeting + user + assistant
    preview = plugin.core.history_preview(event.unified_msg_origin, limit=10)
    assert any("小明" in line for line in preview)
    assert event.stopped is True
    assert plugin._inflight[event.unified_msg_origin] == 0


def test_message_flow_skips_when_cooldown_active(tavern, tmp_path: Path) -> None:
    plugin = _make_plugin(tavern, tmp_path, {"trigger": {"cooldown_seconds": 30}})
    _seed(plugin)
    plugin._backend = FakeBackend()
    plugin._last_reply["aiocqhttp:GroupMessage:123"] = __import__("time").monotonic()

    sent = _run(_collect(plugin.handle_message(FakeEvent("你好", at=True))))
    assert sent == []


def test_backend_error_is_reported(tavern, tmp_path: Path) -> None:
    from tavern.backends.base import BackendError

    class Boom:
        name = "boom"

        async def generate(self, request: Any):
            raise BackendError("模型炸了")

        async def close(self) -> None:
            return None

    plugin = _make_plugin(tavern, tmp_path)
    _seed(plugin)
    plugin._backend = Boom()

    sent = _run(_collect(plugin.handle_message(FakeEvent("你好", at=True))))
    assert any("生成失败" in item.text for item in sent)


def _fallback_plugin(tavern, tmp_path: Path, **backend_config: Any):
    """A plugin whose backend is a fallback over a failing tavern."""
    from tavern.backends.fallback import FallbackBackend

    class Boom:
        name = "tavern"

        async def generate(self, request: Any):
            raise BackendError("无法连接酒馆 http://127.0.0.1:8000: refused")

        async def close(self) -> None:
            return None

    class Works(FakeBackend):
        async def close(self) -> None:
            return None

    plugin = _make_plugin(tavern, tmp_path, {"backend": backend_config})
    _seed(plugin)
    plugin.core.binding("aiocqhttp:GroupMessage:123").greeting_sent = True
    plugin._backend = FallbackBackend(Boom(), Works(["来自 AstrBot 的回答"]))
    return plugin


def test_a_fallback_reply_says_so(tavern, tmp_path: Path) -> None:
    """The point of the whole feature: the swap must be visible in the reply."""
    plugin = _fallback_plugin(tavern, tmp_path, fallback_notice=True)

    sent = _run(_collect(plugin.handle_message(FakeEvent("你好", at=True))))
    text = "\n".join(item.text for item in sent)
    assert "来自 AstrBot 的回答" in text, "the fallback answer must be delivered"
    assert "外部酒馆不可用" in text, "the fallback must announce itself"
    assert "AstrBot" in text


def test_a_fallback_can_be_quiet_when_asked(tavern, tmp_path: Path) -> None:
    """Notices are configurable -- but turning them off is a deliberate act."""
    plugin = _fallback_plugin(tavern, tmp_path, fallback_notice=False)

    sent = _run(_collect(plugin.handle_message(FakeEvent("你好", at=True))))
    text = "\n".join(item.text for item in sent)
    assert "来自 AstrBot 的回答" in text
    assert "外部酒馆不可用" not in text


def _status_lines(plugin: Any, tavern: Any, text: str = "/tavern 状态") -> str:
    """The status text, for *this* plugin instance.

    `cmd_status` is a ``staticmethod`` whose first argument is the plugin, so calling
    it off :class:`TavernPlugin` with an explicit instance runs the real handler
    against the instance under test -- no copy of its logic here.
    """
    event = FakeEvent(text, at=True)
    results = list(_run(_collect(tavern.TavernPlugin.cmd_status(plugin, event))))
    return "\n".join(item.text if hasattr(item, "text") else str(item) for item in results)


def test_the_fallback_reason_is_remembered(tavern, tmp_path: Path) -> None:
    """The notice scrolls away after one message; the reason has to persist.

    `plugin._last_fallback` is what `/tavern status` reads, so it is asserted
    directly, and the status rendering is exercised on this instance.
    """
    plugin = _fallback_plugin(tavern, tmp_path, fallback_notice=True)

    _run(_collect(plugin.handle_message(FakeEvent("你好", at=True))))
    assert plugin._last_fallback, "the reason must be recorded, not only printed"
    assert "无法连接酒馆" in plugin._last_fallback

    assert "上次回退原因" in _status_lines(plugin, tavern)
    assert "无法连接酒馆" in _status_lines(plugin, tavern)


def test_a_fallback_free_run_records_no_reason(tavern, tmp_path: Path) -> None:
    plugin = _make_plugin(tavern, tmp_path)
    _seed(plugin)
    plugin.core.binding("aiocqhttp:GroupMessage:123").greeting_sent = True
    plugin._backend = FakeBackend(["正常回答"])

    _run(_collect(plugin.handle_message(FakeEvent("你好", at=True))))
    assert plugin._last_fallback == "", "a clean turn must not leave a stale reason"
    assert "上次回退原因" not in _status_lines(plugin, tavern)


def test_no_fallback_means_the_error_is_reported(tavern, tmp_path: Path) -> None:
    """With the fallback off, a tavern failure stays a failure -- not a silent swap."""
    plugin = _make_plugin(tavern, tmp_path, {"backend": {"fallback_to_astrbot": False}})
    _seed(plugin)
    plugin.core.binding("aiocqhttp:GroupMessage:123").greeting_sent = True

    class Boom:
        name = "tavern"

        async def generate(self, request: Any):
            raise BackendError("无法连接酒馆")

        async def close(self) -> None:
            return None

    plugin._backend = Boom()
    sent = _run(_collect(plugin.handle_message(FakeEvent("你好", at=True))))
    text = "\n".join(item.text for item in sent)
    assert "生成失败" in text
    assert "外部酒馆不可用" not in text, "nothing should claim a fallback that is off"


def test_the_config_gate_builds_no_wrapper_by_default(tavern, tmp_path: Path) -> None:
    """`backend()` must return the tavern bare unless the fallback is configured."""
    from tavern.backends.sillytavern import SillyTavernBackend

    plugin = _make_plugin(
        tavern,
        tmp_path,
        {"backend": {"type": "sillytavern", "st_base_url": "http://127.0.0.1:8000"}},
    )
    assert isinstance(plugin.backend(), SillyTavernBackend)

    wrapped = _make_plugin(
        tavern,
        tmp_path,
        {
            "backend": {
                "type": "sillytavern",
                "st_base_url": "http://127.0.0.1:8000",
                "fallback_to_astrbot": True,
            }
        },
    )
    built = wrapped.backend()
    assert not isinstance(built, SillyTavernBackend)
    assert isinstance(built.primary, SillyTavernBackend)


def test_the_newest_instance_wins_after_a_reload(tavern, tmp_path: Path) -> None:
    """A reload must not leave the old, closed instance answering.

    AstrBot constructs a fresh plugin on reload while this module stays imported, and
    the runtime handlers are staticmethods that resolve the instance through
    `PLUGIN_INSTANCES`. Reading `[0]` meant the *first* instance kept serving every
    message and every command for the life of the process -- and after `terminate`
    that instance is closed, so its backend is gone.
    """
    first = _make_plugin(tavern, tmp_path)
    assert tavern._current_plugin() is first

    second = _make_plugin(tavern, tmp_path)
    assert tavern._current_plugin() is second, "the newest instance must win"
    assert tavern.PLUGIN_INSTANCES == [second], "only the live instance is kept"

    _run(first.terminate())
    assert tavern.PLUGIN_INSTANCES == [second], "an unrelated terminate must not evict it"

    _run(second.terminate())
    assert tavern.PLUGIN_INSTANCES == [], "terminate drops its own entry"


def test_a_handler_never_reaches_a_closed_instance(tavern, tmp_path: Path) -> None:
    """The concrete symptom: `/tavern status` reading the wrong plugin's data dir."""
    first = _make_plugin(tavern, tmp_path)
    first.config.data_dir = tmp_path / "first"

    second = _make_plugin(tavern, tmp_path)
    second.config.data_dir = tmp_path / "second"

    _run(first.terminate())
    resolved = tavern._current_plugin()
    assert resolved is second
    assert resolved.config.data_dir == tmp_path / "second"


def test_render_splits_long_answer(tavern, tmp_path: Path) -> None:
    plugin = _make_plugin(tavern, tmp_path, {"render": {"max_chars_per_message": 10}})
    _seed(plugin)
    long_answer = "一二三四五六七八九十" * 3
    plugin._backend = FakeBackend([long_answer])
    # the card greeting would be split as well; assert on the model answer only
    plugin.core.binding("aiocqhttp:GroupMessage:123").greeting_sent = True

    sent = _run(_collect(plugin.handle_message(FakeEvent("你好", at=True))))
    answers = [item.text.replace("\u200b", "") for item in sent]
    assert len(answers) >= 3
    assert all(len(answer) <= 10 for answer in answers)
    assert "".join(answers) == long_answer


def test_plain_text_message_is_unchanged_by_the_file_path(tavern, tmp_path: Path) -> None:
    """A message without a file segment must never hit the import branch."""
    plugin = _make_plugin(tavern, tmp_path)
    _seed(plugin)
    event = FakeEvent("你好", at=True)
    assert tavern._incoming_files(event) == []
    assert tavern._file_name(event.message_obj.message[0]) == ""


def test_uploaded_card_is_imported_and_answered(tavern, tmp_path: Path) -> None:
    plugin = _make_plugin(tavern, tmp_path)
    plugin._backend = FakeBackend(["不该被调用"])
    payload = (FIXTURES / "cards" / "iris.json").read_bytes()
    event = FakeEvent(
        "",
        at=True,
        admin=True,
        files=[FakeFile("新卡.json", payload)],
    )

    sent = _run(_collect(plugin.handle_message(event)))
    texts = [item.text for item in sent]
    assert any("导入成功" in text for text in texts)
    assert any("Iris" in text for text in texts)
    # the import took over the message: no model call, no role play reply
    assert plugin._backend.requests == []
    assert event.stopped is True
    assert "Iris" in plugin.core.card_ids()


def test_uploaded_lorebook_is_detected_by_content(tavern, tmp_path: Path) -> None:
    """A ``.json`` upload may be a world book even though cards share the suffix."""
    plugin = _make_plugin(tavern, tmp_path)
    book = {
        "spec": "lorebook_v3",
        "data": {
            "name": "Harbour Notes",
            "scan_depth": 3,
            "entries": [
                {"id": 0, "keys": ["harbour"], "content": "Tar and gulls.", "enabled": True}
            ],
        },
    }
    event = FakeEvent(
        "",
        at=True,
        admin=True,
        files=[FakeFile("notes.json", json.dumps(book, ensure_ascii=False).encode("utf-8"))],
    )

    sent = _run(_collect(plugin.handle_message(event)))
    texts = [item.text for item in sent]
    assert any("Harbour Notes" in text for text in texts)
    assert "Harbour Notes" in plugin.core.book_ids()
    assert plugin.core._books["Harbour Notes"].scan_depth == 3


def test_uploaded_junk_file_reports_a_readable_error(tavern, tmp_path: Path) -> None:
    plugin = _make_plugin(tavern, tmp_path)
    event = FakeEvent(
        "",
        at=True,
        admin=True,
        files=[FakeFile("mystery.json", b'{"nothing": 1}')],
    )

    sent = _run(_collect(plugin.handle_message(event)))
    texts = [item.text for item in sent]
    assert any("导入失败" in text for text in texts)
    assert any("无法识别" in text for text in texts)
    assert event.stopped is True


def test_import_permission_blocks_non_admin(tavern, tmp_path: Path) -> None:
    plugin = _make_plugin(tavern, tmp_path, {"permissions": {"import_requires_admin": True}})
    event = FakeEvent(
        "",
        at=True,
        admin=False,
        files=[FakeFile("新卡.json", (FIXTURES / "cards" / "iris.json").read_bytes())],
    )

    sent = _run(_collect(plugin.handle_message(event)))
    texts = [item.text for item in sent]
    assert len(texts) == 1
    assert "只有管理员" in texts[0]
    assert plugin.core.card_ids() == []


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)
