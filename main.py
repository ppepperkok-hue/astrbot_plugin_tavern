"""AstrBot plugin entry point: tavern style role play.

AstrBot discovers ``main.py`` in the plugin directory and imports it **under its own
dotted path** (``data.plugins.<dir>.main``). That name matters more than it looks.

The plugin directory is put on ``sys.path`` here so the modules in ``tavern/`` always
resolve to one canonical import name (``tavern.main``), both inside AstrBot and in the
unit tests. Without that, ``tavern/main.py``'s own ``from tavern... import ...`` lines
would fail, since they are absolute and a plugin loaded as
``data.plugins.<dir>.main`` is not a top-level package.

Those two facts used to collide into a real, user-visible bug. Importing
``tavern.main`` by hand registered every command handler under the module path
``tavern.main``, while AstrBot's loader asked for
``data.plugins.<dir>.main``. ``star_manager`` binds the plugin instance onto handlers
by looking them up **by module path**:

    related_handlers = star_handlers_registry.get_handlers_by_module_name(metadata.module_path)
    ...
    handler.handler = functools.partial(raw_handler, metadata.star_cls)

With the names disagreeing, that lookup found nothing, so **no handler was ever bound**
-- and AstrBot calls an unbound handler as ``handler(event)``, which for a handler
declared ``(plugin, event, ...)`` means ``event`` lands in the ``plugin`` slot. Every
``/tavern`` subcommand answered with::

    TypeError: cmd_help() missing 1 required positional argument: 'event'

``tools/astrbot_e2e.py`` never caught it because it imports ``tavern.main`` directly
and calls ``on_message``, bypassing ``star_manager`` entirely.
``tools/qq_commands_live.py`` caught it, by going through a real AstrBot process.

The fix is to make both names the *same module object*: this file aliases the package
module into ``sys.modules`` under each name the loader may use, before handing the
class back. One module, one set of registered handlers, and the loader's lookup finds
them.

Everything of substance lives in the ``tavern`` package:

* ``tavern/st``        - SillyTavern compatible formats (cards, world books, prompts, chats)
* ``tavern/backends``  - generation backends (AstrBot providers, external SillyTavern)
* ``tavern/core.py``   - library, per chat bindings, assembly and rendering
* ``tavern/main.py``   - the :class:`TavernPlugin` Star subclass
"""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from tavern import main as _tavern_main  # noqa: E402
from tavern.main import (  # noqa: E402
    HELP_TEXT,
    PLUGIN_INSTANCES,
    PLUGIN_NAME,
    PLUGIN_VERSION,
    TavernPlugin,
)


def _alias_module(module: object, name: str) -> None:
    """Make ``name`` resolve to ``module``, so there is only ever one copy.

    AstrBot's loader is free to ask for the plugin as ``data.plugins.<dir>.main`` or,
    on some paths, as ``<dir>.main``. Registering those names against the same module
    object is what keeps ``star_manager``'s by-module-path handler lookup matching the
    handlers the decorators registered -- see the module docstring for what happens
    when it does not.
    """
    if not name or name == getattr(module, "__name__", ""):
        return
    sys.modules.setdefault(name, module)


for _prefix in ("data.plugins", "plugins"):
    _alias_module(_tavern_main, f"{_prefix}.{PLUGIN_DIR.name}.main")

__all__ = [
    "HELP_TEXT",
    "PLUGIN_INSTANCES",
    "PLUGIN_NAME",
    "PLUGIN_VERSION",
    "TavernPlugin",
]
