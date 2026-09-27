"""Cloud fallback model (codec_cloud_models + its hooks in codec_llm,
codec_models, codec_voice and the Project-mode agents).

The properties that matter: a switch to the cloud never touches the local
server and is undone exactly when the model does not answer; every cloud call
carries the entry's key and kwargs and is charged to the monthly ledger; a
blocked call never leaves the Mac; local calls are unchanged.
docs/CLOUD-FALLBACK-MODEL-DESIGN.md
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import codec_cloud_models  # noqa: E402
import codec_llm  # noqa: E402
import codec_models  # noqa: E402

CLOUD_URL = "https://cloud.example/v1"
LOCAL_URL = "http://localhost:8083/v1"
CLOUD = {"id": "cloud-pro", "label": "Cloud Pro (cloud)", "base_url": CLOUD_URL,
         "key_slot": "test_cloud_key", "kwargs": {"thinking": {"type": "disabled"}},
         "price_in_per_m": 0.5, "price_out_per_m": 1.0, "monthly_cap_usd": 10}
MSGS = [{"role": "user", "content": "hi"}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "llm_model": "mlx-community/A", "llm_base_url": LOCAL_URL,
        "llm_kwargs": {"chat_template_kwargs": {"enable_thinking": False}},
        "extra_models": [dict(CLOUD)]}))
    spend = tmp_path / "cloud_spend.json"
    for mod in (codec_models, codec_cloud_models):
        monkeypatch.setattr(mod, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(codec_cloud_models, "SPEND_PATH", str(spend))
    monkeypatch.setattr(codec_cloud_models, "get_key", lambda e: "sk-test")
    monkeypatch.setattr(codec_llm, "_cloud_blocked_msg", lambda url: None)
    monkeypatch.setattr(codec_models.shutil, "which", lambda _n: None)  # never a real pm2
    monkeypatch.setattr(codec_models, "discover_local",
                        lambda: [{"id": "mlx-community/A", "label": "A", "size_gb": 1.0},
                                 {"id": "mlx-community/B", "label": "B", "size_gb": 2.0}])
    return cfg_path, spend


def _cfg(env):
    return json.loads(env[0].read_text())


def _ledger(env):
    data = json.loads(env[1].read_text())
    return data[time.strftime("%Y-%m")]["cloud-pro"]


def _no_http(*a, **kw):
    raise AssertionError("a blocked call must not send a request")


class _Resp:
    def __init__(self, payload=None, lines=None):
        self.status_code, self._payload, self._lines, self.text = 200, payload, lines or [], ""

    def json(self):
        return self._payload

    def iter_lines(self):
        yield from (ln.encode() for ln in self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _sse(obj):
    return "data: " + json.dumps(obj)


# ── Switching ────────────────────────────────────────────────────────────────

def test_picker_lists_cloud_model_with_monthly_spend(env):
    m = next(x for x in codec_models.list_models()["models"] if x["id"] == "cloud-pro")
    assert m["cloud"] is True and m["active"] is False
    assert "$0.00 of $10 this month" in m["role"]


def test_switch_to_cloud_rewrites_config_and_never_restarts(env, monkeypatch):
    monkeypatch.setattr(codec_models, "restart_server",
                        lambda *a, **k: pytest.fail("cloud switch must not touch the local server"))
    monkeypatch.setattr(codec_models, "probe", lambda m, **k: (True, "answered in 1s"))
    r = codec_models.set_active("cloud-pro")
    c = _cfg(env)
    assert r["ok"] and r["active"] == "cloud-pro" and r["cloud"]
    assert c["llm_base_url"] == CLOUD_URL and c["llm_model"] == "cloud-pro"
    assert c["llm_local_restore"] == {"llm_base_url": LOCAL_URL, "llm_model": "mlx-community/A"}
    assert c["llm_kwargs"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_failed_cloud_switch_restores_config_exactly(env, monkeypatch):
    before = _cfg(env)
    monkeypatch.setattr(codec_models, "restart_server", lambda *a, **k: pytest.fail("no restart"))
    monkeypatch.setattr(codec_models, "probe", lambda m, **k: (False, "HTTP 401"))
    r = codec_models.set_active("cloud-pro")
    assert r["ok"] is False and r["active"] == "mlx-community/A" and "HTTP 401" in r["error"]
    assert _cfg(env) == before


def test_switch_back_to_local_restores_base_url(env, monkeypatch):
    monkeypatch.setattr(codec_models, "probe", lambda m, **k: (True, "ok"))
    restarts = []
    monkeypatch.setattr(codec_models, "restart_server",
                        lambda *a, **k: restarts.append(1) or (True, "restarted"))
    codec_models.set_active("cloud-pro")
    r = codec_models.set_active("mlx-community/B")
    c = _cfg(env)
    assert r["ok"] and c["llm_base_url"] == LOCAL_URL and c["llm_model"] == "mlx-community/B"
    assert "llm_local_restore" not in c and restarts == [1]


def test_failed_local_load_goes_back_to_the_cloud_model(env, monkeypatch):
    monkeypatch.setattr(codec_models, "probe",
                        lambda m, **k: (m != "mlx-community/B", "probed"))
    restarts = []
    monkeypatch.setattr(codec_models, "restart_server",
                        lambda *a, **k: restarts.append(1) or (False, "no pm2"))
    codec_models.set_active("cloud-pro")
    r = codec_models.set_active("mlx-community/B")
    c = _cfg(env)
    assert r["ok"] is False and r["active"] == "cloud-pro" and r["reverted"] is True
    assert c["llm_base_url"] == CLOUD_URL and c["llm_model"] == "cloud-pro"
    assert c["llm_local_restore"]["llm_model"] == "mlx-community/A"
    assert restarts == [1], "going back to the cloud must not restart the local server"


def test_capped_model_cannot_be_switched_to(env, monkeypatch):
    env[1].write_text(json.dumps({time.strftime("%Y-%m"): {"cloud-pro": {"usd": 10.0}}}))
    import requests
    monkeypatch.setattr(requests, "post", _no_http)
    before = _cfg(env)
    r = codec_models.set_active("cloud-pro")          # real probe path
    assert r["ok"] is False and "monthly spend cap reached" in r["error"]
    assert _cfg(env) == before


# ── Requests and metering ────────────────────────────────────────────────────

def test_cloud_request_gets_key_model_kwargs_and_is_charged(env, monkeypatch):
    seen = {}

    def post(url, json=None, headers=None, **kw):
        seen.update(url=url, json=json, headers=headers)
        return _Resp({"choices": [{"message": {"content": "READY"}}],
                      "usage": {"prompt_tokens": 1000, "completion_tokens": 500}})

    import requests
    monkeypatch.setattr(requests, "post", post)
    out = codec_llm.call(MSGS, base_url=CLOUD_URL, model="mlx-community/A",
                         extra_kwargs={"chat_template_kwargs": {"enable_thinking": False},
                                       "top_p": 0.9})
    assert out == "READY"
    body = seen["json"]
    assert body["model"] == "cloud-pro" and body["thinking"] == {"type": "disabled"}
    assert "chat_template_kwargs" not in body and body["top_p"] == 0.9
    assert seen["headers"]["Authorization"] == "Bearer sk-test"
    assert _ledger(env)["usd"] == pytest.approx(1000 * 0.5 / 1e6 + 500 * 1.0 / 1e6)


def test_local_request_is_unchanged_and_not_charged(env, monkeypatch):
    seen = {}

    def post(url, json=None, headers=None, **kw):
        seen.update(json=json, headers=headers)
        return _Resp({"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 9}})

    import requests
    monkeypatch.setattr(requests, "post", post)
    codec_llm.call(MSGS, base_url=LOCAL_URL, model="mlx-community/A")
    assert seen["json"]["model"] == "mlx-community/A"
    assert "chat_template_kwargs" in seen["json"] and "Authorization" not in seen["headers"]
    assert not env[1].exists()


def test_stream_charges_the_usage_chunk_once(env, monkeypatch):
    seen = {}

    def post(url, json=None, headers=None, **kw):
        seen["json"] = json
        return _Resp(lines=[_sse({"choices": [{"delta": {"content": "Hel"}}]}),
                            _sse({"choices": [{"delta": {"content": "lo"}}]}),
                            _sse({"choices": [], "usage": {"prompt_tokens": 200, "completion_tokens": 2}}),
                            "data: [DONE]"])

    import requests
    monkeypatch.setattr(requests, "post", post)
    out = [t for t in codec_llm.stream(MSGS, base_url=CLOUD_URL, model="x") if isinstance(t, str)]
    assert "".join(out) == "Hello"
    assert seen["json"]["stream_options"] == {"include_usage": True}
    rec = _ledger(env)
    assert rec["calls"] == 1 and rec["prompt_tokens"] == 200 and "estimated_calls" not in rec


def test_stream_without_usage_is_charged_an_estimate(env, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(
        lines=[_sse({"choices": [{"delta": {"content": "x" * 400}}]}), "data: [DONE]"]))
    list(codec_llm.stream(MSGS, base_url=CLOUD_URL, model="x"))
    rec = _ledger(env)
    assert rec["estimated_calls"] == 1 and rec["completion_tokens"] == 100 and rec["usd"] > 0


def test_astream_charges_the_usage_chunk(env):
    class _CM:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def aiter_lines(self):
            for ln in (_sse({"choices": [{"delta": {"content": "Hi"}}]}),
                       _sse({"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 1}}),
                       "data: [DONE]"):
                yield ln

    class _Client:
        def stream(self, method, url, json=None, headers=None):
            assert json["model"] == "cloud-pro" and headers["Authorization"] == "Bearer sk-test"
            return _CM()

    async def run():
        return [t async for t in codec_llm.astream(MSGS, base_url=CLOUD_URL, model="x", http=_Client())]

    assert asyncio.run(run()) == ["Hi"]
    assert _ledger(env)["prompt_tokens"] == 50


@pytest.mark.parametrize("problem, expect", [
    ("cap", "monthly spend cap reached"),
    ("key", "no API key"),
    ("prices", "no price set"),
])
def test_blocked_call_never_leaves_the_mac(env, monkeypatch, problem, expect):
    if problem == "cap":
        env[1].write_text(json.dumps({time.strftime("%Y-%m"): {"cloud-pro": {"usd": 10.5}}}))
    elif problem == "key":
        monkeypatch.setattr(codec_cloud_models, "get_key", lambda e: "")
    else:
        c = _cfg(env)
        del c["extra_models"][0]["price_in_per_m"]
        env[0].write_text(json.dumps(c))
    import requests
    monkeypatch.setattr(requests, "post", _no_http)
    assert expect in codec_llm.call(MSGS, base_url=CLOUD_URL, model="x")
    assert expect in next(iter(codec_llm.stream(MSGS, base_url=CLOUD_URL, model="x")))
    with pytest.raises(codec_llm.LLMError, match=expect):
        codec_llm.call(MSGS, base_url=CLOUD_URL, model="x", raise_on_error=True)


# ── Voice and agents ─────────────────────────────────────────────────────────

def test_voice_follows_the_switch_only_to_a_cloud_model(env, monkeypatch):
    import codec_voice
    monkeypatch.setattr(codec_voice, "_CONFIG_PATH", str(env[0]))
    base, model, cloud = codec_voice._resolve_voice_llm()
    assert (base, model, cloud) == (LOCAL_URL, codec_voice.VOICE_LLM_MODEL, False)
    c = _cfg(env)
    c.update(llm_base_url=CLOUD_URL, llm_model="cloud-pro",
             llm_local_restore={"llm_base_url": LOCAL_URL, "llm_model": "mlx-community/A"})
    env[0].write_text(json.dumps(c))
    assert codec_voice._resolve_voice_llm() == (CLOUD_URL, "cloud-pro", True)


def test_voice_on_a_cloud_model_gates_the_observer_summary(env, monkeypatch):
    import codec_memory
    import codec_observer
    import codec_voice
    seen = []
    monkeypatch.setattr(codec_observer, "maybe_inject_observation_summary",
                        lambda **kw: seen.append(kw["transport"]) or (None, "skipped"))

    class _Mem:
        def get_context(self, *a, **k):
            return ""

    monkeypatch.setattr(codec_memory, "CodecMemory", _Mem)
    monkeypatch.setattr(codec_voice, "VISION_PROVIDER", "local")
    p = object.__new__(codec_voice.VoicePipeline)
    p.mode, p.messages, p._warmed_up, p._stream_error = "default", [{"role": "system", "content": "s"}], True, False

    async def fake_stream(msgs, max_tokens=2000):
        yield "ok"

    p._stream_qwen = fake_stream

    async def run(base):
        p._llm_base = base
        return [c async for c in p.generate_response("what is on my screen")]

    asyncio.run(run(CLOUD_URL))
    asyncio.run(run(LOCAL_URL))
    assert seen == ["voice", "local"]


def test_project_agents_stay_on_the_local_model(env, monkeypatch):
    import codec_agent_plan
    import codec_agent_runner
    c = _cfg(env)
    c.update(llm_base_url=CLOUD_URL, llm_model="cloud-pro",
             llm_local_restore={"llm_base_url": LOCAL_URL, "llm_model": "mlx-community/A"})
    env[0].write_text(json.dumps(c))
    sent = []
    monkeypatch.setattr(codec_llm, "call", lambda msgs, **kw: sent.append(kw) or "{}")
    for mod in (codec_agent_plan, codec_agent_runner):
        monkeypatch.setattr(mod, "_qwen_base", lambda: CLOUD_URL)
        monkeypatch.setattr(mod, "_qwen_model", lambda: "cloud-pro")
        mod._qwen_chat("plan this")
    assert [(k["base_url"], k["model"]) for k in sent] == [(LOCAL_URL, "mlx-community/A")] * 2
