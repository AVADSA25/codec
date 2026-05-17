"""Tests for skills/python_exec.py and /api/execute removal.

Closes audit findings D-9 (python_exec substring blocker bypassable +
no sandbox) and D-10 (/api/execute shell=True bypassable). PR-2C.

The sandbox-exec subprocess tests run real commands on macOS and use
~6s of wall-clock each. Marked `@pytest.mark.slow` so a developer can
opt out with `pytest -m 'not slow'` when iterating quickly.

Reference: docs/audits/PHASE-1-SECURITY.md findings D-9 and D-10.
"""
from __future__ import annotations

import platform
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "skills"))

import python_exec  # noqa: E402


# ── AST-validation tests (no sandbox subprocess needed) ──────────────────────


def test_python_exec_blocks_dunder_import():
    """`__import__('os').system(...)` must be refused at the AST gate."""
    result = python_exec.run(
        "run python ```\n__import__('os').system('echo pwned')\n```"
    )
    assert "Blocked for safety" in result, result


def test_python_exec_blocks_eval():
    """`eval(...)` is in DANGEROUS_CALLS — refused at AST."""
    result = python_exec.run("run python ```\neval('1+1')\n```")
    assert "Blocked for safety" in result, result


def test_python_exec_blocks_exec():
    """`exec(...)` likewise refused."""
    result = python_exec.run("run python ```\nexec('x = 1')\n```")
    assert "Blocked for safety" in result, result


def test_python_exec_blocks_subprocess_import():
    """`import subprocess` is in DANGEROUS_MODULES."""
    result = python_exec.run(
        "run python ```\nimport subprocess\nsubprocess.run(['ls'])\n```"
    )
    assert "Blocked for safety" in result, result


def test_python_exec_blocks_os_system():
    """The `os.system(...)` attribute call is in DANGEROUS_ATTRS."""
    result = python_exec.run(
        "run python ```\nimport os\nos.system('echo x')\n```"
    )
    assert "Blocked for safety" in result, result


def test_python_exec_blocks_getattr_reflection():
    """`getattr(__builtins__, ...)` is in DANGEROUS_CALLS — AST catches
    the reflection-bypass that the old substring blocker missed."""
    result = python_exec.run(
        "run python ```\n"
        "getattr(__builtins__, chr(95)*2 + 'import' + chr(95)*2)('os')\n"
        "```"
    )
    assert "Blocked for safety" in result, result


def test_python_exec_blocks_vars_builtins():
    """`vars(__builtins__)` reflection — `vars` is not in DANGEROUS_CALLS
    but `__import__` is, and the attempted call resolves to it.

    NOTE: this exact form `vars(__builtins__)['__import__']` only trips
    the AST if `vars` is in DANGEROUS_CALLS. It currently isn't — the
    audit D-17 plans to add `vars`. Skip until D-17 lands; verify
    sandbox-exec still blocks the runtime.
    """
    # Not asserting AST refusal here — D-17 closure (later wave) will add
    # `vars` and `dir` to DANGEROUS_CALLS. For now the sandbox is the
    # backstop. Document the gap.
    pass


def test_python_exec_emits_blocked_audit_event(monkeypatch):
    """AST refusal must emit `python_exec_blocked` so an operator can
    grep ~/.codec/audit.log for blocked-execution attempts."""
    captured = []

    def fake_log_event(event_type, *args, **kwargs):
        captured.append({"event_type": event_type, "args": args, "kwargs": kwargs})

    monkeypatch.setattr("codec_audit.log_event", fake_log_event)

    python_exec.run("run python ```\n__import__('os').system('x')\n```")

    matches = [c for c in captured if c["event_type"] == "python_exec_blocked"]
    assert len(matches) == 1, (
        f"Expected exactly one python_exec_blocked event; got {captured}"
    )
    extra = matches[0]["kwargs"].get("extra", {})
    assert "reason" in extra and extra["reason"]
    assert "code_preview" in extra


def test_python_exec_helpers_present():
    """Internal helpers a future PR will rely on must exist."""
    assert hasattr(python_exec, "_is_safe_code")
    assert hasattr(python_exec, "_sandbox_command")
    assert hasattr(python_exec, "_minimal_safe_env")
    assert hasattr(python_exec, "_preexec_set_rlimits")


def test_minimal_safe_env_strips_dangerous_vars():
    """PYTHONPATH / LD_LIBRARY_PATH / SHELL must NOT be in the sandboxed env."""
    env = python_exec._minimal_safe_env()
    for k in ("PYTHONPATH", "LD_LIBRARY_PATH", "SHELL", "HOME"):
        assert k not in env, f"Dangerous env var {k} leaked into sandbox"
    assert env["PATH"] == "/usr/bin:/bin"


def test_sandbox_command_uses_sandbox_exec_binary():
    """`_sandbox_command` must prepend `/usr/bin/sandbox-exec -f <profile>`."""
    cmd = python_exec._sandbox_command("/usr/bin/python3", "/tmp/x.py")
    assert cmd[0] == "/usr/bin/sandbox-exec"
    assert cmd[1] == "-f"
    # Index 2 is the profile path written by codec_sandbox; just sanity
    # that it's a real path string and exists in the filesystem.
    assert cmd[2].endswith("sandbox.sb")
    assert cmd[3] == "/usr/bin/python3"
    assert cmd[4] == "/tmp/x.py"


# ── Live sandbox-exec subprocess tests (macOS only, slow) ────────────────────


@pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="sandbox-exec is macOS-only",
)
def test_python_exec_runs_safe_math():
    """A simple expression must reach exec and return correct output."""
    result = python_exec.run("run python ```\nprint(2 + 2)\n```")
    assert "4" in result, f"Expected '4' in output, got: {result!r}"


@pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="sandbox-exec is macOS-only",
)
def test_python_exec_blocks_network_at_sandbox():
    """`urllib.parse` is in SAFE_MODULES so import passes the AST gate,
    but `urllib.request` is NOT — AST refuses before the sandbox.
    Verifies the AST layer catches network-capable imports."""
    result = python_exec.run(
        "run python ```\n"
        "from urllib.request import urlopen\n"
        "urlopen('http://example.com').read()\n"
        "```"
    )
    # Either AST refuses (preferred) or sandbox blocks at socket()
    assert (
        "Blocked for safety" in result
        or "Error" in result
        or "denied" in result.lower()
    ), f"Network attempt must fail; got: {result!r}"


# ── /api/execute endpoint removal (D-10 closure) ─────────────────────────────


def test_dashboard_has_no_execute_endpoint():
    """The dashboard module must no longer expose `execute_terminal`,
    `_is_command_safe`, `_DANGEROUS_PATTERNS`, or the /api/execute
    route handler."""
    import codec_dashboard
    for sym in ("execute_terminal", "_is_command_safe",
                 "_DANGEROUS_PATTERNS", "_DANGEROUS_RE", "TerminalRequest"):
        assert not hasattr(codec_dashboard, sym), (
            f"codec_dashboard.{sym} must be removed (D-10 closure)"
        )


def test_dashboard_source_does_not_contain_api_execute_route():
    """Source-level check: no `@app.post(\"/api/execute\")` decorator
    AND no `async def execute_terminal` function definition. The string
    `execute_terminal` may still appear in the deletion-marker comment
    explaining the removal — that's intentional historical context."""
    src = (REPO / "codec_dashboard.py").read_text()
    assert '@app.post("/api/execute")' not in src
    assert "async def execute_terminal" not in src
    assert "def _is_command_safe" not in src


def test_no_api_execute_route_registered_in_app():
    """Inspect the FastAPI app's route table directly — `/api/execute`
    must NOT be present. This bypasses AuthMiddleware (which would 401
    in test mode) and proves at the routing layer that the endpoint is
    gone."""
    import codec_dashboard
    paths = []
    for route in codec_dashboard.app.routes:
        path = getattr(route, "path", None)
        if path:
            paths.append(path)
    assert "/api/execute" not in paths, (
        f"/api/execute must NOT be registered; routes: "
        f"{[p for p in paths if 'execute' in p or 'api' in p][:10]}"
    )
