# Phase 1 Step 3 — sampling commands (plain reference)

> **No Apple Reminders. No Notes. No Calendar events. No notifications.** This file is a plain checklist the user can open IF they want to capture a sample. Nothing pings or interrupts.

## When to capture (suggested cadence — informational only, no enforcement)

Step 3 merged at **2026-05-01 13:47 UTC (15:47 CEST)** as commit `59bfbda`.

| Label | Target time (UTC) | Target time (CEST) |
|---|---|---|
| T+0 | 2026-05-01 13:47 | 15:47 — DONE |
| T+4h | 2026-05-01 17:47 | 19:47 |
| T+8h | 2026-05-01 21:47 | 23:47 |
| T+12h | 2026-05-02 01:47 | 03:47 |
| T+16h | 2026-05-02 05:47 | 07:47 |
| T+20h | 2026-05-02 09:47 | 11:47 |

If you skip a sample, just capture the next one. The cadence is a guideline, not a contract.

## How to capture a sample (one paste-able command)

```bash
LABEL="T+4h"
cd ~/codec-repo
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 << 'PYEOF'
import json
from datetime import datetime, timezone, timedelta
records = []
with open('/Users/mickaelfarina/.codec/audit.log') as f:
    for line in f:
        if line.strip():
            try: records.append(json.loads(line))
            except: pass
records.sort(key=lambda r: r.get('ts',''))
now = datetime.now(timezone.utc)
cutoff = (now - timedelta(minutes=30)).isoformat(timespec='milliseconds')
window = [r for r in records if r.get('ts','') >= cutoff]
durs = [r['duration_ms'] for r in window if r.get('duration_ms') is not None]
print(f'Records in 30-min window: {len(window)}')
print(f'  with duration: {len(durs)}')
if durs:
    durs_s = sorted(durs)
    n = len(durs_s)
    avg = sum(durs)/n
    p95 = durs_s[int(0.95*(n-1))]
    print(f'  avg duration_ms: {avg:.2f}')
    print(f'  p95 duration_ms: {p95:.2f}')
    # Threshold check
    HARD_AVG = 1975.92
    HARD_P95 = 3815.56
    if avg > HARD_AVG or p95 > HARD_P95:
        print(f'⚠️ HARD-REVERT THRESHOLD BREACHED — investigate immediately')
    elif avg > 987.96 * 1.3 or p95 > 1907.78 * 1.3:
        print(f'⚠️ INVESTIGATE — over 1.3× baseline')
    else:
        print('✓ within baseline')
step3_events = {'ask_user_question_emit', 'ask_user_question_answer', 'ask_user_question_timeout',
                'stuck_warning', 'stuck_escalated', 'step_budget_exhausted'}
hits = [r for r in window if r.get('event','') in step3_events]
print(f'Step 3 audit events emitted in window: {len(hits)}')
if len(hits) > 50:
    print(f'⚠️ Step 3 audit-event flood — >10× normal volume; check for runaway agent / config issue')
PYEOF
```

Expected output for a normal sample:
```
Records in 30-min window: <some_int>
  with duration: <some_int>
  avg duration_ms: <some_float>
  p95 duration_ms: <some_float>
✓ within baseline
Step 3 audit events emitted in window: <small_int>
```

## How to append a sample to the tracker

Open `docs/PHASE1-STEP3-POSTMERGE-SAMPLES.md` in any editor and replace one of the `## Sample T+Xh — pending` blocks with the captured numbers. Format matches the `## Sample T+0` block at the top of that file.

Then `git add docs/PHASE1-STEP3-POSTMERGE-SAMPLES.md && git commit && git push` if you want it on remote.

## Service health quick-check (anytime)

```bash
curl -s -o /dev/null -w "Dashboard: %{http_code}\n" http://127.0.0.1:8090/api/health
curl -s -o /dev/null -w "MCP-HTTP:  %{http_code}\n" http://127.0.0.1:8091/health
pm2 list | grep -E "codec-(dashboard|mcp-http|heartbeat|open-codec)"
```

Both endpoints should return `200`. All four PM2 processes should show `online`.

## State-file health quick-check (anytime)

```bash
echo "pending_questions: $(/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 -c "import json; print(len(json.load(open('/Users/mickaelfarina/.codec/pending_questions.json')).get('pending_questions', [])))")"
echo "question notifs:   $(/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 -c "import json; print(len([n for n in json.load(open('/Users/mickaelfarina/.codec/notifications.json')) if n.get('type')=='question']))")"
echo "/tmp/codec_*.txt:  $(find /tmp -maxdepth 1 -name 'codec_*.txt' 2>/dev/null | wc -l | tr -d ' ')"
echo "Apple Reminders:   $(osascript -e 'tell application "Reminders" to count reminders whose completed is false' 2>&1)"
```

All four should be `0` in normal operation. Non-zero `pending_questions` is OK if a real agent is waiting on a real user answer; non-zero `/tmp/codec_*.txt` is suspicious and points to a regression in the SKIP_SKILLS hotfix.

## Per-feature kill switches (if anything misbehaves)

Set the env var BEFORE PM2 restart of the affected process:

```bash
ASKUSER_ENABLED=false      pm2 restart codec-dashboard codec-mcp-http
STUCK_DETECTION_ENABLED=false  pm2 restart codec-dashboard
STEP_BUDGET_ENABLED=false  pm2 restart codec-dashboard
```

Each disables its feature without modifying production code or restarting other services.

## When to mark Phase 1 Step 3 production-stable

When all six samples (T+0 through T+20h) are within 1.3× baseline AND no Step 3 audit-event flood AND no two-strike consent or stuck-warning regression on live load: append a single line to `docs/known-issues.md`:

```
- Phase 1 Step 3 (commit 59bfbda) — production-stable as of <date>
```

Until that line lands, Phase 1 Step 4 does NOT start.

## What this file does NOT do

- Does not create Apple Reminders.
- Does not create Apple Notes.
- Does not create Calendar events.
- Does not send notifications.
- Does not run on a timer.

It is a plain markdown reference. Open it when you want; ignore it otherwise.
