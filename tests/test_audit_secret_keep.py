"""P-081: the audit log keeps its signing secret and counts the lines it drops.

Design: docs/AUDIT-SECRET-KEEP-DESIGN.md.

Every test here talks to a fake `security` program written under tmp_path
(`FAKE_SECURITY`, a small file-backed store) and points the fallback store,
the audit log and its .lock at tmp paths. No test sets HOME, and none reaches
the real Keychain or the real ~/.codec.
"""
from __future__ import annotations

import json
import logging
import os
import stat
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


# A stand-in for the macOS `security` tool. It keeps its items in store.json next to
# itself, logs each call (argv, with the -w value masked) to calls.log, and
# obeys three switch files: exit.<cmd> (always exit with that code),
# once.<cmd> (exit with that code once, then delete the file) and
# sleep.<cmd> (sleep that many seconds first). Exit codes follow the real
# tool: 44 item not found, 45 item already exists, 51 locked.
FAKE_SECURITY = r'''#!{python}
import fcntl, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
def p(name):
    return os.path.join(HERE, name)
argv = sys.argv[1:]
cmd = argv[0] if argv else ""
logged, i = [], 0
while i < len(argv):
    logged.append(argv[i])
    if argv[i] == "-w" and i + 1 < len(argv):
        logged.append("***")
        i += 2
        continue
    i += 1
with open(p("calls.log"), "a") as f:
    f.write(json.dumps(logged) + "\n")
def number(name):
    try:
        with open(p(name)) as f:
            return float(f.read().strip())
    except (OSError, ValueError):
        return None
def opt(flag):
    if flag in argv and argv.index(flag) + 1 < len(argv):
        return argv[argv.index(flag) + 1]
    return ""
delay = number("sleep." + cmd)
if delay:
    time.sleep(delay)
once = number("once." + cmd)
if once is not None:
    os.unlink(p("once." + cmd))
    sys.exit(int(once))
forced = number("exit." + cmd)
if forced is not None:
    sys.exit(int(forced))
item = opt("-s") + "|" + opt("-a")
with open(p("store.lock"), "w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        with open(p("store.json")) as f:
            store = json.load(f)
    except (OSError, ValueError):
        store = {}
    if cmd == "find-generic-password":
        if item in store:
            sys.stdout.write(store[item] + "\n")
            sys.exit(0)
        sys.stderr.write("security: The specified item could not be found in the keychain.\n")
        sys.exit(44)
    if cmd == "add-generic-password":
        if item in store and "-U" not in argv:
            sys.stderr.write("security: The specified item already exists in the keychain.\n")
            sys.exit(45)
        store[item] = opt("-w")
    elif cmd == "delete-generic-password":
        if item not in store:
            sys.exit(44)
        del store[item]
    else:
        sys.exit(2)
    with open(p("store.json.tmp"), "w") as f:
        json.dump(store, f)
    os.replace(p("store.json.tmp"), p("store.json"))
sys.exit(0)
'''

PREFIX = "ai.avadigital.codec._test_p081"
ACCOUNT = "codec-test"
AUDIT_KEY_ITEM = f"{PREFIX}.audit_hmac_secret|{ACCOUNT}"


class FakeKeychain:
    """Test handle on the fake `security` program's folder."""

    def __init__(self, folder: Path):
        self.dir = folder

    def calls(self, cmd: str | None = None) -> list[list[str]]:
        log = self.dir / "calls.log"
        if not log.exists():
            return []
        rows = [json.loads(x) for x in log.read_text().splitlines() if x]
        return [r for r in rows if cmd is None or (r and r[0] == cmd)]

    def store(self) -> dict:
        f = self.dir / "store.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def seed(self, value: str) -> None:
        s = self.store()
        s[AUDIT_KEY_ITEM] = value
        (self.dir / "store.json").write_text(json.dumps(s))

    def switch(self, kind: str, cmd: str, value) -> None:
        (self.dir / f"{kind}.{cmd}").write_text(str(value))


@pytest.fixture
def fake_kc(tmp_path, monkeypatch):
    import codec_audit
    import codec_keychain as kc

    folder = tmp_path / "kc"
    folder.mkdir()
    fake = folder / "security"
    fake.write_text(FAKE_SECURITY.replace("{python}", sys.executable))
    os.chmod(fake, stat.S_IRWXU)

    monkeypatch.setattr(kc, "_SECURITY_BIN", str(fake))
    # The fake answers like the real tool on any OS, so CI on Linux runs the
    # Keychain path too.
    monkeypatch.setattr(kc, "is_keychain_available",
                        lambda: Path(kc._SECURITY_BIN).exists())
    monkeypatch.setattr(kc, "_SERVICE_PREFIX", PREFIX)
    monkeypatch.setattr(kc, "_account", lambda: ACCOUNT)
    monkeypatch.setattr(kc, "_FALLBACK_KEY_PATH", tmp_path / "secret.key")
    monkeypatch.setattr(kc, "_FALLBACK_STORE_PATH", tmp_path / "secrets.enc.json")
    monkeypatch.setattr(codec_audit, "_AUDIT_DIR", tmp_path)
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    kc._invalidate_audit_hmac_cache()
    yield FakeKeychain(folder)
    kc._invalidate_audit_hmac_cache()


@pytest.fixture
def clock(monkeypatch):
    """A hand-moved clock for the 30 s retry window."""
    import codec_keychain as kc
    now = {"t": 1000.0}
    monkeypatch.setattr(kc, "_audit_hmac_clock", lambda: now["t"])
    return now


def _secret_hex(s: bytes | None) -> str | None:
    return s.hex() if s is not None else None


# ── Golden record: the envelope is unchanged ─────────────────────────────────

GOLDEN_SECRET_HEX = "00112233445566778899aabbccddeeff" * 2
# Captured from origin/main 4a3c76e (before P-081) with the same inputs.
GOLDEN_LINE = (
    '{"client_id":null,"context_len":0,"duration_ms":null,"error_type":null,'
    '"event":"golden_event","extra":{"correlation_id":"0123456789ab","n":1},'
    '"hmac":"ce4ffbc37de27abf17e84388bbcd79f789ccdd37b86ca65f59ac974227ef3e6e",'
    '"level":"info","message":"key <REDACTED:openai_or_anthropic_key> here",'
    '"outcome":"ok","schema":1,"source":"codec-test","task_len":12,'
    '"tool":"golden_tool","transport":"local","ts":"2026-11-03T12:00:00.123+00:00"}\n'
)


class _FixedDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 3, 12, 0, 0, 123000, tzinfo=timezone.utc)


def test_golden_record_envelope_unchanged(fake_kc, monkeypatch, tmp_path):
    import codec_audit
    monkeypatch.delenv("CODEC_MCP_TRANSPORT", raising=False)
    monkeypatch.delenv("CODEC_PROCESS", raising=False)
    monkeypatch.setattr(codec_audit, "datetime", _FixedDT)
    fake_kc.seed(GOLDEN_SECRET_HEX)

    codec_audit.audit(
        "golden_tool", event="golden_event", source="codec-test",
        task_len=12, outcome="ok", level="info",
        message="key sk-abcdefghijklmnopqrstuvwxyz0123 here",
        correlation_id="0123456789ab", extra={"n": 1, "ts": "dropped"},
    )

    assert (tmp_path / "audit.log").read_text() == GOLDEN_LINE
    result = codec_audit.verify_audit_log()
    assert result["integrity_ok"] is True, result
    assert result["signed_lines"] == result["total_lines"] == 1


# ── Keychain answers: locked / other / missing ───────────────────────────────


def test_locked_keychain_returns_none_and_never_adds(fake_kc, clock, monkeypatch):
    import codec_audit
    import codec_keychain as kc
    emitted = []
    monkeypatch.setattr(kc, "_kc_log_event", lambda *a, **k: emitted.append(a))
    monkeypatch.setattr(codec_audit, "audit", lambda *a, **k: emitted.append(a))
    fake_kc.seed(GOLDEN_SECRET_HEX)
    fake_kc.switch("exit", "find-generic-password", 51)

    assert kc.get_audit_hmac_secret() is None
    assert len(fake_kc.calls()) == 1
    assert fake_kc.calls("add-generic-password") == []

    clock["t"] += 29.0
    assert kc.get_audit_hmac_secret() is None
    assert len(fake_kc.calls()) == 1, "a second call within 30 s must not run security"

    clock["t"] += 2.0
    assert kc.get_audit_hmac_secret() is None
    assert len(fake_kc.calls()) == 2, "after 30 s it asks the Keychain again"
    assert fake_kc.calls("add-generic-password") == []
    assert fake_kc.store()[AUDIT_KEY_ITEM] == GOLDEN_SECRET_HEX, "stored secret untouched"
    assert emitted == [], "the secret getter must never write an audit line"


@pytest.mark.parametrize("answer", ["exit36", "exit1", "timeout"])
def test_other_failures_are_not_treated_as_missing(fake_kc, clock, monkeypatch, answer):
    import codec_keychain as kc
    fake_kc.seed(GOLDEN_SECRET_HEX)
    if answer == "timeout":
        # 1 s: the fake must start and log its call before the kill, even
        # on a slow CI runner (it takes about 30 ms on the Studio).
        monkeypatch.setattr(kc, "_SECURITY_TIMEOUT", 1.0)
        fake_kc.switch("sleep", "find-generic-password", 3)
    else:
        fake_kc.switch("exit", "find-generic-password", int(answer[4:]))

    assert kc.get_audit_hmac_secret() is None
    assert kc.get_audit_hmac_secret() is None
    assert len(fake_kc.calls()) == 1
    assert fake_kc.calls("add-generic-password") == []
    assert fake_kc.store()[AUDIT_KEY_ITEM] == GOLDEN_SECRET_HEX


def test_missing_secret_bootstraps_exactly_once(fake_kc, clock):
    import codec_keychain as kc
    first = kc.get_audit_hmac_secret()
    assert isinstance(first, bytes) and len(first) == 32
    adds = fake_kc.calls("add-generic-password")
    assert len(adds) == 1
    assert "-U" not in adds[0], "the bootstrap must never overwrite an existing item"
    assert fake_kc.store()[AUDIT_KEY_ITEM] == first.hex()

    assert kc.get_audit_hmac_secret() == first  # cached
    kc._invalidate_audit_hmac_cache()
    assert kc.get_audit_hmac_secret() == first  # read back, not made again
    assert len(fake_kc.calls("add-generic-password")) == 1


def test_bootstrap_race_keeps_the_secret_another_process_made(fake_kc, clock):
    """Another daemon created the item between our 'not found' and our add:
    the add is refused (exit 45) and we use the stored secret."""
    import codec_keychain as kc
    theirs = "ab" * 32
    fake_kc.seed(theirs)
    fake_kc.switch("once", "find-generic-password", 44)

    assert _secret_hex(kc.get_audit_hmac_secret()) == theirs
    assert len(fake_kc.calls("add-generic-password")) == 1
    assert fake_kc.store()[AUDIT_KEY_ITEM] == theirs


def test_corrupt_secret_is_not_replaced(fake_kc, clock):
    import codec_keychain as kc
    fake_kc.seed("not-hex")
    assert kc.get_audit_hmac_secret() is None
    assert kc.get_audit_hmac_secret() is None
    assert len(fake_kc.calls()) == 1
    assert fake_kc.calls("add-generic-password") == []
    assert fake_kc.store()[AUDIT_KEY_ITEM] == "not-hex"


# ── Fallback store: processes racing end with one secret ─────────────────────

_RACER = textwrap.dedent('''
    import sys, time
    from pathlib import Path
    repo, tmp = sys.argv[1], Path(sys.argv[2])
    sys.path.insert(0, repo)
    import codec_audit
    codec_audit._AUDIT_DIR = tmp
    codec_audit._AUDIT_LOG = tmp / "audit.log"
    import codec_keychain as kc
    kc._SECURITY_BIN = str(tmp / "no-security-here")
    kc.is_keychain_available = lambda: False
    kc._FALLBACK_KEY_PATH = tmp / "secret.key"
    kc._FALLBACK_STORE_PATH = tmp / "secrets.enc.json"
    real_set = kc._fallback_set
    def slow_set(key, value):
        time.sleep(0.4)   # widen the read-then-write window
        return real_set(key, value)
    kc._fallback_set = slow_set
    while not (tmp / "go").exists():
        time.sleep(0.005)
    s = kc.get_audit_hmac_secret()
    print(s.hex() if s else "NONE")
''')


def test_fallback_store_race_ends_with_one_secret(tmp_path, monkeypatch):
    import codec_keychain as kc
    race = tmp_path / "race"
    race.mkdir()
    procs = [
        subprocess.Popen([sys.executable, "-c", _RACER, str(REPO), str(race)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(3)
    ]
    time.sleep(0.5)
    (race / "go").write_text("")
    outs = []
    for p in procs:
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0, err
        outs.append(out.strip())

    assert "NONE" not in outs
    assert len(set(outs)) == 1, f"every process must end with the same secret: {outs}"
    monkeypatch.setattr(kc, "_FALLBACK_KEY_PATH", race / "secret.key")
    monkeypatch.setattr(kc, "_FALLBACK_STORE_PATH", race / "secrets.enc.json")
    assert kc._fallback_get(kc.KEY_AUDIT_HMAC_SECRET) == outs[0]
    assert (race / "secrets.enc.json.lock").exists(), "the sidecar lock was used"


# ── Lines the writer drops are counted ───────────────────────────────────────


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def audit_failures(monkeypatch):
    import codec_audit
    now = {"t": 5000.0}
    monkeypatch.setattr(codec_audit, "_write_failures", 0)
    monkeypatch.setattr(codec_audit, "_last_fail_warning", None)
    monkeypatch.setattr(codec_audit, "_fail_clock", lambda: now["t"])
    h = _Records()
    logging.getLogger("codec.audit").addHandler(h)
    yield now, h
    logging.getLogger("codec.audit").removeHandler(h)


def test_unwritable_log_is_counted_and_warned_once_a_minute(fake_kc, audit_failures, tmp_path):
    import codec_audit
    now, h = audit_failures
    fake_kc.seed(GOLDEN_SECRET_HEX)
    (tmp_path / "audit.log").mkdir()   # the log path cannot be opened for append

    for _ in range(3):
        codec_audit.audit(event="dropped", source="codec-test")
    assert codec_audit.write_failures() == 3
    assert len(h.records) == 1

    now["t"] += 59.0
    codec_audit.audit(event="dropped", source="codec-test")
    assert codec_audit.write_failures() == 4
    assert len(h.records) == 1

    now["t"] += 2.0
    codec_audit.audit(event="dropped", source="codec-test")
    assert codec_audit.write_failures() == 5
    assert len(h.records) == 2
    assert "5" in h.records[-1].getMessage()


def test_verify_still_ok_around_a_full_disk(fake_kc, audit_failures, monkeypatch):
    import errno
    import codec_audit
    fake_kc.seed(GOLDEN_SECRET_HEX)
    codec_audit.audit(event="before_1", source="codec-test")
    codec_audit.audit(event="before_2", source="codec-test")
    before = codec_audit.verify_audit_log()
    assert before["integrity_ok"] is True and before["signed_lines"] == 2, before

    def full_disk():
        raise OSError(errno.ENOSPC, "No space left on device")
    with monkeypatch.context() as m:
        m.setattr(codec_audit, "_open_audit_log_append", full_disk)
        codec_audit.audit(event="lost_1", source="codec-test")
        codec_audit.audit(event="lost_2", source="codec-test")
    codec_audit.audit(event="after", source="codec-test")

    after = codec_audit.verify_audit_log()
    assert after["integrity_ok"] is True and after["signed_lines"] == 3, after
    assert codec_audit.write_failures() == 2, "the two lost lines are counted"


def test_api_status_shows_audit_write_failures(fake_kc, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import codec_audit
    import routes.health as health
    monkeypatch.setattr(health, "CONFIG_PATH", str(tmp_path / "no-config.json"))
    monkeypatch.setattr(codec_audit, "_write_failures", 7)

    app = FastAPI()
    app.include_router(health.router)
    body = TestClient(app).get("/api/status").json()
    assert body["audit_write_failures"] == 7
    assert "alive" in body and "config" in body
