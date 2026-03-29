"""Scroll web pages via CDP"""
import sys, os
sys.path.insert(0, os.path.expanduser("~/codec-repo"))

SKILL_NAME = "chrome_scroll"
SKILL_TRIGGERS = ["scroll down", "scroll up", "scroll to bottom", "scroll to top",
                  "page down", "page up", "go to bottom of page", "go to top of page"]
SKILL_DESCRIPTION = "Scroll web pages up, down, or to specific positions via CDP"

def run(task: str, context: str = "") -> str:
    from codec_cdp import is_cdp_available, run_cdp, ChromeCDP

    if not is_cdp_available():
        # Fallback: AppleScript scroll
        import subprocess
        direction = "down" if "down" in task.lower() or "bottom" in task.lower() else "up"
        key = "Page Down" if direction == "down" else "Page Up"
        subprocess.run(["osascript", "-e", f'tell application "Google Chrome" to activate'],
                      capture_output=True)
        subprocess.run(["osascript", "-e", f'tell application "System Events" to key code {121 if direction == "down" else 116}'],
                      capture_output=True)
        return f"Scrolled {direction} (AppleScript fallback)"

    task_lower = task.lower()

    async def _scroll():
        cdp = ChromeCDP()
        await cdp.connect()
        try:
            if "bottom" in task_lower or "end" in task_lower:
                await cdp.scroll("bottom")
                return "Scrolled to bottom of page"
            elif "top" in task_lower or "start" in task_lower:
                await cdp.scroll("top")
                return "Scrolled to top of page"
            elif "up" in task_lower:
                await cdp.scroll("up", 500)
                return "Scrolled up"
            else:
                await cdp.scroll("down", 500)
                return "Scrolled down"
        finally:
            await cdp.close()

    return run_cdp(_scroll())
