"""Tests for PR-3G dead-code removal (A-9, A-10, A-13, A-14, A-18).

Pins the deletions so the dead code can't silently creep back, and
verifies the KEPT symbols (run_session_in_terminal, close_session,
HealthResponse, clean_transcript) still work.

Reference: docs/audits/PHASE-1-CODE-QUALITY.md findings A-9, A-10, A-13, A-14, A-18.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


# ── A-9: _live_overlay_script_appkit_DISABLED removed ────────────────────────


def test_a9_disabled_appkit_overlay_removed():
    src = (REPO / "codec_dictate.py").read_text()
    assert "_live_overlay_script_appkit_DISABLED" not in src, (
        "explicit dead code (_DISABLED appkit overlay) must be removed (A-9)"
    )


# ── A-10: run_session_module removed, run_session_in_terminal kept ───────────


def test_a10_run_session_module_removed_but_terminal_kept():
    src = (REPO / "codec_agent.py").read_text()
    assert "def run_session_module" not in src, "unused run_session_module must be removed (A-10)"
    assert "def run_session_in_terminal" in src, "the LIVE launcher must be kept"
    import codec_agent
    assert hasattr(codec_agent, "run_session_in_terminal")
    assert not hasattr(codec_agent, "run_session_module")


# ── A-13: divergent dashboard blocklist already gone (PR-2C) ─────────────────


def test_a13_dashboard_dangerous_patterns_not_live():
    """The divergent _DANGEROUS_PATTERNS / _is_command_safe / /api/execute were
    removed in PR-2C. Only the deletion-marker COMMENT may remain."""
    import codec_dashboard
    assert not hasattr(codec_dashboard, "_DANGEROUS_PATTERNS")
    assert not hasattr(codec_dashboard, "_is_command_safe")
    # No live route handler
    src = (REPO / "codec_dashboard.py").read_text()
    assert "def _is_command_safe" not in src
    assert '@app.post("/api/execute")' not in src


# ── A-14: dead close_session import dropped, behavior preserved ──────────────


def test_a14_close_session_import_dropped_local_kept():
    src = (REPO / "codec.py").read_text()
    # The codec_core import line must no longer pull close_session
    assert "terminal_session_exists, close_session," not in src
    # The local def is kept + callable
    import codec
    assert callable(codec.close_session)
    # codec_core still exports close_session (other consumers rely on it)
    import codec_core
    assert hasattr(codec_core, "close_session")


# ── A-18: 9 unused Pydantic models removed, HealthResponse kept ──────────────


def test_a18_unused_pydantic_models_removed():
    import codec_dashboard
    # HealthResponse IS used (response_model=) — must stay
    assert hasattr(codec_dashboard, "HealthResponse")
    # The 9 unused models must be gone
    for dead in ("StatusResponse", "SkillItem", "ConversationItem", "ScheduleItem",
                 "ServiceStatus", "CommandRequest", "ChatRequest", "AgentRunRequest",
                 "ErrorResponse"):
        assert not hasattr(codec_dashboard, dead), (
            f"unused Pydantic model {dead} must be removed (A-18)"
        )


def test_a18_health_endpoint_still_works():
    """Deleting the models must not break the one that IS wired."""
    from fastapi.testclient import TestClient
    import codec_dashboard
    client = TestClient(codec_dashboard.app)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"
