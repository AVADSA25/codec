# Phase 3 — Drop-a-Project Autonomy

**Date:** 2026-05-03
**Status:** Design approved by user (Sections A-E + Q1-Q15 resolved during brainstorming session 2026-05-03). Ready for implementation planning via `superpowers:writing-plans`.
**Pattern:** mirrors Phase 1 (4 steps, sequential, sign-off-gated) and Phase 2 (3 steps, sequential, sign-off-gated, kill-switched). Phase 3 = 3 steps (Steps 8 / 9 / 10), with proactive intelligence deferred to Phase 3.5.
**Anchor example:** *"Build me a Telegram bot that monitors Marbella property listings under €X and pings me on new ones."*
**Constraint:** 100% local LLM (Qwen-3.6 PM2 service). No cloud calls.

---

## §0 Vision

CODEC becomes a **real AI employee** — autonomous, with computer-use, that:

1. Receives a project description (chat / voice / explicit Project mode)
2. Generates a structured plan with explicit permission manifest
3. Awaits your one-shot approval
4. Executes overnight without blocking on per-action confirmations
5. Sends proactive updates as it makes progress
6. Asks for help only when blocked on permission, ambiguity, or destructive ops

**Differentiator:** CODEC's computer-use capability — agents drive the actual machine (clicks, screenshots, browser, terminal, Chrome, file ops), not just LLM API calls. Most competing "agent" products are LLM-only.

**Inspirations:** Claude Agent SDK (long-running plan-and-execute), Devin (status pill + agent timeline), Cursor Agents (mode dropdown UX), Claude Code's plan mode (plan-and-grant).

---

## §1 Overall architecture

### Project lifecycle pipeline

```
You drop a project (chat or Project mode)
   │
   ▼
[Step 8] LLM (Qwen-3.6) drafts plan + permission manifest
   │
   ▼
[Step 8] You review + approve in PWA → grants persisted
   │
   ▼
[Step 9] codec-agent-runner picks up approved plan, spawns agent thread
   │
   ▼
[Step 9] Per-checkpoint loop:
   ├─ run skills via codec_dispatch.run_skill  (Step 1+2 hooks fire)
   ├─ destructive op?  → Step 3 §1.7 strict-consent  (universal floor)
   ├─ outside manifest? → pause, queue question, wait for grant
   └─ checkpoint done → write state atomically, post update message  (Step 10)
   │
   ▼
[Step 9] Plan complete → status=completed
   │
   ▼
[Step 10] You see "done" notification, review work in chat thread
```

### Storage layout (atomic tmp+rename, Phase 2 pattern)

```
~/.codec/agents/<agent_id>/
  manifest.json          — id, title, status, mode, created/updated, plan_hash
  plan.json              — goals, checkpoints, permission manifest (Step 8)
  grants.json            — user-approved subset of manifest (Step 8)
  state.json             — current_checkpoint, retry_count, last_activity, notification_channels
  messages.jsonl         — append-only message log (user ↔ agent)
  events.jsonl           — append-only skill-call / error log
  artifacts/             — sandbox-write target (files agent creates)

~/.codec/agent_global_grants.json   — global allowlist (cross-agent)
~/.codec/agent_runner_kills.json    — per-agent kill state (atomic, persistent)
```

### Process model

- **New PM2 service `codec-agent-runner`** (sibling to `codec-observer`)
- Daemon polls `~/.codec/agents/*/state.json` every 5 s
- Each running agent gets its own thread inside the daemon
- On PM2 restart: daemon scans for `status=running`, resumes from last atomic checkpoint
- `codec-heartbeat` monitors `codec-agent-runner` liveness (Q15 — same pattern as the other 5 services)

### Reuses from Phase 1 + 2 (no rebuilds)

| Phase 1+2 component | Phase 3 use |
|---|---|
| Step 1 audit envelope | `correlation_id` per agent + per checkpoint |
| Step 2 plugin lifecycle hooks | Every `run_skill` call wrapped in `run_with_hooks` automatically |
| Step 3 `ask_user` | "Outside-manifest permission" pauses, "Approve plan" prompt |
| Step 3 §1.7 strict-consent gate | Universal floor for destructive ops (even pre-approved) |
| Step 3 `StepBudget` | Per-checkpoint cap (NOT per-agent — lets agents run long) |
| Step 4 `_SELF_TOOLS` guard | Prevents agent → agent recursion via plugin path |
| Step 5 observer | Passively captures agent activity (no new code needed) |
| Step 7 shift_report | Agent activity surfaces in daily summary automatically |
| `codec_dispatch.run_skill` | Single dispatch chokepoint for every skill call |
| `codec_skill_registry` | Validates `skills_needed` exists at plan time |

### Phase 3 net-new audit events (~14 across 3 steps)

| Event | Step | Source |
|---|---|---|
| `agent_plan_drafted` | 8 | codec-agent-plan |
| `agent_plan_approved` | 8 | codec-agent-plan |
| `agent_plan_rejected` | 8 | codec-agent-plan |
| `agent_plan_revised` | 8 | codec-agent-plan |
| `agent_global_grant_added` | 8 | codec-agent-plan |
| `agent_global_grant_removed` | 8 | codec-agent-plan |
| `agent_started` | 9 | codec-agent-runner |
| `agent_checkpoint_started` | 9 | codec-agent-runner |
| `agent_checkpoint_completed` | 9 | codec-agent-runner |
| `agent_paused` | 9 | codec-agent-runner |
| `agent_resumed` | 9 | codec-agent-runner |
| `agent_blocked_on_permission` | 9 | codec-agent-runner |
| `agent_completed` | 9 | codec-agent-runner |
| `agent_aborted` | 9 | codec-agent-runner |
| `agent_message_sent` | 10 | codec-agent-runner |
| `agent_message_received` | 10 | codec-dashboard |
| `agent_auto_escalated_from_chat` | 10 | codec-dashboard |

### Kill-switch matrix (3 layers × 3 steps)

| Layer | Step 8 | Step 9 | Step 10 |
|---|---|---|---|
| Per-feature env | `AGENT_PLANNING_ENABLED` | `AGENT_RUNNER_ENABLED` | `AGENT_AUTO_ESCALATE_ENABLED` |
| Per-agent | (n/a) | `POST /api/agents/{id}/abort`, `/pause`, `/resume` | `POST /api/agents/{id}/silence` |
| Per-checkpoint | (n/a) | step budget exhaustion → block + ask user to extend | (n/a) |

---

## §2 Step 8 — Plan + Permission Contract

**Goal:** when you drop a project, agent generates a structured plan with explicit permission manifest. You approve before any execution.

### New module: `codec_agent_plan.py`

#### Plan schema (versioned `schema:1`)

```python
{
  "schema": 1,
  "agent_id": "...",
  "goals": [str, ...],
  "checkpoints": [
    {
      "id": str,                    # stable sha8 of (title + description)
      "title": str,
      "description": str,
      "skills_needed": [str, ...],  # validated against codec_skill_registry
      "expected_output": str,
      "step_budget": int,           # default 30 LLM iterations / checkpoint
    }
  ],
  "permission_manifest": {
    "read_paths":      [glob, ...],   # ~/Documents/research/**, etc.
    "write_paths":     [glob, ...],   # SANDBOX-prefixed: ~/.codec/agents/<id>/artifacts/**
    "network_domains": [domain, ...], # auto-extracted from URLs in plan
    "skills":          [str, ...],    # union of all checkpoints' skills_needed
    "destructive_ops": [str, ...],    # explicit — STILL hit Step 3 strict-consent
  },
  "estimated_duration_minutes": int,
  "assumptions": [str, ...],
}
```

#### Pipeline

1. User drops project → `POST /api/agents` creates agent dir + status=`draft_pending`
2. `draft_plan(description)` calls **Qwen-3.6** (Q1 — local-only, no Gemini fallback) with system prompt that enforces the schema
3. Returned plan is **validated**: every `skills_needed` entry must exist in `codec_skill_registry`. Any unknown skill → plan rejected with `validation_error`, status=`plan_failed`
4. Plan written atomically to disk; status → `awaiting_approval`; audit emit `agent_plan_drafted`
5. Plan items already in `~/.codec/agent_global_grants.json` → marked `auto_approved` (Q4 — global allowlist tier)
6. PWA shows plan + permission manifest table → you Approve / Edit / Reject
7. **Approve** → `grants.json` written (subset = full manifest), `manifest.plan_hash = sha256(plan.json)` stored for tamper detection (Q13), status=`approved`, audit emit `agent_plan_approved`. Agent runner (Step 9) picks up.
8. **Edit (inline structured)** → you revise checkpoints / manifest in PWA form (Q2), agent re-validates, audit emit `agent_plan_revised`
9. **Reject** → status=`rejected`, agent dir kept for review then deleted on TTL (default 7 days)

#### Vague-project handling (Q3)

If LLM determines the project description is too vague for a structured plan (heuristic: missing concrete success criteria, no measurable output, no domain), it does NOT draft. Instead:
- Status stays `draft_pending`
- Agent posts up to 3 clarifying questions via `codec_ask_user.ask`
- Once answered, plan drafting retries with the enriched description
- After 3 rounds without convergence: status=`plan_failed`, reason=`description_too_vague`

#### Global allowlist (Q4)

`~/.codec/agent_global_grants.json` (atomic R/W):

```json
{
  "schema": 1,
  "version": 12,
  "network_domains": ["github.com", "news.ycombinator.com", ...],
  "read_paths":      ["~/Documents/research/**", ...],
  "write_paths":     ["~/codec-projects/**", ...],
  "skills":          ["web_fetch", "calculator", "time", ...],
  "updated_at": "...",
}
```

Managed via PWA settings panel: `GET/POST/DELETE /api/agent_global_grants/{kind}/{value}`. Audit emits: `agent_global_grant_added`, `_removed`. Cached in-memory inside `codec-agent-runner`, invalidated on file mtime change.

#### PWA endpoints (Step 8)

- `GET /api/agents` — list all agents (any status)
- `GET /api/agents/{id}` — full state
- `POST /api/agents` — create + draft (body: `description`, `notification_channels`)
- `POST /api/agents/{id}/approve`
- `POST /api/agents/{id}/reject` (body: `reason`)
- `POST /api/agents/{id}/revise` (body: `edited_plan`)
- `GET /api/agent_global_grants` — list
- `POST /api/agent_global_grants` — add (body: `kind`, `value`)
- `DELETE /api/agent_global_grants` — remove (body: `kind`, `value`)

#### Audit emits (Step 8 only)

`agent_plan_drafted`, `agent_plan_approved`, `agent_plan_rejected`, `agent_plan_revised`, `agent_global_grant_added`, `agent_global_grant_removed`

#### Kill switches

- `AGENT_PLANNING_ENABLED=false` — drafting blocked entirely (returns "planning disabled")
- Plan validation hard-rejects any unknown skill (no silent degradation)

#### Tests (`tests/test_agent_plan.py`, ~25 tests)

Schema validation, manifest extraction, atomic R/W roundtrip, state transitions (drafted → approved / rejected / revised), 9 PWA endpoint tests with mocked LLM, schema-version round-trip, global-allowlist auto-approve, vague-description clarifying loop, plan-hash computation, tamper detection.

---

## §3 Step 9 — Background Execution + Audit Envelope

**Goal:** approved plans actually run, autonomously, with permission enforcement and full reuse of Phase 1+2 substrate.

### New module: `codec_agent_runner.py` + new PM2 service `codec-agent-runner`

#### Daemon outer loop (5 s tick)

```python
while True:
    for agent_dir in scan(~/.codec/agents/*):
        agent = load_state(agent_dir)
        match agent.status:
            case "approved":
                if active_threads < AGENT_RUNNER_MAX_CONCURRENT:  # default 3 (Q6)
                    spawn_thread(_run, agent_id)
                    mark_running()
                # else: stays approved, picked up next tick
            case "running":
                if thread_dead(agent_id):
                    mark_crashed(agent_id, recovery=True)
                    if AGENT_RESUME_ON_CRASH: respawn_thread(_run, agent_id)
            case "blocked_on_permission":
                if grant_received(agent_id):
                    mark_running(agent_id)
                    spawn_thread(_run, agent_id)
            case "blocked_on_destructive":
                # Q7 part: queued for morning, no auto-progression
                pass
    sleep(5)
```

#### Per-agent run loop

```python
def _run(agent_id):
    plan = load_plan(agent_id)
    grants = load_grants(agent_id)
    global_grants = load_global_grants()
    state = load_state(agent_id)
    audit_cid = secrets.token_hex(6)  # Step 1 envelope

    # Tamper check (Q13)
    if sha256(plan.json) != manifest.plan_hash:
        atomic_save_status(agent_id, "aborted", reason="plan_tampered")
        emit("agent_aborted", correlation_id=audit_cid, extra={"reason": "plan_tampered"})
        return

    emit("agent_started", correlation_id=audit_cid)

    for cp in plan.checkpoints[state.current_checkpoint:]:
        with StepBudget(cap=cp.step_budget, name=f"agent:{agent_id}:cp:{cp.id}"):
            emit("agent_checkpoint_started", correlation_id=audit_cid, extra={"checkpoint_id": cp.id})

            # LLM ↔ skill loop (Qwen-3.6)
            while True:
                action = qwen36.next_action(plan, cp, state, history=load_events(agent_id))
                if action.kind == "checkpoint_done":
                    break

                # Permission gate (Step 9 NEW — the core enforcement)
                try:
                    permission_gate(action, grants, global_grants)
                except PermissionViolation as pv:
                    atomic_save_status(agent_id, "blocked_on_permission",
                                       reason=pv.reason, needed=pv.needed)
                    emit("agent_blocked_on_permission", correlation_id=audit_cid,
                         extra={"checkpoint_id": cp.id, "reason": pv.reason})
                    post_message(agent_id, type="agent_blocked",
                                 actions=[{"label":"Grant", ...}, {"label":"Skip", ...}])
                    return  # daemon picks up later when user grants

                # Destructive op? Step 3 §1.7 strict-consent (universal floor)
                if action.is_destructive:
                    consent = strict_consent_gate(action, deadline=600)
                    if consent.timed_out:
                        # Q7: blocked_on_destructive (NOT abort), queued for morning
                        atomic_save_status(agent_id, "blocked_on_destructive")
                        emit("agent_paused", correlation_id=audit_cid,
                             extra={"reason": "destructive_consent_timeout"})
                        return
                    if not consent.approved:
                        atomic_save_status(agent_id, "aborted",
                                           reason="user_rejected_destructive")
                        emit("agent_aborted", correlation_id=audit_cid)
                        return

                # Execute via codec_dispatch.run_skill (Step 1+2 hooks fire)
                result = run_skill(action.skill, action.task)
                append_event(agent_id, action, result)

            # Checkpoint done
            state.current_checkpoint += 1
            atomic_save_state(agent_id, state)
            emit("agent_checkpoint_completed", correlation_id=audit_cid, extra={"checkpoint_id": cp.id})
            post_message(agent_id, type="agent_update")  # Step 10 hook

    atomic_save_status(agent_id, "completed")
    emit("agent_completed", correlation_id=audit_cid)
    post_message(agent_id, type="agent_done")
```

#### Permission gate (the core Step 9 enforcement)

```python
def permission_gate(action, agent_grants, global_grants):
    skills = agent_grants.skills | global_grants.skills
    if action.skill not in skills:
        raise PermissionViolation(reason="skill_not_authorized", needed=action.skill)

    if action.touches_path:
        write_paths = agent_grants.write_paths | global_grants.write_paths
        if not any(fnmatch(action.path, p) for p in write_paths):
            raise PermissionViolation(reason="path_not_authorized", needed=action.path)

    if action.network_call:
        domains = agent_grants.network_domains | global_grants.network_domains
        if action.domain not in domains:
            raise PermissionViolation(reason="domain_not_authorized", needed=action.domain)

    # Destructive ops fall through to strict_consent_gate (Step 3 §1.7) — even if pre-approved
```

#### Resume policy (Q5)

After PM2 restart, daemon scans `~/.codec/agents/*/state.json`:

- `status=running` → assume crashed (the thread that was running is gone). Mark `crashed_resumed`. Resume from `state.current_checkpoint` (the LAST atomically-saved checkpoint). Audit emit `agent_resumed` with `extra.recovery=true`.
- Worst case: one operation re-fires (the in-flight one before crash). Idempotent skills are safe; destructive ops re-hit strict-consent on re-fire (universal floor).
- `status=blocked_on_permission` / `blocked_on_destructive` → check for grant / consent, resume if available.

#### Multi-agent concurrency (Q6, Q8)

- Default `AGENT_RUNNER_MAX_CONCURRENT=3`
- Excess approved agents stay `status=approved`, daemon picks next free slot
- Blocked agents (any `blocked_*` state) **occupy a slot** (Q8). Trade-off: 3 agents all blocked overnight = no new agent can start. Acceptable for v1; revisit if it becomes a real constraint.

#### Audit emits (Step 9 only)

`agent_started`, `agent_checkpoint_started`, `agent_checkpoint_completed`, `agent_paused`, `agent_resumed`, `agent_blocked_on_permission`, `agent_completed`, `agent_aborted`

#### Kill switches

- Per-agent: `POST /api/agents/{id}/abort` → atomic `status=aborted` (daemon checks each tick before any further action)
- Per-agent: `POST /api/agents/{id}/pause` → `status=paused` (daemon doesn't re-spawn until `/resume`)
- Global: `AGENT_RUNNER_ENABLED=false` → daemon idles (still scans, but doesn't spawn threads)
- Per-checkpoint: step budget exhaustion (Step 3 reuse) → blocks, posts message asking to extend or abort
- `codec-heartbeat` monitors `codec-agent-runner` (Q15) → emits `service_down` on crash

#### Tests (`tests/test_agent_runner.py`, ~30 tests)

Daemon scan loop, status transitions matrix, permission gate (skill / path / domain × in-manifest / in-global / outside), step budget enforcement, strict-consent for destructive ops (4 paths: approved / rejected / timeout-overnight / timeout-aborted), resume after PM2 restart, multi-agent parallelism (3 concurrent, no state contamination), crash recovery, atomic state writes, plan-hash tamper detection, no-total-cap (Q7) verified.

---

## §4 Step 10 — Proactive Messaging + Project Mode UI

**Goal:** the layer you actually feel. Agent talks back proactively. UI exposes Project mode + agent state. Chat auto-escalates "this is a project" asks. **Proactive intelligence overlay deferred to Phase 3.5** (Q12).

### Agent → User messaging

Agent posts simultaneously to:

1. `~/.codec/agents/<id>/messages.jsonl` — append-only durable log
2. `~/.codec/notifications.json` — banner + chat thread (existing system, Q9)

#### Message types

- `agent_update` — checkpoint complete, here's what I did
- `agent_blocked` — blocked on permission, grant or skip?
- `agent_question` — clarifying question (reuses Step 3 `ask_user` infra)
- `agent_done` — plan complete, here's the summary + artifacts
- `agent_aborted` — aborted (user / crash / step-budget / destructive-rejected)

Each message carries `correlation_id` paired with `agent_started`, plus `actions[]` (Pause / Abort / Grant / View artifacts) rendered inline as buttons.

#### Notification UX (Q9 — PWA only)

Notifications go to existing `~/.codec/notifications.json` system that the PWA already renders. No macOS banner, no iMessage, no Telegram for v1. Same surface you already see for `shift_report`, `ask_user_question`, etc.

#### Multi-message batching (Q10)

If agent finishes 3 checkpoints in 5 minutes while you're away:

- 3 separate messages in the chat thread (preserves timeline)
- ONE notification banner covering the batch (`"3 checkpoints completed in <agent_title>"`) — no badge spam
- Batching window: 60 s (any messages from the same agent within 60 s of the last unread one merge into the same banner)

### User → Agent reply

User reply in chat thread → written as `type=user_reply` to `messages.jsonl`. Daemon picks up next tick, feeds to LLM as additional context for the next `qwen36.next_action` call.

For `agent_blocked` messages, user can also click **Grant** (one-shot grant for this agent only) or **Skip** (skip this op, continue) without typing.

### Project mode UI (PWA)

#### Mode dropdown (added to existing chat composer)

```
[Chat ▾] → Voice / Agent / Project
```

Pick **Project** → input placeholder becomes *"Drop your project here…"*. Send → `POST /api/agents` (Step 8 endpoint) → plan view inline → approve → agent spawns. Agent updates appear back in this chat thread.

#### Status pills (above input when agents are active)

```
🟢 Property bot · running · 2/5 checkpoints · 12m  [Pause][Abort]
🟡 Research X · blocked on permission · 3h ago    [Grant][Abort]
🔵 Recipes · paused · 1d ago                      [Resume][Abort]
```

Multiple agents → stack of pills (max 3 visible, "+N more" expander).

#### "Projects" sidebar tab (small addition, NOT a sidebar overhaul)

List of all agents (any status) with filter pills (Running / Approved / Blocked / Done / Aborted). Click → opens that agent's chat thread + plan view + artifacts browser. Defer the "full Projects sidebar overhaul" to a later phase.

### Auto-escalation from chat (Q11)

When you're in **Chat** mode (not Project mode), an LLM intent classifier runs on each outbound message. Two-signal gate:

1. Classifier says "multi-step / long-running"
2. Plan-stub estimate says ≥ 3 checkpoints

Both true → response prepended with: *"This looks like a project (~30 min, ~5 checkpoints, needs file writes + browser). Promote to Project mode? \[Yes / No, just answer here\]"*. **Yes** → spawn agent (Step 8+9 path). **No** → fall back to single-shot chat answer AND silence the prompt for the rest of this conversation (resets on new chat session).

Threshold tunable in `~/.codec/config.json` → `chat.auto_escalate_threshold`.

### Audit emits (Step 10 only)

`agent_message_sent`, `agent_message_received`, `agent_auto_escalated_from_chat`

### Kill switches

- `AGENT_AUTO_ESCALATE_ENABLED=false` — chat never suggests promotion
- Per-agent: `POST /api/agents/{id}/silence` — agent runs but posts no notifications
- Per-conversation auto-escalation silence (Q11) — first No suppresses for the rest of that chat

### Tests (`tests/test_agent_messaging.py` + `tests/test_chat_escalation.py`, ~25 tests total)

Message post atomic, notifications integration, reply handling, batching window, mode dropdown UI, classifier (mocked Qwen), auto-escalation gate (matrix: classifier × checkpoint estimate × Yes / No / silence-history), per-pattern kill switch persistence.

---

## §5 Error handling, kill switches, edge cases

### 8 named failure modes

| # | Failure | Response |
|---|---|---|
| 1 | Qwen-3.6 down at plan time | status=`plan_failed`, agent dir kept, retry button in PWA |
| 2 | Qwen-3.6 down mid-run | exponential backoff (3 attempts, 5/15/45 s), then status=`blocked_on_llm`, daemon retries every 60 s |
| 3 | Skill missing from registry mid-run | `PermissionViolation(skill_missing)` → status=`blocked_on_permission` with substitute-or-abort prompt |
| 4 | Disk full / atomic write fail | log + 1 retry + `aborted(disk_write_failed)` |
| 5 | PM2 restart mid-checkpoint | daemon scans on boot, `status=running` resumes from last atomic checkpoint per Q5(c). Audit emit `agent_resumed` with `extra.recovery=true` |
| 6 | Global grant revoked mid-run | in-memory cache invalidates on `mtime` change, next op hits `PermissionViolation(grant_revoked)`, agent blocks |
| 7 | Strict-consent timeout overnight | does NOT abort. Agent transitions to `blocked_on_destructive`, queues for morning, skips to next non-destructive op if plan allows |
| 8 | Multi-agent state contamination | all dirs isolated under `~/.codec/agents/<id>/`, no shared globals beyond audit log + observer. Test coverage explicitly asserts |

### 5 named edge cases

| # | Edge case | Mitigation |
|---|---|---|
| A | Agent recurses on itself (plan includes `agent_*` skills) | Plan validator rejects pre-approval |
| B | Skill loop (skill A → skill B → skill A via agent path) | Step 4 `_SELF_TOOLS` guard reuse, no new code |
| C | Approve-then-immediate-abort | Daemon checks `status` before spawning thread; if `aborted`, never spawns |
| D | Plan tampered after approval (manual edit of `plan.json`) | `manifest.plan_hash = sha256(plan.json)` at approval; daemon verifies on each tick (Q13). Mismatch → `aborted(plan_tampered)` |
| E | Agent runs 2× expected duration | No auto-abort. Informational audit emit `agent_running_longer_than_expected` at 2× threshold; user can manually pause/abort |

---

## §6 Testing strategy

### Per-step test files

| File | Tests | Step |
|---|---|---|
| `tests/test_agent_plan.py` | ~25 | 8 |
| `tests/test_agent_runner.py` | ~30 | 9 |
| `tests/test_agent_messaging.py` | ~15 | 10 |
| `tests/test_chat_escalation.py` | ~10 | 10 |
| `tests/test_agent_e2e.py` | ~5 | 8+9+10 mocked end-to-end |

### Test discipline (mirrors Phase 1+2)

All tests:

- Mock Qwen-3.6 LLM calls (no real local LLM hits in tests)
- Mock `codec_dispatch.run_skill` (never fire real skills)
- Use `tmp_path` for all storage paths
- No writes to `~/.codec/*` outside fixtures
- No real notifications posted
- All test files redirect `codec_audit._AUDIT_LOG` to `tmp_path`

### Test invariants (must hold across all 3 steps)

- 0 new baseline failures (must stay 20)
- 0 new skips (must stay 73)
- All Phase 3 events have paired `correlation_id` per Step 1 §1.4 contract
- All atomic writes use tmp+rename (no torn reads possible)
- Permission gate matrix tested (skill × in-manifest / in-global / outside)
- Multi-agent isolation explicitly asserted
- Plan-hash tamper detection asserted
- Resume-after-restart asserted

**Estimated net Phase 3 contribution:** +85 to +100 passing tests.

---

## §7 Reuse map

### What Phase 3 reuses (no rebuilds)

| Phase 1+2 component | Phase 3 file / call site | Why |
|---|---|---|
| `codec_audit.audit()` + `log_event` | All Phase 3 emits | Step 1 envelope, paired correlation IDs |
| `codec_hooks.run_with_hooks` | `codec_dispatch.run_skill` (already wraps) | Step 2 plugin hooks fire automatically on every agent op |
| `codec_ask_user.ask` | "Outside-manifest permission" pause, "Approve plan" prompt | Step 3 reuse |
| `codec_ask_user._strict_consent_gate` | Destructive ops (universal floor) | Step 3 §1.7 reuse |
| `codec_dashboard._StepBudget` | Per-checkpoint cap | Step 3 reuse |
| `codec_skill_registry` | `skills_needed` validation at plan time | Existing infrastructure |
| `codec_dispatch.run_skill` | Single dispatch chokepoint for every agent skill call | Existing infrastructure |
| `codec_audit.PHASE2_STEP5_EVENTS` | Observer captures agent activity passively | No new code |
| `skills/shift_report.py` | Picks up agent activity in daily summary | No new code |
| `~/.codec/notifications.json` | Agent message + banner system | No new code |

### What Phase 3 explicitly does NOT touch

- `codec_self_improve.py` (Phase 1 Step 4 — sealed)
- `_HTTP_BLOCKED` (user explicit constraint)
- `codec-autopilot` (stays stopped per user instruction)
- Apple Reminders / Notes / Calendar (user explicit constraint)
- Marketplace UX (user said "we already have that in place")
- Memory / context system (user said "what we have is good enough")

---

## §8 Resolved open questions

| Q | Topic | User answer |
|---|---|---|
| Q1 | Plan-time LLM | **Qwen-3.6 always (local-only).** No Gemini Flash fallback. |
| Q2 | Plan editing UX | **Inline structured edit** in PWA form. |
| Q3 | Vague-project handling | **Agent asks clarifying questions first** via `ask_user`. |
| Q4 | Permission TTL | **Per-agent + global allowlist tier.** Global at `~/.codec/agent_global_grants.json`, managed via PWA. |
| Q5 | Resume policy after PM2 restart | **(c) Resume from last atomic checkpoint.** Worst case: one op re-fires (idempotent safe; destructive re-hits strict-consent). |
| Q6 | Multi-agent concurrency cap | **3 max** (`AGENT_RUNNER_MAX_CONCURRENT`). |
| Q7 | Total LLM cap per agent | **No total cap for v1.** Per-checkpoint budget sufficient. |
| Q8 | Blocked agents occupy a slot? | **Yes, occupied.** Trade-off accepted; revisit if real constraint. |
| Q9 | Notification channels | **PWA only** via existing `notifications.json`. No macOS banner / iMessage / Telegram for v1. |
| Q10 | Multi-message batching | **3 separate messages in chat thread (preserves timeline) + 1 batched notification banner** (no badge spam). 60 s window. |
| Q11 | Auto-escalation persistence | **Silence after first No** for that conversation; resets on new chat session. |
| Q12 | Proactive intelligence overlay | **Defer to Phase 3.5.** Not in Step 10. |
| Q13 | Plan tamper detection | **Yes, ship it.** `manifest.plan_hash = sha256(plan.json)` verified on each daemon tick. |
| Q14 | OS-level sandbox enforcement | **No for v1.** LLM-side discipline + permission gate at `run_skill` is sufficient. Revisit if needed. |
| Q15 | Heartbeat monitoring of agent-runner | **Yes.** One-line addition to `codec-heartbeat` config — same pattern as the other 5 PM2 services. |

---

## §9 Phase 3.5 / Phase 4 deferrals

**Not in Phase 3 — explicitly deferred:**

- **Proactive intelligence overlay** (Phase 3.5) — observer-driven contextual nudges ("you've been on this Notion doc 30 min, want a summary?"). Introduces a new threat model (false positives, alert fatigue) deserving its own design pass after Steps 8/9/10 are battle-tested.
- **OS-level sandbox enforcement** (Phase 4 if needed) — chroot / AppArmor / similar. Only ship if a "trusting LLM" failure surfaces in production. Existing `codec_sandbox.py` (marketplace path) can be revisited.
- **Multi-channel notifications** (Phase 4 if requested) — macOS banner, iMessage, Telegram delivery channels per-agent. PWA-only is sufficient for v1.
- **Memory / context system** (out of scope) — user said current memory is good enough. Not in Phase 3.
- **Marketplace UX overhaul** (out of scope) — user said "we already have that in place". Not in Phase 3.
- **Total LLM call cap per agent** (Q7 deferred) — per-checkpoint budget is enough for v1; add total cap if Qwen latency / cost becomes an issue.
- **Full "Projects" sidebar overhaul** — Step 10 ships a small "Projects" tab. Bigger UX overhaul deferred until multi-project juggling becomes a real need (YAGNI).

---

## §10 Step ordering rationale

**Why Step 8 first:** plan-and-grant is the SAFETY foundation. Without it, Step 9's runner has nothing to enforce against. Permission manifest is the contract that makes overnight execution safe.

**Why Step 9 second:** execution depends on plans. Without execution, Step 10 has no agent to communicate with.

**Why Step 10 last:** UI/UX layer is surface, depends on substrate from Steps 8+9. Building UI before backend works leads to mocks that drift from reality.

**Each step deployable independently:**

- **Step 8 alone:** you can draft + approve plans, but they never run. Useful for scoping/refining the planning UX before committing to runner.
- **Step 8+9:** plans run end-to-end, but you only see results in `audit.log` + `notifications.json` (no UI, no agent messages in chat). Useful for testing the runner without UI complications.
- **Step 8+9+10:** full feature.

**Sign-off gates between steps** (mirrors Phase 1+2):

- Per-step pre-merge audit (`docs/PHASE3-STEP<N>-PREMERGE-AUDIT.md`)
- Per-step post-merge sample capture (`docs/PHASE3-STEP<N>-POSTMERGE-SAMPLES.md`)
- Per-step sign-off block in `docs/known-issues.md`
- Phase 3 closeout doc (`docs/PHASE3-COMPLETE.md`) when all 3 steps land

---

*Phase 3 design approved 2026-05-03. Ready for implementation planning via `superpowers:writing-plans`.*
