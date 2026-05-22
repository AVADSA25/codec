"""Tests for PR-3C config-knob wiring (A-16, A-17, A-21).

- A-16: codec.py honored a hardcoded wake-keyword list + ignored
  `WAKE_PHRASES`; now `_is_wake_utterance` matches homophone keywords
  AND user-configured wake phrases (length-guarded).
- A-17: `draft_keywords` config knob was dead; now `codec_core.is_draft`
  honors it.
- A-21: dead `codec_config.AGENT_NAME` constant removed.

Reference: docs/audits/PHASE-1-CODE-QUALITY.md findings A-16, A-17, A-21.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _import_codec():
    """Import codec.py, skipping on headless envs where pynput/native deps
    aren't available (mirrors tests/test_session_execution.py)."""
    try:
        import codec
        return codec
    except Exception as e:  # pragma: no cover — CI-only skip
        pytest.skip(f"codec import failed (likely pynput/native dep): {e}")


# ── A-21: dead AGENT_NAME removed ────────────────────────────────────────────


def test_agent_name_constant_removed():
    import codec_config
    assert not hasattr(codec_config, "AGENT_NAME"), (
        "AGENT_NAME was dead (declared, never read) and must be removed (A-21)"
    )


def test_assistant_name_and_user_name_kept():
    """ASSISTANT_NAME + USER_NAME ARE used elsewhere — must NOT be removed."""
    import codec_config
    assert hasattr(codec_config, "ASSISTANT_NAME")
    assert hasattr(codec_config, "USER_NAME")


# ── A-17: draft_keywords config wiring ───────────────────────────────────────


def test_is_draft_matches_builtin_keyword():
    import codec_core
    assert codec_core.is_draft("draft a reply to this email") is True
    assert codec_core.is_draft("rephrase that") is True


def test_is_draft_false_for_non_draft():
    import codec_core
    assert codec_core.is_draft("what's the weather in Paris") is False


def test_is_draft_honors_user_configured_keywords(monkeypatch):
    """A-17: a user-supplied draft_keywords entry must now take effect."""
    import codec_config
    import codec_core
    # Inject a custom keyword that's NOT in the built-in list
    monkeypatch.setitem(codec_config.cfg, "draft_keywords", ["yo translate"])
    assert codec_core.is_draft("yo translate this into French") is True
    # And a phrase NOT configured + not built-in stays False
    assert codec_core.is_draft("blorptastic gibberish") is False


def test_is_draft_tolerates_malformed_config(monkeypatch):
    import codec_config
    import codec_core
    monkeypatch.setitem(codec_config.cfg, "draft_keywords", [None, 123, "  ", "valid_kw"])
    # Malformed entries ignored; valid one works; no crash
    assert codec_core.is_draft("please valid_kw now") is True
    assert codec_core.is_draft("nothing here") is False


def test_draft_keywords_cfg_removed_from_codec():
    """The dead `DRAFT_KEYWORDS_CFG = _cfg.get(...)` line in codec.py is gone
    (wiring moved into codec_core.is_draft)."""
    src = (REPO / "codec.py").read_text()
    assert "DRAFT_KEYWORDS_CFG = _cfg.get" not in src


# ── A-16: wake-phrase config wiring ──────────────────────────────────────────


def test_wake_keyword_defaults_deduped():
    """The old inline list had 'kodak' twice; the new tuple must be dedup'd."""
    codec = _import_codec()
    kws = codec._WAKE_KEYWORD_DEFAULTS
    assert len(kws) == len(set(kws)), f"_WAKE_KEYWORD_DEFAULTS has duplicates: {kws}"
    assert "kodak" in kws  # still present, just once


def test_is_wake_matches_homophone_keywords():
    codec = _import_codec()
    for text in ("hey codec", "okay codex", "kodak listen", "hey co-dec"):
        assert codec._is_wake_utterance(text) is True, f"should wake: {text!r}"


def test_is_wake_false_on_ordinary_speech():
    codec = _import_codec()
    for text in ("what's the weather today", "add this to the list", "send an email"):
        assert codec._is_wake_utterance(text) is False, f"must NOT wake: {text!r}"


def test_is_wake_honors_custom_wake_phrases(monkeypatch):
    """A-16: a user-customized WAKE_PHRASES entry must trigger the matcher.
    Before PR-3C the matcher ignored WAKE_PHRASES entirely."""
    codec = _import_codec()
    monkeypatch.setattr(codec, "WAKE_PHRASES", ["jarvis online", "hey"])
    assert codec._is_wake_utterance("ok jarvis online please") is True
    # The generic short "hey" entry (<5 chars) must NOT false-trigger
    assert codec._is_wake_utterance("hey there friend") is False


def test_is_wake_empty_input_safe():
    codec = _import_codec()
    assert codec._is_wake_utterance("") is False
    assert codec._is_wake_utterance(None) is False


def test_codec_source_no_longer_hardcodes_inline_wake_list():
    """The inline `_WAKE_KEYWORDS = [...]` with the duplicate 'kodak' must be
    gone; matcher routes through _is_wake_utterance (which reads WAKE_PHRASES)."""
    src = (REPO / "codec.py").read_text()
    assert '_WAKE_KEYWORDS = ["codec"' not in src
    assert "_is_wake_utterance" in src
    assert "WAKE_PHRASES" in src  # now imported + used
