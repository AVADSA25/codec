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

Switching costs a load pause. Measured on this box: **6-9s** between models of
the same family already in the page cache, **18-21s** switching across families
(3.6 <-> 3.8). Plan for ~20s of dead air if switching on camera.

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

Verified live against a genuinely broken target: `Qwen3.8-27B` used to fail with
`Unrecognized image processor` on the old serving venv. The switch failed,
reverted, and the model still answered — including once in the operator's own
hands (audit `model_switched` at 15:12, outcome=error, auto-reverted to 3.6).

That failure mode is now fixed rather than merely survivable (see *Serving venv*
below), but the revert path stays: any future model whose architecture the
serving stack does not know will fail the same way.

Only locally discovered models may be selected; an arbitrary string would trade
a working model for a 404.

## Serving venv

`~/codec-qwen36-test/start.sh` activates the venv that runs the model server.
It was moved from `~/codec-qwen36-venv` (transformers 5.5.4 / mlx_vlm 0.4.4) to
`~/codec-qwen38-venv` (transformers 5.15.0 / mlx_vlm 0.6.13), because the older
stack could not load `qwen3_5`-architecture models at all.

Verified on a separate port BEFORE the swap, then again after:

| | old stack | new stack |
|---|---|---|
| Qwen3.6-35B-A3B | ~50-59 tok/s | **61.7 tok/s** |
| Qwen3.8-27B | fails to load | **loads, 24.2 tok/s** |
| beat-8 riddle | correct | correct |

Rollback is one line in `start.sh` plus `pm2 restart qwen3.6`; a timestamped
`start.sh.bak-*` sits beside it.

**These venvs are invisible to Homebrew.** `brew` once autoremoved `python@3.13`
as an unused formula and broke the venv's interpreter symlink, taking the model
server down with `ModuleNotFoundError: mlx_vlm`. `python@3.13` is now installed
on-request so autoremove leaves it alone — do not uninstall it.

## Scope

Chat already re-read config per request, so it needed no change. Voice read the
model at import, so its call site now resolves per call. Other callers
(`codec_watcher`, `codec_agent_plan`, …) keep the import-time constant
deliberately — background jobs should not change model mid-flight.

## Not included

Pre-warming a second model to make switches instant. That requires two resident
models, which this box cannot do without halving throughput.
