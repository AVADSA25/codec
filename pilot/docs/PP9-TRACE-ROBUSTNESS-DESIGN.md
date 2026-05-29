# PP-9 — Trace-load robustness

**Closes:** Pilot audit **P-15** (`_from_dict` used `data["task"]`/`["run_id"]`/`["status"]`
+ `s["step"]`/`s["action"]` → `KeyError` 500 on a corrupt/hand-edited/truncated trace).
**Repo:** `~/codec/`.

## Fix
`trace._from_dict` now uses `.get(...)` with defaults for the top-level fields (task→"",
run_id→"", status→"unknown") and per-step (step→0, action→{}), so a partial trace degrades
gracefully instead of crashing the replay path.

## Tests (`tests/test_phase15_trace_robust.py`)
empty dict loads (no KeyError); a step missing `step`/`action` loads. 2 tests.
