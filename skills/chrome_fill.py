"""Fill web forms via Chrome DevTools Protocol"""
import sys, os
sys.path.insert(0, os.path.expanduser("~/codec-repo"))

SKILL_NAME = "chrome_fill"
SKILL_TRIGGERS = ["fill in", "fill form", "fill the field", "type into field", "enter in field", "put in field"]
SKILL_DESCRIPTION = "Fill web form fields using Chrome DevTools Protocol for precise form automation"
SKILL_MCP_EXPOSE = True

def run(task: str, context: str = "") -> str:
    from codec_cdp import is_cdp_available, run_cdp, ChromeCDP
    import re

    if not is_cdp_available():
        return "Chrome CDP not available. Launch Chrome with: open -a 'Google Chrome' --args --remote-debugging-port=9222"

    # Parse: "fill in [selector] with [value]" or "fill the [field] field with [value]"
    task_lower = task.lower()
    value = ""
    selector = "input"

    for pat in [r"fill.*?(?:in|the)?\s+(.+?)\s+(?:with|:)\s+(.+)", r"type\s+(.+?)\s+into\s+(.+)"]:
        m = re.search(pat, task_lower)
        if m:
            if "into" in pat:
                value, selector = m.group(1), m.group(2)
            else:
                selector, value = m.group(1), m.group(2)
            break

    if not value:
        return "Please specify: 'fill in [field] with [value]'"

    async def _fill():
        cdp = ChromeCDP()
        await cdp.connect()
        try:
            title = await cdp.get_title()
            # Try common selectors if selector is a description
            selectors = [f"#{selector}", f"[name='{selector}']", f"[placeholder*='{selector}']",
                         f"input[type='text']", "input:not([type='hidden'])", "textarea"]
            for sel in selectors:
                els = await cdp.get_elements(sel)
                if els:
                    await cdp.fill_field(sel, value)
                    return f"Filled '{selector}' with '{value}' on {title}"
            return f"Could not find field '{selector}' on page"
        finally:
            await cdp.close()

    return run_cdp(_fill())
