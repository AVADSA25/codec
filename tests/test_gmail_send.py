"""Tests for the google_gmail `send` action (voice think-mode v1).

Contract (docs/VOICE-MODES-DESIGN.md §3.4):
- "send email …" routes to the send path; reading stays untouched.
- Sending ALWAYS goes through codec_ask_user strict consent
  (destructive_verb="send"); timeout / disabled / non-verb answers ⇒ NOT sent.
- Missing to/body ⇒ usage hint (agent-friendly), no consent prompt, no send.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import codec_ask_user  # noqa: E402
import google_gmail  # noqa: E402


class _FakeSend:
    def __init__(self, store):
        self.store = store

    def execute(self):
        return {"id": "m1"}


class _FakeMessages:
    def __init__(self, store):
        self.store = store

    def send(self, userId=None, body=None):
        self.store.append(body)
        return _FakeSend(self.store)


class _FakeUsers:
    def __init__(self, store):
        self._m = _FakeMessages(store)

    def messages(self):
        return self._m


class _FakeService:
    def __init__(self, store):
        self._u = _FakeUsers(store)

    def users(self):
        return self._u


def _wire(monkeypatch, answer):
    sent = []
    monkeypatch.setattr(google_gmail, "_get_service", lambda: _FakeService(sent))
    asked = {}

    def fake_ask(question, **kw):
        asked.update(kw, question=question)
        return answer

    monkeypatch.setattr(codec_ask_user, "ask", fake_ask)
    return sent, asked


def test_send_with_consent(monkeypatch):
    sent, asked = _wire(monkeypatch, "send")
    out = google_gmail.run(
        "send email to: bob@example.com subject: Dinner body: See you at 8.")
    assert len(sent) == 1, "exactly one send API call"
    assert asked["destructive"] is True
    assert asked["destructive_verb"] == "send"
    assert "sent" in out.lower()
    import base64
    raw = base64.urlsafe_b64decode(sent[0]["raw"]).decode()
    assert "bob@example.com" in raw
    assert "Dinner" in raw
    assert "See you at 8." in raw


def test_send_refused_without_verb(monkeypatch):
    sent, _ = _wire(monkeypatch, "no thanks")
    out = google_gmail.run(
        "send email to: bob@example.com subject: Hi body: Yo")
    assert sent == [], "must NOT send without the spoken verb"
    assert "not sent" in out.lower()


def test_send_timeout_sentinel_fails_closed(monkeypatch):
    sent, _ = _wire(monkeypatch, codec_ask_user.TIMEOUT_SENTINEL)
    out = google_gmail.run(
        "send email to: bob@example.com subject: Hi body: Yo")
    assert sent == []
    assert "not sent" in out.lower()


def test_send_askuser_disabled_fails_closed(monkeypatch):
    sent, _ = _wire(monkeypatch, codec_ask_user.DISABLED_SENTINEL)
    out = google_gmail.run(
        "send email to: bob@example.com subject: Hi body: Yo")
    assert sent == []
    assert "not sent" in out.lower()


def test_send_missing_fields_returns_usage(monkeypatch):
    sent, asked = _wire(monkeypatch, "send")
    out = google_gmail.run("send an email to my brother about dinner")
    assert sent == []
    assert "question" not in asked, "no consent prompt for unparseable send"
    assert "to:" in out and "body:" in out  # agent-friendly usage hint


def test_read_path_untouched(monkeypatch):
    # "check email" must NOT hit the send path or consent.
    called = {"send_path": False}
    monkeypatch.setattr(google_gmail, "_send_email",
                        lambda t: called.__setitem__("send_path", True) or "x")
    monkeypatch.setattr(google_gmail, "_get_service",
                        lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    out = google_gmail.run("check email")
    assert called["send_path"] is False
    assert "error" in out.lower()  # read path attempted the (offline) service
