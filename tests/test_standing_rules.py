"""Standing rules must actually persist — that's the entire point.

CODEC used to claim it had "ingested" a rules document and was "operating under
this framework for all future interactions", with no mechanism behind it. It
bluffed because the honest answer ("I can't do that") had no alternative. This
module is the alternative, so the tests care most about one property: after a
write, the rules are on disk and in the prompt.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


@pytest.fixture
def sr(tmp_path, monkeypatch):
    import codec_standing_rules as mod
    monkeypatch.setattr(mod, "RULES_PATH", tmp_path / "standing_rules.json")
    return mod


def test_rule_survives_on_disk(sr):
    """The property CODEC previously faked."""
    sr.add_rule("Always answer in French.")
    assert sr.RULES_PATH.exists(), "a saved rule must reach the disk"
    # Re-read through a fresh load — no in-memory state
    assert any("French" in r["text"] for r in sr.load()["rules"])


def test_rule_reaches_the_prompt(sr):
    sr.add_rule("Never say done without proof.")
    block = sr.prompt_block()
    assert "STANDING RULES" in block and "Never say done without proof." in block


def test_no_rules_means_no_prompt_noise(sr):
    assert sr.prompt_block() == "", "empty rules must add nothing to the prompt"


def test_duplicates_rejected(sr):
    sr.add_rule("Be concise.")
    out = sr.add_rule("be CONCISE.")
    assert out["ok"] is False and "already" in out["message"].lower()
    assert len(sr.list_rules()) == 1


def test_empty_rule_rejected(sr):
    assert sr.add_rule("   ")["ok"] is False


def test_overlong_rule_rejected(sr):
    out = sr.add_rule("x" * (sr.MAX_RULE_CHARS + 1))
    assert out["ok"] is False and "characters" in out["message"]
    assert sr.list_rules() == []


def test_rule_cap_enforced(sr):
    for i in range(sr.MAX_RULES):
        assert sr.add_rule(f"rule number {i}")["ok"] is True
    out = sr.add_rule("one too many")
    assert out["ok"] is False and "limit" in out["message"].lower()


def test_remove_by_index_and_by_id(sr):
    sr.add_rule("first")
    r2 = sr.add_rule("second")["rule"]
    assert sr.remove_rule("1")["ok"] is True
    assert [r["text"] for r in sr.list_rules()] == ["second"]
    assert sr.remove_rule(r2["id"])["ok"] is True
    assert sr.list_rules() == []


def test_remove_unknown_is_graceful(sr):
    sr.add_rule("only one")
    out = sr.remove_rule("99")
    assert out["ok"] is False and len(sr.list_rules()) == 1


def test_clear(sr):
    sr.add_rule("a"); sr.add_rule("b")
    assert sr.clear_rules()["ok"] is True
    assert sr.list_rules() == []


def test_corrupt_file_does_not_crash(sr):
    sr.RULES_PATH.write_text("{ not json")
    assert sr.list_rules() == []
    assert sr.add_rule("recovers")["ok"] is True


# ── the skill wrapper ─────────────────────────────────────────────────────────
def _skill(monkeypatch, tmp_path):
    import importlib.util
    import codec_standing_rules as mod
    monkeypatch.setattr(mod, "RULES_PATH", tmp_path / "sr.json")
    spec = importlib.util.spec_from_file_location(
        "sr_skill", _REPO / "skills" / "standing_rules.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_skill_add_and_list(monkeypatch, tmp_path):
    s = _skill(monkeypatch, tmp_path)
    out = s.run("add a standing rule: always answer in French")
    assert "saved" in out.lower() and "standing_rules.json" in out
    listed = s.run("show my standing rules")
    assert "always answer in French" in listed


def test_skill_remove(monkeypatch, tmp_path):
    s = _skill(monkeypatch, tmp_path)
    s.run("add a standing rule: be brief")
    assert "Removed" in s.run("remove standing rule 1")
    assert "no standing rules" in s.run("list standing rules").lower()


# ── inject_count telemetry ────────────────────────────────────────────────────
# Named inject_count, NOT fired_count, deliberately. A standing rule's whole
# lifecycle is prompt_block() -> string -> sys_prompt -> LLM. Nothing downstream
# reports whether the model used the rule, so "fired" would be a fabricated
# metric wearing an authoritative name — in the module that exists to refuse
# exactly that.

def test_new_rule_starts_at_zero(sr):
    rule = sr.add_rule("be brief")["rule"]
    assert rule["inject_count"] == 0
    assert rule["last_injected"] is None


def test_injection_increments_and_stamps(sr):
    sr.add_rule("be brief")
    sr.prompt_block(record=True)
    r = sr.list_rules()[0]
    assert r["inject_count"] == 1
    assert r["last_injected"] is not None


def test_preview_does_not_count_itself(sr):
    """Listing/preview must never inflate the metric it reports."""
    sr.add_rule("be brief")
    for _ in range(5):
        sr.prompt_block()          # record defaults to False
        sr.list_rules()
    assert sr.list_rules()[0]["inject_count"] == 0


def test_count_survives_a_reload(sr):
    """The counter is on disk, not in memory — a restart must not reset it."""
    sr.add_rule("be brief")
    sr.prompt_block(record=True)
    sr.prompt_block(record=True)
    fresh = sr.load()              # re-read from disk, no cached state
    assert fresh["rules"][0]["inject_count"] == 2


def test_migration_backfills_without_resetting(sr):
    """An old store keeps its data and gains the fields at zero."""
    import json
    sr.RULES_PATH.write_text(json.dumps({"schema": 1, "rules": [
        {"id": "old1", "text": "keep me", "added": "2026-07-01T10:00:00+00:00"}]}))
    r = sr.list_rules()[0]
    assert r["text"] == "keep me", "existing rule text must survive migration"
    assert r["added"] == "2026-07-01T10:00:00+00:00", "added date must survive"
    assert r["inject_count"] == 0 and r["last_injected"] is None


def test_no_rules_means_no_write_churn(sr):
    """An empty store must not be rewritten on every turn."""
    sr.prompt_block(record=True)
    assert not sr.RULES_PATH.exists() or sr.load()["rules"] == []


def test_listing_labels_injection_honestly(monkeypatch, tmp_path):
    """The user-facing text must not imply the model 'used' the rule."""
    s = _skill(monkeypatch, tmp_path)
    s.run("add a standing rule: be brief")
    import codec_standing_rules as mod
    mod.prompt_block(record=True)
    out = s.run("show my standing rules")
    assert "sent to the model" in out
    assert "counts INJECTION" in out
    assert "fired" not in out.lower(), "must not claim the rule 'fired'"
