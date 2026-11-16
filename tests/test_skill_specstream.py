"""Tests for skills/specstream.py - SpecStream web search and page extraction.

All HTTP requests and Keychain/config lookups are mocked or isolated under
tmp_path; nothing touches the live network or ~/.codec.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import requests

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _load():
    spec = importlib.util.spec_from_file_location("_skill_specstream", REPO / "skills" / "specstream.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeResp:
    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json_data = json_data
        self.headers = headers or {}

    def json(self):
        if isinstance(self._json_data, Exception):
            raise self._json_data
        return self._json_data


def test_metadata_contract():
    mod = _load()
    assert mod.SKILL_NAME == "specstream"
    assert isinstance(mod.SKILL_DESCRIPTION, str) and len(mod.SKILL_DESCRIPTION) >= 10
    assert isinstance(mod.SKILL_TRIGGERS, list) and "specstream" in mod.SKILL_TRIGGERS
    assert mod.SKILL_MCP_EXPOSE is True
    assert callable(mod.run)


def test_missing_api_key_makes_no_network_call(monkeypatch):
    mod = _load()
    import codec_config

    monkeypatch.setattr(codec_config, "SPECSTREAM_BASE_URL", "https://api.specstream.ai")
    monkeypatch.setattr(codec_config, "get_specstream_api_key", lambda: "")
    monkeypatch.setattr(mod.requests, "get", lambda *a, **kw: pytest.fail("unexpected GET"))
    monkeypatch.setattr(mod.requests, "post", lambda *a, **kw: pytest.fail("unexpected POST"))

    out = mod.run("specstream search python 3.13 release date")
    assert "not configured" in out
    assert "SPECSTREAM_API_KEY" in out


def test_empty_task_returns_usage_without_network(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod.requests, "get", lambda *a, **kw: pytest.fail("unexpected GET"))
    monkeypatch.setattr(mod.requests, "post", lambda *a, **kw: pytest.fail("unexpected POST"))

    for prompt in ("", "specstream", "specstream search"):
        out = mod.run(prompt)
        assert "Specify a search query or URL" in out


def test_invalid_base_url_refused_without_network(monkeypatch):
    mod = _load()
    import codec_config

    monkeypatch.setattr(codec_config, "SPECSTREAM_BASE_URL", "ftp://invalid.example")
    monkeypatch.setattr(codec_config, "get_specstream_api_key", lambda: "sk-test")
    monkeypatch.setattr(mod.requests, "get", lambda *a, **kw: pytest.fail("unexpected GET"))

    out = mod.run("specstream search test")
    assert "must start with http://" in out


def test_search_uses_codec_config_url_and_formats_results(monkeypatch):
    mod = _load()
    import codec_config

    monkeypatch.setattr(codec_config, "SPECSTREAM_BASE_URL", "https://custom.specstream.test/")
    monkeypatch.setattr(codec_config, "get_specstream_api_key", lambda: "sk-secret-123")
    monkeypatch.setitem(codec_config.cfg, "specstream_speed", "fast")

    seen = {}
    audits = []
    monkeypatch.setattr(mod, "_emit_audit", lambda event, **kw: audits.append((event, kw)))

    def fake_get(url, params=None, headers=None, timeout=None):
        seen["url"] = url
        seen["params"] = params
        seen["headers"] = headers
        seen["timeout"] = timeout
        return _FakeResp(
            200,
            {
                "data": {
                    "kind": "fact",
                    "answer": {"value": "Oct 7, 2024", "page": "https://docs.python.org/3.13/"},
                    "sources": [
                        {
                            "title": "Python 3.13.0 Release",
                            "url": "https://www.python.org/downloads/release/python-3130/",
                            "snippet": "Python 3.13.0 is the newest major release.",
                        }
                    ],
                }
            },
        )

    monkeypatch.setattr(mod.requests, "get", fake_get)

    out = mod.run("search with specstream for python 3.13 release date", app="agent:test")
    assert seen["url"] == "https://custom.specstream.test/v1/web-search"
    assert seen["params"] == {"q": "python 3.13 release date", "num": 5, "speed": "fast"}
    assert seen["headers"] == {"Authorization": "Bearer sk-secret-123"}
    assert "Answer: Oct 7, 2024" in out
    assert "Python 3.13.0 Release" in out
    assert "https://www.python.org/downloads/release/python-3130/" in out
    assert len(audits) == 1
    assert audits[0][0] == "specstream_search"
    assert audits[0][1]["outcome"] == "ok"
    assert audits[0][1]["extra"] == {"query_chars": len("python 3.13 release date"), "results": 1, "status_code": 200}


def test_search_fences_non_agent_output_and_falls_back_to_rows(monkeypatch):
    mod = _load()
    import codec_config

    monkeypatch.setattr(codec_config, "SPECSTREAM_BASE_URL", "https://api.specstream.ai")
    monkeypatch.setattr(codec_config, "get_specstream_api_key", lambda: "sk-test")
    monkeypatch.setitem(codec_config.cfg, "specstream_speed", "")

    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResp(
            200,
            {
                "data": {
                    "rows": [{"name": "Example Org", "page": "https://example.org", "snippet": "Row snippet"}],
                    "sources": [],
                }
            },
        )

    monkeypatch.setattr(mod.requests, "get", fake_get)
    out = mod.run("specstream search example org")
    assert "SpecStream web search results" in out
    assert "Example Org" in out
    assert "https://example.org" in out


def test_extract_posts_urls_and_handles_statuses(monkeypatch):
    mod = _load()
    import codec_config

    monkeypatch.setattr(codec_config, "SPECSTREAM_BASE_URL", "https://api.specstream.ai")
    monkeypatch.setattr(codec_config, "get_specstream_api_key", lambda: "sk-extract")

    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["url"] = url
        seen["json"] = json
        seen["headers"] = headers
        return _FakeResp(
            200,
            {
                "data": {
                    "results": [
                        {
                            "url": "https://example.com/a",
                            "status": "ok",
                            "title": "Example A",
                            "text": "Extracted body text.",
                        },
                        {
                            "url": "https://example.com/b",
                            "status": "not_found",
                            "text": "",
                        },
                    ]
                }
            },
        )

    monkeypatch.setattr(mod.requests, "post", fake_post)
    out = mod.run("specstream extract https://example.com/a, https://example.com/b", app="agent:1")
    assert seen["url"] == "https://api.specstream.ai/v1/extract"
    assert seen["json"] == {"urls": ["https://example.com/a", "https://example.com/b"], "format": "markdown"}
    assert seen["headers"]["Authorization"] == "Bearer sk-extract"
    assert "Example A (https://example.com/a)" in out
    assert "Extracted body text." in out
    assert "https://example.com/b: not_found" in out


@pytest.mark.parametrize(
    "status,body,headers,expected",
    [
        (401, {"error": {"code": "invalid_token"}}, {}, "rejected (401)"),
        (402, {"error": {"code": "insufficient_balance", "message": "Add credit"}}, {}, "balance or monthly limit reached (402): Add credit"),
        (429, {"error": {"code": "rate_limited"}}, {"Retry-After": "5"}, "Retry after 5s."),
        (503, {}, {}, "503"),
    ],
)
def test_http_errors_do_not_crash_or_leak_key(monkeypatch, status, body, headers, expected):
    mod = _load()
    import codec_config

    secret = "sk-do-not-leak-me"
    monkeypatch.setattr(codec_config, "SPECSTREAM_BASE_URL", "https://api.specstream.ai")
    monkeypatch.setattr(codec_config, "get_specstream_api_key", lambda: secret)
    monkeypatch.setattr(mod.requests, "get", lambda *a, **kw: _FakeResp(status, body, headers))

    out = mod.run("specstream search test")
    assert expected in out
    assert secret not in out


def test_network_timeout_and_connection_error(monkeypatch):
    mod = _load()
    import codec_config

    monkeypatch.setattr(codec_config, "SPECSTREAM_BASE_URL", "https://api.specstream.ai")
    monkeypatch.setattr(codec_config, "get_specstream_api_key", lambda: "sk-test")

    monkeypatch.setattr(mod.requests, "get", lambda *a, **kw: (_ for _ in ()).throw(requests.exceptions.Timeout()))
    assert "timed out" in mod.run("specstream search timeout")

    monkeypatch.setattr(mod.requests, "get", lambda *a, **kw: (_ for _ in ()).throw(requests.exceptions.ConnectionError()))
    assert "connection failed" in mod.run("specstream search conn")


def test_chat_allowlist_and_palette_group():
    from routes.chat import CHAT_SKILL_ALLOWLIST
    from routes.palette import skill_group

    assert "specstream" in CHAT_SKILL_ALLOWLIST
    assert skill_group("specstream") == "Tools"


def test_config_api_key_helper_env_fallback(tmp_path, monkeypatch):
    import codec_config as cc
    import codec_keychain as kc

    monkeypatch.setattr(kc, "is_keychain_available", lambda: False)
    monkeypatch.setattr(kc, "_FALLBACK_KEY_PATH", tmp_path / "secret.key")
    monkeypatch.setattr(kc, "_FALLBACK_STORE_PATH", tmp_path / "secrets.enc.json")
    monkeypatch.setattr(kc, "_SERVICE_PREFIX", f"ai.avadigital.codec._test_ss_{tmp_path.name}")
    kc._invalidate_negative_cache()
    cc._invalidate_secret_cache()
    monkeypatch.setitem(cc.cfg, "specstream_api_key", "")
    monkeypatch.delenv("SPECSTREAM_API_KEY", raising=False)
    assert cc.get_specstream_api_key() == ""

    cc._invalidate_secret_cache()
    monkeypatch.setenv("SPECSTREAM_API_KEY", "sk-env-key")
    assert cc.get_specstream_api_key() == "sk-env-key"
    cc._invalidate_secret_cache()
    kc._invalidate_negative_cache()
