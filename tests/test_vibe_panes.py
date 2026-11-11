"""Vibe code and preview panes that open when there is something to show (static/vibe-panes.js,
docs/VOICE-IN-CHAT-AND-VIBE-PANES-DESIGN.md part 2).

The rule is plain functions that Node runs without a browser; the rest checks the page wiring.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
JS = REPO / "static" / "vibe-panes.js"
SRC = JS.read_text(encoding="utf-8")
PAGE = (REPO / "codec_vibe.html").read_text(encoding="utf-8")

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def run(expr: str):
    script = f"const V = require({json.dumps(str(JS))}); console.log(JSON.stringify({expr}));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


@node
def test_auto_opens_on_content_and_the_owners_choice_wins():
    assert run("[V.isOpen('auto', false), V.isOpen('auto', true)]") == [False, True]
    assert run("[V.isOpen('closed', true), V.isOpen('open', false)]") == [False, True]


@node
def test_code_counts_project_files_or_real_editor_text_never_the_placeholder():
    assert run("V.hasCode(['index.html'], '')") is True
    assert run("V.hasCode([], '// Ask CODEC to build something\\n')") is False
    assert run("V.hasCode([], '# New project')") is False
    assert run("V.hasCode([], '   ')") is False
    assert run("V.hasCode([], 'print(1)')") is True


@node
def test_a_page_is_what_the_preview_has_always_shown():
    assert run("[V.isPage('<!DOCTYPE html><p>'), V.isPage('<canvas>'), V.isPage('x<body>'), V.isPage('<html>')]") == [True] * 4
    assert run("[V.isPage('print(1)'), V.isPage(''), V.isPage(null)]") == [False] * 3


@node
def test_each_project_keeps_its_own_choice_and_the_old_hide_code_carries_over():
    assert run("[V.storageKey('p1'), V.storageKey(''), V.storageKey(null)]") == [
        "codec-vibe-panes:p1", "codec-vibe-panes:", "codec-vibe-panes:"]
    assert run("V.parse(null, null)") == {"code": "auto", "preview": "auto"}
    assert run("V.parse(null, 'off')") == {"code": "closed", "preview": "auto"}, "Hide code set before stays hidden"
    assert run("V.parse(null, 'on')") == {"code": "auto", "preview": "auto"}
    assert run("V.parse('{\"code\":\"open\",\"preview\":\"closed\"}', 'off')") == {"code": "open", "preview": "closed"}
    assert run("V.parse('{\"code\":\"weird\",\"preview\":7}', null)") == {"code": "auto", "preview": "auto"}
    assert run("V.parse('not json', null)") == {"code": "auto", "preview": "auto"}


@node
def test_a_click_sets_one_pane_and_only_a_pane_on_auto_opens_by_itself():
    assert run("V.choose({code:'auto', preview:'auto'}, 'preview', false)") == {"code": "auto", "preview": "closed"}
    assert run("V.choose({code:'closed', preview:'auto'}, 'code', true)") == {"code": "open", "preview": "auto"}
    assert run("V.choose({code:'auto', preview:'auto'}, 'other', true)") == {"code": "auto", "preview": "auto"}
    assert run("[V.openedByItself(false, true, 'auto'), V.openedByItself(false, true, 'open'), V.openedByItself(true, true, 'auto')]") == [
        True, False, False]


def test_script_is_stamped_and_loaded_after_the_other_vibe_scripts():
    tag = re.search(r'<script src="/static/vibe-panes\.js\?v=[0-9a-f]{8}"></script>', PAGE)
    assert tag, "stamped by tools/stamp_static.py"
    assert PAGE.index("/static/vibe-chat.js") < tag.start(), "VibeProj exists when it first looks"
    stamp = (REPO / "tools" / "stamp_static.py").read_text(encoding="utf-8")
    assert '"vibe-panes.js"' in stamp


def test_panes_start_closed_and_two_buttons_bring_them_back_on_a_wide_screen():
    assert '<div class="sp code-off prev-off" data-pane="chat">' in PAGE, "no flash of empty panes"
    assert '<div class="pp off" id="pp">' in PAGE
    for bid, pane in (("showCode", "code"), ("showPrev", "preview")):
        tag = re.search(r'<button[^>]*id="' + bid + r'"[^>]*>', PAGE).group(0)
        assert f"VibePanes.pick('{pane}', true)" in tag
    assert "@media (max-width:768px){.pane-btns{display:none}}" in PAGE, "the phone keeps its tabs"
    assert ".sp.code-off.prev-off .cp{flex:1 1 auto!important;width:auto!important" in PAGE, "the chat takes the width"
    assert re.search(r'id="vtabCode"[^>]*>Code<span class="vdot" hidden></span>', PAGE)


def test_an_automatic_refresh_never_opens_a_preview_the_owner_closed():
    fn = PAGE[PAGE.index("function doPrev(silent)"):PAGE.index("function refreshPreview()")]
    assert "if (VibePanes.isPage(c)) {" in fn
    assert "if (silent && !VibePanes.allow('preview')) return;" in fn
    assert "if (silent) VibePanes.setPrev(true); else VibePanes.pick('preview', true, true);" in fn
    close = PAGE[PAGE.index("function closePrev()"):PAGE.index("function toggleCodePane()")]
    assert "VibePanes.pick('preview', false);" in close
    assert "function allow(pane) { return prefs()[pane] !== 'closed'; }" in SRC


def test_the_editor_tells_the_panes_when_the_first_file_appears():
    assert "if (ed) VibePanes.attachEditor(ed);" in PAGE
    attach = SRC[SRC.index("function attachEditor(ed)"):SRC.index("root.VibePanes =")]
    assert "onDidChangeModelContent(syncSoon)" in attach and "onDidChangeModel(syncSoon)" in attach


def test_nothing_goes_in_as_html():
    page_half = SRC[SRC.index("// ── the page"):]
    assert "innerHTML" not in page_half and "insertAdjacentHTML" not in page_half
