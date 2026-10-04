"""Accessibility baseline (UI phase 2, P2.13; docs/P2.13-DESIGN.md)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGES = {p.name: p.read_text(encoding="utf-8") for p in sorted(REPO.glob("codec_*.html"))}
SHELL = (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")
CSS = (REPO / "static" / "codec.css").read_text(encoding="utf-8")


def _unnamed_buttons(src: str):
    for m in re.finditer(r"<button\b([^>]*)>(.*?)</button>", src, re.S):
        attrs, inner = m.group(1), m.group(2)
        text = re.sub(r"<[^>]+>", "", inner)
        text = re.sub(r"'\s*\+[^+]*\+\s*'", "X", text).strip()
        if text or "aria-label" in attrs:
            continue
        if re.search(r'id="csAsk(Cancel|Ok)"', attrs):  # text is set when the dialog opens
            continue
        yield src[:m.start()].count("\n") + 1, attrs.strip()[:80]


@pytest.mark.parametrize("name", list(PAGES) + ["static/codec-shell.js"])
def test_every_icon_button_has_a_name(name):
    src = PAGES.get(name) or SHELL
    assert list(_unnamed_buttons(src)) == []


def test_live_regions_and_status_roles():
    chat, voice, home, tasks = PAGES["codec_chat.html"], PAGES["codec_voice.html"], PAGES["codec_dashboard.html"], PAGES["codec_tasks.html"]
    assert 'id="messages" role="log" aria-live="polite"' in chat
    assert 'id="transcript" role="log" aria-live="polite"' in voice
    assert 'id="toast" role="status" aria-live="polite"' in chat and 'id="toast" role="status" aria-live="polite"' in home
    assert 'id="toastContainer" role="status" aria-live="polite"' in tasks
    assert "t.setAttribute('role', 'status');" in SHELL


def _css_blocks(src):
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", src):
        yield m.group(1).strip(), m.group(2)


@pytest.mark.parametrize("name", list(PAGES) + ["static/codec.css"])
def test_no_bare_outline_none(name):
    src = PAGES.get(name) or CSS
    bad = []
    for sel, body in _css_blocks(src):
        if not re.search(r"outline\s*:\s*(none|0)\b", body):
            continue
        if "box-shadow" in body and ("focus" in sel or "--focus-ring" in body):
            continue  # the focus ring replaces the outline
        if sel in (".cs-search input", ".cs-pal-search input", ".chat-input", ".chat-input:focus-visible",
                   ".cs-menu-list", ".cs-pop-item:hover, .cs-pop-item:focus-visible"):
            continue  # the container shows focus (:focus-within), or the focused row is highlighted
        bad.append(sel[-60:])
    assert bad == []
    assert ":focus-visible { outline: 2px solid var(--accent-ring); outline-offset: 2px; }" in CSS


def test_clickable_rows_are_buttons_or_role_button():
    chat, vibe = PAGES["codec_chat.html"], PAGES["codec_vibe.html"]
    assert '<button type="button" class="fc-close" aria-label="Remove' in chat and 'class="fc-close" role="button"' not in chat
    assert ">+ New Project</button>" in vibe and '<span onclick="clearWorkingFolder()"' not in vibe
    assert 'class="prompt-header" role="button" tabindex="0" aria-expanded="false"' in PAGES["codec_dashboard.html"]
    assert "h.setAttribute('aria-expanded', String(card.classList.contains('open')));" in PAGES["codec_dashboard.html"]
    assert 'role="button" tabindex="0" onclick="markReportRead(' in PAGES["codec_tasks.html"]
    assert 'role="button" tabindex="0" onclick="ldSes(' in vibe
    assert 'id="fpRing" onclick="authenticate()" aria-hidden="true"' in PAGES["codec_auth.html"]
    keys = SHELL[SHELL.index("// A clickable row that cannot be a <button>"):][:500]
    assert "(e.key === 'Enter' || e.key === ' ')" in keys and "t.click();" in keys


def _dialogs(src):
    for m in re.finditer(r"<div\b[^>]*\bdata-dialog=\"[^\"]*\"[^>]*>", src):
        tag = m.group(0)
        # A sheet's overlay may hold the dialog role on its inner panel.
        yield tag if 'role="dialog"' in tag else src[m.start():m.start() + 400]


def test_every_marked_dialog_is_a_labelled_modal_with_a_close():
    found = 0
    for name, src in list(PAGES.items()) + [("shell", SHELL)]:
        for tag in _dialogs(src):
            found += 1
            assert 'role="dialog"' in tag and 'aria-modal="true"' in tag and "aria-label" in tag, (name, tag[:120])
        assert src.count("data-dialog-close") >= len(list(_dialogs(src))), name
    assert found == 8, "quick settings, image viewers on Chat, Home, Vibe and Voice, Agents sheet, Tasks preview, Vibe drawer"
    trap = SHELL[SHELL.index("var DLG = { stack: [], seen: [] };"):SHELL.index("function ready() {")]
    for needle in ("e.key === 'Tab'", "last.focus()", "first.focus()", "e.key === 'Escape'",
                   "top.querySelector('[data-dialog-close]')", "back.focus()", "new MutationObserver("):
        assert needle in trap, needle
    assert "dlgWatch();" in SHELL[SHELL.index("function ready() {"):][:200]


def test_tabs_and_pressed_states():
    tasks = PAGES["codec_tasks.html"]
    assert '<div class="tabs" role="tablist"' in tasks and tasks.count('role="tab" aria-selected=') == 4
    assert tasks.count('role="tabpanel"') == 4 and "setAttribute('aria-selected', 'true');" in tasks
    assert PAGES["codec_dashboard.html"].count('role="tab" aria-selected="false" data-sub=') == 4
    assert "b.setAttribute('aria-pressed', String(!!on));  // P2.13" in SHELL
    assert "b.setAttribute('aria-pressed', String(st.textContent.trim() === 'ON'));" in SHELL


def test_two_factor_code_autofill():
    auth = PAGES["codec_auth.html"]
    for pre in ("totp", "setup"):
        assert f'id="{pre}1" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code"' in auth
    assert "if (idx === 1 && this.value.replace(/\\D/g, '').length > 1) {" in auth
    assert "(idx === 1 ? /^\\d+$/ : /^\\d$/)" in auth
    assert auth.count('aria-label="Digit ') == 10 and auth.count('aria-label="PIN digit ') == 6


def test_reduced_motion_everywhere():
    block = CSS[CSS.index("@media (prefers-reduced-motion: reduce) {"):][:400]
    assert "animation-duration: 0.01ms !important" in block and "transition-duration: 0.01ms !important" in block
    assert "scroll-behavior: auto !important" in block


_JS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><html><head></head><body><script src="/static/codec-shell.js" data-page="tasks" data-title="Tasks"></script>' +
  '<main class="cs-main"><button id="opener">Open</button><div id="row" role="button" tabindex="0">Row</div>' +
  '<div id="dlg" class="x" role="dialog" aria-modal="true" aria-label="T" data-dialog="show">' +
  '<button id="a">A</button><button id="b">B</button><button id="close" data-dialog-close>Close</button></div></main></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/tasks' });
const w = dom.window, d = w.document;
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {} });
w.fetch = () => Promise.resolve({ ok: false, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
w.HTMLElement.prototype.getClientRects = function () { return [1]; };
w.eval(fs.readFileSync(process.argv[1], 'utf8'));
const key = (el, k, shift) => el.dispatchEvent(new w.KeyboardEvent('keydown', { key: k, shiftKey: !!shift, bubbles: true }));
const wait = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const out = {}, dlg = d.getElementById('dlg');
  let rows = 0; d.getElementById('row').addEventListener('click', () => rows++);
  d.getElementById('close').addEventListener('click', () => dlg.classList.remove('show'));
  d.getElementById('opener').focus();
  dlg.classList.add('show'); await wait(80);
  out.first = d.activeElement.id;
  d.getElementById('close').focus(); key(d.activeElement, 'Tab'); out.wrap = d.activeElement.id;
  key(d.activeElement, 'Tab', true); out.back = d.activeElement.id;
  key(d.activeElement, 'Escape'); await wait(30);
  out.closed = !dlg.classList.contains('show'); out.returned = d.activeElement.id;
  d.getElementById('row').focus(); key(d.getElementById('row'), 'Enter'); key(d.getElementById('row'), ' ');
  out.rows = rows;
  console.log(JSON.stringify(out)); process.exit(0);
})().catch(e => { console.log('ERR ' + e.message); process.exit(1); });
"""


def _jsdom_available() -> bool:
    if shutil.which("node") is None:
        return False
    r = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True, cwd=REPO,
                       env=os.environ.copy(), timeout=20)
    return r.returncode == 0


@pytest.mark.skipif(not _jsdom_available(), reason="node with jsdom not resolvable (set NODE_PATH)")
def test_dialog_trap_and_role_button_under_jsdom():
    out = subprocess.run(["node", "-e", _JS, str(REPO / "static" / "codec-shell.js")], capture_output=True, text=True,
                         cwd=REPO, env=os.environ.copy(), timeout=60)
    d = json.loads([ln for ln in out.stdout.splitlines() if ln.startswith("{")][-1])
    assert d["first"] == "a", "focus moves into the dialog"
    assert d["wrap"] == "a" and d["back"] == "close", "Tab and Shift+Tab stay inside"
    assert d["closed"] is True and d["returned"] == "opener", "Esc closes; focus goes back"
    assert d["rows"] == 2, "Enter and Space press a role=button row"
