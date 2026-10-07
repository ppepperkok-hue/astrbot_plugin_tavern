# 02 · 既有插件与插件市场调研（生态与竞品）

- 调研员：subagent B（生态与竞品方向）
- 数据采样时间：**2026-10-07 UTC**（GitHub REST API `api.github.com` 实测，star/时间/许可以接口返回为准）
- 方法与局限：本机 HTTPS 出网损坏，未使用 `curl`/`git clone`；所有结论来自 harness 的 `web_search` / `web_fetch`、GitHub REST API、`gh-proxy.com` 转发的 `raw.githubusercontent.com`、以及 `research/_raw/` 中 Lead 已缓存的仓库元数据与 README（base64）。
- 本文所有 "star 数/最近提交" 都是**采样时刻值**，会变；标 ⚠️ 的条目未完全核实，请勿直接当结论用。

---

## 1. 结论与建议（先看这一节）

### 1.1 一句话结论

**这个赛道已经有人做了，而且做得比"最小插件"大得多。** AstrBot 生态里已存在一个功能几乎完全覆盖我们目标的一体化角色扮演插件 [`astrbot_plugin_komeiji_tavern`](https://github.com/KomeijiDono/astrbot_plugin_komeiji_tavern)（AGPL-3.0，v0.8.2，活跃），以及一个**仍在维护、57★ 的世界书注入插件** [`Zhalslar/astrbot_plugin_worldbook`](https://github.com/Zhalslar/astrbot_plugin_worldbook)、一个 YAML 世界书运行时 [`astrbot_plugin_lorebook_lite`](https://github.com/Raven95676/astrbot_plugin_lorebook_lite)。而 AstrBot 官方开发原则明确写着：**"如果是对某个插件进行功能扩增，请优先给那个插件提交 PR，而不是单独再写一个插件（除非原插件作者已经停止维护）。"**

因此我不建议"从零造一个全功能酒馆插件"。建议按下面 1.2 的三选一，首选 **路线 B + A 组合**。

### 1.2 路线对比与推荐

| 路线 | 做法 | 许可后果 | 工作量 | 评价 |
|---|---|---|---|---|
| **A. 薄桥接插件** | 新建 `astrbot_plugin_*`，只做：QQ 会话↔酒馆会话映射、消息/图片渲染、ST 卡与世界书的**读取与展示**；真正的 RP 后端调用**已部署的 SillyTavern**（经 ST 服务端插件暴露 REST） | 自选（MIT 可行），只要不抄 AGPL/GPL 代码 | 小 | **推荐作为降级/加成路径**：契合"插件要尽量小、优先调用外部酒馆服务"的原始诉求，但强依赖用户已部署 ST + 服务端插件 |
| **B. 给既有插件提 PR / 以上游为底座** | 以 `astrbot_plugin_komeiji_tavern` 为上游，向它提 PR（补 ST 资产导入增强、外部 ST 桥接、NapCat 表情/合并转发、世界书 V3 语义）；或直接把自己的实现做成它的模块 | 贡献代码进入 AGPL-3.0 仓库 → **我们这部分变成 AGPL** | 中 | **首选**：符合官方原则、不重复造轮子、对方有 WebUI/存储/测试基建 |
| **C. 完全自研全功能插件** | clean-room 重写角色卡/世界书/Prompt 编排/存储/面板 | 自选许可（保持 clean-room） | 大（komeiji 的 `service.py` 单文件就 137 KB） | 只在"必须自有品牌/自有许可"或上游不接受 PR 时才做 |

补充：**LKarxa 那个插件不适合作为上游**（理由见 1.4），但它证明了两件事——(1) AstrBot 生态确实缺"ST 资产→AstrBot"的桥；(2) 光做"转换器"价值有限（11★ 后 17 个月零提交）。

### 1.3 可以直接复用的"轮子"

| 轮子 | 用途 | 许可 | 怎么用（合规） |
|---|---|---|---|
| SillyTavern 角色卡 V2/V3 规范（PNG `tEXt` 块 `chara`/`ccv3` + base64(JSON)，JSON V2 `spec: chara_card_v2`、V3 `chara_card_v3`） | 卡解析 | 事实标准/公开规范 | **自己按规范写解析器**（~50 行），不要抄 ST 或 LKarxa 的代码 |
| `pypng` / Pillow | PNG chunk 读写 | MIT / MIT-CMU | 写进 `requirements.txt`。注意：**Pillow 默认会丢弃未知 chunk**，要保真读写 `tEXt`/`iTXt` 顺序就用 `pypng` 或手工 chunk 拼接 |
| [`astrbot_plugin_lorebook_lite`](https://github.com/Raven95676/astrbot_plugin_lorebook_lite) | 世界书**运行时**（YAML：trigger/world_state/user_state/占位符/变量/骰子/存档） | AGPL-3.0 | **只把它当"外部依赖插件"，产出它认的 YAML**（格式是接口，不是代码 → 无传染）。要 import 它的代码才需 AGPL |
| [`astrbot_plugin_worldbook`](https://github.com/Zhalslar/astrbot_plugin_worldbook)（Zhalslar，57★） | 关键词/正则触发 `system_prompt` 注入、导入导出、`{}` 通配符 | **GPL-3.0** | 同上看接口。它的数据模型（name/enabled/priority/scope/keywords/cron/duration/times/probability/content）可以直接**借鉴为我们的字段设计**（字段设计=思想） |
| [`Youzini-afk/st-external-bridge`](https://github.com/Youzini-afk/st-external-bridge) | 把已部署 ST 暴露为 REST/SSE：`POST /v2/generate`、`/v2/generate/batch`、`GET /v2/characters|presets|worldbooks`、`/v2/proxy/*` | ⚠️ README 写 MIT，但 GitHub API 检出 **AGPL-3.0**（仓库内含 AGPL 文件）→ **许可需人工核实** | 只通过 HTTP 调用（不改不抄），风险最低；建议在 README 里声明"可选后端依赖" |
| [`Lianues/st-api-wrapper`](https://github.com/Lianues/st-api-wrapper) | ST **服务端插件框架**，封装 hooks 与 `prompt_buildRequest`（含 System Prompt、WIAN、采样参数、模板、正则） | ⚠️ **无 LICENSE** | 它由 st-external-bridge 依赖，我们不直接依赖；**无许可证 = 默认保留全部权利，不可复制其代码** |
| [`qiuqiu-2/CharaRelay`](https://github.com/qiuqiu-2/CharaRelay) | clean-room 的 ST 语义**参考实现**：V2/V3 卡、PNG、CharX 容器、世界书（关键词/正则/递归/概率/包含组/预算/timed effects/V3 decorators/宏/Outlet）、Author's Note、Persona、SQLite 历史、审计 trace | Apache-2.0 | **最佳"照着实现"的蓝本**（Apache 兼容性好）。注意它自述 clean-room、与 ST 无关联，我们照它也要写明灵感来源 |
| `tiktoken`（本机已装 0.12.0） | 上下文 token 预算 | MIT | 直接依赖；AstrBot 本体也有上下文压缩能力可配合 |
| AstrBot 官方插件 API | `@filter.on_llm_request(event, req)` 注入（稳定人设走 `req.system_prompt`，每轮变化的动态内容走 `req.extra_user_content_parts`）、`SessionController` 做多轮、`context.llm_generate()` | AstrBot 本体 **AGPL-3.0**（41499★，pushed 2026-10-06） | 用官方 API 写插件是正常使用，不构成代码移植 |
| [`aikohanasaki/SillyTavern-MemoryBooks`](https://github.com/aikohanasaki/SillyTavern-MemoryBooks)（315★）、[`SenriYuki/SillyTavern-Horae`](https://github.com/SenriYuki/SillyTavern-Horae)（195★） | "把聊天记忆写成 lorebook 条目"的产品设计参考 | AGPL / 无 | 只借鉴产品形态 |

### 1.4 关于 `LKarxa/astrbot_plugin_SillyTavern_card`：不建议在其上做全功能 PR

| 维度 | 实测值 | 判断 |
|---|---|---|
| Star / Fork / Issue | 11★ / 1 fork / 0 open issue / 0 PR | 有少量关注，无社区协作痕迹 |
| 时间线 | created `2025-04-24`，**最后一次 push `2025-04-26`**（即两天后停更，至今约 **17.5 个月**） | 按 AstrBot 官方原则（"除非原插件作者已经停止维护"）**已满足另起炉灶的条件** |
| 许可 | `license: null`（**无 LICENSE 文件**） | **默认 "All rights reserved"**，抄它的代码/文件有法律风险；只能借鉴思路 |
| 定位 | 单向转换器：PNG 卡 → `name/prompt/first_mes` 文本 + Lorebook YAML | 与"运行时 RP"完全不是一个量级 |
| 硬 Bug | `metadata.yaml` 的 `name: strbot_plugin_SillyTavern_card`（少了 `a`），`@register(...)` 里也是同一个错名，而仓库/目录名是 `astrbot_plugin_SillyTavern_card` | 违反市场规范中 `plugin_id = author/name` 与目录/包身份一致性要求，**市场身份会错位** |

**建议**：可以发一个**极小 bugfix PR**（只改 `metadata.yaml` 的 `name` + `@register` 名 + 补 `astrbot_version`）来留下善意与记录，但**不要把我们的产品建在它上面**；若想复用它的"PNG 卡解析"能力，**自己重写**（规范公开，重写成本 < 半天）。

### 1.5 明确的"不要做"

- 不要把 SillyTavern 的 `world-info.js` / `character-card-parser` / `openai.js` 等源码**翻译或移植**进插件。ST 是 **AGPL-3.0**（34163★，pushed 2026-10-02）——AGPL 会传染，且触发网络服务条款。
- 不要复制任何 `license: null` 仓库的代码（LKarxa 插件、`horizoe10/astrbot_plugin_tavern`、`st-api-wrapper`、`ysyhlly/astrbot_plugin_worldbook`、`SenriYuki/SillyTavern-Horae` 等）。
- 不要分发他人的角色卡 PNG/世界书/立绘素材（那些是第三方同人作品，另有版权）。插件只做**解析器/导入器**，素材由用户自带。
- 不要在插件名/描述里暗示与 SillyTavern、TavernAI 官方有关联。

---

## 2. 逐项目详表

### 2.1 AstrBot 生态：与本目标直接相关的插件

全部为 2026-10-07 采样值。**"在市场上？"列仅当 README 明确写了"在插件市场搜索安装"才标 ✅，其余为 ⚠️ 未核实。**

| 仓库 | ★ | 许可 | 创建 → 最近 push | 做了什么 | 在市场上？ |
|---|---|---|---|---|---|
| [KomeijiDono/astrbot_plugin_komeiji_tavern](https://github.com/KomeijiDono/astrbot_plugin_komeiji_tavern) | 2 | AGPL-3.0 | 2026-06-19 → 2026-09-06 | **功能最全的竞品**：可排序 Prompt Manager、世界书扫描（主/次关键词、正则、递归、概率、Sticky/Cooldown/Delay/Outlet/深度注入）、混合检索（FTS5/向量）、素材库、长期记忆治理、多世界战役、状态审核、分支树归档、自动摘要、完整调试器、WebUI 面板、QQ 长回复分片/合并转发、`/tavern` 系列命令 | ✅ |
| [Zhalslar/astrbot_plugin_worldbook](https://github.com/Zhalslar/astrbot_plugin_worldbook) | **57** | GPL-3.0 | 2026-01-22 → **2026-10-07（今天仍在提交）** | 正则/关键词触发的 `system_prompt` 注入；条目字段 name/enabled/priority/scope/keywords/cron/duration/times/probability/content；导入导出 JSON/YAML；内置示例世界书；中文命令（`条目状态`/`添加条目`/`导出世界书`…） | ✅ |
| [Raven95676/astrbot_plugin_lorebook_lite](https://github.com/Raven95676/astrbot_plugin_lorebook_lite) | 20 | AGPL-3.0 | 2025-04-01 → 2026-03-31 | YAML 世界书**运行时**：`world_state`/`user_state`/`trigger`（regex/keywords/listener，`&` 与 `~` 逻辑、priority/block/probability/position/actions）、`authors_note`、`{ns::fn(args)}` 占位符（时间/骰子/变量/逻辑/存档，25 层嵌套），按会话与人格隔离 | ⚠️ |
| [Nana7mi0721/astrbot_plugin_quillplus](https://github.com/Nana7mi0721/astrbot_plugin_quillplus) | 4 | AGPL-3.0 | 2026-07-04 → 2026-09-30 | 羽笔世界书 + 素材库注入；GitHub topics 直接挂了 `sillytavern`/`character-card`/`lorebook`/`worldbook`/`long-term-memory`/`roleplay` | ⚠️ |
| [ysyhlly/astrbot_plugin_worldbook](https://github.com/ysyhlly/astrbot_plugin_worldbook) | 1 | ⚠️ 无 | 2026-09-21 → 2026-09-21（当天） | **与我们要做的最像**：导入世界书（本插件 JSON / **SillyTavern 风格 `entries`** / 文本块）、导入角色卡、常驻/关键词/场景绑定条目、自动场景切换、`on_llm_request` 注入（`inject_mode=both`：卡进 system，世界书进 system+extra）、角色卡提示词去重、可视化管理面板（插件页内）、`/世界书` 系列命令 | ⚠️ |
| [horizoe10/astrbot_plugin_tavern](https://github.com/horizoe10/astrbot_plugin_tavern) | 8 | ⚠️ 无 | 2026-07-30 → 2026-08-24 | "面向群聊的多人与世界运行平台"：私聊建卡、AI/真人主持、模块化世界、状态推进、结局与归档；`is_template=true`（把仓库标成了 template） | ⚠️ |
| [Ortfine/astrbot_plugin_trpg](https://github.com/Ortfine/astrbot_plugin_trpg) | 1 | MIT | 2026-08-27 → 同日 | "在私聊里实现类 SillyTavern 的轻量角色扮演"：对话式/粘贴式建卡（AI 整理字段）、6 个预设模板、采访式建卡改卡、AI/玩家/叙事者三种玩法模式、主动性模式、状态栏、动态世界书、剧情总结、自动家访 | ⚠️ |
| [Liuxd-1230/astrbot_plugin_hdsi](https://github.com/Liuxd-1230/astrbot_plugin_hdsi) | 1 | AGPL-3.0 | 2026-08-27 → 同日 | Koishi 版 HDS-Interlude 的 AstrBot 移植：**持续叙事运行时**（多角色注册表、独立 Canon/世界线、Facts/Consequences/Overlay/Alter/Agency Window、延迟回复重裁决、权威会话路由） | ⚠️ |
| [dafeiwu666/astrbot_plugin_persona_manager](https://github.com/dafeiwu666/astrbot_plugin_persona_manager) | 4 | MIT | 2026-01-13 → 2026-04-28 | 卡片式人设管理：多卡切换、群/私聊分别维护、关键词触发临时人设、`on_llm_request` 追加 `system_prompt`（不覆盖其他插件）、注入前后缀、正则清洗、CozyNook 社区卡小屋 | ⚠️ |
| [yussica1016/astrbot_plugin_roleplay](https://github.com/yussica1016/astrbot_plugin_roleplay) | 1 | AGPL-3.0 | 2026-04-27 → 2026-04-28 | 随机生成场景卡（8 世界观 / 78 场景 / 108 身份 / 15 关系 / 10 开局 / 12 突发事件），`角色设定`/`角色扮演`/`推进`/`换场景`/`存档` 命令，纯文本驱动 | ⚠️ |
| [math89423-star/moon-qqbot](https://github.com/math89423-star/moon-qqbot) | 2 | MIT | 2026-07-01 → 2026-07-03 | AstrBot v4.26 + NapCatQQ 的"完整深度人格"RP QQ 机器人（八重温度人格/记忆/情感追踪）；更像**独立 bot 项目**而非插件 | — |
| [ASA-max-afk/astrbot_plugin_sillytavern](https://github.com/ASA-max-afk/astrbot_plugin_sillytavern) | 1 | AGPL-3.0 | 2026-07-18 → 同日 | 描述写"可以像酒馆一样直接导入角色卡"，但 **README 仍是 AstrBot helloworld 模板原文**（`# astrbot-plugin-helloworld` + "This repo is just a template"）→ ⚠️ 疑似空壳/未完成 | — |
| [SGSxingchen/astrbot_plugin_discord_tavern](https://github.com/SGSxingchen/astrbot_plugin_discord_tavern) | 1 | ⚠️ 无 | 2026-05-02 → 同日 | "Discord-native lightweight RP runtime with SillyTavern asset compatibility"（一天产物，未核实内容） | — |
| [KyonQi/astrbot_plugin_tavern_connector](https://github.com/KyonQi/astrbot_plugin_tavern_connector) | 0 | ⚠️ 无 | 2026-05-04 → 同日 | 描述 "A Astrbot Plugin for Silly Tavern"，**仓库 size=0（空）** | — |
| [o2e/astrbot_plugin_tavern_dispatcher](https://github.com/o2e/astrbot_plugin_tavern_dispatcher) | 0 | AGPL-3.0 | 2026-06-24 → 同日 | 无 README 描述，size 15 KB | — |
| [nekodeath9527/astrbot_plugin_silly_astr_tavern](https://github.com/nekodeath9527/astrbot_plugin_silly_astr_tavern) | 0 | AGPL-3.0 | 2026-08-26 → 同日 | 无描述，size 15 KB | — |
| [XZZKANY/astrbot_plugin_SillyTavern_card](https://github.com/XZZKANY/astrbot_plugin_SillyTavern_card) | 0 | MIT | 2026-04-29 → 2026-05-08 | 与 LKarxa 同名（疑似重写/fork 改名），语言 JavaScript | — |
| [chenming0v0/astrbot_plugin_thunder_lorebook_pro_max_plus_ultra](https://github.com/chenming0v0/astrbot_plugin_thunder_lorebook_pro_max_plus_ultra) | 1 | ⚠️ 无 | 2026-08-22 → 2026-08-23 | 语言 HTML，疑似带前端面板的世界书插件 | — |
| [anrrow/astrbot-plugin-lorebook](https://github.com/anrrow/astrbot-plugin-lorebook) | 0 | ⚠️ 无 | 2026-05-28 → 2026-06-12 | "仿照世界书" | — |
| [EmilyCheoh/astrbot_plugin_lorebook](https://github.com/EmilyCheoh/astrbot_plugin_lorebook) | 0 | ⚠️ 无 | 2026-04-10 → 2026-09-02 | 无描述 | — |
| [monieckbo-svg/astrbot_plugin_astra_lorebook](https://github.com/monieckbo-svg/astrbot_plugin_astra_lorebook) | 0 | ⚠️ 无 | 2026-07-06 → 同日 | 关键词 + 纪念日触发 | — |
| [kumavulp/astrbot-plugins](https://github.com/kumavulp/astrbot-plugins) | 0 | ⚠️ 无 | 2026-06-24 → 2026-07-11 | 插件集合仓（lorebook / proactive / qzone / gpt-image / poke） | — |

**竞品小结**：功能最深的是 **komeiji_tavern**（编排 + 世界书 + 记忆 + 战役 + 面板 + 调试器，已经到 v0.8.2）；最像"我们要做的那个最小插件"的是 **ysyhlly/worldbook**（导入 ST `entries` + 角色卡 + 面板，但只活了一天、无许可、1★）；生态里**最稳的公共设施**是 **Zhalslar/worldbook（57★，今天还在提交）** 和 **lorebook_lite（20★）**。

### 2.2 AstrBot 生态：记忆 / 人格 / 群聊人格（可复用或需避让）

| 仓库 | ★ | 许可 | 最近 push | 定位 |
|---|---|---|---|---|
| [NickCharlie/astrbot_plugin_self_learning](https://github.com/NickCharlie/astrbot_plugin_self_learning) | **412** | AGPL-3.0 | 2026-09-30 | 学习对话风格/群黑话/社交关系与好感度/人格演化 |
| [kawayiYokami/astrbot_plugin_angel_memory](https://github.com/kawayiYokami/astrbot_plugin_angel_memory) | 187 | ⚠️ NOASSERTION | 2026-09-15 | 长期记忆 + 主动思考 |
| [zhanzhao2/astrbot-plugin-persistent-memory](https://github.com/zhanzhao2/astrbot-plugin-persistent-memory) | 87 | MIT | 2026-03-07 | LanceDB 持久化记忆 + 混合检索 |
| [Renyus/astrbot_plugin_self_evolution](https://github.com/Renyus/astrbot_plugin_self_evolution) | 80 | ⚠️ NOASSERTION | 2026-05-06 | persona evolution + long-term memory |
| [menglimi/astrbot_plugin_memory_companion](https://github.com/menglimi/astrbot_plugin_memory_companion) | 51 | ⚠️ 无 | 2026-09-18 | 拟人记忆调度 |
| [leafliber/astrbot_plugin_iris_chat_memory](https://github.com/leafliber/astrbot_plugin_iris_chat_memory) | 19 | AGPL-3.0 | — | 聊天记忆 |
| [Railgun19457/astrbot_plugin_persona_plus](https://github.com/Railgun19457/astrbot_plugin_persona_plus) | 17 | AGPL-3.0 | 2026-09-01 | AstrBot 人格设定管理 |
| [vivy1024/astrbot_plugin_wave_memory](https://github.com/vivy1024/astrbot_plugin_wave_memory) | 17 | AGPL-3.0 | 2026-09-28 | 记忆 |
| [wangkant/personagent](https://github.com/wangkant/personagent) | 17 | MIT | 2026-10-03 | 群聊人格（知道何时闭嘴 + 证据账本 + 回滚 + 评测），走 AstrBot/Koishi/Matrix |
| [piexian/astrbot_plugin_simple_long_memory](https://github.com/piexian/astrbot_plugin_simple_long_memory) | 15 | AGPL-3.0 | 2026-09-15 | 基于内置知识库的长期记忆 |
| [yussica1016/astrbot_plugin_memory_system](https://github.com/yussica1016/astrbot_plugin_memory_system) | 15 | GPL-3.0 | 2026-05-19 | 遗忘曲线 + 情绪效价 |
| [Illusory-moon/bot-mindscape](https://github.com/Illusory-moon/bot-mindscape) | 6 | MIT | **2026-10-07** | 通用 bot 增强框架（topics: napcat/onebot/persona/roleplay/long-term-memory） |
| [LMG-arch/astrbot-plugin-realistic-persona](https://github.com/LMG-arch/astrbot-plugin-realistic-persona) | 4 | Apache-2.0 | 2026-06-17 | 拟真人设 |
| [oyxning/astrbot_plugin_dynamic_persona](https://github.com/oyxning/astrbot_plugin_dynamic_persona) | 4 | AGPL-3.0 | 2026-10-04 | 每轮动态生成临时性格 |

**含义**：记忆与人格**不要在插件里重造**——AstrBot 生态已有 400★ 级的 self_learning 和多个 15★+ 记忆插件。我们的插件应把"长期记忆"设计成**可选外部依赖**（配置里填某个记忆插件/知识库），而不是自己实现一套。

### 2.3 `LKarxa/astrbot_plugin_SillyTavern_card` 深度分析（题目点名要的那个）

**仓库**：<https://github.com/LKarxa/astrbot_plugin_SillyTavern_card> ｜ 11★ ｜ 1 fork ｜ **无 LICENSE** ｜ created 2025-04-24 ｜ **pushed 2025-04-26（停更）** ｜ size 20 KB ｜ 语言 Python

**文件清单（API 实测）**：`README.md` (3,556 B)、`main.py` (10,879 B)、`character_card_parser.py` (9,931 B)、`json_to_lorebook_yaml.py` (21,043 B)、`metadata.yaml` (190 B)、`requirements.txt` (32 B，几乎肯定是 `pypng` + `PyYAML`)。

**它到底做了什么**

| 环节 | 实现 |
|---|---|
| 数据模型 | 读出 PNG 卡 JSON（V3 `ccv3` 优先、否则 V2 `chara`，base64 → JSON）→ 拆成两半输出：(1) 三个字段的"角色信息"文本 `name`/`prompt`(=description)/`first_mes`；(2) 给 `astrbot_plugin_lorebook_lite` 用的 Lorebook YAML |
| 解析 | `character_card_parser.py`：`png.Reader.chunks()` 遍历 `tEXt`，`keyword.split(b'\x00')`，`base64.b64decode(...).decode('utf-8')`；另有 `write_metadata()` 能**同时写回** `chara`(v2) 与 `ccv3`(v3) 并重算 CRC —— **但 `main.py` 从未调用它**（写卡能力是死代码） |
| 字段兼容 | `first_mes` ← `first_mes` / `begin_dialogs[0]` / `greeting` / `example_dialog[0]` / `char_greeting` / `alternate_greetings[0]`；只取 description，**丢弃** `personality`、`scenario`、`mes_example`、`system_prompt`、`post_history_instructions`、`creator_notes`、`tags`、`extensions`、`character_version` |
| 世界书转换 | `json_to_lorebook_yaml.py`：兼容 `data.character_book.entries` / `character_book.entries` / `entries`(dict) / 单条目 / 顶层字段 / 数组 等 6 条分支；`keys + secondary_keys` → `A&B~C` 匹配串；`position` 映射到 `sys_start`/`sys_end`；`priority = 100 - insertion_order`；`extensions.probability/100`；`prevent_recursion` → `block`；`enabled:false` 跳过；**未知字段全部丢弃**（没有 V3 的 `constant`/`selective`/`case_sensitive`/`match_whole_words`/`use_group_scoring`/`delay`/`sticky`/`cooldown`/`group`/`depth`/`role`/`vectorized`） |
| 命令 | `/convert_card [文件名]`、`/list_cards`、`/help_convert`（无命令组、无命令别名、无权限控制） |
| 配置项 | **没有** `_conf_schema.json`；路径硬编码：`StarTools.get_data_dir("strbot_plugin_SillyTavern_card")/{card,characters}`、输出到 `os.getcwd()/data/lorebooks/` |
| 依赖关系 | 运行前必须另装 `Raven95676/astrbot_plugin_lorebook_lite`（AGPL-3.0）——**单向导出，无回读、无会话绑定、无注入** |
| 已知缺陷 | ① `metadata.yaml` 的 `name` 与 `@register` 名都是 `strbot_plugin_SillyTavern_card`（少一个 `a`），与目录名/仓库名不一致 → 违反市场身份规则；② 用"手动加双引号 + 自定义 Dumper"的方式生成 YAML（YAML 里塞 `\"`、再把 `\n` 还原成块标量），**脆弱且容易产出非法 YAML**；③ `first_mes` 分支顺序把 `example_dialog` 当开场白；④ 无测试、无 `ruff`、无错误边界测试 |

**能不能复用 / 能不能提 PR**
- 代码：**不能复制**（无 LICENSE → 默认保留所有权利）。
- 提 PR 修 bug：**可以且建议做一次**（改 `name` 拼写、补 `_conf_schema.json`、`write_metadata` 接入或删除、补 `requirements.txt` 校验）。但按官方原则，"原插件已停止维护"→ **允许另写独立插件**。
- 我们的差距（相对它）：运行时（会话/多轮）、世界书回读与按会话注入、表情/正则、聊天管理、图片与合并转发、WebUI 面板、ST 外部服务调用——**它一样都没有**，等于零重叠。

### 2.4 跨框架/跨平台：把酒馆搬到 IM 的既有方案

| 项目 | ★ | 许可 | push | 做法与可借鉴点 |
|---|---|---|---|---|
| [SillyTavern/SillyTavern](https://github.com/SillyTavern/SillyTavern)（本体） | 34163 | AGPL-3.0 | 2026-10-02 | **对外接口不能直接当后端**：官方 [Issue #4792](https://github.com/SillyTavern/SillyTavern/issues/4792)（2025-11-22 提出，**同一需求**：外部 bot 框架如 AstrBot 通过 OpenAI 兼容接口调 ST；2025-11-24 由作者自己关闭，state_reason=completed）→ 上游不会做这件事。且 ST 的 chat 端点有 **CSRF 保护**（社区补丁专门"run chat endpoints on a separate port to bypass SillyTavern CSRF"），必须靠**服务端插件**绕开 |
| [Lianues/st-api-wrapper](https://github.com/Lianues/st-api-wrapper) | 16 | ⚠️ **无** | 2026-02-05 | ST 服务端插件：把 ST 内部能力（hooks、`prompt_buildRequest`）封装成官方文档级 API，供别人写插件 |
| [Youzini-afk/st-external-bridge](https://github.com/Youzini-afk/st-external-bridge) | 0 | ⚠️ README 写 MIT / GitHub 检出 AGPL-3.0 | 2026-01-27 | 基于 st-api-wrapper 的 **REST + SSE 桥**：`POST /v2/generate`（支持 streaming）、`/v2/generate/batch`、`GET /v2/characters|presets|worldbooks`、V2 HooksDrivenProxy（无 Playwright、1MB/会话、100+ 并发）。**这就是"插件尽量小、后端借酒馆"的最短路径**，但只开发了 3 天、0★、许可矛盾 → 只能当可选后端 |
| [sakisakisa-design/tavern-link](https://github.com/sakisakisa-design/tavern-link) | 12 | ⚠️ 无 | 2026-01-18 | Node：**QQ(NapCat/OneBot WS) 聊天引擎，兼容 ST 角色卡（PNG tEXt/iTXt base64）与世界书**，带 Web 面板、@触发、长回复分段、豆包 TTS。作者自述 100% Claude 生成 |
| [theStar7/SillyTavern-QQ-Bridge](https://github.com/theStar7/SillyTavern-QQ-Bridge) | 5 | ⚠️ NOASSERTION | 2026-01-13 | ST ↔ NapCat **双向消息转发中间件** + WebUI 控制面板；README 自标"开发中，谨慎使用" |
| [qiuqiu-2/CharaRelay](https://github.com/qiuqiu-2/CharaRelay) | 0 | **Apache-2.0** | 2026-08-05 | **最有参考价值的 clean-room 实现**：QQ 官方 Bot C2C、V2/V3 卡 + PNG + **CharX 容器**（保留未知字段与原始容器）、世界书（关键词/正则/递归/概率/包含组/预算/timed effects/V3 decorators/宏/Outlet）、Author's Note + Character's Note + Persona、SQLite 历史 + 去重回执 + 滚动摘要 + 结构化长期记忆、**每轮 prompt 决策 audit trace**、默认离线 Fake Model。TypeScript/Node≥22 |
| [xz-dev/SillyTavern-ChatBot-Proxy-koishi-plugin](https://github.com/xz-dev/SillyTavern-ChatBot-Proxy-koishi-plugin) | 1 | ⚠️ 无 | 2026-06-29 | Koishi 侧把 chatbot 消息代理到 ST（本地缓存的 README 为空，细节 ⚠️ 未核实） |
| [haveanulllove/advanced-tavern-bot](https://github.com/haveanulllove/advanced-tavern-bot) | 0 | ⚠️ 无 | 2026-01-29 | Java 单文件"高级醉月酒馆机器人"：卡 + 世界书 + 预设关键词 + 正则 + API 调用 + 上下文（教学级实现） |
| [pearyj/sillytavern-cards-skill](https://github.com/pearyj/sillytavern-cards-skill) | 25 | AGPL-3.0 | 2026-03-16 | OpenClaw skill：导入并 roleplay ST 卡（TavernAI V2/V3），可在微信/QQ/Telegram 用 |
| [momori777/Artemis](https://github.com/momori777/Artemis) | 377 | ⚠️ NOASSERTION | 2026-09-30 | 本地 AI 女友 + Agent + 向量库 + live2D + **酒馆角色卡导入** + WebChat，QQ + Telegram 双通道 |
| [Bobini1/SillyTavernDiscordBot](https://github.com/Bobini1/SillyTavernDiscordBot) | 13 | ⚠️ 无 | 2024-03-04 | Discord ↔ ST（已停更） |
| [rimrimcat/SillyTavernDiscordBot](https://github.com/rimrimcat/SillyTavernDiscordBot) | 5 | ⚠️ 无 | 2025-02-10 | 用 **Playwright 操作 ST 网页 DOM** 的桥（反面教材：脆、内存高，st-external-bridge 文档里明确对比） |
| [AyeeMinerva/SillyTavern-Extension-ChatBridge](https://github.com/AyeeMinerva/SillyTavern-Extension-ChatBridge) | 48 | AGPL-3.0 | 2026-01-16 | ST 前端扩展：**WebSocket 双向桥**（聊天历史同步、事件监听、远程发消息、流式） |
| [rinmashiro0529/SillyTavern-IM-Bridge](https://github.com/rinmashiro0529/SillyTavern-IM-Bridge) | 2 | ⚠️ 无 | 2026-07-20 | ST **服务端插件**：把 ST 聊天桥到 Telegram（另有 `-Next`、`-UI`、`im-bridge-rs` 三个仓库） |
| [tsuru0805/lorecards-mcp](https://github.com/tsuru0805/lorecards-mcp) | 2 | ⚠️ 无 | 2026-09-13 | 世界书/人物卡/关键词注入 + MCP 写工具 + 本地 web 编辑器 + **ST 导入导出** |
| [spancerxing/tavern-rikka-bridge](https://github.com/spancerxing/tavern-rikka-bridge) | 5 | ⚠️ 无 | 2026-07-15 | 纯前端：导入 ST 卡（PNG/JSON）与世界书、按类别编辑、导出为 rikkahub 可吃 JSON，**明确"不丢字段"**（字段保真清单可直接抄字段名，代码不可抄） |
| [andclear/piney](https://github.com/andclear/piney) | 323 | ⚠️ NOASSERTION | 2026-06-05 | "SillyTavern 角色卡工作站"：卡/世界书/正则/美化的创建与编辑（编辑器形态参考） |

**从这一节得到的关键设计结论**
1. **不要走"Playwright 操作 ST 页面"** 的路（内存/并发/脆弱，社区已淘汰）。
2. **走"ST 服务端插件 + REST/SSE"** 的路是社区共识（st-api-wrapper → st-external-bridge / IM-Bridge / ChatBridge）。
3. **格式解析一定要自己做保真的**：`tavern-rikka-bridge`、`CharaRelay` 都把"保留未知字段/原始容器"当卖点——这正是 LKarxa 插件最大的短板。

### 2.5 独立的"轻量世界书/角色卡引擎"是否存在？

| 候选 | 类型 | 结论 |
|---|---|---|
| `character-card-parser`（npm） | ST 生态的卡解析库 | ⚠️ 本次未取到仓库页（搜索结果指向 ST 生态，未核实许可证与维护状态）→ 列入情报缺口 |
| [HyperBlaze456/risu-backend-python `char_card.py`](https://github.com/HyperBlaze456/risu-backend-python/blob/main/char_card.py) | Python 卡解析（**可直接读的对照实现**） | ⚠️ 未核实许可证/完整性 → 只当"读一读别人怎么写" |
| [thecapibara/LoreEngine](https://github.com/thecapibara/LoreEngine) | "lightweight world-wiki engine for LLM games, deterministic, zero-dependency alternative to RAG" | 概念可借鉴，**但不是 ST 世界书格式** → 不能当 ST 兼容层 |
| `tsuru0805/lorecards-mcp` | 世界书/卡 + 关键词注入 + ST 导入导出 | ⚠️ 无许可，2★ |
| **结论** | — | **Python 生态里没有一个"可直接 pip install 的 SillyTavern 世界书/角色卡引擎"**。要么用 AGPL/GPL 插件的"格式约定"，要么照 Apache-2.0 的 CharaRelay 语义 clean-room 重写 —— 这本身就是一个可以立项的空位 |

---

## 3. 插件市场：结构、提交入口、检索方式

### 3.1 仓库结构（`AstrBotDevs/AstrBot_Plugins_Collection`，API 实测）

| 路径 | 说明 |
|---|---|
| `plugins.json` | **493,124 B** —— 插件市场主源，符合"插件市场 JSON 规范 2026-06-27" |
| `plugin_cache_original.json` | 676,000 B —— 原始缓存（含更多字段/历史） |
| `del-plugins.json` | 已删除插件记录（当前 2 B = 空） |
| `unreachable-plugins.json` | 不可达仓库（当前 3 B = 空） |
| `scripts/`、`tests/`、`.github/` | 生成/校验脚本与 CI（提交后由 CI 校验并合并进 `plugins.json`） |
| `README.md`（347 B）、`LICENSE`（34,523 B ≈ AGPL-3.0 长度） | 提交入口写在 README：**<https://cloud.astrbot.app/>** |

**检索方式（推荐做法）**
1. **首选**：AstrBot WebUI → 插件 → 插件市场，用关键词搜（这是官方检索面）。
2. **批量/离线检索**：把 `plugins.json` 拉下来本地 grep（本机出网坏，需要在能联网的机器上执行；`raw.githubusercontent.com` 在本机被 DNS 拦，harness 侧可经 `https://gh-proxy.com/https://raw.githubusercontent.com/AstrBotDevs/AstrBot_Plugins_Collection/main/plugins.json` 取；**注意 493 KB，`web_fetch` 会截断，需在能联网环境用 curl/gh**）。
3. **GitHub API 反查**（本次用的办法，最稳）：
   `https://api.github.com/search/repositories?q=astrbot+tavern&sort=stars&order=desc`
   `.../q=astrbot+lorebook`、`q=astrbot+persona`、`q=astrbot+memory`、`q=astrbot+roleplay`
4. ⚠️ `https://cloud.astrbot.app/` 本机 `web_fetch` 直接失败（`TypeError: fetch failed`），**市场 UI 的搜索/分类无法在本次调研中实测** → 见第 5 节缺口。

### 3.2 提交入口与规范要点

- **提交入口**：<https://cloud.astrbot.app/publish>（需注册 AstrBot Cloud 账号）。官方文档：[发布插件到插件市场](https://docs.astrbot.app/dev/star/plugin-publish.html)。
- **市场 JSON 规范**：[版本列表](https://docs.astrbot.app/dev/plugin-market/) → [2026-06-27](https://docs.astrbot.app/dev/plugin-market/2026-06-27.html)（`schema_version: 1`，当前唯一版本）。关键硬规则：
  - 根对象必须有 `$meta`；除 `$meta` 外每个 key 必须是 `plugin_id` 或（兼容例外）`name`。
  - **`plugin_id = metadata.author + "/" + metadata.name`**；`author`/`name` 不得含 `/`；全局唯一（小写归一后不得重复）。
  - 必填字段：`author`、`name`、`version`、`repo`、`desc`；可选：`display_name`、`short_desc`、`download_url`、`logo`、`tags`、`category`、`support_platforms`、`astrbot_version`、`social_link`、`updated_at`、`i18n`、`pinned`、`stars`、`download_count`。
  - `repo` 只能是 `https://github.com/{owner}/{repo}[.git|/tree/{branch}]`，**不能是 SSH、非 GitHub、release/PR/子目录 URL**；owner/repo/branch 必须匹配 `[A-Za-z0-9_-]+`。
  - 安装/更新时必须校验 `metadata.yaml` 的 `author/name/version` 与市场记录**完全一致**，不一致就**安装失败**。
  - 保留字段不得出现在记录里：`plugin_id`、`market_plugin_id`、`market_plugin_identifier`、`root_dir_name`、`local_plugin_name`、`install_method`、`registry_url`、`registry_name`、`installed_at`。
  - 已弃用：`support_platform`/`platform` → 用 `support_platforms`。
- **体积限制**：发布 zip **不得超过 16 MB**，否则 CI 自动拒绝（建议压缩图片、`.gitignore` 排除 `.git`/`__pycache__`/`node_modules`、必要时用 `.gitattributes` 或发布分支）。

### 3.3 检索关键词建议

中文：`酒馆`、`角色卡`、`世界书`、`人设`、`人格`、`扮演`、`长期记忆`、`群聊人格`、`剧情`、`跑团`
英文/拼音：`tavern`、`sillytavern`、`roleplay`、`rpg`、`persona`、`card`、`character card`、`lorebook`、`worldbook`、`world info`、`wi`、`memory`、`prompt`、`preset`、`regex`、`rp`
市场 tags 建议（我们提交时）：`sillytavern`、`character-card`、`lorebook`、`worldbook`、`roleplay`、`tavern`、`prompt`、`ai`。

---

## 4. 命名与合规（必须遵守）

### 4.1 插件命名与元数据（官方原文要点）

来源：[AstrBot 插件开发指南 🌠](https://docs.astrbot.app/dev/star/plugin-new.html) / [发布插件到插件市场](https://docs.astrbot.app/dev/star/plugin-publish.html)

- 仓库/插件名格式（官方原文）：
  - **推荐以 `astrbot_plugin_` 开头**；
  - **不能包含空格**；
  - **保持全部字母小写**；
  - **尽量简短**。
- `metadata.yaml` 必填 `name`（英文唯一标识）、`desc`、`version`（语义化）、`author`、`repo`；可选 `display_name`、`short_desc`、`astrbot_version`（PEP 440，如 `>=4.17.0`、`>=4.16,<5`、`~=4.17`，**不加 `v` 前缀**）、`support_platforms`（必须用 `ADAPTER_NAME_2_TYPE` 的 key，如 `aiocqhttp`、`qq_official`、`telegram`…）、`tags`、`social_link`。
- 市场安装身份：`plugin_id = author/name`，且**必须等于** `metadata.yaml.author/name`（LKarxa 那个插件正是在这里踩坑）。
- 依赖写在插件目录 `requirements.txt`（pip 安装）。
- Logo 可选 `logo.png`，1:1、推荐 256×256。

### 4.2 AstrBot 官方开发原则（**逐字相关条款**）

> - 如果是对某个插件进行功能扩增，请优先给那个插件提交 PR 而不是单独再写一个插件（**除非原插件作者已经停止维护**）。
> - 如果直接借鉴了其他项目的设计、功能创意或实现思路，请在 README 中清楚说明灵感来源并附上相关项目链接。
> - 如果使用、修改或移植了其他项目的代码或资源，请遵守原项目的开源许可协议，并按协议要求保留版权及许可声明。
> - 不要使用 `requests` 库来进行网络请求，可以使用 `aiohttp`, `httpx` 等异步网络请求库。
> - 持久化数据请存储于 `data` 目录下，而非插件自身目录，防止更新/重装插件时数据被覆盖。
> - 提交前请使用 `ruff` 格式化代码；功能需经过测试；需包含良好注释；良好错误处理。

（AstrBot 本体是 **AGPL-3.0**；插件通过官方 API 使用本体不构成代码移植，但"借鉴/移植"第三方代码要按上表第 2、3 条处理。）

### 4.3 许可证矩阵与风险（本次实测）

| 许可 | 涉及仓库（示例） | 对我们的约束 |
|---|---|---|
| **AGPL-3.0** | SillyTavern 本体、`astrbot_plugin_komeiji_tavern`、`lorebook_lite`、`quillplus`、`astrbot_plugin_self_learning`、`persona_plus`、`wave_memory`、`iris_chat_memory`、`self_evolution`(?)、`SillyTavern-MemoryBooks`、`AyeeMinerva/ChatBridge`、`st-external-bridge`(⚠️ 与 README 冲突) | **复制/移植 → 我们的插件整体必须以 AGPL 发布**，且网络服务条款适用。AGPL 的"传染"针对**代码/衍生作品**；**调用其 HTTP API、产出它认识的 YAML 格式、借鉴字段设计**不传染 |
| **GPL-3.0** | `Zhalslar/astrbot_plugin_worldbook`、`yussica1016/astrbot_plugin_memory_system` | 同上（无网络条款），复制即需 GPL |
| **MIT** | `Ortfine/astrbot_plugin_trpg`、`dafeiwu666/astrbot_plugin_persona_manager`、`persistent-memory`、`personagent`、`bot-mindscape`、`math89423-star/moon-qqbot` | 可复制，需保留版权与许可声明 |
| **Apache-2.0** | **`qiuqiu-2/CharaRelay`**、`EmaFanClub/EverMemoryArchive`、`realistic-persona` | 可复制，需保留 NOTICE/许可，且带专利授权——**最佳借鉴对象** |
| **无 LICENSE（`license: null`）** | `LKarxa/astrbot_plugin_SillyTavern_card`、`horizoe10/astrbot_plugin_tavern`、`ysyhlly/astrbot_plugin_worldbook`、`st-api-wrapper`、`tavern-link`、`SillyTavern-QQ-Bridge`、`sakisakisa-design/tavern-link` | **默认保留全部权利（All rights reserved）**。**只能读它学思路/接口，一行代码都不能复制**，也不能把它的文件打包进我们的 zip |
| **NOASSERTION / 非标准** | `angel_memory`、`self_evolution`、`memory_companion`(?)、`piney`、`Artemis` | 必须人工打开 LICENSE 确认（列入缺口） |

**"借鉴思路 vs 复制代码"的操作边界（建议写进我们仓库的 CONTRIBUTING）**
1. **可以**：读文档/读源码后**用自己的话与自己的结构重新实现**；沿用公开的数据格式（ST 卡 V2/V3、世界书字段名）；照抄**字段清单/决策表**（创意与事实不受版权保护）；调用他人 HTTP/WS 接口。
2. **不可以**：复制函数体后改改变量名；复制注释、docstring、正则常量串（正则表达式本身可受保护）；复制 README 大段文字；把对方仓库文件直接搬进我们 zip。
3. **必须做**：README 注明"灵感来源"并附链接（AGPL/GPL 项目还要写明其许可）；移植时代码里保留原版权头。
4. **推荐流程**：选一个 **Apache-2.0 或 MIT** 的蓝本（CharaRelay / personagent）→ 语义对齐；对 AGPL/GPL 项目**只读不抄**，在 README 里以"可选外部依赖插件"的形式声明。
5. **素材红线**：角色卡 PNG、立绘、世界书内容、预设，版权属于各自作者；**插件仓库只放解析器与示例字段说明，不分发他人卡片**；也不要复制 ST 的默认 Prompt 预设文本（那属于 ST 内容资产）。

---

## 5. 情报缺口（未核实，需后续补齐）

1. **市场实况**：`cloud.astrbot.app` 无法抓取（`fetch failed`）；`plugins.json`（493 KB）未逐条解析 → **"哪些插件真在市场里、下载量/分类/tags 如何"全部未核实**（本表"在市场上？"列多数为 ⚠️）。需要能联网的环境执行一次全量 grep。
2. **`st-external-bridge` 的真实许可**：README 徽章与"许可证"段落写 MIT，GitHub API 返回 `AGPL-3.0`（仓库里同时存在 AGPL 文本）→ **必须人工看 `LICENSE` 与 `package.json` 才能决定能否在我们文档里推荐它**。
3. **`st-api-wrapper` 无 LICENSE**：16★ 却无许可，`st-external-bridge` V2 强依赖它 → 后端路线存在许可灰区。
4. **LKarxa 插件是否曾被市场收录 / 是否有 fork 在用**；`XZZKANY/astrbot_plugin_SillyTavern_card` 与它的关系（同源重写？fork？）。
5. **`komeiji_tavern` 的维护者意愿未知**：是否接受外部 PR、是否有贡献指南（仓库里有 `AGENTS.md`，未读）。这是路线 B 成立的前提，**必须先读 `AGENTS.md` + 开 issue 试探**。
6. **`ysyhlly/astrbot_plugin_worldbook` 只活了 1 天**：是弃坑还是 fork 自别处；其"导入 ST `entries`"的具体实现与字段保真度未知（无许可，也不能抄，只能看文档判断是否有价值合作）。
7. **AstrBot 内置能力边界未核实**：官方是否有内置 Persona 管理（有）、"自定义规则"（有）与 `req.extra_user_content_parts` 的容量/顺序语义、上下文压缩策略——与"我们自己注入"是否冲突。这部分属于 `01-astrbot-plugin-api.md` 的职责，本文不重复。
8. **ST 卡/世界书规范的权威版本**：V1/V2/V3 字段全集、CharX 容器规范、`iTXt` 与 `tEXt` 的处理差异——属于 `03-sillytavern-formats-and-api.md`，本文只确认"规范是公开事实标准、可 clean-room 实现"。
9. **`character-card-parser`（npm）** 等独立解析库的许可与维护状态（可能是最省事的轮子）。
10. **`angel_memory` / `self_evolution` / `Artemis` / `piney` 的 LICENSE 文本**（GitHub 只给 `NOASSERTION`）。
11. **`MomoCore/HDS-Interlude`（Koishi 原版）** 的许可与语义文档（`astrbot_plugin_hdsi` 是它的移植，AGPL-3.0）。
12. **市场审核实际标准**：文档只写了大小限制与技术规范；是否审查"是否重复造轮子"/"许可合规"，无公开说明。

---

## 6. 后续要检索的关键词 / 命令

**GitHub REST API（可直接用，已验证可用）**
```
https://api.github.com/search/repositories?q=astrbot+tavern&sort=stars&order=desc
https://api.github.com/search/repositories?q=astrbot+lorebook
https://api.github.com/search/repositories?q=astrbot+persona
https://api.github.com/search/repositories?q=astrbot+memory
https://api.github.com/search/repositories?q=astrbot+roleplay
https://api.github.com/search/repositories?q=astrbot+%E8%A7%92%E8%89%B2%E5%8D%A1
https://api.github.com/search/repositories?q=sillytavern+qq
https://api.github.com/search/repositories?q=sillytavern+nonebot
https://api.github.com/search/repositories?q=sillytavern+koishi
https://api.github.com/search/repositories?q=sillytavern+telegram
https://api.github.com/search/repositories?q=langbot+tavern
https://api.github.com/search/repositories?q=character+card+parser
https://api.github.com/search/repositories?q=lorebook+engine
https://api.github.com/search/repositories?q=world+info+python
https://api.github.com/repos/<owner>/<repo>/license          # 拿许可原文
https://api.github.com/repos/<owner>/<repo>/contents/        # 看有无 LICENSE / tests
```
**中文检索词**：`酒馆插件 AstrBot`、`astrbot 角色卡 插件`、`astrbot 世界书 插件`、`QQ 酒馆 机器人 NapCat`、`Koishi 酒馆 插件`、`NoneBot 酒馆`、`LangBot 酒馆`、`角色卡 V3 规范`、`世界书 递归 概率 注入位置`、`SillyTavern 服务端插件 API`
**英文检索词**：`SillyTavern server plugin API prompt_buildRequest`、`SillyTavern CSRF chat endpoint`、`SillyTavern character card V3 spec`、`CharX container spec`、`lorebook format specification`、`PNG tEXt chara ccv3 base64`、`tiktoken context budget roleplay`、`world info recursion probability sticky cooldown`
**要打开的页面**
```
https://docs.astrbot.app/dev/plugin-market/2026-06-27.html     # 规范（已读）
https://docs.astrbot.app/dev/star/plugin-publish.html          # 发布（已读）
https://docs.astrbot.app/dev/star/plugin-new.html              # 开发原则（已读）
https://cloud.astrbot.app/publish                              # 提交入口（需登录）
https://github.com/AstrBotDevs/AstrBot_Plugins_Collection      # plugins.json 全量
https://github.com/KomeijiDono/astrbot_plugin_komeiji_tavern/blob/main/AGENTS.md   # 贡献规则（路线 B 前提）
https://github.com/Youzini-afk/st-external-bridge/blob/main/LICENSE                # 许可矛盾核实
https://github.com/Lianues/st-api-wrapper                                          # 无许可，需确认
```

---

## 附：本文与同目录其他文件的边界

- `01-astrbot-plugin-api.md`：AstrBot 插件 API 事实（事件、注入点、会话、存储）。
- `03-sillytavern-formats-and-api.md`：ST 卡 V1/V2/V3、世界书算法、ST 服务端 API 可行性。
- `04-prior-art-qq-tavern.md`：QQ/微信/IM 搬酒馆的产品形态与实现细节（本文 2.4 只做竞品概览，细节归它）。
- **本文（02）**：AstrBot 生态竞品盘点、市场检索与提交、命名与许可合规、情报缺口。
