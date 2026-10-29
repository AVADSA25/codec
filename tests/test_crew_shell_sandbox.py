"""P-030 parts 2-4 (owner decision D-17, 29 Oct 2026): the crew / custom-agent
shell_execute runs inside a macOS sandbox built from codec_sandbox's template,
flagged commands ask through codec_consent, saved custom agents name only known
tools, and every shell approval or refusal writes one audit line with lengths
only (docs/CREW-SHELL-SANDBOX-DESIGN.md).

codec_ask_user.ask is always patched: no PWA card, no real pending-questions
file, no phone push. The real-sandbox tests run on macOS only and keep
everything in a temp folder under the per-user temp dir: a temp "home" with a
canary file in <temp home>/.ssh, the output folder and the profile folder. The
real ~/.codec and ~/.ssh are never read or written.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
needs_sandbox = pytest.mark.skipif(
    sys.platform != "darwin" or not os.path.exists(SANDBOX_EXEC),
    reason="macOS sandbox-exec is not available here")


def _this_checkout(name, monkeypatch):
    """This checkout's module: other tests put ~/codec-repo first on sys.path,
    so a module first imported after that would be the live checkout's copy."""
    mod = sys.modules.get(name) or importlib.import_module(name)
    if Path(getattr(mod, "__file__", "") or "").resolve() == REPO / f"{name}.py":
        return mod
    spec = importlib.util.spec_from_file_location(name, REPO / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, mod)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wt(monkeypatch):
    """codec_sandbox, codec_consent and codec_agents from this checkout (the
    shell tool imports the first two from sys.modules when it runs)."""
    return {name: _this_checkout(name, monkeypatch)
            for name in ("codec_sandbox", "codec_consent", "codec_agents")}


@pytest.fixture
def audit_log(monkeypatch, tmp_path):
    import codec_audit
    path = tmp_path / "audit.log"
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", path)
    return path


def _records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.fixture(autouse=True)
def asks(monkeypatch):
    """Record ask() calls and answer with whatever the test sets (default: no
    answer). Autouse: no test here may reach the owner's real approval card."""
    import codec_ask_user
    state = {"answer": codec_ask_user.TIMEOUT_SENTINEL, "calls": []}

    def fake_ask(question, **kw):
        state["calls"].append({"question": question, **kw})
        return state["answer"]

    monkeypatch.setattr(codec_ask_user, "ask", fake_ask)
    return state


# ── the profile builder ──

def test_the_default_profile_is_python_execs_and_the_shell_one_adds_bin_and_drops_var_folders(wt, tmp_path):
    sb = wt["codec_sandbox"]
    plain = Path(sb._write_sandbox_profile(home=str(tmp_path / "h"), profile_path=str(tmp_path / "a.sb"),
                                           output_dir=str(tmp_path / "o"))).read_text()
    shell = Path(sb._write_sandbox_profile(home=str(tmp_path / "h"), profile_path=str(tmp_path / "b.sb"),
                                           output_dir=str(tmp_path / "o"), allow_shell=True)).read_text()
    exec_plain = plain[plain.index("(allow process-exec"):plain.index("(allow process-fork)")]
    exec_shell = shell[shell.index("(allow process-exec"):shell.index("(allow process-fork)")]
    assert '"/bin"' not in exec_plain and '(subpath "/bin")' in exec_shell
    write_plain = plain[plain.index("(allow file-write*"):plain.index("(allow process-exec")]
    write_shell = shell[shell.index("(allow file-write*"):shell.index("(allow process-exec")]
    assert '(subpath "/private/var/folders")' in write_plain and '(subpath "/private/var/folders")' not in write_shell
    assert f'(subpath "{tmp_path / "o"}")' in write_shell and '(subpath "/private/tmp")' in write_shell
    assert write_shell.count("(subpath") == 2 and write_shell.count("(literal") == 1
    assert '(literal "/dev/null")' in write_shell and "/dev/null" not in write_plain
    for p in (plain, shell):
        assert "(deny default)" in p and "(deny network-outbound)" in p and "(allow network" not in p
        assert f'(subpath "{tmp_path / "h"}/.ssh")' in p
        assert f'(literal "{tmp_path / "h"}/.codec/oauth_state.json")' in p
    assert (tmp_path / "o").is_dir()


def test_a_quote_in_a_folder_name_cannot_add_rules(wt, tmp_path):
    sb = wt["codec_sandbox"]
    evil = str(tmp_path / 'o")(allow file-write* (subpath "/')
    p = Path(sb._write_sandbox_profile(home=str(tmp_path / "h"), profile_path=str(tmp_path / "c.sb"),
                                       output_dir=evil, allow_shell=True)).read_text()
    assert '\\"' in p and '(subpath "/"))' not in p.replace('\\"', "")


# ── the tool's decisions (run everywhere; the sandbox run is stubbed) ──

@pytest.fixture
def ran(wt, monkeypatch, tmp_path):
    """Pretend sandbox-exec exists and record what would run."""
    calls = []
    ag = wt["codec_agents"]
    monkeypatch.setattr(ag, "_shell_sandbox_available", lambda: True)

    def fake_run(cmd, home, output_dir, profile_dir):
        calls.append((cmd, home, output_dir, profile_dir))
        return "ran\n", "", False

    monkeypatch.setattr(ag, "_run_shell_sandboxed", fake_run)
    return calls


def _call(ag, tmp_path, cmd):
    return ag._shell_execute(cmd, home=str(tmp_path / "home"), output_dir=str(tmp_path / "out"),
                             profile_dir=str(tmp_path / "prof"))


def test_without_sandbox_exec_the_shell_refuses_and_runs_nothing(wt, monkeypatch, audit_log, asks, tmp_path):
    ag = wt["codec_agents"]
    monkeypatch.setattr(ag, "_shell_sandbox_available", lambda: False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("nothing may start"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("nothing may start"))
    out = _call(ag, tmp_path, "echo secret-command-text")
    assert out.startswith("BLOCKED") and "sandbox" in out and "not run" in out
    assert asks["calls"] == []
    recs = _records(audit_log)
    assert [r["event"] for r in recs] == ["shell_blocked"]
    assert recs[0]["tool"] == "shell_execute" and recs[0]["outcome"] == "denied"
    assert recs[0]["extra"]["reason"] == "no_sandbox" and recs[0]["extra"]["cmd_chars"] == len("echo secret-command-text")
    assert "secret-command-text" not in audit_log.read_text()


def test_an_ordinary_command_runs_in_the_sandbox_without_asking(wt, ran, asks, audit_log, tmp_path):
    out = _call(wt["codec_agents"], tmp_path, "ls -la")
    assert out == "ran\n" and asks["calls"] == []
    assert ran == [("ls -la", os.path.realpath(tmp_path / "home"), os.path.realpath(tmp_path / "out"),
                    str(tmp_path / "prof"))]
    assert _records(audit_log) == []


def test_a_flagged_command_asks_through_codec_consent_and_a_decline_runs_nothing(wt, ran, asks, audit_log, tmp_path):
    cmd = "rm -rf ./build-secret-dir"
    out = _call(wt["codec_agents"], tmp_path, cmd)
    assert out.startswith("BLOCKED") and "declined or not answered" in out and ran == []
    (call,) = asks["calls"]
    assert call["destructive"] is True and call["asked_from"] == "crew" and call["tool_name"] == "shell_execute"
    assert cmd in call["question"]          # the owner sees the whole command on the card
    recs = _records(audit_log)
    assert [r["event"] for r in recs] == ["local_consent_decision"]
    assert recs[0]["tool"] == "shell_execute"
    assert recs[0]["extra"] == {**recs[0]["extra"], "asked_from": "crew", "decision": "refused", "task_chars": len(cmd)}
    assert "build-secret-dir" not in audit_log.read_text()


def test_a_flagged_command_the_owner_allows_runs_in_the_sandbox(wt, ran, asks, audit_log, tmp_path):
    asks["answer"] = "codec"
    out = _call(wt["codec_agents"], tmp_path, "rm -rf ./build")
    assert out == "ran\n" and len(ran) == 1 and len(asks["calls"]) == 1
    recs = _records(audit_log)
    assert [(r["event"], r["extra"]["decision"]) for r in recs] == [("local_consent_decision", "allowed")]


def test_with_the_consent_gate_off_a_flagged_command_is_blocked_as_before(wt, ran, asks, audit_log, tmp_path, monkeypatch):
    monkeypatch.setenv("CONSENT_GATE_ENABLED", "false")
    out = _call(wt["codec_agents"], tmp_path, "rm -rf ./build")
    assert out.startswith("BLOCKED") and ran == [] and asks["calls"] == []
    (rec,) = _records(audit_log)
    assert rec["event"] == "shell_blocked" and rec["extra"]["reason"] == "consent_gate_off"


def test_a_flagged_command_too_long_for_the_approval_card_is_refused(wt, ran, asks, audit_log, tmp_path):
    cmd = "rm -rf ./build" + " " * 190 + "; echo hidden-tail"
    out = _call(wt["codec_agents"], tmp_path, cmd)
    assert out.startswith("BLOCKED") and "200" in out and ran == [] and asks["calls"] == []
    (rec,) = _records(audit_log)
    assert rec["extra"] == {**rec["extra"], "reason": "too_long_to_show", "cmd_chars": len(cmd.strip())}
    assert "hidden-tail" not in audit_log.read_text()


def test_an_empty_command_and_a_broken_sandbox_setup_are_refused_with_one_line_each(wt, ran, audit_log, tmp_path, monkeypatch):
    ag = wt["codec_agents"]
    assert _call(ag, tmp_path, "   ").startswith("BLOCKED")

    def broken(*a):
        raise OSError("no room")

    monkeypatch.setattr(ag, "_run_shell_sandboxed", broken)
    assert _call(ag, tmp_path, "echo hi").startswith("BLOCKED: the sandbox could not be set up")
    assert [(r["event"], r["extra"]["reason"]) for r in _records(audit_log)] == [
        ("shell_blocked", "empty"), ("shell_blocked", "sandbox_setup_failed")]


def test_shell_execute_is_not_a_skill_so_other_paths_are_unchanged(wt):
    consent = wt["codec_consent"]
    assert "shell_execute" in consent._LOCAL_CONSENT_REQUIRED
    assert {"chrome_fill", "chrome_click_cdp", "mouse_control"} <= consent._LOCAL_CONSENT_REQUIRED
    # Not destructive in the MCP sense: the set is the local one only.
    assert "shell_execute" not in consent._DESTRUCTIVE_BUILTINS and consent.mcp_allowed("shell_execute")


# ── custom-agent tool lists (part 3) ──

@pytest.fixture
def agents_app(wt, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.agents as ra
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    monkeypatch.setattr(ra, "_AGENTS_DIR", str(agents_dir))
    ag = wt["codec_agents"]
    monkeypatch.setattr(ag, "known_tool_names", lambda: {"web_search", "web_fetch", "shell_execute", "weather"})
    monkeypatch.setattr(ag, "_DROPPED_AUDITED", set())
    app = FastAPI()
    app.include_router(ra.router)
    return TestClient(app), agents_dir


def test_saving_an_agent_with_an_unknown_tool_is_refused_with_a_clear_error(agents_app):
    client, agents_dir = agents_app
    r = client.post("/api/agents/custom/save", json={"name": "Helper", "tools": ["web_search", "steal_keys", "rm_all"]})
    assert r.status_code == 400
    body = r.json()
    assert "Unknown tool name(s): rm_all, steal_keys" in body["error"] and body["unknown_tools"] == ["rm_all", "steal_keys"]
    assert list(agents_dir.iterdir()) == []
    r = client.post("/api/agents/custom/save", json={"name": "Helper", "tools": "web_search"})
    assert r.status_code == 400 and "list of tool names" in r.json()["error"]
    r = client.post("/api/agents/custom/save", json={"name": "Helper", "tools": [{"x": 1}]})
    assert r.status_code == 400


def test_saving_an_agent_with_known_tools_still_works(agents_app):
    client, agents_dir = agents_app
    r = client.post("/api/agents/custom/save", json={"name": "Helper", "role": "R", "tools": ["web_search", "shell_execute"]})
    assert r.status_code == 200 and r.json()["saved"] is True
    assert json.loads((agents_dir / "helper.json").read_text())["tools"] == ["web_search", "shell_execute"]
    assert client.post("/api/agents/custom/save", json={"name": "NoTools"}).status_code == 200


def test_an_agent_saved_earlier_loads_with_unknown_tools_left_out_and_one_audit_line(agents_app, audit_log, monkeypatch):
    client, agents_dir = agents_app
    (agents_dir / "old.json").write_text(json.dumps(
        {"id": "old", "name": "Old", "role": "R", "tools": ["web_search", "gone_skill", "weather", "x" * 500]}))
    for _ in range(3):
        r = client.get("/api/agents/custom/list")
        assert r.status_code == 200
        (a,) = r.json()["agents"]
        assert a["tools"] == ["web_search", "weather"] and a["name"] == "Old"
    import routes.palette as pal
    import routes._shared as shared
    monkeypatch.setattr(shared, "_AGENTS_DIR", str(agents_dir))
    assert pal._agents()[0]["tools"] == ["web_search", "weather"]
    recs = [r for r in _records(audit_log) if r["event"] == "custom_agent_tools_dropped"]
    assert len(recs) == 1
    assert recs[0]["extra"] == {**recs[0]["extra"], "agent_id": "old", "dropped": 2, "kept": 2}
    assert "gone_skill" not in audit_log.read_text()
    # the saved file itself is left as it was (a skill that comes back is used again)
    assert json.loads((agents_dir / "old.json").read_text())["tools"][1] == "gone_skill"


def test_the_known_tool_names_are_the_builtins_and_the_skills(wt):
    names = wt["codec_agents"].known_tool_names()
    assert {"shell_execute", "web_search", "file_read"} <= names and "weather" in names


# ── the real sandbox (this Mac) ──

def _user_temp_dir():
    """macOS's per-user temp folder (under /private/var/folders), whatever
    TMPDIR says: the shell profile may write /private/tmp, so the test home
    must not live there."""
    base = subprocess.check_output(["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"], text=True).strip()
    base = os.path.realpath(base)
    assert base.startswith("/private/var/folders/"), base
    return base


@pytest.fixture
def box(wt, audit_log):
    root = os.path.realpath(tempfile.mkdtemp(prefix="crew-shell-test-", dir=_user_temp_dir()))
    home = Path(root) / "home"
    (home / ".ssh").mkdir(parents=True)
    canary = "CANARY-" + secrets.token_hex(8)
    (home / ".ssh" / "id_canary").write_text(canary + "\n")
    (home / "victim.txt").write_text("keep me\n")
    out = home / "codec-workspace" / "agent-shell"
    prof = home / ".codec"
    ag = wt["codec_agents"]

    def run(cmd):
        return ag._shell_execute(cmd, home=str(home), output_dir=str(out), profile_dir=str(prof))

    try:
        yield {"run": run, "home": home, "out": out, "prof": prof, "canary": canary, "root": Path(root)}
    finally:
        shutil.rmtree(root, ignore_errors=True)


@needs_sandbox
def test_echo_and_ls_still_run(box):
    r = box["run"](f"echo hello-from-the-sandbox; ls {box['home']}; pwd; ls /nope 2>/dev/null; echo x >/dev/null; echo devnull-ok")
    assert "hello-from-the-sandbox" in r and "codec-workspace" in r and "victim.txt" in r and "devnull-ok" in r
    assert str(box["out"]) in r                      # the working folder is the output folder
    assert "STDERR" not in r


@needs_sandbox
def test_reading_the_canary_under_home_ssh_is_refused_by_the_kernel(box, asks):
    assert box["canary"] in (box["home"] / ".ssh" / "id_canary").read_text()   # control: the file is there
    # Not flagged by is_dangerous (it is a typo-catcher): only the kernel stops it.
    r = box["run"]("d=.ss; cat $HOME/${d}h/id_canary; ls $HOME/${d}h")
    assert box["canary"] not in r and r.count("Operation not permitted") >= 2 and asks["calls"] == []
    # Flagged and approved by the owner: still refused by the kernel.
    asks["answer"] = "codec"
    r = box["run"](f"cat {box['home']}/.ssh/id_canary")
    assert len(asks["calls"]) == 1 and box["canary"] not in r and "Operation not permitted" in r


@needs_sandbox
def test_an_outbound_connection_to_a_local_listener_is_refused(box):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    srv.settimeout(1.0)
    port = srv.getsockname()[1]
    probe = f"/usr/bin/nc -v -z -w 2 127.0.0.1 {port}; echo nc_exit=$?"
    try:
        # Control: the same command outside the sandbox reaches the listener.
        subprocess.run(["/bin/sh", "-c", probe], capture_output=True, timeout=10)
        conn, _ = srv.accept()
        conn.close()
        r = box["run"](probe)
        assert "nc_exit=1" in r and "Operation not permitted" in r, r
        with pytest.raises(socket.timeout):
            srv.accept()
    finally:
        srv.close()


@needs_sandbox
def test_writes_land_only_in_the_output_folder_and_tmp(box):
    tmp_probe = Path(f"/private/tmp/crew-shell-test-{os.getpid()}-{secrets.token_hex(4)}.txt")
    outside = box["home"] / "outside.txt"
    sibling = box["root"] / "sibling.txt"
    try:
        r = box["run"](f"echo made > made.txt; echo t > {tmp_probe}; echo no > {outside}; echo no > {sibling}; "
                       f"echo gone > {box['home']}/victim.txt; mkdir {box['home']}/newdir")
        assert (box["out"] / "made.txt").read_text() == "made\n" and tmp_probe.read_text() == "t\n"
        assert not outside.exists() and not sibling.exists() and not (box["home"] / "newdir").exists()
        assert (box["home"] / "victim.txt").read_text() == "keep me\n"
        assert r.count("Operation not permitted") >= 4 and "[sandbox] Something was refused" in r
    finally:
        tmp_probe.unlink(missing_ok=True)


@needs_sandbox
def test_an_approved_flagged_delete_outside_the_output_folder_still_changes_nothing(box, asks, audit_log):
    asks["answer"] = "codec"
    r = box["run"](f"rm -rf {box['home']}/victim.txt")
    assert len(asks["calls"]) == 1 and "Operation not permitted" in r
    assert (box["home"] / "victim.txt").read_text() == "keep me\n"
    recs = [x for x in _records(audit_log) if x["event"] == "local_consent_decision"]
    assert [x["extra"]["decision"] for x in recs] == ["allowed"] and "victim" not in audit_log.read_text()


@needs_sandbox
def test_the_command_gets_no_secrets_from_the_environment_and_no_input(box, monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "leak-me")
    r = box["run"]("env | grep -c leak-me; read x; echo got=[$x]; echo home=$HOME")
    assert r.splitlines()[0] == "0" and "got=[]" in r and f"home={box['home']}" in r


@needs_sandbox
def test_a_command_that_runs_too_long_is_stopped_with_everything_it_started(box, wt, monkeypatch):
    monkeypatch.setattr(wt["codec_agents"], "_SHELL_TIMEOUT_SECONDS", 1.5)
    t0 = time.monotonic()
    r = box["run"]("sleep 31.7 & sleep 31.7")
    assert "timed out" in r and time.monotonic() - t0 < 10
    time.sleep(0.3)
    assert subprocess.run(["/usr/bin/pgrep", "-f", "sleep 31.7"], capture_output=True).returncode != 0


@needs_sandbox
def test_the_per_call_profile_file_is_removed_and_lives_outside_the_writable_folders(box):
    box["run"]("echo hi")
    assert box["prof"].is_dir() and list(box["prof"].glob("crew_shell-*.sb")) == []
    assert not list(box["out"].glob("*.sb"))
