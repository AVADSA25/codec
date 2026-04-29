"""Tests for codec_slash_commands.py

Covers: parser, registry, dispatch, individual command handlers.
Uses pytest. Run with: pytest -xvs tests/test_slash_commands.py
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

# Add project root to path so we can import codec_slash_commands
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

import codec_slash_commands as scm


# ── parse_slash() ──

class TestParser:
    def test_simple(self):
        assert scm.parse_slash("/help") == ("help", [])

    def test_with_args(self):
        assert scm.parse_slash("/skills enable weather") == ("skills", ["enable", "weather"])

    def test_quoted_args(self):
        assert scm.parse_slash('/skills info "web search"') == ("skills", ["info", "web search"])

    def test_leading_whitespace(self):
        assert scm.parse_slash("   /cost   ") == ("cost", [])

    def test_not_at_start(self):
        assert scm.parse_slash("hello /world") is None

    def test_url_passthrough(self):
        assert scm.parse_slash("http://example.com") is None

    def test_path_passthrough(self):
        assert scm.parse_slash("/Users/me/file.txt") is None

    def test_empty(self):
        assert scm.parse_slash("") is None

    def test_just_slash(self):
        assert scm.parse_slash("/") is None
        assert scm.parse_slash("/   ") is None

    def test_double_slash(self):
        assert scm.parse_slash("//") is None

    def test_backslash_escaped(self):
        # User wants to type /help literally without invoking it
        assert scm.parse_slash("\\/help") is None

    def test_case_insensitive_command(self):
        assert scm.parse_slash("/HELP")[0] == "help"
        assert scm.parse_slash("/Skills")[0] == "skills"

    def test_invalid_chars(self):
        assert scm.parse_slash("/foo!bar") is None
        assert scm.parse_slash("/foo bar") == ("foo", ["bar"])

    def test_unbalanced_quote_falls_back(self):
        # Should fall back to simple split rather than crash
        result = scm.parse_slash('/skills info "unclosed')
        assert result is not None
        assert result[0] == "skills"


# ── Registry / find_command() ──

class TestRegistry:
    def test_help_present(self):
        cmd = scm.find_command("help")
        assert cmd is not None
        assert cmd.name == "help"

    def test_alias(self):
        # /commands and /? are aliases for /help
        assert scm.find_command("commands").name == "help"
        assert scm.find_command("?").name == "help"

    def test_unknown(self):
        assert scm.find_command("totally_made_up_command") is None

    def test_all_commands_have_unique_names(self):
        names = [c.name for c in scm.SLASH_COMMANDS]
        assert len(names) == len(set(names)), "Duplicate command names"

    def test_all_aliases_unique(self):
        aliases = [a for c in scm.SLASH_COMMANDS for a in c.aliases]
        assert len(aliases) == len(set(aliases)), "Duplicate aliases"

    def test_no_alias_collides_with_name(self):
        names = {c.name for c in scm.SLASH_COMMANDS}
        for c in scm.SLASH_COMMANDS:
            for a in c.aliases:
                assert a not in names or a == c.name, \
                    f"alias '{a}' on /{c.name} collides with another command"


# ── dispatch() ──

class TestDispatch:
    def test_help_returns_markdown(self):
        out = scm.dispatch("help", [])
        assert "## CODEC Slash Commands" in out
        assert "/help" in out
        assert "/skills" in out

    def test_unknown_returns_friendly_error(self):
        out = scm.dispatch("totally_made_up", [])
        assert "Unknown" in out
        assert "/help" in out  # nudges toward help

    def test_handler_exception_caught(self):
        # Inject a broken command, ensure dispatch doesn't crash the dashboard
        def broken(args): raise RuntimeError("boom")
        scm.SLASH_COMMANDS.append(scm.SlashCommand(
            name="_test_broken", handler=broken, summary="test"
        ))
        try:
            out = scm.dispatch("_test_broken", [])
            assert "failed" in out.lower()
            assert "RuntimeError" in out
        finally:
            scm.SLASH_COMMANDS.pop()


# ── Individual handlers ──

class TestHandlers:
    def test_help_no_args(self):
        out = scm._cmd_help([])
        assert "Slash Commands" in out
        for c in scm.SLASH_COMMANDS:
            assert f"/{c.name}" in out

    def test_help_with_args_includes_hint(self):
        out = scm._cmd_help(["skills"])
        assert "help <command>" in out

    def test_skills_list_returns_markdown_table(self):
        out = scm._cmd_skills(["list"])
        # Either has skills or returns the unavailable warning
        assert ("Skills (" in out and "| Name |" in out) or "skill registry unavailable" in out

    def test_version_includes_python(self):
        out = scm._cmd_version([])
        assert "Python" in out
        assert "3." in out  # python version string

    def test_who_renders(self):
        out = scm._cmd_who([])
        assert "CODEC identity" in out

    def test_clear_returns_markdown(self):
        out = scm._cmd_clear([])
        assert "cleared" in out.lower()


# ── Integration: full round-trip from text → response ──

class TestRoundTrip:
    """parse → find → dispatch end-to-end."""

    def _roundtrip(self, line):
        parsed = scm.parse_slash(line)
        assert parsed is not None, f"failed to parse: {line!r}"
        return scm.dispatch(parsed[0], parsed[1])

    def test_help(self):
        assert "Slash Commands" in self._roundtrip("/help")

    def test_help_alias(self):
        assert "Slash Commands" in self._roundtrip("/?")
        assert "Slash Commands" in self._roundtrip("/commands")

    def test_version_alias(self):
        assert "Version" in self._roundtrip("/v")

    def test_cost_alias(self):
        # Should at least not crash; will return either spend or DB-missing message
        out = self._roundtrip("/spend")
        assert ("Today's spend" in out) or ("usage DB" in out)

    def test_who(self):
        assert "identity" in self._roundtrip("/who").lower()


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-xvs", __file__]))
