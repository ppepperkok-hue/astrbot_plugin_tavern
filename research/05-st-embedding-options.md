# 05 · 把酒馆引擎「直接塞进」AstrBot 插件的可行性与边界

> 调研范围：SillyTavern `release` 分支（1.19.0）+ 官方文档 + 社区既有实现。
> 交付物：本报告 + `research/_raw/` 下的原始文件与可复现脚本。
> 约束前提（来自 Lead，不再论证）：插件许可证已决定改为 **AGPL-3.0**；插件必须尽量小；保留「可选调用外部已部署酒馆」的后端。

---

## 0. 结论先行

**一句话：三个核心文件不能「原样搬进 Python 进程」，但「把世界书引擎原样搬进一个 Node 侧进程」在本机被实测跑通了；只是它进不了 16MB 市场包，所以最终推荐仍是「Python 自研引擎 + 用真酒馆引擎做开发期一致性 oracle」。**

七条硬结论，按重要性排序：

1. **「酒馆本体 sidecar」在 16MB 市场限制下无解，不用再讨论。**
   实测：SillyTavern 源码树（不含 `node_modules`、不含 `.git`）**1010 个文件 / 59.12 MB**；本机 `node.exe`（v22.22.1）单文件 **83.03 MB**。仅这两项就是 16MB 上限的 **8.9 倍**。
   来源：`gh api repos/SillyTavern/SillyTavern/git/trees/release?recursive=1`；`C:\Program Files\nodejs\node.exe` 实测大小。

2. **`public/scripts/world-info.js` 在 Node 里「能加载、能跑」，但要靠一层 shim 撑着。**
   本报告做了一次实测 PoC：为它的 **24 个静态 import / 94 个命名导出** 生成 Proxy 桩 + 5 个全局（`$`/`window`/`document`/`toastr`/`localStorage`）+ 一个 `fetch` 桩（实现 `/api/worldinfo/get`），然后 `import` 成功，`checkWorldInfo()` / `getWorldInfoPrompt()` **输出正确结果**：
   ```
   import ms        : 27
   scan ms          : 3
   worldInfoString  : "A dragon guards the {{user}} gate.The sword is rusty."
   convertCharacterBook keys/uid: 42
   engine bytes     : 271489     # world-info.js 原样（CRLF）
   shim tree bytes  : 20267  files: 24
   total bytes      : 291756
   ```
   脚本与完整输出：`research/_raw/poc_node_worldinfo_engine.mjs`、`research/_raw/poc_node_worldinfo_adapter_gen.py`、`research/_raw/poc_node_worldinfo_output.txt`。
   但**这个「能跑」是假象的一部分**：81 个顶层函数里只有 22 个是纯逻辑，另外 14 个依赖 ST 全局状态，剩下 45 个强绑定 DOM——被 Proxy 静默吞掉后，编辑器、导入导出、徽章、slash 命令全部形同虚设，真正可用的只有扫描内核那一条链。

3. **它是不是 ES module：是。** `package.json` 有 `"type": "module"`，`world-info.js` 用 `import` 语法，**不能 `require()`**（除非先打包），且 `import { Fuse } from '../lib.js'` 会传递性拉进 `public/lib.js` —— 那是个 webpack 客户端库汇总，里面是 `dompurify`、`@mozilla/readability`、`localforage`、`morphdom`、`bowser`、`@iconfu/svg-inject` 等**浏览器专用依赖**，在 Node 里根本无法求值。
   来源：`research/_raw/st_package.json`、`research/_raw/st_public_lib.js`。

4. **模块级副作用决定了「裸 import 必挂」。** `world-info.js` 第 62–63 行在模块顶层直接执行 jQuery：
   ```js
   const WI_ENTRY_HEADER_TEMPLATE = $('#entry_edit_template .world_entry');
   const WI_ENTRY_EDIT_TEMPLATE = $('#entry_edit_template .world_entry_edit');
   ```
   另外第 92 行 `new FilterHelper(...)`、第 882 行 `new StructuredCloneMap(...)`。所以哪怕只 import 不调用，也必须先备好 `$` 与两个类。

5. **路线 2（浏览器自动化）已经被社区实测过并被放弃，有明确数字。**
   `Youzini-afk/st-external-bridge` 的架构对比表直接给出 V1「浏览器代理」的代价：**内存 200MB/会话、并发 5–10、响应 3–5 秒、DOM 依赖「易碎」**；V2 改用服务端插件 + hooks 后变成 **1MB/会话、并发 100+、0.5–1 秒**。其 FAQ 原文：
   > **Q: 是否需要安装 Playwright？** V2 架构不需要。V1 浏览器代理模式需要，但已不推荐使用。
   另一条独立证据：`fannnnnnn5822/tavern-tanuki` 明确说「酒馆的提示词组装（预设/世界书/正则）都发生在浏览器侧，纯服务器 API 触发不了完整生成」，因此它也不用 Playwright，而是往酒馆前端注入一个连接器脚本 + 本地 WebSocket。
   补充事实：酒馆**自己**的端到端测试就是用 Playwright 跑的（`tests/playwright.config.js`、`tests/frontend/WorldInfoRenameChatLore.e2e.js`），所以「技术上自动化得了」，但那是**测试**（4 workers、fullyParallel、失败才录像），不是**产品集成**。

6. **官方文档没有任何「世界书格式规范」或「服务端 API 参考」，也没有官方/半官方的 prompt 组装 API。**
   ST-Docs 仓库全部 93 篇 markdown 里**没有 API reference 页**；`For_Contributors/` 只有 `index / st-script / Function-Calling / Provider-Integrations / Writing-Extensions / Server-Plugins / i18n` 七篇。官方给外部程序的正式扩展点只有四种：**STscript、Function Calling、Provider Integrations、Server Plugins（= 给酒馆自己加路由）**。
   仓库里也没有任何 JSON Schema 文件（`gh api search/code` for `in:path+schema` → `total_count: 0`）。**唯一被机器校验的格式是角色卡**，实现是 `src/validator/TavernCardValidator.js`（169 行，V1/V2/V3，且 V3 只校验 `spec === 'chara_card_v3'` + `3.0 ≤ spec_version < 4.0`）；**世界书连校验器都没有**，服务端只做了一句浅检查：
   ```js
   if (!('entries' in worldContent)) throw new Error('File must contain a world info entry list');
   ```
   → **结论：世界书「格式」的权威来源是 `world-info.js` 的行为本身，不是文档。** 这一点对我们有利：移植就是对着代码抄，不存在「文档 vs 实现」的二义性。

7. **许可证干净且不冲突。** SillyTavern：仓库根 `LICENSE` = **AGPL-3.0 全文**（34,523 B），`package.json` `"license": "AGPL-3.0"`，README 只有一行 `## License / AGPL-3.0`；文档 `LicenseCredits.md` 只声明原始 TavernAI 1.2.8 是 MIT。**AstrBot 本体也是 AGPL-3.0**（`repos/AstrBotDevs/AstrBot` → `license.spdx_id = "AGPL-3.0"`，另有 `EULA.md` 第 1 节明写「AstrBot 是一个遵循 GNU Affero General Public License v3（AGPLv3）协议发布的免费开源软件项目」）。AstrBot 官方对插件许可证**没有强制要求**，但开发规范第 9 条写明：
   > 如果使用、修改或移植了其他项目的代码或资源，请遵守原项目的开源许可协议，并按协议要求保留版权及许可声明。

**推荐排序：路线 3（Python 移植）＞ 路线 1b（vendored Node 迷你引擎，仅作开发期 oracle）＞ 路线 1（酒馆本体 sidecar，仅作可选外部后端）＞ 路线 2（浏览器自动化，排除）。**
详见 §6。

---

## 1. 实测环境与证据落盘清单

本机：Windows，Node v22.22.1（`C:\Program Files\nodejs\node.exe`，83.03 MB），Python 3.11.9，`gh.exe` 2.97.0（已登录 `ppepperkok-hue`）。
可用通道与 Lead 的索引一致；本次全部通过 `gh api ... /contents/<path>?ref=release` 取 base64 后本地解码，未使用 `web_fetch` 抓源码。

落盘到 `research/_raw/` 的原始文件（全部为 ST `release` = 1.19.0）：

| 文件 | 字节 | 说明 |
|---|---|---|
| `st_LICENSE` | 34,523 | SillyTavern 根 LICENSE 全文（AGPL-3.0） |
| `st_package.json` | 5,941 | 依赖清单 / `type: module` / `license` / `engines` |
| `st_package-lock.json` | 424,669 | 用于核查是否有原生模块 |
| `st_public_scripts_world-info.js` | 265,081 | 世界书引擎（6,408 行） |
| `st_public_scripts_openai.js` | 307,267 | Chat Completion 路径 + Prompt 组装（7,396 行） |
| `st_public_scripts_PromptManager.js` | 90,717 | Prompt Manager UI/数据（2,144 行） |
| `st_src_endpoints_characters.js` | 68,637 | 角色卡服务端 CRUD/导入导出（1,687 行） |
| `st_src_endpoints_worldinfo.js` | 5,336 | 世界书服务端读写（157 行） |
| `st_src_prompt-converters.js` | 55,910 | **服务端纯逻辑** prompt 转换器（1,451 行） |
| `st_src_validator_TavernCardValidator.js` | 4,561 | 唯一的格式校验器（角色卡） |
| `st_public_lib.js` | 3,085 | 客户端 webpack 库汇总（证明浏览器依赖） |
| `st_tests_playwright.config.js` | 364 | 官方 e2e 用 Playwright 的配置 |
| `st_tests_sample.e2e.js` | 395 | 官方 e2e 样例 |
| `st_default_config.yaml` | 16,790 | 默认配置（CSRF/server plugins 开关） |
| `st_public_scripts_tokenizers.js` | 42,875 | token 计数（客户端，强耦合） |
| `st_public_scripts_extensions.js` | 88,263 | `getContext()` / `extension_settings` 来源 |
| `dep_world-info.json` / `dep_openai.json` / `dep_PromptManager.json` | — | **函数级依赖探针的机器可读结果** |
| `poc_node_worldinfo_adapter_gen.py` | — | PoC 生成器（24 个 shim 模块 / 94 个命名导出） |
| `poc_node_worldinfo_engine.mjs` | — | PoC 运行器（全局桩 + 内存 `/api/worldinfo/get`） |
| `poc_node_worldinfo_output.txt` | — | PoC 完整输出（见 §0.2） |
| `astrbot_EULA.md` | 13,427 | AstrBot EULA（第 1、11 节讲许可证） |
| `astrbot_docs_zh_dev_star_plugin-new.md` / `plugin.md` / `plugin-publish.md` | — | AstrBot 插件开发/发布规范原文 |
| `stdocs_LicenseCredits.md` / `stdocs_Usage_worldinfo.md` / `stdocs_For_Contributors_Server-Plugins.md` | — | 酒馆官方文档正文 |

方法说明（供复现）：
```powershell
gh api "repos/SillyTavern/SillyTavern/contents/public/scripts/world-info.js?ref=release" |
  ConvertFrom-Json | % { [IO.File]::WriteAllBytes($dest, [Convert]::FromBase64String($_.content)) }
```
函数级依赖探针的实现：用括号匹配切出每个顶层 `function`，再对一份「DOM/全局/导入符号」正则表统计命中。脚本逻辑与结果见 `dep_*.json`。**注意：括号匹配在含有字符串内花括号时可能提前截断（`ChatCompletion` 类就是这样），所以对 class 的依赖统计以人工复核为准**——见 §4.4。

---

## 2. 问题 1：酒馆官方对「复用其引擎」的立场与文档依据

### 2.1 有没有权威的「世界书 / Prompt 组装 / 聊天存储」格式规范？

**没有。官方的态度是「这是实现细节，不是规范」。** 证据四层：

| 层 | 事实 | 证据 |
|---|---|---|
| 文档 | ST-Docs 仓库 93 篇 `.md`，**没有 API reference，没有 schema 页**；世界书只有一篇面向用户的说明 `Usage/worldinfo.md`（30,199 B），讲的是「这个字段在界面上是什么意思」，不是 JSON 结构 | `gh api repos/SillyTavern/SillyTavern-Docs/git/trees/main?recursive=1` |
| 文档 | 该页只给了唯一一个外部「详尽参考」链接，是**社区**文档：*World Info Encyclopedia* (rentry.co, by kingbri/Alicat/Trappu)，官方用词是 "Exhaustive in-depth guide" | `stdocs_Usage_worldinfo.md` → "Further reading" |
| 代码 | 仓库内**没有任何 JSON Schema 文件**（`search/code` with `in:path+schema` → 0） | `gh api search/code?q=repo:SillyTavern/SillyTavern+in:path+schema` |
| 代码 | 唯一校验器 `src/validator/TavernCardValidator.js` 只管**角色卡**；世界书服务端只查 `'entries' in body` | `research/_raw/st_src_validator_TavernCardValidator.js:124-141`；`st_src_endpoints_worldinfo.js:114-121` |

**角色卡**倒是有真正的规范，且是**外部规范 + 服务端校验器**：
- `TavernCardValidator.js` 头部注释直接引用外部规范仓库：`@link https://github.com/malfoyslastname/character-card-spec-v2`；
- V2 必填字段硬编码在代码里：`['name','description','personality','scenario','first_mes','mes_example','creator_notes','system_prompt','post_history_instructions','alternate_greetings','tags','creator','character_version','extensions']`；
- V2 内嵌世界书 `data.character_book` 只要求 `['extensions','entries']` 且 `entries` 是数组；
- V3 校验极浅：`spec === 'chara_card_v3'` 且 `3.0 <= spec_version < 4.0`。

**对我们的意义**：世界书引擎的「正确性」只能以 `public/scripts/world-info.js` 的行为为唯一判据。这意味着
① 移植时必须逐函数对齐代码而不是文档；
② 「用真酒馆引擎跑同一批 fixture，diff Python 输出」是唯一可信的验收方式（→ 推荐方案里的 oracle）。

### 2.2 有没有官方或半官方的「服务端 API」概念？

**有「服务端插件」机制，但没有「服务端 API」（没有世界书/Prompt 组装的服务端接口）。**

官方 `For_Contributors/index.md` 列出的全部扩展点（原文）：

> **[STscript](st-script)** … **[Function Calling](Function-Calling)** … **[External Provider Integrations](Provider-Integrations)** Choose between Custom OpenAI-compatible setup, a UI extension, a server plugin, or prior discussion for an official integration. … **[UI Extensions](Writing-Extensions)** UI extensions run in a browser environment … **[Server Plugins](Server-Plugins)** Server plugins allow adding functionality such as new API endpoints by running code in the NodeJS environment.

`Server-Plugins.md` 的关键原文：

> These plugins allow for adding functionality that is impossible to achieve using UI extensions alone, such as creating new API endpoints or using Node.JS packages that are unavailable in a browser environment.
> … You can register routes via the router that will be registered under the `/api/plugins/{id}/{route}` path.
> **Server Plugins are not sandboxed.** This means they can potentially gain access to your entire file system…

即：**服务端插件是「让你给酒馆加路由」的槽位，不是「酒馆暴露引擎给你」的 API。** 酒馆自带的 `/api/worldinfo/*`、`/api/characters/*`、`/api/chats/*` 只是浏览器前端的私有后端（`src/endpoints/*.js` 全是文件读写），既没有文档，也没有版本化契约。

这一点被第三方实现反复独立确认：
- `Youzini-afk/st-external-bridge`：V2 的「资源桥接」靠 `/api/plugins/.../v2/worldbooks` 这类**自己加的**路由，而「完整预处理（System Prompt、WIAN、采样参数、模板渲染、正则脚本）」是靠 `st-api-wrapper` 的 **Hooks**（前端注入）拿到的，不是酒馆 API。
- `fannnnnnn5822/tavern-tanuki`：原文「酒馆的提示词组装（预设/世界书/正则）都发生在浏览器侧，纯服务器 API 触发不了完整生成。所以连接器脚本在酒馆里开一条 WebSocket 连到本机 MCP 服务器」。

与我方 `03-sillytavern-formats-and-api.md` §7/§8 的结论完全一致（该报告已逐字核对 `/api/backends/chat-completions/generate` 的 payload 与 CSRF 栈），本报告不重复。

---

## 3. 问题 2：三条路线的可行性、体积与工作量

### 3.1 路线 1（Node 子进程 / sidecar）

拆成两个子方案，结论不同。

#### 1a. 把酒馆本体当 sidecar（`node server.js`）

| 项 | 实测/事实 |
|---|---|
| 源码体积 | release 树 **1010 files / 59.12 MB**（无 `node_modules`）；最大单文件是 `src/tokenizers/llama3.json` 6.08 MB、`gemma.model` 4.24 MB |
| 依赖 | `package.json` **96 个 runtime dependencies**（express / multer / lodash / jimp 全家桶 / tiktoken / sillytavern-transformers / archiver / ws / handlebars …），启动脚本 `"start": "node server.js"` |
| Node 版本要求 | `"engines": { "node": ">= 20" }` |
| 原生模块 | `package-lock.json` 中**未出现** `node-gyp` / `prebuild-install` / `node-addon-api` / `onnxruntime-node` / `sharp` / `better-sqlite3` / `canvas`（`onnxruntime-web` 出现 4 次，是 WASM 版）→ **锁文件层面判断不需要编译工具链**（⚠️ 仅锁文件静态核对，未实际 `npm install`，见 §7） |
| 是否可离线打包 | 可以（`npm ci` 后整目录打包），但体积 + 必须联网装依赖/带 `node_modules` |
| 是否被 16MB 拦住 | **是，无解**。83.03 MB 的单个 `node.exe` 就已经是上限的 5.2 倍 |
| AstrBot 插件带 Node 运行时的现实性 | **不现实**。AstrBot 插件安装走 pip（`requirements.txt`），zip 上限 16MB（官方原文：「发布到插件市场的插件压缩包（zip）大小**不得超过 16MB**。如果超过此限制，CI/CD 流水线将自动拒绝该发布请求。」）。Node 二进制既不能 pip 装，也不可能塞进 zip |
| 唯一可行形态 | **不打包，只当「用户已部署酒馆」的外部后端**——这正是我们已经决定保留的方案。注意它需要：`enableServerPlugins: false`（默认关）、`disableCsrfProtection: false`（默认开 CSRF）、`basicAuthMode: false`、`whitelistMode: true`；即调用方要处理 session cookie + `x-csrf-token` |

#### 1b. 自带一个「精简 Node 引擎」，直接 `import` 酒的 `world-info.js`

**这是本报告实测跑通的部分，也是最值得记录的一条。**

能不能在 Node 里被 require / import：
- **不是 CommonJS**，是 ESM（`"type": "module"`），所以是 `import`，不是 `require`；
- **24 个静态 import / 94 个命名导出**必须由调用方提供；
- `../lib.js` 是 webpack 客户端库汇总（`dompurify` / `@mozilla/readability` / `localforage` / `morphdom` / `bowser` / `@iconfu/svg-inject` / `showdown` / `moment` / `d3`… 不对，是 `seedrandom`/`Popper`/`droll`）；**其中多个是浏览器专用**，所以「先 `npm i` 让 import 解析成功」这条路在纯 Node 下仍然会炸；
- **模块顶层就用了 `$`**（第 62–63 行），以及 `new FilterHelper()`、`new StructuredCloneMap()`；
- 需要 `window` / `document` / `toastr` / `localStorage` 至少能被访问到。

**实测需要的 shim 面（可复现）**：

| shim | 数量 | 说明 |
|---|---|---|
| 桩模块 | 24 个文件 | 对应 24 条 import 语句 |
| 桩命名导出 | 94 个 | 泛型 Proxy，`apply`/`construct`/`get` 全通 |
| 其中换成**真实现**才跑得通的 | 43 个 | 主要是 `script.js`(19) + `utils.js`(13) + `extensions.js`(2) + `tokenizers.js`(1) + `StructuredCloneMap`/`FilterHelper`/`accountStorage`/`i18n.t`/`power_user`/`debounce_timeout`/`getRegexedString` … |
| 全局 | 5 个 | `$`、`window`、`document`、`toastr`、`localStorage` |
| 网络 | 1 个 | `fetch` 需实现 `POST /api/worldinfo/get`（`loadWorldInfo` 就靠它） |
| 源码改动 | **0 行** | `world-info.js` 原样不改 |

**代价与坑**：
- `checkWorldInfo` 里有一个 `if (shouldWIAddPrompt) {` —— 这是**对函数引用做真值判断**（永远为真），所以 shim 必须真的提供 `context.extensionPrompts['note'].value` 与 `context.setExtensionPrompt()`，否则直接 `TypeError`。这类「文档看不出来、只有跑一遍才知道」的耦合点，是这条路的主要风险来源。
- `getTokenCountAsync` 来自 `tokenizers.js`，真实现强耦合 `characters/this_chid/oai_settings/main_api/localforage` 和服务端 `/api/tokenizers/*`。**必须在 shim 里换成自己的计数器**——这就意味着「token 预算」这一块跟酒馆不可能 100% 对齐（除非连服务端 tokenizer 一起搬）。
- 体积上**非常漂亮**：`world-info.js` 265 KB + 适配层 ~20 KB ≈ **292 KB，0 个 npm 依赖**。
- 但**它仍然需要宿主有 Node**。AstrBot 插件不能pip 安装 Node；把「宿主存在 Node ≥ 18」当硬依赖，对 QQ bot 部署环境是很大的隐性成本。
- AGPL：vendored 的 `world-info.js` 是 AGPL 代码，按 AGPL §5 我们整包已经是 AGPL-3.0（已决定），**这条不构成阻碍**，只是必须保留版权与修改声明。

**1b 能不能进市场包？** 技术上能（292 KB），但只在「宿主有 Node」时才可用。**所以我把它定位为开发期工具，而不是运行期引擎**（见 §6）。

### 3.2 路线 2（浏览器自动化 / Playwright 驱动已部署酒馆）

| 维度 | 结论 | 证据 |
|---|---|---|
| 官方 UI 是否稳定可自动化 | **技术稳定**（酒馆自己拿 Playwright 跑 e2e），但**产品集成不稳定** | `tests/playwright.config.js`：`testMatch: '*.e2e.js'`, `baseURL: http://127.0.0.1:8000`, `workers: 4`, `fullyParallel: true`, 失败才录像/截图；`tests/frontend/WorldInfoRenameChatLore.e2e.js` 等 15 个 e2e 用例 |
| 每会话内存/并发 | **200 MB/会话、5–10 并发、3–5 秒响应**；作者自己的定性是「DOM 依赖 ✅ **易碎**」 | `Youzini-afk/st-external-bridge` README 架构对比表 |
| 是否被社区证明不可行并放弃 | **是。** 同一项目 V2 直接删掉浏览器路径，改为 server plugin + hooks，指标变成 1 MB / 100+ / 0.5–1 s；FAQ 明写「V1 浏览器代理模式需要 [Playwright]，但已不推荐使用」 | 同上 |
| 更轻的替代模式 | 不进 Playwright，而是**往酒馆前端注入 UI 扩展**（STscript/script library），由它开 WebSocket 连回本机；`play_send` → 浏览器 `createChatMessages` + `/trigger` → 等 `GENERATION_ENDED` | `fannnnnnn5822/tavern-tanuki` README「陪玩原理」 |
| 对 AstrBot 插件的额外要求 | 必须有**开着的浏览器标签页**；云酒馆（https）因混合内容限制连不上本机 `ws`；Playwright 自带浏览器约 1.5 GB，16MB 上限直接排除 | `tavern-tanuki`「⚠️ 云酒馆（https）因浏览器混合内容限制连不上本机 ws」；Playwright 体积为公开常识（本报告未实测，列 §7） |

**判定：排除（作为运行期方案）。** 如果非要「复用前端」，正确形态是注入式 UI 扩展 + WebSocket（`tavern-tanuki` 路线），但那要求用户安装酒馆扩展并保持标签页打开，与「插件尽量小、可离线」的目标冲突。

### 3.3 路线 3（Python 移植 / 本地实现）

**现状（本仓库已在跑的那一版，作者自己写的）：**

| 文件 | 体积 | 行数 |
|---|---|---|
| `tavern/st/worldbook.py` | 28,975 B | 746 |
| `tavern/st/prompt.py` | 38,393 B | 1,035 |
| `tavern/st/chat_store.py` | 33,853 B | 867 |
| `tavern/st/cards.py` | 12,022 B | 315 |
| **小计** | **113,243 B** | **2,963** |

对照被移植的对象：`world-info.js` 6,408 行 + `openai.js` 7,396 行 + `characters.js` 1,687 行 ≈ **15,491 行 JS → 2,963 行 Python**，压缩比约 **1:5.2**（因为 Python 版只做数据/算法，不做 UI）。

**已覆盖的机制**（在 `tavern/st/worldbook.py` 里 grep 到）：`sticky`(29)、`cooldown`(15)、`delay`(14)、`recursion`(34)、`min_activations`(6)、`outlet`(5)、`probability`(13)、`selective`(15)、`scan_depth`(16)、`whole_word`(14)、`token`(30)、`budget`(14)、`position`(31)、`depth`(28)、`constant`(5)、group scoring（`_apply_group_scoring`）、`AND_ALL`/`NOT_ANY`。

**已知缺口**（grep 计数为 0）：
- **inclusion group 的完整语义**（`filterByInclusionGroups` / `filterGroupsByScoring` / `filterGroupsByTimedEffects`，JS 里合起来 ~170 行）；
- **decorators**（`parseDecorators`，`@@activate` / `@@dont_activate`，JS 47 行）；
- **`\x01` 扫描缓冲分隔符的逐字对齐**（文档 §"Advanced Regex Per-Message Matching" 提到 ST 从 v1.12.6 起用 `\x01`；`world-info.js` 里 `get()` 用 `MATCHER = '\x01'` + `JOINER = '\n' + MATCHER`）；
- `scan_state` 命名对齐（Python 版没这个名字，行为靠流程实现）。

**「直接抄 AGPL 代码做移植」的工作量 vs 收益：**

| 做法 | 工作量 | 收益 | 法律 |
|---|---|---|---|
| 继续 clean-room 自研（现状） | 补 inclusion group + decorators + `\x01` 对齐 ≈ **3–5 人日**；补 token 计数对齐 ≈ 1–2 人日 | 无新增法律义务；可自称「未复制代码」 | 无 |
| 逐函数对抄 `world-info.js` 的算法细节（含变量名、分支结构） | **≈ 1–2 人日**就能把上面缺口补齐（因为不用反推语义，直接翻译） | 行为保真度显著提高，尤其 inclusion group 的平局/打分逻辑 | 变成衍生作品 → **必须 AGPL-3.0 整包 + 保留版权声明 + 声明修改** |

**关键判断：「移植」在 AGPL 下算不算衍生作品？** 算——只要译文体现了原作的选择与结构（FSF 的通行解释，且 AGPL §0 定义 "modified version" = 任何需要 copyright permission 的改写）。**但我们的前提已经把这个成本消化掉了**：插件本就是 AGPL-3.0（`LICENSE` 已是 AGPL 全文 + 版权头），AstrBot 本身也是 AGPL-3.0，所以**「抄」在法律上比「猜」更划算**。
→ **唯一必须同步修改的东西是那句声明**：`metadata.yaml` 第 5 行现在写着
> 「本插件复用了它的角色卡…、世界书…与聊天记录…格式语义，但**不包含**其任何代码或资源。」
一旦对抄，这行就是**假的**，AGPL §5(a)/(b) 要求「prominent notices stating that you modified it, and giving a relevant date」+「prominent notices stating that it is released under this License」。**必须改。**（详见 §5.2，这是本次调研发现的最重要的合规动作项。）

### 3.4 三条路线对比表

体积一栏的分母是 AstrBot 插件市场的 **16 MB zip** 上限（官方原文见 `astrbot_docs_zh_dev_star_plugin-publish.md`）。

| | 路线 1a · 酒馆本体 sidecar | 路线 1b · vendored 迷你 Node 引擎 | 路线 2 · Playwright 浏览器自动化 | 路线 3 · Python 引擎（现状 + 对抄） |
|---|---|---|---|---|
| **随插件分发的体积** | **不随包**（用户自部署，59 MB 源码 + 96 依赖 + 83 MB node.exe） | **292 KB**（引擎 265 KB + 适配 20 KB），但**要求宿主有 Node ≥ 18** | 不随包（Playwright + Chromium ≈ 1.5 GB，**必须**在宿主装） | **~113 KB Python**，随包，无额外运行时 |
| **依赖** | Node ≥ 20、`npm install` 96 包、默认需打开 `enableServerPlugins` | 0 个 npm 依赖，但要**自己维护 24 个桩模块 / 43 个真实现** | Python `playwright` + Chromium；被驱动的酒馆需开着浏览器 | 纯 stdlib（`tiktoken` 可选，已有近似 counter） |
| **工作量** | 中：写 HTTP 客户端 + cookie/CSRF；**但拿不到 prompt 组装**（必须另做引擎） | 小-中：本报告已验证 PoC（数小时级）；生产化需处理 token 计数与 3 处隐性耦合 | 中-大：且是**负收益**（引擎仍在酒馆侧，我们只是遥控 UI） | 补缺口 3–5 人日（clean-room）或 1–2 人日（对抄） |
| **离线安装** | ❌（要联网 `npm install`；除非打整包，那就更超 16MB） | ✅（前提是宿主已有 Node） | ❌（要下载浏览器） | ✅ |
| **风险** | 版本来回漂移；`/api/backends/*/generate` 是无状态转发 | 隐性耦合多（`shouldWIAddPrompt` 真值判断、token 计数、`loadWorldInfo` 走 fetch）；酒馆升级会改前端脚本 → 需锁版本；**一旦宿主没 Node 就整体不可用** | 已实测「易碎」；200MB/会话；国内部署基本不可行 | 保真度靠自己对齐；用 oracle 可量化解 |
| **AGPL** | 不传播（网络服务，触发 §13 源码可获取义务的是**酒馆**不是我们） | 传播 + 修改 → §5(a)/(b) 义务落在我们头上 | 不传播代码，但自动化酒馆 UI 可能触碰其使用条款（未核实，见 §7） | 对抄 = 衍生 → §5(a)/(b) 义务（**已决定接受**） |
| **判定** | **保留为「可选外部后端」**（已决定） | **降级为开发期 oracle**（不进运行期关键路径） | **排除** | **主引擎** |

---

## 4. 问题 3：三个核心文件的真实可复用边界（函数级清单）

依赖探针原始结果：`research/_raw/dep_world-info.json`（81 个函数）、`dep_openai.json`（101 个）、`dep_PromptManager.json`（2 个，因为它是 class 主体）。
下面 ★ = 纯逻辑（可脱离 DOM 复用），○ = 轻度耦合（依赖 ST 全局状态，但无 DOM），✖ = 强绑定 DOM / `SillyTavern` 全局对象。

### 4.1 `public/scripts/world-info.js`（6,408 行 / 265,081 B / 81 个顶层函数）

**★ 纯逻辑（22 个，探针判定无任何 DOM/全局命中）——这是移植时唯一值得逐字对抄的部分：**

| 行 | 函数 | LOC | 依赖 | 备注 |
|---|---|---|---|---|
| 795 | `getWorldInfoSettings` | 18 | 只读 `world_info_*` 全局 | 导出配置快照 |
| 2086 | `hideWorldEditor` | 3 | 无 | 空实现（真实现移到了别处） |
| 2104 | `addMissingWorldInfoFields` | 33 | `structuredClone` | 老世界书字段补全，**移植必抄**（决定默认值） |
| 2226 | `updateWorldEntryKeyOptionsCache` | 20 | 无 | 编辑器缓存，无复用价值 |
| 2756 | `setWIOriginalDataValue` | 11 | 无 | `originalData` 回写映射 |
| 2774 | `deleteWIOriginalDataValue` | 11 | 无 | 同上 |
| 2797 | `splitKeywordsAndRegexes` | 19 | 无 | **关键**：主键里可能有 `/re/flags`，切分规则逐字 |
| 2825 | `customTokenizer` | 54 | 无 | 编辑器用，无复用价值 |
| 2888 | `isValidRegex` | 3 | 无 | |
| 2901 | `parseRegexFromString` | 26 | 无 | **关键**：`/…/flags` → `RegExp`，含转义处理 |
| 3073 | `updatePosOrdDisplayHelper` | 12 | 无 | UI 文本 |
| 3954 | `getAutomationIdCallback` | 10 | 无 | UI |
| 3965 | `getOutletNameCallback` | 6 | 无 | UI |
| 4019 | `duplicateWorldInfoEntry` | 15 | `structuredClone` | |
| 4395 | `getFreeWorldEntryUid` | 15 | 无 | **关键**：新条目 uid 分配（取 `entries` 里最大 uid + 1） |
| 4137 | `createWorldInfoEntry` | 13 | `structuredClone` + `newWorldInfoEntryTemplate` | **关键**：新条目默认值 |
| 4652 | `parseDecorators` | 47 | 无 | **关键**：`@@activate` / `@@dont_activate`（`KNOWN_DECORATORS` 第 100 行） |
| 5292 | `filterGroupsByScoring` | 37 | `world_info_use_group_scoring` | **关键，Python 版缺口** |
| 5337 | `filterGroupsByTimedEffects` | 42 | 无 | **关键，Python 版缺口** |
| 5388 | `filterByInclusionGroups` | 88 | `Math.random` | **关键，Python 版缺口**（含 `removeAllBut`） |
| 6145 | `charSetAuxWorlds` | 3 | 无 | |
| 6149 | `updateAuxBooks` | 25 | `world_info.charLore` | |

**★ 加一个白名单例外：`class WorldInfoBuffer`（第 199–478 行，280 行）在探针里被算作"耦合"，但它实际是纯类。** 人工复核第 199–328 行：`#globalScanData` / `#depthBuffer` / `#recurseBuffer` / `#injectBuffer` / `#skew` / `#startDepth` 全部是私有字段，只读 `world_info_case_sensitive` 这类全局设置，**零 DOM**。它是整个引擎最值得抄的一块（`get()` 的 `MATCHER='\x01'`、`JOINER='\n'+MATCHER` 是逐字语义）。
`class WorldInfoTimedEffects`（第 479–794 行）则依赖 `chat_metadata`（18 次命中），是「聊天级状态」，需要外部注入但不碰 DOM → 也算可抄。

**○ 轻度耦合（依赖 ST 全局状态 / 导入的辅助函数，但无 `$`）：**

| 行 | 函数 | LOC | 主要耦合 |
|---|---|---|---|
| 892 | `getWorldInfoPrompt` | 24 | `eventSource.emit` —— 整体上层的入口，可复用（只要喂 `checkWorldInfo`） |
| 4590 | `getSortedEntries` | 55 | `eventSource`、`getStringHash`、`structuredClone`、`world_info_character_strategy` |
| 4709 | **`checkWorldInfo`** | **574** | `getContext()`、`eventSource`、`getTokenCountAsync`、`substituteParams`、`getRegexedString`、`getCharaFilename`、`shouldWIAddPrompt`、`chat_metadata`、`this_chid`、`toastr`、`extension_prompt_roles`、`metadata_keys` —— **引擎主体，最值得对抄的一块** |
| 4475–4588 | `getCharacterLore` / `getGlobalLore` / `getChatLore` / `getPersonaLore` | 51/16/19/25 | `loadWorldInfo`（→ `fetch('/api/worldinfo/get')`）、`selected_world_info`、`chat_metadata`、`power_user`、`characters`、`this_chid` |
| 5617 | `convertCharacterBook` | 58 | `extension_prompt_roles`（一个枚举！）—— V2 `character_book` → ST 原生条目，**移植必抄** |
| 5477/5522/5567 | `convertAgnaiMemoryBook` / `convertRisuLorebook` / `convertNovelLorebook` | 44/44/49 | 同上（`extension_prompt_roles.SYSTEM`） |
| 2036 | `loadWorldInfo` | 24 | `fetch` + `getRequestHeaders` + `worldInfoCache` |
| 2146 | `sortWorldInfoEntries` | 65 | `FILTER_TYPES`、`$` |
| 917 | `setWorldInfoSettings` | 117 | `$` 共 20+ 处、`accountStorage`、`eventSource`、`registerWorldInfoSlashCommands` —— **这是唯一的"外部改配置"入口，纯文本环境里只能靠 Proxy 吞掉 jQuery** |

**✖ 强绑定 DOM / 编辑器（59 个，不可复用）：**
`reloadEditor`、`registerWorldInfoSlashCommands`(961 行)、`showWorldEditor`、`updateWorldInfoList`、`getWIElement`、`nullWorldInfo`、`clearEntryList`、`displayWorldEntries`(376 行)、`verifyWorldInfoSearchSortRule`、`enableKeysInputHelper`、`handle*Helper` 全家（8 个）、`setCommentPlaceholder`、**`getWorldEntry`(512 行，导入导出解析，强 DOM)**、`buildAutocompleteCallback`、`getInclusionGroupCallback`、`createEntryInputAutocomplete`、`deleteWorldInfoEntry`、`_save`、`saveWorldInfo`、`renameWorldInfo`、`updateWorldInfoLinks`、`deleteWorldInfo`、`getFreeWorldName`、`createNewWorldInfo`、`setWorldInfoButtonClass`、`checkEmbeddedWorld`、`importEmbeddedWorldInfo`、`onWorldInfoChange`、`importWorldInfo`、`openWorldInfoEditor`、`assignLorebookToChat`、`moveWorldInfoEntry`、`charUpdatePrimaryWorld`、`charUpdateAddAuxWorld`、**`initWorldInfo`(234 行，启动装配)**。

**常量与枚举（必须逐字抄，散落在文件各处）：**
`world_info_insertion_strategy`(27)、`world_info_logic`(33)、`scan_state`(43)、`METADATA_KEY='world_info'`(94)、`SORT_ORDER_KEY`(93)、`DEFAULT_DEPTH=4`(96)、`DEFAULT_WEIGHT=100`(97)、`MAX_SCAN_DEPTH=1000`(98)、`KNOWN_DECORATORS=['@@activate','@@dont_activate']`(100)、`world_info_position`(855)、`wi_anchor_position`(866)、`newWorldInfoEntryDefinition`(4082)、`newWorldInfoEntryTemplate`(4127)、`originalWIDataKeyMap`(2687)、`defaultGlobalScanData`(186)。

> ✅ `newWorldInfoEntryDefinition` 与 `newWorldInfoEntryTemplate` 均在本次下载范围内（第 4082–4136 行），补上了我方 `03` 报告缺口清单的第 2 项。
> ✅ `checkWorldInfo` 完整函数体（第 4709–5282 行，574 行）+ `WorldInfoBuffer` + `WorldInfoTimedEffects` 也都在本地，补上了缺口清单的第 1 项。

### 4.2 `public/scripts/openai.js`（7,396 行 / 307,267 B / 101 个函数）

这是 Prompt 组装层。**关键结论：它整体不可复用，但其中 3 个 class 意外地干净。**

**★ `class TokenHandler`(3420) / `class Message`(3511) / `class MessageCollection`(3808) —— 人工复核第 3420–3916 行：`$(` 0 次、`document.` 0 次、`toastr` 0 次、`eventSource` 0 次、`promptManager` 0 次**，只有 `oai_settings` 3 次、`countTokensOpenAIAsync` 1 次。
→ **这是一整套「prompt 集合 + token 计数 + 消息模型」的纯数据结构，是整个 openai.js 里最值得借鉴的东西。**（探针把 `ChatCompletion` 的区间切错导致误判，见 §4.4）

| 行 | 函数 | LOC | 依赖 | 判定 |
|---|---|---|---|---|
| 729 | `parseExampleIntoIndividual` | 59 | `name1`、`selected_group` | ○ 宏/示例解析，可抄 |
| 789 | `formatWorldInfo` | 13 | `oai_settings.wi_format` | ★ 世界书包裹模板（`{0}` 占位），**可抄** |
| 1140 | `getPromptPosition` | 11 | `extension_prompt_types` | ★ 位置枚举映射 |
| 1157 | `getPromptRole` | 12 | `extension_prompt_roles` | ★ |
| 1185 | `populateChatCompletion` | 163 | `ToolManager`、`oai_settings`、`power_user`、`promptManager`、`substituteParams` | ○ **预算裁剪主循环，最值得对抄** |
| 1367 | `preparePromptsForChatCompletion` | 150 | `formatWorldInfo`、`promptManager.getPromptCollection/preparePrompt`、`oai_settings`、`power_user`、`extension_prompt_types` | ○ **组装顺序的权威实现** |
| 1542 | `prepareOpenAIMessages` | 83 | `new ChatCompletion()`、`eventSource`、`promptManager.render()`、`toastr` | ✖（因为 finally 里 `promptManager.render(false)`） |
| 570 | `setOpenAIMessages` | 80 | `oai_settings`、`name1`、`selected_group`、`MEDIA_TYPE` | ○ |
| 885 | `populateChatHistory` | 208 | `ToolManager`、`promptManager`、`substituteParams` | ○ 历史裁剪 |
| 1101 | `populateDialogueExamples` | 34 | `oai_settings`、`substituteParams` | ○ EM 分块 |
| 810 | `populateInjectionPrompts` | 76 | 未详查 | ○ |
| 2416/2486 | `sortModelsBy`/`groupModelsByVendor` | 63/52 | **无** | ★ 但与本项目无关 |
| 1722 | `getChatCompletionModel` | 61 | `oai_settings` | ○ 模型映射表 |
| 2691 | `createGenerationParameters` | 437 | `ToolManager`、`name1`、`power_user`、`proxies`、`substituteParams` | ○ 采样参数表，可参考 |
| 3138 | `sendOpenAIRequest` | 74 | `fetch`、`getEventSourceStream`、`getRequestHeaders`、`oai_settings` | ✖ 网络层 |
| 3917 | `class ChatCompletion` | ~665 | `$(` **33 次**、`document.` 2 次（如第 4327 行 `$('#settings_preset_openai').empty()`）、`oai_settings` 50 次 | ✖ 混杂：数据方法干净，但类里塞了 UI 预设渲染 |
| 6757 | `initOpenAI` | — | `$`、`eventSource`、`renderTemplateAsync` | ✖ 启动装配 |

**✖ 完全 DOM 的**：`setupChatCompletionPromptManager`、`add_msg`、`onModelChange`、`toggleChatCompletionForms`、`loadProxyPresets`、`setNamesBehaviorControls`、`onLogitBias*`、`on*Preset*`、`getStatusOpen`、`testApiConnection`、`reconnectOpenAi` 等约 60 个。

### 4.3 `src/endpoints/characters.js`（1,687 行 / 68,637 B）

**服务端文件，但≠可复用。它的依赖里有 `../jimp.js`（Jimp + wasm 编解码器）、`node-persist`（磁盘缓存）、`charx.js`、`byaf.js`、`character-card-parser.js`、`TavernCardValidator.js`。**

| 行 | 函数 | LOC | 依赖 | 判定 |
|---|---|---|---|---|
| **663** | **`convertWorldInfoToCharacterBook(name, entries)`** | **60** | **零依赖**（纯数据映射） | ★★ 但**未 export**（是模块私有函数）→ 想用只能抄袭一遍或起 Express 拿路由 |
| 450 | `getCharaCardV2` | 19 | `directories` | ★ 需要目录注入 |
| 469 | `convertToV2` | 29 | `directories` | ★ |
| 498 | `unsetPrivateFields` | 6 | 无 | ★ |
| 504 | `readFromV2` | 61 | `directories` | ★ |
| 565 | `charaFormatData` | 92 | `readWorldInfoFile`、`deepMerge`、`humanizedDateTime` | ○ 卡片归一化主逻辑 |
| 370 | `toShallow` | 36 | 无 | ★ |
| 406 | `processCharacter` | 44 | 文件系统 | ○ |
| 342/361 | `calculateChatSize` / `calculateDataSize` | — | 文件系统 | ○ |
| 181 | `readCharacterData` | 39 | `../jimp.js`（PngChunksExtract） | ✖ PNG tEXt 读取，需 Jimp 或自写 chunk 解析 |
| 220 | `writeCharacterData` | 63 | Jimp | ✖ |
| 283 | `applyAvatarCropResize` | 31 | Jimp | ✖ |
| 731–1020 | `importFromYaml/CharX/Byaf/Json/Png` | 5 个 × 40–90 | 文件系统 + Jimp + CharX + Byaf | ✖ |
| 1022–1687 | 15 条 `router.post(...)` | — | Express | ✖ |

**★ 结论：这个文件里真正「纯」的只有 `convertWorldInfoToCharacterBook`（60 行）和几个 6–30 行的归一化辅助函数；其余全与 Jimp / Express / 文件系统绑定。** 好消息是这 60 行我们已经有了（`tavern/st/cards.py` 覆盖了对应方向），坏消息是**它的 `extensions.*` 字段命名表是「世界书原生 → V2 character_book」的权威映射**，值得逐字核对——本次已下载完整（第 663–722 行，42 个字段，PoC 中验证 `Object.keys(...).length === 42`）。

补充：`src/endpoints/worldinfo.js`（157 行）只有 5 条路由（`/list`、`/get`、`/delete`、`/import`、`/edit`），**全部是文件读写，不含任何算法**；`readWorldInfoFile()` 是唯一可复用的函数（18 行）。

### 4.4 一个意外发现：`src/prompt-converters.js` 是服务端纯逻辑

`src/prompt-converters.js`（55,910 B / 1,451 行）**只 `import crypto from 'node:crypto'` 和 `./util.js`**，导出 **20 个函数** + `PROMPT_PROCESSING_TYPE` 常量，把 ST 内部的 messages 数组转成各家的 payload：

```
getPromptNames / addAssistantPrefix / postProcessPrompt / convertClaudePrompt / convertClaudeMessages /
convertCohereMessages / convertGooglePrompt / convertAI21Messages / convertMistralMessages /
convertXAIMessages / mergeMessages / convertTextCompletionPrompt / cachingAtDepthForClaude /
cachingAtDepthForOpenRouterClaude / cachingSystemPromptForOpenRouter /
calculateClaudeBudgetTokens / calculateGoogleBudgetTokens / embedOpenRouterMedia /
addReasoningContentToToolCalls / addOpenRouterSignatures / PROMPT_PROCESSING_TYPE
```

**意义**：
① 证明「Prompt 组装」这条链在酒馆里**不是全部在浏览器**——**末尾的 provider 适配层在服务端，而且是纯函数**，有 55 KB 的 Jest 测试（`tests/prompt-converters.test.js`）。
② 它接受的是**已经组装好的 messages 数组**；上游（角色卡字段 + 世界书 + PromptManager 排序 + 预算裁剪）仍然在浏览器。所以「搬运它 = 省掉我们写 provider 适配」是**真的**，但「搬运它 = 不用写引擎」是假的。
③ 这是我们插件**唯一真正值得考虑直接对抄的服务端文件**（本插件已按 provider 抽象，收益中等）。

### 4.5 依赖探针的已知误差（诚实声明）

括号匹配在遇到字符串/正则里的 `{` `}` 会提前结束区间。已确认的例子：`ChatCompletion`（第 3917 行起）被截成 352 行（实际 ~665 行），探针因此报告它「零依赖」，而人工 grep 显示区间内有 **33 处 `$(`、2 处 `document.`**。
→ **所有「★ 纯逻辑」结论都已在 §4.1/§4.2 里做了人工复核**（WorldInfoBuffer、Message/MessageCollection/TokenHandler 两块都逐行看过 import 与 grep）。其余标 ○/✖ 的条目即使有偏差也不影响结论方向。

---

## 5. 问题 4：许可证事实

### 5.1 SillyTavern 到底是什么许可

| 证据 | 内容 |
|---|---|
| 仓库根 `LICENSE` | **GNU AFFERO GENERAL PUBLIC LICENSE, Version 3, 19 November 2007** 全文，34,523 B，661 行（`research/_raw/st_LICENSE`） |
| `package.json` | `"license": "AGPL-3.0"` |
| `README.md`（release） | 全文只有 20 行，末节 `## License` → `AGPL-3.0` |
| 官方文档 `LicenseCredits.md` | 只有一句免责声明 + 一条：**"Original [TavernAI](https://github.com/TavernAI/TavernAI) 1.2.8 by Humi: MIT License"** ——即 MIT 的部分是历史项目 TavernAI，与当今 ST 代码无关 |
| 分支差异 | `release` 与 `staging` 同为 AGPL-3.0（`package.json` 一致）；无「商业双授权」痕迹 |

**结论：就是 AGPL-3.0，没有例外、没有附加条款（§7 additional terms 未见）、没有 CLA 式商业化口径。**

### 5.2 我们改成 AGPL-3.0 后，复用它的代码要做什么

AGPL-3.0 原文（本次逐字取回）关键段落：

**§5 Conveying Modified Source Versions（第 196–221 行）**
> You may convey a work based on the Program, or the modifications to produce it from the Program, in the form of source code under the terms of section 4, provided that you also meet all of these conditions:
> **a) The work must carry prominent notices stating that you modified it, and giving a relevant date.**
> **b) The work must carry prominent notices stating that it is released under this License** and any conditions added under section 7. This requirement modifies the requirement in section 4 to "keep intact all notices".
> **c) You must license the entire work, as a whole, under this License** to anyone who comes into possession of a copy. …
> d) If the work has interactive user interfaces, each must display Appropriate Legal Notices; however, if the Program has interactive interfaces that do not display Appropriate Legal Notices, your work need not make them do so.

**§13 Remote Network Interaction（第 540–551 行）**
> Notwithstanding any other provision of this License, if you modify the Program, your modified version must **prominently offer all users interacting with it remotely through a computer network** (if your version supports such interaction) **an opportunity to receive the Corresponding Source** of your version by providing access to the Corresponding Source from a network server at no charge…

**由此推出的具体义务清单（针对本插件）：**

| # | 义务 | 落地动作 | 现状 |
|---|---|---|---|
| 1 | 整包以 AGPL-3.0 发布 | 仓库根 `LICENSE` = AGPL-3.0 全文 | ✅ 已满足（36,249 B，含 `Copyright (C) 2026 ppepperkok-hue` 头部） |
| 2 | **保留上游版权与许可声明** | 加 `NOTICE` / `THIRD_PARTY_LICENSES.md`，写明 "This plugin contains code derived from SillyTavern (https://github.com/SillyTavern/SillyTavern), Copyright (c) SillyTavern contributors, licensed under AGPL-3.0. See LICENSE." | ❌ **缺失** |
| 3 | **声明「我们修改了它」并给出日期**（§5a） | 每个被对抄/移植的文件头加 provenance 注释：`Derived from SillyTavern public/scripts/world-info.js @ release/1.19.0 (2026-xx-xx), AGPL-3.0, modified.` | ❌ **缺失** |
| 4 | 声明「本作品以本许可发布」（§5b） | 同上文件头 + `metadata.yaml`/README 显式写出 | ❌ **缺失且当前陈述为假**（见下） |
| 5 | 源码可获取（§13） | 插件 repo URL 已写在 `metadata.yaml.repo`；README 里再点明一次 | 🟡 基本满足 |
| 6 | 修改日期 | 随每次对抄更新 | ❌ |

**⚠️ 本次调研最重要的合规动作项：**
`metadata.yaml` 第 4–5 行当前写着：

> 本插件复用了它的角色卡（Character Card V1/V2/V3）、世界书（World Info）
> 与聊天记录（.jsonl）格式语义，但**不包含**其任何代码或资源。

以及 `desc` 里的对应句「格式与行为语义参考 SillyTavern（AGPL-3.0），仅借鉴公开的数据格式与交互设计，未复制其代码、素材或预设文本。」

**一旦我们采用「对抄 AGPL 代码」的路线（路线 3 的快路径，或把 1b 的 vendored 引擎收进包里），这两处声明立即变成不实陈述**，且违反 AGPL §5(a)(b) 的 "prominent notices" 要求。必须同时改。这是**一句删除 + 一句新增**的成本，不构成阻碍，但绝不能不处理——AstrBot 官方规范第 9 条明确要求「按协议要求保留版权及许可声明」。

### 5.3 AstrBot 官方对插件许可证有没有要求

| 问题 | 结论 | 原文/证据 |
|---|---|---|
| AstrBot 本体许可证 | **AGPL-3.0** | `gh api repos/AstrBotDevs/AstrBot` → `license.spdx_id: "AGPL-3.0"`；`EULA.md` §1「AstrBot 是一个遵循 **GNU Affero General Public License v3（AGPLv3）** 协议发布的**免费开源软件项目**」；§11「在遵守本声明及 AGPLv3 协议的前提下…」 |
| 插件是否被强制要求某个许可证 | **没有。** 发布文档（`plugin-publish.md`）与插件市场 JSON 规范（`plugin-market/2026-06-27.md`）通篇没有 license 字段，也没有许可证要求 | `metadata.yaml` 解析字段只有 `name/display_name/short_desc/desc/version/author/repo/astrbot_version/support_platforms/social_link/tags`；市场规范必填字段只有 `author/name/version/repo/desc` |
| 但有没有「须遵守上游许可」的要求 | **有，且是明文的** | `docs/zh/dev/star/plugin-new.md:174` 与 `plugin.md:214`：**「如果使用、修改或移植了其他项目的代码或资源，请遵守原项目的开源许可协议，并按协议要求保留版权及许可声明。」** |
| 有没有署名/致谢要求 | **有** | 同两处第 173/213 行：「如果直接借鉴了其他项目的设计、功能创意或实现思路，请在 README 中清楚说明灵感来源并附上相关项目链接。」 |
| 16MB 限制 | 官方原文：「发布到插件市场的插件压缩包（zip）大小**不得超过 16MB**。如果超过此限制，CI/CD 流水线将自动拒绝该发布请求。」建议措施含「使用 `.gitattributes` 或发布分支」只包含发布所需文件 | `docs/zh/dev/star/plugin-publish.md` |

**所以：选 AGPL-3.0 与 AstrBot 生态完全一致（AstrBot 自身就是 AGPL-3.0），无冲突；官方不强制，但强制「照上游许可办事」。**

---

## 6. 问题 5：「直接塞进去」的现实定义与分步落地

### 6.1 先说清楚什么是「不能」

**不能真的把酒馆塞进 Python 插件。** 三条物理约束，任何一条单独成立就足够否决：

1. **语言边界**：ST 的世界书/Prompt 组装是 ESM 浏览器脚本（`"type": "module"`，`import` 语法，顶层 `$()`），Python 进程无法 `import`，跨语言只能走子进程/HTTP。
2. **体积边界**：59.12 MB 源码 + 96 个 npm 依赖 + 83.03 MB `node.exe` vs **16 MB zip 上限**。
3. **运行时边界**：AstrBot 插件靠 pip 安装依赖，**没有为插件提供 Node 运行时**的机制；要求宿主自带 Node ≥ 18 是一个部署级假设，不是一个可发布的依赖。

**但有三件「接近」的事是真的，而且已被实测/已被现有代码证明：**

- ✅ **可以把世界书引擎原样搬到一个 Node 侧进程里跑**（本报告 PoC：292 KB、0 npm 依赖、扫描 3 ms）；
- ✅ **可以把它的算法以 AGPL 允许的方式对抄进 Python**（本仓库已有 113 KB / 2,963 行成果，缺口只剩 inclusion group + decorators + `\x01` 对齐）；
- ✅ **可以把已部署的酒馆当数据源与生成代理**（`/api/worldinfo/*`、`/api/characters/*`、`/api/chats/*`、`/api/backends/chat-completions/generate`），但**拿不到 prompt 组装**——这最后一点已被三份独立来源确认（我方 `03` §7.4、`st-external-bridge`、`tavern-tanuki`）。

### 6.2 推荐方案（具体、可执行、尽量小）

```
QQ / 微信
   │
   ▼
AstrBot 插件（Python 进程，16MB 包内，纯 stdlib）
   ├─ 引擎层（插件自带，路线 3）        tavern/st/worldbook.py + prompt.py + cards.py + chat_store.py
   ├─ 生成层（二选一，可切换）          ① AstrBot provider（默认）  ② ST 后端 /api/backends/chat-completions/generate
   └─ 数据层（可选，路线 1a）            已部署酒馆的 /api/worldinfo|characters|chats（读，失败回退本地文件）
                                            ↑ 后端抽象已存在：tavern/backends/{base,astrbot_provider,sillytavern}.py

开发期（不进市场包）
   └─ tools/st-oracle/（路线 1b）         vendored world-info.js + Node adapter + fixture + jest-like diff
                                            ↑ 本报告 PoC 已跑通，直接可用
```

**具体到「插件目录里放什么」：**

| 放什么 | 是否进市场包 | 体积 | 说明 |
|---|---|---|---|
| `main.py` / `metadata.yaml` / `_conf_schema.json` / `pyproject.toml` / `README.md` / `LICENSE` | ✅ | 54,776 B | 现状 |
| `tavern/**`（4 个 st 模块 + core + config + main + backends） | ✅ | 200,121 B | 现状（`__pycache__` 必须排除） |
| `NOTICE` / `THIRD_PARTY_LICENSES.md` | ✅ 新增 | ~2 KB | AGPL §5(a)(b) 义务 |
| `tools/st-oracle/`（vendored `world-info.js` + adapter + fixtures） | ❌ **不进包**，走 `.gitattributes export-ignore` | ~1 MB | 开发期一致性 oracle |
| `research/**` | ❌ **必须排除** | **10.99 MB / 260 files** | ⚠️ **这是当前最大的体积风险**：research 目录已占 16MB 上限的 **69%** |
| `tests/**` / `.uv-cache` / `.tools` / `data` / `.pytest_cache` / `.ruff_cache` | ❌ | — | 开发资产 |

**实测当前可发布体积：254,897 B（0.24 MB）over 19 files** —— 距离 16MB 有 60+ 倍余量，**体积完全不是问题，问题只在别把 `research/` 打进去**。

**依赖：不新增任何东西。** Python 侧仅 stdlib（`tavern/st/prompt.py` 已有 `tiktoken` 可选降级路径：`_tiktoken_counter()` + `count_tokens()` 兜底）。**没有 npm、没有 Node、没有浏览器、没有 Playwright。**

**离线安装保证**：pip 侧零新依赖；`pytest` 只在 dev；`tiktoken` 缺失时走近似 counter（并接受 token 预算与酒馆有 ±误差，这就是 §4.5 里 `getTokenCountAsync` 无法 shim 的同一个问题）。

### 6.3 分步落地计划

| 步 | 动作 | 产出 | 验收 | 估时 |
|---|---|---|---|---|
| **P0** | **合规修复（先做，因为它不依赖任何技术选型）** | ① `NOTICE`/`THIRD_PARTY_LICENSES.md`；② 改掉 `metadata.yaml` 第 4–5 行与 `desc` 里的「不包含其任何代码」表述；③ 需要时在派生文件头加 provenance 注释 | `grep -r "不包含.*代码"` 无结果；`NOTICE` 含 SillyTavern 的 repo URL、AGPL-3.0、修改日期 | 0.5 人日 |
| **P1** | 把 `research/` 从发布包排除 | `.gitattributes` 增 `research/ export-ignore`、`tests/ export-ignore`、`tools/ export-ignore`；`metadata.yaml.desc` 里加 provenance 链接 | `git archive` 出的 zip < 300 KB 且不含 `research/` | 0.5 人日 |
| **P2** | 把本报告的 `dep_world-info.json` 当 checklist，逐条核对 `tavern/st/worldbook.py` | 一张「JS 函数 → Python 函数」映射表（差异即缺口） | 81 个 JS 函数中，★ 22 个 + ○ 14 个 + 2 个 class（`WorldInfoBuffer` / `WorldInfoTimedEffects`）全部有对应或显式豁免 | 0.5 人日 |
| **P3** | 补三个已知缺口：inclusion group、decorators、`\x01` 缓冲 | `worldbook.py` 新增/修改 | 见 P4 的 diff 通过 | 3–5 人日（clean-room）或 1–2 人日（对抄，且要回到 P0 补声明） |
| **P4** | **搭 st-oracle（路线 1b 生产化）**：把 PoC 从 `%TEMP%` 固定到 `tools/st-oracle/`，锁 ST 版本，写 fixture | `tools/st-oracle/run.mjs` + `fixtures/*.json`（世界书 + 聊天 + 期望输出） | `node tools/st-oracle/run.mjs` 输出与 Python 引擎**逐条 diff 全绿** | 1.5–2 人日 |
| **P5** | 补 `src/endpoints/characters.js:663` 的 42 字段映射核对（→ `tavern/st/cards.py`） | 映射表 + 单测 | 对一张真实 V2 卡 `convertWorldInfoToCharacterBook` 双向 round-trip | 0.5–1 人日 |
| **P6** | 保留并验证「外部酒馆后端」 | `tavern/backends/sillytavern.py` 增 cookie/CSRF、`enableServerPlugins` 无关性说明、失败回退 | 有 ST 实例时端到端跑通；无 ST 时静默回退 AstrBot provider | 1–2 人日（依赖用户提供 ST 环境） |
| **P7** | 可选：对抄 `src/prompt-converters.js` 的 provider 适配（Anthropic/Gemini 的 system 合并、caching） | 仅在我们需要「和酒馆输出逐字节一致」时做 | diff | 1–2 人日 |

**总计：MVP 增量 ≈ 6–8 人日；完整对抄版 ≈ 9–12 人日。** 对比 `03` 报告的「从零完整兼容 28–40 人日」，本方案省掉的部分正是「靠 oracle 把保真度问题从猜变成测」。

### 6.4 如果非要「真的塞进去」——唯一自洽的形态

只有当**同时**接受下面三条时，路线 1b 才能进运行期：
1. 宿主**保证**有 Node ≥ 18；
2. 接受 `world-info.js` 的 **59/81 函数被 Proxy 桩吞掉**，只使用扫描内核（`checkWorldInfo` / `getWorldInfoPrompt` / `convertCharacterBook` / `splitKeywordsAndRegexes` / `parseRegexFromString` / `parseDecorators` / `filterByInclusionGroups` 这条链）；
3. 接受自己实现 token 计数（`getTokenCountAsync` 不可复用）。

此时形态是：插件包内 `tavern/js_engine/`（292 KB：`world-info.js` + 24 个桩模块 + `index.mjs`），Python 侧通过 `subprocess` 或一个 Unix socket 调 `node index.mjs`，输入 `{worlds, chat, settings}`，输出 `{worldInfoBefore, worldInfoAfter, WIDepthEntries, ...}`。
**我不推荐它作为主路径**，理由是「宿主必须有 Node」这条假设在 QQ bot 部署里太脆，而它换来的收益（vs 对抄 Python）只是省 1–3 人日。**但它在开发期极其有用**——作为 oracle 它是零成本的（PoC 已经跑通）。

---

## 7. 未核实 / 存疑清单

| # | 事项 | 为什么没核实 | 建议补法 |
|---|---|---|---|
| 1 | 酒馆 `node_modules` 的实际磁盘体积 | 本机 HTTPS 出网坏（schannel），无法 `npm install`；锁文件不含 size | 在有网机器上 `npm ci && du -sh node_modules` |
| 2 | 「锁文件里没有 `node-gyp`/`prebuild-install`/`onnxruntime-node` → 安装不需要编译工具链」 | 只是静态判断；prebuilt binary 可能由包内 `postinstall` 下载 | 实际 `npm ci` 一次并看是否触发编译 |
| 3 | Playwright + Chromium 的精确安装体积（我引用的是"约 1.5 GB"） | 未实测、未查到官方数字 | 查 playwright 官方 "Browsers" 文档或实测 |
| 4 | 浏览器自动化酒馆是否违反 ST 的使用条款 / ToS | 未在 ST-Docs 找到 ToS 页面 | 读 `SECURITY.md`、Discord 公告、或直接问维护者 |
| 5 | `Youzini-afk/st-external-bridge` 的真实许可证 | README badge 写 **MIT**，GitHub API 返回 **AGPL-3.0** —— 自相矛盾 | 拉仓库根 `LICENSE` 文件确认；**在引用其性能数字时不影响结论（数字是自报）** |
| 6 | `st-external-bridge` 的性能数字（200MB→1MB、5-10→100+）是自报的 | 无第三方复现 | 视为「作者自述」，只用来证明「同一作者放弃了浏览器路线」这个事实 |
| 7 | `st-api-wrapper` 的许可证 | GitHub API `license: null`（无 LICENSE 文件） | 若我们要依赖它，必须先确认许可；**当前方案不依赖它** |
| 8 | ST 1.19.0 的 `src/endpoints/backends/text-completions.js` 是否有 `/generate` | 同 `03` 报告缺口 #3（gitcode 403） | 本次未补；走 `gh api` base64 即可 |
| 9 | ST `staging` 分支的 `world-info.js` 与我们锁定的 `release` 1.19.0 是否一致 | 只取了 `release` | 若要走 oracle 路线，必须锁 commit SHA 而不是分支名 |
| 10 | `metadata.yaml` 的「不包含其任何代码」是否已在 P0 修掉 | 本次任务禁止我改 `research/` 与 `05-*.md` 以外的文件 | Lead 安排（见 §6.3 P0） |
| 11 | 依赖探针对 class 的区间切分误差 | 括号匹配遇字符串内 `{`/`}` 会截断（已确认 `ChatCompletion` 被误判） | 建议改用 AST（`acorn`）重跑；当前结论已绕过该误差 |
| 12 | `world-info.js` 的 token 预算与酒馆服务端 tokenizer 的偏差量级 | 需要真实 tokenizer 才能测 | 用 `tiktoken` + 同一批文本比对 ST 的 `/api/tokenizers/*` 输出 |
| 13 | 16MB 上限是否按 zip 压缩后计算、`research/` 是否真的会被打包 | 官方只说「压缩包（zip）大小不得超过 16MB」；AstrBot 的打包脚本未核实 | 查 AstrBot CI 流水线脚本；或直接发布一次试 |

---

## 8. 参考链接（全部可点击）

**SillyTavern 源码（release，1.19.0）**
- 仓库树 / LICENSE：<https://github.com/SillyTavern/SillyTavern/blob/release/LICENSE>
- `public/scripts/world-info.js`：<https://github.com/SillyTavern/SillyTavern/blob/release/public/scripts/world-info.js>
- `public/scripts/openai.js`：<https://github.com/SillyTavern/SillyTavern/blob/release/public/scripts/openai.js>
- `public/scripts/PromptManager.js`：<https://github.com/SillyTavern/SillyTavern/blob/release/public/scripts/PromptManager.js>
- `public/lib.js`（浏览器库汇总）：<https://github.com/SillyTavern/SillyTavern/blob/release/public/lib.js>
- `src/endpoints/characters.js`（`convertWorldInfoToCharacterBook` 在第 663 行，**未导出**）：<https://github.com/SillyTavern/SillyTavern/blob/release/src/endpoints/characters.js>
- `src/endpoints/worldinfo.js`：<https://github.com/SillyTavern/SillyTavern/blob/release/src/endpoints/worldinfo.js>
- `src/prompt-converters.js`（服务端纯逻辑）：<https://github.com/SillyTavern/SillyTavern/blob/release/src/prompt-converters.js>
- `src/validator/TavernCardValidator.js`：<https://github.com/SillyTavern/SillyTavern/blob/release/src/validator/TavernCardValidator.js>
- `default/config.yaml`：<https://github.com/SillyTavern/SillyTavern/blob/release/default/config.yaml>
- 官方 Playwright e2e 配置：<https://github.com/SillyTavern/SillyTavern/blob/release/tests/playwright.config.js>
- 官方 World Info e2e 用例：<https://github.com/SillyTavern/SillyTavern/blob/release/tests/frontend/WorldInfoRenameChatLore.e2e.js>
- 角色卡规范（外部，ST 校验器引用）：<https://github.com/malfoyslastname/character-card-spec-v2>

**SillyTavern 官方文档**
- World Info：<https://docs.sillytavern.app/usage/worldinfo/>
- Prompt Manager：<https://docs.sillytavern.app/usage/prompts/prompt-manager/>
- Development and Automation（扩展点总览）：<https://docs.sillytavern.app/for_contributors/>
- Server Plugins：<https://docs.sillytavern.app/for_contributors/server-plugins/>
- Writing Extensions：<https://docs.sillytavern.app/for_contributors/writing-extensions/>
- License and credits：<https://docs.sillytavern.app/licensecredits/>

**社区既有实现（路线 1/2 的证据来源）**
- `Youzini-afk/st-external-bridge`（V1 浏览器代理 → V2 server plugin+hooks，含 200MB/5-10 并发/易碎 的自报数据）：<https://github.com/Youzini-afk/st-external-bridge>
- `Lianues/st-api-wrapper`（其依赖，无 LICENSE 文件）：<https://github.com/Lianues/st-api-wrapper>
- `fannnnnnn5822/tavern-tanuki`（前端注入 + WebSocket，独立确认「prompt 组装在浏览器侧」）：<https://github.com/fannnnnnn5822/tavern-tanuki>

**AstrBot**
- 发布插件到插件市场（16MB 原文）：<https://docs.astrbot.app/dev/star/plugin-publish.html>
- 插件开发（第 173/174 行的许可与署名要求）：<https://docs.astrbot.app/dev/star/plugin-new.html>
- 插件市场 JSON 规范：<https://docs.astrbot.app/dev/plugin-market/2026-06-27.html>
- AstrBot EULA（§1/§11 许可证）：<https://github.com/AstrBotDevs/AstrBot/blob/master/EULA.md>
- AstrBot 仓库许可证：<https://github.com/AstrBotDevs/AstrBot>

**本仓库内（同目录其它报告）**
- 索引与环境事实：[00-lead-index.md](00-lead-index.md)
- 角色卡/世界书/Prompt/聊天格式与服务端 API 逐字调研：[03-sillytavern-formats-and-api.md](03-sillytavern-formats-and-api.md)（本报告 §4 补上了它的缺口 #1、#2）
- 汇总报告：[REPORT.md](REPORT.md)
