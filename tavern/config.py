"""Configuration access helpers for the tavern plugin.

AstrBot hands the parsed ``_conf_schema.json`` values to the plugin as an
``AstrBotConfig`` (a ``dict`` subclass). Everything else in the plugin reads
configuration through :class:`TavernConfig`, which

* tolerates missing keys and wrong types (a hand-edited config must not crash a
  group chat),
* picks the right data directory through ``StarTools.get_data_dir`` (never the
  plugin directory itself: AstrBot replaces that directory on update), and
* exposes the few values the pipeline actually needs as plain attributes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PLUGIN_NAME = "astrbot_plugin_tavern"
#: Sub directories created under ``data/plugin_data/astrbot_plugin_tavern/``.
CARDS_DIR = "cards"
WORLDBOOKS_DIR = "worldbooks"
PRESETS_DIR = "presets"
CHATS_DIR = "chats"
#: Files created in the same data directory.
STATE_FILE = "state.json"
INDEX_FILE = "library.json"

_TRUE = {"1", "true", "yes", "on", "y"}
_FALSE = {"0", "false", "no", "off", "n", ""}


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
    return default


def as_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value)]


def section(config: Any, key: str) -> dict[str, Any]:
    """Return a nested config section as a plain dict."""
    if isinstance(config, dict):
        value = config.get(key)
        if isinstance(value, dict):
            return value
    value = getattr(config, key, None)
    return value if isinstance(value, dict) else {}


def resolve_data_dir(config: Any = None, data_dir: str | Path | None = None) -> Path:
    """Resolve the plugin data directory.

    Order: explicit argument, ``StarTools.get_data_dir`` (inside AstrBot),
    then ``<cwd>/data/plugin_data/<plugin>`` so the module also works in tests
    and in a plain checkout.
    """
    if data_dir is not None:
        path = Path(data_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    configured = as_str(section(config, "advanced").get("data_dir"))
    if configured:
        path = Path(configured).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    try:  # pragma: no cover - only available inside AstrBot
        from astrbot.api.star import StarTools

        return StarTools.get_data_dir(PLUGIN_NAME)
    except Exception:  # noqa: BLE001 - fall back to a checkout-local path
        path = Path.cwd() / "data" / "plugin_data" / PLUGIN_NAME
        path.mkdir(parents=True, exist_ok=True)
        return path


@dataclass
class TriggerConfig:
    private_always: bool = True
    group_at_only: bool = True
    wake_prefixes: list[str] = field(default_factory=lambda: ["酒馆"])
    cooldown_seconds: int = 3
    max_concurrent: int = 1


@dataclass
class WorldBookConfig:
    enabled: bool = True
    scan_depth: int = 4
    token_budget: int = 1024
    allow_recursion: bool = True
    max_recursion_steps: int = 3
    match_whole_words: bool = False
    injection_cap: int = 20


@dataclass
class RenderConfig:
    max_chars_per_message: int = 500
    segment_delay_ms: int = 400
    keep_leading_space: bool = True
    strip_status_bar: bool = True
    regex_rules: list[str] = field(default_factory=list)


@dataclass
class BackendConfig:
    type: str = "astrbot"
    provider_id: str = ""
    st_base_url: str = ""
    st_cookie: str = ""
    st_verify_ssl: bool = True
    request_timeout: float = 120.0

    @property
    def uses_sillytavern(self) -> bool:
        return self.type.strip().lower() in ("sillytavern", "st", "tavern")


@dataclass
class PermissionConfig:
    switch_card_requires_admin: bool = False
    import_requires_admin: bool = True


@dataclass
class DebugConfig:
    log_prompt: bool = False
    show_debug_in_chat: bool = True


@dataclass
class TavernConfig:
    """Typed view over the plugin configuration."""

    enabled: bool = True
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    worldbook: WorldBookConfig = field(default_factory=WorldBookConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    backend: BackendConfig = field(default_factory=BackendConfig)
    permissions: PermissionConfig = field(default_factory=PermissionConfig)
    debug: DebugConfig = field(default_factory=DebugConfig)
    data_dir: Path | None = None

    @classmethod
    def from_raw(cls, raw: Any, data_dir: str | Path | None = None) -> TavernConfig:
        trigger = section(raw, "trigger")
        worldbook = section(raw, "worldbook")
        render = section(raw, "render")
        backend = section(raw, "backend")
        permissions = section(raw, "permissions")
        debug = section(raw, "debug")
        advanced = section(raw, "advanced")

        config = cls(
            enabled=as_bool(raw.get("enabled") if isinstance(raw, dict) else None, True),
            trigger=TriggerConfig(
                private_always=as_bool(trigger.get("private_always"), True),
                group_at_only=as_bool(trigger.get("group_at_only"), True),
                wake_prefixes=as_str_list(trigger.get("wake_prefixes")) or ["酒馆"],
                cooldown_seconds=as_int(trigger.get("cooldown_seconds"), 3),
                max_concurrent=max(1, as_int(trigger.get("max_concurrent"), 1)),
            ),
            worldbook=WorldBookConfig(
                enabled=as_bool(worldbook.get("enabled"), True),
                scan_depth=max(1, as_int(worldbook.get("scan_depth"), 4)),
                token_budget=max(0, as_int(worldbook.get("token_budget"), 1024)),
                allow_recursion=as_bool(worldbook.get("allow_recursion"), True),
                max_recursion_steps=max(1, as_int(worldbook.get("max_recursion_steps"), 3)),
                match_whole_words=as_bool(worldbook.get("match_whole_words"), False),
                injection_cap=max(0, as_int(worldbook.get("injection_cap"), 20)),
            ),
            render=RenderConfig(
                max_chars_per_message=max(1, as_int(render.get("max_chars_per_message"), 500)),
                segment_delay_ms=max(0, as_int(render.get("segment_delay_ms"), 400)),
                keep_leading_space=as_bool(render.get("keep_leading_space"), True),
                strip_status_bar=as_bool(render.get("strip_status_bar"), True),
                regex_rules=as_str_list(render.get("regex_rules")),
            ),
            backend=BackendConfig(
                type=as_str(backend.get("type"), "astrbot") or "astrbot",
                provider_id=as_str(backend.get("provider_id")),
                st_base_url=as_str(backend.get("st_base_url")),
                st_cookie=as_str(backend.get("st_cookie")),
                st_verify_ssl=as_bool(backend.get("st_verify_ssl"), True),
                request_timeout=as_float(
                    backend.get("request_timeout", advanced.get("request_timeout")), 120.0
                ),
            ),
            permissions=PermissionConfig(
                switch_card_requires_admin=as_bool(
                    permissions.get("switch_card_requires_admin"), False
                ),
                import_requires_admin=as_bool(permissions.get("import_requires_admin"), True),
            ),
            debug=DebugConfig(
                log_prompt=as_bool(debug.get("log_prompt"), False),
                show_debug_in_chat=as_bool(debug.get("show_debug_in_chat"), True),
            ),
            data_dir=resolve_data_dir(raw, data_dir),
        )
        return config

    # -- derived paths ----------------------------------------------------
    @property
    def cards_dir(self) -> Path:
        return self._sub(CARDS_DIR)

    @property
    def worldbooks_dir(self) -> Path:
        return self._sub(WORLDBOOKS_DIR)

    @property
    def presets_dir(self) -> Path:
        return self._sub(PRESETS_DIR)

    @property
    def chats_dir(self) -> Path:
        return self._sub(CHATS_DIR)

    @property
    def state_path(self) -> Path:
        assert self.data_dir is not None
        return self.data_dir / STATE_FILE

    @property
    def index_path(self) -> Path:
        assert self.data_dir is not None
        return self.data_dir / INDEX_FILE

    def ensure_dirs(self) -> None:
        assert self.data_dir is not None
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for directory in (self.cards_dir, self.worldbooks_dir, self.presets_dir, self.chats_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def _sub(self, name: str) -> Path:
        assert self.data_dir is not None
        path = self.data_dir / name
        return path
