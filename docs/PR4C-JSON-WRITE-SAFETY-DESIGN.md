# PR-4C — JSON write safety: notifications + pending_questions (C-3, C-4, M-1, M-2) (DESIGN)

**Status:** IMPLEMENTED. New `codec_jsonstore.py` (`atomic_write_json` + `file_lock`). C-3+M-1: `routes/_shared._write_notifications` → `atomic_write_json` + cap 500. C-4+M-2: codec_ask_user's 3 pending-questions RMW sites wrapped in `file_lock(PENDING_QUESTIONS_PATH)` + `_save_pending_questions` prunes resolved >24h. 12 tests (`tests/test_json_write_safety.py`); 51 ask_user/pending tests still green; full suite 1519 passing, zero new; zero net-new ruff.
**Findings:** C-3 (notifications.json non-atomic writer → corruption → reader reseeds fake samples) [CRITICAL]; C-4 (pending_questions.json cross-process read-modify-write race → lost question → agent blocked 600s) [CRITICAL]; M-1 (notifications unbounded) + M-2 (pending_questions never evicts answered/timed_out) [MEDIUM].

---

## 1. New `codec_jsonstore.py` (shared primitive)
`_atomic_write_json` is currently triplicated (codec_proactive / codec_agent_messaging / codec_agent_plan), none with cross-process locking. Canonicalize:
- **`atomic_write_json(path, data)`** — write to a same-dir tmp, `flush` + `os.fsync`, `os.replace`, `chmod 0600`. The durable atomic write (no half-written file ever observed by a reader). For C-3.
- **`@contextmanager file_lock(path)`** — cross-process mutex: open/create `<path>.lock`, `fcntl.flock(LOCK_EX)`, `yield`, release. Locking a dedicated `.lock` sidecar (NOT the data file, whose inode is swapped by the atomic `os.replace`). Held across a whole read→modify→write so two processes can't interleave and lose each other's append. For C-4. (A context manager — not a function-based `update_json` — because codec_ask_user has 5 multi-statement read-modify-write blocks; the swap is `with _FILE_LOCK:` → `with _FILE_LOCK, file_lock(PATH):`, bodies unchanged. flock is per-open-fd + non-reentrant, matching the existing non-reentrant `_FILE_LOCK` — the RMW sites are already non-nested, so no deadlock.)

(The 3 existing `_atomic_write_json` copies can migrate onto this later; not required for these findings.)

## 2. C-3 + M-1 — notifications.json
**Fix (audit's option a — atomicity):** `routes/_shared._write_notifications` → `atomic_write_json` (kills the only non-atomic writer; the other two notification writers — codec_ask_user, codec_agent_messaging — already write atomically, so corruption is eliminated). **M-1:** cap to the last **500** entries inside `_write_notifications` (applied on every routes/_shared write — trims the shared list regardless of which daemon appended, so growth is bounded). The cross-process *lost-write* race for notifications is explicitly accepted per the audit (a missed notification is annoying, not blocking) — `flock` is reserved for the file where a lost write is *critical* (pending_questions, §3). NOT doing partial flock on notifications (some writers wouldn't use it → false protection).

## 3. C-4 + M-2 — pending_questions.json
**Fix (audit — flock):** wrap codec_ask_user's 5 `with _FILE_LOCK:` read-modify-write blocks in `codec_jsonstore.file_lock(PENDING_QUESTIONS_PATH)` (i.e. `with _FILE_LOCK, file_lock(PATH):`) so each read→modify→write is `flock(LOCK_EX)`-serialized **across processes** (all 5, for consistency — a single un-flocked RMW would reopen the race). `_save_pending_questions` routes through `atomic_write_json` (adds the missing fsync). This closes the window where two daemons both read, both write, and one's question is lost (→ its `threading.Event` waiter blocks until the 600s timeout). The in-process `threading.Event` mechanism is unchanged.
**Verify:** the other two writers the audit lists (`codec_voice`, `routes/agents`) go through `codec_ask_user.submit_answer` (not direct writes) — so funnelling codec_ask_user through `update_json` makes ALL pending_questions writes flock-guarded. (Confirm at implementation; if either writes directly, route it too.)
**M-2:** inside the save mutate, prune records with `status in {"answered","timed_out"}` older than 24h (keep `pending` regardless).

## 4. Test plan
- `tests/test_jsonstore.py`: `atomic_write_json` round-trips + leaves no tmp + 0600; a reader never sees partial data (write is replace-based). `update_json` applies the mutate + persists; returns the new data; `default` on missing/corrupt file; **concurrency**: two sequential `update_json` appends both survive (RMW under lock), and the file stays valid.
- `tests/test_json_write_safety.py`: `_write_notifications` writes atomically + caps to 500 (write 600 → 500 kept, newest-first preserved); codec_ask_user ask→answer round-trip persists across a reload; M-2 prunes an answered record dated >24h ago but keeps a pending one.
- Regression: full suite — 23 known-baseline failures, **zero new**. No `skills/` touched.

## 5. Risk + rollback
- **Blast radius:** new `codec_jsonstore.py` + `routes/_shared._write_notifications` (atomic + cap) + codec_ask_user's pending-questions RMW (flock + prune). Both files are documented don't-touch-**by-hand** zones (AGENTS.md §7/§10) — but the *writers* are the legitimate edit surface, and this PR makes them safer. Atomic writes + flock are additive; the on-disk schema is unchanged. The `threading.Event` waiter contract is untouched.
- **Rollback:** single-commit revert.
- **Deferred (flagged):** full cross-writer flock + cap on notifications (route ask_user + agent_messaging notification writes through `update_json` too) — only matters for the accepted lost-write race; a small PR-4C-2 if it ever bites.
