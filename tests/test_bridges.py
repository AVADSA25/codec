"""Tests for PR-3F — A-19: codec_bridges shared outbound-bridge core.

Extracts the genuinely-identical helpers shared by codec_telegram + codec_imessage:
load_dispatch / try_skill (skill dispatch), call_llm (canonical bridge LLM call
via codec_llm, channel-selected persona), save_to_memory (memory.db write). Each
bridge keeps its own (drifted) process_message. Reference: docs/PR3F-BRIDGE-UNIFICATION-DESIGN.md.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import codec_bridges  # noqa: E402
import codec_llm  # noqa: E402


def _cfg(api_key="", kwargs=None):
    return {"base_url": "http://x/v1", "model": "m",
            "api_key": api_key, "kwargs": kwargs or {}}


# ── call_llm (channel persona + None contract + kwargs filter) ─────────────────


def test_call_llm_telegram_persona(monkeypatch):
    cap = {}
    monkeypatch.setattr(codec_llm, "call",
                        lambda messages, **k: cap.update(messages=messages, **k) or "reply")
    out = codec_bridges.call_llm("telegram", "hi", _cfg())
    assert out == "reply"
    assert "via Telegram" in cap["messages"][0]["content"]
    assert cap["messages"][-1] == {"role": "user", "content": "hi"}


def test_call_llm_imessage_persona(monkeypatch):
    cap = {}
    monkeypatch.setattr(codec_llm, "call",
                        lambda messages, **k: cap.update(messages=messages) or "r")
    codec_bridges.call_llm("imessage", "hi", _cfg())
    assert "via iMessage" in cap["messages"][0]["content"]


def test_call_llm_none_on_empty(monkeypatch):
    monkeypatch.setattr(codec_llm, "call", lambda *a, **k: "")
    assert codec_bridges.call_llm("telegram", "hi", _cfg()) is None


def test_call_llm_filters_chat_template_kwargs(monkeypatch):
    cap = {}
    monkeypatch.setattr(codec_llm, "call", lambda messages, **k: cap.update(k) or "r")
    codec_bridges.call_llm("telegram", "hi", _cfg(api_key="K", kwargs={
        "top_p": 0.9, "chat_template_kwargs": {"enable_thinking": True}}))
    assert cap["extra_kwargs"] == {"top_p": 0.9}
    assert cap["api_key"] == "K"


def test_call_llm_history_and_override(monkeypatch):
    cap = {}
    monkeypatch.setattr(codec_llm, "call", lambda messages, **k: cap.update(messages=messages) or "r")
    hist = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "ok"}]
    codec_bridges.call_llm("telegram", "now", _cfg(), conversation_history=hist,
                           system_prompt_override="SYS")
    assert cap["messages"][0] == {"role": "system", "content": "SYS"}
    assert cap["messages"][1:] == hist + [{"role": "user", "content": "now"}]


# ── try_skill (skip set + result) ─────────────────────────────────────────────


def test_try_skill_skips_blocked(monkeypatch):
    monkeypatch.setattr(codec_bridges, "_dispatch_loaded", True)
    monkeypatch.setattr(codec_bridges, "_check_skill", lambda t: {"name": "open_terminal"})
    monkeypatch.setattr(codec_bridges, "_run_skill", lambda s, t: "should not run")
    assert codec_bridges.try_skill("open a terminal") == (None, None)


def test_try_skill_runs_allowed(monkeypatch):
    monkeypatch.setattr(codec_bridges, "_dispatch_loaded", True)
    monkeypatch.setattr(codec_bridges, "_check_skill", lambda t: {"name": "weather"})
    monkeypatch.setattr(codec_bridges, "_run_skill", lambda s, t: "sunny")
    assert codec_bridges.try_skill("weather?") == ("weather", "sunny")


def test_try_skill_no_match(monkeypatch):
    monkeypatch.setattr(codec_bridges, "_dispatch_loaded", True)
    monkeypatch.setattr(codec_bridges, "_check_skill", lambda t: None)
    monkeypatch.setattr(codec_bridges, "_run_skill", lambda s, t: "x")
    assert codec_bridges.try_skill("chitchat") == (None, None)


# ── save_to_memory (channel-prefixed session_id) ──────────────────────────────


def test_save_to_memory(monkeypatch, tmp_path):
    db = tmp_path / "memory.db"
    monkeypatch.setattr(codec_bridges, "MEMORY_DB", str(db))
    codec_bridges.save_to_memory("telegram", "12345", "hello", "hi there")
    rows = sqlite3.connect(str(db)).execute(
        "SELECT session_id, role, content FROM conversations ORDER BY id").fetchall()
    assert rows == [
        ("telegram-12345", "user", "hello"),
        ("telegram-12345", "assistant", "hi there"),
    ]


# ── source-level migration invariants ─────────────────────────────────────────


def test_bridges_use_codec_bridges():
    for mod in ("codec_telegram.py", "codec_imessage.py"):
        src = (REPO / mod).read_text()
        assert "codec_bridges" in src, f"{mod} doesn't use codec_bridges"
        # the duplicated skill-dispatch internals are gone
        assert "from codec_dispatch import check_skill, run_skill" not in src, f"{mod} still has its own dispatch loader"
