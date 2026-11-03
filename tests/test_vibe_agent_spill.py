"""Vibe agent: long command output kept in a file, read_output, older results that name their way back (P-043).

A scripted model stands in for the real one (tests/vibe_agent_fakes.py). The engine tests replace the sandbox runner
with one that feeds a made-up output through the real collector, which writes the spill file exactly as the real
runner does. The real sandbox runs on this Mac only (skipped elsewhere); its command carries `vibe-run-test`.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

import codec_vibe_agent as va
import codec_vibe_agent_cmd as cmd
import codec_vibe_agent_proto as proto
import codec_vibe_projects as vp
from vibe_agent_fakes import agent_env, audits, cfg, make_project, run_script, scripted, wait_finished  # noqa: F401

needs_sandbox = pytest.mark.skipif(not cmd.available(), reason="macOS sandbox-exec is not available here")
RUN = "Run the tests.\n@@ run_check\nnpm test\n"


def loud(n=7000) -> str:
    """About 300 KB: one line per test, then the failure and the summary at the very end."""
    lines = [f"test_case_{i:05d} ... ok  " + "x" * 20 for i in range(1, n + 1)]
    return "\n".join(lines + ["FAILED test_add: AssertionError: 5 != -1", f"1 failed, {n} passed in 9.1 s"]) + "\n"


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def feed(box, data: bytes, size=65536) -> None:
    for i in range(0, len(data), size):
        box.add(data[i:i + size])


class Collected:
    """Stands in for cmd.run: the output goes through the real collector, with the spill file the engine named."""

    def __init__(self, monkeypatch, output: str, exit_code=1):
        self.output, self.exit_code, self.calls = output, exit_code, []
        monkeypatch.setattr(va, "_run_command", self)
        monkeypatch.setattr(cmd, "available", lambda: True)

    def __call__(self, project, root, command, **kw):
        self.calls.append(kw)
        box = cmd._Collector(cmd._open_spill(kw.get("spill")))
        feed(box, self.output.encode())
        spilled = box.close_spill()
        text, cut = box.text()
        return cmd.Result(self.exit_code, text, 9.1, cut_bytes=cut, spilled=spilled)


def result_of(call) -> str:
    """The newest result in one prompt, without the file map after it."""
    return call[-1]["content"].split("\n\nPROJECT FILES")[0]


# ── the collector ──

def test_the_collector_copies_the_whole_output_to_a_0600_file_and_stops_at_1_mb_with_an_end_line(tmp_path):
    out = loud().encode()
    assert 250_000 < len(out) < cmd.SPILL_BYTES
    path = tmp_path / "1.txt"
    box = cmd._Collector(cmd._open_spill(str(path)))
    feed(box, out, 4096)
    assert box.close_spill() is True and box.close_spill() is True              # closing twice is harmless
    assert path.read_bytes() == out and mode(path) == 0o600
    text, cut = box.text()                                                       # the result keeps its head and tail as before
    assert cut == len(out) - cmd.HEAD_BYTES - cmd.TAIL_BYTES and text.rstrip().endswith("1 failed, 7000 passed in 9.1 s")
    big = (b"y" * 99 + b"\n") * 21000                                            # 2.1 MB
    path2 = tmp_path / "2.txt"
    box = cmd._Collector(cmd._open_spill(str(path2)))
    feed(box, big)
    assert box.close_spill() is True
    data = path2.read_bytes()
    assert len(data) <= cmd.SPILL_BYTES and data.startswith(big[:cmd.SPILL_BYTES - 1024])
    last = data.rstrip(b"\n").rsplit(b"\n", 1)[-1].decode()
    assert f"the command printed {len(big)} bytes in all" in last and "the end of the output is in the result" in last
    assert box.text()[0].rstrip().endswith("y" * 99)


def test_a_spill_file_is_never_opened_through_a_link_and_a_failing_one_never_stops_the_reading(tmp_path):
    target = tmp_path / "elsewhere.txt"
    target.write_text("keep me\n")
    (tmp_path / "link.txt").symlink_to(target)
    assert cmd._open_spill(str(tmp_path / "link.txt")) is None and target.read_text() == "keep me\n"
    assert cmd._open_spill(None) is None and cmd._open_spill("") is None

    class DiskFull:
        def write(self, b):
            raise OSError(28, "No space left on device")

        def close(self):
            pass
    box = cmd._Collector(DiskFull())
    out = loud().encode()
    feed(box, out)
    assert box.close_spill() is False
    assert box.total == len(out) and box.text()[0].rstrip().endswith("1 failed, 7000 passed in 9.1 s")


@needs_sandbox
def test_the_real_runner_copies_the_output_and_the_sandboxed_command_cannot_touch_the_copy(tmp_path):
    root = tmp_path / "vibe"
    proj = root / "proj-abc123"
    out_dir = proj / ".vibe" / "runs" / "r00000001" / "out"
    out_dir.mkdir(parents=True)
    spill = out_dir / "1.txt"
    rel = ".vibe/runs/r00000001/out/1.txt"
    r = cmd.run(proj, root, f"echo evil >> {rel}; cat {rel}; python3 -c \"[print(f'line {{i:05d}} ' + 'z' * 30) for i in range(1, 8001)]\""
                            " # vibe-run-test", spill=str(spill))
    assert r.exit_code == 0 and r.spilled and r.cut_bytes > 200_000 and mode(spill) == 0o600
    data = spill.read_bytes()
    assert len(data) == r.cut_bytes + cmd.HEAD_BYTES + cmd.TAIL_BYTES                # every byte it printed, nothing else
    lines = cmd.clean(data).splitlines()
    assert [ln for ln in lines if ln.startswith("line ")] == [f"line {i:05d} " + "z" * 30 for i in range(1, 8001)]
    assert "evil" not in cmd.clean(data) and sum("Operation not permitted" in ln for ln in lines) == 2
    assert r.output.rstrip().endswith("line 08000 " + "z" * 30)


# ── the engine: what the model sees, read_output, and what is never kept ──

def test_a_300_kb_output_is_kept_in_its_run_folder_and_read_output_gives_back_those_lines(scripted, monkeypatch):
    output = loud()
    Collected(monkeypatch, output)
    pid = make_project()
    vp.allow_command(pid, "npm test", False)                          # stored: the step runs without waiting for the owner
    view, model = run_script(pid, [RUN, "Look at the middle.\n@@ read_output 1 100-150\n", "@@ finish\nok\n"], scripted,
                             can_run=True)
    f = vp.project_dir(pid) / ".vibe" / "runs" / view["run"] / "out" / "1.txt"
    assert f.read_bytes() == output.encode()
    assert mode(f) == 0o600 and mode(f.parent) == 0o700 and mode(f.parent.parent) == 0o700
    lines = output.splitlines()
    seen = result_of(model.calls[1])
    head, rest = seen.split(" characters left out) ...\n", 1)
    pointer, tail = rest.split("\n", 1)
    assert pointer == f"(This result is shown in part. Its text is kept, {len(lines)} lines: @@ read_output 1 FIRST-LAST shows any of them.)"
    assert output.rstrip("\n")[-va.MODEL_TAIL_CHARS:] in tail and tail.endswith("1 failed, 7000 passed in 9.1 s\n[END RESULT]")
    assert head.split("\n")[:2] == ["[RESULT 1 run_check npm test: FAILED]", "Exit code 1 after 9.1 s."]
    assert len(head) <= va.MODEL_HEAD_CHARS + 100                    # the P-040 window, unchanged
    got = result_of(model.calls[2]).split("\n")
    assert got[0] == "[RESULT 2 read_output 1 100-150]"
    assert got[1] == f"Result 1 (run_check npm test), lines 100-150 of {len(lines)} (more lines follow: @@ read_output 1 151-)"
    assert got[2:-1] == [f"{i:>4}| {lines[i - 1]}" for i in range(100, 151)] and got[-1] == "[END RESULT]"
    ev = next(e for e in view["events"] if e["type"] == "result" and e["step"] == 1)
    assert ev["out"] == {"lines": len(lines)} and "read_output" not in ev["output"]   # the page's text is as before


def test_a_short_output_leaves_no_file_no_folder_and_no_pointer(scripted, monkeypatch):
    fake = Collected(monkeypatch, "3 passed\n", exit_code=0)
    pid = make_project()
    vp.allow_command(pid, "npm test", False)
    view, model = run_script(pid, [RUN, "@@ finish\nok\n"], scripted, can_run=True)
    assert fake.calls[0]["spill"].endswith(f"/.vibe/runs/{view['run']}/out/1.txt")
    assert not (vp.project_dir(pid) / ".vibe" / "runs" / view["run"]).exists()
    assert "read_output" not in result_of(model.calls[1]) and "3 passed" in result_of(model.calls[1])
    assert "out" not in next(e for e in view["events"] if e["type"] == "result")


def test_a_long_read_file_result_is_never_kept_and_read_output_sends_the_model_to_the_file(scripted, audits):
    pid = make_project({"big.txt": "".join(f"line {i} " + "x" * 60 + "\n" for i in range(1, 400))})
    view, model = run_script(pid, ["@@ read_file big.txt\n", "@@ read_output 1\n", "@@ read_file .vibe/runs/r00000001/out/1.txt\n",
                                   "@@ finish\nok\n"], scripted)
    assert not (vp.project_dir(pid) / ".vibe" / "runs" / view["run"]).exists()
    first = result_of(model.calls[1])
    assert "read_output" not in first and "(more lines follow: @@ read_file big.txt " in first.split("\n")[1]
    second = result_of(model.calls[2])
    assert second.startswith("[RESULT 2 read_output 1: FAILED]") and "Read the file itself with @@ read_file big.txt" in second
    third = result_of(model.calls[3])
    assert third.startswith("[RESULT 3 read_file .vibe/runs/r00000001/out/1.txt: FAILED]")   # the file tools still refuse .vibe
    assert [x["reason"] for e, x in audits if e == "vibe_agent_blocked"] == ["path"]


def test_older_results_name_their_way_back_and_read_output_gives_one_back_from_memory(scripted):
    pid = make_project()
    steps = ['@@ search "color"\n', "@@ read_file style.css 1-2\n", "@@ read_file index.html\n", "@@ read_file app.js\n",
             "@@ list_files\n", '@@ search "h1"\n', '@@ search "go"\n', "@@ read_file index.html 1-3\n",
             "@@ read_output 1\n", "@@ finish\nok\n"]
    view, model = run_script(pid, steps, scripted)
    users = [m["content"] for m in model.calls[8] if m["role"] == "user"]
    assert users[1].startswith('[RESULT 1 search "color"] 2 match(es) for "color" (shortened to save space; '
                               "@@ read_output 1 shows it again)")
    assert users[2].startswith("[RESULT 2 read_file style.css 1-2] Read style.css, lines 1-2 of 2 (shortened to save space; "
                               "@@ read_file style.css 1-2 reads it again)")
    back = result_of(model.calls[9]).split("\n")
    assert back[1] == 'Result 1 (search "color"), lines 1-2 of 2'
    assert back[2:4] == ["  1| style.css:1: body { color: black; }", "  2| style.css:2: h1 { color: red; }"]


def test_a_newest_result_cut_to_fit_a_small_context_names_read_output_at_the_cut(scripted, monkeypatch, cfg):
    cfg["vibe_agent"] = {"max_context_tokens": 2000}
    Collected(monkeypatch, loud())
    pid = make_project()
    vp.allow_command(pid, "npm test", False)
    view, model = run_script(pid, [RUN, "@@ finish\nok\n"], scripted, can_run=True)
    seen = model.calls[1][-1]["content"]
    assert "\n... (cut to fit the context; @@ read_output 1 shows all of it) ...\n" in seen
    assert va.Run._estimate(model.calls[1]) <= 2000


def test_a_continued_run_reads_the_kept_output_of_the_run_it_continues(scripted, monkeypatch, cfg):
    output = loud()
    Collected(monkeypatch, output)
    cfg["vibe_agent"] = {"max_steps": 1}
    pid = make_project()
    vp.allow_command(pid, "npm test", False)
    old, _ = run_script(pid, [RUN], scripted, can_run=True)
    assert va.get_run(pid, old["run"])["continuable"] is True
    cfg["vibe_agent"] = {"max_steps": 5}
    model = scripted(["The end of the tests.\n@@ read_output 1 7001-7002\n", "@@ finish\nok\n"])
    new = wait_finished(pid, va.continue_run(pid, old["run"], can_run=True)["run"])
    rebuilt = [m["content"] for m in model.calls[0] if m["role"] == "user"][1]
    assert rebuilt.startswith("[RESULT 1 run_check npm test: FAILED]") and "Its text is kept, 7002 lines: @@ read_output 1 " in rebuilt
    got = result_of(model.calls[1]).split("\n")
    assert got[2:4] == ["7001| FAILED test_add: AssertionError: 5 != -1", "7002| 1 failed, 7000 passed in 9.1 s"]
    runs = vp.project_dir(pid) / ".vibe" / "runs"
    assert (runs / old["run"] / "out" / "1.txt").is_file() and not (runs / new["run"]).exists()


def test_past_the_runs_budget_a_long_output_stays_in_memory_and_is_still_given_back(scripted, monkeypatch):
    monkeypatch.setattr(va, "SPILL_RUN_BYTES", 1)
    fake = Collected(monkeypatch, loud())
    pid = make_project()
    vp.allow_command(pid, "npm test", False)
    view, model = run_script(pid, [RUN, RUN, "@@ read_output 2 1-2\n", "@@ finish\nok\n"], scripted, can_run=True)
    assert fake.calls[0]["spill"] and fake.calls[1]["spill"] is None            # the first one used up this run's budget
    assert sorted(p.name for p in (vp.project_dir(pid) / ".vibe" / "runs" / view["run"] / "out").iterdir()) == ["1.txt"]
    assert "@@ read_output 2 FIRST-LAST" in result_of(model.calls[2])
    assert "out" not in [e for e in view["events"] if e["type"] == "result"][1]
    back = result_of(model.calls[3]).split("\n")                               # the text the run holds: head, cut, tail
    assert [b.split("| ", 1)[1] for b in back[2:4]] == ["Exit code 1 after 9.1 s.", "test_case_00001 ... ok  " + "x" * 20]


# ── removal at KEEP_RUNS ──

def test_kept_output_folders_go_with_their_run_logs_at_keep_runs_and_a_link_is_never_followed(scripted, tmp_path):
    pid = make_project()
    runs = vp.project_dir(pid) / ".vibe" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    for i in range(24):
        rid = f"r{i:08x}"
        log = runs / f"{rid}.jsonl"
        log.write_text(json.dumps({"seq": 1, "type": "start", "goal": f"old {i}", "t": 1.0}) + "\n")
        os.utime(log, (1000 + i, 1000 + i))
        (runs / rid / "out").mkdir(parents=True)
        (runs / rid / "out" / "1.txt").write_text("old output\n")
    (runs / "r0000abcd" / "out").mkdir(parents=True)                         # a folder whose log is already gone
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("not a run\n")
    (runs / "r0000beef").symlink_to(elsewhere)
    view, _ = run_script(pid, ["@@ finish\ndone\n"], scripted)
    logs = sorted(p.stem for p in runs.glob("r*.jsonl"))
    folders = sorted(p.name for p in runs.iterdir() if p.is_dir() and not p.is_symlink())
    assert len(logs) == va.KEEP_RUNS and view["run"] in logs
    assert folders == sorted(set(logs) - {view["run"]})                      # the 19 kept old runs keep theirs, the rest are gone
    assert (elsewhere / "keep.txt").read_text() == "not a run\n" and (runs / "r0000beef").is_symlink()


# ── the tool format ──

def test_read_output_takes_a_result_number_never_a_path_and_its_prompt_line_is_cheap():
    assert proto.scan_reply("@@ read_output 3 100-150", final=True).call.args == {"n": 3, "first": 100, "last": 150}
    assert proto.scan_reply("@@ read_output #4 10-", final=True).call.args == {"n": 4, "first": 10, "last": None}
    for bad in ("@@ read_output", "@@ read_output x", "@@ read_output 0", "@@ read_output .vibe/runs/r00000001/out/1.txt",
                "@@ read_output 3 9-2", "@@ read_output 3 1-2 4"):
        assert proto.scan_reply(bad, final=True).state == "error", bad
    line = next(ln for ln in proto.system_prompt().split("\n") if ln.startswith("@@ read_output"))
    assert line.startswith("@@ read_output N [FIRST-LAST]") and "@@ read_output N [FIRST-LAST]" in proto.FORMAT_HELP
    assert va.Run._estimate([{"role": "system", "content": line}]) - 4 <= 40   # about 40 prompt tokens at most
