"""
CODEC Pilot — Phase 1 Foundation / Phase 2 Snapshot
=====================================================

Dedicated Chromium instance for CODEC Pilot, separate from the user's real
Chrome (which existing chrome_* skills target on port 9222).

Pilot Chrome lives on:
    profile : ~/.codec/pilot_chrome_profile
    CDP port: 9223
    process : owned by Pilot, no interference with user's daily browsing

Phase 2 wires snapshot() to the indexed-DOM extractor in snapshot.py.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Any
from urllib.parse import urlsplit

from playwright.async_api import (
    async_playwright,
    BrowserContext,
    Page,
    Playwright,
)

if TYPE_CHECKING:
    from .snapshot import PageSnapshot

DEFAULT_PROFILE_DIR = Path.home() / ".codec" / "pilot_chrome_profile"
DEFAULT_CDP_PORT = 9223  # legacy constant; the CDP port is now randomized per launch (P-8)


def _free_port() -> int:
    """PP-5 (audit P-8): pick a random free localhost port for Chromium's CDP
    socket, instead of a fixed predictable 9223 that any local process could
    attach to (Chrome's CDP socket has no auth). The socket binds loopback only,
    so this is local-hijack hardening — unpredictability over a known port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])

# PP-3 (audit P-4): SSRF / dangerous-scheme guard for navigation.
_ALLOWED_SCHEMES = {"http", "https"}
_BLOCKED_HOSTNAMES = {"localhost"}
_BLOCKED_HOST_SUFFIXES = (".local", ".localhost", ".internal")


def validate_navigation_url(url: str) -> str:
    """Return `url` if safe to navigate to, else raise ValueError.

    Blocks (audit P-4): non-http(s) schemes (file:/javascript:/data:/chrome:/about:),
    and hosts that are loopback / private (RFC1918) / link-local (incl. the cloud
    metadata 169.254.169.254) / reserved / multicast, plus localhost and
    *.local/.localhost/.internal. This stops the agent (or an unauthenticated caller)
    from reading local files, pivoting to internal services (the dashboard, the local
    LLM, the Pilot CDP socket), or hitting cloud metadata.

    Residual (documented): a public hostname that DNS-resolves to a private IP
    (DNS-rebinding) is not caught here — that needs resolve-then-check with TOCTOU
    handling, a larger change."""
    if not url or not isinstance(url, str):
        raise ValueError("empty navigation URL")
    # Exact-match allowance for the canonical empty page (PP-3 follow-up): about:blank
    # has no host, makes no network request, and reads no file, so it's not an SSRF/
    # file-read vector — and it's the standard page-reset primitive the agent uses.
    # NOTE: exact match only — broad about: URLs (about:config, about:settings, …) and
    # all other non-http(s) schemes stay blocked below.
    if url.strip().lower() == "about:blank":
        return url
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError(f"blocked URL scheme: {parts.scheme!r} (only http/https)")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("navigation URL has no host")
    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_HOST_SUFFIXES):
        raise ValueError(f"blocked internal host: {host!r}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None  # not a literal IP — a public hostname
    if ip is not None and (ip.is_loopback or ip.is_private or ip.is_link_local
                           or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
        raise ValueError(f"blocked internal/non-routable IP: {host!r}")
    return url


class PilotChrome:
    """Low-level wrapper around a dedicated Chromium instance for CODEC Pilot."""

    def __init__(
        self,
        headless: bool = False,
        cdp_port: Optional[int] = None,
        profile_dir: Optional[Path] = None,
        viewport: tuple[int, int] = (1280, 800),
    ) -> None:
        self.headless = headless
        # P-8: randomize the CDP port per launch unless one is explicitly given.
        self.cdp_port = cdp_port if cdp_port is not None else _free_port()
        self.profile_dir: Path = profile_dir or DEFAULT_PROFILE_DIR
        self.viewport = viewport
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    # ─── lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        """Launch Pilot Chromium with persistent profile + CDP debugging enabled."""
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = await async_playwright().start()

        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            viewport={"width": self.viewport[0], "height": self.viewport[1]},
            args=[
                f"--remote-debugging-port={self.cdp_port}",
                "--no-first-run",
                "--no-default-browser-check",
                # Minimal anti-fingerprint: hides navigator.webdriver. Full
                # stealth (proxies, captcha solving) is out of scope for v1.
                "--disable-blink-features=AutomationControlled",
            ],
        )

        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

    async def stop(self) -> None:
        """Close browser cleanly. Profile persists on disk for the next run."""
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._page = None

    # ─── navigation ──────────────────────────────────────────────────────

    async def navigate(self, url: str, wait_until: str = "domcontentloaded") -> None:
        """Navigate to URL. wait_until: 'load' | 'domcontentloaded' | 'networkidle'."""
        validate_navigation_url(url)  # PP-3 (P-4): block file:/internal/SSRF targets
        from .audit import audit  # PP-8 (P-12): record navigation of logged-in sites
        audit("navigate", url=url)
        await self._require_page().goto(url, wait_until=wait_until)

    async def get_url(self) -> str:
        return self._require_page().url

    async def get_title(self) -> str:
        return await self._require_page().title()

    # ─── observation ─────────────────────────────────────────────────────

    async def screenshot(
        self,
        path: Optional[str] = None,
        full_page: bool = False,
        quality: int = 80,
    ) -> bytes:
        """Capture JPEG screenshot as bytes. Optionally also write to disk."""
        kwargs: dict[str, Any] = {
            "full_page": full_page,
            "type": "jpeg",
            "quality": quality,
        }
        if path is not None:
            kwargs["path"] = path
        return await self._require_page().screenshot(**kwargs)

    async def snapshot(self) -> "PageSnapshot":
        """
        Phase 2: indexed-DOM snapshot via snapshot.take_snapshot().

        Returns a PageSnapshot with all viewport-visible interactive elements
        indexed [1..N] with role, accessible name, XPath, and bounding box.
        Use render_for_llm(snap) to get the compact text for an LLM prompt.
        """
        # Late import avoids circular dependency during package init
        from .snapshot import take_snapshot
        return await take_snapshot(self._require_page())

    # ─── low-level primitives (Phase 1 — Phase 2 adds indexed actions) ───

    async def click_xpath(self, xpath: str, timeout: int = 5000) -> None:
        """Click an element by raw XPath. Phase 2 will use indexed [N] refs."""
        await self._require_page().locator(f"xpath={xpath}").click(timeout=timeout)

    async def type_xpath(self, xpath: str, text: str, timeout: int = 5000) -> None:
        """Type text into an element by XPath."""
        await self._require_page().locator(f"xpath={xpath}").fill(text, timeout=timeout)

    async def wait(self, ms: int) -> None:
        """Sleep without blocking the event loop."""
        await asyncio.sleep(ms / 1000)

    # ─── escape hatch ────────────────────────────────────────────────────

    @property
    def page(self) -> Page:
        """Direct Playwright Page access for primitives not yet wrapped."""
        return self._require_page()

    # ─── internals ───────────────────────────────────────────────────────

    def _require_page(self) -> Page:
        if self._page is None:
            raise RuntimeError("PilotChrome not started — call start() first.")
        return self._page


class pilot_session:
    """Async context manager: `async with pilot_session() as pilot: ...`"""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.pilot: Optional[PilotChrome] = None

    async def __aenter__(self) -> PilotChrome:
        self.pilot = PilotChrome(**self.kwargs)
        await self.pilot.start()
        return self.pilot

    async def __aexit__(self, *exc: Any) -> None:
        if self.pilot is not None:
            await self.pilot.stop()
