"""codec_ssrf — SSRF guard for outbound URL fetches (Fix #7 / H1·H2·H6).

`validate_url(url)` raises `SSRFError` when a URL is unsafe to fetch, and
returns the URL unchanged when it is safe. It is the shared chokepoint for
every place CODEC fetches an attacker-influenceable URL (the `web_fetch`
skill — which `clipboard_url_fetch` delegates to — and the `_web_fetch`
crew tool).

Rejections:
- scheme not in {http, https} (blocks file://, ftp://, gopher://, …)
- missing host
- the host resolves to ANY non-public address: loopback (127.0.0.1, ::1),
  private (10/8, 172.16/12, 192.168/16, fc00::/7), link-local (incl.
  169.254.169.254 cloud-metadata), multicast, reserved, or unspecified
  (0.0.0.0). Every resolved address is checked, so a dual-record /
  dual-stack host that mixes a public and an internal IP is still rejected.

Limitation (documented): there is a TOCTOU gap between this DNS resolution
and the actual connect() inside requests/httpx — a determined DNS-rebinding
attacker controlling an authoritative server could return a public IP here
and an internal IP at connect time. Closing that fully needs IP-pinned
connections (custom adapter); this guard covers the static-URL and
naive-rebind cases the audit flagged. Keep call sites' own timeouts/size
caps as defence in depth.
"""
import ipaddress
import socket
from urllib.parse import urlparse

__all__ = ["SSRFError", "validate_url"]

_ALLOWED_SCHEMES = {"http", "https"}


class SSRFError(Exception):
    """Raised when a URL is rejected by the SSRF guard."""


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable address → block (fail closed)
    # IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1) — unwrap and re-check.
    if getattr(ip, "ipv4_mapped", None) is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_url(url: str) -> str:
    """Return `url` if safe to fetch; raise `SSRFError` otherwise."""
    if not url or not isinstance(url, str):
        raise SSRFError("empty or non-string URL")

    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SSRFError(f"scheme '{parsed.scheme}' not allowed (only http/https)")

    host = parsed.hostname
    if not host:
        raise SSRFError("URL has no host")

    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise SSRFError(f"DNS resolution failed for {host}: {e}") from e

    addrs = {info[4][0] for info in infos}
    if not addrs:
        raise SSRFError(f"no addresses resolved for {host}")
    for addr in addrs:
        if _is_blocked_ip(addr):
            raise SSRFError(f"host {host!r} resolves to blocked address {addr}")
    return url
