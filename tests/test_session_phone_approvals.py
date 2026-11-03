"""Session command approvals reach the phone through codec_ask_user (P-060).

docs/SESSION-PHONE-APPROVALS-DESIGN.md. The Terminal session is its own process,
so its approval dialogs ask through codec_ask_user (a question file every process
reads) instead of the dashboard's in-memory approvals dict. Only the exact Allow
label approves; the Mac dialog decides beside the phone and the first answer wins.

No real window opens (tests/session_tk_fake.py stands in for tkinter), no push
leaves the Mac (codec_push is replaced), and every file is in tmp_path.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import session_tk_fake

REPO = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
SECRET = b"p060-test-secret-0123456789abcdef"


@pytest.fixture
def env(tmp_path, monkeypatch):
    import codec_ask_user
    import codec_audit
    import codec_keychain
    import codec_push
    import codec_session
    import routes._shared as sh
    monkeypatch.setenv("ASKUSER_ENABLED", "true")
    monkeypatch.setattr(sh, "NOTIFICATIONS_PATH", str(tmp_path / "dashboard_notifications.json"))
    monkeypatch.setattr(codec_ask_user, "PENDING_QUESTIONS_PATH", tmp_path / "pending_questions.json")
    monkeypatch.setattr(codec_ask_user, "NOTIFICATIONS_PATH", tmp_path / "notifications.json")
    monkeypatch.setattr(codec_ask_user, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(codec_ask_user, "_WAITERS", {})
    monkeypatch.setattr(codec_ask_user, "_REJECTION_COUNT", {})
    monkeypatch.setattr(codec_audit, "_AUDIT_DIR", tmp_path)
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    monkeypatch.setattr(codec_keychain, "get_audit_hmac_secret", lambda: SECRET)
    pushes = []
    monkeypatch.setattr(codec_push, "notify", pushes.append)
    monkeypatch.setattr(codec_session, "_APPROVAL_POLL_MS", 50)

    def use_tk(clicks=(), max_seconds=20):
        fake = session_tk_fake.module(clicks, max_seconds)
        monkeypatch.setitem(sys.modules, "tkinter", fake)
        return fake

    yield SimpleNamespace(tmp=tmp_path, pushes=pushes, use_tk=use_tk)
    for t in threading.enumerate():
        if t.name == "codec-session-approval":
            t.join(10)


def _session():
    import codec_session
    return codec_session.Session.__new__(codec_session.Session)


def _audits(path: Path, event: str) -> list:
    if not path.exists():
        return []
    recs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r for r in recs if r.get("event") == event]


def _questions() -> list:
    import codec_ask_user
    return codec_ask_user._load_pending_questions()["pending_questions"]


def _phone_answers(answer, delay=0.1):
    """Answer the first pending question as the dashboard's answer route would."""
    import codec_ask_user
    box = {}

    def run():
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            recs = [r for r in _questions() if r.get("status") == "pending"]
            if recs:
                time.sleep(delay)
                box["result"] = codec_ask_user.submit_answer(recs[0]["id"], answer, answered_via="pwa")
                return
            time.sleep(0.02)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return box, t


# ── what is asked ─────────────────────────────────────────────────────────────

def test_both_dialogs_ask_through_ask_user(env, monkeypatch):
    import codec_ask_user
    calls = []

    def fake_ask(question, **kw):
        calls.append((question, kw))
        return "Allow"

    monkeypatch.setattr(codec_ask_user, "ask", fake_ask)
    env.use_tk()
    s = _session()
    assert s._cmd_preview("bash", "mkdir ~/Desktop/p060-test") is True
    assert s._danger_preview("bash", "rm -rf ~/Desktop/p060-test") is True
    assert len(calls) == 2
    for (question, kw), command in zip(calls, ["mkdir ~/Desktop/p060-test", "rm -rf ~/Desktop/p060-test"]):
        assert kw == {"options": ["Allow", "Deny"], "timeout": 60, "destructive": True,
                      "destructive_verb": "allow", "asked_from": "session"}
        assert command in question and "This will:" in question
    assert "DANGEROUS" in calls[1][0] and "DANGEROUS" not in calls[0][0]
    decisions = _audits(env.tmp / "audit.log", "session_approval_decision")
    assert [d["extra"]["dangerous"] for d in decisions] == [False, True]
    for d in decisions:
        assert (d["extra"]["decision"], d["extra"]["decided_by"], d["extra"]["reason"]) == ("allowed", "phone", "allow")
        assert d["source"] == "codec-session" and "p060-test" not in json.dumps(d)


def test_the_session_no_longer_uses_the_dashboard_dict():
    import codec_session
    src = Path(codec_session.__file__).read_text(encoding="utf-8")
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    assert "codec_ask_user" in mods  # control: the walk sees the lazy imports
    assert not any(m == "routes" or m.startswith("routes.") for m in mods)
    assert "_pending_approvals" not in src
    for name in ("_register_remote_approval", "_check_remote_approval", "_cleanup_approval"):
        assert not hasattr(codec_session.Session, name)


# ── what approves ─────────────────────────────────────────────────────────────

def test_only_the_exact_allow_label_approves(env):
    import codec_ask_user
    import codec_session

    def state_of(answer):
        p = codec_session._PhoneApproval("q", dangerous=False, cmd_hash="h")
        p._answer = answer
        p._done.set()
        return p.state()

    for a in ("Allow", "allow", " Allow "):
        assert state_of(a) == "allow", a
    for a in ("Allow it", "allow it please", "yes allow", "Allow.", "do not allow", "Deny", "", None,
              codec_ask_user.TIMEOUT_SENTINEL):
        assert state_of(a) == "deny", a

    # A typed reply that ask()'s strict mode takes as consent still does not approve.
    env.use_tk()
    box, t = _phone_answers("allow it please")
    assert _session()._cmd_preview("bash", "mkdir ~/Desktop/p060-test") is False
    t.join(5)
    assert box["result"]["ok"] is True
    (d,) = _audits(env.tmp / "audit.log", "session_approval_decision")
    assert (d["extra"]["decision"], d["extra"]["decided_by"], d["extra"]["reason"]) == ("denied", "phone", "reply")
    assert env.pushes == ["approval"]


def test_off_error_and_timeout_never_approve(env, monkeypatch):
    import codec_ask_user
    import codec_session
    monkeypatch.setattr(codec_session, "APPROVAL_TIMEOUT_SEC", 1)
    monkeypatch.setattr(codec_session, "_APPROVAL_GRACE_SEC", 0.5)
    s = _session()

    # ask_user switched off: nothing is asked, and with no click the dialog denies.
    monkeypatch.setenv("ASKUSER_ENABLED", "false")
    env.use_tk()
    assert s._danger_preview("bash", "rm -rf ~/p060-test") is False
    assert not (env.tmp / "pending_questions.json").exists() and env.pushes == []

    # ask() raising.
    monkeypatch.setenv("ASKUSER_ENABLED", "true")
    real_ask = codec_ask_user.ask

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(codec_ask_user, "ask", boom)
    env.use_tk()
    assert s._cmd_preview("bash", "mkdir ~/p060-test") is False

    # Nobody answers: the question times out (first) and the command is denied.
    monkeypatch.setattr(codec_ask_user, "ask", real_ask)
    fake = env.use_tk()
    assert s._danger_preview("bash", "rm -rf ~/p060-test") is False
    assert not fake.roots[-1].safety_stop
    (rec,) = _questions()
    assert rec["status"] == "timed_out"

    got = [(d["extra"]["phone"], d["extra"]["decision"], d["extra"]["decided_by"], d["extra"]["reason"])
           for d in _audits(env.tmp / "audit.log", "session_approval_decision")]
    assert got == [("disabled", "denied", "none", "timeout"), ("error", "denied", "none", "timeout"),
                   ("asked", "denied", "none", "timeout")]


# ── the Mac dialog and the first answer ───────────────────────────────────────

@pytest.mark.parametrize("label,allowed,phone", [("Allow", True, "asked"), ("Deny", False, "asked"),
                                                 ("Allow", True, "disabled")])
def test_the_mac_dialog_decides_when_the_phone_does_not(env, monkeypatch, label, allowed, phone):
    import codec_ask_user
    import routes.inbox
    if phone == "disabled":
        monkeypatch.setenv("ASKUSER_ENABLED", "false")
    fake = env.use_tk(clicks=[(0.3, label)])
    assert _session()._danger_preview("bash", "rm -rf ~/p060-test") is allowed
    assert fake.roots[-1].clicked == [label]
    (d,) = _audits(env.tmp / "audit.log", "session_approval_decision")
    assert (d["extra"]["decided_by"], d["extra"]["reason"], d["extra"]["phone"]) == ("mac", label.lower(), phone)
    if phone == "disabled":
        assert _questions() == [] and env.pushes == []
        return
    (rec,) = _questions()
    assert (rec["status"], rec["answered_via"], rec["answer"]) == ("answered", "dialog", label)
    assert d["extra"]["pending_question_id"] == rec["id"]
    assert routes.inbox._questions() == []  # the card has left the Inbox
    assert codec_ask_user.submit_answer(rec["id"], "Allow", answered_via="pwa")["error"] == "already_answered"
    (ans,) = _audits(env.tmp / "audit.log", "ask_user_question_answer")
    assert ans["extra"]["answered_via"] == "dialog"
    assert env.pushes == ["approval"]


def test_a_phone_answer_closes_the_dialog(env):
    fake = env.use_tk(clicks=[(3.0, "Allow")])
    box, t = _phone_answers("Deny")
    assert _session()._danger_preview("bash", "rm -rf ~/p060-test") is False
    t.join(5)
    root = fake.roots[-1]
    assert root.clicked == [] and root.quit_called and root.destroyed
    (rec,) = _questions()
    assert (rec["answered_via"], rec["answer"]) == ("pwa", "Deny")
    (d,) = _audits(env.tmp / "audit.log", "session_approval_decision")
    assert (d["extra"]["decided_by"], d["extra"]["reason"]) == ("phone", "deny")


def test_the_record_holds_first_answer(env, monkeypatch):
    import codec_ask_user
    import codec_session
    real = codec_ask_user.submit_answer

    def phone_first(qid, answer, *, answered_via="pwa"):
        if answered_via == "dialog":  # the phone's Deny lands a moment before the click
            real(qid, "Deny", answered_via="pwa")
        return real(qid, answer, answered_via=answered_via)

    monkeypatch.setattr(codec_ask_user, "submit_answer", phone_first)
    monkeypatch.setattr(codec_session, "_APPROVAL_POLL_MS", 5000)  # the dialog has not seen it yet
    fake = env.use_tk(clicks=[(0.3, "Allow")])
    assert _session()._danger_preview("bash", "rm -rf ~/p060-test") is False
    assert fake.roots[-1].clicked == ["Allow"]
    (rec,) = _questions()
    assert (rec["answered_via"], rec["answer"]) == ("pwa", "Deny")
    (d,) = _audits(env.tmp / "audit.log", "session_approval_decision")
    assert (d["extra"]["decided_by"], d["extra"]["reason"]) == ("phone", "deny")


def test_simultaneous_answers_run_only_if_both_allow(env, monkeypatch):
    import codec_ask_user
    import codec_session

    def tie(phone_answer):
        p = codec_session._PhoneApproval("q", dangerous=True, cmd_hash="h")
        p.qid = "q_test"

        def both_written(qid, answer, *, answered_via="pwa"):
            p._answer = phone_answer  # ask() returned the phone's answer, and the Mac's write also landed
            p._done.set()
            return {"ok": True, "agent_unblocked": True}

        monkeypatch.setattr(codec_ask_user, "submit_answer", both_written)
        return p

    assert tie("Allow").finish("deny") is False
    assert tie("Deny").finish("allow") is False
    assert tie("Allow").finish("allow") is True  # the same answer: the Mac's own
    got = [d["extra"]["decided_by"] for d in _audits(env.tmp / "audit.log", "session_approval_decision")]
    assert got == ["both", "both", "mac"]


def test_without_tkinter_nothing_changes(env, monkeypatch):
    monkeypatch.setitem(sys.modules, "tkinter", None)
    s = _session()
    assert s._cmd_preview("bash", "mkdir ~/p060-test") is True
    assert s._danger_preview("bash", "rm -rf ~/p060-test") is False
    assert not (env.tmp / "pending_questions.json").exists() and env.pushes == []
    assert _audits(env.tmp / "audit.log", "session_approval_decision") == []


# ── two processes: the dashboard answers a waiting session ────────────────────

CHILD = r'''
import json, sys, threading, time
from pathlib import Path
cfg = json.loads(sys.argv[1])
sys.path.insert(0, cfg["tests"])
sys.path.insert(0, cfg["repo"])
tmp = Path(cfg["tmp"])


class NoDashboardState:
    """The session must not reach for the dashboard's in-memory state; refusing
    the import also keeps an old build from writing the owner's notifications."""
    attempts = 0

    def find_spec(self, name, path=None, target=None):
        if name == "routes" or name.startswith("routes."):
            NoDashboardState.attempts += 1
            raise ImportError("the session process must not import routes")
        return None


sys.meta_path.insert(0, NoDashboardState())

import codec_audit, codec_keychain
codec_audit._AUDIT_DIR = tmp
codec_audit._AUDIT_LOG = tmp / "child_audit.log"
codec_keychain.get_audit_hmac_secret = lambda: cfg["secret"].encode()

import codec_ask_user
codec_ask_user.PENDING_QUESTIONS_PATH = tmp / "pending_questions.json"
codec_ask_user.NOTIFICATIONS_PATH = tmp / "notifications.json"
codec_ask_user.CONFIG_PATH = tmp / "config.json"

import codec_push
from cryptography.hazmat.primitives.asymmetric import ec
codec_push.STATE_PATH = tmp / "push.json"
_key = ec.generate_private_key(ec.SECP256R1())
codec_push._private_key = lambda: _key
codec_push._subject = lambda: codec_push.DEFAULT_SUBJECT
sent, plain = [], []
_encrypt = codec_push.encrypt
def encrypt(plaintext, p256dh, auth, **kw):
    plain.append(plaintext.decode("utf-8"))
    return _encrypt(plaintext, p256dh, auth, **kw)
codec_push.encrypt = encrypt
def _post(endpoint, data, headers):
    from urllib.parse import urlsplit
    sent.append({"host": urlsplit(endpoint).hostname, "topic": headers.get("Topic"),
                 "urgency": headers.get("Urgency"), "bytes": len(data)})
    return 201
codec_push._post = _post

import codec_session
import session_tk_fake
codec_session.APPROVAL_TIMEOUT_SEC = cfg["timeout"]
fake = session_tk_fake.module(max_seconds=cfg["timeout"] + 20)
sys.modules["tkinter"] = fake

s = codec_session.Session.__new__(codec_session.Session)
t0 = time.monotonic()
allowed = s._danger_preview("bash", cfg["command"])
seconds = round(time.monotonic() - t0, 2)
log = tmp / "child_audit.log"
deadline = time.monotonic() + 10
while time.monotonic() < deadline and '"push_sent"' not in (log.read_text() if log.exists() else ""):
    time.sleep(0.05)
print(json.dumps({"allowed": allowed, "seconds": seconds, "push": sent, "plain": plain,
                  "safety_stop": fake.roots[-1].safety_stop,
                  "routes_imports": NoDashboardState.attempts}))
'''


def _push_device(tmp: Path) -> None:
    """One subscribed test device in the child's own push state (never ~/.codec/push.json)."""
    import codec_push
    from cryptography.hazmat.primitives.asymmetric import ec
    ua = ec.generate_private_key(ec.SECP256R1()).public_key()
    state = {"schema": 1, "types": {s: True for s in codec_push.SWITCHES}, "devices": [{
        "endpoint": "https://fcm.googleapis.com/fcm/send/p060-test-device",
        "p256dh": codec_push._b64u(codec_push._point(ua)), "auth": codec_push._b64u(os.urandom(16)),
        "label": "Test device", "created": "2026-11-03T12:00:00", "last_ok": None, "last_error": None}]}
    (tmp / "push.json").write_text(json.dumps(state))


def _inbox_question(client, child, timeout=20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child.poll() is not None:
            pytest.fail("the session exited before asking: " + child.stderr.read()[-2000:])
        r = client.get("/api/inbox")
        assert r.status_code == 200
        items = [i for i in r.json()["items"] if i["kind"] == "question"]
        if items:
            return items[0]
        time.sleep(0.05)
    pytest.fail("the session's question never reached the Inbox")


@pytest.mark.parametrize("answer,allowed,reason", [("Allow", True, "allow"), ("Deny", False, "deny"),
                                                   (None, False, "timeout")])
def test_two_processes_the_dashboard_answers_the_session(tmp_path, monkeypatch, answer, allowed, reason):
    import codec_ask_user
    import codec_audit
    import codec_config
    import codec_dashboard
    import codec_push
    import routes._shared as sh
    (tmp_path / "notifications.json").write_text("[]")
    _push_device(tmp_path)
    monkeypatch.setattr(codec_ask_user, "PENDING_QUESTIONS_PATH", tmp_path / "pending_questions.json")
    monkeypatch.setattr(codec_ask_user, "NOTIFICATIONS_PATH", tmp_path / "notifications.json")
    monkeypatch.setattr(codec_ask_user, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(sh, "NOTIFICATIONS_PATH", str(tmp_path / "notifications.json"))
    monkeypatch.setattr(codec_config, "get_dashboard_token", lambda: "")
    monkeypatch.setattr(codec_dashboard, "AUTH_ENABLED", False)
    monkeypatch.setattr(codec_push, "notify", lambda kind: None)
    client = TestClient(codec_dashboard.app, client=("127.0.0.1", 5000))

    command = "rm -rf ~/p060-test-folder"
    script = tmp_path / "child.py"
    script.write_text(CHILD)
    cfg = {"repo": str(REPO), "tests": str(TESTS), "tmp": str(tmp_path), "secret": SECRET.decode(),
           "timeout": 3 if answer is None else 30, "command": command}
    env = dict(os.environ, ASKUSER_ENABLED="true", PUSH_ENABLED="true", PYTHONDONTWRITEBYTECODE="1")
    child = subprocess.Popen([sys.executable, str(script), json.dumps(cfg)], cwd=str(tmp_path), env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        item = _inbox_question(client, child)
        assert item["group"] == "needs_you" and item["options"] == ["Allow", "Deny"]
        assert item["strict"] is True and item["verb"] == "allow" and command in item["body"]
        qid = item["question_id"]
        if answer is not None:
            r = client.post(f"/api/agents/answer/{qid}", json={"answer": answer, "answered_via": "pwa"})
            assert r.status_code == 200 and r.json()["ok"] is True
        out, err = child.communicate(timeout=60)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    assert child.returncode == 0, err[-2000:]
    result = json.loads(out.strip().splitlines()[-1])
    assert result["allowed"] is allowed
    assert result["safety_stop"] is False and result["routes_imports"] == 0
    assert not [i for i in client.get("/api/inbox").json()["items"] if i.get("question_id") == qid]

    # The content-free approval push was sent (to the replaced _post), never the command.
    assert result["push"] == [{"host": "fcm.googleapis.com", "topic": "codec-approval", "urgency": "high",
                               "bytes": result["push"][0]["bytes"]}]
    assert result["plain"] == [json.dumps(codec_push.payload("approval"), separators=(",", ":"))]
    assert "p060" not in result["plain"][0]

    log = tmp_path / "child_audit.log"
    (emit,) = _audits(log, "ask_user_question_emit")
    assert (emit["extra"]["asked_from"], emit["extra"]["consent_strict"], emit["extra"]["destructive_verb"]) \
        == ("session", True, "allow")
    (d,) = _audits(log, "session_approval_decision")
    assert d["extra"]["decision"] == ("allowed" if allowed else "denied")
    assert (d["extra"]["decided_by"], d["extra"]["reason"]) == ("none" if answer is None else "phone", reason)
    assert d["extra"]["cmd_hash"] == codec_audit._cmd_hash(command) and d["extra"]["pending_question_id"] == qid
    assert d["extra"]["correlation_id"] == emit["extra"]["correlation_id"]
    assert "p060-test-folder" not in json.dumps(d)
    if answer is None:
        (to,) = _audits(log, "ask_user_question_timeout")
        assert to["extra"]["reason"] == "deadline"
    else:
        (ans,) = _audits(log, "ask_user_question_answer")
        assert ans["extra"]["answered_via"] == "pwa"
    (push,) = _audits(log, "push_sent")
    assert (push["extra"]["kind"], push["extra"]["ok"]) == ("approval", 1)


def test_the_inbox_card_answers_with_its_button_label():
    """The phone's Allow is the button label, posted as is to the answer route."""
    shell = (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")
    assert '<button type="button" class="cs-dbtn cs-q-opt">\' + esc(o) + \'</button>' in shell
    assert "send(b.textContent)" in shell
    assert "postJSON('/api/agents/answer/' + encodeURIComponent(qid), { answer: answer, answered_via: 'pwa' })" in shell
