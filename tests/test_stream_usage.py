"""codec_llm.stream(usage_sentinel=True) surfaces token counts.

The server reports usage in a FINAL chunk whose `choices` list is EMPTY. The
parser used to do choices[0] unconditionally, so that chunk raised IndexError and
the counts were swallowed as a parse failure — which is why reply stats were
impossible before.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import codec_llm


class _FakeResp:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    def iter_lines(self):
        for ln in self._lines:
            yield ln.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run(monkeypatch, lines, **kw):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None, stream=None):
        captured["payload"] = json
        return _FakeResp(lines)

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    out = list(codec_llm.stream([{"role": "user", "content": "hi"}],
                                base_url="http://x/v1", model="m", **kw))
    return out, captured


def _chunk(content):
    return "data: " + json.dumps(
        {"choices": [{"delta": {"content": content}}]})


USAGE_CHUNK = "data: " + json.dumps(
    {"choices": [], "usage": {"prompt_tokens": 15, "completion_tokens": 42,
                              "total_tokens": 57}})


def test_usage_is_yielded_as_a_typed_sentinel(monkeypatch):
    out, _ = _run(monkeypatch, [_chunk("hi"), USAGE_CHUNK, "data: [DONE]"],
                  usage_sentinel=True)
    usage = [x for x in out if isinstance(x, codec_llm.StreamUsage)]
    assert len(usage) == 1
    assert usage[0]["completion_tokens"] == 42
    # and it must NOT have leaked into the text stream
    assert "".join(x for x in out if isinstance(x, str)) == "hi"


def test_include_usage_only_requested_when_asked(monkeypatch):
    _, cap = _run(monkeypatch, [_chunk("hi"), "data: [DONE]"], usage_sentinel=True)
    assert cap["payload"]["stream_options"] == {"include_usage": True}
    _, cap2 = _run(monkeypatch, [_chunk("hi"), "data: [DONE]"])
    assert "stream_options" not in cap2["payload"]


def test_usage_chunk_does_not_break_the_default_path(monkeypatch):
    """A server that volunteers usage must not corrupt callers that didn't ask."""
    out, _ = _run(monkeypatch, [_chunk("a"), USAGE_CHUNK, _chunk("b"),
                                "data: [DONE]"])
    assert "".join(x for x in out if isinstance(x, str)) == "ab"
    assert not [x for x in out if isinstance(x, codec_llm.StreamUsage)]
