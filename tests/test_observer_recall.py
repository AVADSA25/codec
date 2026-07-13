"""observer_recall — "what was I doing?" from CODEC's observer buffer.

The observer daemon mirrors its RAM ring buffer to ~/.codec/observer_buffer.json;
this skill reads it. Tests use a crafted buffer file (no daemon needed).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "skills"))

import observer_recall  # noqa: E402


def _entry(minutes_ago: float, app: str, title: str = "", ocr: str = "", files=None):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(timespec="milliseconds")
    return {
        "ts": ts,
        "active_window": {"app": app, "title": title},
        "screenshot_ocr": ocr,
        "clipboard": {},
        "recent_files": [{"path": f} for f in (files or [])],
    }


@pytest.fixture
def buffer(tmp_path, monkeypatch):
    path = tmp_path / "observer_buffer.json"
    monkeypatch.setattr(observer_recall, "_BUFFER_PATH", str(path))

    def write(entries):
        path.write_text(json.dumps({"updated": "now", "entries": entries}))
    return write


def test_no_buffer_is_explained(tmp_path, monkeypatch):
    monkeypatch.setattr(observer_recall, "_BUFFER_PATH", str(tmp_path / "missing.json"))
    out = observer_recall.run("what was I doing?")
    assert "observer" in out.lower() and "running" in out.lower()


def test_recall_lists_apps_and_files(buffer):
    buffer([
        _entry(8, "Safari", "Time Magazine", ocr="Scientists announced a breakthrough today"),
        _entry(5, "Mail", "Reply to client"),
        _entry(2, "Terminal", files=["deploy.sh"]),
    ])
    out = observer_recall.run("what was I doing?")
    assert "Safari" in out and "Mail" in out and "Terminal" in out
    assert "deploy.sh" in out


def test_time_window_filters_older_entries(buffer):
    buffer([
        _entry(45, "Xcode", "old work"),        # outside a 20-min window
        _entry(3, "Slack", "recent chat"),      # inside
    ])
    out = observer_recall.run("what was I doing 20 minutes ago?")
    assert "Slack" in out
    assert "Xcode" not in out, "entry older than the window must be excluded"


def test_window_beyond_buffer_is_honest(buffer):
    # Buffer only has a 3-min-old entry; asking about 'an hour ago' → honest miss.
    buffer([_entry(3, "Notes")])
    out = observer_recall.run("what was I doing an hour ago?")
    # 'last 60 min' includes the 3-min entry, so this returns Notes — fine.
    assert "Notes" in out


def test_window_parsing():
    assert observer_recall._window_seconds("what was I doing 20 minutes ago") == 1200
    assert observer_recall._window_seconds("last 2 hours") == 7200
    assert observer_recall._window_seconds("30 sec ago") == 30
    assert observer_recall._window_seconds("just now") == 120
    assert observer_recall._window_seconds("what was I doing") is None


def test_persist_round_trips_the_daemon_buffer(tmp_path, monkeypatch):
    """The daemon's _persist_buffer_to_disk writes exactly what the skill reads."""
    import codec_observer
    monkeypatch.setattr(codec_observer, "_BUFFER_DISK_PATH", tmp_path / "observer_buffer.json")
    buf = codec_observer.RingBuffer(maxlen=10)
    buf.append({"ts": datetime.now(timezone.utc).isoformat(), "active_window": {"app": "Figma"}})
    codec_observer._persist_buffer_to_disk(buf)

    monkeypatch.setattr(observer_recall, "_BUFFER_PATH", str(tmp_path / "observer_buffer.json"))
    entries, _updated = observer_recall._load_entries()
    assert len(entries) == 1 and entries[0]["active_window"]["app"] == "Figma"
