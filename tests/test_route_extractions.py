"""Route extraction regression tests (C1..C5).

Pins:
  - C1 / SR-36: /api/approvals/* → routes/approvals.py
  - C2 / SR-37: /api/heartbeat/* → routes/heartbeat.py
  - C3 / SR-38: /api/cortex/* → routes/cortex.py
  - C4 / SR-39: /api/audit, /api/audit/stream, /api/audit/stats → routes/audit.py
  - C5 / SR-40: /api/observer/buffer → routes/observer.py

Each block verifies (a) the endpoint is reachable through the FastAPI
app via the router include, and (b) the import surface lives in the
right module.
"""
import pytest


def _registered_paths():
    """Return the set of paths registered on the main FastAPI app."""
    from codec_dashboard import app
    return {route.path for route in app.routes if hasattr(route, "path")}


# ── C1: approvals ──────────────────────────────────────────────────────────
class TestC1Approvals:
    @pytest.mark.parametrize("path", [
        "/api/approvals",
        "/api/approvals/count",
        "/api/approvals/{approval_id}/allow",
        "/api/approvals/{approval_id}/deny",
    ])
    def test_endpoint_registered(self, path):
        assert path in _registered_paths()

    def test_module_imports_from_shared(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent / "routes" / "approvals.py").read_text()
        assert "from routes._shared import" in text
        assert "_pending_approvals" in text
        assert "_evict_expired_approvals" in text


# ── C2: heartbeat ──────────────────────────────────────────────────────────
class TestC2Heartbeat:
    @pytest.mark.parametrize("path", [
        "/api/heartbeat/config",
        "/api/heartbeat/alerts",
    ])
    def test_endpoint_registered(self, path):
        assert path in _registered_paths()

    def test_module_uses_config_path(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent / "routes" / "heartbeat.py").read_text()
        assert "CONFIG_PATH" in text


# ── C3: cortex ─────────────────────────────────────────────────────────────
class TestC3Cortex:
    @pytest.mark.parametrize("path", [
        "/api/cortex/health",
        "/api/cortex/skills",
        "/api/cortex/logs/{service}",
        "/api/cortex/restart/{service}",
    ])
    def test_endpoint_registered(self, path):
        assert path in _registered_paths()

    def test_cortex_uses_dispatch_registry(self):
        """A-4 invariant: cortex_skills reads from codec_dispatch.registry,
        not the legacy codec_core.loaded_skills."""
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent / "routes" / "cortex.py").read_text()
        assert "from codec_dispatch import registry" in text
        assert "from codec_core import loaded_skills" not in text


# ── C4: audit ──────────────────────────────────────────────────────────────
class TestC4Audit:
    @pytest.mark.parametrize("path", [
        "/api/audit",
        "/api/audit/stream",
        "/api/audit/stats",
    ])
    def test_endpoint_registered(self, path):
        assert path in _registered_paths()

    def test_module_reads_audit_log(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent / "routes" / "audit.py").read_text()
        assert "AUDIT_LOG" in text


# ── C5: observer ───────────────────────────────────────────────────────────
class TestC5Observer:
    def test_endpoint_registered(self):
        assert "/api/observer/buffer" in _registered_paths()

    def test_observer_emits_audit_event(self):
        """The privileged read must emit OBSERVER_BUFFER_INSPECTED."""
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent / "routes" / "observer.py").read_text()
        assert "OBSERVER_BUFFER_INSPECTED" in text


# ── Smoke: codec_dashboard.py is shrinking ────────────────────────────────
def test_dashboard_loc_below_3700():
    """B6 + C1..F7 shaved codec_dashboard.py from 3,912 → ~2,187 LOC.
    Floor stays loose here; the tighter floor lives in
    test_dashboard_loc_below_2300 below."""
    from pathlib import Path
    lines = (Path(__file__).resolve().parent.parent / "codec_dashboard.py").read_text().count("\n")
    assert lines < 3700, f"codec_dashboard.py still has {lines} lines"


# ── F-series (SR-51..56): config/history/tts/vision/vibe_exec/web_search/cdp
class TestFSeriesRouteExtractions:
    @pytest.mark.parametrize("path", [
        "/api/config",
        "/api/history",
        "/api/conversations",
        "/api/tts",
        "/api/response",
        "/api/vision",
        "/api/preview",
        "/preview_frame",
        "/api/run_code",
        "/api/web_search",
        "/api/cdp/status",
    ])
    def test_endpoint_registered(self, path):
        assert path in _registered_paths()

    def test_modules_exist_and_export_router(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "routes"
        for name in ("config", "history", "tts", "vision", "vibe_exec",
                     "web_search", "cdp"):
            text = (root / f"{name}.py").read_text()
            assert "router = APIRouter()" in text, f"{name}.py must export router"

    def test_dashboard_does_not_redefine_endpoints(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "codec_dashboard.py").read_text()
        # All 11 endpoints below must NOT have an `@app.<method>("<path>")`
        # decorator inside codec_dashboard.py — they live in routes/*.py only.
        moved = [
            '/api/config")', '/api/history")', '/api/conversations")',
            '/api/tts")', '/api/response")', '/api/vision")',
            '/api/preview")', '/preview_frame"', '/api/run_code")',
            '/api/web_search")', '/api/cdp/status")',
        ]
        for snippet in moved:
            for verb in ("@app.get(", "@app.post(", "@app.put(", "@app.delete("):
                assert (verb + '"' + snippet) not in src, (
                    f"codec_dashboard.py must not redefine {verb}\"{snippet}; "
                    "it now lives in routes/*.py"
                )


def test_dashboard_loc_below_2300():
    """After F-series, codec_dashboard.py should be under 2,300 lines.
    Tighten when more endpoints move (e.g. chat_completion)."""
    from pathlib import Path
    lines = (Path(__file__).resolve().parent.parent / "codec_dashboard.py").read_text().count("\n")
    assert lines < 2300, f"codec_dashboard.py still has {lines} lines"
