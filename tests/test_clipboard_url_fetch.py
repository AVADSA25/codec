"""Tests for skills/clipboard_url_fetch.py — first real Step 6 trigger.

15 tests covering:
  - Metadata correctness (3): SKILL_NAME / SKILL_OBSERVATION_TRIGGER / chat allowlist
  - Trigger schema validation (1): codec_triggers._validate_trigger_dict
  - Trigger pattern semantics (3): matches https/http, rejects ftp / plain text
  - URL extraction (3): basic, trailing punct, none
  - run() behavior (5): no URL, URL via clipboard, URL via task, truncation, web_fetch failure

All tests mock subprocess + web_fetch — never makes a real network call,
never reads the user's actual clipboard.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock


_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_SKILLS = _REPO / "skills"
if str(_SKILLS) not in sys.path:
    sys.path.insert(0, str(_SKILLS))

import clipboard_url_fetch as cuf  # the skill


# ─────────────────────────────────────────────────────────────────────────────
# Metadata correctness (3)
# ─────────────────────────────────────────────────────────────────────────────

def test_metadata_constants():
    assert cuf.SKILL_NAME == "clipboard_url_fetch"
    assert cuf.SKILL_MCP_EXPOSE is False
    assert "fetch clipboard url" in cuf.SKILL_TRIGGERS
    assert isinstance(cuf.SKILL_OBSERVATION_TRIGGER, dict)


def test_observation_trigger_has_required_keys():
    """All Phase 2 Step 6 required keys present."""
    t = cuf.SKILL_OBSERVATION_TRIGGER
    for key in ("type", "pattern", "cooldown_seconds",
                "require_confirmation", "destructive"):
        assert key in t, f"missing required key: {key}"
    assert t["type"] == "clipboard_pattern"
    assert t["destructive"] is False
    assert t["require_confirmation"] is True
    assert t["cooldown_seconds"] >= 60  # not too aggressive


def test_skill_in_chat_allowlist():
    """clipboard_url_fetch must be allowed for chat-path dispatch.

    Without this, the chat-path will silently drop the match and fall
    through to LLM (same bug pattern as PR #13 for shift_report).
    """
    import codec_dashboard
    assert "clipboard_url_fetch" in codec_dashboard.CHAT_SKILL_ALLOWLIST, (
        "clipboard_url_fetch missing from CHAT_SKILL_ALLOWLIST — chat-path "
        "dispatch will silently drop the match. See PR #13 hotfix history."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Trigger schema validation (1)
# ─────────────────────────────────────────────────────────────────────────────

def test_trigger_metadata_passes_codec_triggers_validation():
    """Validate the SKILL_OBSERVATION_TRIGGER dict against the
    codec_triggers schema validator. This locks the contract so any
    schema change in codec_triggers will fail this test (and signal
    that this skill needs an update)."""
    from codec_triggers import _validate_trigger_dict
    ok, why = _validate_trigger_dict(cuf.SKILL_OBSERVATION_TRIGGER)
    assert ok, f"trigger dict invalid: {why}"


# ─────────────────────────────────────────────────────────────────────────────
# Trigger pattern semantics (3)
# ─────────────────────────────────────────────────────────────────────────────

def test_pattern_matches_https():
    import re
    pattern = cuf.SKILL_OBSERVATION_TRIGGER["pattern"]
    assert re.search(pattern, "https://example.com") is not None
    assert re.search(pattern, "before https://test.com after") is not None


def test_pattern_matches_http():
    import re
    pattern = cuf.SKILL_OBSERVATION_TRIGGER["pattern"]
    assert re.search(pattern, "http://example.com/path?q=1") is not None


def test_pattern_rejects_non_http_schemes_and_plain_text():
    import re
    pattern = cuf.SKILL_OBSERVATION_TRIGGER["pattern"]
    assert re.search(pattern, "just plain text no url") is None
    assert re.search(pattern, "ftp://example.com") is None
    assert re.search(pattern, "ssh://host") is None
    assert re.search(pattern, "") is None


# ─────────────────────────────────────────────────────────────────────────────
# URL extraction (3)
# ─────────────────────────────────────────────────────────────────────────────

def test_extract_url_basic():
    assert cuf._extract_url("check https://example.com") == "https://example.com"
    assert cuf._extract_url("https://github.com/x/y") == "https://github.com/x/y"


def test_extract_url_strips_trailing_punctuation():
    """Trailing `).,;'"` chars are almost never part of the URL."""
    assert cuf._extract_url("(see https://example.com)") == "https://example.com"
    assert cuf._extract_url("https://example.com.") == "https://example.com"
    assert cuf._extract_url("link: https://example.com;") == "https://example.com"


def test_extract_url_returns_none_when_no_url():
    assert cuf._extract_url("just text") is None
    assert cuf._extract_url("") is None
    assert cuf._extract_url(None) is None  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# run() behavior (5)
# ─────────────────────────────────────────────────────────────────────────────

def test_run_no_url_anywhere(monkeypatch):
    monkeypatch.setattr(cuf, "_read_clipboard", lambda: "no url here")
    result = cuf.run("also no url")
    assert "no URL" in result


def test_run_url_from_clipboard(monkeypatch):
    monkeypatch.setattr(cuf, "_read_clipboard", lambda: "https://example.com")

    fake_web_fetch = MagicMock()
    fake_web_fetch.run.return_value = "<html>page content here</html>"
    monkeypatch.setitem(sys.modules, "web_fetch", fake_web_fetch)

    result = cuf.run("[trigger fired]")
    assert "https://example.com" in result
    assert "page content here" in result
    fake_web_fetch.run.assert_called_once_with("https://example.com")


def test_run_url_from_task_when_clipboard_empty(monkeypatch):
    monkeypatch.setattr(cuf, "_read_clipboard", lambda: "")

    fake_web_fetch = MagicMock()
    fake_web_fetch.run.return_value = "manual fetch"
    monkeypatch.setitem(sys.modules, "web_fetch", fake_web_fetch)

    result = cuf.run("please fetch https://test.com for me")
    assert "https://test.com" in result
    assert "manual fetch" in result


def test_run_truncates_large_content(monkeypatch):
    monkeypatch.setattr(cuf, "_read_clipboard", lambda: "https://big.example.com")

    long_content = "x" * 5000
    fake_web_fetch = MagicMock()
    fake_web_fetch.run.return_value = long_content
    monkeypatch.setitem(sys.modules, "web_fetch", fake_web_fetch)

    result = cuf.run("")
    assert "[truncated" in result
    assert "5000" in result   # full-length number is reported
    assert len(result) < 2500   # truncated body + header + tail


def test_run_handles_web_fetch_failure(monkeypatch):
    monkeypatch.setattr(cuf, "_read_clipboard", lambda: "https://broken.example.com")

    fake_web_fetch = MagicMock()
    fake_web_fetch.run.side_effect = RuntimeError("boom")
    monkeypatch.setitem(sys.modules, "web_fetch", fake_web_fetch)

    result = cuf.run("")
    assert "web_fetch failed" in result
    assert "boom" in result
    assert "https://broken.example.com" in result
