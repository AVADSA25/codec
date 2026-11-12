"""Vibe: the sandbox of an agent command in an attached folder (P-050, owner decision R-17, and R-14 for secrets).

The attached profile only adds refusals to today's profile: no write to a .git entry, a git config or a hooks
folder at any depth (nested repos, submodules, worktrees), and no reading what is in a secret-looking file. A
created project's profile is byte for byte today's (two copies kept in tests/fixtures). The rules are checked as
text and in the real sandbox on this Mac (skipped elsewhere); every command carries the marker `vibe-run-test`.
"""
from __future__ import annotations

import difflib
import os
import shlex
from pathlib import Path

import pytest

import codec_vibe_agent as va
import codec_vibe_agent_cmd as cmd
import codec_vibe_projects as vp
from vibe_agent_fakes import (  # noqa: F401  (fixtures are used by name)
    agent_env, audits, cfg, scripted, wait_finished, wait_for,
)

needs_sandbox = pytest.mark.skipif(not cmd.available(), reason="macOS sandbox-exec is not available here")
FIX = Path(__file__).parent / "fixtures"
ARGS = ("/work/vibe/proj-abc123", "/work/vibe", "/Users/someone", "/private/var/folders/x/T/vibe-agent-1")


# ── the profile, as text ──

def test_r17_a_created_projects_profile_is_byte_identical_to_todays_copy():
    assert cmd.profile(*ARGS, False) == (FIX / "vibe_agent_profile_created_off.sb").read_text()
    assert cmd.profile(*ARGS, True) == (FIX / "vibe_agent_profile_created_on.sb").read_text()
    assert cmd.profile(*ARGS, False, attached=False) == cmd.profile(*ARGS, False)


def test_r17_the_attached_profile_only_adds_refusals_and_widens_nothing():
    for network in (False, True):
        base = cmd.profile(*ARGS, network).splitlines()
        att = cmd.profile(*ARGS, network, attached=True).splitlines()
        diff = [d for d in difflib.ndiff(base, att) if d[:2] in ("- ", "+ ")]
        assert diff and all(d.startswith("+ ") for d in diff), "no line of today's profile is removed or changed"
        added = [d[2:] for d in diff]
        for line in added:
            assert (line.startswith(";;") or line in ("(deny file-write*", "(deny file-read-data")
                    or (line.startswith('    (regex #"^') and line.rstrip(")").endswith('"'))), line
        assert not any("allow" in line for line in added if not line.startswith(";;"))
        text = "\n".join(att)
        assert text.index("(deny file-write*\n    (literal") < text.index("an attached folder") < text.index(";; programs it may start")


def test_r17_the_attached_rules_cover_nested_git_entries_configs_hooks_and_secret_reads():
    p = cmd.profile("/Users/me/Projects/my app (v2)", "/Users/me/codec-workspace/vibe", "/Users/me", "/tmp/t", False, attached=True)
    at = r'^/Users/me/Projects/my app \(v2\)/(.+/)?'
    git = at + "[.][Gg][Ii][Tt]"
    for rx in (git + "$", git + "/(.+/)?[Cc][Oo][Nn][Ff][Ii][Gg]$", git + "/(.+/)?[Hh][Oo][Oo][Kk][Ss]$",
               git + "/(.+/)?[Hh][Oo][Oo][Kk][Ss]/", git + "/(.+/)?[Cc][Oo][Nn][Ff][Ii][Gg][.][Ww][Oo][Rr][Kk][Tt][Rr][Ee][Ee]$"):
        assert f'(regex #"{rx}")' in p, rx
    reads = p[p.index("(deny file-read-data"):]
    for name in vp._BLOCKED_NAME_PREFIXES + vp._BLOCKED_EXTS:
        assert cmd._ci(name) in reads, name


def test_r17_an_attached_path_the_profile_cannot_hold_is_refused():
    for bad in ('/Users/me/Projects/ev"il', "/Users/me/Projects/back\\slash", "/Users/me/Projects/new\nline", "relative/app"):
        with pytest.raises(cmd.Refused):
            cmd.profile(bad, "/w/vibe", "/Users/me", "/tmp/t", False, attached=True)


# ── the real sandbox (this Mac) ──

@pytest.fixture
def attached_box(tmp_path):
    root = tmp_path / "vibe"
    rec = root / "my-app-abc123"
    (rec / ".vibe").mkdir(parents=True)
    (rec / ".vibe" / "checkpoints.jsonl").write_text("history\n")
    proj = tmp_path / "Projects" / "my app (v2).é"
    (proj / ".git" / "hooks").mkdir(parents=True)
    (proj / ".git" / "config").write_text("[core]\n")
    (proj / ".git" / "modules" / "m" / "hooks").mkdir(parents=True)
    (proj / "sub" / ".git").mkdir(parents=True)
    (proj / "sub" / ".git" / "config").write_text("[core]\n")
    (proj / "other").mkdir()
    (proj / ".env").write_text("SECRET-CANARY-ENV\n")
    (proj / "sub" / "server.KEY").write_text("SECRET-CANARY-KEY\n")
    (proj / "credentials.json").write_text("SECRET-CANARY-CRED\n")
    (proj / "a.txt").write_text("plain text\n")
    (tmp_path / "Projects" / "sibling").mkdir()
    return proj, root, rec


def go(box, command, attached=True):
    proj, root, _rec = box
    return cmd.run(proj, root, command + " # vibe-run-test", attached=attached)


@needs_sandbox
def test_r17_a_command_writes_in_the_attached_folder_but_no_git_entry_config_or_hooks_at_any_depth(attached_box):
    proj = attached_box[0]
    r = go(attached_box, "echo ok > ok.txt; mkdir -p deep/er && echo ok > deep/er/b.txt; echo ok > sub/.git/HEAD; "
                         "echo ok > .git/index; echo x > .git/config; echo x > .git/hooks/pre-commit; "
                         "echo x > sub/.git/config; mkdir sub/.git/hooks; echo x > sub/.git/config.worktree; "
                         "echo x > .git/modules/m/hooks/post-checkout; echo x > .git/modules/m/config; "
                         "touch other/.git; mkdir other/.GIT; mv sub/.git sub/gone; true")
    for made in ("ok.txt", "deep/er/b.txt", "sub/.git/HEAD", ".git/index"):
        assert (proj / made).read_text() == "ok\n", made
    assert (proj / ".git" / "config").read_text() == "[core]\n" and (proj / "sub" / ".git" / "config").read_text() == "[core]\n"
    for refused in (".git/hooks/pre-commit", "sub/.git/hooks", "sub/.git/config.worktree", ".git/modules/m/hooks/post-checkout",
                    ".git/modules/m/config", "other/.git", "other/.GIT", "sub/gone"):
        assert not os.path.lexists(proj / refused), refused
    assert (proj / "sub" / ".git").is_dir() and r.denied


@needs_sandbox
def test_r17_a_command_cannot_write_outside_the_folder_or_touch_vibes_record(attached_box):
    proj, _root, rec = attached_box
    q = shlex.quote
    r = go(attached_box, f"echo x > ../parent.txt; echo x > ../sibling/x.txt; echo x > {q(str(rec / '.vibe' / 'evil.txt'))}; "
                         f"cat {q(str(rec / '.vibe' / 'checkpoints.jsonl'))}; rm -f {q(str(rec / '.vibe' / 'checkpoints.jsonl'))}; true")
    assert not (proj.parent / "parent.txt").exists() and not (proj.parent / "sibling" / "x.txt").exists()
    assert not (rec / ".vibe" / "evil.txt").exists() and (rec / ".vibe" / "checkpoints.jsonl").read_text() == "history\n"
    assert "history" not in r.output and r.denied


@needs_sandbox
def test_r14_an_agent_command_cannot_read_a_secret_file_of_an_attached_folder(attached_box):
    proj = attached_box[0]
    r = go(attached_box, "cat .env; cat sub/server.KEY; cat credentials.json; cp .env copy.txt; "
                         "python3 -c \"print(open('.env').read())\"; ls -l .env; cat a.txt; true")
    assert "SECRET-CANARY" not in r.output and "plain text" in r.output
    assert ".env" in r.output and r.denied                       # its name and size are visible, not its content
    assert not (proj / "copy.txt").exists() or "SECRET" not in (proj / "copy.txt").read_text()
    assert (proj / ".env").read_text() == "SECRET-CANARY-ENV\n"


@needs_sandbox
def test_r17_a_created_project_keeps_todays_rules_for_the_same_commands(attached_box):
    proj = attached_box[0]
    r = go(attached_box, "cat .env; mkdir -p nested/.git && echo x > nested/.git/config; echo x > .git/config; true", attached=False)
    assert "SECRET-CANARY-ENV" in r.output                       # today's profile: unchanged, not narrowed either
    assert (proj / "nested" / ".git" / "config").read_text() == "x\n"
    assert (proj / ".git" / "config").read_text() == "[core]\n"   # D-23, as before


# ── the agent uses the attached profile for an attached folder only ──

class FakeCommands:
    def __init__(self, monkeypatch):
        self.calls = []
        monkeypatch.setattr(va, "_run_command", self)
        monkeypatch.setattr(cmd, "available", lambda: True)

    def __call__(self, project, root, command, **kw):
        self.calls.append({"project": project, "root": root, "command": command, **kw})
        return cmd.Result(0, "1 passed", 0.1)


def _run_check(pid):
    run_id = va.start_run(pid, "Run the tests", can_run=True)["run"]

    def approval():
        return next((e for e in va.get_run(pid, run_id)["events"] if e["type"] == "approval"), None)
    ev = wait_for(approval)
    va.approve_run(pid, run_id, ev["id"], "once", False)
    return wait_finished(pid, run_id)


def test_r17_the_agent_runs_an_attached_folders_command_in_the_folder_with_the_attached_profile(tmp_path, monkeypatch, scripted):
    (tmp_path / "Projects" / "app").mkdir(parents=True)
    (tmp_path / "Projects" / "app" / "index.html").write_text("<h1>x</h1>\n")
    monkeypatch.setenv("CODEC_VIBE_ATTACH_ROOTS", str(tmp_path / "Projects"))
    fake = FakeCommands(monkeypatch)
    pid = vp.attach_folder(str(tmp_path / "Projects" / "app"))["id"]
    scripted(["Check.\n@@ run_check\nnpm test\n", "@@ finish\nok\n"])
    _run_check(pid)
    created = vp.create_project("Made", [{"path": "index.html", "content": "x\n"}])["id"]
    scripted(["Check.\n@@ run_check\nnpm test\n", "@@ finish\nok\n"])
    _run_check(created)
    att, made = fake.calls
    assert att["project"] == tmp_path / "Projects" / "app" and att["attached"] is True
    assert made["project"] == vp.project_dir(created) and "attached" not in made
