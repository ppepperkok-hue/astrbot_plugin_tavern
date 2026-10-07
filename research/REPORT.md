# 酒馆（SillyTavern）功能迁移到 AstrBot · 调研总报告

- 版本：v1（调研阶段产出，2026-10-07 采样）
- 工作目录：`E:\astrbot_plugin`
- 分报告：[`01 AstrBot 插件 API`](01-astrbot-plugin-api.md) · [`02 生态与竞品`](02-existing-plugins-and-market.md) · [`03 ST 格式与 API`](03-sillytavern-formats-and-api.md) · [`04 同类实现`](04-prior-art-qq-tavern.md)
- 证据纪律：本机 `powershell`/`curl`/`git` 的 HTTPS 出网损坏（`schannel: SEC_E_NO_CREDENTIALS`），`raw.githubusercontent` 与 `cdn.jsdelivr` 不可抓；可用通道为 harness 的 `web_search`/`web_fetch`、`gh.exe`（GitHub API）、PyPI GitLab 源码镜像（AstrBot 4.25.2 真实源码）、`raw.gitcode.com`（SillyTavern 1.19.0）与 `raw.giteeusercontent.com`（1.18.0 兜底）。凡未逐字核对的结论在文中标 ⚠️。

---

## 1. 结论先行

1. **这个功能能做，而且必须"迁格式"，不是"迁前端"。** 酒馆的前端（世界书引擎、Prompt Manager、聊天渲染）全在浏览器里跑；release 服务端 `src/world-info.js` 已不存在，服务端只做文件读写 + 上游 provider 转发。所以"调用酒馆"只能拿到**数据源**和**LLM 代理**，Prompt 组装与世界书触发必须由插件实现。
2. **最省事的路线不是自研全功能，也不是 fork 酒馆，而是"自带最小引擎 + 可选外部酒馆后端"。** 数据层可切换本地 JSON（默认）或 ST HTTP API（用户已部署酒馆时），生成层可切换 AstrBot provider（默认）或 ST `/api/backends/chat-completions/generate`。
3. **纯 Python 生态里没有可直接 pip 安装的 ST 世界书/角色卡引擎**——这是真实空位，也是本插件存在的理由；已有实现要么绑死某个 bot 框架，要么是空壳，要么许可不可用。
4. **AstrBot 生态已有 1~2 个功能高度重合的活跃竞品**（`komeiji_tavern`、`quillplus`），但它们都很大（单文件上百 KB 级）、都自带整套 prompt/记忆/面板体系。我们的差异化定位是：**小而准的 ST 兼容层**（格式 1:1、引擎纯净、可被别的插件当库用）。
5. **几个硬约束先说清楚**：运行数据必须放 `data/plugin_data/<plugin>/`（从 WebUI 更新插件会整目录替换插件目录，社区已翻车）；世界书注入走 `@filter.on_llm_request` 的 `req.extra_user_content_parts`，绝不要每轮改 `system_prompt`（破坏 prompt cache，成本 +7~20 倍）；QQ 侧纯文本发送会被 `strip()`，酒馆式多行输出需要零宽空格保护；日志必须用 `from astrbot.api import logger`。

---

## 2. 路线对比与推荐

| 路线 | 做法 | 优点 | 代价 | 结论 |
|---|---|---|---|---|
| A. 薄桥接 | 插件只做 QQ↔ST 会话映射 + 渲染，引擎全调外部酒馆 | 插件最小 | 需要 ST 服务端插件（`st-external-bridge` 许可矛盾 ⚠️ / `SillyTavern-Extension-ChatBridge` 48★ WebSocket 扩展）；链路 3→5 跳；仍要自己做历史裁剪与触发 | 只作为**可选后端** |
| B. fork/PR 竞品 | 以 `KomeijiDono/astrbot_plugin_komeiji_tavern`（AGPL-3.0，v0.8.2）为上游 | 功能最全、最快出效果 | 贡献进 AGPL 仓库；体型与设计受制于上游 | 备选（若要最快可用） |
| C. clean-room 自研全功能 | 照 ST 语义重写全部 | 完全可控、许可干净 | 28~40 人日；重复造轮子 | 不推荐 |
| **D. 混合（推荐）** | **自带最小 WI 引擎 + 卡片/世界书/聊天格式层；数据与生成两端都做成可插拔 Backend（本地 / 外部酒馆）** | 插件小、离线可用、有酒馆时又"调用"它；纯 MIT 可控 | 需要自己实现约 14~20 人日的最小集（其中格式层已开始实现） | **建议主线** |

推荐 D 的理由一句话：它同时满足"插件尽量小"和"能调用酒馆就调用"两个要求，而且把"调用酒馆"降级成可选加速项，而不是唯一命脉。

---

## 3. 关键事实（动手前必读）

### 3.1 ST 数据格式（照抄级）

- **角色卡**：V1 六字段；V2 `spec: chara_card_v2` + `data` 下 14 个必填字段；V3 新增 `assets[]/nickname/creator_notes_multilingual/source[]/group_only_greetings/creation_date/modification_date`。PNG **只用 `tEXt`**，keyword `chara`(V2) / `ccv3`(V3)，值是 base64(UTF-8 JSON)，读取时 `ccv3` 优先；ST 自己不读 `iTXt`（兼容层建议写 `tEXt`、读取两者都认）。
- **世界书位置枚举是数字**：`before:0 after:1 ANTop:2 ANBottom:3 atDepth:4 EMTop:5 EMBottom:6 outlet:7`。
- **selectiveLogic 数值与 UI 顺序不同**：`AND_ANY:0 NOT_ALL:1 NOT_ANY:2 AND_ALL:3`。
- **世界书默认值**：`world_info_depth=2`、budget `25%`、`include_names=true`、`matchWholeWords=false`、`max_recursion_steps=0`、`DEFAULT_DEPTH=4`、`DEFAULT_WEIGHT=100`、`MAX_SCAN_DEPTH=1000`。中文场景建议默认关闭 `matchWholeWords`（`\W` 边界对无空格语言有害）。
- **字段映射**在 `src/endpoints/characters.js` 的 `convertWorldInfoToCharacterBook()`：ST 驼峰 ↔ V2 lorebook 下划线（`key↔keys`、`keysecondary↔secondary_keys`、`order↔insertion_order`、`disable↔enabled`(取反)、`position:0↔"before_char"`）。
- **Prompt 默认顺序**（`Default.json`，character_id 100000/100001）：`main → worldInfoBefore → (personaDescription，仅 100001) → charDescription → charPersonality → scenario → enhanceDefinitions(false) → nsfw → worldInfoAfter → dialogueExamples → chatHistory → jailbreak(PHI)`；in-chat 注入按 role 分组固定顺序 `User→Assistant→System`，同组内按 `Order` 升序。**不要把这份顺序硬编码**，启动时从外部酒馆 `/api/presets` 或本地预设读取。
- **聊天记录**：`data/<user>/chats/<cardName>/<file>.jsonl`，**第一行是 header** `{user_name, character_name, chat_metadata}`，`chat_items = 行数-1`；消息行 `{name, is_user, is_system?, send_date, mes, extra}`；`chat_metadata.timedWorldInfo = {hash, start, end, protected}`，key 为 `<world>.<uid>`，即 sticky/cooldown 的持久化位置。群聊在 `data/<user>/group chats/<id>.jsonl`，配合 `groups/<id>.json`。

### 3.2 ST 服务端 API 能做/不能做

- **能做**：数据源（`/api/characters/{all,get,chats}`、`/api/worldinfo/{list,get}`、`/api/chats/{get,save,rename,export,recent,group/*}`）与带鉴权的 LLM 代理（`POST /api/backends/chat-completions/generate`，配 `reverse_proxy` 可指向任意 OpenAI 兼容端点）。
- **不能做**：不存在"给 prompt 出回复"的 `/api/chat/completions`。`/generate` 的入参是**已经组装好的 `messages[]`**，服务端只做 provider 转换与转发，**不读 characters/worlds/chats 任何文件**。世界书触发、prompt 组装、历史裁剪、宏替换全部要插件自己做。
- **鉴权成本**：除 `/api/users` 外所有 `/api/*` 都在 `requireLoginMiddleware` 之后（cookie session）；csrf-sync 对非 GET 全量生效，token 走 `GET /csrf-token`、请求头 `x-csrf-token`；`--disableCsrf` 可用但要打安全警告；basicAuth 仅 `listen:true` + `basicAuthMode:true` 时挂在 app 级。**这也是"读接口也是 POST"的原因。**

### 3.3 AstrBot 侧接口（已源码级核对 4.25.2）

- **数据目录**：`StarTools.get_data_dir("astrbot_plugin_tavern")` → `data/plugin_data/<plugin>/`，自动 mkdir 并 resolve。**插件目录内的数据会在面板更新时被整目录替换清空。**
- **注入入口**：`@filter.on_llm_request(event, req)` 在 `build_main_agent` 之后、真正调用前触发，改 `req` 立即生效；`req.extra_user_content_parts.append(TextPart(text=...))` 放每轮动态内容（该 part 落在**本轮 user 文本之后**、图片之前），`TextPart(...).mark_as_temp()`（≥4.24.0）可只参与本轮不落库。**该钩子返回真值会直接跳过本次默认 LLM 调用。**
- **接管普通聊天**：`@filter.event_message_type(filter.EventMessageType.ALL)` + `event.should_call_llm(False)`（比"自己发过消息"更可靠）。
- **会话键**：`event.unified_msg_origin` = `{platform_id}:{message_type.value}:{session_id}`，例如 `aiocqhttp:GroupMessage:123456789`，直接作为"每个 QQ 用户/群一条酒馆分支"的键。
- **自管历史的调用方式**：`context.llm_generate(chat_provider_id=..., prompt=..., contexts=[...], system_prompt=...)`（keyword-only；另有 `image_urls/audio_urls/tools/**kwargs`）。此时 AstrBot 的 `Conversation.history` 用不上——注意 v4.25 起它是 **JSON 字符串**，且表里**没有自定义字段列**。
- **优先级**：`@filter.*` 的 `priority` 默认 0，**数值越大越先执行**。
- **其他**：`metadata.yaml` 的 `astrbot_version` 用 PEP 440（建议 `>=4.17,<5`）；插件市场 zip ≤ 16MB；`_conf_schema.json` 支持 `string/text/int/float/bool/object/list/dict/template_list/file`，`_special: select_persona` 可直接让用户挑 AstrBot 人设。
- **发送端坑**：aiocqhttp 发送 `Plain` 会被 `strip()`，酒馆式多行输出要保护首尾空行；合并转发用 `Nodes`/`Node`；表情用 `Face`。
- **日志**：必须 `from astrbot.api import logger`，禁止 `import logging`（市场审查项）。

### 3.4 生态与许可（决定能不能抄）

- 高度重合的竞品：`KomeijiDono/astrbot_plugin_komeiji_tavern`（AGPL-3.0，含 `importers.py/lore.py/prompt_builder.py/storage.py/web/`）、`Nana7mi0721/astrbot_plugin_quillplus`（含 `persona_manager.py`、状态栏降级链）、`Zhalslar/astrbot_plugin_worldbook`（GPL-3.0，57★，仍在提交）、`Raven95676/astrbot_plugin_lorebook_lite`（AGPL-3.0，YAML 世界书 + 占位符引擎）、`ysyhlly/astrbot_plugin_worldbook`（无许可 ⚠️，但匹配器处理了 `entries` 为 map / `selective:false` / 只有 `keysecondary` 三个坑）。
- 题目点名的 `LKarxa/astrbot_plugin_SillyTavern_card`：11★、**无 LICENSE**、2025-04 停更，只做 PNG→文本 + lorebook_lite YAML 的单向转换，且 `metadata.yaml` 的插件名少一个 `a` 与目录不一致。**代码一行不能抄**，只能作为"生态缺口"的证据。
- clean-room 参考蓝本：`qiuqiu-2/CharaRelay`（Apache-2.0，V2/V3 + CharX 保真、递归/概率/包含组/预算/timed effects/decorators/宏/Outlet）。
- "调用外部酒馆"的桥（都只能当可选后端）：`AyeeMinerva/SillyTavern-Extension-ChatBridge`（48★，WebSocket 扩展，支持历史同步与流式）、`Youzini-afk/st-external-bridge`（`POST /v2/generate`，⚠️ README 写 MIT 但 API 检出 AGPL-3.0）。
- 明确的"查无此物"：`sillynome`、`st-api-proxy` 均不存在（近似的是 `Lianues/st-api-wrapper`）；NoneBot2/Koishi/go-cqhttp 生态没有酒馆插件。文档与 README 里不要引用这两个名字。

---

## 4. 插件设计

### 4.1 架构

```
QQ/微信/NapCat ──> AstrBot 平台适配器 ──> 本插件
                                          │
        ┌─────────────────────────────────┼──────────────────────────────┐
        │ 触发门控（私聊直回 / 群聊 @+唤醒词 / 冷却 / 并发 1）              │
        │ 会话键：unified_msg_origin（+ 可选角色 id）                     │
        │ 历史：自管 JSONL（ST 兼容）                                     │
        │ 装配：卡片 → 预设顺序 → 世界书命中 → 历史裁剪 → token 预算       │
        │ 生成：Backend（AstrBot provider | ST /generate）                │
        │ 渲染：正则清洗 → 分段/节流 → 纯文本降级 → Face/合并转发          │
        └───────────────────────────────────────────────────────────────┘
```

三条铁律：**格式层不 import astrbot**（可单测、可被复用）；**Backend 从第一天就抽象**；**数据只写 `data/plugin_data/`**。

### 4.2 目录与模块

| 路径 | 内容 | 状态 |
|---|---|---|
| `astrbot_plugin_tavern/st/cards.py` | 角色卡 V1/V2/V3 解析、PNG `tEXt` 读写、导出 V2 | ✅ 已实现 + 测试 |
| `astrbot_plugin_tavern/st/worldbook.py` | 世界书解析 + 激活引擎（关键词/正则/整词/selective/scan_depth/order/position/概率/递归/sticky-cooldown-delay/token 预算/分组） | ✅ 已实现 + 测试 |
| `astrbot_plugin_tavern/st/prompt.py` | 预设块顺序 + 历史裁剪 + token 预算 + 宏替换（`{{char}}`/`{{user}}`/`{{time}}`…） | ⏳ 下一步 |
| `astrbot_plugin_tavern/st/chat_store.py` | ST 兼容 JSONL（header + 消息行 + `chat_metadata`）读写、分支/重开/回退 | ⏳ 下一步 |
| `astrbot_plugin_tavern/backends/` | `base.py` + `astrbot_provider.py` + `st_http.py`（外部酒馆，可选） | ⏳ 下一步 |
| `astrbot_plugin_tavern/main.py` | Star 主类、指令、`on_llm_request` 注入、分段发送 | ⏳ 依赖上表 |
| `metadata.yaml` / `_conf_schema.json` / `requirements.txt` | 插件元数据与配置 | ⏳ 上架前 |
| `tests/` + `tools/make_fixtures.py` | 离线测试与 ST 格式夹具（可复现生成） | ✅ 27 项全绿 |

### 4.3 分阶段计划

- **P0 格式层（进行中，已完成 2/4）**：卡片解析、世界书引擎 ✅；预设装配、ST 兼容聊天存储 ⏳。验收：`pytest tests -q` 全绿。
- **P1 最小可用插件（MVP）**：指令面（`导入卡片/换卡/新开/回退/世界书开关/预览/重载`）+ `on_llm_request` 注入 + AstrBot provider 生成 + 分段发送 + 防刷屏。验收：离线单测 + 用户 AstrBot 实机跑通一轮群聊与私聊。
- **P2 体验补齐**：正则清洗、状态栏剥离、宏、多角色/群聊多卡、token 预算与上下文裁剪策略、`/酒馆 预览` 调试面板。
- **P3 可选外部酒馆后端**：`st_http.py`（cookie+`x-csrf-token` 鉴权）、卡片/世界书/聊天同步、生成代理切换。
- **P4 上架准备**：README（注明 ST 为灵感来源与链接）、`.gitignore`、ruff、metadata/`_conf_schema` 完善、16MB 打包检查。

### 4.4 工作量（1 人日 = 1 熟练开发）

- 纯格式兼容层 MVP：**14~20 人日**（卡片 1.5~2.5 / 世界书最小引擎 4~6 / 组装 3~4 / 预算 1.5~2 / 聊天 1.5~2 / NapCat 适配 3~5）。
- 混合方案（数据 + 生成走酒馆 API）：13~18 人日。
- 完整兼容（timed effects、包含组、outlet、递归分级、群聊）：28~40 人日。

---

## 5. 待确认（动手前逐项验）

1. `build_main_agent` 内 persona / 规则 / 工具 / history 的确切拼接顺序（影响注入位置）。
2. `call_event_hook` 多钩子返回值的聚合规则（影响 `on_llm_request` 能否安全返回真值）。
3. `extra_user_content_parts` 在各 provider 适配层是否总是保留。
4. `StarTools.get_data_dir()` 在 `__init__` 之前是否可用；Docker 下 `data` 卷路径。
5. `mark_as_temp()` 的实现落点与版本边界。
6. ST 侧 `checkWorldInfo`/`getSortedEntries` 函数体（265 KB 的 `world-info.js` 中段未取到）与 `newWorldInfoEntryDefinition` 默认值全表。
7. SPEC_V3.md / spec_v2.md 全文（需在有网机器上用 `api.github.com` blob base64 解码）。
8. QQ 侧真实限额：消息长度、合并转发 `Nodes` 上限、发消息频率（需实测）。
9. `st-external-bridge` 的 LICENSE 与 `package.json` 矛盾待人工核实。
10. 插件市场实况（下载量、分类、同名冲突）需能联网的机器拉 `plugins.json` 全量 grep。

---

## 6. 需要你拍板的两件事

1. **路线**：是否采纳 **D（自带最小引擎 + 可选外部酒馆后端）**？还是先要 B（给 `komeiji_tavern` 提 PR，最快出效果但进 AGPL 仓库）？
2. **联调环境**：本机没有 AstrBot、没有 NapCat、也没有酒馆。端到端验证需要你给一台环境（或允许我在本机装 AstrBot 并配 NapCat 反向 WS），否则我只能做到"离线单测全绿 + 你那边实机试跑"。
