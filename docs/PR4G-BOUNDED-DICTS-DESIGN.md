# PR-4G — bounded growth + eviction for in-memory dicts (H-4, H-6, M-6) (DESIGN)

**Status:** PROPOSED. Add eviction to three unbounded in-memory dicts that leak until a PM2 max-memory restart. **H-5 (`codec_mcp_http._RATE_WINDOW`) is deferred** — `codec_mcp_http` imports `mcp`/`fastmcp`, which are absent locally AND in CI, so its fix can't be unit-tested; shipping an untested change to a live claude.ai-facing service violates the verify-everything bar. It gets its own PR-4G-2 (with an mcp test stub or careful manual verify). Touches `routes/_shared.py`, `routes/agents.py`, `codec_dashboard.py`, `codec_voice.py`. New `tests/test_bounded_dicts.py`.

**Findings:** H-4 (`_agent_jobs` unbounded, no lock, no cleanup) [HIGH]; H-6 (`_pending_approvals` expired entries never deleted) [HIGH]; M-6 (`_resumable_sessions` never evicts on idle) [MEDIUM].

---

## 1. H-4 — `_agent_jobs` (dashboard crew jobs)

`POST /api/agents/run` does `_agent_jobs[job_id] = {"status":"running","crew":…,"progress":[],"started":<iso>}` and a daemon thread later flips `status` to `complete`/`error`. Entries are **never removed** and there's **no lock** — so (1) the dict grows unbounded over days (→ `max_memory_restart: 256M`), and (2) the eviction-sweep iterating while a worker/endpoint adds a key risks `RuntimeError: dictionary changed size during iteration`.

**Fix (`routes/_shared.py`):**
- `_agent_jobs_lock = threading.Lock()` (new; alongside the existing `_approval_lock`).
- `_evict_stale_agent_jobs(now=None, ttl_seconds=86400)`:
  - Under `_agent_jobs_lock`, **snapshot-iterate** `list(_agent_jobs.items())`.
  - Drop entries whose `status != "running"` AND whose `started` ISO timestamp is > 24 h old. (`started`-age is a safe proxy: crews cap at 8 steps / minutes, so a non-running job started >24 h ago is long done. No `finished_at` field needed.)
  - Unparseable/missing `started` → **keep** (never lose data on a bad field).
- `routes/agents.py` run endpoint: call `_evict_stale_agent_jobs()` first, then add the new job **under `_agent_jobs_lock`**. Worker-thread value mutations (`progress.append`, `["status"]=…`) don't change dict *structure*, so they need no lock; only the add (new key) + evict (del key) are structural and are now lock-guarded. Snapshot iteration is the belt-and-suspenders against the size-change race.

## 2. H-6 — `_pending_approvals` (remote command approvals)

`GET /api/approvals` marks entries `status="expired"` after 120 s but **never deletes** them, so the dict accumulates hundreds of dead entries; every list/count iterates + serializes them.

**Fix (`routes/_shared.py` + `codec_dashboard.py`):**
- `_evict_expired_approvals(now=None, ttl_seconds=120)` (caller holds `_approval_lock`): delete every entry whose `now - timestamp > ttl` **regardless of status** (pending→expired→gone, allowed/denied→gone after 120 s). Snapshot-iterate the keys to delete.
- Call it at the top of both `list_pending_approvals` and `pending_approval_count` (inside their existing `with _approval_lock:`), so whichever endpoint the PWA polls keeps the dict swept.
- **Behavior delta (acceptable):** a click on an approval that expired >120 s ago now returns 404 ("not found") instead of 409 ("already expired"). A 2-minute-stale command approval is re-issued by the user either way; the audit explicitly recommends deletion.

## 3. M-6 — `VoicePipeline._resumable_sessions` (dropped voice sessions)

`_save_for_resume` stashes `{session_id: messages}` + prunes entries older than `_RESUME_TTL` (600 s) — but only **at the next save**. A dropped-and-never-reconnected session with no subsequent voice activity sits until dashboard restart.

**Fix (`codec_voice.py`):**
- Promote `_resume_timestamps` to a class attribute (was lazily created) so it always exists.
- Extract `@classmethod _prune_resumable(cls, now=None)` — drops `_resumable_sessions` + `_resume_timestamps` entries older than `_RESUME_TTL`.
- Call `_prune_resumable()` in **`__init__`** (every new/resumed session) in addition to `_save_for_resume`. Also pop `_resume_timestamps` on the resume path (line 393) so the two dicts can't drift.
- **Deviation from the audit's "threading.Timer every 60 s":** a background timer adds lifecycle complexity (daemon thread, cancellation, test flakiness) for a leak the audit itself calls "minor… bounded by usage pattern." Pruning on `__init__` + `save` evicts on *any* voice activity; the only un-evicted window is total voice inactivity, during which the small `messages` lists pose no real pressure and are swept the instant activity resumes. Simpler, testable, no thread to leak.

## 4. Test plan (`tests/test_bounded_dicts.py`)
All three modules import cleanly locally (`routes._shared`, `routes.agents`, `codec_voice` verified), so these are real unit tests, not source-invariants:
- **H-4:** `_evict_stale_agent_jobs` drops a `complete` job dated 25 h ago; keeps a `running` job (any age), a `complete` job dated 1 h ago, and an entry with a malformed `started`. Snapshot iteration doesn't raise when the dict is mutated.
- **H-6:** `_evict_expired_approvals` deletes a 200 s-old entry (pending/allowed/denied alike); keeps a 30 s-old one; handles missing `timestamp` (treated as epoch → deleted, or kept — pin the chosen behavior).
- **M-6:** `VoicePipeline._prune_resumable` drops an entry whose timestamp is `>_RESUME_TTL` ago, keeps a fresh one; constructing a `VoicePipeline` (mock websocket) prunes stale entries; the resume path removes both the session and its timestamp.
- **Regression:** full suite — exactly the 41 known-baseline failures, **zero new**. `ruff` per-file delta vs `origin/main` clean for every touched file.

## 5. Risk + rollback
- **Blast radius:** two new helpers + one lock in `routes/_shared.py`; one evict-call + lock in the agents run endpoint; two evict-calls in dashboard approval endpoints; a classmethod + `__init__` call + class-attr in `codec_voice`. All additive; no schema, no API shape change, no on-disk state. The only observable behavior change is the H-6 404-vs-409 on a >120 s-stale approval.
- **Concurrency:** structural dict mutations (add/del keys) are lock-guarded; snapshot iteration removes the size-change race. Value mutations by worker threads are unchanged.
- **Deferred:** H-5 (`_RATE_WINDOW`) → PR-4G-2 (needs an importable test path for `codec_mcp_http`). H-7 (observer per-poll tempfile leak on *timeout*) is a separate, complementary fix to the PR-4A-2 shutdown glob-purge — also out of this PR's scope.
- **Rollback:** single-commit revert; no persisted state to migrate.
