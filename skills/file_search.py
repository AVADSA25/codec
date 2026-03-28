"""Find files on your Mac by name or content"""
SKILL_NAME = "file_search"
SKILL_TRIGGERS = ["find file", "search file", "locate", "where is", "find document", "search for file"]
SKILL_DESCRIPTION = "Search for files by name or content"

import subprocess

def run(task, app="", ctx=""):
    query = task.lower()
    for w in ["find file", "search file", "locate", "where is", "find document", "search for file", "find", "search"]:
        query = query.replace(w, "").strip()
    if not query:
        return "What file should I search for?"
    try:
        r = subprocess.run(["mdfind", "-name", query], capture_output=True, text=True, timeout=10)
        files = [f for f in r.stdout.strip().split("\n") if f][:8]
        if files:
            return f"Found {len(files)} files:\n" + "\n".join(f"• {f.split('/')[-1]} — {f}" for f in files)
        return f"No files found matching '{query}'"
    except:
        return "Search failed"
