"""Tests for config schema versioning + migration (A-15, PR-3).

codec_config now stamps a `config_version` and runs a migration ladder on
first load after an upgrade. Writes back only when the file exists AND a
migration changed something; never creates a file just to stamp, never
overwrites an unparseable config.

Reference: docs/audits/PHASE-1-CODE-QUALITY.md finding A-15.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import codec_config  # noqa: E402


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Redirect CONFIG_PATH to a tmp file so load_config never touches the
    operator's real ~/.codec/config.json."""
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(codec_config, "CONFIG_PATH", str(cfg_path))
    return cfg_path


# ── Schema constant + migration ladder ───────────────────────────────────────


def test_schema_version_constant():
    assert isinstance(codec_config.CONFIG_SCHEMA_VERSION, int)
    assert codec_config.CONFIG_SCHEMA_VERSION >= 1


def test_migrate_v0_empty_stamps_version():
    cfg, changed = codec_config._migrate_config({})
    assert changed is True
    assert cfg["config_version"] == codec_config.CONFIG_SCHEMA_VERSION


def test_migrate_v0_preserves_existing_keys():
    cfg, changed = codec_config._migrate_config(
        {"llm_model": "qwen", "step_budget": {"chat": 5}, "dashboard_token": "x"})
    assert changed is True
    assert cfg["config_version"] == 1
    assert cfg["llm_model"] == "qwen"
    assert cfg["step_budget"] == {"chat": 5}
    assert cfg["dashboard_token"] == "x"


def test_migrate_already_current_is_noop():
    cfg, changed = codec_config._migrate_config({"config_version": 1, "a": 1})
    assert changed is False
    assert cfg == {"config_version": 1, "a": 1}


def test_migrate_handles_garbage_version():
    """A non-int / negative config_version is treated as v0 and re-stamped."""
    for bad in ("nope", -3, None):
        cfg, changed = codec_config._migrate_config({"config_version": bad})
        assert cfg["config_version"] == 1
        assert changed is True


# ── load_config: write-back behavior ─────────────────────────────────────────


def test_load_config_stamps_and_writes_existing_v0_file(tmp_config):
    """A real v0 file (no config_version) gets stamped + written back."""
    tmp_config.write_text(json.dumps({"llm_model": "qwen", "wake_energy": 200}))
    cfg = codec_config.load_config()
    assert cfg["config_version"] == 1
    assert cfg["llm_model"] == "qwen"
    # Written back to disk
    on_disk = json.loads(tmp_config.read_text())
    assert on_disk["config_version"] == 1
    assert on_disk["wake_energy"] == 200


def test_load_config_v1_file_not_rewritten(tmp_config):
    """An already-v1 file must NOT be rewritten (idempotent — check mtime)."""
    tmp_config.write_text(json.dumps({"config_version": 1, "x": 1}))
    mtime_before = os.stat(tmp_config).st_mtime_ns
    import time
    time.sleep(0.01)
    cfg = codec_config.load_config()
    assert cfg["config_version"] == 1
    mtime_after = os.stat(tmp_config).st_mtime_ns
    assert mtime_after == mtime_before, "v1 config must not be rewritten"


def test_load_config_missing_file_no_create(tmp_config):
    """Fresh install (no file): version stamped in-memory, but NO file created."""
    assert not tmp_config.exists()
    cfg = codec_config.load_config()
    assert cfg["config_version"] == 1
    assert not tmp_config.exists(), "load_config must not create a config file just to stamp"


def test_load_config_corrupt_file_not_overwritten(tmp_config):
    """A corrupt/unparseable config must be preserved (not overwritten)."""
    tmp_config.write_text("{ this is not valid json ,,, ")
    cfg = codec_config.load_config()
    assert cfg == {}  # parse failed → empty in-memory
    # The corrupt file must be left intact for the user to repair
    assert "not valid json" in tmp_config.read_text()


def test_written_config_is_0600(tmp_config):
    tmp_config.write_text(json.dumps({"foo": "bar"}))
    codec_config.load_config()
    mode = os.stat(tmp_config).st_mode & 0o777
    assert mode == 0o600, f"migrated config must be 0600, got 0o{mode:o}"


def test_written_config_is_valid_json(tmp_config):
    tmp_config.write_text(json.dumps({"a": 1, "nested": {"b": 2}}))
    codec_config.load_config()
    # Must round-trip cleanly
    data = json.loads(tmp_config.read_text())
    assert data["config_version"] == 1
    assert data["nested"] == {"b": 2}


def test_load_config_second_call_idempotent(tmp_config):
    """First load stamps + writes; second load is a clean no-op read."""
    tmp_config.write_text(json.dumps({"k": "v"}))
    codec_config.load_config()  # stamps v1
    mtime1 = os.stat(tmp_config).st_mtime_ns
    import time
    time.sleep(0.01)
    cfg2 = codec_config.load_config()  # should NOT rewrite
    assert cfg2["config_version"] == 1
    assert os.stat(tmp_config).st_mtime_ns == mtime1
