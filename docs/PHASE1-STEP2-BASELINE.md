# Phase 1 Step 2 — pre-merge MCP latency baseline

**Captured:** 2026-04-30 13:18 GMT+2 (immediately before opening the
phase1-step2-plugin-hooks PR).

## Source: reuse Step 1 baseline

Per `docs/PHASE1-STEP2-DESIGN.md` §10.4: **"Reuse the Step 1 baseline
numbers."** Same hardware, same MCP traffic pattern, same workload
profile. Step 1 was merged 2026-04-30 07:17 UTC and has been in
production for ~6 hours by the time this PR opens.

The Step 2 hook layer is the only thing changing from Step 1's
production state — adding a no-plugin run_with_hooks wrapper at four
insertion points. Per design §9.5 the wrapper overhead with zero
plugins is < 1 ms/call (validated by `tests/test_hook_audit_perf.py::
test_hook_overhead_under_1ms_with_zero_hooks`). With zero plugins
registered (the production state at merge time — `~/.codec/plugins/`
is empty), the wrapper adds ~one dataclass construction + an empty
list iteration + a passthrough call. That should not move the MCP
p95 measurably.

## Numbers (anchor — copied from Step 1)

| metric              | value     |
|---------------------|-----------|
| total records (Step 1 sample window) | 292 |
| records w/ duration | 203       |
| avg duration_ms     | **987.96**|
| p95 duration_ms     | **1907.78** |
| Step 1 sample window | 2026-04-21 09:06 → 2026-04-29 23:18 UTC |

Live audit.log at the moment of this PR opening contains 122 records
since the Step 1 PM2 restart (07:17 → 11:13 UTC). The duration_ms
sample (n=8) is too small to be a meaningful baseline — most live
records are heartbeat_tick / service_down lifecycle emits with no
duration. This is consistent with morning-local low-traffic conditions
(same shape as Step 1's T+0 sample which also had n_with_duration=0).

## Revert thresholds (from design §10.4)

Same as Step 1:

| trigger | value | action |
|---|---|---|
| p95 > 2× baseline | **> 3815.56 ms** | hard revert |
| avg > 2× baseline | **> 1975.92 ms** | hard revert |
| p95 between 1.3× and 2× | 2484.11 – 3815.56 ms | investigate |
| `tests/test_hook_audit_perf.py::test_hook_concurrent_no_audit_corruption` fails on live load | (binary) | hard revert |

## Sampling cadence (post-merge)

Identical to Step 1: T+0, T+4h, T+8h, T+12h, T+16h, T+20h. Sample
tracker file: `docs/PHASE1-STEP2-POSTMERGE-SAMPLES.md` (created at
T+0). The capture script `scripts/capture_audit_sample.py` from
Step 1 is reused — no code change needed; just pass the
`"T+Nh-step2"` label.

## Sign-off after 24 hours

If all six samples are within 1.3× baseline AND no audit-corruption
failure on live load AND no `hook_error` flood (>10× normal volume,
which would indicate a buggy production plugin): mark merge as
production-stable in `docs/known-issues.md` per §10.4. Until that
line is added, Phase 1 Step 4 (codec_self_improve plugin migration)
does not start.
