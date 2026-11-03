"""Chat reply parity (P-099, docs/CHAT-REPLY-PARITY-DESIGN.md).

/api/chat answers as an SSE stream (stream:true, what the Chat page sends) or as one
JSON reply (stream:false). The checks after the reply live in one helper,
routes.chat._post_reply_checks, so both give the same claim note, premise note, Project
suggestion and step-budget handling; stream:false also cuts a repetition loop the way
the stream does.

The model, the escalation gate, the skill run and every ~/.codec read are fakes or in
tmp_path: nothing here reaches a model server.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codec_chat_pipeline
import codec_claim_check
import codec_dashboard  # noqa: F401  (the chat's system prompt is imported from it per request)
import codec_premise_check
import routes.chat as chat

SOURCE = ("A reader wrote back last week. The tip I liked best came from someone who closes every "
          "working day by writing the three open questions on a card and pinning it above the desk.")
ASK = ("You said you close every working day by writing the three open questions on a card and "
       "pinning it above the desk. Help me plan and build a weekly review routine around that habit.")
TAG = "[SKILL:time:current time]"
REPLY = f"It is {TAG} now, and I have saved the summary to your Desktop."
SUGGESTION = {"estimated_checkpoints": 4, "reason": "several steps"}
LOOP = "Films to watch tonight:\n" + "- The Long Night Train\n" * 120
SESSION = "sess-p099"


@pytest.fixture
def env(tmp_path, monkeypatch):
    import codec_config
    import codec_daybreak
    import codec_llm
    import codec_models
    import codec_observer
    import codec_standing_rules
    import codec_usage
    import routes.prompts as prompts

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"llm_base_url": "http://localhost:8083/v1", "llm_model": "test-model"}))
    monkeypatch.setattr(chat, "CONFIG_PATH", str(cfg))
    monkeypatch.setenv("STEP_BUDGET_ENABLED", "true")
    monkeypatch.setenv("PREMISE_CHECK_ENABLED", "true")
    monkeypatch.setattr(codec_models, "ensure_llm_available", lambda *a, **k: None)
    monkeypatch.setattr(codec_models, "model_extras", lambda *a, **k: {})
    monkeypatch.setattr(codec_config, "get_llm_api_key", lambda: "")
    monkeypatch.setattr(codec_standing_rules, "prompt_block", lambda **k: "")
    monkeypatch.setattr(codec_daybreak, "get_working_context", lambda: "")
    monkeypatch.setattr(codec_observer, "maybe_inject_observation_summary", lambda **k: ("", "skip"))
    monkeypatch.setattr(codec_usage, "record_reply", lambda *a, **k: None)
    monkeypatch.setattr(prompts, "_load_prompt_overrides", lambda: {})
    monkeypatch.setattr(chat, "_identity_block", lambda meta, temporary: "")
    monkeypatch.setattr(chat, "_recall_memory", lambda messages, meta=None, temporary=False: [])
    monkeypatch.setattr(chat, "_try_skill", lambda text, temporary=False: (None, None))

    st = SimpleNamespace(reply=REPLY, limit=5, llm_calls=[], skills=[], gate=[], classifier=[],
                         audits=[], budget=[])

    def gate(text, session_id):
        st.gate.append((text, session_id))
        return {"escalate": True, **SUGGESTION}

    def skill(name, query, temporary=False):
        st.skills.append((name, query))
        return name, "12:00"

    def fake_stream(messages, **kw):
        for i in range(0, len(st.reply), 4):   # small deltas, like a local model's
            yield st.reply[i:i + 4]

    def fake_call(messages, **kw):
        st.llm_calls.append(messages)
        return st.reply

    class SpyBudget(codec_chat_pipeline._StepBudget):
        def consume(self, kind="step"):
            ok = super().consume(kind)
            st.budget.append((kind, ok))
            return ok

    monkeypatch.setattr(chat, "_should_escalate_to_project", gate)
    # Guard: the real classifier must never run (it would call the local model).
    monkeypatch.setattr(codec_chat_pipeline, "_qwen_chat_classify",
                        lambda *a, **k: st.classifier.append(a) or "")
    monkeypatch.setattr(chat, "_try_skill_by_name", skill)
    monkeypatch.setattr(chat, "log_event", lambda *a, **k: st.audits.append((a[0], k.get("extra"))))
    monkeypatch.setattr(codec_chat_pipeline, "_step_budget_for_route", lambda route: st.limit)
    monkeypatch.setattr(chat, "_StepBudget", SpyBudget)
    monkeypatch.setattr(codec_llm, "stream", fake_stream)
    monkeypatch.setattr(codec_llm, "call", fake_call)

    app = FastAPI()
    app.include_router(chat.router)
    client = TestClient(app)

    def post(stream: bool, messages=None):
        for name in ("llm_calls", "skills", "gate", "audits", "budget"):
            getattr(st, name).clear()
        messages = messages or [{"role": "user", "content": SOURCE},
                                {"role": "assistant", "content": "Noted."},
                                {"role": "user", "content": ASK}]
        r = client.post(f"/api/chat?s={SESSION}", json={"messages": messages, "stream": stream,
                                                        "thinking": False, "web": False})
        assert r.status_code == 200, r.text
        if stream:
            assert r.text.rstrip().endswith("data: [DONE]")
            frames = [json.loads(ln[6:]) for ln in r.text.splitlines()
                      if ln.startswith("data: ") and ln != "data: [DONE]"]
            assert not any("error" in f for f in frames), frames
            text = "".join(f["token"] for f in frames if "token" in f)
            suggestions = [f["escalate_project"] for f in frames if "escalate_project" in f]
        else:
            out = r.json()
            text = out["response"]
            suggestions = [out["escalate_project"]] if "escalate_project" in out else []
        return SimpleNamespace(text=text, suggestions=suggestions, llm_calls=len(st.llm_calls),
                               skills=list(st.skills), gate=list(st.gate), audits=list(st.audits),
                               budget=list(st.budget))

    yield SimpleNamespace(post=post, state=st)
    assert st.classifier == [], "the real escalation classifier ran"


def _without_transport(audits):
    return [(name, {k: v for k, v in (extra or {}).items() if k != "transport"})
            for name, extra in audits]


def test_one_reply_gets_the_same_checks_on_both_paths(env):
    sse, js = env.post(True), env.post(False)
    shown = REPLY.replace(TAG, "**12:00**")
    claim = codec_claim_check.correction_note(
        codec_claim_check.find_unbacked_claims(shown, actions_taken={"time"}, user_request=ASK))
    premise = codec_premise_check.premise_note(codec_premise_check.find_misattributions([SOURCE, ASK]))
    assert "Correction" in claim and "Worth checking" in premise
    assert sse.text == shown + claim + premise, "stream: tag resolved, then the claim and premise notes"
    assert js.text == sse.text, "stream:false gives the same reply text"
    assert sse.suggestions == js.suggestions == [SUGGESTION]
    assert chat._LOOP_NOTE not in js.text, "an ordinary reply is not cut"
    assert sse.gate == js.gate == [(ASK, SESSION)]
    assert sse.skills == js.skills == [("time", "current time")]
    assert sse.budget == js.budget and ("post_llm_skill_tag", True) in js.budget
    names = ["agent_auto_escalated_from_chat", "unbacked_claim_corrected", "premise_misattribution_flagged"]
    assert [a[0] for a in sse.audits] == [a[0] for a in js.audits] == names
    assert _without_transport(sse.audits) == _without_transport(js.audits)
    assert [e.get("transport") for _, e in sse.audits[1:]] == ["stream", "stream"]
    assert all("transport" not in e for _, e in js.audits), "the JSON audit lines are unchanged"
    assert (sse.llm_calls, js.llm_calls) == (0, 1), "one model call: the reply, no classifier"


def test_a_spent_step_budget_drops_the_tag_on_both_paths(env):
    env.state.limit = 1   # the model call uses the one step
    sse, js = env.post(True), env.post(False)
    for out in (sse, js):
        assert out.skills == [], "the skill must not run past the budget"
        assert TAG not in out.text and "**12:00**" not in out.text
        assert ("post_llm_skill_tag", False) in out.budget
        assert "Correction" in out.text, "nothing ran, so the Desktop claim stays unbacked"
    assert js.text == sse.text and js.budget == sse.budget


def test_a_reply_stuck_in_a_loop_is_cut_on_both_paths(env):
    env.state.reply = LOOP
    plain = [{"role": "user", "content": "films for tonight please"}]
    sse, js = env.post(True, plain), env.post(False, plain)
    cut = []
    for out in (sse, js):
        assert out.text.endswith(chat._LOOP_NOTE) and out.text.count(chat._LOOP_NOTE) == 1
        body = out.text[:-len(chat._LOOP_NOTE)]
        assert LOOP.startswith(body) and 900 <= len(body) < len(LOOP) // 2
        assert out.suggestions == [], "no action verb: no Project suggestion"
        cut.append(len(body))
    assert abs(cut[0] - cut[1]) <= 160, "both cut at about the same point"


def test_the_cut_finds_the_first_loop_and_leaves_ordinary_text():
    text = " ".join(f"Point {i} adds a different detail about the plan." for i in range(80))
    assert len(text) > 2400 and chat._cut_repetition(text) == (text, False)
    assert chat._cut_repetition("short reply") == ("short reply", False)
    assert chat._cut_repetition(LOOP) == (LOOP[:900], True)
    tail = LOOP + "A different ending."
    assert chat._cut_repetition(tail)[1], "a loop in the middle is still cut"
