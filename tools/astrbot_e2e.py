"""End-to-end smoke test inside AstrBot: real event objects, fake backend.

Runs the plugin's message handler with AstrBot's own ``AstrMessageEvent`` /
``AstrBotMessage`` / ``MessageChain`` types, but with a stub generation backend,
so no model and no platform connection are needed.

Run with the AstrBot tool environment:
    E:\\astrbot_plugin\\.tools\\uv-tools\\astrbot\\Scripts\\python.exe tools\\astrbot_e2e.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PLUGIN_DIR_NAME = "astrbot_plugin_tavern"
MODULE_PATH = f"data.plugins.{PLUGIN_DIR_NAME}.main"
SCOPE = "aiocqhttp:GroupMessage:123456789"


class _Event:
    """Concrete ``AstrMessageEvent``: the real class, minimal ABC implementation."""


def _collect(async_gen):
    async def run():
        return [item async for item in async_gen]

    return asyncio.run(run())


def main() -> int:
    from astrbot.api.message_components import Plain
    from astrbot.core.config import AstrBotConfig
    from astrbot.core.message.components import At
    from astrbot.core.platform.astr_message_event import AstrMessageEvent
    from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.platform.platform_metadata import PlatformMetadata

    module = __import__(MODULE_PATH, fromlist=["main"])
    plugin_class = module.TavernPlugin
    # The plugin's own module namespace (the root main.py re-exports from it).
    plugin_module = __import__("tavern.main", fromlist=["main"])

    config = AstrBotConfig()
    plugin = plugin_class(None, config)
    data_dir = plugin.config.data_dir
    print(f"data dir: {data_dir}")

    # Fresh library for the run: copy the fixtures into the plugin data dir.
    fixtures = REPO_ROOT / "tests" / "fixtures"
    for name, source in (
        ("cards", fixtures / "cards" / "iris.json"),
        ("worldbooks", fixtures / "worldbooks" / "lighthouse.json"),
    ):
        target = data_dir / name
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target / source.name)
    plugin.core.load()
    plugin.core.bind_card(SCOPE, "Iris")
    plugin.core.toggle_book(SCOPE, "Lighthouse Lore", True)
    print(f"cards={plugin.core.card_ids()} books={plugin.core.book_ids()}")

    # A deterministic backend replaces the model.
    class Backend:
        name = "smoke"

        async def generate(self, request):
            from tavern.backends.base import GenerationResult

            self.request = request
            return GenerationResult(
                text='The fog lifts.\n\n"The lamp is lit again."', model="smoke"
            )

        async def close(self):
            return None

    backend = Backend()
    plugin._backend = backend

    # Build a real AstrBot event.
    message = AstrBotMessage()
    message.type = MessageType.GROUP_MESSAGE
    message.self_id = "10000"
    message.session_id = "123456789"
    message.message_id = "1"
    message.group_id = "123456789"
    message.sender = MessageMember(user_id="20000", nickname="小明")
    message.message = [At(qq="10000"), Plain("the lighthouse showed up")]
    message.message_str = "the lighthouse showed up"
    message.raw_message = None

    class SmokeEvent(AstrMessageEvent):
        """Concrete subclass; only the abstract hooks are filled in."""

        async def send(self, message) -> None:  # pragma: no cover - not used here
            return None

    event = SmokeEvent(
        message_str="the lighthouse showed up",
        message_obj=message,
        platform_meta=PlatformMetadata(name="aiocqhttp", description="smoke", id="aiocqhttp"),
        session_id="123456789",
    )
    event.is_at_or_wake_command = True

    print(f"unified_msg_origin: {event.unified_msg_origin}")
    print(f"is_private: {event.is_private_chat()}  at_bot: {plugin_module._is_at_bot(event)}")

    results = _collect(plugin.on_message(event))
    texts = [item.get_plain_text() for item in results]
    print("--- replies ---")
    for text in texts:
        print(f"  | {text!r}")
    print(f"stopped: {event.is_stopped()}")

    if not any("lamp is lit" in text for text in texts):
        print("FAIL: the model answer was not sent")
        return 1
    if not any("The lamp sweeps" in text for text in texts):
        print("FAIL: the card greeting was not sent on the first turn")
        return 1
    preview = plugin.core.history_preview(SCOPE, limit=5)
    print("--- history ---")
    for line in preview:
        print(f"  | {line}")
    print(f"system prompt chars: {len(backend.request.system_prompt)}")
    print(f"system prompt head: {backend.request.system_prompt[:120]!r}")
    print(f"messages: {[(m.role, m.content[:30]) for m in backend.request.messages]}")
    if "44 metres tall" not in backend.request.system_prompt:
        print("FAIL: world info was not injected into the system prompt")
        return 1
    print("E2E SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
