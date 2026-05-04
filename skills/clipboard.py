"""CODEC Skill: Clipboard History"""
SKILL_NAME = "clipboard"
SKILL_DESCRIPTION = "Track and show clipboard history"
SKILL_MCP_EXPOSE = True
SKILL_TRIGGERS = ["clipboard history", "show clipboard", "what did i copy", "last copied",
                   "my clipboard", "paste history", "copied items"]
import subprocess, sqlite3, os
from datetime import datetime

DB = os.path.expanduser("~/.codec/clipboard.db")

def _init():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS clips (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, content TEXT)")
    c.commit(); c.close()

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

    import tempfile
    lines = ["CODEC CLIPBOARD HISTORY", "=" * 40, ""]
    for ts, content in rows:
        t = ts[:16].replace("T", " ")
        preview = content[:100].replace("\n", " ")
        lines.append(f"[{t}]  {preview}")
        lines.append("")

    tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", prefix="codec_clipboard_")
    tmp.write("\n".join(lines)); tmp.close()
    subprocess.Popen(["open", tmp.name])
    return f"Opening {len(rows)} clipboard items."
