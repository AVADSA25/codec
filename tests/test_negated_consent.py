"""P-078: strict consent refuses negated answers ("don't send it", "do not confirm")."""
import importlib.util
import json
import threading
import time
from pathlib import Path

import pytest

import codec_ask_user

_REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("verb,answer", [
    ("send", "don't send it"),
    ("send", "do not send"),
    ("send", "never send this"),
    ("send", "Don’t send it"),
    ("send", "no, send it"),
    ("delete", "no, cancel the delete"),
    ("delete", "don't delete"),
    ("confirm", "do not confirm"),
    ("confirm", "don't confirm"),
    ("send", "yes"),
])
def test_negated_answer_refused(verb, answer):
    ok, _ = codec_ask_user._is_consenting_answer(answer, destructive_verb=verb, options=None)
    assert ok is False


@pytest.mark.parametrize("verb,answer", [
    ("send", "send"),
    ("send", "Yes, send it"),
    ("send", "please send"),
    ("send", "yes, send it, do not wait"),
    ("delete", "yes go ahead and delete it"),
    ("confirm", "I confirm"),
])
def test_plain_consent_accepted(verb, answer):
    ok, _ = codec_ask_user._is_consenting_answer(answer, destructive_verb=verb, options=None)
    assert ok is True


def test_exact_option_label_accepted():
    ok, _ = codec_ask_user._is_consenting_answer(
        "Delete it", destructive_verb="delete", options=["Delete it", "Cancel"])
    assert ok is True


@pytest.fixture
def askuser_paths(tmp_path, monkeypatch):
    import codec_audit
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    monkeypatch.setattr(codec_ask_user, "PENDING_QUESTIONS_PATH", tmp_path / "pending_questions.json")
    monkeypatch.setattr(codec_ask_user, "NOTIFICATIONS_PATH", tmp_path / "notifications.json")
    monkeypatch.setattr(codec_ask_user, "CONFIG_PATH", tmp_path / "config.json")
    codec_ask_user._WAITERS.clear()
    codec_ask_user._REJECTION_COUNT.clear()
    return tmp_path


def _await_qid(pq, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pq.exists():
            pending = json.loads(pq.read_text()).get("pending_questions", [])
            if pending and pending[-1].get("status") == "pending":
                return pending[-1]["id"]
        time.sleep(0.05)
    raise AssertionError("no pending question appeared in time")


def test_two_negated_answers_end_as_ambiguous_consent(askuser_paths):
    holder = {}

    def caller():
        holder["v"] = codec_ask_user.ask("Send the email?", timeout=300,
                                         destructive=True, destructive_verb="send")

    t = threading.Thread(target=caller, daemon=True)
    t.start()
    qid = _await_qid(askuser_paths / "pending_questions.json")
    r1 = codec_ask_user.submit_answer(qid, "don't send it")
    r2 = codec_ask_user.submit_answer(qid, "do not send")
    t.join(timeout=4)
    assert r1.get("rejected") is True
    assert r2.get("ok") is False
    assert holder["v"] == codec_ask_user.TIMEOUT_SENTINEL
    rec = json.loads((askuser_paths / "pending_questions.json").read_text())
    assert rec["pending_questions"][-1]["status"] != "answered"


def _gmail():
    spec = importlib.util.spec_from_file_location("_t_gmail", _REPO / "skills" / "google_gmail.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("answer", ["don't send", "do not send it", "never send this"])
def test_gmail_does_not_send_on_negated_answer(monkeypatch, answer):
    gmail = _gmail()
    monkeypatch.setattr(codec_ask_user, "ask", lambda *a, **k: answer)

    def _no_service(*a, **k):
        raise AssertionError("Gmail service must not be touched")

    monkeypatch.setattr(gmail, "_get_service", _no_service)
    out = gmail._send_email("send email to: someone@example.com subject: Hi body: Body text")
    assert "not sent" in out.lower()
