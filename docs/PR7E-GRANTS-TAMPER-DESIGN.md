# PR-7E — cover grants.json with the tamper hash (Audit B / B-4)

> Wave 7. Closes **B-4** (the plan-hash tamper check verifies `plan.json` but
> not `grants.json` — the file that actually gates execution). Reference:
> `docs/audits/PHASE-1-PROJECTS-PILOT.md` B-4.

## The hole

Run-start verifies `manifest.plan_hash == sha256(plan.json)`, but `grants.json`
(loaded to enforce permissions) is never hashed. So a post-approval edit to
`grants.json` — adding `write_paths`, `skills`, `network_domains` — survives
every restart and runs unchallenged.

## The fix

Mirror the plan-hash mechanism for grants, with one extra concern: `/grant`
*legitimately* mutates grants, so the hash must be re-synced on every legit write
or a legit grant would trip the check.

- `compute_grants_hash(agent_id)` — sha256 of canonical `grants.json` (mirrors
  `compute_plan_hash`).
- `set_grants_hash(agent_id)` — recompute + store `manifest.grants_hash`. The
  single sync point.
- **`approve_plan`** sets `manifest.grants_hash` alongside `plan_hash`.
- **`/api/agents/grant`** calls `set_grants_hash` after `save_grants` (legit
  change stays in sync).
- **`_run_agent`** verifies `grants_hash` after `plan_hash`:
  - present + mismatch → **abort** (`grants_tampered`),
  - **absent → heal-forward** (recompute + store + warn), NOT abort.

## Why heal-forward on absence (diverges from plan_hash's abort-on-absence)

`plan_hash` aborts on absence because approval *always* sets it, so absence ⇒
never-approved/tampered. `grants_hash` is a **new** field: agents approved
*before* this PR have no `grants_hash`. Aborting on absence would break every
in-flight legacy agent on upgrade. Heal-forward establishes protection from the
next run on without the breakage. The evade-by-deleting-the-hash vector requires
`manifest.json` write access — at which level an attacker could re-tamper any
hash anyway — so the hash's real protection (a process that can only touch
`grants.json`) holds. Trade-off documented.

## Scope

4 coordinated edits: 2 new helpers + `approve_plan` (`codec_agent_plan.py`),
`grant_permission` (`routes/agents.py`), the run-start verify (`codec_agent_runner.py`).
B-4 only — does not touch B-9.

## Test plan (`tests/test_grants_tamper.py`)

- `compute_grants_hash` deterministic + changes when grants change.
- `approve_plan` sets `manifest.grants_hash` == `compute_grants_hash`.
- `set_grants_hash` re-syncs after a `save_grants`.
- `_run_agent` with a **mismatched** stored `grants_hash` → `aborted` /
  `grants_tampered` (mirror the plan-tamper test harness).
- `_run_agent` with **absent** `grants_hash` → not aborted for grants (heals);
  the existing happy-path run tests (which set grants but no grants_hash) prove
  the no-breakage path.

## Rollback

`git revert` — additive helpers + one field at each of 3 call sites.
