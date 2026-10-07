"""Drive `/tavern` commands through real AstrBot's command dispatch.

    .tools\\uv-tools\\astrbot\\Scripts\\python.exe tools/qq_commands_live.py

Why this is separate from the chat test, and why it exists at all:

`tools/astrbot_e2e.py` calls `on_message` directly, so it never exercises AstrBot's
command *filter* -- which is where the arguments are parsed, and where every
parameterised `/tavern` subcommand was silently broken (see `docs/known-issues.md`
entry 2). `tools/verify_cmd_params.py` checks the parsing contract against the
installed `CommandFilter`, but it does not prove a real message reaches a real
handler with real arguments. This does.

It needs no model: every command here answers from local state. That is deliberate --
the point is dispatch, not generation, so a failure means dispatch.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import qq_e2e  # noqa: E402

ROOT = qq_e2e.ROOT
DASHBOARD_PORT = 6200
ONEBOT_PORT = qq_e2e.ONEBOT_PORT

#: ``(command typed, a substring that must appear in the reply)``.
#:
#: The expectations are written to fail if dispatch drops arguments, which is the
#: specific bug this covers: `/tavern worldbook effect` with its arguments lost would
#: answer the usage text instead of naming the entry.
#:
#: Two of them assert the *failure* paths on purpose, because those are answers too:
#: `history` on a fresh binding shows the card greeting rather than a branch list, and
#: `st cards` reports that it cannot reach a tavern -- this run starts no mock tavern,
#: so reaching it would be the bug.
COMMANDS: tuple[tuple[str, str], ...] = (
    ("/tavern help", "酒馆角色扮演 · 指令"),
    ("/tavern list", "角色卡列表"),
    ("/tavern status", "酒馆状态"),
    ("/tavern use Iris", "已切换为「Iris」"),
    ("/tavern card", "角色卡：Iris"),
    ("/tavern history 3", "Iris"),
    ("/tavern worldbook list", "世界书列表"),
    ("/tavern worldbook on Lighthouse Lore", "世界书已开启"),
    ("/tavern reload", "已重载"),
    ("/tavern st cards", "无法连接酒馆"),
)


def start_astrbot() -> subprocess.Popen:
    env = dict(os.environ)
    env["ASTRBOT_ROOT"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    command = [str(Path(sys.executable).parent / "astrbot.exe"), "run", "-p", str(DASHBOARD_PORT)]
    handle = (ROOT / "astrbot-commands.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        command, cwd=str(ROOT), env=env, stdout=handle, stderr=subprocess.STDOUT
    )
    print(f"started AstrBot: {' '.join(command)} (pid {process.pid})")
    return process


def wait_for_port(port: int, timeout: float = 150.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(1.0)
    return False


async def run_commands(per_command_timeout: float = 45.0) -> list[tuple[str, str]]:
    """Send each command over one connection and collect its reply."""
    import websockets

    url = f"ws://127.0.0.1:{ONEBOT_PORT}/ws"
    headers = {
        "X-Self-ID": str(qq_e2e.BOT_QQ),
        "X-Client-Role": "Universal",
        "User-Agent": "OneBot/11 (mock-qq-commands)",
    }
    out: list[tuple[str, str]] = []

    async with websockets.connect(url, max_size=None, additional_headers=headers) as websocket:
        await websocket.send(
            json.dumps(
                {
                    "post_type": "meta_event",
                    "meta_event_type": "lifecycle",
                    "sub_type": "connect",
                    "time": int(time.time()),
                    "self_id": int(qq_e2e.BOT_QQ),
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
                            "self_id": int(qq_e2e.BOT_QQ),
                            "status": {"online": True, "good": True},
                            "interval": 5000,
                        }
                    )
                )

        heart = asyncio.ensure_future(heartbeat())
        try:
            for index, (command, _expect) in enumerate(COMMANDS, start=1):
                event = {
                    "post_type": "message",
                    "message_type": "group",
                    "sub_type": "normal",
                    "message_id": 500 + index,
                    "group_id": int(qq_e2e.GROUP_ID),
                    "user_id": int(qq_e2e.USER_QQ),
                    "self_id": int(qq_e2e.BOT_QQ),
                    "time": int(time.time()),
                    "sender": {
                        "user_id": int(qq_e2e.USER_QQ),
                        "nickname": "小明",
                        "role": "admin",
                    },
                    "message": [{"type": "text", "data": {"text": command}}],
                    "raw_message": command,
                    "font": 0,
                }
                await websocket.send(json.dumps(event))

                chunks: list[str] = []
                deadline = time.time() + per_command_timeout
                while time.time() < deadline:
                    try:
                        raw = await asyncio.wait_for(websocket.recv(), timeout=4)
                    except asyncio.TimeoutError:
                        if chunks:
                            break
                        continue
                    payload = json.loads(raw)
                    if payload.get("action") == "send_group_msg":
                        params = payload.get("params", {})
                        chunks.append(
                            "".join(
                                segment.get("data", {}).get("text", "")
                                for segment in params.get("message", [])
                                if isinstance(segment, dict)
                            )
                        )
                        await websocket.send(
                            json.dumps(
                                {
                                    "status": "ok",
                                    "retcode": 0,
                                    "data": {"message_id": 600 + index},
                                    "echo": payload.get("echo"),
                                }
                            )
                        )
                        continue
                    if payload.get("action"):
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
                out.append((command, "\n".join(chunks)))
        finally:
            heart.cancel()
    return out


def main() -> int:
    qq_e2e.seed_astrbot_root()
    # Call the plugin's real command surface: no model needed, so no provider is set.
    process = start_astrbot()
    failures: list[str] = []
    try:
        if not wait_for_port(ONEBOT_PORT, timeout=150):
            print("FAIL: AstrBot never opened the OneBot websocket port")
            print(qq_e2e.log_tail(50))
            return 1
        print(f"OneBot reverse websocket listening on {ONEBOT_PORT}\n")

        results = asyncio.run(run_commands())

        print("=== command dispatch ===")
        for (command, expected), (_, reply) in zip(COMMANDS, results, strict=True):
            ok = expected in reply
            print(f"  {'OK  ' if ok else 'FAIL'} {command}")
            print(f"        expected substring: {expected!r}")
            if not ok:
                print(f"        got: {reply[:220]!r}")
                failures.append(f"{command} did not answer with {expected!r}")
            elif reply.strip():
                first = reply.strip().splitlines()[0]
                print(f"        -> {first[:120]}")

        if failures:
            print("\nFAIL:")
            for item in failures:
                print(f"  - {item}")
            print("\n--- astrbot log tail ---")
            print(qq_e2e.log_tail(40))
            return 1
        print(f"\nCOMMAND DISPATCH PASSED ({len(COMMANDS)} commands)")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
