"""Doctor: one read-only check of this Mac (P-022, docs/DOCTOR-DESIGN.md).

Every path is a temp folder given to codec_doctor through a Paths object: never a fake HOME, never the
live ~/.codec. PM2, lsof, the model server and the macOS permission reads are replaced by fakes, except
in the two tests that call a local socket and the Mac's own permission reads (which cannot prompt).
Fixture keys are built at run time, so this file holds no token-shaped text.
"""
from __future__ import annotations

import http.server
import io
import json
import os
import re
import runpy
import socket
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import codec_doctor as cd

REPO = Path(__file__).resolve().parent.parent
NOW = 1_800_000_000.0

# Fixture keys, assembled so no literal token sits in the repo.
ANTHROPIC = "sk-" + "ant-" + "api03-" + "Q7" * 20
TELEGRAM = "1234" + "56789:" + "AAH" + "x9" * 16
GITHUB = "gh" + "p_" + "Zr4" * 12
CF_TOKEN = "cfut" + "Lq83kd" * 6
OAUTH_AT = "codec" + "_at_" + "ab12" * 16
CLIENT_SECRET = "Pz" + "w8Tq" * 8
PLANTED = [ANTHROPIC, TELEGRAM, GITHUB, CF_TOKEN, OAUTH_AT, CLIENT_SECRET]

# The search the zip must pass, written here rather than taken from codec_doctor.
SHAPES = [re.compile(p) for p in (r"sk-ant-[A-Za-z0-9_-]{16,}", r"\bgh[pousr]_[A-Za-z0-9]{30,}",
                                  r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b", r"codec_at_[0-9a-f]{16,}",
                                  r"(?i)bearer\s+[A-Za-z0-9._-]{16,}", r"\b[0-9a-f]{32,}\b")]


def _shape_hits(text: str) -> int:
    return sum(len(p.findall(text)) for p in SHAPES)


def _write(path: Path, data, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    path.chmod(mode)
    return path


@pytest.fixture
def mac(tmp_path, monkeypatch):
    home = tmp_path / "home"  # only for the report's display and scrubbing; HOME is not changed
    codec = home / ".codec"
    codec.mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    p = cd.Paths(home=str(home), codec_dir=str(codec), zshrc=str(home / ".zshrc"), pm2_home=str(home / ".pm2"),
                 app_logs=str(home / "Library" / "Logs" / "CODEC"), repo=str(repo),
                 health_dir=str(codec / "health"), messages_db=str(home / "Library" / "Messages" / "chat.db"))
    calls = {"model": [], "run": []}
    monkeypatch.setattr(cd, "pm2_procs", lambda paths: None)
    monkeypatch.setattr(cd, "listeners", lambda: [])
    monkeypatch.setattr(cd, "model_answers", lambda base, timeout=3: calls["model"].append(base) or "up")
    monkeypatch.setattr(cd, "_on_mac", lambda: True)
    monkeypatch.setattr(cd, "permission_states", lambda paths: {
        "screen": True, "accessibility": True, "full_disk": True, "microphone": True, "automation": True,
        "_automation": {"Reminders": "allowed"}})
    return SimpleNamespace(p=p, home=home, codec=codec, repo=repo, tmp=tmp_path, calls=calls)


def _by_id(report: dict) -> dict:
    return {c["id"]: c for c in report["checks"]}


def _plant_everything(mac) -> None:
    """Every leftover the doctor looks for, with real-looking keys and home-folder paths in the files."""
    _write(mac.codec / "config.json", {"llm_base_url": "http://localhost:8083/v1", "llm_api_key": ANTHROPIC})
    _write(mac.codec / "config.json.bak-1", {"llm_api_key": ANTHROPIC, "telegram": {"bot_token": TELEGRAM,
           "chat_id": "123"}, "extra_models": [{"name": "cloud", "api_key": CF_TOKEN, "key_slot": "x" * 12}],
           "skills_dir": str(mac.home / "codec-repo" / "skills")}, 0o644)
    _write(mac.codec / "oauth_state.json.bak-2", {"access_tokens": {OAUTH_AT: {"token": OAUTH_AT, "scopes": []}},
                                                  "clients": {"client-a": {"client_secret": CLIENT_SECRET,
                                                                           "token_endpoint_auth_method": "client_secret_post"}}})
    _write(mac.codec / "backups" / "config-pre-swap.json", {"llm_api_key": "not-needed", "telegram": {}})
    _write(mac.codec / "config.json.lock", "")
    _write(mac.codec / "mcp_servers.json", {"servers": [{"name": "github", "headers": {
        "Authorization": "Bearer " + GITHUB}}, {"name": "stripe", "headers": {"Authorization": "Bearer <STRIPE_KEY>"}}]},
        0o644)
    _write(Path(mac.p.zshrc), f"export PATH=/opt/homebrew/bin:$PATH\nexport CLOUDFLARE_API_TOKEN={CF_TOKEN}\n"
           f"# export OLD_TOKEN={CF_TOKEN}\nexport OPENAI_API_KEY=$(security find-generic-password -w)\n"
           f"KEYTIMEOUT=1\nexport KEYCHAIN_PATH={mac.home}/Library/Keychains/login.db\n", 0o644)
    for name, mode in (("codec-dashboard-out.log", 0o644), ("codec-dashboard-error.log", 0o644),
                       ("open-codec-out.log", 0o600)):
        _write(Path(mac.p.pm2_home) / "logs" / name, f"chat text {mac.home}/Documents\n", mode)
    _write(Path(mac.p.app_logs) / "codec-telegram.out", "x\n", 0o644)


# ── Leftovers found from fixtures ────────────────────────────────────────────

def test_finds_keys_in_config_and_oauth_copies_and_reports_names_only(mac):
    _plant_everything(mac)
    os.symlink(mac.codec / "config.json.bak-1", mac.codec / "config.json.bak-link")
    c = _by_id(cd.run(mac.p, now=NOW))["config_backups"]
    assert c["status"] == cd.FAIL
    d = c["data"]
    assert (d["files"], d["with_keys"], d["readable_by_others"]) == (3, 2, 1), "the live config, a lock and a link do not count"
    assert {"llm_api_key", "telegram.bot_token", "extra_models[].api_key", "access_tokens", "access_tokens.*.token",
            "clients.client-a.client_secret"} == set(d["fields"])
    text = json.dumps(c)
    assert not any(v in text for v in PLANTED) and "chat_id" not in text and "not-needed" not in text
    assert "Keychain" in c["detail"] and c["fix"].startswith("List them")
    assert "token_endpoint_auth_method" not in text, "a setting about keys is no key"


def test_finds_nothing_in_clean_copies(mac):
    assert _by_id(cd.run(mac.p, now=NOW))["config_backups"]["status"] == cd.OK
    _write(mac.codec / "config.json.bak-3", {"llm_api_key": "", "dashboard_token": "<TOKEN>", "auth_pin_hash": "a" * 64,
                                             "telegram": {"bot_token": "${TELEGRAM_BOT_TOKEN}"}})
    c = _by_id(cd.run(mac.p, now=NOW))["config_backups"]
    assert c["status"] == cd.OK and c["data"]["files"] == 1 and c["data"]["with_keys"] == 0


@pytest.mark.skipif(os.geteuid() == 0, reason="file modes")
def test_finds_mcp_servers_and_zshrc_modes(mac):
    ids = _by_id(cd.run(mac.p, now=NOW))
    assert ids["mcp_servers"]["status"] == cd.OK and ids["zshrc"]["status"] == cd.OK, "neither file exists yet"
    _plant_everything(mac)
    ids = _by_id(cd.run(mac.p, now=NOW))
    assert ids["mcp_servers"]["status"] == cd.FAIL and ids["mcp_servers"]["data"] == {"mode": "0644", "keys": 1}
    assert ids["zshrc"]["status"] == cd.FAIL and ids["zshrc"]["data"] == {"mode": "0644", "key_lines": 1}, \
        "a comment, a $(...) lookup, a short value and a path are not keys"
    assert ids["zshrc"]["fix"] == "chmod 600 ~/.zshrc"
    _write(mac.codec / "mcp_servers.json", {"servers": [{"headers": {"Authorization": "Bearer <GITHUB_TOKEN>"}}]}, 0o644)
    assert _by_id(cd.run(mac.p, now=NOW))["mcp_servers"]["status"] == cd.WARN, "readable, no key yet"
    for path, mode in ((mac.codec / "mcp_servers.json", 0o600), (Path(mac.p.zshrc), 0o600)):
        path.chmod(mode)
    ids = _by_id(cd.run(mac.p, now=NOW))
    assert ids["mcp_servers"]["status"] == cd.OK and ids["zshrc"]["status"] == cd.OK
    Path(mac.p.zshrc).chmod(0o664)
    (mac.codec / "mcp_servers.json").chmod(0o666)
    ids = _by_id(cd.run(mac.p, now=NOW))
    assert ids["zshrc"]["status"] == cd.FAIL and ids["mcp_servers"]["status"] == cd.FAIL, "others can change them"
    Path(mac.p.zshrc).write_text("export PATH=/usr/bin\n")
    Path(mac.p.zshrc).chmod(0o644)
    assert _by_id(cd.run(mac.p, now=NOW))["zshrc"]["status"] == cd.OK, "readable, but it sets no key"


@pytest.mark.skipif(os.geteuid() == 0, reason="file modes")
def test_finds_logs_other_accounts_can_read(mac):
    assert _by_id(cd.run(mac.p, now=NOW))["logs"]["status"] == cd.OK
    _plant_everything(mac)
    c = _by_id(cd.run(mac.p, now=NOW))["logs"]
    assert c["status"] == cd.WARN
    assert c["data"] == {"pm2_files": 3, "pm2_readable_by_others": 2, "app_files": 1, "app_readable_by_others": 1}
    assert c["fix"] == "chmod 600 ~/.pm2/logs/* and chmod 600 ~/Library/Logs/CODEC/*"
    assert "codec-dashboard" not in json.dumps(c), "counts only, no file names"
    for f in list((Path(mac.p.pm2_home) / "logs").iterdir()) + list(Path(mac.p.app_logs).iterdir()):
        f.chmod(0o600)
    assert _by_id(cd.run(mac.p, now=NOW))["logs"]["status"] == cd.OK


def test_finds_codec_ports_on_every_interface(mac, monkeypatch):
    rows = [(10, "*", 8090), (11, "127.0.0.1", 8083), (12, "*", 5000), (13, "*", 5301), (14, "[::]", 7000),
            (15, "0.0.0.0", 9555)]
    monkeypatch.setattr(cd, "listeners", lambda: rows)
    monkeypatch.setattr(cd, "pm2_procs", lambda paths: [{"name": "codec-mcp-http", "pid": 15, "status": "online",
                                                          "started": NOW - 9000, "cwd": str(mac.repo)}])
    c = _by_id(cd.run(mac.p, now=NOW))["listeners"]
    assert c["status"] == cd.WARN
    assert c["data"]["codec"] == [{"service": "Vibe preview", "port": 5301}, {"service": "dashboard", "port": 8090},
                                  {"service": "codec-mcp-http", "port": 9555}]
    assert c["data"]["other_count"] == 2 and c["data"]["other_ports"] == [5000, 7000]
    assert "8083" not in c["detail"], "a loopback listener is fine"
    monkeypatch.setattr(cd, "listeners", lambda: [(12, "*", 5000), (11, "127.0.0.1", 8090)])
    assert _by_id(cd.run(mac.p, now=NOW))["listeners"]["status"] == cd.OK
    monkeypatch.setattr(cd, "listeners", lambda: None)
    assert _by_id(cd.run(mac.p, now=NOW))["listeners"]["status"] == cd.INFO, "no lsof: not checked"


def test_finds_listeners_from_lsof_output(monkeypatch):
    seen = []
    monkeypatch.setattr(cd.shutil, "which", lambda name: "/usr/sbin/lsof" if name == "lsof" else None)
    monkeypatch.setattr(cd, "_run", lambda cmd, timeout=15: seen.append(cmd) or
                        "p123\nf4\nn*:8090\np456\nf7\nn127.0.0.1:8083\nn[::1]:8083\np9\nn[::]:7000\nnjunk\n")
    assert cd.listeners() == [(123, "*", 8090), (456, "127.0.0.1", 8083), (456, "[::1]", 8083), (9, "[::]", 7000)]
    assert seen == [["/usr/sbin/lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpn"]]


def _git(repo: Path, lines: list, worktree: bool = False) -> None:
    gd = repo.parent / "gitdir-wt" if worktree else repo / ".git"
    (gd / "logs").mkdir(parents=True)
    (gd / "logs" / "HEAD").write_text("".join(f"{a} {b} Some One <someone@example.com> {ts} +0100\t{msg}\n"
                                              for a, b, ts, msg in lines))
    if worktree:
        (repo / ".git").write_text(f"gitdir: {gd}\n")


def test_finds_services_started_before_the_code_changed(mac, monkeypatch):
    moved = int(NOW) - 3600
    _git(mac.repo, [("0" * 40, "a1b2c3d" + "0" * 33, moved - 86400, "clone"),
                    ("a1b2c3d" + "0" * 33, "f00dbabe" + "1" * 32, moved, "pull --ff-only: Fast-forward")])
    procs = [{"name": "codec-dashboard", "pid": 1, "status": "online", "started": moved - 600, "cwd": str(mac.repo)},
             {"name": "open-codec", "pid": 2, "status": "online", "started": moved + 60, "cwd": str(mac.repo)},
             {"name": "codec-telegram", "pid": 3, "status": "stopped", "started": moved - 600, "cwd": str(mac.repo)},
             {"name": "other-app", "pid": 4, "status": "online", "started": moved - 600, "cwd": str(mac.tmp)},
             {"name": "qwen3.6", "pid": 5, "status": "online", "started": moved - 600, "cwd": str(mac.repo)},
             {"name": "kokoro-82m", "pid": 6, "status": "online", "started": moved - 600, "cwd": str(mac.repo)}]
    monkeypatch.setattr(cd, "pm2_procs", lambda paths: procs)
    c = _by_id(cd.run(mac.p, now=NOW))["code_version"]
    assert c["status"] == cd.WARN and c["data"] == {"head": "f00dbab", "services": 2, "started_before": ["codec-dashboard"]}
    assert c["fix"] == "pm2 restart codec-dashboard", "the model and kokoro-82m run installed packages, not this code"
    assert "someone@example.com" not in json.dumps(c) and "Some One" not in json.dumps(c)
    procs[0]["started"] = moved + 5
    assert _by_id(cd.run(mac.p, now=NOW))["code_version"]["status"] == cd.OK
    monkeypatch.setattr(cd, "pm2_procs", lambda paths: None)
    assert _by_id(cd.run(mac.p, now=NOW))["code_version"]["status"] == cd.INFO, "no PM2: nothing to compare"


def test_finds_the_reflog_of_a_worktree_checkout(tmp_path):
    repo = tmp_path / "wt"
    repo.mkdir()
    _git(repo, [("0" * 40, "c0ffee1" + "2" * 33, 1_700_000_000, "reset: moving to origin/main")], worktree=True)
    assert cd.install_kind(str(repo)) == "git" and cd.head_moved(str(repo)) == ("c0ffee1", 1_700_000_000)
    assert cd.head_moved(str(tmp_path)) is None


def _beat(folder: Path, name: str, ts: float, cadence: float = 60.0, errors: int = 0, stopped: bool = False):
    _write(folder / f"{name}.json", {"ts": ts, "ok": errors == 0, "consecutive_errors": errors,
                                     "last_error_class": "PermissionDenied" if errors else None,
                                     "cadence_s": cadence, "stopped": stopped})


def test_finds_silent_and_failing_health_beats(mac, monkeypatch):
    assert _by_id(cd.run(mac.p, now=NOW))["health_beats"]["status"] == cd.INFO, "no beat yet"
    (mac.repo / ".git").mkdir()  # a checkout: the fix names PM2
    hd = Path(mac.p.health_dir)
    _beat(hd, "codec-telegram", NOW - 3600, cadence=40)
    _beat(hd, "codec-imessage", NOW - 30, errors=25)
    _beat(hd, "codec-autopilot", NOW - 30)
    _beat(hd, "codec-old", NOW - 99999, stopped=True)
    c = _by_id(cd.run(mac.p, now=NOW))["health_beats"]
    assert c["status"] == cd.FAIL
    assert c["data"] == {"silent": ["codec-telegram"], "failing": ["codec-imessage"], "ok": ["codec-autopilot"],
                         "stopped": ["codec-old"]}
    assert c["fix"] == "pm2 restart codec-telegram, then check its log."
    online = [{"name": n, "pid": i, "status": "online", "started": NOW - 9000, "cwd": ""}
              for i, n in enumerate(("codec-imessage", "codec-autopilot"))]
    monkeypatch.setattr(cd, "pm2_procs", lambda paths: online)
    c = _by_id(cd.run(mac.p, now=NOW))["health_beats"]
    assert c["status"] == cd.WARN and c["data"]["stopped"] == ["codec-old", "codec-telegram"], "PM2 does not run it"
    monkeypatch.setattr(cd, "pm2_procs", lambda paths: online + [
        {"name": "codec-telegram", "pid": 9, "status": "online", "started": NOW - 60, "cwd": ""}])
    c = _by_id(cd.run(mac.p, now=NOW))["health_beats"]
    assert "codec-telegram" not in json.dumps(c["data"]), "in its start grace: skipped this time"


def test_finds_the_model_server_down(mac, monkeypatch):
    (mac.repo / ".git").mkdir()  # a checkout: the fix names PM2
    _write(mac.codec / "config.json", {"llm_base_url": "http://localhost:8083/v1"})
    for answer, status in (("refused", cd.FAIL), ("error", cd.FAIL), ("timeout", cd.WARN), ("up", cd.OK)):
        monkeypatch.setattr(cd, "model_answers", lambda base, timeout=3, a=answer: a)
        c = _by_id(cd.run(mac.p, now=NOW))["model_server"]
        assert c["status"] == status, answer
    monkeypatch.setattr(cd, "model_answers", lambda base, timeout=3: "refused")
    monkeypatch.setattr(cd, "pm2_procs", lambda paths: [{"name": "qwen3.6", "pid": 1, "status": "stopped",
                                                          "started": 0.0, "cwd": ""}])
    c = _by_id(cd.run(mac.p, now=NOW))["model_server"]
    assert "Nothing answers on port 8083" in c["detail"] and "PM2 shows qwen3.6 stopped" in c["detail"]
    assert c["fix"] == "pm2 restart qwen3.6 (loading the model takes a few minutes)"
    monkeypatch.setattr(cd, "pm2_procs", lambda paths: [{"name": "qwen3.6", "pid": 1, "status": "online",
                                                          "started": NOW - 40, "cwd": ""}])
    assert _by_id(cd.run(mac.p, now=NOW))["model_server"]["status"] == cd.INFO, "still loading"


def test_finds_nothing_to_check_for_a_cloud_model_and_calls_nothing(mac):
    _write(mac.codec / "config.json", {"llm_base_url": "https://api.example.com/v1"})
    c = _by_id(cd.run(mac.p, now=NOW))["model_server"]
    assert c["status"] == cd.NA and mac.calls["model"] == [] and "example.com" not in c["detail"]


def test_finds_missing_permissions_with_links_and_a_fix(mac, monkeypatch):
    monkeypatch.setattr(cd, "permission_states", lambda paths: {
        "screen": True, "accessibility": None, "full_disk": None, "microphone": False, "automation": False,
        "_automation": {"Messages": "denied", "Reminders": "allowed", "Notes": "not open"}})
    ids = _by_id(cd.run(mac.p, now=NOW))
    mic, auto = ids["perm_microphone"], ids["perm_automation"]
    assert mic["status"] == cd.WARN and mic["url"].endswith("?Privacy_Microphone")
    assert mic["fix"] == "In System Settings > Privacy & Security > Microphone, turn it on for CODEC."
    assert auto["status"] == cd.WARN and "Not allowed: Messages. Allowed: Reminders. Checked when open: Notes." in \
        auto["detail"]
    assert ids["perm_screen"]["status"] == cd.OK and ids["perm_accessibility"]["status"] == cd.INFO
    assert "no Messages database" in ids["perm_full_disk"]["detail"]
    monkeypatch.setattr(cd, "_on_mac", lambda: False)
    assert {c["status"] for c in cd.check_permissions(mac.p)} == {cd.NA}


def test_app_install_code_version_is_na_and_fixes_name_launchctl(mac, monkeypatch):
    app = mac.tmp / "Sovereign AI Workstation.app" / "Contents" / "Resources" / "app"
    app.mkdir(parents=True)
    mac.p.repo = str(app)
    _write(mac.codec / "config.json", {"llm_base_url": "http://127.0.0.1:8083/v1"})
    _beat(Path(mac.p.health_dir), "codec-telegram", NOW - 3600, cadence=40)
    monkeypatch.setattr(cd, "model_answers", lambda base, timeout=3: "refused")
    monkeypatch.setattr(cd, "pm2_procs", lambda paths: [{"name": "my-node-app", "pid": 1, "status": "online",
                                                          "started": NOW - 9000, "cwd": "/x"}])
    r = cd.run(mac.p, now=NOW)
    ids = _by_id(r)
    assert r["about"]["pm2"] is False, "the app's services run under launchd: a PM2 for other work is not asked"
    assert r["about"]["install"] == "app" and ids["code_version"]["status"] == cd.NA
    assert ids["health_beats"]["fix"].startswith("launchctl kickstart -k gui/$(id -u)/ai.avadigital.codec.codec-telegram")
    assert "launchctl kickstart -k gui/$(id -u)/ai.avadigital.codec.qwen3.6" in ids["model_server"]["fix"]
    assert "quit CODEC and open it again" in ids["model_server"]["fix"]
    assert not any("pm2" in c["fix"] for c in r["checks"]), "a buyer's Mac has no PM2"


# ── Read only, never raises ───────────────────────────────────────────────────

def test_pm2_list_only_from_a_live_daemon_and_without_env(tmp_path, monkeypatch):
    calls = []
    jlist = [{"name": "codec-dashboard", "pid": 77, "pm2_env": {"status": "online", "pm_uptime": 1_700_000_000_000,
              "pm_cwd": "/x/codec-repo", "TELEGRAM_BOT_TOKEN": TELEGRAM, "env": {"KEY": ANTHROPIC}}}]
    title = {"now": "python3 -m pytest"}

    def fake_run(cmd, timeout=15):
        calls.append(cmd)
        return title["now"] if "ps" in os.path.basename(cmd[0]) else json.dumps(jlist)

    monkeypatch.setattr(cd, "_pm2_bin", lambda: "/fake/pm2")
    monkeypatch.setattr(cd.shutil, "which", lambda name: "/usr/bin/ps" if name == "ps" else None)  # no /bin/ps
    monkeypatch.setattr(cd, "_run", fake_run)
    p = cd.Paths(home="", codec_dir="", zshrc="", pm2_home=str(tmp_path), app_logs="", repo="", health_dir="",
                 messages_db="")
    assert cd.pm2_procs(p) is None and calls == [], "no pid file: PM2 is not asked (it would start a daemon)"
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    (tmp_path / "pm2.pid").write_text(str(dead.pid))
    assert cd.pm2_procs(p) is None and calls == [], "a dead daemon's pid"
    (tmp_path / "pm2.pid").write_text(str(os.getpid()))
    assert cd.pm2_procs(p) is None and not any(c[0] == "/fake/pm2" for c in calls), \
        "a live pid that another program got after PM2 died: PM2 is not asked"
    calls.clear()
    title["now"] = "PM2 v6.0.14: God Daemon (/x/.pm2)\n"
    procs = cd.pm2_procs(p)
    assert calls[-1] == ["/fake/pm2", "jlist"] and calls[0][1:] == ["-p", str(os.getpid()), "-o", "command="]
    assert procs == [{"name": "codec-dashboard", "pid": 77, "status": "online", "started": 1_700_000_000.0,
                      "cwd": "/x/codec-repo"}], "only five fields; the environment is dropped"


def test_run_never_raises_when_a_check_breaks(mac, monkeypatch):
    def broken(p, **_):
        raise RuntimeError(f"{ANTHROPIC} at {mac.home}")
    monkeypatch.setattr(cd, "CHECKS", [("zshrc", "~/.zshrc", broken)] + [c for c in cd.CHECKS if c[0] != "zshrc"])
    r = cd.run(mac.p, now=NOW)
    z = _by_id(r)["zshrc"]
    assert z["status"] == cd.INFO and z["detail"] == "This check could not run (RuntimeError)."
    assert len(r["checks"]) == 13 and sum(r["summary"].values()) == 13


def test_model_answers_on_a_real_local_socket():
    class Quiet(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(404)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert cd.model_answers(f"http://127.0.0.1:{srv.server_address[1]}/v1") == "up", "any HTTP answer is up"
    finally:
        srv.shutdown()
        srv.server_close()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert cd.model_answers(f"http://127.0.0.1:{port}/v1", timeout=2) == "refused"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS permission reads")
def test_the_permission_reads_answer_on_a_mac_without_prompting():
    src = (REPO / "codec_doctor.py").read_text(encoding="utf-8")
    for prompting in ("requestAccess", "AXIsProcessTrustedWithOptions", "CGRequestScreenCaptureAccess"):
        assert prompting not in src, prompting
    assert "0x2A2A2A2A, 0x2A2A2A2A, False)" in src, "askUserIfNeeded is false"
    assert cd.microphone_ok() in (True, False, None)
    states = cd.automation_states()
    assert list(states) == [n for n, _ in cd.AUTOMATION_TARGETS]
    assert set(states.values()) <= set(cd.AUTOMATION_STATES)
    assert cd.screen_recording_ok() in (True, False) and cd.accessibility_ok() in (True, False)


# ── Redaction: the scrubber, the zip, the command line ────────────────────────

def test_scrub_replaces_the_home_folder_and_token_shapes(tmp_path):
    raw = {"a": f"{ANTHROPIC} {TELEGRAM} Bearer {GITHUB}", f"{tmp_path}/x": [f"{tmp_path}/Documents", OAUTH_AT]}
    assert _shape_hits(json.dumps(raw)) >= 4 and str(tmp_path) in json.dumps(raw), "negative control"
    out = json.dumps(cd.scrub(raw, str(tmp_path)))
    assert _shape_hits(out) == 0 and str(tmp_path) not in out and "~/Documents" in out


def test_the_zip_holds_no_token_shape_and_no_home_path(mac, monkeypatch):
    _plant_everything(mac)
    raw = "".join(f.read_text(errors="replace") for f in mac.home.rglob("*") if f.is_file())
    assert _shape_hits(raw) >= 5 and str(mac.home) in raw, "negative control: the fixtures hold both"
    report = cd.run(mac.p, now=NOW)
    report["checks"].append({"id": "future", "title": "A later check", "status": cd.INFO,
                             "detail": f"{ANTHROPIC} in {mac.home}/.codec", "fix": ""})  # a check that would leak
    data = cd.build_zip(report, str(mac.home))
    z = zipfile.ZipFile(io.BytesIO(data))
    assert sorted(z.namelist()) == ["codec-doctor.json", "codec-doctor.txt"]
    for name in z.namelist():
        text = z.read(name).decode("utf-8")
        assert _shape_hits(text) == 0, name
        assert str(mac.home) not in text and os.path.realpath(mac.home) not in text, name
        assert not any(v in text for v in PLANTED), name
    rep = json.loads(z.read("codec-doctor.json"))
    assert rep["summary"]["FAIL"] >= 3 and "telegram.bot_token" in json.dumps(rep), "names stay, values go"


def test_cli_json_lists_every_check_without_values(mac, monkeypatch, capsys):
    _plant_everything(mac)
    monkeypatch.setattr(cd, "default_paths", lambda repo=None: mac.p)
    monkeypatch.setattr(sys, "argv", ["doctor.py", "--json"])
    with pytest.raises(SystemExit) as stop:
        runpy.run_path(str(REPO / "tools" / "doctor.py"), run_name="__main__")
    out = capsys.readouterr().out
    rep = json.loads(out)
    assert stop.value.code == 1, "a FAIL exits 1"
    assert [c["id"] for c in rep["checks"]] == ["config_backups", "mcp_servers", "zshrc", "logs", "listeners",
                                                "code_version", "health_beats", "model_server", "perm_screen",
                                                "perm_accessibility", "perm_full_disk", "perm_microphone",
                                                "perm_automation"]
    assert rep["permissions_note"].endswith("the dashboard cannot read their grants.")
    assert not any(v in out for v in PLANTED) and str(mac.home) not in out and _shape_hits(out) == 0
    assert rep["checks"][0]["data"]["fields"] and rep["checks"][2]["data"]["key_lines"] == 1, "counts and names"
    for f in [mac.codec / "config.json.bak-1", mac.codec / "oauth_state.json.bak-2", mac.codec / "mcp_servers.json",
              Path(mac.p.zshrc)]:
        f.unlink()
    monkeypatch.setattr(sys, "argv", ["doctor.py"])
    assert cd.main([]) == 0, "no FAIL: exit 0"
    text = capsys.readouterr().out
    assert text.startswith("CODEC doctor") and "FAIL 0" in text
