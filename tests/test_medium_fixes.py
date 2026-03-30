"""
Tests for Medium-Priority Fixes (Audit Items 12, 13, 15).
Run: pytest tests/test_medium_fixes.py -v
"""
import inspect
import os
import re
import pytest


# ── Fix 12: Parallelize heartbeat checks ──

class TestHeartbeatParallel:
    """Health checks must run in parallel, not sequential."""

    def test_uses_thread_pool(self):
        """codec_heartbeat must use ThreadPoolExecutor."""
        import codec_heartbeat
        source = inspect.getsource(codec_heartbeat)
        assert "ThreadPoolExecutor" in source, "Missing ThreadPoolExecutor import/usage"

    def test_uses_as_completed(self):
        """Results gathered with as_completed for earliest-first reporting."""
        import codec_heartbeat
        source = inspect.getsource(codec_heartbeat)
        assert "as_completed" in source, "Missing as_completed for parallel result gathering"

    def test_check_one_service_helper(self):
        """_check_one_service must be a standalone function for pool.submit."""
        import codec_heartbeat
        assert hasattr(codec_heartbeat, "_check_one_service")
        assert callable(codec_heartbeat._check_one_service)

    def test_check_one_service_returns_tuple(self):
        """_check_one_service returns (name, status) tuple."""
        import codec_heartbeat
        # Test with a definitely-down service
        name, status = codec_heartbeat._check_one_service("TestService", "http://localhost:99999/")
        assert name == "TestService"
        assert "DOWN" in status

    def test_no_sequential_loop(self):
        """check_system_health should NOT have a sequential for-loop with requests.get inside."""
        import codec_heartbeat
        source = inspect.getsource(codec_heartbeat.check_system_health)
        # Should not have "requests.get" directly in check_system_health
        assert "requests.get" not in source, (
            "check_system_health still calls requests.get directly — should use pool"
        )

    def test_max_workers_matches_services(self):
        """ThreadPoolExecutor max_workers should scale with service count."""
        import codec_heartbeat
        source = inspect.getsource(codec_heartbeat.check_system_health)
        assert "max_workers" in source


# ── Fix 13: HTTP connection pooling ──

class TestConnectionPooling:
    """HTTP calls should reuse connections via pooled clients."""

    def test_sync_http_client_exists(self):
        """Module-level _sync_http client for synchronous calls."""
        import codec_agents
        assert hasattr(codec_agents, "_sync_http")
        import httpx
        assert isinstance(codec_agents._sync_http, httpx.Client)

    def test_async_http_client_exists(self):
        """Module-level _async_http client for async calls."""
        import codec_agents
        assert hasattr(codec_agents, "_async_http")
        import httpx
        assert isinstance(codec_agents._async_http, httpx.AsyncClient)

    def test_web_fetch_uses_pooled_client(self):
        """_web_fetch should use _sync_http, not create new client."""
        import codec_agents
        source = inspect.getsource(codec_agents._web_fetch)
        assert "_sync_http" in source, "_web_fetch should use pooled _sync_http client"
        assert "httpx.get(" not in source, "_web_fetch should not create ad-hoc httpx calls"
        assert "httpx.Client(" not in source, "_web_fetch should not create new Client per call"

    def test_agent_run_uses_pooled_client(self):
        """Agent.run should use _async_http, not create new AsyncClient."""
        import codec_agents
        source = inspect.getsource(codec_agents.Agent.run)
        assert "_async_http" in source, "Agent.run should use pooled _async_http client"
        assert "AsyncClient(" not in source, "Agent.run should not create new AsyncClient per call"

    def test_no_async_with_httpx(self):
        """No 'async with httpx.AsyncClient' pattern — use module-level pool."""
        import codec_agents
        source = inspect.getsource(codec_agents.Agent)
        assert "async with httpx" not in source, (
            "Agent still creates per-call AsyncClient — should use _async_http"
        )


# ── Fix 15: MCP documentation in README ──

class TestMCPDocumentation:
    """README must document MCP security configuration."""

    def _read_readme(self):
        path = os.path.join(os.path.dirname(__file__), "..", "README.md")
        with open(path) as f:
            return f.read()

    def test_skill_mcp_expose_documented(self):
        content = self._read_readme()
        assert "SKILL_MCP_EXPOSE" in content, "README missing SKILL_MCP_EXPOSE docs"

    def test_mcp_default_allow_documented(self):
        content = self._read_readme()
        assert "mcp_default_allow" in content, "README missing mcp_default_allow docs"

    def test_mcp_allowed_tools_documented(self):
        content = self._read_readme()
        assert "mcp_allowed_tools" in content, "README missing mcp_allowed_tools docs"

    def test_mcp_example_skill(self):
        """README should show an example skill with MCP exposure."""
        content = self._read_readme()
        assert "SKILL_MCP_EXPOSE = True" in content, "README missing example of exposing a skill"

    def test_mcp_config_table(self):
        """README should have a config table for MCP options."""
        content = self._read_readme()
        # Check for markdown table structure
        assert "| Option" in content or "| `mcp_default_allow`" in content, (
            "README missing MCP config table"
        )
