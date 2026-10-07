# 07 · 世界书引擎移植映射：SillyTavern → `tavern/st/*`

**源锁定**：SillyTavern `public/scripts/world-info.js` @
`06bde939fb1e9c4c8d8641d810f0a916b5bce127`（`release` 分支头，`package.json` = 1.19.0，
相对 tag `1.19.0` 只多一个改 `.github/workflows/npm-publish.yml` 的提交 → **所有源码与
tag 1.19.0 字节一致**）。快照与校验见 [`research/_raw/st-src/PROVENANCE.md`](_raw/st-src/PROVENANCE.md)，
行号即该目录下 `world-info.js` 的行号。

**本文用到的三个与任务书不一致的事实纠正**（已在 PROVENANCE §2 记录）：

1. `src/TavernCardValidator.js` 不存在，真实路径是 `src/validator/TavernCardValidator.js`。
2. `GENERATION_TYPE_TRIGGERS` 不在 `src/constants.js`（那是服务端常量），而在
   `public/scripts/constants.js`——`world-info.js:12` 的 `import ... from './constants.js'` 指的是后者。
3. **`world_info_role` 这个常量在 1.19.0 里不存在。** 条目 `role` 用的是
   `extension_prompt_roles`（`public/script.js:494-498`），`world_info.js:3` 从 `../script.js` 导入。
   所以任务书附表 A 里那一行按"不存在"处理，见 §2。

---

## 0. 状态定义（读表之前先看这个）

| 状态 | 含义 | 判定依据 |
|---|---|---|
| **ported** | 行为已在 Python 侧存在（可能改名、拆散、简化）。表里的 `Python 目标` 一定是**能 grep 到的真符号**，并给出 `文件:行号`。 | `tavern/` 下实测 |
| **pending** | Python 侧**完全没有**该行为。`Python 目标` 是**建议名**，前缀 `（建议）`。 | 已 grep 确认无对应 |
| **exempt** | DOM / jQuery / select2 / i18n / toastr / 编辑器渲染 / 斜杠命令注册等**服务端无意义**的部分，故意不移植。 | 依赖里含 `jQuery$`/`document`/`select2`/`toastr` |

统计：**ported 23 / pending 26 / exempt 32 = 81**。

`research/_raw/dep_world-info.json` 只有 81 条，它是**顶层函数/声明**的 AST 清单；两个类
（`WorldInfoBuffer`、`WorldInfoTimedEffects`）与全部模块级 `const/let` 不在里面，所以它们放在
§3 / §2 单独处理。`checkWorldInfo` 之前的 `defaultGlobalScanData`、
`worldInfoCache`、`originalWIDataKeyMap`、`newWorldInfoEntryDefinition` 等也没进那 81 条。

---

## 1. 主表：81 个 JS 函数 → Python 目标

> 行号区间与 LOC 逐字取自 `dep_world-info.json`。
> `Python 目标` 一列里，带 `:` 的是真实现（`文件:行号`），带 `（建议）` 的是待建。

| # | JS 函数（行号 / LOC） | 职责一句话 | Python 目标 | 状态 | 难点与坑 |
|---|---|---|---|---|---|
| 1 | `getWorldInfoSettings` 795–812 / 18 | 把全局 WI 设置打包成一个对象返回 | `tavern/config.py::WorldBookConfig` + `TavernConfig.from_raw` `config.py:211-219`；会话级书目在 `tavern/core.py::SessionBinding.worldbooks` `core.py:69` | ported | ST 有 13 个模块级 `world_info_*` 标量（`world-info.js:69-82`）；Python 只映射了 `scan_depth`/`token_budget`/`allow_recursion`/`max_recursion_steps`/`match_whole_words`。缺 `budget_cap`、`min_activations`、`min_activations_depth_max`、`include_names`、`case_sensitive`、`character_strategy`、`use_group_scoring`、`overflow_alert` |
| 2 | `updateWorldInfoSettings` 819–853 / 35 | 用 settings 对象覆盖全局变量，并迁移旧版 `world_info` 字符串 | `tavern/config.py::TavernConfig.from_raw` `config.py:192-219` | ported | 旧格式迁移（`world-info.js:956-963`：`world_info` 曾是字符串，要转成数组）无对应；Python 每次从 config 重建，不存在"全局被就地改写"的竞态 |
| 3 | `getWorldInfoPrompt` 892–915 / 24 | 调 `checkWorldInfo`，把结果摊成 before/after/depth/AN/outlet 六个字段 | `tavern/core.py::PluginCore.build_turn` `core.py:422-530` → `tavern/st/prompt.py::build_messages` `prompt.py:805` | ported | ST 返回 8 个键（含 `outletEntries`、`allActivatedEntries`）；Python 只回 7 个 position 桶，`outlet` 桶在 `core.py:624-637` 被**静默丢弃**；`eventSource.emit(WORLD_INFO_ACTIVATED)` 无对应 |
| 4 | `setWorldInfoSettings` 917–1033 / 117 | 从后端设置对象灌入全局 + 旧数据迁移 + 首次装配 | `tavern/config.py::TavernConfig.from_raw` `config.py:192-219` | ported | `world-info.js:946-948` 的静默纠正（`budget > 100 → 25`）没移植；那 117 行里约 60 行是 DOM/`accountStorage`，本就该扔 |
| 5 | `reloadEditor` 1040–1046 / 7 | 重载编辑器下拉 | — | exempt | 纯 jQuery |
| 6 | `registerWorldInfoSlashCommands` 1049–2009 / **961** | 注册 `/world`、`/findentry`、`/getentry`、`/setentry` 等 30+ 斜杠命令 | `tavern/main.py` 的 `/tavern worldbook list\|on\|off` `main.py:506-544`、`/tavern import` `main.py:570` | pending | AstrBot 命令体系完全不同（`@filter.command_group("tavern")` `main.py:388`）。这 961 行里值得移植的只有 `/world`（干跑一次扫描看命中）与 `/findentry`；其余是编辑器/文件管理 |
| 7 | `showWorldEditor` 2018–2026 / 9 | 显示编辑面板 | — | exempt | |
| 8 | `loadWorldInfo` 2036–2059 / 24 | 走 `/api/worldinfo/get` 拉书并写进 `worldInfoCache` | `tavern/st/worldbook.py::load_world_book` `worldbook.py:296` + `tavern/core.py::PluginCore.get_book` `core.py:254`、`_books` 缓存 `core.py:149` | ported | ST 用 `StructuredCloneMap` 做**深拷贝**缓存（`world-info.js:882`，`cloneOnGet: true`），调用方拿不到可变引用；Python 直接返回内存里的可变 `WorldBook`，`build_turn` 靠"只读约定"维持正确性 |
| 9 | `updateWorldInfoList` 2061–2084 / 24 | 刷新书名列表 | `tavern/core.py::PluginCore.reload_library` `core.py:197`、`book_ids` `core.py:239` | ported | |
| 10 | `hideWorldEditor` 2086–2088 / 3 | 隐藏面板 | — | exempt | |
| 11 | `getWIElement` 2090–2096 / 7 | `$('#world_info')` | — | exempt | DOM 耦合示例：返回值是 jQuery 对象，调用方一律逃不开浏览器 |
| 12 | `addMissingWorldInfoFields` 2104–2136 / 33 | 给老书补 `newWorldInfoEntryTemplate` 里缺的字段 | `tavern/st/worldbook.py::entry_from_dict` `worldbook.py:206-244` | ported | ST 在**载入时就地补齐并回写**；Python 只在解析时用默认值兜底、不回写 → 再把 `WorldBook` 导出成 JSON 会**少字段**，与 ST 的书不互通 |
| 13 | `sortWorldInfoEntries` 2146–2210 / 65 | 编辑器排序（order / displayIndex / 各种 filter 规则） | `tavern/st/worldbook.py::activate` 的排序 `worldbook.py:667` | ported | **排序方向易写反**：编辑器与 `getSortedEntries` 用 `sortFn = (a,b) => b.order - a.order`（**降序**，`world-info.js:88`）；`checkWorldInfo` 建 prompt 时又 `sort(sortFn)` + `unshift`（`world-info.js:5203-5214`）抵消成升序。Python 统一用 `(insertion_order, display_index)` 升序（`worldbook.py:667`），最终拼接顺序与 ST 一致，但**不能用它做 budget 淘汰顺序的唯一依据** |
| 14 | `nullWorldInfo` 2212–2214 / 3 | 清空 + toast | — | exempt | |
| 15 | `updateWorldEntryKeyOptionsCache` 2226–2245 / 20 | 下拉补全选项缓存 | — | exempt | |
| 16 | `clearEntryList` 2247–2307 / 61 | 清空条目列表 DOM | — | exempt | |
| 17 | `displayWorldEntries` 2310–2685 / **376** | 渲染条目列表 | — | exempt | 376 行几乎全是 `renderTemplateAsync` + jQuery |
| 18 | `verifyWorldInfoSearchSortRule` 2727–2744 / 18 | 搜索时显示/隐藏排序项 | — | exempt | |
| 19 | `setWIOriginalDataValue` 2756–2766 / 11 | 按 `originalWIDataKeyMap` 把改动写回**卡里那份原始 world book** | `worldbook.set_original_data_value`（建议） | pending | `originalData` 是"从角色卡导入的书"的逆向指针（`world-info.js:5618`）；`tavern/st/worldbook.py::WorldBook` `worldbook.py:139-153` **没有** `originalData` 字段，卡内世界书丢了就再也写不回去 |
| 20 | `deleteWIOriginalDataValue` 2774–2784 / 11 | 从原始数据里删条目 | `worldbook.delete_original_data_value`（建议） | pending | 同上。注意 ST 用**非严格**比较找 uid（`x.uid == uid`，`world-info.js:2778`），字符串 uid 也能命中 |
| 21 | `splitKeywordsAndRegexes` 2797–2815 / 19 | 把逗号串拆成 key/regex 列表，尊重 `/…/flags` 内部的逗号 | `worldbook.split_keywords_and_regexes`（建议） | pending | **不能用 `str.split(',')`**。必须保留正则内逗号与 `\/` 转义；`entry_from_dict` `worldbook.py:210` 只做 `_as_str_list`，读 V2 数组没问题，但读手写的 `"key": "a,b"` 会把一个 key 当成一个 |
| 22 | `customTokenizer` 2825–2878 / 54 | #21 的状态机实现 | `worldbook.custom_tokenizer`（建议，或并入 #21） | pending | `insideRegex`/`regexClosed` 双标志 + "半成品正则先放行、下个逗号再验"的容忍策略（`world-info.js:2850`）；循环里 `i = 0` 重置也容易抄漏 |
| 23 | `isValidRegex` 2888–2890 / 3 | `parseRegexFromString` 的布尔包装 | `tavern/st/worldbook.py::is_regex_key` `worldbook.py:334` | ported | Python 用 `^/(.*?)/([a-z]*)$` 且额外要求 `'/' in key.strip()[1:]`，比 ST 的 `^\/([\w\W]+?)\/([gimsuy]*)$`（`world-info.js:2903`）**宽松**：非法 flag 字母不被拒绝 |
| 24 | `parseRegexFromString` 2901–2926 / 26 | `/pattern/flags` → `RegExp` | `tavern/st/worldbook.py::compile_key` `worldbook.py:340-363` | ported | 三处偏差：①ST **拒绝** pattern 里未转义的 `/`（`world-info.js:2913`），Python 没查；②flag 集合不同——JS `gimsuy` vs Python `im sx`（`g`/`y` 无意义、`x` Python 独有，`worldbook.py:347-352`）；③ST 会把 `\/` 还原成 `/`（`world-info.js:2918`），Python 直接交给 `re` |
| 25 | `enableKeysInputHelper` 2938–3041 / 104 | 键输入框（select2/textarea 双形态） | — | exempt | |
| 26 | `handleMatchCheckboxHelper` 3052–3064 / 13 | 复选框 → 存盘 | — | exempt | |
| 27 | `updatePosOrdDisplayHelper` 3073–3084 / 12 | position/order 回显 | — | exempt | |
| 28 | `initCharacterFilterSelect2Helper` 3090–3099 / 10 | 角色筛选 select2 | — | exempt | |
| 29 | `fillCharacterAndTagOptionsHelper` 3107–3126 / 20 | 填角色/标签选项 | — | exempt | |
| 30 | `handleCharacterFilterChangeHelper` 3136–3163 / 28 | 角色筛选变更 | — | exempt | |
| 31 | `handleProbabilityInputHelper` 3173–3190 / 18 | 概率输入 | — | exempt | |
| 32 | `handleProbabilityToggleHelper` 3201–3220 / 20 | 概率开关 | — | exempt | |
| 33 | `handleBooleanSelectHelper` 3231–3241 / 11 | 布尔下拉 → entry | — | exempt | |
| 34 | `handleNumberInputHelper` 3255–3274 / 20 | 数字输入 + clamp | — | exempt | 唯一有运行期意义的信息：depth 有 min/max 约束，与 `WorldInfoBuffer.get` 里的 `MAX_SCAN_DEPTH` clamp（`world-info.js:290-293`）呼应 |
| 35 | `handleEntryStateSelectorHelper` 3284–3316 / 33 | 条目状态选择器 | — | exempt | |
| 36 | `handleEntryKillSwitchHelper` 3327–3343 / 17 | delete/disable 开关 | — | exempt | |
| 37 | `setCommentPlaceholder` 3350–3354 / 5 | 占位符文案 | — | exempt | |
| 38 | `getWorldEntry` 3362–3873 / **512** | 单条目编辑表单（全文件最大的 DOM 函数） | — | exempt | 里面唯一值得抄的是 `world-info.js:3439`：`role` 只在 `position === atDepth` 时才写回条目 |
| 39 | `buildAutocompleteCallback` 3884–3921 / 38 | 自动补全 | — | exempt | |
| 40 | `getInclusionGroupCallback` 3935–3952 / 18 | 组名补全 | — | exempt | |
| 41 | `getAutomationIdCallback` 3954–3963 / 10 | automationId 补全 | — | exempt | |
| 42 | `getOutletNameCallback` 3965–3970 / 6 | outlet 名补全 | — | exempt | |
| 43 | `createEntryInputAutocomplete` 3979–4010 / 32 | 绑定补全 | — | exempt | |
| 44 | `duplicateWorldInfoEntry` 4019–4033 / 15 | `structuredClone` 一条条目 | `worldbook.duplicate_entry`（建议） | pending | 需要新 uid，而 uid 分配器（#52）也没移植 |
| 45 | `deleteWorldInfoEntry` 4043–4073 / 31 | 删条目 + 同步 originalData + 重排 | `worldbook.delete_entry`（建议） | pending | |
| 46 | `createWorldInfoEntry` 4137–4149 / 13 | 按 `newWorldInfoEntryTemplate` 建条目 | `tavern/st/worldbook.py::entry_from_dict` `worldbook.py:206`（默认值表） | ported | 传空 dict 即可复现模板（已逐字段核对，见 §2 表 C）；但 ST 顺手分配 uid，Python 的 uid 由调用方给 |
| 47 | `_save` 4151–4161 / 11 | `POST /api/worldinfo/edit` | `tavern/core.py::_atomic_write_json` `core.py:640` | ported | |
| 48 | `saveWorldInfo` 4177–4190 / 14 | debounce 保存 + 刷缓存 | `tavern/core.py::_atomic_write_json` `core.py:640` + `save_state` `core.py:187` | ported | ST 默认 debounce（`world-info.js:83`）；Python 每次原子写盘，语义更安全但写放大 |
| 49 | `renameWorldInfo` 4192–4223 / 32 | 重命名书（文件名 sanitize + 下拉同步 + 引用修复） | `worldbook.rename_book`（建议） | pending | 必须连带改所有引用，见 #50 |
| 50 | `updateWorldInfoLinks` 4232–4338 / **107** | 改名后修复全局/角色/聊天/persona 四处引用 | `worldbook.update_world_info_links`（建议） | pending | 107 行涉及 5 类引用（`selected_world_info`、`chat_metadata`、`world_info.charLore`、角色卡 `extensions.world`、`persona_description_lorebook`）。Python 侧引用只存在 `SessionBinding.worldbooks`（`core.py:69`），范围小得多，可以只做一版 |
| 51 | `deleteWorldInfo` 4346–4393 / 48 | 删书 + 清理所有引用 | `worldbook.delete_book`（建议） | pending | `tavern/core.py` 有 `import_worldbook_bytes`（`core.py:306`）但**没有** delete |
| 52 | `getFreeWorldEntryUid` 4395–4409 / 15 | 找最小空闲 uid | `worldbook.free_entry_uid`（建议） | pending | |
| 53 | `getFreeWorldName` 4422–4437 / 16 | 生成不重名的书名 | `tavern/core.py::PluginCore._unique_id` `core.py:227` | ported | 命名风格不同：ST 是 `Name 2`/`Name 3`（空格 + 序号），Python 是 `name_2`。都不重名，但导出的文件名不同 |
| 54 | `createNewWorldInfo` 4448–4473 / 26 | 建空书并选中 | `worldbook.create_world_book`（建议） | pending | 里面有"覆盖已存在数据"的交互确认，服务端可省；空书骨架可直接复用 `book_from_dict({})` `worldbook.py:259` |
| 55 | `getCharacterLore` 4475–4525 / 51 | 收集角色主书 + `charLore` 附加书，并跳过已在别处的 | `tavern/core.py::PluginCore.build_turn` `core.py:452-454` | ported | 大幅简化：ST 会按来源去重（`world-info.js:4499-4512`）；Python 的 `binding.worldbooks` 是一张**扁平列表**，没有"角色书 vs 全局书 vs 聊天书"的优先级概念 |
| 56 | `getGlobalLore` 4527–4542 / 16 | 收集全局启用书 | 同上 `core.py:452-454` | ported | |
| 57 | `getChatLore` 4544–4562 / 19 | 收集聊天绑定书 | 同上（`SessionBinding` 本身按 chat 作用域，`core.py:63-106`） | ported | |
| 58 | `getPersonaLore` 4564–4588 / 25 | 收集 persona 描述书 | `worldbook.get_persona_lore`（建议） | pending | `power_user.persona_description_lorebook` / `getOrCreatePersonaDescriptor` 在 Python 侧完全没有 |
| 59 | `getSortedEntries` 4590–4644 / 55 | 合并 4 类来源 → 按策略排序 → 解析 decorator → 算 hash | `tavern/core.py::build_turn` `core.py:450-467` | ported | 三处缺失：①`world_info_character_strategy` 的三种排列（`world-info.js:4608-4622`）没实现，Python 直接按 `binding.worldbooks` 顺序；②"chat 永远最前、persona 次之"（`world-info.js:4625`）没实现；③`hash = getStringHash(JSON.stringify(entry))`（`world-info.js:4632`）没实现——Python 改用 `(source_path or name, uid)`（`worldbook.py:453-458`），跨进程稳定但与 ST 不通用 |
| 60 | `parseDecorators` 4652–4698 / 47 | 取内容开头的 `@@…` 装饰器，并返回剥掉后的正文 | `worldbook.parse_decorators`（建议） | pending | **纯逻辑、零依赖**（`deps` 为空），最该第一个移植。两个反直觉点见 §5-1 |
| 61 | `checkWorldInfo` 4709–5282 / **574** | 主扫描循环：注入缓冲 → 多轮递归 / min-activations → 概率 → budget → inclusion group → 按 position 建 prompt | `tavern/st/worldbook.py::activate` `worldbook.py:503-694` | ported | 最大的偏差源。依赖分类见 §4-1；行为差异见 §5 |
| 62 | `filterGroupsByScoring` 5292–5328 / 37 | 组内只留 key 命中分最高的 | `tavern/st/worldbook.py::_apply_group_scoring` `worldbook.py:697-713` | ported | **算法不同**：ST 用 `buffer.getScore()`（主键命中数 + AND_ANY 下的次键命中数，`world-info.js:428-473`）淘汰；Python 按 `group_weight` 留最重的。要真正对齐必须把"扫描缓冲"传进组过滤，Python 现在的签名没有这个参数 |
| 63 | `filterGroupsByTimedEffects` 5337–5378 / 42 | 组内有 sticky 就淘汰其余；剔掉在 cooldown/delay 的 | `tavern/st/worldbook.py::ActivationState` `worldbook.py:425-495` + `activate` `worldbook.py:555-566` | ported | ST 是**组级**淘汰并回传 `hasStickyMap`（"本组已被 sticky 占位"），Python 是**条目级**屏蔽，没有这个组级语义 |
| 64 | `filterByInclusionGroups` 5388–5475 / 88 | 组映射 → 时间效果 → 打分 → `groupOverride` 优先 → 按 `groupWeight` 加权随机取一 | `worldbook.filter_by_inclusion_groups`（建议） | pending | **核心语义缺口**：`activate` 里只有单组过滤 `active_group`（`worldbook.py:632-633`）和 opt-in 的 group scoring（`worldbook.py:669-670`），**没有"一个包含组最终只能活一条条目"**。另外 `group` 字段支持逗号分隔多组（`world-info.js:5392` 的 `split(/,\s*/)`），Python 把 `entry.group` 当单值字符串用（`worldbook.py:118`） |
| 65 | `convertAgnaiMemoryBook` 5477–5520 / 44 | Agnai 记忆书 → ST 格式 | `worldbook.convert_agnai_memory_book`（建议） | pending | 三个 converter 都是"字段重命名 + 补 `newWorldInfoEntryTemplate`"，可直接照抄 |
| 66 | `convertRisuLorebook` 5522–5565 / 44 | Risu lorebook → ST | `worldbook.convert_risu_lorebook`（建议） | pending | `entry.key.split(',')`（`world-info.js:5529`）——又是"用逗号分 key"的坑；且 `useProbability: entry.activationPercent ?? true` 是类型混用的历史 bug（数字当布尔） |
| 67 | `convertNovelLorebook` 5567–5615 / 49 | NovelAI lorebook → ST | `worldbook.convert_novel_lorebook`（建议） | pending | |
| 68 | `convertCharacterBook` 5617–5674 / 58 | V2 `character_book`（下划线）→ ST 条目（驼峰） | `worldbook.convert_character_book`（建议）——**测试 oracle 里已有一版**：`tools/st-oracle/run_python.py::convert_character_book` `run_python.py:56-108`，但它不参与生产路径 | pending | 这是官方文档偏差 **C8**（卡内世界书读不到）的根因：`tavern/st/cards.py::CharacterCard` `cards.py:40-67` **没有** `character_book` 字段，`card_from_dict` `cards.py:135` 直接丢弃它。映射规则：`keys↔key`、`secondary_keys↔keysecondary`、`insertion_order↔order`、`enabled↔!disable`、`position:"before_char"→0 / else→1`（`world-info.js:5636`）、其余全在 `extensions.*` 下 |
| 69 | `setWorldInfoButtonClass` 5676–5689 / 14 | 世界书按钮高亮 | — | exempt | 依赖 `characters[]` + jQuery |
| 70 | `checkEmbeddedWorld` 5691–5729 / 39 | 检测卡内嵌世界书并弹窗询问 | `cards.embedded_worldbook`（建议） | pending | 依赖 #68 先落地 |
| 71 | `importEmbeddedWorldInfo` 5731–5770 / 40 | 把卡内嵌世界书导成独立书 | `cards.import_embedded_worldbook`（建议） | pending | 同上 |
| 72 | `onWorldInfoChange` 5772–5844 / 73 | 下拉变更事件处理 | — | exempt | |
| 73 | `importWorldInfo` 5850–5931 / 82 | 导入文件（含 PNG 内嵌）→ 建书 | `tavern/core.py::PluginCore.import_worldbook_bytes` `core.py:306`、`import_card_bytes` `core.py:280` | ported | Python 只认 `entries` map / list 两种形状（`worldbook.py:259-293`）；ST 那 82 行里的格式嗅探（Agnai/Risu/Novel/Native）对应 pending 的 #65–#68 |
| 74 | `openWorldInfoEditor` 5937–5944 / 8 | 打开编辑器 | — | exempt | |
| 75 | `assignLorebookToChat` 5951–5988 / 38 | 把书绑到当前聊天（shift/alt 改绑全局/角色） | `tavern/core.py::PluginCore.toggle_book` `core.py:372`、`set_books` `core.py:388` | ported | 三个作用域（global / character / chat）在 Python 合并成 `SessionBinding.worldbooks` 一个列表（`core.py:69`）；ST 的 shift/alt 修饰键语义（`world-info.js:5951` 的解构参数）没有对应 |
| 76 | `moveWorldInfoEntry` 6000–6089 / **90** | 把条目从一本书移到另一本 | `worldbook.move_entry`（建议） | pending | 90 行：uid 冲突解决 + 目标书按 order 重排 + 源书清理 + toast |
| 77 | `charUpdatePrimaryWorld` 6097–6127 / 31 | 改角色卡的主世界书 | `cards.` / `core.PluginCore.bind_card` 扩展（建议） | pending | `SessionBinding` 与角色卡解耦，没有"卡片自带世界书"的概念 |
| 78 | `charUpdateAddAuxWorld` 6134–6138 / 5 | 给角色加一本附加书 | 同上 | pending | |
| 79 | `charSetAuxWorlds` 6145–6147 / 3 | 设置角色附加书列表 | 同上 | pending | |
| 80 | `updateAuxBooks` 6149–6173 / 25 | 维护 `world_info.charLore`（角色→附加书映射） | 同上 | pending | 整个 `charLore` 概念在 Python 侧不存在 |
| 81 | `initWorldInfo` 6175–6408 / **234** | 启动装配：建 UI、绑事件、注册命令、初始化 charLore | `tavern/main.py`（`@filter.command_group("tavern")` `main.py:388`、`_register_commands` `main.py:393-411`）+ `tavern/core.py::PluginCore.load` `core.py:166` | exempt | 234 行里只有 `eventSource.on(WORLDINFO_SETTINGS_UPDATED)` 与 charLore 初始化有服务端语义，其余全是 DOM 装配 |

---

## 2. 附表 A：常量、枚举与默认值表

### A-1 `world_info_position`（`world-info.js:855-864`，逐字）

```js
export const world_info_position = {
    before: 0,      after: 1,
    ANTop: 2,       ANBottom: 3,
    atDepth: 4,     EMTop: 5,      EMBottom: 6,
    outlet: 7,
};
```

| ST | 值 | Python 对应（`tavern/st/worldbook.py`） | 行号 | 备注 |
|---|---:|---|---|---|
| `before` | 0 | `POSITION_BEFORE_CHAR` | 44 | 渲染成 `worldInfoBefore` 块 |
| `after` | 1 | `POSITION_AFTER_CHAR` | 45 | 渲染成 `worldInfoAfter` 块 |
| `ANTop` | 2 | `POSITION_ANT_TOP` | 46 | |
| `ANBottom` | 3 | `POSITION_ANT_BOTTOM` | 47 | |
| `atDepth` | 4 | `POSITION_AT_DEPTH` | 48 | 只有它是 `role` 生效的位置（`world-info.js:3439`、`5236`） |
| `EMTop` | 5 | `POSITION_EM_TOP` | 49 | ST 里带 `wi_anchor_position.before` 锚点 |
| `EMBottom` | 6 | `POSITION_EM_BOTTOM` | 50 | |
| `outlet` | 7 | `POSITION_OUTLET` | 53 | **Python 只定义、不消费**：`core.py:624-637` 没有 outlet 桶 |

`POSITION_NAMES`（`worldbook.py:55-64`）是 Python 自造的调试映射，ST 侧无对应。

### A-2 `world_info_logic`（`world-info.js:33-38`，逐字）

```js
export const world_info_logic = {
    AND_ANY: 0,
    NOT_ALL: 1,
    NOT_ANY: 2,
    AND_ALL: 3,
};
```

| ST | 值 | Python | 行号 |
|---|---:|---|---|
| `AND_ANY` | 0 | `LOGIC_AND_ANY` | 67 |
| `NOT_ALL` | 1 | `LOGIC_NOT_ALL` | 68 |
| `NOT_ANY` | 2 | `LOGIC_NOT_ANY` | 69 |
| `AND_ALL` | 3 | `LOGIC_AND_ALL` | 70 |

**值序不一致的风险**：这是 ST 的顺序，Python 完全照抄（连"NOT_ALL 排在 NOT_ANY 前面"这个反直觉顺序都保住了）。
比较逻辑在 `worldbook.py:617-628`。

### A-3 `world_info_role` —— **1.19.0 里不存在**

任务书提到的 `world_info_role` 常量在锁定的源码里搜不到。实际机制：

```js
// public/script.js:494-498
export const extension_prompt_roles = { SYSTEM: 0, USER: 1, ASSISTANT: 2 };
```

- `world-info.js:3` 从 `../script.js` 导入它；条目 `role` 默认 `0`（`world-info.js:4117`）。
- 只有 `position === atDepth` 时 `role` 才有意义（`world-info.js:3439`、`5236`、`5243`）。
- Python 侧：`WorldInfoEntry.role` 字段存在（`worldbook.py:111`，`entry_from_dict` 原样透传 `payload.get("role")` `worldbook.py:222`），
  但 `tavern/st/prompt.py:31-33` 的文档明确写了**所有注入统一用 `INJECTION_ROLE = "system"`**（`prompt.py:121`），
  即 `role` 被存储但**在渲染时丢弃**。`VALID_ROLES`（`prompt.py:124`）是字符串集合，与 ST 的数字枚举**类型都不一致**。
  → 结论：**字段 ported、语义 pending**。

### A-4 其它枚举（Python 侧全部缺失）

| ST 常量 | 位置 | 值 | Python |
|---|---|---|---|
| `world_info_insertion_strategy` | `world-info.js:27-31` | `evenly:0, character_first:1, global_first:2` | **无**（`core.py:452` 直接把 `binding.worldbooks` 当顺序） |
| `scan_state` | `world-info.js:43-60` | `NONE:0, INITIAL:1, RECURSION:2, MIN_ACTIVATIONS:3` | **无**。Python 用 `via_recursion: bool`（`worldbook.py:553`）+ `allow_recursion` 近似，**没有 MIN_ACTIVATIONS 这一相** |
| `wi_anchor_position` | `world-info.js:866-869` | `before:0, after:1` | **无**（EM 条目只按 position 分桶，`worldbook.py:49-50`） |
| `extension_prompt_roles` | `public/script.js:494-498` | `SYSTEM:0, USER:1, ASSISTANT:2` | 语义 pending（见 A-3） |
| `GENERATION_TYPE_TRIGGERS` | `public/scripts/constants.js:36-43` | `['normal','continue','impersonate','swipe','regenerate','quiet']` | **无**（`triggers` 字段本身也没移植） |
| `DEFAULT_DEPTH` / `DEFAULT_WEIGHT` / `MAX_SCAN_DEPTH` | `world-info.js:96-98` | `4` / `100` / `1000` | `default_scan_depth=4`（`worldbook.py:78`）、`group_weight=100`（`worldbook.py:119`）；**`MAX_SCAN_DEPTH` 无** |
| `METADATA_KEY = 'world_info'` | `world-info.js:94` | 聊天元数据键 | 无（Python 用 `SessionBinding.worldbooks`） |
| `originalWIDataKeyMap` | `world-info.js:2687-2724` | 37 条 驼峰→V2路径 映射 | **无**（→ #19/#20 pending；也是 #68 的对照表） |

### A-5 `newWorldInfoEntryDefinition` 逐字（`world-info.js:4082-4125`）

```js
export const newWorldInfoEntryDefinition = {
    key: { default: [], type: 'array' },
    keysecondary: { default: [], type: 'array' },
    comment: { default: '', type: 'string' },
    content: { default: '', type: 'string' },
    constant: { default: false, type: 'boolean' },
    vectorized: { default: false, type: 'boolean' },
    selective: { default: true, type: 'boolean' },
    selectiveLogic: { default: world_info_logic.AND_ANY, type: 'enum' },
    addMemo: { default: false, type: 'boolean' },
    order: { default: 100, type: 'number' },
    position: { default: 0, type: 'number' },
    disable: { default: false, type: 'boolean' },
    ignoreBudget: { default: false, type: 'boolean' },
    excludeRecursion: { default: false, type: 'boolean' },
    preventRecursion: { default: false, type: 'boolean' },
    matchPersonaDescription: { default: false, type: 'boolean' },
    matchCharacterDescription: { default: false, type: 'boolean' },
    matchCharacterPersonality: { default: false, type: 'boolean' },
    matchCharacterDepthPrompt: { default: false, type: 'boolean' },
    matchScenario: { default: false, type: 'boolean' },
    matchCreatorNotes: { default: false, type: 'boolean' },
    delayUntilRecursion: { default: 0, type: 'number' },
    probability: { default: 100, type: 'number' },
    useProbability: { default: true, type: 'boolean' },
    depth: { default: DEFAULT_DEPTH, type: 'number' },
    outletName: { default: '', type: 'string' },
    group: { default: '', type: 'string' },
    groupOverride: { default: false, type: 'boolean' },
    groupWeight: { default: DEFAULT_WEIGHT, type: 'number' },
    scanDepth: { default: null, type: 'number?' },
    caseSensitive: { default: null, type: 'boolean?' },
    matchWholeWords: { default: null, type: 'boolean?' },
    useGroupScoring: { default: null, type: 'boolean?' },
    automationId: { default: '', type: 'string' },
    role: { default: 0, type: 'enum' },
    sticky: { default: null, type: 'number?' },
    cooldown: { default: null, type: 'number?' },
    delay: { default: null, type: 'number?' },
    characterFilterNames: { default: [], type: 'array', excludeFromTemplate: true },
    characterFilterTags: { default: [], type: 'array', excludeFromTemplate: true },
    characterFilterExclude: { default: false, type: 'boolean', excludeFromTemplate: true },
    triggers: { default: [], type: 'array', arrayFilter: (value) => GENERATION_TYPE_TRIGGERS.includes(value) },
};
```

**逐字段与 Python 对照**（`entry_from_dict` 在 `worldbook.py:206-244`；`WorldInfoEntry` 定义在 `worldbook.py:96-136`）：

| # | JS 字段 | JS 默认 | Python 字段 | `entry_from_dict` | 状态 / 坑 |
|---|---|---|---|---|---|
| 1 | `key` | `[]` | `keys` | `worldbook.py:210` | ✅ 同时接受 `key`/`keys` |
| 2 | `keysecondary` | `[]` | `secondary_keys` | `worldbook.py:211` | ✅ 同时接受 `keysecondary`/`secondary_keys` |
| 3 | `comment` | `''` | `comment` | `worldbook.py:213` | ✅ |
| 4 | `content` | `''` | `content` | `worldbook.py:212` | ✅ |
| 5 | `constant` | `false` | `constant` | `worldbook.py:214` | ✅ |
| 6 | `vectorized` | `false` | `vectorized` | `worldbook.py:234` | ⚠️ 字段在，但只有注入 `settings.vector_match` 时才可能激活（`worldbook.py:586-591`），且缺 `keyvector` 时永远 false |
| 7 | `selective` | **`true`** | `selective` | `worldbook.py:217`（`_as_bool(payload.get("selective", True), True)`） | ✅ **默认值是 true**，与 #68 里 `convertCharacterBook` 的 `|| false` **方向相反** → 见 §5-2 |
| 8 | `selectiveLogic` | `0` | `selective_logic` | `worldbook.py:218` | ✅ |
| 9 | `addMemo` | `false` | — | — | ❌ 无字段（纯编辑器提示位，服务端可省） |
| 10 | `order` | `100` | `insertion_order` | `worldbook.py:219` | ✅ 同时接受 `order`/`insertion_order` |
| 11 | `position` | `0` | `position` | `worldbook.py:220` | ✅ |
| 12 | `disable` | `false` | `disable` | `worldbook.py:223` | ✅ |
| 13 | `ignoreBudget` | `false` | — | — | ❌ 无字段；ST 的 budget 逻辑用它豁免（`world-info.js:5017-5026`） |
| 14 | `excludeRecursion` | `false` | `exclude_recursion` | `worldbook.py:238` | ✅ |
| 15 | `preventRecursion` | `false` | `prevent_recursion` | `worldbook.py:239` | ✅ |
| 16–21 | `matchPersonaDescription` / `matchCharacterDescription` / `matchCharacterPersonality` / `matchCharacterDepthPrompt` / `matchScenario` / `matchCreatorNotes` | `false` | — | — | ❌ 全部无字段 → **globalScanData 扫描整条链路都没移植** |
| 22 | `delayUntilRecursion` | `0`（**number**） | `delay_until_recursion`（**bool**） | `worldbook.py:240` | ⚠️ **类型降级**：ST 的是"递归等级"，`checkWorldInfo` 为它跑了一整套 `availableRecursionDelayLevels` 机制（`world-info.js:4754-4759`、`4865`、`5129-5133`） |
| 23 | `probability` | `100` | `probability` | `worldbook.py:224` | ✅ |
| 24 | `useProbability` | `true` | `use_probability` | `worldbook.py:225` | ✅ |
| 25 | `depth` | `4` | `depth` | `worldbook.py:221` | ✅ |
| 26 | `outletName` | `''` | — | — | ❌ 无字段（outlet 位置整体未实现） |
| 27 | `group` | `''` | `group` | `worldbook.py:229` | ⚠️ ST 允许逗号分隔多组（`world-info.js:5392`），Python 当单值 |
| 28 | `groupOverride` | `false` | `group_override` | `worldbook.py:231` | ⚠️ 字段在，**无行为**（#64 pending） |
| 29 | `groupWeight` | `100` | `group_weight` | `worldbook.py:230` | ⚠️ 字段在；Python 用在 `_apply_group_scoring`（`worldbook.py:709`），与 ST 的用法（加权随机，`world-info.js:5452-5465`）不同 |
| 30 | `scanDepth` | `null` | `scan_depth` | `worldbook.py:228` | ✅ `_as_optional_int` 把 `0`/`""` 归一成 `None`，`0` 的语义（"不扫聊天"）在 `worldbook.py:549` 保留 |
| 31 | `caseSensitive` | `null` | `case_sensitive` | `worldbook.py:226` | ✅ |
| 32 | `matchWholeWords` | `null` | `match_whole_words` | `worldbook.py:227` | ✅ `None` 表示回退全局默认 |
| 33 | `useGroupScoring` | `null` | `use_group_scoring` | `worldbook.py:232` | ⚠️ 字段在，只被 `_apply_group_scoring` 的全局开关近似（`worldbook.py:669`） |
| 34 | `automationId` | `''` | `automation_id` | `worldbook.py:233` | ✅ 仅字段 |
| 35 | `role` | `0`（enum，数） | `role: str \| None` | `worldbook.py:222` 原样透传 | ⚠️ 类型不一致 + 渲染时丢弃，见 A-3 |
| 36 | `sticky` | `null` | `sticky: int = 0` | `worldbook.py:235` | ⚠️ `null`→`0` 的类型降级，语义在 `ActivationState` 里用回合数近似 |
| 37 | `cooldown` | `null` | `cooldown` | `worldbook.py:236` | ⚠️ 同 #36 |
| 38 | `delay` | `null` | `delay` | `worldbook.py:237` | ⚠️ 同 #36 |
| 39–41 | `characterFilterNames` / `characterFilterTags` / `characterFilterExclude` | `[]`/`[]`/`false`（且 `excludeFromTemplate: true`） | — | — | ❌ 无字段（角色/标签过滤整条链路没移植，`world-info.js:4816-4843`） |
| 42 | `triggers` | `[]`（带 `arrayFilter`） | — | — | ❌ 无字段（生成类型过滤没移植，`world-info.js:4807-4813`） |

**Python 独有、不在 `newWorldInfoEntryDefinition` 里的字段**：

| Python 字段 | 行号 | 说明 |
|---|---|---|
| `uid` | `worldbook.py:100` | ST 条目里也有，但不在 definition 表里（由 `createWorldInfoEntry` 单独赋） |
| `display_index` | `worldbook.py:131`，解析在 `worldbook.py:241` | ST 同名 `displayIndex` 也不在 definition 表里——由 `convertCharacterBook`（`world-info.js:5642`）与导入路径补 |
| `key_vector` | `worldbook.py:134`，解析在 `worldbook.py:243` | ST 的 `keyvector`（向量书专用） |
| `extensions` | `worldbook.py:132`、`worldbook.py:242` | 保留原样，也是 `min_activations`/`max_activations` 的落脚点 |
| `book` | `worldbook.py:136` | 运行时回填来源书名，ST 用的字段名是 `world` |

**Python 自造的两个 `extensions` 键**（ST 1.19.0 的 definition 里没有，属于本插件扩展）：

- `extensions.min_activations`（`worldbook.py:595`）
- `extensions.max_activations`（`worldbook.py:596`）

移植真实 ST 世界书时它们不存在 → 走 `default 1` / `default 0`，等价于 ST 的原语义。

---

## 3. 附表 B：两个 class 逐方法

### B-1 `WorldInfoBuffer`（`world-info.js:199-474`，约 276 行）

"一次 WI 求值所用的扫描缓冲"。Python 侧**没有对应对象**，能力被摊进
`worldbook.activate` 的闭包（`scan_depth_for` `worldbook.py:537`、`scan_text` `worldbook.py:546`）与
`key_matches`。

| 成员（行号） | 职责 | Python 目标 | 状态 |
|---|---|---|---|
| `static externalActivations` 203 | `${world}.${uid}` → 强制激活的条目 | `worldbook.WorldInfoBuffer.external_activations`（建议） | pending |
| `#globalScanData` 208 | persona/角色/场景等聊天外文本 | 无（`activate` 没有该参数） | pending |
| `#depthBuffer` 213 | 按深度升序的聊天文本 | `activate(messages=...)` 参数 `worldbook.py:505` | ported |
| `#recurseBuffer` 218 | 递归扫描产生的文本 | `fresh_texts: list[str]` `worldbook.py:534` | ported |
| `#injectBuffer` 223 | 扩展注入的、可扫描的 prompt | 无 | pending |
| `#skew` 228 | min-activations 的深度偏移 | 无 | pending |
| `#startDepth` 233 | 扫描起点深度 | 无（隐含为 0） | pending |
| `constructor(messages, globalScanData)` 240 | 初始化 | `activate` 内部闭包，无独立对象 | — |
| `#initDepthBuffer(messages)` 250 | 填缓冲，上限 `MAX_SCAN_DEPTH` | `scan_text(depth)` `worldbook.py:546-551` | ported |
| `#transformString(str, entry)` 268 | 按 `caseSensitive ?? 全局` 转小写 | `key_matches` 的 lower 分支 `worldbook.py:396-399` | ported |
| `get(entry, scanState)` 279 | 切片缓冲 + 拼 6 类 globalScanData + inject + recurse | `scan_depth_for` + `scan_text` `worldbook.py:537-551` | ported（大幅简化：**丢了 `\x01` 分隔符、globalScanData、inject buffer、MIN_ACTIVATIONS 时排除 recurse 的规则**） |
| `matchKeys(haystack, needle, entry)` 337 | 单 key 匹配（regex 优先，否则按 whole-word 分支） | `worldbook.key_matches` `worldbook.py:378-400` + `_whole_word_match` `worldbook.py:366-375` | ported（whole-word 分支行为有差异，见 §5-3） |
| `addRecurse(message)` 372 | 追加递归文本 | `fresh_texts.append(...)` `worldbook.py:646` | ported |
| `addInject(message)` 380 | 追加注入文本 | 无 | pending |
| `hasRecurse()` 388 | 递归缓冲是否非空 | 近似：`len(activated)` 是否增长 `worldbook.py:659-664` | ported（近似） |
| `advanceScan()` 395 | skew++ | 无 | pending |
| `getDepth()` 402 | `world_info_depth + skew` | `scan_depth_for` `worldbook.py:537`（无 skew） | ported（无 skew） |
| `getExternallyActivated(entry)` 411 | 查 `externalActivations` | 无 | pending |
| `resetExternalEffects()` 419 | 清空 `externalActivations` | 无 | pending |
| `getScore(entry, scanState)` 428 | 主键/次键命中计数 → 组内打分 | **无直接对应**；`_apply_group_scoring` `worldbook.py:697` 用的是 `group_weight` | pending（建议 `worldbook.key_match_score`） |

### B-2 `WorldInfoTimedEffects`（`world-info.js:479-793`，约 315 行）

"sticky / cooldown / delay 管理器"，状态持久化在 `chat_metadata.timedWorldInfo`。
Python 对应物是 `tavern/st/worldbook.py::ActivationState`（`worldbook.py:425-495`），
但**时钟与身份都换了**：ST 用 `chat.length`（消息条数）+ 内容 `hash`；Python 用自己维护的回合计数
（`next_turn` `worldbook.py:450`）+ `(source_path or name, uid)`（`worldbook.py:453-458`），且**不落盘**。

| 成员（行号） | 职责 | Python 目标 | 状态 |
|---|---|---|---|
| `#chat` 484 | 聊天消息数组（长度即时钟） | `ActivationState.turn` `worldbook.py:444` + `main.py` 调用 `next_turn` | ported（换成回合数） |
| `#entries` 490 | 参与扫描的全部条目 | `activate(books=...)` 参数 `worldbook.py:504` | ported |
| `#isDryRun` 496 | 干跑时是否跳过写回 | 无 | pending |
| `#buffer` 502 | `{sticky:[], cooldown:[], delay:[]}` | `_sticky_until` / `_cooldown_until` / `_ever_fired` `worldbook.py:445-448` | ported |
| `#onEnded` 512 | sticky 结束时**立刻开 cooldown**（`world-info.js:518-529`） | 无 | pending ← 真实缺口 |
| `constructor(chat, entries, isDryRun)` 549 | 初始化 + 保证元数据结构 | `ActivationState.__init__` `worldbook.py:443` | ported |
| `#ensureChatMetadata()` 559 | 建/修 `chat_metadata.timedWorldInfo` | 无（Python 状态在内存，按 chat 建实例：`core.py:411-419`） | ported（换了存储） |
| `#getEntryHash(entry)` 584 | 取条目内容 hash | 无 | pending |
| `#getEntryKey(entry)` 593 | `${entry.world}.${entry.uid}` | `ActivationState._identity` `worldbook.py:453-458` | ported（key 构成不同） |
| `#getEntryTimedEffect(type, entry, isProtected)` 604 | `{hash, start: chat.length, end: chat.length + duration, protected}` | `on_activate` `worldbook.py:472-495`（只记 `until` 绝对回合，无 `start`/`protected`） | ported（简化） |
| `#checkTimedEffectOfType(type, buffer, onEnded)` 619 | 遍历元数据，四种清理（聊天没推进 / 条目不存在 / 条目不再配置 / 区间已过） | `is_blocked` `worldbook.py:460-467`、`is_sticky` `worldbook.py:469-470` | ported（**四种清理全无**；`protected` 概念在 `delay` 上体现为"只挡第一次"，`worldbook.py:464-467`） |
| `#checkDelayEffect(buffer)` 666 | delay 条目（`chat.length < entry.delay`） | `is_blocked` 的 delay 分支 `worldbook.py:467` | ported |
| `checkTimedEffects()` 682 | 统一入口 | 无独立入口（逻辑散在 `is_blocked`/`is_sticky`，由 `activate` 调用） | ported（无入口） |
| `getEffectMetadata(type, entry)` 696 | 读某条目的效果元数据 | 无 | pending |
| `#setTimedEffectOfType(type, entry)` 710 | 首次记录效果（不覆盖） | `on_activate` `worldbook.py:472` | ported |
| `setTimedEffects(activatedEntries)` 730 | 对本轮激活条目统一登记 | `activate` 里的 `state.on_activate(...)` `worldbook.py:650` | ported |
| `setTimedEffect(type, entry, newState)` 744 | 手动强制开/关效果（斜杠命令用） | 无 | pending |
| `isValidEffectType(type)` 767 | `'sticky'\|'cooldown'\|'delay'` | 无 | pending |
| `isEffectActive(type, entry)` 777 | 某类型效果当前是否生效 | `is_sticky` `worldbook.py:469` + `is_blocked` `worldbook.py:460` | ported |
| `cleanUp()` 789 | 清空三个 buffer | 无 | pending |

---

## 4. 附表 C：关键顶层函数的依赖清单

分类口径：**纯逻辑** = 只吃参数、无外部状态；**全局设置** = 读模块级可变变量/设置对象；
**DOM/浏览器** = jQuery、`document`、select2、toastr、i18n、`eventSource`；**运行时上下文** = `getContext()`/`this_chid` 这类"当前界面状态"。

### 4-1 `checkWorldInfo`（4709–5282，574 LOC）

依赖列表来自 `dep_world-info.json`，逐个定性：

| 依赖 | 类别 | 备注 |
|---|---|---|
| `WorldInfoBuffer` | 纯逻辑（内部类） | 见 §3-B1 |
| `WorldInfoTimedEffects` | 纯逻辑（内部类） | 见 §3-B2 |
| `wi_settings_vars` | **全局设置** | 12 个 `world_info_*`（`world-info.js:69-82`）→ Python 对应 `WorldBookConfig` |
| `extension_settings` | **全局设置** | note 模块设置，Python 无 |
| `shouldWIAddPrompt` | **全局设置** | 决定是否把 ANTop/ANBottom 拼进 Author's Note（`world-info.js:5268-5272`）→ Python 无 |
| `substituteParams` | 纯逻辑 + 全局宏表 | ST 会**就地改写** `entry.content`（`world-info.js:5058`）；Python 在渲染期做宏（`prompt.py:430`），激活期不做 → 行为差异 |
| `metadata_keys` / `NOTE_MODULE_NAME` | 常量 | Python 无 |
| `extension_prompt_roles` | 常量 | 见 A-3（Python 丢弃 role） |
| `getTokenCountAsync` | 纯逻辑（异步 tokenizer） | Python 是同步的 `worldbook.greedy_token_count`（`worldbook.py:716`）/ `prompt.count_tokens`（`prompt.py:664`） |
| `getRegexedString` | 纯逻辑 + 全局设置 | 激活后跑 regex 扩展（`world-info.js:5205`）；Python 侧 `core.apply_regex_rules`（`core.py:691`）是出站消息处理，**不是** WI 内容管道 |
| `UUID/misc-math`（`Math.random`/`uuidv4`） | 纯逻辑（随机/ID） | Python `settings.rng`（`worldbook.py:93`）可注入 |
| `chat_metadata` | **隐式全局状态** | 持久化的聊天元数据；Python 用 `SessionBinding`（`core.py:63`）+ 内存 `ActivationState` |
| `eventSource` + `WORLDINFO_SCAN_DONE` | DOM/事件总线 | 每轮循环发事件并允许监听者改 `scanState`/`budget`（`world-info.js:5150-5186`）→ Python 无 |
| `getContext()` | **运行时上下文** | 提供 `extensionPrompts`、`tagMap`（`world-info.js:4719`、`4830`）→ Python 无 |
| `getExtensionPromptByName` | 运行时上下文 | 供 inject buffer 用（`world-info.js:4721`）→ Python 无 |
| `getCharaFilename` | **全局状态** | `this_chid` + `characters[]` → Python 无 |
| `getTagKeyForEntity` | **全局状态** | `this_chid` 的标签表（`world-info.js:4827`）→ Python 无 |
| `this_chid` | **全局状态** | 当前角色下标 → Python 用 `binding.card_id` |
| `toastr` | DOM | 溢出告警（`world-info.js:5066`）→ Python 应换成 logger |

**结论**：`checkWorldInfo` 的 574 行里，真正"服务端可移植的纯逻辑"约占一半——
主循环框架、primary/secondary key 判定、4 种 selectiveLogic、概率、budget、组过滤、按 position 分桶；
另一半（事件、context、角色/标签过滤、Note 注入、i18n）在 AstrBot 场景下应当**替换或删除**，
而不是硬搬。Python 的 `activate`（`worldbook.py:503-694`，192 行）就是这个裁剪后的版本。

### 4-2 `getWorldInfoPrompt`（892–915，24 LOC）

依赖只有 `eventSource`（`world-info.js:902`，发 `WORLD_INFO_ACTIVATED`）→ **DOM/事件（可省）**。
函数本体是纯数据整形。Python 对应：`core.build_turn` `core.py:460-470` + `core._targets_from_entries` `core.py:624`。
**唯一实质缺口**：ST 返回 `outletEntries`，Python 没有 outlet 桶。

### 4-3 `convertCharacterBook`（5617–5674，58 LOC）

依赖只有 `extension_prompt_roles`（常量）+ `newWorldInfoEntryTemplate`（模块级常量）+ `DEFAULT_DEPTH`/`DEFAULT_WEIGHT`
→ **纯逻辑**，可以无痛 1:1 移植。Python 侧 pending，唯一已有的实现是测试 oracle
`tools/st-oracle/run_python.py::convert_character_book`（`run_python.py:56-108`），且没有接进 `tavern/`。

### 4-4 `parseDecorators`（4652–4698，47 LOC）

`deps` 为**空** → **纯逻辑，零依赖**（只用模块常量 `KNOWN_DECORATORS = ['@@activate','@@dont_activate']`，`world-info.js:100`）。
纯函数、无 I/O、无异步 → **最该第一个移植**（建议 `worldbook.parse_decorators`），单测可以完全用 ST 的
`tools/st-oracle/fixtures/10-decorators.json` 做对照。

### 4-5 `filterByInclusionGroups`（5388–5475，88 LOC）

依赖只有 `UUID/misc-math`（即 `Math.random`，`world-info.js:5453`）+ 两个内部函数
`filterGroupsByTimedEffects` / `filterGroupsByScoring` → **纯逻辑**。
坑在于它**同时**读 `WorldInfoBuffer.getScore`（所以需要 buffer）和 `WorldInfoTimedEffects`
（所以需要时间效果对象）→ Python 想移植必须先补 §3-B2 的 `getScore`。
另外它**原地改 `newEntries`**（`removeEntry` 用 `splice`，`world-info.js:5406`），移植时别改成"返回新列表"而漏掉副作用。

### 4-6 `splitKeywordsAndRegexes`（2797–2815，19 LOC）

`deps` 为**空** → **纯逻辑**，但内部调 `customTokenizer`（#22，54 行）与 `getSelect2OptionId`。
`customTokenizer` 也不依赖浏览器（`deps` 为空）→ 两者一起移植即可。
⚠️ 不要被它所在的位置误导：它周围的 `enableKeysInputHelper` 等全是 DOM，这两个不是。

### 4-7 `parseRegexFromString`（2901–2926，26 LOC）

`deps` 为**空** → **纯逻辑**。Python 已有 `worldbook.compile_key`（`worldbook.py:340-363`），
但如 §1-#24 所列有三处语义差异，建议在移植其它部分时**顺手对齐**（尤其是"pattern 里未转义的 `/` 要拒绝"）。

### 4-8 补充：`getWorldInfoSettings` / `setWorldInfoSettings` / `updateWorldInfoSettings`

依赖 `wi_settings_vars`（全局设置）、`selected_world_info`、`accountStorage`（浏览器 localStorage 包装）、
`WorldInfoBuffer`、`chat_metadata`、`eventSource`、`jQuery$`
→ **全局设置 + DOM 混合**。Python 用不可变配置对象取代了整块，
`accountStorage`/`jQuery$` 部分直接 exempt。

---

## 5. 最容易移植错的 5 处

### 5-1 `parseDecorators` 的两条反直觉规则（`world-info.js:4652-4698`）

1. **`@@@foo` 会被"降级"成 `@@foo`**：`isKnownDecorator` 先 `substring(1)` 去掉一个 `@` 再比对
   （`4659-4661`），push 时也对 `@@@` 做 `substring(1)`（`4684`）。也就是说三斜线写法是"转义版双斜线"，
   最终落库的 `decorators` 值**永远只有一个 `@`**。照抄判定条件时若把 `@@@` 当成不认识的装饰器，行为会反。
2. **遇到不认识的 `@@` 行是 fallback 而不是 break**：`fallbacked = true` 后继续循环，
   只有下一条**认识**的装饰器才会重新接受（`4683-4688`）。而且 `@@@` 在第一轮会被 `continue` 直接跳过（`4679-4681`）。
   写成"遇到未知就停"会少认装饰器，写成"遇到未知就继续 push"会多认。

这条纯逻辑 47 行，却因为这两个状态位最容易在第 4 次重构时被改坏——建议配 ST 的
`tools/st-oracle/fixtures/10-decorators.json` 逐例对照。

### 5-2 `selective` 的默认值在两条路径上**方向相反**

- ST 原生世界书：`newWorldInfoEntryDefinition.selective` 默认 **`true`**（`world-info.js:4089`），
  所以 `addMissingWorldInfoFields` 给老书补字段时会把它补成 true。
  Python 的 `entry_from_dict` 抓对了这一点（`worldbook.py:217`，注释也写了）。
- V2 卡内世界书：`convertCharacterBook` 用的是 **`entry.selective || false`**（`world-info.js:5634`），
  于是缺字段时是 **`false`**。

**后果**：同一份语义（"没写 selective 字段"），从原生书读进来是 `true`（要看次键），
从卡里读进来是 `false`（不看次键）。`checkWorldInfo` 又只在 `keysecondary` 非空时才理会 selective
（`world-info.js:4924-4928`），所以**只有"有次键但没写 selective"的条目**才会表现出差异——
正好是最常见的导出形态。移植 #68 时必须**逐字照抄 `|| false`**，不能"顺便统一成 true"。

### 5-3 whole-word 匹配的分支与边界集（`world-info.js:337-366` vs `worldbook.py:366-400`）

ST 的 `matchKeys` 在 `matchWholeWords` 下**先按空格切词**（`350`）：

- key 是**多词**（`keyWords.length > 1`）→ 直接 `haystack.includes(key)`，**完全不做边界检查**；
- key 是**单词** → 用 `(?:^|\W)(escapeRegex(key))(?:$|\W)`（`356`）。

Python 的 `_whole_word_match`（`worldbook.py:366-375`）改成按"key 是否全为 `\w`+CJK"分类：

- 全词字符 → `(?<!\w)key(?!\w)`；
- 否则 → 退化成子串匹配。

差异点：`C++`、`A-1`、`foo.bar` 这类**不以词字符结尾的 key**，ST 走的是 `(?:^|\W)(C\+\+)(?:$|\W)`
（要求右边是行尾或非词字符），Python 走子串。另外 Python 的 `(?<!\w)`/`(?!\w)` 与 JS 的 `\W`
在 Unicode 属性上不完全等价，CJK 场景下 Python 多做了一层 `_WORD_CHARS` 白名单回退（`worldbook.py:331`）。
移植其它匹配路径（如正则 key）时不要顺手"统一"这两套边界逻辑。

### 5-4 `scan_state` 是四态机，`delayUntilRecursion` 是**等级**不是布尔

ST 的主循环是 `while (scanState)`（`world-info.js:4766`）——靠 `scan_state.NONE === 0` 为假值退出。
四态分别是 `INITIAL`(1) / `RECURSION`(2) / `MIN_ACTIVATIONS`(3) / `NONE`(0)，并且：

- `world_info_max_recursion_steps` 与 `world_info_min_activations` **互斥**（`4767-4771`）；
- min-activations 阶段会 `buffer.advanceScan()` 加大深度并**再扫一遍**（`5111-5126`）；
- `MIN_ACTIVATIONS` 时 `buffer.get` **不拼递归缓冲**（`world-info.js:323`，注释写得很明确）；
- 若 min-activations 之后递归缓冲非空，必须**先做一次递归扫描**再继续加深度（`5104-5107`）；
- `delayUntilRecursion` 是数字等级：先收集所有等级（`4754-4757`），
  每轮只放行 `<= currentRecursionDelayLevel` 的条目（`4865-4868`），
  扫完后若有剩余等级就**再起一轮 RECURSION**（`5129-5133`）。

Python 的 `activate` 只有"第一遍 + 最多 `max_recursion_steps-1` 遍递归"（`worldbook.py:653-664`），
`delay_until_recursion` 被降成 bool（`worldbook.py:130`），**没有 MIN_ACTIVATIONS 相**，
也没有"递归与 delay 等级交替"的次序。这是 `checkWorldInfo` 里最容易"看起来跑通了但结果不同"的部分——
建议移植时**照着 `while` 状态机重写**，而不是在现有 `for` 循环上打补丁。

### 5-5 timed effects 的**身份**与**时钟**（`world-info.js:479-793` vs `worldbook.py:425-495`）

三处同时不同：

1. **身份**：ST 用 `${entry.world}.${entry.uid}` 做键（`593`），并额外存 `hash = getStringHash(JSON.stringify(entry))`
   （`4632`）用于在元数据里找回条目（`624`）。Python 用 `(book.source_path or book.name, entry.uid)`
   （`worldbook.py:453-458`）——注释解释了为什么必须带 `source_path`（同名书会互相遮蔽），
   但**没有内容 hash**，所以"改了内容之后旧效果还算不算"这件事 ST 与 Python 答案不同。
2. **时钟**：ST 的 `start`/`end` 是 `chat.length`（消息条数，`607-608`），
   并且有"聊天没推进就删掉该效果"的保护（`626-630`）和 `protected` 标志（`144`、`609`）。
   Python 用自己维护的 `turn` 计数（`worldbook.py:444`、`450`），**不落盘**——
   重启服务或切回旧聊天后所有 sticky/cooldown/delay 归零，ST 则从 `chat_metadata` 恢复。
3. **联动**：ST 里 sticky 结束时**立刻给条目开 cooldown**（`518-529`，`#onEnded.sticky`），
   并顺手推进本次求值的 `#buffer.cooldown`。Python 完全没有这段（`on_activate` 只在激活时开窗口，
   `worldbook.py:472-495`）。所以"sticky 3 → 立刻 cooldown 5"这种配置在两边行为**完全不同**。

**另外两条容易漏的**（不在那 5 处里，但一样致命）：

- **`\x01` 分隔符**：`WorldInfoBuffer.get` 返回的 haystack **以 `\x01` 开头**，段间用 `'\n' + '\x01'` 连接
  （`world-info.js:295-297`）。后果是**任何 `^` 锚定的正则 key 永远不会命中**。
  Python 的 `scan_text`（`worldbook.py:551`）用普通 `\n` 拼接，`^` 正则会命中。
- **内容就地宏替换**：ST 在激活成功后 `entry.content = substituteParams(entry.content)`（`5058`），
  改的是缓存里的条目对象；Python 激活期不做宏替换（渲染期才做，`prompt.py:430`），
  所以同一条目在多次扫描之间"内容会不会变"两边不同。

---

## 6. Python 现状锚点（便于 grep 复核）

| 概念 | 位置 |
|---|---|
| 条目 schema / 默认值 | `tavern/st/worldbook.py:96-136`、`entry_from_dict` `:206-244` |
| 书容器解析 | `book_from_dict` `tavern/st/worldbook.py:259-293`、`load_world_book` `:296`、`scan_world_books` `:310` |
| key 匹配 | `is_regex_key` `:334`、`compile_key` `:340`、`_whole_word_match` `:366`、`key_matches` `:378` |
| 激活主流程（≈ `checkWorldInfo`） | `activate` `tavern/st/worldbook.py:503-694` |
| sticky/cooldown/delay | `ActivationState` `tavern/st/worldbook.py:425-495` |
| 组打分 | `_apply_group_scoring` `tavern/st/worldbook.py:697-713` |
| token 计数 | `greedy_token_count` `tavern/st/worldbook.py:716`、`_tiktoken_counter` `:737`；`prompt.count_tokens` `tavern/st/prompt.py:664` |
| 配置 | `WorldBookConfig` + `TavernConfig.from_raw` `tavern/config.py:192-219` |
| 会话级书目绑定 | `SessionBinding` `tavern/core.py:63-106`；`toggle_book` `:372`、`set_books` `:388` |
| 装配入口（≈ `getWorldInfoPrompt`） | `PluginCore.build_turn` `tavern/core.py:422-530`；`_targets_from_entries` `:624-637` |
| 位置 → prompt 落点 | `InChatTargets` `tavern/st/prompt.py:270-338`、`build_messages` `:805` |
| 注入角色（写死 system） | `INJECTION_ROLE` `tavern/st/prompt.py:121`、`VALID_ROLES` `:124` |
| 命令面 | `/tavern worldbook list\|on\|off` `tavern/main.py:506-544` |
| 对照测试装置（非生产路径） | `tools/st-oracle/`（`run.mjs` / `run_python.py` / 10 个 fixtures） |
| 现有测试 | `tests/test_worldbook.py` |
