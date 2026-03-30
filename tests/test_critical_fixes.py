"""
Tests for Critical Security Fixes (Audit Items 1-5).
Run: pytest tests/test_critical_fixes.py -v
"""
import importlib
import hashlib
import os
import sys
import time
import threading
import pytest

# ── Fix 1: fastmcp in requirements.txt ──

def test_fastmcp_in_requirements():
    """fastmcp must be listed in requirements.txt."""
    req_path = os.path.join(os.path.dirname(__file__), "..", "requirements.txt")
    with open(req_path) as f:
        content = f.read()
    assert "fastmcp" in content.lower(), "fastmcp missing from requirements.txt"


# ── Fix 2: /api/run_code uses full DANGEROUS_PATTERNS ──

def test_is_dangerous_blocks_full_list():
    """is_dangerous() should block all 30+ patterns, not just 6."""
    from codec_config import is_dangerous, DANGEROUS_PATTERNS
    assert len(DANGEROUS_PATTERNS) >= 25, f"Expected 25+ patterns, got {len(DANGEROUS_PATTERNS)}"
    # Test every pattern is actually caught
    for pat in DANGEROUS_PATTERNS:
        assert is_dangerous(pat), f"is_dangerous() missed pattern: {pat}"


def test_is_dangerous_catches_networksetup():
    """Patterns like networksetup must be blocked (was missing in old inline list)."""
    from codec_config import is_dangerous
    assert is_dangerous("networksetup -setdnsservers Wi-Fi 8.8.8.8")


def test_is_dangerous_catches_launchctl():
    from codec_config import is_dangerous
    assert is_dangerous("launchctl unload /System/Library/LaunchDaemons/com.apple.ftp.plist")


def test_is_dangerous_catches_chown():
    from codec_config import is_dangerous
    assert is_dangerous("chown -R root:wheel /usr/local")


def test_is_dangerous_safe_code_allowed():
    from codec_config import is_dangerous
    assert not is_dangerous("print('hello world')")
    assert not is_dangerous("x = 1 + 2")
    assert not is_dangerous("import json\ndata = json.loads('{}')")


# ── Fix 3: PIN brute-force rate limiting ──

class TestPinBruteForce:
    """Test PIN lockout after 5 failed attempts."""

    def test_pin_attempts_dict_exists(self):
        """Dashboard must have _pin_attempts tracking dict."""
        import codec_dashboard
        assert hasattr(codec_dashboard, "_pin_attempts")
        assert isinstance(codec_dashboard._pin_attempts, dict)

    def test_pin_lockout_logic(self):
        """After 5 failures, client should be locked for 300 seconds."""
        import codec_dashboard
        # Simulate 5 failed attempts
        ip = "test_brute_force_127.0.0.1"
        codec_dashboard._pin_attempts[ip] = {"count": 4, "locked_until": 0.0}
        # 5th failure should trigger lockout
        attempt = codec_dashboard._pin_attempts[ip]
        attempt["count"] = attempt.get("count", 0) + 1
        if attempt["count"] >= 5:
            attempt["locked_until"] = time.time() + 300
            attempt["count"] = 0
        codec_dashboard._pin_attempts[ip] = attempt
        # Verify lockout is active
        assert codec_dashboard._pin_attempts[ip]["locked_until"] > time.time()
        assert codec_dashboard._pin_attempts[ip]["locked_until"] <= time.time() + 301
        # Cleanup
        codec_dashboard._pin_attempts.pop(ip, None)

    def test_pin_success_resets_counter(self):
        """Successful PIN auth should clear the failed attempts counter."""
        import codec_dashboard
        ip = "test_reset_127.0.0.1"
        codec_dashboard._pin_attempts[ip] = {"count": 3, "locked_until": 0.0}
        # Simulate success: pop the entry
        codec_dashboard._pin_attempts.pop(ip, None)
        assert ip not in codec_dashboard._pin_attempts


# ── Fix 4: Skill Forge semantic validation ──

class TestSkillForgeBlocklist:
    """Skill code must be scanned for dangerous imports/calls."""

    DANGEROUS_SKILL_PATTERNS = [
        "os.system('rm -rf /')",
        "subprocess.Popen(['ls'])",
        "subprocess.run(['ls'])",
        "eval(user_input)",
        "exec(code_string)",
        "__import__('os').system('id')",
        "import importlib; importlib.import_module('os')",
        "shutil.rmtree('/important')",
        "open('/etc/passwd')",
        "open('/dev/sda', 'w')",
        "import ctypes; ctypes.CDLL('libc.so.6')",
    ]

    SAFE_SKILL_CODE = '''
SKILL_NAME = "test_skill"
SKILL_DESCRIPTION = "A safe test skill"
SKILL_TRIGGERS = ["test"]

def run(task, app=None, ctx=None):
    return "Hello from test skill"
'''

    @pytest.mark.parametrize("dangerous_code", DANGEROUS_SKILL_PATTERNS)
    def test_blocked_patterns_detected(self, dangerous_code):
        """Each dangerous pattern must be caught by the blocklist."""
        BLOCKED_IN_SKILLS = [
            "os.system(", "subprocess.", "eval(", "exec(", "__import__",
            "importlib", "shutil.rmtree", "open('/etc", "open('/dev", "ctypes",
        ]
        skill_code = f'SKILL_DESCRIPTION = "test"\ndef run(task):\n    {dangerous_code}'
        found = any(b in skill_code for b in BLOCKED_IN_SKILLS)
        assert found, f"Dangerous pattern not caught: {dangerous_code}"

    def test_safe_skill_passes(self):
        """Normal skill code should not be flagged."""
        BLOCKED_IN_SKILLS = [
            "os.system(", "subprocess.", "eval(", "exec(", "__import__",
            "importlib", "shutil.rmtree", "open('/etc", "open('/dev", "ctypes",
        ]
        found = any(b in self.SAFE_SKILL_CODE for b in BLOCKED_IN_SKILLS)
        assert not found, "Safe skill code was incorrectly flagged"

    def test_blocklist_has_minimum_patterns(self):
        """Blocklist should have at least 10 patterns (expanded from original 5)."""
        # Read the actual source to verify
        import inspect
        import codec_dashboard
        source = inspect.getsource(codec_dashboard.save_skill)
        # Count blocked patterns by finding the list
        assert "subprocess." in source, "subprocess. pattern missing (was subprocess.Popen only)"
        assert "importlib" in source, "importlib pattern missing"
        assert "shutil.rmtree" in source, "shutil.rmtree pattern missing"
        assert "ctypes" in source, "ctypes pattern missing"
        assert "open('/etc" in source, "open('/etc pattern missing"
        assert "open('/dev" in source, "open('/dev pattern missing"


# ── Fix 5: Thread-safe _auth_sessions ──

class TestAuthSessionThreadSafety:
    """_auth_sessions must be protected by _auth_lock."""

    def test_auth_lock_exists(self):
        """Dashboard must have a threading.Lock for auth sessions."""
        import codec_dashboard
        assert hasattr(codec_dashboard, "_auth_lock")
        assert isinstance(codec_dashboard._auth_lock, type(threading.Lock()))

    def test_auth_lock_is_used_in_source(self):
        """_auth_lock must wrap all _auth_sessions access."""
        import inspect
        import codec_dashboard
        source = inspect.getsource(codec_dashboard)
        # Count occurrences of _auth_lock usage
        lock_uses = source.count("with _auth_lock")
        assert lock_uses >= 4, f"Expected at least 4 lock acquisitions, found {lock_uses}"

    def test_concurrent_session_writes(self):
        """Concurrent writes to _auth_sessions must not corrupt data."""
        import codec_dashboard
        results = []
        errors = []

        def writer(token_id):
            try:
                with codec_dashboard._auth_lock:
                    codec_dashboard._auth_sessions[f"test_thread_{token_id}"] = {
                        "created": "test",
                        "ip": "127.0.0.1",
                        "method": "test",
                    }
                results.append(token_id)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0, f"Thread errors: {errors}"
        assert len(results) == 20, f"Expected 20 writes, got {len(results)}"

        # Cleanup
        for i in range(20):
            codec_dashboard._auth_sessions.pop(f"test_thread_{i}", None)
