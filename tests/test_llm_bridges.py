"""Tests for PR-3E-bridges — A-12: migrate iMessage + Telegram call_llm.

Both `call_llm` text sites now route through codec_llm.call (default never-raise).
The bridge contract — return None on any failure/empty for graceful degradation
— is preserved via `content if content else None`. `chat_template_kwargs` is
filtered out of llm_cfg["kwargs"] so enable_thinking=False survives.

Reference: docs/PR3E-BRIDGES-DESIGN.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import codec_llm  # noqa: E402


def _cfg(api_key="", kwargs=None):
    return {"base_url": "http://x/v1", "model": "m",
            "api_key": api_key, "kwargs": kwargs or {}}


# ── telegram ──────────────────────────────────────────────────────────────────


def test_telegram_call_llm_returns_content(monkeypatch):
    import codec_telegram
    monkeypatch.setattr(codec_llm, "call", lambda *a, **k: "tg reply")
    assert codec_telegram.call_llm("hi", _cfg()) == "tg reply"


def test_telegram_call_llm_none_on_empty(monkeypatch):
    import codec_telegram
    monkeypatch.setattr(codec_llm, "call", lambda *a, **k: "")
    assert codec_telegram.call_llm("hi", _cfg()) is None


def test_telegram_call_llm_passes_params_and_filters_kwargs(monkeypatch):
    import codec_telegram
    cap = {}

    def fake(messages, **k):
        cap["messages"] = messages
        cap.update(k)
        return "ok"

    monkeypatch.setattr(codec_llm, "call", fake)
    codec_telegram.call_llm("hi", _cfg(api_key="K", kwargs={
        "top_p": 0.9, "chat_template_kwargs": {"enable_thinking": True}}))
    assert cap["base_url"] == "http://x/v1" and cap["model"] == "m"
    assert cap["api_key"] == "K"
    assert cap["extra_kwargs"] == {"top_p": 0.9}      # chat_template_kwargs filtered
    assert cap["messages"][-1] == {"role": "user", "content": "hi"}


# ── imessage (call_llm has a `sender` 2nd positional arg) ──────────────────────


def test_imessage_call_llm_returns_content(monkeypatch):
    import codec_imessage
    monkeypatch.setattr(codec_llm, "call", lambda *a, **k: "im reply")
    assert codec_imessage.call_llm("hi", "+1", _cfg()) == "im reply"


def test_imessage_call_llm_none_on_empty(monkeypatch):
    import codec_imessage
    monkeypatch.setattr(codec_llm, "call", lambda *a, **k: "")
    assert codec_imessage.call_llm("hi", "+1", _cfg()) is None


def test_imessage_call_llm_filters_kwargs(monkeypatch):
    import codec_imessage
    cap = {}

    def fake(messages, **k):
        cap.update(k)
        return "ok"

    monkeypatch.setattr(codec_llm, "call", fake)
    codec_imessage.call_llm("hi", "+1", _cfg(api_key="K", kwargs={
        "top_p": 0.5, "chat_template_kwargs": {"enable_thinking": True}}))
    assert cap["extra_kwargs"] == {"top_p": 0.5} and cap["api_key"] == "K"


# ── source-level migration invariants ─────────────────────────────────────────


def test_telegram_uses_codec_llm():
    # PR-3F (A-19): the LLM call moved into codec_bridges (which calls codec_llm).
    # The bridge now delegates via codec_bridges.call_llm; only the vision POST
    # (llm_cfg["vision_url"]) remains inline.
    src = (REPO / "codec_telegram.py").read_text()
    assert "codec_bridges" in src
    assert src.count("/chat/completions") == 1   # only the vision site remains


def test_imessage_uses_codec_llm():
    src = (REPO / "codec_imessage.py").read_text()
    assert "codec_bridges" in src
    assert src.count("/chat/completions") == 1
