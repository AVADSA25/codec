# PP-1 — Pilot API hardening: loopback bind + token auth + CORS lockdown

**Closes:** Pilot audit **P-1** (unauthenticated control plane on 0.0.0.0 + public tunnel) —
the live RCE that was emergency-stopped 2026-05-24. See
`codec-repo/docs/audits/PHASE-1-PILOT-AUDIT.md`.
**Repo:** `~/codec/` (the Pilot repo — separate from `codec-repo`).
**Paired change:** `codec-repo/skills/pilot.py` must send the token (its own PR).

## What

The FastAPI server (`pilot_runner.py`) on :8094 had **no authentication**, CORS `["*"]`, and
bound `0.0.0.0` — and was internet-published via the `pilot.lucyvpa.com` Cloudflare tunnel.
Binding loopback alone does NOT close the tunnel (cloudflared connects from the same host),
so **authentication is the essential fix**. PP-1:

1. **Token auth on every request.** A shared secret at `~/.codec/pilot_token` (0600,
   auto-bootstrapped on first start). An HTTP middleware requires header `x-pilot-token` to
   match (constant-time `hmac.compare_digest`) on **all** routes; otherwise **401**. This
   rejects unauthenticated callers even via the tunnel.
2. **Loopback bind.** `uvicorn.run(host=PILOT_API_HOST)` with new `config.PILOT_API_HOST =
   "127.0.0.1"` (was `0.0.0.0`) — removes direct LAN reach as defense-in-depth.
3. **CORS lockdown.** `allow_origins=["*"]` → `allow_origin_regex` for localhost only — a
   malicious web page can't script cross-origin reads of the API.

## Token handshake

Both processes read `~/.codec/pilot_token`:
- **Pilot** bootstraps it at startup (generate `secrets.token_urlsafe(32)`, write 0600 if
  absent) and loads it into `_PILOT_TOKEN`; the middleware checks it.
- **Parent** (`codec-repo/skills/pilot.py`) reads the same file and sends `x-pilot-token` on
  every `_get`/`_post`. If the file is missing (Pilot never started), the parent sends empty
  → 401 → reports "pilot not available" (acceptable; Pilot must be up to serve anyway).

No `codec_*` import — Pilot stays repo-independent (it can't cleanly import the parent's
`codec_keychain`). A flat 0600 token file is the minimal self-contained mechanism; it sits
in `~/.codec/` which is already the user-private state dir.

## Known follow-ups (not PP-1)

- The dashboard **live MJPEG view** (`<img src=:8094/stream>`) can't send a custom header, so
  it will 401 after this change — it needs a token-aware proxy through the authed dashboard
  (tracked; the stream leaked screenshots while unauthed, so gating it is correct).
- Remaining wave: PP-2 (compiler injection + approve-gate), PP-3 (SSRF + CDP), PP-4 (prompt
  injection + real HITL), PP-5 (audit adapter + secret redaction).

## Test plan (TDD — `tests/test_phase7_auth.py`, pytest + Starlette TestClient)

1. `test_unauthenticated_request_rejected` — request with no `x-pilot-token` → 401.
2. `test_authenticated_request_passes_auth` — request with the correct token → not 401.
3. `test_api_binds_loopback_by_default` — `config.PILOT_API_HOST == "127.0.0.1"`.

## Rollback

Revert the commit. Token-auth + loopback are additive; reverting restores the (insecure)
open API. Do not run Pilot reverted while the tunnel route exists.
