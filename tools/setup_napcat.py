"""Download the latest NapCat Shell (OneBot v11 QQ client) into .tools/napcat.

Uses ``httpx`` because the Windows PowerShell/curl TLS stack is broken in this
sandbox. Pass ``--list`` to only show the release assets.
"""

from __future__ import annotations

import argparse
import io
import subprocess
import zipfile
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / ".tools"
TARGET = TOOLS / "napcat"
API = "https://api.github.com/repos/NapNeko/NapCatQQ/releases/latest"


def _github_token() -> str:
    """Reuse the ``gh`` CLI login: the anonymous API is rate limited here."""
    try:
        completed = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="only list the release assets")
    parser.add_argument("--asset", default="", help="asset name substring (default: auto)")
    args = parser.parse_args()

    headers = {"Accept": "application/vnd.github+json"}
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        print("warning: no gh token found, the API may be rate limited")

    with httpx.Client(timeout=60, follow_redirects=True, headers=headers) as client:
        response = client.get(API)
        response.raise_for_status()
        release = response.json()
        print(f"release: {release['tag_name']}")
        assets = release["assets"]
        for asset in assets:
            print(f"  {asset['name']}  ({asset['size'] / 1048576:.1f} MB)")
        if args.list:
            return 0

        wanted = args.asset.lower()
        candidates = [
            asset
            for asset in assets
            if asset["name"].lower().endswith(".zip")
            and "shell" in asset["name"].lower()
            and ("win" in asset["name"].lower())
        ]
        if wanted:
            candidates = [a for a in assets if wanted in a["name"].lower()] or candidates
        if not candidates:
            print("no matching Windows Shell zip found; pass --asset <substring>")
            return 1

        asset = max(candidates, key=lambda a: a["size"])
        print(f"downloading {asset['name']} ...")
        TARGET.mkdir(parents=True, exist_ok=True)
        blob = client.get(asset["browser_download_url"], timeout=600).content
        print(f"downloaded {len(blob) / 1048576:.1f} MB")

    archive = zipfile.ZipFile(io.BytesIO(blob))
    archive.extractall(TARGET)
    print(f"extracted to {TARGET}")
    for path in sorted(TARGET.iterdir())[:20]:
        print(f"  {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
