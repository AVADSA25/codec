"""Route-level audit events go through the HMAC-signed envelope, never plaintext.

`routes/_shared._audit_write` used to append raw f-strings to ~/.codec/audit.log.
Nine auth events were the only UNSIGNED lines in the log — precisely the ones an
intruder would want to edit — and they made `verify_audit_log()` report the
operator's own log as tampered. conftest isolates the audit path, so these
tests write to a throwaway log and verify it the way the operator would.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import codec_audit  # noqa: E402
from routes import _shared  # noqa: E402


def _lines():
    p = Path(codec_audit._AUDIT_LOG)
    return p.read_text().splitlines() if p.exists() else []


def test_structured_event_is_signed_json_and_verifies():
    before = len(_lines())
    _shared._audit_event("auth_failed", outcome="error", level="warning",
                         method="pin", error="wrong_pin", ip="203.0.113.7")
    new = _lines()[before:]
    assert len(new) == 1, f"expected exactly one line, got {new}"
    rec = json.loads(new[0])                      # JSON, not "[ts] AUTH_FAILED: ..."
    assert rec["event"] == "auth_failed" and rec["source"] == "codec-dashboard"
    assert rec["extra"]["ip"] == "203.0.113.7" and rec["error"] == "wrong_pin"   # error is a top-level envelope field
    assert re.fullmatch(r"[0-9a-f]{64}", rec.get("hmac", "")), "line is not HMAC-signed"
    r = codec_audit.verify_audit_log()
    assert r["broken_lines"] == 0 and r["integrity_ok"] is True, r


def test_legacy_shim_never_writes_plaintext():
    """A caller still using the old string API must land in the envelope."""
    before = len(_lines())
    _shared._audit_write("[2026-09-05T09:00:00] TOTP_SETUP: 2FA enabled\n")
    new = _lines()[before:]
    assert len(new) == 1
    rec = json.loads(new[0])                      # would raise on the old raw line
    assert rec["event"] == "totp_setup" and rec["extra"]["legacy"] is True
    assert rec["extra"]["detail"] == "2FA enabled"
    assert "hmac" in rec


def test_no_route_writes_the_audit_log_directly():
    """Lint: codec_audit is the only module allowed to open the audit log."""
    offenders = []
    for path in sorted((REPO / "routes").glob("*.py")):
        src = path.read_text()
        # Reading the log (the /audit viewer) is fine; WRITING it is not.
        if re.search(r"open\(\s*AUDIT_LOG\s*,\s*['\"][aw]", src):
            offenders.append(f"{path.name}: writes AUDIT_LOG directly")
        if re.search(r'_audit_write\(\s*f"\[', src):
            offenders.append(f"{path.name}: legacy plaintext audit call")
    assert not offenders, offenders
    # Positive control for the lint: the pattern it hunts must be detectable.
    assert re.search(r'_audit_write\(\s*f"\[', '_audit_write(f"[{now}] X: y")')
