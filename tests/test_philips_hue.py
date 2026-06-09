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

import pytest  # noqa: E402
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


# ── launchctl relay: escape the macOS Local-Network block on the pm2 tree ────
# Regression (2026-06-09): macOS Sequoia's Local Network privacy denies the
# pm2/node process tree a LAN route to the Hue bridge ('[Errno 65] No route to
# host') while the internet still works, and there is no grantable entry for
# the python-under-node identity. The fix routes the bridge call OUTSIDE the
# pm2 tree via `launchctl asuser`, which runs in the GUI login session (which
# HAS Local-Network access) — using a python whose *launch path* is granted
# (e.g. /usr/local/bin/python3.13), NOT the dashboard's raw Cellar
# sys.executable (a separate, ungranted identity — the final-mile bug).
def test_get_falls_back_to_launchctl_relay_on_connection_error(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("[Errno 65] No route to host")

    monkeypatch.setattr(requests, "get", boom)
    sentinel = [{"state": {"on": True}}]
    captured = {}

    def fake_relay(method, url, body=None):
        captured.update(method=method, url=url, body=body)
        return sentinel

    monkeypatch.setattr(philips_hue, "_launchctl_request", fake_relay)
    out = philips_hue._get("192.168.1.81", "user", "/lights")
    assert out is sentinel  # did NOT propagate the ConnectionError
    assert captured["method"] == "GET"
    assert "192.168.1.81" in captured["url"]


def test_put_falls_back_to_launchctl_relay_on_connection_error(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("[Errno 65] No route to host")

    monkeypatch.setattr(requests, "put", boom)
    captured = {}

    def fake_relay(method, url, body=None):
        captured.update(method=method, url=url, body=body)
        return [{"success": {}}]

    monkeypatch.setattr(philips_hue, "_launchctl_request", fake_relay)
    body = {"on": False}
    out = philips_hue._put("192.168.1.81", "user", "/groups/0/action", body)
    assert out == [{"success": {}}]
    assert captured["method"] == "PUT"
    assert captured["body"] == body  # the PUT body is relayed through


def test_relay_prefers_granted_python_over_sys_executable(monkeypatch):
    """The final-mile fix: the relay must launch via a Local-Network-granted
    python path, not the raw Cellar sys.executable."""
    import subprocess

    real_exists = os.path.exists
    granted = "/usr/local/bin/python3.13"
    others = ("/opt/homebrew/bin/python3.13", "/usr/local/bin/python3",
              "/opt/homebrew/bin/python3")

    def fake_exists(p):
        if p == granted:
            return True
        if p in others:
            return False
        return real_exists(p)  # don't disturb pytest internals

    monkeypatch.setattr(os.path, "exists", fake_exists)
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        with open(argv[-1], "w") as fh:  # emulate the relay child writing its result file
            fh.write("[]")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    philips_hue._launchctl_request("GET", "http://192.168.1.81/api/u/lights")

    argv = captured["argv"]
    # argv = [launchctl, asuser, <uid>, <python>, -c, code, method, url, payload, out]
    assert argv[:3] == ["launchctl", "asuser", str(os.getuid())]
    assert argv[3] == granted, f"relay launched ungranted python: {argv[3]}"


def test_relay_propagates_bridge_error_as_connection_error(monkeypatch):
    import subprocess

    def fake_run(argv, **kw):
        with open(argv[-1], "w") as fh:
            fh.write("HUE_ERR:bridge unreachable")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(requests.ConnectionError):
        philips_hue._launchctl_request("GET", "http://192.168.1.81/api/u/lights")
