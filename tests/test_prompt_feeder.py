"""CODEC prompt_feeder — parsing + input-selection tests.

prompt_feeder drives the Pilot browser to type a list of prompts into an AI tool
one at a time. The fragile part is turning a spoken/typed request into (target,
prompts) — three real bugs were found there by hand during development:
  * dictated inline numbering "1. a 2. b" parsed as a single prompt
  * an explicit URL leaked its trailing colon into prompt #1
  * a decimal ("3.5 stars") got split on the '.'

These pin all three. Pure functions only — no network, no Pilot.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import skills.prompt_feeder as pf  # noqa: E402


# ── target resolution ─────────────────────────────────────────────────────────
class TestResolveTarget:
    def test_alias_by_name(self):
        assert pf._resolve_target("feed these into gemini: hi")[0] == "gemini"
        assert pf._resolve_target("run these on google flow: hi")[0] == "flow"
        assert pf._resolve_target("paste these into chatgpt: hi")[0] == "chatgpt"

    def test_explicit_url_wins_and_strips_trailing_colon(self):
        label, url = pf._resolve_target("feed into https://claude.ai/new: first")
        assert url == "https://claude.ai/new"          # trailing ':' dropped
        assert not url.endswith(":")

    def test_default_is_gemini(self):
        assert pf._resolve_target("feed these prompts: hello there")[0] == "gemini"


# ── prompt parsing ────────────────────────────────────────────────────────────
class TestParsePrompts:
    def test_dictated_inline_numbering(self):
        # The exact shape voice input produces — one line, run-together numbers.
        out = pf._parse_prompts(
            "feed these into flow: 1. a drone shot at golden hour "
            "2. the same shot at night 3. a close up of the marina")
        assert out == ["a drone shot at golden hour",
                       "the same shot at night",
                       "a close up of the marina"]

    def test_quoted_list(self):
        out = pf._parse_prompts('run on gemini: "explain quantum simply" "now shorter"')
        assert out == ["explain quantum simply", "now shorter"]

    def test_bulleted_lines(self):
        out = pf._parse_prompts(
            "paste into chatgpt:\n- a haiku about the sea\n- now funnier\n- now in French")
        assert out == ["a haiku about the sea", "now funnier", "now in French"]

    def test_explicit_url_does_not_leak_into_first_prompt(self):
        out = pf._parse_prompts("feed into https://claude.ai/new: first one\nsecond one")
        assert out == ["first one", "second one"]

    def test_single_instruction(self):
        out = pf._parse_prompts("send these one at a time: just a single instruction")
        assert out == ["just a single instruction"]

    def test_decimal_is_not_split(self):
        # "3.5" must NOT be treated as list markers "3." / "5"
        out = pf._parse_prompts("feed into gemini: rate this 3.5 stars and explain why")
        assert out == ["rate this 3.5 stars and explain why"]

    def test_numbered_with_parens(self):
        out = pf._parse_prompts("feed: 1) first\n2) second")
        assert out == ["first", "second"]

    def test_empty_yields_nothing(self):
        assert pf._parse_prompts("feed these prompts into gemini:") == []


# ── prompt-box selection ──────────────────────────────────────────────────────
class TestFindInput:
    def _el(self, role, name, w, h, disabled=False, left=0, top=0):
        e = {"role": role, "name": name,
             "bbox": {"left": left, "top": top, "width": w, "height": h}}
        if disabled:
            e["attrs"] = {"disabled": True}
        return e

    def test_prefers_named_prompt_box_over_search(self):
        els = [self._el("searchbox", "Search", 200, 20),
               self._el("textbox", "Enter a prompt for Gemini", 439, 24, left=390, top=388)]
        got = pf._find_input(els)
        assert got and got["name"] == "Enter a prompt for Gemini"

    def test_skips_disabled(self):
        els = [self._el("textbox", "prompt", 900, 90, disabled=True),
               self._el("textbox", "message box", 300, 20)]
        got = pf._find_input(els)
        assert got and got["name"] == "message box"

    def test_largest_when_none_named(self):
        els = [self._el("textbox", "", 100, 20),
               self._el("textbox", "", 400, 40)]
        got = pf._find_input(els)
        assert got and got["bbox"]["width"] == 400

    def test_none_when_no_inputs(self):
        assert pf._find_input([{"role": "button", "name": "Send",
                                "bbox": {"left": 0, "top": 0, "width": 40, "height": 20}}]) is None

    def test_center_math(self):
        cx, cy = pf._center({"bbox": {"left": 390, "top": 388, "width": 440, "height": 24}})
        assert (round(cx), round(cy)) == (610, 400)
