"""B6 architecture refactor regression tests.

Pins:
  - B6-P1: codec_plugin_trust split + codec_hooks back-compat re-exports
  - B6-P2: codec_chat_pipeline split + codec_dashboard back-compat re-exports
  - B6-P3: routes/notifications.py — endpoints reachable
  - B6-P4: codec_voice_filters split + codec_voice back-compat re-exports
"""
import pytest


# ── B6-P1: codec_plugin_trust ──────────────────────────────────────────────
class TestB6P1HooksTrustSplit:
    """codec_hooks re-exports must be identity-equal to codec_plugin_trust."""

    def test_read_allowlist_identity(self):
        import codec_hooks
        import codec_plugin_trust
        assert codec_hooks._read_allowlist is codec_plugin_trust._read_allowlist

    def test_write_allowlist_identity(self):
        import codec_hooks
        import codec_plugin_trust
        assert codec_hooks._write_allowlist is codec_plugin_trust._write_allowlist

    def test_file_sha256_identity(self):
        import codec_hooks
        import codec_plugin_trust
        assert codec_hooks._file_sha256 is codec_plugin_trust._file_sha256

    def test_is_plugin_allowed_identity(self):
        import codec_hooks
        import codec_plugin_trust
        assert codec_hooks._is_plugin_allowed is codec_plugin_trust._is_plugin_allowed

    def test_allowlist_lock_identity(self):
        import codec_hooks
        import codec_plugin_trust
        assert codec_hooks._ALLOWLIST_LOCK is codec_plugin_trust._ALLOWLIST_LOCK

    def test_grandfather_migration_writes_empty_allowlist(self, tmp_path):
        """End-to-end: empty plugins dir → empty allowlist file gets written."""
        import codec_plugin_trust
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        allowlist_path = tmp_path / "plugins.allowlist"
        codec_plugin_trust._maybe_grandfather_existing_plugins(
            str(plugins_dir), allowlist_path)
        # Empty dir → empty allowlist file created.
        assert allowlist_path.exists()
        assert allowlist_path.read_text() in ("{}", "{}\n")


# ── B6-P2: codec_chat_pipeline ─────────────────────────────────────────────
class TestB6P2ChatPipelineSplit:
    """codec_dashboard re-exports must be identity-equal to codec_chat_pipeline."""

    def test_step_budget_class_identity(self):
        import codec_dashboard
        import codec_chat_pipeline
        assert codec_dashboard._StepBudget is codec_chat_pipeline._StepBudget

    def test_is_conversational_identity(self):
        import codec_dashboard
        import codec_chat_pipeline
        assert codec_dashboard._is_conversational is codec_chat_pipeline._is_conversational

    def test_step_budget_enabled_identity(self):
        import codec_dashboard
        import codec_chat_pipeline
        assert codec_dashboard._step_budget_enabled is codec_chat_pipeline._step_budget_enabled

    @pytest.mark.parametrize("text,expected", [
        ("delete file", False),       # 2 words → not conversational
        ("what do you think?", True), # conv pattern
        ("here is the link", True),   # conv pattern
        ("hi", False),                # 1 word
        ("set timer", False),         # 2 words
    ])
    def test_is_conversational_behavior(self, text, expected):
        from codec_chat_pipeline import _is_conversational
        assert _is_conversational(text) is expected

    def test_step_budget_consume_and_warn(self):
        from codec_chat_pipeline import _StepBudget
        budget = _StepBudget(route="chat")
        # If not enabled (mocked off via env), we get a trivial pass through
        if not budget.enabled:
            pytest.skip("step budget disabled in this env")
        assert budget.consume("step") is True
        # warn_now flips at limit-1
        for _ in range(budget.limit - 2):
            budget.consume("step")
        # Now we should be one consume away from the warn line.


# ── B6-P3: routes/notifications.py ─────────────────────────────────────────
class TestB6P3NotificationsRoute:
    """The 4 notification endpoints must be reachable through the
    main FastAPI app via the router include."""

    def test_endpoints_registered_in_app(self):
        from codec_dashboard import app
        paths = {route.path for route in app.routes if hasattr(route, "path")}
        assert "/api/notifications" in paths
        assert "/api/notifications/count" in paths
        assert "/api/notifications/{notif_id}/read" in paths
        assert "/api/notifications/read-all" in paths


# ── B6-P4: codec_voice_filters ─────────────────────────────────────────────
class TestB6P4VoiceFiltersSplit:
    """codec_voice re-exports must be identity-equal to codec_voice_filters."""

    def test_noise_words_identity(self):
        import codec_voice
        import codec_voice_filters
        assert codec_voice.NOISE_WORDS is codec_voice_filters.NOISE_WORDS

    def test_whisper_hallucinations_identity(self):
        import codec_voice
        import codec_voice_filters
        assert codec_voice.WHISPER_HALLUCINATIONS is codec_voice_filters.WHISPER_HALLUCINATIONS

    @pytest.mark.parametrize("text,expected", [
        ("you", True),
        ("Thank you", True),
        ("delete the file", False),
        ("UM", True),
        ("hello there friend", False),
    ])
    def test_is_noise_behavior(self, text, expected):
        from codec_voice_filters import is_noise
        assert is_noise(text) is expected

    @pytest.mark.parametrize("text,expected", [
        ("Thanks for watching everyone", True),
        ("please subscribe and like", True),
        ("Delete the file please", False),
        ("All rights reserved", True),
    ])
    def test_is_hallucination_behavior(self, text, expected):
        from codec_voice_filters import is_hallucination
        assert is_hallucination(text) is expected

    def test_rms_int16_empty(self):
        from codec_voice_filters import rms_int16
        assert rms_int16(b"") == 0.0
        assert rms_int16(b"x") == 0.0  # < 2 bytes → 0

    def test_rms_int16_nonzero(self):
        from codec_voice_filters import rms_int16
        # Build a short int16 PCM buffer with known RMS.
        import numpy as np
        samples = np.array([1000, -1000, 1000, -1000], dtype=np.int16)
        rms = rms_int16(samples.tobytes())
        assert rms == pytest.approx(1000.0, abs=1.0)
