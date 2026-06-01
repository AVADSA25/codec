"""CODEC Email Triage tests — read-only inbox digest.

The Gmail service and the local LLM are mocked, so the suite runs offline and
touches no real inbox. Triage is read-only by design — these tests also assert
it never calls a mutating Gmail method.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import codec_email_triage as et  # noqa: E402


# ── fake Gmail service ───────────────────────────────────────────────────────
class _Exec:
    def __init__(self, v):
        self._v = v

    def execute(self):
        return self._v


class _Msgs:
    def __init__(self, ids, recorder=None):
        self._ids = ids
        self._rec = recorder

    def list(self, **k):
        if self._rec is not None:
            self._rec.append(("list", k))
        return _Exec({"messages": [{"id": i} for i in self._ids]})

    def get(self, **k):
        if self._rec is not None:
            self._rec.append(("get", k))
        i = k["id"]
        return _Exec({
            "payload": {"headers": [
                {"name": "From", "value": f'Person {i} <p{i}@ex.com>'},
                {"name": "Subject", "value": f"Subject {i}"},
                {"name": "Date", "value": "Mon, 1 Jun 2026 09:00:00 +0000"},
            ]},
            "snippet": "x" * 500,
            "labelIds": (["UNREAD"] if i == "1" else []),
        })


class FakeSvc:
    def __init__(self, ids=("1", "2", "3"), recorder=None):
        self._ids = list(ids)
        self._rec = recorder

    def users(self):
        outer = self

        class _U:
            def messages(self):
                return _Msgs(outer._ids, outer._rec)
        return _U()


# ── fetch_recent ─────────────────────────────────────────────────────────────
class TestFetch:
    def test_parses_and_cleans(self):
        msgs = et.fetch_recent(service=FakeSvc(("1", "2")))
        assert len(msgs) == 2
        assert msgs[0]["sender"] == "Person 1"   # <addr> stripped
        assert msgs[0]["subject"] == "Subject 1"
        assert msgs[0]["unread"] is True and msgs[1]["unread"] is False
        assert len(msgs[0]["snippet"]) == 200    # truncated

    def test_uses_only_readonly_calls(self):
        rec = []
        et.fetch_recent(service=FakeSvc(("1",), recorder=rec))
        methods = {m for m, _ in rec}
        assert methods <= {"list", "get"}, f"triage must be read-only, used: {methods}"


# ── classification parsing ───────────────────────────────────────────────────
class TestParse:
    def test_plain_json(self):
        raw = json.dumps([{"idx": 0, "category": "lead", "priority": "high", "reason": "r"}])
        out = et._parse_classification(raw, 1)
        assert out[0]["category"] == "lead" and out[0]["priority"] == "high"

    def test_code_fence_stripped(self):
        raw = "```json\n[{\"idx\":0,\"category\":\"support\",\"priority\":\"low\",\"reason\":\"r\"}]\n```"
        assert et._parse_classification(raw, 1)[0]["category"] == "support"

    def test_prose_wrapped_array(self):
        raw = 'Here you go:\n[{"idx":0,"category":"noise","priority":"low","reason":"ad"}]\nThanks!'
        assert et._parse_classification(raw, 1)[0]["category"] == "noise"

    def test_unknown_enums_coerced(self):
        raw = json.dumps([{"idx": 0, "category": "BOGUS", "priority": "urgent", "reason": "r"}])
        out = et._parse_classification(raw, 1)
        assert out[0]["category"] == "noise" and out[0]["priority"] == "medium"

    def test_out_of_range_idx_skipped(self):
        raw = json.dumps([{"idx": 9, "category": "lead", "priority": "high"}])
        assert et._parse_classification(raw, 1) == {}

    def test_garbage_returns_empty(self):
        assert et._parse_classification("not json at all", 3) == {}
        assert et._parse_classification("", 3) == {}


# ── classify ─────────────────────────────────────────────────────────────────
class TestClassify:
    def test_single_llm_call_enriches_all(self, monkeypatch):
        calls = {"n": 0}

        def fake_call(msgs, **k):
            calls["n"] += 1
            return json.dumps([
                {"idx": 0, "category": "lead", "priority": "high", "reason": "a"},
                {"idx": 1, "category": "noise", "priority": "low", "reason": "b"},
            ])
        import codec_llm
        monkeypatch.setattr(codec_llm, "call", fake_call)
        msgs = [{"sender": "A", "subject": "s1", "snippet": "x"},
                {"sender": "B", "subject": "s2", "snippet": "y"}]
        out = et.classify(msgs)
        assert calls["n"] == 1, "must classify the whole batch in ONE LLM call"
        assert out[0]["category"] == "lead" and out[1]["category"] == "noise"

    def test_llm_failure_falls_back_unclassified(self, monkeypatch):
        import codec_llm
        monkeypatch.setattr(codec_llm, "call",
                            lambda m, **k: (_ for _ in ()).throw(RuntimeError("down")))
        out = et.classify([{"sender": "A", "subject": "s", "snippet": "x"}])
        assert out[0]["category"] == "unclassified" and out[0]["priority"] == "medium"


# ── triage end-to-end (ranking) ──────────────────────────────────────────────
class TestTriage:
    def test_ranks_priority_then_category(self, monkeypatch):
        import codec_llm
        monkeypatch.setattr(codec_llm, "call", lambda m, **k: json.dumps([
            {"idx": 0, "category": "noise", "priority": "low", "reason": "n"},
            {"idx": 1, "category": "lead", "priority": "high", "reason": "l"},
            {"idx": 2, "category": "support", "priority": "medium", "reason": "s"},
        ]))
        r = et.triage(service=FakeSvc(("1", "2", "3")))
        order = [(it["priority"], it["category"]) for it in r["items"]]
        assert order == [("high", "lead"), ("medium", "support"), ("low", "noise")]
        assert r["by_priority"] == {"high": 1, "medium": 1, "low": 1}

    def test_empty_inbox(self):
        r = et.triage(service=FakeSvc(()))
        assert r["count"] == 0 and r["items"] == []


# ── skill ────────────────────────────────────────────────────────────────────
class TestSkill:
    def test_discovered_and_exposed(self):
        import codec_dispatch
        codec_dispatch.load_skills()
        reg = codec_dispatch.registry
        assert "email_triage" in reg.names()
        assert reg.get_mcp_expose("email_triage") is True

    def test_formats_digest(self, monkeypatch):
        import skills.email_triage as st
        monkeypatch.setattr(st, "triage", lambda max_messages=25, query="is:inbox": {
            "count": 2, "query": query, "by_priority": {"high": 1, "low": 1},
            "by_category": {}, "items": [
                {"sender": "Acme", "subject": "Quote?", "category": "lead",
                 "priority": "high", "reason": "new deal", "unread": True},
                {"sender": "News", "subject": "Weekly", "category": "noise",
                 "priority": "low", "reason": "newsletter", "unread": False},
            ]})
        out = st.run("triage my inbox")
        assert "Acme" in out and "lead" in out and "HIGH" in out and "new deal" in out

    def test_unread_query(self, monkeypatch):
        import skills.email_triage as st
        seen = {}
        monkeypatch.setattr(st, "triage",
                            lambda max_messages=25, query="is:inbox":
                            seen.update(query=query) or {"count": 0, "items": []})
        st.run("triage my unread email")
        assert seen["query"] == "is:unread is:inbox"

    def test_auth_error_friendly(self, monkeypatch):
        import skills.email_triage as st
        monkeypatch.setattr(st, "triage",
                            lambda **k: (_ for _ in ()).throw(RuntimeError("invalid credentials")))
        out = st.run("triage my inbox")
        assert "connect google" in out.lower()

    def test_no_messages(self, monkeypatch):
        import skills.email_triage as st
        monkeypatch.setattr(st, "triage", lambda **k: {"count": 0, "items": []})
        assert "No" in st.run("triage inbox")
