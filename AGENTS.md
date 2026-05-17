# AGENTS.md

> Front-door context for any AI coding agent (Claude Code, OpenClaw, Hermes Agent, Cursor, etc.) working on the CODEC repository. Read this fully before making code changes. Update it when architecture changes.

## 1. Identity & purpose

**CODEC** is the open-source engine codename for the **Sovereign AI Workstation** — a voice-controlled, local-first AI agent that runs entirely on the user's macOS hardware. Brand naming follows the iPhone/Darwin pattern: Sovereign AI Workstation is the product, CODEC is the engine. Code paths, `~/.codec/` config, `codec_*.py` modules, and PM2 process names all use CODEC.

**Operating principles** (live in `codec_identity.py:30-65`, do not duplicate here — read the file):
- Local-first: zero data leaves the user's machine unless they explicitly route through a cloud LLM provider
- Inbound is private (PWA over Cloudflare Zero Trust only). Outbound (Gmail, iMessage, Twilio, future WhatsApp/Discord) goes through user-owned bridges
- Every action that touches the user's filesystem, processes, or external services is auditable via `~/.codec/audit.log`

## 2. Repo map

```
codec.py                     Main process: keyboard listener, wake word, dispatch, safety
codec_core.py                Shared core: skill loader, DB init, build_session_script, TTS helper
codec_dashboard.py           HTTP server (3,439 lines): /api/chat, /api/agents, notifications
codec_dashboard.html         PWA frontend
codec_voice.py               WebSocket voice loop, _CREW_TRIGGERS, voice-to-crew dispatch
codec_dictate.py             CODEC Dictate: F5 live-typing + draft refinement (one of the 9 products)
codec_agents.py              Agent + Crew runtime (1,468 lines, see §3)
codec_skill_registry.py      Skill discovery + lazy loading via AST parse
codec_dispatch.py            Skill trigger matching for voice/wake-word path
codec_memory.py              SQLite + FTS5 + public API
codec_memory_upgrade.py      Facts table, CCF compression, tiered retrieval
codec_compaction.py          Context compaction — summarize old turns when window fills
codec_audit.py               Structured audit log (see §6)
codec_audit_analyzer.py      Audit summary skill (audit_report)
codec_hooks.py               Plugin lifecycle hooks (Phase 1 Step 2 — see §3)
codec_scheduler.py           Cron-style scheduler + notification bridge (see §6) — runs as background service inside codec-dashboard, NOT as its own PM2 process
codec_heartbeat.py           Background service health checks + alerts
codec_autopilot.py           Ambient triggers (sunset, time-of-day, etc.) — own PM2 process
codec_self_improve.py        Nightly skill-proposal drafter
codec_marketplace.py         Skill install/search/publish
codec_mcp.py                 MCP tool registration (stdio transport)
codec_mcp_http.py            MCP HTTP transport with OAuth 2.1
codec_oauth_provider.py      Token persistence (30d access / 90d refresh)
codec_slash_commands.py      Chat meta-controls (/help, /skills, /version, /cost, etc.)
codec_identity.py            System prompts (operating principles + chat persona + voice rules)
codec_session.py             Session lifecycle
codec_sandbox.py             Sandboxed file/shell wrappers
codec_config.py              Config + dangerous-pattern detection + _HTTP_BLOCKED
codec_ava_client.py          AVA cloud proxy client (Gemini/Claude/GPT routing for paid users)
codec_imessage.py            iMessage outbound bridge
codec_telegram.py            Telegram outbound bridge
whisper_server.py            Local STT server
routes/agents.py             /api/agents/* endpoints (custom agents, crew launcher)
routes/_shared.py            notifications.json read/write helpers

skills/                      71 built-in skill modules
~/.codec/                    User config + state (see §7)
docs/                        API.md, MCP_HTTP_SETUP.md, CONTEXT_REPORT.md, design docs
```

Other engine modules (`codec_overlays`, `codec_metrics`, `codec_logging`, `codec_gdocs`, `codec_google_auth`, `codec_cdp`, `codec_llm_proxy`, `codec_retry`, `codec_alerts`, `codec_search`, `codec_textassist`, `codec_keyboard`, `codec_watcher`, `codec_watchdog`) are internal helpers — read them when you need them, but they're not part of the navigation surface for an agent making structural changes.

## 3. Agent + Crew runtime

CODEC has its own minimalist multi-agent runtime in `codec_agents.py`. **Zero dependency on CrewAI or LangChain** — it's self-contained, only depends on `requests` and `codec_skill_registry`.

### Core types
- `Tool` (`codec_agents.py:93-110`): `name`, `description`, `fn: Callable[[str], str]` — string in, string out, blocking
- `Agent` (`codec_agents.py:317-358`): `name`, `role` (system-prompt persona), `tools`, `max_tool_calls=5`, `thinking`, `verbose`. The agent loop is ReAct-lite at `codec_agents.py:325-495`, using a text protocol: `TOOL: <name>\nINPUT: <text>` to call a tool, `FINAL: <answer>` to terminate
- `Crew` (`codec_agents.py:512-573`): `agents`, `tasks`, `mode` (`sequential` | `parallel`), `max_steps=8`, `allowed_tools` (hard tool allowlist enforced at construction)

### Single source of truth: CREW_REGISTRY
**`codec_agents.py:1361-1374`** is canonical for built-in crews. Any new built-in crew gets registered there. Currently 12 crews: `deep_research`, `daily_briefing`, `trip_planner`, `competitor_analysis`, `email_handler`, `social_media`, `code_review`, `data_analysis`, `content_writer`, `meeting_summarizer`, `invoice_generator`, `project_manager`.

Public entry point: `run_crew(crew_name, callback=None, **kwargs)` at `codec_agents.py:1380-1395`.

### Built-in agent tools
Defined in `codec_agents.py:113-307`: `web_search`, `web_fetch`, `file_read`, `file_write`, `google_docs_create`, `shell`. Plus every registered skill auto-becomes a tool via `_make_lazy_fn` + `load_skill_tools()` (`codec_agents.py:279-307`).

### Custom agents (user-defined)
Stored as JSON files at `~/.codec/agents/*.json`, keyed by slugified name. CRUD via `routes/agents.py`. Live job state lives in the in-memory `_agent_jobs` dict and does NOT survive `codec-dashboard` restart — this is a known gap.

### Voice-side dispatch
`codec_voice.py:682-721` — `_CREW_TRIGGERS` maps spoken phrases to crew names. `dispatch_crew_from_voice(user_text)` at `codec_voice.py:723-755` does the matching, builds args, runs the crew, streams progress via TTS.

### Plugin lifecycle hooks (Phase 1 Step 2)

CODEC supports user-authored Python plugins at `~/.codec/plugins/*.py` that register lifecycle hooks around skill / tool execution. Hooks fire identically across all five execution paths (crew, voice WebSocket, voice wake-word + chat pre-LLM hijack + chat post-LLM tag via `codec_dispatch.run_skill`, MCP stdio + HTTP).

**Lifecycle:** `pre_tool`, `post_tool`, `on_error`, `on_operation_start`, `on_operation_end`. See `docs/PHASE1-STEP2-DESIGN.md` §1 for the full table. All sync, all observe-or-mutate (never async).

**Discovery:** AST parse at startup (mirrors `codec_skill_registry`). A plugin file declares any subset of the five lifecycle functions plus optional `PLUGIN_NAME` / `PLUGIN_DESCRIPTION` / `PLUGIN_PRIORITY` (default 100; lower runs first) / `PLUGIN_TOOL_FILTER` (exact list of tool names; `None` = all tools). Module imports deferred to first hook fire — broken plugins don't break startup.

**Veto:** `pre_tool` may return `HookVeto(reason="…")` to abort the tool call. The wrapper emits a `tool_vetoed` audit event and the caller receives the deterministic string `"Skill 'X' was vetoed by plugin 'Y': <reason>"`. Crew behaviour: agent's ReAct loop sees the veto string as the tool result and decides on its next move (no retry, no fail-the-crew).

**Mutation contract:** `pre_tool` returns `{"task": str, "context": str}` (either or both keys) to mutate; identity fields (`tool_name`, `transport`, `agent`, `correlation_id`, `client_id`, `operation_id`) are immutable and dropped with a warning. `post_tool` returns `str` to replace the result. `on_error` / `on_operation_*` are observe-only.

**Audit:** every successful hook fire emits `hook_fired`; plugin-internal exceptions emit `hook_error` with `level="warning"` (operation still succeeded). `correlation_id` inherits from the wrapping operation per Step 1 §1.4 — never regenerated.

**Trust model:** local Python files curated by the user. No marketplace, no auto-install, no inter-plugin sandbox. Same trust model as skills.

Implementation: `codec_hooks.py` (run_with_hooks + PluginRegistry), wired into `codec_dispatch.py:run_skill` (covers wake-word + chat pre-LLM + chat post-LLM tag), `codec_agents.py:Agent.run` (crew), `codec_voice.py:dispatch_skill` (voice WebSocket), `codec_mcp.py:tool_fn` (MCP stdio + HTTP). Voice WebSocket also fires `emit_operation_start` / `emit_operation_end` from `VoicePipeline.run` start/finally.

### AskUserQuestion + stuck detection + step budget (Phase 1 Step 3)

CODEC agents can pause and ask the user a structured question, self-detect when they're stuck in a loop, and enforce a per-turn step budget on the chat handler. See `docs/PHASE1-STEP3-DESIGN.md` (commit b187b8d, §9 RESOLVED).

**AskUserQuestion** (`codec_ask_user.py`): public API `ask(question, *, options=None, timeout=600, destructive=False, destructive_verb=None, agent=None, crew_id=None, asked_from="chat", tool_name=None) -> str`. Blocks the caller's worker thread on `threading.Event.wait()` until the user replies via PWA (`POST /api/agents/answer/{qid}`) or voice (`codec_voice.VoicePipeline._handle_voice_ask_user_answer`). Question state in `~/.codec/pending_questions.json` (atomic write); display surface in `~/.codec/notifications.json` with `type="question"`. Skill shim at `skills/ask_user.py` exposes the tool to the LLM and over MCP. Default 600s timeout; tunable via `~/.codec/config.json:ask_user.timeout_seconds`. Kill switch: `ASKUSER_ENABLED=false`.

**Strict-consent gate (§1.7)**: irreversible actions opt in via `destructive=True`, caller-supplied `destructive_verb`, OR auto-trigger when the calling tool name is in `codec_config._HTTP_BLOCKED`. On strict-consent, the answer must contain the destructive verb literally (case-insensitive); generic "yes"/"ok"/"sure" rejected with re-prompt. After two rejections, the question times out as `ask_user_question_timeout` with `extra.reason="ambiguous_consent"` — does NOT wait for the deadline. Voice ASR layer (`codec_voice._resolve_voice_option_choice`) BYPASSES fuzzy match in strict mode so `submit_answer` evaluates the literal verb-match rule.

**Stuck detection** (`codec_agents.Agent`): per-agent ring buffer of last M=5 (tool_name, args_hash) tuples. Soft warning at N=3 identical calls (banner injected into result string + `stuck_warning` audit emit), escalation at N+2=5 calls (configurable: `ask_user` (default), `abort`, or `warn_only` via `~/.codec/config.json:stuck.escalation_action`). Wired into `Agent.run` post-tool via `run_in_executor` so synchronous `ask_user.ask()` doesn't block the event loop. LLM-self-recognized stuck path: `skills/stuck.py` (companion shim, also routes to `ask_user`). Kill switch: `STUCK_DETECTION_ENABLED=false`.

**Chat-handler step budget** (`codec_dashboard._StepBudget`): per-turn cap on the chat handler. Default routes: `chat=5`, `voice=5`, `mcp=None` (no cap). `consume(kind)` returns False when over limit; `warn_now()` returns True at limit-1 (drives "1 step remaining" prompt suffix); first over-step emits a `step_budget_exhausted` audit line (idempotent). Tune up before tuning out: `~/.codec/config.json:step_budget.{chat,voice}` accepts 8 or 10. Kill switch: `STEP_BUDGET_ENABLED=false`.

**Audit envelope**: all Step 3 events use `outcome="warning"`, `level="warning"`. They are NOT `outcome="error"` because each is an operational signal, not an operation failure (same Q4 tightening as Step 2's `hook_error`). `correlation_id` inherits from the wrapping operation per Step 1 §1.4.

### Continuous Observation Loop (Phase 2 Step 5)

CODEC has a background process (`codec-observer` PM2 service, `codec_observer.py`) that polls four cheap signals — frontmost window, screenshot OCR, clipboard delta, recent file changes — and keeps the last 10 minutes of state in a RAM-only ring buffer. On every chat / voice request, an injection helper decides whether to prepend a ≤200-token summary to the LLM's system prompt, gated per the §X "Observation injection contract":

- **`transport="local"`** (local Qwen) → always inject. Cheap + private.
- **`transport="mcp"`** → never inject. The MCP client (claude.ai, Claude.app) brings its own context.
- **`transport in {"chat", "voice", "http"}`** → gated on cheap text-pattern checks: possessive-without-context (`"my X"`/`"this Y"` filtered against a stop-noun list), continuation language (`"continue"`, `"where was I"`), or skill-flag (`SKILL_NEEDS_OBSERVATION = True` on a resolved skill module).

**Privacy contract**: 4 layers. (1) RAM only — `collections.deque` wiped on process restart. (2) Audit emits are METADATA-ONLY: lengths, counts, `content_type` tags, but NEVER raw window titles, OCR text, clipboard content, or file paths. (3) Cloud-transport injection gating per §X. (4) NO new system permissions — uses existing skills + primitives (osascript, pbpaste, Quartz, getmtime).

**Cadence**: 60s when active (`CGEventSourceSecondsSinceLastEventType < 60s`); drops to 5min when idle. Long-idle reset wipes buffer at 30min idle.

**Kill switch**: `OBSERVER_ENABLED=false` env var disables polling AND injection.

**Audit events** (4 new): `observation_tick` (per poll, info), `observation_tick_slow` (poll > 150ms, warning), `observation_summary_injected` (gated inject fired, info, inherits cid), `observer_buffer_inspected` (debug-gated PWA read).

**Forward-compat API for Steps 6 + 7**: `get_global_buffer()` exposes the live ring buffer (Step 6 Triggers reads `.snapshot()` for trigger evaluation); `persist_for_shift_report()` writes a summary to `~/.codec/observation_summaries/<ts>.md` (the only persistent observer output, called by Step 7 shift-report assembly).

Implementation: `codec_observer.py` (RingBuffer + poll + injection helper + run_daemon), wired into `codec_dashboard.py:chat_completion` and `codec_voice.py:generate_response`. Debug PWA endpoint at `GET /api/observer/buffer?debug=1` returns metadata-only summary (raw entries never exposed even to authed callers; emits `observer_buffer_inspected` per call).

### Trigger System (Phase 2 Step 6)

CODEC skills can declaratively auto-fire on observer signals. A skill adds a `SKILL_OBSERVATION_TRIGGER` dict alongside its existing `SKILL_TRIGGERS` list:

```python
SKILL_OBSERVATION_TRIGGER = {
    "type": "window_title_match",      # or clipboard_pattern / file_change / time / compound
    "pattern": r"Stripe — Dashboard",
    "cooldown_seconds": 600,            # min seconds between fires (RAM-only state)
    "require_confirmation": True,       # PWA approval gate before fire
    "destructive": False,               # if True, routes through Step 3 §1.7 strict-consent
}
```

After every `codec_observer.poll()`, `codec_triggers.evaluate(snapshot)` walks the registered triggers, matches each against the snapshot, and dispatches matches that pass cooldown + consent gates through the existing `codec_dispatch.run_skill` chokepoint (which Step 2's `run_with_hooks` already wraps — every fire is observable by plugins).

**5 trigger types**: `window_title_match` (regex on active title), `clipboard_pattern` (regex on clipboard preview), `file_change` (glob over recent_files), `time` (cron-like "M H D Mo W", ≥1min granularity), `compound` (recursive AND/OR).

**Cooldown**: per-trigger last-fired timestamp in RAM (process restart resets all). Trigger key = `<skill_name>:<sha8(trigger_dict)>` — editing a pattern resets cooldown via key change.

**Per-trigger kill switch**: persistent at `~/.codec/triggers_killed.json`. Toggled via PWA `POST /api/triggers/{key}/kill`. Killed triggers are skipped silently (no `trigger_blocked` audit emit, to avoid spam from popular killed patterns).

**Per-skill mute config** (post Step 6 hotfix): persistent at `~/.codec/triggers.json`. JSON file with `muted_skills` (permanent) and `muted_until` (ISO-8601 timestamp). Muted matches DO emit `trigger_muted` (warning), unlike kill which is silent. Default contents (when file missing): `{"muted_skills": ["clipboard_url_fetch"]}`. See `docs/PHASE2-STEP6-TRIGGER-MUTE.md`.

**Global kill switch**: `TRIGGERS_ENABLED=false` env var on `codec-observer` skips evaluation entirely.

**Step 6 ships ZERO triggers** — only the plumbing. Skills opt in one-by-one. Same trust model as plugins (user-curated local Python). At merge time, `evaluate()` iterates over zero registered triggers and exits in <1ms.

**4 audit events**: `trigger_evaluated` (info, on match), `trigger_fired` (info, on dispatch), `trigger_blocked` (warning, with `block_reason`), `trigger_muted` (warning, with `mute_source`).

**PWA endpoints**:
- `GET /api/triggers` — list all registered triggers + state
- `GET /api/triggers/{key}` — detail with cooldown_remaining
- `POST /api/triggers/{key}/kill` — toggle kill state

Implementation: `codec_triggers.py` (Trigger dataclass, validation, matchers, dispatch, mute config), `codec_skill_registry.py` extension (AST-extracts `SKILL_OBSERVATION_TRIGGER`), `codec_observer.py` integration (calls `evaluate()` after each poll, try/except so failures never break polling), `routes/triggers.py` (PWA endpoints).

### Shift Report (Phase 2 Step 7)

End-of-day summary of everything CODEC observed and accomplished. Single notification with `type="shift_report"` and a 5-section markdown body the PWA renders inline.

**Three trigger paths:**
- **Time-based** — wall clock matches `~/.codec/config.json:shift_report.daily_at_hour:daily_at_minute` (default 18:00 local). Detected by `codec-observer`'s daemon loop.
- **Idle-based** — `CGEventSourceSecondsSinceLastEventType` exceeds `shift_report.idle_minutes` (default 30). Same observer detection.
- **Manual** — user invokes via skill name (`"shift report"`, `"what did i do today"`, `"summarize my day"`) through chat / voice / MCP.

**Per-day dedup** at `~/.codec/shift_report_state.json`: time and idle paths fire at most once per local date; whichever wins suppresses the other. Manual invocations bypass dedup.

**5 sections, ~500-1500 words:**
1. Completed tasks — successful `tool_result` / `crew_complete` / `hook_fired` / `trigger_fired` counts + most-fired tools
2. Blocked / stuck moments — `stuck_warning` / `step_budget_exhausted` / `ask_user_question_timeout` / `trigger_blocked` events
3. Observed work patterns — app time-share from `observation_tick` metadata + count of persisted observer summaries
4. Pending decisions — open `type="question"` notifications + unreviewed skill proposals
5. Tomorrow's open threads — `crew_start` without matching `crew_complete`

**Inputs scanned:**
- Last 24h of `audit.log` + rotated logs (configurable via `lookback_hours`)
- `~/.codec/notifications.json` entries created in last 24h
- `~/.codec/observation_summaries/` (only persistent observer output, written by Step 5's `persist_for_shift_report()`)
- `~/.codec/skill_proposals/<today>/` markdown files

**2 audit events:** `shift_report_started` (info, on assembly begin) + `shift_report_completed` (info, with `extra.{trigger_kind, sections_included, word_count, audit_records_scanned, duration_ms}`). Both share a single `correlation_id` (multi-emit op per Step 1 §1.4).

**Optional auto-save:** if `shift_report.auto_save_path` is set in config (e.g. `~/Documents/CODEC Shift Reports`), the markdown body is also written to `<path>/YYYY-MM-DD.md`. Default `null` (notification-only).

**Kill switch:** `SHIFT_REPORT_ENABLED` env var (default true) — blocks all 3 trigger paths.

Implementation: `skills/shift_report.py` (assembly + rendering + notification post + per-day state), `codec_observer.py` extension (calls `_maybe_fire_shift_report(idle)` after each poll, time + idle detection inside).

### Plan + Permission Contract (Phase 3 Step 8)

Drop-a-project planning layer. User describes a project; Qwen-3.6 drafts a structured plan with explicit permission manifest (read paths, write paths, network domains, skills, destructive ops); user approves in PWA; grants persisted to `~/.codec/agents/<id>/grants.json` with `plan_hash` (sha256) for Step 9 tamper detection.

**Storage:**
- `~/.codec/agents/<id>/manifest.json` — id, title, status, plan_hash, timestamps
- `~/.codec/agents/<id>/plan.json` — schema:1, goals, checkpoints, permission manifest
- `~/.codec/agents/<id>/state.json` — current_checkpoint, retry_count
- `~/.codec/agents/<id>/grants.json` — written at approval, includes `auto_approved` subset (items pre-allowed via global allowlist)
- `~/.codec/agent_global_grants.json` — cross-agent allowlist (network domains / read paths / write paths / skills)

**Status state machine** (Step 8 only — Step 9 will extend):
`draft_pending → awaiting_approval → approved/rejected/revised → awaiting_approval (if revised)`. `plan_failed` is terminal-with-retry.

**Public API (`codec_agent_plan`):**
- `create_agent(title, description, registry=None)` → returns `agent_id`
- `approve_plan(agent_id)` → returns grants dict (re-validates skills against registry, computes plan_hash)
- `reject_plan(agent_id, reason="")`
- `revise_plan(agent_id, edited_plan_dict, registry=None)` → returns Plan
- `load_global_grants()` / `add_global_grant(kind, value)` / `remove_global_grant(kind, value)`

**PWA endpoints (`routes/agents.py`):** `POST /api/agents` (create + draft), `GET /api/agents` (list), `GET /api/agents/{id}` (detail), `POST /api/agents/{id}/approve`, `/reject`, `/revise`, `GET/POST/DELETE /api/agent_global_grants`.

**LLM:** local Qwen-3.6 only via `http://127.0.0.1:8090/v1/chat/completions` (per Q1 — no cloud fallback).

**Vague-description handling (Q3):** if LLM detects scope is too thin, agent posts up to 3 rounds of clarifying questions via `codec_ask_user.ask` before drafting. After 3 rounds without convergence: status=`plan_failed`, reason=`description_too_vague`.

**Kill switch:** `AGENT_PLANNING_ENABLED=false` blocks drafting (existing plans untouched).

**6 audit events** (paired correlation_id per Step 1 §1.4 contract): `agent_plan_drafted`, `_approved`, `_rejected`, `_revised`, `agent_global_grant_added`, `_removed`.

Implementation: `codec_agent_plan.py` (~640 LOC), `routes/agents.py` (~250 LOC of new endpoints).

### Background Execution + Permission Gate (Phase 3 Step 9)

`codec_agent_runner.py` is the runtime layer. PM2-managed daemon `codec-agent-runner` polls `~/.codec/agents/*/state.json` every 5s, picks up `status=approved` plans, executes their checkpoints autonomously via Qwen-3.6 ↔ skill loops. **Permission gate** enforces the manifest on every action; outside-manifest = `blocked_on_permission` + `ask_user` notification.

**Per-checkpoint loop** (inside `_execute_checkpoint`):
1. `_qwen_next_action()` returns either `Action(kind="skill_call", ...)` or `Action(kind="checkpoint_done")`
2. `permission_gate(action, agent_grants, global_grants)` raises `PermissionViolation` if outside manifest
3. If `action.is_destructive`: `_enforce_destructive_gate()` calls Step 3 §1.7 strict-consent (literal verb-match required, generic "yes" rejected)
4. `_run_skill()` dispatches via `codec_dispatch.run_skill` (Step 1+2 hooks fire automatically)
5. Append result to history, loop until `checkpoint_done` OR `step_budget` cap reached

**Resume policy (Q5):** after PM2 restart, daemon scans for `status=running` agents. Any with no live thread = crashed. Marks `crashed_resumed`, then transitions back to `running` and respawns. Worst case: one operation re-fires from the last atomic checkpoint save (idempotent skills are safe; destructive ops re-hit strict-consent).

**Multi-agent concurrency (Q6, Q8):** default `MAX_CONCURRENT=3`, env var `AGENT_RUNNER_MAX_CONCURRENT`. Blocked agents (any `blocked_*` state) **occupy a slot** — trade-off: 3 simultaneous overnight blocks = no new agent can start until you grant.

**Plan-hash tamper detection (Q13):** at run start, `_run_agent` verifies `manifest.plan_hash == sha256(plan.json)`. Mismatch → `aborted(plan_tampered)`.

**Public API (`codec_agent_runner`):**
- `_run_agent(agent_id)` — main per-agent thread function (called by daemon)
- `_daemon_one_tick()` — synchronous test-only wrapper
- `run_daemon()` — production entry point (PM2 `codec-agent-runner`)
- `permission_gate(action, agent_grants, global_grants)` — synchronous gate check
- Dataclasses: `Action`, `ConsentResult`
- Exceptions: `PermissionViolation`, `DestructiveOpRejected`, `StepBudgetExhausted`, `QwenUnavailableError`

**PWA endpoints (`routes/agents.py` Step 9 additions):** `POST /api/agents/{id}/abort`, `/pause`, `/resume`, `/grant` (body: `kind`, `value` — adds to per-agent grants, unblocks if `blocked_on_permission`).

**Service supervision:** PM2's built-in `autorestart: true` provides crash recovery (no separate heartbeat HTTP probe needed — `codec-agent-runner` is a daemon, not an HTTP service). PM2 max_memory_restart=256M and max_restarts=10.

**8 audit events** (paired correlation_id per `agent_started` operation envelope per Step 1 §1.4): `agent_started`, `agent_checkpoint_started`, `_completed`, `agent_paused`, `agent_resumed`, `agent_blocked_on_permission`, `agent_completed`, `agent_aborted`.

**Kill switches:**
- `AGENT_RUNNER_ENABLED=false` — daemon idles (still scans, never spawns threads)
- Per-agent: `POST /api/agents/{id}/abort` (atomic state write)
- Per-agent: `POST /api/agents/{id}/pause` / `/resume`

**Reuses (no new infrastructure):** Step 1 audit envelope · Step 2 plugin lifecycle hooks (every `run_skill` wrapped automatically) · Step 3 `ask_user` (outside-manifest pause) · Step 3 §1.7 strict-consent (universal floor for destructive ops) · Step 5 observer (passively records agent activity) · Step 7 shift_report (agent activity surfaces in daily summary).

Implementation: `codec_agent_runner.py` (~700 LOC), `routes/agents.py` (+120 for Step 9 endpoints), `ecosystem.config.js` (+22 for PM2 entry).

### Proactive Messaging + Project Mode (Phase 3 Step 10)

`codec_agent_messaging.py` is the agent ↔ user message dispatch. Backend complete; PWA HTML for the Project-mode dropdown + status pills deferred to Phase 3.5 alongside the proactive intelligence overlay.

**Per-message flow:**
1. `_run_agent` (Step 9) calls `post_message(agent_id, type, title, body, actions)` at 5 lifecycle points: agent start, checkpoint completion, blocked-on-permission, destructive-rejected abort, and final completion
2. `post_message` writes the record to `~/.codec/agents/<id>/messages.jsonl` (1:1 timeline preservation, never batched)
3. `post_message` then updates `~/.codec/notifications.json` — but only ONE banner per agent per `BATCH_WINDOW_SECONDS=60` (Q10). Within the window, the existing banner's `batch_count` increments and `title` updates to "N updates from <agent>: <latest>".
4. Audit emit `agent_message_sent` with `extra.batched` flag

**Message types** (frozen vocabulary): `agent_update` / `agent_blocked` / `agent_question` / `agent_done` / `agent_aborted` / `user_reply`.

**Silence kill-switch:** `is_silenced(agent_id)` reads `~/.codec/agent_silence.json`. When silenced: `post_message` still writes the timeline but skips notifications. Toggled via `POST /api/agents/{id}/silence {"silenced": bool}`. State is per-agent + persistent (atomic R/W).

**User reply pickup:** `POST /api/agents/{id}/messages {"body": "..."}` writes a `type=user_reply` line to `messages.jsonl` and emits `agent_message_received`. Step 9's `_run_agent` calls `get_unread_user_replies(agent_id, since_ts)` between checkpoints to feed replies into the next `_qwen_next_action` call as additional context.

**Auto-escalation from chat (Q11):** `_classify_chat_message(text)` calls Qwen-3.6 with a structured-JSON prompt and returns `(is_project: bool, estimated_checkpoints: int, reason: str)`. `_should_escalate_to_project(user_text, session_id)` is the 2-signal gate:
- Signal 1: classifier verdict `is_project=True`
- Signal 2: `estimated_checkpoints >= ESCALATE_CHECKPOINTS_THRESHOLD = 3`
- Plus 2 kill conditions: `AGENT_AUTO_ESCALATE_ENABLED=false` env var, OR `session_id` in `_autoescalate_silence_set` (in-memory, mutated under `_AUTOESCALATE_SILENCE_LOCK`)

After the user says "No" once for a session, that session_id is silenced for the rest of the conversation. Resets on new chat session (because `_autoescalate_silence_set` is in-memory).

**3 audit events:** `agent_message_sent`, `agent_message_received`, `agent_auto_escalated_from_chat`. `PHASE3_STEP10_EVENTS` frozenset exposed.

**Reuses:** `~/.codec/notifications.json` (existing PWA infrastructure since Phase 1) · Step 8 storage layout · Step 9 `_run_agent` emit sites · Qwen-3.6 (existing local LLM at `http://127.0.0.1:8090/v1/chat/completions`).

**Kill switches:**
- `AGENT_AUTO_ESCALATE_ENABLED=false` — chat handler never suggests project promotion
- Per-agent: `POST /api/agents/{id}/silence {"silenced": true}` — agent runs but no notification banners
- Per-conversation auto-escalation silence (Q11) — first "No" suppresses for that session

**PWA endpoints (Step 10):**
- `GET /api/agents/{id}/messages` — return messages.jsonl as a list (newest last)
- `POST /api/agents/{id}/messages {"body": "..."}` — user reply
- `POST /api/agents/{id}/silence {"silenced": bool}` — toggle silence

Implementation: `codec_agent_messaging.py` (~270 LOC), `routes/agents.py` (+61 for Step 10 endpoints), `codec_dashboard.py` (+125 for classifier + escalation gate), `codec_agent_runner.py` (+42 for `post_message` integration into 5 emit sites).

### Other known gaps (tracked for Phase 3.5 follow-on)
- **Project mode UI** — `codec_dashboard.html` does not yet have a mode-dropdown selector or agent status pills. Backend supports project dispatch via `POST /api/agents`; UI affordances deferred to Phase 3.5 alongside proactive overlay.
- **Proactive intelligence overlay** — observer-driven contextual nudges ("you've been on this Notion doc 30 min, want a summary?") deferred per Q12. Step 10 backend done; Phase 3.5 layers proactive on top.
- **`blocked_on_qwen` dedicated status** (Step 9 review C2) — Qwen unavailability currently maps to `blocked_on_permission` with reason. Phase 3.5 may introduce dedicated status with daemon-driven auto-resume.
- **Read-paths runtime enforcement** (Step 9 review M4) — `PermissionManifest.read_paths` declared but not gated; documented inline. Phase 3.5 may add `Action.reads_path` field + LLM prompt update.
- No formal teammate / sub-agent recursion — Crew is the only multi-agent primitive
- (Phase 3 backend complete after Step 10 ships; Phase 3.5 = UI + proactive + Step 9 review polish)

## 4. Skill system

Single-file Python modules with module-level metadata + `run(task, app="", ctx="") -> str`. See `skills/_template.py` for the canonical template.

Required: `SKILL_NAME`, `SKILL_TRIGGERS`, `SKILL_DESCRIPTION`. Optional: `SKILL_MCP_EXPOSE`.

### Discovery
`codec_skill_registry.SkillRegistry` (`codec_skill_registry.py:57-195`) AST-parses every `.py` file at startup to extract metadata — **no skill code runs unless the skill is actually called**. Broken skills don't break startup.

### Three execution paths

**A. Voice / wake-word:** `codec.py:wake_word_listener()` → `dispatch(text)` → `_dispatch_inner(task)` → `check_skill(task)` → `run_skill(skill, task, app)`.

**B. Dashboard chat:**
- Slash commands: `parse_slash` at `codec_slash_commands.py:55-90` runs first
- Pre-LLM hijack: `_try_skill(user_text)` at `codec_dashboard.py:2081-2110` — if a skill in `CHAT_SKILL_ALLOWLIST` matches and the message isn't conversational and has no file attachments, fire the skill and skip the LLM
- Post-LLM tag: LLM emits `[SKILL:name:query]`, regex at `codec_dashboard.py:2412-2419` parses, runs the skill, replaces the tag

**C. MCP (stdio + HTTP):** `codec_mcp.py:87-191` registers every skill with `SKILL_MCP_EXPOSE=True` as an MCP tool. 30s timeout, validated via `_validate_mcp_input` (task ≤ 5,000 chars, context ≤ 10,000), audited via `codec_audit.audit()`. HTTP transport adds OAuth 2.1 + a stricter `_HTTP_BLOCKED` list (`codec_config.py`): currently blocks `python_exec`, `terminal`, `process_manager`, `pm2_control`, `ax_control`.

### Adding a new skill
Drop a `.py` file in `~/.codec/skills/` (user) or `skills/` (built-in). Restart `codec-dashboard` and the main `open-codec` process. Hot-reload is not currently supported.

## 5. Memory contract

Single SQLite database at `~/.codec/memory.db`. Wrapper: `codec_memory.CodecMemory`.

### Schema
See `codec_memory.py:30-105` for live schema. Tables: `sessions`, `voice_chats`, `conversations`, `corrections`, `agent_goals`, `facts`, plus FTS5 virtual table `conversations_fts` kept in sync via triggers.

### Tiered retrieval (codec_memory_upgrade.py)
- **L0/L1 — `identity.txt`:** always-loaded boot payload (<200 tokens). Persistent identity + preferences. Zero query cost
- **L2 — recent rooms:** last N sessions from `conversations`, role-aware, single indexed query
- **L3 — deep FTS:** on-demand FTS5 over full history, sub-200ms typical

### Public API
```python
save(session_id, role, content, user_id="default") -> int
search(query, limit=10, user_id=None) -> list[dict]
search_recent(days=7, limit=50, user_id=None) -> list[dict]
get_context(query, n=5, user_id=None) -> str
get_sessions(limit=20, user_id=None) -> list[dict]
cleanup(retention_days=90) -> dict
rebuild_fts() -> int
close() -> None
```

### Facts table
Temporal KV with `valid_from`, `valid_until`, `superseded_by`. Supports `valid_at(timestamp)` queries — time-travel over user state.

### CCF (Conversational Context Fragmentation)
Rule-based compressor for memory writes that need shrinking. Entity abbreviation + filler stripping. Personal entity entries belong in `~/.codec/entity_map.json` (private), not in source.

### Injection points
| File:line | What gets injected |
|---|---|
| `codec.py:358-362` | Voice-mode prompt suffix (boot ctx + facts + memory) |
| `codec_dashboard.py:1827-1862` | Chat handler before LLM call |
| `codec_dashboard.py:1851-1886` | Same handler, separate channel |
| `codec_voice.py:288-320` | VAD speech-start preload |

`[MEMORY]` and `[RECENT MEMORY]` markers are explicitly stripped from agent output by `codec_identity.py`. Agents must not echo raw markers.

## 6. Audit + notification contract

### Audit log (`codec_audit.audit()`)
File: `~/.codec/audit.log`, newline-delimited JSON, daily rotation, 30-day retention. Append is thread-safe via `threading.Lock`.

**Schema status: UNIFIED (schema:1) — Phase 1 Step 1 implemented on the `phase1-step1-audit-unification` branch (HEAD 05f9b80).** The unified envelope is:

```jsonc
{
  "ts":          "2026-04-30T08:14:23.451+00:00",  // ISO8601 UTC, ms
  "schema":      1,                                  // schema version
  "event":       "tool_call|tool_result|crew_start|crew_complete|...",
  "source":      "codec-mcp-http|codec-heartbeat|codec-scheduler|...",
  "outcome":     "ok|error|timeout|validation|denied|warning",
  "tool":        "weather",
  "task_len":    42,
  "context_len": 128,
  "duration_ms": 120.5,
  "transport":   "stdio|http|local|voice|chat|crew|scheduler|heartbeat|dispatch|session",
  "agent":       "Writer | null",
  "level":       "debug|info|warning|error",
  "message":     "free-text, ≤ 500 chars",
  "error_type":  "TimeoutError | null",
  "error":       "short string ≤ 500 chars",
  "client_id":   "claude-ai | null",
  "extra":       { "correlation_id": "a3f7b2c8e409", "...": "..." }
}
```

`event=` is a **REQUIRED** kwarg on `audit()` — calling without it raises `TypeError` (per design Q4). `correlation_id` is **REQUIRED** for any operation that emits ≥2 audit lines (paired tool_call/tool_result, crew lifecycle, voice session, schedule run, OAuth chain — see design §1.4 for the full list). It rides under `extra.correlation_id` as a 12-char lowercase-hex string from `secrets.token_hex(6)`.

Pre-Phase-1 entries stay readable: `codec_audit_analyzer.py` already used `.get()` for every field, so legacy records (no `schema`, no `event`, naïve `ts`) bucket cleanly alongside unified ones. Migration plan: leave-as-is, age-out via the 30-day rotation. See `docs/PHASE1-STEP1-DESIGN.md` for the full contract.

### log_event (`codec_audit.log_event`)
Real adapter over `audit()` for lifecycle events (session start/end, scheduler tick, dispatch decision, heartbeat alert). Defined in `codec_audit.py`. Call sites in `codec_session.py`, `codec_scheduler.py`, `codec_dispatch.py`, `codec_heartbeat.py`, `codec_dashboard.py`, `codec.py`, `routes/auth.py`.

> **Status as of Phase 1 Step 1 (commit 05f9b80):** adapter wired through, correlation_id contract enforced. Prior to this branch, every `log_event` call was a silent no-op (the `try: from codec_audit import log_event` import was failing because the export didn't exist; the `except: def log_event(*a, **kw): pass` fallback ran instead). All 7 modules now import the real adapter and emit canonical event names per §1.2 of the design.

### Phase 1 Step 3 audit events (askuser + stuck + step budget)
Six new event names exported from `codec_audit.py` as module constants. All `outcome="warning"`, `level="warning"` (operational signals, not failures); all inherit `correlation_id` from the wrapping operation per §1.4.

| Event | Source | extra fields |
|---|---|---|
| `ask_user_question_emit` | `codec-ask-user` | `pending_question_id`, `question_preview`, `options`, `timeout_seconds`, `agent`, `crew_id`, `asked_from`, `consent_strict`, `destructive_verb` |
| `ask_user_question_answer` | `codec-ask-user` | `pending_question_id`, `answered_via` (pwa\|voice), `answer_len`, `elapsed_seconds` |
| `ask_user_question_timeout` | `codec-ask-user` | `pending_question_id`, `elapsed_seconds`, `timeout_seconds`, `reason` (`deadline`\|`ambiguous_consent`), `consent_rejection_count` (only on `ambiguous_consent`) |
| `stuck_warning` | `codec-agents` | `tool` (top-level), `repeat_count`, `agent` (in message line) |
| `stuck_escalated` | `codec-agents` | `tool` (top-level), `repeat_count`, `agent`, `action` (`ask_user`\|`abort`\|`warn_only`) |
| `step_budget_exhausted` | `codec-dashboard` | `budget_type` (`chat_turn`), `limit`, `actual`, `kind`, `correlation_id` |

The constants are also exposed as frozensets for analyzer / introspection: `ASKUSER_EVENTS`, `STUCK_EVENTS`, `STEP3_EVENTS`. `audit_report.py` ingests them as additive event types — no schema bump.

### Phase 2 Step 5 audit events (continuous observation)
Four new event names exported from `codec_audit.py` for the Continuous Observation Loop. All inherit `correlation_id` per §1.4 (the inject event reuses the wrapping chat/voice op's cid; the tick events generate per-poll cids).

| Event | Source | level | extra fields |
|---|---|---|---|
| `observation_tick` | `codec-observer` | info | METADATA-ONLY: `active_app`, `active_title_len`, `ocr_chars`, `ocr_skipped`, `clipboard_changed`, `clipboard_kind`, `recent_files_count`, `idle_seconds`, `cadence_used_s`, `buffer_depth`, `poll_duration_ms` |
| `observation_tick_slow` | `codec-observer` | warning | Same as `observation_tick` — emitted instead when `poll_duration_ms > poll_slow_threshold_ms` (default 150ms). Q5.5 flag for visibility, no behavior change. |
| `observation_summary_injected` | `codec-observer` | info | `tokens_used`, `injection_reason` (`always_local`\|`possessive_match`\|`continuation_match`\|`skill_flag`), `buffer_entries_summarized`. `transport` is top-level (reserved). |
| `observer_buffer_inspected` | `codec-dashboard` | info | `client_ip`, `buffer_entries_returned`. Q5.6 PWA `?debug=1` audit. |

`PHASE2_STEP5_EVENTS` frozenset exposed for analyzer breakdown. `observation_tick` is METADATA-ONLY by design — no titles, no OCR text, no clipboard content, no file paths leak to `~/.codec/audit.log`.

### Phase 2 Step 6 audit events (Trigger System)
Four event names. `trigger_evaluated` fires only when a pattern matches (pre-cooldown, pre-consent — silent on no-match to avoid audit spam). `trigger_fired` is the actual dispatch. `trigger_blocked` fires for any non-firing reason except `killed` (silent). `trigger_muted` fires when an otherwise-eligible match is suppressed by the runtime mute config (`~/.codec/triggers.json` — see `docs/PHASE2-STEP6-TRIGGER-MUTE.md`). All inherit the wrapping observer poll's `correlation_id`.

| Event | Source | level | extra fields |
|---|---|---|---|
| `trigger_evaluated` | `codec-triggers` | info | `trigger_key`, `skill_name`, `trigger_type`, `match_summary` |
| `trigger_fired` | `codec-triggers` | info | `trigger_key`, `skill_name`, `trigger_type`, `dispatch_correlation_id` |
| `trigger_blocked` | `codec-triggers` | warning | `trigger_key`, `skill_name`, `trigger_type`, `block_reason` (`cooldown` \| `user_skipped` \| `confirmation_timeout` \| `ambiguous_consent`). NOTE: `killed` reason is intentionally NOT emitted to keep audit clean. |
| `trigger_muted` | `codec-triggers` | warning | `trigger_key`, `skill_name`, `trigger_type`, `mute_source` (`muted_skills` \| `muted_until`), `muted_until` (only when source=`muted_until`) |

`PHASE2_STEP6_EVENTS` frozenset exposed.

### Phase 2 Step 7 audit events (Shift Report)
Two new event names. Both `level="info"` (operational). `shift_report_started` opens the assembly operation, `shift_report_completed` closes it with summary stats. Both share a single `correlation_id` (multi-emit op per Step 1 §1.4 — the wrapping operation envelope).

| Event | Source | level | extra fields |
|---|---|---|---|
| `shift_report_started` | `codec-shift-report` | info | `trigger_kind` (`time` \| `idle` \| `manual`) |
| `shift_report_completed` | `codec-shift-report` | info | `trigger_kind`, `sections_included` (0-5), `word_count`, `audit_records_scanned`, `notifications_scanned`, `observer_summaries_used`. `duration_ms` is top-level. |

`PHASE2_STEP7_EVENTS` frozenset exposed.

#### Phase 3 Step 8 events — agent planning lifecycle

Six event names. All `level="info"` except `_rejected` (warning). Each is a single-emit operation; the `_drafted → _approved` (or `_drafted → _rejected`) sequence shares no implicit correlation_id since they're independent user-driven transitions (each gets a fresh cid generated at emit time).

| Event | Source | level | extra fields |
|---|---|---|---|
| `agent_plan_drafted` | `codec-agent-plan` | info | `agent_id`, `checkpoint_count`, `estimated_duration_minutes`, `skills_count`, `domains_count` |
| `agent_plan_approved` | `codec-agent-plan` | info | `agent_id`, `plan_hash` (sha256 hex), `checkpoint_count`, `skills_count`, `domains_count` |
| `agent_plan_rejected` | `codec-agent-plan` | warning | `agent_id`, `reason` (truncated to 200 chars) |
| `agent_plan_revised` | `codec-agent-plan` | info | `agent_id`, `checkpoint_count` |
| `agent_global_grant_added` | `codec-agent-plan` | info | `kind` (`network_domains` \| `read_paths` \| `write_paths` \| `skills`), `value` |
| `agent_global_grant_removed` | `codec-agent-plan` | info | `kind`, `value` |

`PHASE3_STEP8_EVENTS` frozenset exposed.

#### Phase 3 Step 9 events — agent runtime lifecycle

Eight event names. `agent_started` opens the per-agent operation envelope; subsequent events all share that single correlation_id (multi-emit op per Step 1 §1.4). `agent_blocked_on_permission` and `agent_paused` are warning level; `agent_aborted` is error or warning depending on cause; the rest are info.

| Event | Source | level | extra fields |
|---|---|---|---|
| `agent_started` | `codec-agent-runner` | info | `agent_id`, `checkpoint_count`, `starting_at` (resume idx) |
| `agent_checkpoint_started` | `codec-agent-runner` | info | `agent_id`, `checkpoint_id`, `checkpoint_idx` |
| `agent_checkpoint_completed` | `codec-agent-runner` | info | `agent_id`, `checkpoint_id`, `checkpoint_idx`, `steps_used` |
| `agent_paused` | `codec-agent-runner` | warning | `agent_id`, `checkpoint_id`, `reason` |
| `agent_resumed` | `codec-agent-runner` | info | `agent_id`, `recovery` (true=PM2-restart) |
| `agent_blocked_on_permission` | `codec-agent-runner` | warning | `agent_id`, `checkpoint_id`, `reason`, `needed` |
| `agent_completed` | `codec-agent-runner` | info | `agent_id`, `total_steps` |
| `agent_aborted` | `codec-agent-runner` | error\|warning | `agent_id`, `reason` |

`PHASE3_STEP9_EVENTS` frozenset exposed.

#### Phase 3 Step 10 events — agent ↔ user messaging

Three event names, all info-level. `agent_message_sent` and `agent_message_received` thread the per-agent `correlation_id` from `_run_agent`'s envelope when called from there; `agent_auto_escalated_from_chat` is independent (chat-handler invocation, no agent yet).

| Event | Source | level | extra fields |
|---|---|---|---|
| `agent_message_sent` | `codec-agent-messaging` | info | `agent_id`, `type` (one of `agent_update` \| `agent_blocked` \| `agent_question` \| `agent_done` \| `agent_aborted` \| `user_reply`), `batched` (bool) |
| `agent_message_received` | `codec-agent-messaging` | info | `agent_id`, `body_len` |
| `agent_auto_escalated_from_chat` | `codec-dashboard` | info | `session_id`, `estimated_checkpoints`, `verdict`, `silenced` (bool, true if subsequent No) |

`PHASE3_STEP10_EVENTS` frozenset exposed.

### Notifications (`~/.codec/notifications.json`)
Four sources can produce notifications: scheduler (crew completion), heartbeat (threshold alert), autopilot (ambient trigger), and Phase 1 Step 3's AskUserQuestion (`type="question"`). All write through `routes/_shared.py:51-127` except AskUserQuestion which writes via `codec_ask_user._write_question_notification`.

Schema:
```json
{
  "id": "notif_<hex>",
  "type": "task_report|alert|status|question",
  "title": "...",
  "body": "markdown",
  "status": "success|warning|error",
  "created": "ISO8601",
  "read": false,
  "schedule_id": "sched_<id> | null",
  "doc_url": "https://... | null",
  "pending_question_id": "q_<8hex> | null",
  "options": ["..."] | null,
  "agent": "Writer | null",
  "deadline": "ISO8601 | null",
  "consent_strict": false
}
```

`type="question"` adds `pending_question_id`, `options`, `agent`, `deadline`, `consent_strict`. The PWA renders an inline answer panel when these fields are present (see `codec_dashboard.html` AskUserQuestion panel). Reply path: `POST /api/agents/answer/{pending_question_id}` (defined in `routes/agents.py`).

API endpoints in `codec_dashboard.py`: `GET /api/notifications`, `GET /api/notifications/count`, `POST /api/notifications/read-all`, `POST /api/notifications/{id}/read`, `DELETE /api/notifications/{id}`. Frontend polls `/api/notifications/count` every ~30s, and the inline AskUserQuestion panel polls `/api/agents/pending_questions` every 8s.

### Pending questions (`~/.codec/pending_questions.json`)
Canonical state file for AskUserQuestion. Atomic write via tmp+rename. Schema:
```json
{
  "schema": 1,
  "pending_questions": [
    {
      "id": "q_<8hex>",
      "operation_id": "<correlation_id>",
      "correlation_id": "<12hex>",
      "agent": "Writer | null",
      "crew_id": "deep_research | null",
      "question": "...",
      "options": ["yes","no"] | null,
      "asked_at": "ISO8601",
      "deadline": "ISO8601",
      "timeout_seconds": 600,
      "status": "pending|answered|timed_out",
      "answered_at": "ISO8601 | null",
      "answered_via": "pwa|voice | null",
      "answer": "...",
      "asked_from": "chat|voice|crew|mcp",
      "consent_strict": false,
      "destructive_verb": "delete | null",
      "timeout_reason": "deadline|ambiguous_consent | null"
    }
  ]
}
```

## 7. Sandbox + safety boundaries

### Files & directories
- `~/.codec/` — all user state (config, memory, audit, schedules, notifications, agents, skills, plugins, proposals)
- `~/.codec/plugins/*.py` — user-authored lifecycle hook plugins (Phase 1 Step 2; see §3 *Plugin lifecycle hooks*). Same trust model as `~/.codec/skills/`: local Python files curated by the user, no marketplace, no auto-install, no inter-plugin sandbox. Files starting with `_` are skipped.
- `~/.codec/pending_questions.json` — Phase 1 Step 3 canonical state for AskUserQuestion (atomic write; never edit by hand). The reply path goes through `POST /api/agents/answer/{qid}` and the voice handler — both call `codec_ask_user.submit_answer()` which writes the answered status atomically. Direct edits race the in-flight `threading.Event` waiters and break agents.
- `~/.codec/voice_session.json` — Phase 1 Step 3 voice-session active-marker. Touched by `VoicePipeline.run` start, removed in finally. `codec_ask_user` reads this to decide whether voice should announce and listen for an answer or defer to PWA only.
- File operations from agent tools go through `codec_sandbox` (path validation against blocklist, size caps)
- Dangerous code patterns detected by `codec_config.is_dangerous_skill_code` before any skill is staged from `codec_self_improve.py` proposals

### MCP HTTP transport blocklist
`codec_config._HTTP_BLOCKED`: `python_exec`, `terminal`, `process_manager`, `pm2_control`, `ax_control`. These skills are NEVER exposed over HTTP MCP. They remain available locally (voice, chat) and over stdio MCP only.

### Skill creation flow — review-and-approve only (Phase 1 Wave 1, PR-1B — closes D-2 + D-3)

Skill creation is exclusively via the review-and-approve flow:

  `POST /api/skill/review`   →  stages code for human review (no disk write)
  `POST /api/skill/approve`  →  writes to disk after explicit operator approval; runs `is_dangerous_skill_code` as the write-time gate

The legacy direct-write endpoints `/api/save_skill` and `/api/forge` were **removed in PR-1B**. Both were CRITICAL RCE-enabling paths per `docs/audits/PHASE-1-SECURITY.md`:
- `/api/save_skill` (**D-3**) wrote user/LLM-supplied code straight to `<skills_dir>/<name>.py` after only a substring blocker.
- `/api/forge` (**D-2**) fetched arbitrary URLs (SSRF), passed the response to the LLM, and wrote the LLM's output directly to disk.

The Skill Forge UI in `codec_vibe.html` (modal, toolbar buttons, JS handlers) was removed alongside. The URL-fetch capability is intentionally dropped — anyone wanting to import code from a URL now pastes the source into the editor and goes through the review-and-approve flow like any other skill.

### Skill load-time safety gate (Phase 1 Wave 1, PR-1A — closes D-1)

`SkillRegistry.load` (`codec_skill_registry.py`) runs a two-stage check on every skill load — BEFORE `spec.loader.exec_module(mod)` — so a malicious `.py` file dropped in `~/.codec/skills/` cannot execute regardless of how it reached disk:

1. **Trusted manifest.** `<skills_dir>/.manifest.json` (committed under `<repo>/skills/.manifest.json`, generated by `tools/generate_skill_manifest.py`) maps each approved built-in skill filename to its `sha256(file-bytes)`. If the on-disk file's hash matches an entry, the skill is hash-pinned-trusted and loads. This is what lets legitimately-dangerous built-ins (`calculator`, `system`, `file_write`, `pilot`, ...) continue to work — their source IS dangerous, but their hash IS approved.
2. **AST safety gate.** Files NOT in the manifest (or in skill directories with no manifest at all, e.g. `~/.codec/skills/`) run through `codec_config.is_dangerous_skill_code`. Any dangerous pattern → refuse → emit `skill_load_blocked` audit event → `load()` returns None. Fail-safe on any error inside the check (validator raised, file unreadable, UTF-8 decode failure).

**Implication for contributors:** any legitimate edit to a built-in skill file (or addition of a new built-in) requires regenerating the manifest. Run `python3 tools/generate_skill_manifest.py --write` after the source edit, commit `skills/.manifest.json` alongside. CI verifies no drift via `--check`.

This is the chokepoint defense against the four enabling-path findings (D-2 `/api/forge`, D-3 `/api/save_skill`, D-4 `file_write`, D-5 `permission_gate`) per `docs/audits/PHASE-1-SECURITY.md`. Even if any of those paths drop a malicious file, the file's hash won't match the manifest → AST check runs → refused.

### Skill self-improvement
`codec_self_improve.py` drafts skill proposals into `~/.codec/skill_proposals/YYYY-MM-DD/<name>.md`. **Never auto-deploys.** Human review required before promoting to `~/.codec/skills/`.

### Dangerous command guard (voice/chat)
`codec.py` maintains a hardcoded blocklist (`rm -rf`, `sudo`, `shutdown`, `killall`, `dd`, `diskutil erase`, `curl|bash`, etc.). Flagged commands prompt for explicit confirmation. Every flagged command is logged.

### 8-step execution cap
No agent task chains more than 8 steps without breaking. Hardcoded in `Crew.max_steps`.

## 8. How to add an agent

### Adding a built-in crew
1. Write a builder function in `codec_agents.py` returning a `Crew` instance
2. Register in `CREW_REGISTRY` at `codec_agents.py:1361-1374` with name + builder + description
3. (Optional) Add a voice trigger to `_CREW_TRIGGERS` in `codec_voice.py:682-721`
4. Test via `POST /api/agents/run` with `{"crew": "<name>", ...kwargs}`

### Adding a custom agent (user-runtime, not in code)
1. `POST /api/agents/custom/save` with `{name, role, tools, ...}` — persisted to `~/.codec/agents/<slug>.json`
2. Run via `POST /api/agents/run` with the custom name
3. List via `GET /api/agents/custom/list`

### Adding a skill
1. Write `<name>.py` in `~/.codec/skills/` (user) or `skills/` (built-in)
2. Required exports: `SKILL_NAME`, `SKILL_TRIGGERS`, `SKILL_DESCRIPTION`, `def run(task, app="", ctx="") -> str`
3. Optional: `SKILL_MCP_EXPOSE = True/False`
4. Restart `codec-dashboard` and main `open-codec` process (no hot-reload yet)

## 9. Conventions

### Code
- Python 3.11+, no type stubs required but type hints encouraged
- No new dependencies without explicit approval — local-first means small dependency surface
- Prefer stdlib (`sqlite3`, `json`, `pathlib`, `subprocess`) over third-party
- Module names: `codec_<area>.py` for engine modules, `routes/<area>.py` for HTTP routes

### Commits
- One concern per commit
- Audit-log schema changes go in their own commit and update this file's §6
- New crews go in their own commit and update `CREW_REGISTRY` and §3

### Testing
- `pytest` from repo root. **600+ tests collected** (live count via `pytest --collect-only`); all must pass before merge except known pre-existing failures documented in `docs/known-issues.md`
- Skill changes: add a smoke test that imports and calls `run("test")` with empty args
- Audit-log changes: add a parser round-trip test

### Personal data
- **Never commit user-specific data** (name, location, employer, language preference, custom rules)
- Personal context lives in private files: `~/.codec/config.json` (settings), `~/.codec/prompt_overrides.json` (system-prompt addons via `/api/prompts`), `~/.codec/entity_map.json` (CCF abbreviations)
- The repo is public; assume any string in a `.py` file is world-readable

## 10. Don't-touch zones (require explicit user confirmation)

These zones break running infrastructure if changed without coordination. NEVER modify without surfacing the change to the user first:

- `~/.codec/memory.db` schema — migrations require backup + rollback plan
- `codec_audit.py` audit envelope schema — affects 30 days of logs
- `_HTTP_BLOCKED` list in `codec_config.py` — security boundary
- PM2 process names (real list: `open-codec`, `codec-dashboard`, `codec-autopilot`, `codec-heartbeat`, `codec-dictate`, `codec-hotkey`, `codec-imessage`, `codec-mcp-http`, `codec-overlay`, `codec-telegram`, `codec-watchdog`, plus support: `kokoro-82m`, `qwen3.6`, `whisper-stt`, `cloudflared`, `ava-license`, `ava-proxy`) — break supervision if renamed
- Port assignments in `setup_codec.py` and `~/.codec/config.json` — break multi-machine LAN setup
- Cloudflare tunnel hostnames (configured in `~/.cloudflared/config.yml` as `codec.<your-domain>`) — break PWA access from phone
- `codec_identity.py` operating principles — these are user-facing identity, change with care
- `codec_oauth_provider.py` `ACCESS_TOKEN_TTL` / `REFRESH_TOKEN_TTL` — currently 30d / 90d. Shortening these breaks live claude.ai MCP connections mid-week
- `~/.codec/oauth_state.json` — clearing this invalidates ALL claude.ai connections; touch only after explicit user OK + `pm2 restart codec-mcp-http`
- `~/.codec/pending_questions.json` (Phase 1 Step 3) — direct edits race in-flight `threading.Event` waiters; agents will hang or skip answers. Use `POST /api/agents/answer/{qid}` or `codec_ask_user.submit_answer()` instead.
- `~/.codec/voice_session.json` (Phase 1 Step 3) — voice-session active-marker; `VoicePipeline.run` owns its lifecycle.
- Phase 1 Step 3 feature-flag env vars — `ASKUSER_ENABLED`, `STUCK_DETECTION_ENABLED`, `STEP_BUDGET_ENABLED` (default true). Set to `false` to disable a feature in production; tests use these to bypass during isolated unit testing. Don't toggle them globally without coordinating — they alter agent behavior across all paths (chat / voice / crew / MCP).
- `~/.codec/config.json:ask_user.{timeout_seconds, consent_strict_max_attempts}` and `:stuck.{window, repeat_threshold, escalation_action}` and `:step_budget.{chat, voice}` — Phase 1 Step 3 tunables. Bumping `step_budget.chat` to 8 or 10 is the documented "tune up before tuning out" pressure-relief valve, but don't touch the others without referencing the design doc rationale (§1.2 Q1, §1.7, §2.3, §3.2).
- `~/.codec/observation_summaries/` (Phase 2 Step 5) — populated only by `codec_observer.persist_for_shift_report()`. Do not add files manually; the Step 7 shift-report assembly relies on the time-stamped naming convention. Safe to delete the whole directory if you want to wipe the persisted history.
- `OBSERVER_ENABLED` env var (Phase 2 Step 5, default `true`). Setting `false` disables both the polling loop AND the prompt injection. No separate injection kill switch — the buffer is always populated when enabled, only injection is gated.
- `~/.codec/config.json:observer.{...}` — Phase 2 Step 5 tunables (cadence_active_s, cadence_idle_s, idle_threshold_s, buffer_depth_min, ocr_enabled, ocr_timeout_ms, ocr_retry_timeout_ms, reset_on_long_idle, reset_idle_threshold_s, summary_max_tokens, poll_slow_threshold_ms, stop_nouns). Don't tune the cadences below 30s without considering OCR cost. `ocr_enabled: false` is the recommended baseline if Screen Recording permissions aren't granted to the PM2 child process — bypasses screencapture entirely (see incident `INCIDENT-2026-05-01-spurious-skill-fires.md` and the Step 5 hotfix in PR #10).
- `~/.codec/triggers_killed.json` (Phase 2 Step 6) — persistent per-trigger kill state. Atomic-write owned by `codec_triggers.set_killed()`; do not edit by hand (the trigger keys are content-hashed and need to match what `discover_triggers()` computes). Use the PWA `POST /api/triggers/{key}/kill` endpoint instead.
- `TRIGGERS_ENABLED` env var (Phase 2 Step 6, default `true`). Setting `false` skips trigger evaluation entirely; observer keeps polling. Per-trigger kill switch via PWA is the finer knob.
- `SKILL_OBSERVATION_TRIGGER` declaration in skill files (Phase 2 Step 6) — adding one to a skill makes it auto-fire on observer signals. **High-impact change** — review the cooldown / require_confirmation / destructive flags carefully. Same trust model as plugins.
- `~/.codec/triggers.json` (Phase 2 Step 6 mute config) — user-facing soft-disable for noisy triggers. Schema: `{"muted_skills": [...], "muted_until": {skill: ISO8601}}`. Cached in `_MUTE_CACHE`; hand-edits require service restart OR `codec_triggers._refresh_mute_cache()`. Default contents (when file missing): `{"muted_skills": ["clipboard_url_fetch"]}` — preserves PR #38's old behavior. **Writing the file replaces defaults entirely; no merge.** See `docs/PHASE2-STEP6-TRIGGER-MUTE.md`.
- `_DEFAULT_MUTE_CONFIG` in `codec_triggers.py` (Phase 2 Step 6 mute config) — hardcoded fallback when `~/.codec/triggers.json` is missing. Touching this changes the on-fresh-install behavior — coordinate with the user before adding/removing skills from the default list.
- `~/.codec/shift_report_state.json` (Phase 2 Step 7) — per-day fire dedup state (`last_fired_date`, `last_fired_at`, `last_trigger_kind`). Owned by `skills/shift_report.mark_fired_today()`. Safe to delete to force-fire today again; do not hand-edit (atomic-write contract).
- `SHIFT_REPORT_ENABLED` env var (Phase 2 Step 7, default `true`). False blocks all three trigger paths (time / idle / manual).
- `~/.codec/config.json:shift_report.{daily_at_hour, daily_at_minute, idle_minutes, lookback_hours, auto_save_path}` — Phase 2 Step 7 tunables. `auto_save_path` is `null` by default (notification-only); set to a directory path to also write `YYYY-MM-DD.md` files.
- `codec_agent_plan.py` (Phase 3 Step 8) — Plan + Permission Contract module. Don't refactor without re-running the PHASE3-STEP8 design gate. The dataclasses (`Plan`, `Checkpoint`, `PermissionManifest`) and `plan_from_dict()` lock the on-disk schema; bumping `PLAN_SCHEMA_VERSION` requires migration logic.
- `routes/agents.py` Phase 3 Step 8 endpoints (`/api/agents`, `/api/agent_global_grants`) — don't change endpoint shapes without bumping API version. PWA reads these directly.
- `~/.codec/agents/<id>/` — per-agent runtime state. Modify only via the documented public API (`codec_agent_plan.create_agent`, `approve_plan`, `reject_plan`, `revise_plan`). Direct edits to `plan.json` after approval will fail Step 9's plan-hash tamper check.
- `~/.codec/agent_global_grants.json` (Phase 3 Step 8) — cross-agent allowlist. Modify only via `add_global_grant()` / `remove_global_grant()` or the `/api/agent_global_grants` endpoints. Atomic-write contract.
- `AGENT_PLANNING_ENABLED` env var (Phase 3 Step 8, default `true`). Setting `false` blocks plan drafting; existing approved plans are untouched.
- `MAX_CLARIFYING_ROUNDS` constant in `codec_agent_plan.py` (default 3) — caps the vague-description clarifying loop. Tune up cautiously; users can get stuck in long Q&A loops if too high.
- `codec_agent_runner.py` (Phase 3 Step 9) — runtime daemon. Don't refactor without re-running the PHASE3-STEP9 design gate. The `MAX_CONCURRENT` constant and `_active_threads` global are mutated under `_threads_lock`; no other code may touch them.
- `_VALID_TRANSITIONS` in `codec_agent_plan.py` (Phase 3 Step 9 extension) — state machine map. Never remove a transition; only add. Step 10 will extend with paused-with-message states.
- `AGENT_RUNNER_ENABLED` and `AGENT_RUNNER_MAX_CONCURRENT` env vars (Phase 3 Step 9, defaults `true` / `3`). `AGENT_RUNNER_ENABLED=false` idles the daemon.
- PM2 `codec-agent-runner` service (Phase 3 Step 9). Stop/restart through PM2; `autorestart: true` provides crash recovery automatically. Don't add HTTP heartbeat probes — daemon doesn't expose HTTP by design.
- `~/.codec/agents/<id>/state.json` after Step 9 deploy — read/written by `codec_agent_runner._run_agent` mid-checkpoint. Manual edits while an agent is `running` will desync the resume mechanism. To pause an agent: `POST /api/agents/{id}/pause`.
- `codec_agent_messaging.py` (Phase 3 Step 10) — agent ↔ user message dispatch + 60s batching. Don't refactor without re-running PHASE3-STEP10 design gate. The `BATCH_WINDOW_SECONDS=60` constant is the user-facing batching contract; tune cautiously. The `MAX_MESSAGE_BODY_LEN=5000` constant caps body size in `to_dict()` — never raise without considering audit-log impact.
- `~/.codec/agents/<id>/messages.jsonl` (Phase 3 Step 10) — append-only message log. Never edit directly; use `post_message` / `post_user_reply` / endpoints. Bare-edits during a running agent will desync the daemon's `since_ts` read position for user replies.
- `~/.codec/agent_silence.json` (Phase 3 Step 10) — per-agent silence state. Modify only via `set_silenced` or `POST /api/agents/{id}/silence`. Atomic-write contract.
- `_autoescalate_silence_set` global in `codec_dashboard.py` (Phase 3 Step 10) — in-memory per-session silence state for chat → project escalation. Mutated under `_AUTOESCALATE_SILENCE_LOCK` (`threading.Lock()`); never touch from outside. Resets on dashboard restart by design.
- `AGENT_AUTO_ESCALATE_ENABLED` env var (Phase 3 Step 10, default `true`). Setting `false` disables the chat-handler "Promote to Project mode?" prompt entirely.
- `ESCALATE_CHECKPOINTS_THRESHOLD` constant in `codec_dashboard.py` (default 3). Lowering to 1-2 will prompt-escalate even single-skill asks; raising past 5 effectively disables auto-escalation.

## 11. Working with this repo as a coding agent

### Before any code change
1. Read this file fully
2. Read the file you're about to modify
3. Read any test file that imports the file you're about to modify
4. Check `docs/` for any relevant design specs

### Design-first workflow
For any non-trivial change (>50 lines or touching >1 module):
1. Write a design doc to `docs/<change-name>-DESIGN.md` covering: what, why, schema/API changes, migration plan, test plan, rollback plan
2. Stop. Wait for user approval
3. Only after approval: implement, with tests passing after each file change

### When you're stuck
- If a tool call returns unexpected data: check the actual schema in this file, don't guess
- If you need user input mid-task: stop and ask in chat. Do not invent
- If you find a bug outside your task scope: log it in `docs/known-issues.md`, don't fix it

### What you should never do
- Auto-deploy skill proposals from `~/.codec/skill_proposals/` — these are draft-only
- Modify `~/.codec/memory.db` without a backup
- Change audit envelope schema in §6 without updating this file
- Add cloud dependencies that send user data anywhere by default
- Add inbound channels (Telegram bot polling, Discord webhook listener, etc.) — inbound stays PWA-only
- Commit user-specific data — see §9 *Personal data*

---

**Last updated:** 2026-04-30
**Repo:** github.com/AVADSA25/codec
**Maintainer:** AVA Digital LLC
