# CODEC Pilot — parked (project development)

**Status:** parked as of v3.5 (2026-07-28). Not deleted — the code stays in the repo and on GitHub for future development.

## What "parked" means

Pilot's user-facing surface is **unhooked** from the running product:

- The dashboard no longer mounts the Pilot proxy router (`routes/pilot_proxy.py` removed from `codec_dashboard.py`).
- The `pilot-runner` PM2 service is removed from `ecosystem.config.js` (no `:8094` process).
- The `pilot` skill (`skills/pilot.py`) is removed, so it is no longer exposed over chat/voice/MCP and no longer counts toward the built-in skill total.

CODEC ships as **7 products** — Core · Dictate · Instant · Chat · Vibe · Voice · Overview. "CODEC Project" folds under **CODEC Overview**.

## Why it was parked

Browser record-and-replay automation hit hard external walls that aren't ours to fix:

- Google (and similar providers) detect and block CDP-controlled browser sign-in, so any flow that starts behind a Google login can't be automated reliably.
- Cookie/consent walls and anti-bot challenges break deterministic replay.

Rather than ship an automation product that fails on the most common real-world flows, Pilot is parked until the approach (or the platform constraints) change.

## Where the code lives

The Pilot engine source was vendored under `pilot/` and its skill/route/proxy adapters in the main tree. Removed in the v3.5 unhook PR (see git history for the full file list). To revive: restore those files, re-add the `pilot-runner` block to `ecosystem.config.js`, re-mount the proxy router in `codec_dashboard.py`, and regenerate the skill manifest.
