"""Phase 1 Step 2 §9.1 — plugin discovery tests.

Validates the AST-parse + lazy-import contract from
docs/PHASE1-STEP2-DESIGN.md §2.

Tests use a temp plugins dir + a fresh PluginRegistry instance so the
production module-level registry is untouched. ~/.codec/plugins/ on
the developer's machine is NEVER touched by these tests.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import codec_hooks


@pytest.fixture
def temp_plugins(tmp_path, monkeypatch):
    """Yields a (plugins_dir, registry) pair backed by a fresh tmp dir.

    Tests write plugin files into plugins_dir and call registry.scan().
    Production registry untouched.
    """
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    registry = codec_hooks.PluginRegistry(str(plugins_dir))
    yield plugins_dir, registry


def _write_plugin(plugins_dir: Path, name: str, source: str) -> Path:
    fpath = plugins_dir / name
    fpath.write_text(textwrap.dedent(source).strip() + "\n", encoding="utf-8")
    return fpath


# ── Discovery ────────────────────────────────────────────────────────────────

def test_plugin_with_metadata_constants_only_does_not_register(temp_plugins):
    plugins_dir, reg = temp_plugins
    _write_plugin(plugins_dir, "noop.py", """
        PLUGIN_NAME = "noop"
        PLUGIN_PRIORITY = 50
    """)
    n = reg.scan()
    assert n == 0
    assert reg.all() == []


def test_plugin_with_pre_tool_only_loads(temp_plugins):
    plugins_dir, reg = temp_plugins
    _write_plugin(plugins_dir, "p1.py", """
        def pre_tool(ctx):
            return None
    """)
    n = reg.scan()
    assert n == 1
    plugins = reg.all()
    assert plugins[0].name == "p1"
    assert plugins[0].declared_hooks == ["pre_tool"]


def test_plugin_with_all_five_hooks_loads(temp_plugins):
    plugins_dir, reg = temp_plugins
    _write_plugin(plugins_dir, "everything.py", """
        PLUGIN_NAME = "everything"
        def pre_tool(ctx): return None
        def post_tool(ctx, result): return None
        def on_error(ctx, exc): pass
        def on_operation_start(ctx): pass
        def on_operation_end(ctx): pass
    """)
    reg.scan()
    p = reg.all()[0]
    assert p.name == "everything"
    assert set(p.declared_hooks) == {
        "pre_tool", "post_tool", "on_error",
        "on_operation_start", "on_operation_end",
    }


def test_plugin_with_syntax_error_skipped_silently(temp_plugins, caplog):
    plugins_dir, reg = temp_plugins
    _write_plugin(plugins_dir, "broken.py", """
        def pre_tool(ctx)        # missing colon
            return None
    """)
    _write_plugin(plugins_dir, "ok.py", """
        def pre_tool(ctx): return None
    """)
    n = reg.scan()
    # Broken file is skipped; the well-formed one is still discovered.
    assert n == 1
    assert reg.all()[0].name == "ok"


def test_plugin_starting_with_underscore_skipped(temp_plugins):
    plugins_dir, reg = temp_plugins
    _write_plugin(plugins_dir, "_template.py", """
        def pre_tool(ctx): return None
    """)
    _write_plugin(plugins_dir, "real.py", """
        def pre_tool(ctx): return None
    """)
    reg.scan()
    names = [p.name for p in reg.all()]
    assert names == ["real"]


def test_plugin_default_name_is_filename_stem(temp_plugins):
    plugins_dir, reg = temp_plugins
    _write_plugin(plugins_dir, "cost_tracker.py", """
        def pre_tool(ctx): return None
    """)
    reg.scan()
    assert reg.all()[0].name == "cost_tracker"


def test_plugin_default_priority_is_100(temp_plugins):
    plugins_dir, reg = temp_plugins
    _write_plugin(plugins_dir, "p.py", """
        def pre_tool(ctx): return None
    """)
    reg.scan()
    assert reg.all()[0].priority == 100


def test_plugin_priority_explicit(temp_plugins):
    plugins_dir, reg = temp_plugins
    _write_plugin(plugins_dir, "p.py", """
        PLUGIN_PRIORITY = 25
        def pre_tool(ctx): return None
    """)
    reg.scan()
    assert reg.all()[0].priority == 25


def test_plugin_tool_filter_list(temp_plugins):
    plugins_dir, reg = temp_plugins
    _write_plugin(plugins_dir, "p.py", """
        PLUGIN_TOOL_FILTER = ["weather", "notes"]
        def pre_tool(ctx): return None
    """)
    reg.scan()
    p = reg.all()[0]
    assert p.tool_filter == ["weather", "notes"]
    assert p.applies_to("weather") is True
    assert p.applies_to("calendar") is False
    # operation hooks: tool_name is None → applies_to returns True regardless
    assert p.applies_to(None) is True


def test_plugin_tool_filter_none_means_all_tools(temp_plugins):
    plugins_dir, reg = temp_plugins
    _write_plugin(plugins_dir, "p.py", """
        PLUGIN_TOOL_FILTER = None
        def pre_tool(ctx): return None
    """)
    reg.scan()
    p = reg.all()[0]
    assert p.tool_filter is None
    assert p.applies_to("anything") is True


def test_module_import_is_lazy(temp_plugins):
    """Per §2.3: AST parse at scan; module import only on first hook fire.

    We simulate this by including a top-level statement with side effects
    (a print to a sentinel file). After scan, the file MUST NOT exist;
    after the first get_fn() call, it should.
    """
    plugins_dir, reg = temp_plugins
    sentinel = plugins_dir / "loaded.txt"
    _write_plugin(plugins_dir, "p.py", f"""
        # This top-level statement only runs if the module is imported.
        with open({str(sentinel)!r}, "w") as f:
            f.write("imported")

        def pre_tool(ctx): return None
    """)
    reg.scan()
    # AST parse only — sentinel file should not exist.
    assert not sentinel.exists(), "scan() should not import the module"
    # Now trigger a lazy import via get_fn.
    p = reg.all()[0]
    fn = reg.get_fn(p, "pre_tool")
    assert callable(fn)
    assert sentinel.exists(), "get_fn() should trigger module import"


def test_broken_import_marks_plugin_as_broken_does_not_break_others(temp_plugins):
    """Per §2.3: broken plugin doesn't break startup OR other plugins."""
    plugins_dir, reg = temp_plugins
    _write_plugin(plugins_dir, "broken_at_import.py", """
        # Valid AST but raises on import (syntax-OK, semantics-bad).
        raise RuntimeError("intentional import failure")
        def pre_tool(ctx): return None
    """)
    _write_plugin(plugins_dir, "good.py", """
        def pre_tool(ctx): return None
    """)
    reg.scan()
    # Both discovered at scan time (AST parse passes for both).
    assert len(reg.all()) == 2
    # Calling get_fn on the broken one fails gracefully + marks broken.
    broken = next(p for p in reg.all() if p.name == "broken_at_import")
    fn = reg.get_fn(broken, "pre_tool")
    assert fn is None
    # The good plugin still works.
    good = next(p for p in reg.all() if p.name == "good")
    assert callable(reg.get_fn(good, "pre_tool"))


def test_for_hook_filters_by_hook_name_and_tool(temp_plugins):
    plugins_dir, reg = temp_plugins
    _write_plugin(plugins_dir, "weather_only.py", """
        PLUGIN_TOOL_FILTER = ["weather"]
        def pre_tool(ctx): return None
    """)
    _write_plugin(plugins_dir, "all_tools.py", """
        def pre_tool(ctx): return None
        def post_tool(ctx, result): return None
    """)
    reg.scan()
    pre_for_weather = reg.for_hook("pre_tool", "weather")
    pre_for_notes = reg.for_hook("pre_tool", "notes")
    post_for_weather = reg.for_hook("post_tool", "weather")
    assert {p.name for p in pre_for_weather} == {"weather_only", "all_tools"}
    assert {p.name for p in pre_for_notes} == {"all_tools"}
    assert {p.name for p in post_for_weather} == {"all_tools"}
