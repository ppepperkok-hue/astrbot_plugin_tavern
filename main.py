"""AstrBot plugin entry point: tavern style role play.

AstrBot discovers ``main.py`` in the plugin directory and imports it. The plugin
directory is put on ``sys.path`` here (before any package import) so the modules
in ``tavern/`` always resolve to one canonical import name, both inside AstrBot
and in the unit tests.

Everything of substance lives in the ``tavern`` package:

* ``tavern/st``      - SillyTavern compatible formats (cards, world books, prompts, chats)
* ``tavern/backends`` - generation backends (AstrBot providers, external SillyTavern)
* ``tavern/core.py``  - library, per chat bindings, assembly and rendering
* ``tavern/main.py``  - the :class:`TavernPlugin` Star subclass
"""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from tavern.main import (  # noqa: E402
    HELP_TEXT,
    PLUGIN_NAME,
    PLUGIN_VERSION,
    TavernPlugin,
)

__all__ = ["HELP_TEXT", "PLUGIN_NAME", "PLUGIN_VERSION", "TavernPlugin"]
