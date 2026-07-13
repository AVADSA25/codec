"""Pilot health check must be authenticated (the "half-broken" root cause).

2026-07: Pilot appeared dead — every teach/replay command returned "Pilot
Runner is not running" even though the pilot-runner was online and healthy. The
runner requires x-pilot-token on EVERY endpoint including /health (401 without),
but _pilot_up() sent no header, so it always saw 401 → False. These tests pin
that _pilot_up sends the token, and that a genuine outage still reads as down.
"""
from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "skills"))

import pilot  # noqa: E402


def test_pilot_up_sends_the_token(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=0):
        seen["header"] = req.headers.get("X-pilot-token")

        class _R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return _R()

    monkeypatch.setattr(pilot, "_pilot_token", lambda: "SECRET123")
    monkeypatch.setattr(pilot.urllib.request, "urlopen", fake_urlopen)
    assert pilot._pilot_up() is True
    assert seen["header"] == "SECRET123", "health check must send x-pilot-token"


def test_pilot_up_false_on_401(monkeypatch):
    """A real 401 (bad/absent token) still reads as down — fail-closed."""
    def raise_401(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
    monkeypatch.setattr(pilot, "_pilot_token", lambda: "")
    monkeypatch.setattr(pilot.urllib.request, "urlopen", raise_401)
    assert pilot._pilot_up() is False


def test_pilot_up_false_when_service_down(monkeypatch):
    def refuse(req, timeout=0):
        raise ConnectionError("connection refused")
    monkeypatch.setattr(pilot, "_pilot_token", lambda: "x")
    monkeypatch.setattr(pilot.urllib.request, "urlopen", refuse)
    assert pilot._pilot_up() is False


def test_run_reports_not_running_when_down(monkeypatch):
    monkeypatch.setattr(pilot, "_pilot_up", lambda: False)
    out = pilot.run("pilot status")
    assert "not running" in out.lower()


def test_no_emoji_in_pilot_output():
    """No-emoji rule: pilot.py must not contain pictographic emoji."""
    src = (REPO / "skills" / "pilot.py").read_text()
    offenders = sorted({c for c in src if ord(c) > 0x2600})
    assert not offenders, f"pictographic emoji in pilot.py: {offenders}"
