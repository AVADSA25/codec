"""Pilot PP-3 — navigation must reject non-http(s) schemes (file:, javascript:, data:)
and internal/loopback/link-local/private hosts (SSRF: cloud metadata, the dashboard,
the local LLM, the real Chrome CDP). Closes audit P-4.

Reference: docs/PP3-SSRF-GUARD-DESIGN.md.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # ~/codec on sys.path

from pilot.pilot_chrome import validate_navigation_url  # noqa: E402


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "chrome://settings",
    "about:config",                           # broad about: stays blocked (exact-match only)
    "about:settings",
    "http://127.0.0.1:8090/api/agents",      # the CODEC dashboard
    "http://localhost:8083/v1/chat",          # the local LLM
    "http://169.254.169.254/latest/meta-data",  # cloud metadata
    "http://10.0.0.5/internal",
    "http://192.168.1.73:8090",
    "http://[::1]:9223/json",                 # the Pilot CDP socket over loopback v6
    "ftp://example.com/x",
    "",
])
def test_blocked_urls_rejected(url):
    with pytest.raises(ValueError):
        validate_navigation_url(url)


@pytest.mark.parametrize("url", [
    "https://example.com",
    "https://news.ycombinator.com/news",
    "http://example.com:8080/path?q=1",
    "https://sub.domain.example.org/a/b",
    "about:blank",                            # canonical empty page — no host/network/file
    "  about:blank  ",                        # tolerant of surrounding whitespace
    "ABOUT:BLANK",                            # case-insensitive exact match
])
def test_public_urls_allowed(url):
    # Must not raise — returns the URL (or a normalized form).
    assert validate_navigation_url(url)
