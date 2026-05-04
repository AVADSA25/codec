# PHASE 1 STEP 2 — Plugin Lifecycle Hooks

**Status:** DESIGN v2 — §11 RESOLVED. Ready for implementation prompt.
**Author:** drafted by Claude Code, reviewed by Mickael + Claude chat 2026-04-30.
**Depends on:** Phase 1 Step 1 (commit `45d4aa7` on `main`) — unified audit envelope (`schema:1`) + `correlation_id` contract from `docs/PHASE1-STEP1-DESIGN.md` §1.4.
**Scope:** define a single hook system that lets local Python files in `~/.codec/plugins/*.py` register lifecycle handlers around skill/tool execution, fired identically from all five execution paths. **No code changes in this step.**

**v1 → v2 changes (2026-04-30 reviewer pass):** all 6 §11 questions resolved; three carried tightenings — Q2 added `tool_name` to the immutable-identity-fields list (§6), Q4 added the `hook_error` event spec (§7.5) with `level: "warning"` so a buggy plugin doesn't inflate operation error rates, Q6 renamed `on_session_*` / `session_id` to `on_operation_*` / `operation_id` for vocabulary consistency with Step 1 §1.4 ("operation"). Mass-rename touched ~21 lines across §1, §2, §5, §6, §7, §9, §12, §13. No behavior change implied — purely the contract before code.

---

## 0 · Why this exists

CODEC has five paths that invoke skills/tools today, each landing in its own audit emit but with **no shared extension surface**:

| # | Path | Entry function | Skill invocation site |
|---|---|---|---|
| 1 | Crew runtime | `codec_agents.py:Agent.run` | line 519: `result = await loop.run_in_executor(None, ctx.run, tool.run, tool_input)` |
| 2a | Voice (WebSocket) | `codec_voice.py:VoicePipeline.dispatch_skill` | line 668: `result = await loop.run_in_executor(None, skill["run"], user_text)` |
| 2b | Voice (wake-word) + slash + chat fallback | `codec_dispatch.py:run_skill` | line 61: `result = registry.run(skill_name, task, app)` |
| 3 | Dashboard chat — pre-LLM hijack | `codec_dashboard.py:_try_skill` (line 2127) → `codec_dispatch.run_skill` | inherits 2b |
| 4 | Dashboard chat — post-LLM `[SKILL:]` tag | `codec_dashboard.py:_try_skill_by_name` (line 2144) → `codec_dispatch.run_skill` | inherits 2b |
| 5 | MCP (stdio + HTTP) | `codec_mcp.py:tool_fn` (closure inside `_load_skill_tools_into`) | line 201: `return mod.run(task, context), None` |

Three concrete consequences of the missing hook system:

1. **`codec_self_improve.py` can't observe skill calls in real time.** It re-parses `audit.log` after the fact (see §6 for the planned migration to a hook in Phase 1 Step 4). This is a polling architecture for a system that should be event-driven.
2. **No way to gate or modify skill calls without forking the engine.** A user who wants "block any skill call that mentions a personal email address" has to monkey-patch each of the 5 entry points.
3. **No common observability surface.** `audit.log` shows what happened, but a custom telemetry sink (Sentry, Honeycomb, a local cost tracker) would have to listen to the audit file or duplicate logic in 5 places.

This step **adds the seam, wires it identically into all 5 paths, and inherits the audit envelope (schema:1) + correlation_id contract from Step 1**. It does not add hot-reload, async hooks, a marketplace, or any sandbox between plugins. Those are deferred — see §11 open questions.

---

## 1 · Hook lifecycle enumeration

### 1.1 Hook set in this step

Five hooks. All sync, all observe-or-mutate (never async). Scope of each:

| Hook | Fires when | Receives | May return | Audit event when this hook fires |
|---|---|---|---|---|
| `pre_tool(ctx)` | Just before any tool/skill is invoked from one of the 5 paths. After input validation (so `task`/`context` are clean). | `HookCtx` (see §1.4) | `None` (unchanged) \| `dict` (mutated `task`/`context`) \| `HookVeto` (abort) | `hook_fired` with `extra.hook_name="pre_tool"`, plus `hook_error` if the hook itself raises (§7.5) |
| `post_tool(ctx, result)` | Just after the tool returns. After `tool_result` is logged but **before** the result reaches the caller. | `HookCtx`, `result: str` | `None` (unchanged) \| `str` (replacement result) | `hook_fired` with `extra.hook_name="post_tool"`, plus `hook_error` if the hook itself raises (§7.5) |
| `on_error(ctx, exc)` | If invoke raises. Receives the exception object. After the path's existing error-audit emit (`tool_result` with `outcome="error"`) but before the path's own error-formatting/return. | `HookCtx`, `exc: BaseException` | Return value ignored. | `hook_fired` with `extra.hook_name="on_error"`, plus `hook_error` if the hook itself raises (§7.5) |
| `on_operation_start(ctx)` | At the start of any user-facing operation: `voice_session_start`, `crew_start`, `chat_command` (the first `_try_skill` hit per request). Not fired for individual MCP tool calls — those don't form an "operation envelope." Naming aligns with the §1.4 Step 1 correlation_id contract vocabulary ("any operation emitting ≥2 audit lines"); does **not** collide with the existing `voice_session_*` audit events which retain their Step 1 names. | `HookCtx` (no `task`/`context`/`tool_name`; sets `operation_id`, `transport`, `agent`) | Return value ignored. | `hook_fired` with `extra.hook_name="on_operation_start"`, plus `hook_error` if the hook itself raises (§7.5) |
| `on_operation_end(ctx)` | At the end of the same operations: `voice_session_end`, `crew_complete`/`crew_error`, end of chat request handler. | `HookCtx` (adds `duration_ms`, `outcome`) | Return value ignored. | `hook_fired` with `extra.hook_name="on_operation_end"`, plus `hook_error` if the hook itself raises (§7.5) |

### 1.2 Position relative to the audit envelope (Step 1)

For a single tool invocation, the order is:

```
1. Operation-level audit emit ─ tool_call (existing, unchanged)
2. pre_tool hook(s) fire (in order, see §5)
3. invoke(task, context) runs               ← the actual tool body
4. Operation-level audit emit ─ tool_result (existing, unchanged)
5. post_tool hook(s) fire                   ← can mutate result
6. (path-specific) caller receives final result
```

If invoke raises:

```
3. invoke raises Exception
4. Operation-level audit emit ─ tool_result with outcome="error"  (existing)
5. on_error hook(s) fire                    ← observe-only
6. (path-specific) caller receives error string / re-raise
```

If pre_tool vetoes:

```
2. pre_tool returns HookVeto(reason="...")
   ↳ Operation-level audit emit ─ tool_vetoed (NEW event, §4)
   ↳ invoke is NOT called
   ↳ caller receives a deterministic veto string (§4)
```

`hook_fired` audits are emitted from inside `run_with_hooks()` (§7). They sit alongside but don't replace the operation-level `tool_call` / `tool_result` / `tool_vetoed` lines.

### 1.3 What's deferred — `pre_llm_call` / `post_llm_call`

**Decision: defer to a later step.** Reasons:

- Of the 5 paths, only 2 unambiguously make LLM calls (crew agent loop, voice WebSocket pipeline). MCP and chat-pre-LLM-hijack don't, and chat-post-LLM-tag is the *result* of an LLM call already being made by the dashboard. Adding the hook there would be a fifth surface that's not symmetric with the other four.
- The two natural use cases (token counting, cost ceiling enforcement) are real but each has a simpler short-term path: token counting via the existing audit envelope (`extra.task_len` + `extra.context_len`), cost via a wrapper inside `codec_llm_proxy`.
- Adding LLM hooks now without a clear story for "what does this hook receive in the chat-post-LLM-tag path?" risks the same drift this step is fighting.

Revisit when there's a concrete consumer (e.g. a billing/cost cap that has to gate every LLM call). Tracked in §11 Q3.

### 1.4 `HookCtx` shape

Frozen dataclass passed to every hook. Inherits `correlation_id` from the wrapping operation per Step 1 §1.4 (the wrapper receives it from the call site, never generates a new one).

```python
@dataclass(frozen=True)
class HookCtx:
    # ── Always populated ─────────────────────────────────────────────
    transport: str          # "crew" | "voice" | "stdio" | "http" | "chat" | "dispatch"
    correlation_id: str     # 12-char hex from the wrapping operation
    plugin_name: str        # this plugin's PLUGIN_NAME (or filename stem)
    timestamp_utc: str      # ISO8601 ms — pre_tool's perceived "now"

    # ── Tool-call hooks (pre_tool / post_tool / on_error) ────────────
    tool_name: Optional[str] = None
    task: Optional[str] = None
    context: Optional[str] = None
    agent: Optional[str] = None       # only set in crew path
    client_id: Optional[str] = None   # only set in MCP-HTTP path

    # ── Operation hooks (on_operation_start / on_operation_end) ──────
    operation_id: Optional[str] = None    # voice WebSocket session_id, crew run id, etc.
    duration_ms: Optional[float] = None   # only on_operation_end
    outcome: Optional[str] = None         # only on_operation_end ("ok"/"error")

    # ── Error hook (on_error only) ───────────────────────────────────
    # Exception is passed as a separate positional arg, NOT in ctx,
    # so the hook signature is on_error(ctx, exc). Reason: ctx is
    # frozen and shared across the chain; exc is the unique-per-call
    # thing the hook actually wants to see.
```

Plugins must treat `ctx` as read-only — it's a frozen dataclass and any field mutation raises `FrozenInstanceError`. To mutate `task`/`context`, return a `dict` (see §6).

---

## 2 · Hook registration

### 2.1 File layout

```
~/.codec/plugins/
├── cost_tracker.py
├── deny_list.py
└── self_improve.py        ← will live here in Step 4 (see §0)
```

One plugin per file. File name (minus `.py`) is the default `PLUGIN_NAME`. Files starting with `_` are skipped (template/fixture convention from skills).

### 2.2 Plugin file shape

Plugins use **top-level functions and constants** — same pattern as skills (`SKILL_NAME` constants + `def run`, see `codec_skill_registry.py:36-49`). No `HOOKS` dict — that would be a second discovery convention with no benefit.

```python
# ~/.codec/plugins/cost_tracker.py
"""Track per-skill cost into ~/.codec/plugin_state/cost_tracker.jsonl"""

# ── Metadata (all optional; only PLUGIN_NAME has a documented default) ──
PLUGIN_NAME = "cost_tracker"          # default: file stem
PLUGIN_DESCRIPTION = "Sidecar cost log per tool call"
PLUGIN_PRIORITY = 50                  # default: 100. Lower runs first. See §5.
PLUGIN_TOOL_FILTER = None             # optional: list of tool names; None=all

# ── Hooks (define any subset; missing ones simply don't fire) ───────────
def pre_tool(ctx):
    return None  # no mutation

def post_tool(ctx, result):
    # observe + return None to leave result unchanged
    return None

def on_error(ctx, exc):
    pass

def on_operation_start(ctx):
    pass

def on_operation_end(ctx):
    pass
```

### 2.3 Discovery — AST parse, lazy import

Mirror `codec_skill_registry.SkillRegistry`. Parse each `*.py` with `ast` at startup, extract:

1. Top-level constants: `PLUGIN_NAME`, `PLUGIN_DESCRIPTION`, `PLUGIN_PRIORITY`, `PLUGIN_TOOL_FILTER` (via `ast.literal_eval`).
2. Top-level `def` whose names match the lifecycle set: `pre_tool`, `post_tool`, `on_error`, `on_operation_start`, `on_operation_end`. Mark the plugin as offering those hooks.

A plugin file that defines none of the lifecycle functions is skipped (logged at WARN, not ERROR — same as a skill missing `def run`).

The **module is imported lazily on first hook fire**, exactly as `SkillRegistry.load()`. Reasoning identical to skills: a broken plugin should not break startup. Import errors degrade gracefully — the plugin is logged as "broken" and skipped on every fire.

### 2.4 Reload semantics

**PM2 restart required.** Hot reload is deferred — same trade-off as skills today (see AGENTS.md §4 *Adding a new skill*: "Restart `codec-dashboard` and the main `open-codec` process. Hot-reload is not currently supported.").

When the user adds `~/.codec/plugins/X.py`:

```bash
pm2 restart codec-dashboard open-codec codec-mcp-http codec-heartbeat codec-autopilot --update-env
```

Hot reload is tracked for a future step. Doing it correctly requires solving import-cache invalidation **and** in-flight tool-call coherence (a hot-reload during an active crew run could swap hooks mid-flight, with no audit trail of which version handled which call). Not urgent.

### 2.5 Where the registry lives

New module: **`codec_hooks.py`** at the repo root. Owns:

- `PluginRegistry` (mirrors `SkillRegistry`)
- `HookCtx` dataclass
- `HookVeto` sentinel
- `run_with_hooks(...)` — the wrapper from §3
- `emit_operation_start(...)` / `emit_operation_end(...)` — thin wrappers over the operation hooks
- A module-level `_registry: PluginRegistry` initialised from `~/.codec/plugins/`

No public API outside `codec_hooks.py` for plugin internals — call sites only see `run_with_hooks`, `emit_operation_start`, `emit_operation_end`, and `HookVeto`.

---

## 3 · Hook execution surface — `run_with_hooks`

### 3.1 Public signature

```python
# codec_hooks.py

class HookVeto:
    def __init__(self, reason: str, *, plugin_name: Optional[str] = None):
        self.reason = (reason or "")[:200]
        self.plugin_name = plugin_name

def run_with_hooks(
    *,
    tool_name: str,
    task: str,
    context: str = "",
    transport: str,                 # "crew" | "voice" | "stdio" | "http" | "chat" | "dispatch"
    agent: Optional[str] = None,
    client_id: Optional[str] = None,
    correlation_id: str,            # required — inherited from caller (Step 1 §1.4)
    invoke: Callable[[str, str], str],
) -> Union[str, HookVeto]:
    """
    Orchestrate pre/post/on_error hooks around invoke(task, context).

    Never raises in the hook layer. If invoke raises, on_error fires and
    the exception is re-raised so the call site's existing audit emit and
    error-formatting paths are unchanged.

    Returns the post-hook result string, or a HookVeto sentinel if any
    pre_tool returned one. The first veto wins; subsequent pre_tool hooks
    in the chain do not fire after a veto.

    `task` and `context` are passed through pre_tool's mutation chain
    (§6) before invoke runs. `result` is passed through post_tool's chain
    after invoke returns.
    """
```

### 3.2 Behaviour outline (semantics only — no code)

```
1. ctx0 = HookCtx(transport=..., correlation_id=..., tool_name=..., task=..., context=..., agent=..., client_id=...)
2. for each pre_tool hook in priority order:
       t0 = monotonic()
       try:
           ret = pre_tool_fn(ctx_with_plugin_name)
       except Exception as e:
           log.warning("plugin %s pre_tool raised: %s", plugin_name, e)
           emit hook_fired with outcome="error"
           continue   # buggy plugin doesn't break the call
       emit hook_fired with duration_ms=monotonic()-t0
       if ret is HookVeto:
           emit tool_vetoed with extra.veto_reason, extra.plugin_name
           return ret    # caller handles veto (§4)
       if isinstance(ret, dict):
           task = ret.get("task", task)
           context = ret.get("context", context)
           ctx = ctx with new task/context
3. try:
       result = invoke(task, context)   # the actual tool body
   except Exception as exc:
       for each on_error hook in priority order:
           emit hook_fired
       raise   # caller's existing audit + error-formatting takes over
4. for each post_tool hook in priority order:
       t0 = monotonic()
       try:
           new_result = post_tool_fn(ctx, result)
       except Exception as e:
           log.warning("plugin %s post_tool raised: %s", plugin_name, e)
           emit hook_fired with outcome="error"
           continue
       emit hook_fired with duration_ms=monotonic()-t0
       if new_result is not None:
           result = new_result
5. return result
```

### 3.3 Insertion points — exact lines

The Q3 v2 reviewer decision is **unified surface across all 5 paths, no drift**. The wiring is concentrated in **4 code locations**, which together cover all 6 of the user-listed "5 paths" (chat pre-LLM and chat post-LLM both go through `codec_dispatch.run_skill`, so a single edit there covers both):

#### Path 1 — Crew runtime

**File:** `codec_agents.py`
**Function:** `Agent.run`
**Current lines:** 510–523

```python
510  _audit("tool_call", agent=self.name, tool=tool_name,
511         input=tool_input[:200])
512  loop = asyncio.get_event_loop()
513  …
518  ctx = contextvars.copy_context()
519  result = await loop.run_in_executor(
520      None, ctx.run, tool.run, tool_input)
521  tool_calls_made += 1
522  _audit("tool_result", agent=self.name, tool=tool_name,
523         result_len=len(result))
```

**Insertion:** replace line 519–520's executor call with a wrapped version. The wrapper still runs in the executor (preserves asyncio + contextvar propagation from Step 1). Pseudocode delta:

```
before line 519:
   def _invoke(t, c): return tool.run(t)
after line 519:
   result = await loop.run_in_executor(None, ctx.run,
       lambda: run_with_hooks(
           tool_name=tool_name, task=tool_input, context="",
           transport="crew", agent=self.name,
           correlation_id=_correlation_id_var.get(),
           invoke=_invoke))
   if isinstance(result, HookVeto):
       result = f"Tool '{tool_name}' was vetoed by plugin '{result.plugin_name}': {result.reason}"
```

#### Path 2a — Voice WebSocket

**File:** `codec_voice.py`
**Function:** `VoicePipeline.dispatch_skill`
**Current lines:** 664–676

```python
664  async def dispatch_skill(self, skill: dict, user_text: str) -> Optional[str]:
665      try:
666          print(f"[Voice] → skill: {skill['name']}")
667          loop = asyncio.get_event_loop()
668          result = await loop.run_in_executor(None, skill["run"], user_text)
669          result = str(result).strip() if result else ""
```

**Insertion:** replace line 668. The cid is on `self._cid` (set by `VoicePipeline.run` per Step 1 (d)).

```
def _invoke(t, c): return skill["run"](t)
result = await loop.run_in_executor(None, contextvars.copy_context().run,
    lambda: run_with_hooks(
        tool_name=skill["name"], task=user_text, context="",
        transport="voice",
        correlation_id=self._cid,
        invoke=_invoke))
if isinstance(result, HookVeto):
    return f"Skill '{skill['name']}' was vetoed by plugin '{result.plugin_name}': {result.reason}"
```

#### Path 2b — Voice wake-word + chat pre-LLM hijack + chat post-LLM tag

**File:** `codec_dispatch.py`
**Function:** `run_skill`
**Current lines:** 47–91

```python
59  for skill_name in all_matches:
60      try:
61          result = registry.run(skill_name, task, app)
62          if result is None:
63              log.info("Skill '%s' returned None — trying next match", skill_name)
64              continue
```

**Insertion:** replace line 61 with a wrapped version. The cid `cid` is already generated at line 56 — pass it through.

```
def _invoke(t, c): return registry.run(skill_name, t, app)
result = run_with_hooks(
    tool_name=skill_name, task=task, context="",
    transport="dispatch", correlation_id=cid,
    invoke=_invoke)
if isinstance(result, HookVeto):
    log.info("Skill '%s' vetoed by plugin '%s': %s",
             skill_name, result.plugin_name, result.reason)
    return f"Skill '{skill_name}' was vetoed by plugin '{result.plugin_name}': {result.reason}"
```

This single edit covers paths 2b (voice wake-word from `codec.py:_dispatch_inner` → `run_skill`), 3 (chat pre-LLM hijack from `codec_dashboard.py:_try_skill` → `run_skill`), and 4 (chat post-LLM tag from `codec_dashboard.py:_try_skill_by_name` → `run_skill`). Verified by grepping every caller of `codec_dispatch.run_skill`.

#### Path 5 — MCP (stdio + HTTP)

**File:** `codec_mcp.py`
**Function:** `tool_fn` (closure inside `_load_skill_tools_into`)
**Current lines:** 180–238

```python
180  def tool_fn(task: str, context: str = "") -> str:
181      """Execute this CODEC skill with the given task"""
182      t0 = time.time()
183      …
185      cid = _new_correlation_id()
186
187      err = _validate_mcp_input(sname, task, context)
188      if err is not None:
189          _audit(sname, event="validation", …)
190          return err
191      …
207      with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
208          fut = ex.submit(_run)
209          try:
210              result, errmsg = fut.result(timeout=SKILL_TIMEOUT_SEC)
```

**Insertion:** wrap the validation-pass-through-result flow. Validation runs first (so plugins see clean inputs); then `run_with_hooks` wraps the threadpool/timeout block.

```
After line 190 (validation passed):
   def _invoke(t, c):
       def _run():
           mod = registry.load(rkey)
           if mod is None or not hasattr(mod, "run"):
               raise SkillLoadError("load_failed")
           return mod.run(t, c)
       with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
           fut = ex.submit(_run)
           return fut.result(timeout=SKILL_TIMEOUT_SEC)

   try:
       result = run_with_hooks(
           tool_name=sname, task=task, context=context,
           transport=os.environ.get("CODEC_MCP_TRANSPORT", "stdio"),
           correlation_id=cid,
           invoke=_invoke)
   except concurrent.futures.TimeoutError:
       _audit(sname, event="timeout", …)   # existing emit
       return f"Skill '{sname}' timed out after {SKILL_TIMEOUT_SEC}s."
   if isinstance(result, HookVeto):
       _audit(sname, event="tool_vetoed",
              outcome="denied", correlation_id=cid,
              extra={"veto_reason": result.reason,
                     "plugin_name": result.plugin_name})
       return f"Skill '{sname}' was vetoed by plugin '{result.plugin_name}': {result.reason}"
```

**No drift.** The wrapper signature is identical at every call site; only the `transport` and the `invoke` closure differ.

---

## 4 · Veto semantics

### 4.1 What a veto looks like to the plugin author

```python
# ~/.codec/plugins/deny_list.py
from codec_hooks import HookVeto

_DENY = {"shell_execute", "python_exec"}

def pre_tool(ctx):
    if ctx.tool_name in _DENY:
        return HookVeto(reason=f"{ctx.tool_name} disabled by deny_list plugin")
    return None
```

`HookVeto` is a sentinel — not an exception. Plugins don't `raise` it; they `return` it. Reason: the plugin author shouldn't have to reason about exception propagation through 5 different call sites. Simple value-returns make audit/log/branching trivially predictable.

### 4.2 What the caller sees

For every path, the wrapper returns a single deterministic veto string:

```
"Tool '<tool_name>' was vetoed by plugin '<plugin_name>': <reason>"
```

This string flows back through the path's existing return channel — no new error type to handle, no special UI work. The audit log carries the structured details (tool_vetoed event with `extra.veto_reason`, `extra.plugin_name`).

### 4.3 New audit event — `tool_vetoed`

Added to the §1.2 enumeration in Step 1 (the design doc treated it as an open extension point; this is the first occupant). Schema:

```jsonc
{
  "ts":          "...",
  "schema":      1,
  "event":       "tool_vetoed",
  "source":      "codec-hooks",
  "outcome":     "denied",
  "tool":        "shell_execute",
  "transport":   "crew|voice|stdio|http|chat|dispatch",
  "level":       "warning",
  "duration_ms": 0.4,                    // pre_tool exec time, not invoke time
  "extra": {
    "correlation_id": "a3f7b2c8e409",
    "veto_reason":    "shell_execute disabled by deny_list plugin",
    "plugin_name":    "deny_list",
    "task_preview":   "..."              // truncated to _PREVIEW_MAX
  }
}
```

Emitted from inside `run_with_hooks` at the moment of veto, BEFORE `run_with_hooks` returns. The path's own `tool_call` emit has already fired by the time pre_tool runs (because `tool_call` lives in path-level code), so the audit log shows: `tool_call` → `hook_fired` (the vetoing pre_tool) → `tool_vetoed`. `tool_result` is **never emitted** for vetoed calls — the `outcome="denied"` field on `tool_vetoed` carries that.

### 4.4 Crew behaviour on veto

**Decision: SKIP, not retry, not fail-the-crew.** The agent receives the veto string as the tool result and decides on its next move via the existing ReAct loop:

```
Agent sees:
  Tool result from shell_execute:
  Tool 'shell_execute' was vetoed by plugin 'deny_list': shell_execute disabled by deny_list plugin

  Continue. Use another TOOL or respond with FINAL: (4 tool calls remaining).
```

Justification:
- **No retry**: retrying the exact same call would just hit the same veto. Wasteful, and the plugin's intent was "don't run this," not "run it later."
- **Not fail-the-crew**: a veto is a deliberate user/plugin decision, not an error. Failing the entire crew because one tool was denied gives the agent zero chance to find an alternate path. Users who *want* veto-fails-crew can write a post_tool hook that re-raises on veto (or set the agent's max_tool_calls to 1).
- **Skip with veto-as-result**: lets the agent reason about the denial. If `shell_execute` is denied, the agent might switch to `file_read` or give a `FINAL:` answer that explains it can't run that command. This is the same shape the agent already handles for ordinary tool errors (`return f"{type(e).__name__}: {str(e)[:200]}"` at line 162 of codec_agents.py).

The crew's outer `crew_complete` event fires normally; the inner `tool_vetoed` is one entry among the crew's emit chain, all sharing the crew's correlation_id.

### 4.5 Voice / chat / MCP behaviour on veto

Same shape: the veto string is returned to the user as the skill's "result," prefixed with `**⚡ <skill>**:` in chat or spoken aloud in voice. No special UI. The user sees that the skill was blocked and by which plugin.

For MCP: the client (claude.ai or Claude Desktop) sees a tool-result string starting with `"Tool 'X' was vetoed by plugin 'Y': ..."`. From their perspective it looks like the skill returned that text — which is exactly the truth.

---

## 5 · Hook ordering

### 5.1 Order per hook type

For each hook lifecycle (pre_tool, post_tool, etc.) the registry produces an ordered list of (plugin, fn) pairs. Within that list:

1. **Primary sort:** `PLUGIN_PRIORITY` ascending. Default = `100`. Lower runs first. (Range convention: 0–199 reserved for system plugins, 200+ for user plugins, but no enforcement — just a convention.)
2. **Tie-break:** filename alphabetical ascending. Same convention as `SkillRegistry.scan()` which uses `sorted(os.listdir(self.skills_dir))` at line 86.

Order is computed once at `_registry.scan()` time, cached. Adding a plugin requires PM2 restart (per §2.4).

### 5.2 Conflict resolution when two plugins mutate the same field

**Chained transforms.** Plugin A's output is plugin B's input.

```
ctx0 = (task="weather Paris", context="")
plugin A pre_tool:  task = task.lower()              → ctx1 = (task="weather paris", …)
plugin B pre_tool:  task = "[gated] " + task         → ctx2 = (task="[gated] weather paris", …)
invoke runs with task="[gated] weather paris"
```

This means **ordering matters for mutating plugins**. The doc on plugin authoring (to be written in Step 2 implementation) will state this explicitly. Two plugins that both want to be the canonical pre-processor must coordinate their priority numbers — the registry won't auto-resolve.

For `post_tool`: identical chain.
For `on_error` / `on_operation_start` / `on_operation_end`: order matters for human-visible side effects (logs, sidecar files) but no return-value mutation, so the chain is just sequential observation.

### 5.3 What if two plugins veto?

The **first veto wins** (in §5.1 order). Subsequent pre_tool hooks for that call are not invoked. Reasoning: a veto is final; running more hooks after a veto only wastes time and clutters the audit log.

Logged as `hook_fired` with `extra.vetoed=true` for the vetoing plugin, and `tool_vetoed` for the operation as a whole. The plugins downstream simply don't appear in the chain.

---

## 6 · Mutation contract

I recommend the user's proposal verbatim, with one tightening:

| Hook | Return shape | Effect |
|---|---|---|
| `pre_tool` | `None` | unchanged |
| | `{"task": str, "context": str}` (either or both keys) | replaces `task` and/or `context` for invoke + downstream pre_tool hooks |
| | `HookVeto(reason=...)` | aborts invoke; subsequent pre_tool hooks skipped |
| | anything else | logged as warning, treated as `None`; protects against typos like `return ctx` (returning a frozen dataclass instead of a dict) |
| `post_tool` | `None` | unchanged |
| | `str` | replaces `result` for downstream post_tool hooks + caller |
| | anything else | logged as warning, treated as `None` |
| `on_error` | always ignored | observe-only |
| `on_operation_start` | always ignored | observe-only |
| `on_operation_end` | always ignored | observe-only |

**Immutable identity fields (per Q2 tightening):** `pre_tool` may return a dict to replace `task` and/or `context`, **but no other ctx field is mutable**. The wrapper ignores any of the following keys if a plugin tries to set them in the returned dict, and logs a warning:

| Field | Why immutable |
|---|---|
| `tool_name` | Routing identity. A plugin that wants different routing must veto and let the user retry through a different entry point — not silently rewrite the tool mid-flight. Silent rewrite would break audit pairing (the operation-level `tool_call` already emitted with the old name). |
| `transport` | Operation identity. Path-determined; not plugin business. |
| `agent` | Crew operation identity. Set by the crew runtime, not by plugins. |
| `correlation_id` | Step 1 §1.4 contract. Once generated at the operation entry point, threaded verbatim through every audit emit; mutating it would break the pairing of paired emits inside one operation. |
| `client_id` | OAuth / MCP-HTTP identity. Set by the transport layer. |
| `operation_id` | Same envelope identity as the wrapping op (voice WebSocket session, crew run, etc.). |

If the dict contains any of these keys, the wrapper drops them, logs `[hooks] plugin X tried to mutate immutable field 'tool_name'; ignored`, and continues with whatever mutable keys remain. Catches plugin authors who try to "rewrite a chat call as a crew call" — refused at the boundary.

**Tightening over the user's proposal:** explicit "anything else → log warning + None." Without this, a plugin author who returns `result + ""` from `post_tool` thinks they're idempotent, but if they accidentally return `False` or a list (because they forgot the `return result` and Python returned `None` from a single-return-path branch), the wrapper would either crash or misbehave. The explicit warning + None catches typos at runtime with a clear log message: `[hooks] plugin X post_tool returned <list>; ignored`.

**Rationale for the asymmetry (mutate ok on tool, observe-only on error/operation):**

- `on_error`: by the time on_error fires, the operation already failed. A plugin "fixing" the error by returning a string would silently mask bugs. If a plugin wants retry-on-error, that's not a hook, it's a different feature (maybe Step 6).
- `on_operation_start` / `on_operation_end`: an operation is an envelope, not a value. There's no result to mutate. A plugin that wanted to "block the operation" can do so via pre_tool on the first tool call inside the operation — same effect, simpler model.

**No async hooks.** Sync-only. Reasoning identical to the user's: the skill registry is sync, the `invoke` callable is sync (it might run in a threadpool, but from the wrapper's perspective it's a sync call), and adding async would require either an async wrapper variant or thread-juggling inside every plugin. The simplicity of "you have <1ms of total budget, do your work, return" beats async ergonomics at this scale.

---

## 7 · Audit integration

### 7.1 `hook_fired` event

Every successful hook invocation (including a deliberate `HookVeto` return — that's a normal outcome, not a failure) emits one audit line:

```jsonc
{
  "ts": "...",
  "schema": 1,
  "event": "hook_fired",
  "source": "codec-hooks",
  "outcome": "ok",
  "transport": "crew|voice|stdio|http|chat|dispatch",
  "level": "info",
  "duration_ms": 0.21,
  "extra": {
    "correlation_id": "a3f7b2c8e409",   // inherited from operation
    "plugin_name": "cost_tracker",
    "hook_name": "pre_tool",
    "tool_name": "weather",              // null for operation hooks
    "mutated": false,                    // true if return value mutated state
    "vetoed": false                      // true if pre_tool returned HookVeto
  }
}
```

A plugin returning `HookVeto` is `outcome="ok"` with `vetoed=true`. (The veto on the operation level is `tool_vetoed`, separate event in §4.) When the hook itself raises, the failure is captured by the new `hook_error` event in §7.5 — not by setting `outcome="error"` on `hook_fired`. Splitting the two events keeps `hook_fired` cheap and uniformly shaped on the hot path.

### 7.2 Where the emit lives

Inside `run_with_hooks`, in two places:

1. **For tool-call hooks**: a small helper `_fire_one(plugin, fn, *args)` wraps the call: monotonic timer, try/except, audit emit. Called from the pre_tool / post_tool / on_error loops. On success → `hook_fired`. On plugin exception → `hook_error` (§7.5) and `_fire_one` returns `None` so the chain continues.

2. **For operation hooks**: `emit_operation_start(...)` and `emit_operation_end(...)` each iterate the relevant hook chain and call `_fire_one`.

The emit goes through `codec_audit.log_event(...)` (the Step 1 adapter) — never reinvent. `correlation_id` is passed through verbatim from the wrapping operation; never regenerated in the hook layer.

### 7.3 correlation_id inheritance — exact contract

Per Step 1 §1.4: the wrapping operation generates a `correlation_id` once at its entry point, threads it as a kwarg through every audit emit. `run_with_hooks` receives it as a required kwarg and propagates it into:

- Each `hook_fired` emit (under `extra.correlation_id`)
- Each `hook_error` emit (under `extra.correlation_id`) — §7.5
- The `tool_vetoed` emit on veto (under `extra.correlation_id`)

Plugins read it from `ctx.correlation_id` if they want to correlate their own sidecar writes (e.g. `cost_tracker.py` writing one JSONL line per tool call, keyed by cid).

### 7.4 Performance budget for the audit emit

From Step 1 perf tests: `audit()` is ~0.1–0.5 ms/call single-thread, ~1–2.5 ms under 10-way contention. The `hook_fired` emit fits inside the §9 perf budget for hook overhead.

### 7.5 `hook_error` event (Q4 tightening)

When a plugin's hook function itself raises (Q4 resolution: log + skip; the user's tool call must not break because of a buggy plugin), `_fire_one` catches the exception and emits a separate audit event:

```jsonc
{
  "ts": "...",
  "schema": 1,
  "event": "hook_error",
  "source": "codec-hooks",
  "outcome": "error",
  "level": "warning",            // not "error" — the OPERATION still succeeds
  "transport": "crew|voice|stdio|http|chat|dispatch",
  "duration_ms": 0.18,           // time spent before the hook raised
  "tool": "weather",             // null for operation hooks
  "error_type": "KeyError",
  "error": "missing key 'x'",    // truncated to _PREVIEW_MAX (200 chars)
  "extra": {
    "correlation_id": "a3f7b2c8e409",   // inherited from operation
    "plugin_name": "cost_tracker",
    "hook_name": "pre_tool"
  }
}
```

**Required fields:**
- `event: "hook_error"`
- `source: "codec-hooks"`
- `outcome: "error"`
- `level: "warning"` (not `"error"` — see below)
- `extra.plugin_name` (the plugin whose hook failed)
- `extra.hook_name` (which lifecycle the failure happened in)
- `error_type` (top-level, per Step 1 envelope)
- `error` (top-level, truncated to `_PREVIEW_MAX` from `codec_audit.py`)
- `extra.correlation_id` (inherited verbatim from the wrapping operation)

**Why `level: "warning"`, not `"error"`:** the OPERATION still succeeded — the user's tool call ran and returned a result; only one plugin's hook failed and was skipped. `error` level is reserved for failures that broke the operation (`tool_result outcome=error`, `service_down`, `chat_llm_error`). A buggy plugin is operationally a warning, not an error. This keeps `audit_report`'s error-rate metric meaningful — a noisy plugin doesn't inflate the error count and mask real problems.

**Storage cost:** at perf budget (§9: < 5 ms/call with 5 hooks), the steady-state `hook_error` rate is zero — these only fire when a plugin is actually broken. A misbehaving plugin emits one `hook_error` per tool call until the user removes it; not a hot path.

`hook_error` is added to the §1.2 enumeration in Step 1's design as the third hook-layer event (alongside `hook_fired` and `tool_vetoed`).

---

## 8 · Trust model

**Same as skills.** Hooks are local Python files written or vetted by the user, dropped into `~/.codec/plugins/`. No marketplace, no auto-install, no isolation between plugins, no permissions system, no signature verification. The threat model is "the user is the operator and the only principal that can touch `~/.codec/`."

Justification:

1. **Single-user system.** CODEC is a personal AI on the user's Mac. A multi-tenant marketplace makes sense for VS Code; it doesn't here. The user already curates what runs on their machine.
2. **Skills already have this trust model.** A `~/.codec/skills/X.py` file can do anything a Python process running as the user can do — open files, hit network, run subprocesses. Adding plugin sandboxing while skills run unsandboxed would be theatre.
3. **Sandboxing has real cost.** A subprocess-per-plugin model (à la `codec_sandbox.run_skill_sandboxed` for skills) doubles the per-call overhead from <1 ms to >10 ms — and breaks the §9 performance budget. Process-pool models lose the contextvar-based correlation_id propagation we just got working in Step 1.
4. **`codec_self_improve` will live here in Step 4.** That plugin reads audit lines and writes proposals. If we sandboxed plugins, self-improve couldn't observe much. Trusting the plugin model and trusting self-improve are the same decision.

A future contributor who wants to add a marketplace or plugin sandbox should propose it as its own design — and confront these trade-offs head-on. **Do not add it to this step.**

The `~/.codec/skill_proposals/<date>/<name>.py` workflow (human-in-the-loop review before promotion) is the correct shape for "code that wasn't written by the user." If a plugin source ever gets remote (e.g. shared via Git), it goes through the same proposal-stage gate before landing in `~/.codec/plugins/`. **This is a workflow boundary, not a code boundary.**

---

## 9 · Test plan

Five new test files, ~480 LOC tests, conservative.

### 9.1 `tests/test_hooks_discovery.py` (~80 LOC)

```python
# Plugin file shape
def test_plugin_with_metadata_constants_only_loads():
    # File with PLUGIN_NAME + PLUGIN_PRIORITY but no hook fns → not registered
    ...
def test_plugin_with_pre_tool_only_loads():
    # File with just `def pre_tool(ctx): pass` → registered for pre_tool only
    ...
def test_plugin_with_all_five_hooks_loads():
    ...
def test_plugin_with_syntax_error_skipped_with_warning():
    # AST-parse error → warning logged, no exception, no registration
    ...
def test_plugin_starting_with_underscore_skipped():
    # ~/.codec/plugins/_template.py → skipped, mirrors skill convention
    ...
def test_plugin_default_name_is_filename_stem():
    # No PLUGIN_NAME constant → name = filename without ".py"
    ...
def test_plugin_default_priority_is_100():
    ...
def test_module_import_is_lazy():
    # AST parse at scan; module import only on first hook fire
    ...
```

### 9.2 `tests/test_hooks_lifecycle.py` (~120 LOC)

```python
# pre/post/on_error firing order, separately and together

def test_pre_tool_fires_before_invoke():
def test_post_tool_fires_after_invoke():
def test_post_tool_sees_invoke_result():
def test_on_error_fires_when_invoke_raises():
def test_on_error_does_not_fire_on_success():
def test_on_operation_start_fires_at_voice_session_start():
def test_on_operation_end_fires_at_voice_session_end():
def test_on_operation_end_fires_with_outcome_error_when_pipeline_raises():
def test_operation_start_does_not_fire_for_individual_mcp_calls():
    # MCP tool calls aren't an operation envelope; sanity check
def test_pre_tool_after_validation_in_mcp():
    # Validation runs first, then pre_tool sees clean inputs
```

### 9.3 `tests/test_hooks_veto.py` (~80 LOC)

```python
def test_pre_tool_returning_hookveto_skips_invoke():
def test_pre_tool_veto_short_circuits_remaining_pre_hooks():
def test_pre_tool_veto_emits_tool_vetoed_audit():
def test_pre_tool_veto_returns_deterministic_string_to_caller():
def test_pre_tool_veto_in_crew_passes_string_to_agent_react_loop():
def test_pre_tool_veto_in_mcp_returns_string_to_client():
def test_first_veto_wins_in_priority_chain():
def test_post_tool_cannot_veto():
    # post_tool's HookVeto return is logged as a warning + ignored
def test_on_error_cannot_recover_via_return():
    # on_error returning a string is ignored; original exception still surfaces
```

### 9.4 `tests/test_hooks_mutation_and_ordering.py` (~100 LOC)

```python
def test_pre_tool_mutates_task():
def test_pre_tool_mutates_context():
def test_pre_tool_no_mutation_when_returning_none():
def test_post_tool_mutates_result():
def test_two_pre_tool_chain_in_priority_order():
    # pluginA priority=10 mutates first, pluginB priority=20 sees pluginA's output
def test_priority_tie_broken_alphabetically():
def test_default_priority_100():
def test_invalid_pre_tool_return_logs_warning_treats_as_none():
    # Plugin returns False / [] / a frozen ctx → warning + treated as None
def test_invalid_post_tool_return_logs_warning_treats_as_none():
def test_plugin_tool_filter_skips_unmatched_tools():
    # PLUGIN_TOOL_FILTER = ["weather"] only fires for weather, skipped for notes
def test_pre_tool_immutable_field_tool_name_dropped():
    # Q2 tightening: pre_tool returning {"tool_name": "X", "task": "Y"}
    # keeps the new task but drops tool_name with a warning log line.
def test_pre_tool_immutable_fields_transport_agent_correlation_dropped():
    # transport / agent / correlation_id / client_id / operation_id
    # are also dropped if a plugin tries to mutate them.
```

### 9.5 `tests/test_hooks_audit_and_perf.py` (~100 LOC)

```python
def test_hook_fired_audit_emitted_per_call():
def test_hook_fired_carries_plugin_name_hook_name_duration_ms():
def test_hook_fired_inherits_correlation_id():
def test_hook_error_emitted_when_plugin_raises():
    # Q4 tightening: plugin raises in pre_tool → emit hook_error with
    # event="hook_error", outcome="error", level="warning", error_type,
    # error (truncated to _PREVIEW_MAX), extra.plugin_name,
    # extra.hook_name, extra.correlation_id. Operation continues, invoke
    # still runs.
def test_hook_error_does_NOT_emit_hook_fired_for_same_call():
    # Buggy plugin in pre_tool emits hook_error only — not both.
def test_hook_error_truncates_long_error_message_at_preview_max():
    # error string > 200 chars → truncated to 200 in audit envelope.
def test_hook_error_inherits_correlation_id():
def test_hook_error_level_is_warning_not_error():
    # Operation succeeds; only the plugin failed. Don't inflate error rate.
def test_hook_overhead_under_1ms_with_zero_hooks():
    # 1000 invocations: avg run_with_hooks overhead < 1.0 ms with no plugins
def test_hook_overhead_under_5ms_with_5_hooks():
    # 5 trivial hooks (pre, post, error, operation_start, operation_end),
    # each just `pass`. 1000 invocations: avg overhead < 5.0 ms.
def test_hook_concurrent_no_audit_corruption():
    # 10 threads × 100 invocations × 5 hooks → no JSON corruption in audit.log,
    # all hook_fired / hook_error entries parseable, no dropped writes
    # (mirrors Step 1 §4.4).
```

CI multiplier identical to Step 1: under `CI=1` the budgets are 5× looser to avoid flakes on slow runners; the production guard is the post-deploy 24h sample (§10).

---

## 10 · Rollback plan

### 10.1 No new schema field

Hooks aren't a schema — they're behavior. The audit envelope (schema:1) gains two new event types (`hook_fired`, `tool_vetoed`); both are additive and don't bump the schema version. Old analyzer code with `.get()` access (§3.1 Step 1) ignores them cleanly.

### 10.2 Git revert as primary

Identical mechanics to Step 1 §5.4:

```bash
git -C ~/codec-repo revert <merge-commit> --no-edit
git -C ~/codec-repo push origin main
pm2 restart codec-dashboard open-codec codec-mcp-http codec-heartbeat codec-autopilot --update-env
```

`~/.codec/audit.log` continues working — `hook_fired` entries from before the revert remain valid records; the analyzer just doesn't surface them post-revert.

### 10.3 What "broken in production" looks like

| symptom | cause | response |
|---|---|---|
| MCP p95 > 2× Step 1 baseline | Hook overhead exceeds budget | Hard revert. The hook layer is on the hot path. |
| `hook_fired` audit entries flood the log (>10× normal volume) | A plugin mis-registered or in a hot loop | Identify via `extra.plugin_name`, remove the plugin file, restart. No revert needed. |
| Skill calls return `"Tool X was vetoed by plugin Y: ..."` unexpectedly | Plugin veto fires wrong | Identify via `tool_vetoed.extra.plugin_name`, fix or remove the plugin. No revert needed. |
| A plugin's exception breaks every skill call | `_fire_one` exception handling broken | Hard revert — this is the wrapper itself failing, not user-plugin. |
| `correlation_id` missing on `hook_fired` entries | Wrapper not threading cid through | Wiring bug, fix forward (don't revert — schema:1 is unaffected). |
| Audit log corruption under live load | Concurrent emit lost the lock | Hard revert and run `tests/test_hooks_audit_and_perf.py::test_hook_concurrent_no_audit_corruption` against the reverted code to triage. |

### 10.4 Post-deploy 24h sampling — same shape as Step 1

Reuse the Step 1 baseline numbers (`docs/PHASE1-STEP1-BASELINE.md`: avg=987.96 ms, p95=1907.78 ms). After Step 2 merges:

- T+0, T+4h, T+8h, T+12h, T+16h, T+20h samples to `docs/PHASE1-STEP2-POSTMERGE-SAMPLES.md`.
- Same hard-revert thresholds: p95 > 3815.56 ms or avg > 1975.92 ms at any sample → revert.
- Soft-investigate threshold: 1.3× baseline.

The Step 2 sample tracker is a separate file (`PHASE1-STEP2-POSTMERGE-SAMPLES.md`) so the revert decision and the sign-off are unambiguous per step.

The `scripts/capture_audit_sample.py` script from Step 1 will be reused — it reads `~/.codec/audit.log` and computes the trailing-30m window. No code change needed; it just needs a label switch to `"T+Nh-step2"`.

---

## 11 · Reviewer resolutions (closed)

**Status: RESOLVED.** All six questions decided by the Phase 1 reviewer (Mickael + Claude chat) on 2026-04-30. Three of the six approvals carried tightenings — all three are baked into the body of the doc above (§1, §6, §7, §9, §12, §13).

| # | Question | Resolution |
|---|---|---|
| **Q1** | Should `PLUGIN_TOOL_FILTER` be a list of exact tool names only, or also support glob/regex (e.g. `["weather", "google_*"]`)? | **APPROVED** as recommended. Exact list only for v1. Globs are a small feature with a non-trivial maintenance surface (priority interactions when one plugin matches `*` and another matches `weather`). Defer globs to a later step if a real use case appears. |
| **Q2** | Should `pre_tool` be allowed to mutate `transport`, `agent`, or `correlation_id`? | **APPROVED with tightening.** No, those are operation-identity fields. **Tightening:** lock `tool_name` itself in the immutable-identity-fields list alongside `transport` / `agent` / `correlation_id` / `client_id` / `operation_id`. A plugin that wants different routing must **veto** and let the user retry through a different entry point — silently rewriting `tool_name` mid-flight would break audit pairing (the operation-level `tool_call` already emitted with the old name). The full immutable-fields table is in §6. |
| **Q3** | Should `pre_llm_call` / `post_llm_call` ship in this step or wait? | **APPROVED** as recommended. Defer until there's a concrete consumer (cost cap, prompt-injection scrub) and a clean answer for what the hook receives in the chat-post-LLM-tag path. Documented in §1.3. |
| **Q4** | Should hook errors (a plugin raises in `pre_tool`/`post_tool`) be (a) logged + skipped, (b) abort the operation, or (c) logged + treated as veto? | **APPROVED with tightening.** (a) logged + skipped is the only correct option — a buggy plugin must not break a working skill. **Tightening:** specify the audit envelope for plugin-raised errors. New event type **`hook_error`** with required fields `plugin_name`, `hook_name`, `error_type`, `error` (truncated to `_PREVIEW_MAX` = 200 chars), inheriting `correlation_id` from the wrapping operation. `level: "warning"` (not `"error"` — the operation still succeeded; only the plugin failed). `hook_fired` and `hook_error` are split events, never both for the same call. Full spec in §7.5; row added to §1.1's audit-event column for every hook lifecycle. |
| **Q5** | When a `post_tool` hook chain produces a result larger than the path's expected size, do we cap it? | **APPROVED** as recommended. No cap in the hook layer. Each path already has its own size handling (MCP truncates at the protocol layer; voice has `_skill_to_speech` summarizing at codec_voice.py:678; chat has its own SSE streaming). A plugin that wants to *expand* a result (e.g. adding citations to a search result) is legitimate. |
| **Q6** | Should `on_session_start` for crew receive the per-agent breakdown, or just the crew name? Default fire-once or fire-per-agent? | **APPROVED with rename.** Default: fire once per crew (not per-agent). **Rename:** `on_session_start` / `on_session_end` → `on_operation_start` / `on_operation_end`. Reasoning: "operation" matches the correlation_id contract vocabulary from Step 1 §1.4 ("required for any operation emitting ≥2 audit lines"). Avoids collision with the existing `voice_session_start` / `voice_session_end` audit events from Step 1 (those keep their names — they refer to literal voice sessions, a narrower scope). The HookCtx field `session_id` is also renamed to `operation_id` for vocabulary consistency. Mass-edit touched ~21 lines across §1, §2, §5, §6, §7, §9, §12, §13. |

The Step 1 design used 5 open questions (all resolved before implementation). This step had 6 — slightly more because it sits on top of an existing schema and the integration surface is larger. All six are now resolved; no further reviewer input needed before implementation.

---

## 12 · Summary — what gets shipped at implementation time

When Phase 1 Step 2 implementation runs, the diff will land:

| File | Δ | What |
|---|---|---|
| `codec_hooks.py` (new) | ~+265 LOC | `PluginRegistry`, `HookCtx` (incl. `operation_id` field), `HookVeto`, `run_with_hooks`, `emit_operation_start`, `emit_operation_end`, `_fire_one` audit helper (emits `hook_fired` on success, `hook_error` on plugin exception per §7.5), immutable-identity-fields filter on `pre_tool` returns per §6 Q2 tightening, AST discovery |
| `codec_agents.py` | ~-3 / +12 | Wrap `loop.run_in_executor(None, ctx.run, tool.run, tool_input)` at line 519 with `run_with_hooks`. Handle `HookVeto` return. |
| `codec_voice.py` | ~-1 / +12 | Wrap `loop.run_in_executor(None, skill["run"], user_text)` at line 668. Handle veto. Wire `on_operation_start` / `on_operation_end` calls into `VoicePipeline.run` start/finally. |
| `codec_dispatch.py` | ~-1 / +12 | Wrap `registry.run(skill_name, task, app)` at line 61 with `run_with_hooks`. Handle veto. (Single edit covers paths 2b/3/4.) |
| `codec_mcp.py` | ~-30 / +50 | Refactor `tool_fn` body — validation pass-through stays where it is; the threadpool/timeout/result block becomes the `invoke` closure passed to `run_with_hooks`. Handle veto via existing `_audit` style. Add `tool_vetoed` event emit. |
| `codec_audit.py` | 0 LOC | No changes. `log_event("hook_fired", ...)`, `log_event("hook_error", ...)`, and `log_event("tool_vetoed", ...)` are routine usages of the existing adapter. |
| `codec_self_improve.py` | 0 LOC this step | Migration to a hook is **Step 4** scope. This step ships the plumbing, not the migrant. |
| `AGENTS.md` §3 + new §12 | small update | Status note: "PreToolUse / PostToolUse hooks: implemented in Phase 1 Step 2 (commit `<hash>`)." Plus a new §12 "Plugin authoring guide" pointer. |
| `tests/test_hooks_discovery.py` (new) | ~80 LOC | §9.1 |
| `tests/test_hooks_lifecycle.py` (new) | ~120 LOC | §9.2 |
| `tests/test_hooks_veto.py` (new) | ~80 LOC | §9.3 |
| `tests/test_hooks_mutation_and_ordering.py` (new) | ~100 LOC | §9.4 |
| `tests/test_hooks_audit_and_perf.py` (new) | ~100 LOC | §9.5 |
| `~/.codec/plugins/_template.py` (new, optional) | ~40 LOC | Template plugin file with all 5 hooks stubbed. Mirrors `skills/_template.py`. Lives in user state, not in the repo. |
| `docs/PHASE1-STEP2-POSTMERGE-SAMPLES.md` (new) | small | Reserved for post-merge 24h sampling (§10.4). |

**Net code change:** ~+345 functional LOC (most concentrated in the new `codec_hooks.py` — slightly higher than the v1 estimate due to the §7.5 `hook_error` emit + the §6 Q2 immutable-fields filter), ~+500 LOC tests (Q4 added 5 new tests for `hook_error` envelope + level + correlation_id inheritance + truncation, Q2 added 2 tests for the immutable-fields filter), **zero breaking changes** to schema:1 or to skill/crew/voice/MCP/chat behaviour for users who don't drop a plugin file. New audit events (`hook_fired`, `hook_error`, `tool_vetoed`) are additive and the existing analyzer tolerates them.

---

## 13 · Migration story for `codec_self_improve.py` (preview)

This isn't shipped in Step 2, but the design decisions here were guided by it. To make sure the seam supports the migration cleanly:

`codec_self_improve.py` today (post-Step 1) reads `audit.log.<yesterday>`, finds gaps, drafts proposals. It's a polling loop run by autopilot at a nightly time.

In **Step 4**, it becomes a plugin at `~/.codec/plugins/self_improve.py` that:

- Implements `on_operation_end(ctx)` — every voice/crew/chat operation end, increment a counter for the operation's tool-call gap signals.
- Implements a daily flush via `on_operation_end` checking the day boundary; when the boundary crosses, run the existing proposal-drafting logic synchronously (fast-fail if Qwen is unavailable; not a hot path).
- Reads from a sidecar file at `~/.codec/plugin_state/self_improve.jsonl` instead of audit.log directly. The sidecar is written by `on_operation_end` itself — eliminates the audit-log polling.

Verification that Step 2's design supports this:

- ✅ `on_operation_end` ctx carries `correlation_id` and `transport`, enough to attribute signals to specific operations.
- ✅ `pre_tool` ctx carries `tool_name` — enough for the unknown-tool / failing-tool / timeout-tool gap kinds.
- ✅ Audit-log polling stays *available* (the proposal logic can fall back to it if the plugin's sidecar gets corrupted).
- ✅ The plugin's daily flush doesn't need async — Qwen calls are sync inside the LLM proxy.

If any of these assumptions break in implementation, this section will be revised before Step 4 starts.

---

**End of design (v2 — §11 RESOLVED).** No code modified. No other docs written. Stops here.
