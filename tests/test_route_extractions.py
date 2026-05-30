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
    """B6 + C1..C5 shaved codec_dashboard.py from 3,912 → 3,618 LOC
    (~300 LOC moved to route groups). Floor: < 3,700.

    Future route extractions (qchat, vibe, schedules, prompts) will
    keep dropping this number — tighten the floor as those land."""
    from pathlib import Path
    lines = (Path(__file__).resolve().parent.parent / "codec_dashboard.py").read_text().count("\n")
    assert lines < 3700, f"codec_dashboard.py still has {lines} lines"
