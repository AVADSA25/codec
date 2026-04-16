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

import os, re

# ── Safety: restricted paths ──
_BLOCKED_PATHS = [
    "/System", "/Library", "/usr", "/bin", "/sbin", "/etc",
    "/var", "/private", "/dev", "/Volumes",
]
_BLOCKED_NAMES = [
    ".ssh", ".gnupg", ".env", "credentials", "secrets",
    ".aws", ".gcloud", ".kube", "id_rsa", "id_ed25519",
    ".netrc", ".npmrc", ".pypirc", "keychain",
]
_MAX_READ = 10000    # chars
_MAX_WRITE = 50000   # chars


def _is_safe_path(path):
    """Reject system paths, hidden credential files, etc."""
    path = os.path.expanduser(path)
    path = os.path.realpath(path)
    for bp in _BLOCKED_PATHS:
        if path.startswith(bp):
            return False, f"Blocked: system path ({bp})"
    base = os.path.basename(path)
    for bn in _BLOCKED_NAMES:
        if bn in base.lower():
            return False, f"Blocked: sensitive file ({bn})"
    return True, ""


def _parse_action(task):
    """Parse the user's intent: read, write, append, list."""
    t = task.lower().strip()
    # Detect action
    if any(w in t for w in ["list files", "list dir", "ls ", "list folder", "show folder"]):
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
                result += f"Folders ({len(dirs)}): " + ", ".join(dirs[:20]) + "\n"
            if files:
                result += f"Files ({len(files)}): " + ", ".join(files[:30])
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
