"""Find files on your Mac by name or content"""
SKILL_NAME = "file_search"
SKILL_TRIGGERS = ["find file", "search file", "locate file", "where is file", "find document", "search for file"]
SKILL_DESCRIPTION = "Search for files by name or content"

import subprocess, os

def run(task, app="", ctx=""):
    query = task.lower()
    for w in ["find file", "search file", "locate file", "where is file", "find document", "search for file", "find", "search"]:
        query = query.replace(w, "").strip()
    if not query:
        return "What file should I search for?"
    try:
        r = subprocess.run(["mdfind", "-name", query], capture_output=True, text=True, timeout=10)
        files = [f for f in r.stdout.strip().split("\n") if f][:12]
        if files:
            result = f"Found {len(files)} files matching '{query}':\\n" + "\\n".join(files)
            # Open in Terminal for easy copy-paste
            safe = result.replace("'", "'\\''")
            subprocess.Popen(["osascript", "-e", f"""tell application "Terminal"
                activate
                do script "echo ''; echo '\\033[38;2;232;113;26m━━━ CODEC FILE SEARCH ━━━\\033[0m'; echo ''; echo '{safe}'; echo ''"
            end tell"""])
            return f"Found {len(files)} files — opened in Terminal"
        return f"No files found matching '{query}'"
    except:
        return "Search failed"
