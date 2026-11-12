"""scripts/start_model_server.sh reads the model from config with the
interpreter it chose, never a bare python3.

On a Mac without the Command Line Tools /usr/bin/python3 is only Apple's
install stub, which fails. The launcher used it to read config.json, fell
back to DEFAULT_MODEL (the 35B) and asked for a model a buyer's Mac does not
have. Each test runs the real script in dry-run mode (it prints its choice and
starts nothing) with a PATH whose only python3 is such a stub.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "start_model_server.sh"
MODEL = "mlx-community/Qwen2.5-7B-Instruct-4bit"

pytestmark = pytest.mark.skipif(shutil.which("dirname") is None, reason="needs dirname")


def _exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _stub_path(tmp_path: Path) -> str:
    """A PATH with dirname and a python3 that fails like Apple's stub."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    os.symlink(shutil.which("dirname"), bin_dir / "dirname")
    _exe(bin_dir / "python3", "#!/bin/sh\nexit 1\n")
    return str(bin_dir)


def _config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"llm_model": MODEL, "llm_base_url": "http://127.0.0.1:8084/v1"}))
    return cfg


def _python_wrapper(path: Path) -> Path:
    return _exe(path, f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')


def _run(script: Path, env: dict) -> str:
    base = {"HOME": os.environ.get("HOME", "/tmp"), "CODEC_LAUNCHER_DRY_RUN": "1"}
    p = subprocess.run(["/bin/bash", str(script)], env={**base, **env},
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return p.stderr


def test_config_is_read_with_the_chosen_venv_not_a_bare_python3(tmp_path):
    venv_py = _python_wrapper(tmp_path / "venv" / "bin" / "python")
    out = _run(SCRIPT, {"PATH": _stub_path(tmp_path), "CODEC_CONFIG": str(_config(tmp_path)),
                        "CODEC_MODEL_VENV": str(tmp_path / "venv")})
    assert f"python={venv_py}" in out
    assert f"model={MODEL} port=8084" in out


def test_inside_the_app_config_is_read_with_the_bundled_python(tmp_path):
    resources = tmp_path / "App.app" / "Contents" / "Resources"
    script = resources / "app" / "scripts" / "start_model_server.sh"
    script.parent.mkdir(parents=True)
    shutil.copy(SCRIPT, script)
    _python_wrapper(resources / "python" / "bin" / "python3")
    out = _run(script, {"PATH": _stub_path(tmp_path), "CODEC_CONFIG": str(_config(tmp_path))})
    chosen = out.split("python=", 1)[1].split()[0]
    assert Path(chosen).resolve() == (resources / "python" / "bin" / "python3").resolve()
    assert f"model={MODEL} port=8084" in out


def test_no_bytecode_is_written_before_the_first_python_run():
    text = SCRIPT.read_text()
    assert text.index("export PYTHONDONTWRITEBYTECODE=1") < text.index("read -r MODEL PORT")
    assert "\n  python3 - <<'PY'" not in text
