"""Tests for PR-3E-2c — A-12 tranche 2c: codec_llm.call(raise_on_error=True).

- raise_on_error=True raises codec_llm.LLMError on EVERY non-success path
  (non-200, request exception, empty/unparseable 200). Default False keeps the
  existing never-raise -> "" contract (regression guard).
- The 4 fail-loud sites migrate: codec_agent_plan/_runner._qwen_chat adapt
  LLMError -> their public QwenUnavailableError; textassist + regen propagate.

Reference: docs/PR3E2C-RAISE-MODE-DESIGN.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import codec_llm  # noqa: E402


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _ok(content):
    return {"choices": [{"message": {"content": content}}]}


# ── codec_llm.LLMError + raise_on_error ───────────────────────────────────────


def test_llmerror_is_exception():
    assert issubclass(codec_llm.LLMError, Exception)


def test_raise_mode_success_returns_content(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(200, _ok("answer")))
    out = codec_llm.call([{"role": "user", "content": "q"}], base_url="http://x/v1",
                         model="m", raise_on_error=True)
    assert out == "answer"


def test_raise_mode_non_200_raises(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(500, text="boom"))
    monkeypatch.setattr(codec_llm.time, "sleep", lambda *_: None)
    with pytest.raises(codec_llm.LLMError):
        codec_llm.call([{"role": "user", "content": "q"}], base_url="http://x/v1",
                       model="m", retries=2, raise_on_error=True)


def test_raise_mode_exception_raises(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")

    import requests
    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(codec_llm.time, "sleep", lambda *_: None)
    with pytest.raises(codec_llm.LLMError):
        codec_llm.call([{"role": "user", "content": "q"}], base_url="http://x/v1",
                       model="m", raise_on_error=True)


def test_raise_mode_empty_200_raises(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(200, _ok("")))
    with pytest.raises(codec_llm.LLMError):
        codec_llm.call([{"role": "user", "content": "q"}], base_url="http://x/v1",
                       model="m", raise_on_error=True)


def test_default_mode_never_raises(monkeypatch):
    """Regression guard: raise_on_error defaults False -> "" on all failures."""
    import requests
    monkeypatch.setattr(codec_llm.time, "sleep", lambda *_: None)

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(500, text="x"))
    assert codec_llm.call([{"role": "user", "content": "q"}], base_url="http://x/v1",
                          model="m", retries=2) == ""

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(200, _ok("")))
    assert codec_llm.call([{"role": "user", "content": "q"}], base_url="http://x/v1",
                          model="m") == ""

    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(requests, "post", boom)
    assert codec_llm.call([{"role": "user", "content": "q"}], base_url="http://x/v1",
                          model="m") == ""


# ── agent_plan / agent_runner adapters (LLMError -> QwenUnavailableError) ──────


def test_agent_plan_qwen_chat_success(monkeypatch):
    import codec_agent_plan
    monkeypatch.setattr(codec_llm, "call", lambda *a, **k: "plan-text")
    assert codec_agent_plan._qwen_chat("hi", "sys") == "plan-text"


def test_agent_plan_qwen_chat_adapts_to_qwen_unavailable(monkeypatch):
    import codec_agent_plan

    def raise_llm(*a, **k):
        raise codec_llm.LLMError("boom-from-codec-llm")

    monkeypatch.setattr(codec_llm, "call", raise_llm)
    with pytest.raises(codec_agent_plan.QwenUnavailableError) as ei:
        codec_agent_plan._qwen_chat("hi", "sys")
    assert "boom-from-codec-llm" in str(ei.value)   # went through the adapter


def test_agent_runner_qwen_chat_success(monkeypatch):
    import codec_agent_runner
    monkeypatch.setattr(codec_llm, "call", lambda *a, **k: "action-json")
    assert codec_agent_runner._qwen_chat("hi", "sys") == "action-json"


def test_agent_runner_qwen_chat_adapts_to_qwen_unavailable(monkeypatch):
    import codec_agent_runner

    def raise_llm(*a, **k):
        raise codec_llm.LLMError("boom-runner")

    monkeypatch.setattr(codec_llm, "call", raise_llm)
    with pytest.raises(codec_agent_runner.QwenUnavailableError) as ei:
        codec_agent_runner._qwen_chat("hi", "sys")
    assert "boom-runner" in str(ei.value)


# ── source-level migration invariants ─────────────────────────────────────────


def test_textassist_uses_codec_llm():
    src = (REPO / "codec_textassist.py").read_text()
    assert "codec_llm.call(" in src
    assert "/chat/completions" not in src       # inline POST gone


def test_regen_uses_codec_llm():
    src = (REPO / "scripts" / "regen_skill_descriptions.py").read_text()
    assert "codec_llm.call(" in src
    assert ".raise_for_status(" not in src      # fail-loud now via LLMError (call, not prose)


def test_agent_plan_uses_codec_llm():
    src = (REPO / "codec_agent_plan.py").read_text()
    assert "codec_llm.call(" in src
    assert "raise_on_error=True" in src


def test_agent_runner_uses_codec_llm():
    src = (REPO / "codec_agent_runner.py").read_text()
    assert "codec_llm.call(" in src
    assert "raise_on_error=True" in src
