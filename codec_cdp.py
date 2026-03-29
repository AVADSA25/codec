"""
CODEC Chrome DevTools Protocol — deep browser automation
Connects to Chrome via CDP WebSocket for JS execution, element interaction, screenshots.

Chrome must be launched with: --remote-debugging-port=9222
Or use: open -a "Google Chrome" --args --remote-debugging-port=9222
"""
import json
import asyncio
import httpx
import websockets
from typing import Any, Optional

CDP_URL = "http://localhost:9222"


class ChromeCDP:
    def __init__(self):
        self.ws = None
        self.msg_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._listener_task = None

    async def connect(self, tab_index: int = 0):
        """Connect to Chrome's debug WebSocket (picks tab by index)."""
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{CDP_URL}/json", timeout=5)
            tabs = r.json()
        if not tabs:
            raise ConnectionError("No Chrome tabs. Launch: open -a 'Google Chrome' --args --remote-debugging-port=9222")
        # Pick first non-devtools tab
        page_tabs = [t for t in tabs if t.get("type") == "page"]
        if not page_tabs:
            page_tabs = tabs
        ws_url = page_tabs[min(tab_index, len(page_tabs) - 1)]["webSocketDebuggerUrl"]
        self.ws = await websockets.connect(ws_url, max_size=10 * 1024 * 1024)
        self._listener_task = asyncio.create_task(self._listen())

    async def _listen(self):
        """Background listener — routes responses to waiting futures."""
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                mid = msg.get("id")
                if mid and mid in self._pending:
                    self._pending[mid].set_result(msg)
        except Exception:
            pass

    async def send(self, method: str, params: Optional[dict] = None) -> dict:
        """Send CDP command, await response."""
        if not self.ws:
            raise RuntimeError("Not connected. Call connect() first.")
        self.msg_id += 1
        mid = self.msg_id
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[mid] = future
        msg = {"id": mid, "method": method}
        if params:
            msg["params"] = params
        await self.ws.send(json.dumps(msg))
        try:
            resp = await asyncio.wait_for(future, timeout=30)
        finally:
            self._pending.pop(mid, None)
        if "error" in resp:
            raise RuntimeError(f"CDP error: {resp['error']}")
        return resp.get("result", {})

    async def navigate(self, url: str) -> dict:
        """Navigate to URL and wait for load."""
        result = await self.send("Page.navigate", {"url": url})
        await asyncio.sleep(1.5)  # Give page time to load
        return result

    async def evaluate(self, expression: str, await_promise: bool = False) -> Any:
        """Run JavaScript in the page context."""
        result = await self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
        })
        return result.get("result", {}).get("value")

    async def get_url(self) -> str:
        return await self.evaluate("window.location.href") or ""

    async def get_title(self) -> str:
        return await self.evaluate("document.title") or ""

    async def get_page_text(self, max_chars: int = 50000) -> str:
        """Extract all visible text from page."""
        text = await self.evaluate("document.body.innerText")
        return (text or "")[:max_chars]

    async def get_page_html(self, max_chars: int = 100000) -> str:
        """Get page HTML."""
        html = await self.evaluate("document.documentElement.outerHTML")
        return (html or "")[:max_chars]

    async def click_selector(self, selector: str):
        """Click element by CSS selector."""
        await self.evaluate(f"document.querySelector({json.dumps(selector)})?.click()")

    async def click_text(self, text: str):
        """Click first element containing text."""
        expr = f"""
        var els = document.querySelectorAll('a,button,input[type=submit],[role=button]');
        for(var el of els) {{
            if(el.textContent.trim().toLowerCase().includes({json.dumps(text.lower())})) {{
                el.click(); true;
            }}
        }}
        """
        await self.evaluate(expr)

    async def fill_field(self, selector: str, value: str):
        """Fill an input field by CSS selector."""
        safe_sel = json.dumps(selector)
        safe_val = json.dumps(value)
        await self.evaluate(f"""
            var el = document.querySelector({safe_sel});
            if(el) {{
                el.focus();
                el.value = {safe_val};
                el.dispatchEvent(new Event('input', {{bubbles:true}}));
                el.dispatchEvent(new Event('change', {{bubbles:true}}));
            }}
        """)

    async def scroll(self, direction: str = "down", amount: int = 500):
        """Scroll the page."""
        if direction == "down":
            await self.evaluate(f"window.scrollBy(0, {amount})")
        elif direction == "up":
            await self.evaluate(f"window.scrollBy(0, -{amount})")
        elif direction == "top":
            await self.evaluate("window.scrollTo(0, 0)")
        elif direction == "bottom":
            await self.evaluate("window.scrollTo(0, document.body.scrollHeight)")

    async def get_links(self) -> list:
        """Get all links on the page."""
        return await self.evaluate("""
            Array.from(document.querySelectorAll('a[href]')).map(a => ({
                text: a.textContent.trim().substring(0, 80),
                href: a.href
            })).filter(a => a.href && !a.href.startsWith('javascript'))
        """) or []

    async def get_elements(self, selector: str) -> list:
        """Get elements matching CSS selector."""
        safe = json.dumps(selector)
        return await self.evaluate(f"""
            Array.from(document.querySelectorAll({safe})).map(el => ({{
                tag: el.tagName.toLowerCase(),
                text: el.textContent.trim().substring(0, 100),
                id: el.id,
                class: el.className.substring(0, 60),
                href: el.href || '',
                value: el.value || ''
            }}))
        """) or []

    async def screenshot_b64(self) -> str:
        """Take page screenshot, returns base64 PNG."""
        result = await self.send("Page.captureScreenshot", {"format": "png", "quality": 80})
        return result.get("data", "")

    async def get_tabs(self) -> list:
        """List all open Chrome tabs."""
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{CDP_URL}/json", timeout=5)
        return [{"title": t.get("title", ""), "url": t.get("url", ""), "id": t.get("id", "")}
                for t in r.json() if t.get("type") == "page"]

    async def close(self):
        if self._listener_task:
            self._listener_task.cancel()
        if self.ws:
            await self.ws.close()


# ── Sync convenience wrapper (for skills that can't use async) ──
def run_cdp(coro):
    """Run a CDP coroutine from synchronous code."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=35)
        else:
            return loop.run_until_complete(coro)
    except Exception as e:
        return f"CDP error: {e}"


def is_cdp_available() -> bool:
    """Check if Chrome is running with CDP enabled."""
    try:
        r = httpx.get(f"{CDP_URL}/json", timeout=2)
        return r.status_code == 200 and len(r.json()) > 0
    except Exception:
        return False


async def _quick_eval(expression: str) -> Any:
    cdp = ChromeCDP()
    await cdp.connect()
    try:
        return await cdp.evaluate(expression)
    finally:
        await cdp.close()
