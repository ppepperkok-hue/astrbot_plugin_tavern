"""Talk to the plugin through real AstrBot with a real model, over one connection.

    .tools\\uv-tools\\astrbot\\Scripts\\python.exe tools/qq_live_chat.py

`tools/qq_live.py` proves one turn arrives, but it reconnects per message, so the
conversation never continues. This keeps **one** OneBot websocket open for several
turns, which is what makes multi-turn behaviour testable: the plugin binds a card and
a chat branch to the session, and a new connection is a new session.

Why this exists at all: "does it work" for this plugin means the model answers *in
character, with world info injected, over more than one turn*. A one-shot test cannot
show that, and the mock-backend test cannot show a real model reading the card.

Every turn is asserted, so a silent failure (a turn that produced no reply) is a
failure rather than a gap. The AstrBot log tail is printed on any failure, because
the cause is almost always there and not in the client.
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import qq_e2e  # noqa: E402
import setup_provider  # noqa: E402

ROOT = qq_e2e.ROOT
DASHBOARD_PORT = 6200
ONEBOT_PORT = qq_e2e.ONEBOT_PORT
PROVIDER_MODEL = "deepseek_deepseek-flash"

#: What the user types, in order. The first turn gets the card greeting; the rest are
#: the conversation proper. The third turn refers back, so a model that lost the
#: history will visibly fail to connect it.
TURNS = (
    "where am I?",
    "is the lamp still turning?",
    "what did I just ask you?",
)

GREETING_MARKER = "lamp sweeps past your face"


def switch_to_real_model() -> None:
    plugin_config = ROOT / "data" / "config" / "astrbot_plugin_tavern_config.json"
    config = json.loads(plugin_config.read_text(encoding="utf-8-sig"))
    config["backend"]["type"] = "astrbot"
    config["backend"]["provider_id"] = PROVIDER_MODEL
    plugin_config.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"plugin backend -> astrbot/{PROVIDER_MODEL}")


def start_astrbot() -> subprocess.Popen:
    env = dict(__import__("os").environ)
    env["ASTRBOT_ROOT"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    command = [
        str(Path(sys.executable).parent / "astrbot.exe"),
        "run",
        "-p",
        str(DASHBOARD_PORT),
    ]
    log = ROOT / "astrbot-chat.log"
    handle = log.open("w", encoding="utf-8")
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


def text_of(payload: dict) -> str:
    return "".join(
        segment.get("data", {}).get("text", "")
        for segment in payload.get("message", [])
        if isinstance(segment, dict)
    )


async def converse(turns: tuple[str, ...], per_turn_timeout: float) -> list[list[str]]:
    """One connection, one message per turn, collecting the replies to each."""
    import websockets

    url = f"ws://127.0.0.1:{ONEBOT_PORT}/ws"
    headers = {
        "X-Self-ID": str(qq_e2e.BOT_QQ),
        "X-Client-Role": "Universal",
        "User-Agent": "OneBot/11 (mock-qq-chat)",
    }
    results: list[list[str]] = []

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
            for index, text in enumerate(turns, start=1):
                event = {
                    "post_type": "message",
                    "message_type": "group",
                    "sub_type": "normal",
                    "message_id": 100 + index,
                    "group_id": int(qq_e2e.GROUP_ID),
                    "user_id": int(qq_e2e.USER_QQ),
                    "self_id": int(qq_e2e.BOT_QQ),
                    "time": int(time.time()),
                    "sender": {
                        "user_id": int(qq_e2e.USER_QQ),
                        "nickname": "小明",
                        "role": "member",
                    },
                    "message": [
                        {"type": "at", "data": {"qq": str(qq_e2e.BOT_QQ)}},
                        {"type": "text", "data": {"text": f" {text}"}},
                    ],
                    "raw_message": f"[CQ:at,qq={qq_e2e.BOT_QQ}] {text}",
                    "font": 0,
                }
                print(f"\n--- turn {index}: {text!r}")
                await websocket.send(json.dumps(event))

                collected: list[str] = []
                deadline = time.time() + per_turn_timeout
                while time.time() < deadline:
                    try:
                        raw = await asyncio.wait_for(websocket.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        if collected:
                            break  # a quiet gap after a reply ends the turn
                        continue
                    payload = json.loads(raw)
                    if payload.get("action") == "send_group_msg":
                        reply = text_of(payload.get("params", {}))
                        collected.append(reply)
                        print(f"  bot: {reply!r}")
                        await websocket.send(
                            json.dumps(
                                {
                                    "status": "ok",
                                    "retcode": 0,
                                    "data": {"message_id": 200 + index},
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
                results.append(collected)
        finally:
            heart.cancel()
    return results


def main() -> int:
    qq_e2e.seed_astrbot_root()
    setup_provider.configure(
        (REPO_ROOT / ".tools" / "deepseek.key")
        .read_text(encoding="utf-8-sig")
        .strip()
        .lstrip("\ufeff"),
        "deepseek-flash",
        "https://api.deepseek.com/v1",
    )
    switch_to_real_model()
    process = start_astrbot()
    failures: list[str] = []
    try:
        if not wait_for_port(ONEBOT_PORT, timeout=150):
            print("FAIL: AstrBot never opened the OneBot websocket port")
            print(qq_e2e.log_tail(50))
            return 1
        print(f"OneBot reverse websocket listening on {ONEBOT_PORT}")

        results = asyncio.run(converse(TURNS, per_turn_timeout=180))

        print("\n=== summary ===")
        for index, (asked, replies) in enumerate(zip(TURNS, results, strict=True), start=1):
            model_replies = [text for text in replies if GREETING_MARKER not in text]
            print(f"turn {index}: asked {asked!r}")
            print(f"         replies={len(replies)} model_replies={len(model_replies)}")
            if not replies:
                failures.append(f"turn {index} produced no reply at all")
            elif index == 1 and not model_replies:
                # Expected, not a gap: the first turn of a fresh binding sends the
                # card's greeting and does *not* call the model. A test that demanded
                # a model answer here would be asserting against the design.
                print("         (card greeting only -- by design on the first turn)")
            elif not model_replies:
                failures.append(
                    f"turn {index} only produced the card greeting, never a model answer"
                )
            else:
                for text in model_replies:
                    print(f"         -> {text[:160]!r}")

        # Every turn after the greeting must have had a real model answer.
        if len(results) > 1 and not any(
            GREETING_MARKER not in text for replies in results[1:] for text in replies
        ):
            failures.append("no turn after the greeting produced a model answer")

        if failures:
            print("\nFAIL:")
            for item in failures:
                print(f"  - {item}")
            print("\n--- astrbot log tail ---")
            print(qq_e2e.log_tail(40))
            return 1
        print(f"\nLIVE {len(TURNS)}-TURN CHAT PASSED")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
