"""
CODEC Pilot — Phase 5: Trace Storage
======================================

Saves AgentRun traces to disk as JSON and loads them back.

Each trace is written to:
    ~/.codec/pilot_traces/{run_id}/trace.json

The trace captures everything needed for Phase-5 compiler + replay:
  - task description
  - all agent steps (action, snapshot_before, result, error, timestamp)
  - final status and result

Usage:
    from pilot.trace import save_trace, load_trace

    run: AgentRun = await agent.execute()
    path = save_trace(run)

    run2 = load_trace(run.run_id)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .config import PILOT_TRACES_DIR
from .pilot_agent import AgentRun, AgentStep


def save_trace(run: AgentRun, traces_dir: Path = PILOT_TRACES_DIR) -> Path:
    """Serialise AgentRun to {traces_dir}/{run_id}/trace.json. Returns path."""
    run_dir = traces_dir / run.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "trace.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(run.to_dict(), f, indent=2)
    return path


def load_trace(run_id: str, traces_dir: Path = PILOT_TRACES_DIR) -> AgentRun:
    """Load an AgentRun from disk by run_id. Raises FileNotFoundError if missing."""
    path = traces_dir / run_id / "trace.json"
    if not path.exists():
        raise FileNotFoundError(f"Trace not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return _from_dict(data)


def list_traces(traces_dir: Path = PILOT_TRACES_DIR) -> list[dict]:
    """Return summary dicts for all saved traces, newest first."""
    results = []
    if not traces_dir.exists():
        return results
    for run_dir in sorted(traces_dir.iterdir(), reverse=True):
        p = run_dir / "trace.json"
        if p.exists():
            try:
                with open(p) as f:
                    data = json.load(f)
                results.append({
                    "run_id":     data.get("run_id", run_dir.name),
                    "task":       data.get("task", ""),
                    "status":     data.get("status", ""),
                    "step_count": data.get("step_count", 0),
                    "started_at": data.get("started_at"),
                    "ended_at":   data.get("ended_at"),
                    "path":       str(p),
                })
            except Exception:
                pass
    return results


def _from_dict(data: dict) -> AgentRun:
    """Reconstruct AgentRun from a trace dict (for replay)."""
    run = AgentRun(
        task=data.get("task", ""),          # P-15: tolerate corrupt/partial trace
        run_id=data.get("run_id", ""),
        status=data.get("status", "unknown"),
        result=data.get("result"),
        error=data.get("error"),
        started_at=data.get("started_at", 0.0),
        ended_at=data.get("ended_at"),
    )
    for s in data.get("steps", []):
        run.steps.append(AgentStep(
            step=s.get("step", 0),          # P-15: tolerate partial step
            action=s.get("action", {}),
            snapshot_before=s.get("snapshot_before", ""),
            result=s.get("result", ""),
            error=s.get("error"),
            ts=s.get("ts", 0.0),
            target_xpath=s.get("target_xpath"),
            target_css=s.get("target_css"),
            target_name=s.get("target_name"),
            target_role=s.get("target_role"),
        ))
    return run


# Public alias so other modules (replay.py) can import it.
from_dict = _from_dict
