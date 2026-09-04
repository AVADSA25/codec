"""A buyer must be able to give CODEC a brain, and must never be told one works
when it does not.

The bug this pins: a fresh paid install downloaded Qwen2.5-7B and pointed the
chat handler at Qwen3.6-35B, which was never downloaded. Every first message
failed. The developer's machine had both models, so it looked fine everywhere
it was tested.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import codec_setup  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_side_effects(monkeypatch):
    """Never let the suite restart the operator's model server or touch the
    real Keychain: set_provider('local') calls codec_models.restart_server()."""
    import codec_models
    monkeypatch.setattr(codec_models, "restart_server", lambda *a, **k: (False, "test"))
    monkeypatch.setattr(codec_setup, "_store_key", lambda k: None)
    monkeypatch.setattr(codec_setup, "get_key", lambda: "")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{}")
    monkeypatch.setattr(codec_setup, "CONFIG_PATH", str(path))
    monkeypatch.setattr(codec_setup, "HF_CACHE", str(tmp_path / "hf"))
    monkeypatch.setattr(codec_setup, "MODELS_DIR", str(tmp_path / "models"))
    return path


def _read(path):
    return json.loads(Path(path).read_text())


# ── A fake OpenAI-compatible server, so the honesty tests face real HTTP ──────

class _Handler(BaseHTTPRequestHandler):
    mode = "ok"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if _Handler.mode == "unauthorized":
            self.send_response(401); self.end_headers()
            self.wfile.write(b'{"error":"invalid api key"}'); return
        if _Handler.mode == "notfound":
            self.send_response(404); self.end_headers()
            self.wfile.write(b'{"error":"model not found"}'); return
        if _Handler.mode == "notchat":
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(b'{"hello":"world"}'); return
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "READY"}}]}).encode())

    def log_message(self, *a):  # keep pytest output clean
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


# ── G1 ───────────────────────────────────────────────────────────────────────

def test_defaults_match_reality(capsys):
    """The model the installer downloads and the model CODEC defaults to must
    be the SAME model. They were not, which is the whole bug."""
    manifest = json.loads((REPO / "packaging/macos/models.json").read_text())
    bundled = [m["repo"] for m in manifest["models"]
               if m["kind"] == "llm" and m["tier"] == "bundled"]
    assert bundled, "no bundled LLM in models.json — a fresh install has no brain"
    assert codec_setup.BUNDLED_LOCAL_MODEL in bundled, (
        f"codec_setup defaults to {codec_setup.BUNDLED_LOCAL_MODEL} but the installer "
        f"downloads {bundled} — a fresh install would point at a model it never fetched"
    )

    # The original bug was NOT in codec_setup (which did not exist) — it was the
    # chat handler's hardcoded fallback. Pinning only the new constant would
    # "fix" the bug in a new module while the old default still misled every
    # config that omits llm_model. So assert the real fallbacks too.
    import codec_models
    assert codec_models.DEFAULT_MODEL in bundled, (
        f"codec_models.DEFAULT_MODEL is {codec_models.DEFAULT_MODEL}, which a fresh "
        f"install never downloads (it fetches {bundled})"
    )
    dash = (REPO / "codec_dashboard.py").read_text()
    import re
    for m in re.finditer(r'config\.get\("llm_model",\s*"([^"]+)"\)', dash):
        assert m.group(1) in bundled, (
            f"codec_dashboard falls back to {m.group(1)}, which is not a bundled model"
        )
    print("UNLAZY-G1-PASS")


# ── G2 ───────────────────────────────────────────────────────────────────────

def test_status_reflects_real_connectivity(cfg, server, monkeypatch, capsys):
    assert codec_setup.status()["connected"] is False, "empty config must not read as connected"

    codec_setup.set_provider("custom", base_url=server, model="test-model")
    assert codec_setup.status()["connected"] is False, \
        "choosing a provider must NOT count as connected — only a real reply does"

    _Handler.mode = "ok"
    assert codec_setup.verify()["ok"] is True
    assert codec_setup.status()["connected"] is True
    print("UNLAZY-G2-PASS")


# ── G3 ───────────────────────────────────────────────────────────────────────

def test_provider_roundtrip_all_three(cfg, capsys):
    r = codec_setup.set_provider("local", model=codec_setup.BUNDLED_LOCAL_MODEL)
    assert r["ok"] and _read(cfg)["llm_base_url"] == codec_setup.LOCAL_BASE_URL

    Path(cfg).write_text(json.dumps({"ava": {"license_key": "k", "proxy_url": "https://p.example"}}))
    r = codec_setup.set_provider("ava")
    assert r["ok"] and _read(cfg)["llm_base_url"] == "https://p.example/v1"

    r = codec_setup.set_provider("custom", base_url="http://localhost:11434/v1", model="llama3")
    assert r["ok"] and _read(cfg)["llm_model"] == "llama3"

    assert codec_setup.set_provider("custom", base_url="")["ok"] is False, "custom needs a URL"
    assert codec_setup.set_provider("nonsense")["ok"] is False
    print("UNLAZY-G3-PASS")


# ── G4: the honesty gate, with a known-bad control ───────────────────────────

def test_test_call_is_honest(cfg, server, monkeypatch, capsys):
    codec_setup.set_provider("custom", base_url=server, model="m")

    _Handler.mode = "unauthorized"
    r = codec_setup.verify()
    assert r["ok"] is False and "key" in r["detail"].lower(), r

    _Handler.mode = "notfound"
    r = codec_setup.verify()
    assert r["ok"] is False and "404" in r["detail"], r

    _Handler.mode = "notchat"
    r = codec_setup.verify()
    assert r["ok"] is False and "format" in r["detail"].lower(), r

    codec_setup.set_provider("custom", base_url="http://127.0.0.1:1/v1", model="m")
    r = codec_setup.verify(timeout=3)
    assert r["ok"] is False and "reach" in r["detail"].lower(), r

    # A local model that was never downloaded — the original failure exactly.
    codec_setup.set_provider("local", model="mlx-community/Never-Downloaded")
    r = codec_setup.verify()
    assert r["ok"] is False and "not downloaded" in r["detail"].lower(), r

    # And a real success still reads as success.
    _Handler.mode = "ok"
    codec_setup.set_provider("custom", base_url=server, model="m")
    assert codec_setup.verify()["ok"] is True
    print("UNLAZY-G4-PASS")


# ── G5 ───────────────────────────────────────────────────────────────────────

def test_key_never_on_disk(cfg, monkeypatch, capsys):
    stored = {}
    monkeypatch.setattr(codec_setup, "_store_key", lambda k: stored.update(key=k))
    codec_setup.set_provider("custom", base_url="https://api.example/v1",
                             model="m", api_key="sk-super-secret-value")
    raw = Path(cfg).read_text()
    assert "sk-super-secret-value" not in raw, "API KEY LEAKED INTO config.json"
    assert "llm_api_key" not in _read(cfg), "llm_api_key must not be written to disk"
    assert stored.get("key") == "sk-super-secret-value", "key never reached the Keychain"

    # The slot must be the one the CHAT path reads, or verify() passes while
    # every real message sends no key. That happened.
    assert codec_setup.KEY_SLOT == "llm_api_key", \
        f"setup stores keys under {codec_setup.KEY_SLOT!r} but chat reads 'llm_api_key'"

    # AVA mode must put the licence key in that same slot.
    Path(cfg).write_text(json.dumps({"ava": {"license_key": "lic-123", "proxy_url": "https://p.example"}}))
    codec_setup.set_provider("ava")
    assert stored.get("key") == "lic-123", "AVA licence key never reached the chat's key slot"

    # And a local choice must clear it, so no cloud bearer is sent to the local server.
    codec_setup.set_provider("local", model="x")
    assert stored.get("key") == "", "stale cloud key left in place for the local server"
    print("UNLAZY-G5-PASS")
