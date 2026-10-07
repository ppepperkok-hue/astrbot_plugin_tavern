"""Check the published plugin archive against AstrBot's market requirements.

    python tools/check_plugin_size.py

AstrBot's plugin market rejects archives above **16 MB**
(https://docs.astrbot.app/dev/star/plugin-publish.html), and the market JSON spec
requires the archive to contain ``metadata.yaml`` whose ``author`` / ``name`` /
``version`` match the market record
(https://docs.astrbot.app/dev/plugin-market/2026-06-27.html, rules 22-25).

The archive the market sees is not the working directory: `git archive` and
GitHub's `/archive/refs/heads/<branch>` download both honour
``export-ignore`` in ``.gitattributes``, which is how this project keeps the
research tree (12.6 MB of vendored SillyTavern sources and third-party documents)
out of a user's plugin folder. Measuring the working tree instead would report a
12 MB plugin and hide the real number, so both are reported here and only the
archive gates.

Exit 0 when the archive fits and carries what the spec requires. The budget is
deliberately checked with headroom in mind: a plugin that squeaks in at 15.9 MB
will fail the moment someone adds a fixture image.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

#: The market's hard ceiling.
LIMIT_BYTES = 16 * 1024 * 1024

#: Fail before the ceiling rather than at it. Anything above this needs a
#: deliberate conversation, not a surprise CI rejection.
WARN_BYTES = 8 * 1024 * 1024

#: Files the installed plugin cannot work without. `main.py` is the AstrBot entry
#: point (`data.plugins.<dir>.main`), `metadata.yaml` is the identity the market
#: validates, and the schema drives the configuration panel.
REQUIRED = ("main.py", "metadata.yaml", "_conf_schema.json", "LICENSE")

#: Directories that must be present for the plugin to run at all.
REQUIRED_DIRS = ("tavern",)

#: Development-only paths that must NOT reach a user's plugin folder.
FORBIDDEN = ("HANDOFF.md", "research", "tests", "tools")


def build_archive(fixture: bool = False) -> Path:
    """Write the publishable archive to a temp file and return its path."""
    out = Path(tempfile.mkdtemp()) / "plugin.zip"
    command = ["git", "archive", "--format=zip", "-o", str(out)]
    command.append("HEAD" if fixture else "HEAD")
    proc = subprocess.run(command, capture_output=True, cwd=REPO, check=False)
    if proc.returncode != 0:
        raise SystemExit(
            "git archive failed (is HEAD valid?): "
            + proc.stderr.decode("utf-8", errors="replace").strip()
        )
    return out


def working_tree_size() -> tuple[int, int]:
    """``(bytes, files)`` of everything git tracks, i.e. the unfiltered download."""
    listing = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, cwd=REPO, check=True
    ).stdout.split()
    total = 0
    files = 0
    for name in listing:
        path = REPO / name
        if path.is_file():
            total += path.stat().st_size
            files += 1
    return total, files


def metadata_fields(archive: zipfile.ZipFile) -> dict[str, str]:
    """The `author` / `name` / `version` the market record has to match."""
    member = next((name for name in archive.namelist() if name.endswith("metadata.yaml")), None)
    if member is None:
        return {}
    text = archive.read(member).decode("utf-8", errors="replace")
    fields: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        if key in ("name", "author", "version", "repo"):
            fields.setdefault(key, value.strip().strip("'\""))
    return fields


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="only print problems")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    archive_path = build_archive()
    problems: list[str] = []

    with zipfile.ZipFile(archive_path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        names = {info.filename for info in archive.infolist()}
        compressed = archive_path.stat().st_size
        uncompressed = sum(info.file_size for info in infos)

        if not args.quiet:
            print(f"archive (what the market receives): {compressed:>12,} bytes")
            print(f"  uncompressed                     : {uncompressed:>12,} bytes")
            print(f"  entries                          : {len(infos):>12,}")
            print(f"  limit                            : {LIMIT_BYTES:>12,} bytes (16 MB)")
            print()

        for name in REQUIRED:
            if name not in names:
                problems.append(f"required file missing from the archive: {name}")
        for name in REQUIRED_DIRS:
            if not any(entry.startswith(f"{name}/") for entry in names):
                problems.append(f"required directory missing from the archive: {name}/")
        for name in FORBIDDEN:
            hits = [entry for entry in names if entry == name or entry.startswith(f"{name}/")]
            if hits:
                problems.append(f"development path shipped to users: {name} ({len(hits)} entries)")

        fields = metadata_fields(archive)
        for field in ("name", "author", "version", "repo"):
            if not fields.get(field):
                problems.append(f"metadata.yaml has no usable `{field}`")

        if compressed > LIMIT_BYTES:
            problems.append(
                f"archive is {compressed:,} bytes, over the 16 MB market limit "
                f"by {compressed - LIMIT_BYTES:,}"
            )
        elif compressed > WARN_BYTES:
            print(
                f"warning: archive is {compressed:,} bytes, above the "
                f"{WARN_BYTES:,}-byte comfort threshold; review before adding assets",
                file=sys.stderr,
            )

    tree_bytes, tree_files = working_tree_size()
    if not args.quiet:
        print("working tree (what a plain checkout download would be):")
        print(f"  {tree_files:,} files, {tree_bytes:,} bytes ({tree_bytes / 1024 / 1024:.2f} MB)")
        saved = tree_bytes - uncompressed
        percent = (100 * uncompressed / tree_bytes) if tree_bytes else 0
        print(f"  export-ignore removes {saved:,} bytes; the archive is {percent:.1f}% of the tree")
        print()
        print(f"metadata: {fields}")
        print()

    if problems:
        print("FAIL:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print("PASS -- archive carries every required file and fits the 16 MB limit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
