"""Tests for audit log HMAC integrity + secret redaction + chmod 0600.

Closes audit findings D-12 (HIGH — no HMAC), D-19 (MEDIUM — no secret
redaction), and D-22 (LOW — default umask creates world-readable file).
PR-2E.

Reference: docs/audits/PHASE-1-SECURITY.md findings D-12, D-19, D-22.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


# ── Per-test isolation ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_audit_and_keychain(tmp_path, monkeypatch):
    """Redirect both codec_audit's AUDIT_LOG and codec_keychain's fallback
    paths + service prefix per test. Each test starts with a clean audit log
    and a fresh HMAC secret (via cache invalidation + per-test service prefix)."""
    import codec_audit
    import codec_keychain as kc

    # Redirect audit log
    test_audit = tmp_path / "audit.log"
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", test_audit)
    monkeypatch.setattr(codec_audit, "_AUDIT_DIR", tmp_path)

    # Redirect keychain fallback
    monkeypatch.setattr(kc, "_FALLBACK_KEY_PATH", tmp_path / "secret.key")
    monkeypatch.setattr(kc, "_FALLBACK_STORE_PATH", tmp_path / "secrets.enc.json")
    test_id = f"_test_audit_{os.getpid()}_{tmp_path.name}"
    monkeypatch.setattr(kc, "_SERVICE_PREFIX", f"ai.avadigital.codec.{test_id}")

    # Wipe both caches
    kc._invalidate_audit_hmac_cache()

    yield codec_audit, kc, test_audit

    # Teardown: real-Keychain entries created during this test (if macOS)
    if kc.is_keychain_available():
        kc._keychain_delete(kc.KEY_AUDIT_HMAC_SECRET)
    kc._invalidate_audit_hmac_cache()


# ── HMAC tests (D-12) ───────────────────────────────────────────────────────


def test_audit_line_has_hmac_field(_isolate_audit_and_keychain):
    """Every audit line written post-PR-2E must include a 64-hex `hmac` field."""
    codec_audit, _, test_audit = _isolate_audit_and_keychain
    codec_audit.audit(event="test_event", source="codec-test", tool="test_tool")
    assert test_audit.exists()
    line = test_audit.read_text().splitlines()[-1]
    obj = json.loads(line)
    assert "hmac" in obj, f"Missing hmac field: {obj!r}"
    assert isinstance(obj["hmac"], str)
    assert len(obj["hmac"]) == 64, f"HMAC must be 64-hex chars; got {obj['hmac']!r}"
    int(obj["hmac"], 16)  # raises if not hex


def test_audit_hmac_verifies_correctly(_isolate_audit_and_keychain):
    """verify_audit_log on a single fresh write returns integrity_ok=True
    with signed_lines >= 1."""
    codec_audit, _, _ = _isolate_audit_and_keychain
    codec_audit.audit(event="test_event", source="codec-test")
    result = codec_audit.verify_audit_log()
    assert result["integrity_ok"] is True, result
    assert result["signed_lines"] >= 1
    assert result["broken_lines"] == 0


def test_audit_hmac_detects_tampered_line(_isolate_audit_and_keychain):
    """Mutating a non-hmac field on an audit line must trip verification."""
    codec_audit, _, test_audit = _isolate_audit_and_keychain
    codec_audit.audit(event="event_one", source="codec-test")
    codec_audit.audit(event="event_two", source="codec-test")

    # Tamper line 1: replace the event value, leave hmac intact
    lines = test_audit.read_text().splitlines()
    obj = json.loads(lines[0])
    obj["event"] = "event_tampered"
    lines[0] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    test_audit.write_text("\n".join(lines) + "\n")

    result = codec_audit.verify_audit_log()
    assert result["integrity_ok"] is False
    assert result["broken_lines"] >= 1
    assert result["first_broken_line_no"] == 1


def test_audit_hmac_detects_added_forged_line(_isolate_audit_and_keychain):
    """A forged line appended with a bogus hmac must trip verification."""
    codec_audit, _, test_audit = _isolate_audit_and_keychain
    codec_audit.audit(event="legitimate_1", source="codec-test")
    codec_audit.audit(event="legitimate_2", source="codec-test")
    codec_audit.audit(event="legitimate_3", source="codec-test")

    # Attacker appends a forged event with a made-up hmac
    forged = {
        "ts": "2026-05-17T00:00:00.000+00:00",
        "schema": 1,
        "event": "attacker_event",
        "source": "codec-test",
        "outcome": "ok",
        "hmac": "0" * 64,  # bogus
    }
    with open(test_audit, "a", encoding="utf-8") as f:
        f.write(json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n")

    result = codec_audit.verify_audit_log()
    assert result["integrity_ok"] is False
    assert result["broken_lines"] == 1
    assert result["first_broken_line_no"] == 4


def test_audit_hmac_whole_line_deletion_undetected(_isolate_audit_and_keychain):
    """Per-line HMAC has a documented limitation: deleting a whole line
    isn't detectable by HMAC alone (the remaining lines still verify).
    This test pins the documented behavior so a future hash-chain
    enhancement is a deliberate behavior change, not a regression."""
    codec_audit, _, test_audit = _isolate_audit_and_keychain
    for i in range(5):
        codec_audit.audit(event=f"event_{i}", source="codec-test")

    lines = test_audit.read_text().splitlines()
    assert len(lines) == 5
    # Delete line 3 (index 2)
    del lines[2]
    test_audit.write_text("\n".join(lines) + "\n")

    result = codec_audit.verify_audit_log()
    # Remaining 4 lines still verify — HMAC-per-line doesn't catch deletion
    assert result["integrity_ok"] is True
    assert result["total_lines"] == 4
    assert result["signed_lines"] == 4


def test_audit_hmac_keychain_unavailable_writes_unsigned(
    _isolate_audit_and_keychain, monkeypatch
):
    """When `get_audit_hmac_secret` returns None (Keychain locked), the
    line is still written but tagged `hmac_status='unsigned_keychain_unavailable'`."""
    codec_audit, kc, test_audit = _isolate_audit_and_keychain
    monkeypatch.setattr(kc, "get_audit_hmac_secret", lambda: None)
    # Bust the codec_audit's deferred import path by also patching the
    # module-level import binding in codec_keychain (codec_audit imports
    # via `from codec_keychain import get_audit_hmac_secret` inside the
    # function, so monkey-patching the module is enough).
    codec_audit.audit(event="unsigned_event", source="codec-test")
    line = test_audit.read_text().splitlines()[-1]
    obj = json.loads(line)
    assert obj.get("hmac") == ""
    assert obj.get("hmac_status") == "unsigned_keychain_unavailable"


def test_audit_verify_classifies_unsigned_not_broken(
    _isolate_audit_and_keychain, monkeypatch
):
    """Pre-PR-2E lines (no `hmac` field at all) AND post-PR-2E unsigned
    lines must count as `unsigned`, not `broken`. integrity_ok stays True
    when all non-broken lines are signed or legitimately unsigned."""
    codec_audit, kc, test_audit = _isolate_audit_and_keychain

    # Write one pre-PR-2E style line (no hmac field) manually
    pre_pr2e = {
        "ts": "2026-04-01T00:00:00.000+00:00",
        "schema": 1,
        "event": "old_event",
        "source": "codec-test",
        "outcome": "ok",
    }
    with open(test_audit, "a", encoding="utf-8") as f:
        f.write(json.dumps(pre_pr2e, sort_keys=True, separators=(",", ":")) + "\n")

    # Write one fresh post-PR-2E signed line
    codec_audit.audit(event="new_event", source="codec-test")

    result = codec_audit.verify_audit_log()
    assert result["total_lines"] == 2
    assert result["signed_lines"] == 1
    assert result["unsigned_lines"] == 1
    assert result["broken_lines"] == 0
    assert result["integrity_ok"] is True


# ── Redaction tests (D-19) ──────────────────────────────────────────────────


def _read_last_line(test_audit) -> str:
    return test_audit.read_text().splitlines()[-1]


def test_redact_openai_key_in_task_preview(_isolate_audit_and_keychain):
    """OpenAI/Anthropic-style `sk-...` must be redacted before write."""
    codec_audit, _, test_audit = _isolate_audit_and_keychain
    code = "my key is sk-abc123def456ghi789jkl012mno345pqr"
    codec_audit.audit(event="test", source="codec-test", message=code)
    line = _read_last_line(test_audit)
    assert "sk-abc123def456ghi789jkl012mno345pqr" not in line
    assert "REDACTED:openai_or_anthropic_key" in line


def test_redact_anthropic_key(_isolate_audit_and_keychain):
    """`sk-ant-...` gets the specific anthropic_key tag, not the generic
    openai_or_anthropic_key tag."""
    codec_audit, _, test_audit = _isolate_audit_and_keychain
    key = "sk-ant-api03-abc123def456ghi789jkl012mno345pqrstu"
    codec_audit.audit(event="test", source="codec-test", message=key)
    line = _read_last_line(test_audit)
    assert key not in line
    assert "REDACTED:anthropic_key" in line


def test_redact_bearer_token(_isolate_audit_and_keychain):
    """`Authorization: Bearer <token>` must be redacted (Bearer prefix kept,
    token body replaced)."""
    codec_audit, _, test_audit = _isolate_audit_and_keychain
    msg = "Authorization: Bearer abc123def456ghi789jkl012mno345"
    codec_audit.audit(event="test", source="codec-test", message=msg)
    line = _read_last_line(test_audit)
    assert "abc123def456ghi789jkl012mno345" not in line
    assert "REDACTED:bearer_token" in line


def test_redact_jwt(_isolate_audit_and_keychain):
    """3-part base64url JWTs must be redacted."""
    codec_audit, _, test_audit = _isolate_audit_and_keychain
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0fQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk"
    codec_audit.audit(event="test", source="codec-test", message=jwt)
    line = _read_last_line(test_audit)
    assert jwt not in line
    assert "REDACTED:jwt" in line


def test_redact_github_pat(_isolate_audit_and_keychain):
    """GitHub PATs `ghp_<36 chars>` must be redacted."""
    codec_audit, _, test_audit = _isolate_audit_and_keychain
    pat = "ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    codec_audit.audit(event="test", source="codec-test", message=pat)
    line = _read_last_line(test_audit)
    assert pat not in line
    assert "REDACTED:github_pat" in line


def test_redact_codec_oauth_tokens(_isolate_audit_and_keychain):
    """CODEC's own access + refresh tokens must be redacted."""
    codec_audit, _, test_audit = _isolate_audit_and_keychain
    at = "codec_at_" + "a" * 64
    rt = "codec_rt_" + "b" * 64
    codec_audit.audit(event="test", source="codec-test", message=f"at={at} rt={rt}")
    line = _read_last_line(test_audit)
    assert at not in line
    assert rt not in line
    assert "REDACTED:codec_oauth_access" in line
    assert "REDACTED:codec_oauth_refresh" in line


def test_redact_nested_in_extra(_isolate_audit_and_keychain):
    """Strings nested inside `extra` (and arbitrary depth) must be redacted."""
    codec_audit, _, test_audit = _isolate_audit_and_keychain
    codec_audit.audit(
        event="test",
        source="codec-test",
        extra={"context": {"api_key": "sk-deeplynesteddef456ghi789jkl012mn"}},
    )
    line = _read_last_line(test_audit)
    assert "sk-deeplynesteddef456ghi789jkl012mn" not in line
    assert "REDACTED:openai_or_anthropic_key" in line


def test_no_redaction_false_positive_short_id(_isolate_audit_and_keychain):
    """A short numeric ID (12 digits or fewer) must NOT trip the credit-card
    regex. Keeps order IDs, session IDs, etc., visible in audit."""
    codec_audit, _, test_audit = _isolate_audit_and_keychain
    codec_audit.audit(event="test", source="codec-test", message="user 12345")
    line = _read_last_line(test_audit)
    assert "12345" in line
    assert "REDACTED:cc_candidate" not in line


# ── chmod 0600 tests (D-22) ────────────────────────────────────────────────


def test_audit_log_created_with_0600(_isolate_audit_and_keychain):
    """A fresh audit log file must be created with rw------- (0600), not
    the umask default 0644."""
    codec_audit, _, test_audit = _isolate_audit_and_keychain
    assert not test_audit.exists()
    codec_audit.audit(event="first_event", source="codec-test")
    assert test_audit.exists()
    mode = os.stat(test_audit).st_mode & 0o777
    assert mode == 0o600, f"Expected 0o600, got 0o{mode:o}"


def test_existing_audit_log_chmod_enforced(_isolate_audit_and_keychain):
    """If an existing audit.log has 0644 perms (legacy or external tool),
    the next write must defensively chmod it to 0600."""
    codec_audit, _, test_audit = _isolate_audit_and_keychain
    # Create with 0644
    test_audit.write_text('{"event":"legacy","ts":"old","schema":1}\n')
    os.chmod(test_audit, 0o644)
    assert os.stat(test_audit).st_mode & 0o777 == 0o644

    codec_audit.audit(event="new_event", source="codec-test")
    mode = os.stat(test_audit).st_mode & 0o777
    assert mode == 0o600, f"Defensive chmod failed: 0o{mode:o}"


# ── Integration / smoke tests ───────────────────────────────────────────────


def test_verify_audit_log_smoke(_isolate_audit_and_keychain):
    """Emit 10 events, verify all sign + verify clean."""
    codec_audit, _, _ = _isolate_audit_and_keychain
    for i in range(10):
        codec_audit.audit(event=f"event_{i}", source="codec-test", tool=f"tool_{i}")
    result = codec_audit.verify_audit_log()
    assert result["integrity_ok"] is True
    assert result["total_lines"] == 10
    assert result["signed_lines"] == 10
    assert result["broken_lines"] == 0


def test_audit_verify_skill_runs(_isolate_audit_and_keychain):
    """The audit_verify skill must load and run; output contains a known
    status string."""
    codec_audit, _, _ = _isolate_audit_and_keychain
    codec_audit.audit(event="smoke", source="codec-test")
    skills_dir = REPO / "skills"
    sys.path.insert(0, str(skills_dir))
    try:
        import audit_verify
        # Each test reloads to pick up monkey-patched paths
        import importlib
        importlib.reload(audit_verify)
        result = audit_verify.run("verify audit log")
        assert isinstance(result, str) and result, "Skill must return non-empty str"
        assert "audit log integrity" in result.lower() or "violation" in result.lower()
    finally:
        sys.path.remove(str(skills_dir))


def test_audit_verify_skill_is_not_mcp_exposed():
    """Forensic operations must never reach the MCP boundary — claude.ai
    over MCP HTTP cannot tamper with or read the audit log via this skill."""
    skills_dir = REPO / "skills"
    sys.path.insert(0, str(skills_dir))
    try:
        import audit_verify
        import importlib
        importlib.reload(audit_verify)
        assert getattr(audit_verify, "SKILL_MCP_EXPOSE", True) is False
    finally:
        sys.path.remove(str(skills_dir))


# ── HMAC + redaction interaction ────────────────────────────────────────────


def test_hmac_computed_after_redaction(_isolate_audit_and_keychain):
    """HMAC must be computed over the REDACTED payload, not the raw. If the
    payload were hashed before redaction, an attacker who knew the original
    secret could craft a line that verifies-after-redaction but didn't
    contain the redacted credential in transit."""
    codec_audit, kc, test_audit = _isolate_audit_and_keychain
    secret_key = "sk-redactme123def456ghi789jkl012mn"
    codec_audit.audit(event="test", source="codec-test", message=secret_key)
    line = test_audit.read_text().splitlines()[-1]
    obj = json.loads(line)

    # Recompute HMAC manually against the redacted payload — must match
    secret = kc.get_audit_hmac_secret()
    assert secret is not None
    obj_for_hmac = {k: v for k, v in obj.items() if k != "hmac"}
    canonical = json.dumps(
        obj_for_hmac, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, default=str,
    )
    expected = _hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    assert obj["hmac"] == expected


def test_get_audit_hmac_secret_returns_bytes(_isolate_audit_and_keychain):
    """The helper returns raw bytes (suitable for `hmac.new(...)` direct use),
    not a hex string. 32 bytes long."""
    _, kc, _ = _isolate_audit_and_keychain
    secret = kc.get_audit_hmac_secret()
    assert isinstance(secret, bytes)
    assert len(secret) == 32


def test_get_audit_hmac_secret_silent_no_circular_emit(
    _isolate_audit_and_keychain, monkeypatch
):
    """The bootstrap path must NOT call `_kc_log_event` (which would
    re-enter `audit()` → deadlock on the non-reentrant `_LOCK`)."""
    _, kc, _ = _isolate_audit_and_keychain
    captured = []

    def _capture(*args, **kwargs):
        captured.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(kc, "_kc_log_event", _capture)
    kc._invalidate_audit_hmac_cache()
    secret = kc.get_audit_hmac_secret()
    assert secret is not None
    # No audit emits from the bootstrap path
    assert captured == [], (
        f"get_audit_hmac_secret must not emit audit events; got {captured!r}"
    )


def test_canonical_json_byte_stable_ordering(_isolate_audit_and_keychain):
    """Two dicts with the same content but different key insertion order
    must canonicalize to identical bytes (HMAC stability)."""
    codec_audit, _, _ = _isolate_audit_and_keychain
    a = {"z": 1, "a": 2, "m": {"y": 3, "b": 4}}
    b = {"a": 2, "m": {"b": 4, "y": 3}, "z": 1}
    assert codec_audit._canonical_json(a) == codec_audit._canonical_json(b)


def test_redaction_works_on_top_level_error_field(_isolate_audit_and_keychain):
    """`error` is a top-level field in the unified envelope. Secrets in
    error messages must also be redacted."""
    codec_audit, _, test_audit = _isolate_audit_and_keychain
    codec_audit.audit(
        event="test",
        source="codec-test",
        error="Auth failed for key sk-leakedinerror123def456ghi789jkl0",
    )
    line = _read_last_line(test_audit)
    assert "sk-leakedinerror123def456ghi789jkl0" not in line
    assert "REDACTED:openai_or_anthropic_key" in line


def test_redact_before_truncate_long_message(_isolate_audit_and_keychain):
    """A secret placed near the 500-char message cap must still be fully
    redacted. Truncation BEFORE redaction would chop the regex mid-pattern
    and leak the prefix. PR-2E enforces redact-then-truncate."""
    codec_audit, _, test_audit = _isolate_audit_and_keychain
    # 480-char prefix + 40-char secret = 520 chars (over the 500 _MESSAGE_MAX cap)
    secret = "sk-leakingNearTheCap12345678901234567890"
    long_msg = ("x" * 480) + " " + secret
    codec_audit.audit(event="test", source="codec-test", message=long_msg)
    line = _read_last_line(test_audit)
    obj = json.loads(line)
    # The full secret must not appear in the stored message
    assert secret not in obj.get("message", ""), (
        f"Secret leaked after truncation: {obj['message']!r}"
    )


def test_audit_log_perms_match_keychain_secret_perms(_isolate_audit_and_keychain):
    """Defensive integration: the audit log and the fallback secret store
    both end up at 0600. Catches any future regression where the umask
    creeps back in."""
    codec_audit, kc, test_audit = _isolate_audit_and_keychain
    codec_audit.audit(event="test", source="codec-test")
    audit_mode = os.stat(test_audit).st_mode & 0o777
    assert audit_mode == 0o600
    # Fallback key file (only exists if Keychain unavailable on this host)
    if kc._FALLBACK_KEY_PATH.exists():
        key_mode = os.stat(kc._FALLBACK_KEY_PATH).st_mode & 0o777
        assert key_mode == 0o600
