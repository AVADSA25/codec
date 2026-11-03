"""Vibe page fixes (P-064, docs/VIBE-PAGE-LOOP-DEFECTS-DESIGN.md): the page's own pure functions run in Node, plus the
wiring around them in codec_vibe.html and static/vibe-projects.js.

The functions are taken out of the page's script by name, so what runs here is the code the page runs.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = (REPO / "codec_vibe.html").read_text(encoding="utf-8")
PROJ = (REPO / "static" / "vibe-projects.js").read_text(encoding="utf-8")
CHAT = REPO / "static" / "vibe-chat.js"

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def fn(name: str) -> str:
    """The source of one top-level function of the page script."""
    start = PAGE.index(f"\nfunction {name}(") + 1
    return PAGE[start:PAGE.index("\n}\n", start) + 3]


def run(names, expr: str, setup: str = ""):
    script = (f"const C = require({json.dumps(str(CHAT))});\n{setup}\n" + "\n".join(fn(n) for n in names) +
              f"\nconsole.log(JSON.stringify({expr}));")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


# ── 1. a reloaded session ───────────────────────────────────────────────

@node
def test_a_reloaded_session_sends_no_old_file_block_back_to_the_model():
    rows = [{"role": "user", "content": "make a page\n\n[PROJECT FILES]\nOpen file: index.html\n```html file:index.html\n<h1>x</h1>\n```"},
            {"role": "assistant", "content": "Here."},
            {"role": "user", "content": "fix it\n\n[CURRENT CODE]\n```\nprint(1)\n```"},
            {"role": "assistant", "content": "Fixed."}]
    new = {"role": "user", "content": "blue now\n\n[PROJECT FILES]\nOpen file: index.html", "plain": "blue now"}
    setup = f"var hist = C.loaded({json.dumps(rows)}); hist.push({json.dumps(new)});"
    for project in ("true", "false"):
        sent = run(["_histForSend"], f"_histForSend({project})", setup)
        assert [m["content"] for m in sent[:-1]] == ["make a page", "Here.", "fix it", "Fixed."], project
        assert "[PROJECT FILES]" in sent[-1]["content"], "the latest message still carries the files"


def test_opening_a_session_goes_through_loaded():
    ld = fn("ldSes")
    assert "var rows = window.VibeChat ? VibeChat.loaded(d.messages) : d.messages;" in ld
    assert "addMsg(m.role === 'user' ? 'user' : 'assistant', m.content || '');" in ld and "var m = rows[i];" in ld


# ── 2. Run ──────────────────────────────────────────────────────────────

@node
def test_run_shows_the_servers_refusal_and_never_exit_undefined():
    cases = run(["_runReport"], "["
                "_runReport(false, 400, {error: 'Unsupported language: java'}),"
                "_runReport(false, 403, {error: 'Running code works only on this Mac.'}),"
                "_runReport(false, 502, {}),"
                "_runReport(true, 200, {error: 'odd'}),"
                "_runReport(true, 200, {stdout: 'hi', stderr: '', exit_code: 0, elapsed: 0.1}),"
                "_runReport(true, 200, {stdout: '', stderr: 'boom', exit_code: 1, elapsed: 0.2})]")
    assert cases[0] == {"rs": "Error", "lines": [["er", "Unsupported language: java"]]}
    assert cases[1] == {"rs": "Error", "lines": [["er", "Running code works only on this Mac."]]}
    assert cases[2] == {"rs": "Error", "lines": [["er", "The server answered 502"]]}
    assert cases[3]["rs"] == "Error"
    assert cases[4] == {"rs": "Done (0.1s)", "lines": [["ot", "hi"]]}
    assert cases[5] == {"rs": "Exit:1 (0.2s)", "lines": [["er", "boom"]]}
    assert "undefined" not in json.dumps(cases)
    assert "_runReport(r.ok, r.status, d)" in fn("doRun")


# ── 3. Save ─────────────────────────────────────────────────────────────

@node
def test_single_file_save_says_saved_only_when_the_server_wrote_the_file():
    got = run(["_saveReport"], "[_saveReport(true, 200, {path: '/Users/x/codec-workspace/a.py', size: 3}),"
              "_saveReport(false, 403, {error: 'Directory not allowed', reason: 'Blocked path: /etc'}),"
              "_saveReport(false, 500, {}), _saveReport(true, 200, {})]")
    assert got[0] == ["sy", "Saved: /Users/x/codec-workspace/a.py"]
    assert got[1] == ["er", "Not saved: Directory not allowed (Blocked path: /etc)"]
    assert got[2] == ["er", "Not saved: the server answered 500"] and got[3][0] == "er"


def test_save_in_a_project_keeps_a_version_and_never_posts_to_save_file():
    save = fn("doSave")
    assert save.index("VibeProj.saveVersion(); return;") < save.index("/api/save_file")
    version = PROJ[PROJ.index("function saveVersion() {"):PROJ.index("function closeVersions() {")]
    assert "flush().then" in version and "api('POST', base('/checkpoint')" in version
    assert "/api/save_file" not in PROJ and "saveVersion: saveVersion" in PROJ
    assert "sv.textContent = P.id ? 'Save version' : 'Save';" in PROJ
    assert '<button class="bt" id="saveBtn" onclick="doSave()"' in PAGE and '<span id="saveLbl">Save</span>' in PAGE


# ── 4. Inspect ──────────────────────────────────────────────────────────

@node
def test_inspect_sets_its_properties_and_keeps_every_other_declaration():
    tags = run(["_styleDecls", "_tagWithStyle"], "["
               "_tagWithStyle('<h1 class=\"t\" style=\"color:red; margin: 0 auto;\">', [['font-size', '30px']]),"
               "_tagWithStyle('<h1 style=\"color:red;font-size:12px\">', [['font-size', '30px'], ['color', '#00ff00']]),"
               "_tagWithStyle('<p>', [['padding', '4px']]),"
               "_tagWithStyle('<img src=\"a.png\"/>', [['padding', '4px']]),"
               "_tagWithStyle('<div data-style=\"x\" style=\"background:url(data:image/png;base64,AA==);top:0\">', [['color', '#111111']]),"
               "_tagWithStyle(\"<b style='font-family:\\\"A\\\"'>\", [['color', '#111111']])]")
    assert tags[0] == '<h1 class="t" style="color:red;margin: 0 auto;font-size:30px">'
    assert tags[1] == '<h1 style="font-size:30px;color:#00ff00">'
    assert tags[2] == '<p style="padding:4px">' and tags[3] == '<img src="a.png" style="padding:4px"/>'
    assert tags[4] == '<div data-style="x" style="background:url(data:image/png;base64,AA==);top:0;color:#111111">'
    assert tags[5] == "<b style='font-family:\"A\";color:#111111'>"


def test_inspect_writes_only_what_the_owner_touched_in_the_file_the_preview_shows():
    panel = fn("showEditPanel")
    assert "panel._touched = {};" in panel and "addEventListener('input', mark)" in panel
    apply = fn("applyEdits")
    assert "bg: touched['ep-bg'] ? bg : null" in apply and "color: set.color, bg: set.bg, fs: set.fs, pad: set.pad" in apply
    assert "props.length ? _tagWithStyle(openTag, props) : openTag" in apply
    assert "var content  = vpath ? VibeProj.fileText(vpath) : ed.getModel().getValue();" in apply
    assert "VibeProj.activate(vpath); VibeProj.setFileText(vpath, newContent);" in apply
    assert "'background-color:' + bg" not in apply, "a transparent background is no longer written as black"
    agent = fn("_pvAgent")
    assert "if (d.bg != null) picked.style.backgroundColor = String(d.bg);" in agent
    assert "//" not in agent.replace("http://", ""), "the preview agent is put in the frame on one line"
    pick = fn("handleInspectPick")
    assert "var vpath = _inspectPath();" in pick and "VibeProj.activate(vpath)" in pick
    assert "function previewPath()" in PROJ and "previewPath: previewPath, fileText: fileText" in PROJ


# ── 5. one answer at a time ─────────────────────────────────────────────

def test_enter_while_an_answer_streams_starts_no_second_request():
    busy = "  if (_vibeBusy) { if (window.VibeAgent) VibeAgent.command(inp, true); return; }"     # only /stop runs (P-069)
    assert fn("sendVibe").startswith("function sendVibe() {\n  var inp = document.getElementById('vi');\n" + busy)
    send = fn("doSend")
    assert send.index("if (_vibeBusy) return;") < send.index("var t = inp.value.trim();")
    assert send.index("_vibeBusy = true;") < send.index("(async function() {")
    assert "} finally {\n      _vibeBusy = false;\n    }\n  })();" in send


# ── 6. the editor does not load ─────────────────────────────────────────

def test_the_page_script_runs_on_when_the_editor_does_not_load():
    guard = PAGE[PAGE.index("function _editorFailed() {"):PAGE.index("var LANG_EXT")]
    assert "if (typeof require === 'function' && typeof require.config === 'function') {" in guard
    assert "}, function () { _editorFailed(); });\n} else _editorFailed();" in guard
    assert "The editor could not load." in guard and "textContent" in guard and "innerHTML" not in guard
    assert re.search(r"function setDetectedLang\(lang\) \{\n  if \(!ed\) return;", PAGE)
    assert "if (ed && ed.getValue().trim() !== code.trim())" in PAGE
