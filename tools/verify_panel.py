"""Verify the panel's web-API registration against the real AstrBot Context.

    astrbot_python tools/verify_panel.py

Not a unit test: it imports the *installed* `astrbot` and calls the real
`Context.register_web_api`, so the route strings and the four-argument signature are
checked against the host that will actually serve them. The panel's own unit tests
run without astrbot installed, so this is the only place that can catch a signature
drift.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from astrbot.core.star.context import Context  # noqa: E402

from tavern import panel  # noqa: E402

PLUGIN_NAME = "astrbot_plugin_tavern"


class Host:
    """A Context stand-in that uses the *real* registration method.

    ``Context()`` needs eleven live managers (event queue, DB, providers, ...), which
    this check has no business constructing. Binding the genuine unbound method to a
    minimal object keeps the implementation and its signature real while dropping
    only the constructor, so a drift in either is still caught.
    """

    def __init__(self) -> None:
        self.registered_web_apis: list[tuple[str, object, list[str], str]] = []

    register_web_api = Context.register_web_api


def main() -> int:
    context = Host()
    count = panel.register(context, PLUGIN_NAME)
    print(f"panel.register returned {count}")
    print(f"host now holds {len(context.registered_web_apis)} route(s)")
    print()

    failures: list[str] = []
    for route, handler, methods, desc in context.registered_web_apis:
        print(f"  {','.join(methods):<5} {route:<52} -> {getattr(handler, '__name__', handler)}")
        if not route.startswith(f"/{PLUGIN_NAME}/"):
            failures.append(f"route {route!r} is not prefixed with the metadata name")
        if not callable(handler):
            failures.append(f"handler for {route!r} is not callable")
        if not desc:
            failures.append(f"route {route!r} has no description")

    if count != len(panel.ROUTES):
        failures.append(f"registered {count} of {len(panel.ROUTES)} routes")

    # The route matcher AstrBot uses, exercised for real.
    from astrbot.dashboard.api.plugins import _match_registered_web_api  # noqa: E402

    print()
    print("matching (the function AstrBot calls):")
    for subpath, method in (
        ("astrbot_plugin_tavern/panel/state", "GET"),
        ("astrbot_plugin_tavern/panel/library", "GET"),
        ("astrbot_plugin_tavern/panel/book/Lighthouse", "GET"),
        ("astrbot_plugin_tavern/panel/card/iris", "GET"),
        ("astrbot_plugin_tavern/panel/st/characters", "GET"),
    ):
        matched = _match_registered_web_api(context.registered_web_apis, subpath, method)
        if matched is None:
            failures.append(f"{method} /{subpath} did not match any registered route")
            print(f"  NO MATCH  {method:<5} /{subpath}")
        else:
            handler, params = matched
            name = getattr(handler, "__name__", str(handler))
            print(f"  matched   {method:<5} /{subpath} -> {name} {params or ''}")

    # A wrong method must NOT match, which is what makes the methods list matter.
    for subpath, method in (("astrbot_plugin_tavern/panel/state", "DELETE"),):
        matched = _match_registered_web_api(context.registered_web_apis, subpath, method)
        if matched is not None:
            failures.append(f"{method} /{subpath} should not have matched")
            print(f"  UNEXPECTED MATCH {method} /{subpath}")

    # The handlers themselves, with no request object (the argument is optional).
    print()
    print("handlers with no request (must not raise a TypeError):")
    for handler in (panel.state, panel.library, panel.st_characters, panel.st_worldbooks):
        try:
            result = asyncio.run(handler(None))
        except Exception as exc:  # noqa: BLE001 - reporting is the point
            failures.append(f"{handler.__name__}(None) raised {type(exc).__name__}: {exc}")
            print(f"  RAISED    {handler.__name__}: {type(exc).__name__}: {exc}")
        else:
            ok = isinstance(result, dict) and "ok" in result
            print(
                f"  {handler.__name__:<16} -> ok={result.get('ok')} ({'dict with ok' if ok else 'MISSING ok'})"
            )
            if not ok:
                failures.append(f"{handler.__name__} did not return a dict with an 'ok' key")

    print()
    if failures:
        print("FAIL:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"PASS -- {count} routes registered, prefixed, matchable and method-scoped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
