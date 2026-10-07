# NapCat（真实 QQ 协议端）联调清单

本文件记录把 `.tools/napcat` 里的 NapCat 接到隔离的 AstrBot 测试根目录
（`.tools/e2e-root`）所需的步骤。真实账号登录需要用户提供 QQ 号并在 WebUI 扫码/输入密码，
本仓库不会记录任何账号凭据。

## 1. 已就绪的东西

| 组件 | 位置 | 状态 |
|---|---|---|
| AstrBot 4.28.2 | `.tools/uv-tools/astrbot/`（uv tool 安装） | ✅ |
| 插件 | 仓库根目录，通过 junction 挂到 `data/plugins/astrbot_plugin_tavern` | ✅ |
| 假 OneBot 客户端回归 | `tools/qq_e2e.py` | ✅ 已通过 |
| NapCat Shell（Windows, Node） | `.tools/napcat/`（`python tools/setup_napcat.py` 下载） | ✅ 已下载 |

## 2. 隔离测试根目录

`tools/qq_e2e.py` 会重新生成 `.tools/e2e-root`，其中的关键配置：

* `data/cmd_config.json`：`platform` 里一条 `aiocqhttp`（`ws_reverse_host=127.0.0.1`、
  `ws_reverse_port=6199`）、`wake_prefix=["/"]`、`plugin_set=["*"]`、dashboard 关闭。
* `data/config/astrbot_plugin_tavern_config.json`：`backend.type=sillytavern` 指向本地 mock；
  改成 `astrbot` 并配好 AstrBot 的模型 provider 即可用真实模型。
* `data/plugin_data/astrbot_plugin_tavern/`：角色卡、世界书与 `state.json` 绑定。

启动（保持根目录隔离）：

```powershell
cd E:\astrbot_plugin\.tools\e2e-root
E:\astrbot_plugin\.tools\uv-tools\astrbot\Scripts\astrbot.exe run -p 6200
```

## 3. 接真实 NapCat

1. 解压目录里找到 QQ 客户端：`.tools/napcat/` 下的 `napcat.bat` 会读 `config.json`；
   首次运行会引导安装/定位 QQNT。
2. 启动 NapCat 后打开它的 WebUI（默认 `http://127.0.0.1:6099`），登录一个 QQ 小号。
3. 在「网络配置」里新增**反向 WebSocket 客户端**：

   | 字段 | 值 |
   |---|---|
   | 名称 | astrbot |
   | URL | `ws://127.0.0.1:6199/ws` |
   | 消息格式 | array（OneBot v11 数组格式） |
   | 心跳 | 30 秒 |
   | Token | 留空（与 AstrBot `ws_reverse_token` 保持一致） |
   | 启用 | 是 |

   NapCat 会自动带上 `X-Self-ID` / `X-Client-Role` 头，这也是
   `tools/qq_e2e.py` 里假客户端必须手动补的握手头。
4. AstrBot 日志出现 `aiocqhttp ... adapter connected` 即接通。

## 4. 联调时要看的点

* 私聊直接发消息；群里需要 @机器人 或以 `/` 唤醒词开头（AstrBot 的 `wake_prefix`）。
* `/tavern status` 看当前群的绑卡与世界书；`/tavern preview` 看本轮真正发出去的 prompt。
* 世界书命中会出现在 system prompt 里，可用 `/tavern preview` 或 AstrBot 日志里的
  `[tavern] prompt preview:` 段核对。
* 长回复按 `render.max_chars_per_message` 分段发送；aiocqhttp 会 `strip()` 首尾空白，
  插件已用零宽空格保护。

## 5. 还没做的

* 多群多角色（一个群多张卡轮流说话）。
* 酒馆 `outlet`、向量化世界书、按秒计时的 timed effects。
* 插件市场发布（`metadata.yaml` 的 author/repo 已填好，需要时再走 cloud.astrbot.app）。
