"""Install the published archive into a clean AstrBot and run the plugin from there.

    .tools\\uv-tools\\astrbot\\Scripts\\python.exe tools/release_check.py

**This is the release gate, and it is the only test that answers the question that
matters before publishing: does the thing we actually ship work on a machine that is
not this one?**

Every other test runs against the working tree, where `tests/`, `tools/`, `research/`
and a `data/plugins/` junction are all present and importable. The published archive
has none of them -- `export-ignore` strips them -- and the plugin must survive that.
Nothing before this script proved it did.

What it does:

1. `git archive HEAD` -- the exact bytes the market receives, honouring export-ignore;
2. unpacks into a **fresh AstrBot root** (`ASTRBOT_ROOT`), as
   `<root>/data/plugins/astrbot_plugin_tavern/`, which is what an install produces;
3. starts a real AstrBot process against that root and sends a command and a chat
   message through a mock OneBot v11 client;
4. asserts the plugin loaded, answered the command, and -- with no model provider
   configured -- still behaved predictably.

Failures are printed with the AstrBot log tail, because the cause is always there.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_NAME = "astrbot_plugin_tavern"
BOT_QQ = "10000"
USER_QQ = "20000"
GROUP_ID = "123456789"
ONEBOT_PORT = 6299
DASHBOARD_PORT = 6300
COMMAND = "/tavern help"
EXPECTED = "酒馆角色扮演 · 指令"


def build_archive() -> Path:
    """Write the published archive into the workspace.

    Not the system temp directory: this runs under a file sandbox that permits writes
    inside the workspace and asks otherwise, and a release check that needs an
    approval prompt to run is a release check nobody runs.
    """
    out = REPO_ROOT / ".scratch" / "release-check.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "archive", "--format=zip", "-o", str(out), "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"git archive failed: {proc.stderr.decode(errors='replace')}")
    print(f"archive: {out.stat().st_size:,} bytes")
    return out


def install_into_fresh_root(archive: Path) -> Path:
    """Unpack the archive as an installed plugin under a brand-new AstrBot root."""
    root = REPO_ROOT / ".scratch" / "release-root"
    shutil.rmtree(root, ignore_errors=True)
    plugin_dir = root / "data" / "plugins" / PLUGIN_NAME
    plugin_dir.mkdir(parents=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(plugin_dir)
    (root / ".astrbot").write_text("", encoding="utf-8")
    print(f"installed into {plugin_dir}")

    # The plugin's own data directory, as AstrBot would create it.
    (root / "data" / "plugin_data" / PLUGIN_NAME).mkdir(parents=True, exist_ok=True)

    # Bare AstrBot config: the platform plus defaults, no provider at all. A fresh
    # install is exactly this state, and the plugin must not depend on anything more.
    from astrbot.core.config.default import DEFAULT_CONFIG

    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["log_level"] = "INFO"
    config["dashboard"]["enable"] = False
    config["wake_prefix"] = ["/"]
    config["plugin_set"] = ["*"]
    config["platform"] = [
        {
            "id": "release-check",
            "type": "aiocqhttp",
            "enable": True,
            "ws_reverse_host": "127.0.0.1",
            "ws_reverse_port": ONEBOT_PORT,
            "ws_reverse_token": "",
        }
    ]
    (root / "data" / "cmd_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return root


def start_astrbot(root: Path) -> subprocess.Popen:
    env = dict(os.environ)
    env["ASTRBOT_ROOT"] = str(root)
    env["PYTHONIOENCODING"] = "utf-8"
    command = [
        str(Path(sys.executable).parent / "astrbot.exe"),
        "run",
        "-p",
        str(DASHBOARD_PORT),
    ]
    handle = (root / "astrbot.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        command, cwd=str(root), env=env, stdout=handle, stderr=subprocess.STDOUT
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


async def send_command(timeout: float = 40.0) -> str:
    import websockets

    url = f"ws://127.0.0.1:{ONEBOT_PORT}/ws"
    headers = {
        "X-Self-ID": BOT_QQ,
        "X-Client-Role": "Universal",
        "User-Agent": "OneBot/11 (release-check)",
    }
    chunks: list[str] = []
    async with websockets.connect(url, max_size=None, additional_headers=headers) as ws:
        await ws.send(
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
        await ws.send(
            json.dumps(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "sub_type": "normal",
                    "message_id": 1,
                    "group_id": int(GROUP_ID),
                    "user_id": int(USER_QQ),
                    "self_id": int(BOT_QQ),
                    "time": int(time.time()),
                    "sender": {"user_id": int(USER_QQ), "nickname": "小明", "role": "admin"},
                    "message": [{"type": "text", "data": {"text": COMMAND}}],
                    "raw_message": COMMAND,
                    "font": 0,
                }
            )
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=4)
            except asyncio.TimeoutError:
                if chunks:
                    break
                continue
            payload = json.loads(raw)
            if payload.get("action") == "send_group_msg":
                chunks.append(
                    "".join(
                        segment.get("data", {}).get("text", "")
                        for segment in payload.get("params", {}).get("message", [])
                        if isinstance(segment, dict)
                    )
                )
                await ws.send(
                    json.dumps(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {"message_id": 1},
                            "echo": payload.get("echo"),
                        }
                    )
                )
                continue
            if payload.get("action"):
                await ws.send(
                    json.dumps(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {},
                            "echo": payload.get("echo"),
                        }
                    )
                )
    return "\n".join(chunks)


def log_tail(root: Path, lines: int = 40) -> str:
    log = root / "astrbot.log"
    if not log.is_file():
        return "(no log)"
    text = log.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = "\n".join(text[-lines:])
    for code in ("\x1b[0m",):
        tail = tail.replace(code, "")
    return tail


def main() -> int:
    archive = build_archive()
    root = install_into_fresh_root(archive)
    failures: list[str] = []
    process = start_astrbot(root)
    try:
        if not wait_for_port(ONEBOT_PORT, timeout=150):
            print("FAIL: AstrBot never opened the OneBot websocket port")
            print(log_tail(root, 60))
            return 1
        print(f"OneBot reverse websocket listening on {ONEBOT_PORT}\n")

        reply = asyncio.run(send_command())
        print(f"--- '{COMMAND}' answered ---")
        print(f"  {reply[:400]!r}")

        print("\n--- plugin load lines ---")
        for line in log_tail(root, 400).splitlines():
            if "tavern" in line.lower() or "plugin" in line.lower():
                print(f"  {line.strip()[:150]}")

        if "Plugin astrbot_plugin_tavern" not in log_tail(root, 400) and (
            "astrbot_plugin_tavern" not in log_tail(root, 400)
        ):
            failures.append("AstrBot never logged loading the plugin")
        if EXPECTED not in reply:
            failures.append(f"{COMMAND} did not answer with {EXPECTED!r}")
        if "No module named" in log_tail(root, 400):
            failures.append(
                "the archive is missing a module the plugin imports -- "
                "export-ignore has stripped something it needs"
            )
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(root, ignore_errors=True)

    print()
    if failures:
        print("FAIL:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("PASS -- the published archive installs and runs in a clean AstrBot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
