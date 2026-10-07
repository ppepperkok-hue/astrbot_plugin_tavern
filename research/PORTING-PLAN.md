# 移植路线 · 分步计划（不做面板）

目标：**把酒馆的引擎能力完整搬进插件**（世界书、prompt 组装、聊天记录、导入导出、各模型格式适配），
**不做**它的界面面板；同时**保留**"可选调用外部已部署酒馆"的后端。

工作方式（用户已定）：**镜像式移植**——尽量按酒馆的函数结构与命名翻译成 Python，方便以后对照上游；
但**分步做**，每一步都能单独验收，不一口气铺开。

许可证：AGPL-3.0。凡是直接移植酒馆代码的文件，**文件头必须保留原始版权与许可声明、写明修改日期**，
并在 `THIRD_PARTY_LICENSES.md` 登记。

---

## 进度总览

| 步 | 内容 | 产物 | 状态 |
|---|---|---|---|
| S0 | 判定机（oracle） | `tools/st-oracle/`（Node 跑酒馆原版 + Python 跑我们的实现 + diff） | 进行中 |
| S0b | 源码快照与移植映射 | `research/_raw/st-src/`、`research/07-port-map.md` | 进行中 |
| S1 | 世界书引擎（逐行移植） | `tavern/st/wi_*.py`（buffer / 关键词解析 / decorators / timed effects / 主流程） | 待开始 |
| S2 | Prompt 组装与消息模型 | 按 `openai.js` 的 `ChatCompletion` / `Message` / `TokenHandler` 与组装序移植 | 待开始 |
| S3 | 导入导出 | 角色卡（PNG/JSON/YAML）、`character_book`、`lorebook_v3`、预设、聊天 `.jsonl`（含 swipes） | 待开始 |
| S4 | Provider 格式适配 | 移植 `src/prompt-converters.js`（服务端纯逻辑，1451 行 / 20 个导出） | 待开始 |
| S5 | 外部酒馆后端 | 保留并完善 `tavern/backends/sillytavern.py`（cookie/CSRF、失败回退） | 待开始 |

---

## S1 · 世界书引擎（最值钱的一步）

拆成互不重叠的文件，便于并行：

| 文件 | 移植对象 | 说明 |
|---|---|---|
| `tavern/st/wi_buffer.py` | `WorldInfoBuffer`（约 199–478 行，280 行） | 纯逻辑：`#globalScanData` / `#depthBuffer` / `#recurseBuffer` / `#injectBuffer` / `#skew`；`MATCHER='\x01'`、`JOINER='\n'+MATCHER` 逐字对齐 |
| `tavern/st/wi_keywords.py` | `splitKeywordsAndRegexes`、`parseRegexFromString`、`getRegexedString` 相关 | 键的解析、正则键、整词匹配 |
| `tavern/st/wi_decorators.py` | `parseDecorators` 与 `@@` 装饰器 | depth / role / position / scan_depth / activate_only_after 等 |
| `tavern/st/wi_timed.py` | `WorldInfoTimedEffects` | `sticky` / `cooldown` / `delay` 的**轮次与时间**语义 |
| `tavern/st/wi_engine.py` | `checkWorldInfo`（约 574 行）、`getWorldInfoPrompt`、`convertCharacterBook`、`filterByInclusionGroups` | 主流程与 inclusion group 打分 |

**验收**：`python tools/st-oracle/diff.py --all` 在 S1 覆盖的场景上**零差异**；`port-map.json` 中 S1 相关条目全部 `ported`。

---

## 关键约束（每步都适用）

1. **判定机优先**：没有 oracle 就不动引擎代码——改完必须两边跑、逐条 diff。
2. **版本锁死**：以 `research/_raw/st-src/PROVENANCE.md` 记录的 commit 为准，不追上游更新，只在用户报问题时对比。
3. **不做面板**：任何纯 UI / DOM 的函数在 `port-map.json` 里标 `exempt` 并写明理由。
4. **数据目录**：运行时数据仍只写 `data/plugin_data/<plugin>/`，绝不写插件目录。
5. **发布包**：`research/`、`tests/`、`tools/` 通过 `.gitattributes` 的 `export-ignore` 排除，包体保持在数百 KB 量级。
