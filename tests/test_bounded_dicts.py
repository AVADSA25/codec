"""Tests for PR-4G (H-4 / H-6 / M-6) — bounded growth + eviction for the three
unbounded in-memory dicts that leak until a PM2 max-memory restart:

  * H-4 routes/_shared._agent_jobs        → _evict_stale_agent_jobs (terminal >24h)
  * H-6 routes/_shared._pending_approvals → _evict_expired_approvals (>120s)
  * M-6 codec_voice.VoicePipeline._resumable_sessions → _prune_resumable (>TTL)

(H-5 codec_mcp_http._RATE_WINDOW is deferred — that module imports mcp/fastmcp,
absent locally + in CI, so it can't be unit-tested. See PR4G design §5.)

Reference: docs/PR4G-BOUNDED-DICTS-DESIGN.md, docs/audits/PHASE-1-RELIABILITY.md.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ── H-4 + H-6: routes/_shared dict evictions ──────────────────────────────────


@pytest.fixture
def shared(monkeypatch):
    import routes._shared as sh
    monkeypatch.setattr(sh, "_agent_jobs", {})
    monkeypatch.setattr(sh, "_pending_approvals", {})
    return sh


def test_evict_stale_agent_jobs_drops_old_terminal(shared):
    now = datetime(2026, 5, 24, 12, 0, 0)
    old = (now - timedelta(hours=25)).isoformat()
    recent = (now - timedelta(hours=1)).isoformat()
    shared._agent_jobs.update({
        "old_done":     {"status": "complete", "started": old},
        "old_error":    {"status": "error", "started": old},
        "recent_done":  {"status": "complete", "started": recent},
        "old_running":  {"status": "running", "started": old},
        "bad_ts":       {"status": "complete", "started": "not-a-date"},
    })
    shared._evict_stale_agent_jobs(now=now)
    assert "old_done" not in shared._agent_jobs, "terminal job >24h must be evicted"
    assert "old_error" not in shared._agent_jobs, "errored job >24h must be evicted"
    assert "recent_done" in shared._agent_jobs, "recent terminal job must stay"
    assert "old_running" in shared._agent_jobs, "a running job must never be evicted"
    assert "bad_ts" in shared._agent_jobs, "unparseable timestamp must be kept (no data loss)"


def test_evict_stale_agent_jobs_empty_is_safe(shared):
    shared._evict_stale_agent_jobs()  # must not raise on an empty dict


def test_evict_expired_approvals_drops_old(shared):
    now = 100000.0
    shared._pending_approvals.update({
        "old_pending": {"status": "pending", "command": "x", "timestamp": now - 200},
        "old_allowed": {"status": "allowed", "command": "x", "timestamp": now - 200},
        "old_denied":  {"status": "denied", "command": "x", "timestamp": now - 200},
        "recent":      {"status": "pending", "command": "x", "timestamp": now - 30},
    })
    shared._evict_expired_approvals(now=now)
    assert "old_pending" not in shared._pending_approvals
    assert "old_allowed" not in shared._pending_approvals
    assert "old_denied" not in shared._pending_approvals
    assert "recent" in shared._pending_approvals, "a fresh approval must stay"


def test_evict_expired_approvals_empty_is_safe(shared):
    shared._evict_expired_approvals()  # must not raise


# ── M-6: VoicePipeline resumable-session prune ────────────────────────────────


@pytest.fixture
def voice(monkeypatch):
    import codec_voice
    vp = codec_voice.VoicePipeline
    monkeypatch.setattr(vp, "_resumable_sessions", {}, raising=False)
    monkeypatch.setattr(vp, "_resume_timestamps", {}, raising=False)
    return vp


def test_prune_resumable_drops_stale_keeps_fresh(voice):
    now = 10_000.0
    ttl = voice._RESUME_TTL
    voice._resumable_sessions.update({"stale": ["m"], "fresh": ["m"]})
    voice._resume_timestamps.update({"stale": now - ttl - 1, "fresh": now - 10})
    voice._prune_resumable(now=now)
    assert "stale" not in voice._resumable_sessions, "session older than TTL must be evicted"
    assert "stale" not in voice._resume_timestamps, "its timestamp must be evicted too (no drift)"
    assert "fresh" in voice._resumable_sessions, "a fresh session must stay"
    assert "fresh" in voice._resume_timestamps


def test_prune_resumable_empty_is_safe(voice):
    voice._prune_resumable(now=10_000.0)  # must not raise


# ── source invariants: the eviction is actually wired into the call sites ──────


def test_agents_run_endpoint_evicts():
    src = (REPO / "routes" / "agents.py").read_text()
    assert "_evict_stale_agent_jobs(" in src, "the run endpoint must sweep stale jobs"


def test_dashboard_approval_endpoints_evict():
    src = (REPO / "codec_dashboard.py").read_text()
    assert "_evict_expired_approvals(" in src, "approval endpoints must sweep expired approvals"


def test_voice_init_prunes_resumable():
    src = (REPO / "codec_voice.py").read_text()
    init = src[src.index("def __init__(self, websocket"):]
    init = init[:init.index("\n    def ", 1)]
    assert "_prune_resumable(" in init, (
        "__init__ must prune stale sessions (evict on any new session, not only on save)"
    )
