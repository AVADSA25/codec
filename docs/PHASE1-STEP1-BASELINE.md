# Phase 1 Step 1 — pre-merge MCP latency baseline

Captured **2026-04-29 23:30 GMT+2** (immediately before opening the
phase1-step1-audit-unification PR). This is the post-merge revert
yardstick per docs/PHASE1-STEP1-DESIGN.md §5.4.

## Source

`~/.codec/audit.log` (live entries, not a synthetic load).

Note: this is the **all-time available window** in the current rotated
audit.log file (`first_ts` → `last_ts` below), not a strict 30-minute
trailing slice. The 30-min slice from §5.4 assumes audit.db (which doesn't
yet exist on this machine — see codec_dashboard.py:782 stale read_events
import); all-time aggregate is the closest pre-merge proxy and serves the
same purpose: a numeric anchor for the post-merge revert decision.

## Numbers

| metric              | value     |
|---------------------|-----------|
| total records       | 292       |
| records w/ duration | 203       |
| avg duration_ms     | **987.96**|
| p95 duration_ms     | **1907.78** |
| first_ts            | 2026-04-21T09:06:54.741+00:00 |
| last_ts             | 2026-04-29T23:18:57.228+00:00 |

## Revert thresholds (from design §5.4)

| trigger | value | action |
|---|---|---|
| p95 > 2× baseline at any sample point | **> 3815.56 ms** | hard revert |
| avg > 2× baseline at any sample point | **> 1975.92 ms** | hard revert |
| p95 between 1.3× and 2× baseline | 2484.11 – 3815.56 ms | investigate, do NOT revert |
| `tests/test_audit_perf.py::test_audit_concurrent_no_corruption` fails on live load | (binary) | hard revert |
| audit_analyzer error rate > 2× baseline (zero baseline = trigger any uptick) | > 0 errors | triage which event |

## Sampling cadence (post-merge)

Every **4 hours** for **24 hours**:
T+0, T+4h, T+8h, T+12h, T+16h, T+20h.

Each sample reruns the same query against the trailing 30-minute window
of `~/.codec/audit.log`, appended to `~/.codec/perf-postmerge.txt` with
timestamp.

## Sign-off

If all six samples are within **1.3× baseline (≤ 2484.11 ms p95, ≤ 1284.35
ms avg)** and no failures triggered: mark merge as production-stable in
`docs/known-issues.md` (or wherever the running stability ledger lives).

## Recompute command

```bash
python3 -c '
import json
records = [json.loads(l) for l in open("/Users/mickaelfarina/.codec/audit.log") if l.strip()]
ds = sorted(r["duration_ms"] for r in records if isinstance(r.get("duration_ms"), (int, float)))
n = len(ds)
print(f"n={n} avg={sum(ds)/n:.2f} p95={ds[int(n*0.95)-1]:.2f}")
'
```

Or, once `audit.db` exists, the SQL form from design §5.4.
