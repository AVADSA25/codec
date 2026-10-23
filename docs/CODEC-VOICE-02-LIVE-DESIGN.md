# CODEC voice 02, phase 2: the live conversation voice — design

Status: approved 23 Oct 2026 ("Proceed") and built the same day. Phase 1 (reading aloud and announcements) is merged and live (`docs/CODEC-VOICE-02-DESIGN.md`, PR #469).

## Goal

When the owner talks with CODEC (the voice call in the PWA, spoken or typed turns, every mode), the replies are spoken in voice 02, the cloned voice he chose, instead of the Kokoro blend. The first sound must come within about half a second of the first sentence being ready, and a reply must play without gaps. Anything that goes wrong must fall back to the Kokoro blend so that CODEC never goes silent.

## What exists today (read in the code, 23 Oct 2026)

- **Server side, `codec_voice.py`:** the LLM reply streams token by token. `_flush_on_boundary` cuts it into sentences, and for each sentence `_speak` awaits `synthesize` (one Kokoro request that returns a whole MP3, about 0.3 s) and sends it as one binary WebSocket frame. The loop waits for each sentence before reading more tokens, and it holds the LLM queue slot (`Priority.CRITICAL`) for the whole spoken reply. `last_tts_end` (echo guard) is computed from the MP3 size (`TTS_BYTES_PER_SEC`).
- **Page side, `codec_voice.html`:** each binary frame is a complete audio file. `playNext` decodes it with `decodeAudioData` and plays it, then the next one when `onended` fires. The audio context runs at 16 kHz (it was made for the microphone), so every reply is played band-limited to 8 kHz. `stopAudio()` clears the queue when the owner interrupts.
- **TTS server (`mlx_audio.server` 0.4.3, PM2 `kokoro-82m`, :8085):** one broker thread does all GPU work one request at a time, in order. A request with `stream: true` and `response_format: "pcm"` returns raw 16-bit mono 24 kHz audio in chunks of `streaming_interval` seconds. The broker checks for a closed connection after every chunk and then stops the generation. Requests sent together were handled one after another in the measurements below.
- Phase 1 helper `codec_tts.py` already knows the clone settings (`tts_clone`, purposes `speak` and `live`) and the protections (model in the local cache, reference is a real WAV, short text).

## Measured today on the real TTS server (M1 Ultra, model warm, `~/Documents/CODEC-private/voice-lab/measure_stream.py`)

| What | Result |
|---|---|
| Clone streaming, `streaming_interval` 0.5 / 1.0 / 2.0 s | first sound after 0.53 / 0.91 / 1.72 s (about 0.13 s + 0.8 x the interval); audio is made at 0.83-0.92 x real time |
| Played live from the first byte, no waiting buffer, 6 runs | 0.00 s of gaps in all of them |
| One request after another (3 sentences) | the server starts the next request about 0.9 s after the end of the previous one at interval 1.0 (about 0.5 s at 0.5); that always fits inside the audio still being played |
| Whole replies of 1, 3 and 6 sentences through the planned worker, interval 0.5 | first sound after 0.53-0.55 s; gaps in total 0.00-0.05 s; 0.00 s with a 0.5 s waiting buffer (which delays the start by 0.4 s) |
| One request per sentence versus "what is waiting, up to 280 characters" | 0.02-0.05 s versus 0.00-0.03 s of gaps: both fine, the second makes fewer requests |
| The LLM and the clone running together | LLM 53 tokens/s instead of 72; clone first sound 0.64 s instead of 0.56 s; no gaps |
| Closing the connection after the first bytes | the server was free again within about 1 s (a Kokoro request right after took 1.04 s, then 0.13 s); at interval 0.5 the wait is about half |
| A Kokoro request sent while a clone is being generated | it waited until the clone finished (14.6 s for an 18.8 s clone): the server does one thing at a time |
| Joins between chunks | no clicks: the biggest jump at a join was 0.81 x the normal sample-to-sample jump nearby |

Streamed audio has the same sample rate as the non-streamed one (24,000 Hz, mono, 16-bit). A streamed clip of the voice is `~/Documents/CODEC-private/voice-lab/voice02-streamed.m4a`.

## Design

### 1. Streaming client, `codec_tts.py`

`astream_clone(http, url, text, cfg, ...)` is an async generator over an `httpx` streaming request with `{model, input, ref_audio, ref_text, lang_code, response_format: "pcm", stream: true, streaming_interval}`. It yields byte-aligned PCM (a chunk that ends in the middle of a sample keeps its odd byte for the next one). It raises `CloneUnavailable` if the settings are not usable for purpose `live` or no audio comes within `first_byte_timeout` (8 s), and `CloneBroke` if the stream stops or goes silent for more than `idle_timeout` (6 s) after audio has started. Leaving the loop closes the connection, which makes the server stop generating. The text splitter for the live voice lives here too: sentences, never more than `chunk_chars` (280) characters, cut at a comma or a space if one sentence is longer.

### 2. The speaking worker, `codec_voice_speech.py` (new)

One `LiveSpeech` object per call, with one background task that speaks one piece at a time:

- `say_nowait(text)`: queue text for speaking and return at once. `say(text)` queues and waits until the audio of that text has been sent. `drain()` waits until everything queued has been sent or cancelled. `cancel()` stops the current stream, drops the queue and sends `{"type": "audio_reset"}` to the page.
- The task takes the first waiting piece and adds the pieces already waiting behind it, up to `chunk_chars` (what the measurements call "greedy"). It streams the clone and sends every PCM chunk as one binary frame (section 4). `transcript_chunk` for that text goes out just before its audio starts, as today, so the text on the page stays in step with the voice.
- `last_tts_end` moves forward by the length of each chunk (plus the existing browser lag once), so the echo guard still covers the whole spoken reply.
- **Fallback ladder:** (1) the clone stream; (2) if it fails before any audio (not usable, error, no first byte in 8 s), this piece goes to Kokoro through the existing `synthesize` and one whole-file frame, exactly as today; (3) if it breaks after audio has started, the rest of that piece is dropped (repeating the start would be worse) and the next piece tries the clone again; (4) two failures in a row turn the clone off for the rest of that call; (5) if Kokoro fails as well, the reply is shown as text only, as today. Each failure writes the existing `tts_fallback` audit line with purpose `live` (reason only, never the text, at most one a minute per reason).
- A piece that arrives while the interrupt flag is set is dropped.

### 3. Voice pipeline, `codec_voice.py`

- `_speak(text)` keeps its meaning (returns when the audio is sent, False when interrupted); with the clone on for `live` it goes through `LiveSpeech.say`. Every spoken output in the call uses it, so the greeting, mode acknowledgements, crew and skill results, screen descriptions and question announcements all come out in voice 02. Long texts are split by the worker; nothing sends a request longer than `chunk_chars`.
- The LLM loop uses `say_nowait` and no longer waits for the audio, then `await drain()` after the last token. The LLM queue slot is released as soon as the model has finished (a second or two) instead of after the whole spoken reply. `processing` stays true until the drain ends, and the final `transcript` message is sent after it, as now. If the interrupt flag is set at the end, the transcript gets its " [interrupted]" mark as now.
- An `interrupt` control message always gets an `audio_reset` back (also when nothing is speaking), and an interrupt detected from the microphone energy cancels the worker the same way.
- At the start of a call the clone model is warmed in the background (`codec_tts.warm_up`), because the dashboard only warms it once at its own start and the TTS server may have restarted since.
- Without `live` in `tts_clone.enabled_for`, none of this runs: `_speak` and the LLM loop behave exactly as today.

### 4. Wire format

Existing frames are unchanged: a binary frame that is a complete audio file (Kokoro MP3). New: a PCM frame is `b"CPCM"` + the sample rate as 4 bytes (little endian) + 16-bit little-endian mono samples. Every frame says what it is, so the page needs no mode switch and a mix of both kinds in one reply (a fallback in the middle) plays in order. New JSON message: `{"type": "audio_reset"}`.

### 5. The page, `static/voice-audio.js` and `codec_voice.html`

- **One playback scheduler** replaces the "play the next file when the last one ends" chain: every item (a decoded file or a PCM chunk) is started at the end of the previous one on the audio clock, so playback is gapless. Items are handled in the order they arrive. `isPlaying`, `stopAudio()`, the orb level and the interrupt detection keep their meaning.
- **Output at 24 kHz:** reply audio plays through its own `AudioContext` at 24,000 Hz (the microphone context stays at 16 kHz), so voice 02 is heard at the rate it was made and chosen at, not cut at 8 kHz. If that context cannot be made, the old one is used.
- **Interrupt:** on `interrupt` the page stops everything and ignores audio frames until the server's `audio_reset` (or 1.5 s), so chunks that were already on the way cannot play after the owner has spoken. `audio_reset` from the server also stops playback.
- The page is served live from the repo, so it takes effect on the next load; a hard refresh on each device.

### 6. Config (the owner's machine only, nothing in the repo defaults)

`tts_clone.enabled_for` gains `"live"`. Optional keys under `tts_clone.live`, with these defaults in code: `interval` 0.5, `first_byte_timeout` 8, `idle_timeout` 6, `chunk_chars` 280, `breaker_failures` 2. Phase 1's `max_chars` (400) stays the limit for one request.

### 7. Limits that stay true (documented, not fixed here)

- **One speaker at a time.** The TTS server runs one request at a time, so anything else that speaks while a reply is being generated (Speak on a page, a timer, the morning briefing) waits for it, and a live reply waits behind a long reading that was already running. Only matters if both happen at once.
- **Speed.** Qwen3-TTS ignores the speed setting, so Flash mode's faster speech (1.25 x) no longer applies; voice 02 speaks at its own pace in every mode.
- **Wake-word replies on the Mac, Telegram voice briefings and the generated session scripts** keep the Kokoro blend. The wake-word path plays a whole file with `afplay`; streaming it needs `ffplay` (installed here, not on a customer's Mac), so it is a separate step.

## Tests

- **Streaming client:** an `httpx` mock transport that streams: odd-size chunks stay sample-aligned; no first byte, a 500, a body that is JSON, a stop in the middle and silence in the middle each raise the right error; leaving the loop early closes the connection; the splitter keeps every piece within `chunk_chars` and loses no text.
- **Worker:** pieces in order; waiting pieces are merged up to the limit; `cancel()` closes the stream, empties the queue and sends `audio_reset` once; fallback ladder steps 2 to 5 including the per-call breaker; `last_tts_end` moves by each chunk; no text in any audit line; a closed socket stops the stream.
- **Pipeline:** the LLM loop is not held up by slow speech (a fake worker that takes 5 s per piece); `processing` stays true until the drain; interrupt during speech, during the LLM and after the LLM has finished; typed turns; crew, skill and screen results are split and spoken; with `live` off the old path is byte for byte the same (frames and order).
- **Page, with Node and a fake audio context** (the way `tests/test_vibe_agent_page.py` does it): files and PCM chunks play in arrival order; each item starts exactly where the last one ends; `stopAudio` stops everything; frames are ignored until `audio_reset` and accepted after it; a PCM header is parsed and a broken one is skipped; `isPlaying` goes false only when the last item has ended; the existing `test_voice_call_ui.py` assertions still hold.
- **A real mlx-audio server on a spare port**, as in phase 1: the real worker streams a three-sentence reply (first sound under 1 s, gaps under 0.1 s in total), a cancel frees the server in under 1 s, an uncached model id and a garbage reference clip go to Kokoro at once and the server is never held up, the test server is stopped.
- **Mutation check:** break each rule above on purpose, one at a time; every break must fail a test.
- **Live check after the restart** (`live_check_voice_live.py`, written with the build): open `/ws/voice` with the internal token, send a typed turn, and check that PCM frames arrive, the first one within 3 s of the turn, and that `status: listening` comes at the end; send `interrupt` after the first frame and check for `audio_reset` and no PCM after it; hide the reference clip and check that whole-file frames arrive instead; put the clip back. Then the ear check: a real call, listened to by the owner.

## Rollout and rollback

Merge, then the owner restarts `codec-dashboard open-codec` and hard-refreshes the voice page. The code is inert until `"live"` is added to `tts_clone.enabled_for`; I add it after the restart, run the live check, and the owner makes one call. Rollback: remove `"live"` from `enabled_for` (or the whole `tts_clone`) and restart; the page keeps working with both kinds of frame, and the old behaviour comes back exactly.

## Decisions (defaults chosen; say if you want another)

1. **Scope: the PWA voice call only, all modes, spoken and typed turns.** Other option: also the wake-word replies on the Mac now (needs a player that streams; adds a dependency on `ffplay`).
2. **Every spoken output in a call is voice 02**, not only the LLM replies.
3. **Reply audio plays at 24 kHz through its own audio context.** Other option: keep the 16 kHz context (smaller change, duller voice). It is a single constant; the owner's ear decides.
4. **The Flash mode speed-up is given up** (the model ignores speed). Other option: keep Flash on Kokoro.
5. **No waiting buffer before playback** (first sound at about 0.55 s; measured gaps 0.00-0.05 s). Other option: a 0.5 s buffer, no gaps at all, first sound at about 0.95 s. Not built; easy to add if he hears gaps.

## Gates (what "done" means for the build)

Tests for the client, the worker, the pipeline and the page pass; the real-server test and the mutation check pass; the full suite and ruff pass; the PR is merged; after the restart the live check passes and the owner has heard a call in voice 02 and says it sounds right.

## Changes made while building

- **The page player is its own file**, `static/voice-audio.js`, stamped by `tools/stamp_static.py` and tested with Node and a fake audio context, as `static/vibe-agent.js` and `static/notes.js` are, instead of more inline script in `codec_voice.html`. The page keeps `queueAudio`, `stopAudio` and `isPlaying`; `playNext`, `audioQueue` and `curSource` are gone.
- **Two failures "in a row", not "in one call":** a good piece resets the count, so two unlucky moments in a long call do not turn voice 02 off for hours.
- **`prebuffer` was not built** (measured gaps 0.00-0.05 s without it).
- **Every `interrupt` control message gets an `audio_reset` answer**, with voice 02 off as well, because the new page ignores audio until it arrives. An interrupt found from the microphone's energy sends one only when the worker exists, once however many loud frames follow, so with voice 02 off the server's messages are exactly what they were.
- **Greeting, crew progress lines and the think agent's narration are queued, not awaited** (`_speak_outside_a_turn`), so the call starts listening and the agent keeps working while they are spoken.
- **The call warms the clone model at its start** only when the TTS server does not list it as loaded (`GET /v1/models`, `codec_tts.model_loaded`); `codec_tts.warm_up` takes a `purpose` now.
- **Real-server check on the real server, not a spare one:** the live path sends no new unusual request shape (all measured on the real server first), and the three protections from phase 1 are applied before any request by `live_settings`, so `check_voice_live_server.py` runs the real worker against the owner's TTS server with short requests only (and a closed port for the fallback case). Result on 23 Oct: first sound 0.54 s, 8.2 s of audio with 0.00 s of gaps, the server free again 0.67 s after a cancel, three Kokoro files when the clone connection is refused, clone off after two failures.
- Phase 1's test that `codec_voice.py` does not import `codec_tts` became "reaches voice 02 only through `LiveSpeech`"; the orb test now looks for `out: outAnalyser` instead of the old `src.connect(...)` line.
