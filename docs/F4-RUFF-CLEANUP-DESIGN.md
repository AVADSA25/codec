# F-4 — Ruff lint baseline + CI gate

**Closes:** Investor-readiness audit **F-4** (no lint in CI; ~640 ruff findings).
**Repo:** codec-repo. **Greenlit by Mickael 2026-05-25** (overrides the standing
"don't touch working code" guard for this specific, verified pass).

## Approach — pragmatic, not scorched-earth
Of the ~640 default ruff findings, the large majority are **CODEC house style**, not bugs:
lazy/conditional imports with `sys.path` setup (E402 ×176), compact one-line conditionals
(E701 ×114 / E702 ×60), short loop vars (E741 ×28). Rewriting 600+ lines of working, tested
code to satisfy stylistic rules is pure risk for no behavioral gain. So:

1. **`ruff.toml`** ignores those stylistic rules and keeps ruff focused on the meaningful
   **pyflakes (F\*)** correctness checks — unused imports, undefined names, f-string bugs,
   redefinitions. `pilot/` (separate repo) and `swift-overlay/` (not Python) are excluded.
2. **Safe auto-fixes applied** (`ruff check --fix`, no `--unsafe-fixes`): 229 behaviour-preserving
   fixes — unused-import removal (F401), import-statement splitting (E401), f-strings with no
   placeholders (F541). These cannot change runtime behaviour by construction.
3. **Real bug fixed:** `codec_heartbeat.py` called `subprocess.run(...)` in the alert path with
   **no `subprocess` import anywhere** — a latent `NameError` whenever an alert fired. ruff's
   F821 surfaced it; added the import. Also added `from typing import List` to
   `test_agent_runner.py` (7× F821 — harmless under `from __future__ import annotations`, but
   now correct).
4. **Benign stragglers** (12): availability-probe imports in `tests/test_smoke.py` +
   `scripts/feature_audit.py` (the import *is* the check) and a handful of unused locals in
   working skills are **per-file-ignored** in `ruff.toml` rather than risk-edited.
5. **CI gate:** `ruff check .` added to `.github/workflows/ci.yml` (installs `ruff`). The
   baseline is clean, so the gate blocks future lint regressions.

## Manifest
The auto-fix touched ~40 `skills/*.py` (import cleanup). Skills are hash-pinned by
`skills/.manifest.json` (PR-1A D-1 gate), so the manifest was regenerated
(`tools/generate_skill_manifest.py --write`, 76 skills) — the CI `--check` stays green.

## Verification
- `ruff check .` → **clean** (All checks passed).
- `pytest --collect-only` → the only 4 collection errors are **pre-existing** (missing
  `pynput`/`fastmcp` dev-deps + a pre-existing `test_smoke` regex check), confirmed identical on
  the pre-change tree via `git stash`. Zero new import breakage from the cleanup.
- Full suite re-run post-change matches the documented baseline (auto-fixes are
  behaviour-preserving; collection is unchanged).

## Rollback
Delete `ruff.toml` + the CI step and `git revert` the fix commit. The auto-fixes are all
behaviour-preserving, so reverting is purely cosmetic.

## Not done (separate / deferred)
- Running the **full** pytest suite in CI still needs the optional-dep matrix sorted on the
  runner (`pynput`/`fastmcp` etc.) — tracked under F-4's CI-depth half; this PR delivers the
  lint gate, not the full-suite gate.
- `mypy` type-checking — out of scope here.
