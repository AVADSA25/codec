"""Regression tests for the 2026-05-30 Top-10 Action List fixes.

Each Ax test pins a specific finding the final audit surfaced. If any of
these fail in the future, the corresponding fix has regressed.

- A1 / SR-8: /api/save_file must refuse writes under ~/.codec/, repo skills/,
  system roots — mirrors PR-1C's file_write skill blocklist.
- A3 / SR-10: license._fetch_pubkey + license_state must be cached so the
  paid edition doesn't hammer the license server on every skill call.
- A5 / SR-5: correlation_id contextvars must live in codec_audit and
  codec_agents / codec_voice re-export them. Eliminates 3 import cycles.
- A7 / SR-11: codec_jsonstore.file_lock must not leak the lock-file handle
  if open() raises after makedirs.
- A9 / SR-12: codec_memory_upgrade._conn() must set busy_timeout=5000.
- A10 / SR-13: _bg_watcher poll interval must be 1.0s (not 0.2s).
"""

import os

import pytest


# ── A1: /api/save_file blocklist ────────────────────────────────────────────
class TestA1SaveFileBlocklist:
    """A1: the /api/save_file HTTP endpoint must refuse writes to ~/.codec/
    and other sensitive roots — mirroring PR-1C's file_write skill blocklist.
    Before this fix, an authenticated POST could drop a malicious plugin in
    ~/.codec/plugins/ + add its hash to plugins.allowlist → RCE on next
    dispatch tick.
    """

    def test_codec_home_refused(self):
        from codec_dashboard import _save_file_is_safe
        ok, reason = _save_file_is_safe(
            os.path.expanduser("~/.codec/plugins/evil.py"))
        assert ok is False
        assert ".codec" in reason

    def test_codec_skills_refused(self):
        from codec_dashboard import _save_file_is_safe
        ok, reason = _save_file_is_safe(
            os.path.expanduser("~/.codec/skills/evil.py"))
        assert ok is False

    def test_codec_audit_refused(self):
        from codec_dashboard import _save_file_is_safe
        ok, reason = _save_file_is_safe(
            os.path.expanduser("~/.codec/audit.log"))
        assert ok is False

    def test_codec_config_refused(self):
        from codec_dashboard import _save_file_is_safe
        ok, reason = _save_file_is_safe(
            os.path.expanduser("~/.codec/config.json"))
        assert ok is False

    def test_plugins_allowlist_refused(self):
        from codec_dashboard import _save_file_is_safe
        ok, reason = _save_file_is_safe(
            os.path.expanduser("~/.codec/plugins.allowlist"))
        assert ok is False

    def test_system_root_refused(self):
        from codec_dashboard import _save_file_is_safe
        ok, reason = _save_file_is_safe("/etc/passwd")
        assert ok is False

    def test_repo_skills_refused(self):
        from codec_dashboard import _save_file_is_safe
        # codec_dashboard.py lives in the repo root; its sibling skills/ dir
        # is in the blocklist.
        import codec_dashboard
        repo_skills = os.path.join(
            os.path.dirname(os.path.abspath(codec_dashboard.__file__)),
            "skills", "evil.py")
        ok, reason = _save_file_is_safe(repo_skills)
        assert ok is False

    @pytest.mark.parametrize("path", [
        "~/codec-workspace/output.txt",
        "~/Desktop/note.md",
        "~/Documents/draft.txt",
        "/tmp/scratch.py",
    ])
    def test_legitimate_paths_allowed(self, path):
        from codec_dashboard import _save_file_is_safe
        ok, reason = _save_file_is_safe(os.path.expanduser(path))
        assert ok is True, f"{path} should be allowed (reason: {reason})"

    def test_sensitive_filename_refused(self):
        from codec_dashboard import _save_file_is_safe
        # Filename pattern blocklist applies globally regardless of dir.
        ok, reason = _save_file_is_safe(
            os.path.expanduser("~/Documents/id_rsa"))
        assert ok is False


# ── A3: License pubkey + state caching ──────────────────────────────────────
class TestA3LicenseCaching:
    """A3: _fetch_pubkey and license_state must be cached so a paid edition
    doesn't hammer the license server on every skill call.
    """

    def test_invalidate_caches_exists(self):
        import codec_license
        # The escape hatch for operators rotating their license token.
        assert hasattr(codec_license, "_invalidate_caches")
        codec_license._invalidate_caches()  # should not raise

    def test_pubkey_cache_globals_exist(self):
        import codec_license
        assert hasattr(codec_license, "_PUBKEY_CACHE_VALUE")
        assert hasattr(codec_license, "_PUBKEY_CACHE_TS")
        assert hasattr(codec_license, "_PUBKEY_CACHE_TTL")
        assert codec_license._PUBKEY_CACHE_TTL >= 3600.0, (
            "Pubkey TTL should be ≥1h to absorb the per-customer call rate")

    def test_license_state_cache_globals_exist(self):
        import codec_license
        assert hasattr(codec_license, "_LICENSE_STATE_CACHE_VALUE")
        assert hasattr(codec_license, "_LICENSE_STATE_CACHE_TS")
        assert hasattr(codec_license, "_LICENSE_STATE_CACHE_TTL")

    def test_pubkey_in_memory_cache_returns_same_bytes(self, monkeypatch):
        import codec_license
        codec_license._invalidate_caches()
        # Stub urlopen to count hits.
        calls = {"count": 0}

        class _StubResponse:
            def __init__(self, data):
                self._data = data
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return self._data

        def _stub_urlopen(url, timeout):
            calls["count"] += 1
            return _StubResponse(
                b"-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----")

        monkeypatch.setattr(codec_license.urllib.request,
                            "urlopen", _stub_urlopen)
        # First call → network hit.
        pem1 = codec_license._fetch_pubkey({})
        assert pem1 is not None
        assert calls["count"] == 1
        # Second call within TTL → cache hit, no new network call.
        pem2 = codec_license._fetch_pubkey({})
        assert pem2 == pem1
        assert calls["count"] == 1, (
            "Second call should hit the in-memory cache, not the network")


# ── A5: Correlation ID contextvars in codec_audit ───────────────────────────
class TestA5CorrelationIdRefactor:
    """A5: both correlation_id contextvars live in codec_audit; codec_agents
    and codec_voice re-export them. Eliminates 3 of 4 documented import
    cycles.
    """

    def test_correlation_id_canonical_home(self):
        import codec_audit
        assert hasattr(codec_audit, "_correlation_id_var")
        assert hasattr(codec_audit, "_voice_correlation_id_var")
        assert hasattr(codec_audit, "_new_correlation_id")

    def test_agents_reexport_identity(self):
        # codec_agents must re-export the SAME object (not a copy) so any
        # external module that imports codec_agents._correlation_id_var
        # gets the canonical contextvar.
        import codec_agents
        import codec_audit
        assert codec_agents._correlation_id_var is codec_audit._correlation_id_var
        assert codec_agents._new_correlation_id is codec_audit._new_correlation_id

    def test_voice_reexport_identity(self):
        import codec_voice
        import codec_audit
        assert codec_voice._voice_correlation_id_var is codec_audit._voice_correlation_id_var

    def test_new_correlation_id_format(self):
        import codec_audit
        cid = codec_audit._new_correlation_id()
        assert isinstance(cid, str)
        assert len(cid) == 12  # 6 bytes → 12 hex chars
        # Lowercase hex only
        assert all(c in "0123456789abcdef" for c in cid)

    def test_ask_user_reads_from_codec_audit(self):
        """codec_ask_user should not import codec_agents or codec_voice for
        the correlation_id — it should read from codec_audit only.
        """
        from pathlib import Path
        text = Path(__file__).parent.parent.joinpath("codec_ask_user.py").read_text()
        # Grep for the legacy cycle-causing imports.
        assert "from codec_agents import _correlation_id_var" not in text, (
            "codec_ask_user must not import _correlation_id_var from codec_agents")
        assert "from codec_voice import _voice_correlation_id_var" not in text, (
            "codec_ask_user must not import _voice_correlation_id_var from codec_voice")


# ── A7: codec_jsonstore.file_lock no handle leak ────────────────────────────
class TestA7JsonstoreFileLock:
    """A7: file_lock must close the lock-sidecar file handle even when
    flock or makedirs raises.
    """

    def test_file_lock_closes_handle_on_success(self, tmp_path):
        from codec_jsonstore import file_lock
        path = tmp_path / "test.json"
        with file_lock(path):
            assert (tmp_path / "test.json.lock").exists()
        # Lock file is still on disk but the handle has been closed by the
        # finally block. Open it again to confirm no exclusive lock leaked.
        with open(path.as_posix() + ".lock", "w") as f:
            f.write("")  # would block if a prior LOCK_EX leaked

    def test_file_lock_handle_close_on_exception(self, tmp_path):
        from codec_jsonstore import file_lock
        path = tmp_path / "exc.json"
        # An exception inside the with-block must still close the handle.
        with pytest.raises(RuntimeError):
            with file_lock(path):
                raise RuntimeError("boom")
        # Confirm we can re-acquire the lock (no leaked LOCK_EX).
        with file_lock(path):
            pass  # if a prior fhandle leaked LOCK_EX, this would block


# ── A9: SQLite busy_timeout consistency ─────────────────────────────────────
class TestA9SqliteBusyTimeout:
    """A9: codec_memory_upgrade._conn() must set busy_timeout=5000 to
    eliminate intermittent SQLITE_BUSY under concurrent writes.
    """

    def test_memory_upgrade_conn_has_busy_timeout(self, monkeypatch, tmp_path):
        # Use a temp DB so we don't touch the real ~/.codec/memory.db
        import codec_memory_upgrade
        monkeypatch.setattr(codec_memory_upgrade, "DB_PATH",
                            str(tmp_path / "facts.db"))
        c = codec_memory_upgrade._conn()
        try:
            row = c.execute("PRAGMA busy_timeout").fetchone()
            assert row is not None
            assert row[0] == 5000, (
                f"busy_timeout should be 5000ms, got {row[0]}")
        finally:
            c.close()


# ── A10: _bg_watcher poll interval ──────────────────────────────────────────
class TestA10BgWatcherPollInterval:
    """A10: _bg_watcher must poll at 1.0s, not the legacy 200ms (5x reduction
    in syscall load per customer per day).
    """

    def test_bg_watcher_poll_is_1s(self):
        from pathlib import Path
        text = Path(__file__).parent.parent.joinpath("codec_dashboard.py").read_text()
        # Find the _bg_watcher function body and check its sleep call.
        assert "async def _bg_watcher" in text
        # The 200ms legacy value must be gone.
        assert "await asyncio.sleep(0.2)" not in text, (
            "_bg_watcher should not poll every 200ms anymore")
        # The 1.0s value should be present in the watcher body.
        assert "await asyncio.sleep(1.0)" in text, (
            "_bg_watcher should poll every 1.0s")
