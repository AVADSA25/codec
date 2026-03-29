"""Extract structured data from web pages via CDP"""
import sys, os
sys.path.insert(0, os.path.expanduser("~/codec-repo"))

SKILL_NAME = "chrome_extract"
SKILL_TRIGGERS = ["extract from page", "get data from page", "scrape page", "get all prices",
                  "get all links", "get all text", "extract links", "get page data"]
SKILL_DESCRIPTION = "Extract tables, prices, links, and structured data from web pages via CDP"

def run(task: str, context: str = "") -> str:
    from codec_cdp import is_cdp_available, run_cdp, ChromeCDP

    if not is_cdp_available():
        return "Chrome CDP not available. Launch Chrome with --remote-debugging-port=9222"

    task_lower = task.lower()

    async def _extract():
        cdp = ChromeCDP()
        await cdp.connect()
        try:
            url = await cdp.get_url()
            title = await cdp.get_title()

            if "link" in task_lower:
                links = await cdp.get_links()
                lines = [f"Links on {title} ({len(links)} total):"]
                for link in links[:20]:
                    lines.append(f"  {link['text'][:60]} → {link['href'][:80]}")
                return "\n".join(lines)

            elif "price" in task_lower:
                prices = await cdp.evaluate("""
                    Array.from(document.querySelectorAll('[class*=price],[class*=Price],[data-price]'))
                        .map(el => el.textContent.trim())
                        .filter(t => t && t.length < 30)
                        .slice(0, 20)
                """) or []
                if prices:
                    return f"Prices on {title}:\n" + "\n".join(f"  {p}" for p in prices)
                return f"No prices found on {title}"

            else:
                text = await cdp.get_page_text(max_chars=3000)
                return f"Text from {title}:\n{text}"
        finally:
            await cdp.close()

    return run_cdp(_extract())
