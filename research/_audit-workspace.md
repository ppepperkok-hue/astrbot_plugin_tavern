# 工作区审计报告（只读侦察）

- 审计者：teammate `workspace-auditor`（Lead: `lead`）
- 时间：2026-10-07 20:15 (+08:00)
- 范围：仓库 `E:\astrbot_plugin`，只读侦察 + 仅写本文件。未修改任何其它文件（未 `git add`/`git commit`/`git checkout`）。
- 纪律：本报告只写「我读过文件或跑过命令」得到的结论；凡是没验证的，明确标注「未验证」。
- 未执行：`python tools/check.py`（Lead 指定由另一位队友负责）。我对它的判断来自**读源码**（`tools/check.py:84`），不是实跑。

---

## 工作区状态

### git status（原样）

```
On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
	modified:   HANDOFF.md
	modified:   research/PORTING-PLAN.md
	modified:   tools/st-oracle/STATUS-S2.md
	modified:   tools/st-oracle/gen_adapter.py
	modified:   tools/st-oracle/run_assembly.mjs

Untracked files:
  (use "git add <file>..." to include in what will be committed)
	.scratch/

no changes added to commit (use "git add" and/or "git commit -a")
```

`git status` 另对 3 个文件抛出警告（Git 侧文本转换）：

```
warning: in the working copy of 'tools/st-oracle/STATUS-S2.md', CRLF will be replaced by LF the next time Git touches it
warning: in the working copy of 'tools/st-oracle/gen_adapter.py', CRLF will be replaced by LF the next time Git touches it
warning: in the working copy of 'tools/st-oracle/run_assembly.mjs', CRLF will be replaced by LF the next time Git touches it
```

### git log --oneline -8（原样）

```
e6836d2 docs: add a handoff file for the S2 assembly-oracle repair
3c6f3c5 ﻿fix(oracle): assembly diff 3 match / 5 diverged, from node-error on all eight
eb11aca ﻿fix(oracle): five harness defects fixed; assembly diff is 1 match / 7 diverged
5d04917 ﻿fix(oracle): guard the missing-prompt path and record the chat-turn finding
433c319 docs(oracle): record the add-call diagnostic for the assembly harness
1540f46 fix(oracle): rebuild the assembly harness without runtime patching
74657e4 feat(st): S2 assembly port and its fixtures; harness repair noted
bc28730 feat(st): S2 assembly gets an oracle, and three reference behaviours it was violating
```

顺带查出：上面前 3 行（`3c6f3c5` / `eb11aca` / `5d04917`）的 commit subject **行首带一个 U+FEFF（BOM）字符**。证据（逐字符码点，第 9 位 = 65279）：

```
3c6f3c5 ... codepoints: 51,99,54,102,51,99,53,32,65279,102,105,120
eb11aca ... codepoints: 101,98,49,49,97,99,97,32,65279,102,105,120
5d04917 ... codepoints: 53,100,48,52,57,49,55,32,65279,102,105,120
e6836d2 ... codepoints: 101,54,56,51,54,100,50,32,100,111,99,115   ← 干净
```

### git diff --stat（原样）

```
 HANDOFF.md                       |  95 +++++++++++++++----------
 research/PORTING-PLAN.md         |   4 +-
 tools/st-oracle/STATUS-S2.md     | 112 ++++++++++++++++++++----------
 tools/st-oracle/gen_adapter.py   |  16 ++++-
 tools/st-oracle/run_assembly.mjs | 146 ++++++++++++++++++++++++++++++++++++---
 5 files changed, 286 insertions(+), 87 deletions(-)
```

### 两个代码文件的 diff：是否自洽

我逐行读了 `git diff -- tools/st-oracle/gen_adapter.py` 与 `git diff -- tools/st-oracle/run_assembly.mjs` 的全文。结论：**改动自洽，且与仓库现状互相印证**。具体核对：

1. **`gen_adapter.py`（+16/-6）** 只做两件事，都在 JS 侧：
   - `oracleSetPrompts` 从 2 个键扩到 4 个键（`newChat` / `newGroupChat` / `newExampleChat` / `continueNudge`），每个键仍然是 `if (typeof X === 'string')` 守卫 —— 所以调用方传 `null` = 「保留引擎默认值」。
   - 新增 `substituteParamsExtended` 的 override（用正则把 `{{key}}` 替换成 `args[key]`，缺键时原样返回）。
   - 我核对了它声称的来源：`research/_raw/st-src/openai.js:108-111` 确实是四个**各不相同的**字面量：
     ```
     108: const default_new_chat_prompt = '[Start a new Chat]';
     109: const default_new_group_chat_prompt = '[Start a new group chat. Group members: {{group}}]';
     110: const default_new_example_chat_prompt = '[Example Chat]';
     111: const default_continue_nudge_prompt = '[Continue your last message without repeating its original content.]';
     ```
     所以「`newExampleChat` 不能继承 `newChat`」这个理由是**有据**的。
   - diff 注释里对 `openai.js:911`（continue nudge 传 `{ lastChatMessage }`）的说法，我只验证了「override 的行为与注释一致」，**没有去读 openai.js:911 那一行**，这一条属于注释自述、未验证。
2. **`run_assembly.mjs`（+146/-24）** 有 5 组改动，互相咬合：
   - 把 4 个横幅字符串改为「读引擎默认值、fixture 可覆盖」，并把实际用到的 3 个写进 `record.prompts`（`newChat` / `newExampleChat` / `continueNudge`）—— 这是给人类/对比用的观测数据，不是判定输入。
   - 新增顶层 fixture 键 → `oai_settings` 透传：`continue_prefill` / `assistant_prefill` / `chat_completion_source`。与 Python 侧 `tools/st-oracle/run_assembly_python.py:137`（`settings.setdefault("continue_prefill", ...)`）对称。
   - `messageExamples` 的块内正文：**从 `mes` 改成读 `content`**（`content ?? mes`）。我读了 fixture 实际形状：`tools/st-oracle/fixtures/prompt/assembly-01-order.json:113,118` 聊天轮次用 `"mes"`，而 `:126,130` 示例条目用 `"content"`；Python 侧 `run_assembly_python.py:151` 把 `fixture["examples"]` 原样交给 `prompt_build`（读 `content`）。所以这个映射是**fixture 形状的正确镜像**。
   - 把 `countFor` 装到 `Message.createAsync` 与 `Message.prototype.setName`；并新增 `OPTIONAL_SYSTEM_PROMPTS`（`impersonate` / `quietPrompt`，空内容）只在 fixture 未声明时才补。
   - 新增 `record.comparable.chat.ok/reason`，且 `completed` 只在 `populateChatCompletion` 正常返回后置 true。
3. **双向闭合检查**：`record.comparable` 不是孤儿键 —— `tools/st-oracle/diff_assembly.py:118-124` 确实读它（`(js.get("comparable") or {}).get("chat")`，`ok is False` 时记 `not comparable`）。这正是 STATUS-S2 §5.7 声称「护栏从死的变活的」的那一处。
4. **生成树无漂移**：我实跑 `python tools/st-oracle/gen_adapter.py --check` → 输出 `adapter is up to date`，退出码 0。即被改的 `gen_adapter.py` 与 `.build/` 里的生成树当前一致，没有「改了生成器忘了重建」的隐患。
5. **「端口一行没改」是真的**：`git status` 里 `tavern/` 与 `tests/` 零改动；改动全部落在 `tools/st-oracle/` 与 3 个 md 上。
6. 一处**无害但不完全对称**的地方：JS 的 `countFor` 只拼 `[role, content, name]`，而 Python 的 `tavern/st/chat_completion.py:70-78` 还会拼 `reasoning` 与 `tool_calls`。装配路径不产出这两样，所以 8/8 结果不受影响；但「逐字照抄端口规则」这句话在这一点上是近似的，值得知道。

### 可疑的临时/探针残留

| 路径 | 判断 | 证据 |
|---|---|---|
| `.scratch/`（整个目录，70 个文件、76.9 MB） | **未跟踪且未被 .gitignore 忽略** ← 最严重 | `git status` 把它列为 Untracked；`git check-ignore -v .scratch/inventory.py` **无输出**（未被忽略）。内含 `session.zip`(12,350,035 B)、`zip/session.v4.jsonl`(18,765,686 B)、17 份 `zip/subagents/*/session.v4.jsonl`，以及 13 个脚本：`check1.py chk_err.py extract.py extract2.py extract3.py extract4.py extract5.py extract_session.py inventory.py mine_session.py probe1.py probe2.py realuser.py` |
| `tools/st-oracle/out/t3.js.json`、`t3.py.json` | 旧的即席探针输出（含 `.tmp` 之外的手写 `t3` 命名） | 修改时间 2026-10-07 19:07:32，其余 out 文件在我这轮运行后被刷成 20:11；`t3.*` 没被刷新 → 无人读取的残留 |
| `tools/st-oracle/out/assembly-01-js.json`、`assembly-02-js.json`、`assembly-02-python.json`、`prompt-01-js.json`、`prompt-02-assembly-order.js.json`、`prompt-02-assembly-order.python.json` | 旧命名规范的残留输出（当前命名是 `assembly-01-order.js.json` 这种） | mtime 18:36–19:45，本轮未被重写 |
| `research/_raw/st-src/_fetch_*.py`（4 个）、`_check_deliverable.py`、`_check_js_refs.py`、`_verify.py`、`_gen_functions_md.py`、`_release_delta.json`、`_repo_meta.json` | 快照获取/校验脚本，放在快照目录里 | 目录被 `research/_raw/` 规则忽略，**永远不会进 git**；不影响 CI，但也不可复现 |
| `tools/st-oracle/.build/`、`tools/st-oracle/out/` | 生成物，**已正确忽略** | `git check-ignore -v` 命中 `.gitignore:38` / `.gitignore:39` |

补一句：`.gitignore` 里有两条重复的 `tools/st-oracle/.build/` + `tools/st-oracle/out/`（第 38-39 行与第 46-47 行）。无害，但说明该文件被追加过一段复制粘贴。

---

## 验证基线

5 条命令我**逐条串行**执行（同一 shell 会话，前一条结束才起下一条），输出落盘到 `%TEMP%\stverify\*.txt`，下面是**实测的原样收尾行**与退出码/耗时。

### 1. `python tools/st-oracle/diff.py --all` — 退出 0，3.7 s

```
fixtures: 14 | match: 11 | diverged: 0 | not-comparable: 3 | skipped: 0 | divergences: 0
VERDICT: PASS
```

（最后 3 行逐条：`12-budget-overflow not-comparable 0`、`13-inclusion-group-roll not-comparable 0`、`14-probability-roll not-comparable 0`。）

### 2. `python tools/st-oracle/diff_prompt.py --all` — 退出 0，0.4 s

```
fixture           result         divergences
01-message-model  match          0

fixtures: 1 | match: 1 | diverged: 0 | not-comparable: 0 | divergences: 0
VERDICT: PASS
```

### 3. `python tools/st-oracle/diff_assembly.py --all` — 退出 0，3.1 s

```
fixture                       result           divergences
assembly-01-order             match            0
assembly-02-injection-depths  match            0
assembly-03-pin-examples      match            0
assembly-04-continue-prefill  match            0
assembly-05-disabled-prompts  match            0
assembly-06-history-names     match            0
assembly-07-examples-budget   match            0
assembly-08-continue-nudge    match            0

fixtures: 8 | match: 8 | diverged: 0 | not-comparable: 0 | divergences: 0
VERDICT: PASS
```

### 4. `python -m pytest tests -q` — 退出 0，9.2 s，**但没有汇总行**

原样收尾（文件最后 7 行，`Get-Content -Raw` 也确认 100% 之后没有任何字符）：

```
........................................................................ [ 15%]
........................................................................ [ 31%]
.................s...................................................... [ 47%]
........................................................................ [ 62%]
........................................................................ [ 78%]
........................................................................ [ 94%]
...........................                                              [100%]
```

原因我查了：`pyproject.toml:22` 已经有 `addopts = "-q"`，命令行再给一个 `-q` = **双 `-q`**，pytest 把最后的汇总统计行也压掉了。所以「这条命令的最后一行」拿到的是进度点，不是数字。

为拿到可引用的数字，我补跑了一条（**这不是 Lead 指定的那条命令**）：

```
python -m pytest tests -o addopts= -q
→ 458 passed, 1 skipped in 3.82s      (退出 0)
```

即 `458 passed, 1 skipped` 与 HANDOFF.md:53 的声称一致；跳过 1 项，HANDOFF 说是 `tests/test_prompt.py:151`（缺时区库）—— 该 skip 的具体位置**我未单独验证**（pytest 加 `-q` 没打印 skip 列表）。

### 5. `python -m ruff check .` — **退出 1，0.2 s，FAIL**

```
Found 22 errors.
[*] 15 fixable with the `--fix` option (2 hidden fixes can be enabled with the `--unsafe-fixes` option).
```

22 条**全部**落在 `.scratch\`（`extract.py`、`extract_session.py`、`inventory.py`、`probe1.py`、`probe2.py`、`realuser.py`），`tavern/`、`tests/`、`tools/`、`main.py` **一条都没有**。也就是说 lint 本身是干净的，失败完全由未忽略的 `.scratch/` 目录造成。

> 与文档冲突（三处）：HANDOFF.md:54 写「`python -m ruff check .` → All checks passed」，STATUS-S2.md:101 写「clean / formatted」，而实测是 22 个错误。并且 `tools/check.py:84` 用的是同一条命令 `ruff check .`，所以 HANDOFF.md:52 声称的「`tools/check.py` → ALL CHECKS PASSED」**在当前工作区状态下不可复现**（该结论来自读源码，不是实跑 check.py）。

补充：`pyproject.toml:28-35` 的 `[tool.ruff] exclude` 列了 `.tmp-test-data` / `.tools` / `.uv-cache` / `.acl-recovery` / `tools/st-oracle/.build` / `data`，**唯独没有 `.scratch`**——这就是它被扫到的直接原因。

---

## 项目结构

### 顶层

```
E:\astrbot_plugin\
├─ main.py                 32 行，插件入口（薄壳）
├─ metadata.yaml           AstrBot 插件元信息
├─ _conf_schema.json       配置面板 schema
├─ pyproject.toml          pytest addopts=-q；ruff select=E,F,W,I,UP,B ignore=E501
├─ README.md / HANDOFF.md / LICENSE(AGPL-3.0) / THIRD_PARTY_LICENSES.md
├─ tavern/                 插件本体（无外部依赖，标准库 + 可选 httpx/pillow/yaml）
├─ tools/                  check.py + st-oracle/（判定机）
├─ tests/                  13 个 test_*.py + fixtures/
├─ research/               调研文档 + PORTING-PLAN.md + _raw/（原始快照，gitignored）
├─ data/                  运行时数据（gitignored）
└─ .tools/ .uv-cache/ .scratch/ .pytest_cache/ .ruff_cache/ .tmp-test-data/ .acl-recovery/
```

### 用户可见入口

- `main.py`（文档字符串原文：*"AstrBot plugin entry point: tavern style role play."*）把插件目录塞进 `sys.path`，然后 `from tavern.main import TavernPlugin` —— 所以真正注册的是 `tavern/main.py` 里的类。
- `tavern/main.py`（文档字符串：*"AstrBot entry point for the tavern plugin (SillyTavern style role play). The module is deliberately thin: decorators, message components, permissions and platform quirks live here, while :mod:`tavern.core` owns every decision."*）
  - `@register(PLUGIN_NAME, ...)` 的 `class TavernPlugin(Star)`（`tavern/main.py:95-96`）；
  - 消息入口 `on_message` / `handle_message`（`tavern/main.py:203/212`，接 `@filter.event_message_type(ALL)`）；
  - **指令面**：`filter.command_group("tavern", alias={"酒馆"})`（`tavern/main.py:463`），子指令清单直接引自 `HELP_TEXT`（`tavern/main.py:423-439`）：`help / status / list / use <名字> / card / new / history [条数] / worldbook list|on|off <名字> / reload / import / preview [文字]`。
  - 文件导入路径：`tavern/main.py:314` 调 `plugin.core.import_uploaded_file(name, payload)`，实现在 `tavern/core.py:344`（用户把卡/世界书直接发给机器人即可导入）。
  - 单轮装配入口：`tavern/core.py:542 build_turn(...)`；出话渲染：`tavern/core.py:876 render_answer(...)`。

### `tavern/` 各子包（引号内为 docstring 原文节选）

- `tavern/core.py` — *"Framework independent brain of the tavern plugin. ``main.py`` only does AstrBot plumbing … every decision lives here so it can be unit tested without an AstrBot installation."* 职责：library / binding / history / assembly / rendering；**不 import astrbot**。
- `tavern/main.py` — AstrBot 薄壳（见上）。
- `tavern/config.py` — *"Configuration access helpers … tolerates missing keys and wrong types … picks the right data directory through ``StarTools.get_data_dir``"*；定义 `CARDS_DIR/WORLDBOOKS_DIR/PRESETS_DIR/CHATS_DIR/STATE_FILE/INDEX_FILE`。
- `tavern/backends/base.py` — *"Generation backends: one interface, two implementations. The plugin never talks to a model directly."* 纯接口 + 数据类，无 astrbot 依赖。
- `tavern/backends/astrbot_provider.py` — 默认后端，包 `Context.llm_generate`。
- `tavern/backends/sillytavern.py` — *"Optional backend that proxies generation to an already deployed SillyTavern."* 打 `POST /api/backends/chat-completions/generate`，需要 cookie + `x-csrf-token`；`httpx` 延迟导入。**S5 的对象**。
- `tavern/st/cards.py` — *"SillyTavern character card parsing utilities … reading … from ``.json`` files and from PNG ``tEXt``/``iTXt`` metadata chunks, and normalising V1 / V2 / V3 cards into one dataclass."*
- `tavern/st/worldbook.py` — *"SillyTavern compatible World Info (lorebook) engine."* 解析 + 关键词扫描 + selective keys + scan depth + probability + 递归；S1 主流程。
- `tavern/st/wi_buffer.py` / `wi_timed.py` / `wi_keywords.py` / `wi_decorators.py` / `wi_scan_state.py` — S1 的逐函数镜像件：`WorldInfoBuffer`（world-info.js:199-478）、`WorldInfoTimedEffects`（479-794，sticky/cooldown/delay）、键解析与正则（`splitKeywordsAndRegexes` 等）、`@@` 装饰器（`parseDecorators` 4652-4698）、`checkWorldInfo` 的扫描状态机（43-60 等）。
- `tavern/st/prompt.py` — *"SillyTavern compatible prompt assembly and history trimming."* 偏「格式/预设」侧：`DEFAULT_PROMPT_ORDER`、`render_macro`、`build_messages`、`trim_history`、`count_tokens`。
- `tavern/st/prompt_build.py` — *"SillyTavern prompt **assembly** layer, mirrored function by function."* 源 `openai.js` 1.19.0；1775 行；S2 装配主体。
- `tavern/st/chat_completion.py` — *"SillyTavern's prompt message model, mirror-ported from ``public/scripts/openai.js``"*：`TokenHandler` / `Message` / `MessageCollection` / `ChatCompletion`。
- `tavern/st/chat_store.py` — *"SillyTavern compatible chat (JSONL) storage."* 目录布局 `<root>/chats/<character_name>/<file>.jsonl`，首行 header。
- `tavern/st/importers.py`（2038 行）/ `exporters.py` — 导入/导出侧，共享 `FIELD_MAPPING`；文件头带 AGPL 版权与 upstream commit。**S3**。
- `tavern/st/__init__.py` — ⚠️ 它的 docstring 只列了 `cards` / `worldbook` / `prompt` 三个模块，**没有提到** `prompt_build` / `chat_completion` / `chat_store` / `importers` / `exporters` / `wi_*`。属于陈旧文档（不是错误，但新人会看漏）。

`tools/`：`tools/check.py`（一把梭：manifest + ruff + pytest + AstrBot 装载 + 真实消息链路；`tools/check.py:84` 跑 `ruff check .`，`:102` 跑 `pytest tests -q`）、`tools/st-oracle/`（判定机，见下节）。

---

## S0-S5 进度

来源：`research/PORTING-PLAN.md`（**工作区版本，未提交**）的「进度总览」表 + `tools/st-oracle/STATUS-S2.md`。

### 状态表行（逐字引用，`research/PORTING-PLAN.md:18-24`）

```
| S0 | 判定机（oracle） | `tools/st-oracle/`（Node 跑酒馆原版 + Python 跑我们的实现 + diff），15 个 fixture | ✅ 已立：S1 14 条（11 一致 / 0 差异 / 3 不可比）、S2 消息模型 1 条、S2 装配 8 条（全一致） |
| S0b | 源码快照与移植映射 | `research/_raw/st-src/`（13 份，sha 校验）、`research/07-port-map.md`（81 条） | ✅ 已立（26 ported / 24 pending / 31 exempt） |
| S1 | 世界书引擎（逐行移植） | `tavern/st/wi_buffer.py`、`wi_timed.py`、`wi_scan_state.py`、`wi_keywords.py`、`wi_decorators.py` + `worldbook.py` 主流程 | ✅ **完成**：判定机 14 个 fixture，11 一致 / 0 差异 / 3 不可比 |
| S2 | Prompt 组装与消息模型 | 按 `openai.js` 的 `ChatCompletion` / `Message` / `TokenHandler` 与组装序移植 | ✅ **完成**：消息模型 1 条一致；装配 8 个 fixture **全一致**（`prompt_build.py` 未改动，差异全在判定机侧） |
| S3 | 导入导出 | `tavern/st/importers.py`、`exporters.py`（PNG/JSON/YAML 卡、`character_book`、`lorebook_v3`/V2/AgnAI/Risu/Novel、聊天 `.jsonl`） | ✅ 已落地并接进 `PluginCore.import_uploaded_file`（用户把文件发给机器人即可导入） |
| S4 | Provider 格式适配 | 移植 `src/prompt-converters.js`（服务端纯逻辑，1451 行 / 20 个导出） | 待开始 |
| S5 | 外部酒馆后端 | 保留并完善 `tavern/backends/sillytavern.py`（cookie/CSRF、失败回退） | 待开始 |
```

同一文件另有一张「S1 已完成的对齐」表（`:28-34`），以及 `PORTING-PLAN.md:50` 的验收条件：「**验收**：`python tools/st-oracle/diff.py --all` 在 S1 覆盖的场景上**零差异**；`port-map.json` 中 S1 相关条目全部 `ported`。」（注意：S1 的实测是 11 match + **3 不可比**，并非字面上的「零差异」；文档自己在 S0/S1 行写明了这 3 条不可比。）

### 我实测能背书的 DONE / NOT STARTED

- **DONE（实测）**：S0 判定机（三族命令全 PASS）、S1（`diff.py --all` → `14 | 11 | 0 | 3`，PASS）、S2 消息模型（`diff_prompt.py --all` → `1 | 1 | 0 | 0`，PASS）、S2 装配（`diff_assembly.py --all` → `8 | 8 | 0 | 0`，PASS）。S3 我只做了**存在性**核对（`tavern/st/importers.py`、`exporters.py` 存在，`tavern/core.py:344 import_uploaded_file` 存在，`tavern/main.py:314` 调它）；**没有实跑 S3 的功能验收**。
- **NOT STARTED（实测）**：S4、S5。证据：`grep -rn` 全仓（`*.py,*.md,*.json,*.mjs`）对 `prompt-converters|convertClaudeMessages|mergeMessages|PROMPT_PROCESSING_TYPE` 的命中，除了 3 个 md（HANDOFF、PORTING-PLAN、`research/05-st-embedding-options.md`、`03-sillytavern-formats-and-api.md`）以外，**没有任何 `.py`**；`tavern/` 内部零命中。S5 对应的 `tavern/backends/sillytavern.py` 已存在但按 PORTING-PLAN 仍是「待开始」的加固项。

### STATUS-S2.md 的核心声称（与我的实测对照）

它开头写（`STATUS-S2.md:9-11`）：

```
> **Current numbers** (`python tools/st-oracle/diff_assembly.py --all`):
> **8 fixtures, 8 match, 0 diverged, 0 not-comparable, 0 divergences** — from
> `node-error` on all eight, via 3/5/6 and 4/4/5.
```

以及 `STATUS-S2.md:96-103` 的验证表（8 match；`diff_prompt` PASS；`458 passed, 1 skipped`；ruff clean；`gen_adapter.py --check` adapter up to date；`check.py` ALL CHECKS PASSED）。

- 我实测：8/8 PASS ✅、diff_prompt PASS ✅、458 passed/1 skipped ✅（需绕开双 `-q`）、`gen_adapter.py --check` → `adapter is up to date` ✅。
- **ruff clean ❌**（实测 22 errors，全在 `.scratch/`）；连带 `check.py` 的 ALL CHECKS PASSED 在当前状态不可复现（读源码得，未实跑）。
- STATUS-S2 §5「Still open」第 7 条声称 `comparable.chat.ok` 已被 `diff_assembly.py` 使用；我对 `diff_assembly.py:118-124` 的阅读确认属实，且 `run_assembly.mjs` 本次 diff 首次写入它 —— 两边闭合。

---

## S4 待移植清单

### 目标文件（实测的读后结果，不是猜的）

- 路径：`research/_raw/st-src/prompt-converters.js`
- 大小 **55,910 字节**；**1,451 行**（`Get-Content ... ).Count`）
- 头部两个 import（`:1-2`）：`import crypto from 'node:crypto'`、`import { getConfigValue, tryParse } from './util.js'`
- 模块级副作用常量（读文件头得到）：`:4` `PROMPT_PLACEHOLDER = getConfigValue('promptPlaceholder', 'Let\'s get started.')`、`:34` `enableThoughtSignatures = !!getConfigValue('gemini.thoughtSignatures', true, 'boolean')`。**这两处意味着 S4 不能只搬一个文件**：需要 `util.js` 的 `getConfigValue`/`tryParse` 以及配置来源的桩。
- 导出（`grep '^export'` 实测，21 条）：

  | 行 | 导出 | 类型 |
  |---|---|---|
  | 15 | `PROMPT_PROCESSING_TYPE` | 常量对象（10 个成员：`NONE/CLAUDE/MERGE/MERGE_TOOLS/SEMI/SEMI_TOOLS/STRICT/STRICT_TOOLS/SINGLE`） |
  | 49 | `getPromptNames` | function |
  | 67 | `addAssistantPrefix` | function |
  | 85 | `postProcessPrompt` | function |
  | 120 | `convertClaudePrompt` | function |
  | 197 | `convertClaudeMessages` | function |
  | 384 | `convertCohereMessages` | function |
  | 432 | `convertGooglePrompt` | function |
  | 631 | `convertAI21Messages` | function |
  | 703 | `convertMistralMessages` | function |
  | 785 | `convertXAIMessages` | function |
  | 827 | `mergeMessages` | function |
  | 961 | `convertTextCompletionPrompt` | function |
  | 985 | `cachingAtDepthForClaude` | function |
  | 1020 | `cachingAtDepthForOpenRouterClaude` | function |
  | 1072 | `cachingSystemPromptForOpenRouter` | function |
  | 1124 | `calculateClaudeBudgetTokens` | function |
  | 1182 | `calculateGoogleBudgetTokens` | function |
  | 1336 | `embedOpenRouterMedia` | function |
  | 1377 | `addReasoningContentToToolCalls` | function |
  | 1397 | `addOpenRouterSignatures` | function |

  即：**20 个导出函数 + 1 个导出常量**。文档里 `PORTING-PLAN.md:23` 与 `HANDOFF.md:197` 写「20 个导出」，`research/05-st-embedding-options.md:382` 写「导出 **20 个函数** + `PROMPT_PROCESSING_TYPE` 常量」—— 后者是精确表述，前者是概略说法。

### `tavern/` 里有没有 already 引用 prompt 转换？

**没有。** `grep -rn '(?i)prompt[-_ ]?converter|convertPrompt|prompt_convert' tavern/` → 0 命中。整个 `tavern/` 里与 provider 相关的只有两处**顺带**提到，都不是 S4 的实现：

- `tavern/st/prompt_build.py:1194-1195`：`# chat_completion_sources.CLAUDE === 'claude'` / `supports_assistant_prefill = _setting(settings, "chat_completion_source") == "claude"`
- `tavern/backends/sillytavern.py:208`：`if isinstance(content, list):  # Anthropic style content blocks`

结论：S4 是**纯新增**，符合 HANDOFF §7 的说法。

### 判定机里能复用的基础设施

`tools/st-oracle/` 目前的文件（全部实测存在）：

**共享（与「哪一步」无关，S4 直接用）**

| 文件 | 作用 |
|---|---|
| `gen_adapter.py`（20,538 B） | 生成 `.build/` Node 影子树。**关键限制**：`SRC = REPO/"research"/"_raw"/"st-src"/"world-info.js"`（`:29`）、`ENGINE_REL = "public/scripts/world-info.js"`（`:34`）—— 引擎文件是**硬编码**的；`PromptManager.js` 是它按花括号配对抽出来的特例（`:318`、`:380-415`）。我列了 `.build/` 的实际内容（33 个文件），**里面没有 `src/prompt-converters.js`，也没有 `src/util.js`**。S4 必须扩展这个生成器。 |
| `runtime.mjs`（2,762 B） | Proxy 桩、种子 PRNG、共享 token 计数器。 |
| `port-map.json`（19,270 B）+ `gen_port_map.py` | 移植进度单一事实源。`grep prompt-converters port-map.json` → **0 命中**，即 S4 在映射表里还没登记。 |
| `gen_fixtures.py`（32,493 B） | 重新生成 fixture 的工厂。 |
| `diff.py` / `diff_prompt.py` / `diff_assembly.py` | 三族 diff 驱动（各自 `--all`）。 |
| `fixtures/` + `out/` | 输入/输出；`out/` 的产物名规则是 `<fixture>.json` / `<fixture>.python.json`。 |
| `README.md` / `STATUS.md` / `STATUS-S2.md` | 用法与状态。 |

**按步骤分（S4 要照着再加一套）**

| 步骤 | Node 跑 | Python 跑 | fixture | diff |
|---|---|---|---|---|
| S1 世界书 | `run.mjs` (13,321 B) | `run_python.py` (14,249 B) | `fixtures/*.json`（14 个） | `diff.py` |
| S2 消息模型 | `run_prompt.mjs` (9,275 B) | `run_prompt_python.py` (6,863 B) | `fixtures/prompt/01-message-model.json` | `diff_prompt.py` |
| S2 装配 | `run_assembly.mjs` (20,745 B) | `run_assembly_python.py` (9,101 B) | `fixtures/prompt/assembly-0{1..8}-*.json`（8 个） | `diff_assembly.py` |
| **S4（缺）** | 需新增 `run_converters.mjs` | 需新增 `run_converters_python.py` | 建议 `fixtures/converters/*.json` | 需新增 `diff_converters.py` |

注：fixture 总数为 **23 个 JSON**（`fixtures/*.json` 14 + `fixtures/prompt/*.json` 9），与 PORTING-PLAN S0 行写的「15 个 fixture」不符（见下节）。

---

## 异常与风险

按严重程度排序。

### R1（高）`.scratch/`：76.9 MB 未忽略的临时目录，直接把 lint/基线打红

- 事实：`git status` 报 `Untracked: .scratch/`；`git check-ignore -v .scratch/inventory.py` 无输出 → **不在忽略名单**；实测 70 个文件、80,602,052 B（76.9 MB），其中 `session.zip` 12.35 MB、`session.v4.jsonl` 18.77 MB、`zip/subagents/` 下 17 份会话 jsonl 合计约 45 MB，外加 13 个临时脚本（`probe1.py`、`probe2.py`、`extract*.py`、`realuser.py`…）。
- 后果一：`python -m ruff check .` → `Found 22 errors.`（全部来自 `.scratch/`）。`tools/check.py:84` 跑的就是这条命令，所以 HANDOFF.md:52 记的 `ALL CHECKS PASSED` 基线**当前不成立**（此结论由读 `tools/check.py` 得出，我没有实跑 check.py）。
- 后果二：任何人执行 `git add .` 会把约 77 MB 会话日志写进公开仓库。
- 建议（不实施，交 Lead 定）：把 `.scratch/` 加进 `.gitignore`（或直接删目录）；若要保留，至少让 `[tool.ruff] exclude` 覆盖它。

### R2（高）文档与实测的三处硬冲突

1. **ruff**：HANDOFF.md:54「`python -m ruff check .` → All checks passed」、STATUS-S2.md:101「clean / formatted」↔ 实测 `Found 22 errors.`（退出 1）。
2. **fixture 数量**：`research/PORTING-PLAN.md:18` 的 S0 行说「15 个 fixture」，但同一行的括号里列的是「S1 14 条 + 消息模型 1 条 + 装配 8 条」= 23；磁盘实测也是 **23 个 fixture JSON**。这一行是本次未提交改动新写的，**自相矛盾**。
3. **`tools/st-oracle/README.md` 整体过期**：`:36-37` 写「Currently it exits 1 on purpose: eight of the twelve fixtures still diverge」（实测 14 fixtures / 0 diverged / PASS）；`:43` 写「24 stub modules」，`.build/` 实际是 33 个文件。同一仓库里 `tools/st-oracle/STATUS.md:41` 也还停在「`12 fixtures | match 3 | diverged 8 | not-comparable 1 | 24 diverging fields`」（该文件本次未被修改），而 HANDOFF.md:189 又把 STATUS.md 指为 S1 的不可比点权威文档 —— 新人照它读会得到完全错误的现状。

### R3（中）存档性残留物：st-oracle/out 的旧命名 / 探针产物 + commit message 里的 BOM

- `tools/st-oracle/out/` 里 8 个文件属于旧命名或即席探针：`t3.js.json`、`t3.py.json`（19:07）、`assembly-01-js.json`（19:45）、`assembly-02-js.json`、`assembly-02-python.json`、`prompt-01-js.json`、`prompt-02-assembly-order.js.json`、`prompt-02-assembly-order.python.json`（18:36–18:45）。本轮 `diff_*` 跑完后当前命名的 46 个文件都被刷新到 20:11，这 8 个没被刷新 → 确认无人读取。因为 `out/` 已被 gitignore，风险仅限于「有人 grep out/ 得出错误结论」。
- `research/_raw/st-src/` 里混放了 10 个下划线前缀的抓取/校验脚本与 2 个 json；目录被忽略所以永不进 git，代价是**不可复现**（换台机器只能重新抓）。另外 PORTING-PLAN 说该目录「13 份」，我实测非下划线前缀的文件是 **16 份**（12 个 `.js` + `FUNCTIONS.md` + `manifest.json` + `package.json` + `PROVENANCE.md`）—— 「13 份」的具体口径我**没有去 PROVENANCE.md 里核实**，在此仅记为待核。
- 三个 commit 的 message 开头嵌了 U+FEFF（见「工作区状态」）。不影响功能与 `git` 解析，但会在 `git log` 里显示成怪字符；后续 commit 别再用 PowerShell 管道/重定向写 message（HANDOFF §5.3 已有同类教训）。

### R4（中）行尾：`core.autocrlf=true` 与 `.gitattributes eol=lf` 打架

- 配置：`git config core.autocrlf` → `true`；`.gitattributes:2-9` 又要求 `* text=auto eol=lf`（含 `*.py`、`*.md` 显式 `eol=lf`）。
- 5 个被改文件的实测字节统计（无 BOM）：

  | 文件 | 字节 | BOM | CRLF | 裸 LF |
  |---|---|---|---|---|
  | `HANDOFF.md` | 14,941 | 否 | 0 | 224 |
  | `research/PORTING-PLAN.md` | 4,708 | 否 | 0 | 60 |
  | `tools/st-oracle/STATUS-S2.md` | 13,587 | 否 | 216 | 0 |
  | `tools/st-oracle/gen_adapter.py` | 20,538 | 否 | 482 | 0 |
  | `tools/st-oracle/run_assembly.mjs` | 20,745 | 否 | 432 | 0 |

  → **5 个里 3 个是纯 CRLF、2 个是纯 LF，同一批改动内部就不一致**。commit 时会被规范化成 LF（我核对了 `HEAD:` 的 blob：5 个文件的 CR 计数都是 **0**），所以**不会污染仓库历史**；代价是 `git diff` 会对那 3 个文件刷「CRLF will be replaced by LF」警告，且未来若有人关掉 `autocrlf` 或做字节级比对会踩坑。没有 BOM，这点是干净的。
- 附带：`.gitignore:38-39` 与 `:46-47` 是同一对规则重复出现，属复制粘贴残留。

### R5（低）S4 的基础设施缺口（不是 bug，是工作量）

- `.build/` 生成树里没有 `src/prompt-converters.js` 与 `src/util.js`；`gen_adapter.py` 的引擎源是硬编码常量（`:29`/`:34`）。S4 的第一步必然是**扩展生成器**（多引擎文件 + `util.js` 的 `getConfigValue`/`tryParse` 桩），否则连 fixture 都跑不起来。
- `port-map.json` 里 prompt-converters 相关条目为 0，S4 落地后要同步 `gen_port_map.py` 的探针口径，否则「单一事实源」会把这块静默略过。

### R6（低）pytest 汇总行被双 `-q` 吃掉

`pyproject.toml:22` 已有 `addopts = "-q"`，再手写 `-q` 会让 `python -m pytest tests -q` **不打印** `N passed, M skipped` 汇总行（实测输出止于 `[100%]` 那行）。要引用数字用 `python -m pytest tests -o addopts= -q`（实测 `458 passed, 1 skipped in 3.82s`）。这不是缺陷，但会让「以最后一行作为基线」的做法拿到错东西 —— 建议 HANDOFF 与 STATUS-S2 的复现命令改成后者。

---

### 附：我**没有**验证的事项（明确列出，避免误读）

1. `python tools/check.py` 未实跑（Lead 指定他人负责）；相关结论均来自读 `tools/check.py:84`。
2. `research/_raw/st-src/PROVENANCE.md` 里「13 份」的准确含义与 sha 校验未做。
3. `research/07-port-map.md` 的「81 条」与 `port-map.json` 的 26/24/31 计数未逐条核对，只做了文件存在性与「无 prompt-converters 条目」的 grep。
4. S3 的功能验收未跑，只核对了源码路径与调用点存在。
5. `diff` 注释里引用的若干上游行号（如 `openai.js:911`、`PromptManager.js:294-297`）只核对了 `openai.js:108-111` 一处，其余未逐行回读快照。
6. `tests/test_prompt.py:151` 那个 skip 的具体位置未单独确认。
