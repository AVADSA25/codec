# PHASE 1 STEP 1 — Unified Audit Envelope + `log_event` Adapter

**Status:** DESIGN. Not implemented.
**Author:** drafted by Claude Code, reviewed by Mickael + Claude chat before any code is written.
**Scope:** define the unified envelope schema (per `AGENTS.md` §6), specify `log_event` as a real adapter over `audit()`, plan the migration, define tests, and define rollback. **No code changes in this step.**

---

## 0 · Why this exists

Today the audit log has **two parallel shapes** in the same `~/.codec/audit.log` file:

| Producer | Shape (current) | Where |
|---|---|---|
| `codec_audit.audit()` (MCP / dashboard skill calls) | `{ts, tool, task_len, context_len, duration_ms, outcome, error_type, client_id, transport, …extra}` | `codec_mcp.py:148-184`, `codec_mcp.py:202-230` |
| `codec_agents._audit()` (crew + agent runtime) | `{ts, event, …kwargs}` (e.g. `agent`, `tool`, `mode`, `elapsed`, `result_len`) | `codec_agents.py:65-78` |
| `log_event()` (lifecycle events: session, scheduler, dispatch, heartbeat, dashboard) | **NO-OP** — every module has `def log_event(*a, **kw): pass` because the import `from codec_audit import log_event` fails (it isn't exported) | 5 call sites listed in §2.4 |

Three problems:
1. Analyzer (`codec_audit_analyzer.py`) reads only `tool`, `outcome`, `duration_ms`, `ts`, `client_id` — crew entries don't surface in `top_used` / `slowest_tools` / `high_error_tools` reports because they have `event` instead of `tool` (or have `tool` but no `outcome`).
2. Two timestamp formats — `audit()` uses UTC milliseconds (`isoformat(timespec="milliseconds")`), crew `_audit()` uses naive local time. Sorting + bucketing breaks across the boundary.
3. Lifecycle events are silently lost. Every `log_event("error", "codec-heartbeat", ...)` call has been a no-op for as long as the codebase has had it. We've been operating blind on heartbeat / scheduler / dispatch errors.

This step **unifies the schema, wires `log_event` through, and keeps the analyzer working without rewriting it**.

---

## 1 · Unified envelope schema

### 1.1 Field definitions

```jsonc
{
  // ── Required on every entry ───────────────────────────────────────────
  "ts":        "2026-04-30T08:14:23.451+00:00",  // ISO8601 UTC, milliseconds
  "schema":    1,                                 // schema version, integer; bumped on breaking change (see §5)
  "event":     "tool_call",                       // discriminator — see §1.2 enumeration
  "source":    "codec-mcp-http",                  // emitter module / process — used in place of "service"
  "outcome":   "ok",                              // "ok" | "error" | "timeout" | "validation" | "denied"
  "ts_mono":   1234567890.123,                    // optional monotonic timestamp (for sub-second ordering on same host)

  // ── Optional but heavily preferred (analyzer reads them) ──────────────
  "tool":         "weather",         // skill / tool / crew name. Defaults to "" if not applicable
  "duration_ms":  120.5,             // float; null if not measurable yet (e.g. tool_call without paired tool_result)
  "task_len":     42,                // int; len of the user input that triggered this
  "context_len":  128,               // int; len of injected context (memory, system prompt addons)
  "transport":    "http",            // "stdio" | "http" | "voice" | "chat" | "crew" | "scheduler" | "heartbeat" | "dispatch" | "session" | "local"
  "agent":        "Writer",          // for crew events; null otherwise
  "client_id":    "claude-ai-mcp",   // OAuth client identifier when known
  "level":        "info",            // "debug" | "info" | "warning" | "error"
  "message":      "Schedule fired: daily_briefing",  // free-text summary, ≤ 500 chars

  // ── Error / timeout payload (only present when outcome != "ok") ────────
  "error_type":   "TimeoutError",
  "error":        "task exceeded 30s",   // short string, ≤ 500 chars

  // ── Extension payload (anything not in the above) ──────────────────────
  "extra": { "schedule_id": "sched_daily_briefing", "result_len": 757 }
}
```

### 1.2 Event-type enumeration

Every audit line discriminates by `event`. The full set, grouped by producer:

| `event` | Producer | Required fields beyond core | Optional |
|---|---|---|---|
| **MCP / Skills** | | | |
| `tool_call` | `codec_mcp.py`, `codec_agents._audit` | `tool`, `task_len`, `context_len` | `agent` (if crew) |
| `tool_result` | `codec_mcp.py`, `codec_agents._audit` | `tool`, `duration_ms`, `outcome` | `agent`, `extra.result_len`, `error_type` |
| `validation` | `codec_mcp._validate_mcp_input` | `tool`, `outcome="validation"` | `extra.reason` |
| `timeout` | `codec_mcp.py` (when 30s cap fires) | `tool`, `duration_ms`, `outcome="timeout"` | |
| **Crew / Agent runtime** | | | |
| `crew_start` | `codec_agents.Crew.run` | `extra.crew_name`, `extra.agents`, `extra.mode` | `extra.allowed_tools` |
| `crew_complete` | `codec_agents.Crew.run` | `duration_ms`, `extra.mode`, `extra.elapsed` | `extra.result_len` |
| `crew_error` | `codec_agents.Crew.run` | `outcome="error"`, `error_type`, `error` | |
| `agent_start` | `codec_agents.Agent.run` | `agent`, `extra.task_num`, `extra.total` | |
| `agent_finish` | `codec_agents.Agent.run` | `agent`, `duration_ms`, `outcome` | |
| `shell_blocked` | `codec_agents._shell_execute` | `outcome="denied"`, `extra.cmd` | |
| **Voice / chat** | | | |
| `voice_session_start` | `codec_voice.VoicePipeline.run` | `transport="voice"`, `extra.session_id` | `extra.resume_id` |
| `voice_session_end` | `codec_voice.VoicePipeline` | `duration_ms`, `extra.turns` | |
| `voice_interrupt` | `codec_voice` (barge-in) | `transport="voice"` | `extra.tts_remaining_ms` |
| `chat_command` | `codec_dashboard.py:856` | `transport="chat"`, `extra.source`, `extra.task_preview` (truncated) | |
| `chat_skill` | `codec_dashboard.py:880` | `tool`, `extra.result_len` | |
| `chat_llm` | `codec_dashboard.py:951` | `extra.model`, `extra.answer_len` | |
| `chat_llm_error` | `codec_dashboard.py:954` | `outcome="error"`, `error_type`, `error`, `extra.model` | |
| `chat_vision` | `codec_dashboard.py:999` | `extra.prompt_preview` (truncated) | `duration_ms` |
| `slash_command` | `codec_slash_commands.dispatch` (new — added Phase 1) | `extra.command`, `extra.args_count` | |
| **Scheduler / autopilot** | | | |
| `schedule_fire` | `codec_scheduler.py:233` | `transport="scheduler"`, `extra.schedule_id`, `extra.label` | |
| `schedule_done` | `codec_scheduler.py:236` | `outcome`, `duration_ms`, `extra.schedule_id` | |
| `autopilot_fire` | `codec_autopilot.py` | `transport="scheduler"`, `extra.trigger_name` | |
| **Heartbeat** | | | |
| `heartbeat_tick` | `codec_heartbeat.py:427` | `transport="heartbeat"` | `extra.tasks_run` |
| `service_down` | `codec_heartbeat.py:55` | `outcome="error"`, `level="error"`, `extra.service` | |
| `alert_fired` | `codec_heartbeat` (threshold breach) | `extra.alert_name`, `extra.value`, `extra.threshold` | |
| **Dispatch (voice/wake-word)** | | | |
| `wake_dispatch` | `codec_dispatch.run_skill` | `transport="dispatch"`, `tool` | |
| `wake_skill_error` | `codec_dispatch.py:72` | `outcome="error"`, `error_type`, `tool` | |
| **Session lifecycle** | | | |
| `session_start` | `codec_session.Session.__init__` (new wiring) | `transport="session"`, `extra.session_id` | |
| `session_end` | `codec_session.Session.close` | `duration_ms`, `extra.session_id`, `extra.turns` | |
| `command_flagged` | `codec_session.py:673` | `outcome="denied"`, `extra.cmd` (truncated, hashed) | |
| `command_approved` | `codec_session.py:680` | `extra.cmd` (hashed) | |
| `command_denied` | `codec_session.py:686` | `outcome="denied"`, `extra.cmd` (hashed) | |
| **Auth / OAuth (granular)** | | | |
| `auth_success` | `codec_dashboard` (after PIN/Touch ID) | `client_id`, `extra.method` (`pin` \| `touchID`) | `extra.ip` |
| `auth_reject` | `codec_dashboard` (auth middleware) | `outcome="denied"`, `extra.path`, `extra.reason` | `extra.ip` |
| `token_issued` | `codec_oauth_provider.PersistentOAuthProvider.exchange_authorization_code` | `client_id`, `extra.access_token_id` (last 8 chars), `extra.expires_in_sec` | `extra.scope` |
| `token_refreshed` | `codec_oauth_provider.PersistentOAuthProvider.exchange_refresh_token` | `client_id`, `extra.access_token_id` (new), `extra.previous_id` (old, last 8) | `extra.expires_in_sec` |
| `token_expired` | `codec_oauth_provider` (token TTL check on validate) | `outcome="denied"`, `client_id`, `extra.access_token_id`, `extra.age_seconds` | |
| `oauth_state_invalidated` | `codec_oauth_provider` admin / file-clear path | `outcome="warning"`, `level="warning"`, `extra.reason` (`admin_clear` \| `corruption` \| `manual_delete`), `extra.tokens_cleared` (count) | |
| **Security / safety** | | | |
| `mcp_http_blocked` | `codec_mcp_http` middleware (when `_HTTP_BLOCKED` skill called) | `outcome="denied"`, `tool`, `client_id`, `extra.reason="http_blocked"` | `extra.ip` |
| **Skill self-improvement** | | | |
| `skill_proposal_staged` | `codec_self_improve` (proposal write) | `outcome="ok"`, `extra.proposal_path`, `extra.skill_name`, `extra.signal_type` (`unknown_tool` \| `failing_tool` \| `timeout_tool`) | `extra.danger_score` |
| **System** | | | |
| `service_restart` | `codec_dashboard.py:3104` | `extra.service` | |
| `error` | catch-all | `outcome="error"`, `error_type`, `level="error"` | |

**Conventions:**
- **Required-on-every-entry** (`ts`, `schema`, `event`, `source`, `outcome`) gives the analyzer enough to compute totals, error rates, and per-source rollups for **every** entry — solves problem #1.
- **`tool`** is required on tool/skill events, optional otherwise. Analyzer's `top_used` / `slowest_tools` reports filter on `event in {tool_call, tool_result, chat_skill, wake_dispatch}` — proper tool stats with no garbage.
- **`extra`** is the unbounded escape hatch. Putting non-core fields under `extra` keeps the top level stable and easy to migrate.

### 1.3 Why `event` is the discriminator (not `tool`)

`event` answers *"what happened?"* — the analyzer's first dimension. `tool` answers *"to what?"* — second dimension. Currently the crew and MCP shapes flip these: crew uses `event`, MCP uses `tool` as the implicit discriminator. Standardizing on `event` lets us:
- Filter cleanly: `jq 'select(.event=="tool_result" and .outcome=="error")' audit.log`
- Add new lifecycle events without bending the schema
- Keep `tool` semantically clean (it's the *target*, not the *operation*)

### 1.4 `correlation_id` contract

**Status: REQUIRED** for any operation that emits ≥2 audit entries. The reviewer promoted this from "optional" in v1 of the design.

#### Format
6-byte (12-character) lowercase hex string. Generated via `secrets.token_hex(6)` at the entry point of any multi-emit operation. Stored as `extra.correlation_id` on every paired emit.

```
extra.correlation_id  =  "a3f7b2c8e409"
```

#### Operations that MUST propagate a correlation_id

| Operation | Entry point | Paired events |
|---|---|---|
| **MCP tool call** | `codec_mcp.py:_load_skill_tools_into.tool_fn` start | `tool_call` + `tool_result` (and `validation` / `timeout` if they fire) |
| **Crew run** | `codec_agents.run_crew` start | `crew_start`, every `agent_start` / `agent_finish`, every `tool_call` / `tool_result` from inside the crew, `crew_complete` (or `crew_error`) |
| **Voice session** | `codec_voice.VoicePipeline.run` start | `voice_session_start`, every `voice_interrupt`, every chat-style `tool_call` / `tool_result` fired during the session, `voice_session_end` |
| **Wake-word dispatch** | `codec.dispatch` entry | `wake_dispatch` + `tool_call` + `tool_result` (or `wake_skill_error`) |
| **Schedule run** | `codec_scheduler._run_crew` entry | `schedule_fire` + nested `crew_start` / `crew_complete` + `schedule_done` |
| **OAuth handshake** | `codec_oauth_provider` token-issuance flow | `auth_success` + `token_issued`, OR `token_refreshed` chain on subsequent renewals |
| **Skill proposal lifecycle** | `codec_self_improve.run` start (one ID per nightly run) | every `skill_proposal_staged` emitted in that run |

#### Operations that MAY OMIT correlation_id

Single-emit events (heartbeat tick, service_restart, alert_fired, slash_command, command_flagged/approved/denied trio — these are independent decisions, not paired) are exempt.

#### Propagation rules

- The ID is **passed by argument** through the call stack — never read from a global, never recomputed mid-operation. This survives async boundaries (asyncio.create_task) and thread boundaries (`run_in_executor`).
- For nested operations (e.g. an MCP `tool_call` fired inside a `crew_start`), **the inner operation INHERITS the outer correlation_id**. We do NOT generate a new ID for the nested operation. Reasoning: the analyzer can group all events of one user-initiated workflow under one ID. If we ever want sub-operation grouping, add `extra.parent_correlation_id` later.
- If a callsite somehow emits without a correlation_id when it should have one, the entry is still valid (audit.log writes succeed); the analyzer flags it as `correlation_id: <orphan>` for later debugging.

#### Why required, not optional

The Phase 1 reviewer promoted this from optional → required because:
- Without it, `tool_call` and its `tool_result` can only be paired by timestamp + tool name + heuristics. Brittle once we have parallel agent crews.
- Crew lifecycle has up to 8 nested events (1 crew + 3 agents × 2 events each). No way to attribute them without an ID.
- The 6-byte cost is negligible: 12 chars × ~10K entries/day = 120 KB/day. Disk is free at this scale; analyzability is not.

#### Generation contract

```python
import secrets
correlation_id = secrets.token_hex(6)   # e.g. "a3f7b2c8e409"
```

Generated **once per top-level operation**, then threaded as a function-argument through every audit emit in that operation's lifetime. If a deeper helper needs to emit, the correlation_id reaches it through:
- Direct kwarg passing for sync code
- `contextvars.ContextVar("codec_correlation_id")` for async/threaded code that doesn't naturally have the kwarg

`audit()` accepts `correlation_id=None` (kwarg) and writes it to `extra.correlation_id` if non-null. `log_event()` does the same.

---

## 2 · `log_event` adapter — full contract

### 2.1 Public function signature

Add to `codec_audit.py`:

```python
def log_event(
    event_type: str,        # one of the events in §1.2 (or any caller-defined string)
    source: str,            # emitter module — e.g. "codec-heartbeat", "codec-scheduler"
    message: str = "",      # free-text summary, ≤ 500 chars (truncated if longer)
    extra: dict | None = None,
    *,
    level: str = "info",    # "debug" | "info" | "warning" | "error"
    outcome: str | None = None,  # if None: derived from level (info → "ok", warning → "ok", error → "error")
    tool: str | None = None,
    transport: str | None = None,
    duration_ms: float | None = None,
    error_type: str | None = None,
    error: str | None = None,
    client_id: str | None = None,
) -> None:
    """Lifecycle/event audit emitter. Adapter over audit(). Never raises.

    The signature is positional-friendly so all 5 existing call sites work
    unchanged: log_event("error", "codec-heartbeat", "Service down: ...", level="error").
    """
```

### 2.2 Implementation outline (no code yet — semantics only)

```python
def log_event(event_type, source, message="", extra=None, *, level="info",
              outcome=None, tool=None, transport=None, duration_ms=None,
              error_type=None, error=None, client_id=None):
    if outcome is None:
        outcome = "error" if level == "error" else "ok"
    if transport is None:
        transport = _transport_for(source)         # see table below
    msg = (message or "")[:500]
    err = (error or "")[:500] if error else None

    audit(
        tool=(tool or ""),
        outcome=outcome,
        duration_ms=duration_ms,
        error_type=error_type,
        client_id=client_id,
        transport=transport,
        extra={
            "event": event_type,
            "source": source,
            "level": level,
            "message": msg,
            **({"error": err} if err else {}),
            **(extra or {}),
        },
    )
```

`_transport_for(source)` is a tiny lookup:

| `source` | `transport` |
|---|---|
| `codec-heartbeat` | `heartbeat` |
| `codec-scheduler` | `scheduler` |
| `codec-dispatch` | `dispatch` |
| `codec-session` | `session` |
| `codec-dashboard` | `chat` |
| `codec-mcp-http` | `http` |
| `codec-mcp` | `stdio` |
| `codec-voice` | `voice` |
| (default) | `local` |

### 2.3 Updates to the existing `audit()` function

`codec_audit.audit()` needs three small additions to write the unified envelope:

1. Always include `"schema": 1`.
2. Always include `"event"` — caller passes it via `extra={"event": "tool_call"}` or via a new `event=` kwarg. `audit()`'s existing callers in `codec_mcp.py` already conceptually emit `tool_call`/`tool_result` pairs but never label them — `event` becomes a required kwarg with a sensible default of `"tool_result"` to preserve the most common existing shape.
3. Always include `"source"` — defaults to `os.environ.get("CODEC_PROCESS", "codec")`.

The existing positional+keyword surface stays compatible; new fields are kwargs with defaults.

### 2.4 Per-call-site contract — what each existing `log_event` call should pass

#### `codec_session.py`

| Line | Current call | After adapter |
|---|---|---|
| 673 | `log_event("security", "codec-session", f"Command flagged: {code[:80]}", {"action": "flagged"})` | event_type → `command_flagged`. `extra={"cmd_hash": sha1(code)[:8], "cmd_preview": code[:80], "correlation_id": <op-id>}`. `outcome="denied"`, `level="warning"`. **`action` field dropped — redundant with `event`.** |
| 680 | `log_event("security", "codec-session", f"Command approved", {"action": "approved"})` | event_type → `command_approved`. `extra={"cmd_hash": <same as flagged>, "correlation_id": <same op-id>}`. `outcome="ok"`, `level="info"`. **`action` field dropped — redundant with `event`.** |
| 686 | `log_event("security", "codec-session", f"Command denied", {"action": "denied"})` | event_type → `command_denied`. `extra={"cmd_hash": <same as flagged>, "correlation_id": <same op-id>}`. `outcome="denied"`, `level="warning"`. **`action` field dropped — redundant with `event`.** |

Add (new wiring at session boundary):
- `log_event("session_start", "codec-session", session_id, extra={"session_id": ...})`
- `log_event("session_end", "codec-session", session_id, duration_ms=..., extra={"session_id": ..., "turns": ...})`

#### `codec_scheduler.py`

| Line | Current | After adapter |
|---|---|---|
| 233 | `log_event("scheduled", "codec-scheduler", f"Schedule fired: {sched.get('label', sched.get('crew', '?'))}", {"schedule_id": sched.get('id')})` | event_type → `schedule_fire`. `transport="scheduler"`. `extra={"schedule_id": ..., "label": ..., "crew": ...}`. |
| 236 | `log_event("scheduled", "codec-scheduler", f"Schedule done: {title}", {"success": success})` | event_type → `schedule_done`. `outcome="ok" if success else "error"`. `duration_ms=elapsed_ms`. `extra={"schedule_id": ..., "title": ...}`. |

#### `codec_dispatch.py`

| Line | Current | After adapter |
|---|---|---|
| 61 | `log_event("skill", "codec-dispatch", f"Skill: {skill.get('name', '?')}", {"result_len": len(...)})` | event_type → `wake_dispatch`. `tool=skill_name`. `transport="dispatch"`. `duration_ms=elapsed_ms`. `extra={"result_len": ...}`. |
| 72 | `log_event("error", "codec-dispatch", f"Skill error: {e}", level="error")` | event_type → `wake_skill_error`. `tool=skill_name`. `outcome="error"`. `error_type=type(e).__name__`. `error=str(e)[:500]`. |

#### `codec_heartbeat.py`

| Line | Current | After adapter |
|---|---|---|
| 55 | `log_event("error", "codec-heartbeat", f"Service down: {name}", level="error")` | event_type → `service_down`. `outcome="error"`. `extra={"service": name}`. |
| 427 | `log_event("system", "codec-heartbeat", "Heartbeat tick completed")` | event_type → `heartbeat_tick`. `extra={"tasks_run": tasks_run, "memory_size_mb": ...}`. |

Add (new wirings):
- `log_event("alert_fired", "codec-heartbeat", alert_name, extra={"alert_name": ..., "value": ..., "threshold": ...})`

#### `codec_dashboard.py`

| Line | Current | After adapter |
|---|---|---|
| 856 | `log_event("command", "codec-dashboard", f"Command from {source}: {task[:80]}", {"source": source, "task": task[:200]})` | event_type → `chat_command`. `transport="chat"`. `extra={"source": source, "task_preview": task[:200]}`. **Drop full task body — privacy.** |
| 880 | `log_event("skill", "codec-dashboard", f"Dashboard skill: {skill_name}", {"skill": skill_name, "result_len": len(skill_answer)})` | event_type → `chat_skill`. `tool=skill_name`. `extra={"result_len": ...}`. |
| 951 | `log_event("llm", "codec-dashboard", f"Flash response ready", {"model": model, "answer_len": len(answer)})` | event_type → `chat_llm`. `extra={"model": ..., "answer_len": ...}`. `duration_ms=elapsed`. |
| 954 | `log_event("error", "codec-dashboard", f"Flash LLM failed: {e}", level="error")` | event_type → `chat_llm_error`. `outcome="error"`. `error_type=type(e).__name__`. |
| 999 | `log_event("vision", "codec-dashboard", f"Vision analysis: {prompt[:60]}")` | event_type → `chat_vision`. `extra={"prompt_preview": prompt[:60]}`. `duration_ms=elapsed`. |
| 3104 | `log_event("system", "codec-dashboard", f"Service restart: {service}")` | event_type → `service_restart`. `extra={"service": service}`. |

### 2.5 Crew runtime (`codec_agents._audit`)

`codec_agents._audit()` is rewritten as a thin shim over `audit()`:

```python
def _audit(event_type: str, **kwargs):
    audit(
        tool=kwargs.pop("tool", ""),
        duration_ms=kwargs.pop("elapsed", None) and kwargs["elapsed"] * 1000.0,
        outcome=kwargs.pop("outcome", "ok"),
        transport="crew",
        extra={"event": event_type, "source": "codec-agents", **kwargs},
    )
```

This collapses the two parallel audit functions into one writer, kills the duplicate `_AUDIT_LOG_PATH = os.path.expanduser("~/.codec/audit.log")` (currently re-defined in `codec_agents.py:64`), and inherits the rotation + thread-locking from `codec_audit.py`.

---

## 3 · Migration plan

### 3.1 Recommendation: **leave-as-is, age out**

Rationale:
- **Analyzer already accepts both shapes** — `codec_audit_analyzer.analyze` reads `outcome`, `duration_ms`, `tool`, `ts`, `client_id` with `.get()` and tolerates missing keys. New unified entries don't break it; old crew-style entries (with `event` but no `outcome`) just don't surface in error stats — they never did.
- **30-day retention** — `_RETAIN_DAYS = 30` in `codec_audit.py:25`. After 30 days from deploy, every legacy-shape entry has been pruned. No backfill cost, no risk of corrupting old logs we might want for forensics.
- **Backfilling 188 KB of audit history** (the 16-Apr file is the largest at ~190 KB) requires a careful read-modify-write pass that locks the audit file for several seconds. Audit is on the hot path of every tool call. The cost-benefit doesn't justify it.

### 3.2 What we DO at deploy time

1. **Bump `audit()` to write `schema: 1`, `event:`, `source:` on every new entry.** All future entries are unified; nothing in the past changes.
2. **Update `codec_audit_analyzer.py` rendering** to take advantage of new fields (group by `event`, surface `source`, distinguish lifecycle events from tool events). Backward-compatible — old entries simply have null/empty new fields and are bucketed under "legacy".
3. **Add a `legacy=true` synthetic flag** in the analyzer when reading entries without `schema:` field, so reports can filter (`Show entries from before 2026-04-30 (legacy schema)`) if useful.

### 3.3 Rejected alternative: backfill

I considered a one-time `migrate_audit_log.py` script that reads each rotated `audit.log.YYYY-MM-DD`, normalizes entries to `schema:1`, and rewrites. Rejected because:
- Risks data loss if the script crashes mid-write.
- Only benefits the analyzer's "all-time" stats, which we don't currently produce (analyzer is per-day).
- Old crew entries lack a `transport` field — we'd have to invent one (`crew`) for entries from before the field existed.

---

## 4 · Test plan

### 4.1 Schema validation tests (`tests/test_audit_envelope.py`, new)

```python
# Required-field test
def test_unified_envelope_has_required_fields():
    audit("weather", event="tool_result", source="codec-mcp-http", outcome="ok",
          duration_ms=42.0)
    rec = _read_last_line()
    for k in ("ts", "schema", "event", "source", "outcome"):
        assert k in rec, f"missing required field: {k}"
    assert rec["schema"] == 1

# Event-type enumeration test
def test_all_event_types_pass_validation():
    for evt in EVENT_TYPES_FROM_DESIGN_DOC:  # extracted from §1.2 table
        log_event(evt, "test-source", "test message")
        # No exception raised, line written

# Truncation enforcement
def test_message_truncates_at_500():
    log_event("test", "src", "x" * 1000)
    rec = _read_last_line()
    assert len(rec["extra"]["message"]) == 500

# Privacy: task field is preview only, not full body
def test_chat_command_strips_long_task():
    log_event("chat_command", "codec-dashboard", "user typed something",
              extra={"task_preview": "x" * 200})
    rec = _read_last_line()
    assert "task" not in rec["extra"]   # never the full body
    assert "task_preview" in rec["extra"]
```

### 4.2 Call-site tests (`tests/test_log_event_callsites.py`, new)

For each of the 5 modules using `log_event`, monkeypatch `audit()` and assert the call arguments match §2.4:

```python
def test_heartbeat_service_down(monkeypatch):
    captured = []
    monkeypatch.setattr("codec_audit.audit", lambda **kw: captured.append(kw))
    from codec_heartbeat import _check_one_service
    _check_one_service("kokoro-82m", "http://localhost:9999/down")  # forced-fail URL
    assert captured[-1]["extra"]["event"] == "service_down"
    assert captured[-1]["extra"]["source"] == "codec-heartbeat"
    assert captured[-1]["outcome"] == "error"
    assert captured[-1]["transport"] == "heartbeat"

def test_scheduler_schedule_fire(monkeypatch):
    # ...analogous for scheduler
def test_dispatch_skill_run(monkeypatch):
    # ...analogous for dispatch
def test_session_command_flagged(monkeypatch):
    # ...analogous for session — verifies cmd_hash is sha256, not raw
def test_dashboard_chat_command(monkeypatch):
    # ...analogous for dashboard — verifies task is preview-only
```

5 modules × 2-3 events each ≈ **12-15 call-site tests**.

### 4.3 Analyzer round-trip tests (`tests/test_audit_analyzer_compat.py`, new)

The analyzer must produce identical-shape report dicts whether fed unified or legacy entries.

```python
def test_analyzer_handles_unified_entries():
    records = [unified_entry(event="tool_result", tool="weather", outcome="ok", duration_ms=42),
               unified_entry(event="tool_result", tool="weather", outcome="error", duration_ms=10)]
    out = analyze(records)
    assert out["total"] == 2
    assert out["errors"] == 1
    assert "weather" in dict(out["top_used"])

def test_analyzer_handles_legacy_crew_entries():
    records = [legacy_crew_entry(event="crew_start", agents=["Researcher"], mode="sequential"),
               legacy_crew_entry(event="tool_call", agent="Writer", tool="google_docs_create"),
               legacy_crew_entry(event="crew_complete", mode="sequential", elapsed=226)]
    out = analyze(records)
    assert out["total"] == 3
    # Legacy entries with no outcome don't count as errors
    assert out["errors"] == 0

def test_analyzer_mixed_unified_and_legacy():
    # 50/50 split → totals add cleanly, error rates compute correctly
```

### 4.4 Performance regression tests (`tests/test_audit_perf.py`, new)

`audit()` runs on every MCP tool call. The unified envelope adds 3-4 fields and one dict merge — needs to stay under **0.5 ms per call** in the single-thread case and **2.5 ms per call** under contention.

#### Single-thread budget

```python
def test_audit_write_performance():
    n = 1000
    t0 = time.monotonic()
    for i in range(n):
        audit("perf_test", event="tool_result", outcome="ok", duration_ms=1.0,
              correlation_id="a3f7b2c8e409")
    elapsed = time.monotonic() - t0
    avg_ms = (elapsed / n) * 1000
    assert avg_ms < 0.5, f"audit() took {avg_ms:.3f}ms/call, regressed past 0.5ms budget"

def test_log_event_write_performance():
    # Same test for log_event — should be ~0.6ms (one extra dict merge)
```

#### Concurrent stress test (NEW per reviewer §4.4)

The audit log is shared across PM2 processes (`codec-dashboard`, `open-codec`, `codec-mcp-http`, `codec-heartbeat`, `codec-autopilot`) and across threads inside `codec-dashboard`. Real production hits ~5-10 concurrent writers during voice sessions. The single-thread test isn't enough.

```python
def test_audit_concurrent_no_corruption():
    """10 threads × 1000 writes each = 10,000 entries. Verify:
       - Every line is valid JSON
       - All 10,000 lines present (no drops)
       - Avg latency < 2.5 ms/call under contention
    """
    import threading, time, json, tempfile, os
    from concurrent.futures import ThreadPoolExecutor

    log_path = tempfile.NamedTemporaryFile(suffix=".log", delete=False).name
    monkeypatch_audit_log_to(log_path)  # helper: temp file, not ~/.codec/audit.log

    N_THREADS = 10
    N_WRITES = 1000
    cid_pool = [f"corr{i:08x}{j:04x}"[:12] for i in range(N_THREADS) for j in range(N_WRITES)]

    def worker(thread_id):
        for j in range(N_WRITES):
            audit("stress",
                  event="tool_result",
                  outcome="ok",
                  duration_ms=0.1,
                  correlation_id=cid_pool[thread_id * N_WRITES + j])

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=N_THREADS) as ex:
        list(ex.map(worker, range(N_THREADS)))
    elapsed = time.monotonic() - t0

    total_calls = N_THREADS * N_WRITES
    avg_ms = (elapsed / total_calls) * 1000

    # 1. Avg latency budget under contention
    assert avg_ms < 2.5, f"audit() under 10-way contention: {avg_ms:.3f}ms/call (budget 2.5ms)"

    # 2. No corrupt JSON (every line parseable)
    with open(log_path) as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    assert len(lines) == total_calls, f"expected {total_calls} lines, got {len(lines)}"
    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise AssertionError(f"corrupt JSON at line {i}: {e!r}: {line[:200]!r}")
        # Required fields present
        for k in ("ts", "schema", "event", "source", "outcome"):
            assert k in obj, f"line {i} missing required field {k}: {line[:200]}"

    # 3. All correlation_ids accounted for (no drops)
    seen_cids = {json.loads(l)["extra"]["correlation_id"] for l in lines}
    assert len(seen_cids) == total_calls, "some correlation_ids missing — write was lost"

    os.unlink(log_path)
```

Why these numbers:
- **2.5 ms/call ceiling** under 10-way contention — `_LOCK` (threading.Lock) is the choke point; under perfect contention each thread waits ~1 ms for the lock + ~0.5 ms for the actual write. 2.5 ms gives margin without being slack.
- **10 threads × 1000 writes = 10,000 entries** is heavier than any realistic burst. A voice session with crew + tools peaks at ~50 entries/sec. We're testing at 4,000+/sec.
- **No corrupt JSON** is the hard correctness bar. A single torn write would be silently fatal — analyzer skips bad lines, so corruption hides forever.

Run on every PR via CI. Single-thread test runs on every commit; concurrent stress runs nightly + on any PR touching `codec_audit.py`.

### 4.5 Live smoke against `audit_report` skill

After implementation, run the existing `audit_report` skill and verify:
- Top-used tools still surface.
- Error counts include `wake_skill_error`, `service_down`, `chat_llm_error` — events that were silently no-op'd before.
- `unique_clients` still works (depends on `client_id` field, unchanged).

---

## 5 · Rollback plan

### 5.1 Recommendation: **schema-version field + git revert as primary**

Both layers, low overhead:

1. **`schema: 1`** in every new entry (already in §1.1).
   - Future schema changes bump to `schema: 2`. Analyzer reads both because new fields are additive.
   - This isn't a feature flag, it's a forward-compat marker — but it gives us the option to write a `schema_v1_to_v2_migrator` later if needed without guessing which entries pre-date the change.

2. **Git revert is the rollback mechanism.**
   - The change is contained: `codec_audit.py` (~20 lines added), `codec_agents.py:65-78` (rewrites `_audit` as 5-line shim), 5 call-site files (no-op fallbacks deleted).
   - `git revert <commit>` restores prior behavior in one operation.
   - `~/.codec/audit.log` continues working (analyzer is back-compat).
   - PM2 restart of `codec-dashboard`, `open-codec`, `codec-mcp-http`, `codec-heartbeat`, `codec-autopilot` picks up reverted code.
   - Total rollback time: ~30 seconds.

### 5.2 Rejected alternative: feature flag

I considered a `CODEC_AUDIT_UNIFIED=1` env var with `audit()` branching. Rejected because:
- `audit()` is on the hot path; adding a runtime branch costs ~50ns × every-tool-call. Negligible per-call but it's still tax for no benefit.
- Feature flags hide which path is in production. Hard to debug incidents when the answer to "what entry shape are we writing today?" is "depends on the env at the moment of write."
- Two branches means two test paths means double the maintenance cost.

### 5.3 What "broken in production" looks like — and the response

| Symptom | Cause | Response |
|---|---|---|
| `audit_analyzer` errors on KeyError | Required field missing in some entries | Fix the writer that's emitting incomplete entries; analyzer should be tolerant via `.get()` (already is) |
| `audit.log` size grows unexpectedly fast | Some call site is logging in a hot loop | grep for the runaway `event:` value, kill that emit, rotate |
| `pytest tests/test_audit_envelope.py` fails after merge | Schema regression | Revert. The schema is the contract. |
| MCP tool calls suddenly slow (p95 > 200 ms) | `audit()` perf regression | Run `tests/test_audit_perf.py`; if confirmed, revert. |
| `~/.codec/audit.log` corrupt mid-line | Write race despite `_LOCK` | This is unrelated to the schema change but worth knowing the failure mode. Recovery: rotate the corrupt file aside, restart. |
| `tool_call` and `tool_result` not pairing in analyzer | `correlation_id` propagation broken at one call site | grep for `extra.correlation_id` absent on `tool_result`. Fix the missing pass-through. Don't revert — this is a wiring bug, not a schema bug. |

### 5.4 Post-deploy monitoring window (24 hours)

Per the reviewer's revert criteria — `audit()` is on the hot path, so we treat the merge like a production incident in slow motion: instrument before, check at fixed intervals, revert if the instrumentation says we regressed.

#### Pre-merge baseline

Before the merge commit lands on `main`, capture a baseline 30-minute MCP p95 latency window:

```bash
# Run this 30 minutes before scheduled merge
# Captures real Claude.ai → MCP traffic
date > ~/.codec/perf-baseline.txt
sqlite3 ~/.codec/audit.db <<'SQL' >> ~/.codec/perf-baseline.txt
  SELECT
    COUNT(*) as n,
    ROUND(AVG(duration_ms), 2) as avg_ms,
    ROUND((SELECT duration_ms FROM (SELECT duration_ms FROM audit ORDER BY duration_ms LIMIT (SELECT COUNT(*) * 95 / 100 FROM audit))), 2) as p95_ms
  FROM audit
  WHERE ts > datetime('now', '-30 minutes');
SQL
```

(`audit.db` is the SQLite-backed view of `audit.log` produced by the existing `codec_audit_analyzer` — if not yet built, sample directly from `audit.log` via `jq` instead.)

Record: `n`, `avg_ms`, `p95_ms`. This is the baseline for the next 24 hours.

#### Post-merge sampling cadence

Every **4 hours** for **24 hours** post-merge (six samples: T+0, T+4h, T+8h, T+12h, T+16h, T+20h), run the same query against the trailing 30 minutes. Append each sample to `~/.codec/perf-postmerge.txt` with timestamp.

Sampling can be a tiny PM2 cron-skill or a calendar reminder + manual run — both work. Pick one before merge.

#### Revert criteria

**Hard revert if any of:**
- p95 > 2× baseline at any sample point (e.g. baseline 80 ms → trigger at 160 ms)
- avg > 2× baseline at any sample point
- `tests/test_audit_concurrent_no_corruption` fails when re-run against live load

**Soft action if any of:**
- p95 between 1.3× and 2× baseline → investigate, do NOT revert immediately. Likely a wiring inefficiency in one new call site, fixable forward.
- New error spikes in audit_analyzer report (unique error counts > 2× baseline) → triage which event is producing them.

#### Revert mechanics (bound to ≤ 2 minutes)

```bash
git -C ~/codec-repo revert <merge-commit> --no-edit
git -C ~/codec-repo push origin main
pm2 restart codec-dashboard open-codec codec-mcp-http codec-heartbeat codec-autopilot --update-env
# Verify rollback
curl -s http://localhost:8090/api/health | jq -r '.audit_schema_version'   # should report null or absent
```

Revert leaves the new `audit.log` lines (schema:1) in the file — they remain valid. The reverted code goes back to writing the pre-merge shape; analyzer tolerates both. No data loss.

#### Sign-off after 24 hours

If all six samples are within 1.3× baseline and no failures triggered, mark the merge as production-stable in `docs/known-issues.md` (or wherever the running stability ledger lives) and unwatch.

---

## 6 · Summary — what gets shipped at implementation time

When Phase 1 Step 2 implementation runs, the diff will land:

| File | Δ | What |
|---|---|---|
| `codec_audit.py` | ~+85 LOC | `log_event()` (~30), `event`/`source`/`schema`/`correlation_id` plumbing in `audit()` (~15), transport lookup (~15), `_PREVIEW_MAX=200` constant + truncation helper (~10), 6 new event_type validators or constants (~15) |
| `codec_agents.py:64-78` | -15 / +20 | rewrite `_audit()` as shim, plus correlation_id threading through `Crew.run` + `Agent.run` (every audit emit inside the crew gets the crew's correlation_id) |
| `codec_mcp.py` | ~+20 LOC | generate correlation_id at start of every tool_fn, pass to `_audit` calls (5 sites). Required-kwarg `event=` on every audit() call (per Q4 resolution: no default — explicit `event="tool_call"` or `event="tool_result"` etc.). Add `mcp_http_blocked` emit in HTTP transport blocklist check |
| `codec_voice.py` | ~+10 LOC | correlation_id at `VoicePipeline.run` entry, threaded through every audit emit during the session lifetime + nested `tool_call`/`tool_result` events |
| `codec_oauth_provider.py` | ~+15 LOC | new `token_issued`, `token_refreshed`, `token_expired`, `oauth_state_invalidated` emits at the right state transitions. Correlation_id covers the issue→refresh chain. |
| `codec_self_improve.py` | ~+5 LOC | `skill_proposal_staged` emit per proposal staged, with shared correlation_id for the nightly run |
| `codec_session.py:25` | -1 / +1 | delete `def log_event(*a, **kw): pass`, real import |
| `codec_scheduler.py:14` | -1 / +1 | same |
| `codec_dispatch.py:12` | -1 / +1 | same |
| `codec_heartbeat.py:12` | -1 / +1 | same |
| `codec_dashboard.py:29` | -1 / +1 | same |
| `tests/test_audit_envelope.py` | new, ~100 lines | schema validation + all 30+ event_types + correlation_id contract |
| `tests/test_log_event_callsites.py` | new, ~140 lines | 5 modules × 2-3 events with correlation_id propagation asserted |
| `tests/test_audit_analyzer_compat.py` | new, ~60 lines | unified + legacy + mixed |
| `tests/test_audit_perf.py` | new, ~80 lines | single-thread + concurrent stress (10×1000) |
| `tests/test_correlation_id_propagation.py` | new, ~80 lines | crew run + voice session + MCP tool call all preserve their ID end-to-end |
| `AGENTS.md` §6 | small update | status note + correlation_id contract reference |

**Net code change:** **~+100 functional LOC** (was ~+60 in v1; correlation_id threading + 6 new event types added the difference), **~+460 LOC tests**, zero breaking API changes. Audit log entries written from this commit forward conform to the unified envelope; entries before this commit remain readable.

---

## 7 · Reviewer resolutions (closed)

Resolved by the Phase 1 reviewer (Claude chat) on 2026-04-30. All five questions from v1 of this doc are now decided:

| # | Question | Resolution |
|---|---|---|
| **Q1** | `cmd_hash` algorithm — sha256 vs sha1@8? | **ACCEPTED** sha1 truncated to 8 hex chars. Implementation: `hashlib.sha1(code.encode()).hexdigest()[:8]`. Used in `command_flagged` / `command_approved` / `command_denied` triplet. Not a security primitive — only a pairing key. |
| **Q2** | `_PREVIEW_MAX = 200` constant in `codec_audit.py` to enforce truncation? | **ACCEPTED**. `codec_audit.py` exports `_PREVIEW_MAX = 200` and a helper `_truncate(s, max_len=_PREVIEW_MAX)`. Every `*_preview` field (`task_preview`, `cmd_preview`, `prompt_preview`, etc.) is run through it before write. No call site can bypass. |
| **Q3** | `schema:` field on every entry? | **ACCEPTED**. `audit()` always writes `"schema": 1`. 16-byte/line cost is negligible vs forward-compat value. |
| **Q4** | `audit()` default `event="tool_result"`? | **REJECTED**. `event=` is now a **required kwarg with no default**. Calling `audit("weather", outcome="ok")` without `event=` raises `TypeError`. Reasoning from reviewer: defaults hide the discriminator and make schema regressions silent. Every existing `codec_mcp.py` call site (5 in tool_fn, 4 in memory tools — see `codec_mcp.py:148-230`) gets an explicit `event="tool_call"` or `event="tool_result"` or `event="validation"` or `event="timeout"` or `event="error"` based on the branch it's emitting from. |
| **Q5** | `correlation_id` — keep optional? | **ACCEPTED AND PROMOTED**. Now **required** (not optional) for any operation emitting ≥2 audit lines. Full contract documented in §1.4. Analyzer flags entries that should have one but don't (`<orphan>`) so we catch wiring bugs in test, not in production. |

These resolutions are baked into §1, §1.4, §2, §4, §5, §6 above. No further reviewer input needed before implementation.

---

**End of design (v2).** No code modified. No other docs written. Stops here.
