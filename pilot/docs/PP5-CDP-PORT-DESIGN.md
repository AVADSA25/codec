# PP-5 — Randomized CDP debug port

**Closes:** Pilot audit **P-8** (Chrome CDP debug port is a fixed, predictable 9223 that
any local process can attach to). See `codec-repo/docs/audits/PHASE-1-PILOT-AUDIT.md`.
**Repo:** `~/codec/` (Pilot).

## What

`PilotChrome` launched Chromium with `--remote-debugging-port=9223` — a fixed, predictable
port. Chrome's CDP socket has **no authentication**, so any local user-mode process could
attach (`/json` → WebSocket → `Network.getAllCookies`, `Runtime.evaluate`) and take over the
logged-in Pilot profile. **Verified loopback-bound** (no `--remote-debugging-address=0.0.0.0`),
so this is local-process-hijack hardening, not the 0.0.0.0 case.

Nothing in Pilot connects *to* that port (it's only passed to Chromium + reported in the
status dict), so randomizing it is safe.

## Fix

- `_free_port()` picks a random free loopback port.
- `PilotChrome(cdp_port=None)` (the new default) allocates a fresh random port per launch;
  an explicit `cdp_port=` is still honored (back-compat). `pilot_runner` no longer forces the
  fixed `config.CDP_PORT`.
- Predictability — the core of P-8 — is removed; a local attacker can no longer assume 9223.

**Residual:** a determined local process could still port-scan + read `/json`. The stronger
fix is Playwright pipe transport (`--remote-debugging-pipe`, no TCP) — a larger change noted
for follow-up.

## Tests (`tests/test_phase11_cdp_port.py`, pytest, no browser)

CDP port is randomized per instance (≠ 9223, distinct, ephemeral range); an explicit port is
still respected.
