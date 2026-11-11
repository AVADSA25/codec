"""Vibe agent: the owner's rules for "Always allow", like `git diff *` (P-048, docs/VIBE-ALLOW-RULES-DESIGN.md).

The matcher is checked against many ways to slip a second command or an expansion past it, in the style of
tests/test_dangerous_command.py: any doubt means no match, and the card is shown. Programs are looked up on a PATH
of fake programs in a temp folder, so the tests do not depend on what is installed. The sandbox runner is the
recording fake of tests/test_vibe_agent_cmd_run.py, except one real-sandbox test (macOS only, marker vibe-run-test).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codec_vibe_agent as va
import codec_vibe_agent_cmd as cmd
import codec_vibe_agent_rules as rules
import codec_vibe_projects as vp
from routes.vibe_agent import router
from route_table import route_methods
from vibe_agent_fakes import (  # noqa: F401  (fixtures are used by name)
    agent_env, audits, cfg, end_of, make_project, scripted, wait_finished, wait_for,
)

REPO = Path(__file__).resolve().parent.parent
SRC = (REPO / "static" / "vibe-agent.js").read_text(encoding="utf-8")
REMOTE = {"cf-connecting-ip": "203.0.113.9"}
TOOLS = ("git", "ls", "wc", "cat", "touch", "grep", "node")


def _program(folder: Path, name: str) -> Path:
    f = folder / name
    f.write_text("#!/bin/sh\nexit 0\n")
    f.chmod(0o755)
    return f


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    """A PATH holding only fake programs; command_env() answers it, as the sandbox runner would build it."""
    b = tmp_path / "bin"
    b.mkdir()
    for name in TOOLS:
        _program(b, name)
    env = {"PATH": str(b), "LC_ALL": "C.UTF-8"}
    monkeypatch.setattr(rules, "command_env", lambda: dict(env))
    return b


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


def m(command, rule_list=("git diff *",), project=None, env=None):
    return rules.match(None if rule_list is None else list(rule_list), command, project, env=env)


# ── the rule syntax ──

@pytest.mark.parametrize("text, stored", [
    ("git diff *", "git diff *"),
    ("  git diff *  ", "git diff *"),
    ("run_check(git diff *)", "git diff *"),
    ("git log --oneline *", "git log --oneline *"),
    ("git status *", "git status *"),
    ("ls *", "ls *"),
    ("ls -la *", "ls -la *"),
    ("wc -l *", "wc -l *"),
    ("cat *", "cat *"),
    ("grep -n --color=never *", "grep -n --color=never *"),
])
def test_rules_the_editor_accepts_and_how_they_are_stored(text, stored, fake_bin, proj):
    assert rules.check_rule(text, project=proj) == stored
    assert rules.check_rule(text) == stored


@pytest.mark.parametrize("text", [
    None, 5, "", "   ", "*", " * ", "**", "git diff", "git diff**", "git * diff", "git diff * *", "git  diff *",
    "git\tdiff *", "git diff\t*", "git diff *\n", "git\ndiff *", "a b c d e f g *", "ls " + "a" * 200 + " *",
    "./build.sh *", "scripts/build *", "/usr/bin/git diff *", "../x *", "~/bin/x *", "$HOME/x *", "${x} *",
    "git diff 'x' *", 'git diff "x" *', "git diff ~x *", "git diff x~ *", "git diff ^x *", "git diff $x *",
    "git diff `x` *", "git diff ;x *", "git diff (x) *", "git diff [x] *", "git diff {a,b} *", "git diff ? *",
    "gіt diff *", "git diff *", "git​diff *", "ｇit diff *", "FOO=1 git diff *", "-x *",
    ".hidden *", "_x$ *", "git diff \\ *",
])
def test_rule_shapes_the_editor_refuses(text, fake_bin, proj):
    with pytest.raises(rules.RuleRefused):
        rules.check_rule(text, project=proj)


REFUSED_PROGRAMS = [
    # the floor named on the board card and in the task
    "npm", "npx", "yarn", "pnpm", "node", "python", "python3", "pip", "uv", "bash", "sh", "zsh", "env", "sudo", "make",
    # case and version spellings
    "NPM", "Python3", "Bash", "python3.12", "pip3", "pip3.11", "node18", "gcc-13", "perl5.30", "lua5.4",
    # more shells, interpreters, managers, runners, wrappers, builtins, destructive programs, scripts
    "dash", "fish", "pwsh", "busybox", "deno", "bun", "ruby", "perl", "php", "osascript", "swift", "awk", "sed", "dc",
    "vim", "uvx", "pipx", "poetry", "cargo", "brew", "gem", "pytest", "jest", "vitest", "tox", "cmake", "ninja", "just",
    "go", "gcc", "clang", "cc", "rustc", "xcrun", "xcodebuild", "eslint", "prettier", "mypy", "pylint", "docker",
    "xargs", "find", "fd", "nohup", "timeout", "watch", "ssh", "rsync", "open", "launchctl", "tar", "zip", "rg", "sort",
    "sqlite3", "defaults", "eval", "exec", "source", "command", "builtin", "echo", "time", "trap", "export", "cd",
    "rm", "rmdir", "mv", "dd", "shred", "truncate", "build.sh", "run.py", "app.js", "x.command", "tool.mjs", "node.exe",
]


@pytest.mark.parametrize("name", REFUSED_PROGRAMS)
def test_programs_that_run_code_or_other_commands_cannot_be_rules(name, fake_bin, proj):
    for text in (f"{name} *", f"{name} --version *", f"{name} run test *"):
        with pytest.raises(rules.RuleRefused) as e:
            rules.check_rule(text, project=proj)
        assert "cannot be a rule" in str(e.value), (text, str(e.value))
        with pytest.raises(rules.RuleRefused):
            rules.check_rule(text)                  # the static check alone refuses too (it runs on every read)


@pytest.mark.parametrize("text", [
    "git *", "git -c x *", "git --no-pager diff *", "git -C .. diff *", "git config *", "git fetch *", "git pull *",
    "git push *", "git clone *", "git grep *", "git rebase *", "git branch *", "git tag *", "git stash *",
    "git submodule *", "git bisect *", "git difftool *", "git mergetool *", "git help *", "git Diff *", "git reflog *",
])
def test_git_rules_must_name_a_read_only_git_command(text, fake_bin, proj):
    with pytest.raises(rules.RuleRefused) as e:
        rules.check_rule(text, project=proj)
    assert "read-only" in str(e.value)


def test_every_read_only_git_command_can_be_a_rule(fake_bin, proj):
    for sub in sorted(rules.GIT_READ_ONLY):
        assert rules.check_rule(f"git {sub} *", project=proj) == f"git {sub} *"


def test_dangerous_words_cannot_be_a_rule(fake_bin, proj):
    for text in ("git log --output=/etc/passwd *", "cat /etc/shadow *", "cat /Users/x/.ssh/id_rsa *"):
        with pytest.raises(rules.RuleRefused) as e:
            rules.check_rule(text, project=proj)
        assert "dangerous" in str(e.value)


def test_a_rule_cannot_name_the_projects_own_files_or_a_missing_program(fake_bin, proj):
    _program(proj, "deploy")
    (proj / "tools").mkdir()
    (proj / "Makefile.local").write_text("x\n")
    for text, why in (("deploy *", "in this project"), ("tools *", "in this project"), ("nosuchprogram *", "No program"),
                      ("Makefile.local *", "in this project")):
        with pytest.raises(rules.RuleRefused) as e:
            rules.check_rule(text, project=proj)
        assert why in str(e.value), text


def test_a_link_that_leads_to_an_interpreter_cannot_be_a_rule(fake_bin, proj):
    (fake_bin / "fmt-tool").symlink_to(fake_bin / "node")
    with pytest.raises(rules.RuleRefused) as e:
        rules.check_rule("fmt-tool *", project=proj)
    assert "leads to node" in str(e.value)
    assert m("fmt-tool --check", ["fmt-tool *"], proj) is None


def test_a_program_found_in_the_project_through_the_path_cannot_be_a_rule(fake_bin, proj, monkeypatch):
    (proj / "bin").mkdir()
    _program(proj / "bin", "lint-it")
    monkeypatch.setattr(rules, "command_env", lambda: {"PATH": f"{proj / 'bin'}:{fake_bin}"})
    with pytest.raises(rules.RuleRefused) as e:
        rules.check_rule("lint-it *", project=proj)
    assert "inside this project" in str(e.value)


# ── the matcher: what matches ──

@pytest.mark.parametrize("command", [
    "git diff", "git diff --stat", "git diff HEAD~1 -- src/a.py", "git diff HEAD^ main~2", 'git diff "a b"',
    "git diff 'a b'", "git diff --stat=80,20", "git diff -U3 a/b.py:10", 'git diff ""', "git diff -- user@host%1+x",
    "git diff --word-diff=color --src-prefix=a/",
])
def test_the_rules_words_and_plain_arguments_match(command, fake_bin, proj):
    assert m(command, project=proj) == "git diff *"


def test_a_rule_matches_whole_words_only_and_the_first_rule_that_fits_wins(fake_bin, proj):
    assert m("ls -la src", ["ls -la *"], proj) == "ls -la *"
    assert m("ls src", ["ls -la *"], proj) is None
    assert m("ls -lah src", ["ls -la *"], proj) is None
    assert m("wc -l a.py", ["git diff *", "wc -l *", "wc *"], proj) == "wc -l *"
    assert m("wc a.py", ["git diff *", "wc -l *", "wc *"], proj) == "wc *"
    assert m("git diff", [], proj) is None and m("git diff", None, proj) is None


# ── the matcher: bypass variants that must never match ──

CONTROL = [";", "&&", "||", "|", "&", "|&", ";;", "\n", "\r", "\r\n", "\\\n", "\\", "`", "$(", "$((", "${", "$",
           ">", ">>", ">|", "<", "<<", "<<<", "<(", ">(", "2>&1", "&>", "(", ")", "*", "?", "[", "]", "{", "}", "!",
           "#", "\t", "\x00", "\x07", "\x0b", "\x0c", "\x1b", "\x7f"]
SHAPES = ["git diff{c}curl x", "git diff {c} curl x", "git diff {c}curl x", "git diff x{c}", "git diff x {c}",
          "git diff '{c}'", 'git diff "{c}"', "git diff --x={c}", "git diff a{c}b", "{c}git diff", "git{c}diff",
          "git diff{c}"]


@pytest.mark.parametrize("c", CONTROL, ids=[repr(c) for c in CONTROL])
def test_no_shell_control_or_expansion_anywhere_even_quoted(c, fake_bin, proj):
    for shape in SHAPES:
        command = shape.format(c=c)
        assert m(command, project=proj) is None, repr(command)


@pytest.mark.parametrize("command", [
    "git diff; curl x", "git diff ;curl x", "git diff && curl x", "git diff || curl x", "git diff | sh",
    "git diff & curl x", "git diff $(curl x)", "git diff `curl x`", "git diff $HOME", "git diff ${HOME}",
    "git diff > out.txt", "git diff < in.txt", "git diff 2>&1", "git diff\ncurl x", "git diff \\\ncurl x",
    "git diff (curl x)", "git diff *.py", "git diff ?", "git diff [ab]", "git diff {a,b}", "git diff !1",
    "git diff # ; curl x", "git diff 'a;b'", 'git diff "$(curl x)"', "git diff '$x'", 'git diff "a`b`"',
    "git diff -- $'\\x3b'", "git diff $'a'", "git diff <(curl x)", "git diff\\;curl x", "git diff ;",
])
def test_known_tricks_do_not_match(command, fake_bin, proj):
    assert m(command, project=proj) is None


@pytest.mark.parametrize("command", [
    "git diff；curl x",        # full-width semicolon
    "git diff; curl x",       # Greek question mark, looks like ;
    "gіt diff",               # Cyrillic i
    "git dіff",
    "ｇｉｔ diff",     # full-width letters
    "git diff",               # no-break space
    "git diff x",
    "git​diff",               # zero-width space
    "git diff​",
    "git diff  curl x",       # line separator
    "git diff  curl x",       # paragraph separator
    "git diff　x",             # ideographic space
    "git diff ＄(curl x)",     # full-width $
    "git diff ‮x",            # right-to-left override
    "git diff ﻿x",            # byte-order mark
    "git diff é",             # any letter outside ASCII
])
def test_unicode_look_alikes_and_invisible_characters_do_not_match(command, fake_bin, proj):
    assert m(command, project=proj) is None


@pytest.mark.parametrize("command", [
    "git  diff", " git diff", "git difftool", "git diffx", "git differ --stat", "gitdiff", "git-diff",
    '"git" diff', "'git' diff", 'g""it diff', 'git "diff"', 'git diff"" --stat', 'git di"ff"', "GIT diff", "Git diff",
    "env git diff", "GIT_DIR=x git diff", "git -C .. diff", "git --no-pager diff", "git -c x=y diff", "command git diff",
    "exec git diff", "time git diff", "nohup git diff", "sudo git diff", "./git diff", "/usr/bin/git diff",
    "git diff ~", "git diff ~/x", "git diff ~root", "git diff --x=~/y", "git diff a:~/y", "git diff x ~", "git diff ^x",
    "git diff HEAD^^", 'git diff "x', "git diff 'x", "git diff '", "git diff " + "a" * 600, "",
])
def test_spacing_quoting_and_prefix_tricks_do_not_match(command, fake_bin, proj):
    assert m(command, project=proj) is None


def test_things_that_are_not_text_do_not_match(fake_bin, proj):
    for command in (None, 5, b"git diff", ["git", "diff"]):
        assert m(command, project=proj) is None


def test_a_program_the_shell_finds_in_the_project_does_not_match(fake_bin, proj):
    _program(proj, "git")
    (proj / "tools").mkdir()
    _program(proj / "tools", "git")
    for path in (f".:{fake_bin}", f":{fake_bin}", f"tools:{fake_bin}", f"{proj / 'tools'}:{fake_bin}", f"{proj}:{fake_bin}"):
        assert m("git diff", project=proj, env={"PATH": path}) is None, path
    assert m("git diff", project=proj, env={"PATH": f"{fake_bin}:"}) == "git diff *"     # found outside first
    assert m("git diff", project=proj, env={"PATH": f"{fake_bin}:."}) == "git diff *"
    assert m("git diff", project=proj, env={"PATH": "/nonexistent-folder"}) is None      # not found at all
    link = fake_bin / "lnk"
    link.symlink_to(proj / "git")                                                       # a link into the project
    assert m("lnk x", ["lnk *"], proj, env={"PATH": str(fake_bin)}) is None


@pytest.mark.parametrize("name", ["BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "PS4", "BASH_FUNC_git%%", "BASH_FUNC_x()"])
def test_an_environment_that_runs_code_before_the_command_means_no_match(name, fake_bin, proj):
    assert m("git diff", project=proj, env={"PATH": str(fake_bin)}) == "git diff *"
    assert m("git diff", project=proj, env={"PATH": str(fake_bin), name: "/tmp/x"}) is None


def test_a_stored_rule_the_editor_would_refuse_never_matches(fake_bin, proj):
    for bad in ("bash *", "npm *", "git *", "git  diff *", "*", "git diff", "rm -rf *", "node *", "env *"):
        prog = bad.split(" ")[0]
        assert m(f"{prog} x", [bad], proj) is None, bad


def test_the_command_is_split_the_same_way_bash_splits_it():
    assert rules.command_words('git diff "a b" \'c d\' e""f') == ["git", "diff", "a b", "c d", "ef"]
    assert rules.command_words("git diff HEAD~1") == ["git", "diff", "HEAD~1"]
    for bad in ('git diff "x', "git diff $x", "git diff\tx", "git diff \\x"):
        assert rules.command_words(bad) is None


# ── storage ──

def test_rules_are_stored_in_project_json_next_to_the_rest(fake_bin):
    pid = make_project()
    before = json.loads((vp.project_dir(pid) / ".vibe" / "project.json").read_text())
    assert vp.allow_rule(pid, "run_check(git diff *)") == [{"rule": "git diff *"}]
    vp.allow_rule(pid, "ls *")
    assert vp.allow_rule(pid, "git diff *") == [{"rule": "ls *"}, {"rule": "git diff *"}]       # again: moved to the end
    after = json.loads((vp.project_dir(pid) / ".vibe" / "project.json").read_text())
    assert {k: v for k, v in after.items() if k != "allowed_rules"} == before
    assert after["allowed_rules"] == [{"rule": "ls *"}, {"rule": "git diff *"}]
    assert vp.allowed_commands(pid) == []


def test_a_refused_rule_is_a_400_with_the_reason_and_nothing_is_stored(fake_bin, audits):
    pid = make_project()
    for text in ("npm *", "python3 -c *", "./build.sh *", "*", "git *", 5, None):
        with pytest.raises(vp.VibeError) as e:
            vp.allow_rule(pid, text)
        assert e.value.status == 400 and e.value.message
    assert vp.allowed_rules(pid) == [] and "allowed_rules" not in json.loads((vp.project_dir(pid) / ".vibe" / "project.json").read_text())
    assert not [x for e, x in audits if e == "vibe_agent_allow"]


def test_the_list_is_capped_and_a_rule_can_be_forgotten(fake_bin, audits):
    pid = make_project()
    letters = "abcdefghijklmnopqrstuvwxyz"[:vp.MAX_ALLOWED_RULES + 3]
    for c in letters:
        vp.allow_rule(pid, f"wc -{c} *")
    items = vp.allowed_rules(pid)
    assert len(items) == vp.MAX_ALLOWED_RULES and items[0]["rule"] == f"wc -{letters[3]} *" and items[-1]["rule"] == f"wc -{letters[-1]} *"
    assert va.command_rules(pid) == items
    left = va.forget_command_rule(pid, f"wc -{letters[5]} *")
    assert len(left) == vp.MAX_ALLOWED_RULES - 1 and all(e["rule"] != f"wc -{letters[5]} *" for e in left)
    with pytest.raises(vp.VibeError) as e:
        va.forget_command_rule(pid, f"wc -{letters[5]} *")
    assert e.value.status == 404
    with pytest.raises(vp.VibeError) as e:
        va.forget_command_rule(pid, 5)
    assert e.value.status == 400
    allows = [x for e, x in audits if e == "vibe_agent_allow"]
    assert {x["action"] for x in allows} == {"allow_rule", "forget_rule"} and all(x["network"] is False for x in allows)
    assert "wc" not in json.dumps(allows)                                             # never the rule text


def test_rules_written_into_project_json_by_hand_are_checked_again_when_read(fake_bin):
    pid = make_project()
    path = vp.project_dir(pid) / ".vibe" / "project.json"
    meta = json.loads(path.read_text())
    meta["allowed_rules"] = [{"rule": "bash *"}, {"rule": "git diff *"}, {"rule": "git  diff *"}, "ls *", {"rule": 5},
                             {"rule": "npm test *"}, {"rule": "git diff *"}, {"rule": " ls * "}, {"rule": "git push *"}]
    path.write_text(json.dumps(meta))
    assert vp.allowed_rules(pid) == [{"rule": "git diff *"}]


def test_nothing_in_a_run_writes_a_rule():
    agent = (REPO / "codec_vibe_agent.py").read_text(encoding="utf-8")
    assert agent.count("vp.allow_rule(") == 1 and "def add_command_rule(pid: str, rule) -> list:" in agent
    body = agent[agent.index("def add_command_rule("):]
    assert "return vp.allow_rule(pid, rule)" in body[:400]
    routes = (REPO / "routes" / "vibe_agent.py").read_text(encoding="utf-8")
    assert routes.count("va.add_command_rule") == 1
    for name in ("codec_vibe_agent_proto.py", "codec_vibe_playbooks.py", "codec_vibe_agent_hooks.py", "codec_vibe_agent_cmd.py"):
        assert "allow_rule" not in (REPO / name).read_text(encoding="utf-8"), name


# ── in a run ──

class FakeCommands:
    def __init__(self, monkeypatch):
        self.calls = []
        monkeypatch.setattr(va, "_run_command", self)
        monkeypatch.setattr(cmd, "available", lambda: True)

    def __call__(self, project, root, command, **kw):
        self.calls.append({"command": command, **kw})
        return cmd.Result(0, "ok", 0.1)


@pytest.fixture
def fake(monkeypatch):
    return FakeCommands(monkeypatch)


def start(pid):
    return va.start_run(pid, "Check the changes", can_run=True)["run"]


def approval_of(pid, run_id):
    return wait_for(lambda: next((e for e in va.get_run(pid, run_id)["events"] if e["type"] == "approval"), None))


def test_a_command_a_rule_allows_runs_without_a_card_and_without_the_web(scripted, fake, fake_bin, audits):
    pid = make_project()
    vp.allow_rule(pid, "git diff *")
    model = scripted(["@@ run_check\ngit diff --stat\n", "@@ finish\nok\n"])
    view = wait_finished(pid, start(pid))
    assert not [e for e in view["events"] if e["type"] == "approval"]
    assert len(fake.calls) == 1 and fake.calls[0]["command"] == "git diff --stat" and fake.calls[0]["network"] is False
    notes = [e["text"] for e in view["events"] if e["type"] == "note" and "rule" in e.get("text", "")]
    assert notes == ["Ran without a card: your rule git diff * allows it (no web)."]
    assert "Exit code 0" in model.calls[1][-1]["content"] and end_of(view)["status"] == "done"
    cmds = [x for e, x in audits if e == "vibe_agent_command"]
    assert [(x["decision"], x["program"], x["network"]) for x in cmds] == [("rule", "git", False)]


def test_a_command_no_rule_allows_still_shows_the_card(scripted, fake, fake_bin):
    pid = make_project()
    vp.allow_rule(pid, "git diff *")
    scripted(["@@ run_check\ngit diff; curl x\n", "@@ finish\nok\n"])
    run_id = start(pid)
    ev = approval_of(pid, run_id)
    assert ev["command"] == "git diff; curl x" and fake.calls == []
    va.approve_run(pid, run_id, ev["id"], "decline", False)
    wait_finished(pid, run_id)
    assert fake.calls == []


def test_a_dangerous_command_is_refused_before_any_rule_is_looked_at(scripted, fake, fake_bin, audits, monkeypatch):
    pid = make_project()
    vp.allow_rule(pid, "git log *")
    looked = []
    real = rules.match
    monkeypatch.setattr(rules, "match", lambda *a, **k: looked.append(a) or real(*a, **k))
    model = scripted(["@@ run_check\ngit log --output=/etc/passwd\n", "@@ finish\nok\n"])
    view = wait_finished(pid, start(pid))
    assert fake.calls == [] and looked == [] and not [e for e in view["events"] if e["type"] == "approval"]
    assert "refused" in model.calls[1][-1]["content"]
    assert [x["decision"] for e, x in audits if e == "vibe_agent_command"] == ["refused"]


def test_an_exact_always_allowed_command_still_wins_with_its_web_flag(scripted, fake, fake_bin, audits):
    pid = make_project()
    vp.allow_rule(pid, "git diff *")
    vp.allow_command(pid, "git diff --stat", True)
    scripted(["@@ run_check\ngit diff --stat\n", "@@ finish\nok\n"])
    view = wait_finished(pid, start(pid))
    assert fake.calls[0]["network"] is True and not [e for e in view["events"] if e["type"] == "approval"]
    assert [x["decision"] for e, x in audits if e == "vibe_agent_command"] == ["stored"]


def test_a_rule_put_into_project_json_by_hand_that_the_editor_refuses_does_not_skip_the_card(scripted, fake, fake_bin):
    pid = make_project()
    path = vp.project_dir(pid) / ".vibe" / "project.json"
    meta = json.loads(path.read_text())
    meta["allowed_rules"] = [{"rule": "node *"}, {"rule": "git *"}]
    path.write_text(json.dumps(meta))
    scripted(["@@ run_check\nnode build.js\n", "@@ finish\nok\n"])
    run_id = start(pid)
    assert approval_of(pid, run_id)["command"] == "node build.js" and fake.calls == []
    va.stop_run(pid, run_id)
    wait_finished(pid, run_id)


# ── routes (Mac only) ──

@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_rules_are_listed_added_and_forgotten_over_http_at_the_mac(client, fake_bin):
    pid = make_project()
    base = f"/api/vibe/projects/{pid}/agent/rules"
    assert client.get(base).json() == []                                               # not taken for a run id
    assert client.post(base, json={"rule": "git diff *"}).json() == [{"rule": "git diff *"}]
    r = client.post(base, json={"rule": "npm *"})
    assert r.status_code == 400 and "package manager" in r.json()["error"]
    assert client.post(base, json={"rule": 5}).status_code == 400
    assert client.post(base, content=b"nope").status_code == 400
    assert client.get(base).json() == [{"rule": "git diff *"}]
    assert client.post(f"{base}/forget", json={"rule": "git diff *"}).json() == []
    assert client.post(f"{base}/forget", json={"rule": "git diff *"}).status_code == 404
    assert client.post(f"{base}/forget", json={"rule": 5}).status_code == 400
    assert client.post(f"{base}/forget", content=b"x").status_code == 400
    assert client.get("/api/vibe/projects/no-such-project/agent/rules").status_code == 404


def test_nothing_about_rules_is_open_through_the_tunnel(client, fake_bin):
    pid = make_project()
    vp.allow_rule(pid, "ls *")
    base = f"/api/vibe/projects/{pid}/agent/rules"
    assert client.get(base, headers=REMOTE).status_code == 403
    assert client.post(base, json={"rule": "git diff *"}, headers=REMOTE).status_code == 403
    assert client.post(f"{base}/forget", json={"rule": "ls *"}, headers=REMOTE).status_code == 403
    assert vp.allowed_rules(pid) == [{"rule": "ls *"}]


def test_the_rule_routes_are_registered(client):
    paths = route_methods(client.app)
    assert {"GET", "POST"} <= paths["/api/vibe/projects/{pid}/agent/rules"]
    assert "POST" in paths["/api/vibe/projects/{pid}/agent/rules/forget"]


# ── the page ──

def test_the_page_lists_rules_with_forget_and_a_form_to_add_one_as_text_only():
    allowed = SRC[SRC.index("function drawAllowed() {"):SRC.index("function drawNote(eff) {")]
    assert "api('GET', base(run.pid, '/rules'))" in allowed and "api('POST', base(run.pid, '/rules'), { rule: text })" in allowed
    assert "api('POST', base(run.pid, '/rules/forget'), { rule: item.rule })" in allowed
    assert "el('code', 'agc-cmd', item.rule)" in allowed and "shelf.textContent = '';" in allowed
    assert "input.placeholder = 'git diff *'" in allowed and "toast(e.status === 403" in allowed
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML"):
        assert banned not in allowed
    assert "if (eff.t === 'end') { drawEnd(eff.end); drawAllowed(); }" in SRC.replace("else if (eff.t === 'end')", "if (eff.t === 'end')")


# ── the real sandbox (this Mac only) ──

@pytest.mark.skipif(not cmd.available(), reason="macOS sandbox-exec is not available here")
def test_a_command_a_rule_allows_still_runs_in_the_sandbox(scripted):
    pid = make_project()
    project = vp.project_dir(pid)
    command = "touch vibe-run-test-inside ../vibe-run-test-outside"
    vp.allow_rule(pid, "touch *")                                     # the real PATH: /usr/bin/touch
    assert rules.match(["touch *"], command, project) == "touch *"    # fail here, not by waiting for a card
    model = scripted([f"@@ run_check\n{command}\n", "@@ finish\nok\n"])
    view = wait_finished(pid, start(pid), timeout=60)
    assert not [e for e in view["events"] if e["type"] == "approval"]
    assert (project / "vibe-run-test-inside").exists()
    assert not os.path.exists(project.parent / "vibe-run-test-outside")
    assert "Exit code 1" in model.calls[1][-1]["content"] and "not permitted" in model.calls[1][-1]["content"].lower()
