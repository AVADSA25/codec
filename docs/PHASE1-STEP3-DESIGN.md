# PHASE 1 STEP 3 — `AskUserQuestion` + Stuck Detection + Chat-handler Step Budget

**Status:** DESIGN. Not implemented.
**Author:** drafted by Claude Code, reviewed by Mickael + Claude chat before any code is written.
**Depends on:**
- Phase 1 Step 1 (commit `45d4aa7`) — unified audit envelope (`schema:1`) + `correlation_id` contract
- Phase 1 Step 2 (commit `15c6f70`) — plugin lifecycle hook system (`run_with_hooks`, `HookCtx.operation_id`, `hook_fired` / `hook_error` / `tool_vetoed` events)
**Source spec:** `~/ava-stack/docs/PHASE2-design-specs.md` §1, §2, §3 — copied verbatim into Appendix A below as the canonical engineering reference (per the 2026-04-30 chat decision: copy not link).
**Scope:** three of the five `Known gaps (tracked for Phase 2)` from `AGENTS.md` §3 — `AskUserQuestion`, stuck detection, chat-handler step budget. The remaining two (formal teammate / sub-agent recursion, additional self-detection signals) are deferred to Step 5+. **No code changes in this step.**

---

## 0 · Why this exists

Three concrete user-pain symptoms from the AGENTS.md §3 gap list:

1. **Agents guess instead of asking.** When the Writer agent in a `deep_research` crew can't tell if "Maria" is the user's friend or the user's customer, it currently picks one and moves on. Output is sometimes wrong; the user only finds out at the report level. There's no mechanism for the agent to pause and ask.

2. **Loops are silent.** When the calculator skill keeps returning the same wrong result and the agent keeps calling it with the same args, nothing breaks the cycle. The crew burns through its 8-step budget, returns garbage, and the user reads through 3 minutes of LLM output before noticing.

3. **Chat-handler runaway.** A single `/api/chat` turn can cascade through pre-LLM skill hijack → LLM call → post-LLM `[SKILL:...]` tag interpretation → potentially recursive. There's no per-turn cap. A pathological prompt (or a prompt-injected one) can rack up tool calls until something else fails.

This step **adds the three mechanisms — synchronous-feeling AskUserQuestion via async resumption, per-agent stuck detection with soft-then-escalating response, and a per-turn step budget at chat-handler level**. All three integrate with the Step 2 hook surface for plugin observability without permitting bypass: these are safety boundaries.

---

## 1 · `AskUserQuestion` tool design

### 1.1 Source spec (copied verbatim from `~/ava-stack/docs/PHASE2-design-specs.md` §1)

> **Goal**
> Let the agent pause mid-task and explicitly ask the user a structured question instead of guessing wrong.
>
> **CODEC implementation**
> - New skill: `~/codec-repo/skills/ask_user.py`
> - Calls into a new lightweight FastAPI endpoint: `POST /api/ask_user_question` on the dashboard (port 8090)
> - The dashboard renders a modal in `codec_dashboard.html` with the agent's question + a text input — user replies, modal closes, response flows back to the agent
> - For voice sessions: the question is read aloud via Kokoro TTS, user answers via voice (existing wake-word path)
> - Timeout: 60 seconds default. If user doesn't reply, skill returns `"(no answer)"` and the agent decides how to proceed
>
> **Skill API (LLM-facing)**
> ```
> SKILL_NAME = "ask_user"
> SKILL_DESCRIPTION = "Pause and ask the user a clarifying question. Use when ambiguous about user intent, file path, or destructive action."
> SKILL_TRIGGERS = ["ask user", "clarify with user", "confirm with mike"]
> SKILL_MCP_EXPOSE = True
>
> def run(task: str, context: str = "") -> str:
>     # task = the question to ask
>     # returns the user's answer (or "(no answer)" on timeout)
> ```

### 1.2 Step 3 engineering refinements over the source spec

**Three deltas from the source spec, each justified:**

| spec says | this design says | why |
|---|---|---|
| Skill at `skills/ask_user.py` | Skill exists at `skills/ask_user.py` AS THE LLM-FACING SHIM, but the actual blocking + answer routing lives in **`codec_ask_user.py` (new core module)** | The user prompt explicitly says "AskUserQuestion is core code… it's a tool, not a plugin extension." Putting the wait-for-answer state in core module guarantees lifecycle hooks see it; a plugin can't replace or bypass it. |
| 60s timeout default | **600s (10 min) default, configurable via `~/.codec/config.json: ask_user.timeout_seconds`** | 60s is too short for the realistic case — the user is doing something else and only checks the PWA every few minutes. 600s gives breathing room without holding agent state forever. Open question Q1 if the reviewer disagrees. |
| Modal slide-in | Modal slide-in **AND** notifications.json entry with `type="question"` (per §1.6 below) | The source spec assumed the user is on the dashboard. PWA-on-phone scenarios need the badge route too — same channel as scheduler/heartbeat notifications, distinguishable by `type` field. |

### 1.3 Storage — `~/.codec/pending_questions.json`

Single JSON file, atomic write via tmp+rename (mirrors `oauth_state.json` pattern). One `pending_questions` array; entries get appended on emit, mutated in place on answer.

```jsonc
{
  "pending_questions": [
    {
      "id": "q_a3f7b2c8",
      "operation_id": "deepresearch_2026-05-01_AVA",   // crew's operation_id from Step 2
      "correlation_id": "feed1234abcd",                // inherits Step 1 §1.4 contract
      "agent": "Writer",                               // null for solo skill invocations
      "crew_id": "deep_research",                      // null if not from a crew
      "question": "Maria runs which company — Muse Film Festival or AVA Digital?",
      "options": ["Muse Film Festival", "AVA Digital"], // null = free-text only
      "asked_at": "2026-05-01T10:14:23.451+00:00",
      "deadline": "2026-05-01T10:24:23.451+00:00",     // asked_at + timeout_seconds
      "timeout_seconds": 600,
      "status": "pending",                              // "pending" | "answered" | "timed_out"
      "answered_at": null,
      "answered_via": null,                             // "pwa" | "voice"
      "answer": null,
      "asked_from": "chat"                              // "chat" | "voice" | "crew" | "mcp"
    }
  ],
  "schema": 1
}
```

`_load_pending_questions()` / `_save_pending_questions()` helpers in `codec_ask_user.py`, identical pattern to `routes/_shared.py` notification helpers — `threading.Lock` guard, file-rename atomic write.

### 1.4 The blocking surface — sync feel via async wait

`AskUserQuestion.ask(question, options=None, timeout=None)` is what the agent calls. It's invoked from inside `Agent.run`'s ReAct loop (sync code running in a worker thread via `loop.run_in_executor`). The implementation:

1. Generate `pending_question_id = "q_" + secrets.token_hex(4)`.
2. Build the pending-question record (per §1.3 schema). Inherit `correlation_id` and `operation_id` from the calling agent's `HookCtx` (which the agent loop has from Step 2's `run_with_hooks`).
3. Atomic-append to `pending_questions.json`.
4. Write a notification entry per §1.6.
5. Emit `ask_user_question_emit` audit event (§6).
6. **Wait** — `threading.Event` keyed by `pending_question_id`. The waiting agent thread blocks on `event.wait(timeout=timeout_seconds)`.
7. When user answers (PWA `POST /api/agents/answer/{id}` or voice handler), the answer-routing code:
   - Updates the pending-question record (`status="answered"`, `answer=<text>`, `answered_at`, `answered_via`)
   - Emits `ask_user_question_answer` audit event
   - Fires the `threading.Event` for that `pending_question_id`
8. Agent thread unblocks, reads the now-answered record, returns the answer string to the LLM as the tool result.
9. On timeout (no `event.set()` within deadline):
   - Update the pending-question record (`status="timed_out"`)
   - Emit `ask_user_question_timeout` audit event
   - Return the sentinel string `"(no answer — timed out)"` to the agent

**Why threading.Event, not asyncio:** the agent's tool call already runs in a worker thread (per Step 2 `run_with_hooks` in `Agent.run`). A `threading.Event` is the right primitive for that thread to wait on; it does NOT hold a worker pool slot doing busy-work — `Event.wait()` is a kernel sleep, the OS scheduler sleeps the thread until signalled. Per §7's perf test: 100 concurrent waiters on threading.Event consume ~100KB of kernel state, no CPU.

### 1.5 PWA reply path — `POST /api/agents/answer/{pending_question_id}`

```http
POST /api/agents/answer/q_a3f7b2c8
Content-Type: application/json

{ "answer": "Muse Film Festival" }
```

Handler in `codec_dashboard.py` (new endpoint):
1. Load `pending_questions.json`, find by id. 404 if missing.
2. Reject if `status != "pending"` (already answered or timed out).
3. Update record (status, answer, answered_at, answered_via="pwa").
4. Save atomically.
5. Look up the in-memory `_ASKUSER_EVENTS[id]` (a dict from `pending_question_id` → `threading.Event`) and call `.set()`.
6. Mark the corresponding notification entry as `read=true`.
7. Return `{ "ok": true, "agent_unblocked": true }`.

Idempotency: a duplicate POST after status flips to `answered` returns 409 with `{ "error": "already_answered", "answered_at": "..." }`. No state mutation.

### 1.6 Notification format — `notifications.json` extension

Existing schema (per AGENTS.md §6) uses `type ∈ { "task_report", "alert", "status" }`. Add a fourth: `type="question"`. Sample entry:

```jsonc
{
  "id": "notif_a3f7b2c8e409",
  "type": "question",                                       // NEW
  "title": "Writer is asking a question",
  "body": "Maria runs which company — Muse Film Festival or AVA Digital?",
  "status": "warning",                                       // pulses badge distinctly
  "created": "2026-05-01T10:14:23",
  "read": false,
  "schedule_id": null,
  "doc_url": null,
  "pending_question_id": "q_a3f7b2c8",                       // NEW — deep-link
  "options": ["Muse Film Festival", "AVA Digital"],          // NEW — for quick-action buttons
  "agent": "Writer",                                          // NEW — display
  "deadline": "2026-05-01T10:24:23"                          // NEW — show timer in UI
}
```

The PWA badge already polls `/api/notifications/count` every 30s. With the new `type`, the frontend can render question-type entries with a distinct visual (orange pulse vs the existing blue badge for task_reports) and surface the answer affordance inline. See §5 for the full PWA UX.

### 1.7 Resume — operation_id continuity

Per Step 1 §1.4: `correlation_id` is generated once at operation entry and threaded through every audit emit. This is preserved across the AskUserQuestion wait — the agent thread holds the same `correlation_id` in its `contextvars` while blocked, and any nested emits (the `ask_user_question_*` chain, plus later `tool_call`/`tool_result` after resume) carry the same id. **The agent does NOT re-emit a fresh envelope on resume.** The blocked-then-unblocked tool call looks like one logical operation in the audit log — exactly the behavior the analyzer's pairing logic expects.

### 1.8 LLM-facing skill (the `skills/ask_user.py` shim)

```python
SKILL_NAME = "ask_user"
SKILL_DESCRIPTION = "Pause and ask the user a clarifying question. Use when ambiguous about user intent, file path, or destructive action."
SKILL_TRIGGERS = ["ask user", "clarify with user", "confirm with"]
SKILL_MCP_EXPOSE = True

def run(task: str, context: str = "") -> str:
    """task = the question; returns the user's answer string,
    or '(no answer — timed out)' on deadline."""
    from codec_ask_user import ask
    return ask(question=task, options=None, timeout=None)
```

The shim is intentionally thin — discovery happens via `codec_skill_registry`'s AST parse, but the actual logic is in `codec_ask_user.py` (core).

For agents that want to surface options (quick-action buttons in the PWA), they pass them through a structured input format the LLM is taught to emit:

```
TOOL: ask_user
INPUT: {"question": "Approve refund of $400?", "options": ["Approve", "Reject", "Modify"]}
```

The skill `run()` parses JSON-or-string-or-text gracefully (try `json.loads(task)`, fall back to treating `task` as the question with no options).

---

## 2 · Stuck detection

### 2.1 Source spec (copied verbatim from `~/ava-stack/docs/PHASE2-design-specs.md` §2)

> **Goal**
> Self-diagnose when the agent is looping / repeating failures, escalate to user with context.
>
> **CODEC implementation**
> - New skill: `~/codec-repo/skills/stuck.py`
> - Detection logic (called by the LLM when it self-recognizes the pattern, OR auto-triggered after N retries of same skill+args):
>   - Agent inspects last N tool calls (from `audit.log`)
>   - If same skill called ≥3 times with same args → STUCK
>   - If 5 different skills called with no successful result → STUCK
> - On detect: build context summary (last 5 turns), call `ask_user` skill (Spec 1) with: "I'm stuck on X. Last 3 attempts: Y. Want me to try Z, abandon, or do something different?"
>
> **Auto-trigger hook**
> Add a lightweight watchdog in `codec_dashboard.py` chat handler:
> - After every tool call, count repetitions of `(skill_name, args_hash)` in last 10 turns
> - If count ≥3 → inject system message: "You may be stuck. Consider invoking the `stuck` skill."
> - LLM decides whether to accept the nudge

### 2.2 Step 3 engineering refinements

**Where it lives:** core code in `codec_agents.py:Agent.run` (per-agent detection) AND a thin observer in `codec_ask_user.py` (cross-call ledger). Not a plugin — bypass would defeat the safety purpose.

**Detection trigger — N repeats in M turns:**

| parameter | recommended default | configurable via |
|---|---|---|
| `N` (repeat count threshold) | **3** | `~/.codec/config.json: stuck.repeat_threshold` |
| `M` (turn window) | **5** | `~/.codec/config.json: stuck.window` |
| Match key | `(tool_name, sha1(task+context)[:8])` | not configurable |

**Per-agent, NOT per-crew:** Each agent in a crew gets independent stuck-tracking. Reasoning: in a `deep_research` crew, the Researcher might legitimately call `web_search` 5x with different queries while the Writer is looping. Per-crew detection would punish the Researcher for the Writer's bug. Per-agent isolation is the right unit. (Open question Q4 if the reviewer wants per-crew aggregate.)

**Implementation:** ring buffer per agent instance in `Agent` dataclass:

```python
@dataclass
class Agent:
    # ... existing fields ...
    _recent_calls: List[Tuple[str, str]] = field(default_factory=list)  # (tool_name, args_hash)

# Inside run() loop, after each tool call:
key = (tool_name, hashlib.sha1((tool_input or '').encode()).hexdigest()[:8])
self._recent_calls.append(key)
self._recent_calls = self._recent_calls[-self.stuck_window:]   # truncate
repeat_count = self._recent_calls.count(key)
if repeat_count >= self.stuck_threshold:
    self._handle_stuck(tool_name, repeat_count)
```

### 2.3 What happens when stuck fires — soft-then-escalate

Three options from the user prompt: hard abort, soft warning, ask-user. **Recommendation: soft-then-escalate, in that order:**

| stage | trigger | action |
|---|---|---|
| **soft warning** | first time `repeat_count == N` | Inject a synthetic tool result `"⚠️ You've called {tool} {N} times with the same args. Try a different tool or different inputs — repeating won't help."` into the agent's message log. Emit `stuck_warning` audit event. The next LLM turn sees the warning and decides on its own. |
| **escalation** | `repeat_count >= N+2` (i.e. user ignored the warning, two more identical calls) | Auto-invoke `AskUserQuestion`: `"I've called {tool} {repeat_count} times with the same args and keep getting the same result. Want me to: (a) try a different approach, (b) abandon the task, (c) do something else specific?"`. Emit `stuck_escalated` audit event. Return the user's answer to the agent as the tool result for that round. |

Hard abort is **rejected** as a default — too aggressive. A buggy detector would kill correct work. Configurable opt-in: `~/.codec/config.json: stuck.escalation_action ∈ { "ask_user" (default), "abort", "warn_only" }`.

### 2.4 Manual `stuck` skill (optional companion)

Per the source spec: `skills/stuck.py` exists as an LLM-callable shim that takes "I think I'm stuck" → produces a context summary → invokes `ask_user`. This is the LLM-self-recognition path; the auto-detection above is the safety net. Both ship in this step.

---

## 3 · Step budget at chat-handler level

### 3.1 Source spec (copied verbatim from `~/ava-stack/docs/PHASE2-design-specs.md` §3)

> **Goal**
> Hard cap on tool calls per user turn so the agent can't burn through tokens or money on a runaway loop.
>
> **CODEC implementation**
> - New config key in `~/.codec/config.json`:
>   ```json
>   "step_budget": {
>     "default": 15,
>     "voice": 8,
>     "agent_crews": 30
>   }
>   ```
> - In `codec_dashboard.py`'s chat handler:
>   - Track `tool_calls_this_turn` counter
>   - Before each tool call, check counter < budget
>   - At budget: inject system message *"You've hit the step budget for this turn. Summarize what you have and stop."* — LLM produces a graceful "here's what I got" response instead of grinding
>   - Logged to audit as `outcome=budget_exhausted`
>
> **LLM-visible behavior**
> After step #14 of #15, prompt suffix gets *"⚠️ 1 step remaining. Wrap up."* When exhausted: forced summary mode.

### 3.2 Step 3 engineering refinements

**The user prompt and the source spec disagree on the default value:** source says `default=15`, user prompt says "recommend 5". Reconciliation: 5 covers the *normal* chat-turn case (a few skill calls + one LLM call) but is tight for legitimate cascades (LLM emits `[SKILL:weather:Paris]` + `[SKILL:calculator:5*7]` + answer in one response = 3 tool calls already). 15 is loose enough to never trip on normal use, but lets a runaway loop run 15 cycles before stopping. Surface as **Open Question Q3**; this design assumes user-prompt **default=5 for chat, 5 for voice, MCP exempt** with the source's 15 / 8 / 30 listed in Q3 as the alternative.

**Where the counter lives:** `codec_dashboard.py:/api/chat` handler holds a per-request `tool_calls_this_turn = 0` counter. Increment around each path that fires a skill:
- `_try_skill` pre-LLM hijack (line ~2127 → run_skill via codec_dispatch)
- `_try_skill_by_name` post-LLM `[SKILL:...]` tag (line ~2412 → run_skill via codec_dispatch)
- The LLM call itself counts as 1 step (so the budget includes "the conversation turn")

**Interaction with crew + agent budgets:**

```
chat handler:    tool_calls_this_turn ≤ STEP_BUDGET_CHAT   (default 5)
  └─ if invokes a crew: crew has its own max_steps=8 (existing)
       └─ each agent in crew: max_tool_calls=5 (existing)
```

The chat-handler cap is the **outermost** boundary. A crew spawned from `/api/chat` counts as **1** step toward the chat budget (the "spawn the crew" call), regardless of how many internal steps the crew burns. The crew's own `max_steps=8` and per-agent `max_tool_calls=5` are independent inner budgets. This avoids double-counting.

**Configurable per-route:**

```jsonc
"step_budget": {
  "chat": 5,                // /api/chat per turn (chat tab + dashboard pwa)
  "voice": 5,               // voice WebSocket per utterance
  "mcp": null,              // MCP inherits skill timeout (30s) — no turn-budget
  "agent_crew_max_steps": 8 // unchanged from Crew.max_steps default
}
```

`mcp: null` means MCP doesn't have a turn concept — each tool call is its own turn, governed by SKILL_TIMEOUT_SEC (30s) and the unchanged blocked-stub registration from Step 2.

### 3.3 LLM-visible behavior

Per source spec: at step `budget - 1`, append `"⚠️ 1 step remaining. Wrap up."` to the system prompt suffix for that turn. At budget exhaustion: forcibly switch the LLM call to a "summarize what you have and stop" mode — system prompt becomes `"You've hit the step budget. Summarize what you accomplished and any blockers. Do NOT call more tools."` and `max_tokens` halves.

This matches the source spec's "graceful exit" behavior.

### 3.4 Audit event

When budget exhausts: emit `step_budget_exhausted` (per §6) with `extra.budget_type ∈ { "chat_turn", "crew_max_steps", "agent_max_tool_calls" }`, `extra.limit`, `extra.actual`. The crew + agent variants of this event are added by extending the existing `crew_complete` / `agent_finish` events to optionally fire the new event when their respective budgets cap out. Step 1 §1.4 contract preserved — the new event inherits `correlation_id` from the wrapping operation.

---

## 4 · Integration with the Step 2 hook system

### 4.1 Where the new code lives — core, not plugins

| feature | location | reasoning |
|---|---|---|
| `AskUserQuestion` core (`codec_ask_user.py`) | core module | It's a tool, not an extension. Plugin replacement would let a malicious plugin intercept "ask user" calls and answer them silently. Same threat model as `_HTTP_BLOCKED` — a safety boundary belongs in core. |
| Stuck detection (per-agent ring buffer in `Agent`) | core, in `codec_agents.py` | Bypass would defeat the purpose. A plugin could see stuck signals via `on_error` hook (when escalation invokes ask_user, that's a tool_call the plugin sees) but cannot disable detection itself. |
| Step budget enforcement (chat-handler counter) | core, in `codec_dashboard.py:/api/chat` | Same rationale. The budget is the user's last-line-of-defense against runaway costs. Plugins observe `step_budget_exhausted` audit events but cannot override the cap. |

### 4.2 Plugin observability — new hook surface

**No new hook lifecycle is added in this step.** Plugins observe via:

- `on_error(ctx, exc)` — if escalation eventually raises (e.g. `STUCK_DETECTION_ENABLED=false` AND `escalation_action=abort`)
- `pre_tool` / `post_tool` — sees `ask_user` like any other tool call
- Audit-log tail — plugins that maintain sidecar state can subscribe to `~/.codec/audit.log` and react to `stuck_warning` / `stuck_escalated` / `step_budget_exhausted` events. This is the same pattern Step 4's `codec_self_improve` plugin will use.

Adding a dedicated `on_budget` hook was considered and **rejected** for this step. Reasoning: plugins that need to react can do so via audit-tail or via `on_error`; introducing a fifth lifecycle slot for a single use case (cost-cap notifications) bloats the contract before there's a concrete consumer. Defer to Step 5+ if a real need surfaces.

### 4.3 What plugins CAN observe but CANNOT veto

- The `ask_user` tool call IS routed through `run_with_hooks` (it's a normal tool from the wrapper's perspective). A plugin's `pre_tool` can therefore see the question being asked.
- A plugin's `pre_tool` returning `HookVeto` on an `ask_user` call would block the question from being asked. This is **intentionally permitted** — a privacy plugin might block ask_user calls that contain personally-identifying questions. The veto path emits `tool_vetoed` per Step 2 §4 and the agent sees a deterministic veto string.
- A plugin's `pre_tool` CANNOT veto stuck-detection or step-budget-exhausted *events* — those are emitted directly by core code, not via `run_with_hooks`. The events are observable read-only.

This asymmetry is correct: ask_user is user-interaction code (vetoable, per the privacy story); stuck and step-budget are safety boundaries (not vetoable).

---

## 5 · PWA UX for `AskUserQuestion`

### 5.1 Notification rendering

The existing badge on the dashboard polls `/api/notifications/count` every 30s. Frontend changes:

- Question-type entries (`type="question"`) get a distinct visual: **orange pulse** (vs the existing blue badge for `task_report`), badge count incremented separately. CSS class `.notif--question` with `animation: pulse 1.2s infinite`.
- Click on a question-type notification opens an **inline answer panel** at the top of the chat, NOT a modal that takes the screen. Reasoning: modals interrupt; a panel keeps context. The panel:
  - Shows `agent` ("Writer is asking…"), `question`, `deadline countdown`
  - Has a textarea for free-text reply
  - If `options` is non-null, renders quick-action buttons (`["Approve", "Reject", "Modify"]` in the source example) as primary buttons; clicking submits the option label as the answer
  - "Send" button submits via `POST /api/agents/answer/{id}`
  - On success: panel collapses with a green checkmark; next polled `/api/notifications/count` returns the unread count minus one

### 5.2 Mobile (PWA over Cloudflare tunnel)

Per AGENTS.md §1: inbound is PWA-only. The mobile flow is the same — same `/api/agents/answer/{id}` endpoint, same notification entry. The mobile PWA already handles the badge poll. Adding the question panel is a single React component; render-time is platform-agnostic.

### 5.3 Voice-session fallback — recommended: yes

If the user is in an active voice session when an `ask_user` is emitted from a *background* crew (e.g. nightly `email_handler` running while the user is on a voice call):
- Detect "active voice session" via `~/.codec/voice_session.json` (touched by `VoicePipeline.run` start, removed in finally) or via PM2 `codec-voice` process state
- TTS announces the question via the existing voice pipeline: `await self._speak("The Writer agent is asking: <question>. Please answer.")`
- Switch the voice session into single-question listen mode: next utterance from user is treated as the answer (NOT a new wake-word command). State flag `self._awaiting_ask_user = pending_question_id`.
- POST the spoken answer back through the same `/api/agents/answer/{id}` path
- Fall back to PWA-only (skip voice) if no voice session is detected

This matches the source spec's "voice path" intent. Scope-controlled: only fires if the agent emit happens *during* an active voice session — does NOT proactively interrupt the user.

If voice ASR returns empty / "no answer", the voice pipeline calls the same path as PWA timeout (deadline-driven, not user-driven).

---

## 6 · Audit envelope additions

Six new event types, all `schema:1` envelope, all inheriting the wrapping operation's `correlation_id` per Step 1 §1.4:

| event | source | required `extra` fields | outcome | level |
|---|---|---|---|---|
| `ask_user_question_emit` | `codec-ask-user` | `pending_question_id`, `question_preview` (≤ `_PREVIEW_MAX`), `options`, `timeout_seconds`, `agent`, `crew_id`, `asked_from` | `ok` | `info` |
| `ask_user_question_answer` | `codec-ask-user` | `pending_question_id`, `answered_via` (`pwa`\|`voice`), `answer_len`, `elapsed_seconds` | `ok` | `info` |
| `ask_user_question_timeout` | `codec-ask-user` | `pending_question_id`, `elapsed_seconds`, `timeout_seconds` | `warning` | `warning` |
| `stuck_warning` | `codec-agents` | `tool`, `repeat_count`, `agent` | `warning` | `warning` |
| `stuck_escalated` | `codec-agents` | `tool`, `repeat_count`, `agent`, `action` (`ask_user`\|`abort`\|`warn_only`) | `warning` | `warning` |
| `step_budget_exhausted` | `codec-dashboard` (chat) / `codec-voice` (voice) | `budget_type` (`chat_turn`\|`crew_max_steps`\|`agent_max_tool_calls`), `limit`, `actual` | `warning` | `warning` |

`level="warning"` (not `"error"`) for all new events — they are operationally not failures. `ask_user_question_timeout` is the user not-answering, which is fine; `stuck_*` is detection, not crash; `step_budget_exhausted` is a graceful early-stop. Keeps `audit_report`'s error rate metric meaningful (same logic as Step 2 Q4 hook_error).

All six events get added to `codec_audit.py` as constants alongside `HOOK_EVENT_FIRED` / `HOOK_EVENT_ERROR` / `HOOK_EVENT_VETOED` (Step 2 (b) precedent).

---

## 7 · Test plan

Three new test files, ~520 LOC, conservative.

### 7.1 `tests/test_ask_user.py` (~200 LOC)

```python
def test_ask_user_writes_pending_question():
    # Skill called → record appears in pending_questions.json
def test_ask_user_emits_audit():
    # ask_user_question_emit fires with pending_question_id + agent + crew_id
def test_ask_user_writes_notification():
    # notifications.json gets a type="question" entry with deep-link
def test_ask_user_blocks_until_answer():
    # Background thread calls ask(); main thread POSTs answer; ask() returns
    # within 100ms of the POST
def test_ask_user_returns_user_answer():
    # Answer routed via /api/agents/answer/{id} reaches the blocked caller
def test_ask_user_timeout_returns_sentinel():
    # No answer within timeout → returns "(no answer — timed out)"
    # Audit ask_user_question_timeout fires
def test_ask_user_correlation_id_inherited():
    # All three emits (emit/answer/timeout) carry same correlation_id
def test_ask_user_options_render_in_notification():
    # options=["A","B"] → notif["options"]=["A","B"]
def test_ask_user_idempotent_duplicate_answer():
    # POST /api/agents/answer/{id} twice → second returns 409
def test_ask_user_in_voice_session_fallback():
    # Voice session active → TTS announce + listen for spoken answer
def test_ask_user_concurrent_questions_no_state_leak():
    # 10 agents ask simultaneously → 10 distinct ids, 10 distinct events
```

### 7.2 `tests/test_stuck_detection.py` (~150 LOC)

```python
def test_stuck_detected_at_N_repeats():
    # Mock 3 identical tool calls → stuck_warning fires
def test_stuck_NOT_triggered_below_threshold():
    # 2 repeats + 1 different = 3 total, repeat_count=2 < N=3 → no fire
def test_stuck_warning_then_escalation():
    # 3 repeats → stuck_warning. 5th repeat → stuck_escalated, ask_user fires
def test_stuck_per_agent_isolation():
    # Researcher calls web_search 3x; Writer calls calculator 1x → only
    # Researcher gets stuck_warning. (Writer's calculator unaffected.)
def test_stuck_args_hash_distinguishes_calls():
    # web_search("Paris") + web_search("Paris") + web_search("London") +
    # web_search("Paris") = repeat_count of (web_search,Paris)=3 → stuck
def test_stuck_warning_message_visible_to_LLM():
    # Synthetic tool result with "you've called X" appears in messages[]
def test_stuck_escalation_invokes_ask_user():
    # stuck_escalated → ask_user record appears in pending_questions.json
def test_stuck_disabled_via_env_var():
    # STUCK_DETECTION_ENABLED=false → no detection, no warnings
```

### 7.3 `tests/test_step_budget.py` (~170 LOC)

```python
def test_chat_budget_5_normal_case_passes():
    # 3 tool calls in turn → fits under budget=5, no event
def test_chat_budget_exhausted_at_5():
    # 5th tool call → step_budget_exhausted fires, "summarize" mode
def test_chat_budget_warns_at_4():
    # 4th call → "⚠️ 1 step remaining" appended to system prompt
def test_voice_budget_separate_from_chat():
    # voice budget defaults can differ; verify they don't collide
def test_mcp_no_turn_budget():
    # MCP tool calls don't have a turn budget — only SKILL_TIMEOUT_SEC
def test_crew_spawned_from_chat_counts_as_1():
    # /api/chat → run_crew → 8-step crew → counts as 1 step toward chat budget
def test_step_budget_exhausted_audit_event():
    # event=step_budget_exhausted, extra.budget_type="chat_turn", limit=5
def test_step_budget_disabled_via_env_var():
    # STEP_BUDGET_ENABLED=false → no cap, no event
def test_plugin_cannot_bypass_budget():
    # post_tool returning a string cannot prevent budget exhaustion firing
```

### 7.4 Cross-feature integration

```python
# tests/test_step3_integration.py (~50 LOC)
def test_stuck_escalation_writes_pending_question_AND_increments_chat_counter():
    # End-to-end: stuck → escalation → ask_user → counter += 1 → user answers
def test_correlation_id_threads_through_stuck_to_ask_user_to_answer():
    # Same correlation_id appears in stuck_escalated → ask_user_question_emit
    # → ask_user_question_answer
```

### 7.5 Performance contract

- `ask()` blocking: 100 concurrent waiters consume ≤ 100KB RSS, 0% CPU during wait (kernel-blocked threads). Pytest verifies via `resource.getrusage()` delta.
- `_recent_calls` ring-buffer append: O(M) per check (M=5 default). Per-tool-call overhead < 0.1 ms. Verify in `test_stuck_detection.py::test_stuck_detection_per_call_overhead`.
- Step-budget counter: O(1). Negligible overhead.

---

## 8 · Rollback plan

### 8.1 Per-feature env-flag fast disable

This is the first Phase-1 step that introduces user-visible behavior changes (modals, notifications, agent timeouts). Per the user prompt's reasoning, three independent kill-switches:

| env var | default | what it disables |
|---|---|---|
| `ASKUSER_ENABLED` | `true` | The `ask_user` skill returns `"(skill disabled)"` immediately. No questions written, no notifications, no waits. The skill stays *registered* (so existing call sites don't break with `KeyError: ask_user`) but is a no-op. |
| `STUCK_DETECTION_ENABLED` | `true` | The `_recent_calls` ring buffer still tracks (cheap), but `_handle_stuck` becomes a no-op — no warnings injected, no escalation. Audit events still fire (so the analyzer keeps the data) but agent loop is unaffected. |
| `STEP_BUDGET_ENABLED` | `true` | Counter still increments (for telemetry) but the `if counter >= budget` branch is skipped. No `step_budget_exhausted` events. Agent runs uncapped — same behavior as pre-Step-3 main. |

These are read once at process start (cached in module-level `_FEATURES_*` constants) so the toggles take effect on PM2 restart, NOT mid-call. This avoids race conditions where the cap turns off halfway through a turn and the agent suddenly has 0 budget remaining.

### 8.2 Why per-feature flags here vs none in Steps 1 + 2

Step 1's audit envelope was schema-additive — couldn't break user-facing behavior. Step 2's hook layer was zero-impact in production until a plugin exists. Step 3 actually changes how agents behave (modals, timeouts, caps). A flag per feature lets the user disable an individual mechanism without reverting the entire merge if (e.g.) the modal renders weirdly on iPhone but stuck detection is fine.

### 8.3 Git revert as nuclear option

Same as Steps 1 + 2:

```bash
git -C ~/codec-repo revert <merge-commit> --no-edit
git -C ~/codec-repo push origin main
pm2 restart codec-dashboard open-codec codec-mcp-http codec-heartbeat codec-autopilot --update-env
```

Audit-log entries from before the revert remain valid records; the analyzer keeps reading them.

### 8.4 What "broken in production" looks like

| symptom | cause | response |
|---|---|---|
| Question modal appears repeatedly with same text | Bug in agent emission OR pending_questions.json corruption | `ASKUSER_ENABLED=false` + `pm2 restart codec-dashboard`. Inspect `pending_questions.json`. No revert needed. |
| Agent appears stuck mid-task with no UI | `ask()` blocked but `/api/agents/answer/{id}` not reachable | Check `/api/health`. If dashboard responds, check `~/.codec/pending_questions.json`. If dashboard down, restart codec-dashboard. |
| Spurious stuck warnings fire for legitimate retries | N/M defaults too aggressive | `STUCK_DETECTION_ENABLED=false` to disable; tune `~/.codec/config.json: stuck.repeat_threshold` higher; restart. |
| Chat budget kicks in too early (legitimate cascades) | Default of 5 too tight (Q3 alternative was 15) | Bump `step_budget.chat` in `~/.codec/config.json` to 15; restart codec-dashboard. |
| Voice ASR captures user's answer for an `ask_user` but the agent never resumes | `_awaiting_ask_user` flag stuck | Inspect `pending_questions.json`; force-update status to `answered` via the file; restart codec-voice. |
| Agent thread leak (waiters not cleaned up on dashboard restart) | `_ASKUSER_EVENTS` not garbage-collected on dashboard SIGTERM | Restart all 5 PM2 services; pending_questions.json keeps state, threads are reborn. |

### 8.5 Post-deploy 24h sampling

Same shape as Steps 1 + 2. Reuse the Step 1 baseline anchor (avg 987.96 ms / p95 1907.78 ms). Track in new file `docs/PHASE1-STEP3-POSTMERGE-SAMPLES.md` with the same `T+0/+4h/+8h/+12h/+16h/+20h` cadence + `hook_error_count` (Step 2) + new `pending_question_count` and `stuck_event_count` per sample.

Hard-revert criteria add three Step 3-specific signals:

- `pending_question_count > 50` at any sample → indicates ask_user is firing pathologically (e.g. a stuck agent escalates every turn). Revert.
- `stuck_event_count > 20` per 30-min window → false-positive epidemic. Set `STUCK_DETECTION_ENABLED=false` (don't full revert; this is what the flag is for) and tune.
- `step_budget_exhausted` count > baseline 0 (i.e. ANY in 30 min) on the chat path with default budget=5 → either default is too tight (tune to 15 per Q3 alternative) or there's an LLM regression — investigate, don't auto-revert.

---

## 9 · Open questions for the reviewer

| # | Question | Recommendation | Why a real choice |
|---|---|---|---|
| **Q1** | `ask_user` timeout default — 60s (source spec) vs 600s (this design's recommendation) vs configurable-no-default? | **600s default + configurable.** 60s is too short for the realistic case (user is doing something else and only checks PWA every few minutes). Configurable-no-default forces every caller to specify; bad ergonomics. | Real ergonomics call. 60s is comfortable for in-the-flow chat, hostile for ambient/background crews. |
| **Q2** | Stuck thresholds — `N=3, M=5` (this design) vs `N=5, M=10` (more permissive)? | **N=3, M=5.** Source spec says ≥3 repeats is the canonical signal; tighter window catches loops faster. | Higher N would miss tight loops; higher M would generate false positives for legitimate agents that revisit a tool naturally over many turns. |
| **Q3** | Step budget default — chat=5 (user prompt) vs chat=15 (source spec)? | **chat=5 default**, document chat=15 as the alt. The user prompt is more conservative — catches more pathological cases at the cost of trimming some legitimate cascades. Easy to bump in `~/.codec/config.json` if 5 turns out tight. | Defaults are sticky. Aggressive default → user feedback and we tune up. Loose default → tokens burned before anyone notices. |
| **Q4** | Stuck detection per-agent (this design) vs per-crew aggregate? | **Per-agent.** Each agent has independent state and independent dysfunction modes. Aggregating would mask one agent's loop behind another's normal activity. | A `deep_research` crew's Researcher and Writer have different work patterns; treating them as one silences signal. |
| **Q5** | Voice ask_user — TTS+listen during active voice session (this design) vs always defer to PWA? | **TTS+listen if a voice session is currently active.** Closing the loop without forcing context switch is the user-respect move. | Forcing PWA hop mid-voice-session breaks the use case the user is in — talking to CODEC. |
| **Q6** | Step-budget interaction with crew nesting — chat-spawned crew counts as 1 step toward chat budget (this design) vs each crew internal step counts? | **1 step.** The crew has its own 8-step budget; double-counting punishes legitimate crew work twice. | Counting nested steps inflates the chat-turn budget unpredictably (depends on which crew got spawned). 1-step charge keeps the chat budget about chat. |
| **Q7** | PWA quick-action buttons — supported in this step (free-text + structured options) vs free-text only first, options in a follow-up step? | **Both in this step.** Adding options is ~30 lines of JS in `codec_dashboard.html` and the affordance is what makes "Approve / Reject" flows actually fast. | Options are the differentiator vs a generic notification. Free-text-only would feel like email. |

Step 1 had 5 open questions (resolved before merge). Step 2 had 6. Step 3 has 7. The pattern reflects scope: each step layers more user-visible mechanism on the previous, expanding the surface that needs reviewer judgment.

---

## 10 · Diff inventory — what gets shipped at implementation time

| File | Δ | What |
|---|---|---|
| `codec_ask_user.py` (new) | ~+220 LOC | Core: `ask()`, `_load_pending_questions()`, `_save_pending_questions()`, `_ASKUSER_EVENTS` registry, threading.Event-based blocking, voice-session TTS+listen fallback, audit emits |
| `skills/ask_user.py` (new) | ~+30 LOC | LLM-facing shim: `SKILL_NAME`, `SKILL_DESCRIPTION`, `SKILL_TRIGGERS`, `def run()` calling `codec_ask_user.ask()` |
| `skills/stuck.py` (new, optional companion) | ~+50 LOC | Manual stuck-skill the LLM can self-invoke. Builds context summary, calls `ask_user`. Core auto-detect lives in `codec_agents.py` |
| `codec_agents.py` | ~-2 / +60 | Agent dataclass: `_recent_calls` ring buffer + `stuck_threshold` / `stuck_window` / `stuck_escalation_action` config-loaded fields. `_handle_stuck()` method (warn → escalate). Agent loop: append to `_recent_calls` after each tool, check threshold |
| `codec_dashboard.py` | ~+90 LOC | `/api/chat` handler: `tool_calls_this_turn` counter + budget check + warn-at-N-1 + force-summary-at-N. New endpoint `POST /api/agents/answer/{id}`. Notification serializer extends to handle `type="question"` entries with deep-link, options, deadline countdown fields. |
| `codec_dashboard.html` | ~+120 LOC | Question-type notification renderer (orange pulse), inline answer panel with textarea + quick-action buttons, deadline countdown. JS: poll `/api/notifications` for `type="question"` and surface the panel |
| `codec_voice.py` | ~+45 LOC | Voice-session ask_user fallback: detect active session, TTS-announce, listen for spoken answer, route through `/api/agents/answer/{id}`. State flag `self._awaiting_ask_user` |
| `codec_audit.py` | ~+15 LOC | Six new event constants + docstring extension of Step 2's enum |
| `routes/_shared.py` | ~+20 LOC | Notification serializer accepts `type="question"` and the new fields (`pending_question_id`, `options`, `agent`, `deadline`); `_save_notification()` accepts a `type` kwarg |
| `~/.codec/config.json` | small additive | New keys: `ask_user.timeout_seconds`, `stuck.repeat_threshold`, `stuck.window`, `stuck.escalation_action`, `step_budget.chat`, `step_budget.voice` |
| `AGENTS.md` §3 | small update | Remove "No `AskUserQuestion` tool", "No `stuck` self-detection", "No step budget at chat-handler level" lines from Known-gaps. Add cross-reference to this design doc as Step 3 implementation. |
| `tests/test_ask_user.py` (new) | ~+200 LOC | §7.1 |
| `tests/test_stuck_detection.py` (new) | ~+150 LOC | §7.2 |
| `tests/test_step_budget.py` (new) | ~+170 LOC | §7.3 |
| `tests/test_step3_integration.py` (new) | ~+50 LOC | §7.4 cross-feature |
| `docs/PHASE1-STEP3-POSTMERGE-SAMPLES.md` (new) | small | Reserved for post-merge 24h sampling per §8.5 |

**Net code change:** ~+595 functional LOC (most concentrated in `codec_ask_user.py` and `codec_dashboard.py`), ~+570 LOC tests, **zero breaking changes** to schema:1, the Step 2 hook contract, or to skill/crew/voice/MCP/chat behaviour for users with default config + all three feature flags on. New audit events (`ask_user_question_emit`/`answer`/`timeout`, `stuck_warning`, `stuck_escalated`, `step_budget_exhausted`) are additive and the existing analyzer tolerates them.

---

## Appendix A — source spec, copied verbatim

`~/ava-stack/docs/PHASE2-design-specs.md` Specs 1, 2, 3. Source-of-truth at the BRAND/PRODUCT level; this document is the engineering canonical translation. If they disagree on a detail, this document wins (decisions per §1.2, §2.2, §3.2 above).

The verbatim text is in §1.1, §2.1, §3.1 above (one section per spec, marked with the `> ` blockquote to make the imported boundary visible).

---

**End of design (v1).** No code modified. No other docs written. Stops here.
