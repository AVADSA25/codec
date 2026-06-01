"""CODEC Cookbook — local-model lifecycle tests.

Coverage matches the build brief:
  * fit.py    — golden anchors (30B→17.2, 80B→42), KV math, offline anchor fallback
  * serve.py  — port allocation stays in 8110-8119 + skips bound ports
  * STOP-GUARD (highest priority) — refuses protected ports, non-cookbook ports,
    non-cookbook PM2 names; requires confirm=True
  * integration — serve → served.json → list → stop(confirm), PM2/health mocked
  * skills     — the six thin skills parse args + format helper output

Real PM2 / MLX / Hub calls are mocked so the suite runs offline + side-effect-free.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from codec_cookbook import args, catalog, fit, probe, serve  # noqa: E402

# A representative Qwen3-MoE-ish config for offline KV math.
_CFG = {"num_hidden_layers": 48, "num_attention_heads": 32,
        "num_key_value_heads": 4, "head_dim": 128, "hidden_size": 4096}


# ── catalog ──────────────────────────────────────────────────────────────
class TestCatalog:
    def test_known_ids(self):
        ids = catalog.ids()
        assert {"qwen3-30b-a3b", "qwen3-next-80b", "llama32-3b"} <= set(ids)

    def test_get_unknown_raises(self):
        with pytest.raises(KeyError):
            catalog.get("does-not-exist")

    def test_find_unknown_is_none(self):
        assert catalog.find("nope") is None

    def test_by_role(self):
        assert any(e["id"] == "qwen3-coder-30b" for e in catalog.by_role("code"))

    def test_primary_model_not_in_catalog(self):
        # the live qwen3.6@8083 must never be Cookbook-managed
        assert not any("Qwen3.6" in e["hf_repo"] for e in catalog.all_entries())


# ── fit math ─────────────────────────────────────────────────────────────
class TestFit:
    def test_golden_anchors_present(self):
        assert catalog.get("qwen3-30b-a3b")["anchor_gb"] == 17.2
        assert catalog.get("qwen3-next-80b")["anchor_gb"] == 42.0

    def test_kv_cache_math(self):
        # 2 * nl * nkv * hd * ctx * 2 bytes / 1e9
        expected = (2 * 48 * 4 * 128 * 8192 * 2) / 1e9
        assert abs(fit.kv_cache_gb(_CFG, 8192) - expected) < 1e-9

    def test_kv_gqa_falls_back_to_attention_heads(self):
        mha = {"num_hidden_layers": 4, "num_attention_heads": 8, "hidden_size": 512}
        # no num_key_value_heads → uses num_attention_heads; head_dim = 512/8 = 64
        expected = (2 * 4 * 8 * 64 * 1024 * 2) / 1e9
        assert abs(fit.kv_cache_gb(mha, 1024) - expected) < 1e-9

    def test_footprint_uses_anchor_and_overhead(self):
        need = fit.estimate_footprint_gb("x", 8192, anchor_gb=17.2, cfg=_CFG)
        kv = fit.kv_cache_gb(_CFG, 8192)
        expected = (17.2 + kv) * 1.10 + 1.5
        assert abs(need - expected) < 1e-6

    def test_offline_anchor_fallback_no_network(self, monkeypatch):
        # simulate Hub unreachable: load_config raises → KV omitted, no crash,
        # and weight_gb_from_hub must NOT be called (anchor provided).
        monkeypatch.setattr(fit, "load_config",
                            lambda r: (_ for _ in ()).throw(RuntimeError("offline")))
        called = {"hub": False}
        monkeypatch.setattr(fit, "weight_gb_from_hub",
                            lambda r: called.__setitem__("hub", True) or 999.0)
        need = fit.estimate_footprint_gb("x", 8192, anchor_gb=17.2)
        assert called["hub"] is False, "anchor given → must not hit the Hub for weights"
        assert abs(need - (17.2 * 1.10 + 1.5)) < 1e-6

    def test_available_gb_formula(self):
        assert fit.available_gb(192, [17.2, 42.0], os_reserve_gb=24) == pytest.approx(108.8)

    def test_fits_margin(self):
        ok, hr = fit.fits(need_gb=50, avail_gb=60, margin_gb=8)
        assert ok and hr == 10
        ok2, hr2 = fit.fits(need_gb=55, avail_gb=60, margin_gb=8)
        assert not ok2 and hr2 == 5

    def test_recommend_orders_fits_first_biggest(self):
        entries = [
            {"id": "tiny", "hf_repo": "r/tiny", "anchor_gb": 2.0, "roles": ["tiny"]},
            {"id": "big", "hf_repo": "r/big", "anchor_gb": 40.0, "roles": ["max"]},
            {"id": "huge", "hf_repo": "r/huge", "anchor_gb": 200.0, "roles": ["max"]},
        ]
        # KV unavailable offline → footprint ≈ anchor*1.1+1.5
        with patch.object(fit, "load_config", side_effect=RuntimeError("offline")):
            ranked = fit.recommend(entries, avail_gb=60, ctx=8192)
        ids = [r["entry"]["id"] for r in ranked]
        assert ids[0] == "big", "biggest model that fits should rank first"
        assert ids[-1] == "huge", "non-fitting model ranks last"
        assert ranked[-1]["fits"] is False


# ── args parsing ───────────────────────────────────────────────────────────
class TestArgs:
    def test_model_id(self):
        assert args.parse_model_id("cookbook serve qwen3-30b-a3b now") == "qwen3-30b-a3b"
        assert args.parse_model_id("nothing here") is None

    def test_context(self):
        assert args.parse_context("serve x context 16384") == 16384
        assert args.parse_context("serve x ctx=4096") == 4096
        assert args.parse_context("serve x") == 8192

    def test_flags(self):
        assert args.parse_flag("serve x force", "force")
        assert args.parse_flag("stop x confirm=true", "confirm")
        assert not args.parse_flag("serve x", "force")

    def test_port(self):
        assert args.parse_port("stop port 8112") == 8112
        assert args.parse_port("stop 8083") is None  # not in cookbook range
        assert args.parse_port("stop x") is None

    def test_role(self):
        assert args.parse_role("recommend a code model") == "code"
        assert args.parse_role("recommend something") is None


# ── port allocation ─────────────────────────────────────────────────────────
class TestPortAllocation:
    def test_stays_in_range_and_skips(self, tmp_path, monkeypatch):
        monkeypatch.setattr(serve, "SERVED_PATH", str(tmp_path / "served.json"))
        monkeypatch.setattr(probe, "bound_ports_in_range", lambda lo, hi: {8110, 8111})
        monkeypatch.setattr(probe, "pm2_processes",
                            lambda: [{"port": 8112, "name": "x", "status": "online", "rss_gb": 0}])
        serve._save_served([{"id": "a", "port": 8113, "pm2_name": "cookbook-a-8113"}])
        port = serve.allocate_port()
        assert port == 8114
        assert 8110 <= port <= 8119

    def test_exhausted_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(serve, "SERVED_PATH", str(tmp_path / "served.json"))
        monkeypatch.setattr(probe, "bound_ports_in_range",
                            lambda lo, hi: set(range(8110, 8120)))
        monkeypatch.setattr(probe, "pm2_processes", lambda: [])
        assert serve.allocate_port() is None

    def test_never_allocates_protected_port(self, tmp_path, monkeypatch):
        monkeypatch.setattr(serve, "SERVED_PATH", str(tmp_path / "served.json"))
        monkeypatch.setattr(probe, "bound_ports_in_range", lambda lo, hi: set())
        monkeypatch.setattr(probe, "pm2_processes", lambda: [])
        for _ in range(20):
            p = serve.allocate_port()
            assert p not in probe.PROTECTED_PORTS


# ── STOP-GUARD (highest priority) ────────────────────────────────────────────
class TestStopGuard:
    @pytest.fixture
    def served(self, tmp_path, monkeypatch):
        monkeypatch.setattr(serve, "SERVED_PATH", str(tmp_path / "served.json"))
        serve._save_served([
            {"id": "llama32-3b", "port": 8112,
             "pm2_name": "cookbook-llama32-3b-8112", "backend": "mlx"},
        ])
        return serve

    @pytest.mark.parametrize("port", [8083, 8084, 8085, 8090, 8094, 9222, 9223, 5678])
    def test_refuses_protected_ports(self, served, port):
        r = served.stop(port, confirm=True)
        assert r["status"] == "refused"

    def test_protected_set_covers_live_core_stack(self):
        # the live core services verified by lsof on the box must all be protected
        for port in (8083, 8084, 8085, 8090, 8094, 5678):
            assert port in probe.PROTECTED_PORTS, f"{port} (live core service) not protected"

    def test_refuses_protected_port_even_if_in_served(self, served):
        # defense-in-depth: a (hypothetical) cookbook record on a protected port
        served._save_served([{"id": "x", "port": 8090, "pm2_name": "cookbook-x-8090"}])
        r = served.stop(8090, confirm=True)
        assert r["status"] == "refused" and r["reason"] == "protected_port"

    @pytest.mark.parametrize("name", ["qwen3.6", "codec-dashboard", "pilot-runner", "n8n"])
    def test_refuses_non_cookbook_names(self, served, name):
        r = served.stop(name, confirm=True)
        assert r["status"] == "refused" and r["reason"] == "not_a_cookbook_process"

    def test_refuses_non_cookbook_namespace_record(self, served):
        # a served record whose name isn't cookbook- prefixed → guard 3
        served._save_served([{"id": "x", "port": 8115, "pm2_name": "evil-proc-8115"}])
        r = served.stop("evil-proc-8115", confirm=True)
        assert r["status"] == "refused" and r["reason"] == "not_cookbook_namespace"

    def test_refuses_bound_non_cookbook_port(self, served):
        # a port we never served (not in served.json) → refused, never stopped
        r = served.stop(8085, confirm=True)
        assert r["status"] == "refused" and r["reason"] == "not_a_cookbook_process"

    def test_dry_run_without_confirm(self, served):
        r = served.stop("cookbook-llama32-3b-8112", confirm=False)
        assert r["status"] == "would_stop"
        assert r["pm2_name"] == "cookbook-llama32-3b-8112" and r["port"] == 8112

    def test_dry_run_by_port_without_confirm(self, served):
        assert served.stop(8112, confirm=False)["status"] == "would_stop"

    def test_confirmed_stop_calls_pm2_delete(self, served, monkeypatch):
        captured = {}

        def fake_run(argv, **kw):
            captured["argv"] = argv
            return MagicMock(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(serve.subprocess, "run", fake_run)
        r = served.stop("cookbook-llama32-3b-8112", confirm=True)
        assert r["status"] == "stopped"
        assert captured["argv"] == ["pm2", "delete", "cookbook-llama32-3b-8112"]
        # removed from served.json
        assert served.stop(8112, confirm=False)["status"] == "refused"


# ── integration: serve → list → stop (PM2 + health mocked) ──────────────────
class TestIntegration:
    def test_serve_then_list_then_stop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(serve, "SERVED_PATH", str(tmp_path / "served.json"))
        monkeypatch.setattr(probe, "bound_ports_in_range", lambda lo, hi: set())
        monkeypatch.setattr(probe, "pm2_processes", lambda: [])
        monkeypatch.setattr(serve, "resolve_mlx_python", lambda: "/usr/bin/python3")
        monkeypatch.setattr(serve, "_health_ok", lambda port, timeout_s=90: True)
        monkeypatch.setattr(serve.subprocess, "run",
                            lambda argv, **kw: MagicMock(returncode=0, stdout="ok", stderr=""))

        entry = catalog.get("llama32-3b")
        res = serve.launch(entry, context_length=8192)
        assert res["status"] == "serving"
        assert 8110 <= res["port"] <= 8119
        assert res["pm2_name"].startswith("cookbook-llama32-3b-")

        # appears in list
        monkeypatch.setattr(probe, "is_port_bound", lambda port, host="127.0.0.1": True)
        listed = serve.list_served()
        assert any(r["id"] == "llama32-3b" for r in listed)

        # stop it
        stopped = serve.stop(res["pm2_name"], confirm=True)
        assert stopped["status"] == "stopped"
        assert serve.list_served() == []

    def test_serve_command_is_corrected_mlx_form(self, tmp_path, monkeypatch):
        monkeypatch.setattr(serve, "SERVED_PATH", str(tmp_path / "served.json"))
        monkeypatch.setattr(serve, "resolve_mlx_python", lambda: "/PY")
        argv, err = serve._build_command(catalog.get("llama32-3b"), 8112, 8192)
        assert err is None
        # `-m mlx_lm server` subcommand form (NOT `-m mlx_lm.server`); --max-tokens present
        assert argv[:5] == ["pm2", "start", "/PY", "--name", "cookbook-llama32-3b-8112"]
        assert "mlx_lm" in argv and "server" in argv
        assert "mlx_lm.server" not in argv
        assert "--max-tokens" in argv
        assert "--port" in argv and "8112" in argv


# ── skills smoke ─────────────────────────────────────────────────────────────
class TestSkills:
    def test_all_six_discovered_with_expected_exposure(self):
        import codec_dispatch
        codec_dispatch.load_skills()
        reg = codec_dispatch.registry
        names = set(reg.names())
        expected = {"cookbook_scan", "cookbook_recommend", "cookbook_list",
                    "cookbook_serve", "cookbook_download", "cookbook_stop"}
        assert expected <= names
        # read-only exposed, mutating not
        assert reg.get_mcp_expose("cookbook_scan") is True
        assert reg.get_mcp_expose("cookbook_serve") is False
        assert reg.get_mcp_expose("cookbook_stop") is False

    def test_serve_skill_refuses_on_insufficient_memory(self, monkeypatch):
        import skills.cookbook_serve as cs
        monkeypatch.setattr(cs.probe, "available_gb", lambda *a, **k: 10.0)
        monkeypatch.setattr(cs.fit, "estimate_footprint_gb", lambda *a, **k: 42.0)
        out = cs.run("cookbook serve qwen3-next-80b")
        assert "Refused" in out and "insufficient memory" in out

    def test_serve_skill_force_overrides(self, monkeypatch):
        import skills.cookbook_serve as cs
        monkeypatch.setattr(cs.probe, "available_gb", lambda *a, **k: 10.0)
        monkeypatch.setattr(cs.fit, "estimate_footprint_gb", lambda *a, **k: 42.0)
        monkeypatch.setattr(cs.serve, "launch",
                            lambda e, c: {"status": "serving", "port": 8112,
                                          "pm2_name": "cookbook-x-8112"})
        out = cs.run("cookbook serve qwen3-next-80b force")
        assert "Serving" in out

    def test_stop_skill_requires_confirm(self, monkeypatch):
        import skills.cookbook_stop as ck
        monkeypatch.setattr(ck.serve, "list_served",
                            lambda: [{"id": "llama32-3b", "port": 8112,
                                      "pm2_name": "cookbook-llama32-3b-8112"}])
        monkeypatch.setattr(ck.serve, "stop",
                            lambda t, confirm=False: ({"status": "would_stop",
                                                       "pm2_name": "cookbook-llama32-3b-8112",
                                                       "port": 8112} if not confirm
                                                      else {"status": "stopped",
                                                            "pm2_name": "cookbook-llama32-3b-8112",
                                                            "port": 8112}))
        assert "confirm" in ck.run("cookbook stop llama32-3b").lower()
        assert "Stopped" in ck.run("cookbook stop llama32-3b confirm")
