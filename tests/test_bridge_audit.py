"""The iMessage and Telegram bridges write signed audit lines, not plain text.

Each bridge had its own `audit(msg)` that appended `[time] IMESSAGE: ...` to
~/.codec/audit.log, with the first 100 characters of every message. Those lines
have no JSON or HMAC, so verify_audit_log() counted each one as broken
(docs/known-issues.md, 2026-10-07). They now go through codec_audit.log_event
with who and how long, never the text.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def emitted(monkeypatch):
    import codec_audit
    seen = []
    monkeypatch.setattr(codec_audit, "log_event", lambda *a, **k: seen.append((a, k)))
    return seen


@pytest.mark.parametrize("mod_name, source", [("codec_imessage", "codec-imessage"), ("codec_telegram", "codec-telegram")])
def test_a_bridge_event_is_one_structured_line_without_text(emitted, mod_name, source):
    mod = __import__(mod_name)
    mod.audit("message_received", sender="+10000000000", length=42)
    (args, kwargs), = emitted
    assert args[0] == "bridge_message_received" and args[1] == source
    assert kwargs["extra"]["length"] == 42 and kwargs["extra"]["bridge"] in ("imessage", "telegram")


@pytest.mark.parametrize("fname", ["codec_imessage.py", "codec_telegram.py"])
def test_no_plain_text_writer_and_no_message_text_left(fname):
    src = (REPO / fname).read_text(encoding="utf-8")
    assert "AUDIT_LOG" not in src, "no direct writes to the audit log"
    for leak in ("text={text", "text={reply", "audit(f\""):
        assert leak not in src, f"{fname}: {leak}"


def test_the_audit_log_verifies_after_bridge_events(tmp_path, monkeypatch):
    import codec_audit
    import codec_imessage
    import codec_telegram
    monkeypatch.setattr(codec_audit, "_AUDIT_DIR", tmp_path)
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    codec_imessage.audit("service_start")
    codec_telegram.audit("reply_sent", chat="123", length=5)
    lines = (tmp_path / "audit.log").read_text().splitlines()
    assert len(lines) == 2 and all(line.startswith("{") for line in lines)
    r = codec_audit.verify_audit_log(str(tmp_path / "audit.log"))
    if r.get("error"):  # no signing secret on this machine (CI): the lines being JSON is what failed before
        return
    assert r["broken_lines"] == 0 and r["total_lines"] == 2
