"""'CODEC is watching' (UI phase 3, P3.7; docs/P3.7-DESIGN.md).

The header's eye pauses the observer through a flag the daemon reads every loop,
shows what it keeps as metadata only, and lists the automatic triggers with mute
and turn off; /api/triggers is the trigger list again and kill/mute reach the
observer when their files change. Nothing here touches ~/.codec: tmp paths.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
SHELL = (REPO / "static" / "codec-shell.js").read_text(encoding="utf-8")
HOME = (REPO / "codec_dashboard.html").read_text(encoding="utf-8")


@pytest.fixture
def obs(tmp_path, monkeypatch):
    import codec_audit
    import codec_observer
    import codec_triggers
    import routes.observer as ro
    import routes.triggers as rt
    monkeypatch.setattr(codec_observer, "_PAUSE_PATH", tmp_path / "observer_paused_until")
    monkeypatch.setattr(codec_observer, "_BUFFER_DISK_PATH", tmp_path / "observer_buffer.json")
    monkeypatch.setattr(codec_triggers, "_KILLED_PATH", tmp_path / "triggers_killed.json")
    monkeypatch.setattr(codec_triggers, "_MUTE_CONFIG_PATH", tmp_path / "triggers.json")
    for name in ("_KILLED_CACHE", "_MUTE_CACHE", "_KILLED_STAMP", "_MUTE_STAMP"):
        monkeypatch.setattr(codec_triggers, name, None)
    audits = []
    monkeypatch.setattr(codec_audit, "log_event", lambda *a, **k: audits.append((a, k)))
    app = FastAPI()
    app.include_router(ro.router)
    app.include_router(rt.router)
    return SimpleNamespace(client=TestClient(app), tmp=tmp_path, o=codec_observer, t=codec_triggers, rt=rt, audits=audits)


def _mirror(path: Path, entries: list, age_s: float = 0) -> None:
    path.write_text(json.dumps({"updated": datetime.now().isoformat(), "entries": entries}))
    if age_s:
        then = time.time() - age_s
        os.utime(path, (then, then))


ENTRY = {"ts": "2026-10-06T10:00:00", "active_window": {"app": "Notes", "pid": 1, "title": "Secret project plan"},
         "screenshot_ocr": "confidential numbers on screen", "ocr_skipped": False,
         "clipboard": {"content_type": "url", "length": 42}, "recent_files": [{"path": "/Users/someone/a.txt"}],
         "idle_seconds": 3.0}


def test_pause_writes_the_flag_and_resume_removes_it(obs):
    c = obs.client
    r = c.post("/api/observer/pause", json={"minutes": 15}).json()
    left = datetime.fromisoformat(r["paused_until"]) - datetime.now()
    assert timedelta(minutes=14) < left <= timedelta(minutes=15)
    assert stat.S_IMODE(os.stat(obs.tmp / "observer_paused_until").st_mode) == 0o600
    assert c.get("/api/observer/state").json()["paused_until"] == r["paused_until"]
    tomorrow = c.post("/api/observer/pause", json={"until": "tomorrow"}).json()["paused_until"]
    assert tomorrow.endswith("T06:00:00") and tomorrow[:10] == (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    for bad in ({"minutes": 30}, {}, {"until": "next week"}):
        assert c.post("/api/observer/pause", json=bad).status_code == 400, bad
    assert c.post("/api/observer/resume").json() == {"ok": True}
    assert not (obs.tmp / "observer_paused_until").exists() and obs.o.paused_until() is None
    assert [a[0][0] for a in obs.audits] == ["observer_paused", "observer_paused", "observer_resumed"]
    assert obs.audits[0][1]["extra"]["until"] == r["paused_until"][:16]
    (obs.tmp / "observer_paused_until").write_text("2020-01-01T00:00:00")
    assert obs.o.paused_until() is None and not (obs.tmp / "observer_paused_until").exists(), "a past pause is cleaned"


def test_the_daemon_stops_watching_while_paused(obs, monkeypatch):
    import codec_lifecycle
    o = obs.o
    _mirror(obs.tmp / "observer_buffer.json", [ENTRY])
    o.pause(datetime.now() + timedelta(minutes=15))
    fired = []
    monkeypatch.setattr(codec_lifecycle, "install_handlers", lambda *a, **k: None)
    monkeypatch.setattr(o, "poll", lambda *a, **k: pytest.fail("polled while paused"))
    monkeypatch.setattr(o, "_maybe_fire_shift_report", lambda idle: fired.append(idle))
    monkeypatch.setattr(o, "_idle_seconds", lambda: 5.0)

    class Stop(Exception):
        pass

    def sleep(s):
        assert 1 <= s <= 30
        raise Stop
    monkeypatch.setattr(o.time, "sleep", sleep)
    with pytest.raises(Stop):
        o.run_daemon()
    assert not (obs.tmp / "observer_buffer.json").exists(), "the mirror is wiped"
    assert fired == [5.0], "the shift report still fires on time"
    assert o.maybe_inject_observation_summary("what am I doing", "local") == (None, "skipped_disabled")


def test_state_says_watching_only_while_the_mirror_is_fresh(obs):
    c = obs.client
    assert c.get("/api/observer/state").json() == {"watching": False, "paused_until": None, "updated": None, "entries": 0}
    _mirror(obs.tmp / "observer_buffer.json", [ENTRY, ENTRY])
    d = c.get("/api/observer/state").json()
    assert d["watching"] is True and d["entries"] == 2
    _mirror(obs.tmp / "observer_buffer.json", [ENTRY], age_s=20 * 60)
    assert c.get("/api/observer/state").json()["watching"] is False


def test_what_it_sees_now_is_metadata_only(obs):
    _mirror(obs.tmp / "observer_buffer.json", [ENTRY])
    r = obs.client.get("/api/observer/now")
    d = r.json()
    assert d["latest"] == {"at": "2026-10-06T10:00:00", "app": "Notes", "title_length": 19, "screen_text_length": 30,
                           "screen_read": True, "clipboard": {"type": "url", "length": 42}, "recent_files": 1}
    for secret in ("Secret", "confidential", "/Users/someone"):
        assert secret not in r.text, secret
    inspected = [a for a in obs.audits if a[0][0] == "observer_buffer_inspected"]
    assert inspected and inspected[0][1]["extra"] == {"buffer_entries_returned": 1, "source": "now"}
    obs.o.pause(datetime.now() + timedelta(minutes=5))
    assert obs.client.get("/api/observer/now").json()["latest"] is None


def test_the_trigger_list_is_reachable_and_the_voice_guide_moved():
    import routes.skills as rs
    paths = {(r.path, tuple(sorted(r.methods))) for r in rs.router.routes}
    assert ("/api/voice_triggers", ("GET",)) in paths and ("/api/voice_triggers", ("POST",)) in paths
    assert not any(p == "/api/triggers" for p, _ in paths)
    assert HOME.count("fetch('/api/voice_triggers'") == 3 and "fetch('/api/triggers'" not in HOME
    assert 'check_endpoint("/api/voice_triggers")' in (REPO / "scripts" / "feature_audit.py").read_text(encoding="utf-8")


def test_mute_writes_the_file_whole_and_keeps_the_default(obs, monkeypatch):
    t = obs.t
    assert t._resolve_mute("clipboard_url_fetch")[0] is True, "the default mute"
    t.set_muted("other_skill", True)
    saved = json.loads((obs.tmp / "triggers.json").read_text())
    assert saved == {"muted_skills": ["clipboard_url_fetch", "other_skill"], "muted_until": {}}
    t.set_muted("clipboard_url_fetch", False)
    assert json.loads((obs.tmp / "triggers.json").read_text())["muted_skills"] == ["other_skill"]
    monkeypatch.setattr(obs.rt, "_list_triggers", lambda: [{"trigger_key": "other_skill:ab12cd34", "skill_name": "other_skill"}])
    c = obs.client
    assert c.post("/api/triggers/other_skill:ab12cd34/mute", json={"muted": False}).json()["muted"] is False
    assert t._resolve_mute("other_skill")[0] is False
    assert c.post("/api/triggers/nope/mute", json={"muted": True}).status_code == 404
    assert c.post("/api/triggers/other_skill:ab12cd34/mute", json={"muted": "yes"}).status_code == 400
    trig = SimpleNamespace(key="k1", skill_name="other_skill", type="clipboard_pattern", short_summary=lambda: "URL copied",
                           cooldown_seconds=60, require_confirmation=True, destructive=False)
    t.set_muted("other_skill", True)
    assert obs.rt._trigger_summary(trig)["muted"] is True


def test_kill_and_mute_reach_the_observer_when_their_files_change(obs):
    t = obs.t
    assert t.is_killed("k1") is False  # cached now
    path = obs.tmp / "triggers_killed.json"
    path.write_text(json.dumps({"killed_keys": ["k1"], "schema": 1}))  # written by another process
    later = time.time() + 2
    os.utime(path, (later, later))
    assert t.is_killed("k1") is True, "no restart needed"
    assert t._resolve_mute("solo")[0] is False
    mute = obs.tmp / "triggers.json"
    mute.write_text(json.dumps({"muted_skills": ["solo"], "muted_until": {}}))
    os.utime(mute, (later, later))
    assert t._resolve_mute("solo")[0] is True


def test_the_header_has_the_eye_and_its_menu():
    assert 'id="csWatch" hidden aria-haspopup="menu" aria-label="CODEC is watching"' in SHELL
    assert "fetch('/api/observer/state')" in SHELL and "fetch('/api/observer/now')" in SHELL
    for label in ("Pause for 15 minutes", "Pause for 1 hour", "Pause until tomorrow", "Resume watching",
                  "What CODEC sees now", "Automatic triggers", "Observer settings"):
        assert f"label: '{label}'" in SHELL, label
    assert "postJSON('/api/observer/pause', body)" in SHELL and "postJSON('/api/observer/resume', {})" in SHELL
    assert 'aria-labelledby="csTrigTitle" data-dialog="open"' in SHELL and "'/' + act, act === 'mute' ? { muted: !on } : { killed: !on }" in SHELL
    assert "$('csAskCancel').hidden = !!opts.single;" in SHELL


_JS_HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><html><head></head><body><script src="/static/codec-shell.js" data-page="tasks" data-title="Tasks"></script>' +
  '<main class="cs-main"></main></body></html>', { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/tasks' });
const w = dom.window, calls = [];
let state = { watching: true, paused_until: null, updated: '', entries: 3 };
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {} });
w.fetch = (u, o) => { u = String(u); calls.push([u, (o && o.method) || 'GET', (o && o.body) || '']);
  let d = null;
  if (u === '/api/observer/state') d = state;
  if (u === '/api/observer/pause') { state = { watching: false, paused_until: '2026-10-06T23:45:00', updated: null, entries: 0 }; d = { paused_until: state.paused_until }; }
  return Promise.resolve({ ok: !!d, status: d ? 200 : 404, json: () => Promise.resolve(d || {}) }); };
w.eval(fs.readFileSync(process.argv[1], 'utf8'));
(async () => {
  const d = w.document, out = {};
  await new Promise(r => setTimeout(r, 150));
  const b = d.getElementById('csWatch');
  out.shown = !b.hidden && b.getAttribute('aria-label');
  b.click();
  await new Promise(r => setTimeout(r, 30));
  const items = [...d.querySelectorAll('#csActions .cs-pop-item')].map(x => x.textContent.trim());
  out.items = items;
  d.querySelectorAll('#csActions .cs-pop-item')[0].click();
  await new Promise(r => setTimeout(r, 150));
  out.paused = calls.filter(c => c[0] === '/api/observer/pause').map(c => c[2]);
  out.after = [b.classList.contains('cs-watch-paused'), b.getAttribute('aria-label')];
  console.log(JSON.stringify(out)); process.exit(0);
})().catch(e => { console.log('ERR ' + e.message); process.exit(1); });
setTimeout(() => { console.log('TIMEOUT'); process.exit(2); }, 20000);
"""


def _jsdom_available() -> bool:
    if shutil.which("node") is None:
        return False
    r = subprocess.run(["node", "-e", "require.resolve('jsdom')"], capture_output=True, cwd=REPO, env=os.environ.copy(), timeout=20)
    return r.returncode == 0


@pytest.mark.skipif(not _jsdom_available(), reason="node with jsdom not resolvable (set NODE_PATH)")
def test_the_eye_shows_and_pauses_under_jsdom():
    out = subprocess.run(["node", "-e", _JS_HARNESS, str(REPO / "static" / "codec-shell.js")], capture_output=True,
                         text=True, cwd=REPO, env=os.environ.copy(), timeout=60)
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("{") or ln.startswith("ERR") or ln == "TIMEOUT"][-1]
    d = json.loads(line)
    assert d["shown"] == "CODEC is watching"
    assert d["items"][:3] == ["Pause for 15 minutes", "Pause for 1 hour", "Pause until tomorrow"]
    assert d["items"][3:] == ["What CODEC sees now", "Automatic triggers", "Observer settings"]
    assert d["paused"] == ['{"minutes":15}']
    assert d["after"][0] is True and re.match(r"Watching paused until \d", d["after"][1])
