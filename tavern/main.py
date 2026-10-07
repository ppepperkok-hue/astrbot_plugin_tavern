"""AstrBot entry point for the tavern plugin (SillyTavern style role play).

The module is deliberately thin: decorators, message components, permissions and
platform quirks live here, while :mod:`tavern.core` owns every
decision. The tavern pipeline takes over the conversation instead of piggybacking
on AstrBot's own history, which matches SillyTavern semantics (per branch chat
files, world info bookkeeping, swipes) and avoids fighting over one history:

1. ``@filter.event_message_type(ALL)`` receives the message.
2. trigger gating decides whether this chat is role playing (see config).
3. :class:`PluginCore` assembles card + world info + history into a request.
4. the configured backend (AstrBot provider or an external SillyTavern) replies.
5. the answer is cleaned, split and sent, then recorded as the chat's next turn.

The structured imports at the top are guarded so the module can also be imported
outside AstrBot (unit tests, linting); :func:`_require_astrbot` refuses to run the
plugin in that case.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

# AstrBot loads plugins as ``data.plugins.<plugin_dir>.main`` through implicit
# namespace packages, while the tests and the package itself use the canonical
# ``tavern.*`` names. Anchoring the plugin root on ``sys.path`` makes both views
# resolve to the *same* module objects, so the class registered here is the same
# one the tests exercise.
_PLUGIN_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

from tavern.backends.astrbot_provider import AstrBotProviderBackend  # noqa: E402
from tavern.backends.base import (  # noqa: E402
    BackendError,
    GenerationBackend,
    GenerationRequest,
)
from tavern.config import TavernConfig  # noqa: E402
from tavern.core import (  # noqa: E402
    PluginCore,
    TavernError,
    render_answer,
    request_preview,
    split_message,
)

PLUGIN_NAME = "astrbot_plugin_tavern"
PLUGIN_VERSION = "0.1.0"
PLUGIN_AUTHOR = "ppepperkok-hue"
PLUGIN_DESC = "酒馆风格角色扮演（角色卡 / 世界书 / 聊天记录）"

#: Guarded imports: present inside AstrBot, tolerated in a plain checkout.
try:  # pragma: no cover - exercised inside AstrBot
    import astrbot.api.message_components as Comp
    from astrbot.api import AstrBotConfig, logger
    from astrbot.api.event import AstrMessageEvent, MessageChain, filter
    from astrbot.api.star import Context, Star, register
except Exception:  # noqa: BLE001 - tests import this module without AstrBot
    AstrBotConfig = Any  # type: ignore[assignment,misc]
    logger = None  # type: ignore[assignment]
    Star = object  # type: ignore[assignment,misc]
    Context = Any  # type: ignore[assignment,misc]
    Comp = None  # type: ignore[assignment]
    filter = None  # type: ignore[assignment]
    MessageChain = Any  # type: ignore[assignment,misc]

    def register(*_args: Any, **_kwargs: Any):  # type: ignore[misc]
        """No-op stand-in for ``astrbot.api.star.register``."""

        def decorator(cls: Any) -> Any:
            return cls

        return decorator

    class AstrMessageEvent:  # type: ignore[no-redef]
        """Placeholder so type hints keep working outside AstrBot."""


def _log(level: str, message: str) -> None:
    """Log through AstrBot's logger when available, otherwise stay silent."""
    if logger is None:
        return
    getattr(logger, level, logger.info)(f"[tavern] {message}")


def _require_astrbot() -> bool:
    return filter is not None and Comp is not None


@register(PLUGIN_NAME, PLUGIN_AUTHOR, PLUGIN_DESC, PLUGIN_VERSION)
class TavernPlugin(Star):  # type: ignore[misc]
    """AstrBot plugin implementing the SillyTavern experience."""

    def __init__(self, context: Any, config: Any = None) -> None:
        super().__init__(context)
        self.context = context
        self.raw_config = config
        self.config = TavernConfig.from_raw(config)
        self.core = PluginCore(self.config)
        self.core.load()
        self._backend: GenerationBackend | None = None
        self._last_reply: dict[str, float] = {}
        self._inflight: dict[str, int] = {}
        # Staticmethod handlers need a way back to this instance.
        PLUGIN_INSTANCES.append(self)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def initialize(self) -> None:
        counts = self.core.reload_library()
        _log(
            "info",
            f"loaded {counts['cards']} cards, {counts['worldbooks']} world books, "
            f"{counts['presets']} presets from {self.config.data_dir}",
        )

    async def terminate(self) -> None:
        backend = self._backend
        self._backend = None
        if backend is not None:
            try:
                await backend.close()
            except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                _log("warning", f"closing backend failed: {exc}")

    # ------------------------------------------------------------------
    # backend
    # ------------------------------------------------------------------
    def backend(self) -> GenerationBackend:
        """Return the configured backend, creating it on first use."""
        if self._backend is not None:
            return self._backend

        if self.config.backend.uses_sillytavern:
            from tavern.backends.sillytavern import SillyTavernBackend

            self._backend = SillyTavernBackend(
                self.config.backend.st_base_url,
                cookie=self.config.backend.st_cookie,
                verify_ssl=self.config.backend.st_verify_ssl,
                timeout=self.config.backend.request_timeout,
            )
        else:
            self._backend = AstrBotProviderBackend(self.context, self.config.backend.provider_id)
        return self._backend

    async def generate(self, request: GenerationRequest) -> str:
        backend = self.backend()
        if isinstance(backend, AstrBotProviderBackend) and not request.extra.get("provider_id"):
            request.extra["provider_id"] = await backend.resolve_provider_id()
        result = await backend.generate(request)
        return result.text

    # ------------------------------------------------------------------
    # gating
    # ------------------------------------------------------------------
    def is_triggered(self, event: Any) -> bool:
        """Decide whether this message belongs to the role play."""
        if not self.config.enabled:
            return False
        text = (getattr(event, "message_str", "") or "").strip()
        if not text:
            return False
        if _is_private(event):
            return self.config.trigger.private_always
        if not self.config.trigger.group_at_only:
            return True
        if _is_at_bot(event):
            return True
        return any(text.startswith(prefix) for prefix in self.config.trigger.wake_prefixes)

    def strip_wake_prefix(self, text: str) -> str:
        stripped = text.strip()
        for prefix in self.config.trigger.wake_prefixes:
            if stripped.startswith(prefix):
                return stripped[len(prefix) :].strip()
        return stripped

    def _cooldown_active(self, scope: str) -> bool:
        cooldown = self.config.trigger.cooldown_seconds
        if cooldown <= 0:
            return False
        last = self._last_reply.get(scope, 0.0)
        return (time.monotonic() - last) < cooldown

    def _too_many_inflight(self, scope: str) -> bool:
        return self._inflight.get(scope, 0) >= self.config.trigger.max_concurrent

    # ------------------------------------------------------------------
    # main message handler
    # ------------------------------------------------------------------
    # AstrBot calls handlers as ``handler(event)``: it does *not* pass the plugin
    # instance. Handlers are therefore staticmethods that receive the plugin
    # through :data:`PLUGIN_INSTANCES` (the loaded instance, or a lazily built
    # one when AstrBot did not instantiate the class itself).
    @staticmethod
    async def on_message(event: Any):
        """Role play handler: takes over the conversation for enrolled chats."""
        plugin = _current_plugin()
        if plugin is None:
            return
        async for result in _handle_message(plugin, event):
            yield result
        event.stop_event()

    async def handle_message(self, event: Any):
        """Instance level entry point (also used directly by tests)."""
        async for result in _handle_message(self, event):
            yield result

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------
    # The subcommand handlers are plain module level functions registered
    # through the group (AstrBot's ``RegisteringCommandable.command`` chain);
    # they are attached to the class right after it is defined, which gives the
    # loader a single stable module for handler discovery.

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _result(self, event: Any, text: str):
        """Wrap text into a message result.

        ``chain_result`` expects a *list* of message components; handing it a
        ``MessageChain`` object breaks ``get_plain_text()`` at send time (the
        chain has to be iterated).
        """
        if Comp is None:
            return text
        return event.chain_result([Comp.Plain(text)])


#: Plugins instantiated by AstrBot. The runtime handlers are staticmethods, so
#: they need a way back to the instance that owns the library and bindings.
PLUGIN_INSTANCES: list[TavernPlugin] = []


def _current_plugin() -> TavernPlugin | None:
    """Return the live plugin instance, building one if AstrBot did not."""
    if PLUGIN_INSTANCES:
        return PLUGIN_INSTANCES[0]
    try:
        plugin = TavernPlugin(None)
    except Exception as exc:  # noqa: BLE001 - never let the handler explode
        _log("error", f"could not initialise the plugin automatically: {exc}")
        return None
    return plugin


def _file_name(component: Any) -> str:
    """Best effort file name of a message component (empty when it is not one)."""
    name = getattr(component, "name", None)
    if not isinstance(name, str) or not name:
        return ""
    if hasattr(component, "file_") or hasattr(component, "file"):
        return name
    return ""


def _incoming_files(event: Any) -> list[str]:
    """File names carried by ``event`` (empty when there is no file segment)."""
    message = getattr(event, "message_obj", None)
    components = getattr(message, "message", None) or []
    names: list[str] = []
    for component in components:
        name = _file_name(component)
        if name:
            names.append(name)
    return names


async def _fetch_file(component: Any) -> bytes:
    """Read a ``File`` segment as bytes, preferring the async accessor."""
    getter = getattr(component, "get_file", None)
    if callable(getter):
        try:
            blob = await getter()
        except TypeError:
            blob = getter()
        if isinstance(blob, (bytes, bytearray)):
            return bytes(blob)
    path = getattr(component, "file_", "") or ""
    if path and "://" not in str(path) and Path(str(path)).is_file():
        return Path(str(path)).read_bytes()
    raise TavernError(
        f"\u62ff\u4e0d\u5230\u6587\u4ef6\u300c{getattr(component, 'name', '')}\u300d\u7684\u5185\u5bb9\u3002"
    )


async def _handle_incoming_files(plugin: TavernPlugin, event: Any) -> list[str]:
    """Import every file attached to ``event`` and return the reply texts."""
    if plugin.config.permissions.import_requires_admin and not _is_admin(event):
        return ["\u53ea\u6709\u7ba1\u7406\u5458\u53ef\u4ee5\u5bfc\u5165\u6587\u4ef6\u3002"]
    message = getattr(event, "message_obj", None)
    components = getattr(message, "message", None) or []
    replies: list[str] = []
    for component in components:
        name = _file_name(component)
        if not name:
            continue
        try:
            payload = await _fetch_file(component)
        except TavernError as exc:
            replies.append(str(exc))
            continue
        try:
            summary = plugin.core.import_uploaded_file(name, payload)
        except TavernError as exc:
            replies.append(f"\u5bfc\u5165\u5931\u8d25\uff1a{exc}")
            continue
        replies.append(f"\u5bfc\u5165\u6210\u529fdesuwa\u3002{summary}")
    return replies


async def _handle_message(plugin: TavernPlugin, event: Any):
    """The actual message pipeline, independent of how it was invoked."""
    # Files are handled before the trigger rules: a card upload carries no text,
    # so ``is_triggered`` would drop it before the import could run.
    if _incoming_files(event):
        if not plugin.config.enabled:
            return
        event.stop_event()
        for text in await _handle_incoming_files(plugin, event):
            yield plugin._result(event, text)
        return

    if not plugin.is_triggered(event):
        return
    scope = event.unified_msg_origin
    if plugin._cooldown_active(scope) or plugin._too_many_inflight(scope):
        return
    if _is_at_bot(event) and not plugin.strip_wake_prefix(event.message_str):
        return

    plugin._inflight[scope] = plugin._inflight.get(scope, 0) + 1
    try:
        user_text = plugin.strip_wake_prefix(event.message_str)
        sender = _safe_call(event, "get_sender_name") or "用户"
        greeting = plugin.core.greeting(scope)
        if greeting:
            yield plugin._result(event, greeting)
            if not user_text:
                event.stop_event()
                return

        if user_text:
            plugin.core.record_user(scope, user_text, sender_name=sender)
            turn = plugin.core.build_turn(scope, user_text, sender_name=sender)
            if plugin.config.debug.log_prompt:
                _log("info", "prompt preview:\n" + request_preview(turn))
            try:
                answer = await plugin.generate(turn.request)
            except BackendError as exc:
                yield plugin._result(event, f"生成失败：{exc}")
                event.stop_event()
                return

            for chunk in render_answer(answer, config=plugin.config):
                yield plugin._result(event, chunk)
                if plugin.config.render.segment_delay_ms > 0:
                    await asyncio.sleep(plugin.config.render.segment_delay_ms / 1000)
            plugin.core.record_assistant(scope, answer)
            plugin._last_reply[scope] = time.monotonic()
    finally:
        plugin._inflight[scope] = max(0, plugin._inflight.get(scope, 1) - 1)
    event.stop_event()


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "..."


def _safe_call(event: Any, method: str, default: Any = None) -> Any:
    func = getattr(event, method, None)
    if callable(func):
        try:
            return func()
        except Exception:  # noqa: BLE001 - platform shims may raise
            return default
    return default


def _is_private(event: Any) -> bool:
    value = _safe_call(event, "is_private_chat")
    if value is None:
        group_id = _safe_call(event, "get_group_id")
        return not group_id
    return bool(value)


def _is_at_bot(event: Any) -> bool:
    """True when the message mentions this bot (not merely any user)."""
    message = getattr(event, "message_obj", None)
    components = getattr(message, "message", None) or []
    bot_id = _safe_call(event, "get_self_id") or getattr(message, "self_id", None)
    for component in components:
        if type(component).__name__ != "At":
            continue
        target = getattr(component, "qq", None) or getattr(component, "target", None)
        if bot_id is None or target is None:
            # Platform shim without an id: a mention is the best signal we have.
            return True
        if str(target) == str(bot_id):
            return True
    return False


def _is_admin(event: Any) -> bool:
    role = _safe_call(event, "get_sender_role") or ""
    if str(role).lower() in ("admin", "owner"):
        return True
    return bool(getattr(event, "is_admin", False))


HELP_TEXT = """酒馆角色扮演 · 指令

/tavern help                 显示本帮助
/tavern status               当前会话状态
/tavern list                 角色卡列表
/tavern use <名字>           切换角色卡并开启新分支
/tavern card                 查看当前角色卡详情
/tavern new                  开启新分支（重开）
/tavern history [条数]       查看最近聊天记录
/tavern worldbook list       世界书列表
/tavern worldbook on|off <名字>  启用/关闭世界书
/tavern worldbook effect <书> <uid> <sticky|cooldown|delay> [on|off]
                             查看或设置条目的计时效果（省略 on|off 则查询）
/tavern reload               重新扫描数据目录
/tavern import               查看导入目录说明
/tavern preview [文字]       预览本轮发给模型的完整内容

私聊直接说话即可；群聊需要 @机器人 或以唤醒词开头。
"""


# ----------------------------------------------------------------------
# subcommand handlers
# ----------------------------------------------------------------------
# AstrBot's command groups work through a "registering commandable" chain: the
# decorator returned by ``group.command(...)`` registers the function and hands
# it back. Building that chain at module level (instead of faking decorators
# inside the class body) keeps handler discovery working, because the loader
# resolves every handler through ``inspect.getmembers`` on the plugin class and
# matches it in ``star_handlers_registry`` by its module and function name.


def _register_commands(plugin_cls: Any) -> bool:
    """Attach the ``/tavern`` group and its subcommands to ``plugin_cls``.

    Returns ``False`` when AstrBot is not importable (tests, linting), in which
    case the command tree simply does not exist; the role play handler and the
    core logic keep working.
    """
    if not _require_astrbot():
        return False

    @filter.command_group("tavern", alias={"酒馆"})
    def group() -> None:
        """酒馆角色扮演指令组"""

    def sub(name: str, alias: str | None = None):
        def decorator(fn: Any) -> Any:
            # AstrBot calls ``fn(event)``; the plugin instance comes from the
            # registry, so ``fn`` keeps taking the plugin explicitly.
            async def shim(self_unused: Any, event: Any, *args: Any):
                plugin = _current_plugin()
                if plugin is None:
                    return
                async for result in fn(plugin, event, *args):
                    yield result

            shim.__name__ = fn.__name__
            shim.__doc__ = fn.__doc__
            shim.__module__ = fn.__module__
            shim.__qualname__ = fn.__qualname__
            # Register the shim (wrapper) and hand it back for class attachment.
            decorated = group.command(name, alias={alias}) if alias else group.command(name)
            return decorated(shim)

        return decorator

    @sub("help", "菜单")
    async def cmd_help(plugin: TavernPlugin, event: Any):
        yield plugin._result(event, HELP_TEXT)

    @sub("status", "状态")
    async def cmd_status(plugin: TavernPlugin, event: Any):
        binding = plugin.core.binding(event.unified_msg_origin)
        lines = [
            "酒馆状态",
            f"数据目录: {plugin.config.data_dir}",
            f"角色卡: {binding.card_id or '(未选择)'}",
            f"世界书: {', '.join(binding.worldbooks) or '(未启用)'}",
            f"分支: {binding.chat_name}",
            f"后端: {plugin.config.backend.type}",
            f"冷却: {plugin.config.trigger.cooldown_seconds}s",
            f"状态: {'启用' if plugin.config.enabled else '禁用'}",
        ]
        yield plugin._result(event, "\n".join(lines))

    @sub("list", "列表")
    async def cmd_list_cards(plugin: TavernPlugin, event: Any):
        cards = plugin.core.card_ids()
        if not cards:
            yield plugin._result(
                event,
                f"还没有角色卡。请把 .png/.json 卡片放进：\n{plugin.config.cards_dir}\n"
                "然后执行 /tavern reload。",
            )
            return
        binding = plugin.core.binding(event.unified_msg_origin)
        lines = ["角色卡列表（* 为当前）"]
        for name in cards:
            marker = "*" if name == binding.card_id else " "
            card = plugin.core.get_card(name)
            lines.append(f"{marker} {name} - {card.creator or '未知作者'}")
        lines.append("切换：/tavern use <名字>")
        yield plugin._result(event, "\n".join(lines))

    @sub("use", "换卡")
    async def cmd_use_card(plugin: TavernPlugin, event: Any, name: str = "", *args: str):
        if plugin.config.permissions.switch_card_requires_admin and not _is_admin(event):
            yield plugin._result(event, "只有管理员可以切换角色卡。")
            return
        target = " ".join([name, *args]).strip()
        if not target:
            yield plugin._result(event, "用法：/tavern use <角色卡名>")
            return
        try:
            binding = plugin.core.bind_card(event.unified_msg_origin, target)
        except TavernError as exc:
            yield plugin._result(event, str(exc))
            return
        yield plugin._result(event, f"已切换为「{binding.card_name}」，并开启新分支。")
        greeting = plugin.core.greeting(event.unified_msg_origin)
        if greeting:
            for chunk in split_message(greeting, plugin.config.render.max_chars_per_message):
                yield plugin._result(event, chunk)

    @sub("card")
    async def cmd_card_detail(plugin: TavernPlugin, event: Any):
        binding = plugin.core.binding(event.unified_msg_origin)
        if not binding.card_id:
            yield plugin._result(event, "当前会话还没有选择角色卡。")
            return
        card = plugin.core.get_card(binding.card_id)
        lines = [
            f"角色卡：{card.name}",
            f"版本：{card.character_version or '-'}  作者：{card.creator or '-'}",
            f"标签：{', '.join(card.tags) or '-'}",
            f"描述：{_clip(card.description, 300)}",
            f"开场白：{_clip(card.first_mes, 120)}",
        ]
        yield plugin._result(event, "\n".join(lines))

    @sub("new", "重开")
    async def cmd_new_chat(plugin: TavernPlugin, event: Any):
        binding = plugin.core.reset_chat(event.unified_msg_origin)
        yield plugin._result(event, f"已开启新分支「{binding.chat_name}」。")
        greeting = plugin.core.greeting(event.unified_msg_origin)
        if greeting:
            for chunk in split_message(greeting, plugin.config.render.max_chars_per_message):
                yield plugin._result(event, chunk)

    @sub("history", "历史")
    async def cmd_history(plugin: TavernPlugin, event: Any, limit: str = "6"):
        try:
            count = max(1, min(30, int(limit)))
        except ValueError:
            count = 6
        lines = plugin.core.history_preview(event.unified_msg_origin, limit=count)
        yield plugin._result(event, "\n".join(lines) if lines else "当前分支还没有记录。")

    @sub("worldbook", "世界书")
    async def cmd_worldbook(plugin: TavernPlugin, event: Any, action: str = "list", *args: str):
        binding = plugin.core.binding(event.unified_msg_origin)
        if action in ("list", "列表"):
            names = plugin.core.book_ids()
            if not names:
                yield plugin._result(
                    event,
                    f"还没有世界书。请把 .json 放进：\n{plugin.config.worldbooks_dir}\n"
                    "然后执行 /tavern reload。",
                )
                return
            lines = ["世界书列表（* 为已启用）"]
            for name in names:
                marker = "*" if name in binding.worldbooks else " "
                book = plugin.core.get_book(name)
                lines.append(f"{marker} {name} ({len(book.entries)} 条)")
            lines.append("开关：/tavern worldbook on|off <名字>")
            yield plugin._result(event, "\n".join(lines))
            return

        if action in ("on", "off"):
            target = " ".join(args).strip()
            if not target:
                yield plugin._result(event, f"用法：/tavern worldbook {action} <名字>")
                return
            try:
                binding, enabled = plugin.core.toggle_book(
                    event.unified_msg_origin, target, enabled=(action == "on")
                )
            except TavernError as exc:
                yield plugin._result(event, str(exc))
                return
            state = "开启" if enabled else "关闭"
            yield plugin._result(
                event, f"世界书已{state}，当前启用：{', '.join(binding.worldbooks) or '(无)'}"
            )
            return

        if action in ("effect", "效果"):
            # /tavern worldbook effect <书> <uid> <sticky|cooldown|delay> [on|off|toggle]
            # Ported from world-info.js:1449-1560 (`/wi-get-timed-effect` and
            # `/wi-set-timed-effect`), which the port map tracked as the last
            # engine-facing gap in S1.
            if len(args) < 3:
                yield plugin._result(
                    event,
                    "用法：/tavern worldbook effect <世界书> <uid> <sticky|cooldown|delay> [on|off]\n"
                    "省略 on/off 即为查询当前状态。\n"
                    "条目必须已经在书里配置过对应字段（sticky/cooldown/delay 大于 0），"
                    "否则无处可设——和酒馆的提示一致。",
                )
                return
            book_id, uid, effect = args[0], args[1], args[2]
            raw_state = args[3] if len(args) > 3 else ""
            try:
                if not raw_state:
                    active = plugin.core.timed_effect_state(
                        event.unified_msg_origin, book_id, uid, effect
                    )
                    yield plugin._result(
                        event,
                        f"{effect} 当前{'生效中' if active else '未生效'}（{book_id} uid={uid}）",
                    )
                    return
                if raw_state.strip().lower() in ("toggle", "t", "切换"):
                    current = plugin.core.timed_effect_state(
                        event.unified_msg_origin, book_id, uid, effect
                    )
                    enabled = not current
                else:
                    enabled = raw_state.strip().lower() in ("on", "true", "1", "开")
                active, applied = plugin.core.set_timed_effect(
                    event.unified_msg_origin, book_id, uid, effect, enabled
                )
            except TavernError as exc:
                yield plugin._result(event, str(exc))
                return
            if not applied:
                yield plugin._result(
                    event,
                    f"条目 {uid} 没有配置 {effect}（该字段为 0 或缺失），所以无法设置。"
                    "请先在世界书里给它填上对应的时长。",
                )
                return
            yield plugin._result(
                event,
                f"{effect} 已设为{'生效' if active else '失效'}（{book_id} uid={uid}）。"
                "只对当前会话分支有效；重开会话即清除。",
            )
            return

        yield plugin._result(
            event,
            "用法：/tavern worldbook list | on <名字> | off <名字> | effect <书> <uid> <效果> [on|off]",
        )

    @sub("reload", "重载")
    async def cmd_reload(plugin: TavernPlugin, event: Any):
        counts = plugin.core.reload_library()
        yield plugin._result(
            event,
            f"已重载：{counts['cards']} 张角色卡，{counts['worldbooks']} 本世界书，"
            f"{counts['presets']} 个预设。",
        )

    @sub("preview", "预览")
    async def cmd_preview(plugin: TavernPlugin, event: Any, text: str = ""):
        if not plugin.config.debug.show_debug_in_chat:
            yield plugin._result(event, "调试回显已在配置中关闭。")
            return
        try:
            turn = plugin.core.build_turn(
                event.unified_msg_origin, text or "（预览，无输入）", sender_name="预览"
            )
        except TavernError as exc:
            yield plugin._result(event, str(exc))
            return
        yield plugin._result(event, _clip(request_preview(turn), 1500))

    @sub("import", "导入")
    async def cmd_import(plugin: TavernPlugin, event: Any):
        if plugin.config.permissions.import_requires_admin and not _is_admin(event):
            yield plugin._result(event, "只有管理员可以导入文件。")
            return
        yield plugin._result(
            event,
            "把文件直接发给机器人就能导入：角色卡用 .png/.json/.yaml，"
            "世界书用 .json/.yaml（V2/V3/Agnai/Risu 都认）。\n"
            "也可以自己放进目录后执行 /tavern reload：\n"
            f"角色卡: {plugin.config.cards_dir}\n"
            f"世界书: {plugin.config.worldbooks_dir}\n"
            f"预设: {plugin.config.presets_dir}",
        )

    # ``staticmethod`` keeps ``inspect.getmembers`` unwrapping to the function
    # while the loader instantiates the plugin and calls ``handler(plugin, event)``.
    for name, func in (
        ("cmd_help", cmd_help),
        ("cmd_status", cmd_status),
        ("cmd_list_cards", cmd_list_cards),
        ("cmd_use_card", cmd_use_card),
        ("cmd_card_detail", cmd_card_detail),
        ("cmd_new_chat", cmd_new_chat),
        ("cmd_history", cmd_history),
        ("cmd_worldbook", cmd_worldbook),
        ("cmd_reload", cmd_reload),
        ("cmd_preview", cmd_preview),
        ("cmd_import", cmd_import),
    ):
        setattr(plugin_cls, name, staticmethod(func))
    return True


def main() -> None:
    """Fail loudly when the module is executed without AstrBot."""
    if not _require_astrbot():
        print(
            "tavern.main 必须在 AstrBot 内运行；直接执行时只有 st/ 与 core 层的单元测试可用。",
            file=sys.stderr,
        )
        raise SystemExit(2)
    print(f"{PLUGIN_NAME} {PLUGIN_VERSION} loaded")


_REGISTERED_SUBCOMMANDS = _register_commands(TavernPlugin)
if _require_astrbot():
    # ``on_message`` is a staticmethod carrying a filter created *before* the
    # class existed; register it through AstrBot's own decorator so the handler
    # metadata matches what ``call_handler`` looks up at runtime.
    TavernPlugin.on_message = staticmethod(
        filter.event_message_type(filter.EventMessageType.ALL)(TavernPlugin.on_message)
    )


if __name__ == "__main__":  # pragma: no cover
    main()
