# Security hardening, September 2026

Follow-ups to the 27 Sep 2026 audit (items 2, 3, 5 and 6). One section per PR.

## Session cookie set by the server (audit item 2)

**Before:** the login endpoints returned the session token in the JSON body, and the auth page wrote it into a cookie from JavaScript. A copy also went to sessionStorage. Every page's fetch wrapper added the token to each API URL as `?s=<token>`, so the tokens ended up in the access log. The CSRF token was the first 16 characters of the session token.

**Now:**
- PIN, Touch ID and TOTP logins set `codec_session` in the response: `HttpOnly; SameSite=Lax; Path=/; Max-Age=<auth_session_hours>`. `Secure` is added when the visitor's connection is HTTPS (`x-forwarded-proto` / `cf-visitor` behind the tunnel). Page JavaScript cannot read the cookie.
- `codec_csrf` is a separate random value. It stays readable by JavaScript, which sends it in the `x-csrf-token` header on state-changing requests. The CSRF check itself did not change.
- Login responses no longer contain the token. The TOTP step reads the session from the cookie; a token in the request body is ignored.
- Logout deletes the session and clears both cookies from the server side.
- `AuthMiddleware` no longer accepts `?s=<token>`. Same-origin images, streams and the WebSocket send the cookie by themselves. The `AUTH REJECTED` log line says only whether a cookie was present.
- `/api/auth/verify` (Touch ID) answers 403 to requests from the tunnel or another machine, and `/api/auth/check` reports Touch ID as unavailable to them. The prompt appears on the Mac, so remote visitors use the PIN.

**Compatibility:** a browser that still holds an old JavaScript-written cookie keeps working until it expires. The auth page writes the cookies itself only when an older server returns the token in the body, so a new page served by a not-yet-restarted server still logs in.

**Tests:** `tests/test_auth_cookie.py`.

## Safe defaults (audit item 3)

**Host allowlist.** `HostAllowlistMiddleware` in `codec_dashboard.py` is the outermost layer, for HTTP and the WebSocket. It accepts:
- any IP literal (`127.0.0.1`, `[::1]`, a LAN address), since a client can only send one of those by connecting to this Mac directly;
- `localhost` and this Mac's own name (`socket.gethostname()`, with and without `.local`), for LAN setups;
- the tunnel hosts in `AuthMiddleware.TRUSTED_ORIGIN_HOSTS`;
- any name in `config.json:dashboard_public_hosts`;
- any name in the comma-separated `CODEC_DASHBOARD_EXTRA_HOSTS`.

Anything else gets 400, or a closed WebSocket. This stops DNS rebinding: a web page that points its own domain at 127.0.0.1 would otherwise look local and same-origin, and on an install with no login it could call `/api/run_code`. Put your own tunnel hostname in `dashboard_public_hosts`.

**WebSocket with no login configured:** only this Mac, the same rule as HTTP since #374.

**`/api/run_code`:**
- It answers 403 to requests from the tunnel or another machine. Clicking Run at the Mac is the approval.
- Each program runs in its own process group. When the 30 s timeout fires, the whole group is killed, so children it started in the background stop too. A child that starts its own session escapes this.
- There is still no `sandbox-exec` profile, for the reason given at the top of `routes/vibe_exec.py`: 8 languages, compilers and network.

**Installer (`setup_codec.py`):**
- The login question defaults to "Both Touch ID + PIN"; "None" is still available.
- `config.json` is written 0600.
- The token question now says what the token is: an API token for scripts, not a login.
- The installer does not generate a token automatically. With a token and no PIN, every dashboard page would get 401, because pages cannot send the token.

**Tests:** `tests/test_safe_defaults.py`. `tests/conftest.py` adds Starlette's `testserver` host through `CODEC_DASHBOARD_EXTRA_HOSTS`.

## MCP: default deny and chrome consent (audit item 5)

- **`mcp_default_allow: false`** on the live Mac since 28 Sep. The default in code was already `false`; the live config had it `true`. The switch left the same 71 skill tools exposed, because every exposed skill already sets `SKILL_MCP_EXPOSE = True`. From now on a new skill stays off MCP until it opts in or is listed in `mcp_allowed_tools`.
- **Seven more tools need the owner's Allow over HTTP** (`_HTTP_CONSENT_REQUIRED` only grows):
  - `chrome_read`, `chrome_extract` and `chrome_tabs` read pages and URLs from the logged-in browser;
  - `chrome_open` and `chrome_search` load URLs, which can carry data out;
  - `chrome_close` and `chrome_automate` can close every tab or quit Chrome.

  `chrome_scroll` stays open: it moves the page and reads nothing. Over stdio nothing changed.
- **README:** the table described both modes wrongly. It now says:
  - opt-in exposes skills with `SKILL_MCP_EXPOSE = True` plus `mcp_allowed_tools`;
  - opt-out exposes everything except `SKILL_MCP_EXPOSE = False`;
  - the HTTP built-in blocklist is listed in full.

**Tests:** `tests/test_mcp_http_side_effects.py` (the consent tests run for all 14 tools, plus an add-only check).

## One remote alert channel (audit item 6)

**Channel:** Telegram, outbound only, through the existing CODEC bot.
- Set `alerts.telegram.chat_id` in `config.json`. The bot token comes from the Keychain (`get_telegram_bot_token()`), so no plaintext token is needed; an older `alerts.telegram.bot_token` still works.
- `telegram.allowed_chat_ids` stays empty, so the bot accepts no commands.
- Messages are plain text, never HTML, so quoted errors cannot break them. They carry only service names and states.

**Once per problem.** `codec_alerts.alert_once(key, …)` sends an ongoing problem once, repeats it at most every 6 h, and `alert_resolved(key, …)` sends one message when it clears. State lives in `~/.codec/alerts_sent.json` (0600, file lock), shared by `codec-heartbeat` and the dashboard's own heartbeat. Before this, "not responding" fired on every heartbeat (every 20–30 min) for as long as a service stayed down.

**What alerts:**
- a service down twice in a row, and its recovery;
- disk under 0.5 GB;
- the PM2 working-directory check;
- PM2 restart storms (unchanged);
- new: any CODEC PM2 app in `errored`, meaning its crash loop hit the PM2 cap;
- new: `scripts/auto_pull.sh` failures, with one message when a later run pulls or finds main up to date again.

**Daily status.** The first heartbeat after 07:30 sends one line: CODEC apps online, the last auto-pull, free disk, open problems. It doubles as proof of life: no message means the Mac or the heartbeat is down.

**Cloud model.** While chat uses a cloud model (`llm_base_url` not local), the local model server is off on purpose, so "LLM (Qwen)" and "Vision" are not probed.

**macOS banner:** alert text is escaped before it goes into the AppleScript string.

**Tests:** `tests/test_remote_alerts.py`. The auto-pull tests run the real script with a stand-in Python and a throwaway git remote.
