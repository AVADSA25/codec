"""CODEC Connector (external MCP) API routes.

Backs the dashboard "Connector" tab. CODEC is an MCP *client* via
``skills/mcp_connect.py``, which reads ``~/.codec/mcp_servers.json`` — a curated
menu of public MCP servers (Notion, GitHub, Linear, Stripe, Hugging Face, …).
Until now that file was hand-edited and driven only by voice/chat
("list mcp", "connect to notion"). These two endpoints give it a UI:

  GET  /api/mcp/servers                → list servers (name, transport, url,
                                          enabled, needs_auth, auth, note).
                                          Secret ``headers`` are never returned.
  POST /api/mcp/servers/{name}/toggle  {enabled: bool}
                                       → flip ONLY the ``enabled`` flag of one
                                         server. No other field from the request
                                         body is ever written.

Both sit behind the dashboard's AuthMiddleware. The POST is state-changing so
the PWA sends ``x-csrf-token`` (the global fetch wrapper injects it and the
Connector JS also sets it explicitly). The write is cross-process-safe via
``codec_jsonstore.read_modify_write`` (flock sidecar + atomic tmp+fsync+replace),
so a concurrent voice/chat edit and a UI toggle can't clobber each other.
"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Request

from codec_jsonstore import read_modify_write

router = APIRouter()

# Canonical location, owned by skills/mcp_connect.py (which seeds it on first
# use). We read/write the SAME file so the Connector tab and the voice/chat
# "list mcp" / "connect to notion" path stay in sync.
MCP_SERVERS_PATH = os.path.expanduser("~/.codec/mcp_servers.json")


def _read_servers() -> list[dict]:
    """Return the raw servers list, or [] if the file is missing/corrupt.

    Read-only: never seeds. The file is created by the mcp_connect skill on
    its first run; if it doesn't exist yet the UI shows a "say 'list mcp'"
    hint instead of us writing user state from a GET.
    """
    try:
        with open(MCP_SERVERS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    servers = data.get("servers") if isinstance(data, dict) else None
    return servers if isinstance(servers, list) else []


def _public_view(s: dict) -> dict:
    """Project a server entry to the fields the Connector UI needs.

    Deliberately omits ``headers`` (which carry API keys) — the UI only needs
    to know that sign-in is required, not the secret itself.
    """
    auth = str(s.get("auth") or "").strip().lower()
    # "Needs sign-in" if the entry carries auth headers (an API key) OR declares
    # an oauth/api_key auth mode. bool(headers) alone would miss the OAuth
    # servers (notion/github/linear ship without headers), so we OR in `auth`.
    needs_auth = bool(s.get("headers")) or auth not in ("", "none")
    return {
        "name": s.get("name", ""),
        "transport": s.get("transport", "http"),
        "url": s.get("url", ""),
        "enabled": bool(s.get("enabled")),
        "needs_auth": needs_auth,
        "auth": auth or "none",
        "note": s.get("note", ""),
        "connected": False,  # filled in below for enabled servers
    }


def _mcp_connect():
    """Load skills/mcp_connect.py by path (skills/ isn't an importable package)."""
    import importlib.util
    from codec_config import SKILLS_DIR
    path = os.path.join(SKILLS_DIR, "mcp_connect.py")
    spec = importlib.util.spec_from_file_location("mcp_connect_rt", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@router.get("/api/mcp/servers")
async def mcp_servers():
    """List configured external MCP servers for the Connector tab, including
    whether each ENABLED one is actually signed in (`connected`). Disabled
    servers are never connected, so we skip the token lookup for them."""
    import asyncio
    servers = [_public_view(s) for s in _read_servers()
               if isinstance(s, dict) and s.get("name")]

    def _fill_connected():
        mc = _mcp_connect()
        for v in servers:
            if v["enabled"]:
                try:
                    v["connected"] = bool(mc.is_connected(v["name"]))
                except Exception:
                    v["connected"] = False

    try:
        await asyncio.to_thread(_fill_connected)
    except Exception:
        pass  # never fail the listing over a token probe

    return {"servers": servers, "count": len(servers)}


@router.post("/api/mcp/servers/{name}/toggle")
async def mcp_toggle(name: str, request: Request):
    """Flip ONLY the ``enabled`` flag of one server.

    The request body's ``enabled`` is the sole input consumed — no other field
    (url, headers, transport, …) is ever copied into the stored JSON, so this
    endpoint can't be used to rewrite a connector's target or inject secrets.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    enabled = bool(body.get("enabled"))
    target = str(name or "").strip().lower()

    # Pre-check (unlocked read) so an unknown name returns 404-style WITHOUT
    # rewriting the file. The authoritative match happens again under the lock.
    if not any(str(s.get("name", "")).strip().lower() == target
               for s in _read_servers() if isinstance(s, dict)):
        return {"ok": False, "error": f"no MCP server named '{name}'"}

    matched = {"found": False}

    def _mutate(data):
        if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
            return data
        for s in data["servers"]:
            if isinstance(s, dict) and str(s.get("name", "")).strip().lower() == target:
                s["enabled"] = enabled  # ← the ONLY field this endpoint writes
                matched["found"] = True
                break
        return data

    read_modify_write(MCP_SERVERS_PATH, _mutate)

    if not matched["found"]:
        # Raced away between the pre-check and the lock — treat as not found.
        return {"ok": False, "error": f"no MCP server named '{name}'"}

    try:
        from codec_audit import log_event
        log_event(
            "mcp_server_toggled", "codec-dashboard",
            f"MCP connector '{target}' {'enabled' if enabled else 'disabled'} via Connector tab",
            extra={"server": target, "enabled": enabled},
            outcome="ok", level="info",
        )
    except Exception:
        pass

    return {"ok": True, "name": target, "enabled": enabled}


@router.post("/api/mcp/servers/{name}/signin")
async def mcp_signin(name: str):
    """Trigger the OAuth sign-in for one connector. fastmcp opens the user's
    browser to the server's authorize page, runs a localhost callback, and caches
    the token — so this can block for a while (the user has to authorize). Loads
    skills/mcp_connect.py by path (skills/ isn't an importable package)."""
    import asyncio
    try:
        msg = await asyncio.to_thread(lambda: _mcp_connect().signin_server(name))
        # signin_server never raises — it returns an operator-readable message
        # for every failure mode (no such server, DCR unsupported, timeout).
        # Only the success path starts with "Signed in to".
        ok = msg.lower().startswith("signed in to")
        return {"ok": ok, "message": msg}
    except Exception as e:
        return {"ok": False, "message": f"Sign-in failed: {e}"}


@router.post("/api/mcp/servers/{name}/disconnect")
async def mcp_disconnect(name: str):
    """Forget the stored OAuth token for one connector — the card returns to
    'Sign in'. Clears tokens + client info + expiry via fastmcp's own adapter."""
    import asyncio
    try:
        msg = await asyncio.to_thread(lambda: _mcp_connect().disconnect_server(name))
        ok = msg.lower().startswith("disconnected")
        if ok:
            try:
                from codec_audit import log_event
                log_event("mcp_server_disconnected", "codec-dashboard",
                          f"MCP connector '{name}' token cleared via Connector tab",
                          extra={"server": name}, outcome="ok", level="info")
            except Exception:
                pass
        return {"ok": ok, "message": msg}
    except Exception as e:
        return {"ok": False, "message": f"Disconnect failed: {e}"}
