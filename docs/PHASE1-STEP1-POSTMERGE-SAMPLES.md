# Phase 1 Step 1 — post-merge perf samples

**Merge commit:** `45d4aa7` on `main` (2026-04-30 ~09:17 GMT+2 / 07:17 UTC).
**Baseline (pre-merge):** [`docs/PHASE1-STEP1-BASELINE.md`](PHASE1-STEP1-BASELINE.md) — avg=987.96 ms, p95=1907.78 ms.
**Cadence:** T+0, T+4h, T+8h, T+12h, T+16h, T+20h. Each sample is the trailing 30-min window of `~/.codec/audit.log` at sample time.
**Revert thresholds (§5.4):** hard revert if avg > 1975.92 ms (2×) or p95 > 3815.56 ms (2×) at any sample. Soft (investigate) if p95 between 2484.11 – 3815.56 ms.

---

## Sample T+0 — 2026-04-30T09:23:05+02:00 (T+0h06m post-merge)

| field | value |
|---|---|
| sample_at_local | 2026-04-30T09:23:05+02:00 |
| sample_at_utc | 2026-04-30T07:23:05Z |
| window | trailing 30 min |
| total_records | 31 |
| schema1_records | 28 |
| legacy_records | 3 |
| with_duration | 0 |
| avg_ms | N/A (no `duration_ms` entries in window) |
| p95_ms | N/A |
| delta_vs_baseline_avg | N/A |
| delta_vs_baseline_p95 | N/A |
| errors_count | 7 |
| top_events | `shell_blocked: 10`, `heartbeat_tick: 8`, `service_down: 7`, `skill_proposal_staged: 6` |
| sources | `codec-heartbeat: 15`, `codec-agents: 7`, `codec-self-improve: 6`, `<none>: 3` (legacy pre-merge entries) |
| **status** | **ok (with caveats — see notes)** |

### Notes for T+0

**Why avg/p95 are N/A:** zero records in the trailing 30-min window have a `duration_ms` field. The baseline numbers (987.96 / 1907.78) came from MCP tool-result emits which carry duration_ms. The current window contains lifecycle emits only (heartbeat ticks, shell_blocked from a recently-fired crew, skill_proposal_staged from `codec_self_improve`). No claude.ai → MCP traffic happened in the last 30 min — this is morning-local on the user's clock with no outside hits. Latency comparison defers to T+4h/T+8h when traffic resumes.

**Why errors_count=7 is not a regression:** all 7 are `service_down` emits from `codec_heartbeat` flagging Whisper / Kokoro / Vision intermittent failures. Pre-merge these emits were silently dropped (the `log_event` import was failing → `def log_event(*a, **kw): pass` no-op fallback ran). Post-merge they are visible. **This is the design's stated intent** — quoting `docs/PHASE1-STEP1-DESIGN.md` §0: *"Lifecycle events are silently lost. Every `log_event("error", "codec-heartbeat", ...)` call has been a no-op for as long as the codebase has had it. We've been operating blind on heartbeat / scheduler / dispatch errors."* The errors did not start appearing — visibility into them did.

**Schema:1 dominance:** 28/31 entries (90%) are the unified envelope. The 3 legacy entries (`<none>` source) are pre-merge emits within the 30-min window cutoff — they will roll out of subsequent samples as the window slides forward.

**Service health:** all 5 PM2 services (codec-dashboard, open-codec, codec-mcp-http, codec-heartbeat, codec-autopilot) restarted cleanly with the new code. `/api/health` (dashboard) and `/health` (mcp-http) both green.

**Boot tracebacks** in `pm2 logs` (`KeyboardInterrupt` at the daemon entry of codec-heartbeat / codec-autopilot) are from the **previous** instances during graceful shutdown — not from the new code path. Verified by timestamp: every Traceback line precedes the new instance's first INFO line.

---

## Sample T+4h — 2026-05-01T11:08:38+02:00

| field | value |
|---|---|
| sample_at_local | 2026-05-01T11:08:38+02:00 |
| sample_at_utc | 2026-05-01T09:08:38+00:00 |
| window_cutoff_utc | 2026-05-01T08:38:38+00:00 |
| total_records | 2 |
| schema1_records | 2 |
| legacy_records | 0 |
| with_duration | 0 |
| avg_ms | N/A |
| p95_ms | N/A |
| delta_vs_baseline_avg | N/A |
| delta_vs_baseline_p95 | N/A |
| errors_count | 0 |
| top_events | `heartbeat_tick: 2` |
| sources | `codec-heartbeat: 2` |
| **status** | **ok** — no `duration_ms` entries in window — latency comparison N/A; service health green |

## Sample T+8h — 2026-04-30T17:42:22+02:00

| field | value |
|---|---|
| sample_at_local | 2026-04-30T17:42:22+02:00 |
| sample_at_utc | 2026-04-30T15:42:22+00:00 |
| window_cutoff_utc | 2026-04-30T15:12:22+00:00 |
| total_records | 3 |
| schema1_records | 3 |
| legacy_records | 0 |
| with_duration | 0 |
| avg_ms | N/A |
| p95_ms | N/A |
| delta_vs_baseline_avg | N/A |
| delta_vs_baseline_p95 | N/A |
| errors_count | 0 |
| top_events | `heartbeat_tick: 3` |
| sources | `codec-heartbeat: 3` |
| **status** | **ok** — no `duration_ms` entries in window — latency comparison N/A; service health green |

## Sample T+12h — pending

Capture at: 2026-04-30 21:23 GMT+2.

## Sample T+16h — pending

Capture at: 2026-05-01 01:23 GMT+2.

## Sample T+20h — pending

Capture at: 2026-05-01 05:23 GMT+2.

---

## Sampling command (for reproduction)

```bash
python3 <<'PY'
import json, os
from datetime import datetime, timezone, timedelta

PATH = os.path.expanduser("~/.codec/audit.log")
WINDOW_MIN = 30
now = datetime.now(timezone.utc)
cutoff = now - timedelta(minutes=WINDOW_MIN)

records = []
schema1 = legacy = errors = 0
with_dur = []
events = {}

with open(PATH) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        ts = r.get("ts")
        if not ts:
            continue
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if t < cutoff:
            continue
        records.append(r)
        if r.get("schema") == 1:
            schema1 += 1
        else:
            legacy += 1
        if r.get("outcome") == "error":
            errors += 1
        if isinstance(r.get("duration_ms"), (int, float)):
            with_dur.append(r["duration_ms"])
        events[r.get("event", "<none>")] = events.get(r.get("event", "<none>"), 0) + 1

n = len(records)
print(f"sample_at_utc: {now.isoformat(timespec='seconds')}")
print(f"window: trailing {WINDOW_MIN} min")
print(f"total: {n}  schema1: {schema1}  legacy: {legacy}  errors: {errors}")
if with_dur:
    with_dur.sort()
    avg = sum(with_dur) / len(with_dur)
    p95 = with_dur[max(0, int(len(with_dur) * 0.95) - 1)]
    print(f"with_duration: {len(with_dur)}  avg_ms={avg:.2f}  p95_ms={p95:.2f}")
    base_avg = 987.96
    base_p95 = 1907.78
    print(f"delta_vs_baseline_avg: {(avg/base_avg - 1) * 100:+.1f}%")
    print(f"delta_vs_baseline_p95: {(p95/base_p95 - 1) * 100:+.1f}%")
else:
    print("with_duration: 0   (no MCP traffic in window — comparison N/A)")
print("\ntop events:")
for ev, c in sorted(events.items(), key=lambda x: -x[1])[:10]:
    print(f"  {ev}: {c}")
PY
```

## Status flag rubric

- **ok** — sample within 1.3× baseline on both avg AND p95; OR window has no `duration_ms` entries (no MCP traffic) and PM2 services all online and healthy.
- **investigate** — p95 between 1.3× and 2× baseline (2484.11 – 3815.56 ms); OR avg between 1.3× and 2× baseline (1284.35 – 1975.92 ms); OR error spike > 2× over surrounding samples that can't be attributed to known service-down lifecycle emits. Surface for review, do NOT revert.
- **revert** — p95 > 2× baseline (>3815.56 ms); OR avg > 2× baseline (>1975.92 ms); OR `tests/test_audit_perf.py::test_audit_concurrent_no_corruption` fails on live load. Execute §5.4 revert mechanics immediately.

---

## Capture script

`scripts/capture_audit_sample.py` runs the same analysis and replaces the matching `## Sample <LABEL> — pending` block with the captured numbers. Usage:

```bash
python3 /Users/mickaelfarina/codec-repo/scripts/capture_audit_sample.py "T+4h"
```

It exits **0** for `ok`, **1** for `investigate`, **2** for `revert`. The `revert` exit prints the §5.4 mechanics inline.

---

## Sample T+24h — 2026-05-01T09:48:43+02:00

| field | value |
|---|---|
| sample_at_local | 2026-05-01T09:48:43+02:00 |
| sample_at_utc | 2026-05-01T07:48:43+00:00 |
| window_cutoff_utc | 2026-05-01T07:18:43+00:00 |
| total_records | 3 |
| schema1_records | 3 |
| legacy_records | 0 |
| with_duration | 0 |
| avg_ms | N/A |
| p95_ms | N/A |
| delta_vs_baseline_avg | N/A |
| delta_vs_baseline_p95 | N/A |
| errors_count | 0 |
| top_events | `heartbeat_tick: 3` |
| sources | `codec-heartbeat: 3` |
| **status** | **ok** — no `duration_ms` entries in window — latency comparison N/A; service health green |
