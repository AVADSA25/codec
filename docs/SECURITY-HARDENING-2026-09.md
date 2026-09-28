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
