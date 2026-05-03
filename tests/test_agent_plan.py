"""Phase 3 Step 8 tests — codec_agent_plan + routes/agents.py.

25 tests covering: audit constants, dataclasses, atomic R/W, validation,
plan-hash, LLM drafter, clarifying loop, global allowlist, state machine,
PWA endpoints, and end-to-end integration.
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


# ─────────────────────────────────────────────────────────────────────────────
# Task 2 — Plan/Checkpoint/PermissionManifest dataclasses (3 tests)
# ─────────────────────────────────────────────────────────────────────────────

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


def test_plan_from_dict_rejects_bad_schema():
    from codec_agent_plan import plan_from_dict
    with pytest.raises(ValueError, match="unsupported plan schema"):
        plan_from_dict({"schema": 99, "agent_id": "x"})


# ─────────────────────────────────────────────────────────────────────────────
# Task 3 — Plan-hash for tamper detection (Q13) (2 tests)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Task 4 — Atomic R/W (2 tests)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Task 5 — Skill-registry validation (2 tests)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Task 6 — LLM plan drafter (Qwen-3.6, local-only) (3 tests)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Task 7 — Vague-description clarifying loop (Q3) (2 tests)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Task 8 — Global allowlist (Q4) (3 tests)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Task 9 — State machine (2 tests)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Task 10 — create_agent orchestrator (1 test)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Task 11 — approve / reject / revise (2 tests)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Task 12 — PWA endpoints (2 tests)
# ─────────────────────────────────────────────────────────────────────────────

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
