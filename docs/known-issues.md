# 已知问题与踩过的坑

这份文件的存在理由是：**修好的 bug 不该只活在提交历史里**。

提交信息只有 `git log` 翻到底才会看到，而"导致这个 bug 的思维方式"通常比 bug 本身更容易复发。
所以每个条目都写三件事：现象、根因、**为什么当初没被发现**——最后一点才是可复用的。

`tools/check_known_issues.py` 会检查这份文件和代码里的 `KNOWN-ISSUE:` 标记是否对得上，
见文末。

---

## 1. 插件实例解析成旧实例（`PLUGIN_INSTANCES[0]`）—— 已修

**标记它的是什么：** 写一个测试时，断言 `plugin._last_fallback` 已经正确赋值，但
`/tavern status` 的输出里没有它，而且 `data_dir` 显示的是**另一个用例的临时目录**。

**现象：** `/tavern status`、以及每一条聊天消息和每一条指令，都在用一个已经被关闭的插件实例。

**根因：** `_current_plugin()` 返回 `PLUGIN_INSTANCES[0]`，即列表里的第一个实例。

AstrBot 每次重载会**构造一个新的 `TavernPlugin`**，而 `tavern/main.py` 模块**不会被重新导入**，
所以 `PLUGIN_INSTANCES` 是一部实例历史，**只有最后一个活着**。`[0]` 于是永远指向第一个；
重载之后它已经被 `terminate()` 关掉——后端 `close` 了、库状态过期了——却继续接管一切。

**修法**（提交 `f157fea`）：`__init__` 先 `clear()` 再 `append`，列表最多一条；`terminate()`
移除自己（覆盖重载时 terminate 与 initialize 之间那个窗口）；`_current_plugin()` 改读 `[-1]`。

**为什么这么久没被发现——这一条最值得记住：**

这个 bug **只在多个实例共存时才显形**。而 `tools/astrbot_e2e.py` 全程只用一个实例；
单元测试则被 fixture 隔离开，也看不到。真正把它逼出来的是 `tavern` fixture 是
`scope="module"`，于是同一个测试文件里连续构造了多个实例。

也就是说，**"测试全绿"在这里是假证据：测试的隔离性恰好掩盖了产品的共享状态问题**。

**推广：** 凡模块级可变注册表都该专门写"两个实例共存"的用例。本仓库里已知的有两个：
`tavern.main.PLUGIN_INSTANCES`（本条目）与 AstrBot 的 `Context.registered_web_apis`
（类属性、全仓无 clear/remove，所以路由注册必须放在 `initialize()` 且不能重复注册）。

**守它的测试：** `tests/test_plugin_integration.py::test_the_newest_instance_wins_after_a_reload`、
`::test_a_handler_never_reaches_a_closed_instance`。

---

## 2. 所有带参数的 `/tavern` 子指令都收不到参数 —— 已修

**现象：** `/tavern use <卡>`、`/tavern history <n>`、`/tavern worldbook on <书>`、
`worldbook effect ...` 一调就抛 `TypeError: Any cannot be instantiated`。

**根因：** AstrBot 从 handler 的**签名**取参数，而 `tavern/main.py` 的 `sub()` 包装器把真签名
遮住了。三个偏移必须同时对上：

1. loader 用 `functools.partial(raw_handler, star_cls)` 绑定实例（`star_manager.py:1273`），
   `inspect` 因此少掉一个参数；
2. `init_handler_md` 再**无条件跳过前两个**参数；
3. "贪吃"的尾参数靠 `is GreedyStr` 判定——**和类本身比**，放实例进去会静默退化成"只吃一个词"。

**修法**（提交 `3cf2788`）：给 shim 设 `__signature__`，在 `event` **之后**插一个带默认值的
padding 参数。位置没有选择余地：插在 `plugin` 之前不合法（`Signature.replace` 拒绝"有默认值参数
后面跟无默认值参数"），插在真参数之后无效（AstrBot 填满自己的参数就停了）。

**为什么这么久没被发现：** `tools/astrbot_e2e.py` **直接调 `on_message`，绕过了指令分发**。
它跑一万遍也看不到。这是真实的覆盖缺口。

**守它的检查：** `tools/verify_cmd_params.py`——复刻 partial 绑定，用 AstrBot 自己的
`CommandFilter` 解析 9 条真实输入。

**调试时的另一个陷阱：** 别把 handler 放进 `type()` 的类字典当元数据。`functools.partial`
存成类属性后，访问时会被当方法**再绑一次**，又吃掉一个参数，于是每次都解析成空。用
`SimpleNamespace`（对应 AstrBot 的 `StarHandlerMetadata` 数据对象）。

---

## 3. 整词匹配下中文关键词不匹配 —— 已修

**现象：** 开了 `match_whole_words` 之后，`中文关键词测试` 无法激活键为 `关键词` 的条目，
而真酒馆会激活。

**根因：** `WorldInfoBuffer.matchKeys` 的边界是 `(?:^|\W)(key)(?:$|\W)`，而 **JS 的 `\w`/`\W`
在所有模式下都是 ASCII**（`u` 只加 `\p{...}`），Python 的 `\w` 对 `str` 却是 Unicode 的。
端口写了 `re.UNICODE`，于是关键词两边的汉字被当成单词字符，`\W` 边界永远不成立。

**修法**（提交 `ff27ff3`）：改成 `re.ASCII`。

**怎么找到的：** 前两轮我都在"读代码"里找 bug，两次都是假的。这一轮改成**让判定机说话**：
新建 `fixtures/15-word-boundaries.json`，每个用例两条条目（`matchWholeWords` true/false）。
它当场报出 `9 entries vs 7` 并点名那两对 CJK。

**教训：** 读一个名字不等于跟着调用走。同名的东西在 `tavern/st/` 里有三份 `match_keys`、
两份计时效果实现。

---

## 4. 有意为之的取舍（不是 bug，但容易被当成 bug 报）

| 事项 | 为什么这样 |
|---|---|
| 三个镜像模块 `wi_keywords` / `wi_scan_state` / `wi_timed` 没有生产调用方 | 有意保留为参考实现。理由逐条记在 `tools/st-oracle/check_module_wiring.py` 的 `KNOWN_UNWIRED` 里，并由 `--fail-on-unwired` 守着：再出现一个**未声明**的未接线模块会直接红。 |
| 计时效果按**轮次**计，酒馆按**消息条数**计 | 已接受的差异，S1 夹具 11 通过。 |
| `backend.max_context_tokens` 默认 `0`（不裁剪） | 插件无法得知模型的真实上下文长度，猜错会静默删掉对话。填上才生效。 |
| 外部酒馆**只读** | 酒馆也暴露 `/delete`、`/edit`。从运行中的酒馆**导入**是便利；**删掉用户自己的资料库**是在一个为聊天而装的插件里做的破坏性操作。有测试解析请求路由，出现写路由就红。 |
| 回退默认关闭 | 回退意味着回答来自**另一个模型**。静默换模型比报错更糟，所以默认关，开启后在回复里明说。 |
