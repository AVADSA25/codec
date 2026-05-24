# PR-7H — Atomic approval write + pre-approval abort (Audit B / B-9)

**Status:** design → TDD → ship
**Closes:** Audit-B finding **B-9** (`docs/audits/PHASE-1-PROJECTS-PILOT.md`)
**Branch:** `fix/pr7h-atomic-approval`

## What

Two coupled correctness fixes in `codec_agent_plan.py`:

1. **Approval is a single atomic manifest write.** `approve_plan` currently writes
   the manifest **twice**: once to stamp `plan_hash` / `grants_hash` / `approved_at`
   (status still `awaiting_approval`), then again via `set_status(agent_id, "approved")`.
   The two writes are not a single CAS, so the *status* and the *hashes it depends on*
   are not guaranteed to land together. We collapse them into one flock-protected write
   by teaching `set_status` to accept an `extra=` dict of fields to merge alongside the
   status transition.

2. **A pre-approval agent can be aborted.** `_VALID_TRANSITIONS` has no `aborted`
   edge out of `draft_pending`, `awaiting_approval`, or `revised`. So an agent that is
   stuck mid-draft, or sitting in the approval queue, can only be `rejected` (a
   *decision about the plan*) — there is no way to *abort the agent* before it ever runs.
   We add `aborted` to all three pre-approval states.

## Why (the invariant B-9 protects)

The Step-9 runner verifies `manifest.plan_hash == sha256(plan.json)` **and**
`manifest.grants_hash == sha256(grants.json)` at run-start. If a crash interleaves the
two approval writes such that `status=approved` is persisted but a hash is missing, the
agent is **bricked**: run-start tamper detection sees an absent hash and the agent can
never start (or, worse under a future refactor that flips write order, starts with an
unverified manifest).

Invariant: **`status == "approved"` MUST coexist with both `plan_hash` and
`grants_hash` — always, with no crash window between them.** A single atomic write under
the existing `_status_lock` flock is the structural guarantee. Today's "hashes first,
status second" ordering happens to be crash-safe, but that safety is incidental to
statement order; a one-line reorder in a future PR silently reintroduces the brick.
B-9 makes the invariant load-bearing instead of accidental.

The abort fix is the recovery affordance for the *previously-illegal* path: B-8 (PR-7G)
gave running/blocked agents a recovery route; B-9 extends abort to the pre-run states so
a draft that the LLM mangled, or an approval the user no longer wants, can be cleanly
terminated instead of orphaned.

## Schema / API changes

- **`set_status(agent_id, new_status, reason=None, extra=None)`** — new optional
  trailing `extra: Optional[Dict[str, Any]]`. When provided, its keys are merged into
  the manifest **inside the same `_status_lock` block** as the status transition, before
  the single `save_manifest`. Backward compatible: every existing positional/keyword
  call is unaffected (`extra` defaults to `None`).
- **`_VALID_TRANSITIONS`** — additive only (never remove an edge, per the don't-touch
  note in AGENTS.md §10):
  - `draft_pending`: `+aborted`
  - `awaiting_approval`: `+aborted`
  - `revised`: `+aborted`
- **`approve_plan`** — internal refactor only; no signature change. The two manifest
  writes become one `set_status(agent_id, "approved", extra={plan_hash, grants_hash, approved_at})`.

No on-disk schema version bump: manifest fields are unchanged; only the *atomicity* of
how they're written and the *set* of legal transitions change.

## Migration

None. Existing manifests already carry `plan_hash`/`grants_hash` (PR-7E heal-forward
covers any legacy ones that don't, at run-start). The new `aborted` edges only *add*
reachable states; no existing agent is in an invalid state after this change.

## Test plan (TDD — `tests/test_atomic_approval.py`)

- `test_set_status_writes_extra_fields_atomically` — `set_status(..., extra={plan_hash, grants_hash})`
  → reloaded manifest has status **and** both hashes (single write).
- `test_awaiting_approval_can_be_aborted` — `awaiting_approval → aborted` no longer raises.
- `test_draft_pending_can_be_aborted` — `draft_pending → aborted` no longer raises.
- `test_approve_plan_leaves_consistent_state` — after `approve_plan`, manifest is
  `approved` **and** carries both hashes (the B-9 invariant, end-to-end).

Full suite: zero new failures vs the 41-failed baseline. Ruff: zero delta vs origin/main.

## Rollback

Revert the single commit. `set_status`'s `extra` param is additive and unused by other
callers; removing it and restoring the two-write `approve_plan` body returns to prior
behavior. The `_VALID_TRANSITIONS` edges are additive — reverting removes the abort
affordance but breaks nothing that depended on it (nothing did before this PR).
