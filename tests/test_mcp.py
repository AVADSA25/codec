def test_mcp_import():
    from codec_mcp import mcp
    assert mcp is not None

def test_mcp_has_tools():
    from codec_mcp import mcp
    assert len(mcp._tools) > 0
