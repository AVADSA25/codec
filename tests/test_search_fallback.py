"""Web search: an outage is not cached as the answer (P-097, docs/SMALL-CORRECTNESS-FIXES-DESIGN.md).

When Serper raises or answers 429 (out of credit), codec_search.search answers with
DuckDuckGo, and neither the "Search error" row nor an empty list is kept in the
5-minute cache. Every HTTP call is a fake on codec_search.httpx: nothing here goes
to the network.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

import codec_config
import codec_search

DDG_ANSWER = {"AbstractText": "Marbella is a city in Andalusia.", "Heading": "Marbella",
              "AbstractURL": "https://example.org/marbella", "RelatedTopics": []}
SERPER_ANSWER = {"organic": [{"title": "Marbella", "link": "https://example.org/m",
                              "snippet": "A city on the Costa del Sol."}]}


class _Resp:
    def __init__(self, status, data=None, text=""):
        self.status_code, self._data, self.text = status, data, text

    def json(self):
        return self._data


@pytest.fixture
def net(monkeypatch):
    """Fake Serper (httpx.post to serper) and DuckDuckGo (httpx.get / html post)."""
    st = SimpleNamespace(serper=lambda: _Resp(200, SERPER_ANSWER),
                         ddg=lambda: _Resp(200, DDG_ANSWER),
                         ddg_html=lambda: _Resp(200, text=""),
                         calls=[])

    def post(url, **kw):
        st.calls.append(url)
        return st.serper() if "serper" in url else st.ddg_html()

    def get(url, **kw):
        st.calls.append(url)
        return st.ddg()

    monkeypatch.setattr(codec_search.httpx, "post", post)
    monkeypatch.setattr(codec_search.httpx, "get", get)
    monkeypatch.setattr(codec_config, "get_serper_api_key", lambda: "test-key")
    monkeypatch.setattr(codec_search, "_cache", {})
    return st


def _raise(exc):
    def f():
        raise exc
    return f


@pytest.mark.parametrize("serper", [
    _raise(httpx.ConnectError("no route")),
    lambda: _Resp(429, {"message": "Not enough credits", "statusCode": 429}),
    lambda: _Resp(500, {"message": "server error"}),
], ids=["raises", "429", "500"])
def test_serper_failure_falls_back_to_duckduckgo(net, serper):
    net.serper = serper
    results = codec_search.search("marbella", 5)
    assert results and results[0]["title"] == "Marbella"
    assert results[0]["snippet"].startswith("Marbella is a city")
    assert any("serper" in u for u in net.calls) and any("duckduckgo" in u for u in net.calls)


def test_neither_the_error_row_nor_an_empty_list_is_cached(net):
    net.serper = _raise(httpx.ConnectError("no route"))
    net.ddg = _raise(httpx.ConnectError("no route"))
    net.ddg_html = _raise(httpx.ConnectError("no route"))
    first = codec_search.search("marbella", 5)
    assert first and first[0]["title"] == "Search error"
    assert codec_search._cache == {}, "the error row must not be cached"

    net.serper = lambda: _Resp(200, {"organic": []})
    assert codec_search.search("marbella", 5) == []
    assert codec_search._cache == {}, "an empty list must not be cached"

    net.serper = lambda: _Resp(200, SERPER_ANSWER)
    assert codec_search.search("marbella", 5)[0]["link"] == "https://example.org/m"


def test_a_real_answer_is_still_cached(net):
    assert codec_search.search("marbella", 5)[0]["title"] == "Marbella"
    calls = len(net.calls)
    assert codec_search.search("marbella", 5)[0]["title"] == "Marbella"
    assert len(net.calls) == calls, "second ask answered from the cache"


def test_search_serper_alone_keeps_its_error_row(net):
    net.serper = lambda: _Resp(429, {"message": "Not enough credits"})
    row = codec_search.search_serper("marbella", "test-key", 5)
    assert row == [{"title": "Search error", "link": "", "snippet": "Serper answered HTTP 429"}]
