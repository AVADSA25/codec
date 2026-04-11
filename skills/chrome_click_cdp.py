"""Click specific web elements via CDP"""
import sys, os
sys.path.insert(0, os.path.expanduser("~/codec-repo"))

SKILL_NAME = "chrome_click_cdp"
SKILL_TRIGGERS = ["click button on page", "click link on page", "press button on website", "click on the page", "click on the website"]
SKILL_DESCRIPTION = "Click specific buttons and links on web pages via Chrome DevTools Protocol"

def run(task: str, context: str = "") -> str:
    from codec_cdp import is_cdp_available, run_cdp, ChromeCDP

    if not is_cdp_available():
        return None  # Fall through so other skills (e.g. mouse_control) can handle it

    task_lower = task.lower()
    target = ""
    for prefix in ["click button on page ", "click link on page ", "press button on website ",
                   "click on the ", "click on ", "click the ", "click "]:
        if prefix in task_lower:
            target = task_lower.split(prefix, 1)[1].strip().rstrip(".")
            break

    async def _click():
        cdp = ChromeCDP()
        await cdp.connect()
        try:
            title = await cdp.get_title()
            if target:
                await cdp.click_text(target)
                return f"Clicked '{target}' on {title}"
            else:
                return "Please specify what to click: 'click button Submit'"
        finally:
            await cdp.close()

    return run_cdp(_click())
