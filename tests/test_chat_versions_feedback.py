"""Regenerated versions and thumbs feedback (UI phase 2, P2.7; docs/P2.7-DESIGN.md).

Regenerate and edit keep the replaced part of a chat as an earlier version of the
same turn, browsed with a '2 / 3' pager; only the version on screen is in chatHist
and the saved chat. Thumbs up / down (with an optional reason) are stored in a
qchat_feedback table (a hash of the reply, never its text) and a metadata-only
chat_feedback audit event that the shift report and the self-improvement run count.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.qchat as qchat

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "skills") not in sys.path:
    sys.path.insert(0, str(REPO / "skills"))
SHELL = (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")
CHAT = (REPO / "codec_chat.html").read_text(encoding="utf-8")


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "qchat.db"
    monkeypatch.setattr(qchat, "QCHAT_DB", str(path))
    monkeypatch.setattr(qchat, "_qchat_conn", None)
    yield path
    if qchat._qchat_conn is not None:
        qchat._qchat_conn.close()
    monkeypatch.setattr(qchat, "_qchat_conn", None)


@pytest.fixture
def events(monkeypatch):
    got = []
    import codec_audit
    monkeypatch.setattr(codec_audit, "log_event", lambda *a, **kw: got.append((a, kw)))
    return got


@pytest.fixture
def client(db, events):
    app = FastAPI()
    app.include_router(qchat.router)
    c = TestClient(app)
    c.post("/api/qchat/save", json={"session_id": "s1", "title": "Trip plan",
                                    "messages": [{"role": "user", "content": "Plan a trip"},
                                                 {"role": "assistant", "content": "Day one: the coast."}]})
    return c


def _rows(db):
    c = sqlite3.connect(db)
    try:
        return c.execute("SELECT session_id, rating, reason, model, skill, chars, msg_hash FROM qchat_feedback").fetchall()
    finally:
        c.close()


# ── The route ──────────────────────────────────────────────────────────────

def test_thumbs_up_then_down_with_a_reason_updates_one_row(client, db):
    r = client.post("/api/qchat/feedback", json={"session_id": "s1", "content": "Day one: the coast.",
                                                "rating": "up", "model": "local-a"})
    assert r.status_code == 200 and r.json()["rating"] == "up"
    client.post("/api/qchat/feedback", json={"session_id": "s1", "content": "Day one: the coast.",
                                            "rating": "down", "reason": "too_long", "skill": "weather"})
    rows = _rows(db)
    assert len(rows) == 1
    sid, rating, reason, _model, skill, chars, h = rows[0]
    assert (sid, rating, reason, skill, chars) == ("s1", -1, "too_long", "weather", len("Day one: the coast."))
    assert len(h) == 64


def test_clearing_a_rating_keeps_the_row(client, db):
    body = {"session_id": "s1", "content": "Day one: the coast."}
    client.post("/api/qchat/feedback", json={**body, "rating": "down", "reason": "wrong"})
    client.post("/api/qchat/feedback", json={**body, "rating": "none"})
    rows = _rows(db)
    assert len(rows) == 1 and rows[0][1] == 0 and rows[0][2] == "", "cleared, never deleted; no reason without a down"


def test_a_reason_only_goes_with_thumbs_down(client, db):
    client.post("/api/qchat/feedback", json={"session_id": "s1", "content": "x", "rating": "up", "reason": "wrong"})
    assert _rows(db)[0][2] == ""


@pytest.mark.parametrize("body,code", [
    ({"session_id": "s1", "content": "x", "rating": "meh"}, 400),
    ({"session_id": "s1", "content": "x", "rating": "down", "reason": "rude"}, 400),
    ({"session_id": "s1", "content": "", "rating": "up"}, 400),
    ({"session_id": "nope", "content": "x", "rating": "up"}, 404),
])
def test_bad_ratings_are_refused(client, body, code):
    assert client.post("/api/qchat/feedback", json=body).status_code == code


def test_the_reply_text_is_stored_nowhere(client, db, events):
    secret = "The quarterly figure is 4.2 million for Harbor Lane."
    client.post("/api/qchat/save", json={"session_id": "s1", "messages": [{"role": "assistant", "content": "short"}]})
    client.post("/api/qchat/feedback", json={"session_id": "s1", "content": secret, "rating": "down",
                                            "reason": "no_data"})
    c = sqlite3.connect(db)
    dump = "\n".join(c.iterdump())
    c.close()
    assert "Harbor Lane" not in dump.split("CREATE TABLE qchat_feedback")[1]
    fb = [e for e in events if e[0][0] == "chat_feedback"]
    assert len(fb) == 1
    extra = fb[0][1]["extra"]
    assert "Harbor Lane" not in json.dumps(fb[0], default=str)
    assert extra["rating"] == "down" and extra["reason"] == "no_data" and extra["chars"] == len(secret)
    assert len(extra["reply"]) == 16


def test_backup_before_the_feedback_table_on_a_database_with_rows(tmp_path, monkeypatch):
    path = tmp_path / "qchat.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE qchat_sessions (id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT, "
              "user_id TEXT DEFAULT 'default', pinned INTEGER DEFAULT 0, archived INTEGER DEFAULT 0)")
    c.execute("CREATE TABLE qchat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, "
              "content TEXT, timestamp TEXT, user_id TEXT DEFAULT 'default', superseded_at TEXT)")
    c.execute("INSERT INTO qchat_sessions (id, title) VALUES ('s9', 'Garden')")
    c.execute("INSERT INTO qchat_messages (session_id, role, content) VALUES ('s9', 'user', 'Water the roses')")
    c.commit()
    c.close()
    monkeypatch.setattr(qchat, "QCHAT_DB", str(path))
    monkeypatch.setattr(qchat, "_qchat_conn", None)
    try:
        conn = qchat.qchat_db()
        bak = Path(str(path) + ".bak-p2.7")
        assert bak.exists() and stat.S_IMODE(bak.stat().st_mode) == 0o600
        b = sqlite3.connect(bak)
        assert b.execute("SELECT content FROM qchat_messages").fetchone()[0] == "Water the roses"
        assert not b.execute("SELECT 1 FROM sqlite_master WHERE name='qchat_feedback'").fetchone()
        b.close()
        assert conn.execute("SELECT COUNT(*) FROM qchat_messages").fetchone()[0] == 1, "no row lost"
        assert not Path(str(path) + ".bak-p2.2").exists(), "P2.2 columns were already there"
    finally:
        qchat._qchat_conn.close()
        monkeypatch.setattr(qchat, "_qchat_conn", None)


def test_no_backup_for_an_empty_database(db):
    qchat.qchat_db()
    assert not Path(str(db) + ".bak-p2.7").exists()


# ── Shift report and self-improvement ──────────────────────────────────────

def _fb(rating, reply, reason="", skill=""):
    return {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "event": "chat_feedback",
            "outcome": "ok", "extra": {"rating": rating, "reason": reason, "skill": skill, "reply": reply}}


def test_shift_report_counts_the_last_rating_per_reply():
    import shift_report
    recs = [_fb("up", "a"), _fb("down", "b", "too_long"), _fb("down", "c", "too_long"),
            _fb("down", "d", "wrong"), _fb("up", "d"), _fb("down", "e"), _fb("none", "e")]
    line = shift_report._render_feedback(recs)
    assert line == "**Reply feedback:** 2 thumbs up, 2 thumbs down (too long: 2)."
    section = shift_report._render_completed_tasks(recs)
    assert "Reply feedback" in section, "feedback alone still shows in section 1"
    ok = [{"event": "tool_result", "outcome": "ok", "tool": "weather"}]
    assert "Reply feedback" in shift_report._render_completed_tasks(ok + recs)
    assert shift_report._render_feedback(ok) == ""
    assert shift_report._render_completed_tasks(ok).count("Reply feedback") == 0


def test_three_thumbs_down_on_a_skill_is_a_self_improve_gap():
    import codec_self_improve as si
    recs = [_fb("down", "r1", "wrong", "weather"), _fb("down", "r2", "too_long", "weather"),
            _fb("down", "r3", "", "weather"), _fb("down", "r4", "", "calculator"),
            _fb("down", "r5", "", "calculator")]
    gaps = si._find_gaps(recs, {"weather", "calculator"})
    assert [(g["kind"], g["tool"], g["count"]) for g in gaps] == [("disliked_tool", "weather", 3)]
    assert si._GAP_KIND_TO_SIGNAL["disliked_tool"] == "thumbs_down"
    assert "disliked_tool" in si._DRAFT_PROMPT


def test_a_change_of_mind_and_feedback_events_are_not_tool_calls():
    import codec_self_improve as si
    recs = [_fb("down", "r1", "", "weather"), _fb("up", "r1", "", "weather"),
            _fb("down", "r2", "", "weather"), _fb("down", "r3", "", "weather")]
    assert si._find_gaps(recs, {"weather"}) == [], "r1 was changed to up: only 2 downs"
    many = [dict(_fb("down", f"x{i}", "", ""), tool="weather") for i in range(6)]
    assert si._find_gaps(many, {"weather"}) == [], "ratings never count toward the error-rate gap"


# ── The page ───────────────────────────────────────────────────────────────

def _block(src: str, start: str, end: str) -> str:
    i = src.index(start)
    return src[i:src.index(end, i)]


def test_regenerate_and_edit_keep_the_replaced_tail_as_a_version():
    regen = _block(CHAT, "function regenerateResponse(", "document.getElementById('crewSelect')")
    assert "_verKeep(n,tail,'assistant');" in regen and "tail.forEach(function(t){t.remove()});" in regen
    assert "div.remove();_verRestore()" in regen, "a regenerate with no answer puts the old one back"
    edit = _block(CHAT, "function editMessage(", "var addMsg=addMessage;")
    assert "_verKeep(i,tail,'user');chatHist=chatHist.slice(0,i);_markDiscard(i);break" in edit
    add = _block(CHAT, "function addMessage(", "// ── Agent → Chat handoff")
    assert "_verAttach(div,role);" in add


def test_the_pager_swaps_the_tail_and_resaves_the_chosen_version():
    ver = _block(CHAT, "var _verPending=null;", "// ── Thumbs feedback (P2.7)")
    for needle in ('aria-label="Previous version"', 'aria-label="Next version"', "(g.cur+1)+' / '+g.versions.length",
                   "if(isProcessing){showToast('Wait for the reply to finish');return}",
                   "chatHist=chatHist.slice(0,g.prefix).concat(v.hist||[]);",
                   "_markDiscard(g.prefix);\n  saveMessages(v.saved||[]);"):
        assert needle in ver, needle
    save = _block(CHAT, "async function saveMessages(", "\n}\n")
    assert "_savedRows=_savedRows.slice(0,_discardFrom);" in save, "the saved-rows mirror follows discard_from"
    assert "_savedRows.push(_rowCopy(m))" in CHAT, "a loaded chat fills the mirror"


def test_thumbs_reasons_and_the_more_menu():
    add = _block(CHAT, "function addMessage(", "// ── Agent → Chat handoff")
    for needle in ('data-act="up" aria-pressed="false"', 'data-act="down" aria-pressed="false"',
                   'data-act="more" aria-haspopup="menu"', "rateMessage(host,txt,'up')", "replyMore(host,mb)"):
        assert needle in add, needle
    fb = _block(CHAT, "// ── Thumbs feedback (P2.7)", "// ── More menu on a reply (P2.7)")
    assert "fetch('/api/qchat/feedback'" in fb and "x-csrf-token" in fb
    for reason in ("['wrong','Wrong']", "['too_long','Too long']", "['no_data','Didn\\'t use my data']"):
        assert reason in fb, reason
    more = _block(CHAT, "// ── More menu on a reply (P2.7)", "// ── Composer card")
    assert "'Retry with Think'" in more and "toggleThinking();regenerateResponse(div)" in more
    assert "'Retry with another model'" in more and "m.open({onClose:function(changed){_retryAfterSwitch=changed?div:null}})" in more
    assert "CodecShell.actions(btn,items)" in more
    sw = _block(CHAT, "function switchModel(){", "// ── Create image")
    assert "if(rd&&rd.isConnected)regenerateResponse(rd);" in sw
    assert "if(j.skill){_lastSkill=j.skill}" in CHAT, "the streamed skill is kept for the rating"


def test_the_shell_offers_an_action_menu_and_menu_close_callback():
    assert "ask: ask, menu: menu, actions: actions," in SHELL
    act = SHELL[SHELL.index("function actions(btn, items)"):][:3000]
    for needle in ("role=\"menuitem\"", "lab.textContent = it.label;", "btn.setAttribute('aria-expanded', 'true');"):
        assert needle in act, needle
    keys = act
    for k in ("'ArrowDown'", "'ArrowUp'", "'Home'", "'End'", "'Escape'"):
        assert k in keys, k
    assert "st.onClose = o && o.onClose;" in SHELL and "if (cb) { try { cb(st.changed); }" in SHELL
    assert "bulb:" in SHELL


# ── Under jsdom (skipped without it, as in CI) ─────────────────────────────

_JS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const src = fs.readFileSync(process.argv[1], 'utf8');
const ver = src.slice(src.indexOf('var _verPending=null;'), src.indexOf('// ── Thumbs feedback (P2.7)'));
const dom = new JSDOM('<!doctype html><div id="messages"></div>', { runScripts: 'outside-only' });
const w = dom.window;
w.eval(`var chatHist=[],_savedRows=[],_discardFrom=null,isProcessing=false,saved=[];
  function _markDiscard(n){_discardFrom=n}
  function saveMessages(m){saved.push({from:_discardFrom,rows:m});_discardFrom=null;_savedRows=_savedRows.concat(m)}
  function showToast(){} function scrollBottom(){}
  function msg(role,t){var d=document.createElement('div');d.className='msg '+role;d.innerHTML='<div class="msg-bubble"></div><div class="msg-meta"><span class="msg-time"></span></div>';d.firstChild.textContent=t;document.getElementById('messages').appendChild(d);_verAttach(d,role);return d}
  ` + ver);
w.eval(`msg('user','Q');chatHist.push({role:'user',content:'Q'});_savedRows.push({role:'user',content:'Q'});
  var a1=msg('assistant','A1');chatHist.push({role:'assistant',content:'A1'});_savedRows.push({role:'assistant',content:'A1'});
  _verKeep(1,[a1],'assistant');a1.remove();chatHist=chatHist.slice(0,1);
  var a2=msg('assistant','A2');chatHist.push({role:'assistant',content:'A2'});_savedRows.push({role:'assistant',content:'A2'});
  var out={pager:a2.querySelector('.msg-ver-n').textContent};
  _verShow(a2._verGroup,0);
  out.after=[].map.call(document.querySelectorAll('.msg-bubble'),function(b){return b.textContent});
  out.hist=chatHist.map(function(m){return m.content});out.saved=saved;
  out.pager0=a1.querySelector('.msg-ver-n').textContent;
  _verShow(a1._verGroup,1);out.back=chatHist.map(function(m){return m.content});
  console.log(JSON.stringify(out));`);
"""


def _jsdom_available() -> bool:
    if shutil.which("node") is None:
        return False
    r = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True, cwd=REPO,
                       env=os.environ.copy(), timeout=20)
    return r.returncode == 0


@pytest.mark.skipif(not _jsdom_available(), reason="node with jsdom not resolvable (set NODE_PATH)")
def test_version_swap_under_jsdom():
    out = subprocess.run(["node", "-e", _JS, str(REPO / "codec_chat.html")], capture_output=True, text=True,
                         cwd=REPO, env=os.environ.copy(), timeout=60)
    d = json.loads([ln for ln in out.stdout.splitlines() if ln.startswith("{")][-1])
    assert d["pager"] == "2 / 2" and d["pager0"] == "1 / 2"
    assert d["after"] == ["Q", "A1"] and d["hist"] == ["Q", "A1"]
    assert d["saved"] == [{"from": 1, "rows": [{"role": "assistant", "content": "A1"}]},
                          {"from": 1, "rows": [{"role": "assistant", "content": "A2"}]}]
    assert d["back"] == ["Q", "A2"]
