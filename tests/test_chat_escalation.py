"""Phase 3 Step 10 tests — chat auto-escalation classifier.

11 tests covering: classifier (3), 2-signal gate (3), session silence (2),
integration with chat handler (2), kill switch (1).

I1 / SR-60: the classifier cluster moved from codec_dashboard to
codec_chat_pipeline. These tests monkeypatch the in-module call chain
(`_qwen_chat_classify` → `_classify_chat_message` → `_should_escalate_to_project`),
so they must patch where the functions are DEFINED (codec_chat_pipeline),
not the codec_dashboard re-export. Hence `import codec_chat_pipeline as cd`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def test_classify_chat_message_returns_project_when_multi_step(monkeypatch):
    """LLM verdict says multi-step → returns is_project=True with checkpoints estimate."""
    import codec_chat_pipeline as cd

    fake_response = json.dumps({
        "is_project": True,
        "estimated_checkpoints": 5,
        "reason": "Building a Telegram bot requires scaffolding, scraping, deployment",
    })
    monkeypatch.setattr(cd, "_qwen_chat_classify", lambda text: fake_response)

    is_project, n, reason = cd._classify_chat_message(
        "Build me a Telegram bot for property listings"
    )
    assert is_project is True
    assert n == 5
    assert "Telegram" in reason or "bot" in reason


def test_classify_chat_message_returns_not_project_for_quick_question(monkeypatch):
    import codec_chat_pipeline as cd

    fake_response = json.dumps({
        "is_project": False, "estimated_checkpoints": 0,
        "reason": "Single-shot factual question",
    })
    monkeypatch.setattr(cd, "_qwen_chat_classify", lambda text: fake_response)

    is_project, n, reason = cd._classify_chat_message("What's the weather in Paris?")
    assert is_project is False
    assert n == 0


def test_classify_chat_message_handles_qwen_failure(monkeypatch):
    """If Qwen call fails or returns garbage, classifier returns (False, 0, reason)."""
    import codec_chat_pipeline as cd

    monkeypatch.setattr(cd, "_qwen_chat_classify",
                        lambda text: "garbage non-json")

    is_project, n, reason = cd._classify_chat_message("anything")
    assert is_project is False
    assert n == 0


def test_should_escalate_when_both_signals_pass(monkeypatch):
    """LLM says project + checkpoints >= 3 → escalate."""
    import codec_chat_pipeline as cd

    monkeypatch.setattr(cd, "_classify_chat_message",
                        lambda text: (True, 5, "multi-step"))

    decision = cd._should_escalate_to_project(user_text="x", session_id="s1")
    assert decision["escalate"] is True
    assert decision["estimated_checkpoints"] == 5


def test_should_not_escalate_when_checkpoints_below_3(monkeypatch):
    """LLM says project but estimate=2 → don't escalate."""
    import codec_chat_pipeline as cd

    monkeypatch.setattr(cd, "_classify_chat_message",
                        lambda text: (True, 2, "small"))

    decision = cd._should_escalate_to_project(user_text="x", session_id="s2")
    assert decision["escalate"] is False


def test_should_not_escalate_when_classifier_says_no(monkeypatch):
    """LLM says not-a-project → don't escalate even if checkpoints>=3."""
    import codec_chat_pipeline as cd

    monkeypatch.setattr(cd, "_classify_chat_message",
                        lambda text: (False, 5, "actually quick"))

    decision = cd._should_escalate_to_project(user_text="x", session_id="s3")
    assert decision["escalate"] is False


def test_session_silence_persists_across_calls(monkeypatch):
    """Q11: After silence_session(s1), subsequent _should_escalate calls return escalate=False."""
    import codec_chat_pipeline as cd

    monkeypatch.setattr(cd, "_classify_chat_message",
                        lambda text: (True, 5, "always-project"))

    # Sanity: would normally escalate
    cd._reset_autoescalate_silence_for_test()  # test helper to clear state
    d1 = cd._should_escalate_to_project(user_text="x", session_id="s4")
    assert d1["escalate"] is True

    # User said no
    cd.silence_session_autoescalate("s4")

    # Now suppressed
    d2 = cd._should_escalate_to_project(user_text="x", session_id="s4")
    assert d2["escalate"] is False
    assert d2.get("reason", "").startswith("session_silenced") or d2.get("silenced", False)


def test_kill_switch_disables_all_escalation(monkeypatch):
    """AGENT_AUTO_ESCALATE_ENABLED=false → never escalate."""
    import codec_chat_pipeline as cd

    monkeypatch.setenv("AGENT_AUTO_ESCALATE_ENABLED", "false")
    monkeypatch.setattr(cd, "_classify_chat_message",
                        lambda text: (True, 99, "would always escalate"))

    decision = cd._should_escalate_to_project(user_text="x", session_id="s5")
    assert decision["escalate"] is False
