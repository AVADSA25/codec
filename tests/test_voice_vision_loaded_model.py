"""Voice and vision name the model the server holds (P-013).

docs/VOICE-VISION-LOADED-MODEL-DESIGN.md. A fake model server on 127.0.0.1
(GET /health, POST /v1/chat/completions) stands in for mlx_vlm: no real model
server is asked and no model is loaded. It swaps its model like the real one
does when a request names another, and counts the swaps.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import codec_audit  # noqa: E402
import codec_cloud_models  # noqa: E402
import codec_models  # noqa: E402

SEVEN_B = "mlx-community/Qwen2.5-7B-Instruct-4bit"
BIG = "mlx-community/Qwen3.6-35B-A3B-4bit"
VL = "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"


class _FakeServer:
    """GET /health reports `loaded`; a POST naming another model swaps it."""

    def __init__(self, loaded):
        self.loaded, self.posts, self.swaps, self.health_gets = loaded, [], 0, 0
        fake = self

        class _H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                data = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/health":
                    fake.health_gets += 1
                    self._send(200, json.dumps({"status": "healthy", "loaded_model": fake.loaded}))
                else:
                    self._send(404, "{}")

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path != "/v1/chat/completions":
                    return self._send(404, "{}")
                fake.posts.append(body)
                if body.get("model") != fake.loaded:
                    fake.swaps += 1          # "New text_generation model requested"
                    fake.loaded = body.get("model")
                if body.get("stream"):
                    chunk = json.dumps({"choices": [{"delta": {"content": "hello"}}]})
                    return self._send(200, f"data: {chunk}\n\ndata: [DONE]\n\n", "text/event-stream")
                self._send(200, json.dumps({"choices": [{"message": {"content": "a screen"}}]}))

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = _FakeServer(SEVEN_B)
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path, monkeypatch, server):
    """A config file every module reads, pointing at the fake server."""
    path = tmp_path / "config.json"

    def write(**values):
        path.write_text(json.dumps({"llm_base_url": server.base, **values}))
        return json.loads(path.read_text())

    for mod in (codec_models, codec_cloud_models):
        monkeypatch.setattr(mod, "CONFIG_PATH", str(path))
    import codec_voice
    monkeypatch.setattr(codec_voice, "_CONFIG_PATH", str(path))
    write.path = path
    return write


@pytest.fixture
def audit_log(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", log)

    def rows(event="model_swap"):
        if not log.exists():
            return []
        return [r for r in (json.loads(x) for x in log.read_text().splitlines() if x.strip())
                if r["event"] == event]
    return rows


# ── Voice names the loaded model ─────────────────────────────────────────────

def test_voice_names_the_config_model_byte_for_byte(cfg, server):
    import codec_voice
    cfg(llm_model=SEVEN_B)
    base, model, cloud = codec_voice._resolve_voice_llm()
    assert (base, model, cloud) == (server.base, SEVEN_B, False)
    server.loaded = "/Users/someone/.cache/huggingface/hub/models--x/snapshots/abc"
    assert codec_voice._resolve_voice_llm()[1] == SEVEN_B, "never a /health value"
    assert server.health_gets == 0, "choosing the model asks nothing of the server"


def test_a_pinned_voice_model_is_kept(cfg):
    import codec_voice
    cfg(llm_model=SEVEN_B, voice_model=BIG)
    assert codec_voice._resolve_voice_llm()[1] == BIG


def test_while_chat_is_on_the_cloud_the_local_model_is_the_one_held(cfg):
    c = cfg(llm_model="cloud-pro", llm_base_url="https://cloud.example/v1",
            llm_local_restore={"llm_base_url": "http://localhost:8083/v1", "llm_model": BIG},
            extra_models=[{"id": "cloud-pro", "base_url": "https://cloud.example/v1"}])
    assert codec_models.local_server_model(c) == ("http://localhost:8083/v1", BIG)


def test_one_voice_turn_on_the_bundled_7b_uses_it(cfg, server, audit_log):
    import httpx

    import codec_voice
    cfg(llm_model=SEVEN_B)

    async def turn():
        with patch("codec_voice.VoicePipeline._load_skills"), \
                patch("codec_voice._build_system_prompt", return_value="test"):
            p = codec_voice.VoicePipeline(_Ws())
        p._http = httpx.AsyncClient(timeout=10)
        try:
            await asyncio.to_thread(codec_voice._warm_voice_llm, p._llm_model, p._llm_base, False, "c1")
            return "".join([t async for t in p._stream_qwen([{"role": "user", "content": "hi"}], 8)])
        finally:
            await p._http.aclose()

    assert asyncio.run(turn()) == "hello"
    assert [b["model"] for b in server.posts] == [SEVEN_B, SEVEN_B], "warm-up and the turn"
    assert server.swaps == 0 and audit_log() == []


class _Ws:
    async def send_json(self, *_a, **_k):
        pass

    async def send_bytes(self, *_a, **_k):
        pass


# ── The swap line ────────────────────────────────────────────────────────────

def test_a_pinned_model_that_is_not_loaded_writes_one_model_swap_line(cfg, server, audit_log):
    # `cfg`: probe() reads config (a cloud entry check); keep it off the owner's file.
    import codec_voice
    codec_voice._warm_voice_llm(BIG, server.base, False, "abc123")
    rows = audit_log()
    assert len(rows) == 1 and server.swaps == 1
    r = rows[0]
    assert (r["source"], r["outcome"], r["level"]) == ("codec-models", "warning", "warning")
    assert r["extra"]["caller"] == "voice" and r["extra"]["correlation_id"] == "abc123"
    assert (r["extra"]["requested"], r["extra"]["loaded"]) == (BIG, SEVEN_B)
    assert server.posts[-1]["model"] == BIG, "the id sent is the configured string"
    codec_voice._warm_voice_llm(BIG, server.base, False)
    assert len(audit_log()) == 1, "nothing more once the server holds it"


def test_while_an_image_job_runs_the_swap_is_noted_but_nothing_loads(server, audit_log):
    import codec_voice
    codec_voice._warm_voice_llm(BIG, server.base, True)
    assert len(audit_log()) == 1 and server.posts == [] and server.loaded == SEVEN_B


@pytest.mark.parametrize("reported", [
    "/Users/someone/.cache/huggingface/hub/models--a--b/snapshots/0123abcd",
    "mlx-community/../../etc", "http://x/y", "a" * 150 + "/" + "b" * 60, 7, None, "",
])
def test_health_value_must_be_a_plain_hub_id(server, audit_log, reported):
    server.loaded = reported
    assert codec_models.health_model(server.base) is None
    assert codec_models.note_swap(BIG, server.base, caller="voice") is False
    assert audit_log() == []


def test_health_is_read_only_on_this_mac(monkeypatch):
    def no_request(*a, **k):
        raise AssertionError("a server off this Mac must not be asked")
    monkeypatch.setattr(codec_models.urllib.request, "urlopen", no_request)
    assert codec_models.health_model("https://cloud.example/v1") is None
    assert codec_models.note_swap(BIG, "https://cloud.example/v1") is False


def test_no_swap_line_when_the_server_does_not_answer(audit_log):
    assert codec_models.note_swap(BIG, "http://127.0.0.1:9/v1", caller="voice") is False
    assert audit_log() == []


# ── Vision ───────────────────────────────────────────────────────────────────

def test_same_server():
    assert codec_models.same_server("http://localhost:8083/v1", "http://127.0.0.1:8083")
    assert not codec_models.same_server("http://localhost:8083/v1", "http://localhost:8090/v1")
    assert not codec_models.same_server("http://10.0.0.2:8083/v1", "http://localhost:8083/v1")
    assert not codec_models.same_server("", "http://localhost:8083/v1")


def test_vision_rule(server):
    base = server.base
    on_7b = {"llm_base_url": base, "llm_model": SEVEN_B}
    assert codec_models.vision_model(on_7b, base, BIG) == SEVEN_B, "no vision_model: the loaded one"
    assert codec_models.vision_model(dict(on_7b, vision_model=VL), base, BIG) == VL, "pinned is kept"
    assert codec_models.vision_model(on_7b, "http://localhost:8099/v1", VL) == VL, "own server"


def test_one_vision_request_on_the_bundled_7b_uses_it(cfg, server, monkeypatch):
    import codec_config
    import codec_vision
    monkeypatch.setattr(codec_config, "cfg", cfg(llm_model=SEVEN_B))
    monkeypatch.setattr(codec_config, "QWEN_VISION_URL", server.base)
    monkeypatch.setattr(codec_config, "QWEN_VISION_MODEL", BIG)   # the old default
    assert codec_vision.describe_sync("b64", "what is on my screen?") == "a screen"
    assert [b["model"] for b in server.posts] == [SEVEN_B] and server.swaps == 0


# ── The dashboard's vision warm-up ───────────────────────────────────────────

def _warm(monkeypatch, path):
    import requests

    import codec_dashboard as dash
    sent = []
    monkeypatch.setattr(dash, "_WARMUP_DELAY_S", 0)
    monkeypatch.setattr(dash, "CONFIG_PATH", str(path))
    monkeypatch.setattr(requests, "post", lambda url, json=None, **k: sent.append((url, json["model"]))
                        or type("R", (), {"status_code": 200})())
    asyncio.run(dash._warmup_vision())
    return sent


def test_warmup_sends_nothing_for_another_model_on_the_chat_server(cfg, server, monkeypatch):
    cfg(llm_model=BIG, vision_model=VL, vision_base_url=server.base)
    assert _warm(monkeypatch, cfg.path) == []
    other_spelling = server.base.replace("127.0.0.1", "localhost") + "/"
    cfg(llm_model=BIG, vision_model=VL, vision_base_url=other_spelling)
    assert _warm(monkeypatch, cfg.path) == [], "the same server written another way"


def test_warmup_still_warms_the_chat_model_and_a_separate_vision_server(cfg, server, monkeypatch):
    cfg(llm_model=BIG, vision_model=BIG, vision_base_url=server.base)
    assert _warm(monkeypatch, cfg.path) == [(server.base + "/chat/completions", BIG)], "control"
    cfg(llm_model=BIG, vision_base_url="http://localhost:8099/v1")
    assert _warm(monkeypatch, cfg.path) == [("http://localhost:8099/v1/chat/completions", VL)]
