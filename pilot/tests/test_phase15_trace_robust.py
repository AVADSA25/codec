"""Pilot PP-9 — loading a corrupt/partial trace must not raise KeyError (P-15). A
hand-edited or truncated trace.json should degrade gracefully, not 500 the replay path.

Reference: docs/PP9-TRACE-ROBUSTNESS-DESIGN.md.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # ~/codec on sys.path

from pilot.trace import _from_dict  # noqa: E402


def test_from_dict_tolerates_missing_top_keys():
    run = _from_dict({})  # no task / run_id / status
    assert run.task == "" and run.run_id == "" and run.status  # no KeyError


def test_from_dict_tolerates_partial_step():
    run = _from_dict({
        "task": "t", "run_id": "r", "status": "done",
        "steps": [{"snapshot_before": "x"}],  # missing step + action
    })
    assert len(run.steps) == 1
    assert run.steps[0].action == {}
