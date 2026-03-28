"""Chrome Read — extract and read current page content via AppleScript"""
import subprocess

SKILL_NAME = "chrome_read"
SKILL_DESCRIPTION = "Read and extract text content from the current Chrome tab"
SKILL_TRIGGERS = [
    "read this page", "read page", "what's on this page", "whats on this page",
    "read the page", "page content", "extract page", "read website",
    "read this site", "summarize page", "summarize this page", "what does this page say"
]

def run(task, app="", ctx=""):
    try:
        # Get page title and URL
        info_script = '''
tell application "Google Chrome"
    set pageTitle to title of active tab of front window
    set pageURL to URL of active tab of front window
    return pageTitle & "|SPLIT|" & pageURL
end tell
'''
        info = subprocess.run(["osascript", "-e", info_script], capture_output=True, text=True, timeout=5)
        title, url = "", ""
        if info.returncode == 0 and "|SPLIT|" in info.stdout:
            parts = info.stdout.strip().split("|SPLIT|")
            title = parts[0]
            url = parts[1] if len(parts) > 1 else ""

        # Extract page text via JavaScript
        js_script = '''
tell application "Google Chrome"
    set pageText to execute active tab of front window javascript "
        (function() {
            var body = document.body;
            if (!body) return 'No content found';
            var clone = body.cloneNode(true);
            var remove = clone.querySelectorAll('script,style,nav,footer,header,.nav,.footer,.header,.sidebar,.menu,.ad,.ads,.advertisement');
            for (var i = 0; i < remove.length; i++) remove[i].remove();
            var text = clone.innerText || clone.textContent || '';
            text = text.replace(/\\\\n{3,}/g, '\\\\n\\\\n').trim();
            return text.substring(0, 8000);
        })()
    "
    return pageText
end tell
'''
        r = subprocess.run(["osascript", "-e", js_script], capture_output=True, text=True, timeout=15)

        if r.returncode == 0 and r.stdout.strip():
            content = r.stdout.strip()[:5000]
            header = f"\U0001f4c4 **{title}**\n{url}\n\n"
            return header + content
        else:
            return f"Could not extract page content. Error: {r.stderr.strip() if r.stderr else 'empty page'}"
    except Exception as e:
        return f"Chrome read error: {e}"
