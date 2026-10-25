"""Contract tests for the management page against AstrBot 4.29.1's bridge.

AstrBot injects its bridge tag with a single ``str.replace("</body>", ..., 1)``
(``dashboard/services/plugin_page_service.py`` at v4.29.1).  It also prepends the
plugin name to every bridge endpoint and rejects ``?``/``#`` in that endpoint
(``dashboard/src/views/PluginViewPage.vue``).  These tests use the same mechanics so
a page that only looks correct in a browser is still caught here.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INDEX = REPO / "pages" / "panel" / "index.html"
APP = REPO / "pages" / "panel" / "app.js"

BRIDGE_TAG = '<script src="/api/plugin/page/bridge-sdk.js?asset_token=TOKEN"></script>'


def test_bridge_is_injected_before_the_real_body_close() -> None:
    html = INDEX.read_text(encoding="utf-8")
    rendered = html.replace("</body>", f"{BRIDGE_TAG}</body>", 1)

    comment_end = rendered.index("-->") + len("-->")
    bridge_at = rendered.index(BRIDGE_TAG)
    body_close = rendered.rindex("</body>")

    assert html.count("</body>") == 1, "only the real body close may be a literal"
    assert comment_end < bridge_at < body_close, (
        "AstrBot's first-</body> replacement put the bridge inside the comment"
    )
    assert rendered.count(BRIDGE_TAG) == 1


NODE_HARNESS = r"""
const fs = require("node:fs");
const vm = require("node:vm");

const appPath = process.argv[1];
let source = fs.readFileSync(appPath, "utf8");
source = source.replace(/\nboot\(\);\s*$/, "\n");

const calls = [];
const bridge = {
  ready: async () => ({ username: "tester" }),
  apiGet: async (endpoint, params) => {
    calls.push({ kind: "get", endpoint, params });
    return { ok: true, data: {} };
  },
  apiPost: async (endpoint, body) => {
    calls.push({ kind: "post", endpoint, body });
    return { ok: true };
  },
};
const element = () => ({
  textContent: "",
  className: "",
  innerHTML: "",
  hidden: false,
  addEventListener() {},
});
const sandbox = {
  window: {
    AstrBotPluginPage: bridge,
    location: { origin: "http://dashboard.test" },
    setTimeout,
    clearTimeout,
  },
  document: { getElementById: element, querySelectorAll: () => [] },
  URLSearchParams,
  CSS: { escape: (value) => value },
  console,
  setTimeout,
  clearTimeout,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.__apiGet = apiGet; this.__apiPost = apiPost;`, sandbox);

(async () => {
  await sandbox.__apiGet("panel/sessions", { scope: "aiocqhttp:GroupMessage:1" });
  await sandbox.__apiPost("panel/action", { action: "reload_library" });
  process.stdout.write(JSON.stringify(calls));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""


def test_bridge_calls_are_plugin_local_and_pass_params_separately() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    completed = subprocess.run(
        [node, "-e", NODE_HARNESS, str(APP)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    calls = json.loads(completed.stdout)
    assert calls[0] == {
        "kind": "get",
        "endpoint": "panel/sessions",
        "params": {"scope": "aiocqhttp:GroupMessage:1"},
    }
    assert calls[1]["endpoint"] == "panel/action"
    assert calls[1]["body"] == {"action": "reload_library"}

    # The same failure, stated as source invariants: the dashboard already inserts
    # PLUGIN_ID, and a query string in the endpoint is rejected before HTTP.
    source = APP.read_text(encoding="utf-8")
    assert not re.search(r"bridge\.api(?:Get|Post)\(`\$\{PLUGIN_ID\}/", source)
    assert "URLSearchParams" not in source
