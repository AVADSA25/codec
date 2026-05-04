"""CODEC Audit Analyzer — nightly replay of audit.log.

Reads yesterday's ~/.codec/audit.log.YYYY-MM-DD (or today's live log if no
rotation yet), computes per-tool stats, and writes a markdown report to
~/.codec/reports/YYYY-MM-DD.md.

Insights surfaced:
  - Total calls / error rate / p50/p95 latency overall
  - Top 10 most-used tools
  - Tools with highest error rate (>= 3 errors AND >= 20% failure)
  - Slowest tools (p95 latency)
  - Never-used tools (silent candidates for deprecation)
  - Timeout incidents
  - Unique caller IPs (from OAuth client_id when present)

Run on demand:  python3 codec_audit_analyzer.py [YYYY-MM-DD]
Auto-scheduled: add to autopilot.json at a nightly time.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

_CODEC = Path(os.path.expanduser("~/.codec"))
_REPORTS = _CODEC / "reports"
_REPORTS.mkdir(parents=True, exist_ok=True)


def _find_log(date_str: str | None) -> Path | None:
    if date_str is None:
        # Default: yesterday's rotated log if exists, else today's live log
        y = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        rotated = _CODEC / f"audit.log.{y}"
        if rotated.exists():
            return rotated
        return _CODEC / "audit.log"
    rotated = _CODEC / f"audit.log.{date_str}"
    if rotated.exists():
        return rotated
    return None


def _load_lines(path: Path):
    if not path.exists():
        return []
    records = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    return records


def _pct(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    k = int(round((p / 100) * (len(values) - 1)))
    return values[k]


def analyze(records: list[dict], known_tools: set[str] | None = None) -> dict:
    total = len(records)
    errors = sum(1 for r in records if r.get("outcome") == "error")
    timeouts = sum(1 for r in records if r.get("outcome") == "timeout")
    validations = sum(1 for r in records if r.get("outcome") == "validation")

    latencies = [r["duration_ms"] for r in records if isinstance(r.get("duration_ms"), (int, float))]

    by_tool: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_tool[r.get("tool", "unknown")].append(r)

    usage = Counter({t: len(rs) for t, rs in by_tool.items()})

    err_rate: dict[str, tuple[int, int, float]] = {}
    p95_by_tool: dict[str, float] = {}
    for tool, rs in by_tool.items():
        n = len(rs)
        e = sum(1 for r in rs if r.get("outcome") in ("error", "timeout"))
        err_rate[tool] = (e, n, e / n if n else 0.0)
        p95_by_tool[tool] = _pct([r["duration_ms"] for r in rs if isinstance(r.get("duration_ms"), (int, float))], 95)

    unused = []
    if known_tools:
        used = set(by_tool.keys())
        unused = sorted(known_tools - used)

    clients = Counter(r.get("client_id") for r in records if r.get("client_id"))

    return {
        "total": total,
        "errors": errors,
        "timeouts": timeouts,
        "validations": validations,
        "p50_ms": _pct(latencies, 50),
        "p95_ms": _pct(latencies, 95),
        "avg_ms": sum(latencies) / len(latencies) if latencies else 0,
        "top_used": usage.most_common(10),
        "high_error_tools": sorted(
            [(t, e, n, r) for t, (e, n, r) in err_rate.items() if e >= 3 and r >= 0.2],
            key=lambda x: x[3], reverse=True,
        )[:10],
        "slowest_tools": sorted(p95_by_tool.items(), key=lambda x: x[1], reverse=True)[:10],
        "unused_tools": unused,
        "timeout_incidents": [
            {"ts": r.get("ts"), "tool": r.get("tool")}
            for r in records if r.get("outcome") == "timeout"
        ][:20],
        "unique_clients": len(clients),
        "top_clients": clients.most_common(5),
    }


def _render(date_str: str, summary: dict) -> str:
    def pct(x, tot):
        return f"{(x/tot*100):.1f}%" if tot else "0%"

    lines = [
        f"# CODEC Audit Report — {date_str}",
        "",
        "## Summary",
        f"- **Total calls:** {summary['total']}",
        f"- **Errors:** {summary['errors']} ({pct(summary['errors'], summary['total'])})",
        f"- **Timeouts:** {summary['timeouts']}",
        f"- **Validation rejects:** {summary['validations']}",
        f"- **Latency:** p50 {summary['p50_ms']:.0f}ms · p95 {summary['p95_ms']:.0f}ms · avg {summary['avg_ms']:.0f}ms",
        f"- **Unique clients:** {summary['unique_clients']}",
        "",
        "## Top 10 most-used tools",
    ]
    for tool, n in summary["top_used"]:
        lines.append(f"- `{tool}` — {n}")

    if summary["high_error_tools"]:
        lines += ["", "## ⚠ High-error tools (≥3 errors, ≥20% failure)"]
        for tool, e, n, r in summary["high_error_tools"]:
            lines.append(f"- `{tool}` — {e}/{n} ({r*100:.0f}%)")

    if summary["slowest_tools"]:
        lines += ["", "## Slowest tools (p95)"]
        for tool, p95 in summary["slowest_tools"][:5]:
            lines.append(f"- `{tool}` — p95 {p95:.0f}ms")

    if summary["timeout_incidents"]:
        lines += ["", "## Timeouts"]
        for inc in summary["timeout_incidents"][:10]:
            lines.append(f"- {inc['ts']} → `{inc['tool']}`")

    if summary["unused_tools"]:
        lines += ["", "## Unused tools (candidates for deprecation review)",
                  ", ".join(f"`{t}`" for t in summary["unused_tools"][:40])]

    lines += ["", "## Recommendations"]
    recs = []
    if summary["high_error_tools"]:
        recs.append("Investigate high-error tools — check skill `run()` exception handling.")
    if summary["p95_ms"] > 5000:
        recs.append(f"p95 latency is {summary['p95_ms']:.0f}ms — consider optimizing slowest tools.")
    if summary["timeouts"] > 0:
        recs.append("Tools hit 30s timeout — likely external service hanging. Add retry/backoff.")
    if not recs:
        recs.append("All systems nominal — no issues detected.")
    for r in recs:
        lines.append(f"- {r}")

    return "\n".join(lines) + "\n"


def run(task: str = "", context: str = "") -> str:
    """CODEC skill interface — `run audit report` or `run audit report 2026-04-14`."""
    date_str = None
    for tok in (task or "").split():
        if len(tok) == 10 and tok[4] == "-" and tok[7] == "-":
            date_str = tok
            break

    log_path = _find_log(date_str)
    if log_path is None or not log_path.exists():
        return f"No audit log found for {date_str or 'yesterday'}."

    records = _load_lines(log_path)
    if not records:
        return f"No records in {log_path.name}."

    # Derive "known tools" from skills/ dir for unused-tool detection
    known = set()
    try:
        skills_dir = Path(__file__).resolve().parent / "skills"
        for f in skills_dir.glob("*.py"):
            if not f.name.startswith("_") and f.name != "codec.py":
                known.add(f.stem)
    except Exception:
        pass

    summary = analyze(records, known_tools=known)
    report_date = date_str or datetime.now(timezone.utc).date().isoformat()
    md = _render(report_date, summary)

    out = _REPORTS / f"{report_date}.md"
    out.write_text(md)
    return f"Report written to {out}\n\n{md}"


# Skill metadata so autopilot can invoke this and the CI contract test passes
SKILL_NAME = "audit_report"
SKILL_DESCRIPTION = "Generate a nightly CODEC audit report: call volume, errors, p95 latency, high-error tools, timeouts, unused tools."
SKILL_MCP_EXPOSE = True


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    print(run(arg))
