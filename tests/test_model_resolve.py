"""Model ids by role: codec_models.resolve / default_for (P-057).

docs/MODEL-RESOLVE-DESIGN.md. codec_models is the one place holding default
model ids; every other Python file names a role. Config is read only from
tmp_path (codec_models.CONFIG_PATH patched) or passed in, never the real
~/.codec and never a fake HOME.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import codec_models  # noqa: E402

KEYS = {"chat": "llm_model", "agent": "llm_model", "plan": "llm_model", "voice": "voice_model",
        "vision": "vision_model", "ui_tars": "ui_tars_model", "stt": "stt_model", "tts": "tts_model"}
ROLES = list(KEYS) + ["research"]
HUB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")

# Lines outside codec_models.py and tests/ allowed to hold an mlx-community/ id, by exact text.
ALLOWED = {
    # The setup wizard's prompts and the values it writes; it imports nothing from CODEC.
    ("setup_codec.py", 'config["llm_model"] = ask_text("Model name", "mlx-community/Qwen3.6-35B-A3B-4bit")'),
    ("setup_codec.py", 'config["tts_model"] = "mlx-community/Kokoro-82M-bf16"'),
    ("setup_codec.py", 'config["stt_model"] = ask_text("Whisper model", "mlx-community/whisper-large-v3-turbo")'),
    # Step 9 design-gate files: their fallback waits for the owner's word (own design note).
    ("codec_agent_plan.py", 'return "mlx-community/Qwen3.6-35B-A3B-4bit"'),
    ("codec_agent_runner.py", 'return "mlx-community/Qwen3.6-35B-A3B-4bit"'),
}


def _scan(paths, root=REPO):
    """(path, stripped line) for every line holding an mlx-community/ id."""
    hits = []
    for rel in paths:
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        hits += [(rel, line.strip()) for line in text.splitlines() if "mlx-community/" in line]
    return hits


def _tracked_python():
    try:
        out = subprocess.run(["git", "-C", str(REPO), "ls-files", "*.py"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git is not available")
    if out.returncode != 0 or not out.stdout.strip():
        pytest.skip("not a git checkout")
    return [p for p in out.stdout.split()
            if not p.startswith("tests/") and p != "codec_models.py" and (REPO / p).is_file()]


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(codec_models, "CONFIG_PATH", str(path))

    def write(**cfg):
        path.write_text(json.dumps(cfg))
        return cfg
    return write


# ── The contract ─────────────────────────────────────────────────────────────

def test_every_role_has_a_hub_id_default_and_resolves_to_it_with_no_config():
    for role in ROLES:
        default = codec_models.default_for(role)
        assert HUB_ID.fullmatch(default) and not default.startswith("/"), role
        assert codec_models.resolve(role, {}) == default, role
    assert codec_models.default_for("chat") == codec_models.DEFAULT_MODEL
    assert codec_models.default_for("research") == codec_models.RESEARCH_MODELS["35b"]


@pytest.mark.parametrize("role", list(KEYS))
def test_each_role_reads_its_key_byte_for_byte(role):
    odd = " mlx-community/Some-Model_v2.1 "          # never stripped, never rewritten
    assert codec_models.resolve(role, {KEYS[role]: odd}) == odd
    for empty in ("", None, 5, ["x/y"]):
        assert codec_models.resolve(role, {"llm_model": None, KEYS[role]: empty}) == codec_models.default_for(role)


def test_roles_read_only_their_own_key():
    cfg = {"llm_model": "org/chat", "vision_model": "org/eyes", "stt_model": "org/ears",
           "tts_model": "org/mouth", "ui_tars_model": "org/hands"}
    got = {r: codec_models.resolve(r, cfg) for r in ROLES}
    assert got == {"chat": "org/chat", "agent": "org/chat", "plan": "org/chat", "voice": "org/chat",
                   "vision": "org/eyes", "ui_tars": "org/hands", "stt": "org/ears", "tts": "org/mouth",
                   "research": codec_models.default_for("research")}


def test_unknown_role_raises():
    with pytest.raises(ValueError):
        codec_models.resolve("chatt", {})
    with pytest.raises(ValueError):
        codec_models.default_for("")


def test_resolve_reads_config_path_when_no_cfg_is_given(config_file):
    config_file(llm_model="org/from-file", stt_model="org/stt-file")
    assert codec_models.resolve("chat") == "org/from-file"
    assert codec_models.resolve("stt") == "org/stt-file"
    assert codec_models.resolve("tts") == codec_models.default_for("tts")


def test_voice_rule_pinned_then_the_model_the_local_server_holds():
    assert codec_models.resolve("voice", {"voice_model": "org/pinned", "llm_model": "org/a"}) == "org/pinned"
    assert codec_models.resolve("voice", {"llm_model": "org/a"}) == "org/a"
    on_cloud = {"llm_model": "cloud-x", "llm_base_url": "https://api.example.com/v1",
                "extra_models": [{"id": "cloud-x", "base_url": "https://api.example.com/v1"}],
                "llm_local_restore": {"llm_model": "org/local", "llm_base_url": "http://localhost:8083/v1"}}
    assert codec_models.resolve("voice", on_cloud) == "org/local", "voice never names a cloud id"
    assert codec_models.resolve("chat", on_cloud) == "cloud-x", "chat follows the picker"


def test_defaults_are_the_manifest_ids_of_their_kind():
    models = json.loads((REPO / "packaging/macos/models.json").read_text())["models"]
    kinds = {m["repo"]: (m["kind"], m["tier"]) for m in models}
    assert kinds[codec_models.default_for("chat")] == ("llm", "bundled")
    assert kinds[codec_models.default_for("stt")][0] == "stt"
    assert kinds[codec_models.default_for("vision")][0] == "vision"
    assert kinds[codec_models.default_for("research")][0] == "llm"
    assert codec_models.TEXT_ONLY_MODELS == {codec_models.DEFAULT_MODEL}


# ── Only codec_models holds ids ──────────────────────────────────────────────

def test_only_codec_models_holds_ids_outside_the_allowlist():
    hits = set(_scan(_tracked_python()))
    assert hits - ALLOWED == set(), "name a role (codec_models.resolve / default_for) instead"
    assert ALLOWED - hits == set(), "an allowlisted line is gone: drop it from ALLOWED"


def test_scan_finds_a_planted_literal(tmp_path):
    (tmp_path / "planted.py").write_text('MODEL = "mlx-community/Planted-1B"\nOTHER = 1\n')
    assert _scan(["planted.py"], root=tmp_path) == [("planted.py", 'MODEL = "mlx-community/Planted-1B"')]


# ── Call sites name their role ───────────────────────────────────────────────

def test_shared_data_points_at_codec_models():
    import codec_agents
    import codec_setup
    import codec_vision
    from routes import transcribe, tts
    assert codec_agents.DEEP_RESEARCH_MODELS == codec_models.RESEARCH_MODELS
    assert codec_agents.DEEP_RESEARCH_DEFAULT == codec_models.default_for("research")
    assert codec_setup.BUNDLED_LOCAL_MODEL == codec_models.DEFAULT_MODEL
    assert codec_vision._TEXT_ONLY_MODELS is codec_models.TEXT_ONLY_MODELS
    assert transcribe.DEFAULT_MODEL == codec_models.default_for("stt")
    assert tts._DEFAULT_MODEL == codec_models.default_for("tts")


def test_crews_name_the_agent_role(monkeypatch):
    import codec_agents
    monkeypatch.setattr(codec_agents, "_cfg", lambda: {})
    assert codec_agents._qwen_model() == codec_models.DEFAULT_MODEL
    monkeypatch.setattr(codec_agents, "_cfg", lambda: {"llm_model": "org/crew"})
    assert codec_agents._qwen_model() == "org/crew"


def _skill(name):
    spec = importlib.util.spec_from_file_location(f"_p057_{name}", REPO / "skills" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_translate_names_the_model_the_local_server_holds(config_file, monkeypatch):
    import codec_llm
    sent = []
    monkeypatch.setattr(codec_llm, "call", lambda msgs, **kw: sent.append(kw["model"]) or "bonjour")
    mod = _skill("translate")
    config_file(llm_model="org/local-chat")
    assert mod.run("translate hello to french") == "hello -> bonjour"
    config_file(llm_model="cloud-x", llm_base_url="https://api.example.com/v1",
                extra_models=[{"id": "cloud-x", "base_url": "https://api.example.com/v1"}],
                llm_local_restore={"llm_model": "org/restore", "llm_base_url": "http://localhost:8083/v1"})
    mod.run("translate hello to french")
    assert sent == ["org/local-chat", "org/restore"]


def test_screenshot_text_keeps_sending_the_vision_default(monkeypatch, tmp_path):
    import functools
    import tempfile

    import requests
    mod = _skill("screenshot_text")
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", functools.partial(tempfile.NamedTemporaryFile, dir=tmp_path))
    sent = []

    class _R:
        def json(self):
            return {"choices": [{"message": {"content": "a screen"}}]}
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(requests, "post", lambda url, json=None, **k: sent.append(json["model"]) or _R())
    assert mod.run("read my screen") == "a screen"
    assert sent == [codec_models.default_for("vision")]
