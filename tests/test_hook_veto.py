"""Phase 1 Step 2 §9.3 — veto semantics tests.

Validates HookVeto behaviour from design §4: returned (not raised),
first-veto-wins, deterministic veto string to caller, tool_vetoed
audit emit, post_tool can't veto, on_error can't recover.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import codec_audit
import codec_hooks


@pytest.fixture
def temp_audit_log(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    tmp.close()
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", Path(tmp.name))
    yield Path(tmp.name)
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


@pytest.fixture
def temp_registry(tmp_path, monkeypatch):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    fresh = codec_hooks.PluginRegistry(str(plugins_dir))
    monkeypatch.setattr(codec_hooks, "_registry", fresh)
    return plugins_dir, fresh


def _write(plugins_dir: Path, name: str, source: str) -> Path:
    fpath = plugins_dir / name
    fpath.write_text(textwrap.dedent(source).strip() + "\n", encoding="utf-8")
    return fpath


def _records(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ── Veto behaviour ───────────────────────────────────────────────────────────

def test_pre_tool_returning_hookveto_skips_invoke(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "deny.py", """
        from codec_hooks import HookVeto
        def pre_tool(ctx):
            return HookVeto(reason="denied for testing")
    """)
    reg.scan()
    invoke_calls = []
    cid = secrets.token_hex(6)
    result = codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid,
        invoke=lambda t, c: invoke_calls.append(1) or "should not see this",
    )
    assert isinstance(result, codec_hooks.HookVeto)
    assert invoke_calls == []
    assert result.reason == "denied for testing"
    assert result.plugin_name == "deny"   # auto-stamped


def test_pre_tool_veto_short_circuits_remaining_pre_hooks(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    # Two plugins. First (priority 10) vetoes; second (priority 20) must NOT fire.
    _write(plugins_dir, "first.py", """
        PLUGIN_PRIORITY = 10
        from codec_hooks import HookVeto
        def pre_tool(ctx):
            return HookVeto(reason="first wins")
    """)
    _write(plugins_dir, "second.py", """
        PLUGIN_PRIORITY = 20
        FIRED = []
        def pre_tool(ctx):
            FIRED.append(1)
            return None
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=lambda t, c: "ok",
    )
    # The second plugin must never have been imported (its pre_tool wasn't called).
    recs = _records(temp_audit_log)
    fires_by_plugin = [r.get("extra", {}).get("plugin_name")
                       for r in recs if r.get("event") == "hook_fired"]
    assert "first" in fires_by_plugin
    assert "second" not in fires_by_plugin


def test_pre_tool_veto_emits_tool_vetoed_audit(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "denylist.py", """
        from codec_hooks import HookVeto
        def pre_tool(ctx):
            return HookVeto(reason="shell_execute disabled by deny_list plugin")
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    codec_hooks.run_with_hooks(
        tool_name="shell_execute", task="rm -rf /",
        transport="dispatch", correlation_id=cid,
        invoke=lambda t, c: "should not run",
    )
    recs = _records(temp_audit_log)
    vetoed = [r for r in recs if r.get("event") == "tool_vetoed"]
    assert len(vetoed) == 1
    rec = vetoed[0]
    assert rec["outcome"] == "denied"
    assert rec["level"] == "warning"
    assert rec["tool"] == "shell_execute"
    assert rec["extra"]["correlation_id"] == cid
    assert rec["extra"]["plugin_name"] == "denylist"
    assert "shell_execute disabled" in rec["extra"]["veto_reason"]


def test_first_veto_wins_in_priority_chain(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "p1.py", """
        PLUGIN_PRIORITY = 10
        from codec_hooks import HookVeto
        def pre_tool(ctx):
            return HookVeto(reason="from p1")
    """)
    _write(plugins_dir, "p2.py", """
        PLUGIN_PRIORITY = 20
        from codec_hooks import HookVeto
        def pre_tool(ctx):
            return HookVeto(reason="from p2")
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    result = codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=lambda t, c: "x",
    )
    # Only p1's veto should be observed.
    assert isinstance(result, codec_hooks.HookVeto)
    assert result.plugin_name == "p1"
    assert result.reason == "from p1"


def test_post_tool_returning_hookveto_is_logged_and_ignored(temp_registry, temp_audit_log):
    """post_tool cannot veto. If a plugin returns HookVeto from post_tool,
    the wrapper logs a warning and treats it as None (no mutation).
    """
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "weird.py", """
        from codec_hooks import HookVeto
        def post_tool(ctx, result):
            return HookVeto(reason="post_tool can't veto")
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    result = codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=lambda t, c: "real result",
    )
    # Result is unchanged (post_tool's veto is ignored).
    assert result == "real result"
    # No tool_vetoed emit (vetos can only come from pre_tool).
    recs = _records(temp_audit_log)
    vetoed = [r for r in recs if r.get("event") == "tool_vetoed"]
    assert vetoed == []


def test_on_error_return_value_is_ignored(temp_registry, temp_audit_log):
    """on_error is observe-only — return value is ignored, original
    exception still surfaces."""
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "rescuer.py", """
        def on_error(ctx, exc):
            # Try to "recover" — should be ignored.
            return "recovered!"
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    with pytest.raises(ValueError, match="boom"):
        codec_hooks.run_with_hooks(
            tool_name="weather", task="x", transport="dispatch",
            correlation_id=cid,
            invoke=lambda t, c: (_ for _ in ()).throw(ValueError("boom")),
        )


def test_veto_returns_deterministic_string_via_caller(temp_registry, temp_audit_log):
    """Caller receives HookVeto sentinel; downstream code (e.g.
    codec_dispatch.run_skill) translates it to the canonical string.
    Verify the sentinel carries the data the canonical string is built from."""
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "block.py", """
        from codec_hooks import HookVeto
        def pre_tool(ctx):
            return HookVeto(reason="just because")
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    result = codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=lambda t, c: "ok",
    )
    canonical = (f"Skill '{'weather'}' was vetoed by plugin "
                 f"'{result.plugin_name}': {result.reason}")
    assert canonical == "Skill 'weather' was vetoed by plugin 'block': just because"
