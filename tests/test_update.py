"""Tests for codec_update — Sparkle-compatible update checker.

Verifies appcast parsing, semver comparison, and (critically) Ed25519 signature
verification using a real throwaway key — a tampered download must be rejected.
"""
import base64
import importlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture
def upd():
    import codec_update
    return importlib.reload(codec_update)


@pytest.fixture
def keypair():
    priv = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(priv.public_key().public_bytes_raw()).decode()
    return priv, pub_b64


def _appcast(version: str, url: str, sig: str, length: int) -> str:
    return f'''<?xml version="1.0"?>
<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle" version="2.0">
  <channel>
    <item>
      <title>Version {version}</title>
      <sparkle:shortVersionString>{version}</sparkle:shortVersionString>
      <sparkle:version>1</sparkle:version>
      <enclosure url="{url}" sparkle:edSignature="{sig}" length="{length}"
                 type="application/octet-stream"/>
    </item>
  </channel>
</rss>'''


# ── version comparison ───────────────────────────────────────────────────────────

def test_is_newer(upd):
    assert upd.is_newer("3.2.0", "3.1.0")
    assert upd.is_newer("3.1.1", "3.1.0")
    assert not upd.is_newer("3.1.0", "3.1.0")
    assert not upd.is_newer("3.0.9", "3.1.0")
    assert upd.is_newer("3.10.0", "3.9.0")     # numeric, not lexical

def test_semver_tolerates_prefix_and_partial(upd):
    assert upd._semver_tuple("v3.1") == (3, 1, 0)
    assert upd._semver_tuple("3.1.0-beta") == (3, 1, 0)


# ── appcast parsing ──────────────────────────────────────────────────────────────

def test_parse_appcast(upd):
    xml = _appcast("3.2.0", "https://x/cdc-3.2.0.dmg", "SIGAAA", 123)
    items = upd.parse_appcast(xml)
    assert len(items) == 1
    it = items[0]
    assert it.version == "3.2.0" and it.url.endswith("3.2.0.dmg")
    assert it.ed_signature == "SIGAAA" and it.length == 123

def test_parse_appcast_sorts_newest_first(upd):
    xml = '''<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel>
      <item><sparkle:shortVersionString>3.1.0</sparkle:shortVersionString>
        <enclosure url="u/3.1.0.dmg" sparkle:edSignature="a" length="1"/></item>
      <item><sparkle:shortVersionString>3.3.0</sparkle:shortVersionString>
        <enclosure url="u/3.3.0.dmg" sparkle:edSignature="b" length="2"/></item>
    </channel></rss>'''
    items = upd.parse_appcast(xml)
    assert items[0].version == "3.3.0"


# ── Ed25519 verification (the security-critical path) ────────────────────────────

def test_verify_accepts_valid_signature(upd, keypair):
    priv, pub_b64 = keypair
    data = b"the real CODEC dmg bytes"
    sig = base64.b64encode(priv.sign(data)).decode()
    assert upd.verify_ed25519(data, sig, pub_b64) is True

def test_verify_rejects_tampered_data(upd, keypair):
    priv, pub_b64 = keypair
    sig = base64.b64encode(priv.sign(b"original")).decode()
    assert upd.verify_ed25519(b"TAMPERED", sig, pub_b64) is False

def test_verify_rejects_wrong_key(upd, keypair):
    priv, _ = keypair
    other_pub = base64.b64encode(
        Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
    sig = base64.b64encode(priv.sign(b"data")).decode()
    assert upd.verify_ed25519(b"data", sig, other_pub) is False

def test_verify_rejects_garbage(upd, keypair):
    _, pub_b64 = keypair
    assert upd.verify_ed25519(b"data", "not-base64-sig!!", pub_b64) is False


# ── check_for_update gating ──────────────────────────────────────────────────────

def test_check_returns_none_when_not_newer(upd, monkeypatch):
    monkeypatch.setattr(upd, "_current_version", lambda: "9.9.9")
    xml = _appcast("3.2.0", "https://x/a.dmg", "sig", 1)
    monkeypatch.setattr(upd, "parse_appcast", upd.parse_appcast)  # keep real parser
    # feed a fake appcast via a stubbed urlopen
    import io
    class _R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return xml.encode()
    monkeypatch.setattr(upd.urllib.request, "urlopen", lambda *a, **k: _R())
    assert upd.check_for_update("http://feed") is None

def test_check_returns_info_when_newer(upd, monkeypatch):
    monkeypatch.setattr(upd, "_current_version", lambda: "3.1.0")
    xml = _appcast("3.2.0", "https://x/a.dmg", "sig", 1)
    class _R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return xml.encode()
    monkeypatch.setattr(upd.urllib.request, "urlopen", lambda *a, **k: _R())
    info = upd.check_for_update("http://feed")
    assert info is not None and info.version == "3.2.0"
