# 06 · 酒馆官方文档与规范（世界书 / 角色卡 / Prompt / 聊天记录 / API）

> 调研方向：**SillyTavern 官方文档与权威格式规范**（不是第三方博客、不是源码推断）。
> 调研对象：`SillyTavern/SillyTavern` 分支 `release`；文档仓库 `SillyTavern/SillyTavern-Docs` 分支 `main`（commit 时点 = 本地抓取时，2026-09 之后的 release 内容）。
> 术语、字段名、代码保留英文；每条关键结论后都带**文件名或 URL**。凡是"文档没写、只能靠源码/实测"的地方一律标 **⚠️ 文档缺口**。
> 本报告只写入 `research/06-st-official-docs.md` 与 `research/_raw/`（原始摘录），未改动仓库其他内容。

---

## 0. 结论先行（12 条）

1. **官方文档没有"格式规范"章节。** 文档讲的是 UI 语义（"Insertion Order 数字越大越靠后"之类），**真正的字段级规范在三个仓库里**：`malfoyslastname/character-card-spec-v2`（V1/V2）、`kwaroran/character-card-spec-v3`（V3 + Lorebook + decorators），以及 ST 源码 `src/validator/TavernCardValidator.js`（判定 V1/V2/V3 的唯一权威实现）。我们已经把这三份原文抓到 `research/_raw/`。→ §2
2. **V3 规范里有一整个"独立世界书文件"格式 `{"spec":"lorebook_v3","data":Lorebook}`**，以及 `@@decorator` 语法（`@@depth`/`@@role`/`@@position`/`@@scan_depth`/`@@activate_only_after` …）。这两样官方文档**完全没提**，我们的实现也**完全没做**。→ §2.4、§7
3. **文档对世界书触发规则的覆盖度约 80%**：`scan_depth`、`constant`、`selective` + 四种 `selectiveLogic`、`position`/`depth`、`order`、`probability`、递归三开关、`sticky`/`cooldown`/`delay` 的时间语义、`Context % / Budget` 全部写了。**没写**的是：枚举数值、`ignoreBudget`、`addMemo`、`vectorized`/`keyvector` 的存储、`groupOverride`、`useProbability`、`min_activations`/`max_activations`、递归的算法细节。→ §3
4. **`order` 的方向是全篇最容易踩的坑，且文档自相矛盾**：文档说"**larger** order 会插入到更靠近 context 末尾"；V2/V3 规范说"**lower** insertion_order = inserted higher"；ST 源码 `const sortFn = (a, b) => b.order - a.order;`（**降序**）＋ 按此顺序消费 token 预算。结论：**ST 引擎语义 = 文档 = 降序（大 order 后插、优先占预算）**，我们实现一致。→ §3.3
5. **递归的"内容触发"在文档里写成"entry 的 content 提到别的 entry 的 keyword"**；源码里递归增量是 `fresh_texts`（entry content 的"两侧各加一个换行"的变体，见 `_entry_scan_text`），且 `Min Activations` 的额外扫描**不看递归新增条目**（文档明确写了这一句）。`delayUntilRecursion` 在 ST 里是**可带层级数字**（`entry.delayUntilRecursion = 1|2|…`），文档只把它描述成布尔。→ §3.5 ⚠️
6. **Prompt 组装有两条完全不同的路径**：Text Completion 走 Advanced Formatting / `Context Template`（Handlebars "Story String"，参数是 `{{description}}`/`{{scenario}}`/`{{personality}}`/`{{system}}`/`{{persona}}`/`{{wiBefore}}`/`{{wiAfter}}`/`{{mesExamples}}`…）；Chat Completion 走 **Prompt Manager**（拖拽块顺序 + `Position: Relative | In-Chat` + `Depth` + `Order` + `Role`）。**我们实现的是 Chat Completion 那条**（块级 `order`、`INJECTION_ROLE`），`Context Template` 那条完全没做。→ §4
7. **`depth` 的官方定义**（Prompt Manager）：`Position = In-Chat` 时，Depth 0 = 最后一条消息之后，Depth 1 = 最后一条消息之前，Depth 2 = 倒数第二条之前……**顺序与直觉相反，越大的 depth 越靠前（越"深"）**。Author's Note 页面给的是一致定义（Depth 4 = 成为 chat history 里第 4 个实体）。→ §4.3
8. **宏的官方清单很长**（`usage/macros.md`，824 行，按 8 个分类列出 ~90 个宏）：基础语法 `{{name}}` 大小写不敏感、参数用空格或 `::`、legacy 用单 `:`；`{{outlet::Name}}` 属 Utility 类。我们只实现 8 个（`char`/`user`/`time`/`date`/`weekday`/`isotime`/`isodate`/可选 `input`），且是正则 `[A-Za-z_][A-Za-z0-9_]*` —— **`{{outlet::X}}`、`{{getvar::x}}`、`{{random::a::b}}`、`{{trim}}` 这些带参数形式我们一个都匹配不到**。→ §4.5、§5.2
9. **`.jsonl` 的文件格式，官方文档一个字都没写。** `usage/characters/chatfilemanagement.md` 只讲"能导出/导入 .jsonl、包含所有 metadata、不含图片附件"。**header 行、消息行字段、`chat_metadata`、swipes、branches 的存储约定全部属于 ⚠️ 文档缺口**，只能以源码为权威（本报告给了源码逐字证据）。→ §5
10. **swipe 的真实存储**（源码，非文档）：消息行里 `swipes: string[]`、`swipe_id: number`、`swipe_info: [{send_date, gen_started, gen_finished, extra}]`，**且首条消息的 alternate greetings 会被物化成首个消息的 swipes**（`script.js getFirstMessage()`）。我们的 `ChatMessage` **不落盘 `swipes`/`swipe_id`/`swipe_info`**，只在 `from_st_dict` 里把未知键塞进 `extra` 保命 —— 单向可读写、双向不可兼容。→ §6.4
11. **API 侧**：官方文档没有任何"REST API 参考"。对外可编程的两条正规通道是 **UI Extensions**（`data/<user>/extensions/*/manifest.json` + `SillyTavern.getContext()`，文档 `For_Contributors/Writing-Extensions.md`，1400+ 行）和 **Server Plugins**（`plugins/` 目录 + `enableServerPlugins: true`，路由挂 `/api/plugins/{id}/{route}`，**官方明确警告不沙箱化**）。想"搬进 Python 插件"应走**文件格式兼容**（把 ST 的 data 目录当数据源），而不是调 HTTP。→ §8
12. **许可证**：官方文档首页明确 "released under the **AGPL-3.0 License**"（`readme.md` → `/LICENSE`）。我们已决定把插件改为 AGPL-3.0，法务前提成立；但注意 ST 的 **preset 内容**（`default/content/presets/openai/Default.json` 里的 prompt 文本）也属 AGPL 覆盖范围，我们 `prompt.py` 已经刻意不复制其原文（`DEFAULT_MAIN_PROMPT` 注明是自写），这个处理**策略上正确**，但**若后续要逐字复用 preset 文本，必须保留 AGPL 与来源声明**。→ §8.3

---

## 1. 调研方法（可复现的抓取配方）

| 用途 | 通道 | 结果 |
|---|---|---|
| ST 仓库任意文件 | `gh api "repos/SillyTavern/SillyTavern/contents/<path>?ref=release" --jq .content` → base64 解码 | ✅ 本次全部源码/规范都走这条（`raw.githubusercontent.com` DNS 失败、`gitcode` 对部分 `.js` 返回 403，故不再依赖镜像） |
| ST 官方文档 `.md` 源 | `gh api "repos/SillyTavern/SillyTavern-Docs/contents/<path>"`（**注意**：`--jq .content` 必须在 `cmd /c` 里跑，PowerShell 会把含空格的 jq 过滤器拆成多参数） | ✅ 27 个页面，落在 `research/_raw/doc_*.md` |
| 官方文档渲染页 | `https://docs.sillytavern.app/<route>.md` | ✅ 可直接抓（网页版路径含 `route:` frontmatter，如 `/usage/core-concepts/worldinfo.md`） |
| 角色卡规范 | `malfoyslastname/character-card-spec-v2`、`kwaroran/character-card-spec-v3` | ✅ 4 份原文落在 `_raw/` |

**踩坑记录（给后续接手的人）**：
- `gh api` 在 PowerShell 下 `--jq '... | ...'` 会被拆参；要么用 `cmd /c`，要么把 jq 写成不含空格的表达式。
- `contents` 端点对**大写目录名**（`Usage/Characters/...`）路径解析正常，但**必须走 `cmd /c` 重定向**才能拿到干净 base64；直接用 PowerShell 变量接会混入 stderr 文本。
- `docs.sillytavern.app/<path>/`（带尾斜杠的 HTML 路由）对 Retype 站点 **404**，只有 `.../<file>.md` 和部分 `route:` 路径能抓。

本报告引用的本地原始文件（都在 `research/_raw/`）：

| 文件 | 来源 | 大小 |
|---|---|---|
| `doc_Usage_worldinfo.md` | `SillyTavern-Docs/Usage/worldinfo.md` | 30 KB |
| `doc_Usage_characters_characterdesign.md` | `Usage/Characters/characterdesign.md` | 10.8 KB |
| `doc_Usage_Prompts_prompt-manager.md` | `Usage/Prompts/prompt-manager.md` | 10.5 KB |
| `doc_Usage_Prompts_index.md` | `Usage/Prompts/index.md` | 13.5 KB |
| `doc_Usage_Prompts_context-template.md` | `Usage/Prompts/context-template.md` | 6.4 KB |
| `doc_Usage_Prompts_advancedformatting.md` | `Usage/Prompts/advancedformatting.md` | 6.2 KB |
| `doc_Usage_Prompts_instructmode.md` | `Usage/Prompts/instructmode.md` | 6.0 KB |
| `doc_Usage_macros.md` | `Usage/macros.md` | 28.4 KB |
| `doc_Usage_Characters_chatfilemanagement.md` | `Usage/Characters/chatfilemanagement.md` | 2.9 KB |
| `doc_Usage_Characters_Author_s-Note.md` | `Usage/Characters/Author's-Note.md` | 2.7 KB |
| `doc_Usage_Characters_groupchats.md` | `Usage/Characters/groupchats.md` | 5.9 KB |
| `doc_Usage_Characters_data-bank.md` | `Usage/Characters/data-bank.md` | 14.8 KB |
| `doc_For_Contributors_Server-Plugins.md` | `For_Contributors/Server-Plugins.md` | 3.6 KB |
| `doc_For_Contributors_Writing-Extensions.md` | `For_Contributors/Writing-Extensions.md` | 57 KB |
| `doc_Administration_config-yaml.md` | `Administration/config-yaml.md` | 27.8 KB |
| `spec_v1_malfoyslastname.md` / `spec_v2_malfoyslastname.md` / `readme_ccsv2.md` | character-card-spec-v2 | 3.0 / 7.4 / 17.6 KB |
| `SPEC_V3_kwaroran.md` | character-card-spec-v3 | 48.2 KB |
| `st_src_TavernCardValidator.js` | `src/validator/TavernCardValidator.js` | 4.6 KB |
| `st_src_world-info.js` | `public/scripts/world-info.js` | 265 KB |
| `st_src_openai.js` | `public/scripts/openai.js` | 307 KB |
| `st_src_chats.js` | `src/endpoints/chats.js` | 44.5 KB |
| `st_src_public_script.js` | `public/script.js` | 497 KB |
| `st_default_openai_preset.json` | `default/content/presets/openai/Default.json` | 7.8 KB |

---

## 2. 文档地图（标题 · URL · 一句话）

**URL 规律**：渲染页 = `https://docs.sillytavern.app` + frontmatter 里的 `route:`；`.md` 源 = `https://docs.sillytavern.app<route>.md`（`index.md` 的 route 末尾就是 `/`，`.md` 写成 `<route>index.md`）。源码仓库路径 = `SillyTavern-Docs/<repo path>`。

> ⚠️ **抓取注意**：Retype 站点的**带尾斜杠 HTML 路由直接 `web_fetch` 会 404**（页面由前端路由渲染）。可靠性最高的读法是 **`<route>.md`**（本文所有 URL 均按此给）；若要拿"原始 repo 路径"，用 `gh api repos/SillyTavern/SillyTavern-Docs/contents/<path>`。

### 2.1 世界书 / Lorebook

| 标题 | URL | 内容 |
|---|---|---|
| World Info | <https://docs.sillytavern.app/usage/core-concepts/worldinfo.md> | 世界书唯一官方页面：Key/Optional Filter/Content/Insertion Order/**Insertion Position**/Outlet/Strategy/Probability/Inclusion Group/Prioritize Inclusion/Use Group Scoring/Automation ID/Character Filter/Triggers/Additional matching sources/Vector Storage Matching/Timed Effects/Activation Settings（Scan Depth、Include Names、Context % / Budget、Min Activations、Max Depth、Recursive scanning、Max Recursion Steps、Case-sensitive keys、Match whole words、Alert on overflow） |
| Character Lore / Persona Lorebook / Chat Lorebook | 同上，§"Context-Specific Sources" | 三种"上下文专属"世界书来源 + `Lore Insertion Strategy`（**Sorted Evenly 默认 / Character Lore First / Global Lore First**） |
| Data Bank（含 Vector Storage） | <https://docs.sillytavern.app/usage/core-concepts/data-bank/index.md> | RAG / 向量库；世界书的 🔗 Vectorized 匹配要求它开启 |
| World Info Encyclopedia（第三方，官方推荐） | <https://rentry.co/world-info-encyclopedia> | 官方页面 "Further reading" 指向它；**不是官方文档，但被官方认可** |

### 2.2 角色卡

| 标题 | URL | 内容 |
|---|---|---|
| Character Design | <https://docs.sillytavern.app/usage/core-concepts/characterdesign.md> | 唯一讲角色卡字段的页面：Description / Character tokens / First message / Alternate Greetings / Favorite / **Advanced Definitions**（Prompt Overrides、Creator's Metadata、Personality summary、Scenario、**Character's Note（@ Depth + Role）**、Talkativeness、Examples of dialogue 的 `<START>` 约定） |
| Characters（概览） | <https://docs.sillytavern.app/usage/characters/index.md> | 角色管理面板、导入/导出、标签、Advanced Definitions 面板入口 |
| Character Card V1 spec | <https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v1.md> | **规范原文**（6 必填字段 + 宏替换 + PNG `Chara` 元数据 + `<START>` 语义） |
| Character Card V2 spec | <https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md> | **规范原文**（`spec`/`spec_version`/`data.*` + `character_book` 的 TS 类型） |
| Character Card V3 spec | <https://github.com/kwaroran/character-card-spec-v3/blob/main/SPEC_V3.md> | **规范原文**（V3 新增字段、PNG `ccv3`、CHARX、Lorebook 对象、`lorebook_v3`、decorators、curly-braced syntaxes） |
| ST 的卡校验实现 | <https://github.com/SillyTavern/SillyTavern/blob/release/src/validator/TavernCardValidator.js> | V1/V2/V3 的判定与必填清单（**ST 侧真正执行的规则**） |

### 2.3 Prompt / Preset / 格式化

| 标题 | URL | 内容 |
|---|---|---|
| Prompts（概览） | <https://docs.sillytavern.app/usage/prompts/index.md> | prompt 由哪些东西拼成、如何查看（Prompt Itemization / Prompt Inspector）、Main Prompt、Post-History Instructions、World Info 在 prompt 中的位置 |
| Prompt Manager | <https://docs.sillytavern.app/usage/prompts/prompt-manager.md> | **块顺序**（拖拽列表，bottom = 最后发送）、Pinned/default prompts 清单、**Position: Relative / In-Chat**、**Depth**、**Order**、Role、Triggers；Utility Prompts（Format Templates 的 `{0}`/`{{scenario}}`/`{{personality}}`、Group Nudge、New Chat/New Example Chat、Continue Nudge、Replace Empty Message） |
| Advanced Formatting | <https://docs.sillytavern.app/usage/core-concepts/advancedformatting.md> | Text Completion 侧的 System Prompt、Context Template、Tokenizer、Custom Stopping Strings、Start Reply With、模板重置 |
| Context Template | <https://docs.sillytavern.app/usage/prompts/context-template.md> | **Story String** 的 Handlebars 参数全表、Prompt Anchors、Story String position（含 In-chat @ Depth）、Example Separator、Chat Start、Names as Stop Strings |
| Instruct Mode | <https://docs.sillytavern.app/usage/prompts/instructmode.md> | Instruct 序列/包裹、Include Names、Story String Sequences、Chat Messages Sequences |
| Macros | <https://docs.sillytavern.app/usage/macros.md> | 宏语法（`{{}}`、空格/`::`/legacy `:` 参数、嵌套、scoped、条件、flags、转义）＋ 8 个分类的宏清单 |
| Author's Note | <https://docs.sillytavern.app/usage/characters/authors-note.md> | A/N 的 Position/Depth（**Depth 0 = 最末尾，Depth 4 = 成为第 4 个实体**）、Insertion Frequency、A/N 也支持世界书触发扫描 |
| Common Settings / Tokenizer / Reasoning / CFG | `/usage/Common-Settings.md`、`/usage/prompts/tokenizer.md`、`/usage/prompts/reasoning.md`、`/usage/prompts/CFG.md` | 采样参数、分词器、推理模式、CFG |

### 2.4 聊天记录 / swipes / branches / 群聊

| 标题 | URL | 内容 |
|---|---|---|
| Chat File Management | <https://docs.sillytavern.app/usage/core-concepts/chatfilemanagement.md> | 导入来源、**Export as .jsonl**（"可原样再导入、包含所有 metadata、不含图片与附件"）、Export as .txt（不可再导入）、**Checkpoints / Create Branch**、Rename Chat |
| Chatting | <https://docs.sillytavern.app/usage/chatting/index.md> | 消息操作：Swipe the response、Edit and swipe、hotkeys |
| Group Chats | <https://docs.sillytavern.app/usage/core-concepts/groupchats.md> | Reply order strategies（Manual / Natural Order / List Order / Pooled Order）、Group generation handling（Swap / Join character cards）、Mute/Force Talk/Auto-mode/Allow Self Responses/Scenario Override |
| User Settings / Personas | `/usage/user-settings/index.md`、`/usage/personas.md` | "Prefer Char. Prompt / Prefer Char. Instructions"、Example Messages Behavior、persona 描述 |
| Branches（fork 页） | `/usage/branches.md` | **已废弃**：整页 `redirect: /installation/`，讲的是 ST 的 release/staging 分支，不是聊天 branch |

### 2.5 API / 扩展机制

| 标题 | URL | 内容 |
|---|---|---|
| API Connections | <https://docs.sillytavern.app/usage/api-connections/index.md> | 支持的后端清单（OpenAI 兼容、Claude、Google、OpenRouter、KoboldCpp、NovelAI、Tabby、Ooba…）、连接配置、Chat Completion 特有问题 |
| Connection Profiles | `/usage/api-connections/connection-profiles.md` | 连接配置档 |
| **Writing Extensions（UI Extensions）** | <https://docs.sillytavern.app/for-contributors/writing-extensions>（`For_Contributors/Writing-Extensions.md`，57 KB） | 扩展机制唯一权威：`manifest.json` 字段、`getContext()`、`chatMetadata`/`saveMetadata`、`writeExtensionField`、`extensionSettings`、Prompt Interceptors、事件（`MESSAGE_SWIPED` 等）、`generateRaw`/`generateQuietPrompt`、`macros.register()` |
| **Server Plugins** | <https://docs.sillytavern.app/for-contributors/server-plugins>（`For_Contributors/Server-Plugins.md`） | `{init, exit, info}`、路由 `/api/plugins/{id}/{route}`、`enableServerPlugins: true`、**"Server Plugins are not sandboxed"** |
| Function Calling / Provider Integrations | `/for-contributors/function-calling.md`、`/for-contributors/provider-integrations.md` | 工具调用与 provider 适配 |
| config.yaml | <https://docs.sillytavern.app/administration/config-yaml>（`Administration/config-yaml.md`，27.8 KB） | **没有 REST API 端点清单**，只有服务端配置（端口、`enableServerPlugins`、`skipContentCheck`、数据目录…） |

> **⚠️ 结论**：官方文档**不存在** REST API 参考页。可编程面 = UI 扩展 + Server Plugins + Slash commands/STscript（`/for-contributors/st-script.md`，54.8 KB）。**不存在**任何"外部程序读 ST 数据"的官方约定。

---

## 3. 权威格式规范原文（关键段落摘录）

### 3.1 V1（`spec_v1.md`）

来源：<https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v1.md>（本地 `_raw/spec_v1_malfoyslastname.md`）

```ts
type TavernCard = {
  name: string
  description: string
  personality: string
  scenario: string
  first_mes: string
  mes_example: string
}
```

要点（原文）：
- 所有字段 **mandatory**；缺失必须填 `""`，**不是** `null`/`undefined`。
- `description`/`personality`/`scenario`/`first_mes`/`mes_example` **MUST** 做大小写不敏感宏替换：`{{char}}` 与 `<BOT>` → `name`；`{{user}}` 与 `<USER>` → 应用侧显示名，且 user 名必须有默认值。
- 内嵌方式：PNG/APNG 的 `Chara` EXIF/tEXt 字段（base64 JSON）；`.json` 不推荐；**WEBP 规范未覆盖**。
- `mes_example` 以 `<START>` 分块；示例块 SHOULD 在上下文不足时**逐块**被挤出。

**ST 侧 V1 判定（源码逐字）**：`st_src_TavernCardValidator.js:55`
```js
validateV1() {
    const requiredFields = ['name', 'description', 'personality', 'scenario', 'first_mes', 'mes_example'];
    return requiredFields.every(field => Object.hasOwn(this.card, field));
}
```

### 3.2 V2（`spec_v2.md`）——必填 / 可选 / 枚举

来源：<https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md>（本地 `_raw/spec_v2_malfoyslastname.md`）

```ts
type TavernCardV2 = {
  spec: 'chara_card_v2'
  spec_version: '2.0'          // May 8th addition
  data: {
    name: string; description: string; personality: string;
    scenario: string; first_mes: string; mes_example: string
    // New fields
    creator_notes: string
    system_prompt: string
    post_history_instructions: string
    alternate_greetings: Array<string>
    character_book?: CharacterBook
    // May 8th additions
    tags: Array<string>
    creator: string
    character_version: string
    extensions: Record<string, any>
  }
}

type CharacterBook = {
  name?: string
  description?: string
  scan_depth?: number       // agnai: "Memory: Chat History Depth"
  token_budget?: number     // agnai: "Memory: Context Limit"
  recursive_scanning?: boolean
  extensions: Record<string, any>
  entries: Array<{
    keys: Array<string>
    content: string
    extensions: Record<string, any>
    enabled: boolean
    insertion_order: number        // if two entries inserted, lower "insertion order" = inserted higher
    case_sensitive?: boolean
    // FIELDS WITH NO CURRENT EQUIVALENT IN SILLY
    name?: string
    priority?: number              // if token budget reached, lower priority value = discarded first
    // FIELDS WITH NO CURRENT EQUIVALENT IN AGNAI
    id?: number
    comment?: string
    selective?: boolean            // true ⇒ 需要 keys 和 secondary_keys 各命中一个
    secondary_keys?: Array<string>
    constant?: boolean
    position?: 'before_char' | 'after_char'   // ← 枚举只有两个值
  }>
}
```

规范级 MUST/SHOULD（原文要点）：
- `spec` **MUST** = `"chara_card_v2"`，`spec_version` **MUST** = `"2.0"`。
- `creator_notes` **MUST NOT** 用于 prompt。
- `system_prompt`：前端**默认行为 MUST** 用它替换全局 system prompt；**空串时 MUST 回退**用户设置；**MUST** 支持 `{{original}}`。
- `post_history_instructions`：同上，替换的是 ujb/jailbreak 设置。
- `alternate_greetings`：**MUST** 在首条消息上提供 swipes，数组每一项一个 swipe。
- `character_book`：**MUST** 默认使用；**SHOULD** 与全局世界书**叠加**（角色书 **SHOULD** 优先）。编辑器 **MUST** 按此格式保存。
- `tags` / `creator` / `character_version` **MUST NOT** 用于 prompt。
- `extensions` **MUST** 默认为 `{}`；**MUST NOT** 销毁未知键。

**ST 侧 V2 判定（源码逐字）**：`st_src_TavernCardValidator.js:104`
```js
const requiredFields = ['name','description','personality','scenario','first_mes','mes_example',
  'creator_notes','system_prompt','post_history_instructions','alternate_greetings',
  'tags','creator','character_version','extensions'];
// 另要求：Array.isArray(data.alternate_greetings) && Array.isArray(data.tags) && typeof data.extensions === 'object'
// #validateCharacterBookV2(): character_book 存在时 requiredFields = ['extensions','entries']
//   且 Array.isArray(characterBook.entries) && typeof characterBook.extensions === 'object'
```

### 3.3 V3（`SPEC_V3.md`）——必填 / 可选 / 枚举

来源：<https://github.com/kwaroran/character-card-spec-v3/blob/main/SPEC_V3.md>（本地 `_raw/SPEC_V3_kwaroran.md`）

```ts
interface CharacterCardV3 {
  spec: 'chara_card_v3'
  spec_version: '3.0'
  data: {
    // fields from CCV2
    name: string; description: string; tags: Array<string>; creator: string;
    character_version: string; mes_example: string; extensions: Record<string, any>;
    system_prompt: string; post_history_instructions: string; first_mes: string;
    alternate_greetings: Array<string>; personality: string; scenario: string;
    // Changes from CCV2
    creator_notes: string
    character_book?: Lorebook
    // New fields in CCV3
    assets?: Array<{ type: string; uri: string; name: string; ext: string }>
    nickname?: string
    creator_notes_multilingual?: Record<string, string>
    source?: string[]
    group_only_greetings: Array<string>          // ← 注意：只有它是"非可选"的 V3 新字段
    creation_date?: number
    modification_date?: number
  }
}
```

**必填 vs 可选（按规范字面）**：
- 必填：CCV2 的 14 个 `data.*` 字段（V3 是 V2 的 superset）＋ `group_only_greetings: Array<string>`。
- 可选（`?`）：`character_book`、`assets`、`nickname`、`creator_notes_multilingual`、`source`、`creation_date`、`modification_date`。

**V3 新字段语义（摘录）**：
- `nickname`：`{{char}}`、`<char>`、`<bot>` **SHOULD** 替换为它而不是 `name`。
- `assets`：`uri` 可为 HTTP(S) URL / base64 data URL / `embeded://path/to/asset.png`（注意规范原文拼写就是 `embeded`，不是 `embedded`）/ `ccdefault:`。`type` 枚举实用值：`icon` / `background` / `user_icon` / `emotion`（其余交给应用）；`ext` **MUST** 小写、不带点。多个 `icon` 时 **MUST** 恰有一个 `name: "main"`。
- `source`：**SHOULD NOT** 由用户编辑，应用只追加、不改删非自己加的项。
- `creator_notes_multilingual`：key **MUST** 是 ISO 639-1 语言码（无 region）。

**嵌入方式（枚举）**：
- PNG/APNG：**MUST** 是名为 **`ccv3`** 的 tEXt chunk，值 = UTF-8 → base64 JSON；**若 `chara` 与 `ccv3` 同时存在，SHOULD 用 `ccv3`**。可从 `chara`（V2）backfill，但 **SHOULD** 在 `creator_notes` 里加警告文案。
- JSON：文件根 **MUST** 就是 CharacterCardV3 对象。
- CHARX：zip，**MUST** 有根级 `card.json`；资源 URI 用 `embeded://`；资产路径 `assets/{type}/images|audio|video|l2d|3d|ai|fonts|code|other/`。

**`spec_version` 比较规则（V3 原文）**：按 float 解析；`>3.0` 视为更新版本（**SHOULD** 仍允许导入并提示），`<3.0` 视为旧版本。

**Lorebook 对象（V3 把它抽成独立类型）**：
```ts
type Lorebook = {
  name?: string
  description?: string
  scan_depth?: number
  token_budget?: number
  recursive_scanning?: boolean
  extensions: Record<string, any>
  entries: Array<{
    keys: Array<string>
    content: string
    extensions: Record<string, any>
    enabled: boolean
    insertion_order: number
    case_sensitive?: boolean
    // V3 Additions
    use_regex: boolean
    // On V2 it was optional, but on V3 it is required to implement
    constant?: boolean
    // Optional Fields
    name?: string
    priority?: number
    id?: number|string
    comment?: string
    selective?: boolean
    secondary_keys?: Array<string>
    position?: 'before_char' | 'after_char'
  }>
}
// 独立导出的世界书文件：
{ spec: 'lorebook_v3', data: Lorebook }
```

关键规范句：
- `keys` **MUST** 是 string 数组；**命中即匹配**（大小写由 `case_sensitive` 决定，`undefined` 时应用自定）。
- `use_regex: true` ⇒ 按正则匹配 `keys`；正则非法 ⇒ **MUST** 视为不匹配；此时 `constant` **SHOULD** 被忽略，`secondary_keys` **SHOULD** 被忽略。
- `secondary_keys`：原文写的是 "**MUST** be a multiple values separated by a comma, as strings"（这条措辞与 TS 类型 `Array<string>` 冲突，⚠️ 规范自身不一致）。
- `content`：匹配 **MUST** 只插入一次；空则 **MUST NOT** 插入任何东西；content 内可含 decorators，插入前 **SHOULD** 剥掉并 trim 前后换行。
- `enabled: false` ⇒ **MUST NOT** 匹配 **IN ANY CASE**。
- `insertion_order`：**"the lower the number, it would be added to the prompt earlier"**（与 ST 文档 §3.4 的表述相反，见 §3.4 结论）。
- `token_budget`：超出时 **SHOULD** 先移除 priority 最低者；没有 `priority` 时可用 `insertion_order`。
- `recursive_scanning`：`false` ⇒ **MUST NOT** 因其他 entry 的 content 命中而匹配；`true` ⇒ **MAY** 命中（**不管 `scan_depth`**）。

**V3 decorators（官方文档零覆盖，⚠️ 重大缺口）**：
- 语法：`@@name value`，行首 `@@` 到行尾；多值逗号分隔；fallback 用 `@@@`；未识别的 decorator **SHOULD** 忽略。
- 已定义：`@@activate_only_after`、`@@activate_only_every`、`@@keep_activate_after_match`、`@@dont_activate_after_match`、`@@depth`、`@@instruct_depth`、`@@reverse_depth`、`@@reverse_instruct_depth`、`@@role`、`@@scan_depth`、`@@instruct_scan_depth`、`@@is_greeting`、`@@position`、`@@ignore_on_max_context`、`@@additional_keys`、`@@exclude_keys`、`@@is_user_icon`、`@@dont_activate`、`@@activate`、`@@disable_ui_prompt`。
- `@@depth N`：插到"从最新往旧数第 N 条"；`N > 总消息数` ⇒ 放在最旧消息**之前**；`N < 1` ⇒ 放在最新消息**之后**。`@@depth 0` + `@@role assistant` + 支持 prefill ⇒ **SHOULD** 作为 prefill。

**Curly braced syntaxes（V3 定义）**：`{{char}}`、`{{user}}`、`{{random:A,B,C...}}`、`{{pick:A,B,C...}}`、`{{roll:N}}`、`{{// A}}`、`{{hidden_key:A}}`、`{{comment: A}}`、`{{reverse:A}}` —— 注意与 ST 的宏（`::` 分隔、~90 个）**不是同一套**。

### 3.4 三种 order 语义的对照（结论）

| 出处 | 原文 | 语义 |
|---|---|---|
| ST 官方文档 `worldinfo.md` §Insertion Order | "Entries with **larger** order numbers will be inserted closer to the end of the context" | **大 = 靠后 = 影响大** |
| V2 规范 `spec_v2.md` | `insertion_order: number // if two entries inserted, **lower** "insertion order" = inserted higher` | 小 = 靠前 |
| V3 规范 `SPEC_V3.md` §insertion_order | "the **lower** the number, it would be added to the prompt earlier" | 小 = 靠前 |
| ST 源码 `world-info.js:88` | `const sortFn = (a, b) => b.order - a.order;`（降序）＋ `world-info.js:5203` `[...allActivatedEntries.values()].sort(sortFn).forEach(...)` | **大 order 先处理/更靠后**，与官方**文档**一致 |

→ **两套说法本质相同**（"大 order = 靠后插入" ≡ 排在前面的元素 order 更小），但**措辞方向容易看反**。我们 `worldbook.activate()` 的 `activated.sort(key=lambda item: (item[1].insertion_order, item[1].display_index))`（升序）与 ST 的降序**在预算消费顺序上不同**，但**最终文本拼接顺序一致** —— 见 §6.2 的 A2/B1。

### 3.5 世界书字段表（ST 原生格式 vs V2/V3 lorebook 形态）

**两套字段名并存，这是最关键的兼容事实**：

| 概念 | ST 原生（`data/<user>/worlds/*.json`） | 卡内 `data.character_book.entries[]`（V2/V3） |
|---|---|---|
| 容器 | `{"entries": {"<uid>": {...}}}` | `{"entries": [ {...} ]}`（**数组**） |
| 主键 | `key: string[]` | `keys: string[]` |
| 次键 | `keysecondary: string[]` | `secondary_keys: string[]` |
| 顺序 | `order: number`（默认 100） | `insertion_order: number` |
| 开关 | `disable: boolean` | `enabled: boolean`（**取反**） |
| 位置 | `position: 0..7`（数字） | `position: 'before_char' \| 'after_char'`（字符串，只有两值） |
| 正则 | `key` 里直接写 `/re/flags` | V3 的 `use_regex: boolean` |
| 备注 | `comment` | `comment` |
| 深度 | `depth`（默认 4） | 无（V3 用 `@@depth` decorator） |
| 角色 | `role`（数字 0/1/2） | 无 |

**ST 原生 entry 的完整字段表（源码 `world-info.js:4085-4125` 的 `newWorldInfoEntryDefinition`，权威）**：

```js
// name / 默认值 / 类型 —— 逐字
comment:               { default: '',    type: 'string' }
content:               { default: '',    type: 'string' }
constant:              { default: false, type: 'boolean' }
vectorized:            { default: false, type: 'boolean' }
selective:             { default: true,  type: 'boolean' }   // ← 注意默认 true
selectiveLogic:        { default: 0,     type: 'enum' }      // world_info_logic.AND_ANY
addMemo:               { default: false, type: 'boolean' }
order:                 { default: 100,   type: 'number' }
position:              { default: 0,     type: 'number' }
disable:               { default: false, type: 'boolean' }
ignoreBudget:          { default: false, type: 'boolean' }
excludeRecursion:      { default: false, type: 'boolean' }
preventRecursion:      { default: false, type: 'boolean' }
matchPersonaDescription:   { default: false, type: 'boolean' }
matchCharacterDescription: { default: false, type: 'boolean' }
matchCharacterPersonality: { default: false, type: 'boolean' }
matchCharacterDepthPrompt: { default: false, type: 'boolean' }
matchScenario:             { default: false, type: 'boolean' }
matchCreatorNotes:         { default: false, type: 'boolean' }
delayUntilRecursion:   { default: 0,     type: 'number' }    // ← number，可为层级
probability:           { default: 100,   type: 'number' }
useProbability:        { default: true,  type: 'boolean' }
depth:                 { default: 4,     type: 'number' }
outletName:            { default: '',    type: 'string' }
group:                 { default: '',    type: 'string' }
groupOverride:         { default: false, type: 'boolean' }
groupWeight:           { default: 100,   type: 'number' }
scanDepth:             { default: null,  type: 'number?' }
caseSensitive:         { default: null,  type: 'boolean?' }
matchWholeWords:       { default: null,  type: 'boolean?' }
useGroupScoring:       { default: null,  type: 'boolean?' }
automationId:          { default: '',    type: 'string' }
role:                  { default: 0,     type: 'enum' }
sticky:                { default: null,  type: 'number?' }
cooldown:              { default: null,  type: 'number?' }
delay:                 { default: null,  type: 'number?' }
characterFilterNames:  { default: [], excludeFromTemplate: true }   // 落盘 key = character_filter.names
characterFilterTags:   { default: [], excludeFromTemplate: true }   // 落盘 key = character_filter.tags
characterFilterExclude:{ default: false, excludeFromTemplate: true } // 落盘 key = character_filter.isExclude
triggers:              { default: [], arrayFilter: v => GENERATION_TYPE_TRIGGERS.includes(v) }
```

配套的 `world_info_position` / `world_info_logic` / 映射表（逐字）：

```js
// world-info.js:855
export const world_info_position = { before:0, after:1, ANTop:2, ANBottom:3, atDepth:4, EMTop:5, EMBottom:6, outlet:7 };
export const wi_anchor_position   = { before:0, after:1 };

// world-info.js:33
export const world_info_logic = { AND_ANY: 0, NOT_ALL: 1, NOT_ANY: 2, AND_ALL: 3 };

// world-info.js:2689-2722 —— ST 内部字段名 ↔ extensions 落盘名
'excludeRecursion': 'extensions.exclude_recursion',
'preventRecursion': 'extensions.prevent_recursion',
'delayUntilRecursion': 'extensions.delay_until_recursion',
'matchWholeWords': 'extensions.match_whole_words',
'useGroupScoring': 'extensions.use_group_scoring',
'caseSensitive': 'extensions.case_sensitive',
'matchPersonaDescription': 'extensions.match_persona_description',
'matchCharacterDescription': 'extensions.match_character_description',
'matchCharacterPersonality': 'extensions.match_character_personality',
'matchScenario': 'extensions.match_scenario',
'matchCreatorNotes': 'extensions.match_creator_notes',
'scanDepth': 'extensions.scan_depth',
'automationId': 'extensions.automation_id',
'triggers': 'extensions.triggers',
'selectiveLogic': ...      // 反序列化时 entry.extensions?.selectiveLogic
'outletName': 'extensions.outlet_name'
```

（对照实现 `world-info.js:5637-5668` 的 `parse()`：`delayUntilRecursion: entry.extensions?.delay_until_recursion ?? false`，但编辑器里它其实是 `true | number`，见 §3.5 的 `3801` 行 `['number','string'].includes(typeof entry.delayUntilRecursion)`。）

---

## 4. 世界书触发算法（**只依据官方文档能确认的部分**）

以下每条都是 `doc_Usage_worldinfo.md` 的原文语义转述；标 ⚠️ 的是文档没写清的。

### 4.1 扫描窗口

- **Insertion**：引擎是"动态字典"，只在关键词出现在被扫描文本里时插入（页面开头）。
- **`Scan depth`**：定义"扫多少条 chat history"。**0 ⇒ 只评估 recursed entries 与 Author's Note**；**1 ⇒ 只扫最后一条**；2 = 最后两条。（⚠️ 文档没说 0 时 `constant` 条目是否还算触发 —— 源码里 `WorldInfoBuffer.get()` 在 `depth <= startDepth` 时返回空串，`constant` 走的是另一条不依赖 haystack 的路径，**需源码/实测确认**。）
- **`Include Names`**（默认开）：扫描文本里给每条消息加 `Alice:` / `Bob:` 前缀；关掉就没有前缀。源码里前缀分隔符是 `\x01`（v1.12.6+）：`MATCHER = '\x01'`，`JOINER = '\n' + MATCHER`，扫描串以 `\x01` 开头。
- **`Case-sensitive keys`**（可被 entry 的 `caseSensitive` 覆盖）：默认不敏感。
- **`Match whole words`**（**默认开**，可被 entry 覆盖）：单字 key 必须整词匹配。文档特别警告：**中日文这类不用空格分词的语言应当关闭**。

### 4.2 触发条件

- **`Key`**：逗号分隔（plaintext 模式）或独立元素（fancy 模式）。**纯文本 key 不支持逗号**（逗号是分隔符）；正则 key 写成 `/…/flags` 时可以含逗号。key 会用 JavaScript 正则（全部 flag 可用）。
- 每条消息前缀是 `character name:`，且 v1.12.6+ 前面加了 `\x01`，所以可以用 `/\x01{{user}}:[^\x01]*?hello/` 精确匹配"用户说 hello"。
- **`Optional Filter`（secondary keys）** 与 `selective`：文档列了 4 种：
  1. **AND ANY**：主 key + 任一 filter key 命中 ⇒ 激活。
  2. **AND ALL**：主 key + **全部** filter key 命中 ⇒ 激活。
  3. **NOT ANY**：主 key 命中且 filter key **一个都没命中** ⇒ 激活。
  4. **NOT ALL**：即使主 key 命中，只要 filter key **全部命中**就**阻止**激活。
  （filter key 也支持正则。）
- **`Strategy`**：🔵 蓝圈 = 无需关键词、**必定触发**（= `constant`）；🟢 绿圈 = 需关键词；🔗 链环 = 允许被向量相似度插入（= `vectorized`）。每条还有 enable/disable 开关。
- **`character filter`**：按角色名列表 / 标签列表过滤，`Exclude` 模式取反。
- **`Triggers`**：按生成类型过滤 —— `Normal` / `Continue` / `Impersonate` / `Swipe` / `Regenerate` / `Quiet`；全不选 = 全部类型。⚠️ **"Regenerate" 在群聊不可用**（群聊用不同逻辑）。
- **`Additional matching sources`**：可把 entry 也拿去匹配 角色 Description / Personality / Scenario / 角色备注（Character's Note）/ Creator's Notes / persona description。文档明确：这些字段**不显示在聊天里**，是为了少维护 tag。⚠️ 注意文档没有列出 `matchCharacterDepthPrompt`（源码有，见 §3.5）。

### 4.3 位置与深度

**`Insertion Position`（文档原文定义，共 8 种）**：

| 文档名称 | 语义 | 源码枚举值 |
|---|---|---|
| Before Char Defs | 插在角色 description/scenario **之前**，影响中等 | `before = 0` |
| After Char Defs | 插在其**之后**，影响更大 | `after = 1` |
| Before Example Messages | 当作 example dialogue 块，插在卡的 examples **之前** | `EMTop = 5` |
| After Example Messages | 同上，插在**之后** | `EMBottom = 6` |
| Top of AN / Bottom of AN | 插在 Author's Note 内容的首/尾；**A/N 被禁用（Insertion Frequency = 0）时这两个位置会被忽略** | `ANTop = 2` / `ANBottom = 3` |
| `@ D`（At Depth） | 插进 chat 指定深度（**Depth 0 = prompt 底部**），可选 role：⚙️ system / 👤 user / 🤖 assistant | `atDepth = 4` |
| Outlet | **不自动注入**，内容存进具名 outlet，由 `{{outlet::Name}}` 宏决定出现位置 | `outlet = 7` |

**`Outlet Name` 的规则（文档写得很细）**：`{{outlet::Name}}` 会被替换成同名 entries 内容按 **Insertion Order** 排序、换行拼接；缺名字的 outlet entry 生成时被跳过；**不能**在 World Info entry 里嵌 outlet 宏、**不能**嵌套 outlet、**角色卡字段无法展开 outlet**（因为卡字段很早就被解析、作为 WI 扫描源）、**A/N 编辑器也无法解析 outlet**；名字**大小写敏感**；前后空格在调用时被忽略。

**⚠️ 文档缺口**：`position` 的**数值**（0..7）文档完全没给；`role` 的数值（0/1/2）也没给。只能从源码拿（见 §3.5）。

### 4.4 预算

- **`Context % / Budget`**：`Context %` = 相对 API max-context 的**百分比**；`Budget` = **绝对 token 数**。两者都存在。预算耗尽后**即使 key 命中也不再激活**。
- 优先顺序（文档原文）：**先插 `constant` 条目，然后按 order 更大的先插**；**"因直接提到 key 而插入的条目"优先于"因出现在别的条目内容里而插入的"**。
- ⚠️ 文档没写百分比与绝对值的**换算基准**（是否含 reply 预留），也没写 `ignoreBudget` 的存在（源码有）。

### 4.5 递归

- 全局开关 **`Recursive Scan`**；三条 per-entry 控制（**文档术语 → 源码字段**）：
  - **Non-recursable** → `excludeRecursion`：「不会被其他条目激活」。
  - **Prevent further recursion** → `preventRecursion`：「一旦被激活，不再触发别的条目」。
  - **Delay until recursion** → `delayUntilRecursion`：「只在递归检查里激活」；**新增 Recursion Level**：按层级分组，先只匹配最小层级，无匹配后下一个层级才有资格（配合 NOT ANY / NOT ALL 用）。⚠️ 文档没写"层级"存的是**数字**（源码 `delayUntilRecursion: number`）。
- **触发机制**：entry 的内容里提到另一个 entry 的 keyword 即可互相激活（文档给了 Bessie/Rufus 的双向例子）。
- **`Max Recursion Steps`**：与 `Min Activations` **互斥**。0 = 只受 prompt 预算限制；非 0 限制扫描轮数：1 ≈ 关闭递归，2 = 只能递归一次，3 = 可递归两次。
- **`Min Activations`**：非 0 时无视 `scan_depth`，从最新消息往回找关键词，直到触发够 N 条；仍受 `Max Depth` 与总预算限制。文档强调：**Min Activations 触发的额外扫描不会检查"前几轮递归新增的条目"，只有 chat 消息与 extension prompts 能触发它**；但它激活的条目可以照常触发别人。
- **`Max Depth`**：Min Activations 模式下的最大扫描深度。

### 4.6 概率 / 组 / 评分

- **`Probability (Trigger %)`**：作为"**额外**的**不插入**概率"，作用于**任何**激活途径（constant、主 key、递归）。100 = 每次都插，50 = 1:1，0 = 不插（等价禁用）。**sticky 期间忽略 probability 检查**（Timed Effects 原文）。
- **`Inclusion Group`**：同组多个被激活时只插一个；默认按 `Group Weight`（默认 100）**随机**加权挑选；一个 entry 可属于多个组（逗号分隔），它触发时会 **disable** 同组其它条目。
- **`Prioritize Inclusion`**：开启后不再随机，**取 Order 最大者**（用于做 fallback 序列）。
- **`Use Group Scoring`**：用"激活 key 数"决定组内胜者；只保留命中数最高的子集，再交给 Group Weight / Priority 决定。打分规则文档写得很细：主 key **1 命中 = 1 分**；secondary —— AND ANY 每命中 1 个 +1 分、AND ALL 全命中才 +1 分、NOT ANY / NOT ALL **不变**。文档给了 songs 组的两条 entry 例子（`sing me a song` 2 分对 2 分、加上 `about Ghosts` 后 Entry 2 变 3 分）。
- **排序（Automation ID 段落里才透露）**：automations 按"Character Lore Insertion Strategy + Priority"排序执行，**蓝圈（constant）先处理，然后按 Order**，**递归触发的条目在其后**。

### 4.7 时间效应（sticky / cooldown / delay）

文档规则（逐条转述）：
1. 时间单位是**消息条数**（不是"对话轮次/一来一回"），**0 = 无效**。
2. 只作用于**激活它的那个 chat**；**branches 继承父 chat 的状态**。
3. chat 如果没前进（最后一条被 swipe 或删除），已激活的 timed effect 会被移除。
4. **修改**正在生效的 entry 会**强制**移除该效应。
5. 关键词**再次命中不会刷新**时长。

三类：
- **Sticky**：激活后保持 N 条消息；**在 sticky 期内忽略 probability 检查**。
- **Cooldown**：激活后 N 条内**不能**再激活；可与 sticky 连用（sticky 结束才进 cooldown）。
- **Delay**：chat 里至少有 N 条消息时才可能激活（Delay=0 随时；Delay=1 空 chat 不行；Delay=2 只有 0 或 1 条时不行）。

文档给出的示例（`sticky=3, cooldown=2, delay=2`）：msg0 delay → msg1 激活 → msg2/3/4 sticky → msg5/6 cooldown → msg7 可再激活。

**⚠️ 文档缺口**：`sticky/cooldown/delay` 的**存储位置**（`chat_metadata.timedWorldInfo`）、键名格式（`"<world>.<uid>"`）、`hash`/`start`/`end`/`protected` 字段，文档**完全没提**。只能从源码（本报告 §5.3 与 `_raw/st_src_world-info.js:140-150`）。

### 4.8 向量匹配

- 前提：Vector Storage 扩展开启并配好 embedding；勾 "Enable for World Info"；entry 标 🔗 或设置里勾 "Enabled for all entries"。
- **只替换关键词检查**：trigger%、character filter、inclusion group 等**全部照旧**。
- **不使用 `Scan Depth`**，改用扩展的 "Query messages"；因此可以 `Scan Depth = 0`（无关键词匹配）但仍被向量激活。
- 🔗 只是**额外标记**：有 key 的 entry 仍会被关键词激活。
- 文档收尾：向量检索结果**不可预测**，要确定性就用关键词。

### 4.9 明确"文档没写清"的清单（必须以源码为准）

| # | 缺口 | 需要确认什么 |
|---|---|---|
| G1 | `position` / `role` / `selectiveLogic` 的**数值枚举** | 文档只给 UI 名称；源码给 0..7 / 0..2 / 0..3 |
| G2 | `constant` 与 `Scan Depth = 0` 的交互 | 扫深度为 0 时 constant 是否仍插 |
| G3 | `ignoreBudget`、`addMemo`、`groupOverride`、`useProbability`、`vectorized` 字段 | 文档一个都没提 |
| G4 | `min_activations` / `max_activations` 扩展键 | 文档只讲全局 Min Activations；源码里 per-entry 有扩展键 |
| G5 | 四轮递归的**精确循环结构**（`successfulNewEntries.filter(x => !x.preventRecursion)`、recursion delay level 递增时机） | 文档只有叙述 |
| G6 | `sticky/cooldown/delay` 的持久化格式与 "chat 没前进" 的判定 | 文档没有 |
| G7 | probability 的**采样点**（是否在 ordering/预算之前） | 文档只描述效果 |
| G8 | 预算的 `Context %` 换算基准 | 文档没有 |
| G9 | `triggers` 的取值字符串（`normal`/`continue`/…） | 文档只给 UI 名 |
| G10 | `character_filter` 的落盘结构与 `isExclude` | 文档只讲 UI 语义 |

---

## 5. Prompt 组装 / 聊天记录（文档能给的 vs 只能靠源码的）

### 5.1 Chat Completion 的 Prompt Manager（我们实现的那条路）

**块顺序（文档原文）**："Prompts placed closer to the **top** are sent earlier. The **bottom** of the list is the **last thing** sent to the model (typically Post-History Instructions)."

**Pinned（不可删除，只能 toggle OFF）的 default prompts（文档原文清单）**：
`Main Prompt`、`World Info (before/after)`、`Persona Description`、`Character Description`、`Character Personality`、`Scenario`、`Enhance Definitions`、`Auxiliary Prompt`、`Chat Examples`、`Chat History`、`Post-History Instructions`。

**In-Chat 注入（文档原文）**：
- `Position = Relative`：随拖拽列表位置发送。
- `Position = In-Chat` + `Depth`：**在 chat history 内部**发送，**忽略拖拽顺序**。
- `Depth`：0 = 最后一条消息**之后**；1 = 最后一条**之前**；2 = 倒数第二条之前；越大越靠前（越"深"）。
- `Order`：In-Chat 时同 `Role` + 同 `Depth` 的 prompt 会**分组**，组内按 `Order` 升序；**组间顺序（从上到下）= User → AI Assistant → System**。
- Role 可选 `System` / `AI Assistant` / `User`。
- Triggers 同世界书（Normal/Continue/Impersonate/Swipe/Regenerate/Quiet）。

**实际默认顺序（ST 仓库 `default/content/presets/openai/Default.json` 的 `prompt_order`，逐字）**：
- `character_id: 100000`（无 persona）：`main → worldInfoBefore → charDescription → charPersonality → scenario → enhanceDefinitions(false) → nsfw → worldInfoAfter → dialogueExamples → chatHistory → jailbreak`
- `character_id: 100001`（persona 变体）：同上，但在 `charDescription` **之前**多一个 `personaDescription`

**注意**：`prompt_order` 数组只带 `{identifier, enabled}`，**文本在 `prompts` 数组里**（`{name, system_prompt: true|"文本", role, content, identifier, marker?}`）。而且生成时的实际顺序还有一层硬编码（`openai.js:1211-1218`）：
```js
await addToChatCompletion('worldInfoBefore');
await addToChatCompletion('main');
await addToChatCompletion('worldInfoAfter');
await addToChatCompletion('charDescription');
await addToChatCompletion('charPersonality');
await addToChatCompletion('scenario');
await addToChatCompletion('personaDescription');
chatCompletion.reserveBudget(3);
```
→ ⚠️ **文档的"拖拽列表决定一切"和源码里这几行的硬编码顺序并不完全等价**，这是"文档 vs 源码"的一处实质差异，需要实测确认哪条优先。

**Utility Prompts（文档）**：
- **Format Templates**：用 `{0}`（World Info 模板）、`{{scenario}}`（Scenario 模板）、`{{personality}}`（Personality 模板）包裹来自世界书/角色卡的信息；**未设置模板则原样发送**。
- **New Chat / New Group Chat / New Example Chat**：在 chat history **之前**、以及每个 example dialogue 块之前发送；`<START>` 会被替换成 "New Example Chat" 的内容。
- **Continue Nudge**、**Replace Empty Message**、**Group Nudge**（群聊末尾强制某角色回复）、**Continue Postfix**、**Continue Prefill**、**Squash system messages**（**文档标 deprecated**，建议用 Prompt Post-Processing）。
- **Character Names Behavior**：控制"消息归属哪个人"的策略。

### 5.2 Text Completion 的 Context Template（Story String）—— 我们完全没做

来源 `doc_Usage_Prompts_context-template.md`（Handlebars 语法）：

```
{{anchorBefore}}  {{anchorAfter}}
{{description}} {{scenario}} {{personality}} {{system}} {{persona}} {{char}} {{user}}
{{wiBefore}} | {{loreBefore}}     // Position = "Before Char Defs" 的 WI 合并文本
{{wiAfter}}  | {{loreAfter}}      // Position = "After Char Defs"
{{mesExamples}}      // instruct-formatted，带 separator
{{mesExamplesRaw}}   // 原样
{{trim}}             // 去掉周围换行（空格不 trim）
```

- **警告（原文）**："If any of the above parameters are missing from the story string template, they will **not** be sent in the prompt at all."（模板没写的字段就**彻底不发**。）
- **Story String position**：默认在最前面（后面跟 examples + chat history）；也可选 **"In-chat @ Depth"**；文档警告这会与模板里的静态前后缀重复包裹。
- **Example Separator**：`<START>` 被替换成它；**Chat Start**：story string 与 examples 之后、第一条消息之前的插入物。
- `{{outlet::Name}}` 也在这里可用（Prompt Manager / Advanced Formatting 的 prompt 字段里）。

### 5.3 宏（`usage/macros.md`，官方全表摘录）

**语法规则（原文）**：
- `{{macroName}}`；**宏名大小写不敏感**（`{{User}}` = `{{user}}`）。
- 参数：空格分隔（`{{getvar myVariable}}`）或 `::`（`{{setvar::myVariable::Hello World}}`）；单 `:` 是 **legacy**。
- 支持嵌套、scoped（`{{macroName::args}}` 内再嵌）、条件宏、macro flags、`{{// comment}}`（注释）、转义。

**官方分类清单（节选，完整见 `_raw/doc_Usage_macros.md:674-823`）**：

| 分类 | 代表宏 |
|---|---|
| Names & Participants | `{{user}}` `{{char}}` `{{group}}` `{{groupNotMuted}}` `{{charIfNotGroup}}` `{{notChar}}` |
| Character Card & Persona | `{{description}}` `{{personality}}` `{{scenario}}` `{{persona}}` `{{charPrompt}}` `{{charInstruction}}` `{{charDepthPrompt}}` `{{charCreatorNotes}}` `{{charVersion}}` `{{mesExamples}}` `{{mesExamplesRaw}}` `{{charFirstMessage}}`（可带 index） `{{original}}` |
| Chat History & Messages | `{{lastMessage}}` `{{lastMessageId}}` `{{lastUserMessage}}` `{{lastCharMessage}}` `{{firstIncludedMessageId}}` `{{firstDisplayedMessageId}}` `{{lastSwipeId}}` `{{currentSwipeId}}` `{{allChatRange}}` `{{summary}}` |
| Time & Date | `{{time}}` `{{time::UTC±(offset)}}` `{{date}}` `{{weekday}}` `{{isotime}}` `{{isodate}}` `{{datetimeformat::format}}` `{{idleDuration}}` `{{timeDiff::left::right}}` |
| Variables | `{{getvar::n}}` `{{setvar::n::v}}` `{{addvar}}` `{{incvar}}` `{{decvar}}` `{{hasvar}}` `{{deletevar}}` + `*globalvar` 系列 |
| Randomization | `{{random::a::b::c}}`（每次重掷） `{{pick::a::b::c}}`（同 chat 同位置稳定） `{{roll::1d20}}` |
| Runtime State | `{{maxPrompt}}` `{{maxContextTokens}}` `{{maxResponseTokens}}` `{{model}}` `{{isMobile}}` `{{lastGenerationType}}` `{{hasExtension::name}}` |
| Prompt Templates | `{{systemPrompt}}` `{{defaultSystemPrompt}}` `{{authorsNote}}` `{{charAuthorsNote}}` `{{defaultAuthorsNote}}` `{{instruct*Prefix/Suffix/Separator/Stop/Filler}}` `{{chatSeparator}}` `{{chatStart}}` `{{reasoningPrefix/Suffix/Separator}}` `{{charPrefix}}` `{{charNegativePrefix}}` |
| Utility | `{{newline}}` `{{newline::count}}` `{{space}}` `{{space::count}}` `{{noop}}` `{{trim}}` `{{reverse::text}}` `{{input}}` `{{banned::word}}` **`{{outlet::key}}`** |

### 5.4 Preset 导出格式（文档 + 源码）

- Prompt Manager 的 preset = `default/content/presets/openai/*.json` 那种对象：顶层是**生成参数**（`openai_max_context`、`temperature`…）+ `prompts: [...]` + `prompt_order: [{character_id, order:[{identifier, enabled}]}]` + `names_behavior`、`wi_format`、`scenario_format`、`personality_format`、`new_chat_prompt`、`new_example_chat_prompt`、`continue_nudge_prompt`、`group_nudge_prompt`、`impersonation_prompt`、`send_if_empty`…
- 文档的 **Format Templates** 三个字段对应 `wi_format`（`{0}`）、`scenario_format`（`{{scenario}}`）、`personality_format`（`{{personality}}`）—— `Default.json` 里逐字可见（第 46-48 行）。
- **命名陷阱（文档原文）**：preset 若与角色卡同名，会在打开该角色聊天时**自动选中**。
- ⚠️ 文档没有给 preset 的字段级 schema，也没有"导出/导入"按钮背后的格式说明；只能靠仓库里的 `Default.json`。

### 5.5 聊天记录格式（**这是最大的文档缺口**）

**官方文档能确认的全部内容**（`doc_Usage_Characters_chatfilemanagement.md`）：
- 导出 **`.jsonl`**："in a format that can then be re-imported as is… including all their metadata (**but excluding images and file attachments**)"。
- 导出 `.txt`："**can't be re-imported again as it loses important metadata!**"。
- **Checkpoints**：克隆当前 chat 到某条消息为止，并**用 chat 文件名记录来源链接**；`Create Branch` = 克隆并切换，`Create Checkpoint` = 克隆并命名但不切换；可从 "Back to parent chat" 回到父级。
- **Rename Chat 会打断 checkpoints 的链接**（因为按文件名链接）。
- 导入来源：Character.AI（CAI-Tools）、TavernAI、oobabooga、Agnai、KoboldAI Lite、RisuAI。
- 路径/文件名：默认文件名 = 启动时的日期时间。

**文档完全没有**：header 行结构、消息行字段、`chat_metadata` 的字段、swipes/branches 的存储、群聊路径。→ 只能以源码为权威，见 §5.6。

### 5.6 聊天记录的权威结构（源码逐字，作为文档缺口的替代证据）

**存储路径（源码）**：`src/endpoints/chats.js:986`
```js
const character_name = avatar_url.replace('.png', '');
const directoryPath = path.join(request.user.directories.chats, character_name);
```
→ `data/<user-handle>/chats/<avatar-stem>/*.jsonl`。群聊在 `chats/<group id>/`（`settings.json` 里也可以看到 `"group chats"` 目录，⚠️ 群聊文件名前缀 `group-chats`/目录名未见文档，需源码/实测确认）。

**header 行（源码多处，例如 `chats.js:133`、`:312`）**：
```js
{ chat_metadata: {}, user_name: 'unused', character_name: 'unused' }
```
- 注意导入路径里 `user_name`/`character_name` 字面量就是字符串 `'unused'`；真实保存时由前端传入（`chats.js:776-777` `sanitize(request.body.character_name) || 'Character'`、`sanitize(request.body.user_name) || 'User'`）。
- `chats.js:839` 判定"这是不是一条有效 chat 行"的依据：`jsonData.user_name !== undefined || jsonData.name !== undefined || jsonData.chat_metadata !== undefined`。

**消息行（源码，`script.js:7714` 首条消息 + `chats.js` 导入路径）**：
```js
{
  name: string,           // 角色名或用户名
  is_user: boolean,
  is_system: boolean,
  send_date: string,      // new Date().toISOString()（ISO 8601）
  mes: string,
  extra: {},
  // 可选（有 swipe 时）：
  swipe_id: number,
  swipes: string[],
  swipe_info: [{ send_date, gen_started, gen_finished, extra }]
}
```
- **alternate greetings 的物化（关键）**：`script.js:7723-7739` 把 `[first_mes, ...alternate_greetings]` 做成首条消息的 `swipes`，`swipe_id = 0`，并逐条生成 `swipe_info`。文档只说"alternate greetings 显示为额外 swipe"，**没说落盘就是 swipes 数组**。
- 非首条的 swipe：`script.js:6855` `typeof message.swipe_id !== 'number' ⇒ 0`；`6868` 缺 `swipe_info` 时用 `message.swipes.map(_ => createSwipeInfo())` 回填；`6881` 还会对坏数据 warn。
- `swipe_info` 每项形状（`script.js:6793` / `10347`）：`{ send_date, gen_started, gen_finished, extra }`。

**`chat_metadata`**：`chats.js:359` `jsonData?.chat_metadata?.integrity`；扩展文档 `Writing-Extensions.md:338-356` 说 `chatMetadata` 是"任意 JSON 可序列化数据"，用 `saveMetadata()` 落盘 —— **即 `chat_metadata` 是开放容器**。已知键（跨源码）：`integrity`、`timedWorldInfo`、`world_info`、`note_prompt`/`note_interval`/`note_position`（Author's Note 状态）、`scenario_override`、`variables` 等。

**`integrity` 校验（源码）**：`chats.js:337-368` —— 文件缺失或大小为 0 ⇒ 视为完好；**没有 `integrity` 字段 ⇒ 跳过校验**（兼容旧 chat）；有则要求字符串全等。`IntegrityMismatchError` 会组织保存。

**timed effects 的存储（源码）**：`world-info.js:140-150` 的 `WITimedEffect` typedef 逐字给出：`{ hash, start, end, protected }`；**键 = `"<world>.<uid>"`**（由 `MetadataKeys`/`chat_metadata.timedWorldInfo` 持有）；`start`/`end` 是 chat index；`protected` 表示"chat 没前进也不能被移除"。

---

## 6. 与我们的实现对照

对照对象：`tavern/st/cards.py`、`tavern/st/worldbook.py`、`tavern/st/prompt.py`、`tavern/st/chat_store.py`（＋调用方 `tavern/core.py`）。

图例：✅ 一致 · ⚠️ 部分一致/有偏差 · ❌ 不一致或缺实现 · ❓ 文档没写（需源码为准）

### 6.1 角色卡

| # | 官方（文档/规范/源码） | 我们的实现 | 判定 |
|---|---|---|---|
| C1 | V1 判定 = 6 个字段**存在**（值可为空）（`TavernCardValidator.js:56`） | `cards.card_from_dict()`：没有 `data` 就当 V1（`spec="chara_card_v1"`），**不校验字段存在性**，缺字段给 `""` | ⚠️ 更宽松（能读 ST 会拒的卡），方向可接受 |
| C2 | V2 必填 14 字段 + `alternate_greetings`/`tags` 必须 `Array` + `extensions` 必须 `object` | `CharacterCard` 有全部 14 个字段；`_as_str_list()` 把字符串也接受（**比规范宽松**） | ⚠️ 有意宽松 |
| C3 | `spec_version` 用 float 比较（V3 规则）；ST 侧 V3 只判 `>=3.0 && <4.0` | 只存 `spec_version` **字符串**，不做版本比较 | ⚠️ 缺失：`3.1` 卡不会被识别为"更新版本" |
| C4 | PNG：`ccv3` 与 `chara` 同时存在 ⇒ **SHOULD 用 ccv3** | `PNG_CARD_KEYWORDS = ("chara", "ccv3")`，循环**取第一个命中的** ⇒ **优先 `chara`** | ❌ **方向反了**，建议改为 `("ccv3", "chara")`（一行改动） |
| C5 | V3 PNG 的 `ccv3` chunk；tEXt 值为 base64(UTF-8 JSON) | `_parse_text_chunk` 支持 `tEXt`/`iTXt`/`zTXt`；`tEXt` 用 `latin-1` 解（PNG 规范正确）；`base64.b64decode(validate=False)` | ✅ |
| C6 | `nickname` ⇒ `{{char}}` **SHOULD** 用 nickname 而不是 `name` | 读了 `nickname`，但为空时**回填 `card.name`**（`cards.py:176`），没有"宏解析用 nickname"的语义 | ⚠️ 有字段、缺语义 |
| C7 | `assets` 的枚举语义（`icon`/`background`/`user_icon`/`emotion`、`name:"main"`、`ext` 小写） | 只 `list(data.get("assets") or [])` 透传，**不解析、不校验** | ⚠️ 透传（可接受） |
| C8 | `character_book`（卡内 lorebook）**MUST** 默认使用、**SHOULD** 与全局叠加、角色书 SHOULD 优先 | `CharacterCard` **没有** `character_book` 字段；`card_from_dict` 完全丢弃 `data.character_book` | ❌ **丢数据**：卡内世界书读不到 |
| C9 | V3 独立文件 `{"spec":"lorebook_v3","data":{...}}` | `worldbook.book_from_dict` 只认顶层 `entries`（dict 或 list）或"值里有 `content` 的顶层键" | ❌ 读不了 V3 独立 lorebook |
| C10 | `CHARX`（zip + 根 `card.json`），`.charx` 扩展名 | `load_card()` 只认 `.json`/`.png`/`.yaml` | ❌ 未实现 |
| C11 | V1 规范：`description/personality/scenario/first_mes/mes_example` **MUST** 做 `{{char}}`/`<BOT>`/`{{user}}`/`<USER>` 大小写不敏感替换 | `prompt.render_macro` 只做 `{{char}}`/`{{user}}`… 全小写匹配（大小写不敏感 ✅），但**没有 `<BOT>`/`<USER>` 别名** | ⚠️ 缺 `<BOT>`/`<USER>` |
| C12 | ST `charaFormatData()` 往 `data.extensions` 写 `talkativeness`(0.5)/`fav`/`world`/`depth_prompt.{prompt,depth=4,role='system'}` | `extensions` 整块**原样保留**（`extensions=data.get("extensions")`），但**不读取** `depth_prompt`/`world`/`talkativeness` 的语义 | ⚠️ 保数据、缺语义（尤其 `depth_prompt` = 角色备注，与 PHI/depth 注入相关） |
| C13 | `creator_notes` **MUST NOT** 用于 prompt；`tags`/`creator`/`character_version` 同上 | 我们的 prompt 组装确实不用它们（`prompt.py` 只取 `description/personality/scenario/mes_example/system_prompt/post_history_instructions`） | ✅ |
| C14 | `{{original}}`：卡 `system_prompt` / `post_history_instructions` 里 **MUST** 支持 | `preset_from_dict`/`build_messages` 里 `main`/`jailbreak` 会用卡的覆盖文本，但**没有实现 `{{original}}` 替换**（`render_macro` 的 `_macro_table` 无 `original`） | ❌ 缺失（会导致卡里写 `{{original}}` 时把字面量发出去） |
| C15 | `to_v2_dict()` 导出 | 我们 `cards.py:82-108` **永远输出 `spec: "chara_card_v2"`**，即使是 V3 卡（只把 V3 专属字段塞进 `data`） | ⚠️ 语义：V3 卡往返会降级；规范要求**未识别字段应保留以便安全再导出** |

### 6.2 世界书引擎

| # | 官方（文档/规范/源码） | 我们的实现 | 判定 |
|---|---|---|---|
| W1 | `position` 枚举 0..7（`outlet: 7`） | `POSITION_BEFORE_CHAR=0 … POSITION_EM_BOTTOM=6`，**没有 `POSITION_OUTLET=7`**；`POSITION_NAMES` 只有 7 项 | ❌ 缺 outlet 位置 |
| W2 | `outletName` + `{{outlet::X}}` 宏 + outlet entries 按 Insertion Order 拼接 | 无 `outletName` 字段、无 outlet 分组、无 `{{outlet::}}` 宏 | ❌ 未实现 |
| W3 | `selectiveLogic` = `AND_ANY:0, NOT_ALL:1, NOT_ANY:2, AND_ALL:3` | `LOGIC_AND_ANY=0, LOGIC_NOT_ALL=1, LOGIC_NOT_ANY=2, LOGIC_AND_ALL=3`，名字与数值**逐一对上** | ✅ |
| W4 | 四种 selective 语义（含 AND ANY 的 `1 命中 = 1 分`评分） | `activate()` 的 `LOGIC_*` 分支与文档语义一致；评分在 `_apply_group_scoring`，但**用的是 `group_weight` 而非"key 命中数"** | ⚠️ Use Group Scoring 语义不符（见 W9） |
| W5 | `role` 用于 In-Chat 注入（⚙️/👤/🤖） | `WorldInfoEntry.role` **只在 dataclass 里存着，从不使用**；`prompt.INJECTION_ROLE = "system"` 硬编码 | ❌ 所有深度注入都是 system |
| W6 | 深度注入按 `depth` 分组（源码：同 depth 且**同 role** 才合并） | `InChatTargets.at_depth` 是**扁平 list**，`at_depth_before_index` 是**单个** index ⇒ **所有 depth 的条目插在同一个位置** | ❌ 未按 depth 分组 |
| W7 | `probability` 作用于所有激活途径（constant/主键/递归）；**useProbability** 可关 | `if entry.use_probability and entry.probability < 100: rng.randint(1,100) > probability ⇒ 不激活`（`worldbook.py:617`） | ✅ |
| W8 | sticky 期间**忽略 probability** | `already_sticky` 分支直接 `triggered = True` 并跳过 probability | ✅ |
| W9 | Use Group Scoring：按 key 命中数选组内胜者（主键 1 命中 = 1 分；AND ANY 每个 secondary +1；AND ALL 全中才 +1；NOT ANY/NOT ALL 不加分） | `_apply_group_scoring()` 按 `group_weight` 最大者胜出 | ❌ 语义与文档不符 |
| W10 | `Prioritize Inclusion` ⇒ 组内取 **Order 最大者** | 无该开关；`group_override: bool` 字段存在但**未使用** | ❌ 未实现 |
| W11 | `Inclusion Group` 默认**随机**加权（Group Weight，默认 100） | 没有"同组随机挑一个"；`_apply_group_scoring` 只在 `settings.group_scoring` 时生效，否则同组条目**全部插入** | ❌ 未实现 inclusion group |
| W12 | `excludeRecursion`（不被别人激活）/`preventRecursion`（不激活别人）/`delayUntilRecursion`（仅递归期 + **层级**） | `exclude_recursion`/`prevent_recursion` 语义与文档一致；`delay_until_recursion: bool`（**不是 number**）⇒ 无层级 | ⚠️ 前两个 ✅、delayUntilRecursion 缺层级 |
| W13 | 递归由新激活 entry 的 content 触发；`Min Activations` 的额外扫描**不看递归新增条目** | 递归用 `fresh_texts = [_entry_scan_text(entry)]`，`via_recursion` 时才并入 haystack | ✅ 结构一致；**Min Activations 未实现** |
| W14 | `Min Activations` / `Max Depth`（与 Max Recursion Steps 互斥） | **都没有** | ❌ 未实现 |
| W15 | `Max Recursion Steps`（0 = 无限，受预算限制） | `WorldBookSettings.max_recursion_steps: int = 3`，循环 `steps_limit - 1` 轮 | ⚠️ 默认 3 是**自定值**（ST 默认 0）；`0` 会变成"不递归"（`range(-1)`） |
| W16 | `scan_depth`：全局 → 可被 entry 覆盖；**0 = 只评估 recursed + A/N** | `scan_depth_for()`：entry > book > `settings.default_scan_depth(4)`；`scan_text(0)` 返回**全部消息** | ❌ drive-by 不一致：depth 0 应为"不扫"，我们扫全部 |
| W17 | `Include Names`（默认开）+ 前缀分隔符 `\x01` | `activate()` 的 `messages: Sequence[str]` 是调用方拼好的纯文本，**没有名字前缀、没有 `\x01`** | ⚠️ 需调用方对齐；`/\x01{{user}}:.../` 类正则**必然失配** |
| W18 | `Context % / Budget`：百分比或绝对值；预算耗尽停止插入；constant 先插、order 大者先插 | `token_budget`（绝对值）✅；`for item in reversed(activated)` 从 order 最大端消费 ✅；**没有百分比**；没有 `ignoreBudget` | ⚠️ 缺百分比与 ignoreBudget |
| W19 | `sticky/cooldown/delay`：单位=消息条数、只作用于本 chat、branch 继承、chat 不前进则移除、改条目强制移除、不刷新 | `ActivationState` 有 `is_blocked/is_sticky/on_activate/next_turn`；`delay` 字段读入了但**是否真正参与判断需复核**（docstring 说 turn-based） | ❓ 需逐行复核 `ActivationState`（`worldbook.py:414-486`） |
| W20 | `matchPersonaDescription` / `matchCharacterDescription` / `matchCharacterPersonality` / `matchCharacterDepthPrompt` / `matchScenario` / `matchCreatorNotes` | `WorldInfoEntry` **完全没有这些字段**，`entry_from_dict` 不读 | ❌ 未实现 |
| W21 | `character_filter.{names,tags,isExclude}` | 未读、未实现 | ❌ |
| W22 | `triggers`（generation type 过滤） | `WorldInfoEntry` 无 `triggers`；`activate(active_group=...)` 是**组过滤**，语义不同 | ❌ |
| W23 | `automationId` | `automation_id: str` 读了但**没有被任何逻辑使用**（Automation 是 STscript 联动） | ⚠️ 保字段、不做事（可接受） |
| W24 | `vectorized` + `keyvector` + Vector Storage 只替换关键词检查 | `vectorized: bool`、`key_vector`、`settings.vector_match` 回调（`worldbook.py:80,565-570`） | ⚠️ 有钩子；`keyvector` 落盘键名需与 ST 对齐（ST 存的是 embedding 结果，⚠️ 键名待源码确认） |
| W25 | `useGroupScoring`（entry 级，默认 `null` = 用全局） | `use_group_scoring: bool\|None` ✅，但实际评分逻辑见 W9 | ⚠️ |
| W26 | `addMemo` / `ignoreBudget` / `groupOverride` | 字段都在 dataclass：`add_memo` ❌ 没有、`ignore_budget` ❌ 没有、`group_override` ✅ 有但未用 | ⚠️/❌ |
| W27 | `hash`（entry 修改检测，用于强制清除 timed effect） | `ActivationState._identity()` 用 `(book, uid)`；`ChatMetadata.mark_timed(..., hash_=...)` 有 hash 字段但 `ActivationState` 不写 hash | ⚠️ 部分 |
| W28 | `displayIndex` | `display_index` ✅，参与排序 ✅ | ✅ |
| W29 | `order` 排序方向（源码降序 `sortFn`） | `activated.sort(key=(insertion_order, display_index))`（**升序**）→ **最终文本顺序与 ST 相同**（因为 ST 是先排后按序插入 context） | ✅（文本顺序等价）；⚠️ 预算消费顺序相反（见 W18） |
| W30 | `WorldBook` 的 `scan_depth` / `token_budget` / `recursive_scanning` / `extensions` | `book_from_dict` 全部读取 ✅ | ✅ |
| W31 | `keys` 的正则约定：`/re/flags`（含 flags） | `_REGEX_KEY = ^/(.*?)/([a-z]*)$` ✅ + `compile_key()` | ✅ |
| W32 | `matchWholeWords` 默认**开**（全局），中文/日文建议关闭 | `WorldInfoEntry.match_whole_words: bool\|None`；`activate()` 里 `whole_words=bool(entry.match_whole_words)` ⇒ `None` 变 **False** | ❌ 默认反了（ST 全局默认 true） |
| W33 | `caseSensitive` 全局默认 false，entry `null` 继承全局 | `case_sensitive: bool\|None`；`case_sensitive = bool(entry.case_sensitive)` ⇒ `None` 变 `False` | ✅（等价，但丢掉了"继承"语义） |
| W34 | `secondary_keys` / `selective` 默认值 | ST 新条目 `selective` **默认 true**；我们 `entry_from_dict` 用 `_as_bool(payload.get("selective"))` ⇒ `None` → **False** | ❌ 默认反了 |
| W35 | `constant` 与 `key` 同时存在 | `constant or vectorized ⇒ triggered = True`，不看 keys | ✅ |
| W36 | `disable` / `enabled` | `disable` ✅（`constant=False` 时 `if not entry.disable` 过滤） | ✅ |

### 6.3 Prompt 组装

| # | 官方 | 我们的实现 | 判定 |
|---|---|---|---|
| P1 | Chat Completion 的默认块顺序 = `Default.json` 的 `prompt_order[100001]` | `DEFAULT_PROMPT_ORDER` **逐项等于** 100001 变体（`main, worldInfoBefore, personaDescription, charDescription, charPersonality, scenario, enhanceDefinitions, nsfw, worldInfoAfter, dialogueExamples, chatHistory, jailbreak`） | ✅ |
| P2 | `personaDescription` / `enhanceDefinitions` 默认关 | `DEFAULT_DISABLED_BLOCKS = {personaDescription, enhanceDefinitions}` | ✅（与 `Default.json` 一致） |
| P3 | `marker: true` 的块从卡/聊天取内容 | `MARKER_BLOCKS` 集合列举正确（8 个） | ✅ |
| P4 | 文本在 `prompts[]`，开关在 `prompt_order[].order[].enabled` | `preset_from_dict(payload, apply_prompt_order=...)`：先读 `prompts` 数组定顺序与文本，`prompt_order` 只覆盖 `enabled`。**默认参数是 `False`，但实际调用方 `core.py:217` 传了 `apply_prompt_order=True`** ⇒ 运行时行为与 ST 一致 | ✅（运行时）；函数级默认值是陷阱（直接调用会漏开关） |
| P5 | 顺序最终**应以 `prompt_order` 为准**（文档"拖拽列表决定"） | `preset_from_dict` **以 `prompts` 数组顺序为准**，`prompt_order` 的顺序被忽略 | ⚠️ 文档说顺序在拖拽列表（= `prompt_order`）；我们以 `prompts` 数组序为准。**当两者不一致时会与 ST 不同**，且 `Default.json` 里这个顺序**其实就是 100001 的顺序**，恰好等价 |
| P6 | In-Chat 注入：`Depth` 0=最后一条之后、1=最后之前…；同 Role+Depth 分组按 Order；组间 User→Assistant→System | `at_depth_before_index`（**单一 index**）+ 全部 system；`_history_block_messages` 把它插在 `history[index]` 之前 | ❌ 只支持单一 depth、单一 role |
| P7 | `ANTop/ANBottom` 拼到 Author's Note 首/尾；**A/N 被禁用时忽略** | `an_top` 拼在 history 最前、`an_bottom` 拼在最后（`_history_block_messages`） | ⚠️ 位置语义近似（不是真 A/N，但可接受）；**没有 A/N 禁用判定** |
| P8 | `EMTop/EMBottom` 是"当作 example dialogue 块"插在卡的 examples 前后 | `target.em_top`/`em_bottom` 在 `_example_block_messages` 里包裹 | ✅ |
| P9 | `Before/After Char Defs` → story string 的 `{{wiBefore}}`/`{{wiAfter}}`（Text Completion）；Chat Completion 里是 `worldInfoBefore`/`worldInfoAfter` 块 | `before_char`/`after_char` 映射到这两个块 ✅ | ✅ |
| P10 | `squash system messages`（**文档标 deprecated**，且 `openai.js:497` 的 preset 默认值 = `false`） | `build_messages(..., squash_system=True)` 默认开；**但实际调用方 `core.py:493` 显式传了 `squash_system=False`** ⇒ 运行时与 ST 默认一致 | ✅（运行时）；同样存在"函数默认值 ≠ 运行时值"的陷阱 |
| P11 | 宏：~90 个，参数用空格/`::`，大小写不敏感，支持嵌套/scoped | `_MACRO_PATTERN = \{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}`：**只匹配无参数、单标识符**；表里只有 8 个（`char/user/time/date/weekday/isotime/isodate` + 可选 `input`） | ❌ 覆盖面极小；`{{outlet::X}}`、`{{getvar::x}}`、`{{random::a::b}}`、`{{trim}}`、`{{original}}`、`{{description}}` 全部**原样漏出到 prompt** |
| P12 | `{{title}}`/`{{time}}` 类时间的**格式** | `time`=`%H:%M`、`isodate`=`%Y-%m-%dT%H:%M:%S`（无 offset） | ⚠️ ST 的 `{{date}}` 是"short format"、`{{isodate}}` 是 `YYYY-MM-DD`（**不是** datetime）。我们的 `isodate` 是 ISO datetime、`date` 是 `YYYY-MM-DD` ⇒ **`isodate` 语义与 ST 不同** |
| P13 | 未知宏 SHOULD 原样保留 | `replace()` 在表里查不到就返回 `match.group(0)` | ✅ |
| P14 | `{{trim}}` 去周围换行（空格不 trim） | `_strip_leading_newlines` 用 `_LEADING_NEWLINES` 只去**开头**的空白行 | ⚠️ 不是 `{{trim}}` 的等价物；ST 有全局"Strip leading newlines"设置，这个 ✅ |
| P15 | Format Templates（`wi_format`/`scenario_format`/`personality_format`，`{0}`/`{{scenario}}`/`{{personality}}`） | **未实现**（`_block_text` 直接输出原文本） | ❌ |
| P16 | Story String / Context Template（Handlebars） | **未实现**（模块 docstring 明确说是 Chat Completion 路线） | ❌（设计取舍，需在文档里写明） |
| P17 | Instruct Mode / Chat Messages Sequences | **未实现** | ❌ |
| P18 | `PresetSpec.names_as_prefix` / `names_behavior` | 有字段，但 `build_messages` 里只用于 `_history_messages` 的 `name` 透传 | ⚠️ |

### 6.4 聊天记录

| # | 官方/源码 | 我们的实现 | 判定 |
|---|---|---|---|
| H1 | 路径 `data/<user>/chats/<card-stem>/<name>.jsonl` | `ChatStore(root, user_name)`：`character_dir()` + `path_for()`，`CHAT_SUFFIX=".jsonl"` | ✅（需复核是否拼成 `chats/<card>`） |
| H2 | 第 1 行 header：`{user_name, character_name, chat_metadata}` | `ChatSession.to_jsonl()` 写出**同样三个键、同样顺序** | ✅ |
| H3 | 注释行/空行容错 | `_iter_chat_lines()` 容忍 BOM/CRLF/无尾换行/空文件/只有 header/空行 | ✅ |
| H4 | 消息行 `{name, is_user, is_system, send_date, mes, extra}` | `ChatMessage.to_st_dict()` **逐字一致** | ✅ |
| H5 | `send_date` 是 ISO 字符串（`new Date().toISOString()`），导入路径可能遇到 epoch ms | `_normalise_send_date()`：ISO 字符串原样、数字 >1e11 当 ms 转 UTC ISO | ✅ |
| H6 | `swipes: string[]` / `swipe_id: number` / `swipe_info: [{send_date, gen_started, gen_finished, extra}]` **是消息行的正式字段** | `from_st_dict` 把它们塞进 `extra`（`known` 集合只有 6 个键）；**不从 `extra` 还原成正式字段**；`to_st_dict()` **不落盘** swipes | ❌ **双向不兼容**：读进来的卡能保命但写出去会丢 swipe；ST 侧再打开会只见当前 `mes` |
| H7 | alternate greetings ⇒ 首条消息的 swipes（源码 `script.js:7723`） | `CharacterCard.greeting(index)` 只做**读取取值**；`chat_store.create()` 落盘首条消息时**不生成 swipes** | ❌ |
| H8 | `chat_metadata.timedWorldInfo`，键 `"<world>.<uid>"`，值 `{hash, start, end, protected}`，桶名 `sticky`/`cooldown`/`delay` | `ChatMetadata.timed_world_info` + `_flatten_timed`/`_nested_timed`；`TIMED_BUCKETS=("sticky","cooldown")`；`mark_timed()` 写 `{bucket, hash, start, end, protected}`；`is_timed_active` 用 `start <= turn < end`；`prune_timed` 丢 `end <= turn and not protected` | ⚠️ 结构对得上，但源码注释写的是 `currentTurn <= end && currentTurn >= start`（**闭区间**），我们注释写"此 guard 导致 end==start 为空"⇒ 实现用的是**半开区间**。**这是一处与源码字面不一致**，需以实测/源码复核 |
| H9 | `chat_metadata.integrity` 的比对方式 | `content_hash()` = 对**消息行** `json.dumps(..., sort_keys=True, separators=(",",":"))` 的 SHA-256；`from_jsonl` 里 `integrity_ok = (not stored) or stored == content_hash()` | ❌ **算法完全不同**（ST 的 slug 由前端算，字段是字符串全等）。后果：ST 存的 chat 我们读会 `integrity_ok=False`；我们写的 chat ST 打开会触发 `IntegrityMismatchError` **拒绝保存**。⚠️ **这是互操作硬伤**，应改为"ST 的 integrity 一律忽略/只读" |
| H10 | `chat_metadata.note_prompt` / `note_interval` / `note_position` | `ChatMetadata` 有这三个字段，`to_st_dict()` **无条件写出** | ✅ |
| H11 | `chat_metadata.world_info` | `world_info: dict\|str\|None` 读写 ✅ | ✅ |
| H12 | 未知 `chat_metadata` 键 | 保留在 `ChatMetadata.extra` 并**合并回**输出 ✅（`to_st_dict` 先 `dict(self.extra)` 再覆盖已知键） | ✅ |
| H13 | Checkpoints/branches：克隆到某条消息 + **用 chat 文件名记录来源**；rename 会断链 | `ChatStore.fork()` 存在；`ChatSession` **没有 "branch 来源" 字段**（如 `chat_metadata` 里的 source/父文件名） | ❌ 未记录父子链接 |
| H14 | 群聊 | `ChatStore` 只有 `character_dir(character_name)`；**无 group chat 路径** | ❌ 未实现 |
| H15 | 文件命名：默认"启动日期时间" | `_safe_target()`/`_validate_chat_name()`；`sanitize_filename` 处理 Windows 非法字符 | ✅（命名策略自定，可接受） |
| H16 | 导出 `.txt`（不可再导入） | **未实现** | ❌（低优先级） |

### 6.5 交叉层（`core.py`）

| # | 观察 | 判定 |
|---|---|---|
| X1 | `_targets_from_entries()` 按 `entry.position` 分组，**丢弃 `entry.role`**（`core.py:624-637`） | 与 W5 同源：world info 的 role 全线丢失 |
| X2 | `_history_from_store()` 把消息映射为 `role = "user" if is_user else "assistant"`，**忽略 `is_system`**（`chat_store.ChatMessage.role` 有三态，但 core 的重建路径没走它） | ⚠️ system 消息会被当成 assistant 发出去 |
| X3 | `core.py:628` 用 `grouped.setdefault(entry.position, []).append(...)` ⇒ 同一 position 下**丢失 entry 顺序以外的深度信息**（depth 被丢） | ❌ 与 W6 同源 |

### 6.6 按投入产出排序的修复清单（给实现者的直接行动项）

**A 类 · 单点改动、收益立刻可见（建议本轮就做）**

| 项 | 文件:位置 | 改法 | 依据 |
|---|---|---|---|
| A1 | `tavern/st/cards.py:23` `PNG_CARD_KEYWORDS` | `("ccv3", "chara")` | V3 规范 §PNG/APNG："if the application detects both `chara` and `ccv3` chunk, the application *SHOULD* use the `ccv3` chunk." |
| A2 | `tavern/st/worldbook.py:206` `selective` 默认 | `payload.get("selective", True)`（与 ST 新条目默认一致） | `world-info.js:4089 selective: { default: true, ... }` |
| A3 | `tavern/st/worldbook.py:216/557-558` `match_whole_words` | `None` 时回落到**全局默认 True**（新增 `WorldBookSettings.default_match_whole_words = True`） | 文档 §Match whole words："Enabled by default." |
| A4 | `tavern/st/worldbook.py:44-60` | 补 `POSITION_OUTLET = 7` + `POSITION_NAMES[7] = "outlet"` | `world-info.js:863 outlet: 7` |
| A5 | `tavern/st/worldbook.py:534-536` `scan_text(0)` | `depth <= 0` 时返回 `""`（只有递归缓冲/扩展 prompt 参与） | 文档 §Scan Depth："If set to 0, then only recursed entries and Author's Note are evaluated." |
| A6 | `tavern/st/prompt.py:345` `_MACRO_PATTERN` | 放宽为允许参数：`\{\{\s*([^}]+?)\s*\}\}`，再在 `render_macro` 里按空格/`::` 切分参数 | `doc_Usage_macros.md` §Arguments |
| A7 | `tavern/st/prompt.py:413-427` `_macro_table` | 补 `{{original}}`（卡 `system_prompt`/`post_history_instructions` 覆盖全局时用） | V2 规范 `system_prompt` / `post_history_instructions` 段：**MUST** support `{{original}}` |
| A8 | `tavern/st/prompt.py:422` `isodate` | 改成 `%Y-%m-%d`（当前是完整 ISO datetime） | `doc_Usage_macros.md`: `{{isodate}}` = "Current date in YYYY-MM-DD format" |
| A9 | `tavern/st/chat_store.py` integrity | 引入"只读 ST 文件时忽略 integrity"策略，**不要**用自研 SHA-256 去覆盖 ST 的 slug | `chats.js:337-368` + `IntegrityMismatchError` |

**B 类 · 结构性补齐（本轮或下一轮）**

| 项 | 依据 | 说明 |
|---|---|---|
| B1 | W6/X3 | `InChatTargets.at_depth` 改成按 `(depth, role)` 分组；`build_messages` 支持多个注入点 |
| B2 | W5/X1 | `world info entry.role` 贯通到 `PromptMessage.role`（0/1/2 → system/user/assistant） |
| B3 | C8/C9 | 支持 `data.character_book` 与 `{"spec":"lorebook_v3","data":…}`；卡内世界书与全局世界书按 ST 策略合并 |
| B4 | H6/H7 | `ChatMessage` 补 `swipes`/`swipe_id`/`swipe_info` 三个正式字段（`to_st_dict`/`from_st_dict` 双向） |
| B5 | W2 | `outletName` + `{{outlet::X}}`：`InChatTargets.outlets: dict[str, list[str]]`，按 Insertion Order 拼接 |
| B6 | W20/W21/W22 | `match*` 六件套 + `character_filter` + `triggers` |
| B7 | W9/W10/W11 | Inclusion Group（随机/加权/Prioritize/Group Scoring 的 key 计分） |
| B8 | W14 | `Min Activations` + `Max Depth`（与 `max_recursion_steps` 互斥） |
| B9 | W12 | `delayUntilRecursion` 从 `bool` 升级为 `int`（层级） |
| B10 | P15 | Format Templates（`wi_format`/`scenario_format`/`personality_format`） |

**C 类 · 明确不做（写进文档/README 的"已知差异"）**

- Text Completion 的 Story String / Context Template / Instruct Mode（P16/P17）——我们只做 Chat Completion 路线。
- `Automation ID` 与 STscript 联动（W23）——只保留字段。
- Vector Storage 的真实 embedding 计算（W24）——只保留 `vector_match` 注入点。
- CHARX（C10）——需要 zip 资产分发时才做。
- ST 的 HTTP 内网端点（§8.2）——非文档化，不承诺兼容。

---

## 7. 必须"以源码为准"的结论清单（文档没写 / 文档与源码冲突）

**A. 文档完全没写的（⚠️ 高危）**

1. `.jsonl` 的 header / 消息行 / `chat_metadata` 全部字段结构（§5.5、§5.6）。
2. swipes / `swipe_id` / `swipe_info` 的存储与 alternate greetings 物化规则（§5.6、§6.4 H6/H7）。
3. branches / checkpoints 的父子链接方式（文档只说"按 chat 文件名链接"）。
4. `world_info_position` / `world_info_logic` / `role` 的**数值枚举**（§3.5）。
5. V3 独立世界书 `{"spec":"lorebook_v3","data":…}` 与全部 `@@decorator`（§3.3）。
6. CHARX 格式（文档 0 覆盖，规范有）。
7. `context %` 与 `budget` 的换算基准、`ignoreBudget`、`addMemo`、`groupOverride`、`useProbability`。
8. `sticky/cooldown/delay` 的持久化格式与"chat 没前进"的判定。
9. `character_filter` / `triggers` / `min_activations` / `max_activations` 的落盘键名。
10. 群聊的目录结构与文件名前缀。
11. Preset 的字段级 schema（只有仓库里的 `Default.json` 可当样例）。

**B. 文档与源码不一致 / 文档自相矛盾**

12. `insertion_order` 方向（§3.4）。
13. 世界书 `selective` 的默认值：ST 新条目默认 `true`（`newWorldInfoEntryDefinition`），我们默认 `False`。
14. `matchWholeWords` 默认值：ST 全局默认 **true**，我们 `None → False`。
15. `Scan Depth = 0` 的语义（文档："只评估 recursed 与 A/N"；我们的实现扫全部消息）。
16. Prompt 发送顺序：文档说"拖拽列表决定"，`openai.js:1211-1218` 有硬编码调用序。
17. `timedWorldInfo` 的区间是闭区间（源码注释）还是半开（我们实现）。
18. `integrity` 的算法（ST 用前端 slug 字符串全等；我们用 SHA-256 消息哈希）。
19. `squash system messages`：文档标 deprecated 且 ST 默认 `false`；我们默认 `True`。
20. PNG chunk 优先级：规范说 `ccv3` 优先，我们代码优先 `chara`。

**C. 需要实测才能确认的**

21. ST 打开我们写的 `.jsonl` 是否会因 `integrity` 拒绝保存（H9）。
22. V3 卡里 `assets.uri = embedded://…` 的实际处理（规范原文拼写是 `embeded://`，⚠️ 可能是规范笔误，需实测）。
23. `{{outlet::Name}}` 与角色卡字段的解析顺序（文档给了限制，但没给算法）。
24. Vector Storage 的 `keyvector` 落盘键名与向量维度。
25. 群聊 `Join character cards` 的字段拼接顺序（文档给了 5 项顺序，但没说分隔符与 macros 的展开时机）。

---

## 8. API / 扩展机制（对"搬进 Python 插件"的直接含义）

### 8.1 ST 提供的可编程面（官方文档口径）

| 机制 | 入口 | 能力 | 对我们的意义 |
|---|---|---|---|
| **UI Extensions** | `data/<user-handle>/extensions/<name>/manifest.json` + `js` 入口（文档 `Writing-Extensions.md:77`） | `SillyTavern.getContext()` 暴露 `chat`、`characters`、`chatMetadata`/`saveMetadata`、`extensionSettings`、`eventSource`/`event_types`（含 `MESSAGE_SWIPED`）、`generateRaw`/`generateQuietPrompt`、`macros.register()`、`registerFunctionTool`、`registerDataBankScraper`、Prompt Interceptors、messageFormatter pipeline | 若我们未来要**与 ST 双向同步**，扩展是最正当的写入点（`chatMetadata` 可放我们的 ID） |
| **Server Plugins** | `plugins/` 目录 + `config.yaml: enableServerPlugins: true`；导出 `{init(router), exit, info}`；路由 `/api/plugins/{id}/{route}`（文档 `Server-Plugins.md`） | 新增 HTTP 端点、用 Node 包；**"not sandboxed"** | 若我们要"插件 → ST"拉数据，写 ST server plugin 比爬文件更稳 |
| Slash Commands / STscript | `/for-contributors/st-script.md`（54.8 KB） | 自动化脚本、`/swipe`、`/addswipe`、`/delswipe` 等 | 可用 `/api/plugins` 或 STscript 做批处理 |
| HTTP 端点 | **没有官方 API 参考**；`Administration/config-yaml.md` 也不列端点 | — | ⚠️ 结论：**不要依赖未文档化的 ST HTTP API**，走文件格式兼容 |

### 8.2 我们现有 `backends/sillytavern.py` 的定位

我们仓库里已有一个 `tavern/backends/sillytavern.py`（8.9 KB）。⚠️ 本节未逐行核对（不在本次 scoped 读取范围内），但从官方文档结论看：**ST 的对外前端只有 UI + 未公开的 cookie/CSRF 保护端点**，把 ST 当"LLM 后端"只有在 `POST /api/backends/chat-completions/generate` 这类**内部端点**上可行，而它们**没有任何官方文档保证**（另一份报告 `03-sillytavern-formats-and-api.md` §0 已记录此结论）。建议：把这条通道标注为 **"非文档化、可能随版本破坏"**。

### 8.3 许可证结论

- 官方文档首页（`readme.md`）："released under the **AGPL-3.0 License**"（链接 `/LICENSE`）。
- 推论：**复用 ST 代码/格式/规范文本** ⇒ 插件整体 AGPL-3.0 是必须的，我们已决定改 AGPL-3.0，方向正确。
- **注意**：`default/content/presets/openai/Default.json` 的 prompt 文本、`default/content/*` 模板属 ST 仓库内容，同受 AGPL 覆盖。我们 `prompt.py` 的 `DEFAULT_MAIN_PROMPT` 已注明"不复用 ST 原文"，但**`DEFAULT_PROMPT_ORDER` 的块名列表**（`main`, `worldInfoBefore`, …）是从 `Default.json` 抄来的**标识符**——标识符本身是互操作必需（不受版权保护），**保真但保留出处注释**即可（我们 docstring 里已经写了来源，✅）。
- 若最终决定**逐字复用** presets / 模板，必须：保留 AGPL-3.0、保留版权与来源声明、把对应文件标为来自 SillyTavern 并给出 commit/版本。

### 8.4 权威规范文件路径速查（给实现者）

| 内容 | 路径 / URL |
|---|---|
| ST 卡判定实现 | `SillyTavern/SillyTavern@release:src/validator/TavernCardValidator.js` |
| V1 规范 | `malfoyslastname/character-card-spec-v2:spec_v1.md` |
| V2 规范 | `malfoyslastname/character-card-spec-v2:spec_v2.md` |
| V3 规范 | `kwaroran/character-card-spec-v3:SPEC_V3.md` |
| 世界书字段权威 | `SillyTavern/SillyTavern@release:public/scripts/world-info.js`（`newWorldInfoEntryDefinition` / `world_info_position` / `world_info_logic` / `getWorldInfoPrompt` / `WorldInfoTimedEffects`） |
| Prompt 顺序权威 | `SillyTavern/SillyTavern@release:default/content/presets/openai/Default.json` + `public/scripts/openai.js:1211-1254` |
| 聊天存储权威 | `SillyTavern/SillyTavern@release:src/endpoints/chats.js` + `public/script.js`（`getFirstMessage` / `ensureMessageSwipeData`） |
| 世界书文档 | <https://docs.sillytavern.app/usage/core-concepts/worldinfo.md> |
| Prompt Manager 文档 | <https://docs.sillytavern.app/usage/prompts/prompt-manager.md> |
| 角色卡文档 | <https://docs.sillytavern.app/usage/core-concepts/characterdesign.md> |
| 聊天文件管理文档 | <https://docs.sillytavern.app/usage/core-concepts/chatfilemanagement.md> |
| 宏文档 | <https://docs.sillytavern.app/usage/macros.md> |
| 扩展机制文档 | <https://docs.sillytavern.app/for-contributors/writing-extensions> |
| Server Plugin 文档 | <https://docs.sillytavern.app/for-contributors/server-plugins> |

---

## 9. 未核实清单（只能算推测 / 需进一步查源码或实测）

| # | 待核实 | 现状 | 建议动作 |
|---|---|---|---|
| U1 | `sticky/cooldown/delay` 的区间是闭区间还是半开 | 我们实现是半开 `[start, end)`；源码注释写 `currentTurn <= end && currentTurn >= start` | 读 `world-info.js` 的 `WorldInfoTimedEffects.check()` 全文（本次已落盘，可直接 grep `currentTurn`） |
| U2 | `timedWorldInfo` 的键到底是 `"<world>.<uid>"` 还是别的 | 我们按 `"<world>.<uid>"`；源码 `WITimedEffect` 只给了值结构，键格式从代码推断 | grep `setTimedEffects` / `getTimedEffect` |
| U3 | 群聊目录名与文件名前缀 | 我们无实现；报告 §5.6 只给了 `chats/<character>` | 查 ST `settings.json` / `chats.js` 的 group 分支；或看 `data/<user>/group chats/` |
| U4 | `keyvector` 落盘键名与维度 | 我们读 `keyvector` 或 `key_vector` | 查 `public/scripts/extensions/vectors/index.js`（已落盘 `st_src_vectors.js`） |
| U5 | `integrity` 的真实算法 | 我们自研 SHA-256，与 ST 不兼容 | 若要与 ST 互操作，改为"忽略 ST integrity"；不要试图伪造 |
| U6 | `{{isodate}}` 在 ST 的真实输出 | 我们输出 `YYYY-MM-DDTHH:MM:SS`；文档写 `YYYY-MM-DD` | 文档已是权威，**应改我们** |
| U7 | `{{date}}` 的 "short format" 具体样式 | 我们 `%Y-%m-%d` | 查 ST `macros.js` 或实测 |
| U8 | `squash system messages` 在 Chat Completion 的真实默认 | `openai.js:497` 写 `squash_system_messages: false` | 建议我们默认改 `False` 与 ST 对齐（除非 AstrBot 侧有理由） |
| U9 | charset：ST 读写 `.jsonl` 是否带 BOM | 我们 `_strip_bom` 容错 ✅ | 无需动作 |
| U10 | `character_book` 与全局世界书的合并顺序 | 文档："字符书 SHOULD 优先/叠加"；`world-info.js:4610-4625` 有 3 种策略 | 需实现时读 `world_info_character_strategy` 分支 |
| U11 | `role` 的数值 → 字符串映射（0/1/2 → system/user/assistant） | 源码 `extension_prompt_roles`（`openai.js:814-816`）把 `'system'/'user'/'assistant'` 映射过去；世界书 `role` 默认 0 | 读 `public/scripts/extensions.js` 的 `extension_prompt_roles` 定义确认 0=SYSTEM |
| U12 | `{{outlet::}}` 在我们链条里该怎么落地 | 完全没做 | 需在 `InChatTargets` 加 `outlets: dict[str,list[str]]`，在 `render_macro` 加双冒号参数解析 |
| U13 | V3 卡导出：我们 `to_v2_dict()` 是否要保留未知字段 | 规范要求 "MUST NOT destroy unknown key-value pairs" | 建议加 `raw` 回填或 `extra_fields` |
| U14 | `_entry_scan_text()` 与 ST 递归增量文本是否等价 | 我们用它生成 `fresh_texts` | 对比 ST `world-info.js` 的 recursion buffer 构造（`recurseBuffer`） |
| U15 | `activate()` 里 `delay` 字段是否真的参与判断 | 字段读入；`ActivationState` 的行为本次未逐行读完（`worldbook.py:414-486`） | 需要一次专门的 code review |

---

## 10. 附：本次抓取到的原始文件清单（`research/_raw/`）

**官方文档源（27 个）**：`doc_Usage_worldinfo.md`、`doc_Usage_macros.md`、`doc_Usage_Prompts_index.md`、`doc_Usage_Prompts_prompt-manager.md`、`doc_Usage_Prompts_advancedformatting.md`、`doc_Usage_Prompts_context-template.md`、`doc_Usage_Prompts_instructmode.md`、`doc_Usage_Prompts_tokenizer.md`、`doc_Usage_Characters_characterdesign.md`、`doc_Usage_Characters_index.md`、`doc_Usage_Characters_chatfilemanagement.md`、`doc_Usage_Characters_groupchats.md`、`doc_Usage_Characters_Author_s-Note.md`、`doc_Usage_Characters_data-bank.md`、`doc_Usage_Chatting_index.md`、`doc_Usage_Chatting_slashcommands.md`、`doc_Usage_API_Connections_index.md`、`doc_Usage_API_Connections_openai.md`、`doc_Usage_index.md`、`doc_Usage_personas.md`、`doc_Usage_Common-Settings.md`、`doc_Usage_branches.md`、`doc_For_Contributors_Writing-Extensions.md`、`doc_For_Contributors_Server-Plugins.md`、`doc_For_Contributors_index.md`、`doc_For_Contributors_st-script.md`、`doc_Administration_config-yaml.md`
**规范原文（4 个）**：`spec_v1_malfoyslastname.md`、`spec_v2_malfoyslastname.md`、`readme_ccsv2.md`、`SPEC_V3_kwaroran.md`
**源码证据（6 个）**：`st_src_TavernCardValidator.js`、`st_src_world-info.js`、`st_src_openai.js`、`st_src_chats.js`、`st_src_public_script.js`、`st_src_worldinfo_ep.js`、`st_src_vectors.js`
**预设样例（1 个）**：`st_default_openai_preset.json`
**抓取辅助（1 个）**：`docs_tree.txt`（`SillyTavern-Docs` 全文件树，用于确认文档页清单与路径）

---

*报告完毕。所有"官方文档结论"均可通过 §1 的配方复现；所有"⚠️ 文档缺口"都给了对应的源码文件与行号，可直接 `grep` 复核。*
