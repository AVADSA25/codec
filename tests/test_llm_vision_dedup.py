"""Tests for PR-3E — LLM-call (A-12) + vision (A-11) dedup.

- codec_llm.call: the canonical chat/completions caller (headers, payload,
  enable_thinking, <think> strip, content→reasoning extraction, retries).
- codec_vision.describe_sync/_async: canonical Gemini-Flash → local-Qwen-VL.
- First-tranche migrations: codec.py (vision + voice chat), codec_session
  (vision + qwen_call), codec_voice._analyze_screenshot.

Reference: docs/PR3E-LLM-VISION-DEDUP-DESIGN.md (Option 2).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import codec_llm  # noqa: E402
import codec_vision  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _msg(content=None, reasoning=None):
    m = {}
    if content is not None:
        m["content"] = content
    if reasoning is not None:
        m["reasoning"] = reasoning
    return {"choices": [{"message": m}]}


# ── codec_llm.extract_content + strip_think ──────────────────────────────────


def test_strip_think():
    assert codec_llm.strip_think("<think>plan</think>answer") == "answer"
    assert codec_llm.strip_think("  hi  ") == "hi"
    assert codec_llm.strip_think("") == ""


def test_extract_content_prefers_content():
    assert codec_llm.extract_content(_msg(content="hello")) == "hello"


def test_extract_content_reasoning_fallback():
    assert codec_llm.extract_content(_msg(content="", reasoning="fallback")) == "fallback"


def test_extract_content_strips_think():
    assert codec_llm.extract_content(_msg(content="<think>x</think>real")) == "real"


def test_extract_content_bad_shape_returns_empty():
    assert codec_llm.extract_content({}) == ""
    assert codec_llm.extract_content({"choices": []}) == ""


# ── codec_llm.call ───────────────────────────────────────────────────────────


def test_call_success(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _Resp(200, _msg(content="42"))

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    out = codec_llm.call([{"role": "user", "content": "q"}],
                         base_url="http://x/v1", model="qwen", api_key="k",
                         max_tokens=400, temperature=0.7, extra_kwargs={"top_p": 0.9})
    assert out == "42"
    assert captured["url"] == "http://x/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer k"
    p = captured["json"]
    assert p["model"] == "qwen"
    assert p["max_tokens"] == 400 and p["temperature"] == 0.7
    assert p["chat_template_kwargs"] == {"enable_thinking": False}
    assert p["top_p"] == 0.9  # extra_kwargs merged


def test_call_no_api_key_omits_auth(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["headers"] = headers
        return _Resp(200, _msg(content="ok"))

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    codec_llm.call([{"role": "user", "content": "q"}], base_url="http://x/v1", model="m")
    assert "Authorization" not in captured["headers"]


def test_call_retries_then_empty(monkeypatch):
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        return _Resp(500, text="err")

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(codec_llm.time, "sleep", lambda *_: None)  # no real backoff
    out = codec_llm.call([{"role": "user", "content": "q"}],
                         base_url="http://x/v1", model="m", retries=3)
    assert out == ""
    assert calls["n"] == 3  # all attempts used


def test_call_exception_returns_empty(monkeypatch):
    def fake_post(*a, **k):
        raise ConnectionError("down")

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(codec_llm.time, "sleep", lambda *_: None)
    assert codec_llm.call([{"role": "user", "content": "q"}],
                          base_url="http://x/v1", model="m", retries=2) == ""


# ── codec_vision.describe_sync ───────────────────────────────────────────────


def test_describe_sync_gemini_first(monkeypatch):
    monkeypatch.setattr(codec_vision, "_vision_config",
                        lambda: ("gemini", "gemkey", "http://local/v1", "qwen-vl"))
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        # Gemini response shape
        return _Resp(200, {"candidates": [{"content": {"parts": [{"text": "a chart"}]}}]})

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    out = codec_vision.describe_sync("b64", "what is this?", mime="image/png")
    assert out == "a chart"
    assert "generativelanguage.googleapis.com" in captured["url"]


def test_describe_sync_falls_back_to_local(monkeypatch):
    monkeypatch.setattr(codec_vision, "_vision_config",
                        lambda: ("gemini", "gemkey", "http://local/v1", "qwen-vl"))
    seen = []

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.append(url)
        if "googleapis" in url:
            return _Resp(500, text="gemini down")
        return _Resp(200, _msg(content="local says hi"))

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    out = codec_vision.describe_sync("b64", "p")
    assert out == "local says hi"
    assert any("googleapis" in u for u in seen) and any("local/v1" in u for u in seen)


def test_describe_sync_local_only_when_provider_local(monkeypatch):
    monkeypatch.setattr(codec_vision, "_vision_config",
                        lambda: ("local", "", "http://local/v1", "qwen-vl"))
    seen = []

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.append(url)
        return _Resp(200, _msg(content="local"))

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    out = codec_vision.describe_sync("b64", "p")
    assert out == "local"
    assert not any("googleapis" in u for u in seen)  # Gemini never tried


def test_describe_sync_both_fail_returns_empty(monkeypatch):
    monkeypatch.setattr(codec_vision, "_vision_config",
                        lambda: ("gemini", "k", "http://local/v1", "m"))
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(500, text="x"))
    assert codec_vision.describe_sync("b64", "p") == ""


# ── codec_vision.describe_async ──────────────────────────────────────────────


class _FakeAsyncClient:
    def __init__(self, handler):
        self._handler = handler

    async def post(self, url, json=None, headers=None, timeout=None):
        return self._handler(url, json)


def test_describe_async_gemini(monkeypatch):
    monkeypatch.setattr(codec_vision, "_vision_config",
                        lambda: ("gemini", "k", "http://local/v1", "m"))

    def handler(url, json):
        return _Resp(200, {"candidates": [{"content": {"parts": [{"text": "async vision"}]}}]})

    out = asyncio.run(codec_vision.describe_async("b64", "p", http=_FakeAsyncClient(handler)))
    assert out == "async vision"


def test_describe_async_fallback(monkeypatch):
    monkeypatch.setattr(codec_vision, "_vision_config",
                        lambda: ("gemini", "k", "http://local/v1", "m"))

    def handler(url, json):
        if "googleapis" in url:
            return _Resp(500)
        return _Resp(200, _msg(content="local async"))

    out = asyncio.run(codec_vision.describe_async("b64", "p", http=_FakeAsyncClient(handler)))
    assert out == "local async"


# ── source-level migration invariants ───────────────────────────────────────


def test_codec_vision_is_single_source():
    src = (REPO / "codec.py").read_text()
    assert "def _gemini_vision" not in src and "def _local_vision" not in src
    assert "codec_vision.describe_sync" in src


def test_codec_chat_uses_codec_llm():
    src = (REPO / "codec.py").read_text()
    assert "codec_llm.call(" in src
    # No inline chat/completions POST left in _dispatch_inner's LLM block
    assert 'f"{QWEN_BASE_URL}/chat/completions"' not in src


def test_voice_uses_codec_vision():
    src = (REPO / "codec_voice.py").read_text()
    assert "codec_vision.describe_async" in src
    # Inline gemini URL gone from the analyze path
    assert "generativelanguage.googleapis.com" not in src


def test_session_uses_canonical_helpers():
    src = (REPO / "codec_session.py").read_text()
    assert "codec_llm.call(" in src
    assert "codec_vision.describe_sync" in src
