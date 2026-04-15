"""CODEC MCP Server — HTTP transport with OAuth 2.1 for remote Claude access.

Exposes the same 50+ skills as `codec_mcp.py` but over streamable-http with
MCP-spec-compliant OAuth 2.1 (Dynamic Client Registration + Authorization Code
flow). Claude.ai custom connectors only accept OAuth; this unlocks them.

Architecture:
    claude.ai  →  Cloudflare tunnel  →  FastAPI :8091 (OAuth + MCP)  →  skills

Run:   pm2 start ecosystem.config.js --only codec-mcp-http
Env:   CODEC_MCP_HTTP_PORT       (default 8091)
       CODEC_MCP_HTTP_HOST       (default 127.0.0.1)
       CODEC_MCP_PUBLIC_BASE_URL (default https://codec-mcp.lucyvpa.com)
"""
import os, sys, logging

_REPO_DIR = os.path.dirname(os.path.abspath(__file__))
if _REPO_DIR not in sys.path:
    sys.path.insert(0, _REPO_DIR)

# Mark transport BEFORE importing codec_config so the strict HTTP blocklist
# applies (python_exec, file_ops, ax_control excluded from HTTP reach).
os.environ["CODEC_MCP_TRANSPORT"] = "http"

from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions

from codec_mcp import build_mcp  # side-effect-free factory
import uvicorn

log = logging.getLogger("codec_mcp_http")
logging.basicConfig(level=logging.INFO, format="[codec-mcp-http] %(message)s")


def main():
    port = int(os.environ.get("CODEC_MCP_HTTP_PORT", "8091"))
    host = os.environ.get("CODEC_MCP_HTTP_HOST", "127.0.0.1")
    public_base = os.environ.get(
        "CODEC_MCP_PUBLIC_BASE_URL", "https://codec-mcp.lucyvpa.com"
    )

    # OAuth 2.1 provider with dynamic client registration enabled —
    # claude.ai will auto-register itself as a client on first connect.
    auth = InMemoryOAuthProvider(
        base_url=public_base,
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
    )

    # Build FastMCP with OAuth baked in
    mcp = build_mcp(auth=auth)

    # Construct the HTTP app — FastMCP adds /.well-known/oauth-authorization-server,
    # /authorize, /token, /register, /revoke automatically.
    app = mcp.http_app(path="/mcp", transport="streamable-http")

    # Unauthenticated health endpoint for uptime checks
    from starlette.routing import Route
    from starlette.responses import PlainTextResponse

    async def _health(_req):
        return PlainTextResponse("ok")

    app.router.routes.append(Route("/health", _health, methods=["GET"]))

    tool_count = len(mcp._tools)
    log.info("CODEC MCP HTTP (OAuth 2.1) starting on %s:%s", host, port)
    log.info("Public base: %s", public_base)
    log.info("Tools exposed: %d", tool_count)
    log.info("OAuth metadata: %s/.well-known/oauth-authorization-server",
             public_base)
    log.info("MCP endpoint:   %s/mcp", public_base)
    log.info("Health:         %s/health", public_base)

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
