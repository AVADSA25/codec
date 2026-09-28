"""Automatic cloud fallback (codec_models.ensure_llm_available /
maybe_switch_back, and their hooks in chat and the heartbeat).

The properties that matter: nothing happens without the opt-in; a closed
local port switches to the owner's cloud entry once, with one alert; a
blocked entry or a still-loading server never switches; the owner's pick
always wins; the way back needs two good local probes and restarts nothing.
docs/CLOUD-AUTO-FALLBACK-DESIGN.md
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import codec_alerts  # noqa: E402
import codec_cloud_models  # noqa: E402
import codec_models  # noqa: E402

CLOUD_URL = "https://cloud.example/v1"
LOCAL_URL = "http://localhost:8083/v1"
CLOUD = {"id": "cloud-pro", "label": "Cloud Pro (cloud)", "base_url": CLOUD_URL,
         "key_slot": "test_cloud_key", "price_in_per_m": 0.5, "price_out_per_m": 1.0,
         "monthly_cap_usd": 10}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated config, a closed or open local port, fake probes, captured alerts."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "llm_model": "mlx-community/A", "llm_base_url": LOCAL_URL,
        "extra_models": [dict(CLOUD)], "llm_auto_fallback": "cloud-pro"}))
    for mod in (codec_models, codec_cloud_models):
        monkeypatch.setattr(mod, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(codec_cloud_models, "SPEND_PATH", str(tmp_path / "spend.json"))
    monkeypatch.setattr(codec_cloud_models, "get_key", lambda e: "sk-test")
    monkeypatch.setattr(codec_models, "_SWITCH_LOCK_PATH", str(tmp_path / "switch.lock"))
    monkeypatch.setattr(codec_models.shutil, "which", lambda _n: None)  # never a real pm2
    monkeypatch.setattr(codec_models, "_emit_audit", lambda *a, **k: None)
    monkeypatch.setattr(codec_models, "discover_local",
                        lambda: [{"id": "mlx-community/A", "label": "A", "size_gb": 1.0}])
    state = {"listening": False, "local_ok": True, "alerts": [], "probes": []}
    monkeypatch.setattr(codec_models, "_is_listening", lambda h, p, timeout=1.5: state["listening"])

    def _probe(model_id, timeout=240.0, base_url=None):
        state["probes"].append(model_id)
        if model_id == "cloud-pro":
            return True, "cloud answered"
        return (True, "loaded") if state["local_ok"] else (False, "ConnectionRefusedError")
    monkeypatch.setattr(codec_models, "probe", _probe)
    monkeypatch.setattr(codec_models, "_fallback_alert",
                        lambda msg, resolved=False: state["alerts"].append((resolved, msg)))
    return cfg_path, state


def _cfg(env):
    return json.loads(env[0].read_text())


def test_without_opt_in_nothing_switches(env):
    cfg = _cfg(env)
    cfg.pop("llm_auto_fallback")
    env[0].write_text(json.dumps(cfg))
    assert codec_models.ensure_llm_available() is None
    assert _cfg(env)["llm_model"] == "mlx-community/A"


def test_closed_local_port_switches_once_with_one_alert(env):
    assert codec_models.ensure_llm_available() == "switched to Cloud Pro (cloud)"
    cfg = _cfg(env)
    assert (cfg["llm_model"], cfg["llm_base_url"]) == ("cloud-pro", CLOUD_URL)
    assert cfg["llm_auto_fallback_active"]["from"] == "mlx-community/A"
    assert cfg["llm_local_restore"] == {"llm_base_url": LOCAL_URL, "llm_model": "mlx-community/A"}
    assert codec_models.ensure_llm_available() is None      # already on the cloud
    assert [a for a in env[1]["alerts"] if not a[0]] == [env[1]["alerts"][0]]


def test_open_local_port_or_loading_server_does_not_switch(env, monkeypatch):
    env[1]["listening"] = True
    assert codec_models.ensure_llm_available() is None
    env[1]["listening"] = False
    monkeypatch.setattr(codec_models, "_server_loading", lambda cfg: True)
    assert codec_models.ensure_llm_available() is None
    assert _cfg(env)["llm_model"] == "mlx-community/A"


def test_blocked_cloud_entry_stays_local(env, monkeypatch):
    monkeypatch.setattr(codec_cloud_models, "block_message", lambda e: "monthly spend cap reached")
    assert codec_models.ensure_llm_available() is None
    assert _cfg(env)["llm_model"] == "mlx-community/A"
    assert env[1]["alerts"] == []


def test_back_after_two_good_local_probes_without_restart(env, monkeypatch):
    codec_models.ensure_llm_available()
    monkeypatch.setattr(codec_models, "restart_server",
                        lambda *a, **k: pytest.fail("switching back must not restart the server"))
    assert codec_models.maybe_switch_back() is None          # first good probe
    assert codec_models.maybe_switch_back() == "back on A"   # second
    cfg = _cfg(env)
    assert (cfg["llm_model"], cfg["llm_base_url"]) == ("mlx-community/A", LOCAL_URL)
    assert "llm_auto_fallback_active" not in cfg and "llm_local_restore" not in cfg
    assert env[1]["alerts"][-1][0] is True                   # the recovery alert


def test_a_failed_local_probe_restarts_the_count(env):
    codec_models.ensure_llm_available()
    codec_models.maybe_switch_back()                          # 1 good
    env[1]["local_ok"] = False
    codec_models.maybe_switch_back()                          # reset
    env[1]["local_ok"] = True
    assert codec_models.maybe_switch_back() is None           # 1 good again
    assert _cfg(env)["llm_model"] == "cloud-pro"


def test_the_owners_pick_wins(env):
    codec_models.ensure_llm_available()
    r = codec_models.set_active("mlx-community/A")            # back to local by hand
    assert r["ok"] and "llm_auto_fallback_active" not in _cfg(env)
    codec_models.set_active("cloud-pro")                       # cloud by hand: no flag
    assert codec_models.maybe_switch_back() is None
    assert _cfg(env)["llm_model"] == "cloud-pro"


def test_a_switch_in_progress_blocks_the_fallback(env):
    with codec_models._switch_lock() as held:
        assert held
        assert codec_models.ensure_llm_available() is None
    assert _cfg(env)["llm_model"] == "mlx-community/A"


def test_chat_checks_before_it_builds_the_prompt():
    import routes.chat
    src = inspect.getsource(routes.chat.chat_completion)
    assert src.index("ensure_llm_available") < src.index("_build_chat_system_prompt(")


def test_heartbeat_keeps_probing_the_local_model_on_an_automatic_fallback(monkeypatch):
    cfg = {"llm_base_url": CLOUD_URL, "llm_model": "cloud-pro",
           "llm_local_restore": {"llm_base_url": "http://localhost:8099/v1", "llm_model": "mlx-community/A"},
           "llm_auto_fallback_active": {"from": "mlx-community/A"}}
    probed = []
    monkeypatch.setattr(codec_alerts, "_load_config", lambda: cfg)
    monkeypatch.setattr(codec_alerts, "_load_state", lambda: {})
    monkeypatch.setattr(codec_alerts, "_save_state", lambda s: None)
    monkeypatch.setattr(codec_alerts, "send_alert", lambda *a, **k: None)
    monkeypatch.setattr(codec_alerts, "_try_restart", lambda name: True)
    monkeypatch.setattr(codec_alerts.time, "sleep", lambda s: None)
    monkeypatch.setattr(codec_alerts.subprocess, "check_output", lambda *a, **kw: b"")
    monkeypatch.setattr(codec_alerts, "_check_service", lambda url, timeout=5: probed.append(url) or True)
    codec_alerts.check_services_and_alert()
    assert "http://localhost:8099/v1/models" in probed
