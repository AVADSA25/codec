"""Settings > Learning (UI phase 3, P3.12; docs/P3.12-DESIGN.md).

The proposal list, the diary, the learned-fact list, fact_extract's structured
fact, Review then Approve through the existing gates, the proposal note, and the
page. Nothing here touches ~/.codec or a model: tmp folders and fakes.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
HOME = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")
GOOD = ('SKILL_NAME = "weather_plus"\nSKILL_DESCRIPTION = "Weather with the week ahead."\n'
        'SKILL_TRIGGERS = ["weather plus"]\n\n\ndef run(task, app="", ctx=""):\n    return "sunny"\n')
NOTE = ("# Proposal: `{name}`\n\n**Generated:** 2026-10-05T03:00:00+00:00\n**Gap kind:** missing_tool\n"
        "**Triggering tool:** {name}\n**Signal count:** 4\n\n## Validation\n\n- Status: {status}\n- Reason: {reason}\n")


def _proposal(root, date, name, code=GOOD, status="✅ PASSED", reason="clean", note=True):
    d = root / date
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.py").write_text(code)
    if note:
        (d / f"{name}.md").write_text(NOTE.format(name=name, status=status, reason=reason))


@pytest.fixture
def lrn(tmp_path, monkeypatch):
    import routes.learning as rl
    root = tmp_path / "skill_proposals"
    root.mkdir()
    monkeypatch.setattr(rl, "PROPOSALS_ROOT", str(root))
    monkeypatch.setattr(rl, "_installed", lambda: {"already_there"})
    app = FastAPI()
    app.include_router(rl.router)
    return SimpleNamespace(client=TestClient(app), rl=rl, root=root, tmp=tmp_path)


def test_the_proposal_list_reads_the_folder_safely(lrn):
    root = lrn.root
    _proposal(root, "2026-10-05", "weather_plus")
    _proposal(root, "2026-10-01", "weather_plus")
    _proposal(root, "2026-10-04", "bad_one", status="❌ REJECTED", reason="Blocked import: subprocess")
    _proposal(root, "2026-10-04", "already_there")
    _proposal(root, "2026-10-03", "no_note", note=False)
    _proposal(root, "2026-10-03", "huge", code="x" * (lrn.rl.CODE_MAX + 50))
    (root / "2026-10-04" / "notes.txt").write_text("not a skill")
    (root / "2026-10-04" / "bad-name!.py").write_text(GOOD)
    (root / "not-a-date").mkdir()
    _proposal(root / "not-a-date", "x", "hidden")
    outside = lrn.tmp / "outside"
    _proposal(outside, "2026-10-02", "linked")
    (root / "2026-10-02").symlink_to(outside / "2026-10-02")
    got = {p["name"]: p for p in lrn.client.get("/api/learning/proposals").json()["proposals"]}
    assert sorted(got) == ["already_there", "bad_one", "huge", "no_note", "weather_plus"], "names, dates and links checked"
    w = got["weather_plus"]
    assert (w["date"], w["earlier"], w["status"], w["reason"]) == ("2026-10-05", ["2026-10-01"], "passed", "")
    assert w["gap_kind"] == "missing tool" and w["tool"] == "weather_plus" and w["signals"] == "4" and w["code"] == GOOD
    assert got["bad_one"]["status"] == "rejected" and got["bad_one"]["reason"] == "Blocked import: subprocess"
    assert got["already_there"]["installed"] is True and got["weather_plus"]["installed"] is False
    assert got["no_note"]["status"] == "unknown"
    assert len(got["huge"]["code"]) == lrn.rl.CODE_MAX and got["huge"]["truncated"] is True


def test_the_diary_is_built_from_facts_proposals_and_the_audit_log(lrn, monkeypatch):
    import codec_audit
    today = datetime.now().strftime("%Y-%m-%d")
    three = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    old = (datetime.now() - timedelta(days=20)).isoformat()
    monkeypatch.setattr(lrn.rl, "learned_facts", lambda limit=200: [
        {"key": "learned:a", "value": "Juan is launching NovaPay", "valid_from": datetime.now().isoformat()},
        {"key": "learned:b", "value": "An old fact", "valid_from": old}])
    _proposal(lrn.root, today, "weather_plus")
    _proposal(lrn.root, three, "bad_one", status="❌ REJECTED", reason="no")
    now_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    monkeypatch.setattr(codec_audit, "read_events", lambda **kw: [
        {"ts": now_utc, "event": "skill_approved", "extra": {"filename": "weather_plus.py"}},
        {"ts": now_utc, "event": "skill_review_rejected", "extra": {"filename": "other.py"}},
        {"ts": now_utc, "event": "tool_result", "extra": {"filename": "x.py"}}])
    monkeypatch.setattr(lrn.rl, "older_fact_count", lambda: 28)
    d = lrn.client.get("/api/learning/diary?days=14").json()
    assert [x["date"] for x in d["days"]] == [today, three], "only days with something in them, newest first"
    t, b = d["days"]
    assert t["facts"] == ["Juan is launching NovaPay"] and t["proposed"] == [{"name": "weather_plus", "status": "passed"}]
    assert t["approved"] == ["weather_plus"] and t["turned_down"] == ["other"]
    assert b["proposed"] == [{"name": "bad_one", "status": "rejected"}] and d["older_facts"] == 28


def test_the_learned_fact_list_and_the_older_count(lrn, monkeypatch, tmp_path):
    import codec_config
    import codec_memory_upgrade as cmu
    monkeypatch.setattr(cmu, "query_valid_facts", lambda key=None, limit=50, **kw: [
        {"key": "learned:a", "value": "A", "valid_from": "2026-10-01T10:00:00", "source": "fact_extract"},
        {"key": "pref", "value": "dark mode", "valid_from": "2026-10-05T10:00:00", "source": "mcp"},
        {"key": "learned:b", "value": "B", "valid_from": "2026-10-04T10:00:00", "source": "fact_extract"},
        {"key": "learned:c", "value": "C, edited", "valid_from": "2026-10-03T10:00:00", "source": "memory page"}])
    keys = [f["key"] for f in lrn.client.get("/api/learning/facts").json()["facts"]]
    assert keys == ["learned:b", "learned:c", "learned:a"], "an edited learned fact stays on the list"
    db = tmp_path / "memory.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE conversations (session_id TEXT, timestamp TEXT, role TEXT, content TEXT, user_id TEXT)")
    con.executemany("INSERT INTO conversations VALUES (?,?,?,?,?)", [
        ("fact_extract", "t", "fact", "A", "u"), ("fact_extract", "t", "fact", "Old one", "u"),
        ("fact_extract", "t", "fact", "Old one", "u"), ("s1", "t", "user", "hello", "u")])
    con.commit()
    con.close()
    monkeypatch.setattr(codec_config, "DB_PATH", str(db))
    assert lrn.rl.older_fact_count() == 1, "conversation-only facts, once each, without the structured ones"


def _fact_extract():
    spec = importlib.util.spec_from_file_location("fact_extract_p312", REPO / "skills" / "fact_extract.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fact_extract_now_keeps_a_structured_fact(monkeypatch):
    import codec_memory
    import codec_memory_upgrade as cmu
    saved, stored, active = [], [], set()

    class FakeMemory:
        def save(self, **kw):
            saved.append(kw)

    monkeypatch.setattr(codec_memory, "CodecMemory", FakeMemory)
    monkeypatch.setattr(cmu, "query_valid_facts", lambda key=None, limit=50, **kw: [{"key": key}] if key in active else [])

    def fake_store(key, value, **kw):
        stored.append((key, value, kw))
        active.add(key)

    monkeypatch.setattr(cmu, "store_fact", fake_store)
    fe = _fact_extract()
    assert fe._save("Juan is launching NovaPay in Q3") is True
    assert fe._save("Juan is launching NovaPay in Q3") is True
    key = "learned:" + hashlib.sha1(b"Juan is launching NovaPay in Q3").hexdigest()[:10]
    assert stored == [(key, "Juan is launching NovaPay in Q3", {"fact_type": "learned", "source": "fact_extract"})], \
        "one structured fact for the same text"
    assert [s["role"] for s in saved] == ["fact", "fact"], "the conversation row is still written, as before"


def test_review_then_approve_go_through_the_existing_gates(tmp_path, monkeypatch):
    import routes.skills as rs
    skills_dir = tmp_path / "skills"
    monkeypatch.setattr(rs, "_reviews_dir", lambda: str(tmp_path / "reviews"))
    monkeypatch.setattr(rs, "_get_skills_dir", lambda: str(skills_dir))
    monkeypatch.setattr(rs, "_pending_skills", {})
    app = FastAPI()
    app.include_router(rs.router)
    c = TestClient(app)
    rid = c.post("/api/skill/review", json={"code": GOOD, "filename": "weather_plus.py"}).json()["review_id"]
    assert [r["id"] for r in c.get("/api/skill/reviews").json()["reviews"]] == [rid]
    assert c.post("/api/skill/approve", json={"review_id": rid}).status_code == 200
    assert (skills_dir / "weather_plus.py").read_text() == GOOD
    bad = GOOD.replace('return "sunny"', 'import subprocess\n    return subprocess.check_output(["ls"]).decode()')
    rid = c.post("/api/skill/review", json={"code": bad, "filename": "sneaky.py"}).json()["review_id"]
    r = c.post("/api/skill/approve", json={"review_id": rid})
    assert r.status_code == 400 and "Blocked" in r.json()["error"] and not (skills_dir / "sneaky.py").exists()


def test_a_new_proposal_note_points_to_the_page(tmp_path):
    import codec_self_improve as si
    si._write_proposal(tmp_path, "weather_plus", GOOD, {"kind": "missing_tool", "tool": "weather_plus", "count": 4},
                       True, "")
    md = (tmp_path / "weather_plus.md").read_text()
    assert "Open Settings > Learning in CODEC, Review it, then Approve and install." in md and "✅ PASSED" in md


def test_the_learning_page_is_wired():
    for needle in ('data-sub="learning" id="subnav-learning"', 'id="panel-learning"', 'id="learnProposals"',
                   'id="learnDiary"', 'id="learnFacts"', "learning: 1,", "if (id === 'learning') loadLearning();"):
        assert needle in HOME, needle
    block = HOME[HOME.index("// ── Learning (P3.12"):HOME.index("function loadMemoryPage()")]
    for url in ("'/api/learning/proposals'", "'/api/learning/diary?days=14'", "'/api/learning/facts'",
                "'/api/skill/reviews'", "'/api/skill/review'", "'/api/skill/approve'", "'/api/skill/reject/'",
                "'/api/memory/facts'", "'/api/memory/facts/forget'"):
        assert url in block, url
    assert "else if (p.status !== 'rejected') acts.appendChild(_memBtn('Review'" in block, "no Review for a failed check"
    known = (REPO / "docs" / "known-issues.md").read_text(encoding="utf-8")
    assert "fact_extract writes a fixed user_id" in known


def _jsdom_available() -> bool:
    if shutil.which("node") is None:
        return False
    r = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True, cwd=REPO,
                       env=os.environ.copy(), timeout=20)
    return r.returncode == 0


_JS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const src = fs.readFileSync(process.argv[1], 'utf8');
const code = src.slice(src.indexOf('function _memMsg(id, text, err)'), src.indexOf('function loadMemoryPage()'));
const html = src.slice(src.indexOf('<div class="panel" id="panel-learning">'), src.indexOf('<style>', src.indexOf('<div class="panel" id="panel-learning">')));
const dom = new JSDOM('<!doctype html><body>' + html + '</body>', { runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window, calls = [];
const today = new Date().toISOString().slice(0, 10);
const data = {
  '/api/learning/proposals': { proposals: [
    { name: 'weather_plus', date: '2026-10-05', earlier: ['2026-10-01'], status: 'passed', reason: '', gap_kind: 'missing tool', tool: 'weather_plus', signals: '4', code: 'SKILL_NAME = "weather_plus"', truncated: false, installed: false },
    { name: 'bad_one', date: '2026-10-04', earlier: [], status: 'rejected', reason: 'Blocked import: subprocess', gap_kind: '', tool: '', signals: '', code: 'x', truncated: false, installed: false }] },
  '/api/learning/diary?days=14': { days: [{ date: today, facts: ['Juan is launching NovaPay'], proposed: [{ name: 'weather_plus', status: 'passed' }], approved: [], turned_down: ['other'] }], older_facts: 28 },
  '/api/learning/facts': { facts: [{ key: 'learned:a', value: 'Juan is launching NovaPay', valid_from: today + 'T10:00:00' }] },
  '/api/skill/reviews': { reviews: [] },
  '/api/skill/review': { review_id: 'r1' },
  '/api/skill/approve': { path: '/tmp/x', skill: 'weather_plus.py' } };
w.fetch = (u, o) => { calls.push([u, (o && o.method) || 'GET', (o && o.body) || '']);
  return Promise.resolve({ ok: true, json: () => Promise.resolve(data[u] || {}) }); };
w.CodecShell = { watchDialogs() {} };
w.eval(code);
(async () => {
  const d = w.document, out = {}, tick = () => new Promise(r => setTimeout(r, 40));
  w.loadLearning();
  await tick();
  const rows = [...d.querySelectorAll('#learnProposals .mem-fact')];
  out.rows = rows.map(r => [r.querySelector('.mem-val').textContent, [...r.querySelectorAll('button')].map(b => b.textContent)]);
  out.meta = rows.map(r => [...r.querySelectorAll('.mem-meta')].map(m => m.textContent));
  out.diary = d.getElementById('learnDiary').textContent;
  out.facts = [...d.querySelectorAll('#learnFacts .mem-fact')].map(r => [r.querySelector('.mem-val').textContent, [...r.querySelectorAll('button')].map(b => b.textContent)]);
  rows[0].querySelector('button').click();
  await tick();
  out.dialog = [d.getElementById('learnBox').classList.contains('open'), d.getElementById('learnTitle').textContent, d.getElementById('learnCode').textContent];
  d.getElementById('learnYes').click();
  await tick();
  out.closed = d.getElementById('learnWrap').hidden;
  out.msg = d.getElementById('learnMsg').textContent;
  out.writes = calls.filter(c => c[1] !== 'GET').map(c => c[0] + ' ' + c[2]);
  console.log(JSON.stringify(out)); process.exit(0);
})().catch(e => { console.log('ERR ' + e.message); process.exit(1); });
setTimeout(() => { console.log('TIMEOUT'); process.exit(2); }, 20000);
"""


@pytest.mark.skipif(not _jsdom_available(), reason="node with jsdom not resolvable (set NODE_PATH)")
def test_the_learning_page_reviews_and_approves_under_jsdom():
    out = subprocess.run(["node", "-e", _JS, str(REPO / "codec_dashboard.html")], capture_output=True, text=True,
                         cwd=REPO, env=os.environ.copy(), timeout=60)
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("{") or ln.startswith("ERR") or ln == "TIMEOUT"][-1]
    d = json.loads(line)
    assert d["rows"] == [["weather_plus", ["Review"]], ["bad_one", []]], "no Review for a failed safety check"
    assert d["meta"][0][0].startswith("proposed 2026-10-05 (and on 1 earlier day)") and "4 times" in d["meta"][0][0]
    assert d["meta"][1][1] == "Failed the safety check: Blocked import: subprocess."
    assert "Learned: Juan is launching NovaPay" in d["diary"] and "You turned down the skill other." in d["diary"]
    assert "28 facts learned before this version" in d["diary"]
    assert d["facts"] == [["Juan is launching NovaPay", ["Edit", "Forget"]]]
    assert d["dialog"] == [True, "Review weather_plus", 'SKILL_NAME = "weather_plus"']
    assert d["writes"] == ['/api/skill/review {"code":"SKILL_NAME = \\"weather_plus\\"","filename":"weather_plus.py"}',
                           '/api/skill/approve {"review_id":"r1"}']
    assert d["closed"] is True and d["msg"].startswith("weather_plus is installed.")
