"""
CODEC Pilot — Phase 6: Human-In-The-Loop (HITL) Takeover
==========================================================

Allows a human to pause, inspect, manually control, and resume a
PilotAgent run mid-execution.

Architecture
------------
HitlController wraps PilotAgent.  The agent loop checks an asyncio
Event every step:

    _pause_event.is_set()  →  agent pauses, waits for resume
    _inject_queue          →  human pushes manual actions, agent executes them
    resume()               →  agent continues its ReAct loop
    takeover() / handback()→  human takes full keyboard/mouse control
                              via an injected JS overlay (when headed),
                              or via the HTTP API (when headless)

HTTP API (mounted on pilot_runner at /hitl/…)
---------------------------------------------
    POST /hitl/{run_id}/pause     pause the agent
    POST /hitl/{run_id}/resume    resume the agent
    POST /hitl/{run_id}/inject    inject a manual action step
    GET  /hitl/{run_id}/status    takeover state + inject queue length

This module is headless-safe: the human controller communicates via the
API; no UI automation or accessibility bridge is required.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .config import HITL_PAUSE_TIMEOUT_S
from .pilot_chrome import PilotChrome
from .pilot_agent import PilotAgent, AgentRun, AgentStep
from .snapshot import take_snapshot, render_for_llm


# ─── State ────────────────────────────────────────────────────────────────────

@dataclass
class HitlState:
    run_id: str
    paused: bool = False
    human_in_control: bool = False
    injected_steps: list[dict] = field(default_factory=list)
    pause_reason: str = ""
    paused_at: Optional[float] = None
    resumed_at: Optional[float] = None


# ─── Controller ───────────────────────────────────────────────────────────────

class HitlController:
    """
    Wraps a PilotAgent with pause/resume/inject capability.

    Usage:
        controller = HitlController(pilot, task="find price on amazon.com")
        # from another coroutine:
        await controller.pause("needs login")
        await controller.inject({"action": "type", "index": 2, "text": "user@example.com"})
        await controller.resume()
        run = await controller.execute()
    """

    def __init__(
        self,
        pilot: PilotChrome,
        task: str,
        run_id: str = "",
        step_budget: int = 40,
        use_stub: bool = False,
        pause_timeout_s: Optional[float] = None,
    ) -> None:
        self.pilot = pilot
        self.task = task
        self.run_id = run_id or f"hitl_{int(time.time())}"
        self.step_budget = step_budget
        self._use_stub = use_stub
        # PP-12 (P-14): bound the pause gate so an abandoned pause can't pin the
        # browser + run slot forever. None → config default (read at construction).
        self.pause_timeout_s = (
            pause_timeout_s if pause_timeout_s is not None else HITL_PAUSE_TIMEOUT_S
        )

        self._pause_event = asyncio.Event()
        self._pause_event.set()   # starts unpaused (set = may run)
        self._inject_queue: asyncio.Queue[dict] = asyncio.Queue()
        self.state = HitlState(run_id=self.run_id)

    # ── Control interface ────────────────────────────────────────────────────

    async def pause(self, reason: str = "") -> None:
        """Pause the agent at its next step boundary."""
        self._pause_event.clear()
        self.state.paused = True
        self.state.pause_reason = reason
        self.state.paused_at = time.time()

    async def resume(self) -> None:
        """Resume the agent."""
        self.state.paused = False
        self.state.human_in_control = False
        self.state.resumed_at = time.time()
        self._pause_event.set()

    async def inject(self, action: dict) -> None:
        """
        Queue a manual action to be executed before the agent's next LLM call.
        Can be called while paused or running.
        """
        self.state.injected_steps.append({**action, "_injected": True, "ts": time.time()})
        await self._inject_queue.put(action)

    async def takeover(self) -> str:
        """
        Pause the agent and mark human_in_control=True.
        Returns a snapshot of the current page for the human to inspect.
        """
        await self.pause("human takeover")
        self.state.human_in_control = True
        snap = await take_snapshot(self.pilot.page)
        return render_for_llm(snap)

    async def handback(self) -> None:
        """End human takeover and resume the agent."""
        await self.resume()

    def status(self) -> dict:
        """Return serialisable HITL state."""
        return {
            "run_id":            self.state.run_id,
            "paused":            self.state.paused,
            "human_in_control":  self.state.human_in_control,
            "pause_reason":      self.state.pause_reason,
            "inject_queue_size": self._inject_queue.qsize(),
            "injected_steps":    len(self.state.injected_steps),
            "paused_at":         self.state.paused_at,
            "resumed_at":        self.state.resumed_at,
        }

    # ── Execute ──────────────────────────────────────────────────────────────

    async def _await_resume_or_timeout(self, run: AgentRun) -> bool:
        """Block while paused, bounded by ``pause_timeout_s`` (PP-12 / audit P-14).

        Returns True if the agent may proceed (not paused, or resumed in time).
        On timeout, finalizes ``run`` as ``paused_timeout`` and returns False so the
        caller returns the run and frees the browser + run slot instead of hanging
        forever on an abandoned pause.
        """
        try:
            await asyncio.wait_for(self._pause_event.wait(), timeout=self.pause_timeout_s)
            return True
        except asyncio.TimeoutError:
            run.status = "paused_timeout"
            run.error = f"Paused >{self.pause_timeout_s:g}s with no resume"
            run.ended_at = time.time()
            return False

    async def execute(self) -> AgentRun:
        """
        Run the agent loop with HITL checkpoints between every step.

        The loop:
          1. Wait for _pause_event (blocks when paused)
          2. Drain inject queue — execute any human-injected actions
          3. LLM decides next action (same as PilotAgent)
          4. Execute action
          5. Goto 1
        """
        from .pilot_agent import StubLLM, _call_llm, _parse_action
        from .config import DEFAULT_TIMEOUT_MS

        run = AgentRun(task=self.task, run_id=self.run_id)
        stub = StubLLM(self.task) if self._use_stub else None
        history = [
            {"role": "system", "content": _SYSTEM_PROMPT_HITL},
            {"role": "user",   "content": f"Task: {self.task}"},
        ]

        for step_num in range(1, self.step_budget + 1):
            # ── 1. Pause gate (bounded — PP-12 / audit P-14) ──────────────
            if not await self._await_resume_or_timeout(run):
                return run

            # ── 2. Drain inject queue ─────────────────────────────────────
            injected = []
            while not self._inject_queue.empty():
                injected.append(await self._inject_queue.get())

            for inj_action in injected:
                inj_step = await self._execute_action(
                    inj_action, step_num, "", run
                )
                inj_step.action["_injected"] = True
                run.steps.append(inj_step)
                step_num_str = f"{step_num}i{len(injected)}"

            # ── 3. Observe ────────────────────────────────────────────────
            snap = await take_snapshot(self.pilot.page)
            snap_text = render_for_llm(snap)

            history.append({
                "role": "user",
                "content": f"Current page:\n{snap_text}\n\nNext action? (JSON only)",
            })

            # ── 4. Decide ─────────────────────────────────────────────────
            try:
                if stub:
                    action = await stub.next_action(snap_text)
                else:
                    raw = await _call_llm(history)
                    action = _parse_action(raw)
                    history.append({"role": "assistant", "content": raw})
            except Exception as exc:
                run.steps.append(AgentStep(
                    step=step_num, action={}, snapshot_before=snap_text,
                    error=f"LLM error: {exc}",
                ))
                run.status = "error"
                run.error = str(exc)
                run.ended_at = time.time()
                return run

            # ── 5. Act ────────────────────────────────────────────────────
            step = await self._execute_action(action, step_num, snap_text, run)
            run.steps.append(step)

            if action.get("action") in ("done", "error"):
                break

        if run.status == "running":
            run.status = "budget_exhausted"
            run.error  = f"Reached step budget ({self.step_budget})"
            run.ended_at = time.time()

        return run

    async def _execute_action(
        self,
        action: dict,
        step_num: int,
        snap_text: str,
        run: AgentRun,
    ) -> AgentStep:
        """Execute a single action dict and return an AgentStep."""
        from .snapshot import take_snapshot
        from .config import DEFAULT_TIMEOUT_MS

        name         = action.get("action", "")
        result_text  = ""
        step_error: Optional[str] = None
        t_xpath: Optional[str] = None
        t_css:   Optional[str] = None
        t_name:  Optional[str] = None
        t_role:  Optional[str] = None

        try:
            if name == "navigate":
                url = action["url"]
                await self.pilot.navigate(url)
                result_text = f"navigated to {url}"
                run.status  = "running"

            elif name == "click":
                snap = await take_snapshot(self.pilot.page)
                idx  = action["index"]
                el   = next((e for e in snap.elements if e.index == idx), None)
                if not el:
                    raise ValueError(f"Element [{idx}] not in snapshot")
                t_xpath, t_css, t_name, t_role = el.xpath, el.css_sel, el.name, el.role
                await self.pilot.click_xpath(el.xpath, timeout=DEFAULT_TIMEOUT_MS)
                result_text = f"clicked [{idx}]"

            elif name == "type":
                snap = await take_snapshot(self.pilot.page)
                idx  = action["index"]
                el   = next((e for e in snap.elements if e.index == idx), None)
                if not el:
                    raise ValueError(f"Element [{idx}] not in snapshot")
                t_xpath, t_css, t_name, t_role = el.xpath, el.css_sel, el.name, el.role
                await self.pilot.type_xpath(el.xpath, action.get("text", ""),
                                             timeout=DEFAULT_TIMEOUT_MS)
                result_text = f"typed into [{idx}]"

            elif name == "scroll":
                direction = action.get("direction", "down")
                amount    = int(action.get("amount", 500))
                delta_y   = amount if direction == "down" else -amount
                await self.pilot.page.evaluate(f"window.scrollBy(0, {delta_y})")
                result_text = f"scrolled {direction}"

            elif name == "wait":
                await self.pilot.wait(action.get("ms", 1000))
                result_text = "waited"

            elif name == "done":
                run.result  = action.get("result", "")
                run.status  = "done"
                run.ended_at = time.time()

            elif name == "error":
                run.error   = action.get("reason", "unknown")
                run.status  = "error"
                run.ended_at = time.time()

            else:
                step_error = f"unknown action: {name}"

        except Exception as exc:
            step_error = str(exc)

        return AgentStep(
            step=step_num,
            action=action,
            snapshot_before=snap_text,
            result=result_text,
            error=step_error,
            target_xpath=t_xpath,
            target_css=t_css,
            target_name=t_name,
            target_role=t_role,
        )


_SYSTEM_PROMPT_HITL = """\
You are a browser automation agent with human-in-the-loop support.
A human operator may pause you, inject manual steps, or take over at any time.
Continue where you left off after any human intervention.

Respond with ONLY a JSON action on a single line.
Actions: navigate/click/type/scroll/wait/done/error
"""
