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
codec_dictate.py             CODEC Dictate: F5 live-typing + draft refinement (one of the 7 products)
codec_agents.py              Agent + Crew runtime (1,468 lines, see §3)
codec_skill_registry.py      Skill discovery + lazy loading via AST parse
codec_dispatch.py            Skill trigger matching for voice/wake-word path
codec_memory.py              SQLite + FTS5 + public API
codec_memory_upgrade.py      Facts table, CCF compression, tiered retrieval
codec_compaction.py          Context compaction — summarize old turns when window fills
codec_audit.py               Structured audit log (see §6)
codec_audit_analyzer.py      Audit summary skill (audit_report)
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

### Known gaps (tracked for Phase 2)
- No `PreToolUse` / `PostToolUse` hooks (being added in Phase 1 Step 2)
- No `AskUserQuestion` tool — agents can't pause to ask the user
- No `stuck` self-detection (repeated identical tool calls)
- No step budget at chat-handler level (only inside crew runs)
- No formal teammate / sub-agent recursion — Crew is the only multi-agent primitive

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

### Notifications (`~/.codec/notifications.json`)
Three sources can produce notifications: scheduler (crew completion), heartbeat (threshold alert), autopilot (ambient trigger). All write through `routes/_shared.py:51-127`.

Schema:
```json
{
  "id": "notif_<hex>",
  "type": "task_report|alert|status",
  "title": "...",
  "body": "markdown",
  "status": "success|warning|error",
  "created": "ISO8601",
  "read": false,
  "schedule_id": "sched_<id> | null",
  "doc_url": "https://... | null"
}
```

API endpoints in `codec_dashboard.py`: `GET /api/notifications`, `GET /api/notifications/count`, `POST /api/notifications/read-all`, `POST /api/notifications/{id}/read`, `DELETE /api/notifications/{id}`. Frontend polls `/api/notifications/count` every ~30s.

## 7. Sandbox + safety boundaries

### Files & directories
- `~/.codec/` — all user state (config, memory, audit, schedules, notifications, agents, skills, proposals)
- File operations from agent tools go through `codec_sandbox` (path validation against blocklist, size caps)
- Dangerous code patterns detected by `codec_config.is_dangerous_skill_code` before any skill is staged from `codec_self_improve.py` proposals

### MCP HTTP transport blocklist
`codec_config._HTTP_BLOCKED`: `python_exec`, `terminal`, `process_manager`, `pm2_control`, `ax_control`. These skills are NEVER exposed over HTTP MCP. They remain available locally (voice, chat) and over stdio MCP only.

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
