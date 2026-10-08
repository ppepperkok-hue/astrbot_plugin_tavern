"""SillyTavern compatible World Info (lorebook) engine.

The module implements the *data* and *activation* halves of SillyTavern's
World Info system so that AstrBot can reuse existing lorebooks unchanged:

* parsing of the V2 world book container (``entries`` as dict or list) and of
  the legacy flat "folder of books" exports,
* keyword scanning with case sensitivity, whole word matching and regular
  expressions,
* secondary ("selective") keys with the four ``selectiveLogic`` modes,
* scan depth, constant ("blue") entries, per entry ``order`` and
  ``displayIndex`` sorting,
* probability gating (``useProbability`` / ``probability``),
* recursive scanning with ``preventRecursion`` / ``excludeRecursion`` /
  ``delayUntilRecursion``.

Deliberate simplifications (documented so the plugin layer can decide):

* ``sticky`` / ``cooldown`` / ``delay`` are tracked per activation *turn*
  (the caller increments the turn counter), not per wall clock second.
* Group scoring (``group`` / ``groupWeight`` / ``useGroupScoring``) only
  de-duplicates entries inside the same group; the "weighted pick one"
  behaviour is opt-in through :attr:`WorldBookSettings.group_scoring`.
* Vectorised entries (``vectorized``) are treated as inactive unless a
  semantic matcher is supplied through :attr:`WorldBookSettings.vector_match`.

String values are stored in ``WorldInfoEntry`` untouched; the compatibility
layer lives in the parser so that unknown exporter variants keep working.
"""

from __future__ import annotations

import json
import math
import random
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The scan itself (``\x01``-joined haystack, global scan fields, recursion
# buffer) lives in the mirror port of ``WorldInfoBuffer``; this module keeps the
# book/entry model and the activation pipeline on top of it.
from tavern.log import logger
from tavern.st.wi_buffer import (
    SCAN_STATE_INITIAL,
    WorldInfoBuffer,
    WorldInfoBufferConfig,
)
from tavern.st.wi_decorators import apply_decorators

# --- Insertion positions (SillyTavern ``position`` field) --------------------
POSITION_BEFORE_CHAR = 0
POSITION_AFTER_CHAR = 1
POSITION_ANT_TOP = 2
POSITION_ANT_BOTTOM = 3
POSITION_AT_DEPTH = 4
POSITION_EM_TOP = 5
POSITION_EM_BOTTOM = 6
#: ``outlet`` entries are not injected into the prompt; they can only be pulled
#: in through a ``{{outlet::name}}`` macro, so the plugin renders them last.
POSITION_OUTLET = 7

POSITION_NAMES: dict[int, str] = {
    POSITION_BEFORE_CHAR: "before_char",
    POSITION_AFTER_CHAR: "after_char",
    POSITION_ANT_TOP: "ant_top",
    POSITION_ANT_BOTTOM: "ant_bottom",
    POSITION_AT_DEPTH: "at_depth",
    POSITION_EM_TOP: "em_top",
    POSITION_EM_BOTTOM: "em_bottom",
    POSITION_OUTLET: "outlet",
}

# --- Selective logic --------------------------------------------------------
LOGIC_AND_ANY = 0
LOGIC_NOT_ALL = 1
LOGIC_NOT_ANY = 2
LOGIC_AND_ALL = 3


@dataclass
class WorldBookSettings:
    """Runtime knobs that are *not* stored inside the world book file."""

    #: Fallback scan depth when neither the book nor the entry defines one.
    default_scan_depth: int = 4
    #: Global ``Match Whole Words`` default, used when an entry leaves
    #: ``matchWholeWords`` unset (SillyTavern's own default is true; the plugin
    #: config keeps it false because word boundaries behave badly for Chinese
    #: and Japanese, where substring matching is what users expect).
    match_whole_words: bool = False
    #: Global ``Case Sensitive`` default (``world_info_case_sensitive``).
    case_sensitive: bool = False
    #: Recursion is off by default: SillyTavern enables it per book/globally.
    allow_recursion: bool = False
    max_recursion_steps: int = 3
    #: Optional semantic matcher used for ``vectorized`` entries:
    #: ``matcher(entry_key_vector, texts) -> bool``.
    vector_match: Callable[[Sequence[float], Sequence[str]], bool] | None = None
    #: When True, only the heaviest entry of each ``group`` activates.
    group_scoring: bool = False
    #: Injectable RNG so tests can make ``probability`` deterministic.
    rng: random.Random = field(default_factory=random.Random)


@dataclass
class WorldInfoEntry:
    """One World Info entry (lorebook row)."""

    uid: int = 0
    keys: list[str] = field(default_factory=list)
    secondary_keys: list[str] = field(default_factory=list)
    content: str = ""
    comment: str = ""
    constant: bool = False
    selective: bool = False
    selective_logic: int = LOGIC_AND_ANY
    insertion_order: int = 100
    position: int = POSITION_BEFORE_CHAR
    depth: int = 4
    role: str | None = None
    disable: bool = False
    probability: int = 100
    use_probability: bool = True
    #: SillyTavern's ``ignoreBudget``: such entries keep activating even after the
    #: world info token budget was exceeded (world-info.js:5061).
    ignore_budget: bool = False
    #: ``@@`` decorators parsed out of ``content`` (world-info.js:4652). The two
    #: SillyTavern ones are ``@@activate`` / ``@@dont_activate``; this plugin also
    #: understands parameterised ones (``@@depth=``, ``@@role=``, ...).
    decorators: list[str] = field(default_factory=list)
    case_sensitive: bool | None = None
    match_whole_words: bool | None = None
    scan_depth: int | None = None
    group: str = ""
    group_weight: int = 100
    group_override: bool = False
    use_group_scoring: bool | None = None
    automation_id: str = ""
    vectorized: bool = False
    # Turn based lifecycle, see the module docstring.
    sticky: int = 0
    cooldown: int = 0
    delay: int = 0
    exclude_recursion: bool = False
    prevent_recursion: bool = False
    #: ``delayUntilRecursion``: the recursion pass the entry waits for. ``0`` means
    #: "immediately eligible", ``True`` behaves like ``1`` (world-info.js:4754).
    delay_until_recursion: int = 0
    display_index: int = 0
    extensions: dict[str, Any] = field(default_factory=dict)
    #: Raw vector, only present in vectorised books.
    key_vector: list[float] = field(default_factory=list)
    #: Book this entry came from (filled by the activation engine).
    book: str = ""


@dataclass
class WorldBook:
    """A parsed world book."""

    name: str = ""
    description: str = ""
    entries: list[WorldInfoEntry] = field(default_factory=list)
    scan_depth: int | None = None
    token_budget: int | None = None
    recursive_scanning: bool = False
    extensions: dict[str, Any] = field(default_factory=dict)
    source_path: str = ""

    def __len__(self) -> int:
        return len(self.entries)


def _as_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_optional_int(value: Any) -> int | None:
    """Parse a value that may be absent, ``None`` or ``0`` meaning "unset"."""
    if value is None or value == "":
        return None
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed or None


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def parse_bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return _as_bool(value)


def entry_from_dict(uid: int, payload: dict[str, Any]) -> WorldInfoEntry:
    """Build a :class:`WorldInfoEntry` from a raw lorebook row."""
    entry = WorldInfoEntry(
        uid=_as_int(payload.get("uid"), uid),
        keys=_as_str_list(payload.get("key", payload.get("keys"))),
        secondary_keys=_as_str_list(payload.get("keysecondary", payload.get("secondary_keys"))),
        content=str(payload.get("content", "") or ""),
        comment=str(payload.get("comment", "") or ""),
        constant=_as_bool(payload.get("constant")),
        # SillyTavern's ``newWorldInfoEntryDefinition`` defaults ``selective`` to
        # true (``world-info.js``), so a missing field means "selective on".
        selective=_as_bool(payload.get("selective", True), True),
        selective_logic=_as_int(payload.get("selectiveLogic"), LOGIC_AND_ANY),
        insertion_order=_as_int(payload.get("order", payload.get("insertion_order")), 100),
        position=_as_int(payload.get("position"), POSITION_BEFORE_CHAR),
        depth=_as_int(payload.get("depth"), 4),
        role=payload.get("role"),
        disable=_as_bool(payload.get("disable")),
        probability=_as_int(payload.get("probability"), 100),
        use_probability=_as_bool(payload.get("useProbability", True), True),
        ignore_budget=_as_bool(payload.get("ignoreBudget")),
        case_sensitive=parse_bool_or_none(payload.get("caseSensitive")),
        match_whole_words=parse_bool_or_none(payload.get("matchWholeWords")),
        scan_depth=_as_optional_int(payload.get("scanDepth")),
        group=str(payload.get("group", "") or ""),
        group_weight=_as_int(payload.get("groupWeight"), 100),
        group_override=_as_bool(payload.get("groupOverride")),
        use_group_scoring=parse_bool_or_none(payload.get("useGroupScoring")),
        automation_id=str(payload.get("automationId", "") or ""),
        vectorized=_as_bool(payload.get("vectorized")),
        sticky=_as_int(payload.get("sticky"), 0),
        cooldown=_as_int(payload.get("cooldown"), 0),
        delay=_as_int(payload.get("delay"), 0),
        exclude_recursion=_as_bool(payload.get("excludeRecursion")),
        prevent_recursion=_as_bool(payload.get("preventRecursion")),
        delay_until_recursion=_recursion_level(payload.get("delayUntilRecursion")),
        display_index=_as_int(payload.get("displayIndex"), uid),
        extensions=payload.get("extensions") if isinstance(payload.get("extensions"), dict) else {},
        key_vector=_as_float_list(payload.get("keyvector", payload.get("key_vector"))),
    )
    return _apply_entry_decorators(entry)


def _apply_entry_decorators(entry: WorldInfoEntry) -> WorldInfoEntry:
    """Run the entry's ``content`` through the decorator parser.

    SillyTavern removes decorator lines from the content and stores the parsed
    list on the entry (world-info.js:4627-4634, 4652); the engine then honours
    ``@@activate`` / ``@@dont_activate`` (:4875-4883). The plugin additionally
    understands parameterised decorators, which overwrite ``depth`` / ``position``
    / ``role`` / ``scan_depth`` / ``delay_until_recursion``.
    """
    if "@@" not in entry.content:
        return entry
    mapping = {
        "uid": entry.uid,
        "content": entry.content,
        "constant": entry.constant,
        "disable": entry.disable,
        "depth": entry.depth,
        "position": entry.position,
        "role": entry.role,
        "scan_depth": entry.scan_depth,
        "delay_until_recursion": entry.delay_until_recursion,
    }
    updated, content = apply_decorators(mapping, entry.content)
    entry.content = content
    entry.decorators = list(updated.get("decorators") or [])
    entry.constant = bool(updated.get("constant", entry.constant))
    entry.disable = bool(updated.get("disable", entry.disable))
    entry.depth = _as_int(updated.get("depth"), entry.depth)
    entry.position = _as_int(updated.get("position"), entry.position)
    entry.role = updated.get("role", entry.role)
    entry.scan_depth = _as_optional_int(updated.get("scan_depth"))
    entry.delay_until_recursion = _recursion_level(updated.get("delay_until_recursion"))
    return entry


def _as_float_list(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    out: list[float] = []
    for item in value:
        try:
            out.append(float(item))
        except (TypeError, ValueError):
            return []
    return out


def book_from_dict(payload: dict[str, Any], source_path: str = "") -> WorldBook:
    """Parse a world book container.

    Handles ``{"entries": {"0": {...}}}`` (SillyTavern V2 export),
    ``{"entries": [{...}]}`` and ``{"0": {...}}`` (raw ``world_info`` map).
    """
    if not isinstance(payload, dict):
        raise ValueError("world book root must be a JSON object")

    raw_entries = payload.get("entries")
    if raw_entries is None:
        raw_entries = {k: v for k, v in payload.items() if isinstance(v, dict) and "content" in v}

    entries: list[WorldInfoEntry] = []
    if isinstance(raw_entries, dict):
        for key, value in raw_entries.items():
            if not isinstance(value, dict):
                continue
            entries.append(entry_from_dict(_as_int(key, len(entries)), value))
    elif isinstance(raw_entries, list):
        for index, value in enumerate(raw_entries):
            if not isinstance(value, dict):
                continue
            entries.append(entry_from_dict(index, value))

    return WorldBook(
        name=str(payload.get("name", "") or ""),
        description=str(payload.get("description", "") or ""),
        entries=entries,
        scan_depth=_as_optional_int(payload.get("scan_depth")),
        token_budget=_as_optional_int(payload.get("token_budget")),
        recursive_scanning=_as_bool(payload.get("recursive_scanning")),
        extensions=payload.get("extensions") if isinstance(payload.get("extensions"), dict) else {},
        source_path=source_path,
    )


def load_world_book(path: str | Path) -> WorldBook:
    """Load a single world book file (``.json``, or ``.yaml`` with PyYAML)."""
    path = Path(path)
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ValueError("YAML world books need PyYAML installed") from exc
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
    return book_from_dict(payload, source_path=str(path))


def scan_world_books(directory: str | Path) -> list[WorldBook]:
    """Load every world book in ``directory``; broken files are skipped."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    books: list[WorldBook] = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in (".json", ".yaml", ".yml"):
            continue
        try:
            books.append(load_world_book(path))
        except Exception as exc:  # noqa: BLE001 - one bad book must not break the rest
            logger.warning("skip world book %s: %s", path.name, exc)
    return books


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

_REGEX_KEY = re.compile(r"^/(.*?)/([a-z]*)$", re.DOTALL)
_WORD_CHARS = re.compile(r"[\w\u4e00-\u9fff\u3040-\u30ff]+", re.UNICODE)


def is_regex_key(key: str) -> bool:
    """True when a key uses SillyTavern's ``/pattern/flags`` syntax."""
    match = _REGEX_KEY.match(key.strip())
    return bool(match) and "/" in key.strip()[1:]


def compile_key(key: str, case_sensitive: bool) -> re.Pattern[str] | None:
    """Compile one key into a regex; regex keys keep their own flags."""
    flags = 0 if case_sensitive else re.IGNORECASE
    stripped = key.strip()
    match = _REGEX_KEY.match(stripped)
    if match and is_regex_key(stripped):
        pattern, flag_letters = match.group(1), match.group(2).lower()
        for letter, flag in (
            ("i", re.IGNORECASE),
            ("m", re.MULTILINE),
            ("s", re.DOTALL),
            ("x", re.VERBOSE),
        ):
            if letter in flag_letters:
                flags |= flag
        try:
            return re.compile(pattern, flags)
        except re.error as exc:
            logger.warning("invalid world info regex %r: %s", key, exc)
            return None
    try:
        return re.compile(re.escape(stripped), flags)
    except re.error:
        return None


def _whole_word_match(haystack: str, needle: str, case_sensitive: bool) -> bool:
    """Whole word search that also works for CJK keys without word boundaries."""
    if not needle:
        return False
    if _WORD_CHARS.fullmatch(needle):
        pattern = re.compile(
            rf"(?<!\w){re.escape(needle)}(?!\w)", 0 if case_sensitive else re.IGNORECASE
        )
        return pattern.search(haystack) is not None
    return (needle in haystack) if case_sensitive else (needle.lower() in haystack.lower())


def key_matches(
    haystack: str,
    keys: Iterable[str],
    case_sensitive: bool,
    whole_words: bool,
) -> bool:
    """True when any key matches ``haystack``."""
    for key in keys:
        if not key:
            continue
        if is_regex_key(key):
            pattern = compile_key(key, case_sensitive)
            if pattern is not None and pattern.search(haystack):
                return True
        elif whole_words:
            if _whole_word_match(haystack, key, case_sensitive):
                return True
        else:
            needle = key if case_sensitive else key.lower()
            hay = haystack if case_sensitive else haystack.lower()
            if needle in hay:
                return True
    return False


# ---------------------------------------------------------------------------
# Activation engine
# ---------------------------------------------------------------------------


@dataclass
class ActivationResult:
    """Entries that should be injected for this turn."""

    #: ``position`` value -> entries in insertion order.
    by_position: dict[int, list[WorldInfoEntry]] = field(default_factory=dict)
    #: Every activated entry, deduplicated, in insertion order.
    activated: list[WorldInfoEntry] = field(default_factory=list)
    #: True when the token budget dropped at least one entry.
    truncated: bool = False

    def content_for(self, position: int) -> str:
        return "\n".join(
            entry.content for entry in self.by_position.get(position, []) if entry.content
        )


class ActivationState:
    """Per-chat activation bookkeeping for ``sticky`` / ``cooldown`` / ``delay``.

    SillyTavern keeps this in the chat metadata. The plugin stores one instance
    per chat and calls :meth:`next_turn` after every assistant reply.

    The model, stated precisely:

    * ``delay`` — the entry cannot fire before ``turn >= delay``.
    * ``sticky`` — after a *fresh* activation the entry keeps being injected
      for ``sticky`` more turns, without rescanning its keywords.
    * ``cooldown`` — after a *fresh* activation the entry cannot fire again
      (by keyword) until ``turn >= activation_turn + cooldown``.

    A held (sticky) entry never refreshes either window, which is what makes
    the pair terminate instead of extending itself forever.
    """

    def __init__(self) -> None:
        self.turn: int = 0
        self._sticky_until: dict[tuple[str, int], int] = {}
        self._cooldown_until: dict[tuple[str, int], int] = {}
        self._first_seen: dict[tuple[str, int], int] = {}
        self._ever_fired: set[tuple[str, int]] = set()
        #: Entries whose ``delay`` was cleared by hand (``/wi-set-timed-effect``).
        #: ``delay`` is computed from ``entry.delay`` and the turn counter rather
        #: than stored as a window, so the override has to live beside the maps.
        self._delay_forced: set[tuple[str, int]] = set()

    def next_turn(self) -> None:
        self.turn += 1

    def _identity(self, book: WorldBook, entry: WorldInfoEntry) -> tuple[str, int]:
        # ``source_path`` first: two books may legitimately share a display
        # name (or have none at all), and colliding uids would then shadow
        # each other's windows.
        key = book.source_path or book.name or f"book-{id(book):x}"
        return (key, entry.uid)

    def _any_identity(self, entry: WorldInfoEntry) -> tuple[str, int]:
        """The identity an entry-only caller means -- always the uid-keyed one.

        A command like ``/wi-set-timed-effect`` has a uid but no book object, and it
        must record the effect under a key the *scan* will look at. The scan asks
        through :meth:`_effect_active`, which matches on the uid half of the key, so
        ``('', uid)`` is the one identity both sides can agree on regardless of
        which ran first. Making this the book's identity when the scan happened to
        run first would make the same command behave differently depending on chat
        history, which is not a thing a user can reason about.
        """
        return ("", entry.uid)

    def identity_for(self, book: WorldBook, entry: WorldInfoEntry) -> tuple[str, int]:
        """Public view of :meth:`_identity`, for callers keying their own maps."""
        return self._identity(book, entry)

    def is_blocked(self, book: WorldBook, entry: WorldInfoEntry) -> bool:
        ident = self._identity(book, entry)
        if self.turn < self._cooldown_until.get(ident, -1):
            return True
        if ident in self._ever_fired:
            return False
        # ``delay`` only gates the first ever activation.
        return self.turn < entry.delay

    def is_sticky(self, book: WorldBook, entry: WorldInfoEntry) -> bool:
        return self.turn < self._sticky_until.get(self._identity(book, entry), -1)

    # ------------------------------------------------------------------
    # entry-only helpers
    #
    # The inclusion group filter (world-info.js:5388) asks "is this effect
    # active for this entry" without carrying the book around, and in practice
    # a chat binds a single book set, so the two views are equivalent. They
    # scan the per-entry maps for the matching uid.
    # ------------------------------------------------------------------
    def _effect_active(self, table: dict[tuple[str, int], int], entry: WorldInfoEntry) -> bool:
        return any(key[1] == entry.uid and self.turn < until for key, until in table.items())

    def is_sticky_entry(self, entry: WorldInfoEntry) -> bool:
        """``WorldInfoTimedEffects.isEffectActive('sticky', entry)``."""
        return self._effect_active(self._sticky_until, entry)

    def is_cooldown_active(self, entry: WorldInfoEntry) -> bool:
        """``WorldInfoTimedEffects.isEffectActive('cooldown', entry)``."""
        return self._effect_active(self._cooldown_until, entry)

    def is_delayed(self, entry: WorldInfoEntry) -> bool:
        """``WorldInfoTimedEffects.isEffectActive('delay', entry)``.

        SillyTavern keys the effect on ``entry.delay`` against ``chat.length``
        (world-info.js:666-677): the delay counts *messages*, so the entry only
        fires once the chat has grown past it. The plugin advances
        :class:`ActivationState` once per reply, which is its closest equivalent
        measure.

        A manual ``/wi-set-timed-effect`` override wins over the computed value --
        that is what makes the command useful, since ``entry.delay`` is otherwise
        a static field the user has to edit in the book.
        """
        return self.is_effect_forced("delay", entry) or self._delay_is_pending(entry)

    def _delay_is_pending(self, entry: WorldInfoEntry) -> bool:
        if entry.delay <= 0:
            return False
        return self.turn < entry.delay

    # ------------------------------------------------------------------
    # manual timed-effect control -- world-info.js:744-760
    # ------------------------------------------------------------------
    def is_effect_forced(self, effect_type: str, entry: WorldInfoEntry) -> bool:
        """Whether this effect was forced **on** by hand rather than by the scan."""
        identity = self._any_identity(entry)
        if effect_type == "delay":
            return identity in self._delay_forced
        table = self._sticky_until if effect_type == "sticky" else self._cooldown_until
        return any(key[1] == entry.uid and table[key] == math.inf for key in table)

    def set_effect_forced(self, effect_type: str, entry: WorldInfoEntry, forced: bool) -> bool:
        """``setTimedEffect`` (``world-info.js:744-760``) for this turn-based model.

        Returns ``False`` when the entry does not carry the effect at all, which is
        the reference's own guard (``:1532``: "This entry does not have the selected
        effect. Configure it in the editor first.").

        Forcing **on** records the duration as *never expiring* rather than adding
        a fresh window, because the reference's command sets
        ``start = chat.length`` with no ``end``; its help text says re-enabling
        refreshes the duration, and in a turn-counted model "refreshed forever" is
        the faithful reading. Forcing **off** simply drops the recorded window, so
        the next scan decides from the book's own configuration.

        ``delay`` has no stored window here -- it is a view over ``entry.delay`` and
        the turn counter -- so the override is kept as a set and wins over the
        computed value.
        """
        if effect_type not in ("sticky", "cooldown", "delay"):
            return False

        identity = self._any_identity(entry)

        if effect_type == "delay":
            if entry.delay <= 0:
                return False
            if forced:
                self._delay_forced.add(identity)
            else:
                self._delay_forced.discard(identity)
            return True

        configured = entry.sticky if effect_type == "sticky" else entry.cooldown
        if configured <= 0:
            return False

        table = self._sticky_until if effect_type == "sticky" else self._cooldown_until
        if forced:
            table[identity] = math.inf
        else:
            table.pop(identity, None)
        return True

    def on_activate(
        self,
        book: WorldBook,
        entry: WorldInfoEntry,
        *,
        fresh: bool = True,
    ) -> None:
        """Record an activation.

        ``fresh=False`` means the entry is only being *held* by its sticky
        window; the sticky and cooldown windows are then left untouched.
        """
        ident = self._identity(book, entry)
        self._first_seen.setdefault(ident, self.turn)
        self._ever_fired.add(ident)
        if not fresh:
            return
        if entry.cooldown > 0:
            self._cooldown_until[ident] = self.turn + entry.cooldown
        if entry.sticky > 0:
            self._sticky_until[ident] = max(
                self._sticky_until.get(ident, -1),
                self.turn + entry.sticky,
            )


def _recursion_level(value: Any) -> int:
    """Normalise ``delayUntilRecursion`` to its numeric level.

    SillyTavern stores a level there (``true`` behaves like 1, ``2`` waits for the
    second recursion pass); anything non positive means "immediately eligible".
    """
    if value is None or value is False:
        return 0
    if value is True:
        return 1
    try:
        level = int(value)
    except (TypeError, ValueError):
        return 0
    return max(level, 0)


def _entry_scan_text(entry: WorldInfoEntry) -> str:
    """Text that a recursive pass may match against (comment + content)."""
    return "\n".join(part for part in (entry.comment, entry.content) if part)


#: Entry keys read by ``WorldInfoBuffer`` (``WIScanEntry`` in world-info.js).
_BUFFER_VIEW_FIELDS = (
    "scanDepth",
    "caseSensitive",
    "matchWholeWords",
    "matchPersonaDescription",
    "matchCharacterDescription",
    "matchCharacterPersonality",
    "matchCharacterDepthPrompt",
    "matchScenario",
    "matchCreatorNotes",
)


def _buffer_view(entry: WorldInfoEntry, scan_depth: int | None = None) -> dict[str, Any]:
    """Adapt a :class:`WorldInfoEntry` to the mapping ``WorldInfoBuffer`` reads.

    The buffer is a faithful port and deliberately reads plain mappings (JS
    objects), so this is the single conversion point. ``scan_depth`` overrides
    the entry value, which is how the book level ``scan_depth`` reaches the
    buffer.
    """
    view: dict[str, Any] = {name: None for name in _BUFFER_VIEW_FIELDS}
    view["scanDepth"] = scan_depth if scan_depth is not None else entry.scan_depth
    view["caseSensitive"] = entry.case_sensitive
    view["matchWholeWords"] = entry.match_whole_words
    # ``getScore`` needs the keys, ``matchKeys`` needs the ``match*`` flags.
    view["key"] = list(entry.keys)
    view["keysecondary"] = list(entry.secondary_keys)
    view["selectiveLogic"] = entry.selective_logic
    for name in _BUFFER_VIEW_FIELDS[3:]:
        # ``match*`` flags are stored in ``extensions`` by SillyTavern exports.
        value = entry.extensions.get(name)
        view[name] = bool(value) if value is not None else False
    return view


def activate(
    books: Sequence[WorldBook],
    messages: Sequence[str],
    settings: WorldBookSettings | None = None,
    state: ActivationState | None = None,
    *,
    token_counter: Callable[[str], int] | None = None,
    token_budget: int | None = None,
    max_recursion_steps: int | None = None,
    active_group: str | None = None,
    global_scan_data: Mapping[str, Any] | None = None,
) -> ActivationResult:
    """Run the World Info activation pipeline for one turn.

    ``messages`` is the chat history **oldest first** (the plugin's natural
    order); it is reversed into the buffer because SillyTavern indexes depth 0 as
    the newest message. ``global_scan_data`` carries the chat independent fields
    the buffer may also scan (persona / character description / scenario / ...),
    mirroring ``WIGlobalScanData``.
    """
    settings = settings or WorldBookSettings()
    state = state or ActivationState()
    counter = token_counter or greedy_token_count
    steps_limit = (
        max_recursion_steps if max_recursion_steps is not None else settings.max_recursion_steps
    )

    # uid -> (book, entry), insertion_order, display_index
    candidates: list[tuple[WorldBook, WorldInfoEntry]] = [
        (book, entry) for book in books for entry in book.entries if not entry.disable
    ]

    activated: list[tuple[WorldBook, WorldInfoEntry]] = []
    activated_ids: set[tuple[str, int]] = set()
    #: text generated by newly activated entries, used by recursion passes
    fresh_texts: list[str] = []
    excluded_from_recursion: set[tuple[str, int]] = set()

    # SillyTavern builds its timed-effect buffers *before* the scan and never
    # updates them mid-run, so an entry activated by this very pass must not
    # influence this pass's inclusion group decision. Freeze the status that
    # existed when the turn started for all three effect kinds.
    effect_state_at_start: dict[tuple[str, int], dict[str, bool]] = {
        state.identity_for(book, entry): {
            "sticky": state.is_sticky(book, entry),
            "cooldown": state.is_cooldown_active(entry),
            "delay": state.is_delayed(entry),
        }
        for book in books
        for entry in book.entries
    }

    # The scan itself runs through the ported WorldInfoBuffer, which owns the
    # ``\x01``-joined haystack, the global scan fields and the recursion buffer
    # (world-info.js:199-478).
    buffer = WorldInfoBuffer(
        messages=list(reversed(list(messages))),
        global_scan_data=global_scan_data,
        config=WorldInfoBufferConfig(
            default_scan_depth=settings.default_scan_depth,
            case_sensitive=settings.case_sensitive,
            match_whole_words=settings.match_whole_words,
        ),
    )
    scan_state_value = SCAN_STATE_INITIAL

    def scan_depth_for(book: WorldBook, entry_scan_depth: int | None) -> int:
        # ``0`` is meaningful ("do not scan the chat"), so compare with None
        # explicitly instead of relying on truthiness.
        if entry_scan_depth is not None:
            return entry_scan_depth
        if book.scan_depth is not None:
            return book.scan_depth
        return settings.default_scan_depth

    def scan_text(book: WorldBook, entry: WorldInfoEntry) -> str:
        view = _buffer_view(entry, scan_depth_for(book, entry.scan_depth))
        return buffer.get(view, scan_state_value)

    def try_activate(
        book: WorldBook, entry: WorldInfoEntry, *, via_recursion: bool, level: int = 0
    ) -> bool:
        ident = (book.name or book.source_path, entry.uid)
        already_sticky = state.is_sticky(book, entry)

        # ``delayUntilRecursion`` is a *level*, not a flag: the entry becomes
        # eligible once the scan has recursed that many times (a plain ``true``
        # parses to 1). world-info.js:4754-4759, :5129-5133.
        required_level = _recursion_level(entry.delay_until_recursion)
        if level < required_level:
            return False
        if via_recursion and ident in excluded_from_recursion:
            return False
        if via_recursion and entry.exclude_recursion:
            excluded_from_recursion.add(ident)
        if not already_sticky and ident in activated_ids and not state.is_sticky(book, entry):
            return False
        if not already_sticky and state.is_blocked(book, entry):
            return False

        haystack = scan_text(book, entry)
        if via_recursion:
            haystack = "\n".join([haystack, *fresh_texts])

        # Case sensitivity and whole-word matching are applied inside
        # ``WorldInfoBuffer.match_keys`` via the view below, so the pipeline does
        # not resolve them here any more.
        view = _buffer_view(entry, scan_depth_for(book, entry.scan_depth))

        if already_sticky:
            # An entry inside its sticky window is simply held: its own content
            # is not rescanned, and holding it neither extends the sticky window
            # nor re-opens the cooldown window.
            triggered = True
        elif entry.constant or entry.vectorized:
            triggered = True
            if entry.vectorized:  # vectorised entries only fire with a matcher
                triggered = bool(entry.key_vector) and settings.vector_match is not None
                if triggered and settings.vector_match is not None:
                    triggered = settings.vector_match(entry.key_vector, [haystack])
        else:
            # ``WorldInfoBuffer.matchKeys`` implements the whole-word rule: a
            # multi-word key always falls back to a plain ``includes`` check, and
            # regex keys ignore both case and whole-word settings.
            triggered = any(buffer.match_keys(haystack, key, view) for key in entry.keys if key)
            if triggered and entry.selective and entry.secondary_keys:
                min_activations = _as_int(entry.extensions.get("min_activations"), 1)
                max_activations = _as_int(entry.extensions.get("max_activations"), 0)
                secondary_hits = [
                    key
                    for key in entry.secondary_keys
                    if key and buffer.match_keys(haystack, key, view)
                ]
                hits = len(secondary_hits)
                required = len([k for k in entry.secondary_keys if k])
                if entry.selective_logic == LOGIC_AND_ALL:
                    # Every secondary key must match.
                    triggered = hits == required and hits >= max(1, min_activations)
                elif entry.selective_logic == LOGIC_NOT_ANY:
                    # No secondary key may match.
                    triggered = hits < max(1, min_activations)
                elif entry.selective_logic == LOGIC_NOT_ALL:
                    # Not every secondary key may match.
                    triggered = hits < required
                else:  # LOGIC_AND_ANY
                    # At least ``min_activations`` secondary keys must match.
                    triggered = hits >= max(1, min_activations)
                if triggered and max_activations:
                    triggered = hits <= max_activations

        if active_group and entry.group and entry.group != active_group:
            triggered = False

        if not triggered and not state.is_sticky(book, entry):
            return False

        if entry.use_probability and entry.probability < 100:
            # SillyTavern: ``Math.random() * 100 < entry.probability``
            # (world-info.js). The stream itself differs from the browser, so the
            # oracle marks probability fixtures as not comparable; the formula is
            # what matters for parity of behaviour.
            if settings.rng.random() * 100 >= max(entry.probability, 0):
                return False

        if ident not in activated_ids:
            activated_ids.add(ident)
            activated.append((book, entry))
            # ``preventRecursion`` controls whether this entry's content feeds the
            # recursion buffer (world-info.js:5080), it does not stop the entry
            # itself from being activated by a recursion pass.
            if entry.content and not (via_recursion and entry.prevent_recursion):
                fresh_texts.append(_entry_scan_text(entry))
            # A fresh fire opens new sticky/cooldown windows; a held fire
            # (the entry was already inside its sticky window) must not, or the
            # entry would keep extending its own lifetime forever.
            state.on_activate(book, entry, fresh=not already_sticky)
        return True

    # Pass 1: direct scan (level 0).
    for book, entry in candidates:
        try_activate(book, entry, via_recursion=False, level=0)

    # Passes 2..N: recursive scanning over the freshly activated content. The
    # first recursion pass runs at level 1, which is what ``delayUntilRecursion``
    # compares against.
    if settings.allow_recursion and steps_limit > 1:
        for step in range(1, steps_limit):
            before = len(activated)
            for book, entry in candidates:
                try_activate(book, entry, via_recursion=True, level=step)
            if len(activated) == before:
                break

    # Inclusion groups are resolved *before* the final ordering, exactly like
    # ``checkWorldInfo`` does at world-info.js:5012.
    activated = _filter_by_inclusion_groups(
        activated, buffer, scan_state_value, settings, effect_state_at_start
    )

    # Ordering mirrors SillyTavern: entries are sorted by descending ``order``
    # (world-info.js:88 ``sortFn = (a, b) => b.order - a.order``) and then pushed
    # to the front of the target list with ``unshift`` (:5214), which turns the
    # result back into **ascending** order. ``sort`` is stable in JS and the
    # source list came from a Map, so entries sharing an ``order`` end up with
    # the greater uid first. The oracle pins this down (fixtures 03/06).
    activated.sort(key=lambda item: (item[1].insertion_order, -item[1].uid))

    # Sequential budget pass, matching world-info.js:5061-5073: walk the
    # activation order and stop accepting further entries once the running total
    # reaches the budget. Entries flagged ``ignore_budget`` keep passing (unless
    # the budget was never exceeded), and the remaining entries are *dropped* --
    # this is a hard cutoff, not a best-effort repacking.
    budget = token_budget
    if budget is None:
        budget = next((book.token_budget for book in books if book.token_budget), None)

    truncated = False
    if budget:
        used = 0
        kept: list[tuple[WorldBook, WorldInfoEntry]] = []
        ignore_budget_left = sum(1 for _book, entry in activated if entry.ignore_budget)
        for item in activated:
            entry = item[1]
            cost = counter(entry.content) if entry.content else 0
            if not entry.ignore_budget and (used + cost) > budget:
                # Hard cutoff (world-info.js:5072 ``continue``): entries past the
                # budget are dropped, and only ``ignore_budget`` ones may follow.
                truncated = True
                continue
            if entry.ignore_budget and ignore_budget_left > 0:
                ignore_budget_left -= 1
            used += cost
            kept.append(item)
        activated = kept

    result = ActivationResult(activated=[entry for _book, entry in activated], truncated=truncated)
    # at-depth entries are grouped by ``(depth, role)`` and the reference
    # discovers those groups while walking its descending order (:5236
    # ``findIndex``), so the *bucket* order follows that walk even though the
    # entries inside each bucket keep the final order.
    for _book, entry in sorted(activated, key=lambda item: item[1].insertion_order, reverse=True):
        result.by_position.setdefault(entry.position, [])
    for _book, entry in activated:
        result.by_position[entry.position].append(entry)
    return result


def _split_group_names(entry: WorldInfoEntry) -> list[str]:
    """``entry.group.split(/,\\s*/)`` with empty names dropped (world-info.js:5392)."""
    if not entry.group:
        return []
    return [part for part in (piece.strip() for piece in entry.group.split(",")) if part]


def _resolved_group_names(entry: WorldInfoEntry) -> list[str]:
    """Group names that actually make the entry a group member.

    ``-1`` is SillyTavern's "no group" placeholder (an entry created before
    groups existed), so it must not take part in the one-winner rule.
    """
    if not entry.group or entry.group.strip() in ("-1", "0"):
        return []
    return _split_group_names(entry)


def _is_effect_active(kind: str, entry: WorldInfoEntry, state: ActivationState) -> bool:
    """Whether a timed effect is currently active for ``entry``.

    ``WorldInfoTimedEffects`` keeps this in chat metadata; the plugin keeps it in
    :class:`ActivationState`. The mapping is deliberately narrow -- it only
    answers the question the inclusion group filter asks.
    """
    if kind == "sticky":
        return state.is_sticky_entry(entry)
    if kind == "cooldown":
        return state.is_cooldown_active(entry)
    if kind == "delay":
        return state.is_delayed(entry)
    return False


def _filter_by_inclusion_groups(
    activated: list[tuple[WorldBook, WorldInfoEntry]],
    buffer: WorldInfoBuffer,
    scan_state_value: int,
    settings: WorldBookSettings,
    effect_state_at_start: dict[tuple[str, int], dict[str, bool]] | None = None,
) -> list[tuple[WorldBook, WorldInfoEntry]]:
    """Mirror of ``filterByInclusionGroups`` (world-info.js:5388-5475).

    Entries may belong to several comma separated groups. Per group exactly one
    entry survives: sticky entries first, then ``groupOverride`` priority, then a
    weighted random roll; when ``useGroupScoring`` is on (globally or per entry)
    the entries with the highest key score win instead.

    ``effect_state_at_start`` is the timed-effect status frozen *before* this
    turn's scan, because SillyTavern's timed effects never change mid-run.
    """
    grouped: dict[str, list[tuple[WorldBook, WorldInfoEntry]]] = {}
    for item in activated:
        for name in _resolved_group_names(item[1]):
            grouped.setdefault(name, []).append(item)
    if not grouped:
        return activated

    current = list(activated)

    def effect_active(
        kind: str, book: WorldBook, entry: WorldInfoEntry, state: ActivationState | None = None
    ) -> bool:
        if effect_state_at_start is not None and state is not None:
            return effect_state_at_start.get(state.identity_for(book, entry), {}).get(kind, False)
        if state is None:
            return False
        return _is_effect_active(kind, entry, state)

    def remove(entry: WorldInfoEntry) -> None:
        for index, item in enumerate(current):
            if item[1] is entry:
                del current[index]
                return

    has_sticky: dict[str, bool] = {}
    for name, members in grouped.items():
        has_sticky[name] = False

        sticky = [item for item in members if effect_active("sticky", item[0], item[1])]
        if sticky:
            for item in list(members):
                if item in sticky:
                    continue
                remove(item[1])
                members.remove(item)
            has_sticky[name] = True

        for kind in ("cooldown", "delay"):
            for item in list(members):
                if effect_active(kind, item[0], item[1]):
                    remove(item[1])
                    members.remove(item)

    for name, members in grouped.items():
        if has_sticky.get(name):
            continue

        if len(members) <= 1:
            continue

        # Group scoring: keep only the entries whose key score matches the best.
        if settings.group_scoring or any(item[1].use_group_scoring for item in members):
            scores = [
                (item, buffer.get_score(_buffer_view(item[1]), scan_state_value))
                for item in members
            ]
            best_score = max(score for _item, score in scores)
            for item, score in list(scores):
                scored = (
                    settings.group_scoring
                    if item[1].use_group_scoring is None
                    else bool(item[1].use_group_scoring)
                )
                if scored and score < best_score:
                    remove(item[1])
                    if item in members:
                        members.remove(item)
            if len(members) <= 1:
                continue

        # ``groupOverride`` entries are a priority tier, highest order first.
        overrides = [item for item in members if item[1].group_override]
        if overrides:
            overrides.sort(key=lambda item: item[1].insertion_order, reverse=True)
            winner = overrides[0]
        else:
            total_weight = sum(item[1].group_weight for item in members)
            roll = settings.rng.random() * total_weight
            running = 0
            winner = None
            for item in members:
                running += item[1].group_weight
                if roll <= running:
                    winner = item
                    break

        if winner is None:
            continue

        for item in list(members):
            if item is winner:
                continue
            remove(item[1])
            members.remove(item)

    return current


def greedy_token_count(text: str) -> int:
    """Cheap token estimate used when no exact counter is supplied.

    Falls back to tiktoken when it is installed, because the plugin ships
    ``tiktoken`` as an optional dependency.
    """
    if not text:
        return 0
    counter = _tiktoken_counter()
    if counter is not None:
        try:
            return len(counter(text))
        except Exception:  # noqa: BLE001 - never let counting break a turn
            pass
    return max(1, len(text) // 3)


_TIKTOKEN: Any = None
_TIKTOKEN_TRIED = False


def _tiktoken_counter() -> Any:
    global _TIKTOKEN, _TIKTOKEN_TRIED
    if _TIKTOKEN_TRIED:
        return _TIKTOKEN
    _TIKTOKEN_TRIED = True
    try:
        import tiktoken  # type: ignore[import-not-found]

        _TIKTOKEN = tiktoken.get_encoding("cl100k_base").encode
    except Exception:  # noqa: BLE001 - optional dependency
        _TIKTOKEN = None
    return _TIKTOKEN
