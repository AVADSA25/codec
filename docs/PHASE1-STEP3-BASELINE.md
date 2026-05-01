# Phase 1 Step 3 — pre-merge MCP latency baseline

**Captured:** 2026-05-01 13:36 GMT+2 (immediately before opening the
phase1-step3-askuser-stuck-budget PR).

## Source: reuse Step 1 baseline (same protocol as Step 2)

Per `docs/PHASE1-STEP3-DESIGN.md` §10 (Rollback / monitoring): same
hardware, same MCP traffic pattern, same workload profile. Step 1 was
merged 2026-04-30, Step 2 was merged 2026-05-01 09:55 UTC, both have been
in production with no measured regression at T+0.

The Step 3 changes — three additive features — touch only paths that
did NOT exist before:

1. **AskUserQuestion** is a new opt-in tool; no caller invokes it on the
   hot path. The 600s default timeout means the MCP transport sees zero
   AskUserQuestion traffic until an agent specifically asks for one.
2. **Stuck detection** runs post-tool inside `Agent.run`'s ReAct loop in
   a thread-pool executor (per §2.2). The fast path is a `list.append`
   plus `list.count` on a 5-element ring buffer (~µs). When triggered,
   it emits one audit line and either modifies the result string or
   invokes `ask_user.ask()` (which doesn't return until the user
   answers, but that's not a measured per-call latency — it's a
   user-visible long-running operation).
3. **Step budget** is per-request in `chat_completion` (codec-dashboard,
   not MCP). It does not apply to the `mcp` route by design. MCP traffic
   bypasses the `_StepBudget` instance entirely.

Net effect on MCP p95: **none expected.** The MCP path is
`codec_mcp.tool_fn` → `run_with_hooks` → skill `run()`. None of the
three Step 3 features instruments this path.

## Numbers (anchor — copied from Step 1 / Step 2 baseline)

| metric              | value     |
|---------------------|-----------|
| total records (Step 1 sample window) | 292 |
| records w/ duration | 203       |
| avg duration_ms     | **987.96**|
| p95 duration_ms     | **1907.78** |
| Step 1 sample window | 2026-04-21 09:06 → 2026-04-29 23:18 UTC |

Live audit.log at the moment of this PR opening contains 232 records
since today's rotation (2026-05-01 00:01 → 11:35 UTC). The duration_ms
sample is **n=8**, with avg=607.89 ms / p95=1000.00 ms. Same shape as
the Step 1 / Step 2 T+0 captures: morning-local low-traffic conditions,
mostly heartbeat_tick / service_down lifecycle emits with no duration.
Too small to be a meaningful baseline; the production-stable Step 1/2
8-day sample is the canonical reference.

## Revert thresholds (from design §10)

Same as Step 1 / Step 2 (both passed 24h watch with no breach):

| trigger | value | action |
|---|---|---|
| p95 > 2× baseline | **> 3815.56 ms** | hard revert |
| avg > 2× baseline | **> 1975.92 ms** | hard revert |
| p95 between 1.3× and 2× | 2484.11 – 3815.56 ms | investigate |
| Step 3 audit-event flood (askuser/stuck/step_budget > 10× normal volume) | (binary) | investigate — likely a buggy plugin or runaway agent |
| `tests/test_ask_user.py::test_ambiguous_consent_two_strikes_times_out` fails on live load | (binary) | hard revert |
| `tests/test_stuck_detection.py::test_warning_fires_at_threshold` fails on live load | (binary) | hard revert |

## Sampling cadence (post-merge)

Identical to Steps 1 + 2: T+0, T+4h, T+8h, T+12h, T+16h, T+20h. Sample
tracker file: `docs/PHASE1-STEP3-POSTMERGE-SAMPLES.md` (created at T+0).
The capture script `scripts/capture_audit_sample.py` from Step 1 is
reused — no code change needed; just pass the `"T+Nh-step3"` label.

## Sign-off after 24 hours

If all six samples are within 1.3× baseline AND no audit-corruption
failure on live load AND no Step 3 audit-event flood (>10× normal
volume, which would indicate a runaway agent caught in a stuck loop or
a malformed config that lowers the step budget): mark merge as
production-stable in `docs/known-issues.md` per §10. Until that line
is added, Phase 1 Step 4 (codec_self_improve plugin migration to a
codec_hooks-based plugin) does not start.

## Per-feature kill switches (smoke-tested pre-merge)

If any feature misbehaves in production, it can be disabled independently
via env var (no PM2 restart required for the env var itself, but each
process picks up env on its next restart):

| Env var | Default | What it disables |
|---|---|---|
| `ASKUSER_ENABLED=false` | `true` | `codec_ask_user.ask()` returns `"(skill disabled)"` immediately; no state writes; no audit emit. Stuck detection's escalate=`ask_user` action falls through to "(ask_user failed — agent should self-recover)" string. |
| `STUCK_DETECTION_ENABLED=false` | `true` | `Agent._handle_stuck_post_tool` is bypassed entirely. No ring buffer accumulation, no warn / escalate emits. |
| `STEP_BUDGET_ENABLED=false` | `true` | `_StepBudget.enabled` stays False at construction; `consume()` always returns True; no `step_budget_exhausted` emit. |

Tests: `tests/test_ask_user.py::test_kill_switch_returns_disabled_sentinel`,
`tests/test_stuck_detection.py::test_kill_switch_disables_stuck_detection`,
`tests/test_step_budget.py::test_kill_switch_disables_budget`.
