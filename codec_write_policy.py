"""CODEC write policy: the folders an AI-driven file write may touch (P-006).

Owner decision D-17 (29 Oct 2026): "file writes only in Documents, Desktop,
Downloads, Projects, codec-workspace and /tmp". Used by the file_write skill,
the chat "save file" route (routes/upload.py) and the crew file_write tool
(codec_agents.py). Each caller keeps its own blocklist as a second check.

Everything outside ALLOWED_ROOTS is refused: the repo, ~/.claude (hooks),
~/Library/LaunchAgents, ~/.cloudflared, ~/.codec, dotfiles such as ~/.gitconfig.
Inside the allowed roots, files that something on this Mac runs by itself are
refused too: the Obsidian vault's _system folder (launchd runs scripts from
there) and any target ending in .sh, .mjs, .command or .plist.
"""
import os

ALLOWED_ROOTS = [
    "~/Documents", "~/Desktop", "~/Downloads", "~/Projects",
    "~/codec-workspace", "/tmp",
]
REFUSED_INSIDE = ["~/Documents/Claude/Obsidian/_system"]
REFUSED_SUFFIXES = (".sh", ".mjs", ".command", ".plist")

ALLOWED_TEXT = "Documents, Desktop, Downloads, Projects, codec-workspace and /tmp"


def _real(p):
    return os.path.realpath(os.path.expanduser(p))


def _under(path, root):
    return path == root or path.startswith(root + os.sep)


def check_write(path):
    """Return (True, "") when an AI-driven write to `path` is allowed,
    else (False, reason). Symlinks are resolved first."""
    if not path:
        return False, "Empty path."
    real = _real(path)
    if not any(_under(real, _real(r)) for r in ALLOWED_ROOTS):
        return False, f"Writes are allowed only in {ALLOWED_TEXT} (got: {real})."
    for r in REFUSED_INSIDE:
        if _under(real, _real(r)):
            return False, f"Blocked path: {_real(r)} holds scripts this Mac runs."
    if real.lower().endswith(REFUSED_SUFFIXES):
        return False, ("Blocked file type: scripts and launch files "
                       f"({', '.join(REFUSED_SUFFIXES)}) are not written by CODEC.")
    return True, ""
