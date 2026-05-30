"""Phase 1 Step 3 §7 — chat-handler step budget tests.

Validates docs/PHASE1-STEP3-DESIGN.md §3:
    - Per-route caps from ~/.codec/config.json (chat=5, voice=5, mcp=None)
    - consume(kind) returns True/False; counts up; emits step_budget_exhausted
      audit event the first time the cap is hit (idempotent: emitted once)
    - warn_now() returns True at limit-1 (drives "1 step remaining" prompt suffix)
    - at_limit() returns True after consume returned False
    - STEP_BUDGET_ENABLED env-var kill switch
    - MCP route returns None (no cap) — _StepBudget.enabled stays False
    - "Tune up before tuning out" — config.json override bumps the cap to 8/10

Tests construct _StepBudget directly. We don't exercise the chat_completion
HTTP route (that needs FastAPI + LLM mocking). The §3 contract is:
"_StepBudget.consume() should return False once over limit and emit one
step_budget_exhausted line per request" — that's testable in isolation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import codec_audit


# Lazy import of codec_dashboard — it pulls in pynput which may be missing
# in the test env. We catch ImportError and skip the whole module if so.
try:
    import codec_dashboard
    _DASH_OK = True
except Exception as _e:
    _DASH_OK = False
    _DASH_ERR = _e


pytestmark = pytest.mark.skipif(
    not _DASH_OK,
    reason=f"codec_dashboard import failed (missing optional dep): {_DASH_ERR if not _DASH_OK else ''}",
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_audit_log(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", log)
    return log


@pytest.fixture
def temp_config(tmp_path, monkeypatch):
    """Redirect CONFIG_PATH to a tmp file so we control what
    _step_budget_for_route() reads on each call.

    B6-P2: _step_budget_for_route moved to codec_chat_pipeline; patch
    there. Also patch codec_dashboard for any test that checks via the
    re-export."""
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(codec_dashboard, "CONFIG_PATH", str(cfg))
    import codec_chat_pipeline
    monkeypatch.setattr(codec_chat_pipeline, "CONFIG_PATH", str(cfg))
    return cfg


def _records(audit_log: Path) -> list[dict]:
    if not audit_log.exists():
        return []
    return [json.loads(l) for l in audit_log.read_text(encoding="utf-8").splitlines() if l.strip()]


def _exhausted(records: list[dict]) -> list[dict]:
    return [r for r in records if r.get("event") == codec_audit.STEP_BUDGET_EXHAUSTED]


# ── _step_budget_for_route ─────────────────────────────────────────────────────

def test_default_chat_cap_is_5(temp_config):
    """No config.json present → chat route default = 5."""
    assert codec_dashboard._step_budget_for_route("chat") == 5


def test_default_voice_cap_is_5(temp_config):
    assert codec_dashboard._step_budget_for_route("voice") == 5


def test_mcp_route_has_no_cap(temp_config):
    """MCP route returns None (no turn budget — SKILL_TIMEOUT_SEC governs)."""
    assert codec_dashboard._step_budget_for_route("mcp") is None


def test_config_override_chat_cap(temp_config):
    """User-supplied config.json:step_budget.chat=8 overrides default."""
    temp_config.write_text(json.dumps({"step_budget": {"chat": 8}}))
    assert codec_dashboard._step_budget_for_route("chat") == 8


def test_config_override_voice_cap_to_10(temp_config):
    """Q3 design directive: bumping to 10 is a single config edit."""
    temp_config.write_text(json.dumps({"step_budget": {"voice": 10}}))
    assert codec_dashboard._step_budget_for_route("voice") == 10


def test_config_invalid_value_falls_back_to_default(temp_config):
    """Negative / non-int values → falls back to default 5."""
    temp_config.write_text(json.dumps({"step_budget": {"chat": -3}}))
    assert codec_dashboard._step_budget_for_route("chat") == 5
    temp_config.write_text(json.dumps({"step_budget": {"chat": "bad"}}))
    assert codec_dashboard._step_budget_for_route("chat") == 5


# ── _StepBudget.consume / warn_now / at_limit ────────────────────────────────

def test_budget_consume_below_limit_returns_true(temp_audit_log, temp_config):
    """consume returns True for steps 1..limit; only step (limit+1) returns False."""
    b = codec_dashboard._StepBudget(route="chat", correlation_id="c0")
    assert b.limit == 5
    assert b.enabled is True
    for i in range(5):
        ok = b.consume(f"step_{i}")
        assert ok is True
        assert b.count == i + 1
    # 6th step exceeds the cap.
    ok = b.consume("step_5")
    assert ok is False
    assert b.at_limit() is True


def test_budget_warn_now_at_limit_minus_1(temp_config):
    """After consuming 4/5 steps, warn_now() == True (drives the prompt suffix)."""
    b = codec_dashboard._StepBudget(route="chat", correlation_id="c0")
    for _ in range(4):
        b.consume("x")
    assert b.warn_now() is True
    # After the 5th, warn_now is no longer True (we're AT limit, not BEFORE).
    b.consume("x")
    assert b.warn_now() is False


def test_budget_emits_exhausted_audit(temp_audit_log, temp_config):
    """Hitting the cap emits exactly one step_budget_exhausted audit line."""
    b = codec_dashboard._StepBudget(route="chat", correlation_id="cid12345")
    for _ in range(6):  # 5 OK + 1 over
        b.consume("llm_call")
    recs = _records(temp_audit_log)
    exh = _exhausted(recs)
    assert len(exh) == 1
    extra = exh[0]["extra"]
    assert extra["budget_type"] == "chat_turn"
    assert extra["limit"] == 5
    assert extra["actual"] == 6
    assert extra["correlation_id"] == "cid12345"
    assert exh[0]["outcome"] == "warning"
    assert exh[0]["level"] == "warning"


def test_budget_exhausted_emit_is_idempotent(temp_audit_log, temp_config):
    """Subsequent consume() calls past the cap don't re-emit the audit line."""
    b = codec_dashboard._StepBudget(route="chat", correlation_id="c0")
    for _ in range(10):  # blow well past the limit
        b.consume("x")
    recs = _records(temp_audit_log)
    exh = _exhausted(recs)
    assert len(exh) == 1, "step_budget_exhausted should emit only once"


# ── Kill switch ───────────────────────────────────────────────────────────────

def test_kill_switch_disables_budget(monkeypatch, temp_audit_log, temp_config):
    """STEP_BUDGET_ENABLED=false → _StepBudget.enabled stays False, consume()
    always returns True (no enforcement), no audit emits."""
    monkeypatch.setenv("STEP_BUDGET_ENABLED", "false")
    b = codec_dashboard._StepBudget(route="chat", correlation_id="c0")
    assert b.enabled is False
    assert b.limit is None
    for _ in range(20):
        assert b.consume("x") is True
    # warn_now / at_limit always False when disabled.
    assert b.warn_now() is False
    assert b.at_limit() is False
    recs = _records(temp_audit_log)
    assert _exhausted(recs) == [], "no audit emit when disabled"


def test_kill_switch_default_enabled(monkeypatch):
    """Default (no env var) — enabled is True."""
    monkeypatch.delenv("STEP_BUDGET_ENABLED", raising=False)
    assert codec_dashboard._step_budget_enabled() is True


def test_kill_switch_off_alias(monkeypatch):
    """Various aliases for "off" all disable: false, 0, no, off."""
    for v in ["false", "0", "no", "off", "FALSE", "Off"]:
        monkeypatch.setenv("STEP_BUDGET_ENABLED", v)
        assert codec_dashboard._step_budget_enabled() is False


# ── MCP route is exempt ───────────────────────────────────────────────────────

def test_mcp_budget_is_disabled_at_construction(temp_audit_log, temp_config):
    """_StepBudget(route='mcp') — limit=None, enabled=False, consume always True."""
    b = codec_dashboard._StepBudget(route="mcp", correlation_id="c0")
    assert b.limit is None
    assert b.enabled is False
    for _ in range(50):
        assert b.consume("x") is True
    assert _exhausted(_records(temp_audit_log)) == []


# ── consume(kind) telemetry ───────────────────────────────────────────────────

def test_exhausted_emit_includes_last_kind_label(temp_audit_log, temp_config):
    """The kind argument to the OVER-cap consume() ends up in extra.kind so
    operators can tell what kind of step blew the budget."""
    b = codec_dashboard._StepBudget(route="chat", correlation_id="c0")
    for _ in range(5):
        b.consume("llm_call")
    b.consume("post_llm_skill_tag")
    recs = _records(temp_audit_log)
    exh = _exhausted(recs)
    assert len(exh) == 1
    assert exh[0]["extra"]["kind"] == "post_llm_skill_tag"


# ── Independent counters per request (no shared state) ────────────────────────

def test_two_budgets_have_independent_counts(temp_audit_log, temp_config):
    """Each request constructs its own _StepBudget — counts don't bleed."""
    b1 = codec_dashboard._StepBudget(route="chat", correlation_id="c1")
    b2 = codec_dashboard._StepBudget(route="chat", correlation_id="c2")
    for _ in range(4):
        b1.consume("x")
    # b2 untouched.
    assert b2.count == 0
    for _ in range(6):
        b2.consume("x")
    # b2 over the limit; b1 still in the warn-now zone.
    assert b1.warn_now() is True
    assert b2.at_limit() is True
    # Each emits exactly once.
    recs = _records(temp_audit_log)
    exh = _exhausted(recs)
    assert len(exh) == 1
    assert exh[0]["extra"]["correlation_id"] == "c2"
