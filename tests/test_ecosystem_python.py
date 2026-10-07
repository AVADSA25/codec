"""The PM2 ecosystem finds Python 3.13 instead of hard-coding one path (GitHub issue #424).

Five services were pinned to /usr/local/bin/python3.13, so `pm2 start ecosystem.config.js`
failed with "Script not found" on Macs where Python lives elsewhere (Homebrew on Apple
Silicon uses /opt/homebrew/bin).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ECO = REPO / "ecosystem.config.js"
PINNED = ("codec-mcp-http", "codec-dictate", "codec-autopilot", "codec-observer", "codec-agent-runner")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node to load ecosystem.config.js")


def _scripts(env: dict) -> dict:
    out = subprocess.run(
        ["node", "-e", f"const c=require({json.dumps(str(ECO))});"
                       "console.log(JSON.stringify(Object.fromEntries(c.apps.map(a=>[a.name,a.script]))))"],
        capture_output=True, text=True, env=env, timeout=30, check=True)
    return json.loads(out.stdout)


def test_no_service_hard_codes_a_python_path():
    assert '"/usr/local/bin/python3.13"' not in ECO.read_text(encoding="utf-8").split("function findPython313")[1]


def test_codec_python_wins():
    env = dict(os.environ, CODEC_PYTHON="/custom/bin/python3.13")
    scripts = _scripts(env)
    assert {scripts[n] for n in PINNED} == {"/custom/bin/python3.13"}


def test_a_python313_on_path_is_found(tmp_path):
    fake = tmp_path / "python3.13"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "CODEC_PYTHON"}
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    scripts = _scripts(env)
    found = {scripts[n] for n in PINNED}
    assert len(found) == 1, "the five services use the same Python"
    (python,) = found
    # The usual install places come first; otherwise the one on PATH.
    assert python.endswith("/python3.13") and os.access(python, os.X_OK)
    if not any(os.access(f"{d}/python3.13", os.X_OK) for d in ("/usr/local/bin", "/opt/homebrew/bin")):
        assert python == str(fake)
