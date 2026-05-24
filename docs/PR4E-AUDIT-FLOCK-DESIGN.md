# PR-4E — `audit.log` cross-process flock around write + rotation (H-3) (DESIGN)

**Status:** PROPOSED. Wrap `codec_audit._write`'s critical section (rotate → open-append → write → close) in a cross-process `fcntl.flock` on a stable sidecar (`audit.log.lock`), inside the existing in-process `_LOCK`. Reuses `codec_jsonstore.file_lock` (the PR-4C primitive). Touches `codec_audit.py` only (+ new `tests/test_audit_flock.py`).

**Finding:** H-3 (`audit.log` writers don't use inter-process file locking; rotation race can corrupt or lose entries) [HIGH].

---

## 1. The defect (verified)

All 11 PM2 daemons write `~/.codec/audit.log` via `codec_audit._write`. Today:
```python
with _LOCK:                       # threading.Lock — PER-PROCESS only
    _rotate_if_needed()           # may rename audit.log → audit.log.YYYY-MM-DD
    f = _open_audit_log_append()  # os.open(O_WRONLY|O_APPEND|O_CREAT, 0o600)
    f.write(line); f.close()
```
`_LOCK` serializes threads within ONE process; it does nothing across the 11 separate PM2 processes. Three cross-process races (audit §H-3):
- **Race A — concurrent rotation:** two daemons both `rename()` at the day boundary; the loser's `except OSError: return` silently swallows, and ordering can leave a daemon appending to the just-rotated file.
- **Race B — write during rotation:** daemon X holds an append fd while daemon Y `rename()`s; X's writes follow the inode into `audit.log.YYYY-MM-DD` while Y opens the fresh `audit.log` → one logical run's entries split across two files (breaks the multi-emit `correlation_id` paired-cid contract).
- **Race C — line interleaving:** POSIX only guarantees atomic appends ≤ `PIPE_BUF` (~4 KB). Large `extra` payloads (`observation_tick`, `hook_fired`) exceed it; concurrent appends from two processes interleave bytes → corrupt JSON → crashes `codec_audit_analyzer.audit_report` and fails HMAC verification.

Audit integrity is CODEC's compliance foundation (CLAUDE.md §6), so this is HIGH.

## 2. The fix — flock a stable sidecar across the whole critical section

```python
import codec_jsonstore  # stdlib-only; no codec_* imports → no cycle with the foundation module
...
with _LOCK:                                       # in-process (kept)
    with codec_jsonstore.file_lock(_AUDIT_LOG):   # cross-process (fcntl.flock LOCK_EX on audit.log.lock)
        _rotate_if_needed()
        f = _open_audit_log_append()
        try:
            f.write(line)
        finally:
            f.close()
```

Why this is correct:
- **Stable lock inode.** `file_lock(_AUDIT_LOG)` flocks `audit.log.**lock**` — a dedicated sidecar that is **never renamed**. Locking `audit.log` itself would be useless: rotation renames its inode away, so a second process opening the *new* `audit.log` wouldn't contend on the same lock. (Same reasoning PR-4C used for `pending_questions`.)
- **Rotation is inside the lock.** Only one process can rotate-or-write at a time → Race A gone (the loser re-checks `_rotate_if_needed`, sees `mtime_day >= today` or the file already renamed, and appends to the current file) and Race B gone (no writer holds an fd across another process's rename — the rename happens under the same lock as every write).
- **Writes are serialized.** One process appends at a time → Race C gone regardless of line size.
- **Two-layer lock, consistent order** (`_LOCK` then `file_lock`) — mirrors PR-4C's `with _FILE_LOCK, file_lock(PATH):`. Non-reentrant + non-nested (no other `_write` runs inside `_write`), so no deadlock. `_LOCK` is kept so two threads in one process don't both race to open the sidecar.

### Why reuse `codec_jsonstore.file_lock` (not inline)
`codec_jsonstore` imports **only stdlib** (`fcntl, json, os, tempfile, contextlib, typing`) and does **not** import `codec_audit` — so adding `import codec_jsonstore` to the foundational `codec_audit` introduces **no import cycle**. It's the same flock primitive already shipped + tested cross-process in PR-4C (`tests/test_json_write_safety.py`). DRY over a second hand-rolled flock.

## 3. What is NOT changed
- The audit **envelope/schema** is untouched (AGENTS.md §10 don't-touch zone is about the schema — this is purely the write *mechanism*). No new fields, no version bump.
- HMAC signing + secret redaction + 0600 perms (PR-2E) are unchanged — the redact→HMAC→canonical-JSON step still runs *before* the lock; only the disk-append section gains the cross-process lock.
- `verify_audit_log()`, the analyzer, rotation cadence, retention pruning — all unchanged.
- Never-raises contract preserved: the lock block stays inside `_write`'s outer `try/except Exception: pass`, so a flock failure degrades to a dropped line (today's behavior on any write error), never a crash.

## 4. Test plan (`tests/test_audit_flock.py`)
Mirrors `test_audit_integrity.py`'s `_isolate_audit_and_keychain` fixture (redirects `_AUDIT_LOG` + `_AUDIT_DIR` + keychain to tmp):
- **Sidecar created (red→green):** after one `audit()` write, `audit.log.lock` exists in the audit dir.
- **Source invariant (red→green):** `_write` calls `codec_jsonstore.file_lock(` and `_rotate_if_needed()` is inside that block (rotation under the cross-process lock).
- **Concurrency no-corruption (guard):** 8 threads × 250 writes with a large (~6 KB > PIPE_BUF) `extra` payload → every line parses as JSON, total count exact, every correlation_id present.
- **Rotation under writes, no loss (guard):** backdate `audit.log` mtime to yesterday, write → old lines land in `audit.log.<yesterday>`, the new line in a fresh `audit.log`; both files are valid JSON and no line is lost.
- **Integrity preserved (guard):** after writes, `verify_audit_log()` returns `integrity_ok=True` with `signed_lines == total_lines` (the HMAC path still works through the new lock).
- **Never-raises (guard):** monkeypatch `codec_jsonstore.file_lock` to raise → `audit()` still does not propagate.
- **Regression:** `tests/test_audit_perf.py` stays green (flock adds ~tens of µs; budgets are 0.5 ms single / 2.5 ms concurrent local, 10× on CI). Full suite — exactly the 41 known-baseline failures, **zero new**. `ruff` per-file delta vs `origin/main` clean.

## 5. Risk + rollback
- **Blast radius:** `codec_audit.py` — one `import` + wrapping the existing append block in `file_lock`. No schema/API change. Every other module that calls `audit()`/`log_event()` is unaffected.
- **Performance:** one extra `open`+`flock`+`close` of a tiny sidecar per write (~tens of µs uncontended), well within the perf budgets; under contention the writes were already serialized by `_LOCK`, so the contention profile is unchanged.
- **Don't-touch zone:** `codec_audit.py` is AGENTS.md §10 — the schema is the protected surface and it is **untouched**; the design gate is re-run here per §11. AGENTS.md §6 gains a one-line note that audit writes are now cross-process flock-serialized.
- **Rollback:** single-commit revert (removes the `import` + the `file_lock` wrapper, restoring the `_LOCK`-only block). No on-disk format change — old logs (with or without the change) read identically.
