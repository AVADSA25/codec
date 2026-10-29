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


def test_the_page_loads_monaco_from_the_repo_copy_with_its_language_workers():
    # D-24: the editor comes from static/vendor/monaco, never from a CDN, and the copy
    # keeps the TypeScript/JavaScript language service (error underlines, hover, completion).
    assert "cdnjs" not in PAGE
    assert '<script src="/static/vendor/monaco/vs/loader.js"></script>' in PAGE
    assert "require.config({paths:{vs:'/static/vendor/monaco/vs'}});" in PAGE
    assert "setModeConfiguration" not in PAGE
    vs = REPO / "static" / "vendor" / "monaco" / "vs"
    assert (vs / "loader.js").is_file() and (vs / "editor" / "editor.main.js").is_file()
    for worker in ("ts", "css", "html", "json", "editor"):
        assert len(list((vs / "assets").glob(worker + ".worker-*.js"))) == 1, worker
    for notice in ("LICENSE", "ThirdPartyNotices.txt"):
        assert (vs.parent / notice).is_file()


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


# ── panes, hideable code, live preview (step 2a) ────────────────────────

def _css(selector_start: str) -> str:
    i = PAGE.index(selector_start)
    return PAGE[i:PAGE.index("\n", i)]


def test_layout_has_a_hide_code_button_that_is_remembered_and_only_hides_the_code_on_a_wide_screen():
    btn = re.search(r'<button[^>]*id="codeBtn"[^>]*>[^<]*</button>', PAGE).group(0)
    assert 'onclick="toggleCodePane()"' in btn and 'aria-pressed="true"' in btn
    assert "@media (min-width:769px){.sp.code-off .ep{display:none}}" in PAGE
    fn = PAGE[PAGE.index("function _applyCodePane()"):PAGE.index("// silent: an automatic refresh")]
    assert "_lsGet('codec-vibe-code') === 'off'" in fn and "_lsSet('codec-vibe-code'" in fn
    assert "'Show code'" in fn and "'Hide code'" in fn and fn.rstrip().endswith("_applyCodePane();")
    assert "#codeBtn{display:none}" in PAGE[PAGE.index("@media (max-width:768px)"):], "the phone has tabs instead"


def test_layout_phone_shows_one_pane_at_a_time_from_three_tabs():
    assert '<div class="sp" data-pane="chat">' in PAGE
    tabs = re.search(r'<div class="vtabs" id="vtabs"[^>]*>(.*?)</div>', PAGE, re.S).group(1)
    assert [m for m in re.findall(r"_setVibeTab\('(\w+)'\)", tabs)] == ["chat", "preview", "code"]
    phone = PAGE[PAGE.index("@media (max-width:768px)"):PAGE.index("@media (max-width:480px)")]
    for rule in ('.sp[data-pane="chat"] .cp{display:flex}', '.sp[data-pane="preview"] .pp{display:flex}',
                 '.sp[data-pane="code"] .ep{display:flex}', ".cp,.ep,.pp{display:none;"):
        assert rule in phone, rule
    assert "tab-preview" not in PAGE and "_vibeTabPreview" not in PAGE, "the old Code/Preview tabs are gone"
    fn = PAGE[PAGE.index("function _setVibeTab(t)"):PAGE.index("function closePrev()")]
    assert "setAttribute('data-pane', t)" in fn and "aria-selected" in fn
    assert "var d = document.querySelector('#vtabPrev .vdot'); if (d) d.hidden = true;" in fn


def test_preview_is_open_from_the_start_with_an_empty_state_and_its_own_buttons():
    assert ".pp{display:flex;" in PAGE and ".pp.off{display:none}" in PAGE
    pane = PAGE[PAGE.index('<div class="pp" id="pp">'):PAGE.index('<div class="op" id="op">')]
    assert 'id="pvEmpty"' in pane and "The preview appears here" in pane
    assert pane.count('class="ph-b') == 5, "stop, run, refresh, inspect and close"
    assert 'onclick="doPrev()"' in pane and 'onclick="toggleInspect()"' in pane and 'onclick="closePrev()"' in pane
    assert 'sandbox="allow-scripts"' in pane and "allow-same-origin" not in pane
    assert ".pv-wrap.has .pv-empty{display:none}" in PAGE


def test_preview_refresh_is_silent_and_does_not_pull_the_phone_to_the_preview_tab():
    fn = PAGE[PAGE.index("function doPrev(silent)"):PAGE.index("function refreshPreview()")]
    assert "silent = silent === true;" in fn
    assert "} else _setVibeTab('preview');" in fn and "if (!silent) addOut('sy', 'Preview updated');" in fn
    assert "} else if (!silent) {\n    addMsg('assistant', 'Preview works with HTML." in fn
    assert "dot.hidden = false" in fn
    # an automatic refresh that finds no page (everything undone) goes back to the empty state
    assert "w2.classList.remove('has')" in fn and "document.getElementById('pf').srcdoc = '';" in fn


def test_refresh_runs_only_while_the_preview_is_open_and_after_every_way_the_files_change():
    fn = PAGE[PAGE.index("function refreshPreview()"):]
    fn = fn[:fn.index("\n}\n")]
    assert "classList.contains('off')) return;" in fn and "doPrev(true);" in fn
    src = (REPO / "static" / "vibe-projects.js").read_text(encoding="utf-8")
    assert src.count("refreshPreview();") >= 4
    assert "flush().then(function () { if (!P.live) refreshPreview(); })" in src
    reload_fn = src[src.index("function reload()"):src.index("function meaningful(")]
    assert "refreshPreview();" in reload_fn
    open_fn = src[src.index("function open(id)"):src.index("function loadFiles(")]
    assert "refreshPreview();" in open_fn
    assert "setTimeout(function () { doPrev(true); }, 300);\n          else refreshPreview();" in PAGE


# ── show on localhost / run a command (step 2b) ─────────────────────────

CONTROLLER = (REPO / "static" / "vibe-projects.js").read_text(encoding="utf-8")


def test_live_dialog_is_a_labelled_modal_with_the_command_in_view_and_says_the_ai_cannot_start_it():
    dlg = re.search(r'<div class="rd" id="rd"[^>]*>', PAGE).group(0)
    for attr in ('role="dialog"', 'aria-modal="true"', "aria-label=", 'data-dialog="ro"'):
        assert attr in dlg, attr
    body = PAGE[PAGE.index('id="rd"'):PAGE.index('id="rdGo"') + 120]
    assert "data-dialog-close" in body and 'id="rdCommand"' in body and 'maxlength="500"' in body
    assert "The AI never starts anything by itself" in body and "Read it before you start" in body
    assert 'id="rdErr"' in body and 'role="alert"' in body
    assert 'id="rov"' in PAGE and "VibeProj.closeRun()" in PAGE


def test_live_header_has_a_state_chip_stop_and_run_all_hidden_until_there_is_something_to_show():
    for needle in ('id="liveState"', 'id="stopLiveBtn"', 'id="runBtn"'):
        tag = re.search(r'<[a-z]+[^>]*' + needle + r'[^>]*>', PAGE).group(0)
        assert "hidden" in tag, needle
    assert "var ON_MAC = /^(localhost|127\\.0\\.0\\.1|\\[::1\\])$/.test(location.hostname);" in CONTROLLER
    assert "runb.hidden = !(P.id && ON_MAC)" in CONTROLLER
    assert 'onclick="VibeProj.runDialog()"' in PAGE and 'onclick="VibeProj.stopLive()"' in PAGE


def test_live_frames_are_sandboxed_by_mode_and_the_inline_preview_never_overwrites_a_live_frame():
    sandbox = re.search(r"var SANDBOX = \{ serve: '([^']*)',\s*run: '([^']*)' \};", CONTROLLER)
    assert sandbox, "the frame sandbox is set in one place"
    served, ran = sandbox.groups()
    assert "allow-scripts" in served and "allow-same-origin" not in served, "files are shown with no access to anything"
    assert "allow-same-origin" in ran, "a running app needs its own origin for storage"
    fn = PAGE[PAGE.index("function doPrev(silent)"):PAGE.index("function refreshPreview()")]
    assert fn.index("VibeProj.liveActive()") < fn.index("VibeProj.previewSource()")
    assert "VibeProj.liveReload(); return;" in fn


def test_live_command_comes_only_from_the_dialog_never_from_a_file_the_ai_wrote():
    assert CONTROLLER.count("base('/run')") == 1
    go = CONTROLLER[CONTROLLER.index("function runGo()"):CONTROLLER.index("// ── small hooks for the page")]
    assert "$('rdCommand').value.trim()" in go and "{ command: command, port: port }" in go
    assert CONTROLLER.count("runGo(") == 1, "runGo is only defined, nothing calls it by itself"
    assert PAGE.count("VibeProj.runGo()") == 1 and 'id="rdGo"' in PAGE
    suggest = CONTROLLER[CONTROLLER.index("function suggestCommand()"):CONTROLLER.index("function runDialog()")]
    assert "api(" not in suggest and "fetch(" not in suggest and "exec" not in suggest, "a suggestion is only text for the dialog"


def test_live_a_hidden_tab_does_not_keep_what_is_running_alive():
    poll = CONTROLLER[CONTROLLER.index("function pollLive()"):CONTROLLER.index("function beginLive(")]
    assert "if (document.hidden) { r.timer = setTimeout(pollLive, 5000); return; }" in poll
    assert poll.index("document.hidden") < poll.index("api('GET'"), "a hidden tab must not call the server"


def test_live_server_adopts_what_is_running_when_a_project_is_opened_and_stops_it_when_leaving():
    open_fn = CONTROLLER[CONTROLLER.index("function open(id)"):CONTROLLER.index("function adoptLive(")]
    assert "adoptLive(id);" in open_fn
    assert "if (P.run && P.id) stopLive(true);" in CONTROLLER[CONTROLLER.index("function detach()"):CONTROLLER.index("function pickFirst(")]
