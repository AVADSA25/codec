"""Tests for the remaining 4 plaintext secrets → Keychain (D-15 full closure).

Closes the PR-2B-2 remainder of audit finding D-15: `gemini_api_key`,
`pexels_api_key`, `serper_api_key` (top-level cfg keys) and
`telegram.bot_token` (nested) migrate from `~/.codec/config.json`
plaintext to macOS Keychain, reusing the PR-2B migration machinery.

Each getter precedence: Keychain → cfg (migrate on first call) → env var.

Reference: docs/audits/PHASE-1-SECURITY.md finding D-15.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


# ── Per-test isolation (mirrors test_keychain.py) ────────────────────────────


@pytest.fixture
def force_fallback(tmp_path, monkeypatch):
    """Force the fallback Keychain backend + redirect its files to tmp_path,
    and clear the codec_config secret cache so each test starts clean."""
    import codec_keychain as kc
    import codec_config as cc
    monkeypatch.setattr(kc, "is_keychain_available", lambda: False)
    monkeypatch.setattr(kc, "_FALLBACK_KEY_PATH", tmp_path / "secret.key")
    monkeypatch.setattr(kc, "_FALLBACK_STORE_PATH", tmp_path / "secrets.enc.json")
    test_id = f"_test_rem_{os.getpid()}_{tmp_path.name}"
    monkeypatch.setattr(kc, "_SERVICE_PREFIX", f"ai.avadigital.codec.{test_id}")
    cc._invalidate_secret_cache()
    yield kc, cc
    cc._invalidate_secret_cache()


# ── Top-level secret migration (gemini / pexels / serper) ────────────────────

_TOP_LEVEL = [
    ("gemini_api_key", "get_gemini_api_key", "KEY_GEMINI_API_KEY", "AIzaGEMINItest"),
    ("pexels_api_key", "get_pexels_api_key", "KEY_PEXELS_API_KEY", "pexels-563492test"),
    ("serper_api_key", "get_serper_api_key", "KEY_SERPER_API_KEY", "serper-abc123test"),
]


@pytest.mark.parametrize("cfg_key,getter,kc_const,value", _TOP_LEVEL)
def test_migrates_from_cfg_on_first_call(force_fallback, monkeypatch,
                                          cfg_key, getter, kc_const, value):
    kc, cc = force_fallback
    kc_key = getattr(kc, kc_const)
    # Pre-state: Keychain empty, cfg has plaintext
    assert kc.keychain_get(kc_key) is None
    monkeypatch.setitem(cc.cfg, cfg_key, value)
    blanked = {"called": False}

    def fake_blank(key):
        if key == cfg_key:
            blanked["called"] = True
            cc.cfg[key] = ""
    monkeypatch.setattr(cc, "_blank_config_field", fake_blank)

    # First call migrates
    assert getattr(cc, getter)() == value
    assert kc.keychain_get(kc_key) == value, "Keychain populated"
    assert blanked["called"], "cfg blanked"
    assert cc.cfg[cfg_key] == "", "in-process cfg dict blanked"


@pytest.mark.parametrize("cfg_key,getter,kc_const,value", _TOP_LEVEL)
def test_reads_from_keychain_when_present(force_fallback, monkeypatch,
                                           cfg_key, getter, kc_const, value):
    kc, cc = force_fallback
    kc_key = getattr(kc, kc_const)
    kc.keychain_set(kc_key, value)
    monkeypatch.setitem(cc.cfg, cfg_key, "")
    assert getattr(cc, getter)() == value


@pytest.mark.parametrize("cfg_key,getter,kc_const,env_var", [
    ("gemini_api_key", "get_gemini_api_key", "KEY_GEMINI_API_KEY", "GEMINI_API_KEY"),
    ("pexels_api_key", "get_pexels_api_key", "KEY_PEXELS_API_KEY", "PEXELS_API_KEY"),
    ("serper_api_key", "get_serper_api_key", "KEY_SERPER_API_KEY", "SERPER_API_KEY"),
])
def test_env_fallback_when_no_keychain_or_cfg(force_fallback, monkeypatch,
                                               cfg_key, getter, kc_const, env_var):
    """When Keychain + cfg are both empty, fall back to the env var. The env
    value must NOT be migrated to Keychain (it's ephemeral per process)."""
    kc, cc = force_fallback
    kc_key = getattr(kc, kc_const)
    monkeypatch.setitem(cc.cfg, cfg_key, "")
    monkeypatch.setenv(env_var, "env-fallback-value")
    assert getattr(cc, getter)() == "env-fallback-value"
    # Env value must NOT have been written to Keychain
    assert kc.keychain_get(kc_key) is None, "env fallback must not migrate to Keychain"


@pytest.mark.parametrize("cfg_key,getter,kc_const,value", _TOP_LEVEL)
def test_empty_everywhere_returns_empty(force_fallback, monkeypatch,
                                         cfg_key, getter, kc_const, value):
    kc, cc = force_fallback
    monkeypatch.setitem(cc.cfg, cfg_key, "")
    # Ensure env is clear
    env_var = {"gemini_api_key": "GEMINI_API_KEY",
               "pexels_api_key": "PEXELS_API_KEY",
               "serper_api_key": "SERPER_API_KEY"}[cfg_key]
    monkeypatch.delenv(env_var, raising=False)
    assert getattr(cc, getter)() == ""


# ── Nested secret migration (telegram.bot_token) ─────────────────────────────


def test_telegram_migrates_from_nested_cfg(force_fallback, monkeypatch):
    kc, cc = force_fallback
    assert kc.keychain_get(kc.KEY_TELEGRAM_BOT_TOKEN) is None
    monkeypatch.setitem(cc.cfg, "telegram", {"bot_token": "123456:ABCtoken",
                                              "chat_id": "999"})
    blanked = {"called": False}

    def fake_blank_nested(parent, child):
        if parent == "telegram" and child == "bot_token":
            blanked["called"] = True
            cc.cfg["telegram"]["bot_token"] = ""
    monkeypatch.setattr(cc, "_blank_nested_config_field", fake_blank_nested)

    assert cc.get_telegram_bot_token() == "123456:ABCtoken"
    assert kc.keychain_get(kc.KEY_TELEGRAM_BOT_TOKEN) == "123456:ABCtoken"
    assert blanked["called"], "nested cfg blanked"
    assert cc.cfg["telegram"]["bot_token"] == "", "in-process nested dict blanked"
    # chat_id (a non-secret sibling) must be preserved in-process
    assert cc.cfg["telegram"]["chat_id"] == "999"


def test_telegram_reads_from_keychain_when_present(force_fallback, monkeypatch):
    kc, cc = force_fallback
    kc.keychain_set(kc.KEY_TELEGRAM_BOT_TOKEN, "kc-bot-token")
    monkeypatch.setitem(cc.cfg, "telegram", {"bot_token": ""})
    assert cc.get_telegram_bot_token() == "kc-bot-token"


def test_telegram_env_fallback(force_fallback, monkeypatch):
    kc, cc = force_fallback
    monkeypatch.setitem(cc.cfg, "telegram", {"bot_token": ""})
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-bot-token")
    assert cc.get_telegram_bot_token() == "env-bot-token"
    assert kc.keychain_get(kc.KEY_TELEGRAM_BOT_TOKEN) is None


def test_telegram_missing_telegram_dict_returns_empty(force_fallback, monkeypatch):
    """No `telegram` dict in cfg at all → empty (no crash)."""
    kc, cc = force_fallback
    cc.cfg.pop("telegram", None)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert cc.get_telegram_bot_token() == ""


def test_blank_nested_config_field_preserves_siblings(force_fallback, tmp_path, monkeypatch):
    """_blank_nested_config_field must blank only the target child and keep
    sibling keys (chat_id) + other top-level config intact."""
    kc, cc = force_fallback
    import json
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "telegram": {"bot_token": "secret123", "chat_id": "42", "enabled": True},
        "llm_model": "qwen",
    }))
    monkeypatch.setattr(cc, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(cc, "load_config",
                        lambda: json.loads(cfg_path.read_text()))

    cc._blank_nested_config_field("telegram", "bot_token")

    after = json.loads(cfg_path.read_text())
    assert after["telegram"]["bot_token"] == ""        # blanked
    assert after["telegram"]["chat_id"] == "42"         # sibling preserved
    assert after["telegram"]["enabled"] is True         # sibling preserved
    assert after["llm_model"] == "qwen"                 # other top-level preserved


# ── Cache behavior ───────────────────────────────────────────────────────────


def test_cache_avoids_repeated_shellouts(force_fallback, monkeypatch):
    kc, cc = force_fallback
    monkeypatch.setitem(cc.cfg, "gemini_api_key", "")
    kc.keychain_set(kc.KEY_GEMINI_API_KEY, "cached-gemini")
    calls = {"n": 0}
    orig = kc.keychain_get

    def counting(name):
        calls["n"] += 1
        return orig(name)
    monkeypatch.setattr(kc, "keychain_get", counting)

    assert cc.get_gemini_api_key() == "cached-gemini"
    n = calls["n"]
    cc.get_gemini_api_key()
    cc.get_gemini_api_key()
    assert calls["n"] == n, "no new shellouts within TTL"


# ── codec_keychain key constants exist ───────────────────────────────────────


def test_keychain_constants_defined():
    import codec_keychain as kc
    assert kc.KEY_GEMINI_API_KEY == "gemini_api_key"
    assert kc.KEY_PEXELS_API_KEY == "pexels_api_key"
    assert kc.KEY_SERPER_API_KEY == "serper_api_key"
    assert kc.KEY_TELEGRAM_BOT_TOKEN == "telegram_bot_token"


# ── Call-site source invariants ──────────────────────────────────────────────


def test_call_sites_use_getters_not_raw_cfg():
    """The migrated call sites must call the codec_config getter, not read
    the raw cfg plaintext directly (which would bypass Keychain)."""
    # NOTE: codec_alerts.py reads `alerts.telegram.bot_token` — a DIFFERENT
    # nested config location than the audit-named `telegram.bot_token`.
    # Out of D-15 scope; left as a documented future cleanup (see closure
    # footnote). Not asserted here.
    checks = [
        ("codec.py", "get_gemini_api_key"),
        ("codec_voice.py", "get_gemini_api_key"),
        ("codec_gdocs.py", "get_pexels_api_key"),
        ("codec_search.py", "get_serper_api_key"),
        ("codec_agents.py", "get_serper_api_key"),
        ("codec_telegram.py", "get_telegram_bot_token"),
    ]
    for fname, getter in checks:
        src = (REPO / fname).read_text()
        assert getter in src, f"{fname} must call codec_config.{getter}()"
