"""Tests for codec_hooks plugin trust model (D-18 closure).

Closes audit finding D-18 (MEDIUM) — plugins have no privilege isolation.
PR-2F gates plugin loading on:
  1. SHA-256 in `~/.codec/plugins.allowlist` (operator-managed).
  2. AST check via `codec_config.is_dangerous_skill_code` for files not
     in the allowlist (refused; AST result included in audit emit for
     forensic clarity).
And wraps every hook fire in a daemon thread with a hard timeout
(default 500ms, configurable via `plugin_hook_timeout_ms`).

Reference: docs/audits/PHASE-1-SECURITY.md finding D-18.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


# ── Per-test isolation: tmp plugins dir + tmp allowlist ────────────────────


@pytest.fixture
def plugin_env(tmp_path, monkeypatch):
    """Isolated plugins dir + allowlist file per test. Returns
    (plugins_dir, allowlist_path, registry) where registry is a fresh
    PluginRegistry pointing at the tmp dir. The registry derives its
    allowlist path from `plugins_dir`'s parent (PR-2F design), so the
    fixture doesn't need to monkeypatch module-level constants —
    creating the registry with the tmp plugins_dir is sufficient."""
    import codec_hooks
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    # Allowlist derives from plugins_dir's parent → `<tmp_path>/plugins.allowlist`
    allowlist_path = tmp_path / "plugins.allowlist"

    reg = codec_hooks.PluginRegistry(str(plugins_dir), str(allowlist_path))
    return plugins_dir, allowlist_path, reg, codec_hooks


def _write_plugin(plugins_dir: Path, name: str, body: str):
    """Helper: write a plugin file and return its path."""
    p = plugins_dir / name
    p.write_text(body, encoding="utf-8")
    return p


def _allowlist_entry(plugins_dir: Path, fname: str) -> dict:
    """Compute the SHA-256 of a plugin file and build an allowlist entry."""
    import hashlib
    h = hashlib.sha256((plugins_dir / fname).read_bytes()).hexdigest()
    return {
        "sha256": h,
        "approved_at": "2026-05-18T00:00:00",
        "approved_by": "test",
    }


_BENIGN_PLUGIN = '''"""benign plugin"""
PLUGIN_NAME = "{name}"
def pre_tool(ctx):
    return None
'''

_DANGEROUS_PLUGIN = '''"""dangerous plugin — uses subprocess"""
PLUGIN_NAME = "{name}"
import subprocess
def pre_tool(ctx):
    subprocess.run(["ls"])
    return None
'''


# ── D-18 (a): Plugin AST + allowlist gate at load ─────────────────────────


def test_plugin_load_blocked_not_in_allowlist(plugin_env, monkeypatch):
    """Plugin with no allowlist entry must be refused at load + emit
    `plugin_load_blocked` with reason=`not_in_allowlist`."""
    plugins_dir, allowlist_path, reg, codec_hooks = plugin_env
    _write_plugin(plugins_dir, "test_unauth.py",
                   _BENIGN_PLUGIN.format(name="test_unauth"))
    # Empty allowlist
    allowlist_path.write_text("{}")

    captured = []
    monkeypatch.setattr(codec_hooks, "_log_event",
                         lambda *args, **kw: captured.append({"args": args, "kw": kw}))

    reg.scan()
    plugins = reg.all()
    assert len(plugins) == 1
    fn = reg.get_fn(plugins[0], "pre_tool")
    assert fn is None, "Plugin without allowlist entry must NOT load"

    matches = [c for c in captured
               if c["args"] and c["args"][0] == "plugin_load_blocked"]
    assert len(matches) >= 1, f"Expected plugin_load_blocked emit; got {captured!r}"
    assert matches[0]["kw"].get("extra", {}).get("reason") == "not_in_allowlist"


def test_plugin_load_succeeds_with_allowlist_entry(plugin_env):
    """Plugin with matching hash in allowlist must load (skip AST check)."""
    plugins_dir, allowlist_path, reg, _ = plugin_env
    _write_plugin(plugins_dir, "test_ok.py",
                   _BENIGN_PLUGIN.format(name="test_ok"))
    allowlist = {"test_ok.py": _allowlist_entry(plugins_dir, "test_ok.py")}
    allowlist_path.write_text(json.dumps(allowlist))

    reg.scan()
    plugins = reg.all()
    assert len(plugins) == 1
    fn = reg.get_fn(plugins[0], "pre_tool")
    assert fn is not None, "Plugin with valid allowlist entry must load"
    assert callable(fn)


def test_plugin_load_refused_when_hash_mismatches(plugin_env, monkeypatch):
    """Allowlist has hash A; file content modified → hash B; must refuse
    + emit `plugin_load_blocked` with reason=`hash_mismatch`."""
    plugins_dir, allowlist_path, reg, codec_hooks = plugin_env
    _write_plugin(plugins_dir, "test_mismatch.py",
                   _BENIGN_PLUGIN.format(name="test_mismatch"))
    # Allowlist contains a BOGUS hash (not the file's actual hash)
    allowlist = {
        "test_mismatch.py": {
            "sha256": "0" * 64,
            "approved_at": "2026-01-01T00:00:00",
            "approved_by": "test",
        }
    }
    allowlist_path.write_text(json.dumps(allowlist))

    captured = []
    monkeypatch.setattr(codec_hooks, "_log_event",
                         lambda *args, **kw: captured.append({"args": args, "kw": kw}))

    reg.scan()
    plugins = reg.all()
    fn = reg.get_fn(plugins[0], "pre_tool")
    assert fn is None

    matches = [c for c in captured
               if c["args"] and c["args"][0] == "plugin_load_blocked"]
    assert matches, "Expected plugin_load_blocked on hash mismatch"
    assert matches[0]["kw"].get("extra", {}).get("reason") == "hash_mismatch"


def test_plugin_load_blocked_emits_ast_detail_when_dangerous(plugin_env, monkeypatch):
    """When a plugin is NOT in the allowlist AND its AST is dangerous,
    the `plugin_load_blocked` audit must include the specific AST reason
    in `detail`. Forensic clarity — operator sees both signals."""
    plugins_dir, allowlist_path, reg, codec_hooks = plugin_env
    _write_plugin(plugins_dir, "test_dangerous.py",
                   _DANGEROUS_PLUGIN.format(name="test_dangerous"))
    allowlist_path.write_text("{}")

    captured = []
    monkeypatch.setattr(codec_hooks, "_log_event",
                         lambda *args, **kw: captured.append({"args": args, "kw": kw}))

    reg.scan()
    plugins = reg.all()
    fn = reg.get_fn(plugins[0], "pre_tool")
    assert fn is None

    matches = [c for c in captured
               if c["args"] and c["args"][0] == "plugin_load_blocked"]
    assert matches
    extra = matches[0]["kw"].get("extra", {})
    assert extra.get("reason") == "not_in_allowlist"
    # Detail should mention the AST reason (subprocess.run or import subprocess)
    detail = extra.get("detail", "")
    assert "ast_dangerous" in detail
    assert "subprocess" in detail


# ── D-18 (b): SHA-256 allowlist + grandfather migration ────────────────────


def test_initial_migration_grandfathers_existing_plugins(plugin_env):
    """First scan with no allowlist file + plugins in dir → auto-seed
    the allowlist with current hashes. Idempotent (second scan is no-op)."""
    plugins_dir, allowlist_path, reg, _ = plugin_env
    assert not allowlist_path.exists()
    _write_plugin(plugins_dir, "legacy_plugin.py",
                   _BENIGN_PLUGIN.format(name="legacy_plugin"))

    reg.scan()
    assert allowlist_path.exists(), "scan() must create allowlist on first migration"

    allowlist = json.loads(allowlist_path.read_text())
    assert "legacy_plugin.py" in allowlist
    assert allowlist["legacy_plugin.py"]["approved_by"] == "initial_migration"
    # Hash matches file content
    import hashlib
    expected = hashlib.sha256(
        (plugins_dir / "legacy_plugin.py").read_bytes()
    ).hexdigest()
    assert allowlist["legacy_plugin.py"]["sha256"] == expected


def test_grandfather_migration_idempotent(plugin_env):
    """If allowlist already exists, scan() must NOT overwrite it.
    Operator's deliberate additions must survive subsequent scans."""
    plugins_dir, allowlist_path, reg, _ = plugin_env
    custom = {"custom.py": {"sha256": "deadbeef" * 8,
                              "approved_at": "2025-01-01T00:00:00",
                              "approved_by": "manual"}}
    allowlist_path.write_text(json.dumps(custom))

    _write_plugin(plugins_dir, "new_plugin.py",
                   _BENIGN_PLUGIN.format(name="new_plugin"))
    reg.scan()

    after = json.loads(allowlist_path.read_text())
    # Existing custom entry survived
    assert "custom.py" in after
    assert after["custom.py"]["sha256"] == "deadbeef" * 8
    # New plugin was NOT auto-grandfathered (operator must approve)
    assert "new_plugin.py" not in after


def test_allowlist_file_has_0600_perms(plugin_env):
    """The allowlist file contains operator trust decisions — must be
    operator-readable only."""
    plugins_dir, allowlist_path, reg, _ = plugin_env
    _write_plugin(plugins_dir, "p.py", _BENIGN_PLUGIN.format(name="p"))
    reg.scan()
    assert allowlist_path.exists()
    mode = os.stat(allowlist_path).st_mode & 0o777
    assert mode == 0o600, f"plugins.allowlist must be 0600; got 0o{mode:o}"


def test_approve_plugin_helper_adds_entry(plugin_env, monkeypatch):
    """`codec_hooks.approve_plugin` adds a fresh allowlist entry."""
    plugins_dir, allowlist_path, reg, codec_hooks = plugin_env
    # Make codec_hooks's module-level _registry point at our tmp registry
    monkeypatch.setattr(codec_hooks, "_registry", reg)

    _write_plugin(plugins_dir, "newhook.py", _BENIGN_PLUGIN.format(name="newhook"))
    # Pre-allowlist is empty
    allowlist_path.write_text("{}")

    result = codec_hooks.approve_plugin("newhook.py", approved_by="operator")
    assert result["ok"] is True
    assert result["filename"] == "newhook.py"
    assert len(result["sha256"]) == 64
    assert len(result["last8"]) == 8

    # Allowlist now contains the entry
    allowlist = json.loads(allowlist_path.read_text())
    assert "newhook.py" in allowlist
    assert allowlist["newhook.py"]["approved_by"] == "operator"


def test_approve_plugin_rejects_path_traversal(plugin_env, monkeypatch):
    """`../../../etc/passwd` and other path-traversal must be refused."""
    _, _, reg, codec_hooks = plugin_env
    monkeypatch.setattr(codec_hooks, "_registry", reg)
    for bad in ("../etc/passwd", "../../foo.py", "/etc/passwd", ".hidden.py"):
        result = codec_hooks.approve_plugin(bad)
        assert result["ok"] is False, f"Should reject path traversal: {bad!r}"


def test_approve_plugin_rejects_missing_file(plugin_env, monkeypatch):
    _, _, reg, codec_hooks = plugin_env
    monkeypatch.setattr(codec_hooks, "_registry", reg)
    result = codec_hooks.approve_plugin("nonexistent.py")
    assert result["ok"] is False
    assert "not found" in result["reason"]


def test_approve_plugin_clears_broken_cache(plugin_env, monkeypatch):
    """If a plugin was previously refused (in _broken), approving it
    must clear that entry so the next fire retries."""
    plugins_dir, allowlist_path, reg, codec_hooks = plugin_env
    monkeypatch.setattr(codec_hooks, "_registry", reg)

    _write_plugin(plugins_dir, "retry.py", _BENIGN_PLUGIN.format(name="retry"))
    allowlist_path.write_text("{}")

    # Simulate a prior load failure
    reg._broken.add("retry")

    codec_hooks.approve_plugin("retry.py", approved_by="operator")
    assert "retry" not in reg._broken, "approve_plugin must clear broken cache"


# ── D-18 (c): hook timeout ──────────────────────────────────────────────────


def test_run_hook_with_timeout_returns_value_for_fast_hook():
    """Fast hooks return their value normally."""
    import codec_hooks
    result, exc = codec_hooks._run_hook_with_timeout(
        lambda x: x * 2, (21,),
        hook_name="pre_tool", plugin_name="fast",
        timeout_s=1.0,
    )
    assert result == 42
    assert exc is None


def test_run_hook_with_timeout_aborts_slow_hook():
    """A hook that takes longer than the timeout returns _HookTimedOut.
    The calling thread is NOT blocked beyond the timeout."""
    import codec_hooks

    def slow(_x):
        time.sleep(1.0)
        return "never seen"

    t0 = time.monotonic()
    result, exc = codec_hooks._run_hook_with_timeout(
        slow, (None,),
        hook_name="pre_tool", plugin_name="slow",
        timeout_s=0.05,
    )
    elapsed = time.monotonic() - t0
    assert isinstance(result, codec_hooks._HookTimedOut)
    assert result.timeout_s == 0.05
    # Must not have blocked for the full 1s — accept up to 200ms slack
    assert elapsed < 0.3, f"Calling thread blocked too long: {elapsed:.2f}s"
    assert exc is None


def test_run_hook_with_timeout_captures_exception():
    """If the hook raises, the exception is returned (not raised)."""
    import codec_hooks

    def boom(_x):
        raise ValueError("plugin bug")

    result, exc = codec_hooks._run_hook_with_timeout(
        boom, (None,),
        hook_name="pre_tool", plugin_name="boomer",
        timeout_s=1.0,
    )
    assert result is None
    assert isinstance(exc, ValueError)
    assert str(exc) == "plugin bug"


def test_fire_one_pre_tool_emits_plugin_hook_timeout(plugin_env, monkeypatch):
    """Wired test: a slow `pre_tool` hook triggers `plugin_hook_timeout`
    audit via `_fire_one_pre_tool`."""
    plugins_dir, allowlist_path, reg, codec_hooks = plugin_env
    monkeypatch.setattr(codec_hooks, "_registry", reg)

    slow_src = (
        '"""slow plugin"""\n'
        'PLUGIN_NAME = "slow"\n'
        'import time\n'
        'def pre_tool(ctx):\n'
        '    time.sleep(1.0)\n'
        '    return None\n'
    )
    _write_plugin(plugins_dir, "slow.py", slow_src)
    allowlist = {"slow.py": _allowlist_entry(plugins_dir, "slow.py")}
    allowlist_path.write_text(json.dumps(allowlist))

    # Tight timeout for the test (override via config)
    from codec_config import cfg
    monkeypatch.setitem(cfg, "plugin_hook_timeout_ms", 50)

    captured = []
    monkeypatch.setattr(codec_hooks, "_log_event",
                         lambda *args, **kw: captured.append({"args": args, "kw": kw}))

    reg.scan()
    ctx = codec_hooks.HookCtx(
        transport="chat",
        correlation_id="testcid000000",
        plugin_name="",
        timestamp_utc="2026-05-18T00:00:00.000+00:00",
        tool_name="weather",
    )
    plugins = reg.for_hook("pre_tool", "weather")
    assert plugins, "Slow plugin should be in pre_tool list"

    t0 = time.monotonic()
    codec_hooks._fire_one_pre_tool(plugins[0], ctx)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.3, f"Slow hook blocked too long: {elapsed:.2f}s"

    matches = [c for c in captured
               if c["args"] and c["args"][0] == "plugin_hook_timeout"]
    assert matches, f"Expected plugin_hook_timeout emit; got {captured!r}"
    extra = matches[0]["kw"].get("extra", {})
    assert extra["plugin_name"] == "slow"
    assert extra["hook_name"] == "pre_tool"


# ── Source-level invariants ─────────────────────────────────────────────────


def test_codec_hooks_source_has_allowlist_gate():
    """Belt-and-suspenders: `get_fn` MUST call `_is_plugin_allowed`
    before any `exec_module` call."""
    src = (REPO / "codec_hooks.py").read_text()
    idx = src.find("def get_fn")
    assert idx >= 0
    body = src[idx:idx + 3000]
    assert "_is_plugin_allowed" in body, (
        "get_fn must check the allowlist before exec_module"
    )
    # The check must come BEFORE the spec.loader.exec_module(mod) call
    # (search for the call form so we don't false-positive on docstring
    # mentions of "exec_module")
    allowed_idx = body.find("_is_plugin_allowed(\n")  # multi-line call form
    if allowed_idx < 0:
        allowed_idx = body.find("_is_plugin_allowed(plugin")
    exec_idx = body.find("spec.loader.exec_module(")
    assert allowed_idx >= 0, "_is_plugin_allowed call not found in get_fn body"
    assert exec_idx >= 0, "spec.loader.exec_module call not found"
    assert allowed_idx < exec_idx, (
        "_is_plugin_allowed must run BEFORE spec.loader.exec_module"
    )


def test_plugin_approve_skill_is_not_mcp_exposed():
    """Plugin trust extension must never be reachable from claude.ai/MCP."""
    skills_dir = REPO / "skills"
    sys.path.insert(0, str(skills_dir))
    try:
        import plugin_approve
        import importlib
        importlib.reload(plugin_approve)
        assert getattr(plugin_approve, "SKILL_MCP_EXPOSE", True) is False
    finally:
        sys.path.remove(str(skills_dir))
