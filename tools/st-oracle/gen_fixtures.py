#!/usr/bin/env python3
"""Regenerate ``tools/st-oracle/fixtures/*.json`` deterministically.

    python tools/st-oracle/gen_fixtures.py

Fixtures are the shared contract between both backends: the Node side loads them
into real SillyTavern World Info code, the Python side feeds the same file to
``tavern/st/worldbook.py``. Nothing here talks to a model or the network.

Entry schema is the SillyTavern-native world-book row (``key`` / ``keysecondary``
/ ``order`` / ``position`` ...), which is what ``/api/worldinfo/get`` returns and
what the loader in world-info.js expects. ``convertCharacterBook`` rewrites that
shape from the V2 character-book shape used in ``character_books``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"

#: SillyTavern `world_info_position`
POS = {
    "before": 0,
    "after": 1,
    "AN_top": 2,
    "AN_bottom": 3,
    "at_depth": 4,
    "EM_top": 5,
    "EM_bottom": 6,
    "outlet": 7,
}
#: `world_info_logic`
LOGIC = {"AND_ANY": 0, "NOT_ALL": 1, "NOT_ANY": 2, "AND_ALL": 3}


def entry(uid: int, content: str, **over: Any) -> dict[str, Any]:
    """A native world-book row with SillyTavern's own defaults filled in."""
    row: dict[str, Any] = {
        "uid": uid,
        "key": [],
        "keysecondary": [],
        "comment": f"entry {uid}",
        "content": content,
        "constant": False,
        "selective": True,
        "order": 100,
        "position": POS["before"],
        "disable": False,
        "addMemo": True,
        "displayIndex": uid,
        "probability": 100,
        "useProbability": True,
        "depth": 4,
        "selectiveLogic": LOGIC["AND_ANY"],
        "outletName": "",
        "group": "",
        "groupOverride": False,
        "groupWeight": 100,
        "scanDepth": None,
        "caseSensitive": None,
        "matchWholeWords": None,
        "useGroupScoring": None,
        "automationId": "",
        "role": 0,
        "vectorized": False,
        "sticky": None,
        "cooldown": None,
        "delay": None,
        "excludeRecursion": False,
        "preventRecursion": False,
        "delayUntilRecursion": False,
        "matchPersonaDescription": False,
        "matchCharacterDescription": False,
        "matchCharacterPersonality": False,
        "matchCharacterDepthPrompt": False,
        "matchScenario": False,
        "matchCreatorNotes": False,
        "extensions": {},
        "triggers": [],
        "ignoreBudget": False,
    }
    row.update(over)
    if row["key"] and "selective" not in over:
        row["selective"] = True
    return row


def book(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {"entries": {str(e["uid"]): e for e in entries}}


def write(name: str, payload: dict[str, Any]) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(HERE.parent.parent)}")


# ---------------------------------------------------------------------------
# 01 - minimal: a single constant entry
# ---------------------------------------------------------------------------
write(
    "01-constant-minimal.json",
    {
        "description": "One constant entry, no keys: must always activate and land before the char.",
        "book_scan_depth": {"minimal": 2},
        "chat": ["Hello there.", "I look around the empty room."],
        "books": {
            "minimal": book(
                [
                    entry(
                        0,
                        "The northern gate is sealed until dawn.",
                        constant=True,
                        position=POS["before"],
                        comment="constant rule",
                    )
                ]
            )
        },
        "scans": [
            {
                "worlds": ["minimal"],
                "worlds_scan_depth": [2],
                "max_context": 4096,
                "settings": {
                    "world_info_depth": 2,
                    "world_info_budget": 25,
                    "world_info_budget_cap": 0,
                    "world_info_recursive": False,
                    "world_info_max_recursion_steps": 0,
                    "seed": 1337,
                },
            }
        ],
    },
)

# ---------------------------------------------------------------------------
# 02 - keyword hit + scan_depth boundary
# ---------------------------------------------------------------------------
write(
    "02-scan-depth-boundary.json",
    {
        "description": (
            "inside_depth matches a message 2 back (inside a depth-3 window); "
            "outside_depth matches the oldest of 5 messages (depth 4, outside the window). "
            "This pins the boundary: world-info.js scans depthBuffer[0..depth)."
        ),
        "book_scan_depth": {"boundary": 3},
        "chat": [
            "the outside_depth token sits in the oldest message",
            "second message, nothing relevant",
            "third message, nothing relevant",
            "I open the inside_depth cabinet",
            "and now I wait",
        ],
        "books": {
            "boundary": book(
                [
                    entry(0, "Cabinet contents: three copper keys.", key=["inside_depth"]),
                    entry(1, "This text must never appear.", key=["outside_depth"]),
                ]
            )
        },
        "scans": [
            {
                "worlds": ["boundary"],
                "worlds_scan_depth": [3],
                "max_context": 4096,
                "settings": {
                    "world_info_depth": 3,
                    "world_info_budget": 25,
                    "world_info_budget_cap": 0,
                    "world_info_recursive": False,
                    "world_info_max_recursion_steps": 0,
                    "seed": 1337,
                },
            }
        ],
    },
)

# ---------------------------------------------------------------------------
# 03 - selective secondary keys, all four selectiveLogic modes
# ---------------------------------------------------------------------------
write(
    "03-selective-logic.json",
    {
        "description": (
            "One entry per selectiveLogic. Every primary key 'gate' matches. "
            "Secondary keys are ['red', 'blue']: only 'red' is present in the chat. "
            "AND_ANY(0) fires, NOT_ALL(1) fires, NOT_ANY(2) does not, AND_ALL(3) does not."
        ),
        "book_scan_depth": {"selective": 4},
        "chat": ["I reach the gate.", "A red lantern hangs above it.", "Nothing else moves."],
        "books": {
            "selective": book(
                [
                    entry(
                        0,
                        "and_any fired: the red lantern is lit.",
                        key=["gate"],
                        keysecondary=["red", "blue"],
                        selectiveLogic=LOGIC["AND_ANY"],
                    ),
                    entry(
                        1,
                        "not_all fired: one secondary key is missing.",
                        key=["gate"],
                        keysecondary=["red", "blue"],
                        selectiveLogic=LOGIC["NOT_ALL"],
                    ),
                    entry(
                        2,
                        "not_any fired: no secondary key is present.",
                        key=["gate"],
                        keysecondary=["red", "blue"],
                        selectiveLogic=LOGIC["NOT_ANY"],
                    ),
                    entry(
                        3,
                        "and_all fired: every secondary key is present.",
                        key=["gate"],
                        keysecondary=["red", "blue"],
                        selectiveLogic=LOGIC["AND_ALL"],
                    ),
                ]
            )
        },
        "scans": [
            {
                "worlds": ["selective"],
                "worlds_scan_depth": [4],
                "max_context": 4096,
                "settings": {
                    "world_info_depth": 4,
                    "world_info_budget": 25,
                    "world_info_budget_cap": 0,
                    "world_info_recursive": False,
                    "world_info_max_recursion_steps": 0,
                    "world_info_case_sensitive": False,
                    "seed": 1337,
                },
            }
        ],
    },
)

# ---------------------------------------------------------------------------
# 04 - regex keys /pattern/flags
# ---------------------------------------------------------------------------
write(
    "04-regex-keys.json",
    {
        "description": (
            "/sword/i matches 'SWORD' case-insensitively; /\\bDRAGON\\b/ without i does not; "
            "a plain key 'shield' still does substring matching next to regex keys."
        ),
        "book_scan_depth": {"regex": 4},
        "chat": ["The SWORD is drawn.", "Far away a dragon sleeps.", "I raise my shield."],
        "books": {
            "regex": book(
                [
                    entry(0, "Regex key with the i flag matched.", key=["/sword/i"]),
                    entry(
                        1, "The case-sensitive regex must not match here.", key=["/\\bDRAGON\\b/"]
                    ),
                    entry(2, "Plain key still matches by substring.", key=["shiel"]),
                ]
            )
        },
        "scans": [
            {
                "worlds": ["regex"],
                "worlds_scan_depth": [4],
                "max_context": 4096,
                "settings": {
                    "world_info_depth": 4,
                    "world_info_budget": 25,
                    "world_info_budget_cap": 0,
                    "world_info_recursive": False,
                    "world_info_max_recursion_steps": 0,
                    "seed": 1337,
                },
            }
        ],
    },
)

# ---------------------------------------------------------------------------
# 05 - position and depth buckets
# ---------------------------------------------------------------------------
write(
    "05-position-and-depth.json",
    {
        "description": (
            "before / after / at_depth(2) / at_depth(7) / AN_top / EM_top / EM_bottom, "
            "all constant, order chosen to exercise the 'unshift' ordering world-info.js uses."
        ),
        "book_scan_depth": {"positions": 2},
        "chat": ["What is this place?", "The hall stretches on."],
        "books": {
            "positions": book(
                [
                    entry(0, "BEFORE one.", constant=True, order=10, position=POS["before"]),
                    entry(1, "BEFORE two.", constant=True, order=20, position=POS["before"]),
                    entry(2, "AFTER one.", constant=True, order=30, position=POS["after"]),
                    entry(3, "AFTER two.", constant=True, order=40, position=POS["after"]),
                    entry(
                        4,
                        "DEPTH two note.",
                        constant=True,
                        order=50,
                        position=POS["at_depth"],
                        depth=2,
                    ),
                    entry(
                        5,
                        "DEPTH seven note.",
                        constant=True,
                        order=60,
                        position=POS["at_depth"],
                        depth=7,
                    ),
                    entry(
                        6,
                        "DEPTH two, user role.",
                        constant=True,
                        order=70,
                        position=POS["at_depth"],
                        depth=2,
                        role=1,
                    ),
                    entry(7, "AN TOP note.", constant=True, order=80, position=POS["AN_top"]),
                    entry(8, "EM TOP example.", constant=True, order=90, position=POS["EM_top"]),
                    entry(
                        9, "EM BOTTOM example.", constant=True, order=95, position=POS["EM_bottom"]
                    ),
                ]
            )
        },
        "scans": [
            {
                "worlds": ["positions"],
                "worlds_scan_depth": [2],
                "max_context": 4096,
                "settings": {
                    "world_info_depth": 2,
                    "world_info_budget": 25,
                    "world_info_budget_cap": 0,
                    "world_info_recursive": False,
                    "world_info_max_recursion_steps": 0,
                    "seed": 1337,
                },
            }
        ],
    },
)

# ---------------------------------------------------------------------------
# 06 - insertion order and probability
# ---------------------------------------------------------------------------
write(
    "06-order-and-probability.json",
    {
        "description": (
            "Three constant entries with order 10/50/100 decide the emission order. "
            "A fourth entry has probability 50 with a pinned seed, so its outcome is "
            "reproducible per backend but the RNG streams differ (see STATUS.md)."
        ),
        "book_scan_depth": {"ordering": 2},
        "chat": ["Nothing in particular.", "Still nothing."],
        "books": {
            "ordering": book(
                [
                    entry(0, "ORDER ten.", constant=True, order=10),
                    entry(1, "ORDER fifty.", constant=True, order=50),
                    entry(2, "ORDER hundred.", constant=True, order=100),
                    entry(
                        3,
                        "PROBABILITY fifty survived the roll.",
                        constant=True,
                        order=150,
                        probability=50,
                        useProbability=True,
                    ),
                ]
            )
        },
        "scans": [
            {
                "worlds": ["ordering"],
                "worlds_scan_depth": [2],
                "max_context": 4096,
                "settings": {
                    "world_info_depth": 2,
                    "world_info_budget": 25,
                    "world_info_budget_cap": 0,
                    "world_info_recursive": False,
                    "world_info_max_recursion_steps": 0,
                    "seed": 1337,
                },
            }
        ],
    },
)

# ---------------------------------------------------------------------------
# 07 - recursion flags
# ---------------------------------------------------------------------------
write(
    "07-recursion-flags.json",
    {
        "description": (
            "anchor activates directly and emits 'alpha'. level_one is keyed on 'alpha' so it "
            "can only fire on the recursive pass, and it is flagged preventRecursion. level_two "
            "is keyed on 'beta', which level_one would emit, and is flagged excludeRecursion. "
            "'delta' exists only inside level_one's content, which is where delayUntilRecursion "
            "entries are supposed to become eligible."
        ),
        "book_scan_depth": {"recursion": 3},
        "chat": ["I whisper the seed word.", "nothing else happens"],
        "books": {
            "recursion": book(
                [
                    entry(
                        0,
                        "anchor text mentioning alpha and delta for the recursive passes.",
                        key=["seed word"],
                        order=10,
                    ),
                    entry(
                        1,
                        "level one reached by recursion; it mentions beta.",
                        key=["alpha"],
                        order=20,
                        preventRecursion=True,
                    ),
                    entry(
                        2,
                        "level two reached by recursion.",
                        key=["beta"],
                        order=30,
                        excludeRecursion=True,
                    ),
                    entry(
                        3,
                        "delayed entry only eligible once recursion has started.",
                        key=["delta"],
                        order=40,
                        delayUntilRecursion=True,
                    ),
                ]
            )
        },
        "scans": [
            {
                # Scan A keeps SillyTavern's default 25% budget on purpose: the
                # engine's budget accounting is cumulative over the whole scan and
                # a single overflow `break`s the rest of the pass, so this scan
                # also demonstrates that coupling (see STATUS.md).
                "worlds": ["recursion"],
                "worlds_scan_depth": [3],
                "max_context": 4096,
                "settings": {
                    "world_info_depth": 3,
                    "world_info_budget": 25,
                    "world_info_budget_cap": 0,
                    "world_info_recursive": True,
                    "world_info_max_recursion_steps": 3,
                    "seed": 1337,
                },
            },
            {
                # Scan B lifts the budget out of the way so the recursion-flag
                # semantics can be read without budget noise.
                "worlds": ["recursion"],
                "worlds_scan_depth": [3],
                "max_context": 4096,
                "settings": {
                    "world_info_depth": 3,
                    "world_info_budget": 100,
                    "world_info_budget_cap": 16384,
                    "world_info_recursive": True,
                    "world_info_max_recursion_steps": 3,
                    "seed": 1337,
                },
            },
        ],
    },
)

# ---------------------------------------------------------------------------
# 08 - inclusion groups
# ---------------------------------------------------------------------------
write(
    "08-inclusion-groups.json",
    {
        "description": (
            "Two scans. First with group scoring off (every keyed member activates), then "
            "with world_info_use_group_scoring on: group 'weights' keeps only the heavier "
            "member, while group 'tie' has two members with equal weight, which is the "
            "tie-break path that depends on the RNG."
        ),
        "book_scan_depth": {"groups": 3},
        "chat": ["the alpha token", "the beta token", "the tie token"],
        "books": {
            "groups": book(
                [
                    entry(
                        0,
                        "GROUP weights heavy wins.",
                        key=["alpha"],
                        group="weights",
                        groupWeight=200,
                        order=10,
                    ),
                    entry(
                        1,
                        "GROUP weights light loses.",
                        key=["alpha"],
                        group="weights",
                        groupWeight=50,
                        order=20,
                    ),
                    entry(
                        2, "GROUP tie left.", key=["tie"], group="tie", groupWeight=100, order=30
                    ),
                    entry(
                        3, "GROUP tie right.", key=["tie"], group="tie", groupWeight=100, order=40
                    ),
                ]
            )
        },
        "scans": [
            {
                "worlds": ["groups"],
                "worlds_scan_depth": [3],
                "max_context": 4096,
                "settings": {
                    "world_info_depth": 3,
                    "world_info_budget": 25,
                    "world_info_budget_cap": 0,
                    "world_info_recursive": False,
                    "world_info_max_recursion_steps": 0,
                    "world_info_use_group_scoring": False,
                    "seed": 1337,
                },
            },
            {
                "worlds": ["groups"],
                "worlds_scan_depth": [3],
                "max_context": 4096,
                "settings": {
                    "world_info_depth": 3,
                    "world_info_budget": 25,
                    "world_info_budget_cap": 0,
                    "world_info_recursive": False,
                    "world_info_max_recursion_steps": 0,
                    "world_info_use_group_scoring": True,
                    "seed": 1337,
                },
            },
        ],
    },
)

# ---------------------------------------------------------------------------
# 09 - embedded character_book
# ---------------------------------------------------------------------------
write(
    "09-character-book.json",
    {
        "description": (
            "A V2 character_book reaches the engine through convertCharacterBook(), so both "
            "the translation and the scan are exercised. Entry 2 is disabled, entry 3 only "
            "has its 'exclude_recursion' extension set."
        ),
        "book_scan_depth": {"card": 3},
        "chat": ["I greet the blacksmith.", "He mentions the broken anvil."],
        "character_books": {
            "card": {
                "name": "Card Book",
                "description": "Embedded lore from a character card",
                "scan_depth": 3,
                "token_budget": 500,
                "recursive_scanning": False,
                "extensions": {},
                "entries": [
                    {
                        "id": 0,
                        "keys": [],
                        "content": "Card constant: the forge is cold today.",
                        "enabled": True,
                        "constant": True,
                        "insertion_order": 10,
                        "position": "before_char",
                        "extensions": {"position": POS["before"], "display_index": 0},
                    },
                    {
                        "id": 1,
                        "keys": ["anvil"],
                        "content": "Card keyword: the anvil split in two last winter.",
                        "enabled": True,
                        "insertion_order": 20,
                        "position": "after_char",
                        "extensions": {"position": POS["after"], "display_index": 1},
                    },
                    {
                        "id": 2,
                        "keys": [],
                        "content": "Card disabled row must never appear.",
                        "enabled": False,
                        "constant": True,
                        "insertion_order": 30,
                        "position": "before_char",
                        "extensions": {"position": POS["before"], "display_index": 2},
                    },
                    {
                        "id": 3,
                        "keys": ["blacksmith"],
                        "content": "Card recursion row with an extension flag.",
                        "enabled": True,
                        "insertion_order": 40,
                        "position": "before_char",
                        "extensions": {
                            "position": POS["before"],
                            "display_index": 3,
                            "exclude_recursion": True,
                            "group": "smiths",
                            "group_weight": 120,
                        },
                    },
                ],
            }
        },
        "scans": [
            {
                "worlds": ["card"],
                "worlds_scan_depth": [3],
                "max_context": 4096,
                "settings": {
                    "world_info_depth": 3,
                    "world_info_budget": 25,
                    "world_info_budget_cap": 0,
                    "world_info_recursive": False,
                    "world_info_max_recursion_steps": 0,
                    "seed": 1337,
                },
            }
        ],
    },
)

# ---------------------------------------------------------------------------
# 10 - decorators
# ---------------------------------------------------------------------------
write(
    "10-decorators.json",
    {
        "description": (
            "@@activate forces activation and is stripped from the content; @@dont_activate "
            "suppresses an otherwise matching entry; @@unknown is kept verbatim; a bare @@@ "
            "line stays in the content. Only @@activate / @@dont_activate are real "
            "world-info.js decorators -- the rest belong to the ST macro interpolator."
        ),
        "book_scan_depth": {"decorators": 2},
        "chat": ["Here is the keyword.", "And the other keyword."],
        "books": {
            "decorators": book(
                [
                    entry(
                        0,
                        "@@activate\nThe forced line appears without its decorator.",
                        key=["never matches this"],
                        order=10,
                    ),
                    entry(
                        1,
                        "@@dont_activate\nThis suppressed line must never appear.",
                        key=["keyword"],
                        order=20,
                    ),
                    entry(
                        2,
                        "@@unknown_decorator\nContent keeps the unknown decorator line.",
                        key=["keyword"],
                        order=30,
                    ),
                    entry(
                        3,
                        "@@@\nTriple at line stays inside the content.",
                        constant=True,
                        order=40,
                    ),
                ]
            )
        },
        "scans": [
            {
                "worlds": ["decorators"],
                "worlds_scan_depth": [2],
                "max_context": 4096,
                "settings": {
                    "world_info_depth": 2,
                    "world_info_budget": 25,
                    "world_info_budget_cap": 0,
                    "world_info_recursive": False,
                    "world_info_max_recursion_steps": 0,
                    "world_info_case_sensitive": False,
                    "seed": 1337,
                },
            }
        ],
    },
)

# ---------------------------------------------------------------------------
# 11 - sticky / cooldown / delay
# ---------------------------------------------------------------------------
write(
    "11-sticky-cooldown-delay.json",
    {
        "description": (
            "Three replays of one book, the chat grown by one message each time, to expose the "
            "timed effects and the two places where the port's turn counter is not the engine's "
            "chat length. scan[0] chats ['north']: the delayed entry cannot fire yet and both "
            "sides agree. scan[1] ['raven','north']: the engine reads delay as "
            "'chat.length < delay' (2 < 2 is false, so it fires) while the port reads "
            "'turn < delay' (1 < 2, so it stays blocked). scan[2] "
            "['raven','north','north'] keeps the engine's per-chat timed effects applied from "
            "the previous replay, while the port is back at a fresh turn, so only the engine "
            "still holds the sticky entry. The deliverable fields (strings and buckets) are "
            "compared normally; the differences are the point."
        ),
        "book_scan_depth": {"timed": 5},
        "books": {
            "timed": book(
                [
                    entry(
                        0,
                        "STICKY raven flies overhead.",
                        key=["raven"],
                        order=10,
                        sticky=2,
                        cooldown=2,
                    ),
                    entry(
                        1,
                        "DELAY north road opens at last.",
                        key=["north"],
                        order=20,
                        delay=2,
                    ),
                ]
            )
        },
        "scans": [
            {
                "chat": ["north"],
                "worlds": ["timed"],
                "worlds_scan_depth": [5],
                "max_context": 4096,
                "turns": 0,
                "settings": {
                    "world_info_depth": 5,
                    "world_info_budget": 100,
                    "world_info_budget_cap": 16384,
                    "world_info_recursive": False,
                    "world_info_max_recursion_steps": 0,
                    "seed": 1337,
                },
            },
            {
                "chat": ["raven", "north"],
                "worlds": ["timed"],
                "worlds_scan_depth": [5],
                "max_context": 4096,
                "turns": 1,
                "settings": {
                    "world_info_depth": 5,
                    "world_info_budget": 100,
                    "world_info_budget_cap": 16384,
                    "world_info_recursive": False,
                    "world_info_max_recursion_steps": 0,
                    "seed": 1337,
                },
            },
            {
                "chat": ["raven", "north", "north"],
                "worlds": ["timed"],
                "worlds_scan_depth": [5],
                "max_context": 4096,
                "turns": 2,
                "settings": {
                    "world_info_depth": 5,
                    "world_info_budget": 100,
                    "world_info_budget_cap": 16384,
                    "world_info_recursive": False,
                    "world_info_max_recursion_steps": 0,
                    "seed": 1337,
                },
            },
        ],
    },
)

# ---------------------------------------------------------------------------
# 12 - token budget overflow
# ---------------------------------------------------------------------------
write(
    "12-budget-overflow.json",
    {
        "description": (
            "NOT COMPARABLE by design: the token budget is the one place where the two engines "
            "cannot agree without porting a tokenizer. SillyTavern counts tokens with the "
            "generation backend's tokenizer, the port estimates len//3, so the fixture only "
            "checks that both sides drop entries in the same direction. Kept so that a future "
            "tokenizer port has a target."
        ),
        "comparable": False,
        "unavailable_reason": (
            "token counting is not comparable: SillyTavern uses the model tokenizer, the port "
            "uses a len//3 estimate, so budget arithmetic diverges by construction"
        ),
        "book_scan_depth": {"budget": 2},
        "chat": ["the budget token appears here", "nothing else"],
        "books": {
            "budget": book(
                [
                    entry(
                        0,
                        "BUDGET one: " + ("a" * 200),
                        key=["budget"],
                        order=100,
                    ),
                    entry(
                        1,
                        "BUDGET two: " + ("b" * 200),
                        key=["budget"],
                        order=90,
                    ),
                ]
            )
        },
        "scans": [
            {
                "worlds": ["budget"],
                "worlds_scan_depth": [2],
                "max_context": 4096,
                "settings": {
                    "world_info_depth": 2,
                    "world_info_budget": 2,
                    "world_info_budget_cap": 0,
                    "world_info_recursive": False,
                    "world_info_max_recursion_steps": 0,
                    "seed": 1337,
                },
            }
        ],
    },
)

print(f"{len(list(FIXTURES.glob('*.json')))} fixtures in {FIXTURES}")
