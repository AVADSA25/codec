"""
CODEC Pilot Phase 3 — HTTP Runner + Screencast test suite
==========================================================

Tests:
  1. Screencast — captures ≥2 frames during a 2-page navigation
  2. Runner startup — FastAPI app imports and creates routes correctly
  3. /health endpoint — returns status=ok
  4. /navigate endpoint — navigates and returns element_count
  5. /snapshot endpoint — returns rendered snapshot text
  6. /screenshot endpoint — returns JPEG bytes
  7. /run + /run/{id}/status — run lifecycle
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pilot.pilot_chrome import pilot_session
from pilot.snapshot import take_snapshot, render_for_llm
from pilot.screencast import Screencast


# ─── Test 1: Screencast ───────────────────────────────────────────────────────

async def test_screencast(pilot):
    print("│ [1/7] Screencast — ≥2 frames during 2-page nav...")
    async with Screencast(pilot, trace_id="test_phase3_sc", fps=4.0) as sc:
        await pilot.navigate("https://example.com")
        await pilot.wait(600)    # let screencast capture ~2 frames at 4fps
        await pilot.navigate("https://example.com")
        await pilot.wait(300)

    assert len(sc.frames) >= 2, f"Expected ≥2 frames, got {len(sc.frames)}"
    # All frame files should exist on disk
    for frame in sc.frames:
        assert frame.path.exists(), f"Frame file missing: {frame.path}"
        assert frame.size_bytes > 0, f"Empty frame: {frame.path}"

    manifest = sc.manifest()
    assert manifest["frame_count"] == len(sc.frames)
    assert manifest["trace_id"] == "test_phase3_sc"

    print(f"│       ✓ {len(sc.frames)} frames captured, manifest ok, dir={sc.trace_dir.name}")


# ─── Test 2: Runner app imports ───────────────────────────────────────────────

async def test_runner_imports(_pilot):
    print("│ [2/7] Runner imports + route registration...")
    from pilot.pilot_runner import app
    routes = {r.path for r in app.routes}
    required = {"/health", "/screenshot", "/snapshot", "/navigate", "/runs"}
    missing = required - routes
    assert not missing, f"Missing routes: {missing}"
    print(f"│       ✓ {len(routes)} routes registered, required routes present")


# ─── Test 3-7: Runner endpoints via TestClient ────────────────────────────────

async def test_runner_endpoints(pilot):
    print("│ [3/7] HTTP endpoints via ASGI TestClient...")
    try:
        from httpx import AsyncClient, ASGITransport
    except ImportError:
        print("│       ⚠ httpx not installed — skipping endpoint tests (pip install httpx)")
        return

    # Patch global pilot reference so the app uses our live pilot
    import pilot.pilot_runner as runner_mod
    runner_mod._pilot = pilot

    # PP-1: the runner now requires the x-pilot-token header on every request.
    transport = ASGITransport(app=runner_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"x-pilot-token": runner_mod._PILOT_TOKEN}) as client:

        # [3] /health
        r = await client.get("/health")
        assert r.status_code == 200, f"/health → {r.status_code}"
        body = r.json()
        assert body["status"] == "ok"
        print(f"│       ✓ [3] /health → ok, port={body['cdp_port']}")

        # [4] /navigate
        await pilot.navigate("https://example.com")
        r = await client.post("/navigate", json={"url": "https://example.com"})
        assert r.status_code == 200, f"/navigate → {r.status_code}"
        body = r.json()
        assert body["element_count"] >= 1
        print(f"│       ✓ [4] /navigate → {body['element_count']} elements")

        # [5] /snapshot
        r = await client.get("/snapshot")
        assert r.status_code == 200
        body = r.json()
        assert "rendered" in body
        assert body["element_count"] >= 1
        assert "URL:" in body["rendered"]
        print(f"│       ✓ [5] /snapshot → {body['element_count']} elements, {body['took_ms']:.0f}ms")

        # [6] /screenshot
        r = await client.get("/screenshot")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert len(r.content) > 5000, f"Screenshot too small: {len(r.content)} bytes"
        print(f"│       ✓ [6] /screenshot → {len(r.content)/1024:.1f} KB JPEG")

        # [7] /run + /run/{id}/status
        r = await client.post("/run", json={"task": "test task", "tag": "phase3-test"})
        assert r.status_code == 200
        run_id = r.json()["run_id"]
        assert run_id

        r2 = await client.get(f"/run/{run_id}/status")
        assert r2.status_code == 200
        status = r2.json()
        assert status["task"] == "test task"
        assert status["status"] == "running"
        print(f"│       ✓ [7] /run → run_id={run_id[:8]}…, status=running")


# ─── runner ───────────────────────────────────────────────────────────────────

async def main():
    print("┌─ CODEC Pilot Phase 3 — HTTP Runner + Screencast test ────────")
    print("│ Launching Pilot Chromium (headless=True)...")

    failed = []
    async with pilot_session(headless=True) as pilot:
        tests = [
            test_screencast,
            test_runner_imports,
            test_runner_endpoints,
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
                import traceback; traceback.print_exc()
                failed.append(name)

    print("│")
    if failed:
        print(f"└─ ✗ Phase 3 FAILED — {len(failed)} test(s): {', '.join(failed)}")
        sys.exit(1)
    else:
        print("└─ ✓ Phase 3 PASSED — screencast + runner endpoints green")


if __name__ == "__main__":
    asyncio.run(main())
