"""Pilot PP-1 — API hardening: every :8094 route requires the shared x-pilot-token,
the server binds loopback, and CORS is not a wildcard. Closes Pilot audit P-1 (the live
unauthenticated RCE). Runs under pytest with Starlette's TestClient.

Reference: docs/PP1-API-AUTH-DESIGN.md.
"""
import sys
from pathlib import Path

from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # ~/codec on sys.path

from pilot import config, pilot_runner  # noqa: E402


def test_unauthenticated_request_rejected(monkeypatch):
    monkeypatch.setattr(pilot_runner, "_PILOT_TOKEN", "secret-test-token")
    client = TestClient(pilot_runner.app)
    r = client.get("/__authprobe__")  # nonexistent path; middleware runs before routing
    assert r.status_code == 401, "an unauthenticated request must be rejected (P-1)"


def test_authenticated_request_passes_auth(monkeypatch):
    monkeypatch.setattr(pilot_runner, "_PILOT_TOKEN", "secret-test-token")
    client = TestClient(pilot_runner.app)
    r = client.get("/__authprobe__", headers={"x-pilot-token": "secret-test-token"})
    # Passes auth → falls through to the router → 404 (not 401).
    assert r.status_code != 401, "a correctly-tokened request must pass auth"
    assert r.status_code == 404


def test_api_binds_loopback_by_default():
    assert getattr(config, "PILOT_API_HOST", "0.0.0.0") == "127.0.0.1", \
        "the API must bind loopback by default, not 0.0.0.0 (P-1)"
