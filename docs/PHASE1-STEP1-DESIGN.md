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
| `chat_command` | `codec_dashboard.py:856` | `transport="chat"`, `extra.source`, `extra.task` (truncated) | |
| `chat_skill` | `codec_dashboard.py:880` | `tool`, `extra.result_len` | |
| `chat_llm` | `codec_dashboard.py:951` | `extra.model`, `extra.answer_len` | |
| `chat_vision` | `codec_dashboard.py:999` | `extra.prompt` (truncated) | |
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
| **Auth / system** | | | |
| `auth_success` | `codec_dashboard` (after OAuth flow) | `client_id`, `extra.method` | |
| `auth_reject` | `codec_dashboard` | `outcome="denied"`, `extra.path`, `extra.reason` | |
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
| 673 | `log_event("security", "codec-session", f"Command flagged: {code[:80]}", {"action": "flagged"})` | event_type → `command_flagged`. Pass `extra={"cmd_hash": sha256(code), "cmd_preview": code[:80], "action": "flagged"}` (don't store full command — privacy + log size). `outcome="denied"`, `level="warning"`. |
| 680 | `log_event("security", "codec-session", f"Command approved", {"action": "approved"})` | event_type → `command_approved`. Pass the same `cmd_hash` so it pairs with the `command_flagged` entry. `outcome="ok"`, `level="info"`. |
| 686 | `log_event("security", "codec-session", f"Command denied", {"action": "denied"})` | event_type → `command_denied`. Same `cmd_hash`. `outcome="denied"`, `level="warning"`. |

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

### 4.4 Performance regression test (`tests/test_audit_perf.py`, new)

`audit()` runs on every MCP tool call. The unified envelope adds 3-4 fields and one dict merge — needs to stay under **0.5 ms per call** so it doesn't slow MCP latency.

```python
def test_audit_write_performance():
    n = 1000
    t0 = time.monotonic()
    for i in range(n):
        audit("perf_test", event="tool_result", outcome="ok", duration_ms=1.0)
    elapsed = time.monotonic() - t0
    avg_ms = (elapsed / n) * 1000
    assert avg_ms < 0.5, f"audit() took {avg_ms:.3f}ms/call, regressed past 0.5ms budget"

def test_log_event_write_performance():
    # Same test for log_event — should be ~0.6ms (one extra dict merge)
```

Run on every PR via CI; fail the build on regression.

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

---

## 6 · Summary — what gets shipped at implementation time

When Phase 1 Step 2 implementation runs, the diff will land:

| File | Δ |
|---|---|
| `codec_audit.py` | + `log_event()` function (~30 lines), + `event` / `source` / `schema` defaults in `audit()` (~10 lines), + transport lookup table (~15 lines). Total ~+55 lines. |
| `codec_agents.py:64-78` | rewrite `_audit()` as 5-line shim over `audit()`. -15 / +5. |
| `codec_session.py:25` | delete `def log_event(*a, **kw): pass`, replace `from codec_audit import log_event` to use real one. -1 / +1. |
| `codec_scheduler.py:14` | same. -1 / +1. |
| `codec_dispatch.py:12` | same. -1 / +1. |
| `codec_heartbeat.py:12` | same. -1 / +1. |
| `codec_dashboard.py:29` | same. -1 / +1. |
| `tests/test_audit_envelope.py` | new, ~80 lines |
| `tests/test_log_event_callsites.py` | new, ~120 lines |
| `tests/test_audit_analyzer_compat.py` | new, ~60 lines |
| `tests/test_audit_perf.py` | new, ~30 lines |
| `AGENTS.md` §6 | update status note: "Status as of Phase 1 Step 1 (commit `<hash>`): adapter wired through" |

**Net code change:** ~+60 lines functional, ~+290 lines tests, zero breaking API changes. Audit log entries written from this commit forward conform to the unified envelope; entries before this commit remain readable.

---

## 7 · Open questions for the reviewer

These are flagged for explicit decision before implementation begins. None are blockers, but each has consequences:

1. **`cmd_hash` algorithm for session security events** — sha256 is overkill for log indexing. Is sha1 (shorter, faster) acceptable given these aren't security tokens, just dedup keys? Recommend sha1 truncated to 8 hex chars.

2. **`extra.task_preview` length cap** — currently call sites use `task[:200]`. Should the unified envelope enforce this in `log_event()` directly so it can't drift? Recommend yes — a `_PREVIEW_MAX = 200` constant in `codec_audit.py`.

3. **Should `schema:` be on every entry or only on entries written via the unified path?** Strict envelope says yes-on-every-entry. Cost: 16 bytes/line × ~10K lines/day = 160 KB/day. Negligible. Recommend yes.

4. **What about entries written by `audit()` callers in `codec_mcp.py` that don't yet pass `event=`?** I propose `audit()` defaults `event="tool_result"` for backward compatibility — every call from `codec_mcp.py` is in fact a tool_result emit. Confirm this default is right.

5. **Do we want a `correlation_id`** so a `tool_call` and its paired `tool_result` (or `crew_start` and its `crew_complete`) can be linked? Currently nothing links them. Recommend yes, as `extra.correlation_id`, generated as a 6-byte hex at the start of any operation that produces multiple audit lines. Optional field; analyzer already tolerates it.

If the reviewer accepts these defaults (or amends them), implementation can proceed. If any of them are contested, surface the change here and re-circulate before code starts.

---

**End of design.** No code modified. No other docs written. Stops here.
