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

P-008 (owner decision D-18, Oct 2026):
- a URL with a backslash, a control character or a space is refused, and so
  is any URL whose host as read here differs from the host requests/urllib3
  or httpx would dial (the backslash-before-@ bypass, 30 variants);
- every non-global address is refused, including 100.64.0.0/10 (Tailscale
  and carrier NAT) and IPv4 embedded in IPv6 (::ffff:0:0/96, 64:ff9b::/96,
  2002::/16, ::/96), checked as the IPv4 address it carries;
- FetchBudget / read_capped cap one fetch, redirects included, at
  MAX_FETCH_BYTES and MAX_FETCH_SECONDS. All three fetchers use them
  (skills/web_fetch.py, codec_agents._web_fetch, routes/chat.py).
"""
import ipaddress
import re
import socket
import time
from urllib.parse import urlparse

__all__ = ["SSRFError", "FetchTimeout", "FetchBudget", "read_capped",
           "validate_url", "MAX_FETCH_BYTES", "MAX_FETCH_SECONDS"]

_ALLOWED_SCHEMES = {"http", "https"}
_BAD_URL_CHARS = re.compile(r"[\x00-\x20\x7f\\]")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_V4_COMPAT = ipaddress.ip_network("::/96")

MAX_FETCH_BYTES = 2 * 1024 * 1024
MAX_FETCH_SECONDS = 15.0
_STEP_SECONDS = 5.0   # connect / read timeout per network step


class SSRFError(Exception):
    """Raised when a URL is rejected by the SSRF guard."""


class FetchTimeout(TimeoutError):
    """Raised when one fetch, redirects included, passes MAX_FETCH_SECONDS."""


def _embedded_ipv4(ip):
    """The IPv4 address an IPv6 address carries, or None."""
    if ip.version != 6:
        return None
    if ip.ipv4_mapped is not None:      # ::ffff:0:0/96
        return ip.ipv4_mapped
    if ip.sixtofour is not None:        # 2002::/16
        return ip.sixtofour
    if ip in _NAT64 or ip in _V4_COMPAT:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable address → block (fail closed)
    embedded = _embedded_ipv4(ip)
    if embedded is not None:
        return _is_blocked_ip(str(embedded))
    return (
        not ip.is_global
        or (ip.version == 4 and ip in _CGNAT)
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _norm_host(host):
    h = (host or "").strip("[]").rstrip(".").lower()
    try:
        return h.encode("idna").decode("ascii")
    except Exception:
        return h


def _client_hosts(url: str):
    """The hosts requests (urllib3) and httpx would dial for this URL."""
    hosts = []
    try:
        from urllib3.util import parse_url
    except ImportError:
        parse_url = None
    if parse_url is not None:
        try:
            hosts.append(parse_url(url).host)
        except Exception as e:
            raise SSRFError(f"URL cannot be parsed ({type(e).__name__})") from e
    try:
        import httpx
    except ImportError:
        httpx = None
    if httpx is not None:
        try:
            hosts.append(httpx.URL(url).host)
        except Exception as e:
            raise SSRFError(f"URL cannot be parsed ({type(e).__name__})") from e
    return hosts


class FetchBudget:
    """Time and size budget for one fetch, shared by its redirect hops."""

    def __init__(self, seconds: float = MAX_FETCH_SECONDS,
                 max_bytes: int = MAX_FETCH_BYTES):
        self.deadline = time.monotonic() + seconds
        self.max_bytes = max_bytes

    def expired(self) -> bool:
        return time.monotonic() >= self.deadline

    def timeout(self) -> float:
        """Timeout for the next network step; FetchTimeout once spent."""
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise FetchTimeout(f"fetch took longer than {MAX_FETCH_SECONDS:g} s")
        return min(_STEP_SECONDS, left)


def read_capped(chunks, budget: FetchBudget):
    """Read body chunks until the end, max_bytes or the deadline.

    Returns (body bytes, truncated flag)."""
    buf = bytearray()
    for chunk in chunks:
        buf += chunk
        if len(buf) >= budget.max_bytes:
            return bytes(buf[:budget.max_bytes]), True
        if budget.expired():
            return bytes(buf), True
    return bytes(buf), False


def validate_url(url: str) -> str:
    """Return `url` if safe to fetch; raise `SSRFError` otherwise."""
    if not url or not isinstance(url, str):
        raise SSRFError("empty or non-string URL")

    url = url.strip()
    if _BAD_URL_CHARS.search(url):
        raise SSRFError("URL contains a backslash, a space or a control character")
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SSRFError(f"scheme '{parsed.scheme}' not allowed (only http/https)")

    host = parsed.hostname
    if not host:
        raise SSRFError("URL has no host")
    for dialled in _client_hosts(url):
        if _norm_host(dialled) != _norm_host(host):
            raise SSRFError("URL host is read differently by the HTTP client")

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
