"""Tests for codec_dashboard host binding + startup safety check.

Closes D-7 (HIGH): default-no-auth dashboard binds 0.0.0.0:8090.
Before PR-2A, the dashboard hard-coded `host="0.0.0.0"` and the
no-auth Layer 0 of AuthMiddleware (line 152) let every LAN request
through. PR-2A:
  1. Adds DASHBOARD_HOST config knob, default `127.0.0.1`.
  2. Refuses to start the dashboard when host=0.0.0.0 AND no auth
     is configured (no dashboard_token, no AUTH_ENABLED).

Reference: docs/audits/PHASE-1-SECURITY.md finding D-7.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def test_dashboard_host_defaults_to_loopback():
    """The default value for DASHBOARD_HOST must be 127.0.0.1, not 0.0.0.0.

    Closes D-7 §1: out-of-box config must NOT bind on all interfaces.
    """
    import codec_config
    # If user already overrode in ~/.codec/config.json, skip
    import importlib
    importlib.reload(codec_config)
    assert hasattr(codec_config, "DASHBOARD_HOST"), (
        "codec_config must export DASHBOARD_HOST (default 127.0.0.1)"
    )
    # If a user-supplied value is present in cfg, accept either default or
    # the user's choice — both should NOT be 0.0.0.0 by default.
    cfg_value = codec_config.cfg.get("dashboard_host")
    if cfg_value is None:
        assert codec_config.DASHBOARD_HOST == "127.0.0.1", (
            f"Default DASHBOARD_HOST must be 127.0.0.1, got: "
            f"{codec_config.DASHBOARD_HOST!r}"
        )


def test_check_dashboard_start_safety_refuses_unsafe():
    """When host=0.0.0.0 AND no auth is configured, the start-safety check
    must refuse the start. Closes D-7 §3."""
    from codec_dashboard import _check_dashboard_start_safety
    ok, msg = _check_dashboard_start_safety(
        host="0.0.0.0",
        dashboard_token="",
        auth_enabled=False,
    )
    assert ok is False
    assert msg, "Refusal must include a non-empty error message"
    # Sanity: message mentions the unsafe combination
    assert "0.0.0.0" in msg
    assert "auth" in msg.lower() or "token" in msg.lower()


def test_check_dashboard_start_safety_allows_loopback_no_auth():
    """The default config (host=127.0.0.1, no auth) is fine — loopback-only
    means LAN can't reach it, so no auth is acceptable for local use."""
    from codec_dashboard import _check_dashboard_start_safety
    ok, msg = _check_dashboard_start_safety(
        host="127.0.0.1",
        dashboard_token="",
        auth_enabled=False,
    )
    assert ok is True, f"Loopback + no auth must be allowed: {msg!r}"


def test_check_dashboard_start_safety_allows_public_with_token():
    """When host=0.0.0.0 BUT dashboard_token is set, the start is allowed —
    the token gates API requests."""
    from codec_dashboard import _check_dashboard_start_safety
    ok, msg = _check_dashboard_start_safety(
        host="0.0.0.0",
        dashboard_token="abc123",
        auth_enabled=False,
    )
    assert ok is True, f"0.0.0.0 + dashboard_token must be allowed: {msg!r}"


def test_check_dashboard_start_safety_allows_public_with_auth_enabled():
    """When host=0.0.0.0 BUT AUTH_ENABLED is True (Touch ID / PIN), the
    start is allowed — biometric gates dashboard access."""
    from codec_dashboard import _check_dashboard_start_safety
    ok, msg = _check_dashboard_start_safety(
        host="0.0.0.0",
        dashboard_token="",
        auth_enabled=True,
    )
    assert ok is True, f"0.0.0.0 + auth_enabled must be allowed: {msg!r}"


def test_check_dashboard_start_safety_allows_ipv6_loopback():
    """`::1` is the IPv6 loopback equivalent and must also be allowed."""
    from codec_dashboard import _check_dashboard_start_safety
    ok, msg = _check_dashboard_start_safety(
        host="::1",
        dashboard_token="",
        auth_enabled=False,
    )
    assert ok is True


def test_uvicorn_run_uses_dashboard_host_config():
    """The __main__ block must read DASHBOARD_HOST from codec_config rather
    than hard-coding `0.0.0.0`. Source-level check."""
    src = (REPO / "codec_dashboard.py").read_text()
    # The hard-coded literal must be gone OR only used in a conditional.
    # Simpler check: the __main__ block must reference DASHBOARD_HOST.
    assert "DASHBOARD_HOST" in src, (
        "codec_dashboard.py must reference DASHBOARD_HOST from codec_config"
    )
    # And the hard-coded host="0.0.0.0" line must not be present anymore.
    assert 'host="0.0.0.0"' not in src, (
        "codec_dashboard.py must not hard-code host=\"0.0.0.0\""
    )


# ── Startup hook: the check also runs under `uvicorn codec_dashboard:app` ────


def _no_auth(monkeypatch, cd, token=""):
    import codec_config
    monkeypatch.setattr(cd, "AUTH_ENABLED", False)
    monkeypatch.setattr(cd, "_MAIN_BIND_HOST", None)
    monkeypatch.setattr(codec_config, "get_dashboard_token", lambda: token)
    monkeypatch.delenv("UVICORN_HOST", raising=False)


def test_startup_safety_hook_runs_before_background_services():
    import codec_dashboard as cd
    assert cd.app.router.on_startup[0] is cd._enforce_dashboard_start_safety


def test_startup_hook_refuses_uvicorn_cli_public_bind_without_auth(monkeypatch):
    import codec_dashboard as cd
    import pytest
    _no_auth(monkeypatch, cd)
    monkeypatch.setattr(sys, "argv",
                        ["uvicorn", "codec_dashboard:app", "--host", "0.0.0.0", "--port", "8090"])
    with pytest.raises(RuntimeError, match="Refusing to start"):
        cd._enforce_dashboard_start_safety()


def test_startup_hook_reads_host_equals_form_and_env(monkeypatch):
    import codec_dashboard as cd
    import pytest
    _no_auth(monkeypatch, cd)
    monkeypatch.setattr(sys, "argv", ["uvicorn", "codec_dashboard:app", "--host=::"])
    with pytest.raises(RuntimeError):
        cd._enforce_dashboard_start_safety()
    monkeypatch.setattr(sys, "argv", ["uvicorn", "codec_dashboard:app"])
    monkeypatch.setenv("UVICORN_HOST", "0.0.0.0")
    with pytest.raises(RuntimeError):
        cd._enforce_dashboard_start_safety()


def test_startup_hook_allows_public_bind_with_token(monkeypatch):
    import codec_dashboard as cd
    _no_auth(monkeypatch, cd, token="abc123")
    monkeypatch.setattr(sys, "argv", ["uvicorn", "codec_dashboard:app", "--host", "0.0.0.0"])
    cd._enforce_dashboard_start_safety()  # no raise


def test_startup_hook_allows_loopback_without_token_lookup(monkeypatch):
    import codec_config
    import codec_dashboard as cd
    _no_auth(monkeypatch, cd)

    def _boom():
        raise AssertionError("loopback start must not look up the dashboard token")
    monkeypatch.setattr(codec_config, "get_dashboard_token", _boom)
    for argv in (["uvicorn", "codec_dashboard:app", "--host", "127.0.0.1"],
                 ["uvicorn", "codec_dashboard:app"]):  # uvicorn default = 127.0.0.1
        monkeypatch.setattr(sys, "argv", argv)
        cd._enforce_dashboard_start_safety()


def test_startup_hook_uses_main_block_host(monkeypatch):
    """`python codec_dashboard.py` records DASHBOARD_HOST; argv has no --host."""
    import codec_dashboard as cd
    import pytest
    _no_auth(monkeypatch, cd)
    monkeypatch.setattr(sys, "argv", ["codec_dashboard.py"])
    monkeypatch.setattr(cd, "_MAIN_BIND_HOST", "0.0.0.0")
    with pytest.raises(RuntimeError):
        cd._enforce_dashboard_start_safety()


def test_unsafe_bind_fails_the_asgi_lifespan(monkeypatch):
    """End-to-end through Starlette's lifespan: startup fails, so uvicorn
    would exit before binding. Only the safety hook is registered here so a
    regression can never start the real background services in a test."""
    import codec_dashboard as cd
    import pytest
    from starlette.testclient import TestClient
    _no_auth(monkeypatch, cd)
    monkeypatch.setattr(sys, "argv", ["uvicorn", "codec_dashboard:app", "--host", "0.0.0.0"])
    monkeypatch.setattr(cd.app.router, "on_startup", [cd._enforce_dashboard_start_safety])
    monkeypatch.setattr(cd.app.router, "on_shutdown", [])
    with pytest.raises(BaseException) as exc:
        with TestClient(cd.app):
            pass
    assert "Refusing to start" in repr(exc.value) or "Refusing to start" in str(exc.value)
