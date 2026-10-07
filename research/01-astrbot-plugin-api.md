# AstrBot 插件 API 事实清单（面向「酒馆式角色扮演 + QQ 接入」插件）

> 调研员：subagent A ｜ 工作目录 `E:\astrbot_plugin` ｜ 目标读者：本项目的实现者
> 证据等级标注：
> - **[官方文档]** = docs.astrbot.app 原文
> - **[源码]** = AstrBot 源码原文（master 或 PyPI 发行版 4.25.2 源码）；PyPI 镜像可读链接：`https://gitlab.com/gitlab-oss-package-research/source/pypi/as/astrbot-cbc7072d/-/raw/4.25.2/astrbot-4.25.2/...`
> - **[实测]** = 本机可验证的事实
> - **[推测]** = 文档未明说，基于源码/社区实践推断，落地前需复核
>
> 本机限制：`curl`/`git`/`Invoke-WebRequest` 的 HTTPS 出网被 schannel 阻断，`raw.githubusercontent.com` DNS 在本 harness 内不可解析；`cdn.jsdelivr.net` 返回 `application/octet-stream` 无法读取。可用通道：`web_fetch` 访问 `docs.astrbot.app`、`github.com`（含 API）、`gitlab.com`、`deepwiki.com`（有 429 风控）、`web_search`。**结论：开发阶段无法 `git clone` AstrBot 源码，只能靠本文档 + 官方文档 + PyPI 源码镜像。**

---

## 0. 结论速览（给架构决策用）

| 需求 | AstrBot 提供的钩子 | 结论 |
|---|---|---|
| 角色卡（ST Character Card）导入 | 插件自己存盘（`data/plugin_data/<plugin>/`） | 插件自管 |
| 世界书 / Lorebook 关键词命中注入 | `@filter.on_llm_request` → `req.system_prompt` / `req.extra_user_content_parts` | **官方指定入口** |
| 多轮对话 + 聊天管理（酒馆式分支） | 要么用 AstrBot 会话（`context.conversation_manager`，每 umo 多对话 + `persona_id` + 完整 history 读写），要么插件完全自管 history 并直接 `llm_generate(contexts=...)` | **两条路都可行**，见 §5.5 选型 |
| 在 QQ 上对话（NapCat/OneBot v11） | aiocqhttp 适配器，反向 WS；插件无需关心协议 | 见 §9 |
| 直接调用外部已部署的酒馆 | AstrBot 不阻止异步 HTTP；插件用 `aiohttp`/`httpx` 直连 ST 的 `/api/v1/...`（详见 `03-sillytavern-formats-and-api.md`） | **推荐"薄插件 + 外部酒馆"路线** |
| 覆盖/改写 AstrBot 自己的 LLM 请求 | `on_llm_request` 里改 `req.prompt` / `req.contexts` / `req.system_prompt`；`event.should_call_llm(False)` 可阻止默认 LLM 链路 | 见 §4.5 |

---

## 1. 插件基本结构

### 1.1 目录与文件

```
data/plugins/astrbot_plugin_xxx/     # 插件根目录（目录名即插件名，推荐 astrbot_plugin_ 前缀、全小写、无空格）
├── metadata.yaml                    # 必需：插件元数据
├── main.py                          # 必需：插件主类所在文件，文件名固定 main.py
├── requirements.txt                 # 可选：pip 依赖，安装时自动 pip install
├── _conf_schema.json                # 可选：WebUI 配置表单 schema
├── logo.png                         # 可选：1:1，推荐 256x256（v4.5.0+）
├── pages/<page_name>/index.html     # 可选：WebUI 插件页面（见 §7.2）
├── skills/<name>/SKILL.md           # 可选：随插件分发的 Skill
└── i18n/…                           # 可选：插件国际化
```
来源：[插件开发指南（中文）](https://docs.astrbot.app/dev/star/plugin-new.html)、[Plugin Pages](https://docs.astrbot.app/en/dev/star/guides/plugin-pages.html)、[旧版指南](https://docs.astrbot.app/dev/star/plugin.html)

### 1.2 `metadata.yaml` 字段（源码级确认）

`StarMetadata` dataclass（[源码 `astrbot/core/star/star.py`](https://gitlab.com/gitlab-oss-package-research/source/pypi/as/astrbot-cbc7072d/-/raw/4.25.2/astrbot-4.25.2/astrbot/core/star/star.py)）：

```python
@dataclass
class StarMetadata:
    name: str | None = None            # 插件名
    author: str | None = None
    desc: str | None = None            # 简介
    short_desc: str | None = None      # 短简介（市场卡片；空则回退 desc）
    version: str | None = None
    repo: str | None = None            # 仓库地址
    display_name: str | None = None    # 展示名（v4.5.0+）
    logo_path: str | None = None       # logo.png 会被识别
    support_platforms: list[str] = field(default_factory=list)   # 必须是 ADAPTER_NAME_2_TYPE 的 key
    astrbot_version: str | None = None # PEP 440 specifier，不带 v 前缀
    i18n: dict[str, dict] = field(default_factory=dict)
    pages: list[dict] = field(default_factory=list)
    reserved: bool = False             # 是否 AstrBot 保留插件
    activated: bool = True
    config: AstrBotConfig | None = None
```

`metadata.yaml` 写法（[官方文档](https://docs.astrbot.app/dev/star/plugin-new.html)）：

```yaml
name: astrbot_plugin_tavern
display_name: 酒馆角色扮演
short_desc: 在 QQ 上用 SillyTavern 角色卡与世界书聊天
desc: 角色卡 / 世界书 / 多轮对话，支持调用外部 SillyTavern 服务
version: 0.1.0
author: YOUR_NAME
repo: https://github.com/YOUR_NAME/astrbot_plugin_tavern
astrbot_version: ">=4.16,<5"
support_platforms:
  - aiocqhttp
```

要点：
- 本文件是**权威来源**；类上的 `@register(...)` 优先级更低（旧文档原话：`metadata.yaml` 优先）。
- `support_platforms` 必须是字符串列表，取值限于：`aiocqhttp`、`qq_official`、`qq_official_webhook`、`telegram`、`wecom`、`wecom_ai_bot`、`lark`、`dingtalk`、`discord`、`slack`、`kook`、`vocechat`、`weixin_official_account`、`weixin_oc`、`satori`、`misskey`、`line`、`matrix`、`mattermost`（[官方文档](https://docs.astrbot.app/dev/star/plugin-new.html)）。
- `astrbot_version` 不满足时**插件被阻止加载**（WebUI 安装时可"无视警告继续安装"）。

### 1.3 `main.py` 最小形态 + 生命周期

```python
from astrbot.api import logger                     # 必须用 astrbot 的 logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import AstrBotConfig


@register("astrbot_plugin_tavern", "YOUR_NAME", "描述", "0.1.0", "repo_url")
class TavernPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}

    async def initialize(self) -> None:      # 插件被激活时调用（async，可 await 初始化）
        ...

    async def terminate(self) -> None:       # 插件被禁用/重载/卸载时调用
        ...
```

`Star` 基类真实定义（[源码 `astrbot/core/star/base.py`](https://gitlab.com/gitlab-oss-package-research/source/pypi/as/astrbot-cbc7072d/-/raw/4.25.2/astrbot-4.25.2/astrbot/core/star/base.py)）：

```python
class Star(CommandParserMixin, PluginKVStoreMixin):
    author: str
    name: str

    def __init__(self, context, config: dict | None = None) -> None:
        self.context = context

    async def text_to_image(self, text: str, return_url=True) -> str: ...
    async def html_render(self, tmpl: str, data: dict, return_url=True, options=None) -> str: ...
    async def initialize(self) -> None: ...   # 空实现，按需 override
    async def terminate(self) -> None: ...    # 空实现，按需 override
```
- `__init_subclass__` 会**自动**把子类登记进 `star_map` / `star_registry`，因此 `@register` 装饰器不是必需的（社区插件大量省略它）。
- 插件类必须定义在 `main.py`；所有 Handler 必须定义在插件类内部（Handler 前两个参数必须为 `self, event`）。

### 1.4 plugin data 目录约定（关键，踩坑重灾区）

官方口径 **[官方文档 Storage]**：大文件放 `data/plugin_data/{plugin_name}/`，可用 `StarTools.get_data_dir()` 或 `get_astrbot_data_path()` 取路径。

源码级真实签名（[源码 `astrbot/core/star/star_tools.py`](https://gitlab.com/gitlab-oss-package-research/source/pypi/as/astrbot-cbc7072d/-/raw/4.25.2/astrbot-4.25.2/astrbot/core/star/star_tools.py)）：

```python
@classmethod
def get_data_dir(cls, plugin_name: str | None = None) -> Path:
    """返回插件数据目录的绝对路径 data/plugin_data/{plugin_name}，自动 mkdir 并 resolve()。
    plugin_name 为 None 时通过调用栈 + star_map 自动推断（必须从插件方法内调用）。"""
```

推荐写法（双保险）：

```python
from pathlib import Path
from astrbot.api.star import StarTools

STORE = StarTools.get_data_dir("astrbot_plugin_tavern") / "tavern_store.json"
# 或自行拼：Path(get_astrbot_data_path()) / "plugin_data" / self.name
```

**血泪教训（社区实测）**：AstrBot 从 WebUI 安装/更新插件时会**整目录替换插件目录**，放在插件目录里的 JSON 数据会被一次更新清空。社区插件 `astrbot_plugin_worldbook` 曾因此丢掉全部世界书/角色卡/21 个会话绑定，v1.3.2 起改为写入 `data/plugin_data/<插件名>/` 并自动迁移旧文件。（来源：[该插件 README 附录](https://github.com/ysyhlly/astrbot_plugin_worldbook)）

另有简易 KV 存储（≥ v4.9.2，见 §7.1）。

### 1.5 `requirements.txt`

官方口径：插件依赖走 **pip 的 requirements.txt**，插件目录下建文件列出依赖即可，安装插件时自动装（[官方文档](https://docs.astrbot.app/dev/star/plugin-new.html)）。开发铁律（官方"开发原则"）：
- 持久化数据放 `data` 目录，**不要放插件目录**；
- **禁止 `requests`**，用 `aiohttp` / `httpx` 等异步库；
- 提交前用 `ruff` 格式化；
- 不要因为没有错误处理让一个异常把插件打崩。

---

## 2. 消息事件处理：过滤器与事件字段

### 2.1 `astrbot.api.event.filter` 的**完整**导出清单（源码级）

`astrbot/api/event/filter/__init__.py` 的 `__all__`（[源码](https://gitlab.com/gitlab-oss-package-research/source/pypi/as/astrbot-cbc7072d/-/raw/4.25.2/astrbot-4.25.2/astrbot/api/event/filter/__init__.py)）：

```python
CustomFilter, EventMessageType, EventMessageTypeFilter, PermissionType, PermissionTypeFilter,
PlatformAdapterType, PlatformAdapterTypeFilter,
after_message_sent, command, command_group, custom_filter, event_message_type, llm_tool,
on_agent_begin, on_agent_done, on_astrbot_loaded, on_decorating_result, on_llm_request,
on_llm_response, on_plugin_error, on_plugin_loaded, on_plugin_unloaded, on_platform_loaded,
on_waiting_llm_request, permission_type, platform_adapter_type, regex,
on_using_llm_tool, on_llm_tool_respond,
```

即"过滤器类"（参与事件匹配）与"钩子"（事件回调）是两套东西：

| 类型 | 装饰器 | 注册的 EventType | 语义 |
|---|---|---|---|
| 过滤器 | `@filter.command(name, alias=set(), **kw)` | `AdapterMessageEvent` | 指令，需被唤醒（私聊/At/唤醒前缀）；自动解析参数 |
| 过滤器 | `@filter.command_group(name, alias=set())` + `@group.command(...)` / `@group.group(...)` | `AdapterMessageEvent` | 指令组，可无限嵌套，无子指令时渲染树 |
| 过滤器 | `@filter.event_message_type(EventMessageType.X)` | `AdapterMessageEvent` | 私聊/群聊/全部 |
| 过滤器 | `@filter.platform_adapter_type(PlatformAdapterType.X \| Y)` | `AdapterMessageEvent` | 平台限定 |
| 过滤器 | `@filter.permission_type(PermissionType.ADMIN, raise_error=True)` | `AdapterMessageEvent` | 权限 |
| 过滤器 | `@filter.regex(pattern)` | `AdapterMessageEvent` | 正则匹配 `message_str` |
| 过滤器 | `@filter.custom_filter(CustomFilterCls)` | `AdapterMessageEvent` | 自定义过滤器 |
| 钩子 | `@filter.on_astrbot_loaded()` | `OnAstrBotLoadedEvent` | AstrBot 加载完成 |
| 钩子 | `@filter.on_platform_loaded()` | `OnPlatformLoadedEvent` | 平台加载完成 |
| 钩子 | `@filter.on_waiting_llm_request()` | `OnWaitingLLMRequestEvent` | **拿锁前**通知，只能 `event.send()` 不能 yield |
| 钩子 | `@filter.on_llm_request()` | `OnLLMRequestEvent` | **改 `req` 的入口**（签名 `(self, event, req)`） |
| 钩子 | `@filter.on_llm_response()` | `OnLLMResponseEvent` | 签名 `(self, event, response: LLMResponse)` |
| 钩子 | `@filter.on_agent_begin()` / `on_agent_done()` | `OnAgentBeginEvent` / `OnAgentDoneEvent` | `(event, run_context[, response])` |
| 钩子 | `@filter.on_using_llm_tool()` / `on_llm_tool_respond()` | `OnUsingLLMToolEvent` / `OnLLMToolRespondEvent` | 工具调用前后 |
| 钩子 | `@filter.on_decorating_result()` | `OnDecoratingResultEvent` | 发消息前 |
| 钩子 | `@filter.after_message_sent()` | `OnAfterMessageSentEvent` | 发消息后 |
| 钩子 | `@filter.on_plugin_loaded()` / `on_plugin_unloaded()` / `on_plugin_error()` | 对应 EventType | 插件加载/卸载/异常 |
| 工具 | `@filter.llm_tool(name=None, **kw)` | `OnCallingFuncToolEvent` | 定义 function-calling 工具（解析 docstring） |
| Agent | `@filter.agent(...)`（`register_agent`，未在 filter 导出） | — | 注册 Agent（handoff） |

`EventType` 枚举全量（[源码 `astrbot/core/star/star_handler.py`](https://gitlab.com/gitlab-oss-package-research/source/pypi/as/astrbot-cbc7072d/-/raw/4.25.2/astrbot-4.25.2/astrbot/core/star/star_handler.py)）：

```python
class EventType(enum.Enum):
    OnAstrBotLoadedEvent; OnPlatformLoadedEvent
    AdapterMessageEvent
    OnWaitingLLMRequestEvent      # 拿锁前
    OnLLMRequestEvent             # 插件/用户都能触发
    OnLLMResponseEvent
    OnAgentBeginEvent; OnAgentDoneEvent
    OnDecoratingResultEvent
    OnCallingFuncToolEvent
    OnUsingLLMToolEvent; OnLLMToolRespondEvent
    OnAfterMessageSentEvent
    OnPluginErrorEvent; OnPluginLoadedEvent; OnPluginUnloadedEvent
```

> ⚠️ 与旧文档的差异：新源码里 **`on_llm_request` 只注册 `OnLLMRequestEvent`**；旧指南说的"事件钩子不能与 `@filter.command` 等混用"要按"钩子类与过滤器类不要叠加"来理解，最稳妥做法是一个函数只挂一种装饰器。

### 2.2 `priority` 语义（源码级，重要）

`register_*` 都是 `(..., **kwargs)`，`kwargs` 被塞进 `StarHandlerMetadata.extras_configs`：

```python
class StarHandlerRegistry:
    def append(self, handler):
        if "priority" not in handler.extras_configs:
            handler.extras_configs["priority"] = 0
        self._handlers.append(handler)
        self._handlers.sort(key=lambda h: -h.extras_configs["priority"])   # 降序
```

- **默认 priority = 0**；**数值越大越先执行**（降序排序）。
- 过滤器本身（`CommandFilter` 等）没有 priority 概念；`priority` 是 Handler 级别的调度序。
- 写法：`@filter.command("x", priority=100)`、`@filter.on_llm_request(priority=10)`。
- 多装饰器 = **AND** 逻辑（[官方文档](https://docs.astrbot.app/dev/star/guides/listen-message-event.html)）。

### 2.3 `AstrMessageEvent` 真实字段与方法（源码级，v4.25.2）

```python
class AstrMessageEvent(abc.ABC):
    def __init__(self, message_str: str, message_obj: AstrBotMessage,
                 platform_meta: PlatformMetadata, session_id: str) -> None
```

实例属性：

| 属性 | 说明 |
|---|---|
| `message_str: str` | 纯文本消息（Plain 段拼接） |
| `message_obj: AstrBotMessage` | `type/self_id/session_id/message_id/group_id/sender/message/message_str/raw_message/timestamp` |
| `platform_meta: PlatformMetadata` | `.name`（如 `aiocqhttp`）、`.id`（唯一实例 id） |
| `session: MessageSession` | 会话三元组 |
| `role: str` | `"member"` 或 `"admin"` |
| `is_wake: bool` | 是否通过唤醒阶段（插件注册的监听器会把它设为 True） |
| `is_at_or_wake_command: bool` | 是否 At 机器人/带唤醒词/私聊 |
| `call_llm: bool` | **True 表示禁止 AstrBot 默认 LLM 请求**（由 `should_call_llm()` 设置） |
| `created_at: float`、`trace: TraceSpan`、`span` | 时间与链路追踪 |
| `plugins_name: list[str] \| None` | 该事件启用的插件白名单，None=全部 |
| `platform` | `platform_meta` 的向后兼容别名 |

属性/方法（全部真实存在）：

```python
event.unified_msg_origin            # property -> str(session)："{platform_id}:{message_type.value}:{session_id}"
event.session_id                    # property=session.session_id（可写）
event.get_platform_name() -> str    # 平台类型，如 aiocqhttp
event.get_platform_id() -> str      # 平台实例唯一 id（推荐用它取平台实例）
event.get_message_str() -> str
event.get_message_outline() -> str  # 图片->[图片] 等占位符
event.get_messages() -> list[BaseMessageComponent]
event.get_message_type() -> MessageType
event.get_session_id() -> str
event.get_group_id() -> str         # 私聊返回 ""
event.get_self_id() -> str          # 机器人自身 id
event.get_sender_id() -> str        # sender.user_id（必须是 str 才返回，否则 ""）
event.get_sender_name() -> str
event.is_private_chat() -> bool     # get_message_type() == MessageType.FRIEND_MESSAGE
event.is_wake_up() -> bool
event.is_admin() -> bool            # role == "admin"
event.set_extra(key, value) / get_extra(key=None, default=None) / clear_extra()
event.track_temporary_local_file(path) / cleanup_temporary_local_files()
event.stop_event() / continue_event() / is_stopped() -> bool
event.should_call_llm(call_llm: bool)   # 只阻止 AstrBot 默认 LLM 链路，不阻止插件里的 LLM 请求
event.set_result(result) / get_result() / clear_result()
event.make_result() -> MessageEventResult
event.plain_result(text) / image_result(url_or_path) / chain_result(chain)
event.request_llm(prompt, func_tool_manager=None, tool_set=None, session_id="",
                  image_urls=None, audio_urls=None, contexts=None,
                  system_prompt="", conversation=None) -> ProviderRequest
await event.send(message: MessageChain) -> None      # 直接发（不可 yield 的场合用它）
await event.react(emoji: str) -> None
await event.send_typing() / stop_typing()
await event.send_streaming(generator, use_fallback=False)
await event.get_group(group_id=None, **kwargs) -> Group | None   # aiocqhttp 支持
```

`unified_msg_origin` 的**确切**字符串格式（[源码 `message_session.py`](https://gitlab.com/gitlab-oss-package-research/source/pypi/as/astrbot-cbc7072d/-/raw/4.25.2/astrbot-4.25.2/astrbot/core/platform/message_session.py)）：

```python
@dataclass
class MessageSession:
    platform_name: str   # 实际是 platform_id（v4.0.0 起）
    message_type: MessageType
    session_id: str
    def __str__(self) -> str:
        return f"{self.platform_id}:{self.message_type.value}:{self.session_id}"
    @staticmethod
    def from_str(s): platform_id, message_type, session_id = s.split(":", 2); ...
MessageSesion = MessageSession   # 历史拼写别名，两个名字都能 import
```
示例：`aiocqhttp:GroupMessage:123456789`、`aiocqhttp:FriendMessage:10001`。
👉 **酒馆分支键建议直接用 `unified_msg_origin`**（它已经区分了平台实例/群或私聊/会话），再叠加 `conversation_id` 或自建 `char_id`。

### 2.4 指令参数解析（源码级，实用细节）

`CommandFilter` 会把指令后的文本按空格切分，并按 handler 的**类型注解**强转（[源码 `star/filter/command.py`](https://gitlab.com/gitlab-oss-package-research/source/pypi/as/astrbot-cbc7072d/-/raw/4.25.2/astrbot-4.25.2/astrbot/core/star/filter/command.py)）：

```python
@filter.command("send")
async def send(self, event: AstrMessageEvent, text: GreedyStr):   # 吃掉剩余全部文本
    yield event.plain_result(text)

@filter.command("set")
async def set_(self, event: AstrMessageEvent, level: int = 1):    # 有默认值 = 可选参数
    ...
```
- `GreedyStr`（`astrbot.core.star.filter.command.GreedyStr`）必须是**最后一个**参数，用于"角色卡导入 <名字> {JSON...}"这类指令。
- 参数缺失会抛 `ValueError`（会被上层转成错误提示）。
- 参数解析结果同时写入 `event.set_extra("parsed_params", params)`。

---

## 3. 发送消息

### 3.1 被动回复（handler 内 yield）

```python
@filter.command("hello")
async def hello(self, event: AstrMessageEvent):
    yield event.plain_result("Hello!")                       # 纯文本
    yield event.image_result("path/to/img.png")              # 本地图片
    yield event.image_result("https://example.com/a.jpg")    # 网络图片（http/https 开头）
    import astrbot.api.message_components as Comp
    yield event.chain_result([                                # 富媒体消息链
        Comp.At(qq=event.get_sender_id()),
        Comp.Plain("来看这个图："),
        Comp.Image.fromURL("https://example.com/image.jpg"),
        Comp.Image.fromFileSystem("path/to/image.jpg"),
    ])
```
来源：[消息的发送](https://docs.astrbot.app/dev/star/guides/send-message.html)

### 3.2 主动发送

```python
from astrbot.api.event import MessageChain

# 记住 umo，之后随时推
umo = event.unified_msg_origin
await self.context.send_message(umo, MessageChain().message("Hello!").file_image("p.png"))
```
`Context.send_message` 真实签名（[源码 `star/context.py`](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/star/context.py)）：

```python
async def send_message(self, session: str | MessageSession, message_chain: MessageChain) -> bool
# session 为 str 时会 MessageSession.from_str()；找不到平台返回 False 并 warning。
# qq_official（QQ 官方 API 平台）不支持主动消息。
```
`StarTools` 还提供（**classmethod，无需实例**）：

```python
await StarTools.send_message(umo, message_chain)                     # 同 context.send_message
await StarTools.send_message_by_id("GroupMessage", "123456", chain, platform="aiocqhttp")
await StarTools.send_message_by_id("PrivateMessage", "10001", chain)
```
`send_message_by_id` 目前**只实现 aiocqhttp**（其它 platform 抛 `ValueError`），底层走 `AiocqhttpMessageEvent.send_message(bot=..., is_group=..., session_id=...)`。这对"定时推送/主动打招呼"很有用。

### 3.3 消息段（`astrbot.api.message_components`）

通用：`Plain` `At` `AtAll` `Image` `Record` `Video` `File` `Reply` `Forward` `Node` `Nodes` `Face` `Poke`。（[旧版文档原文列表](https://docs.astrbot.app/dev/star/plugin.html)）
平台差异（官方矩阵）：QQ 个人号 aiocqhttp 支持 **全部**（含 Poke / Node(s) 合并转发）；QQ 官方 API 不支持 At/Record/Reply/主动消息。

```python
Comp.Image.fromURL(url) / Comp.Image.fromFileSystem(path)
Comp.Video.fromURL(url) / Comp.Video.fromFileSystem(path)
Comp.Record(file=path, url=path)      # 只接受 wav
Comp.File(file="path/to/f.txt", name="f.txt")   # 部分平台不支持
Comp.Node(uin=905617992, name="Soulter", content=[Comp.Plain("hi"), Comp.Image.fromFileSystem("t.jpg")])
```

⚠️ **aiocqhttp 坑**：发送 `Plain` 时会 `strip()` 去掉首尾空格与换行；需要保留时在文本前后加零宽空格 `\u200b`（[官方](https://docs.astrbot.app/dev/star/guides/send-message.html)）。酒馆式输出常有多行/首行缩进，**这里必踩**。

### 3.4 拿到 bot / 平台对象

```python
# 取平台实例（>= v4.0.0）
platform_id = event.get_platform_id()
platform = self.context.get_platform_inst(platform_id)      # -> Platform | None

# 取 aiocqhttp 的 client（可调 OneBot API）
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
assert isinstance(event, AiocqhttpMessageEvent)
client = event.bot                                            # aiocqhttp client
ret = await client.api.call_action("delete_msg", message_id=event.message_obj.message_id)
```
来源：[杂项（中文）](https://docs.astrbot.app/dev/star/guides/other.html)。`context.get_platform(PlatformAdapterType.AIOCQHTTP)` 已废弃（>= v4.0.0）。NapCat 的 API 文档：<https://napcat.apifox.cn/>。

---

## 4. 插件调用 LLM

### 4.1 取 provider id

```python
umo = event.unified_msg_origin
provider_id = await self.context.get_current_chat_provider_id(umo=umo)
```
- `get_current_chat_provider_id(umo)` 内部 = `get_using_provider_async(umo)` → `prov.meta().id`，取不到抛 `ProviderNotFoundError`。
- `get_using_provider(umo=None)` / `get_using_provider_async(umo=None)`（推荐 async 版本，旧的同步版已 `@deprecated`）。
- `get_all_providers() -> list[Provider]`（只含 chat completion）、`get_all_tts_providers()`、`get_all_stt_providers()`、`get_all_embedding_providers()`、`get_provider_by_id(provider_id)`。
- 来源：[官方 AI 文档](https://docs.astrbot.app/en/dev/star/guides/ai.html)、[源码 context.py](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/star/context.py)

### 4.2 `Context.llm_generate` 真实签名（源码级）

```python
async def llm_generate(
    self,
    *,
    chat_provider_id: str,
    prompt: str | None = None,
    image_urls: list[str] | None = None,
    audio_urls: list[str] | None = None,
    tools: ToolSet | None = None,
    system_prompt: str | None = None,     # 会作为第一条 system message 插入
    contexts: list[Message] | None = None, # 自带上下文；同时给 prompt 时 prompt 追加为最后一条 user
    **kwargs,                             # 额外参数直通 provider.text_chat()（OpenAI 兼容，如 temperature）
) -> LLMResponse
```
- `system_prompt` 提供时**总是插入为第一条 system message**。
- `contexts` + `prompt` 同时给：`prompt` 作为最后一条 user message 追加；`image_urls`/`audio_urls` 也追加到最后一条 user。
- 返回值 `LLMResponse`，取文本用 `resp.completion_text`（内部 = `resp.result_chain.get_plain_text()`，空链时回退 `_completion_text`）。

**能否绕过 AstrBot 的会话历史自己组装 prompt？→ 能。** `llm_generate(contexts=[...])` 完全不碰 `conversation_manager`，等于你自己管 prompt。

### 4.3 直接调 provider（更底层，可完全自定义）

```python
prov = await self.context.get_using_provider_async(umo)
resp = await prov.text_chat(prompt=..., system_prompt=..., contexts=[...], ...)
```
`ProviderRequest` 字段（[源码 `provider/entities.py`](https://gitlab.com/gitlab-oss-package-research/source/pypi/as/astrbot-cbc7072d/-/raw/4.25.2/astrbot-4.25.2/astrbot/core/provider/entities.py)）：

```python
@dataclass
class ProviderRequest:
    prompt: str | None = None
    session_id: str | None = ""
    image_urls: list[str] = field(default_factory=list)
    audio_urls: list[str] = field(default_factory=list)
    extra_user_content_parts: list[ContentPart] = field(default_factory=list)  # 动态上下文，放在本轮 user 输入之后
    func_tool: ToolSet | None = None
    contexts: list[dict] = field(default_factory=list)   # OpenAI 格式
    system_prompt: str = ""
    conversation: Conversation | None = None             # 关联对话（决定 history 读写与 persona）
    tool_calls_result: list[ToolCallsResult] | ToolCallsResult | None = None
    model: str | None = None
```
`LLMResponse` 关键字段：`role`（`assistant`/`tool`/`err`）、`completion_text`（property）、`result_chain: MessageChain`、`tools_call_args/name/ids`、`reasoning_content`、`raw_completion`、`usage: TokenUsage`。

### 4.4 让**普通聊天消息**进入自定义处理（Hijack 方案）

三条真实可行的路径，按侵入度排序：

**A. 在 `on_llm_request` 里改请求（最小侵入，推荐）**

```python
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import TextPart

@filter.on_llm_request()
async def inject_tavern(self, event: AstrMessageEvent, req: ProviderRequest):
    # 稳定人设 / 世界书常驻 → 写 system_prompt（官方推荐位置，注意 prompt cache）
    req.system_prompt = self.persona_card_prompt + "\n" + req.system_prompt
    # 每轮变化的动态内容（关键词命中的世界书条目、场景、记忆）→ 走 extra_user_content_parts
    wb = self.match_worldbook(req.prompt, req.contexts)
    if wb:
        req.extra_user_content_parts.append(TextPart(text=f"<world_info>\n{wb}\n</world_info>"))
    # 关键世界书条目可以顺带写入 contexts 做更深度的控制
    # req.contexts.insert(0, {"role": "system", "content": "..."})
```
官方警告：`req.system_prompt += ...` 只适合**稳定内容**；每轮变化的内容（时间、好感度、状态栏、检索摘要）写进 system_prompt 会破坏 prompt 缓存，成本上升约 **7–20 倍**，务必用 `req.extra_user_content_parts`。`TextPart(...).mark_as_temp()`（≥ v4.24.0）可让内容只参与本轮、不落库。来源：[官方 listen-message-event 中文页](https://docs.astrbot.app/dev/star/guides/listen-message-event.html)。

**B. 完全接管：`@filter.event_message_type(filter.EventMessageType.ALL)` + `event.should_call_llm(False)`**

```python
@filter.event_message_type(filter.EventMessageType.ALL)
async def on_all(self, event: AstrMessageEvent):
    if not self.enabled(event.unified_msg_origin):
        return
    event.should_call_llm(False)         # 阻止 AstrBot 自带 LLM 链路
    reply = await self.my_tavern_chat(event)   # 自己组 prompt / 调 llm_generate 或外部酒馆
    yield event.plain_result(reply)
```
注意：源码里 `ProcessStage` 的默认 LLM 分支条件是 `not event._has_send_oper and event.is_at_or_wake_command and not event.call_llm`；`event.send()` / `yield` 过结果都会置 `_has_send_oper = True`。所以"自己发过消息"天然也会阻止默认 LLM，但**显式** `should_call_llm(False)` 更可靠。

**C. 命令式会话控制器**（适合 `/tavern` 之后进入多轮）：见 §5.4。

### 4.5 `on_llm_request` 是"改请求"还是"截断"（源码级）

`InternalAgentSubStage.process` 里的关键片段（[源码 internal.py](https://gitlab.com/gitlab-oss-package-research/source/pypi/as/astrbot-cbc7072d/-/raw/4.25.2/astrbot-4.25.2/astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py)）：

```python
await call_event_hook(event, EventType.OnWaitingLLMRequestEvent)   # 拿锁前
async with session_lock_manager.acquire_lock(event.unified_msg_origin):
    build_result = await build_main_agent(event=event, plugin_context=..., config=build_cfg, apply_reset=False)
    req = build_result.provider_request
    ...
    if await call_event_hook(event, EventType.OnLLMRequestEvent, req):
        if reset_coro: reset_coro.close()
        return          # ← 钩子返回真值 = 跳过本次默认 LLM 调用
    ... 正常跑 agent_runner，最后 _save_to_history(...)
```
- 钩子签名定为 `(self, event, req)`；**返回真值（truthy）可中止默认 LLM 请求**；返回 `None` 表示继续。
- 钩子**修改后的 `req` 会被真正用于调用**（`agent_runner` 用同一个 `req`）。
- 运行结束后 `_save_to_history()` 只在 `req.conversation` 存在时写库：`await self.conv_manager.update_conversation(event.unified_msg_origin, req.conversation.cid, history=message_to_save, token_usage=...)`，并且**第一段 system message 不入库**。

### 4.6 persona（人设）在 AstrBot 内部如何存储与生效

数据库模型（[源码 `db/po.py`](https://gitlab.com/gitlab-oss-package-research/source/pypi/as/astrbot-cbc7072d/-/raw/4.25.2/astrbot-4.25.2/astrbot/core/db/po.py)）：

```python
class Persona(TimestampMixin, SQLModel, table=True):
    __tablename__: str = "personas"
    persona_id: str                  # unique
    system_prompt: str               # Text，人设正文
    begin_dialogs: list | None       # JSON：开场白对话
    tools: list | None               # None=全部工具，[]=不用工具
    skills: list | None
    custom_error_message: str | None
    folder_id / sort_order
```
- [官方文档/社区实践] 加人设：WebUI 「人格」页新建，或在插件里同步（社区 `astrbot_plugin_worldbook` 会"同步/创建 AstrBot 人格（`wb_<角色名>`）"并绑定会话 persona）。
- 生效方式：`conversation.persona_id` 指向某个 persona；`build_main_agent` 把 persona 的 `system_prompt`、工具集、begin_dialogs 组装进 `ProviderRequest`（`req.system_prompt` / `req.func_tool`）。**具体拼接顺序（persona / custom rules / 工具说明 / 插件追加）本报告未逐行核对 → 见 §10 疑点。**
- **插件想彻底掌控人设**：在 `on_llm_request` 里覆写 `req.system_prompt`，或走 §4.2 自己调 `llm_generate(system_prompt=..., contexts=...)`，两条路都不依赖 persona 表。

---

## 5. 会话控制 / 聊天记录归谁管

### 5.1 两个层次，别混淆

| 层次 | 概念 | 谁在用 |
|---|---|---|
| **session（会话）** | `unified_msg_origin`，= 平台实例:消息类型:会话id。`PlatformSession` 表只在 WebChat 用途明显 | 平台消息路由 |
| **conversation（对话）** | 一个 session 下可以有**多个** conversation（= 酒馆的"聊天分支"），有 `title` / `persona_id` / `content`(history) / `token_usage`，`session -> 当前 conversation` 的映射存在 `shared_preferences` | LLM 历史 |

### 5.2 `context.conversation_manager` 真实 API（源码级）

```python
class ConversationManager:
    def __init__(self, db_helper: BaseDatabase) -> None

    async def new_conversation(self, unified_msg_origin: str,
                               platform_id: str | None = None,
                               content: list[dict] | None = None,
                               title: str | None = None,
                               persona_id: str | None = None) -> str          # 返回 conversation_id(uuid)
    async def switch_conversation(self, unified_msg_origin: str, conversation_id: str) -> None
    async def delete_conversation(self, unified_msg_origin: str, conversation_id: str | None = None) -> None
    async def delete_conversations_by_user_id(self, unified_msg_origin: str) -> None
    async def get_curr_conversation_id(self, unified_msg_origin: str) -> str | None
    async def get_conversation(self, unified_msg_origin: str, conversation_id: str,
                               create_if_not_exists: bool = False) -> Conversation | None
    async def get_conversations(self, unified_msg_origin: str | None = None,
                                platform_id: str | None = None) -> list[Conversation]
    async def get_filtered_conversations(self, page: int = 1, page_size: int = 20,
                                         platform_ids: list[str] | None = None,
                                         search_query: str = "", **kwargs) -> tuple[list[Conversation], int]
    async def update_conversation(self, unified_msg_origin: str, conversation_id: str | None = None,
                                  history: list[dict] | None = None, title: str | None = None,
                                  persona_id: str | None = None, token_usage: int | None = None) -> None
    async def update_conversation_title(self, unified_msg_origin, title, conversation_id=None) -> None   # deprecated
    async def update_conversation_persona_id(self, unified_msg_origin, persona_id, conversation_id=None) -> None  # deprecated
    async def add_message_pair(self, cid: str, user_message, assistant_message) -> None
    async def get_human_readable_context(self, unified_msg_origin, conversation_id,
                                         page: int = 1, page_size: int = 10) -> tuple[list[str], int]
    def register_on_session_deleted(self, callback) -> None
```
来源：[[源码 4.25.2 conversation_mgr.py]](https://gitlab.com/gitlab-oss-package-research/source/pypi/as/astrbot-cbc7072d/-/raw/4.25.2/astrbot-4.25.2/astrbot/core/conversation_mgr.py)。`update_conversation(history=...)` 传的是 **OpenAI 格式 `list[dict]`**（`content` 字段），落库时序列化成 JSON。

### 5.3 `Conversation` 对象字段（两个版本，务必注意）

**当前（≥ 4.25，`Conversation` 是 dataclass，从 `ConversationV2` ORM 转换而来）**：

```python
@dataclass
class Conversation:
    platform_id: str
    user_id: str
    cid: str                 # 对话 ID，uuid 字符串
    history: str = ""        # ← 字符串！json.dumps(list[dict])
    title: str | None = ""
    persona_id: str | None = ""
    created_at: int = 0      # unix ts
    updated_at: int = 0
    token_usage: int = 0
# 读取历史：json.loads(conversation.history)
```

**底层表 `ConversationV2` / `conversations`**：

```python
class ConversationV2(TimestampMixin, SQLModel, table=True):
    __tablename__: str = "conversations"
    inner_conversation_id: int   # PK autoincrement
    conversation_id: str         # unique, max_length=36, default uuid4
    platform_id: str
    user_id: str                 # ← 存的是 unified_msg_origin
    content: list | None         # JSON，OpenAI 格式 list[dict]
    title: str | None
    persona_id: str | None
    token_usage: int = 0
```
> 🔴 **没有 `extra` / 自定义字段列**。想给"酒馆聊天"存 `character_id`、`worldbook_id`、分支名等元数据，只能：
> 1) 编码进 `title`（脏，但 WebUI 可见）；2) 插件自建 JSON/SQLite（推荐）；3) AstrBot 的 `Preference` 表（`scope="plugin"`）或 `put_kv_data`（键值，需自己按 umo+cid 组装 key）。

**旧版（≤ 4.24 社区代码常见）**：`Conversation` 是 SQLModel 表，字段 `id / platform_id / user_id / cid / history: str / title / persona_id / created_at / updated_at / token_usage`，`cid` 是主键列名。旧教程写法 `conv = await ...get_conversation(umo, cid); conv.history` 仍可读，但 **v4.25 起 `history` 是 JSON 字符串，`conv.history` 直接当 list 用会炸**。

### 5.4 官方"会话控制器"：`session_waiter`（多轮交互最省事的写法）

```python
import astrbot.api.message_components as Comp
from astrbot.core.utils.session_waiter import session_waiter, SessionController, SessionFilter

@filter.command("tavern")
async def tavern(self, event: AstrMessageEvent):
    yield event.plain_result("开始酒馆模式，输入 /exit 退出")

    @session_waiter(timeout=600, record_history_chains=False)
    async def waiter(controller: SessionController, event: AstrMessageEvent):
        if event.message_str.strip() in ("/exit", "退出"):
            await event.send(event.plain_result("已退出~"))   # 注意：不能 yield
            controller.stop()
            return
        resp = await self.chat_once(event)
        await event.send(event.make_result().message(resp))
        controller.keep(timeout=600, reset_timeout=True)

    try:
        await waiter(event)
    except TimeoutError:
        yield event.plain_result("超时了")
    finally:
        event.stop_event()
```
`SessionController`：`keep(timeout: float, reset_timeout: bool)`、`stop()`、`get_history_chains() -> List[List[Comp.BaseMessageComponent]]`。
**默认以 `sender_id` 作为会话键**；想"整个群一个会话"要自定义 `SessionFilter`：

```python
class GroupSessionFilter(SessionFilter):
    def filter(self, event: AstrMessageEvent) -> str:
        return event.get_group_id() or event.unified_msg_origin
await waiter(event, session_filter=GroupSessionFilter())
```
来源：[会话控制（中文）](https://docs.astrbot.app/dev/star/guides/session-control.html)。
✅ 这个机制**天然适合"每个 QQ 用户 / 每个群一条独立酒馆聊天"**，且它接管的是"后续普通消息"，不要求用户每轮带指令。

### 5.5 结论：酒馆聊天记录必须走 AstrBot 会话吗？

**不必须。** 三种可行架构：

| 方案 | 做法 | 优点 | 代价 |
|---|---|---|---|
| **A. 全量复用 AstrBot 会话** | `conversation_manager` 每 umo 多 conversation + `persona_id`；历史由 AstrBot 自动读写 | 与 WebUI 聊天管理/AstrBot 人格无缝，零存储 | 无法存自定义元数据；`history` 是 JSON 字符串要自己 parse；受 AstrBot 上下文裁剪/压缩策略影响 |
| **B. 插件自管 history** | 自己存 `data/plugin_data/<plugin>/chars/<char>/chats/<chat>.json`；调用时 `llm_generate(chat_provider_id=..., contexts=[...], system_prompt=card_prompt)` | 与酒馆语义 1:1（char card / world info / depth / 分支），完全可控 | 要自己实现裁剪、token 统计、WebUI 展示；WebUI 里看不到聊天 |
| **C. 混合（推荐）** | 用 `conversation_manager` 让每个"分支"= 一个 conversation（`title` 存角色名），把酒馆元数据放插件 JSON；每轮在 `on_llm_request` 注入世界书 | 兼顾 AstrBot 原生能力与酒馆语义 | 需要处理 v4.25 的 `history` 字符串格式与字段缺失 |

若走"调用外部酒馆"路线：**history 由酒馆服务端持有**（ST 的 `/api/chats/...`），插件只做 QQ ↔ 酒馆的翻译层，方案 B 的复杂度大幅下降（详见 `03-sillytavern-formats-and-api.md`）。

---

## 6. 插件配置 `_conf_schema.json`

### 6.1 支持的 `type`

`string` `text`（大 textarea）`int` `float` `bool` `object` `list` `dict` `template_list`（≥ v4.10.4）`file`（≥ v4.13.0）。
来源：[插件配置（英文）](https://docs.astrbot.app/en/dev/star/guides/plugin-config.html)

### 6.2 字段与渲染

| 字段 | 说明 |
|---|---|
| `type` | **必需** |
| `description` | 一句话说明 |
| `hint` | 问号悬浮提示 |
| `obvious_hint` | 是否显著展示 |
| `default` | 默认值；未给时 string=""、int=0、float=0.0、bool=False、object={}、list=[] |
| `items` | `object` 类型的子 schema（可嵌套） |
| `invisible` | 隐藏（默认 false） |
| `secret` | string / string-list 显示为密码框（仅 UI 掩码，**配置文件里仍是明文**） |
| `options` | 下拉选项，如 `["chat","agent","workflow"]` |
| `slider` | int/float：`{"min":1,"max":100,"step":1}` |
| `editor_mode` / `editor_language` / `editor_theme` | 代码编辑器（≥ v3.5.10），theme: `vs-light`(默认)/`vs-dark` |
| `_special` | ≥ v4.0.0：`select_provider`、`select_provider_tts`、`select_provider_stt`、`select_persona`（返回 str）、`select_knowledgebase`（配合 `type: list`） |
| `templates` / `template_schema` | `template_list` / `dict` 类型的结构 |
| `file_types` | `type: file` 时允许的扩展名，如 `["pdf","docx"]` |

### 6.3 一个贴近本项目的 schema 样例

```json
{
  "backend": {
    "description": "后端模式",
    "type": "string",
    "options": ["astrbot_llm", "external_tavern"],
    "default": "astrbot_llm"
  },
  "tavern_base_url": {
    "description": "外部 SillyTavern 服务地址",
    "type": "string",
    "default": "http://127.0.0.1:8000"
  },
  "tavern_api_key": {
    "description": "SillyTavern API Key",
    "type": "string",
    "default": "",
    "secret": true
  },
  "provider_id": {
    "description": "AstrBot 模型提供商（留空则跟随当前会话）",
    "type": "string",
    "default": "",
    "_special": "select_provider"
  },
  "worldbook": {
    "description": "世界书注入配置",
    "type": "object",
    "items": {
      "enabled": { "description": "启用", "type": "bool", "default": true },
      "inject_mode": { "description": "注入位置", "type": "string", "options": ["system","extra","both"], "default": "both" },
      "max_entries": { "description": "关键词条目上限", "type": "int", "default": 10, "slider": {"min":1,"max":50,"step":1} }
    }
  }
}
```

### 6.4 读写

```python
from astrbot.api import AstrBotConfig
class TavernPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config            # AstrBotConfig 继承 Dict，支持所有字典方法
        # self.config.save_config()     # 直接保存
```
- Schema 存在时，AstrBot 自动把配置落到 **`data/config/<plugin_name>_config.json`** 并作为 `__init__` 第二个参数注入。
- 升级 Schema 时，AstrBot 递归比对，**自动补默认值、删除已不存在的项**。
来源：[插件配置](https://docs.astrbot.app/en/dev/star/guides/plugin-config.html)

---

## 7. 持久化与 WebUI 插件页

### 7.1 持久化三件套

```python
# (1) 简易 KV（≥ v4.9.2），底层 shared_preferences
await self.put_kv_data("greeted", True)
greeted = await self.get_kv_data("greeted", False)
await self.delete_kv_data("greeted")
# 签名（源码 star/base.py 混入 PluginKVStoreMixin / utils/plugin_kv_store.py）：
#   async def put_kv_data(self, key: str, value: int|float|str|bytes|bool|dict|list|None) -> None
#   async def get_kv_data(self, key: str, default: _VT) -> _VT | None
#   async def delete_kv_data(self, key: str) -> None
# plugin_id = self.plugin_id（插件名）

# (2) 自己建 JSON 存盘（推荐给角色卡/世界书这种大结构）
from astrbot.api.star import StarTools
import json, asyncio
DATA = StarTools.get_data_dir("astrbot_plugin_tavern")
CHARS = DATA / "characters"
```

(3) AstrBot 的 SQLite（`context.get_db()`，BaseDatabase）也可用，但**不要**直接读写 AstrBot 的表结构，升级会炸。

官方铁律：**运行数据不要放插件目录**（更新会整目录替换）。

### 7.2 WebUI 插件页面（`pages/`）

结构：

```
astrbot_plugin_tavern/
├─ main.py
└─ pages/
   └─ tavern/index.html        # 只有 pages/<page_name>/index.html 会被发现
```
后端（[官方 Plugin Pages](https://docs.astrbot.app/en/dev/star/guides/plugin-pages.html)）：

```python
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request

PLUGIN_NAME = "astrbot_plugin_tavern"

class TavernPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        context.register_web_api(f"/{PLUGIN_NAME}/cards", self.list_cards, ["GET"], "List character cards")
        context.register_web_api(f"/{PLUGIN_NAME}/cards/<card_id>", self.get_card, ["GET"], "Get card")
        context.register_web_api(f"/{PLUGIN_NAME}/upload", self.upload, ["POST"], "Upload card")

    async def list_cards(self):
        limit = request.query.get("limit", 20, type=int)
        return json_response({"items": [...], "limit": limit, "username": request.username})

    async def get_card(self, card_id: str):                     # 动态段作为 kwarg 传入
        return json_response({"card_id": card_id})
```
前端要点：
- 页面里 `window.AstrBotPluginPage` 由 AstrBot 注入（SDK 路径 `/api/plugin/page/bridge-sdk.js`）；用 `const ctx = await bridge.ready()`；请求用 `await bridge.apiGet("cards", { limit: 20 })`（**不带插件名前缀**）。
- Dashboard 会把它转发到 `/api/v1/plugins/extensions/<plugin_name>/cards`，与注册路由 `/<plugin_name>/cards` 对应。
- 文件上传：`request.form()` + `request.files()` → `PluginUploadFile`，落盘建议 `get_astrbot_plugin_data_path()/<plugin>/imports/`。
- 路由动态段：`<name>` 单段、`<path:name>` 多段。
- 响应工具：`json_response / error_response / file_response / stream_response(SSE)`。
- ⚠️ 社区踩坑：`PluginRequest` 把查询串挂在 `request.query` 上（不是 `query_params`/`args`）；旧代码只查后两者会静默拿到空字符串（`astrbot_plugin_worldbook` v1.3.1 修复记录）。
- 增删 `pages/` 目录需要**重载插件**；只改静态资源刷新页面即可。

### 7.3 指令注册回顾

```python
@filter.command("tavern") @filter.command_group("tavern") @tavern.command("new") ...
@filter.command("help", alias={"帮助", "helpme"})       # ≥ v3.4.28 支持别名
@filter.command("import", priority=10)                  # 数字序，越大越先
```
详见 §2.1 / §2.4。

---

## 8. 版本兼容与常见坑

### 8.1 `astrbot_version` 与版本策略
- `metadata.yaml` 的 `astrbot_version` 用 PEP 440（`>=4.16,<5`），不满足即**阻止加载**。本项目要用的 `on_llm_request` / `extra_user_content_parts` / `mark_as_temp` 分别对应 v3.4.34+ / 4.x / 4.24+，建议声明 `>=4.17,<5`，并检测运行时特性而非硬编码。

### 8.2 v4.x 之后的已知 API 变更（务必遵守）
| 旧 | 新 |
|---|---|
| `context.get_platform(PlatformAdapterType.AIOCQHTTP)` | `context.get_platform_inst(event.get_platform_id())`（v4.0.0 起旧方法 deprecated） |
| 手写 `@register(...)` 提供元数据 | `metadata.yaml` 权威；类上装饰器可省 |
| `context.register_llm_tool(...)` | **已废弃**，用 `@filter.llm_tool` 或 `context.add_llm_tools(FunctionTool...)`（≥ v4.5.1） |
| `Conversation` 是表、`history` 是 list | v4.25 起 `Conversation` 是 dataclass、`history` 是 **JSON 字符串** |
| `context.provider_manager.llm_tools.func_list.append(...)` | `context.add_llm_tools(...)` |
| `session_id` 作为会话唯一键 | 用 `unified_msg_origin`（含平台实例 id） |

### 8.3 反模式清单（每条都有真实事故）
1. **阻塞事件循环**：同步 `requests`、`time.sleep`、大文件同步读 → 官方明确禁止 `requests`，用 `aiohttp`/`httpx`；重活用 `asyncio.to_thread` 或 `context.register_task`（已废弃，改为在 `initialize()` 里 `asyncio.create_task`）。
2. **重复回复**：同时 `yield` 和 `await event.send()` 同一内容 → 发两条；`on_waiting_llm_request` / `session_waiter` 内**只能** `event.send()` 不能 `yield`。
3. **命令后仍触发 LLM**：只 `yield` 不 `event.stop_event()` 时，若事件被唤醒且没有发送过结果，默认 LLM 分支仍可能跑（见 §4.4/§4.5 的 `ProcessStage` 条件）。
4. **数据放插件目录**：WebUI 更新插件会整目录替换 → 数据全丢（社区已真实翻车）。
5. **把每轮变化的内容塞进 `system_prompt`**：破坏 prompt cache，成本涨 7–20 倍（官方警告）。
6. **aiocqhttp 发送 Plain 被 `strip()`**：首尾空行丢失，用 `\u200b` 兜。
7. **LLM 工具 docstring 格式**：`@filter.llm_tool` 用 **docstring** 生成参数 schema（不读类型注解），格式必须是 `param(type): desc`，缺 `Args:` 段会导致参数被静默丢弃；传 `parameters=` 会被忽略（[官方 AI 页](https://docs.astrbot.app/en/dev/star/guides/ai.html)）。
8. **`metadata.yaml` 写错**：插件直接不加载（重载列表里报错）。
9. **命令名带空格**：会被当成第二个参数。
10. **`config` 参数形式**：`__init__(self, context, config)` 是 v4 主流；只写 `(self, context)` 会拿不到配置（旧部分文档仍写两参形式，源码里 config 是可选参）。

### 8.4 上下文窗口/裁剪
AstrBot 自己有上下文管理（`max_context_length`、`dequeue_context_length`、`context_limit_reached_strategy`、LLM 压缩等，见 internal stage 配置项）。插件自管 history 时**要自己实现裁剪**，否则长对话会被 provider 拒。

---

## 9. 本地调试 & 平台接入

### 9.1 本地跑起来
1. 插件放 `AstrBot/data/plugins/<name>/`，启动 AstrBot（`astrbot run` 或桌面端）。
2. 改完代码 → WebUI `插件` 页 → 插件卡片刷新图标（`重载插件`）；加载失败的在 `加载失败插件` 列表点重载。
3. 日志：WebUI `数据与日志 → 日志`；插件内用 `from astrbot.api import logger`（会自动路由到当前插件的 logger，等级可在插件配置弹窗单独设）。**不要用标准 `logging` 模块**。
4. 没有 AstrBot 环境时：把纯逻辑（世界书匹配、卡解析）写成不依赖 AstrBot 的模块 + `tests/conftest.py` 打桩。社区 `astrbot_plugin_worldbook` 就是这么做的（`python -m pytest tests -q`，127 项测试不依赖 AstrBot 运行时）——**本项目应沿用这个模式**。
5. ⚠️ 本机现状：**未安装 AstrBot、未部署 SillyTavern / NapCat**，且本机 HTTPS 出网损坏 → 端到端联调必须由用户提供环境或允许部署（见 `00-lead-index.md` 环境事实）。

### 9.2 NapCat / aiocqhttp（OneBot v11）接入
官方步骤（[接入 OneBot v11](https://docs.astrbot.app/en/platform/aiocqhttp.html)）：
1. AstrBot WebUI → `平台` → `新增适配器` → 选 `OneBot v11`。
2. 填写：`ID`（任意，区分实例）、`启用`、**反向 WebSocket host**（一般 `0.0.0.0`）、**反向 WebSocket port**（默认 `6199`）、**反向 WebSocket token**（当 NapCat 网络配置设了 token 时必须一致）。
3. NapCat 侧配置**反向 WebSocket**，URL 形如 `ws(s)://<astrbot-host>:6199/ws`，AstrBot 作服务端、NapCat 作客户端。
4. 验证：日志出现 `aiocqhttp(OneBot v11) adapter connected.`；若几秒后 `aiocqhttp adapter has been closed` 说明握手失败，检查 host/port/token。
5. 官方同时提示：接 QQ 优先推荐 QQ 官方机器人（WebSockets）；NapCat 属"社区实现"，需自行按其文档部署（NapCat 仓库：<https://github.com/NapNeko/NapCatQQ>）。
6. 常用 API：`event.bot.api.call_action("delete_msg", message_id=...)` 等，参考 <https://napcat.apifox.cn/>。

---

## 10. 尚未确认的疑点（落地前必须验证）

1. **`build_main_agent` 内部拼装顺序**：persona.system_prompt、AstrBot 自定义规则、工具提示、`begin_dialogs`、`contexts`(history) 的确切拼接与优先级未逐行核对；`req.contexts` 与 `req.conversation` 同时存在时的最终 messages 组装、`apply_reset` 语义也未知 → **本文档只确认"钩子能改 req 且改动生效"**。
2. **`on_llm_request` 返回值语义**：源码显示 `call_event_hook(...)` 返回真值时跳过本次默认 LLM 调用，但 `call_event_hook` 对多个钩子的聚合规则（任一真值即真？）未核对。
3. **旧版 `Conversation` 与 v4.25 `Conversation` 的兼容边界**：旧教程代码在新版本下读写 `history` 的具体失败点（`json.loads` vs 直接用）未实测。
4. **`data/plugin_data` 权限与路径**：`StarTools.get_data_dir()` 的 `self.name` 在 `__init__` 之前是否可用、Docker 部署下 `data` 卷的挂载路径未验证。
5. **WebUI 插件页鉴权细节**：`request.username` 是否总是存在、`pages/` 的 CSP/iframe 限制（本地文件、外部 CDN、字体）未验证；社区插件用"只读开关 + 管理员写"兜底，本项目可照抄。
6. **`context.register_task` 是否已彻底移除**（源码中 `register_task` 被标 deprecated 并写 `self._register_tasks`）→ 后台任务的官方推荐写法（`initialize()` 里 `asyncio.create_task`）未在文档中明说，属 **[推测]**。
7. **`extra_user_content_parts` 的具体落位**：`ProviderRequest.assemble_context()` 已确认把 `part.model_dump_for_context()` 放在**本轮 user 文本之后**、图片之前；但"多轮历史 + 该 part"在 provider 适配层是否总是保留，未逐 provider 验证。
8. **`mark_as_temp()`（≥ v4.24.0）** 的持久化行为只在官方中文页出现一次，未在源码中定位实现。
9. **NapCat 的消息段 ↔ AstrBot `Comp` 双向映射细节**（例如 `forward`/`node` 的构造参数、`Reply` 在 aiocqhttp 下的字段）未逐条验证。
10. **AstrBot HTTP API（`/api/v1/...`）** 是否更适合"外部程序驱动 AstrBot"（而非写插件）——官方有 [HTTP API](https://docs.astrbot.app/dev/openapi.html) 与 [API Scope](https://docs.astrbot.app/dev/openapi-scopes.html) 页面，本次未调研，若用户想"外部酒馆主动推 QQ"值得补。
11. **插件市场对 `_conf_schema.json`/`pages/` 的最新校验规则**（[插件市场规范 2026-06-27](https://docs.astrbot.app/dev/plugin-market/)）未读。

---

## 附：核心来源清单

- 插件开发指南（中文）：<https://docs.astrbot.app/dev/star/plugin-new.html>
- 最小实例：<https://docs.astrbot.app/en/dev/star/guides/simple.html>
- 处理消息事件：<https://docs.astrbot.app/dev/star/guides/listen-message-event.html>
- 消息的发送：<https://docs.astrbot.app/dev/star/guides/send-message.html>
- 插件配置：<https://docs.astrbot.app/en/dev/star/guides/plugin-config.html>
- 插件 Pages：<https://docs.astrbot.app/en/dev/star/guides/plugin-pages.html>
- 调用 AI：<https://docs.astrbot.app/en/dev/star/guides/ai.html>
- 存储：<https://docs.astrbot.app/en/dev/star/guides/storage.html>
- 会话控制器：<https://docs.astrbot.app/dev/star/guides/session-control.html>
- 杂项（取平台实例 / 调协议端 API）：<https://docs.astrbot.app/dev/star/guides/other.html>
- 插件开发指南（旧，仍可用）：<https://docs.astrbot.app/dev/star/plugin.html>
- OneBot v11 接入：<https://docs.astrbot.app/en/platform/aiocqhttp.html>
- 源码（master）：<https://github.com/AstrBotDevs/AstrBot>（`raw.githubusercontent.com` 本机不可用）
- 源码（PyPI 4.25.2 可读镜像，本报告源码证据主要来自此处）：<https://gitlab.com/gitlab-oss-package-research/source/pypi/as/astrbot-cbc7072d/-/tree/4.25.2/astrbot-4.25.2/astrbot>
- 社区参考实现（世界书 / 角色卡 / 注入 / 数据目录踩坑）：<https://github.com/ysyhlly/astrbot_plugin_worldbook>
- 社区参考实现（persona 管理，`on_llm_request` 追加 system_prompt）：<https://github.com/dafeiwu666/astrbot_plugin_persona_manager>
