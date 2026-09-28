"""/static serves the shared UI assets (UI phase 1, PR-B).

The prefix is auth-public, so the route must serve only files inside static/:
no directory listing, no path traversal, no dotfiles.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import codec_config
import codec_dashboard
import routes._shared as shared
from codec_dashboard import app

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def pin_login_required(monkeypatch):
    """A PIN login is configured and the client has no session."""
    monkeypatch.setattr(codec_config, "get_dashboard_token", lambda: "")
    monkeypatch.setattr(codec_dashboard, "AUTH_ENABLED", True)
    monkeypatch.setattr(shared, "AUTH_ENABLED", True)
    monkeypatch.setattr(codec_dashboard, "_auth_available", lambda: True)
    return TestClient(app, client=("127.0.0.1", 5000))


def test_stylesheet_is_served_without_login(pin_login_required):
    r = pin_login_required.get("/static/codec.css")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/css")
    assert r.headers["cache-control"] == "public, max-age=3600"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "--fs-scale" in r.text


def test_logo_is_an_svg(pin_login_required):
    r = pin_login_required.get("/static/logo.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")


def test_font_is_served_without_login(pin_login_required):
    r = pin_login_required.get("/static/fonts/IBMPlexSans-Regular.woff2")
    assert r.status_code == 200
    assert r.headers["content-type"] == "font/woff2"


def test_vendored_dompurify_is_served_without_login(pin_login_required):
    r = pin_login_required.get("/static/vendor/purify.min.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")


def test_csp_drops_google_fonts_but_keeps_cdnjs(pin_login_required):
    # PR-C self-hosts IBM Plex (static/codec.css), so the CSP no longer needs
    # the Google Fonts origins; cdnjs.cloudflare.com stays for Vibe's Monaco.
    r = pin_login_required.get("/")
    csp = r.headers["content-security-policy"]
    assert "fonts.googleapis.com" not in csp
    assert "fonts.gstatic.com" not in csp
    assert "cdnjs.cloudflare.com" in csp


def test_the_login_still_guards_everything_else(pin_login_required):
    assert pin_login_required.get("/api/config").status_code == 401
    # "/static" without the slash is not the public prefix: a page path goes to login.
    r = pin_login_required.get("/staticfoo", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/auth"


@pytest.mark.parametrize("host", ["[::1]/static", "[::1]:8090/static", "[::1]/api/auth/"])
def test_a_crafted_host_cannot_borrow_a_public_prefix(pin_login_required, monkeypatch, host):
    """Starlette rebuilds request.url from the Host header, so `Host: [::1]/static`
    once turned /api/config into /static/api/config. The Host check refuses it,
    and the login decides on the routed path even if a Host slipped through."""
    client = pin_login_required
    assert client.get("/api/config", headers={"host": host}).status_code == 400
    monkeypatch.setattr(codec_dashboard, "_host_allowed", lambda h: True)
    assert client.get("/api/config", headers={"host": host}).status_code == 401


@pytest.mark.parametrize("path", [
    "/api/agents/x.js", "/api/triggers/x.css", "/api/image/file/aaaaaaaaaaaa/x.png",
])
def test_a_file_extension_does_not_skip_the_login(pin_login_required, path):
    assert pin_login_required.get(path).status_code == 401


def test_favicon_is_still_public(pin_login_required):
    assert pin_login_required.get("/favicon.png").status_code != 401


@pytest.mark.parametrize("path, target", [
    ("/static/../codec_dashboard.py", "codec_dashboard.py"),
    ("/static/..%2fcodec_dashboard.py", "codec_dashboard.py"),
    ("/static/..%2Froutes%2F_shared.py", "routes/_shared.py"),
    ("/static/%2e%2e/codec_dashboard.py", "codec_dashboard.py"),
    ("/static/%2e%2e%2fcodec_dashboard.py", "codec_dashboard.py"),
])
def test_traversal_never_returns_source(pin_login_required, path, target):
    source = (REPO / target).read_text()
    r = pin_login_required.get(path, follow_redirects=False)
    assert r.status_code != 200
    assert source[:300] not in r.text


def test_missing_file_dotfile_directory_and_unknown_type_are_404(pin_login_required):
    client = pin_login_required
    assert client.get("/static/nope.css").status_code == 404
    assert client.get("/static/").status_code == 404
    assert client.get("/static/.DS_Store").status_code == 404
    assert client.get("/static/codec.css.bak").status_code == 404
    assert client.get("/static/codec.css%00.png").status_code == 404


def test_unknown_host_is_still_refused():
    r = TestClient(app).get("/static/codec.css", headers={"host": "rebind.evil.example"})
    assert r.status_code == 400


def test_static_links_carry_the_current_content_version():
    """Pages are no-cache but /static is cached for an hour; a stale ?v= would
    pair a new page with an old stylesheet. Fix: python3 tools/stamp_static.py"""
    import subprocess, sys as _sys
    from pathlib import Path as _P
    repo = _P(__file__).resolve().parent.parent
    r = subprocess.run([_sys.executable, str(repo / "tools" / "stamp_static.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
