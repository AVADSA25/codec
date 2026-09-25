# Custom CODEC voice (Kokoro voice file + tts_speed) — design

**Date:** 2026-09-25 · **Status:** implemented. Supersedes the first draft of this file
(Qwen3-TTS + reference clip), dropped after measurement.

## Decision history

Mickael chose a calm, slow, low male voice ("Night Shift", made with Qwen3-TTS
VoiceDesign). Measured Qwen3-TTS Base with a reference clip on this Mac:

| | Kokoro | Qwen3-TTS |
|---|---|---|
| One sentence, time to full audio | 0.28 s | 3.6–4.7 s |
| Streaming first sound | n/a | 0.45 s (generates at about real time) |
| RAM | tiny | 4.6 GB steady, 9.6 GB peak |

`codec_voice.synthesize()` waits for the full audio per sentence, so Qwen would add
about 4 s before every spoken sentence. Rejected: speed and RAM matter more.

## What shipped instead

A Kokoro voice is a `(510, 1, 256)` style tensor. `mlx_audio` accepts a `.safetensors`
path as `voice`, and averages comma-separated voices. So a custom voice needs no new
model: same 0.2–0.5 s latency, no extra RAM.

- Voice file: `~/.codec/voices/kokoro/k2-george-onyx.safetensors` = 0.6 `bm_george`
  + 0.4 `am_onyx`. Built with `mx.save_safetensors({"voice": tensor}, path)`.
- Config (Mickael's machine only): `tts_voice` = that absolute path, `tts_speed` = 0.85.
- New `codec_config.TTS_SPEED` (`tts_speed`, default 1.0). Every non-live caller now
  sends `speed`: `codec_watcher`, `codec_core` (runtime and generated session
  script), `codec_session`, `codec_textassist`, `skills/timer`, `skills/tts_say`.
- Live voice (`codec_voice`) reads `tts_speed` with its old default 1.15.
- `codec_watcher` and `skills/timer` no longer hardcode `am_adam`; they follow config.

Repo defaults are unchanged (`am_adam`, speed 1.0 / 1.15). Buyers see no difference.

## Left alone

Telegram briefings keep their own `briefing_voice` (`af_heart`). Flash mode keeps
its own speed (`FLASH_CFG`).

## Rollback

Set `tts_voice` back to `am_adam` and delete `tts_speed` in `~/.codec/config.json`,
then restart `codec-dashboard` and `open-codec` (they read config at import).
