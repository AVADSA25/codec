"""Where config.json is: one rule, one name (docs/TEST-CONFIG-ISOLATION-DESIGN.md).

codec_config.CONFIG_PATH is the one name; it honours CODEC_CONFIG_PATH (tests only) and is
~/.codec/config.json otherwise. Every other module asks codec_config_path.config_path() when it
opens the file. These tests pin the rule and fail when a module builds its own path again.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import codec_config
import codec_config_path

REPO = Path(__file__).resolve().parent.parent
LIVE = os.path.expanduser("~/.codec/config.json")

# Files that may still name a config.json of their own, and why.
ALLOWED = {
    "codec_config_path.py": "the rule itself: the default path",
    "codec_agent_plan.py": "the plan path blocklist entry /.codec/config.json (a security list)",
    "codec_backup.py": "the state tar's item list and its CODEC_HOME (the backup's own folder)",
    "codec_doctor.py": "reads the folder its Paths object names (a doctor run checks another install)",
    "codec_license.py": "its own folder, CODEC_LICENSE_DIR in tests (P-009)",
    "codec_cookbook/fit.py": "a model's config.json on the Hugging Face Hub, not CODEC's",
    "packaging/macos/first_run.py": "the app's first run reads the home folder it is given",
    "tools/list_old_secrets.py": "skips config.json while it lists old copies",
}
SKIP_DIRS = {"tests", "pilot", "swift-overlay", "node_modules", "vendor", "__pycache__"}


def _runtime_files():
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        for name in files:
            if name.endswith(".py"):
                yield Path(root) / name


def test_unset_means_the_old_path(monkeypatch):
    monkeypatch.delenv("CODEC_CONFIG_PATH", raising=False)
    assert codec_config_path.from_env() == LIVE == os.path.expanduser("~/.codec/config.json")
    assert codec_config_path.DEFAULT_CONFIG_PATH == LIVE


def test_the_variable_wins_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEC_CONFIG_PATH", str(tmp_path / "c.json"))
    assert codec_config_path.from_env() == str(tmp_path / "c.json")


def test_the_suite_reads_a_missing_temp_file():
    assert codec_config.CONFIG_PATH == os.environ["CODEC_CONFIG_PATH"] == codec_config_path.from_env()
    assert os.path.realpath(codec_config.CONFIG_PATH) != os.path.realpath(LIVE)
    assert not os.path.exists(codec_config.CONFIG_PATH)
    assert set(codec_config.load_config()) <= {"config_version"}  # nothing of the owner's


def test_config_path_follows_the_one_patched_name(monkeypatch, tmp_path):
    monkeypatch.setattr(codec_config, "CONFIG_PATH", str(tmp_path / "patched.json"))
    assert codec_config_path.config_path() == str(tmp_path / "patched.json")


def test_config_path_without_codec_config_loaded(monkeypatch, tmp_path):
    monkeypatch.delitem(sys.modules, "codec_config")
    monkeypatch.setenv("CODEC_CONFIG_PATH", str(tmp_path / "env.json"))
    assert codec_config_path.config_path() == str(tmp_path / "env.json")
    assert "codec_config" not in sys.modules, "config_path() imported codec_config"


def test_no_module_builds_its_own_config_path():
    found = []
    for path in _runtime_files():
        rel = path.relative_to(REPO).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value.endswith("config.json") and not any(c.isspace() for c in node.value)
                    and rel not in ALLOWED):
                found.append(f"{rel}:{node.lineno} {node.value!r}")
            if isinstance(node, ast.ImportFrom) and any(a.name == "CONFIG_PATH" for a in node.names):
                found.append(f"{rel}:{node.lineno} from {node.module} import CONFIG_PATH")
    assert found == [], "use codec_config_path.config_path() instead:\n" + "\n".join(found)


def test_the_allowlist_has_no_stale_entries():
    for rel in ALLOWED:
        assert (REPO / rel).is_file(), rel
