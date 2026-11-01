"""P-056: Settings > About. The version route, the notices route and the page block."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
PAGE = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")


@pytest.fixture
def client():
    from routes.about import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_about_reports_the_release_version_and_serves_the_notices_as_text(client):
    r = client.get("/api/about")
    assert r.status_code == 200
    assert r.json() == {"version": (REPO / "VERSION").read_text().strip(), "notices": True}
    n = client.get("/api/about/notices")
    assert n.status_code == 200 and n.headers["content-type"].startswith("text/plain")
    assert n.headers["x-content-type-options"] == "nosniff"
    assert n.text.startswith("# Third-party notices")


def test_without_the_file_the_route_says_so_and_the_flag_is_false(client, monkeypatch):
    import routes.about as about
    monkeypatch.setattr(about, "notices_path", lambda: None)
    assert client.get("/api/about").json()["notices"] is False
    assert client.get("/api/about/notices").status_code == 404


def test_the_dashboard_serves_the_route_and_the_page_has_the_block():
    assert "app.include_router(about_router)" in (REPO / "codec_dashboard.py").read_text(encoding="utf-8")
    assert 'id="aboutSection"' in PAGE and 'href="/api/about/notices"' in PAGE and 'rel="noopener"' in PAGE
    assert "loadUsage(); loadVersionInfo(); }" in PAGE, "the Settings tab does not load the About block"
    assert PAGE.count("function loadVersionInfo(") == 1, "another function with this name would replace it"
    body = PAGE.split("async function loadVersionInfo()", 1)[1].split("async function loadUsage()", 1)[0]
    assert "textContent" in body and "innerHTML" not in body, "version text must be drawn as text"
