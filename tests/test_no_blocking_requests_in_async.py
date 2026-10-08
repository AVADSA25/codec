"""No blocking `requests` call inside an `async def` of the dashboard.

A blocking call there stops the event loop: every page, poll and the voice socket
wait until it returns. The vision warmup did this at startup (fixed 2026-10-07,
#446), and three photo routes did it while a picture was analysed (webcam
capture, image upload, vision). Put the call in `asyncio.to_thread(...)`.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FILES = [REPO / "codec_dashboard.py", *sorted((REPO / "routes").glob("*.py"))]
REQUEST_NAMES = {"requests", "rq", "_requests"}
BLOCKING = {"get", "post", "put", "delete", "patch", "head", "request"}


def _blocking_calls(path: Path) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in BLOCKING and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in REQUEST_NAMES):
                hits.append(f"{path.relative_to(REPO)}:{node.lineno} in {fn.name}()")
    return hits


def test_no_async_handler_calls_requests_on_the_event_loop():
    hits = [h for f in FILES for h in _blocking_calls(f)]
    assert not hits, "blocking requests call on the event loop: " + ", ".join(hits)


def test_the_scan_catches_a_blocking_call(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import requests as rq\nasync def h():\n    return rq.post('http://x')\n")
    ok = tmp_path / "ok.py"
    ok.write_text("import asyncio, requests as rq\nasync def h():\n    return await asyncio.to_thread(rq.post, 'http://x')\n")
    global REPO
    saved, REPO = REPO, tmp_path
    try:
        assert _blocking_calls(bad) == ["bad.py:3 in h()"] and _blocking_calls(ok) == []
    finally:
        REPO = saved
