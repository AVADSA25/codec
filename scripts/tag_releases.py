#!/usr/bin/env python3
"""Retroactively tag documented CHANGELOG releases (F-5).

DRY-RUN BY DEFAULT. Parses CHANGELOG.md, maps each documented version to the last commit
on/before that version's date, and prints the annotated tags it *would* create. Review the
mapping, then opt in to writing:

    python3 scripts/tag_releases.py                    # dry run (default) — writes nothing
    python3 scripts/tag_releases.py --execute          # create annotated tags locally
    python3 scripts/tag_releases.py --execute --push   # also push tags to origin

Stdlib only. Creates/pushes nothing unless --execute (and --push) are given.

NOTE on v3.0.0: the repo already carries a `v3.0.0` tag that is ahead of the documented
history (latest CHANGELOG entry is v2.3.0). This script never deletes tags — see
docs/VERSIONING.md for the reconciliation recommendation.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_VERSION_HEADING = re.compile(
    r"^##+\s*\[?v?(\d+\.\d+\.\d+)\]?\s*\((\d{4}-\d{2}-\d{2})\)", re.MULTILINE
)


def parse_changelog_versions(text: str) -> list[tuple[str, str]]:
    """Return ``[(version, date), ...]`` in CHANGELOG order (newest first)."""
    return [(m.group(1), m.group(2)) for m in _VERSION_HEADING.finditer(text)]


def _git(*args: str) -> str:
    out = subprocess.run(["git", "-C", str(_REPO), *args],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _commit_for_date(date: str) -> str | None:
    """Last commit on/before 23:59:59 of ``date`` (best-effort mapping)."""
    try:
        return _git("rev-list", "-1", f"--before={date} 23:59:59", "HEAD") or None
    except Exception:
        return None


def _existing_tags() -> set[str]:
    try:
        return set(_git("tag", "--list").split())
    except Exception:
        return set()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Retroactively tag CHANGELOG releases (F-5).")
    ap.add_argument("--execute", action="store_true", help="actually create the annotated tags")
    ap.add_argument("--push", action="store_true", help="push created tags to origin")
    ap.add_argument("--changelog", default=str(_REPO / "CHANGELOG.md"))
    args = ap.parse_args(argv)

    versions = parse_changelog_versions(Path(args.changelog).read_text(encoding="utf-8"))
    if not versions:
        print("No version headings found in CHANGELOG.", file=sys.stderr)
        return 1

    existing = _existing_tags()
    print(f"{'(DRY RUN) ' if not args.execute else ''}Planned tags from {args.changelog}:\n")
    planned: list[tuple[str, str, str]] = []
    for version, date in versions:
        tag = f"v{version}"
        if tag in existing:
            print(f"  skip  {tag:<10} (already exists)")
            continue
        commit = _commit_for_date(date)
        if not commit:
            print(f"  WARN  {tag:<10} ({date}) — no commit on/before this date; skipping")
            continue
        print(f"  tag   {tag:<10} -> {commit[:12]}  ({date})")
        planned.append((tag, commit, date))

    if not args.execute:
        print("\nDry run only. Re-run with --execute to create these tags"
              " (add --push to push to origin).")
        return 0

    for tag, commit, date in planned:
        _git("tag", "-a", tag, commit, "-m", f"Release {tag} ({date})")
        print(f"created {tag}")
    if args.push and planned:
        _git("push", "origin", *[t for t, _c, _d in planned])
        print("pushed tags to origin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
