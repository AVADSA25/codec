# Phase 2 Complete Blueprint

**Goal**: Turn CODEC from "AI assistant that responds to commands" into "AI colleague that observes, anticipates, and reports."

**Three steps. Builds on Phase 1** (audit log + hooks + AskUserQuestion + stuck detection + step budget + self_improve plugin).

**Reviewer-resolved decisions** (locked at intake — see §Y at bottom for the resolution log):

- Q1 Observer cadence: **60s active, 5min idle** (per `CGEventSourceSecondsSinceLastEventType`).
- Q2 Ring buffer depth: **10 minutes**.
- Q3 Trigger storage: **declarative in skill files** (`SKILL_OBSERVATION_TRIGGER` alongside existing `SKILL_TRIGGERS`).
- Q4 Idle definition: **no keyboard/mouse for 30 min** (Quartz `CGEventSourceSecondsSinceLastEventType`).
- Q5 Observation summary into prompt: **gated** — see §X "Observation injection contract" below.
- Q6 No per-app blocklist: confirmed.

---

## Step 5 — Continuous Observation Loop

**What**: A background process that watches what you're working on so CODEC has context without you re-explaining.

**File**: New `codec_observer.py` (~400 LOC) + new PM2 service `codec-observer`.

**Behavior**:
- Polls every 60 seconds when active (`CGEventSourceSecondsSinceLastEventType < 60s`), drops to 5 minutes when idle. Polled state: `active_window` (frontmost app/window), `screenshot_text` (vision OCR of visible screen), clipboard delta (changed since last poll), open files (recently modified in last 5 min).
- Writes to a **RAM-only ring buffer** holding 10 minutes of state. Never written to disk.
- On every chat/voice request, the buffer is summarized into a 200-token context block prepended to the system prompt **subject to the §X injection contract** — not unconditionally.
- No filtering, no blocklist (per Q6: CODEC runs locally, nothing to hide from it).

**Audit events**:
- `observation_tick` — every poll, lightweight (just timestamp + window title length, no content).
- `observation_summary_injected` — when buffer summary gets prepended to a prompt, with `extra.tokens_used`, `extra.transport`, `extra.injection_reason`.

**Storage**:
- RAM only via `collections.deque(maxlen=N)`. Process restart = buffer wiped. By design.
- Optional persistence to `~/.codec/observation_summaries/` only when CODEC explicitly summarizes a session (e.g., end-of-day shift report — Step 7 trigger).

**Integration**:
- Reads via existing `ax_control` + `screenshot_text` + clipboard skills — no new system permissions.
- Hook into prompt assembly in `codec_dashboard.py` chat handler and `codec_voice.py` voice handler — same insertion point as the L1 identity injection at lines 1827-1862 and 288-320.

**Performance budget**: <50ms per poll. <5ms to inject summary into prompt. Memory: <50MB RSS for 10-min buffer.

**Kill switch**: `OBSERVER_ENABLED` env var, default `true`. Setting `false` disables polling and prompt injection.

**Test plan**: 30 tests covering poll cadence (active vs idle transition), ring buffer wraparound, prompt injection contract per §X, summary token count, kill switch, integration with existing Step 1 audit envelope.

---

## Step 6 — Trigger System

**What**: Skills can declare patterns that fire them automatically when the observer detects them. Generalizes the autopilot's hardcoded triggers.

**File**: Extend `codec_skill_registry.py` to recognize a new `SKILL_OBSERVATION_TRIGGER` dict in skill modules, alongside existing `SKILL_TRIGGERS`. New `codec_triggers.py` (~250 LOC) handles matching + firing.

**Skill-side declaration**:
```python
SKILL_OBSERVATION_TRIGGER = {
    "type": "window_title_match" | "clipboard_pattern" | "file_change" | "time" | "compound",
    "pattern": "...",
    "cooldown_seconds": 300,         # min time between fires
    "require_confirmation": True | False,
    "destructive": True | False,     # if True, uses Step 3 destructive consent gate
}
```

**Trigger types**:
- `window_title_match`: regex on active window title. Example: skill `stripe_dashboard_helper` triggers when window title contains "Stripe — Dashboard".
- `clipboard_pattern`: regex on clipboard content. Example: skill `address_lookup` triggers when clipboard matches a postal address pattern.
- `file_change`: file path glob with debounce. Example: skill `csv_validator` triggers when `~/Downloads/*.csv` changes.
- `time`: cron-like expression. Replaces the autopilot's existing time triggers.
- `compound`: AND/OR of the above.

**Behavior**:
- `codec-observer` polls and emits trigger candidates to `codec-triggers`.
- `codec-triggers` checks cooldown, confirmation gate, destructive consent (reuses Step 3 §1.7 logic), then fires the skill via the existing `codec_dispatch.run_skill` chokepoint (already hooked by Step 2).
- Every fire goes through `run_with_hooks` so plugins can pre/post observe.

**PWA UX**:
- New "Triggers" tab in dashboard showing all registered triggers, last fired, cooldown remaining, kill switch per trigger.
- Trigger fires send a notification with `type="trigger_fired"` if `require_confirmation=True`, else fires silently.

**Audit events**:
- `trigger_evaluated` — pattern matched, before cooldown/confirmation gate.
- `trigger_fired` — skill actually invoked.
- `trigger_blocked` — cooldown / confirmation rejection / destructive consent failure.

**Migration**:
- Existing autopilot triggers in `~/.codec/autopilot.json` migrate to new format via one-time script. Old format kept readable as fallback for 30 days.

**Kill switch**: `TRIGGERS_ENABLED` env var, default `true`. Per-trigger disable in PWA.

**Performance budget**: <10ms per evaluation cycle. <1ms cooldown check.

**Test plan**: 35 tests covering each trigger type, cooldown, confirmation, destructive consent integration, migration from autopilot.json.

---

## Step 7 — Shift Report Crew

**What**: New built-in crew in `CREW_REGISTRY` that runs at end-of-day or 30-min idle, produces a single notification summarizing everything CODEC observed and accomplished.

**File**: Add `shift_report` crew to `codec_agents.py` (~150 LOC). New `skills/shift_report.py` for the underlying assembly.

**Trigger**:
- Scheduled fire at 18:00 local time (configurable via `~/.codec/config.json`).
- OR fires when `CGEventSourceSecondsSinceLastEventType > 30min` (no keyboard/mouse for 30 min — detected by observer's idle classifier from Q4).
- Whichever first.

**Inputs assembled**:
- Last 24h of `audit.log` filtered by event types: `tool_result` (successful), `crew_complete`, `schedule_done`, `hook_fired`, `ask_user_question_answer`, `stuck_warning`, `step_budget_exhausted`.
- All notifications from `~/.codec/notifications.json` created in last 24h.
- Observer summaries persisted to `~/.codec/observation_summaries/` (the only persistent observer output).
- Queued skill proposals in `~/.codec/skill_proposals/` not yet reviewed.

**Output sections**:
1. **Completed tasks** — every successful crew run, every long voice session, every multi-step chat conversation.
2. **Blocked / stuck moments** — every `stuck_warning`, `step_budget_exhausted`, `ask_user_question_timeout`. Diagnostic for what frustrated the agent today.
3. **Observed work patterns** — what apps, what files, what you spent time on (from observer summaries).
4. **Pending decisions** — open AskUserQuestions, queued skill proposals, anything waiting on you.
5. **Tomorrow's open threads** — incomplete crews, scheduled work for tomorrow.

**Format**: Markdown, ~500-1500 words depending on day's activity. Renders in dashboard with collapsible sections.

**Delivery**:
- Single notification in `~/.codec/notifications.json` with `type="shift_report"`, distinct visual treatment (green pulse, full-width banner).
- Markdown body opens in dashboard inline reader.
- Optional auto-save to `~/Documents/CODEC Shift Reports/YYYY-MM-DD.md` (configurable, default off).

**Audit events**:
- `shift_report_started` — crew begins assembly.
- `shift_report_completed` — notification posted, with `extra.sections_included`, `extra.word_count`, `extra.duration_ms`.

**Kill switch**: `SHIFT_REPORT_ENABLED` env var, default `true`.

**Test plan**: 20 tests covering input assembly, idle detection, dashboard rendering, audit emission, kill switch.

---

## §X Observation Injection Contract (Q5 override — locked at intake)

The observation buffer is always populated. **Whether the 200-token summary gets prepended to a given LLM call's system prompt is gated**, not unconditional. This protects:

- **Privacy** — cloud LLMs (Claude API, Gemini API) get screen-context only when the user's actual question implies they want it. We don't ship "user has Stripe Dashboard open" to Anthropic on every "what's 2+2".
- **Cost** — 200 tokens × every turn × 100 turns/day × cloud rates adds up. Local Qwen is free; cloud is not.
- **Cognitive load on the LLM** — the LLM doesn't need observer context to answer "what time is it"; injecting it pads the prompt for no benefit.

### Gating rules

```
                                        ┌─ transport == "local"  → INJECT (always)
                                        │
prompt incoming                         ├─ transport in ("http",  ┌─ matches §X.1 pattern → INJECT
   → check transport ────────────────── │  "voice-cloud-LLM",  ───┤
                                        │  "chat-cloud-LLM")      ├─ skill flag set → INJECT
                                        │                         │
                                        │                         └─ otherwise        → SKIP
                                        │
                                        └─ transport == "mcp"     → SKIP (the LLM client
                                                                      decides its own context)
```

### §X.1 Trigger patterns for cloud-transport injection

Cheap text scan, no relevance model. Inject if any of these match the user's prompt text:

1. **Possessive-without-context**: `\b(my|this|that|these|those|the)\s+(\w+)\b` AND the noun isn't a generic word (filter against a stop-noun list: question, thing, time, day, etc.). Examples that match:
   - "what's my Stripe balance"
   - "summarize this PR"
   - "translate that paragraph"

2. **Continuation language**: `\b(continue|resume|next|where was I|pick up|keep going|finish)\b`. Examples:
   - "continue the email"
   - "where was I"
   - "what's next"

3. **Skill / plugin explicitly requests context**: a skill's `SKILL_NEEDS_OBSERVATION = True` module attribute (default `False`) flips injection on for any prompt that triggered that skill. Example: `email_handler` skill needs to know which email is open in the foreground.

### §X.2 Audit observability

Every injection emits `observation_summary_injected` with:
- `extra.tokens_used` — actual token count of the summary
- `extra.transport` — "local" / "http" / "voice" / "chat" / "mcp"
- `extra.injection_reason` — "always_local" / "possessive_match" / "continuation_match" / "skill_flag" / "skipped_no_match"

When the summary is **skipped**, `observation_summary_injected` is NOT emitted (no audit-line spam for the common case). The injection event is the audit signal; no-injection is the silent default.

### §X.3 Kill switch interaction

`OBSERVER_ENABLED=false` skips both polling AND injection. There is no separate `OBSERVATION_INJECTION_ENABLED` — if you don't want the injection, disable the observer entirely.

---

## Cross-cutting concerns

**Privacy**: Observer's RAM-only ring buffer never writes to disk by default. The only persisted observer output is summaries created during shift report assembly. PWA-only inbound channel still holds — observer reads local state, doesn't transmit. Q5 cloud-transport gating adds a second layer of privacy enforcement.

**Performance**: All three new PM2 services (`codec-observer`, `codec-triggers`, none for shift_report — runs as scheduled crew) plus existing 11 = 14 PM2 processes. M1 Ultra has headroom. Observer is the heaviest at ~50MB RSS; triggers <10MB; shift report fires once daily.

**Audit envelope additions** (extending Step 1 §1.2):
- `observation_tick`, `observation_summary_injected`
- `trigger_evaluated`, `trigger_fired`, `trigger_blocked`
- `shift_report_started`, `shift_report_completed`

All inherit `correlation_id` per Step 1 §1.4 contract. Shift report assembly is a multi-emit operation, gets one correlation_id covering all sub-events.

**Hook integration**: All three steps fire through Step 2's `run_with_hooks` chokepoint. Plugins (incl. Phase 1 Step 4's `self_improve` plugin) can observe everything Phase 2 introduces without modification.

**Kill switches**: Three independent env vars (`OBSERVER_ENABLED`, `TRIGGERS_ENABLED`, `SHIFT_REPORT_ENABLED`), all default true. Disabling any one cleanly removes that capability without affecting the other two.

---

## §Y Resolution log (intake decisions)

| # | Question | Resolution | Source |
|---|---|---|---|
| Q1 | Observer poll cadence | 60s active / 5min idle (idle = `CGEventSourceSecondsSinceLastEventType > 60s`) | User intake |
| Q2 | Ring buffer depth | 10 minutes | User intake |
| Q3 | Trigger storage location | Declarative in skill files via `SKILL_OBSERVATION_TRIGGER` dict alongside existing `SKILL_TRIGGERS` | User intake |
| Q4 | Idle definition | No keyboard/mouse input for 30 min via `CGEventSourceSecondsSinceLastEventType` | User intake |
| Q5 | Observation summary into prompt | Gated per §X above (always for local transport; pattern-match or skill-flag gate for cloud transports) | User intake (override of original "every request" recommendation) |
| Q6 | Per-app observation opt-out | No blocklist; confirmed previous decision | User intake |

---

## Diff inventory across Phase 2

| File | LOC delta |
|---|---|
| `codec_observer.py` (new, Step 5) | ~+400 |
| `codec_triggers.py` (new, Step 6) | ~+250 |
| `codec_skill_registry.py` (extend, Step 6) | ~+50 |
| `codec_agents.py` (add `shift_report` crew, Step 7) | ~+150 |
| `skills/shift_report.py` (new, Step 7) | ~+200 |
| `codec_audit.py` (event constants, Step 5+6+7) | ~+15 |
| `codec_dashboard.py` (Triggers tab + shift report rendering + injection logic) | ~+200 |
| `codec_voice.py` (observer prompt injection + idle classifier) | ~+30 |
| `routes/triggers.py` (new) | ~+100 |
| `AGENTS.md` updates | ~+150 |
| Test files (5 new across 3 steps) | ~+800 |
| **Total** | **~+2,345 functional + tests** |

Compared to Phase 1 (~+1,400 functional + 1,580 tests). Phase 2 is roughly the same size, more user-facing.

---

## Sequencing

Same pattern as Phase 1: design → review → implement → pre-merge audit → merge → 24h watch → sign off. **Per step**.

If pace from Phase 1 holds: Phase 2 ships in 5-7 days from start. **No more Apple Reminders for sampling.** Whatever cadence sampling uses, it's launchd-automated or skipped entirely.

After Step 5 sign-off, the implementer (currently me — Claude Code in this session) judges whether Steps 6 and 7 split into 2 PRs or fuse — depends on what falls out of Step 5 implementation. Reviewer authority on scope boundaries reverts to user any time.

---

That's Phase 2 complete. Three steps, fully specced. After Phase 2, CODEC is the AI colleague we sketched on day one: observes your work, fires helpful skills automatically with consent gates, reports back at end of day. Sovereign. Local. Yours.
