"""Fix #7a (H1/H2/H6): SSRF guard for outbound URL fetches.

codec_ssrf.validate_url(url) must reject URLs that would let an
attacker-supplied link (chat injection / clipboard / crew task) reach
internal services or the cloud metadata endpoint, while allowing ordinary
public hosts.

Network-free by design: IP-literal + bad-scheme cases need no DNS; the
allow / mixed-resolution cases monkeypatch socket.getaddrinfo.
"""
import pytest

import codec_ssrf


# IP literals + bad schemes — no DNS lookup needed, fully deterministic.
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://[::1]/",
        "http://0.0.0.0/",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://evil/",
        "",
        "not-a-url",
    ],
)
def test_validate_url_rejects_unsafe(url):
    with pytest.raises(codec_ssrf.SSRFError):
        codec_ssrf.validate_url(url)


def test_validate_url_allows_public_host(monkeypatch):
    monkeypatch.setattr(
        codec_ssrf.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    assert codec_ssrf.validate_url("https://example.com/page") == "https://example.com/page"


def test_validate_url_rejects_when_any_resolved_ip_is_internal(monkeypatch):
    # DNS-rebinding-style defense: a hostname that resolves to BOTH a public
    # and an internal address must be rejected (ANY blocked → reject).
    monkeypatch.setattr(
        codec_ssrf.socket,
        "getaddrinfo",
        lambda *a, **k: [
            (2, 1, 6, "", ("93.184.216.34", 80)),
            (2, 1, 6, "", ("169.254.169.254", 80)),
        ],
    )
    with pytest.raises(codec_ssrf.SSRFError):
        codec_ssrf.validate_url("http://rebind.example/")


def test_validate_url_rejects_dns_failure(monkeypatch):
    import socket as _socket

    def _boom(*a, **k):
        raise _socket.gaierror("name does not resolve")

    monkeypatch.setattr(codec_ssrf.socket, "getaddrinfo", _boom)
    with pytest.raises(codec_ssrf.SSRFError):
        codec_ssrf.validate_url("http://nonexistent.invalid/")


# ── Wiring: the guard must run BEFORE the HTTP client at every fetch site ────
def _load_web_fetch_skill():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "skills" / "web_fetch.py"
    spec = importlib.util.spec_from_file_location("_wf_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_web_fetch_skill_does_not_request_blocked_url(monkeypatch):
    web_fetch = _load_web_fetch_skill()
    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("requests.get must not be called for a blocked URL")

    monkeypatch.setattr(web_fetch.requests, "get", _boom)
    result = web_fetch.run("fetch http://169.254.169.254/latest/meta-data/")
    assert calls["n"] == 0, "web_fetch reached the network for an SSRF-blocked URL"
    assert "block" in result.lower(), f"expected an SSRF-block message, got: {result!r}"


def test_agents_web_fetch_tool_does_not_request_blocked_url(monkeypatch):
    import codec_agents

    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("_sync_http.get must not be called for a blocked URL")

    monkeypatch.setattr(codec_agents._sync_http, "get", _boom)
    result = codec_agents._web_fetch("http://127.0.0.1/internal")
    assert calls["n"] == 0, "_web_fetch reached the network for an SSRF-blocked URL"
    assert "block" in result.lower(), f"expected an SSRF-block message, got: {result!r}"
