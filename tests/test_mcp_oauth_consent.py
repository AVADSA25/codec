"""Owner gate on CODEC MCP HTTP OAuth: /authorize must not issue a code
until the owner enters the CODEC PIN on /oauth/consent."""
import base64
import hashlib
import secrets
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions

import codec_mcp_consent
import codec_oauth_provider
from codec_mcp_consent import build_consent_routes
from codec_oauth_provider import PersistentOAuthProvider
from codec_pinhash import hash_pin

BASE = "https://mcp.test"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
PIN = "4821"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    import codec_keychain as kc
    monkeypatch.setattr(kc, "is_keychain_available", lambda: False)
    monkeypatch.setattr(kc, "_FALLBACK_KEY_PATH", tmp_path / "kc.key")
    monkeypatch.setattr(kc, "_FALLBACK_STORE_PATH", tmp_path / "kc.json")
    events = []
    monkeypatch.setattr(codec_oauth_provider, "_oauth_log_event", lambda e, *a, **k: events.append(e))
    monkeypatch.setattr(codec_mcp_consent, "_log_event", lambda e, *a, **k: events.append(e))
    yield events


def _app(tmp_path, pin_hash=None):
    provider = PersistentOAuthProvider(
        base_url=BASE,
        client_registration_options=ClientRegistrationOptions(enabled=True),
        state_path=tmp_path / "state.json",
    )
    ph = hash_pin(PIN) if pin_hash is None else pin_hash
    routes = create_auth_routes(
        provider, AnyHttpUrl(BASE),
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
    ) + build_consent_routes(provider, pin_hash_getter=lambda: ph)
    client = TestClient(Starlette(routes=routes), base_url=BASE, follow_redirects=False)
    return provider, client


def _register(client):
    r = client.post("/register", json={
        "redirect_uris": [CALLBACK], "client_name": "Claude",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "none",
    })
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def _start_authorize(client, client_id):
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    r = client.get("/authorize", params={
        "client_id": client_id, "response_type": "code", "redirect_uri": CALLBACK,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "st8",
    })
    return r, verifier


def _rid(resp):
    loc = urlparse(resp.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == f"{BASE}/oauth/consent"
    return parse_qs(loc.query)["rid"][0]


def test_authorize_redirects_to_consent_without_code(tmp_path):
    provider, client = _app(tmp_path)
    r, _ = _start_authorize(client, _register(client))
    assert r.status_code == 302
    assert "code=" not in r.headers["location"]
    _rid(r)
    assert provider.auth_codes == {}


def test_correct_pin_issues_exchangeable_code(tmp_path, _isolate):
    provider, client = _app(tmp_path)
    cid = _register(client)
    r, verifier = _start_authorize(client, cid)
    rid = _rid(r)

    page = client.get("/oauth/consent", params={"rid": rid})
    assert page.status_code == 200 and "CODEC PIN" in page.text
    assert page.headers["x-frame-options"] == "DENY"

    r = client.post("/oauth/consent", data={"rid": rid, "pin": PIN, "action": "approve"})
    assert r.status_code == 303
    loc = urlparse(r.headers["location"])
    assert loc.netloc == "claude.ai"
    q = parse_qs(loc.query)
    assert q["state"] == ["st8"]

    tok = client.post("/token", data={
        "grant_type": "authorization_code", "code": q["code"][0], "client_id": cid,
        "redirect_uri": CALLBACK, "code_verifier": verifier,
    })
    assert tok.status_code == 200, tok.text
    assert tok.json()["access_token"]
    assert "oauth_consent_granted" in _isolate

    # The request is single-use.
    again = client.post("/oauth/consent", data={"rid": rid, "pin": PIN, "action": "approve"})
    assert again.status_code == 404


def test_wrong_pin_issues_nothing_and_fifth_failure_denies(tmp_path, _isolate):
    provider, client = _app(tmp_path)
    rid = _rid(_start_authorize(client, _register(client))[0])
    for i in range(4):
        r = client.post("/oauth/consent", data={"rid": rid, "pin": "0000", "action": "approve"})
        assert r.status_code == 200 and "Wrong PIN" in r.text
    r = client.post("/oauth/consent", data={"rid": rid, "pin": "0000", "action": "approve"})
    assert r.status_code == 303
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["error"] == ["access_denied"] and "code" not in q
    assert provider.auth_codes == {}
    assert provider.pending_request(rid) is None
    assert "oauth_consent_pin_failed" in _isolate


def test_ip_lockout_blocks_even_the_correct_pin(tmp_path):
    provider, client = _app(tmp_path)
    cid = _register(client)
    # 5 wrong PINs spread over two requests from the same IP.
    rid1 = _rid(_start_authorize(client, cid)[0])
    for _ in range(3):
        client.post("/oauth/consent", data={"rid": rid1, "pin": "0000", "action": "approve"})
    rid2 = _rid(_start_authorize(client, cid)[0])
    for _ in range(2):
        client.post("/oauth/consent", data={"rid": rid2, "pin": "0000", "action": "approve"})
    r = client.post("/oauth/consent", data={"rid": rid2, "pin": PIN, "action": "approve"})
    assert r.status_code == 429
    assert provider.auth_codes == {}


def test_deny_redirects_with_access_denied(tmp_path):
    provider, client = _app(tmp_path)
    rid = _rid(_start_authorize(client, _register(client))[0])
    r = client.post("/oauth/consent", data={"rid": rid, "action": "deny"})
    assert r.status_code == 303
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["error"] == ["access_denied"] and q["state"] == ["st8"]
    assert provider.auth_codes == {}


def test_no_pin_configured_fails_closed(tmp_path):
    provider, client = _app(tmp_path, pin_hash="")
    rid = _rid(_start_authorize(client, _register(client))[0])
    assert client.get("/oauth/consent", params={"rid": rid}).status_code == 503
    r = client.post("/oauth/consent", data={"rid": rid, "pin": "", "action": "approve"})
    assert r.status_code == 503
    assert provider.auth_codes == {}


def test_unknown_or_expired_request(tmp_path, monkeypatch):
    provider, client = _app(tmp_path)
    assert client.get("/oauth/consent", params={"rid": "nope"}).status_code == 404
    rid = _rid(_start_authorize(client, _register(client))[0])
    monkeypatch.setattr(codec_oauth_provider.time, "time", lambda: 10**12)
    r = client.post("/oauth/consent", data={"rid": rid, "pin": PIN, "action": "approve"})
    assert r.status_code == 404
    assert provider.auth_codes == {}


def test_client_name_is_html_escaped(tmp_path):
    provider, client = _app(tmp_path)
    r = client.post("/register", json={
        "redirect_uris": [CALLBACK], "client_name": "<script>x</script>",
        "token_endpoint_auth_method": "none",
    })
    rid = _rid(_start_authorize(client, r.json()["client_id"])[0])
    page = client.get("/oauth/consent", params={"rid": rid}).text
    assert "<script>x</script>" not in page and "&lt;script&gt;" in page
