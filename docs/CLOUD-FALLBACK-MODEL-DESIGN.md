# Cloud fallback model (MiMo V2.6 Pro) — design

Status: approved 2026-09-27 and implemented (codec_cloud_models.py).

## What

A cloud model the owner can switch to by hand when the local Qwen server is
down. Picking it in the chat model picker moves chat, PWA voice, crews and
schedules to that model. Picking a local model moves them back. The model has
its own API key in the Keychain and a hard monthly spend cap.

First entry: Xiaomi MiMo V2.6 Pro. The API is OpenAI-compatible
(`POST https://api.xiaomimimo.com/v1/chat/completions`, model `mimo-v2.6-pro`,
`Authorization: Bearer <key>`). Checked 2026-09-27: the endpoint is live and
answers 401 `invalid_key` without a key. Price: $0.435 per million input
tokens and $0.87 per million output tokens. Billing is prepaid from the
account balance.

## Why

When `qwen3.6` is stopped (a memory-heavy job, a crash, a model that will not
load), CODEC has no model and every chat fails. The existing ways to connect a
cloud model do not work as a quick switch:

- The first-run "Connect your own" screen can point CODEC at MiMo. But
  switching back to local deletes the key (`codec_setup.set_provider("local")`
  stores an empty key), so every later switch means pasting the key again.
- Nothing sends `"thinking": {"type": "disabled"}`. MiMo then thinks by
  default: slower replies, and the thinking tokens are billed as output.
- The global `llm_kwargs` sends `chat_template_kwargs` (a local-server
  parameter) to the cloud.
- `codec_models.probe()` sends no key, so any switch to a cloud model fails its
  check.
- PWA voice pins `voice_model` (a local model id) and reads its config once at
  import, so voice never follows a switch.
- Voice sends the observer summary (window titles, OCR, clipboard) on every
  turn unless `vision_provider == "gemini"`. On a cloud model, that would send
  the screen summary to Xiaomi on every voice turn. Chat already gates this
  correctly, because it checks whether `llm_base_url` is local.
- There is no spend cap.

## How

1. **Registry: reuse `extra_models`.** An entry that has a `base_url` is served
   by that endpoint, not by the local server. The owner's entry lives in
   `~/.codec/config.json`, never in the repo:

   ```json
   {"id": "mimo-v2.6-pro", "label": "MiMo V2.6 Pro (cloud)",
    "base_url": "https://api.xiaomimimo.com/v1", "key_slot": "mimo_api_key",
    "kwargs": {"thinking": {"type": "disabled"}},
    "price_in_per_m": 0.435, "price_out_per_m": 0.87, "monthly_cap_usd": 10}
   ```

2. **Key: in the Keychain, never on disk or in git.** The key is stored as
   `ai.avadigital.codec.mimo_api_key`. The owner adds it with `security`, which
   prompts for the key, so it never passes through chat. CODEC never copies it
   into `llm_api_key`, so switching back to local cannot delete it.

3. **Switch: `codec_models.set_active()`.** It handles a cloud entry as follows:
   - It stores the current local `llm_base_url` and `llm_kwargs` in
     `llm_local_restore`.
   - It writes the entry's `base_url`, id and `kwargs`.
   - It does not restart PM2.
   - It sends a 1-token check request with the key. If the check fails, it
     reverts.

   Switching back to a local model restores the stored values, then runs the
   existing stop, start and check sequence. `llm_provider_mode` and
   `llm_verified_at` do not change, so the first-run screen does not appear.

4. **One check before each cloud call, in `codec_llm`.** `_cloud_blocked_msg`
   already imports a module on demand for every non-local `base_url`. The new
   check sits in the same place (new module `codec_cloud_models.py`) and runs
   in `call`, `stream`, `acall` and `astream`. For a `base_url` that matches a
   cloud entry, it does the following:
   - It sets the entry's key and model id. This fixes callers that pass a local
     model id, such as voice.
   - It merges the entry's `kwargs` and removes `chat_template_kwargs`.
   - It asks streamed replies to include token usage.
   - It refuses the call when this month's spend has reached the cap. The
     refusal is a plain message, like the licence gate: "MiMo monthly cap ($10)
     reached. Switch back to a local model or raise the cap."
   - After each reply, it adds the cost (tokens × price) to
     `~/.codec/cloud_spend.json`. The file has one total per month and model.
     Writes use a file lock, because several PM2 processes write to it.
   - The picker label shows the spend, for example "MiMo V2.6 Pro (cloud) —
     $0.42 of $10 this month".

5. **Voice.** Voice resolves its model when each session starts, not at import.
   If the active model is a cloud entry, voice uses that entry. Otherwise it
   keeps its pinned local `voice_model`, as today. The observer transport
   follows the resolved `base_url`, as chat does. The warm-up check is skipped
   for cloud models.

6. **Background agents stay local.** The Project-mode planner and runner keep
   the local model (Step 8, Q1: "no cloud fallback"), even after a restart
   while cloud is active. They fail while Qwen is down, as they do today.

## What goes to Xiaomi while switched

Every chat, PWA voice, crew and schedule message goes to Xiaomi. Each message
includes the system prompt, memory context and standing rules. The observer
summary is sent only when the existing check passes. These stay on the Mac:
vision, UI-TARS, STT, TTS and screenshots, because they have their own local
URLs.

## Limits (not in this change)

- Wake-word (`open-codec`), Telegram and iMessage read the model settings once
  at import. They follow a switch only after that process restarts. Fixing this
  is audit item 11 ("one way to read config").
- Vision stays on the local Qwen. MiMo accepts images, so vision could use it
  later.
- `ava.license_key` is stored as plain text in `config.json`. This will be
  logged in `docs/known-issues.md`.

## Tests

One new file, `tests/test_cloud_models.py`, with about 8 tests:

- The picker lists the cloud entry.
- Switching to cloud does not call PM2, and switching back restores the stored
  values.
- A failed cloud check reverts the switch.
- The pre-call check sets the key, model and `kwargs`, and removes
  `chat_template_kwargs`.
- A call over the cap is refused.
- Spend is added up correctly from usage, for both `call` and `stream`.
- Voice resolves to the cloud entry only when cloud is active.
- The observer transport is `voice` (not `local`) on a cloud model.

Live test with the real key:

- one chat reply and one PWA voice reply through MiMo;
- `cloud_spend.json` increases;
- switch back to Qwen 3.6.

## Rollback

Revert the PR. The config entry has no effect without the code. If the owner
is on MiMo at the time, picking a local model switches back.
