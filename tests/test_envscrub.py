"""P-084: one scrubbed environment for child processes (docs/ENV-SCRUB-DESIGN.md).

codec_envscrub holds the one rule for secret-looking variable names; the three
Vibe modules use it and keep their own SSH_AUTH_SOCK behaviour; the terminal
skill and the crew shell_execute start commands that cannot see such a
variable. The probe variable MY_API_TOKEN is set only in this test's own
environment (monkeypatch) with a random dummy value: no real secret is set or
printed. The probes run a harmless `env | grep`; the crew one runs in the real
macOS sandbox with a temp home, and audit lines go to a temp file.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import codec_envscrub  # noqa: E402

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
needs_sandbox = pytest.mark.skipif(
    sys.platform != "darwin" or not os.path.exists(SANDBOX_EXEC),
    reason="macOS sandbox-exec is not available here")
needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git is not installed")


def _this_checkout(name, monkeypatch):
    """This checkout's module, even if another test imported the live
    checkout's copy first (same helper as tests/test_crew_shell_sandbox.py)."""
    mod = sys.modules.get(name) or importlib.import_module(name)
    if Path(getattr(mod, "__file__", "") or "").resolve() == REPO / f"{name.replace('.', '/')}.py":
        return mod
    spec = importlib.util.spec_from_file_location(name, REPO / f"{name.replace('.', '/')}.py")
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, mod)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def probe(monkeypatch):
    """MY_API_TOKEN with a random dummy value, in this test's environment only."""
    value = "p084-dummy-" + secrets.token_hex(8)
    monkeypatch.setenv("MY_API_TOKEN", value)
    return value


# ── the rule ──

def test_scrub_drops_secret_looking_names_and_keeps_the_rest():
    env = {
        "ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "b", "MY_API_TOKEN": "c", "GITHUB_TOKEN": "d",
        "DB_PASSWORD": "e", "AWS_ACCESS_KEY_ID": "f", "AWS_SECRET_ACCESS_KEY": "g", "KEY": "h",
        "SIGNING_KEY": "i", "SESSION_TOKEN": "j", "GOOGLE_CREDENTIALS": "k", "my_passwd": "l",
        "PATH": "/usr/bin:/bin", "HOME": "/Users/someone", "LANG": "en_US.UTF-8", "TERM": "xterm",
        "SSH_AUTH_SOCK": "/tmp/agent.sock", "KEYCHAIN_PATH": "x", "GOPATH": "/go", "npm_config_cache": "/c",
    }
    before = dict(env)
    out = codec_envscrub.scrub(env)
    assert out == {"PATH": "/usr/bin:/bin", "HOME": "/Users/someone", "LANG": "en_US.UTF-8", "TERM": "xterm",
                   "SSH_AUTH_SOCK": "/tmp/agent.sock", "KEYCHAIN_PATH": "x", "GOPATH": "/go",
                   "npm_config_cache": "/c"}
    assert env == before and out is not env          # the input is not changed
    kept = codec_envscrub.scrub(env, keep=("MY_API_TOKEN", "NOT_THERE"))
    assert kept["MY_API_TOKEN"] == "c" and "NOT_THERE" not in kept and "GITHUB_TOKEN" not in kept
    assert codec_envscrub.scrub({}) == {}


@needs_git
def test_the_pattern_lives_in_one_module_only():
    r = subprocess.run(["git", "grep", "-lF", "session[_-]?token", "--", "*.py", ":!tests"],
                       cwd=REPO, capture_output=True, text=True, timeout=30)
    if r.returncode not in (0, 1) or "not a git repository" in r.stderr:
        pytest.skip("not a git checkout")
    assert r.stdout.split() == ["codec_envscrub.py"]


# ── the three Vibe modules keep their own SSH_AUTH_SOCK behaviour ──

@pytest.mark.parametrize("name, keeps_ssh_agent", [
    ("routes.vibe_exec", True),
    ("codec_vibe_runner", True),
    ("codec_vibe_agent_cmd", False),
])
def test_each_vibe_module_uses_the_shared_rule_and_keeps_its_ssh_agent_rule(name, keeps_ssh_agent, monkeypatch, probe):
    mod = _this_checkout(name, monkeypatch)
    monkeypatch.setenv("FAKE_API_KEY", "p084-dummy")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/p084-agent.sock")
    monkeypatch.delenv("LC_ALL", raising=False)
    env = {"routes.vibe_exec": lambda: mod._hardened_run_env(),
           "codec_vibe_runner": lambda: mod._run_env(5301),
           "codec_vibe_agent_cmd": lambda: mod.command_env("/private/tmp/p084")}[name]()
    assert "MY_API_TOKEN" not in env and "FAKE_API_KEY" not in env
    assert probe not in env.values()
    assert ("SSH_AUTH_SOCK" in env) is keeps_ssh_agent
    assert env["LC_ALL"] == "C.UTF-8" and env["HOME"] == os.environ["HOME"] and env["PATH"]
    assert "codec_envscrub" in (REPO / f"{name.replace('.', '/')}.py").read_text()


# ── the terminal skill ──

def _terminal(monkeypatch):
    spec = importlib.util.spec_from_file_location("_p084_terminal", REPO / "skills" / "terminal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "is_dangerous", lambda cmd: False)   # let the probe reach the subprocess
    return mod


def test_terminal_commands_cannot_see_a_secret_looking_variable(monkeypatch, probe):
    term = _terminal(monkeypatch)
    # The skill lower-cases the command, hence grep -i.
    out = term.run("env | grep -i my_api")
    assert out == "Done (no output)"
    assert probe not in out
    # Control: the same probe without the filter does see it, so an empty
    # answer above means the variable was not there, not a blind probe.
    monkeypatch.setattr(term, "_scrub_env", lambda env: dict(env))
    leaked = term.run("env | grep -i my_api")
    assert f"MY_API_TOKEN={probe}" in leaked.splitlines()


def test_terminal_commands_keep_path_and_home(monkeypatch, probe):
    term = _terminal(monkeypatch)
    assert term.run("env | grep -i ^home=") == f"HOME={os.environ['HOME']}"
    assert term.run("env | grep -i ^path=") == f"PATH={os.environ['PATH']}"[:500]


@needs_git
def test_git_status_still_works_through_the_terminal_skill(monkeypatch, tmp_path, probe):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True, timeout=30)
    term = _terminal(monkeypatch)
    monkeypatch.chdir(repo)   # the skill lower-cases the command, so no path goes in it
    out = term.run("git status --porcelain=v2 --branch")
    assert out.startswith("# branch.oid (initial)") and "# branch.head " in out, out


def test_without_the_filter_the_terminal_skill_runs_nothing(monkeypatch):
    term = _terminal(monkeypatch)
    monkeypatch.setattr(term, "_scrub_env", None)
    monkeypatch.setattr(term.subprocess, "run", lambda *a, **k: pytest.fail("nothing may start"))
    assert term.run("echo hi").startswith("Blocked: the environment filter")


# ── the crew / custom-agent shell_execute (real sandbox) ──

@pytest.fixture
def crew_box(monkeypatch, tmp_path):
    import codec_audit
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    agents = _this_checkout("codec_agents", monkeypatch)
    for name in ("codec_sandbox", "codec_consent"):
        _this_checkout(name, monkeypatch)
    import codec_config   # the tool reads is_dangerous from it at call time
    monkeypatch.setattr(codec_config, "is_dangerous", lambda cmd: False)
    base = os.path.realpath(subprocess.check_output(
        ["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"], text=True).strip())
    root = os.path.realpath(tempfile.mkdtemp(prefix="p084-crew-", dir=base))
    home = Path(root) / "home"
    out = home / "codec-workspace" / "agent-shell"
    out.mkdir(parents=True)

    def run(cmd):
        return agents._shell_execute(cmd, home=str(home), output_dir=str(out), profile_dir=str(home / ".codec"))

    try:
        yield {"run": run, "home": home, "out": out}
    finally:
        shutil.rmtree(root, ignore_errors=True)


@needs_sandbox
def test_crew_shell_commands_cannot_see_a_secret_looking_variable(crew_box, probe):
    out = crew_box["run"]("env | grep -i my_api")
    assert out == "(no output)" and probe not in out
    # Control: the probe does print a variable the tool hands over.
    assert crew_box["run"]("env | grep -i ^term=").strip() == "TERM=dumb"
    found = crew_box["run"]("env | grep -i -e ^path= -e ^home=").split()
    assert f"HOME={crew_box['home']}" in found and any(line.startswith("PATH=/") for line in found)
