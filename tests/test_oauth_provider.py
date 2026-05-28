"""Unit tests for PersistentOAuthProvider.

Verifies tokens and clients survive a simulated restart via disk persistence
AND the PR-2B Keychain-encrypted state path.
"""
import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mcp.shared.auth import OAuthClientInformationFull
from mcp.server.auth.settings import ClientRegistrationOptions
from pydantic import AnyUrl

from codec_oauth_provider import PersistentOAuthProvider


@pytest.fixture(autouse=True)
def _isolate_oauth_keychain(tmp_path, monkeypatch):
    """PR-2B: PersistentOAuthProvider now stores state in Keychain. Isolate
    the test's Keychain writes from the user's real Keychain by:
      - Forcing the fallback backend (no real macOS Keychain side effects)
      - Redirecting fallback storage paths to tmp_path
    """
    import codec_keychain as kc
    monkeypatch.setattr(kc, "is_keychain_available", lambda: False)
    monkeypatch.setattr(kc, "_FALLBACK_KEY_PATH", tmp_path / "kc_secret.key")
    monkeypatch.setattr(kc, "_FALLBACK_STORE_PATH", tmp_path / "kc_secrets.enc.json")
    yield


def _make_client(cid="test-client"):
    return OAuthClientInformationFull(
        client_id=cid,
        client_secret="s",
        redirect_uris=[AnyUrl("https://example.com/cb")],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope="read",
    )


def test_client_persists_across_restart(tmp_path):
    state = tmp_path / "state.json"

    p1 = PersistentOAuthProvider(
        base_url="https://test.example.com",
        client_registration_options=ClientRegistrationOptions(enabled=True),
        state_path=state,
    )
    asyncio.run(p1.register_client(_make_client("alpha")))
    # PR-2B (D-8): state lives in Keychain now (fallback in tests).
    # Legacy `state.exists()` check no longer applies — Keychain is the
    # source of truth.
    import codec_keychain
    blob = codec_keychain.get_oauth_state()
    assert blob is not None, "OAuth state must be persisted to Keychain"
    assert "alpha" in blob

    # Simulate restart
    p2 = PersistentOAuthProvider(
        base_url="https://test.example.com",
        client_registration_options=ClientRegistrationOptions(enabled=True),
        state_path=state,
    )
    loaded = asyncio.run(p2.get_client("alpha"))
    assert loaded is not None
    assert loaded.client_id == "alpha"


def test_expired_tokens_pruned_on_load(tmp_path):
    import json
    state = tmp_path / "state.json"
    # Pre-seed an expired token on disk
    state.write_text(json.dumps({
        "clients": {},
        "access_tokens": {
            "dead": {
                "token": "dead",
                "client_id": "x",
                "scopes": [],
                "expires_at": 1,  # ancient
            }
        },
        "refresh_tokens": {},
        "access_to_refresh": {},
        "refresh_to_access": {},
    }))
    p = PersistentOAuthProvider(
        base_url="https://test.example.com",
        state_path=state,
    )
    assert "dead" not in p.access_tokens


def test_fallback_write_is_fsync_durable(tmp_path, monkeypatch):
    """C6: when Keychain (and its encrypted fallback) cannot persist, _save
    writes the plaintext oauth_state.json via a CRASH-DURABLE path — unique
    tmp + fsync + atomic replace + 0600. Before the fix the fallback did
    `tmp.write_text(blob); os.replace(...)` with NO fsync, so a crash between
    write() and the page-cache flush could land a truncated/empty file and
    lose every OAuth token (claude.ai forced re-auth on next restart).

    We assert the durability behaviour directly: os.fsync MUST be invoked on
    the fallback write, and the resulting file must round-trip + be 0600.
    Reference: docs/SECURITY-REMEDIATION-DESIGN.md Fix #1b.
    """
    import json
    import os
    import stat

    import codec_keychain

    state = tmp_path / "oauth_state.json"

    # Force the plaintext fallback path: Keychain (and its encrypted fallback)
    # report they could not persist this write.
    monkeypatch.setattr(codec_keychain, "set_oauth_state", lambda blob: False)

    # Spy on fsync to prove the write is flushed to stable storage before the
    # atomic replace. Delegate to the real fsync so behaviour is preserved.
    fsync_calls = {"n": 0}
    real_fsync = os.fsync

    def _counting_fsync(fd):
        fsync_calls["n"] += 1
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", _counting_fsync)

    p = PersistentOAuthProvider(
        base_url="https://test.example.com",
        client_registration_options=ClientRegistrationOptions(enabled=True),
        state_path=state,
    )
    asyncio.run(p.register_client(_make_client("durable")))

    assert fsync_calls["n"] >= 1, (
        "Fallback oauth_state.json write must fsync before replace (C6); "
        "no fsync was observed during _save"
    )
    # Round-trips + correct perms.
    assert state.exists(), "fallback must persist the plaintext state file"
    data = json.loads(state.read_text())
    assert "durable" in data.get("clients", {}), (
        "persisted state must contain the registered client"
    )
    mode = stat.S_IMODE(os.stat(state).st_mode)
    assert mode == 0o600, f"fallback file must be 0600; got {oct(mode)}"


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        test_client_persists_across_restart(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_expired_tokens_pruned_on_load(Path(d))
    print("OAuth provider tests passed.")
