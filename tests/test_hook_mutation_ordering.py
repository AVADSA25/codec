"""Phase 1 Step 2 §9.4 — mutation contract + ordering tests.

Validates:
  - pre_tool can mutate task / context (returns dict)
  - post_tool can mutate result (returns str)
  - chain composition: A's output is B's input
  - PLUGIN_PRIORITY ascending; alphabetical filename tie-break
  - PLUGIN_TOOL_FILTER skips unmatched tools
  - Q11.Q2 tightening: tool_name / transport / agent / correlation_id /
    client_id / operation_id are immutable identity fields — silently
    dropped from a pre_tool dict return, with a warning log.
  - Anything-else returns logged as warning + treated as None.
"""
from __future__ import annotations

import json
import logging
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


# ── Mutation contract — pre_tool ─────────────────────────────────────────────

def test_pre_tool_mutates_task(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "uppercaser.py", """
        def pre_tool(ctx):
            return {"task": ctx.task.upper()}
    """)
    reg.scan()
    seen = []
    cid = secrets.token_hex(6)
    codec_hooks.run_with_hooks(
        tool_name="weather", task="paris", transport="dispatch",
        correlation_id=cid,
        invoke=lambda t, c: seen.append((t, c)) or "ok",
    )
    assert seen == [("PARIS", "")]


def test_pre_tool_mutates_context(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "ctx_inj.py", """
        def pre_tool(ctx):
            return {"context": "INJECTED"}
    """)
    reg.scan()
    seen = []
    cid = secrets.token_hex(6)
    codec_hooks.run_with_hooks(
        tool_name="weather", task="paris", context="orig",
        transport="dispatch", correlation_id=cid,
        invoke=lambda t, c: seen.append((t, c)) or "ok",
    )
    assert seen == [("paris", "INJECTED")]


def test_pre_tool_no_mutation_when_returning_none(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "noop.py", """
        def pre_tool(ctx):
            return None
    """)
    reg.scan()
    seen = []
    cid = secrets.token_hex(6)
    codec_hooks.run_with_hooks(
        tool_name="weather", task="orig_task", context="orig_ctx",
        transport="dispatch", correlation_id=cid,
        invoke=lambda t, c: seen.append((t, c)) or "ok",
    )
    assert seen == [("orig_task", "orig_ctx")]


# ── Mutation contract — post_tool ────────────────────────────────────────────

def test_post_tool_mutates_result(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "wrap.py", """
        def post_tool(ctx, result):
            return f"[wrapped] {result}"
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    out = codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=lambda t, c: "raw",
    )
    assert out == "[wrapped] raw"


# ── Chain composition + ordering ─────────────────────────────────────────────

def test_two_pre_tool_chain_in_priority_order(temp_registry, temp_audit_log):
    """Plugin A (priority 10) mutates first; plugin B (priority 20) sees A's output."""
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "a_low_priority.py", """
        PLUGIN_PRIORITY = 10
        def pre_tool(ctx):
            return {"task": ctx.task + "_A"}
    """)
    _write(plugins_dir, "b_high_priority.py", """
        PLUGIN_PRIORITY = 20
        def pre_tool(ctx):
            return {"task": ctx.task + "_B"}
    """)
    reg.scan()
    seen = []
    cid = secrets.token_hex(6)
    codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=lambda t, c: seen.append(t) or "ok",
    )
    # A runs first (lower priority), then B sees A's output.
    assert seen == ["x_A_B"]


def test_priority_tie_broken_alphabetically(temp_registry, temp_audit_log):
    """Same priority → alphabetical filename order."""
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "alpha.py", """
        def pre_tool(ctx):
            return {"task": ctx.task + "_alpha"}
    """)
    _write(plugins_dir, "bravo.py", """
        def pre_tool(ctx):
            return {"task": ctx.task + "_bravo"}
    """)
    reg.scan()
    seen = []
    cid = secrets.token_hex(6)
    codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=lambda t, c: seen.append(t) or "ok",
    )
    assert seen == ["x_alpha_bravo"]


def test_default_priority_100(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "default.py", """
        def pre_tool(ctx): return None
    """)
    reg.scan()
    assert reg.all()[0].priority == 100


# ── Bad return shapes ────────────────────────────────────────────────────────

def test_invalid_pre_tool_return_logs_warning_treats_as_none(temp_registry, temp_audit_log, caplog):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "buggy.py", """
        def pre_tool(ctx):
            return False
    """)
    reg.scan()
    seen = []
    cid = secrets.token_hex(6)
    with caplog.at_level(logging.WARNING, logger="codec_hooks"):
        codec_hooks.run_with_hooks(
            tool_name="weather", task="orig", transport="dispatch",
            correlation_id=cid,
            invoke=lambda t, c: seen.append(t) or "ok",
        )
    # Task unchanged.
    assert seen == [("orig")]
    # Warning message present.
    assert any("buggy" in r.message and "pre_tool" in r.message for r in caplog.records)


def test_invalid_post_tool_return_logs_warning_treats_as_none(temp_registry, temp_audit_log, caplog):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "buggy_post.py", """
        def post_tool(ctx, result):
            return [42]
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    with caplog.at_level(logging.WARNING, logger="codec_hooks"):
        out = codec_hooks.run_with_hooks(
            tool_name="weather", task="x", transport="dispatch",
            correlation_id=cid, invoke=lambda t, c: "real",
        )
    # Result unchanged.
    assert out == "real"
    assert any("buggy_post" in r.message and "post_tool" in r.message for r in caplog.records)


# ── Tool filter ──────────────────────────────────────────────────────────────

def test_plugin_tool_filter_skips_unmatched_tools(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "weather_only.py", """
        PLUGIN_TOOL_FILTER = ["weather"]
        def pre_tool(ctx):
            return {"task": ctx.task + "_filtered"}
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    # Tool name = "weather" → filter matches → mutation applies.
    seen_w = []
    codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid,
        invoke=lambda t, c: seen_w.append(t) or "ok",
    )
    assert seen_w == ["x_filtered"]
    # Tool name = "notes" → filter does NOT match → no mutation.
    seen_n = []
    codec_hooks.run_with_hooks(
        tool_name="notes", task="x", transport="dispatch",
        correlation_id=cid,
        invoke=lambda t, c: seen_n.append(t) or "ok",
    )
    assert seen_n == ["x"]


# ── §11 Q2 immutable identity fields ─────────────────────────────────────────

def test_pre_tool_immutable_field_tool_name_dropped(temp_registry, temp_audit_log, caplog):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "rewriter.py", """
        def pre_tool(ctx):
            # Try to silently route the call to a different tool.
            # MUST be dropped per Q2 tightening; mutated task should still apply.
            return {"tool_name": "notes", "task": ctx.task + "_mutated"}
    """)
    reg.scan()
    seen = []
    cid = secrets.token_hex(6)
    with caplog.at_level(logging.WARNING, logger="codec_hooks"):
        codec_hooks.run_with_hooks(
            tool_name="weather", task="x", transport="dispatch",
            correlation_id=cid,
            invoke=lambda t, c: seen.append(t) or "ok",
        )
    # Task DID mutate.
    assert seen == ["x_mutated"]
    # Warning logged for tool_name.
    assert any("immutable field 'tool_name'" in r.message for r in caplog.records)


def test_pre_tool_immutable_fields_transport_agent_correlation_dropped(
        temp_registry, temp_audit_log, caplog):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "evil.py", """
        def pre_tool(ctx):
            return {
                "transport": "evil",
                "agent": "spoofed",
                "correlation_id": "deadbeef0000",
                "client_id": "evil-client",
                "operation_id": "evil-op",
                "task": "should still mutate",
            }
    """)
    reg.scan()
    seen = []
    cid = secrets.token_hex(6)
    with caplog.at_level(logging.WARNING, logger="codec_hooks"):
        codec_hooks.run_with_hooks(
            tool_name="weather", task="orig", transport="dispatch",
            correlation_id=cid,
            invoke=lambda t, c: seen.append(t) or "ok",
        )
    # Only `task` survived.
    assert seen == ["should still mutate"]
    # All five identity fields generated warnings.
    msgs = " ".join(r.message for r in caplog.records)
    for field in ("transport", "agent", "correlation_id", "client_id", "operation_id"):
        assert f"immutable field '{field}'" in msgs, f"missing warning for {field}"
