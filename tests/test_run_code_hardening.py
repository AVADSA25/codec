"""K1 — regression tests for /api/run_code execution hardening.

The Vibe IDE's run_code endpoint runs 8 languages incl. compilers + bash, so a
full sandbox-exec deny-default profile can't be applied without breaking the
feature. Instead it gets two language-agnostic hardenings, pinned here:

  1. resource limits (CPU / address-space / FDs / output-file size) applied in
     the child via preexec_fn — bounds runaway compute / memory / disk;
  2. a secret-stripped-but-functional env — drops API keys / tokens / secrets
     while preserving PATH / HOME / tool config so every interpreter still runs.

The python path is exercised end-to-end (proving the child actually receives
the limits + stripped env); the compiler/bash paths are validated structurally.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


class _FakeReq:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _run(payload):
    import routes.vibe_exec as ve
    return asyncio.run(ve.run_code(_FakeReq(payload)))


# ── env stripping ──────────────────────────────────────────────────────────
class TestHardenedEnv:
    def test_secrets_dropped(self, monkeypatch):
        import routes.vibe_exec as ve
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-leak")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-leak")
        monkeypatch.setenv("MY_SECRET", "s")
        monkeypatch.setenv("SOME_TOKEN", "t")
        monkeypatch.setenv("DB_PASSWORD", "p")
        monkeypatch.setenv("AWS_ACCESS_KEY", "a")
        env = ve._hardened_run_env()
        for leaked in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MY_SECRET",
                       "SOME_TOKEN", "DB_PASSWORD", "AWS_ACCESS_KEY"):
            assert leaked not in env, f"{leaked} leaked into run_code env"

    def test_functional_vars_preserved(self):
        import routes.vibe_exec as ve
        env = ve._hardened_run_env()
        assert env.get("PATH"), "PATH must be preserved so interpreters resolve"
        assert "HOME" in env, "HOME must be preserved for tool config"

    def test_ssh_auth_sock_kept(self, monkeypatch):
        # matches the 'auth' pattern but is in the keep-list
        import routes.vibe_exec as ve
        monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh.sock")
        assert ve._hardened_run_env().get("SSH_AUTH_SOCK") == "/tmp/ssh.sock"

    def test_secrets_not_in_child_environ(self, monkeypatch):
        """End-to-end: code run via /api/run_code must NOT see a secret env var."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-MUSTNOTLEAK")
        out = _run({"code": "import os; print(os.environ.get('ANTHROPIC_API_KEY','<absent>'))",
                    "language": "python"})
        assert out["stdout"].strip() == "<absent>", "secret leaked into child process"


# ── resource limits ─────────────────────────────────────────────────────────
class TestResourceLimits:
    def test_cpu_and_fsize_enforced_in_child(self):
        out = _run({
            "code": "import resource as r; "
                    "print(r.getrlimit(r.RLIMIT_CPU)[0], r.getrlimit(r.RLIMIT_FSIZE)[0])",
            "language": "python",
        })
        cpu, fsize = out["stdout"].split()
        assert int(cpu) == 25, f"RLIMIT_CPU not applied (got {cpu})"
        assert int(fsize) == 64 * 1024 * 1024, f"RLIMIT_FSIZE not applied (got {fsize})"

    def test_nofile_enforced_in_child(self):
        out = _run({
            "code": "import resource as r; print(r.getrlimit(r.RLIMIT_NOFILE)[0])",
            "language": "python",
        })
        assert int(out["stdout"].strip()) == 256

    def test_preexec_and_env_wired_into_subprocess(self):
        src = (_REPO / "routes" / "vibe_exec.py").read_text()
        assert "preexec_fn=_preexec_set_rlimits" in src
        assert "env=_hardened_run_env()" in src


# ── the feature still works ─────────────────────────────────────────────────
def test_python_still_runs():
    out = _run({"code": "print(6 * 7)", "language": "python"})
    assert out["stdout"].strip() == "42"
    assert out["exit_code"] == 0


def test_bash_still_runs():
    out = _run({"code": "echo hardened", "language": "bash"})
    assert "hardened" in out["stdout"]
