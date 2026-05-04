"""CODEC Skill: File Write — save content to a file anywhere on the Mac.

Purpose-built for remote callers (claude.ai over HTTP MCP). Writes only —
no read, no delete, no list. Every write is logged to ~/.codec/file_write.log
so you can audit what the remote Claude has been saving.

Usage patterns the skill understands (pass in the `task` string):

    save this to ~/Documents/notes/plan.md
    ```
    # Plan
    - step 1
    - step 2
    ```

    write file ~/Desktop/scratch.txt content: hello world

    path: ~/Projects/foo/bar.md
    mode: append
    content:
    ---
    new entry
    ---

The skill accepts `mode: write` (default, overwrites) or `mode: append`.
"""
SKILL_NAME = "file_write"
SKILL_DESCRIPTION = (
    "Save text content to a file anywhere on the Mac (creates parent dirs). "
    "Input: a task string that includes the destination path and the content "
    "to write. Example: \"save to ~/Documents/plan.md\\n```\\n# Plan\\n- ...\\n```\". "
    "Supports mode: write (default, overwrites) or mode: append. "
    "Blocks system paths (/System, /etc, /usr, /Library) and credential files "
    "(.ssh, .env, id_rsa, keychain, etc.). Every write is audited."
)
SKILL_MCP_EXPOSE = True
SKILL_TRIGGERS = [
    "save to file", "save this to", "write to file", "write file",
    "create file", "save file", "store in file", "export to",
    "dump to", "put in file", "append to file",
]

import os
import re
import json
import time
from datetime import datetime

# ── Configurable limits ──
_MAX_WRITE_BYTES = 500_000   # 500 KB per call — plenty for notes, code, docs
_AUDIT_LOG = os.path.expanduser("~/.codec/file_write.log")

# ── Path safety ──
# These directory roots are ALWAYS blocked, regardless of who's calling.
_BLOCKED_ROOTS = [
    "/System", "/Library", "/usr", "/bin", "/sbin", "/etc",
    "/var", "/private", "/dev", "/Volumes",
]
# Any filename (case-insensitive substring) in this list is blocked.
_BLOCKED_FILENAME_PATTERNS = [
    ".ssh", ".gnupg", ".env", "credentials", "secrets", "secret",
    ".aws", ".gcloud", ".kube", "id_rsa", "id_ed25519", "id_dsa",
    ".netrc", ".npmrc", ".pypirc", "keychain", "password", "token",
    "api_key", "apikey", "private_key",
]
# Block extensions that could be executable shells / trust-sensitive.
_BLOCKED_EXTS = [".pem", ".key", ".p12", ".pfx", ".keystore"]


def _is_safe_target(path: str):
    """Return (True, "") if safe to write; (False, reason) otherwise.

    Resolves symlinks via realpath so a symlink into /etc can't slip through.
    """
    if not path:
        return False, "Empty path."
    expanded = os.path.expanduser(path)
    # If parent exists, realpath the parent and append basename — the file
    # itself may not exist yet, so we can't realpath(path) directly.
    parent = os.path.dirname(expanded) or "."
    try:
        real_parent = os.path.realpath(parent)
    except Exception:
        real_parent = parent
    real_path = os.path.join(real_parent, os.path.basename(expanded))

    for blocked in _BLOCKED_ROOTS:
        if real_path == blocked or real_path.startswith(blocked + os.sep):
            return False, f"Blocked system path: {blocked}"

    base_lower = os.path.basename(real_path).lower()
    for pat in _BLOCKED_FILENAME_PATTERNS:
        if pat in base_lower:
            return False, f"Blocked filename pattern: {pat!r}"

    for ext in _BLOCKED_EXTS:
        if base_lower.endswith(ext):
            return False, f"Blocked extension: {ext}"

    # Sanity: must be under $HOME or /tmp (broad but not everything).
    home = os.path.realpath(os.path.expanduser("~"))
    tmp = "/tmp"
    if not (real_path.startswith(home + os.sep) or real_path.startswith(tmp + os.sep)):
        return False, (
            f"Target must live under $HOME or /tmp (got: {real_path}). "
            "Adjust file_write._BLOCKED_ROOTS if you need wider scope."
        )

    return True, ""


# ── Parsing ──

_PATH_HINTS = [
    r'(?:^|\s)(?:path|file|to|into|at|destination|dest)\s*[:=]\s*["\']?([^"\'\n]+?)["\']?(?:\s|$)',
    r'save\s+(?:this\s+|that\s+|it\s+)?(?:to\s+|into\s+|at\s+)["\']?([^"\'\n]+?)["\']?(?:\s|$)',
    r'write\s+(?:this\s+|to\s+|into\s+)?["\']?(~?[/\w][\w./\s_-]*?\.[\w]{1,8})["\']?',
    r'(["\'])(~?/[^"\'\n]+)\1',
    r'(~?/[\w./_-]+\.[\w]{1,8})',
]

_MODE_RE = re.compile(r'(?:^|\s)mode\s*[:=]\s*(write|append|overwrite)\b', re.I)


def _extract_path(task: str):
    """Best-effort path extraction from a natural-language instruction."""
    for pat in _PATH_HINTS:
        m = re.search(pat, task, re.IGNORECASE | re.MULTILINE)
        if m:
            # Last group is always the captured path
            groups = [g for g in m.groups() if g]
            if groups:
                candidate = groups[-1].strip().rstrip(".,;:")
                if candidate and ("/" in candidate or candidate.startswith("~")):
                    return os.path.expanduser(candidate)
    return None


def _extract_content(task: str):
    """Pull the content out. Preference order:
    1) Triple-backtick fenced block (optionally with language tag)
    2) After an explicit 'content:' / 'body:' / 'data:' / 'text:' marker
    3) After a markdown '---' separator
    """
    # 1) ```[lang]\n ... \n```
    fence = re.search(r'```[\w+-]*\s*\n?(.*?)\n?```', task, re.DOTALL)
    if fence:
        return fence.group(1).rstrip("\n")

    # 2) explicit marker — take everything after it (trailing newline trimmed)
    for kw in ("content:", "body:", "data:", "text:"):
        idx = task.lower().find(kw)
        if idx >= 0:
            after = task[idx + len(kw):].lstrip("\n").rstrip()
            # strip a leading space
            if after.startswith(" "):
                after = after[1:]
            if after:
                return after

    # 3) --- separator (take everything AFTER the last ---)
    if "\n---\n" in task:
        return task.rsplit("\n---\n", 1)[-1].rstrip()

    return None


def _extract_mode(task: str) -> str:
    m = _MODE_RE.search(task)
    if not m:
        return "write"
    val = m.group(1).lower()
    if val in ("write", "overwrite"):
        return "write"
    if val == "append":
        return "append"
    return "write"


# ── Audit log ──

def _audit_write(path: str, size: int, mode: str, transport: str):
    """Append one JSON line per successful write."""
    try:
        os.makedirs(os.path.dirname(_AUDIT_LOG), exist_ok=True)
        entry = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "path": path,
            "size": size,
            "mode": mode,
            "transport": transport,
        }
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        # Never fail a write because of audit-log problems.
        pass


# ── Entry point ──

def run(task: str, context: str = "") -> str:
    if not isinstance(task, str) or not task.strip():
        return "file_write: empty task. Example: \"save to ~/notes.txt content: hello\""

    path = _extract_path(task)
    if not path:
        return (
            "file_write: couldn't find a destination path in the task. "
            "Try: 'save to ~/Documents/foo.md\\n```\\n<your text>\\n```' "
            "or 'path: ~/Desktop/x.txt\\ncontent: hi'"
        )

    content = _extract_content(task)
    if content is None:
        return (
            f"file_write: resolved path '{path}' but found no content. "
            "Put the content in a triple-backtick block, or after 'content:'."
        )

    size = len(content.encode("utf-8"))
    if size > _MAX_WRITE_BYTES:
        return (
            f"file_write: content too large ({size:,} bytes > "
            f"{_MAX_WRITE_BYTES:,} cap). Split into smaller chunks or raise "
            "_MAX_WRITE_BYTES in skills/file_write.py."
        )

    safe, reason = _is_safe_target(path)
    if not safe:
        return f"file_write: refused — {reason}"

    mode_label = _extract_mode(task)
    fmode = "a" if mode_label == "append" else "w"

    # Ensure parent dir exists.
    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except Exception as e:
            return f"file_write: cannot create directory {parent}: {e}"

    try:
        with open(path, fmode, encoding="utf-8") as f:
            f.write(content)
    except PermissionError as e:
        return f"file_write: permission denied for {path}: {e}"
    except OSError as e:
        return f"file_write: OS error writing {path}: {e}"
    except Exception as e:
        return f"file_write: unexpected error writing {path}: {type(e).__name__}: {e}"

    transport = os.environ.get("CODEC_MCP_TRANSPORT", "stdio")
    _audit_write(path, size, mode_label, transport)

    verb = "Appended to" if mode_label == "append" else "Saved"
    return f"{verb} {path} ({size:,} bytes)."
