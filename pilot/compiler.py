"""
CODEC Pilot — Phase 5: Script Compiler
=========================================

Distils an AgentRun trace into a compact, replayable Python script.

The compiler strips failed steps, normalises actions into a clean
sequence, and emits a self-contained async Python script that imports
PilotChrome and re-runs the browsing session without an LLM.

Usage:
    from pilot.compiler import compile_trace, save_script
    from pilot.trace import load_trace

    run = load_trace("run_abc123")
    script = compile_trace(run)
    path = save_script(run.run_id, script)
"""

from __future__ import annotations

import textwrap
import time
from pathlib import Path
from typing import Optional

from .config import PILOT_TRACES_DIR
from .pilot_agent import AgentRun, AgentStep
from .skill_review import save_pending, slugify


# ─── PP-2 (audit P-2): injection-safe interpolation helpers ───────────────────
def _safe(s) -> str:
    """Neutralize a trace-derived string for embedding in a generated docstring or
    `# comment`: strip backslashes + collapse the triple-quote and newlines that
    would otherwise break out into module-level code. Attacker-influenced fields
    (task, status, result, index) flow through here."""
    return (str(s).replace("\\", "")
            .replace('"""', "'''").replace("'''", "'")
            .replace("\n", " ").replace("\r", " "))[:200]


def _int(v, default: int) -> int:
    """Coerce a trace-derived numeric to int (scroll amount / wait ms are spliced
    into evaluate()/wait() positions); fall back on anything non-numeric so a
    string payload can't be injected."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ─── Compiler ─────────────────────────────────────────────────────────────────

def compile_trace(run: AgentRun) -> str:
    """
    Compile an AgentRun into a replayable Python script string.

    Only successful steps are included (steps where error is None
    and action is navigate/click/type/scroll/wait).
    """
    lines: list[str] = []

    # Header
    lines += [
        '"""',
        'Compiled Pilot Script',
        f'Task    : {_safe(run.task)}',
        f'Run ID  : {_safe(run.run_id)}',
        f'Status  : {_safe(run.status)}',
        f'Steps   : {len(run.steps)}',
        '"""',
        "import asyncio",
        "import sys",
        "from pathlib import Path",
        "sys.path.insert(0, str(Path(__file__).parent.parent))",
        "from pilot.pilot_chrome import pilot_session",
        "",
        "",
        "async def run():",
        "    async with pilot_session(headless=False) as pilot:",
    ]

    # Action steps
    action_lines = _compile_steps(run.steps)
    if action_lines:
        lines += ["        " + ln for ln in action_lines]
    else:
        lines.append("        pass  # no successful actions to replay")

    # Footer
    lines += [
        "",
        "",
        'if __name__ == "__main__":',
        '    asyncio.run(run())',
    ]

    return "\n".join(lines) + "\n"


def _compile_steps(steps: list[AgentStep]) -> list[str]:
    """Return a list of Python statement strings for each successful step."""
    out: list[str] = []
    for step in steps:
        if step.error:
            out.append(f"# SKIPPED step {step.step} (error: {step.error[:60]})")
            continue
        act = step.action
        name = act.get("action", "")

        if name == "navigate":
            url = act.get("url", "")
            out.append(f'await pilot.navigate({url!r})')

        elif name == "click":
            xpath = step.target_xpath or ""
            idx   = _safe(act.get("index", "?"))
            name_str = step.target_name or ""
            if xpath:
                out.append(f'await pilot.click_xpath({xpath!r})  # [{idx}] {name_str!r}')
            else:
                out.append(f'# SKIPPED click [{idx}] — no XPath captured')

        elif name == "type":
            xpath = step.target_xpath or ""
            text  = act.get("text", "")
            idx   = _safe(act.get("index", "?"))
            name_str = step.target_name or ""
            if xpath:
                out.append(f'await pilot.type_xpath({xpath!r}, {text!r})  # [{idx}] {name_str!r}')
            else:
                out.append(f'# SKIPPED type [{idx}] — no XPath captured')

        elif name == "scroll":
            direction = act.get("direction", "down")
            amount    = _int(act.get("amount", 500), 500)  # P-2: int-cast, no injection
            delta_y = amount if direction == "down" else -amount
            out.append(f'await pilot.page.evaluate("window.scrollBy(0, {delta_y})")')

        elif name == "wait":
            ms = _int(act.get("ms", 1000), 1000)  # P-2: int-cast, no injection
            out.append(f'await pilot.wait({ms})')

        elif name in ("done", "error"):
            result = _safe(act.get("result") or act.get("reason", ""))
            out.append(f'# {name.upper()}: {result}')

        else:
            out.append(f'# unknown action: {_safe(name)}')

    return out


# ─── File I/O ─────────────────────────────────────────────────────────────────

def save_script(
    run_id: str,
    script: str,
    traces_dir: Path = PILOT_TRACES_DIR,
) -> Path:
    """Write compiled script to {traces_dir}/{run_id}/script.py. Returns path."""
    run_dir = traces_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "script.py"
    with open(path, "w", encoding="utf-8") as f:
        f.write(script)
    return path


# ─── Replayer-based skill template (blueprint §5) ─────────────────────────────

def compile_skill(run: AgentRun, slug: Optional[str] = None) -> tuple[str, str]:
    """
    Emit a Replayer-based Python skill that re-runs the trace deterministically.

    The compiled file imports Replayer + loads the trace JSON from disk,
    so changes to selector logic stay centralised in `pilot/replay.py`.

    Returns (slug, source).
    """
    slug = slug or slugify(run.task) or f"run_{run.run_id}"

    successful = sum(1 for s in run.steps if not s.error)
    total      = len(run.steps)

    source = textwrap.dedent(f'''\
        """
        CODEC Pilot skill — auto-generated.

        Goal     : {_safe(run.task)}
        Trace ID : {_safe(run.run_id)}
        Status   : {_safe(run.status)}
        Steps    : {successful}/{total} successful at record time
        Created  : {time.strftime("%Y-%m-%d %H:%M:%S")}
        Source   : ~/.codec/pilot_traces/{_safe(run.run_id)}/trace.json

        This file was generated by CODEC Pilot's trace compiler. Edit at your
        own risk — regenerating from the trace will overwrite changes.
        """
        from __future__ import annotations
        import asyncio
        from pathlib import Path
        from pilot.pilot_chrome import pilot_session
        from pilot.replay import Replayer
        from pilot.trace import load_trace

        TRACE_ID = "{run.run_id}"

        SKILL_NAME        = "pilot_{slug}"
        SKILL_DESCRIPTION = {run.task!r}
        SKILL_TAGS        = ["pilot", "browser-automation", "auto-generated"]


        async def run(allow_llm_rescue: bool = True, headless: bool = True) -> dict:
            """Replay the recorded automation. Returns the ReplayResult.to_dict()."""
            trace = load_trace(TRACE_ID)
            async with pilot_session(headless=headless) as pilot:
                replayer = Replayer(pilot, allow_llm_rescue=allow_llm_rescue)
                result   = await replayer.replay(trace)
                return result.to_dict()


        if __name__ == "__main__":
            print(asyncio.run(run()))
        ''')

    # P-2: fail closed if interpolation ever produced non-compiling source
    # (a missed injection that breaks syntax) rather than writing it to a skill.
    try:
        compile(source, f"<pilot-skill:{slug}>", "exec")
    except SyntaxError as e:
        raise ValueError(f"refusing to emit non-compiling skill source: {e}") from e

    return slug, source


def compile_to_pending(run: AgentRun, slug: Optional[str] = None) -> Path:
    """
    One-shot: compile the trace + save into `~/.codec/skills/.pending/`.

    Used by the runner immediately after a successful run, so the user
    can review the auto-generated skill in the dashboard before approving.
    """
    final_slug, source = compile_skill(run, slug=slug)
    return save_pending(final_slug, source)


# ─── Replay ───────────────────────────────────────────────────────────────────

async def replay_trace(run: AgentRun, pilot) -> list[str]:
    """
    Replay a compiled trace against a live PilotChrome instance.

    Executes each successful step in order.  Returns list of result strings.
    Unlike running a compiled script, this resolves element indices against
    a live take_snapshot() call so the XPath is always accurate.
    """
    from .snapshot import take_snapshot

    results: list[str] = []
    for step in run.steps:
        if step.error:
            results.append(f"[{step.step}] SKIPPED (was error)")
            continue

        act  = step.action
        name = act.get("action", "")

        try:
            if name == "navigate":
                await pilot.navigate(act["url"])
                results.append(f"[{step.step}] navigated → {act['url']}")

            elif name in ("click", "type"):
                snap = await take_snapshot(pilot.page)
                idx  = act.get("index")
                el   = next((e for e in snap.elements if e.index == idx), None)
                if not el:
                    results.append(f"[{step.step}] SKIP click/type [{idx}]: not in snapshot")
                    continue
                if name == "click":
                    await pilot.click_xpath(el.xpath)
                    results.append(f"[{step.step}] clicked [{idx}] '{el.name}'")
                else:
                    await pilot.type_xpath(el.xpath, act.get("text", ""))
                    results.append(f"[{step.step}] typed into [{idx}]")

            elif name == "scroll":
                direction = act.get("direction", "down")
                amount    = int(act.get("amount", 500))
                delta_y = amount if direction == "down" else -amount
                await pilot.page.evaluate(f"window.scrollBy(0, {delta_y})")
                results.append(f"[{step.step}] scrolled {direction}")

            elif name == "wait":
                await pilot.wait(act.get("ms", 1000))
                results.append(f"[{step.step}] waited")

            elif name in ("done", "error"):
                results.append(f"[{step.step}] {name.upper()}")

        except Exception as exc:
            results.append(f"[{step.step}] ERROR: {exc}")

    return results
