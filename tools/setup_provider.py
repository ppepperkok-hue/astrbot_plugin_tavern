"""Configure DeepSeek as the model provider for the isolated AstrBot root.

This is *local test configuration only*: the API key is written to
``.tools/e2e-root/data/cmd_config.json``, which is git-ignored, and never to a
tracked file.

Usage:
    python tools/setup_provider.py --check     # only verify the key/model listing
    python tools/setup_provider.py             # write the AstrBot config
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT = REPO_ROOT / ".tools" / "e2e-root"
CONFIG = ROOT / "data" / "cmd_config.json"
PLUGIN_CONFIG = ROOT / "data" / "config" / "astrbot_plugin_tavern_config.json"

PROVIDER_ID = "deepseek"
DEFAULT_API_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


def check_key(api_key: str, api_base: str = DEFAULT_API_BASE) -> int:
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        response = httpx.get(f"{api_base}/models", headers=headers, timeout=30)
    except Exception as exc:  # noqa: BLE001 - report and stop
        print(f"FAIL: cannot reach {api_base}: {exc}")
        return 1
    print(f"GET {api_base}/models -> HTTP {response.status_code}")
    if response.status_code != 200:
        print(response.text[:300])
        return 1
    models = [item.get("id") for item in response.json().get("data", [])]
    print("models:", ", ".join(models) or "(none listed)")
    return 0


def configure(api_key: str, model: str, api_base: str) -> int:
    if not CONFIG.is_file():
        print(f"FAIL: {CONFIG} not found; run tools/qq_e2e.py once to seed the root")
        return 1

    config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))

    source = {
        "id": PROVIDER_ID,
        "provider": "openai",
        "type": "openai_chat_completion",
        "provider_type": "chat_completion",
        "enable": True,
        "key": [api_key],
        "api_base": api_base,
        "timeout": 120,
        "proxy": "",
        "custom_headers": {},
        "model_config": {"model": model},
    }
    sources = [item for item in config.get("provider_sources", []) if item.get("id") != PROVIDER_ID]
    sources.append(source)
    config["provider_sources"] = sources

    models = [
        item for item in config.get("provider", []) if item.get("id") != f"{PROVIDER_ID}_{model}"
    ]
    models.append(
        {
            "id": f"{PROVIDER_ID}_{model}",
            "provider_source_id": PROVIDER_ID,
            "model": model,
            "enable": True,
            "provider_type": "chat_completion",
        }
    )
    config["provider"] = models

    settings = config.setdefault("provider_settings", {})
    settings["default_provider_id"] = f"{PROVIDER_ID}_{model}"
    settings["default_provider_model"] = model
    config["log_level"] = "INFO"

    CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote provider source '{PROVIDER_ID}' and model '{PROVIDER_ID}_{model}' into {CONFIG}")

    if PLUGIN_CONFIG.is_file():
        plugin_config = json.loads(PLUGIN_CONFIG.read_text(encoding="utf-8-sig"))
        plugin_config.setdefault("backend", {})
        plugin_config["backend"]["type"] = "astrbot"
        plugin_config["backend"]["provider_id"] = f"{PROVIDER_ID}_{model}"
        plugin_config["debug"]["log_prompt"] = False
        PLUGIN_CONFIG.write_text(
            json.dumps(plugin_config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"plugin backend switched to astrbot/{PROVIDER_ID}_{model} in {PLUGIN_CONFIG}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--key", default="", help="DeepSeek API key (otherwise read from --key-file)"
    )
    parser.add_argument("--key-file", default=".tools/deepseek.key", help="file holding the key")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--check", action="store_true", help="only verify the key")
    args = parser.parse_args()

    api_key = args.key.strip().lstrip("\ufeff")
    key_file = REPO_ROOT / args.key_file
    if not api_key and key_file.is_file():
        # tolerate a BOM: Windows editors like to add one
        api_key = key_file.read_text(encoding="utf-8-sig").strip().lstrip("\ufeff")
    if not api_key:
        print("FAIL: no API key given (--key or --key-file)")
        return 1

    status = check_key(api_key, args.api_base)
    if status != 0 or args.check:
        return status
    return configure(api_key, args.model, args.api_base)


if __name__ == "__main__":
    raise SystemExit(main())
