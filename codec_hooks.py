"""CODEC Plugin Lifecycle Hooks — unified surface across all 5 execution paths.

Local Python files in ~/.codec/plugins/*.py register lifecycle handlers
(pre_tool, post_tool, on_error, on_operation_start, on_operation_end)
that fire identically from crew, voice, chat pre-LLM, chat post-LLM tag,
and MCP (stdio + HTTP) tool invocations.

Discovery: AST parse at startup mirrors codec_skill_registry — broken
plugins don't break startup; module imports are deferred to first hook
fire.

Audit: every successful hook fire emits `hook_fired`; hook-internal
exceptions emit `hook_error` (level=warning, never `error` — operation
still succeeded). pre_tool veto emits `tool_vetoed`. correlation_id
inherits from the wrapping operation per Step 1 §1.4 — never regenerated.

Trust model: hooks are local Python written or vetted by the user. No
marketplace, no auto-install, no isolation. Same as skills.

See docs/PHASE1-STEP2-DESIGN.md for the full contract.
"""
from __future__ import annotations

import ast
import importlib.util
import logging
import os
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Union

from codec_audit import log_event as _log_event, _PREVIEW_MAX, _truncate

log = logging.getLogger("codec_hooks")

# ── Storage ────────────────────────────────────────────────────────────────────
_PLUGINS_DIR_DEFAULT = os.path.expanduser("~/.codec/plugins")

# Lifecycle hook names. AST discovery looks for top-level def's matching
# any of these. Plugins implement any subset.
_HOOK_NAMES = (
    "pre_tool",
    "post_tool",
    "on_error",
    "on_operation_start",
    "on_operation_end",
)

# Fields a pre_tool hook MAY mutate via its return dict. Anything else
# returned in the dict is dropped with a warning. Per design §6 + §11 Q2.
_MUTABLE_PRE_TOOL_FIELDS = ("task", "context")

# Identity fields a pre_tool hook MUST NOT mutate. Listed explicitly so
# the warning log message can name which one was attempted. Per §11 Q2.
_IMMUTABLE_IDENTITY_FIELDS = (
    "tool_name",
    "transport",
    "agent",
    "correlation_id",
    "client_id",
    "operation_id",
)

_DEFAULT_PRIORITY = 100

# Default identifier used when a plugin omits PLUGIN_NAME — the file stem.
_PLUGIN_FILE_SUFFIX = ".py"


# ── HookCtx + HookVeto ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class HookCtx:
    """Read-only context passed to every hook. Frozen — mutation via return only.

    Per design §1.4. The fields populated depend on which hook is firing:
        pre_tool / post_tool / on_error: tool_name, task, context, agent, client_id
        on_operation_start / on_operation_end: operation_id; on_operation_end
            also sets duration_ms + outcome
    `transport`, `correlation_id`, `plugin_name`, `timestamp_utc` are always set.
    """
    transport: str
    correlation_id: str
    plugin_name: str
    timestamp_utc: str

    tool_name: Optional[str] = None
    task: Optional[str] = None
    context: Optional[str] = None
    agent: Optional[str] = None
    client_id: Optional[str] = None

    operation_id: Optional[str] = None
    duration_ms: Optional[float] = None
    outcome: Optional[str] = None


class HookVeto:
    """Sentinel returned by pre_tool to abort a tool invocation. Not raised."""
    __slots__ = ("reason", "plugin_name")

    def __init__(self, reason: str, *, plugin_name: Optional[str] = None):
        self.reason = (reason or "")[:200]
        self.plugin_name = plugin_name

    def __repr__(self) -> str:  # pragma: no cover — debug aid only
        return f"HookVeto(plugin={self.plugin_name!r}, reason={self.reason!r})"


# ── Plugin metadata + registry ─────────────────────────────────────────────────
@dataclass
class _PluginMeta:
    name: str
    description: str
    priority: int
    tool_filter: Optional[List[str]]   # exact tool names; None = all tools
    file_path: str
    # Hook names declared at module top-level via AST. Functions are loaded
    # lazily on first hook fire — module not imported until needed.
    declared_hooks: List[str] = field(default_factory=list)

    def applies_to(self, tool_name: Optional[str]) -> bool:
        """Does this plugin apply to the given tool? None tool_name = always (operation hook)."""
        if self.tool_filter is None:
            return True
        if tool_name is None:
            return True   # operation hooks fire regardless of tool_name
        return tool_name in self.tool_filter


def _extract_metadata(filepath: str) -> Optional[_PluginMeta]:
    """AST-parse a plugin file; return _PluginMeta or None.

    Mirrors codec_skill_registry._extract_metadata. Never executes the
    module. Looks for top-level constants (PLUGIN_*) and top-level def's
    matching the lifecycle hook names.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, OSError) as e:
        log.warning("Plugin metadata parse error (%s): %s", filepath, e)
        return None

    name: Optional[str] = None
    description = ""
    priority = _DEFAULT_PRIORITY
    tool_filter: Optional[List[str]] = None
    declared_hooks: List[str] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                tid = target.id
                if tid == "PLUGIN_NAME":
                    try:
                        name = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        pass
                elif tid == "PLUGIN_DESCRIPTION":
                    try:
                        description = ast.literal_eval(node.value) or ""
                    except (ValueError, TypeError):
                        pass
                elif tid == "PLUGIN_PRIORITY":
                    try:
                        v = ast.literal_eval(node.value)
                        if isinstance(v, int):
                            priority = v
                    except (ValueError, TypeError):
                        pass
                elif tid == "PLUGIN_TOOL_FILTER":
                    try:
                        v = ast.literal_eval(node.value)
                        if v is None:
                            tool_filter = None
                        elif isinstance(v, (list, tuple)):
                            tool_filter = [str(x) for x in v]
                    except (ValueError, TypeError):
                        pass
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in _HOOK_NAMES:
                declared_hooks.append(node.name)

    if not declared_hooks:
        # No lifecycle functions → not a plugin. Skip silently.
        return None

    if name is None:
        # Default: filename stem (matches skill convention).
        name = os.path.basename(filepath)
        if name.endswith(_PLUGIN_FILE_SUFFIX):
            name = name[:-len(_PLUGIN_FILE_SUFFIX)]

    return _PluginMeta(
        name=name,
        description=description,
        priority=priority,
        tool_filter=tool_filter,
        file_path=filepath,
        declared_hooks=declared_hooks,
    )


class PluginRegistry:
    """AST-discover plugins; lazy-load modules on first hook fire.

    Exposed for tests; production uses the module-level _registry instance.
    """

    def __init__(self, plugins_dir: str):
        self.plugins_dir = plugins_dir
        # Sort key: (priority asc, filename asc) per §5.1
        self._plugins: List[_PluginMeta] = []
        # name → loaded module (populated on first fire)
        self._modules: Dict[str, Any] = {}
        # Plugin names that failed to import — skipped on every fire.
        self._broken: set[str] = set()
        self._lock = threading.Lock()

    def scan(self) -> int:
        """AST-parse every plugin file; cache metadata. Cheap — no imports."""
        plugins: List[_PluginMeta] = []
        if os.path.isdir(self.plugins_dir):
            for fname in sorted(os.listdir(self.plugins_dir)):
                if not fname.endswith(_PLUGIN_FILE_SUFFIX):
                    continue
                if fname.startswith("_"):
                    continue
                fpath = os.path.join(self.plugins_dir, fname)
                meta = _extract_metadata(fpath)
                if meta is not None:
                    plugins.append(meta)
        # Sort by (priority, filename) — lower priority runs first.
        plugins.sort(key=lambda m: (m.priority, os.path.basename(m.file_path)))
        with self._lock:
            self._plugins = plugins
        log.info("Plugin registry: %d plugins discovered (metadata only)", len(plugins))
        return len(plugins)

    def all(self) -> List[_PluginMeta]:
        with self._lock:
            return list(self._plugins)

    def for_hook(self, hook_name: str, tool_name: Optional[str] = None) -> List[_PluginMeta]:
        """Plugins that declared `hook_name` AND apply to `tool_name`."""
        with self._lock:
            plugins = list(self._plugins)
        return [p for p in plugins
                if hook_name in p.declared_hooks and p.applies_to(tool_name)
                and p.name not in self._broken]

    def get_fn(self, plugin: _PluginMeta, hook_name: str) -> Optional[Callable]:
        """Lazy-load the plugin module and return the named hook function.

        Returns None on import failure (plugin marked broken) or if the
        function turns out to not be defined at runtime.
        """
        if plugin.name in self._broken:
            return None
        mod = self._modules.get(plugin.name)
        if mod is None:
            try:
                import sys
                module_name = f"codec_plugin_{plugin.name}"
                spec = importlib.util.spec_from_file_location(
                    module_name, plugin.file_path)
                if spec is None or spec.loader is None:
                    raise ImportError(f"could not build spec for {plugin.file_path}")
                mod = importlib.util.module_from_spec(spec)
                # Register in sys.modules BEFORE exec_module so the plugin can
                # import itself transitively without re-loading. Standard
                # importlib pattern; mirrors codec_skill_registry's caching.
                sys.modules[module_name] = mod
                try:
                    spec.loader.exec_module(mod)
                except BaseException:
                    sys.modules.pop(module_name, None)  # roll back on failure
                    raise
                self._modules[plugin.name] = mod
                log.info("Lazy-loaded plugin: %s", plugin.name)
            except Exception as e:
                log.warning("Plugin import error (%s): %s", plugin.name, e)
                self._broken.add(plugin.name)
                return None
        fn = getattr(mod, hook_name, None)
        if not callable(fn):
            return None
        return fn


# Module-level registry. Tests can monkeypatch _registry to point at a temp dir.
_registry: PluginRegistry = PluginRegistry(_PLUGINS_DIR_DEFAULT)
_registry.scan()


# ── Audit emit helper ──────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _emit_hook_fired(*, plugin_name: str, hook_name: str,
                     tool_name: Optional[str], transport: str,
                     correlation_id: str, duration_ms: float,
                     mutated: bool, vetoed: bool) -> None:
    """Fire-and-forget hook_fired emit. Never raises."""
    extra = {
        "plugin_name": plugin_name,
        "hook_name": hook_name,
        "tool_name": tool_name,
        "mutated": bool(mutated),
        "vetoed": bool(vetoed),
    }
    try:
        _log_event(
            "hook_fired", "codec-hooks",
            extra=extra,
            outcome="ok",
            level="info",
            transport=transport,
            duration_ms=duration_ms,
            correlation_id=correlation_id,
        )
    except Exception as e:
        log.debug("hook_fired emit failed: %s", e)


def _emit_hook_error(*, plugin_name: str, hook_name: str,
                     tool_name: Optional[str], transport: str,
                     correlation_id: str, duration_ms: float,
                     exc: BaseException) -> None:
    """Per design §7.5. level='warning' (operation still succeeded)."""
    err_type = type(exc).__name__
    err_msg = _truncate(str(exc), _PREVIEW_MAX)
    try:
        _log_event(
            "hook_error", "codec-hooks",
            f"plugin {plugin_name}.{hook_name} raised {err_type}",
            extra={
                "plugin_name": plugin_name,
                "hook_name": hook_name,
                "tool_name": tool_name,
            },
            outcome="error",
            level="warning",       # NOT "error" — operation still succeeded
            transport=transport,
            duration_ms=duration_ms,
            error_type=err_type,
            error=err_msg,
            tool=tool_name or "",
            correlation_id=correlation_id,
        )
    except Exception as e:
        log.debug("hook_error emit failed: %s", e)


def _emit_tool_vetoed(*, tool_name: str, transport: str, correlation_id: str,
                      veto: HookVeto, duration_ms: float,
                      task_preview: Optional[str] = None) -> None:
    """Per design §4.3."""
    extra = {
        "veto_reason": (veto.reason or "")[:_PREVIEW_MAX],
        "plugin_name": veto.plugin_name,
    }
    if task_preview is not None:
        extra["task_preview"] = _truncate(task_preview, _PREVIEW_MAX)
    try:
        _log_event(
            "tool_vetoed", "codec-hooks",
            f"{tool_name} vetoed by {veto.plugin_name}",
            extra=extra,
            outcome="denied",
            level="warning",
            transport=transport,
            tool=tool_name,
            duration_ms=duration_ms,
            correlation_id=correlation_id,
        )
    except Exception as e:
        log.debug("tool_vetoed emit failed: %s", e)


def _fire_one_pre_tool(plugin: _PluginMeta, ctx: HookCtx) -> Any:
    """Run one plugin's pre_tool. Returns None / dict / HookVeto / sentinel-skip.

    Internal contract: on plugin exception, logs + emits hook_error and
    returns None (skip — operation continues with unmutated state).
    """
    fn = _registry.get_fn(plugin, "pre_tool")
    if fn is None:
        return None
    plugin_ctx = replace(ctx, plugin_name=plugin.name)
    t0 = time.monotonic()
    try:
        ret = fn(plugin_ctx)
    except BaseException as e:
        elapsed = (time.monotonic() - t0) * 1000.0
        _emit_hook_error(
            plugin_name=plugin.name, hook_name="pre_tool",
            tool_name=ctx.tool_name, transport=ctx.transport,
            correlation_id=ctx.correlation_id, duration_ms=elapsed, exc=e,
        )
        return None
    elapsed = (time.monotonic() - t0) * 1000.0
    mutated = isinstance(ret, dict) and bool(ret)
    vetoed = isinstance(ret, HookVeto)
    if vetoed and ret.plugin_name is None:
        # Stamp the plugin name so callers know who vetoed.
        ret.plugin_name = plugin.name
    _emit_hook_fired(
        plugin_name=plugin.name, hook_name="pre_tool",
        tool_name=ctx.tool_name, transport=ctx.transport,
        correlation_id=ctx.correlation_id, duration_ms=elapsed,
        mutated=mutated, vetoed=vetoed,
    )
    return ret


def _fire_one_post_tool(plugin: _PluginMeta, ctx: HookCtx, result: str) -> Any:
    fn = _registry.get_fn(plugin, "post_tool")
    if fn is None:
        return None
    plugin_ctx = replace(ctx, plugin_name=plugin.name)
    t0 = time.monotonic()
    try:
        ret = fn(plugin_ctx, result)
    except BaseException as e:
        elapsed = (time.monotonic() - t0) * 1000.0
        _emit_hook_error(
            plugin_name=plugin.name, hook_name="post_tool",
            tool_name=ctx.tool_name, transport=ctx.transport,
            correlation_id=ctx.correlation_id, duration_ms=elapsed, exc=e,
        )
        return None
    elapsed = (time.monotonic() - t0) * 1000.0
    mutated = isinstance(ret, str)
    _emit_hook_fired(
        plugin_name=plugin.name, hook_name="post_tool",
        tool_name=ctx.tool_name, transport=ctx.transport,
        correlation_id=ctx.correlation_id, duration_ms=elapsed,
        mutated=mutated, vetoed=False,
    )
    return ret


def _fire_one_observe(plugin: _PluginMeta, hook_name: str, ctx: HookCtx,
                      *extra_args: Any) -> None:
    """Run one observe-only hook (on_error / on_operation_*). Return ignored."""
    fn = _registry.get_fn(plugin, hook_name)
    if fn is None:
        return
    plugin_ctx = replace(ctx, plugin_name=plugin.name)
    t0 = time.monotonic()
    try:
        fn(plugin_ctx, *extra_args)
    except BaseException as e:
        elapsed = (time.monotonic() - t0) * 1000.0
        _emit_hook_error(
            plugin_name=plugin.name, hook_name=hook_name,
            tool_name=ctx.tool_name, transport=ctx.transport,
            correlation_id=ctx.correlation_id, duration_ms=elapsed, exc=e,
        )
        return
    elapsed = (time.monotonic() - t0) * 1000.0
    _emit_hook_fired(
        plugin_name=plugin.name, hook_name=hook_name,
        tool_name=ctx.tool_name, transport=ctx.transport,
        correlation_id=ctx.correlation_id, duration_ms=elapsed,
        mutated=False, vetoed=False,
    )


# ── Mutation-contract enforcement ──────────────────────────────────────────────
def _apply_pre_tool_mutation(plugin_name: str, ret: Any,
                             task: str, context: str) -> tuple[str, str]:
    """Validate + apply a pre_tool dict return. Drops immutable-identity
    fields with a warning. Per design §6 + §11 Q2.
    """
    if not isinstance(ret, dict):
        # Anything else (False, [], a frozen ctx) is a typo — log + ignore.
        if ret is not None:
            log.warning("[hooks] plugin %s pre_tool returned %s; ignored",
                        plugin_name, type(ret).__name__)
        return task, context
    new_task = task
    new_context = context
    for k, v in ret.items():
        if k in _IMMUTABLE_IDENTITY_FIELDS:
            log.warning("[hooks] plugin %s tried to mutate immutable field %r; "
                        "ignored", plugin_name, k)
            continue
        if k == "task":
            if isinstance(v, str):
                new_task = v
            else:
                log.warning("[hooks] plugin %s pre_tool returned non-str task; "
                            "ignored", plugin_name)
        elif k == "context":
            if isinstance(v, str):
                new_context = v
            else:
                log.warning("[hooks] plugin %s pre_tool returned non-str "
                            "context; ignored", plugin_name)
        else:
            # Unknown key — just warn; not necessarily harmful but signals a typo.
            log.warning("[hooks] plugin %s pre_tool returned unknown key %r; "
                        "ignored", plugin_name, k)
    return new_task, new_context


# ── Public emitters ────────────────────────────────────────────────────────────
def run_with_hooks(
    *,
    tool_name: str,
    task: str,
    context: str = "",
    transport: str,
    agent: Optional[str] = None,
    client_id: Optional[str] = None,
    correlation_id: str,
    invoke: Callable[[str, str], str],
) -> Union[str, HookVeto]:
    """Orchestrate pre/post/on_error hooks around invoke(task, context).

    Per design §3.1. Never raises in the hook layer. If invoke raises,
    on_error fires and the exception is re-raised so the call site's
    existing audit emit + error-formatting paths are unchanged.

    Returns the post-hook result string, or a HookVeto sentinel if any
    pre_tool returned one. The first veto wins; subsequent pre_tool
    hooks in the chain do not fire after a veto.
    """
    ctx = HookCtx(
        transport=transport,
        correlation_id=correlation_id,
        plugin_name="",                # set per-fire by _fire_one_*
        timestamp_utc=_now_iso(),
        tool_name=tool_name,
        task=task,
        context=context,
        agent=agent,
        client_id=client_id,
    )

    # 1. pre_tool chain
    plugins_pre = _registry.for_hook("pre_tool", tool_name)
    cur_task = task
    cur_context = context
    veto_t0 = time.monotonic()
    for p in plugins_pre:
        # Each fire sees the current (possibly mutated) task/context
        ctx_now = replace(ctx, task=cur_task, context=cur_context)
        ret = _fire_one_pre_tool(p, ctx_now)
        if isinstance(ret, HookVeto):
            _emit_tool_vetoed(
                tool_name=tool_name, transport=transport,
                correlation_id=correlation_id, veto=ret,
                duration_ms=(time.monotonic() - veto_t0) * 1000.0,
                task_preview=cur_task,
            )
            return ret
        cur_task, cur_context = _apply_pre_tool_mutation(
            p.name, ret, cur_task, cur_context)

    # 2. invoke. If it raises, fire on_error, then re-raise.
    try:
        result = invoke(cur_task, cur_context)
    except BaseException as exc:
        plugins_err = _registry.for_hook("on_error", tool_name)
        ctx_err = replace(ctx, task=cur_task, context=cur_context)
        for p in plugins_err:
            _fire_one_observe(p, "on_error", ctx_err, exc)
        raise

    # 3. post_tool chain — chain mutations: A's output is B's input
    plugins_post = _registry.for_hook("post_tool", tool_name)
    cur_result = result if isinstance(result, str) else (str(result) if result is not None else "")
    for p in plugins_post:
        ctx_now = replace(ctx, task=cur_task, context=cur_context)
        ret = _fire_one_post_tool(p, ctx_now, cur_result)
        if ret is None:
            continue
        if isinstance(ret, str):
            cur_result = ret
        else:
            log.warning("[hooks] plugin %s post_tool returned %s; ignored",
                        p.name, type(ret).__name__)

    return cur_result


def emit_operation_start(
    *,
    operation_id: str,
    transport: str,
    correlation_id: str,
    agent: Optional[str] = None,
    client_id: Optional[str] = None,
) -> None:
    """Fire on_operation_start hooks for every registered plugin.

    Called from voice WebSocket session start, crew run start, chat
    request handler. Not fired for individual MCP tool calls (those
    don't form an operation envelope).
    """
    ctx = HookCtx(
        transport=transport,
        correlation_id=correlation_id,
        plugin_name="",
        timestamp_utc=_now_iso(),
        agent=agent,
        client_id=client_id,
        operation_id=operation_id,
    )
    for p in _registry.for_hook("on_operation_start", tool_name=None):
        _fire_one_observe(p, "on_operation_start", ctx)


def emit_operation_end(
    *,
    operation_id: str,
    transport: str,
    correlation_id: str,
    duration_ms: float,
    outcome: str = "ok",
    agent: Optional[str] = None,
    client_id: Optional[str] = None,
) -> None:
    """Fire on_operation_end hooks. Mirrors emit_operation_start."""
    ctx = HookCtx(
        transport=transport,
        correlation_id=correlation_id,
        plugin_name="",
        timestamp_utc=_now_iso(),
        agent=agent,
        client_id=client_id,
        operation_id=operation_id,
        duration_ms=duration_ms,
        outcome=outcome,
    )
    for p in _registry.for_hook("on_operation_end", tool_name=None):
        _fire_one_observe(p, "on_operation_end", ctx)


__all__ = [
    "HookCtx",
    "HookVeto",
    "PluginRegistry",
    "run_with_hooks",
    "emit_operation_start",
    "emit_operation_end",
]
