"""CODEC MCP Server — Exposes all CODEC skills as MCP tools

Uses SkillRegistry for lazy loading: skill metadata is parsed via AST
at startup so MCP tool listings work immediately, but the actual module
import only happens when a tool is first invoked.
"""
from fastmcp import FastMCP
import os, sys, json, logging, time

log = logging.getLogger("codec_mcp")

# --- Input validation constants ---
MCP_MAX_TASK_LENGTH = 5_000
MCP_MAX_CONTEXT_LENGTH = 10_000


def _validate_mcp_input(tool_name: str, task: str, context: str = "") -> str | None:
    """Validate MCP tool call inputs and log the call.

    Returns an error message string if validation fails, or None if inputs are valid.
    Every call is audit-logged regardless of validation outcome.
    """
    # Audit log every call
    log.info(
        "MCP tool call: tool=%s task_len=%s context_len=%s ts=%s",
        tool_name,
        len(task) if isinstance(task, str) else "INVALID",
        len(context) if isinstance(context, str) else "INVALID",
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    # Type checks
    if not isinstance(task, str):
        return f"[MCP] Validation error: 'task' must be a string, got {type(task).__name__}"
    if not isinstance(context, str):
        return f"[MCP] Validation error: 'context' must be a string, got {type(context).__name__}"

    # Length checks
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

from codec_config import MCP_DEFAULT_ALLOW, MCP_ALLOWED_TOOLS, SKILLS_DIR
if SKILLS_DIR not in sys.path:
    sys.path.insert(0, SKILLS_DIR)
from codec_skill_registry import SkillRegistry

mcp = FastMCP("CODEC", instructions="Voice-controlled computer agent with 50+ skills")

# Compatibility shim: expose _tools as a dict-like object for introspection
class _ToolsProxy:
    """Proxy that makes len(mcp._tools) work across FastMCP versions."""
    def __init__(self, server):
        self._server = server
    def __len__(self):
        return len([k for k in self._server._local_provider._components if k.startswith("tool:")])
    def __iter__(self):
        return iter([k for k in self._server._local_provider._components if k.startswith("tool:")])

mcp._tools = _ToolsProxy(mcp)

# Global registry for MCP skill tools
_mcp_registry = SkillRegistry(SKILLS_DIR)


def load_skill_tools():
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
                print(f"[MCP] Skip {name}: not in mcp_allowed_tools (opt-in mode)")
                continue

        skill_desc = meta.get("SKILL_DESCRIPTION", f"CODEC skill: {name}")

        # Create a closure with lazy loading
        def make_tool(registry, sname, sdesc):
            def tool_fn(task: str, context: str = "") -> str:
                """Execute this CODEC skill with the given task"""
                err = _validate_mcp_input(sname, task, context)
                if err is not None:
                    return err
                try:
                    mod = registry.load(sname)
                    if mod is None or not hasattr(mod, "run"):
                        return f"Skill '{sname}' could not be loaded."
                    return mod.run(task, context)
                except Exception as e:
                    _audit(sname, task, f"ERROR: {type(e).__name__}")
                    return f"Skill '{sname}' failed: {type(e).__name__}: {str(e)[:200]}"
            tool_fn.__name__ = sname
            tool_fn.__doc__ = sdesc
            return tool_fn

        mcp.tool()(make_tool(_mcp_registry, skill_name, skill_desc))


# Also add memory search as a tool
@mcp.tool()
def search_memory(query: str, limit: int = 10) -> str:
    """Search CODEC's conversation memory using FTS5 full-text search"""
    err = _validate_mcp_input("search_memory", query)
    if err is not None:
        return err
    from codec_memory import CodecMemory
    mem = CodecMemory()
    results = mem.search(query, limit)
    return json.dumps(results, indent=2)

@mcp.tool()
def get_recent_memory(days: int = 7) -> str:
    """Get recent conversations from CODEC memory"""
    log.info(
        "MCP tool call: tool=get_recent_memory days=%s ts=%s",
        days,
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    from codec_memory import CodecMemory
    mem = CodecMemory()
    results = mem.search_recent(days=days, limit=20)
    return json.dumps(results, indent=2)

# Load all skills as tools (metadata only — modules loaded on demand)
load_skill_tools()

if __name__ == "__main__":
    print(f"[MCP] CODEC MCP Server starting with {len(mcp._tools)} tools")
    mcp.run(transport="stdio")
