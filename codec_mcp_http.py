"""CODEC MCP Server — HTTP transport with OAuth 2.1 for remote Claude access.

Exposes the same 50+ skills as `codec_mcp.py` but over streamable-http with
MCP-spec-compliant OAuth 2.1 (Dynamic Client Registration + Authorization Code
flow). Claude.ai custom connectors only accept OAuth; this unlocks them.

Architecture:
    claude.ai  →  Cloudflare tunnel  →  FastAPI :8091 (OAuth + MCP)  →  skills

Adds:
  - Token-bucket rate limiter on /mcp (per client IP, default 60/min)
  - Deep /health endpoint (memory DB, Kokoro TTS, Qwen LLM reachability)
  - /metrics endpoint (JSON counters: requests, errors, avg latency)

Run:   pm2 start ecosystem.config.js --only codec-mcp-http
Env:   CODEC_MCP_HTTP_PORT       (default 8091)
       CODEC_MCP_HTTP_HOST       (default 127.0.0.1)
       CODEC_MCP_PUBLIC_BASE_URL (default https://codec-mcp.lucyvpa.com)
       CODEC_MCP_RATE_PER_MIN    (default 60)
"""
import os, sys, logging, time, json, threading
from collections import defaultdict, deque

_REPO_DIR = os.path.dirname(os.path.abspath(__file__))
if _REPO_DIR not in sys.path:
    sys.path.insert(0, _REPO_DIR)

# Mark transport BEFORE importing codec_config so the strict HTTP blocklist
# applies (python_exec, file_ops, ax_control excluded from HTTP reach).
os.environ["CODEC_MCP_TRANSPORT"] = "http"

from codec_oauth_provider import PersistentOAuthProvider
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions

from codec_mcp import build_mcp  # side-effect-free factory
import uvicorn

log = logging.getLogger("codec_mcp_http")
logging.basicConfig(level=logging.INFO, format="[codec-mcp-http] %(message)s")


# ---------- Rate limiter (per-IP sliding window) ----------
_RATE_LOCK = threading.Lock()
_RATE_WINDOW: dict[str, deque] = defaultdict(deque)
_RATE_LIMIT = int(os.environ.get("CODEC_MCP_RATE_PER_MIN", "60"))


def _rate_check(ip: str) -> bool:
    """Return True if under limit, False if rate-limited."""
    now = time.time()
    cutoff = now - 60
    with _RATE_LOCK:
        q = _RATE_WINDOW[ip]
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= _RATE_LIMIT:
            return False
        q.append(now)
        return True


# ---------- Metrics ----------
_METRICS = {
    "started_at": time.time(),
    "requests_total": 0,
    "requests_error": 0,
    "rate_limited": 0,
    "latency_ms_sum": 0.0,
}
_METRICS_LOCK = threading.Lock()


def main():
    port = int(os.environ.get("CODEC_MCP_HTTP_PORT", "8091"))
    host = os.environ.get("CODEC_MCP_HTTP_HOST", "127.0.0.1")
    public_base = os.environ.get(
        "CODEC_MCP_PUBLIC_BASE_URL", "https://codec-mcp.lucyvpa.com"
    )

    auth = PersistentOAuthProvider(
        base_url=public_base,
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
    )

    mcp = build_mcp(auth=auth)
    app = mcp.http_app(path="/mcp", transport="streamable-http")

    # ---------- Middleware: rate limit + metrics ----------
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse, PlainTextResponse
    from starlette.routing import Route

    class RateAndMetricsMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            # Only rate-limit the MCP tool path; leave OAuth + health alone
            if request.url.path.startswith("/mcp"):
                # Prefer CF-Connecting-IP (set by Cloudflare tunnel)
                ip = (request.headers.get("cf-connecting-ip")
                      or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                      or (request.client.host if request.client else "unknown"))
                if not _rate_check(ip):
                    with _METRICS_LOCK:
                        _METRICS["rate_limited"] += 1
                    return JSONResponse(
                        {"error": "rate_limited",
                         "error_description": f"Max {_RATE_LIMIT} req/min per IP"},
                        status_code=429,
                    )
            t0 = time.time()
            try:
                resp = await call_next(request)
                with _METRICS_LOCK:
                    _METRICS["requests_total"] += 1
                    _METRICS["latency_ms_sum"] += (time.time() - t0) * 1000
                    if resp.status_code >= 500:
                        _METRICS["requests_error"] += 1
                return resp
            except Exception:
                with _METRICS_LOCK:
                    _METRICS["requests_total"] += 1
                    _METRICS["requests_error"] += 1
                raise

    app.add_middleware(RateAndMetricsMiddleware)

    # ---------- /health — deep check ----------
    import httpx
    from codec_config import KOKORO_URL, QWEN_BASE_URL, DB_PATH

    async def _health(_req):
        checks = {}
        # Memory DB writable?
        try:
            import sqlite3
            conn = sqlite3.connect(DB_PATH, timeout=2)
            conn.execute("SELECT 1")
            conn.close()
            checks["memory_db"] = "ok"
        except Exception as e:
            checks["memory_db"] = f"error: {type(e).__name__}"

        # OAuth state file exists?
        oauth_state = os.path.expanduser("~/.codec/oauth_state.json")
        checks["oauth_state"] = "ok" if os.path.exists(oauth_state) else "missing"

        # Kokoro TTS
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(KOKORO_URL.replace("/v1/audio/speech", "/health"))
                checks["kokoro_tts"] = "ok" if r.status_code < 500 else f"http_{r.status_code}"
        except Exception:
            checks["kokoro_tts"] = "unreachable"

        # Qwen LLM
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(QWEN_BASE_URL.replace("/v1", "/health"))
                checks["qwen_llm"] = "ok" if r.status_code < 500 else f"http_{r.status_code}"
        except Exception:
            checks["qwen_llm"] = "unreachable"

        critical_ok = checks["memory_db"] == "ok" and checks["oauth_state"] == "ok"
        status_code = 200 if critical_ok else 503
        return JSONResponse(
            {"status": "healthy" if critical_ok else "degraded",
             "uptime_sec": int(time.time() - _METRICS["started_at"]),
             "checks": checks},
            status_code=status_code,
        )

    # ---------- /metrics ----------
    async def _metrics(_req):
        with _METRICS_LOCK:
            snap = dict(_METRICS)
        total = max(snap["requests_total"], 1)
        snap["uptime_sec"] = int(time.time() - snap["started_at"])
        snap["avg_latency_ms"] = round(snap["latency_ms_sum"] / total, 2)
        snap["error_rate"] = round(snap["requests_error"] / total, 4)
        return JSONResponse(snap)

    app.router.routes.append(Route("/health", _health, methods=["GET"]))
    app.router.routes.append(Route("/metrics", _metrics, methods=["GET"]))

    tool_count = len(mcp._tools)
    log.info("CODEC MCP HTTP (OAuth 2.1) starting on %s:%s", host, port)
    log.info("Public base: %s", public_base)
    log.info("Tools exposed: %d", tool_count)
    log.info("Rate limit: %d req/min per IP", _RATE_LIMIT)
    log.info("OAuth metadata: %s/.well-known/oauth-authorization-server", public_base)
    log.info("MCP endpoint:   %s/mcp", public_base)
    log.info("Health:         %s/health", public_base)
    log.info("Metrics:        %s/metrics", public_base)

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
