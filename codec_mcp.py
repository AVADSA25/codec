"""CODEC MCP Server — Exposes all CODEC skills as MCP tools"""
from fastmcp import FastMCP
import importlib, os, sys, json

SKILLS_DIR = os.path.expanduser("~/.codec/skills")
sys.path.insert(0, SKILLS_DIR)

# Load MCP gating config
sys.path.insert(0, os.path.dirname(__file__))
from codec_config import MCP_DEFAULT_ALLOW, MCP_ALLOWED_TOOLS

mcp = FastMCP("CODEC", instructions="Voice-controlled computer agent with 40+ skills")

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

def load_skill_tools():
    """Auto-load all skills as MCP tools"""
    for fname in sorted(os.listdir(SKILLS_DIR)):
        if not fname.endswith('.py') or fname.startswith('_'):
            continue
        name = fname[:-3]
        try:
            mod = importlib.import_module(name)
            if hasattr(mod, 'run') and hasattr(mod, 'SKILL_DESCRIPTION'):
                # Per-skill opt-out always wins
                if getattr(mod, 'SKILL_MCP_EXPOSE', None) is False:
                    continue
                skill_name_check = getattr(mod, 'SKILL_NAME', name)
                # Determine whether this skill is allowed via MCP
                if MCP_DEFAULT_ALLOW:
                    # Opt-out mode: expose unless the skill explicitly sets SKILL_MCP_EXPOSE = False (handled above)
                    pass
                else:
                    # Opt-in mode (default): only expose if explicitly allowed or skill sets SKILL_MCP_EXPOSE = True
                    if getattr(mod, 'SKILL_MCP_EXPOSE', None) is True:
                        pass  # skill explicitly opted in
                    elif skill_name_check in MCP_ALLOWED_TOOLS or name in MCP_ALLOWED_TOOLS:
                        pass  # listed in config allowlist
                    else:
                        print(f"[MCP] Skip {name}: not in mcp_allowed_tools (opt-in mode)")
                        continue
                skill_name = getattr(mod, 'SKILL_NAME', name)
                skill_desc = getattr(mod, 'SKILL_DESCRIPTION', f"CODEC skill: {name}")

                # Create a closure to capture the module
                def make_tool(m):
                    def tool_fn(task: str, context: str = "") -> str:
                        """Execute this CODEC skill with the given task"""
                        return m.run(task, context)
                    tool_fn.__name__ = skill_name
                    tool_fn.__doc__ = skill_desc
                    return tool_fn

                mcp.tool()(make_tool(mod))
        except Exception as e:
            print(f"[MCP] Skip {name}: {e}")

# Also add memory search as a tool
@mcp.tool()
def search_memory(query: str, limit: int = 10) -> str:
    """Search CODEC's conversation memory using FTS5 full-text search"""
    sys.path.insert(0, os.path.expanduser("~/codec-repo"))
    from codec_memory import CodecMemory
    mem = CodecMemory()
    results = mem.search(query, limit)
    return json.dumps(results, indent=2)

@mcp.tool()
def get_recent_memory(days: int = 7) -> str:
    """Get recent conversations from CODEC memory"""
    sys.path.insert(0, os.path.expanduser("~/codec-repo"))
    from codec_memory import CodecMemory
    mem = CodecMemory()
    results = mem.search_recent(days=days, limit=20)
    return json.dumps(results, indent=2)

# Load all skills as tools
load_skill_tools()

if __name__ == "__main__":
    print(f"[MCP] CODEC MCP Server starting with {len(mcp._tools)} tools")
    mcp.run(transport="stdio")
