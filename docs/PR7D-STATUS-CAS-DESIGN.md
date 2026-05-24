# PR-7D — cross-process flock on the status CAS (Audit B / B-7)

> Wave 7. Closes **B-7** (no single, cross-process-safe state-machine authority).
> Reference: `docs/audits/PHASE-1-PROJECTS-PILOT.md` B-7.

## The hole

`codec_agent_plan.set_status` does a read → validate-transition → write CAS:

```python
manifest = load_manifest(agent_id)
current = manifest.get("status", ...)
if new_status not in _VALID_TRANSITIONS.get(current, ...): raise
save_manifest(...)            # tmp+rename, atomic per-write
```

The `codec-agent-runner` daemon and the `codec-dashboard` PWA are **separate
processes** that both call this (the daemon's `_atomic_set_status` already wraps
`set_status`). With only per-write atomicity and **no cross-process lock**, a
daemon `running→blocked` and a concurrent PWA `running→paused` race on the read —
last-writer-wins silently drops a transition, and the `current ∈ _VALID_TRANSITIONS`
check is not atomic with the write, so the validity guarantee is illusory.

## The fix (bounded — one writer already exists)

`set_status` is already the single status writer (good — the daemon routes
through it). So the fix is just to make its CAS **cross-process atomic**: wrap
the load→validate→write in `codec_jsonstore.file_lock(manifest.json)` — the same
flock primitive PR-4E used for `audit.log` across the 11 daemons. Behaviour is
otherwise identical (still raises `InvalidStatusTransition` on an illegal move).

```python
with _status_lock(manifest_path):     # codec_jsonstore.file_lock, nullcontext fallback
    manifest = load_manifest(...); validate; save_manifest(...)
```

- **Cross-process** (daemon vs dashboard) is exactly the flock's scope; that's
  the B-7 race. (Intra-process threads each operate on *different* agents'
  manifests, so a per-process lock isn't the gap here.)
- **Graceful fallback:** if `codec_jsonstore` is unavailable (headless/CI),
  `_status_lock` returns `contextlib.nullcontext()` — no lock, but never breaks.
- The agent dir is `mkdir`-ed before locking so the `.lock` sidecar can be created.

## Scope / deferred

- **In:** the flock CAS on `set_status` (the architecture-review's
  highest-leverage fix). Both the daemon and the PWA inherit it for free.
- **Deferred — B-9** (approval is a non-transactional 3-file write; a crash
  mid-`approve_plan` can brick the agent + the illegal `awaiting_approval →
  aborted` recovery transition): a separate restructure of `approve_plan`
  (fold `plan_hash` into the status write + a daemon reconciliation pass). Its
  own PR — kept out of here so the status-lock change stays small + safe.

## Test plan (`tests/test_status_cas.py`)

- `set_status` acquires `codec_jsonstore.file_lock` on the agent's `manifest.json`
  (monkeypatch `file_lock` with a recorder; assert it was entered) — the B-7
  regression guard.
- Behaviour preserved: a legal transition persists; an illegal one still raises
  `InvalidStatusTransition` (no write).
- Existing `test_agent_runner.py` / `test_agent_plan.py` / `test_atomic_set_status.py`
  stay green (the contract is unchanged).

## Rollback

`set_status` body + one helper in `codec_agent_plan.py` + one test. `git revert`.
