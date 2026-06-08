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
