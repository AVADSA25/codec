"""P-029: codec.py listens for the keys Settings saves, the defaults stay F13 /
F18 / F16, and a laptop preset is offered (docs/HOTKEYS-QUICK-ACTIONS-DESIGN.md).

Key presses are simulated with a stand-in key enum, so these run the same on a
Mac with pynput and on Linux CI with the conftest stub; the checks against the
real pynput keys run only where pynput imports.
"""
from __future__ import annotations

import enum
import json
import re
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codec_config
import codec_hotkeys
import routes.config as rc

REPO = Path(__file__).resolve().parent.parent
DASH = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")
DEFAULTS = {"key_toggle": "f13", "key_voice": "f18", "key_text": "f16"}
LAPTOP = codec_hotkeys.preset("laptop")


class _K(enum.Enum):
    """Stand-in for pynput's Key: members compare like pynput's do."""
    f6 = 6
    f8 = 8
    f9 = 9
    f13 = 13
    f16 = 16
    f18 = 18


FAKE_KB = types.SimpleNamespace(Key=_K)


def _real_pynput() -> bool:
    kb = codec_config.keyboard
    return kb is not None and isinstance(getattr(kb, "Key", None), enum.EnumMeta)


# ── Defaults ────────────────────────────────────────────────────────────────

def test_default_keys_stay_f13_f18_f16():
    assert codec_hotkeys.DEFAULTS == DEFAULTS
    assert codec_hotkeys.names({}) == DEFAULTS
    assert codec_hotkeys.preset("extended") == DEFAULTS
    example = json.loads((REPO / "config.json.example").read_text())
    assert {f: example[f] for f in codec_hotkeys.FIELDS} == DEFAULTS


@pytest.mark.skipif(not _real_pynput(), reason="pynput does not import here (headless CI)")
def test_default_keys_resolve_to_the_real_pynput_keys():
    kb = codec_config.keyboard
    assert codec_config.resolve_hotkeys({}) == (kb.Key.f13, kb.Key.f18, kb.Key.f16)


@pytest.mark.skipif(not _real_pynput(), reason="pynput does not import here (headless CI)")
def test_a_laptop_setting_resolves_to_the_real_pynput_keys_not_the_defaults():
    kb = codec_config.keyboard
    assert codec_config.resolve_hotkeys(LAPTOP) == (kb.Key.f6, kb.Key.f8, kb.Key.f9)


def test_saved_names_become_the_listener_keys():
    cfg = {"key_toggle": "F6", "key_voice": " f8 ", "key_text": "f9"}
    assert codec_config.resolve_hotkeys(cfg, kb=FAKE_KB) == (_K.f6, _K.f8, _K.f9)


def test_unusable_names_fall_back_to_their_default():
    bad = {"key_toggle": "x", "key_voice": "", "key_text": "f25"}
    assert codec_hotkeys.names(bad) == DEFAULTS
    assert codec_hotkeys.names({"key_voice": 8, "key_text": None}) == DEFAULTS
    assert codec_config.resolve_hotkeys(bad, kb=FAKE_KB) == (_K.f13, _K.f18, _K.f16)


# ── codec.py: simulated key presses on a non-default setting ────────────────

@pytest.fixture
def codec_keys(monkeypatch):
    """codec.py with the laptop keys in force and every side effect recorded."""
    import codec
    toggle, voice, text = codec_config.resolve_hotkeys(LAPTOP, kb=FAKE_KB)
    monkeypatch.setattr(codec, "KEY_TOGGLE", toggle)
    monkeypatch.setattr(codec, "KEY_VOICE", voice)
    monkeypatch.setattr(codec, "KEY_TEXT", text)
    monkeypatch.setattr(codec, "_KEY_NAMES", dict(LAPTOP))
    for key, value in (("active", True), ("recording", False), ("last_f13", 0.0), ("overlay_proc", None)):
        monkeypatch.setitem(codec.state, key, value)
    rec = {"pushed": [], "threads": [], "overlays": []}
    monkeypatch.setattr(codec, "push", lambda fn, *a: rec["pushed"].append(fn))

    class _Thread:
        def __init__(self, target=None, daemon=None, **_kw):
            rec["threads"].append(target)

        def start(self):
            pass

    monkeypatch.setattr(codec, "threading", types.SimpleNamespace(Thread=_Thread))
    monkeypatch.setattr(codec, "show_recording_overlay", lambda label: rec["overlays"].append(label))
    monkeypatch.setattr(codec, "show_recording_stop", lambda: None)
    monkeypatch.setattr(codec, "show_toggle_overlay", lambda on, hint="": rec["overlays"].append((on, hint)))
    monkeypatch.setattr(codec, "reset_voice_session", lambda: None)
    monkeypatch.setattr(codec, "_try_begin_recording",
                        lambda: codec.state.__setitem__("recording", True) or True)
    monkeypatch.setattr(codec, "_dispatch_cooldown", 0.0)
    monkeypatch.setattr(codec._core, "tts_playing", False)
    return codec, rec


def test_key_press_of_the_configured_voice_key_records_and_release_sends(codec_keys):
    codec, rec = codec_keys
    codec.on_press(_K.f8)
    assert codec.do_start_recording in rec["pushed"]
    assert rec["overlays"] == ["F8"], "the recording overlay names the configured key"
    assert codec.state["recording"] is True
    codec.on_release(_K.f8)
    assert codec.do_stop_voice in rec["pushed"]


def test_key_press_of_the_configured_text_key_opens_the_text_box(codec_keys):
    codec, rec = codec_keys
    codec.on_press(_K.f9)
    assert rec["threads"] == [codec.do_text]


def test_key_press_of_the_old_default_keys_does_nothing_once_changed(codec_keys):
    codec, rec = codec_keys
    for key in (_K.f13, _K.f18, _K.f16):
        codec.on_press(key)
        codec.on_release(key)
    assert rec == {"pushed": [], "threads": [], "overlays": []}
    assert codec.state["active"] is True and codec.state["recording"] is False


def test_key_press_of_the_configured_toggle_key_shows_the_configured_hint(codec_keys):
    codec, rec = codec_keys
    codec.state["active"] = False
    codec.on_press(_K.f6)
    assert codec.state["active"] is True
    assert len(rec["pushed"]) == 1
    rec["pushed"][0]()  # the overlay call that was queued
    assert rec["overlays"] == [(True, "F8=voice  F9=text  **=screen  ++=doc  --=chat")]


def test_codec_py_listens_for_the_settings_keys_and_prints_them():
    src = (REPO / "codec.py").read_text(encoding="utf-8")
    assert not re.search(r"keyboard\.Key\.f\d", src), "a hotkey is still hardcoded"
    for name in ("KEY_TOGGLE", "KEY_VOICE", "KEY_TEXT"):
        assert f"key == {name}" in src, f"on_press/on_release do not use {name}"
    assert "KEY_TOGGLE, KEY_VOICE, KEY_TEXT," in src, "imported from codec_config"
    shown = "\n".join(ln for ln in src.splitlines() if not ln.startswith('"""CODEC v2.1 |'))  # header names the defaults
    for literal in ("F18=voice", "F16=text", "'F18'", "Press F13 to activate", "F13 toggle   F18 voice"):
        assert literal not in shown, f"hint still says {literal}"
    assert "{_tg:<3} toggle   {_vk:<3} voice" in src, "the start banner prints the configured keys"


# ── The laptop preset ───────────────────────────────────────────────────────

def _dictate_fkeys() -> set:
    src = (REPO / "codec_dictate.py").read_text(encoding="utf-8")
    return set(re.findall(r"keyboard\.Key\.(f\d+)\b", src))


def test_laptop_preset_has_three_distinct_keys_within_f1_to_f12_that_dictate_does_not_use():
    keys = list(LAPTOP.values())
    assert set(LAPTOP) == set(codec_hotkeys.FIELDS)
    assert len(set(keys)) == 3, "two hotkeys on one key"
    for k in keys:
        assert codec_hotkeys.is_valid(k) and 1 <= int(k[1:]) <= 12, f"{k} is not on a MacBook keyboard"
    dictate = _dictate_fkeys()
    assert dictate, "codec_dictate.py F-keys not found (pattern moved?)"
    assert set(codec_hotkeys.RESERVED) == dictate, "RESERVED and codec_dictate.py disagree"
    assert not dictate & set(keys) and not dictate & set(DEFAULTS.values())
    assert codec_hotkeys.clash(LAPTOP) is None


def test_preset_setup_codec_offers_the_shared_presets():
    src = (REPO / "setup_codec.py").read_text(encoding="utf-8")
    assert 'codec_hotkeys.preset("laptop")' in src and 'codec_hotkeys.preset("extended")' in src
    assert '= "f5"' not in src, "the old F5 laptop preset clashes with Dictate"


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"agent_name": "C", "telegram": {"chat_id": "42"}}))
    monkeypatch.setattr(rc, "CONFIG_PATH", str(path))
    return path


@pytest.fixture
def client(cfg_path):
    app = FastAPI()
    app.include_router(rc.router)
    return TestClient(app)


def _saved(path):
    return json.loads(Path(path).read_text())


def test_default_keys_in_the_settings_api(client):
    assert client.get("/api/config").json()["keys"] == DEFAULTS


def test_preset_list_offers_extended_and_laptop(client):
    d = client.get("/api/hotkeys/presets").json()
    assert [p["id"] for p in d["presets"]] == ["extended", "laptop"]
    assert d["presets"][1]["keys"] == LAPTOP and "F12" in d["presets"][1]["label"]
    assert d["current"] == DEFAULTS and d["active"] == "extended"


def test_preset_choice_writes_the_three_keys(client, cfg_path):
    r = client.post("/api/hotkeys/preset", json={"preset": "laptop"})
    assert r.status_code == 200 and r.json()["saved"] is True and r.json()["keys"] == LAPTOP
    saved = _saved(cfg_path)
    assert {f: saved[f] for f in codec_hotkeys.FIELDS} == LAPTOP
    assert saved["telegram"] == {"chat_id": "42"}, "other keys untouched"
    assert client.get("/api/hotkeys/presets").json()["active"] == "laptop"
    client.post("/api/hotkeys/preset", json={"preset": "extended"})
    assert {f: _saved(cfg_path)[f] for f in codec_hotkeys.FIELDS} == DEFAULTS


@pytest.mark.parametrize("body", [{"preset": "gaming"}, {}, ["laptop"]])
def test_preset_unknown_is_refused_and_nothing_is_written(client, cfg_path, body):
    before = cfg_path.read_text()
    assert client.post("/api/hotkeys/preset", json=body).status_code == 400
    assert cfg_path.read_text() == before


# ── Settings: PUT /api/config ───────────────────────────────────────────────

def test_settings_put_saves_a_valid_key_lower_case(client, cfg_path):
    r = client.put("/api/config", json={"Hotkeys": {"key_voice": "F8"}})
    assert r.status_code == 200, r.text
    assert _saved(cfg_path)["key_voice"] == "f8"


@pytest.mark.parametrize("value", ["f21", "x", "", "F 8", 8, "f0"])
def test_settings_put_refuses_a_key_codec_cannot_hear(client, cfg_path, value):
    before = cfg_path.read_text()
    r = client.put("/api/config", json={"Hotkeys": {"key_toggle": value}})
    assert r.status_code == 422
    assert cfg_path.read_text() == before


def test_settings_put_refuses_dictates_f5(client, cfg_path):
    r = client.put("/api/config", json={"Hotkeys": {"key_toggle": "f5"}})
    assert r.status_code == 422 and "Dictate" in " ".join(r.json()["details"])


def test_settings_put_refuses_a_key_another_hotkey_uses(client, cfg_path):
    r = client.put("/api/config", json={"Hotkeys": {"key_voice": "f13"}})
    assert r.status_code == 422 and "start / stop" in " ".join(r.json()["details"])
    assert "key_voice" not in _saved(cfg_path)
    # all three changed together, each different: accepted
    r = client.put("/api/config", json={"Hotkeys": {"key_toggle": "f18", "key_voice": "f13", "key_text": "f16"}})
    assert r.status_code == 200
    # the Advanced editor is held to the same rule
    r = client.put("/api/config/raw", json={"config": {"key_text": "f13"}})
    assert r.status_code == 422


# ── The two pages list the presets ──────────────────────────────────────────

def _between(start: str, end: str) -> str:
    return DASH[DASH.index(start):DASH.index(end, DASH.index(start))]


def test_page_settings_offers_f1_to_f20_and_the_presets():
    hotkeys = _between("{ title: 'Hotkeys'", "{ title: 'Wake Word'")
    assert "presets: true" in hotkeys
    assert hotkeys.count("c: 'select', opts: _HOTKEY_OPTS") == 3, "the three keys are dropdowns"
    assert "for (var _hk = 1; _hk <= 20; _hk++) _HOTKEY_OPTS.push(['f' + _hk, 'F' + _hk]);" in DASH
    load = _between("async function loadHotkeyPresets()", "async function applyHotkeyPreset(")
    assert "fetch('/api/hotkeys/presets')" in load and "b.textContent = p.label;" in load
    assert "innerHTML" not in load, "server text goes in with textContent"
    apply_fn = _between("async function applyHotkeyPreset(", "function _setField(")
    assert "fetch('/api/hotkeys/preset', { method: 'POST'" in apply_fn
    render = _between("function renderSettings(cfg)", "async function loadHotkeyPresets()")
    assert 'id="hotkeyPresets"' in render and "loadHotkeyPresets();" in render


def test_page_first_run_screen_offers_the_presets():
    overlay = _between('<div class="setup-overlay" id="setupOverlay"', '<div class="app cs-main">')
    assert overlay.index('id="setupKeys"') < overlay.index('id="setupStatus"')
    assert 'id="setupKeyOpts"' in overlay and "Your keyboard" in overlay
    code = _between("function showKeys(){", "window.openConnectSetup=")
    assert "fetch('/api/hotkeys/presets')" in code and "b.textContent=p.label;" in code
    assert "fetch('/api/hotkeys/preset',{method:'POST'" in code
    assert "innerHTML" not in code
    tail = _between("window.openConnectSetup=", "})();")
    assert "showKeys()" in tail and "\n  showKeys();" in tail, "drawn when the screen opens"
