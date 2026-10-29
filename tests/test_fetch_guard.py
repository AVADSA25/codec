"""P-008: one fetch guard for web_fetch, the crew fetch and the chat URL fetch.

Network-free: names resolve through a patched getaddrinfo, requests gets stub
responses, httpx runs on a MockTransport. No real LAN or Tailscale host is
contacted.
"""
import importlib.util
import io
import os

import httpx
import pytest
import requests

import codec_ssrf

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHUNK = 64 * 1024

# The 30 URLs where the old guard read example.com while requests dialled
# 127.0.0.1 (audit scratch gap-14/fuzz_diff.py).
BYPASSES = [
    "http://127.0.0.1:8090\\@example.com/",
    "http://127.0.0.1:80\\@example.com/",
    "http://127.0.0.1\\@example.com\t/",
    "http://127.0.0.1\\@example.com\n/",
    "http://127.0.0.1\\@example.com\r/",
    "http://127.0.0.1\\@example.com#/",
    "http://127.0.0.1\\@example.com/",
    "http://127.0.0.1\\@example.com//",
    "http://127.0.0.1\\@example.com:/",
    "http://127.0.0.1\\@example.com?/",
] + [f"http://u{sep}@127.0.0.1\\@example.com/" for sep in (
    "\x00", "\t", "\n", "\r", " ", "%00", "%0d%0a", "%2e", "%2f", "%40",
    "%5c", "..", ".", ":", ";", "@", "‥", "。", "．", "＼",
)]


@pytest.fixture
def dns(monkeypatch):
    """Names resolve to a public address, IP literals to themselves."""
    table = {}

    def gai(host, port, *a, **k):
        ip = table.get(host) or ("93.184.216.34" if any(c.isalpha() for c in host) else host)
        return [(2, 1, 6, "", (ip, port))]

    monkeypatch.setattr(codec_ssrf.socket, "getaddrinfo", gai)
    return table


def test_thirty_bypass_variants():
    assert len(set(BYPASSES)) == 30


@pytest.mark.parametrize("url", BYPASSES)
def test_bypass_variant_refused(dns, url):
    with pytest.raises(codec_ssrf.SSRFError):
        codec_ssrf.validate_url(url)


@pytest.mark.parametrize("url", ["https://example.com/a?b=c#d", "http://example.com:8080/x",
                                 "https://user:pw@example.com/", "https://bücher.de/"])
def test_ordinary_urls_still_pass(dns, url):
    assert codec_ssrf.validate_url(url) == url


@pytest.mark.parametrize("addr", [
    "100.64.0.1", "100.100.100.100", "100.127.255.254",       # Tailscale / CGNAT
    "::ffff:127.0.0.1", "64:ff9b::7f00:1", "2002:7f00:1::",   # embedded IPv4
    "::7f00:1", "64:ff9b::a00:1", "2002:a9fe:a9fe::",
])
def test_non_public_addresses_refused(dns, addr):
    dns["tail.example"] = addr
    with pytest.raises(codec_ssrf.SSRFError):
        codec_ssrf.validate_url("http://tail.example/")


def test_chat_guard_shares_the_rules(dns):
    import routes.chat as chat
    dns["tail.example"] = "100.101.102.103"
    assert chat._url_host_is_public("http://tail.example/") is False
    assert chat._url_host_is_public("http://100.64.0.1/") is False
    assert chat._url_host_is_public(BYPASSES[0]) is False
    assert chat._url_host_is_public("https://example.com/") is True


def test_nat64_of_a_public_address_passes(dns):
    dns["v6.example"] = "64:ff9b::808:808"   # carries 8.8.8.8
    assert codec_ssrf.validate_url("http://v6.example/")


# --- 2 MB / 15 s caps ----------------------------------------------------------

class _Endless:
    """A body that never ends; counts the chunks handed out."""

    def __init__(self):
        self.n = 0

    def __iter__(self):
        while True:
            self.n += 1
            yield b"a" * CHUNK


class _FakeClock:
    def __init__(self, step):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def _web_fetch_skill():
    spec = importlib.util.spec_from_file_location(
        "_wf_cap", os.path.join(REPO, "skills", "web_fetch.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _requests_stub(body):
    def fake_get(url, **kw):
        assert kw.get("stream") is True and kw.get("allow_redirects") is False
        r = requests.Response()
        r.status_code = 200
        r.headers["content-type"] = "text/plain; charset=utf-8"
        r.raw = io.BytesIO(b"")
        r.iter_content = lambda n=1, **k: iter(body)
        return r
    return fake_get


def test_web_fetch_stops_near_2mb(dns, monkeypatch):
    wf = _web_fetch_skill()
    body = _Endless()
    monkeypatch.setattr(wf.requests, "get", _requests_stub(body))
    out = wf.run("fetch https://example.com/big")
    assert f"truncated, {codec_ssrf.MAX_FETCH_BYTES} raw chars" in out
    assert body.n <= codec_ssrf.MAX_FETCH_BYTES // CHUNK + 1


def test_web_fetch_stops_near_15s(dns, monkeypatch):
    wf = _web_fetch_skill()
    body = _Endless()
    monkeypatch.setattr(codec_ssrf.time, "monotonic", _FakeClock(1.0))
    monkeypatch.setattr(wf.requests, "get", _requests_stub(body))
    out = wf.run("fetch https://example.com/slow")
    assert "truncated" in out
    assert body.n <= 16


def _mock_client(body, **kw):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"},
                              content=iter(body))
    return httpx.Client(transport=httpx.MockTransport(handler), **kw)


def test_crew_fetch_stops_near_2mb(dns, monkeypatch):
    import codec_agents
    body = _Endless()
    monkeypatch.setattr(codec_agents, "_sync_http", _mock_client(body))
    out = codec_agents._web_fetch("https://example.com/big")
    assert f"page was {codec_ssrf.MAX_FETCH_BYTES} chars" in out
    assert body.n <= codec_ssrf.MAX_FETCH_BYTES // CHUNK + 1


def test_crew_fetch_stops_near_15s(dns, monkeypatch):
    import codec_agents
    body = _Endless()
    monkeypatch.setattr(codec_ssrf.time, "monotonic", _FakeClock(1.0))
    monkeypatch.setattr(codec_agents, "_sync_http", _mock_client(body))
    codec_agents._web_fetch("https://example.com/slow")
    assert body.n <= 16


def test_chat_fetch_stops_near_2mb(dns, monkeypatch):
    import routes.chat as chat
    body = _Endless()
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: _mock_client(body, **kw))
    try:
        chat._fetch_url_content("https://example.com/big")
    finally:
        monkeypatch.setattr(httpx, "Client", real)
    assert body.n <= codec_ssrf.MAX_FETCH_BYTES // CHUNK + 1


def test_budget_timeout_raises_when_spent(monkeypatch):
    clock = _FakeClock(20.0)
    monkeypatch.setattr(codec_ssrf.time, "monotonic", clock)
    budget = codec_ssrf.FetchBudget()
    with pytest.raises(codec_ssrf.FetchTimeout):
        budget.timeout()
