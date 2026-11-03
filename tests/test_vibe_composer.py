"""Vibe composer (P-069, docs/VIBE-COMPOSER-KEYS-DESIGN.md): our own /commands, @file, one key table, the project link,
the chips after a run, the selection block and the step times.

The rules are plain functions in static/vibe-agent.js, so Node runs them without a browser. The page side is checked
in headless Chrome by the builder; here only the wiring that keeps the rules in force is read from the source.
"""
from __future__ import annotations

import json
import os
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
    env = {**os.environ, "TZ": "UTC"}                  # the step times are shown in local time
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


def part(src: str, start: str, end: str) -> str:
    return src[src.index(start):src.index(end, src.index(start))]


ALL = {"projects": True, "agent": True, "cloud": True}
MODELS = {"cloud": {"label": "MiMo", "ready": True, "host": "api.example.test"}}
SPIES = ("var log = [], calls = {}; ['say', 'setMode', 'keep', 'model', 'stop', 'undo', 'clear'].forEach(function (k) { "
         "calls[k] = function (a) { log.push(a === undefined ? [k] : [k, a]); }; }); "
         f"var ALL = {json.dumps(ALL)}, MODELS = {json.dumps(MODELS)};")


# ── commands ────────────────────────────────────────────────────────────

@node
def test_each_command_calls_its_own_page_function():
    texts = ["/undo", "/stop", "/clear", "/agent", "/chat make it blue", "/model cloud", "/model local", "  /STOP  ", "/stop now"]
    out = run("T.map(function (t) { log = []; var c = A.parseCommand(t, ALL); A.runCommand(c, calls, ALL, MODELS, 'local'); "
              "return [c.name, log]; })", SPIES + f" var T = {json.dumps(texts)};")
    assert out == [["undo", [["undo"]]], ["stop", [["stop"]]], ["clear", [["clear"]]], ["agent", [["setMode", "agent"]]],
                   ["chat", [["setMode", "chat"], ["keep", "make it blue"]]], ["model", [["model", "cloud"]]],
                   ["model", [["model", "local"]]], ["stop", [["stop"]]], ["stop", [["stop"]]]]
    said = run("['/help', '/model', '/model gpt'].map(function (t) { log = []; A.runCommand(A.parseCommand(t, ALL), calls, ALL, "
               "MODELS, 'cloud'); return log; })", SPIES)
    assert [len(x) for x in said] == [1, 1, 1] and all(x[0][0] == "say" for x in said)     # these only write a line
    assert said[0][0][1].startswith("Commands (type one alone in the box):\n/help - ")
    assert "Model for the next message: MiMo (cloud)." in said[1][0][1] and said[2][0][1] == "Use /model local or /model cloud."
    # the page hands each call to its own function; while an answer is written only /stop runs
    cmd = part(SRC, "function command(inp, busy) {", "// ── the composer: @file")
    assert "if (!cmd || (busy && cmd.name !== 'stop')) return false;" in cmd
    for wiring in ("setMode: setMode", "model: pickModel", "stopRun()", "root.doStop()", "VP.undo()", "root.nS()"):
        assert wiring in cmd, wiring
    send = part(PAGE, "function sendVibe() {", "function doSend() {")
    assert send.index("VibeAgent.command(inp, true)") < send.index("VibeAgent.command(inp, false)") < send.index("chatCloud()")


@node
def test_help_lists_only_commands_whose_feature_exists():
    out = run("[{}, {projects: true}, {projects: true, agent: true}, ALL].map(function (c) { "
              "return A.commandList(c).map(function (x) { return x.name; }); })", SPIES)
    assert out == [["help", "stop", "clear"], ["help", "stop", "undo", "clear"],
                   ["help", "stop", "undo", "clear", "agent", "chat"],
                   ["help", "stop", "undo", "clear", "agent", "chat", "model"]]
    bare = run("A.helpText({})")
    assert "/help" in bare and "/stop" in bare and "/clear" in bare
    for absent in ("/undo", "/agent", "/chat", "/model", "/plan", "/compact", "/init"):
        assert absent not in bare, absent
    assert not any(w in run("A.helpText(ALL)", SPIES) for w in ("/plan", "/compact", "/init"))
    assert run("[A.modelChoice('cloud', {}), A.modelChoice('cloud', {cloud: {label: 'M', ready: false, reason: 'no key'}}), "
               "A.modelChoice(' Cloud ', MODELS), A.modelChoice('', MODELS)]", SPIES) == [
        {"error": "No cloud model is set up."}, {"error": "The cloud model is not ready: no key."}, {"model": "cloud"}, {"show": True}]


@node
def test_an_unknown_or_malformed_word_is_sent_as_text():
    texts = ["", "/", "//stop", "/ stop", "/stop/", "/stop-now", "/undo2", "/plan", "/compact", "/init", "/deploy",
             "hello /stop", "/usr/bin/env node", "please /help"]
    assert run(f"{json.dumps(texts)}.map(function (t) {{ return A.parseCommand(t, ALL); }})", SPIES) == [None] * len(texts)
    # a command whose feature is missing here is text too
    assert run("[A.parseCommand('/undo', {}), A.parseCommand('/agent', {projects: true}), A.parseCommand('/model cloud', "
               "{projects: true, agent: true}), A.parseCommand('/help', {})]") == [None, None, None, {"name": "help", "arg": ""}]


# ── /model never skips the cloud dialog ─────────────────────────────────

@node
def test_model_only_selects_and_no_request_has_confirm_cloud_without_the_dialog():
    out = run("[A.runRequest('Add a form', 'cloud', undefined, []), A.runRequest('Add a form', 'cloud', 'yes', []), "
              "A.runRequest('Add a form', 'cloud', 1, []), A.runRequest('Add a form', 'cloud', true, [{role: 'user', content: 'hi'}]), "
              "A.runRequest('Add a form', 'local', true), A.runRequest('Add a form', 'gpt', true)]")
    assert [(b["model"], b["confirm_cloud"]) for b in out] == [("cloud", False), ("cloud", False), ("cloud", False),
                                                                ("cloud", True), ("local", False), ("local", False)]
    assert out[3] == {"goal": "Add a form", "model": "cloud", "confirm_cloud": True, "history": [{"role": "user", "content": "hi"}]}
    assert out[4]["history"] == []
    # /model cloud only calls the model picker: nothing is started or sent
    assert run("(function () { log = []; A.runCommand(A.parseCommand('/model cloud', ALL), calls, ALL, MODELS, 'local'); "
               "return log; })()", SPIES) == [["model", "cloud"]]
    pick = part(SRC, "function pickModel(m) {", "// A message that is one of our commands")
    assert "api(" not in pick and "startRun" not in pick and "send(" not in pick
    # the run's body comes from runRequest with the dialog's own answer; the dialog refuses without the shell
    start = part(SRC, "function startRun(text, inp) {", "function stopRun() {")
    assert "var body = runRequest(text, model, ok, earlier);" in start and "if (ok !== true)" in start
    assert start.index("confirmCloud()") < start.index("runRequest(")
    cloud = part(SRC, "function confirmCloud() {", "function chatCloud()")
    assert "if (!(root.CodecShell && root.CodecShell.ask)) return Promise.resolve(false);" in cloud
    assert "confirm_cloud: S.model" not in SRC and "confirm_cloud: true" not in SRC


# ── @file ───────────────────────────────────────────────────────────────

PATHS = ["index.html", "style.css", "js/app.js", "js/util.js", "README.md"]


@node
def test_at_finds_the_project_files_and_picking_inserts_only_their_paths():
    found = run("[A.mentionAt('see @st', 7), A.mentionAt('@', 1), A.mentionAt('mail me@x', 9), A.mentionAt('see @st and', 7), "
                "A.mentionAt('see @st and', 11), A.mentionAt('a @b @c', 7)]")
    assert found == [{"start": 4, "query": "st"}, {"start": 0, "query": ""}, None, {"start": 4, "query": "st"}, None,
                     {"start": 5, "query": "c"}]
    setup = f"var P = {json.dumps(PATHS)};"
    assert run("[A.mentionMatches(P, 'app'), A.mentionMatches(P, 'js'), A.mentionMatches(P, 'S')[0], A.mentionMatches(P, 'zzz'), "
               "A.mentionMatches(P, '').length, A.mentionMatches([], 'a')]", setup) == [
        ["js/app.js"], ["js/app.js", "js/util.js"], "style.css", [], 5, []]
    picked = run("[A.insertMention('see @st now', {start: 4, query: 'st'}, 7, 'style.css', P), "
                 "A.insertMention('fix @in', {start: 4, query: 'in'}, 7, 'index.html', P), "
                 "A.insertMention('fix @in', {start: 4, query: 'in'}, 7, '../etc/passwd', P), "
                 "A.insertMention('fix @in', {start: 4, query: 'in'}, 7, '.env', P), "
                 "A.insertMention('fix @in', null, 7, 'index.html', P)]", setup)
    assert picked == [{"text": "see style.css now", "caret": 13}, {"text": "fix index.html ", "caret": 15}, None, None, None]
    # the page lists only the open project's files, and draws them as text
    paths = part(SRC, "function mentionPaths()", "function updateMention()")
    assert "VP.on() ? VP.paths() : []" in paths
    assert "paths: function () { return P.id ? order(Object.keys(P.files)) : []; }" in PROJ
    draw = part(SRC, "function renderMention() {", "function closeMention() {")
    assert "el('div', 'vm-opt' + (i === M.sel ? ' sel' : ''), p)" in draw and "innerHTML" not in draw
    assert "insertMention(vi.value, at, vi.selectionStart, path, mentionPaths())" in SRC
    assert 'id="vmention" role="listbox"' in PAGE


# ── keys ────────────────────────────────────────────────────────────────

@node
def test_composer_keys_ime_cmd_enter_touch_and_esc():
    cases = [
        ({"key": "Enter"}, {}, "send"),
        ({"key": "Enter", "isComposing": True}, {}, ""),
        ({"key": "Enter", "keyCode": 229}, {}, ""),
        ({"key": "Enter", "metaKey": True}, {}, "send"),
        ({"key": "Enter", "ctrlKey": True}, {}, "send"),
        ({"key": "Enter", "shiftKey": True}, {}, ""),
        ({"key": "Enter", "altKey": True}, {}, ""),
        ({"key": "Enter"}, {"touch": True}, ""),
        ({"key": "Enter", "metaKey": True}, {"touch": True}, "send"),
        ({"key": "Enter", "metaKey": True, "isComposing": True}, {}, ""),
        ({"key": "Escape"}, {"running": True}, "stop"),
        ({"key": "Escape"}, {}, ""),
        ({"key": "Escape", "isComposing": True}, {"running": True}, ""),
        ({"key": "a"}, {}, ""),
    ]
    got = run(f"{json.dumps([[e, c] for e, c, _ in cases])}.map(function (x) {{ return A.composerKey(x[0], x[1]); }})")
    assert got == [want for _, _, want in cases]
    assert run("A.composerKey(null, {})") == ""
    handler = part(PAGE, "document.getElementById('vi').addEventListener('keydown', function(e) {", "document.getElementById('vi').addEventListener('input'")
    assert "VibeAgent.keyAction(e)" in handler and "e.preventDefault(); sendVibe();" in handler
    assert "!e.isComposing ? 'send' : ''" in handler                                  # without the script, IME is still safe
    assert "(hover: none) and (pointer: coarse)" in SRC
    init = part(SRC, "function init() {", "function active() {")
    assert "e.key === 'Escape' && !e.isComposing && running() && !M.open" in init and "stopRun()" in init


@node
def test_the_list_keys_move_pick_and_close():
    cases = [
        ({"key": "ArrowDown"}, "next"), ({"key": "ArrowUp"}, "prev"), ({"key": "Enter"}, "pick"), ({"key": "Tab"}, "pick"),
        ({"key": "Tab", "shiftKey": True}, ""), ({"key": "Escape"}, "close"), ({"key": "Enter", "isComposing": True}, ""),
        ({"key": "Enter", "metaKey": True}, "send"), ({"key": "Enter", "shiftKey": True}, ""),
    ]
    got = run(f"{json.dumps([e for e, _ in cases])}.map(function (e) {{ return A.composerKey(e, {{mention: true, running: true}}); }})")
    assert got == [want for _, want in cases]                     # Esc on an open list closes it and leaves the run alone
    act = part(SRC, "function keyAction(e) {", "// ── the composer: an editor selection")
    assert "e.stopImmediatePropagation(); closeMention();" in act and "pickMention(M.sel)" in act
    assert "if (act === 'send') { closeMention(); return 'send'; }" in act         # Cmd+Enter does not leave the list open


# ── the project link ────────────────────────────────────────────────────

@node
def test_the_project_link_takes_only_a_project_id():
    queries = ["?project=my-site", "?project=ab", "?project=../x", "?project=My-Site", "?project=a%2Fbc", "", "?x=1",
               "?project=-abc", "?project=abc&project=zzz", "?project=" + "a" * 61, "?project=" + "a" * 62, "?project=abc%0A",
               "project=abc"]
    assert run(f"{json.dumps(queries)}.map(A.projectFromQuery)") == [
        "my-site", None, None, None, None, None, None, None, "abc", "a" * 61, None, None, "abc"]
    link = part(PAGE, "function _openFromLink() {", "async function delSes(")
    assert link.index("if (!pid) return;") < link.index("fetch(")                   # a bad id: no request at all
    assert "VibeAgent.core.projectFromQuery(location.search)" in link and "VibeProj.open(pid)" in link and "ldSes(s.id)" in link
    assert link.index("sesId = ") < link.index("fetch(")                          # takes over from _reopenLiveRun
    boot = part(PAGE, "if (ed) _reopenLiveRun();", "}, function () { _editorFailed(); });")
    assert "if (ed) _openFromLink();" in boot


# ── after a run ─────────────────────────────────────────────────────────

@node
def test_the_after_run_chips_follow_the_projects_files():
    npm = json.dumps({"scripts": {"test": "node test.js"}})
    default = json.dumps({"scripts": {"test": 'echo "Error: no test specified" && exit 1'}})
    out = run(f"[A.afterRunChips(['index.html', 'style.css'], null), A.afterRunChips(['index.html', 'package.json'], {json.dumps(npm)}), "
              f"A.afterRunChips(['index.html', 'package.json'], {json.dumps(default)}), A.afterRunChips(['app.py', 'test_app.py'], null), "
              "A.afterRunChips(['main.py', 'lib/calc_test.py'], 'not json'), A.afterRunChips(['main.py'], null), A.afterRunChips([], null)]")
    serve = {"label": "Show it on localhost", "mode": "serve", "command": ""}
    assert out == [[serve], [{"label": "Run the checks", "mode": "run", "command": "npm test"}, serve], [serve],
                   [{"label": "Run the checks", "mode": "run", "command": "python3 -m pytest -q"}],
                   [{"label": "Run the checks", "mode": "run", "command": "python3 -m pytest -q"}], [], []]
    nxt = part(SRC, "function drawNext(run) {", "// ── the composer: our own commands")
    assert "VP.onMac()" in nxt and "VP.runDialog({ mode: now.mode, command: now.command })" in nxt
    assert "el('button', 'agc-btn quiet', chip.label)" in nxt and "innerHTML" not in nxt
    assert ".then(function () { drawNext(run); });" in part(SRC, "function finished(run) {", "function clearInput(inp) {")
    dialog = part(PROJ, "function runDialog() {", "function closeRun() {")
    assert "(opts.mode === 'run' && opts.command) || s.command" in dialog and "opts.mode === 'run' || opts.mode === 'serve'" in dialog
    assert "api(" not in dialog                                                    # it only fills the dialog: Start runs it


# ── the selection ───────────────────────────────────────────────────────

@node
def test_a_selection_becomes_a_block_within_its_cap():
    out = run("[A.selectionBlock('js/app.js', 12, ['a', 'b', 'c']), A.selectionBlock('index.html', 5, ['<h1>Hi</h1>']), "
              "A.selectionBlock('big.js', 1, ['x'.repeat(1000), 'y'.repeat(1000), 'z'.repeat(1000), 'w', 'v']), "
              "A.selectionBlock('one.js', 3, ['q'.repeat(5000)]).length]")
    assert out[0] == "[SELECTION js/app.js:12-14]\na\nb\nc\n[END SELECTION]"
    assert out[1] == "[SELECTION index.html:5]\n<h1>Hi</h1>\n[END SELECTION]"
    assert out[2].startswith("[SELECTION big.js:1-2]\n" + "x" * 1000 + "\n" + "y" * 1000 + "\n[END SELECTION]")
    assert out[2].endswith("\n(3 more lines of the selection left out)")
    assert out[3] == len("[SELECTION one.js:3]\n") + 3000 + len("\n[END SELECTION]")
    sel = part(SRC, "function currentSelection() {", "function renderSelection() {")
    assert "VP.fileText(path) !== model.getValue()" in sel and "sel.endColumn === 1" in sel    # only a project file's own lines
    assert "VibeAgent.attachEditor(ed)" in PAGE and 'id="vselBtn"' in PAGE


# ── the run card ────────────────────────────────────────────────────────

@node
def test_a_step_tooltip_says_when_it_ran():
    assert run("[A.clock(1000), A.stepTitle(2, 1000, 1003.4), A.stepTitle(1, 1000, 0), A.stepTitle(3, 0, 0), A.clock('x')]") == [
        "00:16:40", "Step 2: 00:16:40 to 00:16:43 (3 s)", "Step 1: started 00:16:40", "Step 3", ""]
    fx = run("(function () { var r = A.newRun('p1'); var a = A.applyEvent(r, {seq: 1, t: 1000, type: 'tool', step: 1, name: 'read_file', "
             "arg: 'a.js'}); var b = A.applyEvent(r, {seq: 2, t: 1002, type: 'result', step: 1, ok: true, tool: 'read_file'}); "
             "return [a[2].began, b[0].began, b[0].ended]; })()")
    assert fx == [1000, 1000, 1002]
    assert "li.title = stepTitle(ownStep(eff.step), eff.began, 0);" in SRC
    assert "row.li.title = stepTitle(ownStep(eff.step), eff.began, eff.ended);" in SRC


def test_the_run_card_views_and_the_changes_entry_are_wired():
    card = part(SRC, "function buildCard(run) {", "function drawMeta() {")
    assert "liveLine.setAttribute('aria-live', 'polite');" in card
    assert "'Compact'" in card and "'Raw replies'" in card and "innerHTML" not in card
    replies = part(SRC, "function drawReplies(c, replies) {", "function drawMeta() {")
    assert "el('pre', 'agc-out', r.text || '')" in replies and "innerHTML" not in replies     # a reply goes in as text
    poll = part(SRC, "function poll() {", "function releaseUi() {")
    assert "card && card.raw ? '&replies=' + card.replyStep : ''" in poll        # the default poll never asks for replies
    assert SRC.count("&replies=") == 2
    views = part(SRC, "function setCompact(on) {", "function drawReplies(c, replies) {")
    assert "document.querySelectorAll('.agc')" in views                       # Compact on any card applies to every card
    assert "if (c.raw && c.run && c.run.id) fetchReplies(c, c.run);" in views    # an earlier run's card asks for its own run
    assert ".agc-ea[hidden]{display:none}" in PAGE and ".agc-raw-on .agc-raw{display:block}" in PAGE
    changes = part(PROJ, "function changesRow(newest) {", "function versionRow(cp, newest) {")
    assert "'&to=working'" in changes and "activate(f.path); closeVersions();" in changes and "innerHTML" not in changes
    flush = part(PROJ, "function flush(keepalive) {", "// ── opening, creating, leaving a project")
    assert "if (jobs.length && P.changes && P.changes.id === id && versionsOpen()) loadChanges();" in flush
