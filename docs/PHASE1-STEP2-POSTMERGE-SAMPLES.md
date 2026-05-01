# Phase 1 Step 2 — post-merge perf samples

**Merge commit:** `15c6f70` on `main` (2026-05-01 ~09:50 GMT+2 / 07:50 UTC).
**Baseline (pre-merge):** [`docs/PHASE1-STEP2-BASELINE.md`](PHASE1-STEP2-BASELINE.md) — reuses Step 1 anchor avg=987.96 ms, p95=1907.78 ms.
**Cadence:** T+0, T+4h, T+8h, T+12h, T+16h, T+20h. Each sample is the trailing 30-min window of `~/.codec/audit.log` at sample time.
**Revert thresholds (§10.4):** hard revert if avg > 1975.92 ms (2×) or p95 > 3815.56 ms (2×) at any sample. Soft (investigate) if p95 between 2484.11 – 3815.56 ms. Also hard-revert on any `hook_error` flood > 10× normal volume (signals a buggy production plugin) or `test_hook_concurrent_no_audit_corruption` failure on live load.

---

## Sample T+0 — 2026-05-01T09:55:37+02:00 (T+0h05m post-merge)

| field | value |
|---|---|
| sample_at_local | 2026-05-01T09:55:37+02:00 |
| sample_at_utc | 2026-05-01T07:55:37+00:00 |
| window_cutoff_utc | 2026-05-01T07:25:37+00:00 |
| total_records | 4 |
| schema1_records | 4 |
| legacy_records | 0 |
| with_duration | 0 |
| avg_ms | N/A (no `duration_ms` entries in window) |
| p95_ms | N/A |
| delta_vs_baseline_avg | N/A |
| delta_vs_baseline_p95 | N/A |
| errors_count | 0 |
| hook_error_count | 0 |
| top_events | `heartbeat_tick: 4` |
| sources | `codec-heartbeat: 4` |
| **status** | **ok** |

### Notes for T+0

**Service health:** all 5 PM2 services restarted cleanly with the new code:
- `codec-dashboard` (09:51:29 GMT+2) — `/api/health` 200, status=ok
- `open-codec` (09:52:14 GMT+2) — boot logs clean
- `codec-mcp-http` (09:52:59 GMT+2) — `/health` 200, all 4 deps green (memory_db, oauth_state, kokoro_tts, qwen_llm)
- `codec-heartbeat` (09:53:46 GMT+2) — first tick all 5 deps green (Kokoro, Whisper, Dashboard, Vision, LLM)
- `codec-autopilot` (09:54:30 GMT+2) — Skill registry: 69 skills discovered

Boot tracebacks in `pm2 logs` (`KeyboardInterrupt` at the daemon entry of codec-heartbeat / codec-autopilot) are from the **previous** instances during graceful shutdown — same pattern as Step 1 (b)'s restart, not from the new code path.

**Why the trailing-30m window only shows heartbeat ticks:** the merge happened ~5 minutes ago; the trailing-30m window captures ~25 min of pre-merge entries (which were a quiet morning) + ~5 min of post-merge entries (just heartbeat ticks so far — the user hasn't sent any MCP traffic since the restart). Same shape as Step 1 T+0.

**Why this is `ok`:** no `duration_ms` entries in the window means latency comparison is N/A — same convention as Step 1's T+0 sample. The `ok` flag is awarded on:
1. PM2 services all online and healthy (verified above)
2. Zero `hook_error` audit events in the 30-min window (any plugin crash would emit one — none did, because the production state has zero plugins in `~/.codec/plugins/`, so hook layer is in pure passthrough mode)
3. Zero `errors_count` (no service_down spikes from heartbeat — Step 1's T+0 had 7 of these, this T+0 has 0, so things are quieter than the Step 1 baseline)
4. 100% schema:1 records (no legacy entries in the window — log has rolled enough since Step 1 merge that legacy entries are gone; clean envelope)

**Why `hook_error_count = 0` is meaningful:** with zero plugins registered (the production state at merge time — `~/.codec/plugins/` is empty), `run_with_hooks` is in pure passthrough mode. Per the §9.5 perf test, that adds <1 ms/call. We expect zero `hook_error` emits ever in this state, since there's no plugin to crash. Any non-zero count in subsequent samples would mean a plugin was added in the meantime — investigate by `grep '"plugin_name"' ~/.codec/audit.log | tail`.

---

## Sample T+4h — pending

Capture at: 2026-05-01 13:55 GMT+2.

## Sample T+8h — pending

Capture at: 2026-05-01 17:55 GMT+2.

## Sample T+12h — pending

Capture at: 2026-05-01 21:55 GMT+2.

## Sample T+16h — pending

Capture at: 2026-05-02 01:55 GMT+2.

## Sample T+20h — pending

Capture at: 2026-05-02 05:55 GMT+2.

---

## Sampling command

Reuse the Step 1 capture script with the Step 2 file as target — the script auto-detects the matching `## Sample T+Nh — pending` block and replaces it. The script lives at `scripts/capture_audit_sample.py` but its hard-coded target is `PHASE1-STEP1-POSTMERGE-SAMPLES.md`. For Step 2, run the inline Python from below (same logic, edits this file in place).

```bash
python3 <<'PY'
import json, os, re
from datetime import datetime, timezone, timedelta
PATH = os.path.expanduser("~/.codec/audit.log")
DOC = "/Users/mickaelfarina/codec-repo/docs/PHASE1-STEP2-POSTMERGE-SAMPLES.md"
LABEL = "T+4h"   # ← change per sample

now = datetime.now(timezone.utc)
cutoff = now - timedelta(minutes=30)
recs = []
with open(PATH) as f:
    for line in f:
        line = line.strip()
        if not line: continue
        try: r = json.loads(line)
        except: continue
        ts = r.get("ts")
        if not ts: continue
        try:
            t = datetime.fromisoformat(ts.replace("Z","+00:00"))
            if t.tzinfo is None: t = t.replace(tzinfo=timezone.utc)
        except: continue
        if t >= cutoff: recs.append(r)
durs = sorted([r["duration_ms"] for r in recs if isinstance(r.get("duration_ms"),(int,float))])
schema1 = sum(1 for r in recs if r.get("schema") == 1)
legacy = len(recs) - schema1
errors = sum(1 for r in recs if r.get("outcome") == "error")
hook_errors = sum(1 for r in recs if r.get("event") == "hook_error")
events = {}
for r in recs:
    e = r.get("event", "<none>")
    events[e] = events.get(e, 0) + 1
sources = {}
for r in recs:
    s = r.get("source", "<none>")
    sources[s] = sources.get(s, 0) + 1
avg = sum(durs)/len(durs) if durs else None
p95 = durs[max(0,int(len(durs)*0.95)-1)] if durs else None
top_ev = ", ".join(f"`{k}: {v}`" for k,v in sorted(events.items(), key=lambda x:-x[1])[:5])
top_src = ", ".join(f"`{k}: {v}`" for k,v in sorted(sources.items(), key=lambda x:-x[1])[:5])

# Status
status = "ok"
if avg is not None and p95 is not None:
    if avg > 1975.92 or p95 > 3815.56: status = "revert"
    elif avg > 1284.35 or p95 > 2484.11: status = "investigate"
if hook_errors > 0:
    status = "investigate (hook_error fires)"

block = f"""## Sample {LABEL} — {now.astimezone().isoformat(timespec='seconds')}

| field | value |
|---|---|
| sample_at_local | {now.astimezone().isoformat(timespec='seconds')} |
| sample_at_utc | {now.isoformat(timespec='seconds')} |
| window_cutoff_utc | {cutoff.isoformat(timespec='seconds')} |
| total_records | {len(recs)} |
| schema1_records | {schema1} |
| legacy_records | {legacy} |
| with_duration | {len(durs)} |
| avg_ms | {f'{avg:.2f}' if avg else 'N/A'} |
| p95_ms | {f'{p95:.2f}' if p95 else 'N/A'} |
| delta_vs_baseline_avg | {f'{(avg/987.96-1)*100:+.1f}%' if avg else 'N/A'} |
| delta_vs_baseline_p95 | {f'{(p95/1907.78-1)*100:+.1f}%' if p95 else 'N/A'} |
| errors_count | {errors} |
| hook_error_count | {hook_errors} |
| top_events | {top_ev or '(none)'} |
| sources | {top_src or '(none)'} |
| **status** | **{status}** |
"""
text = open(DOC).read()
pat = re.compile(rf"## Sample {re.escape(LABEL)} — pending\n(?:.*\n)*?(?=\n## |\Z)", re.MULTILINE)
m = pat.search(text)
if m:
    text = text[:m.start()] + block + text[m.end():]
else:
    text += "\n---\n\n" + block
open(DOC, "w").write(text)
print(f"{LABEL}: status={status}  avg={avg}  p95={p95}  hook_errors={hook_errors}")
PY
```

## Status flag rubric

- **ok** — sample within 1.3× baseline on both avg AND p95; OR window has no `duration_ms` entries (no MCP traffic) and PM2 services all online and healthy AND `hook_error_count == 0`.
- **investigate** — avg between 1.3× and 2× baseline (1284.35 – 1975.92 ms); OR p95 between 1.3× and 2× baseline (2484.11 – 3815.56 ms); OR `hook_error_count > 0`. Surface for review, do NOT revert.
- **revert** — p95 > 2× baseline (>3815.56 ms); OR avg > 2× baseline (>1975.92 ms); OR `tests/test_hook_audit_perf.py::test_hook_concurrent_no_audit_corruption` fails on live load; OR `hook_error_count > 10×` surrounding samples (signals a buggy production plugin). Execute §10.2 revert mechanics immediately.

---

## Sign-off

24h watch sign-off (after T+20h sample lands and all six are within 1.3× baseline) is reserved for `docs/known-issues.md` per §10.4. Until that line is added, Phase 1 Step 4 (codec_self_improve plugin migration) does not start.
