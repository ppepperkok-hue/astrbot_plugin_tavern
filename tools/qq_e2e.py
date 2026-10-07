"""End-to-end test with a real AstrBot process and a mock OneBot v11 client.

What it proves: the whole chain a QQ user would hit, without a real QQ account.

    mock OneBot client --ws(reverse)--> AstrBot aiocqhttp adapter
        -> /tavern plugin -> mock SillyTavern HTTP backend -> reply
        -> send_group_msg action back to the mock client

Isolation: the AstrBot root is a scratch directory (``ASTRBOT_ROOT``), the
plugin is linked from this checkout, and the generation backend is the plugin's
own SillyTavern backend pointed at a local mock endpoint, so no model is called.

Run it with the AstrBot interpreter:
    E:\\astrbot_plugin\\.tools\\uv-tools\\astrbot\\Scripts\\python.exe tools\\qq_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PLUGIN_NAME = "astrbot_plugin_tavern"
ROOT = REPO_ROOT / ".tools" / "e2e-root"
BOT_QQ = "10000"
USER_QQ = "20000"
GROUP_ID = "123456789"
MOCK_PORT = 18080
ONEBOT_PORT = 6199
DASHBOARD_PORT = 6200
REPLY_TEXT = "「海雾散了些。」她抬手，将灯芯捻亮。"


# ----------------------------------------------------------------------
# mock SillyTavern backend (the plugin's ``sillytavern`` backend talks to it)
# ----------------------------------------------------------------------
class MockBackendHandler(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path == "/csrf-token":
            self._json({"token": "mock-csrf"})
        else:
            self._json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).calls.append(body)
        if self.path == "/api/backends/chat-completions/generate":
            self._json(
                {
                    "id": "mock",
                    "object": "chat.completion",
                    "model": "mock-tavern",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": REPLY_TEXT},
                        }
                    ],
                    "usage": {"total_tokens": 42},
                }
            )
        else:
            self._json({"error": "not found"}, status=404)

    def _json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args) -> None:  # silence the server log
        return


def start_mock_backend() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), MockBackendHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ----------------------------------------------------------------------
# scratch AstrBot root
# ----------------------------------------------------------------------
def seed_astrbot_root() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT, ignore_errors=True)
    data = ROOT / "data"
    (data / "config").mkdir(parents=True, exist_ok=True)
    # ``check_astrbot_root`` requires this marker file.
    (ROOT / ".astrbot").write_text("", encoding="utf-8")

    from astrbot.core.config.default import DEFAULT_CONFIG

    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["log_level"] = "INFO"
    config["dashboard"]["enable"] = False
    config["wake_prefix"] = ["/"]
    config["plugin_set"] = ["*"]
    config["platform"] = [
        {
            "id": "napcat-e2e",
            "type": "aiocqhttp",
            "enable": True,
            "ws_reverse_host": "127.0.0.1",
            "ws_reverse_port": ONEBOT_PORT,
            "ws_reverse_token": "",
        }
    ]
    (data / "cmd_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    plugin_config = {
        "enabled": True,
        "trigger": {
            "private_always": True,
            "group_at_only": True,
            "wake_prefixes": ["酒馆"],
            "cooldown_seconds": 0,
            "max_concurrent": 2,
        },
        "worldbook": {"enabled": True, "scan_depth": 4, "token_budget": 1024},
        "render": {"max_chars_per_message": 500, "segment_delay_ms": 0},
        "backend": {
            "type": "sillytavern",
            "st_base_url": f"http://127.0.0.1:{MOCK_PORT}",
            "st_cookie": "",
            "st_verify_ssl": False,
        },
        "permissions": {"switch_card_requires_admin": False, "import_requires_admin": False},
        "debug": {"log_prompt": True, "show_debug_in_chat": True},
    }
    (data / "config" / f"{PLUGIN_NAME}_config.json").write_text(
        json.dumps(plugin_config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    plugins = data / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    link = plugins / PLUGIN_NAME
    if link.exists():
        shutil.rmtree(link, ignore_errors=True)
    # A junction keeps the checkout as the single source of truth.
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(REPO_ROOT)],
        check=True,
        capture_output=True,
    )

    # seed the plugin library: one card, one world book, one bound session
    plugin_data = data / "plugin_data" / PLUGIN_NAME
    (plugin_data / "cards").mkdir(parents=True, exist_ok=True)
    (plugin_data / "worldbooks").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        REPO_ROOT / "tests" / "fixtures" / "cards" / "iris.json",
        plugin_data / "cards" / "iris.json",
    )
    shutil.copyfile(
        REPO_ROOT / "tests" / "fixtures" / "worldbooks" / "lighthouse.json",
        plugin_data / "worldbooks" / "lighthouse.json",
    )
    scope = f"aiocqhttp:GroupMessage:{GROUP_ID}"
    (plugin_data / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "bindings": {
                    scope: {
                        "scope": scope,
                        "card_id": "Iris",
                        "card_name": "Iris",
                        "worldbooks": ["Lighthouse Lore"],
                        "chat_name": "main",
                        "greeting_sent": True,
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"seeded AstrBot root at {ROOT}")


# ----------------------------------------------------------------------
# AstrBot process
# ----------------------------------------------------------------------
def start_astrbot() -> subprocess.Popen:
    env = dict(os.environ)
    env["ASTRBOT_ROOT"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    # The console script next to the interpreter is the supported entry point
    # (the ``astrbot`` package has no ``__main__``).
    candidate = Path(sys.executable).parent / ("astrbot.exe" if os.name == "nt" else "astrbot")
    if candidate.is_file():
        command = [str(candidate), "run", "-p", str(DASHBOARD_PORT)]
    else:
        command = [sys.executable, "-m", "astrbot.cli", "run", "-p", str(DASHBOARD_PORT)]
    log = ROOT / "astrbot.log"
    handle = log.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    print(f"started AstrBot: {' '.join(command)} (pid {process.pid}), log: {log}")
    return process


def wait_for_port(port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(1.0)
    return False


def log_tail(lines: int = 60) -> str:
    log = ROOT / "astrbot.log"
    if not log.is_file():
        return "(no log)"
    content = log.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])


# ----------------------------------------------------------------------
# mock QQ client (OneBot v11 over the reverse websocket)
# ----------------------------------------------------------------------
async def run_onebot_client(
    timeout: float = 60.0,
    *,
    expect_text: str | None = REPLY_TEXT,
    ignore_texts: tuple[str, ...] = (),
) -> tuple[bool, list[dict]]:
    """Drive one group message and collect the replies.

    ``expect_text=None`` accepts any non-ignored answer, which is what the live
    model test needs (a real model never repeats a fixed string).
    """
    import websockets

    url = f"ws://127.0.0.1:{ONEBOT_PORT}/ws"
    # aiocqhttp reads the OneBot reverse-websocket handshake headers:
    # X-Self-ID identifies the bot account and X-Client-Role selects the mode.
    headers = {
        "X-Self-ID": str(BOT_QQ),
        "X-Client-Role": "Universal",
        "User-Agent": "OneBot/11 (mock-qq-e2e)",
    }
    replies: list[dict] = []
    print(f"connecting mock QQ client to {url}")
    async with websockets.connect(url, max_size=None, additional_headers=headers) as websocket:
        await websocket.send(
            json.dumps(
                {
                    "post_type": "meta_event",
                    "meta_event_type": "lifecycle",
                    "sub_type": "connect",
                    "time": int(time.time()),
                    "self_id": int(BOT_QQ),
                }
            )
        )
        await websocket.send(
            json.dumps(
                {
                    "post_type": "meta_event",
                    "meta_event_type": "heartbeat",
                    "time": int(time.time()),
                    "self_id": int(BOT_QQ),
                    "status": {"online": True, "good": True},
                    "interval": 5000,
                }
            )
        )

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(5)
                await websocket.send(
                    json.dumps(
                        {
                            "post_type": "meta_event",
                            "meta_event_type": "heartbeat",
                            "time": int(time.time()),
                            "self_id": int(BOT_QQ),
                            "status": {"online": True, "good": True},
                            "interval": 5000,
                        }
                    )
                )

        heart = asyncio.create_task(heartbeat())

        # give AstrBot a moment to run the adapter connect logic
        await asyncio.sleep(6)

        message = [
            {"type": "at", "data": {"qq": str(BOT_QQ)}},
            {"type": "text", "data": {"text": " the lighthouse showed up"}},
        ]
        event = {
            "post_type": "message",
            "message_type": "group",
            "sub_type": "normal",
            "message_id": 1001,
            "group_id": int(GROUP_ID),
            "user_id": int(USER_QQ),
            "self_id": int(BOT_QQ),
            "time": int(time.time()),
            "sender": {"user_id": int(USER_QQ), "nickname": "小明", "role": "member"},
            "message": message,
            "raw_message": f"[CQ:at,qq={BOT_QQ}] the lighthouse showed up",
            "font": 0,
        }
        print("sending group message: @bot the lighthouse showed up")
        await websocket.send(json.dumps(event))

        deadline = time.time() + timeout
        matched = False
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=5)
            except asyncio.TimeoutError:
                print("  ... waiting for a bot action")
                continue
            payload = json.loads(raw)
            print(f"  <- {json.dumps(payload, ensure_ascii=False)[:200]}")
            if payload.get("action") == "send_group_msg":
                params = payload.get("params", {})
                replies.append(params)
                text = "".join(
                    segment.get("data", {}).get("text", "")
                    for segment in params.get("message", [])
                    if isinstance(segment, dict)
                )
                print(f"bot replied: {text!r}")
                await websocket.send(
                    json.dumps(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {"message_id": 1},
                            "echo": payload.get("echo"),
                        }
                    )
                )
                if expect_text is None:
                    if text.strip() and text not in ignore_texts:
                        matched = True
                        break
                    continue
                if expect_text in text:
                    matched = True
                    break
                continue
            if payload.get("action"):
                # answer any other API call so AstrBot does not block
                await websocket.send(
                    json.dumps(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {},
                            "echo": payload.get("echo"),
                        }
                    )
                )

        heart.cancel()
        if not replies:
            print("FAIL: no send_group_msg action received")
            return False, replies
        if not matched:
            texts = [
                "".join(
                    segment.get("data", {}).get("text", "")
                    for segment in reply.get("message", [])
                    if isinstance(segment, dict)
                )
                for reply in replies
            ]
            print(f"FAIL: the model reply never arrived, got: {texts}")
            return False, replies
        return True, replies


def main() -> int:
    from astrbot.core.utils.astrbot_path import get_astrbot_config_path  # noqa: F401

    server = start_mock_backend()
    print(f"mock SillyTavern backend on http://127.0.0.1:{MOCK_PORT}")
    seed_astrbot_root()
    process = start_astrbot()
    try:
        if not wait_for_port(ONEBOT_PORT, timeout=120):
            print("FAIL: AstrBot did not open the OneBot websocket port")
            print(log_tail())
            return 1
        print(f"OneBot reverse websocket is listening on {ONEBOT_PORT}")
        ok, _replies = asyncio.run(run_onebot_client())
        print("--- astrbot log tail ---")
        print(log_tail(40))
        print(f"backend calls: {len(MockBackendHandler.calls)}")
        if MockBackendHandler.calls:
            payload = MockBackendHandler.calls[0]
            messages = payload.get("messages", [])
            systems = [m for m in messages if m.get("role") == "system"]
            joined = json.dumps(systems, ensure_ascii=False)
            if "44 metres tall" not in joined:
                print("FAIL: world info never reached the generation backend")
                return 1
            print(f"world info present in the system messages ({len(systems)} system blocks)")
        print("QQ E2E PASSED" if ok else "QQ E2E FAILED")
        return 0 if ok else 1
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
        server.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
