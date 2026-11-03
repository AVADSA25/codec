"""Atomic, owner-only state files and the overlay events trim (P-082).

docs/ATOMIC-STATE-FILES-DESIGN.md. Every test writes under tmp_path through the
modules' own path constants: never the owner's ~/.codec, never a fake HOME.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SKILLS = _REPO / "skills"
for _p in (str(_REPO), str(_SKILLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import codec_agent_messaging  # noqa: E402
import codec_google_auth  # noqa: E402
import codec_heartbeat  # noqa: E402
import codec_license  # noqa: E402
import codec_overlays  # noqa: E402
import routes._shared as shared  # noqa: E402
import routes.prompts as prompts  # noqa: E402
import shift_report  # noqa: E402  (the skill module)

JSON_WRITERS = ("sessions", "e2e_keys", "google_token", "license_state", "prompt_overrides")


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def umask_022():
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


class _FakeCreds:
    def __init__(self, token):
        self.token = token

    def to_json(self):
        return json.dumps({"token": self.token, "refresh_token": "refresh-test", "scopes": ["x"]})


def _session(hours_ago=1):
    return {"created": datetime.now() - timedelta(hours=hours_ago), "ip": "127.0.0.1", "method": "pin"}


def _write_with(name, path, monkeypatch, version=1):
    """Run one real writer against `path`; `version` changes what it writes."""
    if name == "sessions":
        monkeypatch.setattr(shared, "_SESSION_FILE", str(path))
        monkeypatch.setattr(shared, "_auth_sessions",
                            {f"tok_{i}": _session() for i in range(version)})
        shared._save_sessions()
    elif name == "e2e_keys":
        monkeypatch.setattr(shared, "_E2E_KEYS_FILE", str(path))
        monkeypatch.setattr(shared, "_auth_sessions", {f"tok_{i}": _session() for i in range(version)})
        monkeypatch.setattr(shared, "_e2e_keys", {f"tok_{i}": bytes([i]) * 32 for i in range(version)})
        shared._save_e2e_keys()
    elif name == "google_token":
        monkeypatch.setattr(codec_google_auth, "TOKEN_PATH", str(path))
        codec_google_auth._save_token(_FakeCreds(f"token-v{version}"))
    elif name == "license_state":
        monkeypatch.setattr(codec_license, "GRACE_STATE_PATH", Path(path))
        codec_license._write_grace(1000.0 * version, "pro", "")
    elif name == "prompt_overrides":
        monkeypatch.setattr(prompts, "PROMPTS_FILE", str(path))
        prompts._save_prompt_overrides({"chat": f"override v{version}"})
    else:
        raise AssertionError(name)


# ── A failed write keeps the previous file ────────────────────────────────────

@pytest.mark.parametrize("name", JSON_WRITERS)
def test_replace_fails_keeps_previous_file(name, tmp_path, monkeypatch):
    target = tmp_path / f"{name}.json"
    _write_with(name, target, monkeypatch, version=1)
    before = target.read_bytes()
    json.loads(before)

    def boom(src, dst):
        raise OSError("disk full (test)")

    monkeypatch.setattr(os, "replace", boom)
    if name == "prompt_overrides":  # the route turns this into a 500; the file stays
        with pytest.raises(OSError):
            _write_with(name, target, monkeypatch, version=2)
    else:  # these writers log the failure and carry on
        _write_with(name, target, monkeypatch, version=2)
    assert target.read_bytes() == before
    json.loads(target.read_bytes())
    assert sorted(p.name for p in tmp_path.iterdir()) == [target.name], "a temp file was left behind"


# ── Owner-only from creation ──────────────────────────────────────────────────

@pytest.mark.parametrize("name", JSON_WRITERS)
def test_created_0600_before_it_is_in_place(name, tmp_path, monkeypatch, umask_022):
    """The temp file is 0600 when os.replace puts it in place: never readable by others."""
    seen = []
    real_replace = os.replace

    def watching(src, dst):
        seen.append(_mode(src))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", watching)
    target = tmp_path / f"{name}.json"
    _write_with(name, target, monkeypatch)
    assert seen == [0o600]
    assert _mode(target) == 0o600


def _overlay_via_emit(path, monkeypatch):
    monkeypatch.setattr(codec_overlays, "_EVENTS", str(path))
    assert codec_overlays._emit({"type": "toggle_on"}) is True


def _redirect_codec_home(monkeypatch, folder):
    real = os.path.expanduser

    def expand(p):
        p = str(p)
        return str(folder / p[len("~/.codec/"):]) if p.startswith("~/.codec/") else real(p)

    monkeypatch.setattr(os.path, "expanduser", expand)


def _overlay_via_core(path, monkeypatch):
    """codec_core's F16 input request, with a stand-in for the Swift panel."""
    import codec_core
    _redirect_codec_home(monkeypatch, path.parent)

    def panel():
        for _ in range(200):
            if path.exists() and path.read_text().strip():
                rid = json.loads(path.read_text().splitlines()[-1])["id"]
                base = path.parent / f"overlay_input_{rid}"
                Path(str(base) + ".json").write_text(json.dumps({"text": "hello"}))
                Path(str(base) + ".ack").write_text("")
                return
            time.sleep(0.01)

    t = threading.Thread(target=panel, daemon=True)
    t.start()
    assert codec_core._swift_text_input(timeout=5) == "hello"
    t.join(2)


def _overlay_via_dispatch(path, monkeypatch):
    """codec_dispatch's skill_fired line after a skill answered."""
    from types import SimpleNamespace
    import codec_audit
    import codec_dispatch
    import codec_hooks
    monkeypatch.setattr(codec_license, "feature_allowed", lambda *_a: True)
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", path.parent / "audit.log")
    monkeypatch.setattr(codec_hooks, "_registry", SimpleNamespace(for_hook=lambda hook, tool: []))
    monkeypatch.setattr(codec_dispatch, "_skill_off", lambda n: False)
    monkeypatch.setattr(codec_dispatch.registry, "run", lambda name, task, app="": "Sunny")
    _redirect_codec_home(monkeypatch, path.parent)
    assert codec_dispatch.run_skill({"name": "weather"}, "weather") == "Sunny"


@pytest.mark.parametrize("writer", [_overlay_via_emit, _overlay_via_core, _overlay_via_dispatch],
                         ids=["overlays_emit", "core_text_input", "dispatch_skill_fired"])
def test_overlay_events_created_0600(writer, tmp_path, monkeypatch, umask_022):
    path = tmp_path / "overlay_events.jsonl"
    writer(path, monkeypatch)
    assert path.read_text().strip(), "nothing was written"
    assert _mode(path) == 0o600


def test_google_token_created_0600_in_a_new_0700_folder(tmp_path, monkeypatch, umask_022):
    target = tmp_path / "codec" / "google_token.json"
    _write_with("google_token", target, monkeypatch)
    assert _mode(target) == 0o600 and _mode(target.parent) == 0o700
    data = json.loads(target.read_text())
    assert data["token"] == "token-v1" and data["scopes"] == codec_google_auth.ALL_SCOPES


def _notif_via_shift_report(path, monkeypatch):
    monkeypatch.setattr(shift_report, "_NOTIFS_PATH", path)
    assert shift_report._post_notification(_report())


FOLDER_WRITERS = JSON_WRITERS + ("overlay_events", "shift_report")


@pytest.mark.parametrize("name", FOLDER_WRITERS)
def test_folder_mode_new_0700_existing_untouched(name, tmp_path, monkeypatch, umask_022):
    def write(path):
        if name == "overlay_events":
            _overlay_via_emit(path, monkeypatch)
        elif name == "shift_report":
            _notif_via_shift_report(path, monkeypatch)
        else:
            _write_with(name, path, monkeypatch)

    new = tmp_path / "new" / "state.json"
    write(new)
    assert _mode(new.parent) == 0o700
    existing = tmp_path / "existing"
    existing.mkdir()
    os.chmod(existing, 0o755)
    write(existing / "state.json")
    assert _mode(existing) == 0o755


# ── Files written by the old code still load ──────────────────────────────────

def test_old_format_sessions_file_still_loads_and_bytes_match(tmp_path, monkeypatch):
    created = (datetime.now() - timedelta(hours=1)).replace(microsecond=0)
    raw = {"tok_old": {"created": created.isoformat(), "ip": "127.0.0.1", "method": "pin"}}
    f = tmp_path / ".auth_sessions.json"
    with open(f, "w") as fh:  # exactly what the old _save_sessions wrote
        json.dump(raw, fh)
    old_bytes = f.read_bytes()
    monkeypatch.setattr(shared, "_SESSION_FILE", str(f))
    monkeypatch.setattr(shared, "_auth_sessions", {})
    shared._load_sessions()
    assert shared._auth_sessions == {"tok_old": {"created": created, "ip": "127.0.0.1", "method": "pin"}}
    shared._save_sessions()
    assert f.read_bytes() == old_bytes


def test_old_format_e2e_keys_file_still_loads_and_bytes_match(tmp_path, monkeypatch):
    import base64
    key = bytes(range(32))
    f = tmp_path / ".e2e_keys.json"
    with open(f, "w") as fh:  # exactly what the old _save_e2e_keys wrote
        json.dump({"tok_old": base64.b64encode(key).decode()}, fh)
    old_bytes = f.read_bytes()
    monkeypatch.setattr(shared, "_E2E_KEYS_FILE", str(f))
    monkeypatch.setattr(shared, "_e2e_keys", {})
    monkeypatch.setattr(shared, "_auth_sessions", {"tok_old": _session()})
    shared._load_e2e_keys()
    assert shared._e2e_keys == {"tok_old": key}
    shared._save_e2e_keys()
    assert f.read_bytes() == old_bytes


# ── The shift report shares the notifications lock ────────────────────────────

def _report():
    return {"title": "Shift report test", "markdown": "body", "trigger_kind": "manual", "word_count": 1}


def _other_via_agent_message(path, monkeypatch):
    monkeypatch.setattr(codec_agent_messaging, "_CODEC_DIR", path.parent)
    monkeypatch.setattr(codec_agent_messaging, "_AGENTS_DIR", path.parent / "agents")
    monkeypatch.setattr(codec_agent_messaging, "_NOTIFICATIONS_PATH", path)
    monkeypatch.setattr(codec_agent_messaging, "_audit", lambda *a, **k: None)
    return lambda: codec_agent_messaging.post_message("agent_test", "agent_update", "Step done", "b")


def _other_via_shared_save(path, monkeypatch):
    monkeypatch.setattr(shared, "NOTIFICATIONS_PATH", str(path))
    return lambda: shared._save_notification("Heartbeat Alert", "disk 95% used", status="warning",
                                             schedule_id="heartbeat")


@pytest.mark.parametrize("other", [_other_via_agent_message, _other_via_shared_save],
                         ids=["agent_message", "shared_save_notification"])
def test_shift_report_and_other_writer_both_kept(other, tmp_path, monkeypatch):
    """The shift report is held between its read and its write. Under the shared lock
    the other writer waits and both entries survive; without it, the other entry is lost."""
    path = tmp_path / "notifications.json"
    path.write_text("[]")
    monkeypatch.setattr(shift_report, "_NOTIFS_PATH", path)
    write_other = other(path, monkeypatch)
    read_done, other_done = threading.Event(), threading.Event()
    real_load = json.load

    def load(fh, *a, **k):
        data = real_load(fh, *a, **k)
        if threading.current_thread().name == "shift-report":
            read_done.set()
            other_done.wait(0.5)  # long enough for an unlocked writer to finish
        return data

    monkeypatch.setattr(json, "load", load)
    a = threading.Thread(target=shift_report._post_notification, args=(_report(),), name="shift-report")
    a.start()
    assert read_done.wait(5), "the shift report never read the file"

    def run_other():
        write_other()
        other_done.set()

    b = threading.Thread(target=run_other, name="other")
    b.start()
    a.join(10)
    b.join(10)
    entries = json.loads(path.read_text())
    titles = sorted(e["title"] for e in entries)
    assert "Shift report test" in titles
    assert any(t in titles for t in ("Step done", "Heartbeat Alert")), titles
    assert len(entries) == 2


# ── Overlay events trim ───────────────────────────────────────────────────────

def _big_events_file(path, size=2 * 1024 * 1024):
    lines, total, i = [], 0, 0
    while total < size:
        line = json.dumps({"type": "skill_fired", "name": f"skill_{i:07d}"}) + "\n"
        lines.append(line)
        total += len(line)
        i += 1
    path.write_text("".join(lines))
    return lines


def test_overlay_trim_cuts_an_old_2mb_file_to_its_last_100_lines(tmp_path, umask_022):
    folder = tmp_path / "codec"
    folder.mkdir()
    os.chmod(folder, 0o755)
    path = folder / "overlay_events.jsonl"
    lines = _big_events_file(path)
    assert path.stat().st_size >= 2 * 1024 * 1024 and _mode(path) == 0o644
    old = time.time() - 3600
    os.utime(path, (old, old))
    inode = path.stat().st_ino
    removed = codec_heartbeat.trim_overlay_events(str(path))
    assert path.read_text() == "".join(lines[-codec_heartbeat.OVERLAY_KEEP_LINES:])
    assert len(path.read_text().splitlines()) == 100
    assert removed == len("".join(lines)) - path.stat().st_size > 0
    assert path.stat().st_ino != inode, "not replaced"
    assert _mode(path) == 0o600 and _mode(folder) == 0o755
    assert [p.name for p in folder.iterdir()] == ["overlay_events.jsonl"]


def test_overlay_trim_leaves_a_file_touched_10s_ago(tmp_path):
    path = tmp_path / "overlay_events.jsonl"
    _big_events_file(path)
    recent = time.time() - 10
    os.utime(path, (recent, recent))
    before, inode = path.read_bytes(), path.stat().st_ino
    assert codec_heartbeat.trim_overlay_events(str(path)) == 0
    assert path.read_bytes() == before and path.stat().st_ino == inode


def test_overlay_trim_leaves_a_short_file_and_a_missing_one(tmp_path):
    path = tmp_path / "overlay_events.jsonl"
    path.write_text("".join(json.dumps({"type": "toggle_on", "n": i}) + "\n" for i in range(100)))
    old = time.time() - 3600
    os.utime(path, (old, old))
    before, inode = path.read_bytes(), path.stat().st_ino
    assert codec_heartbeat.trim_overlay_events(str(path)) == 0
    assert path.read_bytes() == before and path.stat().st_ino == inode
    assert codec_heartbeat.trim_overlay_events(str(tmp_path / "missing.jsonl")) == 0
    assert not (tmp_path / "missing.jsonl").exists()


def test_overlay_trim_uses_the_writer_path_and_the_suite_never_trims_the_owner_file(tmp_path, monkeypatch):
    assert not codec_overlays._EVENTS.startswith(os.path.join(str(Path.home()), ".codec")), \
        "tests/conftest.py must point codec_overlays._EVENTS at a temp folder"
    path = tmp_path / "overlay_events.jsonl"
    lines = _big_events_file(path, size=64 * 1024)
    old = time.time() - 3600
    os.utime(path, (old, old))
    monkeypatch.setattr(codec_overlays, "_EVENTS", str(path))
    assert codec_heartbeat.trim_overlay_events() > 0
    assert path.read_text() == "".join(lines[-100:])
