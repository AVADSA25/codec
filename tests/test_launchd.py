"""Tests for PR-5C (Audit E / E-7, W5-3) — the PM2 -> launchd LaunchAgent
generator + install/uninstall toolkit. The generator core is pure stdlib
(plistlib + shlex), so the mapping is fully unit-tested here without node or
launchctl; a darwin+node smoke exercises the real ecosystem dump.

Reference: docs/W5-3-LAUNCHD-DESIGN.md, docs/audits/PHASE-1-APPLE-APP.md (E-7).
"""
from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LAUNCHD = REPO / "packaging" / "macos" / "launchd"
GEN = LAUNCHD / "generate_launchagents.py"
INSTALL = LAUNCHD / "install_launchagents.sh"
UNINSTALL = LAUNCHD / "uninstall_launchagents.sh"


def _load_gen():
    spec = importlib.util.spec_from_file_location("generate_launchagents", GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FIXTURE = [
    {"name": "codec", "script": "python3", "args": "codec.py", "cwd": "/repo", "autorestart": True, "restart_delay": 3000},
    {"name": "codec-imessage", "script": "bash", "args": "-c 'python3 codec_imessage.py'", "cwd": "/repo", "autorestart": True},
    {"name": "codec-observer", "script": "/usr/local/bin/python3.13", "args": "-u codec_observer.py",
     "cwd": "/repo", "env": {"OBSERVER_ENABLED": "true"}, "autorestart": True},
]


# ---- pure mapping ---------------------------------------------------------

def test_basic_mapping():
    gen = _load_gen()
    label, d = gen.pm2_app_to_launchd(FIXTURE[0], log_dir="/tmp/logs")
    assert label == "ai.avadigital.codec.codec"
    assert d["Label"] == label
    assert d["ProgramArguments"] == ["python3", "codec.py"]
    assert d["RunAtLoad"] is True
    assert d["KeepAlive"] is True
    assert d["WorkingDirectory"] == "/repo"
    assert d["StandardOutPath"] == "/tmp/logs/codec.out"
    assert d["StandardErrorPath"] == "/tmp/logs/codec.err"


def test_bash_dash_c_is_tokenised_correctly():
    gen = _load_gen()
    _, d = gen.pm2_app_to_launchd(FIXTURE[1], log_dir="/tmp/logs")
    assert d["ProgramArguments"] == ["bash", "-c", "python3 codec_imessage.py"]


def test_env_is_carried_through():
    gen = _load_gen()
    _, d = gen.pm2_app_to_launchd(FIXTURE[2], log_dir="/tmp/logs")
    assert d["EnvironmentVariables"] == {"OBSERVER_ENABLED": "true"}


def test_interpreter_remap():
    gen = _load_gen()
    _, d = gen.pm2_app_to_launchd(
        FIXTURE[2], log_dir="/tmp/logs",
        interpreter_map={"/usr/local/bin/python3.13": "/BUNDLE/python3"},
    )
    assert d["ProgramArguments"][0] == "/BUNDLE/python3"


def test_render_plist_roundtrips():
    gen = _load_gen()
    _, d = gen.pm2_app_to_launchd(FIXTURE[0], log_dir="/tmp/logs")
    raw = gen.render_plist(d)
    assert isinstance(raw, (bytes, bytearray))
    parsed = plistlib.loads(raw)
    assert parsed["Label"] == "ai.avadigital.codec.codec"
    assert parsed["ProgramArguments"] == ["python3", "codec.py"]


# ---- CLI front-end --------------------------------------------------------

def test_cli_from_json_writes_plists():
    with tempfile.TemporaryDirectory() as td:
        jf = Path(td) / "svcs.json"
        jf.write_text(json.dumps(FIXTURE))
        out = Path(td) / "agents"
        r = subprocess.run(
            [sys.executable, str(GEN), "--from-json", str(jf), "--out", str(out), "--log-dir", "/tmp/logs"],
            capture_output=True, text=True, timeout=60,
        )
        assert r.returncode == 0, f"generator failed: {r.stderr}\n{r.stdout}"
        for name in ("codec", "codec-imessage", "codec-observer"):
            p = out / f"ai.avadigital.codec.{name}.plist"
            assert p.exists(), f"missing {p.name}"
            plistlib.loads(p.read_bytes())  # valid plist


# ---- install / uninstall scripts -----------------------------------------

def test_scripts_present_executable_and_safe():
    for s in (INSTALL, UNINSTALL):
        assert s.exists(), f"{s.name} must exist"
        assert os.access(s, os.X_OK), f"{s.name} must be executable"
        assert s.read_text().splitlines()[0].startswith("#!"), f"{s.name} needs a shebang"
        assert "launchctl" in s.read_text(), f"{s.name} must use launchctl"
    it = INSTALL.read_text()
    assert "--dry-run" in it, "install must support --dry-run"
    assert "pm2" in it.lower(), "install must guard against the PM2 fleet running"


# ---- darwin + node end-to-end smoke --------------------------------------

@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("node") is None,
                    reason="needs macOS + node to dump ecosystem.config.js")
def test_from_ecosystem_emits_all_services():
    eco = REPO / "ecosystem.config.js"
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(
            [sys.executable, str(GEN), "--from-ecosystem", str(eco),
             "--out", td, "--log-dir", "/tmp/logs", "--dry-run"],
            capture_output=True, text=True, timeout=90,
        )
        assert r.returncode == 0, f"--from-ecosystem failed: {r.stderr}\n{r.stdout}"
        # 16 services defined in ecosystem.config.js → 16 labels in dry-run output.
        labels = [ln for ln in r.stdout.splitlines() if "ai.avadigital.codec." in ln]
        assert len(labels) >= 16, f"expected >=16 services, saw {len(labels)}:\n{r.stdout}"
