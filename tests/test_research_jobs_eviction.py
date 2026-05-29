"""N9 (re-audit, High): _research_jobs had NO lock and NO eviction (unlike
_agent_jobs after H-4) — an unbounded memory leak plus a 'dict changed size'
race between deep_research_start's insert and the worker's update. Add a TTL
eviction mirroring _evict_stale_agent_jobs.
"""
from datetime import datetime, timedelta

import routes._shared as shared


def test_evict_stale_research_jobs(monkeypatch):
    monkeypatch.setattr(shared, "_research_jobs", {}, raising=False)
    now = datetime.now()
    old = (now - timedelta(hours=48)).isoformat()
    fresh = now.isoformat()
    shared._research_jobs["old_done"] = {"status": "done", "started": old}
    shared._research_jobs["old_running"] = {"status": "running", "started": old}
    shared._research_jobs["fresh_done"] = {"status": "done", "started": fresh}

    removed = shared._evict_stale_research_jobs(now=now)

    assert removed == 1, "only the old terminal job should be evicted"
    assert "old_done" not in shared._research_jobs
    assert "old_running" in shared._research_jobs, "running jobs must never be evicted"
    assert "fresh_done" in shared._research_jobs


def test_evict_stale_research_jobs_keeps_unparseable_started(monkeypatch):
    monkeypatch.setattr(shared, "_research_jobs", {}, raising=False)
    shared._research_jobs["bad_ts"] = {"status": "done", "started": "not-a-date"}
    removed = shared._evict_stale_research_jobs()
    assert removed == 0
    assert "bad_ts" in shared._research_jobs, "never lose data on an unparseable timestamp"
