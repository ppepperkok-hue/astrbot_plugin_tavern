# 同类实现与产品形态调研：把「酒馆」搬进 IM 机器人

> 调研范围：NoneBot2 / Koishi / LangBot / AstrBot / MaiBot / OneBot(NapCat, go-cqhttp, mirai) 生态中「角色卡 + 世界书 + 长记忆 + 多轮 RP」的既有实现，以及 SillyTavern 的 API 代理 / 桥接 / 第三方客户端类项目。
> 调研方法：GitHub REST API（`gh api`，本机可用）+ GitHub 仓库搜索 + 官方文档/README + PyPI / npm registry 元数据。**本机 PowerShell 的 schannel HTTPS 已损坏，`curl`/`Invoke-WebRequest` 全部失败，`git clone` 不可用；raw.githubusercontent.com 在本机 DNS 解析失败**，因此 README 全部通过 GitHub REST API 的 `readme` 端点 + base64 解码获取。
> 时间基准：以检索时仓库 `pushed_at` 为准，检索时刻约为 **2026-10**。
> 不确定性声明：所有「维护状态」仅由 `pushed_at`/`created_at`/star 数推断，未做代码级审查；标 ⚠ 的条目为仅有搜索摘要或元数据、未取得完整 README 的项目。

---

## 一、结论先行

1. **「酒馆进 IM」这件事已经被人做过至少 6 个不同路线，但没有任何一个做成「事实标准」。** 最接近我们目标的是 AstrBot 生态本身：`KomeijiDono/astrbot_plugin_komeiji_tavern`（提示词编排 + 世界书 + 分支树 + QQ 分片）和 `Nana7mi0721/astrbot_plugin_quillplus`（世界书 + 角色卡 V2 + RAG 记忆 + 状态栏）。**先读这两个的源码，比读任何外部项目的收益都高。**

2. **「调用外部酒馆」这条路有现成中间层，可作为可选后端，但不建议作为默认主链路。** `Youzini-afk/st-external-bridge`（基于 `st-api-wrapper`，V2 走 Hooks 拦截生成流程，暴露 `/v2/generate`、`/v2/characters`、`/v2/worldbooks`）是设计最正规的一个；`AyeeMinerva/SillyTavern-Extension-ChatBridge`（WebSocket 双向桥，48★）是更轻的备选。它们的代价是：必须让用户自己装并维护一个 SillyTavern，链路变长、故障点变多，且流式/中断语义跨进程很难保持一致。

3. **没有人做过「QQ 里跑酒馆前端」这种形态——因为不成立。** 真正被反复验证的形态是「**QQ 消息 → 本地提示词编排 → LLM → 分段回发**」，酒馆的价值在于 **角色卡/世界书/预设这三份数据格式** 和 **prompt 装配顺序**，不在于前端。我们要迁的是前者，不是后者。

4. **共性架构高度收敛，可以直接抄。** 所有成熟实现都是同一个流水线：`事件 → 触发判定（@/前缀/概率/门控）→ 会话隔离标识 → 历史裁剪 + 世界书命中注入 + 角色卡/人设注入 → LLM 调用 → 正则清洗/状态栏剥离 → 分段发送`。差异只在「触发判定」和「记忆」两处花活。

5. **消息编排（防刷屏）是这个形态里最容易翻车、也最没有现成轮子的部分。** 只有 `komeiji_tavern`（QQ 普通分片 / 合并转发 / 失败重试 / 自动降级）和 `nonebot-plugin-shiro-personification`（按生成耗时补打字延迟、短消息分段、输入状态、消息 reaction）把这件事做细了。**这部分必须自研**，但要照着它们的参数面去设计。

6. **最小可用版（MVP）不需要向量库、不需要 Agent、不需要 WebUI。** 需要的是：角色卡 V2/V3 解析、世界书关键词扫描、会话级上下文与裁剪、`/换卡 /重开 /回退 /重说` 四个指令、长回复分段 + 发送节流。这些一个下午能写完，且不依赖任何外部服务。

---

## 二、逐生态/逐项目详表

### 2.1 AstrBot 生态（与我们同构，最高优先级）

| 项目 | 语言/许可 | 规模 | 维护状态 | 实现要点（可借鉴点） |
|---|---|---|---|---|
| [`KomeijiDono/astrbot_plugin_komeiji_tavern`](https://github.com/KomeijiDono/astrbot_plugin_komeiji_tavern) | Python / AGPL-3.0 | 2★ | created 2026-06-19，**pushed 2026-09-06**，v0.8.2 活跃 | **最完整的对标品。** 可排序 Prompt Manager（角色卡/Persona/世界书/示例/作者注/摘要/记忆/PHI/Bias/自定义块）；世界书支持主次关键词、正则、递归、概率、Sticky/Cooldown/Delay、Outlet、深度注入；混合检索（FTS5 关键词 + 向量，加权合并、分类去重）；创作素材 SQLite 知识库导入；长期记忆 pending/active/archived/rejected 状态机；多世界战役把「世界/规则/权威状态」与聊天会话解耦；LLM 每轮产出 JSON 状态补丁、默认需确认才改权威状态；**分支树归档 + swipe（同输入重生成候选，默认最多 5 个）**；会话级自动摘要（累计 18 条触发，保留最近 12 条）；完整调试器（查看最终 `messages[]`、世界书激活原因、裁剪项）；**QQ 长回复：普通分片 / 合并转发 / 失败重试 / 自动降级**；`/tavern continue / impersonate / quiet`；`/tv undo` 会同步回退插件状态、摘要、预览与自动记忆；数据落 `data/astrbot_plugin_komeiji_tavern/tavern.db` |
| [`ysyhlly/astrbot_plugin_worldbook`](https://github.com/ysyhlly/astrbot_plugin_worldbook) | Python | 1★ | created 2026-09-21，pushed 2026-09-21，新 | **世界书单独拆成一个插件的范式。** 通过宿主 `on_llm_request` 注入（`inject_mode=extra/system/both`）；条目类型三分：常驻 / 关键词 / 场景绑定（带 behavior 行为指引）；自动场景识别（每轮投票，不写回会话）；注入文案全模板化（header/preamble/entry_title/scenario_line/behavior_line/footer/card_label 全可配）；**角色卡提示词去重**（persona 已有同内容则跳过、群聊只补群聊适配层）；`scan_roles` 白名单避免模型自己的话反向触发条目；内置 WebUI 面板（新建书/条目/角色卡都不用写 JSON）；**明确列出「已忽略的酒馆字段」**（`use_regex`/`probability`/`group`/`selectiveLogic`/`position`/`depth`/`role`/`sticky`/`cooldown`/`delay`，`order`→priority）；**踩过的坑值得抄**：运行数据必须放 `data/plugin_data/<插件名>`，因为 AstrBot 从面板安装会整目录替换插件目录，会清空数据 |
| [`Nana7mi0721/astrbot_plugin_quillplus`](https://github.com/Nana7mi0721/astrbot_plugin_quillplus) | Python / AGPL-3.0 | 4★ | created 2026-07-04，**pushed 2026-09-30**，v5.3.1 | **工程完成度最高的一个。** 完整 Character Card V2（PNG/JPG/WebP/JSON 导入导出，内置 W++/Raw Text 正则解析引擎，可导入 Character.AI / Chub）；世界书按角色绑定、切卡自动挂载卸载；**四层 Prompt 装配（协议层→素材层→触发层→安全层）+ 自动截断**；`astrbot.api.logger` 是插件市场硬性上架规则；动态记忆用 `event.unified_msg_origin` 做 session_id 天然隔离，SQLite BLOB 存向量 + NumPy 余弦；**核心记忆锚定**（`is_core=1` 不参与 Top-K，无条件注入 `<core_memory>`）；FTS5(BM25) + Vector 混合检索 + RRF 融合 + 艾宾浩斯时间衰减；**状态栏双模板**（QQ/微信不渲染 Markdown → 自动改用分隔线模板，否则 `**状态栏**` 会原样显示）；状态栏 **6 级降级解析**（code block / LOVE_DATA / STATUS / raw / lenient / LLM 提取）；上下文剥离器：关闭状态栏时同时清除历史里已渲染的状态栏，**避免「边禁边示范」**；原子写（tmp + fsync + os.replace）；6 个 LLM Hooks 的优先次序：`on_waiting_llm_request(100)` → `on_llm_request(100)` → `on_using_llm_tool(200)` → `on_llm_response(10)` → `on_llm_tool_respond(10)` |
| [`Zhalslar/astrbot_plugin_worldbook`](https://github.com/Zhalslar/astrbot_plugin_worldbook) | Python / GPL-3.0 | **57★** | created 2026-01-22，**pushed 2026-10-07**，最活跃 | 生态里星最多的世界书插件。设计取向完全不同：**不做酒馆格式兼容，做「关键词触发的 system_prompt 规则引擎」**。条目字段：`priority`（数字越小越靠前、约束力越强）/ `scope`（会话/群/用户/admin）/ `keywords`（支持正则）/ `cron` 定时触发 / `duration` 生效时长 / `times` 生效次数 / `probability`；通配符 `{user_id}` `{user_name}` `{time}`；导入导出 json/yaml。**「触发后持续生效一段时间」这个语义和酒馆世界书（一次性命中注入）不同**，值得注意——IM 场景下用户能感知到「规则还在生效」，需要显式提供 `条目状态`/`清除条目` |
| [`Raven95676/astrbot_plugin_lorebook_lite`](https://github.com/Raven95676/astrbot_plugin_lorebook_lite) | Python / AGPL-3.0 | 20★ | created 2025-04-01，pushed 2026-03-31 | 受 **ChatLuna 世界书**启发并大幅扩展。YAML 定义 `world_state` / `user_state`（会话内隔离、用户间隔离）/ `trigger`（type: regex/keywords/listener；`block` 阻断后续；`probability`；`position`: sys_start/user_start/sys_end/user_end；`actions` 可链式触发其他触发器，上限 25）/ `authors_note`。**占位符引擎**：`{namespace::function(args)}`，支持 25 层嵌套，内建 `time`（含世界时间推进 `+1D`）、`random`（完整骰子语法 `XdYkZlZuZbZrZtZ`、`adv/dis`）、`var`（set/get/del/add/sub/mul/div）、`logic`（if/and/or/not）、`load/save` 存档。**这是「世界书 + 变量 + 逻辑」能做到什么程度的最好参考** |
| [`LKarxa/astrbot_plugin_SillyTavern_card`](https://github.com/LKarxa/astrbot_plugin_SillyTavern_card) | Python | 11★ | created 2025-04-24，pushed 2025-04-26，**已停滞** | 只做一件事：把 SillyTavern PNG 角色卡元数据 → 结构化文本 + **Lorebook YAML**（喂给 `astrbot_plugin_lorebook_lite`）。字段映射表：`name→name`、`description→prompt`、`begin_dialogs/first_mes/greeting→first_mes`。依赖 `pypng`。**「拆成两个小插件、用文件格式做接口」的思路很值得抄** |
| [`dafeiwu666/astrbot_plugin_persona_manager`](https://github.com/dafeiwu666/astrbot_plugin_persona_manager) | Python / MIT | 4★ | created 2026-01-13，pushed 2026-04-28 | 角色注入与人设管理：会话式创建/修改、标签检索、**关键词触发**、注入前后缀、**群私聊隔离**、CozyNook 社区角色小屋浏览/导入/导出。适合参考「人设 CRUD 与作用域」 |
| [`horizoe10/astrbot_plugin_tavern`](https://github.com/horizoe10/astrbot_plugin_tavern)（321开团） | Python | 8★ | created 2026-07-30，pushed 2026-08-24，v1.0.0-rc10 | **群聊多人跑团**：私聊分步建卡（用验证码桥接群聊与私聊）、角色卡审核、回合选项/集体投票、AI 队友与真人同列、世界包（TWP）、**正文模式三档长度（极简 350–600 / 平衡 700–1200 / 史诗 1400–2600 字）**、叙事文风独立轴、**「较长内容按安全段落拆分投递」**、持久回执 + 断线补读 + 去重 + 快照备份恢复、致命后果需本人预览确认。**多人多群的权限与状态一致性范本** |
| [`Ortfine/astrbot_plugin_trpg`](https://github.com/Ortfine/astrbot_plugin_trpg) | Python / MIT | 1★ | created 2026-08-27，pushed 2026-08-27，新 | 「在 QQ/TG/Discord 等平台的**私聊**中实现类 SillyTavern 的轻量角色扮演体验」。定位明确：只做私聊，不做群聊 |
| [`Liuxd-1230/astrbot_plugin_hdsi`](https://github.com/Liuxd-1230/astrbot_plugin_hdsi) | Python / AGPL-3.0 | 1★ | created/pushed 2026-08-27 | 「持续叙事运行时与多角色独立世界线管理」。多世界线隔离的另一种做法 |
| [`wangkant/personagent`](https://github.com/wangkant/personagent)（+ [`astrbot_plugin_personagent`](https://github.com/wangkant/astrbot_plugin_personagent)） | Python / MIT | **17★** | created 2026-05-16，**pushed 2026-10-03**，v1.0 | **「外部服务 + 瘦连接器插件」的典范架构**，直接对应我们「插件要尽量小」的诉求。personagent 本体是一个 Python 服务（FastAPI + httpx），AstrBot / Koishi(Satori) / Matrix 三端各只有一个薄插件；靠一条**签名的 HTTP 请求/消息**通信，`personagent connect astrbot` 自动安装插件并双向写入 `CONNECTOR_TOKEN`。核心能力：**模型判断「这个人会不会插话」而不是掷骰子**（一次消息爆发只回一条）；**纠错学习需要同一 chat 两条一致反应、其中一条为强信号**，append-only 账本 + 可回滚；lorebook 与输出过滤明确「遵循 SillyTavern 的 World Info 与 regex 扩展」。附带 `personagent eval`（发言准确率、人设辨识度、学习有效性）——**它做了我们最该做但最容易被跳过的事：给自己的 bot 写评测** |
| [`math89423-star/moon-qqbot`](https://github.com/math89423-star/moon-qqbot)（暮恩 Moon） | Python / MIT | 2★ | created 2026-07-01，pushed 2026-07-03 | **AstrBot + NapCatQQ 的完整角色扮演 bot 发行版**（不是插件，是一整套部署包）。SillyTavern **v3** 角色卡（`characters/moon.json`，含 `system_prompt`、`group_persona`、**95 条 few-shot `mes_example`**、11 条世界书条目）；18 个解耦单向依赖插件分层：transport ← orchestration ← intelligence，含 `suli_tavern`(主控) / `suli_gate`(**三层意图门控**) / `suli_guards`(**92+ 正则注入守卫 + 指数衰减 + LLM 裁决**) / `suli_routing`(Lite/Pro 模型路由) / `suli_memory`(工作/情节/日常/核心/自传体五类记忆) / `suli_emotion`(Valence×Arousal 双维 + 好感度 -2~+5 五级门控) / `suli_proactive`(冷场 15 分钟主动发言)；**触发优先级表**：@提及 → 回复 → 昵称检测 → 话题延续 → 主动发言 → 防抖静默 → 批量累积。**这是「一个角色做到极致」的形态，对我们参考价值在「触发与门控」而不在「架构分层」** |
| [`EmaFanClub/EverMemoryArchive`](https://github.com/EmaFanClub/EverMemoryArchive) | TypeScript / Apache-2.0 | 27★ | created 2026-05-02，pushed 2026-07-11 | 「角色随你一起变老的终身陪伴 AI」，支持多 LLM 与 QQ 接入。长时程记忆衰减/成长的一种设计 |
| ⚠ `ASA-max-afk/astrbot_plugin_sillytavern` | Python / AGPL-3.0 | 1★ | created=pushed 2026-07-18，**空壳** | 描述为「可以像酒馆一样直接导入角色卡」，但仓库 README 仍是 AstrBot helloworld 模板，**实际代码未发布，不可用** |
| ⚠ `o2e/astrbot_plugin_tavern_dispatcher`、`nekodeath9527/astrbot_plugin_silly_astr_tavern`、`assassinillya/astrbot_plugin_sillytavern_bridge`、`laoin114514/astrbot_plugin_sillytavern_prompt`、`lonelyxmas/astrbot_plugin_sillytavern` | Python | 0–1★ | 多为 created=pushed 的同日仓库 | 名字很对，实现量极小（有的 README 只有 108 字符）。**说明这个赛道「想做的人多、做完的人少」，不用怕重复造轮子，但也不用指望能直接复用** |

### 2.2 NoneBot2 生态

**结论：NoneBot 生态里基本没有「酒馆」类插件，但有一个完成度极高的「拟人化聊天」运行时，其架构可以直接照抄。**

| 项目 | 语言/许可 | 规模 | 维护状态 | 实现要点（可借鉴点） |
|---|---|---|---|---|
| [`luojisama/nonebot-plugin-personification`](https://github.com/luojisama/nonebot-plugin-personification)（PyPI: `nonebot-plugin-shiro-personification`） | Python / MIT | PyPI 上 0.1.0(2026-01) → **0.7.0.post0(2026-08)**，wheel 从 13KB 长到 2.0MB | **活跃且持续扩张** | **本报告中最有工程参考价值的外部项目。** 设计原则明确写了「**对话语义由 LLM 决定，代码只负责事实/上下文/权限/预算/契约/持久化/安全兜底，不用关键词表代替『要不要回复』**」，还有一条 CI 静态门禁 `python -m tools.personification_semantic_scan` 专门阻止有人在核心对话模块里重新引入 keyword/regex 语义路由。要点：<br>• **群聊参与决策**：白名单群/私聊/@/引用/随机插话/戳一戳、多人并行话题、短期 thread state、回复缓冲、**过期提交拦截**<br>• **TurnPlan + `speech_act` + 工具筛选 + 多步 tool call + 证据综合 + Provider fallback + 预算 shadow/adaptive + Trace**<br>• **拟人发送**：按生成耗时补足打字延迟、短消息分段、引用、@、message reaction、拍一拍、**私聊输入状态**、可选低概率错别字<br>• **Memory**：会话历史 → 摘要压缩 → 长期 memory item → RAG/向量索引 → 衰减 → 整合 → 结晶 → Memory Palace + 关系图；用户画像 + 好感度 + inner state<br>• **一致性工程**：有副作用的工具必须显式声明 `side_effect` / `final_behavior` / `retryable`；外发用 **Operation ledger**，结果不确定时保持 `unknown` 且**永不自动重发**，优先远端精确对账；session lock + stale check 防慢回复插入错话题；同一业务流程**只能有一个 retry owner**<br>• 默认关闭所有高风险能力（QZone/TTS/主动社交/远程 Skill/插件代调用）<br>• WebUI 登录：验证码私聊发送、5 分钟有效、与浏览器 challenge 绑定、`HttpOnly`+`SameSite=Lax`+CSRF token、按来源 IP 限速、管理员撤销后已有 Session 下次请求即失效<br>• 明确列出 OneBot 端能力差异：NapCat / Lagrange / LLOneBot / go-cqhttp 对 reaction、输入状态、拍一拍、QQ 空间的支持不同，**要按实现端探测降级** |
| ⚠ [`47Lux/nonebot-plugin-aichat`](https://github.com/47Lux/nonebot-plugin-aichat)（PyPI: `nonebot-plugin-aichatbyluxis`） | Python / MIT | 0.1.0–0.1.4 全在 **2026-01-07 一天内** | **已停滞** | 轻量角色扮演：自定义 System Prompt、内置 Web 面板、**User ID→昵称 / Group ID→群名映射**（让 AI 看懂谁在说话）、前缀触发 + 概率主动回复、**关键词触发的长期记忆检索**。`data/nonebot_plugin_aichat/` 下四个 JSON（config/characters/groups/memory）。**「把 QQ 号映射成人名」这个小功能在群聊 RP 里是刚需，容易被忽略** |
| [`Bocchiiiiiii/AI-Liars-Tavern-based-on-Napcat-and-NoneBot2`](https://github.com/Bocchiiiiiii/AI-Liars-Tavern-based-on-Napcat-and-NoneBot2) | Python / MIT（template） | 0★ | created 2026-06-16，pushed 2026-06-16，未继续 | 名字里有「Tavern」但是**AI 骗子酒馆游戏**（斗蛐蛐式桌游），**不是 SillyTavern 相关**。标注以免后续误引 |
| ⚠ [`Youzini-afk/st-external-bridge`](https://github.com/Youzini-afk/st-external-bridge) | JavaScript / AGPL-3.0 | 0★ | created 2026-01-24，pushed 2026-01-27 | 作者自述「允许外部机器人框架（如 **AstrBot、Koishi、NoneBot**）直接接入 SillyTavern」。虽然挂在 NoneBot 关键词下，实际是 ST 服务端插件，详见 2.4 |
| nonebot-plugin-lorebook / 角色卡解析类 | — | — | — | **未找到。** GitHub 搜索 `nonebot lorebook`、`nonebot 角色卡` 均无相关仓库；PyPI 未检索到该类包。**结论：NoneBot 生态没有可复用的世界书/角色卡实现** |

### 2.3 Koishi 生态

**结论：真正成体系的只有 ChatLuna（预设系统内置世界书），另有若干角色卡/伪装类扩展插件；专门的「酒馆插件」很少。**

| 项目 | 语言/许可 | 规模 | 维护状态 | 实现要点（可借鉴点） |
|---|---|---|---|---|
| [ChatLunaLab/chatluna](https://github.com/ChatLunaLab/chatluna)（预设系统） | TypeScript | 大型 Koishi 插件 | 官方文档「最后编辑于 12 天前」，活跃 | **预设（preset）= 角色卡 + 提示词块 + 世界书的统一载体**，YAML 格式：<br>• `keywords` 关键词（预设的唯一标识，消息命中别名即切换预设）<br>• `prompts`: `[{role: system/user/assistant, content}]`，支持多组 user/assistant 示例固化风格<br>• `format_user_prompt`: 例如 `"用户{sender}说: {prompt}"`，**这是群聊里区分发言者的关键字段**<br>• **`world_lores`**：默认配置项（`scanDepth` 扫描最近 N 条 / `tokenLimit` / `recursiveScan` / `maxRecursionDepth`）+ 条目（`keywords` 支持正则 / `content` 支持占位符 / `matchWholeWord` / `caseSensitive` / `enabled` / `constant` 常驻）——**字段命名与语义几乎一比一对应 SillyTavern World Info**<br>• `authors_note`：`content` / `insertPosition`(`after_char_defs`\|`in_chat`) / `insertDepth` / `insertionFrequency`（每 N 次用户输入插一次）<br>• `config.longMemoryPrompt` / `longMemoryExtractPrompt` / `loreBooksPrompt` 可替换<br>• **变量与函数**：`sender`/`sender_id`/`user`/`user_id`/`prompt`/`message_id`/`is_group`/`is_private`/`idle_duration`/`bot_id`/`name`；`date`/`isotime`/`time_UTC('+8')`/`weekday`；`random()`/`roll('2d20+5')`；`url('get'|'post', ...)`<br>• 有在线预设编辑器与预设广场（preset.chatluna.chat）<br>**对比 SillyTavern 的差异：ChatLuna 世界书缺少 `sticky/cooldown/delay/position/depth/role/probability` 这些生命周期字段**，用「扫描深度 + token 上限 + 递归」这套更简单的模型替代 |
| [ChatLuna 角色卡兼容扩展](https://chatluna.chat/ecosystem/extension/character-card.html)（`chatluna-character-card`） | TypeScript | 官方生态扩展 | 文档「最后编辑于 4 个月前」 | **直接加载 SillyTavern 角色卡**：把 `.json` / `.png` 卡放到 `<koishi 数据目录>/data/chathub/sillytavern` 即可用。`loadMode`: `memory`（动态加载）或 `file`（转换成 ChatLuna 预设文件，需重启）。可配置 `jailbreak` 与 `systemMainPrompt` / `personalityPrompt` / `scenarioPrompt` / `jailbreakPrompt`。**官方明确警告：不保证兼容所有卡、效果不保证和酒馆一致**——这句话本身就是我们做兼容层时最好的预期管理文案 |
| [`PinkElysiaDev/chatluna-character-meow`](https://github.com/PinkElysiaDev/chatluna-character-meow) | TypeScript | ⚠ 未取到元数据 | ⚠ 未知 | 「让大语言模型进行角色扮演，**伪装成群友**」——Koishi 侧的拟人化社群角色路线。仅由搜索摘要确认存在 |
| [`Oppenheymu/koishi-plugin-rolecard`](https://github.com/Oppenheymu/koishi-plugin-rolecard)（npm `koishi-plugin-rolecard`） | TypeScript / MIT | v1.0.0(2026-07-04) → **v1.9.0(2026-08-26)**，13 个版本 | **非常活跃** | **注意名字有误导性：这不是 LLM 角色卡插件，而是「数据驱动的角色台词引擎」——纯本地、不调 LLM。** 架构是「引擎与角色卡内容完全解耦」：`types.ts`(契约) / `config.ts`(Schema) / `loader.ts`(扫 `assets/` 目录) / `core.ts`(关键词匹配、概率触发、冷却、插图) / `index.ts`(Koishi 生命周期)；角色卡是 `rolecard.json` + `words.json` + `trigger-words.json` 三个纯数据文件，新增角色**不改源码**。配置是**多群聊配置模型**：每个群独立勾选启用哪些角色卡 + 每张卡专属 `cooldown`/`tags`/`enableRandom`/`randomProbability`/`enableImage`/`imageProbability`。**可借鉴的是「卡片纯数据化 + 按群独立配置 + 触发标签体系」，不是它的 LLM 部分** |
| ⚠ [`xz-dev/SillyTavern-ChatBot-Proxy-koishi-plugin`](https://github.com/xz-dev/SillyTavern-ChatBot-Proxy-koishi-plugin) | TypeScript | 1★ | created 2026-03-14，pushed 2026-06-29 | 名字直接命中「Koishi 插件 + SillyTavern 代理」，但**仓库无 README、无描述，未能确认实现**。**情报缺口**（见第五节） |
| `koishi 酒馆` / `koishi lorebook` 关键词搜索 | — | **0 结果** | — | **未找到**其他 Koishi 侧的酒馆/世界书专用插件 |

### 2.4 LangBot / QChatGPT / NapCat 生态（含 go-cqhttp、mirai 时代）

| 项目 | 语言/许可 | 规模 | 维护状态 | 实现要点（可借鉴点） |
|---|---|---|---|---|
| [`sanxianxiaohuntun/QQSillyTavern`](https://github.com/sanxianxiaohuntun/QQSillyTavern)（QQ酒馆） | Python | 33★ | created 2025-02-07，**pushed 2025-03-17，已停滞**（最新版本 0.1.4） | **最正面的「LangBot 酒馆插件」对标品。** 安装方式：LangBot 管理员向机器人发 `!plugin get https://github.com/...`。定位：「完全兼容 SillyTavern 的角色卡和世界书格式」。要点：<br>• 角色卡放 `QQSillyTavern\png`，支持 **YAML（推荐）/ JSON / PNG** 三种<br>• 世界书放 `QQSillyTavern\shijieshu`，`entries[]` 含 `content` / `comment` / `constant`（常驻）/ `enabled` / `key`（关键词）<br>• 智能记忆系统（短期 + 长期）<br>• 世界设定支持**常驻 + 关键词触发**<br>• **破甲模式**（多模型适配）<br>• 用户个人资料/花名控制<br>• **灵活的正则处理系统**<br>• 修复历史就是一部踩坑史：并发与记忆错乱、**群聊中用用户 ID 而非群聊 ID 做记忆归属（作者称否则会「NTR」）**、短期记忆重复回复、群聊角色卡翻页异常、群聊与私聊记忆不同步。**这五条几乎是我们必然会踩的坑** |
| [`Light-yzc/LangBot-Silly-Traven-tools`](https://github.com/Light-yzc/LangBot-Silly-Traven-tools) | Python | 3★ | created 2025-02-08，pushed 2025-05-12，停滞 | LangBot 的酒馆聊天插件，与上者同期、体量更小。**说明 2025 年初有一波 LangBot 酒馆插件热潮，然后都停了** |
| [`LKarxa/prompt_tools`](https://github.com/LKarxa/prompt_tools) | Python | 8★ | created 2025-04-23，pushed 2025-05-24，停滞 | 「读取**酒馆预设**并应用到 bot 上的插件」。与 `astrbot_plugin_SillyTavern_card` 同一作者，思路一致：**只做格式转换，不做运行时**。有 4 个 open issue |
| [`Mai-with-u/MaiBot`](https://github.com/Mai-with-u/MaiBot)（麦麦 / MaiSaka） | Python / GPL-3.0 | **6123★**，643 fork | created 2025-02-25，**pushed 2026-10-07**，极活跃 | **QQ 生态里最成功的「数字生命」型 bot，也是我们必须对照的竞品。** 官方定位是「像真人一样理解你、不追求完美与效率」；有 [`MaiBot-Napcat-Adapter`](https://github.com/Mai-with-u/MaiBot-Napcat-Adapter)(86★)、[插件仓库](https://github.com/Mai-with-u/plugin-repo)、[一键包](https://github.com/Mai-with-u/MaiBotOneKey)(58★)、独立文档站 docs.mai-mai.org。<br>据 personagent 的对比表（2026-10，来自各项目文档）：MaiBot **由 planner 模型决定是否发言，用「频率」设置控制节奏**；**从聊天里学表达和黑话**；学习内容可**人工审核**；但没有 append-only 账本与回滚，也没有公开的行为评测。**它的「学习表达/黑话 + 人类审核队列」是我们短期不做、但要预留接口的方向** |
| [`internetsb/Maizone`](https://github.com/internetsb/Maizone) | Python / MIT | 61★ | created 2025-07-08，pushed 2026-10-06，活跃 | MaiBot 生态插件，可作「第三方插件能扩展什么」的样本 |
| ⚠ [`cjyaddone/ChatWaifu`](https://github.com/cjyaddone/ChatWaifu) 系列 | Python | `ChatWaifuL2D` 520★（pushed 2024-04-08）、`ChatWaifu-marai` 248★（pushed 2023-02-28）、`ChatWaifu-API` 25★（pushed 2023-03-01） | **全部停滞（2023–2024）** | **go-cqhttp / mirai 时代的代表。** ChatGPT + Moegoe TTS + Live2D 做「会说话的二次元女友」。`marai` 版对接 mirai-api-http。**它的历史价值是证明「语音 + 立绘」这条增强路线早在 2023 年就有人做完并放弃了**——说明这类增强是锦上添花、不是留存关键 |
| ⚠ [`MuBai-He/ChatWaifu-NEXT`](https://github.com/MuBai-He/ChatWaifu-NEXT) | ⚠ 未取到元数据 | ⚠ 未知 | ⚠ 未知 | ChatWaifu 后继项目，有第三方整理的「project-architecture」Agent Skill 文档（[skillsmp.com 收录](https://skillsmp.com/ja/creators/mubai-he/chatwaifu-next/agents-skills-project-architecture)）。**未能直接取到仓库 README，架构细节未验证** |
| ⚠ [`Raven95676/astrbot_plugin_lorebook_lite`](https://github.com/Raven95676/astrbot_plugin_lorebook_lite) 的灵感来源 | — | — | — | README 明确写「灵感来源：chatluna - 编写预设 - 世界书」，并自建 QQ 交流群。**这印证了 ChatLuna 世界书是中文 bot 生态里事实上的格式参考** |
| go-cqhttp / mirai 时代的「酒馆类项目」 | — | — | — | **未找到**。GitHub 搜索 `tavern qq bot`、`酒馆 qq`、`sillytavern qq` 返回的结果全部是 2025-02 之后（LangBot/AstrBot/NapCat 时代）的仓库，没有 go-cqhttp/mirai 时代的 SillyTavern 对接项目。**结论：这个形态是 NapCat 普及之后才出现的** |

### 2.5 「酒馆 API 代理 / 封装 / 桥接」类项目

这一类的共同形态：**让外部程序复用 SillyTavern 的完整 prompt 装配能力（角色卡 + 世界书 + 预设 + 正则 + 采样参数），而不是自己重新实现。**

| 项目 | 语言/许可 | 规模 | 维护状态 | 架构与可借鉴点 |
|---|---|---|---|---|
| [`Youzini-afk/st-external-bridge`](https://github.com/Youzini-afk/st-external-bridge) | JavaScript / MIT（README 标 MIT，GitHub 元数据 AGPL-3.0，**不一致**） | 0★ | created 2026-01-24，pushed 2026-01-27 | **设计最正规的中间层。** 基于 [`Lianues/st-api-wrapper`](https://github.com/Lianues/st-api-wrapper) 的 Hooks 系统。V2 两套架构：<br>• **资源桥接**：调 `stApiClient.prompt_buildRequest()` 拿到含 System Prompt / WIAN / 采样参数 / 模板渲染 / 正则的完整请求；数据完整性 95%<br>• **Hooks 代理**：`hooks_install({id, intercept:{targets:['sendButton']}})` 实时拦截生成流程；内存 1MB/会话（vs 浏览器代理 200MB）、并发 100+（vs 5-10）、响应 0.5–1s（vs 3–5s）<br>• V1 还做过 **Playwright DOM 操作**的浏览器代理（`chromium.launch()` → `page.fill('#send-textarea')` → `page.click('[data-send-button]')`）——**这就是「复用前端」的下场，数据完整但脆、贵、慢，作者已明确不推荐**<br>• REST API：`POST /v2/generate`（支持 `streaming: true` 走 SSE）、`/v2/generate/batch`、`GET /v2/characters|presets|worldbooks`、`/v2/proxy/attach|detach|session/:id`、`/v2/stats/*`、`/v2/cache/*`<br>• 认证三种：`X-API-Key` / `Authorization: Bearer` / `?api_key=`<br>• 配置走 `.env`（`ST_URL` / `ST_BRIDGE_ENABLE_V2` / `ST_BRIDGE_ENABLE_PROXY` / `ST_BRIDGE_MAX_SESSIONS` / `ST_BRIDGE_EXTERNAL_AI_PROVIDER`）<br>• **前提条件很硬**：ST 1.12.0+、Node 18+、必须装 `st-api-wrapper`、必须开 `enableServerPlugins: true`、必须改 ST `config.yaml` 的 `listen: true` + `whitelist` |
| [`AyeeMinerva/SillyTavern-Extension-ChatBridge`](https://github.com/AyeeMinerva/SillyTavern-Extension-ChatBridge) | Python / AGPL-3.0 | **48★** | created 2025-02-21，pushed 2026-01-16 | **最通用的 ST 桥（但不是 HTTP API，是 WebSocket 扩展）。** 双向实时通信，支持**聊天历史同步、事件监听、远程消息控制、AI 回复流式**。作为 ST 扩展安装，不需要服务端插件权限。**如果我们决定支持「外部酒馆后端」，这个比 st-external-bridge 的接入成本低得多** |
| [`theStar7/SillyTavern-QQ-Bridge`](https://github.com/theStar7/SillyTavern-QQ-Bridge) | JavaScript / CC-BY-NC-SA-4.0 | 5★ | created 2026-01-13，pushed 2026-01-13 | **完整的「NapCat ↔ SillyTavern」双向中间件，Node.js + Express + ws，带 WebUI。** README 顶部自标「**开发中，谨慎使用**」。要点：<br>• 链路：QQ → 消息转发到 ST → ST 生成 → 回发 QQ<br>• 反向 WebSocket：NapCat → `ws://127.0.0.1:3001`<br>• ST 侧需 `listen: true` + `whitelist: [::1, 127.0.0.1]` + 可选 `basicAuthMode`<br>• **触发策略：私聊直接发；群聊必须 @ 或前缀（`#chat`，可配）** ← 这是防刷屏的标准答案<br>• 用户/群黑白名单、**回复延迟（模拟打字效果）**、WebSocket 自动重连、对话记忆<br>• REST：`/api/status|config|bridge/start|stop|test/sillytavern|test/napcat|logs|history`<br>• 模块划分很干净：`bridgeService.js`（核心）/ `napCatClient.js` / `sillyTavernClient.js` / `configManager.js` / `logger.js` |
| [`Harrishao/Sillytavern-Relay2-Anything`](https://github.com/Harrishao/Sillytavern-Relay2-Anything) | Python | 0★ | created 2026-05-20，pushed 2026-05-21 | 「Bridging messages between **Telegram/Discord/QQ** and Sillytavern」。三天后就停了 |
| [`JOJO666888888/sillytavern-gateway`](https://github.com/JOJO666888888/sillytavern-gateway) / [`sillytavern-qq-gateway`](https://github.com/JOJO666888888/sillytavern-qq-gateway) | JavaScript | 0★ | 2026-06 ~ 2026-08 | 「SillyTavern 多平台聊天网关 - QQ/Telegram/Discord 三大平台 AI 自动回复」 |
| [`destinl/qq-sillytavern-bridge`](https://github.com/destinl/qq-sillytavern-bridge) | Python | 0★ | created/pushed 2026-08-23 | 同日仓库，无描述 |
| [`un4gt/SillyTavern-QQ-Connector`](https://github.com/un4gt/SillyTavern-QQ-Connector) | TypeScript | 0★ | created 2026-09-13，pushed 2026-09-13 | 同日仓库，无描述 |
| [`spancerxing/tavern-rikka-bridge`](https://github.com/spancerxing/tavern-rikka-bridge) | TypeScript / MIT | 5★ | created 2026-05-28，pushed 2026-07-15 | **纯前端桥**：导入 ST 角色卡（PNG/JSON）与世界书 → 分类编辑 → 导出 rikkahub 可消费的 JSON，**保留 rikkahub 导入器本来会丢的字段**。**「导出不丢字段」这个诉求在我们做卡导入时同样存在** |
| [`MissSinful/claude-code-sillytavern-bridge`](https://github.com/MissSinful/claude-code-sillytavern-bridge) | Python / MIT | 31★ | created 2026-04-15，pushed 2026-05-29 | Flask 桥：把 SillyTavern 接到 **Claude Code CLI** 做协作写作。**「用 CLI/Agent 当生成后端」的另一种形态** |
| [`qiuqiu-2/CharaRelay`](https://github.com/qiuqiu-2/CharaRelay) | TypeScript / Apache-2.0 | 0★ | created 2026-07-16，pushed 2026-08-05 | 「Local-first, single-user AI virtual companion for **QQ Official Bot**，with Character Cards and **SillyTavern-style world books**」。注意是 **QQ 官方机器人**（不是 NapCat），**single-user** 定位 |
| ⚠ [`cjyaddone/ChatWaifu`](https://github.com/cjyaddone/ChatWaifu) | Python | 见 2.4 | 停滞 | 无 API 桥接，但常被当作「角色扮演机器人」参考 |
| ⚠ `sillynome` | — | — | — | **未找到。** 该名字在本次检索中无任何结果。可能是记忆偏差或极冷门项目，**不列入** |
| ⚠ `st-api-proxy` | — | — | — | **未找到名为 `st-api-proxy` 的 SillyTavern 适配器。** 搜索 `st-api-proxy` 返回 6147 条无关结果（网络代理类）。**相关但不同名的是 `Lianues/st-api-wrapper`（SillyTavern 服务端 API 包装器，被 st-external-bridge 依赖）** |
| 其他 OpenAI 兼容代理（`LyubomirT/intense-rp-next` 198★ 已归档、`omega-slender/intense-rp-api` 26★、`JOJO666888888` 系列、`AijooseFactory/clawproxy`、`avaritiachaos/qoder-proxy`） | Python/TS | — | 多为个人项目 | 方向是「**反向**」的：把别的 LLM 接进 SillyTavern，而不是把 SillyTavern 接进 bot。**方向相反，不适用**，但可作为「OpenAI 兼容层」的写法参考 |

### 2.6 专门做角色卡 / 世界书的工具与库（可当现成轮子）

| 项目 | 语言/许可 | 规模 | 维护状态 | 可借鉴点 |
|---|---|---|---|---|
| [`andclear/piney`](https://github.com/andclear/piney) | Svelte | 323★ | created 2026-01-30，pushed 2026-06-05 | **SillyTavern 角色卡工作站**：角色卡 / 世界书 / 正则 / 美化 / 图库的创建、导入、编辑、修改。做卡编辑器时的功能清单参考 |
| [`pearyj/sillytavern-cards-skill`](https://github.com/pearyj/sillytavern-cards-skill) | JavaScript / AGPL-3.0 | 25★ | created 2026-03-15，pushed 2026-03-16 | **OpenClaw skill：导入并按 TavernAI V2/V3 规则扮演角色卡，明确写了「在微信、QQ、Telegram 上和角色卡聊天」。** 「Skill（纯 prompt + 少量工具）而不是插件」的极简形态，**和「插件要尽量小」的思路同源** |
| [`foreverse-app/character-card-skills`](https://github.com/foreverse-app/character-card-skills) | Python | 21★ | created 2026-07-17，pushed 2026-07-26 | Agent skills + 15 张原创角色卡 + **「AI 味检测器」（calibrated AI-flavor detector，专给写卡的人用）**。**「检测角色卡质量」这个思路很有价值：我们可以在导入卡时给出可读性/信息完整度体检** |
| [`aikohanasaki/SillyTavern-MemoryBooks`](https://github.com/aikohanasaki/SillyTavern-MemoryBooks) | JavaScript / AGPL-3.0 | **315★** | created 2025-06-12，**pushed 2026-10-05**，活跃 | 「**把 SillyTavern 的聊天记忆存进 lorebook**」——即用世界书条目本身当长期记忆容器。**这个设计非常适合 IM 场景：长记忆 = 一批自动生成的、可控的世界书条目，复用同一套关键词命中逻辑，不需要额外的向量库** |
| [`SenriYuki/SillyTavern-Horae`](https://github.com/SenriYuki/SillyTavern-Horae) | JavaScript | 195★ | created 2026-02-15，pushed 2026-06-07 | SillyTavern 记忆增强扩展 |
| [`LucieEveille/kiwi-mem`](https://github.com/LucieEveille/kiwi-mem) | Python | 329★ | created 2026-04-18，**pushed 2026-10-05**，活跃 | **「AI 伴侣记忆网关」：OpenAI 兼容代理 + 向量搜索 + 记忆热度 + Dream 睡眠整合 + 日历层级摘要，任何客户端都能接。** 如果我们要外置记忆服务，这是最合适的形态模板——**把自己伪装成 OpenAI 兼容端点**，这样 bot 侧完全不用改 |
| [`Kronic90/Mimirs-Memory-Hub`](https://github.com/Kronic90/Mimirs-Memory-Hub) | Python | 31★ | pushed 2026-08-29 | 同类记忆中枢，另一种实现 |
| [`pixelnull/sillytavern-DeepLore`](https://github.com/pixelnull/sillytavern-DeepLore) | JavaScript / MIT | 87★ | created 2026-02-18，pushed 2026-07-04 | 从 **Obsidian 库**做关键词 + AI 检索后注入上下文。**「把世界书后端换成外部知识库」的参考** |
| [`letuhao/lore-weave`](https://github.com/letuhao/lore-weave) | Python / AGPL-3.0 | 33★ | created 2026-03-21，pushed 2026-10-05 | 小说家/世界观作者的 AI 协作工具，canon-safe 写作 + RAG 术语表 + 多语言。**「Setting Book 版本管理与冲突检测」的参考** |
| `UpstreetAI/character-card-parser`、`felixchaos/tavern-card`、`roleplay-studio/character-card`、`motioneffector/cards` 等 | JS/Python/TS | 0–2★ | 停滞 | 一堆很小的角色卡解析器。**说明「解析角色卡」这件事本身没有权威库，我们大概率得自己写**（或直接抄 QuillPlus 的 `persona_manager.py`） |
| [`hiunikitty/Nika-Character-Studio`](https://github.com/HiUnikitty/Nika-Character-Studio) | HTML | 294★ | created 2025-07-10，pushed 2026-09-05 | 「一站式的酒馆编卡器 + 复刻聊天功能」 |

---

## 三、共性设计归纳（这七个问题，别人是这么解的）

### 3.1 如何把长 prompt 塞进聊天

**共识做法：不要把 prompt 发到聊天里，只在服务端拼 `messages[]`。**

- 分层装配，不是一根长字符串。QuillPlus 是显式的**四层 Prompt 装配：协议层 → 素材层 → 触发层 → 安全层**，带自动截断；Komeiji 用**可排序 Prompt Manager**，把角色卡、Persona、世界书、示例、作者注、摘要、记忆、PHI、Bias、自定义块都做成有序块，每块有 `role` / 注入位置 / 深度 / 裁剪优先级。
- **注入位置是个真问题。"深度 0" 不等于 "system"。** ChatLuna 的 `authors_note` 用 `insertPosition`(`after_char_defs` \| `in_chat`) + `insertDepth`（深度 0 = 聊天历史最末端，深度 4 = 最近 3 条消息之前）+ `insertionFrequency`（每 N 次用户输入插一次）。lorebook_lite 用 `position`：`sys_start` / `user_start` / `sys_end` / `user_end`。**"越靠近 prompt 底部，对模型影响越大" 是这几家的共同结论。**
- 预算必须显式管理。Komeiji 有「上下文预算 + 输出预留 + 历史条数 + 裁剪顺序」+「固定历史条数、token 预算、近期消息保护和核心提示块保护」；QuillPlus 有「Prompt 截断上限 + 最低回复字数」。
- 长回复不要一次发。Komeiji：`/tavern continue`（续写不重复）、`/tavern impersonate`（以用户口吻草拟下一条）、`/tavern quiet`（深度 0 注入临时提示词，不改长期预设）。
- **实测教训**：「上下文未超限也可能因注意力稀释而漏遵循尾部格式」（Komeiji README 原话）。解决办法是减少历史条数或开自动摘要。

### 3.2 多人 / 多群上下文隔离

**共识：用「会话标识」做隔离键，且要区分「谁在说话」和「在哪说话」。**

- AstrBot 侧的标准键是 **`event.unified_msg_origin`**（QuillPlus）。世界书插件则做「会话 > Persona > 全局」的三级覆盖：单选资料按此优先级覆盖，世界书和素材叠加。
- **`QQSillyTavern` 踩过的坑值得单独强调**：0.1.2 之前用**群聊 ID** 做记忆归属，导致群内所有人的记忆混在一起，作者称之为「NTR 情况」，改成**用用户 ID**；0.1.4 又修了「群聊和私聊记忆不同步」。**所以正确做法是记忆既要有 `user_id` 维度也要有 `chat_id` 维度，并且要定义跨场景是否共享。**
- NoneBot 的 personification 做得更细：白名单群 + 私聊 + 短期 **thread state**（群内多个并行话题各自独立）+ 回复缓冲。
- MoA（多人跑团，horizoe10）：把「世界 / 规则集 / 权威状态」与「聊天会话」解耦，同一战役可跨多个聊天继续而不串档（Komeiji 同思路）；玩家私聊建卡用**验证码**桥接回群聊流程。
- **lorebook_lite** 给出了另一条正交的隔离轴：`world_state`（所有用户共用，但不同会话自动隔离）vs `user_state`（每个用户自动隔离，且不同会话自动隔离）。**这是「世界变量」与「角色变量」分离的正确切法。**
- 配置粒度上，`koishi-plugin-rolecard` 的**按群独立配置模型**（每群勾选启用哪些卡 + 每张卡独立的冷却/标签/概率）值得抄。

### 3.3 指令设计（换卡 / 重开 / 回退 / 编辑 / 世界书开关）

**共识：指令用斜杠主命令 + 子系统子命令 + 中文别名，权限分读/写两档。**

- **Komeiji 是最完整的指令面**（所有 `/tavern` 可缩写为 `/tv`）：

  | 类别 | 指令 |
  |---|---|
  | 状态/预览 | `/tv status`、`/tv preview` |
  | 重开/回退 | `/tv reset`（只清插件生命周期、状态变量、滚动摘要、请求预览，**不删 AstrBot 聊天与绑定资料**）、`/tv undo`（= `rollback` = `撤回`，删当前会话最近一次用户消息 + 助手回复，并回退插件状态/摘要/预览/自动记忆；**分支树快照仍保留**） |
  | 重说 | `/tv swipe`（= `sw` = `换一个`，用完全相同的上一轮纯文本输入重生成；`sw ls` / `sw p\|n` / `sw u <编号>`，每轮默认最多 5 个候选，继续正常剧情后锁定） |
  | 换卡 | `/tv character status\|next\|use <角色名>`；角色组支持 `round_robin` / `manual`，用户消息里点名成员会自动切卡 |
  | 世界书/素材 | `/tv retrieval test <文本>`、`/tv retrieval stats` |
  | 分支树 | `/tv archive list\|show <节点ID>\|branch <节点ID> [分支名]` |
  | 特殊生成 | `/tavern continue\|impersonate\|quiet` |

- **ysyhlly 世界书的指令面**：`/世界书 列表|新建|启用|禁用|删除|详情|导入|导入示例|加条|加常驻|删条|场景[设置|清除]|开|关|绑定|解绑|状态`，别名 `/wb` `/worldbook` `/lorebook`。**注意它把「场景」做成了一等公民**：手动 `设置` 后锁定，`清除` 恢复自动识别。
- **Zhalslar 世界书**把指令按权限切得很干净：普通用户只有 `条目状态` / `清除条目 [名称]` / `启用条目` / `禁用条目`（**只影响当前聊天**）；管理员才有 `查看条目` / `添加条目` / `删除条目` / `设置触发词` / `设置优先级` / `导出世界书` / `导入世界书`。
- **QuillPlus 把权限写成一条明确规则**：`admin_users` 白名单**只作用于群聊的写指令**，读指令（list/info/search/无参状态查询）在群聊对所有人开放，私聊与 Web 面板不受此限制。写指令的范围被明确枚举为「会改动持久状态」的全部子命令，包括 `/quill debug`（因为输出含会话标识与注入构成）。**这条规则可以直接抄。**
- 指令必须是**聊天内可用**，不能只依赖 WebUI。QuillPlus 的 FAQ 直接写「可以在手机端使用吗？可以。通过发送指令。」——因为 IM 用户 90% 在手机上。
- **可配置化程度是区分度所在**：ysyhlly 把注入文案的每一行（`header` / `preamble` / `entry_title_format` / `scenario_line_format` / `behavior_line_format` / `entry_separator` / `footer_with_scenario` / `footer_without_scenario` / `card_label` / `card_label_group` / `persona_override_notice`）都做成模板配置，**清空某字段即关闭对应那一行**，写错的占位符原样保留而不报错。

### 3.4 流式输出的分段发送

**共识：流式只是内部实现，对 IM 必须变成「分段 + 节流 + 降级」。**

- **Komeiji 是唯一把 QQ 长回复当一等公民处理的**：普通消息分片、**合并转发（合并转发 Node）**、失败重试、**自动降级**；插件配置里独立成组「QQ 普通消息分片 / 合并转发」。FAQ 直接给排查路径：「降低每个 Node 或普通消息分片字符数；合并转发失败时可启用自动降级，或改用逐条普通消息发送。」
- **MoA（horizoe10）**：故事按**安全段落**拆分投递，有正文模式三档目标长度，并有「多段故事投递、失败重试和**断点续发**」，投递有审计、短时断线可补读持久事件并去重。
- **personification**：按生成耗时补足打字延迟、短消息分段、引用、@、reaction、拍一拍、**私聊输入状态（正在输入）**、可选低概率错别字。**输入状态是几乎没人做但体验提升很大的一个点。**
- **tavern-link**：`chat.splitMessage` 开关，长回复自动分段。
- **theStar7 bridge**：`回复延迟`（模拟打字效果）作为独立配置项。
- ST 侧：`st-external-bridge` 支持 SSE 流式，但 README 自己也指出「聊天历史需要外部存储」。
- **技术警示**：QuillPlus 的 `on_waiting_llm_request` 钩子（priority=100）专门用来**控制流式模式**——即「要不要开流式」本身是个需要在请求前决定的状态，不能事后补救。

### 3.5 避免刷屏与风控

这是最多人踩坑、也最需要设计的地方。归纳成五层：

**第一层：触发前过滤（最重要）**

- 群聊必须 @ 或前缀，私聊直接回。**这是 tavern-link 和 theStar7 bridge 的一致默认。** 前缀可配（`#chat`）。
- Komeiji 的触发面是「命令前缀 / @机器人」（theStar7）；personagent 的做法更高级：**由模型判断「一个真人会不会在这种情况下插话」，而不是掷骰子**——「一次消息爆发只回一条，不是每条都回」。它的 eval 报告里明确给出「该沉默时说了话」和「该说话时沉默了」两个方向的错误率，实测 2/11 和 0/13（中英各一组）。
- moon-qqbot 的触发优先级表（7 级）：`@提及` → `回复` → `昵称检测` → `话题延续` → `主动发言` → `防抖静默` → `批量累积`。**「刚说完话后短暂静默」和「多条消息后统一判断」是两个非常实用的防刷屏机制。**
- Zhalslar 世界书用 `probability` + `times`（一次生效周期内最多注入次数）+ `duration` 控制「规则别一直怼」。

**第二层：冷却与配额**

- `koishi-plugin-rolecard`：每张卡、每个群独立的 `cooldown`（默认 60s）+ `cooldownWhitelist`（填用户 ID 后不受冷却限制）。
- personification：`personification_probability`（默认 0.30）、`personification_meme_reply_probability`（0.18，且**只在已决定回复时**才允许带梗，不提高随机发言率）、单 session 并发 3 / 全局并发 12 / 回复总超时 180s。
- komeiji / tavern 都有「会话级开关」。

**第三层：发送节流与降级**

- 见 3.4。核心是**发送失败要能降级，而不是重试到把 bot 刷爆**。
- personification 的 `Operation ledger` 是最严格的：外发操作有 `reserved → dispatching → succeeded / definite_failure / unknown` 状态机，**`unknown` 永不自动重发**，必须人工对账；「同一业务流程只能有一个 retry owner」，避免 Agent、Provider、SDK 多层重试放大请求。

**第四层：内容清洗与安全检查**

- **正则处理系统**几乎是标配：QQSillyTavern 有「灵活的正则处理系统」；tavern-link 的 Web 面板可配正则规则（替换敏感词、格式化输出、移除不需要的内容）；Komeiji 有 `regex_rules` 配置；QuillPlus 有「响应清洗」服务。
- **状态栏/格式污染**是个隐蔽的坑：QuillPlus 的做法是「**关闭状态栏后自动剥离残留格式，关闭时也会清除历史上下文里已渲染的状态栏，避免边禁边示范**」；还有平台双模板（QQ/微信不渲染 Markdown → 用分隔线模板，否则 `**状态栏**` 和代码围栏会原样显示）。
- moon-qqbot 的安全守卫：**92+ 正则注入模式 + 指数衰减 + LLM 裁决**；滥用检测（刷屏、恶意内容）；**Bot 检测**（识别群内可疑机器人账号）；调教防护。
- QuillPlus 的工程安全：全量 HTML 转义 + 模式值白名单防 XSS；世界书导入名只允许字母数字/下划线/短横线/CJK；状态文件 tmp + fsync + os.replace 原子写。

**第五层：平台风控（非技术层面）**

- Komeiji 明确警告：「管理接口复用 Dashboard 鉴权，**不建议直接暴露到公网**」；`theStar7` 的安全提示：「**不要**将 SillyTavern 直接暴露到公网」。
- personification 对 QQ 空间这类非官方接口的态度值得学：**「遇到机器人验证、滑块或风控只切换官方普通浏览器人工处理，不提供验证绕过」**，并把 `risk_controlled` 作为一等状态停止该平台请求。
- **用 QQ 小号登录机器人**（moon-qqbot README 明写「建议使用小号登录机器人，不要用大号！」）。这是最朴素也最重要的风控措施。

### 3.6 数据持久化的两个硬教训

1. **运行数据绝不能放在插件目录里。** ysyhlly 的世界书插件 v1.3.0 实测踩到：AstrBot 从面板安装插件时会**整目录替换**插件目录，把 `worldbook_store.json` 一起删掉，导致世界书、角色卡和 21 个会话绑定被重新补种覆盖。修法是移到 `data/plugin_data/<插件名>/` 并在启动时自动迁移旧数据。**QuillPlus 也把数据放 `data/`。这一条我们必须从第一版就做对。**
2. **写入要防抖 + 原子。** ysyhlly：只在内容变化时写盘、命令类改动立即落盘、变化类改动 2 秒防抖合并、会话数超 200 自动剪枝空闲会话（状态文件实测 147KB → 71KB）。QuillPlus：tmp + fsync + os.replace。

---

## 四、我们的插件应该怎么做

### 4.1 定位建议

**形态选择：走 AstrBot 原生插件（`on_llm_request` 注入 + 指令 + 可选 Pages 面板），而不是外部中间件。**

理由：
- AstrBot 已经被 `moon-qqbot`（完整发行版）和至少 8 个社区插件验证过是能承载 RP 的。
- 中间件路线（`st-external-bridge` / `theStar7 bridge`）要求用户额外维护 SillyTavern + 服务端插件权限 + `listen: true` + whitelist，链路从 3 跳到 5 跳，**而收益只是「复用酒馆的 prompt 装配」——这件事我们自己做也就是几百行。**
- 用户要的是「在 QQ 里和角色聊天」，不是「在 QQ 里用酒馆」。

**但把「外部酒馆后端」做成可选适配器**，优先级：
1. 优先接 **ST-Extension-ChatBridge**（WebSocket 扩展，48★，安装成本低，支持历史同步与流式）；
2. 其次接 **ST-External-Bridge**（HTTP + SSE，数据完整度更高，但依赖 `st-api-wrapper` 与服务端插件）；
3. **不接浏览器 DOM 代理**（作者自己已标注不推荐）。

用统一的 `Backend` 抽象接口，本地实现是默认后端，外部 ST 是可选后端。

### 4.2 现成轮子 vs 必须自研

**可以直接用 / 直接抄的：**

| 需求 | 现成方案 |
|---|---|
| 世界书解析与命中（ST 格式，含 `key`/`keysecondary`/`order`/`comment`/`constant`/`entries` 数组或 map、卡片内 `character_book`） | 抄 ysyhlly `astrbot_plugin_worldbook` 的匹配器（已处理 `entries` 为 map、`selective=false`、仅 `keysecondary` 条目退化成常驻这三个坑） |
| 角色卡 V2/V3 + PNG tEXt/iTXt 元数据解析、W++/Raw Text 解析 | 抄 QuillPlus `persona_manager.py` + `character_card_parser.py`；或 `LKarxa/astrbot_plugin_SillyTavern_card`（依赖 `pypng`，只做转换） |
| 世界书变量/逻辑/骰子占位符引擎 | 抄 `astrbot_plugin_lorebook_lite` 的 `{namespace::func(args)}` 设计（`var`/`logic`/`time`/`random`/`buildin`） |
| 「记忆写成世界书条目」的长期记忆 | 参考 `SillyTavern-MemoryBooks`（315★）的形态，**避免引入向量库** |
| 外置记忆服务（若将来要做） | 参考 `kiwi-mem`（329★）：**把自己伪装成 OpenAI 兼容端点**，bot 侧零改动 |
| 人设与群/私聊隔离 | 抄 `astrbot_plugin_persona_manager` 的作用域模型 |
| 世界书注入的文案模板化 | 抄 ysyhlly 的 `inject_style` 全套字段名 |
| 状态栏解析与降级链 | 抄 QuillPlus 的 6 级降级 + 「改字段名会同步影响提示词契约、剥离器与解析器」的单一来源 `build_status_contract()` |
| AstrBot 插件的上架合规 | QuillPlus 写明了硬规则：**日志一律 `from astrbot.api import logger`，禁止 `import logging`**，这是市场 LLM Guard 审查项，违反直接 Rejected |
| 外部服务 + 瘦插件架构 | `personagent` + `astrbot_plugin_personagent`（一个签名 HTTP 请求/消息 + 自动装插件 + 共享 `CONNECTOR_TOKEN`） |

**必须自研的：**

1. **消息编排（分段 / 节流 / 降级 / 合并转发）** —— 没有现成 Python 轮子。参考 Komeiji 的配置面（每个 Node 字符数、普通消息分片字符数、自动降级开关）和 personification 的「按生成耗时补打字延迟」。
2. **触发与门控策略** —— 必须贴合我们支持的每个平台（QQ 群 @、微信无 @ 概念、Telegram 有 reply）。personification 的「LLM 判断要不要插话」值得抄，但要留一个纯规则的降级路径（小模型/无模型时）。
3. **上下文隔离与记忆归属模型** —— 得自己定义 `(platform, chat_id, user_id, persona_id)` 这套键以及跨场景共享规则。**QQSillyTavern 的两个 bug 就是这里出的。**
4. **指令面与权限模型** —— 平台无关，但必须自己设计中文别名的稳定性。
5. **调试器** —— Komeiji 的调试器（查看最终 `messages[]`、世界书激活原因、裁剪项、摘要状态、警告）是它最有价值的部分。**RP 插件不给出「为什么这轮注入了这些」的可观测性，就没法调优。**

### 4.3 MVP 必含功能（第一版）

**必须做：**

1. **角色卡导入**：Character Card V2 JSON + PNG（tEXt/iTXt），支持 `.json` / `.png`，放指定目录即用；也支持直接粘贴 JSON。落 `data/plugin_data/<插件名>/`。
2. **世界书**：ST 兼容 `entries`（数组 + map 两种），支持常驻 / 关键词；**`scanDepth` 扫描最近 N 条 + `tokenLimit` 上限**；`priority` 排序。
3. **注入装配**：`on_llm_request` 钩子；角色卡进 system prompt，世界书同时进 system + extra（`inject_mode=both`）；**Prompt 块顺序可配**（至少：system 角色卡 → 人设 → 世界书 → 历史 → 本轮输入 → 尾部格式约束）。
4. **多轮上下文**：按会话（`unified_msg_origin`）隔离；固定保留最近 N 条 + token 预算裁剪；`user_id` 出现在消息前缀里（`{user_name}说: ...`，抄 ChatLuna 的 `format_user_prompt`）。
5. **指令**：`/卡列表` `/换卡 <名>` `/重开` `/回退` `/重说` `/世界书 开|关|列表|状态` `/帮助`。权限：读指令全开，写指令群聊仅管理员（抄 QuillPlus 的 `admin_users` 规则）。
6. **长回复分段**：按字符数分段 + 段间延迟；**发送失败自动降级为逐条普通消息**；提供 `合并转发` 开关。
7. **基础防刷屏**：群聊默认需 @，私聊直回；每会话冷却；单 session 并发 1。
8. **正则清洗**：可配规则，对输出做替换/剥离。
9. **调试**：`/酒馆 预览` 输出本轮最终 `messages[]` 与命中的世界书条目、裁剪掉的内容。

**明确不做（第一版）：**

- 向量检索 / Embedding（用 `scanDepth` + 关键词就够；记忆先只做「短期窗口 + 手动锚定」）
- Agent / 工具调用
- 多角色组轮询（`round_robin`）
- 分支树 / swipe 候选（`/重说` 先做成「重新生成一次」即可）
- 状态栏 / 自动配图 / TTS / 语音
- WebUI 面板（指令优先；面板留到 v2，且**必须做 `/酒馆 预览` 才能调优**）
- 外部酒馆后端（v2 再上，先把 `Backend` 接口留出来）
- **绝不**做浏览器/DOM 代理

**第一版就要预留的接口（否则后面要重写）：**

- `Backend` 抽象（`generate(messages) -> text` / `stream()`）
- 平台能力的探测与降级表（是否支持合并转发、输入状态、reaction、Markdown 渲染）——抄 personification 的做法
- 记忆的 `(chat_id, user_id, persona_id)` 三元键
- 「世界书条目」与「自动提取的记忆」共用同一种条目类型（这样记忆天然获得关键词命中能力）

### 4.4 三个最容易翻车的地方（提前设防）

1. **群聊记忆归属。** 别用群 ID 当记忆键。QQSillyTavern 因此出了「NTR」，并且群聊/私聊记忆不同步又修了一个版本。
2. **插件目录被整目录替换。** 数据必须放 `data/plugin_data/`，并在启动时迁移旧路径。
3. **状态栏/格式在 QQ 里原样显示。** QQ 不渲染 Markdown；凡是给模型的格式契约，都要为 QQ 准备一套纯文本模板，且在关闭该功能时**同时清理历史里已渲染的痕迹**，否则模型会继续模仿。

---

## 五、情报缺口清单

以下是本次调研**未能确认**的点，需要在动工前或动工中补齐：

| # | 缺口 | 为什么重要 | 建议的补法 |
|---|---|---|---|
| 1 | **`xz-dev/SillyTavern-ChatBot-Proxy-koishi-plugin`** 的实际实现（无 README、无描述，1★，created 2026-03-14，pushed 2026-06-29） | 名字完全命中「Koishi 插件 + ST 代理」，可能有可直接参考的 Koishi 侧代理实现 | 用 `gh api repos/xz-dev/.../contents` 拉源码目录树后逐个读 |
| 2 | **`st-api-wrapper`（`Lianues/st-api-wrapper`）的 API 面** | 它是 `st-external-bridge` V2 的基础设施；如果我们接外部 ST，大概率要直接对它编程 | 读其 README 与 `prompt_buildRequest` / `hooks_install` 的签名 |
| 3 | **`MuBai-He/ChatWaifu-NEXT` 的架构** | ChatWaifu 系列是这个赛道的老祖宗，NEXT 版可能已经吸收了 2025–2026 的做法 | 直接读仓库；可参考第三方整理的 project-architecture 文档 |
| 4 | **`sillynome`** | 找不到任何对应仓库/包 | 建议向提出该名字的人确认来源；**在确认前不要在任何文档里引用这个名字** |
| 5 | **`st-api-proxy`** | 检索无同名结果，可能指 `st-api-wrapper` 或某个已被删除的仓库 | 同上，确认后再引用 |
| 6 | **MaiBot 的发言决策机制细节** | 6123★ 的最大竞品，personagent 的对比表说它「由 planner 模型决定、按频率设置控制」，但未验证是规则还是模型 | 读 docs.mai-mai.org；直接看源码的 planner 模块 |
| 7 | **`KomeijiDono/astrbot_plugin_komeiji_tavern` 的源码级实现** | **这是我们最高优先级的对标品。** 本次只读了 README（8601 字符），未读代码 | 直接 clone 后读 `main.py` + WebUI 后端；重点看 prompt 块的排序/裁剪实现、QQ 分片与合并转发的具体调用、分支树的数据结构 |
| 8 | **`Nana7mi0721/astrbot_plugin_quillplus` 的源码级实现** | 同上。尤其是 6 个 LLM Hooks 的实际用法与状态栏降级链 | 读 `interfaces/astrbot_hooks.py` + `quill/services/` |
| 9 | **AstrBot 插件市场现有 RP 类插件的完整体量** | 我们用 GitHub 搜索（`astrbot tavern` 8 个、`astrbot 世界书` 7 个、`sillytavern astrbot` 14 个），但市场可能还收录了未写关键词的插件 | 直接抓 AstrBot 插件市场页面/JSON |
| 10 | **QQ 侧的实测限额（消息长度上限、合并转发 Node 上限、发消息频率限制）** | 分段策略的参数必须基于真实限额，不能拍脑袋 | Komeiji README 只说「降低每个 Node 或普通消息分片字符数」，未给具体值；需要实测 |
| 11 | **微信群与 QQ 群在「@ / 回复 / 引用」上的能力差异** | 直接影响触发策略能不能跨平台统一 | 查 AstrBot 各平台适配器的能力表 |
| 12 | **`pearyj/sillytavern-cards-skill` 的具体实现**（25★，明确写了支持 QQ/微信/TG） | 「Skill 形态」（纯 prompt + 少量工具）可能是比插件更轻的实现路径，值得对比 | 读仓库（JavaScript，37KB） |
| 13 | **SillyTavern 自身的 `prompt_buildRequest` 装配顺序** | 我们声称「迁前端体验」，就必须精确复刻它的块顺序（Main Prompt → 角色描述 → 性格 → 场景 → 示例 → 世界书 → 作者注 → 历史 → 尾部） | 读 SillyTavern 源码 `public/script.js` 的 prompt 装配段，或 `st-api-wrapper` 的实现 |
| 14 | **本机网络限制的绕过方式** | 本次 `raw.githubusercontent.com` DNS 解析失败、schannel HTTPS 损坏，只能靠 `gh api` + `web_fetch` 工具工作，效率受限 | 需要 `gh` 之外的大文件下载/批量抓取时，考虑配置 DNS 或代理 |

---

## 附：本次调研用到的检索方式（可复用）

本机**不能用** `git clone`、`curl`、`Invoke-WebRequest`（schannel 报「基础连接已经关闭: 接收时发生错误」），`raw.githubusercontent.com` DNS 也解析不了。可行路径：

```powershell
# 1) 仓库元数据（star/语言/created/pushed/license/description）——这是判断维护状态最可靠的一手数据
gh api repos/OWNER/REPO > meta.json

# 2) README（返回 base64，需要本地解码）
gh api repos/OWNER/REPO/readme > readme.b64
# 解码：
$b = (Get-Content readme.b64 -Raw) -replace '\s',''
[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($b))

# 3) 仓库搜索（注意：GitHub search API 限额 30 次/分钟，容易打满；变量插值有时会静默失败，直接传字面量更稳）
gh api -X GET search/repositories -f q="sillytavern qq" -f per_page=50 -f sort=stars > r.json
$j = Get-Content r.json -Raw | ConvertFrom-Json
$j.items | ForEach-Object { "{0} {1} {2} {3}" -f $_.stargazers_count,$_.full_name,$_.pushed_at,$_.description }

# 4) 限额检查
gh api rate_limit
```

`web_fetch` / `web_search` 工具可用于：npm registry（`https://registry.npmjs.org/<pkg>`，返回完整 README）、PyPI JSON（`https://pypi.org/pypi/<pkg>/json`）、各项目官方文档站、`api.github.com`。**`www.npmjs.com` 页面和 `deepwiki.com` 会被反爬挡住（403/429），优先用 registry 和 github API。**
