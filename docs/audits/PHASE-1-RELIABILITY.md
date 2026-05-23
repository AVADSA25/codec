# PHASE 1 AUDIT C — RELIABILITY + CRASH SURFACE

**Date:** 2026-05-17
**Auditor:** general-purpose agent
**Scope:** Anything that can crash CODEC, hang it, or leave it in a bad state during daily use

## Summary
- Total findings: 22
- Critical: 5, High: 9, Medium: 6, Low: 2
- Key themes:
  - **Inter-process file races on `~/.codec/*.json` state files.** Per-process `threading.Lock` is used as if it were `fcntl.flock`, but multiple PM2 daemons (`codec-dashboard`, `codec-mcp-http`, `codec-agent-runner`, `codec-observer`, `codec-autopilot`, main `codec.py`) read/write the same files. Mix of atomic (`os.replace`) and non-atomic (`open("w")`) writers on the same paths is the worst failure mode — a non-atomic writer can corrupt the file between an atomic writer's read-modify-write cycle.
  - **Signal handling is broken on the main process.** `codec.py:3-4` installs no-op handlers for `SIGINT` and `SIGTERM` before any cleanup logic runs. PM2's graceful-stop signal is dropped, the daemon is force-killed 10s later, all in-flight `sox` recording subprocesses + tkinter overlays + temp files leak on every restart.
  - **In-memory job state evaporates silently.** `_agent_jobs`, `_pending_approvals`, `_autoescalate_silence_set`, `_RATE_WINDOW`, `_MUTE_CACHE`, stuck-detection ring buffers, trigger cooldowns, voice-session resume map — all live in process memory with no persistence and no eviction. A PM2 restart erases them; a long-running daemon accumulates them.
  - **PWA bridge file has confirmed race conditions** (pre-audit P-7). Non-atomic in-place write of `~/.codec/pwa_response.json`, no request/response correlation, stale-detection uses mtime > 10s. Two concurrent PWA commands can deliver each other's responses.

## Methodology

1. Read `CLAUDE.md` and `ecosystem.config.js` to map every PM2 daemon and its entry-point module.
2. Grep'd `threading.Lock`, `threading.Event`, `threading.Thread`, `threading.Condition`, `threading.Semaphore` across all `codec*.py` modules and counted ~57 sites; reviewed the shared-state ones individually.
3. Located every `subprocess.run` / `subprocess.Popen` / `subprocess.check_output` call (107 total); checked timeout presence and cleanup on failure.
4. Mapped every `~/.codec/*.json` state file to all reader/writer modules; flagged any mismatch in write semantics (atomic vs. non-atomic).
5. Walked the state machines in `codec_agent_plan._VALID_TRANSITIONS` and `codec_agent_runner._atomic_set_status` wrapper; confirmed transitions are validated but failures are swallowed.
6. Checked SQLite concurrency setup in `codec_memory.py` and `routes/_shared.py:get_db` (WAL + 5s busy_timeout — solid).
7. Audited `tempfile.NamedTemporaryFile(delete=False)` sites for unlink-on-error coverage (16 sites; 3 confirmed leaks on subprocess failure).
8. Verified P-7 (PWA bridge race) — file is actually `~/.codec/pwa_response.json`, not `/tmp/q_pwa_response.json` as pre-audit stated.
9. Reviewed observer ring buffer (bounded — good), notifications growth (unbounded — bad), `_agent_jobs` dict (unbounded — bad).
10. Checked signal/atexit handler coverage across all 11 PM2 daemons.

Findings cross-reference each other to avoid double-counting the same root cause.

## Findings

### C-1 — Main `codec.py` daemon ignores SIGINT and SIGTERM, leaks subprocesses on PM2 restart [CRITICAL]

> **Closed by PR-4A** (Wave 4 opener). The two no-op handlers (`signal.signal(SIG*, lambda *a: None)`) are gone; `codec._graceful_shutdown(signum, frame)` now terminates `state["rec_proc"]` (sox) + `state["overlay_proc"]` (tkinter), unlinks `state["audio_path"]`, and exits 0 on the signal path — registered via `signal.signal(SIGTERM/SIGINT, …)` + `atexit.register(…)` in `main()` (after `state` is in scope). Idempotent + never-raises (mirrors `codec_dictate`'s `atexit._cleanup`). So `pm2 restart open-codec` / reboot / max-memory restart no longer orphans the recording subprocess or leaks temp `.wav`/`.png`. Pinned by `tests/test_graceful_shutdown.py` (5). See `docs/PR4A-CODEC-GRACEFUL-SHUTDOWN-DESIGN.md`. **H-1** (the `codec_lifecycle.py` helper for the other 10 PM2 daemons) is the follow-on.
**Location:** `codec.py:1-4`
**Description:**
```python
#!/usr/bin/env python3
import signal
signal.signal(signal.SIGINT, lambda *a: None)
signal.signal(signal.SIGTERM, lambda *a: None)
```
These two lines install no-op handlers BEFORE any cleanup logic runs. There is no `atexit.register(...)` anywhere in `codec.py`. The main daemon has no explicit shutdown path at all.
**Trigger:** Every `pm2 restart open-codec`, every system reboot, every PM2-triggered max_memory restart (limit 512M).
**Impact:**
- `sox` recording subprocess (if currently recording) is orphaned. PM2 SIGKILLs `codec.py` after 10s; the `sox` child reparents to init and keeps recording into a .wav file that is never cleaned up.
- Tkinter overlay subprocesses (recording overlay, screenshot overlay, toggle overlay) are orphaned the same way.
- Temp `.wav` and `.png` files in `/var/folders/.../T/tmp*` accumulate forever.
- The PWA voice session marker `~/.codec/voice_session.json` is owned by `codec_voice.VoicePipeline.run` (in `codec-dashboard`) — this finding does NOT affect that. But the keyboard-listener thread's `state["audio_path"]` and `state["overlay_proc"]` references are lost; no graceful sigterm-driven cleanup.
- On Mac sleep/wake cycles, PM2 may restart the process, the listener thread dies silently, F13/F18 stop responding until a manual restart.
**Recommended fix:** Remove the no-op handlers. Register a single SIGTERM handler that terminates `state["rec_proc"]`, `state["overlay_proc"]`, and any tkinter children, then unlinks `state["audio_path"]` and tempfiles, then `sys.exit(0)`. Mirror the pattern in `codec_dictate.py:650-652`.
**Effort:** small

---

### C-2 — `~/.codec/pwa_response.json` bridge has race conditions and no request/response correlation [CRITICAL]
**Location:** `codec_dashboard.py:916-1000` (writer), `codec_dashboard.py:1128-1136` (reader)
**Description:** Multiple defects on the same file:
1. **Non-atomic write.** Line 999: `with open(resp_file, "w") as f: json.dump(...)` writes in place. A crash mid-write leaves a half-written JSON the next reader will fail to parse.
2. **No mutex between concurrent writers.** Two near-simultaneous `POST /api/command` requests both delete the stale file (line 920) and both schedule `asyncio.create_task(_process_command())` (line 1029). Whichever finishes second clobbers the first's response.
3. **No correlation ID on the response.** `{"response": answer, "task": task, "ts": ...}` — no `request_id`. The reader has no way to tell whether the response on disk belongs to its current query or a previous one.
4. **Stale-file mtime check is racy.** Reader at line 1132-1134: `file_age = time.time() - os.path.getmtime(resp_file); if file_age > 10: os.unlink(resp_file)`. Writer writes at T=0, reader checks at T=9.9 and reads stale data. Or writer is mid-write at T=10 and reader unlinks the file out from under it.
**Trigger:** Sending two PWA commands within ~10 seconds of each other, or any reader-while-writer race.
**Impact:** Wrong response delivered to a command, half-written JSON parse errors logged, occasional silent "no response" if the file was just unlinked. P-7 is **confirmed**.
**Recommended fix:** Move to a per-request file `~/.codec/pwa_response_{request_id}.json` written atomically (tmp+rename), with a startup cleanup that purges files older than 10 min. Or move the entire bridge to the `conversations` DB table and remove the file path entirely — the DB fallback already exists at line 1138-1146.
**Effort:** medium

---

### C-3 — `notifications.json` has three writers with three different write semantics (atomic vs. in-place) [CRITICAL]

> **Closed by PR-4C** (audit option a — atomicity). New `codec_jsonstore.atomic_write_json` (tmp+fsync+`os.replace`+0600); `routes/_shared._write_notifications` (the only non-atomic writer) now uses it, so a reader never observes a half-written file → no more corrupt-parse → fake-sample reseed. The other two writers (codec_ask_user, codec_agent_messaging) were already atomic. The cross-process *lost-write* race is accepted per the audit (a missed notification is non-blocking; `flock` is reserved for pending_questions, C-4 — partial flock across only some writers would be false protection). See `docs/PR4C-JSON-WRITE-SAFETY-DESIGN.md`.
**Location:**
- `routes/_shared.py:103-107` — `_write_notifications()` uses `open(NOTIFICATIONS_PATH, "w")` — **NOT atomic**
- `codec_ask_user.py:191-193`, `:621-623` — writes via tmp+`os.replace` — atomic
- `codec_agent_messaging.py:215` — writes via `_atomic_write_json` (tmp+fsync+replace) — atomic

The three writers also use three different locks: `_notif_lock` (in `routes/_shared.py`), `_FILE_LOCK` (in `codec_ask_user.py`), and no lock at all in `codec_agent_messaging.py`. None of these are inter-process locks — they're all `threading.Lock()`, so they only protect within ONE process. `codec-dashboard` and `codec-agent-runner` and `codec-autopilot` are SEPARATE processes.
**Trigger:** Any concurrent notification write across the three daemons. The autopilot fires at 07:30, an agent finishes a checkpoint at 07:30, and the user clicks "Mark all read" in the PWA at 07:30. Three writers, one file.
**Impact:** Lost notifications. Half-written JSON that fails to parse on next read (the reader at `routes/_shared.py:54-56` falls back to seeding sample data, which the user has already deleted — so the dashboard suddenly shows fake samples). Mark-as-read silently reverted.
**Recommended fix:** Pick one writer interface. Either (a) all three call `_atomic_write_json` and accept the read-modify-write race (some writes will be lost but the file stays valid), or (b) move notifications to SQLite where the write/read contention is handled by WAL + busy_timeout. (a) is the smaller change.
**Effort:** small

---

### C-4 — `pending_questions.json` has cross-process race window between read and atomic-write [CRITICAL]

> **Closed by PR-4C.** codec_ask_user's 3 read-modify-write blocks (ask-append, timeout-mark, submit-answer-mark) are now wrapped in `codec_jsonstore.file_lock(PENDING_QUESTIONS_PATH)` (`fcntl.flock(LOCK_EX)` on a `.lock` sidecar) inside the existing `_FILE_LOCK`, so the read→write is serialized **across processes** (codec-dashboard + codec-agent-runner) — two near-simultaneous `ask()`s can no longer clobber each other and strand a `threading.Event` waiter for 600s. The other writers the finding lists (`codec_voice`, `routes/agents`) funnel through `codec_ask_user.submit_answer`, so all writes are flock-guarded. The `threading.Event` mechanism is unchanged. Pinned by `tests/test_json_write_safety.py`.
**Location:** `codec_ask_user.py:124-146` + `codec_voice.py:838-839` + `routes/agents.py:218-219`
**Description:** `_save_pending_questions(data)` is atomic via `os.replace`. But the read-modify-write pattern at `codec_ask_user.py:386-390` is NOT inter-process atomic:
```python
with _FILE_LOCK:
    data = _load_pending_questions()  # read at T=0
    data.setdefault(...).append(record)
    _save_pending_questions(data)     # write at T=1
```
`_FILE_LOCK` is `threading.Lock()` (per process). Process A (`codec-dashboard`) reads at T=0, Process B (`codec-agent-runner`) reads at T=0.1, A writes at T=1, B writes at T=1.1 — B's write contains B's record but NOT A's record. A's question is LOST.
**Trigger:** Two agents (one from a crew running in codec-dashboard, one from the agent-runner) emit `ask_user.ask()` within the same ~100ms window.
**Impact:** The lost question's threading.Event waiter sits forever (until the 600s timeout). The agent thread is silently blocked. User never sees the question. Per CLAUDE.md §10: `~/.codec/pending_questions.json` is a documented "don't-touch zone" specifically because of the race issue.
**Recommended fix:** Use `fcntl.flock(LOCK_EX)` on the pending_questions.json fd to serialize the read-modify-write across processes. Or move the canonical state to SQLite where atomicity is built in. The threading.Event mechanism stays in process memory — that's fine — but the persistence layer must be cross-process-safe.
**Effort:** medium

---

### C-5 — `_atomic_set_status` swallows `InvalidStatusTransition` exceptions, agent state machine can desync [CRITICAL]
**Location:** `codec_agent_runner.py:659-668`
**Description:**
```python
def _atomic_set_status(agent_id: str, new_status: str, reason=None) -> None:
    try:
        from codec_agent_plan import set_status
        set_status(agent_id, new_status, reason=reason)
    except Exception as e:
        log.warning("[%s] set_status %s failed: %s", agent_id, new_status, e)
```
The wrapper catches ALL exceptions (not just `InvalidStatusTransition`), logs a warning, and continues. The 10 call sites (lines 701, 717, 725, 738, 782, 800, 815, 829, 881, 905) do NOT check the return value (there is none) and proceed as if the transition succeeded.
**Trigger:** Any code path that attempts a transition not in `_VALID_TRANSITIONS`. E.g. an agent in `paused` state hits `_atomic_set_status(agent_id, "blocked_on_permission", ...)` — paused only allows `{running, aborted}`, the transition fails silently, the agent stays paused but the runner thinks it's blocked.
**Impact:** Agent in a state inconsistent with what the runner believes. The PWA shows status=paused, but the runner code has emitted `agent_blocked_on_permission` audit events. User clicks "Resume" expecting blocked-resolution; the resume path uses paused state machine and treats it differently.
**Recommended fix:** (a) Catch only `InvalidStatusTransition` and re-raise everything else. (b) Return a bool from `_atomic_set_status` and have call sites abort the operation if the transition failed. (c) Don't suppress at all — let the bare-except in `_run_agent` (line 912) catch it and abort the agent cleanly.
**Effort:** small

---

### H-1 — Mixed signal handling across daemons: 10 of 11 PM2 services have no SIGTERM handler [HIGH]
**Location:**
- `codec.py:1-4` — installs no-op handlers (see C-1)
- `codec_autopilot.py` — no signal handler, no atexit
- `codec_observer.py` — no signal handler, no atexit
- `codec_agent_runner.py` — no signal handler, no atexit
- `codec_imessage.py` — no signal handler
- `codec_telegram.py` — no signal handler
- `codec_dashboard.py` (uvicorn handles its own SIGTERM, but no app-level shutdown of WebSocket sessions)
- `codec_mcp_http.py` (uvicorn handles SIGTERM, but rate-limit window and OAuth state not flushed)
- `codec_dictate.py:650-652` — **the only one with proper atexit + SIGTERM handler**
**Description:** PM2 sends SIGTERM on `pm2 restart` and `pm2 reload`. Without handlers, the process is force-killed after `kill_timeout_ms` (default 1600ms). Anything in-flight is dropped.
**Trigger:** Every PM2 restart, max_memory restart, watchdog kill.
**Impact:**
- `codec-agent-runner`: running agents have threads killed mid-checkpoint. State.json reflects the LAST atomic save, so resume-on-restart picks up from there (correct per design Q5), but any uncommitted progress in the current step is lost. Subprocesses spawned by the running skill are orphaned.
- `codec-observer`: ring buffer is RAM-only by design (correct), but any in-flight `screencapture` subprocess is left orphaned and its tempfile leaks.
- `codec-autopilot`: trigger state.json is written after each fire, so worst case is one trigger re-fires.
- `codec-imessage` / `codec-telegram`: in-flight outbound messages may be lost (the upstream API may not have ACK'd yet).
**Recommended fix:** Add a uniform shutdown helper module (`codec_lifecycle.py`?) with `install_handlers(cleanup_fn)` that registers SIGTERM + SIGINT + atexit pointing at the same function. Each daemon's `cleanup_fn` does its own resource teardown.
**Effort:** medium

---

### H-2 — `state` dict in `codec.py` mutated by 3+ threads without a lock [HIGH]
**Location:** `codec.py:134-146` (definition), `codec.py:1015-1097` (keyboard listener thread), `codec.py:894-1012` (wake_word_listener thread), `codec.py:224-237` (worker thread)
**Description:** The `state` dict tracks `active`, `recording`, `rec_proc`, `audio_path`, `last_f13`, `last_star`, `last_plus`, etc. — all mutated from multiple threads. There is no lock guarding this dict. The pynput keyboard listener fires `on_press` / `on_release` on its own thread, `wake_word_listener` runs in its own thread, and `worker` runs in a third thread.
**Trigger:** Holding F18 while wake-word fires (race on `state["recording"]`). Toggling F13 while a wake-word is in flight (race on `state["active"]`).
**Impact:** Compound check-then-set operations are not atomic:
```python
# codec.py:1040-1055
if not state["recording"]:        # check
    if _core.tts_playing: return
    state["recording"] = True     # set
    state["rec_start"] = time.time()
```
Two threads can both pass the `if not state["recording"]` check; both think they own the recording. Two sox processes start, neither knows about the other, both write to the same audio_path, audio gets garbled, neither gets cleaned up. (Note: the sibling module `codec_keyboard.py:21` has a `_state_lock` for exactly this purpose, but `codec_keyboard` is **dead code** — not imported anywhere, not in `ecosystem.config.js`. `codec.py` is the live path.)
**Recommended fix:** Add a `_state_lock = threading.Lock()` and wrap every compound check-then-set in `with _state_lock:`. The single-field reads in display code can stay unlocked (Python GIL covers them).
**Effort:** small

---

### H-3 — `audit.log` writers don't use inter-process file locking; rotation race can corrupt or lose entries [HIGH]
**Location:** `codec_audit.py:63 _LOCK`, `:340 _rotate_if_needed`, `:362 _write`
**Description:** All 11 PM2 daemons write to `~/.codec/audit.log` via `codec_audit._write()`. The `_LOCK` is `threading.Lock()` — per-process. Rotation is `_AUDIT_LOG.rename(rotated)` at line 350.

Race A — concurrent rotation: Process X and Process Y both call `_rotate_if_needed` at midnight. X renames audit.log → audit.log.2026-05-16. Y's rename raises OSError (target exists? no, the source doesn't). The except is silent. Y now writes to a new audit.log it just opened. No data loss but the schema is weird if X creates a fresh file before Y opens append-mode.

Race B — write during rotation: Process X has `open(audit.log, "a")` and holds the fd. Process Y calls rename(). On macOS, X's fd is still valid (it's a numbered inode); X writes go to the file that's now named audit.log.YYYY-MM-DD. Y's next writes open the new audit.log. Audit entries get split across files.

Race C — line interleaving: POSIX guarantees atomic appends only for writes ≤ PIPE_BUF (typically 4096 bytes). Most audit lines are under that, but observation_tick / hook_fired with large `extra` payloads can exceed it. Concurrent writes from two processes can interleave bytes within a single line, corrupting the JSON.
**Trigger:** Daily during rotation; constantly during normal multi-process operation.
**Impact:** Audit log integrity is the foundation of CODEC's compliance story (CLAUDE.md §6). Corrupted JSON lines crash `codec_audit_analyzer.audit_report` skill. Split-across-files entries break the multi-emit `correlation_id` paired-cid contract.
**Recommended fix:** Use `fcntl.flock(fd, LOCK_EX)` around the `open("a") + write` block. The lock is cheap; logs are short. Rotation does the rename WHILE holding the lock to serialize across processes.
**Effort:** small

---

### H-4 — `_agent_jobs` dict grows unbounded, no lock, no cleanup [HIGH]
**Location:** `routes/_shared.py:297` (definition), `routes/agents.py:65-106` (mutation)
**Description:** Each call to `POST /api/agents/run` appends an entry: `_agent_jobs[job_id] = {...}`. The thread function mutates `_agent_jobs[job_id]["progress"]`, etc. The dict is never purged. There is no lock.
**Trigger:**
1. Long-running dashboard accumulates entries over days. Eventually `_agent_jobs` has thousands of entries.
2. Two concurrent crew runs both mutate `_agent_jobs[job_id]` from different worker threads. If `routes/agents.py:113 job = _agent_jobs.get(job_id)` is called during iteration in another path, raises `RuntimeError: dictionary changed size during iteration`.
**Impact:** Memory leak. Eventually the dashboard process hits `max_memory_restart: 256M` and PM2 restarts it — losing ALL in-flight crew state because `_agent_jobs` is in memory only.
**Recommended fix:** (a) Wrap mutations in a `threading.Lock`. (b) Add a 24-hour eviction policy that removes completed entries. (c) For durability across restart, persist completed-job results to disk (or to the conversations table).
**Effort:** small

---

### H-5 — `_RATE_WINDOW` dict in `codec_mcp_http.py` grows unbounded per unique IP [HIGH]
**Location:** `codec_mcp_http.py:43-44`
**Description:** `_RATE_WINDOW: dict[str, deque] = defaultdict(deque)` — new entry per unique source IP. Entries are never removed. Cloudflare tunnel forwards `cf-connecting-ip` from real clients; if claude.ai uses a fleet of source IPs (it does — they rotate), each rotation adds a new deque.
**Trigger:** Long uptime of `codec-mcp-http`.
**Impact:** Steady memory growth. Hits `max_memory_restart: 512M` over days/weeks, MCP HTTP service restarts, claude.ai connections drop (and probably bounce back via reconnect logic, but observable hiccup).
**Recommended fix:** After every `_rate_check`, also evict any IP whose deque is empty (i.e. has been silent for >60s).
**Effort:** small

---

### H-6 — `_pending_approvals` dict grows unbounded; expired items never deleted [HIGH]
**Location:** `routes/_shared.py:300`, `codec_dashboard.py:3281-3299`
**Description:** `_pending_approvals` is mutated to add new approvals (typical path), and the "auto-expire after 120 seconds" logic at line 3286-3287 sets `a["status"] = "expired"` but never removes the entry. After hours of operation, the dict contains hundreds of expired entries.
**Trigger:** Long uptime of dashboard.
**Impact:** Memory leak; every `GET /api/approvals` iterates the whole dict and serializes filtered entries to JSON. Slow at scale.
**Recommended fix:** In the auto-expire branch, delete the entry: `del _pending_approvals[aid]`.
**Effort:** trivial

---

### H-7 — `codec_observer._screencapture_and_ocr_blocking` leaks tempfile on subprocess failure [HIGH]
**Location:** `codec_observer.py:297-334`
**Description:**
```python
with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
    tmp_png = f.name
subprocess.run(["screencapture", ...], timeout=2)   # line 306
# OCR via Vision framework via osascript
result = subprocess.run(["osascript", ...], timeout=3)   # line 324
try:
    os.unlink(tmp_png)
except OSError:
    pass
```
The unlink is reached only on the successful path. If `subprocess.run` raises `TimeoutExpired` at line 306 OR line 324, the outer `except Exception: return ""` (line 333) catches and returns — the unlink is SKIPPED.
**Trigger:** OCR timeout (3s). Happens routinely if Vision framework is busy on screen with a lot of text.
**Impact:** Tempfile leak. Observer polls every 60s when active. Each timeout leaks one PNG (typically 2-5 MB on a 4K screen). Over a day of busy work, leaks 100+ MB to `/var/folders/.../T/`. macOS will eventually purge but only on extended idle.
**Recommended fix:** Wrap the entire body in `try/finally` with the unlink in the finally clause.
**Effort:** trivial

---

### H-8 — `codec_dashboard._exec_code` leaks tempfiles on every code-execution request [HIGH]
**Location:** `codec_dashboard.py:1828-1857`
**Description:** `tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False, mode="w")` is created at line 1831 and never unlinked. For Rust code, a `.out` binary file is also created (line 1839, 1846) and never unlinked. The function returns inside try/except blocks that don't reach cleanup code.
**Trigger:** Every `POST /api/exec` call.
**Impact:** Per request: one tempfile (Python/JS/TS/Bash/Go/Swift/Ruby) or two tempfiles (Rust). User running this endpoint frequently accumulates hundreds of files in `/var/folders/.../T/`.
**Recommended fix:** `try/finally` with `os.unlink(tmp.name)` and (for Rust) `os.unlink(tmp.name + ".out")` in the finally.
**Effort:** trivial

---

### H-9 — `codec_session.speak()` leaks one .mp3 tempfile per TTS call [HIGH]
**Location:** `codec_session.py:257-262`
**Description:**
```python
tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
for chunk in r.iter_content(4096):
    tmp.write(chunk)
tmp.close()
subprocess.Popen(["afplay", tmp.name])   # fire-and-forget
```
`afplay` runs detached; the parent doesn't `wait()` for it and doesn't unlink the file when playback finishes. Every TTS utterance leaks one mp3 file (typically 50-200KB).
**Trigger:** Every voice or chat reply where TTS is enabled.
**Impact:** Mickael uses CODEC daily — hundreds of TTS replies per day. After a year, /tmp has gigabytes of leftover mp3 files until OS reboot/idle-purge.
**Recommended fix:** Spawn a one-shot thread that waits on afplay and unlinks the file after playback finishes. Pattern: `threading.Thread(target=lambda p=tmp.name, proc=subprocess.Popen(...): (proc.wait(), os.unlink(p)), daemon=True).start()`.
**Effort:** trivial

---

### M-1 — `notifications.json` grows unbounded; no rotation, no eviction [MEDIUM]

> **Closed by PR-4C.** `routes/_shared._write_notifications` caps to the most-recent `_NOTIF_CAP = 500` entries (`notifications[:500]`, list is newest-first) on every write — trims the shared list regardless of which daemon appended, so growth is bounded.
**Location:** all notification writers (see C-3 for the list)
**Description:** Notifications are appended via `notifs.insert(0, entry)` and `notifs.append(notif)`. No size cap, no age-based eviction. The PWA reads the full file on every poll (every ~30s for the badge count).
**Trigger:** Daily use over weeks. Autopilot fires ~10/day, agents post ~20/day, scheduler ~5/day, ask_user ~2/day. After 3 months, ~3000-5000 entries.
**Impact:** Each notification reads + JSON-parses the full file. After 5000 entries the dashboard polling becomes a measurable CPU spike every 30s. Eventually the file is large enough that the non-atomic writers (see C-3) race window widens.
**Recommended fix:** Cap at last N=500 entries (or last 30 days). Apply on every write inside `_write_notifications`.
**Effort:** trivial

---

### M-2 — `pending_questions.json` has no eviction for `status="answered"` and `"timed_out"` records [MEDIUM]

> **Closed by PR-4C.** `codec_ask_user._save_pending_questions` calls `_prune_resolved` first: records with `status in {answered, timed_out}` whose `answered_at`/`asked_at` is older than `_RESOLVED_TTL_HOURS = 24` are dropped. `pending` records are kept regardless of age; records with an unparseable timestamp are kept (never lose data on a bad field).
**Location:** `codec_ask_user.py:124-146`
**Description:** Records are appended on `ask()` and updated to status=`answered` or `timed_out` on resolution, but never removed. The file grows indefinitely.
**Trigger:** Daily use.
**Impact:** Smaller-scale version of M-1. Eventually the file is large enough that ask_user.ask() blocking reads slow down. Less acute than notifications because frequency is lower.
**Recommended fix:** On every `_save_pending_questions`, prune records older than 7 days OR with `status in {"answered","timed_out"}` older than 24h.
**Effort:** trivial

---

### M-3 — `codec_audit._rotate_if_needed` retains failed rotations silently [MEDIUM]
**Location:** `codec_audit.py:349-352`
**Description:**
```python
try:
    _AUDIT_LOG.rename(rotated)
except OSError:
    return
```
Bare OSError suppression. If rotation fails (target exists, perms problem, disk full), the daemon continues writing to the un-rotated `audit.log`. After several days of failed rotations, audit.log is gigabytes; nothing visibly broken until disk fills.
**Trigger:** Disk full, perms broken, or one daemon rotates while another's target rename collides.
**Impact:** Latent disk-fill bug. The user gets no warning until the disk pressure is acute.
**Recommended fix:** Log the OSError at WARNING level (currently swallowed). Optionally, fall back to a counter-based filename like `audit.log.YYYY-MM-DD.N`.
**Effort:** trivial

---

### M-4 — Observer's main loop exception-handler scope is narrow; an exception in `_idle_seconds` or `_load_config` kills the daemon [MEDIUM]
**Location:** `codec_observer.py:822-854`
**Description:** The `try/except` at line 828-831 wraps only `poll()`. Lines 832-835 (`idle = _idle_seconds(); cadence = ...`) are outside any try. If `_idle_seconds` raises (e.g., on macOS API throttle), the `while True` loop exits and the daemon dies. PM2 restarts (autorestart=true), but during the restart window observation halts and the ring buffer is cleared.
**Trigger:** macOS API momentary failure during system pressure (e.g., during Spotlight reindex).
**Impact:** Observation gaps. The shift-report at 18:00 may be incomplete because observation snapshots were lost.
**Recommended fix:** Move the entire iteration body inside the try block, leave only `time.sleep(cadence)` outside.
**Effort:** trivial

---

### M-5 — `_db_conn` is a single global SQLite connection shared across all FastAPI request threads [MEDIUM]
**Location:** `routes/_shared.py:281-290`
**Description:**
```python
_db_conn = None
def get_db():
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db_conn.execute("PRAGMA journal_mode=WAL")
        _db_conn.execute("PRAGMA busy_timeout=5000")
        _db_conn.row_factory = sqlite3.Row
    return _db_conn
```
`check_same_thread=False` permits cross-thread use, but SQLite serializes internally per-connection. Under load (concurrent PWA users + dashboard polling + agent runner reads), every read waits for every other read. Performance is fine for a single user (Mickael) but the architecture is fragile if a second client joins (claude.ai over MCP HTTP, mobile PWA).
**Trigger:** Concurrent FastAPI workers (uvicorn default is single worker, so this is latent), or a second device hitting the dashboard.
**Impact:** Latency spikes. Possible `sqlite3.OperationalError: database is locked` if busy_timeout is exceeded (5s is usually enough).
**Recommended fix:** Use a per-thread connection via `threading.local()`, OR use a connection pool. For CODEC's scale this is currently fine but worth noting.
**Effort:** medium

---

### M-6 — VoicePipeline `_resumable_sessions` class-level dict never evicts on session expiry [MEDIUM]
**Location:** `codec_voice.py:378-379`, `:412-425`
**Description:** `_resumable_sessions: dict[str, list] = {}` accumulates one entry per dropped WebSocket session. Eviction happens in `_save_for_resume` (line 420-424) but only at next save — if a session is dropped and never reconnected AND the user never creates a new session, the entry sits there until the dashboard restarts.
**Trigger:** Voice sessions dropped due to flaky network without reconnect, then no new voice session for >10 min.
**Impact:** Minor memory leak in the dashboard. Bounded by usage pattern, not unbounded.
**Recommended fix:** Add a periodic sweep (a small `threading.Timer` repeating every 60s) that evicts entries older than `_RESUME_TTL`.
**Effort:** trivial

---

### L-1 — `tmp.write_text()` in `codec_ask_user` skips `fsync` before `os.replace` [LOW]
**Location:** `codec_ask_user.py:144-146`, `:191-193`, `:621-623`
**Description:** `Path.write_text(...)` calls `open + write + close` but does NOT call `os.fsync`. On a hard system crash between the write and the os.replace, the tmp file may be replaced-into-place but contain stale (cached) bytes that hadn't been flushed to disk.
**Trigger:** Hard power loss or kernel panic during a write.
**Impact:** A potentially corrupted pending_questions.json or notifications.json after crash recovery. Compare `codec_agent_messaging._atomic_write_json` (line 90-94) which DOES fsync — it's the right pattern.
**Recommended fix:** Replace `tmp.write_text(...)` with an explicit `open + write + flush + fsync + close` block, mirroring `_atomic_write_json`.
**Effort:** trivial

---

### L-2 — `codec_autopilot._save_state` writes atomically but `_load_state` doesn't reject corrupt files [LOW]
**Location:** `codec_autopilot.py:78-92`
**Description:** If `~/.codec/autopilot_state.json` is corrupted (mid-write crash, hand edit), `_load_state` returns `{}` (line 83 — bare `except Exception: pass`). The empty state means every trigger thinks "I haven't fired today" and re-fires. For morning_briefing, the user just gets one extra notification. For something that sends a real outbound message (Twilio), the user is double-charged or the recipient gets two messages.
**Trigger:** Disk full mid-write, or accidental hand edit.
**Impact:** Double-fire of autopilot triggers. Low severity because most triggers are read-only (briefing, weather), but anything outbound is a real-world consequence.
**Recommended fix:** On JSONDecodeError, log a loud ERROR and refuse to fire any triggers that day — let the user notice and fix manually.
**Effort:** trivial

---

## Entry point reliability table

| Entry point | Module | Network deps | Subprocess deps | Failure modes |
|---|---|---|---|---|
| Keyboard listener (F13/F16/F18/*/+/-) | `codec.py:1015` (`on_press`/`on_release`) | none | sox (record), afplay (sounds), tkinter (overlay), screencapture, osascript (file dialog) | C-1 (signals ignored), H-2 (state race), tempfile leaks on sox subprocess crash, no cleanup on F13 toggle-off |
| Wake-word listener (~hands-free) | `codec.py:894` (`wake_word_listener`) | Whisper :8084 | sox (record-2s loops), screencapture for follow-up | Whisper down → wakes never trigger; sox tempfile leak on subprocess timeout (line 999, follow-up 8s recording); no inter-process gate so two wake-word triggers can race the dispatch_lock |
| Dictate (F5 live + hold-CMD draft) | `codec_dictate.py:282` (`_live_record_loop`) | Whisper :8084 | sox (record 2s chunks), pyperclip, pyautogui | Whisper down → live dictation appends nothing silently; sox subprocess pile-up if queue stuck; tempfile is unlinked in producer / consumer paths but not on full-queue drop edge case |
| PWA chat poll (`/api/command`) | `codec_dashboard.py:870` (`run_command`) | Qwen :8081 | none | C-2 (response file race), no timeout cap on combined skill+LLM path (120s on LLM), no cancellation if the client disconnects (LLM runs to completion regardless) |
| PWA voice (WS) | `routes/websocket.py:21` → `codec_voice.VoicePipeline.run` | Whisper, Qwen, Kokoro, Vision | none | M-6 (resume dict leak); on Whisper down all utterances return empty (no user-visible error, just silence); Kokoro down → no TTS but text still streamed; no shutdown handler if uvicorn SIGTERMs mid-session |
| MCP stdio | `codec_mcp.py` | local Qwen + skill backends | varies per skill | 30s tool timeout — but the skill subprocess (if any) may keep running after the timeout; no per-skill resource caps |
| MCP HTTP (Cloudflare) | `codec_mcp_http.py:73` (`main`) | local Qwen + skill backends | varies | H-5 (rate window dict leak), OAuth state at `~/.codec/oauth_state.json` (CLAUDE.md §10 — touching it kills all claude.ai connections) |
| Autopilot triggers | `codec_autopilot.py:195` (`main`) | varies per skill | varies | L-2 (corrupt state → re-fire); 30s poll latency means a trigger at 07:30:01 fires at 07:30:31 (acceptable) |
| Observer poll | `codec_observer.py:822` (`run_daemon`) | none (local Vision via osascript) | screencapture, osascript, pbpaste | H-7 (tempfile leak on OCR timeout), M-4 (narrow exception scope) |
| Agent runner daemon | `codec_agent_runner.py:1052` (`run_daemon`) | Qwen :8090 | varies per skill | C-5 (state machine swallow), Qwen down → blocked_on_qwen status (resilient by design), no signal handler |
| iMessage outbound | `codec_imessage.py:821` | iMessage DB poll + AppleScript send | osascript, sqlite3 Messages.db | poll loop has no signal handler; tight 5s loop is fine |
| Telegram bot | `codec_telegram.py:105` (`get_updates`) | Telegram Bot API (cloud) | none | 30s long-poll timeout + 10s grace; on network drop sleeps 3s and retries — fine |

## State persistence audit

| File | Atomic write? | Race conditions? | Recovery on crash? |
|---|---|---|---|
| `~/.codec/pwa_response.json` | NO — `open("w")` | C-2: yes, multiple defects | none — file is unlinked on age > 10s |
| `~/.codec/notifications.json` | MIXED — 3 writers, only 2 atomic | C-3: yes | Reader at `routes/_shared.py:54-56` falls back to sample data on parse error — wipes user data |
| `~/.codec/pending_questions.json` | atomic via `os.replace` | C-4: cross-process race window between load and save | atomic write guarantees the file is valid; recovery is "lose at most one write" |
| `~/.codec/voice_session.json` | non-atomic `open("w")` | minor — single writer (VoicePipeline.run) and best-effort failure handling | marker is touch-on-start, remove-on-finally; safe |
| `~/.codec/agents/<id>/state.json` | atomic (`_atomic_write_json` with fsync) | minor — only written by `_run_agent` (single thread per agent) | resume from current_checkpoint works correctly |
| `~/.codec/agents/<id>/manifest.json` | atomic | minor — race between approve_plan path and set_status writes possible | plan_hash check catches manifest/plan drift |
| `~/.codec/agents/<id>/messages.jsonl` | append-only with fsync | append is POSIX-atomic for small writes, NOT for large records | minor risk of partial line on crash mid-write |
| `~/.codec/agent_silence.json` | atomic | none | safe |
| `~/.codec/triggers_killed.json` | atomic | none | safe |
| `~/.codec/triggers.json` (mute config) | user-edited; cache reloaded on `_refresh_mute_cache` | none — caller is user, single writer | safe |
| `~/.codec/shift_report_state.json` | atomic (`_atomic_write_json`) | none — only written by skill | safe |
| `~/.codec/autopilot_state.json` | atomic | L-2: corrupt → empty → trigger re-fire | safe but degrades to double-fire |
| `~/.codec/memory.db` | WAL + busy_timeout 5s | minor (M-5: shared connection) | WAL provides crash recovery |
| `~/.codec/audit.log` | append-only, NO inter-process lock | H-3: rotation race + line interleave | bare-OSError swallows means the daemon never knows rotation failed |
| `~/.codec/agent_global_grants.json` | atomic | minor | safe |
| `~/.codec/observation_summaries/*.md` | `Path.write_text` — NOT atomic, NO fsync | minor — single writer (observer) per file (unique timestamp) | unique filenames eliminate inter-file race |

## Pre-audit finding verification

| ID | Status | Evidence |
|----|--------|----------|
| P-7 | **confirmed (partial — file path differs)** | The pre-audit named `/tmp/q_pwa_response.json`. Actual file is `~/.codec/pwa_response.json` (`codec_dashboard.py:916` writer, `:1128` reader). The defects described in the pre-audit are real and worse than stated: not just race conditions but also non-atomic write and no request/response correlation. See C-2 above for full enumeration. |

## Open Questions for Mickael

1. **`codec.py` SIGTERM suppression (C-1) is deliberate or accidental?** Lines `signal.signal(signal.SIGINT, lambda *a: None)` look like they were added to stop accidental Ctrl-C from killing the daemon during interactive testing. If so, the production behavior on PM2 restart is unintentional and a fix is safe. Confirm before changing.

2. **Are `codec_keyboard.py` and `codec_hotkey` PM2 entry (mentioned in CLAUDE.md §10) actually used?** No imports of `codec_keyboard` exist anywhere in the codebase, and the PM2 ecosystem.config.js does not register `codec-hotkey`. CLAUDE.md should either be updated or `codec_keyboard.py` should be deleted. (Recommend logging this in `docs/known-issues.md`.)

3. **Should notifications and pending_questions be migrated to SQLite?** Several findings (C-3, C-4, M-1, M-2) would dissolve if those files moved to the existing `memory.db`. The trade-off is one more table to backup and a small migration. The current architecture's defects suggest the migration is overdue, but the alternative (proper `fcntl.flock` on the JSON files) is also defensible.

4. **Is `codec.py`'s `state` dict (H-2) intended to be lockless because the GIL makes single-field reads atomic, with no compound operations relied upon?** The code at lines 1040-1055 (F18 handler) clearly DOES rely on a compound check-then-set. A lock is needed unless this is rewritten to a single atomic op via, e.g., an `Enum` state with `compare_and_swap`-style transitions (overkill — just add the lock).

5. **What is the retention policy for `_agent_jobs` (H-4)?** Currently entries live forever in process memory. Should completed jobs persist to disk so the PWA can show "your last 30 crew runs" after a dashboard restart, or is the existing audit log emit enough?

## Files reviewed

- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/CLAUDE.md`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/ecosystem.config.js`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_audit.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_ask_user.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_agent_plan.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_agent_runner.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_agent_messaging.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_agents.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_dashboard.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_dictate.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_dispatch.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_voice.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_observer.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_keyboard.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_autopilot.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_telegram.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_session.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_memory.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_llm_proxy.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_mcp_http.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/codec_triggers.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/routes/_shared.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/routes/agents.py`
- `/Users/mickaelfarina/codec-repo/.claude/worktrees/zealous-villani-2a4867/routes/websocket.py`
