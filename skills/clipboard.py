"""CODEC Skill: Clipboard History

Transport-aware return:
- MCP / HTTP transport (e.g., claude.ai calling): returns the clipboard
  content inline as a string so the calling LLM can USE it as context.
  This is what the user wants — clipboard accessible to claude.ai
  without an approval prompt.
- Local stdio / voice / dashboard direct: keeps the existing behavior
  of writing to a temp file + opening in TextEdit, so the human user
  sees a real window with the content.

Detection: CODEC_MCP_TRANSPORT env var is set to "http" by
codec_mcp_http.py at startup. Absent or "stdio" → local path.
"""
SKILL_NAME = "clipboard"
SKILL_DESCRIPTION = "Track and show clipboard history"
SKILL_MCP_EXPOSE = True
SKILL_TRIGGERS = ["clipboard history", "show clipboard", "what did i copy", "last copied",
                   "my clipboard", "paste history", "copied items"]
import subprocess
import sqlite3
import os
from datetime import datetime

DB = os.path.expanduser("~/.codec/clipboard.db")


def _init():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS clips (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, content TEXT)")
    c.commit(); c.close()


def _is_remote_transport() -> bool:
    """True when the skill is being invoked from a remote MCP client
    (claude.ai / external MCP). The HTTP transport sets this env var on
    startup. Local CLI / voice / dashboard sessions don't."""
    return os.environ.get("CODEC_MCP_TRANSPORT", "stdio").lower() == "http"


def run(task, app="", ctx=""):
    _init()
    # Get current clipboard and save it
    r = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=3)
    current = r.stdout.strip()
    if current:
        c = sqlite3.connect(DB)
        last = c.execute("SELECT content FROM clips ORDER BY id DESC LIMIT 1").fetchone()
        if not last or last[0] != current:
            c.execute("INSERT INTO clips (timestamp, content) VALUES (?,?)",
                (datetime.now().isoformat(), current[:500]))
            c.commit()
        c.close()

    # Show history
    c = sqlite3.connect(DB)
    rows = c.execute("SELECT timestamp, content FROM clips ORDER BY id DESC LIMIT 10").fetchall()
    c.close()
    if not rows:
        return "Clipboard history is empty."

    lines = ["CODEC CLIPBOARD HISTORY", "=" * 40, ""]
    for ts, content in rows:
        t = ts[:16].replace("T", " ")
        preview = content[:200].replace("\n", " ")
        lines.append(f"[{t}]  {preview}")
        lines.append("")
    body = "\n".join(lines)

    if _is_remote_transport():
        # claude.ai / remote MCP: return the actual content so the LLM
        # has the clipboard as context. No TextEdit popup.
        return body

    # Local path: write a temp file + open in TextEdit (visible to the
    # human user), and ALSO return the content as a string so dashboard
    # / voice TTS can speak / display it as a fallback.
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", prefix="codec_clipboard_")
    tmp.write(body); tmp.close()
    subprocess.Popen(["open", tmp.name])
    return body
