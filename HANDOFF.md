# 交接文件 — AstrBot 酒馆插件（astrbot_plugin_tavern）

写于一次会话中断之后。目的：让接手的人**不看聊天记录也能继续**，并且知道哪些事已经证明过、
哪些只是声称。

---

## 0. 一句话现状

**插件本体是可用的**：世界书引擎与真酒馆逐字对齐（判定机 PASS），角色卡/世界书导入已接通（发文件即导入），
提示词组装层已移植完并有 458 项离线测试。**唯一没修好的是 S2 装配层的第三道判定机**
（`diff_assembly.py`），它还红着，6 处差异，且已经确认其中至少一处可能是真端口差异。

---

## 1. 仓库与环境

| 项 | 值 |
|---|---|
| 工作目录 | `E:\astrbot_plugin` |
| 远端 | `https://github.com/ppepperkok-hue/astrbot_plugin_tavern`（公开，`git push` 走 `gh` 凭据助手） |
| 分支 / 最新提交 | `main` / `3c6f3c5`（工作区干净） |
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
python tools/check.py                          # 一把梭：manifest + ruff + pytest + AstrBot 装载 + 真实消息链路
python tools/st-oracle/diff.py --all           # S1：世界书引擎 vs 真酒馆
python tools/st-oracle/diff_prompt.py --all    # S2：消息模型 vs 真酒馆
python tools/st-oracle/diff_assembly.py --all --verbose   # S2：装配顺序 vs 真酒馆（当前 FAIL）
python tools/st-oracle/gen_adapter.py --check  # 判定机生成树是否漂移
```

### 实测结果（写这份文件时刚跑过）

| 命令 | 结果 |
|---|---|
| `python tools/check.py` | **ALL CHECKS PASSED** |
| `python -m pytest tests` | **458 passed, 1 skipped**（唯一 skip 是 `tests/test_prompt.py:151`，缺时区库） |
| `python -m ruff check .` | All checks passed |
| `diff.py --all`（S1） | 14 fixtures，**11 match / 0 diverged / 3 not-comparable** → PASS |
| `diff_prompt.py --all`（S2 消息模型） | 1 fixture，**match** → PASS |
| `diff_assembly.py --all`（S2 装配） | 8 fixtures，**3 match / 5 diverged / 6 divergences** → **FAIL** |

三个 `not-comparable` 是**真的比不了**，不是偷懒：两个是加权随机（酒馆用 `Math.random()`，我们没法复刻
JS 的 RNG 流），一个是 token 预算（酒馆用真 tokenizer，我们只有长度估算）。这三条都写了确定性场景去盯边界。

---

## 3. 已完成什么（以及各自的证据）

### S1 世界书引擎 —— 完成
`tavern/st/worldbook.py` + `wi_buffer.py` + `wi_keywords.py` + `wi_decorators.py` + `wi_timed.py` +
`wi_scan_state.py`，按 `world-info.js` 逐个函数镜像移植。14 个 fixture、11 个可比场景**逐字一致**。

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

### S2 装配层 —— 本体完成，判定机未恢复
`tavern/st/prompt_build.py`（约 1750 行）+ `tests/test_prompt_build.py`（64 项，全绿）。
`populationInjectionPrompts` / `populateChatHistory` / `populateDialogueExamples` /
`populateChatCompletion` / `preparePromptsForChatCompletion` 逐个函数对照移植。

---

## 4. 唯一没修好的东西：S2 装配判定机

### 4.1 事情经过（必须知道，否则会重复踩）

1. 一个工人移植完装配层，并把判定机跑到 **8 fixtures 全 match**——但那版 harness **没有提交**。
2. 我为了回退自己的一个实验，对那两个 harness 文件执行了 `git checkout -- <file>`，
   **把工人未提交的工作一起覆盖掉了**。
3. 我按同样思路重建：`gen_adapter.py` 现在把真的 `Prompt`/`PromptCollection`/`INJECTION_POSITION`
   从快照里按花括号配对抽出来（逐字，加上它们读的 `DEFAULT_DEPTH`/`DEFAULT_ORDER`），
   `getExtensionPrompt*` 也给真值覆盖；`run_assembly.mjs` 不再运行时改写生成树。
4. 重建过程中又修掉 8 个 **harness 侧**缺陷（见 §4.2），差异从"8 个 fixture 全 node-error"
   降到 3 match / 5 diverged / 6 处差异。

### 4.2 已经修掉的 8 个 harness 缺陷（每个都曾冤枉端口）

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

### 4.3 剩下 6 处差异，已逐条看过

| fixture | 现象 | 初判 |
|---|---|---|
| `01-order` | 真引擎少给一部分历史，端口给了 | 疑 harness（参考侧没喂够），**需探针证实** |
| `03-pin-examples` | 真引擎不给 `[Example Chat]` 块，端口给了 | 同上 |
| `07-examples-budget` | 同上 | 同上 |
| `08-continue-nudge` | 上述 + `continueNudge` 那一条 | 同上 |
| `04-continue-prefill` | 端口**无条件**套用 `assistant_prefill`，真引擎对补全来源有条件 | **疑真端口差异**，要核 `openai.js:1318-1331` |

### 4.4 下一步该怎么做（按顺序，别跳）

1. `assembly-04`：读 `openai.js:1318-1331`，确认 `continue_prefill` 是否真的按补全来源门控。
   若真，改 `tavern/st/prompt_build.py`，**这是端口侧该改的一处**。
2. `assembly-03`/`07`：给 `populateDialogueExamples` 的参考侧加探针（照 §4.5 的方法），
   确认是 harness 没喂够还是端口多给。**先证明，再动手。**
3. `assembly-01`/`08`：同上，针对 `populateChatHistory` 的参考侧输入。
4. 全绿后重跑 §2 全部命令，并把 `tools/st-oracle/STATUS-S2.md` 的维修框改成"已恢复"。

### 4.5 探针法（这轮唯一有效的手段）

在 `run_assembly.mjs` 里 monkeypatch 真引擎的方法来记录调用，例如：

```js
const originalAdd = ChatCompletion.prototype.add;
ChatCompletion.prototype.add = function (collection, position) {
    console.log('add', collection?.identifier, position, collection?.collection?.length);
    return originalAdd.call(this, collection, position);
};
```

**记得用完就 `git checkout -- tools/st-oracle/run_assembly.mjs` 撤掉探针**（但注意 §4.1 的教训：
撤之前先确认没有别人未提交的改动）。

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
| `tools/st-oracle/STATUS-S2.md` | **S2 装配判定机的维修状态**（§4 的详细版，含全部行号） |
| `tools/st-oracle/README.md` / `STATUS.md` | 判定机用法与 S1 的不可比点 |
| `research/07-port-map.md` | S1 的函数级移植映射（81 条） |
| `research/06-st-official-docs.md` | 酒馆官方文档要点与未核实清单 |

---

## 7. 还没开始的部分

- **S4 提示词格式适配**：移植服务端纯逻辑 `research/_raw/st-src/prompt-converters.js`（1451 行、20 个导出）。
  这块是**新增**，不影响已完成的任何东西，适合在不碰装配判定机的情况下推进。
- **S5 外部酒馆后端加固**：`tavern/backends/sillytavern.py`（cookie/CSRF、失败回退）。
- **群聊**：用户明确说"多群聊先不做"。
- **已知不兼容**：聊天 `.jsonl` 的 `swipes` 字段只存在 `extra` 里；`integrity` 是我们自己的 SHA-256
  （酒馆会拒绝保存我们的文件）；`outlet`/向量化世界书/计时效果时钟（`chat.length`）未实现。

---

## 8. 建议的第一小时

```powershell
cd E:\astrbot_plugin
python tools/check.py                        # 确认基线是 ALL CHECKS PASSED
python tools/st-oracle/diff_assembly.py --all   # 确认仍是 3 match / 5 diverged / 6 divergences
git log --oneline -3                         # 确认在 3c6f3c5
```

然后**只做一件事**：`assembly-04` 的 `assistant_prefill` 门控。那一处最有可能是真端口差异，
证据最明确（`openai.js:1318-1331`），改动面最小。做完再进入其余五处。

**不要**一上手就去修 harness——先把 §4.4 的第 1 步做完，因为它可能是唯一真正属于端口的差异。
