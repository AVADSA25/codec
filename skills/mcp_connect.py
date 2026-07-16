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
import logging
import os
import re

log = logging.getLogger("codec")

CONFIG_PATH = os.path.expanduser("~/.codec/mcp_servers.json")

# A curated menu of popular public MCP servers, seeded on first run. Most need
# OAuth or an API key, so they ship DISABLED with a note — the operator fills in
# `url`/`headers` and flips `enabled`. This is the "add all main public MCP"
# starter set; enabling one is a one-line edit.
_SEED = {
    "servers": [
        {"name": "notion", "transport": "http", "url": "https://mcp.notion.com/mcp",
         "enabled": False, "auth": "oauth", "note": "Notion — pages, databases"},
        # api_key, NOT oauth: GitHub's MCP server publishes no OAuth metadata
        # (/.well-known/oauth-authorization-server → 404), so registration 404s
        # and the browser never opens. Advertising a Sign in button that cannot
        # possibly work is worse than no button — this shows "needs an API key".
        {"name": "github", "transport": "http", "url": "https://api.githubcopilot.com/mcp/",
         "enabled": False, "auth": "api_key",
         "headers": {"Authorization": "Bearer <GITHUB_TOKEN>"},
         "note": "GitHub — repos, issues, PRs (needs a personal access token)"},
        {"name": "linear", "transport": "sse", "url": "https://mcp.linear.app/sse",
         "enabled": False, "auth": "oauth", "note": "Linear — issues, projects"},
        {"name": "stripe", "transport": "http", "url": "https://mcp.stripe.com",
         "enabled": False, "auth": "api_key",
         "headers": {"Authorization": "Bearer <STRIPE_KEY>"}, "note": "Stripe — payments (read)"},
        {"name": "hugging-face", "transport": "http", "url": "https://huggingface.co/mcp",
         "enabled": False, "auth": "none", "note": "Hugging Face — models, datasets, papers"},
        # Open endpoints — no account, no sign-in, useful on day one. Each was
        # probed live and returned its tool list unauthenticated.
        {"name": "deepwiki", "transport": "http", "url": "https://mcp.deepwiki.com/mcp",
         "enabled": False, "auth": "none",
         "note": "DeepWiki — ask questions about any public GitHub repo"},
        {"name": "context7", "transport": "http", "url": "https://mcp.context7.com/mcp",
         "enabled": False, "auth": "none",
         "note": "Context7 — up-to-date docs for any library"},
        {"name": "microsoft-learn", "transport": "http", "url": "https://learn.microsoft.com/api/mcp",
         "enabled": False, "auth": "none",
         "note": "Microsoft Learn — official Microsoft & Azure docs"},
        {"name": "sentry", "transport": "http", "url": "https://mcp.sentry.dev/mcp",
         "enabled": False, "auth": "oauth", "note": "Sentry — errors, issues, traces"},
        {"name": "asana", "transport": "http", "url": "https://mcp.asana.com/v2/mcp",
         "enabled": False, "auth": "oauth", "note": "Asana — tasks, projects"},
        {"name": "atlassian", "transport": "http", "url": "https://mcp.atlassian.com/v1/mcp/authv2",
         "enabled": False, "auth": "oauth", "note": "Atlassian — Jira & Confluence"},
        {"name": "cloudflare", "transport": "http", "url": "https://mcp.cloudflare.com/mcp",
         "enabled": False, "auth": "oauth", "note": "Cloudflare — DNS, Workers, deployments"},
        {"name": "vercel", "transport": "http", "url": "https://mcp.vercel.com",
         "enabled": False, "auth": "oauth", "note": "Vercel — projects, deployments"},
        {"name": "intercom", "transport": "http", "url": "https://mcp.intercom.com/mcp",
         "enabled": False, "auth": "oauth", "note": "Intercom — conversations, contacts"},
    ]
}


def _merge_seed(cfg: dict) -> tuple[dict, bool]:
    """Add servers introduced since this config was written.

    The seed was only ever applied when the file did NOT exist, so anyone with
    an existing ~/.codec/mcp_servers.json could never receive a newly shipped
    connector — the list silently froze on the day it was created.

    Merge is additive and by name: the user's own entries, their enabled flags,
    and their headers are never touched. Only genuinely new names are appended,
    always disabled.
    """
    servers = cfg.get("servers")
    if not isinstance(servers, list):
        return cfg, False
    have = {str(s.get("name", "")).strip().lower() for s in servers if isinstance(s, dict)}
    added = [dict(s) for s in _SEED["servers"]
             if str(s.get("name", "")).strip().lower() not in have]
    if not added:
        return cfg, False
    cfg["servers"] = servers + added
    log.info("mcp_connect: added %d new connector(s): %s",
             len(added), ", ".join(s["name"] for s in added))
    return cfg, True


def _repair_bad_defaults(cfg: dict) -> tuple[dict, bool]:
    """Correct a default WE shipped wrong, without touching the user's choices.

    We seeded github as auth:"oauth", but its MCP server publishes no OAuth
    metadata — sign-in can never succeed. _merge_seed is additive by name, so
    existing configs keep the broken entry forever and keep offering a Sign in
    button that cannot work.

    Narrow on purpose: only github, only while it still looks like our untouched
    default (auth == "oauth" and no headers of their own). If the user has
    edited it, theirs wins and we leave it alone.
    """
    changed = False
    for s in cfg.get("servers", []):
        if not isinstance(s, dict):
            continue
        if (str(s.get("name", "")).strip().lower() == "github"
                and s.get("auth") == "oauth"
                and not s.get("headers")):
            s["auth"] = "api_key"
            s["headers"] = {"Authorization": "Bearer <GITHUB_TOKEN>"}
            s["note"] = "GitHub — repos, issues, PRs (needs a personal access token)"
            changed = True
            log.info("mcp_connect: github repaired oauth→api_key "
                     "(its MCP server has no OAuth metadata)")
    return cfg, changed


def _load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(_SEED, fh, indent=2)
        return dict(_SEED)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return dict(_SEED)

    cfg, added = _merge_seed(cfg)
    cfg, repaired = _repair_bad_defaults(cfg)
    changed = added or repaired
    if changed:
        try:
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=2)
            os.replace(tmp, CONFIG_PATH)   # atomic: never leave a torn config
        except OSError as e:
            log.warning("mcp_connect: couldn't persist new connectors: %s", e)
    return cfg


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


# ── OAuth token persistence ──────────────────────────────────────────────────
# fastmcp's OAuth defaults to IN-MEMORY token storage, so every dashboard restart
# lost the sign-in and nothing could tell whether a connector was connected. We
# hand it a Keychain-backed store instead (same secret tier as CODEC's other
# secrets), which makes sign-in durable AND makes `connected` observable.
_TOKEN_SERVICE = "ai.avadigital.codec.mcp_tokens"


def _token_store():
    """Persistent AsyncKeyValue for OAuth tokens. macOS Keychain first; a 0600
    on-disk store is the fallback (headless/CI, or Keychain unavailable)."""
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # store is flagged 'unstable' upstream
            from key_value.aio.stores.keyring import KeyringStore
            return KeyringStore(service_name=_TOKEN_SERVICE)
    except Exception as e:
        log.warning("mcp_connect: Keychain token store unavailable (%s) — using disk", e)
        from key_value.aio.stores.disk import DiskStore
        d = os.path.expanduser("~/.codec/mcp_tokens")
        os.makedirs(d, mode=0o700, exist_ok=True)
        return DiskStore(directory=d)


def _token_adapter(url: str):
    """fastmcp's own token adapter over our persistent store — gives us
    get_tokens() (→ connected?) and clear() (→ disconnect) without guessing at
    its key scheme."""
    from fastmcp.client.auth.oauth import TokenStorageAdapter
    return TokenStorageAdapter(async_key_value=_token_store(), server_url=url)


def is_connected(name: str) -> bool:
    """True if this server has a stored OAuth token. Servers that need no auth
    are 'connected' as soon as they're enabled; api_key servers count as
    connected once their header is configured."""
    server = _find_server(name)
    if not server:
        return False
    auth = str(server.get("auth") or "").strip().lower()
    if auth in ("", "none"):
        return True
    if auth == "api_key":
        return bool(server.get("headers"))
    url = server.get("url")
    if not url:
        return False
    try:
        return _run_async(_token_adapter(url).get_tokens()) is not None
    except Exception as e:
        log.debug("mcp_connect: connected-check failed for %s: %s", name, e)
        return False


def disconnect_server(name: str) -> str:
    """Forget the stored OAuth token for one server (clears tokens + client info
    + expiry via fastmcp's own adapter). The card returns to 'Sign in'."""
    server = _find_server(name)
    if not server:
        return f"No MCP server named '{name}'."
    url = server.get("url")
    if not url:
        return f"server '{name}' has no url"
    try:
        _run_async(_token_adapter(url).clear())
        return f"Disconnected from {server.get('name', name)}."
    except Exception as e:
        return f"Couldn't disconnect {name}: {e}"


def _client_for(server: dict):
    """Build a fastmcp.Client for a server entry (no connection yet)."""
    from fastmcp import Client  # local import: keeps skill scan cheap
    transport = server.get("transport", "http")
    headers = server.get("headers") or None
    if transport in ("http", "sse"):
        url = server.get("url")
        if not url:
            raise ValueError(f"server '{server.get('name')}' has no url")
        # OAuth servers (Notion, GitHub, Linear, Atlassian, …): use fastmcp's
        # built-in OAuth — it discovers the auth server from the MCP endpoint,
        # opens the browser for the user to authorize, runs a localhost callback,
        # and caches the token so later connects are silent. Without this, hitting
        # https://mcp.notion.com/mcp just returns {"error":"invalid_token"}.
        if server.get("auth") == "oauth":
            try:
                from fastmcp.client.auth import OAuth
                # token_storage → Keychain-backed, so the sign-in survives a
                # dashboard restart instead of dying with the process.
                #
                # token_endpoint_auth_method="none" registers CODEC as a PUBLIC
                # client (PKCE only, no client_secret). Without it Notion issues
                # a client_secret_basic client, and the mcp library then sends
                # BOTH an Authorization: Basic header AND client_id in the body —
                # which Notion rejects outright:
                #   400 {"error":"invalid_request","error_description":
                #        "Client must not use multiple authentication methods"}
                # so the browser approval succeeded and the token exchange died.
                # A public client is also simply correct here: a local desktop
                # app cannot keep a secret, and PKCE (S256) is what secures it.
                return Client(url, auth=OAuth(
                    mcp_url=url,
                    token_storage=_token_store(),
                    additional_client_metadata={"token_endpoint_auth_method": "none"},
                ))
            except Exception as e:  # OAuth unavailable → fall through to plain
                import logging
                logging.getLogger("codec").warning(
                    "mcp_connect: OAuth unavailable for '%s' (%s) — trying plain",
                    server.get("name"), e)
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


def signin_server(name: str) -> str:
    """Trigger the OAuth sign-in for one server: open a client (fastmcp runs the
    OAuth browser flow + localhost callback + token cache) and list its tools.
    Uses a longer timeout than the normal 60s so the user has time to authorize
    in the browser. Public — called by the Connector tab's "Sign in" button."""
    target = str(name or "").strip().lower()
    server = next((s for s in _servers()
                   if str(s.get("name", "")).strip().lower() == target), None)
    if not server:
        return f"No MCP server named '{name}'."

    # Start every sign-in from a clean slate. A previous attempt can leave a
    # registered-client record behind WITHOUT a usable token (Notion's DCR
    # returns 201, then the token exchange fails) — and that stale record is
    # reused forever after, so the same failure repeats no matter how many times
    # the user clicks Sign in. Only safe because we already know we have no
    # token: is_connected() gates this.
    url = server.get("url")
    if url and not is_connected(name):
        try:
            _run_async(_token_adapter(url).clear())
        except Exception as e:
            log.debug("mcp_connect: pre-signin clear failed for %s: %s", name, e)

    def _target():
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_alist_tools(server))
        finally:
            loop.close()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            tools = ex.submit(_target).result(timeout=180)
    except concurrent.futures.TimeoutError:
        return (f"Sign-in to {name} timed out after 3 minutes — the browser "
                f"authorization was never completed. Click Sign in and approve "
                f"the request in the window that opens.")
    except Exception as e:
        return _signin_error_message(name, server, e)
    return f"Signed in to {name} — {len(tools)} tool(s) available. Drive it by voice/chat: \"list tools on {name}\"."


def _signin_error_message(name: str, server: dict, exc: Exception) -> str:
    """Turn a raw OAuth stack trace into something the operator can act on.

    The failure that motivated this: GitHub's MCP server publishes no OAuth
    metadata at all, so fastmcp's Dynamic Client Registration call 404s and the
    flow dies BEFORE a browser ever opens. The user saw a Sign in button do
    literally nothing. Name the cause and the fix instead."""
    detail = f"{type(exc).__name__}: {exc}"
    log.warning("mcp_connect: sign-in to %s failed — %s", name, detail)
    low = detail.lower()
    label = server.get("name", name)

    if "registration" in low and "404" in low:
        return (f"{label} doesn't support automatic app registration, so CODEC "
                f"can't sign in through the browser — the sign-in fails before a "
                f"window can open. This server needs a personal access token "
                f"instead: add it under \"headers\" for \"{name}\" in "
                f"{CONFIG_PATH} (e.g. {{\"Authorization\": \"Bearer <token>\"}}) "
                f"and set \"auth\": \"api_key\".")
    if "registration" in low:
        return (f"{label} rejected CODEC's app registration, so the browser "
                f"sign-in can't start. Details: {detail[:180]}")
    if "timeout" in low or "timed out" in low:
        return (f"{label} didn't respond in time. Check the URL in {CONFIG_PATH} "
                f"and that you're online.")
    return f"Sign-in to {label} failed — {detail[:220]}"


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
