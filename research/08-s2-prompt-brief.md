"""S2 task brief: mirror-port the SillyTavern prompt assembly (openai.js).

This file is a working note for the porters; it records the source map, the
deliverable files, the interface contract and the acceptance procedure. It is
checked in because the port is staged and the next step must be resumable.

Source (``research/_raw/st-src/openai.js``, SillyTavern 1.19.0, commit 06bde939)
-------------------------------------------------------------------------------
| JS (line range)            | what it is                                            | Python target |
|----------------------------|-------------------------------------------------------|---------------|
| ``TokenHandler`` 3420-3479 | the eight token buckets and their arithmetic          | ``chat_completion.py::TokenHandler`` |
| ``Message`` 3511-3806      | one prompt message: role/content/name/tokens/identifier | ``chat_completion.py::Message`` |
| ``MessageCollection`` 3808-3905 | nested list of messages, ``flatten``/``getChat``  | ``chat_completion.py::MessageCollection`` |
| ``ChatCompletion`` 3917-4270 | ``squashSystemMessages``, budget bookkeeping, ``getChat`` | ``chat_completion.py::ChatCompletion`` |
| ``populationInjectionPrompts`` 810-884 | extension prompts (incl. world info slots)  | ``prompt_build.py::population_injection_prompts`` |
| ``populateChatHistory`` 885-1100 | chat history, ``names_behavior``, ``continue_postfix``, example blocks | ``prompt_build.py::populate_chat_history`` |
| ``populateDialogueExamples`` 1101-1184 | the dialogue example block                | ``prompt_build.py::populate_dialogue_examples`` |
| ``populateChatCompletion`` 1185-1366 | the assembly order of the whole prompt     | ``prompt_build.py::populate_chat_completion`` |
| ``preparePromptsForChatCompletion`` 1367-1634 | turns the ST prompt set into ``prompts[]`` | ``prompt_build.py::prepare_prompts_for_chat_completion`` |

Deliverable files
-----------------
1. ``tavern/st/chat_completion.py`` (Lead) -- the message model and token buckets.
2. ``tavern/st/prompt_build.py`` (porter) -- the population layer.
3. ``tests/test_chat_completion.py`` (Lead), ``tests/test_prompt_build.py`` (porter).
4. ``tools/st-oracle/fixtures/prompt/*.json`` + ``run_prompt_python.py`` -- the
   prompt assembly oracle (Lead), so every claim above is verified against the
   real ``openai.js`` under Node instead of being read off the source.

Interface contract (frozen before the port started)
---------------------------------------------------
``ChatCompletion``
    ``set_token_budget(context, response)``, ``add(collection, position=None)``,
    ``insert_at_start(message, identifier)``, ``insert_at_end(...)``,
    ``remove_last_from(identifier)``, ``can_afford(message)``,
    ``can_afford_all(messages)``, ``has(identifier)``, ``get_total_token_count()``,
    ``get_chat()``, ``squash_system_messages()``.

``Message``
    ``Message(role, content, identifier)``; ``create_async`` is a classmethod that
    counts tokens exactly like the reference (content, then name, then tool calls).
    Roles stay the OpenAI strings (``system``/``user``/``assistant``/``tool``).

Token counting
    The reference awaits ``countTokensOpenAIAsync``; the plugin cannot, so the
    counter is injected into ``TokenHandler`` as a plain callable. The default
    stays ``word//3`` (the same estimate the world info port uses) and callers may
    pass a real tokenizer.

Runtime differences that are deliberate
---------------------------------------
* No DOM, no ``extension_settings`` globals: every setting arrives as an argument.
* ``oai_settings`` is a plain mapping, read through ``_setting(settings, key,
  default)`` so an AstrBot config dict and a SillyTavern preset both work.
* ``chat_metadata`` (timed world info, persona) is not on the assembly path and is
  simply not read here; the world info port owns it.
* Streaming, tool calls, images and logit bias are out of scope for this stage.
"""

from __future__ import annotations

SOURCE = "research/_raw/st-src/openai.js"
COMMIT = "06bde939fb1e9c4c8d8641d810f0a9165bce127"

__all__ = ["COMMIT", "SOURCE"]
