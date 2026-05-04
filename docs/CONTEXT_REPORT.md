# CODEC — Context Report

**Generated:** 2026-04-30 · **Repo:** [github.com/AVADSA25/codec](https://github.com/AVADSA25/codec) at HEAD `0cf61a1`
**Scope:** read-only audit of agent/crew architecture, memory, skills, audit log, scheduler, hooks, and `AGENTS.md` placement. **No code modified during this audit. No `AGENTS.md` written.**

> Brand note: as of April 29 2026 (`2b45641`), the product brand is **Sovereign AI Workstation**; **CODEC** is the open-source engine codename — what you see in code paths, `~/.codec/` config, `codec_*.py` modules, and PM2 process names. Same pattern as iPhone (product) / Darwin (engine codename). This report uses **CODEC** throughout because every concrete file/path/identifier is named that way in the codebase.

---

## 0 · Existing orientation files

```text
README.md                     Sovereign AI Workstation pitch + setup. Brand-front-door.
CHANGELOG.md                  Reverse-chronological version notes.
CONTRIBUTING.md               Contributor onboarding (mentions dual brand naming).
FEATURES.md                   245 features · 60 skills · 378 tests · ~34K LOC.
PHOTON_DEMO.md                Marketing demo writeup.
docs/API.md                   Dashboard HTTP API reference.
docs/MCP_HTTP_SETUP.md        How the OAuth-protected MCP HTTP transport is wired.
```

**No `AGENTS.md`, `CLAUDE.md`, or `GEMINI.md` currently exists** in the repo. Recommended placement: see §7.

---

## 1 · Agent + Crew architecture

CODEC ships its own minimalist multi-agent runtime (`codec_agents.py`, **1,468 lines**) modeled on CrewAI's "agents + sequential/parallel tasks" pattern but with **zero dependency on CrewAI or LangChain**. The runtime is self-contained — only depends on `requests` for the LLM call and `codec_skill_registry` for tool loading.

### 1.1 Core dataclasses

`codec_agents.py:93-110` — **`Tool`**
```python
class Tool:
    name: str
    description: str
    fn: Callable[[str], str]   # str -> str, blocking
```

`codec_agents.py:317-358` — **`Agent`**
```python
class Agent:
    name: str
    role: str                              # the system-prompt persona
    tools: List[Tool] = []
    max_tool_calls: int = 5
    thinking: bool = False
    verbose: bool = True

    async def run(self, task, context="", callback=None) -> str: ...
```
The agent loop ("ReAct-lite") is implemented in `Agent.run` at `codec_agents.py:325-495`. It builds a system prompt that teaches an explicit text protocol — `TOOL: <name>\nINPUT: <text>` to call a tool, `FINAL: <answer>` to terminate — then loops until either `FINAL:` is emitted, `max_tool_calls + 3` rounds elapse, or the loop budget is exhausted (`codec_agents.py:357`).

`codec_agents.py:512-573` — **`Crew`**
```python
class Crew:
    agents: List[Agent]
    tasks:  List[str]
    mode: str = "sequential"      # "sequential" | "parallel"
    max_steps: int = 8
    allowed_tools: Optional[List[str]] = None  # tool-name allowlist; None = no restriction
```
`Crew.__post_init__` (`codec_agents.py:521-530`) enforces the allowlist by stripping any `Agent.tools` entries not in `allowed_tools` *at construction time* — i.e., crew-level tool scoping is hard, not advisory.
`Crew.run()` at `codec_agents.py:531-574` supports two execution modes:
- **sequential** — pairs agents with tasks (zipped, capped at `max_steps`), feeds previous agent's output as `context` to the next.
- **parallel** — `asyncio.gather` over all `(agent, task)` pairs, results joined with `\n\n---\n\n`.

### 1.2 Crew registry (12 built-in crews)

`codec_agents.py:1361-1374` — **`CREW_REGISTRY`** is the single source of truth:

| Crew name | Builder fn | Description |
|---|---|---|
| `deep_research` | `:676` | Comprehensive web research → Google Docs |
| `daily_briefing` | `:741` | Morning briefing: calendar, weather, news |
| `trip_planner` | `:806` | Plan a trip: research + itinerary → Google Docs |
| `competitor_analysis` | `:842` | Competitive analysis: web research → report |
| `email_handler` | `:876` | Read, categorize, and draft email replies |
| `social_media` | `:910` | Platform-specific social posts |
| `code_review` | `:964` | Bugs, security, quality |
| `data_analysis` | `:1016` | Data analysis on any topic |
| `content_writer` | `:1056` | Blog/articles/newsletters → Google Docs |
| `meeting_summarizer` | `:1109` | Meeting → action items → Google Docs + Calendar |
| `invoice_generator` | `:1192` | Natural-language → professional invoice → Google Docs |
| `project_manager` | `:1284` | Project status from Calendar/Gmail/Drive/Tasks → Google Docs |

Public entry-point is `run_crew(crew_name, callback=None, **kwargs)` at `codec_agents.py:1380-1395`. `kwargs` are passed to the crew builder, which constructs `Agent` + `Crew` objects bound to a curated tool allowlist.

### 1.3 Built-in tool surface

Defined inside `codec_agents.py:113-307` (callable from any crew agent):

| Function | Tool name | Source |
|---|---|---|
| `_web_search(query)` | `web_search` | DuckDuckGo HTML scrape |
| `_web_fetch(url)` | `web_fetch` | `requests.get` with HTML→text |
| `_file_read(path)` | `file_read` | sandboxed via `codec_sandbox` |
| `_file_write(input)` | `file_write` | sandboxed (path + content from a single string) |
| `_google_docs_create(input)` | `google_docs_create` | wraps `codec_gdocs.create_doc` |
| `_shell_execute(cmd)` | `shell` | sandboxed shell wrapper |

Skills already loaded by the global `SkillRegistry` are also exposed as tools by `_make_lazy_fn` + `load_skill_tools()` (`codec_agents.py:279-307`), so any registered skill auto-becomes available to agents.

### 1.4 Custom agent persistence

Custom (user-defined) agents have a separate, lighter path in `routes/agents.py`:

| Endpoint | Method | What it does |
|---|---|---|
| `/api/deep_research` | POST | Convenience launcher for the deep_research crew. |
| `/api/deep_research/{job_id}` | GET | Poll a deep-research job by id. |
| `/api/agents/crews` | GET | List `CREW_REGISTRY` entries with descriptions. |
| `/api/agents/run` | POST | **Generic crew launcher.** Body: `{crew, ...kwargs}`. Returns `job_id` immediately; runs in a background thread (`routes/agents.py:74-105`). |
| `/api/agents/status/{job_id}` | GET | Poll the in-memory `_agent_jobs` dict. |
| `/api/agents/tools` | GET | `get_all_tools()` — full tool catalog. |
| `/api/agents/custom/save` | POST | Persists a custom agent JSON to `~/.codec/agents/<safe_id>.json`. **No schema validation beyond `name` required.** |
| `/api/agents/custom/list` | GET | Reads every `.json` under `~/.codec/agents/`. |
| `/api/agents/custom/delete` | POST | Removes the JSON file. |

Custom-agent storage is a flat directory of JSON files keyed by slugified name. Live state of running jobs lives in `_agent_jobs` (in-memory `dict[str, dict]`, not persisted) — restarting `codec-dashboard` loses progress logs of any in-flight job.

### 1.5 Voice-side crew dispatch

`codec_voice.py:682-721` — `_CREW_TRIGGERS` dictionary maps spoken phrases to crew names. `dispatch_crew_from_voice(user_text)` at `codec_voice.py:723-755` matches a trigger, builds args via `arg_builder(text)`, runs the crew via `run_crew`, and streams progress callbacks back to the browser as TTS. Examples: `"deep research"` → `deep_research`, `"morning briefing"` → `daily_briefing`, `"plan a trip to"` → `trip_planner`, etc.

### 1.6 What the agent runtime does NOT have (gaps)

- **No PreToolUse / PostToolUse hooks** — agents can't be intercepted around tool execution. See §6.
- **No step budget at chat-handler level** — `Agent.max_tool_calls` is enforced inside `Agent.run`, but the dashboard `/api/chat` flow has no equivalent (a chat turn currently fires at most one skill via the `[SKILL:...]` tag regex; multi-step is achieved via crew runs only).
- **No `AskUserQuestion` tool** — agents can't pause to ask the user mid-run.
- **No `stuck` self-detection** — repeated identical tool calls aren't flagged.
- **No formal teammate / coordinator / sub-agent spawning** — Crew is the only multi-agent primitive; agents don't recursively spawn agents.
- **No structured / SSE streaming of tool execution to the dashboard chat** — voice has it via WebSocket; chat does not.

These are tracked in `~/ava-stack/docs/PHASE2-design-specs.md` as Specs 1, 2, 3 (ask_user / stuck / step budget).

---

## 2 · Memory system

CODEC's memory is a **single SQLite database at `~/.codec/memory.db`** (currently 2.7 MB, 3,616 conversation entries, 455 sessions) with FTS5 full-text search. The wrapper class is `codec_memory.CodecMemory` at `codec_memory.py:28-369`.

### 2.1 Schema (live, exact)

```sql
-- sessions: one row per agent/conversation session
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    task TEXT,
    app TEXT,
    response TEXT,
    user_id TEXT DEFAULT 'default'
);

-- voice_chats: legacy voice transcript storage
CREATE TABLE voice_chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    speaker TEXT,
    message TEXT
);

-- conversations: canonical "what was said" log (used by FTS)
CREATE TABLE conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    timestamp TEXT,
    role TEXT,                 -- "user" | "assistant" | "system"
    content TEXT,
    user_id TEXT DEFAULT 'default'
);

-- corrections: user-flagged "you got that wrong" pairs
CREATE TABLE corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    original TEXT,
    corrected TEXT,
    context TEXT,
    user_id TEXT DEFAULT 'default'
);

-- agent_goals: KV-by-sender for stateful agent goals
CREATE TABLE agent_goals (
    sender TEXT PRIMARY KEY,
    data TEXT,
    updated_at TEXT
);

-- facts: temporal key/value store (codec_memory_upgrade.py)
CREATE TABLE facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    fact_type TEXT DEFAULT 'generic',
    confidence REAL DEFAULT 1.0,
    valid_from TEXT NOT NULL,
    valid_until TEXT,           -- NULL = current
    superseded_by INTEGER,      -- FK to facts.id of replacement
    user_id TEXT DEFAULT 'default',
    source TEXT
);

-- FTS5 virtual table over conversations.content
CREATE VIRTUAL TABLE conversations_fts USING fts5(
    content, session_id, timestamp, role,
    src_id   UNINDEXED,
    user_id  UNINDEXED
);
```

Plus three **triggers** (`codec_memory.py:84-105`) that keep `conversations_fts` in sync with the `conversations` base table on INSERT / UPDATE / DELETE — so FTS is implicitly maintained.

Indexes (`codec_memory.py:74-77`, `codec_memory_upgrade.py:86-88`):
```
idx_conv_session, idx_conv_ts, idx_conv_user, idx_sessions_user,
idx_corrections_user, idx_facts_key, idx_facts_valid, idx_facts_user
```

### 2.2 Tiered retrieval (codec_memory_upgrade.py)

Header at `codec_memory_upgrade.py:1-15` describes a 3-layer retrieval model:

| Layer | What | Cost |
|---|---|---|
| **L0/L1** — `identity.txt` | Always-loaded boot payload (<200 tokens). Keeps "who is this user / what are persistent preferences" in every prompt. | 0 (in-memory) |
| **L2** — recent rooms | Last N sessions from `conversations`, role-aware. | 1 indexed query |
| **L3** — deep FTS search | On-demand FTS5 query over full history with relevance ranking. | 1 FTS query (sub-200ms typical) |

Plus a **"facts" table** with temporal validity (`valid_from`, `valid_until`, `superseded_by`) — supports `valid_at(timestamp)` queries, enabling time-travel ("what value did the user choose for setting X on April 14"). And **CCF (Conversational Context Fragmentation)**, a rule-based compressor for memory writes that need shrinking — entity abbreviation + filler stripping.

### 2.3 Public API of `CodecMemory`

`codec_memory.py:179-369`:

```python
def save(session_id, role, content, user_id="default") -> int
def search(query, limit=10, user_id=None) -> list[dict]            # FTS rank order
def search_recent(days=7, limit=50, user_id=None) -> list[dict]    # by timestamp
def get_context(query, n=5, user_id=None) -> str                   # joins relevance + recent
def get_sessions(limit=20, user_id=None) -> list[dict]
def cleanup(retention_days=90) -> dict                             # DELETE old rows
def rebuild_fts() -> int                                           # repair FTS index
def close() -> None
```

FTS5 query strings are sanitized at `codec_memory.py:16-25` — `_sanitize_fts_query` strips operators (`NEAR/AND/OR/NOT`), special chars (`*"()^`), and clamps length to 200 chars to prevent FTS injection.

### 2.4 Memory injection points (where it gets prepended to LLM prompts)

| File:line | Variable | What gets injected |
|---|---|---|
| `codec.py:358-362` | `boot_ctx`, `facts_ctx`, `mem`, `mem_ctx` | Voice-mode prompt suffix. |
| `codec_dashboard.py:1827-1862` | `Recent memory injected: 5 messages` | Chat handler before LLM call. |
| `codec_dashboard.py:1851-1886` | `Recent chat memory injected` | Same handler, separate channel. |
| `codec_voice.py:288-320` | (preload during VAD speech-start) | Pre-loads system prompt + recent memory. |

`[MEMORY]` and `[RECENT MEMORY]` tag markers in injected blocks are explicitly redacted from agent output by the system prompt (`codec_identity.py:30-31`, "never echo the raw markers in your output").

---

## 3 · Skill loading + execution flow

CODEC has **123 skills** total: 71 built-ins under `skills/` + 52 user skills under `~/.codec/skills/`. The two directories merge at registry-scan time, with user skills taking precedence on name collisions.

### 3.1 Skill module convention (skills/_template.py)

Every skill is a single Python file with module-level metadata + a `run` function. Required:

```python
SKILL_NAME      = "my_skill"            # short identifier
SKILL_TRIGGERS  = ["phrase one", ...]   # natural-language phrases that fire it
SKILL_DESCRIPTION = "..."               # MCP / dashboard label

# Optional:
SKILL_MCP_EXPOSE = True                 # gate MCP HTTP exposure (default per config)

def run(task: str, app: str = "", ctx: str = "") -> str:
    """Process the task and return a string response."""
    return "..."
```

### 3.2 Registry — `codec_skill_registry.SkillRegistry`

`codec_skill_registry.py:57-195`:

```python
class SkillRegistry:
    def __init__(skills_dir: str)
    def scan() -> int                          # AST-parse only; no module import
    def names() -> List[str]
    def get_meta(name) -> dict                 # SKILL_NAME, _DESCRIPTION, _TRIGGERS, _MCP_EXPOSE
    def get_triggers(name) -> List[str]
    def get_description(name) -> str
    def get_mcp_expose(name) -> Optional[bool]
    def load(name) -> module                   # ACTUAL import — cached after first call
    def run(name, task, *a, **kw) -> Optional[str]
    def match_trigger(task) -> Optional[str]
    def match_all_triggers(task) -> List[str]  # ranked by trigger specificity
```

**Key design choice**: at startup, the registry only `ast.parse`s each `.py` file to extract metadata — **no skill code is executed unless the skill is actually called**. This means broken skills can sit in the directory without breaking startup, and 123 skills load in <100 ms.

### 3.3 Three execution paths

**Path A — voice / wake-word (`codec.py` / `codec_dispatch.py`)**
```
codec.py:wake_word_listener() (line 593)
  → dispatch(text) (line 241)
    → _dispatch_inner(task) (line 254)
      → check_skill(task) [codec_dispatch.py:28-46]   # ranked trigger matching
      → run_skill(skill, task, app) [codec_dispatch.py:47-75]
```

**Path B — dashboard chat (`codec_dashboard.py:/api/chat`)**
- *Pre-LLM hijack:* `_try_skill(user_text)` at `codec_dashboard.py:2081-2110` — if a skill in `CHAT_SKILL_ALLOWLIST` (`codec_dashboard.py:1991`) matches the message **and** the message isn't conversational and has no file attachments, fire the skill directly and skip the LLM. Returns the skill output as if it were the LLM response.
- *Post-LLM tag interpretation:* the LLM is taught (`codec_identity.py:32-34`) to emit `[SKILL:name:query]` when a skill is appropriate. The chat handler's regex `re.search(r'\[SKILL:(\w+):([^\]]+)\]', answer)` at `codec_dashboard.py:2412-2419` parses the tag, runs the skill, and replaces the tag with the result — same shape as if the LLM had inlined the answer.
- *Slash commands (new):* `parse_slash` at `codec_slash_commands.py:55-90` runs **before** both of the above. If the message is `/help`, `/skills`, etc., dispatch returns markdown without LLM round-trip.

**Path C — MCP (HTTP and stdio)**
`codec_mcp.py:87-191` — every skill with `SKILL_MCP_EXPOSE=True` (or in opt-out mode) gets registered as an MCP tool with shape `tool_fn(task: str, context: str = "") -> str`. Each tool runs in a `ThreadPoolExecutor` with a 30s timeout (`SKILL_TIMEOUT_SEC` env-var override). Every call goes through `_validate_mcp_input` (`codec_mcp.py:22-38`) for length caps — task ≤ 5,000 chars, context ≤ 10,000 — and through `codec_audit.audit()` for forensics.

The HTTP transport (`codec_mcp_http.py`) is a thin OAuth-2.1-protected wrapper over the same registry; the strict `_HTTP_BLOCKED` list in `codec_config.py` adds extra safety (currently blocks `python_exec`, `terminal`, `process_manager`, `pm2_control`, `ax_control`).

---

## 4 · Audit log

### 4.1 The "tool audit" — `codec_audit.audit()`

`codec_audit.py:1-82`. Single function, structured-JSON-line append. Schema (per the docstring at `codec_audit.py:6-9`):

```json
{
  "ts": "ISO8601",
  "tool": "skill_or_tool_name",
  "task_len": 42,
  "context_len": 128,
  "duration_ms": 120.5,
  "outcome": "ok | error | validation | timeout",
  "error_type": "TypeError | None",
  "client_id": null,
  "transport": "stdio | http | local"
}
```

Backed by **`~/.codec/audit.log`** (newline-delimited JSON), rotated daily (`codec_audit.py:27-46`) into `audit.log.YYYY-MM-DD`, retained 30 days. Append is thread-safe via `threading.Lock` (`codec_audit.py:24`). Used by:
- `codec_mcp.py` (every MCP tool call)
- `routes/agents.py` (job lifecycle)
- `codec_agents.py:65-91` (`_audit` wrapper for crew steps — `crew_start`, `tool_call`, `tool_result`, `crew_complete`)

Live sample lines:
```json
{"ts": "2026-04-29T15:00:13", "event": "tool_call", "agent": "Writer", "tool": "google_docs_create", "input": "..."}
{"ts": "2026-04-29T15:00:22", "event": "tool_result", "agent": "Writer", "tool": "google_docs_create", "result_len": 104}
{"ts": "2026-04-29T15:00:37", "event": "crew_complete", "mode": "sequential", "elapsed": 226, "result_len": 757}
```

> ⚠️ **Schema drift**: agent runtime lines use `event` and `agent` keys; MCP tool lines use `tool` and `outcome`. Two parallel schemas in the same file. The `codec_audit_analyzer.py` reader (§4.3) accepts both shapes.

### 4.2 The "event log" — fallback `log_event` shim

A second logger surface, `log_event(event_type, source, message, extra=None, level="info")`, is **declared as a fallback** (no-op) in five separate files:

| File:line | Defined as |
|---|---|
| `codec_session.py:25` | `def log_event(*a, **kw): pass` |
| `codec_scheduler.py:14` | same |
| `codec_dispatch.py:12` | same |
| `codec_heartbeat.py:12` | same |
| `codec_dashboard.py:29` | same |

Each tries `from codec_audit import log_event` first (which **does not export `log_event`** — only `audit`); the import fails silently, and the no-op fallback is used. **Net effect: every call site that relies on `log_event` is a no-op today.** This is a real gap — see §6 / §8.

### 4.3 Audit analyzer

`codec_audit_analyzer.py:34-222` — readable summary report. `analyze(records)` at `:71-122` produces percentile stats (p50/p95/p99 latency), error rates per tool, and unknown-tool detection. `_render(date_str, summary)` at `:124-178` produces a human-readable markdown report. Exposed as the **`audit_report`** skill (`run(task, context)` at `:179-222`).

### 4.4 macOS "Notification Center" notifications

Separate from the audit log. Dashboard-side state at `~/.codec/notifications.json` (JSON array, see §5).

---

## 5 · Scheduler ↔ CODEC Overview integration

The scheduler is the bridge between cron-style triggers and the dashboard's notification surface.

### 5.1 Scheduler core — `codec_scheduler.py`

`codec_scheduler.py:1-443`. Cron-like with hour/minute/days-of-week granularity. **Runs as its own PM2 process** today (was `codec-autopilot`); newer code (`codec_dashboard.py:101`) wraps it as a "background service" of the dashboard process for unified lifecycle.

**Storage:** `~/.codec/schedules.json` — JSON array of:
```json
{
  "id": "sched_daily_briefing",
  "crew": "daily_briefing",       // CREW_REGISTRY key
  "topic": "Morning news...",
  "hour": 8, "minute": 0,
  "days": [0,1,2,3,4,5,6],        // 0=Mon..6=Sun
  "enabled": false,
  "last_run":      "2026-04-15T08:01:52",
  "last_attempt":  "2026-04-15T08:00:00",
  "created":       "2026-03-20T09:15:22"
}
```

**Public API** (`codec_scheduler.py:31-444`):
- `load_schedules() -> list`
- `save_schedules(schedules)`
- `add_schedule(...)` — `codec_scheduler.py:46-71`
- `remove_schedule(sched_id)`
- `toggle_schedule(sched_id, enabled)`
- `_run_crew(sched)` — `codec_scheduler.py:139-194`. Resolves crew, calls `run_crew` async, persists notification when complete.
- `check_and_run()` — `codec_scheduler.py:195-249`. The cron tick. Walks schedules, fires those due this minute, idempotent via `last_attempt`.
- `run_daemon(check_interval=60)` — `codec_scheduler.py:280-327`. The main loop.
- `_parse_schedule_intent(task)` — `codec_scheduler.py:329-361`. Natural-language → schedule (e.g. *"daily briefing every weekday at 8am"*).

### 5.2 Scheduler → notifications

**`_notify(title, body, status, schedule_id)`** at `codec_scheduler.py:92-138` is the bridge. After a crew finishes, it appends an entry to `~/.codec/notifications.json` via `_load_notifications` / `_write_notifications` from `routes/_shared.py:51-127`. **The dashboard's notification UI polls this file** (and increments the badge counter at `/api/notifications/count`).

Notification record shape (live sample):
```json
{
  "id": "notif_11f397ea57",
  "type": "task_report",
  "title": "Weekly AI industry analysis",
  "body": "📄 [View Full Report](https://docs.google.com/.../edit)\n...",
  "status": "success",                    // "success" | "warning" | "error"
  "created": "2026-04-27T09:02:48",
  "read": true,
  "schedule_id": "sched_deep_research",   // links back to schedule
  "doc_url": "https://docs.google.com/.../edit"
}
```

### 5.3 Heartbeat → notifications

`codec_heartbeat.py` runs as a background service (`codec_dashboard.py:101`). Every 5 minutes (configurable via `heartbeat_interval`), it:
1. Pings local services (Whisper, Kokoro, LLM, Vision) and writes status into `~/.codec/alert_state.json`.
2. Walks `heartbeat_alerts` config (e.g., `BTC Price > 5%`, `Disk Space > 90%`) and posts notifications when thresholds breach.

Result: **Three sources can produce notifications** — the scheduler (crew completions), the heartbeat (threshold alerts), and `codec_autopilot` (ambient triggers). All write through the same `notifications.json` mechanism.

### 5.4 Notification API endpoints

(In `codec_dashboard.py`):
- `GET  /api/notifications` — paginated list
- `GET  /api/notifications/count` — unread count for the badge
- `POST /api/notifications/read-all` — mark all read
- `POST /api/notifications/{id}/read` — mark one
- `DELETE /api/notifications/{id}` — remove

The dashboard frontend (`codec_dashboard.html`) polls `/api/notifications/count` every ~30s.

---

## 6 · Hooks + plugin extension points

### 6.1 What exists today

A grep across the repo for hook-style patterns (`PreToolUse`, `PostToolUse`, `register_hook`, `HOOK_`, `hook_pipeline`) returns **zero matches**. CODEC has no formal hook system.

What it *does* have, in increasing order of formality:

1. **Skill registry** (§3) — the primary extension point. Drop a `.py` file in `~/.codec/skills/`, restart `codec-dashboard` and `open-codec`, and CODEC picks it up. Every layer (voice / chat / MCP) auto-uses it.
2. **Custom agents JSON files** (`~/.codec/agents/*.json`, §1.4) — one-shot agents with hand-picked tools; runtime-only, no hooks fire around their execution.
3. **Skill marketplace** — `codec_marketplace.py:1-30` provides install / search / publish / list / update / remove / info commands and a `publish` subcommand. Distribution is by hash-pinned files in a community index. Live install via voice: *"Hey CODEC, install bitcoin price skill"*.
4. **Skill self-improvement** — `codec_self_improve.py` is a nightly gap analyzer that replays the audit log and **drafts new skill proposals**. Output: `~/.codec/skill_proposals/YYYY-MM-DD/<name>.md` with rationale + proposed code + validation status. Code is run through `codec_config.is_dangerous_skill_code` before staging. **Critically, it never auto-deploys** — proposals stage for human review.
5. **Slash commands** — `codec_slash_commands.py:1-414` (added 2026-04-29). Registry of `SlashCommand(name, handler, summary, usage, aliases)` objects. Adding a slash command is a one-line append to `SLASH_COMMANDS`. Limited to chat-meta-controls, not agent runtime hooks.
6. **Autopilot** — `codec_autopilot.py:1-30`. Reads `~/.codec/autopilot.json` for ambient triggers ("at sunset, run X"). Single-thread polling every 30s, isolated PM2 process. Closest thing CODEC has to a programmatic event loop, but the trigger types are hardcoded.

### 6.2 What's missing (hook-system gaps)

| Hook type (Claude-Code-style) | CODEC equivalent |
|---|---|
| `PreToolUse` / `PostToolUse` | **none** — tools are called directly with no interception layer |
| `OnSessionStart` / `OnSessionEnd` | partial (PM2 startup/shutdown) — no per-conversation hook |
| `OnError` | partial (`codec_audit.audit(outcome="error")`) — no programmable response |
| `OnSkillResult` mutation | **none** — skill output goes straight into the LLM stream / response |
| Plugin lifecycle (install / enable / disable / hot-reload / configure) | partial — install/list via marketplace, but no enable/disable runtime toggle and no hot-reload (skills require PM2 restart to pick up changes) |

These are the gaps Phase 2 design specs (`~/ava-stack/docs/PHASE2-design-specs.md`) target — `ask_user`, `stuck`, hooks, plugin lifecycle.

---

## 7 · Where `AGENTS.md` lives / should live

### 7.1 Today

**`AGENTS.md` does not exist** in the repo. Neither does `CLAUDE.md`, `GEMINI.md`, or `AGENT.md`.

Searched (case-insensitive): `find . -iname "AGENTS.md"` returns nothing.

### 7.2 Recommended placement

Following the Anthropic and Open-Source conventions, **`AGENTS.md` should live at the repository root** (peer of `README.md` and `CONTRIBUTING.md`). Same level as `CLAUDE.md` would live for Claude-Code-driven repos.

Recommended sections (suggested skeleton — to be authored separately, NOT in this audit):
1. **Identity & purpose of the agent runtime** — link `codec_identity.py` and `~/ava-stack/docs/PHASE2-design-specs.md`.
2. **Crew taxonomy** — link `codec_agents.py:1361` `CREW_REGISTRY` as the single source of truth.
3. **Tool inventory** — point at the skill registry + marketplace.
4. **Memory contract** — `codec_memory.py` API surface (§2.3) + `[MEMORY]` / `[RECENT MEMORY]` tag conventions.
5. **Audit + notification contract** — when does a run produce a notification? schema for `~/.codec/notifications.json`.
6. **Sandbox + safety boundaries** — `_HTTP_BLOCKED`, file/path blocklists, dangerous-pattern detection in `codec_config.py`.
7. **How to add an agent** — pointer to `routes/agents.py:128` `save_custom_agent` flow; pointer to `CREW_REGISTRY` for a built-in crew.

Putting it at root makes it the natural "what does the repo look like to a coding agent like Claude / OpenClaw / Hermes Agent" front door — exactly the goal of this comparison.

---

## 8 · Summary of structural gaps (objective)

These are statements of fact, not value judgments. They tell another reading agent exactly where CODEC is and isn't aligned with the Claude-Code-style `AGENTS.md` framework.

| # | Gap | Severity for "AGENTS.md" parity | File of record |
|---|---|---|---|
| 1 | No hook execution pipeline (PreToolUse / PostToolUse) | high | absent |
| 2 | `log_event` exported as `def log_event(*a, **kw): pass` in 5 modules — every call to it is silently a no-op | medium | `codec_audit.py` (missing export), `codec_scheduler.py:14`, et al. |
| 3 | Two parallel audit-log schemas (`event` + `agent` for crews; `tool` + `outcome` for MCP) coexist in the same `audit.log` file | medium | `codec_audit.py:49`, `codec_agents.py:65` |
| 4 | Custom agents persisted with no schema validation beyond `name` | low | `routes/agents.py:128-141` |
| 5 | In-flight crew job state lives only in `_agent_jobs` (in-memory dict) — restart loses progress | low | `routes/agents.py` |
| 6 | No `AskUserQuestion` / agent-pause mechanism — agents can't escalate ambiguity | high | absent |
| 7 | No formal step budget at chat-handler level (only `Agent.max_tool_calls` inside crew runs) | medium | `codec_dashboard.py:/api/chat` |
| 8 | Hot-reload of skills requires PM2 restart — no live reload | low | absent |
| 9 | Plugin lifecycle missing enable/disable runtime toggles (slash framework added 2026-04-29 partially fills this — `/skills enable/disable`) | low–medium | `codec_slash_commands.py:147-180` (partial) |
| 10 | No remote-permission bridge — agents running on Mac Studio can't ask the user for approval via mobile | medium | absent |

---

## 9 · Appendix — files that matter most for an agent comparison

For another agent reading this report, the highest-signal files for understanding CODEC's agent surface (in priority order):

```
codec_agents.py              1,468 lines — entire agent runtime (Tool, Agent, Crew, 12 crews, registry)
codec_skill_registry.py        196 lines — skill discovery + lazy loading
codec_memory.py                370 lines — SQLite + FTS5 + public API
codec_memory_upgrade.py        ~250 lines — facts table, CCF compression, tiered retrieval
codec_dashboard.py           3,439 lines — HTTP server, chat handler, /api/agents/* endpoints
codec_slash_commands.py        414 lines — chat meta-controls registry
codec_identity.py               65 lines — system prompts (operating-principles style as of 2026-04-29)
codec_voice.py                  ~990 lines — WebSocket voice loop, _CREW_TRIGGERS
codec_scheduler.py             443 lines — cron + notifications bridge
codec_audit.py                  82 lines — structured audit log
codec_audit_analyzer.py        222 lines — audit summary skill
codec_self_improve.py          ~150 lines — nightly skill-proposal drafter
codec_marketplace.py           ~250 lines — skill install/search/publish
codec_mcp.py                   246 lines — MCP tool registration
codec_mcp_http.py              ~200 lines — HTTP transport with OAuth 2.1
codec_oauth_provider.py        ~200 lines — token persistence (30d access / 90d refresh)
routes/agents.py               173 lines — agent crew HTTP API
routes/_shared.py              ~150 lines — notifications.json read/write
skills/                         71 built-in skill modules
~/.codec/skills/               52 user skill modules
~/.codec/memory.db             2.7 MB SQLite + FTS5
~/.codec/audit.log             rotated daily, 30-day retention
~/.codec/schedules.json        scheduler persistence
~/.codec/notifications.json    dashboard notification queue
~/.codec/agents/*.json         custom agent definitions
~/.codec/skill_proposals/      staged self-improvement proposals
```

— **end of report**
