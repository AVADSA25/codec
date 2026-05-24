# PR-4H — tempfile leak fixes via try/finally (H-7, H-8, H-9 + a same-class bonus) (DESIGN)

**Status:** PROPOSED. Ensure four tempfile-creating paths always unlink, even on the failure/timeout path. Touches `codec_observer.py`, `codec_dashboard.py`, `codec_session.py`. New `tests/test_tempfile_leaks.py`.

**Findings:** H-7 (`codec_observer._screencapture_and_ocr_blocking` leaks PNG on subprocess timeout) [HIGH]; H-8 (`codec_dashboard` code-exec leaks tempfiles) [HIGH]; H-9 (`codec_session.speak()` leaks one mp3 per TTS call) [HIGH]. **Bonus (same class):** `codec_session.screenshot_ctx()` has the identical H-7 bug.

---

## 1. Verify-first findings (the audit was partly stale)

- **H-8 is already MOSTLY fixed.** `POST /api/run_code` (the current name; the audit's `_exec_code` is gone) now has a `finally: os.unlink(tmp.name)` (codec_dashboard.py:1878-1881) — the source tempfile **is** cleaned. The **only** remaining leak is the **Rust `.out` compiled binary** (`{tmp.name}.out`, line 1868), which the finally doesn't remove. So H-8 shrinks to: also unlink `.out` in the finally.
- **H-7 overlaps PR-4A-2.** I already gave the capture a `codec_obs_` prefix + a shutdown glob-purge (PR-4A-2). Those are a *backstop*; H-7's actual fix is the **per-call** cleanup so a routine OCR timeout doesn't leak a 2-5 MB PNG every poll. Still needed.
- **Bonus — `screenshot_ctx()` (codec_session.py:196-215)** has the exact H-7 pattern: its `.png` tempfile is unlinked at line 205 only on the **success path**; a `screencapture` timeout (line 200) or a vision-call error skips cleanup. Same file as H-9, same bug class, same PR theme → fixed here (transparently documented, not silent scope-creep).

## 2. Fixes (all: move the unlink into a `finally`, never raise)

### H-7 — `codec_observer._screencapture_and_ocr_blocking`
```python
def _screencapture_and_ocr_blocking() -> str:
    import tempfile
    tmp_png = None
    try:
        with tempfile.NamedTemporaryFile(prefix="codec_obs_", suffix=".png", delete=False) as f:
            tmp_png = f.name
        subprocess.run(["screencapture", …], timeout=2)   # may TimeoutExpired
        result = subprocess.run(["osascript", …], timeout=3)  # may TimeoutExpired
        return (result.stdout or "")[:500]
    except Exception:
        return ""
    finally:
        if tmp_png:
            try: os.unlink(tmp_png)
            except OSError: pass
```
`tmp_png = None` guards the case where `NamedTemporaryFile` itself raises. The inline unlink (old lines 331-334) is removed (now in finally).

### H-8 — `codec_dashboard.run_code` (Rust `.out` only)
```python
    finally:
        for _p in (tmp.name, tmp.name + ".out"):   # .out only exists for Rust; FileNotFoundError is caught
            try: os.unlink(_p)
            except OSError as e: log.debug(f"Temp cleanup failed for {_p}: {e}")
```

### H-9 — `codec_session.Session.speak()` (afplay mp3)
`afplay` is spawned fire-and-forget; nothing waits or unlinks. Spawn a one-shot **daemon** thread that waits for playback then unlinks (the audit's pattern). Requires `import threading` (not currently imported in `codec_session`).
```python
                proc = subprocess.Popen(["afplay", tmp.name])
                def _cleanup_after_play(p=proc, path=tmp.name):
                    try: p.wait()
                    except Exception: pass
                    try: os.unlink(path)
                    except OSError: pass
                threading.Thread(target=_cleanup_after_play, daemon=True).start()
```
Daemon=True so it never blocks process shutdown. If afplay never exits (edge), the daemon thread dies with the process and the OS idle-purge reclaims that one file — strictly better than today's leak-every-call.

### Bonus — `codec_session.Session.screenshot_ctx()`
Restructure to `try/finally` with the unlink in `finally` (and a `tmp_png = None` guard), removing the success-path-only unlink. The early base64 read (`ib`) already happens before the vision call, so holding the file a few hundred ms longer is irrelevant.

## 3. Test plan (`tests/test_tempfile_leaks.py`)
`codec_observer` + `codec_session` import cleanly locally → real behavioral tests; `codec_dashboard.run_code` is async + invokes real compilers → source-invariant.
- **H-7:** monkeypatch `codec_observer.subprocess.run` to raise `TimeoutExpired`; call `_screencapture_and_ocr_blocking()`; assert no NEW `codec_obs_*.png` remains in the temp dir (snapshot before/after) and the call returned `""`.
- **H-9:** build a stub `Session` self (`SimpleNamespace` with tts fields); monkeypatch `codec_session.requests.post` → fake 200 streaming response, `codec_session.subprocess.Popen` → fake proc (`.wait()` returns 0), and `codec_session.threading.Thread` → a stub that runs `target()` synchronously on `.start()`; call `Session.speak(stub, "hi")`; assert the created `.mp3` path was unlinked.
- **Bonus:** monkeypatch `codec_session.subprocess.run` to raise; capture the tempfile path via a `NamedTemporaryFile` wrapper; assert it's unlinked after `screenshot_ctx()` returns `""`.
- **H-8 (source-invariant):** `run_code`'s `finally` unlinks both `tmp.name` and `tmp.name + ".out"`.
- **Regression:** `tests/test_observer.py` + any `codec_session` tests stay green. Full suite — exactly the 41 known-baseline failures, **zero new**. `ruff` per-file delta vs `origin/main` clean.

## 4. Risk + rollback
- **Blast radius:** three functions, each gains a `finally`/cleanup-thread; one new `import threading` in `codec_session`. No schema/API/behavior change on the success path (files were already cleaned on success; this adds the failure-path + fire-and-forget cleanup). H-9's success path now also cleans up (previously never).
- **H-9 thread:** one short-lived daemon thread per TTS utterance — they exit as soon as afplay does (seconds); never accumulate.
- **Rollback:** single-commit revert; no persisted state.
