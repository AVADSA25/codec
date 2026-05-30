"""D-series route extraction regression tests.

Pins:
  - D1 / SR-42: /api/qchat/*       → routes/qchat.py
  - D2 / SR-43: /api/vibe/*        → routes/vibe.py
  - D3 / SR-44: /api/schedules/*   → routes/schedules.py
  - D4 / SR-45: /api/prompts/*     → routes/prompts.py
  - D5 / SR-46: media (webcam x3,
                screenshot,
                clipboard x2)     → routes/media.py

Each phase pins (a) endpoints reachable through the FastAPI app and
(b) source-side invariants where relevant.
"""
from pathlib import Path

import pytest


def _registered_paths():
    from codec_dashboard import app
    return {route.path for route in app.routes if hasattr(route, "path")}


REPO = Path(__file__).resolve().parent.parent


# ── D1: qchat ──────────────────────────────────────────────────────────────
class TestD1Qchat:
    @pytest.mark.parametrize("path", [
        "/api/qchat/sessions",
        "/api/qchat/session/{sid}",
        "/api/qchat/save",
        "/api/qchat/search",
    ])
    def test_endpoint_registered(self, path):
        assert path in _registered_paths()

    def test_qchat_db_helper_lives_in_routes(self):
        text = (REPO / "routes" / "qchat.py").read_text()
        assert "def qchat_db" in text
        assert "QCHAT_DB = " in text


# ── D2: vibe ───────────────────────────────────────────────────────────────
class TestD2Vibe:
    @pytest.mark.parametrize("path", [
        "/api/vibe/sessions",
        "/api/vibe/session/{sid}",
        "/api/vibe/save",
    ])
    def test_endpoint_registered(self, path):
        assert path in _registered_paths()

    def test_vibe_db_helper_lives_in_routes(self):
        text = (REPO / "routes" / "vibe.py").read_text()
        assert "def vibe_db" in text
        assert "VIBE_DB = " in text


# ── D3: schedules ──────────────────────────────────────────────────────────
class TestD3Schedules:
    @pytest.mark.parametrize("path", [
        "/api/schedules",
        "/api/schedules/{sched_id}",
        "/api/schedules/{sched_id}/run",
        "/api/schedules/history",
    ])
    def test_endpoint_registered(self, path):
        assert path in _registered_paths()

    def test_atomic_write_on_update(self):
        """The update endpoint must use codec_jsonstore.atomic_write_json
        — re-audit medium fix vs racing the scheduler's read."""
        text = (REPO / "routes" / "schedules.py").read_text()
        assert "codec_jsonstore.atomic_write_json" in text


# ── D4: prompts ────────────────────────────────────────────────────────────
class TestD4Prompts:
    @pytest.mark.parametrize("path", [
        "/api/prompts",
        "/api/prompts/reset",
    ])
    def test_endpoint_registered(self, path):
        assert path in _registered_paths()

    def test_prompt_helpers_live_in_routes(self):
        text = (REPO / "routes" / "prompts.py").read_text()
        assert "def _load_prompt_overrides" in text
        assert "def _save_prompt_overrides" in text
        assert "def _get_all_prompts" in text

    def test_chat_completion_lazy_imports_helper(self):
        """codec_dashboard.chat_completion's _build_system_prompt helper
        must lazy-import _load_prompt_overrides from routes.prompts
        (avoids module-load-time cycle)."""
        text = (REPO / "codec_dashboard.py").read_text()
        assert "from routes.prompts import _load_prompt_overrides" in text


# ── D5: media ──────────────────────────────────────────────────────────────
class TestD5Media:
    @pytest.mark.parametrize("path", [
        "/api/webcam",
        "/api/webcam/stream",
        "/api/webcam/snapshot",
        "/api/screenshot",
        "/api/clipboard",
    ])
    def test_endpoint_registered(self, path):
        assert path in _registered_paths()


# ── Smoke: codec_dashboard.py keeps shrinking ─────────────────────────────
def test_dashboard_loc_below_3000():
    """After C1..C5 + D1..D5 + B6, codec_dashboard.py should be below
    3,000 LOC. Started this PR at 3,618; current target floor 3,000."""
    lines = (REPO / "codec_dashboard.py").read_text().count("\n")
    assert lines < 3000, f"codec_dashboard.py still has {lines} lines"
