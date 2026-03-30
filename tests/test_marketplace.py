"""Tests for codec_marketplace.py"""
import hashlib
import json
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from codec_marketplace import (
    _load_marketplace_meta,
    _save_marketplace_meta,
    _load_cached_registry,
    _verify_sha256,
    SKILL_TRIGGERS,
    SKILL_DESCRIPTION,
    run,
)


# ── Meta helpers ─────────────────────────────────────────────────────────────

def test_meta_round_trip(tmp_path, monkeypatch):
    """Load/save marketplace meta persists data correctly."""
    monkeypatch.setenv("HOME", str(tmp_path))
    meta_dir = tmp_path / ".codec" / "skills"
    meta_dir.mkdir(parents=True)
    meta_path = meta_dir / ".marketplace.json"

    import codec_marketplace as cm
    monkeypatch.setattr(cm, "MARKETPLACE_META", str(meta_path))
    monkeypatch.setattr(cm, "SKILLS_DIR", str(meta_dir))

    meta = _load_marketplace_meta()
    assert meta["installed"] == {}

    meta["installed"]["bitcoin-price"] = {"version": "1.0.0", "file": "bitcoin_price.py"}
    _save_marketplace_meta(meta)

    loaded = _load_marketplace_meta()
    assert loaded["installed"]["bitcoin-price"]["version"] == "1.0.0"


def test_meta_missing_file_returns_default(tmp_path, monkeypatch):
    """Missing .marketplace.json returns empty default."""
    import codec_marketplace as cm
    monkeypatch.setattr(cm, "MARKETPLACE_META", str(tmp_path / "nonexistent.json"))
    result = _load_marketplace_meta()
    assert result == {"installed": {}, "last_update": ""}


# ── Cached registry ───────────────────────────────────────────────────────────

def test_cached_registry_returns_empty_when_no_cache(tmp_path, monkeypatch):
    """Returns empty skills list when no cache and no network."""
    import codec_marketplace as cm
    monkeypatch.setattr(cm, "CACHE_DIR", str(tmp_path))
    result = _load_cached_registry()
    assert "skills" in result


def test_cached_registry_reads_file(tmp_path, monkeypatch):
    """Reads skills from cache file when present."""
    import codec_marketplace as cm
    monkeypatch.setattr(cm, "CACHE_DIR", str(tmp_path))
    fake = {"skills": [{"name": "test-skill"}], "categories": ["utility"]}
    (tmp_path / "registry.json").write_text(json.dumps(fake))
    result = _load_cached_registry()
    assert result["skills"][0]["name"] == "test-skill"


# ── CODEC skill interface ─────────────────────────────────────────────────────

def test_skill_triggers_not_empty():
    assert len(SKILL_TRIGGERS) > 0


def test_skill_description_not_empty():
    assert SKILL_DESCRIPTION


def test_run_returns_string_for_unknown_task(monkeypatch):
    """run() always returns a string even with no network."""
    import codec_marketplace as cm
    monkeypatch.setattr(cm, "_fetch_registry", lambda silent=False: {"skills": [], "categories": []})
    monkeypatch.setattr(cm, "_load_marketplace_meta", lambda: {"installed": {}})
    result = run("show marketplace")
    assert isinstance(result, str)
    assert len(result) > 0


def test_run_search_no_results(monkeypatch):
    """run('search xyz') returns helpful message when nothing found."""
    import codec_marketplace as cm
    monkeypatch.setattr(cm, "_fetch_registry", lambda silent=False: {"skills": [], "categories": []})
    result = run("search xyznonexistent")
    assert isinstance(result, str)
    assert "not found" in result.lower() or "no skill" in result.lower()


def test_run_install_not_found(monkeypatch):
    """run('install nonexistent') returns error string, not exception."""
    import codec_marketplace as cm
    monkeypatch.setattr(cm, "_fetch_registry", lambda silent=False: {"skills": [], "categories": []})
    result = run("install skill nonexistent-skill-xyz")
    assert isinstance(result, str)
    assert "not found" in result.lower()


def test_run_search_finds_match(monkeypatch):
    """run('search bitcoin') returns skill info when it exists."""
    import codec_marketplace as cm
    fake_registry = {
        "skills": [{
            "name": "bitcoin-price",
            "display_name": "Bitcoin Price",
            "description": "Check crypto prices",
            "version": "1.0.0",
            "author": "AVA Digital",
            "triggers": ["bitcoin price", "crypto price"],
            "category": "finance",
            "verified": True,
        }],
        "categories": ["finance"]
    }
    monkeypatch.setattr(cm, "_fetch_registry", lambda silent=False: fake_registry)
    result = run("search skills bitcoin")
    assert "bitcoin" in result.lower()
    assert "Found" in result


# ── CLI plumbing ──────────────────────────────────────────────────────────────

def test_cmd_list_runs(tmp_path, monkeypatch, capsys):
    """cmd_list() prints table without errors."""
    import codec_marketplace as cm
    monkeypatch.setattr(cm, "SKILLS_DIR", str(tmp_path))
    monkeypatch.setattr(cm, "_load_marketplace_meta", lambda: {"installed": {}})
    (tmp_path / "test_skill.py").write_text("# test")
    cm.cmd_list()
    out = capsys.readouterr().out
    assert "test_skill" in out


def test_cmd_search_no_network(monkeypatch, capsys):
    """cmd_search() handles empty registry gracefully."""
    import codec_marketplace as cm
    monkeypatch.setattr(cm, "_fetch_registry", lambda silent=False: {"skills": [], "categories": []})
    cm.cmd_search("bitcoin")
    out = capsys.readouterr().out
    assert "No skills" in out or "found" in out.lower()


def test_cmd_info_not_found(monkeypatch, capsys):
    """cmd_info() prints 'not found' for unknown skill."""
    import codec_marketplace as cm
    monkeypatch.setattr(cm, "_fetch_registry", lambda silent=False: {"skills": [], "categories": []})
    cm.cmd_info("nonexistent-skill-xyz")
    out = capsys.readouterr().out
    assert "not found" in out.lower()


# ── SHA-256 checksum verification ────────────────────────────────────────────

def test_verify_sha256_valid():
    """Matching hash returns True."""
    content = "print('hello world')\n"
    expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert _verify_sha256(content, expected) is True


def test_verify_sha256_invalid():
    """Mismatched hash returns False."""
    content = "print('hello world')\n"
    assert _verify_sha256(content, "0" * 64) is False


def test_verify_sha256_case_insensitive():
    """Hash comparison is case-insensitive."""
    content = "x = 1\n"
    expected = hashlib.sha256(content.encode("utf-8")).hexdigest().upper()
    assert _verify_sha256(content, expected) is True


def test_download_skill_rejects_bad_hash(monkeypatch):
    """_download_skill returns None when sha256 does not match."""
    import codec_marketplace as cm

    class FakeResp:
        status_code = 200
        text = "# malicious code\n"

    import types
    fake_requests = types.ModuleType("requests")
    fake_requests.get = lambda url, timeout=30: FakeResp()
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    entry = {"name": "evil-skill", "file": "evil/evil.py", "sha256": "0" * 64}
    result = cm._download_skill(entry, {"base_url": "https://example.com"})
    assert result is None


def test_download_skill_accepts_good_hash(monkeypatch):
    """_download_skill returns content when sha256 matches."""
    import codec_marketplace as cm

    code = "# legit skill\nprint('ok')\n"
    good_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()

    class FakeResp:
        status_code = 200
        text = code

    import types
    fake_requests = types.ModuleType("requests")
    fake_requests.get = lambda url, timeout=30: FakeResp()
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    entry = {"name": "good-skill", "file": "good/good.py", "sha256": good_hash}
    result = cm._download_skill(entry, {"base_url": "https://example.com"})
    assert result == code


def test_download_skill_warns_no_hash(monkeypatch, caplog):
    """_download_skill logs a warning when no sha256 in registry entry."""
    import logging
    import codec_marketplace as cm

    code = "# skill without hash\n"

    class FakeResp:
        status_code = 200
        text = code

    import types
    fake_requests = types.ModuleType("requests")
    fake_requests.get = lambda url, timeout=30: FakeResp()
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    entry = {"name": "no-hash-skill", "file": "nohash/nohash.py"}
    with caplog.at_level(logging.WARNING, logger="codec_marketplace"):
        result = cm._download_skill(entry, {"base_url": "https://example.com"})
    assert result == code
    assert "no sha256" in caplog.text.lower() or "skipping integrity" in caplog.text.lower()
