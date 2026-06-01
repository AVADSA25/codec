"""CODEC Compare — model fan-out tests.

All model callers (codec_llm.call, codec_ava_client.ava_chat_simple) and the
Cookbook registry are mocked, so the suite runs offline + side-effect-free.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import codec_compare as cc  # noqa: E402


def _eps():
    return [
        {"label": "local", "kind": "openai", "model": "qwen",
         "base_url": "http://x/v1", "tier": "local"},
        {"label": "cloud-pro", "kind": "ava", "model": "gemini-2.5-pro", "tier": "cloud"},
    ]


# ── _query_one ───────────────────────────────────────────────────────────────
class TestQueryOne:
    def test_openai_path(self, monkeypatch):
        import codec_llm
        monkeypatch.setattr(codec_llm, "call", lambda m, **k: f"hi from {k['model']}")
        r = cc._query_one(_eps()[0], "p", None, 10)
        assert r["ok"] and r["response"] == "hi from qwen"
        assert r["tier"] == "local" and isinstance(r["elapsed_ms"], int)

    def test_ava_path(self, monkeypatch):
        import codec_ava_client
        monkeypatch.setattr(codec_ava_client, "ava_chat_simple",
                            lambda p, system=None, **k: f"cloud:{k['model']}")
        r = cc._query_one(_eps()[1], "p", None, 10)
        assert r["ok"] and r["response"] == "cloud:gemini-2.5-pro"

    def test_error_is_captured_not_raised(self, monkeypatch):
        import codec_llm
        monkeypatch.setattr(codec_llm, "call",
                            lambda m, **k: (_ for _ in ()).throw(RuntimeError("down")))
        r = cc._query_one(_eps()[0], "p", None, 10)
        assert r["ok"] is False and "down" in r["error"]

    def test_system_prompt_threaded(self, monkeypatch):
        import codec_llm
        seen = {}
        monkeypatch.setattr(codec_llm, "call",
                            lambda m, **k: seen.update(msgs=m) or "ok")
        cc._query_one(_eps()[0], "p", "be terse", 10)
        assert seen["msgs"][0] == {"role": "system", "content": "be terse"}


# ── compare fan-out ──────────────────────────────────────────────────────────
class TestCompare:
    def test_order_preserved_and_all_collected(self, monkeypatch):
        monkeypatch.setattr(cc, "_query_one",
                            lambda e, p, s, t: {"label": e["label"], "ok": True,
                                                "response": e["label"], "elapsed_ms": 1})
        out = cc.compare("hi", endpoints=_eps())
        assert [r["label"] for r in out["results"]] == ["local", "cloud-pro"]
        assert out["blind"] is False
        assert all(r["display"] == r["label"] for r in out["results"])

    def test_blind_anonymizes_and_maps(self, monkeypatch):
        monkeypatch.setattr(cc, "_query_one",
                            lambda e, p, s, t: {"label": e["label"], "ok": True,
                                                "response": "x", "elapsed_ms": 1})
        out = cc.compare("hi", endpoints=_eps(), blind=True)
        assert [r["display"] for r in out["results"]] == ["Model A", "Model B"]
        assert out["mapping"] == {"Model A": "local", "Model B": "cloud-pro"}

    def test_empty_prompt(self):
        assert cc.compare("   ")["results"] == []

    def test_no_endpoints_note(self):
        out = cc.compare("hi", endpoints=[])
        assert out["results"] == [] and "no endpoints" in out["note"]

    def test_one_failure_does_not_sink_others(self, monkeypatch):
        import codec_llm
        monkeypatch.setattr(codec_llm, "call",
                            lambda m, **k: "ok" if k["model"] == "qwen"
                            else (_ for _ in ()).throw(RuntimeError("boom")))
        eps = [_eps()[0], {"label": "b", "kind": "openai", "model": "z",
                           "base_url": "http://y/v1", "tier": "cookbook"}]
        out = cc.compare("hi", endpoints=eps)
        assert [r["ok"] for r in out["results"]] == [True, False]


# ── endpoint discovery ───────────────────────────────────────────────────────
class TestDefaultEndpoints:
    def test_local_always_present(self, monkeypatch):
        monkeypatch.setattr(cc, "_load_cfg", lambda: {})
        monkeypatch.setattr(cc, "_cookbook_endpoints", lambda: [])
        monkeypatch.setitem(sys.modules, "codec_ava_client",
                            SimpleNamespace(load_config=lambda: None))
        eps = cc.default_endpoints()
        assert eps[0]["tier"] == "local" and eps[0]["kind"] == "openai"

    def test_cloud_tiers_only_when_ava_ready(self, monkeypatch):
        monkeypatch.setattr(cc, "_load_cfg", lambda: {})
        monkeypatch.setattr(cc, "_cookbook_endpoints", lambda: [])
        ready = SimpleNamespace(is_ready=lambda: True)
        monkeypatch.setitem(sys.modules, "codec_ava_client",
                            SimpleNamespace(load_config=lambda: ready))
        labels = [e["label"] for e in cc.default_endpoints()]
        assert "cloud-balanced" in labels and "cloud-pro" in labels

    def test_cloud_tiers_absent_when_not_ready(self, monkeypatch):
        monkeypatch.setattr(cc, "_load_cfg", lambda: {})
        monkeypatch.setattr(cc, "_cookbook_endpoints", lambda: [])
        notready = SimpleNamespace(is_ready=lambda: False)
        monkeypatch.setitem(sys.modules, "codec_ava_client",
                            SimpleNamespace(load_config=lambda: notready))
        assert all(e["tier"] != "cloud" for e in cc.default_endpoints())

    def test_config_overrides_cloud_tiers(self, monkeypatch):
        cfg = {"compare": {"cloud_tiers": [{"label": "claude", "model": "claude-3-5-sonnet"}]}}
        monkeypatch.setattr(cc, "_load_cfg", lambda: cfg)
        monkeypatch.setattr(cc, "_cookbook_endpoints", lambda: [])
        ready = SimpleNamespace(is_ready=lambda: True)
        monkeypatch.setitem(sys.modules, "codec_ava_client",
                            SimpleNamespace(load_config=lambda: ready))
        cloud = [e for e in cc.default_endpoints() if e["tier"] == "cloud"]
        assert len(cloud) == 1 and cloud[0]["model"] == "claude-3-5-sonnet"

    def test_cookbook_endpoints_skip_unhealthy(self, monkeypatch):
        served = [
            {"id": "a", "port": 8112, "hf_repo": "r/a", "pm2_status": "online", "healthy": True},
            {"id": "b", "port": 8113, "hf_repo": "r/b", "pm2_status": "stopped", "healthy": False},
        ]
        monkeypatch.setitem(sys.modules, "codec_cookbook",
                            SimpleNamespace(serve=SimpleNamespace(list_served=lambda: served)))
        # import path inside _cookbook_endpoints is `from codec_cookbook import serve`
        import codec_cookbook  # noqa: F401
        eps = cc._cookbook_endpoints()
        labels = [e["label"] for e in eps]
        assert labels == ["cookbook-a"]  # only the healthy/online one
        assert eps[0]["base_url"] == "http://127.0.0.1:8112/v1"


# ── skill ────────────────────────────────────────────────────────────────────
class TestSkill:
    def test_discovered_and_exposed(self):
        import codec_dispatch
        codec_dispatch.load_skills()
        reg = codec_dispatch.registry
        assert "compare" in reg.names()
        assert reg.get_mcp_expose("compare") is True

    def test_parses_prompt_and_formats_labeled(self, monkeypatch):
        import skills.compare as sc
        monkeypatch.setattr(sc, "compare", lambda prompt, blind=False: {
            "prompt": prompt, "blind": blind,
            "results": [{"label": "local", "display": "local", "tier": "local",
                         "ok": True, "response": "42", "elapsed_ms": 10}]})
        out = sc.run("compare models: meaning of life")
        assert "meaning of life" in out and "local" in out and "42" in out

    def test_blind_flag_detected_and_key_shown(self, monkeypatch):
        import skills.compare as sc
        monkeypatch.setattr(sc, "compare", lambda prompt, blind=False: {
            "prompt": prompt, "blind": blind,
            "results": [{"label": "local", "display": "Model A", "tier": "local",
                         "ok": True, "response": "x", "elapsed_ms": 5}],
            "mapping": {"Model A": "local"}})
        out = sc.run("blind compare what is 2+2")
        assert "Model A" in out and "Key" in out and "local" in out

    def test_empty_prompt_asks(self):
        import skills.compare as sc
        assert "What should I compare" in sc.run("compare models")

    def test_failure_rendered(self, monkeypatch):
        import skills.compare as sc
        monkeypatch.setattr(sc, "compare", lambda prompt, blind=False: {
            "prompt": prompt, "blind": False,
            "results": [{"label": "cloud-pro", "display": "cloud-pro", "tier": "cloud",
                         "ok": False, "error": "license expired", "elapsed_ms": 3}]})
        out = sc.run("compare models hello")
        assert "✗" in out and "license expired" in out
