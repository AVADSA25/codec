"""Keep the launchd services' log files small (P-032, docs/LAUNCHD-STATUS-DESIGN.md).

launchd appends each service's output to ~/Library/Logs/CODEC/<name>.out|.err and never rotates
them. A log file there over MAX_BYTES keeps its last KEEP_BYTES, cut at a line start, in the same
file: launchd opened it with O_APPEND, so the service goes on writing into the trimmed file. The
heartbeat runs `trim_folder` every cycle on launchd; `python3 codec_log_trim.py [--dir DIR]` runs it
by hand. Stdlib only. Names and sizes are reported, never a line of a log.
"""
from __future__ import annotations

import argparse
import os
import stat
import sys

MAX_BYTES = 10_000_000   # 10 MB, so also under 10 MiB
KEEP_BYTES = 5_000_000   # what a trimmed file keeps: the next trim is days away, not one line
SUFFIXES = (".out", ".err", ".log")
DEFAULT_DIR = os.path.expanduser("~/Library/Logs/CODEC")
_REREADS = 3


def _pread_all(fd: int, size: int, offset: int) -> bytes:
    chunks, done = [], 0
    while done < size:
        chunk = os.pread(fd, size - done, offset + done)
        if not chunk:
            break
        chunks.append(chunk)
        done += len(chunk)
    return b"".join(chunks)


def trim_file(path: str, max_bytes: int = MAX_BYTES, keep_bytes: int = KEEP_BYTES) -> tuple | None:
    """Trim one regular file over `max_bytes` to its last lines (at most `keep_bytes`, plus what a
    writer appends meanwhile). Returns (bytes before, bytes after), or None when nothing was done.
    A link or anything but a regular file is never opened."""
    keep_bytes = max(0, min(keep_bytes, max_bytes))
    fd = os.open(path, os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size <= max_bytes:
            return None
        size = st.st_size
        start = max(0, size - keep_bytes - 1)
        tail = _pread_all(fd, size - start, start)
        if start > 0:
            cut = tail.find(b"\n")
            # The byte before the kept part is read too: a newline there means the tail starts a line.
            tail = tail[cut + 1:] if cut >= 0 else tail[-keep_bytes:] if keep_bytes else b""
        for _ in range(_REREADS):  # a writer appended while the tail was read: keep that too
            now = os.fstat(fd).st_size
            if now <= size:
                break
            tail += _pread_all(fd, now - size, size)
            size = now
        os.ftruncate(fd, 0)
        view = memoryview(tail)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        return st.st_size, os.fstat(fd).st_size
    finally:
        os.close(fd)


def trim_folder(folder: str = DEFAULT_DIR, max_bytes: int = MAX_BYTES, keep_bytes: int = KEEP_BYTES) -> list:
    """Trim every .out, .err and .log file directly in `folder` (no sub-folders, no links).
    Returns [(file name, bytes before, bytes after)] for the files trimmed. A missing folder or an
    unreadable file is skipped."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    done = []
    for name in names:
        path = os.path.join(folder, name)
        if not name.endswith(SUFFIXES) or os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            result = trim_file(path, max_bytes, keep_bytes)
        except OSError:
            continue
        if result:
            done.append((name, *result))
    return done


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description="Trim CODEC's launchd service logs to their last lines.")
    ap.add_argument("--dir", default=DEFAULT_DIR, help="log folder (default ~/Library/Logs/CODEC)")
    args = ap.parse_args(argv)
    done = trim_folder(args.dir)
    for name, before, after in done:
        print(f"{name}: {before} -> {after} bytes")
    print(f"{len(done)} log file(s) trimmed in {args.dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
