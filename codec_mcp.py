"""CODEC MCP Server — Exposes all CODEC skills as MCP tools

Uses SkillRegistry for lazy loading: skill metadata is parsed via AST
at startup so MCP tool listings work immediately, but the actual module
import only happens when a tool is first invoked.
"""
from fastmcp import FastMCP
import os, sys, json, logging, time, asyncio, inspect

log = logging.getLogger("codec_mcp")

# Per-tool timeout (seconds). Prevents one hung skill from blocking the server.
SKILL_TIMEOUT_SEC = int(os.environ.get("CODEC_SKILL_TIMEOUT", "30"))

from codec_audit import audit as _audit

# --- Input validation constants ---
MCP_MAX_TASK_LENGTH = 5_000
MCP_MAX_CONTEXT_LENGTH = 10_000


def _validate_mcp_input(tool_name: str, task: str, context: str = "") -> str | None:
    """Validate MCP tool call inputs. Returns error string or None."""
    if not isinstance(task, str):
        return f"[MCP] Validation error: 'task' must be a string, got {type(task).__name__}"
    if not isinstance(context, str):
        return f"[MCP] Validation error: 'context' must be a string, got {type(context).__name__}"
    if len(task) > MCP_MAX_TASK_LENGTH:
        return (
            f"[MCP] Validation error: 'task' exceeds max length "
            f"({len(task)} > {MCP_MAX_TASK_LENGTH})"
        )
    if len(context) > MCP_MAX_CONTEXT_LENGTH:
        return (
            f"[MCP] Validation error: 'context' exceeds max length "
            f"({len(context)} > {MCP_MAX_CONTEXT_LENGTH})"
        )
    return None


# Consolidate sys.path setup (done once, not scattered)
_REPO_DIR = os.path.dirname(os.path.abspath(__file__))
if _REPO_DIR not in sys.path:
    sys.path.insert(0, _REPO_DIR)

from codec_config import MCP_DEFAULT_ALLOW, MCP_ALLOWED_TOOLS, MCP_BLOCKED_TOOLS, SKILLS_DIR
if SKILLS_DIR not in sys.path:
    sys.path.insert(0, SKILLS_DIR)
from codec_skill_registry import SkillRegistry

# Compatibility shim: expose _tools as a dict-like object for introspection
class _ToolsProxy:
    """Proxy that makes len(mcp._tools) work across FastMCP versions."""
    def __init__(self, server):
        self._server = server
    def __len__(self):
        return len([k for k in self._server._local_provider._components if k.startswith("tool:")])
    def __iter__(self):
        return iter([k for k in self._server._local_provider._components if k.startswith("tool:")])


# Global registry for MCP skill tools
_mcp_registry = SkillRegistry(SKILLS_DIR)


def build_mcp(auth=None):
    """Build and fully configure a FastMCP server (skills + memory tools).

    Args:
        auth: optional FastMCP AuthProvider. When None, MCP runs unauthenticated
              at the protocol layer (suitable for stdio with client-side trust).

    Returns:
        FastMCP instance with all allowed skill tools and memory tools registered.
    """
    m = FastMCP(
        "CODEC",
        instructions="Voice-controlled computer agent with 50+ skills",
        auth=auth,
    )
    m._tools = _ToolsProxy(m)
    _load_skill_tools_into(m)
    _register_memory_tools(m)
    return m


def _load_skill_tools_into(mcp):
    """Register all allowed skills as MCP tools using lazy loading.

    Metadata is extracted via AST (no module import). The actual import
    is deferred to the first time each tool is called.
    """
    _mcp_registry.scan()

    for name in _mcp_registry.names():
        meta = _mcp_registry.get_meta(name)
        if meta is None:
            continue

        # Per-skill opt-out always wins
        mcp_expose = meta.get("SKILL_MCP_EXPOSE", None)
        if mcp_expose is False:
            continue

        skill_name = meta.get("SKILL_NAME", name)

        # Hard blocklist — skills that arbitrary-execute code or could damage system
        if skill_name in MCP_BLOCKED_TOOLS or name in MCP_BLOCKED_TOOLS:
            print(f"[MCP] Block {name}: in mcp_blocked_tools", file=sys.stderr)
            continue

        # Sanitize tool name to MCP spec (A-Z a-z 0-9 _ - .)
        # `registry_key` preserves the ORIGINAL SKILL_NAME for registry.load()
        # lookups; `skill_name` becomes the sanitized MCP-facing name.
        import re as _re
        registry_key = skill_name  # unsanitized — registry._paths is keyed by this
        safe_name = _re.sub(r'[^A-Za-z0-9_.-]', '_', skill_name).strip('_')
        if safe_name != skill_name:
            print(f"[MCP] Sanitize tool name '{skill_name}' -> '{safe_name}'", file=sys.stderr)
            skill_name = safe_name

        # Determine whether this skill is allowed via MCP
        if MCP_DEFAULT_ALLOW:
            # Opt-out mode: expose unless the skill explicitly sets SKILL_MCP_EXPOSE = False (handled above)
            pass
        else:
            # Opt-in mode (default): only expose if explicitly allowed or skill sets SKILL_MCP_EXPOSE = True
            if mcp_expose is True:
                pass  # skill explicitly opted in
            elif skill_name in MCP_ALLOWED_TOOLS or name in MCP_ALLOWED_TOOLS:
                pass  # listed in config allowlist
            else:
                print(f"[MCP] Skip {name}: not in mcp_allowed_tools (opt-in mode)", file=sys.stderr)
                continue

        skill_desc = meta.get("SKILL_DESCRIPTION", f"CODEC skill: {name}")

        # Create a closure with lazy loading, timeout, and audit
        def make_tool(registry, sname, rkey, sdesc):
            def tool_fn(task: str, context: str = "") -> str:
                """Execute this CODEC skill with the given task"""
                t0 = time.time()
                tlen = len(task) if isinstance(task, str) else 0
                clen = len(context) if isinstance(context, str) else 0

                err = _validate_mcp_input(sname, task, context)
                if err is not None:
                    _audit(sname, task_len=tlen, context_len=clen,
                           duration_ms=(time.time()-t0)*1000,
                           outcome="validation", error_type="ValidationError")
                    return err

                def _run():
                    mod = registry.load(rkey)
                    if mod is None or not hasattr(mod, "run"):
                        return None, "load_failed"
                    try:
                        return mod.run(task, context), None
                    except Exception as e:
                        return None, f"{type(e).__name__}: {str(e)[:200]}"

                # Run with timeout in a worker thread (most skills are sync)
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(_run)
                    try:
                        result, errmsg = fut.result(timeout=SKILL_TIMEOUT_SEC)
                    except concurrent.futures.TimeoutError:
                        _audit(sname, task_len=tlen, context_len=clen,
                               duration_ms=(time.time()-t0)*1000,
                               outcome="timeout", error_type="Timeout")
                        return f"Skill '{sname}' timed out after {SKILL_TIMEOUT_SEC}s."

                dur_ms = (time.time()-t0)*1000
                if errmsg == "load_failed":
                    _audit(sname, task_len=tlen, context_len=clen,
                           duration_ms=dur_ms, outcome="error", error_type="LoadFailed")
                    return f"Skill '{sname}' could not be loaded."
                if errmsg:
                    _audit(sname, task_len=tlen, context_len=clen,
                           duration_ms=dur_ms, outcome="error",
                           error_type=errmsg.split(":")[0])
                    return f"Skill '{sname}' failed: {errmsg}"
                _audit(sname, task_len=tlen, context_len=clen,
                       duration_ms=dur_ms, outcome="ok")
                return result
            tool_fn.__name__ = sname
            tool_fn.__doc__ = sdesc
            return tool_fn

        mcp.tool()(make_tool(_mcp_registry, skill_name, registry_key, skill_desc))
    # scan once at module load is fine; keep here for callers who pass fresh mcp


def _register_memory_tools(mcp):
    @mcp.tool()
    def search_memory(query: str, limit: int = 10) -> str:
        """Search CODEC's conversation memory using FTS5 full-text search"""
        t0 = time.time()
        err = _validate_mcp_input("search_memory", query)
        if err is not None:
            _audit("search_memory", task_len=len(query or ""),
                   duration_ms=(time.time()-t0)*1000, outcome="validation")
            return err
        try:
            from codec_memory import CodecMemory
            mem = CodecMemory()
            results = mem.search(query, limit)
            _audit("search_memory", task_len=len(query),
                   duration_ms=(time.time()-t0)*1000, outcome="ok")
            return json.dumps(results, indent=2)
        except Exception as e:
            _audit("search_memory", task_len=len(query or ""),
                   duration_ms=(time.time()-t0)*1000, outcome="error",
                   error_type=type(e).__name__)
            return f"search_memory failed: {type(e).__name__}: {e}"

    @mcp.tool()
    def get_recent_memory(days: int = 7) -> str:
        """Get recent conversations from CODEC memory"""
        t0 = time.time()
        try:
            from codec_memory import CodecMemory
            mem = CodecMemory()
            results = mem.search_recent(days=days, limit=20)
            _audit("get_recent_memory", duration_ms=(time.time()-t0)*1000,
                   outcome="ok", extra={"days": days})
            return json.dumps(results, indent=2)
        except Exception as e:
            _audit("get_recent_memory", duration_ms=(time.time()-t0)*1000,
                   outcome="error", error_type=type(e).__name__)
            return f"get_recent_memory failed: {type(e).__name__}: {e}"


# Default instance for stdio transport (no auth — client-side trust via approval UI)
mcp = build_mcp()

# Back-compat alias
def load_skill_tools():
    _load_skill_tools_into(mcp)


if __name__ == "__main__":
    print(f"[MCP] CODEC MCP Server starting with {len(mcp._tools)} tools", file=sys.stderr)
    mcp.run(transport="stdio")
