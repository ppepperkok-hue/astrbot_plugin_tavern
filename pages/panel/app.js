/*
 * Tavern management console.
 *
 * Everything the plugin serves goes through one place: `PLUGIN_ID` below must equal
 * `metadata.yaml`'s `name`. AstrBot matches the route against
 * `/<metadata.name>/<endpoint>`, and a mismatch is answered with "未找到该路由" and
 * nothing else -- no hint that the prefix is the problem. So it is spelled once.
 *
 * No framework and no build step, and deliberately a *single* file. AstrBot serves
 * plugin page assets with a short-lived (60s) token bound to plugin+page, so a page
 * left open then lazily importing a second module can 401; one file, loaded with the
 * page, cannot. `styles.css` is safe because the entry HTML requests it immediately.
 */

const PLUGIN_ID = "astrbot_plugin_tavern";
const bridge = window.AstrBotPluginPage;

// ---------------------------------------------------------------------------
// transport
// ---------------------------------------------------------------------------

/** GET a panel endpoint. Throws with the server's message on `{ok:false}`. */
async function apiGet(endpoint, params) {
  const query = params ? "?" + new URLSearchParams(params).toString() : "";
  const payload = await bridge.apiGet(`${PLUGIN_ID}/${endpoint}${query}`);
  if (payload && payload.ok === false) throw new Error(payload.error || "请求失败");
  return payload && Object.prototype.hasOwnProperty.call(payload, "data")
    ? payload.data
    : payload;
}

/** POST a panel action. Returns the whole envelope so `message` survives. */
async function apiPost(endpoint, body) {
  const payload = await bridge.apiPost(`${PLUGIN_ID}/${endpoint}`, body);
  if (payload && payload.ok === false) throw new Error(payload.error || "操作失败");
  return payload || {};
}

// ---------------------------------------------------------------------------
// dom helpers
// ---------------------------------------------------------------------------

const el = (id) => document.getElementById(id);

function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char],
  );
}

/** Render an error into a container instead of letting it escape to the console. */
async function guard(container, work) {
  try {
    await work();
  } catch (error) {
    container.innerHTML = `<div class="banner err">${esc(error.message || error)}</div>`;
  }
}

let toastTimer = 0;

function toast(message, kind = "ok") {
  const box = el("toast");
  box.textContent = message;
  box.className = `toast ${kind}`;
  box.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    box.hidden = true;
  }, 3600);
}

const empty = (text) => `<div class="empty">${esc(text)}</div>`;

//: The AstrBot session the console acts on. Held in memory only -- the iframe is
//: sandboxed without `allow-same-origin`, so there is no localStorage to persist it.
let scope = "";

// ---------------------------------------------------------------------------
// views
// ---------------------------------------------------------------------------

const views = {};

/** Load a card or book list once and reuse it across view switches. */
let libraryCache = null;

async function library(refresh = false) {
  if (!libraryCache || refresh) libraryCache = (await apiGet("panel/library")) || {};
  return libraryCache;
}

views.overview = async (box) => {
  box.innerHTML = '<div class="loading">加载中…</div>';
  await guard(box, async () => {
    const [state, lib] = await Promise.all([apiGet("panel/state"), library()]);
    const backend = state.backend || {};
    const counts = {
      cards: (lib.cards || []).length,
      books: (lib.worldbooks || []).length,
    };
    const remoteReady = Boolean(backend.st_base_url && backend.st_cookie_set);

    box.innerHTML = `
      <h1>总览</h1>
      <p class="lead">角色卡、世界书与分支都放在 AstrBot 的数据目录里，插件更新不会丢。</p>
      <div class="grid">
        <div class="card stat">
          <span class="stat-num">${counts.cards}</span><span class="stat-label">角色卡</span>
        </div>
        <div class="card stat">
          <span class="stat-num">${counts.books}</span><span class="stat-label">世界书</span>
        </div>
        <div class="card stat">
          <span class="stat-num">${backend.max_context_tokens || "∞"}</span>
          <span class="stat-label">上下文上限</span>
        </div>
        <div class="card stat">
          <span class="stat-num">${backend.fallback_to_astrbot ? "开" : "关"}</span>
          <span class="stat-label">酒馆回退</span>
        </div>
      </div>

      <h2>后端</h2>
      <dl class="kv">
        <dt>类型</dt><dd>${esc(backend.type || "未设置")}</dd>
        <dt>模型</dt><dd>${esc(backend.provider_id || "跟随当前会话")}</dd>
        <dt>外部酒馆</dt><dd class="${remoteReady ? "ok" : "muted"}">${
          remoteReady ? esc(backend.st_base_url) : "未配置"
        }</dd>
      </dl>
      ${
        backend.fallback_to_astrbot
          ? `<div class="banner">已开启回退：酒馆不可用时改用 AstrBot 的模型，回复里会明确说明${
              backend.fallback_notice ? "" : "（<b>提示已关闭</b>，这条回退将是静默的）"
            }。</div>`
          : ""
      }

      <h2>世界书默认值</h2>
      <dl class="kv">
        <dt>扫描深度</dt><dd>${esc(state.config?.worldbook?.scan_depth ?? "?")}</dd>
        <dt>token 预算</dt><dd>${esc(state.config?.worldbook?.token_budget ?? "?")}</dd>
        <dt>整词匹配</dt><dd>${
          state.config?.worldbook?.match_whole_words ? "开启（中文建议关闭）" : "关闭"
        }</dd>
      </dl>

      <div class="row gap">
        <button class="act primary" id="reload-library">重新扫描数据目录</button>
        <button class="act" data-goto="cards">浏览角色卡</button>
        <button class="act" data-goto="sessions">管理分支</button>
      </div>`;

    el("reload-library").addEventListener("click", async () => {
      try {
        const result = await apiPost("panel/action", {
          action: "reload_library",
          scope: scope || "console",
        });
        libraryCache = null;
        toast(result.message || "已重载");
        await views.overview(box);
      } catch (error) {
        toast(error.message, "err");
      }
    });
    box.querySelectorAll("[data-goto]").forEach((button) => {
      button.addEventListener("click", () => select(button.dataset.goto));
    });
  });
};

views.cards = async (box) => {
  box.innerHTML = '<div class="loading">加载中…</div>';
  await guard(box, async () => {
    const lib = await library();
    const cards = lib.cards || [];
    if (!cards.length) {
      box.innerHTML = `<h1>角色卡</h1>${empty(
        "还没有导入角色卡。把 .png / .json / .yaml 直接发给机器人即可。",
      )}`;
      return;
    }
    box.innerHTML = `
      <h1>角色卡 <span class="count">${cards.length}</span></h1>
      <input type="search" id="card-filter" placeholder="筛选…" />
      <div class="grid" id="card-grid"></div>`;

    const paint = (keyword) => {
      const shown = cards.filter(
        (card) => !keyword || String(card.name).toLowerCase().includes(keyword),
      );
      el("card-grid").innerHTML = shown.length
        ? shown
            .map(
              (card) => `
          <div class="card">
            <h3>${esc(card.name)}</h3>
            <p>${esc((card.description || "").slice(0, 140) || "（没有简介）")}</p>
            <div class="row">
              ${card.has_world_book ? '<span class="tag">内嵌世界书</span>' : ""}
              ${card.spec ? `<span class="tag muted">${esc(card.spec)}</span>` : ""}
            </div>
            <div class="row gap">
              <button class="act" data-detail="${esc(card.name)}">查看全文</button>
              <button class="act primary" data-bind="${esc(card.name)}">用这个卡开始对话</button>
            </div>
            <div data-slot="${esc(card.name)}"></div>
          </div>`,
            )
            .join("")
        : empty("没有匹配的角色卡。");
    };
    paint("");
    el("card-filter").addEventListener("input", (event) =>
      paint(event.target.value.trim().toLowerCase()),
    );

    box.addEventListener("click", async (event) => {
      const detail = event.target.closest("[data-detail]");
      if (detail) {
        const name = detail.dataset.detail;
        const slot = box.querySelector(`[data-slot="${CSS.escape(name)}"]`);
        if (slot.dataset.loaded) {
          slot.innerHTML = "";
          delete slot.dataset.loaded;
          return;
        }
        slot.innerHTML = '<div class="loading">加载中…</div>';
        try {
          const card = await apiGet(`panel/card/${encodeURIComponent(name)}`);
          slot.dataset.loaded = "1";
          slot.innerHTML = [
            "description",
            "personality",
            "scenario",
            "first_mes",
            "mes_example",
          ]
            .filter((field) => card[field])
            .map(
              (field) =>
                `<dt>${esc(field)}</dt><dd><pre>${esc(card[field])}</pre></dd>`,
            )
            .join("");
          slot.className = "kv detail";
        } catch (error) {
          slot.innerHTML = `<div class="banner err">${esc(error.message)}</div>`;
        }
        return;
      }

      const bind = event.target.closest("[data-bind]");
      if (!bind) return;
      if (!scope) {
        toast("请先在「会话与分支」里选择要操作的会话。", "err");
        return;
      }
      try {
        const result = await apiPost("panel/action", {
          action: "bind_card",
          scope,
          card: bind.dataset.bind,
        });
        toast(result.message || "已切换");
      } catch (error) {
        toast(error.message, "err");
      }
    });
  });
};

views.books = async (box) => {
  box.innerHTML = '<div class="loading">加载中…</div>';
  await guard(box, async () => {
    const lib = await library();
    const books = lib.worldbooks || [];
    if (!books.length) {
      box.innerHTML = `<h1>世界书</h1>${empty("还没有导入世界书。")}`;
      return;
    }
    box.innerHTML = `
      <h1>世界书 <span class="count">${books.length}</span></h1>
      <table>
        <thead><tr><th>名称</th><th>条目</th><th>常驻</th><th>计时</th><th>关键词</th><th></th></tr></thead>
        <tbody>${books
          .map(
            (book) => `
          <tr>
            <td>${esc(book.name)}</td>
            <td>${esc(book.entries ?? "")}</td>
            <td>${esc(book.constant ?? "")}</td>
            <td>${esc(book.timed ?? "")}</td>
            <td class="muted">${esc((book.keys || []).slice(0, 6).join("、"))}</td>
            <td class="row gap">
              <button class="act" data-expand="${esc(book.name)}">展开</button>
              <button class="act" data-toggle="${esc(book.name)}">启用/关闭</button>
            </td>
          </tr>
          <tr hidden data-detail-row="${esc(book.name)}"><td colspan="6"></td></tr>`,
          )
          .join("")}</tbody>
      </table>`;

    box.addEventListener("click", async (event) => {
      const expand = event.target.closest("[data-expand]");
      if (expand) {
        const name = expand.dataset.expand;
        const row = box.querySelector(`[data-detail-row="${CSS.escape(name)}"]`);
        if (!row.hidden) {
          row.hidden = true;
          expand.textContent = "展开";
          return;
        }
        row.hidden = false;
        expand.textContent = "收起";
        const cell = row.firstElementChild;
        if (cell.dataset.loaded) return;
        cell.innerHTML = '<div class="loading">加载中…</div>';
        try {
          const book = await apiGet(`panel/book/${encodeURIComponent(name)}`);
          cell.dataset.loaded = "1";
          cell.innerHTML = `<table><thead><tr><th>uid</th><th>关键词</th><th>内容</th><th>计时</th></tr></thead>
            <tbody>${(book.entries || [])
              .map(
                (entry) => `
              <tr>
                <td>${esc(entry.uid)}</td>
                <td>${esc((entry.keys || []).join("、"))}</td>
                <td><pre>${esc(entry.content)}</pre></td>
                <td class="muted">${esc(
                  [
                    entry.sticky ? `粘滞${entry.sticky}` : "",
                    entry.cooldown ? `冷却${entry.cooldown}` : "",
                    entry.delay ? `延迟${entry.delay}` : "",
                  ]
                    .filter(Boolean)
                    .join(" ") || "-",
                )}</td>
              </tr>`,
              )
              .join("")}</tbody></table>`;
        } catch (error) {
          cell.innerHTML = `<div class="banner err">${esc(error.message)}</div>`;
        }
        return;
      }

      const toggle = event.target.closest("[data-toggle]");
      if (!toggle) return;
      if (!scope) {
        toast("请先在「会话与分支」里选择要操作的会话。", "err");
        return;
      }
      const name = toggle.dataset.toggle;
      const sessions = await apiGet("panel/sessions", { scope });
      const on = (sessions.worldbooks || []).includes(name);
      try {
        const result = await apiPost("panel/action", {
          action: "toggle_book",
          scope,
          book: name,
          enabled: !on,
        });
        toast(result.message || "已更新");
      } catch (error) {
        toast(error.message, "err");
      }
    });
  });
};

views.sessions = async (box) => {
  box.innerHTML = '<div class="loading">加载中…</div>';
  await guard(box, async () => {
    const state = await apiGet("panel/state");
    const known = state.sessions || [];
    box.innerHTML = `
      <h1>会话与分支</h1>
      <p class="lead">
        一个群或一个私聊就是一条会话。这里选择要操作的会话，然后切换角色卡或管理分支。
      </p>
      <div class="row gap">
        <input id="scope-input" type="text" placeholder="粘一个会话标识，例如 aiocqhttp:GroupMessage:123456"
               value="${esc(scope)}" />
        <button class="act primary" id="scope-load">载入</button>
      </div>
      ${
        known.length
          ? `<p class="muted">已知会话：${known
              .map(
                (item) =>
                  `<button class="link" data-scope="${esc(item.scope)}">${esc(item.scope)}</button>`,
              )
              .join(" ")}</p>`
          : '<p class="muted">还没有任何会话在用过这个插件。</p>'
      }
      <div id="session-body"></div>`;

    el("scope-load").addEventListener("click", () => loadScope(el("scope-input").value.trim()));
    box.querySelectorAll("[data-scope]").forEach((button) =>
      button.addEventListener("click", () => loadScope(button.dataset.scope)),
    );

    async function loadScope(value) {
      if (!value) {
        toast("先填一个会话标识。", "err");
        return;
      }
      scope = value;
      const body = el("session-body");
      body.innerHTML = '<div class="loading">加载中…</div>';
      await guard(body, async () => {
        const data = await apiGet("panel/sessions", { scope });
        const worldbooks = (await library()).worldbooks || [];
        body.innerHTML = `
          <h2>${esc(data.scope)}</h2>
          <dl class="kv">
            <dt>角色卡</dt><dd>${esc(data.card || "（未选择）")}</dd>
            <dt>当前分支</dt><dd>${esc(data.chat || "-")}</dd>
          </dl>
          <h2>分支</h2>
          <div class="row gap">
            <button class="act primary" id="new-branch">开启新分支</button>
          </div>
          ${
            (data.branches || []).length
              ? `<table><thead><tr><th>分支</th><th>状态</th><th></th></tr></thead><tbody>${data.branches
                  .map(
                    (name) => `
                <tr>
                  <td>${esc(name)}</td>
                  <td>${name === data.chat ? '<span class="tag">使用中</span>' : ""}</td>
                  <td class="row gap">
                    <button class="act" data-use="${esc(name)}">切换</button>
                    <button class="act" data-rename="${esc(name)}">改名</button>
                    <button class="act danger" data-del="${esc(name)}">删除</button>
                  </td>
                </tr>`,
                  )
                  .join("")}</tbody></table>`
              : empty("这个角色还没有任何分支。")
          }
          <h2>启用的世界书</h2>
          <div class="grid compact">${worldbooks
            .map(
              (book) => `
            <label class="check">
              <input type="checkbox" data-book="${esc(book.name)}"
                     ${(data.worldbooks || []).includes(book.name) ? "checked" : ""} />
              <span>${esc(book.name)} <em class="muted">${esc(book.entries ?? "")} 条</em></span>
            </label>`,
            )
            .join("")}</div>`;

        el("new-branch").addEventListener("click", () => act("new_chat", {}));
        body.querySelectorAll("[data-book]").forEach((checkbox) =>
          checkbox.addEventListener("change", () =>
            act("toggle_book", {
              book: checkbox.dataset.book,
              enabled: checkbox.checked,
            }),
          ),
        );
        body.querySelectorAll("[data-use]").forEach((button) =>
          button.addEventListener("click", () => act("use_chat", { chat: button.dataset.use })),
        );
        body.querySelectorAll("[data-del]").forEach((button) =>
          button.addEventListener("click", () => {
            if (window.confirm(`删除分支「${button.dataset.del}」？此操作不可撤销。`)) {
              act("delete_chat", { chat: button.dataset.del });
            }
          }),
        );
        body.querySelectorAll("[data-rename]").forEach((button) =>
          button.addEventListener("click", () => {
            const name = window.prompt("新的分支名：", button.dataset.rename);
            if (name && name !== button.dataset.rename) {
              act("rename_chat", { chat: button.dataset.rename, new_name: name });
            }
          }),
        );

        async function act(action, extra) {
          try {
            const result = await apiPost("panel/action", { action, scope, ...extra });
            toast(result.message || "已更新");
            await loadScope(scope);
          } catch (error) {
            toast(error.message, "err");
          }
        }
      });
    }

    if (scope) await loadScope(scope);
  });
};

views.remote = async (box) => {
  box.innerHTML = '<div class="loading">正在连接外部酒馆…</div>';
  await guard(box, async () => {
    const [books, characters] = await Promise.all([
      apiGet("panel/st/worldbooks"),
      apiGet("panel/st/characters"),
    ]);
    box.innerHTML = `
      <h1>外部酒馆</h1>
      <div class="banner">
        这里只做<b>读取</b>：把酒馆里已有的内容拉进来，不会修改或删除酒馆里的任何文件。
        导入请用聊天里的 <code>/tavern st import</code>。
      </div>
      <h2>世界书 <span class="count">${(books || []).length}</span></h2>
      ${
        (books || []).length
          ? `<table><thead><tr><th>名称</th><th>文件</th></tr></thead><tbody>${books
              .map(
                (book) => `<tr><td>${esc(book.name)}</td><td class="muted">${esc(book.file_id)}</td></tr>`,
              )
              .join("")}</tbody></table>`
          : empty("酒馆里没有世界书。")
      }
      <h2>角色卡 <span class="count">${(characters || []).length}</span></h2>
      ${
        (characters || []).length
          ? `<table><thead><tr><th>名称</th><th>文件</th></tr></thead><tbody>${characters
              .map(
                (card) => `<tr><td>${esc(card.name)}</td><td class="muted">${esc(card.avatar)}</td></tr>`,
              )
              .join("")}</tbody></table>`
          : empty("酒馆里没有角色卡。")
      }`;
  });
};

views.settings = async (box) => {
  box.innerHTML = '<div class="loading">加载中…</div>';
  await guard(box, async () => {
    const state = await apiGet("panel/state");
    const backend = state.backend || {};
    box.innerHTML = `
      <h1>配置</h1>
      <div class="banner">
        这些是<b>当前生效</b>的值。要修改请到「插件管理 → 酒馆角色扮演 → 操作 → 插件配置」，
        改完重载插件即可。
      </div>
      <h2>后端</h2>
      <dl class="kv">
        <dt>类型</dt><dd>${esc(backend.type || "未设置")}</dd>
        <dt>模型</dt><dd>${esc(backend.provider_id || "（跟随当前会话）")}</dd>
        <dt>酒馆地址</dt><dd>${esc(backend.st_base_url || "（未配置）")}</dd>
        <dt>酒馆 Cookie</dt><dd class="${backend.st_cookie_set ? "ok" : "err"}">${
          backend.st_cookie_set ? "已设置" : "未设置"
        }</dd>
        <dt>校验 TLS</dt><dd>${backend.st_verify_ssl ? "开启" : "关闭"}</dd>
        <dt>上下文上限</dt><dd>${
          backend.max_context_tokens ? esc(backend.max_context_tokens) + " tokens" : "未限制"
        }</dd>
        <dt>回复预留</dt><dd>${esc(backend.reply_reserve_tokens)} tokens</dd>
      </dl>
      <h2>回退</h2>
      <dl class="kv">
        <dt>酒馆失败时回退</dt><dd>${backend.fallback_to_astrbot ? "开启" : "关闭"}</dd>
        <dt>回退提示</dt><dd>${
          backend.fallback_to_astrbot
            ? backend.fallback_notice
              ? "开启（推荐）"
              : '<span class="err">已关闭——模型会被静默换掉</span>'
            : "（回退未开启）"
        }</dd>
      </dl>
      <p class="muted">Cookie 只显示是否已配置，不会显示内容。</p>`;
  });
};

// ---------------------------------------------------------------------------
// shell
// ---------------------------------------------------------------------------

function select(name) {
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.setAttribute("aria-selected", String(button.dataset.view === name));
  });
  document.querySelectorAll(".view").forEach((section) => {
    section.hidden = section.id !== `view-${name}`;
  });
  const view = views[name];
  if (view) view(el(`view-${name}`));
}

async function boot() {
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => select(button.dataset.view));
  });

  if (!bridge) {
    el("conn").textContent = "桥接脚本未加载";
    el("conn").className = "conn err";
    el("view-overview").innerHTML =
      '<div class="banner err">没有找到 AstrBot 的页面桥接脚本，请从 AstrBot 面板里打开本页。</div>';
    return;
  }

  try {
    const context = await bridge.ready();
    el("conn").textContent = context?.username ? `已连接 · ${context.username}` : "已连接";
    el("conn").className = "conn ok";
    const version = await apiGet("panel/state").then((state) => state.plugin?.version || "");
    if (version) el("brand-version").textContent = `v${version}`;
  } catch (error) {
    el("conn").textContent = "桥接握手失败";
    el("conn").className = "conn err";
    console.error(error);
  }

  select("overview");
}

boot();
