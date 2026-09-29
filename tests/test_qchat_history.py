"""Chat history management (UI phase 2, P2.2; docs/P2.2-DESIGN.md).

Chats can be renamed, pinned, archived and exported; the list pages; a save
can mark the discarded tail of a chat superseded (regenerate / edit) so it
does not come back on reload. qchat.db changes are additive and follow a
one-time backup copy. The sidebar groups chats, loads more, has an archived
view and a select mode with an in-page confirm.
"""
from __future__ import annotations

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

import routes.qchat as qchat

REPO = Path(__file__).resolve().parent.parent
SHELL = (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")
CHAT = (REPO / "codec_chat.html").read_text(encoding="utf-8")


def _legacy_db(path: Path) -> None:
    """The schema as it was before P2.2, with a little made-up history."""
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE qchat_sessions (id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT, user_id TEXT DEFAULT 'default')")
    c.execute("CREATE TABLE qchat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp TEXT, user_id TEXT DEFAULT 'default')")
    c.execute("INSERT INTO qchat_sessions VALUES ('s1', 'Trip plan', '2026-09-01T10:00:00', '2026-09-01T10:05:00', 'default')")
    c.executemany("INSERT INTO qchat_messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                  [("s1", "user", "Plan a trip", "2026-09-01T10:00:00"), ("s1", "assistant", "Day one: the coast.", "2026-09-01T10:01:00")])
    c.commit()
    c.close()


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
def client(db):
    app = FastAPI()
    app.include_router(qchat.router)
    return TestClient(app)


def _save(client, sid, msgs, **extra):
    r = client.post("/api/qchat/save", json={"session_id": sid, "title": sid, "messages": msgs, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_migration_backs_up_once_and_keeps_rows(db, monkeypatch):
    _legacy_db(db)
    conn = qchat.qchat_db()
    backup = Path(str(db) + ".bak-p2.2")
    assert backup.exists() and stat.S_IMODE(backup.stat().st_mode) == 0o600
    b = sqlite3.connect(backup)
    assert b.execute("SELECT COUNT(*) FROM qchat_messages").fetchone()[0] == 2
    assert "pinned" not in {r[1] for r in b.execute("PRAGMA table_info(qchat_sessions)")}, "the copy is taken before the change"
    b.close()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(qchat_sessions)")}
    assert {"pinned", "archived"} <= cols
    assert "superseded_at" in {r[1] for r in conn.execute("PRAGMA table_info(qchat_messages)")}
    assert conn.execute("SELECT title FROM qchat_sessions").fetchall() == [("Trip plan",)]
    assert conn.execute("SELECT COUNT(*) FROM qchat_messages").fetchone()[0] == 2
    # A second start finds the columns and leaves the backup alone.
    before = backup.stat().st_mtime_ns
    conn.close()
    monkeypatch.setattr(qchat, "_qchat_conn", None)
    qchat.qchat_db()
    assert backup.stat().st_mtime_ns == before


def test_new_empty_database_gets_the_columns_without_a_backup(db):
    conn = qchat.qchat_db()
    assert {"pinned", "archived"} <= {r[1] for r in conn.execute("PRAGMA table_info(qchat_sessions)")}
    assert not Path(str(db) + ".bak-p2.2").exists(), "an empty database needs no copy"


def test_list_pins_first_hides_archived_and_pages(client):
    for i in range(5):
        _save(client, f"c{i}", [{"role": "user", "content": f"question {i}"}])
    assert client.patch("/api/qchat/session/c0", json={"pinned": True}).status_code == 200
    assert client.patch("/api/qchat/session/c1", json={"archived": True}).status_code == 200
    rows = client.get("/api/qchat/sessions").json()
    assert [r["id"] for r in rows] == ["c0", "c4", "c3", "c2"], "pinned first, then newest; archived hidden"
    assert rows[0]["pinned"] is True and "created_at" in rows[0]
    page = client.get("/api/qchat/sessions?offset=1&limit=2").json()
    assert [r["id"] for r in page] == ["c4", "c3"]
    arch = client.get("/api/qchat/sessions?archived=1").json()
    assert [r["id"] for r in arch] == ["c1"] and arch[0]["archived"] is True


def test_patch_renames_and_refuses_bad_changes(client):
    _save(client, "c", [{"role": "user", "content": "hello"}])
    r = client.patch("/api/qchat/session/c", json={"title": "  Better name  "})
    assert r.status_code == 200 and r.json()["title"] == "Better name"
    _save(client, "c", [{"role": "assistant", "content": "hi"}])
    assert client.get("/api/qchat/sessions").json()[0]["title"] == "Better name", "a later save keeps the new name"
    assert client.patch("/api/qchat/session/nope", json={"pinned": True}).status_code == 404
    assert client.patch("/api/qchat/session/c", json={"title": "   "}).status_code == 400
    assert client.patch("/api/qchat/session/c", json={}).status_code == 400


def test_discard_from_marks_the_tail_and_nothing_is_deleted(client, db):
    _save(client, "c", [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"},
                        {"role": "user", "content": "q2"}, {"role": "assistant", "content": "old answer"}])
    # Regenerate: the page dropped the last reply (position 3) and saves the new one.
    out = _save(client, "c", [{"role": "assistant", "content": "new answer"}], discard_from=3)
    assert out["rows"] == 4
    msgs = client.get("/api/qchat/session/c").json()
    assert [m["content"] for m in msgs] == ["q1", "a1", "q2", "new answer"]
    assert client.get("/api/qchat/search?q=old answer").json() == [], "search skips superseded messages"
    # Edit q2: the page drops from position 2, then saves the edited question.
    _save(client, "c", [{"role": "user", "content": "q2 edited"}], discard_from=2)
    assert [m["content"] for m in client.get("/api/qchat/session/c").json()] == ["q1", "a1", "q2 edited"]
    total = sqlite3.connect(db).execute("SELECT COUNT(*) FROM qchat_messages").fetchone()[0]
    assert total == 6, "superseded rows stay in the database"
    assert client.post("/api/qchat/save", json={"session_id": "c", "messages": [], "discard_from": "x"}).status_code == 400


def test_export_markdown_and_json(client):
    _save(client, "c", [{"role": "user", "content": "Plan a trip"}, {"role": "assistant", "content": "old"}])
    _save(client, "c", [{"role": "assistant", "content": "Day one: the coast."}], discard_from=1)
    client.patch("/api/qchat/session/c", json={"title": "Coast trip!"})
    md = client.get("/api/qchat/session/c/export?format=md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert 'filename="coast-trip.md"' in md.headers["content-disposition"]
    assert md.text.startswith("# Coast trip!") and "**You**" in md.text and "**CODEC**" in md.text
    assert "Day one: the coast." in md.text and "old" not in md.text
    js = client.get("/api/qchat/session/c/export?format=json")
    data = json.loads(js.text)
    assert data["title"] == "Coast trip!" and [m["content"] for m in data["messages"]] == ["Plan a trip", "Day one: the coast."]
    assert client.get("/api/qchat/session/c/export?format=pdf").status_code == 400
    assert client.get("/api/qchat/session/nope/export").status_code == 404


def test_google_doc_route_uses_the_skill_helper(client, monkeypatch):
    import codec_dispatch
    calls = []

    class FakeSkill:
        @staticmethod
        def create_doc(title, content=""):
            calls.append((title, content))
            return "https://docs.google.com/document/d/abc/edit"

    monkeypatch.setattr(codec_dispatch.registry, "load", lambda name: FakeSkill if name == "google_docs" else None)
    _save(client, "c", [{"role": "user", "content": "Plan a trip"}])
    r = client.post("/api/qchat/session/c/gdoc")
    assert r.status_code == 200 and r.json()["url"].startswith("https://docs.google.com/")
    assert calls and calls[0][0] == "c" and "Plan a trip" in calls[0][1]


def test_patch_is_csrf_checked_like_other_writes():
    src = (REPO / "codec_dashboard.py").read_text(encoding="utf-8")
    assert 'if request.method in ("POST", "PUT", "DELETE", "PATCH") and path not in self.CSRF_EXEMPT' in src


def test_chat_marks_the_discarded_tail_on_regenerate_and_edit():
    regen = CHAT[CHAT.index("function regenerateResponse("):CHAT.index("function regenerateResponse(") + 800]
    assert "chatHist.pop();\n  _markDiscard(chatHist.length);" in regen
    assert "chatHist=chatHist.slice(0,i);_markDiscard(i);break" in CHAT
    save = CHAT[CHAT.index("async function saveMessages("):]
    save = save[:save.index("\n}\n")]
    assert "payload.discard_from=_discardFrom;_discardFrom=null" in save


def test_sidebar_groups_menu_paging_archive_and_select():
    for label in ("'Pinned'", "'Today'", "'Yesterday'", "'Previous 7 days'", "'Older'"):
        assert label in SHELL, f"no {label} group"
    for item in ("Rename", "'Pin'", "Archive", "Export as Markdown", "Export as JSON", "Save to Google Doc", "'Delete'"):
        assert item in SHELL, f"row menu has no {item}"
    assert "'/api/qchat/sessions?offset=' + offset + '&limit=' + limit + '&archived='" in SHELL
    assert "method: 'PATCH'" in SHELL and "/export?format=" in SHELL and "/gdoc'" in SHELL
    assert "data-act=\"many-yes\"" in SHELL and "Delete this chat?" in SHELL
    assert "confirm(" not in SHELL and "alert(" not in SHELL and "prompt(" not in SHELL, "no native dialogs"


def _jsdom_available() -> bool:
    if shutil.which("node") is None:
        return False
    r = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True,
                       cwd=REPO, env=os.environ.copy(), timeout=20)
    return r.returncode == 0


_HARNESS = r"""
const { JSDOM } = require('jsdom');
const fs = require('fs');
const shell = fs.readFileSync(process.argv[1], 'utf8').replace(/<\/script/gi, '<\\/script');
const day = 864e5, now = Date.now();
const iso = t => new Date(t).toISOString().slice(0, 19);
const rows = [
  { id: 'p', title: 'Pinned one', updated_at: iso(now - 20 * day), pinned: true },
  { id: 't', title: 'Today one', updated_at: iso(now - 60e3) },
  { id: 'y', title: 'Yesterday one', updated_at: iso(now - day) },
  { id: 'o', title: 'Old one', updated_at: iso(now - 40 * day) }];
const html = '<!doctype html><html><body>' +
  '<script>window.__calls=[];window.fetch=(u,o)=>{window.__calls.push([u,(o&&o.method)||"GET"]);' +
  'return Promise.resolve({ok:true,json:()=>Promise.resolve(u.indexOf("/api/qchat/sessions")===0?' + JSON.stringify(rows) +
  ':{ok:true,unread:0})})};</script>' +
  '<script data-page="tasks" data-title="Tasks">' + shell + '</script><div class="cs-main">x</div></body></html>';
const dom = new JSDOM(html, { url: 'http://localhost/tasks', runScripts: 'dangerously', pretendToBeVisual: true,
  beforeParse(w) { Object.defineProperty(w, 'innerWidth', { value: 1470, configurable: true }); } });
const w = dom.window, d = w.document;
setTimeout(() => {
  const out = {};
  out.groups = [...d.querySelectorAll('.cs-group')].map(e => e.textContent);
  out.rows = [...d.querySelectorAll('.cs-hrow')].map(e => e.dataset.sid);
  d.querySelector('[data-menu="t"]').click();
  out.menu = [...d.querySelectorAll('#csPop .cs-pop-item span')].map(e => e.textContent);
  d.querySelector('#csPop [data-act="delete"]').click();
  out.ask = !!d.querySelector('.cs-hask');
  d.querySelector('[data-act="one-no"]').click();
  d.getElementById('csSelectBtn').click();
  d.querySelector('.cs-hrow[data-sid="y"]').click();
  d.querySelector('.cs-hrow[data-sid="o"]').click();
  d.querySelector('[data-act="many-ask"]').click();
  out.bar = d.getElementById('csSelBar').textContent;
  d.querySelector('[data-act="many-yes"]').click();
  setTimeout(() => {
    out.deleted = w.__calls.filter(c => c[1] === 'DELETE').map(c => c[0]);
    console.log(JSON.stringify(out));
    process.exit(0);
  }, 50);
}, 100);
"""


@pytest.mark.skipif(not _jsdom_available(), reason="node with jsdom not resolvable (set NODE_PATH)")
def test_sidebar_groups_menu_and_select_delete_under_jsdom():
    out = subprocess.run(["node", "-e", _HARNESS, str(REPO / "static" / "codec-shell.js")], capture_output=True,
                         text=True, cwd=REPO, env=os.environ.copy(), timeout=60)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["groups"] == ["Pinned", "Today", "Yesterday", "Older"], r
    assert r["rows"] == ["p", "t", "y", "o"]
    assert r["menu"] == ["Rename", "Pin", "Archive", "Export as Markdown", "Export as JSON", "Save to Google Doc", "Delete"]
    assert r["ask"] is True, "single delete asks in the row"
    assert "Delete 2 chats?" in r["bar"]
    assert sorted(r["deleted"]) == ["/api/qchat/session/o", "/api/qchat/session/y"]

