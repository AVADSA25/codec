"""Tests for PR-5G (Audit E / E-8, W5-5) — the model-pack downloader.

Hermetic: the manifest + the pure planning/consent core + a no-network --dry-run
are tested. The real HuggingFace download (lazy huggingface_hub import) is never
exercised here.

Reference: docs/W5-5-MODEL-FETCH-DESIGN.md, docs/audits/PHASE-1-APPLE-APP.md (E-8).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "packaging" / "macos"
MANIFEST = PKG / "models.json"
FETCH = PKG / "fetch_models.py"


def _load():
    spec = importlib.util.spec_from_file_location("fetch_models", FETCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_manifest_valid_and_tiered():
    d = json.loads(MANIFEST.read_text())
    models = d["models"]
    assert len(models) >= 3
    for m in models:
        for k in ("name", "kind", "repo", "revision", "tier", "approx_gb"):
            assert k in m, f"model {m.get('name')} missing {k}"
        assert m["tier"] in ("bundled", "on_demand")
        assert m["kind"] in ("stt", "tts", "llm", "vision")
        assert isinstance(m["approx_gb"], (int, float))
    tiers = {m["tier"] for m in models}
    assert "bundled" in tiers and "on_demand" in tiers, "need both tiers"
    repos = {m["repo"] for m in models}
    assert "mlx-community/Qwen3.5-35B-A3B-4bit" in repos, "should reference the real LLM repo"


def test_select_and_total():
    f = _load()
    models = f.load_manifest(str(MANIFEST))
    bundled = f.select(models, "bundled")
    assert bundled and all(m["tier"] == "bundled" for m in bundled)
    assert len(f.select(models, "all")) == len(models)
    assert f.total_gb(bundled) > 0


def test_consent_text_mentions_size_and_dest():
    f = _load()
    models = f.load_manifest(str(MANIFEST))
    txt = f.consent_text(f.select(models, "bundled"), "~/.codec/models")
    assert "GB" in txt and "~/.codec/models" in txt
    assert any(m["name"] in txt for m in models)


def test_dry_run_lists_without_downloading():
    r = subprocess.run(
        [sys.executable, str(FETCH), "--tier", "bundled", "--manifest", str(MANIFEST), "--dry-run"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, f"dry-run failed: {r.stderr}\n{r.stdout}"
    assert "GB" in r.stdout, "must show total size"
    assert "dry-run" in r.stdout.lower() or "nothing" in r.stdout.lower()


def test_fetch_script_executable():
    assert FETCH.exists() and os.access(FETCH, os.X_OK), "fetch_models.py must be executable"
