"""launchd's service logs stay at or under 10 MB (P-032, docs/LAUNCHD-STATUS-DESIGN.md).

Every file here is in a temp folder: never ~/Library/Logs on this Mac.
"""
from __future__ import annotations

import os

import pytest

import codec_heartbeat
import codec_log_trim as lt
import codec_supervisor


def _fixture_log(path, size=12_000_000):
    """A log of numbered lines, `size` bytes or a little more; returns its last line."""
    line_no, written = 0, 0
    with open(path, "wb") as f:
        while written < size:
            line = f"2026-11-03 10:00:00 [codec-dashboard] INFO line {line_no:08d} ok\n".encode()
            f.write(line)
            written += len(line)
            line_no += 1
    return line.decode()


def _all_logs_at_most_10_mb(folder):
    return all(os.path.getsize(os.path.join(folder, n)) <= 10_000_000
               for n in os.listdir(folder) if n.endswith(lt.SUFFIXES))


def test_a_12_mb_log_keeps_its_last_lines_in_the_same_file(tmp_path):
    log = tmp_path / "codec-dashboard.err"
    last = _fixture_log(log)
    before = log.stat()
    assert before.st_size >= 12_000_000
    done = lt.trim_folder(str(tmp_path))
    assert done == [("codec-dashboard.err", before.st_size, log.stat().st_size)]
    after = log.stat()
    assert after.st_size <= lt.KEEP_BYTES <= 10_000_000
    assert after.st_ino == before.st_ino and after.st_mode == before.st_mode, "trimmed in place"
    text = log.read_text()
    assert text.endswith(last), "the newest line is kept"
    assert text.startswith("2026-11-03 10:00:00 [codec-dashboard] INFO line "), "it starts at a line"
    numbers = [int(x.split("line ")[1].split()[0]) for x in text.splitlines()]
    assert numbers == list(range(numbers[0], numbers[-1] + 1)), "a run of whole lines, none lost"
    assert _all_logs_at_most_10_mb(tmp_path)


def test_a_writer_that_keeps_the_file_open_writes_into_the_trimmed_file(tmp_path):
    log = tmp_path / "codec-telegram.out"
    _fixture_log(log)
    fd = os.open(log, os.O_WRONLY | os.O_APPEND)  # how launchd opens StandardOutPath
    try:
        lt.trim_file(str(log))
        os.write(fd, b"after the trim\n")
    finally:
        os.close(fd)
    assert log.read_text().endswith("after the trim\n") and log.stat().st_size <= 10_000_000


def test_small_files_other_names_and_links_are_left_alone(tmp_path):
    small = tmp_path / "codec-imessage.err"
    small.write_text("one line\n")
    other = tmp_path / "notes.txt"
    _fixture_log(other)
    target = tmp_path / "elsewhere.bin"
    _fixture_log(target)
    (tmp_path / "linked.log").symlink_to(target)
    sub = tmp_path / "sub"
    sub.mkdir()
    _fixture_log(sub / "deep.log")
    assert lt.trim_folder(str(tmp_path)) == []
    assert small.read_text() == "one line\n"
    assert other.stat().st_size >= 12_000_000 and target.stat().st_size >= 12_000_000
    assert (sub / "deep.log").stat().st_size >= 12_000_000, "no sub-folders"
    assert lt.trim_folder(str(tmp_path / "missing")) == []


def test_a_log_with_no_newline_keeps_its_last_bytes(tmp_path):
    log = tmp_path / "blob.log"
    log.write_bytes(b"x" * 11_000_000)
    assert lt.trim_file(str(log)) == (11_000_000, lt.KEEP_BYTES)
    assert log.read_bytes() == b"x" * lt.KEEP_BYTES


def test_the_command_line_trims_a_folder(tmp_path, capsys):
    _fixture_log(tmp_path / "codec.out")
    assert lt.main(["--dir", str(tmp_path)]) == 0
    assert "1 log file(s) trimmed" in capsys.readouterr().out
    assert _all_logs_at_most_10_mb(tmp_path)


@pytest.mark.parametrize("supervisor, trimmed", [("launchd", True), ("pm2", False)])
def test_the_heartbeat_trims_on_launchd_only(monkeypatch, tmp_path, supervisor, trimmed):
    log = tmp_path / "codec-dashboard.err"
    _fixture_log(log)
    monkeypatch.setattr(codec_supervisor, "kind", lambda *a, **k: supervisor)
    monkeypatch.setattr(codec_supervisor, "LOG_DIR", str(tmp_path))
    done = codec_heartbeat.trim_service_logs()
    assert bool(done) is trimmed
    assert (log.stat().st_size <= 10_000_000) is trimmed
    assert codec_heartbeat.trim_service_logs(str(tmp_path / "nothing")) == []
