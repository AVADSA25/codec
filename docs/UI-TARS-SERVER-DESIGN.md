# UI-TARS server on demand

Status: design, waiting for the owner's word. Date: 20 Nov 2026.

## What is wrong today

`mouse_control` ("click the Send button") takes a screenshot and asks UI-TARS-1.5-7B
where the element is. It sends that request to the main model server on 8083
(`ui_tars_base_url`, default `http://localhost:8083/v1`).

- Since 18 Nov (#625) the main server holds one model and answers 409 to a request
  naming another one. Mouse control gets no answer and reports that it found nothing.
- Before 18 Nov the same request made 8083 unload Qwen3.6 and load UI-TARS in place,
  and the next chat request loaded Qwen3.6 again. Those swaps are the leak that took
  the server to 88 GB on 17 Nov (docs/MODEL-MEMORY-GUARD-DESIGN.md).

So UI-TARS needs a server of its own.

## Facts that decide the shape

| Fact | Value |
|---|---|
| `mouse_control` runs in the audit logs since 21 Oct (30 days kept) | 0 |
| UI-TARS model on the Studio | `mlx-community/UI-TARS-1.5-7B-6bit` (`config.json:ui_tars_model`), 6.4 GB on disk |
| Memory it holds when loaded | about 7-8 GB (to be measured in the live check) |
| Mac Studio | 68.7 GB; Qwen3.6 holds 21.1 GB; swap 81% on 20 Nov |
| Image jobs | need 36 GB free (`codec_image`, `min_free_gb`) |
| Buyer app | does not ship UI-TARS (`packaging/macos/models.json`) |
| Free port | 8087 (nothing in the repo, the tunnel config or `lsof` uses it) |

An always-on server would hold about 7 GB for a skill that has not run in a month, and
would make image jobs refuse more often. A server that starts when mouse control needs it
and stops by itself costs only a load wait on the first click of a task.

## Proposal: start on first use, stop after 5 idle minutes

### 1. `scripts/ui_tars_server.py` (new, about 80 lines)

- Starts `mlx_vlm.server` through `scripts/mlx_pinned_server.py`, so it is pinned to the
  UI-TARS model: a request naming any other model gets 409. A wrong request can never
  load a second model into it.
- Adds an idle timer. A small HTTP middleware on mlx_vlm's FastAPI app records the time of
  the last request and counts requests in flight. A thread asks uvicorn to shut down when
  no request has arrived for `idle_seconds` (default 300) and none is in flight.
- Hard stop after `max_seconds` (default 7200) whatever happens: a backstop, so a
  stuck timer can never leave 7 GB resident.
- If the middleware cannot be added (a newer mlx_vlm), the script exits at once with
  a reason on stderr. A UI-TARS server that never stops would be worse than none.
- Listens on 127.0.0.1 only, like the main server.
- Sets `HF_HUB_OFFLINE=1`: it never downloads. A missing model fails fast. It does not
  wait on the network the way the shared TTS server did.

### 2. `codec_ui_tars.py` (new, about 200 lines, standard library)

`endpoint(cfg) -> (url, None)` or `(None, message for the owner)`:

1. `config.json:ui_tars_base_url` set: return it and start nothing. This lets the owner
   point at a server they run themselves, as before.
2. `config.json:ui_tars.on_demand` false, or env `UI_TARS_ON_DEMAND=false`: return the
   old default `http://localhost:8083/v1`. This is the old behaviour, so it gets 409 today.
3. Otherwise, under an flock on `~/.codec/ui_tars_server.lock` (one starter at a time
   across codec-dashboard, open-codec and codec-mcp-http):
   - The state file `~/.codec/ui_tars_server.json` (0600, atomic) names a pid that is
     alive, and `GET /v1/models` on its port answers with the UI-TARS model: return its
     URL (reuse).
   - Refuse with a plain message, and start nothing, when:
     - the model is not in the local Hugging Face cache (`not_installed`, the buyer case);
     - an image job is running (`codec_image.busy_message()`, `image_job`);
     - free + inactive memory is below `min_free_gb` (default 10; `low_memory`);
     - the port answers but is not ours (`port_taken`). Nothing is ever killed.
   - Start: `subprocess.Popen` with `start_new_session=True` (it outlives the
     process that started it and stops itself). The command is the interpreter, then
     `scripts/ui_tars_server.py --model M --host 127.0.0.1 --port 8087`, then the idle
     and max limits.
     - The environment is `codec_envscrub.scrub(os.environ)` plus `HF_HUB_OFFLINE=1`.
     - stdout and stderr go to `~/.codec/logs/ui_tars_server.log` (0600).
     - The interpreter is chosen in the same order as `start_model_server.sh`:
       `CODEC_MODEL_VENV`, then the app's bundled Python, then `~/codec-qwen38-venv`.
   - Wait for the port for up to `load_timeout_s` (default 120). Then write the state,
     or kill only the process it started and report `load_failed`.
4. Audit: `ui_tars_server_started` (model, port, load seconds) and
   `ui_tars_server_refused` (reason code). Figures and codes only.

Settings live in `config.json:ui_tars`: `on_demand`, `port`, `idle_seconds`, `max_seconds`,
`min_free_gb` and `load_timeout_s`. Every value is clamped to a safe range.

### 3. Small edits

- `skills/mouse_control.py`: get the URL from `codec_ui_tars.endpoint()` at the first
  vision call of each task. On a refusal, return the message ("UI-TARS is not installed on
  this Mac", "An image is being generated", "Not enough free memory: N GB free, UI-TARS
  needs about 10"). The model id still comes from `codec_models.resolve("ui_tars")`.
  Regenerate `skills/.manifest.json`.
- `codec_dispatch.SLOW_SKILL_TIMEOUTS`: add `"mouse_control": 180`. A first click now
  waits for the load, and the chat and voice limit of 60 s would cut it off.
- `codec_doctor.CODEC_PORTS`: add `8087: "UI-TARS (on demand)"`.
- `tests/conftest.py`: add 8087 to `LIVE_PORTS`, so tests can never reach a real one.
- `packaging/macos/build_app.sh`: copy `scripts/ui_tars_server.py` next to
  `mlx_pinned_server.py` (same code in the app). The model is not shipped, so a buyer
  gets the `not_installed` message.

### Not changed

- The main server, its launcher (`scripts/start_model_server.sh`) and its pin.
- The memory guard. It watches the main server only; UI-TARS stops by itself.
- No PM2 app and no LaunchAgent. Nothing new to supervise or save in `dump.pm2`.

## Set aside

- **Always-on PM2 app on 8087:** holds about 7 GB all the time (see the facts above).
- **Let the loaded Qwen3.6 (it reads images) find the clicks:** no new process. But
  nobody has checked its click accuracy, and mouse control's prompts and coordinate
  parsing are written for UI-TARS. Worth a test later, as a change of its own.
- **Cookbook serve (8110-8119):** it runs text models (`mlx_lm`) under PM2, and its stop
  needs a confirmed request. Not suited to a vision model that should stop by itself.

## Tests (about one per behaviour, no real server, no real pm2)

- `ui_tars_base_url` set: returned, nothing started.
- The switch off: the old URL, nothing started.
- Refusals: model not cached, image job alive, low memory, port taken. One test each,
  with the message.
- A live state (fake pid alive, fake `/v1/models`) is reused and nothing is started.
- Start (fake `Popen`): the argv (loopback host, port, model, limits), a scrubbed
  environment with `HF_HUB_OFFLINE=1`, a new session, and the state written after the
  port answers.
- Load timeout: only the started pid is stopped, and the reason is `load_failed`.
- Two threads call `endpoint()` at once: one `Popen`.
- The idle decision in `ui_tars_server.py` (pure function): it stops after idle, and
  never while a request is in flight or before `idle_seconds`.
- `mouse_control` returns the refusal message, and takes its URL from `codec_ui_tars`.
- `SLOW_SKILL_TIMEOUTS["mouse_control"]` is 180; `LIVE_PORTS` holds 8087.

Live check (run by hand, `~/Documents/CODEC-private/live-checks/live_check_ui_tars.py`):

- Start the real server and time the load. Read its footprint.
- Ask for one click position on a saved screenshot.
- Check that another model id gets 409.
- Check that it stops after a short idle (`idle_seconds` 30 for the check).
- Check that a second start reuses the server, and that the image-job lock refuses one.

## Deploy and rollback

- Deploy: merge, then restart `codec-dashboard`, `open-codec` and `codec-mcp-http` so they
  load the new `mouse_control`. These are the owner's restarts. Nothing else changes until then.
- Instant switch back: `config.json:ui_tars.on_demand=false`.
- Full rollback: revert the PR. A running UI-TARS server stops by itself within 5 minutes.
