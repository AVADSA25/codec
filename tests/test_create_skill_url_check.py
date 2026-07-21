"""Generated skills must not call hosts that don't exist.

The 2026-07-21 incident: asked for a moon-phase skill, the generator wrote
`https://api.moon.ph/v1/moon`. That host does not resolve. The code compiled,
passed every safety check, and read perfectly in review — then failed 100% of
the time it ran, returning "Error fetching moon phase data".

A prompt rule ("don't invent URLs") reduces this but cannot catch it: the model
is equally confident either way. The only honest check asks the world. DNS
resolution is the cheapest such check and it is decisive — if the hostname
doesn't resolve, the model made it up.

Deliberately narrow: DNS failure only. A host that resolves but 404s or rate
limits is a live-service question, not a fabrication.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "cs_under_test", _REPO / "skills" / "create_skill.py")
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


def _skill(body: str) -> str:
    return (
        '"""CODEC Skill: T"""\n'
        'SKILL_NAME = "t"\n'
        'SKILL_DESCRIPTION = "test"\n'
        'def run(task, app="", ctx=""):\n'
        f"    {body}\n"
    )


def test_invented_host_is_rejected(monkeypatch):
    """The exact failure from the incident."""
    monkeypatch.setattr(cs, "_unresolvable_hosts", lambda code: ["api.moon.ph"])
    ok, err = cs._validate_skill_code(
        _skill('import requests; return requests.get("https://api.moon.ph/v1/moon").text'))
    assert ok is False
    assert "api.moon.ph" in err
    assert "does not exist" in err


def test_real_host_passes(monkeypatch):
    monkeypatch.setattr(cs, "_unresolvable_hosts", lambda code: [])
    ok, err = cs._validate_skill_code(
        _skill('import requests; return requests.get("https://api.github.com/zen").text'))
    assert ok is True, err


def test_local_computation_passes(monkeypatch):
    """No URLs at all — the preferred shape — must never be blocked."""
    monkeypatch.setattr(cs, "_unresolvable_hosts", lambda code: [])
    ok, err = cs._validate_skill_code(_skill("import math; return str(math.pi)"))
    assert ok is True, err


def test_checker_crash_does_not_fail_the_skill(monkeypatch):
    """A bug in the checker must never block a legitimate skill."""
    def boom(code):
        raise RuntimeError("resolver exploded")
    monkeypatch.setattr(cs, "_unresolvable_hosts", boom)
    ok, _ = cs._validate_skill_code(_skill("return 'hi'"))
    assert ok is True


def test_offline_skips_the_check(monkeypatch):
    """With no DNS at all the check is meaningless — it must not fail
    every skill on a laptop in flight mode."""
    import socket

    def no_dns(host, port):
        raise OSError("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    assert cs._unresolvable_hosts('requests.get("https://totally-made-up-xyz.tld/a")') == []


def test_placeholder_hosts_are_ignored(monkeypatch):
    """example.com is a documentation placeholder, not a claim about the world."""
    import socket
    calls = []

    def fake(host, port):
        calls.append(host)
        if host == "one.one.one.one":
            return [("ok",)]
        raise OSError("nope")

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    assert cs._unresolvable_hosts('requests.get("https://example.com/x")') == []
    assert "example.com" not in calls


def test_real_incident_file_is_caught():
    """End-to-end against the genuine artifact, using real DNS.
    Skipped when offline."""
    import socket
    try:
        socket.getaddrinfo("one.one.one.one", 443)
    except OSError:
        pytest.skip("offline")
    code = _skill('import requests; return requests.get("https://api.moon.ph/v1/moon").text')
    assert cs._unresolvable_hosts(code) == ["api.moon.ph"]
