"""AstrBot 4.29.1's loader keys Star and handler metadata by module path.

The base class's ``__init_subclass__`` registers ``star_map[cls.__module__]``, and
``register/star_handler.get_handler_or_create`` records ``handler.__module__``.
``PluginManager.load`` then looks up ``metadata.module_path`` -- exactly
``data.plugins.<dir>.main``.
A class defined in ``tavern/main.py`` therefore registers as ``tavern.main``; the
loader misses it, takes the legacy path, and appends a second root-dir-less Star.
These assertions make that mismatch fail without needing a live AstrBot process.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
MAIN = REPO / "main.py"
LOADER_PATH = "data.plugins.astrbot_plugin_tavern.main"

#: Run in a fresh interpreter because the bug is about ``sys.modules`` state. The
#: foreign module claims the name ``main`` *before* the shim is imported; the shim
#: must resolve root ``main.py`` by file identity and share it with the panel.
FOREIGN_MAIN_PROBE = r"""
import sys
import types
from pathlib import Path

repo = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repo))

foreign = types.ModuleType("main")
foreign.__file__ = str(repo / "not_the_plugin.py")
foreign.PLUGIN_INSTANCES = ["foreign"]
sys.modules["main"] = foreign

from tavern import main as runtime

assert runtime is not foreign, "tavern.main must not alias a foreign top-level main"
assert Path(runtime.__file__).resolve() == (repo / "main.py").resolve()
assert sys.modules["tavern.main"] is runtime
assert sys.modules["tavern"].main is runtime

from tavern import panel

sentinel = object()
runtime.PLUGIN_INSTANCES.append(sentinel)
assert panel._instance() is sentinel

executions = {
    id(module)
    for module in sys.modules.values()
    if getattr(module, "__file__", None)
    and Path(module.__file__).resolve() == (repo / "main.py").resolve()
}
assert len(executions) == 1, "the plugin runtime must be executed exactly once"
print("ok")
"""

#: The normal host path: root ``main.py`` is imported first under its dotted loader
#: name and installs the alias. A later direct ``import tavern.main`` must return
#: that same module instead of executing the file a second time.
LOADER_FIRST_PROBE = r"""
import importlib
import importlib.util
import sys
import types
from pathlib import Path

repo = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repo))
loader_name = "data.plugins.astrbot_plugin_tavern.main"
for name in ("data", "data.plugins", "data.plugins.astrbot_plugin_tavern"):
    package = types.ModuleType(name)
    package.__path__ = []
    sys.modules[name] = package

spec = importlib.util.spec_from_file_location(loader_name, repo / "main.py")
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[loader_name] = module
spec.loader.exec_module(module)

assert sys.modules["tavern.main"] is module
assert sys.modules["tavern"].main is module

alias = importlib.import_module("tavern.main")
assert alias is module
assert module.__name__ == loader_name

from tavern import panel

sentinel = object()
module.PLUGIN_INSTANCES.append(sentinel)
assert panel._instance() is sentinel

executions = {
    id(item)
    for item in sys.modules.values()
    if getattr(item, "__file__", None)
    and Path(item.__file__).resolve() == (repo / "main.py").resolve()
}
assert len(executions) == 1, "loading the alias must not re-execute root main.py"
print("ok")
"""


def _run_probe(source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", source, str(REPO)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )


HANDLER_MODULES: list[str] = []
STAR_MAP: dict[str, SimpleNamespace] = {}
STAR_REGISTRY: list[SimpleNamespace] = []


class _Group:
    def command(self, _name, alias=None):
        def decorator(fn):
            HANDLER_MODULES.append(fn.__module__)
            return fn

        return decorator


class _Filter:
    class EventMessageType:
        ALL = "all"

    def command_group(self, _name, alias=None):
        return lambda _fn: _Group()

    def event_message_type(self, _kind):
        return lambda fn: fn


class _Star:
    """The registration half of AstrBot 4.29.1's ``Star`` base class."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not STAR_MAP.get(cls.__module__):
            metadata = SimpleNamespace(
                name=None,
                star_cls_type=cls,
                module_path=cls.__module__,
                root_dir_name=None,
            )
            STAR_MAP[cls.__module__] = metadata
            STAR_REGISTRY.append(metadata)
        else:
            STAR_MAP[cls.__module__].star_cls_type = cls
            STAR_MAP[cls.__module__].module_path = cls.__module__

    def __init__(self, context=None, config=None) -> None:
        self.context = context


def _package(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = []  # type: ignore[attr-defined]
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _load_plugin_as_astrbot_does() -> types.ModuleType:
    modules = {
        "astrbot": _package("astrbot"),
        "astrbot.api": _package(
            "astrbot.api",
            AstrBotConfig=type("AstrBotConfig", (dict,), {}),
            logger=SimpleNamespace(
                debug=lambda *a, **k: None,
                info=lambda *a, **k: None,
                warning=lambda *a, **k: None,
                error=lambda *a, **k: None,
            ),
        ),
        "astrbot.api.message_components": _package(
            "astrbot.api.message_components",
            Plain=type("Plain", (), {}),
        ),
        "astrbot.api.event": _package(
            "astrbot.api.event",
            filter=_Filter(),
            MessageChain=type("MessageChain", (), {}),
            AstrMessageEvent=type("AstrMessageEvent", (), {}),
        ),
        "astrbot.api.star": _package(
            "astrbot.api.star",
            Context=object,
            Star=_Star,
            register=lambda *_a, **_k: (lambda cls: cls),
        ),
        "astrbot.core": _package("astrbot.core"),
        "astrbot.core.star": _package("astrbot.core.star"),
        "astrbot.core.star.filter": _package("astrbot.core.star.filter"),
        "astrbot.core.star.filter.command": _package(
            "astrbot.core.star.filter.command",
            GreedyStr=type("GreedyStr", (str,), {}),
        ),
        "data": _package("data"),
        "data.plugins": _package("data.plugins"),
        "data.plugins.astrbot_plugin_tavern": _package(
            "data.plugins.astrbot_plugin_tavern",
            __path__=[str(REPO)],
        ),
    }
    previous = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        spec = importlib.util.spec_from_file_location(LOADER_PATH, MAIN)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[LOADER_PATH] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(LOADER_PATH, None)
        for name, old in previous.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


def test_class_and_handlers_use_the_loader_module_path() -> None:
    STAR_MAP.clear()
    STAR_REGISTRY.clear()
    HANDLER_MODULES.clear()
    previous_alias = sys.modules.get("tavern.main")
    tavern_package = sys.modules.get("tavern")
    missing = object()
    previous_attr = getattr(tavern_package, "main", missing) if tavern_package else missing
    try:
        module = _load_plugin_as_astrbot_does()

        assert module.TavernPlugin.__module__ == LOADER_PATH
        assert set(HANDLER_MODULES) == {LOADER_PATH}
        assert len(HANDLER_MODULES) == 12
        assert getattr(module, "_REGISTERED_SUBCOMMANDS", False) is True
        assert list(STAR_MAP) == [LOADER_PATH]
        assert [item.module_path for item in STAR_REGISTRY] == [LOADER_PATH]

        # ``tavern.panel`` still imports ``tavern.main``; it must resolve to this same
        # module instead of executing the root file a second time.
        assert sys.modules["tavern.main"] is module
        sentinel = object()
        module.PLUGIN_INSTANCES.append(sentinel)
        from tavern import panel

        assert panel._instance() is sentinel

        # The legacy ``PluginManager.load`` fallback appends a second, root-dir-less
        # Star whenever ``star_map`` has no entry for the loader path. The assertions
        # above -- the class and every handler registered under exactly that path --
        # are what keep that branch unreachable; this states the resulting registry
        # state directly, without simulating a branch that cannot run.
        assert len(STAR_REGISTRY) == 1, "the plugin must be registered exactly once"
    finally:
        if previous_alias is None:
            sys.modules.pop("tavern.main", None)
        else:
            sys.modules["tavern.main"] = previous_alias
        if tavern_package is not None:
            if previous_attr is missing:
                try:
                    delattr(tavern_package, "main")
                except AttributeError:
                    pass
            else:
                tavern_package.main = previous_attr


def test_direct_import_ignores_a_foreign_main_module() -> None:
    """``import main`` must resolve the known file, not whatever owns the name."""
    completed = _run_probe(FOREIGN_MAIN_PROBE)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok"


def test_the_loader_first_alias_is_reused_without_a_second_execution() -> None:
    """The host imports root ``main.py`` first; ``tavern.main`` must reuse it."""
    completed = _run_probe(LOADER_FIRST_PROBE)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok"
