# PR-4A — codec.py graceful shutdown (C-1) (DESIGN)

**Status:** IMPLEMENTED — Wave 4 (Reliability) opener. Removed codec.py's no-op SIGINT/SIGTERM handlers; added `_graceful_shutdown` (terminates `rec_proc`/`overlay_proc` + unlinks `audio_path`, exits 0 on the signal path, idempotent + never-raises) registered via `signal.signal` + `atexit` in `main()`. Added `import sys, atexit`. 5 tests (`tests/test_graceful_shutdown.py`); full suite 1507 passing, zero new; zero net-new ruff. **Scoped to C-1 (codec.py);** H-1 (the `codec_lifecycle.py` helper across the other 10 daemons) follows.
**Finding:** C-1 [CRITICAL] — the main `codec.py` daemon installs no-op SIGINT/SIGTERM handlers and has no shutdown path, so every `pm2 restart open-codec` (or reboot, or max-memory restart) orphans the `sox` recording subprocess + tkinter overlays and leaks temp `.wav`/`.png` files.
**Wave:** 4. This is the **start of Wave 4** — scoped to **C-1 only** (the main daemon). H-1 (the `codec_lifecycle.py` helper across the other 10 PM2 daemons) is the larger follow-on (PR-4A-2+).

---

## 1. The bug (codec.py:1-4)
```python
import signal
signal.signal(signal.SIGINT,  lambda *a: None)
signal.signal(signal.SIGTERM, lambda *a: None)
```
No-op handlers installed before any cleanup logic; **no `atexit`** anywhere. PM2 SIGTERMs, the daemon ignores it, PM2 SIGKILLs after 10s → `state["rec_proc"]` (sox) reparents to init and keeps recording; `state["overlay_proc"]` (tkinter) orphaned; `state["audio_path"]` `.wav` never unlinked.

## 2. Fix (mirror codec_dictate.py's `atexit._cleanup` + do_stop_voice cleanup)
- **Remove** the two no-op handler lines (keep `import signal`). Add `import sys, atexit` (neither is currently imported).
- **New `_graceful_shutdown(signum=None, frame=None)`** — idempotent, never-raises:
  - `state["rec_proc"]`: `terminate()` + `wait(timeout=2)`, set None.
  - `state["overlay_proc"]`: `terminate()`, set None.
  - `state["audio_path"]`: `os.unlink` if it exists, set None.
  - if `signum is not None` (called as a signal handler, not atexit): `sys.exit(0)`.
- **Register in `main()`** (after `setup_logging()`, before the listener loop): `signal.signal(SIGTERM, _graceful_shutdown)`, `signal.signal(SIGINT, _graceful_shutdown)`, `atexit.register(_graceful_shutdown)`.

Behavior: a SIGTERM now terminates the children + unlinks the temp file + exits 0 (within PM2's 10s window, before SIGKILL). atexit covers normal exit. Idempotent (the signal path's `sys.exit(0)` re-fires atexit → second call no-ops on the now-None state).

**Why register in `main()` (not at module top):** the handler needs `state` (defined at codec.py:131) in scope. During the brief import window before `main()`, SIGTERM falls back to the default (terminate) — fine, there's no recording state yet. The real handler (registered last) wins over any pynput-installed handler.

## 3. Test plan
- `tests/test_graceful_shutdown.py`: `_graceful_shutdown()` (atexit path) terminates fake `rec_proc` + `overlay_proc`, unlinks a real temp `audio_path`, and nulls the `state` entries; never raises if a proc's `terminate()` throws; `_graceful_shutdown(15, None)` (signal path) raises `SystemExit`. Source invariant: the `lambda *a: None` no-op handlers are gone; `atexit.register` is present.
- Regression: full suite — the 23 known-baseline failures, **zero new**. (Note: `test_full_product_audit::test_dry_run_enforcement` is a *pre-existing baseline failure* asserting `DRY_RUN` in the generated session script — unrelated to these top-of-file signal lines.)

## 4. Risk + rollback
- **Blast radius:** `codec.py` only — top-of-file handlers + one new helper + 3 registration lines in `main()`. Signal handling on the main daemon is PM2-supervision-adjacent → covered by a unit test for the cleanup + a manual `pm2 restart` check before merge.
- **Rollback:** single-commit revert restores the (buggy but stable) no-ops.

## 5. Wave 4 sequence (from the consolidated triage, for reference)
PR-4A **C-1** (this) → H-1 lifecycle helper for the other 10 daemons → C-2 (`pwa_response.json` per-request files) → C-3+C-4+M-1+M-2 (atomic-write + `flock` + eviction for the `~/.codec/*.json` writers) → C-5 (`_atomic_set_status`) → H-3 (audit `flock`) → H-2 (`state` lock) → H-4/5/6/M-6 (bounded dicts) → H-7/8/9 (tempfile leaks) → M/L misc. Each its own design-first PR.
