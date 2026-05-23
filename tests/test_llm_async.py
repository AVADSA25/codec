"""Tests for PR-3E-async — A-12: codec_llm.acall() + astream().

Async siblings of call()/stream() for the queue-coupled httpx sites:
- acall  -> codec_agents (Agent.run, research-refiner): async non-stream.
- astream -> codec_voice._stream_qwen: async streaming; PROPAGATES exceptions
  (voice wraps it in try/except -> spoken error), unlike sync stream().

Both take an injected httpx async client (`http=`); when injected they do NOT
pass a per-request timeout (use the client's configured timeout — exact parity).
Driven via asyncio.run + fake async clients (no pytest-asyncio).

Reference: docs/PR3E-ASYNC-DESIGN.md (Option 2).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import codec_llm  # noqa: E402


# ── fakes ─────────────────────────────────────────────────────────────────────


class _AResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakePostClient:
    def __init__(self, handler):
        self._handler = handler  # (url, json) -> _AResp, or raises

    async def post(self, url, json=None, headers=None, **kw):
        return self._handler(url, json)


class _FakeStreamCM:
    def __init__(self, lines, status=200, raise_on_enter=None):
        self.status_code = status
        self._lines = lines
        self._raise = raise_on_enter

    async def __aenter__(self):
        if self._raise:
            raise self._raise
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln


class _FakeStreamClient:
    def __init__(self, lines=None, raise_on_enter=None):
        self._lines = lines or []
        self._raise = raise_on_enter

    def stream(self, method, url, json=None, headers=None, **kw):
        return _FakeStreamCM(self._lines, raise_on_enter=self._raise)


def _ok(content):
    return {"choices": [{"message": {"content": content}}]}


def _sse(content):
    return "data: " + json.dumps({"choices": [{"delta": {"content": content}}]})


def _empty():
    return "data: " + json.dumps({"choices": [{"delta": {}}]})


async def _collect(agen):
    out = []
    async for x in agen:
        out.append(x)
    return out


# ── acall (async non-stream) ──────────────────────────────────────────────────


def test_acall_success():
    http = _FakePostClient(lambda u, j: _AResp(200, _ok("hello")))
    out = asyncio.run(codec_llm.acall([{"role": "user", "content": "q"}],
                                      base_url="http://x/v1", model="m", http=http))
    assert out == "hello"


def test_acall_strips_think():
    http = _FakePostClient(lambda u, j: _AResp(200, _ok("<think>x</think>real")))
    out = asyncio.run(codec_llm.acall([{"role": "user", "content": "q"}],
                                      base_url="http://x/v1", model="m", http=http))
    assert out == "real"


def test_acall_default_returns_empty_on_non_200():
    http = _FakePostClient(lambda u, j: _AResp(500, {}))
    out = asyncio.run(codec_llm.acall([{"role": "user", "content": "q"}],
                                      base_url="http://x/v1", model="m", http=http))
    assert out == ""


def test_acall_raise_on_error_non_200():
    http = _FakePostClient(lambda u, j: _AResp(500, {}))
    with pytest.raises(codec_llm.LLMError):
        asyncio.run(codec_llm.acall([{"role": "user", "content": "q"}],
                                    base_url="http://x/v1", model="m",
                                    http=http, raise_on_error=True))


def test_acall_raise_on_error_exception():
    def boom(u, j):
        raise ConnectionError("down")
    http = _FakePostClient(boom)
    with pytest.raises(codec_llm.LLMError):
        asyncio.run(codec_llm.acall([{"role": "user", "content": "q"}],
                                    base_url="http://x/v1", model="m",
                                    http=http, raise_on_error=True))


def test_acall_passes_payload_and_auth():
    cap = {}

    def handler(u, j):
        cap["url"] = u
        cap["json"] = j
        return _AResp(200, _ok("ok"))

    asyncio.run(codec_llm.acall([{"role": "user", "content": "q"}],
                                base_url="http://x/v1", model="qwen", api_key="k",
                                max_tokens=4000, temperature=0.5,
                                http=_FakePostClient(handler)))
    assert cap["url"] == "http://x/v1/chat/completions"
    assert cap["json"]["model"] == "qwen"
    assert cap["json"]["max_tokens"] == 4000 and cap["json"]["temperature"] == 0.5
    assert cap["json"]["chat_template_kwargs"] == {"enable_thinking": False}


# ── astream (async streaming, propagates) ─────────────────────────────────────


def test_astream_yields_raw_deltas():
    http = _FakeStreamClient(lines=[_sse("a"), _sse("b"), "data: [DONE]", _sse("after")])
    out = asyncio.run(_collect(codec_llm.astream(
        [{"role": "user", "content": "q"}], base_url="http://x/v1", model="m", http=http)))
    assert out == ["a", "b"]


def test_astream_keepalive_yields_sentinel():
    http = _FakeStreamClient(lines=[_empty(), _empty(), _sse("hi"), "data: [DONE]"])
    out = asyncio.run(_collect(codec_llm.astream(
        [{"role": "user", "content": "q"}], base_url="http://x/v1", model="m",
        http=http, keepalive=True)))
    assert out == [codec_llm.KEEPALIVE, "hi"]


def test_astream_keepalive_off_by_default():
    http = _FakeStreamClient(lines=[_empty(), _empty(), _sse("hi"), "data: [DONE]"])
    out = asyncio.run(_collect(codec_llm.astream(
        [{"role": "user", "content": "q"}], base_url="http://x/v1", model="m", http=http)))
    assert out == ["hi"]


def test_astream_propagates_exception():
    # Unlike sync stream() (never-raises), astream lets errors propagate so
    # voice's try/except can speak the failure.
    http = _FakeStreamClient(raise_on_enter=ConnectionError("down"))
    with pytest.raises(ConnectionError):
        asyncio.run(_collect(codec_llm.astream(
            [{"role": "user", "content": "q"}], base_url="http://x/v1", model="m", http=http)))


# ── source-level migration invariants ─────────────────────────────────────────


def test_voice_uses_astream():
    src = (REPO / "codec_voice.py").read_text()
    assert "codec_llm.astream(" in src
    assert ".stream(\n" not in src and 'self._http.stream(' not in src  # inline async stream gone


def test_agents_use_acall():
    src = (REPO / "codec_agents.py").read_text()
    assert "codec_llm.acall(" in src
    assert "_async_http.post(_qwen_url()" not in src   # inline async POSTs gone
