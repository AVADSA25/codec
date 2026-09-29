"""Settings > Memory (UI phase 2, P2.4; docs/P2.4-DESIGN.md).

Facts are superseded or closed, never deleted; standing rules, About me and the two
switches work; a reply knows what memory it was given; every change is audited
without values. Nothing here touches ~/.codec: tmp paths and fakes.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
SECRET = "lives at 12 Example Street"


@pytest.fixture
def mem(tmp_path, monkeypatch):
    import codec_memory_upgrade as cmu
    import codec_scheduler
    import codec_standing_rules as sr
    import routes.memory_page as mp
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".codec").mkdir()
    monkeypatch.setattr(cmu, "DB_PATH", str(tmp_path / "memory.db"))
    monkeypatch.setattr(cmu, "IDENTITY_PATH", str(tmp_path / "memory" / "identity.txt"))
    monkeypatch.setattr(sr, "RULES_PATH", tmp_path / "standing_rules.json")
    monkeypatch.setattr(codec_scheduler, "SCHEDULE_PATH", str(tmp_path / "schedules.json"))
    monkeypatch.setattr(mp, "CONFIG_PATH", str(tmp_path / ".codec" / "config.json"))
    audits = []
    monkeypatch.setattr(mp, "_audit", lambda action, **extra: audits.append((action, extra)))
    app = FastAPI()
    app.include_router(mp.router)
    cmu.store_fact("home_city", "Marbella", fact_type="person")
    cmu.store_fact("address", SECRET, fact_type="person")
    cmu.store_fact("thread:working_on:launch-plan", "Launch plan", fact_type="thread", source="daybreak")
    return SimpleNamespace(client=TestClient(app), cmu=cmu, sr=sr, sched=codec_scheduler, audits=audits, tmp=tmp_path)


def _rows(tmp):
    with sqlite3.connect(tmp / "memory.db") as c:
        return c.execute("SELECT COUNT(*) FROM facts").fetchone()[0]


def test_facts_come_without_threads_which_are_listed_apart(mem):
    d = mem.client.get("/api/memory/facts").json()
    assert sorted(f["key"] for f in d["facts"]) == ["address", "home_city"]
    assert [t["text"] for t in d["threads"]] == ["Launch plan"]


def test_editing_a_fact_keeps_the_old_value_in_its_history(mem):
    c = mem.client
    assert c.put("/api/memory/facts", json={"key": "home_city", "value": "Malaga"}).json() == {"ok": True, "changed": True}
    versions = c.get("/api/memory/facts/history", params={"key": "home_city"}).json()["versions"]
    assert [v["value"] for v in versions] == ["Malaga", "Marbella"] and versions[1]["superseded_by"] == versions[0]["id"]
    assert c.put("/api/memory/facts", json={"key": "home_city", "value": "Malaga"}).json()["changed"] is False
    for bad, code in (({"key": "home_city", "value": " "}, 400), ({"key": "thread:working_on:launch-plan", "value": "x"}, 400),
                      ({"key": "nope", "value": "x"}, 404), ({"key": "home_city", "value": "x" * 1001}, 400)):
        assert c.put("/api/memory/facts", json=bad).status_code == code, bad


def test_forgetting_closes_a_fact_and_deletes_nothing(mem):
    before = _rows(mem.tmp)
    assert mem.client.post("/api/memory/facts/forget", json={"key": "address"}).json() == {"ok": True}
    assert "address" not in [f["key"] for f in mem.client.get("/api/memory/facts").json()["facts"]]
    hist = mem.client.get("/api/memory/facts/history", params={"key": "address"}).json()["versions"]
    assert hist[0]["value"] == SECRET and hist[0]["valid_until"] and _rows(mem.tmp) == before
    assert mem.client.post("/api/memory/facts/forget", json={"key": "address"}).status_code == 404


def test_standing_rules_add_remove_and_clear_with_the_skills_limits(mem):
    c = mem.client
    rules = c.post("/api/memory/rules", json={"text": "Always answer in French"}).json()["rules"]
    assert [r["text"] for r in rules] == ["Always answer in French"]
    assert c.post("/api/memory/rules", json={"text": "always answer in french"}).json()["error"] == "You already have that rule."
    assert c.post("/api/memory/rules", json={"text": "x" * 501}).status_code == 400
    c.post("/api/memory/rules", json={"text": "Keep replies short"})
    assert c.delete("/api/memory/rules/" + rules[0]["id"]).json()["rules"][0]["text"] == "Keep replies short"
    assert c.delete("/api/memory/rules/r9_0").status_code == 404
    assert c.post("/api/memory/rules/clear").json()["rules"] == [] and c.get("/api/memory/rules").json()["rules"] == []


def test_about_me_is_capped_and_owner_only(mem):
    r = mem.client.put("/api/memory/about", json={"text": "  I build CODEC.\r\nShort answers, please.  "})
    assert r.json() == {"ok": True, "text": "I build CODEC.\nShort answers, please."}
    path = Path(mem.cmu.IDENTITY_PATH)
    assert path.read_text() == "I build CODEC.\nShort answers, please.\n" and stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert mem.client.get("/api/memory/about").json()["text"] == "I build CODEC.\nShort answers, please."
    assert mem.client.put("/api/memory/about", json={"text": "x" * 1501}).status_code == 400


def test_the_fact_extract_switch_stops_the_skill(mem, monkeypatch):
    spec = importlib.util.spec_from_file_location("fact_extract_under_test", REPO / "skills" / "fact_extract.py")
    fx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fx)
    asked = []
    monkeypatch.setattr(fx, "_call_llm", lambda prompt: asked.append(prompt) or "__ERR__:offline")
    text = "extract facts from: Ana moved to Lisbon in March and now runs a small bakery there."
    assert fx.run(text).startswith("LLM extraction failed") and len(asked) == 1, "on by default"
    assert mem.client.put("/api/memory/settings", json={"fact_extract": False}).json()["fact_extract"] is False
    assert json.loads((mem.tmp / ".codec" / "config.json").read_text())["memory"] == {"fact_extract": False}
    assert fx.run(text) == "Saving facts is switched off (Settings > Memory > Save facts from text)." and len(asked) == 1


def test_the_nightly_switch_manages_one_silent_job(mem):
    s = mem.client.put("/api/memory/settings", json={"nightly": True}).json()
    assert s["nightly"] is True and s["nightly_when"] == "Every day at 03:30"
    job, = mem.sched.load_schedules()
    assert (job["id"], job["skill"], job["deliver"], job["managed"], job["enabled"]) == \
        ("sched_auto_memorize", "auto_memorize", ["silent"], "memory", True)
    assert (job["hour"], job["minute"], job["days"]) == (3, 30, [0, 1, 2, 3, 4, 5, 6])
    assert mem.client.put("/api/memory/settings", json={"nightly": False}).json()["nightly"] is False
    job, = mem.sched.load_schedules()
    assert job["enabled"] is False
    assert [a[0] for a in mem.audits] == ["nightly_switched", "nightly_switched"]


def test_silent_delivery_sends_nothing(mem, monkeypatch):
    import codec_today
    sent = []
    monkeypatch.setattr(mem.sched, "_notify", lambda *a, **k: sent.append("notify"))
    monkeypatch.setattr(codec_today, "add_card", lambda *a, **k: sent.append("card"))
    mem.sched._deliver({"id": "sched_auto_memorize", "deliver": ["silent"]}, "Learn", "3 facts saved")
    assert sent == []
    mem.sched._deliver({"id": "x", "deliver": []}, "t", "out")
    assert sent == ["notify"], "an empty delivery still means a notification"


def test_enrich_messages_reports_what_memory_it_used(monkeypatch):
    import codec_memory
    import routes.chat as chat
    import routes.qchat as qchat

    class FakeMemory:
        def get_context(self, text, n=8):
            return "line one\nline two"

        def search_recent(self, days=3, limit=5):
            return [{"timestamp": "2026-09-28T10:00:00", "role": "user", "content": "the garden needs water"}]

    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("CREATE TABLE qchat_messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT, timestamp TEXT)")
    db.executemany("INSERT INTO qchat_messages (role, content, timestamp) VALUES (?,?,?)",
                   [("user", "last week: do you remember the plumber from Tuesday", "2026-09-28T09:00"),
                    ("assistant", "yes, do you remember the plumber who fixed the sink", "2026-09-28T09:01")])
    monkeypatch.setattr(codec_memory, "CodecMemory", FakeMemory)
    monkeypatch.setattr(qchat, "qchat_db", lambda: db)
    meta: dict = {}
    out = chat._enrich_messages([{"role": "user", "content": "do you remember the plumber"}], {}, meta=meta)
    assert len(out) == 2
    got = {(m["source"], m["kind"]): m["count"] for m in meta["memory"]}
    assert got == {("voice", "relevant"): 2, ("voice", "recent"): 1, ("chat", "relevant"): 2, ("chat", "recent"): 2}
    meta = {}
    chat._enrich_messages([{"role": "user", "content": "hello"}], {}, meta=meta)
    assert {(m["source"], m["kind"]) for m in meta["memory"]} == {("voice", "recent"), ("chat", "recent")}
    assert chat._enrich_messages([{"role": "user", "content": "hello"}], {}) == chat._enrich_messages(
        [{"role": "user", "content": "hello"}], {}), "no meta: unchanged behaviour"


def test_the_chat_passes_the_memory_on_and_the_pages_show_it():
    src = (REPO / "routes" / "chat.py").read_text(encoding="utf-8")
    assert "messages = _enrich_messages(messages, config, force_search=bool(force_search), meta=_mem_meta)" in src
    assert "config, _budget, has_attachment, last_user_text, meta=_mem_meta" in src
    assert "yield f\"data: {json.dumps({'memory': _mem_items})}\\n\\n\"" in src and 'out["memory"] = _mem_items' in src
    assert '_note_memory(meta, "rules", "standing"' in src and '_note_memory(meta, "threads", "open"' in src
    page = (REPO / "codec_chat.html").read_text(encoding="utf-8")
    assert page.count("if(j.memory){_lastMemory=j.memory}") == 2 and "function renderMemoryUsed(msgEl, items)" in page
    assert page.count("renderMemoryUsed(md") == 2 and "a.href = '/#memory';" in page
    home = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")
    for needle in ('id="panel-memory"', 'data-sub="memory"', "var _SETTINGS_GROUP = { settings: 1, memory: 1,",
                   "if (id === 'memory') loadMemoryPage();", "_memJSON('/api/memory/facts')", "'/api/memory/facts/forget'",
                   "'/api/memory/facts/history?key='", "'/api/memory/rules'", "'/api/memory/about'", "'/api/memory/settings'",
                   "'/api/memory/search?limit=20&q='", "'/api/memory/rebuild'", "'/api/today/threads/done'",
                   'id="promptsSection"', "function openPrompts(ev)"):
        assert needle in home, needle
    mem_js = home[home.index("// ── Memory (P2.4"):home.index("async function loadCheckin()")]
    assert "confirm(" not in mem_js and "innerHTML = f." not in mem_js and ".innerHTML = r." not in mem_js
    tasks = (REPO / "codec_tasks.html").read_text(encoding="utf-8")
    assert "s.managed === 'memory' ? '/#memory' : '/#settings'" in tasks and "silent: 'Nothing (run history only)'" in tasks


def test_changes_are_audited_without_values(mem):
    c = mem.client
    c.put("/api/memory/facts", json={"key": "address", "value": SECRET + ", flat 2"})
    c.post("/api/memory/facts/forget", json={"key": "home_city"})
    c.post("/api/memory/rules", json={"text": "Never mention " + SECRET})
    c.put("/api/memory/about", json={"text": "I " + SECRET})
    c.put("/api/memory/settings", json={"fact_extract": True})
    assert [a[0] for a in mem.audits] == ["fact_edited", "fact_forgotten", "rule_added", "about_saved", "fact_extract_switched"]
    assert SECRET not in json.dumps(mem.audits) and "Marbella" not in json.dumps(mem.audits)
