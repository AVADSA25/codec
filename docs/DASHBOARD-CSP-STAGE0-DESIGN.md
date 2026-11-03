# Dashboard web hardening, stage 0 (P-089)

Plan item P-089 (audit 25 Oct 2026: GP04-2 stage 0, GP03-7, GP04-4, GP04-6, GP04-7, GP04-8). Defence in depth: no
live script injection is known today, but any future one would run with the page's full authority and could send data
out through `connect-src ws: wss:` and `img-src https:`. No schema change, no audit event, no new dependency, no
sandbox change. Takes effect after a `codec-dashboard` restart (the consent page after a `codec-mcp-http` restart).

## What changes

| # | Today | After |
|---|---|---|
| 1 | One CSP for every page: `script-src`/`style-src`/`font-src` allow cdnjs, `img-src` allows any `https:`, `connect-src` allows any `ws:`/`wss:` plus `http://localhost:*` and `http://127.0.0.1:*`, no `object-src`, `base-uri` or `form-action`. No Permissions-Policy. | `codec_web_policy.py` holds two policies. Every page but `/vibe`: `script-src`/`style-src 'self' 'unsafe-inline'`, `font-src 'self'`, `img-src 'self' data:`, `connect-src 'self'` plus `ws://<Host> wss://<Host>` for the request's own Host only, `frame-src 'self'`, `worker-src 'self' blob:`, `object-src 'none'`, `base-uri 'none'`, `form-action 'self'`, `frame-ancestors` unchanged. `/vibe` adds cdnjs to script/style/font, `https:` to `img-src` and `http://127.0.0.1:*` to `frame-src`. Every page also gets `Permissions-Policy: camera=(self), microphone=(self), geolocation=(), payment=(), usb=()`. |
| 2 | The MCP consent page (`codec_mcp_consent.py`, served by `codec-mcp-http`) has no `form-action`. | `form-action 'self'` plus the origin of the client's registered `redirect_uri` on the consent form. Chrome checks `form-action` on the 303 that follows the POST too, so `'self'` alone would block the return to claude.ai and break every new connection. Other consent answers (expired, no PIN, locked) get `form-action 'self'`. |
| 3 | Monaco 0.45.0 from cdnjs. | Already done on main (D-24, 29 Oct): Monaco 0.57.0 is vendored in `static/vendor/monaco` with its LICENSE, ThirdPartyNotices.txt, SHA-256 in `static/vendor/SOURCES.md`, a row in `THIRD-PARTY-NOTICES.md`, and `.ttf` in `_StaticAssets.TYPES` with a test. Residual: it is 14.9 MB, not the brief's 8 MB (the TypeScript worker alone is 7.0 MB), so cdnjs stays in the `/vibe` policy only, and there only for AI-written preview pages, not for Monaco. |
| 4 | `POST /api/preview` writes any HTML to `~/.codec/preview.html` and `GET /preview_frame` serves it on the dashboard origin; only `scripts/feature_audit.py` called them. | Both routes removed (404). The Vibe preview is the page's sandboxed srcdoc frame. `feature_audit.py` reports the preview as UI-only; route tests updated. |
| 5 | `codec_chat.html` fetches `'/api/qchat/session/'+id` with the decoded `#session=` hash, so `#session=../../../api/screenshot` reaches another GET route. `/api/vibe/save` stores any `session_id`. | Chat: an id must match `^[A-Za-z0-9_-]{1,80}$` (genId's and the voice call's ids) in `_chatFromHash`, `bootSession` and `loadSession`, and both fetches use `encodeURIComponent`; a bad id starts a new chat and makes no request. Vibe: `/api/vibe/save` answers 400 unless `session_id` matches `^[a-z0-9]{8,40}$` (the page's `Date.now()` + `Math.random()` base-36 ids, 9 to 13 characters). Reading and deleting an old row are unchanged. |

## Remote pictures

The session lead's rule: keep `img-src` as it is wherever a page shows remote pictures today. Found:

- Chat, Home, Tasks, Voice, Cortex, Auth: none. Markdown pictures already render only `/api/image/file/...` and `data:`
  sources; any other address becomes a link (`static/codec-md.js`, `LOCAL_IMG`/`DATA_IMG` and the DOMPurify hook).
  Pexels pictures go into Google Docs (`codec_gdocs.py`), never into a page. Search results show text and links only.
  So `img-src 'self' data:` loses nothing there.
- Vibe: AI-written preview pages (srcdoc frames inherit the page's policy) can use pictures from the web. Kept:
  `img-src` on `/vibe` still allows `https:`. Whether to drop it (previews lose web pictures) or add a proxy is the
  owner's choice. `wss://exfil.example` is refused on `/vibe` as everywhere.

## Why per request

CSP 3 lets `'self'` match `ws:`/`wss:` on the same host, but older Safari does not, so the voice call's WebSocket is
named explicitly. The Host header is checked against `^(hostname|IPv4|[IPv6])(:port)?$` before it goes into the header;
anything else (spaces, `;`, quotes) gets no ws source instead of a broken policy. `HostAllowlistMiddleware` refuses
unknown hosts before this runs anyway.

## Tests

- `tests/test_web_hardening_stage0.py`: the six non-Vibe pages and `/vibe` get exactly the directives above and the
  Permissions-Policy; the ws source is the Host or nothing; CSS answers get no page policy; the preview routes answer
  404; the chat page's own `_chatFromHash` and `loadSession`, run in Node with a stubbed `fetch`, make no request for
  `#session=../api/screenshot` and its encoded forms and still load a real id. (The brief asked for jsdom; jsdom is not
  installed on this Mac or in CI, so the page's functions run in plain Node, as `tests/test_vibe_page_js.py` does.)
- `tests/test_vibe_page.py`: 200 ids made by the page's own expression (run in Node) save; `""`, `s1`, `../../etc`,
  capitals, a dash, 41 characters, a number and `null` answer 400 and store nothing.
- `tests/test_mcp_oauth_consent.py`: the form's policy names `'self'` and `https://claude.ai`; error pages `'self'`.
- Updated: `test_high_fixes`, `test_static_assets`, `test_vibe_runner`, `test_route_extractions`, `test_security`,
  `test_full_product_audit` (live-dashboard suite, not run here).
- Headless Chrome on a temp profile against a dashboard started from the worktree on a free port (writes, Keychain and
  outbound connections denied by a sandbox profile, lifespan off so no scheduler or warm-up runs): the 7 pages load,
  tabs are clicked, a chat with an uploaded picture, a voice call start, Vibe load and typing; console read of 0 CSP
  violations; an injected sandboxed frame cannot open `wss://exfil.example` or load an https picture on the six pages.

## Rollback

`git revert` of the PR restores the old header, the preview routes and the unchecked ids; then the owner restarts
`codec-dashboard` (and `codec-mcp-http` for the consent page).
