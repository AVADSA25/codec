"""Settings with human labels, help text and proper controls (UI phase 2, P2.10; docs/P2.10-DESIGN.md).

Every field has a label, a help line and a proper control, and saves by itself.
PUT /api/config merges the nested blocks (shift_report, observer, daybreak,
ask_user, step_budget, image, ui_prefs) instead of flattening them. The Advanced
editor (/api/config/raw) merges and never deletes a key; masked secrets and
llm_local_restore stay as saved.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.config as rc

REPO = Path(__file__).resolve().parent.parent
DASH = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "agent_name": "C", "llm_provider": "mlx", "llm_api_key": "sk-test-abcdefghijklmnop",
        "dashboard_token": "tok-123456789", "llm_local_restore": {"model": "local-a"},
        "observer": {"stop_nouns": ["thing"], "cadence_active_s": 90},
        "shift_report": {"daily_at_hour": 17, "auto_save_path": None},
        "telegram": {"chat_id": "42"},
    }))
    monkeypatch.setattr(rc, "CONFIG_PATH", str(path))
    return path


@pytest.fixture
def client(cfg_path):
    app = FastAPI()
    app.include_router(rc.router)
    return TestClient(app)


def _saved(path):
    return json.loads(Path(path).read_text())


# ── PUT /api/config: nested blocks ─────────────────────────────────────────

def test_a_nested_block_is_merged_not_flattened(client, cfg_path):
    r = client.put("/api/config", json={"observer": {"ocr_enabled": False}})
    assert r.status_code == 200 and r.json()["updated_fields"] == ["observer.ocr_enabled"]
    saved = _saved(cfg_path)
    assert saved["observer"] == {"stop_nouns": ["thing"], "cadence_active_s": 90, "ocr_enabled": False}
    assert "ocr_enabled" not in saved, "never flattened to the top level"


def test_ui_prefs_lands_as_a_block(client, cfg_path):
    client.put("/api/config", json={"ui_prefs": {"chat_sidebar": False}})
    client.put("/api/config", json={"ui_prefs": {"voice_btn": True}})
    saved = _saved(cfg_path)
    assert saved["ui_prefs"] == {"chat_sidebar": False, "voice_btn": True}
    assert "chat_sidebar" not in saved


def test_old_grouped_sections_still_flatten(client, cfg_path):
    client.put("/api/config", json={"Identity": {"agent_name": "Nova"}, "Wake Word": {"wake_word_enabled": False}})
    saved = _saved(cfg_path)
    assert saved["agent_name"] == "Nova" and saved["wake_word_enabled"] is False
    assert saved["telegram"] == {"chat_id": "42"}, "other keys untouched"


@pytest.mark.parametrize("body", [
    {"observer": {"cadence_active_s": 10}},
    {"step_budget": {"chat": 7}},
    {"shift_report": {"daily_at_hour": 25}},
    {"ask_user": {"timeout_seconds": "600"}},
    {"image": {"max_count": True}},
    {"ui_prefs": {"chat_sidebar": "no"}},
    {"daybreak": "on"},
])
def test_bad_nested_values_are_refused(client, cfg_path, body):
    before = cfg_path.read_text()
    r = client.put("/api/config", json=body)
    assert r.status_code == 422
    assert cfg_path.read_text() == before


def test_tune_up_step_budget_values_are_accepted(client, cfg_path):
    assert client.put("/api/config", json={"step_budget": {"chat": 8, "voice": 10}}).status_code == 200
    assert _saved(cfg_path)["step_budget"] == {"chat": 8, "voice": 10}


def test_get_carries_the_blocks_with_defaults(client):
    d = client.get("/api/config").json()
    assert d["observer"]["cadence_active_s"] == 90 and d["observer"]["enabled"] is True
    assert "stop_nouns" not in d["observer"], "only the fields Settings edits"
    assert d["shift_report"]["daily_at_hour"] == 17 and d["shift_report"]["idle_minutes"] == 30
    assert d["step_budget"] == {"chat": 5, "voice": 5}
    assert d["ask_user"] == {"timeout_seconds": 600}
    assert d["image"]["steps"] == 20 and d["daybreak"]["include_email"] is True
    assert d["llm"]["llm_api_key"].startswith("*") and d["llm"]["llm_api_key"].endswith("mnop")


def test_nested_defaults_match_the_modules_that_read_them():
    import codec_image
    import codec_observer
    sys_path_skill = str(REPO / "skills")
    import sys
    if sys_path_skill not in sys.path:
        sys.path.insert(0, sys_path_skill)
    import shift_report
    for k in ("cadence_active_s", "cadence_idle_s", "ocr_enabled"):
        assert rc.NESTED_BLOCKS["observer"][k][2] == codec_observer._DEFAULT_CONFIG[k], k
    for k in ("enabled", "daily_at_hour", "daily_at_minute", "idle_minutes", "lookback_hours", "auto_save_path"):
        assert rc.NESTED_BLOCKS["shift_report"][k][2] == shift_report._DEFAULT_CONFIG[k], k
    for k in ("steps", "max_count", "min_free_gb"):
        assert rc.NESTED_BLOCKS["image"][k][2] == codec_image.DEFAULTS[k], k


# ── The Advanced editor ────────────────────────────────────────────────────

def test_raw_get_masks_secrets(client):
    d = client.get("/api/config/raw").json()
    assert d["config"]["llm_api_key"].startswith("****") and "sk-test" not in json.dumps(d)
    assert d["config"]["dashboard_token"].endswith("6789") and "tok-123" not in json.dumps(d)
    assert "llm_local_restore" in d["read_only"] and "llm_api_key" in d["read_only"]


def test_raw_put_merges_and_never_deletes(client, cfg_path):
    d = client.get("/api/config/raw").json()["config"]
    d["agent_name"] = "Nova"
    del d["telegram"]                               # removed in the editor
    d["llm_local_restore"] = {"model": "evil"}      # read-only
    r = client.put("/api/config/raw", json={"config": d})
    assert r.status_code == 200 and r.json()["updated_fields"] == ["agent_name"]
    saved = _saved(cfg_path)
    assert saved["agent_name"] == "Nova"
    assert saved["telegram"] == {"chat_id": "42"}, "a removed key is kept"
    assert saved["llm_local_restore"] == {"model": "local-a"}
    assert saved["llm_api_key"] == "sk-test-abcdefghijklmnop", "the masked secret is not written back"
    assert saved["dashboard_token"] == "tok-123456789"


def test_raw_put_validates_and_refuses_non_objects(client, cfg_path):
    before = cfg_path.read_text()
    assert client.put("/api/config/raw", json={"config": [1, 2]}).status_code == 400
    assert client.put("/api/config/raw", json={"config": {"observer": {"cadence_active_s": 5}}}).status_code == 422
    assert client.put("/api/config/raw", json={"config": {"tts_speed": 9}}).status_code == 422
    assert cfg_path.read_text() == before


# ── observer.enabled ───────────────────────────────────────────────────────

def test_settings_can_turn_the_observer_off_and_the_env_still_wins(tmp_path, monkeypatch):
    import codec_observer
    path = tmp_path / "config.json"
    monkeypatch.setattr(codec_observer, "_CODEC_CONFIG_PATH", path)
    monkeypatch.delenv("OBSERVER_ENABLED", raising=False)
    assert codec_observer._enabled() is True, "no file: on"
    path.write_text(json.dumps({"observer": {"enabled": False}}))
    assert codec_observer._enabled() is False
    path.write_text(json.dumps({"observer": {"enabled": True}}))
    monkeypatch.setenv("OBSERVER_ENABLED", "false")
    assert codec_observer._enabled() is False


# ── The page ───────────────────────────────────────────────────────────────

def _schema():
    return DASH[DASH.index("var SETTINGS_SCHEMA = ["):DASH.index("function _setId(")]


def test_every_field_the_api_returns_has_a_label_and_help(client):
    d = client.get("/api/config").json()
    schema = _schema()
    skip = {"tts_voice", "tts_speed"}  # the Voice section edits these (P2.8)
    for sec, vals in d.items():
        if sec == "ui_prefs":
            continue  # Page Customization has its own switches
        assert f"sec: '{sec}'" in schema, f"no Settings section for {sec}"
        for key in vals:
            if key in skip:
                continue
            m = re.search(r"\{ k: '" + re.escape(key) + r"', label: '([^']+)', help: '([^']+)'", schema[schema.index(f"sec: '{sec}'"):])
            assert m, f"{sec}.{key} has no label and help"
            assert m.group(1) != key and "_" not in m.group(1), f"{sec}.{key} shows a raw key"


def test_controls_save_by_themselves():
    assert "saveConfigBtn" not in DASH and "function saveConfig(" not in DASH, "no Save Changes button"
    ctl = DASH[DASH.index("function _setControl("):DASH.index("function renderSettings(")]
    assert ctl.count('onchange="saveSetting(this)"') == 5, "switch, select, range, number and text"
    for c in ("'switch'", "'select'", "'range'", "'number'"):
        assert c in ctl
    save = DASH[DASH.index("async function saveSetting(el)"):DASH.index("// ── Advanced: edit config.json")]
    assert "_setShow(el, f, before);" in save, "a failed save puts the old value back"
    assert "body[sec.put][f.k] = v;" in save and "fetch('/api/config', { method: 'PUT'" in save
    assert "Always-on wake word: ON" in save, "the wake-word switch keeps its messages"


def test_advanced_editor_and_hash_link():
    assert 'id="rawConfigSection"' in DASH and "fetch('/api/config/raw'" in DASH
    assert "JSON.parse(ta.value)" in DASH, "a syntax error is caught before sending"
    assert "a key you delete here is kept in the file" in DASH
    assert "window.addEventListener('hashchange', _tabFromHash);" in DASH and "\n_tabFromHash();" in DASH


def test_phone_fields_do_not_zoom():
    css = DASH[DASH.index("/* Settings fields (P2.10)"):]
    phone = css[css.index("@media (max-width: 767px)"):][:500]
    assert "font-size: max(16px, var(--fs-14))" in phone
