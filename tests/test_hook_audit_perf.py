"""Phase 1 Step 2 §9.5 — audit emission + performance + concurrent stress.

Validates:
  - hook_fired audit emitted per fire (success or veto).
  - hook_fired carries plugin_name, hook_name, tool_name, duration_ms.
  - hook_fired inherits the wrapping operation's correlation_id.
  - §11 Q4 hook_error event fires when a plugin raises in pre_tool /
    post_tool. event=hook_error, outcome=error, level=WARNING (not
    "error"), error_type + error truncated to _PREVIEW_MAX, plugin_name
    + hook_name in extra. Operation continues; invoke still runs.
  - hook_error and hook_fired are split — never both for same call.
  - Performance: <1 ms/call with zero plugins, <5 ms/call with five
    plugins (CI 5× looser).
  - Concurrent stress: 10×100×5-hook → no JSON corruption, all entries
    parseable, no dropped writes.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor
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


_CI = bool(os.environ.get("CI"))
_BUDGET_ZERO_HOOKS_MS = 1.0 if not _CI else 5.0
_BUDGET_FIVE_HOOKS_MS = 5.0 if not _CI else 25.0
_BUDGET_CONCURRENT_MS = 10.0 if not _CI else 50.0


# ── hook_fired audit shape ───────────────────────────────────────────────────

def test_hook_fired_audit_emitted_per_call(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "p.py", """
        def pre_tool(ctx): return None
        def post_tool(ctx, result): return None
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=lambda t, c: "ok",
    )
    fires = [r for r in _records(temp_audit_log) if r.get("event") == "hook_fired"]
    # Two fires expected: pre_tool + post_tool, both from plugin "p".
    assert len(fires) == 2
    hooks = sorted(r["extra"]["hook_name"] for r in fires)
    assert hooks == ["post_tool", "pre_tool"]


def test_hook_fired_carries_plugin_name_hook_name_tool_name_duration(
        temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "tracer.py", """
        def pre_tool(ctx): return None
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=lambda t, c: "ok",
    )
    rec = [r for r in _records(temp_audit_log)
           if r.get("event") == "hook_fired"][0]
    assert rec["extra"]["plugin_name"] == "tracer"
    assert rec["extra"]["hook_name"] == "pre_tool"
    assert rec["extra"]["tool_name"] == "weather"
    assert isinstance(rec["duration_ms"], (int, float))
    assert rec["duration_ms"] >= 0
    assert rec["outcome"] == "ok"
    assert rec["level"] == "info"
    assert rec["transport"] == "dispatch"


def test_hook_fired_inherits_correlation_id(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "p.py", """
        def pre_tool(ctx): return None
    """)
    reg.scan()
    cid = "feedface1234"
    codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=lambda t, c: "ok",
    )
    rec = [r for r in _records(temp_audit_log)
           if r.get("event") == "hook_fired"][0]
    assert rec["extra"]["correlation_id"] == cid


def test_hook_fired_tool_name_null_for_operation_hooks(
        temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "op.py", """
        def on_operation_start(ctx): pass
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    codec_hooks.emit_operation_start(
        operation_id="op1", transport="voice", correlation_id=cid)
    rec = [r for r in _records(temp_audit_log)
           if r.get("event") == "hook_fired"][0]
    # tool_name should be None for operation hooks.
    assert rec["extra"]["tool_name"] is None
    assert rec["extra"]["hook_name"] == "on_operation_start"


# ── §11 Q4 hook_error event ──────────────────────────────────────────────────

def test_hook_error_emitted_when_plugin_raises(temp_registry, temp_audit_log):
    """Q4: plugin raises in pre_tool. Verify hook_error envelope per §7.5."""
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "buggy.py", """
        def pre_tool(ctx):
            raise KeyError("missing key 'x'")
    """)
    reg.scan()
    invoke_calls = []
    cid = secrets.token_hex(6)
    result = codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid,
        invoke=lambda t, c: invoke_calls.append(1) or "ok",
    )
    # Operation continues, invoke runs.
    assert result == "ok"
    assert invoke_calls == [1]

    recs = _records(temp_audit_log)
    errs = [r for r in recs if r.get("event") == "hook_error"]
    assert len(errs) == 1
    e = errs[0]
    assert e["outcome"] == "error"
    assert e["level"] == "warning"   # NOT "error" per §7.5
    assert e["error_type"] == "KeyError"
    assert "x" in e["error"]   # message captured
    assert e["extra"]["plugin_name"] == "buggy"
    assert e["extra"]["hook_name"] == "pre_tool"
    assert e["extra"]["correlation_id"] == cid


def test_hook_error_does_NOT_emit_hook_fired_for_same_call(
        temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "buggy.py", """
        def pre_tool(ctx):
            raise RuntimeError("boom")
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=lambda t, c: "ok",
    )
    recs = _records(temp_audit_log)
    fires = [r for r in recs if r.get("event") == "hook_fired"
             and r.get("extra", {}).get("plugin_name") == "buggy"]
    errs = [r for r in recs if r.get("event") == "hook_error"
            and r.get("extra", {}).get("plugin_name") == "buggy"]
    assert fires == []
    assert len(errs) == 1


def test_hook_error_truncates_long_error_message_at_preview_max(
        temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "verbose.py", """
        def pre_tool(ctx):
            raise ValueError('y' * 1000)
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=lambda t, c: "ok",
    )
    rec = [r for r in _records(temp_audit_log)
           if r.get("event") == "hook_error"][0]
    assert len(rec["error"]) <= codec_audit._PREVIEW_MAX
    assert rec["error"] == "y" * codec_audit._PREVIEW_MAX


def test_hook_error_inherits_correlation_id(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "buggy.py", """
        def post_tool(ctx, result):
            raise ValueError("err in post")
    """)
    reg.scan()
    cid = "abad1dea0000"
    codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=lambda t, c: "ok",
    )
    rec = [r for r in _records(temp_audit_log)
           if r.get("event") == "hook_error"][0]
    assert rec["extra"]["correlation_id"] == cid


def test_hook_error_level_is_warning_not_error(temp_registry, temp_audit_log):
    """Q4 tightening: operation succeeded; only the plugin failed.
    A buggy plugin must NOT inflate audit_report's error-rate metric."""
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "buggy.py", """
        def pre_tool(ctx):
            raise IOError("disk full")
    """)
    reg.scan()
    cid = secrets.token_hex(6)
    codec_hooks.run_with_hooks(
        tool_name="weather", task="x", transport="dispatch",
        correlation_id=cid, invoke=lambda t, c: "ok",
    )
    rec = [r for r in _records(temp_audit_log)
           if r.get("event") == "hook_error"][0]
    assert rec["level"] == "warning"
    assert rec["level"] != "error"


# ── Performance ──────────────────────────────────────────────────────────────

def test_hook_overhead_under_1ms_with_zero_hooks(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    reg.scan()  # no plugins registered
    n = 1000
    cid = secrets.token_hex(6)
    t0 = time.monotonic()
    for _ in range(n):
        codec_hooks.run_with_hooks(
            tool_name="weather", task="x", transport="dispatch",
            correlation_id=cid, invoke=lambda t, c: "ok",
        )
    elapsed = time.monotonic() - t0
    avg_ms = (elapsed / n) * 1000.0
    assert avg_ms < _BUDGET_ZERO_HOOKS_MS, (
        f"run_with_hooks overhead with zero hooks: {avg_ms:.3f}ms/call "
        f"(budget {_BUDGET_ZERO_HOOKS_MS}ms)"
    )


def test_hook_overhead_under_5ms_with_5_hooks(temp_registry, temp_audit_log):
    plugins_dir, reg = temp_registry
    # 5 trivial hooks (pre, post, error, op_start, op_end), all `pass`.
    _write(plugins_dir, "all_five.py", """
        def pre_tool(ctx): return None
        def post_tool(ctx, result): return None
        def on_error(ctx, exc): pass
        def on_operation_start(ctx): pass
        def on_operation_end(ctx): pass
    """)
    reg.scan()
    n = 500
    cid = secrets.token_hex(6)
    t0 = time.monotonic()
    for _ in range(n):
        codec_hooks.run_with_hooks(
            tool_name="weather", task="x", transport="dispatch",
            correlation_id=cid, invoke=lambda t, c: "ok",
        )
    elapsed = time.monotonic() - t0
    avg_ms = (elapsed / n) * 1000.0
    # NOTE: only pre_tool + post_tool fire on a tool-call path, so this is
    # really 2 hook fires per invocation (op_start/op_end fire only via
    # emit_operation_*). The 5-hook label refers to the count of registered
    # hooks, not the count fired per call. Budget is conservative.
    assert avg_ms < _BUDGET_FIVE_HOOKS_MS, (
        f"run_with_hooks overhead with 5 hooks: {avg_ms:.3f}ms/call "
        f"(budget {_BUDGET_FIVE_HOOKS_MS}ms)"
    )


# ── Concurrent stress ────────────────────────────────────────────────────────

def test_hook_concurrent_no_audit_corruption(temp_registry, temp_audit_log):
    """10 threads × 100 invocations × 5 hooks → no JSON corruption,
    all entries parseable, no dropped writes."""
    plugins_dir, reg = temp_registry
    _write(plugins_dir, "all_five.py", """
        def pre_tool(ctx): return None
        def post_tool(ctx, result): return None
        def on_error(ctx, exc): pass
        def on_operation_start(ctx): pass
        def on_operation_end(ctx): pass
    """)
    reg.scan()

    N_THREADS = 10
    N_PER_THREAD = 100

    def worker(thread_id: int):
        cid = f"{thread_id:08x}{0:04x}"   # deterministic 12-char hex
        for _ in range(N_PER_THREAD):
            codec_hooks.run_with_hooks(
                tool_name="weather", task="x", transport="dispatch",
                correlation_id=cid, invoke=lambda t, c: "ok",
            )

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=N_THREADS) as ex:
        list(ex.map(worker, range(N_THREADS)))
    elapsed = time.monotonic() - t0

    # Sanity: every line in audit log is valid JSON (no torn writes).
    text = temp_audit_log.read_text(encoding="utf-8")
    lines = [l for l in text.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise AssertionError(f"corrupt JSON at line {i}: {e!r}: {line[:200]!r}")
        for k in ("ts", "schema", "event", "source", "outcome"):
            assert k in obj, f"line {i} missing required field {k}: {line[:200]}"

    # Each invocation emits 2 hook_fired entries (pre_tool + post_tool).
    # Plus there might be other emits from tool_call/tool_result if any
    # hooks did them — none here. So we expect exactly 2*N entries.
    fires = [json.loads(l) for l in lines if "hook_fired" in l]
    assert len(fires) == N_THREADS * N_PER_THREAD * 2, (
        f"expected {N_THREADS * N_PER_THREAD * 2} hook_fired entries, "
        f"got {len(fires)}")

    # Latency budget check — each invocation involves 2 audit writes
    # under contention. The Step 1 audit perf budget is 2.5ms/write under
    # 10-way contention; with 2 writes/call we expect ~5ms/call. Add
    # plugin-fn overhead. Budget: 10ms/call (conservative).
    avg_ms = (elapsed / (N_THREADS * N_PER_THREAD)) * 1000.0
    assert avg_ms < _BUDGET_CONCURRENT_MS, (
        f"concurrent run_with_hooks: {avg_ms:.3f}ms/call "
        f"(budget {_BUDGET_CONCURRENT_MS}ms)"
    )
