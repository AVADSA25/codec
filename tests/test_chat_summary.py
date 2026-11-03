"""Notes on the earlier part of a long Deep Chat (P-038, docs/CHAT-SUMMARY-DESIGN.md).

P-034 leaves the oldest turns out of a chat that is over config.json:chat_prompt_token_budget.
codec_chat_summary writes notes on those turns with the local model, after a reply, and
later turns send the notes right after the system message, then the newest turns that fit.
The chat is the made-up 60-turn fixture of the live eval (tools/chat_summary_fixture.json);
the model is a fake. The live recall and prefill bars are in tools/chat_summary_eval.py.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codec_chat_summary as notes
import codec_dashboard  # noqa: F401  (the chat's system prompt is imported from it per request)
import routes.chat as chat

REPO = Path(__file__).resolve().parent.parent
PAGE = (REPO / "codec_chat.html").read_text(encoding="utf-8")
LOCAL = {"base_url": "http://localhost:8083/v1", "model": "test-model", "api_key": ""}
BUDGET = chat.CHAT_PROMPT_TOKEN_BUDGET
_spec = importlib.util.spec_from_file_location("chat_summary_eval_under_test", REPO / "tools" / "chat_summary_eval.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)
DATA = ev.load_fixture()
REAL_COMPLETE = notes._complete


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    """No notes from another test, no live voice marker, and no real model call."""
    notes.clear()
    monkeypatch.setattr(notes, "VOICE_SESSION_PATH", str(tmp_path / "voice_session.json"))
    monkeypatch.setattr(notes, "_complete", lambda *a, **k: pytest.fail("unexpected model call"))
    yield
    notes.clear()


def scripted(monkeypatch, calls: list):
    """_complete replaced by a fake: one note line per user line of the chunk; a merge halves the notes."""
    def fake(messages, llm, max_tokens, whole=False):
        calls.append(copy.deepcopy(messages))
        if "Rewrite them" in messages[0]["content"]:
            return "- merged: " + messages[-1]["content"][:400]
        part = messages[-1]["content"].split("The part of the chat to write notes on:\n\n")[-1]
        return "\n".join("- " + line[6:120] for line in part.splitlines() if line.startswith("User: "))
    monkeypatch.setattr(notes, "_complete", fake)


def fit(messages, budget=BUDGET, config=None, llm=LOCAL, persona=False):
    return notes.fit_with_notes(messages, budget, config or {}, fit=chat._fit_prompt_budget, llm=llm, persona=persona)


def history_of(messages):
    start, end = notes._bounds(messages)
    return messages[start:end]


def test_markers_match_the_chat_route():
    assert notes.INTERNAL_CONTEXT_START == chat._INTERNAL_CONTEXT_START
    assert notes._ATTACHMENT_RE.pattern == chat._ATTACHMENT_RE.pattern
    assert notes.CHARS_PER_TOKEN == chat.CHARS_PER_TOKEN


def test_nothing_is_written_to_disk_and_codec_compaction_is_not_used():
    src = (REPO / "codec_chat_summary.py").read_text(encoding="utf-8")
    assert not re.search(r"(?<![\w.])open\(", src), "no file is opened"
    for banned in ("write_text", "write_bytes", "sqlite3", "codec_jsonstore", "codec_compaction", "codec_memory"):
        assert banned not in src, banned
    assert re.search(r"(?<![\w.])open\(", "with open(p) as f:"), "the check finds a real open("


@pytest.mark.parametrize("raw, on",[(None, True), (True, True), (False, False), (0, False), (1, True),
                                     ("off", False), ("No", False), ("false", False), ("yes", True)])
def test_the_switch(raw, on):
    assert notes.enabled({} if raw is None else {"chat_summary": raw}) is on


def test_the_fixture_is_60_turns_with_20_answerable_questions():
    assert len(DATA["turns"]) == 60 and len(DATA["questions"]) == 20
    for q in DATA["questions"]:
        i = q["turn"] - 1
        source = DATA["turns"][i]["user"] + " " + ev.reply(DATA, i)
        assert ev.answered(source, q["answers"]), q["q"]
    assert not ev.answered("The fair is on 14 June.", DATA["questions"][14]["answers"]), "the old date fails"


def test_notes_go_after_the_system_message_and_the_prompt_stays_in_the_budget(monkeypatch):
    calls: list = []
    scripted(monkeypatch, calls)
    at_40 = ev.messages_at(DATA, 40)
    _, _, job = fit(at_40)
    assert job is not None and job.start_at == 0 and job.run() is not None and calls
    msgs = ev.messages_at(DATA, 50)
    before = copy.deepcopy(msgs)
    sent, trim, _ = fit(msgs)
    assert msgs == before, "the request's own list is not changed"
    assert sent[0]["role"] == "system" and notes.is_notes_message(sent[1])
    assert "Tobias Henmarsh" in sent[1]["content"], "an early fact is in the notes"
    assert sent[-1] == msgs[-1], "the latest message is last, as typed"
    assert chat.estimate_prompt_tokens(sent) <= BUDGET
    kept = sent[2:-1]
    assert kept == msgs[-1 - len(kept):-1] and kept[0]["role"] == "user", "the newest whole turns follow"
    left_out = len(history_of(msgs)) - len(kept)
    covered = notes._lookup(notes._prefix_keys(history_of(msgs))).covered
    assert trim["summarised"] == min(left_out, covered) > 0 and trim["dropped"] == left_out - trim["summarised"]


def test_switched_off_sends_what_p034_sends(monkeypatch):
    scripted(monkeypatch, [])
    fit(ev.messages_at(DATA, 40))[2].run()
    msgs = ev.messages_at(DATA, 50)
    sent, trim, job = fit(msgs, config={"chat_summary": False})
    assert (sent, trim) == chat._fit_prompt_budget(msgs, BUDGET) and job is None
    assert not any(notes.is_notes_message(m) for m in sent) and "summarised" not in trim
    assert fit(msgs, budget=0) == (msgs, None, None), "no cap, no notes"


def test_a_job_is_planned_only_near_the_budget_on_this_mac_without_a_persona():
    assert fit(ev.messages_at(DATA, 10))[2] is None, "a short chat plans nothing"
    near = ev.messages_at(DATA, 30)
    job = fit(near)[2]
    assert job is not None and 0 == job.start_at < job.end
    hist = history_of(near)
    assert job.end == len(hist) or hist[job.end]["role"] == "user", "whole turns"
    assert fit(near, llm={**LOCAL, "base_url": "https://api.example.com/v1"})[2] is None, "never on a cloud URL"
    assert fit(near, persona=True)[2] is None and fit(near, llm=None)[2] is None


def test_a_job_writes_notes_the_next_turn_uses_and_never_sees_file_text(monkeypatch):
    calls: list = []
    scripted(monkeypatch, calls)
    secret = "The spare key is under the blue heron statue."
    doc = ("Harbour ledger season orchard kettle copper meadow. " * 300) + secret
    msgs = ev.messages_at(DATA, 40)
    msgs[1] = {"role": "user", "content": msgs[1]["content"] + "\n\n[DOCUMENT: report.pdf]\n" + doc + "\n[END DOCUMENT]"}
    job = fit(msgs)[2]
    done = job.run()
    asked = json.dumps(calls)
    assert "[file attached: report.pdf]" in asked and secret not in asked and "Harbour ledger" not in asked
    assert done.covered == job.end and len(calls) == len(job.chunks()) >= 2
    nxt = msgs + [{"role": "assistant", "content": "Noted."}, {"role": "user", "content": "Where is the spare key?"}]
    sent, trim, _ = fit(nxt)
    assert notes.is_notes_message(sent[1]) and trim["summarised"] > 0
    assert secret in sent[-2]["content"] and trim["excerpts"] == ["report.pdf"], "P-034's excerpt still carries the file"


def test_an_open_voice_session_stops_notes_before_and_during_a_call(monkeypatch):
    job = fit(ev.messages_at(DATA, 30))[2]
    marker = Path(notes.VOICE_SESSION_PATH)
    marker.write_text("{}")
    assert job.start() is False, "never started during a voice call (the autouse fake fails on any call)"
    assert job.run() is None and job.stopped == "voice"
    import codec_llm
    started: list = []
    monkeypatch.setattr(codec_llm, "stream", lambda messages, **kw: started.append(1) or iter(["- x\n"]))
    assert REAL_COMPLETE([{"role": "user", "content": "x"}], LOCAL, 100) == "" and started == [], \
        "every notes call (a merge too) checks first and starts nothing"
    marker.unlink()

    closed: list = []

    def fake_stream(messages, **kw):
        try:
            yield "- first note line\n"
            marker.write_text("{}")         # a voice call starts while the notes call streams
            for _ in range(50):
                yield "- more\n"
        finally:
            closed.append(True)

    monkeypatch.setattr(codec_llm, "stream", fake_stream)
    monkeypatch.setattr(notes, "_VOICE_CHECK_S", 0)
    assert REAL_COMPLETE([{"role": "user", "content": "x"}], LOCAL, 100) == "" and closed == [True], \
        "the call is dropped and its connection closed"
    marker.unlink()
    monkeypatch.setattr(codec_llm, "stream", lambda messages, **kw: iter(["- first note line\n", "- more\n"]))
    assert REAL_COMPLETE([{"role": "user", "content": "x"}], LOCAL, 100) == "- first note line\n- more"


def test_an_edited_earlier_message_gets_no_notes(monkeypatch):
    scripted(monkeypatch, [])
    fit(ev.messages_at(DATA, 40))[2].run()
    msgs = ev.messages_at(DATA, 45)
    assert fit(msgs)[1].get("summarised")
    edited = copy.deepcopy(msgs)
    edited[3]["content"] = "The association has given us a total budget of 9,999 euros."
    sent, trim, _ = fit(edited)
    assert not any(notes.is_notes_message(m) for m in sent) and "summarised" not in trim


def test_long_notes_are_merged(monkeypatch):
    calls: list = []
    scripted(monkeypatch, calls)
    msgs = ev.messages_at(DATA, 40)
    hist = history_of(msgs)
    job = notes.NotesJob(hist, notes._prefix_keys(hist), 0, "- an earlier note\n" * 400, 4000, LOCAL)
    done = job.run()
    merges = [c for c in calls if "Rewrite them" in c[0]["content"]]
    assert merges and done.text.startswith("- merged: ")
    assert notes._lookup(notes._prefix_keys(hist)) == done
    have = int(re.search(r"they are (\d+) words now", merges[0][0]["content"]).group(1))
    assert have == len(merges[0][1]["content"].split()), "the merge is told how long the notes are now"
    assert job.merges and job.merges[0][1] < job.merges[0][0]


def test_a_merge_cut_by_the_token_cap_is_not_used(monkeypatch):
    import codec_llm
    monkeypatch.setattr(codec_llm, "stream",
                        lambda messages, **kw: iter(["- one\n", "- two\n- thr", codec_llm.FINISH_LENGTH]))
    msg = [{"role": "user", "content": "x"}]
    assert REAL_COMPLETE(msg, LOCAL, 10) == "- one\n- two", "a cut segment keeps its whole lines"
    assert REAL_COMPLETE(msg, LOCAL, 10, whole=True) == "", "a cut merge would lose the newest notes"
    monkeypatch.setattr(notes, "_complete", lambda messages, llm, max_tokens, whole=False:
                        "" if whole else "- a new note")
    msgs = ev.messages_at(DATA, 40)
    hist = history_of(msgs)
    long = "- an earlier note\n" * 300
    job = notes.NotesJob(hist, notes._prefix_keys(hist), 0, long, 4000, LOCAL)
    assert job.run().text.startswith(long.strip()), "the notes stay as they were, not cut"
    assert job.merges and all(after == before for before, after in job.merges)


def test_notes_a_merge_cannot_shorten_stop_growing(monkeypatch):
    """Notes sit with the system message, where P-034 never leaves anything out, so a model
    that cannot shorten them must not let them grow past twice the merge cap (half the budget)."""
    monkeypatch.setattr(notes, "_complete", lambda messages, llm, max_tokens, whole=False:
                        "" if whole else "- a new note")
    msgs = ev.messages_at(DATA, 40)
    hist = history_of(msgs)
    job = notes.NotesJob(hist, notes._prefix_keys(hist), 0, "- an earlier note\n" * 600, 4000, LOCAL)
    assert job.run() is None and job.stopped == "notes too long"
    assert notes._lookup(notes._prefix_keys(hist)) is None, "nothing over the bound is kept"


@pytest.fixture
def api(tmp_path, monkeypatch):
    """POST /api/chat in process; the model, memory and skills are fakes; NotesJob.start records."""
    import codec_config
    import codec_daybreak
    import codec_llm
    import codec_models
    import codec_observer
    import codec_standing_rules
    import routes.prompts as prompts

    cfg = tmp_path / "config.json"
    monkeypatch.setattr(chat, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(codec_models, "ensure_llm_available", lambda: None)
    monkeypatch.setattr(codec_models, "model_extras", lambda *a, **k: {})
    monkeypatch.setattr(codec_config, "get_llm_api_key", lambda: "")
    monkeypatch.setattr(codec_standing_rules, "prompt_block", lambda **k: "")
    monkeypatch.setattr(codec_daybreak, "get_working_context", lambda: "")
    monkeypatch.setattr(codec_observer, "maybe_inject_observation_summary", lambda **k: ("", "skip"))
    monkeypatch.setattr(prompts, "_load_prompt_overrides", lambda: {})
    monkeypatch.setattr(chat, "_identity_block", lambda meta, temporary: "")
    monkeypatch.setattr(chat, "_recall_memory", lambda messages, meta=None, temporary=False: [])
    monkeypatch.setattr(chat, "_try_skill", lambda text, temporary=False: (None, None))
    monkeypatch.setattr(chat, "_maybe_escalate_suggestion", lambda text, sid: None)
    sent: list = []
    started: list = []

    def fake_stream(messages, **kw):
        sent.append(copy.deepcopy(messages))
        yield "Noted."

    def fake_call(messages, **kw):
        sent.append(copy.deepcopy(messages))
        return "Noted."

    monkeypatch.setattr(codec_llm, "stream", fake_stream)
    monkeypatch.setattr(codec_llm, "call", fake_call)
    monkeypatch.setattr(notes.NotesJob, "start", lambda self: started.append(self) or True)
    app = FastAPI()
    app.include_router(chat.router)
    client = TestClient(app)

    def post(messages, stream=True, **config):
        cfg.write_text(json.dumps({"llm_base_url": "http://localhost:8083/v1", "llm_model": "test-model", **config}))
        r = client.post("/api/chat", json={"messages": messages, "stream": stream, "thinking": False})
        assert r.status_code == 200, r.text
        frames = [json.loads(ln[6:]) for ln in r.text.splitlines()
                  if ln.startswith("data: ") and ln != "data: [DONE]"] if stream else [r.json()]
        return SimpleNamespace(sent=sent[-1], frames=frames, started=list(started))

    return post


def test_the_route_sends_notes_and_starts_the_job_after_the_reply(api, monkeypatch):
    out = api(ev.messages_at(DATA, 30))
    assert len(out.started) == 1 and out.started[0].start_at == 0, "planned at the fit, started after the reply"
    assert not any(notes.is_notes_message(m) for m in out.sent), "no notes exist yet"
    scripted(monkeypatch, [])
    out.started[0].run()
    later = api(ev.messages_at(DATA, 45))
    assert later.sent[0]["role"] == "system" and notes.is_notes_message(later.sent[1])
    (trim,) = [f["trim"] for f in later.frames if "trim" in f]
    assert trim["summarised"] > 0 and trim["dropped"] >= 0 and trim["tokens"] <= BUDGET
    plain = api(ev.messages_at(DATA, 45), stream=False)
    assert plain.frames[0]["trim"]["summarised"] == trim["summarised"] and notes.is_notes_message(plain.sent[1])
    off = api(ev.messages_at(DATA, 45), chat_summary=False)
    assert not any(notes.is_notes_message(m) for m in off.sent) and len(off.started) == len(plain.started)


def test_the_fit_runs_off_the_loop_thread(api, monkeypatch):
    """Up to four fits run per turn (slow with a big file), so not on the dashboard's event loop (P-016)."""
    import asyncio
    real, on_loop = notes.fit_with_notes, []

    def spy(*a, **k):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(*a, **k)

    monkeypatch.setattr(notes, "fit_with_notes", spy)
    api(ev.messages_at(DATA, 30))
    api(ev.messages_at(DATA, 30), stream=False)
    assert on_loop == [False, False]


def test_the_page_names_the_summarised_messages():
    fn = PAGE[PAGE.index("function renderTrimUsed(msgEl, t){"):PAGE.index("// ── Model picker")]
    assert "t.summarised" in fn and "innerHTML" not in fn


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_note_under_the_reply_says_summarised():
    fn = PAGE[PAGE.index("function renderTrimUsed(msgEl, t){"):PAGE.index("// ── Model picker")]
    script = fn + """
const made = [];
global.document = {createElement: () => { const e = {}; made.push(e); return e; }};
const meta = {appendChild: () => {}};
const msg = {querySelector: () => meta};
renderTrimUsed(msg, {budget: 12000, summarised: 40, dropped: 0, excerpts: [], over: false});
renderTrimUsed(msg, {budget: 12000, summarised: 1, dropped: 2, excerpts: ['report.pdf'], over: false});
console.log(JSON.stringify(made.map(e => e.textContent)));
"""
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == ["Shortened to fit: 40 earlier messages summarised",
                                    "Shortened to fit: 1 earlier message summarised, 2 earlier messages left out, "
                                    "excerpt of report.pdf"]


def test_the_eval_bars_and_the_log_reader():
    qs = [{"pass": i < 18, "notes_only": i < 14} for i in range(20)]
    assert ev.recall_verdict(qs)["met"] and not ev.recall_verdict([{"pass": i < 17} for i in range(20)])["met"]
    rows = [{"turn": t, "prefill_s": 10.0 if t == 30 else 11.9} for t in range(30, 61)]
    assert ev.prefill_verdict(rows)["met"] and ev.prefill_verdict(rows)["ratio"] == 1.19
    assert not ev.prefill_verdict(rows[:-1] + [{"turn": 60, "prefill_s": 12.1}])["met"]
    assert not ev.prefill_verdict(rows[:5] + [{**rows[5], "disturbed": True}] + rows[6:])["met"]
    assert not ev.prefill_verdict(rows[:5] + [{"turn": 35, "prefill_s": None}] + rows[6:])["met"], "every turn measured"
    lines = ["2026-11-04 01:25:17,914 - INFO - Request started: endpoint=/chat/completions model=m stream=False in_flight=1",
             "2026-11-04 01:25:21,343 - INFO - Prefill completed: request=a1 prompt_tokens=9400 cached_tokens=0 elapsed=11.250s rate=835.6 tok/s"]
    assert ev.read_prefill(lines, 9400) == {"prefill_s": 11.25, "server_prompt_tokens": 9400, "cached_tokens": 0,
                                            "disturbed": False}
    busy = lines + [lines[0].replace("in_flight=1", "in_flight=2"),
                    "2026-11-04 01:25:22,000 - INFO - Prefill completed: request=b2 prompt_tokens=209 cached_tokens=0 elapsed=0.4s"]
    assert ev.read_prefill(busy, 9400)["disturbed"] is True
    assert ev.read_prefill(lines, 123)["prefill_s"] is None, "a line for another request size is not ours"
