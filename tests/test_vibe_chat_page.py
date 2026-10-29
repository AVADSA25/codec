"""Vibe chat on the page (static/vibe-chat.js, docs/VIBE-CHAT-ROUTE-DESIGN.md, D-22).

The script is plain functions on values, so Node runs them without a browser; `fetch` is a fake that records the calls.
The rest checks the wiring in codec_vibe.html: the script is stamped and loaded in the right place, the cloud dialog
comes before the send, and the page saves the typed text.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import codec_vibe_chat as vc
from test_vibe_chat import env, frames, tokens  # noqa: F401  (env is a fixture)

REPO = Path(__file__).resolve().parent.parent
JS = REPO / "static" / "vibe-chat.js"
SRC = JS.read_text(encoding="utf-8")
PAGE = (REPO / "codec_vibe.html").read_text(encoding="utf-8")
AGENT = (REPO / "static" / "vibe-agent.js").read_text(encoding="utf-8")

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

HIST = [
    {"role": "user", "content": "make it blue\n\n[PROJECT FILES]\nOpen file: index.html\n```html file:index.html\n<h1>x</h1>\n```",
     "plain": "make it blue"},
    {"role": "assistant", "content": "Done.\n```html file:index.html\n<h1 class=blue>x</h1>\n```", "plain": "Done.\n[Files written: index.html]"},
    {"role": "user", "content": "a turn loaded from vibe.db"},
    {"role": "user", "content": "fix it\n\n[CURRENT CODE]\n```\nprint(1)\n```", "plain": ""},
]


def run(body: str, setup: str = ""):
    """Run `body` (a JS expression) with a fresh copy of the script and a fake fetch; return its JSON value."""
    script = (f"globalThis.calls = []; {setup}\n"
              f"const C = require({json.dumps(str(JS))});\n"
              f"Promise.resolve({body}).then(v => console.log(JSON.stringify(v)));")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


def fake_fetch(status_reply, post_replies):
    """status_reply: [httpStatus, json] or 'error'; post_replies: list of httpStatus, one per POST in order."""
    return (f"const STATUS = {json.dumps(status_reply)}; const POSTS = {json.dumps(post_replies)};\n"
            "globalThis.fetch = (url, opts) => { opts = opts || {}; calls.push({url: url, method: opts.method || 'GET', body: opts.body ? JSON.parse(opts.body) : null});\n"
            "  if (url === '/api/vibe/chat/status') return STATUS === 'error' ? Promise.reject(new Error('down')) : Promise.resolve({status: STATUS[0], ok: STATUS[0] < 300, json: () => Promise.resolve(STATUS[1])});\n"
            "  const s = POSTS.shift(); return Promise.resolve({status: s, ok: s < 300, json: () => Promise.resolve({})}); };\n")


OPTS = "{messages: [{role: 'user', content: 'hi'}], system: 'SYS', project: true, workdir: '/w', cloud: false}"


# ── what is saved ───────────────────────────────────────────────────────

@node
def test_stored_history_keeps_what_was_typed_not_the_file_dump():
    out = run(f"C.stored({json.dumps(HIST)})")
    assert [m["role"] for m in out] == ["user", "assistant", "user", "user"]
    assert out[0]["content"] == "make it blue"
    assert out[1]["content"] == HIST[1]["content"], "an assistant turn is kept as it is"
    assert out[2]["content"] == "a turn loaded from vibe.db"
    assert out[3]["content"] == HIST[3]["content"], "no typed text recorded: keep what there is rather than lose the turn"
    assert "[PROJECT FILES]" not in json.dumps(out[0]) and run("C.stored(null)") == []


def test_the_page_saves_through_stored_so_vibe_db_gets_the_typed_text():
    save = PAGE[PAGE.index("function doSaveSes() {"):PAGE.index("// Auto-save on navigation away")]
    assert "messages: window.VibeChat ? VibeChat.stored(hist) : hist" in save
    assert "plain: t" in PAGE, "the typed text is recorded on every user turn"


# ── the request on each route ───────────────────────────────────────────

@node
def test_the_own_route_request_has_no_system_message_no_web_flag_and_is_local_by_default():
    req = run(f"C.request('own', {OPTS})")
    assert req["url"] == "/api/vibe/chat"
    assert req["body"] == {"messages": [{"role": "user", "content": "hi"}], "stream": True, "project": True,
                           "workdir": "/w", "model": "local"}


@node
def test_cloud_is_asked_for_only_by_a_true_cloud_flag():
    cloud = run("C.request('own', {messages: [{role: 'user', content: 'hi'}], cloud: true})")["body"]
    assert cloud["model"] == "cloud" and cloud["confirm_cloud"] is True
    for flag in ("false", "'yes'", "1", "undefined", "null"):
        body = run(f"C.request('own', {{messages: [{{role: 'user', content: 'hi'}}], cloud: {flag}}})")["body"]
        assert body["model"] == "local" and "confirm_cloud" not in body, flag


@node
def test_the_legacy_request_keeps_the_old_route_with_the_stop_gap_flags():
    req = run(f"C.request('chat', {OPTS})")
    assert req["url"] == "/api/chat"
    assert req["body"] == {"messages": [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}],
                           "thinking": False, "stream": True, "workdir": "/w", "web": False, "tools": False, "temporary": True}
    again = run("C.request('chat', {messages: [{role: 'system', content: 'X'}, {role: 'user', content: 'hi'}], system: 'SYS', cloud: true})")
    assert [m["role"] for m in again["body"]["messages"]] == ["system", "user"] and "confirm_cloud" not in again["body"]


# ── which route ─────────────────────────────────────────────────────────

@node
def test_the_status_decides_the_route():
    got = run("[C.routeFromStatus(200, {route: 'own'}), C.routeFromStatus(200, {route: 'chat'}), C.routeFromStatus(404, null), "
              "C.routeFromStatus(500, {}), C.routeFromStatus(200, null), C.routeFromStatus(200, {route: 'x'})]")
    assert got == ["own", "chat", "chat", "own", "own", "own"]


@node
def test_a_message_goes_to_the_new_route_when_the_status_says_own():
    out = run(f"C.send({OPTS}).then(r => [r.status, calls.map(c => c.url + ' ' + c.method)])", fake_fetch([200, {"route": "own"}], [200]))
    assert out == [200, ["/api/vibe/chat/status GET", "/api/vibe/chat POST"]]


@node
def test_fallback_when_the_switch_says_chat_the_new_route_is_never_called():
    out = run(f"C.send({OPTS}).then(r => calls.map(c => c.url))", fake_fetch([200, {"route": "chat"}], [200]))
    assert out == ["/api/vibe/chat/status", "/api/chat"]


@node
def test_fallback_to_chat_when_the_new_route_answers_404_and_the_page_remembers():
    setup = fake_fetch([200, {"route": "own"}], [404, 200, 200])
    out = run(f"C.send({OPTS}).then(r => [r.status]).then(first => C.send({OPTS}).then(r => [first[0], r.status, calls.map(c => c.url)]))", setup)
    assert out == [200, 200, ["/api/vibe/chat/status", "/api/vibe/chat", "/api/chat", "/api/chat"]]


@node
def test_an_older_server_without_the_status_route_uses_the_old_route_and_an_unreachable_status_the_new_one():
    old = run(f"C.send({OPTS}).then(r => calls.map(c => c.url))", fake_fetch([404, {}], [200]))
    assert old == ["/api/vibe/chat/status", "/api/chat"]
    down = run(f"C.send({OPTS}).then(r => calls.map(c => c.url))", fake_fetch("error", [200]))
    assert down == ["/api/vibe/chat/status", "/api/vibe/chat"]


@node
def test_a_refusal_from_the_new_route_is_returned_not_retried_on_the_old_one():
    out = run(f"C.send({OPTS}).then(r => [r.status, calls.map(c => c.url)])", fake_fetch([200, {"route": "own"}], [409, 200]))
    assert out == [409, ["/api/vibe/chat/status", "/api/vibe/chat"]], "a cap or a provider refusal must not fall back to a route that follows chat's model"


def test_the_request_built_by_the_page_script_is_accepted_by_the_route(env):
    body = json.loads(subprocess.run(
        ["node", "-e", f"const C = require({json.dumps(str(JS))}); console.log(JSON.stringify(C.request('own', {OPTS}).body))"],
        capture_output=True, text=True, timeout=30, check=True).stdout)
    r = env.client.post("/api/vibe/chat", json=body)
    assert r.status_code == 200 and tokens(r) == "Sure. Here it is." and frames(r)[-1] == "[DONE]"
    assert env.model.calls[0]["messages"][0]["content"] == vc.build_system_prompt(True, "/w")


# ── the page ────────────────────────────────────────────────────────────

def test_the_script_is_stamped_and_loaded_after_the_agent_script_and_before_the_page_script():
    agent = re.search(r'<script src="/static/vibe-agent\.js\?v=[0-9a-f]{8}"></script>', PAGE)
    chat = re.search(r'<script src="/static/vibe-chat\.js\?v=[0-9a-f]{8}"></script>', PAGE)
    assert agent and chat and agent.start() < chat.start() < PAGE.index("function doSend(")
    # loaded by path: on this Mac another test can leave the live checkout's `tools` package in sys.modules
    spec = importlib.util.spec_from_file_location("_p042_stamp_static", REPO / "tools" / "stamp_static.py")
    stamp_static = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stamp_static)
    assert "vibe-chat.js" in stamp_static.STAMPED
    assert stamp_static.main(True) == 0


def test_the_page_sends_chat_through_vibechat_and_shows_the_servers_refusal():
    send = PAGE[PAGE.index("function doSend() {"):PAGE.index("// Extract the current (possibly still-streaming) code fence")]
    assert "VibeChat.send({messages: _histForSend(pm), system: pm ? SYSP_PROJECT : SYSP, project: pm, workdir: _workdir, cloud: cloud}, _vibeAbort.signal)" in send
    assert "fetch('/api/chat'" not in send and "web: false" not in send, "the request is built in static/vibe-chat.js"
    refusal = send[send.index("if (!r.ok) {"):send.index("if (!r.body)")]
    assert "ej.error" in refusal and "throw new Error" in refusal, "a cap or provider refusal reaches the person, not 'No response'"
    assert "if (j.token) {" in send, "the stream is still read the way it was: token frames"


def test_the_cloud_yes_comes_from_the_dialog_and_covers_one_message_only():
    fn = PAGE[PAGE.index("var _vibeCloud = false"):PAGE.index("function doSend() {")]
    assert "VibeAgent.chatCloud()" in fn and "VibeAgent.confirmChatCloud().then(function (ok)" in fn
    assert "if (ok) { _vibeCloud = true; doSend(); }" in fn and fn.count("_vibeCloud = true") == 1
    body = PAGE[PAGE.index("function doSend() {"):PAGE.index("var t = inp.value.trim();")]
    assert "var cloud = _vibeCloud; _vibeCloud = false;" in body, "the yes is read and cleared before anything else"
    assert PAGE.count("_vibeCloud = true") == 1, "nothing else turns the cloud on"
    assert "addEventListener('click', sendVibe)" in PAGE and "e.preventDefault(); sendVibe();" in PAGE
    assert "doSend)" not in PAGE and "; doSend();" not in PAGE.replace("_vibeCloud = true; doSend();", ""), "no path skips the dialog"


def test_the_chat_dialog_names_the_provider_fails_closed_and_the_model_list_shows_in_chat_mode():
    dialog = AGENT[AGENT.index("function confirmChatCloud() {"):AGENT.index("function send(inp) {")]
    assert "if (!(root.CodecShell && root.CodecShell.ask)) return Promise.resolve(false);" in dialog
    assert "Send this message to " in dialog and "c.host" in dialog and "monthly cap" in dialog
    assert "if (sel) sel.hidden = !(S.available && S.models && S.models.cloud);" in AGENT
    assert "chatCloud: chatCloud, confirmChatCloud: confirmChatCloud" in AGENT


def test_the_script_builds_no_html():
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert banned not in SRC, banned
    assert not re.search(r"fetch\(\s*['\"]https?://", SRC)


@node
def test_the_page_prompts_are_the_servers_prompts_word_for_word():
    code = PAGE[PAGE.index("var SYSP ="):PAGE.index("// What goes to the model")]
    out = subprocess.run(["node", "-e", f"{code}\nconsole.log(JSON.stringify([SYSP, SYSP_PROJECT]));"],
                         capture_output=True, text=True, timeout=30, check=True).stdout
    assert json.loads(out) == [vc.build_system_prompt(False, ""), vc.build_system_prompt(True, "")]
