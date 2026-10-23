"""Notes on the page (static/notes.js, codec_tasks.html, codec_chat.html, docs/NOTES-DESIGN.md).

The top half of notes.js is plain functions, so Node runs them directly. The panel is driven in Node too,
on a small stand-in for the page (elements, a fake fetch, a fake dialog), so the flows are checked without a
browser: the tab stays hidden until the routes answer, notes go in as text and never as HTML, saving and
deleting call the right routes, and unsaved words are not thrown away silently. The rest checks the wiring.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
JS = REPO / "static" / "notes.js"
SRC = JS.read_text(encoding="utf-8")
TASKS = (REPO / "codec_tasks.html").read_text(encoding="utf-8")
CHAT = (REPO / "codec_chat.html").read_text(encoding="utf-8")
SHELL = (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")
STAMP = (REPO / "tools" / "stamp_static.py").read_text(encoding="utf-8")

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def run(expr: str, setup: str = ""):
    script = f"const N = require({json.dumps(str(JS))}); {setup} console.log(JSON.stringify({expr}));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


# ── plain functions ──────────────────────────────────────────────────────────

@node
def test_titles_and_labels():
    assert run("[N.rowTitle({title:'Lunch'}), N.rowTitle({title:''}), N.rowTitle(null), N.sourceLabel('chat'), N.sourceLabel('manual')]") \
        == ["Lunch", "Untitled", "Untitled", "Chat", ""]
    assert run("[N.countLabel(0,0,''), N.countLabel(1,1,''), N.countLabel(5,5,''), N.countLabel(5,2,'fennel'), N.countLabel(5,5,'  ')]") \
        == ["", "1 note", "5 notes", "2 of 5 notes", "5 notes"]
    assert "Press New note" in run("N.emptyText('')") and "all of those words" in run("N.emptyText('x')")


@node
def test_dates_are_by_calendar_day_in_local_time():
    setup = ("const at=(d,h,m)=>new Date(d[0],d[1],d[2],h,m).toISOString(); const now=new Date(2026,9,21,15,0);")
    assert run("N.dateLabel(at([2026,9,21],9,5), now)", setup) == "Today 09:05"
    assert run("N.dateLabel(at([2026,9,20],23,59), now)", setup) == "Yesterday 23:59"
    assert run("N.dateLabel(at([2026,9,12],8,0), now)", setup) == "12 Oct"
    assert run("N.dateLabel(at([2025,0,3],8,0), now)", setup) == "3 Jan 2025"
    assert run("N.dateLabel('not a date', now)", setup) == ""


@node
def test_list_url_and_dirty_check():
    assert run("[N.listUrl('', 0), N.listUrl('  fennel salad ', 50), N.listUrl('a&b=c', 1)]") \
        == ["/api/notes?limit=200", "/api/notes?limit=50&q=fennel%20salad", "/api/notes?limit=1&q=a%26b%3Dc"]
    assert run("[N.isDirty({title:'a',body:'b'},'a','b'), N.isDirty({title:'a',body:'b'},'a','c'), N.isDirty({title:'a',body:'b'},'','b'), "
               "N.isDirty({id:null,title:'',body:''},'',''), N.isDirty(null,'x','')]") == [False, True, True, False, True]
    assert run("[N.errorText(new Error('Too long')), N.errorText({}), N.errorText(null)]") \
        == ["Too long", "Something went wrong. Try again.", "Something went wrong. Try again."]


# ── the panel, on a stand-in for the page ────────────────────────────────────

HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[1], 'utf8');
const tick = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };

function el() {
  let text = '';
  const e = { children: [], hidden: false, disabled: false, value: '', dataset: {}, attrs: {}, listeners: {}, cls: new Set(), focused: false };
  Object.defineProperty(e, 'textContent', { get: () => text, set: (v) => { text = String(v); e.children = []; } });
  Object.defineProperty(e, 'innerHTML', { get: () => '', set: () => { globalThis.__html++; } });
  e.classList = { add: (c) => e.cls.add(c), remove: (c) => e.cls.delete(c), contains: (c) => e.cls.has(c),
    toggle: (c, on) => { const want = on === undefined ? !e.cls.has(c) : !!on; if (want) e.cls.add(c); else e.cls.delete(c); return want; } };
  e.appendChild = (c) => { e.children.push(c); return c; };
  e.setAttribute = (k, v) => { e.attrs[k] = v; };
  e.addEventListener = (t, f) => { (e.listeners[t] = e.listeners[t] || []).push(f); };
  e.focus = () => { e.focused = true; };
  e.select = () => {};
  e.fire = async (t, ev) => { for (const f of (e.listeners[t] || [])) await f.call(e, ev || {}); await tick(); };
  return e;
}

const NOTE = (id, title, body, extra) => Object.assign({ id, title, body, preview: body, source: 'manual', created: '2026-10-21T08:00:00.000+00:00', updated: '2026-10-21T08:00:00.000+00:00', chars: body.length }, extra || {});

async function boot(handler, opts) {
  opts = opts || {};
  globalThis.__html = 0;
  const ids = ['panel-notes', 'tab-notes', 'notesOff', 'notesWrap', 'notesEdit', 'notesHint', 'notesTitle', 'notesBody', 'notesSave',
    'notesCopy', 'notesDelete', 'notesBack', 'notesNew', 'notesSearch', 'notesItems', 'notesEmpty', 'notesCount', 'notesStatus'];
  const els = {};
  ids.forEach((i) => { els[i] = el(); });
  ['tab-notes', 'notesOff', 'notesWrap', 'notesEdit'].forEach((i) => { els[i].hidden = true; });
  const docListeners = {};
  const calls = [], toasts = [], asks = [], timers = [];
  const state = { ask: true };
  const sandbox = {
    document: { readyState: 'complete', cookie: 'a=b; codec_csrf=tok123', getElementById: (i) => els[i] || null,
      createElement: () => el(), addEventListener: (t, f) => { (docListeners[t] = docListeners[t] || []).push(f); } },
    fetch: async (url, o) => {
      o = o || {};
      const method = o.method || 'GET';
      calls.push({ method, url, headers: o.headers || {}, body: o.body ? JSON.parse(o.body) : undefined });
      const hit = handler(method, url, o.body ? JSON.parse(o.body) : undefined);
      return { ok: hit.status < 400, status: hit.status, json: async () => hit.body };
    },
    navigator: {}, Date, JSON, Promise, encodeURIComponent, String, Math, isNaN, Error, console,
    setTimeout: (f) => { timers.push(f); return timers.length; }, clearTimeout: () => {},
    CodecShell: { toast: (m) => toasts.push(m), ask: async (o) => { asks.push(o.title); return state.ask; } },
    addEventListener: () => {},
  };
  if (opts.coarse) sandbox.matchMedia = (q) => ({ matches: q.includes('coarse') });
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  await tick();
  return { els, calls, toasts, asks, timers, state, notes: sandbox.CodecNotes, docListeners,
    flush: async () => { while (timers.length) timers.shift()(); await tick(); } };
}

const LIST = [NOTE('aaaaaaaaaaaa', '<img src=x onerror=alert(1)>', 'first body <b>bold</b>', { source: 'chat' }), NOTE('bbbbbbbbbbbb', 'Plain', 'second body')];
const live = (extra) => (method, url, body) => {
  if (extra) { const r = extra(method, url, body); if (r) return r; }
  if (method === 'GET' && url === '/api/notes?limit=1') return { status: 200, body: { notes: [], total: 2, matched: 2 } };
  if (method === 'GET' && url.startsWith('/api/notes?')) {
    const q = decodeURIComponent((url.split('&q=')[1] || '')).toLowerCase();
    const rows = LIST.filter((n) => !q || (n.title + n.preview).toLowerCase().includes(q));
    return { status: 200, body: { notes: rows, total: 2, matched: rows.length } };
  }
  const m = url.match(/^\/api\/notes\/([a-z]+)$/);
  if (m && method === 'GET') { const n = LIST.find((x) => x.id === m[1]); return n ? { status: 200, body: n } : { status: 404, body: { error: 'That note was not found.' } }; }
  if (method === 'POST' && url === '/api/notes') return { status: 200, body: NOTE('cccccccccccc', body.title || 'Made title', body.body) };
  if (m && method === 'PUT') return { status: 200, body: NOTE(m[1], body.title || 'Made title', body.body) };
  if (m && method === 'DELETE') return { status: 200, body: { ok: true } };
  return { status: 404, body: { detail: 'Not Found' } };
};
const rows = (b) => b.els.notesItems.children.map((li) => li.children[0]);
const out = {};

(async () => {
  // 1. before the restart: every notes route answers 404, so the tab stays hidden and the panel says why
  let route = false;
  let b = await boot((m, u, body) => (route ? live()(m, u, body) : { status: 404, body: { detail: 'Not Found' } }));
  out.absent = { tabHidden: b.els['tab-notes'].hidden, ready: b.notes.ready() };
  await b.notes.show();
  out.absent.offShown = !b.els.notesOff.hidden;
  out.absent.wrapHidden = b.els.notesWrap.hidden;
  route = true;                                   // the dashboard was restarted: the next visit tries again
  await b.notes.show(); await tick();
  out.absent.recovered = { tabHidden: b.els['tab-notes'].hidden, ready: b.notes.ready(), wrapShown: !b.els.notesWrap.hidden, rows: rows(b).length };

  // 2. with the routes: the tab shows, notes are drawn as text, never as HTML
  b = await boot(live());
  await b.notes.show();
  const first = rows(b)[0];
  out.present = { tabHidden: b.els['tab-notes'].hidden, rows: rows(b).length, firstTitle: first.children[0].textContent,
    firstPreview: first.children[1].textContent, firstMeta: first.children[2].children.map((c) => c.textContent).slice(1),
    count: b.els.notesCount.textContent, html: globalThis.__html, emptyHidden: b.els.notesEmpty.hidden,
    listCalls: b.calls.filter((c) => c.url.startsWith('/api/notes?limit=200')).length };
  await b.notes.show();                           // the hash handler echoes show(): no second load
  out.present.echoCalls = b.calls.filter((c) => c.url.startsWith('/api/notes?limit=200')).length;

  // two show() calls at once (the tab opens and the hash handler echoes it) fetch the list once
  b = await boot(live());
  await Promise.all([b.notes.show(), b.notes.show()]);
  out.concurrent = b.calls.filter((c) => c.url.startsWith('/api/notes?limit=200')).length;

  // on a phone, opening a note to read it does not raise the keyboard; a new note does
  b = await boot(live(), { coarse: true });
  await b.notes.show();
  await rows(b)[0].fire('click');
  out.coarse = { openedFocused: b.els.notesBody.focused };
  await b.els.notesNew.fire('click');
  out.coarse.newFocused = b.els.notesBody.focused;
  b = await boot(live());
  await b.notes.show();
  await rows(b)[0].fire('click');
  out.coarse.mouseOpenedFocused = b.els.notesBody.focused;

  // 3. search waits a moment, then asks the server; an empty answer says so
  b = await boot(live());
  await b.notes.show();
  b.els.notesSearch.value = ' plain ';
  await b.els.notesSearch.fire('input');
  out.search = { before: b.calls.filter((c) => c.url.includes('&q=')).length };
  await b.flush();
  out.search.url = b.calls.filter((c) => c.url.includes('&q=')).map((c) => c.url);
  out.search.rows = rows(b).map((r) => r.children[0].textContent);
  b.els.notesSearch.value = 'zzz nothing';
  await b.els.notesSearch.fire('input'); await b.flush();
  out.search.empty = { rows: rows(b).length, text: b.els.notesEmpty.textContent, shown: !b.els.notesEmpty.hidden, count: b.els.notesCount.textContent };

  // 4. a new note: blank text is not sent; real text is posted with the CSRF header and the list reloads
  b = await boot(live());
  await b.notes.show();
  await b.els.notesNew.fire('click');
  out.newNote = { editorShown: !b.els.notesEdit.hidden, hintHidden: b.els.notesHint.hidden, editing: b.els.notesWrap.cls.has('editing'),
    focused: b.els.notesBody.focused, deleteDisabled: b.els.notesDelete.disabled };
  b.els.notesBody.value = '   ';
  await b.els.notesSave.fire('click');
  out.newNote.blankPosts = b.calls.filter((c) => c.method === 'POST').length;
  out.newNote.blankStatus = b.els.notesStatus.textContent;
  b.els.notesBody.value = 'hello world';
  await b.els.notesBody.fire('input');
  out.newNote.unsaved = b.els.notesStatus.textContent;
  await b.els.notesSave.fire('click');
  const post = b.calls.find((c) => c.method === 'POST');
  out.newNote.post = post && { url: post.url, body: post.body, csrf: post.headers['x-csrf-token'], type: post.headers['Content-Type'] };
  out.newNote.after = { status: b.els.notesStatus.textContent, title: b.els.notesTitle.value, deleteDisabled: b.els.notesDelete.disabled,
    saveDisabled: b.els.notesSave.disabled, reloads: b.calls.filter((c) => c.url.startsWith('/api/notes?limit=200')).length };

  // 5. open, edit, save: a PUT to that note; the dirty guard asks before another note replaces the words
  b = await boot(live());
  await b.notes.show();
  await rows(b)[1].fire('click');
  out.edit = { opened: b.els.notesBody.value, title: b.els.notesTitle.value, deleteDisabled: b.els.notesDelete.disabled,
    current: rows(b).map((r) => r.attrs['aria-current'] || null) };
  b.els.notesBody.value = 'second body, edited';
  await b.els.notesBody.fire('input');
  out.edit.unsaved = b.els.notesStatus.textContent;
  b.state.ask = false;
  await rows(b)[0].fire('click');
  out.edit.guard = { asked: b.asks.slice(), otherLoaded: b.calls.some((c) => c.url === '/api/notes/aaaaaaaaaaaa'), body: b.els.notesBody.value };
  b.state.ask = true;
  await rows(b)[0].fire('click');
  out.edit.guard.afterYes = { otherLoaded: b.calls.some((c) => c.url === '/api/notes/aaaaaaaaaaaa'), body: b.els.notesBody.value };
  await rows(b)[1].fire('click');
  b.els.notesBody.value = 'second body, edited again';
  await b.els.notesSave.fire('click');
  const put = b.calls.find((c) => c.method === 'PUT');
  out.edit.put = put && { url: put.url, body: put.body };

  // 6. delete asks first; no is a no-op, yes calls DELETE, closes the editor and tells the owner
  b = await boot(live());
  await b.notes.show();
  await rows(b)[1].fire('click');
  b.state.ask = false;
  await b.els.notesDelete.fire('click');
  out.del = { askedNo: b.asks.slice(), deletesNo: b.calls.filter((c) => c.method === 'DELETE').length, editorStillShown: !b.els.notesEdit.hidden };
  b.state.ask = true;
  await b.els.notesDelete.fire('click');
  out.del.deletesYes = b.calls.filter((c) => c.method === 'DELETE').map((c) => c.url);
  out.del.editorHidden = b.els.notesEdit.hidden;
  out.del.hintShown = !b.els.notesHint.hidden;
  out.del.toasts = b.toasts.slice();

  // 7. Cmd or Ctrl plus S saves, but only with the Notes tab open
  b = await boot(live());
  await b.notes.show();
  b.els['panel-notes'].classList.add('active');
  await b.els.notesNew.fire('click');
  b.els.notesBody.value = 'saved with the keyboard';
  const key = { metaKey: true, ctrlKey: false, key: 'S', preventDefault() { this.stopped = true; } };
  const press = async (ev) => { for (const f of (b.docListeners.keydown || [])) f(ev); await tick(); };
  await press(key);
  out.keys = { posts: b.calls.filter((c) => c.method === 'POST').length, stopped: !!key.stopped };
  b.els['panel-notes'].cls.delete('active');
  b.els.notesBody.value = 'another';
  b.els.notesNew.fire('click');
  await tick();
  const key2 = { metaKey: true, key: 's', preventDefault() { this.stopped = true; } };
  await press(key2);
  out.keys.postsWhenClosed = b.calls.filter((c) => c.method === 'POST').length;
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module")
def panel():
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    res = subprocess.run(["node", "-e", HARNESS, str(JS)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


@node
def test_the_tab_stays_hidden_until_the_routes_answer(panel):
    a = panel["absent"]
    assert a["tabHidden"] is True and a["ready"] is False
    assert a["offShown"] is True and a["wrapHidden"] is True
    assert a["recovered"] == {"tabHidden": False, "ready": True, "wrapShown": True, "rows": 2}


@node
def test_notes_are_drawn_as_text_never_as_html(panel):
    p = panel["present"]
    assert p["tabHidden"] is False and p["rows"] == 2
    assert p["firstTitle"] == "<img src=x onerror=alert(1)>"
    assert p["firstPreview"] == "first body <b>bold</b>"
    assert p["firstMeta"] == ["Chat"]
    assert p["count"] == "2 notes" and p["emptyHidden"] is True
    assert p["html"] == 0
    assert p["listCalls"] == 1 and p["echoCalls"] == 1          # the hash handler's second show() does not reload


@node
def test_the_list_is_fetched_once_for_two_simultaneous_opens_and_a_phone_does_not_pop_the_keyboard(panel):
    assert panel["concurrent"] == 1
    assert panel["coarse"] == {"openedFocused": False, "newFocused": True, "mouseOpenedFocused": True}


@node
def test_search_waits_then_asks_the_server_and_says_when_nothing_matches(panel):
    s = panel["search"]
    assert s["before"] == 0
    assert s["url"] == ["/api/notes?limit=200&q=plain"]
    assert s["rows"] == ["Plain"]
    assert s["empty"] == {"rows": 0, "text": "No note has all of those words.", "shown": True, "count": "0 of 2 notes"}


@node
def test_a_new_note_is_posted_with_the_csrf_header_and_blank_text_is_not_sent(panel):
    n = panel["newNote"]
    assert n["editorShown"] and n["hintHidden"] and n["editing"] and n["focused"] and n["deleteDisabled"]
    assert n["blankPosts"] == 0 and n["blankStatus"] == "Write something first."
    assert n["unsaved"] == "Unsaved changes"
    assert n["post"] == {"url": "/api/notes", "body": {"title": "", "body": "hello world", "source": "manual"},
                         "csrf": "tok123", "type": "application/json"}
    assert n["after"]["status"] == "Saved" and n["after"]["title"] == "Made title"
    assert n["after"]["deleteDisabled"] is False and n["after"]["saveDisabled"] is False
    assert n["after"]["reloads"] >= 2


@node
def test_editing_saves_with_put_and_unsaved_words_are_guarded(panel):
    e = panel["edit"]
    assert e["opened"] == "second body" and e["title"] == "Plain" and e["deleteDisabled"] is False
    assert e["current"] == [None, "true"]
    assert e["unsaved"] == "Unsaved changes"
    assert e["guard"]["asked"] == ["Discard your changes?"]
    assert e["guard"]["otherLoaded"] is False and e["guard"]["body"] == "second body, edited"
    assert e["guard"]["afterYes"] == {"otherLoaded": True, "body": "first body <b>bold</b>"}
    assert e["put"] == {"url": "/api/notes/bbbbbbbbbbbb", "body": {"title": "Plain", "body": "second body, edited again"}}


@node
def test_delete_asks_first_and_then_moves_the_note_away(panel):
    d = panel["del"]
    assert d["askedNo"] == ["Delete this note?"] and d["deletesNo"] == 0 and d["editorStillShown"] is True
    assert d["deletesYes"] == ["/api/notes/bbbbbbbbbbbb"]
    assert d["editorHidden"] is True and d["hintShown"] is True and d["toasts"] == ["Note deleted"]


@node
def test_the_keyboard_shortcut_saves_only_while_the_tab_is_open(panel):
    assert panel["keys"] == {"posts": 1, "stopped": True, "postsWhenClosed": 1}


# ── wiring ───────────────────────────────────────────────────────────────────

def test_the_script_is_stamped_and_loaded_after_the_shell():
    assert '"notes.js"' in STAMP
    m = re.search(r'<script src="/static/notes\.js\?v=([0-9a-f]{8})"></script>', TASKS)
    assert m, "the Tasks page must load notes.js with a version stamp"
    assert TASKS.index("codec-shell.js") < TASKS.index("notes.js")


def test_every_element_the_script_asks_for_is_in_the_page():
    wanted = set(re.findall(r"\$\('([A-Za-z-]+)'\)", SRC))
    assert {"notesWrap", "notesItems", "tab-notes", "panel-notes"} <= wanted
    missing = sorted(i for i in wanted if f'id="{i}"' not in TASKS)
    assert not missing, f"ids in notes.js but not in codec_tasks.html: {missing}"


def test_the_tab_is_hidden_by_default_and_has_a_panel_and_the_open_hook():
    tab = re.search(r'<button[^>]*id="tab-notes"[^>]*>', TASKS).group(0)
    assert " hidden" in tab and 'aria-controls="panel-notes"' in tab and "showTab('notes')" in tab
    assert 'id="panel-notes"' in TASKS and 'role="tabpanel"' in TASKS
    assert ".tab[hidden] { display: none; }" in TASKS
    assert "if (name === 'notes' && window.CodecNotes) CodecNotes.show();" in TASKS
    assert re.search(r"@media \(max-width: 767px\) \{[^}]*\.notes-wrap", TASKS), "the phone layout is missing"


def test_no_note_text_ever_goes_in_as_html():
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert forbidden not in SRC, f"notes.js uses {forbidden}"
    save = SHELL[SHELL.index("function notesSave"):SHELL.index("// ── One modal for questions")]
    assert "innerHTML" not in save and "'/api/notes'" in save


def test_the_chat_reply_gets_a_save_to_notes_button_bound_without_inline_text():
    btn = re.search(r"var noteBtn=role==='assistant'\?'(<button[^']*>)", CHAT).group(1)
    assert 'data-act="note"' in btn and 'aria-label="Save to Notes"' in btn
    assert "onclick" not in btn, "message text must never be embedded in an inline handler"
    assert "schedBtn+noteBtn+fbBtns" in CHAT
    bind = CHAT[CHAT.index("var nb=host.querySelector('[data-act=\"note\"]')"):]
    bind = bind[:bind.index("var ub=")]
    assert "CodecShell.notes.save({text:txt,source:'chat'})" in bind
    assert ".msg-act.saved{" in CHAT and "<button" in btn and "emoji" not in btn.lower()


def test_the_shell_offers_notes_save_next_to_schedule():
    assert "notes: { save: notesSave }," in SHELL
    assert SHELL.index("schedule: { open: schedOpen") < SHELL.index("notes: { save: notesSave }")
    assert "Notes need a dashboard restart first." in SHELL and "Open Notes" in SHELL and "/tasks#notes" in SHELL
