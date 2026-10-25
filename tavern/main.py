"""Compatibility import for ``tavern.main``.

The plugin implementation lives in the repository's root ``main.py``.  That is the
module AstrBot imports as ``data.plugins.<plugin_dir>.main``, so the ``Star`` class
and every command handler get the host's exact module path without rewriting a
private registry.  This shim keeps the historical ``tavern.main`` import name
pointing at that same module object, for tests and the panel's instance lookup.

The runtime is resolved by *file identity*, never by the bare name ``main``: a
foreign module cached under that common name must not become the plugin runtime.
The normal AstrBot path is loader-first -- root ``main.py`` installs the
``sys.modules`` alias and the package attribute before ``initialize()`` imports the
panel -- and this shim then only reuses that module.  A direct ``import
tavern.main`` with no loader first executes root ``main.py`` exactly once, under a
stable private name, so a later import finds the same module and the plugin keeps a
single ``PLUGIN_INSTANCES`` list.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from types import ModuleType

#: Fixed (not per-import) so a second direct import reuses this module instead of
#: defining a second ``TavernPlugin`` and a second ``PLUGIN_INSTANCES``.
_PRIVATE_NAME = "_astrbot_plugin_tavern_runtime"
_RUNTIME_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "main.py"
)
_RUNTIME_KEY = os.path.normcase(_RUNTIME_FILE)


def _matches_runtime(module: object) -> bool:
    """True when ``module`` is the repository root ``main.py`` and fully initialised."""
    if getattr(module, "PLUGIN_INSTANCES", None) is None:
        return False
    path = getattr(module, "__file__", "")
    if not isinstance(path, str) or not path:
        return False
    try:
        return os.path.normcase(os.path.realpath(path)) == _RUNTIME_KEY
    except OSError:  # pragma: no cover - an unreadable path cannot be the runtime
        return False


def _loaded_runtime() -> ModuleType | None:
    """The runtime already in ``sys.modules``, if any, without importing anything."""
    shim = sys.modules.get(__name__)
    package = sys.modules.get("tavern")
    for module in (
        getattr(package, "main", None),
        sys.modules.get("tavern.main"),
        sys.modules.get("main"),
        sys.modules.get(_PRIVATE_NAME),
    ):
        if module is not None and module is not shim and _matches_runtime(module):
            return module
    # A host may have imported root ``main.py`` under a dotted loader name of its
    # own. Adopt it by file identity rather than running the plugin a second time.
    for name in sorted(sys.modules):
        module = sys.modules.get(name)
        if module is not None and module is not shim and _matches_runtime(module):
            return module
    return None


def _load_runtime() -> ModuleType:
    """Execute root ``main.py`` once, under the stable private name."""
    spec = importlib.util.spec_from_file_location(_PRIVATE_NAME, _RUNTIME_FILE)
    if spec is None or spec.loader is None:  # pragma: no cover - the file is in the repo
        raise ImportError(f"cannot load the tavern plugin runtime from {_RUNTIME_FILE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_PRIVATE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(_PRIVATE_NAME, None)
        raise
    return module


_runtime = _loaded_runtime() or _load_runtime()

# The import machinery re-reads ``sys.modules[__name__]`` after executing this file,
# so the historical name resolves to the runtime, not to this shim. Root ``main.py``
# already does this in the loader-first path; repeating it here covers direct imports
# without touching any AstrBot registry.
sys.modules[__name__] = _runtime
_package = sys.modules.get("tavern")
if _package is not None:
    _package.main = _runtime
