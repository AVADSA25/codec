"""Tests for codec_license — client-side license enforcement.

Covers the operator-approved design: OSS builds are NEVER enforced, paid builds
degrade to read-only on invalid/expired license with a 7-day offline grace.
Uses a throwaway RSA keypair to sign test tokens (no network, no real server).
"""
import base64
import importlib
import json
import time

import pytest

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding


# ── helpers: mint a signed RS256 license token with a test key ──────────────────

def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

def _make_token(priv, claims: dict) -> str:
    header = {"alg": "RS256", "typ": "JWT"}
    h = _b64url(json.dumps(header).encode())
    p = _b64url(json.dumps(claims).encode())
    sig = priv.sign((h + "." + p).encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{h}.{p}.{_b64url(sig)}"

@pytest.fixture
def keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv, pub_pem

@pytest.fixture
def lic():
    import codec_license
    return importlib.reload(codec_license)


# ── token verification ──────────────────────────────────────────────────────────

def test_valid_token_verifies(lic, keypair):
    priv, pub = keypair
    tok = _make_token(priv, {"tier": "pro", "exp": time.time() + 3600})
    ok, claims, reason = lic.verify_license_token(tok, pub)
    assert ok and reason == "ok" and claims["tier"] == "pro"

def test_expired_token_fails(lic, keypair):
    priv, pub = keypair
    tok = _make_token(priv, {"tier": "pro", "exp": time.time() - 10})
    ok, _, reason = lic.verify_license_token(tok, pub)
    assert not ok and reason == "expired"

def test_bad_signature_fails(lic, keypair):
    priv, pub = keypair
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tok = _make_token(other, {"tier": "pro", "exp": time.time() + 3600})  # signed by wrong key
    ok, _, reason = lic.verify_license_token(tok, pub)
    assert not ok and reason == "bad signature"

def test_malformed_token_fails(lic, keypair):
    _, pub = keypair
    ok, _, reason = lic.verify_license_token("not.a.jwt.x", pub)
    assert not ok

def test_expires_at_iso_is_honored(lic, keypair):
    priv, pub = keypair
    from datetime import datetime, timedelta, timezone
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    tok = _make_token(priv, {"tier": "pro", "expires_at": past})
    ok, _, reason = lic.verify_license_token(tok, pub)
    assert not ok and reason == "expired"


# ── state machine: OSS is NEVER enforced (the critical safety property) ──────────

def test_oss_build_is_never_enforced(lic):
    st = lic.license_state({})                       # no edition key
    assert st.mode == "oss" and not st.enforced
    st2 = lic.license_state({"edition": "community"})
    assert st2.mode == "oss" and not st2.enforced

def test_oss_allows_all_features(lic):
    for f in ("skill_exec", "agents", "pilot", "project", "cloud_proxy"):
        assert lic.feature_allowed(f, {}) is True

def test_paid_without_token_is_readonly(lic):
    st = lic.license_state({"edition": "paid"})
    assert st.mode == "readonly" and st.enforced

def test_paid_readonly_blocks_gated_but_allows_ungated(lic):
    cfg = {"edition": "paid"}                          # paid, no token → readonly
    assert lic.feature_allowed("agents", cfg) is False
    assert lic.feature_allowed("pilot", cfg) is False
    assert lic.feature_allowed("settings", cfg) is True   # ungated, always allowed

def test_require_raises_on_gated_when_readonly(lic):
    with pytest.raises(lic.LicenseError):
        lic.require("project", {"edition": "paid"})

def test_require_passes_for_oss(lic):
    lic.require("project", {})        # OSS → no raise


# ── offline grace window ────────────────────────────────────────────────────────

def test_grace_window_keeps_working(lic, tmp_path, monkeypatch):
    # last good 3 days ago, 7-day window → grace, not readonly
    monkeypatch.setattr(lic, "GRACE_STATE_PATH", tmp_path / ".license_state.json")
    now = time.time()
    lic._write_grace(now - 3 * 86400, "pro", "")
    st = lic._grace_or_readonly(now, "server down")
    assert st.mode == "grace" and not st.enforced and st.grace_days_left in (3, 4)

def test_grace_expired_falls_to_readonly(lic, tmp_path, monkeypatch):
    monkeypatch.setattr(lic, "GRACE_STATE_PATH", tmp_path / ".license_state.json")
    now = time.time()
    lic._write_grace(now - 10 * 86400, "pro", "")     # 10 days > 7-day window
    st = lic._grace_or_readonly(now, "server down")
    assert st.mode == "readonly" and st.enforced


# ── cloud_proxy transport gate (codec_llm) ──────────────────────────────────────

def test_cloud_gate_local_never_blocked(monkeypatch):
    import codec_llm
    import codec_license
    # even a paid readonly build must NEVER block a local call
    monkeypatch.setattr(codec_license, "feature_allowed",
                        lambda f, cfg=None: False)
    assert codec_llm._cloud_blocked_msg("http://localhost:8083/v1") is None
    assert codec_llm._cloud_blocked_msg("http://127.0.0.1:8083/v1") is None

def test_cloud_gate_oss_allows_cloud(monkeypatch):
    import codec_llm
    import codec_license
    monkeypatch.setattr(codec_license, "feature_allowed", lambda f, cfg=None: True)
    assert codec_llm._cloud_blocked_msg("https://ava-proxy.lucyvpa.com") is None

def test_cloud_gate_paid_readonly_blocks_cloud(monkeypatch):
    import codec_llm
    import codec_license
    monkeypatch.setattr(codec_license, "feature_allowed",
                        lambda f, cfg=None: f != "cloud_proxy")
    msg = codec_llm._cloud_blocked_msg("https://ava-proxy.lucyvpa.com")
    assert msg and "license" in msg.lower()

def test_cloud_gate_fails_open_on_error(monkeypatch):
    import codec_llm
    import codec_license
    def _boom(*a, **k): raise RuntimeError("license module broken")
    monkeypatch.setattr(codec_license, "feature_allowed", _boom)
    # must NOT raise — transport stays up even if licensing throws
    assert codec_llm._cloud_blocked_msg("https://ava-proxy.lucyvpa.com") is None
