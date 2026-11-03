"""P-009: the suite stays off the owner's Keychain, ~/.codec and home folders.

Design: docs/TEST-LIVE-STATE-DESIGN.md. The helpers live in tests/live_state_guard.py
and are installed by tests/conftest.py at import time. Nothing here writes the
owner's files: the guard tests use a temp root, and the live-path tests open
the real files without O_CREAT or read-only, expecting the guard to refuse.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import live_state_guard as lsg

LIVE = os.path.realpath(os.path.expanduser("~/.codec"))


def _under_live(path) -> bool:
    real = os.path.realpath(str(path))
    return real == LIVE or real.startswith(LIVE + os.sep)


# ── Fake `security` ──────────────────────────────────────────────────────────


def test_fake_security_answers_like_the_tool(tmp_path):
    fake = str(lsg.write_fake_security(tmp_path))
    item = ["-s", "ai.avadigital.codec._test_p009.k", "-a", "codec-test"]

    def run(*args):
        return subprocess.run([fake, *args], capture_output=True, text=True)

    assert run("find-generic-password", *item, "-w").returncode == 44
    assert run("add-generic-password", *item, "-w", "v1").returncode == 0
    assert run("add-generic-password", *item, "-w", "v2").returncode == 45  # no -U: kept
    r = run("find-generic-password", *item, "-w")
    assert (r.returncode, r.stdout) == (0, "v1\n")
    assert run("add-generic-password", *item, "-w", "v3", "-U").returncode == 0
    assert run("find-generic-password", *item, "-w").stdout == "v3\n"
    (tmp_path / "locked").write_text("")
    assert run("find-generic-password", *item, "-w").returncode == 51
    (tmp_path / "locked").unlink()
    assert run("delete-generic-password", *item).returncode == 0
    assert run("delete-generic-password", *item).returncode == 44
    assert json.loads((tmp_path / "items.json").read_text()) == {}


def test_keychain_is_pointed_at_the_fake():
    import codec_keychain as kc
    assert kc._SECURITY_BIN != lsg.REAL_SECURITY
    assert Path(kc._SECURITY_BIN).name == "security"
    for p in (kc._SECURITY_BIN, kc._FALLBACK_KEY_PATH, kc._FALLBACK_STORE_PATH):
        assert not _under_live(p), "codec_keychain still points at the owner's files"


@pytest.mark.skipif(sys.platform != "darwin", reason="the Keychain path runs on macOS only")
def test_keychain_roundtrip_lands_in_the_fake(monkeypatch):
    import codec_keychain as kc
    monkeypatch.setattr(kc, "_SERVICE_PREFIX", "ai.avadigital.codec._test_p009")
    kc._invalidate_negative_cache()
    assert kc.is_keychain_available()
    assert kc.keychain_set("roundtrip", "fake-only")
    store = json.loads((Path(kc._SECURITY_BIN).parent / "items.json").read_text())
    assert any(k.startswith("ai.avadigital.codec._test_p009.roundtrip|") for k in store)
    assert kc.keychain_get("roundtrip") == "fake-only"
    assert kc.keychain_delete("roundtrip")
    assert kc.keychain_get("roundtrip") is None


# ── Redirected notification paths ────────────────────────────────────────────


def test_notification_paths_are_temp():
    import codec_agent_messaging as cam
    import codec_ask_user as cau
    import codec_daybreak
    import codec_watchdog
    import routes._shared as shared
    for p in (cau.PENDING_QUESTIONS_PATH, cau.NOTIFICATIONS_PATH, cam._CODEC_DIR,
              cam._AGENTS_DIR, cam._NOTIFICATIONS_PATH, codec_watchdog.NOTIFICATIONS_PATH,
              codec_daybreak._NOTIFICATIONS_PATH, shared.NOTIFICATIONS_PATH):
        assert not _under_live(p), "a notifications path still points at the owner's ~/.codec"


# ── Guard on a temp root (runs everywhere, CI included) ──────────────────────


def test_guard_refuses_the_protected_names_and_nothing_else(tmp_path):
    g = lsg.Guard(tmp_path)
    for name in ("notifications.json", "notifications.json.lock", "notifications.tmp",
                 "pending_questions.json", "audit.log", "audit.log.1", "memory.db",
                 "memory.db-wal", "shift_report_state.json"):
        assert g.protected(tmp_path / name), name
    for name in ("config.json", "notes", "memory.dbx/../x", "reply_stats.jsonl"):
        assert g.protected(tmp_path / name) is None, name
    assert g.protected(tmp_path / "sub" / "notifications.json") is None  # other folder
    with pytest.raises(lsg.LiveStateRefused):
        g.check_path("open for write", str(tmp_path / "audit.log"))
    assert g.hits == [("open for write", "audit.log")]
    with g.expected():
        with pytest.raises(lsg.LiveStateRefused):
            g.check_path("delete", tmp_path / "memory.db")
    assert g.hits == [("open for write", "audit.log")]


def test_guard_knows_the_real_security_program(tmp_path):
    g = lsg.Guard(tmp_path)
    fake = str(lsg.write_fake_security(tmp_path / "kc"))
    g.check_command([fake, "find-generic-password"])  # the fake is fine
    g.check_command(["/usr/bin/sqlite3", "x"])
    for argv in (["/usr/bin/security", "-h"], "/usr/bin/security -h"):
        with pytest.raises(lsg.LiveStateRefused):
            g.check_command(argv)
    assert lsg.Guard(tmp_path, allow_real_keychain=True).check_command(["/usr/bin/security"]) is None


# ── Installed guard on the owner's real paths (his Mac only) ─────────────────

# Keyed on the folder, not on lsg.ACTIVE: if conftest stopped installing the
# guard, this must fail on the owner's Mac, not skip.
needs_owner_home = pytest.mark.skipif(not lsg.OWNER_HOME_EXISTED,
                                      reason="no owner ~/.codec before this run (CI)")


@needs_owner_home
def test_installed_guard_refuses_live_path_opens():
    guard = lsg.ACTIVE
    assert guard is not None, "tests/conftest.py did not install the live-state guard"
    for name in ("notifications.json", "pending_questions.json", "audit.log"):
        with guard.expected(), pytest.raises(lsg.LiveStateRefused):
            # No O_CREAT and no write: even an unguarded open would change nothing.
            fd = os.open(os.path.join(LIVE, name), os.O_WRONLY | os.O_APPEND)
            os.close(fd)
        with guard.expected(), pytest.raises(lsg.LiveStateRefused):
            # "r+", not "a": a missing file is not created even if the guard failed.
            open(os.path.join(LIVE, name), "r+").close()
    with guard.expected(), pytest.raises(lsg.LiveStateRefused):
        sqlite3.connect(f"file:{os.path.join(LIVE, 'memory.db')}?mode=ro", uri=True).close()
    if not guard.allow_real_keychain:  # CODEC_REAL_KEYCHAIN_TESTS=1 lets it run
        with guard.expected(), pytest.raises(lsg.LiveStateRefused):
            subprocess.run(["/usr/bin/security", "-h"], capture_output=True)
    with guard.expected(), pytest.raises(lsg.LiveStateRefused):
        os.replace(os.path.join(LIVE, "no-such-p009-file"), os.path.join(LIVE, "notifications.json"))
    # Reading stays allowed.
    with open(os.devnull) as f:
        f.read()


def test_a_swallowed_refusal_is_still_reported(tmp_path):
    """conftest's `_live_state_untouched` fails a test on any unreported refusal."""
    g = lsg.Guard(tmp_path)
    try:
        g.check_path("open for write", tmp_path / "notifications.json")
    except PermissionError:
        pass  # the code under test swallowed it
    assert g.unreported() == [("open for write", "notifications.")]
    assert g.unreported() == []


# ── Watch-list (read-only, counts only) ──────────────────────────────────────


def _home(tmp_path) -> Path:
    home = tmp_path / "home"
    (home / ".codec" / "agents").mkdir(parents=True)
    for d in ("Documents", "Desktop", "Downloads"):
        (home / d).mkdir()
    return home


def _write_notifs(home, entries):
    (home / ".codec" / "notifications.json").write_text(json.dumps(entries))


def test_watch_list_is_quiet_when_nothing_changes(tmp_path):
    home = _home(tmp_path)
    _write_notifs(home, [{"id": "a", "type": "shift_report", "trigger_kind": "manual"}])
    snap = lsg.watch_snapshot(home)
    assert lsg.watch_changes(snap, lsg.watch_snapshot(home)) == []


def test_watch_list_reports_each_hazard_by_count_only(tmp_path):
    home = _home(tmp_path)
    old_manual = {"id": "m0", "type": "shift_report", "trigger_kind": "manual"}
    _write_notifs(home, [old_manual])
    (home / ".codec" / "shift_report_state.json").write_text('{"last_trigger_kind": "idle"}')
    before = lsg.watch_snapshot(home)

    (home / "Documents" / "plan.md").write_text("x")
    agent = home / ".codec" / "agents" / "test_agent"
    agent.mkdir()
    (agent / "messages.jsonl").write_text("{}\n")
    _write_notifs(home, [
        {"id": "m1", "type": "shift_report", "trigger_kind": "manual"},
        {"type": "agent_update", "agent_id": "test_agent", "title": "x"},
        old_manual,
    ])
    (home / ".codec" / "shift_report_state.json").write_text('{"last_trigger_kind": "manual"}')
    changes = lsg.watch_changes(before, lsg.watch_snapshot(home))

    text = " | ".join(changes)
    assert len(changes) == 5, text
    assert "1 of 3 watched files" in text
    assert "test agent folder" in text
    assert "1 new or changed manual shift report" in text
    assert "1 new or changed test-named" in text
    assert "shift report state changed" in text
    for name in ("plan.md", "scratch.md", "note.txt", str(home)):
        assert name not in text, "the message names a file"


def test_watch_list_ignores_live_daemon_changes(tmp_path):
    home = _home(tmp_path)
    old = [{"id": f"m{i}", "type": "shift_report", "trigger_kind": "manual"} for i in range(3)]
    _write_notifs(home, old)
    (home / ".codec" / "shift_report_state.json").write_text('{"last_trigger_kind": "manual"}')
    before = lsg.watch_snapshot(home)
    # The observer fires the idle report (and updates the state), and a live
    # service trims the oldest entries: neither is a test's doing.
    _write_notifs(home, [{"id": "i1", "type": "shift_report", "trigger_kind": "idle"},
                         {"id": "t1", "type": "task_report"}] + old[:1])
    (home / ".codec" / "shift_report_state.json").write_text('{"last_trigger_kind": "idle"}')
    assert lsg.watch_changes(before, lsg.watch_snapshot(home)) == []


def test_watch_list_ignores_the_owner_reading_his_inbox(tmp_path):
    """Marking entries read (and the inbox giving an id-less banner an id) is the
    owner's doing, not a test's: the run must not fail on it."""
    home = _home(tmp_path)
    manual = {"id": "m0", "type": "shift_report", "trigger_kind": "manual", "read": False}
    banner = {"type": "agent_update", "agent_id": "test_agent", "title": "x", "_post_ts": 1.0}
    _write_notifs(home, [manual, banner])
    before = lsg.watch_snapshot(home)
    _write_notifs(home, [dict(manual, read=True), dict(banner, id="auto_1", read=True)])
    assert lsg.watch_changes(before, lsg.watch_snapshot(home)) == []
