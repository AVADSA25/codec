"""Tests for PR-4G-2 (H-5) — codec_mcp_http._RATE_WINDOW evicts idle IPs so it
can't grow unbounded as claude.ai rotates source IPs.

codec_mcp_http imports mcp.* / fastmcp.* / codec_mcp (none installed locally or
in CI), but build_mcp() is only called in main(), so the module imports fine
once those names resolve. The `mcp_http` fixture installs a contained sys.modules
stub (real subclassable classes, auto-reverted via monkeypatch.setitem, and the
imported modules popped before+after) so NOTHING leaks to test_mcp /
test_oauth_provider — their baseline failures must stay exactly as-is. The
function under test (_rate_check) is real code; the stubs only unblock the import.

Reference: docs/PR4G2-RATE-WINDOW-DESIGN.md, docs/audits/PHASE-1-RELIABILITY.md H-5.
"""
from __future__ import annotations

import importlib
import sys
import time
import types
from collections import deque
from pathlib import Path

import pytest

# Force THIS worktree to the front of sys.path so the fixture's fresh import of
# codec_mcp_http resolves here (not to a parent checkout that lacks the H-5 fix
# but would still satisfy the sys.modules stubs). No-op in CI's single checkout.
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) in sys.path:
    sys.path.remove(str(_REPO))
sys.path.insert(0, str(_REPO))


_STUB_MODULES = [
    "mcp", "mcp.server", "mcp.server.auth", "mcp.server.auth.settings",
    "mcp.server.auth.provider", "mcp.shared", "mcp.shared.auth", "codec_mcp",
    "fastmcp", "fastmcp.server", "fastmcp.server.auth",
    "fastmcp.server.auth.providers", "fastmcp.server.auth.providers.in_memory",
]


class _Stub(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return type(name, (), {})  # real, subclassable, instantiable empty class


@pytest.fixture
def mcp_http(monkeypatch):
    for n in _STUB_MODULES:
        monkeypatch.setitem(sys.modules, n, _Stub(n))
    # Fresh import under the stubs (clean _RATE_WINDOW per test); pop the real
    # modules we import so the stub-based versions never leak to other tests.
    for m in ("codec_mcp_http", "codec_oauth_provider"):
        sys.modules.pop(m, None)
    mod = importlib.import_module("codec_mcp_http")
    try:
        yield mod
    finally:
        for m in ("codec_mcp_http", "codec_oauth_provider"):
            sys.modules.pop(m, None)


# ── rate-limit semantics still work (regression) ──────────────────────────────


def test_under_limit_allows(mcp_http):
    assert mcp_http._rate_check("1.2.3.4") is True


def test_over_limit_blocks(mcp_http):
    ip = "5.6.7.8"
    for _ in range(mcp_http._RATE_LIMIT):
        assert mcp_http._rate_check(ip) is True
    assert mcp_http._rate_check(ip) is False  # one past the limit → blocked


# ── eviction (the fix) ────────────────────────────────────────────────────────


def test_idle_ips_evicted_after_interval(mcp_http):
    now = time.time()
    for i in range(5):
        mcp_http._RATE_WINDOW[f"idle{i}"] = deque([now - 120])  # newest entry 2 min old
    mcp_http._RATE_LAST_EVICT = 0.0  # force a sweep this call
    mcp_http._rate_check("active")
    for i in range(5):
        assert f"idle{i}" not in mcp_http._RATE_WINDOW, "H-5: idle IPs must be evicted"
    assert "active" in mcp_http._RATE_WINDOW, "the current IP must remain"


def test_active_ip_not_evicted(mcp_http):
    now = time.time()
    mcp_http._RATE_WINDOW["fresh"] = deque([now])  # recent activity
    mcp_http._RATE_LAST_EVICT = 0.0
    mcp_http._rate_check("caller")
    assert "fresh" in mcp_http._RATE_WINDOW, "an IP with a recent entry must survive the sweep"


def test_sweep_gated_by_interval(mcp_http):
    now = time.time()
    mcp_http._RATE_WINDOW["idle"] = deque([now - 120])
    mcp_http._RATE_LAST_EVICT = now  # within the interval → must NOT sweep this call
    mcp_http._rate_check("caller")
    assert "idle" in mcp_http._RATE_WINDOW, "no sweep should run within _RATE_EVICT_INTERVAL"
