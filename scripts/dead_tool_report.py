"""Report tools unused for N days based on audit.log history.

Reads ~/.codec/audit.log and all rotated audit.log.YYYY-MM-DD files,
counts per-tool usage, and flags any skill in skills/ that has ZERO
invocations in the window.

Usage:
    python3 scripts/dead_tool_report.py           # default 30-day window
    python3 scripts/dead_tool_report.py --days 7
    python3 scripts/dead_tool_report.py --skip "memory_*,google_*"
"""
import argparse
import fnmatch
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "skills"
AUDIT = Path.home() / ".codec"


def _iter_audit_files(days: int):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    live = AUDIT / "audit.log"
    if live.exists():
        yield live
    for p in AUDIT.glob("audit.log.*"):
        try:
            date_str = p.name.replace("audit.log.", "")
            d = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
            if d >= cutoff:
                yield p
        except Exception:
            continue


def _usage(days: int) -> Counter:
    c = Counter()
    for p in _iter_audit_files(days):
        for line in p.read_text(errors="replace").splitlines():
            try:
                r = json.loads(line)
                t = r.get("tool")
                if t and t != "unknown":
                    c[t] += 1
            except Exception:
                continue
    return c


def _existing_skills() -> list[str]:
    return sorted(
        f.stem for f in SKILLS.glob("*.py")
        if not f.name.startswith("_") and f.name != "codec.py"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--skip", default="", help="comma-separated glob patterns to exclude")
    args = ap.parse_args()

    skip_patterns = [p.strip() for p in args.skip.split(",") if p.strip()]
    usage = _usage(args.days)
    skills = _existing_skills()

    dead = []
    for s in skills:
        if any(fnmatch.fnmatch(s, pat) for pat in skip_patterns):
            continue
        if usage.get(s, 0) == 0:
            dead.append(s)

    print(f"Window: last {args.days} days")
    print(f"Total unique tools called: {len(usage)}")
    print(f"Total skills on disk:      {len(skills)}")
    print(f"Dead (zero invocations):   {len(dead)}\n")
    if dead:
        print("Candidates for deprecation review:")
        for s in dead:
            print(f"  - {s}")
    else:
        print("All skills have been used at least once. Nothing to prune.")


if __name__ == "__main__":
    main()
