"""Vibe agent, P-066: list by glob, search by regex, files-only and count, a file map that reads no file
(codec_vibe_agent_search, docs/VIBE-AGENT-SEARCH-DESIGN.md).

The regex child really runs here (it needs only the standard library). Every child these tests start carries a
vibe-run-test marker in its argv, so `pgrep -f "[v]ibe-run-test"` can show that none is left running.
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import sys
import time

import pytest

import codec_vibe_agent as va
import codec_vibe_agent_proto as p
import codec_vibe_agent_search as vs
import codec_vibe_projects as vp
from vibe_agent_fakes import agent_env, cfg, end_of, make_project, run_script, scripted  # noqa: F401  (fixtures by name)

MARK = "vibe-run-test-p066-" + secrets.token_hex(4)
CATASTROPHIC = ("(a+)+$", "a" * 100_000 + "!")        # 100 KB line: Python's re backtracks for ever on it
OLD_PROMPT_CHARS = {True: 2283, False: 1910}          # system_prompt() before P-066, with and without run_check


@pytest.fixture(autouse=True)
def _marked(monkeypatch):
    monkeypatch.setattr(vs, "EXTRA_ARGV", (MARK,))


def tool_run(pid):
    return va.Run(pid, "g", [], va._Llm("local", "http://localhost:1/v1", "m", "Local"), va.settings(), False)


def scan(line):
    return p.scan_reply(line + "\n", final=True)


def spy_popen(monkeypatch):
    """Record every child the search module starts."""
    seen = []
    real = subprocess.Popen

    def spy(args, **kw):
        proc = real(args, **kw)
        seen.append((list(args), kw, proc))
        return proc
    monkeypatch.setattr(vs.subprocess, "Popen", spy)
    return seen


def project_with(files: dict) -> str:
    """A project with any number of files: create_project takes 50 at a time, the rest go straight into the folder."""
    items = list(files.items())
    pid = make_project(dict(items[:1]))
    for rel, text in items[1:]:
        target = vp.project_dir(pid) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return pid


def still_running(marker: str) -> bool:
    if not shutil.which("pgrep"):
        pytest.skip("pgrep is not installed")
    return subprocess.run(["pgrep", "-f", marker], capture_output=True).returncode == 0


# ── the search line ──

@pytest.mark.parametrize("line,args", [
    ('@@ search "hello world" css', {"pattern": "hello world", "path": "css", "regex": False, "mode": "lines"}),
    ('@@ search --regex "colou?r" css', {"pattern": "colou?r", "path": "css", "regex": True, "mode": "lines"}),
    ("@@ search --files needle", {"pattern": "needle", "path": "", "regex": False, "mode": "files"}),
    ('@@ search --count "a b"', {"pattern": "a b", "path": "", "regex": False, "mode": "count"}),
    ('@@ search "a" src --files --count', {"pattern": "a", "path": "src", "regex": False, "mode": "count"}),
    ('@@ search -E "x|y" src/**/*.js', {"pattern": "x|y", "path": "src/**/*.js", "regex": True, "mode": "lines"}),
    ("@@ search -rl needle", {"pattern": "needle", "path": "", "regex": False, "mode": "files"}),
    ("@@ search -c needle", {"pattern": "needle", "path": "", "regex": False, "mode": "count"}),
    ("@@ search -rni -e needle", {"pattern": "needle", "path": "", "regex": False, "mode": "lines"}),
    ('@@ search "-l" css', {"pattern": "-l", "path": "css", "regex": False, "mode": "lines"}),
    ('@@ search "--count"', {"pattern": "--count", "path": "", "regex": False, "mode": "lines"}),
    (r"@@ search --regex \d+px css", {"pattern": r"\d+px", "path": "css", "regex": True, "mode": "lines"}),
    (r'@@ search "\bfoo\b" --regex', {"pattern": r"\bfoo\b", "path": "", "regex": True, "mode": "lines"}),
    (r'@@ search "class=\"btn\""', {"pattern": 'class="btn"', "path": "", "regex": False, "mode": "lines"}),
])
def test_search_reads_its_flags_before_or_after_the_text(line, args):
    s = scan(line)
    assert s.state == "complete" and s.call.args == args


@pytest.mark.parametrize("line", ["@@ search --regex", "@@ search --files --count", "@@ search a b c", '@@ search "x" a --files b'])
def test_search_without_a_text_or_with_two_paths_says_how_to_write_it(line):
    s = scan(line)
    assert s.state == "error" and s.error == p.SEARCH_USE


def test_the_timeline_label_shows_the_flags():
    assert va._tool_arg(scan('@@ search --regex --count "a|b" src').call) == '--regex --count "a|b" in src'
    assert va._tool_arg(scan("@@ search needle").call) == '"needle"'


# ── globs ──

def test_a_glob_matches_folders_names_and_any_case():
    m = vs.matcher("src/**/*.js")
    names = ["src/a.js", "src/x/y/b.js", "src/a.css", "a.js", "lib/src/a.js", "SRC/Up.JS", "src/x.jsx"]
    assert [n for n in names if m(n)] == ["src/a.js", "src/x/y/b.js", "SRC/Up.JS"]
    assert [n for n in ["a.css", "x/y/b.css", "a.cssx"] if vs.matcher("*.css")(n)] == ["a.css", "x/y/b.css"]
    assert vs.matcher("**")("a/b/c") and vs.matcher("./src/*")("src/a") and not vs.matcher("src/*")("src/a/b")
    assert vs.matcher("t?st/[ab].txt")("test/a.txt") and not vs.matcher("t?st/[ab].txt")("test/c.txt")


@pytest.mark.parametrize("bad,unsafe", [("../*.js", True), ("/etc/*", True), ("~/x*", True), ("./*/..", True),
                                        ("a\\*", False), ("*" * 201, False)])
def test_a_glob_that_leaves_the_project_or_is_malformed_is_refused(bad, unsafe):
    with pytest.raises(vs.GlobError) as e:
        vs.matcher(bad)
    assert e.value.unsafe is unsafe


def test_a_glob_full_of_stars_does_not_backtrack():
    m = vs.matcher("*a" * 40 + "b")
    t0 = time.monotonic()
    assert not m("a" * 200)
    assert time.monotonic() - t0 < 1.0


def test_list_files_with_a_glob_lists_only_matching_names():
    pid = make_project({"src/app.js": "1\n", "src/lib/util.js": "2\n", "src/style.css": "3\n", "app.js": "4\n"})
    (vp.project_dir(pid) / "src" / ".env.js").write_text("KEY=1\n")
    run = tool_run(pid)
    r = va._t_list_files(run, "src/**/*.js")
    assert r.ok and r.summary == "2 file(s) matching src/**/*.js"
    assert [ln.split("  ")[0] for ln in r.text.splitlines()[1:]] == ["src/app.js", "src/lib/util.js"]
    assert va._t_list_files(run, "*.md").text == "No files matching *.md."
    bad = va._t_list_files(run, "../**/*.js")
    assert not bad.ok and bad.blocked == "path"
    assert va._t_list_files(run, "src").summary == "3 file(s) in src"           # a folder works as before


def test_a_real_folder_or_file_with_brackets_in_its_name_is_still_a_path():
    pid = make_project({"pages/[slug]/index.js": "needle\n", "pages/[id].js": "needle\n", "pages/i.js": "needle\n"})
    run = tool_run(pid)
    assert va._t_list_files(run, "pages/[slug]").summary == "1 file(s) in pages/[slug]"
    assert va._t_search(run, "needle", "pages/[id].js").text == "pages/[id].js:1: needle"
    assert va._t_search(run, "needle", "pages/[id].js", regex=True).text == "pages/[id].js:1: needle"
    assert va._t_list_files(run, "pages/[i].js").summary == "1 file(s) matching pages/[i].js"   # no such name: a glob


# ── search modes ──

def test_files_and_count_modes_answer_past_the_40_match_cap():
    files = {f"f{i:02d}.txt": "needle\n" * 3 for i in range(50)}
    run = tool_run(make_project(files))
    assert "narrow the search" in va._t_search(run, "needle", "").text            # the line listing still stops at 40
    for regex in (False, True):
        f = va._t_search(run, "needle", "", regex=regex, mode="files")
        assert f.ok and "narrow" not in f.text
        assert f.text.splitlines() == ['50 file(s) match "needle":'] + sorted(files)
        c = va._t_search(run, "NEEDLE", "", regex=regex, mode="count")
        assert c.ok and "narrow" not in c.text
        assert c.text.splitlines()[0] == '150 matching line(s) in 50 file(s) for "NEEDLE":'
        assert c.text.splitlines()[1:] == [f"{name}: 3" for name in sorted(files)]


def test_regex_search_finds_patterns_and_never_sends_secret_files(monkeypatch):
    pid = make_project({"a.js": "var colour = 1;\nvar color = 2;\n", "src/b.js": "COLOR\n", "src/c.css": "color: red;\n"})
    (vp.project_dir(pid) / ".env").write_text("color=secret\n")
    sent = []
    real = vs.run_regex

    def record(pattern, mode, files, cap):
        sent.append([rel for rel, _text in files])
        return real(pattern, mode, files, cap)
    monkeypatch.setattr(vs, "run_regex", record)
    run = tool_run(pid)
    r = va._t_search(run, r"colou?r\b", "", regex=True)
    assert r.text.splitlines() == ["a.js:1: var colour = 1;", "a.js:2: var color = 2;", "src/b.js:1: COLOR",
                                   "src/c.css:1: color: red;"]
    assert sent[0] == ["a.js", "src/b.js", "src/c.css"]
    assert va._t_search(run, "colou?r", "src/**/*.js", regex=True).text == "src/b.js:1: COLOR"
    bad = va._t_search(run, "(unclosed", "", regex=True)
    assert not bad.ok and bad.text.startswith("That is not a valid regular expression: missing )")
    assert va._t_search(run, "colou?r", "").text == 'No matches for "colou?r".'   # without --regex it is plain text


def test_plain_and_regex_searches_agree_on_the_same_text():
    texts = [("a.txt", "Needle\nhay\nneedle needle\n"), ("b/c.txt", "x\n" * 5 + "NEEDLE\n")]
    for mode in ("lines", "files", "count"):
        plain = va._plain_search(iter(texts), "needle", mode)
        child = vs.run_regex(re.escape("needle"), mode, texts, va.SEARCH_MATCHES)
        assert (plain.hits, plain.counts, plain.more) == (child.hits, child.counts, child.more)


def test_a_full_run_passes_the_flags_to_the_tool(scripted):
    pid = make_project({"a.js": "needle\n", "b.js": "needle\nneedle\n"})
    view, _ = run_script(pid, ["@@ search --count needle\n", "@@ finish\nok\n"], scripted)
    result = next(e for e in view["events"] if e["type"] == "result" and e["tool"] == "search")
    assert result["output"] == '3 matching line(s) in 2 file(s) for "needle":\na.js: 1\nb.js: 2'
    assert end_of(view)["status"] == "done"


# ── the regex child ──

def test_a_catastrophic_regex_is_stopped_within_3_seconds_and_leaves_no_process(monkeypatch):
    seen = spy_popen(monkeypatch)
    pid = make_project({"big.txt": CATASTROPHIC[1] + "\n"})
    t0 = time.monotonic()
    r = va._t_search(tool_run(pid), CATASTROPHIC[0], "", regex=True)
    assert time.monotonic() - t0 < 3.0
    assert not r.ok and r.text == vs.SLOW
    (_args, _kw, proc), = seen
    assert proc.returncode is not None                       # reaped
    with pytest.raises(ProcessLookupError):
        os.killpg(proc.pid, 0)                               # and its whole process group is gone
    assert not still_running(MARK)


def test_the_cpu_limit_alone_stops_a_runaway_pattern(monkeypatch):
    monkeypatch.setattr(vs, "REGEX_SECONDS", 30.0)          # lift the wall clock: only RLIMIT_CPU is left
    seen = spy_popen(monkeypatch)
    t0 = time.monotonic()
    with pytest.raises(vs.RegexFailed) as e:
        vs.run_regex(CATASTROPHIC[0], "lines", [("big.txt", CATASTROPHIC[1])], 40)
    assert e.value.args[0] == vs.SLOW and time.monotonic() - t0 < 15
    assert seen[0][2].returncode == -vs.signal.SIGXCPU
    assert not still_running(MARK)


def test_the_child_gets_a_fixed_argv_the_text_on_stdin_and_nothing_else(monkeypatch):
    seen = spy_popen(monkeypatch)
    pattern = "zq(x|y)+marker"
    assert vs.run_regex(pattern, "lines", [("a.txt", "zqxymarker\n")], 40).hits == [("a.txt", 1, "zqxymarker")]
    (args, kw, _proc), = seen
    assert args == [sys.executable, "-I", "-c", vs.CHILD_CODE, MARK]
    assert not any(pattern in a or "zqxymarker" in a for a in args)
    assert kw["env"] == {} and kw["stdin"] == subprocess.PIPE and kw["start_new_session"] is True
    assert kw["preexec_fn"] is vs._limits and not kw.get("shell")


def test_the_limits_set_the_cpu_limit_and_the_memory_limit_where_the_system_allows_it():
    code = "import resource\nprint(resource.getrlimit(resource.RLIMIT_CPU))\nprint(resource.getrlimit(resource.RLIMIT_AS))"
    out = subprocess.run([sys.executable, "-I", "-c", code, MARK], preexec_fn=vs._limits, env={}, capture_output=True,
                         text=True, timeout=30).stdout.splitlines()
    cpu, memory = (ast.literal_eval(x) for x in out)
    assert cpu == vs.REGEX_CPU
    if sys.platform.startswith("linux"):                     # macOS refuses any RLIMIT_AS below its start-up reserve
        assert memory == (vs.REGEX_MEMORY, vs.REGEX_MEMORY)


# ── the file map reads no file ──

def test_the_file_map_hashes_nothing_on_a_200_file_project(monkeypatch):
    pid = project_with({f"d{i % 7}/f{i:03d}.txt": f"line {i}\n" for i in range(200)})
    run = tool_run(pid)
    names = sorted(vp._scan(vp.project_dir(pid)))           # what the hashing scan lists, taken before counting
    calls = {"sha256": 0, "_scan": 0}
    real_sha, real_scan = hashlib.sha256, vp._scan

    def sha(*a, **k):
        calls["sha256"] += 1
        return real_sha(*a, **k)

    def counted_scan(*a, **k):
        calls["_scan"] += 1
        return real_scan(*a, **k)
    monkeypatch.setattr(vp.hashlib, "sha256", sha)
    monkeypatch.setattr(vp, "_scan", counted_scan)
    site = run._map()
    listed = va._t_list_files(run, "")
    assert calls == {"sha256": 0, "_scan": 0}
    assert [f["path"] for f in vp.list_files(pid)] == names and len(names) == 200
    assert site.startswith("PROJECT FILES (200):")
    assert [ln.rsplit(" (", 1)[0] for ln in site.splitlines()[1:1 + va.MAP_LINES]] == names[:va.MAP_LINES]
    assert listed.summary == "200 file(s)"


def test_the_listing_keeps_the_names_and_sizes_the_hashing_scan_finds():
    pid = make_project({"a.txt": "a\n", "sub/deep/b.js": "bb\n", "empty.txt": ""})
    pdir = vp.project_dir(pid)
    (pdir / "node_modules" / "x").mkdir(parents=True)
    (pdir / "node_modules" / "x" / "i.js").write_text("x")
    (pdir / ".git").mkdir()
    (pdir / ".git" / "HEAD").write_text("ref")
    (pdir / ".DS_Store").write_bytes(b"\0")
    (pdir / "big.bin").write_bytes(b"x" * (vp.MAX_FILE_BYTES + 1))
    (pdir / "c.txt.vibe-tmp").write_text("t")
    os.symlink(pdir / "a.txt", pdir / "link.txt")
    locked = pdir / "locked.txt"
    locked.write_text("no")
    locked.chmod(0)
    try:
        listed = {f["path"]: f["size"] for f in vp.list_files(pid)}
        assert listed == {rel: size for rel, (size, _sha) in vp._scan(pdir).items()}
        assert sorted(listed) == [f["path"] for f in vp.list_files(pid)]
        assert {"a.txt", "sub/deep/b.js", "empty.txt"} <= set(listed)
        if os.geteuid() != 0:
            assert "locked.txt" not in listed
    finally:
        locked.chmod(0o600)


def test_more_than_200_files_still_answer_413():
    pid = project_with({f"f{i:03d}.txt": "x\n" for i in range(201)})
    with pytest.raises(vp.VibeError) as e:
        vp.list_files(pid)
    assert e.value.status == 413


# ── what the model is told ──

@pytest.mark.parametrize("with_check", [True, False])
def test_the_new_prompt_text_costs_at_most_120_tokens(with_check):
    allowed = p.TOOLS if with_check else tuple(t for t in p.TOOLS if t != "run_check")
    prompt = p.system_prompt(allowed)
    new = va.Run._estimate([{"role": "system", "content": prompt}])
    old = va.Run._estimate([{"role": "system", "content": "x" * OLD_PROMPT_CHARS[with_check]}])
    assert 0 < new - old <= 120
    assert "src/**/*.js" in prompt and '@@ search [--regex] [--files] [--count] "text"' in prompt
