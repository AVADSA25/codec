# PR-4F — `codec.py` state lock: atomic check-then-set for recording + active (H-2) (DESIGN)

**Status:** PROPOSED. Add `_state_lock` + three small atomic helpers and wire them into the three compound check-then-set sites in `codec.py`'s keyboard + wake-word threads, so two threads can't both pass a `if not state[...]:` guard. The last remaining Wave-4 HIGH. Touches `codec.py` only. New `tests/test_codec_state_lock.py`.

**Finding:** H-2 (`state` dict in `codec.py` mutated by 3+ threads without a lock) [HIGH]. `codec.py` is the main `open-codec` daemon — an AGENTS.md §10-class file — so this is design-first + behavior-preserving + fully tested.

---

## 1. The defect (verified)
`state` is mutated by three threads: the pynput keyboard listener (`on_press`/`on_release`), `wake_word_listener`, and the `worker`. Single-field reads/writes are GIL-atomic, but **compound check-then-set** sequences are not. Three sites:

1. **Recording start (the bug the audit cites)** — `codec.py:781-798` (`on_press`, F18):
   ```python
   if not state["recording"]:        # check
       if _core.tts_playing: return
       if time.time() < _dispatch_cooldown: return
       state["recording"] = True     # set
       state["rec_start"] = time.time()
       ...push(do_start_recording)   # starts sox
   ```
   Two threads (double-F18, or F18 racing the wake-word path) both pass the check → both set `recording=True` → **two sox processes** write the same `audio_path` → garbled audio, neither cleaned up.
2. **Wake-word activation** — `codec.py:721-723` (`wake_word_listener`): `if not state["active"]: state["active"] = True; ...`.
3. **F13 toggle** — `codec.py:759-771` (`on_press`): `if state["active"]: state["active"]=False ... else: state["active"]=True ...`.

Sites 2 + 3 race each other on `state["active"]` (wake-thread activating while F13 toggles). (The dead `codec_keyboard.py` already has a `_state_lock` for exactly this — but it's not imported anywhere; `codec.py` is the live path.)

## 2. Fix — extract the check-then-set into lock-guarded helpers
Add `_state_lock = threading.Lock()` and three tiny helpers that do **only** the atomic decision; all expensive work (sox, overlays, sounds, dispatch) stays **outside** the lock:
```python
_state_lock = threading.Lock()

def _try_begin_recording() -> bool:
    """Atomically claim the recording slot. True if this caller acquired it
    (sets recording=True + rec_start); False if one was already in progress."""
    with _state_lock:
        if state["recording"]:
            return False
        state["recording"] = True
        state["rec_start"] = time.time()
        return True

def _activate_if_off() -> bool:
    """Atomically set active=True if it was off; True if this caller changed it."""
    with _state_lock:
        if state["active"]:
            return False
        state["active"] = True
        return True

def _toggle_active() -> bool:
    """Atomically flip active; return the NEW value."""
    with _state_lock:
        state["active"] = not state["active"]
        return state["active"]
```

**Wiring (behavior-preserving):**
- **F18 (781-798):** keep the cheap unlocked pre-check + the TTS/cooldown guards, then replace `state["recording"]=True; state["rec_start"]=time.time()` with `if not _try_begin_recording(): return`. A thread that loses the race returns instead of starting a second sox.
- **Wake (721-723):** `if _activate_if_off(): push(show_toggle_overlay(True, …))`.
- **F13 (759-771):** keep the 1.5 s debounce, then `if _toggle_active():` → ON branch (was the `else`), `else:` → OFF branch (was the `if`). Same side effects per branch; only the flip is now atomic.

Single-field reads in display/guard code (`if not state["active"]: return`, `if key==f18 and state["recording"]:`, the wake-loop `state["recording"]` skip) stay unlocked — GIL-atomic, and a stale read there is harmless (worst case one extra loop iteration).

## 3. Why extraction (not inline `with _state_lock:`)
Extracting the atomic decision into named helpers (a) keeps the lock's critical section minimal (no sox/push/print under the lock — those can block), (b) makes the fix **unit-testable** (the helpers are pure state+lock operations — testable for exactly-one-winner under concurrency), and (c) keeps the handler diffs tiny (one line each). Mirrors the PR-3D extraction pattern.

## 4. Test harness — contained `pynput` stub
`codec.py` does `from pynput import keyboard` (absent locally + in CI → `test_graceful_shutdown` / `test_dispatch_inner_helpers` / `test_smoke` are baseline failures). Verified: `codec.py` imports cleanly with only `pynput`/`pynput.keyboard` stubbed, and module import has no heavy side effects (signal/threads are in `main()`, per PR-4A). The fixture (same contained pattern as PR-4G-2):
- `monkeypatch.setitem(sys.modules, "pynput"/"pynput.keyboard", MagicMock())` (auto-reverted).
- Pop `codec` from `sys.modules` before (fresh import) AND after (so the stub-based `codec` doesn't leak to `test_graceful_shutdown` et al., whose baseline failures must stay — verified by **zero-new AND zero-fixed**).
- The helpers under test are real code.

## 5. Test plan (`tests/test_codec_state_lock.py`)
- `_try_begin_recording`: from `recording=False` returns True + sets `recording`/`rec_start`; a second call returns False.
- **Concurrency (the core guarantee):** reset `recording=False`, fire `_try_begin_recording` from N threads → **exactly one** returns True (repeat over several rounds).
- `_activate_if_off`: `active=False` → True + `active=True`; second → False; N-thread → exactly one True.
- `_toggle_active`: `False`→`True`→`False` round-trips the new value.
- **Source invariants:** `_state_lock = threading.Lock()` present; `_try_begin_recording(` used in the F18 handler region; `_activate_if_off(` in the wake region; `_toggle_active(` in the F13 region.
- **Isolation:** full suite — exactly the 41 baseline failures, **zero new AND zero fixed** (the pynput stub must not flip `test_graceful_shutdown`/`test_dispatch_inner_helpers`/`test_smoke`). `ruff` per-file delta vs `origin/main` clean.

## 6. Risk + rollback
- **Blast radius:** `codec.py` — one lock + three ~5-line helpers + three one-line handler swaps. **Behavior-preserving:** the helpers do exactly what the inline check-then-set did; only atomicity is added. No new module-level work, no schema/IPC change. Expensive work stays outside the lock (no new blocking).
- **§10 sensitivity:** `codec.py` is the live main daemon. The change is the minimal, audit-recommended fix; the keyboard/wake hot paths keep all existing guards + side effects in the same order. Reviewed line-by-line in the diff.
- **Rollback:** single-commit revert (removes the lock + helpers, restores inline check-then-set). No persisted state.
