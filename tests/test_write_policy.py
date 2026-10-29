"""P-006: AI-driven file writes only in the allowed folders.

The checks are called and their answers asserted; the only files written are
inside tmp_path with the allowed roots patched to it. No fake HOME.
"""
import importlib.util
import os
from pathlib import Path

import pytest

import codec_write_policy

_REPO = Path(__file__).resolve().parents[1]


def _file_write_skill():
    spec = importlib.util.spec_from_file_location("_t_fw", _REPO / "skills" / "file_write.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _checks():
    from routes.upload import _save_file_is_safe
    wf = _file_write_skill()
    return {
        "file_write": wf._is_safe_target,
        "save_file": _save_file_is_safe,
        "policy": codec_write_policy.check_write,
    }


REFUSED_ROOTS = [
    "~/codec-repo/scripts/auto_pull.sh",
    "~/codec-repo/codec_config.py",
    "~/.claude/hooks/block-service-actions.sh",
    "~/.claude/settings.json",
    "~/Library/LaunchAgents/com.example.agent.plist",
    "~/.cloudflared/config.yml",
    "~/.gitconfig",
    "~/.zlogout",
    "~/.codec/notes/abc.json",
]
REFUSED_INSIDE = [
    "~/Documents/Claude/Obsidian/_system/launch.mjs",
    "~/Documents/Claude/Obsidian/_system/notes.md",
    "~/Documents/run.sh",
    "~/Desktop/start.command",
    "~/Downloads/agent.plist",
    "~/Projects/build.mjs",
]
ALLOWED = [
    "~/Documents/notes.md",
    "~/Desktop/list.txt",
    "~/Downloads/table.csv",
    "~/Projects/demo/app.py",
    "~/codec-workspace/report.md",
    "/tmp/codec-note.txt",
]


@pytest.mark.parametrize("which", ["file_write", "save_file", "policy"])
@pytest.mark.parametrize("path", REFUSED_ROOTS + REFUSED_INSIDE)
def test_refused(which, path):
    ok, reason = _checks()[which](os.path.expanduser(path))
    assert ok is False, reason


@pytest.mark.parametrize("which", ["file_write", "save_file", "policy"])
@pytest.mark.parametrize("path", ALLOWED)
def test_allowed(which, path):
    ok, reason = _checks()[which](os.path.expanduser(path))
    assert ok is True, reason


def test_refusal_names_the_allowed_folders():
    ok, reason = codec_write_policy.check_write(os.path.expanduser("~/.gitconfig"))
    assert not ok and "Documents, Desktop, Downloads, Projects, codec-workspace and /tmp" in reason


@pytest.mark.parametrize("path", REFUSED_ROOTS + REFUSED_INSIDE)
def test_crew_file_write_refused(path, monkeypatch):
    import codec_agents

    def _never(*a, **k):
        raise AssertionError("a refused path must not be opened or created")

    # Even a broken check cannot touch the owner's real files in this test.
    monkeypatch.setattr(codec_agents, "open", _never, raising=False)
    monkeypatch.setattr(codec_agents.os, "makedirs", lambda *a, **k: None)
    out = codec_agents._file_write(f"path: {os.path.expanduser(path)}\ncontent: x")
    assert out.startswith("Error:")


def test_crew_file_write_allowed_in_root(tmp_path, monkeypatch):
    import codec_agents
    monkeypatch.setattr(codec_write_policy, "ALLOWED_ROOTS", [str(tmp_path)])
    target = tmp_path / "out" / "note.md"
    out = codec_agents._file_write(f"path: {target}\ncontent: hello")
    assert out.startswith("Written")
    assert target.read_text() == "hello"


def test_symlink_out_of_allowed_root_refused(tmp_path, monkeypatch):
    allowed = tmp_path / "docs"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (allowed / "link.md").symlink_to(outside / "target.md")
    monkeypatch.setattr(codec_write_policy, "ALLOWED_ROOTS", [str(allowed)])
    ok, _ = codec_write_policy.check_write(str(allowed / "link.md"))
    assert ok is False
