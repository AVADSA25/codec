"""Vibe agent eval script (tools/vibe_agent_eval.py): the bar, the tries, the result file, the regression rule.

A fake model and fake attempts stand in for the live one: no GPU, no network, nothing leaves this computer.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest

import codec_vibe_agent as va
import codec_vibe_projects as vp
from vibe_agent_fakes import agent_env, cfg, scripted  # noqa: F401  (fixtures are used by name)

REPO = Path(__file__).resolve().parent.parent
LOCAL = "http://localhost:8083/v1"


def _load():
    spec = importlib.util.spec_from_file_location("vibe_agent_eval_under_test", REPO / "tools" / "vibe_agent_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ev = _load()


def attempt_rec(ok, detail="ok"):
    return {"pass": ok, "detail": detail, "status": "done", "steps": 3, "seconds": 2.5, "prompt_tokens": 100, "completion_tokens": 20,
            "format_failures": 0, "failed_edits": 0, "error": False, "timeout": False, "report": ""}


def record(i, passed=True, first=None, skipped=False):
    return {"id": i, "pass": passed, "first_try_pass": passed if first is None else first, "skipped": skipped}


def full(passes=(), fails=()):
    """Result records for tasks 1 to 12: ids in `fails` fail, the rest pass."""
    return [record(i, i not in fails) for i in range(1, 13)] + [record(i) for i in passes]


def result_file(model, results, **extra):
    return {"model": model, "base_url": LOCAL, "results": results, **extra}


# ── the bar keys to tasks 1 to 12 ──

def test_bar_ignores_tasks_added_later():
    base = ev.bar_status(full())
    assert base["met"] and (base["file_ok"], base["behaviour_ok"]) == (8, 4)
    extra = full() + [record(13, passed=False), record(14, passed=False), record(15, passed=False, skipped=True)]
    assert ev.bar_status(extra)["met"]                              # failing or skipped 13 and up change nothing
    assert ev.bar_status(extra)["file_ok"] == 8
    assert not ev.bar_status(full(fails=(9,)) + [record(13)])["met"]  # and a good 13 cannot save a failed behaviour task


def test_bar_thresholds_are_six_of_eight_file_tasks_and_all_four_behaviour_tasks():
    assert ev.bar_status(full(fails=(1, 2)))["met"]                 # 6 of 8 file tasks
    assert not ev.bar_status(full(fails=(1, 2, 3)))["met"]          # 5 of 8
    assert not ev.bar_status(full(fails=(12,)))["met"]              # 3 of 4 behaviour tasks


def test_bar_needs_every_original_task_to_have_run():
    results = [r for r in full() if r["id"] != 4]
    bar = ev.bar_status(results)
    assert bar["missing"] == [4] and not bar["met"]
    skipped = [dict(r, skipped=True) if r["id"] == 4 else r for r in full()]
    assert ev.bar_status(skipped)["missing"] == [4]                # skipped is not run


# ── tasks that need a feature that does not exist yet ──

def test_needs_flagged_task_is_skipped_and_not_counted():
    tasks = [{"id": 1, "title": "one", "kind": "file"}, {"id": 13, "title": "new", "kind": "file", "needs": "future_feature"},
             {"id": 4, "title": "tests", "kind": "file", "needs": "run_check"}]
    calls, said = [], []

    def attempt(task, note):
        calls.append(task["id"])
        return attempt_rec(True)
    out = ev.run_suite(tasks, attempt, {"run_check": True}, say=said.append)
    assert calls == [1, 4]                                          # 13 never ran
    by_id = {r["id"]: r for r in out}
    assert by_id[13]["skipped"] and not by_id[13]["pass"] and by_id[13]["tries"] == 0 and by_id[13]["needs"] == "future_feature"
    assert any("TASK 13 SKIPPED" in line and "future_feature" in line for line in said)
    assert ev.bar_status(out)["file_ok"] == 2                        # 13 is not in the counts
    out = ev.run_suite(tasks, attempt, {"run_check": False}, say=said.append)
    assert next(r for r in out if r["id"] == 4)["skipped"]           # no sandbox: skipped, so the bar reports it missing
    assert 4 in ev.bar_status(out)["missing"]


# ── two tries ──

def test_second_try_sees_the_failure_and_either_try_passes_the_task():
    notes = []

    def attempt(task, note):
        notes.append(note)
        return attempt_rec(len(notes) == 2, "the button has no click handler")
    rec = ev.run_with_tries({"id": 1, "title": "t", "kind": "file"}, attempt)
    assert notes[0] == "" and "the button has no click handler" in notes[1]
    assert rec["pass"] and not rec["first_try_pass"] and rec["tries"] == 2
    assert [a["pass"] for a in rec["attempts"]] == [False, True] and rec["steps"] == 6 and rec["seconds"] == 5.0


def test_a_pass_stops_the_tries_and_two_failures_stop_at_two():
    n = []
    rec = ev.run_with_tries({"id": 1, "title": "t", "kind": "file"}, lambda t, note: n.append(1) or attempt_rec(True))
    assert rec["tries"] == 1 and rec["first_try_pass"] and len(n) == 1
    n.clear()
    rec = ev.run_with_tries({"id": 1, "title": "t", "kind": "file"}, lambda t, note: n.append(1) or attempt_rec(False))
    assert rec["tries"] == 2 and not rec["pass"] and len(n) == 2


# ── the numbers of one try, taken from a real run on a fake model ──

def test_run_task_counts_format_failures_failed_edits_and_flags(scripted, cfg):  # noqa: F811
    bad_edit = "@@ edit_file style.css\n<<<<<<< SEARCH\nthis text is not in the file\n=======\nx\n>>>>>>> REPLACE\n"
    scripted(["Let me think about it for a while.", bad_edit, "Done.\n@@ finish\nChanged nothing.\n"])
    task = {"id": 1, "kind": "file", "title": "t", "files": {"index.html": "<h1>Hi</h1>\n", "style.css": "h1 { color: red; }\n"},
            "goal": "Make it blue.", "check": lambda files, events, report, pdir=None: (False, "still red")}
    rec = ev.run_task(task, va, vp, cfg, False)
    assert rec["pass"] is False and rec["detail"] == "still red" and rec["status"] == "done"
    assert (rec["format_failures"], rec["failed_edits"]) == (1, 1)
    assert rec["steps"] == 3 and rec["seconds"] is not None
    assert rec["error"] is False and rec["timeout"] is False
    assert ev.is_timeout({"report": "Stopped at the time limit of 15 minutes."}, False) and ev.is_timeout({}, True)
    assert not ev.is_timeout({"report": "Stopped at the limit of 6 steps."}, False)


# ── the result file ──

def test_result_file_holds_model_url_sha_time_and_every_number(tmp_path, monkeypatch):
    out = tmp_path / "r.json"
    monkeypatch.setattr(va, "_resolve_llm", lambda kind, confirm_cloud=False: va._Llm("local", LOCAL, "mlx-community/Fake-1", "Local"))
    monkeypatch.setattr(ev, "run_task", lambda task, *a, **k: attempt_rec(True))
    monkeypatch.setattr(ev, "git_info", lambda: ("a" * 40, False))
    assert ev.main(["--only", "1,2", "--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["model"] == "mlx-community/Fake-1" and doc["base_url"] == LOCAL and doc["git_sha"] == "a" * 40
    datetime.fromisoformat(doc["time"])
    assert doc["only"] == [1, 2] and "GO when" in doc["rule"] and doc["bar"]["met"] is False   # a partial run cannot meet the bar
    for r in doc["results"]:
        for key in ("steps", "seconds", "format_failures", "failed_edits", "error", "timeout", "tries", "pass", "first_try_pass", "attempts"):
            assert key in r
    assert [r["id"] for r in doc["results"]] == [1, 2] and doc["results"][0]["attempts"][0]["try"] == 1


@pytest.mark.skipif(not (REPO / ".git").exists(), reason="not a git checkout")
def test_git_info_gives_the_checkout_sha():
    sha, dirty = ev.git_info()
    assert len(sha) == 40 and isinstance(dirty, bool)


# ── only the local model ──

def test_refuses_a_model_url_that_is_not_on_this_mac(monkeypatch, capsys):
    monkeypatch.setattr(va, "_resolve_llm", lambda kind, confirm_cloud=False: va._Llm("local", "https://api.xiaomimimo.com/v1", "mimo", "Local"))

    def must_not_run(*a, **k):
        raise AssertionError("a task started against a cloud URL")
    monkeypatch.setattr(va, "start_run", must_not_run)
    monkeypatch.setattr(ev, "run_task", must_not_run)
    assert ev.main(["--bar"]) == 2
    assert "EVAL REFUSED" in capsys.readouterr().out
    with pytest.raises(ev.NotLocal):
        ev.require_local("https://api.openai.com/v1")
    assert ev.require_local("http://127.0.0.1:8083/v1") == "http://127.0.0.1:8083/v1"
    with pytest.raises(SystemExit):                                  # there is no --cloud flag
        ev.main(["--cloud"])


# ── the regression rule ──

def test_a_task_regresses_only_if_it_fails_both_tries():
    task = {"id": 5, "title": "t", "kind": "file"}
    base = result_file("m1", full())
    second_try = ev.run_with_tries(task, lambda t, note: attempt_rec(bool(note)))      # fails, then passes
    both_fail = ev.run_with_tries(task, lambda t, note: attempt_rec(False))
    others = [r for r in full() if r["id"] != 5]
    ok = ev.compare(base, result_file("m1", others + [second_try]))
    assert ok["regressed"] == [] and ok["ok"]
    bad = ev.compare(base, result_file("m1", others + [both_fail]))
    assert bad["regressed"] == [5] and not bad["ok"]
    assert (bad["baseline_pass"], bad["new_pass"]) == (12, 11)


def test_pass_counts_compare_tasks_1_to_12_only():
    base = result_file("m1", full(fails=(3,)) + [record(13), record(14)])
    new = result_file("m1", full(fails=(3,)) + [record(13, False), record(14, False)])
    c = ev.compare(base, new)
    assert c["ok"] and c["regressed"] == [] and (c["baseline_pass"], c["new_pass"]) == (11, 11)
    assert 13 not in c["compared"] and 14 not in c["compared"]
    better = ev.compare(base, result_file("m1", full()))                 # task 3 now passes: no regression
    assert better["ok"] and better["new_pass"] == 12


def test_a_task_missing_from_a_full_run_is_not_a_clean_comparison():
    base = result_file("m1", full())
    new = result_file("m1", [r for r in full() if r["id"] != 2])
    assert not ev.compare(base, new)["ok"] and ev.compare(base, new)["not_compared"] == [2]
    assert ev.compare(base, dict(new, only=[1, 3]))["ok"]                # a run of chosen tasks only compares those


def test_a_baseline_is_valid_only_for_the_model_it_records(tmp_path, monkeypatch, capsys):
    base, new = result_file("model-A", full()), result_file("model-B", full())
    with pytest.raises(ev.BaselineMismatch):
        ev.compare(base, new)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(base))
    b.write_text(json.dumps(new))
    assert ev.main(["--compare", str(b), "--baseline", str(a)]) == 2
    assert "COMPARE REFUSED" in capsys.readouterr().out
    # a run on model B refuses before it starts a single task
    monkeypatch.setattr(va, "_resolve_llm", lambda kind, confirm_cloud=False: va._Llm("local", LOCAL, "model-B", "Local"))
    monkeypatch.setattr(ev, "run_task", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran against a baseline of another model")))
    assert ev.main(["--bar", "--baseline", str(a)]) == 2
    assert ev.main(["--compare", str(a), "--baseline", str(a)]) == 0     # same file, same model: fine
