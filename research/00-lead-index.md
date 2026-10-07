# 酒馆迁移 AstrBot 插件 · 调研索引（Lead）

工作目录：`E:\astrbot_plugin`
状态：**调研完成（4/4 分报告 + 总报告）**；P0 格式层已开始实现（卡片 + 世界书引擎，27 项离线测试全绿）。
总报告：[`REPORT.md`](REPORT.md)

## 本目录文件

| 文件 | 内容 | 负责 |
|---|---|---|
| `00-lead-index.md` | 索引、环境事实、Lead 直接核对的官方文档结论 | Lead |
| `01-astrbot-plugin-api.md` | AstrBot 插件开发 API 事实清单（事件、发送、LLM、会话、配置、存储） | subagent A |
| `02-existing-plugins-and-market.md` | 插件市场 / GitHub 既有相关插件与竞品分析 | subagent B |
| `03-sillytavern-formats-and-api.md` | 角色卡 V1/V2/V3、世界书算法、聊天格式、ST 服务端 API 可行性 | subagent C |
| `04-prior-art-qq-tavern.md` | QQ/微信/IM 机器人搬酒馆的既有实现与产品形态 | subagent D |
| `REPORT.md` | 汇总调研报告 + 架构选型 + 分阶段开发方案（Lead 最终产出） | Lead |

## 环境事实（已实测）

- 工作目录 `E:\astrbot_plugin` 初始为空；`.acl-recovery` 是修复 Windows 文件权限时产生的备份目录（可保留，最终交付前会清理或加 `.gitignore`）。
- 本机：Python 3.11.9、git 2.53.0、Node 可用；已装 `httpx 0.28.1`、`pillow 12.2.0`、`tiktoken 0.12.0`。
- **未安装 AstrBot，也未部署 SillyTavern / NapCat**（全盘未找到相关目录）→ 端到端联调需要用户提供 AstrBot 运行环境（或允许本机部署一份）。
- 本机 `powershell`/`curl`/`git` 的 HTTPS 出网被安全上下文阻断（`schannel: SEC_E_NO_CREDENTIALS`），无法 `git clone` GitHub；调研一律走 harness 的 `web_search` / `web_fetch`。**这也意味着插件开发阶段不能用 `git clone` 拉源码，需要用户提供网络代理或允许换出网方式。**

## 可用的网络通道（实测，写代码与补证据都用它）

| 通道 | 状态 | 用途 |
|---|---|---|
| harness `web_search` / `web_fetch` | ✅ | 官方文档、GitHub blob 页、搜索结果摘要 |
| `gh.exe`（GitHub API） | ✅ | 仓库元数据、README、contents（base64） |
| `https://gitlab.com/gitlab-oss-package-research/source/pypi/as/astrbot-cbc7072d/-/raw/4.25.2/astrbot-4.25.2/<path>` | ✅ | **AstrBot 4.25.2 真实源码**（源码级核对用） |
| `https://raw.gitcode.com/GitHub_Trending/si/SillyTavern/raw/release/<path>` | ✅（部分 403） | SillyTavern 1.19.0 源码 |
| `https://raw.giteeusercontent.com/mirrors/SillyTavern/raw/release/<path>` | ✅ | SillyTavern 1.18.0 兜底 |
| `docs.sillytavern.app/...md`、`docs.astrbot.app` | ✅ | 文档原文 |
| `pwsh` / `curl` / `git clone` 出网 | ❌ `schannel: SEC_E_NO_CREDENTIALS` | 不要浪费回合 |
| `raw.githubusercontent.com` / `cdn.jsdelivr.net` | ❌ DNS 或 content-type 白名单 | 换上述镜像 |
| `api.hindsight.vectorize.io` | ❌ 401（未配置 API key） | Hindsight 记忆不可用，调研结论落盘到 `research/` |

## Lead 直接核对的官方文档结论（可复用）

来源：<https://docs.astrbot.app/en/dev/star/plugin-new.html>、<https://docs.astrbot.app/en/dev/star/guides/listen-message-event.html>、<https://docs.astrbot.app/en/dev/star/guides/session-control.html>、<https://docs.astrbot.app/en/dev/star/guides/ai.html>

- 插件即 `data/plugins/<name>/` 目录，必须有 `metadata.yaml`（`name/desc/version/author/repo`，可选 `display_name`、`short_desc`、`astrbot_version`、`support_platforms`）；主类继承 `Star`，`__init__(self, context: Context)`；依赖写 `requirements.txt`（pip 安装）。
- 事件：`from astrbot.api.event import filter, AstrMessageEvent`；`@filter.command("x")`、`@filter.command_group("g")`、`@filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE | GROUP_MESSAGE)`、`@filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)`、`@filter.permission_type(filter.PermissionType.ADMIN)`、`@filter.llm_tool(name=...)`；多装饰器为 AND。
- 事件钩子（不能与命令类装饰器混用）：`@filter.on_astrbot_loaded()`、`@filter.on_waiting_llm_request(event)`（锁前，适合发"正在思考"提示，只能 `event.send()` 不能 `yield`）、**`@filter.on_llm_request(event, req: ProviderRequest)`**——这是注入的关键：`req.system_prompt` 是稳定人设/全局规则的推荐位置（官方明确警告不要每轮变动，会破坏 prompt cache、成本上涨 7~20 倍），**每轮变化的中小体量动态提示走 `req.extra_user_content_parts`**。
- 事件字段：`message_str`、`message_obj`（`AstrBotMessage`：`type/self_id/session_id/message_id/group_id/sender/message/message_str/raw_message/timestamp`）、`session_id`、`unified_msg_origin`、`get_sender_id()/get_sender_name()/get_group_id()/is_private_chat()`、`stop_event()`、`send()`、`make_result()`。
- 发消息：`yield event.plain_result(...)`、`event.image_result(...)`（支持 URL/本地路径/base64）、`event.chain_result([...])`；消息段 `astrbot.api.message_components`：`Plain/At/Image/Record/Video/File`，OneBot v11 额外 `Face/Node/Nodes/Poke`（可做合并转发/表情）。
- 调用 LLM：`provider_id = await self.context.get_current_chat_provider_id(umo=event.unified_msg_origin)`，然后 `resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt="...")`，取 `resp.completion_text`；Agent 用 `context.tool_loop_agent(...)`；工具注册 `context.add_llm_tools(...)`（`context.register_llm_tool()` 已废弃）。
- 多轮/长会话：官方推荐 `astrbot.core.utils.session_waiter.session_waiter` + `SessionController`（`keep()/stop()/get_history_chains()`），默认以 `sender_id` 为会话键，可用 `SessionFilter` 改成以群为键 → **正好对应"每个 QQ 用户/每个群一条独立酒馆聊天"**。
- 会话对象 API（`context.conversation_manager`）与 `Conversation` 字段细节见 `01-astrbot-plugin-api.md`（subagent A 核实）。
