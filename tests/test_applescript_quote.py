"""P-007: every osascript sink escapes text with codec_applescript.escape.

subprocess is mocked everywhere (CI runs on Ubuntu, no osascript): the tests
read the built script and check the payload stays inside its string literal.
"""
import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

import codec_applescript

_REPO = Path(__file__).resolve().parents[1]

PAYLOADS = [
    'a\\" & (do shell script "id") & \\"b',
    'line one\nline two',
    'back\\slash "quoted" tab\there',
    'x" & (do shell script "touch /tmp/pwned") & "',
]


def _decode_literal(script, start):
    """Decode the AppleScript string literal opening at script[start] == '"'.
    Returns (text, index after the closing quote)."""
    assert script[start] == '"'
    out, i = [], start + 1
    esc = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}
    while i < len(script):
        c = script[i]
        if c == "\\":
            out.append(esc[script[i + 1]])
            i += 2
            continue
        if c == '"':
            return "".join(out), i + 1
        assert c not in "\r\n", "raw line break inside a literal"
        out.append(c)
        i += 1
    raise AssertionError("unterminated literal")


def _literal_after(script, prefix):
    i = script.index(prefix) + len(prefix)
    return _decode_literal(script, i)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_escape_round_trips(payload):
    script = f'"{codec_applescript.escape(payload)}" & "tail"'
    text, end = _decode_literal(script, 0)
    assert text == payload
    assert script[end:] == ' & "tail"'


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_t_{name}", _REPO / "skills" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def osa(monkeypatch):
    """Record every subprocess.run call; osascript 'succeeds' with no output."""
    calls = []

    def fake(argv, *a, **k):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake)
    return calls


def _scripts(calls):
    return [c[c.index("-e") + 1] for c in calls if c and c[0] == "osascript" and "-e" in c]


@pytest.mark.parametrize("payload", PAYLOADS)
def test_reminders(osa, payload):
    _load("reminders").run("remind me to " + payload)
    script = _scripts(osa)[-1]
    text, end = _literal_after(script, "{name:")
    assert "do shell script" not in script[end:] and script[end:] == "}"
    assert text.strip() == payload.strip()


@pytest.mark.parametrize("payload", PAYLOADS)
def test_apple_notes(osa, payload):
    _load("notes")._save_to_apple("save a note in apple notes: " + payload)
    script = _scripts(osa)[-1]
    text, end = _literal_after(script, "{body:")
    assert script[end:] == "}"
    assert text == text.strip() and text  # the whole note sits in the literal


@pytest.mark.parametrize("payload", PAYLOADS[:1] + PAYLOADS[3:])
def test_app_switch(osa, payload):
    _load("app_switch").run("switch to zzapp " + payload)
    script = next(s for s in _scripts(osa) if s.startswith("tell application"))
    text, end = _literal_after(script, "tell application ")
    assert script[end:] == " to activate"
    assert "do shell script" in text.lower()


@pytest.mark.parametrize("payload", PAYLOADS[:1] + PAYLOADS[3:])
def test_chrome_tabs(osa, payload):
    _load("chrome_tabs").run("switch to tab " + payload)
    script = _scripts(osa)[-1]
    text, end = _literal_after(script, "set targetQuery to ")
    assert script[end:].startswith("\n")
    assert "do shell script" in text


def test_chrome_automate(osa):
    mod = _load("chrome_automate")
    evil = 'https://example.com/" & (do shell script "id") & "'
    mod.WORK_TABS[:] = [evil]
    mod.run("work mode")
    script = next(s for s in _scripts(osa) if "open location" in s)
    text, end = _literal_after(script, "open location ")
    assert text == evil and script[end:] == ""


def test_imessage_uses_shared_escape():
    mod = _load("imessage_send")
    for p in PAYLOADS:
        assert mod._escape_text(p) == codec_applescript.escape(p)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_notification_script(payload):
    script = codec_applescript.notification_script(payload, payload, subtitle=payload, sound="Glass")
    pos = len("display notification ")
    for prefix in ("", " with title ", " subtitle ", " sound name "):
        assert script[pos:pos + len(prefix)] == prefix
        text, pos = _decode_literal(script, pos + len(prefix))
        assert text in (payload, "Glass")
    assert pos == len(script)


@pytest.mark.parametrize("path,needle", [
    ("codec_heartbeat.py", "codec_applescript.notification_script("),
    ("codec_scheduler.py", "codec_applescript.notification_script("),
    ("codec_textassist.py", "codec_applescript.escape(save_text)"),
])
def test_daemon_sinks_use_the_helper(path, needle):
    """These sinks run inside daemons or a script; the helper is tested above."""
    src = (_REPO / path).read_text()
    assert needle in src
    assert not re.search(r'display notification "\{', src)
    assert ".replace(\"'\", \"\")" not in src
