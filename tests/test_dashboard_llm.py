"""Tests for PR-3E-dashboard — A-12: 3 independent non-stream dashboard sites.

Migrated to codec_llm.call: the auto-escalate classifier (_qwen_chat_classify),
the `command` Flash fallback, and the crew-report writer. The chat-handler
stream + its non-stream fallback (shared payload + keepalive) are a separate PR.

Reference: docs/PR3E-DASHBOARD-DESIGN.md (Option 1).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import codec_dashboard  # noqa: E402
import codec_llm  # noqa: E402


# ── _qwen_chat_classify (clean, module-level, behavior-testable) ───────────────


def test_qwen_chat_classify_uses_codec_llm(monkeypatch):
    monkeypatch.setattr(codec_llm, "call", lambda *a, **k: "CLASSIFIER-OUT")
    assert codec_dashboard._qwen_chat_classify("hello") == "CLASSIFIER-OUT"


def test_qwen_chat_classify_empty_on_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(codec_llm, "call", boom)
    assert codec_dashboard._qwen_chat_classify("hello") == ""


def test_qwen_chat_classify_passes_local_config(monkeypatch):
    cap = {}

    def fake(messages, **k):
        cap["messages"] = messages
        cap.update(k)
        return "{}"

    monkeypatch.setattr(codec_llm, "call", fake)
    codec_dashboard._qwen_chat_classify("classify me")
    from codec_config import QWEN_BASE_URL
    assert cap["base_url"] == QWEN_BASE_URL
    assert cap["temperature"] == 0.1
    assert cap["messages"][-1]["content"] == "classify me"


# ── source-level migration invariants ─────────────────────────────────────────


def test_dashboard_uses_codec_llm_call():
    src = (REPO / "codec_dashboard.py").read_text()
    assert "import codec_llm" in src
    assert src.count("codec_llm.call(") >= 3        # classifier + Flash + crew report
    # the classifier's inline POST (the only QWEN_BASE_URL.rstrip POST) is gone
    assert "QWEN_BASE_URL.rstrip('/')}/chat/completions" not in src


def test_dashboard_chat_pair_and_vision_posts_remain():
    # This tranche migrates 3 of 10 chat/completions sites. The 5 vision sites
    # (A-11) and the 2 chat-handler-pair sites (stream + fallback, deferred to
    # the keepalive PR) stay → 7 inline /chat/completions occurrences remain.
    src = (REPO / "codec_dashboard.py").read_text()
    assert src.count("/chat/completions") == 7
