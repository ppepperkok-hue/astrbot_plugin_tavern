"""Download the archive GitHub serves for the repository and run the plugin from it.

    .tools\\uv-tools\\astrbot\\Scripts\\python.exe tools/gh_archive_check.py

The two release checks before this one still cut the archive with ``git archive`` -- one
from this checkout, one from a ``git clone``. Both are *local git* operations that
happen to agree with GitHub. This one uses no git at all: it fetches

    https://codeload.github.com/<owner>/<repo>/zip/refs/heads/main

which is the endpoint the GitHub "Download ZIP" button and AstrBot's installer both
resolve to, extracts it exactly as a user would (the archive has a single top-level
``<repo>-main/`` directory), and runs the plugin from the extracted tree. The only
inputs are the public URL and the AstrBot interpreter.

That leaves no assumption about this machine's git state, which matters here because
every previous "it is verified" claim in this project's history turned out to rest on
one.
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
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_URL = "https://github.com/ppepperkok-hue/astrbot_plugin_tavern"
ARCHIVE_URL = f"{REPO_URL.replace('github.com', 'codeload.github.com')}/zip/refs/heads/main"
PLUGIN_NAME = "astrbot_plugin_tavern"

BOT_QQ = "10000"
USER_QQ = "20000"
GROUP_ID = "123456789"
ONEBOT_PORT = 6399
DASHBOARD_PORT = 6400
COMMAND = "/tavern help"
EXPECTED = "酒馆角色扮演 · 指令"

WORK = REPO_ROOT / ".scratch" / "gh-archive"


def download() -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    out = WORK / "repo.zip"
    print(f"downloading {ARCHIVE_URL}")
    with urllib.request.urlopen(ARCHIVE_URL, timeout=120) as response:  # noqa: S310 - fixed https URL
        data = response.read()
    out.write_bytes(data)
    print(f"  {len(data):,} bytes")
    return out


def prepare_root(archive: Path) -> Path:
    """Extract as a user would, then install it as a plugin under a fresh root."""
    extract = WORK / "extracted"
    shutil.rmtree(extract, ignore_errors=True)
    extract.mkdir(parents=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(extract)

    top = [p for p in extract.iterdir() if p.is_dir()]
    if len(top) != 1:
        raise SystemExit(f"expected one top-level directory in the archive, found {len(top)}")
    source = top[0]
    print(f"extracted top-level directory: {source.name}")

    # A GitHub repo zip has no `.git`, which is exactly the installed state.
    if (source / ".git").exists():
        raise SystemExit("the downloaded archive unexpectedly contains .git")

    root = WORK / "astrbot-root"
    shutil.rmtree(root, ignore_errors=True)
    plugin_dir = root / "data" / "plugins" / PLUGIN_NAME
    plugin_dir.mkdir(parents=True)
    for item in source.iterdir():
        target = plugin_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
    (root / ".astrbot").write_text("", encoding="utf-8")
    (root / "data" / "plugin_data" / PLUGIN_NAME).mkdir(parents=True, exist_ok=True)
    print(f"installed into {plugin_dir}")

    from astrbot.core.config.default import DEFAULT_CONFIG

    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["log_level"] = "INFO"
    config["dashboard"]["enable"] = False
    config["wake_prefix"] = ["/"]
    config["plugin_set"] = ["*"]
    config["platform"] = [
        {
            "id": "gh-archive-check",
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
    command = [str(Path(sys.executable).parent / "astrbot.exe"), "run", "-p", str(DASHBOARD_PORT)]
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
        "User-Agent": "OneBot/11 (gh-archive-check)",
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
                        {"status": "ok", "retcode": 0, "data": {}, "echo": payload.get("echo")}
                    )
                )
    return "\n".join(chunks)


def log_text(root: Path) -> str:
    log = root / "astrbot.log"
    return log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""


def main() -> int:
    archive = download()
    root = prepare_root(archive)
    failures: list[str] = []
    process = start_astrbot(root)
    try:
        if not wait_for_port(ONEBOT_PORT, timeout=150):
            print("FAIL: AstrBot never opened the OneBot websocket port")
            print(log_text(root)[-3000:])
            return 1
        reply = asyncio.run(send_command())
        log = log_text(root)
        print(f"\n--- '{COMMAND}' answered ---\n  {reply[:300]!r}\n")
        for line in log.splitlines():
            if "astrbot_plugin_tavern" in line and "INFO" in line:
                print(f"  {line.strip()[:140]}")

        if "Loading plugin astrbot_plugin_tavern" not in log:
            failures.append("AstrBot never loaded the plugin from the downloaded archive")
        if EXPECTED not in reply:
            failures.append(f"{COMMAND} did not answer with {EXPECTED!r}")
        if "No module named" in log:
            failures.append("the archive is missing a module the plugin imports")
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()

    print()
    if failures:
        print("FAIL:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("PASS -- the archive GitHub serves installs and runs in a clean AstrBot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
