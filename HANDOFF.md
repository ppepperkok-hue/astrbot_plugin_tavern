# 交接文件 — AstrBot 酒馆插件（astrbot_plugin_tavern）

写于一次会话中断之后。目的：让接手的人**不看聊天记录也能继续**，并且知道哪些事已经证明过、
哪些只是声称。

---

## 0. 一句话现状

**四个判定机全绿，`tools/check.py` 11 步全过。**
世界书引擎（S1，15 fixtures 12 match/0 diverged/3 不可比）、消息模型（S2，1 条一致）、装配顺序
（S2，8 个 fixture **全一致**）、provider 报文转换（S4，**121 个 fixture 全一致**）；
**515 项离线测试通过**（+1 skip，缺时区库）。

在判定机之外还有三条护栏，都接进了 `check.py`：导出覆盖（21/21，防漏移植）、**管理面板路由**
（7 条，用 AstrBot 自己的匹配器验证）、**指令参数解析**（9 条真实输入，用 AstrBot 自己的
`CommandFilter` 验证）。后者抓到过一个真 bug，见 §6。

**已经能做的前端与外部对接**：`pages/panel/index.html` 是 AstrBot 面板里的管理页（单文件、
无需构建）；`tavern/backends/st_import.py` 可以从运行中的酒馆**只读**拉取角色卡、世界书与聊天。

**S2 装配判定机的第三道判定机已经修好**（`diff_assembly.py` 现在 PASS）。**端口一行没改** ——
那 5 个红着的 fixture 全是判定机自己的缺陷，连交接文件里判定为"疑真端口差异"的
`04-continue-prefill` 也不是。§4 有完整的 14 条缺陷清单与证据。

---

## 1. 仓库与环境

| 项 | 值 |
|---|---|
| 工作目录 | `E:\astrbot_plugin` |
| 远端 | `https://github.com/ppepperkok-hue/astrbot_plugin_tavern`（公开，`git push` 走 `gh` 凭据助手） |
| 分支 / 最新提交 | `main` / `e6836d2`（写这份文件时；判定机修好之后又有新提交） |
| 许可证 | AGPL-3.0（为了让酒馆源码可以被直接翻译复用；每个移植文件头都有出处声明） |
| Python | 3.11.9；AstrBot 4.28.2 装在 `.tools/uv-tools/astrbot/`（用它的 python 跑 AstrBot 相关脚本） |
| Node | v22.22.1（判定机需要） |
| 酒馆快照 | `research/_raw/st-src/`（1.19.0，commit `06bde939`，与 tag `1.19.0` 逐字节一致） |

**重要**：插件的 runtime 数据必须在 `data/plugin_data/astrbot_plugin_tavern/`，**不要**留在仓库根目录的
`data/`（那会让 e2e 读到脏数据、断言错位；已经踩过一次）。`data/` 已在 `.gitignore` 里。

---

## 2. 验证入口（先跑这个再动任何代码）

```powershell
cd E:\astrbot_plugin
python tools/check.py                          # 一把梭：11 步，见下
python tools/st-oracle/diff.py --all           # S1：世界书引擎 vs 真酒馆
python tools/st-oracle/diff_prompt.py --all    # S2：消息模型 vs 真酒馆
python tools/st-oracle/diff_assembly.py --all --verbose   # S2：装配顺序 vs 真酒馆（PASS）
python tools/st-oracle/diff_converters.py --all # S4：provider 报文转换（121 个 fixture）
python tools/st-oracle/check_converter_coverage.py  # 导出覆盖护栏
python tools/st-oracle/check_module_wiring.py  # 有测试但没人调用的模块（报告，不拦）
python tools/st-oracle/gen_adapter.py --check  # 判定机生成树是否漂移
```

`tools/check.py` 现在跑的 11 步：`manifest` / `plugin size` / `readme links` / `ruff check` /
`ruff format` / `pytest` / `astrbot smoke` / `astrbot e2e` / `panel api` / `command params`。
后四个需要 AstrBot 解释器（`.tools/uv-tools/astrbot/Scripts/python.exe`）。

### 实测结果（修完之后重跑的）

| 命令 | 结果 |
|---|---|
| `python tools/check.py` | **ALL CHECKS PASSED**（11 步） |
| `python -m pytest tests` | **515 passed, 1 skipped**（唯一 skip 是 `tests/test_prompt.py:151`，缺时区库） |
| `python -m ruff check .` | All checks passed |
| `diff.py --all`（S1） | 15 fixtures，**12 match / 0 diverged / 3 not-comparable** → PASS |
| `diff_prompt.py --all`（S2 消息模型） | 1 fixture，**match** → PASS |
| `diff_assembly.py --all`（S2 装配） | 8 fixtures，**8 match / 0 diverged / 0 not-comparable** → **PASS** |
| `diff_converters.py --all`（S4） | 121 fixtures，**121 match / 0 diverged** → **PASS** |
| `tools/check_plugin_size.py` | 发布包 **约 0.35 MB**，市场 16 MB 上限的 2% → PASS |

三个 `not-comparable` 是**真的比不了**，不是偷懒：两个是加权随机（酒馆用 `Math.random()`，我们没法复刻
JS 的 RNG 流），一个是 token 预算（酒馆用真 tokenizer，我们只有长度估算）。这三条都写了确定性场景去盯边界。

---

## 3. 已完成什么（以及各自的证据）

### S1 世界书引擎 —— 完成
`tavern/st/worldbook.py` + `wi_buffer.py` + `wi_keywords.py` + `wi_decorators.py` + `wi_timed.py` +
`wi_scan_state.py`，按 `world-info.js` 逐个函数镜像移植。15 个 fixture、12 个可比场景**逐字一致**。

判定机抓到过的、只有实跑才能发现的坑（都已照抄并测试固定）：
- 激活顺序是**升序**（`:88` 降序 sort + `:5214` unshift 抵消）；同 order 时 **uid 大的在前**。
- token 预算是**顺序硬截断**（`:5061-5073`，超了就丢后续），`ignoreBudget` 条目仍可穿过。
- `delay` 比的是 **chat 消息条数**，不是轮次。
- `delayUntilRecursion` 是**数字层级**（`true` 等于 1），不是布尔。
- `preventRecursion` 管的是"我的内容进不进递归缓冲"，**不是**"我能不能被递归激活"。
- 整词匹配必须用 **ASCII 语义**（JS 的 `\w` 只认 ASCII；照抄不加以限制会让中文关键词全漏配）。
- inclusion group 的计时状态在**扫描开始前冻结**，本轮刚点燃的 sticky 不能反过来影响本轮决策。

### S3（导入）—— 完成并接通
`tavern/st/importers.py` + `exporters.py`；42 条字段双向映射、`lorebook_v3`/V2/AgnAI/Risu/NovelAI 识别、
卡内嵌 `character_book` 自动拆成独立世界书、聊天 `.jsonl` 容错。
**用户可用路径**：把卡或世界书**直接发给机器人**即可导入（`tavern/core.py::import_uploaded_file`，
文件处理放在触发规则**之前**——因为发卡没有文字，`is_triggered` 会先把消息丢掉）。
按用户决定，**不做** `/tavern` 的卡/书管理子指令。

### S2 消息模型 —— 完成
`tavern/st/chat_completion.py`：`TokenHandler` / `Message` / `MessageCollection` / `ChatCompletion`
四类，`diff_prompt.py` 与真 `openai.js` 逐字段一致，30 项离线测试。

### S2 装配层 —— 完成，判定机 8/8
`tavern/st/prompt_build.py`（约 1750 行）+ `tests/test_prompt_build.py`（64 项，全绿）。
`populationInjectionPrompts` / `populateChatHistory` / `populateDialogueExamples` /
`populateChatCompletion` / `preparePromptsForChatCompletion` 逐个函数对照移植。
装配判定机 `diff_assembly.py` 8 个 fixture **全一致**；**这一轮端口侧一行没改**（见 §4）。

---

## 4. S2 装配判定机：已修好（端口一行没改）

### 4.1 事情经过（留着当教训）

1. 一个工人移植完装配层，并把判定机跑到 **8 fixtures 全 match**——但那版 harness **没有提交**。
2. 我为了回退自己的一个实验，对那两个 harness 文件执行了 `git checkout -- <file>`，
   **把工人未提交的工作一起覆盖掉了**。
3. 我按同样思路重建：`gen_adapter.py` 现在把真的 `Prompt`/`PromptCollection`/`INJECTION_POSITION`
   从快照里按花括号配对抽出来（逐字，加上它们读的 `DEFAULT_DEPTH`/`DEFAULT_ORDER`），
   `getExtensionPrompt*` 也给真值覆盖；`run_assembly.mjs` 不再运行时改写生成树。
4. 重建过程中修掉 8 个 **harness 侧**缺陷（§4.2），差异从"8 个 fixture 全 node-error"
   降到 3 match / 5 diverged / 6 处差异。
5. **收尾**：又修掉 6 个 harness 侧缺陷（§4.3），5 个红 fixture 全部转绿 —— **8 match / 0 diverged**。

### 4.2 重建时修掉的 8 个 harness 缺陷（每个都曾冤枉端口）

| # | 缺陷 | 证据 |
|---|---|---|
| 1 | `PromptManager.js` 被当桩渲染，`new Prompt(chatPrompt)` 拿到假代理，轮次丢 role/content | 历史组只剩 `newMainChat` |
| 2 | `PromptCollection.override(prompt, position)` 要的是 **Prompt 不是标识符** | `PromptManager.js:294-297` |
| 3 | `preparePrompt` 原样返回导致轮次正文为空；轮次正文在 `mes`，需映到 `content` | `openai.js:955` + `:4047` 的丢弃守卫 |
| 4 | `Message.fromPromptAsync`（`3792-3794`）**立刻解引用参数**，`impersonate`/`quietPrompt` 缺失即抛错、整场装配中断 | 探针：`Cannot read properties of undefined (reading 'role')` |
| 5 | fixture 用 `mes` 写轮次正文，runner 读 `turn.content` | fixture 实际形状 |
| 6 | fixture 旧→新列轮次，而 `setOpenAIMessages`（`570-649`，由 `script.js:4830` 驱动）交出**新→旧** | 端口/真引擎历史顺序相反 |
| 7 | `messageExamples` 是**消息块数组**，块内条目正文在 `content` | fixture 实际形状 |
| 8 | `isValidName`/`sanitizeName` 用了近似实现 | 逐字应为 `^[a-zA-Z0-9_]{1,64}$` 与 `[^a-zA-Z0-9_] → _` + 64 截断（`PromptManager.js:1343-1351`） |

### 4.3 收尾修掉的 6 个 harness 缺陷（这才是那 5 个红 fixture 的真正原因）

| # | 缺陷 | 怎么证实的 | 影响面 |
|---|---|---|---|
| 9 | **可选的系统提示词压根没进集合**。`preparePromptsForChatCompletion` 总会把 `impersonate`(1382)、`quietPrompt`(1383)、`bias`(1385)、`enhanceDefinitions`(2049) 并进来，而 fixture 只声明它要考的那些。`populateChatCompletion` 用**裸 `prompts.get()`** 读前两个（`1224/1229`），于是**真引擎自己抛 TypeError**，装配在 `main` 之后当场中断，截断的快照看起来就像"端口多给了" | 探针打印 `prompts.get` 的每次落空：`[["main","main"],["impersonate","MISS->undefined"]]` | `01/03/04/07/08` 全部 |
| 10 | **`cyclePrompt` 从来没传进去**，真引擎的 `continueNudge` 分支（`907-927`）永远不走，端口的却走 | 探针：端口有多出的一条 nudge，真引擎没有 | `08` |
| 11 | **`new_example_chat_prompt` 被写成了 `'[Start a new Chat]'`**（harness 把新聊天串套到了示例横幅上），横幅 token 数被改，31 的预算就丢错了组 | 读 `openai.js:108-111`：四个横幅字面量各不相同 | `03/07` |
| 12 | **`Message.createAsync` 没接管计数器**，示例条目 token 恒为 0（引擎在 `createAsync` 里还会顺手填 content，所以正文也是空的） | 探针：`canAffordAll need=0`、`content=""` | `01/03/07` |
| 13 | **示例正文按 `mes` 传**，而 `populateDialogueExamples` 读 `content`（`1116`）；真引擎条目变空后被 `getChat()` 丢掉（`4125`） | 探针：真引擎 `insert dialogueExamples 0-0 content=""` | `01/03/07` |
| 14 | **计数器用的是引擎桩**（1.5 字符/token，`newMainChat`=19），端口是 `len//3`（=8）。两套货币比同一个 31 的预算，结论必然不同 | 端口/真引擎同一条消息的 token 数对照 | `03/07` |

**关于 `04-continue-prefill`**：交接文件原来判定它"疑真端口差异"，**这个判断是错的**。
端口 `prompt_build.py:1194-1209` 已经照抄了 `openai.js:1318-1331` 的门控
（`isAssistantRole and supportsAssistantPrefill`，来源必须是 `claude`）。它红，是因为
真引擎压根没跑到那一行（缺陷 9）。真引擎能跑完之后，两边逐字一致。

### 4.4 顺带补上的护栏

`diff_assembly.py` 从重建起就在读 `comparable.chat.ok`，但**从来没人写这个键** ——
护栏是死的。那时候真引擎中途抛错，会拿"截断的真引擎"去比"完整的端口"，然后判端口有罪。
现在 `run_assembly.mjs` 会发布它（带 reason），并且**验证过它真的会触发**：
把可选提示词的补位关掉，真引擎抛 TypeError，该键回 `false` 且 reason 里带着那句 TypeError。

### 4.5 探针法（这轮唯一有效的手段，附可复现命令）

在 `run_assembly.mjs` 里 monkeypatch 真引擎的方法来记录调用，例如：

```js
const originalAdd = ChatCompletion.prototype.add;
ChatCompletion.prototype.add = function (collection, position) {
    console.log('add', collection?.identifier, position, collection?.collection?.length);
    return originalAdd.call(this, collection, position);
};
```

这轮真正好用的做法是**在 `prompts.get` 上包一层，把每次落空打出来**（一次就定位了缺陷 9），
以及**把 `canAffordAll` / `insert` / `reserveBudget` 全部包一层打印 token 账**（一次定位了 12/14）。

**探针用完要撤**，但别再 `git checkout` 撤（§4.1 的教训）。**这次把探针写成独立文件**
（`tools/st-oracle/_probe*.mjs`），用完直接 `Remove-Item` 删掉，不去碰要保留的文件。

---

## 5. 硬性规矩（都是踩过坑换来的）

1. **判定机是唯一裁判，测试不是。** 改端口前先让判定机说话；测试若与真引擎冲突，改测试。
   `tests/test_prompt_build.py` 曾经有过几处与 1.19.0 源码打架的期望（已修），别再犯。
2. **别人刚写完、还没提交的文件，动手前先 `git status`。** 我因为忽略这条丢掉过两个文件。
3. **不要用 PowerShell 做文本替换或 `git show > file`**（会写成 UTF-16，文件直接废掉；
   `Set-Content -Encoding UTF8` 也可能带 BOM）。用 `write`/`edit` 工具，或让 Python 写。
4. **每一处"照抄"都要留出处行号注释**（`# world-info.js:5214` 这种），并且**不许凭记忆写**——
   不确定就回去读快照。
5. **不要为了让测试变绿而改端口。** 反过来的错误也是错误。
6. 提交信息写清"改了什么、为什么、验过什么"；判定机数字要如实写（含 not-comparable）。

---

## 6. 关键文档索引

| 文件 | 内容 |
|---|---|
| `research/PORTING-PLAN.md` | 分步计划（S0 判定机 → S1 世界书 → S2 组装 → S3 导入 → S4 provider → S5 外部酒馆后端） |
| `research/08-s2-prompt-brief.md` | S2 的源映射、接口契约、改写理由 |
| `tools/st-oracle/STATUS-S2.md` | **S2 装配判定机的维修记录**（§4 的详细版，含全部行号与 14 条缺陷） |
| `tools/st-oracle/README.md` / `STATUS.md` | 判定机用法与 S1 的不可比点 |
| `research/07-port-map.md` | S1 的函数级移植映射（81 条） |
| `research/06-st-official-docs.md` | 酒馆官方文档要点与未核实清单 |

---

## 7. 还没开始的部分

- ~~**S4 提示词格式适配**~~ —— **已完成**。`tavern/st/prompt_converters.py` 移植了
  `prompt-converters.js` 的全部 21 个导出，`diff_converters.py` 121 个 fixture 全一致。
- ~~**S5 外部酒馆后端加固**~~ —— **基础部分已完成**：`tavern/backends/auth.py` 抽出登录/CSRF
  握手（两个消费者共用），`st_import.py` 提供对运行中酒馆的**只读**读取（角色卡/世界书/聊天），
  `sillytavern.py` 只剩生成。**依然没有做静默回退**——见 §9。
- ~~**管理面板**~~ —— **已完成**：`pages/panel/index.html` + `tavern/panel.py`（7 条只读路由）。
- **群聊**：用户明确说"多群聊先不做"。
- **已知不兼容**：聊天 `.jsonl` 的 `swipes` 字段只存在 `extra` 里；`integrity` 是我们自己的 SHA-256
  （酒馆会拒绝保存我们的文件）；`outlet`/向量化世界书/计时效果时钟（`chat.length`）未实现。
- **README 的截图**：`logo.png` 与 `docs/images/flow.png` 是脚本生成的真实图，但**没有聊天截图**——
  伪造一张不如没有。真截图需要有人跑起来截。

---

## 8. 现在的下一步

```powershell
cd E:\astrbot_plugin
python tools/check.py                        # 基线：ALL CHECKS PASSED（11 步）
git log --oneline -5                         # 看最新提交
```

S0–S4 与 S5 的基础都收口了，判定机四族全绿。剩下的是**产品决定**，不是工程缺口：

- **三份镜像模块没接线**（`wi_keywords` / `wi_scan_state` / `wi_timed`）。它们有测试、是逐字移植，
  但**没有任何生产调用方**，影响不到任何一个回复——生产走的是 `worldbook.ActivationState` 原生实现。
  `python tools/st-oracle/check_module_wiring.py` 会如实报出来。建议在 `KNOWN_UNWIRED` 里记下
  "有意保留为参考实现"，但**删或接都成立**，这是用户该拍的板。
- **外部酒馆不可用时要不要回退到 AstrBot 模型。** 没做，因为静默回退会在对话中途换掉回答的模型
  还不告诉人。要做也得是"明确告知 + 可配置"。

**判定机的规矩不变**：改端口之前先让判定机说话；测试跟真引擎打架就改测试；

---

## 9. 这一轮新增的检查（以及它们各抓到过什么）

四条护栏都接进了 `tools/check.py`。它们存在的理由都是同一句话：**没被验证过的东西等于没有**。

| 检查 | 防的是什么 | 实际抓到过 |
|---|---|---|
| `check_plugin_size.py` | 市场 16 MB 上限、包内必备文件、开发目录误发 | `HANDOFF.md` 一直在往用户机器上发；`README` 引用了不随包分发的 `research/` |
| `check_converter_coverage.py` | 「没写夹具」和「没实现」长得一样 | 改个名字就变红，验证过会咬 |
| `verify_panel.py` | 路由注册了但匹配不上 | `<>` 写成了 `(?P<name>…)` 正则，只会回"未找到该路由" |
| `verify_cmd_params.py` | 指令收不到参数 | **一个真 bug，见下** |
| `check_readme_links.py` | README 指向不存在的文件 | `README_EN.md` 在引入它的那次提交上就是死链 |
| `check_module_wiring.py` | 「有测试」被当成「已完成」 | 报出三份镜像模块没接线（只报告，不拦） |

### 最值得记的一个：所有带参数的 `/tavern` 子指令都收不到参数

AstrBot 从 handler 的**签名**取参数，中间有三个偏移必须同时对上：

1. loader 用 `functools.partial(raw_handler, star_cls)` 绑定实例（`star_manager.py:1273`），
   `inspect` 会因此少掉一个参数；
2. `init_handler_md` 再**无条件跳过前两个**参数；
3. 「贪吃」的尾参数靠 `is GreedyStr` 判定——**和类本身比**，放实例进去会静默退化成「只吃一个词」。

修之前 `handler_params` 是 `{'args': typing.Any}`：AstrBot 看到一个叫 `args` 的参数，而 `Any`
不能被实例化，于是任何带参数的子指令一调就 `TypeError: Any cannot be instantiated`。
也就是说 `/tavern use <卡>`、`/tavern history <n>`、`/tavern worldbook on <书>`、
`worldbook effect …` 在**真 AstrBot 里全都不可达**。

**`tools/astrbot_e2e.py` 没发现，因为它直接调 `on_message`，绕过了指令分发。** 这是真实覆盖缺口，
`tools/verify_cmd_params.py` 就是补它的：复刻 partial 绑定，用 AstrBot 自己的 `CommandFilter`
解析 9 条真实输入。

调试这条时的另一个教训：**别把 handler 放进 `type()` 的类字典里当元数据**。
`functools.partial` 存成类属性后在访问时会被当方法再绑一次，又多吃掉一个参数，于是每次都解析成空。
用 `SimpleNamespace`（对应 AstrBot 的 `StarHandlerMetadata` 数据对象）。这一点写进了
`verify_cmd_params.py` 的文档字符串里。
真引擎中途抛错时，先怀疑 harness，别先怀疑端口 —— §4.3 那 6 条全是这个形状。

**并且**：不要再用 `git checkout -- <file>` 撤探针（§4.1 就是这么丢掉两个文件的）。
探针写成独立文件，用完删那一个文件。
