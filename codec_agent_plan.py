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

# Audit event names — mirror codec_audit constants (single source of truth).
# Imported at module level so emit sites use the constants and any rename in
# codec_audit will fail loudly here at import time rather than silently drift.
try:
    from codec_audit import (  # noqa: E402
        AGENT_PLAN_DRAFTED,
        AGENT_PLAN_APPROVED,
        AGENT_PLAN_REJECTED,
        AGENT_PLAN_REVISED,
        AGENT_GLOBAL_GRANT_ADDED,
        AGENT_GLOBAL_GRANT_REMOVED,
    )
except ImportError:
    # codec_audit not on path during isolated test collection — fall back to
    # the canonical strings so import doesn't break.
    AGENT_PLAN_DRAFTED = "agent_plan_drafted"
    AGENT_PLAN_APPROVED = "agent_plan_approved"
    AGENT_PLAN_REJECTED = "agent_plan_rejected"
    AGENT_PLAN_REVISED = "agent_plan_revised"
    AGENT_GLOBAL_GRANT_ADDED = "agent_global_grant_added"
    AGENT_GLOBAL_GRANT_REMOVED = "agent_global_grant_removed"

# ── Storage paths (overridable for tests) ─────────────────────────────────────
_CODEC_DIR = Path(os.path.expanduser("~/.codec"))
_AGENTS_DIR = _CODEC_DIR / "agents"
_GLOBAL_GRANTS_PATH = _CODEC_DIR / "agent_global_grants.json"
# Phase 3.5: human-browseable project folder root (Claude Code-style).
# Each agent gets ~/codec-projects/<slugified-title>/ on creation, openable
# in any IDE. Override via ~/.codec/config.json:agents.project_root_dir
# or env CODEC_PROJECT_ROOT_DIR.
_PROJECT_ROOT = Path(os.path.expanduser(
    os.environ.get("CODEC_PROJECT_ROOT_DIR", "") or "~/codec-projects"
))

# ── Schema constants ──────────────────────────────────────────────────────────
PLAN_SCHEMA_VERSION = 1
GLOBAL_GRANTS_SCHEMA_VERSION = 1
DEFAULT_STEP_BUDGET_PER_CHECKPOINT = 60
MAX_CLARIFYING_ROUNDS = 3
MAX_PROJECT_SLUG_LEN = 50


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
# Hotfix: read URL+model from ~/.codec/config.json:llm_base_url+llm_model.
# Falls back to codec_config defaults. Hardcoded values were wrong (8090 is
# the dashboard port; the LLM lives at 8083).
def _qwen_url() -> str:
    try:
        from codec_config import QWEN_BASE_URL
        return f"{QWEN_BASE_URL.rstrip('/')}/chat/completions"
    except Exception:
        return "http://localhost:8083/v1/chat/completions"


def _qwen_model() -> str:
    try:
        from codec_config import QWEN_MODEL as _m
        return _m
    except Exception:
        return "mlx-community/Qwen3.6-35B-A3B-4bit"


QWEN_URL = _qwen_url()       # back-compat — module-level constant for tests
QWEN_MODEL = _qwen_model()   # back-compat
QWEN_TIMEOUT = 60  # seconds


class QwenUnavailableError(RuntimeError):
    """Qwen-3.6 service down or unreachable."""


class PlanValidationError(ValueError):
    """Plan failed schema or skill-registry validation."""


def _qwen_chat(user_prompt: str, system_prompt: str = "",
               max_tokens: int = 4000) -> str:
    """Call local Qwen-3.6 OpenAI-compatible endpoint. Returns the
    assistant's content string. Raises QwenUnavailableError on
    network failure or non-2xx response.

    URL + model resolved at call time via _qwen_url() / _qwen_model()
    so they pick up ~/.codec/config.json:llm_base_url + :llm_model
    rather than the deploy-time hardcoded values."""
    import requests  # lazy import — avoid forcing requests on test machines without it

    payload = {
        "model": _qwen_model(),
        "messages": [
            {"role": "system", "content": system_prompt or ""},
            {"role": "user",   "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    try:
        r = requests.post(_qwen_url(), json=payload, timeout=QWEN_TIMEOUT)
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
      "step_budget":     <int, default 60 — use 40 for simple single-skill checkpoints, 60 for multi-fetch or multi-file work, 80+ for checkpoints with many retries expected>
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
  Common confusions to avoid:
  • NO `file_read` skill → use `file_ops` (reads, writes, appends, lists directories)
  • NO `fetch_url` → use `web_fetch`
  • NO `read_file` → use `file_ops`
  • `file_search` is for finding a file BY NAME across the whole Mac (uses macOS Spotlight). It opens a Terminal window and returns at most 5 results. Do NOT use it to list all files in a directory — use `file_ops` for that (e.g. "list all .md files in ~/codec-repo/docs/").
  • For reading or writing files in a known directory: use `file_ops`, not `file_search`.
- step_budget MUST be at least 60 per checkpoint. The runtime will floor to 60 anyway, so values below 60 are useless. Use 60 for simple single-skill work, 80 for multi-step, 100+ for complex tasks.
- write_paths default to ~/.codec/agents/{agent_id}/artifacts/** unless the project explicitly requires writing elsewhere.
- destructive_ops list any irreversible operations (deletes, payments, sending emails on user's behalf). They will require additional consent at runtime.
- estimated_duration_minutes is your best honest guess.
"""


def draft_plan(agent_id: str, description: str, registry=None,
               available_skills: Optional[List[str]] = None,
               project_dir: Optional[Path] = None) -> Plan:
    """Call Qwen-3.6 with the project description, parse response into Plan,
    validate against skill registry. Raises PlanValidationError on schema or
    validation failure; QwenUnavailableError on LLM unavailability.

    Phase 3.5: when `project_dir` is provided, the LLM is told to default
    write_paths to that folder so files the agent creates land somewhere
    the user can open in an IDE."""
    if registry is None:
        try:
            from codec_dispatch import registry as _reg
            registry = _reg
        except Exception:
            raise PlanValidationError("codec_dispatch unavailable; cannot validate skills")

    if available_skills is None:
        available_skills = sorted(registry.names() or [])

    project_hint = ""
    if project_dir is not None:
        project_hint = (
            f"\nProject folder: {project_dir}\n"
            f"Default write_paths for this agent: \"{project_dir}/**\". "
            f"Files the user will open in their IDE land here. "
            f"Don't write outside this folder unless the project description "
            f"explicitly requires it.\n"
        )

    user_prompt = (
        f"agent_id: {agent_id}\n\n"
        f"Available skills (registry): {', '.join(available_skills)}\n"
        f"{project_hint}\n"
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

    # Detect "too_vague" sentinel from LLM
    if d.get("too_vague"):
        raise PlanValidationError("too_vague: description needs clarification")

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
    if ok:
        return plan

    # PR #41: plan-time hallucination retry. Mirror of the execution-time
    # retry shipped in PR #35 (codec_agent_runner._build_correction_nudge).
    # Real-world Qwen drift hits both layers — at execution it picks
    # `fetch_url` instead of `web_fetch`; at planning it picks `file_read`
    # instead of `file_ops`. Same cure: re-prompt ONCE with an explicit
    # closed-world correction, fail hard if the second draft still misses.
    log.info("[%s] plan referenced unknown skills %s; retrying with correction nudge",
             agent_id, missing)
    correction = (
        f"\n\nYour previous draft referenced these skills which DO NOT EXIST "
        f"in the registry: {sorted(missing)}.\n"
        f"You MUST pick from this exact list — do not invent names, do not "
        f"add suffixes (no _v2, no _read, no _write versions of unrelated "
        f"skills). The full allowed set is:\n"
        f"  {', '.join(available_skills)}\n\n"
        f"Common confusions:\n"
        f"  - Need to read a file? Use `file_ops` (it reads, writes, "
        f"appends, lists). There is NO `file_read` skill.\n"
        f"  - Need to fetch a URL? Use `web_fetch`. There is NO `fetch_url`.\n"
        f"  - Need to search files? Use `file_search`.\n\n"
        f"Re-emit the entire JSON plan with valid skill names only."
    )
    retry_prompt = user_prompt + correction
    try:
        raw2 = _qwen_chat(retry_prompt, _PLAN_SYSTEM_PROMPT)
    except (QwenUnavailableError, ConnectionError, OSError, RuntimeError) as e:
        # If the retry call itself fails, surface the ORIGINAL validation
        # error — that's more diagnostic than "qwen flaked on retry".
        raise PlanValidationError(
            f"plan references unknown skills: {missing} "
            f"(retry failed: {e})"
        )
    raw2 = raw2.strip()
    if raw2.startswith("```"):
        raw2 = re.sub(r"^```(?:json)?\s*", "", raw2)
        raw2 = re.sub(r"\s*```\s*$", "", raw2)
    try:
        d2 = json.loads(raw2)
    except json.JSONDecodeError as e:
        raise PlanValidationError(
            f"plan references unknown skills: {missing} "
            f"(retry returned non-JSON: {e})"
        )
    if d2.get("too_vague"):
        raise PlanValidationError("too_vague: description needs clarification")
    d2.setdefault("schema", PLAN_SCHEMA_VERSION)
    d2.setdefault("agent_id", agent_id)
    for cp in d2.get("checkpoints", []):
        cp.setdefault("id", _stable_checkpoint_id(cp))
    try:
        plan2 = plan_from_dict(d2)
    except (KeyError, ValueError, TypeError) as e:
        raise PlanValidationError(f"retry plan schema invalid: {e}")
    ok2, missing2 = validate_plan_skills(plan2, registry=registry)
    if not ok2:
        # Second miss — give up. Surface BOTH attempts so the user can see
        # the model is consistently confused (e.g. truly unfixable phrasing).
        raise PlanValidationError(
            f"plan references unknown skills after retry: "
            f"first={sorted(missing)}, second={sorted(missing2)}"
        )
    log.info("[%s] retry succeeded; using corrected plan", agent_id)
    return plan2


def _stable_checkpoint_id(cp_dict: Dict[str, Any]) -> str:
    """SHA-1 first 8 of (title + description). Stable across re-drafts of
    the same conceptual checkpoint."""
    seed = f"{cp_dict.get('title', '')}|{cp_dict.get('description', '')}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]


class DescriptionTooVagueError(ValueError):
    """User's project description couldn't be scoped after MAX_CLARIFYING_ROUNDS."""


def _ask_user(question: str, *, agent_id: str,
              deadline_seconds: int = 600) -> Tuple[str, Any]:
    """Lazy-loaded codec_ask_user.ask wrapper. Returns (status, answer).
    status ∈ {"answered", "ambiguous_consent", "timeout"}."""
    try:
        from codec_ask_user import ask
    except Exception as e:
        log.warning("codec_ask_user unavailable: %s", e)
        return ("timeout", None)
    return ask(question, source=f"agent_plan:{agent_id}",
               deadline_seconds=deadline_seconds)


def draft_plan_with_clarification(agent_id: str, description: str,
                                  registry=None,
                                  max_rounds: int = MAX_CLARIFYING_ROUNDS,
                                  project_dir: Optional[Path] = None) -> Plan:
    """Wrap draft_plan with a clarifying-question loop. If LLM returns
    {"too_vague": True, "clarifying_questions": [...]}, ask user via
    codec_ask_user.ask, append answers to description, retry. After
    max_rounds without convergence, raise DescriptionTooVagueError.

    Phase 3.5: `project_dir` (when provided) is forwarded to draft_plan
    so the LLM defaults write_paths to the human-browseable folder."""
    enriched_description = description

    for round_idx in range(max_rounds + 1):
        try:
            return draft_plan(agent_id, enriched_description, registry=registry,
                              project_dir=project_dir)
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


# ── Status transitions ────────────────────────────────────────────────────────
class InvalidStatusTransition(ValueError):
    """Disallowed status transition attempted."""


# Step 8 manages: draft_pending → awaiting_approval → approved/rejected/revised.
# Step 9 adds: approved → running → checkpoint_completed/blocked_*/aborted/completed.
_VALID_TRANSITIONS: Dict[str, frozenset] = {
    "draft_pending":         frozenset({"awaiting_approval", "plan_failed"}),
    "awaiting_approval":     frozenset({"approved", "rejected", "revised"}),
    "revised":               frozenset({"awaiting_approval"}),
    # `approved → aborted` (review fix C1): a plan-hash mismatch or
    # missing-hash check at run-start fires while the agent is still in
    # `approved` status (before transitioning to `running`). We must allow
    # that abort path; otherwise the tamper-detection code raises
    # InvalidStatusTransition and the bare-except handler papers over it.
    # Plan deviation from PHASE3-STEP9-PLAN.md Task 2 — intentional,
    # security-critical addition.
    "approved":              frozenset({"rejected", "running", "aborted"}),
    "rejected":              frozenset(),
    "plan_failed":           frozenset({"draft_pending"}),  # retry path

    # Step 9 runtime states
    "running":               frozenset({"completed", "aborted", "paused",
                                        "blocked_on_permission",
                                        "blocked_on_destructive",
                                        "blocked_on_qwen",
                                        "crashed_resumed"}),
    "paused":                frozenset({"running", "aborted"}),
    "blocked_on_permission": frozenset({"running", "aborted"}),
    "blocked_on_destructive": frozenset({"running", "aborted"}),
    # Phase 3.5 review fix C2: dedicated status for Qwen-3.6 unavailability.
    # Distinct from blocked_on_permission (no permission to grant — service
    # is just down). Daemon auto-resumes on next tick when Qwen comes back.
    "blocked_on_qwen":       frozenset({"running", "aborted"}),
    "crashed_resumed":       frozenset({"running", "aborted"}),
    "completed":             frozenset(),
    "aborted":               frozenset(),
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


# ── Project folder (Phase 3.5: Claude Code-style human-browseable dir) ────────
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(title: str) -> str:
    """Convert a title to a filesystem-safe slug.
    "Build a Telegram bot for Marbella property!" → "build-a-telegram-bot-for-marbella-property".
    Falls back to "project" if the title has no alphanumeric characters."""
    s = _SLUG_RE.sub("-", (title or "").lower()).strip("-")
    if not s:
        s = "project"
    return s[:MAX_PROJECT_SLUG_LEN].rstrip("-") or "project"


def _project_root() -> Path:
    """Return the configured project root, with config.json override.
    Resolved at call time so tests can monkeypatch _PROJECT_ROOT or set
    CODEC_PROJECT_ROOT_DIR via monkeypatch.setenv."""
    # config.json override (~/.codec/config.json:agents.project_root_dir)
    cfg = _CODEC_DIR / "config.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text())
            override = (data.get("agents") or {}).get("project_root_dir")
            if override:
                return Path(os.path.expanduser(override))
        except Exception:
            pass
    return _PROJECT_ROOT


def create_project_folder(title: str, agent_id: str) -> Path:
    """Create a human-browseable project folder for this agent.
    Returns absolute Path. Disambiguates if the slug already exists."""
    root = _project_root()
    root.mkdir(parents=True, exist_ok=True)
    base_slug = _slugify(title)
    candidate = root / base_slug
    suffix = 2
    while candidate.exists():
        candidate = root / f"{base_slug}-{suffix}"
        suffix += 1
        if suffix > 99:
            # Pathological case: 99 collisions → fall back to agent_id suffix
            candidate = root / f"{base_slug}-{agent_id}"
            break
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate.resolve()


# ── Public orchestrator ───────────────────────────────────────────────────────
def _new_agent_id() -> str:
    return f"agent_{secrets.token_hex(6)}"


def create_agent(title: str, description: str,
                 registry=None,
                 notification_channels: Optional[List[str]] = None) -> str:
    """Top-level entry point. Drafts a plan, persists to disk, emits audit.
    Returns the new agent_id. Status after this call: awaiting_approval
    (or plan_failed on validation error).

    Phase 3.5: ALSO creates a human-browseable project folder under
    ~/codec-projects/<slug>/ (or the configured project_root_dir). The
    plan's permission_manifest.write_paths defaults to this folder, so
    files the agent creates land where the user can open them in an IDE.
    """
    agent_id = _new_agent_id()
    cid = secrets.token_hex(6)

    # Phase 3.5: create the human-browseable project folder up-front so
    # the plan-drafter can reference it as the default write_paths root.
    try:
        project_dir = create_project_folder(title, agent_id)
    except Exception as e:
        log.warning("[%s] project folder creation failed: %s", agent_id, e)
        project_dir = None

    # Initial manifest
    manifest = {
        "agent_id": agent_id,
        "title": title[:120],
        "status": "draft_pending",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "notification_channels": notification_channels or ["pwa"],
        "project_dir": str(project_dir) if project_dir else None,
    }
    save_manifest(agent_id, manifest)
    save_state(agent_id, {"current_checkpoint": 0})

    # Draft plan (with clarification loop)
    try:
        plan = draft_plan_with_clarification(agent_id, description, registry=registry,
                                              project_dir=project_dir)
    except DescriptionTooVagueError as e:
        set_status(agent_id, "plan_failed", reason=f"too_vague: {e}")
        _audit(AGENT_PLAN_REJECTED, "codec-agent-plan",
               f"plan failed (vague): {e}", correlation_id=cid,
               outcome="warning", level="warning",
               extra={"agent_id": agent_id, "reason": "too_vague"})
        raise
    except (PlanValidationError, QwenUnavailableError) as e:
        set_status(agent_id, "plan_failed", reason=str(e))
        _audit(AGENT_PLAN_REJECTED, "codec-agent-plan",
               f"plan failed: {e}", correlation_id=cid,
               outcome="error", level="error",
               extra={"agent_id": agent_id, "reason": str(e)[:200]})
        raise

    # Persist plan + transition status
    save_plan(plan)
    set_status(agent_id, "awaiting_approval")

    _audit(AGENT_PLAN_DRAFTED, "codec-agent-plan",
           f"plan drafted for {title[:60]}", correlation_id=cid,
           extra={
               "agent_id": agent_id,
               "checkpoint_count": len(plan.checkpoints),
               "estimated_duration_minutes": plan.estimated_duration_minutes,
               "skills_count": len(plan.permission_manifest.skills),
               "domains_count": len(plan.permission_manifest.network_domains),
           })

    return agent_id


def approve_plan(agent_id: str) -> Dict[str, Any]:
    """Transition awaiting_approval → approved. Computes plan_hash for
    Step 9 tamper detection. Writes grants.json (= full manifest by
    default — Step 8 doesn't yet support partial grants; approve == all)."""
    plan = load_plan(agent_id)
    if plan is None:
        raise ValueError(f"no plan found for {agent_id!r}")

    # Re-validate skills against registry (skills may have been deleted between draft & approval)
    ok, missing = validate_plan_skills(plan)
    if not ok:
        raise PlanValidationError(
            f"plan references skills no longer in registry: {missing}"
        )

    plan_hash = compute_plan_hash(plan)

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
    save_grants(agent_id, grants)

    # Update manifest with hash + transition status
    manifest = load_manifest(agent_id)
    manifest["plan_hash"] = plan_hash
    manifest["approved_at"] = _now_iso()
    save_manifest(agent_id, manifest)
    set_status(agent_id, "approved")

    cid = secrets.token_hex(6)
    _audit(AGENT_PLAN_APPROVED, "codec-agent-plan",
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
    _audit(AGENT_PLAN_REJECTED, "codec-agent-plan",
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
    _audit(AGENT_PLAN_REVISED, "codec-agent-plan",
           f"plan revised for {agent_id}", correlation_id=cid,
           extra={
               "agent_id": agent_id,
               "checkpoint_count": len(plan.checkpoints),
           })

    return plan
