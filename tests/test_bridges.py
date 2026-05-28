"""Tests for PR-3F — A-19: codec_bridges shared outbound-bridge core.

Extracts the genuinely-identical helpers shared by codec_telegram + codec_imessage:
load_dispatch / try_skill (skill dispatch), call_llm (canonical bridge LLM call
via codec_llm, channel-selected persona), save_to_memory (memory.db write). Each
bridge keeps its own (drifted) process_message. Reference: docs/PR3F-BRIDGE-UNIFICATION-DESIGN.md.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import codec_bridges  # noqa: E402
import codec_imessage  # noqa: E402
import codec_llm  # noqa: E402
import codec_telegram  # noqa: E402


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


# ── Fix #2 (C2): fail-closed inbound allowlists ───────────────────────────────
# Before: an EMPTY allowed_chat_ids / allowed_senders meant "allow all" — anyone
# who learned the bot token (telegram) or the handle (imessage) could drive
# CODEC. The secure default is fail-closed: empty allowlist denies everyone until
# the operator explicitly adds their own id/handle.


# iMessage — is_sender_allowed(sender, im_cfg)


def test_imessage_empty_allowlist_denies():
    """C2: empty allowed_senders must DENY all inbound (was allow-all)."""
    assert codec_imessage.is_sender_allowed(
        "+15551234567", {"allowed_senders": []}) is False


def test_imessage_missing_allowlist_denies():
    """C2: allowlist key entirely absent → deny (fail-closed default)."""
    assert codec_imessage.is_sender_allowed("+15551234567", {}) is False


def test_imessage_listed_sender_allowed():
    assert codec_imessage.is_sender_allowed(
        "+15551234567", {"allowed_senders": ["+15551234567"]}) is True


def test_imessage_unlisted_sender_denied():
    assert codec_imessage.is_sender_allowed(
        "+19998887777", {"allowed_senders": ["+15551234567"]}) is False


def test_imessage_blocked_still_denied():
    """Blocklist still wins even if the sender is also in the allowlist."""
    assert codec_imessage.is_sender_allowed("+15551234567", {
        "allowed_senders": ["+15551234567"],
        "blocked_senders": ["+15551234567"],
    }) is False


def test_imessage_empty_allowlist_warns_once(caplog):
    """C2: denying on empty allowlist logs ONE bridge_no_allowlist warning
    (not one per message — that would spam the log)."""
    codec_imessage._warned_no_allowlist = False  # reset the one-time flag
    with caplog.at_level(logging.WARNING):
        codec_imessage.is_sender_allowed("+1", {"allowed_senders": []})
        codec_imessage.is_sender_allowed("+2", {"allowed_senders": []})
    hits = [r for r in caplog.records if "bridge_no_allowlist" in r.getMessage()]
    assert len(hits) == 1, f"expected exactly one warning, got {len(hits)}"


# Telegram — is_chat_allowed(chat_id, tg_cfg)


def test_telegram_empty_allowlist_denies():
    """C2: empty allowed_chat_ids must DENY all inbound (was allow-all)."""
    assert codec_telegram.is_chat_allowed(12345, {"allowed_chat_ids": []}) is False


def test_telegram_missing_allowlist_denies():
    assert codec_telegram.is_chat_allowed(12345, {}) is False


def test_telegram_listed_chat_allowed():
    assert codec_telegram.is_chat_allowed(12345, {"allowed_chat_ids": [12345]}) is True


def test_telegram_unlisted_chat_denied():
    assert codec_telegram.is_chat_allowed(999, {"allowed_chat_ids": [12345]}) is False


def test_telegram_empty_allowlist_warns_once(caplog):
    codec_telegram._warned_no_allowlist = False  # reset the one-time flag
    with caplog.at_level(logging.WARNING):
        codec_telegram.is_chat_allowed(1, {"allowed_chat_ids": []})
        codec_telegram.is_chat_allowed(2, {"allowed_chat_ids": []})
    hits = [r for r in caplog.records if "bridge_no_allowlist" in r.getMessage()]
    assert len(hits) == 1, f"expected exactly one warning, got {len(hits)}"


def test_telegram_process_message_wired_to_gate():
    """The inline allow-all guard in process_message is replaced by the
    fail-closed helper. Source-level invariant (mirrors the existing migration
    guard above)."""
    src = (REPO / "codec_telegram.py").read_text()
    assert "is_chat_allowed(" in src, "process_message must call is_chat_allowed"
    # the old allow-all pattern must be gone
    assert "if allowed and chat_id not in allowed:" not in src


# ── Fix #2 (C2): BRIDGE_SAFE_SKILLS allowlist in try_skill ────────────────────
# High-power skills must NEVER be reachable from a (remote) bridge, even if the
# dispatcher matched one. The allowlist is default-deny: a non-listed skill is
# simply not dispatched (the bridge degrades to an LLM answer — never a hard
# fail), and the 7 dangerous skills are excluded by construction.

_DANGEROUS_BRIDGE_SKILLS = [
    "terminal", "python_exec", "file_write", "pilot",
    "process_manager", "pm2_control", "ax_control",
]


@pytest.mark.parametrize("dangerous", _DANGEROUS_BRIDGE_SKILLS)
def test_try_skill_blocks_dangerous(monkeypatch, dangerous):
    """C2: a dangerous skill is never run from a bridge even when matched."""
    monkeypatch.setattr(codec_bridges, "_dispatch_loaded", True)
    monkeypatch.setattr(codec_bridges, "_check_skill", lambda t: {"name": dangerous})
    monkeypatch.setattr(
        codec_bridges, "_run_skill",
        lambda s, t: pytest.fail(f"{dangerous} must not run from a bridge"))
    assert codec_bridges.try_skill("do the dangerous thing") == (None, None)


def test_try_skill_allows_safe_listed(monkeypatch):
    """A safe read/info skill on the allowlist still runs."""
    monkeypatch.setattr(codec_bridges, "_dispatch_loaded", True)
    monkeypatch.setattr(codec_bridges, "_check_skill", lambda t: {"name": "weather"})
    monkeypatch.setattr(codec_bridges, "_run_skill", lambda s, t: "sunny")
    assert codec_bridges.try_skill("weather?") == ("weather", "sunny")


def test_try_skill_unlisted_safe_skill_not_dispatched(monkeypatch):
    """A skill that isn't on BRIDGE_SAFE_SKILLS degrades to (None, None) so the
    bridge falls back to an LLM answer — not a hard break."""
    monkeypatch.setattr(codec_bridges, "_dispatch_loaded", True)
    monkeypatch.setattr(codec_bridges, "_check_skill", lambda t: {"name": "google_gmail"})
    monkeypatch.setattr(
        codec_bridges, "_run_skill",
        lambda s, t: pytest.fail("unlisted skill must not run from a bridge"))
    assert codec_bridges.try_skill("send an email") == (None, None)


def test_bridge_safe_skills_excludes_dangerous():
    """The allowlist must contain NONE of the 7 dangerous skills."""
    for d in _DANGEROUS_BRIDGE_SKILLS:
        assert d not in codec_bridges.BRIDGE_SAFE_SKILLS
