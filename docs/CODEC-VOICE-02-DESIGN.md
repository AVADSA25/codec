# CODEC voice 02 (Qwen3-TTS) — design

Status: approved 23 Oct 2026 ("proceed on both"). Phase 1 built the same day; phase 2 (live voice) is not started.

## Goal

The owner asked for a new CODEC voice "alike Saito in Inception" made with Qwen3-TTS, listened to ten designed samples and chose **02, "close and warm"** (23 Oct 2026: "let go with 02-close-and-warm.m4a"). Files, script and seeds: `~/Documents/CODEC-private/voice-lab/inception-2026-10-23/`.

The voice is designed from a written description; it does not copy any real person's voice.

## What exists today (checked 23 Oct 2026)

- CODEC speaks with a Kokoro voice blend: `tts_voice` = `~/.codec/voices/kokoro/k2-george-onyx.safetensors`, `tts_speed` 0.85 (25 Sep 2026, `docs/NIGHT-SHIFT-VOICE-DESIGN.md`). Qwen3-TTS was measured then and rejected for live speech (3.6-4.7 s a sentence, 4.6-9.6 GB).
- One TTS server for everything: `mlx_audio.server` on `127.0.0.1:8085` (PM2 `kokoro-82m`). Its speech endpoint takes `model`, `voice`, `speed`, and also `ref_audio`, `ref_text`, `lang_code`, `stream`, `streaming_interval`.
- Ten places build their own request to that server: `codec_voice.synthesize` (live), `routes/tts.py` (the PWA's Speak and Read aloud, `/api/tts`), `skills/tts_say.py`, `skills/timer.py`, `codec_briefing.py`, `codec_scheduler.py`, `codec_watcher.py`, `codec_textassist.py`, `codec_telegram.py`, `codec_core`/`codec_session` (session audio).
- Auguste (Cookbook Chef Corpus) did the same on 25 Sep: VoiceDesign candidates, the pick became a reference clip, Qwen3-TTS Base cloned it per answer on this server, about real time; Auguste later went back to Kokoro for speed.

## Measured on this Mac today (M1 Ultra, model warm, `~/Documents/CODEC-private/voice-lab/measure_clone.py`)

| Way of speaking with voice 02 | Time | RAM in the TTS process |
|---|---|---|
| Kokoro blend (today) | about 0.3 s a sentence | tiny |
| Qwen3-TTS Base clone, whole sentence | about 3.3 s fixed plus 0.9 x the length of the audio (a 3.6 s sentence took 3.3 s; a 1.5 s one took 3.4 s) | 5.1 GB |
| Qwen3-TTS Base clone, streaming | first sound after 0.8-0.9 s, the whole sentence at 0.9 x its length | 5.1 GB |
| VoiceDesign from the description each time | 0.4 x the audio length, but the voice drifts between lines | 5.1 GB |

Speaker similarity to sample 02 (Qwen3-TTS speaker encoder): clone clips 0.955-0.991, design clips 0.943-0.972, nine other designed voices 0.971-0.989. The measure is crude (all these voices are close), but the design clips fall at or below "a different voice", so the voice is only stable with a reference clip. His ears decide.

## Design

### Phase 1: the cloned voice 02 for reading aloud and announcements

1. **Reference clip:** `~/.codec/voices/codec-02/reference.wav` (sample 02, 8.8 s) and `reference.txt` (its exact words), folder 0700, files 0600.
2. **`codec_tts.py` (new):** `payload(text, purpose)` returns the JSON for the TTS server. `purpose` is `live` or `speak`. With `tts_clone` in config, enabled for that purpose, and the reference present, it asks Qwen3-TTS Base to clone the reference (`model`, `input`, `ref_audio`, `ref_text`, `lang_code`); otherwise it returns today's Kokoro payload, byte for byte. `post(url, text, purpose, timeout)` sends it and, on an error, a timeout (45 s) or a reply that is not audio, sends the Kokoro payload once and writes one `tts_fallback` audit event (the reason, never the text).
3. **Callers that use it in phase 1 (purpose `speak`):** `routes/tts.py`, `skills/tts_say.py`, `skills/timer.py`, `codec_briefing.py`, `codec_scheduler.py`, `codec_watcher.py`, `codec_textassist.py`. **The live voice (`codec_voice.synthesize`) keeps the Kokoro blend** until phase 2, so conversation does not slow down; see "Changes made while building" for the other two places that stay as they are.
4. **Config, owner's machine only** (`~/.codec/config.json`, nothing in the repo defaults, so buyers see no change): `tts_clone = {"enabled_for": ["speak"], "model": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16", "ref_audio": "~/.codec/voices/codec-02/reference.wav", "ref_text_file": "~/.codec/voices/codec-02/reference.txt"}`.
5. **Warm-up:** the first Qwen request loads the model (about 6 s) and the TTS process then holds 5 GB. The dashboard sends one throw-away word at start, in the background, so the first Speak is not the slow one.
6. **Speed:** Qwen3-TTS ignores `speed`, so the speed setting does not change this voice. The Settings page says so when `tts_clone` is on.
7. **Audit:** `tts_fallback` only (reason and purpose). No text, no path.

### Phase 2: the live voice with voice 02 (own design after phase 1 is heard)

Streaming gives a first sound after about 0.9 s: `codec_voice.synthesize` would send audio chunks over the WebSocket as they arrive, and the page would play them in order. Not in this change.

## Tests

- `payload`: each purpose with and without `tts_clone`; with no `tts_clone` every caller's payload is identical to today's (a regression test per caller).
- Fallback: error, timeout, an empty body and a JSON error body each fall back once to Kokoro; the audit event carries no text.
- A missing or empty reference file means Kokoro, with no error shown to the owner.
- A scan test: no caller in the phase 1 list builds its own speech payload any more.
- Live check after the restart: Speak on a reply in the PWA returns audio from the clone (the server's model list shows Qwen3-TTS loaded) and falls back when the reference is renamed.

## Rollout and rollback

After the merge the owner restarts `codec-dashboard` and `open-codec` (they read config at import; the briefing, watcher and Telegram processes too if they run: `pm2 restart codec-dashboard open-codec codec-telegram codec-watchdog`). Rollback: remove `tts_clone` from the config (or set `enabled_for` to `[]`) and restart; the code stays and changes nothing.

## Decisions (defaults chosen; say if you want another)

1. **Phase 1 covers reading aloud and announcements; live conversation stays on the Kokoro blend until phase 2.** Other option: use voice 02 for live replies at once, which adds about 1-4 s before each sentence until streaming exists.
2. **Voice 02 is used as it is** (the sample as the reference clip). Other option: first make a longer, cleaner reference clip.
3. **Any failure falls back to the Kokoro blend** without an error message.

## Changes made while building

- **Left alone, on purpose:** `codec_telegram.py` (the Telegram voice briefings use their own `briefing_voice`, a decision of 25 Sep), `codec_core.speak` (the wake-word replies on the Mac are a live conversation, so they belong to phase 2) and the two generated session scripts (`codec_core`, `codec_textassist`) that carry their own copy of the request. A test pins that `codec_voice.py`, `codec_core.py` and `codec_telegram.py` do not import `codec_tts`.
- `/api/tts` answers with a header `X-CODEC-Voice: clone` or `kokoro`, so a check and the owner can tell which voice spoke.
- A voice chosen by hand in the PWA (any voice other than the configured one) is Kokoro's: the clone only speaks for the owner's own setting.
- `codec_tts.speak` tells "no answer at all" (`unreachable`, the PWA's 503) from "an answer that is no audio" (the PWA's 502), as `routes/tts.py` did before.
- The fallback audit line is rate-limited to one per reason per minute, so a stopped TTS server cannot flood the log.
- `tests/test_tts_reader.py` patches `requests.post` instead of the alias `routes.tts.rq`, which went away with the direct request.
- The reference clip and the `tts_clone` config are made on the owner's machine at deployment (a copy of sample 02 and its words); nothing in the repo defaults changes.
- **Three protections found by trying a real mlx-audio server on a spare port (23 Oct 2026):** a model id the server does not have makes it go to the Hugging Face network and hold every request (Kokoro too) for more than a minute; a reference file that is not audio hung a request for 90 seconds; and a clone is spoken at about the speed of speech, so a long text holds the server for as long as it lasts. So `clone_settings` now requires the model to be in the local Hugging Face cache and the reference to be a real WAV file, and `payloads` sends only texts of at most 400 characters (`tts_clone.max_chars`) to the clone; anything else goes to Kokoro straight away. The PWA reader's pieces are at most 280 characters, so reading aloud stays in voice 02; the morning briefing, scheduled results and the text-assist Read aloud (all longer) stay on Kokoro in phase 1.
