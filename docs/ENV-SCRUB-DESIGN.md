# P-084: one scrubbed environment for child processes

Audit finding BH-8 (environment part only) and GAP-412. Small change, no
section-10 zone touched.

## What

A command the `terminal` skill runs (`bash -c <cmd>`) inherits the whole
environment of the process that loaded the skill: the dashboard, `open-codec`
or a bridge. Any API key or token read from an environment variable is then
visible to the command, so an injected "run env" prints every key into the
model's context.

The rule that decides which variable names look secret already exists, but it
is pasted three times: `routes/vibe_exec.py` (`_SENSITIVE_ENV_RE`) and two
fallback copies in `codec_vibe_agent_cmd.command_env` and
`codec_vibe_runner._run_env`.

After this change:

- **New `codec_envscrub.py`** (stdlib only, no I/O): `SECRET_NAME_RE` (the
  same pattern, word for word) and `scrub(env, keep=()) -> dict`, a new dict
  without the variables whose names match, except the names in `keep`. The
  input mapping is not changed.
- **The three Vibe modules import it** and keep exactly what they do today:
  - `routes/vibe_exec._hardened_run_env`: `scrub(os.environ, keep=("SSH_AUTH_SOCK",))`,
    then `LC_ALL` defaults to `C.UTF-8` (unchanged).
  - `codec_vibe_runner._run_env`: the same call and the same `LC_ALL` default
    (today it calls `_hardened_run_env`), so `SSH_AUTH_SOCK` stays.
  - `codec_vibe_agent_cmd.command_env`: `scrub(os.environ)`, the same `LC_ALL`
    default, then `SSH_AUTH_SOCK` is removed as today. The sandbox profile
    (`profile()`) is not touched.
  The two modules no longer import `routes.vibe_exec` (a FastAPI route
  module) to get the rule, and the pasted fallbacks go.
- **`skills/terminal.py`** passes `env=scrub(os.environ)` to its
  `subprocess.run`. `PATH`, `HOME`, the locale and tool settings stay, so
  `git status`, `ls` and the like work as before. If `codec_envscrub` cannot be
  imported the skill refuses and runs nothing (it never falls back to the full
  environment). The skill file is hash-pinned, so `skills/.manifest.json` is
  regenerated in the same commit.
- **The crew / custom-agent `shell_execute`** needs no code change: since
  P-030 part 2 it builds its environment from five fixed names (`PATH`,
  `HOME`, `TMPDIR`, `LANG`, `TERM`) and runs inside `sandbox-exec`. The brief's
  evidence (`subprocess.run(cmd, shell=True)` with no env) is from before that
  change. A real-sandbox test now pins that no variable of the parent reaches it.
  (`git` does not run inside that sandbox on a Mac without Homebrew's git:
  `/usr/bin/git` hands over to Xcode in `/Applications`, which the profile
  does not allow. That is the sandbox, not the environment, and is unchanged
  here; widening the profile is the owner's call.)

## Why

One rule in one place: a later change to the list of secret-looking names
applies to every caller, and nobody pastes a fourth copy. The terminal skill
was the one caller of a user-typed or model-routed command that still saw the
dashboard's keys.

## API and data

- New: `codec_envscrub.SECRET_NAME_RE`, `codec_envscrub.scrub(env, keep=())`.
- No change to any HTTP route, config key, file on disk, audit event or
  `_HTTP_BLOCKED` / `_STDIO_BLOCKED` list (`terminal` stays in both).
- Behaviour change, by design: a terminal-skill command that needs a secret
  from the environment (for example a token for `git push` over HTTPS) no
  longer sees it. `SSH_AUTH_SOCK` does not match the rule, so `git push` over
  SSH with an agent still works.

## Migration

None. The owner restarts `codec-dashboard` and `open-codec` so the processes
load the new skill file and modules.

## Test plan

New `tests/test_envscrub.py`:

- `scrub` drops secret-looking names, keeps the rest, honours `keep`, and does
  not change its input.
- The pattern text appears in exactly one non-test Python file
  (`git grep -lF 'session[_-]?token' -- '*.py' ':!tests'`).
- Each Vibe module keeps its own `SSH_AUTH_SOCK` behaviour and the `LC_ALL`
  default, and drops a secret-looking name.
- With `MY_API_TOKEN` set only in the test's own environment and `is_dangerous`
  patched to `False`, `env | grep -i my_api` through `skills/terminal.run`
  prints nothing, while the same probe with the scrub replaced by a plain copy
  does print it (the control that proves the probe can see a leak). `PATH` and
  `HOME` arrive unchanged and `git status` works in a temp repository.
- The same probe through the crew `shell_execute` in the real sandbox (macOS
  only) prints nothing; a variable the tool does set (`TERM`) is printed, and
  `PATH` and `HOME` are there.

Unchanged and still passing: `tests/test_vibe_agent_cmd.py`,
`tests/test_vibe_agent_cmd_run.py`, `tests/test_vibe_runner.py`,
`tests/test_run_code_hardening.py`, `tests/test_crew_shell_sandbox.py`.

## Rollback

Revert the PR (or the terminal-skill commit alone, which restores the old
skill file and manifest together) and restart `codec-dashboard` and
`open-codec`.
