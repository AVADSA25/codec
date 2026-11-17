"""Headless secret store: every write runs under one file lock.

Design: docs/HEADLESS-SECRET-LOCK-DESIGN.md (known issue found 2026-11-03,
P-081 review).

The race tests start real processes that share one fallback store under
tmp_path; each one slows its own save (or key creation) so the old
load-change-save window is wide. No test sets HOME, reaches the real Keychain
or touches the real ~/.codec: the racers point codec_audit and the fallback
paths at tmp_path and `_SECURITY_BIN` at a path that does not exist.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# One racer: waits for the "go" file, runs one action against the shared store
# in `tmp`, prints its result. Actions: set NAME VALUE, delete NAME, kcset NAME
# VALUE (the public keychain_set, which also writes an audit line), key, audit.
_RACER = textwrap.dedent('''
    import secrets, sys, time
    from pathlib import Path
    repo, tmp, me, slow_save, slow_rand = sys.argv[1], Path(sys.argv[2]), sys.argv[3], \\
        float(sys.argv[4]), float(sys.argv[5])
    action = sys.argv[6:]
    sys.path.insert(0, repo)
    import codec_audit
    codec_audit._AUDIT_DIR = tmp
    codec_audit._AUDIT_LOG = tmp / "audit.log"
    import codec_keychain as kc
    kc._SECURITY_BIN = str(tmp / "no-security-here")
    kc.is_keychain_available = lambda: False
    kc._FALLBACK_KEY_PATH = tmp / "secret.key"
    kc._FALLBACK_STORE_PATH = tmp / "secrets.enc.json"
    real_save = kc._fallback_save
    def slow(store):
        time.sleep(slow_save)   # widen the load-then-save window
        return real_save(store)
    kc._fallback_save = slow
    real_rand = secrets.token_bytes
    def slow_bytes(n=None):
        time.sleep(slow_rand)   # widen the check-then-create window of the key
        return real_rand(n)
    secrets.token_bytes = slow_bytes
    (tmp / ("ready." + me)).write_text("")
    while not (tmp / "go").exists():
        time.sleep(0.005)
    if action[0] == "set":
        print(kc._fallback_set(action[1], action[2]))
    elif action[0] == "delete":
        print(kc._fallback_delete(action[1]))
    elif action[0] == "kcset":
        print(kc.keychain_set(action[1], action[2]))
    elif action[0] == "key":
        print(kc._fallback_key().hex())
    elif action[0] == "audit":
        s = kc.get_audit_hmac_secret()
        print(s.hex() if s else "NONE")
''')


def _race(folder: Path, actions, slow_save=0.0, slow_rand=0.0) -> list[str]:
    """Start one racer per action, let them all go at once, return their output."""
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _RACER, str(REPO), str(folder), str(i),
             str(slow_save), str(slow_rand), *action],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for i, action in enumerate(actions)
    ]
    deadline = time.time() + 30
    while not all((folder / f"ready.{i}").exists() for i in range(len(procs))):
        assert time.time() < deadline, "racers did not start"
        time.sleep(0.01)
    (folder / "go").write_text("")
    outs = []
    for p in procs:
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0, err
        outs.append(out.strip())
    return outs


@pytest.fixture
def store(tmp_path, monkeypatch):
    """This process's codec_keychain on a fallback store in tmp_path."""
    import codec_keychain as kc
    monkeypatch.setattr(kc, "is_keychain_available", lambda: False)
    monkeypatch.setattr(kc, "_FALLBACK_KEY_PATH", tmp_path / "secret.key")
    monkeypatch.setattr(kc, "_FALLBACK_STORE_PATH", tmp_path / "secrets.enc.json")
    kc._invalidate_audit_hmac_cache()
    kc._invalidate_negative_cache()
    yield kc
    kc._invalidate_audit_hmac_cache()
    kc._invalidate_negative_cache()


def test_concurrent_sets_and_delete_all_survive(store, tmp_path):
    kc = store
    kc._fallback_key()                      # one key already, so only the store races
    assert kc._fallback_set("old", "gone-soon")
    outs = _race(tmp_path, [["set", "alpha", "A"], ["set", "beta", "B"],
                            ["set", "gamma", "C"], ["delete", "old"]], slow_save=0.3)
    assert outs == ["True"] * 4
    assert kc._fallback_get("alpha") == "A"
    assert kc._fallback_get("beta") == "B"
    assert kc._fallback_get("gamma") == "C"
    assert kc._fallback_get("old") is None


def test_first_key_calls_at_once_give_one_key(store, tmp_path):
    kc = store
    outs = _race(tmp_path, [["key"]] * 4, slow_rand=0.3)
    assert len(set(outs)) == 1, f"every process must get the same key: {outs}"
    assert kc._FALLBACK_KEY_PATH.read_bytes().hex() == outs[0]
    assert (kc._FALLBACK_KEY_PATH.stat().st_mode & 0o777) == 0o600


def test_audit_secret_survives_a_concurrent_other_key_write(store, tmp_path):
    kc = store
    kc._fallback_key()
    outs = _race(tmp_path, [["audit"], ["kcset", kc.KEY_INTERNAL_TOKEN, "tok-123"]],
                 slow_save=0.4)
    audit_hex, set_ok = outs
    assert audit_hex != "NONE" and set_ok == "True"
    assert kc._fallback_get(kc.KEY_AUDIT_HMAC_SECRET) == audit_hex, \
        "the audit secret the process signs with must be the stored one"
    assert kc._fallback_get(kc.KEY_INTERNAL_TOKEN) == "tok-123"
    assert kc.get_audit_hmac_secret().hex() == audit_hex


def _in_thread(fn, timeout=10.0):
    box = {}
    t = threading.Thread(target=lambda: box.update(v=fn()), daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "the audit secret path waited on its own lock"
    return box["v"]


def test_audit_path_never_waits_on_its_own_lock(store, tmp_path):
    kc = store
    # Empty store, no key: the create runs inside the lock and makes the key there.
    secret = _in_thread(kc.get_audit_hmac_secret)
    assert secret is not None and len(secret) == 32
    assert kc._fallback_get(kc.KEY_AUDIT_HMAC_SECRET) == secret.hex()

    # An entry with no key to decode it: None, nothing replaced, no key made.
    entry = kc._fallback_load()[kc.KEY_AUDIT_HMAC_SECRET]
    kc._FALLBACK_KEY_PATH.unlink()
    kc._invalidate_audit_hmac_cache()
    assert _in_thread(kc.get_audit_hmac_secret) is None
    assert kc._fallback_load()[kc.KEY_AUDIT_HMAC_SECRET] == entry
    assert not kc._FALLBACK_KEY_PATH.exists()


def test_mac_keychain_path_touches_no_fallback_file(tmp_path, monkeypatch):
    import codec_keychain as kc
    import live_state_guard
    fake = live_state_guard.write_fake_security(tmp_path / "kc")
    monkeypatch.setattr(kc, "_SECURITY_BIN", str(fake))
    monkeypatch.setattr(kc, "is_keychain_available", lambda: True)
    monkeypatch.setattr(kc, "_FALLBACK_KEY_PATH", tmp_path / "secret.key")
    monkeypatch.setattr(kc, "_FALLBACK_STORE_PATH", tmp_path / "secrets.enc.json")
    kc._invalidate_audit_hmac_cache()
    kc._invalidate_negative_cache()
    try:
        assert kc.keychain_set("lock_test_key", "v1")
        assert kc.keychain_get("lock_test_key") == "v1"
        assert kc.keychain_delete("lock_test_key")
        assert kc.get_audit_hmac_secret() is not None
    finally:
        kc._invalidate_audit_hmac_cache()
        kc._invalidate_negative_cache()
    for name in ("secret.key", "secrets.enc.json", "secrets.enc.json.lock"):
        assert not (tmp_path / name).exists(), name
