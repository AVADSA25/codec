"""Fix #6: guard the fastmcp floor ↔ code-import consistency.

The deployed python3.13 was found running fastmcp 3.1.1 while requirements.txt
declares >=3.3.1 — and an in-place upgrade to 3.3.1 left a broken install state
on that interpreter. In a CLEAN env the code imports fine on 3.3.1, so the fix
is a clean reinstall (not a code port). This test runs in CI (where fastmcp is
installed from the requirements floor) and would have caught both the version
drift and any future fastmcp API move that breaks codec_mcp / codec_oauth.
"""


def test_fastmcp_meets_safe_floor():
    import importlib.metadata as md

    raw = md.version("fastmcp")
    parts = []
    for chunk in raw.split(".")[:3]:
        num = "".join(c for c in chunk if c.isdigit())
        parts.append(int(num) if num else 0)
    assert tuple(parts) >= (3, 3, 1), f"fastmcp {raw} is below the CVE-safe floor 3.3.1"


def test_codec_mcp_modules_import_on_declared_fastmcp():
    # The exact import paths codec_mcp + codec_oauth_provider depend on.
    from fastmcp import FastMCP  # noqa: F401
    from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider  # noqa: F401

    # And the real modules load against the installed fastmcp.
    import codec_oauth_provider  # noqa: F401
    import codec_mcp  # noqa: F401
