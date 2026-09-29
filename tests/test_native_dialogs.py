"""Styled menus and sheets instead of the browser's dialogs (UI phase 2, P2.6; docs/P2.6-DESIGN.md).

No page calls confirm(), prompt() or alert(); the shell's CodecShell.ask (one modal, a
bottom sheet on the phone) and CodecShell.menu (a keyboard list over a hidden native
select) replace them, and every former dialog uses them.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SHELL = (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")
PAGES = {p.name: p.read_text(encoding="utf-8") for p in REPO.glob("codec_*.html")}
NATIVE = re.compile(r"(?:^|[^.\w])(confirm|alert|prompt)\(|window\.(confirm|alert|prompt)\(")


def _fn(src: str, head: str) -> str:
    start = src.index(head)
    return src[start:start + 900]


@pytest.mark.parametrize("name", sorted(PAGES) + ["static/codec-shell.js", "static/codec-md.js"])
def test_no_browser_dialog_is_left(name):
    src = PAGES.get(name) or (REPO / name).read_text(encoding="utf-8")
    hit = NATIVE.search(src)
    assert hit is None, f"{name}: {hit.group(0) if hit else ''}"


def test_the_shell_offers_one_modal_and_one_picker():
    assert "install: install, toast: toast, palette: palOpen, shortcuts: shortcutsOpen, ask: ask, menu: menu," in SHELL
    a = SHELL[SHELL.index("function askEl()"):SHELL.index("// ── One styled picker")]
    for needle in ('role="alertdialog" aria-modal="true"', "e.key === 'Escape'", "e.key === 'Enter' && e.target && e.target.id === 'csAskInput'",
                   "e.key === 'Tab'", "(f ? inp : (opts.danger ? $('csAskCancel') : $('csAskOk'))).focus();",
                   "new RegExp('^(?:' + f.pattern + ')$')", "ASK.back.focus()"):
        assert needle in a, needle
    m = SHELL[SHELL.index("function menu(sel, opts)"):SHELL.index("// ── Command palette (P2.3")]
    for needle in ("setAttribute('role', 'listbox')", "'ArrowDown'", "'Home'", "'End'", "'Escape'", "type to jump",
                   "sel.dispatchEvent(new Event('change', { bubbles: true }))", "sel.style.setProperty('display', 'none', 'important')",
                   "new MutationObserver(sync)"):
        assert needle in m, needle
    assert "Object.defineProperty(sel" not in m, "no property of the page's select is overridden"
    esc = SHELL[SHELL.index("if (e.key === 'Escape') {\n      if (ASK.el"):]
    assert "if (MENU.open) { MENU.open.close(true);" in esc[:400]


def test_chat_uses_them():
    chat = PAGES["codec_chat.html"]
    for sel in ("modelSelect", "researchModelSelect", "imageSize", "savedAgentSelect"):
        assert f"['{sel}'," in chat, sel
    assert "CodecShell.menu(s,{className:p[1]})" in chat
    assert "var node=(sel.__csMenu&&sel.__csMenu.wrap)||sel;" in chat, "the model pill moves with its button"
    assert "var _mnode=(_msel.__csMenu&&_msel.__csMenu.wrap)||_msel;" in chat
    assert "await CodecShell.ask({title:'Approve this plan?'" in _fn(chat, "async function approveAgentInChat")
    reject = _fn(chat, "async function rejectAgentInChat")
    assert "await CodecShell.ask({title:'Reject this plan?'" in reject and "input:{label:'Reason (optional)'" in reject
    assert "await CodecShell.ask({title:'Stop this agent?'" in _fn(chat, "async function abortAgentInChat")
    sched = _fn(chat, "function showSchedulePanel()")
    assert "CodecShell.schedule.open({kind:'crew',crew:crew" in sched
    assert '<input type="checkbox" role="switch" id="imageTransparent">' in chat and 'class="img-switch"' in chat
    assert 'onclick="_stepMaxIter(-1)" aria-label="Fewer steps"' in chat and 'onclick="_stepMaxIter(1)" aria-label="More steps"' in chat
    assert "Math.max(1,Math.min(20," in _fn(chat, "function _stepMaxIter(d)")


def test_the_other_pages_use_them():
    home = PAGES["codec_dashboard.html"]
    for head in ("async function disableTotp()", "async function enableTotp()"):
        body = _fn(home, head)
        assert "await CodecShell.ask(" in body and "pattern:'[0-9]{6}'" in body and "inputmode:'numeric'" in body, head
    assert "await CodecShell.ask({ title: 'Reset this prompt?'" in _fn(home, "async function resetPrompt(key)")
    assert "await CodecShell.ask({ title: 'Reset these triggers?'" in _fn(home, "async function resetSkillTriggers(name)")
    assert "showToast('Triggers saved. Restart CODEC (F13 off/on) to apply.')" in home
    cortex = PAGES["codec_cortex.html"]
    assert "await CodecShell.ask({ title: 'Restart ' + name + '?'" in _fn(cortex, "async function restartService(id)")
    assert "_toast('Restarted ' + data.pm2_name)" in cortex
    tasks = PAGES["codec_tasks.html"]
    assert "await CodecShell.ask({ title: 'Delete this schedule?'" in _fn(tasks, "async function deleteSchedule(id)")
    vibe = PAGES["codec_vibe.html"]
    assert "await CodecShell.ask({ title: 'Delete this project?'" in _fn(vibe, "async function delSes(sid)")
    shot = _fn(vibe, "function takeScreenshot()")
    assert "img.src='/api/screenshot?_t='+Date.now();" in shot and "method" not in shot and "_toast('Screenshot taken')" in shot


def test_the_schedule_sheet_takes_a_crew():
    s = SHELL[SHELL.index("function schedOpen(opts)"):SHELL.index("// ── One modal for questions")]
    assert "opts.kind === 'crew' && opts.crew" in s and "'Schedule ' + SCHED.crew.label" in s
    assert "{ kind: 'crew', crew: SCHED.crew.name, topic: prompt, label: SCHED.crew.label, when:" in s


_JS_HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><html><head></head><body><script src="/static/codec-shell.js" data-page="tasks" data-title="Tasks"></script>' +
  '<main class="cs-main"><select id="s" title="Pick"><option value="a">Apple</option><option value="b">Banana</option>' +
  '<option value="c" disabled>Cherry</option><option value="d">Date</option></select></main></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/tasks' });
const w = dom.window;
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {} });
w.fetch = () => Promise.resolve({ ok: false, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
w.eval(fs.readFileSync(process.argv[1], 'utf8'));
const key = (el, k) => el.dispatchEvent(new w.KeyboardEvent('keydown', { key: k, bubbles: true }));
(async () => {
  const S = w.CodecShell, out = {}, d = w.document;
  let p = S.ask({ title: 'Delete?', danger: true, confirm: 'Delete' });
  out.dangerFocus = d.activeElement.id; d.getElementById('csAskOk').click(); out.yes = await p;
  p = S.ask({ title: 'Stop?' }); d.getElementById('csAskCancel').click(); out.no = await p;
  p = S.ask({ title: 'x' }); key(d, 'Escape'); out.esc = await p;
  p = S.ask({ title: 'Code', input: { pattern: '[0-9]{6}', required: true, patternText: 'Six digits.' } });
  const inp = d.getElementById('csAskInput'); inp.value = '12'; d.getElementById('csAskOk').click();
  out.bad = d.getElementById('csAskError').textContent; out.stillOpen = !d.getElementById('csAsk').hidden;
  inp.value = '123456'; key(inp, 'Enter'); out.code = await p;
  p = S.ask({ title: 'Why?', input: {} }); d.getElementById('csAskCancel').click(); out.cancelled = await p;
  const sel = d.getElementById('s'); let changes = 0; sel.addEventListener('change', () => changes++);
  const m = S.menu(sel), list = d.getElementById('sBtnList');
  out.label = m.button.textContent; out.selHidden = sel.style.getPropertyValue('display');
  key(m.button, 'ArrowDown'); out.open = !list.hidden;
  key(list, 'ArrowDown'); key(list, 'ArrowDown'); key(list, 'Enter');
  out.picked = [sel.value, m.button.textContent, changes, list.hidden];
  m.button.click(); key(list, 'Escape'); out.escClosed = list.hidden && changes === 1;
  sel.value = 'a'; await new Promise(r => setTimeout(r, 700)); out.codeSet = m.button.textContent;
  console.log(JSON.stringify(out)); process.exit(0);
})().catch(e => { console.log('ERR ' + e.message); process.exit(1); });
setTimeout(() => { console.log('TIMEOUT'); process.exit(2); }, 20000);
"""


def _jsdom_available() -> bool:
    if shutil.which("node") is None:
        return False
    r = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True, cwd=REPO, env=os.environ.copy(), timeout=20)
    return r.returncode == 0


@pytest.mark.skipif(not _jsdom_available(), reason="node with jsdom not resolvable (set NODE_PATH)")
def test_ask_and_menu_behave_under_jsdom():
    out = subprocess.run(["node", "-e", _JS_HARNESS, str(REPO / "static" / "codec-shell.js")], capture_output=True,
                         text=True, cwd=REPO, env=os.environ.copy(), timeout=60)
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("{") or ln.startswith("ERR") or ln == "TIMEOUT"][-1]
    d = json.loads(line)
    assert (d["dangerFocus"], d["yes"], d["no"], d["esc"]) == ("csAskCancel", True, False, False)
    assert d["bad"] == "Six digits." and d["stillOpen"] is True and d["code"] == "123456" and d["cancelled"] is None
    assert d["label"] == "Apple" and d["selHidden"] == "none" and d["open"] is True
    assert d["picked"] == ["d", "Date", 1, True], "Down, Down skips the disabled Cherry; Enter picks Date"
    assert d["escClosed"] is True and d["codeSet"] == "Apple"
