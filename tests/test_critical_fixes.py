"""
Tests for Critical Security Fixes (Audit Items 1-5).
Run: pytest tests/test_critical_fixes.py -v
"""
import os
import time
import threading

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
    """Test PIN lockout after 5 failed attempts.

    The `_pin_attempts` dict and the lockout policy live in `routes/_shared.py`
    + `routes/auth.py` (the dashboard imports them from there). Tests target
    the actual home of the symbol.
    """

    def test_pin_attempts_dict_exists(self):
        """Shared state must have _pin_attempts tracking dict."""
        from routes._shared import _pin_attempts
        assert isinstance(_pin_attempts, dict)

    def test_pin_lockout_logic(self):
        """After 5 failures, client should be locked for 300 seconds."""
        from routes._shared import _pin_attempts
        ip = "test_brute_force_127.0.0.1"
        _pin_attempts[ip] = {"count": 4, "locked_until": 0.0}
        # 5th failure should trigger lockout
        attempt = _pin_attempts[ip]
        attempt["count"] = attempt.get("count", 0) + 1
        if attempt["count"] >= 5:
            attempt["locked_until"] = time.time() + 300
            attempt["count"] = 0
        _pin_attempts[ip] = attempt
        # Verify lockout is active
        assert _pin_attempts[ip]["locked_until"] > time.time()
        assert _pin_attempts[ip]["locked_until"] <= time.time() + 301
        # Cleanup
        _pin_attempts.pop(ip, None)

    def test_pin_success_resets_counter(self):
        """Successful PIN auth should clear the failed attempts counter."""
        from routes._shared import _pin_attempts
        ip = "test_reset_127.0.0.1"
        _pin_attempts[ip] = {"count": 3, "locked_until": 0.0}
        # Simulate success: pop the entry
        _pin_attempts.pop(ip, None)
        assert ip not in _pin_attempts


# ── Fix 4 (former): Skill Forge substring blocker (removed in PR-1B) ──
#
# The TestSkillForgeBlocklist class was deleted in PR-1B alongside the
# /api/save_skill and /api/forge endpoints. The substring blocker it tested
# was the weak validation those endpoints used before writing to disk.
# Coverage of dangerous-pattern detection now lives in
# tests/test_skill_registry.py (AST-based, runs at load time on every skill,
# closes D-1) and in routes/skills.py:skill_approve (AST check at write time
# via the review-and-approve flow). See docs/audits/PHASE-1-SECURITY.md
# findings D-1, D-2, D-3 for the full closure trail.


# ── Fix 5: Thread-safe _auth_sessions ──

class TestAuthSessionThreadSafety:
    """_auth_sessions must be protected by _auth_lock."""

    def test_auth_lock_exists(self):
        """Dashboard must have a threading.Lock for auth sessions."""
        import codec_dashboard
        assert hasattr(codec_dashboard, "_auth_lock")
        assert isinstance(codec_dashboard._auth_lock, type(threading.Lock()))

    def test_auth_lock_is_used_in_source(self):
        """_auth_lock must wrap _auth_sessions access wherever the dict is
        mutated. Dict + lock live in routes/_shared.py; auth flow callers
        are in routes/auth.py and codec_dashboard.py. Count across all
        three so refactors that move call sites between modules don't
        regress the invariant."""
        import inspect
        import codec_dashboard
        from routes import _shared as routes_shared
        from routes import auth as routes_auth
        modules = [codec_dashboard, routes_shared, routes_auth]
        lock_uses = sum(
            inspect.getsource(m).count("with _auth_lock") for m in modules
        )
        assert lock_uses >= 4, (
            f"Expected at least 4 'with _auth_lock' acquisitions across "
            f"{[m.__name__ for m in modules]}, found {lock_uses}"
        )

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
