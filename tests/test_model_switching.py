"""Runtime model switching (codec_models + model_switch skill).

The property that matters most: a switch that fails must leave CODEC with a
working model. The mlx server holds ONE model at a time, so a failed load has
already unloaded the previous one — without the revert the box goes mute.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import codec_models


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm_model": "mlx-community/A", "llm_base_url": "http://x/v1"}))
    monkeypatch.setattr(codec_models, "CONFIG_PATH", str(path))
    monkeypatch.setattr(codec_models, "discover_local",
                        lambda: [{"id": "mlx-community/A", "label": "A", "size_gb": 1.0},
                                 {"id": "mlx-community/B", "label": "B", "size_gb": 2.0}])
    return path


def test_get_active_reads_config(cfg):
    assert codec_models.get_active() == "mlx-community/A"


def test_switch_persists_when_probe_succeeds(cfg, monkeypatch):
    monkeypatch.setattr(codec_models, "probe", lambda m, **kw: (True, "loaded in 1s"))
    r = codec_models.set_active("mlx-community/B")
    assert r["ok"] and r["active"] == "mlx-community/B"
    assert json.loads(cfg.read_text())["llm_model"] == "mlx-community/B"


def test_failed_switch_reverts_to_previous(cfg, monkeypatch):
    """The core safety property — a bad model must not leave CODEC mute."""
    seen = []

    def fake_probe(model, **kw):
        seen.append(model)
        return (False, "HTTP 500: Unrecognized image processor") if model == "mlx-community/B" else (True, "reloaded")

    monkeypatch.setattr(codec_models, "probe", fake_probe)
    r = codec_models.set_active("mlx-community/B")

    assert r["ok"] is False
    assert r["active"] == "mlx-community/A", "must fall back to the working model"
    assert r["reverted"] is True
    assert json.loads(cfg.read_text())["llm_model"] == "mlx-community/A", \
        "config must be restored on disk, not just in the response"
    assert seen == ["mlx-community/B", "mlx-community/A"], \
        "the previous model must be re-probed so it is loaded again"


def test_unknown_model_is_refused_without_touching_config(cfg, monkeypatch):
    monkeypatch.setattr(codec_models, "probe",
                        lambda m, **kw: pytest.fail("must not probe an unknown model"))
    r = codec_models.set_active("evil/not-a-model")
    assert r["ok"] is False
    assert json.loads(cfg.read_text())["llm_model"] == "mlx-community/A"


def test_switch_to_active_is_a_noop(cfg, monkeypatch):
    monkeypatch.setattr(codec_models, "probe",
                        lambda m, **kw: pytest.fail("must not reload the active model"))
    r = codec_models.set_active("mlx-community/A")
    assert r["ok"] and r["changed"] is False


def test_list_marks_active(cfg):
    d = codec_models.list_models()
    assert d["active"] == "mlx-community/A"
    assert [m["active"] for m in d["models"]] == [True, False]


def test_discovery_excludes_non_chat_and_stubs(tmp_path, monkeypatch):
    """Speech/vision/diffusion checkpoints and metadata-only stubs must not be
    offered as chat models — picking one would unload a working model."""
    cache = tmp_path / "hub"
    def make(name, size):
        snap = cache / f"models--{name.replace('/', '--')}" / "snapshots" / "rev1"
        snap.mkdir(parents=True)
        (snap / "model.safetensors").write_bytes(b"0" * size)
    make("mlx-community/Qwen9-Chat-4bit", 600 * 1024 * 1024)   # keep
    make("mlx-community/whisper-large-v3", 600 * 1024 * 1024)  # non-chat
    make("black-forest-labs/FLUX.1-dev", 600 * 1024 * 1024)    # not mlx-community
    make("mlx-community/Qwen9-Stub-4bit", 1024)                # metadata stub
    monkeypatch.setattr(codec_models, "HF_CACHE", str(cache))
    ids = [m["id"] for m in codec_models.discover_local()]
    assert ids == ["mlx-community/Qwen9-Chat-4bit"], ids


# ── skill ────────────────────────────────────────────────────────────────────


def test_skill_query_never_switches(monkeypatch):
    import skills.model_switch as ms
    monkeypatch.setattr(ms.codec_models, "list_models", lambda: {
        "active": "mlx-community/Qwen3.6-35B-A3B-4bit",
        "models": [{"id": "mlx-community/Qwen3.6-35B-A3B-4bit", "label": "Qwen3.6", "active": True, "size_gb": 20.4, "role": ""},
                   {"id": "mlx-community/Qwen3.8-27B-4bit", "label": "Qwen3.8", "active": False, "size_gb": 16.1, "role": ""}]})
    monkeypatch.setattr(ms.codec_models, "set_active",
                        lambda *a, **kw: pytest.fail("a question must not switch the model"))
    out = ms.run("which model are you using")
    assert "Qwen3.6" in out


def test_skill_resolves_aliases_and_prefers_active():
    import skills.model_switch as ms
    models = [{"id": "mlx-community/Qwen3.5-35B-A3B-4bit"},
              {"id": "mlx-community/Qwen3.6-35B-A3B-4bit"},
              {"id": "mlx-community/Qwen3.8-27B-4bit"}]
    active = "mlx-community/Qwen3.6-35B-A3B-4bit"
    assert ms._resolve("coding", models, active) == "mlx-community/Qwen3.8-27B-4bit"
    # 'fast' matches every A3B model — the active one must win over the older 3.5
    assert ms._resolve("fast", models, active) == active
    assert ms._resolve("banana", models, active) is None
