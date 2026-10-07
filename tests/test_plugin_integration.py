"""Integration tests for the plugin entry point with a stubbed AstrBot.

AstrBot is not required: the modules the plugin imports are injected into
``sys.modules`` before ``main`` is loaded. This exercises the real decorator
path (``filter.command_group`` and its ``.command()`` sub-registration) and the
message handler end to end, without starting a bot.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

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

        def command_group(self, name: str, alias: set | None = None) -> _Handler:
            return _Handler("group", name, alias)

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


class FakeMessage:
    def __init__(self, text: str, self_id: str = "10000", at: bool = False) -> None:
        self.message = [At(self_id)] if at else []
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
    ) -> None:
        self.message_str = text
        self.unified_msg_origin = umo
        self.message_obj = FakeMessage(text, at=at)
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
        text = "".join(getattr(part, "text", "") for part in chain.chain)
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
    group = getattr(tavern.TavernPlugin, "tavern_group")
    assert group.handler.kind == "group"
    assert group.handler.name == "tavern"
    assert group.handler.alias == {"酒馆"}
    subcommands = {child.name for child in group.handler.sub}
    assert {
        "help",
        "status",
        "list",
        "use",
        "card",
        "new",
        "history",
        "worldbook",
        "reload",
        "preview",
        "import",
    } <= subcommands


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
    sent = _run(_collect(plugin.on_message(event)))

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

    sent = _run(_collect(plugin.on_message(FakeEvent("你好", at=True))))
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

    sent = _run(_collect(plugin.on_message(FakeEvent("你好", at=True))))
    assert any("生成失败" in item.text for item in sent)


def test_render_splits_long_answer(tavern, tmp_path: Path) -> None:
    plugin = _make_plugin(tavern, tmp_path, {"render": {"max_chars_per_message": 10}})
    _seed(plugin)
    long_answer = "一二三四五六七八九十" * 3
    plugin._backend = FakeBackend([long_answer])
    # the card greeting would be split as well; assert on the model answer only
    plugin.core.binding("aiocqhttp:GroupMessage:123").greeting_sent = True

    sent = _run(_collect(plugin.on_message(FakeEvent("你好", at=True))))
    answers = [item.text.replace("\u200b", "") for item in sent]
    assert len(answers) >= 3
    assert all(len(answer) <= 10 for answer in answers)
    assert "".join(answers) == long_answer


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)
