# Runtime model switching

## What and why

Switch which local LLM CODEC answers with, from the chat UI or by voice, without
restarting anything. Everyday model for general use, a different one for coding.

## Key constraint: one model at a time, enforced by the server

Every local model is served by ONE `mlx_vlm.server` on `:8083`. It resolves the
model **per request** (`get_cached_model(openai_request.model)`) and keeps a
single-slot cache: asking for a different model logs
`New model request, clearing existing cache...`, unloads the current model, and
loads the new one.

That is the desired behaviour and it is automatic — **CODEC never holds two
models in memory.** Measured across a 3.6 → 3.5 → 3.6 cycle, the server process
stayed at 19.8 / 19.9 / 19.5 GB rather than accumulating.

Trying to keep two models resident (two servers) was measured and rejected: the
everyday model dropped from 59 to 23 tok/s and swap hit 18.5 of 19.4 GB.

Switching costs a load pause. Measured on this box: **6-9s** between models
already in the page cache, ~22s cold.

## Design

`codec_models.py`
- `discover_local()` — chat-capable models with real weights in the HF cache.
  Filters to `mlx-community/` (the only namespace this server can load), drops
  speech/vision-only/diffusion checkpoints, and skips metadata-only stubs by
  requiring ≥500 MB of `.safetensors`. Sizes come from the newest snapshot with
  blobs resolved, so shared revisions are not double-counted.
- `get_active()` — reads `llm_model` from `~/.codec/config.json`.
- `probe(model_id)` — 1-token request that forces the load and reports failure.
- `set_active(model_id)` — validate → write config → probe → **revert on
  failure**.

`routes/models.py` — `GET /api/models`, `POST /api/model`.

`skills/model_switch.py` — voice/chat ("switch to the coding model", "which
model are you using"). `SKILL_MCP_EXPOSE = False`: swapping the brain is a local
operator action, not something a remote MCP client should do.

UI — a picker in the chat composer; disabled with a "Loading…" toast during the
switch, since the pause is real.

## The safety property

A failed switch must never leave CODEC mute. Because the server has already
unloaded the previous model by the time a load fails, `set_active()`:

1. writes the new model to config,
2. probes it,
3. on failure restores the previous value **and re-probes it** so the working
   model is loaded again,
4. returns `ok: False` with the reason.

Verified live against a genuinely broken target: `Qwen3.8-27B` fails on the
current serving venv (`Unrecognized image processor` — it needs newer
`transformers`/`mlx_vlm`). The switch failed, reverted, and the model still
answered.

Only locally discovered models may be selected; an arbitrary string would trade
a working model for a 404.

## Scope

Chat already re-read config per request, so it needed no change. Voice read the
model at import, so its call site now resolves per call. Other callers
(`codec_watcher`, `codec_agent_plan`, …) keep the import-time constant
deliberately — background jobs should not change model mid-flight.

## Not included

Pre-warming a second model to make switches instant. That requires two resident
models, which this box cannot do without halving throughput.
