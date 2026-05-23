"""Tests for PR-4C — JSON write safety (C-3, C-4, M-1, M-2).

- codec_jsonstore.atomic_write_json: durable atomic write (tmp+fsync+replace, 0600).
- codec_jsonstore.file_lock: cross-process flock context manager (sidecar .lock).
- routes/_shared._write_notifications: atomic + capped to 500 (C-3 + M-1).
- codec_ask_user pending-questions: flock-guarded RMW + answered/timed_out
  eviction (C-4 + M-2).

Reference: docs/PR4C-JSON-WRITE-SAFETY-DESIGN.md.
"""
from __future__ import annotations

import json
import stat
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import codec_jsonstore  # noqa: E402


# ── codec_jsonstore.atomic_write_json ─────────────────────────────────────────


def test_atomic_write_json_roundtrip(tmp_path):
    p = tmp_path / "d.json"
    codec_jsonstore.atomic_write_json(p, {"a": 1, "b": [2, 3]})
    assert json.loads(p.read_text()) == {"a": 1, "b": [2, 3]}
    # no leftover tmp file in the dir
    assert [f.name for f in tmp_path.iterdir()] == ["d.json"]


def test_atomic_write_json_mode_0600(tmp_path):
    p = tmp_path / "d.json"
    codec_jsonstore.atomic_write_json(p, {"x": 1})
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_atomic_write_json_overwrites(tmp_path):
    p = tmp_path / "d.json"
    codec_jsonstore.atomic_write_json(p, {"v": 1})
    codec_jsonstore.atomic_write_json(p, {"v": 2})
    assert json.loads(p.read_text()) == {"v": 2}


def test_atomic_write_json_accepts_str_path(tmp_path):
    p = str(tmp_path / "s.json")
    codec_jsonstore.atomic_write_json(p, {"k": "v"})
    assert json.loads(Path(p).read_text()) == {"k": "v"}


# ── codec_jsonstore.file_lock ─────────────────────────────────────────────────


def test_file_lock_creates_sidecar_and_yields(tmp_path):
    p = tmp_path / "d.json"
    with codec_jsonstore.file_lock(p):
        codec_jsonstore.atomic_write_json(p, {"in": "lock"})
    assert json.loads(p.read_text()) == {"in": "lock"}
    assert (tmp_path / "d.json.lock").exists()   # sidecar, not the data file


def test_file_lock_rmw_sequential(tmp_path):
    p = tmp_path / "d.json"
    codec_jsonstore.atomic_write_json(p, {"items": []})
    for v in ("a", "b", "c"):
        with codec_jsonstore.file_lock(p):
            data = json.loads(p.read_text())
            data["items"].append(v)
            codec_jsonstore.atomic_write_json(p, data)
    assert json.loads(p.read_text())["items"] == ["a", "b", "c"]


# ── C-3 + M-1: notifications atomic + capped ──────────────────────────────────


def test_write_notifications_atomic_and_caps(monkeypatch, tmp_path):
    import routes._shared as sh
    nf = tmp_path / "notifications.json"
    monkeypatch.setattr(sh, "NOTIFICATIONS_PATH", str(nf))
    items = [{"id": f"n{i}", "created": f"t{i}"} for i in range(600)]  # newest-first
    sh._write_notifications(items)
    on_disk = json.loads(nf.read_text())
    assert len(on_disk) == 500           # M-1 cap
    assert on_disk[0]["id"] == "n0"      # newest kept (first 500)
    assert on_disk[-1]["id"] == "n499"


def test_write_notifications_under_cap_unchanged(monkeypatch, tmp_path):
    import routes._shared as sh
    nf = tmp_path / "notifications.json"
    monkeypatch.setattr(sh, "NOTIFICATIONS_PATH", str(nf))
    items = [{"id": f"n{i}"} for i in range(10)]
    sh._write_notifications(items)
    assert len(json.loads(nf.read_text())) == 10


# ── C-4 + M-2: pending_questions flock RMW + eviction ─────────────────────────


def test_pending_questions_evicts_old_resolved(monkeypatch, tmp_path):
    import codec_ask_user as au
    monkeypatch.setattr(au, "PENDING_QUESTIONS_PATH", tmp_path / "pq.json")
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()
    data = {"schema": 1, "pending_questions": [
        {"id": "q_old_ans", "status": "answered", "answered_at": old, "asked_at": old},
        {"id": "q_old_to", "status": "timed_out", "asked_at": old},
        {"id": "q_pending", "status": "pending", "asked_at": old},        # kept (pending)
        {"id": "q_recent_ans", "status": "answered", "answered_at": recent, "asked_at": recent},
    ]}
    au._save_pending_questions(data)
    ids = {q["id"] for q in au._load_pending_questions()["pending_questions"]}
    assert ids == {"q_pending", "q_recent_ans"}   # old resolved pruned; pending + recent kept


def test_pending_questions_rmw_roundtrip(monkeypatch, tmp_path):
    import codec_ask_user as au
    monkeypatch.setattr(au, "PENDING_QUESTIONS_PATH", tmp_path / "pq.json")
    au._save_pending_questions({"schema": 1, "pending_questions": [
        {"id": "q1", "status": "pending", "asked_at": datetime.now(timezone.utc).isoformat()}]})
    reloaded = au._load_pending_questions()
    assert [q["id"] for q in reloaded["pending_questions"]] == ["q1"]


# ── source invariants ─────────────────────────────────────────────────────────


def test_notifications_writer_atomic():
    src = (REPO / "routes" / "_shared.py").read_text()
    assert "codec_jsonstore" in src
    assert 'open(NOTIFICATIONS_PATH, "w")' not in src   # non-atomic writer gone


def test_ask_user_uses_file_lock():
    src = (REPO / "codec_ask_user.py").read_text()
    assert "codec_jsonstore" in src
    assert "file_lock" in src
