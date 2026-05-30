"""Find files on your Mac by name or content"""
SKILL_NAME = "file_search"
SKILL_TRIGGERS = [
    "find file", "search file", "locate file", "where is file",
    "find document", "search for file", "find files named",
    "search for files",
    # B4 / SR-26: added local-filesystem variants that previously routed
    # to google_drive's "my files" trigger.
    "my files", "list my files", "show my files",
    "my documents", "list my documents",
    "recent files", "recent documents",
]
SKILL_DESCRIPTION = "Search for files by name or content"
SKILL_MCP_EXPOSE = True

import os
import subprocess
import re


def _is_remote_transport() -> bool:
    """True when invoked from remote MCP (claude.ai); False for local
    dashboard/voice. Local users get the Terminal popup + voice-sized
    return; remote callers get the FULL list inline so the LLM has the
    actual file paths as context."""
    return os.environ.get("CODEC_MCP_TRANSPORT", "stdio").lower() == "http"


def _extract_query(task: str) -> str:
    """Strip command phrases and filler, preserving the actual filename/pattern."""
    q = task.strip()
    # Strip leading command phrases with word boundaries (longer first)
    PREFIXES = [
        "search for files named", "search for files called",
        "find files named", "find files called",
        "find a file named", "find a file called",
        "search for file", "search for files",
        "search file", "find file", "locate file",
        "where is the file", "where is file",
        "find document", "find documents",
        "search for", "find", "search", "locate",
    ]
    low = q.lower()
    for p in PREFIXES:
        # word-boundary match at start only
        if re.match(r'^\s*' + re.escape(p) + r'\b', low):
            q = q[len(p):].strip()
            low = q.lower()
            break
    # Strip quotes and trailing punctuation
    q = q.strip('"\'').rstrip("?.,!").strip()
    # Strip trailing "please"/"on my mac"/etc
    for tail in ["on my mac", "on the mac", "please", "for me"]:
        if q.lower().endswith(tail):
            q = q[: -len(tail)].strip()
    return q


def run(task, app="", ctx=""):
    query = _extract_query(task)
    if not query or len(query) < 2:
        return "What file should I search for? (e.g. 'find file CLAUDE.md')"
    try:
        r = subprocess.run(["mdfind", "-name", query],
                           capture_output=True, text=True, timeout=10)
        files = [f for f in r.stdout.strip().split("\n") if f][:12]
        if not files:
            return f"No files found matching '{query}'."
        header = f"Found {len(files)} files matching '{query}':"
        body = "\n".join(files)
        result = f"{header}\n{body}"
        if _is_remote_transport():
            # claude.ai / MCP: return ALL file paths inline as context.
            # No Terminal popup (the human user isn't here).
            return result
        # Local path: open in Terminal for copy-paste, return short summary.
        safe = result.replace("'", "'\\''")
        subprocess.Popen([
            "osascript", "-e",
            f"""tell application "Terminal"
                activate
                do script "echo ''; echo '\\033[38;2;232;113;26m━━━ CODEC FILE SEARCH ━━━\\033[0m'; echo ''; echo '{safe}'; echo ''"
            end tell"""
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Found {len(files)} files — opened in Terminal.\n" + "\n".join(files[:5])
    except subprocess.TimeoutExpired:
        return f"Search timed out for '{query}'."
    except Exception as e:
        return f"Search failed: {e}"
