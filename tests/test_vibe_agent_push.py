"""Phone push when a Vibe run waits or ends (P-070, docs/VIBE-PUSH-DESIGN.md).

A push carries only the fixed line of its kind (codec_push.KINDS). A scripted run (tests/vibe_agent_fakes.py) asks
for a command approval and a question, then finishes. The push side is the fake endpoint of tests/test_push.py:
codec_push.STATE_PATH and _post are patched and the Keychain is a dict, never the owner's ~/.codec/push.json.
"""
from __future__ import annotations

import itertools
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codec_push
import codec_vibe_agent as va
import codec_vibe_agent_cmd as cmd
from test_push import _decrypt, _device, push  # noqa: F401  (push is a fixture, used by name)
from vibe_agent_fakes import (  # noqa: F401  (fixtures are used by name)
    agent_env, audits, cfg, end_of, kinds, make_project, scripted, wait_finished, wait_for,
)

REPO = Path(__file__).resolve().parent.parent
PAGE = (REPO / "codec_vibe.html").read_text(encoding="utf-8")
REAL_PUSH = va._push                       # taken at import, before the fixture puts its no-op in

VIBE = {
    "vibe_approval": ("approvals", "Vibe wants to run a command", "Approve or decline it on your Mac.", "/vibe", 600, "high"),
    "vibe_question": ("questions", "Vibe has a question for you", "Tap to answer it.", "/vibe", 600, "high"),
    "vibe_done": ("agents", "A Vibe run has ended", "Open its project in Vibe to see the report.", "/vibe", 86400, "normal"),
}
ORDER = ["vibe_approval", "vibe_question", "vibe_done"]
GOAL = "Make the Larkspur heading teal"
TITLE = "Quillwort atelier"
QUESTION = "Which shade of teal?"
ANSWER = "Deep teal"
FILES = {"index.html": "<h1>Larkspur greenhouse</h1>\n", "app.js": "console.log('marigold');\n"}
SCRIPT = ["Check it.\n@@ run_check\nnpm test\n",
          f"One question.\n@@ ask\n{QUESTION}\n- {ANSWER}\n- Light teal\n",
          "@@ finish\nMade the heading teal.\n"]


@pytest.fixture
def commands(monkeypatch):
    """The sandbox runner replaced by a recorder, so run_check is offered here and on Linux CI."""
    calls = []
    monkeypatch.setattr(cmd, "available", lambda: True)
    monkeypatch.setattr(va, "_run_command", lambda project, root, command, **kw: calls.append(command) or cmd.Result(0, "3 passed", 0.4))
    return calls


@pytest.fixture
def phone(push, monkeypatch):
    """The real hook and codec_push on test_push's fake endpoint, one subscribed device, sends made at once."""
    class Now:
        def __init__(self, target, args=(), **kw):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(va, "_push", REAL_PUSH)
    monkeypatch.setattr(codec_push, "threading", SimpleNamespace(Thread=Now))
    push.mod.ensure_key()
    push.sub, push.key, push.auth = _device()
    push.mod.add_device(push.sub)
    return push


def drive(pid):
    """One whole run: approve the command once, answer the question, wait until its thread is done."""
    run_id = va.start_run(pid, GOAL, can_run=True)["run"]
    ev = wait_for(lambda: next((e for e in va.get_run(pid, run_id)["events"] if e["type"] == "approval"), None))
    va.approve_run(pid, run_id, ev["id"], "once", False)
    q = wait_for(lambda: next((e for e in va.get_run(pid, run_id)["events"] if e["type"] == "question"), None))
    va.answer_run(pid, run_id, q["id"], ANSWER)
    view = wait_finished(pid, run_id)
    va._RUNS[run_id]._thread.join(10)       # the end push comes after the end event
    return run_id, view


def sent(phone):
    return [json.loads(_decrypt(data, phone.key, phone.auth)) for _endpoint, data, _headers in phone.posts]


# ── the table ──

def test_three_vibe_kinds_with_fixed_short_lines_on_the_existing_switches():
    assert {k: v for k, v in codec_push.KINDS.items() if k.startswith("vibe")} == VIBE
    assert set(codec_push.KINDS) == {"approval", "question", "briefing", "agent_done", "agent_blocked", "agent_stopped",
                                     "test", *VIBE}
    assert codec_push.SWITCHES == ("approvals", "questions", "briefing", "agents")      # no new switch
    for kind, (switch, title, body, url, ttl, urgency) in VIBE.items():
        assert switch in codec_push.SWITCHES and url == "/vibe"
        assert 0 < len(title) <= 60 and 0 < len(body) <= 60, kind
        assert not re.search(r"[{}%<>$\[\]]", title + body), "no placeholder in a fixed line"
        assert codec_push.payload(kind) == {"kind": kind, "title": title, "body": body, "url": url}


# ── a run pushes, with nothing of the run in it ──

def test_a_run_pushes_once_at_the_approval_the_question_and_the_end(phone, scripted, commands):
    pid = make_project(FILES, title=TITLE)
    scripted(SCRIPT)
    run_id, view = drive(pid)
    assert end_of(view)["status"] == "done" and commands == ["npm test"]
    got = sent(phone)
    assert [p["kind"] for p in got] == ORDER
    for p in got:
        _switch, title, body, url, ttl, urgency = codec_push.KINDS[p["kind"]]
        assert p == {"kind": p["kind"], "title": title, "body": body, "url": url}
    private = [GOAL, "Larkspur", TITLE, "Quillwort", pid, run_id, "index.html", "app.js", "marigold", "greenhouse",
               "npm test", QUESTION, ANSWER, "teal", "Made the heading"]
    for (_endpoint, data, headers), p in zip(phone.posts, got):
        plain = _decrypt(data, phone.key, phone.auth).decode("utf-8")
        clear = json.dumps({k: v for k, v in headers.items() if k != "Authorization"})   # what the push service reads
        assert headers["Topic"] == "codec-" + p["kind"].replace("_", "-")
        for text in private:
            assert text not in plain and text not in clear, (p["kind"], text)
    logged = json.dumps(phone.events, default=str)
    assert logged.count('"push_sent"') == 3
    for text in private:
        assert text not in logged


@pytest.mark.parametrize("switch,kind", [("approvals", "vibe_approval"), ("questions", "vibe_question"), ("agents", "vibe_done")])
def test_with_the_matching_switch_off_that_push_is_not_sent(phone, scripted, commands, switch, kind):
    phone.mod.set_types({switch: False})
    pid = make_project(FILES, title=TITLE)
    scripted(SCRIPT)
    _, view = drive(pid)
    assert end_of(view)["status"] == "done"
    assert [p["kind"] for p in sent(phone)] == [k for k in ORDER if k != kind]


def test_all_three_switches_off_send_nothing(phone, scripted, commands):
    phone.mod.set_types({"approvals": False, "questions": False, "agents": False})
    pid = make_project(FILES, title=TITLE)
    scripted(SCRIPT)
    drive(pid)
    assert phone.posts == []


# ── the run does not depend on push ──

@pytest.mark.parametrize("broken", ["notify raises", "codec_push cannot be imported"])
def test_the_run_is_the_same_when_push_is_unavailable(scripted, commands, monkeypatch, broken):
    pid = make_project(FILES, title=TITLE)
    scripted(SCRIPT)
    _, before = drive(pid)                  # with the fixture's no-op hook
    monkeypatch.setattr(va, "_push", REAL_PUSH)
    if broken == "notify raises":
        def boom(kind):
            raise RuntimeError("push service down")
        monkeypatch.setattr(codec_push, "notify", boom)
    else:
        monkeypatch.setitem(sys.modules, "codec_push", None)
    scripted(SCRIPT)
    _, after = drive(pid)
    assert end_of(after)["status"] == end_of(before)["status"] == "done"
    assert end_of(after)["report"] == end_of(before)["report"] == "Made the heading teal."
    assert kinds(after) == kinds(before)


def test_the_owners_stop_sends_no_end_push_but_an_unanswered_wait_does(scripted, monkeypatch, cfg):
    got = []
    monkeypatch.setattr(va, "_push", got.append)
    pid = make_project(FILES, title=TITLE)
    scripted([SCRIPT[1]])
    run_id = va.start_run(pid, GOAL)["run"]
    wait_for(lambda: any(e["type"] == "question" for e in va.get_run(pid, run_id)["events"]))
    va.stop_run(pid, run_id)
    assert end_of(wait_finished(pid, run_id))["status"] == "stopped"
    va._RUNS[run_id]._thread.join(10)
    assert got == ["vibe_question"]

    got.clear()
    cfg["vibe_agent"] = {"wait_seconds": 5}
    clock = itertools.count(0, 1)
    monkeypatch.setattr(va, "_mono", lambda: next(clock))
    monkeypatch.setattr(va, "WAIT_STEP", 0.01)
    scripted([SCRIPT[1]])
    run_id = va.start_run(pid, GOAL)["run"]
    view = wait_finished(pid, run_id)
    va._RUNS[run_id]._thread.join(10)
    assert "No answer" in end_of(view)["report"] and got == ["vibe_question", "vibe_done"]


# ── the page learns which run is live ──

def test_the_live_run_is_named_by_ids_only_while_it_runs_or_waits(scripted, monkeypatch):
    from routes.vibe_agent import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    nothing = {"project": None, "run": None, "status": None}
    assert va.live_run() == nothing and client.get("/api/vibe/agent/live").json() == nothing
    pid = make_project(FILES, title=TITLE)
    scripted([SCRIPT[1]])
    run_id = va.start_run(pid, GOAL)["run"]
    wait_for(lambda: va.live_run()["status"] == "waiting")
    r = client.get("/api/vibe/agent/live")
    assert r.status_code == 200 and r.json() == {"project": pid, "run": run_id, "status": "waiting"}
    for text in (GOAL, TITLE, QUESTION):
        assert text not in r.text
    va.stop_run(pid, run_id)
    wait_finished(pid, run_id)
    assert client.get("/api/vibe/agent/live").json() == nothing
    monkeypatch.setenv("VIBE_AGENT_ENABLED", "false")
    assert client.get("/api/vibe/agent/live").status_code == 404


node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

_SW = r"""
const vm = require('vm'), fs = require('fs');
const code = fs.readFileSync(process.argv[1], 'utf8');
const payloads = JSON.parse(process.argv[2]);
const handlers = {}, shown = [], opened = [], navigated = [], focused = [];
let windows = [];
const self = {
  location: { origin: 'https://codec.test' }, addEventListener: (t, f) => { handlers[t] = f; },
  skipWaiting: async () => {},
  registration: { showNotification: async (title, o) => { shown.push({ title, body: o.body, tag: o.tag, url: o.data.url }); } },
  clients: { claim: async () => {}, matchAll: async () => windows, openWindow: async (u) => { opened.push(u); return null; } },
};
vm.runInNewContext(code, { self, caches: {}, fetch: async () => null, Response, URL, Promise, console });
async function fire(name, ev) {
  const waits = []; ev.waitUntil = (p) => waits.push(p); handlers[name](ev); await Promise.all(waits);
}
(async () => {
  for (const p of payloads) await fire('push', { data: { json: () => p } });
  for (const s of shown) {
    windows = [{ url: 'https://codec.test/chat', focus: async function () { focused.push(this.url); return this; },
                 navigate: async function (u) { navigated.push(u); return this; } }];
    await fire('notificationclick', { notification: { data: { url: s.url }, close: () => {} } });
    windows = [];
    await fire('notificationclick', { notification: { data: { url: s.url }, close: () => {} } });
  }
  console.log(JSON.stringify({ shown, navigated, opened, focused }));
})();
"""


@node
def test_the_worker_shows_each_vibe_line_and_a_tap_opens_vibe():
    payloads = [codec_push.payload(k) for k in ORDER]
    out = subprocess.run(["node", "-e", _SW, str(REPO / "static" / "sw.js"), json.dumps(payloads)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["shown"] == [{"title": VIBE[k][1], "body": VIBE[k][2], "tag": "codec-" + k, "url": "/vibe"} for k in ORDER]
    assert r["navigated"] == ["/vibe"] * 3 and r["focused"] == ["https://codec.test/chat"] * 3
    assert r["opened"] == ["/vibe"] * 3


def _page_fn(name: str) -> str:
    start = PAGE.index(f"\nfunction {name}(") + 1
    return PAGE[start:PAGE.index("\n}\n", start) + 3]


_REOPEN = r"""
var CASES = JSON.parse(process.argv[1]);
async function one(c) {
  globalThis.sesId = 'fresh';
  var opened = [];
  globalThis.ldSes = function (id) { opened.push(id); sesId = id; };
  globalThis.VibeProj = { on: function () { return !!c.projectOpen; } };
  globalThis.window = { VibeProj: VibeProj };
  globalThis.fetch = function (url) {
    if (c.down) return Promise.reject(new Error('offline'));
    if (url === '/api/vibe/sessions' && c.pickWhileLoading) sesId = 'picked';
    var body = url === '/api/vibe/agent/live' ? c.live : url === '/api/vibe/sessions' ? c.sessions : null;
    return Promise.resolve({ ok: body !== null, json: function () { return Promise.resolve(body); } });
  };
  await _reopenLiveRun();
  return opened;
}
(async () => { var out = []; for (var c of CASES) out.push(await one(c)); console.log(JSON.stringify(out)); })();
"""


@node
def test_the_page_opened_from_a_push_reopens_the_live_runs_project_chat_and_nothing_else():
    live = {"project": "p1", "run": "r0123abcd", "status": "waiting"}
    sessions = [{"id": "s9", "project_id": "p2"}, {"id": "s2", "project_id": "p1"}, {"id": "s1", "project_id": "p1"},
                {"id": "s0", "project_id": None}]
    cases = [
        {"live": live, "sessions": sessions},                                    # the newest chat of that project
        {"live": {"project": None, "run": None, "status": None}, "sessions": sessions},
        {"live": live, "sessions": [{"id": "s9", "project_id": "p2"}]},           # no chat for it here
        {"live": live, "sessions": sessions, "pickWhileLoading": True},          # the owner chose a session first
        {"live": live, "sessions": sessions, "projectOpen": True},               # a project is already open
        {"live": None, "sessions": sessions},                                    # the route answered an error
        {"live": live, "sessions": sessions, "down": True},                      # the request failed
    ]
    script = _page_fn("_reopenLiveRun") + _REOPEN
    out = subprocess.run(["node", "-e", script, json.dumps(cases)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == [["s2"], [], [], [], [], [], []]


def test_the_page_looks_for_a_live_run_once_the_editor_exists():
    boot = PAGE[PAGE.index("ed = monaco.editor.create("):PAGE.index("var LANG_EXT")]
    assert "if (ed) _reopenLiveRun();" in boot
    assert boot.index("} catch (e) { ed = null; _editorFailed(); }") < boot.index("if (ed) _reopenLiveRun();")
    src = _page_fn("_reopenLiveRun")
    assert "fetch('/api/vibe/agent/live')" in src and "fetch('/api/vibe/sessions')" in src and "ldSes(s.id)" in src
    assert "innerHTML" not in src and "VibeProj.open" not in src
