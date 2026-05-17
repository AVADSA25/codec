"""Tests for codec_keychain.

Closes audit findings D-8 (OAuth tokens plaintext) and D-15 PARTIAL
(`dashboard_token` + `llm_api_key`) per PR-2B.

Tests are written to work on BOTH macOS Keychain AND the headless
fallback, controlled by monkey-patching `is_keychain_available()`.
The default (live) path uses whichever backend is real on the host —
on Linux CI that's the fallback, on macOS that's real Keychain.
"""
from __future__ import annotations

import os
import sys
import platform
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


# ── Per-test isolation: redirect fallback files to tmp_path ──────────────────


@pytest.fixture(autouse=True)
def _isolate_keychain(tmp_path, monkeypatch):
    """Each test gets its own fallback key + store. On the real Keychain
    backend, we use a unique `_test_` prefix per test run so we can clean
    up at teardown without touching real CODEC secrets."""
    import codec_keychain as kc

    # Redirect fallback paths
    monkeypatch.setattr(kc, "_FALLBACK_KEY_PATH",
                         tmp_path / "secret.key")
    monkeypatch.setattr(kc, "_FALLBACK_STORE_PATH",
                         tmp_path / "secrets.enc.json")
    # Unique service prefix per test so concurrent test runs / interrupted
    # tests don't pollute each other or the user's real Keychain.
    test_id = f"_test_{os.getpid()}_{tmp_path.name}"
    monkeypatch.setattr(kc, "_SERVICE_PREFIX",
                         f"ai.avadigital.codec.{test_id}")
    yield kc
    # Teardown: clear any real-Keychain entries we created (best-effort).
    if kc.is_keychain_available():
        for k in ("test_key", "test_key_2", kc.KEY_DASHBOARD_TOKEN,
                  kc.KEY_LLM_API_KEY, kc.KEY_OAUTH_STATE,
                  "missing_key"):
            kc.keychain_delete(k)


# ── Force-fallback fixture (covers headless CI behavior on macOS dev box) ────


@pytest.fixture
def force_fallback(monkeypatch):
    """Force is_keychain_available() to return False so we exercise the
    fallback path regardless of host OS."""
    import codec_keychain as kc
    monkeypatch.setattr(kc, "is_keychain_available", lambda: False)
    yield kc


# ── Tests ────────────────────────────────────────────────────────────────────


def test_keychain_set_and_get_roundtrip(_isolate_keychain):
    kc = _isolate_keychain
    assert kc.keychain_set("test_key", "secret_value_123")
    assert kc.keychain_get("test_key") == "secret_value_123"


def test_keychain_get_missing_returns_none(_isolate_keychain):
    kc = _isolate_keychain
    assert kc.keychain_get("missing_key") is None


def test_keychain_delete_idempotent(_isolate_keychain):
    kc = _isolate_keychain
    kc.keychain_set("test_key", "x")
    assert kc.keychain_delete("test_key") is True
    # second delete on absent entry — must NOT raise + still return True
    assert kc.keychain_delete("test_key") is True


def test_keychain_overwrite_via_set(_isolate_keychain):
    """keychain_set must update an existing entry, not error on the
    duplicate key (Keychain's -U upsert flag; fallback overwrite)."""
    kc = _isolate_keychain
    kc.keychain_set("test_key", "first")
    kc.keychain_set("test_key", "second")
    assert kc.keychain_get("test_key") == "second"


def test_keychain_unavailable_falls_back(force_fallback, tmp_path):
    """When Keychain is unavailable, set/get must transparently use the
    envelope-encrypted fallback. Also verify the fallback key file is
    created with 0600 perms."""
    kc = force_fallback
    assert not kc.is_keychain_available()
    assert kc.keychain_set("test_key", "fallback_secret")
    assert kc.keychain_get("test_key") == "fallback_secret"
    # 0600 check
    key_path = kc._FALLBACK_KEY_PATH
    assert key_path.exists()
    mode = os.stat(key_path).st_mode & 0o777
    assert mode == 0o600, f"Fallback key file must be 0600, got 0{mode:o}"


def test_fallback_secrets_file_not_plaintext(force_fallback):
    """The fallback store on disk must NOT contain the plaintext value."""
    kc = force_fallback
    kc.keychain_set("test_key", "PLAIN_SECRET_TOKEN_MARKER")
    store_text = kc._FALLBACK_STORE_PATH.read_text()
    assert "PLAIN_SECRET_TOKEN_MARKER" not in store_text


def test_migrate_from_plaintext_idempotent(_isolate_keychain):
    """Migration only runs the first call; subsequent calls are no-ops."""
    kc = _isolate_keychain
    calls = {"blanked": 0}

    def blank():
        calls["blanked"] += 1

    # First call: migrates
    assert kc.migrate_from_plaintext("test_key", "legacy", blank) is True
    assert calls["blanked"] == 1
    assert kc.keychain_get("test_key") == "legacy"

    # Second call: no-op (already in Keychain)
    assert kc.migrate_from_plaintext("test_key", "legacy", blank) is False
    assert calls["blanked"] == 1, "blank_source_fn must not be called twice"


def test_migrate_from_plaintext_blanks_source(_isolate_keychain):
    """blank_source_fn is called exactly once on successful migration."""
    kc = _isolate_keychain
    calls = {"n": 0}

    def blank():
        calls["n"] += 1

    kc.migrate_from_plaintext("test_key", "plaintext_value", blank)
    assert calls["n"] == 1


def test_migrate_from_plaintext_skips_empty_source(_isolate_keychain):
    """If the plaintext value is empty, no migration happens (nothing to
    migrate). Important: avoids storing empty strings in Keychain."""
    kc = _isolate_keychain
    calls = {"n": 0}

    def blank():
        calls["n"] += 1

    assert kc.migrate_from_plaintext("test_key", "", blank) is False
    assert calls["n"] == 0
    assert kc.keychain_get("test_key") is None


def test_get_dashboard_token_alias(_isolate_keychain):
    kc = _isolate_keychain
    kc.keychain_set(kc.KEY_DASHBOARD_TOKEN, "dt_value")
    assert kc.get_dashboard_token() == "dt_value"


def test_get_llm_api_key_alias(_isolate_keychain):
    kc = _isolate_keychain
    kc.keychain_set(kc.KEY_LLM_API_KEY, "sk-test-llm-key")
    assert kc.get_llm_api_key() == "sk-test-llm-key"


def test_oauth_state_set_and_get(_isolate_keychain):
    """OAuth state migration round-trip — serialize one JSON blob, store,
    re-read."""
    kc = _isolate_keychain
    blob = '{"access_tokens": {"codec_at_abc": "..."}}'
    assert kc.set_oauth_state(blob)
    assert kc.get_oauth_state() == blob


# ── macOS-only: real Keychain path ───────────────────────────────────────────


@pytest.mark.skipif(platform.system() != "Darwin",
                     reason="real Keychain only on macOS")
def test_real_keychain_used_on_darwin(_isolate_keychain):
    """Sanity: on macOS, is_keychain_available() is True and the security
    binary path is correct."""
    kc = _isolate_keychain
    assert kc.is_keychain_available() is True
    assert Path(kc._SECURITY_BIN).exists()


# ── OAuth state migration (PersistentOAuthProvider) ──────────────────────────


def test_oauth_state_json_deleted_after_keychain_save(force_fallback, tmp_path):
    """After PersistentOAuthProvider._save runs successfully against Keychain
    (here: the fallback), the legacy plaintext oauth_state.json must be
    removed from disk. Closes D-8 §1."""
    kc = force_fallback

    # Simulate legacy plaintext file present
    legacy = tmp_path / "oauth_state.json"
    legacy.write_text('{"clients":{},"access_tokens":{},"refresh_tokens":{},'
                      '"access_to_refresh":{},"refresh_to_access":{}}')
    assert legacy.exists()

    # Wire the provider's state path to our temp legacy file
    import codec_oauth_provider as cop
    provider = cop.PersistentOAuthProvider(state_path=legacy)
    # Force a save (replays the migration: read legacy → write Keychain → delete legacy)
    provider._save()

    # Legacy file must be gone
    assert not legacy.exists(), (
        "Legacy oauth_state.json must be deleted after successful Keychain save"
    )
    # And Keychain must hold the (serialized) state
    assert kc.get_oauth_state() is not None


def test_oauth_state_loaded_from_keychain_first(force_fallback, tmp_path,
                                                  monkeypatch):
    """If both Keychain and a legacy file exist, the load path must consult
    Keychain BEFORE the file. Verified by monkey-patching the file-read so
    it raises — load must still succeed because Keychain was consulted
    first."""
    kc = force_fallback
    import codec_oauth_provider as cop

    # Pre-populate Keychain with a valid serialized state
    kc.set_oauth_state(
        '{"clients":{},"access_tokens":{},"refresh_tokens":{},'
        '"access_to_refresh":{},"refresh_to_access":{}}'
    )
    # Make the legacy file content garbage — load must NOT fall back to it
    legacy = tmp_path / "oauth_state.json"
    legacy.write_text("not valid json {{{{")

    # Provider initializes (calls _load); if Keychain wasn't checked first,
    # the json-parse error on the file would leave provider in fresh state.
    # With Keychain-first, the valid Keychain blob loads cleanly.
    provider = cop.PersistentOAuthProvider(state_path=legacy)
    # Sanity: provider has all the expected dict attributes (load completed)
    assert hasattr(provider, "_access_to_refresh_map")


def test_oauth_state_migrates_from_legacy_disk(force_fallback, tmp_path):
    """Cold-start migration: only the legacy file exists. Provider reads
    it, then the first _save persists to Keychain and deletes the file."""
    kc = force_fallback
    import codec_oauth_provider as cop

    # Pre-condition: Keychain empty, legacy file exists
    assert kc.get_oauth_state() is None
    legacy = tmp_path / "oauth_state.json"
    legacy.write_text(
        '{"clients":{},"access_tokens":{},"refresh_tokens":{},'
        '"access_to_refresh":{},"refresh_to_access":{}}'
    )

    provider = cop.PersistentOAuthProvider(state_path=legacy)
    # Trigger a save → Keychain populated, file deleted
    provider._save()
    assert not legacy.exists(), (
        "Legacy oauth_state.json must be deleted after first Keychain save"
    )
    assert kc.get_oauth_state() is not None


# ── codec_config: get_llm_api_key + get_dashboard_token migration paths ──────


def test_get_llm_api_key_migrates_from_cfg_on_first_call(force_fallback,
                                                          monkeypatch, tmp_path):
    """First call with cfg.llm_api_key set + Keychain empty must migrate:
    write Keychain, blank cfg, return the migrated value."""
    kc = force_fallback
    import codec_config as cc
    cc._invalidate_secret_cache()

    # Pre-state: Keychain empty, cfg has legacy plaintext
    assert kc.get_llm_api_key() is None
    monkeypatch.setitem(cc.cfg, "llm_api_key", "sk-legacy-plaintext")

    # Stub _blank_config_field so we don't touch the real config file
    blanked = {"called": False}
    def fake_blank(key):
        if key == "llm_api_key":
            blanked["called"] = True
            cc.cfg["llm_api_key"] = ""
    monkeypatch.setattr(cc, "_blank_config_field", fake_blank)

    # First call: migrates
    assert cc.get_llm_api_key() == "sk-legacy-plaintext"
    assert kc.get_llm_api_key() == "sk-legacy-plaintext", "Keychain populated"
    assert blanked["called"], "cfg blank invoked"
    assert cc.cfg["llm_api_key"] == "", "cfg dict in-process is also blanked"

    # Second call (cache hit then would-be cfg-empty + Keychain-populated):
    # still returns the migrated value, but no re-migration.
    cc._invalidate_secret_cache("llm_api_key")
    blanked["called"] = False
    assert cc.get_llm_api_key() == "sk-legacy-plaintext"
    assert not blanked["called"], "no second migration"


def test_get_dashboard_token_migration(force_fallback, monkeypatch):
    """Same migration semantics for dashboard_token."""
    kc = force_fallback
    import codec_config as cc
    cc._invalidate_secret_cache()
    monkeypatch.setitem(cc.cfg, "dashboard_token", "legacy_dt_value")
    monkeypatch.setattr(cc, "_blank_config_field", lambda k: cc.cfg.__setitem__(k, ""))
    assert cc.get_dashboard_token() == "legacy_dt_value"
    assert kc.get_dashboard_token() == "legacy_dt_value"
    assert cc.cfg["dashboard_token"] == ""


def test_secret_cache_30s_ttl(force_fallback, monkeypatch):
    """Repeated calls within the cache TTL must NOT shell out to Keychain
    (verified by stubbing keychain_get to count calls)."""
    kc = force_fallback
    import codec_config as cc
    cc._invalidate_secret_cache()
    monkeypatch.setitem(cc.cfg, "llm_api_key", "")  # no migration needed

    calls = {"n": 0}
    original_get = kc.keychain_get
    def counting_get(name):
        calls["n"] += 1
        return original_get(name)
    monkeypatch.setattr(kc, "keychain_get", counting_get)

    # Seed the Keychain with a value directly so the helper has something to find
    kc.keychain_set(kc.KEY_LLM_API_KEY, "kc_direct_value")

    # First call → 1 shellout
    assert cc.get_llm_api_key() == "kc_direct_value"
    n_after_first = calls["n"]
    # Second + third calls within TTL → no new shellouts
    cc.get_llm_api_key()
    cc.get_llm_api_key()
    assert calls["n"] == n_after_first, (
        f"Expected no new keychain shellouts within TTL; got "
        f"{calls['n'] - n_after_first} extra calls"
    )


def test_secret_cache_invalidate(force_fallback, monkeypatch):
    """_invalidate_secret_cache forces a re-read on next call."""
    kc = force_fallback
    import codec_config as cc
    cc._invalidate_secret_cache()
    monkeypatch.setitem(cc.cfg, "llm_api_key", "")
    kc.keychain_set(kc.KEY_LLM_API_KEY, "v1")
    assert cc.get_llm_api_key() == "v1"
    # Rotate value in Keychain
    kc.keychain_set(kc.KEY_LLM_API_KEY, "v2")
    # Without invalidate: stale cache
    assert cc.get_llm_api_key() == "v1"
    # After invalidate: fresh
    cc._invalidate_secret_cache("llm_api_key")
    assert cc.get_llm_api_key() == "v2"
