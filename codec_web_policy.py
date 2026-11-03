"""The policies the dashboard sends with every page (P-089, stage 0).

docs/DASHBOARD-CSP-STAGE0-DESIGN.md. No page loads code, fonts or pictures from
another site, and connections go only to this origin, so script that ever got
injected into a page has no channel out. `codec_dashboard.CSPMiddleware` sends
`for_request(path, host)` as Content-Security-Policy and `PERMISSIONS_POLICY`
as Permissions-Policy on every HTML answer.
"""
from __future__ import annotations

import re

CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self'; "
    "img-src 'self' data:; "
    # Plus ws:// and wss:// for the request's own Host (for_request): the voice call.
    "connect-src 'self'; "
    "frame-src 'self'; "
    "worker-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    # Phase 0 (2026-05-27): embedding only from the same origin or the AVA
    # Digital portal, so /console/codec can frame the dashboard.
    "frame-ancestors 'self' https://avadigital.ai"
)

# /vibe only. AI-written previews (srcdoc frames, which inherit the page's policy)
# load libraries from cdnjs and pictures from the web, and the page frames a
# project that runs on this Mac, always at 127.0.0.1. Monaco itself is served
# from /static (D-24).
CSP_VIBE = (CSP
            .replace("script-src 'self' 'unsafe-inline';", "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com;")
            .replace("style-src 'self' 'unsafe-inline';", "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com;")
            .replace("font-src 'self';", "font-src 'self' https://cdnjs.cloudflare.com;")
            .replace("img-src 'self' data:;", "img-src 'self' data: https:;")
            .replace("frame-src 'self';", "frame-src 'self' http://127.0.0.1:*;"))

# Microphone: the voice call and dictation. Camera: the webcam features.
PERMISSIONS_POLICY = "camera=(self), microphone=(self), geolocation=(), payment=(), usb=()"

# A Host the policy may name: a hostname, an IPv4 address or an IPv6 literal, and an
# optional port. Anything else (spaces, ';', quotes) gets no ws source, never a broken header.
_HOST_RE = re.compile(r"^(?:[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*|\[[0-9A-Fa-f:.]+\])(?::[0-9]{1,5})?$")


def for_request(path: str, host: str) -> str:
    """The policy for one HTML answer: /vibe's or the general one, with ws://
    and wss:// for this request's Host only."""
    csp = CSP_VIBE if (path or "").rstrip("/") == "/vibe" else CSP
    host = (host or "").strip()
    if host and _HOST_RE.match(host):
        csp = csp.replace("connect-src 'self';", f"connect-src 'self' ws://{host} wss://{host};")
    return csp
