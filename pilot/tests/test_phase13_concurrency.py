"""Pilot PP-7 — only one autonomous run may drive the single shared browser at a time
(P-9: _lock was declared but never used; runs interleaved on one page), and the in-memory
_runs dict is bounded (P-14). Pure-helper tests, no browser.

Reference: docs/PP7-CONCURRENCY-DESIGN.md.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # ~/codec on sys.path

import pilot.pilot_runner as pr  # noqa: E402


def test_second_run_rejected_while_one_executing(monkeypatch):
    monkeypatch.setattr(pr, "_executing", "run_A")
    monkeypatch.setattr(pr, "_runs", {"run_A": {"status": "running", "started_at": 1}})
    with pytest.raises(pr.HTTPException) as e:
        pr._assert_run_slot_free("run_B")
    assert e.value.status_code == 409


def test_slot_free_when_none_executing(monkeypatch):
    monkeypatch.setattr(pr, "_executing", None)
    monkeypatch.setattr(pr, "_runs", {})
    pr._assert_run_slot_free("run_X")  # must not raise


def test_same_run_may_restart(monkeypatch):
    monkeypatch.setattr(pr, "_executing", "run_A")
    monkeypatch.setattr(pr, "_runs", {"run_A": {"status": "running", "started_at": 1}})
    pr._assert_run_slot_free("run_A")  # same run → no raise


def test_runs_evicted_to_cap(monkeypatch):
    monkeypatch.setattr(pr, "_runs",
                        {f"r{i}": {"run_id": f"r{i}", "started_at": i} for i in range(60)})
    pr._evict_old_runs(cap=50)
    assert len(pr._runs) <= 50
    assert "r59" in pr._runs and "r0" not in pr._runs, "oldest dropped, newest kept"
