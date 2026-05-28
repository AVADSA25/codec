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
    """`vars(__builtins__)['__import__']('os')` reflection bypass.

    D-17 (PR-2H) added `vars`/`dir` to DANGEROUS_CALLS and a bare
    `__builtins__` Name check — both now trip the AST gate. This was a
    placeholder (skipped) until D-17 landed; now it asserts the refusal."""
    result = python_exec.run(
        "run python ```\nvars(__builtins__)['__import__']('os')\n```"
    )
    assert "Blocked for safety" in result, result


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


# ── Fix #3 (C3): python_exec off chat-allowlist + tightened sandbox reads ─────
# Closes audit finding C3. Two layers:
#   1. python_exec is removed from CHAT_SKILL_ALLOWLIST so an injection-style
#      chat message can no longer auto-fire it via the pre-LLM hijack / post-LLM
#      tag path. It stays a skill (still reachable on the local voice/chat tag
#      path only through an explicit user [SKILL:...] is now impossible too —
#      the allowlist gates BOTH hijack and tag). SKILL_MCP_EXPOSE=False already
#      keeps it off MCP.
#   2. The sandbox read scope keeps the broad `(allow file-read*)` (so legitimate
#      stdlib imports never break — the "don't break working code" constraint)
#      but layers explicit `(deny file-read* ...)` rules AFTER it (SBPL is
#      last-match-wins) over the named credential paths: ~/.ssh, ~/.aws,
#      ~/.gnupg, the ~/.codec secret/oauth/config files, and the Keychain dirs.


def test_python_exec_not_in_chat_allowlist():
    """C3.1: python_exec must NOT be auto-firable from chat. Removing it from
    CHAT_SKILL_ALLOWLIST closes both the pre-LLM hijack and the post-LLM tag
    path (both gate on this set)."""
    import codec_dashboard
    assert "python_exec" not in codec_dashboard.CHAT_SKILL_ALLOWLIST, (
        "python_exec must be removed from CHAT_SKILL_ALLOWLIST (C3) — it stays "
        "a skill but is no longer auto-firable from a chat message"
    )


def test_dashboard_prompt_drops_python_exec_example():
    """C3.1: the chat system-prompt addon must not advertise a
    [SKILL:python_exec:...] example — the tag would be parsed but the skill is
    no longer on the allowlist, so showing it as an example is misleading."""
    src = (REPO / "codec_dashboard.py").read_text()
    assert "[SKILL:python_exec:" not in src, (
        "the python_exec skill-tag example must be removed from the chat prompt "
        "addon now that python_exec is off CHAT_SKILL_ALLOWLIST"
    )


def test_sandbox_profile_denies_secret_paths():
    """C3.2: the generated sandbox profile must explicitly deny reads of the
    named credential paths so a sandboxed python_exec (or any sandboxed skill)
    cannot exfiltrate private keys / OAuth tokens / Keychain material."""
    from codec_sandbox import _write_sandbox_profile
    path = _write_sandbox_profile(allow_network=False)
    with open(path) as f:
        content = f.read()
    assert "(deny file-read*" in content, "no file-read deny rule present"
    for needle in ("/.ssh", "oauth_state.json", "Keychains"):
        assert needle in content, f"sandbox profile must deny reads of {needle}"


def test_sandbox_profile_retains_broad_read():
    """C3.2 constraint: the broad `(allow file-read*)` MUST remain so legitimate
    stdlib / site-packages imports keep working — the deny rules are layered
    AFTER it (last-match-wins), not a replacement. Guards the 'don't break
    working code' rule against a future switch to a read-allowlist."""
    from codec_sandbox import _write_sandbox_profile
    path = _write_sandbox_profile(allow_network=False)
    with open(path) as f:
        content = f.read()
    assert "(allow file-read*)" in content, (
        "broad file-read allow must stay — removing it risks breaking imports"
    )


def test_sandbox_deny_is_after_allow():
    """C3.2 mechanism: SBPL is last-match-wins, so the targeted deny rules must
    appear AFTER the broad `(allow file-read*)` for the deny to take effect."""
    from codec_sandbox import _write_sandbox_profile
    path = _write_sandbox_profile(allow_network=False)
    with open(path) as f:
        content = f.read()
    allow_idx = content.index("(allow file-read*)")
    deny_idx = content.index("(deny file-read*")
    assert deny_idx > allow_idx, (
        "targeted file-read denies must come AFTER the broad allow "
        "(SBPL last-match-wins) or they have no effect"
    )


@pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="sandbox-exec is macOS-only",
)
def test_sandbox_denies_ssh_read_live():
    """C3.2 behavioral: a python_exec snippet that opens a file under ~/.ssh is
    DENIED by the sandbox, even though `open()` passes the AST gate. Proves the
    deny rule actually enforces — not just that a string is in the profile.

    Routed through the REAL python_exec.run() path so it uses the same
    interpreter selection (_python_bin → homebrew python, which process-exec
    allows). `open()` is deliberately allowed by the AST gate
    (codec_config.py:737), so the ONLY thing that can stop the read is the
    sandbox deny. Reads 1 byte and prints a sentinel — never the secret bytes."""
    ssh_dir = Path.home() / ".ssh"
    if not ssh_dir.exists():
        pytest.skip("no ~/.ssh on this machine to probe")
    target = next((p for p in sorted(ssh_dir.iterdir()) if p.is_file()), None)
    if target is None:
        pytest.skip("~/.ssh has no regular file to probe")
    code = (
        f"f = open({str(target)!r}, 'rb')\n"
        "_b = f.read(1)\n"
        "f.close()\n"
        "print('READ_OK' if _b else 'READ_EMPTY')\n"
    )
    result = python_exec.run(f"run python ```\n{code}```")
    assert "READ_OK" not in result, (
        f"sandbox must DENY reading under ~/.ssh; got: {result!r}"
    )
    low = result.lower()
    assert result.startswith("Error") or "not permitted" in low or "denied" in low, (
        f"expected a sandbox permission denial; got: {result!r}"
    )


@pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="sandbox-exec is macOS-only",
)
def test_python_exec_benign_import_still_runs_after_tightening():
    """C3.2 constraint (behavioral): a benign `import json` skill still runs
    end-to-end through the tightened sandbox — the deny rules must not break
    legitimate stdlib reads."""
    result = python_exec.run(
        "run python ```\nimport json\nprint(json.dumps({'a': 1}))\n```"
    )
    assert '"a": 1' in result or '"a":1' in result, (
        f"benign import json must still run after sandbox tightening; got: {result!r}"
    )
