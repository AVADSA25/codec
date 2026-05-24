# PR-7J — Agent-state persistence hardening (Audit B / B-10 + B-11)

**Status:** design → TDD → ship
**Closes:** Audit-B **B-10** (world-readable agent state) + **B-11** (`notifications.json`
write bypasses the cross-process flock contract).
**Branch:** `fix/pr7j-medium-cluster`
**Touches:** `codec_agent_plan.py` + `codec_agent_messaging.py`.

## What

Two coupled persistence-layer hardening fixes (both MEDIUM, both small, same layer):

1. **B-10 — 0600 files / 0700 dirs for agent state.** `plan.json` / `state.json` /
   `manifest.json` / `grants.json` / `agent_global_grants.json` / `agent_silence.json` /
   `messages.jsonl` are written with the default umask (typically world-readable 0644),
   unlike `audit.log` (PR-2E) and `codec_jsonstore` (PR-4C) which explicitly chmod 0600.
   These files hold plan descriptions, **user replies, and skill results (file contents,
   fetched data)** — they should match the repo's hardened posture.

2. **B-11 — `notifications.json` honours the cross-process flock contract.**
   `post_message` does an **un-locked** read-modify-write of `notifications.json` via its
   own `_atomic_write_json`, while every other writer (scheduler, heartbeat, ask_user,
   dashboard) goes through `codec_jsonstore.file_lock` (PR-4C). The runner is a separate
   process; a concurrent agent banner + scheduler notification = a lost update (atomic
   rename preserves file integrity but not the read-modify-write window).

## Why it matters

- B-10: any local user/process can read `~/.codec/agents/<id>/` — inconsistent with the
  local-first / Keychain-hardened secret posture (D-8/D-15/PR-2E).
- B-11: the notification badge is the user's only surface for "your agent needs you" —
  silently dropping one (e.g. a `blocked_on_permission` banner lost to a racing scheduler
  write) means an agent stalls invisibly.

## Design

### B-10 — file/dir perms

Harden the two module-local `_atomic_write_json` helpers (`plan.py`, `messaging.py`) and
`messaging.py:_append_jsonl`:
- Create the temp/append file via `os.open(..., O_CREAT, 0o600)` — bypasses umask, so the
  file is **never** briefly world-readable.
- Defensive `os.chmod(path, 0o600)` (try/except) to cover a stale temp / pre-existing log
  written before this change.
- `os.chmod(path.parent, 0o700)` (try/except) so the agent dir itself isn't traversable
  by other users. Try/except-wrapped for RO/FUSE mounts (same pattern as PR-2E's
  audit-log chmod).

No serialization change (still `indent=2, sort_keys=False`) — keeps on-disk diffs nil.

### B-11 — flock the notifications RMW

- New `_notifications_lock()` → `codec_jsonstore.file_lock(_NOTIFICATIONS_PATH)` with a
  `contextlib.nullcontext()` fallback when `codec_jsonstore` is unavailable
  (headless/CI) — identical shape to `codec_agent_plan._status_lock` (PR-7D).
- `post_message` wraps the **whole** `notifications.json` read → batch-merge → write block
  in that lock, so the read and the write are one cross-process critical section shared
  with every other notifications writer.
- The write itself routes through `codec_jsonstore.atomic_write_json` (which already
  chmods 0600), with a fallback to the now-hardened local `_atomic_write_json`. This also
  gives B-10's 0600 to `notifications.json` for free.

`messages.jsonl` is append-only and single-writer-per-agent (the agent's own thread), so
it needs the 0600 perms (B-10) but **not** the notifications flock — there's no
cross-process read-modify-write on it.

## Schema / API changes

None. No new public API, no on-disk schema change, no audit events. `_notifications_lock`
and the perms hardening are internal. Backward compatible: existing 0644 files get chmod'd
to 0600 on their next write.

## Migration

None required. Files written before this change stay readable; the first write after
upgrade tightens perms. (A pre-existing 0644 `notifications.json` is chmod'd to 0600 on the
next `post_message`.)

## Test plan (TDD — `tests/test_state_hardening.py`)

1. `test_plan_state_files_are_0600` — `save_plan`/`save_manifest`/`save_state`/`save_grants`
   → each file's mode is exactly `0o600`.
2. `test_messages_jsonl_is_0600` — `post_message` → `messages.jsonl` mode `0o600`.
3. `test_notifications_json_is_0600` — `post_message` (not silenced) → `notifications.json`
   mode `0o600`.
4. `test_agent_silence_json_is_0600` — `set_silenced` → `agent_silence.json` mode `0o600`.
5. `test_notifications_write_uses_cross_process_flock` — spy on
   `codec_jsonstore.file_lock`; `post_message` must acquire it for `_NOTIFICATIONS_PATH`
   (B-11).

Full suite: zero new failures vs the 41-failed baseline. Ruff: zero delta vs origin/main.

## Rollback

Revert the single commit. Perms hardening and the notifications flock are additive
behavior; reverting returns to umask-default perms and the un-locked write — no data-shape
change to roll back.
