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


@pytest.fixture(autouse=True)
def no_real_pm2(monkeypatch):
    """Never let the suite restart the operator's actual model server.

    `restart_server()` shells out to a real `pm2`, which exists on any machine
    that runs CODEC — so an unmocked test would take the brain offline mid-run.
    Hiding pm2 makes restart_server return its clean "switching in place"
    failure. Tests that exercise the restart override this themselves.
    """
    monkeypatch.setattr(codec_models.shutil, "which", lambda _name: None)


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


# ── operator allowlist (models_visible) ──────────────────────────────────────
# Discovery finds every loadable model on disk, including ones the operator does
# not want offered. Hiding must not require deleting weights — screenshot_text.py
# hardcodes the VL checkpoint, so deleting it would break screenshot OCR.


def _cfg_with(tmp_path, monkeypatch, **extra):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm_model": "mlx-community/A", **extra}))
    monkeypatch.setattr(codec_models, "CONFIG_PATH", str(path))
    monkeypatch.setattr(codec_models, "discover_local",
                        lambda: [{"id": "mlx-community/A", "label": "A", "size_gb": 1.0},
                                 {"id": "mlx-community/B", "label": "B", "size_gb": 2.0},
                                 {"id": "mlx-community/HIDE", "label": "H", "size_gb": 3.0}])
    return path


def test_allowlist_hides_models_from_picker(tmp_path, monkeypatch):
    _cfg_with(tmp_path, monkeypatch,
              models_visible=["mlx-community/A", "mlx-community/B"])
    ids = [m["id"] for m in codec_models.list_models()["models"]]
    assert ids == ["mlx-community/A", "mlx-community/B"]


def test_hidden_model_cannot_be_switched_to(tmp_path, monkeypatch):
    cfg = _cfg_with(tmp_path, monkeypatch,
                    models_visible=["mlx-community/A", "mlx-community/B"])
    monkeypatch.setattr(codec_models, "probe",
                        lambda m, **kw: pytest.fail("hidden model must not be probed"))
    r = codec_models.set_active("mlx-community/HIDE")
    assert r["ok"] is False
    assert json.loads(cfg.read_text())["llm_model"] == "mlx-community/A"


def test_active_model_is_shown_even_if_not_in_allowlist(tmp_path, monkeypatch):
    """Hiding what CODEC is currently running would be a lie."""
    _cfg_with(tmp_path, monkeypatch, models_visible=["mlx-community/B"])
    d = codec_models.list_models()
    ids = [m["id"] for m in d["models"]]
    assert d["active"] == "mlx-community/A"
    assert "mlx-community/A" in ids


def test_no_allowlist_shows_everything(tmp_path, monkeypatch):
    _cfg_with(tmp_path, monkeypatch)
    assert len(codec_models.list_models()["models"]) == 3


# ── Restart-on-switch (memory reclaim) ────────────────────────────────────────
# A switch restarts the PM2 model process rather than swapping in-place, because
# in-place swapping does not give the memory back (52 GB measured against a
# 19 GB model). The restart must never be load-bearing: where PM2 is missing the
# switch still has to happen, just without the reclaim.

def test_switch_restarts_the_server_after_writing_config(cfg, monkeypatch):
    """Order matters: the launcher reads llm_model at startup, so the config
    must already name the NEW model when the process comes back."""
    events = []

    def fake_restart(*a, **kw):
        events.append(("restart", json.loads(cfg.read_text())["llm_model"]))
        return True, "restarted"

    monkeypatch.setattr(codec_models, "restart_server", fake_restart)
    monkeypatch.setattr(codec_models, "probe",
                        lambda m, **kw: (events.append(("probe", m)), (True, "loaded"))[1])

    r = codec_models.set_active("mlx-community/B")

    assert r["ok"] and r["restarted"] is True
    assert events == [("restart", "mlx-community/B"), ("probe", "mlx-community/B")], \
        "config must be written before the restart, and probed after it"


def test_switch_survives_a_failed_restart(cfg, monkeypatch):
    """No pm2 (tests, CI, a hand-started server) must degrade to the old
    in-place swap, not fail the switch."""
    monkeypatch.setattr(codec_models, "restart_server",
                        lambda *a, **kw: (False, "pm2 not on PATH — switching in place"))
    monkeypatch.setattr(codec_models, "probe", lambda m, **kw: (True, "loaded in 30s"))

    r = codec_models.set_active("mlx-community/B")

    assert r["ok"] and r["active"] == "mlx-community/B"
    assert r["restarted"] is False
    assert "pm2" in r["restart_detail"]
    assert json.loads(cfg.read_text())["llm_model"] == "mlx-community/B"


def test_failed_switch_restarts_again_before_reverting(cfg, monkeypatch):
    """The process that just failed to load a model is not the one to keep
    serving from — the revert restarts too, with the old model back in config."""
    seen = []
    monkeypatch.setattr(codec_models, "restart_server",
                        lambda *a, **kw: (seen.append(("restart", json.loads(cfg.read_text())["llm_model"])),
                                          (True, "restarted"))[1])
    monkeypatch.setattr(
        codec_models, "probe",
        lambda m, **kw: (seen.append(("probe", m)),
                         (False, "HTTP 500") if m == "mlx-community/B" else (True, "reloaded"))[1])

    r = codec_models.set_active("mlx-community/B")

    assert r["ok"] is False and r["active"] == "mlx-community/A"
    assert seen == [("restart", "mlx-community/B"), ("probe", "mlx-community/B"),
                    ("restart", "mlx-community/A"), ("probe", "mlx-community/A")]
    assert json.loads(cfg.read_text())["llm_model"] == "mlx-community/A"


def test_unverified_switch_does_not_restart(cfg, monkeypatch):
    """verify=False is the caller asking not to wait, and the restart is the
    longest part of the wait."""
    monkeypatch.setattr(codec_models, "restart_server",
                        lambda *a, **kw: pytest.fail("must not restart when unverified"))
    monkeypatch.setattr(codec_models, "probe",
                        lambda m, **kw: pytest.fail("must not probe when unverified"))
    r = codec_models.set_active("mlx-community/B", verify=False)
    assert r["ok"] and json.loads(cfg.read_text())["llm_model"] == "mlx-community/B"


def test_restart_server_without_pm2_is_a_clean_failure(cfg, monkeypatch):
    monkeypatch.setattr(codec_models.shutil, "which", lambda _: None)
    ok, detail = codec_models.restart_server()
    assert ok is False and "pm2" in detail


def test_restart_server_waits_for_a_new_pid_then_the_port(cfg, monkeypatch):
    """The port lies for a moment after `pm2 restart` — the OLD process still
    holds it. Returning on that first connect would report ready before the new
    process exists."""
    monkeypatch.setattr(codec_models.shutil, "which", lambda _: "/usr/bin/pm2")
    monkeypatch.setattr(codec_models.subprocess, "run",
                        lambda *a, **kw: __import__("types").SimpleNamespace(
                            returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(codec_models.time, "sleep", lambda _s: None)

    pids = iter([111, 111, 222, 222, 222])
    monkeypatch.setattr(codec_models, "_pm2_pid", lambda name: next(pids, 222))
    listens = iter([False, False, True])
    monkeypatch.setattr(codec_models, "_is_listening",
                        lambda h, p, **kw: next(listens, True))

    ok, detail = codec_models.restart_server()
    assert ok is True and "serving in" in detail


def test_restart_server_reports_a_port_that_never_opens(cfg, monkeypatch):
    monkeypatch.setattr(codec_models.shutil, "which", lambda _: "/usr/bin/pm2")
    monkeypatch.setattr(codec_models.subprocess, "run",
                        lambda *a, **kw: __import__("types").SimpleNamespace(
                            returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(codec_models.time, "sleep", lambda _s: None)
    pids = iter([111, 222])
    monkeypatch.setattr(codec_models, "_pm2_pid", lambda name: next(pids, 222))
    monkeypatch.setattr(codec_models, "_is_listening", lambda h, p, **kw: False)

    ok, detail = codec_models.restart_server(ready_timeout=0.01)
    assert ok is False and "never opened" in detail


def test_restart_server_reports_a_nonzero_pm2_exit(cfg, monkeypatch):
    monkeypatch.setattr(codec_models.shutil, "which", lambda _: "/usr/bin/pm2")
    monkeypatch.setattr(codec_models.subprocess, "run",
                        lambda *a, **kw: __import__("types").SimpleNamespace(
                            returncode=1, stdout="", stderr="Process not found"))
    ok, detail = codec_models.restart_server()
    assert ok is False and "Process not found" in detail


def test_restart_targets_the_configured_process_and_port(tmp_path, monkeypatch):
    """A second machine renames the service in config.json, not in code."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"model_pm2_process": "qwen-other",
                                "llm_base_url": "http://127.0.0.1:9999/v1"}))
    monkeypatch.setattr(codec_models, "CONFIG_PATH", str(path))
    assert codec_models._pm2_process_name() == "qwen-other"
    assert codec_models._host_port(codec_models._load_config()) == ("127.0.0.1", 9999)


def test_restart_process_name_defaults(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{}")
    monkeypatch.setattr(codec_models, "CONFIG_PATH", str(path))
    assert codec_models._pm2_process_name() == codec_models.DEFAULT_PM2_PROCESS
    assert codec_models._host_port({}) == ("localhost", 8083)
