# AGENTS.md

> Front-door context for any AI coding agent (Claude Code, OpenClaw, Hermes Agent, Cursor, etc.) working on the CODEC repository. Read this fully before making code changes. Update it when architecture changes.

## Reference
Architecture, schemas, and event tables: docs/codec-architecture.md
Current gaps and in-progress work: docs/codec-status.md
Read these when the task needs them. Do not load them by default.

## 1. Identity & purpose

**CODEC** is the open-source engine codename for the **Sovereign AI Workstation** — a voice-controlled, local-first AI agent that runs entirely on the user's macOS hardware. Brand naming follows the iPhone/Darwin pattern: Sovereign AI Workstation is the product, CODEC is the engine. Code paths, `~/.codec/` config, `codec_*.py` modules, and PM2 process names all use CODEC.

**Operating principles** (live in `codec_identity.py:30-65`, do not duplicate here — read the file):
- Local-first: zero data leaves the user's machine unless they explicitly route through a cloud LLM provider
- Inbound is private (PWA over Cloudflare Zero Trust only). Outbound (Gmail, iMessage, Twilio, future WhatsApp/Discord) goes through user-owned bridges
- Every action that touches the user's filesystem, processes, or external services is auditable via `~/.codec/audit.log`

## 3. Agent + Crew runtime

CODEC has its own minimalist multi-agent runtime in `codec_agents.py`. **Zero dependency on CrewAI or LangChain** — it's self-contained, only depends on `requests` and `codec_skill_registry`.

### Built-in agent tools
Defined in `codec_agents.py:113-307`: `web_search`, `web_fetch`, `file_read`, `file_write`, `google_docs_create`, `shell`. Plus every registered skill auto-becomes a tool via `_make_lazy_fn` + `load_skill_tools()` (`codec_agents.py:279-307`).

### Custom agents (user-defined)
Stored as JSON files at `~/.codec/agents/*.json`, keyed by slugified name. CRUD via `routes/agents.py`. Crew-run state lives in the `_agent_jobs` dict; since UI P3.8 it is also written to `~/.codec/agent_jobs.json` (0600, atomic, the newest 50 runs) when a run starts, ends or is stopped, and read back on first use, so the list survives a `codec-dashboard` restart. A run that was still going when the dashboard stopped comes back as `interrupted` (its thread is gone; it is not resumed). `GET /api/agents/jobs` lists them; Tasks > Activity shows them with the Project-mode agents and the global grants.

### Voice-side dispatch
`codec_voice.py:682-721` — `_CREW_TRIGGERS` maps spoken phrases to crew names. `dispatch_crew_from_voice(user_text)` at `codec_voice.py:723-755` does the matching, builds args, runs the crew, streams progress via TTS.

### Plugin lifecycle hooks (Phase 1 Step 2)

CODEC supports user-authored Python plugins at `~/.codec/plugins/*.py` that register lifecycle hooks around skill / tool execution. Hooks fire identically across all five execution paths (crew, voice WebSocket, voice wake-word + chat pre-LLM hijack + chat post-LLM tag via `codec_dispatch.run_skill`, MCP stdio + HTTP).

**Lifecycle:** `pre_tool`, `post_tool`, `on_error`, `on_operation_start`, `on_operation_end`. See `docs/PHASE1-STEP2-DESIGN.md` §1 for the full table. All sync, all observe-or-mutate (never async).

**Discovery:** AST parse at startup (mirrors `codec_skill_registry`). A plugin file declares any subset of the five lifecycle functions plus optional `PLUGIN_NAME` / `PLUGIN_DESCRIPTION` / `PLUGIN_PRIORITY` (default 100; lower runs first) / `PLUGIN_TOOL_FILTER` (exact list of tool names; `None` = all tools). Module imports deferred to first hook fire — broken plugins don't break startup.

**Veto:** `pre_tool` may return `HookVeto(reason="…")` to abort the tool call. The wrapper emits a `tool_vetoed` audit event and the caller receives the deterministic string `"Skill 'X' was vetoed by plugin 'Y': <reason>"`. Crew behaviour: agent's ReAct loop sees the veto string as the tool result and decides on its next move (no retry, no fail-the-crew).

**Mutation contract:** `pre_tool` returns `{"task": str, "context": str}` (either or both keys) to mutate; identity fields (`tool_name`, `transport`, `agent`, `correlation_id`, `client_id`, `operation_id`) are immutable and dropped with a warning. `post_tool` returns `str` to replace the result. `on_error` / `on_operation_*` are observe-only.

**Audit:** every successful hook fire emits `hook_fired`; plugin-internal exceptions emit `hook_error` with `level="warning"` (operation still succeeded). `correlation_id` inherits from the wrapping operation per Step 1 §1.4 — never regenerated.

**Trust model (PR-2F, closes D-18):** local Python files curated by the user, plus a SHA-256 allowlist gate + daemon-thread timeout. Same chokepoint pattern as PR-1A's `skills/.manifest.json` for skills:

- **`~/.codec/plugins.allowlist`** (JSON, 0600, atomic-write) keys plugins by basename → `{sha256, approved_at, approved_by}`. `PluginRegistry.get_fn` computes the file's current SHA-256 and refuses to call `exec_module` if the hash isn't in the allowlist OR has changed. Refusal → `plugin_load_blocked` audit (`reason ∈ {not_in_allowlist, hash_mismatch, file_unreadable}`, optional `detail` field with the AST result from `is_dangerous_skill_code` for forensic clarity).
- **Allowlist IS the trust decision, AST IS NOT.** The same pattern as PR-1A: a plugin that imports `subprocess` (e.g. `self_improve.py`) would fail the AST check, but its hash is in the allowlist so it loads. AST is only consulted to enrich the audit emit for refused plugins.
- **Grandfather migration.** First `scan()` after PR-2F lands with no allowlist file present + plugins in `~/.codec/plugins/*.py` writes their current hashes to the allowlist with `approved_by: "initial_migration"` + emits `plugin_allowlist_migrated`. Idempotent — subsequent scans no-op.
- **Daemon-thread timeout.** Every hook fire runs inside `threading.Thread(daemon=True)` with a hard timeout (default 500ms, configurable via `~/.codec/config.json:plugin_hook_timeout_ms`). On timeout: `plugin_hook_timeout` audit, return `None`, calling thread continues. Daemon=True so process shutdown isn't blocked.
- **Operator approval flow.** New skill `plugin_approve` (`SKILL_MCP_EXPOSE=False`) — invoked via chat "approve plugin newhook.py" — computes the file's SHA-256, writes the allowlist entry with `approved_by: "operator"`, clears any prior refusal cache, emits `plugin_approved`. The skill is NEVER MCP-exposed (claude.ai can't extend the plugin trust boundary).

Implementation: `codec_hooks.py` (run_with_hooks + PluginRegistry — now owns its `allowlist_path` for per-test isolation), wired into `codec_dispatch.py:run_skill` (covers wake-word + chat pre-LLM + chat post-LLM tag), `codec_agents.py:Agent.run` (crew), `codec_voice.py:dispatch_skill` (voice WebSocket), `codec_mcp.py:tool_fn` (MCP stdio + HTTP). Voice WebSocket also fires `emit_operation_start` / `emit_operation_end` from `VoicePipeline.run` start/finally.

### AskUserQuestion + stuck detection + step budget (Phase 1 Step 3)

CODEC agents can pause and ask the user a structured question, self-detect when they're stuck in a loop, and enforce a per-turn step budget on the chat handler. See `docs/PHASE1-STEP3-DESIGN.md` (commit b187b8d, §9 RESOLVED).

**AskUserQuestion** (`codec_ask_user.py`): public API `ask(question, *, options=None, timeout=600, destructive=False, destructive_verb=None, agent=None, crew_id=None, asked_from="chat", tool_name=None) -> str`. Blocks the caller's worker thread on `threading.Event.wait()` until the user replies via PWA (`POST /api/agents/answer/{qid}`) or voice (`codec_voice.VoicePipeline._handle_voice_ask_user_answer`). Question state in `~/.codec/pending_questions.json` (atomic write); display surface in `~/.codec/notifications.json` with `type="question"`. Skill shim at `skills/ask_user.py` exposes the tool to the LLM and over MCP. Default 600s timeout; tunable via `~/.codec/config.json:ask_user.timeout_seconds`. Kill switch: `ASKUSER_ENABLED=false`.

**Strict-consent gate (§1.7)**: irreversible actions opt in via `destructive=True`, caller-supplied `destructive_verb`, OR auto-trigger when the calling tool name is in `codec_config._HTTP_BLOCKED`. On strict-consent, the answer must contain the destructive verb literally (case-insensitive); generic "yes"/"ok"/"sure" rejected with re-prompt. After two rejections, the question times out as `ask_user_question_timeout` with `extra.reason="ambiguous_consent"` — does NOT wait for the deadline. Voice ASR layer (`codec_voice._resolve_voice_option_choice`) BYPASSES fuzzy match in strict mode so `submit_answer` evaluates the literal verb-match rule.

**Stuck detection** (`codec_agents.Agent`): per-agent ring buffer of last M=5 (tool_name, args_hash) tuples. Soft warning at N=3 identical calls (banner injected into result string + `stuck_warning` audit emit), escalation at N+2=5 calls (configurable: `ask_user` (default), `abort`, or `warn_only` via `~/.codec/config.json:stuck.escalation_action`). Wired into `Agent.run` post-tool via `run_in_executor` so synchronous `ask_user.ask()` doesn't block the event loop. LLM-self-recognized stuck path: `skills/stuck.py` (companion shim, also routes to `ask_user`). Kill switch: `STUCK_DETECTION_ENABLED=false`.

**Chat-handler step budget** (`codec_dashboard._StepBudget`): per-turn cap on the chat handler. Default routes: `chat=5`, `voice=5`, `mcp=None` (no cap). `consume(kind)` returns False when over limit; `warn_now()` returns True at limit-1 (drives "1 step remaining" prompt suffix); first over-step emits a `step_budget_exhausted` audit line (idempotent). Tune up before tuning out: `~/.codec/config.json:step_budget.{chat,voice}` accepts 8 or 10. Kill switch: `STEP_BUDGET_ENABLED=false`.

**Audit envelope**: all Step 3 events use `outcome="warning"`, `level="warning"`. They are NOT `outcome="error"` because each is an operational signal, not an operation failure (same Q4 tightening as Step 2's `hook_error`). `correlation_id` inherits from the wrapping operation per Step 1 §1.4.

### Continuous Observation Loop (Phase 2 Step 5)

CODEC has a background process (`codec-observer` PM2 service, `codec_observer.py`) that polls four cheap signals — frontmost window, screenshot OCR, clipboard delta, recent file changes — and keeps the last 10 minutes of state in a RAM ring buffer, mirrored to one owner-only file for cross-process recall (privacy contract below). On every chat / voice request, an injection helper decides whether to prepend a ≤200-token summary to the LLM's system prompt, gated per the §X "Observation injection contract":

- **`transport="local"`** (local Qwen) → always inject. Cheap + private.
- **`transport="mcp"`** → never inject. The MCP client (claude.ai, Claude.app) brings its own context.
- **`transport in {"chat", "voice", "http"}`** → gated on cheap text-pattern checks: possessive-without-context (`"my X"`/`"this Y"` filtered against a stop-noun list), continuation language (`"continue"`, `"where was I"`), or skill-flag (`SKILL_NEEDS_OBSERVATION = True` on a resolved skill module).

**Privacy contract**: 4 layers. (1) RAM buffer — `collections.deque` wiped on process restart — plus one disk mirror, `~/.codec/observer_buffer.json`, which the daemon rewrites after every poll so skills in other processes can answer "what was I doing?" (`skills/observer_recall.py`). The mirror is owner-only (0600, atomic write via `codec_jsonstore.atomic_write_json`), holds no clipboard text (content type and length only; RAM keeps the preview for the local summary), and is deleted when the observer is paused (kill switch), after the long-idle reset, and when the daemon exits (docs/OBS-DESIGN.md). (2) Audit emits are METADATA-ONLY: lengths, counts, `content_type` tags, but NEVER raw window titles, OCR text, clipboard content, or file paths. (3) Cloud-transport injection gating per §X. (4) NO new system permissions — uses existing skills + primitives (osascript, pbpaste, Quartz, getmtime).

**Cadence**: 60s when active (`CGEventSourceSecondsSinceLastEventType < 60s`); drops to 5min when idle. Long-idle reset wipes buffer at 30min idle.

**Kill switch**: `OBSERVER_ENABLED=false` env var disables polling AND injection.

**Pause from the pages (UI P3.7)**: `~/.codec/observer_paused_until` (one ISO time, 0600) is written by `POST /api/observer/pause` (the eye in each page's header) and deleted by `/api/observer/resume`. While it is in the future the daemon does not poll, forgets its RAM buffer and deletes the disk mirror once, injection is skipped, and the shift report still fires. `GET /api/observer/state` and `/api/observer/now` read the disk mirror (metadata only); `/api/observer/buffer` reads the dashboard's own (empty) buffer and stays debug-only.

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

**4 audit events** — `trigger_evaluated`, `trigger_fired`, `trigger_blocked`, `trigger_muted`. Fields and levels: docs/codec-architecture.md.

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

**2 audit events** — `shift_report_started` / `shift_report_completed`, sharing one correlation_id. Fields: docs/codec-architecture.md.

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

**6 audit events** covering the plan lifecycle (drafted / approved / rejected / revised, and the two global-grant events). Fields: docs/codec-architecture.md.

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

**8 audit events** covering the agent runtime lifecycle, all sharing the `agent_started` correlation_id. Fields: docs/codec-architecture.md.

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

**3 audit events** covering agent ↔ user messaging. Fields: docs/codec-architecture.md.

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

## 4. Skill system

Single-file Python modules with module-level metadata + `run(task, app="", ctx="") -> str`. See `skills/_template.py` for the canonical template.

Required: `SKILL_NAME`, `SKILL_TRIGGERS`, `SKILL_DESCRIPTION`. Optional: `SKILL_MCP_EXPOSE`.

### Discovery
`codec_skill_registry.SkillRegistry` (`codec_skill_registry.py:57-195`) AST-parses every `.py` file at startup to extract metadata — **no skill code runs unless the skill is actually called**. Broken skills don't break startup.

**Single loader (A-4).** There is now exactly ONE skill loader: the lazy, AST-gated `SkillRegistry` behind `codec_dispatch.{load_skills, check_skill, run_skill}`. The old eager `codec_core.load_skills` / `loaded_skills` / `run_skill` path was deleted in Wave 3 (A-4) — it bypassed the PR-1A safety gate and the Step-2 plugin hooks. Every entry path (voice/wake via `codec.py:_dispatch_inner`, chat, MCP, session, telegram, imessage, agent-runner) goes through `codec_dispatch` now. **User trigger overrides** in `~/.codec/custom_triggers.json` are honored by the registry (`get_triggers`/`match_all_triggers` overlay them) across ALL paths — not just voice as before.

### Three execution paths

**A. Voice / wake-word:** `codec.py:wake_word_listener()` → `dispatch(text)` → `_dispatch_inner(task)` → `check_skill(task)` → `run_skill(skill, task, app)`.

**B. Dashboard chat:**
- Slash commands: `parse_slash` at `codec_slash_commands.py:55-90` runs first
- Pre-LLM hijack: `_try_skill(user_text)` at `codec_dashboard.py:2081-2110` — if a skill in `CHAT_SKILL_ALLOWLIST` matches and the message isn't conversational and has no file attachments, fire the skill and skip the LLM
- Explicit pick (P2.3): the composer's '@' picker sends a `skill` body field; `routes.chat._try_explicit_skill` runs exactly that skill, no trigger matching. Only the skills `/api/mentions` lists (the chat allowlist minus `create_skill`), or `mcp_connect` for an `mcp:<server>` target, after `codec_consent.chat_consent_ok`, through `codec_dispatch.run_skill`; anything else is refused without running.
- Post-LLM tag: LLM emits `[SKILL:name:query]`, parsed + the skill run + the tag replaced. Non-streaming path: regex in `chat_completion`. Streaming path: `codec_chat_stream.SkillTagBuffer` (PR-3D-c/A-6) buffers tokens char-by-char so a raw tag never leaks, strips `<think>` across chunks, and resolves complete tags via an injected callback; `_stream_gen` keeps only the SSE plumbing. `SKILL_TAG_RE` is the shared pattern.

**C. MCP (stdio + HTTP):** `codec_mcp.py:87-191` registers every skill with `SKILL_MCP_EXPOSE=True` as an MCP tool. 30s timeout, validated via `_validate_mcp_input` (task ≤ 5,000 chars, context ≤ 10,000), audited via `codec_audit.audit()`. HTTP transport adds OAuth 2.1 + a stricter `_HTTP_BLOCKED` list (`codec_config.py`): currently blocks `python_exec`, `terminal`, `process_manager`, `pm2_control`, `ax_control`. Over HTTP only (stdio and local chat/voice unchanged), `codec_config._mcp_blocked_tools("http", …)` also blocks `_HTTP_ONLY_BLOCKED` — `standing_rules` (text it saves goes into every local chat system prompt) and `create_skill` (writes skill code) — via the audited `mcp_http_blocked` stub. These are a separate list on purpose: `_HTTP_BLOCKED` membership also marks a skill destructive on every path (`codec_consent`, `codec_ask_user`, `codec_agent_runner`), which would refuse it over stdio too. Over HTTP, the side-effect skills in `_HTTP_CONSENT_REQUIRED` — `delegate`, `scheduler`, `chrome_fill`, `chrome_click_cdp`, `mouse_control`, `clipboard`, `screenshot_text`, and since 28 Sep 2026 every other `chrome_*` tool except `chrome_scroll` — run only after the owner approves each call in the PWA: `codec_mcp.tool_fn` → `codec_consent.mcp_http_consent_ok` → `codec_ask_user.ask(destructive=True, asked_from="mcp")` with "Allow <tool>" / "Decline" buttons; only the Allow button (or the bare reply "allow") approves; decline, 120s timeout (`CODEC_MCP_CONSENT_TIMEOUT`), or any error refuses with a `denied` / `OwnerConsentDenied` audit. Honors the `CONSENT_GATE_ENABLED=false` kill switch like the destructive MCP refusal.

### Switching a skill off (UI P3.6)
`config.json:skills_off` is the owner's deny list, written by the Skills page (`PUT /api/skills/switch`) and `/skills enable|disable` through `codec_skill_switches` (one `skill_switched` audit line, no values). Every path checks `codec_skill_switches.is_off` before running a skill: `codec_dispatch.check_skill` skips it and `run_skill` refuses it (wake word, chat, sessions, bridges, agent runner), the MCP tools, the voice call's matcher, crew tools, autopilot rules and the '@' list. Features that call a skill internally (the briefing's sources, Save to Google Doc) are not switched by it. A new skill is on until turned off; the old `skills` allow list is ignored. The file is re-read on change, so no restart is needed for a switch.

### Adding a new skill
Drop a `.py` file in `~/.codec/skills/` (user) or `skills/` (built-in). Restart `codec-dashboard` and the main `open-codec` process. Hot-reload is not currently supported.

**Two folders (2026-10-07, docs/USER-SKILLS-DIR-DESIGN.md).** Every registry reads the built-in folder (`codec_config.SKILLS_DIR`, the repo's `skills/` unless `config.json:skills_dir` says otherwise) and then the user folder (`codec_config.USER_SKILLS_DIR`: `config.json:user_skills_dir`, default `~/.codec/skills`). A built-in name always wins; a user file is never trusted by the manifest, so every load runs the AST gate; files starting with `_` and sub-folders are skipped. `/api/skill/approve` (review-and-approve, Settings > Learning) writes approved skills to the user folder (0700 folder, 0600 file), never the repo. The test suite points `CODEC_USER_SKILLS_DIR` at a temp folder (tests/conftest.py).

## 5. Memory contract

Single SQLite database at `~/.codec/memory.db`. Wrapper: `codec_memory.CodecMemory`.

### Tiered retrieval (codec_memory_upgrade.py + codec_memory.py)
- **L0/L1 — `identity.txt`:** `codec_memory_upgrade.load_identity()`, always-loaded boot payload (<200 tokens). Persistent identity + preferences. Zero query cost
- **Recent sessions — `CodecMemory.get_sessions(limit=20, user_id=None)`:** one `GROUP BY session_id` query over `conversations`, newest session first: message count, first and last timestamp, and a 100-character preview of one user message (picked with `MAX(content)`, so alphabetical, not the latest). Used by `GET /api/memory/sessions` and Daybreak
- **Deep search — `CodecMemory.search(query, limit=10, user_id=None)`:** FTS5 over the `conversations_fts` index, ranked by BM25. The query goes through `_sanitize_fts_query` first (FTS operators and `* " ( ) ^` stripped, 200 characters max); an empty query or an FTS error returns `[]`. `CodecMemory.get_context()` formats its top hits for the chat, voice and wake-word prompts; `GET /api/memory/search` and the MCP `search_memory` tool call it directly

### Facts table
Temporal KV with `valid_from`, `valid_until`, `superseded_by`. NOTE (2026-06-09 correction): a `valid_at(timestamp)` time-travel query does NOT exist in code — currently-active facts are `valid_until IS NULL` (`query_valid_facts`); full timelines via `get_fact_history`; close-without-replace via `expire_fact` (added for Daybreak). Daybreak working threads live here as `key="thread:{kind}:{slug}"`, `fact_type="thread"` (docs/DAYBREAK-DESIGN.md).
Settings > Memory (P2.4, `routes/memory_page.py`) edits facts only by superseding (`store_fact`) or closing (`expire_fact`) — never a DELETE — and also edits standing rules and `~/.codec/memory/identity.txt` (0600). `config.json:memory.fact_extract=false` makes the `fact_extract` skill save nothing on every path; the nightly `auto_memorize` run is the managed schedule job `sched_auto_memorize` (delivery `silent`).

### Backups and retention (2026-10, docs/BACKUP-RETENTION-DESIGN.md)
`codec_backup.backup_sqlite` is the only writer of database backups in `~/.codec/backups/`: backup API copy, `PRAGMA quick_check` on the copy, 0600, a failed copy replaces nothing and alerts once. The heartbeat, `scripts/backup_memory.sh` (launchd `com.codec.memory-backup`) and the `backup_status` skill call it; never add another. The state tar (`state_<date>.tar.gz`) holds agents/, config.json, notes/, standing_rules.json, schedules.json, voices/codec-02 and plugins.allowlist. Conversations, clipboard history and webcam photos are deleted after `config.json:retention` days (owner decision D-38: 90 / 30 / 30; Settings > Memory), only after a checked memory.db copy of the day, and memory.db is copied again before the first clean-up with a new number; every count is in `~/.codec/backup_status.json`. Restore is `tools/codec_restore.py`, never a skill: a live restore needs the owner's literal 'restore' through `codec_ask_user` and never restores plugins.allowlist.

### CCF (Conversational Context Fragmentation)
Rule-based compressor for memory writes that need shrinking. Entity abbreviation + filler stripping. Personal entity entries belong in `~/.codec/entity_map.json` (private), not in source.

## 6. Audit + notification contract

### Integrity + redaction + perms (Phase 1 Wave 2, PR-2E — closes D-12 + D-19 + D-22)

**HMAC-SHA256 per line.** Every audit line emitted post-PR-2E carries an `hmac` field — a 64-hex SHA-256 HMAC computed over the canonical-JSON form of the record (minus the `hmac` field itself). Canonical JSON = `sort_keys=True, separators=(",", ":"), ensure_ascii=True` so the byte representation is stable across Python implementations. Secret: 32 raw bytes stored hex-encoded in macOS Keychain as `ai.avadigital.codec.audit_hmac_secret`. Bootstrapped automatically on first write via `codec_keychain.get_audit_hmac_secret()` (silent path — bypasses `_kc_log_event` to avoid recursive audit emit; uses stdlib `logging` for visibility). Cache TTL 5min — audit writes are hot.

**Verification.** `codec_audit.verify_audit_log()` walks every line and returns `{total_lines, signed_lines, unsigned_lines, broken_lines, first_broken_line_no, integrity_ok}`. Operator-facing skill `audit_verify` (chat / voice triggers: "verify audit log", "check audit integrity") wraps it. `SKILL_MCP_EXPOSE=False` — forensic operations don't traverse the MCP boundary.

**Tamper detection contract:**
- Modify an existing line (any field) → HMAC mismatch → `broken_lines` increments → `integrity_ok=False`.
- Append a forged line with bogus HMAC → mismatch → detected.
- Truncate the log → undetectable by HMAC; obvious by file mtime/size.
- **Delete a whole line in the middle → NOT detected by per-line HMAC.** Documented limitation of Q5=a (HMAC-per-line over hash-chain). Hash-chain is fragile on log rotation; HMAC-per-line trades order-of-events tamper detection for simplicity + rotation safety. A future enhancement may add an external anchor (periodic Merkle root).

**Pre-PR-2E lines.** Old audit lines (no `hmac` field) are classified as `unsigned`, not `broken`. After the 30-day rotation window all log files have HMACs.

**Keychain unavailable.** If `get_audit_hmac_secret()` returns None (Keychain locked + fallback failed), the line is written with `hmac=""` and `hmac_status="unsigned_keychain_unavailable"`. The verify utility counts these as `unsigned`, not `broken`. The operator sees a stdlib `WARNING` log line at the time of write.

**Secret redaction (D-19).** Before HMAC + write, `_redact_secrets` walks the entire record (top-level + nested in `extra`, lists, etc.) and applies 15 compiled regex patterns. Order is specific-first so the placeholder tag preserves the most-specific match:

- `<REDACTED:anthropic_key>` — `sk-ant-\w{30,}`
- `<REDACTED:openai_or_anthropic_key>` — `sk-\w{20,}`
- `<REDACTED:aws_access_key>` — `AKIA[0-9A-Z]{16}`
- `<REDACTED:aws_secret>` — `aws_secret_access_key=...{40}`
- `<REDACTED:github_pat>` / `<REDACTED:github_oauth>` — `ghp_\w{36}` / `ghs_\w{36}`
- `<REDACTED:slack_token>` — `xox[bpsa]-\w{10,}`
- `<REDACTED:google_api_key>` — `AIza\w{35}`
- `<REDACTED:jwt>` — 3-part base64url JWT
- `<REDACTED:bearer_token>` — `Bearer \w{20,}` (case-insensitive)
- `<REDACTED:codec_oauth_access>` / `<REDACTED:codec_oauth_refresh>` — `codec_at_<64hex>` / `codec_rt_<64hex>`
- `<REDACTED:user>:<REDACTED:pass>@` — basic-auth URLs
- `<REDACTED:private_key_header>` — `-----BEGIN ... PRIVATE KEY-----` marker
- `<REDACTED:cc_candidate>` — 13-19 digit sequences (accepted false-positive rate; safer than leaking real CCs)

Redaction runs **BEFORE** truncation: a secret near the 500-char `message` cap is fully redacted, not chopped mid-pattern. `_write` does another whole-record redact pass as defense-in-depth for `extra`, `error_type`, etc.

**chmod 0600 (D-22).** `_open_audit_log_append` uses `os.fdopen(os.open(path, O_WRONLY|O_APPEND|O_CREAT, 0o600))` on first creation — bypasses the umask. Existing files defensively `os.chmod(0o600)` before every append (try/except wrapped for FUSE / RO mounts).

**Performance.** 1000 audit writes on M1 Ultra take ~102 ms (102 µs/call). Cache amortizes the Keychain shellout; the per-write hot path is regex sweep + canonical JSON + SHA-256 + file open/write/close.

### Phone notifications (Web Push, UI P3.13)
`codec_push.notify(kind)` sends a content-free push to the owner's subscribed devices: `codec_ask_user.ask` sends `approval` (strict consent) or `question`, `codec_agent_messaging.post_message` sends `agent_done` / `agent_blocked` / `agent_stopped` (not for silenced agents), and the morning briefing will send `briefing`. Callers pass only a kind; the text is a fixed line in `codec_push.KINDS`, so no question, agent name or chat text reaches a push service. VAPID private key: Keychain `ai.avadigital.codec.vapid_private_key` (created once by `GET /api/push`; deleting it invalidates every device's subscription). Devices and the per-kind switches: `~/.codec/push.json` (0600; endpoints only on known push services). Kill switch `PUSH_ENABLED=false`. Design: docs/P3.13-DESIGN.md.

### Inbox (UI P3.2)
`GET /api/inbox` (`routes/inbox.py`) is the one list the PWA polls for what needs the owner or reports back: pending approvals (`routes/_shared._pending_approvals`), open `codec_ask_user` questions, and the report, agent and suggestion entries of `~/.codec/notifications.json` (question entries are left out; id-less agent entries get a stable `auto_` id). The server picks each item's actions and only offers `/api/agents/<id>/pause|resume|grant` and `/api/proactive/acknowledge|dismiss`; there is no "always allow". `POST /api/inbox/read` marks items read under the same two locks as every notifications writer. The shell's drawer is the only poller; Home's Flash question panel subscribes to it. Design: docs/P3.2-DESIGN.md.

### log_event (`codec_audit.log_event`)
Real adapter over `audit()` for lifecycle events (session start/end, scheduler tick, dispatch decision, heartbeat alert). Defined in `codec_audit.py`. Call sites in `codec_session.py`, `codec_scheduler.py`, `codec_dispatch.py`, `codec_heartbeat.py`, `codec_dashboard.py`, `codec.py`, `routes/auth.py`.

> **Status as of Phase 1 Step 1 (commit 05f9b80):** adapter wired through, correlation_id contract enforced. Prior to this branch, every `log_event` call was a silent no-op (the `try: from codec_audit import log_event` import was failing because the export didn't exist; the `except: def log_event(*a, **kw): pass` fallback ran instead). All 7 modules now import the real adapter and emit canonical event names per §1.2 of the design.

## 7. Sandbox + safety boundaries

### Files & directories
- `~/.codec/` — all user state (config, memory, audit, schedules, notifications, agents, skills, plugins, proposals)
- `~/.codec/plugins/*.py` — user-authored lifecycle hook plugins (Phase 1 Step 2; see §3 *Plugin lifecycle hooks*). Same trust model as `~/.codec/skills/`: local Python files curated by the user, no marketplace, no auto-install, no inter-plugin sandbox. Files starting with `_` are skipped.
- `~/.codec/pending_questions.json` — Phase 1 Step 3 canonical state for AskUserQuestion (atomic write; never edit by hand). The reply path goes through `POST /api/agents/answer/{qid}` and the voice handler — both call `codec_ask_user.submit_answer()` which writes the answered status atomically. Direct edits race the in-flight `threading.Event` waiters and break agents.
- `~/.codec/voice_session.json` — Phase 1 Step 3 voice-session active-marker. Touched by `VoicePipeline.run` start, removed in finally. `codec_ask_user` reads this to decide whether voice should announce and listen for an answer or defer to PWA only.
- File operations from agent tools go through `codec_sandbox` (path validation against blocklist, size caps)
- Dangerous code patterns detected by `codec_config.is_dangerous_skill_code` before any skill is staged from `codec_self_improve.py` proposals

### `python_exec` sandbox + `/api/execute` removal (Phase 1 Wave 2, PR-2C — closes D-9 + D-10)

**`/api/execute` removed entirely.** The dashboard's local `/api/execute` endpoint, `_DANGEROUS_PATTERNS` regex blocklist, `_is_command_safe` helper, and `execute_terminal` handler were all deleted in PR-2C. Shell command execution now routes exclusively through the `terminal` skill — which is in BOTH `codec_config._HTTP_BLOCKED` (claude.ai over MCP HTTP can't reach it) and `codec_config._STDIO_BLOCKED` (Claude desktop / stdio MCP blocked), and goes through the strict-consent gate (Step 3 §1.7) for destructive ops on the local chat / voice path.

**`python_exec` hardened.** Two-layer defense for the local-only Python execution skill (`SKILL_MCP_EXPOSE=False`, unchanged):

1. **AST gate.** The substring blocker (`"import os"`, `"eval("`, etc. — trivially bypassable via `getattr(__builtins__, chr(95)*2+'import'+chr(95)*2)`, `vars(__builtins__)`, unicode-escape, etc.) was replaced with `codec_config.is_dangerous_skill_code` — the same AST validator that PR-1A uses at `SkillRegistry.load`. Refusals emit `python_exec_blocked` audit (`source=codec-skill-python-exec`, level=`warning`, extra={`reason`, `code_preview`}).

2. **`sandbox-exec` at runtime.** Python interpreter runs inside `/usr/bin/sandbox-exec -f <profile>` using `codec_sandbox`'s deny-default profile: no network, no process spawning, file writes restricted to `~/.codec/skill_output/` and `/tmp/`. Even if AST-validated code somehow reaches a novel reflection pattern the validator missed, the kernel blocks the syscall.

3. **Resource limits via `preexec_fn`.** `RLIMIT_CPU` (5s wall-clock), `RLIMIT_AS` (256 MB address space), `RLIMIT_NOFILE` (32 file descriptors) — caps `while True: pass`, memory bombs, and FD leaks. `subprocess.run` outer `timeout=10` is the belt-and-suspenders backstop.

4. **Minimal env.** PATH=`/usr/bin:/bin`. PYTHONPATH, LD_LIBRARY_PATH, SHELL, HOME stripped — an attacker can't redirect the interpreter at a writable site-packages.

### Crew shell sandbox + custom-agent tool check (P-030 parts 2-4, owner decision D-17, docs/CREW-SHELL-SANDBOX-DESIGN.md)

The crew / custom-agent `shell_execute` tool runs `sandbox-exec -f <profile> /bin/sh -c <cmd>` with a profile from `codec_sandbox._write_sandbox_profile(home=, profile_path=, output_dir=, allow_shell=True)`: no network, the home's secret folders unreadable, writes only in `~/codec-workspace/agent-shell`, `/private/tmp` and `/dev/null`, programs from `/bin`, `/usr`, `/opt/homebrew` and the Python framework, a minimal environment, 30 s then the whole process group is stopped. One profile file per call in `~/.codec/crew_shell-*.sb`, deleted afterwards. Commands `is_dangerous` flags ask through `codec_consent.local_consent_ok(asked_from="crew")` (`shell_execute` is in `_LOCAL_CONSENT_REQUIRED`); with the gate off or longer than 200 characters they are refused. Without `/usr/bin/sandbox-exec` the tool refuses; never add an unsandboxed fallback. With no keyword arguments the builder writes exactly python_exec's old profile. Custom-agent saves (`/api/agents/custom/save`) refuse tool names outside `codec_agents.known_tool_names()`; older saved agents load with unknown names left out (`custom_agent_tools_dropped`).

### Dashboard session cookie (Sep 2026 audit follow-up)

Logins (`routes/auth.py`) set the session as an **HttpOnly** `codec_session` cookie in the response, plus a separate random `codec_csrf` cookie that pages echo in `x-csrf-token`. The token is never in a response body, a URL (`?s=` is no longer accepted), sessionStorage, or a log line. Never reintroduce page-side token handling: same-origin fetches, images, streams and the WebSocket send the cookie by themselves. Touch ID verify is Mac-only (403 for tunnel requests). Details: `docs/SECURITY-HARDENING-2026-09.md`.

### Host allowlist + Mac-only code execution (Sep 2026 audit follow-up)

`HostAllowlistMiddleware` (outermost, HTTP + WebSocket) refuses any Host that is not an IP literal, `localhost`, a tunnel host in `TRUSTED_ORIGIN_HOSTS`, or listed in `config.json:dashboard_public_hosts` / `CODEC_DASHBOARD_EXTRA_HOSTS`. It is the DNS-rebinding guard for installs without a login. With no login configured, both HTTP and the voice WebSocket serve this Mac only. `/api/run_code` refuses tunnel requests (403) and kills the program's whole process group at its timeout. Details: `docs/SECURITY-HARDENING-2026-09.md`.

### Internal IPC token replacement (Phase 1 Wave 2, PR-2D — closes D-11)

Before PR-2D, `AuthMiddleware.dispatch` short-circuited ALL auth for any localhost request that sent the literal header `X-Internal: codec`. Any malicious user-mode process on the user's Mac (Homebrew tap typo-squat, compromised PyPI package, D-1 RCE chain) could spoof that header from a `curl` against `127.0.0.1:8090` and bypass `dashboard_token` + Touch ID / PIN entirely.

PR-2D replaces the literal-trust with a per-process HMAC token. Three components:

1. **`codec_keychain.get_internal_token()`.** Reads the cached token (30s TTL), falling back to a Keychain read on miss. If no token exists yet, generates a fresh `secrets.token_urlsafe(32)` value and persists to `ai.avadigital.codec.internal_token`. Bootstrap emits `internal_token_autogenerated` audit. **Never lands on disk as plaintext** — the previous design idea of `~/.codec/internal_token` (0600 file) was upgraded to Keychain to match D-8/D-15 storage tier.

2. **`AuthMiddleware` HMAC validation.** The short-circuit (`codec_dashboard.py:138-170`) now:
   - Reads `request.headers.get("x-internal-token", "")`.
   - If non-empty, fetches `get_internal_token()` and compares via `hmac.compare_digest` (constant-time — defends against timing side-channels on token guessing).
   - Match → `await call_next(request)` (skip normal auth).
   - Mismatch → emit `internal_token_mismatch` audit (warning, `extra={path, method, client_ip, presented_len}`) and fall through to normal auth (will 401).
   - The legacy `X-Internal: codec` header IS NO LONGER RECOGNIZED. A caller sending only that header gets 401 like any unauthenticated request.

3. **Internal callers updated.** Every in-process call to the dashboard sends `x-internal-token: <get_internal_token()>` instead of `x-internal: codec`:
   - `codec_heartbeat.py` (DB-size alert notification post)
   - `codec_scheduler.py` (2 sites: `/api/agents/run` POST + status poll GET)
   - `skills/notification_reader.py` (notification list / count / mark-read)
   - `skills/health_check.py` (cortex health aggregation)
   - `scripts/feature_audit.py` (runtime feature inventory audit — INTERNAL header dict + the raw-socket WebSocket upgrade probe)

**Headless / fallback:** Same behavior as PR-2B's other secrets — when `is_keychain_available()` returns False, `get_internal_token()` uses the envelope-encrypted `~/.codec/secrets.enc.json` store (0600 XOR keystream, fallback-grade not Keychain-grade).

**Operator rotation:** to force a fresh token, `security delete-generic-password -s ai.avadigital.codec.internal_token` then restart any daemon. Next `get_internal_token()` call bootstraps a new value. All running daemons re-read on their next cache miss (≤30s).

**Defense-in-depth.** The localhost-IP check is preserved as a defense-in-depth floor — both `client_ip ∈ (127.0.0.1, ::1, localhost)` AND a matching token are required to short-circuit. A non-localhost caller with a stolen token still falls through to normal auth (and a stolen token is a Keychain-extraction-grade compromise — at that level the attacker already has D-8/D-15-class access).

### Secret storage via macOS Keychain (Phase 1 Wave 2, PR-2B — closes D-8 + D-15 partial)

Three secrets live in macOS Keychain instead of `~/.codec/config.json` / `~/.codec/oauth_state.json` plaintext:

| Secret | Keychain key | Was |
|---|---|---|
| OAuth access + refresh tokens (whole state JSON) | `ai.avadigital.codec.oauth_state` | `~/.codec/oauth_state.json` (0600 plaintext) |
| Dashboard bearer token | `ai.avadigital.codec.dashboard_token` | `config.json:dashboard_token` |
| LLM provider API key | `ai.avadigital.codec.llm_api_key` | `config.json:llm_api_key` |
| Gemini API key (PR-2B-2) | `ai.avadigital.codec.gemini_api_key` | `config.json:gemini_api_key` |
| Pexels API key (PR-2B-2) | `ai.avadigital.codec.pexels_api_key` | `config.json:pexels_api_key` |
| Serper API key (PR-2B-2) | `ai.avadigital.codec.serper_api_key` | `config.json:serper_api_key` |
| Telegram bot token (PR-2B-2) | `ai.avadigital.codec.telegram_bot_token` | `config.json:telegram.bot_token` (nested) |

**Migration is idempotent + automatic.** On first daemon startup post-PR-2B, `codec_config.get_llm_api_key()` / `get_dashboard_token()` migrate the cfg plaintext to Keychain, then blank the on-disk field. The OAuth provider migrates its state on first `_save()`. Subsequent startups read directly from Keychain. PR-2B-2's four getters follow the same first-call migration (the telegram one descends into the nested `telegram.bot_token` and preserves sibling keys like `chat_id`).

**Runtime accessors** (use these, not `cfg.get("llm_api_key", "")`):
- `codec_config.get_llm_api_key()` — 30s in-memory cache, falls back to cfg on Keychain miss
- `codec_config.get_dashboard_token()` — same
- `codec_config.get_gemini_api_key()` / `get_pexels_api_key()` / `get_serper_api_key()` / `get_telegram_bot_token()` (PR-2B-2) — same 30s cache; precedence Keychain → cfg (migrate) → env-var fallback (`GEMINI_API_KEY` / `PEXELS_API_KEY` / `SERPER_API_KEY` / `TELEGRAM_BOT_TOKEN`)
- `codec_keychain.get_oauth_state()` — no cache (called only at provider init)

**D-15 fully closed by PR-2B-2.** All six audit-named plaintext secrets are now Keychain-backed. **Residuals:** `alerts.telegram.bot_token` (a SEPARATE nested key under `alerts`) is no longer needed since Sep 2026: `codec_alerts` reads the CODEC bot token from the Keychain and only needs `alerts.telegram.chat_id` (owner alerts, outbound only; see `docs/SECURITY-HARDENING-2026-09.md`). An old plaintext value is still honoured if present. `auth_pin_hash` argon2id refactor remains deferred per the original Out-of-scope decision.

**Headless / CI fallback** (`codec_keychain.is_keychain_available() → False` on Linux): envelope-encrypted store at `~/.codec/secrets.enc.json` keyed by `~/.codec/secret.key` (0600, stdlib XOR keystream). **Fallback-grade, NOT Keychain-grade** — defends against casual file-read disclosure on a CI runner; does NOT defend against a determined attacker with shell access. Production threat model assumes real Keychain on macOS.

**`security` CLI behavior:** exit 51 = "user interaction not allowed" (Keychain locked). Background daemons cannot prompt — `keychain_get` returns None and emits `keychain_locked` audit event. Caller falls back to cfg until next operator login + Keychain unlock.

### Dashboard binding + startup safety (Phase 1 Wave 2, PR-2A — closes D-7)

The dashboard's network surface is gated at startup. Two changes per audit D-7 closure:

1. **Default loopback.** `codec_config.DASHBOARD_HOST` defaults to `"127.0.0.1"`. Out-of-box, the dashboard is unreachable from LAN. Users who want LAN/Cloudflare-tunneled access set `dashboard_host: "0.0.0.0"` in `~/.codec/config.json` — explicit opt-in.

2. **Refuse unsafe start.** `codec_dashboard._check_dashboard_start_safety(host, dashboard_token, auth_enabled)` is called in the `__main__` block before `uvicorn.run`, and again by the first startup hook (`_enforce_dashboard_start_safety`) so `uvicorn codec_dashboard:app --host …` (the PM2 path) is covered too — there the host comes from `--host` / `UVICORN_HOST`, and a refusal fails the ASGI lifespan so uvicorn exits before binding. If the host is public (`0.0.0.0` / `::` / `*`) AND neither `dashboard_token` nor `auth_enabled` is set, the dashboard logs a `CRITICAL` and exits non-zero. The error message lists three concrete fixes (revert to loopback, set token, enable Touch ID/PIN). Loopback hosts (`127.0.0.1`, `::1`, `localhost`) always start regardless of auth — LAN can't reach them.

Combined, the out-of-box state for a fresh CODEC install is *secure-by-default*: PWA over Cloudflare tunnel keeps working (Cloudflare → `127.0.0.1`), but a misconfiguration that would expose unauthenticated `/api/skill/review` / `/api/command` / `/api/agents/*` etc. to the LAN now fails closed at startup instead of silently opening the surface.

### Agent permission gate + path blocklist (Phase 1 Wave 1, PR-1D — closes D-5 + D-14 + D-16)

`permission_gate` in `codec_agent_runner.py` enforces the Step 9 manifest on every Action. As of PR-1D:

1. **`..` segments rejected outright.** `~/Documents/../../etc/passwd` is refused even if the resulting realpath happens to match a different grant (or none). Previously `fnmatch.fnmatch` glob-matched the raw string and missed the traversal.
2. **Realpath both sides.** Action paths and grant roots are resolved via `os.path.realpath` before comparison — symlinks pointing outside a granted root are caught.
3. **Prefix-on-realpath comparison.** `fnmatch` was replaced with `action_real.startswith(grant_real + os.sep)`. Trade-off: a grant like `~/Documents/*.md` now accepts any file under `~/Documents/`, not just `*.md`. Safety > granularity per the audit recommendation.
4. **Audit emission on rejection.** Every refused action emits `permission_gate_blocked` (`source=codec-agent-runner`, `outcome=error`, `level=warning`, `extra={requested_path, resolved_path, reason}`) before raising `PermissionViolation`. The wrapping `_run_agent` continues to emit `agent_blocked_on_permission` — gate-level + agent-level audit are complementary.

`_PATH_BLOCKLIST_SUBSTRINGS` in `codec_agent_plan.py` is the auto-extract blocklist for paths the user types in a plan description. Extended in PR-1D to cover the full security-sensitive `~/.codec/` set:

  `/.ssh`, `/.aws`, `/.gnupg`, `/.config/gh`, `/Library/Keychains`, `/Library/Application Support/com.apple`, `/.codec/secrets`, `/.codec/auth`, `/etc/`, `/var/`, `/private/`, `/System/`, `/usr/local/etc`, **`/.codec/skills`, `/.codec/plugins`, `/.codec/oauth_state.json`, `/.codec/audit.log`, `/.codec/agents`, `/.codec/agent_global_grants.json`, `/.codec/config.json`, `/.codec/memory.db`**.

**Segment-aware matching (D-16 closure).** Each entry is matched as a sequence of consecutive `/`-separated path SEGMENTS — not raw substring. So `~/Documents/notes_ssh/foo.md` no longer false-positive-matches `/.ssh` (the segment is `notes_ssh`, not `.ssh`), but `~/.ssh/config` does. Sequence matching also covers multi-segment entries like `/Library/Application Support/com.apple/<x>`.

**Defense in depth.** Even if an attacker convinces the LLM to draft a plan grant for `~/.codec/skills/**`, the blocklist prevents auto-extraction during plan drafting; the realpath gate catches it at runtime; PR-1A's load-time AST gate catches it at skill load; PR-1C's `file_write` block-roots catches it at the file_write call. Four independent layers — each closes the D-1 chain on its own.

### `file_write` skill path-blocking (Phase 1 Wave 1, PR-1C — closes D-4)

The MCP-exposed `file_write` skill (`skills/file_write.py`) refuses writes to security-sensitive paths. This is a defense-in-depth layer paired with PR-1A's load-time AST gate: even if `file_write` reached a skill directory, the file wouldn't execute on load — but PR-1C closes the write at the source.

**Blocked paths (case-insensitive, realpath-resolved):**
- The entire **`~/.codec/`** tree — covers `skills/`, `plugins/`, `auth/`, `oauth_state.json`, `audit.log`, `config.json`, `memory.db`, `agents/<id>/*`, `notifications.json`, `pending_questions.json`, `agent_global_grants.json`, `triggers_killed.json`, plus any future state file added by later phases.
- The repo's built-in `<repo>/skills/` directory — protects the hash-pinned trusted manifest from PR-1A.
- The macOS system tree — `/System`, `/Library`, `/usr`, `/bin`, `/sbin`, `/etc`, `/var`, `/dev`, `/Volumes` (realpath-resolved so `/etc → /private/etc` aliases work).

**Audit emission:** every refused write emits a `file_write_blocked` audit event (`source=codec-skill-file-write`, `outcome=error`, `level=warning`, `extra={target_path, requested_path, reason}`) to `~/.codec/audit.log`. Forensic visibility for any MCP-client attempt at a sensitive path.

**Path resolution:** the parent directory is realpath-resolved before the blocklist comparison so symlink-into-blocked-root traversal attempts can't slip through. `/tmp` and `$HOME` are also realpath-resolved in the final sanity check (this fixed a pre-existing bug where macOS's `/tmp → /private/tmp` alias was tripping the `/private` block).

**Contributor note:** users edit `~/.codec/triggers.json` or `~/.codec/config.json` through the dashboard PWA or a direct editor, NOT through `file_write`. Since P-006 (Oct 2026) `codec_write_policy.check_write` is an allowlist on top of this blocklist, shared by `file_write`, the chat save-file route (`routes/upload.py`) and the crew `file_write` tool: writes go only to `~/Documents`, `~/Desktop`, `~/Downloads`, `~/Projects`, `~/codec-workspace` and `/tmp`, never into `~/Documents/Claude/Obsidian/_system`, and never to a file ending `.sh`, `.mjs`, `.command` or `.plist`. Widening the list is the owner's call.

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

### Vibe projects (2026-10, docs/VIBE-PROJECTS-DESIGN.md)
A Vibe project is a folder `~/codec-workspace/vibe/<id>/` (`CODEC_VIBE_ROOT` overrides it) with a hidden `.vibe/` folder: `project.json`, `checkpoints.jsonl` (one line per checkpoint: id, kind, label, file -> sha256) and `objects/<sha256>` (every file version, stored once). `codec_vibe_projects.py` is the engine, `routes/vibe_projects.py` the HTTP layer (`/api/vibe/projects/...`), `static/vibe-files.js` + `static/vibe-projects.js` the page side. No git: customer Macs may not have it.
- **Every AI change is applied as files behind a checkpoint** taken before and after (`apply_blocks`). Restore (`restore`, `undo_last_ai`) writes the old files back and takes a new checkpoint; history is never rewritten and nothing is deleted, so a restore can be undone. A hand edit made after an AI change is saved first (`before_restore`).
- **Paths:** `safe_rel` refuses `..`, absolute and `~` paths, `.vibe`, `.git`, `node_modules`, secret-looking names (`.env*`, `credentials*`, keys, `.pem`), links, more than 8 levels, 1 MB per file, 200 files per project, 50 files per reply. Every write goes through `_write_bytes_locked`; keep it the only writer. A damaged history cannot write outside the project: `restore` re-checks every path.
- **Delete moves the folder to `~/.codec/trash/vibe/`** (`CODEC_VIBE_TRASH`); nothing is erased.
- **Kill switch:** `VIBE_PROJECTS_ENABLED=false` makes every project route answer 404 and the page falls back to single-file Vibe.
- **Audit:** `vibe_checkpoint`, `vibe_apply`, `vibe_restore`, `vibe_project_trash`, metadata only (docs/codec-architecture.md).
- **Show or run on this Mac (`codec_vibe_runner.py`, `routes/vibe_runner.py`, step 2b):** `serve` is a read-only file server on `127.0.0.1:5300-5399`; `run` starts a command with `bash -c` in the project folder. **The command that runs is the string in the owner's request** (typed or confirmed in the page's dialog): never read it from `vibe.json`, `package.json` or any file the AI could write, and never start anything from a skill, an agent or a timer. One live thing at a time, own process group, `PORT`/`HOST` set, secret-looking env removed, `is_dangerous` as a typo catcher, stopped on request / after 30 minutes without the page asking / when replaced; `.vibe/run.json` lets the next start reap a leftover. All four routes answer 403 to anything not from this Mac. Audit: `vibe_project_run`, `vibe_project_stop` (ports and lengths only, never the command). The dashboard CSP has `frame-src 'self' http://127.0.0.1:*` for the preview frame; do not widen it.
- **Chat on its own route (`codec_vibe_chat.py`, `routes/vibe_chat.py`, `static/vibe-chat.js`, P-042, D-22, docs/VIBE-CHAT-ROUTE-DESIGN.md):** Vibe's chat mode sends to `POST /api/vibe/chat`, not `/api/chat`. **Vibe chat never uses the general chat's persona, memory, skills or web:** the route builds one system prompt (the Vibe prompt, written on the server; a `system` message from the page is dropped), calls `codec_llm` and nothing else, and imports none of `routes.chat`, `codec_dispatch`, `codec_memory`, `codec_search`, `codec_observer` (a test reads the imports). Local model by default through `codec_cloud_models.local_only` (not edited); a registered cloud entry only for `model: "cloud"` plus `confirm_cloud: true` (exactly `true`), after `block_message` (price, monthly cap, key); a base URL that is neither a registered cloud entry nor on this Mac or network is refused with 409 (fail closed). The page asks with the agent's confirm dialog before every message sent to the cloud model (`VibeAgent.confirmChatCloud`; the model list next to the mode switch is shared by chat and agent). The SSE shape is unchanged: `data: {"token": ...}` frames and `data: [DONE]` (plus `stats`, which the page ignores); `<think>` blocks are removed on the server. `vibe.db` stores the typed text of a user turn (`VibeChat.stored(hist)`), not the `[PROJECT FILES]` dump. **Rollback switch:** `config.json` `vibe.chat_route` = `"chat"` makes the route answer 404 and the page send to `/api/chat` again (with `tools: false`, `temporary: true`, `web: false`); read per request, no restart. The cloud rule does not apply on that path. Audit: `vibe_chat_sent`, counts and booleans only (docs/codec-architecture.md). The prompt text lives in two places (`codec_vibe_chat.py` and `SYSP` in `codec_vibe.html` for the rollback path); `tests/test_vibe_chat_page.py` compares them word for word, so change both together.
- **Coding agent (`codec_vibe_agent.py`, `codec_vibe_agent_proto.py`, `routes/vibe_agent.py`, step 3a, docs/VIBE-AGENT-DESIGN.md):** one background thread per run, one run at a time on the Mac. The model replies with one sentence and one `@@ tool` block of plain text (list, read, search, edit, write, delete, ask, finish); the parser is `codec_vibe_agent_proto`. Every file change goes through `codec_vibe_projects.agent_write` / `agent_delete` (which also refuse `.vscode`, `.idea`, `.husky`, `.devcontainer`, `.github`, `.mcp.json` and `.claude`, while the owner's own edits through `safe_rel` still can: D-23, docs/VIBE-SANDBOX-NARROW-DESIGN.md) and then a checkpoint tagged with `run` and `step`; while a run holds the project (`agent_claim`) every other writer (`write_file`, `apply_blocks`, `restore`, `undo_last_ai`, `trash_project`) answers 409. Local model by default (`codec_cloud_models.local_only`, so a chat switched to the cloud does not move it); a cloud run needs `confirm_cloud: true` in the request. File and result text is data: results are wrapped in `[RESULT n ...]` markers the text cannot forge. Limits in `config.json:vibe_agent` (`max_steps` 30, `max_seconds` 900, `max_context_tokens` 10000, `max_reply_tokens` 3000, `wait_seconds` 600). Run logs: `<project>/.vibe/runs/<run>.jsonl` (newest 20 kept). Kill switch `VIBE_AGENT_ENABLED=false` (404). Audit: `vibe_agent_start|step|blocked|end`, counts and names only. Page side (step 3b): `static/vibe-agent.js` (Chat | Agent switch, run card, long polling of `GET .../agent/{run}?since=N&wait=20`); its event handling is plain functions tested with Node in `tests/test_vibe_agent_page.py`; every run text goes in with `textContent`, never HTML. While a run holds the project, `VibeProj.setBusy(true)` makes the editor read-only and stops the page saving the owner's edits; `VibeProj.reload/activate/restoreId/diffNode` are what the agent script calls. Commands (step 3c, `codec_vibe_agent_cmd.py`): `run_check` is offered only to a run started at this Mac and only where `/usr/bin/sandbox-exec` exists; the owner approves the exact text (Allow once, Always allow here, Decline) before anything runs, and `check_command` (the step 2b danger check) refuses before anyone is asked. A command runs in a sandbox profile written for it: reads the system, the toolchains in the home folder (of `~/.local` only `bin`, `lib` and the pipx and uv interpreter folders) and this project only; writes the project (never `.vibe`, `.git/hooks`, `.git/config` or the `.git` entry itself) and its own temp folder; no network, or when ticked only ports 80/443 and name lookups, never loopback; no Apple Events, clipboard, keychain service, screen, camera or microphone. **Never widen the profile in `profile()` or run a command outside it;** the real-sandbox tests (`tests/test_vibe_agent_cmd.py`) pin each rule. "Always allow" is stored as exact text in `.vibe/project.json:allowed_commands` (max 20; `vp.allow_command` / `forget_command`). A perl watchdog around `sandbox-exec` (outside the sandbox, which lets a process signal only itself) stops the whole process group at the limit plus 5 s even if CODEC died. Approve, list and forget answer 403 to anything not from this Mac. Test commands carry `vibe-run-test`. Live check: `python3 tools/vibe_agent_eval.py --bar` (needs the local model, with no voice call, image job or Auguste run active; it never calls a cloud URL and refuses to start if the active model URL is not on this Mac). `--out FILE` writes the model id, base URL, git sha, time and, per task, steps, seconds, format-failure and failed-edit counts, error and timeout flags and tries (2; the second try sees why the first failed). The bar keys to the original tasks 1 to 12 (at least 6 of the 8 file tasks and all 4 behaviour tasks); tasks added later get ids 13 and up, name the feature they `needs`, and are SKIPPED until it exists, so they never change `--bar`. Go / no-go: GO when tasks 1 to 12 meet the bar on the model that is running, NO-GO otherwise. The baseline is stored outside the repo in `~/Documents/CODEC-private/vibe-eval/` and is valid only for the model id it records; a model switch needs a new one. Later changes to what the model sees run `--bar --baseline FILE` (or `--compare RESULT --baseline FILE`): a task that passed in the baseline counts as regressed only if it fails both tries, and pass counts are compared on tasks 1 to 12 only.
- Tests that start servers or commands carry the marker `vibe-run-test` in the command line so a gate can check that nothing is left running (`pgrep -f "[v]ibe-run-test"`).
- **The Monaco editor is a copy in the repo** (`static/vendor/monaco`, 0.57.0, D-24; provenance in `static/vendor/SOURCES.md`). `tools/vendor_monaco.py <tarball>` builds it from the npm tarball and `--check` proves it unchanged; never edit the files by hand. The dashboard CSP still lists cdnjs on purpose: AI-written Vibe preview pages (srcdoc frames, which inherit it) load libraries from there.

### Buyer app packages (2026-10, docs/BUYER-LOCK-DESIGN.md)
The macOS app installs its Python from `packaging/macos/requirements-lock.txt` only (`bundle_python.sh`: `--require-hashes --only-binary=:all: --no-deps`; CI's macos-14 job does the same). A package a service imports must be in `requirements.txt` and the lock, or listed with a reason in `packaging/macos/optional_imports.json`; `packaging/macos/verify/bundle_imports.py` and `tests/test_buyer_lock.py` check it, and `verify/deps.mjs` imports every one in the built bundle's own interpreter.
- **Licence rule:** permissive or LGPL packages only. Never add pyautogui (mouseinfo, GPLv3+), PyMuPDF/fitz (AGPL), or anything that pulls PyAV (faster-whisper: its wheel bundles GPL libx264/libx265). A test pins that none is in the lock (opencv-python is out too, see Webcam).
- **Webcam** is `codec_webcam.py` (AVFoundation through pyobjc, docs/WEBCAM-AVFOUNDATION-DESIGN.md), used by `routes/media.py`. Never add OpenCV (`opencv-python`, module `cv2`) back: its wheel bundles FFmpeg with GPL libx264/libx265; `tests/test_codec_webcam.py` fails on any `cv2` import. The app needs `NSCameraUsageDescription` and the `com.apple.security.device.camera` entitlement (a test pins both).
- **Paste** is `codec_paste.paste_clipboard()` (Quartz key events, Cmd always released). Do not add pyautogui back.
- **Dictation** uses faster-whisper when installed (the Studio) and otherwise the `whisper-stt` server (`_ServerModel`, `pin_language=en`). **`whisper_server.py`** hands mlx_whisper the file path when `ffmpeg` exists and decoded samples when it does not.
- **Microphone and PDFs (docs/MIC-AND-PDF-DESIGN.md):** `codec_record.py` runs `sox` with the exact old command lines when it is installed and records with sounddevice when it is not; `codec_pdftext.py` runs `pdftotext` first and PDFKit otherwise. Never add PyMuPDF/fitz, never bundle sox, poppler or ffmpeg, and keep sox first (the Studio's wake and F18 paths are hardened around it). `packaging/macos/verify/no_gpl_helpers.mjs` checks a built app.
- **Models** in `packaging/macos/models.json` are pinned to 40-hex commits; `codec_setup.bundled_local_revision()` reads the `qwen-7b-4bit` entry.

### Notes (2026-10, docs/NOTES-DESIGN.md)
The owner's own shelf: Tasks > Notes, and a "Save to Notes" button under each chat reply. `codec_notes.py` is the store, `routes/notes.py` the HTTP layer (`/api/notes`), `static/notes.js` the panel, `CodecShell.notes.save` the shell call the chat page uses.
- **One JSON file per note in `~/.codec/notes/`** (`CODEC_NOTES_DIR` overrides it; `tests/conftest.py` points it at a temp folder). Folder 0700, files 0600, atomic writes. An id is 12 hex digits and every path is built from a checked id (`ID_RE`): never join a request value into a path.
- **Delete moves the file to `~/.codec/notes/.trash/`.** Nothing is erased, and there is no restore button yet.
- **The AI never touches it on its own.** Nothing reads the folder, and the only writer besides the page is the owner's own message: the `notes` skill (chat, voice, bridges) saves "save a note: ..." through `codec_notes.create` and refuses a model-written skill tag (`app` contains `LLM-routed`). No agent, crew, plugin or MCP path reads or writes it. Letting CODEC read the owner's notes needs its own design (privacy, prompt injection).
- **Save by asking (docs/NOTES-BY-ASKING-DESIGN.md):** `skills/notes.py` saves to CODEC Notes by default and to Apple Notes only when the message says "apple notes" or the call comes over claude.ai (MCP, unchanged). "Show my notes" answers with where they are and how many, never what they say. Changing this file means regenerating `skills/.manifest.json`.
- **Audit:** `note_saved`, `note_updated`, `note_deleted` carry the id, the source and a character count, never a title or any text (docs/codec-architecture.md).
- **Page:** the Notes tab starts hidden and shows only after `GET /api/notes?limit=1` answers, so it stays out of sight until `codec-dashboard` has been restarted after a deploy. Titles and text are drawn with `textContent` / `.value`, never as HTML.
- **Kill switch:** `NOTES_ENABLED=false` makes every route answer 404 and the tab stays hidden. Limits: title 120 characters, text 100,000, 2,000 notes.

### Speech: voice 02 (2026-10, docs/CODEC-VOICE-02-DESIGN.md)
`codec_tts.py` decides which voice a speech request asks for. With `tts_clone` in `~/.codec/config.json` (the owner's machine only, nothing in the repo defaults) the callers that read text aloud (purpose `speak`: `routes/tts.py`, the `tts_say` and `timer` skills, `codec_briefing`, `codec_scheduler`, `codec_watcher`, `codec_textassist`) ask Qwen3-TTS Base to clone a reference clip (`~/.codec/voices/codec-02/`). Anything wrong (no clip, an error, 45 s without an answer, a reply that is not audio) falls back once to the Kokoro request the caller built, with one `tts_fallback` audit line (reason and purpose, never the text). Without `tts_clone` every request is exactly the old one.
- **Not in voice 02 at all:** the wake-word replies (`codec_core.speak`, which plays a whole file with `afplay`), the Telegram briefings (their own `briefing_voice`) and the generated session scripts stay on Kokoro, because a clone takes about 3 s before a whole sentence starts. A test pins that `codec_core.py` and `codec_telegram.py` do not import `codec_tts`. The live voice call is phase 2, next section.
- A voice picked by hand in the PWA (any voice other than the configured one) is Kokoro's, as asked. Qwen3-TTS ignores `speed`.
- **The clone only asks the shared TTS server for what cannot hold it up** (found with a real server): the model must already be in the local Hugging Face cache (an unknown model made the server wait on the network for a minute and block every request, Kokoro included), the reference must be a real WAV file, and the text at most 400 characters (`tts_clone.max_chars`; a clone is spoken at about the speed of speech). Anything else goes to Kokoro. Never raise `max_chars` or add a model that is not cached without trying it against a spare mlx-audio server first.
- The dashboard loads the clone model in a background thread at start (`codec_tts.warm_up`); the TTS process (`kokoro-82m`) then holds about 5 GB more. `/api/tts` answers with the header `X-CODEC-Voice: clone` or `kokoro`.
- The samples, seeds, measurements and checks are in `~/Documents/CODEC-private/voice-lab/` and `live-checks/` (not in the repo).

### Speech: voice 02 in the live call (phase 2, 2026-10, docs/CODEC-VOICE-02-LIVE-DESIGN.md)
With `"live"` in `tts_clone.enabled_for` (the owner's machine only; without it the call is exactly what it was) the voice call speaks every output in voice 02, streamed: replies, the greeting, mode acknowledgements, crew and skill results, question announcements.
- **Server:** `codec_tts.astream_clone` streams `POST /v1/audio/speech` with `response_format: "pcm"`, `stream: true`, `streaming_interval` 0.5 (raw 16-bit mono 24 kHz; first sound about 0.55 s). `codec_voice_speech.LiveSpeech` is the call's worker (one per `VoicePipeline`, built on first use by `_live_speech()`): `say_nowait` queues, `say` waits until sent, `drain`, `cancel`. It speaks one piece at a time (the TTS server does one request at a time), merges the pieces waiting behind the first up to `chunk_chars` (280), and `codec_voice.py` keeps the model reading while it speaks (the LLM queue slot is freed when the model has finished, not when the voice has).
- **Fallback ladder:** the clone cannot start a piece (bad status, not audio, no first byte in 8 s, empty) -> that piece is one whole Kokoro file frame, as before; the stream breaks after audio started -> the rest of that piece is dropped; two such failures in a row -> the clone is off for the rest of the call; Kokoro failing as well -> text only. Every failure is one `tts_fallback` audit line with `purpose` `live` (reason only, never the text).
- **Wire format:** the page asks for the stream with `?pcm=1` on the socket URL (`routes/websocket.py` -> `VoicePipeline(client_pcm=...)`); a page opened before this shipped, or any other client, does not send it and keeps getting whole Kokoro files. A whole audio file is one binary frame (unchanged). A streamed chunk is `b"CPCM"` + the sample rate (4 bytes, little endian) + 16-bit little-endian mono samples. `{"type": "audio_reset"}` tells the page to drop its audio: it is the server's answer to every `interrupt` control message (also with voice 02 off), and it is sent when the worker is cancelled by an interrupt.
- **Page:** `static/voice-audio.js` (stamped, Node-tested) plays frames in arrival order, each starting where the last ends on the audio clock, through its own `AudioContext` at 24 kHz (the microphone's stays at 16 kHz). After an interrupt it ignores frames until `audio_reset` (or 1.5 s).
- **Limits:** one speaker at a time (a Speak, timer or briefing during a reply waits for it, and the reverse). Qwen3-TTS ignores `speed`, so Flash mode's faster speech does not apply to voice 02. The clone only applies under the phase 1 protections (cached model, real WAV reference, short pieces). Cancelling closes the HTTP stream, which makes the server stop within one chunk (about 0.7 s measured).
- Wake-word replies and Telegram briefings are not part of this. The measurements, scripts and checks are in `~/Documents/CODEC-private/voice-lab/` (`measure_stream.py`) and `live-checks/`.

### Skill self-improvement
`codec_self_improve.py` drafts skill proposals into `~/.codec/skill_proposals/YYYY-MM-DD/<name>.md`. **Never auto-deploys.** Human review required before promoting to `~/.codec/skills/`.

### Dangerous command guard (voice/chat) — hardened in PR-2G (closes D-6)
`codec_config.is_dangerous(cmd)` flags commands that warrant confirmation (dashboard/session paths) or a hard block (`terminal` skill). PR-2G rewrote it from exact-string matching (which the audit found 45% bypassable) to **normalize-then-layered-categories**:

- **`_normalize_command`** lowercases, strips backslash-escapes (`rm\ -rf` → `rm -rf`), collapses all whitespace, and removes pipe spacing (`x | bash` ≡ `x|bash`).
- **Layers:** legacy `DANGEROUS_PATTERNS` (kept) · dangerous lead-binary after `sudo`/`env`/`\`/`VAR=`/path stripping · sensitive-path access (`/etc/passwd`, `~/.ssh`, `id_rsa`, `~/.codec/`, `oauth_state`, `audit.log`, ...) · pipe-to-interpreter · encoding/eval evasion (`base64 -d`, `eval`, `$(`) · inline `-c`/`-e` exec strings · destructive flags (`-rf /`, `-delete`, `--remove-files`) · network exfil (curl/wget + upload flag) · `echo $SECRET_KEY` env disclosure · `kill` negative-pid · osascript destructive verbs.
- **Bypass rate 45% → 0%** across the 42 red-team variants in `docs/audits/PHASE-1-SECURITY.md`. Pinned by `tests/test_dangerous_command.py` (77 tests, incl. a 25-command safe-list UX guard so the blocker doesn't over-trigger on `ls`/`git status`/`cat README.md`/plain GETs).

**This is a confirmation-trigger heuristic / typo-catcher, NOT a complete security boundary.** The real boundaries are `codec_config._HTTP_BLOCKED` / `_STDIO_BLOCKED` (terminal never reachable over MCP), the Step 3 strict-consent gate (literal-verb confirmation for destructive ops), and `terminal` `SKILL_MCP_EXPOSE=False`. Do not rely on `is_dangerous` as the only gate. `is_dangerous` never raises — malformed input returns `False`.

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
- `pytest` from repo root. **1,300+ tests collected** (live count via `pytest --collect-only` — currently 1,685 collected / 1,386 `def test_` functions across 99 files); all must pass before merge except known pre-existing failures documented in `docs/known-issues.md`
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
- `_HTTP_BLOCKED`, `_HTTP_ONLY_BLOCKED` and `_HTTP_CONSENT_REQUIRED` lists in `codec_config.py` — security boundary (add entries only; never remove)
- `scripts/start_model_server.sh` + `~/.codec/config.json:llm_model` — the launcher for PM2 `qwen3.6` reads the ACTIVE model from config at startup, so a model switch works by writing config and restarting the process (`codec_models.restart_server`). Two invariants: preload by the **hub id** the clients send, never a snapshot path (mlx_vlm caches by exact identifier, so a mismatch double-loads the whole checkpoint); and do NOT re-add `max_memory_restart` — PM2 measures RSS and MLX memory is GPU-backed, so the process reads ~60 MB of RSS while holding 20 GB. `footprint -p <pid>` is the only honest measure.
- Cloud fallback model (`codec_cloud_models.py`, docs/CLOUD-FALLBACK-MODEL-DESIGN.md) — an `extra_models` entry with a non-local `base_url` is a cloud model: its key is a Keychain slot (`key_slot`), its prices and `monthly_cap_usd` drive the hard monthly cap, and `codec_llm` checks it before every call to that URL. `~/.codec/cloud_spend.json` is the spend ledger (atomic write under an flock; delete a month's entry only to reset its cap). `config.json:llm_local_restore` is written by the switch to a cloud model and consumed by the switch back — never hand-edit it while on the cloud. Project-mode planner and runner stay local while switched (Step 8 Q1).
- Create image (`codec_image.py`, `routes/image.py`, `scripts/qwen_image_run.py`, docs/CHAT-CREATE-IMAGE-DESIGN.md) — the chat's Image mode runs Qwen-Image 2.1 (~35 GB) as one process per job, one job at a time; `config.json:image` overrides the model and Python paths. `~/.codec/image_job.lock` names the running runner's pid: `codec_models.set_active` refuses local model loads while it is alive (cloud switches pass) and voice skips its warm-up. Never delete the lock by hand while a runner is alive; a lock with a dead pid is ignored. Results live in `~/.codec/images/YYYY-MM-DD/<job>/`.
- `~/.codec/config.json:model_pm2_process` — which PM2 process a model switch restarts (default `qwen3.6`). Pointing it at the wrong service means switches restart something else and never reclaim memory.
- PM2 process names (real list: `open-codec`, `codec-dashboard`, `codec-autopilot`, `codec-heartbeat`, `codec-dictate`, `codec-hotkey`, `codec-imessage`, `codec-mcp-http`, `codec-overlay`, `codec-telegram`, `codec-watchdog`, plus support: `kokoro-82m`, `qwen3.6`, `whisper-stt`, `cloudflared`, `ava-license`, `ava-proxy`) — break supervision if renamed
- Port assignments in `setup_codec.py` and `~/.codec/config.json` — break multi-machine LAN setup
- Cloudflare tunnel hostnames (configured in `~/.cloudflared/config.yml` as `codec.<your-domain>`) — break PWA access from phone
- `codec_identity.py` operating principles — these are user-facing identity, change with care
- `codec_oauth_provider.py` `ACCESS_TOKEN_TTL` / `REFRESH_TOKEN_TTL` — currently 30d / 90d. Shortening these breaks live claude.ai MCP connections mid-week
- `~/.codec/oauth_state.json` — clearing this invalidates ALL claude.ai connections; touch only after explicit user OK + `pm2 restart codec-mcp-http`
- `~/.codec/mcp_clients.json` / `~/.codec/mcp_revoke.json` (UI P3.9, docs/P3.9-DESIGN.md) — how Settings > Connectors signs out ONE app without touching the OAuth state. `codec-mcp-http` writes the first (app names, sign-in hosts, times, last-8 token ids; never a token or secret); the dashboard writes the second (`{client_id: {at, tokens}}`), and the provider removes that app's listed tokens and any issued before `at` before every token check. Tokens issued later are kept, so the file may stay. Never edit the OAuth state from the dashboard process: `codec-mcp-http` holds it in memory and would write it back.
- Keychain entry `ai.avadigital.codec.internal_token` (Phase 1 Wave 2, PR-2D) — deleting forces a full token regen across every CODEC daemon. Heartbeat / scheduler / skills miss for ≤30s while their cache misses; `internal_token_autogenerated` audits emit. Use only for rotation. The legacy `X-Internal: codec` literal header is no longer recognized anywhere in `AuthMiddleware` — restoring it would re-open D-11.
- Keychain entry `ai.avadigital.codec.audit_hmac_secret` (Phase 1 Wave 2, PR-2E) — backs HMAC-SHA256 signing of `~/.codec/audit.log`. Deleting it forces a fresh bootstrap on next write; subsequent `verify_audit_log()` will count lines signed by the OLD secret as `broken` (stdlib WARNING at bootstrap time tells the operator). Rotate only as part of a planned forensic cycle (export the old log first via the verify utility). NEVER call `codec_audit.log_event` from inside `codec_keychain.get_audit_hmac_secret()` — circular write path → deadlock on the non-reentrant `_LOCK`. The silent bootstrap path in PR-2E exists exactly to avoid this.
- Keychain entries `ai.avadigital.codec.{gemini_api_key, pexels_api_key, serper_api_key, telegram_bot_token}` (Phase 1 Wave 2, PR-2B-2) — provider secrets migrated from `config.json` plaintext. Deleting one makes the corresponding getter fall back to cfg (now blanked) then the env var; if neither is set the feature using it (vision via Gemini, Pexels images, Serper search, Telegram bridge) silently degrades. To rotate: `security delete-generic-password -s ai.avadigital.codec.<key>` then re-add via config + restart (the getter re-migrates on first call). The nested `telegram.bot_token` blanking preserves sibling keys (`chat_id`, `allowed_chat_ids`) — never hand-edit the `telegram` block mid-migration.
- `~/.codec/plugins.allowlist` (Phase 1 Wave 2, PR-2F) — SHA-256 allowlist that gates plugin loading. Deleting it forces every plugin in `~/.codec/plugins/` to fall through grandfather migration on next scan, which re-adds them all with `approved_by: "initial_migration"` — effectively a "trust everything currently in the dir again" reset. Use cautiously: if a malicious file is in the plugins dir at the time of deletion, the migration grandfathers it too. Atomic-write contract — never hand-edit; use the `plugin_approve` skill (operator-only, `SKILL_MCP_EXPOSE=False`) or `codec_hooks.approve_plugin(filename)`. File perms enforced at 0600.
- `~/.codec/config.json:plugin_hook_timeout_ms` (Phase 1 Wave 2, PR-2F) — default 500ms. Lower than ~50ms and legitimate hooks (notably the synchronous body of `self_improve.py`'s `on_operation_end`, which does a snapshot + daemon-thread spawn) start tripping the timeout. Higher than ~2000ms reintroduces DoS risk via a malicious hook blocking the calling thread. Keep in the [100, 1000] band unless surfaced.
- `~/.codec/audit.log` (Phase 1 Wave 2, PR-2E) — tampering with existing lines or appending forged lines is detected by HMAC verification (`verify_audit_log()` / `audit_verify` skill). Whole-line deletion is **NOT** detected by per-line HMAC (documented limitation of Q5=a — hash-chain considered too fragile on rotation). File perms enforced at 0600. Direct edits via shell are technically possible but observable post-hoc; do not edit by hand.
- `<project>/.vibe/project.json:allowed_commands` and `codec_vibe_agent_cmd.profile` (Vibe agent step 3c) — the first is the commands the owner allowed for good (exact text + web flag), written only by `codec_vibe_projects.allow_command` / `forget_command` on the owner's click, never by a skill or an AI; the second is the sandbox every agent command runs in. Widening either, or adding a way to run a command without the owner's click, needs the owner's explicit word.
- `tts_clone` in `~/.codec/config.json` and `~/.codec/voices/codec-02/` (2026-10, docs/CODEC-VOICE-02-DESIGN.md) — the owner's chosen voice and its reference clip. Changing the clip changes the voice for every reader, and deleting it makes CODEC fall back to Kokoro (never an error). `live` in `enabled_for` turns on the streamed voice in the call (phase 2); `tts_clone.live` holds optional `interval` (0.5), `first_byte_timeout` (8), `idle_timeout` (6), `chunk_chars` (280) and `breaker_failures` (2), kept inside safe bounds by `codec_tts.live_settings`. Never raise `chunk_chars` above `max_chars` or lower `interval` below 0.25 without measuring on the real TTS server.
- `codec_voice_speech.py` and the `CPCM` frame (phase 2) — the speaking worker and the frame layout are shared by the server and `static/voice-audio.js`; change both sides and `tests/test_voice_page_audio.py` together. Never let the worker run two pieces at once (the TTS server serialises them anyway) or send a request longer than `chunk_chars`.
- `<project>/.vibe/runs/` (Vibe coding agent run logs, one jsonl per run) — written only by `codec_vibe_agent`; never let a skill or an AI write there (`safe_rel` blocks it). The 409 hold in `codec_vibe_projects` (`agent_claim`, `_refuse_during_agent_run`) keeps two writers out of one project; do not bypass it.
- `<project>/.vibe/` inside a Vibe project (2026-10) — settings, checkpoint history and stored file versions. Never edit by hand and never let a skill or an AI write there (`safe_rel` blocks it). Deleting `objects/` makes older checkpoints unrestorable. `~/.codec/trash/vibe/` holds deleted projects; empty it only on the owner's word.
- `packaging/macos/requirements-lock.txt` and `optional_imports.json` (2026-10, docs/BUYER-LOCK-DESIGN.md) — the buyer app's exact package set. Regenerate the lock with the command in its header (keep `-c` on the old lock so existing pins do not move); never drop `--require-hashes` or `--only-binary`; never add a GPL/AGPL package or one that bundles GPL libraries. Rebuild and the Mac Air test come before any DMG.
- `~/.codec/notes/` (2026-10, docs/NOTES-DESIGN.md) — the owner's own notes, one file per note; `.trash/` holds the deleted ones. Edit only through `codec_notes.py`. The only skill that writes there is `notes`, for the owner's own "save a note" message; never let an agent, a plugin, an MCP tool or a model-written skill tag read or write there, never log a title or any note text, and empty the trash only on the owner's word.
- `config.json:retention`, `~/.codec/backup_status.json`, `~/.codec/backups/` and `tools/codec_restore.py` (2026-10, docs/BACKUP-RETENTION-DESIGN.md) — retention numbers decide what the heartbeat deletes (defaults are the owner's decision D-38); never lower a default, skip the checked copy before a clean-up, or make restore reachable from a skill, an agent or MCP. Never let a restore write plugins.allowlist. Delete backups only on the owner's word.
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

This overrides the global execution threshold in ~/.claude/CLAUDE.md rule 5
for this repo — the bar for stopping is lower here on purpose.

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

**Repo:** github.com/AVADSA25/codec
**Maintainer:** AVA Digital LLC
