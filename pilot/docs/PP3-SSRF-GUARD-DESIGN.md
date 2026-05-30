# PP-3 — Navigation SSRF / scheme guard

**Closes:** Pilot audit **P-4** (no SSRF/scheme guard on `/navigate`). See
`codec-repo/docs/audits/PHASE-1-PILOT-AUDIT.md`.
**Repo:** `~/codec/` (Pilot).

## What

`pilot_chrome.navigate()` passed any URL straight to Playwright `goto()`. With the API
(pre-PP-1) unauthenticated, that let a caller — or the agent steered by a malicious page —
read local files (`file:///…`), pivot to internal services (the dashboard :8090, the local
LLM :8083, the Pilot CDP :9223), or hit cloud metadata (`169.254.169.254`).

## Fix

`validate_navigation_url(url)` — a pure function called at the top of `navigate()` (the
single navigation chokepoint, used by the agent loop, replay, and the `/navigate` route):
- scheme must be `http`/`https` → blocks `file:`, `javascript:`, `data:`, `chrome:`, `about:`, `ftp:`.
- host required; blocks `localhost`, `*.local`/`.localhost`/`.internal`.
- if the host is an IP literal that is loopback / private (RFC1918) / link-local (incl.
  `169.254.169.254`) / reserved / multicast / unspecified → blocked.
- otherwise (a public hostname or public IP) → allowed.

**Documented residual:** a public hostname that DNS-resolves to a private IP
(DNS-rebinding) is not caught — that needs resolve-then-check with TOCTOU handling, a
larger change.

## Tests (`tests/test_phase9_ssrf.py`, pytest, no browser)

13 blocked URLs (file/javascript/data/chrome/about/ftp + loopback/private/link-local/
metadata/::1 + empty) raise; 4 public http(s) URLs are allowed.
