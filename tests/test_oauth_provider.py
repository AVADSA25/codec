"""Unit tests for PersistentOAuthProvider.

Verifies tokens and clients survive a simulated restart via disk persistence.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mcp.server.auth.provider import AuthorizationCode
from mcp.shared.auth import OAuthClientInformationFull
from mcp.server.auth.settings import ClientRegistrationOptions
from pydantic import AnyUrl

from codec_oauth_provider import PersistentOAuthProvider


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
    assert state.exists()
    assert "alpha" in state.read_text()

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


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        test_client_persists_across_restart(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_expired_tokens_pruned_on_load(Path(d))
    print("OAuth provider tests passed.")
