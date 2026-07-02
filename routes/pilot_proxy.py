"""CODEC Pilot HTTP proxy.

G2 / SR-58: extracted from codec_dashboard.py.

Forwards every `/api/pilot/<rest>` request (GET/POST/PUT/DELETE) to the
local Pilot Runner on http://localhost:8094/<rest>. The dashboard runs
over Cloudflare-tunneled HTTPS — Pilot Runner is HTTP-localhost — so
the PWA needs this same-origin proxy to reach it without a CORS
preflight or a mixed-content block.

Auth (PP-1): pilot-runner requires `x-pilot-token` (from ~/.codec/pilot_token,
0600) on every request. The proxy injects it server-side so the token never
reaches the browser; the dashboard's own AuthMiddleware gates who can reach
/api/pilot/* in the first place. Pilot stays loopback-only (P-1 — never
tunnel :8094 directly).

Streaming: the MJPEG live view (`screenshot/stream`) is proxied via
StreamingResponse chunk passthrough — a buffered request would hang forever
on the endless multipart stream.
"""
from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

router = APIRouter()

_TOKEN_PATH = os.path.expanduser("~/.codec/pilot_token")
_PILOT_BASE = "http://localhost:8094"

# Endpoints whose responses never end (multipart streams) — must be chunk-proxied.
_STREAM_PATHS = {"screenshot/stream"}


def _pilot_token() -> str:
    """Read the shared pilot token (never cached — supports rotation)."""
    try:
        with open(_TOKEN_PATH) as f:
            return f.read().strip()
    except OSError:
        return ""


def _build_headers(content_type: str | None) -> dict:
    headers = {"x-pilot-token": _pilot_token()}
    if content_type:
        headers["content-type"] = content_type
    return headers


@router.api_route("/api/pilot/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def pilot_proxy(path: str, request: Request):
    """Proxy /api/pilot/* → localhost:8094/* so the HTTPS dashboard can reach the local runner."""
    target = f"{_PILOT_BASE}/{path}"
    params = dict(request.query_params)
    headers = _build_headers(request.headers.get("content-type"))

    # ── live MJPEG stream: chunk passthrough, no buffering ──
    if path in _STREAM_PATHS:
        client = httpx.AsyncClient(timeout=None)
        try:
            req = client.build_request("GET", target, params=params, headers=headers)
            upstream = await client.send(req, stream=True)
        except httpx.ConnectError:
            await client.aclose()
            return JSONResponse({"error": "Pilot Runner offline — pm2 restart pilot-runner"},
                                status_code=503)
        except Exception as exc:
            await client.aclose()
            return JSONResponse({"error": str(exc)}, status_code=502)

        async def _relay():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            _relay(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type",
                                            "multipart/x-mixed-replace; boundary=frame"),
        )

    # ── normal request/response ──
    body = await request.body()
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
