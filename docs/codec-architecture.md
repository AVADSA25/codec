# CODEC architecture reference

Structure, schemas and event tables, split out of CLAUDE.md so that file holds only
what changes how an agent behaves. Read this when a task needs it — it is not loaded
by default.

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
codec_daybreak.py            Daybreak: morning kickoff briefing + working-threads live memory (threads = temporal facts; docs/DAYBREAK-DESIGN.md)
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
codec_imessage.py            iMessage outbound bridge (thin adapter over codec_bridges)
codec_telegram.py            Telegram outbound bridge (thin adapter over codec_bridges)
codec_bridges.py             Shared outbound-bridge core (A-19): load_dispatch/try_skill, call_llm(channel,…), save_to_memory(channel,…). The "add a channel" surface; process_message stays per-bridge
whisper_server.py            Local STT server
routes/agents.py             /api/agents/* endpoints (custom agents, crew launcher)
routes/_shared.py            notifications.json read/write helpers

skills/                      71 built-in skill modules
~/.codec/                    User config + state (see §7)
docs/                        API.md, MCP_HTTP_SETUP.md, CONTEXT_REPORT.md, design docs
```

Other engine modules (`codec_overlays`, `codec_metrics`, `codec_logging`, `codec_gdocs`, `codec_google_auth`, `codec_cdp`, `codec_llm_proxy`, `codec_retry`, `codec_alerts`, `codec_search`, `codec_textassist`, `codec_watcher`, `codec_watchdog`, `codec_jsonstore` — the canonical cross-process JSON persistence: `atomic_write_json` + `file_lock` (flock), used by the multi-daemon `~/.codec/*.json` writers per PR-4C; `codec_lifecycle` — the canonical graceful-shutdown helper: `install_handlers(cleanup_fn, name)` registers SIGTERM/SIGINT/atexit so PM2 restarts don't force-kill daemons mid-work, used by `codec_autopilot`/`codec_observer`/`codec_agent_runner`/`codec_imessage`/`codec_telegram` per PR-4A-2/H-1) are internal helpers — read them when you need them, but they're not part of the navigation surface for an agent making structural changes. (Keyboard handling — wake word, F13 toggle, F18 voice, double-tap — lives **inline in `codec.py`** in the `codec` PM2 process; the old standalone `codec_keyboard.py` was deleted as a dead duplicate per A-8.)

**Canonical LLM + vision helpers (PR-3E, A-11/A-12).** `codec_vision.py` is the SINGLE source for screen-vision (`describe_sync` / `describe_async`, Gemini-flash → local-Qwen-VL fallback, config read live from `codec_config`) — used by `codec.py`, `codec_voice`, `codec_session`. `codec_llm.py` is the canonical chat/completions caller (`call()` + `strip_think`/`extract_content` — headers, Bearer auth, `enable_thinking`, `<think>` strip, `choices/reasoning` parse, retry+backoff, never-raises). NOTE: `codec_llm_proxy.py` is a priority *queue* (semaphore), NOT an HTTP caller — don't confuse the two. A-12 is migrating the ~45 inline `chat/completions` sites onto `codec_llm` in phased tranches. Done: `codec_llm.call()` (non-stream; + `raise_on_error=True` raising `codec_llm.LLMError` for fail-loud callers) + `stream()` (sync SSE generator, yields raw deltas + an optional `KEEPALIVE` sentinel via `keepalive=True`, never-raises) + async `acall()` (mirrors `call()`, injected httpx client) + `astream()` (mirrors `stream()` + keepalive, but **propagates** exceptions so callers like voice can speak failures); migrated sites = codec.py voice-reply, `codec_session.qwen_call` + `qwen_stream`, `codec_compaction`, `codec_dictate`, `codec_textassist`, the regen script, `codec_agent_plan`/`codec_agent_runner` `_qwen_chat` (adapter maps `LLMError` → their public `QwenUnavailableError`), the `codec_telegram`/`codec_imessage` `call_llm` text sites (default never-raise → `None`-contract preserved; their vision sites are A-11/codec_vision, not migrated here), **the whole dashboard** (the 3 non-stream sites + the chat-handler stream via `codec_llm.stream(keepalive=True)` → `codec_chat_stream.SkillTagBuffer` + non-stream fallback via `call(raise_on_error=True)`), and the **async/queue-coupled** sites `codec_voice._stream_qwen` (`astream`, queue CRITICAL) + `codec_agents.Agent.run` & research-refiner (`acall`, queue MEDIUM). The queue (`codec_llm_proxy`) always stays at the call site — `codec_llm` never owns the semaphore. **A-12 is complete** — also migrated: `codec_self_improve._draft_skill`, `codec_watcher.handle_draft`, and the `translate`/`fact_extract`/`create_skill`/`skill_forge` skills. Every `chat/completions` *text* call site in the repo now routes through `codec_llm`; the only inline LLM POSTs left are vision (handled by `codec_vision` / pending A-11 cleanup, e.g. dashboard + bridge + `skills/screenshot_text` vision sites).

### Core types
- `Tool` (`codec_agents.py:93-110`): `name`, `description`, `fn: Callable[[str], str]` — string in, string out, blocking
- `Agent` (`codec_agents.py:317-358`): `name`, `role` (system-prompt persona), `tools`, `max_tool_calls=5`, `thinking`, `verbose`. The agent loop is ReAct-lite at `codec_agents.py:325-495`, using a text protocol: `TOOL: <name>\nINPUT: <text>` to call a tool, `FINAL: <answer>` to terminate. (PR-3D-a / A-7: the loop body delegates to extracted helpers — `Agent._parse_action` (pure protocol parse), `Agent._validate_tool_call` (pure tool-name/input guards), `Agent._execute_tool_with_hooks` (the `copy_context`+`run_with_hooks`+veto executor). Stuck detection runs inline after the `tool_result` audit.)
- `Crew` (`codec_agents.py:512-573`): `agents`, `tasks`, `mode` (`sequential` | `parallel`), `max_steps=8`, `allowed_tools` (hard tool allowlist enforced at construction)

### Single source of truth: CREW_REGISTRY
**`codec_agents.py:1361-1374`** is canonical for built-in crews. Any new built-in crew gets registered there. Currently 12 crews: `deep_research`, `daily_briefing`, `trip_planner`, `competitor_analysis`, `email_handler`, `social_media`, `code_review`, `data_analysis`, `content_writer`, `meeting_summarizer`, `invoice_generator`, `project_manager`.

Public entry point: `run_crew(crew_name, callback=None, **kwargs)` at `codec_agents.py:1380-1395`.

### Schema
See `codec_memory.py:30-105` for live schema. Tables: `sessions`, `voice_chats`, `conversations`, `corrections`, `agent_goals`, `facts`, plus FTS5 virtual table `conversations_fts` kept in sync via triggers.

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

### Injection points
| File:line | What gets injected |
|---|---|
| `codec._build_voice_system_prompt(task)` | Voice-mode prompt suffix (boot ctx + facts + memory) — extracted from `_dispatch_inner` in PR-3D-b/A-5 |
| `codec_dashboard.py:1827-1862` | Chat handler before LLM call |
| `codec_dashboard.py:1851-1886` | Same handler, separate channel |
| `codec_voice.py:288-320` | VAD speech-start preload |

`[MEMORY]` and `[RECENT MEMORY]` markers are explicitly stripped from agent output by `codec_identity.py`. Agents must not echo raw markers.

### Audit log (`codec_audit.audit()`)
File: `~/.codec/audit.log`, newline-delimited JSON, daily rotation, 30-day retention. Append is thread-safe via `threading.Lock` AND cross-process-safe via `fcntl.flock(LOCK_EX)` on the `audit.log.lock` sidecar (PR-4E/H-3) — the whole rotate-or-write critical section is serialized across all 11 PM2 daemons, so concurrent writes/rotation can't corrupt, split, or interleave entries.

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
| `observation_tick` | `codec-observer` | info | METADATA-ONLY: `active_app`, `active_title_len`, `ocr_chars`, `ocr_skipped`, `clipboard_changed`, `clipboard_kind`, `recent_files_count`, `idle_seconds`, `cadence_used_s`, `buffer_depth`, `poll_duration_ms`, `collector_ms` (2026-07: per-collector durations `{idle, window, clipboard, ocr, files}` — attributes slow polls to a specific collector; durations only, still metadata) |
| `observation_tick_slow` | `codec-observer` | warning | Same as `observation_tick` — emitted instead when `poll_duration_ms > poll_slow_threshold_ms` (default 150ms). Q5.5 flag for visibility, no behavior change. |
| `observation_summary_injected` | `codec-observer` | info | `tokens_used`, `injection_reason` (`always_local`\|`possessive_match`\|`continuation_match`\|`skill_flag`), `buffer_entries_summarized`. `transport` is top-level (reserved). |
| `observer_buffer_inspected` | `codec-dashboard` | info | `client_ip`, `buffer_entries_returned`. Q5.6 PWA `?debug=1` audit. |

`PHASE2_STEP5_EVENTS` frozenset exposed for analyzer breakdown. `observation_tick` is METADATA-ONLY by design — no titles, no OCR text, no clipboard content, no file paths leak to `~/.codec/audit.log`.

### Watchdog events (2026-07 log review)
One event name, emitted by the heartbeat's PM2 restart-storm detector (`codec_heartbeat.check_pm2_restart_storms`). Fires when an `autorestart:true` PM2 process burned ≥5 restarts since the previous heartbeat (~20 min) — the signature of a crash loop hiding behind PM2 status "online" (incident: `ava-litellm` restarted 34,207× over 3 weeks unnoticed). Cron-style jobs (`autorestart:false`) are excluded; a persisting storm re-alerts at most every 6h. State: `~/.codec/pm2_restart_state.json`. Related (no new event): `codec_alerts` supports read-only `alerts.extra_services` probes in `~/.codec/config.json` (`http(s)://` or `tcp://host:port`) with the same consecutive-failure alerting as built-ins but NEVER auto-restart.

| Event | Source | level | extra fields |
|---|---|---|---|
| `pm2_restart_storm` | `codec-heartbeat` | warning | `process`, `delta` (restarts since last heartbeat), `total_restarts` |

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

#### Voice modes event (flash / default / think — docs/VOICE-MODES-DESIGN.md)

| Event | Source | level | extra fields |
|---|---|---|---|
| `voice_mode_changed` | `codec-voice` | info | `mode` (`flash` \| `default` \| `think`), `via` (`voice` \| `ui`) |

Single-emit, fresh or session cid. Think-mode tool calls need no new events — they route through the skill `Tool` wrappers → `run_with_hooks` → existing `tool_call`/`tool_result` envelope.

#### Daybreak events (morning kickoff + working threads — docs/DAYBREAK-DESIGN.md)

Three event names, all info-level, single-emit with fresh cid. `DAYBREAK_EVENTS` frozenset exposed. Thread text never enters audit lines (keys/lengths only).

| Event | Source | level | extra fields |
|---|---|---|---|
| `daybreak_completed` | `codec-daybreak` | info | `sections_included` (0-4), `skipped_sources` (list), `open_threads_count`, `word_count`; `duration_ms` top-level |
| `daybreak_thread_saved` | `codec-daybreak` | info | `kind` (`working_on` \| `waiting_on` \| `priority` \| `follow_up`), `key`, `superseded` (bool), `text_len` |
| `daybreak_thread_closed` | `codec-daybreak` | info | `key`, `rows_expired` |

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

### MCP HTTP transport blocklist
`codec_config._HTTP_BLOCKED`: `python_exec`, `terminal`, `process_manager`, `pm2_control`, `ax_control`. These skills are NEVER exposed over HTTP MCP. They remain available locally (voice, chat) and over stdio MCP only.

### Config schema versioning (A-15)
- `~/.codec/config.json` carries a `config_version` stamp; `codec_config.CONFIG_SCHEMA_VERSION` is the current generation (currently **1**).
- `codec_config.load_config()` runs an ordered migration ladder (`_CONFIG_MIGRATIONS`) on first load after an upgrade, writing back **only** when the file exists AND a migration changed something (idempotent, atomic 0600). It never creates a config file just to stamp a version, and never overwrites an unparseable one.
- **When you add/rename/restructure a config key in a way old configs can't satisfy via `.get(k, default)`**: bump `CONFIG_SCHEMA_VERSION`, append a `_migrate_vN_to_vN+1(cfg) -> cfg` step to `_CONFIG_MIGRATIONS`, and add a round-trip test. Purely additive keys with safe defaults need **no** migration (that's why v0→v1 only stamps the version).
