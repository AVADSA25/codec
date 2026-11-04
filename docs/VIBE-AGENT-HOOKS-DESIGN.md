# Vibe agent: plugin hooks around its tool calls (P-067)

Date 2026-11-03. Owner's go for the 25 Oct audit plan: "go go build i trust ur judgment here" (3 Nov 2026), which
covers the design-first gate for this item.

## Why

Every other path that runs a tool (crew, voice, wake word and chat through `codec_dispatch.run_skill`, MCP) goes
through `codec_hooks.run_with_hooks`. The Vibe coding agent does not import `codec_hooks`, so its tool calls are
invisible to plugins: no veto, no observer, no start and end of a run.

## What

A small adapter, `codec_vibe_agent_hooks.py` (no state of its own), and five call sites in `codec_vibe_agent.py`.
`codec_hooks.py`, the audit envelope and `~/.codec/plugins.allowlist` are not changed, and no audit event is added.

| | Vibe value |
|---|---|
| Tool names | `vibe:<tool>` for the eight tools that do work: `list_files`, `read_file`, `read_output`, `search`, `edit_file`, `write_file`, `delete_file`, `run_check`. `PLUGIN_TOOL_FILTER` takes these exact names. `ask` and `finish` talk to the owner or end the run and are not hooked; neither is a reply the agent could not read. |
| `transport` | `vibe` |
| `agent` | `vibe-agent` |
| `correlation_id` | the run id, as every `vibe_agent_*` line already carries (P-040), so `hook_fired`, `tool_vetoed`, `hook_error` and `plugin_hook_timeout` lines of one run are found with the run's own lines |
| `ctx.task` | empty |
| `ctx.context` | JSON `{"project", "run", "step", "tool", "args"}`: the arguments the engine parsed (path, line range, the SEARCH/REPLACE pairs, a write's content, the command) |
| `post_tool` result | the result text before it is wrapped in `[RESULT ...]` |
| `on_operation_start` / `_end` | once per run, `operation_id` = the run id; `_end` has `duration_ms` and `outcome` = the run's status (`done`, `stopped`, `limit`, `failed`) |

`ctx.task` is empty on purpose: `codec_hooks` writes a preview of the task into the `tool_vetoed` audit line, and
the Vibe agent never writes a path, a file's text or a command into the audit log. `ctx.context` is never written
there by `codec_hooks`.

**What a plugin can do.** A `pre_tool` that returns `HookVeto` stops the step: the tool is not run and the model's
next result reads `Tool 'vibe:edit_file' was vetoed by plugin 'X': <reason>` and that the step was not run. The step
is a failed step with blocked reason `plugin` (one `vibe_agent_blocked` line); it does not count as a failed edit.
Everything else is only observed:

- a `pre_tool` rewrite of `task` or `context` is ignored: Vibe arguments are structured, and the call runs as parsed;
- a string returned by `post_tool` is ignored too: the change is already made, and the result also feeds the page,
  the checkpoints and the failed-edit count, so a rewritten model text would make the model and the page disagree.

**run_check.** `pre_tool` fires before the danger check and before the owner is asked, so a vetoed command is never
put in front of the owner and never runs. If the run ends while the command waits for the owner, `post_tool` gets
"The run ended before this step finished." and the run ends as before.

**Errors.** A tool error the tool does not catch fires `on_error`, then the run fails as before. An error inside the
hook layer itself is logged and the step runs without hooks.

**Fail open.** Every hook runs in `codec_hooks`' daemon thread with its timeout (`plugin_hook_timeout_ms`, default
500 ms). A slow plugin costs a step at most that timeout per plugin and hook, and the run goes on. The hooks are a
guard rail; the sandbox and the Vibe path rules stay the boundary. Only the Python plugins in `~/.codec/plugins`
behind the SHA-256 allowlist run; shell-command hooks are not built (GAP-224, a later item).

**Switch.** `config.json` `vibe_agent.hooks` (default `true`). With `false`, `run_with_hooks` and the operation
emitters are never called: no hook call and no hook thread. Read when a run is created, so a change applies from the
next run without a restart. With hooks on and no plugin registered, `codec_hooks` finds no plugin and starts no
thread.

A run that fails before its start event (for example a project over the file cap) fires neither operation hook. A
continued run (P-044) is a new run with its own pair.

## Live effect

The live `self_improve` plugin applies to all tools, so after a dashboard restart it sees `vibe:<tool>` results in
`post_tool` and runs its throttled, draft-only `on_operation_end` after each Vibe run. Its gap finder
(`codec_self_improve._find_gaps`, which the live plugin imports from the repo, so no plugin file or allowlist entry
changes) skips `vibe:<tool>` names: they are not skills. Without that skip, any tool used twice in a run counted as a
"missing tool", and the end of a run drafted up to three skill proposals (each tool name at most once in 30
minutes) on the chat model (`llm_base_url`, up to 4000 tokens each), next to the agent's own use of the local model. A plugin can leave Vibe out
with `if ctx.transport == "vibe": return`; `vibe_agent.hooks: false` turns all of it off for the agent.

Order: when the run ends while a `run_check` waits for the owner (Stop, or no answer in time), `on_operation_end`
fires inside that wait, and the step's `post_tool` (with the text above) comes after it.

## Tests

`tests/test_vibe_agent_hooks.py`, on the scripted model: a `PluginRegistry` on `tmp_path/plugins` with a temp
allowlist holding each test plugin's SHA-256; `codec_hooks._log_event` and `codec_plugin_trust._log_event` captured;
the hook timeout set by monkeypatch. One test each for the veto (edit not applied, veto reason in the next result,
`tool_vetoed` line), a vetoed `run_check` (the owner is not asked, nothing runs), counts of `post_tool` and the
operation hooks, the tool filter, the correlation id, ignored rewrites, `on_error`, `hooks: false`, the slow plugin
and no plugin. `tests/vibe_agent_fakes.py` swaps in an empty temp registry for every Vibe agent test, so no test
loads `~/.codec/plugins`.

## Rollback

`vibe_agent.hooks: false` in `config.json` (next run, no restart). Code: revert the PR.

## Owner

Restart `codec-dashboard` (the agent runs inside it).
