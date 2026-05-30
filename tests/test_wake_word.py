"""Wake-word detection unit tests (B3 / SR-22).

CODEC's wake path was unprotected by unit tests despite being the
highest-traffic security-relevant code (any matched utterance auto-
dispatches a skill or LLM turn). These tests pin:

  - The homophone keyword set at codec.py:_WAKE_KEYWORD_DEFAULTS
  - The two-layer match in _is_wake_utterance (homophone OR configured)
  - The ≥5-char gate that filters out 3-char default phrases like "hey"
  - Case-insensitivity
  - Anti-false-wake behavior on bare "hey" / short noise tokens
"""

import pytest


def _wake(text):
    """Call _is_wake_utterance with the live WAKE_PHRASES global."""
    from codec import _is_wake_utterance
    return _is_wake_utterance(text)


class TestHomophoneKeywords:
    """The hardcoded `_WAKE_KEYWORD_DEFAULTS` set catches close-sounding
    transcriptions of the CODEC keyword."""

    @pytest.mark.parametrize("phrase", [
        "hey codec",
        "hey CODEC",
        "Hey Codec",
        "okay codec",
        "hello codec",
        "hey codex",       # Whisper substitution
        "hey kodak",       # Whisper substitution
        "hey kodec",
        "hey co-dec",
        "hey caudec",
    ])
    def test_homophone_match(self, phrase):
        assert _wake(phrase) is True, (
            f"Wake homophone should match: {phrase!r}")


class TestAntiFalseWake:
    """Verify the matcher doesn't fire on conversational noise."""

    @pytest.mark.parametrize("phrase", [
        "hey",                  # 3 chars — should be filtered by ≥5-char gate
        "yo",                   # not a wake variant
        "the cat",              # unrelated
        "what time is it",      # legitimate query, no wake keyword
        "",
        " ",
        "completely unrelated phrase",
    ])
    def test_no_match_on_noise(self, phrase):
        assert _wake(phrase) is False, (
            f"Should not wake on noise: {phrase!r}")


class TestCaseInsensitivity:
    """Wake matching is case-insensitive at every layer."""

    @pytest.mark.parametrize("variant", [
        "HEY CODEC",
        "Hey Codec",
        "hey codec",
        "HeY cOdEc",
    ])
    def test_case_variants_all_match(self, variant):
        assert _wake(variant) is True


class TestFiveCharGate:
    """Configured WAKE_PHRASES under 5 chars must NOT auto-wake.

    This is the documented anti-false-wake defense at codec.py:71. A
    config like WAKE_PHRASES=['hey'] would otherwise fire on every
    conversational "hey" the user says.
    """

    def test_3char_phrase_ignored_via_gate(self, monkeypatch):
        """Even with a 3-char phrase in WAKE_PHRASES, bare 'hey' must not
        match. The gate runs inside _is_wake_utterance."""
        import codec
        # Inject a 3-char phrase and verify the gate filters it.
        monkeypatch.setattr(codec, "WAKE_PHRASES", ["hey"])
        # Bare "hey" must not match: 3 chars < 5, AND no homophone hit.
        assert codec._is_wake_utterance("hey there") is False
        assert codec._is_wake_utterance("hey") is False

    def test_5char_phrase_matches(self, monkeypatch):
        """A 5-char wake phrase configured by the user MUST match."""
        import codec
        monkeypatch.setattr(codec, "WAKE_PHRASES", ["hello"])
        assert codec._is_wake_utterance("hello world") is True
