"""Cross-process-safe JSON persistence for ~/.codec/*.json (PR-4C, C-3/C-4).

Two primitives shared by the multi-daemon JSON writers (notifications.json,
pending_questions.json, …) which previously each rolled their own write and used
only per-process `threading.Lock`s:

- `atomic_write_json(path, data)` — durable atomic write: a unique same-dir tmp,
  `flush` + `os.fsync`, `os.replace`, `chmod 0600`. A reader never observes a
  half-written file (fixes the notifications.json corruption → fake-sample reseed,
  C-3).
- `file_lock(path)` — a context manager taking an exclusive `fcntl.flock` on a
  dedicated `<path>.lock` sidecar (NOT the data file, whose inode is swapped by
  the atomic replace). Hold it across a whole read→modify→write so two PROCESSES
  can't interleave and lose each other's append (fixes the pending_questions.json
  lost-question race, C-4). Per-open-fd + non-reentrant (like a `threading.Lock`).
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from typing import Any, Iterator


def atomic_write_json(path: Any, data: Any) -> None:
    """Atomically write `data` as JSON to `path` (tmp + fsync + replace, 0600)."""
    path = str(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextmanager
def file_lock(path: Any) -> Iterator[None]:
    """Exclusive cross-process lock on `<path>.lock` for the duration of the
    block. Use around a read-modify-write of `path` so concurrent daemons
    serialize instead of clobbering each other."""
    path = str(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    lock_file = open(path + ".lock", "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock_file.close()
