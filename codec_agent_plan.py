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


def compute_plan_hash(plan: Plan) -> str:
    """SHA-256 of canonical JSON serialization. Stored in manifest at
    approval time; daemon (Step 9) verifies on every tick. Mismatch
    means someone manually edited plan.json after approval."""
    canonical = json.dumps(plan.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


# ── Skill-registry validation ─────────────────────────────────────────────────
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

    try:
        raw = _qwen_chat(user_prompt, _PLAN_SYSTEM_PROMPT)
    except QwenUnavailableError:
        raise
    except (ConnectionError, OSError, RuntimeError) as e:
        raise QwenUnavailableError(f"qwen3.6 error: {e}")

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
