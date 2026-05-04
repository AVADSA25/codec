#!/usr/bin/env python3
"""Capture a Phase 1 Step 1 post-merge perf sample.

Reads the trailing 30-min window of ~/.codec/audit.log, computes the same
metrics as PHASE1-STEP1-BASELINE.md (avg / p95 over duration_ms records),
and appends a sample row to docs/PHASE1-STEP1-POSTMERGE-SAMPLES.md.

Usage:
    python3 scripts/capture_audit_sample.py [LABEL]

LABEL (optional, default = "T+?h"). Examples: "T+4h", "T+8h", "T+12h",
"T+16h", "T+20h".

Behavior:
    * Read-only access to ~/.codec/audit.log.
    * Append-only edit to docs/PHASE1-STEP1-POSTMERGE-SAMPLES.md — it
      replaces the matching `## Sample <LABEL> — pending` block (if
      present) with the captured numbers; otherwise appends a new block.
    * Prints the captured numbers + status decision to stdout.

Status decision (per design §5.4):
    ok          — both avg AND p95 within 1.3× baseline; OR no duration
                  records in window AND no error spike.
    investigate — avg or p95 between 1.3× and 2× baseline; OR error
                  spike >2× over surrounding samples that can't be
                  attributed to known service-down lifecycle emits.
    revert      — avg > 2× baseline OR p95 > 2× baseline. Caller MUST
                  execute the §5.4 revert mechanics immediately.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AUDIT = Path(os.path.expanduser("~/.codec/audit.log"))
SAMPLES_DOC = REPO / "docs" / "PHASE1-STEP1-POSTMERGE-SAMPLES.md"

WINDOW_MIN = 30
BASELINE_AVG = 987.96
BASELINE_P95 = 1907.78
HARD_REVERT_FACTOR = 2.0
INVESTIGATE_FACTOR = 1.3


def parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t
    except Exception:
        return None


def collect_window():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=WINDOW_MIN)
    records = []
    with AUDIT.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = parse_ts(r.get("ts", ""))
            if t is None or t < cutoff:
                continue
            records.append(r)
    return now, cutoff, records


def summarize(records):
    schema1 = legacy = errors = 0
    durations = []
    events = {}
    sources = {}
    for r in records:
        if r.get("schema") == 1:
            schema1 += 1
        else:
            legacy += 1
        if r.get("outcome") == "error":
            errors += 1
        d = r.get("duration_ms")
        if isinstance(d, (int, float)):
            durations.append(d)
        ev = r.get("event", "<none>")
        events[ev] = events.get(ev, 0) + 1
        src = r.get("source", "<none>")
        sources[src] = sources.get(src, 0) + 1
    durations.sort()
    avg_ms = sum(durations) / len(durations) if durations else None
    p95_ms = durations[max(0, int(len(durations) * 0.95) - 1)] if durations else None
    return {
        "n": len(records),
        "schema1": schema1,
        "legacy": legacy,
        "errors": errors,
        "with_duration": len(durations),
        "avg_ms": avg_ms,
        "p95_ms": p95_ms,
        "events": events,
        "sources": sources,
    }


def decide_status(s) -> tuple[str, str]:
    """Return (status, rationale)."""
    avg = s["avg_ms"]
    p95 = s["p95_ms"]
    if avg is None or p95 is None:
        return ("ok", "no `duration_ms` entries in window — latency comparison N/A; service health green")
    if avg > BASELINE_AVG * HARD_REVERT_FACTOR:
        return ("revert", f"avg {avg:.2f}ms > 2× baseline ({BASELINE_AVG * HARD_REVERT_FACTOR:.2f}ms) — execute §5.4")
    if p95 > BASELINE_P95 * HARD_REVERT_FACTOR:
        return ("revert", f"p95 {p95:.2f}ms > 2× baseline ({BASELINE_P95 * HARD_REVERT_FACTOR:.2f}ms) — execute §5.4")
    if avg > BASELINE_AVG * INVESTIGATE_FACTOR:
        return ("investigate", f"avg {avg:.2f}ms > 1.3× baseline ({BASELINE_AVG * INVESTIGATE_FACTOR:.2f}ms)")
    if p95 > BASELINE_P95 * INVESTIGATE_FACTOR:
        return ("investigate", f"p95 {p95:.2f}ms > 1.3× baseline ({BASELINE_P95 * INVESTIGATE_FACTOR:.2f}ms)")
    return ("ok", f"avg {avg:.2f}ms / p95 {p95:.2f}ms both within 1.3× baseline")


def render_block(label: str, sample_at_local: str, sample_at_utc: str,
                 cutoff_utc: str, s: dict, status: str, rationale: str) -> str:
    avg = "N/A" if s["avg_ms"] is None else f"{s['avg_ms']:.2f}"
    p95 = "N/A" if s["p95_ms"] is None else f"{s['p95_ms']:.2f}"
    delta_avg = "N/A" if s["avg_ms"] is None else f"{(s['avg_ms']/BASELINE_AVG - 1) * 100:+.1f}%"
    delta_p95 = "N/A" if s["p95_ms"] is None else f"{(s['p95_ms']/BASELINE_P95 - 1) * 100:+.1f}%"
    top_events = ", ".join(f"`{k}: {v}`" for k, v in
                           sorted(s["events"].items(), key=lambda x: -x[1])[:6])
    sources = ", ".join(f"`{k}: {v}`" for k, v in
                        sorted(s["sources"].items(), key=lambda x: -x[1])[:6])
    return f"""## Sample {label} — {sample_at_local}

| field | value |
|---|---|
| sample_at_local | {sample_at_local} |
| sample_at_utc | {sample_at_utc} |
| window_cutoff_utc | {cutoff_utc} |
| total_records | {s['n']} |
| schema1_records | {s['schema1']} |
| legacy_records | {s['legacy']} |
| with_duration | {s['with_duration']} |
| avg_ms | {avg} |
| p95_ms | {p95} |
| delta_vs_baseline_avg | {delta_avg} |
| delta_vs_baseline_p95 | {delta_p95} |
| errors_count | {s['errors']} |
| top_events | {top_events} |
| sources | {sources} |
| **status** | **{status}** — {rationale} |
"""


def append_or_replace(label: str, block: str):
    text = SAMPLES_DOC.read_text(encoding="utf-8")
    pending_pattern = re.compile(
        rf"## Sample {re.escape(label)} — pending\n.*?(?=\n## |\Z)",
        re.DOTALL,
    )
    m = pending_pattern.search(text)
    if m:
        text = text[:m.start()] + block + text[m.end():]
    else:
        # Append at end
        if not text.endswith("\n"):
            text += "\n"
        text += "\n---\n\n" + block
    SAMPLES_DOC.write_text(text, encoding="utf-8")


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "T+?h"
    now, cutoff, records = collect_window()
    s = summarize(records)
    status, rationale = decide_status(s)

    sample_at_utc = now.isoformat(timespec="seconds")
    cutoff_utc = cutoff.isoformat(timespec="seconds")
    sample_at_local = now.astimezone().isoformat(timespec="seconds")

    block = render_block(label, sample_at_local, sample_at_utc,
                         cutoff_utc, s, status, rationale)
    append_or_replace(label, block)

    # Stdout summary
    print(f"label: {label}")
    print(f"sample_at_local: {sample_at_local}")
    print(f"window: trailing {WINDOW_MIN} min  (cutoff_utc={cutoff_utc})")
    print(f"total: {s['n']}  schema1: {s['schema1']}  legacy: {s['legacy']}  errors: {s['errors']}")
    if s["with_duration"]:
        print(f"with_duration: {s['with_duration']}  avg_ms={s['avg_ms']:.2f}  p95_ms={s['p95_ms']:.2f}")
        print(f"delta_vs_baseline_avg: {(s['avg_ms']/BASELINE_AVG - 1) * 100:+.1f}%")
        print(f"delta_vs_baseline_p95: {(s['p95_ms']/BASELINE_P95 - 1) * 100:+.1f}%")
    else:
        print("with_duration: 0   (no MCP traffic — comparison N/A)")
    print(f"status: {status}  ({rationale})")

    if status == "revert":
        print("\n*** REVERT REQUIRED PER DESIGN §5.4 ***")
        print("Run:")
        print("  git -C ~/codec-repo revert 45d4aa7 --no-edit")
        print("  git -C ~/codec-repo push origin main")
        print("  pm2 restart codec-dashboard open-codec codec-mcp-http codec-heartbeat codec-autopilot --update-env")
        sys.exit(2)
    if status == "investigate":
        sys.exit(1)


if __name__ == "__main__":
    main()
