"""User skills folder (docs/USER-SKILLS-DIR-DESIGN.md).

Every skill registry reads the built-in folder, then the owner's folder
(~/.codec/skills): a built-in name always wins, a user file is never trusted
by the manifest, and /api/skill/approve writes approved skills there. Nothing
here touches ~/.codec: tmp folders only.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

SAFE = 'SKILL_TRIGGERS = ["say {word}"]\nSKILL_DESCRIPTION = "x"\n\ndef run(task, app="", ctx=""):\n    return "said {word}"\n'
DANGER = ('import subprocess\nSKILL_TRIGGERS = ["run it"]\nSKILL_DESCRIPTION = "x"\n\n'
          'def run(task, app="", ctx=""):\n    return subprocess.run(["true"]).returncode\n')


def _skill(folder: Path, fname: str, src: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / fname
    p.write_text(src, encoding="utf-8")
    return p


@pytest.fixture
def dirs(tmp_path):
    return tmp_path / "builtin", tmp_path / "user"


def test_the_registry_reads_the_builtins_then_the_user_folder(dirs):
    from codec_skill_registry import SkillRegistry
    builtin, user = dirs
    _skill(builtin, "alpha.py", SAFE.format(word="builtin"))
    _skill(user, "beta.py", SAFE.format(word="beta"))
    _skill(user, "alpha.py", SAFE.format(word="shadow"))  # same name as a built-in
    reg = SkillRegistry(str(builtin), str(user))
    assert reg.scan() == 2 and sorted(reg.names()) == ["alpha", "beta"]
    assert reg.load("alpha").run("") == "said builtin", "a built-in name always wins"
    assert reg.load("beta").run("") == "said beta"


def test_a_user_file_is_never_trusted_by_the_manifest(dirs):
    from codec_skill_registry import SkillRegistry
    builtin, user = dirs
    _skill(builtin, "danger_tool.py", DANGER)
    (builtin / ".manifest.json").write_text(json.dumps(
        {"skills": {"danger_tool.py": hashlib.sha256(DANGER.encode()).hexdigest()}}))
    _skill(user, "copycat.py", DANGER)  # the same bytes, under another name
    reg = SkillRegistry(str(builtin), str(user))
    reg.scan()
    assert reg.load("danger_tool") is not None, "the pinned built-in still loads"
    assert reg.load("copycat") is None, "the user copy goes through the AST gate and is refused"


def test_the_ast_gate_decides_for_user_files(dirs):
    from codec_skill_registry import SkillRegistry
    builtin, user = dirs
    builtin.mkdir()
    _skill(user, "ok_skill.py", SAFE.format(word="ok"))
    _skill(user, "bad_skill.py", DANGER)
    reg = SkillRegistry(str(builtin), str(user))
    reg.scan()
    assert reg.load("ok_skill").run("") == "said ok"
    assert reg.load("bad_skill") is None


def test_underscore_files_and_subfolders_are_not_read(dirs):
    from codec_skill_registry import SkillRegistry
    builtin, user = dirs
    builtin.mkdir()
    _skill(user, "_private.py", SAFE.format(word="p"))
    _skill(user / "_dormant-2026-10-07", "old.py", SAFE.format(word="old"))
    _skill(user / "sub", "nested.py", SAFE.format(word="n"))
    reg = SkillRegistry(str(builtin), str(user))
    assert reg.scan() == 0 and reg.names() == []


def test_the_user_folder_is_off_when_it_is_the_builtin_folder_or_missing(dirs):
    from codec_skill_registry import SkillRegistry
    builtin, user = dirs
    _skill(builtin, "alpha.py", SAFE.format(word="a"))
    assert SkillRegistry(str(builtin), str(builtin)).user_dir is None
    reg = SkillRegistry(str(builtin), str(user))  # user folder does not exist
    assert reg.scan() == 1 and reg.names() == ["alpha"]


class _Req:
    def __init__(self, payload):
        self._p = payload

    async def json(self):
        return self._p


def test_approve_writes_to_the_user_folder_owner_only(tmp_path, monkeypatch):
    import codec_config
    import routes.skills as rs
    user = tmp_path / "user_skills"
    monkeypatch.setattr(codec_config, "USER_SKILLS_DIR", str(user))
    monkeypatch.setattr(rs, "_reviews_dir", lambda: str(tmp_path / "reviews"))
    code = 'SKILL_NAME = "moon"\nSKILL_DESCRIPTION = "x"\nSKILL_TRIGGERS = []\ndef run(task, app="", ctx=""):\n    return "ok"\n'
    rs._pending_skills["rev_user"] = {"code": code, "filename": "moon_helper.py"}
    resp = asyncio.run(rs.skill_approve(_Req({"review_id": "rev_user"})))
    assert getattr(resp, "status_code", 200) == 200, resp
    path = user / "moon_helper.py"
    assert path.read_text() == code and resp["path"] == str(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(user.stat().st_mode) == 0o700
    assert not list(user.glob("*.tmp")), "no half-written file left behind"
    assert not (REPO / "skills" / "moon_helper.py").exists(), "never the repo's built-in folder"
    rs._pending_skills["rev_pinned"] = {"code": code, "filename": "calculator.py"}
    resp = asyncio.run(rs.skill_approve(_Req({"review_id": "rev_pinned"})))
    assert resp.status_code == 400 and not (user / "calculator.py").exists(), "a built-in name is still refused"


def test_learning_and_self_improve_see_user_skills(tmp_path, monkeypatch):
    import codec_config
    import codec_self_improve
    import routes.learning as rl
    user = tmp_path / "user_skills"
    _skill(user, "moon_helper.py", 'SKILL_NAME = "moon"\n' + SAFE.format(word="m"))
    monkeypatch.setattr(codec_config, "USER_SKILLS_DIR", str(user))
    monkeypatch.setattr(codec_self_improve, "USER_SKILLS_DIR", str(user))
    assert "moon_helper" in rl._installed(), "an approved skill shows as installed before a restart"
    names = codec_self_improve._existing_skill_names()
    assert {"moon_helper", "moon"} <= names, "self-improve does not propose it again"


def test_every_registry_gets_the_user_folder():
    for mod in ("codec_dispatch.py", "codec_mcp.py", "codec_voice.py", "codec_agents.py", "codec_autopilot.py",
                "codec_slash_commands.py"):
        src = (REPO / mod).read_text(encoding="utf-8")
        assert "SkillRegistry(SKILLS_DIR, USER_SKILLS_DIR)" in src and "SkillRegistry(SKILLS_DIR)" not in src, mod
    import codec_config
    import codec_dispatch
    # The module-level registry keeps the folder it was built with (import order decides
    # which test's value that is), so check what matters: it has a user folder, not the repo's.
    assert codec_dispatch.registry.user_dir
    assert os.path.realpath(codec_dispatch.registry.user_dir) != os.path.realpath(REPO / "skills")
    assert codec_config.USER_SKILLS_DIR == os.environ["CODEC_USER_SKILLS_DIR"], "the suite never reads the owner's folder"
