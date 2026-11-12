"""The paid app's licence key moves from config.json to the Keychain (R-03).

`codec_config.get_license_key(conf, path)`: Keychain -> ava.license_key in the
config file (moved to the Keychain on first read, that one field blanked, sibling
keys kept) -> CODEC_LICENSE_KEY env. docs/LICENSE-KEY-KEYCHAIN-DESIGN.md.

Every test uses the fallback Keychain store and config files under tmp_path, with
a per-test service prefix (like tests/test_remaining_secrets.py). Never the real
Keychain, never a fake HOME. tests/conftest.py turns the move off for the suite;
the fixture here turns it on.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


# A JWT-shaped test value (the signature is not checked here).
TOK = _b64({"alg": "RS256", "typ": "JWT"}) + "." + _b64({"tier": "pro", "exp": 4102444800}) + ".c2ln"
OLD = _b64({"alg": "RS256", "typ": "JWT"}) + "." + _b64({"tier": "old", "exp": 4102444800}) + ".b2xk"


@pytest.fixture
def lk(tmp_path, monkeypatch):
    import codec_config as cc
    import codec_keychain as kc
    monkeypatch.setattr(kc, "is_keychain_available", lambda: False)
    monkeypatch.setattr(kc, "_FALLBACK_KEY_PATH", tmp_path / "secret.key")
    monkeypatch.setattr(kc, "_FALLBACK_STORE_PATH", tmp_path / "secrets.enc.json")
    monkeypatch.setattr(kc, "_SERVICE_PREFIX", f"ai.avadigital.codec._test_lk_{os.getpid()}_{tmp_path.name}")
    events = []
    monkeypatch.setattr(kc, "_kc_log_event", lambda name, **kw: events.append((name, kw)))
    kc._invalidate_negative_cache()
    # The module copy and default path point at tmp_path, never the owner's file.
    monkeypatch.setattr(cc, "cfg", {})
    monkeypatch.setattr(cc, "CONFIG_PATH", str(tmp_path / "default-config.json"))
    monkeypatch.setenv("LICENSE_KEY_MIGRATION_ENABLED", "true")
    monkeypatch.delenv("CODEC_LICENSE_KEY", raising=False)
    cc._invalidate_secret_cache()
    yield cc, kc, events
    cc._invalidate_secret_cache()
    kc._invalidate_negative_cache()


def _write(path: Path, key: str, **extra_top) -> dict:
    data = {"ava": {"enabled": True, "proxy_url": "https://p.example",
                    "license_key": key, "default_cloud_model": "m"},
            "llm_model": "x", **extra_top}
    path.write_text(json.dumps(data))
    return json.loads(path.read_text())


def _disk(path: Path) -> dict:
    return json.loads(path.read_text())


# ── The getter ───────────────────────────────────────────────────────────────

def test_first_read_moves_key_and_keeps_siblings(lk, tmp_path):
    cc, kc, events = lk
    path = tmp_path / "config.json"
    conf = _write(path, TOK, telegram={"chat_id": "42"})

    assert cc.get_license_key(conf, path) == TOK
    assert kc.keychain_get(kc.KEY_LICENSE_KEY) == TOK, "key not in the Keychain"
    after = _disk(path)
    assert after["ava"] == {"enabled": True, "proxy_url": "https://p.example",
                            "license_key": "", "default_cloud_model": "m"}, "field not blanked or a sibling lost"
    assert after["llm_model"] == "x" and after["telegram"] == {"chat_id": "42"}
    assert conf["ava"]["license_key"] == "", "the caller's dict still holds the key"
    assert [kw["outcome"] for n, kw in events if n == "keychain_migration"] == ["ok"]
    # Next read: the field is blank, the Keychain answers.
    assert cc.get_license_key(_disk(path), path) == TOK


def test_reads_keychain_when_field_blank(lk, tmp_path):
    cc, kc, _ = lk
    kc.keychain_set(kc.KEY_LICENSE_KEY, TOK)
    path = tmp_path / "config.json"
    assert cc.get_license_key(_write(path, ""), path) == TOK
    assert cc.get_license_key() == TOK, "no-argument form (module cfg) misses the Keychain"


def test_cache_avoids_repeated_keychain_reads(lk, monkeypatch):
    cc, kc, _ = lk
    kc.keychain_set(kc.KEY_LICENSE_KEY, TOK)
    calls = {"n": 0}
    real = kc.keychain_get

    def counting(name):
        calls["n"] += 1
        return real(name)
    monkeypatch.setattr(kc, "keychain_get", counting)
    assert cc.get_license_key({}) == TOK
    n = calls["n"]
    cc.get_license_key({})
    cc.get_license_key({})
    assert calls["n"] == n, "Keychain read again within the 30 s cache"


def test_env_fallback_is_not_stored(lk, monkeypatch):
    cc, kc, _ = lk
    monkeypatch.setenv("CODEC_LICENSE_KEY", "env-licence")
    assert cc.get_license_key({"ava": {"license_key": ""}}) == "env-licence"
    assert kc.keychain_get(kc.KEY_LICENSE_KEY) is None, "env value written to the Keychain"


def test_empty_everywhere_returns_empty(lk):
    cc, _, _ = lk
    assert cc.get_license_key({}) == ""
    assert cc.get_license_key({"ava": "not a dict"}) == ""


def test_dict_the_file_does_not_hold_stores_nothing(lk, tmp_path):
    """A test's dict, or a copy read before another process moved the key."""
    cc, kc, _ = lk
    path = tmp_path / "config.json"
    _write(path, OLD)
    before = path.read_bytes()
    assert cc.get_license_key({"ava": {"license_key": TOK}}, path) == TOK
    assert kc.keychain_get(kc.KEY_LICENSE_KEY) is None, "a value that is not in the file was stored"
    assert path.read_bytes() == before, "the file was written"
    missing = tmp_path / "missing.json"
    assert cc.get_license_key({"ava": {"license_key": TOK}}, missing) == TOK
    assert not missing.exists()


def test_newer_key_in_file_replaces_stored_one(lk, tmp_path):
    """The installer writes ava.license_key on every run (a reinstall with a new key)."""
    cc, kc, _ = lk
    kc.keychain_set(kc.KEY_LICENSE_KEY, OLD)
    assert cc.get_license_key({}) == OLD  # cached
    path = tmp_path / "config.json"
    assert cc.get_license_key(_write(path, TOK), path) == TOK
    assert kc.keychain_get(kc.KEY_LICENSE_KEY) == TOK, "newer key did not replace the stored one"
    assert _disk(path)["ava"]["license_key"] == ""
    assert cc.get_license_key({}) == TOK, "cache still held the old key"


def test_same_key_written_back_is_only_blanked(lk, tmp_path, monkeypatch):
    cc, kc, _ = lk
    kc.keychain_set(kc.KEY_LICENSE_KEY, TOK)
    sets = []
    monkeypatch.setattr(kc, "keychain_set", lambda *a: sets.append(a) or True)
    path = tmp_path / "config.json"
    assert cc.get_license_key(_write(path, TOK), path) == TOK
    assert sets == [], "stored again although the Keychain already holds it"
    assert _disk(path)["ava"]["license_key"] == ""


def test_keychain_write_fails_keeps_the_field(lk, tmp_path, monkeypatch):
    cc, kc, _ = lk
    monkeypatch.setattr(kc, "keychain_set", lambda *a: False)
    path = tmp_path / "config.json"
    assert cc.get_license_key(_write(path, TOK), path) == TOK, "CODEC lost the key"
    assert _disk(path)["ava"]["license_key"] == TOK, "field blanked though the Keychain write failed"


def test_kill_switch_moves_nothing(lk, tmp_path, monkeypatch):
    cc, kc, _ = lk
    monkeypatch.setenv("LICENSE_KEY_MIGRATION_ENABLED", "false")
    path = tmp_path / "config.json"
    _write(path, TOK)
    before = path.read_bytes()
    assert cc.get_license_key(_disk(path), path) == TOK
    assert kc.keychain_get(kc.KEY_LICENSE_KEY) is None
    assert path.read_bytes() == before


def test_keychain_constant():
    import codec_keychain as kc
    assert kc.KEY_LICENSE_KEY == "license_key"


# ── The readers go through it ────────────────────────────────────────────────

def test_licence_check_reads_through_the_getter(lk, tmp_path, monkeypatch):
    cc, kc, _ = lk
    import codec_license
    path = tmp_path / "config.json"
    monkeypatch.setattr(codec_license, "CONFIG_PATH", path)
    conf = _write(path, TOK, edition="paid")
    assert codec_license._license_token(conf) == TOK
    assert kc.keychain_get(kc.KEY_LICENSE_KEY) == TOK and _disk(path)["ava"]["license_key"] == ""
    assert codec_license._license_token(_disk(path)) == TOK, "licence check lost the key after the move"
    # The legacy names still come first, as before.
    assert codec_license._license_token({"license_token": OLD, "ava": {}}) == OLD


def test_ava_client_reads_through_the_getter(lk, tmp_path, monkeypatch):
    cc, kc, _ = lk
    import codec_ava_client
    path = tmp_path / "config.json"
    monkeypatch.setattr(codec_ava_client, "CONFIG_PATH", path)
    _write(path, TOK)
    assert codec_ava_client.load_config().license_key == TOK
    assert _disk(path)["ava"]["license_key"] == ""
    assert codec_ava_client.load_config().is_ready, "proxy client lost the key after the move"


def test_setup_ava_mode_moves_key_and_does_not_write_it_back(lk, tmp_path, monkeypatch):
    cc, kc, _ = lk
    import codec_setup
    path = tmp_path / "config.json"
    monkeypatch.setattr(codec_setup, "CONFIG_PATH", str(path))
    _write(path, TOK)
    assert codec_setup.set_provider("ava")["ok"]
    after = _disk(path)
    assert after["ava"]["license_key"] == "", "set_provider saved the key back into the file"
    assert after["ava"]["proxy_url"] == "https://p.example" and after["llm_provider"] == "ava"
    assert kc.keychain_get(kc.KEY_LICENSE_KEY) == TOK
    assert kc.keychain_get(codec_setup.KEY_SLOT) == TOK, "the proxy bearer slot did not get the key"
    assert codec_setup._license_key(after) == TOK  # what status()["has_license"] reads


def test_version_reads_through_the_getter_and_who_prints_no_key(lk, tmp_path, monkeypatch):
    cc, kc, _ = lk
    import codec_slash_commands as scm
    path = tmp_path / "config.json"
    monkeypatch.setattr(scm, "CONFIG_PATH", path)
    _write(path, TOK)
    assert scm._ava_license_status(_disk(path)).startswith("pro"), "tier not read from the key"
    assert _disk(path)["ava"]["license_key"] == ""
    assert scm._ava_license_status(_disk(path)).startswith("pro"), "/version lost the key after the move"
    _write(path, TOK)
    who = scm._cmd_who([])
    assert TOK[:10] not in who and "icense" not in who, "/who prints part of the licence key"
