"""Dashboard web hardening stage 0 (P-089, docs/DASHBOARD-CSP-STAGE0-DESIGN.md).

The page policy has no outside sources except on /vibe, connect-src names only this
origin and its own Host's ws/wss, the old preview routes are gone, and a chat link
cannot steer the page's session fetch onto another route.
"""
from __future__ import annotations


import pytest
from fastapi.testclient import TestClient

import codec_config
import codec_dashboard
import routes._shared as shared
from codec_dashboard import CSPMiddleware, app

PAGES = ["/", "/chat", "/voice", "/tasks", "/cortex", "/auth"]

@pytest.fixture
def client(monkeypatch):
    """This Mac, no login configured: every page is served."""
    monkeypatch.setattr(codec_config, "get_dashboard_token", lambda: "")
    monkeypatch.setattr(codec_dashboard, "AUTH_ENABLED", False)
    monkeypatch.setattr(shared, "AUTH_ENABLED", False)
    return TestClient(app, client=("127.0.0.1", 5000))


def directives(csp: str) -> dict:
    out = {}
    for part in csp.split(";"):
        words = part.split()
        if words:
            out[words[0]] = words[1:]
    return out


@pytest.mark.parametrize("path", PAGES)
def test_every_page_but_vibe_gets_the_tight_policy(client, path):
    r = client.get(path, headers={"host": "127.0.0.1:8090"})
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    d = directives(r.headers["content-security-policy"])
    assert d["img-src"] == ["'self'", "data:"]
    assert d["font-src"] == ["'self'"]
    assert d["script-src"] == ["'self'", "'unsafe-inline'"] and d["style-src"] == ["'self'", "'unsafe-inline'"]
    assert d["connect-src"] == ["'self'", "ws://127.0.0.1:8090", "wss://127.0.0.1:8090"]
    assert d["frame-src"] == ["'self'"] and d["worker-src"] == ["'self'", "blob:"]
    assert d["object-src"] == ["'none'"] and d["base-uri"] == ["'none'"] and d["form-action"] == ["'self'"]
    assert d["frame-ancestors"] == ["'self'", "https://avadigital.ai"]
    assert "cdnjs" not in r.headers["content-security-policy"]
    assert r.headers["permissions-policy"] == "camera=(self), microphone=(self), geolocation=(), payment=(), usb=()"


def test_vibe_keeps_its_frames_cdnjs_and_web_pictures_and_nothing_else(client):
    r = client.get("/vibe", headers={"host": "localhost:8090"})
    d = directives(r.headers["content-security-policy"])
    assert d["frame-src"] == ["'self'", "http://127.0.0.1:*"]
    for name in ("script-src", "style-src", "font-src"):
        assert "https://cdnjs.cloudflare.com" in d[name]
    assert d["img-src"] == ["'self'", "data:", "https:"]
    assert d["connect-src"] == ["'self'", "ws://localhost:8090", "wss://localhost:8090"]
    assert d["object-src"] == ["'none'"] and d["base-uri"] == ["'none'"] and d["form-action"] == ["'self'"]
    assert d["worker-src"] == ["'self'", "blob:"] and "permissions-policy" in r.headers


@pytest.mark.parametrize("host, ws", [
    ("127.0.0.1:8090", "ws://127.0.0.1:8090"),
    ("[::1]:8090", "ws://[::1]:8090"),
    ("codec.example.com", "ws://codec.example.com"),
    ("evil.example; script-src *", None),
    ("a b", None),
    ("x'y", None),
    ("", None),
])
def test_the_ws_source_is_the_request_host_or_nothing(host, ws):
    connect = directives(CSPMiddleware.for_request("/chat", host))["connect-src"]
    assert connect == (["'self'", ws, "wss" + ws[2:]] if ws else ["'self'"])


def test_other_answers_get_no_page_policy(client):
    r = client.get("/static/codec.css")
    assert r.status_code == 200
    assert "content-security-policy" not in r.headers and "permissions-policy" not in r.headers
    assert r.headers["x-content-type-options"] == "nosniff"


def test_the_old_preview_routes_are_gone(client):
    assert client.post("/api/preview", json={"code": "<h1>x</h1>"}).status_code == 404
    assert client.get("/preview_frame").status_code == 404
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/preview" not in paths and "/preview_frame" not in paths
