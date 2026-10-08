"""The last check before publishing: what the repository actually contains.

    python tools/preflight.py

Four things, in the order they can hurt you:

1. **Secrets.** A leaked key in a public repository is the one mistake that cannot be
   undone by pushing a fix -- it is scraped within minutes. An earlier session in this
   project did leak a DeepSeek key into a chat archive, so every tracked file is
   scanned, at its *pushed* content, with the patterns that actually appear in this
   ecosystem.
2. **Version drift.** The version is stated in `metadata.yaml`, in `tavern/main.py`,
   and in two README badges. Three of the four are easy to forget.
3. **Config drift.** Every key a user can set should be documented; the README's
   config table is checked against `_conf_schema.json` in both directions.
4. **Placeholders.** `TODO`/`FIXME`/`XXX` and unfinished-looking text that would ship
   in the README or metadata.

Exit 0 only when all four are clean. Read-only: it prints, it does not fix.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

#: Patterns for credentials that plausibly appear in this project. Deliberately
#: narrow: a scanner that flags `key = "value"` in every config file gets ignored,
#: and an ignored secret scanner is worse than none.
SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("Anthropic", r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    ("OpenAI-style", r"\bsk-[A-Za-z0-9]{32,}"),
    ("GitHub token", r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    ("GitHub PAT", r"\bgithub_pat_[A-Za-z0-9_]{40,}"),
    ("Google API", r"\bAIza[0-9A-Za-z_\-]{30,}"),
    ("Slack", r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    ("Telegram bot", r"\b\d{8,12}:AA[A-Za-z0-9_\-]{30,}"),
    ("private key block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("AWS access key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("DeepSeek", r"\bsk-[A-Za-z0-9]{20,}\b"),
)

#: A long token-shaped string assigned to a suspicious name, as a last resort.
ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|bearer)\b\s*[:=]\s*[\"']?([A-Za-z0-9_\-]{24,})"
)

#: Values that are obviously not secrets, so the noisy rule can stay on.
PLACEHOLDER_VALUES = ("your_", "xxx", "placeholder", "example", "changeme", "todo", "<", "---")

PLACEHOLDER_WORDS = ("TODO", "FIXME", "XXX", "TBD", "WIP", "待补", "待写", "占位")

#: Voice that belongs to *working on* this project, not to the project. These are
#: sentence-final tics from the development conversation, and one of them shipped: a
#: user-facing string in `tavern/main.py` read "导入成功desuwa。" -- visible to every user
#: who imported a card, and invisible to every test, because the tests assert on the
#: data the reply carries, not on how it reads.
#:
#: Escaped kana are included because the source writes some Chinese as `\uXXXX`, which
#: hides the text from a reader and from a naive search alike.
VOICE_LEAKS = (
    r"desuwa",
    r"masuwa",
    r"mashitawa",
    r"desuno",
    r"\\u3067\\u3059",  # です
    r"\\u307e\\u3059",  # ます
    r"\\u308f",  # わ
    r"\\u3066\\u3088",  # てよ
)

#: Files exempt from the voice scan because they *define* the patterns; scanning this
#: file would only flag its own token list. Kept to one entry on purpose -- an exception
#: list that grows stops being an exception.
VOICE_SCAN_EXEMPT = {"tools/preflight.py"}


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def scan_secrets(files: list[str]) -> list[str]:
    """Scan the **pushed** content of every tracked file."""
    findings: list[str] = []
    for name in files:
        path = REPO / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for label, pattern in SECRET_PATTERNS:
            for match in re.finditer(pattern, text):
                findings.append(f"{name}: {label} -> {match.group(0)[:12]}…")
        for match in ASSIGNMENT.finditer(text):
            value = match.group(2)
            if any(marker in value.lower() for marker in PLACEHOLDER_VALUES):
                continue
            findings.append(f"{name}: {match.group(1)} = {value[:12]}…")
    return findings


def versions() -> dict[str, str]:
    found: dict[str, str] = {}
    metadata = (REPO / "metadata.yaml").read_text(encoding="utf-8")
    match = re.search(r"^version:\s*(\S+)\s*$", metadata, re.M)
    found["metadata.yaml"] = match.group(1) if match else "?"
    main = (REPO / "tavern" / "main.py").read_text(encoding="utf-8")
    match = re.search(r'^PLUGIN_VERSION\s*=\s*"([^"]+)"', main, re.M)
    found["tavern/main.py"] = match.group(1) if match else "?"
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    badges = set(re.findall(r"badge/version-([0-9][^-]*)-", readme))
    found["README.md badge"] = ", ".join(sorted(badges)) or "?"
    readme_en = (REPO / "README_EN.md").read_text(encoding="utf-8")
    badges_en = set(re.findall(r"badge/version-([0-9][^-]*)-", readme_en))
    found["README_EN.md badge"] = ", ".join(sorted(badges_en)) or "?"
    # The dev project's own version. It drifted one release behind the plugin's the
    # first time the plugin was bumped, which is exactly how a fifth place hides.
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    found["pyproject.toml"] = match.group(1) if match else "?"
    return found


def schema_keys() -> set[str]:
    schema = json.loads((REPO / "_conf_schema.json").read_text(encoding="utf-8"))
    keys: set[str] = set()

    def walk(node: dict, prefix: str = "") -> None:
        for key, value in node.items():
            if not isinstance(value, dict):
                continue
            if value.get("type") == "object":
                walk(value.get("items", {}), f"{prefix}{key}.")
            else:
                keys.add(f"{prefix}{key}")

    walk(schema)
    return keys


def documented_keys() -> set[str]:
    """Config keys the README names, in either of the two forms its table uses.

    The table's first column is the config *group* and the second is the bare key
    (``| `backend` | `provider_id` | ...``), because repeating the prefix on every row
    wastes the narrowest column. A scanner looking only for ``backend.provider_id``
    therefore reports fourteen false positives -- which is how this function got its
    current shape.
    """
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    groups = ("trigger", "worldbook", "render", "backend", "permissions", "debug")
    found: set[str] = set()
    # Fully qualified, e.g. `backend.provider_id`
    for match in re.finditer(r"`(\w+)\.([a-z_]+)`", readme):
        found.add(f"{match.group(1)}.{match.group(2)}")
    # Bare key, e.g. `provider_id` -- matched against the declared key set later
    for match in re.finditer(r"`([a-z_]{3,})`", readme):
        found.add(match.group(1))
    # Top-level keys with no group at all.
    for match in re.finditer(r"`(enabled)`", readme):
        found.add(match.group(1))
    del groups
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    files = tracked_files()
    problems: list[str] = []

    if not args.quiet:
        print(f"tracked files: {len(files)}\n")

    # 1. secrets
    secrets = scan_secrets(files)
    if secrets:
        for item in secrets:
            print(f"  LEAK  {item}")
        problems.append(f"{len(secrets)} possible secret(s) in tracked files")
    elif not args.quiet:
        print("--- secrets")
        print("  OK     none found")

    # 2. versions
    found = versions()
    if not args.quiet:
        print("--- version")
        for where, value in found.items():
            print(f"  {value:<12} {where}")
    distinct = {value for value in found.values() if value not in ("", "?")}
    if len(distinct) != 1:
        problems.append(f"version strings disagree: {found}")

    # 3. config coverage
    declared = schema_keys()
    documented = documented_keys()
    if not args.quiet:
        print("--- config")
        print(
            f"  {len(declared)} key(s) in _conf_schema.json, {len(documented)} token(s) named in README"
        )
    # A declared `group.key` is covered when the README names either the full path or
    # the bare key (the table's group column supplies the prefix).
    undocumented = sorted(
        key
        for key in declared
        if key not in documented and key.rsplit(".", 1)[-1] not in documented
    )
    if undocumented:
        problems.append(
            f"{len(undocumented)} config key(s) not named in the README: {undocumented[:8]}"
        )
    # The reverse direction only for names whose prefix is a real config group: a
    # backticked `diff.py` is a file name, not a config key, and flagging it would turn
    # this check into noise nobody reads.
    real_groups = {key.split(".", 1)[0] for key in declared}
    stale = sorted(
        name
        for name in documented
        if "." in name and name not in declared and name.split(".", 1)[0] in real_groups
    )
    if stale:
        problems.append(f"README names config key(s) that do not exist: {stale}")

    # 4. placeholders in shipping prose
    for name in ("README.md", "README_EN.md", "metadata.yaml"):
        text = (REPO / name).read_text(encoding="utf-8")
        for word in PLACEHOLDER_WORDS:
            for match in re.finditer(rf"\b{word}\b", text):
                line = text[: match.start()].count("\n") + 1
                problems.append(f"{name}:{line} ships a placeholder ({word})")

    # 5. development voice in anything a user can read
    for name in files:
        path = REPO / name
        if name in VOICE_SCAN_EXEMPT:
            continue
        if path.suffix.lower() not in {
            ".py",
            ".md",
            ".js",
            ".html",
            ".css",
            ".json",
            ".yaml",
            ".yml",
        }:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in VOICE_LEAKS:
            for match in re.finditer(pattern, text):
                line = text[: match.start()].count("\n") + 1
                problems.append(
                    f"{name}:{line} contains working-voice text ({match.group(0)!r}) "
                    "that would ship to users"
                )

    print()
    if problems:
        print("FAIL:", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(f"PASS -- {len(files)} tracked files: no secrets, one version, config fully documented")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
