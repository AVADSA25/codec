"""Chat: the web_search skill runs only with the "Search the web" switch (R-01,
docs/WEB-SEARCH-SWITCH-DESIGN.md).

With the switch off (no force_search) or web:false, no chat path runs web_search:
the pre-LLM trigger match leaves it out, an '@' pick and a [SKILL:web_search:...]
tag answer WEB_SEARCH_OFF. With the switch on it gets at most
WEB_SEARCH_MAX_CHARS characters on one line.

No request leaves the test: codec_dispatch's matcher and runner, the chat's own
search, the model and every ~/.codec read are fakes or in tmp_path.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.chat as chat

OFF = chat.WEB_SEARCH_OFF
CAP = chat.WEB_SEARCH_MAX_CHARS
LONG = ("who is the person who answered my letter about the flat on Example Street, "
        "and what did they say about the deposit\n") * 8   # several lines, over the cap, under 2,000


@pytest.fixture
def dispatch(monkeypatch):
    """The matcher answers st.matches; the runner records (skills, task) and answers st.results."""
    import codec_consent
    import codec_dispatch
    import routes.palette as palette
    st = SimpleNamespace(matches=[], results={}, runs=[])

    def check_skill(text):
        return {"name": st.matches[0], "_all_matches": list(st.matches)} if st.matches else None

    def run_skill(skill, task, app="", **kw):
        names = list(skill.get("_all_matches") or [skill["name"]])
        st.runs.append((names, task))
        for n in names:
            if st.results.get(n) is not None:
                return st.results[n]
        return None

    monkeypatch.setattr(codec_dispatch, "check_skill", check_skill)
    monkeypatch.setattr(codec_dispatch, "run_skill", run_skill)
    monkeypatch.setattr(codec_consent, "chat_consent_ok", lambda name, task: True)
    monkeypatch.setattr(palette, "pickable_skills", lambda: {"web_search", "time"})
    return st


def _web_runs(st):
    return [task for names, task in st.runs if "web_search" in names]


# ── The query cap ───────────────────────────────────────────────────────────────

def test_the_query_is_one_line_of_at_most_300_characters_cut_at_a_word():
    assert CAP == 300
    assert chat._web_search_task("  who is\n the   mayor \n") == "who is the mayor"
    q = chat._web_search_task(LONG)
    one = " ".join(LONG.split())
    assert len(one) > CAP and 0 < len(q) <= CAP and "\n" not in q
    assert one.startswith(q) and one[len(q)] == " ", "cut back to a whole word"
    assert chat._web_search_task("x" * 400) == "x" * CAP, "one long word is cut at the cap"


# ── Pre-LLM trigger match (_try_skill) ──────────────────────────────────────────

def test_a_trigger_phrase_does_not_search_with_the_switch_off(dispatch):
    dispatch.matches, dispatch.results = ["web_search"], {"web_search": "RESULT"}
    assert chat._try_skill("who is the mayor of Example town", search=False) == (None, None)
    assert dispatch.runs == []
    assert chat._try_skill("who is the mayor of Example town", search=True) == ("web_search", "RESULT")
    assert _web_runs(dispatch) == ["who is the mayor of Example town"], "control: on, it runs"


def test_switch_off_the_next_match_runs_as_if_web_search_had_not_matched(dispatch):
    dispatch.matches, dispatch.results = ["web_search", "time"], {"web_search": "RESULT", "time": "12:00"}
    assert chat._try_skill("who is on at 12, what time", search=False) == ("time", "12:00")
    assert dispatch.runs == [(["time"], "who is on at 12, what time")]


def test_switch_on_only_web_search_gets_the_capped_query(dispatch):
    dispatch.matches = ["time", "web_search", "weather"]
    dispatch.results = {"weather": "sunny"}
    assert chat._try_skill(LONG, search=True) == ("time", "sunny")
    assert dispatch.runs == [(["time"], LONG), (["web_search"], chat._web_search_task(LONG)),
                             (["weather"], LONG)], "order kept; the others get the message"


def test_a_caller_with_no_switch_still_searches_but_with_the_capped_query(dispatch):
    # Home's Flash box (/api/command) calls _try_skill(task): no switch, default search=True.
    dispatch.matches, dispatch.results = ["web_search"], {"web_search": "RESULT"}
    assert chat._try_skill(LONG) == ("web_search", "RESULT")
    assert _web_runs(dispatch) == [chat._web_search_task(LONG)]


def test_other_skills_run_as_before_with_the_switch_off(dispatch):
    dispatch.matches, dispatch.results = ["time"], {"time": "12:00"}
    skill = {"name": "time", "_all_matches": ["time"]}
    assert chat._without_web_search(skill) is skill
    assert chat._try_skill("what time is it", search=False) == ("time", "12:00")


# ── '@' pick and the [SKILL:] tag helper ────────────────────────────────────────

def test_an_at_pick_of_web_search_is_refused_with_the_switch_off(dispatch):
    dispatch.results = {"web_search": "RESULT"}
    assert chat._try_explicit_skill("web_search", "who is the mayor", search=False) == ("web_search", OFF)
    assert dispatch.runs == []
    assert chat._try_explicit_skill("web_search", LONG, search=True) == ("web_search", "RESULT")
    assert _web_runs(dispatch) == [chat._web_search_task(LONG)]


def test_a_tag_resolves_to_the_off_line_and_runs_nothing(dispatch):
    dispatch.results = {"web_search": "RESULT", "time": "12:00"}
    assert chat._try_skill_by_name("web_search", "mayor of Example town", search=False) == ("web_search", OFF)
    assert dispatch.runs == []
    assert chat._try_skill_by_name("time", "now", search=False) == ("time", "12:00"), "only web_search"
    assert chat._try_skill_by_name("web_search", LONG, search=True) == ("web_search", "RESULT")
    assert _web_runs(dispatch) == [chat._web_search_task(LONG)]


def test_the_off_line_backs_no_claim():
    import codec_claim_check
    assert not codec_claim_check.result_backs_claim(OFF)


# ── The route, stream and stream:false ──────────────────────────────────────────

@pytest.fixture
def route(tmp_path, monkeypatch, dispatch):
    import codec_config
    import codec_llm
    import codec_models
    import codec_search
    import codec_usage

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"llm_base_url": "http://localhost:8083/v1", "llm_model": "test-model"}))
    st = SimpleNamespace(reply="", searched=[], d=dispatch)
    monkeypatch.setattr(chat, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(codec_models, "ensure_llm_available", lambda *a, **k: None)
    monkeypatch.setattr(codec_models, "model_extras", lambda *a, **k: {})
    monkeypatch.setattr(codec_config, "get_llm_api_key", lambda: "")
    monkeypatch.setattr(codec_usage, "record_reply", lambda *a, **k: None)
    monkeypatch.setattr(chat, "_recall_memory", lambda messages, meta=None, temporary=False: [])
    monkeypatch.setattr(chat, "_build_chat_system_prompt", lambda *a, **k: "SYSTEM")
    monkeypatch.setattr(chat, "_maybe_escalate_suggestion", lambda *a, **k: None)
    monkeypatch.setattr(chat, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(codec_search, "search", lambda q, max_results=5: st.searched.append(q) or [])
    monkeypatch.setattr(codec_llm, "stream", lambda messages, **kw: iter([st.reply[i:i + 5]
                                                                          for i in range(0, len(st.reply), 5)]))
    monkeypatch.setattr(codec_llm, "call", lambda messages, **kw: st.reply)
    app = FastAPI()
    app.include_router(chat.router)
    client = TestClient(app)

    def post(text, stream, **body):
        dispatch.runs.clear()
        r = client.post("/api/chat", json={"messages": [{"role": "user", "content": text}],
                                           "stream": stream, "thinking": False, **body})
        assert r.status_code == 200, r.text
        if not stream:
            return r.json()["response"]
        frames = [json.loads(ln[6:]) for ln in r.text.splitlines()
                  if ln.startswith("data: ") and ln != "data: [DONE]"]
        assert not any("error" in f for f in frames), frames
        return "".join(f.get("token", "") for f in frames)

    st.post = post
    return st


@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize("body", [{}, {"force_search": False}, {"force_search": True, "web": False}])
def test_route_switch_off_no_path_searches(route, stream, body):
    route.d.results = {"web_search": "RESULT"}
    route.d.matches = ["web_search"]
    route.reply = "Let me check. [SKILL:web_search:mayor of Example town] That is all."
    text = route.post("who is the mayor of Example town", stream, **body)
    assert f"**{OFF}**" in text and "[SKILL:" not in text and "RESULT" not in text
    assert _web_runs(route.d) == [] and route.searched == []


@pytest.mark.parametrize("stream", [True, False])
def test_route_switch_on_the_trigger_match_runs_with_the_capped_query(route, stream):
    route.d.results = {"web_search": "RESULT"}
    route.d.matches = ["web_search"]
    text = route.post(LONG, stream, force_search=True)
    assert text.startswith("**web_search**: RESULT")
    assert _web_runs(route.d) == [chat._web_search_task(LONG)]


@pytest.mark.parametrize("stream", [True, False])
def test_route_switch_on_a_tag_runs_with_the_capped_query(route, stream):
    route.d.results = {"web_search": "RESULT"}
    route.d.matches = []
    route.reply = f"Here: [SKILL:web_search:{'mayor of Example town ' * 20}] done."
    text = route.post("tell me about the town", stream, force_search=True)
    assert "**RESULT**" in text and OFF not in text
    assert _web_runs(route.d) == [chat._web_search_task("mayor of Example town " * 20)]


@pytest.mark.parametrize("stream", [True, False])
def test_route_an_at_pick_follows_the_switch(route, stream):
    route.d.results = {"web_search": "RESULT"}
    assert route.post("mayor of Example town", stream, skill="web_search") == f"**web_search**: {OFF}"
    assert _web_runs(route.d) == []
    assert route.post("mayor of Example town", stream, skill="web_search",
                      force_search=True) == "**web_search**: RESULT"
    assert _web_runs(route.d) == ["mayor of Example town"]
