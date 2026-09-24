"""Owner consent page for CODEC MCP HTTP OAuth (/oauth/consent).

`PersistentOAuthProvider.authorize()` parks every /authorize request and
redirects here. The owner enters the CODEC PIN (the same `auth_pin_hash` the
dashboard uses); only then is an authorization code issued. Without this,
the SDK's in-memory provider auto-approved everyone.

Brute-force limits (all RAM-only, reset on restart):
  - 5 wrong PINs per parked request  -> that request is denied
  - 5 wrong PINs per client IP        -> IP locked for 15 min
  - 20 wrong PINs across all IPs/hour -> page locked for everyone for 1 h
No PIN configured -> fail closed (nobody can connect a new client).
"""
from __future__ import annotations

import html
import json
import os
import threading
import time
from urllib.parse import parse_qs, urlparse

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from codec_pinhash import verify_pin

try:
    from codec_audit import log_event as _log_event
except ImportError:  # pragma: no cover
    def _log_event(*a, **kw):  # type: ignore[no-redef]
        pass

CONFIG_PATH = os.path.expanduser("~/.codec/config.json")

PER_REQUEST_MAX_FAILS = 5
PER_IP_MAX_FAILS = 5
PER_IP_LOCK_SECONDS = 15 * 60
GLOBAL_MAX_FAILS = 20
GLOBAL_WINDOW_SECONDS = 60 * 60

_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}


def _read_pin_hash() -> str:
    """Read at request time so a PIN set after startup applies."""
    try:
        with open(CONFIG_PATH) as f:
            return str(json.load(f).get("auth_pin_hash", "") or "")
    except Exception:
        return ""


def _client_ip(request: Request) -> str:
    return (request.headers.get("cf-connecting-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown"))


class _Limiter:
    def __init__(self):
        self._lock = threading.Lock()
        self.ip_fails: dict[str, list[float]] = {}
        self.ip_locked_until: dict[str, float] = {}
        self.global_fails: list[float] = []
        self.request_fails: dict[str, int] = {}

    def locked(self, ip: str) -> str | None:
        now = time.time()
        with self._lock:
            self.global_fails = [t for t in self.global_fails if t > now - GLOBAL_WINDOW_SECONDS]
            if len(self.global_fails) >= GLOBAL_MAX_FAILS:
                return "global"
            if self.ip_locked_until.get(ip, 0) > now:
                return "ip"
        return None

    def fail(self, ip: str, rid: str) -> int:
        """Record a wrong PIN; return failures so far on this request."""
        now = time.time()
        with self._lock:
            self.global_fails.append(now)
            fails = [t for t in self.ip_fails.get(ip, []) if t > now - PER_IP_LOCK_SECONDS] + [now]
            self.ip_fails[ip] = fails
            if len(fails) >= PER_IP_MAX_FAILS:
                self.ip_locked_until[ip] = now + PER_IP_LOCK_SECONDS
                self.ip_fails.pop(ip, None)
            n = self.request_fails.get(rid, 0) + 1
            self.request_fails[rid] = n
            # Bound the dict: parked requests expire after 10 min anyway.
            if len(self.request_fails) > 500:
                self.request_fails.clear()
        return n

    def forget(self, rid: str) -> None:
        with self._lock:
            self.request_fails.pop(rid, None)


def _audit(event: str, level: str, **extra) -> None:
    try:
        _log_event(event, "codec-mcp-consent", event, outcome="denied",
                   level=level, extra=extra)
    except Exception:
        pass


def _page(body: str, status: int = 200) -> HTMLResponse:
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Approve connection - CODEC</title>
<style>
body{{font-family:-apple-system,system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:24px 16px}}
main{{max-width:420px;margin:40px auto;background:#1b1b1b;border:1px solid #333;border-radius:12px;padding:24px}}
h1{{font-size:20px;margin:0 0 12px}} p{{color:#bbb;line-height:1.5;margin:0 0 12px}}
.host{{color:#fff;font-weight:600;word-break:break-all}} .err{{color:#ff8a80}}
input{{width:100%;box-sizing:border-box;font-size:16px;padding:12px;border-radius:8px;border:1px solid #444;background:#0d0d0d;color:#fff;margin:8px 0 16px}}
.row{{display:flex;gap:12px}} button{{flex:1;font-size:16px;padding:12px;border-radius:8px;border:0;cursor:pointer}}
.ok{{background:#d97757;color:#fff}} .no{{background:#333;color:#eee}}
</style></head><body><main>{body}</main></body></html>"""
    return HTMLResponse(doc, status_code=status, headers=_HEADERS)


def _form(rid: str, info: dict, error: str = "") -> HTMLResponse:
    host = urlparse(info["redirect_uri"]).netloc or info["redirect_uri"]
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return _page(f"""<h1>Approve connection to CODEC</h1>
<p><span class="host">{html.escape(info["client_name"])}</span> wants access to the
skills on this Mac. After approval it returns to
<span class="host">{html.escape(host)}</span>.</p>
<p>Only approve if you started this connection yourself.</p>{err}
<form method="post" action="/oauth/consent" autocomplete="off">
<input type="hidden" name="rid" value="{html.escape(rid)}">
<label for="pin">CODEC PIN</label>
<input id="pin" name="pin" type="password" inputmode="numeric" autofocus>
<div class="row"><button class="no" name="action" value="deny">Deny</button>
<button class="ok" name="action" value="approve">Approve</button></div>
</form>""")


def build_consent_routes(provider, pin_hash_getter=_read_pin_hash) -> list[Route]:
    limiter = _Limiter()

    async def consent(request: Request) -> Response:
        if request.method == "GET":
            rid = request.query_params.get("rid", "")
            info = provider.pending_request(rid)
            if info is None:
                return _page("<h1>Request expired</h1><p>Start the connection again from Claude.</p>", 404)
            if not pin_hash_getter():
                return _page("<h1>No PIN set</h1><p>Set a PIN in CODEC settings, then connect again.</p>", 503)
            return _form(rid, info)

        raw = (await request.body())[:4096].decode("utf-8", "replace")
        form = {k: v[0] for k, v in parse_qs(raw).items()}
        rid, pin, action = form.get("rid", ""), form.get("pin", ""), form.get("action", "")
        ip = _client_ip(request)
        info = provider.pending_request(rid)
        if info is None:
            return _page("<h1>Request expired</h1><p>Start the connection again from Claude.</p>", 404)

        if action == "deny":
            limiter.forget(rid)
            return RedirectResponse(provider.deny_pending(rid, "owner_denied"), status_code=303, headers=_HEADERS)

        pin_hash = pin_hash_getter()
        if not pin_hash:
            _audit("oauth_consent_no_pin", "warning", client_id=info["client_id"])
            return _page("<h1>No PIN set</h1><p>Set a PIN in CODEC settings, then connect again.</p>", 503)

        lock = limiter.locked(ip)
        if lock:
            _audit("oauth_consent_locked", "warning", scope=lock, client_ip=ip)
            return _page("<h1>Too many attempts</h1><p>Try again later.</p>", 429)

        if verify_pin(pin, pin_hash):
            limiter.forget(rid)
            url = await provider.approve_pending(rid)
            if url is None:
                return _page("<h1>Request expired</h1><p>Start the connection again from Claude.</p>", 404)
            return RedirectResponse(url, status_code=303, headers=_HEADERS)

        n = limiter.fail(ip, rid)
        _audit("oauth_consent_pin_failed", "warning", client_id=info["client_id"],
               client_ip=ip, attempt=n)
        if n >= PER_REQUEST_MAX_FAILS:
            limiter.forget(rid)
            return RedirectResponse(provider.deny_pending(rid, "too_many_pin_failures"),
                                    status_code=303, headers=_HEADERS)
        return _form(rid, info, f"Wrong PIN. {PER_REQUEST_MAX_FAILS - n} attempts left.")

    return [Route("/oauth/consent", consent, methods=["GET", "POST"])]
