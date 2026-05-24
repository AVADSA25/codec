# PR-4I — small reliability fixes: M-3, M-4, L-1, L-2 (DESIGN)

**Status:** PROPOSED. Four small, independent Wave-4 reliability fixes, each in an importable (unit-testable) module. Touches `codec_audit.py`, `codec_observer.py`, `codec_ask_user.py`, `codec_autopilot.py`. New `tests/test_small_reliability.py`. (This is the testable remainder of Wave 4 — **H-2** (`codec.py` state lock) and **H-5** (`codec_mcp_http` rate window) are deferred because both modules import deps absent locally + in CI (`pynput` / `mcp`), so neither can be unit-tested; they need a stub-import test strategy — tracked as PR-4F + PR-4G-2.)

**Findings:** M-3 (audit rotation failure silently swallowed) [MEDIUM]; M-4 (observer loop dies if `_idle_seconds`/`_load_config` raises) [MEDIUM]; L-1 (`codec_ask_user` writes skip `fsync`) [LOW]; L-2 (`codec_autopilot._load_state` returns `{}` on corrupt → triggers re-fire) [LOW].

---

## 1. M-3 — `codec_audit._rotate_if_needed` swallows rotation failure
```python
try:
    _AUDIT_LOG.rename(rotated)
except OSError:
    return            # ← silent: daemon keeps appending to the un-rotated log
```
A persistent rotation failure (disk full, perms, cross-daemon rename collision) means `audit.log` grows without bound and nothing is visible until the disk fills.

**Fix:** add a module logger (`codec_audit` has none today) and `log.warning(...)` the OSError before returning. **Must use stdlib `logging`, NOT `audit()`** — calling `audit()` from inside the rotation path would re-enter `_write` → deadlock on the non-reentrant `_LOCK` (same hazard the PR-2E keychain bootstrap avoids). No fallback-filename logic (keep it trivial); visibility is the fix.

## 2. M-4 — observer loop body outside the try
`run_daemon`'s `while True` wraps only `poll()` in a try; the following `_idle_seconds()` + cadence computation + shift-report are unprotected. An exception there (macOS API throttle during Spotlight reindex, etc.) breaks the loop → the daemon dies → PM2 restarts but observation halts + the ring buffer clears during the window.

**Fix:** wrap the whole iteration body in a try (keeping `poll()`'s existing inner try so a poll failure stays isolated), default `cadence = 60` before the try, and leave only `time.sleep(cadence)` outside. Net: any iteration-body exception is logged and the loop survives.
```python
while True:
    if not _enabled():
        time.sleep(30); continue
    cadence = 60
    try:
        cfg = _load_config()
        try: poll(cfg=cfg)
        except Exception as e: log.warning("[observer] poll iteration failed: %s", e)
        idle = _idle_seconds()
        cadence = (… cfg cadences …)
        … long-idle reset …
        _maybe_fire_shift_report(idle)
    except Exception as e:
        log.warning("[observer] iteration failed: %s", e)
    time.sleep(cadence)
```

## 3. L-1 — `codec_ask_user` writes skip `fsync`
Three sites use `tmp.write_text(json.dumps(…, default=str)); os.replace(tmp, PATH)` (pending_questions save + two notification writes). `Path.write_text` doesn't `fsync`, so a hard crash between write and replace can land a replaced-but-stale file. (PR-4C added cross-process `file_lock` around the *callers*, but the write itself still skipped fsync.)

**Fix:** a small module helper that preserves the exact serialization (`default=str`) while adding the flush+fsync:
```python
def _atomic_write_text(path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        f.write(text); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
```
The 3 sites become `_atomic_write_text(PATH, json.dumps(data, indent=2, default=str))`. (Not `codec_jsonstore.atomic_write_json` — that lacks `default=str`, so a stray non-JSON value would raise where today's code coerces. Same tmp naming (`.with_suffix(".tmp")`) as before.)

## 4. L-2 — `codec_autopilot._load_state` returns `{}` on corrupt → re-fire
`_load_state` catches *all* exceptions and returns `{}`; an empty state makes every trigger think "not fired today" and re-fire (a double morning-briefing is harmless, but a double outbound message is a real-world consequence).

**Fix:**
- `_load_state`: narrow the except — on `json.JSONDecodeError` log a loud **ERROR** and return a sentinel `{"__corrupt__": True}`; on `OSError` return `{}` (transient/first-run).
- `_tick`: early-return `if state.get("__corrupt__"):` — **refuse to fire any trigger** while the state file is corrupt. Because `_tick` bails before `_save_state`, the corrupt file is left in place for the user to notice + fix (autopilot is effectively paused until then), exactly as the audit recommends.

## 5. Test plan (`tests/test_small_reliability.py`)
All four modules import cleanly → real unit tests (M-4's infinite loop is the one source-invariant):
- **M-3:** monkeypatch `_AUDIT_LOG.rename` to raise `OSError`, backdate mtime so rotation triggers, capture the module logger → assert a warning was emitted and `_write` did not raise.
- **M-4 (source-invariant):** `run_daemon`'s body — `_load_config()` + `_idle_seconds()` + `_maybe_fire_shift_report(` — sits inside a `try:` (i.e. an exception there can't escape the loop).
- **L-1:** monkeypatch `os.fsync` to record calls; `_atomic_write_text(tmp, "x")` round-trips the content, calls `os.fsync` ≥1, and leaves no `.tmp` behind. Source-invariant: the 3 sites no longer call `tmp.write_text(`.
- **L-2:** `_load_state` → `{"__corrupt__": True}` on garbage, `{}` on missing, parsed dict on valid (monkeypatch `STATE` to a tmp path). `_tick({"enabled":True,"triggers":[…]}, {"__corrupt__":True}, registry)` fires **nothing** (monkeypatch `_fire`).
- **Regression:** audit/observer/ask_user/autopilot suites green. Full suite — exactly the 41 known-baseline failures, **zero new**. `ruff` per-file delta vs `origin/main` clean.

## 6. Risk + rollback
- **Blast radius:** one log line (M-3) + a module logger; one try-scope widening (M-4); one helper + 3 call swaps (L-1); a narrowed except + one early-return (L-2). No schema/API/format change (L-1 keeps `indent=2, default=str`; the `.tmp` naming is unchanged). M-3 uses stdlib logging only (no `audit()` re-entry).
- **Behavior deltas:** M-3 now emits a WARNING on rotation failure (previously silent). L-2 now *pauses* autopilot firing on a corrupt state file instead of re-firing — the intended, safer behavior.
- **Rollback:** single-commit revert; no persisted state changes.
