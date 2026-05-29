"""Pilot PP-8 — Pilot emits a durable forensic audit trail (it was invisible to any audit
log). Closes audit P-12. Self-contained log (~/.codec/pilot_audit.log) to avoid coupling to
the parent's HMAC/flock'd audit.log.

Reference: docs/PP8-AUDIT-DESIGN.md.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # ~/codec on sys.path

from pilot import audit as pa  # noqa: E402
from pilot import skill_review as sr  # noqa: E402


def test_audit_writes_jsonl(tmp_path, monkeypatch):
    p = tmp_path / "pilot_audit.log"
    monkeypatch.setattr(pa, "_AUDIT_PATH", p)
    pa.audit("skill_approved", slug="pilot_x", actor="operator")
    rec = json.loads(p.read_text().strip().splitlines()[-1])
    assert rec["event"] == "skill_approved"
    assert rec["slug"] == "pilot_x" and rec["actor"] == "operator"
    assert "ts" in rec


def test_audit_never_raises(monkeypatch):
    monkeypatch.setattr(pa, "_AUDIT_PATH", Path("/nonexistent/deep/dir/x.log"))
    pa.audit("anything", foo="bar")  # must not raise


def test_approve_pending_emits_audit(tmp_path, monkeypatch):
    pend = tmp_path / "pending"
    active = tmp_path / "skills"
    pend.mkdir()
    active.mkdir()
    (pend / "pilot_demo.py").write_text("SKILL_NAME='pilot_demo'\n")
    monkeypatch.setattr(sr, "SKILLS_PENDING_DIR", pend)
    monkeypatch.setattr(sr, "SKILLS_DIR", active)
    events = []
    monkeypatch.setattr(sr, "audit", lambda event, **kw: events.append((event, kw)))

    sr.approve_pending("demo")

    assert any(e[0] == "skill_approved" for e in events), "approve must emit an audit event (P-12)"
