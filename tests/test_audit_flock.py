"""Tests for PR-4E (H-3) — cross-process flock around audit.log write + rotation.

All 11 PM2 daemons append to ~/.codec/audit.log. The in-process threading.Lock
doesn't serialize across processes, so concurrent rotation (Race A), write-
during-rotation (Race B), and >PIPE_BUF line interleaving (Race C) can corrupt
or split entries. PR-4E wraps _write's critical section (rotate+open+write+close)
in codec_jsonstore.file_lock(_AUDIT_LOG) — flock(LOCK_EX) on the stable
`audit.log.lock` sidecar.

Mirrors test_audit_integrity.py's isolation fixture. Reference:
docs/PR4E-AUDIT-FLOCK-DESIGN.md, docs/audits/PHASE-1-RELIABILITY.md H-3.
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Redirect audit log + keychain to tmp per test (mirrors test_audit_integrity)."""
    import codec_audit
    import codec_keychain as kc

    test_audit = tmp_path / "audit.log"
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", test_audit)
    monkeypatch.setattr(codec_audit, "_AUDIT_DIR", tmp_path)
    # Force the file-based HMAC fallback (no macOS Keychain shellout / GUI
    # prompt) — deterministic + fast + matches CI/Linux. The fallback store is
    # redirected into tmp so the secret never touches the real keystore.
    monkeypatch.setattr(kc, "is_keychain_available", lambda: False)
    monkeypatch.setattr(kc, "_FALLBACK_KEY_PATH", tmp_path / "secret.key")
    monkeypatch.setattr(kc, "_FALLBACK_STORE_PATH", tmp_path / "secrets.enc.json")
    monkeypatch.setattr(kc, "_SERVICE_PREFIX",
                        f"ai.avadigital.codec._test_flock_{os.getpid()}_{tmp_path.name}")
    kc._invalidate_audit_hmac_cache()
    yield codec_audit, test_audit, tmp_path
    kc._invalidate_audit_hmac_cache()


# ── flock applied (red → green) ───────────────────────────────────────────────


def test_write_creates_lock_sidecar(_isolate):
    codec_audit, test_audit, tmp = _isolate
    codec_audit.audit(event="test_event", source="codec-test")
    assert (tmp / "audit.log.lock").exists(), (
        "the cross-process flock sidecar audit.log.lock must be created on write (H-3)"
    )


def test_write_uses_file_lock_around_rotation(_isolate):
    """Source invariant: rotation + write happen INSIDE the cross-process lock."""
    src = (REPO / "codec_audit.py").read_text()
    body = src[src.index("def _write("):]
    body = body[:body.index("\ndef ", 1)]
    i_lock = body.find("file_lock(")
    i_rotate = body.find("_rotate_if_needed()")
    i_write = body.find("f.write(line)")
    assert i_lock != -1, "_write must use codec_jsonstore.file_lock (cross-process)"
    assert i_lock < i_rotate < i_write, (
        "rotation AND the append must be inside the file_lock block "
        f"(lock@{i_lock} rotate@{i_rotate} write@{i_write})"
    )


# ── concurrency / corruption (guard) ──────────────────────────────────────────


def test_concurrent_writes_no_corruption(_isolate):
    """Within-process regression guard: the flock (+_LOCK) keeps concurrent
    appends clean — every line present, every line valid JSON, every cid seen.
    (True cross-process Race A/B/C serialization is provided by reusing the
    PR-4C `codec_jsonstore.file_lock`, which is cross-process-tested in
    tests/test_json_write_safety.py — a single pytest process can't span PM2
    daemons.)"""
    codec_audit, test_audit, _ = _isolate
    N_THREADS, N_WRITES = 8, 200
    cids = [f"{t:08x}{j:04x}" for t in range(N_THREADS) for j in range(N_WRITES)]

    def worker(tid):
        for j in range(N_WRITES):
            codec_audit.audit(event="stress", source="codec-test",
                              correlation_id=cids[tid * N_WRITES + j])

    with ThreadPoolExecutor(max_workers=N_THREADS) as ex:
        list(ex.map(worker, range(N_THREADS)))

    lines = test_audit.read_text().splitlines()
    assert len(lines) == N_THREADS * N_WRITES, "every line must be present (no loss)"
    seen = set()
    for ln in lines:
        obj = json.loads(ln)  # raises on any interleaved/corrupt line
        seen.add(obj.get("extra", {}).get("correlation_id"))
    assert seen == set(cids), "every correlation_id must appear exactly in the file"


# ── rotation under writes (guard) ─────────────────────────────────────────────


def test_rotation_preserves_old_and_new(_isolate):
    codec_audit, test_audit, tmp = _isolate
    codec_audit.audit(event="day1", source="codec-test")
    assert test_audit.exists()
    # Backdate the log to yesterday so the next write triggers rotation.
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    ts = datetime(yesterday.year, yesterday.month, yesterday.day, 12, tzinfo=timezone.utc).timestamp()
    os.utime(test_audit, (ts, ts))

    codec_audit.audit(event="day2", source="codec-test")

    rotated = tmp / f"audit.log.{yesterday.isoformat()}"
    assert rotated.exists(), "yesterday's entries must be rotated into audit.log.<date>"
    old = [json.loads(x) for x in rotated.read_text().splitlines()]
    new = [json.loads(x) for x in test_audit.read_text().splitlines()]
    assert any(o["event"] == "day1" for o in old), "old entry must survive in rotated file"
    assert any(n["event"] == "day2" for n in new), "new entry must be in the fresh log"


# ── integrity preserved (guard) ───────────────────────────────────────────────


def test_hmac_integrity_preserved(_isolate):
    codec_audit, _, _ = _isolate
    for i in range(5):
        codec_audit.audit(event="tool_result", source="codec-test", correlation_id=f"cid{i:08x}0000")
    result = codec_audit.verify_audit_log()
    assert result["integrity_ok"] is True, result
    assert result["broken_lines"] == 0, result
    assert result["signed_lines"] == result["total_lines"] == 5, result


# ── never raises (guard) ──────────────────────────────────────────────────────


def test_audit_never_raises_if_flock_fails(_isolate, monkeypatch):
    codec_audit, _, _ = _isolate

    def boom(*a, **k):
        raise OSError("flock unavailable")
    monkeypatch.setattr(codec_audit.codec_jsonstore, "file_lock", boom)
    # Must not propagate (line is dropped, but audit() never crashes a caller).
    codec_audit.audit(event="x", source="codec-test")
