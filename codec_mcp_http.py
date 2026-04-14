"""CODEC MCP Server — HTTP transport for remote Claude access.

Exposes the same 50+ skills as `codec_mcp.py` but over streamable-http,
suitable for claude.ai custom connectors / mobile / any remote Claude.

Two auth layers (defense in depth):
  1. Cloudflare Access (email policy) — configured in dashboard
  2. Bearer token — required on every request via Authorization header

Token read from env CODEC_MCP_TOKEN, else ~/.codec/mcp_token (auto-generated).

Run:   python3 codec_mcp_http.py
Env:   CODEC_MCP_HTTP_PORT (default 8091)
       CODEC_MCP_HTTP_HOST (default 127.0.0.1 — put cloudflared in front)
"""
import os, sys, secrets, logging
from pathlib import Path

_REPO_DIR = os.path.dirname(os.path.abspath(__file__))
if _REPO_DIR not in sys.path:
    sys.path.insert(0, _REPO_DIR)

# Importing codec_mcp registers all skill tools on the shared `mcp` object.
import codec_mcp  # noqa: F401  (side-effect: tool registration)
from codec_mcp import mcp

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import uvicorn

log = logging.getLogger("codec_mcp_http")
logging.basicConfig(level=logging.INFO, format="[codec-mcp-http] %(message)s")

# ── Token resolution ──────────────────────────────────────────────────
_TOKEN_FILE = Path.home() / ".codec" / "mcp_token"


def _get_token() -> str:
    tok = os.environ.get("CODEC_MCP_TOKEN", "").strip()
    if tok:
        return tok
    if _TOKEN_FILE.exists():
        return _TOKEN_FILE.read_text().strip()
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    _TOKEN_FILE.write_text(tok)
    _TOKEN_FILE.chmod(0o600)
    log.info("Generated new MCP token at %s", _TOKEN_FILE)
    return tok


TOKEN = _get_token()

# ── Bearer token middleware ───────────────────────────────────────────
PUBLIC_PATHS = {"/health", "/healthz"}


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                {"error": "missing bearer token"}, status_code=401
            )
        supplied = auth[7:].strip()
        # secrets.compare_digest = constant time
        if not secrets.compare_digest(supplied, TOKEN):
            log.warning("Rejected request with bad token from %s",
                        request.client.host if request.client else "?")
            return JSONResponse(
                {"error": "invalid token"}, status_code=403
            )
        return await call_next(request)


def main():
    port = int(os.environ.get("CODEC_MCP_HTTP_PORT", "8091"))
    host = os.environ.get("CODEC_MCP_HTTP_HOST", "127.0.0.1")

    # Build Starlette app from FastMCP then attach auth middleware
    app = mcp.http_app(path="/mcp", transport="streamable-http")
    app.add_middleware(BearerAuthMiddleware)

    # Tiny health endpoint (unauthenticated) for cloudflared/uptime
    from starlette.routing import Route
    from starlette.responses import PlainTextResponse

    async def _health(_req):
        return PlainTextResponse("ok")

    app.router.routes.append(Route("/health", _health, methods=["GET"]))

    tool_count = len(mcp._tools)
    log.info("CODEC MCP HTTP starting on %s:%s (%d tools exposed)",
             host, port, tool_count)
    log.info("Token file: %s", _TOKEN_FILE)
    log.info("MCP endpoint: http://%s:%s/mcp", host, port)
    log.info("Health: http://%s:%s/health", host, port)

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
