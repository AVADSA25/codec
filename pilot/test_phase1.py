import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from pilot.pilot_chrome import pilot_session
from pilot.snapshot import render_for_llm

async def main() -> None:
    out = Path("/tmp/pilot_phase1_test.jpg")
    print("┌─ CODEC Pilot Phase 1 smoke test ─────────────────────────")
    print("│ [1/5] Launching Pilot Chromium (headless=True)...")
    async with pilot_session(headless=True) as pilot:
        print(f"│       ✓ profile : {pilot.profile_dir}")
        print(f"│       ✓ cdp port: {pilot.cdp_port}")
        print("│ [2/5] Navigating to example.com...")
        await pilot.navigate("https://example.com")
        print(f"│       ✓ url   : {await pilot.get_url()}")
        print(f"│       ✓ title : {await pilot.get_title()}")
        print("│ [3/5] Taking snapshot...")
        snap = await pilot.snapshot()
        print(f"│       ✓ snapshot: {len(snap)} elements, {snap.took_ms:.0f}ms")
        print("│ [4/5] Taking screenshot...")
        await pilot.screenshot(path=str(out))
        print(f"│       ✓ saved: {out} ({out.stat().st_size/1024:.1f} KB)")
        print("│ [5/5] Closing...")
    print("└─ ✓ Phase 1 PASSED")

asyncio.run(main())
