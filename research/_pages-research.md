# AstrBot 插件 Pages 前端页面调研（面向 astrbot_plugin_tavern）

- 目标版本：**AstrBot 4.28.2**（本地安装于 `E:\astrbot_plugin\.tools\uv-tools\astrbot\`）
- 官方文档：<https://docs.astrbot.app/dev/star/guides/plugin-pages.html>（英文版 `/en/dev/star/guides/plugin-pages.html`）
- 结论优先级：**安装源码 > 官方文档**。凡两者不一致处，本文以源码为准并显式标注。
- 本文只做调研，未修改任何代码；除本文件外未写入任何文件。

---

## 1. 官方文档摘要（含安装源码补充）

### 1.1 什么是 Page

Page 是插件在 AstrBot WebUI 里自带的页面。页面文件放在插件目录的 `pages/` 下，Dashboard 用**受限 iframe** 加载它；页面脚本通过 `window.AstrBotPluginPage` bridge 与父页面（Dashboard）通信，父页面再把请求转发到插件注册的后端 Web API。

适用场景：复杂表单、运行状态面板、日志查看、文件上传/下载、SSE 实时流、图表。只是少量配置项时官方建议优先用 `_conf_schema.json`。

### 1.2 如何注册

**没有显式注册 API**，是目录约定 + 运行时扫描：

- 只扫描 `pages/<page_name>/index.html`，每个**一级子目录**是一个独立 Page；
- 没有 `index.html` 的目录被忽略；目录名不得为空、`.`、`..`、以 `.` 开头、含 `/` 或 `\`；
- 用户在 WebUI 插件详情页里打开插件声明的 Pages。

### 1.3 manifest / metadata 键

官方文档**没有要求任何 `metadata.yaml` 键**来实现 Page。文档只在 i18n 部分提到 Page 标题走 `pages.<page_name>.title`。

源码补充（文档未写、但可验证）：

- `metadata.yaml` 的 `pages:`（list）会被读进 `StarMetadata.pages`，但在 4.28.2 里**没有任何消费者**（详见 §2.5），因此可以不写；写了也不影响页面发现。
- 页面标题的真实来源是插件 i18n：`.astrbot-plugin/i18n/<locale>.json`，键路径 `metadata.display_name`、`pages.<page_name>.title`。

### 1.4 前端资源如何被服务与寻址

| 用途 | 路径 |
| --- | --- |
| Page 内容/资源 | `/api/plugin/page/content/<plugin_name>/<page_name>/<asset_path>`（空 asset_path 即入口 `index.html`） |
| bridge SDK | `/api/plugin/page/bridge-sdk.js` |
| 新版等价 API | `/api/v1/plugins/page/assets?plugin_id=&page_name=&asset_path=` |

要点：

- **不需要 build**：静态文件直接读盘返回，服务端**重写** HTML 的 `src/href`、CSS 的 `url()`、JS 的 `import/export ... from`、动态 `import()`，把相对路径改写成带 `asset_token` 与 `theme` 查询参数的绝对路径；
- 绝对 URL（`http(s)://`、`//`、`data:`、`blob:`、`mailto:`…）和 `#`/`/#` 不做重写；
- 如果 HTML 里已经有 `/api/plugin/page/bridge-sdk.js`，会被替换成带 token 的版本；否则 AstrBot 会在 `</body>` 前**自动注入**该 script；
- 鉴权：`asset_token` 是一个 HS256 JWT，`token_type=plugin_page_asset`，**TTL 仅 60 秒**，且与 `plugin_name` + `page_name` 绑定（换 Page/换插件即失效）。URL 里的 dashboard JWT 或 asset_token 二者任一有效即可。

### 1.5 Page 与插件 Python 之间的 API 形态

**不是 websocket，也不是页面直接 fetch**，而是：

```
Page iframe JS
  └─ window.AstrBotPluginPage.apiGet / apiPost / upload / download / subscribeSSE
       └─ postMessage(channel="astrbot-plugin-page", kind="request", action="api:get", endpoint=..., ...)
            └─ 父页面（Dashboard 的 PluginPagePage）收到后转成 HTTP：
                 GET/POST /api/v1/plugins/extensions/<plugin_name>/<endpoint>
                    └─ AstrBot 用 _match_registered_web_api() 在全局 registered_web_apis 里匹配
                         └─ 插件用 context.register_web_api() 注册的 handler
                              └─ handler 用 astrbot.api.web 的 json_response / error_response / ... 返回
```

- bridge endpoint **不带插件名前缀**（`apiGet("panel/state")`）；
- 插件注册的 route **必须带插件名前缀**（`/<plugin_name>/panel/state`）；
- 支持的动态段：`<name>` 匹配单段，`<path:name>` 匹配多段剩余路径，并以关键字参数传给 handler；
- 旧插件兼容：handler 仍运行在 Quart 兼容请求上下文里，可继续 `from quart import jsonify, request`；新代码推荐 `from astrbot.api.web import ...`。

---

## 2. 安装源码验证（AstrBot 4.28.2）

路径前缀统一为：
`E:\astrbot_plugin\.tools\uv-tools\astrbot\Lib\site-packages\astrbot\`

### 2.1 Page 注册 API（Python 侧）

`core\star\context.py`

```python
# :53
WebApiHandler = Callable[..., Awaitable[Any]]
# :54
RegisteredWebApi = tuple[str, WebApiHandler, list[str], str]

# :123
class Context:
    """暴露给插件的接口上下文。"""
    # :126  ← 类属性（全局共享），不是实例属性
    registered_web_apis: list[RegisteredWebApi] = []

    # :705  ← 真正的注册 API
    def register_web_api(
        self,
        route: str,
        view_handler: WebApiHandler,
        methods: list[str],
        desc: str,
    ) -> None:
```

真实签名（原文）：

```python
def register_web_api(
    self,
    route: str,
    view_handler: WebApiHandler,
    methods: list[str],
    desc: str,
) -> None:
```

语义（`context.py:723-727`）：同 `route` + 同 `methods` 则**原地替换**，否则 append。
⚠️ 全仓 `.py` 中除上述 4 处外**没有任何 `registered_web_apis.clear()` / `remove()`**，即该列表只在进程内增长，插件卸载/重载不清空。

### 2.2 提供 Page 资源的路由

`dashboard\api\plugins.py`（`legacy_router`，无前缀，走 dashboard 用户鉴权）：

```python
# :1457
@legacy_router.get("/api/plugin/page/bridge-sdk.js")
async def dashboard_get_plugin_page_bridge_sdk(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    page_service: PluginPageService = Depends(get_page_service),
):

# :1469
@legacy_router.get("/api/plugin/page/content/{plugin_id}/{page_name}/")

# :1487
@legacy_router.get("/api/plugin/page/content/{plugin_id}/{page_name}/{asset_path:path}")
```

新版路由挂在 `/api/v1` 前缀下（`dashboard\api\router.py:34` `API_V1_PREFIX = "/api/v1"`，`:38` `APIRouter(prefix=API_V1_PREFIX)`）：

```python
# dashboard\api\plugins.py:866
@router.get("/plugins/pages")            # ?plugin_id=
# :880
@router.get("/plugins/page")             # ?plugin_id=&page_name=  → 返回 content_path（内含 asset_token）
# :897
@router.get("/plugins/page/assets")      # ?plugin_id=&page_name=&asset_path=
# :1157 / :1171 / :1188
@router.get("/plugins/{plugin_id}/pages")
@router.get("/plugins/{plugin_id}/pages/{page_name}")
@router.get("/plugins/{plugin_id}/pages/{page_name}/assets/{asset_path:path}")
```

鉴权实现：`dashboard\server.py:243-277`（若路径属 Page 保护前缀，则从 `?asset_token=` 取额外候选 token）、`:279-311`（`_validate_dashboard_token`，asset token 需通过 `PluginPageAuth.is_scope_valid`）；`dashboard\plugin_page_auth.py:3-13`（保护前缀）、`:41-64`（scope 校验：token 的 plugin/page 必须等于 URL 里的 plugin/page）。

### 2.3 Page 发现 / 服务实现

`dashboard\services\plugin_page_service.py`

```python
# :23-29
PLUGIN_PAGE_ASSET_TOKEN_TYPE = "plugin_page_asset"
PLUGIN_PAGE_ASSET_TOKEN_TTL_SECONDS = 60
PLUGIN_PAGE_ROOT_DIR_NAME = "pages"
PLUGIN_PAGE_ENTRY_FILE_NAME = "index.html"
PLUGIN_PAGE_BRIDGE_FILE = Path(__file__).resolve().parent.parent / "plugin_page_bridge.js"

# :79
class PluginPageService:
    # :508  扫描 <plugin_root>/pages/*/index.html
    async def discover_plugin_pages(self, plugin: StarMetadata) -> list[PluginPage]:
    # :469  目录名合法性：空/. /../前导./含 / 或 \ → ValueError
    def normalize_plugin_page_name(raw_name: str) -> str:
    # :485  插件根目录 = plugin_store_path(或 reserved) / plugin.root_dir_name
    def get_plugin_root_dir(self, plugin: StarMetadata) -> Path:
    # :498  <plugin_root>/pages 必须存在且是目录
    async def resolve_plugin_pages_root(self, plugin: StarMetadata) -> Path:
    # :564  资产路径解析 + relative_to 防目录穿越
    async def resolve_plugin_page_file(self, plugin, page_name, asset_path) -> Path:
    # :345  HTML/CSS/JS 读盘 + 重写，其它类型按 MIME 原样返回二进制
    async def serve_page_content(self, *, plugin_name, page_name, asset_path,
                                 asset_token, jwt_secret=None, username, locale, theme)
    # :315  bridge SDK 内容 + 把初始 context 内联注入
    async def serve_bridge_sdk(self, *, asset_token, jwt_secret=None, locale, theme)
    # :157  initial context 组装
    def build_initial_context(self, *, asset_token, jwt_secret=None, locale, theme) -> dict | None
    # :429  安全响应头
    @staticmethod
    def build_security_headers() -> dict[str, str]:
    # :641  内容路径拼接
    @staticmethod
    def build_plugin_page_content_path(plugin_name, page_name, asset_path="") -> str:
```

值得注意的实现细节：

- `build_initial_context`（`:210-218`）返回 `{"pluginName", "displayName", "pageName", "pageTitle", "locale", "i18n", "isDark"}`；`serve_bridge_sdk`（`:335-339`）把它以 `window.AstrBotPluginPage?.__setInitialContext({...})` 内联追加到 bridge JS 末尾，所以 **`await bridge.ready()` 在页面里能立刻 resolve**，不必等父页面 postMessage（前提是 asset_token 有效且 dashboard jwt_secret 已配置）。
- 页面标题：`get_by_path(locale_data, f"pages.{page_name}.title") or page_name`（`:200-208`），i18n 目录由 `core\star\star_manager.py:564-603` 从 `.astrbot-plugin/i18n/*.json` 加载。
- HTML 重写：`rewrite_plugin_page_html`（`:702-749`），bridge script 注入在 `:741-748`；`apply_theme_to_html`（`:114-155`）会给 `<html>` 打 `data-theme` 并插入 `<meta name="color-scheme">`。
- 安全头（`:429-444`）：`Cache-Control: no-store`、`Referrer-Policy: no-referrer`、`X-Content-Type-Options: nosniff`、`Cross-Origin-Resource-Policy: cross-origin`、`Access-Control-Allow-Origin: *`、`Content-Security-Policy: object-src 'none'; base-uri 'self'`，非 Launcher 环境额外加 `frame-ancestors 'self'` + `X-Frame-Options: SAMEORIGIN`。**没有 `script-src`/`default-src` 限制**，所以外部 CDN 脚本、字体、图片不会被这份 CSP 拦住。
- 插件未启用时资产请求返回 403（`:360-361`），插件不存在 404。

### 2.4 Page → 插件 Python 的转发机制（核心）

`dashboard\api\plugins.py`

```python
# :59
require_plugin_scope = ScopeDependency("plugin")

# :379-421  五种 HTTP 方法都挂在同一条扩展路由上
@router.get("/plugins/extensions/{plugin_path:path}")
async def get_plugin_extension_route(plugin_path: str, request: Request,
                                     auth: AuthContext = Depends(require_plugin_scope)):
    return await _call_plugin_extension(plugin_path, request, auth.username)
# 另有 @router.post / put / patch / delete（:388 / :397 / :406 / :415）

# :190
async def _call_plugin_extension(plugin_path: str, request: Request, username: str):
    registered_web_apis = (
        request.app.state.core_lifecycle.star_context.registered_web_apis
    )
    matched_api = _match_registered_web_api(registered_web_apis, plugin_path, request.method)
    if not matched_api:
        return {"status": "error", "message": "未找到该路由", "data": {}}
    view_handler, path_values = matched_api
    plugin_name = plugin_path.strip("/").split("/", 1)[0].strip() or None
    plugin_request = PluginRequest(request, path_params=path_values,
                                   plugin_name=plugin_name, username=username)
    ...
    with bind_request_context(plugin_request):
        return await call_request_view(request, app_adapter, view_handler, path_values,
                                       g_obj=g_obj,
                                       quart_compat_path=_plugin_extension_legacy_path(...))
```

```python
# :153-163  路由 → 正则
def _plugin_api_route_pattern(route: str) -> str:
    # <name>       → (?P<name>[^/]+)
    # <path:name>  → (?P<name>.*)
    ...

# :166-179  匹配：request_path = "/" + subpath.lstrip("/")，用 re.fullmatch
def _match_registered_web_api(registered_web_apis, subpath: str, method: str):
    ...
    pattern = _plugin_api_route_pattern(route)
    matched = re.fullmatch(pattern, request_path)
```

旧兼容入口：`plugins.py:1506` `@legacy_router.api_route("/api/plug/{plugin_path:path}", methods=["GET","POST"])`。

请求/响应对象：`astrbot\api\web.py`

```python
# :20   class PluginMultiDict(Generic[ValueT]):
# :40   def get(self, key, default=None, type: Callable | None = None)   ← type 必须关键字传
# :62   def getlist(self, key) -> list[ValueT]
# :95   class PluginUploadFile:  save/read/write/seek/close
# :165  class PluginRequest:     method/path/headers/cookies/content_type/client_host/
#                                path_params/plugin_name/username/query + body()/json(default)/
#                                form()/files()
# :254  class PluginRequestProxy     ← contextvars 代理
# :322  request: PluginRequestProxy = PluginRequestProxy()
# :325  @contextmanager def bind_request_context(request_: PluginRequest)
# :342  def json_response(data=None, *, status_code=200, headers=None) -> JSONResponse
# :365  def error_response(message, *, status_code=400, data=None, headers=None) -> JSONResponse
# :390  def file_response(path, *, filename=None, content_type=None, headers=None) -> FileResponse
# :416  def stream_response(content, *, content_type="text/event-stream", status_code=200,
#                           headers=None) -> StreamingResponse
```

`astrbot\api\star\__init__.py:1-7`：`Context / Star / StarTools / register`，与文档示例一致。

### 2.5 metadata.yaml 的 `pages:` 键（文档未写，源码里是死键）

- `core\star\star_manager.py:490-562` `_load_plugin_metadata()`：`yaml.safe_load` 后只做必需字段校验（`core\star\updater.py:357-393`，额外键**允许**），然后 `pages=metadata["pages"] if isinstance(metadata.get("pages"), list) else []`（`:556-558`）。
- `core\star\star.py:75-76`：`pages: list[dict] = field(default_factory=list)`。
- 全仓 grep `.pages` 只有 `star_manager.py:1192` 一处赋值（`metadata.pages = metadata_yaml.pages`），**没有任何读取方**。插件详情/组件列表走的是 `dashboard\services\plugin_service.py:664-697`，其中 Page 组件由 `serialize_pages(plugin)`（即 §2.3 的文件系统扫描）产生，与 `metadata.yaml.pages` 无关。

结论：4.28.2 里 `metadata.yaml` 的 `pages:` 是**惰性/预留键**；页面能否出现完全取决于磁盘上的 `pages/<name>/index.html`。

### 2.6 前端（Dashboard）实际行为

`dashboard\dist\assets\PluginPagePage-yjHss7xZ.js`（构建产物，单行压缩；结论来自对该文件的阅读）：

- 先 `GET /api/v1/plugins/page?plugin_id=&page_name=` 拿到 `content_path`，再
  `iframe.src = new URL(content_path, origin)` 并 `searchParams.set("theme", isDark ? "dark" : "light")`；
- `sandbox="allow-scripts allow-forms allow-downloads"`（**没有 `allow-same-origin`**），`referrerpolicy="no-referrer"`；
- bridge endpoint → URL 的构造是 `` `/api/v1/plugins/extensions/${encodeURIComponent(pluginName)}/${endpoint}` ``，且 endpoint 会被校验：非空、不含 `\`、`://`、`?`、`#`，分段不得为空/`.`/`..`，否则抛 `Plugin bridge endpoint is invalid`；
- 父页面对每种 action 做映射：`api:get`→GET、`api:post`→POST、`files:upload`→multipart POST、`files:download`→GET blob、`sse:subscribe`→带 `Accept: text/event-stream` 的 fetch + 逐块解析 SSE 并回发 `sse_message` / `sse_state`。

`dashboard\plugin_page_bridge.js:207-285`（bridge API 全貌，已全文读取）：

```js
window.AstrBotPluginPage = {
  ready()                 // → Promise<context>
  getContext()            // 当前 context 或 null
  getLocale()             // 默认 "zh-CN"
  getI18n()               // 插件 i18n 字典
  t(key, fallback)        // 支持点号路径，按 [locale, zh-CN, en-US] 回退
  onContext(handler)      // 返回取消订阅函数
  __setInitialContext(ctx)  // 服务端内联调用
  apiGet(endpoint, params)
  apiPost(endpoint, body)
  upload(endpoint, file)  // 用 arrayBuffer 传输（transfer list）
  download(endpoint, params, filename)
  subscribeSSE(endpoint, handlers, params)   // handlers: {onMessage, onOpen, onError}
  unsubscribeSSE(subscriptionId)
}
```

消息循环在 `:146-205`（只接受 `event.source === window.parent`、校验 origin、`channel === "astrbot-plugin-page"`）；`ready` 的握手在 `:287`（`send("ready")`）。

### 2.7 本插件现状（`E:\astrbot_plugin`）

- 插件在 WebUI 里的名字 = `metadata.yaml` 的 `name: astrbot_plugin_tavern`（`star_manager.py:530-531`），也是 §2.4 的插件名前缀，**不是目录名，也不是 `display_name`**。
- `tavern\main.py:95-101`：`@register(...) class TavernPlugin(Star)`，`def __init__(self, context: Any, config: Any = None) -> None`。
- 目前没有任何 `pages/` 目录，也没有 `register_web_api` 调用（全仓 grep 无命中）。
- ⚠️ 测试会传 `context=None`：`tests\test_plugin_integration.py:368` `plugin = tavern.TavernPlugin(context=None, config=config_obj)`。因此新加的注册代码不能无条件调用 `context.register_web_api(...)`。

---

## 3. 4.28.2 上的最小可运行示例

### 3.1 目录布局（唯一合法形态）

```
<插件实际安装目录>/                 # = data/plugins/<root_dir_name>，见 astrbot_path.py:48-50
├─ metadata.yaml                    # 无需任何改动
├─ main.py
├─ tavern/main.py                   # TavernPlugin 在这里
└─ pages/
   └─ panel/
      └─ index.html                 # 必需；文件名只能是 index.html
```

注意：`pages/` 必须位于 **AstrBot 数据目录下 `plugins/<root_dir_name>/`** 里（`plugin_page_service.py:485-506`），`plugin_store_path = get_astrbot_plugin_path() = <data>/plugins`（`star_manager.py:204`、`core\utils\astrbot_path.py:48-50`）。开发仓库 `E:\astrbot_plugin` 是源码目录，真机要看实际安装位置；**`pages/` 目录名固定为小写 `pages`，Page 名（`panel`）就是子目录名**。

### 3.2 metadata.yaml / manifest 改动

**不需要任何改动。** 4.28.2 不读 `pages:` 键，也不需要声明 Page 列表。

可选（只为让标题好看）：新建 `.astrbot-plugin/i18n/zh-CN.json`：

```json
{
  "metadata": { "display_name": "酒馆角色扮演" },
  "pages": { "panel": { "title": "酒馆管理面板" } }
}
```

### 3.3 最小前端（2 个文件）

`pages/panel/index.html`

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <title>Tavern Panel</title>
  </head>
  <body>
    <h3 id="title">Tavern</h3>
    <button id="ping">Ping</button>
    <pre id="out"></pre>
    <script type="module" src="./app.js"></script>
  </body>
</html>
```

`pages/panel/app.js`

```js
const bridge = window.AstrBotPluginPage;
const ctx = await bridge.ready();          // 服务端已内联 initial context
const out = document.getElementById("out");
document.getElementById("title").textContent = ctx.pageTitle;
out.textContent = JSON.stringify(ctx, null, 2);

document.getElementById("ping").addEventListener("click", async () => {
  try {
    const r = await bridge.apiGet("panel/state", { limit: 5 });  // 不带插件名前缀
    out.textContent = JSON.stringify(r, null, 2);
  } catch (e) {
    out.textContent = `error: ${e.message}`;
  }
});
```

说明：不需要打包、不需要 npm、不需要手写 `<script src="/api/plugin/page/bridge-sdk.js">`（AstrBot 会注入）。用外部 module 脚本是因为 module 是 defer 的，一定会晚于注入的 bridge 标签执行；若必须写内联同步脚本，就在它前面显式加 `<script src="/api/plugin/page/bridge-sdk.js"></script>`。

### 3.4 最小 Python 改动（`tavern/main.py` 的 `TavernPlugin.__init__`）

```python
from astrbot.api.web import json_response, request   # 若走 try/except 守卫导入，放到守卫块里

class TavernPlugin(Star):
    def __init__(self, context: Any, config: Any = None) -> None:
        super().__init__(context)
        self.context = context
        # ... 现有初始化 ...
        register_web_api = getattr(context, "register_web_api", None)
        if callable(register_web_api):          # 测试里 context=None，必须守卫
            register_web_api(
                f"/{PLUGIN_NAME}/panel/state",  # 必须带插件名前缀
                self.page_state,
                ["GET"],
                "Tavern panel state",
            )

    async def page_state(self):
        limit = request.query.get("limit", 20, type=int)   # type 必须关键字传
        return json_response({"ok": True, "limit": limit, "user": request.username})
```

对应关系：`apiGet("panel/state")` → `GET /api/v1/plugins/extensions/astrbot_plugin_tavern/panel/state` → 匹配 route `/astrbot_plugin_tavern/panel/state`。

### 3.5 是否需要 build step

**不需要。** `pages/` 里的 HTML/CSS/JS/SVG 等按原样读盘返回，服务端做路径重写；没有 Vite/Webpack 要求，也不需要 manifest 声明产物。

### 3.6 生效时机

- 新增/删除 **Page 目录**：`discover_plugin_pages` 是**每次请求实时扫盘**（`plugin_page_service.py:508-538`，无缓存），所以理论上新增目录后刷新 WebUI 就能看到；官方文档仍建议重载插件（保守做法，也可能是为了兼容旧版本/浏览器缓存）。此点只由源码推断，未实机验证。
- 修改 **Python 路由**：`register_web_api` 只在插件 `__init__` 时执行，**必须重载插件**。
- 只改静态资源：刷新页面即可。

---

## 4. 无法验证的内容 / 文档与源码的分歧

### 4.1 文档与源码的分歧

| 项 | 文档说法 | 安装源码（4.28.2） | 以谁为准 |
| --- | --- | --- | --- |
| Page 注册方式 | 未提；只说放 `pages/<name>/index.html` | 完全一致：纯目录扫描，无注册 API | 一致，无分歧 |
| `metadata.yaml` 键 | 未提 Page 相关键 | 存在 `pages:` 键、会被解析进 `StarMetadata.pages`，但**无任何读取方** | 源码：该键当前无效果，别依赖 |
| bridge endpoint | 不带插件名前缀，Dashboard 转发到 `/api/v1/plugins/extensions/<plugin_name>/...` | 与文档逐字一致（`dashboard\dist\...PluginPagePage-*.js` 中 `${pluginName}/${endpoint}`） | 一致 |
| 增删 Page 目录 | 「需要重载插件」 | 实时扫盘，理论上不需要 | 文档更保守；源码更准确（未实机验证） |
| 路由前缀是否强制 | 文档说「路由需要包含插件名作为前缀」 | 源码 `_match_registered_web_api` 用**完整路径**（含插件名段）匹配，所以前缀是事实必需，但**没有校验**注册者是否真的拥有该插件名 | 源码：必需但无归属校验（见 §5 安全注记） |

### 4.2 未能验证 / 未读到的东西

1. **官方文档 Page 的尾部章节（Bridge API 细节、注意事项）没读到**：`web_fetch` 返回被截断，中英文版都在同一位置截断；`raw.githubusercontent.com` 在本机 DNS 解析失败，无法直接取文档源文件。补偿方式：完整读取了 `dashboard\plugin_page_bridge.js`（bridge 的唯一实现，权威性高于文档）。
2. **没有端到端实跑**：未启动 AstrBot WebUI、未真的创建 `pages/` 目录、未截图确认页面出现。因为本任务要求除本报告外不写任何文件，起服务/建页面都会写盘。因此「页面能出现」是源码推断结论（发现逻辑 + 路由 + 鉴权链路均已逐段核对）。
3. **`request.username` 是否恒为非空**：链路是 `require_plugin_scope`（dashboard JWT）→ `auth.username` → `PluginRequest(username=...)`，理论上必然非空；未实机确认。
4. **Launcher（桌面客户端）模式**：`build_security_headers` 在 `ASTRBOT_LAUNCHER=1/true` 时不加 `frame-ancestors`/`X-Frame-Options`（`plugin_page_service.py:440-443`），未验证该环境变量在本机实际是否设置、以及是否影响嵌入其他界面。
5. **`metadata.yaml.pages` 无消费者**：结论基于对安装目录全部 `.py` 的 grep；未检查插件市场规范文档（`/dev/plugin-market/`）是否要求或使用它。
6. **SSE 长连接与 60s token**：源码显示 `sse:subscribe` 由父页面用 dashboard JWT 调 `/api/v1/plugins/extensions/...`（不是 asset_token），所以理论上不受 60s 影响；未实测长连接。
7. **打包产物形态**（Vite 产出的 hash 文件名、import map、`new URL(..., import.meta.url)` 之类）：`rewrite_plugin_page_js`（`plugin_page_service.py:778-838`）只重写 `import(...)`、`import/export ... from`、副作用 `import "..."` 三种字面量形式，其它动态拼接路径不会被重写；未实测。
8. **插件间路由劫持**：`registered_web_apis` 是全局列表且按注册顺序 `re.fullmatch` 首个命中即用，任何插件都能注册 `/<其他插件名>/...` 而仍然匹配成功（前缀只是约定，没有归属校验）。这是从源码读出的结构性风险，未构造验证用例。
9. 本文件的 `pages/panel/...` 示例未落盘，也未跑通（见第 2 条）。

---

## 5. 已核验的文件与行号清单

路径前缀（除最后三条外）：
`E:\astrbot_plugin\.tools\uv-tools\astrbot\Lib\site-packages\astrbot\`

| 文件 | 行号 | 核验内容 |
| --- | --- | --- |
| `core\star\context.py` | 53-54, 123-126, 705-727 | `WebApiHandler` / `RegisteredWebApi` 类型别名；`Context.registered_web_apis` 类属性（:126）；`register_web_api` 真实签名及替换语义 |
| `core\star\star.py` | 60-76 | `StarMetadata.display_name / i18n / pages` 字段定义 |
| `core\star\star_manager.py` | 204, 208, 490-562, 564-603, 1179-1193 | `plugin_store_path = get_astrbot_plugin_path()`；`_load_plugin_metadata`（含 `pages=metadata["pages"]` :556-558）；i18n 目录 `.astrbot-plugin/i18n`（:565-570）；yaml 元数据覆盖 `metadata.pages`（:1192） |
| `core\star\updater.py` | 357-393 | `validate_plugin_metadata`：只校验必需字段，**额外键允许** |
| `core\utils\astrbot_path.py` | 48-50 | `get_astrbot_plugin_path()` = `<data>/plugins` |
| `dashboard\services\plugin_page_service.py` | 23-29, 79-90 | 常量（TTL 60s、`pages`、`index.html`、bridge 文件路径） |
| 同上 | 157-218 | `build_initial_context`（含 `pages.<page_name>.title` :200-208） |
| 同上 | 315-343 | `serve_bridge_sdk`，`:335-339` 内联 `__setInitialContext` |
| 同上 | 345-427 | `serve_page_content`（HTML/CSS/JS 重写分支） |
| 同上 | 429-444 | `build_security_headers`（CSP、X-Frame-Options、Launcher 分支） |
| 同上 | 449-506, 508-538, 540-580 | 路径/目录名校验、`discover_plugin_pages`（只看 `pages/*/index.html`）、`get_plugin_page`、`resolve_plugin_page_file` |
| 同上 | 641-673 | `build_plugin_page_content_path`（`/api/plugin/page/content/...`）与 `/api/plugin/page/bridge-sdk.js` |
| 同上 | 702-749, 751-776, 778-838 | HTML / CSS / JS 相对资源重写 + bridge 注入 |
| 同上 | 854-919 | `serialize_plugin_page(s)`、`issue_plugin_page_asset_token`（JWT payload / TTL） |
| `dashboard\plugin_page_auth.py` | 3-13, 15-22, 24-39, 41-64 | 受保护路径前缀、asset token 提取与 plugin/page scope 校验 |
| `dashboard\plugin_page_bridge.js` | 1-288（API 207-285；消息循环 146-205；通道常量 :2） | bridge 的**全部**前端 API 与 postMessage 协议 |
| `dashboard\api\plugins.py` | 55-59, 146-163, 166-179 | `require_plugin_scope = ScopeDependency("plugin")`；route→正则（`<name>` / `<path:name>`）；`_match_registered_web_api` |
| 同上 | 182-229, 232-243, 245-261, 264-285, 288-301, 304-322 | `_call_plugin_extension`、locale/theme 提取、错误响应、`_serve_plugin_page_content` / bridge SDK 处理函数、Page 入口配置 |
| 同上 | 379-421 | `GET/POST/PUT/PATCH/DELETE /plugins/extensions/{plugin_path:path}` |
| 同上 | 866-913 | `/plugins/pages`、`/plugins/page`（`plugin_id`+`page_name` query）、`/plugins/page/assets` |
| 同上 | 1157-1205 | `/plugins/{plugin_id}/pages`、`.../pages/{page_name}`、`.../pages/{page_name}/assets/{asset_path:path}`（装饰器行号来自 grep） |
| 同上 | 1457-1503, 1506-1512 | `/api/plugin/page/bridge-sdk.js`、`/api/plugin/page/content/...`（:1469 / :1487）、`/api/plug/{plugin_path:path}` |
| `dashboard\api\router.py` | 34-38, 39-96 | `API_V1_PREFIX = "/api/v1"` 与子路由挂载 |
| `dashboard\server.py` | 32, 243-277 | 引入 `PluginPageAuth`；中间件里 asset_token 作为候选 token（:243-254） |
| 同上 | 279-311 | `_validate_dashboard_token` + `PluginPageAuth.is_asset_token/is_scope_valid` |
| `dashboard\services\plugin_service.py` | 650-659, 664-697 | 插件详情 dict（只带 `i18n`，**没有** `pages`）；`get_plugin_page_components` 来自 `serialize_pages` |
| `dashboard\dist\assets\PluginPagePage-yjHss7xZ.js` | 全文件（压缩单行，9326 B） | iframe `src`=`content_path`+`theme`；`sandbox="allow-scripts allow-forms allow-downloads"`；endpoint → `/api/v1/plugins/extensions/<pluginName>/<endpoint>`；endpoint 合法性校验；bridge 各 action 的 HTTP 映射 |
| `api\web.py` | 20-91, 95-162, 165-246, 249-322, 325-339, 342-439, 442-453 | `PluginMultiDict`（`get(key, default, type=)`）、`PluginUploadFile`、`PluginRequest`、`request` 代理、`bind_request_context`、四个响应 helper 与 `__all__` |
| `api\star\__init__.py` | 1-7 | `Context / Star / StarTools / register` 导出 |
| `E:\astrbot_plugin\metadata.yaml` | `name: astrbot_plugin_tavern`（`display_name`/`astrbot_version` 等） | 插件名 = 路由前缀 |
| `E:\astrbot_plugin\main.py` | 1-32 | 入口只转发到 `tavern.main` |
| `E:\astrbot_plugin\tavern\main.py` | 95-101 | `@register(...)` + `class TavernPlugin(Star)` + `__init__(self, context, config=None)` |
| `E:\astrbot_plugin\tests\test_plugin_integration.py` | 368 | `TavernPlugin(context=None, config=config_obj)` → 注册代码必须守卫 |
| `E:\astrbot_plugin\research\01-astrbot-plugin-api.md` | 795-834, 905 | 既有 §7.2 的 Page 说法与本调研一致；本文补上了它列的开放问题 #5（鉴权/iframe 限制）的一部分 |

外部引用：<https://docs.astrbot.app/dev/star/guides/plugin-pages.html>、<https://docs.astrbot.app/en/dev/star/guides/plugin-pages.html>（两份均在同一位置被截断，见 §4.2）。

---

## 6. 开放问题

1. 文档结尾的 Bridge API 章节原文（中/英）始终取不到，是否还有本文未覆盖的限制（例如官方对 `sandbox` 或 CDN 的额外建议）？
2. 「新增 `pages/` 目录是否需要重载插件」在 4.28.2 上是否真的可以免重载（源码看起来可以，但 WebUI 端是否缓存插件列表 `GET /api/v1/plugins` / 详情、需要实测）。
3. `asset_token` 60s TTL 在长驻页面上的实际体验：页面打开超过 60s 后再加载**新的**相对资源（动态 `import()`、懒加载图片）是否会 401？入口 HTML 与首屏资源没问题，懒加载需要实测。
4. Launcher/桌面端（`ASTRBOT_LAUNCHER=1`）下 CSP 放宽后，是否允许被非 dashboard 页面嵌入（安全边界）。
5. 插件间路由劫持（§4.2 第 8 条）是设计还是缺陷；插件市场是否有命名/前缀强制校验。
6. 是否需要为多 Page 提供显式排序/分组（目前顺序 = 目录名小写排序，`plugin_page_service.py:516-519`；且 `pages:` 键无消费者，没有声明式配置的余地）。
7. `pages/` 目录里的构建产物（hash 文件名 + 动态 import 映射）在 `rewrite_plugin_page_js` 的三种正则之外是否会被漏改（未实测）。
8. 插件市场规范 2026-06-27 是否对 `pages/` 有额外要求（本次未读该文档）。
