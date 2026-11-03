"""CODEC private text files for Terminal windows (S-01).

A skill that shows outside text in a Terminal window (the owner's request, a
model reply, file paths) never puts that text in the AppleScript `do script`
string or in the shell command Terminal types: one quote there ends the
string and the rest runs as the owner. The skill writes the text here, a 0600
file in a fresh 0700 folder, and has Terminal `cat` it, with the file and
folder paths passed through shlex.quote and then codec_applescript.escape.

When the file goes: the Terminal command deletes the file and its folder right
after printing it; the skill deletes them at once if osascript cannot start;
and a folder left behind because Terminal never ran the command is deleted by
the next write_private() call once it is LEFTOVER_SECONDS old.
Design: docs/S01-TERMINAL-TEXT-DESIGN.md.
"""
import os
import re
import stat
import tempfile
import time

PREFIX = "codec_show_"
FILENAME = "text.txt"
LEFTOVER_SECONDS = 600
# None: the system temp folder (the per-user $TMPDIR on macOS). Tests point it
# at a temporary folder.
ROOT = None

# C0 controls except tab and newline, DEL, and the C1 controls: outside text
# must not drive the terminal (colours, window title, cursor moves that hide
# lines).
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _root():
    return ROOT or tempfile.gettempdir()


def clean(text) -> str:
    """The text with every control character but newline and tab made '?'."""
    if not isinstance(text, str):
        text = str(text)
    return _CONTROL.sub("?", text)


def write_private(text, header="", footer="") -> tuple:
    """Write header + clean(text) + footer to a new private file. Returns
    (folder, path). header and footer are the caller's own constant lines,
    written as is. Raises OSError when the file cannot be written (nothing is
    left behind)."""
    sweep()
    folder = tempfile.mkdtemp(prefix=PREFIX, dir=_root())
    path = os.path.join(folder, FILENAME)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(header + clean(text) + footer)
    except BaseException:
        remove(folder)
        raise
    return folder, path


def remove(folder) -> None:
    """Delete a folder made by write_private and its one file. Never raises."""
    try:
        os.unlink(os.path.join(folder, FILENAME))
    except OSError:
        pass
    try:
        os.rmdir(folder)
    except OSError:
        pass


def sweep(now=None) -> int:
    """Delete write_private folders older than LEFTOVER_SECONDS: real folders
    (not links) this user owns, named PREFIX..., holding nothing but FILENAME.
    Returns how many were deleted. Never raises."""
    root = _root()
    now = time.time() if now is None else now
    uid = os.getuid() if hasattr(os, "getuid") else None
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    removed = 0
    for name in names:
        if not name.startswith(PREFIX):
            continue
        folder = os.path.join(root, name)
        try:
            st = os.lstat(folder)
            if not stat.S_ISDIR(st.st_mode) or (uid is not None and st.st_uid != uid):
                continue
            if now - st.st_mtime < LEFTOVER_SECONDS:
                continue
            if set(os.listdir(folder)) - {FILENAME}:
                continue
        except OSError:
            continue
        remove(folder)
        if not os.path.lexists(folder):
            removed += 1
    return removed
