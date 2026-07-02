"""Tests for PR-3E-2 — A-12 tranche 2: codec_llm.stream() + first migrations.

- codec_llm.stream: canonical SSE streaming caller. Yields RAW content deltas
  (think-stripping is the caller's job), stops on `data: [DONE]`, tolerates
  blank/garbage lines, never raises (HTTP/conn error -> empty stream).
- codec_llm._build_request: shared header/payload builder for call() + stream().
- Migrations: codec_session.Session.qwen_stream (streaming proof), and the
  non-streaming trivials codec_compaction.compact_context + codec_dictate.

Reference: docs/PR3E2-LLM-STREAM-TRANCHE2-DESIGN.md (Option 1).
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import codec_llm  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────


class _StreamResp:
    """Fake `requests` streaming response usable as a context manager."""

    def __init__(self, status, lines=None, text=""):
        self.status_code = status
        self._lines = lines or []
        self.text = text

    def iter_lines(self):
        for ln in self._lines:
            yield ln if isinstance(ln, (bytes, bytearray)) else ln.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _sse(content):
    return "data: " + json.dumps({"choices": [{"delta": {"content": content}}]})


def _empty_chunk():
    """An SSE chunk with no content (the LLM 'thinking' phase)."""
    return "data: " + json.dumps({"choices": [{"delta": {}}]})


# ── codec_llm.stream ──────────────────────────────────────────────────────────


def test_stream_yields_raw_deltas_and_stops_at_done(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None, stream=None):
        captured["url"] = url
        captured["json"] = json
        captured["stream_kw"] = stream
        return _StreamResp(200, lines=[
            _sse("Hello"),
            "",                       # blank keepalive — skipped
            _sse(" <think>plan</think>"),  # raw, NOT stripped by stream()
            _sse(" world"),
            "data: [DONE]",
            _sse(" AFTER-DONE"),      # never reached
        ])

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    toks = list(codec_llm.stream([{"role": "user", "content": "q"}],
                                 base_url="http://x/v1", model="m"))
    assert toks == ["Hello", " <think>plan</think>", " world"]
    assert captured["url"] == "http://x/v1/chat/completions"
    assert captured["stream_kw"] is True            # requests stream= kwarg
    assert captured["json"]["stream"] is True        # payload stream flag


def test_stream_skips_blank_and_garbage_lines(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None, stream=None):
        return _StreamResp(200, lines=[
            "",                          # blank
            ": keepalive comment",       # non-data line
            "data: {not valid json",     # bad JSON -> skipped, no raise
            _sse(""),                     # empty content delta -> not yielded
            _sse("ok"),
            "data: [DONE]",
        ])

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    assert list(codec_llm.stream([{"role": "user", "content": "q"}],
                                 base_url="http://x/v1", model="m")) == ["ok"]


def test_stream_non_200_returns_empty(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: _StreamResp(500, text="boom"))
    assert list(codec_llm.stream([{"role": "user", "content": "q"}],
                                 base_url="http://x/v1", model="m")) == []


def test_stream_exception_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")

    import requests
    monkeypatch.setattr(requests, "post", boom)
    assert list(codec_llm.stream([{"role": "user", "content": "q"}],
                                 base_url="http://x/v1", model="m")) == []


def test_stream_auth_header_and_extra_kwargs(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None, stream=None):
        captured["headers"] = headers
        captured["json"] = json
        return _StreamResp(200, lines=["data: [DONE]"])

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    list(codec_llm.stream([{"role": "user", "content": "q"}],
                          base_url="http://x/v1", model="m", api_key="k",
                          extra_kwargs={"top_p": 0.8}))
    assert captured["headers"]["Authorization"] == "Bearer k"
    assert captured["json"]["top_p"] == 0.8
    assert captured["json"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_stream_no_auth_without_key(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None, stream=None):
        captured["headers"] = headers
        return _StreamResp(200, lines=["data: [DONE]"])

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    list(codec_llm.stream([{"role": "user", "content": "q"}],
                          base_url="http://x/v1", model="m"))
    assert "Authorization" not in captured["headers"]


def test_call_and_stream_share_payload_shape(monkeypatch):
    """call() must NOT carry a stream flag; stream() must. Both share the rest."""
    seen = {}

    def fake_call_post(url, json=None, headers=None, timeout=None):
        seen["call"] = json
        from test_llm_vision_dedup import _Resp  # reuse non-stream fake
        return _Resp(200, {"choices": [{"message": {"content": "x"}}]})

    def fake_stream_post(url, json=None, headers=None, timeout=None, stream=None):
        seen["stream"] = json
        return _StreamResp(200, lines=["data: [DONE]"])

    import requests
    monkeypatch.setattr(requests, "post", fake_call_post)
    codec_llm.call([{"role": "user", "content": "q"}], base_url="http://x/v1",
                   model="m", max_tokens=123, temperature=0.4)
    monkeypatch.setattr(requests, "post", fake_stream_post)
    list(codec_llm.stream([{"role": "user", "content": "q"}], base_url="http://x/v1",
                          model="m", max_tokens=123, temperature=0.4))

    for shape in (seen["call"], seen["stream"]):
        assert shape["model"] == "m"
        assert shape["max_tokens"] == 123 and shape["temperature"] == 0.4
        assert shape["chat_template_kwargs"] == {"enable_thinking": False}
    assert "stream" not in seen["call"]      # parity with PR-3E call()
    assert seen["stream"]["stream"] is True


# ── codec_session.Session.qwen_stream migration ───────────────────────────────


def _session_stub(qwen_call_ret="FALLBACK", fb_flag=None):
    def _qc(messages):
        if fb_flag is not None:
            fb_flag["hit"] = True
        return qwen_call_ret
    return types.SimpleNamespace(
        qwen_base_url="http://x/v1", qwen_model="m", llm_api_key="",
        llm_kwargs={}, qwen_call=_qc,
    )


def test_qwen_stream_consumes_codec_llm_and_strips_think(monkeypatch, capsys):
    import codec_session
    monkeypatch.setattr(codec_llm, "stream",
                        lambda *a, **k: iter(["Hel", "lo", " <think>x</think>"]))
    stub = _session_stub()
    out = codec_session.Session.qwen_stream(stub, [{"role": "user", "content": "q"}])
    assert out == "Hello"                     # strip_think on accumulated full
    assert "Hello" in capsys.readouterr().out  # deltas written live to stdout


def test_qwen_stream_falls_back_on_empty_stream(monkeypatch):
    import codec_session
    monkeypatch.setattr(codec_llm, "stream", lambda *a, **k: iter([]))
    fb = {}
    stub = _session_stub(qwen_call_ret="NONSTREAM", fb_flag=fb)
    out = codec_session.Session.qwen_stream(stub, [{"role": "user", "content": "q"}])
    assert out == "NONSTREAM" and fb["hit"] is True


# ── codec_compaction.compact_context migration ────────────────────────────────


def _msgs(n):
    return [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"message number {i}"} for i in range(n)]


def test_compaction_uses_codec_llm(monkeypatch):
    import codec_compaction
    monkeypatch.setattr(codec_llm, "call", lambda *a, **k: "A crisp summary.")
    out = codec_compaction.compact_context(_msgs(8), max_recent=3)
    assert "A crisp summary." in out
    assert "[SUMMARY OF EARLIER CONVERSATION]" in out


def test_compaction_fallback_on_empty(monkeypatch):
    import codec_compaction
    monkeypatch.setattr(codec_llm, "call", lambda *a, **k: "")
    out = codec_compaction.compact_context(_msgs(8), max_recent=3)
    assert "Previous context:" in out          # rule-based fallback summary


# ── source-level migration invariants ─────────────────────────────────────────


def test_session_qwen_stream_uses_codec_llm_stream():
    src = (REPO / "codec_session.py").read_text()
    assert "codec_llm.stream(" in src
    assert ".iter_lines(" not in src           # inline SSE loop gone (call, not prose)


def test_compaction_uses_codec_llm_call():
    src = (REPO / "codec_compaction.py").read_text()
    assert "codec_llm.call(" in src
    assert "httpx.post(" not in src            # inline POST gone


def test_dictate_uses_codec_llm_call():
    src = (REPO / "codec_dictate.py").read_text()
    assert "codec_llm.call(" in src
    assert "localhost:8083/v1/chat/completions" not in src  # inline URL gone


# ── codec_llm.stream(keepalive=) — PR-3E-chat-stream ──────────────────────────


def test_stream_keepalive_yields_sentinel(monkeypatch):
    # 1st empty -> KEEPALIVE (count 1, 1%10==1); 2nd empty -> nothing; then content.
    def fake_post(url, json=None, headers=None, timeout=None, stream=None):
        return _StreamResp(200, lines=[
            _empty_chunk(), _empty_chunk(), _sse("hi"), "data: [DONE]",
        ])

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    out = list(codec_llm.stream([{"role": "user", "content": "q"}],
                                base_url="http://x/v1", model="m", keepalive=True))
    assert out == [codec_llm.KEEPALIVE, "hi"]


def test_stream_keepalive_off_by_default(monkeypatch):
    # Same empties, but keepalive defaults False -> no sentinel (qwen_stream contract).
    def fake_post(url, json=None, headers=None, timeout=None, stream=None):
        return _StreamResp(200, lines=[
            _empty_chunk(), _empty_chunk(), _sse("hi"), "data: [DONE]",
        ])

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    out = list(codec_llm.stream([{"role": "user", "content": "q"}],
                                base_url="http://x/v1", model="m"))
    assert out == ["hi"]


def test_stream_keepalive_cadence_every_tenth(monkeypatch):
    # 11 empty chunks -> KEEPALIVE on the 1st and 11th only.
    lines = [_empty_chunk() for _ in range(11)] + ["data: [DONE]"]

    def fake_post(url, json=None, headers=None, timeout=None, stream=None):
        return _StreamResp(200, lines=lines)

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    out = list(codec_llm.stream([{"role": "user", "content": "q"}],
                                base_url="http://x/v1", model="m", keepalive=True))
    assert out == [codec_llm.KEEPALIVE, codec_llm.KEEPALIVE]


# ── error_sentinel (2026-07 chat-visibility fix) ─────────────────────────────


def _sse_finish(reason):
    return "data: " + json.dumps(
        {"choices": [{"delta": {}, "finish_reason": reason}]})


def test_stream_error_sentinel_on_non_200(monkeypatch):
    monkeypatch.setattr("requests.post",
                        lambda *a, **kw: _StreamResp(503, text="busy"))
    out = list(codec_llm.stream(
        [{"role": "user", "content": "x"}],
        base_url="http://x", model="m", error_sentinel=True))
    assert out == [codec_llm.STREAM_ERROR]


def test_stream_error_sentinel_on_exception(monkeypatch):
    def _boom(*a, **kw):
        raise ConnectionError("dropped")
    monkeypatch.setattr("requests.post", _boom)
    out = list(codec_llm.stream(
        [{"role": "user", "content": "x"}],
        base_url="http://x", model="m", error_sentinel=True))
    assert out == [codec_llm.STREAM_ERROR]


def test_stream_finish_length_sentinel(monkeypatch):
    lines = [_sse("partial answer"), _sse_finish("length")]
    monkeypatch.setattr("requests.post",
                        lambda *a, **kw: _StreamResp(200, lines))
    out = list(codec_llm.stream(
        [{"role": "user", "content": "x"}],
        base_url="http://x", model="m", error_sentinel=True))
    assert out == ["partial answer", codec_llm.FINISH_LENGTH]


def test_stream_clean_stop_no_sentinels(monkeypatch):
    lines = [_sse("full answer"), _sse_finish("stop"), "data: [DONE]"]
    monkeypatch.setattr("requests.post",
                        lambda *a, **kw: _StreamResp(200, lines))
    out = list(codec_llm.stream(
        [{"role": "user", "content": "x"}],
        base_url="http://x", model="m", error_sentinel=True))
    assert out == ["full answer"]


def test_stream_sentinels_off_by_default(monkeypatch):
    """Existing callers (no error_sentinel) keep the old contract: errors and
    length-stops just end the stream with no sentinel objects."""
    monkeypatch.setattr("requests.post",
                        lambda *a, **kw: _StreamResp(500, text="err"))
    out = list(codec_llm.stream(
        [{"role": "user", "content": "x"}],
        base_url="http://x", model="m"))
    assert out == []
