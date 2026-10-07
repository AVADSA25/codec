"""Today home (UI phase 3, P3.4; docs/P3.4-DESIGN.md).

Home opens on Today: open threads (Close, Snooze), agents, yesterday's shift report,
the next calendar events through the calendar skill (cached), fixed quick tiles run
on the server, and Needs you drawn with the Inbox's own cards. Nothing here touches
~/.codec: tmp paths and fakes.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
HOME = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")
SHELL = (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")


def _manifest(root: Path, aid: str, status: str, hours_ago: float, title: str) -> None:
    d = root / aid
    d.mkdir(parents=True)
    when = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    (d / "manifest.json").write_text(json.dumps({"agent_id": aid, "title": title, "status": status,
                                                 "created_at": when, "updated_at": when}))


@pytest.fixture
def today(tmp_path, monkeypatch):
    import codec_agent_plan
    import codec_daybreak
    import codec_today
    import routes._shared as sh
    import routes.chat as rc
    import routes.today as rt
    monkeypatch.setattr(codec_today, "TODAY_PATH", tmp_path / "today.json")
    monkeypatch.setattr(codec_today, "THREAD_SNOOZE_PATH", tmp_path / "thread_snooze.json")
    monkeypatch.setattr(codec_daybreak, "get_open_threads", lambda user_id="default": [
        {"key": "thread:working_on:roof-quote", "kind": "working_on", "text": "Roof quote from the builder", "since": "2026-10-01"},
        {"key": "thread:waiting_on:ana-invoice", "kind": "waiting_on", "text": "Invoice from Ana", "since": "2026-10-02"},
    ])
    agents = tmp_path / "agents"
    _manifest(agents, "ag_run", "running", 1, "Price watcher")
    _manifest(agents, "ag_block", "blocked_on_permission", 3, "Site checker")
    _manifest(agents, "ag_done", "completed", 2, "Weekly digest")
    _manifest(agents, "ag_old", "completed", 72, "Old job")
    _manifest(agents, "ag_draft", "draft_pending", 1, "Draft only")
    monkeypatch.setattr(codec_agent_plan, "_AGENTS_DIR", agents)
    notif = tmp_path / "notifications.json"
    y = (date.today() - timedelta(days=1)).isoformat()
    notif.write_text(json.dumps([
        {"id": "notif_y1", "type": "shift_report", "title": "Shift report", "created": f"{y}T18:00:00", "read": False,
         "body": "# Shift report\n\n## Completed\n- 12 tasks done\n- 3 schedules ran\n\n* 1 question waits\n- Busiest: Chat\n- extra"},
    ]))
    monkeypatch.setattr(sh, "NOTIFICATIONS_PATH", str(notif))
    calls = []

    def fake_skill(name, text, temporary=False):
        calls.append((name, text))
        if name == "google_calendar":
            return name, "Today's schedule — 2 event(s):\n  All day — Holiday\n  11:59 PM — Late call"
        return name, "Now playing: Song A by Band B" if text == "what is playing" else "ok " * 300

    monkeypatch.setattr(rc, "_try_explicit_skill", fake_skill)
    monkeypatch.setattr(rt, "_NEXT", {"at": 0.0, "data": None})
    app = FastAPI()
    app.include_router(rt.router)
    return SimpleNamespace(client=TestClient(app), calls=calls, tmp=tmp_path, rt=rt, notif=notif)


def test_home_lists_threads_agents_and_yesterday(today):
    d = today.client.get("/api/today/home").json()
    assert [t["key"] for t in d["threads"]] == ["thread:working_on:roof-quote", "thread:waiting_on:ana-invoice"]
    assert [a["agent_id"] for a in d["agents"]] == ["ag_run", "ag_block", "ag_done"], "active first, finished today, no drafts"
    assert d["agents"][0]["active"] is True and d["agents"][2]["active"] is False
    assert d["yesterday"] == {"id": "notif_y1", "title": "Shift report",
                              "lines": ["Shift report", "Completed", "12 tasks done", "3 schedules ran"]}
    today.notif.write_text(json.dumps([{"id": "notif_o", "type": "shift_report", "title": "Old", "created": "2020-01-01T18:00:00"}]))
    assert today.client.get("/api/today/home").json()["yesterday"] is None, "only yesterday's"


def test_a_snoozed_thread_leaves_today_for_that_long(today):
    r = today.client.post("/api/today/threads/snooze", json={"key": "thread:waiting_on:ana-invoice", "hours": 24})
    assert r.status_code == 200
    until = datetime.fromisoformat(r.json()["until"])
    assert timedelta(hours=23) < until - datetime.now() <= timedelta(hours=24)
    assert [t["key"] for t in today.client.get("/api/today/home").json()["threads"]] == ["thread:working_on:roof-quote"]
    path = today.tmp / "thread_snooze.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    import codec_today
    assert codec_today.snoozed_threads(now=datetime.now() + timedelta(hours=25)) == set(), "it comes back"
    for bad in ({"key": "notes", "hours": 24}, {"key": "thread:working_on:x", "hours": 0},
                {"key": "thread:working_on:x", "hours": 200}, {"key": "thread:working_on:x", "hours": "x"}):
        assert today.client.post("/api/today/threads/snooze", json=bad).status_code == 400, bad


def test_next_up_reads_the_calendar_skill_and_caches_it(today):
    rt = today.rt
    text = "Today's schedule — 4 event(s):\n  All day — Holiday\n  9:00 AM — Standup\n  2:30 PM — Dentist\n  6:00 PM — Run"
    got = rt._parse_events(text, now=datetime(2026, 10, 6, 10, 0))
    assert got["events"] == [{"time": "All day", "title": "Holiday"}, {"time": "2:30 PM", "title": "Dentist"},
                             {"time": "6:00 PM", "title": "Run"}], "past events dropped, three at most"
    assert rt._parse_events("Calendar error: invalid_grant")["connected"] is False
    assert rt._parse_events("No events today. Your calendar is clear.")["note"] == "Nothing more today."
    first = today.client.get("/api/today/next").json()
    second = today.client.get("/api/today/next").json()
    assert first == second and first["events"][0] == {"time": "All day", "title": "Holiday"}
    assert today.calls == [("google_calendar", "what's on my calendar today")], "once, through the explicit-skill path"


def test_tiles_run_only_their_fixed_skill_and_words(today):
    c = today.client
    assert c.post("/api/today/tile", json={"tile": "volume_up", "skill": "terminal", "task": "rm -rf ~"}).json()["skill"] == "volume_brightness"
    assert c.post("/api/today/tile", json={"tile": "timer", "minutes": 5}).status_code == 200
    playing = c.post("/api/today/tile", json={"tile": "now_playing"}).json()
    assert playing["result"] == "Now playing: Song A by Band B"
    assert len(c.post("/api/today/tile", json={"tile": "lights_relax"}).json()["result"]) == 500
    assert today.calls == [("volume_brightness", "volume up"), ("timer", "set a timer for 5 minutes"),
                           ("music", "what is playing"), ("philips_hue", "relax mode")]
    for bad in ({"tile": "terminal"}, {"tile": "timer"}, {"tile": "timer", "minutes": 0},
                {"tile": "timer", "minutes": 500}, {"tile": "timer", "minutes": "five"}):
        assert c.post("/api/today/tile", json=bad).status_code == 400, bad
    assert len(today.calls) == 4, "a refused tile runs nothing"
    assert set(today.rt.TILES) == {"now_playing", "music_pause", "music_resume", "lights_relax", "lights_off",
                                   "volume_down", "volume_up", "timer"}


def test_home_opens_on_today_and_keeps_flash_and_history():
    assert '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>Today\n    </button>' in HOME
    assert 'onclick="showTab(\'history\')" id="tab-history"' in HOME and 'class="input-area"' in HOME
    for sid in ("thNeedsSec", "thNextSec", "thThreadsSec", "thAgentsSec", "thYesterdaySec"):
        assert f'id="{sid}"' in HOME, sid
    assert HOME.index('id="todayHome"') < HOME.index('id="todayCards"') < HOME.index('id="chatList"')
    tiles = set(re.findall(r'data-tile="([a-z_]+)"', HOME))
    assert tiles == {"now_playing", "music_pause", "music_resume", "lights_relax", "lights_off", "volume_down",
                     "volume_up", "timer", "screenshot"}
    assert "CodecShell.inbox.mount(needs, 'needs_you'" in HOME and "_thPost('/api/today/tile', body)" in HOME
    assert "if (id === 'chat') { loadChat(); loadTodayHome(); loadTodayNext(); }" in HOME
    assert "data-act=\"replay\">Replay</button>" in HOME and "CodecShell.speech.speak(c.body || '', { btn: replay })" in HOME
    assert "inbox: { open: inboxOpen, close: inboxClose, refresh: pollInbox, onChange: inboxOnChange, mount: inboxMount," in SHELL


_JS_HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><html><head></head><body><script src="/static/codec-shell.js" data-page="home" data-title="Today"></script>' +
  '<main class="cs-main"><div id="box"></div></main></body></html>', { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
const w = dom.window, calls = [];
const INBOX = { counts: { needs_you: 1, reports: 1, agents: 0, suggestions: 0, badge: 2 }, items: [
  { id: 'approval_a1', kind: 'approval', group: 'needs_you', title: 'A command needs your approval', command: 'ls', body: '', dangerous: false, approval_id: 'a1', created: '', read: false },
  { id: 'notif_r1', kind: 'report', group: 'reports', title: 'Morning news', body: 'x', read: false, created: '' } ] };
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {} });
w.fetch = (u, o) => { u = String(u); calls.push([u, (o && o.method) || 'GET']);
  const d = u === '/api/inbox' ? INBOX : (u === '/api/approvals/a1/allow' ? { status: 'allowed' } : null);
  return Promise.resolve({ ok: !!d, status: d ? 200 : 404, json: () => Promise.resolve(d || {}) }); };
w.eval(fs.readFileSync(process.argv[1], 'utf8'));
(async () => {
  const S = w.CodecShell, d = w.document, out = {};
  await new Promise(r => setTimeout(r, 150));
  let count = -1;
  S.inbox.mount(d.getElementById('box'), 'needs_you', n => { count = n; });
  const box = d.getElementById('box');
  out.cards = [box.querySelectorAll('.cs-ib').length, count, box.textContent.includes('Morning news')];
  box.querySelector('[data-act="allow"]').click();
  await new Promise(r => setTimeout(r, 100));
  out.allowed = calls.filter(c => c[0] === '/api/approvals/a1/allow' && c[1] === 'POST').length;
  out.drawerClosed = !d.getElementById('csInboxPanel') || !d.getElementById('csInboxPanel').classList.contains('open');
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
def test_a_page_mounts_an_inbox_group_with_its_actions_under_jsdom():
    out = subprocess.run(["node", "-e", _JS_HARNESS, str(REPO / "static" / "codec-shell.js")], capture_output=True,
                         text=True, cwd=REPO, env=os.environ.copy(), timeout=60)
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("{") or ln.startswith("ERR") or ln == "TIMEOUT"][-1]
    d = json.loads(line)
    assert d["cards"] == [1, 1, False], "only that group's cards, and the count"
    assert d["allowed"] == 1 and d["drawerClosed"] is True, "the card's actions work outside the drawer"
