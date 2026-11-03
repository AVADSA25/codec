"""Pytest configuration.

Path setup: when running from a git worktree (e.g. .claude/worktrees/<branch>),
make sure the worktree's repo dir wins over ~/codec-repo so local codec_*.py
gets imported, not the main checkout. Without this, a worktree's Step 3
codec_audit (with new ASKUSER_EVENT_* constants) gets shadowed by main's
older copy and tests that rely on the new constants fail.

pynput stub: on Linux CI runners without an X display, `from pynput import
keyboard` raises ImportError at module-load time (pynput tries to acquire
an X11 connection). codec.py imports pynput unconditionally; many tests
do `import codec` at module scope. We install a minimal stub in sys.modules
BEFORE any test collection so those imports succeed. macOS dev machines
have a real pynput and skip the stub entirely. Per PR-4F design doc
(`docs/PR4F-STATE-LOCK-DESIGN.md`) — same pattern, lifted into conftest
so it applies session-wide instead of per-test.
"""
import sys
import os
import importlib
import importlib.util
import tempfile
import types
from pathlib import Path

import pytest

# Worktree's repo root = parent of `tests/`. When running from main repo
# directly, this resolves to ~/codec-repo (same as the next insert) — harmless.
_WORKTREE_REPO = Path(__file__).resolve().parent.parent

# P-009: the owner's ~/.codec is only guarded where it existed before this run
# (not on a CI runner). Read before anything below can create the folder.
_OWNER_CODEC_HOME = os.path.expanduser("~/.codec")
_OWNER_CODEC_HOME_EXISTED = os.path.isdir(_OWNER_CODEC_HOME)

# P-009 helpers (fake `security`, guard, watch-list), loaded by path so every test
# module shares this one copy.
_spec = importlib.util.spec_from_file_location(
    "live_state_guard", Path(__file__).resolve().parent / "live_state_guard.py")
live_state_guard = importlib.util.module_from_spec(_spec)
sys.modules["live_state_guard"] = live_state_guard
_spec.loader.exec_module(live_state_guard)
live_state_guard.OWNER_HOME_EXISTED = _OWNER_CODEC_HOME_EXISTED

# Order matters: insert(0) prepends. Last insert wins → worktree at sys.path[0].
sys.path.insert(0, os.path.expanduser("~/codec-repo"))
sys.path.insert(0, os.path.expanduser("~/.codec/skills"))
sys.path.insert(0, str(_WORKTREE_REPO))

# Starlette's TestClient sends `Host: testserver`; the dashboard's Host
# allowlist (codec_dashboard.HostAllowlistMiddleware) reads extra names from
# this variable when the module is imported.
os.environ.setdefault("CODEC_DASHBOARD_EXTRA_HOSTS", "testserver")

# The owner's skills folder (~/.codec/skills) is read by every skill registry
# and written by /api/skill/approve. Point it at an empty temp folder before
# anything imports codec_config, so the suite neither loads nor writes the
# owner's approved skills (tests that need user skills use their own tmp dirs).
os.environ.setdefault("CODEC_USER_SKILLS_DIR", tempfile.mkdtemp(prefix="codec-test-user-skills-"))
os.environ.setdefault("CODEC_NOTES_DIR", tempfile.mkdtemp(prefix="codec-test-notes-"))
# P-017: the bridges' main() loops write a health beat a round (codec_health); keep them off ~/.codec/health.
os.environ.setdefault("CODEC_HEALTH_DIR", tempfile.mkdtemp(prefix="codec-test-health-"))


def _isolate_audit_log() -> None:
    """Point the audit log at a throwaway file for the whole test session.

    `codec_audit._AUDIT_DIR` is a module constant of `~/.codec`, so an unmocked
    `audit()` anywhere in the suite appends to the OPERATOR'S real, HMAC-signed
    audit log. Measured: 315 lines per full run, from 12 sources — including
    `file_write_blocked`, `python_exec_blocked` and `plugin_allowlist_migrated`.
    Those are indistinguishable from genuine security events after the fact, so
    the log a forensic review depends on is seeded with fiction by `pytest`.

    Done at conftest IMPORT time rather than in an autouse fixture: a fixture
    runs after test modules are imported, and module-scope audit emits during
    collection would already have landed in the real log.

    Tests that assert on audit output monkeypatch `_AUDIT_LOG` per-test as
    before; this only changes where the unmocked writes go. Existing polluted
    lines are deliberately left alone — hand-editing an HMAC-signed log is
    exactly what §6 of AGENTS.md forbids.
    """
    try:
        import codec_audit
    except Exception:
        return  # audit unavailable in this environment; nothing to isolate
    tmp = Path(tempfile.mkdtemp(prefix="codec-test-audit-"))
    codec_audit._AUDIT_DIR = tmp
    codec_audit._AUDIT_LOG = tmp / "audit.log"


_isolate_audit_log()


def _isolate_keychain() -> None:
    """Point codec_keychain at a fake `security` program and a temp fallback store (P-009).

    Every unmocked secret read went to the owner's real Keychain through
    /usr/bin/security (importing codec_config alone reads three entries, the
    audit HMAC secret among them), and the Keychain tests created and deleted
    real `ai.avadigital.codec._test_*` items. The fake (tests/live_state_guard.py)
    keeps its items in a temp JSON file and answers 0/44/45/51 like the tool, so
    the Keychain code path still runs on a Mac; on Linux `is_keychain_available()`
    stays false and the fallback store is the temp one. Done at conftest import
    time, before anything imports codec_config. A real-Keychain test runs only
    with CODEC_REAL_KEYCHAIN_TESTS=1 (tests/test_keychain.py).
    """
    try:
        import codec_keychain
    except Exception:
        return
    tmp = Path(tempfile.mkdtemp(prefix="codec-test-keychain-"))
    codec_keychain._SECURITY_BIN = str(live_state_guard.write_fake_security(tmp))
    codec_keychain._FALLBACK_KEY_PATH = tmp / "secret.key"
    codec_keychain._FALLBACK_STORE_PATH = tmp / "secrets.enc.json"


_isolate_keychain()


def _guard_live_state() -> None:
    """Refuse this process's writes to the owner's live state (P-009).

    Only where the owner's ~/.codec existed before the run, so it does nothing on
    CI. Write opens, replace, rename and delete of ~/.codec memory.db*,
    notifications.*, pending_questions.*, audit.log* and shift_report_state.*,
    any sqlite3.connect to them, and any run of the real /usr/bin/security raise
    LiveStateRefused; `_live_state_untouched` then fails the test even if the code
    swallowed the error. Installed early so a write made while the helpers below
    import their modules is caught too.
    """
    if not _OWNER_CODEC_HOME_EXISTED:
        return
    live_state_guard.Guard(
        _OWNER_CODEC_HOME,
        allow_real_keychain=os.environ.get("CODEC_REAL_KEYCHAIN_TESTS") == "1",
    ).install()


_guard_live_state()


def _isolate_owner_alerts() -> None:
    """Keep the suite away from the owner's alert state and alert channels.

    `codec_alerts` keeps which problems have alerted in ~/.codec/alerts_sent.json
    and the probe history in ~/.codec/alert_state.json. An unmocked
    `check_services_and_alert()` read the live probe history and wrote the live
    once-per-6-h state, so a second run's dedupe swallowed the alert a test
    expected, and live state gained test entries. The real channels are switched
    off too: a test run must never pop a macOS banner or send a Telegram
    message. Tests that assert on alerts monkeypatch these per test, as before.

    Done at conftest import time, like `_isolate_audit_log`.
    """
    try:
        import codec_alerts
    except Exception:
        return
    tmp = Path(tempfile.mkdtemp(prefix="codec-test-alerts-"))
    codec_alerts.ALERTS_SENT_PATH = str(tmp / "alerts_sent.json")
    codec_alerts.ALERT_STATE_PATH = str(tmp / "alert_state.json")
    codec_alerts._send_macos_notification = lambda message: None
    codec_alerts._send_telegram = lambda bot_token, chat_id, message: False
    codec_alerts._send_email = lambda cfg, subject, body: False
    codec_alerts._send_slack = lambda webhook_url, message: False


_isolate_owner_alerts()


def _isolate_observer_pause() -> None:
    """Keep the suite off the owner's observer pause flag (UI P3.7).

    `codec_observer._PAUSE_PATH` is ~/.codec/observer_paused_until. While the
    owner has watching paused, every unmocked injection check sees it and skips,
    so the observer tests failed on this Mac and passed on CI; and an unmocked
    pause() or resume() would change the owner's real flag. Tests that need the
    flag monkeypatch it per test, as before. Done at conftest import time, like
    `_isolate_audit_log`.
    """
    try:
        import codec_observer
    except Exception:
        return
    tmp = Path(tempfile.mkdtemp(prefix="codec-test-observer-"))
    codec_observer._PAUSE_PATH = tmp / "observer_paused_until"


_isolate_observer_pause()


def _isolate_usage_stores() -> None:
    """Keep the suite out of the owner's usage files (UI P3.8 and P3.11).

    Every chat and voice reply appends a metadata line to
    ~/.codec/reply_stats.jsonl (Settings > Usage), and every crew run is kept in
    ~/.codec/agent_jobs.json (Tasks > Activity). A test that streams a chat reply
    or starts a crew would otherwise add fake replies and runs to the owner's
    numbers. The chat route also opens qchat.db: an unpointed run opened the
    owner's ~/.codec/qchat.db and ran its column migrations there (Mac merge
    pass, 6 Oct). Tests that assert on these files point them at their own tmp
    paths. Done at conftest import time, like `_isolate_audit_log`.
    """
    tmp = Path(tempfile.mkdtemp(prefix="codec-test-usage-"))
    try:
        import codec_usage
        codec_usage.REPLY_LOG = str(tmp / "reply_stats.jsonl")
    except Exception:
        pass
    try:
        import routes._shared as shared
        shared.AGENT_JOBS_PATH = str(tmp / "agent_jobs.json")
    except Exception:
        pass
    try:
        import routes.qchat as qchat
        qchat.QCHAT_DB = str(tmp / "qchat.db")
    except Exception:
        pass


_isolate_usage_stores()


def _isolate_backups() -> None:
    """Keep the suite away from the owner's backups and retention (D-38, docs/BACKUP-RETENTION-DESIGN.md).

    `codec_backup` writes ~/.codec/backups and deletes old chats, clipboard history and webcam photos.
    `tests/test_heartbeat.py` runs a real heartbeat cycle, which calls both. Every path in the module
    hangs off `codec_backup.CODEC_HOME`, so pointing it at an empty temp folder here, at conftest import
    time like `_isolate_audit_log`, makes every unmocked call a no-op on temp files. Tests that need
    files set `CODEC_HOME` to their own tmp_path.
    """
    try:
        import codec_backup
    except Exception:
        return
    codec_backup.CODEC_HOME = tempfile.mkdtemp(prefix="codec-test-backups-")


_isolate_backups()


def _isolate_memory_db() -> None:
    """Keep the suite off the owner's memory.db, About me and standing rules (P-026).

    Every chat prompt build reads the active facts and About me (codec_runtime_context, Daybreak's
    open threads) and counts a standing-rule injection; voice and the wake word read facts too.
    `codec_memory_upgrade._conn()` opens memory.db read-write and `prompt_block(record=True)`
    rewrites standing_rules.json, so an unmocked prompt build in a test opened the owner's
    ~/.codec/memory.db and bumped his rules' counts. `codec_config.DB_PATH` is set before any test
    module imports codec_memory or codec_memory_upgrade, so their own DB_PATH (and CodecMemory's
    default path) is the temp file too. Done at conftest import time, like `_isolate_audit_log`;
    tests that need facts or rules point them at their own tmp paths.
    """
    tmp = Path(tempfile.mkdtemp(prefix="codec-test-memory-"))
    try:
        import codec_config
        codec_config.DB_PATH = str(tmp / "memory.db")
        import codec_memory_upgrade
        codec_memory_upgrade.DB_PATH = codec_config.DB_PATH
        codec_memory_upgrade.IDENTITY_PATH = str(tmp / "identity.txt")
        import routes._shared as shared  # imported above by _isolate_usage_stores, with the real path
        shared.DB_PATH = codec_config.DB_PATH
    except Exception:
        pass
    try:
        import codec_standing_rules
        codec_standing_rules.RULES_PATH = tmp / "standing_rules.json"
    except Exception:
        pass


_isolate_memory_db()


def _install_pynput_stub_if_needed() -> None:
    """Stub `pynput` + `pynput.keyboard` if the real package can't import
    (headless Linux CI). On macOS the real package imports fine and this
    no-ops. The stub provides the symbols codec.py touches at import time
    — Listener, KeyCode, Key, Controller — as minimal placeholders. Tests
    that actually exercise keyboard behavior bring their own mocks; this
    stub only unblocks module import."""
    if "pynput" in sys.modules:
        return
    try:
        import pynput  # noqa: F401
        return
    except Exception:
        pass

    pynput_mod = types.ModuleType("pynput")
    keyboard_mod = types.ModuleType("pynput.keyboard")

    class _Stub:
        def __init__(self, *a, **kw): pass
        def __call__(self, *a, **kw): return self
        def __getattr__(self, name): return _Stub()
        def start(self): pass
        def stop(self): pass
        def join(self, *a, **kw): pass

    # `_Key` is accessed as `Key.f5`, `Key.f13`, `Key.cmd`, etc. — the set
    # of attribute names is open-ended (callers also use `f16`, `f17`).
    # Use a _Stub() instance so __getattr__ returns a fresh non-None _Stub
    # for ANY attribute, matching pynput's real keyboard.Key namespace
    # closely enough for codec_config._resolve_key() to return non-None.
    keyboard_mod.Listener = _Stub
    keyboard_mod.KeyCode = _Stub
    keyboard_mod.Key = _Stub()
    keyboard_mod.Controller = _Stub
    keyboard_mod.HotKey = _Stub
    pynput_mod.keyboard = keyboard_mod
    sys.modules["pynput"] = pynput_mod
    sys.modules["pynput.keyboard"] = keyboard_mod


_install_pynput_stub_if_needed()


def _isolate_notifications() -> None:
    """Keep the suite out of the owner's notifications and pending questions (P-009).

    Every module-level notifications.json / pending_questions.json path points at
    one temp folder: an unpatched agent-message test grew ~/.codec/agents/test_agent
    and added test banners to the owner's list, and ask_user's questions landed in
    the live files. Paths built inside a function (codec_heartbeat,
    codec_scheduler) and skill modules the registry loads later are covered by
    `_guard_live_state`. Tests that patch these per test keep doing so. Done at
    conftest import time, like `_isolate_audit_log`, after the memory isolation so
    these imports cannot bind memory.db first. The overlay events file goes there
    too: every full heartbeat trims it (P-082).
    """
    tmp = Path(tempfile.mkdtemp(prefix="codec-test-notifications-"))
    notifs = tmp / "notifications.json"
    for module, paths in (
        ("codec_ask_user", {"PENDING_QUESTIONS_PATH": tmp / "pending_questions.json",
                            "NOTIFICATIONS_PATH": notifs}),
        ("codec_agent_messaging", {"_CODEC_DIR": tmp, "_AGENTS_DIR": tmp / "agents",
                                   "_NOTIFICATIONS_PATH": notifs}),
        ("codec_watchdog", {"NOTIFICATIONS_PATH": str(notifs)}),
        ("codec_daybreak", {"_NOTIFICATIONS_PATH": str(notifs)}),
        ("routes._shared", {"NOTIFICATIONS_PATH": str(notifs)}),
        ("codec_overlays", {"_EVENTS": str(tmp / "overlay_events.jsonl")}),
    ):
        try:
            mod = importlib.import_module(module)
        except Exception:
            continue
        for name, value in paths.items():
            setattr(mod, name, value)


_isolate_notifications()


def _isolate_heartbeat_guard():
    """Keep the heartbeat's lock, last-run stamp and memory reading off ~/.codec (P-083).

    `heartbeat()` takes ~/.codec/heartbeat.lock, writes ~/.codec/heartbeat_last.json and
    runs `memory_check`, which writes ~/.codec/memory_check.json. Pointed at one temp
    folder at conftest import time, like `_isolate_audit_log`; `_heartbeat_runs_again`
    clears the stamp and the reading before each test, so one test's heartbeat never
    skips the next one's and no high-tick count carries over. Tests that assert on
    these files point them at their own tmp paths. Returns the two cleared paths."""
    try:
        import codec_heartbeat
    except Exception:
        return ()
    tmp = Path(tempfile.mkdtemp(prefix="codec-test-heartbeat-"))
    codec_heartbeat.HEARTBEAT_LOCK_PATH = str(tmp / "heartbeat.lock")
    codec_heartbeat.HEARTBEAT_STAMP_PATH = str(tmp / "heartbeat_last.json")
    codec_heartbeat.MEMORY_STATE_PATH = str(tmp / "memory_check.json")
    return (codec_heartbeat.HEARTBEAT_STAMP_PATH, codec_heartbeat.MEMORY_STATE_PATH)


_HEARTBEAT_TEMP_FILES = _isolate_heartbeat_guard()


@pytest.fixture(autouse=True)
def _heartbeat_runs_again():
    for path in _HEARTBEAT_TEMP_FILES:
        try:
            os.unlink(path)
        except OSError:
            pass
    yield


@pytest.fixture(autouse=True)
def _live_state_untouched():
    """Fail the test during (or just before) which the P-009 guard refused something."""
    yield
    guard = live_state_guard.ACTIVE
    new = guard.unreported() if guard else []
    if not new:
        return
    pytest.fail(f"test isolation (P-009): {len(new)} access(es) to the owner's live state "
                f"refused ({live_state_guard.describe(new)}); point the test at tmp_path",
                pytrace=False)


@pytest.fixture(autouse=True, scope="session")
def _owner_watch_list():
    """macOS only: fail the run if a named hazard changed (P-009, read-only, counts only).

    Watches three test file names in Documents, Desktop and Downloads, the
    agents/test_agent folder, the manual shift report and test-named entries of
    notifications.json, and shift_report_state.json (only when no scheduled report
    fired). Never whole folders: live daemons and Syncthing write there all the time.
    """
    if sys.platform != "darwin":
        yield
        return
    before = live_state_guard.watch_snapshot()
    yield
    changes = live_state_guard.watch_changes(before, live_state_guard.watch_snapshot())
    if changes:
        pytest.fail("test isolation (P-009): the owner's watch-list changed during this run: "
                    + "; ".join(changes), pytrace=False)
