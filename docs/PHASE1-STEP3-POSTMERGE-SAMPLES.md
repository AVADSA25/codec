# Phase 1 Step 3 — post-merge samples

Tracker for the 24-hour post-merge watch on `main` after `59bfbda` (Merge PR #5: Step 3 — AskUserQuestion + stuck detection + step budget).

**Anchor baseline (from `docs/PHASE1-STEP3-BASELINE.md`):** Reusing Step 1's 8-day production sample window — `avg=987.96 ms`, `p95=1907.78 ms` over `n=203`.

**Hard-revert thresholds:**
- `p95 > 3815.56 ms` (2× baseline)
- `avg > 1975.92 ms` (2× baseline)
- Step 3 audit-event flood (any of `ask_user_question_*`, `stuck_*`, `step_budget_exhausted` fires `>10×` normal volume)

---

## Sample T+0 — 2026-05-01 13:47 UTC (15:47 CEST)

**Status:** ok

| Metric | Value |
|---|---|
| Records in 30-min window | 15 |
| Records with `duration_ms` | 0 |
| avg duration_ms | n/a (no duration records yet — process restart window) |
| p95 duration_ms | n/a |
| Step 3 audit events emitted | 0 (`ask_user_question_*`, `stuck_*`, `step_budget_exhausted`) |
| Source distribution | codec-heartbeat: 10, codec-agents: 3, codec-self-improve: 2 |

**Service health (all green):**
- `Dashboard /api/health`: HTTP 200
- `MCP-HTTP /health`: HTTP 200
- `codec_audit.ASKUSER_EVENT_EMIT` importable: ✓ (`ask_user_question_emit`)
- `codec_audit.STUCK_EVENT_WARNING` importable: ✓ (`stuck_warning`)
- `codec_audit.STEP_BUDGET_EXHAUSTED` importable: ✓ (`step_budget_exhausted`)
- `codec_ask_user.ask` importable: ✓
- Step 3 production files present: `codec_ask_user.py` (665 LOC), `skills/ask_user.py` (51 LOC), `skills/stuck.py` (43 LOC)

**State files clean:**
- `~/.codec/pending_questions.json`: 0 entries
- `~/.codec/notifications.json` `type="question"`: 0 entries
- `/tmp/codec_*.txt`: 0 files
- Apple Reminders incomplete: 0 (per user request — no reminders for monitoring this time)

**T+0 sample is sparse (n=0 with duration) because:**
1. PM2 restart cycle finished at ~13:47 UTC; window starts at 13:17 UTC
2. Most heartbeat / self_improve / shell_blocked emits don't carry `duration_ms`
3. The hot path (chat / voice / MCP tool calls) hasn't seen substantial traffic in this short window

This shape matches Step 1 + Step 2 T+0 captures (also `n_with_duration=0`). Status: **ok** because there is no breach of any revert threshold — there is no traffic AT ALL to breach.

**Next sample:** T+4h target = 2026-05-01 17:47 UTC (19:47 CEST). See `docs/PHASE1-STEP3-SAMPLING-COMMANDS.md` for the one-liner to capture it. **No Apple Reminder created** — per user instruction.

---

## Sample T+4h — pending

(Append after running the capture command at T+4h.)

---

## Sample T+8h — pending

---

## Sample T+12h — pending

---

## Sample T+16h — pending

---

## Sample T+20h — pending

---

## 24h sign-off

When all six samples are within thresholds AND there is no Step 3 audit-event flood AND no `tests/test_ask_user.py::test_ambiguous_consent_two_strikes_times_out`-style regression on live load: append a single line to `docs/known-issues.md` marking Phase 1 Step 3 as production-stable. Until that line lands, Phase 1 Step 4 (codec_self_improve plugin migration to a `codec_hooks`-based plugin) does not start.
