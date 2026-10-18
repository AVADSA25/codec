"""Vibe page wiring for projects (docs/VIBE-PROJECTS-DESIGN.md): scripts, controls, router,
and the link between a saved session and its project folder."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
PAGE = (REPO / "codec_vibe.html").read_text(encoding="utf-8")


def test_the_page_loads_both_scripts_with_a_version_stamp_before_its_own_script():
    files = re.search(r'<script src="/static/vibe-files\.js\?v=[0-9a-f]{8}"></script>', PAGE)
    projects = re.search(r'<script src="/static/vibe-projects\.js\?v=[0-9a-f]{8}"></script>', PAGE)
    assert files and projects and files.start() < projects.start() < PAGE.index("function doSend(")
    assert (REPO / "static" / "vibe-files.js").is_file() and (REPO / "static" / "vibe-projects.js").is_file()


def test_the_page_has_the_undo_and_versions_controls_hidden_until_there_is_a_project():
    undo = re.search(r'<button[^>]*id="undoBtn"[^>]*>', PAGE).group(0)
    ver = re.search(r'<button[^>]*id="verBtn"[^>]*>', PAGE).group(0)
    assert "hidden" in undo and "VibeProj.undo()" in undo
    assert "hidden" in ver and "VibeProj.versions()" in ver
    for needed in ('id="ftabs"', 'id="vdList"', 'id="vov"'):
        assert needed in PAGE
    drawer = re.search(r'<div class="vd" id="vd"[^>]*>', PAGE).group(0)
    assert 'role="dialog"' in drawer and 'aria-modal="true"' in drawer and 'data-dialog="vo"' in drawer
    assert "data-dialog-close" in PAGE[PAGE.index('id="vd"'):PAGE.index('id="vdList"')]


def test_the_send_flow_goes_through_a_project_and_keeps_the_single_file_flow():
    assert "pm = window.VibeProj ? await VibeProj.begin(t) : false;" in PAGE
    assert "VibeProj.finish(rp, t)" in PAGE and "VibeProj.abort()" in PAGE
    assert "(window.VibeProj && VibeProj.previewSource()) || ed.getValue()" in PAGE
    # single-file flow untouched: the live fence handling still runs when there is no project
    assert "var f = pm ? null : _vibeFence(clean);" in PAGE
    assert "ed.setValue(f.code)" in PAGE and "function doApply(btn)" in PAGE
    project_prompt = PAGE[PAGE.index("var SYSP_PROJECT"):PAGE.index("function _histForSend")]
    assert "file:index.html" in project_prompt and "overrides rules 1 and 5" in project_prompt
    assert "never write secrets" in project_prompt


def test_older_turns_are_sent_without_their_full_code():
    fn = PAGE[PAGE.index("function _histForSend"):]
    fn = fn[:fn.index("\n}\n")]
    assert "i < last && m.plain" in fn
    assert "plain: t" in PAGE and "plain: (expl" in PAGE


def test_a_project_session_saves_its_project_and_deleting_it_moves_the_folder_to_the_trash():
    assert "project_id: inProject ? VibeProj.id() : null" in PAGE
    assert "'/api/vibe/projects/' + encodeURIComponent(pid), {method: 'DELETE'}" in PAGE
    assert "moved to the trash folder, not erased" in PAGE
    assert "VibeProj.open(d.session.project_id)" in PAGE and "showing the last saved code" in PAGE


def test_the_router_is_registered_in_the_dashboard():
    from codec_dashboard import app
    paths = {getattr(r, "path", "") for r in app.routes}
    for p in ("/api/vibe/projects", "/api/vibe/projects/{pid}", "/api/vibe/projects/{pid}/apply",
              "/api/vibe/projects/{pid}/undo", "/api/vibe/projects/{pid}/restore", "/api/vibe/projects/{pid}/diff"):
        assert p in paths, p


def test_new_scripts_use_text_content_for_names_and_labels():
    src = (REPO / "static" / "vibe-projects.js").read_text(encoding="utf-8")
    assert "innerHTML" not in src and "insertAdjacentHTML" not in src and "document.write" not in src
    assert "eval(" not in src and "new Function" not in src


# ── the session row remembers its project ───────────────────────────────

@pytest.fixture
def sessions(tmp_path, monkeypatch):
    import routes.vibe as rv
    monkeypatch.setattr(rv, "VIBE_DB", str(tmp_path / "vibe.db"))
    monkeypatch.setattr(rv, "_vibe_conn", None)
    app = FastAPI()
    app.include_router(rv.router)
    yield TestClient(app), rv
    if rv._vibe_conn is not None:
        rv._vibe_conn.close()
        rv._vibe_conn = None


def _save(client, **over):
    body = {"session_id": "s1", "title": "t", "language": "python", "code": "x = 1\n", "messages": [], "full_sync": True}
    body.update(over)
    return client.post("/api/vibe/save", json=body)


def test_a_saved_session_keeps_its_project_and_a_page_that_sends_none_does_not_erase_it(sessions):
    client, _rv = sessions
    assert _save(client, project_id="make-it-blue-7bcaef").status_code == 200
    assert client.get("/api/vibe/session/s1").json()["session"]["project_id"] == "make-it-blue-7bcaef"
    assert client.get("/api/vibe/sessions").json()[0]["project_id"] == "make-it-blue-7bcaef"
    _save(client, code="x = 2\n")            # an older page: no project_id in the body
    got = client.get("/api/vibe/session/s1").json()["session"]
    assert got["project_id"] == "make-it-blue-7bcaef" and got["code"] == "x = 2\n"


def test_a_project_id_in_a_bad_shape_is_ignored_and_single_file_sessions_have_none(sessions):
    client, _rv = sessions
    _save(client, session_id="s2", project_id="../../etc")
    _save(client, session_id="s3", project_id=123)
    _save(client, session_id="s4")
    for sid in ("s2", "s3", "s4"):
        assert client.get(f"/api/vibe/session/{sid}").json()["session"]["project_id"] is None


def test_an_old_database_without_the_column_is_upgraded_in_place(tmp_path, monkeypatch):
    import routes.vibe as rv
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE vibe_sessions (id TEXT PRIMARY KEY, title TEXT, language TEXT, code TEXT, created_at TEXT, updated_at TEXT, user_id TEXT DEFAULT 'default')")
    con.execute("INSERT INTO vibe_sessions VALUES ('old', 'kept', 'python', 'print(1)', 'a', 'b', 'default')")
    con.commit()
    con.close()
    monkeypatch.setattr(rv, "VIBE_DB", str(db))
    monkeypatch.setattr(rv, "_vibe_conn", None)
    app = FastAPI()
    app.include_router(rv.router)
    client = TestClient(app)
    got = client.get("/api/vibe/session/old").json()["session"]
    assert got["code"] == "print(1)" and got["project_id"] is None
    rv._vibe_conn.close()
    rv._vibe_conn = None
