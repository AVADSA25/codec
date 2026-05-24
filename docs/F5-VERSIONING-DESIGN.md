# F-5 — Versioning discipline (single source of truth + release tagging)

**Closes:** Investor-readiness audit **F-5** (no release tagging; version drift between the
engine string, CHANGELOG, and the only git tag `v3.0.0`). **Repo:** codec-repo.

## Problem
- CHANGELOG documents 10 releases (v1.0.0 … **v2.3.0**, latest 2026-05-13); **none are git-tagged**.
- The only versioned tag is `v3.0.0` — an outlier ahead of the documented history.
- `codec_dashboard.py` declares the FastAPI app `version="2.1.0"` (stale OpenAPI metadata).
- No introspectable "what version am I running" answer (no `pyproject`, no `__version__`).

## Decision (judgment call, not blocking)
The **CHANGELOG is the source of truth**; its latest entry is the current version. That is
**`2.3.0`** — it matches the README/engine badge. The `v3.0.0` tag is treated as an outlier:
this change does **not** delete it (destructive, remote-affecting → operator's call). The
recommendation is documented in `docs/VERSIONING.md`; the operator decides keep-as-future-major
vs. delete-as-erroneous.

## What ships (all additive — no runtime code touched)
1. **`VERSION`** (repo root) — `2.3.0`. The canonical single source of truth.
2. **`codec_version.py`** — `__version__` read from `VERSION` at import (stdlib only, never
   raises; falls back to a module constant if the file is missing). Gives runtime
   introspectability without touching any don't-touch module.
3. **`scripts/tag_releases.py`** — **dry-run by default**. Parses CHANGELOG → `(version, date)`,
   maps each to the last commit on/before its date, and prints the annotated tags it *would*
   create. `--execute` creates them locally; `--push` pushes. Stdlib only. The operator reviews
   the dry-run mapping before anything is written — Claude does not create/push tags.
4. **`docs/VERSIONING.md`** — the scheme (SemVer), the source-of-truth chain
   (`VERSION` ← CHANGELOG), and the `v3.0.0` reconciliation note.

## Why not change `codec_dashboard.py:version="2.1.0"`
That's the FastAPI app's OpenAPI version — a working-runtime string in a high-traffic module.
Out of scope here (don't touch working code for a cosmetic metadata field); flagged in
`docs/VERSIONING.md` as an optional follow-up.

## Tests (`tests/test_versioning.py`)
- `VERSION` exists + is valid SemVer.
- `codec_version.__version__` equals `VERSION` contents.
- `VERSION` equals the CHANGELOG's latest documented version.
- `scripts/tag_releases.parse_changelog_versions` returns all 10 CHANGELOG versions, newest
  first, with `2.3.0` at the head.

Stdlib-only tests → green on the CI ubuntu runner (additive to the F-4 doc-guard gate).

## Rollback
Delete the 4 new files. Nothing imports them at runtime; zero behavioral impact.
