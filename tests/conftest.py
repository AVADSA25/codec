"""Pytest configuration.

Path setup: when running from a git worktree (e.g. .claude/worktrees/<branch>),
make sure the worktree's repo dir wins over ~/codec-repo so local codec_*.py
gets imported, not the main checkout. Without this, a worktree's Step 3
codec_audit (with new ASKUSER_EVENT_* constants) gets shadowed by main's
older copy and tests that rely on the new constants fail.

pynput stub: on Linux CI runners without an X display, `from pynput import
keyboard` raises ImportError at module-load time (pynput tries to acquire
an X11 connection). codec.py imports pynput unconditionally; many tests
do `import codec` at module scope. We install a minimal stub in sys.modules
BEFORE any test collection so those imports succeed. macOS dev machines
have a real pynput and skip the stub entirely. Per PR-4F design doc
(`docs/PR4F-STATE-LOCK-DESIGN.md`) — same pattern, lifted into conftest
so it applies session-wide instead of per-test.
"""
import sys
import os
import types
from pathlib import Path

# Worktree's repo root = parent of `tests/`. When running from main repo
# directly, this resolves to ~/codec-repo (same as the next insert) — harmless.
_WORKTREE_REPO = Path(__file__).resolve().parent.parent

# Order matters: insert(0) prepends. Last insert wins → worktree at sys.path[0].
sys.path.insert(0, os.path.expanduser("~/codec-repo"))
sys.path.insert(0, os.path.expanduser("~/.codec/skills"))
sys.path.insert(0, str(_WORKTREE_REPO))


def _install_pynput_stub_if_needed() -> None:
    """Stub `pynput` + `pynput.keyboard` if the real package can't import
    (headless Linux CI). On macOS the real package imports fine and this
    no-ops. The stub provides the symbols codec.py touches at import time
    — Listener, KeyCode, Key, Controller — as minimal placeholders. Tests
    that actually exercise keyboard behavior bring their own mocks; this
    stub only unblocks module import."""
    if "pynput" in sys.modules:
        return
    try:
        import pynput  # noqa: F401
        return
    except Exception:
        pass

    pynput_mod = types.ModuleType("pynput")
    keyboard_mod = types.ModuleType("pynput.keyboard")

    class _Stub:
        def __init__(self, *a, **kw): pass
        def __call__(self, *a, **kw): return self
        def __getattr__(self, name): return _Stub()
        def start(self): pass
        def stop(self): pass
        def join(self, *a, **kw): pass

    # `_Key` is accessed as `Key.f5`, `Key.f13`, `Key.cmd`, etc. — the set
    # of attribute names is open-ended (callers also use `f16`, `f17`).
    # Use a _Stub() instance so __getattr__ returns a fresh non-None _Stub
    # for ANY attribute, matching pynput's real keyboard.Key namespace
    # closely enough for codec_config._resolve_key() to return non-None.
    keyboard_mod.Listener = _Stub
    keyboard_mod.KeyCode = _Stub
    keyboard_mod.Key = _Stub()
    keyboard_mod.Controller = _Stub
    keyboard_mod.HotKey = _Stub
    pynput_mod.keyboard = keyboard_mod
    sys.modules["pynput"] = pynput_mod
    sys.modules["pynput.keyboard"] = keyboard_mod


_install_pynput_stub_if_needed()
