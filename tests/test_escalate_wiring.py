"""Step 10 Q11 wiring (2026-07): post-reply Project-promotion suggestion.

The regex prefilter must keep the Qwen classifier off casual messages;
the silence endpoint must mark the session."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import routes.chat as chat


def test_prefilter_skips_short_or_casual(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("classifier must not be called for casual text")
    monkeypatch.setattr(chat, "_should_escalate_to_project", boom)
    assert chat._maybe_escalate_suggestion("hi", "s1") is None
    assert chat._maybe_escalate_suggestion("what's the weather like today?" * 3, "s1") is None


def test_prefilter_passes_task_shaped_text(monkeypatch):
    calls = {}
    def fake_gate(text, sid):
        calls["hit"] = (text, sid)
        return {"escalate": True, "estimated_checkpoints": 4, "reason": "multi-step"}
    monkeypatch.setattr(chat, "_should_escalate_to_project", fake_gate)
    monkeypatch.setattr(chat, "log_event", lambda *a, **kw: None)
    out = chat._maybe_escalate_suggestion(
        "research the top 5 competitors in my niche, build a comparison and prepare a report",
        "sess42")
    assert out == {"estimated_checkpoints": 4, "reason": "multi-step"}
    assert calls["hit"][1] == "sess42"


def test_gate_negative_verdict_returns_none(monkeypatch):
    monkeypatch.setattr(chat, "_should_escalate_to_project",
                        lambda t, s: {"escalate": False, "reason": "single-step"})
    assert chat._maybe_escalate_suggestion(
        "build me one tiny thing that is actually simple but worded long enough", "s") is None


def test_gate_never_raises(monkeypatch):
    monkeypatch.setattr(chat, "_should_escalate_to_project",
                        lambda t, s: (_ for _ in ()).throw(RuntimeError("qwen down")))
    assert chat._maybe_escalate_suggestion(
        "research and build and prepare a giant multi step plan for my business", "s") is None
