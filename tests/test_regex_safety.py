"""Focused tests for the bounded execution of untrusted world-book regexes.

The malicious cases run in a child interpreter with a hard ``subprocess``
timeout. A regression in the guard therefore fails this test instead of
hanging the whole suite, which is exactly how the audit's 3.5 s stall showed up.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tavern.st import regex_safety  # noqa: E402
from tavern.st.regex_safety import (  # noqa: E402
    REGEX_MATCH_TIMEOUT_SECONDS,
    compile_bounded,
    search_bounded,
)
from tavern.st.wi_keywords import match_keys, parse_regex_from_string  # noqa: E402
from tavern.st.worldbook import key_matches  # noqa: E402


def _run_child(source: str, *, timeout: float = 3.0) -> subprocess.CompletedProcess[str]:
    """Run ``source`` in a fresh interpreter with a hard wall-clock limit."""
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed


def test_catastrophic_key_is_bounded_in_both_matchers() -> None:
    # ``(a|aa)+$`` is the shape the stdlib ``re`` engine cannot interrupt. The
    # child is killed after 3 s if the guard stops working.
    completed = _run_child(
        """
        import time
        from tavern.st.wi_keywords import match_keys
        from tavern.st.worldbook import key_matches

        haystack = "a" * 30 + "!"
        started = time.perf_counter()
        mirror = match_keys(haystack, "/(a|aa)+$/", {})
        live = key_matches(haystack, ["/(a|aa)+$/"], False, False)
        elapsed = time.perf_counter() - started
        print(f"{mirror}|{live}|{elapsed:.6f}")
        assert mirror is False
        assert live is False
        assert elapsed < 1.0, f"bounded match took {elapsed:.3f}s"
        """
    )
    assert completed.stdout.strip().startswith("False|False|")


def test_the_audit_pattern_is_bounded() -> None:
    completed = _run_child(
        """
        import time
        from tavern.st.wi_keywords import match_keys
        from tavern.st.worldbook import key_matches

        haystack = "a" * 26 + "!"
        started = time.perf_counter()
        mirror = match_keys(haystack, "/(a+)+$/", {})
        live = key_matches(haystack, ["/(a+)+$/"], False, False)
        elapsed = time.perf_counter() - started
        print(f"{mirror}|{live}|{elapsed:.6f}")
        assert mirror is False
        assert live is False
        assert elapsed < 1.0, f"bounded match took {elapsed:.3f}s"
        """
    )
    assert completed.stdout.strip().startswith("False|False|")


@pytest.mark.parametrize(
    ("pattern", "text", "expected"),
    [
        pytest.param(r"/gull\d+/i", "GULL12", True, id="ignore-case-on"),
        pytest.param(r"/gull\d+/", "GULL12", False, id="ignore-case-off"),
        pytest.param(r"/^second$/m", "first\nsecond", True, id="multiline-on"),
        pytest.param(r"/^second$/", "first\nsecond", False, id="multiline-off"),
        pytest.param(r"/a.b/s", "a\nb", True, id="dotall-on"),
        pytest.param(r"/a.b/", "a\nb", False, id="dotall-off"),
        pytest.param(r"/a\/b/", "a/b", True, id="escaped-slash"),
    ],
)
def test_ordinary_patterns_and_flags_keep_their_meaning(
    pattern: str, text: str, expected: bool
) -> None:
    assert parse_regex_from_string(pattern) is not None
    assert match_keys(text, pattern, {}) is expected
    # The live world-book matcher adds no flags when it is case sensitive, so
    # the key's own flags are the only thing under test here.
    assert key_matches(text, [pattern], True, False) is expected


def test_a_search_timeout_is_reported_as_no_match() -> None:
    class _AlwaysTimesOut:
        pattern = "/(a|aa)+$/"

        def search(self, text: str, *, timeout: float | None = None) -> None:
            assert timeout == REGEX_MATCH_TIMEOUT_SECONDS
            raise TimeoutError

    assert search_bounded(_AlwaysTimesOut(), "anything") is False


def test_a_timed_out_key_does_not_poison_later_matches() -> None:
    assert match_keys("a" * 30 + "!", "/(a|aa)+$/", {}) is False
    assert match_keys("GULL12", r"/gull\d+/i", {}) is True


def test_a_missing_regex_dependency_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Without ``regex`` there is no reliable way to interrupt a stdlib ``re``
    # search, so regex keys are disabled rather than run unbounded.
    monkeypatch.setattr(regex_safety, "_regex", None)
    monkeypatch.setattr(regex_safety, "_missing_dependency_warned", False)
    assert compile_bounded("abc") is None
    assert parse_regex_from_string("/abc/") is None
    assert match_keys("abc", "/abc/", {}) is False
