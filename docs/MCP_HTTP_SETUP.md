# CODEC MCP over HTTP — Remote Claude Access

Expose your CODEC skills to **claude.ai web, Claude mobile, or any remote Claude session** via a Cloudflare Zero Trust tunnel.

## Architecture

```
claude.ai / mobile  →  Cloudflare tunnel  →  FastAPI :8091  →  FastMCP streamable-http  →  51 skills
                            (Access policy)      (Bearer token)
```

Two auth layers:
1. **Cloudflare Access** — email policy (only you get through)
2. **Bearer token** — stored in `~/.codec/mcp_token`, required on every request

## Start the bridge

```bash
pm2 start ecosystem.config.js --only codec-mcp-http
pm2 save
```

Verify locally:
```bash
# Health (no auth)
curl http://127.0.0.1:8091/health     # -> "ok"

# MCP endpoint (auth required)
TOKEN=$(cat ~/.codec/mcp_token)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8091/mcp
```

## Cloudflare Tunnel

Add to your existing `cloudflared` config (same tunnel that serves the dashboard):

```yaml
ingress:
  - hostname: codec-mcp.yourdomain.com
    service: http://127.0.0.1:8091
  - service: http_status:404
```

Then in Cloudflare Zero Trust dashboard:
1. **Access → Applications → Add application → Self-hosted**
2. Application domain: `codec-mcp.yourdomain.com`
3. Policy: `Include → Emails → your@email.com`
4. Session duration: 24h

## Connect from claude.ai

1. Open claude.ai → Settings → Connectors → **Add custom connector**
2. URL: `https://codec-mcp.yourdomain.com/mcp`
3. Authentication: **Bearer token** → paste contents of `~/.codec/mcp_token`
4. Save. Tool list populates automatically (50+ skills).

## Connect from Claude mobile

Same as claude.ai — custom connector URL + bearer token.

## Security notes

- The bearer token is generated on first launch and stored with `0600` perms
- Change it anytime: `rm ~/.codec/mcp_token && pm2 restart codec-mcp-http`
- Cloudflare Access blocks requests before they reach your Mac — bearer is defense-in-depth
- Host stays bound to `127.0.0.1` — only cloudflared can reach it from the box
- Blocklist still applies (`python_exec`, `terminal`, `pm2_control`, `process_manager` excluded)

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| 401 | No `Authorization: Bearer` header | Configure bearer in connector |
| 403 | Wrong token | Re-copy from `~/.codec/mcp_token` |
| 406 | Missing `Accept: application/json, text/event-stream` | MCP client handles this automatically |
| Connection refused | Service down | `pm2 restart codec-mcp-http` |
| Cloudflare 1033 | Tunnel misconfigured | Check `cloudflared tunnel info` |
