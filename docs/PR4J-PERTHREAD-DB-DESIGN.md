# PR-4J — per-thread SQLite connections for `get_db()` (M-5) (DESIGN)

**Status:** PROPOSED. Replace the single global `_db_conn` shared across all FastAPI/worker threads with a per-thread connection via `threading.local()`, plus a registry so shutdown can close them all. Touches `routes/_shared.py` (the `get_db` definition) + `codec_dashboard.py` (the shutdown handler, which references `_db_conn`). New `tests/test_perthread_db.py`. **This is the last open finding in Audit C** — closing it completes the reliability wave.

**Finding:** M-5 (`_db_conn` is a single global SQLite connection shared across all FastAPI request threads) [MEDIUM, latent].

---

## 1. The defect
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
One connection is shared by every thread (dashboard request threads, `asyncio.to_thread` pool, agent workers). SQLite serializes per-connection, so concurrent reads queue behind each other; and a write on one thread + a read on another share the same connection's transaction state (latent correctness hazard). Currently latent (uvicorn runs a single worker), but fragile the moment a second client joins (mobile PWA, claude.ai over MCP HTTP).

## 2. Fix — `threading.local()` per-thread connection + registry
```python
import threading
_db_local = threading.local()
_db_conns: list = []                 # every connection created, for shutdown close
_db_conns_lock = threading.Lock()

def get_db():
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")     # WAL is per-DB (idempotent)
        conn.execute("PRAGMA busy_timeout=5000")    # busy_timeout is PER-CONNECTION
        conn.row_factory = sqlite3.Row
        _db_local.conn = conn
        with _db_conns_lock:
            _db_conns.append(conn)
    return conn

def _close_all_db_conns():
    """Close every per-thread connection (shutdown). WAL is checkpointed on
    close; remaining handles are reclaimed on process exit. Never raises."""
    with _db_conns_lock:
        for c in _db_conns:
            try:
                c.close()
            except Exception:
                pass
        _db_conns.clear()
    try:
        _db_local.conn = None
    except Exception:
        pass
```

Why this is correct + better:
- **Concurrent reads parallelize** — each thread has its own connection; WAL already allows N readers + 1 writer. Writes still serialize at the SQLite layer (WAL single-writer + the existing `busy_timeout=5000`), which is correct.
- **Per-connection transaction isolation** — fixes the latent hazard where one thread's uncommitted writes were visible to another via the shared connection.
- **`busy_timeout` is per-connection**, so it's (correctly) re-applied to every new connection; WAL/row_factory likewise.
- **Bounded count** — connections are per-thread; uvicorn's pool + `asyncio.to_thread`'s executor + the few daemon threads are all bounded, so `_db_conns` stays small.
- `check_same_thread=False` kept (defensive — harmless now that each thread uses its own connection).

## 3. Shutdown handler (`codec_dashboard.py`)
The `@app.on_event("shutdown")` currently does `for conn in (_shared._db_conn, _qchat_conn, _vibe_conn): conn.close()` then `_shared._db_conn = None`. Rework to:
```python
    import routes._shared as _shared
    global _qchat_conn, _vibe_conn
    _shared._close_all_db_conns()                 # M-5: closes all per-thread conns
    for conn in (_qchat_conn, _vibe_conn):        # these two are separate singletons
        if conn is not None:
            try: conn.close()
            except Exception: pass
    _qchat_conn = _vibe_conn = None
```
(`_qchat_conn`/`_vibe_conn` are dashboard-local singletons, untouched by this change.)

## 4. Test plan (`tests/test_perthread_db.py`)
`routes._shared` imports cleanly → real tests. Each test points `DB_PATH` at a tmp file and resets the thread-local + registry first (`_close_all_db_conns()`):
- **Same within a thread:** two `get_db()` calls on the test thread return the *same* object.
- **Different across threads:** a child thread's `get_db()` is a *different* object than the main thread's (the core fix).
- **Pragmas:** the connection reports `journal_mode == wal` and `busy_timeout == 5000`, and `row_factory is sqlite3.Row`.
- **`_close_all_db_conns`:** after creating connections on two threads, it empties the registry and a subsequent `get_db()` returns a fresh connection.
- **Source invariants:** `get_db` uses `threading.local()` and no longer keeps a single `_db_conn` module global reassigned inside it; `codec_dashboard`'s shutdown calls `_close_all_db_conns(`.
- **Isolation:** full suite — exactly the 41 baseline failures, **zero new** (every dashboard/agents/memory test that uses `get_db` must stay green). `ruff` per-file delta vs `origin/main` clean.

## 5. Risk + rollback
- **Blast radius:** `get_db()` body (one function, callers unchanged — they still just call `get_db()`) + the dashboard shutdown handler. The behavior change is per-thread connections instead of one shared — strictly more correct + more concurrent; single-threaded callers (incl. the entire single-thread test suite) see identical behavior (one cached connection per thread).
- **Latent fix:** no current production behavior changes (single uvicorn worker → one request thread today); this hardens for the multi-client future the audit calls out.
- **Rollback:** single-commit revert (restores the `_db_conn` global + the original shutdown loop). No schema/state change.
