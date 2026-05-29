"""
CODEC Pilot Phase 2 — Indexed-DOM Snapshot test suite
======================================================

Runs 5 real-site assertions against take_snapshot() + render_for_llm().
Each site exercises a different class of interactive content.

Sites:
  1. example.com       — minimal page, ≥1 link indexed
  2. news.ycombinator.com — link-heavy page, ≥10 links
  3. google.com        — searchbox present
  4. github.com/login  — textboxes + submit button
  5. en.wikipedia.org  — navigation links ≥5
"""

import asyncio
import sys
from pathlib import Path

# Allow running from tests/ or pilot/ root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pilot.pilot_chrome import pilot_session
from pilot.snapshot import take_snapshot, render_for_llm, PageSnapshot


# ─── helpers ──────────────────────────────────────────────────────────────────

def _roles(snap: PageSnapshot) -> list[str]:
    return [el.role for el in snap.elements]

def _names(snap: PageSnapshot) -> list[str]:
    return [el.name.lower() for el in snap.elements]

def _has_role(snap: PageSnapshot, role: str) -> bool:
    return role in _roles(snap)

def _count_role(snap: PageSnapshot, role: str) -> int:
    return _roles(snap).count(role)


# ─── test cases ───────────────────────────────────────────────────────────────

async def test_example_com(pilot):
    print("│ [1/5] example.com — minimal page, ≥1 link...")
    await pilot.navigate("https://example.com")
    snap = await take_snapshot(pilot.page)
    rendered = render_for_llm(snap)

    assert len(snap) >= 1, f"Expected ≥1 element, got {len(snap)}"
    assert _has_role(snap, "link"), "Expected at least one link on example.com"
    # Snapshot text must contain URL and TITLE headers
    assert "URL: https://example.com" in rendered
    assert "TITLE:" in rendered
    # Performance: must complete in <1000 ms (typical <100 ms)
    assert snap.took_ms < 1000, f"Snapshot took {snap.took_ms:.0f}ms (limit 1000ms)"

    link_count = _count_role(snap, "link")
    print(f"│       ✓ {len(snap)} elements, {link_count} links, {snap.took_ms:.0f}ms")


async def test_hacker_news(pilot):
    print("│ [2/5] news.ycombinator.com — link-heavy, ≥10 links...")
    await pilot.navigate("https://news.ycombinator.com")
    snap = await take_snapshot(pilot.page)

    link_count = _count_role(snap, "link")
    assert link_count >= 10, f"Expected ≥10 links on HN, got {link_count}"
    assert snap.took_ms < 1000, f"Snapshot took {snap.took_ms:.0f}ms"

    print(f"│       ✓ {len(snap)} elements, {link_count} links, {snap.took_ms:.0f}ms")


async def test_google_searchbox(pilot):
    print("│ [3/5] google.com — searchbox present...")
    await pilot.navigate("https://www.google.com")
    snap = await take_snapshot(pilot.page)

    # Google's search input has role=combobox or textbox depending on version
    has_search = (
        _has_role(snap, "combobox") or
        _has_role(snap, "searchbox") or
        _has_role(snap, "textbox")
    )
    assert has_search, (
        f"Expected a searchbox/combobox/textbox on Google.\n"
        f"Roles found: {set(_roles(snap))}"
    )
    assert snap.took_ms < 1000

    search_roles = [r for r in _roles(snap) if r in ("combobox", "searchbox", "textbox")]
    print(f"│       ✓ {len(snap)} elements, search input found as '{search_roles[0]}', {snap.took_ms:.0f}ms")


async def test_github_login(pilot):
    print("│ [4/5] github.com/login — textboxes + submit button...")
    await pilot.navigate("https://github.com/login")
    snap = await take_snapshot(pilot.page)

    # Must have at least 2 textboxes (username + password)
    textbox_count = sum(1 for r in _roles(snap) if r in ("textbox", "searchbox"))
    assert textbox_count >= 2, f"Expected ≥2 text inputs on GitHub login, got {textbox_count}"

    # Must have a submit button
    has_button = _has_role(snap, "button")
    assert has_button, "Expected a submit button on GitHub login page"

    print(f"│       ✓ {len(snap)} elements, {textbox_count} textboxes, button ✓, {snap.took_ms:.0f}ms")


async def test_wikipedia(pilot):
    print("│ [5/5] en.wikipedia.org — navigation links ≥5...")
    await pilot.navigate("https://en.wikipedia.org/wiki/Main_Page")
    snap = await take_snapshot(pilot.page)

    link_count = _count_role(snap, "link")
    assert link_count >= 5, f"Expected ≥5 links on Wikipedia, got {link_count}"
    assert snap.took_ms < 1000

    print(f"│       ✓ {len(snap)} elements, {link_count} links, {snap.took_ms:.0f}ms")


# ─── runner ───────────────────────────────────────────────────────────────────

async def main():
    print("┌─ CODEC Pilot Phase 2 — Indexed-DOM Snapshot test ────────────")
    print("│ Launching Pilot Chromium (headless=True)...")

    failed = []
    async with pilot_session(headless=True) as pilot:
        tests = [
            test_example_com,
            test_hacker_news,
            test_google_searchbox,
            test_github_login,
            test_wikipedia,
        ]
        for test_fn in tests:
            try:
                await test_fn(pilot)
            except AssertionError as exc:
                name = test_fn.__name__
                print(f"│       ✗ FAIL {name}: {exc}")
                failed.append(name)
            except Exception as exc:
                name = test_fn.__name__
                print(f"│       ✗ ERROR {name}: {type(exc).__name__}: {exc}")
                failed.append(name)

    print("│")
    if failed:
        print(f"└─ ✗ Phase 2 FAILED — {len(failed)} test(s) failed: {', '.join(failed)}")
        sys.exit(1)
    else:
        print("└─ ✓ Phase 2 PASSED — all 5 site assertions green")


if __name__ == "__main__":
    asyncio.run(main())
