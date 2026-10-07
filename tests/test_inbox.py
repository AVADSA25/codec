"""One Inbox (UI phase 3, P3.2; docs/P3.2-DESIGN.md).

GET /api/inbox merges waiting approvals, open questions, reports, agent updates and
suggestions with counts; the server offers only allowed actions; items are read by
id (a stable one for id-less agent messages); one shell drawer and one poller replace
the banners, the Reports tab and their pollers. Nothing here touches ~/.codec.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
SHELL = (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")
PAGES = {p.name: p.read_text(encoding="utf-8") for p in REPO.glob("codec_*.html")}


def _iso(minutes: float) -> str:
    return (datetime.now().astimezone() + timedelta(minutes=minutes)).isoformat()


@pytest.fixture
def box(tmp_path, monkeypatch):
    import codec_agent_plan
    import codec_ask_user
    import routes._shared as sh
    import routes.inbox as ri
    import routes.notifications as rn
    notif = tmp_path / "notifications.json"
    monkeypatch.setattr(sh, "NOTIFICATIONS_PATH", str(notif))
    monkeypatch.setattr(codec_ask_user, "PENDING_QUESTIONS_PATH", tmp_path / "pending_questions.json")
    now = time.time()
    monkeypatch.setattr(sh, "_pending_approvals", {
        "a1": {"command": "rm -rf ~/Downloads/old", "explanation": "Deletes a folder", "is_dangerous": True,
               "timestamp": now, "status": "pending"},
        "a2": {"command": "ls", "explanation": "", "is_dangerous": False, "timestamp": now, "status": "allowed"},
        "a3": {"command": "ls", "explanation": "", "is_dangerous": False, "timestamp": now - 500, "status": "pending"},
    })
    (tmp_path / "pending_questions.json").write_text(json.dumps({"pending_questions": [
        {"id": "q1", "question": "Send the draft to Ana?", "options": ["Send it", "Not now"], "status": "pending",
         "deadline": _iso(10), "asked_at": _iso(-1), "agent": None, "consent_strict": False},
        {"id": "q2", "question": "old", "status": "answered", "deadline": _iso(10), "asked_at": _iso(-5)},
        {"id": "q3", "question": "late", "status": "pending", "deadline": _iso(-1), "asked_at": _iso(-20)},
    ]}))
    notif.write_text(json.dumps([
        {"id": "notif_r1", "type": "task_report", "title": "Morning news", "body": "x" * 500, "status": "success",
         "created": "2026-10-06T08:00:00", "read": False, "doc_url": "https://docs.google.com/d/1"},
        {"id": "notif_q", "type": "question", "title": "CODEC is asking a question", "read": False},
        {"type": "agent_blocked", "agent_id": "ag1", "title": "Blocked: needs a write path",
         "body": "Agent needs additional permission: `~/Documents/out`. Grant or skip?", "ts": "2026-10-06T07:00:00.000+00:00",
         "actions": [{"label": "Grant", "endpoint": "/api/agents/ag1/grant",
                      "body_hint": {"kind": "<infer from reason>", "value": "~/Documents/out"}},
                     {"label": "Abort", "endpoint": "/api/agents/ag1/abort"}]},
        {"type": "agent_update", "agent_id": "ag2", "title": "Step 2 done", "body": "ok", "ts": "2026-10-06T06:00:00.000+00:00"},
        {"type": "proactive_suggestion", "agent_id": "proactive", "title": "Want me to summarize?", "body": "Long read.",
         "ts": "2026-10-06T05:00:00.000+00:00",
         "actions": [{"label": "Summarize", "endpoint": "/api/proactive/acknowledge", "body_hint": {"pattern_id": "long_form_dwell"}},
                     {"label": "Sneaky", "endpoint": "/api/skill/approve", "body_hint": {}}]},
        {"id": "notif_run", "type": "task_report", "title": "Research", "status": "running", "read": False,
         "created": "2026-10-06T09:00:00"},
    ]))
    statuses = {"ag1": "blocked_on_permission", "ag2": "running"}
    monkeypatch.setattr(codec_agent_plan, "load_manifest", lambda aid: {"status": statuses.get(aid, "")})
    app = FastAPI()
    app.include_router(ri.router)
    app.include_router(rn.router)
    return SimpleNamespace(client=TestClient(app), notif=notif, statuses=statuses)


def _by_kind(items, kind):
    return [i for i in items if i["kind"] == kind]


def test_the_inbox_merges_what_waits_and_what_reported(box):
    d = box.client.get("/api/inbox").json()
    items = {i["id"]: i for i in d["items"]}
    assert "approval_a1" in items and "approval_a2" not in items and "approval_a3" not in items, "pending and unexpired only"
    a = items["approval_a1"]
    assert a["group"] == "needs_you" and a["dangerous"] is True and a["approval_id"] == "a1" and 0 < a["expires_in"] <= 120
    assert [q["question_id"] for q in _by_kind(d["items"], "question")] == ["q1"], "answered and late questions are left out"
    assert items["question_q1"]["options"] == ["Send it", "Not now"]
    assert "notif_q" not in items, "a question notification is not a second item"
    r1 = items["notif_r1"]
    assert r1["group"] == "reports" and len(r1["body"]) == 300 and r1["more"] is True and r1["doc_url"].startswith("https://")
    assert items["notif_run"]["read"] is True, "a running report is not new yet"
    agents = _by_kind(d["items"], "agent")
    blocked = [i for i in agents if i["agent_id"] == "ag1"][0]
    assert blocked["group"] == "needs_you" and blocked["id"].startswith("auto_")
    assert blocked["actions"] == [{"label": "Grant", "endpoint": "/api/agents/ag1/grant", "confirm": True,
                                   "body": {"kind": "write_paths", "value": "~/Documents/out"}}], "no Abort, kind inferred"
    running = [i for i in agents if i["agent_id"] == "ag2"][0]
    assert running["group"] == "agents" and running["actions"] == [{"label": "Pause", "endpoint": "/api/agents/ag2/pause"}]
    sug = _by_kind(d["items"], "suggestion")[0]
    assert [x["endpoint"] for x in sug["actions"]] == ["/api/proactive/acknowledge"], "only allowed endpoints"
    assert d["counts"] == {"needs_you": 3, "reports": 1, "agents": 1, "suggestions": 1, "badge": 6}
    assert [i["group"] for i in d["items"][:3]] == ["needs_you"] * 3


def test_actions_follow_the_agents_status(box):
    box.statuses.update(ag1="paused", ag2="done")
    items = box.client.get("/api/inbox").json()["items"]
    ag1 = [i for i in items if i.get("agent_id") == "ag1"][0]
    ag2 = [i for i in items if i.get("agent_id") == "ag2"][0]
    assert ag1["group"] == "agents" and ag1["actions"] == [{"label": "Resume", "endpoint": "/api/agents/ag1/resume"}]
    assert ag2["actions"] == []


def test_items_are_read_by_id_and_id_less_ones_get_theirs(box):
    first = box.client.get("/api/inbox").json()["items"]
    auto = [i["id"] for i in first if i.get("agent_id") == "ag1"][0]
    # A batched agent update retitles itself; its id stays.
    data = json.loads(box.notif.read_text())
    data[2]["title"] = "2 updates from ag1"
    box.notif.write_text(json.dumps(data))
    assert [i["id"] for i in box.client.get("/api/inbox").json()["items"] if i.get("agent_id") == "ag1"] == [auto]
    r = box.client.post("/api/inbox/read", json={"ids": [auto, "notif_r1", "notif_nope", "../x"]})
    assert r.json() == {"marked": 2}
    saved = json.loads(box.notif.read_text())
    assert saved[0]["read"] is True and saved[2]["id"] == auto and saved[2]["read"] is True
    assert box.client.get("/api/inbox").json()["counts"]["reports"] == 0
    assert box.client.post("/api/inbox/read", json={"ids": "notif_r1"}).status_code == 400


def test_one_item_gives_its_whole_text(box):
    d = box.client.get("/api/inbox/item/notif_r1").json()
    assert d["title"] == "Morning news" and d["body"] == "x" * 500 and d["doc_url"] == "https://docs.google.com/d/1"
    assert box.client.get("/api/inbox/item/notif_q").status_code == 404, "questions are answered, not opened"
    assert box.client.get("/api/inbox/item/notif_missing").status_code == 404
    assert box.client.get("/api/inbox/item/NOTIF-bad").status_code == 400


def test_marking_a_notification_read_survives_id_less_entries(box):
    r = box.client.post("/api/notifications/notif_run/read")
    assert r.status_code == 200 and json.loads(box.notif.read_text())[5]["read"] is True


def test_one_drawer_one_poller_and_the_pages_use_it():
    assert "fetch('/api/inbox')" in SHELL and "/api/notifications/count" not in SHELL
    assert "setInterval(pollInbox" not in SHELL and "INBOX.timer = setTimeout(pollInbox, ms);" in SHELL
    assert 'href="/#inbox" id="csInbox"' in SHELL and 'href="/#inbox" id="csTabInbox"' in SHELL
    assert "{ label: 'Inbox', href: '/#inbox', icon: 'inbox', run: function () { inboxOpen(); } }" in SHELL
    assert ('<section class="cs-inbox" id="csInboxPanel" role="dialog" aria-modal="true" aria-labelledby="csInboxTitle" data-dialog="open">'
            in SHELL)
    assert "[['needs_you', 'Needs you'], ['reports', 'Reports'], ['agents', 'Agents'], ['suggestions', 'Suggestions']]" in SHELL
    assert "inbox: { open: inboxOpen, close: inboxClose, refresh: pollInbox, onChange: inboxOnChange," in SHELL
    assert "questionCard: questionCard," in SHELL and "'/tasks#reports'" not in SHELL
    for name, src in PAGES.items():
        assert "approvalBanner" not in src and "fetch('/api/approvals')" not in src, name
    home, chat, tasks = PAGES["codec_dashboard.html"], PAGES["codec_chat.html"], PAGES["codec_tasks.html"]
    # Home shows questions with the Inbox's own cards, in Today's Needs you (P3.4)
    assert "/api/agents/pending_questions" not in home and "CodecShell.inbox.mount(needs, 'needs_you'" in home
    assert "/api/notifications" not in tasks and "panel-reports" not in tasks and "tab-reports" not in tasks
    assert "var rm=h.match(/^#report=((?:notif|auto)_[0-9a-z]{1,40})$/);" in chat and "fetch('/api/inbox/item/'+rm[1])" in chat


def test_phone_pushes_open_the_inbox():
    import codec_push
    assert codec_push.KINDS["approval"][3] == "/#inbox" and codec_push.KINDS["question"][3] == "/#inbox"
    assert codec_push.KINDS["agent_blocked"][3] == "/#inbox" and codec_push.KINDS["agent_done"][3] == "/#inbox=agents"


_JS_HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><html><head></head><body><script src="/static/codec-shell.js" data-page="tasks" data-title="Tasks"></script>' +
  '<main class="cs-main"></main></body></html>', { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/tasks' });
const w = dom.window, calls = [];
const INBOX = { counts: { needs_you: 3, reports: 1, agents: 0, suggestions: 0, badge: 4 }, items: [
  { id: 'approval_a1', kind: 'approval', group: 'needs_you', title: 'Dangerous command', command: 'rm -rf <b>x</b>', body: 'Deletes', dangerous: true, approval_id: 'a1', created: '', expires_in: 90, read: false },
  { id: 'question_q1', kind: 'question', group: 'needs_you', title: 'CODEC is asking', body: 'Go <i>now</i>?', question_id: 'q1', options: ['Yes', 'No'], deadline: new Date(Date.now() + 600000).toISOString(), strict: false, read: false },
  { id: 'auto_ab12cd34ef', kind: 'agent', group: 'needs_you', title: 'Blocked: domain', body: 'needs example.com', agent_id: 'ag1', agent_status: 'blocked_on_permission', read: false,
    actions: [{ label: 'Grant', endpoint: '/api/agents/ag1/grant', confirm: true, body: { kind: 'network_domains', value: 'example.com' } }] },
  { id: 'notif_r1', kind: 'report', group: 'reports', title: 'Morning news', body: 'preview', more: true, read: false, created: '2026-10-06T08:00:00' } ] };
const API = { '/api/inbox': INBOX, '/api/inbox/item/notif_r1': { body: 'the whole report' }, '/api/approvals/a1/allow': { status: 'allowed' },
  '/api/agents/answer/q1': { ok: true }, '/api/inbox/read': { marked: 1 }, '/api/agents/ag1/grant': { status: 'running' } };
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {} });
w.fetch = (u, o) => { u = String(u); calls.push([u, (o && o.method) || 'GET', (o && o.body) || '']);
  const d = API[u]; return Promise.resolve({ ok: !!d, status: d ? 200 : 404, json: () => Promise.resolve(d || {}) }); };
w.eval(fs.readFileSync(process.argv[1], 'utf8'));
const tick = (ms) => new Promise(r => setTimeout(r, ms || 60));
const posted = (u) => calls.filter(c => c[0] === u && c[1] === 'POST').map(c => c[2]);
(async () => {
  const S = w.CodecShell, d = w.document, out = {};
  await tick(150);
  const badge = d.getElementById('csInboxBadge');
  out.badge = badge ? [badge.textContent, badge.hidden, badge.classList.contains('cs-badge-urgent')] : null;
  S.inbox.open();
  await tick();
  const panel = d.getElementById('csInboxPanel');
  out.open = panel.classList.contains('open') && !d.getElementById('csInboxBack').hidden;
  out.tab = d.querySelector('.cs-inbox-tab[aria-selected="true"]').textContent.trim();
  const list = d.getElementById('csInboxList');
  out.cards = list.querySelectorAll('.cs-ib').length;
  out.escaped = !list.querySelector('b, i') && list.textContent.includes('rm -rf <b>x</b>') && list.textContent.includes('Go <i>now</i>?');
  list.querySelector('[data-act="allow"]').click(); await tick();
  out.allow = posted('/api/approvals/a1/allow').length;
  list.querySelector('.cs-q-opt').click(); await tick();
  out.answer = posted('/api/agents/answer/q1');
  d.getElementById('csInboxList').querySelector('[data-act="run"]').click(); await tick();
  out.askShown = !d.getElementById('csAsk').hidden && d.getElementById('csAskTitle').textContent;
  out.grantBefore = posted('/api/agents/ag1/grant').length;
  d.getElementById('csAskOk').click(); await tick();
  out.grant = posted('/api/agents/ag1/grant');
  d.getElementById('csIbTab_reports').click(); await tick();
  out.reportTab = d.getElementById('csInboxList').querySelectorAll('.cs-ib').length;
  d.getElementById('csInboxList').querySelector('[data-act="open"]').click(); await tick(150);
  out.read = posted('/api/inbox/read');
  out.full = d.getElementById('csInboxList').textContent.includes('the whole report');
  out.discuss = d.getElementById('csInboxList').querySelector('a[href="/chat#report=notif_r1"]') !== null;
  d.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await tick();
  out.closed = !panel.classList.contains('open');
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
def test_the_drawer_shows_filters_and_posts_each_answer_under_jsdom():
    out = subprocess.run(["node", "-e", _JS_HARNESS, str(REPO / "static" / "codec-shell.js")], capture_output=True,
                         text=True, cwd=REPO, env=os.environ.copy(), timeout=60)
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("{") or ln.startswith("ERR") or ln == "TIMEOUT"][-1]
    d = json.loads(line)
    assert d["badge"] == ["4", False, True], "a red badge while something needs you"
    assert d["open"] is True and re.match(r"Needs you\s*3", d["tab"]) and d["cards"] == 3 and d["escaped"] is True
    assert d["allow"] == 1 and d["answer"] == ['{"answer":"Yes","answered_via":"pwa"}']
    assert d["askShown"] == "Grant this permission?" and d["grantBefore"] == 0, "a grant asks first"
    assert d["grant"] == ['{"kind":"network_domains","value":"example.com"}']
    assert '{"ids":["auto_ab12cd34ef"]}' in d["read"], "acting on an agent update reads it"
    assert d["reportTab"] == 1 and d["read"][-1] == '{"ids":["notif_r1"]}' and d["full"] is True and d["discuss"] is True
    assert d["closed"] is True
