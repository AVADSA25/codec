"""CODEC Pilot HTTP proxy.

G2 / SR-58: extracted from codec_dashboard.py.

Forwards every `/api/pilot/<rest>` request (GET/POST/PUT/DELETE) to the
local Pilot Runner on http://localhost:8094/<rest>. The dashboard runs
over Cloudflare-tunneled HTTPS — Pilot Runner is HTTP-localhost — so
the PWA needs this same-origin proxy to reach it without a CORS
preflight or a mixed-content block.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

router = APIRouter()


@router.api_route("/api/pilot/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def pilot_proxy(path: str, request: Request):
    """Proxy /api/pilot/* → localhost:8094/* so the HTTPS dashboard can reach the local runner."""
    target = f"http://localhost:8094/{path}"
    params = dict(request.query_params)
    body = await request.body()
    headers = {}
    if request.headers.get("content-type"):
        headers["content-type"] = request.headers["content-type"]
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.request(
                method=request.method,
                url=target,
                params=params,
                content=body,
                headers=headers,
            )
        return Response(
            content=r.content,
            status_code=r.status_code,
            media_type=r.headers.get("content-type", "application/json"),
        )
    except httpx.ConnectError:
        return JSONResponse({"error": "Pilot Runner offline — pm2 restart pilot-runner"}, status_code=503)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
