# Skill time limits for chat, voice and the wake word (P-087)

## What

A skill run from the chat, the voice call or the wake word is waited for at most a set time: 60 seconds unless the skill is slow by design. When a run passes its limit, CODEC stops waiting, answers with one short line ("chrome scroll timed out after 60 seconds. If a macOS permission prompt is open, answer it and try again."), writes one `skill_timeout` audit line, and the chat or call carries on. The run itself keeps going in a daemon thread (Python cannot stop a thread); its late result is dropped.

The four `osascript` calls in `chrome_scroll` and `chrome_automate` get `timeout=10` and answer a short line on `TimeoutExpired`.

## Why

`osascript` waits for an unanswered macOS Automation prompt until someone clicks it. On a buyer's first run, saying "scroll down" on a voice call while that prompt was open hung the whole call: `dispatch_skill` awaited the executor with no limit and the pipeline never took the next utterance. Chat (`_try_skill`, the '@' pick, the `[SKILL:]` tag) and the wake word had no limit either. MCP (30 s) and crew tools (120 s) already had one.

## Design

`codec_dispatch`:

- `SKILL_TIMEOUT_S = 60` and `SLOW_SKILL_TIMEOUTS`, a map of skills whose own waits are longer (seconds, or `None` for no limit). Reasons, from each skill's code:

  | Skill | Limit | Its own wait |
  |---|---|---|
  | `delegate` | 330 s | webhook call up to 300 s |
  | `mcp_connect` | 240 s | server sign-in up to 180 s |
  | `skill_forge` | 210 s | model call up to 180 s |
  | `create_skill` | 120 s | 60 s model call, then the review post |
  | `fact_extract` | 240 s | 60 s model call, up to 3 tries |
  | `compare` | 150 s | 60 s per model, 6 at a time |
  | `email_triage` | 180 s | mail fetch, then a 60 s model call |
  | `AI News Digest` | 180 s | about a dozen feeds and searches in a row |
  | `prompt_feeder` | 720 s | up to 12 prompts, 30 s each, in the browser |
  | `cookbook_serve` | 150 s | pm2 start up to 30 s, then a health check up to 90 s while the model loads |
  | `model_switch` | none | server restart and model load, each step bounded in `codec_models` |
  | `ask_user`, `stuck` | none | wait for the owner's answer (`ask_user.timeout_seconds`) |
  | `google_gmail` | none | sending waits for the owner's "send" |

- `skill_timeout(name)`: `config.json:skill_timeouts[name]` if set, else the general limit (`skill_timeouts.default`, else `SKILL_TIMEOUT_S`), raised to the skill's `SLOW_SKILL_TIMEOUTS` entry when it has one. `0` or `null` means no limit; anything else that is not a positive number is ignored. Read from `codec_config.cfg`, so a change needs the restart below.
- `time_limited(via)`: a context manager (`via` is `chat`, `voice` or `wake`) that sets a context variable. `run_skill` applies the limit only inside it, and only around the skill's own run (`registry.run`, inside the hooks' `invoke`), so consent questions and plugin hooks are not counted. The agent runner, scheduler, triggers, sessions and bridges never enter it, so their skills keep no limit (the review's warning against a blanket limit in `run_skill`).
- `run_bounded(name, fn, *args)`: `codec_concurrency.run_with_timeout` in a copy of the caller's context. The skill's own exceptions (a `TimeoutError` from its own socket included) come back unchanged; only the limit raises `SkillTimedOut` (a `TimeoutError` subclass), which `run_with_hooks` passes to `on_error` plugins.
- `note_timeout(err, via, correlation_id)`: the one audit line and the short reply. On a timeout `run_skill` returns that reply and does not try the next matching skill (the first may still be acting).

Callers: `routes/chat.py` `_try_skill`, `_try_explicit_skill`, `_try_skill_by_name` (also behind `/api/command` and the Today page) and `codec.py` `_dispatch_inner` wrap their `run_skill` call in `time_limited`. `VoicePipeline.dispatch_skill` has its own registry, so its `invoke` calls `run_bounded` directly and the executor thread comes back after the limit.

Brief reading: "research crews" and "scheduler" in the done-when are the crew and scheduler paths. Neither runs through a limited path (`dispatch_crew_from_voice` awaits `run_crew`; the scheduler daemon calls `run_skill` outside `time_limited`), and the `scheduler` skill itself only edits the schedule list, so it is not in the map.

## Audit

`skill_timeout`, existing envelope (no schema change): source `codec-dispatch`, level `warning`, outcome `timeout`, `tool` the skill, `transport` `chat` | `voice` | `dispatch` (wake word), `duration_ms`, `error_type` `SkillTimeout`, `extra` `{limit_s, via}`, `correlation_id`. Never the request text.

## Migration

None. Takes effect after the owner restarts `codec-dashboard` (chat and voice call) and `open-codec` (wake word).

## Tests

`tests/test_skill_timeouts.py`, with a fake skill that sleeps 5 s and the limit patched to 1 s: the chat route and the two other chat paths, `VoicePipeline.dispatch_skill`, a real `_pipeline` turn followed by a second utterance, and `codec._dispatch_inner` each return the short reply in about a second and write one `skill_timeout` line; a fast `weather` returns normally on each; slow skills keep their wait and the config overrides; the agent runner, scheduler, crew tool and voice crew dispatch run past the limit untouched; the four `osascript` calls pass `timeout=10` and answer the short line on `TimeoutExpired`. No test touches `~/.codec` (audit log, overlay events and memory are redirected or faked).

## Rollback

`"skill_timeouts": {"default": 0}` in `~/.codec/config.json` plus the two restarts turns every limit off; or revert the PR.

## Residual

An abandoned run keeps its thread (and any child process) until its call returns; the chrome `osascript` calls now end within 10 s. A skill that hangs for good leaves one parked daemon thread per attempt.
