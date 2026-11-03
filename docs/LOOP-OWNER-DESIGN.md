# One owner per background loop, and a memory-pressure alert (P-083)

## What and why

Three loops could run twice and clobber each other's files, and nothing warned
while the 64 GB Mac swapped and chat slowed down.

| Loop | Who ran it | What went wrong |
|---|---|---|
| `codec_heartbeat.heartbeat()` | codec-dashboard every 30 min, and a hand-added PM2 app `codec-heartbeat` every 20 min | probes, auto-restarts, the Gmail check and the alert counters ran twice |
| `~/.codec/alert_state.json` | `codec_alerts.check_services_and_alert` and `codec_heartbeat.check_alerts` | each loaded the file at its start and wrote it whole at its end, with probes and restarts (20 s or more) in between, so one wiped the other's counters |
| `~/.codec/draft_task.json` | the dashboard's `_bg_watcher` and PM2 `codec-hotkey` (`codec_watcher.py`) | both saw the file, both read it, both drafted and pasted |

(`codec-heartbeat` was not in `pm2 jlist` or `~/.pm2/dump.pm2` on 4 Nov 2026,
but nothing stops it coming back; the guard below makes that harmless.)

## Changes

**Heartbeat: one run at a time on the Mac.** `heartbeat()` is now a guard
around the old cycle (`_heartbeat_cycle()`):

1. It takes a non-blocking `flock` on `HEARTBEAT_LOCK_PATH`
   (`~/.codec/heartbeat.lock`, 0600). Held by another run (any process or
   thread): log one line and return `None` at once.
2. It reads `HEARTBEAT_STAMP_PATH` (`~/.codec/heartbeat_last.json`,
   `{"started_at": <epoch>, "pid": <int>}`). A run that started less than half
   of `HEARTBEAT_INTERVAL_S` (30 min, codec-dashboard's loop, the owner) ago:
   return `None` at once. A missing, broken or future stamp does not block.
3. It writes a new stamp (atomic, 0600), runs the cycle, releases the lock.

`heartbeat(min_gap_s=0)` (used by `python3 codec_heartbeat.py once`) keeps the
lock and skips only the stamp check. If the lock file cannot be opened the
cycle runs as before, with a warning: never no heartbeat at all. The dashboard
loop and `run_daemon` are unchanged.

**alert_state.json through `codec_jsonstore.read_modify_write`.** New pure
helper `codec_jsonstore.merge_changes(current, base, new)`: applied to the file
as it is now, under its lock, it writes only the keys this run changed against
`base` (the state it loaded), removes only the keys this run removed, and goes
into nested dicts key by key. `codec_alerts._save_state(state, base=None)` uses
it (without `base` the whole state replaces the file, still under the lock);
`check_services_and_alert` passes the state it loaded. `check_alerts` does the
same on its own path. Long probes stay outside the lock.

**Draft task claimed by rename.** `codec_watcher.claim_draft()` renames
`TASK_FILE` to `TASK_FILE.claimed-<pid>-<random>` (one `os.rename`, atomic on
one volume), then reads and deletes the claimed file. Of two watchers exactly
one gets the task; the other finds nothing. `codec.py` writes the file in place,
so a claim made mid-write may see a short file: the claimed copy is re-read for
up to 2 s (the writer's open handle still fills it). Both watchers use it; the
dashboard calls it in a thread.

**Memory check.** `codec_heartbeat.memory_check(procs)`, run each heartbeat
before the daily status:

- `sysctl vm.swapusage` gives swap used and total (`swap_pct`);
- `memory_pressure -Q` gives the free percentage (shown, never alerted on);
- `footprint -p <pid> -f bytes --noCategories` gives `phys_footprint` of the
  model server, whose pid is the online PM2 app `model_pm2_process` (default
  `qwen3.6`) in the heartbeat's own `pm2 jlist`; the active model's size is
  `extra_models[].size_gb` or its weights on disk (`codec_models._snapshot_bytes`).

Each tool runs through `subprocess.run` with a 10 s timeout. A tick is high
when swap is above 80% or the footprint above 1.5x the model size
(`config.json:memory_alert.swap_pct` / `.footprint_ratio`, bounded 50-100 and
1.1-5). No pid, no footprint output or no model size makes that figure
`unknown`, which never alerts. Two high ticks in a row raise
`alert_once("mem_pressure", "warning", ...)`; a tick with a known figure and
nothing high resets the count and sends `alert_resolved`. The reading and the
count live in `MEMORY_STATE_PATH` (`~/.codec/memory_check.json`, 0600, written
with `read_modify_write`). `GET /api/status` adds `memory` from that file
(`codec_heartbeat.memory_status()`; `{"level": "unknown", "checked_at": null}`
before the first reading). Messages carry figures only.

No audit envelope change, no new audit event, no memory.db change, no PM2 or
ecosystem change.

## Tests

`tests/test_loop_owner.py`: two threaded `heartbeat()` calls run the cycle once
(counter), a fresh stamp skips and an old one runs; two threads writing
alert_state.json keep both counters; two threads claiming one draft run it
once, and a claim mid-write still reads the task; memory parsers on fixture
outputs of the three tools; `mem_pressure` after the second high tick only
(swap and footprint), never on `unknown`, resolved once; `/api/status` shows
the figure. `tests/conftest.py` points the three new paths at a temp folder and
clears the stamp and memory state before each test, so no test writes
`~/.codec` and one test's stamp never skips the next test's heartbeat.

## Rollback

Revert the PR. The three new files in `~/.codec` (`heartbeat.lock`,
`heartbeat_last.json`, `memory_check.json`) are read by nothing else and can
stay or be deleted. To quiet only the memory alert, set
`config.json:memory_alert.swap_pct` to 100.

## Not decided here

Removing a returning PM2 `codec-heartbeat` entry is `pm2 delete`, the owner's
word. Whether `codec-autopilot` (enabled false, no triggers) stays in PM2 is a
question for the owner.
