<div align="center">

<img src="logo.png" alt="astrbot_plugin_tavern" width="128" />

# astrbot_plugin_tavern · 酒馆角色扮演

*把 SillyTavern 的角色卡、世界书与聊天体验带进 AstrBot，在 QQ / 微信里玩角色扮演。*

![License](https://img.shields.io/badge/license-AGPL--3.0-7c6cf0)
![Python](https://img.shields.io/badge/python-3.10%2B-3776ab)
![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.17-4b8bf5)
![Version](https://img.shields.io/badge/version-0.1.0-d6aa60)

**简体中文** · [English](README_EN.md)

</div>

---

## 目录

| 上手 | 深入了解 |
| --- | --- |
| [✨ 功能](#-功能) | [⚙️ 配置](#-配置) |
| [🚀 安装](#-安装) | [❓ 常见问题](#-常见问题) |
| [📖 指令](#-指令) | [🧩 数据与目录](#-数据与目录) |
| [适用平台](#适用平台) | [⚠️ 已知边界](#️-已知边界) |

## ✨ 功能

<img src="docs/images/flow.png" alt="一条消息是怎么变成回复的" width="820" />

| 能力 | 说明 |
| --- | --- |
| **角色卡** | 导入酒馆的 `.json` / `.yaml` / `.png`（V1 / V2 / V3）。**把文件直接发给机器人**就能导入；卡里内嵌的 `character_book` 会自动拆成独立世界书。 |
| **世界书** | V2 / V3 / AgnAI / Risu / Novel 都能识别。关键词触发、正则键、选择性逻辑、递归、`sticky` / `cooldown` / `delay` 计时效果、token 预算，行为对齐酒馆源码。 |
| **多分支** | 每个群或私聊独立绑定角色卡，`/tavern new` 开新分支，`/tavern history` 回看，一个角色可以同时聊好几条线。 |
| **预设与宏** | 按酒馆的预设顺序组装消息，支持 `{{char}}`、`{{user}}` 等宏；稳定人设与动态上下文分离，不破坏 prompt 缓存。 |
| **计时效果** | `/tavern worldbook effect` 可以按会话实时开关条目的粘滞 / 冷却 / 延迟，和酒馆的 `/wi-set-timed-effect` 同一套语义。 |
| **上下文预算** | 填上模型的上下文长度，超出的旧对话整条丢弃，并始终保留最近几条与回复预留，避免请求被上游拒绝。 |
| **分段渲染** | 长回复按段落切分多条发送，模拟真人打字间隔；自动剥离模型自留的状态栏；可自定义输出正则。 |
| **管理台** | AstrBot 面板里有一个**图形化管理台**：总览、角色卡全文、世界书条目、会话与分支管理（切卡 / 开关世界书 / 新建改名删除分支 / 重载数据目录）、外部酒馆列表、以及只读的配置查看。 |
| **外部酒馆** | 可以只把生成代理给已部署的酒馆（保留它的 API Key 与预设），也可以**只读地**把酒馆里已有的角色卡、世界书、聊天记录拉进来。 |
| **失败回退** | 可选：酒馆连不上时改用 AstrBot 的模型继续回答。默认关闭；开启后**会在回复里明确说明发生了回退**，不会偷偷换掉模型。 |

一句话：**角色卡、世界书、预设、分支、计时效果、上下文预算、外部酒馆对接都在**，而且世界书引擎与消息组装是拿真酒馆逐字校验过的（见下面的[判定机](#-它是怎么保证和酒馆一致的)）。

## 🚀 安装

**推荐：插件市场安装**

在 AstrBot WebUI 的「插件管理 → 插件市场」搜索 `astrbot_plugin_tavern`，点击安装。

**命令行安装（尚未上架时也能用）**

```bash
plugin i https://github.com/ppepperkok-hue/astrbot_plugin_tavern
```

**手动安装**

把仓库放进 `data/plugins/`，**目录名必须是 `astrbot_plugin_tavern`**——它要和 `metadata.yaml` 里的 `name` 一致，否则 AstrBot 认不出这个插件。

装好后：

1. 在「插件管理 → 酒馆角色扮演 → 操作 → 插件配置」里按需调整（默认配置可以直接用）。
2. 私聊机器人说句话，或用 `/tavern import` 查看该把角色卡放到哪。
3. 群聊需要 @机器人，或以唤醒词开头（默认 `酒馆`）。

> [!NOTE]
> 依赖只有 `httpx`（调用外部酒馆时）以及可选的 `pillow`（读写 PNG 角色卡）与 `pyyaml`（导入 `.yaml` 卡）。都不装也能跑，只是对应功能不可用。

## 📖 指令

| 指令 | 参数 | 说明 | 别名 |
| --- | --- | --- | --- |
| `/tavern help` | — | 显示帮助 | `/酒馆 帮助` |
| `/tavern status` | — | 当前会话状态：绑定的卡、启用的世界书、分支 | `/酒馆 状态` |
| `/tavern list` | — | 列出所有角色卡 | `/酒馆 列表` |
| `/tavern use` | `<名字>` | 切换角色卡并开启新分支 | `/酒馆 换卡` |
| `/tavern card` | — | 查看当前角色卡详情 | `/酒馆 卡片` |
| `/tavern new` | — | 开启新分支（重开） | `/酒馆 重开` |
| `/tavern history` | `[条数]` | 查看最近聊天记录 | `/酒馆 记录` |
| `/tavern worldbook list` | — | 世界书列表（`*` 为已启用） | `/酒馆 世界书 列表` |
| `/tavern worldbook on\|off` | `<名字>` | 启用 / 关闭某本世界书 | `/酒馆 世界书 开\|关` |
| `/tavern worldbook effect` | `<书> <uid> <sticky\|cooldown\|delay> [on\|off]` | 查看或设置某个条目的计时效果，省略 `on\|off` 即查询 | `/酒馆 世界书 效果` |
| `/tavern reload` | — | 重新扫描数据目录 | `/酒馆 重载` |
| `/tavern import` | — | 查看该把文件放到哪 | `/酒馆 导入` |
| `/tavern preview` | `[文字]` | 预览本轮发给模型的**完整内容**（需在配置里打开 `debug.show_debug_in_chat`） | `/酒馆 预览` |
| `/tavern st cards` | — | 列出外部酒馆里的角色卡 | `/酒馆 酒馆 卡片` |
| `/tavern st books` | — | 列出外部酒馆里的世界书 | `/酒馆 酒馆 世界书` |
| `/tavern st chats` | `<卡片文件>` | 列出某个角色在酒馆里的聊天记录 | — |
| `/tavern st import` | `card <卡片文件>` \| `book <世界书名>` \| `chat <卡片文件> <聊天文件>` | 从酒馆导入到本地 | `/酒馆 酒馆 导入` |

两个真实的输出长这样：

```
/tavern list

角色卡列表（* 为当前）
   Iris - 示例角色
*  Lighthouse Keeper - 未知作者
切换：/tavern use <名字>
```

```
/tavern preview 我在哪

scope: aiocqhttp:GroupMessage:123456
card: Iris (54 chars description)
world books: Lighthouse Lore
activated entries: 1
  - [before_char] Always on: the lighthouse itself
blocks:
  - main: Write Iris's next reply, staying in character as Iris in a scene together with 小...
  - worldInfoBefore: The lighthouse is 44 metres tall and painted white.
  - personaDescription: (empty)
  - charDescription: Iris is a lighthouse keeper on a storm-battered coast.
  - charPersonality: Calm, laconic, unnervingly observant.
  - scenario: The player washes ashore after a shipwreck.
  - enhanceDefinitions: (empty)
  - nsfw: (empty)
  - worldInfoAfter: (empty)
  - dialogueExamples: 小明: Where am I? / Iris: The only dry rock for miles.
  - chatHistory: (empty)
  - jailbreak: Keep replies under four sentences.
messages: 3
messages (first 12):
  1. [system] Write Iris's next reply, staying in character as Iris in a scene together with 小明. /  / The lighthouse is 44 metres tall and painted white. /  / Iris is a light...
  2. [user] 小明: Where am I?
  3. [assistant] Iris: The only dry rock for miles.
  4. [system] Keep replies under four sentences.
```

（中间那几行被截断处是真实输出里的省略，不是排版。）

`/tavern preview` 展开的是**这一分块明细**：哪个世界书条目被激活、命中了哪条关键词、每个预设块各自贡献了什么、最终消息数组长什么样。排查「为什么它不知道这件事」时，看这里比看模型回复有用得多。

## ⚙️ 配置

路径固定是「插件管理 → 酒馆角色扮演 → 操作 → 插件配置」。下面按配置组列出**最常需要动**的项，完整逐键说明在配置页每一项的提示里。

| 配置组 | 关键项 | 默认 | 说明 |
| --- | --- | --- | --- |
| `trigger` | `private_always` | `true` | 私聊里所有消息都交给角色卡处理 |
| | `group_at_only` | `true` | 群聊只有 @机器人 才触发；关掉容易刷屏 |
| | `wake_prefixes` | `["酒馆"]` | 群聊里以这些词开头也会触发 |
| | `cooldown_seconds` | `3` | 冷却期内的消息不触发回复，避免刷屏与风控 |
| | `max_concurrent` | `1` | 超出的消息礼貌拒绝，保护上游模型与账号 |
| `worldbook` | `scan_depth` | `4` | 与酒馆默认一致；太小会导致「刚说过就失效」 |
| | `token_budget` | `1024` | `0` 表示不限制；超出时按插入顺序从低优先级丢弃 |
| | `match_whole_words` | `false` | **中文建议保持关闭**：`\w` 边界对中文不可靠 |
| | `injection_cap` | `20` | 单轮最多注入多少条，`0` 不限制 |
| | `allow_recursion` | `true` | 条目内容命中的关键词可再触发其他条目 |
| `render` | `max_chars_per_message` | `500` | 超出按段落切分多条发送 |
| | `segment_delay_ms` | `400` | 分段之间的间隔，模拟打字 |
| | `keep_leading_space` | `true` | 补零宽空格，防止平台把酒馆式缩进 strip 掉 |
| | `strip_status_bar` | `true` | 移除 `<Status>…</Status>` 这类模型自留状态块 |
| | `regex_rules` | `[]` | 每项 `正则=>替换`，按顺序执行 |
| `backend` | `type` | `astrbot` | `astrbot` 用已配置的模型；`sillytavern` 调用已部署的酒馆 |
| | `provider_id` | `""` | 留空则跟随当前会话的模型 |
| | `st_base_url` | `""` | 例如 `http://127.0.0.1:8000` |
| | `st_cookie` | `""` | 登录酒馆后从浏览器复制；会过期 |
| | `fallback_to_astrbot` | `false` | **酒馆失败时改用 AstrBot 的模型。默认关闭**：那是另一个模型，权重、预设、计费都可能不同 |
| | `fallback_notice` | `true` | 回退时在回复里说明。强烈建议保持开启——关掉之后模型会在对话中途被悄悄换掉 |
| | `max_context_tokens` | `0` | **`0` 表示不裁剪历史**；填上上下文长度后超出部分整条丢弃 |
| | `reply_reserve_tokens` | `1024` | 为回复预留的预算，长回复的角色卡可调大 |
| `permissions` | `switch_card_requires_admin` | `false` | 关掉后群成员可自行换卡，适合私人小群 |
| | `import_requires_admin` | `true` | 导入文件与从外部酒馆导入都受它约束 |
| `debug` | `show_debug_in_chat` | `true` | 允许 `/tavern preview` 回显完整 prompt |
| | `log_prompt` | `false` | 把注入内容与 token 估算写进日志，仅排查时开 |

## 适用平台

| 平台 | 状态 | 说明 |
| --- | --- | --- |
| `aiocqhttp`（QQ / OneBot v11） | ✅ 已实测 | 完整链路跑过真实 AstrBot + 假协议端：世界书注入 → 生成 → 分段回发 |
| `telegram` | ❓ 等待反馈 | 使用 AstrBot 的通用消息接口，理论上可用 |
| `discord` | ❓ 等待反馈 | 同上 |
| `wecom` | ❓ 等待反馈 | 同上 |

「已实测」指仓库里 `tools/qq_e2e.py` 会起一个真实的 AstrBot 进程与一个假 OneBot 客户端，断言整条链路。

踩过的坑、以及**为什么当初没被发现**，记在 [docs/known-issues.md](docs/known-issues.md)。

## ❓ 常见问题

<details>
<summary><b>群里 @ 了机器人却没反应</b></summary>

按顺序查这几项：`trigger.group_at_only`（关掉后所有群消息都会触发）、`trigger.wake_prefixes`（也可以用唤醒词开头）、`trigger.cooldown_seconds`（冷却期内的消息会被忽略）、`trigger.max_concurrent`（并发满了会礼貌拒绝）。另外确认 `enabled` 是开的。

</details>

<details>
<summary><b>世界书不生效 / 中文关键词匹配不上</b></summary>

- **中文请保持 `match_whole_words` 关闭**。整词匹配用的是 ASCII 词边界，中文词边界不可靠。
- `worldbook.scan_depth` 太小会导致「刚说过就失效」，酒馆默认是 4。
- 正则键写 `/pattern/flags`，例如 `/sword/i`。
- `worldbook.injection_cap` 会限制单轮注入条数，超出的不会出现。
- 条目内容想触发别的条目，需要 `worldbook.allow_recursion` 打开且递归层数够。

</details>

<details>
<summary><b>换卡没反应</b></summary>

`permissions.switch_card_requires_admin` 打开时只有管理员能换卡。私聊里你自己就是管理员；群里需要 AstrBot 管理员身份。

</details>

<details>
<summary><b>角色卡导入失败</b></summary>

只支持 `.json` / `.yaml` / `.yml`，以及带 `tEXt` 块的 `.png`（V1 / V2 / V3 规范）。用图片编辑软件重新存过的 PNG 会丢掉卡片数据块，这种情况下请用 `.json` 版本。发送文件时不需要额外指令，直接发给机器人即可。

</details>

<details>
<summary><b>用外部酒馆后端时报 401 或 CSRF 错误</b></summary>

酒馆除 `/api/users` 外的所有 `/api/*` 路由都在登录中间件后面，并且对非 GET 请求强制 CSRF。所以需要：登录酒馆后从浏览器复制会话 Cookie 填到 `backend.st_cookie`。**Cookie 会过期**，报 401 时先换一个新的。自签名证书的场景把 `backend.st_verify_ssl` 关掉。插件会自己取 `/csrf-token`，401/403 时会自动刷新一次再重试。

</details>

<details>
<summary><b>回复被切成乱七八糟的好几条</b></summary>

这是分段发送，不是 bug。调 `render.max_chars_per_message`（默认 500）和 `render.segment_delay_ms`（默认 400ms）。如果排版里的前导空格没了，那是平台侧 `strip()`，`render.keep_leading_space` 会用零宽空格保护它。

</details>

<details>
<summary><b>状态栏 / 模型自己写的 &lt;Status&gt; 没有被剥掉</b></summary>

打开 `render.strip_status_bar`。更一般的情况用 `render.regex_rules`，每项格式是 `正则=>替换`，例如 `^「(.*)」$=>$1`，按顺序执行。

</details>

<details>
<summary><b>提示数据被清空 / 导入的东西不见了</b></summary>

**不要把数据放进插件目录。** 插件的更新与重装会覆盖插件目录，数据会跟着丢。角色卡、世界书、预设、聊天记录都要放在 AstrBot 的数据目录下（见下一节）。

</details>

<details>
<summary><b>酒馆挂了会不会自动改用别的模型？</b></summary>

不会，除非你自己开。`backend.fallback_to_astrbot` **默认关闭**，酒馆失败时你会看到一条错误，而不是一个来路不明的回答。

打开之后，失败的那一轮会改用 AstrBot 已配置的模型继续，并且**在回复开头写明**「外部酒馆不可用（原因），本条回复由 AstrBot 的模型生成」。原因也会记在 `/tavern status` 里，因为一行提示会滚走。

如果你把 `fallback_notice` 也关掉，回退就真的静默了——模型会在对话中途被换掉而用户无从察觉。除非你完全清楚这一点，否则不要关。

</details>

<details>
<summary><b>聊久了请求被上游拒绝</b></summary>

`backend.max_context_tokens` **默认是 0，也就是不裁剪历史**。把它设成模型真实的上下文长度（例如 `8192`、`32768`、`128000`），超出的旧对话会被整条丢弃，同时始终保留最近 `keep_last_messages` 条与 `reply_reserve_tokens` 的回复预留。

</details>

## 🧩 数据与目录

```
<AstrBot 数据目录>/plugin_data/astrbot_plugin_tavern/
├── cards/         角色卡（.json / .yaml / .png）
├── worldbooks/    世界书（.json）
├── presets/       预设（.json）
└── chats/         聊天记录（每角色一个目录，酒馆兼容 .jsonl）
```

放在这里的数据不会因为插件更新而丢失。改动文件后在聊天里执行 `/tavern reload` 重新扫描。

## 🔍 它是怎么保证和酒馆一致的

这是这个项目里花力气最多、也最不容易看见的部分。

`tavern/st/` 这一层是**逐函数移植**酒馆的实现，而不是「照着功能重写」：世界书引擎、消息模型、消息组装、各家 provider 的报文转换。为了保证一致，仓库里有一套**判定机**：让 Node 跑**真正的酒馆源码**，让 Python 跑移植版，再逐字段比对。

| 判定机 | 覆盖 | 结果 |
| --- | --- | --- |
| `diff.py` | 世界书引擎 | 15 个夹具，12 一致 / 0 差异 / 3 不可比 |
| `diff_prompt.py` | 消息模型 | 一致 |
| `diff_assembly.py` | 消息组装 | 8 个夹具**全部一致** |
| `diff_converters.py` | provider 报文转换 | **121 个夹具全部一致** |

判定机之外还有几条护栏：导出覆盖检查（从快照解析清单，防止漏移植）、模块接线检查（报告有测试但没人调用的模块）、管理面板路由检查、指令参数检查、以及**发布包检查**——它会 `git archive` 出真正要发的那份 zip，解到一个**全新 AstrBot 根目录**里跑起来，因为工作区里有 `tests/`/`tools/` 和 `data/plugins/` 的联结，而发布包里**一个都没有**。这些和单元测试一起由 `python tools/check.py` 一键跑完（15 步）。

发布前还有一步**不在门禁里**：直接验 GitHub 提供的那份 zip（市场与安装器都走这个 endpoint）。

```powershell
.tools\uv-tools\astrbot\Scripts\python.exe tools/gh_archive_check.py
```

它故意不进门禁——依赖网络，而且**要等推送之后才有意义**。一个在你还没推的时候就报绿的检查，只会训练人忽略它。分层理由见 [docs/known-issues.md](docs/known-issues.md) 第 5 条。

真酒馆当裁判的价值在于它会推翻你的直觉。举两个真实例子：

- 整词匹配下 `C++` **会**匹配 `abcC++def`，因为两侧是 `\W` 而不是单词字符——我们一开始断言的是相反，是判定机纠正了预期。
- 整词匹配必须用 **ASCII** 的 `\w` 语义，否则中文关键词在中文正文里会**完全不匹配**。这是我们实际修过的一个 bug。

## ⚠️ 已知边界

- **单群聊。** 一个群或私聊绑定一张角色卡，没有多人群聊/多角色同场的玩法。
- **世界书的计时效果按会话轮次计。** 酒馆按聊天消息条数计；这是有意接受的差异（S1 夹具 11 通过）。
- **加权随机与 token 预算超限的条目选择不可比对。** 两边 RNG 流不同、tokenizer 不同，相关夹具标记为不可比对。
- **向量化条目**（`vectorized`）需要有语义匹配器；插件自带一个可注入的接口，但没有内置 embedding 服务。
- **`outlet` 注入位置**（`position: 7`）尚未实现。
- **外部酒馆是只读的。** 可以从酒馆拉取角色卡、世界书、聊天，但不会写入或删除酒馆里的任何文件。
- **没有为外部酒馆配置静默回退。** 回退是 `opt-in` 且默认关闭的；开启后会在回复里说明。一个会在对话中途悄悄换掉回答模型的行为，比一条错误更糟。

## 📄 许可证

[AGPL-3.0](LICENSE)。这意味着可以用、可以改、可以分发，但**分发或通过网络提供服务时必须一并提供完整源码**，且衍生作品同样按 AGPL-3.0 授权。

数据格式与行为语义来自 [SillyTavern](https://github.com/SillyTavern/SillyTavern)（同为 AGPL-3.0）。选择同一许可证，意味着可以合法地直接复用它的实现代码；来源与版权声明见 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。

## 🙏 鸣谢

- [SillyTavern](https://github.com/SillyTavern/SillyTavern) — 角色卡、世界书与聊天格式，以及本项目对齐的行为语义。
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) — 插件框架与平台适配。

<div align="center">

**[⬆ 回到顶部](#astrbot_plugin_tavern--酒馆角色扮演)**

</div>
