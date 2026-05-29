"""Pilot PP-2 — the trace→skill compiler must not let attacker-influenced trace fields
(task, scroll amount, wait ms, …) inject code into the generated skill/script, and skill
review must not allow a path/glob-traversal slug. Closes audit P-2 + P-11.

Reference: docs/PP2-COMPILER-SAFETY-DESIGN.md.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # ~/codec on sys.path

from pilot.pilot_agent import AgentRun, AgentStep  # noqa: E402
from pilot import compiler, skill_review  # noqa: E402


def _top_imports(src: str) -> set:
    tree = ast.parse(src)
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module.split(".")[0])
    return out


def test_compile_skill_task_cannot_break_docstring():
    run = AgentRun(task='ok """\nimport os\nos.system("id")\n"""', run_id="r1", status="done")
    _slug, src = compiler.compile_skill(run)
    compile(src, "<t>", "exec")  # must be valid Python (no docstring breakout)
    assert "os" not in _top_imports(src), "task injection must not become a real import (P-2)"


def test_compile_trace_task_cannot_break_docstring():
    run = AgentRun(task='ok """\nimport socket\nsocket.socket()\n"""', run_id="r2", status="done")
    src = compiler.compile_trace(run)
    compile(src, "<t>", "exec")
    assert "socket" not in _top_imports(src)


def test_scroll_amount_is_int_only():
    run = AgentRun(task="t", run_id="r3", status="done", steps=[
        AgentStep(step=1, action={"action": "scroll", "direction": "down",
                                  "amount": "500); alert(document.cookie)//"},
                  snapshot_before="")])
    src = compiler.compile_trace(run)
    compile(src, "<t>", "exec")
    assert "alert" not in src, "scroll amount must be int-cast, not injected (P-2)"


def test_wait_ms_is_int_only():
    run = AgentRun(task="t", run_id="r4", status="done", steps=[
        AgentStep(step=1, action={"action": "wait", "ms": "1000); import os; os.system('x')"},
                  snapshot_before="")])
    src = compiler.compile_trace(run)
    compile(src, "<t>", "exec")
    assert "os.system" not in src, "wait ms must be int-cast, not injected (P-2)"


def test_compile_skill_normal_trace_is_valid():
    run = AgentRun(task="search the weather in Paris", run_id="r5", status="done")
    _slug, src = compiler.compile_skill(run)
    compile(src, "<t>", "exec")  # no raise
    assert "SKILL_NAME" in src


def test_skill_review_rejects_traversal_slug():
    assert skill_review.get_pending("../../etc/passwd") is None, "traversal slug must not resolve (P-11)"
    assert skill_review.reject_pending("../../evil") is False
