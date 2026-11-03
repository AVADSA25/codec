"""P-016: a slow call in a chat turn no longer stops the dashboard's event loop.

One event loop serves every page, poll, chat stream and the voice call. Each test
sends a chat on that loop with one slow call held for up to HOLD_S seconds (a
link fetch, a web search, the vision call, a non-stream reply), then sends
GET /api/status on the same loop. Status must answer in under FAST_S while the
slow call is still held, the slow call must run on a worker thread, and the
reply must be what it was before the change. A control puts a slow call on the
loop thread on purpose, and the same check must report it.

In process only (httpx.AsyncClient with ASGITransport): config, memory, qchat
and vibe databases in tmp_path, the model and the network replaced by fakes.
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time

import httpx
import pytest
from fastapi import FastAPI

import codec_dashboard  # noqa: F401  (the chat's system prompt is imported from it per request)
import routes.chat as chat
import routes.health as health
import routes.qchat as qchat
import routes.vibe as vibe

FAST_S = 0.25   # /api/status must answer within this while a chat waits
HOLD_S = 5.0    # the longest a held call keeps its thread
LINK = "https://example.org/page"


class Hold:
    """Stands in for a slow call: keeps its thread until released, or for `seconds`."""

    def __init__(self, result=None, seconds: float = HOLD_S):
        self.result, self.seconds = result, seconds
        self.started, self.release = threading.Event(), threading.Event()
        self.thread = None
        self.released = None
        self.calls: list = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        self.thread = threading.get_ident()
        self.started.set()
        self.released = self.release.wait(self.seconds)
        return self.result


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


@pytest.fixture
def env(tmp_path, monkeypatch):
    import codec_config
    import codec_daybreak
    import codec_llm
    import codec_memory
    import codec_models
    import codec_observer
    import codec_standing_rules
    import routes.prompts as prompts

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"llm_base_url": "http://localhost:8083/v1", "llm_model": "test-model",
                               "llm_provider": "test", "chat": {"llm_timeout_s": 5}}))
    monkeypatch.setattr(chat, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(health, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(codec_models, "ensure_llm_available", lambda: None)
    monkeypatch.setattr(codec_models, "model_extras", lambda *a, **k: {})
    monkeypatch.setattr(codec_config, "get_llm_api_key", lambda: "")
    monkeypatch.setattr(codec_standing_rules, "prompt_block", lambda **k: "")
    monkeypatch.setattr(codec_daybreak, "get_working_context", lambda: "")
    monkeypatch.setattr(codec_observer, "maybe_inject_observation_summary", lambda **k: ("", "skip"))
    monkeypatch.setattr(prompts, "_load_prompt_overrides", lambda: {})

    used: list = []           # (what, thread) for memory, qchat and vibe reads
    model_calls: list = []    # the messages each model call received
    audits: list = []

    def fake_llm(messages, **kw):
        model_calls.append([dict(m) for m in messages])
        return "FAKE REPLY"

    class FakeMemory:
        def __init__(self, *a, **k):
            used.append(("memory", threading.get_ident()))

        def get_context(self, text, n=8):
            return "an old talk about the tower"

        def search_recent(self, days=3, limit=5, **kw):
            return [{"timestamp": "2026-11-01T10:00:00", "role": "user", "content": "the tower is tall"}]

    monkeypatch.setattr(codec_llm, "call", fake_llm)
    monkeypatch.setattr(codec_memory, "CodecMemory", FakeMemory)
    monkeypatch.setattr(chat, "log_event", lambda *a, **k: audits.append((a, k)))

    monkeypatch.setattr(qchat, "QCHAT_DB", str(tmp_path / "qchat.db"))
    monkeypatch.setattr(qchat, "_qchat_conn", None)
    monkeypatch.setattr(vibe, "VIBE_DB", str(tmp_path / "vibe.db"))
    monkeypatch.setattr(vibe, "_vibe_conn", None)
    real_qchat, real_vibe = qchat.qchat_db, vibe.vibe_db
    real_qchat().execute("INSERT INTO qchat_messages (session_id, role, content, timestamp) VALUES "
                         "('s1', 'user', 'earlier: do you remember " + LINK + " yes', '2026-11-01T09:00')")
    real_qchat().commit()
    real_vibe().execute("INSERT INTO vibe_messages (session_id, role, content, timestamp) VALUES "
                        "('v1', 'user', 'code note: do you remember " + LINK + " ok', '2026-11-01T09:00')")
    real_vibe().commit()

    def watched(name, real):
        def get():
            used.append((name, threading.get_ident()))
            return real()
        return get

    monkeypatch.setattr(qchat, "qchat_db", watched("qchat", real_qchat))
    monkeypatch.setattr(vibe, "vibe_db", watched("vibe", real_vibe))

    app = FastAPI()
    app.include_router(chat.router)
    app.include_router(health.router)
    yield type("Env", (), {"app": app, "used": used, "model_calls": model_calls, "audits": audits})
    for conn in (qchat._qchat_conn, vibe._vibe_conn):
        if conn is not None:
            conn.close()


def _run(app, hold: Hold, body: dict):
    """Send the chat, wait until the held call starts, then time GET /api/status."""
    async def main():
        loop_thread = threading.get_ident()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://127.0.0.1") as client:
            chat_task = asyncio.create_task(client.post("/api/chat", json=body))
            deadline = time.monotonic() + HOLD_S + 5
            while not hold.started.is_set():
                if chat_task.done():
                    pytest.fail(f"the chat ended before the slow call: {chat_task.result().text}")
                assert time.monotonic() < deadline, "the slow call never started"
                await asyncio.sleep(0.005)
            t0 = time.perf_counter()
            status = await client.get("/api/status")
            took = time.perf_counter() - t0
            hold.release.set()
            reply = await chat_task
        return loop_thread, status, took, reply

    return asyncio.run(main())


def _problems(loop_thread, hold: Hold, status, took: float) -> list:
    out = []
    if status.status_code != 200 or "alive" not in status.json():
        out.append(f"/api/status answered {status.status_code}")
    if took >= FAST_S:
        out.append(f"/api/status took {took:.3f} s")
    if hold.released is not True:
        out.append("/api/status could not answer while the slow call was held")
    if hold.thread == loop_thread:
        out.append("the slow call was held on the loop thread")
    return out


def test_status_answers_while_a_link_fetch_holds_its_thread(env, monkeypatch):
    hold = Hold(result="PAGE TEXT")
    monkeypatch.setattr(chat, "_fetch_url_content", hold)
    loop_thread, status, took, reply = _run(env.app, hold, {
        "messages": [{"role": "user", "content": f"do you remember {LINK}"}], "stream": False, "tools": False})
    assert _problems(loop_thread, hold, status, took) == []
    assert reply.status_code == 200 and reply.json()["response"] == "FAKE REPLY"
    assert hold.calls == [((LINK,), {})]
    (sent,) = env.model_calls
    context = sent[-2]["content"]
    # P-021 fences the page text with a random marker per call.
    assert re.search(re.escape(f"[URL CONTENT: {LINK}]\n[DATA ") + r"(\w+)\] Text fetched from a web page"
                     r".*\nPAGE TEXT\n\[END DATA \1\]\n\[END URL CONTENT\]", context, re.S)
    assert context.index("[MEMORY") < context.index("[URL CONTENT"), "memory first, then the link"
    assert sent[-1] == {"role": "user", "content": f"do you remember {LINK}"}
    used = {(m["source"], m["kind"]) for m in reply.json()["memory"]}
    assert {("voice", "relevant"), ("chat", "relevant"), ("vibe", "relevant")} <= used


def test_status_answers_while_a_web_search_holds_its_thread(env, monkeypatch):
    import codec_search
    hold = Hold(result=[{"title": "Tallest tower", "snippet": "It is 300 m.", "link": "https://e.test/t"}])
    monkeypatch.setattr(codec_search, "search", hold)
    loop_thread, status, took, reply = _run(env.app, hold, {
        "messages": [{"role": "user", "content": "tallest tower in Lisbon"}], "stream": False, "tools": False,
        "force_search": True})
    assert _problems(loop_thread, hold, status, took) == []
    assert reply.status_code == 200 and reply.json()["response"] == "FAKE REPLY"
    assert reply.json()["web"] == {"searched": True, "results": 1}
    assert hold.calls == [(("tallest tower in Lisbon",), {"max_results": 5})]
    (sent,) = env.model_calls
    assert "[WEB SEARCH RESULTS]\n1. Tallest tower\n   It is 300 m." in sent[-2]["content"]
    (event,) = [a for a in env.audits if a[0][0] == "web_search_sent"]
    assert event[1]["extra"]["results"] == 1 and event[1]["outcome"] == "ok"


def test_status_answers_while_a_nonstream_reply_holds_its_thread(env, monkeypatch):
    import codec_llm
    hold = Hold(result="SLOW REPLY")
    monkeypatch.setattr(codec_llm, "call", hold)
    loop_thread, status, took, reply = _run(env.app, hold, {
        "messages": [{"role": "user", "content": "hello there"}], "stream": False, "tools": False})
    assert _problems(loop_thread, hold, status, took) == []
    assert reply.status_code == 200
    assert (reply.json()["response"], reply.json()["model"]) == ("SLOW REPLY", "test-model")
    ((args, kwargs),) = hold.calls
    assert args[0][-1] == {"role": "user", "content": "hello there"} and kwargs["raise_on_error"] is True
    assert kwargs["model"] == "test-model" and kwargs["timeout"] == 5.0


def test_status_answers_while_the_vision_call_holds_its_thread(env, monkeypatch):
    import requests
    hold = Hold(result=FakeResponse({"choices": [{"message": {"content": "<think>hm</think> A red cat."}}]}))
    monkeypatch.setattr(requests, "post", hold)
    loop_thread, status, took, reply = _run(env.app, hold, {
        "messages": [{"role": "user", "content": "what is this"}], "images": ["aGVsbG8="],
        "stream": False, "tools": False})
    assert _problems(loop_thread, hold, status, took) == []
    assert reply.status_code == 200
    assert reply.json() == {"response": "A red cat.", "model": "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"}
    ((args, kwargs),) = hold.calls
    assert args[0] == "http://localhost:8083/v1/chat/completions" and kwargs["timeout"] == 120
    assert kwargs["json"]["messages"][0]["content"][-1] == {"type": "text", "text": "what is this"}
    assert env.model_calls == [], "an image goes to the vision model only"


def test_the_harness_reports_a_slow_call_left_on_the_loop_control(env, monkeypatch):
    """Negative control: memory recall stays on the loop by design, so holding it
    there must be reported by the same check the tests above pass."""
    hold = Hold(result=[], seconds=0.6)
    monkeypatch.setattr(chat, "_recall_memory", hold)
    loop_thread, status, took, reply = _run(env.app, hold, {
        "messages": [{"role": "user", "content": "hello there"}], "stream": False, "tools": False})
    problems = _problems(loop_thread, hold, status, took)
    assert "/api/status could not answer while the slow call was held" in problems
    assert "the slow call was held on the loop thread" in problems
    assert reply.status_code == 200 and reply.json()["response"] == "FAKE REPLY"


def test_qchat_and_vibe_are_used_on_the_loop_thread_only(env, monkeypatch):
    hold = Hold(result="PAGE TEXT")
    monkeypatch.setattr(chat, "_fetch_url_content", hold)
    loop_thread, status, took, reply = _run(env.app, hold, {
        "messages": [{"role": "user", "content": f"do you remember {LINK}"}], "stream": False, "tools": False})
    assert reply.status_code == 200 and hold.thread != loop_thread
    names = {name for name, _ in env.used}
    assert names == {"memory", "qchat", "vibe"}, env.used
    assert {thread for _, thread in env.used} == {loop_thread}, "a shared connection was used off the loop"
