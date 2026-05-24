"""CODEC Phase 3 Step 9 — Background Execution + Permission Gate.

PM2-managed daemon `codec-agent-runner` that picks up status=approved
plans (from Step 8), executes their checkpoints autonomously via
Qwen-3.6 ↔ skill loops, enforces the permission manifest, persists
state for resume-after-restart.

Reuses:
  - codec_audit (Step 1) for paired-cid envelope
  - codec_dispatch.run_skill (Step 2 plugin hooks fire automatically)
  - codec_ask_user (Step 3) for outside-manifest grant prompts
  - codec_ask_user.strict_consent (Step 3 §1.7) for destructive ops
  - codec_dashboard._StepBudget (Step 3) for per-checkpoint cap
  - codec_agent_plan (Step 8) for plan/state/manifest/grants R/W

See docs/PHASE3-BLUEPRINT.md §3 for design rationale.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("codec_agent_runner")

# ── Audit event constants (mirror codec_audit) ────────────────────────────────
try:
    from codec_audit import (
        AGENT_STARTED, AGENT_CHECKPOINT_STARTED, AGENT_CHECKPOINT_COMPLETED,
        AGENT_PAUSED, AGENT_RESUMED, AGENT_BLOCKED_ON_PERMISSION,
        AGENT_COMPLETED, AGENT_ABORTED,
    )
except ImportError:
    AGENT_STARTED = "agent_started"
    AGENT_CHECKPOINT_STARTED = "agent_checkpoint_started"
    AGENT_CHECKPOINT_COMPLETED = "agent_checkpoint_completed"
    AGENT_PAUSED = "agent_paused"
    AGENT_RESUMED = "agent_resumed"
    AGENT_BLOCKED_ON_PERMISSION = "agent_blocked_on_permission"
    AGENT_COMPLETED = "agent_completed"
    AGENT_ABORTED = "agent_aborted"


# ── Configurable knobs (overridable for tests) ────────────────────────────────
DAEMON_TICK_SECONDS = 5
DEFAULT_MAX_CONCURRENT = 3
DESTRUCTIVE_CONSENT_TIMEOUT_S = 600  # Step 3 §1.7 default — overnight = block, not abort
# Default per-checkpoint step budget. Kept in sync with codec_agent_plan.py.
# Real tasks (multi-fetch, multi-file) routinely need 30-50 steps; 60 gives
# comfortable headroom without being unlimited.
DEFAULT_STEP_BUDGET_PER_CHECKPOINT = 60


# ── Action dataclass ──────────────────────────────────────────────────────────
@dataclass
class Action:
    """One proposed step in a checkpoint loop. Returned by
    Qwen-3.6's next-action driver, evaluated by permission_gate,
    executed via codec_dispatch.run_skill.

    Phase 3.5 review M4: `reads_path` + `read_path` added for symmetric
    read/write gating. `touches_path`/`path` is the write side."""
    skill: str
    task: str
    is_destructive: bool = False
    network_call: bool = False
    network_domain: str = ""
    touches_path: bool = False
    path: str = ""
    reads_path: bool = False        # Phase 3.5 review M4: read enforcement
    read_path: str = ""             # Phase 3.5 review M4
    kind: str = "skill_call"   # "skill_call" | "checkpoint_done"


# ── PermissionViolation ───────────────────────────────────────────────────────
class PermissionViolation(Exception):
    """An Action references something outside the union of per-agent
    grants + global allowlist. Caught by _run_agent and translated
    to status=blocked_on_permission + ask_user notification."""

    def __init__(self, reason: str, needed: str, message: str = ""):
        self.reason = reason
        self.needed = needed
        super().__init__(message or f"{reason}: {needed}")


# ── Permission gate ───────────────────────────────────────────────────────────

def _emit_gate_blocked(action_path: str, reason: str, agent_id: str = "") -> None:
    """Emit a `permission_gate_blocked` audit event on rejection. Forensic
    visibility per audit D-5 closure — operators can grep ~/.codec/audit.log
    for blocked-action attempts. Never raises (audit failure must not mask
    the underlying refusal)."""
    try:
        from codec_audit import log_event
        try:
            real = os.path.realpath(os.path.expanduser(action_path)) if action_path else ""
        except Exception:
            real = action_path or ""
        log_event(
            "permission_gate_blocked",
            source="codec-agent-runner",
            message=f"permission_gate refused {action_path!r}: {reason}",
            level="warning",
            outcome="error",
            extra={
                "requested_path": action_path,
                "resolved_path": real,
                "reason": reason,
                "agent_id": agent_id,
            },
        )
    except Exception:
        pass


def _path_allowed(action_path: str, grants: Any) -> tuple[bool, str]:
    """Return (allowed, reason) for an action path against a set of grant
    patterns (originally fnmatch-style, e.g. `~/Documents/**`).

    Closes audit D-5 — three layered checks:
      1. Reject `..` segments outright (no path-traversal bypass).
      2. Realpath the action so symlinks are resolved.
      3. Match against realpath'd grant roots (the substring before the
         first glob char). Acceptance = action's realpath is at or under
         the grant's realpath root.

    Trade-off vs. raw fnmatch: a grant pattern like `~/Documents/*.md`
    now accepts any file under realpath(~/Documents/), not just `*.md`.
    The audit explicitly recommends this (prefix-on-realpath over
    fnmatch) — safety > granularity.
    """
    if not action_path:
        return False, "empty_path"

    # Reject .. anywhere in the path. expanduser is enough here — we don't
    # need realpath to detect the segment "..".
    if ".." in Path(os.path.expanduser(action_path)).parts:
        return False, "path_traversal"

    try:
        action_real = os.path.realpath(os.path.expanduser(action_path))
    except (OSError, RuntimeError, ValueError):
        return False, "realpath_failed"

    for grant in grants:
        if not grant:
            continue
        grant_expanded = os.path.expanduser(grant)
        # Strip glob suffix to find the directory root. Examples:
        #   "~/Documents/**"   → "~/Documents"
        #   "~/Documents/*.md" → "~/Documents"
        #   "~/Documents"      → "~/Documents"
        glob_idx = grant_expanded.find("*")
        grant_root = (grant_expanded[:glob_idx] if glob_idx >= 0
                      else grant_expanded).rstrip(os.sep) or os.sep
        try:
            grant_real = os.path.realpath(grant_root)
        except (OSError, RuntimeError, ValueError):
            continue
        if action_real == grant_real or action_real.startswith(grant_real + os.sep):
            return True, ""

    return False, "not_under_grant"


def permission_gate(action: Action, agent_grants: Dict[str, Any],
                    global_grants: Dict[str, Any]) -> None:
    """The core Step 9 enforcement. Walks the action's resource use,
    checks the union of per-agent grants and global allowlist. Raises
    PermissionViolation on any gap.

    Path checks use `_path_allowed` (realpath + dotdot rejection) — closes
    audit finding D-5. Rejections emit a `permission_gate_blocked` audit
    event before the exception so the operator gets forensic visibility.

    Note: destructive ops fall through to strict_consent_gate (Step 3
    §1.7) — even if pre-approved by the user. That's the universal
    floor; permission_gate alone is not enough.
    """
    skills = set(agent_grants.get("skills", [])) | set(global_grants.get("skills", []))
    if action.skill not in skills:
        raise PermissionViolation("skill_not_authorized", action.skill)

    # Phase 3.5 review M4: symmetric read/write gating now active.
    # `touches_path` = write; `reads_path` = read. Both checked against
    # respective manifest entries. Note: skill-internal reads (where the
    # skill itself opens files without going through Action) still bypass
    # the runner — that's a fundamental limitation of the dispatch model.
    if action.touches_path:
        write_paths = (set(agent_grants.get("write_paths", [])) |
                       set(global_grants.get("write_paths", [])))
        ok, reason = _path_allowed(action.path, write_paths)
        if not ok:
            _emit_gate_blocked(action.path, reason)
            raise PermissionViolation("path_not_authorized", action.path)

    if action.reads_path and action.read_path:
        read_paths = (set(agent_grants.get("read_paths", [])) |
                      set(global_grants.get("read_paths", [])))
        # Write paths are implicitly readable — an agent that can write a file
        # must be able to read it back (verify writes, read prior output, etc.).
        write_paths_implicit = (set(agent_grants.get("write_paths", [])) |
                                set(global_grants.get("write_paths", [])))
        ok, reason = _path_allowed(action.read_path, read_paths | write_paths_implicit)
        if not ok:
            _emit_gate_blocked(action.read_path, reason)
            raise PermissionViolation("read_path_not_authorized", action.read_path)

    if action.network_call:
        domains = (set(agent_grants.get("network_domains", [])) |
                   set(global_grants.get("network_domains", [])))
        if action.network_domain not in domains:
            raise PermissionViolation("domain_not_authorized", action.network_domain)


# ── Qwen-3.6 client (mirrors codec_agent_plan pattern) ────────────────────────
# Hotfix: read URL+model from ~/.codec/config.json via codec_config (8090
# was the dashboard port; LLM lives at 8083).
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


def _qwen_base() -> str:
    """Base URL (no /chat/completions) for codec_llm.call — call-time resolved."""
    try:
        from codec_config import QWEN_BASE_URL
        return QWEN_BASE_URL
    except Exception:
        return "http://localhost:8083/v1"


QWEN_URL = _qwen_url()
QWEN_MODEL = _qwen_model()
QWEN_TIMEOUT = 60


class QwenUnavailableError(RuntimeError):
    """Qwen-3.6 service down or unreachable."""


_NEXT_ACTION_SYSTEM_PROMPT = """You are CODEC's autonomous agent runtime. \
Given a plan, current checkpoint, and recent action history, decide the SINGLE \
next action to take. Return ONLY a JSON object with one of these shapes:

For a skill call:
{
  "kind": "skill_call",
  "skill": "<skill_name — MUST be from the available_skills list in the prompt>",
  "task": "<the natural-language task to pass to that skill>",
  "is_destructive": <bool — true for irreversible ops: file delete, payments, send-on-behalf>,
  "network_call": <bool — true if the skill will make HTTP requests>,
  "network_domain": "<domain if network_call=true, else empty>",
  "touches_path": <bool — true if the skill WRITES to a filesystem path>,
  "path": "<path if touches_path=true, else empty>",
  "reads_path": <bool — true if the skill READS a filesystem path>,
  "read_path": "<path if reads_path=true, else empty>"
}

For checkpoint completion:
{"kind": "checkpoint_done"}

Rules:
- skill MUST come from the available_skills list shown in the prompt. Never invent skill names.
- Return {"kind": "checkpoint_done"} AS SOON AS the checkpoint's expected_output is satisfied.
  Do NOT call more skills after the goal is achieved — stop immediately with checkpoint_done.
- If steps_remaining is 3 or fewer and the checkpoint is not yet done: call the single most
  important remaining skill, then return checkpoint_done on the VERY NEXT step regardless.
- If you have already called a skill and received a result that satisfies expected_output,
  return checkpoint_done now — do not repeat the skill call.
- read_path is checked against permission_manifest.read_paths; write path against write_paths.
- CRITICAL — file_search vs file_ops:
    • file_search uses macOS Spotlight (mdfind). It opens a Terminal window, searches by
      FILE NAME across the whole Mac, and returns AT MOST 5 results. It cannot list all
      files in a directory. NEVER use file_search to enumerate files in a folder.
    • file_ops is the correct skill for: listing files in a directory, reading file contents,
      and writing files. Use "list files in ~/path/to/dir" to enumerate a directory.
- CRITICAL — one file per step:
    • When you need to process multiple files (read, parse, extract), do ONE file per step.
    • Never put multiple file paths in a single "task" string — file_ops only handles one
      path at a time. If you have 30 files to read, make 30 sequential skill calls.
    • The "task" string must be short and specific: "Read file '/path/to/one/file.md'"
- CRITICAL — writing multi-line file content:
    • When writing markdown, tables, or any structured text with file_ops, you MUST include
      actual newlines in the content. Use \\n inside the JSON string to produce a newline.
    • WRONG:  "task": "write file '/p' content: # Title Row1 Row2 Row3"
    • CORRECT: "task": "write file '/p' content: # Title\\n\\nRow1\\nRow2\\nRow3"
    • A markdown table MUST have each row on its own line: | col | col |\\n| --- | --- |\\n| val |
- Output ONLY the JSON. No prose.
"""


def _qwen_chat(user_prompt: str, system_prompt: str = "",
               max_tokens: int = 2000) -> str:
    """Local Qwen-3.6 OpenAI-compatible call. Same shape as
    codec_agent_plan._qwen_chat — keep them parallel.

    URL + model resolved at call time so config.json changes are picked
    up without a process restart."""
    # A-12 (PR-3E-2c): canonical codec_llm.call(raise_on_error=True). Adapter
    # maps codec_llm.LLMError -> the public QwenUnavailableError so the daemon's
    # retry/abort logic (except QwenUnavailableError) is unchanged. Kept parallel
    # with codec_agent_plan._qwen_chat.
    import codec_llm
    try:
        return codec_llm.call(
            [
                {"role": "system", "content": system_prompt or ""},
                {"role": "user",   "content": user_prompt},
            ],
            base_url=_qwen_base(), model=_qwen_model(),
            max_tokens=max_tokens, temperature=0.2,
            timeout=QWEN_TIMEOUT, raise_on_error=True,
        )
    except codec_llm.LLMError as e:
        raise QwenUnavailableError(f"qwen3.6 unavailable: {e}") from e


def _qwen_next_action(plan_dict: Dict[str, Any], checkpoint: Dict[str, Any],
                     history: List[Dict[str, Any]],
                     max_history: int = 10) -> Action:
    """Compose the next-action prompt, call Qwen, parse the JSON
    response into an Action. Raises QwenUnavailableError or
    ValueError on bad JSON shape."""
    recent = history[-max_history:] if history else []
    # Floor to DEFAULT so plans with tiny LLM-generated budgets (e.g. 5 or 10)
    # don't exhaust before Qwen can finish real work.
    budget = max(int(checkpoint.get("step_budget", DEFAULT_STEP_BUDGET_PER_CHECKPOINT)),
                 DEFAULT_STEP_BUDGET_PER_CHECKPOINT)
    steps_used = len(history)
    steps_remaining = max(0, budget - steps_used)

    # Available skills from permission_manifest (what the agent is allowed to use)
    available_skills = (plan_dict.get("permission_manifest") or {}).get("skills", [])
    if not available_skills:
        # Fallback: union of all checkpoint skills_needed
        for cp in plan_dict.get("checkpoints", []):
            available_skills.extend(cp.get("skills_needed", []))
        available_skills = sorted(set(available_skills))

    # Trim history results to avoid bloating the Qwen prompt.
    # Each history entry's "result" is capped at 600 chars — enough to
    # see the key outcome (file paths, headings, status) without flooding
    # the context window and causing response truncation.
    def _trim_history(h_list):
        out = []
        for entry in h_list:
            e = dict(entry)
            if isinstance(e.get("result"), str) and len(e["result"]) > 600:
                e["result"] = e["result"][:600] + "…[truncated]"
            out.append(e)
        return out

    recent_trimmed = _trim_history(recent)

    # ── File-iteration state injection ────────────────────────────────────────
    # When history contains a file-list result (file_ops list call), extract all
    # paths and figure out which have already been read in this checkpoint. Inject
    # an explicit "next file to process" hint so Qwen doesn't need to track
    # iteration state itself — dramatically reduces wasted steps.
    import re as _re

    def _extract_file_list(h_list) -> list:
        """Scan history for a file_ops list result and return the path list."""
        for entry in h_list:
            result = entry.get("result", "")
            if isinstance(result, str) and "Files (" in result:
                # file_ops list output format: "Files (N):\n/path1\n/path2\n..."
                paths = _re.findall(r"(/[\w./_-]+\.[\w]+)", result)
                if paths:
                    return paths
        return []

    def _already_read(h_list) -> set:
        """Return set of absolute paths whose file content is in history."""
        seen = set()
        for entry in h_list:
            result = entry.get("result", "")
            if isinstance(result, str):
                # file_ops read output: "File: /path ..."
                m = _re.match(r"File: (/[^\s(]+)", result)
                if m:
                    seen.add(m.group(1))
        return seen

    file_list = _extract_file_list(history)  # full history, not just recent
    file_iteration_hint = ""
    if file_list:
        already_done = _already_read(history)
        remaining = [p for p in file_list if p not in already_done]
        if remaining:
            next_file = remaining[0]
            file_iteration_hint = (
                f"\nFile iteration state:\n"
                f"  Total files to process: {len(file_list)}\n"
                f"  Already processed: {len(already_done)} files\n"
                f"  Remaining: {len(remaining)} files\n"
                f"  NEXT FILE TO READ NOW: {next_file}\n"
                f"  (Process exactly this one file in your next skill call. "
                f"Do NOT pass multiple paths.)\n"
            )
        else:
            file_iteration_hint = (
                f"\nFile iteration state: ALL {len(file_list)} files have been "
                f"read. Check if expected_output is satisfied; if yes return "
                f"checkpoint_done.\n"
            )

    user_prompt = (
        f"Plan goals: {plan_dict.get('goals')}\n\n"
        f"Available skills (use ONLY these): {available_skills}\n\n"
        f"Current checkpoint:\n"
        f"  title: {checkpoint['title']}\n"
        f"  description: {checkpoint['description']}\n"
        f"  expected_output: {checkpoint['expected_output']}\n"
        f"{file_iteration_hint}\n"
        f"Steps used: {steps_used} / {budget}  (steps_remaining: {steps_remaining})\n\n"
        f"Recent action history (last {len(recent_trimmed)} steps):\n"
        f"{json.dumps(recent_trimmed, indent=2)}\n\n"
        f"What's the next action? If expected_output is already satisfied by the history above, "
        f"return {{\"kind\": \"checkpoint_done\"}} now. Otherwise output the next skill call JSON."
    )

    def _parse_action_json(text: str):
        """Try to extract a valid JSON object from Qwen output.

        Handles:
        - Bare JSON
        - ```json ... ``` fences
        - Truncated output — extract first complete {...} block
        """
        text = text.strip()
        # Strip code fences
        if text.startswith("```"):
            text = _re.sub(r"^```(?:json)?\s*", "", text)
            text = _re.sub(r"\s*```\s*$", "", text)
            text = text.strip()
        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Try to extract the first balanced {...} block
        start = text.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i+1])
                        except json.JSONDecodeError:
                            break
        return None

    raw = _qwen_chat(user_prompt, _NEXT_ACTION_SYSTEM_PROMPT, max_tokens=4000).strip()
    d = _parse_action_json(raw)
    if d is None:
        # One retry with a shorter, sharper prompt
        log.warning("_qwen_next_action: parse failed, retrying. raw=%r", raw[:120])
        retry_prompt = (
            "Output ONLY a single JSON object. No prose, no fences.\n\n"
            f"Plan goals: {plan_dict.get('goals')}\n"
            f"Checkpoint: {checkpoint['title']} — {checkpoint['description']}\n"
            f"Expected output: {checkpoint['expected_output']}\n"
            f"Steps used: {steps_used}/{budget}\n"
            f"Last result: {recent[-1]['result'][:300] if recent else 'none'}\n\n"
            "Return {\"kind\": \"checkpoint_done\"} if expected_output is satisfied, "
            "else the next skill call JSON."
        )
        raw2 = _qwen_chat(retry_prompt, _NEXT_ACTION_SYSTEM_PROMPT).strip()
        d = _parse_action_json(raw2)
    if d is None:
        raise ValueError(f"qwen returned non-JSON next-action: raw={raw[:200]!r}")

    kind = d.get("kind", "skill_call")
    if kind == "checkpoint_done":
        return Action(skill="", task="", kind="checkpoint_done")

    return Action(
        skill=str(d.get("skill", "")),
        task=str(d.get("task", "")),
        is_destructive=bool(d.get("is_destructive", False)),
        network_call=bool(d.get("network_call", False)),
        network_domain=str(d.get("network_domain", "")),
        touches_path=bool(d.get("touches_path", False)),
        path=str(d.get("path", "")),
        reads_path=bool(d.get("reads_path", False)),    # Phase 3.5 review M4
        read_path=str(d.get("read_path", "")),          # Phase 3.5 review M4
        kind="skill_call",
    )


@dataclass
class ConsentResult:
    """Outcome of strict-consent gate for a destructive op."""
    approved: bool = False
    timed_out: bool = False
    user_response: str = ""


def _strict_consent(action: Action, deadline: int = DESTRUCTIVE_CONSENT_TIMEOUT_S) -> ConsentResult:
    """Lazy-imported codec_ask_user.strict_consent_gate wrapper.
    Returns ConsentResult. Reuses Phase 1 Step 3 §1.7 verb-match
    enforcement — generic 'yes' is rejected, two-strike timeout
    in <2s emits ambiguous_consent."""
    try:
        from codec_ask_user import strict_consent_gate
    except Exception as e:
        log.warning("codec_ask_user.strict_consent_gate unavailable: %s", e)
        return ConsentResult(approved=False, timed_out=True,
                              user_response="ask_user_unavailable")

    question = (
        f"⚠️ Destructive op requested by agent: {action.skill}: {action.task[:80]}\n\n"
        f"To approve, type the literal verb: 'delete' / 'send' / 'pay' / 'authorize' "
        f"(matching the operation). Generic 'yes' will be rejected."
    )
    result = strict_consent_gate(question, deadline_seconds=deadline,
                                  source=f"agent_runner")
    return ConsentResult(
        approved=getattr(result, "approved", False),
        timed_out=getattr(result, "timed_out", False),
        user_response=getattr(result, "user_response", ""),
    )


def _enforce_destructive_gate(action: Action,
                              deadline: int = DESTRUCTIVE_CONSENT_TIMEOUT_S) -> ConsentResult:
    """Called by checkpoint executor for any action where
    is_destructive=True. Returns ConsentResult; caller decides
    aborted vs blocked based on `timed_out` flag (Q7)."""
    if not action.is_destructive:
        return ConsentResult(approved=True, timed_out=False)
    return _strict_consent(action, deadline)


class DestructiveOpRejected(Exception):
    """User explicitly rejected a destructive op via strict-consent."""


class StepBudgetExhausted(Exception):
    """Per-checkpoint step budget cap reached without checkpoint_done."""


def _build_correction_nudge(pv: "PermissionViolation",
                            action: Action,
                            agent_grants: Dict[str, Any],
                            global_grants: Dict[str, Any]) -> Optional[str]:
    """PR #35: build a single-shot correction string for the LLM when
    it picks something outside the allowlist. Returns None for unknown
    reasons (caller falls back to raise).

    The string is appended to history.result so the next
    _qwen_next_action call sees it as the most-recent step output, and
    the model corrects itself instead of looping. We list the FULL
    allowed set so the model has a closed-world choice — listing
    nothing was the original PR #34 bug for skills; same logic applies
    to paths and domains."""
    reason = pv.reason
    if reason == "skill_not_authorized":
        allowed = sorted(set(agent_grants.get("skills", [])) |
                         set(global_grants.get("skills", [])))
        return (f"<skill_error: '{action.skill}' is NOT in this agent's "
                f"permission_manifest.skills. Allowed skills: "
                f"{', '.join(allowed) or '(none)'}. Pick one of those "
                f"instead.>")
    if reason == "path_not_authorized":
        allowed = sorted(set(agent_grants.get("write_paths", [])) |
                         set(global_grants.get("write_paths", [])))
        return (f"<path_error: write to '{action.path}' is NOT under "
                f"permission_manifest.write_paths. Allowed write_paths "
                f"(glob patterns): {', '.join(allowed) or '(none)'}. "
                f"Pick a path that matches one of those globs.>")
    if reason == "read_path_not_authorized":
        allowed = sorted(set(agent_grants.get("read_paths", [])) |
                         set(global_grants.get("read_paths", [])))
        return (f"<read_path_error: read of '{action.read_path}' is NOT "
                f"under permission_manifest.read_paths. Allowed read_paths "
                f"(glob patterns): {', '.join(allowed) or '(none)'}. "
                f"Pick a read path that matches one of those globs.>")
    if reason == "domain_not_authorized":
        allowed = sorted(set(agent_grants.get("network_domains", [])) |
                         set(global_grants.get("network_domains", [])))
        return (f"<domain_error: '{action.network_domain}' is NOT in "
                f"permission_manifest.network_domains. Allowed domains: "
                f"{', '.join(allowed) or '(none)'}. Use one of those "
                f"exact domains (no schema, no path).>")
    return None


def _run_skill(skill_name: str, task: str, agent_id: str) -> str:
    """Lazy-imported codec_dispatch.run_skill. Step 1+2 hooks fire
    automatically inside run_skill via run_with_hooks."""
    try:
        from codec_dispatch import run_skill, registry, load_skills
    except Exception as e:
        raise RuntimeError(f"codec_dispatch unavailable: {e}")
    # Defensive scan: if the registry is empty (e.g. daemon just restarted and
    # hasn't hit run_daemon's startup scan yet), scan now so skills resolve.
    if not registry.names():
        log.info("Skill registry empty in _run_skill — scanning now")
        try:
            load_skills()
        except Exception as e:
            log.warning("Defensive skill registry scan failed: %s", e)
    meta = (registry.get_meta(skill_name) if registry else None) or {}
    skill = {"name": skill_name, "_all_matches": [skill_name], **meta}
    return run_skill(skill, task, app=f"agent:{agent_id}")


def _execute_checkpoint(plan_dict: Dict[str, Any],
                        checkpoint: Dict[str, Any],
                        agent_grants: Dict[str, Any],
                        global_grants: Dict[str, Any],
                        agent_id: str,
                        history: Optional[List[Dict[str, Any]]] = None
                        ) -> List[Dict[str, Any]]:
    """Inner loop: ask Qwen for next action, gate it, execute, append
    to history, repeat until checkpoint_done OR step_budget hit OR
    PermissionViolation OR DestructiveOpRejected raised.

    Returns the final history list. Caller (run_agent) is responsible
    for atomic state save + audit emit on each checkpoint completion.

    Raises:
        PermissionViolation — escalate to status=blocked_on_permission
        DestructiveOpRejected — abort the agent
        StepBudgetExhausted — escalate to status=blocked_on_budget
        QwenUnavailableError — daemon retries
    """
    if history is None:
        history = []
    # Floor to DEFAULT so plans with tiny LLM-generated budgets (e.g. 5 or 10)
    # don't exhaust before Qwen can finish real work.
    budget = max(int(checkpoint.get("step_budget", DEFAULT_STEP_BUDGET_PER_CHECKPOINT)),
                 DEFAULT_STEP_BUDGET_PER_CHECKPOINT)

    for step in range(budget):
        action = _qwen_next_action(plan_dict, checkpoint, history)

        if action.kind == "checkpoint_done":
            return history

        # Permission gate (raises PermissionViolation if outside manifest).
        # Phase 3.5 hotfix (PR #34 + #35): if the LLM hallucinates a skill
        # name, write path, read path, or network domain, give it ONE retry
        # with the corrected allowlist as context. Most hallucinations
        # recover with a single correction pass; only block on the SECOND
        # consecutive miss. This dramatically reduces user-visible
        # blocked_on_permission events caused by LLM naming drift.
        try:
            permission_gate(action, agent_grants, global_grants)
        except PermissionViolation as pv:
            nudge = _build_correction_nudge(pv, action, agent_grants, global_grants)
            if nudge is None:
                raise   # unknown reason — fall through unchanged
            history.append({
                "step": len(history),
                "skill": action.skill,
                "task": action.task[:200],
                "result": nudge,
                "is_destructive": False,
                "_skill_correction_nudge": True,
            })
            # Re-call Qwen — if it still picks something invalid, fall through
            # and the SECOND permission_gate call will raise normally.
            action2 = _qwen_next_action(plan_dict, checkpoint, history)
            if action2.kind == "checkpoint_done":
                return history
            permission_gate(action2, agent_grants, global_grants)
            action = action2  # use the corrected action going forward

        # Destructive gate (raises DestructiveOpRejected on user reject)
        if action.is_destructive:
            consent = _enforce_destructive_gate(action)
            if consent.timed_out:
                # Q7: timeout overnight — caller transitions to blocked_on_destructive
                raise StepBudgetExhausted(
                    "destructive_consent_timeout"  # special marker
                )
            if not consent.approved:
                raise DestructiveOpRejected(
                    f"user rejected: {action.skill} {action.task[:80]}"
                )

        # Execute via codec_dispatch.run_skill (Step 1+2 hooks fire)
        try:
            result = _run_skill(action.skill, action.task, agent_id)
        except Exception as e:
            log.warning("[%s] skill %s raised: %s", agent_id, action.skill, e)
            result = f"<skill_error: {e}>"

        history.append({
            "step": len(history),
            "skill": action.skill,
            "task": action.task[:200],
            "result": (result or "")[:500],
            "is_destructive": action.is_destructive,
        })

    raise StepBudgetExhausted(f"step_budget {budget} exhausted in checkpoint {checkpoint.get('id')}")


def _audit(event: str, source: str = "codec-agent-runner",
           message: str = "", correlation_id: str = "",
           outcome: str = "ok", level: str = "info",
           extra: Optional[Dict[str, Any]] = None) -> None:
    """Lazy-imported audit emit. Centralized for monkeypatching in tests."""
    try:
        from codec_audit import audit
    except Exception as e:
        log.debug("codec_audit unavailable for %s: %s", event, e)
        return
    audit(event=event, source=source, message=message,
          correlation_id=correlation_id, outcome=outcome,
          level=level, extra=dict(extra or {}))


def _atomic_set_status(agent_id: str, new_status: str,
                       reason: Optional[str] = None) -> bool:
    """Apply a manifest status transition. Returns True if it was applied,
    False if it was NOT — an illegal/externally-superseded transition
    (`InvalidStatusTransition`) or a write failure. Never raises (C-5).

    Callers branch on the bool so they don't ACT on, or ANNOUNCE, a
    transition that didn't happen: the run-start guard refuses to execute a
    superseded agent, and the in-loop block/abort/complete emits skip their
    audit + notification when the manifest was changed under them (e.g. the
    user paused/aborted via the PWA). On `False`, the external state WINS — we
    never force our intended status over it. (We deliberately do NOT re-raise:
    propagating would let the outer `except Exception` abort the agent, which
    would turn a user pause into an abort.)"""
    try:
        from codec_agent_plan import set_status, InvalidStatusTransition
    except Exception as e:
        log.error("[%s] codec_agent_plan import failed for set_status: %s",
                  agent_id, e)
        return False
    try:
        set_status(agent_id, new_status, reason=reason)
        return True
    except InvalidStatusTransition as e:
        # Usually a benign race: status changed under us by an external actor
        # (PWA pause/abort/grant). The external change wins.
        log.warning("[%s] transition → %s rejected (superseded?): %s",
                    agent_id, new_status, e)
        return False
    except Exception as e:
        log.error("[%s] set_status %s failed unexpectedly: %s",
                  agent_id, new_status, e)
        return False


def _run_agent(agent_id: str, cid: Optional[str] = None) -> None:
    """The main per-agent thread function. Loads plan + grants,
    verifies plan_hash, walks checkpoints via _execute_checkpoint,
    persists state, emits audit events.

    On any unhandled exception: atomic save status=aborted, log,
    emit agent_aborted. Never propagates exceptions to caller (the
    daemon's thread pool depends on this).

    `cid` lets the daemon's crash-recovery path mint a single correlation_id,
    emit AGENT_RESUMED under it, then chain all of this run's emits to the
    same id (Step 1 §1.4 paired-cid contract). When None, generate fresh.
    """
    from codec_agent_plan import (
        load_plan, load_state, load_manifest, load_grants,
        load_global_grants, save_state, save_manifest,
        compute_plan_hash,
    )
    try:
        from codec_agent_messaging import post_message
    except ImportError:
        post_message = lambda **kw: None  # graceful degradation

    if cid is None:
        cid = secrets.token_hex(6)

    try:
        plan = load_plan(agent_id)
        if plan is None:
            log.warning("[%s] plan missing; aborting", agent_id)
            _atomic_set_status(agent_id, "aborted", reason="plan_missing")
            _audit(AGENT_ABORTED, message=f"plan missing for {agent_id}",
                   correlation_id=cid, outcome="error", level="error",
                   extra={"agent_id": agent_id, "reason": "plan_missing"})
            return

        manifest = load_manifest(agent_id)
        stored_hash = manifest.get("plan_hash", "")
        actual_hash = compute_plan_hash(plan)
        # Q13 (review fix I1): if stored_hash is missing/empty, the plan was
        # never properly approved or someone cleared the hash. Either way:
        # ABORT. The "if stored_hash and ..." pattern silently bypasses
        # tamper detection on hash absence — that's an attack vector.
        if not stored_hash:
            log.warning("[%s] plan_hash absent — refusing to run (never approved or hash tampered)",
                        agent_id)
            _atomic_set_status(agent_id, "aborted", reason="plan_hash_missing")
            _audit(AGENT_ABORTED, message="plan_hash missing",
                   correlation_id=cid, outcome="error", level="error",
                   extra={"agent_id": agent_id, "reason": "plan_hash_missing"})
            return
        if stored_hash != actual_hash:
            log.warning("[%s] plan_hash tamper: stored=%s actual=%s",
                        agent_id, stored_hash[:8], actual_hash[:8])
            _atomic_set_status(agent_id, "aborted", reason="plan_tampered")
            _audit(AGENT_ABORTED, message="plan tampered",
                   correlation_id=cid, outcome="error", level="error",
                   extra={"agent_id": agent_id, "reason": "plan_tampered",
                          "stored_hash": stored_hash[:8], "actual_hash": actual_hash[:8]})
            return

        grants = load_grants(agent_id)
        global_grants = load_global_grants()
        state = load_state(agent_id)
        current_idx = int(state.get("current_checkpoint", 0))

        # Transition approved → running (or any prior state → running for resume).
        # C-5 guard: if the transition doesn't apply (e.g. the agent was aborted
        # or paused via the PWA between approval and now), STOP — never execute
        # checkpoints on a superseded agent. The daemon reconciles next tick.
        if not _atomic_set_status(agent_id, "running"):
            log.warning("[%s] run-start aborted: status not transitionable to "
                        "running (superseded by external abort/pause?)", agent_id)
            return
        _audit(AGENT_STARTED, message=f"agent started {agent_id}",
               correlation_id=cid,
               extra={"agent_id": agent_id,
                      "checkpoint_count": len(plan.checkpoints),
                      "starting_at": current_idx})
        post_message(agent_id=agent_id, type="agent_update",
                     title=f"Agent started: {manifest.get('title', agent_id)}",
                     body=f"Starting plan execution from checkpoint {current_idx + 1} of {len(plan.checkpoints)}.",
                     actions=[
                         {"label": "Pause", "endpoint": f"/api/agents/{agent_id}/pause"},
                         {"label": "Abort", "endpoint": f"/api/agents/{agent_id}/abort"},
                     ],
                     correlation_id=cid)

        # Walk checkpoints
        history: List[Dict[str, Any]] = []
        # Review fix I2: per-checkpoint step_budget overrides applied on resume
        # after /extend_budget endpoint bumps the cap. Keys are checkpoint IDs.
        budget_overrides = state.get("step_budget_overrides", {}) or {}
        for idx, cp in enumerate(plan.checkpoints):
            if idx < current_idx:
                continue  # resume: skip already-completed checkpoints
            effective_budget = int(budget_overrides.get(cp.id, cp.step_budget))
            cp_dict = {
                "id": cp.id, "title": cp.title, "description": cp.description,
                "skills_needed": cp.skills_needed,
                "expected_output": cp.expected_output,
                "step_budget": effective_budget,
            }

            _audit(AGENT_CHECKPOINT_STARTED,
                   message=f"checkpoint {cp.id} started",
                   correlation_id=cid,
                   extra={"agent_id": agent_id, "checkpoint_id": cp.id,
                          "checkpoint_idx": idx})

            try:
                history = _execute_checkpoint(
                    plan_dict=plan.to_dict(), checkpoint=cp_dict,
                    agent_grants=grants, global_grants=global_grants,
                    agent_id=agent_id, history=history,
                )
            except PermissionViolation as pv:
                # C-5: only announce the block if the transition actually applied
                # (skip the misleading audit/notification if an external pause/
                # abort already won the race).
                if _atomic_set_status(agent_id, "blocked_on_permission",
                                      reason=f"{pv.reason}:{pv.needed}"):
                    _audit(AGENT_BLOCKED_ON_PERMISSION,
                           message=f"blocked: {pv.reason}",
                           correlation_id=cid, outcome="warning", level="warning",
                           extra={"agent_id": agent_id, "checkpoint_id": cp.id,
                                  "reason": pv.reason, "needed": pv.needed[:200]})
                    post_message(agent_id=agent_id, type="agent_blocked",
                                 title=f"Blocked: {pv.reason}",
                                 body=f"Agent needs additional permission: `{pv.needed}`. Grant or skip?",
                                 actions=[
                                     {"label": "Grant", "endpoint": f"/api/agents/{agent_id}/grant",
                                      "body_hint": {"kind": "<infer from reason>", "value": pv.needed}},
                                     {"label": "Abort", "endpoint": f"/api/agents/{agent_id}/abort"},
                                 ],
                                 correlation_id=cid)
                else:
                    log.info("[%s] block not announced — status superseded externally", agent_id)
                return
            except DestructiveOpRejected as e:
                # C-5: only announce the abort if the transition applied.
                if _atomic_set_status(agent_id, "aborted",
                                      reason=f"destructive_rejected:{e}"):
                    _audit(AGENT_ABORTED, message="destructive op rejected",
                           correlation_id=cid, outcome="warning", level="warning",
                           extra={"agent_id": agent_id, "checkpoint_id": cp.id,
                                  "reason": "destructive_rejected"})
                    post_message(agent_id=agent_id, type="agent_aborted",
                                 title="Aborted: destructive op rejected",
                                 body=f"User rejected a destructive operation. Plan halted.",
                                 actions=[],
                                 correlation_id=cid)
                else:
                    log.info("[%s] abort not announced — status superseded externally", agent_id)
                return
            except StepBudgetExhausted as e:
                # Q7: distinguish "destructive_consent_timeout" from real budget hits.
                # C-5: only announce if the transition applied (external state wins).
                if "destructive_consent_timeout" in str(e):
                    if _atomic_set_status(agent_id, "blocked_on_destructive",
                                          reason="destructive_consent_timeout"):
                        _audit(AGENT_PAUSED,
                               message="paused on destructive consent timeout",
                               correlation_id=cid, outcome="warning", level="warning",
                               extra={"agent_id": agent_id, "checkpoint_id": cp.id,
                                      "reason": "destructive_consent_timeout"})
                    else:
                        log.info("[%s] block not announced — status superseded externally", agent_id)
                else:
                    # Review fix I2: real budget hit → paused (not blocked_on_permission).
                    # User can resolve via POST /api/agents/{id}/extend_budget which
                    # writes step_budget_overrides[checkpoint_id] to state.json and
                    # transitions status=paused → running. The plan stays immutable
                    # (plan_hash tamper check remains intact); the override lives in
                    # mutable state.json.
                    if _atomic_set_status(agent_id, "paused",
                                          reason="step_budget_exhausted"):
                        _audit(AGENT_PAUSED,
                               message="paused on step budget exhaustion",
                               correlation_id=cid, outcome="warning", level="warning",
                               extra={"agent_id": agent_id, "checkpoint_id": cp.id,
                                      "reason": "step_budget_exhausted"})
                    else:
                        log.info("[%s] pause not announced — status superseded externally", agent_id)
                return

            # Checkpoint complete: atomic state save (resume guarantee)
            save_state(agent_id, {
                "current_checkpoint": idx + 1,
                "history_len": len(history),
                "last_checkpoint_completed_at": _now_iso_local(),
            })
            _audit(AGENT_CHECKPOINT_COMPLETED,
                   message=f"checkpoint {cp.id} completed",
                   correlation_id=cid,
                   extra={"agent_id": agent_id, "checkpoint_id": cp.id,
                          "checkpoint_idx": idx, "steps_used": len(history)})
            post_message(agent_id=agent_id, type="agent_update",
                         title=f"Checkpoint {idx + 1}/{len(plan.checkpoints)}: {cp.title}",
                         body=f"Completed in {len(history)} step(s). Output: {cp.expected_output[:200]}",
                         actions=[
                             {"label": "Pause", "endpoint": f"/api/agents/{agent_id}/pause"},
                             {"label": "Abort", "endpoint": f"/api/agents/{agent_id}/abort"},
                         ],
                         correlation_id=cid)

        # All checkpoints done — collect artifacts from project_dir
        project_dir = manifest.get("project_dir", "")
        artifact_lines: list = []
        if project_dir and os.path.isdir(project_dir):
            try:
                files = sorted(
                    e for e in os.listdir(project_dir)
                    if os.path.isfile(os.path.join(project_dir, e))
                )
                for fname in files:
                    fpath = os.path.join(project_dir, fname)
                    size = os.path.getsize(fpath)
                    size_str = f"{size:,} bytes" if size < 1024 else f"{size//1024} KB"
                    artifact_lines.append(f"  • {fname}  ({size_str})")
            except Exception:
                pass

        done_body = (
            f"Plan complete. {len(history)} total steps across {len(plan.checkpoints)} checkpoints.\n\n"
            f"📁 {project_dir}\n"
            + ("\n".join(artifact_lines) if artifact_lines else "  (no files created)")
        )

        # C-5: only announce completion if the transition applied (e.g. don't
        # post "Done" over a user abort that landed on the final checkpoint).
        if _atomic_set_status(agent_id, "completed"):
            _audit(AGENT_COMPLETED, message=f"agent completed {agent_id}",
                   correlation_id=cid,
                   extra={"agent_id": agent_id, "total_steps": len(history)})
            post_message(agent_id=agent_id, type="agent_done",
                         title=f"Done: {manifest.get('title', agent_id)}",
                         body=done_body,
                         actions=[
                             {"label": "📂 Open folder",
                              "endpoint": f"/api/agents/{agent_id}/open-folder"},
                             {"label": "📄 View files",
                              "endpoint": f"/api/agents/{agent_id}/artifacts"},
                         ],
                         correlation_id=cid)
        else:
            log.info("[%s] completion not announced — status superseded externally", agent_id)

    except QwenUnavailableError as e:
        # Phase 3.5 review fix C2: dedicated `blocked_on_qwen` status.
        # Distinct from blocked_on_permission — no permission to grant; the
        # LLM service is just down. The daemon auto-resumes on next tick
        # when Qwen comes back online (see _daemon_one_tick blocked_on_qwen
        # branch). Audit emit still uses AGENT_BLOCKED_ON_PERMISSION with
        # reason="qwen_unavailable" since we don't add a new audit constant
        # for this — the status is enough to disambiguate.
        log.warning("[%s] qwen unavailable: %s", agent_id, e)
        # C-5: only announce the qwen-block if the transition applied.
        if _atomic_set_status(agent_id, "blocked_on_qwen",
                              reason=f"qwen_unavailable:{e}"):
            _audit(AGENT_BLOCKED_ON_PERMISSION,
                   message=f"qwen unavailable: {e}",
                   correlation_id=cid, outcome="warning", level="warning",
                   extra={"agent_id": agent_id, "reason": "qwen_unavailable",
                          "status": "blocked_on_qwen"})
        else:
            log.info("[%s] qwen-block not announced — status superseded externally", agent_id)
    except Exception as e:
        log.exception("[%s] unhandled exception in _run_agent", agent_id)
        _atomic_set_status(agent_id, "aborted",
                           reason=f"unhandled:{type(e).__name__}:{str(e)[:100]}")
        _audit(AGENT_ABORTED, message=f"unhandled: {e}",
               correlation_id=cid, outcome="error", level="error",
               extra={"agent_id": agent_id, "reason": "unhandled_exception"})


def _now_iso_local() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Daemon state (module-global) ──────────────────────────────────────────────
MAX_CONCURRENT = int(os.environ.get("AGENT_RUNNER_MAX_CONCURRENT", DEFAULT_MAX_CONCURRENT))
_active_threads: Dict[str, threading.Thread] = {}
_threads_lock = threading.Lock()


def _scan_agents() -> List[Dict[str, Any]]:
    """Walk ~/.codec/agents/*, return manifest dicts. Skips dirs without manifest.json."""
    from codec_agent_plan import _AGENTS_DIR, load_manifest
    out: List[Dict[str, Any]] = []
    if not _AGENTS_DIR.exists():
        return out
    for d in sorted(_AGENTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        m = load_manifest(d.name)
        if m:
            out.append(m)
    return out


def _occupied_slots() -> int:
    """Count active threads + agents in any blocked_* state (Q8 — they
    occupy a slot). Note: completed/aborted/rejected don't occupy.

    Review fix I3: dedupe so an agent counted as `active_thread` is NOT
    also counted as `blocked_*` if its status was just transitioned but
    the thread hasn't been reaped yet."""
    with _threads_lock:
        active_ids = {aid for aid, t in _active_threads.items() if t.is_alive()}
    active_count = len(active_ids)
    blocked_count = 0
    for m in _scan_agents():
        agent_id = m.get("agent_id", "")
        status = m.get("status", "")
        # Skip if already counted as active (avoid double-count during transition window)
        if agent_id in active_ids:
            continue
        if status.startswith("blocked_"):
            blocked_count += 1
    return active_count + blocked_count


def _daemon_one_tick() -> None:
    """Single iteration of the daemon outer loop. Synchronous (unit-testable).
    Production daemon (`run_daemon`) calls this in a `while True` with sleep."""
    if os.environ.get("AGENT_RUNNER_ENABLED", "true").lower() == "false":
        return

    # Reap dead threads
    with _threads_lock:
        dead = [aid for aid, t in _active_threads.items() if not t.is_alive()]
        for aid in dead:
            _active_threads.pop(aid, None)

    agents = _scan_agents()
    occupied = _occupied_slots()

    for m in agents:
        agent_id = m.get("agent_id", "")
        status = m.get("status", "")

        if status == "approved":
            if occupied >= MAX_CONCURRENT:
                continue  # queue: stay approved, picked up next tick
            with _threads_lock:
                if agent_id in _active_threads and _active_threads[agent_id].is_alive():
                    continue  # already running
            t = threading.Thread(target=_run_agent, args=(agent_id,), daemon=True,
                                  name=f"agent-{agent_id}")
            t.start()
            with _threads_lock:
                _active_threads[agent_id] = t
            occupied += 1

        elif status == "running":
            # If no active thread, agent crashed (e.g. PM2 restart). Mark + resume.
            with _threads_lock:
                has_thread = agent_id in _active_threads and _active_threads[agent_id].is_alive()
            if not has_thread and occupied < MAX_CONCURRENT:
                # Mint cid here and propagate into _run_agent so AGENT_RESUMED
                # chains with the agent_started/checkpoint/completed emits that
                # follow (Step 1 §1.4 paired-cid contract; review I4).
                recovery_cid = secrets.token_hex(6)
                _atomic_set_status(agent_id, "crashed_resumed")
                _audit(AGENT_RESUMED,
                       message=f"resumed {agent_id} after crash/restart",
                       correlation_id=recovery_cid,
                       extra={"agent_id": agent_id, "recovery": True})
                # Transition to running and re-spawn
                _atomic_set_status(agent_id, "running")
                t = threading.Thread(target=_run_agent, args=(agent_id,),
                                      kwargs={"cid": recovery_cid}, daemon=True,
                                      name=f"agent-{agent_id}")
                t.start()
                with _threads_lock:
                    _active_threads[agent_id] = t
                occupied += 1

        elif status == "blocked_on_qwen":
            # Phase 3.5 review C2: auto-resume when Qwen returns. We probe
            # Qwen liveness with a tiny request; if the call succeeds, the
            # agent transitions back to running and the daemon respawns it
            # next iteration. No user interaction needed for this block —
            # unlike blocked_on_permission, the user has nothing to grant.
            if occupied >= MAX_CONCURRENT:
                continue
            try:
                # Probe Qwen with a trivial call; if it succeeds, unblock
                _qwen_chat("ping", system_prompt="", max_tokens=1)
                qwen_alive = True
            except QwenUnavailableError:
                qwen_alive = False
            except Exception as e:
                log.debug("[%s] qwen probe error: %s", agent_id, e)
                qwen_alive = False
            if qwen_alive:
                _atomic_set_status(agent_id, "running")
                t = threading.Thread(target=_run_agent, args=(agent_id,), daemon=True,
                                      name=f"agent-{agent_id}")
                t.start()
                with _threads_lock:
                    _active_threads[agent_id] = t
                occupied += 1


def run_daemon() -> None:
    """Production entry point. Blocks forever, ticking every DAEMON_TICK_SECONDS."""
    log.info("codec-agent-runner daemon starting (MAX_CONCURRENT=%d)", MAX_CONCURRENT)
    # H-1 (PR-4A-2): graceful shutdown on PM2 SIGTERM. Agent worker threads are
    # daemon=True (die with the process) and state.json is saved atomically per
    # checkpoint (resume-on-restart is correct per Step 9 Q5), so a clean exit
    # log is enough — nothing to flush.
    import codec_lifecycle
    codec_lifecycle.install_handlers(
        lambda: log.info("codec-agent-runner graceful shutdown (%d active)",
                         len(_active_threads)),
        name="codec-agent-runner")
    # Scan skill registry at startup so skills are available to executing agents.
    # The dashboard calls load_skills() on its own process; the agent runner is a
    # separate PM2 process and must scan independently.
    try:
        from codec_dispatch import load_skills
        load_skills()
        log.info("Skill registry scanned at daemon startup")
    except Exception as e:
        log.warning("Skill registry scan failed at startup: %s", e)
    while True:
        try:
            _daemon_one_tick()
        except Exception as e:
            log.exception("daemon tick raised: %s", e)
        time.sleep(DAEMON_TICK_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_daemon()
