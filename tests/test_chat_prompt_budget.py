"""Deep Chat prompt budget (P-034, docs/CHAT-PROMPT-BUDGET-DESIGN.md).

The page resends its whole history, and an attached file of up to 80,000
characters, on every turn. /api/chat keeps what it sends to the model under
config.json:chat_prompt_token_budget (default 12,000): the system prompt, this
turn's context and the latest message stay, an older file becomes an excerpt
chosen by relevance to the latest message, and the oldest turns go first. The
turn that brings a file may go over once, by that file.

Fixture: an 80,000-character report with one planted fact after character
70,000, and a 30-turn chat whose first turn attached it. The model is a fake
that records what it was sent; estimate_prompt_tokens is the one estimate used
for the cap and here.
"""
from __future__ import annotations

import copy
import json
import math
import random
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codec_dashboard  # noqa: F401  (the chat's system prompt is imported from it per request)
import routes.chat as chat

REPO = Path(__file__).resolve().parent.parent
PAGE = (REPO / "codec_chat.html").read_text(encoding="utf-8")
BUDGET = chat.CHAT_PROMPT_TOKEN_BUDGET
PLANTED = "The spare key to the boathouse is kept under the blue heron statue by the east gate."
QUESTION = "Where is the spare key to the boathouse kept?"
MEMORY = "[MEMORY]\nan old talk about the harbour\n[END MEMORY]"
_WORDS = ("harbour ledger season orchard kettle copper meadow lantern quarry tide basil cedar granite "
          "velvet market thistle compass ember willow marble river saffron anchor pebble summit cobalt "
          "fennel walnut prairie beacon canyon juniper glacier violet hazel").split()
_NEAR_MISSES = ("The key findings for the harbour budget are listed in the annex. ",
                "Boats are stored for the winter in the north shed. ",
                "The east wing of the market stays closed for repairs. ")


def report(size: int = 80_000, at: int = 74_000) -> str:
    """A report of `size` characters with PLANTED at character `at` and near misses elsewhere."""
    rnd, parts, n = random.Random(34), [], 0
    while n < size:
        s = (rnd.choice(_NEAR_MISSES) if rnd.random() < 0.04 else
             " ".join(rnd.choice(_WORDS) for _ in range(rnd.randint(8, 16))).capitalize() + ". ")
        parts.append(s)
        n += len(s)
    text = "".join(parts)
    return (text[:at] + PLANTED + " " + text[at:])[:size]


def attach(text: str, doc: str, name: str = "report.pdf") -> str:
    """The user message codec_chat.html builds for a message with an attached file."""
    return text + "\n\n[DOCUMENT: " + name + "]\n" + doc + "\n[END DOCUMENT]"


def chat_at_turn_30(doc: str, question: str = QUESTION) -> list:
    rnd = random.Random(30)
    hist = [{"role": "user", "content": attach("Please summarise this report.", doc)},
            {"role": "assistant", "content": "The report covers the harbour, the orchard and the market."}]
    for turn in range(2, 30):
        hist.append({"role": "user", "content": f"Turn {turn}: what does the report say about the "
                                                f"{rnd.choice(_WORDS)} and the {rnd.choice(_WORDS)}?"})
        hist.append({"role": "assistant", "content": " ".join(rnd.choice(_WORDS) for _ in range(110))})
    hist.append({"role": "user", "content": question})
    assert sum(1 for m in hist if m["role"] == "user") == 30
    return hist


@pytest.fixture
def api(tmp_path, monkeypatch):
    """POST /api/chat in process; the model, memory and skills are fakes."""
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
    monkeypatch.setattr(chat, "_recall_memory", lambda messages, meta=None, temporary=False: [MEMORY])
    monkeypatch.setattr(chat, "_try_skill", lambda text, temporary=False: (None, None))
    monkeypatch.setattr(chat, "_maybe_escalate_suggestion", lambda text, sid: None)
    sent: list = []

    def fake_stream(messages, **kw):
        sent.append(copy.deepcopy(messages))
        yield "It is under the blue heron statue."

    def fake_call(messages, **kw):
        sent.append(copy.deepcopy(messages))
        return "It is under the blue heron statue."

    monkeypatch.setattr(codec_llm, "stream", fake_stream)
    monkeypatch.setattr(codec_llm, "call", fake_call)
    app = FastAPI()
    app.include_router(chat.router)
    client = TestClient(app)

    def post(messages, stream=True, **config):
        # P-038's notes have their own tests (test_chat_summary.py); these pin P-034 alone.
        cfg.write_text(json.dumps({"llm_base_url": "http://localhost:8083/v1", "llm_model": "test-model",
                                   "chat_summary": False, **config}))
        r = client.post("/api/chat", json={"messages": messages, "stream": stream, "thinking": False})
        assert r.status_code == 200, r.text
        frames = [json.loads(ln[6:]) for ln in r.text.splitlines()
                  if ln.startswith("data: ") and ln != "data: [DONE]"] if stream else [r.json()]
        return SimpleNamespace(sent=sent[-1], frames=frames)

    return post


def _excerpt(sent: list) -> str:
    (msg,) = [m["content"] for m in sent if m["content"].startswith("(Files attached earlier")]
    return msg


def test_one_estimate_for_the_cap_and_the_tests():
    est = chat.estimate_prompt_tokens([{"role": "user", "content": "x" * 80_000}])
    assert est == math.ceil(80_000 / 3.5) + 4 and 22_000 < est < 24_000, "80,000 characters is about 23k tokens"
    assert chat.estimate_prompt_tokens([{"content": [{"type": "text", "text": "abcdefg"}]}, {"content": None}]) == 2 + 4 + 4
    assert BUDGET == 12_000


@pytest.mark.parametrize("raw, want", [(None, 12_000), ("15000", 15_000), (30_000, 30_000), (0, 0), (-5, 0),
                                       (500, 2_000), ("lots", 12_000), (True, 12_000), ([1], 12_000)])
def test_the_budget_setting(raw, want):
    config = {} if raw is None else {"chat_prompt_token_budget": raw}
    assert chat._prompt_token_budget(config) == want


def test_turn_30_is_sent_within_the_budget_with_the_planted_fact(api):
    doc = report()
    assert len(doc) == 80_000 and doc.index(PLANTED) > 70_000 and doc.count("boathouse") == 1
    hist = chat_at_turn_30(doc)
    assert chat.estimate_prompt_tokens(hist) > 2 * BUDGET, "the whole chat is far over the budget"
    out = api(hist)
    sent = out.sent
    assert chat.estimate_prompt_tokens(sent) <= BUDGET
    assert sent[0]["role"] == "system" and "## Concrete skill-tag examples" in sent[0]["content"], "system prompt kept"
    assert sent[-1] == {"role": "user", "content": QUESTION}, "the question is last, as typed"
    assert MEMORY in sent[-3]["content"] and sent[-3]["content"].startswith("(INTERNAL CONTEXT"), "this turn's context kept"
    ex = _excerpt(sent)
    assert sent[-2]["content"] == ex and PLANTED in ex and "Do not guess." in ex
    assert sum(len(m["content"]) for m in sent) < len(doc) and not any(doc in m["content"] for m in sent), \
        "the whole file is not resent"
    assert not any(doc[:200] in m["content"] for m in sent[1:-3]), "the file is out of its old turn"
    (trim,) = [f["trim"] for f in out.frames if "trim" in f]
    assert trim == {"budget": BUDGET, "tokens": chat.estimate_prompt_tokens(sent),
                    "dropped": trim["dropped"], "excerpts": ["report.pdf"], "not_sent": [], "over": False}
    assert 0 < trim["dropped"] < len(hist) - 1
    kept = sent[1:-3]
    assert kept == hist[-1 - len(kept):-1], "the newest turns stay, the oldest went first"
    assert kept[0]["role"] == "user", "whole turns are left out"
    order = [next(iter(f)) for f in out.frames]
    assert order.index("trim") < order.index("token")


def test_the_turn_that_brings_the_file_sends_all_of_it(api):
    doc = report()
    hist = [m for turn in range(9) for m in (
        {"role": "user", "content": f"Turn {turn}: tell me about the orchard."},
        {"role": "assistant", "content": "The orchard has walnut and hazel trees. " * 200})]
    hist.append({"role": "user", "content": attach("Please summarise this report.", doc)})
    out = api(hist)
    assert out.sent[-1] == hist[-1], "the whole file goes the first time"
    total = chat.estimate_prompt_tokens(out.sent)
    assert total > BUDGET and total - math.ceil(len(doc) / 3.5) <= BUDGET, "over once, by the new file only"
    (trim,) = [f["trim"] for f in out.frames if "trim" in f]
    assert trim["over"] is True and trim["excerpts"] == [] and trim["dropped"] > 0


def test_a_question_the_file_does_not_answer(api):
    out = api(chat_at_turn_30(report(), "What colour is the lighthouse door?"))
    ex = _excerpt(out.sent)
    assert PLANTED not in ex, "the excerpt follows the question, it is not the whole file"
    assert chat.estimate_prompt_tokens([{"content": ex}]) <= BUDGET // 3 + 6
    assert "say it is not in the part of the file you can see" in ex and "Do not guess." in ex


def test_a_short_chat_is_sent_unchanged(api):
    hist = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "Hi."},
            {"role": "user", "content": "how tall is the tower?"}]
    out = api(hist)
    assert out.sent[1:-2] == hist[:-1] and out.sent[-1] == hist[-1]
    assert not any("trim" in f for f in out.frames)


def test_the_cap_can_be_turned_off_and_the_plain_reply_carries_it(api):
    hist = chat_at_turn_30(report())
    off = api(hist, chat_prompt_token_budget=0)
    assert chat.estimate_prompt_tokens(off.sent) > 2 * BUDGET and not any("trim" in f for f in off.frames)
    plain = api(hist, stream=False, chat_prompt_token_budget=8_000)
    assert plain.frames[0]["trim"]["budget"] == 8_000 and chat.estimate_prompt_tokens(plain.sent) <= 8_000


def test_an_image_description_is_kept_the_same_way_and_nothing_is_changed_in_place():
    desc = "A photo of a harbour at dusk. " * 900 + "The boat on the left is named Persephone."
    hist = [{"role": "system", "content": "You are CODEC."},
            {"role": "user", "content": "Look.\n\n[IMAGE ANALYSIS of dusk.jpg]\n" + desc + "\n[END IMAGE]"},
            {"role": "assistant", "content": "A quiet harbour."},
            {"role": "user", "content": "What is the boat on the left named?"}]
    before = copy.deepcopy(hist)
    sent, trim = chat._fit_prompt_budget(hist, 2_000)
    assert hist == before, "the request's own list is not changed"
    assert trim["excerpts"] == ["dusk.jpg"] and "Persephone" in sent[-2]["content"]
    assert chat.estimate_prompt_tokens(sent) <= 2_000
    assert chat._fit_prompt_budget(hist, 0) == (hist, None)


def test_a_file_name_with_brackets_is_still_an_attachment():
    doc = report()
    sys_msg = {"role": "system", "content": "You are CODEC."}
    later = [sys_msg, {"role": "user", "content": attach("Summarise.", doc, name="Invoice [2026].pdf")},
             {"role": "assistant", "content": "Done."}, {"role": "user", "content": QUESTION}]
    sent, trim = chat._fit_prompt_budget(later, BUDGET)
    assert trim["excerpts"] == ["Invoice [2026].pdf"] and PLANTED in sent[-2]["content"]
    first = [sys_msg] + [m for turn in range(9) for m in (
        {"role": "user", "content": f"Turn {turn}: the orchard?"},
        {"role": "assistant", "content": "Walnut and hazel trees. " * 300})]
    first.append({"role": "user", "content": attach("Summarise.", doc, name="a [b].pdf")})
    sent, trim = chat._fit_prompt_budget(first, BUDGET)
    without_file = chat.estimate_prompt_tokens(sent) - math.ceil(len(doc) / 3.5)
    assert without_file <= BUDGET and len(sent) > 2, "the new file is not counted, history stays"


def test_a_file_with_no_room_left_is_named_as_not_sent():
    hist = [{"role": "system", "content": "You are CODEC."},
            {"role": "user", "content": attach("Look.", report())}, {"role": "assistant", "content": "Done."},
            {"role": "user", "content": "y " * 30_000 + QUESTION}]
    sent, trim = chat._fit_prompt_budget(hist, BUDGET)
    assert trim["excerpts"] == [] and trim["not_sent"] == ["report.pdf"] and trim["over"] is True
    assert "not sent this turn" in sent[-2]["content"] and PLANTED not in sent[-2]["content"]


def test_the_page_says_when_a_reply_was_shortened():
    assert PAGE.count("if(j.trim){_lastTrim=j.trim}") == 2, "send and regenerate"
    assert "if(md&&_lastTrim)renderTrimUsed(md,_lastTrim);_lastTrim=null;" in PAGE
    assert "if(md2&&_lastTrim)renderTrimUsed(md2,_lastTrim);_lastTrim=null;" in PAGE
    assert ".msg-trim{" in PAGE
    fn = PAGE[PAGE.index("function renderTrimUsed(msgEl, t){"):PAGE.index("// ── Model picker")]
    assert "innerHTML" not in fn and "textContent" in fn


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_note_under_the_reply_reads_plainly():
    fn = PAGE[PAGE.index("function renderTrimUsed(msgEl, t){"):PAGE.index("// ── Model picker")]
    script = fn + """
const made = [];
global.document = {createElement: () => { const e = {}; made.push(e); return e; }};
const meta = {appendChild: () => {}};
const msg = {querySelector: () => meta};
renderTrimUsed(msg, {budget: 12000, dropped: 24, excerpts: ['report.pdf'], over: false});
renderTrimUsed(msg, {budget: 12000, dropped: 1, excerpts: ['a.pdf', 'b.pdf', 'c.pdf'], over: false});
renderTrimUsed(msg, {budget: 12000, dropped: 0, excerpts: [], over: true});
renderTrimUsed(msg, {budget: 12000, dropped: 2, excerpts: [], not_sent: ['report.pdf'], over: true});
console.log(JSON.stringify(made.map(e => e.textContent)));
"""
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == ["Shortened to fit: 24 earlier messages left out, excerpt of report.pdf",
                                    "Shortened to fit: 1 earlier message left out, excerpt of a.pdf, b.pdf and 1 more",
                                    "Shortened to fit: 2 earlier messages left out, report.pdf not sent"]
