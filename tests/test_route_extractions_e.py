"""E-series route extraction regression tests.

Pins:
  - E1 / SR-47: /api/update/check + /api/update/download → routes/update.py
  - E2 / SR-48: /api/status, /api/health, /health, /manifest.json, /metrics → routes/health.py
  - E4 / SR-49: /api/upload, /api/upload_image, /api/save_file + B1 helpers → routes/upload.py
"""
from pathlib import Path

import pytest


def _registered_paths():
    from codec_dashboard import app
    return {route.path for route in app.routes if hasattr(route, "path")}


REPO = Path(__file__).resolve().parent.parent


class TestE1Update:
    @pytest.mark.parametrize("path", [
        "/api/update/check",
        "/api/update/download",
    ])
    def test_endpoint_registered(self, path):
        assert path in _registered_paths()


class TestE2Health:
    @pytest.mark.parametrize("path", [
        "/api/health",
        "/health",
        "/api/status",
        "/manifest.json",
        "/metrics",
    ])
    def test_endpoint_registered(self, path):
        assert path in _registered_paths()

    def test_health_response_model_exported(self):
        from routes.health import HealthResponse
        assert HealthResponse.__name__ == "HealthResponse"


class TestE4Upload:
    @pytest.mark.parametrize("path", [
        "/api/upload",
        "/api/upload_image",
        "/api/save_file",
    ])
    def test_endpoint_registered(self, path):
        assert path in _registered_paths()

    def test_b1_helpers_live_in_routes(self):
        text = (REPO / "routes" / "upload.py").read_text()
        # B1 / SR-16: fence-marker helper
        assert "def _fence_user_document" in text
        # A1 / SR-8: path safety helper mirroring file_write blocklist
        assert "def _save_file_is_safe" in text
        assert "_SAVE_FILE_BLOCKED_ROOTS_CACHE" in text
        # B1 / SR-15: size cap
        assert "_UPLOAD_MAX_BYTES = 50 * 1024 * 1024" in text


# ── Smoke: codec_dashboard.py keeps shrinking ──────────────────────────────
def test_dashboard_loc_below_2700():
    """After C1..C5 + D1..D5 + E1+E2+E4, codec_dashboard.py is at ~2,530
    LOC. Floor 2,700 leaves headroom for the chat handler + remaining
    /api/command/vision/cdp/web_search endpoints that haven't been
    extracted yet (E5/E6 deferred)."""
    lines = (REPO / "codec_dashboard.py").read_text().count("\n")
    assert lines < 2700, f"codec_dashboard.py still has {lines} lines"
