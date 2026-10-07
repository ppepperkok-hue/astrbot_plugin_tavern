# astrbot_plugin_tavern · 酒馆角色扮演

在 AstrBot 里用 [SillyTavern](https://github.com/SillyTavern/SillyTavern)（下称「酒馆」）的
**角色卡 / 世界书 / 聊天记录**与 QQ、Telegram、Discord 等平台上的用户对话。

本插件借鉴的是酒馆的**公开数据格式与行为语义**，不包含酒馆的任何代码或素材。
灵感来源与格式参考均标注在文末「致谢与许可」。

> 与酒馆的关系：酒馆的**世界书引擎与 Prompt 组装都在浏览器端**，服务端只负责文件读写与
> provider 转发。因此「把酒馆当后端」拿不到组装能力——本插件自带一套最小兼容引擎，
> 同时提供**可选**的外部酒馆后端（复用酒馆已配置的模型与预设），两条路都通。

---

## 功能

| 能力 | 说明 |
|---|---|
| 角色卡 | 导入 `.json` / `.png`（`tEXt` 元数据）/ `.yaml`，支持 Character Card V1 / V2 / V3 |
| 世界书 | 关键词、正则键（`/pattern/flags`）、整词匹配、`selective` 与四种 `selectiveLogic`、`scan_depth`、插入顺序与位置、概率、递归扫描、`sticky`/`cooldown`/`delay`、token 预算、条目分组 |
| 会话隔离 | 以 `unified_msg_origin`（平台:消息类型:会话 id）为键，每个群 / 每个私聊独立绑卡与分支 |
| 聊天记录 | 酒馆兼容 `.jsonl`（首行 header + 消息行 + `chat_metadata`），支持新开分支、历史预览 |
| 输出渲染 | 状态栏剥离、正则清洗、长回复按段落分段发送、零宽空格保护酒馆式排版 |
| 防刷屏 | 冷却、同会话并发上限、群聊仅 @ 或唤醒词触发 |
| 生成后端 | `astrbot`（默认，复用 AstrBot 已配置的模型）或 `sillytavern`（调用已部署的酒馆生成接口） |

## 安装

1. 把本仓库放进 AstrBot 的 `data/plugins/`，**目录名用 `astrbot_plugin_tavern`**
   （AstrBot 按目录名识别插件，`metadata.yaml` 的 `name` 需与之一致）。
2. 在 WebUI「插件管理」里点重载，或重启 AstrBot。
3. 数据目录会在首次加载时自动创建：`data/plugin_data/astrbot_plugin_tavern/`

```
data/plugin_data/astrbot_plugin_tavern/
├── cards/        # 角色卡 (.png/.json/.yaml)
├── worldbooks/   # 世界书 (.json)
├── presets/      # 酒馆预设导出 (.json，可选)
├── chats/        # 聊天记录 (酒馆兼容 .jsonl，自动创建)
└── state.json    # 每个会话的绑卡与世界书开关
```

> ⚠️ 不要把数据放进插件目录：从 WebUI 更新插件会整目录替换插件目录，
> 插件目录里的数据会被清空（插件社区已有真实翻车案例）。

## 使用

先把角色卡放进 `cards/`、世界书放进 `worldbooks/`，然后：

```
/tavern reload               重新扫描数据目录
/tavern list                 角色卡列表
/tavern use Iris             切换角色卡并开启新分支
/tavern card                 查看当前角色卡详情
/tavern worldbook list       世界书列表
/tavern worldbook on <名字>  启用世界书（off 关闭）
/tavern status               当前会话状态
/tavern new                  重开分支
/tavern history 10           最近 10 条记录
/tavern preview 你好         预览本轮真正发给模型的内容
/tavern import               查看导入目录说明
/tavern help                 帮助
```

指令组别名是 `酒馆`，子指令也都有中文别名（`/酒馆 换卡 Iris`）。
私聊直接说话即可；群聊需要 @机器人 或以唤醒词（默认 `酒馆`）开头。

## 配置

配置项定义在 [`_conf_schema.json`](_conf_schema.json)，可在 WebUI 插件页可视化修改：

* `trigger`：私聊直回、群聊仅 @、唤醒词、冷却、并发上限
* `worldbook`：开关、`scan_depth`、token 预算、递归层数、整词匹配、单轮注入上限
* `render`：单条最大字符数、分段间隔、首尾空行保护、状态栏剥离、正则规则（`正则=>替换`）
* `backend`：`astrbot` 或 `sillytavern`；酒馆模式下填写地址、Cookie 与证书校验
* `permissions`：换卡 / 导入是否仅管理员
* `debug`：日志输出组装结果、是否允许 `/tavern preview` 回显

## 目录结构

```
astrbot_plugin_tavern/
├── main.py              # AstrBot 入口：把插件目录挂到 sys.path 并导出 TavernPlugin
├── metadata.yaml        # 插件元数据
├── _conf_schema.json    # 配置 schema
├── tavern/
│   ├── main.py          # Star 子类：消息接管、指令组、后端选择、分段发送
│   ├── core.py          # 库 / 会话绑定 / 组装 / 渲染（不依赖 astrbot）
│   ├── config.py        # 配置解析与数据目录解析
│   ├── st/              # 酒馆兼容格式层（不依赖 astrbot，可单独复用）
│   │   ├── cards.py     #   角色卡 V1/V2/V3 + PNG tEXt
│   │   ├── worldbook.py #   世界书解析 + 激活引擎
│   │   ├── prompt.py    #   预设顺序 / 宏 / 历史裁剪
│   │   └── chat_store.py#   酒馆兼容 .jsonl 读写
│   └── backends/        # 生成后端
│       ├── base.py             # 接口与数据结构
│       ├── astrbot_provider.py # 复用 AstrBot 模型
│       └── sillytavern.py      # 调用外部酒馆 /api/backends/chat-completions/generate
├── tests/               # 离线测试（136 项）+ 可复现的酒馆格式夹具
├── tools/               # 夹具生成、开发检查、AstrBot 集成冒烟脚本
└── research/            # 立项调研报告（AstrBot API / 生态 / 酒馆格式 / 同类实现）
```

## 开发

```bash
python -m pytest tests -q          # 离线单元测试，不需要 AstrBot
python tools/check.py              # 清单校验 + ruff + pytest + AstrBot 集成冒烟
python tools/make_fixtures.py      # 重新生成酒馆格式夹具
```

`tools/astrbot_smoke.py` 与 `tools/astrbot_e2e.py` 用**真实 AstrBot** 验证插件加载与消息链路
（用 `data.plugins.<目录名>.main` 导入、构造真实 `AstrMessageEvent`、用假后端替代模型）：

```bash
astrbot_python tools/astrbot_smoke.py   # 指令组、插件类、initialize/terminate
astrbot_python tools/astrbot_e2e.py     # 一轮群聊：世界书注入 + 分段回复 + 落库
```

AstrBot 侧的兼容性已在 **4.28.2** 实测通过（指令组注册链、`chain_result` 组件列表、
`llm_generate(contexts=…)`、`data.plugin_data` 数据目录）。

## 已知边界

* 酒馆的 `outlet`、向量化世界书、`timed effects` 的时间语义（按秒）未实现：
  `sticky`/`cooldown` 按**轮次**计数，`vectorized` 条目需要外部匹配器才会激活。
* 群聊多角色（多个角色卡同群发言）尚未实现，当前是「一个会话一张卡」。
* 酒馆后端的 Cookie 与 CSRF 需要用户自行维护（酒馆对非 GET 请求强制 CSRF）。
* 插件未做市场发布；发布前需把 `metadata.yaml` 的 `author`/`repo` 换成真实信息。

## 致谢与许可

* 数据格式与交互设计参考 [SillyTavern](https://github.com/SillyTavern/SillyTavern)（AGPL-3.0），
  仅借鉴其公开的格式规范与行为语义，**未复制其代码或资源**。
* 角色卡 V2/V3 规范来自社区文档 `spec_v2.md` / `SPEC_V3.md`；
  世界书字段与默认值参考酒馆源码中的 `convertWorldInfoToCharacterBook()` 与默认预设顺序。
* 本项目自身许可：见仓库根目录的 `LICENSE`（发布前请确认）。
