"""codec_mcp_http /health OAuth check reflects where OAuth state really lives.

PR-2B moved OAuth state into Keychain and deletes ~/.codec/oauth_state.json
after migration, but /health still tested that file, so it reported
"degraded" (503) forever. `_oauth_state_status` now follows the provider's
own load order (Keychain / fallback store, then the legacy file) and also
accepts clients already loaded in the running provider. It must never return
the state itself.

Import stubs: same contained sys.modules pattern as tests/test_rate_window.py.
"""
from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) in sys.path:
    sys.path.remove(str(_REPO))
sys.path.insert(0, str(_REPO))

_STUB_MODULES = [
    "mcp", "mcp.server", "mcp.server.auth", "mcp.server.auth.settings",
    "mcp.server.auth.provider", "mcp.shared", "mcp.shared.auth", "codec_mcp",
    "fastmcp", "fastmcp.server", "fastmcp.server.auth",
    "fastmcp.server.auth.providers", "fastmcp.server.auth.providers.in_memory",
]

_SECRET = "codec_at_" + "f" * 64


class _Stub(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return type(name, (), {})


@pytest.fixture
def mcp_http(monkeypatch):
    for n in _STUB_MODULES:
        monkeypatch.setitem(sys.modules, n, _Stub(n))
    for m in ("codec_mcp_http", "codec_oauth_provider"):
        sys.modules.pop(m, None)
    mod = importlib.import_module("codec_mcp_http")
    try:
        yield mod
    finally:
        for m in ("codec_mcp_http", "codec_oauth_provider"):
            sys.modules.pop(m, None)


@pytest.fixture
def keychain(monkeypatch):
    """Point codec_keychain at a controllable in-test value."""
    import codec_keychain
    box = {"blob": None, "available": True}
    monkeypatch.setattr(codec_keychain, "get_oauth_state", lambda: box["blob"])
    monkeypatch.setattr(codec_keychain, "is_keychain_available", lambda: box["available"])
    return box


def _state_blob() -> str:
    return json.dumps({"clients": {"c1": {}}, "access_tokens": {_SECRET: {}}})


def test_keychain_state_is_ok_without_legacy_file(mcp_http, keychain, tmp_path):
    keychain["blob"] = _state_blob()
    missing_file = str(tmp_path / "oauth_state.json")
    assert mcp_http._oauth_state_status(state_file=missing_file) == ("ok", "keychain")


def test_fallback_store_reported_when_keychain_unavailable(mcp_http, keychain, tmp_path):
    keychain["blob"] = _state_blob()
    keychain["available"] = False
    assert mcp_http._oauth_state_status(state_file=str(tmp_path / "x.json")) == ("ok", "fallback")


def test_legacy_plaintext_file_still_counts(mcp_http, keychain, tmp_path):
    legacy = tmp_path / "oauth_state.json"
    legacy.write_text("{}")
    assert mcp_http._oauth_state_status(state_file=str(legacy)) == ("ok", "legacy_file")


def test_in_memory_clients_count_when_store_unreadable(mcp_http, keychain, tmp_path):
    provider = types.SimpleNamespace(clients={"c1": object()})
    status = mcp_http._oauth_state_status(provider, state_file=str(tmp_path / "x.json"))
    assert status == ("ok", "memory")


def test_nothing_anywhere_is_missing(mcp_http, keychain, tmp_path):
    provider = types.SimpleNamespace(clients={})
    status = mcp_http._oauth_state_status(provider, state_file=str(tmp_path / "x.json"))
    assert status == ("missing", "none")


def test_corrupt_keychain_blob_reported(mcp_http, keychain, tmp_path):
    keychain["blob"] = "not json"
    assert mcp_http._oauth_state_status(state_file=str(tmp_path / "x.json"))[0] == "corrupt"


def test_keychain_error_does_not_raise(mcp_http, monkeypatch, tmp_path):
    import codec_keychain

    def _boom():
        raise OSError("security binary gone")
    monkeypatch.setattr(codec_keychain, "get_oauth_state", _boom)
    assert mcp_http._oauth_state_status(state_file=str(tmp_path / "x.json")) == ("missing", "none")


def test_status_never_contains_state_content(mcp_http, keychain, tmp_path):
    keychain["blob"] = _state_blob()
    result = mcp_http._oauth_state_status(state_file=str(tmp_path / "x.json"))
    assert _SECRET not in repr(result)
    assert all(isinstance(part, str) and len(part) < 20 for part in result)


def test_cached_status_limits_keychain_reads(mcp_http, monkeypatch):
    calls = []

    def _fake(provider=None):
        calls.append(1)
        return ("ok", "keychain")
    monkeypatch.setattr(mcp_http, "_oauth_state_status", _fake)
    assert mcp_http._oauth_state_status_cached() == ("ok", "keychain")
    assert mcp_http._oauth_state_status_cached() == ("ok", "keychain")
    assert len(calls) == 1, "a second /health inside the TTL must not re-read Keychain"
    mcp_http._OAUTH_CHECK_CACHE["ts"] = 0.0  # expire
    mcp_http._oauth_state_status_cached()
    assert len(calls) == 2
