# PP-7 — Run concurrency guard + bounded run history

**Closes:** Pilot audit **P-9** (`_lock` declared but never used; concurrent autonomous runs
interleave on the single shared browser page) + the `_runs`-unbounded part of **P-14**.
**Repo:** `~/codec/` (Pilot).

## Fix
- `_assert_run_slot_free(run_id)` — `POST /run/{id}/start` refuses with **409** if another
  run is already executing on the shared browser (`_executing` tracks the active run_id;
  set when the bg task starts, cleared in its `finally`). A restart of the same run is
  allowed. Stops two runs corrupting each other's navigate/click/snapshot on one page.
- `_evict_old_runs(cap=50)` — `POST /run` drops the oldest runs (by `started_at`) so the
  in-memory `_runs` dict can't grow without bound.

## Tests (`tests/test_phase13_concurrency.py`, pytest, no browser)
second run rejected while one executes; slot free when idle; same run may restart; `_runs`
evicted to cap (oldest dropped, newest kept). 4 tests.
