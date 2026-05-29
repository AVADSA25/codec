"""N4/N10 (re-audit, High): file_write/file_ops resolved only the PARENT dir
via realpath, then re-joined the raw basename — so a symlinked FINAL component
(e.g. ~/Documents/notes.md -> ~/.zshrc or ~/.codec/oauth_state.json) slipped
past the blocklist and open() followed it (write-through / read-exfil over MCP).

The safety check must realpath the FULL path so a symlinked basename resolves
to its (blocked) target.
"""
import importlib.util
import os
from pathlib import Path


def _load(modname):
    p = Path(__file__).resolve().parent.parent / "skills" / modname
    spec = importlib.util.spec_from_file_location(f"_under_test_{modname}", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_file_write_is_safe_target_rejects_symlinked_basename(tmp_path, monkeypatch):
    wf = _load("file_write.py")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "s.txt").write_text("ORIGINAL")
    docs = tmp_path / "home" / "d"
    docs.mkdir(parents=True)
    link = docs / "notes.txt"
    link.symlink_to(blocked / "s.txt")  # final component is a symlink to a blocked target

    monkeypatch.setattr(wf, "_HOME_REAL", os.path.realpath(str(tmp_path / "home")))
    monkeypatch.setattr(wf, "_TMP_REAL", os.path.realpath("/tmp"))
    monkeypatch.setattr(wf, "_BLOCKED_ROOTS_REAL", [os.path.realpath(str(blocked))])
    monkeypatch.setattr(wf, "_BLOCKED_ROOTS", wf._BLOCKED_ROOTS_REAL)

    safe, reason = wf._is_safe_target(str(link))
    assert safe is False, f"symlink-to-blocked target must be unsafe (got safe; reason={reason!r})"


def test_file_ops_is_safe_path_rejects_symlinked_basename(tmp_path, monkeypatch):
    fo = _load("file_ops.py")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "oauth_state.json").write_text("SECRET")
    docs = tmp_path / "home" / "d"
    docs.mkdir(parents=True)
    link = docs / "x.json"
    link.symlink_to(blocked / "oauth_state.json")

    monkeypatch.setattr(fo, "_BLOCKED_ROOTS_REAL", [os.path.realpath(str(blocked))])
    monkeypatch.setattr(fo, "_BLOCKED_PATHS", fo._BLOCKED_ROOTS_REAL)

    safe, reason = fo._is_safe_path(str(link))
    assert safe is False, f"symlink-to-blocked target must be unsafe (got safe; reason={reason!r})"


def test_file_write_still_allows_plain_new_file(tmp_path, monkeypatch):
    # Regression: a normal (non-symlink) new file under home stays writable.
    wf = _load("file_write.py")
    home = tmp_path / "home"
    (home / "d").mkdir(parents=True)
    monkeypatch.setattr(wf, "_HOME_REAL", os.path.realpath(str(home)))
    monkeypatch.setattr(wf, "_TMP_REAL", os.path.realpath("/tmp"))
    monkeypatch.setattr(wf, "_BLOCKED_ROOTS_REAL", [os.path.realpath(str(tmp_path / "blocked"))])
    monkeypatch.setattr(wf, "_BLOCKED_ROOTS", wf._BLOCKED_ROOTS_REAL)
    safe, reason = wf._is_safe_target(str(home / "d" / "newfile.txt"))
    assert safe is True, f"plain new file under home must stay writable (reason={reason!r})"
