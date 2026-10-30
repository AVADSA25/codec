"""codec_paste: Cmd+V as Quartz key events, replacing pyautogui (audit P-011)."""
import ast
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import codec_paste

REPO = Path(__file__).resolve().parent.parent
CMD_FLAG = 0x100000


class FakeQuartz:
    kCGEventFlagMaskCommand = CMD_FLAG
    kCGHIDEventTap = "hid"

    def __init__(self, fail_on=None):
        self.posts = []
        self.fail_on = fail_on

    def CGEventCreateKeyboardEvent(self, source, keycode, down):
        return SimpleNamespace(keycode=keycode, down=down, flags=None)

    def CGEventSetFlags(self, event, flags):
        event.flags = flags

    def CGEventPost(self, tap, event):
        if self.fail_on == (event.keycode, event.down):
            raise RuntimeError("refused")
        self.posts.append((event.keycode, event.down, event.flags, tap))


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(codec_paste.time, "sleep", lambda s: None)


def test_sends_cmd_down_v_down_v_up_cmd_up(monkeypatch, no_sleep):
    q = FakeQuartz()
    monkeypatch.setattr(codec_paste, "_quartz", lambda: q)
    assert codec_paste.paste_clipboard() is True
    assert q.posts == [(55, True, CMD_FLAG, "hid"), (9, True, CMD_FLAG, "hid"),
                       (9, False, CMD_FLAG, "hid"), (55, False, 0, "hid")]


def test_cmd_is_released_and_osascript_used_when_v_is_refused(monkeypatch, no_sleep):
    q = FakeQuartz(fail_on=(9, True))
    ran = []
    monkeypatch.setattr(codec_paste, "_quartz", lambda: q)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: ran.append(cmd))
    assert codec_paste.paste_clipboard() is False
    assert q.posts[-1] == (55, False, 0, "hid"), "Cmd must never be left held"
    assert ran and ran[0][0] == "osascript"


def test_falls_back_to_osascript_when_quartz_is_missing(monkeypatch, no_sleep):
    ran = []

    def boom():
        raise ImportError("no Quartz")
    monkeypatch.setattr(codec_paste, "_quartz", boom)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: ran.append(cmd))
    assert codec_paste.paste_clipboard() is False
    assert "keystroke \"v\" using command down" in ran[0][2]


@pytest.mark.parametrize("name", ["codec_dictate.py", "codec_textassist.py", "codec_watcher.py"])
def test_paste_callers_do_not_import_pyautogui(name):
    tree = ast.parse((REPO / name).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] != "pyautogui" for a in node.names), name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "pyautogui", name
    assert "codec_paste.paste_clipboard" in (REPO / name).read_text()
