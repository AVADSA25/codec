"""CODEC Skill: File Operations — read, write, append, list files safely."""
SKILL_NAME = "file_ops"
SKILL_DESCRIPTION = "Read, write, append, or list files and directories"
SKILL_MCP_EXPOSE = True
SKILL_TRIGGERS = [
    "read file", "write file", "create file", "append to file",
    "list files", "list directory", "show file", "cat file",
    "save to file", "write to", "read from", "open file",
    "file contents", "show me the file",
]

import os
import re

# ── Safety: restricted paths (D-20 closure, PR-2H) ──
# Mirrors PR-1C's file_write blocking. The pre-PR-2H blocklist used
# `path.startswith(bp)` on a realpath but OMITTED the ~/.codec/ tree, so a
# write to ~/.codec/skills/x.py succeeded → D-1 RCE on restart. PR-2H blocks
# the WHOLE ~/.codec/ tree + the repo's built-in skills/ dir, realpath-resolved
# at module load so symlink-into-blocked-root traversal can't slip through.
#
# `/private` is deliberately handled via realpath: macOS realpaths /tmp →
# /private/tmp, so we must NOT blanket-block /private (that would break the
# legitimate /tmp scratch space). Instead /tmp and $HOME are realpath-resolved
# and treated as the allowable scope sanity-check.
_BLOCKED_SYSTEM_ROOTS = [
    "/System", "/Library", "/usr", "/bin", "/sbin", "/etc",
    "/var", "/dev", "/Volumes",
]
_BLOCKED_NAMES = [
    ".ssh", ".gnupg", ".env", "credentials", "secrets",
    ".aws", ".gcloud", ".kube", "id_rsa", "id_ed25519",
    ".netrc", ".npmrc", ".pypirc", "keychain",
]
_MAX_READ = 10000    # chars
_MAX_WRITE = 50000   # chars


def _build_blocked_roots():
    """Realpath-resolved blocklist built once at module load. Covers the
    macOS system tree + the entire ~/.codec/ state dir + the repo skills/
    dir (hash-pinned in PR-1A; defense in depth here)."""
    roots = []
    for p in _BLOCKED_SYSTEM_ROOTS:
        try:
            roots.append(os.path.realpath(p))
        except Exception:
            roots.append(p)
    # The whole ~/.codec/ tree — skills, plugins, oauth_state.json, config.json,
    # audit.log, memory.db, plugins.allowlist, agents/, agent_global_grants.json.
    try:
        roots.append(os.path.realpath(os.path.expanduser("~/.codec")))
    except Exception:
        pass
    # The repo's built-in skills/ dir (this file lives in it).
    try:
        roots.append(os.path.realpath(os.path.dirname(os.path.abspath(__file__))))
    except Exception:
        pass
    return roots


_BLOCKED_ROOTS_REAL = _build_blocked_roots()
# Public alias for back-compat / introspection.
_BLOCKED_PATHS = _BLOCKED_ROOTS_REAL


def _emit_blocked(target_path, requested_path, reason):
    """Audit emit for D-20 refusals — forensic visibility for any MCP-client
    attempt at a sensitive path. Fire-and-forget."""
    try:
        from codec_audit import log_event
        log_event(
            "file_ops_blocked",
            source="codec-skill-file-ops",
            message=f"file_ops refused: {reason}",
            level="warning",
            outcome="error",
            extra={"target_path": str(target_path)[:300],
                   "requested_path": str(requested_path)[:300],
                   "reason": reason},
        )
    except Exception:
        pass


def _is_safe_path(path):
    """Reject system paths, the ~/.codec/ tree, repo skills/, and sensitive
    credential filenames. Realpath-resolves the candidate (and its parent,
    for not-yet-existing files) so symlink redirection is caught.
    Emits `file_ops_blocked` on refusal."""
    requested = path
    expanded = os.path.expanduser(path)
    # The file may not exist yet (write/append) — realpath the parent and
    # re-join the basename so we still resolve symlinked parents.
    parent = os.path.dirname(expanded) or "."
    try:
        real_parent = os.path.realpath(parent)
    except Exception:
        real_parent = parent
    real = os.path.join(real_parent, os.path.basename(expanded))

    for bp in _BLOCKED_ROOTS_REAL:
        if real == bp or real.startswith(bp + os.sep):
            reason = f"system/CODEC path ({bp})"
            _emit_blocked(real, requested, reason)
            return False, f"Blocked: {reason}"
    base = os.path.basename(real)
    for bn in _BLOCKED_NAMES:
        if bn in base.lower():
            reason = f"sensitive file ({bn})"
            _emit_blocked(real, requested, reason)
            return False, f"Blocked: {reason}"
    return True, ""


def _parse_action(task):
    """Parse the user's intent: read, write, append, list."""
    t = task.lower().strip()
    # Detect action
    if any(w in t for w in [
        "list files", "list dir", "ls ", "list folder", "show folder",
        # Extended list triggers so agents can use natural language:
        "find all", "find files", "locate files", "enumerate files",
        "get all files", "list all", "show all files", "all files in",
        "all .md", "all md files",
    ]):
        return "list", t
    if any(w in t for w in ["write file", "create file", "save to file", "write to"]):
        return "write", t
    if any(w in t for w in ["append to file", "append to", "add to file"]):
        return "append", t
    # Default: read
    return "read", t


def _extract_path(text):
    """Pull a file path from the text."""
    # Try quoted path first
    m = re.search(r'["\']([^"\']+)["\']', text)
    if m:
        return os.path.expanduser(m.group(1))
    # Try ~/... or /... or ./...
    m = re.search(r'(~?/[\w./_-]+)', text)
    if m:
        return os.path.expanduser(m.group(1))
    return None


def _extract_content(text):
    """Extract content to write — everything after 'content:' or 'with:' or between triple-backticks."""
    # Triple backtick block
    m = re.search(r'```(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # After "content:" or "with:" or "text:"
    for keyword in ["content:", "with:", "text:", "body:", "data:"]:
        idx = text.lower().find(keyword)
        if idx >= 0:
            return text[idx + len(keyword):].strip()
    return None


def run(task, app="", ctx=""):
    action, text = _parse_action(task)
    path = _extract_path(task)

    if action == "list":
        dirpath = path or os.path.expanduser("~")
        safe, reason = _is_safe_path(dirpath)
        if not safe:
            return reason
        if not os.path.isdir(dirpath):
            return f"Not a directory: {dirpath}"
        try:
            entries = sorted(os.listdir(dirpath))[:50]
            dirs = [e + "/" for e in entries if os.path.isdir(os.path.join(dirpath, e))]
            files = [e for e in entries if os.path.isfile(os.path.join(dirpath, e))]
            result = f"Directory: {dirpath}\n"
            if dirs:
                result += f"Subdirectories ({len(dirs)}): " + ", ".join(dirs[:20]) + "\n"
            if files:
                # Include full absolute paths so callers can read each file directly
                full_paths = [os.path.join(dirpath, f) for f in files[:30]]
                result += f"Files ({len(files)}):\n" + "\n".join(full_paths)
            return result.strip() or "Empty directory"
        except PermissionError:
            return f"Permission denied: {dirpath}"

    if not path:
        return "Please specify a file path (e.g., ~/Documents/notes.txt)"

    safe, reason = _is_safe_path(path)
    if not safe:
        return reason

    if action == "read":
        if not os.path.exists(path):
            return f"File not found: {path}"
        if not os.path.isfile(path):
            return f"Not a file: {path}"
        try:
            size = os.path.getsize(path)
            if size > 1_000_000:
                return f"File too large ({size:,} bytes). Max 1 MB for safety."
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(_MAX_READ)
            truncated = " (truncated)" if len(content) >= _MAX_READ else ""
            return f"File: {path} ({size:,} bytes){truncated}\n\n{content}"
        except Exception as e:
            return f"Error reading {path}: {e}"

    if action in ("write", "append"):
        content = _extract_content(task)
        if not content:
            return "No content provided. Use: write file '/path' content: your text here"
        # Convert literal \n escape sequences to real newlines.
        # LLMs sometimes write the two-character sequence backslash-n instead of
        # an actual newline character. Normalise both forms.
        if '\\n' in content:
            content = content.replace('\\n', '\n')
        if len(content) > _MAX_WRITE:
            return f"Content too large ({len(content):,} chars). Max {_MAX_WRITE:,}."
        # Ensure parent directory exists
        parent = os.path.dirname(path)
        if parent and not os.path.exists(parent):
            try:
                os.makedirs(parent, exist_ok=True)
            except Exception as e:
                return f"Cannot create directory {parent}: {e}"
        try:
            mode = "a" if action == "append" else "w"
            with open(path, mode, encoding="utf-8") as f:
                f.write(content)
            verb = "Appended to" if action == "append" else "Written to"
            return f"{verb} {path} ({len(content)} chars)"
        except Exception as e:
            return f"Error writing {path}: {e}"

    return "Unknown file operation. Try: read file, write file, list files"
