"""Tests for PR-3D-c (A-6) — SkillTagBuffer extracted from chat_completion._stream_gen.

SkillTagBuffer is the pure token state machine behind the dashboard chat stream:
strips <think>…</think> across chunk boundaries and buffers [SKILL:name:query]
tags so the raw tag never leaks, resolving them via an injected callback. The
SSE/HTTP plumbing stays in codec_dashboard. These tests pin the subtle protocol
(partial-prefix match, 5000-char cap, cross-chunk assembly, visible-char
tracking) as a unit. Reference: docs/PR3D-MONOLITH-EXTRACT-DESIGN.md §A-6.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from codec_chat_stream import SkillTagBuffer, SKILL_TAG_RE  # noqa: E402


def _run(tokens, resolve=None):
    """Feed tokens + finish; return (buffer, emitted_list)."""
    resolve = resolve or (lambda tag: tag)
    buf = SkillTagBuffer(resolve)
    out = []
    for t in tokens:
        out.extend(buf.feed(t))
    out.extend(buf.finish())
    return buf, out


# ── plain text ────────────────────────────────────────────────────────────────


def test_plain_text_passthrough():
    buf, out = _run(["hello world"])
    assert "".join(out) == "hello world"
    assert buf.visible_chars == 11


def test_plain_text_split_tokens():
    buf, out = _run(["hel", "lo ", "world"])
    assert "".join(out) == "hello world"
    assert buf.visible_chars == 11


# ── <think> stripping (cross-chunk; same-chunk close is dropped — faithful) ────


def test_think_stripped_across_chunks():
    # Faithful to the original: <think> zeroes the token, so </think> must
    # arrive in a later chunk; think-adjacent text is emitted but NOT counted.
    buf, out = _run(["a<think>", "secret reasoning", "</think>b"])
    assert "".join(out) == "ab"
    assert buf.visible_chars == 0   # think-adjacent text isn't counted (quirk preserved)


def test_think_only_emits_nothing_visible():
    buf, out = _run(["<think>", "planning", "</think>"])
    assert "".join(out) == ""
    assert buf.visible_chars == 0


# ── [SKILL:...] resolution ────────────────────────────────────────────────────


def test_skill_tag_resolved_single_token():
    buf, out = _run(["[SKILL:calc:2+2]"], resolve=lambda tag: "**4**")
    assert "".join(out) == "**4**"
    assert buf.visible_chars == 5


def test_skill_tag_assembled_across_tokens():
    seen = []

    def resolve(tag):
        seen.append(tag)
        return "R"

    buf, out = _run(["x [SKILL:c", "alc:2+2]"], resolve=resolve)
    assert seen == ["[SKILL:calc:2+2]"]      # reassembled across the chunk split
    assert "".join(out) == "x R"


def test_skill_tag_dropped_yields_empty_no_leak():
    # resolve -> "" (dropped tag). The raw tag must NEVER appear in output.
    buf, out = _run(["[SKILL:bad:x]"], resolve=lambda tag: "")
    assert "[SKILL:" not in "".join(out)
    assert buf.visible_chars == 0


def test_text_around_resolved_tag():
    buf, out = _run(["before [SKILL:calc:1]after"], resolve=lambda tag: "Z")
    assert "".join(out) == "before Zafter"


# ── non-tag brackets pass through raw ──────────────────────────────────────────


def test_non_tag_bracket_passthrough():
    called = []
    buf, out = _run(["text [not a tag] more"], resolve=lambda t: called.append(t) or "")
    assert "".join(out) == "text [not a tag] more"
    assert called == []                       # resolve never invoked for non-tags
    assert buf.visible_chars == len("text [not a tag] more")


def test_bracket_prefix_divergence_early():
    # "[x" diverges from "[SKILL:" immediately → emitted raw.
    buf, out = _run(["[xyz]"])
    assert "".join(out) == "[xyz]"


# ── safety cap + finish flush ─────────────────────────────────────────────────


def test_safety_cap_flushes_raw_without_resolving():
    called = []
    long_unclosed = "[SKILL:" + "a" * 6000           # no closing ']'
    buf, out = _run([long_unclosed], resolve=lambda t: called.append(t) or "X")
    assert called == []                       # never resolved (no ']'; hit 5000 cap)
    assert "[SKILL:" in "".join(out)          # flushed raw, nothing lost


def test_finish_flushes_pending_incomplete_tag():
    seen = []

    def resolve(tag):
        seen.append(tag)
        return "DONE"

    buf, out = _run(["[SKILL:calc:2+2"], resolve=resolve)   # stream ends mid-tag
    assert seen == ["[SKILL:calc:2+2"]        # finish() resolves the pending buffer
    assert "".join(out) == "DONE"


# ── exported regex ────────────────────────────────────────────────────────────


def test_skill_tag_re_matches():
    m = SKILL_TAG_RE.search("prefix [SKILL:translate:hola] suffix")
    assert m and m.group(1) == "translate" and m.group(2) == "hola"
