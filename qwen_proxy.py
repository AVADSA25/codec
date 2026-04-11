#!/usr/bin/env python3
"""Minimal serializing proxy for mlx_lm.server.

mlx_lm.server uses ThreadingHTTPServer. When multiple threads call generate()
concurrently, they deadlock on the single-threaded Metal GPU. This proxy sits
on :8081 and ensures only ONE request at a time reaches the backend on :18081.

That's it. No health checks, no auto-restart, no caching. Just serialization.
"""
import asyncio
import aiohttp
from aiohttp import web

LISTEN_PORT = 8081
BACKEND = "http://127.0.0.1:18081"
_sem = asyncio.Semaphore(1)


async def proxy(request: web.Request):
    url = f"{BACKEND}{request.path}"
    if request.query_string:
        url += f"?{request.query_string}"

    # GET requests (like /v1/models) don't need serialization
    if request.method == "GET":
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                body = await r.read()
                return web.Response(body=body, status=r.status,
                                    content_type=r.content_type)

    # POST requests (inference) — serialize through semaphore
    data = await request.read()
    async with _sem:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, data=data,
                              headers={"Content-Type": "application/json"},
                              timeout=aiohttp.ClientTimeout(total=120)) as r:
                body = await r.read()
                return web.Response(body=body, status=r.status,
                                    content_type=r.content_type)


app = web.Application()
app.router.add_route("*", "/{path:.*}", proxy)

if __name__ == "__main__":
    print(f"[QwenProxy] Serializing proxy on :{LISTEN_PORT} → {BACKEND}")
    web.run_app(app, host="127.0.0.1", port=LISTEN_PORT, print=None)
