"""Tests for PR-3D-b (A-5) — helpers extracted from codec._dispatch_inner.

Behavior-preserving extraction of the voice-dispatch monolith:
- codec._build_voice_system_prompt(task) -> str   (memory/identity/facts assembly)
- codec._persist_voice_turn(task, answer, rid)     (session + DB + CodecMemory save)

The voice path is exercised end-to-end elsewhere; these pin the extracted units.
Reference: docs/PR3D-MONOLITH-EXTRACT-DESIGN.md.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import codec  # noqa: E402


class _FakeCM:
    def get_context(self, task, n=5):
        return ""

    def search_recent(self, days=3, limit=5):
        return []

    def save(self, *a):
        pass


def _silence_memory(monkeypatch):
    """Make every memory source empty so _build_voice_system_prompt returns just
    the base prompt — individual tests then re-enable one source."""
    import codec_memory
    import codec_memory_upgrade
    monkeypatch.setattr(codec, "get_memory", lambda n=5: "")
    monkeypatch.setattr(codec_memory, "CodecMemory", _FakeCM)
    monkeypatch.setattr(codec_memory_upgrade, "load_identity", lambda: "")
    monkeypatch.setattr(codec_memory_upgrade, "query_valid_facts", lambda limit=20: [])
    monkeypatch.setattr(codec_memory_upgrade, "compress_rule_based", lambda x: x)


# ── _build_voice_system_prompt ────────────────────────────────────────────────


def test_build_voice_system_prompt_base(monkeypatch):
    _silence_memory(monkeypatch)
    out = codec._build_voice_system_prompt("hi")
    expected = codec.CODEC_VOICE_PROMPT.format(
        date=datetime.now().strftime("%A, %B %d, %Y"))
    assert out == expected


def test_build_voice_system_prompt_includes_get_memory(monkeypatch):
    _silence_memory(monkeypatch)
    monkeypatch.setattr(codec, "get_memory", lambda n=5: "MEMBLOCK123")
    assert "MEMBLOCK123" in codec._build_voice_system_prompt("hi")


def test_build_voice_system_prompt_includes_facts(monkeypatch):
    _silence_memory(monkeypatch)
    import codec_memory_upgrade
    monkeypatch.setattr(codec_memory_upgrade, "query_valid_facts",
                        lambda limit=20: [{"key": "city", "value": "Marbella"}])
    out = codec._build_voice_system_prompt("hi")
    assert "[ACTIVE FACTS]" in out and "city = Marbella" in out


def test_build_voice_system_prompt_includes_identity(monkeypatch):
    _silence_memory(monkeypatch)
    import codec_memory_upgrade
    monkeypatch.setattr(codec_memory_upgrade, "load_identity", lambda: "I am CODEC")
    out = codec._build_voice_system_prompt("hi")
    assert "[IDENTITY — BOOT PAYLOAD]" in out and "I am CODEC" in out


# ── _persist_voice_turn ───────────────────────────────────────────────────────


def test_persist_voice_turn(monkeypatch):
    import codec_memory
    monkeypatch.setattr(codec, "voice_session",
                        {"messages": [], "turn_count": 0, "started": None})
    recorded = {}
    monkeypatch.setattr(codec, "update_session_response",
                        lambda rid, txt: recorded.update(rid=rid, txt=txt))
    saved = []

    class _CM:
        def save(self, *a):
            saved.append(a)

    monkeypatch.setattr(codec_memory, "CodecMemory", lambda: _CM())

    codec._persist_voice_turn("mytask", "myanswer", 42)

    assert codec.voice_session["messages"][-1] == {"role": "assistant", "content": "myanswer"}
    assert codec.voice_session["turn_count"] == 1
    assert recorded == {"rid": 42, "txt": "myanswer"}
    assert ("voice", "user", "mytask") in saved
    assert ("voice", "assistant", "myanswer") in saved


def test_persist_voice_turn_truncates_db_write(monkeypatch):
    import codec_memory
    monkeypatch.setattr(codec, "voice_session",
                        {"messages": [], "turn_count": 0, "started": None})
    recorded = {}
    monkeypatch.setattr(codec, "update_session_response",
                        lambda rid, txt: recorded.update(rid=rid, txt=txt))
    monkeypatch.setattr(codec_memory, "CodecMemory",
                        lambda: type("CM", (), {"save": lambda self, *a: None})())
    long_answer = "z" * 900
    codec._persist_voice_turn("t", long_answer, 7)
    assert len(recorded["txt"]) == 500   # update_session_response gets answer[:500]


# ── source-level migration invariant ──────────────────────────────────────────


def test_dispatch_inner_uses_helpers():
    src = (REPO / "codec.py").read_text()
    assert "_build_voice_system_prompt(" in src
    assert "_persist_voice_turn(" in src
