"""Vibe agent on the page (static/vibe-agent.js, docs/VIBE-AGENT-DESIGN.md step 3b).

The top half of the script is plain functions on events and text, so Node runs them without a
browser. The rest checks the wiring: the script is stamped and loaded in the right place, the
mode switch is reachable and labelled, and nothing from a run ever goes in as HTML.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
JS = REPO / "static" / "vibe-agent.js"
SRC = JS.read_text(encoding="utf-8")
PAGE = (REPO / "codec_vibe.html").read_text(encoding="utf-8")
PROJ = (REPO / "static" / "vibe-projects.js").read_text(encoding="utf-8")

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def run(expr: str, setup: str = ""):
    script = f"const A = require({json.dumps(str(JS))}); {setup} console.log(JSON.stringify({expr}));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


# a whole run, as the server sends it
EVENTS = [
    {"seq": 1, "t": 1000.0, "type": "start", "goal": "Add a form", "model": {"kind": "local", "label": "Local"},
     "limits": {"max_steps": 30, "max_seconds": 900}, "start_checkpoint": "c0002"},
    {"seq": 2, "t": 1000.5, "type": "text", "step": 1, "chunk": "I will read the page. @@ read_f"},
    {"seq": 3, "t": 1001.0, "type": "note", "step": 1, "text": "I will read the page."},
    {"seq": 4, "t": 1001.0, "type": "tool", "step": 1, "name": "read_file", "arg": "index.html"},
    {"seq": 5, "t": 1001.2, "type": "result", "step": 1, "ok": True, "tool": "read_file", "summary": "Read index.html, lines 1-9 of 9", "output": "  1| <h1>"},
    {"seq": 6, "t": 1002.0, "type": "tool", "step": 2, "name": "edit_file", "arg": "index.html (1 edit)"},
    {"seq": 7, "t": 1002.2, "type": "result", "step": 2, "ok": True, "tool": "edit_file", "summary": "Edited index.html (+3 -1)", "output": "ok"},
    {"seq": 8, "t": 1002.3, "type": "checkpoint", "step": 2, "id": "c0003", "parent": "c0002", "label": "Agent: edit_file index.html",
     "lines_added": 3, "lines_removed": 1, "changed": ["index.html"]},
    {"seq": 9, "t": 1003.0, "type": "tool", "step": 3, "name": "ask", "arg": ""},
    {"seq": 10, "t": 1003.0, "type": "question", "step": 3, "id": "q1a2b3", "text": "Which colour?", "choices": ["Blue", "Green"]},
    {"seq": 11, "t": 1010.0, "type": "answer", "step": 3, "id": "q1a2b3"},
    {"seq": 12, "t": 1011.0, "type": "tool", "step": 4, "name": "write_file", "arg": "style.css"},
    {"seq": 13, "t": 1011.1, "type": "result", "step": 4, "ok": True, "tool": "write_file", "summary": "Wrote style.css", "output": "Wrote style.css."},
    {"seq": 14, "t": 1011.2, "type": "checkpoint", "step": 4, "id": "c0004", "parent": "c0003", "label": "Agent: write_file style.css",
     "lines_added": 5, "lines_removed": 0, "changed": ["style.css", "index.html"]},
    {"seq": 15, "t": 1012.0, "type": "end", "status": "done", "report": "Added the form and a stylesheet.", "steps": 5, "seconds": 12.0,
     "start_checkpoint": "c0002"},
]


@node
def test_a_whole_run_is_followed_event_by_event():
    out = run("(function () { var r = A.newRun('p1'); var seen = []; E.forEach(function (e) { A.applyEvent(r, e).forEach(function (x) { seen.push(x.t); }); }); "
              "return { status: r.status, step: r.step, since: r.since, finished: r.finished, changed: r.changed, startCp: r.startCp, model: r.model, "
              "max: r.maxSteps, goal: r.goal, cps: r.checkpoints.map(function (c) { return [c.id, c.parent, c.step]; }), "
              "steps: Object.keys(r.steps).map(function (k) { var s = r.steps[k]; return [s.name, s.ok, s.summary, !!s.cp]; }), seen: seen }; })()",
              f"const E = {json.dumps(EVENTS)};")
    assert out["status"] == "done" and out["finished"] and out["step"] == 4 and out["since"] == 15
    assert out["goal"] == "Add a form" and out["model"] == "Local" and out["max"] == 30 and out["startCp"] == "c0002"
    assert out["changed"] == ["index.html", "style.css"]                       # each file once, in the order first changed
    assert out["cps"] == [["c0003", "c0002", 2], ["c0004", "c0003", 4]]
    assert out["steps"][0] == ["read_file", True, "Read index.html, lines 1-9 of 9", False]
    assert out["steps"][1][3] is True and out["steps"][3][0] == "write_file"
    assert out["seen"][0] == "meta" and out["seen"][-1] == "end" and "question" in out["seen"] and "answer" in out["seen"]


@node
def test_a_question_makes_the_run_wait_and_an_answer_sets_it_going_again():
    states = run("(function () { var r = A.newRun('p'); var out = []; E.forEach(function (e) { A.applyEvent(r, e); out.push([e.type, r.status, !!r.question]); }); return out; })()",
                 f"const E = {json.dumps(EVENTS[8:11])};")
    assert states == [["tool", "running", False], ["question", "waiting", True], ["answer", "running", False]]


@node
def test_ending_with_a_failure_or_a_limit_keeps_the_status_and_report():
    for status in ("failed", "limit", "stopped", "interrupted"):
        out = run("(function () { var r = A.newRun('p'); A.applyEvent(r, E); return [r.status, r.finished, A.isActive(r), A.statusText(r), r.end.report]; })()",
                  f"const E = {json.dumps({'seq': 1, 'type': 'end', 'status': status, 'report': 'why', 'steps': 2})};")
        assert out[0] == status and out[1] is True and out[2] is False and out[4] == "why"


@node
def test_what_is_shown_live_is_the_sentence_and_the_tool_never_the_code():
    live = lambda raw: run("A.describeLive(RAW)", f"const RAW = {json.dumps(raw)};")  # noqa: E731
    assert live("I will read the page.") == "I will read the page."
    assert live("I will read the page.\n@@ read_file index.html 1-40\n") == "I will read the page. Reading index.html 1-40..."
    assert live("Change it.\n@@ edit_file css/a.css\n<<<<<<< SEARCH\nsecret code here\n=======\nmore code") == "Change it. Editing css/a.css..."
    assert "code" not in live("@@ write_file a.js\n<<<<<<< CONTENT\nconsole.log('code')\n")
    assert live("@@ finish\nDone") == "Finishing..."
    assert live("<think>hidden thoughts</think>Visible.") == "Visible."
    assert live("x" * 500).__len__() == 220
    assert live("") == ""


@node
def test_text_for_a_new_step_starts_the_live_line_again_and_a_note_clears_it():
    out = run("(function () { var r = A.newRun('p'); var a = A.applyEvent(r, {seq: 1, type: 'text', step: 1, chunk: 'First '}); "
              "var b = A.applyEvent(r, {seq: 2, type: 'text', step: 1, chunk: 'part.'}); var c = A.applyEvent(r, {seq: 3, type: 'text', step: 2, chunk: 'Next.'}); "
              "var d = A.applyEvent(r, {seq: 4, type: 'note', step: 2, text: 'Next.'}); return [a[0].text, b[0].text, c[0].text, d[0].text, d[1].t, r.live]; })()")
    assert out == ["First", "First part.", "Next.", "", "note", ""]


@node
def test_labels_and_counters():
    assert run("[A.toolLabel('read_file', 'a.js'), A.toolLabel('list_files', ''), A.toolLabel('list_files', 'css'), A.toolLabel('search', '\"x\"'), "
               "A.toolLabel('edit_file', 'a.js (1 edit)'), A.toolLabel('write_file', 'b.css'), A.toolLabel('delete_file', 'c'), A.toolLabel('ask', ''), "
               "A.toolLabel('finish', ''), A.toolLabel('mystery', '')]") == [
        "Reading a.js", "Listing files", "Listing files in css", 'Searching "x"', "Editing a.js (1 edit)", "Writing b.css", "Deleting c",
        "Asking you a question", "Finishing", "mystery"]
    assert run("A.statusText({status: 'waiting'})") == "Waiting for you"
    assert run("A.statusText({status: 'limit'})") == "Stopped at a limit"
    assert run("[A.fmtElapsed(0), A.fmtElapsed(9.9), A.fmtElapsed(65), A.fmtElapsed(600), A.fmtElapsed(-3), A.fmtElapsed(undefined)]") == ["0:00", "0:09", "1:05", "10:00", "0:00", "0:00"]
    assert run("[A.stepText({step: 0, maxSteps: 30}), A.stepText({step: 3, maxSteps: 30}), A.stepText({step: 40, maxSteps: 30}), "
               "A.stepText({step: 1, maxSteps: 30, finished: true}), A.stepText({step: 7, maxSteps: 30, finished: true})]") == [
        "Starting", "Step 3 of 30", "Step 30 of 30", "1 step", "7 steps"]
    assert run("[A.isActive({status: 'running'}), A.isActive({status: 'waiting'}), A.isActive({status: 'stopping'}), A.isActive({status: 'done'}), "
               "A.isActive({status: 'running', finished: true}), A.isActive(null)]") == [True, True, True, False, False, False]


@node
def test_undo_run_is_offered_only_for_a_finished_run_that_changed_something():
    base = "{finished: true, end: {startCp: 'c0002'}, checkpoints: [{id: 'c0003'}]}"
    assert run(f"A.canUndoRun({base})") is True
    assert run("A.canUndoRun({finished: true, end: {startCp: 'c0002'}, checkpoints: []})") is False
    assert run("A.canUndoRun({finished: true, end: {startCp: null}, checkpoints: [{id: 'c1'}]})") is False
    assert run("A.canUndoRun({finished: false, end: null, checkpoints: [{id: 'c1'}]})") is False
    assert run("A.canUndoRun(null)") is False


@node
def test_the_chat_given_to_the_agent_is_the_last_four_plain_messages():
    hist = [{"role": "user", "content": "one"}, {"role": "assistant", "content": "two", "plain": "two plain"}, {"role": "system", "content": "no"},
            {"role": "user", "content": "x" * 3000}, {"role": "assistant", "content": ""}, {"role": "user", "content": "three"},
            {"role": "assistant", "content": "four"}]
    out = run("A.historyFor(H)", f"const H = {json.dumps(hist)};")
    assert [m["content"][:5] for m in out] == ["two p", "xxxxx", "three", "four"] and [m["role"] for m in out] == ["assistant", "user", "user", "assistant"]
    assert len(out[1]["content"]) == 1500
    assert run("A.historyFor(null)") == []


@node
def test_the_report_kept_in_the_chat_names_the_changed_files():
    r = ("{status: 'done', end: {report: 'Added the form.'}, changed: ['index.html', 'style.css']}")
    assert run(f"A.reportMessage({r})") == "Added the form.\n[Files changed: index.html, style.css]"
    assert run("A.reportMessage({status: 'stopped', end: {report: ''}, changed: []})") == "Stopped"
    assert run("A.reportMessage({status: 'failed', end: null, changed: []})") == "Failed"


@node
def test_an_unknown_event_changes_nothing():
    out = run("(function () { var r = A.newRun('p'); var e = A.applyEvent(r, {seq: 7, type: 'something-new'}); return [e.length, r.since, r.status]; })()")
    assert out == [0, 7, "running"]


@node
def test_a_run_read_from_its_log_without_an_end_is_shown_as_interrupted():
    out = run("(function () { var r = A.newRun('p'); A.applyEvent(r, E[0]); A.applyEvent(r, E[5]); A.applyEvent(r, E[7]); "
              "var cut = A.synthEnd(r, {finished: true, status: 'interrupted'}); A.applyEvent(r, cut); "
              "return [cut.status, r.status, r.finished, A.canUndoRun(r), r.end.report.indexOf('restarted') > -1, A.synthEnd(r, {finished: true, status: 'done'})]; })()",
              f"const E = {json.dumps(EVENTS)};")
    assert out == ["interrupted", "interrupted", True, True, True, None]
    assert run("A.synthEnd(A.newRun('p'), {finished: false, status: 'running'})") is None
    assert run("A.synthEnd(A.newRun('p'), {finished: true, status: 'waiting'}).status") == "interrupted"
    assert run("A.synthEnd(A.newRun('p'), {finished: true, status: 'stopped'}).report") == ""


@node
def test_a_command_approval_makes_the_run_wait_and_a_decision_sets_it_going():
    events = [{"seq": 1, "type": "start", "goal": "g", "model": {"label": "Local"}, "can_run": True, "limits": {"max_steps": 30}},
              {"seq": 2, "type": "tool", "step": 1, "name": "run_check", "arg": "npm test"},
              {"seq": 3, "type": "approval", "step": 1, "id": "a1b2c3", "command": "npm test"},
              {"seq": 4, "type": "decision", "step": 1, "id": "a1b2c3", "decision": "always", "network": True}]
    out = run("(function () { var r = A.newRun('p'); var seen = []; E.forEach(function (e) { var fx = A.applyEvent(r, e); seen.push([e.type, r.status, r.approval && r.approval.command, r.ranCommands, fx.map(function (x) { return x.t; })]); }); return seen; })()",
              f"const E = {json.dumps(events)};")
    assert out[1] == ["tool", "running", None, True, ["live", "meta", "tool"]]
    assert out[2] == ["approval", "waiting", "npm test", True, ["meta", "approval"]]
    assert out[3] == ["decision", "running", None, True, ["meta", "decision"]]
    assert run("(function () { var r = A.newRun('p'); var fx = A.applyEvent(r, {seq: 1, type: 'approval', id: 'a1', command: 'make'}); return fx[1]; })()") == {"t": "approval", "id": "a1", "command": "make"}
    assert run("(function () { var r = A.newRun('p'); return A.applyEvent(r, {seq: 2, type: 'decision', id: 'a1', decision: 'decline'})[1]; })()") == {"t": "decision", "id": "a1", "decision": "decline", "network": False}


@node
def test_a_run_started_away_from_the_mac_says_commands_are_off():
    away = run("A.applyEvent(A.newRun('p'), {seq: 1, type: 'start', goal: 'g', can_run: false})")
    assert [x["t"] for x in away] == ["meta", "note"] and "started away from this Mac" in away[1]["text"]
    home = run("A.applyEvent(A.newRun('p'), {seq: 1, type: 'start', goal: 'g', can_run: true})")
    assert [x["t"] for x in home] == ["meta"]
    assert run("A.toolLabel('run_check', 'npm test')") == "Running npm test" and run("A.toolLabel('run_check', '')") == "Running a check"


# ── wiring ──

def test_the_script_is_stamped_and_loaded_after_the_project_script_and_before_the_page_script():
    projects = re.search(r'<script src="/static/vibe-projects\.js\?v=[0-9a-f]{8}"></script>', PAGE)
    agent = re.search(r'<script src="/static/vibe-agent\.js\?v=[0-9a-f]{8}"></script>', PAGE)
    assert projects and agent and projects.start() < agent.start() < PAGE.index("function doSend(")
    from tools import stamp_static
    assert "vibe-agent.js" in stamp_static.STAMPED
    assert stamp_static.main(True) == 0


def test_nothing_from_a_run_goes_in_as_html():
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setTimeout('", 'setTimeout("'):
        assert banned not in SRC, banned
    assert "if (text != null) n.textContent = text;" in SRC and SRC.count("textContent") >= 10      # every text goes in through textContent


def test_the_mode_switch_is_reachable_labelled_and_hidden_until_the_agent_exists():
    box = re.search(r'<div class="amode" id="amode"[^>]*>', PAGE).group(0)
    assert 'role="group"' in box and "aria-label=" in box and " hidden" in box
    for needed in ('id="amChat"', 'id="amAgent"', 'id="amModel"', 'aria-pressed="true"', 'aria-pressed="false"'):
        assert needed in PAGE[PAGE.index('id="amode"'):PAGE.index('id="vibeMic"')]
    assert 'aria-label="Model for the next message"' in PAGE, "chat and agent share the model list (D-22)"
    assert PAGE.index('id="amode"') < PAGE.index('id="vibeMic"') and PAGE.index('id="cm"') < PAGE.index('id="amode"')
    banner = re.search(r'<div class="abn" id="agentBanner"[^>]*>', PAGE).group(0)
    assert 'role="status"' in banner and " hidden" in banner


def test_send_and_stop_hand_over_to_the_agent_before_the_chat_flow_reads_anything():
    send = PAGE[PAGE.index("function doSend() {"):PAGE.index("var t = inp.value.trim();")]
    assert "VibeAgent.active()" in send and "VibeAgent.send(inp)" in send and "return;" in send
    stop = PAGE[PAGE.index("function doStop() {"):PAGE.index("if (_vibeAbort)")]
    assert "VibeAgent.running()" in stop and "VibeAgent.stop()" in stop


def test_the_project_script_gives_the_agent_what_it_needs_and_holds_the_editor_while_it_works():
    for name in ("reload: reload", "activate: activate", "restoreId: restoreId", "setBusy: setBusy", "diffNode: diffNode"):
        assert name in PROJ, name
    flush = PROJ[PROJ.index("function flush(keepalive) {"):PROJ.index("var id = P.id;")]
    assert "P.agent" in flush
    assert "readOnly: !!on" in PROJ and "agentBanner" in PROJ
    assert "window.VibeAgent.adopt(id)" in PROJ and "window.VibeAgent.leave()" in PROJ


def test_the_script_only_talks_to_the_agent_routes_and_the_diff_route():
    urls = set(re.findall(r"'(/api/[^']*)'", SRC)) | set(re.findall(r"base\([^,]+, '(/[^']*)'", SRC))
    assert "/api/vibe/agent/models" in urls
    for needed in ("'/run'", "/stop'", "/answer'", "/runs'", "&wait=20"):
        assert needed in SRC, needed
    assert "confirm_cloud" in SRC and "'/api/vibe/projects/' + S.run.pid + '/diff?from='" in SRC
    assert not re.search(r"fetch\(\s*['\"]https?://", SRC)


def test_the_approval_card_posts_exactly_what_was_clicked_and_shows_the_command_as_text():
    card = SRC[SRC.index("function drawApproval(eff) {"):SRC.index("function drawDecision(eff) {")]
    assert "el('code', 'agc-cmd', eff.command)" in card                       # the command goes in as text
    assert "'/approve'" in card and "decision: decision" in card and "network: net.checked" in card
    for label in ("Allow once", "Always allow here", "Decline"):
        assert label in card
    assert "decision === 'decline'" in card and card.count("createTextNode") == 1       # the only loose text is the fixed label
    assert "'/commands/forget'" in SRC and "'/commands')" in SRC


def test_the_only_call_that_approves_a_command_sits_in_the_cards_click_handlers():
    assert SRC.count("'/approve'") == 1
    body = SRC[SRC.index("function drawApproval(eff) {"):SRC.index("function drawDecision(eff) {")]
    assert body.count("addEventListener('click'") == 3 and body.count("decide('") == 3
