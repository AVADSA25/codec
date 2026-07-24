"""Premise checking — verify attributions the USER supplies, not only CODEC's claims.

codec_claim_check is directional: it validates assistant → user. This module
covers the other direction, and it exists because of a real miss.

The incident (2026-07-24). The user's LinkedIn post said:

    "The most useful reply I got was from SOMEONE WHO ends each session by
     listing which of his standing rules the model actually used…"

A correspondent then wrote back:

    "YOU MENTIONED YOU end each session by listing which standing rules
     actually fired…"

The practice belonged to a commenter; the question attributed it to the user.
Both texts were in context. Neither CODEC nor the assistant flagged it — the
answer engaged with the premise and reinforced it. That is a worse failure than
invention: nobody made anything up, a false premise simply propagated as fact
through the answer, the user's reply, and onward to a third party.

SCOPE — deliberately tiny
-------------------------
Only attributions with an artifact IN CONTEXT are checkable: the source text is
right there, so the discrepancy is a fact about two strings, not a judgement.

Everything else — premises about the world, the user's business, their history —
has nothing to check against. A general premise-checker would just relocate the
fabrication one level up, with CODEC confidently "correcting" the user from
nothing. That is strictly worse than the gap it closes, so it is not built.

TONE — a question, never a correction
-------------------------------------
All this can prove is that two passages disagree. It cannot know which is right;
the user may be paraphrasing loosely, or the commenter may have adopted the
practice since. So the output asks. A confident correction here would be the
same overreach this codebase exists to refuse, aimed at the user instead.

Design bias, as everywhere in this area: FALSE NEGATIVES OVER FALSE POSITIVES.
A false positive tells the user they are wrong about their own life. The bar is
four independent conditions, all of which must hold.
"""
from __future__ import annotations

import os
import re
from typing import List, NamedTuple


class PremiseFlag(NamedTuple):
    practice: str        # the practice being attributed, as the source words it
    credited_to: str     # who the source credits it to ("someone", "a commenter")
    quote_source: str    # the source sentence
    quote_claim: str     # the sentence attributing it to the user


# ── Attribution patterns ──────────────────────────────────────────────────────
# A source crediting a practice to a THIRD PARTY.
_THIRD_PARTY = re.compile(
    r"\b(?:from|by)\s+"
    r"(someone|somebody|a\s+(?:commenter|reader|colleague|friend|founder|dev(?:eloper)?|"
    r"engineer|user|guy|person|reply|responder))\s+"
    r"who\s+([^.!?\n]{15,200})",
    re.I)

# A message crediting a practice to the USER ("you").
_SECOND_PERSON = re.compile(
    r"\byou\s+(?:mentioned|said|noted|wrote|described|explained|told me)\s+"
    r"(?:that\s+)?you\s+([^.!?\n]{15,200})",
    re.I)

_STOPWORDS = frozenset("""
the a an of to by which who whom whose that this these those then than and or but
is are was were be been being am i you he she it we they them him her his their my
your our its as at in on for with from into over under about after before each any
some all not no do does did done have has had can could will would should may might
""".split())

_MIN_OVERLAP = 0.55      # containment of the shorter phrase's content words
_MIN_WORDS = 4           # below this, overlap is noise


def _content_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]+", (text or "").lower())
            if w not in _STOPWORDS and len(w) > 2}


def _containment(a: set, b: set) -> float:
    """Shared fraction of the SHORTER phrase — a paraphrase drops words, so
    symmetric Jaccard would under-score the exact case this exists to catch."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _sentence_around(text: str, index: int) -> str:
    start = max(text.rfind(".", 0, index), text.rfind("\n", 0, index)) + 1
    end = min([x for x in (text.find(".", index), text.find("\n", index)) if x != -1]
              or [len(text)])
    return text[start:end].strip()[:200]


def _enabled() -> bool:
    return (os.environ.get("PREMISE_CHECK_ENABLED") or "true").strip().lower() \
        not in ("false", "0", "no", "off")


def find_misattributions(texts: List[str]) -> List[PremiseFlag]:
    """Practices a source credits to a third party but a later message credits
    to the user. `texts` is every user-supplied string in the conversation —
    pasted documents and messages alike, since the contradiction is usually
    inside a single pasted thread.

    Returns [] unless all four conditions hold:
      1. a third-party attribution exists in context
      2. a second-person attribution exists in context
      3. both describe enough content to compare (>= _MIN_WORDS)
      4. they overlap above _MIN_OVERLAP
    """
    if not _enabled() or not texts:
        return []
    pooled = "\n".join(t for t in texts if t)
    if not pooled:
        return []

    third = [(m.group(1).strip(), m.group(2).strip(), m.start())
             for m in _THIRD_PARTY.finditer(pooled)]
    if not third:
        return []
    second = [(m.group(1).strip(), m.start()) for m in _SECOND_PERSON.finditer(pooled)]
    if not second:
        return []

    flags: List[PremiseFlag] = []
    for who, practice, t_idx in third:
        pw = _content_words(practice)
        if len(pw) < _MIN_WORDS:
            continue
        for claimed, s_idx in second:
            cw = _content_words(claimed)
            if len(cw) < _MIN_WORDS:
                continue
            if _containment(pw, cw) >= _MIN_OVERLAP:
                flags.append(PremiseFlag(
                    practice=practice[:160],
                    credited_to=who.lower(),
                    quote_source=_sentence_around(pooled, t_idx),
                    quote_claim=_sentence_around(pooled, s_idx),
                ))
                break        # one flag per third-party attribution
    return flags


def premise_note(flags: List[PremiseFlag]) -> str:
    """The note appended to a reply. Empty when there is nothing to raise.

    Phrased as a question on purpose — all that is provable here is that two
    passages disagree, not which of them is right.
    """
    if not flags:
        return ""
    f = flags[0]
    # "someone" / "somebody" read badly with a possessive, so vary the closer.
    anon = f.credited_to in ("someone", "somebody")
    whose = "theirs" if anon else f"{f.credited_to}'s"
    return (
        "\n\n---\n"
        f"**Worth checking before you reply.** This is being treated as your own "
        f"practice, but the text you pasted credits it to {f.credited_to} else:\n\n"
        f"> {f.quote_source}\n\n"
        f"vs.\n\n"
        f"> {f.quote_claim}\n\n"
        f"I can only tell you the two disagree, not which is right — you may be "
        f"paraphrasing, or they may have picked it up since. But if it was {whose}, "
        f"answering as though it were yours takes back credit you already gave away."
    ) if anon else (
        "\n\n---\n"
        f"**Worth checking before you reply.** This is being treated as your own "
        f"practice, but the text you pasted credits it to {f.credited_to}:\n\n"
        f"> {f.quote_source}\n\n"
        f"vs.\n\n"
        f"> {f.quote_claim}\n\n"
        f"I can only tell you the two disagree, not which is right — you may be "
        f"paraphrasing, or they may have picked it up since. But if it was {whose}, "
        f"answering as though it were yours takes back credit you already gave away."
    )
