"""CODEC Skill: MCP Connect — make CODEC an MCP *client*.

CODEC already exposes its own skills as an MCP *server* (codec_mcp.py). This
skill is the other direction: it lets CODEC reach OUT to other MCP servers —
the same public connectors Claude can use (Notion, GitHub, Linear, …) — and
call their tools. That makes CODEC bidirectional: a server Claude plugs into,
and a client that plugs into everything else.

Uses `fastmcp.Client` (already a dependency — no new install). Servers are
declared in ``~/.codec/mcp_servers.json`` (seeded on first use). Each entry:

    {
      "name": "notion",
      "transport": "http",            # "http" | "sse" | "stdio"
      "url": "https://mcp.notion.com/mcp",   # for http/sse
      "command": "npx", "args": [...],       # for stdio
      "headers": {"Authorization": "Bearer …"},  # optional, for API-key servers
      "enabled": true
    }

Task grammar (robust, LLM- and human-friendly):
    "list mcp"                         → list configured servers
    "list tools on notion"            → list a server's tools
    "call notion <tool> {json args}"  → call a tool explicitly
    "<anything mentioning a server>"  → connect + list that server's tools so
                                         the caller can pick the next step
"""
SKILL_NAME = "mcp_connect"
SKILL_DESCRIPTION = (
    "Connect CODEC to external MCP servers (Notion, GitHub, Linear, …) and call "
    "their tools. 'list mcp' shows configured servers; 'list tools on <server>' "
    "lists a server's tools; 'call <server> <tool> {json}' runs one."
)
SKILL_TRIGGERS = ["mcp", "connect to", "list tools", "mcp server", "external tool"]
# Not exposed on CODEC's OWN outbound MCP server: we don't want a remote client
# chaining CODEC -> arbitrary third-party MCP tools unattended. Local voice/chat
# (the demo path) still routes here normally.
SKILL_MCP_EXPOSE = False

import concurrent.futures
import json
import os
import re

CONFIG_PATH = os.path.expanduser("~/.codec/mcp_servers.json")

# A curated menu of popular public MCP servers, seeded on first run. Most need
# OAuth or an API key, so they ship DISABLED with a note — the operator fills in
# `url`/`headers` and flips `enabled`. This is the "add all main public MCP"
# starter set; enabling one is a one-line edit.
_SEED = {
    "servers": [
        {"name": "notion", "transport": "http", "url": "https://mcp.notion.com/mcp",
         "enabled": False, "auth": "oauth", "note": "Notion — pages, databases"},
        {"name": "github", "transport": "http", "url": "https://api.githubcopilot.com/mcp/",
         "enabled": False, "auth": "oauth", "note": "GitHub — repos, issues, PRs"},
        {"name": "linear", "transport": "sse", "url": "https://mcp.linear.app/sse",
         "enabled": False, "auth": "oauth", "note": "Linear — issues, projects"},
        {"name": "stripe", "transport": "http", "url": "https://mcp.stripe.com",
         "enabled": False, "auth": "api_key",
         "headers": {"Authorization": "Bearer <STRIPE_KEY>"}, "note": "Stripe — payments (read)"},
        {"name": "hugging-face", "transport": "http", "url": "https://huggingface.co/mcp",
         "enabled": False, "auth": "none", "note": "Hugging Face — models, datasets, papers"},
    ]
}


def _load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(_SEED, fh, indent=2)
        return dict(_SEED)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return dict(_SEED)


def _servers() -> list[dict]:
    return _load_config().get("servers", [])


def _find_server(name: str) -> dict | None:
    name = (name or "").strip().lower()
    for s in _servers():
        if s.get("name", "").lower() == name:
            return s
    # loose contains-match so "notion workspace" finds "notion"
    for s in _servers():
        if s.get("name", "").lower() in name:
            return s
    return None


def _client_for(server: dict):
    """Build a fastmcp.Client for a server entry (no connection yet)."""
    from fastmcp import Client  # local import: keeps skill scan cheap
    transport = server.get("transport", "http")
    headers = server.get("headers") or None
    if transport in ("http", "sse"):
        url = server.get("url")
        if not url:
            raise ValueError(f"server '{server.get('name')}' has no url")
        # fastmcp infers HTTP vs SSE from the URL; headers carry API-key auth.
        try:
            return Client(url, headers=headers)
        except TypeError:  # older fastmcp signature without headers kwarg
            return Client(url)
    if transport == "stdio":
        cmd = server.get("command")
        if not cmd:
            raise ValueError(f"server '{server.get('name')}' has no command")
        return Client({"command": cmd, "args": server.get("args", [])})
    raise ValueError(f"unknown transport '{transport}'")


def _run_async(coro):
    """Run an async coroutine from CODEC's sync skill context. Always uses a
    fresh event loop on a worker thread so it's safe whether or not the caller
    already has a running loop (the dashboard does)."""
    def _target():
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_target).result(timeout=60)


async def _alist_tools(server: dict) -> list:
    async with _client_for(server) as client:
        return await client.list_tools()


async def _acall_tool(server: dict, tool: str, args: dict):
    async with _client_for(server) as client:
        return await client.call_tool(tool, args or {})


def _fmt_tools(tools) -> str:
    out = []
    for t in tools:
        name = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else str(t))
        desc = getattr(t, "description", None) or (t.get("description", "") if isinstance(t, dict) else "")
        out.append(f"  • {name}" + (f" — {desc[:80]}" if desc else ""))
    return "\n".join(out) if out else "  (no tools)"


def _list_servers() -> str:
    servers = _servers()
    if not servers:
        return "No MCP servers configured. Edit " + CONFIG_PATH
    lines = ["Configured MCP servers:"]
    for s in servers:
        state = "on" if s.get("enabled") else f"off ({s.get('auth', '?')})"
        lines.append(f"  • {s['name']} [{state}] — {s.get('note', s.get('url', ''))}")
    lines.append(f"\nEnable one by setting \"enabled\": true in {CONFIG_PATH}")
    return "\n".join(lines)


def run(task: str, context: str = "") -> str:
    t = (task or "").strip()
    low = t.lower()

    # 1. list configured servers
    if re.search(r"\b(list|show|which|what)\b.*\bmcp\b", low) or low in ("mcp", "mcp servers"):
        if "tools" not in low:
            return _list_servers()

    # 2. explicit call:  call <server> <tool> {json}
    m = re.match(r"(?:call|use)\s+(\S+)\s+(\S+)\s*(\{.*\})?\s*$", t, re.IGNORECASE | re.DOTALL)
    if m:
        server = _find_server(m.group(1))
        if not server:
            return f"No MCP server named '{m.group(1)}'. Try 'list mcp'."
        if not server.get("enabled"):
            return (f"MCP server '{server['name']}' is disabled. Enable it in "
                    f"{CONFIG_PATH} (auth: {server.get('auth', '?')}).")
        tool = m.group(2)
        try:
            args = json.loads(m.group(3)) if m.group(3) else {}
        except ValueError as e:
            return f"Could not parse tool arguments as JSON: {e}"
        try:
            res = _run_async(_acall_tool(server, tool, args))
        except Exception as e:  # noqa: BLE001 — surface any client/network error
            return f"MCP call failed ({server['name']}.{tool}): {type(e).__name__}: {e}"
        return f"[{server['name']}.{tool}] {getattr(res, 'data', res)}"

    # 3. list a server's tools:  "list tools on <server>" / mentions a server
    server = None
    mt = re.search(r"tools?\s+(?:on|for|from)\s+(\S+)", low)
    if mt:
        server = _find_server(mt.group(1))
    if server is None:
        # any server name mentioned in the task
        for s in _servers():
            if s.get("name", "").lower() in low:
                server = s
                break
    if server is None:
        return _list_servers()

    if not server.get("enabled"):
        return (f"MCP server '{server['name']}' is configured but disabled "
                f"(auth: {server.get('auth', '?')}). Enable it in {CONFIG_PATH}.")
    try:
        tools = _run_async(_alist_tools(server))
    except Exception as e:  # noqa: BLE001
        return f"Could not connect to MCP server '{server['name']}': {type(e).__name__}: {e}"
    return f"MCP server '{server['name']}' exposes:\n{_fmt_tools(tools)}"
