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
    """After F-series, codec_dashboard.py should be under 2,300 lines."""
    from pathlib import Path
    lines = (Path(__file__).resolve().parent.parent / "codec_dashboard.py").read_text().count("\n")
    assert lines < 2300, f"codec_dashboard.py still has {lines} lines"


# ── G-series (SR-57..58): memory_search + pilot_proxy ─────────────────────
class TestGSeriesRouteExtractions:
    @pytest.mark.parametrize("path", [
        "/api/memory/search",
        "/api/pilot/{path:path}",
    ])
    def test_endpoint_registered(self, path):
        assert path in _registered_paths()

    def test_modules_exist_and_export_router(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "routes"
        for name in ("memory_search", "pilot_proxy"):
            text = (root / f"{name}.py").read_text()
            assert "router = APIRouter()" in text, f"{name}.py must export router"

    def test_memory_search_covers_all_four_sources(self):
        """G1: the extracted endpoint must still query voice + chat + vibe + flash."""
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent / "routes" / "memory_search.py").read_text()
        assert "from codec_memory import CodecMemory" in text          # voice FTS
        assert "from routes.qchat import qchat_db" in text             # chat
        assert "from routes.vibe import vibe_db" in text               # vibe
        assert "FROM sessions" in text                                  # flash

    def test_pilot_proxy_forwards_to_8094(self):
        """G2: the proxy must still hit localhost:8094 — that's the runner port."""
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent / "routes" / "pilot_proxy.py").read_text()
        assert "localhost:8094" in text
        assert "@router.api_route(" in text
        assert "GET" in text and "POST" in text and "PUT" in text and "DELETE" in text

    def test_dashboard_does_not_redefine_endpoints(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "codec_dashboard.py").read_text()
        assert '@app.post("/api/memory/search")' not in src
        assert '@app.api_route("/api/pilot/' not in src


def test_dashboard_loc_below_2100():
    """After G-series, codec_dashboard.py should be under 2,100 lines."""
    from pathlib import Path
    lines = (Path(__file__).resolve().parent.parent / "codec_dashboard.py").read_text().count("\n")
    assert lines < 2100, f"codec_dashboard.py still has {lines} lines"


# ── H1 (SR-59): chat_completion + helper cluster → routes/chat.py ──────────
class TestH1ChatExtraction:
    def test_chat_endpoint_registered(self):
        assert "/api/chat" in _registered_paths()

    def test_module_exports_router(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent / "routes" / "chat.py").read_text()
        assert "router = APIRouter()" in text
        assert '@router.post("/api/chat")' in text

    def test_helpers_reexported_identity_equal(self):
        """codec_dashboard must re-export the moved helpers (identity-equal to
        routes.chat) so /api/command + the existing test surface keep working."""
        import codec_dashboard as cd
        import routes.chat as rc
        for name in (
            "CHAT_SKILL_ALLOWLIST", "_try_skill", "_try_skill_by_name",
            "_enrich_messages", "_chat_vision_response",
            "_build_chat_system_prompt", "_fetch_url_content",
        ):
            assert getattr(cd, name) is getattr(rc, name), f"{name} not re-exported identity-equal"

    def test_safety_surfaces_preserved(self):
        """The post-LLM [SKILL:...] path stays allowlist + budget gated, and the
        pre-LLM hijack stays consent-gated — verbatim from the in-dashboard original."""
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent / "routes" / "chat.py").read_text()
        assert "CHAT_SKILL_ALLOWLIST" in text                     # allowlist gate
        assert "codec_consent.chat_consent_ok" in text            # destructive consent
        assert "_budget.consume(\"post_llm_skill_tag\")" in text   # budget gate
        assert "SkillTagBuffer" in text                           # stream token machine

    def test_dashboard_does_not_redefine_chat(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "codec_dashboard.py").read_text()
        assert '@app.post("/api/chat")' not in src
        assert "def _build_chat_system_prompt(" not in src
        assert "def chat_completion(" not in src


def test_dashboard_loc_below_1400():
    """After H1 (chat_completion + helper cluster extracted), codec_dashboard.py
    should be well under 1,400 lines (down from 3,912 pre-B6 — a ~65% cut).

    What's left is genuinely dashboard-resident: /api/command, /api/services/status,
    the page renderers, startup/shutdown hooks, and the _bg_* background daemons."""
    from pathlib import Path
    lines = (Path(__file__).resolve().parent.parent / "codec_dashboard.py").read_text().count("\n")
    assert lines < 1400, f"codec_dashboard.py still has {lines} lines"


# ── I1 (SR-60): auto-escalate classifier cluster → codec_chat_pipeline ─────
class TestI1EscalationExtraction:
    _NAMES = (
        "_AUTO_ESCALATE_SYSTEM_PROMPT", "_qwen_chat_classify",
        "_classify_chat_message", "_AUTOESCALATE_SILENCE_LOCK",
        "_autoescalate_silence_set", "ESCALATE_CHECKPOINTS_THRESHOLD",
        "silence_session_autoescalate", "_reset_autoescalate_silence_for_test",
        "_should_escalate_to_project",
    )

    def test_cluster_lives_in_chat_pipeline(self):
        import codec_chat_pipeline as p
        for n in self._NAMES:
            assert hasattr(p, n), f"{n} must live in codec_chat_pipeline"

    def test_dashboard_reexports_identity_equal(self):
        """codec_dashboard must re-export every escalation name identity-equal
        (back-compat for any caller / test that imported them from there)."""
        import codec_dashboard as cd
        import codec_chat_pipeline as p
        for n in self._NAMES:
            assert getattr(cd, n) is getattr(p, n), f"{n} not re-exported identity-equal"

    def test_dashboard_no_longer_defines_cluster(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "codec_dashboard.py").read_text()
        assert "def _should_escalate_to_project(" not in src
        assert "def _qwen_chat_classify(" not in src
        assert "_AUTO_ESCALATE_SYSTEM_PROMPT = " not in src
