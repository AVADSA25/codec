"""Phase 1 Step 2 §9.2 — hook lifecycle tests.

Validates pre/post/on_error firing order, on_operation_start/end naming
(per §11 Q6 amendment — NOT the legacy on_session_* names).

Tests redirect codec_audit._AUDIT_LOG to a temp file so the real
~/.codec/audit.log is never touched, AND swap codec_hooks._registry to
a fresh PluginRegistry pointed at a tmp dir.
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


# ── pre_tool / post_tool / on_error ────────────────────────────────────────

def test_pre_tool_fires_before_invoke(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "tracer.py", """
        SEQUENCE = []
        def pre_tool(ctx):
            SEQUENCE.append('pre_tool')
            return None
    """)
    reg.scan()

    seen_during_invoke = []
    def _invoke(t, c):
        # By the time invoke runs, pre_tool must already have fired.
        import sys
        mod = sys.modules.get('codec_plugin_tracer')
        seen_during_invoke.append(list(mod.SEQUENCE))
        return "ok"

    cid = secrets.token_hex(6)
    result = codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=_invoke,
    )
    assert result == "ok"
    assert seen_during_invoke == [["pre_tool"]]


def test_post_tool_fires_after_invoke(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "tracer.py", """
        SEQUENCE = []
        def post_tool(ctx, result):
            SEQUENCE.append(('post_tool', result))
            return None
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=lambda t, c: "the result",
    )
    import sys
    mod = sys.modules['codec_plugin_tracer']
    assert mod.SEQUENCE == [('post_tool', 'the result')]


def test_post_tool_sees_invoke_result(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "tagger.py", """
        def post_tool(ctx, result):
            return f"[tagged] {result}"
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    out = codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=lambda t, c: "raw",
    )
    assert out == "[tagged] raw"


def test_on_error_fires_when_invoke_raises(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "err_tracer.py", """
        ERRORS = []
        def on_error(ctx, exc):
            ERRORS.append((type(exc).__name__, str(exc)))
    """)
    reg.scan()
    def _invoke(t, c):
        raise ValueError("boom")
    cid = secrets.token_hex(6)
    with pytest.raises(ValueError):
        codec_hooks.run_with_hooks(
            tool_name="weather", task="x", transport="dispatch",
            correlation_id=cid, invoke=_invoke,
        )
    import sys
    mod = sys.modules['codec_plugin_err_tracer']
    assert mod.ERRORS == [("ValueError", "boom")]


def test_on_error_does_not_fire_on_success(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "err_tracer.py", """
        ERRORS = []
        def on_error(ctx, exc):
            ERRORS.append(exc)
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=lambda t, c: "ok",
    )
    # No on_error fire = no hook_fired audit with hook_name="on_error".
    # (And module is never even imported, since get_fn is only called
    # on hook trigger.)
    recs = _records(temp_audit_log)
    on_error_fires = [r for r in recs
                      if r.get("event") == "hook_fired"
                      and r.get("extra", {}).get("hook_name") == "on_error"]
    assert on_error_fires == []


# ── on_operation_start / on_operation_end (Q6 rename) ────────────────────────

def test_on_operation_start_fires_with_operation_id(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "op_tracer.py", """
        STARTS = []
        def on_operation_start(ctx):
            STARTS.append((ctx.transport, ctx.operation_id, ctx.correlation_id))
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    codec_hooks.emit_operation_start(
        operation_id="voice_2026-04-30",
        transport="voice",
        correlation_id=cid,
    )
    import sys
    mod = sys.modules['codec_plugin_op_tracer']
    assert mod.STARTS == [("voice", "voice_2026-04-30", cid)]


def test_on_operation_end_fires_with_duration_and_outcome(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "op_tracer.py", """
        ENDS = []
        def on_operation_end(ctx):
            ENDS.append((ctx.operation_id, ctx.duration_ms, ctx.outcome))
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    codec_hooks.emit_operation_end(
        operation_id="op-123", transport="crew",
        correlation_id=cid, duration_ms=1234.5, outcome="error",
    )
    import sys
    mod = sys.modules['codec_plugin_op_tracer']
    assert mod.ENDS == [("op-123", 1234.5, "error")]


def test_legacy_on_session_names_are_NOT_recognized(temp_registry, temp_audit_log):
    """Per §11 Q6 rename: on_session_start/on_session_end are NOT in the
    discovery hook set. A plugin defining only the legacy names is treated
    as having no lifecycle hooks → not registered."""
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "old_school.py", """
        def on_session_start(ctx): pass
        def on_session_end(ctx): pass
    """)
    reg.scan()
    assert reg.all() == []


def test_operation_hooks_do_not_fire_for_individual_tool_calls(temp_registry, temp_audit_log):
    """run_with_hooks (which is per-tool-call) MUST NOT fire on_operation_*.
    Those only fire from emit_operation_start / emit_operation_end calls."""
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "op_tracer.py", """
        STARTS = []
        ENDS = []
        def on_operation_start(ctx): STARTS.append(1)
        def on_operation_end(ctx): ENDS.append(1)
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=lambda t, c: "ok",
    )
    # No on_operation_* fire = no corresponding hook_fired audit. (Module
    # is never imported, since get_fn is only called on hook trigger.)
    recs = _records(temp_audit_log)
    op_fires = [r for r in recs
                if r.get("event") == "hook_fired"
                and r.get("extra", {}).get("hook_name", "").startswith("on_operation_")]
    assert op_fires == []


def test_pre_tool_after_validation_in_mcp_path(temp_registry, temp_audit_log):
    """The MCP path validates inputs BEFORE calling run_with_hooks. So by
    the time a plugin's pre_tool sees the ctx, task and context are
    already-validated strings (not None, not stringified objects).

    We don't actually go through codec_mcp here — we verify the contract
    that run_with_hooks accepts strings, never preprocesses them, and
    passes them to the invoke closure unchanged.
    """
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "shape_check.py", """
        SAW = []
        def pre_tool(ctx):
            SAW.append((type(ctx.task).__name__, len(ctx.task)))
            return None
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    long_task = "x" * 4999  # under MCP's 5000-char cap
    codec_hooks.run_with_hooks(
        tool_name="weather", task=long_task, transport="stdio",
        correlation_id=cid, invoke=lambda t, c: "ok",
    )
    import sys
    mod = sys.modules['codec_plugin_shape_check']
    assert mod.SAW == [("str", 4999)]
