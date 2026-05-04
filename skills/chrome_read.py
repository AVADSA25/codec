"""Chrome Read — extract and read current page content via AppleScript"""
import subprocess

SKILL_NAME = "chrome_read"
SKILL_DESCRIPTION = "Read and extract text content from the current Chrome tab"
SKILL_MCP_EXPOSE = True
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

        # Extract page text via JavaScript — use single-line JS to avoid AppleScript escaping issues
        js_code = "(function(){var b=document.body;if(!b)return 'No content found';var c=b.cloneNode(true);var r=c.querySelectorAll('script,style,nav,footer,header,.nav,.footer,.header,.sidebar,.menu,.ad,.ads,.advertisement');for(var i=0;i<r.length;i++)r[i].remove();var t=c.innerText||c.textContent||'';return t.substring(0,8000);})()"
        js_script = f'''tell application "Google Chrome"
    set pageText to execute active tab of front window javascript "{js_code}"
    return pageText
end tell'''
        r = subprocess.run(["osascript", "-e", js_script], capture_output=True, text=True, timeout=15)

        if r.returncode == 0 and r.stdout.strip():
            content = r.stdout.strip()[:5000]
            header = f"\U0001f4c4 **{title}**\n{url}\n\n"
            return header + content
        else:
            return f"Could not extract page content. Error: {r.stderr.strip() if r.stderr else 'empty page'}"
    except Exception as e:
        return f"Chrome read error: {e}"
