# Phase 2 — COMPLETE

**Date:** 2026-05-02 20:53 CEST
**Status:** All 3 steps merged + production-stable.
**Phase 3 planning:** awaiting explicit go-ahead — not automatic.

---

## Merge commits (chronological)

| Step | PR | Merge SHA | Title | Sign-off |
|---|---|---|---|---|
| 5 | #9 | `824a52f` | feat(observer): Continuous Observation Loop (RingBuffer + injection contract) | T+0 ok; 65 `observation_tick` + 97 `observation_tick_slow` emits in `audit.log` |
| 6 | #11 | `2d2ff3f` | feat(triggers): Trigger System (matcher + cooldown + consent) | T+0 ok; codec-observer `_eval_triggers(snapshot)` integrated; routes/triggers.py mounted |
| 7 | #12 | `0e40687` | feat(shift_report): end-of-day shift report | T+0 ok; live `shift_report_started`/`_completed` paired emits at `2026-05-02T18:49:40Z` (`cid=5f188e5485e5`) |

**Hotfix that landed during Step 5 deployment:**

| PR | Merge SHA | What |
|---|---|---|
| #10 | `26e6add` | hotfix: `observer.ocr_enabled` config flag — bypass `screencapture` popup storm when macOS Screen Recording permission not yet granted to `python3.13` and to `node` (PM2 parent). Default `true`; user runtime config patched to `false` until both permissions explicitly granted. |

**Main HEAD at Phase 2 close:** `0e40687` (Merge PR #12) → followed by Phase 2 sign-off + this doc commit.

---

## What Phase 2 delivered

### Step 5 — Continuous Observation Loop
- `codec_observer.py` (~810 LOC NEW): PM2-managed daemon `codec-observer`. `RingBuffer` keeps the last 10 minutes of observation snapshots in RAM only (no disk persistence). Polls active window + clipboard digest + (optional) screenshot OCR every 5 s; lazy-imports Quartz with graceful non-mac fallback.
- `§X Observation Injection Contract` (Q5 override): `maybe_inject_observation_summary()` always injects for `transport=local` (chat / voice). Cloud transports (`mcp`) gate on possessive pronoun OR continuation phrase OR explicit `SKILL_NEEDS_OBSERVATION` flag.
- Integration: `codec_dashboard.py` chat handler (+59 LOC) and `codec_voice.py` voice handler (+18 LOC) call `maybe_inject_observation_summary()` before LLM dispatch.
- Q5.1 OCR retry-once logic; Q5.2 image-redaction never logs raw pixels; Q5.3 stop-noun list filters trivial captures; Q5.4 audit cardinality (one tick per 5s); Q5.5 slow-poll degraded mode emits `observation_tick_slow`; Q5.6 `/api/observer/buffer?debug=1` PWA debug endpoint; Q5.7 forward-compat snapshot schema reserves keys for Step 6/7.
- 5 audit events: `observation_tick`, `observation_tick_slow`, `observation_summary_injected`, `observer_started`, `observer_stopped`.
- Kill switch: PM2 `codec-observer` `pm2 stop` + `observer.enabled: false` in `~/.codec/config.json`.
- 33 new passing tests (`tests/test_observer.py`, includes the 3 `ocr_enabled` config-flag tests added during the hotfix).

### Step 6 — Trigger System
- `codec_triggers.py` (~520 LOC NEW): declarative `SKILL_OBSERVATION_TRIGGER` per skill. 5 matcher types — `window_title_match`, `clipboard_pattern`, `file_change`, `time`, `compound`. Cooldowns held in RAM (per-skill, configurable seconds). Persistent kill state at `~/.codec/triggers_killed.json` (atomic tmp+rename). `evaluate(snapshot)` returns the list of triggers fired this tick; `dispatch()` calls `codec_dispatch.run_skill` (with optional `codec_ask_user.ask` confirmation gate).
- Stable `sha8` key per `(skill_name, trigger_type, params_hash)` so `triggers_killed.json` survives skill rename without resurrecting an intentionally-killed trigger.
- AST extraction: `codec_skill_registry.py` (+15 LOC) walks every skill module, extracts `SKILL_OBSERVATION_TRIGGER` + `SKILL_NEEDS_OBSERVATION` constants without importing the module.
- Integration: `codec_observer.py` calls `_eval_triggers(snapshot)` after `_emit_observation_tick`, in `try/except` so a broken trigger never breaks the observation loop.
- PWA endpoints: `routes/triggers.py` (NEW, ~95 LOC) — `GET /api/triggers` (list), `GET /api/triggers/{key}` (detail), `POST /api/triggers/{key}/kill` (toggle kill).
- 3 audit events: `trigger_fired`, `trigger_skipped`, `trigger_killed`.
- Kill switches: per-trigger via PWA POST, OR full-system via `TRIGGERS_ENABLED=false`, OR per-skill by simply not declaring `SKILL_OBSERVATION_TRIGGER`.
- 35 new passing tests (`tests/test_triggers.py`, all mocking `codec_dispatch.run_skill`).

### Step 7 — Shift Report
- `skills/shift_report.py` (~470 LOC NEW, 22151 bytes installed at `~/.codec/skills/shift_report.py`): assembles a 5-section markdown report — `## Completed tasks` / `## Blocked or stuck moments` / `## Observed work patterns` / `## Pending questions` / `## Tomorrow`. Per-day dedup via atomic state at `~/.codec/shift_report_state.json` (one report per local-date, idle/time path; manual path always fires).
- 3 trigger paths: `manual` (chat or MCP invocation), `time` (daily-at-hour:minute), `idle` (continuous idle ≥ N minutes). Time + idle paths live inside `codec_observer.py:_maybe_fire_shift_report(idle_seconds)` called every observation tick.
- Public API: `run(task)` (manual entrypoint, used by chat / MCP) and `run_with_trigger_kind(kind)` (used by observer for `time` / `idle`).
- Skill metadata: `SKILL_NAME = "shift_report"`, `SKILL_TRIGGERS = ["shift report", "shift-report", "daily shift report", "what did i do today", "summarize my day", "today's summary", "end of day report", "eod report"]`, `SKILL_MCP_EXPOSE = True`.
- 2 audit events: `shift_report_started`, `shift_report_completed` (paired `correlation_id`, `extra.trigger_kind` ∈ `{manual, time, idle}`, `extra.sections_included`, `extra.word_count`, `extra.audit_records_scanned`).
- Kill switches: `SHIFT_REPORT_ENABLED=false`, OR `shift_report.enabled: false` in `~/.codec/config.json`, OR remove `~/.codec/skills/shift_report.py` (skill not discovered → not callable).
- 20 new passing tests (`tests/test_shift_report.py`, all filesystem-mocked to `tmp_path`).

---

## Audit envelope `schema:1` — all Phase 2 events live in production

Captured from `~/.codec/audit.log` at 2026-05-02 20:53 CEST:

| Event | Source | Count | Phase | Status |
|---|---|---|---|---|
| `observation_tick` | codec-observer | 65 | Step 5 | ✅ live |
| `observation_tick_slow` | codec-observer | 97 | Step 5 | ✅ live (graceful-degraded path active because `ocr_enabled=false`) |
| `observation_summary_injected` | codec-dashboard / codec-voice | 0 | Step 5 | dormant — needs chat-handler call to fire |
| `trigger_fired` | codec-triggers | 0 | Step 6 | dormant — no `SKILL_OBSERVATION_TRIGGER` declared yet on any installed skill |
| `trigger_skipped` | codec-triggers | 0 | Step 6 | dormant — same reason |
| `trigger_killed` | codec-triggers | 0 | Step 6 | dormant — no kills issued |
| **`shift_report_started`** | **codec-shift-report** | **7** | **Step 7** | ✅ live (paired) |
| **`shift_report_completed`** | **codec-shift-report** | **7** | **Step 7** | ✅ live (paired) |

(Step 5 + Step 7 events directly observable. Step 6 events are dormant by design — the trigger evaluator runs every observation tick, but no skill in the runtime currently declares a `SKILL_OBSERVATION_TRIGGER` constant. As soon as one is declared and AST-discovered at next PM2 restart, `trigger_fired` / `trigger_skipped` will populate.)

**Most recent paired emit (live deployment proof):**

```
2026-05-02T18:49:40.547+00:00  shift_report_started    cid=5f188e5485e5  trigger_kind=manual
2026-05-02T18:49:40.555+00:00  shift_report_completed  cid=5f188e5485e5  sections_included=2  word_count=69  audit_records_scanned=305  duration_ms=8.28
```

---

## Skill `shift_report` — registered and observable

```bash
$ ls -la ~/.codec/skills/shift_report.py
-rw-r--r--@ 1 mickaelfarina  staff  22151 May  2 20:49 /Users/mickaelfarina/.codec/skills/shift_report.py
```

```bash
$ pm2 list | grep codec-observer
│ 40 │ codec-observer  │ default │ N/A │ fork │ 46482 │ 3m │ 3 │ online │ 0% │ 0b │ mickael… │ disabled │
```

```bash
$ tail ~/.codec/notifications.json | grep shift_report
{
  "id": "notif_033ec308cd",
  "type": "shift_report",
  "title": "CODEC Shift Report — 2026-05-02",
  "body": "# CODEC Shift Report — 2026-05-02\n\n_Generated 20:49 via `manual` trigger. Window: last 24h._\n..."
}
```

Confirmed:
1. Skill installed at `~/.codec/skills/shift_report.py` ✅
2. Public API `shift_report.run("shift report")` returns success ✅ (live test 20:49 CEST)
3. Audit emits paired `started` + `completed` with shared `correlation_id` ✅
4. Notification posted with `type="shift_report"` and full markdown body ✅
5. State files clean: `~/.codec/shift_report_state.json` only created on first `time`/`idle` fire (manual path bypasses dedup by design) ✅

---

## Final test counts

| Suite | Pass | Fail | Skip |
|---|---|---|---|
| Pre-Phase-2 baseline (Phase 1 close) | 732 | 20 | 73 |
| After Step 5 (observer) | 765 | 20 | 73 |
| After Step 6 (triggers) | 800 | 20 | 73 |
| After Step 7 (shift_report) | 823 | 20 | 73 |

**Net Phase 2 contribution: +91 passing tests, 0 new failures, 0 new skips.**

The 20 baseline failures are the same pre-existing failures from Phase 1 — all classified in `docs/PHASE1-STEP1-PREMERGE-AUDIT.md` / Step 2 / Step 3 audits. None were caused by Phase 2 work, none have been resolved by Phase 2 work. They remain on the deferred-fix list in `docs/known-issues.md`.

---

## PM2 services state at Phase 2 close

| Service | Status | Notes |
|---|---|---|
| `codec-dashboard` | online | Phase 2 Step 5 + Step 6 routes mounted; chat-handler observation injection active |
| `codec-mcp-http` | online | claude.ai connections live; shift_report exposed via `SKILL_MCP_EXPOSE=True` |
| `codec-heartbeat` | online | 20-min daemon loop; all 5 service health checks ✅ |
| **`codec-observer`** | **online** | **NEW — 5 s polling loop + trigger evaluator + `_maybe_fire_shift_report` time/idle scheduler** |
| `codec-autopilot` | **stopped** | intentional, per user request |

Other PM2 processes (cloudflared, kokoro-82m, qwen3.6, whisper-stt, ava-*, sentora-*, etc.) are pre-existing and unrelated to Phase 2.

---

## State files clean

| File | State |
|---|---|
| `~/.codec/skills/shift_report.py` | installed, 22151 bytes |
| `~/.codec/plugins/self_improve.py` | installed, 17722 bytes (Phase 1 Step 4 — unchanged) |
| `~/.codec/shift_report_state.json` | absent (manual path bypasses dedup; will be created on first `time` / `idle` fire) |
| `~/.codec/triggers_killed.json` | absent (no kills issued) |
| `~/.codec/pending_questions.json` | 0 entries |
| `~/.codec/notifications.json` `type="shift_report"` | 1 (the live deployment fire test) |
| `~/.codec/notifications.json` `type="question"` | 0 |
| `/tmp/codec_*.txt` | 0 files |
| Apple Reminders (incomplete) | 0 |
| Apple Notes / Calendar entries created by Phase 2 | 0 |

---

## Process improvements landed during Phase 2

1. **Observer / observation injection contract is `transport`-aware**: `transport=local` always injects, `transport=mcp` requires possessive pronoun OR continuation phrase OR explicit `SKILL_NEEDS_OBSERVATION` flag. Stops cloud-side LLM hallucinations from polluting context with stale local screenshot state.

2. **`ocr_enabled` config-flag pattern**: when a feature requires a macOS TCC permission that may not yet be granted to a specific Python interpreter / PM2 parent process, ship the feature with a default-true config flag AND graceful-degraded code path. User can flip the flag to `false` before any popup storm starts. Pattern: prove the feature works in code, prove the popup storm in production, bisect to the offending call, add the flag, document the permission grant procedure.

3. **`screencapture` popup-storm root cause documented**: `ThreadPoolExecutor`'s `with` block calls `shutdown(wait=True)` on exit, BLOCKING until the thread finishes — so a "100ms timeout" inside the executor was actually waiting ~5 s while the popup was open. Plus the retry triggered a SECOND popup. The fix (skip the executor entirely when `ocr_enabled=False`) is documented in the Step 5 hotfix postmortem (`PR #10` description).

4. **Stable `sha8` keys for kill state**: `triggers_killed.json` keys on `sha8(skill_name + trigger_type + params_hash)` so the kill survives skill rename. Pattern reusable for any feature with persistent per-instance state.

5. **Per-day dedup via atomic state file**: `shift_report_state.json` keyed by `local_date` (UTC offset honored). `time`/`idle` trigger paths early-exit if a report has already been generated today. `manual` path bypasses dedup so the user can always re-run on demand. Pattern reusable for any "one event per local day" scheduling.

6. **`AGENTS.md` §10 don't-touch list extended**: `codec_observer.py`, `codec_triggers.py`, `skills/shift_report.py`, `routes/triggers.py` added to the "don't refactor without re-running the design doc gate" list.

---

## Phase 3 — awaiting go-ahead

Per user instruction (analog of Phase 1 → 2 transition): **Phase 3 planning begins after user explicit go-ahead — not automatic.**

Open follow-ups that the Phase 2 design / postmortem docs flagged for future work (none block Phase 3 itself):

- **Re-enable Step 5 OCR**: requires explicit grant of macOS Screen Recording to BOTH `/opt/homebrew/opt/python@3.13/bin/python3.13` AND the PM2 parent `node` binary. Procedure documented in `docs/PHASE2-STEP5-DESIGN.md` §macOS-permissions. After grant, set `observer.ocr_enabled: true` in `~/.codec/config.json` and `pm2 restart codec-observer`. Verify by tailing `audit.log` for `observation_tick` (not `observation_tick_slow`).
- **First real `SKILL_OBSERVATION_TRIGGER` declaration**: at least one installed skill should declare a trigger so `trigger_fired` audit events populate. Candidates: `chrome_open` (window-title-match `^Slack`), `qr_generator` (clipboard-pattern URL detection). Phase 2 Step 6 ships the system; Phase 3 would ship the first real triggers.
- **Step 7 dedup edge case at midnight**: `shift_report_state.json` keys on local-date. If user works through 23:59 → 00:00 boundary, an `idle` fire at 00:01 will generate a fresh report for the new day, but the previous day's idle work won't be captured if the user was non-idle through 23:50. Could be tightened by adding a `last_seen_active_at` field to the state file.
- **MCP exposure for trigger management**: `routes/triggers.py` is PWA-only. A future PR could expose `triggers_list` / `triggers_kill` as MCP tools so claude.ai can disable a runaway trigger remotely.
- **Old known-issues**: the 20 pre-existing failures in `docs/known-issues.md` are still deferred. Could be cleaned up in a separate housekeeping PR.

---

*Phase 2 complete. Surfacing for user review. No automatic Phase 3 transition.*
