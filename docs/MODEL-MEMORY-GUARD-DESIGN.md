# Model server memory guard (17 Nov 2026 incident)

The owner approved a memory guard for the local model server on 18 Nov 2026, and the
four decisions at the end on the same day ("yes 1 2 3" for decisions 2-4). Built: the
test-suite port block (decision 1, #624), the model pin (decision 2, option B) and the
guard (decisions 3 and 4, `codec_model_guard.py`). Built beyond this note: the guard
also only alerts while chat runs on a cloud model (`cloud_active`: a restart would
wait on the cloud host's port) and after a failed restart (`restart_failed`), and
`config.json:model_pin` false starts the server unpinned.

## What happened

On 17 Nov 2026 the model server (PM2 `qwen3.6`, `mlx_vlm.server` 0.6.13 on :8083,
`Qwen3.6-35B-A3B-4bit`, normal size 20.4 GB) grew to 69.7 GB at 17:30 and 88.4 GB at
17:49 (WindowServer spin reports `WindowServer_2026-11-17-173059_*.spin` and
`-174913_*.spin`, process `Python [1746]`, running since boot). Swap reached 96.7%.
macOS killed WindowServer three times (17:30, 17:31, 17:49), which logged the owner
out each time. He stopped the server by hand.

The heartbeat's memory check saw it once: at 17:36 it read 85.1 GB, 4.17x the model,
level high, `high_ticks` 1. It alerts only after two high ticks, and ticks are 30
minutes apart. At the next tick (18:06) the server was down, its footprint unknown and
swap at 73% (under the 80% line), so the count went back to 0. No alert was sent.

Also on the Mac then: three virtual machines (~14 GB) and a Python process writing
34 GB to disk. Swap was already 76.6% at 17:06, while the server was still 21.4 GB.

## Why it grew

### Certain: the server swapped models in place, eight times

The server's own log (`~/.pm2/logs/qwen3.6-error__2026-11-18_00-00-00.log`, which
holds 17 Nov) shows it. `mlx_vlm.server` loads whatever model a request names. A
request naming a model other than the loaded one makes it unload the current model
(`_unload_model_cache_group`: `gc.collect()`, `mx.clear_cache()`) and load the new
one. There is no option to pin the model. Requests that day named four ids:

| Requested id | Where that id comes from in CODEC |
|---|---|
| `mlx-community/Qwen3.6-35B-A3B-4bit` | the owner's `config.json:llm_model` |
| `mlx-community/Qwen2.5-7B-Instruct-4bit` | `codec_models.DEFAULT_MODEL`, used when config.json has no `llm_model` |
| `local-qwen` | `codec_compare.default_endpoints()` and `codec_email_triage`, same fallback |
| `""` (empty) | `cfg.get("llm_model", "")` in `codec_compaction` and several others |

Sequence (times from the log):

- 16:56:53 a request with model `""`: the 35B is unloaded, loading `""` fails.
- 16:57:33 and 16:57:49 `local-qwen`: not on the Hub, fails.
- 16:57:52 the 7B: never on this Mac before, so the server downloaded it (4.28 GB
  written to the Hugging Face cache from 16:57:54 to 17:23:31). Requests queued
  behind the download for 26 minutes.
- 17:23:34 to 17:25:02: six loads in 88 seconds (7B, 35B, 7B, 35B, 7B, 35B), each
  after an unload; at 17:25:21 a `""` request unloaded the last one.
- 17:27:54 loading the 35B again; at 17:32:15 it failed with
  `[METAL] Command buffer execution failed: Insufficient Memory`.
- 17:32:21 to 17:49:33: the next 35B load took 17 minutes (swap at 96.7%); two
  seconds later a `""` request unloaded it again.

Totals: 8 unloads, 7 successful loads, 1 load out of memory, 30 requests in flight at
the peak, 10 requests failed. Requests whose callers had long given up stayed in the
queue (`MLX_VLM_TOKEN_QUEUE_TIMEOUT` is 600 s; one prefill finished after 1,595 s),
so the swaps went on for 50 minutes after the last caller sent anything.

The unloads did not give the memory back. At 17:06, ten minutes after the 35B was
unloaded and with no model loaded, the heartbeat read 21.4 GB. Each later load added
to it. The launcher's own header already records this: "swapping models in-place
leaks, and repeated in-place switching grew the server to 52 GB against a 19 GB
model". The 528 earlier heartbeat readings in the dashboard logs range from 18.5 to
22.2 GB (median 21.0); none is above 26 GB except the 85.1 GB of 17 Nov.

The same swap also happens in normal use: `skills/mouse_control.py` sends its
screen step to `ui_tars_base_url` (default the same :8083 server) with
`ui_tars_model` (UI-TARS 7B), so each use unloads the chat model and loads UI-TARS
in place. It was not used on 17 Nov.

### Likely: the requests came from the test suite run on this Mac

How sure: high on the mechanism above, medium-high on the sender.

- #617 (merged 17 Nov 11:32) makes the test suite read an empty config
  (`CODEC_CONFIG_PATH` points at a missing file). The three foreign ids are exactly
  what CODEC code falls back to when config.json has no `llm_model`, and the default
  base URL is `http://localhost:8083/v1`, the live server. The suite has no guard
  against connecting to it.
- One test that does this: `tests/test_full_product_audit.py::TestCompaction::
  test_compaction_long_context_no_crash` calls `codec_compaction.compact_context` on
  30 messages, which asks the model for a summary with `model: ""`. With the owner's
  config (before #617) the same call sent the 35B id; the 209-token, `max_tokens=200`
  requests in the log fit that summary (my inference from the prompt size and the
  200-token cap).
- `tests/test_dashboard.py` calls the live dashboard's `/api/memory/search`,
  `/api/memory/sessions`, `/api/forge` and `/api/save_skill`, in that order. The
  dashboard log shows that exact group of `AUTH REJECTED` lines at 16:41:27,
  16:55:16, 16:56:46, 17:06:35 and 17:17:01: five test runs. The first model requests
  came 5 to 7 seconds after a group (16:41:27 then 16:41:32; 16:56:46 then 16:56:53).
- Nothing else in CODEC was active: no chat, Vibe, crew, skill or agent line in the
  audit log from 16:30 to 17:56; the heartbeat's memory line gives the memory
  database's newest entry as 11 Nov.
  The other PM2 apps that logged in that window do not call the model server.

Not pinned down: which test sent each request, and whether the four parallel
`max_tokens=4000` requests of about 425 tokens were one test or several sessions
running the suite at once (no parallel test runner is installed). Guess: several
sessions.

### Ruled out: KV cache, prompt cache, concurrency, long outputs

- The model has 10 full-attention layers (2 key/value heads of 256) and 30
  linear-attention layers. Its KV cache is about 20 KB per token; the linear layers
  keep a fixed state of roughly 63 MB per sequence (assuming 32-bit state). Even 30
  sequences of 8,000 tokens would hold about 7 GB. The longest prompt that day was
  3,531 tokens, and most queued requests never started decoding.
- Every prefill logged `cached_tokens=0`: the prompt cache held nothing.
- The server runs with no `--max-kv-size`, no `--max-num-seqs` (unbounded) and the
  default `--max-tokens`; every CODEC caller sends its own `max_tokens`.

None of these explains +68 GB. The swaps do.

### Side finding: the restart loop after the logouts

From 17:55 to 18:10 every start of `qwen3.6` failed with
`[metal::Device] Unable to load kernel arangeint32`, about every 16 seconds (43
restarts by the 18:06 heartbeat), until it stopped at 18:10 (most likely PM2's
restart cap). The heartbeat's own auto-restart at 18:06 did not bring it back either. The server stayed down until 18 Nov 11:37.
Guess: processes started before the logout lost the GPU after WindowServer died. Out
of scope here (a known-issues entry); the guard below never loops on a restart that
does not come back.

## Prevention at the source

| Option | What it stops | Cost | Proposed |
|---|---|---|---|
| **A. The test suite cannot reach the live servers.** `tests/conftest.py` refuses connections to the model server and dashboard ports; tests that need them carry a `live` marker and run only when asked. | the sender of 17 Nov | the live checks (`test_dashboard.py`, parts of `test_full_product_audit.py` and the like) skip by default; no runtime change | yes, decision 1 |
| **B. Pin the model in the server.** The launcher starts `mlx_vlm.server` through a small wrapper that answers 409 to a chat request naming any model other than the one it loaded, instead of unloading it and loading (or downloading) the other. | every in-place swap from any caller (tests, other apps, a typo, mouse_control) and the server downloading unknown ids | changes `scripts/start_model_server.sh` (a don't-touch file); the wrapper depends on mlx_vlm 0.6.13 internals, so it checks them at start and runs unpinned with a warning if they changed; mouse_control's UI-TARS step gets a 409 until UI-TARS has its own server (a `vision_model` other than the chat model on the same server would too; today they are the same); a model switch whose restart fails can no longer fall back to switching in place (it reports the failure instead); one `qwen3.6` restart by the owner | yes, decision 2 |
| C. Every CODEC caller sends the configured id (no `""`, no `local-qwen`). | CODEC's own placeholder ids | about a dozen files; does not stop tests or other apps; `DEFAULT_MODEL` stays (it is the buyer app's default) | follow-up |
| D. Cap concurrent sequences (`--max-num-seqs`). | memory of a large burst | requests over the cap wait; not the cause here (about 7 GB at worst) | no |
| E. Cap the KV cache (`--max-kv-size`). | long-context growth | the model forgets the start of long prompts (Vibe agent 10k, chat 12k budget); not the cause | no |
| F. Shorter token-queue wait (`MLX_VLM_TOKEN_QUEUE_TIMEOUT`). | requests whose callers left being served late | a real request behind a long prefill fails sooner; B makes those requests harmless | no |
| G. Offline Hub (`HF_HUB_OFFLINE=1`). | the server downloading a model a request names | a configured model missing from the cache fails at start instead of being fetched; B covers it | no |

A and B remove the cause. The guard below is still needed: a leak with another cause
would grow the same way, and on 17 Nov nothing noticed for an hour.

## The guard

### Where it runs

A background task in `codec-dashboard`, every 60 seconds, next to the heartbeat and
the other background services. New module `codec_model_guard.py` (standard library,
plus the existing helpers below); the dashboard calls its `tick()` in
`asyncio.to_thread`, never on the event loop (P-016). A non-blocking `flock` on
`~/.codec/model_guard.lock` makes it one guard per Mac: a second dashboard (a
worktree copy on another port) skips its tick.

Not the heartbeat: its 30-minute cycle is too slow (on 17 Nov the server went from
normal to 70 GB in about six minutes) and its probes take up to a minute. Not
`codec-watchdog`: it reads RSS (wrong for MLX), skips PM2 processes, stops processes
instead of restarting them, and does not ship in the app (`DEV_ONLY`). Not a new
PM2 app or LaunchAgent: a new service on two supervisors for a one-minute reading.
If the dashboard is down the guard is too, like the heartbeat.

### What it reads

Each tick, read only:

- the model server's pid and start time: `codec_heartbeat._pm2_jlist()` (0.16 s
  measured; on launchd it is `codec_supervisor.launchd_jlist()`), then
  `_model_server_pid`;
- its footprint: `footprint -p <pid> -f bytes --noCategories`, parsed by
  `codec_heartbeat.parse_footprint` (0.06 s measured on the 21 GB server; 10 s
  timeout);
- the model's size: `codec_heartbeat._model_bytes(cfg)`, kept per model id;
- the Mac's memory: `sysctl -n hw.memsize`, once;
- swap: `sysctl vm.swapusage`, for the message only.

About 0.25 s of CPU a minute. A figure it cannot read makes the tick do nothing.

### Three lines

Units as in the heartbeat's memory check (1 GB = 10^9 bytes; the Studio's 64 GB is
68.7 GB).

| Line | Rule | Studio (20.4 GB model) | 16 GB Mac, bundled 7B (4.3 GB) |
|---|---|---|---|
| Alert | `memory_alert.footprint_ratio` x model (default 1.5, the heartbeat's own line) | 30.6 GB | 6.4 GB |
| Restart | the smaller of `restart_ratio` x model (2.5) and `restart_ram_pct` of memory (70%), never below the alert line | 48.1 GB | 10.75 GB |
| Urgent | `urgent_ram_pct` of memory (80%), never below the restart line | 55.0 GB | 13.7 GB |

The restart line uses the Mac's memory, not free memory: a virtual machine or an
image job is not the server's growth, and restarting the server cannot give their
memory back.

### What it does

- **Alert line, one reading:** alert at once, `alert_once("model_memory", "warning",
  ...)`, and one `model_memory_high` audit line.
- **Restart line, two readings in a row (one minute apart):** restart the server,
  unless something below holds it.
- **Urgent line, one reading:** restart at once. Only a model switch in progress, the
  hourly budget, or the switches being off stop it.
- **Below the alert line again:** `alert_resolved("model_memory", ...)`.

The restart is `codec_models.restart_for_switch()`, the path model switches use:
under PM2 `restart_server()` (stop, wait until the port is free, start, wait until
the port opens), on launchd `restart_launchd()` (`launchctl kickstart -k` of exactly
`ai.avadigital.codec.qwen3.6`). The guard calls it inside
`codec_models._switch_lock(blocking=False)`: if a switch holds the lock it skips the
tick (the switch restarts the server itself), and while the guard holds it the cloud
auto-fallback (`ensure_llm_available`, same lock) cannot switch to the cloud during
the restart. After the restart it reads the footprint again and says both figures.

### What holds a restart, and what does not

| Situation | Holds the restart? | Why |
|---|---|---|
| a voice call (`~/.codec/voice_session.json` present) | yes, at most `max_defer_s` (120 s), not at the urgent line | a restart cuts the reply he is listening to; a logout cuts the whole call. The cap also covers a marker left behind by a crashed call. |
| a Vibe agent run (`codec_vibe_agent.live_run()` names a run; same process) | yes, same 120 s cap | a run step is about 20 s; a restart fails the step and the owner must Continue. Longer waits let the Mac swap: on 17 Nov the climb from the restart line to WindowServer's first kill took a few minutes. |
| server started less than 5 minutes ago (`codec_models.pm2_app_loading`, works on PM2 and launchd entries) | yes below the urgent line | a load in progress (P-059); restarting throws the load away |
| a model switch holds the switch lock | skip the tick | the switch is already restarting it |
| restart budget used (`restarts_per_hour`, 2) | yes; one critical alert | something keeps causing the growth; restarting again would hide it |
| an image job (`~/.codec/image_job.lock`, live pid) | **no** | the image runner is its own process; the restart returns memory the job needs and reloads the same model at the size the job's 36 GB free check counted. `set_active` refuses loads during a job because a switch can load a different, larger model; the guard never does. |
| a Project-mode agent or a crew mid-step | no | they resume from checkpoints by design; a failed model call is already handled |
| the server is not listening | not the guard's case | a down server is `codec_alerts`' auto-restart; the guard acts only on a listening server, so the two never both restart it |

A hold is written once (`model_guard_held`), not every tick. When the hold ends (call
over, run ended, two minutes passed) the restart happens at the next tick if the
footprint is still above the restart line.

### Never

- Never PM2 `max_memory_restart`, `pm2 restart` by hand, a kill, or any other restart
  path than `restart_for_switch()`.
- Never a restart while a switch holds the switch lock, more than `restarts_per_hour`
  times, or again after a restart whose server did not come back (critical alert,
  then alerts only until the dashboard restarts or the owner turns `restart` back on).
- Never touches the launcher, `config.json:llm_model`, the image runner, virtual
  machines or any other process.
- Never runs in the test suite (`tests/conftest.py` sets the kill switch and points
  both files at a temp folder; the restart function is replaced in every test).
- Never writes a model id, path, prompt or request text to the audit log, an alert or
  its state file: figures and reason codes only.

### Messages (figures only)

- Alert: "CODEC ALERT: the model server holds 36 GB, 1.8x its 20.4 GB model. This
  happens when something asks it for another model. CODEC restarts it at 48 GB."
- Restart: "CODEC: restarted the model server at 49 GB (2.4x its model). It holds 21
  GB again." (with "after waiting 2 minutes for the voice call" when held)
- Budget used or restart failed (critical): "CODEC ALERT: the model server is at 52
  GB and CODEC already restarted it twice this hour. Not restarting again; its log is
  in ~/.pm2/logs/qwen3.6-error.log."

### State, audit, config

State: `~/.codec/model_guard.json` (0600, `codec_jsonstore.atomic_write_json`): last
reading, readings above the restart line in a row, restart times of the last hour,
the current hold and its start. `GET /api/status` adds `model_guard` from it.

Audit, source `codec-model-guard` (rows for docs/codec-architecture.md, no envelope
change):

| Event | Level | extra |
|---|---|---|
| `model_memory_high` | warning | `footprint_gb`, `model_gb`, `ratio`, `ram_gb`, `swap_pct`, `line` (`alert`, `restart`, `urgent`) |
| `model_guard_restart` | info if ok, else warning | `result` (`ok`, `failed`), `supervisor`, `before_gb`, `after_gb`, `seconds`, `waited_s` |
| `model_guard_held` | warning | `reason` (`voice_call`, `vibe_run`, `starting`, `budget`, `restart_off`), `footprint_gb` |

On launchd `restart_launchd` also writes its own `model_server_restarted` line, as on
a switch.

Config, `config.json:model_guard`, read each tick (no restart needed); a wrong type
gives the default, a number outside its bounds is clamped:

| Key | Default | Bounds |
|---|---|---|
| `enabled` | true | |
| `restart` | true (false = alert only) | |
| `interval_s` | 60 | 30-300 |
| `restart_ratio` | 2.5 | 1.5-6.0 |
| `restart_ram_pct` | 70 | 50-90 |
| `urgent_ram_pct` | 80 | 60-95 |
| `max_defer_s` | 120 | 0-600 |
| `restarts_per_hour` | 2 | 1-4 |

The alert line stays `memory_alert.footprint_ratio` (1.5, bounds 1.1-5), so one
setting moves both the guard's alert and the heartbeat's. Kill switch:
`MODEL_GUARD_ENABLED=false` in codec-dashboard's environment (no reading, no file, no
alert, no restart). The 30-minute `memory_check` is unchanged.

### On the buyer app (launchd)

Same task, same lines (they scale with the Mac's memory and the model). The pid and
start time come from `codec_supervisor.launchd_jlist()` through `_pm2_jlist()`, the
restart from `restart_for_switch()` -> `restart_launchd()`. Nothing new to ship:
the module goes in the app with the dashboard. On a 16 GB Mac with the bundled 7B the
restart line is 10.75 GB.

### Replay of 17 Nov (estimate)

The footprint was 21.4 GB before 17:23:34 and 69.7 GB at 17:30:59; the minute-by-minute
figures were not recorded. Six loads happened between 17:23:34 and 17:25:02, so the
footprint crossed 30.6 GB and 48.1 GB inside that window or soon after. A guard reading
every minute would have alerted then and restarted about two minutes later, around
17:26-17:27: some four minutes before WindowServer's first kill. The restart also
drops the stale queue that kept the swaps going. If the tests had kept sending other
ids, the second restart would have been the last, with a critical alert; options A and
B are what stop that.

## Tests

`tests/test_model_guard.py`, about one test per behaviour, all on fixtures (no real
`footprint`, PM2, launchctl or server):

- the decision function on readings: 21 GB nothing; 31 GB one alert, once; 49 GB once
  no restart, twice restart; 56 GB restart at once; unknown footprint nothing;
- holds: voice marker holds then restarts after `max_defer_s`; a live Vibe run holds;
  an image lock does not hold; the start grace holds below the urgent line only; a held
  switch lock skips the tick; the third restart in an hour is held with one critical
  alert; a failed restart stops further restarts;
- the 17 Nov readings as a fixture series: alert and restart at the expected ticks;
- launchd: `restart_for_switch` is the only restart call (patched), never `pm2`;
- config: defaults, clamping, wrong types; the kill switch makes no subprocess call;
- audit lines and messages hold only the listed keys (no path, id or text);
- the dashboard task runs `tick` through `asyncio.to_thread`
  (`tests/test_no_blocking_requests_in_async.py` already checks this).

For A: a test that connecting to the model server or dashboard port from a test fails
unless the test is marked `live`. For B: the launcher dry run names the wrapper, and
the wrapper answers 409 to another id on a fake app.

Live check after deploy: a day with `model_guard.restart` false, reading
`~/.codec/model_guard.json` (figures only). The 409 of B is checked on a spare server
on another port with the small Qwen3-4B, never on the live one: if the pin were not
active, a test request with another id would unload the live model.

## Rollback

- Guard: `config.json:model_guard.enabled` false (read at the next tick) or `restart`
  false for alerts only; `MODEL_GUARD_ENABLED=false` needs a dashboard restart; or
  revert the PR. `~/.codec/model_guard.json` and `model_guard.lock` are read by
  nothing else and can stay or be deleted.
- A: revert the conftest change.
- B: revert the launcher commit, then restart `qwen3.6` (the owner's word).

## Decisions for the owner

1. **Block the test suite from the live model server and dashboard** (option A),
   shipped first as its own small PR. Recommendation: yes. It removes what sent the
   foreign model ids on 17 Nov, with no change to the running Mac.
2. **Pin the model in the server** (option B): the launcher starts mlx_vlm through a
   wrapper that refuses a request naming another model instead of loading it. It
   changes `scripts/start_model_server.sh`, needs one `qwen3.6` restart by you, and
   leaves mouse_control's UI-TARS step refused until UI-TARS gets its own server.
   Recommendation: yes. It stops the cause for every caller, not only the tests.
3. **Let the guard restart the model server by itself**, through the model switch's
   restart path, at most twice an hour: alert at 1.5x the model (30.6 GB on the
   Studio), restart at the smaller of 2.5x the model and 70% of memory (48.1 GB),
   at once at 80% of memory (55 GB). Recommendation: yes. Alerts alone would not have
   saved 17 Nov: the climb took minutes.
4. **What may delay a restart:** a voice call or a Vibe agent run, at most 2 minutes;
   an image job does not delay it; at 80% of memory nothing delays it.
   Recommendation: yes as written.

## Not in this change

- Option C (callers sending `""` and `local-qwen`), and `codec_models.DEFAULT_MODEL`
  as the fallback when config.json has no `llm_model`: follow-up.
- The Metal restart loop after a logout: a known-issues entry.
- A server of its own for UI-TARS (mouse_control), if decision 2 is yes.
- The 7B the server downloaded on 17 Nov (4.28 GB in the Hugging Face cache) stays
  until the owner says otherwise.
