# Voice and vision use the model the server holds (P-013)

Status: approved for build 2026-11-03 (owner: "go go build i trust ur judgment here").

## What

1. **Voice follows the loaded model.** A voice call names `config.json:llm_model`, the hub id the
   model-server launcher preloads (`scripts/start_model_server.sh`, unchanged), or, while chat is on a
   registered cloud model, the local model in `llm_local_restore` (`codec_cloud_models.local_only`).
   `config.json:voice_model` still pins another model on purpose. With no config at all the fallback is
   `codec_models.DEFAULT_MODEL` (the bundled 7B), no longer the 35B.
2. **Vision follows it too when it has no model of its own.** With no `vision_model` and a vision server
   that is the chat model's server, `codec_vision` names the loaded model. A pinned `vision_model`, or a
   separate vision server, is used as before.
3. **The dashboard's vision warm-up sends nothing** when the vision model it would warm differs from the
   chat model on the same server (one server holds one model; the warm-up was unloading the chat model at
   every dashboard start).
4. **One `model_swap` audit line** (warning) at the start of a voice call that names another model than
   the local server reports in `GET /health` (`loaded_model`).

## Why

On a buyer Mac with only the bundled 7B, live voice asked for the 35B, which is not there. On the Studio,
each voice call or dashboard restart that named another model made the server unload the one it held:
7 swaps on 25 Oct, 23 in the 21-23 Oct log, 13 failed requests, one Metal out-of-memory, one 361.9 s prefill.

## API and data

- `codec_models.local_server_model(cfg) -> (base_url, model)`: what the local server holds, by config.
- `codec_models.same_server(a, b) -> bool`: host and port; `localhost`, `127.0.0.1`, `::1`, `0.0.0.0` are one host.
- `codec_models.vision_model(cfg, vision_base_url, default) -> str`: rule 2.
- `codec_models.health_model(base_url) -> str | None`: `loaded_model` from `/health` on a local server
  only, and only when it is a plain hub id (`org/name`, at most 200 characters, no path). Used only to
  compare: no caller ever sends this value. The id sent is always the config string, byte for byte.
- `codec_models.note_swap(model_id, base_url, caller, correlation_id) -> bool`: writes the line.
- Audit event `model_swap`: existing envelope, source `codec-models`, outcome and level `warning`,
  extra `caller` (`voice`), `requested`, `loaded` (model ids only), plus the voice session's correlation id.
  No schema change.
- No config migration. The owner sets `"voice_model": "mlx-community/Qwen3.6-35B-A3B-4bit"` on the Studio
  himself so voice keeps the 35B if chat moves to another model (#372; D-36 keeps the 35B as the default
  for voice and chat there).

## Out of scope

The launcher, the switch code, `config.json` writes, the other vision call sites (chat image upload,
webcam, bridges, `/api/vision`: the `resolve(role)` item), and a lease between processes.

## Test plan

`tests/test_voice_vision_loaded_model.py` with a fake `/health` and fake LLM endpoint (no real server,
no model load): resolver follows `llm_model` byte for byte and keeps a pinned `voice_model`; vision rule;
warm-up sends nothing on a different model on the same server and still warms a separate server;
`health_model` format check; `note_swap` writes exactly one line, and none when equal or unreadable; the
voice session start notes the swap and skips only the probe while an image job runs.
`tests/test_cloud_models.py:359` now expects the env's `llm_model`.

Manual, after the owner's `codec-dashboard` restart: one voice turn and one screen request on the Mac Air
with only the 7B; on the Studio, `grep -c 'New text_generation model requested' ~/.pm2/logs/qwen3.6-error.log`
unchanged 10 minutes after a restart plus a voice call.

## Rollback

`git revert` the PR and restart `codec-dashboard`. Put the previous `voice_model` back (or remove it) in
`config.json` if it was changed. The Studio's pinned 35B is unchanged throughout.
