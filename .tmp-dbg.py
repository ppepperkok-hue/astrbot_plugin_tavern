import asyncio, sys
sys.path.insert(0, ".")
sys.path.insert(0, "tests")
import test_prompt_build as t

c = t.FakeChatCompletion()
print("cls", type(c).__mro__[:2])
run = asyncio.run
run(t.populate_chat_history([t.chat_prompt("assistant","hi",name="Iris")], t.collection_with_history(), c, settings={"names_behavior": 1}, message_factory=t.message_factory))
print("top", c.messages.collection)
for item in c.messages.collection:
    print("  group", item.identifier, type(item).__name__, getattr(item, "collection", None))
print("flatten", c.flatten())
print("chat", t.chat(c))
