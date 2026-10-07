"""Run the *Python port* over an oracle fixture and emit the same JSON shape.

    python tools/st-oracle/run_python.py <fixture.json> [--out <result.json>]

The output mirrors ``tools/st-oracle/run.mjs`` field for field so that
``diff.py`` can compare them directly. Everything here is read-only with respect
to ``tavern/`` -- the port is never patched to make a comparison pass.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

from tavern.st import worldbook  # noqa: E402  (path setup must come first)

#: Documented fallback used on both sides until the tokenizer is ported.
#: Mirrors ``countTokens`` in ``tools/st-oracle/runtime.mjs`` exactly.
TOKEN_DIVISOR = 3


def count_tokens(text: str) -> int:
    s = str(text or "")
    if not s:
        return 0
    return max(1, len(s) // TOKEN_DIVISOR)


def engine_token_budget(settings: dict[str, Any], max_context: int) -> int | None:
    """Reproduce SillyTavern's WI budget arithmetic.

    ``budget = round(world_info_budget * maxContext / 100) || 1`` capped by
    ``world_info_budget_cap`` when that is positive. The Python port takes an
    absolute token count instead of a percentage, so the percentage is converted
    here rather than inside the port.
    """
    percent = settings.get("world_info_budget")
    if percent is None:
        return None
    budget = round(float(percent) * int(max_context) / 100) or 1
    cap = settings.get("world_info_budget_cap") or 0
    if cap > 0 and budget > cap:
        budget = cap
    return int(budget)


def convert_character_book(character_book: dict[str, Any]) -> dict[str, Any]:
    """Python stand-in for SillyTavern's ``convertCharacterBook``.

    Kept here (not in ``tavern/``) on purpose: the port does not implement this
    yet, so the oracle owns the translation and port-map.json marks the JS
    function ``pending``. Field mapping follows world-info.js:5617 exactly.
    """
    entries: dict[str, Any] = {}
    for index, entry in enumerate(character_book.get("entries") or []):
        uid = entry.get("id")
        if uid is None:
            uid = index
        ext = entry.get("extensions") or {}
        position = ext.get("position")
        if position is None:
            position = 0 if entry.get("position") == "before_char" else 1
        native: dict[str, Any] = {
            "uid": uid,
            "key": list(entry.get("keys") or []),
            "keysecondary": list(entry.get("secondary_keys") or []),
            "comment": entry.get("comment") or "",
            "content": entry.get("content") or "",
            "constant": bool(entry.get("constant") or False),
            "selective": bool(entry.get("selective") or False),
            "order": entry.get("insertion_order", 100),
            "position": position,
            "excludeRecursion": ext.get("exclude_recursion", False),
            "preventRecursion": ext.get("prevent_recursion", False),
            "delayUntilRecursion": ext.get("delay_until_recursion", False),
            "disable": not entry.get("enabled", True),
            "displayIndex": ext.get("display_index", index),
            "probability": ext.get("probability", 100),
            "useProbability": ext.get("useProbability", True),
            "depth": ext.get("depth", 4),
            "selectiveLogic": ext.get("selectiveLogic", 0),
            "outletName": ext.get("outlet_name", ""),
            "group": ext.get("group", ""),
            "groupOverride": ext.get("group_override", False),
            "groupWeight": ext.get("group_weight", 100),
            "scanDepth": ext.get("scan_depth"),
            "caseSensitive": ext.get("case_sensitive"),
            "matchWholeWords": ext.get("match_whole_words"),
            "useGroupScoring": ext.get("use_group_scoring"),
            "automationId": ext.get("automation_id", ""),
            "role": ext.get("role", 0),
            "vectorized": ext.get("vectorized", False),
            "sticky": ext.get("sticky"),
            "cooldown": ext.get("cooldown"),
            "delay": ext.get("delay"),
            "extensions": ext,
        }
        entries[str(uid)] = native
    return {"entries": entries, "originalData": character_book}


def build_books(
    fixture: dict[str, Any], worlds: list[str], scan_orders: list[str]
) -> list[worldbook.WorldBook]:
    """Load the fixture's books as the port sees them.

    The Python port has no global scan-depth setting; a world book carries
    ``scan_depth`` (or the entry carries ``scanDepth``). The oracle feeds it the
    scan depth the SillyTavern side actually used for that book
    (``world_info_depth`` unless the book set one), so both backends scan the
    same window. Fixtures must therefore not mix global depth and book depth --
    see tools/st-oracle/STATUS.md.
    """
    books: list[worldbook.WorldBook] = []
    for index, name in enumerate(scan_orders):
        payload: dict[str, Any] | None = None
        if name in (fixture.get("books") or {}):
            payload = fixture["books"][name]
        elif name in (fixture.get("character_books") or {}):
            payload = convert_character_book(fixture["character_books"][name])
        if payload is None:
            continue
        payload = json.loads(json.dumps(payload))  # never mutate the fixture
        if payload.get("scan_depth") is None:
            payload["scan_depth"] = worlds[index] if index < len(worlds) else worlds[-1]
        book = worldbook.book_from_dict(payload, source_path=name)
        book.name = name
        # The port leaves `entry.book` to the caller; fill it in so activated
        # entries can be reported as "book.uid" like the Node harness does.
        for entry in book.entries:
            entry.book = name
        books.append(book)
    return books


def depth_groups(
    entries: list[worldbook.WorldInfoEntry], by_uid: dict[str, worldbook.WorldInfoEntry]
) -> list[dict[str, Any]]:
    """Group ``position == at_depth`` entries the way world-info.js does.

    world-info.js keys ``WIDepthEntries`` on ``(depth, role)``, not on depth
    alone: two entries at the same depth but different roles must stay in
    separate buckets.
    """
    groups: list[dict[str, Any]] = []
    for entry in entries:
        if entry.position != worldbook.POSITION_AT_DEPTH:
            continue
        depth = int(by_uid.get(f"{entry.book}.{entry.uid}", entry).depth)
        role = int(getattr(by_uid.get(f"{entry.book}.{entry.uid}"), "role", 0) or 0)
        found = next((g for g in groups if g["depth"] == depth and g["role"] == role), None)
        if found is None:
            found = {"depth": depth, "role": role, "entries": []}
            groups.append(found)
        # world-info.js walks entries in *ascending* order and unshifts, so the
        # emitted list reads as descending order; mirror that here.
        found["entries"].insert(0, entry.content)
    return groups


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture")
    parser.add_argument("--out")
    args = parser.parse_args()

    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    seed = int((fixture.get("settings") or {}).get("seed", 1337))
    fixture_messages = [
        m if isinstance(m, str) else str(m.get("mes", "")) for m in (fixture.get("chat") or [])
    ]

    t0 = time.perf_counter()
    scans: list[dict[str, Any]] = []
    for scan in fixture.get("scans") or []:
        settings = scan.get("settings") or {}
        # A scan may replay a shorter chat; sticky/cooldown windows are measured
        # against the chat length, so this is how a fixture walks the chat forward.
        messages = [
            m if isinstance(m, str) else str(m.get("mes", ""))
            for m in (scan.get("chat") or fixture_messages)
        ]
        worlds = [int(w) for w in scan.get("worlds_scan_depth") or []]
        if not worlds:
            worlds = [int(settings.get("world_info_depth", 4))] * len(scan.get("worlds") or [])
        books = build_books(fixture, worlds, list(scan.get("worlds") or []))

        # (book, uid) -> entry, used to report entry metadata the same way the
        # Node harness does.
        by_uid = {f"{b.name}.{e.uid}": e for b in books for e in b.entries}

        def describe(entry: worldbook.WorldInfoEntry) -> dict[str, Any]:
            return {
                "uid": entry.uid,
                "world": entry.book,
                "order": entry.insertion_order,
                "position": entry.position,
                "depth": entry.depth,
                "role": int(entry.role or 0),
            }

        rng = random.Random(seed)
        # The port keeps sticky/cooldown bookkeeping per chat in ActivationState;
        # `turns` lets a fixture replay the same chat at several chat lengths, which
        # is how SillyTavern measures sticky/cooldown (it compares against
        # chat.length). See STATUS.md for why the two models are not identical.
        state = worldbook.ActivationState()
        turn = int(scan.get("turns", 0))
        state.turn = turn
        # world_info_max_recursion_steps == 0 means "unbounded" in world-info.js
        # (the loop stops when a pass activates nothing). The port needs a finite
        # number, so pick one far above any plausible pass count.
        steps = int(settings.get("world_info_max_recursion_steps", 0)) or 256
        port_settings = worldbook.WorldBookSettings(
            default_scan_depth=int(settings.get("world_info_depth", 4)),
            allow_recursion=bool(settings.get("world_info_recursive", False)),
            max_recursion_steps=steps,
            group_scoring=bool(settings.get("world_info_use_group_scoring", False)),
            rng=rng,
        )

        # Same guard as the Node harness: a world book that carries its own
        # scan_depth silently overrides default_scan_depth, which is easy to miss
        # and would make the scan window differ from the engine's.
        book_depths = {b.name: b.scan_depth for b in books}
        expected_depth = int(settings.get("world_info_depth", 4))
        depth_mismatch = [
            {"book": name, "requested": expected_depth, "applied": depth}
            for name, depth in book_depths.items()
            if depth is not None and depth != expected_depth
        ]

        t1 = time.perf_counter()
        result = worldbook.activate(
            books,
            messages,
            settings=port_settings,
            state=state,
            token_counter=count_tokens,
            token_budget=engine_token_budget(settings, int(scan.get("max_context", 4096))),
            active_group=scan.get("active_group"),
        )
        scan_ms = (time.perf_counter() - t1) * 1000

        before = result.content_for(worldbook.POSITION_BEFORE_CHAR)
        after = result.content_for(worldbook.POSITION_AFTER_CHAR)
        activated = [describe(entry) for entry in result.activated]
        activated.sort(key=lambda item: (item["order"], item["uid"]))

        scans.append(
            {
                "settings": settings,
                "settings_mismatched": depth_mismatch,
                "world_info": {"globalSelect": scan.get("worlds") or []},
                "worlds_loaded": [b.name for b in books],
                "worldInfoString": before + after,
                "worldInfoBefore": before,
                "worldInfoAfter": after,
                "anBefore": [
                    e.content
                    for e in result.by_position.get(worldbook.POSITION_ANT_TOP, [])
                    if e.content
                ],
                "anAfter": [
                    e.content
                    for e in result.by_position.get(worldbook.POSITION_ANT_BOTTOM, [])
                    if e.content
                ],
                "outletEntries": {},
                "worldInfoDepth": depth_groups(
                    result.by_position.get(worldbook.POSITION_AT_DEPTH, []), by_uid
                ),
                "worldInfoExamples": [
                    {"position": 0, "content": e.content}
                    for e in result.by_position.get(worldbook.POSITION_EM_TOP, [])
                    if e.content
                ]
                + [
                    {"position": 1, "content": e.content}
                    for e in result.by_position.get(worldbook.POSITION_EM_BOTTOM, [])
                    if e.content
                ],
                "activatedEntries": activated,
                "port_truncated": result.truncated,
                "timing_ms": round(scan_ms, 3),
            }
        )

    payload = {
        "fixture": Path(args.fixture).stem,
        "engine": "python-port",
        "oracle": "python",
        "python_version": sys.version.split()[0],
        "sort_mode": "ascending",
        "seed": seed,
        "comparable": fixture.get("comparable") is not False,
        "unavailable_reason": fixture.get("unavailable_reason"),
        "timing": {
            "import_ms": round(
                (time.perf_counter() - t0) * 1000 - sum(s["timing_ms"] for s in scans), 3
            )
        },
        "requests": [],
        "scans": scans,
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    mismatched = [item for scan in scans for item in scan.get("settings_mismatched") or []]
    for item in mismatched:
        sys.stderr.write(
            f"run_python.py: book {item['book']} carries scan_depth={item['applied']} but the "
            f"scan was configured for {item['requested']}\n"
        )
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
