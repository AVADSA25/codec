"""Vibe: which existing folders the owner may attach as a project (P-050, docs/VIBE-ATTACH-FOLDER-DESIGN.md).

Owner decisions R-10 and R-11 (12 Nov 2026). An attached folder:

  - is the owner's own act: a path he types at this Mac (routes/vibe_projects.py answers 403 to anything else);
  - is a real folder, not a link, reached through no link, and owned by the owner;
  - sits at least one level inside Documents, Desktop, Downloads, Projects or codec-workspace (never /tmp, the home
    folder, Library, ~/.codec, a root itself);
  - neither is inside nor holds the CODEC code folder, the Vibe folder or its trash, ~/.codec, the Obsidian _system
    folder (codec_write_policy.REFUSED_INSIDE) or another attached folder;
  - holds at most MAX_FILES tracked files, MAX_BYTES of tracked content and MAX_DEPTH levels (checked by
    codec_vibe_projects, which walks the folder; this module only names the limits).

The same folder rules are checked again every time the project is opened, so a folder later swapped for a link or
moved somewhere refused stops opening. This module reads no file: only names and stat.
"""
from __future__ import annotations

import os
import stat
import unicodedata

MAX_FILES = 2000                    # tracked files (R-11)
MAX_BYTES = 100 * 1024 * 1024       # tracked content
MAX_DEPTH = 16                      # levels of a tracked file's path
MAX_PATH_LEN = 400                  # characters of a path inside the folder (a created project keeps 200)
MAX_TYPED = 1000                    # characters of the typed folder path

ROOTS = ("~/Documents", "~/Desktop", "~/Downloads", "~/Projects", "~/codec-workspace")
ROOTS_TEXT = "Documents, Desktop, Downloads, Projects or codec-workspace"


class Refused(Exception):
    """The folder cannot be attached. str() is the reason, for the owner."""


def enabled() -> bool:
    return os.environ.get("VIBE_ATTACH_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def _real(p: str) -> str:
    return os.path.realpath(os.path.expanduser(p))


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _near(path: str, root: str) -> bool:
    """_under with letter case and Unicode form ignored: for the places a folder is refused near, so a path typed
    in other capitals (the disk does not mind) cannot slip past them."""
    def fold(p):
        return unicodedata.normalize("NFC", p).casefold()
    return _under(fold(path), fold(root))


def _on_disk(real: str) -> str:
    """The same path with every name spelled as the disk stores it (capitals, accents). Names only: no file is
    opened. Refused when a name cannot be found or is spelled two ways."""
    out = os.sep
    for name in [n for n in real.split(os.sep) if n]:
        try:
            names = os.listdir(out)
        except OSError:
            raise Refused("That folder cannot be read.") from None
        if name not in names:
            want = unicodedata.normalize("NFC", name).casefold()
            hits = [n for n in names if unicodedata.normalize("NFC", n).casefold() == want]
            if len(hits) != 1:
                raise Refused("There is no folder at that path.")
            name = hits[0]
        out = os.path.join(out, name)
    return out


def roots() -> list[str]:
    """The real paths of the folders an attached folder must sit inside. CODEC_VIBE_ATTACH_ROOTS (os.pathsep between
    paths) replaces them, for tests."""
    raw = os.environ.get("CODEC_VIBE_ATTACH_ROOTS")
    items = [p for p in raw.split(os.pathsep) if p.strip()] if raw else list(ROOTS)
    return [_real(p) for p in items]


def _code_folder() -> str:
    return os.path.dirname(os.path.realpath(__file__))


def protected() -> list[tuple[str, str]]:
    """(real path, what it is) of the places an attached folder may neither be inside nor hold."""
    import codec_vibe_projects as vp
    import codec_write_policy as wp
    out = [(_real("~/Library"), "your Library folder"), (_real("~/.codec"), "CODEC's own state folder"),
           (_code_folder(), "the folder CODEC runs from"), (os.path.realpath(str(vp.vibe_root())), "the Vibe folder"),
           (os.path.realpath(str(vp.trash_root())), "the Vibe trash")]
    out += [(_real(p), "the Obsidian _system folder (this Mac runs scripts from it)") for p in wp.REFUSED_INSIDE]
    return out


def check_folder(typed, *, others=()) -> str:
    """The real path of the folder the owner typed, or Refused with the reason. `others` are the real paths of the
    folders already attached (the folder itself is not refused for being one of them)."""
    if not isinstance(typed, str) or not typed.strip():
        raise Refused("Type the folder's path, for example ~/Projects/my-app.")
    typed = typed.strip()
    if len(typed) > MAX_TYPED or any(ord(c) < 32 or c in '"\\' for c in typed):
        raise Refused("That path has characters Vibe cannot use (quotes, backslashes or control characters).")
    path = os.path.expanduser(typed)
    if not os.path.isabs(path):
        raise Refused("Type the full path, starting with / or ~/.")
    path = os.path.normpath(path)
    if os.path.islink(path):
        raise Refused("That path is a link. Type the real folder's path.")
    real = os.path.realpath(path)
    if real != path:
        raise Refused(f"That path goes through a link. Type the real path ({real}) if that is the folder you mean.")
    try:
        st = os.lstat(real)
    except FileNotFoundError:
        raise Refused("There is no folder at that path.") from None
    except OSError:
        raise Refused("That folder cannot be read.") from None
    if not stat.S_ISDIR(st.st_mode):
        raise Refused("That path is not a folder.")
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise Refused("That folder belongs to another user.")
    real = _on_disk(real)       # the sandbox and every check below compare exact text
    home = _real("~")
    if _near(home, real):
        raise Refused("That is your home folder, or holds it. Attach a project folder inside " + ROOTS_TEXT + ".")
    rs = roots()
    if any(_near(real, r) and _near(r, real) for r in rs):
        raise Refused("That is a whole top folder. Attach a project folder inside it.")
    if not any(_under(real, r) and real != r for r in rs):
        raise Refused("Vibe attaches only folders inside " + ROOTS_TEXT + ".")
    for p, what in protected():
        if _near(real, p):
            raise Refused(f"That folder is inside {what}.")
        if _near(p, real):
            raise Refused(f"That folder holds {what}. Attach a folder further down.")
    for o in others:
        if o == real:
            continue
        if _near(real, o):
            raise Refused("That folder is inside a folder that is already attached.")
        if _near(o, real):
            raise Refused("That folder holds a folder that is already attached.")
    return real


def recheck(real: str) -> str:
    """check_folder again for a stored real path: it must still be exactly that folder (no link swapped in, not
    moved somewhere refused). The overlap with other attached folders was checked when it was attached."""
    if not isinstance(real, str) or not os.path.isabs(real):
        raise Refused("The record does not say which folder it is.")
    if not os.path.lexists(real):
        raise Refused(f"The attached folder is missing: {real}. It was moved, renamed or deleted.")
    return check_folder(real)
