"""S-01: outside text a skill shows never reaches a command.

network_info, translate and file_search used to put the Wi-Fi name, the
ipify reply, the model's translation, the request and the found paths inside
a Terminal `do script` string. Now translate and file_search write the text to
a private file (codec_terminal_text) that Terminal prints with cat, and
network_info only returns it. subprocess, requests and codec_llm are faked:
no Terminal window, no network, nothing runs (one macOS-only test compiles the
script with osacompile, which runs nothing). Design:
docs/S01-TERMINAL-TEXT-DESIGN.md.
"""
import importlib.util
import os
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import codec_llm  # noqa: E402
import codec_terminal_text  # noqa: E402

SIZES = [0, 10 * 1024]


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_s01_{name}", REPO / "skills" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hostile(tmp_path, size=0):
    """Quotes, backslash, $( ), backticks, ; rm -rf ~, a newline then
    `end tell`, a do shell script, a terminal escape sequence and a tab.
    Starts and ends with plain words so the skills' trimming keeps it whole."""
    one = (f"S01 it's \"quoted\" back\\slash $(touch {tmp_path}/PWNED_A) `touch {tmp_path}/PWNED_B` "
           f"; rm -rf ~ ;\nend tell\ndo shell script \"touch {tmp_path}/PWNED_C\"\n"
           "\x1b]0;S01TITLE\x07 tab\there S01END")
    if size:
        one = ((one + " ") * (size // len(one) + 1)).strip()
        assert len(one) >= size
    return one


# Pieces of the hostile text that must not reach any command.
_PIECES = ["S01", "PWNED_", "$(", "`", "rm -rf", "do shell script", "S01TITLE", "\x1b]0"]


class _Fake:
    """Records every subprocess call and runs nothing."""

    def __init__(self, outputs=None):
        self.calls = []
        self.outputs = outputs or (lambda argv: "")

    def popen(self, argv, *a, **kw):
        self.calls.append(("Popen", argv, kw))
        return object()

    def run(self, argv, *a, **kw):
        self.calls.append(("run", argv, kw))
        return subprocess.CompletedProcess(argv, 0, stdout=self.outputs(argv), stderr="")

    def popens(self):
        return [c for c in self.calls if c[0] == "Popen"]


def _refuse(*a, **k):
    raise AssertionError("a shell command was started")


@pytest.fixture
def fake(monkeypatch, tmp_path):
    root = tmp_path / "tmproot"
    root.mkdir()
    monkeypatch.setattr(codec_terminal_text, "ROOT", str(root))
    monkeypatch.delenv("CODEC_MCP_TRANSPORT", raising=False)
    f = _Fake()
    monkeypatch.setattr(subprocess, "Popen", f.popen)
    monkeypatch.setattr(subprocess, "run", f.run)
    monkeypatch.setattr(os, "system", _refuse)
    monkeypatch.setattr(os, "popen", _refuse)
    f.root = root
    return f


def _no_piece_in(argv):
    items = argv if isinstance(argv, (list, tuple)) else [argv]
    for item in items:
        for piece in _PIECES:
            assert piece not in str(item), f"{piece!r} reached a command: {str(item)[:200]!r}"


def _nothing_ran(tmp_path):
    assert not [p.name for p in tmp_path.iterdir() if p.name.startswith("PWNED_")]


def _do_script(script):
    """Decode the `do script "..."` literal. Returns (shell command, the
    script after the closing quote). Only the escapes codec_applescript.escape
    writes are valid AppleScript (\\033 is a compile error)."""
    start = script.index('do script "') + len('do script "')
    esc = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}
    out, i = [], start
    while i < len(script):
        c = script[i]
        if c == "\\":
            nxt = script[i + 1]
            assert nxt in esc, f"invalid AppleScript escape \\{nxt}"
            out.append(esc[nxt])
            i += 2
            continue
        if c == '"':
            return "".join(out), script[i + 1:]
        out.append(c)
        i += 1
    raise AssertionError("unterminated do script literal")


def _tokens(cmd):
    return list(shlex.shlex(cmd, posix=True, punctuation_chars=True))


def _shown(fake):
    """The one osascript call: checks it and returns (tokens, folder, path)."""
    popens = fake.popens()
    assert len(popens) == 1, popens
    _, argv, kw = popens[0]
    assert isinstance(argv, list) and argv[:2] == ["osascript", "-e"] and len(argv) == 3
    assert not kw.get("shell")
    _no_piece_in(argv)
    script = argv[2]
    assert script.count("end tell") == 1
    cmd, rest = _do_script(script)
    assert rest.strip() == "end tell"
    folders = list(fake.root.iterdir())
    assert len(folders) == 1, folders
    folder = folders[0]
    path = folder / codec_terminal_text.FILENAME
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    return _tokens(cmd), str(folder), str(path)


# ── network_info: the result is returned, nothing is shown ─────────────────


@pytest.mark.parametrize("size", SIZES)
def test_network_info_puts_no_outside_text_in_a_command(fake, monkeypatch, tmp_path, size):
    mod = _load("network_info")
    ssid, reply = _hostile(tmp_path, size), _hostile(tmp_path, size)

    def outputs(argv):
        if argv[:3] == ["ipconfig", "getifaddr", "en0"]:
            return "192.168.1.5\n"
        if argv[:3] == ["networksetup", "-getairportnetwork", "en0"]:
            return f"Current Wi-Fi Network: {ssid}\n"
        return ""

    fake.outputs = outputs

    class _Reply:
        text = reply

    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _Reply())
    out = mod.run("my ip")
    assert out == f"Local IP: 192.168.1.5 (en0)\nPublic IP: {reply}\n[en0] {ssid}"
    assert fake.popens() == []
    for kind, argv, kw in fake.calls:
        assert argv[0] in ("ipconfig", "networksetup") and not kw.get("shell")
        _no_piece_in(argv)
    assert list(fake.root.iterdir()) == []
    _nothing_ran(tmp_path)


# ── translate: the body goes in the private file ───────────────────────────


@pytest.mark.parametrize("size", SIZES)
def test_translate_shows_outside_text_only_through_the_private_file(fake, monkeypatch, tmp_path, size):
    mod = _load("translate")
    text = _hostile(tmp_path, size)
    native = _hostile(tmp_path, size).replace("\n", " ")
    roman = f"S01 roman 'q' \"d\" $(touch {tmp_path}/PWNED_D)"
    literal = f"S01 literal `touch {tmp_path}/PWNED_E` \\"
    monkeypatch.setattr(codec_llm, "call", lambda *a, **k: f"{native}\n{roman}\n{literal}")

    out = mod.run(f"translate {text} to japanese")
    assert out == f"In Japanese, {text} is pronounced: {roman}"

    tokens, folder, path = _shown(fake)
    assert tokens == ["clear", "&&", "cat", path, ";", "rm", "-f", path, ";",
                      "rmdir", folder, ";", "read", "-n", "1"]
    body = "\n".join([f"  {text}  →  Japanese", "", f"  {native}",
                      f"  Pronunciation: {roman}", f"  Literal: {literal}"])
    shown = Path(path).read_text(encoding="utf-8")
    assert shown == mod._HEADER + codec_terminal_text.clean(body) + mod._FOOTER
    assert shown.startswith("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n  CODEC TRANSLATION\n")
    assert shown.endswith("\nPress any key to close...\n")
    assert "S01END" in shown and "\x1b" not in shown
    _nothing_ran(tmp_path)


# ── file_search: the request and the paths go in the private file ─────────


@pytest.mark.parametrize("size", SIZES)
def test_file_search_shows_outside_text_only_through_the_private_file(fake, tmp_path, size):
    mod = _load("file_search")
    query_text = _hostile(tmp_path, size)
    files = [f"/Users/someone/{_hostile(tmp_path, size).replace(chr(10), ' ')}.txt",
             f"/tmp/a'b\"c$(touch {tmp_path}/PWNED_F)`x`.md"]
    fake.outputs = lambda argv: "\n".join(files) + "\n" if argv[0] == "mdfind" else ""
    task = f"find file {query_text}"
    query = mod._extract_query(task)
    assert query == query_text

    out = mod.run(task)
    assert out == "Found 2 files — opened in Terminal.\n" + "\n".join(files)
    runs = [c for c in fake.calls if c[0] == "run"]
    # The query is the search itself: one argv item for mdfind, no shell.
    assert [(argv, bool(kw.get("shell"))) for _, argv, kw in runs] == [(["mdfind", "-name", query], False)]

    tokens, folder, path = _shown(fake)
    assert tokens == ["cat", path, ";", "rm", "-f", path, ";", "rmdir", folder]
    result = f"Found 2 files matching '{query}':\n" + "\n".join(files)
    shown = Path(path).read_text(encoding="utf-8")
    assert shown == mod._HEADER + codec_terminal_text.clean(result) + mod._FOOTER
    assert shown.startswith("\n\x1b[38;2;232;113;26m━━━ CODEC FILE SEARCH ━━━\x1b[0m\n\n")
    assert "S01END" in shown and "\x1b]0" not in shown
    _nothing_ran(tmp_path)


# ── both window skills: remote callers and a failed osascript start ────────


def _run_window_skill(name, monkeypatch, tmp_path, fake):
    mod = _load(name)
    if name == "translate":
        monkeypatch.setattr(codec_llm, "call", lambda *a, **k: "ネコ\nneko\ncat")
        return mod.run("translate cat to japanese")
    fake.outputs = lambda argv: "/tmp/found.txt\n" if argv[0] == "mdfind" else ""
    return mod.run("find file found.txt")


@pytest.mark.parametrize("name", ["translate", "file_search"])
def test_remote_callers_get_the_text_and_no_window(fake, monkeypatch, tmp_path, name):
    monkeypatch.setenv("CODEC_MCP_TRANSPORT", "http")
    out = _run_window_skill(name, monkeypatch, tmp_path, fake)
    assert out and fake.popens() == []
    assert list(fake.root.iterdir()) == []


@pytest.mark.parametrize("name", ["translate", "file_search"])
def test_a_failed_osascript_start_leaves_no_file(fake, monkeypatch, tmp_path, name):
    def no_osascript(argv, *a, **kw):
        fake.calls.append(("Popen", argv, kw))
        raise FileNotFoundError("osascript")

    monkeypatch.setattr(subprocess, "Popen", no_osascript)
    out = _run_window_skill(name, monkeypatch, tmp_path, fake)
    assert out and len(fake.popens()) == 1
    assert list(fake.root.iterdir()) == []


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("osacompile"),
                    reason="osacompile is macOS only")
@pytest.mark.parametrize("name", ["translate", "file_search"])
def test_the_terminal_script_compiles(fake, monkeypatch, tmp_path, name):
    # The old network_info and file_search scripts held \033 in an AppleScript
    # literal, which does not compile, so their windows never opened.
    _run_window_skill(name, monkeypatch, tmp_path, fake)
    script = fake.popens()[0][1][2]
    monkeypatch.undo()  # the real subprocess.run again; osacompile runs nothing
    r = subprocess.run(["osacompile", "-o", str(tmp_path / "s01.scpt"), "-e", script],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


# ── codec_terminal_text ────────────────────────────────────────────────────


def test_write_private_makes_a_private_file_with_clean_text(tmp_path, monkeypatch):
    monkeypatch.setattr(codec_terminal_text, "ROOT", str(tmp_path))
    folder, path = codec_terminal_text.write_private("a\x1b[31mb\rc\x00d\x7fe\x9bf\tg\nh ü", "\x1b[0mH\n", "F")
    assert Path(folder).parent == tmp_path and Path(folder).name.startswith("codec_show_")
    assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert Path(path).read_text(encoding="utf-8") == "\x1b[0mH\na?[31mb?c?d?e?f\tg\nh üF"
    codec_terminal_text.remove(folder)
    assert not os.path.lexists(folder)
    codec_terminal_text.remove(folder)  # gone already: no error


def test_sweep_deletes_only_old_one_file_folders_of_this_user(tmp_path, monkeypatch):
    monkeypatch.setattr(codec_terminal_text, "ROOT", str(tmp_path))
    old = time.time() - codec_terminal_text.LEFTOVER_SECONDS - 60

    def folder(name, files=("text.txt",), age=old):
        d = tmp_path / name
        d.mkdir()
        for f in files:
            (d / f).write_text("x")
        os.utime(d, (age, age))
        return d

    leftover = folder("codec_show_old")
    young = folder("codec_show_young", age=time.time())
    extra = folder("codec_show_extra", files=("text.txt", "mine.txt"))
    other = folder("someone_else_old")
    target = folder("target_old")
    (tmp_path / "codec_show_link").symlink_to(target)
    os.utime(tmp_path / "codec_show_link", (old, old), follow_symlinks=False)

    folder_new, _ = codec_terminal_text.write_private("hello")  # sweeps first
    assert not leftover.exists()
    for kept in (young, extra, other, target, Path(folder_new)):
        assert kept.is_dir()
    assert (target / "text.txt").exists() and (tmp_path / "codec_show_link").is_symlink()
    assert codec_terminal_text.sweep() == 0
