# MCP OAuth owner gate — design

**Date:** 2026-09-24 · **Status:** approved by Mickael in chat ("fix this"), implemented in this PR.

## Problem

`codec-mcp-http` subclasses FastMCP's `InMemoryOAuthProvider`. Its `authorize()`
"simulates user authorization": it returns a code immediately, with no login or
consent. Dynamic Client Registration is open, because claude.ai needs it. Together
this means anyone who reaches `https://codec-mcp.avadigital.ai` (also the
`codec-mcp.lucyvpa.com` fallback) can register a client, get a code, and exchange
it for a 1-year access token to every MCP-exposed skill (Gmail, iMessage send,
Drive, file write). Confirmed 2026-09-24: `/register` answers publicly; no
Cloudflare Access policy sits in front of the hostname.

Registered clients at the time of the fix: 3 claude.ai clients (May 26–28, one
token each, believed to be the owner's) and one test client `alpha` with no tokens.

## Fix

1. `PersistentOAuthProvider.authorize()` no longer issues a code. It parks
   `(client, params)` under a random 32-byte `rid` (RAM only, 10 min TTL, max 50
   parked, oldest evicted) and redirects to `{base_url}/oauth/consent?rid=…`.
2. `codec_mcp_consent.py` serves `/oauth/consent`. GET shows the client name and
   the callback host, and asks for the CODEC PIN (`auth_pin_hash` in
   `~/.codec/config.json`, the same PIN the dashboard uses, read at request time).
   POST with the right PIN calls `approve_pending()`, which mints the code via the
   base class and 303-redirects to the client. Deny redirects with
   `error=access_denied`.
3. Brute-force limits: 5 wrong PINs per request (request denied), 5 per IP (IP
   locked 15 min), 20 across all IPs per hour (page locked 1 h). No PIN configured
   means fail closed.
4. Page headers: `X-Frame-Options: DENY`, CSP `frame-ancestors 'none'`,
   `Cache-Control: no-store`. All user-controlled text is HTML-escaped.

Registration stays open: a registered client without a code gets nothing.

## Audit events (new names, envelope unchanged)

`oauth_consent_requested`, `oauth_consent_granted`, `oauth_consent_denied`
(source `codec-oauth-provider`); `oauth_consent_pin_failed`, `oauth_consent_locked`,
`oauth_consent_no_pin` (source `codec-mcp-consent`). No PIN or token values are
logged.

## Compatibility

Existing access and refresh tokens are untouched, so the owner's claude.ai
connector keeps working after `pm2 restart codec-mcp-http`. Only new connections
see the PIN page. Refresh-token grants do not pass through `/authorize`.

## Tests

`tests/test_mcp_oauth_consent.py` drives the real SDK `/register`, `/authorize`
and `/token` routes: no code before the PIN, correct PIN gives an exchangeable
code, wrong PIN, per-request denial, IP lockout, deny, no-PIN fail-closed,
expiry, HTML escaping.

## Rollback

Revert the PR and restart `codec-mcp-http`. That reopens the hole, so only do it
together with a Cloudflare Access policy on both MCP hostnames.

## Follow-ups (not in this PR)

- `auth_pin_hash` is still legacy SHA-256; re-set the PIN to get argon2id.
- The Claude directory listing needs a reviewer path (the PIN gate blocks
  reviewers, which is correct for a per-user install).
