"""CODEC Phase 3 Step 8 — Plan + Permission Contract.

When user drops a project, this module:
  1. Drafts a structured plan via Qwen-3.6 (local LLM).
  2. Validates skills_needed against codec_skill_registry.
  3. Auto-approves items already in the global allowlist.
  4. Persists to ~/.codec/agents/<id>/ with atomic tmp+rename writes.
  5. Surfaces the plan + permission manifest via the FastAPI router in
     routes/agents.py so the PWA can show approve/edit/reject UI.

Step 8 ships planning ONLY — no execution. Step 9 (codec_agent_runner.py)
will pick up status=approved plans and run them.

Reuses:
  - codec_audit.audit() — Step 1 envelope, paired correlation_id
  - codec_skill_registry.SkillRegistry — skill validation
  - codec_ask_user.ask — clarifying questions for vague descriptions

See docs/PHASE3-BLUEPRINT.md §2 for design rationale.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("codec_agent_plan")

# ── Storage paths (overridable for tests) ─────────────────────────────────────
_CODEC_DIR = Path(os.path.expanduser("~/.codec"))
_AGENTS_DIR = _CODEC_DIR / "agents"
_GLOBAL_GRANTS_PATH = _CODEC_DIR / "agent_global_grants.json"

# ── Schema constants ──────────────────────────────────────────────────────────
PLAN_SCHEMA_VERSION = 1
GLOBAL_GRANTS_SCHEMA_VERSION = 1
DEFAULT_STEP_BUDGET_PER_CHECKPOINT = 30
MAX_CLARIFYING_ROUNDS = 3


# ── Dataclasses ───────────────────────────────────────────────────────────────
@dataclass
class Checkpoint:
    id: str
    title: str
    description: str
    skills_needed: List[str]
    expected_output: str
    step_budget: int = DEFAULT_STEP_BUDGET_PER_CHECKPOINT


@dataclass
class PermissionManifest:
    read_paths: List[str]
    write_paths: List[str]
    network_domains: List[str]
    skills: List[str]
    destructive_ops: List[str]


@dataclass
class Plan:
    schema: int
    agent_id: str
    goals: List[str]
    checkpoints: List[Checkpoint]
    permission_manifest: PermissionManifest
    estimated_duration_minutes: int
    assumptions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "agent_id": self.agent_id,
            "goals": list(self.goals),
            "checkpoints": [asdict(cp) for cp in self.checkpoints],
            "permission_manifest": asdict(self.permission_manifest),
            "estimated_duration_minutes": self.estimated_duration_minutes,
            "assumptions": list(self.assumptions),
        }


def plan_from_dict(d: Dict[str, Any]) -> Plan:
    """Inverse of Plan.to_dict; raises ValueError on bad schema."""
    if d.get("schema") != PLAN_SCHEMA_VERSION:
        raise ValueError(f"unsupported plan schema: {d.get('schema')!r}")
    cps = [Checkpoint(**cp) for cp in d.get("checkpoints", [])]
    pm = PermissionManifest(**d["permission_manifest"])
    return Plan(
        schema=int(d["schema"]),
        agent_id=str(d["agent_id"]),
        goals=list(d.get("goals", [])),
        checkpoints=cps,
        permission_manifest=pm,
        estimated_duration_minutes=int(d.get("estimated_duration_minutes", 0)),
        assumptions=list(d.get("assumptions", [])),
    )
