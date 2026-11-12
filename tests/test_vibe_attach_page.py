"""Vibe page: attaching an existing folder (P-050, docs/VIBE-ATTACH-FOLDER-DESIGN.md).

The page's own functions run in Node: the pure ones taken out of static/vibe-projects.js by name, and the whole
script in a small fake DOM with a fake editor and a fake fetch. Then the wiring in codec_vibe.html, as text.
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
PROJ_JS = REPO / "static" / "vibe-projects.js"
PROJ = PROJ_JS.read_text(encoding="utf-8")

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def fn(name: str) -> str:
    """One function of the projects script (inside its wrapper, two spaces in)."""
    start = PROJ.index(f"\n  function {name}(") + 1
    return PROJ[start:PROJ.index("\n  }\n", start) + 5]


def page_fn(head: str) -> str:
    body = PAGE[PAGE.index(head):]
    return body[:body.index("\n}\n")]


def _node(script: str):
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


def pure(names, expr):
    return _node("\n".join(fn(n) for n in names) + f"\nconsole.log(JSON.stringify({expr}));")


# ── the plain functions ──

@node
def test_attach_takes_the_typed_path_and_the_notes_box_is_off_unless_ticked():
    got = pure(["attachRequest"], """[attachRequest('  ~/Projects/my app  ', false), attachRequest('/Users/me/Projects/x', true),
      attachRequest('~/Projects/x', 'on'), attachRequest('', true), attachRequest('Projects/x', false), attachRequest(null)]""")
    assert got[0] == {"body": {"path": "~/Projects/my app", "folder_notes": False}}
    assert got[1] == {"body": {"path": "/Users/me/Projects/x", "folder_notes": True}}
    assert got[2]["body"]["folder_notes"] is False, "only a ticked box (true) turns the folder's notes on"
    assert "Type the folder" in got[3]["error"] and "full path" in got[4]["error"] and "error" in got[5]


@node
def test_the_banner_says_attached_read_only_or_missing():
    got = pure(["attachedState"], """[attachedState(null),
      attachedState({ path: '/Users/me/Projects/app', folder_notes: false, read_only: null, missing: null }),
      attachedState({ path: '/Users/me/Projects/app', folder_notes: true, read_only: null, missing: null }),
      attachedState({ path: '/Users/me/Projects/app', read_only: 'This folder has more than 2,000 files.', missing: null }),
      attachedState({ path: '/Users/me/Projects/app', read_only: null, missing: 'The attached folder is missing: /x.' })]""")
    assert got[0] is None
    assert got[1] == {"kind": "attached", "locked": False, "text": "Attached folder: /Users/me/Projects/app"}
    assert "AGENTS.md is used as notes" in got[2]["text"]
    assert got[3]["kind"] == "readonly" and got[3]["locked"] and "2,000 files" in got[3]["text"]
    assert got[4] == {"kind": "missing", "locked": True, "text": "The attached folder is missing: /x."}


@node
def test_a_whole_folder_restore_asks_with_how_many_other_files_change():
    got = pure(["restoreAsk"], """[restoreAsk({ id: 'c0001', label: 'Start' }, null),
      restoreAsk({ id: 'c0001', label: 'Start' }, { attached: false }),
      restoreAsk({ id: 'c0001', label: 'Start' }, { attached: true, files: 4, other: 2 }),
      restoreAsk({ id: 'c0001', label: 'Start' }, { attached: true, files: 1, other: 0 })]""")
    before = {"title": "Restore this version?", "confirm": "Restore",
              "message": 'Your files go back to "Start". The files as they are now are saved as a version first, so nothing is lost.'}
    assert got[0] == before and got[1] == before, "a created project asks exactly as before"
    assert got[2]["title"] == "Restore the whole folder?" and got[2]["danger"] is True
    assert "4 files change" in got[2]["message"] and "2 of them are files Vibe's AI did not change" in got[2]["message"]
    assert "1 file changes" in got[3]["message"] and "did not change" not in got[3]["message"] and got[3]["danger"] is False


@node
def test_undo_and_revert_send_scope_ai_for_an_attached_folder_only_and_detach_says_the_folder_is_kept():
    got = pure(["restoreBody", "detachAsk"], "[restoreBody('c0002', true), restoreBody('c0002', false), detachAsk()]")
    assert got[0] == {"checkpoint": "c0002", "scope": "ai"} and got[1] == {"checkpoint": "c0002"}
    assert got[2]["confirm"] == "Detach" and "Nothing in the folder is moved, changed or deleted" in got[2]["message"]


@node
def test_the_files_list_filters_and_caps_and_the_model_gets_a_capped_name_line():
    got = pure(["filterList", "namesLine"], """[filterList([{path: 'src/App.js'}, {path: 'README.md'}, 'src/b.js'], 'src', 10),
      filterList(['a', 'b', 'c'], '', 2), namesLine(['a', 'b', 'c', 'd'], 2)]""")
    assert got[0] == {"shown": ["src/App.js", "src/b.js"], "more": 0}
    assert got[1] == {"shown": ["a", "b"], "more": 1}
    assert got[2] == "a, b, ... and 2 more"


# ── the whole script in a fake DOM ──

HARNESS = r"""
function El(id, tag) { this.id = id; this.tagName = tag || 'div'; this.children = []; this.style = {}; this.className = '';
  this._t = ''; this.listeners = {}; this.attrs = {}; this.hidden = false; this.value = ''; this.checked = false; this.disabled = false;
  var self = this; this.classList = { set: {}, add: function (c) { self.classList.set[c] = 1; }, remove: function (c) { delete self.classList.set[c]; },
    contains: function (c) { return !!self.classList.set[c]; }, toggle: function (c, on) { if (on) self.classList.set[c] = 1; else delete self.classList.set[c]; } }; }
El.prototype.appendChild = function (c) { if (c.parent) c.parent.removeChild(c); c.parent = this; this.children.push(c); return c; };
El.prototype.removeChild = function (c) { this.children.splice(this.children.indexOf(c), 1); c.parent = null; };
El.prototype.insertBefore = function (c, ref) { c.parent = this; this.children.splice(Math.max(0, this.children.indexOf(ref)), 0, c); return c; };
El.prototype.addEventListener = function (t, f) { this.listeners[t] = f; };
El.prototype.setAttribute = function (k, v) { this.attrs[k] = v; };
El.prototype.remove = function () { if (this.parent) this.parent.removeChild(this); };
El.prototype.focus = function () { globalThis.focused = this.id; };
El.prototype.scrollIntoView = function () {};
Object.defineProperty(El.prototype, 'firstChild', { get: function () { return this.children[0] || null; } });
Object.defineProperty(El.prototype, 'textContent', {
  get: function () { return this._t + this.children.map(function (c) { return c.textContent; }).join(''); },
  set: function (v) { this._t = String(v); this.children = []; } });
Object.defineProperty(El.prototype, 'innerHTML', { set: function () { globalThis.usedInnerHTML = true; }, get: function () { return ''; } });
function all(n, out) { out = out || []; out.push(n); n.children.forEach(function (c) { all(c, out); }); return out; }
function find(n, pred) { return all(n).filter(pred)[0] || null; }
var byId = {};
function $(id) { return byId[id] || (byId[id] = new El(id)); }
['attachBox', 'fpick'].forEach(function (id) { $(id).hidden = true; });
globalThis.window = globalThis;
globalThis.addEventListener = function () {};
globalThis.location = { hostname: 'localhost', search: '' };
globalThis.document = { createElement: function (t) { return new El(null, t); }, getElementById: $,
  addEventListener: function () {}, querySelector: function () { return null; }, querySelectorAll: function () { return []; } };
function Model(text) { this.text = text; this.disposed = false; }
Model.prototype.getValue = function () { return this.text; };
Model.prototype.onDidChangeContent = function () {};
Model.prototype.dispose = function () { this.disposed = true; };
Model.prototype.isDisposed = function () { return this.disposed; };
Model.prototype.pushStackElement = function () {};
Model.prototype.getFullModelRange = function () { return {}; };
Model.prototype.pushEditOperations = function (a, ops) { this.text = ops[0].text; };
window.monaco = { editor: { createModel: function (t) { return new Model(t); }, setModelLanguage: function () {} } };
globalThis.opts = [];
window.ed = { model: null, getModel: function () { return this.model; }, setModel: function (m) { this.model = m; },
  getValue: function () { return this.model ? this.model.getValue() : ''; }, updateOptions: function (o) { opts.push(o); } };
window.VibeFiles = { monacoLang: function () { return 'plaintext'; }, langOfPath: function () { return ''; } };
globalThis.asked = [];
window.CodecShell = { ask: function (q) { asked.push(q); return Promise.resolve(true); }, toast: function () {} };
globalThis.calls = [];
globalThis.routes = [];
globalThis.fetch = function (url, o) {
  calls.push([(o && o.method) || 'GET', url, o && o.body ? JSON.parse(o.body) : null]);
  var reply = {};
  for (var i = 0; i < routes.length; i++) if (url.indexOf(routes[i][0]) !== -1) { reply = routes[i][1]; break; }
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(JSON.parse(JSON.stringify(reply))); } });
};
function later(f) { setTimeout(f, 30); }
require(PROJ);
"""

INFO = {"id": "app-abc123", "title": "app", "can_undo": False, "checkpoint_count": 0,
        "files": [{"path": "README.md", "size": 6}, {"path": "index.html", "size": 12}, {"path": "src/app.js", "size": 11}],
        "attached": {"path": "/Users/me/Projects/app", "folder_notes": False, "read_only": None, "missing": None}}


def dom(body: str, routes: list) -> dict:
    script = (f"var PROJ = {json.dumps(str(PROJ_JS))};\n" + HARNESS +
              f"routes.push.apply(routes, {json.dumps(routes)});\n" + body)
    return _node(script)


def file_route(content="x\n"):
    return ["/file?path=", {"content": content, "size": len(content)}]


@node
def test_opening_an_attached_folder_loads_one_file_and_the_files_panel_opens_any_other():
    out = dom("""
VibeProj.open('app-abc123').then(function () { later(function () {
  var first = calls.filter(function (c) { return c[1].indexOf('/file?path=') !== -1; }).map(function (c) { return c[1]; });
  var banner = { hidden: $('attBanner').hidden, cls: $('attBanner').className, text: $('attText').textContent };
  var filesBtn = $('filesBtn').hidden;
  VibeProj.filesDrawer();
  var rows = all($('fdList')).filter(function (n) { return n.tagName === 'button'; }).map(function (n) { return n._t; });
  $('fdQ').value = 'src';
  VibeProj.filterFiles();
  var filtered = all($('fdList')).filter(function (n) { return n.tagName === 'button'; });
  filtered[0].listeners.click();
  later(function () {
    var after = calls.filter(function (c) { return c[1].indexOf('/file?path=') !== -1; }).map(function (c) { return c[1]; });
    console.log(JSON.stringify({ first: first, banner: banner, filesBtn: filesBtn, rows: rows, filtered: filtered.length,
      after: after, panel: $('fpick').hidden, ctx: VibeProj.contextBlock(''), html: !!globalThis.usedInnerHTML }));
  });
}); });
""", [["/api/vibe/projects/app-abc123/file?path=", {"content": "x\n"}], ["/api/vibe/projects/app-abc123/agent/left-off", {}],
      ["/live?", {"state": "idle"}], ["/api/vibe/projects/app-abc123", INFO]])
    assert out["first"] == ["/api/vibe/projects/app-abc123/file?path=index.html"], "one file at open, not the whole folder"
    assert out["banner"] == {"hidden": False, "cls": "abn", "text": "Attached folder: /Users/me/Projects/app"}
    assert out["filesBtn"] is False and out["rows"] == ["index.html", "README.md", "src/app.js"]
    assert out["filtered"] == 1 and out["after"][-1] == "/api/vibe/projects/app-abc123/file?path=src%2Fapp.js"
    assert out["panel"] is True, "picking a file closes the list"
    assert "All files: index.html, README.md, src/app.js" in out["ctx"] and "Open in the editor (shown below)" in out["ctx"]
    assert out["html"] is False


@node
@pytest.mark.parametrize("state", ["read_only", "missing"])
def test_a_read_only_or_missing_folder_locks_the_editor_and_shows_the_warning(state):
    info = json.loads(json.dumps(INFO))
    info["attached"][state] = "This folder has more than 2,000 files." if state == "read_only" else "The attached folder is missing: /x."
    if state == "missing":
        info["files"] = []
    out = dom("""
VibeProj.open('app-abc123').then(function () { later(function () {
  var writes = calls.filter(function (c) { return c[0] !== 'GET'; });
  VibeProj.flush().then(function () {
    console.log(JSON.stringify({ cls: $('attBanner').className, text: $('attText').textContent, locked: opts.some(function (o) { return o.readOnly === true; }),
      writes: writes.length + calls.filter(function (c) { return c[0] !== 'GET'; }).length }));
  });
}); });
""", [file_route(), ["/agent/left-off", {}], ["/live?", {}], ["/api/vibe/projects/app-abc123", info]])
    assert out["cls"] == "abn abn-warn" and out["locked"] is True and out["writes"] == 0
    assert ("2,000 files" if state == "read_only" else "missing") in out["text"]


@node
def test_a_whole_folder_restore_gets_the_counts_first_and_asks_with_them():
    cps = [{"id": "c0002", "kind": "ai", "label": "AI", "parent": "c0001", "files_count": 3, "stat": {}},
           {"id": "c0001", "kind": "start", "label": "Start", "parent": None, "files_count": 3, "stat": {}}]
    out = dom("""
VibeProj.open('app-abc123').then(function () { return VibeProj.versions(); }).then(function () { later(function () {
  var head = all($('vdList')).filter(function (n) { return n.className === 'vrow-h'; })[2];   // Now, c0002, c0001
  head.listeners.click();
  later(function () {
    var btn = find($('vdList'), function (n) { return n._t === 'Restore this version'; });
    btn.listeners.click();
    later(function () {
      console.log(JSON.stringify({ asked: asked, calls: calls.filter(function (c) { return c[1].indexOf('/restore') !== -1; }) }));
    });
  });
}); });
""", [file_route(), ["/agent/left-off", {}], ["/live?", {}], ["/checkpoints?limit=200", cps], ["/unversioned", {"count": 0}],
      ["/diff?", {"files": []}], ["/restore/preview", {"checkpoint": "c0001", "files": 4, "other": 2, "attached": True}],
      ["/restore", {"checkpoint": {"id": "c0004"}}], ["/api/vibe/projects/app-abc123", INFO]])
    assert out["calls"][0] == ["GET", "/api/vibe/projects/app-abc123/restore/preview?checkpoint=c0001", None]
    assert out["calls"][1] == ["POST", "/api/vibe/projects/app-abc123/restore", {"checkpoint": "c0001"}], "the Versions drawer restores the whole folder"
    assert len(out["asked"]) == 1 and "4 files change" in out["asked"][0]["message"] and "2 of them" in out["asked"][0]["message"]


@node
def test_an_agent_cards_undo_sends_scope_ai_in_an_attached_folder():
    out = dom("""
VibeProj.open('app-abc123').then(function () { return VibeProj.restoreId('c0002'); }).then(function () {
  console.log(JSON.stringify(calls.filter(function (c) { return c[0] === 'POST'; })));
});
""", [file_route(), ["/agent/left-off", {}], ["/live?", {}], ["/restore", {}], ["/api/vibe/projects/app-abc123", INFO]])
    assert out == [["POST", "/api/vibe/projects/app-abc123/restore", {"checkpoint": "c0002", "scope": "ai"}]]


@node
def test_attach_posts_the_typed_path_and_the_tick_box_then_opens_the_folder():
    out = dom("""
globalThis.opened = [];
window.openAttached = function (info) { opened.push(info.id); return Promise.resolve(); };
VibeProj.attachDialog();
var open1 = $('attachBox').hidden, box = $('adNotes').checked;
$('adPath').value = '  ~/Projects/app ';
$('adNotes').checked = true;
VibeProj.attachGo().then(function () {
  $('adPath').value = 'Projects/app';
  VibeProj.attachDialog();
  VibeProj.attachDialog();
  return VibeProj.attachGo();
}).then(function () {
  console.log(JSON.stringify({ open1: open1, box: box, posts: calls.filter(function (c) { return c[0] === 'POST'; }), opened: opened,
    err: $('adErr').textContent, focused: globalThis.focused, closed: $('attachBox').hidden }));
});
""", [["/api/vibe/projects/attach", INFO]])
    assert out["open1"] is False and out["box"] is False, "the form opens with the notes box unticked"
    assert out["posts"] == [["POST", "/api/vibe/projects/attach", {"path": "~/Projects/app", "folder_notes": True}]]
    assert out["opened"] == ["app-abc123"] and out["focused"] == "adPath"
    assert "full path" in out["err"], "a path that is not full is caught before anything is sent"


# ── the wiring, as text ──

def test_the_attach_form_files_list_banner_and_run_note_are_on_the_page():
    form = PAGE[PAGE.index('id="attachBtn"') - 200:PAGE.index('id="adGo"') + 200]
    assert 'onclick="VibeProj.attachDialog()"' in form and ">+ Attach a folder</button>" in form and "hidden>" in form
    assert '<input id="adPath" class="rd-in" type="text"' in form and 'maxlength="1000"' in form
    assert '<input type="checkbox" id="adNotes">' in form, "unticked by default (R-16)"
    assert 'id="attBanner" role="status" hidden' in PAGE and 'onclick="detachCurrent()"' in PAGE
    assert 'id="filesBtn"' in PAGE and 'id="fpick" hidden' in PAGE and 'oninput="VibeProj.filterFiles()"' in PAGE
    assert 'id="rdAttNote" hidden' in PAGE and "outside any sandbox" in PAGE
    assert ".att-box[hidden],.fpick[hidden]{display:none}" in PAGE
    assert PAGE.count('data-dialog="') == 4, "no new modal: the form sits in the Projects drawer, the list above the editor"


def test_deleting_an_attached_folder_detaches_it_first_and_only_then_drops_the_chat():
    dl = page_fn("async function delSes(sid, pid) {")
    assert dl.index("_attachedProject(pid)") < dl.index("CodecShell.ask"), "an attached folder never sees the Delete question"
    det = page_fn("async function detachSes(sid, pid) {")
    assert "CodecShell.ask(VibeProj.detachAsk())" in det
    assert det.index("'/api/vibe/projects/'") < det.index("'/api/vibe/session/'"), "a 403 or 409 keeps the chat too"
    assert "_toast('Not detached: '" in det and "return;" in det[det.index("_toast('Not detached"):]
    oa = page_fn("function openAttached(info) {")
    assert "info.already_attached" in oa and "VibeProj.open(info.id)" in oa and "doSaveSes()" in oa


def test_the_new_page_code_writes_text_only_with_textcontent():
    for name in ("attachDialog", "closeAttach", "attachGo", "filesDrawer", "renderFiles", "closeFiles", "showAttached",
                 "takeAttached", "restoreTo"):
        src = fn(name)
        assert "innerHTML" not in src and "insertAdjacentHTML" not in src, name
    assert "t.textContent = st ? st.text : '';" in fn("showAttached")
    assert re.search(r"var ab = \$\('attachBtn'\); if \(ab\) ab\.hidden = !ON_MAC;", PROJ), "offered at this Mac only"


@node
def test_the_sidebar_x_of_an_attached_folder_says_detach_and_a_created_projects_keeps_delete():
    src = page_fn("function _labelAttached(list) {") + "\n}\n"
    out = _node("""
function B(pid) { this.a = { 'data-pid': pid, 'aria-label': 'Delete project' }; this.title = 'Delete project'; }
B.prototype.getAttribute = function (k) { return this.a[k]; };
B.prototype.setAttribute = function (k, v) { this.a[k] = v; };
var bs = [new B('app-abc123'), new B('made-def456'), new B('')];
globalThis.window = { VibeProj: {} };
globalThis.fetch = function (u) { globalThis.asked = u; return Promise.resolve({ ok: true, json: function () {
  return Promise.resolve([{ id: 'app-abc123', attached: true }, { id: 'made-def456' }]); } }); };
""" + src + """
_labelAttached({ querySelectorAll: function () { return bs; } });
setTimeout(function () { console.log(JSON.stringify({ u: asked, b: bs.map(function (b) { return [b.title, b.a['aria-label']]; }) })); }, 20);
""")
    assert out["u"] == "/api/vibe/projects"
    assert out["b"] == [["Detach folder", "Detach folder"], ["Delete project", "Delete project"], ["Delete project", "Delete project"]]
    assert "data-pid=\"' + esc(s.project_id || '') + '\"" in PAGE and "_labelAttached(el);" in page_fn("function loadSes() {")
