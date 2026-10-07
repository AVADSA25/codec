"""Activity board (UI phase 3, P3.8; docs/P3.8-DESIGN.md).

Progress fields on GET /api/agents, crew runs kept across a dashboard restart
(~/.codec/agent_jobs.json), the global-grant folder refusal, Tasks' Activity tab,
Chat's running-task rail and the two logged Chat fixes. Nothing here touches
~/.codec: tmp dirs and fakes.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
TASKS = (REPO / "codec_tasks.html").read_text(encoding="utf-8")
CHAT = (REPO / "codec_chat.html").read_text(encoding="utf-8")


@pytest.fixture
def ag(tmp_path, monkeypatch):
    import codec_agent_messaging as cam
    import codec_agent_plan as cap
    import codec_audit
    import routes._shared as sh
    monkeypatch.setattr(cap, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cap, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cap, "_GLOBAL_GRANTS_PATH", tmp_path / "agent_global_grants.json")
    monkeypatch.setattr(cam, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cam, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    monkeypatch.setattr(sh, "AGENT_JOBS_PATH", str(tmp_path / "agent_jobs.json"))
    monkeypatch.setattr(sh, "_agent_jobs_loaded", False)
    saved = dict(sh._agent_jobs)
    sh._agent_jobs.clear()
    from routes.agents import router
    app = FastAPI()
    app.include_router(router)
    yield SimpleNamespace(client=TestClient(app), cap=cap, cam=cam, sh=sh, tmp=tmp_path)
    sh._agent_jobs.clear()
    sh._agent_jobs.update(saved)


def _iso(minutes_ago: float) -> str:
    return (datetime.now() - timedelta(minutes=minutes_ago)).isoformat()


def _agent(cap, aid, status, *, checkpoints=3, current=0, reason="", created=None, approved=None,
           updated=None, messages=()):
    from codec_agent_plan import Checkpoint, PermissionManifest, Plan
    if checkpoints:
        cps = [Checkpoint(id=f"c{i}", title=f"Step {i}", description="Do it", skills_needed=[], expected_output="x")
               for i in range(checkpoints)]
        cap.save_plan(Plan(schema=1, agent_id=aid, goals=["Ship it"], checkpoints=cps,
                           permission_manifest=PermissionManifest([], [], [], [], []), estimated_duration_minutes=5))
    cap.save_state(aid, {"current_checkpoint": current})
    m = {"agent_id": aid, "title": aid.title(), "status": status, "created_at": created, "updated_at": updated}
    if approved:
        m["approved_at"] = approved
    if reason:
        m["status_reason"] = reason
    cap.save_manifest(aid, m)
    if messages:
        with open(cap._AGENTS_DIR / aid / "messages.jsonl", "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg) + "\n")


def test_the_list_reports_progress_elapsed_the_last_message_and_silence(ag):
    filler = [{"type": "agent_update", "title": "x" * 300, "ts": i} for i in range(200)]  # > 16 KB before the last one
    _agent(ag.cap, "running-one", "running", checkpoints=5, current=2, created=_iso(30), approved=_iso(10),
           messages=filler + [{"type": "agent_update", "title": "Checkpoint 2 done", "ts": 9}, {"bad": "line"}])
    _agent(ag.cap, "budget-one", "paused", reason="step_budget_exhausted", created=_iso(60), approved=_iso(50))
    _agent(ag.cap, "paused-one", "paused", reason="user_paused", created=_iso(60))
    _agent(ag.cap, "done-one", "completed", checkpoints=4, current=3, created=_iso(120), approved=_iso(100),
           updated=_iso(40))
    _agent(ag.cap, "draft-one", "draft_pending", checkpoints=0, created=_iso(1))
    ag.cam.set_silenced("paused-one", True)
    rows = {a["agent_id"]: a for a in ag.client.get("/api/agents").json()["agents"]}
    r = rows["running-one"]
    assert (r["checkpoints_total"], r["checkpoint_index"]) == (5, 2)
    assert 9 * 60 <= r["elapsed_s"] <= 11 * 60, "a live project counts from its approval to now"
    assert r["last_message"]["title"] == "Checkpoint 2 done" and r["last_message"]["type"] == "agent_update"
    assert r["silenced"] is False and r["can_extend"] is False and r["title"] == "Running-One"
    assert rows["budget-one"]["can_extend"] is True and rows["budget-one"]["status_reason"] == "step_budget_exhausted"
    assert rows["paused-one"]["can_extend"] is False and rows["paused-one"]["silenced"] is True
    d = rows["done-one"]
    assert (d["checkpoints_total"], d["checkpoint_index"]) == (4, 4), "a finished project shows every checkpoint done"
    assert 59 * 60 <= d["elapsed_s"] <= 61 * 60, "a finished one counts from its approval to its last update"
    assert rows["draft-one"]["checkpoints_total"] == 0 and rows["draft-one"]["last_message"] is None


def test_crew_runs_are_kept_and_come_back_interrupted_after_a_restart(ag):
    sh = ag.sh
    with sh._agent_jobs_lock:
        sh._agent_jobs["live1"] = {"status": "running", "crew": "daily_briefing", "started": _iso(2),
                                   "progress": [f"step {i}" for i in range(80)]}
        sh._agent_jobs["done1"] = {"status": "complete", "crew": "deep_research", "started": _iso(30),
                                   "finished": _iso(25), "progress": [{"message": "Wrote the report"}],
                                   "doc_url": "https://docs.google.com/document/d/x"}
        sh._agent_jobs["bad1"] = {"status": "error", "crew": "trip_planner", "started": _iso(60), "error": "boom",
                                  "doc_url": "javascript:alert(1)"}
    sh._save_agent_jobs()
    path = Path(sh.AGENT_JOBS_PATH)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    on_disk = json.loads(path.read_text())
    assert len(on_disk["live1"]["progress"]) == 50 and on_disk["live1"]["progress"][-1] == "step 79"
    # The dashboard restarts: memory is empty and the file is read on first use.
    sh._agent_jobs.clear()
    sh._agent_jobs_loaded = False
    jobs = ag.client.get("/api/agents/jobs").json()["jobs"]
    assert [j["job_id"] for j in jobs] == ["live1", "done1", "bad1"], "newest first"
    live, done, bad = jobs
    assert live["status"] == "interrupted" and "restarted" in live["error"] and live["steps"] == 50
    assert done["last"] == "Wrote the report" and done["doc_url"].startswith("https://") and done["finished"]
    assert bad["doc_url"] is None and bad["error"] == "boom"
    assert ag.client.get("/api/agents/status/live1").json()["status"] == "interrupted"


def test_the_job_file_keeps_the_newest_fifty(ag):
    sh = ag.sh
    with sh._agent_jobs_lock:
        for i in range(55):
            sh._agent_jobs[f"j{i:02d}"] = {"status": "complete", "crew": "c", "started": _iso(100 - i)}
    sh._save_agent_jobs()
    kept = json.loads(Path(sh.AGENT_JOBS_PATH).read_text())
    assert len(kept) == 50 and "j54" in kept and "j04" not in kept and "j05" in kept


def test_a_crew_run_is_written_when_it_starts_and_when_it_ends(ag, monkeypatch):
    import codec_agents
    import codec_license
    monkeypatch.setattr(codec_license, "feature_allowed", lambda *a, **k: True)
    seen = {}

    async def fake_run_crew(name, callback=None, **kw):
        seen["at_start"] = json.loads(Path(ag.sh.AGENT_JOBS_PATH).read_text())
        callback("Looked at the inbox")
        return {"status": "complete", "result": "Three emails need you."}

    monkeypatch.setattr(codec_agents, "run_crew", fake_run_crew)
    jid = ag.client.post("/api/agents/run", json={"crew": "email_handler"}).json()["job_id"]
    # Wait for the file, not the status route: the run thread sets the status a
    # moment before it saves the file.
    for _ in range(250):
        saved = json.loads(Path(ag.sh.AGENT_JOBS_PATH).read_text())[jid]
        if saved.get("status") != "running":
            break
        time.sleep(0.02)
    assert seen["at_start"][jid]["status"] == "running"
    assert saved["status"] == "complete" and saved["finished"] and saved["progress"] == ["Looked at the inbox"]
    assert ag.client.post("/api/agents/cancel/" + jid).status_code == 200
    assert json.loads(Path(ag.sh.AGENT_JOBS_PATH).read_text())[jid]["cancel_requested"] is True


def test_the_global_list_refuses_unsafe_folders(ag):
    c = ag.client
    for bad in ("/", "~", "~/.ssh", "/etc", "~/Documents/../../etc", "~/.codec/config.json"):
        for kind in ("read_paths", "write_paths"):
            r = c.post("/api/agent_global_grants", json={"kind": kind, "value": bad})
            assert r.status_code == 400 and "refused" in r.json()["detail"], (kind, bad)
    assert c.get("/api/agent_global_grants").json().get("read_paths", []) == []
    assert "/tmp/agent-work" in c.post("/api/agent_global_grants",
                                        json={"kind": "read_paths", "value": "/tmp/agent-work"}).json()["read_paths"]
    assert "example.com" in c.post("/api/agent_global_grants",
                                   json={"kind": "network_domains", "value": "example.com"}).json()["network_domains"]
    gone = c.request("DELETE", "/api/agent_global_grants", json={"kind": "read_paths", "value": "/tmp/agent-work"}).json()
    assert "/tmp/agent-work" not in gone["read_paths"]


def test_tasks_has_the_activity_tab_with_its_sections_and_actions():
    assert 'id="tab-activity"' in TASKS and 'id="panel-activity" role="tabpanel"' in TASKS
    for sid in ('id="actProjects"', 'id="actCrews"', 'id="actGrants"', 'id="actGrantForm"'):
        assert sid in TASKS, sid
    assert "Permissions for every project" in TASKS and "if (name === 'activity') { actLoad(); actLoadGrants(); }" in TASKS
    assert "fetch('/api/agents/jobs')" in TASKS and "fetch('/api/agent_global_grants')" in TASKS
    for act in ("'/pause'", "'/resume'", "'/extend_budget'", "'/approve'", "'/abort'", "'/silence'", "'/revise'",
                "'/artifacts'", "'/open-folder'", "'/api/agents/cancel/'"):
        assert act in TASKS, act
    assert "pattern: '[1-9][0-9]?|100'" in TASKS, "Extend asks for 1 to 100 steps"
    assert "title: 'Stop this project?'" in TASKS and "danger: true" in TASKS
    assert "CodecShell.inbox.open('needs_you')" in TASKS
    assert TASKS.count('data-dialog="open"') >= 2, "Revise and Files are watched dialogs"
    assert "if (document.hidden || !panel.classList.contains('active')) return;" in TASKS, "polls only while it shows"


def test_chat_rail_and_the_two_fixes():
    assert "rail.className='run-rail'" in CHAT and "<a class=\"run-all\" href=\"/tasks#activity\">All activity</a>" in CHAT
    assert "fetch('/api/agents/jobs')" in CHAT and 'role="progressbar"' in CHAT
    rail = CHAT[CHAT.index("async function refreshAgentStatusPills(){"):CHAT.index("async function pauseAgentInChat")]
    assert "style.cssText" not in rail and "onclick=" not in rail, "classes and one delegated listener, no inline styles"
    delete = CHAT[CHAT.index("async function deleteSelectedAgent(){"):CHAT.index("// ── Header icons")]
    assert delete.index("await CodecShell.ask({title:'Delete this agent?'") < delete.index("/api/agents/custom/delete")
    assert "function _emptySendOk(){" in CHAT
    assert "else if(!_emptySendOk())return;" in CHAT and "if(!text&&!pendingFiles.length&&!_emptySendOk())return;" in CHAT
    assert "&&!(_emptySendOk()&&!_streamingUi));" in CHAT, "Send looks live for a crew that needs no text"
    known = (REPO / "docs" / "known-issues.md").read_text(encoding="utf-8")
    assert "Chat box (2026-09-29) — FIXED in P3.8" in known and "does not ask first (2026-09-29) — FIXED in P3.8" in known


def _jsdom_available() -> bool:
    if shutil.which("node") is None:
        return False
    r = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True, cwd=REPO,
                       env=os.environ.copy(), timeout=20)
    return r.returncode == 0


_JS_HARNESS = r"""
const { JSDOM } = require('jsdom');
const fs = require('fs');
process.on('unhandledRejection', () => {});
const html = fs.readFileSync(process.argv[1], 'utf8');
const now = Date.now(), iso = t => new Date(t).toISOString().slice(0, 19);
const agents = [
  { agent_id: 'run1', title: 'Build the site', status: 'running', checkpoints_total: 5, checkpoint_index: 2, elapsed_s: 125,
    created_at: iso(now - 6e5), silenced: false, can_extend: false, last_message: { type: 'agent_update', title: 'Checkpoint 2 done' } },
  { agent_id: 'old1', title: 'Old report', status: 'completed', checkpoints_total: 3, checkpoint_index: 3, elapsed_s: 600,
    created_at: iso(now - 9e6), updated_at: iso(now - 8e6), silenced: false, can_extend: false, last_message: null },
  { agent_id: 'bud1', title: 'Clean the inbox', status: 'paused', status_reason: 'step_budget_exhausted', checkpoints_total: 2,
    checkpoint_index: 1, elapsed_s: 3000, created_at: iso(now - 4e6), silenced: true, can_extend: true, last_message: null }];
const jobs = [{ job_id: 'j1', crew: 'daily_briefing', status: 'running', started: iso(now - 9e4), steps: 3, last: 'Reading mail' },
  { job_id: 'j0', crew: 'deep_research', status: 'interrupted', started: iso(now - 9e6), steps: 50, error: 'The dashboard restarted while this run was going.' }];
const calls = [], asks = [];
const dom = new JSDOM(html, { url: 'http://localhost/tasks#activity', runScripts: 'dangerously', pretendToBeVisual: true,
  beforeParse(w) {
    w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {} });
    w.CodecShell = { ask: o => { asks.push(o.title); return Promise.resolve(o.input ? '20' : true); }, inbox: { open() {} } };
    w.fetch = (u, o) => { u = String(u); calls.push([u, (o && o.method) || 'GET', (o && o.body) || '']);
      let d = {};
      if (u === '/api/agents') d = { agents };
      else if (u === '/api/agents/jobs') d = { jobs };
      else if (u === '/api/agent_global_grants') d = { network_domains: ['example.com'], read_paths: [], write_paths: [], skills: ['web_search'] };
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(d) }); };
  } });
const w = dom.window;
setTimeout(async () => {
  const d = w.document, out = {};
  out.tabOn = d.getElementById('tab-activity').classList.contains('active');
  const cards = [...d.querySelectorAll('#actProjects .act-card')];
  out.order = cards.map(c => c.getAttribute('data-agent'));
  out.buttons = Object.fromEntries(cards.map(c => [c.getAttribute('data-agent'), [...c.querySelectorAll('button')].map(b => b.textContent)]));
  out.states = cards.map(c => c.querySelector('.act-state').textContent);
  const bar = d.querySelector('.act-card[data-agent="run1"] .act-bar');
  out.bar = [bar.getAttribute('aria-valuenow'), bar.getAttribute('aria-valuemax'), bar.firstChild.style.width];
  out.meta = d.querySelector('.act-card[data-agent="run1"] .act-meta').textContent;
  out.last = d.querySelector('.act-card[data-agent="run1"] .act-last').textContent;
  out.crews = [...d.querySelectorAll('#actCrews .act-card')].map(c => [c.querySelector('.act-title').textContent,
    c.querySelector('.act-state').textContent, [...c.querySelectorAll('button')].map(b => b.textContent)]);
  out.chips = [...d.querySelectorAll('#actGrants .act-chip span')].map(s => s.textContent);
  d.querySelector('.act-card[data-agent="run1"] [data-act="stop"]').click();
  await new Promise(r => setTimeout(r, 50));
  d.querySelector('.act-card[data-agent="bud1"] [data-act="extend"]').click();
  await new Promise(r => setTimeout(r, 50));
  d.querySelector('#actGrantKind [data-kind="skills"]').click();
  d.getElementById('actGrantValue').value = 'calculator';
  d.querySelector('#actGrantForm button[type="submit"]').click();
  await new Promise(r => setTimeout(r, 50));
  out.asks = asks;
  out.posts = calls.filter(c => c[1] !== 'GET').map(c => c[0] + ' ' + c[2]);
  console.log(JSON.stringify(out)); process.exit(0);
}, 300);
setTimeout(() => { console.log('TIMEOUT'); process.exit(2); }, 20000);
"""


@pytest.mark.skipif(not _jsdom_available(), reason="node with jsdom not resolvable (set NODE_PATH)")
def test_the_activity_tab_renders_projects_crews_and_grants_under_jsdom():
    out = subprocess.run(["node", "-e", _JS_HARNESS, str(REPO / "codec_tasks.html")], capture_output=True, text=True,
                         cwd=REPO, env=os.environ.copy(), timeout=60)
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("{") or ln == "TIMEOUT"][-1]
    d = json.loads(line)
    assert d["tabOn"] is True, "/tasks#activity opens the tab"
    assert d["order"] == ["run1", "bud1", "old1"], "active projects first"
    assert d["states"] == ["Running", "Paused", "Done"]
    assert d["buttons"]["run1"] == ["Pause", "Silence", "Files", "Stop"]
    assert d["buttons"]["bud1"] == ["Extend budget", "Turn notifications on", "Files", "Stop"]
    assert d["buttons"]["old1"] == ["Files"], "a finished project only lists its files"
    assert d["bar"] == ["2", "5", "40%"]
    assert "2 of 5 checkpoints" in d["meta"] and "running for 2 min" in d["meta"]
    assert d["last"] == "Checkpoint 2 done"
    assert d["crews"][0] == ["Daily briefing", "Running", ["Stop"]] and d["crews"][1][:2] == ["Deep research", "Interrupted"]
    assert d["chips"] == ["example.com", "web_search"]
    assert d["asks"] == ["Stop this project?", "Give it more steps?"]
    assert d["posts"] == ["/api/agents/run1/abort {}", '/api/agents/bud1/extend_budget {"additional_steps":20}',
                          '/api/agent_global_grants {"kind":"skills","value":"calculator"}']
