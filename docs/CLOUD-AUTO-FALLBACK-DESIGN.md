# Automatic cloud fallback — design

Status: approved 2026-09-28 ("go fallback") and implemented. Builds on
docs/CLOUD-FALLBACK-MODEL-DESIGN.md (manual switch, #377–#379).

## What

When the local model cannot answer, CODEC switches itself to the owner's
cloud model (MiMo V2.6 Pro), tells the owner once, and switches back to the
local model when it answers again. This happens only on installs that opt in.

Owner's request (28 Sep 2026): "if local model not acesible codec shpuld swit
automaticelly to mimo pleas".

## Why

Today the cloud switch is manual on purpose: nothing moves CODEC to the cloud
on its own (codec_cloud_models.py). When `qwen3.6` crashes, is stopped for a
memory-heavy job, or is missing from PM2, every chat fails until the owner
opens the model picker. This happened on 28 Sep: the `qwen3.6` PM2 entry was
gone, and chat worked only because MiMo had been picked by hand.

## How

1. **Opt-in, off by default.** `~/.codec/config.json:"llm_auto_fallback":
   "mimo-v2.6-pro"` names a registered cloud entry. Without it nothing
   changes. This keeps the local-first promise on every other install: data
   goes to a cloud provider only when the owner configured it.

2. **Switch to the cloud before building the prompt.** One check,
   `codec_models.ensure_llm_available()`, runs at the start of a chat turn
   (`routes/chat.py`), at the start of a PWA voice session (`codec_voice.py`),
   and every 60 s from codec-dashboard (which also covers crews and
   schedules). If the active model is local and
   its port is not listening (a TCP connect, under 5 ms when refused), the
   check calls the existing `set_active(<cloud id>)`. That call stores
   `llm_local_restore`, writes the cloud entry and sends its 1-token check.
   The check then records `llm_auto_fallback_active: {"from": <local id>,
   "at": <time>}`, and the turn continues on the cloud model.
   - Because the switch happens before the prompt is built, the existing
     privacy gate applies: the observer summary (window titles, OCR,
     clipboard) is not sent to Xiaomi.
   - A server that PM2 started less than 5 minutes ago counts as loading,
     not down: it opens its port only once the model is loaded.
   - A local server that is listening but hangs or errors is not covered:
     that reply fails as today. Only a closed port switches.
   - Voice checks once per session, not mid-session, because the session
     history may already hold observer summaries meant for the local model.
   - One switch at a time across processes (`~/.codec/model_switch.lock`);
     `set_active` holds the same lock, so a fallback never runs in the middle
     of a model switch.

3. **Tell the owner once.** One alert through `codec_alerts` (Telegram once
   `alerts.telegram.chat_id` is set, else a macOS banner): "Local model not
   answering: CODEC switched to MiMo." Another on the way back.

4. **Switch back by itself.** A 60 s background task in codec-dashboard runs
   while `llm_auto_fallback_active` is set. When the `from` model answers a
   probe twice in a row, it switches back without restarting the server
   (the server already serves that model), clears the flag and sends the
   "back on the local model" alert.
   - While on an automatic fallback, the heartbeat keeps probing the local
     model at its own address and keeps its existing restart rule for a
     crashed `qwen3.6`. It still skips the local probes on a manual cloud
     pick.
   - The launcher (`scripts/start_model_server.sh`) loads the model from
     `llm_local_restore` while config points at a cloud model. Before this, a
     `qwen3.6` restart during a cloud switch tried to load the cloud model id
     and failed, so the local model could never come back by itself.

5. **The owner's choice wins.** Picking any model by hand clears the flag, so
   a manual MiMo pick is never switched back, and a manual local pick is
   never switched away by a stale flag.

6. **Unchanged.** The $10/month hard cap: at the cap the fallback does not
   switch, and the existing refusal message stands. Project-mode planner and
   runner stay local (Step 8, Q1). Vision, STT, TTS and screenshots stay on
   the Mac.

## What goes to Xiaomi while switched

The same as a manual switch: chat, PWA voice, crew and schedule messages,
with the system prompt, memory context and standing rules. The observer
summary goes only when the existing check passes.

## Size

About 190 lines across codec_models.py, routes/chat.py, codec_voice.py,
codec_dashboard.py (a 60 s task), codec_alerts.py and the launcher, plus
tests. codec_llm is unchanged.

## Tests

10 tests, in `tests/test_cloud_auto_fallback.py`:
- no opt-in: no switch;
- local port closed: switches, sets the flag, one alert;
- cap reached or no key: no switch;
- a manual pick clears the flag;
- local back twice: switches back without a restart;
- already on the cloud by hand: nothing happens;
- the lock prevents two switches;
- the chat turn checks before it builds the prompt;
- the heartbeat keeps probing the local model at its own address.

Live test: stop `qwen3.6` (owner's word), send one chat, see it answered by
MiMo and the alert, start `qwen3.6`, see CODEC back on the 35B within ~2 min.

## Rollback

Remove the `llm_auto_fallback` line (instant), or revert the PR. If CODEC is
on an automatic fallback at that moment, pick the local model by hand.
