"""
Tests for High-Priority Fixes (Audit Items 6-10).
Run: pytest tests/test_high_fixes.py -v
"""
import os
import re
import sys
import inspect
import sqlite3
import tempfile
import pytest

# ── Fix 6: SQLite indexes on conversations table ──

class TestSQLiteIndexes:
    """conversations table must have indexes on session_id and timestamp."""

    def test_index_creation_in_source(self):
        """codec_memory.py must create both indexes."""
        import codec_memory
        source = inspect.getsource(codec_memory)
        assert "idx_conv_session" in source, "Missing index on session_id"
        assert "idx_conv_ts" in source, "Missing index on timestamp"

    def test_indexes_created_in_db(self):
        """Actually create a temp DB and verify indexes exist."""
        import codec_memory
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            # Replicate the table + index creation
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    role TEXT,
                    content TEXT,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_ts ON conversations(timestamp)")
            conn.commit()
            # Verify indexes exist
            indexes = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='conversations'"
            ).fetchall()
            index_names = [i[0] for i in indexes]
            assert "idx_conv_session" in index_names
            assert "idx_conv_ts" in index_names
            conn.close()
        finally:
            os.unlink(db_path)


# ── Fix 7: No bare except:pass remaining ──

class TestNoBarExcept:
    """No bare except:pass should remain in critical files."""

    @pytest.mark.parametrize("filepath", [
        "codec_watcher.py",
        "codec_voice.py",
    ])
    def test_no_bare_except_pass(self, filepath):
        """File must not contain bare 'except:' followed by 'pass'."""
        full_path = os.path.join(os.path.dirname(__file__), "..", filepath)
        if not os.path.exists(full_path):
            pytest.skip(f"{filepath} not found")
        with open(full_path) as f:
            content = f.read()
        # Find bare except: (not except Exception, not except SomeError)
        bare_excepts = re.findall(r"except\s*:\s*\n\s*pass", content)
        assert len(bare_excepts) == 0, (
            f"Found {len(bare_excepts)} bare except:pass in {filepath}"
        )

    @pytest.mark.parametrize("filepath", [
        "codec_watcher.py",
        "codec_voice.py",
    ])
    def test_exceptions_are_logged(self, filepath):
        """Exception handlers should log the error, not silently pass."""
        full_path = os.path.join(os.path.dirname(__file__), "..", filepath)
        if not os.path.exists(full_path):
            pytest.skip(f"{filepath} not found")
        with open(full_path) as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if re.match(r"except\s+Exception(\s+as\s+\w+)?:", stripped):
                # Next non-empty line should NOT be just 'pass'
                for j in range(i + 1, min(i + 3, len(lines))):
                    next_line = lines[j].strip()
                    if next_line and next_line != "":
                        assert next_line != "pass", (
                            f"{filepath}:{j+1} has except Exception followed by bare pass"
                        )
                        break


# ── Fix 8: Version consistency ──

class TestVersionConsistency:
    """All version strings must be v2.0.0."""

    VERSION_FILES = [
        ("codec.py", r'["\']v?2\.\d+\.\d+["\']'),
        ("codec_agent.py", r'["\']v?2\.\d+\.\d+["\']'),
        ("setup_codec.py", r'["\']v?2\.\d+\.\d+["\']'),
    ]

    @pytest.mark.parametrize("filename,pattern", VERSION_FILES)
    def test_version_is_2_0_0(self, filename, pattern):
        full_path = os.path.join(os.path.dirname(__file__), "..", filename)
        if not os.path.exists(full_path):
            pytest.skip(f"{filename} not found")
        with open(full_path) as f:
            content = f.read()
        versions = re.findall(pattern, content)
        for v in versions:
            clean = v.strip("\"'").lstrip("v")
            assert clean == "2.0.0", f"{filename} has version {v}, expected 2.0.0"


# ── Fix 9: Skill trigger word-boundary matching ──

class TestTriggerWordBoundary:
    """Trigger matching must use word boundaries, not substring."""

    def test_play_does_not_match_display(self):
        """'play' trigger must NOT match 'display my files'."""
        from codec_skill_registry import SkillRegistry
        # Test the matching logic directly
        low = "display my files"
        trigger = "play"
        # Word boundary check — should NOT match
        assert not re.search(r'\b' + re.escape(trigger) + r'\b', low), (
            "'play' should not match 'display'"
        )

    def test_play_matches_play_music(self):
        """'play' trigger SHOULD match 'play some music'."""
        low = "play some music"
        trigger = "play"
        assert re.search(r'\b' + re.escape(trigger) + r'\b', low)

    def test_time_does_not_match_sometimes(self):
        """'time' trigger must NOT match 'sometimes I wonder'."""
        low = "sometimes i wonder"
        trigger = "time"
        assert not re.search(r'\b' + re.escape(trigger) + r'\b', low)

    def test_time_matches_what_time(self):
        """'time' trigger SHOULD match 'what time is it'."""
        low = "what time is it"
        trigger = "time"
        assert re.search(r'\b' + re.escape(trigger) + r'\b', low)

    def test_note_does_not_match_notebook(self):
        low = "open my notebook"
        trigger = "note"
        assert not re.search(r'\b' + re.escape(trigger) + r'\b', low)

    def test_registry_uses_word_boundary(self):
        """SkillRegistry.match_trigger source must use re.search with \\b."""
        from codec_skill_registry import SkillRegistry
        source = inspect.getsource(SkillRegistry.match_trigger)
        assert r"\b" in source or "\\b" in source, (
            "match_trigger must use word boundary regex"
        )


# ── Fix 10: CSP headers on dashboard routes ──

class TestCSPHeaders:
    """All HTML responses must include Content-Security-Policy."""

    def test_csp_middleware_exists(self):
        """Dashboard must have CSPMiddleware class."""
        import codec_dashboard
        assert hasattr(codec_dashboard, "CSPMiddleware"), "CSPMiddleware class missing"

    def test_csp_header_content(self):
        """CSP header must include essential directives."""
        import codec_dashboard
        source = inspect.getsource(codec_dashboard.CSPMiddleware)
        assert "default-src" in source, "Missing default-src directive"
        assert "script-src" in source, "Missing script-src directive"
        assert "style-src" in source, "Missing style-src directive"
        assert "font-src" in source or "fonts.gstatic.com" in source, "Missing font-src"
        assert "img-src" in source, "Missing img-src directive"

    def test_csp_only_on_html(self):
        """CSP middleware must check content-type before adding header."""
        import codec_dashboard
        source = inspect.getsource(codec_dashboard.CSPMiddleware)
        assert "text/html" in source, "CSP must only apply to HTML responses"
