"""End-to-end test with a real model: fake OneBot client -> AstrBot -> DeepSeek.

Reuses the seeded AstrBot root from ``tools/qq_e2e.py`` (which writes the mock
SillyTavern backend config), switches the plugin backend to AstrBot's DeepSeek
provider, restarts AstrBot, and sends one group message through a mock OneBot
v11 client. The assertion is deliberately loose: we only require that a real
answer came back, so the test is about the pipeline, not about prose.

Run with the AstrBot interpreter:
    E:\\astrbot_plugin\\.tools\\uv-tools\\astrbot\\Scripts\\python.exe tools/qq_live.py
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

import qq_e2e  # noqa: E402  (same directory; reuses the mock client and seeding)
import setup_provider  # noqa: E402

ROOT = qq_e2e.ROOT
DASHBOARD_PORT = 6200
ONEBOT_PORT = qq_e2e.ONEBOT_PORT
PROVIDER_MODEL = "deepseek_deepseek-flash"


def switch_to_real_model() -> None:
    plugin_config = ROOT / "data" / "config" / "astrbot_plugin_tavern_config.json"
    config = json.loads(plugin_config.read_text(encoding="utf-8-sig"))
    config["backend"]["type"] = "astrbot"
    config["backend"]["provider_id"] = PROVIDER_MODEL
    plugin_config.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"plugin backend -> astrbot/{PROVIDER_MODEL}")


def start_astrbot() -> subprocess.Popen:
    env = dict(os.environ)
    env["ASTRBOT_ROOT"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    command = [
        str(Path(sys.executable).parent / "astrbot.exe"),
        "run",
        "-p",
        str(DASHBOARD_PORT),
    ]
    log = ROOT / "astrbot-live.log"
    handle = log.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command, cwd=str(ROOT), env=env, stdout=handle, stderr=subprocess.STDOUT
    )
    print(f"started AstrBot: {' '.join(command)} (pid {process.pid})")
    return process


def wait_for_port(port: int, timeout: float = 120.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(1.0)
    return False


def main() -> int:
    # 1. seed the root exactly like the mock test does, then swap in the real model
    qq_e2e.seed_astrbot_root()
    setup_provider.configure(
        Path(REPO_ROOT / ".tools" / "deepseek.key")
        .read_text(encoding="utf-8-sig")
        .strip()
        .lstrip("\ufeff"),
        "deepseek-flash",
        "https://api.deepseek.com/v1",
    )

    qq_e2e.start_mock_backend()  # keep it listening: harmless, and proves fallbacks
    process = start_astrbot()
    try:
        if not wait_for_port(ONEBOT_PORT, timeout=150):
            print("FAIL: AstrBot never opened the OneBot websocket port")
            print(qq_e2e.log_tail(40))
            return 1
        print(f"OneBot reverse websocket listening on {ONEBOT_PORT}")

        # a live model needs more time than the mock backend
        matched, replies = asyncio.run(
            qq_e2e.run_onebot_client(
                timeout=180,
                expect_text=None,
                ignore_texts=("The lamp sweeps past your face",),
            )
        )
        print(f"--- replies ({len(replies)}) ---")
        answers: list[str] = []
        for reply in replies:
            text = "".join(
                segment.get("data", {}).get("text", "")
                for segment in reply.get("message", [])
                if isinstance(segment, dict)
            )
            answers.append(text)
            print(f"  | {text}")
        model_answers = [text for text in answers if "lamp sweeps past" not in text]
        if not matched or not model_answers:
            print("FAIL: no live model answer arrived")
            print(qq_e2e.log_tail(30))
            return 1
        print(
            f"live model answered ({len(model_answers)} message(s), first: {model_answers[0][:80]!r})"
        )
        print("LIVE MODEL E2E PASSED")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
        print("--- astrbot log tail ---")
        print(qq_e2e.log_tail(25))


if __name__ == "__main__":
    raise SystemExit(main())
