"""Buyer Mac: a model switch restarts the model LaunchAgent (P-039 part 2, E-15).

docs/LAUNCHD-MODEL-RESTART-DESIGN.md. On launchd, `set_active` restarts exactly one job,
`ai.avadigital.codec.qwen3.6`, with `launchctl kickstart -k`; under PM2 (the Studio) it calls
`restart_server()` exactly as before. Every launchctl answer here is a fixture (an injected
runner, or a fake script in a temp folder); an autouse guard fails any test that would run the
real launchctl or pm2. Config and the services list are temp files; HOME is never faked.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import codec_models

REPO = Path(__file__).resolve().parents[1]
LABEL = "ai.avadigital.codec.qwen3.6"
UID = 501
TARGET = f"gui/{UID}/{LABEL}"


@pytest.fixture(autouse=True)
def no_real_launchctl_or_pm2(monkeypatch):
    real_run = subprocess.run

    def guarded(args, *a, **kw):
        argv0 = str(args[0] if isinstance(args, (list, tuple)) else args)
        if argv0 == "/bin/launchctl" or os.path.basename(argv0) in ("launchctl", "pm2"):
            raise AssertionError(f"a test tried to run the real {argv0}: {args}")
        return real_run(args, *a, **kw)

    monkeypatch.setattr(subprocess, "run", guarded)
    monkeypatch.setattr(codec_models.shutil, "which", lambda _name: None)
    monkeypatch.setattr(codec_models.time, "sleep", lambda _s: None)


@pytest.fixture
def audits(monkeypatch):
    import codec_audit
    lines = []
    monkeypatch.setattr(codec_audit, "audit", lambda *a, **kw: lines.append(kw))
    return lines


SERVICES = [
    {"name": "codec-dashboard", "script": "python3", "args": "codec_dashboard.py", "autorestart": True},
    {"name": "qwen3.6", "script": "scripts/start_model_server.sh", "interpreter": "bash",
     "restart_delay": 10000, "max_restarts": 5, "autorestart": True},
    {"name": "whisper-stt", "script": "python3", "args": "whisper_server.py", "autorestart": True},
]


def _services(tmp_path, apps=SERVICES, name="services.json"):
    path = tmp_path / name
    path.write_text(apps if isinstance(apps, str) else json.dumps(apps))
    return str(path)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """A buyer Mac's config. `model_pm2_process` is a trap: launchd must never read it."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm_model": "mlx-community/A", "supervisor": "launchd",
                                "llm_base_url": "http://127.0.0.1:8083/v1",
                                "model_pm2_process": "codec-dashboard"}))
    monkeypatch.setattr(codec_models, "CONFIG_PATH", str(path))
    monkeypatch.setattr(codec_models, "SERVICES_JSON", _services(tmp_path))
    monkeypatch.setattr(codec_models, "discover_local",
                        lambda: [{"id": "mlx-community/A", "label": "A", "size_gb": 1.0},
                                 {"id": "mlx-community/B", "label": "B", "size_gb": 2.0}])
    return path


def _print(target, pid):
    """`launchctl print gui/501/<label>`, with a nested block whose own `state` must be ignored."""
    state = "running" if pid else "not running"
    lines = [f"{target} = {{", "\tactive count = 1", "\ttype = LaunchAgent", f"\tstate = {state}",
             "\tevent triggers = {", "\t\tx => {", "\t\t\tstate = active", "\t\t}", "\t}"]
    if pid:
        lines.append(f"\tpid = {pid}")
    lines += ["\tproperties = keepalive | runatload", "}"]
    return "\n".join(lines) + "\n"


class FakeLaunchctl:
    """Records argv. `print` answers the job; each `kickstart` gives it a new pid (or not)."""

    def __init__(self, *, pid=111, loaded=True, kick_rc=0, kick_err="", respawn=True, on_kick=None):
        self.calls, self.pid, self.loaded = [], pid, loaded
        self.kick_rc, self.kick_err, self.respawn, self.on_kick = kick_rc, kick_err, respawn, on_kick

    def __call__(self, args):
        self.calls.append(list(args))
        assert args[0] == "/bin/launchctl"
        if args[1] == "print":
            if not self.loaded:
                return 113, "", f"Could not find service \"{args[2]}\" in domain"
            return 0, _print(args[2], self.pid), ""
        if args[1] == "kickstart":
            if self.on_kick:
                self.on_kick()
            if self.kick_rc == 0 and self.respawn:
                self.pid = (self.pid or 0) + 111
            return self.kick_rc, "", self.kick_err
        raise AssertionError(f"unexpected launchctl verb: {args}")

    def verbs(self):
        return [c[1] for c in self.calls]

    def kicks(self):
        return [c for c in self.calls if c[1] == "kickstart"]


# ── the label ───────────────────────────────────────────────────────────────

def test_label_comes_from_the_shipped_service_list(tmp_path):
    assert codec_models.LAUNCHD_MODEL_LABEL == LABEL
    assert codec_models.launchd_model_label(_services(tmp_path)) == (LABEL, "")
    wrapped = _services(tmp_path, {"apps": SERVICES}, "wrapped.json")
    assert codec_models.launchd_model_label(wrapped) == (LABEL, ""), "the generator's {apps: [...]} form"


@pytest.mark.parametrize("apps, reason", [
    (None, "no_service_list"),                                   # no file
    ("{broken", "no_service_list"),
    ('"a string"', "no_service_list"),
    ([a for a in SERVICES if a["name"] != "qwen3.6"], "not_model_label"),   # no model server
    (SERVICES + [{"name": "qwen-two", "script": "scripts/start_model_server.sh"}], "not_model_label"),
    ([{"name": "codec-dashboard", "script": "scripts/start_model_server.sh"}], "not_model_label"),
    ([{"name": "qwen3.6/../codec-dashboard", "script": "scripts/start_model_server.sh"}], "not_model_label"),
    ([{"name": "qwen3.6 x", "script": "scripts/start_model_server.sh"}], "not_model_label"),
    ([{"name": ["qwen3.6"], "script": "scripts/start_model_server.sh"}], "not_model_label"),
])
def test_label_refusals(tmp_path, apps, reason):
    path = str(tmp_path / "missing.json") if apps is None else _services(tmp_path, apps)
    assert codec_models.launchd_model_label(path) == (None, reason)


def test_label_matches_the_ecosystem_and_the_plist_generator():
    """The one service that runs the model launcher is qwen3.6, and the generator labels it the same."""
    eco = (REPO / "ecosystem.config.js").read_text()
    pairs = re.findall(r'name:\s*"([^"]+)",\s*script:\s*"([^"]+)"', eco)
    assert [n for n, s in pairs if s == "scripts/start_model_server.sh"] == ["qwen3.6"]
    assert codec_models.DEFAULT_PM2_PROCESS == "qwen3.6"
    spec = importlib.util.spec_from_file_location(
        "gen_la", REPO / "packaging" / "macos" / "launchd" / "generate_launchagents.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    label, plist = gen.pm2_app_to_launchd(SERVICES[1])
    assert label == LABEL and plist["KeepAlive"] is True
    import codec_supervisor
    assert codec_models._LAUNCHD_LABEL_PREFIX == codec_supervisor.LABEL_PREFIX
    assert codec_models._LAUNCHCTL == codec_supervisor.LAUNCHCTL == "/bin/launchctl"


def test_services_json_is_found_next_to_the_app_folder():
    """In the .app this file runs from Contents/Resources/app; build_app.sh writes Resources/services.json."""
    here = os.path.dirname(os.path.abspath(codec_models.__file__))
    assert codec_models.SERVICES_JSON == os.path.join(os.path.dirname(here), "services.json")


# ── the restart ─────────────────────────────────────────────────────────────

def test_restart_kickstarts_exactly_the_model_label(cfg, monkeypatch, audits):
    fake = FakeLaunchctl(pid=111)
    monkeypatch.setattr(codec_models, "_is_listening", lambda h, p, **kw: (h, p) == ("127.0.0.1", 8083))
    ok, detail = codec_models.restart_launchd(run=fake, uid=UID)
    assert ok is True and detail.startswith(f"{LABEL} kickstart -k, serving in"), detail
    assert fake.kicks() == [["/bin/launchctl", "kickstart", "-k", TARGET]], "one kickstart, one label"
    assert set(fake.verbs()) == {"print", "kickstart"}, "never bootout, unload, remove, kill or stop"
    assert all(c[-1] == TARGET for c in fake.calls), "config's model_pm2_process is never used"
    assert fake.verbs()[0] == "print", "launchd must know the job before anything is started"
    assert len(audits) == 1
    line = audits[0]
    assert line["event"] == "model_server_restarted" and line["source"] == "codec-models"
    assert line["outcome"] == "ok" and line["level"] == "info"
    assert set(line["extra"]) == {"supervisor", "label", "result", "reason", "seconds"}
    assert line["extra"]["label"] == LABEL and line["extra"]["result"] == "ok"
    assert "mlx-community" not in json.dumps(line), "metadata only, no model ids"


def test_restart_waits_for_a_new_pid_before_trusting_the_port(cfg, monkeypatch, audits):
    """The old copy still answers on the port until launchd has replaced it: a port alone is not ready."""
    fake = FakeLaunchctl(pid=111)
    reads = []
    real = fake.__call__

    def slow(args):
        if args[1] == "print" and fake.kicks() and len(reads) < 3:
            reads.append(args)
            fake.calls.append(list(args))
            return 0, _print(args[2], 111), ""      # still the old process
        return real(args)

    monkeypatch.setattr(codec_models, "_is_listening", lambda h, p, **kw: True)   # old port open
    ok, _ = codec_models.restart_launchd(run=slow, uid=UID)
    assert ok is True and len(reads) == 3
    assert len(fake.kicks()) == 1


def test_restart_starts_a_job_that_was_not_running(cfg, monkeypatch, audits):
    fake = FakeLaunchctl(pid=None)
    monkeypatch.setattr(codec_models, "_is_listening", lambda h, p, **kw: True)
    ok, _ = codec_models.restart_launchd(run=fake, uid=UID)
    assert ok is True and len(fake.kicks()) == 1


def test_restart_refuses_a_label_launchd_does_not_know(cfg, monkeypatch, audits):
    fake = FakeLaunchctl(loaded=False)
    ok, detail = codec_models.restart_launchd(run=fake, uid=UID)
    assert ok is False and "not loaded" in detail and "switching in place" in detail
    assert fake.verbs() == ["print"], "nothing is started"
    assert [a["extra"]["reason"] for a in audits] == ["not_loaded"]
    assert audits[0]["extra"]["result"] == "refused" and audits[0]["outcome"] == "warning"


def test_restart_refuses_without_a_good_service_list(cfg, monkeypatch, tmp_path, audits):
    bad = _services(tmp_path, [{"name": "codec-dashboard", "script": "scripts/start_model_server.sh"}],
                    "bad.json")
    fake = FakeLaunchctl()
    ok, detail = codec_models.restart_launchd(run=fake, uid=UID, services_path=bad)
    assert ok is False and "switching in place" in detail
    assert fake.calls == [], "no launchctl call at all"
    assert audits[0]["extra"] == {"supervisor": "launchd", "label": "", "result": "refused",
                                  "reason": "not_model_label", "seconds": 0.0}


def test_restart_reports_a_failed_kickstart(cfg, monkeypatch, audits):
    fake = FakeLaunchctl(kick_rc=5, kick_err="Could not kickstart service\n: Operation not permitted")
    ok, detail = codec_models.restart_launchd(run=fake, uid=UID)
    assert ok is False and "exited 5" in detail and "Operation not permitted" in detail
    assert fake.verbs() == ["print", "kickstart"]
    assert [(a["extra"]["result"], a["extra"]["reason"], a["outcome"]) for a in audits] == [
        ("failed", "kickstart_failed", "error")]


def test_restart_reports_no_new_process_and_kicks_once(cfg, monkeypatch, audits):
    monkeypatch.setattr(codec_models, "_RESTART_RESPAWN_TIMEOUT", 0.01)
    monkeypatch.setattr(codec_models, "_is_listening", lambda h, p, **kw: True)
    fake = FakeLaunchctl(respawn=False)
    ok, detail = codec_models.restart_launchd(run=fake, uid=UID)
    assert ok is False and "did not come back" in detail
    assert len(fake.kicks()) == 1, "never kicks a second time"
    assert [a["extra"]["reason"] for a in audits] == ["no_new_process"]


def test_restart_reports_a_port_that_never_opens(cfg, monkeypatch, audits):
    monkeypatch.setattr(codec_models, "_is_listening", lambda h, p, **kw: False)
    fake = FakeLaunchctl()
    ok, detail = codec_models.restart_launchd(ready_timeout=0.01, run=fake, uid=UID)
    assert ok is False and "127.0.0.1:8083 never opened" in detail
    assert [a["extra"]["reason"] for a in audits] == ["not_ready"]


def test_restart_runs_a_launchctl_program_with_one_argv_list(cfg, monkeypatch, tmp_path, audits):
    """The real subprocess path, against a fake launchctl script: each argument arrives on its own."""
    fake = tmp_path / "fake-launchctl"
    fake.write_text(
        "#!/bin/sh\n"
        'd="$(dirname "$0")"\n'
        'for a in "$@"; do printf "[%s]" "$a" >> "$d/argv.log"; done; echo >> "$d/argv.log"\n'
        'case "$1" in\n'
        '  print) if [ -f "$d/kicked" ]; then pid=222; else pid=111; fi\n'
        '         printf "%s = {\\n\\tstate = running\\n\\tpid = %s\\n}\\n" "$2" "$pid" ;;\n'
        '  kickstart) : > "$d/kicked" ;;\n'
        '  *) echo "unexpected $1" >&2; exit 64 ;;\n'
        "esac\n")
    fake.chmod(0o755)
    monkeypatch.setattr(codec_models, "_LAUNCHCTL", str(fake))
    monkeypatch.setattr(codec_models, "_is_listening", lambda h, p, **kw: True)
    ok, detail = codec_models.restart_launchd(uid=UID)
    assert ok is True, detail
    lines = (tmp_path / "argv.log").read_text().splitlines()
    assert lines[0] == f"[print][{TARGET}]"
    assert lines[1] == f"[kickstart][-k][{TARGET}]"
    assert set(lines[2:]) == {f"[print][{TARGET}]"}


# ── set_active on each supervisor ───────────────────────────────────────────

def _cfg_model(cfg):
    return json.loads(cfg.read_text())["llm_model"]


def test_switch_on_launchd_restarts_the_launch_agent_after_writing_config(cfg, monkeypatch, audits):
    seen = []
    fake = FakeLaunchctl(on_kick=lambda: seen.append(("kick", _cfg_model(cfg))))
    monkeypatch.setattr(codec_models, "_launchctl", fake)
    monkeypatch.setattr(codec_models, "_is_listening", lambda h, p, **kw: True)
    monkeypatch.setattr(codec_models, "restart_server",
                        lambda *a, **kw: pytest.fail("PM2's restart on a launchd Mac"))
    monkeypatch.setattr(codec_models, "probe", lambda m, **kw: (seen.append(("probe", m)), (True, "ok"))[1])
    r = codec_models.set_active("mlx-community/B")
    assert r["ok"] and r["restarted"] is True and "kickstart -k" in r["restart_detail"]
    assert seen == [("kick", "mlx-community/B"), ("probe", "mlx-community/B")]
    assert [k[-1] for k in fake.kicks()] == [f"gui/{os.getuid()}/{LABEL}"]


def test_failed_switch_on_launchd_kicks_again_with_the_old_model(cfg, monkeypatch, audits):
    seen = []
    fake = FakeLaunchctl(on_kick=lambda: seen.append(("kick", _cfg_model(cfg))))
    monkeypatch.setattr(codec_models, "_launchctl", fake)
    monkeypatch.setattr(codec_models, "_is_listening", lambda h, p, **kw: True)
    monkeypatch.setattr(codec_models, "probe", lambda m, **kw: (
        seen.append(("probe", m)), (False, "HTTP 500") if m.endswith("B") else (True, "reloaded"))[1])
    r = codec_models.set_active("mlx-community/B")
    assert r["ok"] is False and r["active"] == "mlx-community/A" and r["reverted"] is True
    assert seen == [("kick", "mlx-community/B"), ("probe", "mlx-community/B"),
                    ("kick", "mlx-community/A"), ("probe", "mlx-community/A")]
    assert len(audits) >= 2 and {a["extra"]["result"] for a in audits
                                 if a["event"] == "model_server_restarted"} == {"ok"}


def test_switch_on_launchd_survives_a_refused_restart(cfg, monkeypatch, audits):
    """No model LaunchAgent: the switch goes on in place, as a missing PM2 does today."""
    fake = FakeLaunchctl(loaded=False)
    monkeypatch.setattr(codec_models, "_launchctl", fake)
    monkeypatch.setattr(codec_models, "probe", lambda m, **kw: (True, "loaded in place"))
    r = codec_models.set_active("mlx-community/B")
    assert r["ok"] and r["restarted"] is False and "switching in place" in r["restart_detail"]
    assert _cfg_model(cfg) == "mlx-community/B" and fake.kicks() == []


def test_image_job_stops_the_switch_before_any_launchctl_call(cfg, monkeypatch, audits):
    fake = FakeLaunchctl()
    monkeypatch.setattr(codec_models, "_launchctl", fake)
    monkeypatch.setattr(codec_models, "_image_busy", lambda: "Create image is running")
    monkeypatch.setattr(codec_models, "probe", lambda m, **kw: pytest.fail("no load during an image job"))
    r = codec_models.set_active("mlx-community/B")
    assert r == {"ok": False, "error": "Create image is running", "active": "mlx-community/A"}
    assert fake.calls == [] and audits == [] and _cfg_model(cfg) == "mlx-community/A"


def test_unverified_switch_on_launchd_does_not_restart(cfg, monkeypatch, audits):
    fake = FakeLaunchctl()
    monkeypatch.setattr(codec_models, "_launchctl", fake)
    r = codec_models.set_active("mlx-community/B", verify=False)
    assert r["ok"] and fake.calls == []


def test_setup_restart_on_launchd_stays_in_place(cfg, monkeypatch, audits):
    """codec_setup calls restart_server() itself; on launchd that is still the in-place answer."""
    fake = FakeLaunchctl()
    monkeypatch.setattr(codec_models, "_launchctl", fake)
    assert codec_models.restart_server() == (False, "pm2 not on PATH — switching in place")
    assert fake.calls == []
    assert "codec_models.restart_server()" in (REPO / "codec_setup.py").read_text()


def test_switch_on_pm2_calls_restart_server_as_before(tmp_path, monkeypatch, audits):
    """The Studio: no `supervisor` in config, a checkout, so PM2; launchd is never touched."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm_model": "mlx-community/A", "llm_base_url": "http://x/v1"}))
    monkeypatch.setattr(codec_models, "CONFIG_PATH", str(path))
    monkeypatch.setattr(codec_models, "discover_local",
                        lambda: [{"id": "mlx-community/A"}, {"id": "mlx-community/B"}])
    assert codec_models._supervisor_kind() == "pm2"
    calls = []
    monkeypatch.setattr(codec_models, "restart_server",
                        lambda *a, **kw: (calls.append((a, kw)), (True, "restarted"))[1])
    monkeypatch.setattr(codec_models, "restart_launchd", lambda *a, **kw: pytest.fail("launchd on PM2"))
    monkeypatch.setattr(codec_models, "_launchctl", lambda *a: pytest.fail("launchctl on PM2"))
    monkeypatch.setattr(codec_models, "probe", lambda m, **kw: (True, "ok"))
    r = codec_models.set_active("mlx-community/B")
    assert r["ok"] and r["restart_detail"] == "restarted"
    assert calls == [((), {})], "restart_server() with no arguments, exactly as before"
    assert audits and all(a["event"] != "model_server_restarted" for a in audits)


def test_pm2_restart_commands_are_pinned(tmp_path, monkeypatch):
    """The Studio's stop-then-start: pm2 stop qwen3.6, wait, pm2 restart qwen3.6 --update-env."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm_model": "mlx-community/A"}))
    monkeypatch.setattr(codec_models, "CONFIG_PATH", str(path))
    monkeypatch.setattr(codec_models.shutil, "which", lambda _n: "/usr/bin/pm2")
    state, calls = {"up": False}, []

    def fake_run(args, *a, **kw):
        calls.append(list(args))
        assert args[0] == "/usr/bin/pm2"
        if args[1:] == ["jlist"]:
            procs = [{"name": "qwen3.6", "pid": 222, "pm2_env": {"status": "online"}}] if state["up"] else []
            return SimpleNamespace(returncode=0, stdout=json.dumps(procs), stderr="")
        if args[1] == "restart":
            state["up"] = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(codec_models.subprocess, "run", fake_run)
    monkeypatch.setattr(codec_models, "_is_listening", lambda h, p, **kw: state["up"])
    ok, detail = codec_models.restart_for_switch()
    assert ok is True and detail.startswith("qwen3.6 stop-then-start, serving in")
    assert [c for c in calls if c[1] != "jlist"] == [["/usr/bin/pm2", "stop", "qwen3.6"],
                                                     ["/usr/bin/pm2", "restart", "qwen3.6", "--update-env"]]


# restart_server and _pm2_pid are the Studio's PM2 path (AGENTS.md section 10). This change must
# leave them byte for byte as they were; a later change to them needs the owner's word and a new
# hash here, in the same PR.
PM2_SOURCE_SHA256 = {
    "restart_server": "2c1dff4759faca72edd849c5c2df94700ec137faaa1c8516e1f62d149b3bd145",
    "_pm2_pid": "620509e8f0e5ca7cf9fab5cb93b3062c49b535713e2acaf3f1cb07509c993eb2",
}


@pytest.mark.parametrize("name", sorted(PM2_SOURCE_SHA256))
def test_pm2_path_source_is_unchanged(name):
    src = inspect.getsource(getattr(codec_models, name))
    assert hashlib.sha256(src.encode()).hexdigest() == PM2_SOURCE_SHA256[name], \
        f"{name} is the Studio's PM2 restart path (section 10): changing it needs the owner's word"
