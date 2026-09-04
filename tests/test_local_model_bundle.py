"""A buyer can get a local model without a terminal, and the app can serve it.

Three things made "Use the local model" impossible on a fresh Mac: the bundled
Python had no mlx_vlm, the model-server script was not in the bundle and named
the developer's venv, and first_run ran fetch_models as a DRY RUN so the
"bundled" 4.3 GB LLM never arrived. The first two are verified by the Node
gates against the built app; this file covers the download and discovery path.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import codec_setup  # noqa: E402


@pytest.fixture
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(codec_setup, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(codec_setup, "HF_CACHE", str(tmp_path / "hf"))
    monkeypatch.setattr(codec_setup, "MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(codec_setup, "_store_key", lambda k: None)
    import codec_models
    monkeypatch.setattr(codec_models, "restart_server", lambda *a, **k: (False, "test"))
    # reset module download state between tests
    with codec_setup._DL_LOCK:
        codec_setup._DL.update(state="idle", bytes=0, error="", dest="", started=0.0)
    (tmp_path / "config.json").write_text("{}")
    return tmp_path


def _fake_weights(dest: str, mb: int = 600):
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, "model.safetensors"), "wb") as f:
        f.seek(mb * 1024 * 1024 - 1); f.write(b"\0")


def _wait(pred, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.05)
    return False


# ── G4 ───────────────────────────────────────────────────────────────────────

def test_download_progress_duplicates_and_failure(iso, capsys):
    gate = threading.Event()
    def slow_fetch(repo, dest):
        _fake_weights(dest, mb=550)
        gate.wait(5)   # hold "running" so we can observe it
    r = codec_setup.start_download(fetch=slow_fetch)
    assert r["ok"] and r["dest"].endswith("Qwen2.5-7B-Instruct-4bit")

    assert _wait(lambda: codec_setup.download_status()["bytes"] > 0), "progress never reported"
    st = codec_setup.download_status()
    assert st["state"] == "running" and 0 < st["percent"] < 100, st

    dup = codec_setup.start_download(fetch=slow_fetch)
    assert dup["ok"] is False and "already running" in dup["error"]

    gate.set()
    assert _wait(lambda: codec_setup.download_status()["state"] == "done"), codec_setup.download_status()
    assert codec_setup.download_status()["percent"] == 100

    again = codec_setup.start_download(fetch=slow_fetch)
    assert again["ok"] is False and "already downloaded" in again["error"], "re-download of present weights"

    # A failing fetch must report, not hang.
    with codec_setup._DL_LOCK:
        codec_setup._DL.update(state="idle")
    import shutil; shutil.rmtree(codec_setup._bundled_dest())
    def bad_fetch(repo, dest): raise RuntimeError("network down")
    assert codec_setup.start_download(fetch=bad_fetch)["ok"]
    assert _wait(lambda: codec_setup.download_status()["state"] == "error")
    assert "network down" in codec_setup.download_status()["error"]

    # And a fetch that "succeeds" without writing weights is a failure too.
    with codec_setup._DL_LOCK:
        codec_setup._DL.update(state="idle")
    def empty_fetch(repo, dest): pass
    assert codec_setup.start_download(fetch=empty_fetch)["ok"]
    assert _wait(lambda: codec_setup.download_status()["state"] == "error")
    assert "no weights" in codec_setup.download_status()["error"]
    print("UNLAZY-G4-PASS")


# ── G5 ───────────────────────────────────────────────────────────────────────

def test_discovered_after_download_and_selectable(iso, capsys):
    assert codec_setup.discover_local_models() == [], "must start empty"
    assert codec_setup.start_download(fetch=lambda repo, dest: _fake_weights(dest))["ok"]
    assert _wait(lambda: codec_setup.download_status()["state"] == "done")

    found = codec_setup.discover_local_models()
    assert len(found) == 1 and found[0]["source"] == "codec_models_dir", found
    dest = found[0]["id"]

    r = codec_setup.set_provider("local", model=dest)
    assert r["ok"]
    cfg = json.loads(Path(codec_setup.CONFIG_PATH).read_text())
    assert cfg["llm_model"] == dest and cfg["llm_base_url"] == codec_setup.LOCAL_BASE_URL
    # And status offers it in the picker, un-verified until a real reply.
    st = codec_setup.status()
    assert any(m["id"] == dest for m in st["local_models"]) and st["connected"] is False
    print("UNLAZY-G5-PASS")
