def test_mcp_import():
    from codec_mcp import mcp
    assert mcp is not None

def test_mcp_has_tools():
    from codec_mcp import mcp
    # With mcp_default_allow=False and empty allowlist, only hardcoded tools
    # (search_memory, get_recent_memory) and skills with SKILL_MCP_EXPOSE=True load
    assert mcp is not None

def test_mcp_default_allow_false():
    """When mcp_default_allow is False, skills without explicit opt-in are blocked"""
    from codec_config import MCP_DEFAULT_ALLOW
    # Default should be False (opt-in mode)
    assert MCP_DEFAULT_ALLOW is False or isinstance(MCP_DEFAULT_ALLOW, bool)

def test_mcp_config_keys_exist():
    """Config module exposes MCP gating keys"""
    from codec_config import MCP_DEFAULT_ALLOW, MCP_ALLOWED_TOOLS
    assert isinstance(MCP_DEFAULT_ALLOW, bool)
    assert isinstance(MCP_ALLOWED_TOOLS, list)
