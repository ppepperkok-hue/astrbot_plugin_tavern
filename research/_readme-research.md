# AstrBot 高人气插件 README 写法调研

- 调研对象：AstrBot 插件生态中「最可能被大量安装」的插件仓库，分析其 README 的结构约定
- 目的：为 `astrbot_plugin_tavern`（本仓库）重写 README 提供可执行依据
- 抓取日期：**2026-10-07**（GitHub API 返回的 `updated_at` / `pushed_at` 均为该日前后）
- 结论一句话：**这个生态的 README 有非常稳定的模板；本仓库 README 的文笔与信息密度不差，但缺的是"首屏有图、市场安装、指令表、常见问题、联系方式"这五样**。

---

## 0. 证据质量声明（先看这里）

### 0.1 「下载量」没能拿到，一个数字都没有编

AstrBot 官方插件源是 `https://github.com/AstrBotDevs/AstrBot_Plugins_Collection`：

| 尝试 | 结果 |
|---|---|
| `plugins.json`（493 KB，main 分支，实抓 2026-10-07） | 抓到 107 KB 内容并逐字搜索：**不含 `download_count`，也不含 `stars` 字段**。条目只有 `display_name` / `desc` / `author` / `repo` / `tags` / `social_link` / `category` / `version` 等 |
| 市场 JSON 规范 `docs.astrbot.app/dev/plugin-market/2026-06-27` | `download_count`、`stars`、`pinned` 均为**可选字段**（§7），也就是说源里可以没有，而它确实没有 |
| AstrBot PR [#9570](https://github.com/AstrBotDevs/AstrBot/pull/9570)（2026-08-12 合并，Soulter 亲自 merge） | 正文原话：*"The plugin marketplace already exposes and displays `download_count`, but the marketplace sort menu does not provide a way to order plugins by downloads."* → **下载量数据存在**，但它由 AstrBot Dashboard / 云市场服务端提供 |
| `cloud.astrbot.app/api/plugins`、`/api/plugin/market`、`/api/market/plugins` | 全部 **HTTP 404** |
| `cloud.astrbot.app/plugin/{author}/{name}`（市场详情页的真实 URL 形态） | **抓取超时**，未取到内容 |
| `plugins.astrbot.app`（官方插件浏览站） | 只返回 JS 外壳标题 `AstrBot Plugins`，**纯前端渲染，无可用数据** |

**结论：公开渠道拿不到真实下载量/安装量。** 本报告里的"热门"一律是**代理指标**，且每个数字都标了来源：

- **代理 A：GitHub Stars**。来源 `https://img.shields.io/github/stars/{owner}/{repo}.json`（真实上游 API，可复核）或 GitHub Search API。
- **代理 B：GitHub Search API 的 star 排序**：`https://api.github.com/search/repositories?q=topic%3Aastrbot&sort=stars&order=desc`。2026-10-07 该查询 `total_count = 377`，即"打了 `astrbot` topic 的仓库有 377 个"（**自愿打标签，样本不完整**）。
- **代理 C：第三方/社区推荐清单**。官方文档 `docs.astrbot.app/community.html` **没有**插件推荐榜（只有 QQ 群、Discord、Astrbook、玖帕喵），因此清单来源是第三方媒体与社区帖（见 §1.2）。

Star 数 ≠ 安装量，两者甚至会打架：`jjf1126/astrbot_plugin_llm_amnesia` **0 star**，却出现在第三方推荐清单里；官方出品的 `AstrBotDevs/builtin_commands_extension` 只有 **12 star**。所以本报告只用"高 star **且**上过推荐清单"来选样，且不把 star 当作唯一排序依据。

### 0.2 环境限制（复现说明）

| 主机 / 路径 | 状态 |
|---|---|
| `raw.githubusercontent.com` | DNS 解析失败，不可用 |
| `github.com` 网页 | `fetch failed`（HTML 页面不可达） |
| `cdn.jsdelivr.net/gh/...`（单文件） | 会重定向到 raw.githubusercontent.com，失败 |
| `api.github.com` | **可用**（REST API） |
| `ghproxy.net` 代理 raw 文件 | **可用**（本次所有 README 原文都由此取得） |
| `img.shields.io` | **可用**（star 徽章 JSON） |
| 本机 pwsh 出网 | 全部 TLS 被拦，只能靠 harness 的 web_fetch |
| `docs.astrbot.app/dev/star/plugin-publish.html`、`community-events/tonggujiyu-*` | 抓取失败，未能读取发布规范正文 |
| LINUX DO 社区帖（`linux.do/t/topic/1378383`） | 抓取失败 |
| 电玩帮 vgover 文章里的配图 | 全是懒加载占位 GIF，**看不到截图内容**（只能读到文字清单） |

---

## 1. 样本选取与流行度证据

### 1.1 本次深读的 6 个插件

| # | 插件 | 仓库 | Stars（2026-10-07） | 证据来源 | 推荐清单出现 |
|---|---|---|---|---|---|
| 1 | 群聊日常分析插件 | https://github.com/SXP-Simon/astrbot_plugin_qq_group_daily_analysis | **503** | shields.io | 电玩帮「群分析总结」 |
| 2 | 自主学习插件 Self-Learning | https://github.com/NickCharlie/astrbot_plugin_self_learning | **412** | shields.io + Search API 一致 | — |
| 3 | 主动消息 Proactive Chat | https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat | **405** | shields.io + Search API 一致 | 电玩帮「主动消息」**（该文第一名推荐）** |
| 4 | API 聚合 | https://github.com/Zhalslar/astrbot_plugin_apis | **276** | shields.io | 电玩帮「API 聚合」 |
| 5 | Angel Memory 天使之魂 | https://github.com/kawayiYokami/astrbot_plugin_angel_memory | **187** | shields.io | 电玩帮「天使之魂」 |
| 6 | Bilibili 解析 | https://github.com/Soulter/astrbot_plugin_bilibili | **98** | shields.io | 电玩帮「Bilibili 解析」；**作者 Soulter = AstrBot 作者本人** |

### 1.2 官方/第三方清单来源

- 电玩帮（2026-05-20）《AstrBot 插件推薦：讓她更有活人感》：https://www.vgover.com/zh-tw/news/216082
  —— 明确分两组推荐：「提升角色扮演体验」= 主动消息 / 天使之魂(+天使之心) / LLM 遗忘 / 专业戳一戳 / intelligent_retry / Builtin Commands Extension；「功能性增强」= API 聚合 / Bilibili 解析 / help / kuro_sign / ai_reminder / 聊天记录备份 / 群分析总结。**与本次分析的对象高度重合**，且这是唯一一篇专门面向角色扮演场景的清单，对本插件最有参考价值。
- 官方文档社区页：https://docs.astrbot.app/community.html （无推荐榜，仅社区渠道）
- GitHub topic 页：https://github.com/topics/astrbot （HTML 不可达，改用 Search API 等效数据）

### 1.3 其他高 star 插件（仅取到 star，未深读 README，供参考）

从 `topic:astrbot` star 排序（2026-10-07）得到：`anka-afk/astrbot_plugin_meme_manager` **407**、`GEMILUXVII/astrbot_plugin_jm_cosmos` **279**、`Him666233/astrbot_plugin_group_chat_plus` **146**、`kawayiYokami/astrbot_plugin_angel_heart` **134**、`Pancakes-Labs/astrbot_plugin_disaster_warning` **100**、`Zhalslar/astrbot_plugin_pokepro` **107**、`tinkerbellqwq/astrbot_plugin_help` **53**、`2718labs/astrbot_plugin_sylanne` **58**、`Clhikari/astrbot_plugin_office_assistant` **41**、`GEMILUXVII/astrbot_plugin_cloudrank` **30**、`Omnitopia/astrbot_plugin_history` **4**、`muyouzhi6/astrbot_plugin_retry` **17**、`jjf1126/astrbot_plugin_llm_amnesia` **0**。

> 注意：`Pancakes-Labs/astrbot_plugin_proactive_chat` 是从 `DBJD-CR/astrbot_plugin_proactive_chat` 迁过去的，其 README 里 deepwiki / zread 链接仍指向旧 org。看 star 要认新仓库。

---

## 2. 逐插件结构分析

### 2.1 结构对照总表

| 维度 | 群分析(503⭐) | Self-Learning(412⭐) | 主动消息(405⭐) | API聚合(276⭐) | 天使之魂(187⭐) | Bilibili(98⭐) |
|---|---|---|---|---|---|---|
| README 体量（估算） | 最长档，约 900–1100 行 | 约 300 行（含 3 个 mermaid） | 最长，1000+ 行（抓取时仍被截断） | **最短，约 130 行** | 约 350 行 | 约 180 行（GitHub API: 7321 B） |
| 顶部居中块 `<div align="center">` | ✅ | ✅ | 部分（banner 图 + 居中徽章） | ✅ | ❌（左对齐） | ❌（纯左对齐） |
| 徽章 | 5 个 for-the-badge | 4 个 + 文字导航条 | **最多**：语言导航 + 自维护 SVG 排名 + 7 个徽章 + 2 个 AI 问答徽章 | 4 个 | 3 个 | **0 个** |
| 手写 TOC | ✅（12 项） | ✅（行内锚点） | ✅（2 列表格，14 项） | ❌ | ❌ | ❌ |
| 效果/演示图位置 | 功能之前（`## 效果`，6+1 张） | 无 UI 截图，只有赞赏二维码 | 功能之后（`## ✨ 效果示例` 2 张私聊/群聊） | 文末 1 张示例图 | **无任何截图** | 功能特性之后 1 张 |
| 功能写法 | 分 3 组 H3 + bullet + 8 张 WebUI 截图 | **2 列表格**（功能/说明） | 12 条 bullet（加粗前缀+冒号） | 一句话介绍 + 收录清单 plaintext | H3 分组 + 2 个表格 | bullet + 加粗前缀 + 缩进子项 |
| 安装写法 | **没有安装章节**，靠首屏"插件市场入口"徽章 | 手动 `git clone`（powershell） + WebUI 装依赖 | 市场下载 / Release zip / `➕ 从文件安装` + pip | **一句话**：市场搜索 → 点击安装 | 前置插件 + `git clone` + `pip -r` | 市场下载 + `plugin i <url>` |
| 配置写法 | 3 列表格 + `[!NOTE]` 指向面板 | `docs/` 外链，README 只给顺序 | `<details>` 折叠 + 逐键（类型/默认值/范围/说明/提示）+ JSON 示例 | 一句话指向面板路径 | 3 列表格（配置项/默认值/说明）+ JSON/YAML | 2 种方式 + 1 张截图 |
| 指令表 | fenced code 块（**没有表格**） | 2 列表格（命令/说明） | "这个插件没有命令" | **2 列表格** | 工具调用代码块 | **4 列表格**（指令/参数/说明/别名） |
| 真实示例+预期输出 | ✅ 用户↔AI 对话含工具调用与返回 | ✅ 检查清单式排查 | ✅ `/sid` 返回原文 + 日志片段 + JSON | ✏️ 仅触发示例 | ✅ 工具返回值描述 | ✅ 示例指令 + 行为说明 |
| FAQ / 排障 | ✅✅ **最强**（现象/原因/忽略 + hex 头 + docker 命令） | ✅ 4 组排查 | ✅ 13+ 折叠 Q&A，含限流专题 | ❌ | ✅ 4 条 Q&A | ✅ 5 条，全带 issue 链接 |
| 平台适配表 | ✅ 4 列（平台/适配器/驱动/特殊要求） | ✅ 数据库兼容表 | ✅ 5 列 19 行（✅⚠️❓） | ❌ | ❌（列了前置插件） | ✅ 2 行 bullet |
| 版本兼容 | 徽章 `AstrBot>=4.24.1` + 历史版本表 | 徽章 `>=4.11.4` | 徽章 + 19 行平台表 + 历史版本表 | 徽章 `4.0+` | 徽章 | ❌ |
| 联系方式 | QQ 群 + TG 群二维码图 | QQ 群号 + Issue 模板链接 | QQ 群号 + 二维码图 | QQ 群号（"不点 star 不给进"） | — | Issue 链接 |
| 页脚三件套 | Contributors + Star History + License + 求 Star | 赞赏二维码 + 回到顶部 + 协议 | Repobeats + Star History + License 三条解释 | 鸣谢 API 站点 | 致谢 + 收尾一句话 | Contributors + 更新日志 |
| 多语言 | ❌ | ✅ `README_EN.md` | ✅ EN + JP | ❌ | ❌ | ❌ |

### 2.2 逐个要点

**① SXP-Simon/astrbot_plugin_qq_group_daily_analysis（503⭐，本次 star 最高）**
- 首屏：居中 H1 → 5 个 `for-the-badge` 徽章（版本 / **插件市场入口**（链到 `cloud.astrbot.app/plugin/SXP-Simon/...`）/ `AstrBot>=4.24.1` / License / Ask DeepWiki）→ QQ 群与 Telegram 群二维码图 → 斜体一句话简介（含 NapCat/LLOneBot/SnowLuma 图标）→ getloli 计数器图。
- **没有「安装」章节**：市场入口做成徽章，安装说明被"市场一键安装"这个前提吃掉了 —— 这是本样本里最高 star 插件的做法，值得注意但不建议照抄（对未上架插件不适用）。
- 特色：`## 效果` 用 HTML `<table>` 排 6 张主题截图，每张点开是 `cdn.jsdmirror.com` 全尺寸 jpg；WebUI 用 2×4 截图墙；配置表给"配置项/说明/备注"三列并注明"以插件配置页为准"；有一整节 `### 分析黑白名单配置说明（小白能懂）`：场景 A/B/C/D + 「最容易踩坑的 5 点」+ 「你可能会问（关键边界）」，**这是全样本里对"配置理解"投入最多的一份**。
- FAQ 深度惊人：把 `496e7465726e616c...` 十六进制响应头翻译成 `Internal S`，再给三条排查（`/etc/gai.conf` 走 IPv4、T2I 容器挂载卷、`--shm-size=1g`）。
- 大量 `> [!NOTE]/[!TIP]/[!IMPORTANT]/[!CAUTION]/[!WARNING]` GitHub callout，`<details>` 折叠长内容。

**② NickCharlie/astrbot_plugin_self_learning（412⭐）**
- 首屏：居中 H1 + 一句话 + 4 徽章 + 一行锚点导航；紧跟一个**中英双语「版权与许可」大 blockquote**（作者/维护者/版权/协议）—— AGPL 项目里少见的做法。
- 有 `> [!WARNING] 使用前手动备份人格`、`<details> 免责声明与用户协议`（5 条）。
- **表格优先**：核心功能用 2 列表格，数据库用 3 列表格，管理命令用 2 列表格。
- 用 **3 个 mermaid 图** 表达架构与学习链路（不截图，用图代码）。
- 安装是**手动 `git clone`**（没有市场安装），依赖还要用户去 WebUI 点按钮装 —— 这是它最弱的一环。
- 排障用 H3 提问式标题（"插件加载失败：缺少依赖"/"学不到内容"），答案给 9 条编号自查项。
- 页脚：`## 推荐搭配`（LivingMemory、Group Chat Plus，说明自动跳过重叠能力）→ 协议 → 特别鸣谢（MaiBot）→ 赞赏二维码。

**③ Pancakes-Labs/astrbot_plugin_proactive_chat（405⭐，角色扮演场景最相关）**
- 首屏最"重"：socialify 自动生成 banner → `简体中文 | English | 日本語` 语言导航 → 3 个自维护 SVG 排名徽章（`assets/PluginRank.svg`、`StarRank.svg`、`ShitMountain.svg`）→ 7 个 shields 徽章（License/Python/AstrBot/兼容性/Release/QQ 群）→ deepwiki + zread → 计数器 + `logo.png`。
- **「开发者的话」写得像 blog**（我是文科生、编程能力 0、全仓库由 AI 写、70 天 327 小时的开发统计）—— 附带 `> [!WARNING] 本插件和文档由 AI 生成`。这在本生态是有效的情感钩子，但对技术型插件不是必选项。
- 安装：`1. 通过插件市场下载 / 或从 Release 下载 zip → WebUI 右下角 ➕ 从文件安装`，再 pip、重启、配置 —— **步骤化 + 兜底路径**，是样本里最完整的安装写法。
- `/sid` 的使用给了两段**逐字预期输出**（含 `UMO:`/`UID:` 原文），并配 `> [!WARNING]` 提醒别整段照抄。这是"示例指令带预期输出"的最佳范例。
- 配置章节放在 `<details>` 里，逐键给 类型/默认值/范围/说明/提示/占位符，另有大量真实 prompt 示例与 JSON 片段。
- 19 行平台适配表用 ✅⚠️❓ 三态标注 + "等待社区反馈"，**主动承认未测试项** —— 这一点非常值得抄（我们也可以这么写 Telegram/Discord/企微）。
- 页脚：历史版本表 → 常见问题（13+ 折叠）→ 已知限制 → 友情链接与致谢 → 推荐阅读（自己的其他插件）→ 联系我们（QQ 群 + 二维码图）→ 贡献 → 许可证（**把 AGPL 三条含义展开解释**）→ Repobeats → Star History。

**④ Zhalslar/astrbot_plugin_apis（276⭐）**
- **反例即范例：全文约 130 行，却是第四高 star。** 居中 H1 + 斜体一句话 + 4 徽章，然后 `## 💡 介绍` 一句、`## 📦 安装` 一句、`## ⚙️ 配置` 一句（给面板路径）、`## ⌨️ 使用说明` + **2 列指令表** + 160+ API 的 plaintext 清单。
- 没有 FAQ、没有平台表、没有截图墙、没有 star 图，只有 1 张示例图和 1 个计数器。
- 但它把用户真正关心的事做到了极致：**能不能装（一句）、怎么触发（表格）、有哪些东西（清单）**。
- `## 📌 注意事项` 里放了 docker 路径映射、引用消息开关等真实坑；QQ 群写"不点 star 不给进"，风格随意但有效。

**⑤ kawayiYokami/astrbot_plugin_angel_memory（187⭐）**
- **全文零截图**（除徽章），却有 187 star —— 说明截图不是充分条件。
- 有 `## 🌟 天使五件套` 表格，把同作者 5 个插件互相导流（天使之心/眼/笑/魂/画笔）；`### 前置要求` 明确"**必须**先装 angel_heart"——依赖关系写得最清楚的一份。
- 配置用 3 列表格（配置项/默认值/说明）+ JSON 示例；高级配置用 YAML 块。
- 有 WebUI 使用说明（怎么点进 Plugin Pages）+ 8 行页面功能表 + 导出/导入的数据格式与去重规则。
- `## 🐛 故障排查` 4 条 Q&A（启动失败/检索效果不好/记忆系统不工作/灵魂参数异常）—— 短但命中。
- 有个很长的"知识库不是 RAG！"论说段（做菜比喻）—— 教育用户"正确用法"，这在同类插件里少见但很实用。

**⑥ Soulter/astrbot_plugin_bilibili（98⭐，AstrBot 作者本人）**
- **全样本最"朴素"的官方插件**：无徽章、无居中块、无 TOC、无 star 图、无 stargazers 引导。
- 顺序：H1 → 一句话 → `## ✨ 功能特性`（加粗前缀 + 缩进子项）→ **1 张截图** → `## 🚀 安装`（市场下载 / `plugin i https://github.com/...`）→ `## ⚙️ 配置`（两种方式 + 1 张截图）→ `## 📖 使用说明`（`### 订阅管理页面` + `### 动态订阅指令` 4 列表格 + `#### 参数说明` + 真实示例）→ `## 适用平台/适配器` → `## 常见问题`（5 条，**每条都带 issue 链接**）→ `## 模板开发` → `## Contributors`（contrib.rocks）→ `## 更新日志` 指向 CHANGELOG.md。
- 结论：**"官方/权威"不等于"重排版"**；它的价值在于结构清晰 + 四个表（指令表/参数说明/平台/FAQ）。

---

## 3. 综合：这个生态的 README 通用骨架

### 3.1 高频共用骨架（按出现顺序）

1. **居中标题块 + 一句话定位**（"这是一个为 AstrBot 设计的 XXX 插件"），不超过两行。
2. **徽章行**（shields.io）：`License` / `Python` / **`AstrBot >= x.y.z`** / 版本号 / QQ 群；有的加自维护排名 SVG 或"插件市场入口"徽章（`https://cloud.astrbot.app/plugin/{author}/{name}`）。
3. **手写 TOC**（中文锚点链接；大 README 用 2 列表格排）——章节一多就必须有。
4. **多语言导航**（只在头部大插件出现：`简体中文 | English | 日本語` → `README_EN.md` / `README_JP.md`）。中文优先是绝对默认。
5. **效果/演示区**（截图墙、GIF、合并转发截图），位置通常在功能之前或之后紧邻。
6. **`## ✨ 功能特性`**：bullet + 加粗前缀，或 2 列表格（功能/说明）。
7. **`## 🚀 安装`**：默认是"**在 AstrBot 插件市场搜索 xxx → 点击安装**"，附 `plugin i <repo-url>`；需要外部服务时才展开手动步骤 + 兜底路径（Release zip / `➕ 从文件安装`）。
8. **`## ⚙️ 配置`**：一句话给面板路径（`插件管理 → xxx → 操作 → 插件配置`）+ 3~5 列表格或 `<details>` 逐键说明；长内容用 `> [!NOTE]` 引流到配置页。
9. **`## 📖 使用` + 指令表**：表格列名基本固定为 **指令 / 参数 / 说明 / 别名**；紧跟 1~2 条真实示例，最好带预期输出（截图或逐字文本）。
10. **平台适配**：4~5 列表格（平台 / 是否支持 / AstrBot 版本要求 / 备注 / 测试情况），用 ✅⚠️❓ 标注并写明"等待社区反馈"。
11. **`## ❓ 常见问题` / `## 🐛 故障排查`**：`<details>` 折叠，Q 用症状描述，答里给日志片段、配置 JSON、docker 命令等"可照抄的东西"。
12. **`## ⚠️ 已知限制`**（大插件都有）。
13. **页脚三件套**：Contributors（`contrib.rocks`）+ Star History（`api.star-history.com`）+ License/鸣谢；再补 QQ 群、Issue、推荐搭配（自己的其他插件）。

### 3.2 头部插件做了、弱插件没做的事

| 做法 | 头部 | 弱/未做 |
|---|---|---|
| 首屏 6 行之内出现图（演示/效果/logo） | 群分析、主动消息、Bilibili | 天使之魂（0 图） |
| 明确的 AstrBot 版本要求（badge + 表格双写） | 群分析、主动消息、self_learning | Bilibili、API 聚合 |
| 市场安装 + 兜底安装路径 | 群分析（徽章）、主动消息、API 聚合、Bilibili | self_learning、angel_memory（只有 git clone） |
| 指令用表格而不是 code 块 | 绝大多数 | 群分析（用 code 块） |
| 折叠式 FAQ（`<details>`） | 群分析、主动消息、self_learning | API 聚合（无） |
| 联系方式（QQ 群 + 二维码） | 群分析、主动消息、API 聚合、self_learning | Bilibili、天使之魂 |
| 主动承认"未测试/不支持" | 主动消息（19 行 ✅⚠️❓） | 大多数 |
| 版本历史表 | 群分析、主动消息 | 其余 |
| 把开发内容外移 | self_learning（→ `docs/`）、主动消息（→ `CHANGELOG`/`CONTRIBUTING`）、Bilibili（→ `CHANGELOG.md`） | 只有 Bilibili 在 README 里放了一节"模板开发" |

### 3.3 生态特有的约定（照抄不会错）

- **中文优先**：README 英文版另开 `README_EN.md`，头部做语言切换。
- **emoji 标题是事实标准**：`## ✨ 功能特性`、`## 🚀 安装`、`## ⚙️ 配置`、`## 📖 使用说明`、`## ❓ 常见问题`、`## 📄 许可证`、`## 🙏 鸣谢`、`## ⭐ Star History`。
- **GitHub callout 语法被广泛使用**：`> [!NOTE]` / `[!TIP]` / `[!WARNING]` / `[!IMPORTANT]` / `[!CAUTION]`。
- **市场身份与安装**：市场条目身份 = `metadata.yaml` 的 `author/name`；legacy 官方源 `plugins.json` 的键是 kebab-case（如 `astrbot-plugin-qq-group-daily-analysis`），而 2026-06-27 新规范要求键为 `${author}/${name}`。**两套并存，写文档时不要用市场键当插件名。**
- **市场详情页 URL**：`https://cloud.astrbot.app/plugin/{author}/{name}`，头部插件把它做成"插件市场入口"徽章。
- **`logo.png`**：市场列表会显示，`proactive_chat` 的目录注释写明"`logo.png` 适用于 AstrBot v4.5.0+"。
- **`CHANGELOG.md`**：注释写明"适用于 AstrBot v4.11.2+"，头部插件都在页脚链过去。
- **常用生态指令/路径**：`/sid` 取 UMO；插件配置页路径 `插件管理 → 插件 → 操作 → 插件配置`；Plugin Pages（AstrBot ≥ 4.26.0）；数据目录 `data/plugin_data/<plugin_name>/`，README 里常给目录树。
- **许可证表述**：AGPL/GPLv3/MIT 都常见；**AGPL 项目要在 README 里解释"通过网络提供服务需公开源码"**（`self_learning` 与主动消息都写了）。
- **`plugin i <repo-url>`** 是 AstrBot 的 URL 安装指令，Bilibili README 明确给出 —— **未上架市场的插件也能用这条**。

---

## 4. 对本仓库 `README.md` 的现状诊断

已读原文（165 行 / 9819 B）。它有明显的优点，也有明显的"不像本生态"的地方。

**已经做对的（样本里少见，建议保留）**

1. 开头就声明与 SillyTavern 的关系与许可证兼容性 —— 法务清晰度高于样本平均。
2. `> ⚠️ 不要把数据放进插件目录` —— 用真实踩坑提醒，样本里几乎没人写。
3. `## 已知边界` 明确列出未实现项（outlet / 向量化世界书 / 多角色同群）—— 与主动消息那份"已知限制"同级，是加分项。
4. 发布体积 16 MB 硬约束 + `export-ignore` 的说明 —— 比样本里任何人都严谨。
5. 配置项 hint 写得很细（中文整词匹配的 `\b` 边界问题、冷却/并发、分段发送等），**这些经验目前只存在于面板里，用户不点开就看不到**。

**与生态约定不符的（问题清单）**

| # | 问题 | 依据 |
|---|---|---|
| 1 | **全文 0 张图片**：没有一张截图、GIF、效果图、logo | 头部里除天使之魂外全有；角色扮演场景尤其依赖"效果图" |
| 2 | **首屏是长 blockquote 技术论证**（"酒馆的世界书引擎与 Prompt 组装都在浏览器端…"），第二屏是被迫解释 | 头部首屏都是"一句话 + 徽章 + 目录"；这段是给开发者看的 |
| 3 | **安装是"把本仓库放进 data/plugins/"**，没有任何市场/一键安装路径 | 头部默认市场安装；Bilibili 还给 `plugin i <url>` |
| 4 | **指令是 code fence 列表**，没有表格、没有预期输出 | 生态默认 4 列表格（指令/参数/说明/别名） |
| 5 | **没有「常见问题」章节** | 5/6 个样本都有；素材我们其实已经有了（散落在 hint 与已知边界） |
| 6 | **没有平台适配说明**（只在第一行文字提到 QQ/Telegram/Discord，metadata 里是 4 个平台） | 群分析与主动消息各有一张平台表 |
| 7 | **没有版本徽章/兼容说明**：`AstrBot >=4.17,<5` 在 `metadata.yaml` 里、`4.28.2 实测通过` 埋在「开发」第 143 行 | 头部都把版本要求放首屏 |
| 8 | **README 承载了过多开发者内容**：目录结构、发布体积、pytest/冒烟/e2e 脚本、research/ 12.6MB 说明 | 头部把这些放 `docs/`、`CONTRIBUTING.md`、`CHANGELOG.md` |
| 9 | **页脚没有联系方式/Star/Contributors**，只有「致谢与许可」 | 4/6 有 QQ 群；大插件都有 Star History |
| 10 | 没有 TOC、没有徽章、没有 `README_EN.md` | 大插件标配 |
| 11 | 缺少 `logo.png`、`CHANGELOG.md`、`CONTRIBUTING.md`（仓库根目录确认无这三个文件） | 市场列表与更新日志都用得上 |
| 12 | 「已知边界」里写着"插件未做市场发布"—— 这是维护者 TODO，不该出现在用户文档里 | — |

---

## 5. 对 `README.md` 的具体建议

按"改动收益 / 成本"排序，前 6 条是必须做的。

**① 新增 `## ✨ 效果`（放功能之前，首屏之后）**
- 需要 4~6 张素材：私聊一轮多回合对话、群聊 `@机器人` 触发、`/tavern list` 返回、`/tavern preview` 的世界书命中预览、角色卡 `.png` 导入过程、酒馆后端模式下的设置页。
- 素材放 `docs/images/` 或 `assets/`，README 用 HTML `<table>` 排两列（对齐群分析的截图墙做法），点开链到大图。
- **这是当前最大差距**：一个角色扮演插件不给人看效果，等于让人盲装。

**② 重写首屏（前 15 行）**
- 结构改成：`H1 astrbot_plugin_tavern · 酒馆角色扮演` → 斜体一句话（"把 SillyTavern 的角色卡、世界书与聊天体验带进 AstrBot，在 QQ/微信里玩角色扮演"）→ 徽章行（`License AGPL-3.0` / `Python 3.10+` / `AstrBot >=4.17` / `版本 0.1.0` / `QQ 群`）→ 手写中文 TOC。
- 现在开头的「与酒馆的关系」长引用压缩成**一句话 + 一个链接**，完整论证移到新文件 `docs/design-notes.md`（或 `## 设计说明` 折叠到文末）。
- 建议加 logo：仓库根目录补 `logo.png`（市场列表会显示）。

**③ 安装章节改成"市场优先 + 兜底"三行**
```
1. 在 AstrBot WebUI 的「插件市场」搜索 astrbot_plugin_tavern → 点击安装。
2. 尚未上架前可用指令安装（AstrBot 4.x）：
   plugin i https://github.com/ppepperkok-hue/astrbot_plugin_tavern
3. 手动安装：把仓库放进 data/plugins/，目录名必须是 astrbot_plugin_tavern。
```
- 保留现有那段"目录名必须与 `metadata.yaml` 的 `name` 一致"的说明（它是真实约束）。
- 删掉「已知边界」里的"插件未做市场发布…"那一条。

**④ 指令改成表格 + 给预期输出**
- 4 列表格：`指令 / 参数 / 说明 / 别名`，把现有 12 条 `/tavern ...` 搬进去；`/酒馆` 中文别名单独一行说明。
- 至少给两段逐字预期输出：`/tavern list` 的角色卡列表、`/tavern preview 你好` 的注入预览（可截断但保留真实格式）。
- 参考：Bilibili 的 4 列表格 + 主动消息的 `/sid` 逐字输出。

**⑤ 新增 `## ❓ 常见问题`（折叠）**
至少覆盖这 8 条，素材现成（hint + 已知边界 + 平台差异）：
1. 群里 @ 了没反应 → 检查 `trigger.group_at_only` / 唤醒词 / `cooldown_seconds` / `max_concurrent`。
2. 世界书不生效 → `worldbook.scan_depth`、`match_whole_words`（中文建议关闭，`\b` 边界不可靠）、正则键写法 `/pattern/flags`、`injection_cap`、递归层数。
3. 换卡没反应 → `permissions.switch_card_requires_admin`。
4. 角色卡导入失败 → 仅支持 `.json` / `.yaml` / 带 `tEXt` 的 `.png`，且需 V1/V2/V3。
5. 只有酒馆后端报 401 / CSRF → `st_cookie` 会过期，酒馆对非 GET 强制 CSRF；`st_verify_ssl` 自签名证书场景。
6. 回复被切成乱七八糟的多条 → `render.max_chars_per_message` / `segment_delay_ms` / `keep_leading_space`。
7. 状态栏/`<Status>` 没被剥掉 → `render.strip_status_bar` / `regex_rules` 格式 `正则=>替换`。
8. 提示"数据被清空"→ 引用现有的"不要把数据放进插件目录"警告（并把插件目录 vs 数据目录的树画出来）。

**⑥ 配置章节表格化**
- 现在 6 条 bullet（`trigger` / `worldbook` / `render` / `backend` / `permissions` / `debug`）改成 3~4 列表格（配置组 / 关键项 / 默认值 / 说明），或保留 bullet 但把 `_conf_schema.json` 里的 hint 抽上来做 `<details>` 逐键说明。
- 加一句固定路径文案：`插件管理 → astrbot_plugin_tavern → 操作 → 插件配置`（生态通用表述）。

**⑦ 新增 `## 适用平台` 表格**
按 `metadata.yaml` 的 `support_platforms` 写 4 行，用 ✅/❓ 标注实测程度：
`aiocqhttp`（QQ，实测）/ `telegram` / `discord` / `wecom` —— 主动消息那份 19 行表的"✅ 社区反馈可用 / ❓ 等待社区反馈"写法可以直接套。

**⑧ 把版本要求提到首屏**
- 徽章写 `AstrBot >= 4.17`（`metadata.yaml` 的 `astrbot_version: ">=4.17,<5"`），正文一行"已在 AstrBot 4.28.2 上完整实测（指令组注册、`chain_result`、`llm_generate`、`data.plugin_data`、aiocqhttp 反向 WS）"。
- 现在这句在第 144 行的「开发」小节里，属于埋没。

**⑨ 开发内容外移**
- 新建 `CONTRIBUTING.md`（或 `docs/DEVELOPMENT.md`）：把「目录结构」「开发」「发布体积」「tools/*.py 说明」「research/ 12.6MB 永不进包」搬过去。
- README 里只留 3 行：`python -m pytest tests -q`、`python tools/check.py`、`详细开发说明见 CONTRIBUTING.md`。
- 同时把 `## 致谢与许可` 的格式层保留（它是加分项）。

**⑩ 页脚补齐**
- `Contributors`（`https://contrib.rocks/image?repo=...`，可选）+ `Star History`（`api.star-history.com`）+ QQ 群/Issue 入口 + 一句"觉得有用请点 Star"。
- AGPL 三条含义的解释我们已有，保留。

**⑪ 可选但建议**
- `README_EN.md` + 头部 `简体中文 | English`（头部 2/6 做了，非必需）。
- `CHANGELOG.md`（页脚链过去，AstrBot ≥ 4.11.2 会识别）。
- `logo.png`。

**⑫ 不要抄的地方**
- 群分析的"没有安装章节、只给市场徽章"—— 我们还没上架，会害用户找不到装法。
- 主动消息的"开发者的话"长文与 AI 生成免责声明 —— 对本插件不是必要叙事。
- 天使之魂的"零截图" —— 它是记忆类插件，输出是文本；我们是角色扮演，输出是画面感，必须有图。

---

## 6. 未能核实的部分

1. **真实下载量/安装量：完全没有拿到**（§0.1 已列全部尝试与失败原因）。所有"热门"判断基于 GitHub star 与推荐清单，**不是下载量**。
2. **官方是否维护"推荐插件"榜：没有找到**。`docs.astrbot.app/community.html` 只有社区渠道，没有插件榜；`community-events/tonggujiyu-astrbot-plugin-reward-program.html`（插件奖励活动，可能含官方评选名单）**抓取失败**，未能读取。
3. **`docs.astrbot.app/dev/star/plugin-publish.html`（发布规范）抓取失败**，因此"市场对 README/仓库文件有哪些硬性要求"未能从官方正文核实，只能从插件仓库的实际做法（如 `logo.png` 适用于 ≥4.5.0、`CHANGELOG.md` 适用于 ≥4.11.2）反推。
4. **`plugin_cache_original.json`（676 KB）未拉取**，因此不能 100% 排除"另一份缓存里有下载量/star 字段"的可能；但正式规范 `plugins.json`（493 KB，实抓）确认没有。
5. **`topic:astrbot` 只有 377 个仓库且靠自愿打标签**，star 排序必然漏掉未打标签的插件；`astrbot_plugin_proactive_chat` 的旧仓库（`DBJD-CR`）就说明仓库迁移会让统计口径错位。
6. **第三方推荐清单只有一篇**（电玩帮 2026-05-20）；LINUX DO 两个帖子抓取失败，未能交叉验证。该文的配图全是懒加载占位符，**无法看到它推荐插件的截图**。
7. **未做深读的样本**：`anka-afk/astrbot_plugin_meme_manager`（407 star）本次只取到 star，没有做 README 结构分析。
8. 本文所有"行数/体量"的估算基于抓取到的渲染文本，**未逐行精确计数**（仅 `astrbot_plugin_bilibili` 有精确值 7321 B，来自 GitHub API）。星标数据截止 **2026-10-07**，数字会变。
