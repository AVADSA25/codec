"""Vibe V4 (P-077, docs/VIBE-V4-DESIGN.md): rewind, undo rewind, fork, rename, export, private chats, purge to the
trash first, retention, the not-versioned count and "where you left off".

Every test has a temporary CODEC_VIBE_ROOT and CODEC_VIBE_TRASH (tests/vibe_agent_fakes.agent_env), a temporary
vibe.db (routes.vibe.VIBE_DB patched, _vibe_conn reset) and a fake config; the real ~/.codec is never touched.
No real model: the agent talks to a scripted one, and "where you left off" to one that fails if it is called.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codec_vibe_agent as va
import codec_vibe_projects as vp
import routes.vibe as rv
from vibe_agent_fakes import (  # noqa: F401  (fixtures are used by name)
    agent_env, audits, cfg, end_of, make_project, run_script, scripted,
)

REPO = Path(__file__).resolve().parent.parent
PAGE = (REPO / "codec_vibe.html").read_text(encoding="utf-8")
PROJ_JS = REPO / "static" / "vibe-projects.js"
node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture
def vcfg():
    """config.json as routes.vibe reads it; a test changes it in place."""
    return {}


@pytest.fixture(autouse=True)
def db(agent_env, tmp_path, monkeypatch, vcfg):
    monkeypatch.setattr(rv, "VIBE_DB", str(tmp_path / "vibe.db"))
    monkeypatch.setattr(rv, "_vibe_conn", None)
    monkeypatch.setattr(rv, "_last_sweep", 0.0)
    monkeypatch.setattr(rv, "_cfg", lambda: vcfg)
    monkeypatch.setattr(rv, "_audit", lambda event, message, extra: agent_env.append((event, dict(extra))))
    yield tmp_path / "vibe.db"
    if rv._vibe_conn is not None:
        rv._vibe_conn.close()
        rv._vibe_conn = None


@pytest.fixture
def client():
    import routes.vibe_agent as rva
    import routes.vibe_projects as rvp
    app = FastAPI()
    for r in (rv.router, rvp.router, rva.router):
        app.include_router(r)
    return TestClient(app)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _disk(pid) -> dict:
    """sha256 of every versioned file on disk."""
    pdir = vp.project_dir(pid)
    return {f["path"]: _sha((pdir / f["path"]).read_bytes()) for f in vp.list_files(pid)}


def _cps(pid) -> list[dict]:
    return vp._load_checkpoints(vp.project_dir(pid))          # oldest first, with the file maps


def _cp(pid, cid) -> dict:
    return next(c for c in _cps(pid) if c["id"] == cid)


def _rows(sid="s1"):
    return rv.vibe_db().execute("SELECT id, role, content, checkpoint_id, archived FROM vibe_messages WHERE session_id=? "
                                "ORDER BY id", (sid,)).fetchall()


def _save(client, messages, sid="s1", **over):
    body = {"session_id": sid, "title": "Site", "language": "html", "code": "", "messages": messages, "full_sync": True}
    body.update(over)
    r = client.post("/api/vibe/save", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _chat(client, sid="s1"):
    """A project with two chat answers, each behind its own checkpoint, saved as a four-row chat."""
    pid = vp.create_project("Site", [{"path": "index.html", "content": "<h1>one</h1>\n"}])["id"]
    a = vp.apply_blocks(pid, [{"path": "index.html", "content": "<h1>two</h1>\n"}], "make two")["checkpoint"]["id"]
    b = vp.apply_blocks(pid, [{"path": "index.html", "content": "<h1>three</h1>\n"},
                              {"path": "style.css", "content": "h1 { color: red; }\n"}], "make three")["checkpoint"]["id"]
    msgs = [{"role": "user", "content": "make two"},
            {"role": "assistant", "content": "Two.\n[Files written: index.html]", "checkpoint_id": a},
            {"role": "user", "content": "make three"},
            {"role": "assistant", "content": "Three.\n[Files written: index.html, style.css]", "checkpoint_id": b}]
    _save(client, msgs, sid=sid, project_id=pid)
    return pid, a, b, msgs


# ── vibe.db ──────────────────────────────────────────────────────────────

def test_an_older_vibe_db_is_copied_once_0600_before_the_two_columns_are_added(db):
    old = sqlite3.connect(db)
    old.execute("CREATE TABLE vibe_sessions (id TEXT PRIMARY KEY, title TEXT, language TEXT, code TEXT, created_at TEXT, updated_at TEXT)")
    old.execute("CREATE TABLE vibe_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp TEXT)")
    old.execute("INSERT INTO vibe_sessions VALUES ('s1', 'Old', 'python', 'x = 1', 't', 't')")
    old.executemany("INSERT INTO vibe_messages (session_id, role, content, timestamp) VALUES ('s1', ?, ?, 't')",
                    [("user", "hello"), ("assistant", "hi")])
    old.commit()
    old.close()
    conn = rv.vibe_db()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(vibe_messages)")}
    assert {"checkpoint_id", "archived"} <= cols
    assert conn.execute("SELECT role, content, checkpoint_id, archived FROM vibe_messages ORDER BY id").fetchall() == [
        ("user", "hello", None, 0), ("assistant", "hi", None, 0)]
    copies = sorted(db.parent.glob("vibe.db.pre-v4-*.bak"))
    assert len(copies) == 1 and stat.S_IMODE(copies[0].stat().st_mode) == 0o600
    kept = sqlite3.connect(copies[0])
    assert kept.execute("SELECT content FROM vibe_messages ORDER BY id").fetchall() == [("hello",), ("hi",)]
    assert "archived" not in {r[1] for r in kept.execute("PRAGMA table_info(vibe_messages)")}, "the copy is from before"
    kept.close()
    conn.close()
    rv._vibe_conn = None
    rv.vibe_db()                                                     # the next start: nothing to add, nothing copied
    assert len(list(db.parent.glob("vibe.db.pre-v4-*.bak"))) == 1


def test_a_new_vibe_db_gets_the_columns_without_a_copy(db):
    rv.vibe_db()
    assert list(db.parent.glob("vibe.db.pre-v4-*")) == []


def test_a_save_keeps_the_rows_it_did_not_change_and_their_ids(client):
    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b", "checkpoint_id": "c0002"},
            {"role": "user", "content": "c", "checkpoint_id": "../x"}]
    _save(client, msgs)
    first = _rows()
    assert [r[3] for r in first] == [None, "c0002", None], "a checkpoint id that is not c + digits is not stored"
    _save(client, msgs + [{"role": "assistant", "content": "d"}])
    again = _rows()
    assert [r[0] for r in again[:3]] == [r[0] for r in first] and again[3][2] == "d"
    _save(client, msgs[:1] + [{"role": "assistant", "content": "B"}])
    last = _rows()
    assert last[0][0] == first[0][0] and [r[2] for r in last] == ["a", "B"]


# ── rewind and undo rewind ───────────────────────────────────────────────

def test_rewind_restores_the_answers_files_hides_the_later_rows_and_undo_brings_both_back(client):
    pid, a, b, msgs = _chat(client)
    before = _disk(pid)
    count = len(_cps(pid))
    r = client.post("/api/vibe/session/s1/rewind", json={"index": 1, "checkpoint": a})
    assert r.status_code == 200, r.text
    assert r.json()["hidden"] == 2 and r.json()["restored_to"] == a
    rows = _rows()
    assert len(rows) == 4 and [r[4] for r in rows] == [0, 0, 1, 1], "hidden, never deleted"
    assert _disk(pid) == _cp(pid, a)["files"], "the files are the answer's checkpoint"
    rec = _cps(pid)[-1]
    assert len(_cps(pid)) == count + 1 and rec["kind"] == "restore" and rec["label"].startswith("Rewound: ")
    assert rec["rewind"] == {"session": "s1", "rows": [rows[2][0], rows[3][0]]}
    s = client.get("/api/vibe/session/s1").json()
    assert [m["content"] for m in s["messages"]] == ["make two", "Two.\n[Files written: index.html]"]
    assert s["messages"][1]["checkpoint_id"] == a and s["hidden"] == 2
    assert s["rewind"] == {"checkpoint": rec["id"], "hidden": 2}
    marked = {c["id"]: c.get("rewound", False) for c in client.get(f"/api/vibe/projects/{pid}/checkpoints").json()}
    assert marked[b] is True and marked[a] is False and marked[rec["id"]] is False
    _save(client, msgs[:2], project_id=pid)                         # the page saves what it shows
    assert len(_rows()) == 4, "a save never touches hidden rows"

    r = client.post("/api/vibe/session/s1/rewind/undo", json={"checkpoint": rec["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["shown"] == 2
    assert _disk(pid) == before, "file hashes equal to before the rewind"
    s = client.get("/api/vibe/session/s1").json()
    assert [m["content"] for m in s["messages"]] == [m["content"] for m in msgs] and s["hidden"] == 0 and s["rewind"] is None
    assert [r[0] for r in _rows()] == [r[0] for r in rows], "the rows come back in their place"
    assert _cps(pid)[-1]["unrewind"] == rec["id"]
    assert not any(c.get("rewound") for c in client.get(f"/api/vibe/projects/{pid}/checkpoints").json())
    assert client.post("/api/vibe/session/s1/rewind/undo", json={"checkpoint": rec["id"]}).status_code == 409


def test_rewind_refuses_a_row_that_does_not_hold_that_checkpoint(client):
    pid, a, b, _ = _chat(client)
    assert client.post("/api/vibe/session/s1/rewind", json={"index": 3, "checkpoint": a}).status_code == 409
    assert client.post("/api/vibe/session/s1/rewind", json={"index": 1, "checkpoint": "x"}).status_code == 400
    assert all(r[4] == 0 for r in _rows()) and _disk(pid) == _cp(pid, b)["files"]


def test_undo_rewind_is_not_offered_once_the_chat_went_on(client):
    pid, a, _, msgs = _chat(client)
    rid = client.post("/api/vibe/session/s1/rewind", json={"index": 1, "checkpoint": a}).json()["checkpoint"]["id"]
    _save(client, msgs[:2] + [{"role": "user", "content": "something else"}], project_id=pid)
    assert client.get("/api/vibe/session/s1").json()["rewind"] is None
    r = client.post("/api/vibe/session/s1/rewind/undo", json={"checkpoint": rid})
    assert r.status_code == 409 and "went on" in r.json()["error"]
    assert sum(r[4] for r in _rows()) == 2, "the hidden rows stay saved"


def test_rewind_to_an_agent_runs_end_restores_it_and_the_later_run_is_marked_rewound(client, scripted):
    pid = make_project()
    va_ = run_script(pid, ["@@ write_file a.txt\n<<<<<<< CONTENT\nfirst\n>>>>>>> END\n", "@@ finish\nWrote a.\n"], scripted, goal="Write a")[0]
    vb = run_script(pid, ["@@ write_file b.txt\n<<<<<<< CONTENT\nsecond\n>>>>>>> END\n", "@@ finish\nWrote b.\n"], scripted, goal="Write b")[0]
    end_a = [e["id"] for e in va_["events"] if e["type"] == "checkpoint"][-1]
    end_b = [e["id"] for e in vb["events"] if e["type"] == "checkpoint"][-1]
    _save(client, [{"role": "user", "content": "Write a"}, {"role": "assistant", "content": "Wrote a.", "checkpoint_id": end_a},
                   {"role": "user", "content": "Write b"}, {"role": "assistant", "content": "Wrote b.", "checkpoint_id": end_b}],
          project_id=pid)
    assert client.post("/api/vibe/session/s1/rewind", json={"index": 1, "checkpoint": end_a}).status_code == 200
    assert _disk(pid) == _cp(pid, end_a)["files"] and "b.txt" not in _disk(pid)
    runs = {r["goal"]: r for r in client.get(f"/api/vibe/projects/{pid}/agent/runs").json()}
    assert runs["Write b"]["rewound"] is True and runs["Write a"]["rewound"] is False
    assert (vp.project_dir(pid) / ".vibe" / "runs" / f"{vb['run']}.jsonl").is_file(), "the later run's log is kept"
    changes = client.get(f"/api/vibe/projects/{pid}/checkpoints/{end_a}/changes").json()
    start_a = end_of(va_)["start_checkpoint"]
    assert changes["before"] == start_a and changes["run"] == va_["run"]
    assert [c["path"] for c in changes["changed"]] == ["a.txt"], "a run's chips count from its start"


def test_a_rewind_that_would_hide_more_rows_than_its_record_keeps_changes_nothing(client, monkeypatch):
    pid, a, b, _ = _chat(client)
    monkeypatch.setattr(vp, "MAX_REWIND_ROWS", 1)
    r = client.post("/api/vibe/session/s1/rewind", json={"index": 1, "checkpoint": a})
    assert r.status_code == 409 and "at most 1" in r.json()["error"]
    assert all(x[4] == 0 for x in _rows()) and _disk(pid) == _cp(pid, b)["files"] and _cps(pid)[-1]["id"] == b


def test_with_projects_switched_off_rewind_and_undo_answer_404_and_rename_leaves_the_project(client, monkeypatch):
    pid, a, b, _ = _chat(client)
    rid = client.post("/api/vibe/session/s1/rewind", json={"index": 1, "checkpoint": a}).json()["checkpoint"]["id"]
    before = (_disk(pid), len(_cps(pid)), _rows())
    monkeypatch.setenv("VIBE_PROJECTS_ENABLED", "false")
    assert client.post("/api/vibe/session/s1/rewind", json={"index": 1, "checkpoint": a}).status_code == 404
    assert client.post("/api/vibe/session/s1/rewind/undo", json={"checkpoint": rid}).status_code == 404
    assert client.get("/api/vibe/session/s1").json()["rewind"] is None
    r = client.post("/api/vibe/session/s1/rename", json={"title": "Offline name"})
    assert r.status_code == 200 and vp.project_info(pid)["title"] == "Site", "project.json is not written"
    assert (_disk(pid), len(_cps(pid)), _rows()) == before, "no file, checkpoint or row changed while switched off"
    monkeypatch.delenv("VIBE_PROJECTS_ENABLED")
    assert client.post("/api/vibe/session/s1/rewind/undo", json={"checkpoint": rid}).status_code == 200


def test_a_reloaded_chat_answer_gets_its_chips_and_revert_from_its_checkpoint(client):
    pid, a, b, _ = _chat(client)
    d = client.get(f"/api/vibe/projects/{pid}/checkpoints/{b}/changes").json()
    assert d["checkpoint"]["id"] == b and d["before"] == _cp(pid, b)["parent"] and d["run"] is None
    assert {c["path"]: c["status"] for c in d["changed"]} == {"index.html": "changed", "style.css": "added"}
    assert client.get(f"/api/vibe/projects/{pid}/checkpoints/c9999/changes").status_code == 404


# ── fork, rename, export ─────────────────────────────────────────────────

def test_fork_holds_exactly_that_checkpoints_files_and_the_history_up_to_it(client):
    pid, a, b, _ = _chat(client)
    before = (_disk(pid), [c["id"] for c in _cps(pid)])
    r = client.post(f"/api/vibe/projects/{pid}/fork", json={"checkpoint": a})
    assert r.status_code == 200, r.text
    new = r.json()["id"]
    assert new != pid and r.json()["title"] == "Site (copy)"
    want = _cp(pid, a)["files"]
    assert _disk(new) == want, "sha256 of every file equals the checkpoint"
    ids = [c["id"] for c in _cps(pid)]
    assert [c["id"] for c in _cps(new)] == ids[:ids.index(a) + 1]
    assert (_disk(pid), ids) == before, "the original is unchanged"
    meta = json.loads((vp.project_dir(new) / ".vibe" / "project.json").read_text())
    assert meta["forked_from"] == {"project": pid, "checkpoint": a}
    assert vp.restore(new, _cps(new)[0]["id"])["restored_to"] == _cps(new)[0]["id"], "its history works"


def test_rename_changes_the_shown_name_only(client):
    pid, *_ = _chat(client)
    folder = vp.project_dir(pid)
    r = client.post("/api/vibe/session/s1/rename", json={"title": "  My   shop  "})
    assert r.status_code == 200 and r.json()["title"] == "My shop"
    assert vp.project_info(pid)["id"] == pid and vp.project_info(pid)["title"] == "My shop" and folder.is_dir()
    assert [s["title"] for s in client.get("/api/vibe/sessions").json()] == ["My shop"]
    _save(client, [{"role": "user", "content": "x"}], sid="s2", title="Single")
    assert client.post("/api/vibe/session/s2/rename", json={"title": "Solo"}).json()["title"] == "Solo"
    assert client.post("/api/vibe/session/s2/rename", json={"title": " "}).status_code == 400
    assert client.post("/api/vibe/session/nope/rename", json={"title": "x"}).status_code == 404


def test_export_is_a_markdown_file_of_the_visible_conversation(client):
    pid, a, _, _ = _chat(client)
    client.post("/api/vibe/session/s1/rewind", json={"index": 1, "checkpoint": a})
    r = client.get("/api/vibe/session/s1/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert r.headers["content-disposition"].startswith('attachment; filename="site-') and ".md" in r.headers["content-disposition"]
    text = r.text
    assert text.startswith("# Site\n") and "## You\n\nmake two" in text and "## CODEC\n\nTwo." in text
    assert "make three" not in text and "2 later messages were hidden by a rewind" in text
    assert client.get("/api/vibe/session/nope/export").status_code == 404


# ── an agent run holding the project ─────────────────────────────────────

def test_rewind_fork_rename_and_purge_answer_409_while_a_run_holds_the_project(client, db):
    pid, a, b, _ = _chat(client)
    vp.agent_claim(pid, "r1")
    try:
        assert client.post("/api/vibe/session/s1/rewind", json={"index": 1, "checkpoint": a}).status_code == 409
        assert client.post(f"/api/vibe/projects/{pid}/fork", json={"checkpoint": a}).status_code == 409
        assert client.post("/api/vibe/session/s1/rename", json={"title": "x"}).status_code == 409
        assert client.delete("/api/vibe/session/s1").status_code == 409
    finally:
        vp.agent_release(pid, "r1")
    assert len(_rows()) == 4 and all(r[4] == 0 for r in _rows()) and _disk(pid) == _cp(pid, b)["files"]
    assert vp.project_info(pid)["title"] == "Site" and len(vp.list_projects()) == 1
    assert not (Path(os.environ["CODEC_VIBE_TRASH"]) / "sessions").exists()


# ── private chats ────────────────────────────────────────────────────────

def test_a_private_chat_writes_no_row_and_its_run_no_log(client, scripted):
    pid = make_project()
    assert _save(client, [{"role": "user", "content": "secret"}], project_id=pid, temporary=True) == {"ok": True, "saved": False}
    conn = rv.vibe_db()
    assert conn.execute("SELECT COUNT(*) FROM vibe_sessions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM vibe_messages").fetchone()[0] == 0
    runs = vp.project_dir(pid) / ".vibe" / "runs"
    view, _ = run_script(pid, ["@@ write_file p.txt\n<<<<<<< CONTENT\nprivate\n>>>>>>> END\n", "@@ finish\nDone.\n"],
                         scripted, goal="Private goal", temporary=True)
    assert end_of(view)["report"] == "Done." and vp.read_file(pid, "p.txt")["content"] == "private\n"
    assert not runs.exists() or [p for p in runs.rglob("*") if p.is_file()] == [], "no run log, no kept output"
    assert va.list_runs(pid) == [] and va.left_off(pid)["run"] is None
    run_script(pid, ["@@ finish\nShared.\n"], scripted, goal="Normal goal")
    assert len(list(runs.glob("r*.jsonl"))) == 1, "a normal run still keeps its log"


# ── purge and retention ──────────────────────────────────────────────────

def test_purge_writes_the_trash_file_first_then_deletes_every_row(client, audits):
    pid, a, _, msgs = _chat(client)
    client.post("/api/vibe/session/s1/rewind", json={"index": 1, "checkpoint": a})
    assert client.delete("/api/vibe/session/s1").status_code == 200
    files = list((Path(os.environ["CODEC_VIBE_TRASH"]) / "sessions").glob("s1-*.json"))
    assert len(files) == 1 and stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(files[0].parent.stat().st_mode) == 0o700
    data = json.loads(files[0].read_text())
    assert data["reason"] == "owner" and data["session"]["project_id"] == pid
    assert [m["content"] for m in data["messages"]] == [m["content"] for m in msgs], "hidden rows go to the trash too"
    assert [m["archived"] for m in data["messages"]] == [0, 0, 1, 1]
    conn = rv.vibe_db()
    assert conn.execute("SELECT COUNT(*) FROM vibe_messages").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM vibe_sessions").fetchone()[0] == 0
    ev = [x for e, x in audits if e == "vibe_session_trash"]
    assert ev == [{"messages": 4, "reason": "owner"}]


def test_purge_deletes_nothing_when_the_trash_file_cannot_be_written(client, tmp_path, monkeypatch):
    _chat(client)
    (tmp_path / "blocker").write_text("a file where the trash folder would be")
    monkeypatch.setenv("CODEC_VIBE_TRASH", str(tmp_path / "blocker" / "trash"))
    r = client.delete("/api/vibe/session/s1")
    assert r.status_code == 500 and "nothing was deleted" in r.json()["error"]
    assert len(_rows()) == 4


def _age(sid, when):
    rv.vibe_db().execute("UPDATE vibe_sessions SET updated_at=? WHERE id=?", (when, sid))
    rv.vibe_db().commit()


def test_retention_is_off_by_default_and_deletes_nothing(client, audits):
    _save(client, [{"role": "user", "content": "old"}], sid="old")
    _age("old", "2020-01-01T00:00:00")
    assert rv.retention_sweep() == {"ran": False, "days": 0, "sessions": 0, "messages": 0, "skipped": 0}
    assert [s["id"] for s in client.get("/api/vibe/sessions").json()] == ["old"]
    assert not any(e == "vibe_retention_sweep" for e, _ in audits)
    for bad in (0, -3, True, "30", 1.5, 99999):
        assert rv.retention_days({"vibe": {"retention_days": bad}}) == 0, bad


def test_the_retention_sweep_trashes_idle_chats_first_and_writes_one_audit_line(client, audits, vcfg):
    _save(client, [{"role": "user", "content": "old"}, {"role": "assistant", "content": "reply"}], sid="old")
    _save(client, [{"role": "user", "content": "new"}], sid="new")
    held = make_project()
    _save(client, [{"role": "user", "content": "busy"}], sid="held", project_id=held)
    _age("old", "2020-01-01T00:00:00")
    _age("held", "2020-01-01T00:00:00")
    vcfg["vibe"] = {"retention_days": 30}
    vp.agent_claim(held, "r1")
    try:
        listed = [s["id"] for s in client.get("/api/vibe/sessions").json()]       # the sweep runs when the page lists
    finally:
        vp.agent_release(held, "r1")
    assert sorted(listed) == ["held", "new"]
    trash = list((Path(os.environ["CODEC_VIBE_TRASH"]) / "sessions").glob("old-*.json"))
    assert len(trash) == 1 and json.loads(trash[0].read_text())["reason"] == "retention"
    sweeps = [x for e, x in audits if e == "vibe_retention_sweep"]
    assert sweeps == [{"days": 30, "sessions": 1, "messages": 2, "skipped": 1}], "counts only"
    client.get("/api/vibe/sessions")
    assert len([e for e, _ in audits if e == "vibe_retention_sweep"]) == 1, "at most once every 6 hours"


# ── not versioned, where you left off ────────────────────────────────────

def test_the_versions_drawer_counts_the_files_that_are_not_versioned(client):
    pid = vp.create_project("Build", [{"path": "index.html", "content": "<h1>x</h1>\n"}])["id"]
    pdir = vp.project_dir(pid)
    for rel in ("dist/a.js", "dist/b.js", "build/out.txt", "coverage/lcov.info", "src/target/x.bin"):
        (pdir / rel).parent.mkdir(parents=True, exist_ok=True)
        (pdir / rel).write_text("x")
    (pdir / "big.bin").write_bytes(b"0" * (vp.MAX_FILE_BYTES + 1))
    os.symlink(pdir / "index.html", pdir / "link.html")
    u = client.get(f"/api/vibe/projects/{pid}/unversioned").json()
    assert u == {"count": 7, "folders": {"dist": 2, "build": 1, "coverage": 1, "target": 1}, "large": 1, "links": 1,
                 "capped": False}
    assert [f["path"] for f in vp.list_files(pid)] == ["index.html"]


def test_where_you_left_off_is_the_last_runs_own_report_with_no_model_call(client, scripted, monkeypatch):
    pid = make_project()
    assert client.get(f"/api/vibe/projects/{pid}/agent/left-off").json() == {"run": None, "live": False, "rewound": []}
    view, _ = run_script(pid, ["@@ finish\nMade the page blue.\n"], scripted, goal="Blue page")

    def no_model(*a, **k):
        raise AssertionError("where you left off must not call the model")
    monkeypatch.setattr(va, "_llm_stream", no_model)
    d = client.get(f"/api/vibe/projects/{pid}/agent/left-off").json()
    assert d["run"] == view["run"] and d["goal"] == "Blue page" and d["status"] == "done"
    assert d["report"] == "Made the page blue." and d["live"] is False and d["rewound"] == []


# ── the page ─────────────────────────────────────────────────────────────

def _fn(src, head):
    body = src[src.index(head):]
    return body[:body.index("\n}\n")]


def test_the_page_skips_every_save_in_a_private_chat_and_saves_before_a_rewind():
    save = _fn(PAGE, "function doSaveSes() {")
    assert save.index("if (_vibePrivate) return Promise.resolve(null);") < save.index("fetch('/api/vibe/save'")
    assert "return fetch('/api/vibe/save'" in save
    rw = _fn(PAGE, "async function rewindTo(index, cid, btn) {")
    assert rw.index("await doSaveSes();") < rw.index("/rewind'") and "await ldSes(sesId);" in rw
    assert "if (_vibePrivate || !window.VibeProj || !VibeProj.on()) return;" in rw
    assert "_setPrivate(false);" in _fn(PAGE, "function nS() {") and "_setPrivate(false);" in _fn(PAGE, "function ldSes(sid) {")
    assert "_setPrivate(true);" in _fn(PAGE, "function nPS() {") and 'onclick="nPS()"' in PAGE
    agent = (REPO / "static" / "vibe-agent.js").read_text(encoding="utf-8")
    assert "if (root._vibePrivate === true) body.temporary = true;" in agent


def test_the_page_does_not_save_while_it_reloads_after_a_rewind_or_its_undo():
    """Between the rewind (or undo) answer and ldSes, hist is not vibe.db's visible rows: a full sync then would copy
    the hidden messages back, or delete the ones the undo showed again."""
    save = _fn(PAGE, "function doSaveSes() {")
    assert save.index("if (_vibeResync) return Promise.resolve(null);") < save.index("fetch('/api/vibe/save'")
    for head, url in (("async function rewindTo(index, cid, btn) {", "/rewind'"),
                      ("async function undoRewind(cid, btn) {", "/rewind/undo'")):
        f = _fn(PAGE, head)
        assert f.index(url) < f.index("_vibeResync = true;") < f.index("await ldSes(sesId);"), head
    ld = _fn(PAGE, "function ldSes(sid) {")
    assert ld.index("hist.push(") < ld.index("_vibeResync = false;"), "cleared once hist holds the rows again"
    assert "_vibeResync = false;" in _fn(PAGE, "function nS() {")


def test_the_page_stores_the_checkpoint_and_draws_reloaded_answers_from_it():
    assert "checkpoint: done.applied && done.res && done.res.checkpoint ? done.res.checkpoint.id : null" in PAGE
    assert "VibeProj.messageNode(expl, done, hist.length - 1)" in PAGE
    ld = _fn(PAGE, "function ldSes(sid) {")
    assert "VibeProj.savedTurnNode(m.content || '', m.checkpoint, i, pid)" in ld
    assert "hist.push({role: m.role, content: m.content || '', checkpoint: m.checkpoint || null});" in ld
    assert "_rewindNote(d.rewind)" in ld
    dl = _fn(PAGE, "async function delSes(sid, pid) {")
    assert dl.index("'/api/vibe/session/'") < dl.index("'/api/vibe/projects/'"), "the chat goes first: a 409 stops both"


def _dom_script(body: str, fetch_reply: str = "{}") -> str:
    """vibe-projects.js in Node with a small fake DOM and one fetch answer."""
    return ("""
function El(tag) { this.tagName = tag; this.children = []; this.style = {}; this.className = ''; this._t = '';
  this.listeners = {}; this.attrs = {}; }
El.prototype.appendChild = function (c) { if (c.parent) c.parent.removeChild(c); c.parent = this; this.children.push(c); return c; };
El.prototype.removeChild = function (c) { this.children.splice(this.children.indexOf(c), 1); c.parent = null; };
El.prototype.insertBefore = function (c, ref) { c.parent = this; this.children.splice(Math.max(0, this.children.indexOf(ref)), 0, c); return c; };
El.prototype.addEventListener = function (t, f) { this.listeners[t] = f; };
El.prototype.setAttribute = function (k, v) { this.attrs[k] = v; };
El.prototype.remove = function () { if (this.parent) this.parent.removeChild(this); };
Object.defineProperty(El.prototype, 'firstChild', { get: function () { return this.children[0] || null; } });
Object.defineProperty(El.prototype, 'textContent', {
  get: function () { return this._t + this.children.map(function (c) { return c.textContent; }).join(''); },
  set: function (v) { this._t = String(v); this.children = []; } });
function texts(n, out) { out = out || []; out.push({ tag: n.tagName, cls: n.className, text: n._t }); n.children.forEach(function (c) { texts(c, out); }); return out; }
globalThis.window = globalThis;
globalThis.addEventListener = function () {};
globalThis.location = { hostname: 'localhost' };
globalThis.document = { createElement: function (t) { return new El(t); }, getElementById: function () { return null; },
  addEventListener: function () {}, querySelector: function () { return null; }, querySelectorAll: function () { return []; } };
globalThis.calls = [];
globalThis.fetch = function (url, opts) { calls.push(url); return Promise.resolve({ ok: true, status: 200,
  json: function () { return Promise.resolve(""" + fetch_reply + """); } }); };
window.VibeFiles = {};
require(""" + json.dumps(str(PROJ_JS)) + """);
""" + body)


def _node(script: str):
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


CHANGES = {"checkpoint": {"id": "c0003"}, "before": "c0002", "run": None,
           "changed": [{"path": "index.html", "status": "changed", "added": 2, "removed": 1}]}


@node
@pytest.mark.parametrize("private", [False, True])
def test_a_reloaded_answer_draws_chips_revert_and_rewind_from_its_checkpoint(private):
    out = _node(_dom_script("""
window._vibePrivate = %s;
var rewound = [];
window.rewindTo = function (i, cid) { rewound.push([i, cid]); };
var box = VibeProj.savedTurnNode('Made it blue.\\n[Files written: index.html]', 'c0003', 5, 'my-site');
setTimeout(function () {
  var all = texts(box);
  var rw = null;
  (function find(n) { if (n.tagName === 'button' && n._t === 'Rewind to here') rw = n; n.children.forEach(find); })(box);
  if (rw) rw.listeners.click();
  console.log(JSON.stringify({ calls: calls, all: all, rewound: rewound }));
}, 20);
""" % ("true" if private else "false"), json.dumps(CHANGES)))
    assert out["calls"] == ["/api/vibe/projects/my-site/checkpoints/c0003/changes"]
    shown = [x["text"] for x in out["all"] if x["text"]]
    assert shown[0] == "Made it blue.", "the file line is drawn as chips, not as text"
    assert "index.html" in shown and "+2 −1" in shown and "Revert to before this" in shown
    if private:
        assert "Rewind to here" not in shown and out["rewound"] == []
    else:
        assert "Rewind to here" in shown and out["rewound"] == [[5, "c0003"]]


@node
def test_a_reloaded_answer_with_a_bad_id_asks_nothing_and_keeps_its_text():
    out = _node(_dom_script("""
var box = VibeProj.savedTurnNode('Done.\\n[Files written: a.js]', '../c1', 1, 'my-site');
setTimeout(function () { console.log(JSON.stringify({ calls: calls, text: box.textContent })); }, 20);
"""))
    assert out == {"calls": [], "text": "Done.\n[Files written: a.js]"}


@node
def test_the_not_versioned_line_names_what_is_left_out():
    out = _node(_dom_script("""
console.log(JSON.stringify([
  VibeProj.unversionedText({ count: 0, folders: {} }),
  VibeProj.unversionedText({ count: 4, folders: { dist: 2, build: 0 }, large: 1, links: 1, capped: false }),
  VibeProj.unversionedText({ count: 1, folders: { coverage: 1 }, large: 0, links: 0, capped: true })]));
"""))
    assert out[0] == ""
    assert out[1] == "4 files are not versioned (in dist, 1 over 1 MB, 1 link). Versions, Restore and Copy leave them as they are."
    assert out[2].startswith("More than 1 file is not versioned (in coverage)")


@node
def test_chat_rows_carry_only_a_checkpoint_id_that_looks_like_one():
    chat = REPO / "static" / "vibe-chat.js"
    agent = REPO / "static" / "vibe-agent.js"
    script = (f"const C = require({json.dumps(str(chat))}); const A = require({json.dumps(str(agent))});\n"
              "console.log(JSON.stringify({\n"
              " stored: C.stored([{role: 'assistant', content: 'x', checkpoint: 'c0004'}, {role: 'assistant', content: 'y', checkpoint: 'c1'},\n"
              "                   {role: 'user', content: 'z', checkpoint: null}]),\n"
              " loaded: C.loaded([{role: 'assistant', content: 'x', checkpoint_id: 'c0004'}, {role: 'assistant', content: 'y', checkpoint_id: '<b>'}]),\n"
              " ends: [A.endCheckpoint({checkpoints: [{id: 'c0003'}, {id: 'c0005'}], startCp: 'c0002'}),\n"
              "        A.endCheckpoint({checkpoints: [], startCp: 'c0002'}), A.endCheckpoint({checkpoints: []})]}));")
    out = _node(script)
    assert out["stored"] == [{"role": "assistant", "content": "x", "checkpoint_id": "c0004"},
                             {"role": "assistant", "content": "y"}, {"role": "user", "content": "z"}]
    assert out["loaded"] == [{"role": "assistant", "content": "x", "checkpoint": "c0004"}, {"role": "assistant", "content": "y"}]
    assert out["ends"] == ["c0005", "c0002", None]
