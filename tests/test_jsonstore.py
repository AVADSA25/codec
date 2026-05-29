"""Fix #9 Phase 0: harden codec_jsonstore primitives.

- atomic_write_json gains optional `default=` (custom JSON serializer, e.g.
  str for datetime) and `sort_keys=` passthrough, so it can subsume the
  hand-rolled helpers (codec_ask_user._atomic_write_text uses default=str).
- new read_modify_write(path, fn) standardizes the lock+load+mutate+atomic-write
  pattern so a future RMW site can't forget the file_lock.
"""
import json
import os
import stat
import threading
import time
from datetime import datetime, timezone

import pytest

import codec_jsonstore


def test_atomic_write_json_roundtrip_and_0600(tmp_path):
    p = tmp_path / "x.json"
    codec_jsonstore.atomic_write_json(p, {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1}
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_atomic_write_json_default_serializer(tmp_path):
    p = tmp_path / "dt.json"
    dt = datetime(2026, 5, 29, tzinfo=timezone.utc)
    codec_jsonstore.atomic_write_json(p, {"when": dt}, default=str)
    assert "2026-05-29" in p.read_text()


def test_atomic_write_json_raises_without_default_and_leaves_no_file(tmp_path):
    p = tmp_path / "bad.json"
    with pytest.raises(TypeError):
        codec_jsonstore.atomic_write_json(p, {"when": datetime(2026, 5, 29)})
    assert not p.exists(), "a failed write must not leave a partial target file"


def test_atomic_write_json_sort_keys(tmp_path):
    p = tmp_path / "s.json"
    codec_jsonstore.atomic_write_json(p, {"b": 1, "a": 2}, sort_keys=True)
    text = p.read_text()
    assert text.index('"a"') < text.index('"b"')


def test_read_modify_write_applies_and_persists(tmp_path):
    p = tmp_path / "rmw.json"
    codec_jsonstore.atomic_write_json(p, {"n": 0})

    def bump(d):
        d["n"] += 1
        return d

    out = codec_jsonstore.read_modify_write(p, bump)
    assert out == {"n": 1}
    assert json.loads(p.read_text()) == {"n": 1}


def test_read_modify_write_missing_file_uses_default_factory(tmp_path):
    p = tmp_path / "missing.json"

    def add(d):
        d["k"] = "v"
        return d

    out = codec_jsonstore.read_modify_write(p, add, default_factory=dict)
    assert out == {"k": "v"}


def test_read_modify_write_no_clobber_under_concurrency(tmp_path):
    p = tmp_path / "conc.json"
    codec_jsonstore.atomic_write_json(p, {"items": []})
    n = 16
    barrier = threading.Barrier(n)

    def worker(i):
        barrier.wait()

        def add(d):
            time.sleep(0.005)  # widen the read-modify-write window
            d["items"] = d.get("items", []) + [i]
            return d

        codec_jsonstore.read_modify_write(p, add)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    items = json.loads(p.read_text())["items"]
    assert sorted(items) == list(range(n)), f"read_modify_write clobbered: {sorted(items)}"
