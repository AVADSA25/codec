# PR-4A-2 — `codec_lifecycle.install_handlers`: uniform graceful shutdown for the 5 handler-less daemons (H-1) (DESIGN)

**Status:** PROPOSED. New `codec_lifecycle.py` (`install_handlers(cleanup_fn, name)`) — the uniform SIGTERM/SIGINT/atexit shutdown helper flagged as the follow-on to PR-4A (C-1). Wired into the 5 PM2 daemons that currently have **no** signal handler: `codec_autopilot`, `codec_observer`, `codec_agent_runner`, `codec_imessage`, `codec_telegram`. New `tests/test_lifecycle.py`.

**Finding:** H-1 (Mixed signal handling: 10 of 11 PM2 services have no SIGTERM handler) [HIGH].

---

## 1. The defect (verified)

PM2 sends **SIGTERM** on `pm2 restart` / `reload` / max-memory-restart / watchdog-kill. **Python's default SIGTERM disposition terminates the process immediately — it does NOT run `atexit` handlers or unwind `finally` blocks.** (Only `SIGINT` runs them, because it raises `KeyboardInterrupt`.) So every daemon without an explicit SIGTERM handler is hard-killed mid-work:

| Daemon | Current shutdown | What's lost on SIGTERM |
|---|---|---|
| `codec_autopilot.main` | none | (state.json written per-fire — minimal) |
| `codec_observer.run_daemon` | none | in-flight `screencapture` tempfile leaks (unlinked only on the happy path, `codec_observer.py:329`) |
| `codec_agent_runner.run_daemon` | none | (agent threads are `daemon=True`, state.json atomic per-checkpoint — minimal; clean log only) |
| `codec_imessage.main` | `except KeyboardInterrupt` saves `last_rowid` + `SERVICE_STOP` | **SIGTERM bypasses the save → poll position lost → reprocess/skip on restart** |
| `codec_telegram.main` | `except KeyboardInterrupt` → `SERVICE_STOP` audit | SIGTERM bypasses the `SERVICE_STOP` audit |

`codec.py` (PR-4A/C-1) and `codec_dictate` already do this right; the 2 uvicorn services (`codec_dashboard`, `codec_mcp_http`) handle SIGTERM via uvicorn (their app-level WebSocket/OAuth-flush gap is a **separate, deferred** concern — see §5).

## 2. The helper — `codec_lifecycle.py`

Pure-stdlib (signal / atexit / sys / threading / logging) — **no `codec_*` imports**, so no daemon import cycles.

```python
def install_handlers(cleanup_fn, name="daemon", exit_on_signal=True):
    """Uniform graceful shutdown for a PM2 daemon (H-1). Registers SIGTERM +
    SIGINT handlers that run cleanup_fn ONCE (idempotent, never-raises) then
    sys.exit(0) — so the process exits cleanly within PM2's kill window and
    atexit/finally actually run on a `pm2 restart`. Also registers cleanup_fn
    via atexit for the normal-exit path. Returns the wrapped run-once cleanup
    (for tests / manual call)."""
```

Contract:
- **Idempotent.** A `threading.Event` guards run-once, so SIGTERM-then-atexit (or double signal) runs `cleanup_fn` exactly once.
- **Never raises.** `cleanup_fn` exceptions are caught + logged (a shutdown hook must not crash shutdown).
- **Signal path exits.** The SIGTERM/SIGINT handler runs cleanup then `sys.exit(0)` (raises `SystemExit`, which unwinds the daemon's `while True` loop and runs atexit). `SystemExit` is a `BaseException`, not `Exception`, so the daemons' inner `except Exception` loop-guards do **not** swallow it.
- **Main-thread safe.** `signal.signal()` only works on the main thread; the calls are wrapped in `try/except ValueError` → on failure, log a warning and still register atexit (degrades, never breaks startup). All 5 daemons call `install_handlers` from their main-thread entry point, so this is just belt-and-suspenders.

## 3. Per-daemon wiring (each calls `install_handlers` at the top of its entry point)

1. **`codec_autopilot.main`** — `cleanup = log "graceful shutdown"`. State is atomic per-tick; nothing else to flush.
2. **`codec_agent_runner.run_daemon`** — `cleanup = log "graceful shutdown (N active agents)"`. Threads are `daemon=True`; state.json atomic per-checkpoint; resume-on-restart already correct (Step 9 Q5).
3. **`codec_observer.run_daemon`** — give the screenshot tempfile a distinctive `prefix="codec_obs_"` (one-token change at `codec_observer.py:303`), and `cleanup = log + best-effort glob-purge of `<tmpdir>/codec_obs_*.png`` (safe now that the name is namespaced — directly addresses the finding's "tempfile leaks").
4. **`codec_imessage.main`** — track the live `last_rowid` in a small mutable holder updated in the poll loop; `cleanup = save last_rowid to state.json + audit SERVICE_STOP + log`. This makes SIGTERM do what the existing `except KeyboardInterrupt` already does for Ctrl-C. The existing `except KeyboardInterrupt` block is **kept** as a fallback (harmless if signal-install ever degrades).
5. **`codec_telegram.main`** — `cleanup = audit SERVICE_STOP + log` (offset is in-memory/long-poll; nothing persisted). Existing `except KeyboardInterrupt` kept as fallback.

## 4. Test plan (`tests/test_lifecycle.py`)
Behavioral tests on the standalone helper (monkeypatch `signal.signal` + `atexit.register` to capture registrations; no real signals sent):
- `install_handlers` registers a handler for **SIGTERM and SIGINT** and registers an **atexit** callback.
- The wrapped cleanup is **idempotent** — invoking it twice runs the underlying `cleanup_fn` once.
- The wrapped cleanup **never raises** when `cleanup_fn` raises (and still marks itself done).
- The **signal path calls `sys.exit(0)`** — invoking the captured SIGTERM handler runs cleanup then raises `SystemExit(0)`.
- The **atexit path does not exit** — the registered atexit callback runs cleanup without raising `SystemExit`.
- **Non-main-thread degradation** — when `signal.signal` raises `ValueError`, `install_handlers` still registers atexit and does not propagate.

Source-invariant tests (read each daemon's source — avoids importing daemons that pull `pynput`/native deps): each of the 5 daemons imports `codec_lifecycle` and calls `install_handlers(...)` in its entry function.

Regression: full suite — exactly the 41 known-baseline failures, **zero new**. `ruff` per-file delta vs `origin/main` clean for every touched file.

## 5. Scope, risk, rollback
- **In scope:** the 5 daemons with **no** handler at all (the real force-kill data-loss surface).
- **Deferred (noted, not regressed):** `codec_dashboard` + `codec_mcp_http` already get SIGTERM via uvicorn; their *app-level* cleanup (in-flight WebSocket sessions / OAuth-state + rate-window flush) is a more involved, uvicorn-lifespan change — a separate small follow-on (PR-4A-3) if it ever bites. `codec.py` + `codec_dictate` already done.
- **Blast radius:** 1 new stdlib-only module + 5 small additive wirings (each: an import + a `cleanup` closure + one `install_handlers(...)` call; observer also gets a temp-prefix + glob-purge; imessage gets a 1-line live-rowid holder). No behavior change on the *running* path — only the shutdown path changes (clean exit instead of force-kill). Existing `except KeyboardInterrupt` fallbacks kept.
- **Don't-touch zones:** none of the 5 daemons are §10 "don't-touch-by-hand" *state files*; they're the legitimate edit surface. AGENTS.md §2 module map gains `codec_lifecycle.py`.
- **Rollback:** single-commit revert (removes the module + the 5 wirings). No persisted state migration; the on-disk schemas are unchanged.
