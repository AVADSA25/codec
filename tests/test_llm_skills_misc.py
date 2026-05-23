"""Tests for PR-3E-skills-misc — A-12 final tranche.

Migrates the last 6 inline chat/completions text sites onto codec_llm.call:
self_improve._draft_skill, watcher.handle_draft, and the skills translate /
fact_extract / create_skill / skill_forge. All graceful non-stream sites; the
behavior-critical contracts are pinned here (None-on-failure, __ERR__ sentinel).

Reference: docs/PR3E-SKILLS-MISC-DESIGN.md.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import codec_llm  # noqa: E402


def _load_skill(name):
    spec = importlib.util.spec_from_file_location(f"_skill_{name}", REPO / "skills" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── codec_self_improve._draft_skill (graceful → None on failure) ──────────────


def test_draft_skill_none_on_empty(monkeypatch):
    import codec_self_improve
    monkeypatch.setattr(codec_llm, "call", lambda *a, **k: "")
    gap = {"kind": "missing_tool", "tool": "foo", "examples": []}
    assert codec_self_improve._draft_skill(gap) is None


def test_draft_skill_returns_on_success(monkeypatch):
    import codec_self_improve
    code = 'SKILL_NAME = "foo"\nSKILL_TRIGGERS = ["foo"]\nSKILL_DESCRIPTION = "x"\ndef run(task, app="", ctx=""):\n    return "ok"\n'
    monkeypatch.setattr(codec_llm, "call", lambda *a, **k: code)
    gap = {"kind": "missing_tool", "tool": "foo", "examples": []}
    out = codec_self_improve._draft_skill(gap)
    assert out is not None and "def run" in out[1]


# ── skills/fact_extract._call_llm (__ERR__ sentinel preserved) ────────────────


def test_fact_extract_err_sentinel_on_failure(monkeypatch):
    fe = _load_skill("fact_extract")

    def raise_llm(*a, **k):
        raise codec_llm.LLMError("boom")

    monkeypatch.setattr(codec_llm, "call", raise_llm)
    out = fe._call_llm("prompt")
    assert out.startswith("__ERR__:")


def test_fact_extract_passes_content_through(monkeypatch):
    fe = _load_skill("fact_extract")
    monkeypatch.setattr(codec_llm, "call", lambda *a, **k: '["fact one"]')
    assert fe._call_llm("prompt") == '["fact one"]'


# ── source-level migration invariants ─────────────────────────────────────────


def test_self_improve_uses_codec_llm():
    src = (REPO / "codec_self_improve.py").read_text()
    assert "codec_llm.call(" in src
    assert "/chat/completions" not in src


def test_watcher_text_site_uses_codec_llm():
    src = (REPO / "codec_watcher.py").read_text()
    assert "codec_llm.call(" in src
    # The text site is gone; the VISION POST (QWEN_VISION_URL) stays (A-11).
    assert "QWEN_BASE_URL}/chat/completions" not in src
    assert src.count("/chat/completions") == 1   # only the vision POST remains


def test_skills_use_codec_llm():
    for name in ("translate", "fact_extract", "create_skill", "skill_forge"):
        src = (REPO / "skills" / f"{name}.py").read_text()
        assert "codec_llm.call(" in src, f"{name} not migrated"
        assert "/chat/completions" not in src, f"{name} still has inline POST"
