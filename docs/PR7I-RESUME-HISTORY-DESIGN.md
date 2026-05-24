# PR-7I — Resume-after-crash keeps in-checkpoint history + at-most-once destructive replay (Audit B / B-5)

**Status:** design → TDD → ship
**Closes:** Audit-B finding **B-5** (`docs/audits/PHASE-1-PROJECTS-PILOT.md`) — the last Audit-B HIGH.
**Branch:** `fix/pr7i-resume-history`
**Touches:** `codec_agent_runner.py` (don't-touch zone per AGENTS.md §10 → design-first required).

## What

When a running agent crashes mid-checkpoint (PM2 restart, OOM, power loss), the daemon
resumes it — but today it replays the **entire** checkpoint from step 0 with an empty
history. Two fixes:

1. **Persist the running history incrementally** and reload it on resume, so the model
   picks up where it left off instead of redoing completed work.
2. **At-most-once guard for destructive actions** so an irreversible op (payment, send,
   delete) that already fired before the crash is **not re-executed** on resume.

## Why (verified bug)

- `_run_agent` rebuilds `history = []` on every invocation, including the resume path
  (`runner:954`).
- `_execute_checkpoint` accumulates `history` **in memory**; `save_state` persists only
  `current_checkpoint` (advanced at checkpoint *completion*, `runner:1074`) — the history
  *content* is never written, only its length (`history_len`).
- The daemon's crash-resume (`_daemon_one_tick`, `runner:1243-1265`) re-spawns
  `_run_agent`; `current_idx` points at the **in-progress** checkpoint (it wasn't
  completed), so that checkpoint re-runs from step 0.

**Impact:** the documented "worst case: one op re-fires" is wrong. A 40-step checkpoint
that crashed at step 39 re-runs all 40. Non-idempotent skills duplicate (re-download,
re-append, **duplicate send/charge**); destructive ops re-hit the consent path. For an
agent that runs autonomously on the user's machine and can spend money / send messages,
silent duplication on every restart is a real safety gap.

## Design

### State.json additive keys (scoped to the in-progress checkpoint; cleared on completion)

| key | type | meaning |
|---|---|---|
| `cp_in_progress` | str | checkpoint id the persisted `cp_history` belongs to (guards against seeding a different checkpoint) |
| `cp_history` | list[dict] | the running history accumulated so far (full cross-checkpoint context, same list the loop threads) |
| `executed_destructive` | list[str] | 16-hex fingerprints of destructive actions already **attempted** (marker written BEFORE execution) |

All three are **additive** — a legacy `state.json` without them resumes with empty
history (today's behavior; safe). They are **cleared at each checkpoint completion**
(progress is per-checkpoint), while `current_checkpoint` / `last_reply_ts` /
`step_budget_overrides` are **preserved** (the completion save becomes a
load-modify-save instead of today's full overwrite, which silently dropped
`last_reply_ts` and `step_budget_overrides`).

### Part 1 — incremental persistence + reload

- New helper `_persist_checkpoint_progress(agent_id, checkpoint_id, history, executed_destructive)`:
  `load_state` → set the three keys → `save_state`. Load-modify-save so concurrent
  `last_reply_ts` / `step_budget_overrides` survive.
- `_execute_checkpoint` persists **after every `history.append`** (nudge, skip, result)
  and **after recording a destructive marker** (see Part 2). All writes happen in the
  agent's own thread, sequentially — no intra-process race.
- `_run_agent` seeds `history` (and the `executed_destructive` ledger) from `state` when
  it (re)enters the in-progress checkpoint:
  ```python
  if idx == current_idx and state.get("cp_in_progress") == cp.id:
      history = list(state.get("cp_history", []))
      cp_executed = list(state.get("executed_destructive", []))
  else:
      cp_executed = []
  ```
  Threaded into `_execute_checkpoint(..., executed_destructive=cp_executed)`.

### Part 2 — at-most-once destructive guard

- Fingerprint: `_fingerprint(cp_id, skill, task) = sha256(f"{cp_id}|{skill}|{task}").hexdigest()[:16]`.
- In `_execute_checkpoint`, for an action where `_effective_destructive(action)` is true
  (the **same** server-derived predicate B-2/PR-7G uses for the consent gate — so
  irreversible *network sends* are covered, not just LLM-flagged ops):
  1. If `fp in executed_destructive` → it already fired (or may have) in a prior life.
     **Skip** `_run_skill`, append a synthetic `[SKIPPED ON RESUME …]` history entry,
     persist, `continue`. (Skip happens **before** the consent gate — no re-prompt for
     an op we're not running.)
  2. Else → run the consent gate; on approval, append `fp` to `executed_destructive`
     and **persist the marker BEFORE** calling `_run_skill`. If the crash lands between
     marker-persist and the skill returning, resume sees the marker → skips → the op
     fires **at most once**.
- **Non-destructive** actions get no marker — they're assumed retry-safe, and the
  history reload already prevents most redundant work. Idempotent network GETs
  (re-fetch weather) are intentionally allowed to retry; irreversible network POSTs are
  flagged destructive by `_server_destructive_signal` (B-2) and thus covered above.

**At-most-once, not exactly-once.** Exactly-once is impossible across an uncoordinated
crash. We deliberately choose at-most-once for destructive ops: skipping a possibly-
completed irreversible action is safer than double-charging / double-sending. The
synthetic skip entry tells the model the action is done so it advances.

### Budget on resume

`_execute_checkpoint` re-enters with seeded history (N entries) but `for step in
range(budget)` counts fresh. Resume therefore grants a fresh budget for the *remaining*
work (plus any `/extend_budget` override). Intentional — a crash shouldn't burn the
agent's step budget on work it must partly redo.

## Schema / API changes

- `_execute_checkpoint(...)` gains a trailing optional `executed_destructive: Optional[List[str]] = None`
  (defaults to `[]`). Backward compatible — existing callers/tests unaffected.
- New module helpers `_fingerprint(...)` and `_persist_checkpoint_progress(...)`.
- No change to `codec_agent_plan` `save_state`/`load_state` (still whole-file atomic R/W).
- No new audit events; no manifest schema change.

## Migration

None. New `state.json` keys are additive; absent → empty-history resume (current
behavior). The completion save switching from overwrite to load-modify-save is strictly
more-preserving.

## Test plan (TDD — `tests/test_resume_history.py`)

1. `test_execute_checkpoint_persists_history_each_step` — `_run_skill` spy reads
   `state.json` at call time; by the 2nd step the 1st step's history is already persisted
   (`cp_history` non-empty mid-checkpoint, not just at completion).
2. `test_resume_seeds_history_from_persisted_state` — pre-seed `cp_in_progress`/`cp_history`;
   capture the `history` passed to `_qwen_next_action`; assert the prior entry is present.
3. `test_destructive_action_skipped_if_already_executed_on_resume` — pre-seed
   `executed_destructive=[fp]`; the destructive action is **not** re-run (`_run_skill`
   not called, consent gate not called).
4. `test_destructive_marker_persisted_before_execution` — `_run_skill` spy asserts the
   fingerprint is already in `executed_destructive` when it runs (marker precedes exec).
5. `test_checkpoint_completion_clears_progress_preserves_cursor` — after completion,
   `cp_history`/`cp_in_progress`/`executed_destructive` gone, `current_checkpoint`
   advanced, `last_reply_ts` retained (today's overwrite dropped it).

Full suite: zero new failures vs the 41-failed baseline. Ruff: zero delta vs origin/main.
Agent suites (`test_agent_plan` + `test_agent_runner` + `test_destructive_recovery` +
`test_atomic_approval` + `test_status_cas` + `test_user_replies` + new) stay green.

## Rollback

Revert the single commit. The `executed_destructive` param is additive and unused by
other callers; the new state keys are ignored by older code. Reverting restores the
replay-from-zero behavior but breaks nothing structurally.
