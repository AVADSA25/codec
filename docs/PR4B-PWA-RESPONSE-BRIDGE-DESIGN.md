# PR-4B — PWA response bridge: kill `pwa_response.json`, correlate on DB rowid (C-2) (DESIGN)

**Status:** PROPOSED. Replaces the racy `~/.codec/pwa_response.json` file bridge with a DB-only path correlated on the `conversations.id` autoincrement (server-authoritative). Removes all 4 C-2 defects; closes a *latent* correlation race that naive file-removal would have exposed. Touches `codec_dashboard.py` (`/api/command` + `/api/response` + 1 new helper) and `codec_dashboard.html` (poll URL). New `tests/test_pwa_response_bridge.py`.

**Finding:** C-2 (`~/.codec/pwa_response.json` bridge — non-atomic write, no writer mutex, no request/response correlation id, racy mtime/unlink) [CRITICAL]. Audit's preferred fix: *"move the entire bridge to the `conversations` DB table and remove the file path entirely — the DB fallback already exists."*

---

## 1. Why the file exists and what it does today

`POST /api/command` (the PWA "Flash chat" send) does, in order:
1. **Synchronously** INSERT the user message into `conversations` (`session_id, ts, "user", task`), commit.
2. Schedule `asyncio.create_task(_process_command())` and immediately return `{"status":"processing", ...}` (no correlation id).
3. `_process_command` (background): try a skill, else call the LLM; then **(a)** `open(resp_file,"w")` writes `{"response", "task", "ts"}` to `~/.codec/pwa_response.json` (non-atomic) **and (b)** INSERT the assistant message into `conversations`.

`GET /api/response?session_id=&after=` polls:
- **File fast-path:** if `pwa_response.json` exists → parse it, and if `mtime` age > 10s `os.unlink` it, return its contents. (racy: read-vs-write, unlink-out-from-under-writer)
- **DB fallback:** `SELECT content FROM conversations WHERE session_id=? AND role='assistant' AND timestamp>? ORDER BY timestamp DESC LIMIT 1`.

### Verified caller inventory (whole repo)
- **`pwa_response.json`** is referenced **only** in `codec_dashboard.py` (the writer-clear at :910-916 + the writes at :981-982/:1008-1009, and the reader at :1110-1118). **No other process reads or writes it** — it is a purely *intra-dashboard* mechanism. Removing it cannot affect any other daemon.
- **`/api/response`** is polled **only** by the PWA (`codec_dashboard.html:957`), which **already sends `session_id` + `after`**. (`tests/full_test.py` hits it param-less only to assert the `response` field exists / auth — still satisfied.)
- **`/api/command`** is posted by the PWA (`codec_dashboard.html:932`, sends `session_id`) and by `codec_heartbeat.py:236` (fire-and-forget — checks `status_code`, **never polls `/api/response`**). Neither depends on the file.

**Conclusion:** the file is fully removable. The DB already carries both halves of every turn; the frontend already sends correlation params. The only question is the *correlation key*.

## 2. The latent race that makes naive removal unsafe

The existing DB fallback correlates on `after` — a **client wall-clock** string captured at `codec_dashboard.html:949` (`new Date().toISOString()`), set *after* the POST resolves. The assistant row's `timestamp` is a **server wall-clock** string. The query is `timestamp > after`. For that to match, the assistant's server time must exceed the client's `after`. Two ways it silently fails:

1. **Fast skill on a high-latency link.** `after` is captured one network round-trip *after* the server accepted the command; a skill answer (e.g. `time`, `calculator`) can be INSERTed in a few ms — *before* `after` if `RTT/2 > skill_time` (phone over Cloudflare: RTT 100-300ms). Then `timestamp > after` never matches → the PWA polls until its 5-minute timeout.
2. **Client/server clock skew.** Client clock ahead of server by even ~100ms ⇒ same miss.

Today the **file fast-path masks this** — the file is returned regardless of `after`. So *removing the file without fixing correlation would convert a masked latent bug into a live regression on the primary deployment (PWA over Cloudflare).* The fix must make correlation **server-authoritative**.

## 3. Fix — DB-only bridge, correlate on `conversations.id`

`conversations.id` is `INTEGER PRIMARY KEY AUTOINCREMENT` (`codec_memory.py:62`), indexed by `session_id`. It is monotonic and assigned server-side. The user row for a turn is INSERTed synchronously when the command is accepted; the assistant row is INSERTed later → **always a strictly higher id**. So "this turn's reply" = *the first assistant row whose `id` exceeds the user row's id*. No clock anywhere.

### 3a. New pure helper (the testable core)
```python
def _latest_response_for_session(db, session_id, after_id="", after_ts=""):
    """Newest assistant reply for this turn, or None.

    Correlation is server-authoritative via conversations.id (`after_id` =
    the user row's autoincrement id, returned by /api/command). The turn's
    assistant row always has id > after_id, so `id > after_id ORDER BY id ASC
    LIMIT 1` selects the immediate next assistant reply — zero client-clock
    dependence, exactly correct for the dominant single-tab + sequential
    flows. `after_ts` is a backward-compat fallback (legacy timestamp query)
    for an un-refreshed PWA tab that predates after_id. Never raises."""
```
- `after_id` path: `WHERE session_id=? AND role='assistant' AND id>? ORDER BY id ASC LIMIT 1`.
  - **ASC, not DESC** — the *immediate next* assistant after the request id. For the realistic flow (one tab; or sequential turns; or a fresh session) this is the turn's own reply. In the rare *simultaneous interleave* of a shared `flash-<date>` session across two tabs (userA, userB, then asstA, asstB), strict per-tab attribution would need a reply→request id column (deferred — a `conversations` schema change, a §10 don't-touch zone). Even there, DB+ASC is strictly better than today's single file, which clobbers (defect #2) and hands both tabs one randomly-surviving answer. ASC is the proper, no-schema-change fix for C-2 defect #3.
- `after_ts` fallback path: keeps today's `timestamp > ? ORDER BY timestamp DESC LIMIT 1` verbatim (no behavior change for old clients).
- Empty/None markers → `None`. Any exception → `None` (matches today's defensive reader).

### 3b. `/api/command`
- Capture the user-row id: `req_id = c.execute(INSERT user…).lastrowid` (after commit).
- **Delete** the resp_file clear (:910-916) and both file writes (:981-982 success, :1008-1009 error).
- **Error path now persists to the DB** instead of the file: INSERT an `assistant` row with `f"Error: {e}"` so the frontend's poll still surfaces the error (today only the file carried it). Net improvement: errors now appear in chat history too.
- Return `{"status":"processing","command":task,"source":source,"request_id":req_id,"session_id":session_id}`. (`request_id` is the new correlation token; additive — existing keys unchanged.)

### 3c. `/api/response`
- Signature `get_response(session_id="", after="", after_id="")`. (`after_id` additive; `after` retained for legacy.)
- **Delete** the file fast-path (:1110-1118) entirely.
- Body = `ans = _latest_response_for_session(get_db(), session_id, after_id, after)` → `{"response": ans}` (or `{"response": None}`). Cache headers unchanged.

### 3d. Frontend `codec_dashboard.html`
- After `await r.json()`, capture `var afterId = data.request_id || 0;`.
- Poll URL gains `&after_id=` + `encodeURIComponent(afterId)` **and keeps** `&after=` + `afterTs` (so a new client against an old server, or vice-versa, still resolves during the PM2 restart window). New server prefers `after_id`.
- No other frontend change — it already reads only `pd.response`.

## 4. Defects closed
| C-2 defect | Closed by |
|---|---|
| 1. Non-atomic write | File gone; DB writes are transactional (WAL). |
| 2. No writer mutex (concurrent clobber) | Each turn = distinct rows w/ distinct ids; no shared file to clobber. |
| 3. No correlation id | `request_id` = `conversations.id`, server-authoritative; ASC selects the immediate-next reply per request. |
| 4. Racy mtime/unlink | No file, no mtime/unlink logic. |
| *(latent)* clock/RTT correlation miss | `after_id` is clock-free; legacy `after` kept only as fallback. |

## 5. Alternative considered (rejected)
**Per-request atomic file `pwa_response_{request_id}.json` (tmp+rename) + startup purge.** Closes defects 1-4 but *keeps a second source of truth* alongside the DB (the assistant row is already written either way), adds a temp-file GC job (ties into the H-7/8/9 tempfile-leak findings — more surface), and still needs a `request_id` round-trip. DB-only reuses infra that already exists end-to-end and deletes code. Not chosen.

## 6. Test plan (`tests/test_pwa_response_bridge.py`)
Pattern mirrors `tests/test_session_response_update.py` (tmp DB fixture + direct helper unit tests + source invariants — endpoint tests are auth-skipped in CI, so the helper is the unit under test):
- `after_id` returns this turn's assistant row; **excludes** rows with `id <= after_id`.
- **Sequential turns:** two user rows (ids A<B) each followed by its own assistant row → `after_id=A` returns A's reply, `after_id=B` returns B's reply.
- **ASC tie-break (locks the decision):** with two assistant rows both `id > after_id`, the *smaller* id (immediate next) is returned, not the newest.
- Returns `None` when no assistant row has `id > after_id` (reply not ready yet).
- **Legacy `after_ts` fallback** still returns the newest assistant after a timestamp when `after_id` is absent.
- Empty session_id / both markers empty → `None`; corrupt/closed db → `None` (never raises).
- **Source invariants:** `pwa_response.json` no longer appears in `codec_dashboard.py`; `/api/command` returns `request_id`; `/api/response` calls `_latest_response_for_session`.
- **Regression:** full suite — exactly the 23 known-baseline failures, **zero new**. `ruff` per-file delta vs `origin/main` clean.

## 7. Risk + rollback
- **Blast radius:** `codec_dashboard.py` (one helper + two handlers) + `codec_dashboard.html` (poll URL). No schema change (reuses existing `conversations.id`). No other daemon touches the removed file. `/api/command`/`/api/response` response shapes are additive (`request_id`, `after_id`); the PWA reads only `pd.response`.
- **Graceful deploy:** `after` (legacy) path retained, so an un-refreshed PWA tab keeps working against the new server during the `pm2 restart codec-dashboard` window; the file simply stops being produced (an old tab that *only* knew the file path would fall back to its `after` poll, which now hits the DB).
- **Rollback:** single-commit revert (restores the file writer/reader + drops `request_id`/`after_id`). No persisted state to migrate — `pwa_response.json` is ephemeral.
