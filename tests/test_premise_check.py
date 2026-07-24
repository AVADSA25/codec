"""Premise checking — the OTHER direction from claim-check.

The incident (2026-07-24): the user's LinkedIn post credited a practice to a
commenter ("the most useful reply I got was from SOMEONE WHO ends each session
by listing…"). A correspondent then wrote back attributing it to the user ("YOU
MENTIONED YOU end each session by listing…"). Both texts were in context. The
assistant engaged with the premise and reinforced it — nobody invented anything,
a false premise simply propagated as fact.

The design bias is the strongest in the codebase, because a false positive here
tells the user they are wrong about their own life. Most of these tests are
false-positive guards.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import codec_premise_check as pc  # noqa: E402


POST = ("I posted the full set to a developer community last week. The most useful "
        "reply I got was from someone who ends each session by listing which of his "
        "standing rules the model actually used, then deletes the ones that never fire.")
QUESTION = ("You mentioned you end each session by listing which standing rules "
            "actually fired, then delete the ones that never trigger. In that cleanup "
            "process, was there ever a rule you had to delete?")


# ── the real incident ─────────────────────────────────────────────────────────
def test_the_actual_misattribution_is_caught():
    flags = pc.find_misattributions([POST, QUESTION])
    assert len(flags) == 1
    assert flags[0].credited_to == "someone"
    note = pc.premise_note(flags)
    assert "credits it to someone" in note
    assert "not which is right" in note, "must not assert who is correct"


def test_note_is_a_question_not_a_verdict():
    """All this can prove is that two passages disagree."""
    note = pc.premise_note(pc.find_misattributions([POST, QUESTION]))
    for overclaim in ("you are wrong", "incorrect", "that is false", "you did not"):
        assert overclaim not in note.lower()
    assert "worth checking" in note.lower()


def test_it_works_across_separate_messages():
    """The source and the claim need not be in the same message."""
    assert pc.find_misattributions([POST, "ok thanks", QUESTION])


def test_it_works_inside_one_pasted_thread():
    """The usual shape: a whole conversation pasted as one message."""
    assert pc.find_misattributions([POST + "\n\n" + QUESTION])


# ── FALSE-POSITIVE GUARDS — the tests that matter most ────────────────────────
@pytest.mark.parametrize("texts", [
    # third-party credit + an UNRELATED you-statement
    ["I got a tip from someone who runs their tests in a container before deploy.",
     "You mentioned you prefer working late at night."],
    # you-statement with no source to contradict it
    ["You mentioned you always verify the service is running before any step."],
    # third-party credit with no you-claim
    ["The best reply was from someone who deletes rules that never fire."],
    # genuinely the user's own practice, stated by them
    ["I end each session by listing which rules fired and deleting dead ones.",
     "You mentioned you end each session by listing which rules fired."],
    # a colleague quoted on a different subject
    ["A colleague who reviews every PR twice told me it halves defects.",
     "You said you ship on Fridays."],
    # phrases too thin to compare
    ["a tip from someone who tests more", "You mentioned you test more"],
    # ordinary conversation
    ["What's the weather?", "Can you summarise this document?"],
    ["", ""],
    [],
])
def test_no_false_positive(texts):
    assert pc.find_misattributions(texts) == [], texts


def test_partial_topic_overlap_is_not_enough():
    """Sharing a couple of words must not trip it — only a real paraphrase."""
    a = "The idea came from someone who writes standing rules for every project."
    b = "You mentioned you write documentation for every project."
    assert pc.find_misattributions([a, b]) == []


# ── operational ───────────────────────────────────────────────────────────────
def test_kill_switch(monkeypatch):
    monkeypatch.setenv("PREMISE_CHECK_ENABLED", "false")
    assert pc.find_misattributions([POST, QUESTION]) == []


def test_empty_flags_produce_no_note():
    assert pc.premise_note([]) == ""


def test_named_third_party_reads_naturally():
    """'a colleague' should possessivise; 'someone' should not."""
    a = ("The approach came from a colleague who batches every migration into a "
         "single reviewed transaction before it touches production data.")
    b = ("You mentioned you batch every migration into a single reviewed "
         "transaction before it touches production data.")
    flags = pc.find_misattributions([a, b])
    assert flags, "a named third party must still be caught"
    note = pc.premise_note(flags)
    assert "colleague's" in note and "someone else" not in note


def test_wired_into_both_chat_paths():
    """Guard the wiring against a refactor silently dropping it."""
    src = (_REPO / "routes" / "chat.py").read_text()
    assert src.count("codec_premise_check") >= 4, "must be wired in stream AND non-stream"
    gen = src[src.index("def _stream_gen("):]
    gen = gen[:gen.index("from starlette.responses import StreamingResponse")]
    assert "codec_premise_check" in gen, "streaming path must run the premise check"
    assert gen.index("codec_premise_check") < gen.index('"data: [DONE]'), \
        "the check must run before [DONE]"
