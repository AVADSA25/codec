# Night Shift voice for CODEC — design

**Date:** 2026-09-25 · **Status:** proposed, waiting for Mickael's approval. No code written.

## What and why

Mickael chose "Night Shift" (sample 5 of 5, made with Qwen3-TTS VoiceDesign) as his
CODEC voice: very soft, low, slow, near-murmur male voice. Today CODEC speaks with
Kokoro (`tts_voice: am_adam`).

## Constraint: keep the voice identical on every reply

VoiceDesign builds a new voice from a text description on each call, so the voice
drifts between replies. To keep one voice, use the Qwen3-TTS **Base** model with the
saved Night Shift clip as a reference (voice cloning of our own generated sample).
The reference is already saved:

- `~/.codec/voices/night-shift.wav` (24 kHz, ~12 s)
- `~/.codec/voices/night-shift.txt` (exact transcript, required as `ref_text`)

No real person's voice is cloned.

## Finding: the server already supports it

`mlx_audio.server` (PM2 `kokoro-82m`, port 8085) accepts `ref_audio` and `ref_text`
on `/v1/audio/speech`. Only the CODEC callers need to send them. Callers today send
only `model`, `input`, `voice` (and `speed` in `codec_voice.py`):

| File | Line (approx.) |
|---|---|
| `codec_voice.py` | 792 (main voice reply path) |
| `codec_core.py` | 314 and generated skill code at 447 |
| `codec_session.py` | 278 |
| `codec_watcher.py` | 127 |

## Change

1. New helper `codec_tts.py`: `speech_payload(text, speed=None) -> dict`. Builds the
   request body from config. Adds `ref_audio` and `ref_text` only when both
   `tts_ref_audio` and `tts_ref_text_file` are set. Otherwise the payload is exactly
   what is sent today.
2. The four callers use the helper. No other behaviour changes.
3. Config keys (`~/.codec/config.json`, Mickael's machine only): `tts_model` set to
   `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16`, plus `tts_ref_audio` and
   `tts_ref_text_file`.
4. Fallback: if the Qwen request fails or exceeds a timeout, retry once with Kokoro
   so a reply is never silent.

Repo default stays Kokoro. Buyers are unaffected: the DMG does not ship Qwen3-TTS
(about 8.5 GB on disk).

## Risks

- **Latency.** Qwen3-TTS is 1.7B parameters against Kokoro's 82M. Not measured yet.
  Voice replies need a fast first audio. Streaming (`stream=true`) may be needed.
- **RAM.** A second model resident next to Qwen 35B. Not measured yet. Free RAM is
  about 3.8 GB while the LoRA run is active, so the benchmark waits until it ends.
- **Model switching.** `kokoro-82m` is a PM2 process named for Kokoro but runs the
  generic server; it is on the protected list. Changing the model happens per request,
  so no restart is needed for the config change itself. Confirm the server keeps one
  model resident and does not double-load.

## Gates (verified before "done")

1. Time to first audio for a one-sentence reply, measured 5 times. Mickael sets the
   limit after hearing it. Suggested: 2 s or less.
2. Same sentence generated 3 times sounds like the same voice (listen, plus speaker
   similarity check).
3. Peak extra RAM with Qwen 35B loaded, measured with `footprint`.
4. Force a Qwen failure: the reply still plays, using Kokoro.
5. All four callers use the helper. Existing TTS tests pass. New test: payload is
   unchanged when the ref keys are unset.
6. Rollback: set `tts_model` back to `mlx-community/Kokoro-82M-bf16` and remove the
   ref keys. No restart, no code revert.

## Out of scope

`tts_say` skill (hidden from MCP, PR #369), the setup wizard voice list, changing the
repo default engine, and any other voice.
