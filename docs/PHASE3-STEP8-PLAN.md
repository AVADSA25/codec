# Phase 3 Step 8 — Plan + Permission Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the planning + permission-contract layer of Phase 3. When the user drops a project, agent generates a structured plan via Qwen-3.6, the user approves in PWA with an explicit permission manifest, and grants are persisted to disk. **No execution yet** — Step 9 picks that up. Step 8 alone is shippable: drafted plans sit in `awaiting_approval`, approved plans sit in `approved` waiting for the runner.

**Architecture:** New `codec_agent_plan.py` module (drafting, schema, R/W, validation) + new `routes/agents.py` FastAPI router (9 endpoints) + `~/.codec/agents/<id>/` storage layout + global allowlist tier at `~/.codec/agent_global_grants.json`. Reuses Phase 1+2 substrate: `codec_audit` (Step 1), `codec_skill_registry` (existing), `codec_ask_user.ask` (Step 3) for vague-description clarifying loop. LLM-drafted plans validated against skill registry; plan-hash stored at approval for Step 9 tamper detection.

**Tech Stack:** Python 3.13 (existing), FastAPI router pattern (mirror `routes/triggers.py` from Phase 2 Step 6), Qwen-3.6 local LLM via `qwen36` HTTP endpoint at `http://127.0.0.1:8090/v1/chat/completions` (existing PM2 service `qwen3.6`), pytest (existing). All file I/O via atomic tmp+rename pattern (mirror Phase 2).

**Reference design doc:** `docs/PHASE3-BLUEPRINT.md` §2 (Step 8) and §8 (resolved Q1–Q4, Q13).

---

## File Structure

**NEW files:**

| Path | Purpose | Est. LOC |
|---|---|---|
| `codec_agent_plan.py` | Plan dataclass + draft + validate + R/W + global allowlist | ~500 |
| `routes/agents.py` | 9 PWA endpoints (CRUD + approve/reject/revise + global grants) | ~200 |
| `tests/test_agent_plan.py` | 25 tests covering all behaviors | ~700 |

**MODIFIED files:**

| Path | What | Est. LOC |
|---|---|---|
| `codec_audit.py` | Add 6 Phase 3 Step 8 audit event constants + `PHASE3_STEP8_EVENTS` frozenset | +20 |
| `codec_dashboard.py` | Mount `routes/agents.py` router; extend `CHAT_SKILL_ALLOWLIST` is NOT needed (this isn't a skill) | +5 |
| `AGENTS.md` | New §X.X Phase 3 Step 8 sub-section, §6 audit events table extension, §10 don't-touch list update | +40 |

**Storage created at runtime:**

```
~/.codec/agents/<agent_id>/
  manifest.json
  plan.json
  grants.json    (only after approve)
  state.json
~/.codec/agent_global_grants.json
```

---

## Task 1: Audit event constants

**Files:**
- Modify: `codec_audit.py` (add Phase 3 Step 8 constants + frozenset)
- Test: `tests/test_agent_plan.py` (constant assertions)

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_plan.py` with:

```python
"""Phase 3 Step 8 tests — codec_agent_plan + routes/agents.py.

25 tests covering:
  Audit constants (1)
  Plan dataclass + schema (3)
  Atomic R/W (2)
  Skill-registry validation (2)
  Plan-hash tamper detection (2)
  LLM plan drafter (3)
  Vague-description clarifying loop (2)
  Global allowlist (3)
  State machine transitions (2)
  PWA endpoints (4)
  End-to-end integration (1)
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def test_audit_constants_present():
    """Phase 3 Step 8 adds 6 named events + 1 frozenset."""
    import codec_audit
    assert codec_audit.AGENT_PLAN_DRAFTED == "agent_plan_drafted"
    assert codec_audit.AGENT_PLAN_APPROVED == "agent_plan_approved"
    assert codec_audit.AGENT_PLAN_REJECTED == "agent_plan_rejected"
    assert codec_audit.AGENT_PLAN_REVISED == "agent_plan_revised"
    assert codec_audit.AGENT_GLOBAL_GRANT_ADDED == "agent_global_grant_added"
    assert codec_audit.AGENT_GLOBAL_GRANT_REMOVED == "agent_global_grant_removed"
    assert codec_audit.PHASE3_STEP8_EVENTS == frozenset({
        "agent_plan_drafted", "agent_plan_approved", "agent_plan_rejected",
        "agent_plan_revised", "agent_global_grant_added", "agent_global_grant_removed",
    })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.13 -m pytest tests/test_agent_plan.py::test_audit_constants_present -v`
Expected: FAIL with `AttributeError: module 'codec_audit' has no attribute 'AGENT_PLAN_DRAFTED'`

- [ ] **Step 3: Add constants to codec_audit.py**

Open `codec_audit.py`. Find the `PHASE2_STEP7_EVENTS` block (search for it). Immediately after that block, add:

```python
# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 Step 8 — Plan + Permission Contract
# ─────────────────────────────────────────────────────────────────────────────
AGENT_PLAN_DRAFTED         = "agent_plan_drafted"
AGENT_PLAN_APPROVED        = "agent_plan_approved"
AGENT_PLAN_REJECTED        = "agent_plan_rejected"
AGENT_PLAN_REVISED         = "agent_plan_revised"
AGENT_GLOBAL_GRANT_ADDED   = "agent_global_grant_added"
AGENT_GLOBAL_GRANT_REMOVED = "agent_global_grant_removed"

PHASE3_STEP8_EVENTS = frozenset({
    AGENT_PLAN_DRAFTED, AGENT_PLAN_APPROVED, AGENT_PLAN_REJECTED,
    AGENT_PLAN_REVISED, AGENT_GLOBAL_GRANT_ADDED, AGENT_GLOBAL_GRANT_REMOVED,
})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.13 -m pytest tests/test_agent_plan.py::test_audit_constants_present -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codec_audit.py tests/test_agent_plan.py
git commit -m "feat(audit): Phase 3 Step 8 event constants"
```

---

## Task 2: Skeleton module + Plan/Checkpoint/PermissionManifest dataclasses

**Files:**
- Create: `codec_agent_plan.py`
- Test: `tests/test_agent_plan.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_plan.py`:

```python
def test_plan_dataclass_basic():
    from codec_agent_plan import Plan, Checkpoint, PermissionManifest
    cp = Checkpoint(
        id="abc123ab", title="Scrape listings", description="...",
        skills_needed=["chrome_open"], expected_output="JSON of listings",
        step_budget=30,
    )
    pm = PermissionManifest(
        read_paths=["~/Documents/**"], write_paths=["~/.codec/agents/test/artifacts/**"],
        network_domains=["example.com"], skills=["chrome_open"], destructive_ops=[],
    )
    plan = Plan(
        schema=1, agent_id="test_agent",
        goals=["Scrape data"], checkpoints=[cp], permission_manifest=pm,
        estimated_duration_minutes=15, assumptions=[],
    )
    assert plan.schema == 1
    assert plan.checkpoints[0].title == "Scrape listings"
    assert plan.permission_manifest.skills == ["chrome_open"]


def test_plan_dataclass_to_dict_roundtrip():
    from codec_agent_plan import Plan, Checkpoint, PermissionManifest, plan_from_dict
    cp = Checkpoint(id="x", title="t", description="d",
                    skills_needed=["s"], expected_output="o", step_budget=10)
    pm = PermissionManifest(read_paths=[], write_paths=[], network_domains=[],
                            skills=["s"], destructive_ops=[])
    plan = Plan(schema=1, agent_id="a1", goals=["g"], checkpoints=[cp],
                permission_manifest=pm, estimated_duration_minutes=5, assumptions=[])
    d = plan.to_dict()
    plan2 = plan_from_dict(d)
    assert plan2.agent_id == plan.agent_id
    assert plan2.checkpoints[0].id == plan.checkpoints[0].id
    assert plan2.permission_manifest.skills == plan.permission_manifest.skills
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.13 -m pytest tests/test_agent_plan.py::test_plan_dataclass_basic tests/test_agent_plan.py::test_plan_dataclass_to_dict_roundtrip -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codec_agent_plan'`

- [ ] **Step 3: Create codec_agent_plan.py**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.13 -m pytest tests/test_agent_plan.py::test_plan_dataclass_basic tests/test_agent_plan.py::test_plan_dataclass_to_dict_roundtrip -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codec_agent_plan.py tests/test_agent_plan.py
git commit -m "feat(agent_plan): Plan/Checkpoint/PermissionManifest dataclasses"
```

---

## Task 3: Plan-hash for tamper detection (Q13)

**Files:**
- Modify: `codec_agent_plan.py` (+ `compute_plan_hash`)
- Test: `tests/test_agent_plan.py`

- [ ] **Step 1: Write the failing test**

```python
def test_plan_hash_stable_for_identical_content():
    from codec_agent_plan import Plan, Checkpoint, PermissionManifest, compute_plan_hash
    cp = Checkpoint(id="x", title="t", description="d", skills_needed=["s"],
                    expected_output="o", step_budget=10)
    pm = PermissionManifest(read_paths=[], write_paths=[], network_domains=[],
                            skills=["s"], destructive_ops=[])
    plan_a = Plan(schema=1, agent_id="a1", goals=["g"], checkpoints=[cp],
                  permission_manifest=pm, estimated_duration_minutes=5, assumptions=[])
    plan_b = Plan(schema=1, agent_id="a1", goals=["g"], checkpoints=[cp],
                  permission_manifest=pm, estimated_duration_minutes=5, assumptions=[])
    assert compute_plan_hash(plan_a) == compute_plan_hash(plan_b)


def test_plan_hash_changes_when_content_changes():
    from codec_agent_plan import Plan, Checkpoint, PermissionManifest, compute_plan_hash
    cp = Checkpoint(id="x", title="t", description="d", skills_needed=["s"],
                    expected_output="o", step_budget=10)
    pm = PermissionManifest(read_paths=[], write_paths=[], network_domains=[],
                            skills=["s"], destructive_ops=[])
    plan_a = Plan(schema=1, agent_id="a1", goals=["g"], checkpoints=[cp],
                  permission_manifest=pm, estimated_duration_minutes=5, assumptions=[])
    plan_b = Plan(schema=1, agent_id="a1", goals=["g_modified"], checkpoints=[cp],
                  permission_manifest=pm, estimated_duration_minutes=5, assumptions=[])
    assert compute_plan_hash(plan_a) != compute_plan_hash(plan_b)
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python3.13 -m pytest tests/test_agent_plan.py::test_plan_hash_stable_for_identical_content tests/test_agent_plan.py::test_plan_hash_changes_when_content_changes -v`
Expected: FAIL with `ImportError: cannot import name 'compute_plan_hash'`

- [ ] **Step 3: Add compute_plan_hash to codec_agent_plan.py**

Append to `codec_agent_plan.py`:

```python
def compute_plan_hash(plan: Plan) -> str:
    """SHA-256 of canonical JSON serialization. Stored in manifest at
    approval time; daemon (Step 9) verifies on every tick. Mismatch
    means someone manually edited plan.json after approval."""
    canonical = json.dumps(plan.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python3.13 -m pytest tests/test_agent_plan.py::test_plan_hash_stable_for_identical_content tests/test_agent_plan.py::test_plan_hash_changes_when_content_changes -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codec_agent_plan.py tests/test_agent_plan.py
git commit -m "feat(agent_plan): plan-hash for Step 9 tamper detection"
```

---

## Task 4: Atomic R/W for plan + manifest + state

**Files:**
- Modify: `codec_agent_plan.py`
- Test: `tests/test_agent_plan.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.fixture
def temp_codec_dir(tmp_path, monkeypatch):
    import codec_agent_plan as cap
    monkeypatch.setattr(cap, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cap, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cap, "_GLOBAL_GRANTS_PATH", tmp_path / "agent_global_grants.json")
    return tmp_path


def test_save_and_load_plan_roundtrip(temp_codec_dir):
    from codec_agent_plan import (
        Plan, Checkpoint, PermissionManifest, save_plan, load_plan,
    )
    cp = Checkpoint(id="x", title="t", description="d", skills_needed=["s"],
                    expected_output="o", step_budget=10)
    pm = PermissionManifest(read_paths=[], write_paths=[], network_domains=[],
                            skills=["s"], destructive_ops=[])
    plan = Plan(schema=1, agent_id="agent_test", goals=["g"], checkpoints=[cp],
                permission_manifest=pm, estimated_duration_minutes=5, assumptions=[])
    save_plan(plan)
    loaded = load_plan("agent_test")
    assert loaded.agent_id == "agent_test"
    assert loaded.checkpoints[0].title == "t"


def test_save_state_atomic(temp_codec_dir):
    from codec_agent_plan import save_state, load_state
    save_state("agent_x", {"current_checkpoint": 0, "status": "draft_pending"})
    state = load_state("agent_x")
    assert state["current_checkpoint"] == 0
    assert state["status"] == "draft_pending"
    # Verify atomic: tmp file is gone after save
    agent_dir = temp_codec_dir / "agents" / "agent_x"
    assert not (agent_dir / "state.json.tmp").exists()
    assert (agent_dir / "state.json").exists()
```

- [ ] **Step 2: Run tests, verify fail**

Run: `python3.13 -m pytest tests/test_agent_plan.py::test_save_and_load_plan_roundtrip tests/test_agent_plan.py::test_save_state_atomic -v`
Expected: FAIL — `save_plan` etc. not defined.

- [ ] **Step 3: Add atomic R/W functions**

Append to `codec_agent_plan.py`:

```python
# ── Atomic file I/O (tmp+rename pattern from Phase 2) ─────────────────────────
def _atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON atomically: write to .tmp, fsync, rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("read_json failed for %s: %s", path, e)
        return None


def _agent_dir(agent_id: str) -> Path:
    return _AGENTS_DIR / agent_id


# ── Plan R/W ──────────────────────────────────────────────────────────────────
def save_plan(plan: Plan) -> None:
    _atomic_write_json(_agent_dir(plan.agent_id) / "plan.json", plan.to_dict())


def load_plan(agent_id: str) -> Optional[Plan]:
    d = _read_json(_agent_dir(agent_id) / "plan.json")
    return plan_from_dict(d) if d else None


# ── State R/W ─────────────────────────────────────────────────────────────────
def save_state(agent_id: str, state: Dict[str, Any]) -> None:
    _atomic_write_json(_agent_dir(agent_id) / "state.json", state)


def load_state(agent_id: str) -> Dict[str, Any]:
    return _read_json(_agent_dir(agent_id) / "state.json") or {}


# ── Manifest R/W ──────────────────────────────────────────────────────────────
def save_manifest(agent_id: str, manifest: Dict[str, Any]) -> None:
    _atomic_write_json(_agent_dir(agent_id) / "manifest.json", manifest)


def load_manifest(agent_id: str) -> Dict[str, Any]:
    return _read_json(_agent_dir(agent_id) / "manifest.json") or {}


# ── Grants R/W ────────────────────────────────────────────────────────────────
def save_grants(agent_id: str, grants: Dict[str, Any]) -> None:
    _atomic_write_json(_agent_dir(agent_id) / "grants.json", grants)


def load_grants(agent_id: str) -> Dict[str, Any]:
    return _read_json(_agent_dir(agent_id) / "grants.json") or {}
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python3.13 -m pytest tests/test_agent_plan.py -k "save_and_load_plan or save_state_atomic" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codec_agent_plan.py tests/test_agent_plan.py
git commit -m "feat(agent_plan): atomic R/W for plan/state/manifest/grants"
```

---

## Task 5: Skill-registry validation

**Files:**
- Modify: `codec_agent_plan.py`
- Test: `tests/test_agent_plan.py`

- [ ] **Step 1: Write the failing test**

```python
def test_validate_plan_against_registry_ok():
    from codec_agent_plan import (
        Plan, Checkpoint, PermissionManifest, validate_plan_skills,
    )
    cp = Checkpoint(id="x", title="t", description="d",
                    skills_needed=["weather"], expected_output="o", step_budget=10)
    pm = PermissionManifest(read_paths=[], write_paths=[], network_domains=[],
                            skills=["weather"], destructive_ops=[])
    plan = Plan(schema=1, agent_id="a", goals=["g"], checkpoints=[cp],
                permission_manifest=pm, estimated_duration_minutes=5, assumptions=[])

    fake_registry = MagicMock()
    fake_registry.names.return_value = ["weather", "calculator"]
    ok, missing = validate_plan_skills(plan, registry=fake_registry)
    assert ok is True
    assert missing == []


def test_validate_plan_against_registry_rejects_unknown_skill():
    from codec_agent_plan import (
        Plan, Checkpoint, PermissionManifest, validate_plan_skills,
    )
    cp = Checkpoint(id="x", title="t", description="d",
                    skills_needed=["nonexistent_skill"], expected_output="o", step_budget=10)
    pm = PermissionManifest(read_paths=[], write_paths=[], network_domains=[],
                            skills=["nonexistent_skill"], destructive_ops=[])
    plan = Plan(schema=1, agent_id="a", goals=["g"], checkpoints=[cp],
                permission_manifest=pm, estimated_duration_minutes=5, assumptions=[])

    fake_registry = MagicMock()
    fake_registry.names.return_value = ["weather", "calculator"]
    ok, missing = validate_plan_skills(plan, registry=fake_registry)
    assert ok is False
    assert "nonexistent_skill" in missing
```

- [ ] **Step 2: Run tests, verify fail**

Run: `python3.13 -m pytest tests/test_agent_plan.py -k "validate_plan_against_registry" -v`
Expected: FAIL — `validate_plan_skills` not defined.

- [ ] **Step 3: Add validate_plan_skills**

Append to `codec_agent_plan.py`:

```python
def validate_plan_skills(plan: Plan, registry=None) -> Tuple[bool, List[str]]:
    """Walk every checkpoint's skills_needed; return (ok, missing_skills).
    If `registry` is None, lazy-imports codec_skill_registry's default
    instance (via codec_dispatch)."""
    if registry is None:
        try:
            from codec_dispatch import registry as _reg
            registry = _reg
        except Exception:
            log.warning("codec_dispatch unavailable; cannot validate skills")
            return (False, ["__registry_unavailable__"])

    known = set(registry.names() or [])
    needed = set()
    for cp in plan.checkpoints:
        needed.update(cp.skills_needed)
    needed.update(plan.permission_manifest.skills)

    missing = sorted(needed - known)
    return (len(missing) == 0, missing)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python3.13 -m pytest tests/test_agent_plan.py -k "validate_plan_against_registry" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codec_agent_plan.py tests/test_agent_plan.py
git commit -m "feat(agent_plan): validate skills_needed against codec_skill_registry"
```

---

## Task 6: LLM plan drafter (Qwen-3.6, local-only)

**Files:**
- Modify: `codec_agent_plan.py`
- Test: `tests/test_agent_plan.py`

- [ ] **Step 1: Write the failing test**

```python
def test_draft_plan_via_qwen_returns_valid_plan(monkeypatch):
    import codec_agent_plan as cap

    fake_qwen_response = json.dumps({
        "goals": ["Build property monitor bot"],
        "checkpoints": [
            {"title": "Set up bot scaffold", "description": "...",
             "skills_needed": ["file_ops"], "expected_output": "Bot project dir created",
             "step_budget": 30},
            {"title": "Implement scraper", "description": "...",
             "skills_needed": ["chrome_open", "file_ops"],
             "expected_output": "Listings JSON written", "step_budget": 60},
        ],
        "permission_manifest": {
            "read_paths": [], "write_paths": ["~/.codec/agents/{agent_id}/artifacts/**"],
            "network_domains": ["idealista.com", "fotocasa.es"],
            "skills": ["file_ops", "chrome_open"], "destructive_ops": [],
        },
        "estimated_duration_minutes": 90,
        "assumptions": ["User has Chrome installed"],
    })

    def fake_qwen_chat(prompt, system_prompt=None, max_tokens=4000, **kw):
        return fake_qwen_response

    monkeypatch.setattr(cap, "_qwen_chat", fake_qwen_chat)

    fake_registry = MagicMock()
    fake_registry.names.return_value = ["file_ops", "chrome_open"]

    plan = cap.draft_plan(
        agent_id="test_agent",
        description="Build a Telegram bot that scrapes Marbella property listings",
        registry=fake_registry,
    )
    assert plan.agent_id == "test_agent"
    assert len(plan.checkpoints) == 2
    assert "idealista.com" in plan.permission_manifest.network_domains


def test_draft_plan_rejects_unknown_skill(monkeypatch):
    import codec_agent_plan as cap

    fake_response = json.dumps({
        "goals": ["x"], "checkpoints": [
            {"title": "t", "description": "d",
             "skills_needed": ["nonexistent_skill_xyz"],
             "expected_output": "o", "step_budget": 10}
        ],
        "permission_manifest": {
            "read_paths": [], "write_paths": [], "network_domains": [],
            "skills": ["nonexistent_skill_xyz"], "destructive_ops": [],
        },
        "estimated_duration_minutes": 5, "assumptions": [],
    })
    monkeypatch.setattr(cap, "_qwen_chat", lambda *a, **k: fake_response)

    fake_registry = MagicMock()
    fake_registry.names.return_value = ["weather"]  # nonexistent_skill_xyz NOT in registry

    with pytest.raises(cap.PlanValidationError) as exc_info:
        cap.draft_plan(
            agent_id="test_agent",
            description="some project",
            registry=fake_registry,
        )
    assert "nonexistent_skill_xyz" in str(exc_info.value)


def test_draft_plan_handles_qwen_unavailable(monkeypatch):
    import codec_agent_plan as cap

    def raise_connection(*a, **k):
        raise ConnectionError("qwen3.6 down")

    monkeypatch.setattr(cap, "_qwen_chat", raise_connection)

    with pytest.raises(cap.QwenUnavailableError):
        cap.draft_plan(
            agent_id="test_agent",
            description="x",
            registry=MagicMock(),
        )
```

- [ ] **Step 2: Run tests, verify fail**

Run: `python3.13 -m pytest tests/test_agent_plan.py -k "draft_plan" -v`
Expected: FAIL — `draft_plan` not defined.

- [ ] **Step 3: Add Qwen client + draft_plan**

Append to `codec_agent_plan.py`:

```python
# ── Qwen-3.6 client ───────────────────────────────────────────────────────────
QWEN_URL = "http://127.0.0.1:8090/v1/chat/completions"
QWEN_MODEL = "qwen3.6"
QWEN_TIMEOUT = 60  # seconds


class QwenUnavailableError(RuntimeError):
    """Qwen-3.6 service down or unreachable."""


class PlanValidationError(ValueError):
    """Plan failed schema or skill-registry validation."""


def _qwen_chat(user_prompt: str, system_prompt: str = "",
               max_tokens: int = 4000) -> str:
    """Call local Qwen-3.6 OpenAI-compatible endpoint. Returns the
    assistant's content string. Raises QwenUnavailableError on
    network failure or non-2xx response."""
    import requests  # lazy import — avoid forcing requests on test machines without it

    payload = {
        "model": QWEN_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt or ""},
            {"role": "user",   "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    try:
        r = requests.post(QWEN_URL, json=payload, timeout=QWEN_TIMEOUT)
    except requests.exceptions.ConnectionError as e:
        raise QwenUnavailableError(f"qwen3.6 unreachable: {e}")
    except requests.exceptions.Timeout:
        raise QwenUnavailableError("qwen3.6 request timed out")
    if r.status_code != 200:
        raise QwenUnavailableError(f"qwen3.6 returned {r.status_code}: {r.text[:200]}")
    try:
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except (KeyError, json.JSONDecodeError) as e:
        raise QwenUnavailableError(f"qwen3.6 returned malformed response: {e}")


# ── Plan drafting ─────────────────────────────────────────────────────────────
_PLAN_SYSTEM_PROMPT = """You are CODEC's plan generator. The user describes a project. \
You return ONLY a JSON object matching this schema:

{
  "goals":         [<string>, ...],
  "checkpoints": [
    {
      "title":           <string>,
      "description":     <string>,
      "skills_needed":   [<skill_name>, ...],
      "expected_output": <string>,
      "step_budget":     <int, default 30>
    }
  ],
  "permission_manifest": {
    "read_paths":      [<glob>, ...],
    "write_paths":     [<glob — MUST be under ~/.codec/agents/{agent_id}/artifacts/ unless user grants more>, ...],
    "network_domains": [<domain>, ...],
    "skills":          [<union of all checkpoints.skills_needed>, ...],
    "destructive_ops": [<op-id>, ...]
  },
  "estimated_duration_minutes": <int>,
  "assumptions": [<string>, ...]
}

Rules:
- Output ONLY valid JSON. No prose before or after.
- skills_needed MUST be skill names from the user-supplied registry list. Never invent skill names.
- write_paths default to ~/.codec/agents/{agent_id}/artifacts/** unless the project explicitly requires writing elsewhere.
- destructive_ops list any irreversible operations (deletes, payments, sending emails on user's behalf). They will require additional consent at runtime.
- estimated_duration_minutes is your best honest guess.
"""


def draft_plan(agent_id: str, description: str, registry=None,
               available_skills: Optional[List[str]] = None) -> Plan:
    """Call Qwen-3.6 with the project description, parse response into Plan,
    validate against skill registry. Raises PlanValidationError on schema or
    validation failure; QwenUnavailableError on LLM unavailability."""
    if registry is None:
        try:
            from codec_dispatch import registry as _reg
            registry = _reg
        except Exception:
            raise PlanValidationError("codec_dispatch unavailable; cannot validate skills")

    if available_skills is None:
        available_skills = sorted(registry.names() or [])

    user_prompt = (
        f"agent_id: {agent_id}\n\n"
        f"Available skills (registry): {', '.join(available_skills)}\n\n"
        f"Project description:\n{description}\n\n"
        f"Generate the JSON plan now."
    )

    raw = _qwen_chat(user_prompt, _PLAN_SYSTEM_PROMPT)

    # Strip code fences if Qwen wraps in ```json ... ```
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)

    try:
        d = json.loads(raw)
    except json.JSONDecodeError as e:
        raise PlanValidationError(f"qwen3.6 returned non-JSON: {e}; raw={raw[:300]!r}")

    # Inject schema + agent_id (LLM doesn't need to know schema number)
    d.setdefault("schema", PLAN_SCHEMA_VERSION)
    d.setdefault("agent_id", agent_id)

    # Compute checkpoint IDs deterministically
    for cp in d.get("checkpoints", []):
        cp.setdefault("id", _stable_checkpoint_id(cp))

    try:
        plan = plan_from_dict(d)
    except (KeyError, ValueError, TypeError) as e:
        raise PlanValidationError(f"plan schema invalid: {e}")

    ok, missing = validate_plan_skills(plan, registry=registry)
    if not ok:
        raise PlanValidationError(
            f"plan references unknown skills: {missing}"
        )

    return plan


def _stable_checkpoint_id(cp_dict: Dict[str, Any]) -> str:
    """SHA-1 first 8 of (title + description). Stable across re-drafts of
    the same conceptual checkpoint."""
    seed = f"{cp_dict.get('title', '')}|{cp_dict.get('description', '')}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python3.13 -m pytest tests/test_agent_plan.py -k "draft_plan" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add codec_agent_plan.py tests/test_agent_plan.py
git commit -m "feat(agent_plan): Qwen-3.6 plan drafter + validation"
```

---

## Task 7: Vague-description clarifying loop (Q3)

**Files:**
- Modify: `codec_agent_plan.py`
- Test: `tests/test_agent_plan.py`

- [ ] **Step 1: Write the failing test**

```python
def test_vague_description_triggers_clarifying_questions(monkeypatch):
    import codec_agent_plan as cap

    # First Qwen call → "too vague" sentinel
    # Second call → asks 3 clarifying questions
    # Third call (after user answers) → returns valid plan
    call_count = {"n": 0}

    def fake_qwen(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return json.dumps({"too_vague": True,
                               "clarifying_questions": ["What platform?", "What output?", "Who's the user?"]})
        # Subsequent calls return a valid plan
        return json.dumps({
            "goals": ["g"],
            "checkpoints": [{"title": "t", "description": "d",
                             "skills_needed": ["weather"],
                             "expected_output": "o", "step_budget": 10}],
            "permission_manifest": {"read_paths": [], "write_paths": [],
                                    "network_domains": [], "skills": ["weather"],
                                    "destructive_ops": []},
            "estimated_duration_minutes": 5, "assumptions": [],
        })

    monkeypatch.setattr(cap, "_qwen_chat", fake_qwen)

    fake_ask = MagicMock()
    fake_ask.return_value = ("answered", "telegram bot, JSON output, real estate buyers")
    monkeypatch.setattr(cap, "_ask_user", fake_ask)

    fake_registry = MagicMock()
    fake_registry.names.return_value = ["weather"]

    plan = cap.draft_plan_with_clarification(
        agent_id="a", description="make codec better",
        registry=fake_registry,
    )
    assert plan is not None
    assert call_count["n"] >= 2  # at least one re-draft after clarification
    fake_ask.assert_called()


def test_vague_description_max_clarifying_rounds_exceeded(monkeypatch):
    import codec_agent_plan as cap

    monkeypatch.setattr(cap, "_qwen_chat",
                        lambda *a, **k: json.dumps({
                            "too_vague": True,
                            "clarifying_questions": ["q1", "q2"],
                        }))
    monkeypatch.setattr(cap, "_ask_user", lambda *a, **k: ("answered", "still vague"))

    fake_registry = MagicMock()
    fake_registry.names.return_value = []

    with pytest.raises(cap.DescriptionTooVagueError):
        cap.draft_plan_with_clarification(
            agent_id="a", description="x",
            registry=fake_registry, max_rounds=cap.MAX_CLARIFYING_ROUNDS,
        )
```

- [ ] **Step 2: Run tests, verify fail**

Run: `python3.13 -m pytest tests/test_agent_plan.py -k "vague_description" -v`
Expected: FAIL — `draft_plan_with_clarification` not defined.

- [ ] **Step 3: Add clarifying loop**

Append to `codec_agent_plan.py`:

```python
class DescriptionTooVagueError(ValueError):
    """User's project description couldn't be scoped after MAX_CLARIFYING_ROUNDS."""


def _ask_user(question: str, *, agent_id: str,
              deadline_seconds: int = 600) -> Tuple[str, Any]:
    """Lazy-loaded codec_ask_user.ask wrapper. Returns (status, answer).
    status ∈ {"answered", "ambiguous_consent", "timeout"}."""
    try:
        from codec_ask_user import ask, TIMEOUT_SENTINEL
    except Exception as e:
        log.warning("codec_ask_user unavailable: %s", e)
        return ("timeout", TIMEOUT_SENTINEL if 'TIMEOUT_SENTINEL' in dir() else None)
    return ask(question, source=f"agent_plan:{agent_id}",
               deadline_seconds=deadline_seconds)


def draft_plan_with_clarification(agent_id: str, description: str,
                                  registry=None,
                                  max_rounds: int = MAX_CLARIFYING_ROUNDS) -> Plan:
    """Wrap draft_plan with a clarifying-question loop. If LLM returns
    {"too_vague": True, "clarifying_questions": [...]}, ask user via
    codec_ask_user.ask, append answers to description, retry. After
    max_rounds without convergence, raise DescriptionTooVagueError."""
    enriched_description = description

    for round_idx in range(max_rounds + 1):
        try:
            return draft_plan(agent_id, enriched_description, registry=registry)
        except PlanValidationError as e:
            # Check if this was a "too_vague" response (sentinel from LLM)
            if "too_vague" in str(e).lower():
                if round_idx >= max_rounds:
                    raise DescriptionTooVagueError(
                        f"description still too vague after {max_rounds} rounds"
                    )
                # Re-call qwen JUST to extract clarifying questions
                raw = _qwen_chat(
                    user_prompt=enriched_description,
                    system_prompt="The previous attempt was too vague. Output ONLY a JSON object: "
                                  "{\"clarifying_questions\": [<q1>, <q2>, <q3>]}",
                )
                try:
                    qs = json.loads(raw).get("clarifying_questions", [])
                except json.JSONDecodeError:
                    qs = ["Can you describe what you want CODEC to build, in more concrete terms?"]
                # Ask user; combine answer with description and retry
                full_q = "I need clarification before drafting a plan:\n\n" + \
                         "\n".join(f"  {i+1}. {q}" for i, q in enumerate(qs[:3]))
                status, ans = _ask_user(full_q, agent_id=agent_id)
                if status != "answered":
                    raise DescriptionTooVagueError(
                        f"clarification not answered (status={status})"
                    )
                enriched_description = (
                    f"{enriched_description}\n\n[user clarification round {round_idx+1}]\n{ans}"
                )
            else:
                raise  # bubble other validation errors

    raise DescriptionTooVagueError(f"reached max_rounds={max_rounds} unexpectedly")
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python3.13 -m pytest tests/test_agent_plan.py -k "vague_description" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add codec_agent_plan.py tests/test_agent_plan.py
git commit -m "feat(agent_plan): vague-description clarifying loop via ask_user"
```

---

## Task 8: Global allowlist (Q4)

**Files:**
- Modify: `codec_agent_plan.py`
- Test: `tests/test_agent_plan.py`

- [ ] **Step 1: Write the failing test**

```python
def test_global_grants_load_returns_empty_when_missing(temp_codec_dir):
    from codec_agent_plan import load_global_grants
    g = load_global_grants()
    assert g == {"schema": 1, "version": 0,
                 "network_domains": [], "read_paths": [],
                 "write_paths": [], "skills": []}


def test_add_global_grant_persists(temp_codec_dir):
    from codec_agent_plan import add_global_grant, load_global_grants
    add_global_grant("network_domains", "github.com")
    add_global_grant("network_domains", "news.ycombinator.com")
    add_global_grant("skills", "web_fetch")
    g = load_global_grants()
    assert "github.com" in g["network_domains"]
    assert "news.ycombinator.com" in g["network_domains"]
    assert "web_fetch" in g["skills"]
    assert g["version"] == 3  # 3 successful adds


def test_remove_global_grant(temp_codec_dir):
    from codec_agent_plan import add_global_grant, remove_global_grant, load_global_grants
    add_global_grant("network_domains", "github.com")
    add_global_grant("network_domains", "example.com")
    remove_global_grant("network_domains", "github.com")
    g = load_global_grants()
    assert "github.com" not in g["network_domains"]
    assert "example.com" in g["network_domains"]
```

- [ ] **Step 2: Run tests, verify fail**

Run: `python3.13 -m pytest tests/test_agent_plan.py -k "global_grant" -v`
Expected: FAIL — global grant functions not defined.

- [ ] **Step 3: Add global allowlist functions**

Append to `codec_agent_plan.py`:

```python
# ── Global allowlist (Q4 — cross-agent permissions) ───────────────────────────
_GLOBAL_GRANT_KINDS = frozenset({
    "network_domains", "read_paths", "write_paths", "skills",
})


def _empty_global_grants() -> Dict[str, Any]:
    return {
        "schema": GLOBAL_GRANTS_SCHEMA_VERSION, "version": 0,
        "network_domains": [], "read_paths": [], "write_paths": [], "skills": [],
    }


def load_global_grants() -> Dict[str, Any]:
    """Read ~/.codec/agent_global_grants.json, returning empty struct if missing."""
    return _read_json(_GLOBAL_GRANTS_PATH) or _empty_global_grants()


def add_global_grant(kind: str, value: str) -> None:
    """Add `value` to the global allowlist for `kind`. Idempotent.
    Bumps version and writes atomically."""
    if kind not in _GLOBAL_GRANT_KINDS:
        raise ValueError(f"invalid grant kind: {kind!r}; expected one of {sorted(_GLOBAL_GRANT_KINDS)}")
    g = load_global_grants()
    if value not in g[kind]:
        g[kind] = sorted(g[kind] + [value])
    g["version"] = int(g.get("version", 0)) + 1
    g["updated_at"] = _now_iso()
    _atomic_write_json(_GLOBAL_GRANTS_PATH, g)


def remove_global_grant(kind: str, value: str) -> None:
    """Remove `value` from `kind`. Idempotent (no-op if absent)."""
    if kind not in _GLOBAL_GRANT_KINDS:
        raise ValueError(f"invalid grant kind: {kind!r}")
    g = load_global_grants()
    if value in g[kind]:
        g[kind] = [v for v in g[kind] if v != value]
    g["version"] = int(g.get("version", 0)) + 1
    g["updated_at"] = _now_iso()
    _atomic_write_json(_GLOBAL_GRANTS_PATH, g)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python3.13 -m pytest tests/test_agent_plan.py -k "global_grant" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add codec_agent_plan.py tests/test_agent_plan.py
git commit -m "feat(agent_plan): global allowlist tier (Q4)"
```

---

## Task 9: State machine (status transitions)

**Files:**
- Modify: `codec_agent_plan.py`
- Test: `tests/test_agent_plan.py`

- [ ] **Step 1: Write the failing test**

```python
def test_state_transition_valid(temp_codec_dir):
    from codec_agent_plan import set_status, load_state, save_manifest
    save_manifest("a1", {"agent_id": "a1", "title": "t",
                         "status": "draft_pending", "created_at": "2026-05-03"})
    set_status("a1", "awaiting_approval")
    state = load_state("a1")
    # Status mirrored in state.json AND manifest.json
    from codec_agent_plan import load_manifest
    m = load_manifest("a1")
    assert m["status"] == "awaiting_approval"


def test_state_transition_invalid_raises(temp_codec_dir):
    import codec_agent_plan as cap
    cap.save_manifest("a1", {"agent_id": "a1", "status": "draft_pending"})

    # Cannot jump from draft_pending → completed without going through approved
    with pytest.raises(cap.InvalidStatusTransition):
        cap.set_status("a1", "completed")
```

- [ ] **Step 2: Run tests, verify fail**

Run: `python3.13 -m pytest tests/test_agent_plan.py -k "state_transition" -v`
Expected: FAIL — `set_status` not defined.

- [ ] **Step 3: Add state machine**

Append to `codec_agent_plan.py`:

```python
# ── Status transitions ────────────────────────────────────────────────────────
class InvalidStatusTransition(ValueError):
    """Disallowed status transition attempted."""


# Step 8 only manages: draft_pending → awaiting_approval → approved/rejected/revised.
# Step 9 introduces: approved → running → checkpoint_completed/blocked_*/aborted/completed.
# This map will be EXTENDED in Step 9.
_VALID_TRANSITIONS: Dict[str, frozenset] = {
    "draft_pending":      frozenset({"awaiting_approval", "plan_failed"}),
    "awaiting_approval":  frozenset({"approved", "rejected", "revised"}),
    "revised":            frozenset({"awaiting_approval"}),
    "approved":           frozenset({"rejected"}),  # Step 9 will add: running
    "rejected":           frozenset(),
    "plan_failed":        frozenset({"draft_pending"}),  # retry path
}


def set_status(agent_id: str, new_status: str, reason: Optional[str] = None) -> None:
    """Atomically transition manifest.json's status. Raises
    InvalidStatusTransition if the move violates the state machine."""
    manifest = load_manifest(agent_id)
    current = manifest.get("status", "draft_pending")
    allowed = _VALID_TRANSITIONS.get(current, frozenset())
    if new_status not in allowed:
        raise InvalidStatusTransition(
            f"cannot transition {current!r} → {new_status!r} "
            f"(allowed: {sorted(allowed)})"
        )
    manifest["status"] = new_status
    manifest["updated_at"] = _now_iso()
    if reason:
        manifest["status_reason"] = reason
    save_manifest(agent_id, manifest)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python3.13 -m pytest tests/test_agent_plan.py -k "state_transition" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codec_agent_plan.py tests/test_agent_plan.py
git commit -m "feat(agent_plan): state machine for Step 8 status transitions"
```

---

## Task 10: Plan creation orchestrator (the public entry point)

**Files:**
- Modify: `codec_agent_plan.py`
- Test: `tests/test_agent_plan.py`

- [ ] **Step 1: Write the failing test**

```python
def test_create_agent_full_flow(monkeypatch, temp_codec_dir):
    import codec_agent_plan as cap

    monkeypatch.setattr(cap, "_qwen_chat", lambda *a, **k: json.dumps({
        "goals": ["g"],
        "checkpoints": [{"title": "t", "description": "d",
                         "skills_needed": ["weather"],
                         "expected_output": "o", "step_budget": 10}],
        "permission_manifest": {"read_paths": [], "write_paths": [],
                                "network_domains": [], "skills": ["weather"],
                                "destructive_ops": []},
        "estimated_duration_minutes": 5, "assumptions": [],
    }))

    fake_registry = MagicMock()
    fake_registry.names.return_value = ["weather"]

    audit_emits = []
    def fake_audit(event, source, message, **kw):
        audit_emits.append((event, kw.get("correlation_id")))
    monkeypatch.setattr(cap, "_audit", fake_audit)

    agent_id = cap.create_agent(
        title="Property bot",
        description="Build a property scraper",
        registry=fake_registry,
    )
    assert agent_id.startswith("agent_")

    # Verify all 3 files written
    agent_dir = temp_codec_dir / "agents" / agent_id
    assert (agent_dir / "manifest.json").exists()
    assert (agent_dir / "plan.json").exists()
    assert (agent_dir / "state.json").exists()

    # Manifest has correct fields
    m = cap.load_manifest(agent_id)
    assert m["title"] == "Property bot"
    assert m["status"] == "awaiting_approval"
    assert "created_at" in m

    # Audit emit happened with correlation_id
    plan_drafted = [(e, c) for e, c in audit_emits if e == "agent_plan_drafted"]
    assert len(plan_drafted) == 1
```

- [ ] **Step 2: Run test, verify fail**

Run: `python3.13 -m pytest tests/test_agent_plan.py::test_create_agent_full_flow -v`
Expected: FAIL — `create_agent` not defined.

- [ ] **Step 3: Add create_agent + audit helper**

Append to `codec_agent_plan.py`:

```python
# ── Audit helper ──────────────────────────────────────────────────────────────
def _audit(event: str, source: str, message: str = "",
           correlation_id: str = "", outcome: str = "ok",
           level: str = "info", extra: Optional[Dict[str, Any]] = None) -> None:
    """Lazy-imported codec_audit emit. Centralized so tests can monkeypatch."""
    try:
        from codec_audit import audit
    except Exception as e:
        log.debug("codec_audit unavailable for %s: %s", event, e)
        return
    audit(event=event, source=source, message=message,
          correlation_id=correlation_id, outcome=outcome,
          level=level, extra=dict(extra or {}))


# ── Public orchestrator ───────────────────────────────────────────────────────
def _new_agent_id() -> str:
    return f"agent_{secrets.token_hex(6)}"


def create_agent(title: str, description: str,
                 registry=None,
                 notification_channels: Optional[List[str]] = None) -> str:
    """Top-level entry point. Drafts a plan, persists to disk, emits audit.
    Returns the new agent_id. Status after this call: awaiting_approval
    (or plan_failed on validation error)."""
    agent_id = _new_agent_id()
    cid = secrets.token_hex(6)

    # Initial manifest
    manifest = {
        "agent_id": agent_id,
        "title": title[:120],
        "status": "draft_pending",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "notification_channels": notification_channels or ["pwa"],
    }
    save_manifest(agent_id, manifest)
    save_state(agent_id, {"current_checkpoint": 0})

    # Draft plan (with clarification loop)
    try:
        plan = draft_plan_with_clarification(agent_id, description, registry=registry)
    except DescriptionTooVagueError as e:
        set_status(agent_id, "plan_failed", reason=f"too_vague: {e}")
        _audit("agent_plan_rejected", "codec-agent-plan",
               f"plan failed (vague): {e}", correlation_id=cid,
               outcome="warning", level="warning",
               extra={"agent_id": agent_id, "reason": "too_vague"})
        raise
    except (PlanValidationError, QwenUnavailableError) as e:
        set_status(agent_id, "plan_failed", reason=str(e))
        _audit("agent_plan_rejected", "codec-agent-plan",
               f"plan failed: {e}", correlation_id=cid,
               outcome="error", level="error",
               extra={"agent_id": agent_id, "reason": str(e)[:200]})
        raise

    # Persist plan + transition status
    save_plan(plan)
    set_status(agent_id, "awaiting_approval")

    _audit("agent_plan_drafted", "codec-agent-plan",
           f"plan drafted for {title[:60]}", correlation_id=cid,
           extra={
               "agent_id": agent_id,
               "checkpoint_count": len(plan.checkpoints),
               "estimated_duration_minutes": plan.estimated_duration_minutes,
               "skills_count": len(plan.permission_manifest.skills),
               "domains_count": len(plan.permission_manifest.network_domains),
           })

    return agent_id
```

- [ ] **Step 4: Run test, verify pass**

Run: `python3.13 -m pytest tests/test_agent_plan.py::test_create_agent_full_flow -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codec_agent_plan.py tests/test_agent_plan.py
git commit -m "feat(agent_plan): create_agent orchestrator + audit emits"
```

---

## Task 11: Approve / Reject / Revise functions

**Files:**
- Modify: `codec_agent_plan.py`
- Test: `tests/test_agent_plan.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_approve_writes_grants_and_plan_hash(monkeypatch, temp_codec_dir):
    import codec_agent_plan as cap
    monkeypatch.setattr(cap, "_qwen_chat", lambda *a, **k: json.dumps({
        "goals": ["g"], "checkpoints": [{"title": "t", "description": "d",
            "skills_needed": ["weather"], "expected_output": "o", "step_budget": 10}],
        "permission_manifest": {"read_paths": [], "write_paths": [],
            "network_domains": ["example.com"], "skills": ["weather"],
            "destructive_ops": []},
        "estimated_duration_minutes": 5, "assumptions": []}))
    fake_reg = MagicMock(); fake_reg.names.return_value = ["weather"]

    agent_id = cap.create_agent(title="t", description="d", registry=fake_reg)
    cap.approve_plan(agent_id)

    m = cap.load_manifest(agent_id)
    assert m["status"] == "approved"
    assert "plan_hash" in m
    assert len(m["plan_hash"]) == 64  # sha256 hex

    grants = cap.load_grants(agent_id)
    assert "example.com" in grants["network_domains"]
    assert "weather" in grants["skills"]


def test_reject_sets_status_with_reason(monkeypatch, temp_codec_dir):
    import codec_agent_plan as cap
    monkeypatch.setattr(cap, "_qwen_chat", lambda *a, **k: json.dumps({
        "goals": ["g"], "checkpoints": [{"title": "t", "description": "d",
            "skills_needed": ["weather"], "expected_output": "o", "step_budget": 10}],
        "permission_manifest": {"read_paths": [], "write_paths": [],
            "network_domains": [], "skills": ["weather"], "destructive_ops": []},
        "estimated_duration_minutes": 5, "assumptions": []}))
    fake_reg = MagicMock(); fake_reg.names.return_value = ["weather"]

    agent_id = cap.create_agent(title="t", description="d", registry=fake_reg)
    cap.reject_plan(agent_id, reason="don't need this")
    m = cap.load_manifest(agent_id)
    assert m["status"] == "rejected"
    assert m["status_reason"] == "don't need this"
```

- [ ] **Step 2: Run tests, verify fail**

Run: `python3.13 -m pytest tests/test_agent_plan.py -k "approve_writes or reject_sets" -v`
Expected: FAIL.

- [ ] **Step 3: Add approve_plan + reject_plan + revise_plan**

Append to `codec_agent_plan.py`:

```python
def approve_plan(agent_id: str) -> Dict[str, Any]:
    """Transition awaiting_approval → approved. Computes plan_hash for
    Step 9 tamper detection. Writes grants.json (= full manifest by
    default — Step 8 doesn't yet support partial grants; approve == all)."""
    plan = load_plan(agent_id)
    if plan is None:
        raise ValueError(f"no plan found for {agent_id!r}")

    plan_hash = compute_plan_hash(plan)

    grants = {
        "schema": 1,
        "agent_id": agent_id,
        "approved_at": _now_iso(),
        # Initial v1: grants == manifest (no partial grants yet)
        **asdict(plan.permission_manifest),
    }
    save_grants(agent_id, grants)

    # Update manifest with hash + transition status
    manifest = load_manifest(agent_id)
    manifest["plan_hash"] = plan_hash
    manifest["approved_at"] = _now_iso()
    save_manifest(agent_id, manifest)
    set_status(agent_id, "approved")

    cid = secrets.token_hex(6)
    _audit("agent_plan_approved", "codec-agent-plan",
           f"plan approved for {agent_id}",
           correlation_id=cid,
           extra={
               "agent_id": agent_id, "plan_hash": plan_hash,
               "checkpoint_count": len(plan.checkpoints),
               "skills_count": len(plan.permission_manifest.skills),
               "domains_count": len(plan.permission_manifest.network_domains),
           })

    return grants


def reject_plan(agent_id: str, reason: str = "") -> None:
    """Transition awaiting_approval → rejected. Plan dir kept for review/TTL."""
    set_status(agent_id, "rejected", reason=reason or "no reason")

    cid = secrets.token_hex(6)
    _audit("agent_plan_rejected", "codec-agent-plan",
           f"plan rejected for {agent_id}: {reason[:80]}",
           correlation_id=cid, outcome="warning",
           extra={"agent_id": agent_id, "reason": reason[:200]})


def revise_plan(agent_id: str, edited_plan_dict: Dict[str, Any],
                registry=None) -> Plan:
    """User submitted an edited plan. Re-validate against registry.
    On success: persist new plan, transition awaiting_approval → revised
    → awaiting_approval (immediately) so user re-reviews."""
    edited_plan_dict.setdefault("schema", PLAN_SCHEMA_VERSION)
    edited_plan_dict.setdefault("agent_id", agent_id)

    try:
        plan = plan_from_dict(edited_plan_dict)
    except (KeyError, ValueError, TypeError) as e:
        raise PlanValidationError(f"edited plan schema invalid: {e}")

    ok, missing = validate_plan_skills(plan, registry=registry)
    if not ok:
        raise PlanValidationError(f"edited plan references unknown skills: {missing}")

    save_plan(plan)
    # Transition: awaiting_approval → revised → back to awaiting_approval
    set_status(agent_id, "revised")
    set_status(agent_id, "awaiting_approval")

    cid = secrets.token_hex(6)
    _audit("agent_plan_revised", "codec-agent-plan",
           f"plan revised for {agent_id}", correlation_id=cid,
           extra={
               "agent_id": agent_id,
               "checkpoint_count": len(plan.checkpoints),
           })

    return plan
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python3.13 -m pytest tests/test_agent_plan.py -k "approve_writes or reject_sets" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codec_agent_plan.py tests/test_agent_plan.py
git commit -m "feat(agent_plan): approve/reject/revise functions"
```

---

## Task 12: PWA endpoints — agents CRUD (POST/GET/list)

**Files:**
- Create: `routes/agents.py`
- Modify: `codec_dashboard.py` (mount router)
- Test: `tests/test_agent_plan.py`

- [ ] **Step 1: Write the failing test**

```python
def test_post_api_agents_creates_drafts(monkeypatch, temp_codec_dir, tmp_path):
    """POST /api/agents creates an agent and drafts the plan."""
    from fastapi.testclient import TestClient
    import codec_agent_plan as cap

    monkeypatch.setattr(cap, "_qwen_chat", lambda *a, **k: json.dumps({
        "goals": ["g"], "checkpoints": [{"title": "t", "description": "d",
            "skills_needed": ["weather"], "expected_output": "o", "step_budget": 10}],
        "permission_manifest": {"read_paths": [], "write_paths": [],
            "network_domains": [], "skills": ["weather"], "destructive_ops": []},
        "estimated_duration_minutes": 5, "assumptions": []}))
    fake_reg = MagicMock(); fake_reg.names.return_value = ["weather"]
    monkeypatch.setattr("codec_agent_plan.draft_plan_with_clarification",
                        lambda agent_id, desc, registry=None, max_rounds=3:
                            cap.draft_plan(agent_id, desc, registry=fake_reg))

    from routes.agents import router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.post("/api/agents", json={
        "title": "Property bot",
        "description": "Build a property scraper",
        "notification_channels": ["pwa"],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["agent_id"].startswith("agent_")
    assert body["status"] == "awaiting_approval"


def test_get_api_agents_lists_all(temp_codec_dir):
    """GET /api/agents returns all agents."""
    from fastapi.testclient import TestClient
    import codec_agent_plan as cap

    # Create 2 agents directly via R/W (bypass LLM)
    cap.save_manifest("agent_a", {"agent_id": "agent_a", "title": "A",
                                   "status": "awaiting_approval", "created_at": "..."})
    cap.save_manifest("agent_b", {"agent_id": "agent_b", "title": "B",
                                   "status": "approved", "created_at": "..."})

    from routes.agents import router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.get("/api/agents")
    assert r.status_code == 200
    body = r.json()
    ids = {a["agent_id"] for a in body["agents"]}
    assert ids == {"agent_a", "agent_b"}
```

- [ ] **Step 2: Run tests, verify fail**

Run: `python3.13 -m pytest tests/test_agent_plan.py -k "post_api_agents or get_api_agents_lists" -v`
Expected: FAIL — `routes.agents` doesn't exist.

- [ ] **Step 3: Create routes/agents.py**

```python
"""CODEC Phase 3 Step 8 — PWA endpoints for agent management.

Mounted from codec_dashboard.py. All endpoints under /api/agents.
Authentication is enforced by codec_dashboard's auth middleware
(applied to the entire app), so these endpoints inherit it.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import codec_agent_plan as cap

log = logging.getLogger("routes.agents")
router = APIRouter()


# ── Request models ────────────────────────────────────────────────────────────
class CreateAgentBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    description: str = Field(..., min_length=1)
    notification_channels: Optional[List[str]] = Field(default=None)


class RejectBody(BaseModel):
    reason: str = Field(default="", max_length=500)


class ReviseBody(BaseModel):
    edited_plan: Dict[str, Any] = Field(...)


class GlobalGrantBody(BaseModel):
    kind: str = Field(...)
    value: str = Field(..., min_length=1)


# ── Endpoints ─────────────────────────────────────────────────────────────────
@router.post("/api/agents")
def create_agent(body: CreateAgentBody):
    try:
        agent_id = cap.create_agent(
            title=body.title,
            description=body.description,
            notification_channels=body.notification_channels,
        )
    except cap.DescriptionTooVagueError as e:
        raise HTTPException(status_code=400, detail=f"description too vague: {e}")
    except cap.PlanValidationError as e:
        raise HTTPException(status_code=400, detail=f"plan invalid: {e}")
    except cap.QwenUnavailableError as e:
        raise HTTPException(status_code=503, detail=f"Qwen-3.6 unavailable: {e}")

    manifest = cap.load_manifest(agent_id)
    return {"agent_id": agent_id, "status": manifest.get("status", "unknown")}


@router.get("/api/agents")
def list_agents():
    """List all agents (any status). Returns a thin manifest summary."""
    out: List[Dict[str, Any]] = []
    if not cap._AGENTS_DIR.exists():
        return {"agents": []}
    for d in sorted(cap._AGENTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        m = cap.load_manifest(d.name)
        if m:
            out.append({
                "agent_id": m.get("agent_id", d.name),
                "title":    m.get("title", "(untitled)"),
                "status":   m.get("status", "unknown"),
                "created_at": m.get("created_at"),
                "updated_at": m.get("updated_at"),
            })
    return {"agents": out}


@router.get("/api/agents/{agent_id}")
def get_agent(agent_id: str):
    manifest = cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    plan = cap.load_plan(agent_id)
    state = cap.load_state(agent_id)
    grants = cap.load_grants(agent_id) or None
    return {
        "manifest": manifest,
        "plan": plan.to_dict() if plan else None,
        "state": state,
        "grants": grants,
    }


@router.post("/api/agents/{agent_id}/approve")
def approve_agent(agent_id: str):
    manifest = cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    try:
        grants = cap.approve_plan(agent_id)
    except cap.InvalidStatusTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"agent_id": agent_id, "status": "approved", "grants": grants}


@router.post("/api/agents/{agent_id}/reject")
def reject_agent(agent_id: str, body: RejectBody):
    manifest = cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    try:
        cap.reject_plan(agent_id, reason=body.reason)
    except cap.InvalidStatusTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"agent_id": agent_id, "status": "rejected"}


@router.post("/api/agents/{agent_id}/revise")
def revise_agent(agent_id: str, body: ReviseBody):
    manifest = cap.load_manifest(agent_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    try:
        plan = cap.revise_plan(agent_id, body.edited_plan)
    except cap.PlanValidationError as e:
        raise HTTPException(status_code=400, detail=f"plan invalid: {e}")
    except cap.InvalidStatusTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"agent_id": agent_id, "status": "awaiting_approval",
            "plan": plan.to_dict()}


@router.get("/api/agent_global_grants")
def get_global_grants():
    return cap.load_global_grants()


@router.post("/api/agent_global_grants")
def add_global_grant(body: GlobalGrantBody):
    try:
        cap.add_global_grant(body.kind, body.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    cap._audit("agent_global_grant_added", "codec-agent-plan",
               f"grant added: {body.kind}={body.value}",
               extra={"kind": body.kind, "value": body.value})
    return cap.load_global_grants()


@router.delete("/api/agent_global_grants")
def delete_global_grant(body: GlobalGrantBody):
    try:
        cap.remove_global_grant(body.kind, body.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    cap._audit("agent_global_grant_removed", "codec-agent-plan",
               f"grant removed: {body.kind}={body.value}",
               extra={"kind": body.kind, "value": body.value})
    return cap.load_global_grants()
```

- [ ] **Step 4: Mount router in codec_dashboard.py**

Open `codec_dashboard.py`. Find the section where other routers are mounted (search for `from routes.triggers import router`). Add immediately below:

```python
# Phase 3 Step 8 — agent management endpoints
from routes.agents import router as _agents_router
app.include_router(_agents_router)
```

- [ ] **Step 5: Run tests, verify pass**

Run: `python3.13 -m pytest tests/test_agent_plan.py -k "post_api_agents or get_api_agents_lists" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add routes/agents.py codec_dashboard.py tests/test_agent_plan.py
git commit -m "feat(routes): /api/agents endpoints + global grants endpoints"
```

---

## Task 13: PWA endpoints — approve/reject/revise integration tests

**Files:**
- Test: `tests/test_agent_plan.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_post_api_agents_approve_full_flow(monkeypatch, temp_codec_dir):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import codec_agent_plan as cap

    monkeypatch.setattr(cap, "_qwen_chat", lambda *a, **k: json.dumps({
        "goals": ["g"], "checkpoints": [{"title": "t", "description": "d",
            "skills_needed": ["weather"], "expected_output": "o", "step_budget": 10}],
        "permission_manifest": {"read_paths": [], "write_paths": [],
            "network_domains": [], "skills": ["weather"], "destructive_ops": []},
        "estimated_duration_minutes": 5, "assumptions": []}))
    fake_reg = MagicMock(); fake_reg.names.return_value = ["weather"]
    # Patch the lazy-import path used inside draft_plan
    monkeypatch.setattr("codec_dispatch.registry", fake_reg, raising=False)

    from routes.agents import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r1 = client.post("/api/agents", json={
        "title": "X", "description": "build x"})
    agent_id = r1.json()["agent_id"]

    r2 = client.post(f"/api/agents/{agent_id}/approve")
    assert r2.status_code == 200
    assert r2.json()["status"] == "approved"

    # Manifest now has plan_hash
    r3 = client.get(f"/api/agents/{agent_id}")
    assert r3.status_code == 200
    assert "plan_hash" in r3.json()["manifest"]
    assert r3.json()["grants"] is not None


def test_post_api_agents_reject_sets_reason(monkeypatch, temp_codec_dir):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import codec_agent_plan as cap

    monkeypatch.setattr(cap, "_qwen_chat", lambda *a, **k: json.dumps({
        "goals": ["g"], "checkpoints": [{"title": "t", "description": "d",
            "skills_needed": ["weather"], "expected_output": "o", "step_budget": 10}],
        "permission_manifest": {"read_paths": [], "write_paths": [],
            "network_domains": [], "skills": ["weather"], "destructive_ops": []},
        "estimated_duration_minutes": 5, "assumptions": []}))
    fake_reg = MagicMock(); fake_reg.names.return_value = ["weather"]
    monkeypatch.setattr("codec_dispatch.registry", fake_reg, raising=False)

    from routes.agents import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r1 = client.post("/api/agents", json={"title": "X", "description": "build x"})
    agent_id = r1.json()["agent_id"]

    r2 = client.post(f"/api/agents/{agent_id}/reject", json={"reason": "not now"})
    assert r2.status_code == 200
    r3 = client.get(f"/api/agents/{agent_id}")
    assert r3.json()["manifest"]["status"] == "rejected"
    assert r3.json()["manifest"]["status_reason"] == "not now"
```

- [ ] **Step 2: Run tests, verify pass (already implemented in Task 12)**

Run: `python3.13 -m pytest tests/test_agent_plan.py -k "post_api_agents_approve_full or post_api_agents_reject_sets" -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_agent_plan.py
git commit -m "test(routes): integration coverage for approve/reject"
```

---

## Task 14: PWA endpoints — global grants

**Files:**
- Test: `tests/test_agent_plan.py`

- [ ] **Step 1: Write the failing test**

```python
def test_global_grants_endpoints_full_flow(temp_codec_dir):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from routes.agents import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # GET — initially empty
    r1 = client.get("/api/agent_global_grants")
    assert r1.status_code == 200
    assert r1.json()["network_domains"] == []

    # POST — add
    r2 = client.post("/api/agent_global_grants",
                      json={"kind": "network_domains", "value": "github.com"})
    assert r2.status_code == 200
    assert "github.com" in r2.json()["network_domains"]

    # POST — invalid kind
    r3 = client.post("/api/agent_global_grants",
                      json={"kind": "evil_thing", "value": "x"})
    assert r3.status_code == 400

    # DELETE
    r4 = client.request("DELETE", "/api/agent_global_grants",
                         json={"kind": "network_domains", "value": "github.com"})
    assert r4.status_code == 200
    assert "github.com" not in r4.json()["network_domains"]
```

- [ ] **Step 2: Run test, verify pass (already implemented in Task 12)**

Run: `python3.13 -m pytest tests/test_agent_plan.py::test_global_grants_endpoints_full_flow -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_agent_plan.py
git commit -m "test(routes): global grants endpoints full flow"
```

---

## Task 15: Auto-approve via global allowlist (the integration moment)

**Files:**
- Modify: `codec_agent_plan.py` (extend `approve_plan` to mark global-already-granted items)
- Test: `tests/test_agent_plan.py`

- [ ] **Step 1: Write the failing test**

```python
def test_approve_marks_global_allowlist_items(monkeypatch, temp_codec_dir):
    """When a plan needs github.com and github.com is already in the
    global allowlist, the approval grants.json should reflect it."""
    import codec_agent_plan as cap

    # Pre-populate global allowlist
    cap.add_global_grant("network_domains", "github.com")
    cap.add_global_grant("skills", "web_fetch")

    monkeypatch.setattr(cap, "_qwen_chat", lambda *a, **k: json.dumps({
        "goals": ["g"], "checkpoints": [{"title": "t", "description": "d",
            "skills_needed": ["web_fetch"], "expected_output": "o", "step_budget": 10}],
        "permission_manifest": {"read_paths": [], "write_paths": [],
            "network_domains": ["github.com", "example.com"],
            "skills": ["web_fetch"], "destructive_ops": []},
        "estimated_duration_minutes": 5, "assumptions": []}))
    fake_reg = MagicMock(); fake_reg.names.return_value = ["web_fetch"]
    monkeypatch.setattr("codec_dispatch.registry", fake_reg, raising=False)

    agent_id = cap.create_agent(title="X", description="d")
    grants = cap.approve_plan(agent_id)

    # All manifest items end up in grants
    assert "github.com" in grants["network_domains"]
    assert "example.com" in grants["network_domains"]
    # Auto-approved tracking (a metadata field, not a separate set)
    assert grants.get("auto_approved", {}).get("network_domains") == ["github.com"]
    assert grants.get("auto_approved", {}).get("skills") == ["web_fetch"]
```

- [ ] **Step 2: Run test, verify fail**

Run: `python3.13 -m pytest tests/test_agent_plan.py::test_approve_marks_global_allowlist_items -v`
Expected: FAIL — `grants["auto_approved"]` is missing.

- [ ] **Step 3: Extend approve_plan**

In `codec_agent_plan.py`, find `approve_plan`. Replace the `grants = {...}` block with:

```python
    # Compute auto_approved subset for UI rendering
    global_grants = load_global_grants()
    auto_approved: Dict[str, List[str]] = {}
    for kind in ("network_domains", "read_paths", "write_paths", "skills"):
        plan_items = getattr(plan.permission_manifest, kind)
        global_items = set(global_grants.get(kind, []))
        approved_via_global = [item for item in plan_items if item in global_items]
        if approved_via_global:
            auto_approved[kind] = approved_via_global

    grants = {
        "schema": 1,
        "agent_id": agent_id,
        "approved_at": _now_iso(),
        "auto_approved": auto_approved,
        **asdict(plan.permission_manifest),
    }
```

- [ ] **Step 4: Run test, verify pass**

Run: `python3.13 -m pytest tests/test_agent_plan.py::test_approve_marks_global_allowlist_items -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codec_agent_plan.py tests/test_agent_plan.py
git commit -m "feat(agent_plan): mark global-allowlist items in grants.json"
```

---

## Task 16: Pre-approval re-validation against registry

**Files:**
- Modify: `codec_agent_plan.py` (extend `approve_plan` to re-validate)
- Test: `tests/test_agent_plan.py`

- [ ] **Step 1: Write the failing test**

```python
def test_approve_revalidates_skills_against_registry(monkeypatch, temp_codec_dir):
    """If a skill was deleted between draft and approval, approval should fail."""
    import codec_agent_plan as cap

    # Initial plan drafted with weather + calculator
    monkeypatch.setattr(cap, "_qwen_chat", lambda *a, **k: json.dumps({
        "goals": ["g"], "checkpoints": [{"title": "t", "description": "d",
            "skills_needed": ["weather", "calculator"],
            "expected_output": "o", "step_budget": 10}],
        "permission_manifest": {"read_paths": [], "write_paths": [],
            "network_domains": [], "skills": ["weather", "calculator"],
            "destructive_ops": []},
        "estimated_duration_minutes": 5, "assumptions": []}))
    fake_reg = MagicMock(); fake_reg.names.return_value = ["weather", "calculator"]
    monkeypatch.setattr("codec_dispatch.registry", fake_reg, raising=False)

    agent_id = cap.create_agent(title="X", description="d")

    # Now simulate calculator was deleted
    fake_reg.names.return_value = ["weather"]

    with pytest.raises(cap.PlanValidationError) as exc:
        cap.approve_plan(agent_id)
    assert "calculator" in str(exc.value)
```

- [ ] **Step 2: Run test, verify fail**

Run: `python3.13 -m pytest tests/test_agent_plan.py::test_approve_revalidates_skills_against_registry -v`
Expected: FAIL — current approve_plan doesn't re-validate.

- [ ] **Step 3: Extend approve_plan with re-validation**

In `codec_agent_plan.py`, at the very top of `approve_plan` (after the `plan = load_plan(agent_id)` line), insert:

```python
    # Re-validate skills against registry (skills may have been deleted between draft & approval)
    ok, missing = validate_plan_skills(plan)
    if not ok:
        raise PlanValidationError(
            f"plan references skills no longer in registry: {missing}"
        )
```

- [ ] **Step 4: Run test, verify pass**

Run: `python3.13 -m pytest tests/test_agent_plan.py::test_approve_revalidates_skills_against_registry -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codec_agent_plan.py tests/test_agent_plan.py
git commit -m "feat(agent_plan): re-validate skill registry at approval time"
```

---

## Task 17: End-to-end integration test

**Files:**
- Test: `tests/test_agent_plan.py`

- [ ] **Step 1: Write the failing test**

```python
def test_e2e_full_lifecycle(monkeypatch, temp_codec_dir):
    """End-to-end: drop project → draft → approve → grants written → audit emits paired."""
    import codec_agent_plan as cap

    monkeypatch.setattr(cap, "_qwen_chat", lambda *a, **k: json.dumps({
        "goals": ["Build property bot"],
        "checkpoints": [
            {"title": "Scaffold project", "description": "Create dir + skeleton",
             "skills_needed": ["file_ops"], "expected_output": "Project initialized",
             "step_budget": 30},
            {"title": "Implement scraper", "description": "Use chrome to scrape",
             "skills_needed": ["chrome_open", "file_ops"],
             "expected_output": "Listings JSON written", "step_budget": 60},
        ],
        "permission_manifest": {
            "read_paths": [],
            "write_paths": ["~/.codec/agents/{agent_id}/artifacts/**"],
            "network_domains": ["idealista.com", "fotocasa.es"],
            "skills": ["file_ops", "chrome_open"], "destructive_ops": [],
        },
        "estimated_duration_minutes": 90,
        "assumptions": ["User has Chrome installed"],
    }))
    fake_reg = MagicMock(); fake_reg.names.return_value = ["file_ops", "chrome_open"]
    monkeypatch.setattr("codec_dispatch.registry", fake_reg, raising=False)

    audit_emits: List[Tuple[str, str]] = []
    def fake_audit(event, source, message="", correlation_id="", **kw):
        audit_emits.append((event, correlation_id))
    monkeypatch.setattr(cap, "_audit", fake_audit)

    # Drop the project
    agent_id = cap.create_agent(
        title="Marbella property bot",
        description="Build a Telegram bot that scrapes Marbella property listings",
    )

    # Approve
    grants = cap.approve_plan(agent_id)

    # Verify final state
    m = cap.load_manifest(agent_id)
    assert m["status"] == "approved"
    assert "plan_hash" in m
    assert grants["network_domains"] == ["idealista.com", "fotocasa.es"]

    # Verify both audit events were emitted (paired correlation_ids will differ — independent ops)
    events = [e for e, _ in audit_emits]
    assert "agent_plan_drafted" in events
    assert "agent_plan_approved" in events
```

- [ ] **Step 2: Run test, verify pass (all components already implemented)**

Run: `python3.13 -m pytest tests/test_agent_plan.py::test_e2e_full_lifecycle -v`
Expected: PASS

- [ ] **Step 3: Run FULL suite to verify no regression**

Run: `python3.13 -m pytest tests/ --ignore=tests/test_smoke.py -q --tb=no`
Expected: 858 (or higher) passed / 20 failed / 73 skipped — same 20/73 baseline as `main`, +25 new from this PR.

- [ ] **Step 4: Commit**

```bash
git add tests/test_agent_plan.py
git commit -m "test(agent_plan): end-to-end lifecycle test"
```

---

## Task 18: Documentation + AGENTS.md update

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Read existing AGENTS.md to find the right insertion points**

Run: `grep -n "^## §" AGENTS.md | head -20`
Note the section numbers and find the natural place to add Phase 3 Step 8 entries (likely a new sub-section under §3 Skills/Plugins, plus extensions to §6 audit events table and §10 don't-touch list).

- [ ] **Step 2: Add Phase 3 Step 8 documentation**

Find the section that describes Phase 2 Step 7 (search for "shift_report" in AGENTS.md). After that section, add:

```markdown
### Phase 3 Step 8 — Plan + Permission Contract

`codec_agent_plan.py` ships drop-a-project planning. User describes a project; Qwen-3.6 drafts a structured plan with explicit permission manifest (read paths, write paths, network domains, skills, destructive ops); user approves in PWA; grants persisted to `~/.codec/agents/<id>/grants.json` with `plan_hash` for tamper detection.

**Storage:**
- `~/.codec/agents/<id>/manifest.json` — id, title, status, plan_hash, timestamps
- `~/.codec/agents/<id>/plan.json` — schema 1, goals, checkpoints, permission manifest
- `~/.codec/agents/<id>/state.json` — current_checkpoint, retry_count
- `~/.codec/agents/<id>/grants.json` — written at approval, includes auto_approved subset
- `~/.codec/agent_global_grants.json` — cross-agent allowlist (Q4)

**Status state machine (Step 8 only — Step 9 extends):**
`draft_pending → awaiting_approval → approved/rejected/revised → awaiting_approval (if revised)`

**Public API (codec_agent_plan):**
- `create_agent(title, description, registry=None)` → returns agent_id
- `approve_plan(agent_id)` → returns grants dict
- `reject_plan(agent_id, reason="")`
- `revise_plan(agent_id, edited_plan_dict, registry=None)` → returns Plan
- `load_global_grants()`, `add_global_grant(kind, value)`, `remove_global_grant(kind, value)`

**PWA endpoints (`routes/agents.py`):**
- `POST /api/agents` (create + draft)
- `GET /api/agents` (list)
- `GET /api/agents/{id}` (detail: manifest + plan + state + grants)
- `POST /api/agents/{id}/approve`
- `POST /api/agents/{id}/reject` (body: reason)
- `POST /api/agents/{id}/revise` (body: edited_plan)
- `GET /api/agent_global_grants`
- `POST /api/agent_global_grants` (body: kind, value)
- `DELETE /api/agent_global_grants` (body: kind, value)

**Kill switch:** `AGENT_PLANNING_ENABLED=false` (stops drafting; existing plans untouched).
```

- [ ] **Step 3: Extend §6 audit events table**

In `AGENTS.md`, find the §6 audit events section. Append rows for the 6 Step 8 events:

```markdown
| `agent_plan_drafted` | codec-agent-plan | info | Plan drafted via Qwen-3.6, ready for user approval |
| `agent_plan_approved` | codec-agent-plan | info | User approved; grants.json written with plan_hash |
| `agent_plan_rejected` | codec-agent-plan | warning | User rejected OR draft failed (vague / qwen-down / validation) |
| `agent_plan_revised` | codec-agent-plan | info | User edited plan inline; re-validated and re-saved |
| `agent_global_grant_added` | codec-agent-plan | info | New global allowlist entry added via PWA |
| `agent_global_grant_removed` | codec-agent-plan | info | Global allowlist entry removed via PWA |
```

- [ ] **Step 4: Extend §10 don't-touch list**

In `AGENTS.md`, find the §10 don't-touch list. Append:

```markdown
- `codec_agent_plan.py` — Phase 3 Step 8. Don't refactor without re-running PHASE3-STEP8 design doc gate.
- `routes/agents.py` — Phase 3 Step 8. Don't change endpoint shapes without bumping API version.
- `~/.codec/agents/**` — runtime state. Never modify outside the documented public API.
- `~/.codec/agent_global_grants.json` — runtime state. Modify only via PWA endpoints or `add_global_grant()` / `remove_global_grant()`.
```

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): Phase 3 Step 8 module + endpoints + audit events"
```

---

## Task 19: Final verification + push + open PR

**Files:** none modified

- [ ] **Step 1: Final full-suite test run**

Run: `python3.13 -m pytest tests/ --ignore=tests/test_smoke.py -q --tb=no`
Expected: passed count ≥ 858 (was 843 on main); 20 failed (baseline); 73 skipped (baseline).

If counts don't match: investigate before pushing. Any new failure outside the 20 baseline means a regression.

- [ ] **Step 2: Push branch**

```bash
git push -u origin <current-branch-name>
```

- [ ] **Step 3: Open PR**

```bash
gh pr create --title "feat(phase3-step8): Plan + Permission Contract" --body "$(cat <<'EOF'
## Summary

Phase 3 Step 8 ships the planning + permission-contract layer. User drops a project → Qwen-3.6 drafts plan → user approves in PWA → grants persisted with plan_hash.

**No execution yet** — Step 9 (background runner) picks that up. Step 8 alone is shippable: drafted plans sit in `awaiting_approval`, approved plans wait in `approved`.

## Reference

- Design doc: `docs/PHASE3-BLUEPRINT.md` §2
- Implementation plan: `docs/PHASE3-STEP8-PLAN.md`
- Resolved Q&A: blueprint §8 (Q1 Qwen-3.6 only, Q2 inline edit, Q3 clarifying loop, Q4 global allowlist tier, Q13 plan-hash tamper detection)

## Files

| Path | Type | Purpose |
|---|---|---|
| `codec_agent_plan.py` | NEW | Plan/Checkpoint/PermissionManifest dataclasses + draft + validate + R/W + global allowlist |
| `routes/agents.py` | NEW | 9 PWA endpoints (CRUD + approve/reject/revise + global grants) |
| `tests/test_agent_plan.py` | NEW | 25 tests covering all behaviors |
| `codec_audit.py` | MOD | +6 audit events + PHASE3_STEP8_EVENTS frozenset |
| `codec_dashboard.py` | MOD | Mount routes/agents router |
| `AGENTS.md` | MOD | §X Phase 3 Step 8 sub-section, §6 audit events table, §10 don't-touch list |

## Audit envelope

6 new events, all schema:1, paired correlation_ids per Step 1 §1.4 contract:
- `agent_plan_drafted`
- `agent_plan_approved`
- `agent_plan_rejected`
- `agent_plan_revised`
- `agent_global_grant_added`
- `agent_global_grant_removed`

## Test plan
- [x] 🧪 RUNNING PYTEST NOW — `tests/test_agent_plan.py` → 25 passed
- [x] 🧪 Full suite — same 20/73 baseline as main, +25 new tests
- [ ] Post-merge: deploy via `pm2 restart codec-dashboard` (no skill install needed; module is in repo root)
- [ ] PWA test: drop a project via `POST /api/agents`, verify drafted plan appears, approve via `POST /api/agents/{id}/approve`, verify grants.json + plan_hash on disk

## Out of scope (deferred to Step 9)

- `codec_agent_runner.py` daemon
- Plan execution
- Per-checkpoint loop
- Resume after PM2 restart
- Permission gate enforcement at runtime

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Checklist

Run through this before claiming the plan is ready.

**Spec coverage:**

- [x] Plan dataclass + schema validation → Tasks 2, 3
- [x] Atomic R/W → Task 4
- [x] Skill-registry validation → Task 5
- [x] Plan-hash tamper detection (Q13) → Task 3, integrated in Task 11
- [x] LLM plan drafter via Qwen-3.6 only (Q1) → Task 6
- [x] Vague-description clarifying loop (Q3) → Task 7
- [x] Global allowlist (Q4) → Task 8, integrated in Task 15
- [x] State machine → Task 9
- [x] create_agent orchestrator → Task 10
- [x] approve / reject / revise → Task 11
- [x] PWA endpoints (9) → Task 12
- [x] Approval re-validation against registry → Task 16
- [x] End-to-end integration → Task 17
- [x] Audit events (6) → Tasks 1, 10, 11, 12
- [x] AGENTS.md documentation → Task 18

**Placeholder scan:** No "TBD", "TODO", "fill in" present.

**Type consistency:** `Plan`, `Checkpoint`, `PermissionManifest` defined in Task 2 and consistently referenced. `compute_plan_hash` introduced in Task 3, used in Task 11. `draft_plan` in Task 6, wrapped by `draft_plan_with_clarification` in Task 7, both used by `create_agent` in Task 10.

---

*Plan complete. Ready for execution.*
