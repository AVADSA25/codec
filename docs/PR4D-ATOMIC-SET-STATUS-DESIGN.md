# PR-4D — `_atomic_set_status` returns bool; guard run-start + gate in-loop emits (C-5) (DESIGN)

**Status:** PROPOSED. Make `codec_agent_runner._atomic_set_status` return `bool` (True = transition applied, False = illegal / externally-superseded / write failed) instead of silently swallowing every exception and returning `None`. Guard the run-start `running` transition (never execute checkpoints on a superseded agent) and gate the in-loop block/abort/complete/qwen audit + notification emits so they only fire when the transition actually applied. Touches `codec_agent_runner.py` only. New `tests/test_atomic_set_status.py`.

**Finding:** C-5 (`_atomic_set_status` swallows `InvalidStatusTransition` → agent state machine can desync) [CRITICAL].

---

## 1. The defect (verified against current code)

```python
def _atomic_set_status(agent_id, new_status, reason=None) -> None:
    try:
        from codec_agent_plan import set_status
        set_status(agent_id, new_status, reason=reason)
    except Exception as e:                       # ← swallows EVERYTHING
        log.warning("[%s] set_status %s failed: %s", agent_id, new_status, e)
```

`set_status` raises `InvalidStatusTransition` (a `ValueError` subclass) when `new_status` isn't reachable from the manifest's current status per `codec_agent_plan._VALID_TRANSITIONS`. The wrapper catches it (and any I/O error from `save_manifest`), logs, and returns `None`. All **14** call sites in `codec_agent_runner.py` ignore the (non-existent) return and proceed as if the transition succeeded.

### Two concrete harms
1. **Executes a superseded agent (dangerous).** Run-start (`codec_agent_runner.py:820`) does `_atomic_set_status(agent_id, "running")`, then immediately walks `plan.checkpoints`. If the user aborted the agent between approval and the runner thread reaching line 820, the status is `aborted` (terminal) → `aborted → running` is illegal → swallowed → **the runner executes checkpoints on an aborted agent.**
2. **Audit/notification desync (observability + UX).** The pause-race the finding cites: agent `running`; user clicks Pause (`POST /api/agents/{id}/pause` → status `paused`); the running thread hits a `PermissionViolation` and calls `_atomic_set_status(agent_id, "blocked_on_permission")`. `paused → blocked_on_permission` is illegal → swallowed (manifest stays `paused`), **but the code still emits `AGENT_BLOCKED_ON_PERMISSION` and posts a "Blocked: grant permission" notification.** PWA shows `paused`; user gets a "Blocked" card. Clicking Resume takes the paused path, not the blocked-resolution path.

## 2. Why options (a) and (c) are rejected

The audit lists three fixes. Verified against the actual control flow:

- **(c) "Don't suppress — let the outer `except Exception` (line 994) abort cleanly."** *Rejected — it converts a user pause into an abort.* In the pause-race, the unsuppressed `paused → blocked_on_permission` raises, propagates past line 864 to the outer handler (994), which runs `_atomic_set_status(agent_id, "aborted")`. `paused → aborted` **is** allowed → the agent **aborts**. The user clicked Pause and the agent dies. (c) breaks the exact race the finding describes.
- **(a) "Catch only `InvalidStatusTransition`, re-raise the rest."** *Rejected — incomplete.* It surfaces I/O errors but still swallows the illegal transition, so harm #1 (execute-superseded) and harm #2 (misleading emit) both remain.
- **(b) "Return a bool; check at call sites."** *Chosen.* The caller learns the transition didn't apply and reacts: stop cleanly, let the external state win, and skip the misleading emit. This is the only option that fixes both harms **and** lets an external pause/abort win the race.

## 3. The fix (option b)

### 3a. `_atomic_set_status -> bool`
```python
def _atomic_set_status(agent_id, new_status, reason=None) -> bool:
    """Apply a status transition. Returns True if it was applied, False if it
    was NOT (illegal/externally-superseded transition, or a write failure).
    Never raises — callers branch on the bool to avoid acting on / announcing a
    transition that didn't happen (C-5)."""
    try:
        from codec_agent_plan import set_status, InvalidStatusTransition
    except Exception as e:
        log.error("[%s] codec_agent_plan import failed: %s", agent_id, e)
        return False
    try:
        set_status(agent_id, new_status, reason=reason)
        return True
    except InvalidStatusTransition as e:
        # Usually a benign race: the status was changed under us by an external
        # actor (PWA pause/abort/grant). The external change WINS — do not force
        # our intended status over it.
        log.warning("[%s] transition → %s rejected (superseded?): %s",
                    agent_id, new_status, e)
        return False
    except Exception as e:
        log.error("[%s] set_status %s failed unexpectedly: %s",
                  agent_id, new_status, e)
        return False
```
Uniform contract: **False = "didn't apply, don't act on it."** (Both illegal-transition and write-failure return False; the distinction is in the log level — `warning` vs `error`. We do not re-raise: re-raising re-introduces option (c)'s abort-on-pause via the outer handler.)

### 3b. Run-start guard (line 820) — the dangerous site
```python
if not _atomic_set_status(agent_id, "running"):
    log.warning("[%s] run-start aborted: status not transitionable to running "
                "(superseded by external abort/pause?)", agent_id)
    return
# ... AGENT_STARTED audit + post_message only run when running was applied
```
A superseded agent never reaches `_execute_checkpoint`. The daemon reconciles on its next tick (a `paused` agent waits for resume; an `aborted` agent is terminal; only `running`-with-no-thread re-spawns).

### 3c. Gate the in-loop emits (block/abort/complete/qwen)
The 6 sites that announce a terminal-for-this-run state only emit when the transition applied; otherwise log "superseded" and fall through to the existing `return`/exit:
- `864` `blocked_on_permission`, `882` `aborted` (destructive_rejected), `897` `blocked_on_destructive`, `911` `paused` (step_budget), `963` `completed`, `987` `blocked_on_qwen`.

Pattern (e.g. 864):
```python
if _atomic_set_status(agent_id, "blocked_on_permission", reason=f"{pv.reason}:{pv.needed}"):
    _audit(AGENT_BLOCKED_ON_PERMISSION, ...)
    post_message(agent_id=agent_id, type="agent_blocked", ...)
else:
    log.info("[%s] block not announced — status superseded externally", agent_id)
return
```

### 3d. Left as-is (deliberate, out of scope per chosen "Full close")
- **Early aborts (783/799/807):** pre-run-start `approved → aborted` (plan_missing / hash_missing / tampered). `aborted` is the intended terminal outcome; emitting `AGENT_ABORTED` + returning is correct regardless of whether a concurrent abort beat us. They call the now-bool function and ignore the return (harmless).
- **Last-resort abort (996):** inside the outer `except Exception` — the final safety net. If the abort can't apply (already terminal), there's nothing else to do.
- **Daemon crash-recovery transitions (1092/1098/1125):** `running → crashed_resumed → running`. Backstopped by the run-start guard (3b): a respawned thread whose status isn't `running`-transitionable self-aborts at line 820. (Guarding these is the separate "Full + daemon guards" scope, not chosen.)

## 4. Test plan (`tests/test_atomic_set_status.py`)
Uses the `temp_codec_dir` fixture + `_setup_approved_agent` helper (mirrors `tests/test_agent_runner.py`):
- **Unit:** `_atomic_set_status` returns `True` on a valid transition (`approved → running`) and persists; returns `False` on an illegal one (`paused → blocked_on_permission`) and leaves the manifest unchanged; returns `False` (never raises) when `set_status` raises an unexpected error (monkeypatched `RuntimeError`).
- **Run-start guard:** set status `aborted` after setup → `_run_agent` → `_execute_checkpoint` is **never** called (`call_count == 0`) and status stays `aborted`. (Red on current code: it executes the checkpoint.)
- **In-loop desync:** `_execute_checkpoint` set to flip status to `paused` (simulating PWA pause) then raise `PermissionViolation` → `_run_agent` → status stays `paused` **and** `AGENT_BLOCKED_ON_PERMISSION` is **not** in the captured audit events. (Red on current code: the misleading event is emitted.)
- **Regression:** existing `_run_agent` tests (happy path / blocked / destructive / tamper / resume) stay green — the gating preserves their behavior because their transitions all apply. Full suite — exactly the 41 known-baseline failures, **zero new**. `ruff` per-file delta vs `origin/main` clean.

## 5. Risk + rollback
- **Blast radius:** `codec_agent_runner.py` only — one function signature (None→bool, backward-compatible: no external caller, internal callers that ignore the return are unaffected) + one guard + six gated emits. No state-machine change (`_VALID_TRANSITIONS` untouched), no schema change, no audit-envelope change.
- **Behavior preserved on the happy path:** when transitions apply (the overwhelming common case), every audit/notification fires exactly as before; only the *failed-transition* paths change (stop / don't-announce instead of proceed / mis-announce).
- **Don't-touch zone:** `codec_agent_runner.py` is AGENTS.md §10. This PR re-runs the design gate (this doc) per §11; `MAX_CONCURRENT`/`_active_threads`/`_VALID_TRANSITIONS` are untouched.
- **Rollback:** single-commit revert (restores the swallow-and-return-None wrapper). No persisted state migration.
