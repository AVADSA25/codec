"""The claim check must cover the STREAMING path too.

Deep Chat sends `stream: true` — and Deep Chat is where the incident happened.
Installing the guard only on the non-streaming path would have left it switched
off exactly where it was needed.

These tests exercise the wiring the stream generator uses: text is accumulated
as it is framed, skills that run are recorded, and the assembled reply is
checked before [DONE].
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import codec_claim_check as cc  # noqa: E402


def _simulate_stream(tokens, actions=()):
    """Mirror _stream_gen: accumulate framed text, then check before [DONE]."""
    visible = []
    frames = []
    for tok in tokens:
        if tok:
            visible.append(tok)
        frames.append(f"data: {json.dumps({'token': tok})}\n\n")
    unbacked = cc.find_unbacked_claims("".join(visible), actions_taken=set(actions))
    if unbacked:
        note = cc.correction_note(unbacked)
        frames.append(f"data: {json.dumps({'token': note})}\n\n")
    frames.append("data: [DONE]\n\n")
    return frames


def _text_of(frames):
    out = []
    for f in frames:
        m = re.match(r"data: (\{.*\})\n\n", f, re.S)
        if m:
            out.append(json.loads(m.group(1)).get("token", ""))
    return "".join(out)


def test_claim_split_across_tokens_is_caught():
    """The real risk of the streaming path: the sentence arrives in pieces, so
    no single token contains the claim."""
    tokens = ["Confirmed. ", "I have ", "ingested the ", "10-point ",
              "instruction set", ". I am now operating under this framework ",
              "for all future interactions."]
    frames = _simulate_stream(tokens)
    assert "Correction" in _text_of(frames)


def test_correction_arrives_before_done():
    frames = _simulate_stream(["I'll remember this for future sessions."])
    assert frames[-1] == "data: [DONE]\n\n"
    assert "Correction" in _text_of(frames[:-1]), "correction must precede [DONE]"


def test_honest_stream_gets_no_correction():
    frames = _simulate_stream(["Python ", "is a good ", "first language."])
    assert "Correction" not in _text_of(frames)
    assert frames[-1] == "data: [DONE]\n\n"


def test_backed_action_gets_no_correction():
    """A file claim IS fine when a file skill actually ran in the stream."""
    frames = _simulate_stream(
        ["I've saved ", "the summary ", "to your Desktop."],
        actions=("file_write",))
    assert "Correction" not in _text_of(frames)


def test_unbacked_action_is_corrected():
    frames = _simulate_stream(["I've saved ", "the summary ", "to your Desktop."])
    assert "Correction" in _text_of(frames)


def test_empty_stream_is_safe():
    frames = _simulate_stream([])
    assert frames == ["data: [DONE]\n\n"]


def test_stream_generator_is_actually_wired():
    """Guard against the wiring being dropped in a refactor — the tests above
    simulate the logic, this asserts the real generator carries it."""
    src = (_REPO / "routes" / "chat.py").read_text()
    gen = src[src.index("def _stream_gen("):]
    gen = gen[:gen.index("from starlette.responses import StreamingResponse")]
    assert "_visible.append(tok)" in gen, "stream must accumulate visible text"
    assert "_stream_actions.add(s_name)" in gen, "stream must record skills that ran"
    assert "codec_claim_check" in gen, "stream must run the claim check"
    assert gen.index("codec_claim_check") < gen.index('"data: [DONE]'), \
        "the check must run before [DONE]"
