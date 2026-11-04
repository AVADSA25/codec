"""Vibe agent: its tool calls through the plugin hook layer (P-067, docs/VIBE-AGENT-HOOKS-DESIGN.md).

The same Python plugins (~/.codec/plugins, behind the SHA-256 allowlist) that see crew, voice, chat and MCP tool
calls see the agent's: pre_tool, post_tool and on_error around each of its working tools, named vibe:<tool>, and one
on_operation_start / on_operation_end per run. Transport "vibe", correlation_id the run id.

A pre_tool HookVeto is honoured: the step is not run. Everything else a plugin returns is only observed: a rewrite of
the arguments (they are structured here) and a rewritten result are ignored. Hooks fail open (codec_hooks' timeout),
so they are a guard rail, never the boundary. ctx.task stays empty: codec_hooks writes a preview of the task into the
tool_vetoed audit line, and this agent never writes a path, a file's text or a command into the audit log. The call's
arguments go in ctx.context, which codec_hooks never writes there.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import codec_hooks

log = logging.getLogger("codec_vibe_agent")

TRANSPORT = "vibe"
AGENT = "vibe-agent"
PREFIX = "vibe:"
HOOKED = ("list_files", "read_file", "read_output", "search", "edit_file", "write_file", "delete_file", "run_check")
ENDED = "The run ended before this step finished."      # what post_tool gets when the run ends while the step waits


@dataclass
class Veto:
    plugin: str
    reason: str


def tool_name(name: str) -> str:
    return PREFIX + name


def _context(pid: str, run_id: str, step: int, call) -> str:
    return json.dumps({"project": pid, "run": run_id, "step": step, "tool": call.name, "args": call.args},
                      ensure_ascii=False, default=str)


def around(run_id: str, pid: str, step: int, call, invoke):
    """Run invoke() (one tool of the run, returning its Result or None) with the hooks around it.

    Returns (result, None), or (None, Veto) when a pre_tool vetoed the step and invoke() was not called. An exception
    from invoke() itself goes on up after on_error has fired; one from the hook layer is logged and the step runs, or
    keeps its result, without hooks."""
    box: dict = {}

    def _invoke(_task, _context_text):          # a pre_tool rewrite of task or context is ignored: the call runs as parsed
        box["ran"] = True
        try:
            res = invoke()
        except BaseException:
            box["raised"] = True
            raise
        box["result"] = res
        return ENDED if res is None else res.text

    try:
        out = codec_hooks.run_with_hooks(tool_name=tool_name(call.name), task="", context=_context(pid, run_id, step, call),
                                         transport=TRANSPORT, agent=AGENT, correlation_id=run_id, invoke=_invoke)
    except Exception:
        if box.get("raised"):
            raise
        log.warning("vibe agent: the plugin hooks failed around %s; the step goes on without them", call.name, exc_info=True)
        return (box["result"] if box.get("ran") else invoke()), None
    if isinstance(out, codec_hooks.HookVeto):
        return None, Veto(str(out.plugin_name or "unknown"), " ".join(str(out.reason or "").split()) or "no reason given")
    return box.get("result"), None              # a string post_tool returned is ignored: the page and the model agree


def start(run_id: str) -> None:
    """on_operation_start for one run. Never raises."""
    try:
        codec_hooks.emit_operation_start(operation_id=run_id, transport=TRANSPORT, correlation_id=run_id, agent=AGENT)
    except Exception:
        log.warning("vibe agent: on_operation_start failed", exc_info=True)


def end(run_id: str, duration_ms: float, outcome: str) -> None:
    """on_operation_end for one run, with the run's status as the outcome. Never raises."""
    try:
        codec_hooks.emit_operation_end(operation_id=run_id, transport=TRANSPORT, correlation_id=run_id,
                                       duration_ms=duration_ms, outcome=outcome, agent=AGENT)
    except Exception:
        log.warning("vibe agent: on_operation_end failed", exc_info=True)
