"""Tests for skills/imessage_send.py recipient validation (D-13 closure).

Closes audit finding D-13 (MEDIUM) — AppleScript injection via the
`recipient` field. The skill is `SKILL_MCP_EXPOSE=True` and reachable
from claude.ai over MCP HTTP, so an attacker controlling MCP calls
could craft a recipient that breaks out of the AppleScript string
literal context and executes arbitrary AppleScript.

PR-2F gates the AppleScript surface entirely: `_validate_recipient`
rejects any string that isn't a valid phone (E.164) or email.
Audit emit `imessage_send_blocked` fires on every refusal.

Reference: docs/audits/PHASE-1-SECURITY.md finding D-13.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "skills"))

import imessage_send  # noqa: E402


# ── _validate_recipient tests ───────────────────────────────────────────────


@pytest.mark.parametrize("good", [
    "+15551234567",
    "+34612345678",
    "+447911123456",
    "+819012345678",
    "5551234567",        # without leading +
    "user@example.com",
    "first.last+tag@example.co.uk",
    "mickael@avadigital.ai",
])
def test_validate_recipient_accepts_legitimate(good):
    """Phone numbers (E.164-ish) and emails must pass."""
    assert imessage_send._validate_recipient(good), f"Should accept: {good!r}"


@pytest.mark.parametrize("bad", [
    # D-13 audit's documented breakout
    'xx@x.com" of targetService\nactivate application "Calculator"\nset targetBuddy to buddy "yy@y.com',
    # Quotes
    '"; activate application "Finder";"',
    # Newlines + AppleScript keyword
    "a@b.com\ntell application",
    # Carriage return
    "a@b.com\rtell",
    # Tab
    "a@b.com\ttell",
    # Backslash escape
    "a@b.com\\u0022 of targetService",
    # AppleScript identifier breakout
    '" of (1st account)\ntell',
    # Empty / whitespace
    "",
    "   ",
    # Just metachars
    '""',
    "tell application Calculator",
    # Spaces
    "+1 555 123 4567",
    # Pure text
    "mom",
    # Phone too short
    "+1234",
    # Phone with non-digits
    "+1-555-CALL-NOW",
])
def test_validate_recipient_rejects_metachar_and_invalid(bad):
    """All AppleScript-injection-capable inputs must be refused."""
    assert not imessage_send._validate_recipient(bad), (
        f"Should reject: {bad!r}"
    )


def test_validate_recipient_rejects_overlength():
    """Strings over 254 chars are refused even if they'd otherwise look
    email-ish. RFC 5321 SMTP path limit."""
    long_email = "a" * 250 + "@b.com"  # 256 chars
    assert not imessage_send._validate_recipient(long_email)


def test_validate_recipient_rejects_non_string():
    """None / int / bytes must not crash the validator."""
    assert not imessage_send._validate_recipient(None)
    # `int` isn't a string at all → reject without raising
    assert not imessage_send._validate_recipient(12345)
    assert not imessage_send._validate_recipient(b"+15551234567")


# ── _escape_text tests ──────────────────────────────────────────────────────


def test_escape_text_handles_all_metachars():
    """The text body still goes through string interpolation in the
    AppleScript; the escape MUST cover \\, \", \r, \n, \t. Order matters
    — backslash first."""
    raw = 'line1\\backslash\n"quote"\r\ttab'
    escaped = imessage_send._escape_text(raw)
    # Backslash escaped first
    assert "\\\\" in escaped
    # Quote escaped
    assert '\\"' in escaped
    # Newline + carriage return + tab escaped
    assert "\\n" in escaped
    assert "\\r" in escaped
    assert "\\t" in escaped
    # Raw control chars must NOT appear in escaped string
    assert "\n" not in escaped
    assert "\r" not in escaped
    assert "\t" not in escaped


def test_escape_text_idempotent_safe():
    """Re-escaping an already-escaped string doubles slashes but doesn't
    crash. Pin behavior."""
    escaped_once = imessage_send._escape_text("hello\nworld")
    escaped_twice = imessage_send._escape_text(escaped_once)
    assert "\\\\n" in escaped_twice  # the \n escape itself was escaped


# ── _send refusal + audit emit ──────────────────────────────────────────────


def test_send_refuses_invalid_recipient_no_subprocess_call(monkeypatch):
    """When recipient is invalid, `_send` must NOT call osascript at all.
    Defense-in-depth: even if validation falsely accepts, subprocess
    shouldn't run for a non-validating input. (Audit's Keychain shellout
    to /usr/bin/security is fine — only osascript must be blocked.)"""
    osascript_calls = {"count": 0}
    real_run = imessage_send.subprocess.run

    class _Result:
        returncode = 1
        stdout = ""
        stderr = ""

    def fake_run(args, **kwargs):
        if args and isinstance(args, (list, tuple)) and "osascript" in str(args[0]):
            osascript_calls["count"] += 1
            raise AssertionError(
                f"osascript called with invalid recipient: {args!r}"
            )
        return _Result()

    monkeypatch.setattr(imessage_send.subprocess, "run", fake_run)
    try:
        result = imessage_send._send(
            '"; activate application "Calculator";"', "hi",
        )
    finally:
        monkeypatch.setattr(imessage_send.subprocess, "run", real_run)
    assert result is False
    assert osascript_calls["count"] == 0, (
        "osascript must not be called for invalid recipient"
    )


def test_send_emits_blocked_audit_on_invalid(monkeypatch):
    """Refusal must emit `imessage_send_blocked` audit event."""
    captured = []

    def fake_log_event(event_type, *args, **kwargs):
        captured.append({"event_type": event_type, "args": args, "kwargs": kwargs})

    monkeypatch.setattr("codec_audit.log_event", fake_log_event)
    imessage_send._send(
        'xx@x.com" of targetService\nactivate application "Calculator', "x",
    )
    matches = [c for c in captured if c["event_type"] == "imessage_send_blocked"]
    assert len(matches) == 1, f"Expected one blocked event; got {captured!r}"
    extra = matches[0]["kwargs"].get("extra", {})
    assert extra.get("reason") == "invalid_recipient_format"
    assert "recipient_preview" in extra
    # Preview must be capped at 32 chars so adversarial multi-line input
    # can't bloat the audit log
    assert len(extra["recipient_preview"]) <= 32


def test_send_accepts_legitimate_recipient_calls_subprocess(monkeypatch):
    """Validated recipient → osascript IS called. Sanity test that the
    validation gate doesn't block legitimate sends."""
    called = {"count": 0, "last_args": None}

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **kwargs):
        called["count"] += 1
        called["last_args"] = args
        return _Result()

    monkeypatch.setattr(imessage_send.subprocess, "run", fake_run)
    result = imessage_send._send("+15551234567", "hello")
    assert result is True
    assert called["count"] >= 1
    # The osascript invocation must contain the validated recipient AND
    # the escaped message body
    assert "osascript" in called["last_args"][0]


# ── run() integration ───────────────────────────────────────────────────────


def test_run_refuses_invalid_recipient_with_user_message(monkeypatch):
    """`run()` returns an operator-readable refusal for invalid recipient.
    Skill is MCP-exposed so the message goes back to claude.ai if used
    remotely."""
    monkeypatch.setattr("codec_audit.log_event", lambda *a, **kw: None)
    result = imessage_send.run(
        'recipient: "; tell app "Finder" to delete; " | body: hi'
    )
    assert "Refused" in result or "valid" in result.lower()


def test_validate_recipient_anchor_prevents_prefix_match():
    """The regex is anchored — `+15551234567 extra junk` must fail because
    of the trailing junk, not be a prefix match."""
    assert not imessage_send._validate_recipient("+15551234567 extra")
    assert not imessage_send._validate_recipient("user@example.com tell application")
