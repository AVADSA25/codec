"""Tests for the philips_hue skill command parser.

Regression (2026-06-08): the wake word "codec" leaks into commands on the
F18 / text / chat paths (only the hands-free wake-word listener strips it,
codec.py:778). The scene parser matched scene names by bare substring, and
one scene is literally named "codec" — the assistant's own name. So
"hey codec lights off" matched the codec scene and turned the lights ON
(orange) instead of OFF.

Fix: the `codec` scene must require an explicit "codec mode" / "codec scene"
qualifier so the bare wake word can never hijack an on/off/brightness command.
Other scenes are intentionally left untouched (surgical fix).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests  # noqa: E402

import codec_hue_discovery  # noqa: E402
import philips_hue  # noqa: E402


def test_wake_word_codec_does_not_hijack_off():
    # "hey codec lights off" must turn OFF, not fire the codec scene.
    state, desc = philips_hue._parse_action("hey codec lights off")
    assert state == {"on": False}, f"wake word leaked into scene match: {desc}"
    assert desc == "off"


def test_wake_word_codec_does_not_hijack_on():
    state, desc = philips_hue._parse_action("codec lights on")
    assert state == {"on": True}, f"got {desc}"
    assert desc == "on"


def test_codec_scene_still_reachable_with_qualifier():
    # The intentional codec scene stays reachable via "codec mode"/"codec scene".
    assert philips_hue._parse_action("set codec mode")[1] == "codec scene"
    assert philips_hue._parse_action("codec scene please")[1] == "codec scene"


def test_other_scenes_unchanged():
    # Surgical fix: only the codec scene is special-cased.
    assert philips_hue._parse_action("relax mode")[1] == "relax scene"
    assert philips_hue._parse_action("movie")[1] == "movie scene"


def test_plain_commands_unaffected():
    assert philips_hue._parse_action("lights off")[0] == {"on": False}
    assert philips_hue._parse_action("lights on")[0] == {"on": True}
    assert philips_hue._parse_action("lights 50%")[0]["bri"] == 127


# ── self-heal when the bridge IP changes (DHCP) ─────────────────────────────
def test_run_self_heals_when_bridge_ip_changed(monkeypatch):
    OLD, NEW = "192.168.1.81", "192.168.1.99"
    monkeypatch.setattr(philips_hue, "_load_config", lambda: (OLD, "user"))
    monkeypatch.setattr(philips_hue, "_resolve_target", lambda tl, ip, u: ("all", "0", "all lights"))

    def fake_apply(ip, user, ttype, tid, state):
        if ip == OLD:
            raise requests.ConnectionError("host down")  # stale IP unreachable
        return [{"success": {"/groups/0/action/on": False}}]  # new IP works

    monkeypatch.setattr(philips_hue, "_apply_state", fake_apply)
    monkeypatch.setattr(codec_hue_discovery, "rediscover_and_update_config", lambda *a, **k: NEW)

    assert philips_hue.run("lights off") == "Set all lights to off."


def test_run_absorbs_transient_connection_error_same_ip(monkeypatch):
    # A brief blip at the SAME ip must NOT surface as an error — retry absorbs it.
    IP = "192.168.1.81"
    monkeypatch.setattr(philips_hue, "_load_config", lambda: (IP, "user"))
    monkeypatch.setattr(philips_hue, "_resolve_target", lambda tl, ip, u: ("all", "0", "all lights"))
    calls = {"n": 0}

    def flaky_apply(ip, user, ttype, tid, state):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("transient blip")
        return [{"success": {}}]

    monkeypatch.setattr(philips_hue, "_apply_state", flaky_apply)
    monkeypatch.setattr(codec_hue_discovery, "rediscover_and_update_config", lambda *a, **k: IP)

    assert philips_hue.run("lights off") == "Set all lights to off."
    assert calls["n"] >= 2  # retried rather than giving up


def test_run_friendly_error_when_rediscovery_fails(monkeypatch):
    monkeypatch.setattr(philips_hue, "_load_config", lambda: ("192.168.1.81", "user"))
    monkeypatch.setattr(philips_hue, "_resolve_target", lambda tl, ip, u: ("all", "0", "all lights"))

    def dead(ip, user, ttype, tid, state):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(philips_hue, "_apply_state", dead)
    monkeypatch.setattr(codec_hue_discovery, "rediscover_and_update_config", lambda *a, **k: None)

    out = philips_hue.run("lights off")
    assert "Could not reach Hue Bridge" in out
