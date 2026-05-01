# Phase 2 Step 5 — Continuous Observation Loop (Design)

**Branch:** `phase2-step5-observer`
**Source spec:** `docs/PHASE2-BLUEPRINT.md` §"Step 5"
**Status:** design phase — no code yet
**Reviewer:** user

---

## 0 · Why this exists

CODEC today is reactive: it answers what you ask. Every chat or voice turn arrives without context — the LLM doesn't know which app you have open, what you copied last, what file you just edited, what you've been doing for the last 10 minutes.

That makes "what's my Stripe balance" a pure tool call. It also makes "summarize this PR" require you to paste the URL. And "where was I" is impossible.

The fix isn't smarter prompting. It's a **standing context source** — a small background process that polls a few cheap signals (frontmost window, last screenshot OCR, clipboard delta, recent file changes), keeps 10 minutes in RAM, and feeds the LLM a 200-token summary **on the calls where it actually helps**.

This is the foundation Step 6 (auto-fired trigger skills) and Step 7 (end-of-day shift report) build on. Without an observer there's nothing to trigger off and nothing to summarize.

The Step-1 audit envelope, Step-2 hook chokepoint, and Step-4 plugin mechanism all extend cleanly to cover this — observer is a sidecar PM2 service that emits standard audit events and reads existing skill outputs. No new system permissions, no new transport, no new auth surface.

---

## 1 · Design

### 1.1 PM2 service `codec-observer`

New PM2 service. `codec_observer.py` is the script. Process lifecycle:

```
codec-observer entry
  ↓
load config (~/.codec/config.json: observer.{cadence_active_s, cadence_idle_s, buffer_depth_min, kill_switch})
  ↓
init RingBuffer(maxlen=N)
  ↓
loop:
  → idle_seconds = CGEventSourceSecondsSinceLastEventType(...)
  → cadence = cadence_active_s if idle_seconds < 60 else cadence_idle_s
  → poll() — gather snapshot, append to buffer, emit observation_tick
  → sleep(cadence)
```

Single-threaded, blocking sleep, no asyncio. Matches the simplicity of `codec_heartbeat.py`. PM2 handles restart on crash.

### 1.2 Ring buffer

`collections.deque(maxlen=N)`. N computed from `buffer_depth_min × 60 / min(cadence_active_s, cadence_idle_s)` so the buffer always covers the requested duration even at the fastest cadence.

For the locked defaults (10 min, 60s active): `N = 10`. Cheap.

Each entry is a dict:
```python
{
    "ts": "2026-05-01T18:30:00.123+00:00",   # ISO8601 UTC ms
    "active_window": {                        # via skills/active_window.py
        "app": "Google Chrome",
        "title": "Stripe — Dashboard | dashboard.stripe.com",
        "pid": 12345,
    },
    "screenshot_ocr": "Stripe Dashboard / Payments / $4,231.57 today / ...",   # ≤500 chars, may be ""
    "clipboard": {                            # only set if changed since last poll
        "preview": "https://github.com/AVADSA25/codec/pull/8",   # ≤200 chars
        "content_type": "url" | "text" | "code" | "json" | "image_blob_redacted",
    } | None,
    "recent_files": [                         # ~/Documents + ~/codec-repo + ~/Downloads, mtime in last 5 min
        {"path": "/Users/.../codec_observer.py", "mtime": "..."},
        ...
    ],
    "idle_seconds": 12,                       # at time of poll
}
```

**No filtering, no blocklist** (Q6). The buffer is RAM-only by design — if the user doesn't trust their own machine to hold 10 minutes of screen state, the whole CODEC threat model breaks. Step 7's shift_report assembly is the **only** path that persists summaries (and only with the user's explicit-or-scheduled trigger).

### 1.3 Polling primitives — what we actually call

All four signals come from existing CODEC capabilities. No new permission prompts at install time:

| Signal | Source | Cost |
|---|---|---|
| Frontmost window | `skills/active_window.py` (`pyobjc` `NSWorkspace`) | <5ms |
| Screenshot OCR | `skills/screenshot_text.py` (Vision framework) | ~30-100ms — the heaviest |
| Clipboard delta | direct `pbpaste` + sha1 hash compare to last poll | <10ms |
| Recent files | `os.path.getmtime` over a small candidate list | <10ms |
| Idle seconds | `CGEventSourceSecondsSinceLastEventType` via `Quartz` | <1ms |

Total per poll: ~50-130ms worst case. Within the 50ms-target stated in the blueprint **only when OCR is fast**; for most polls we exceed that. Refined budget: **<150ms per poll p95**, with the OCR step time-budgeted at 100ms (kill the OCR call on timeout, log a `observation_tick` with `extra.ocr_skipped=true`, continue).

### 1.4 Idle classifier (Q4 — locked)

```python
from Quartz import CGEventSourceSecondsSinceLastEventType, kCGEventSourceStateHIDSystemState

def _idle_seconds() -> float:
    # HID system state covers keyboard + mouse + trackpad + apple-pencil
    return CGEventSourceSecondsSinceLastEventType(
        kCGEventSourceStateHIDSystemState, 4294967295  # all event types
    )
```

`> 60s` flips cadence from active to idle. `> 1800s` (30 min) is the Step-7 shift-report idle-trigger threshold (shift_report consumes this same signal — single source of truth).

### 1.5 Observation injection contract (Q5 override — see blueprint §X)

The buffer is always populated; **injection is gated**. The chat handler in `codec_dashboard.py` and the voice handler in `codec_voice.py` are the two injection points. Both call into a single helper:

```python
# codec_observer.py
def maybe_inject_observation_summary(
    user_prompt: str,
    transport: str,
    skill_name: str | None = None,
) -> tuple[str | None, str]:
    """
    Returns (summary_or_None, reason).
    
    reason ∈ {
        "always_local",
        "possessive_match",
        "continuation_match",
        "skill_flag",
        "skipped_no_match",
        "skipped_disabled",
        "skipped_empty_buffer",
    }
    
    Audit emit (observation_summary_injected) ONLY when summary is non-None.
    """
```

Decision tree:

1. `OBSERVER_ENABLED=false` → return `(None, "skipped_disabled")`.
2. Buffer is empty (process just started) → `(None, "skipped_empty_buffer")`.
3. `transport == "local"` → render summary, return `(summary, "always_local")`.
4. `transport == "mcp"` → return `(None, "skipped_no_match")` — MCP clients (claude.ai, Claude.app) bring their own context; we don't pad it from CODEC.
5. `transport in ("http", "voice", "chat")` → run the §X.1 pattern checks:
   - **Possessive-without-context regex** matches user_prompt (with stop-noun filter): inject, reason=`possessive_match`.
   - **Continuation regex** matches: inject, reason=`continuation_match`.
   - Skill specified `SKILL_NEEDS_OBSERVATION = True`: inject, reason=`skill_flag`.
   - Otherwise: `(None, "skipped_no_match")`.

### 1.6 Summary rendering — what gets put in the prompt

Given a buffer of N entries, render to ≤200 tokens (≤800 chars in practice):

```
[CODEC observation, last 10 min]
Active: Google Chrome — Stripe Dashboard (12s ago)
Recent: codec_observer.py edited 3 min ago, PHASE2-STEP5-DESIGN.md edited 1 min ago
Clipboard: github.com/AVADSA25/codec/pull/8 (URL, 30s ago)
Screen text: "Stripe Dashboard, Payments $4,231 today, recent: stripe-test-event"
```

Order matters: most-recent state first, oldest last. Truncated middle if buffer is full and content is verbose. Token-counted via the same tokenizer used by the chat handler (probably `tiktoken` cl100k or whatever Qwen exposes).

### 1.7 Buffer reset triggers

The buffer wipes itself when:
- Process restart (PM2 SIGTERM / crash / boot — RAM gone).
- User explicitly invokes `codec-observer-reset` skill (to-be-added in Step 5 Phase B if needed; deferred for now — easier to `pm2 restart codec-observer` if you want a clean buffer).
- Detected user-input-after-long-idle transition (`idle_seconds > 1800` followed by `idle_seconds < 5`) — old context probably stale, start fresh. Optional, behind a config flag default true.

### 1.8 Configuration

`~/.codec/config.json`:

```jsonc
{
  ...,
  "observer": {
    "cadence_active_s": 60,
    "cadence_idle_s": 300,
    "idle_threshold_s": 60,                  // for active vs idle classification
    "buffer_depth_min": 10,
    "ocr_timeout_ms": 100,
    "reset_on_long_idle": true,
    "reset_idle_threshold_s": 1800,
    "summary_max_tokens": 200
  }
}
```

All values overridable; defaults match the locked Q1-Q5 resolutions. No env-var overrides for these (single source of truth = config file). The single env-var override is `OBSERVER_ENABLED` (kill switch).

---

## 2 · Implementation outline

### 2.1 New files

| File | LOC | Purpose |
|---|---|---|
| `codec_observer.py` | ~400 | Service entry + RingBuffer + poll loop + injection helper + audit emits |
| `tests/test_observer.py` | ~400 | 30 tests covering everything in §7 |

### 2.2 Modified files

| File | LOC delta | Why |
|---|---|---|
| `codec_audit.py` | ~+10 | Two new event constants: `OBSERVATION_TICK`, `OBSERVATION_SUMMARY_INJECTED` |
| `codec_dashboard.py` | ~+15 | Call `maybe_inject_observation_summary()` in chat-handler prompt assembly, between L1 identity injection and the user's prompt |
| `codec_voice.py` | ~+15 | Same as above in voice handler |
| `ecosystem.config.js` | ~+12 | New `codec-observer` PM2 entry |
| `AGENTS.md` | ~+30 | New §3 sub-section "Continuous observation (Phase 2 Step 5)"; §6 audit additions; §7 mention `~/.codec/observation_summaries/` if persisted; §10 mention `OBSERVER_ENABLED` env var |

Total: ~+830 LOC + 30 tests.

### 2.3 Module API

`codec_observer.py` public surface:

```python
# RingBuffer-related
class RingBuffer:
    def __init__(self, maxlen: int): ...
    def append(self, snapshot: dict) -> None: ...
    def snapshot(self) -> list[dict]: ...
    def render_summary(self, max_tokens: int = 200) -> str: ...

# Polling
def poll() -> dict: ...                     # returns snapshot dict per §1.2
def run_daemon() -> None: ...               # PM2 entry; never returns

# Injection
def maybe_inject_observation_summary(
    user_prompt: str,
    transport: str,
    skill_name: str | None = None,
) -> tuple[str | None, str]: ...

# Internal helpers (underscored, but stable API for tests + Step 6/7)
_idle_seconds() -> float
_should_inject_for_cloud_transport(prompt: str) -> tuple[bool, str]
_GLOBAL_BUFFER: RingBuffer | None       # module singleton, lazy-init

if __name__ == "__main__":
    run_daemon()
```

Step 6 (Triggers) reads `_GLOBAL_BUFFER.snapshot()` to evaluate trigger candidates against latest buffer state. Step 7 (Shift Report) reads the persisted `~/.codec/observation_summaries/` files (which only get written by an explicit summarize-and-persist call from the shift_report crew assembly).

### 2.4 Where injection plugs into chat / voice handlers

Both handlers already call into a system-prompt assembler. Insertion point:

```python
# Today (codec_dashboard.py:1827-1862 for chat, codec_voice.py:288-320 for voice)
system = identity_prompt + l1_facts + memory_window + ...

# Phase 2 Step 5 — add ONE call:
from codec_observer import maybe_inject_observation_summary
obs_summary, reason = maybe_inject_observation_summary(
    user_prompt=user_text,
    transport=request_transport,
    skill_name=resolved_skill_name,
)
if obs_summary:
    system += f"\n\n{obs_summary}"
    # Audit emit happens INSIDE maybe_inject_observation_summary;
    # we don't double-emit here.
```

Single insertion line per handler. No prompt-assembly refactor needed.

---

## 3 · Audit envelope additions (extending Step 1 §1.2)

Two new event types, both `outcome="ok"`, `level="info"` (these are observability signals, not failures):

### `observation_tick`

```jsonc
{
  "ts": "2026-05-01T18:30:00.123+00:00",
  "schema": 1,
  "event": "observation_tick",
  "source": "codec-observer",
  "tool": "",
  "task_len": 0,
  "context_len": 0,
  "outcome": "ok",
  "level": "info",
  "transport": "local",
  "extra": {
    "correlation_id": "...",          // per-tick cid (operations are per-poll)
    "active_app": "Google Chrome",
    "active_title_len": 47,           // length only — no content
    "ocr_chars": 234,                 // length only — no content
    "ocr_skipped": false,             // true if OCR timed out
    "clipboard_changed": true,
    "clipboard_kind": "url",          // type only — no content
    "recent_files_count": 2,
    "idle_seconds": 12,
    "cadence_used_s": 60,
    "buffer_depth": 10
  }
}
```

**No content** (no titles, no OCR text, no clipboard text, no file paths) makes it into the audit log. Only metadata. Privacy-by-default — the audit log is for operators / debugging, not for re-deriving screen state.

### `observation_summary_injected`

```jsonc
{
  "ts": "2026-05-01T18:30:01.456+00:00",
  "schema": 1,
  "event": "observation_summary_injected",
  "source": "codec-observer",
  "tool": "",
  "outcome": "ok",
  "level": "info",
  "transport": "chat",        // or "voice", "local", etc.
  "extra": {
    "correlation_id": "...",        // inherits from the wrapping chat/voice operation
    "tokens_used": 187,
    "injection_reason": "possessive_match",
    "buffer_entries_summarized": 8
  }
}
```

The wrapping chat/voice op's `correlation_id` is reused — this emit is part of that op, not a new one.

### What we deliberately do NOT emit

- **No emit when injection is skipped.** A non-injection is the silent default. Otherwise every chat turn emits an `observation_summary_injected` with `reason="skipped_no_match"`, which doubles audit-log volume for no insight.
- **No emit per-buffer-entry update.** The poll loop emits one `observation_tick` per cycle, not one event per signal.

---

## 7 · Test plan

30 tests across `tests/test_observer.py`. Same pattern as Phase 1 step tests — redirect `codec_audit._AUDIT_LOG` to `tmp_path`, mock heavy I/O (Vision OCR, AppleScript), assert against the buffer + audit log.

### 7.1 Ring buffer (6 tests)
- `test_ringbuffer_append_under_capacity` — appends grow length up to N
- `test_ringbuffer_wraparound_drops_oldest` — N+1 entries → oldest evicted
- `test_ringbuffer_snapshot_is_copy` — mutating snapshot doesn't mutate buffer
- `test_ringbuffer_render_summary_under_token_cap` — 10 entries → ≤200 tokens
- `test_ringbuffer_render_summary_truncates_middle_when_overcapacity` — long entries get middle-elided
- `test_ringbuffer_render_summary_includes_recency_markers` — "12s ago" / "3 min ago" in output

### 7.2 Polling primitives (6 tests)
- `test_poll_active_window_via_skills_active_window` — calls into skills/active_window.py
- `test_poll_screenshot_text_timeout` — OCR > 100ms → `ocr_skipped=true`, snapshot OK
- `test_poll_clipboard_only_emits_on_change` — same content twice → second poll has `clipboard=None`
- `test_poll_recent_files_filters_by_mtime` — files older than 5min excluded
- `test_poll_idle_seconds_via_quartz` — mocked Quartz returns expected float
- `test_poll_emits_observation_tick_with_metadata_only` — no content fields in audit emit

### 7.3 Idle classifier + cadence (4 tests)
- `test_cadence_active_when_idle_lt_60s` — picks `cadence_active_s`
- `test_cadence_idle_when_idle_ge_60s` — picks `cadence_idle_s`
- `test_cadence_transition_active_to_idle` — transition logged
- `test_cadence_respects_config_overrides` — config.json override applied

### 7.4 Injection contract (§X) (10 tests)
- `test_inject_always_for_local_transport`
- `test_inject_skipped_for_mcp_transport`
- `test_inject_possessive_match_my_X` — "what's my Stripe balance" → injects
- `test_inject_possessive_match_this_Y` — "summarize this PR" → injects
- `test_inject_possessive_filtered_by_stop_noun` — "what time is it" → does NOT inject
- `test_inject_continuation_continue_email` — "continue the email" → injects
- `test_inject_continuation_where_was_i` — "where was I" → injects
- `test_inject_skill_flag_overrides_pattern` — skill with `SKILL_NEEDS_OBSERVATION=True` → injects regardless
- `test_inject_emits_audit_only_on_inject` — skipped path emits zero audit events
- `test_inject_emits_audit_with_reason_and_tokens_and_transport` — all three fields populated

### 7.5 Kill switch + integration (4 tests)
- `test_observer_disabled_skips_polling` — `OBSERVER_ENABLED=false` → no `observation_tick` audit emits
- `test_observer_disabled_skips_injection` — same env → `maybe_inject_observation_summary` returns `(None, "skipped_disabled")`
- `test_observer_disabled_default_is_enabled` — env unset → enabled=True
- `test_observer_audit_inherits_correlation_id_for_injection` — chat-handler op's cid is the cid on `observation_summary_injected`

---

## 8 · Rollback plan

Step 5 introduces:
1. New PM2 service `codec-observer`.
2. ~+30 LOC into `codec_dashboard.py` and `codec_voice.py` (the injection helper call).
3. Two new audit event types.

**Hard revert (if observer crashes loop / leaks memory / breaks chat):**

```bash
# 1. Disable injection at runtime — no PM2 restart needed
echo "OBSERVER_ENABLED=false" >> ~/.codec/.env
# (assuming a future Phase 2 helper reads .env; until then, edit ecosystem.config.js
#  for codec-dashboard / open-codec / codec-mcp-http to add env: { OBSERVER_ENABLED: "false" }
#  and pm2 restart those three)

# 2. Stop the observer process
pm2 stop codec-observer

# 3. (If the chat/voice patches themselves are the problem)
git revert <step-5-merge-commit>
git push
pm2 restart codec-dashboard open-codec
```

**Soft revert (if just the gating logic is wrong):**
- Edit `~/.codec/config.json: observer.summary_max_tokens = 0` → renders empty string, effectively disables injection without disabling polling.
- OR set `OBSERVER_ENABLED=false` in env of dashboard / voice / mcp-http processes only — observer keeps polling, just nothing reads the buffer.

**Audit-event flood guard:**
- If `observation_tick` rate exceeds 100/min (sanity check — the observer fires once per cadence, so >5/min is already abnormal for the configured 60s cadence), the audit_report skill flags it. No automatic shutoff; user reviews the alert.

---

## 9 · Open questions for reviewer

These are decisions the locked Q1-Q6 resolutions don't cover. Need answers before implementation begins.

**Q5.1 — OCR fallback when screenshot_text fails.**
The OCR step is the slowest poll component (~30-100ms p50, occasional 500ms+). When it times out (`ocr_timeout_ms=100`), we currently plan to set `ocr_skipped=true` and continue with empty `screenshot_ocr`. **Alternative:** retry once with a longer timeout (200ms) before giving up. **Recommendation:** retry once. OCR is the single richest signal; one retry is cheap.

**Q5.2 — Clipboard image handling.**
If the clipboard contains an image (screenshot, copied photo), do we OCR it as well, or just emit `content_type="image_blob_redacted"` and move on? **Recommendation:** redact for v1. Image OCR doubles per-poll cost. Add behind a config flag in a v2 if useful.

**Q5.3 — Stop-noun list for the possessive-without-context regex.**
The §X.1 rule is `\b(my|this|that|these|those|the)\s+(\w+)\b` AND noun-not-in-stop-list. The stop-list determines false-positive rate. Starter list: `{question, time, day, week, month, year, thing, stuff, way, point, idea, problem, issue, plan, file, line, error, bug, code, function, variable, name, list, item, value}`. **Recommendation:** ship with this 25-word list and a `~/.codec/config.json: observer.stop_nouns` override. Iterate based on real misfires.

**Q5.4 — Injection-reason audit cardinality.**
With `extra.injection_reason` getting one of 5 values (`always_local` / `possessive_match` / `continuation_match` / `skill_flag` / `skipped_no_match`), and the latter being NOT emitted (per §X.2), the live cardinality is 4. Audit_report should break-out by reason — should this be folded into the existing `audit_report` skill's output, or a new sub-report? **Recommendation:** fold into existing `audit_report`. One more event-type-grouping, no schema change.

**Q5.5 — Observer's own observability under load.**
What's the right cadence-degradation strategy if poll takes >150ms (>cadence_active_s/2)? Options:
1. Skip the next poll cycle (drop frame).
2. Keep polling but emit a `observation_tick_slow` audit event.
3. Auto-degrade to idle cadence until p95 recovers.
**Recommendation:** option 2 for v1 (visibility without behavior change). Option 3 only if real workloads show degradation.

**Q5.6 — Buffer state inspection from PWA.**
For debugging: do we add a `/api/observer/buffer` PWA endpoint that returns the current 10-min buffer (or its summary)? Privacy concern: anyone with PWA auth can read recent screen state. **Recommendation:** add it but **gated behind `?debug=1` query param + dashboard auth + emit a `observer_buffer_inspected` audit event when invoked**. Not exposed in normal dashboard UI; only for explicit debugging.

**Q5.7 — Step 6/7 dependency timing.**
Step 6 (Triggers) wants to read `_GLOBAL_BUFFER.snapshot()` to evaluate triggers. Step 7 (Shift Report) wants persisted summaries. **Should Step 5 ship with the public API for both, even though only the injection path is exercised?** **Recommendation:** yes. Forces us to commit to the API surface now, prevents Step 5 → Step 6 churn. Cost: +~30 LOC for the unused-yet methods. Worth it.

---

## 10 · Diff inventory — what gets shipped at implementation time

| File | LOC delta | Status |
|---|---|---|
| `codec_observer.py` (new) | ~+400 | new file |
| `tests/test_observer.py` (new) | ~+400 | 30 tests |
| `codec_audit.py` | ~+10 | 2 event constants + frozenset addition |
| `codec_dashboard.py` | ~+15 | one injection-helper call in chat handler |
| `codec_voice.py` | ~+15 | one injection-helper call in voice handler |
| `ecosystem.config.js` | ~+12 | new `codec-observer` PM2 entry |
| `AGENTS.md` | ~+30 | §3 + §6 + §7 + §10 updates |
| `docs/PHASE2-STEP5-DESIGN.md` (this file) | already created | ships in the Step 5 PR |
| `docs/PHASE2-BLUEPRINT.md` | already created | ships in the Step 5 PR (foundation for Steps 6+7) |
| **Total functional + tests** | **~+880** | |

Compared to:
- Phase 1 Step 1 (~+250 functional + 460 tests)
- Phase 1 Step 2 (~+660 functional + 880 tests)
- Phase 1 Step 3 (~+1,050 functional + 850 tests)
- Phase 1 Step 4 (~+360 functional + 360 tests)

Step 5 is roughly the size of Step 2 — modest.

---

## Appendix A · Source spec (verbatim from blueprint)

See `docs/PHASE2-BLUEPRINT.md` §"Step 5". This design doc resolves the open questions left in the blueprint and adds the Q5-override §X folded-in detail.
