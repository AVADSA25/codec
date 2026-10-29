# Crew shell in a macOS sandbox, custom-agent tools checked — design (P-030 parts 2-4)

Status: approved. Owner decision D-17, 29 Oct 2026 ("crew shell in the sandbox with consent", CODEC audit board, recorded in the owner's decisions log). Plan item P-030 parts (2), (3) and (4); it owns audit finding GAP-237 (crew shell). Part (1), the per-call consent for chrome_fill, chrome_click_cdp and mouse_control and the wake/voice consent, shipped in #486 and is reused here, not rewritten. Written 29 Oct 2026.

## What and why

The crew / custom-agent tool `shell_execute` (`codec_agents._shell_execute`) ran `subprocess.run(cmd, shell=True)` in the home folder, as the owner, with the full environment. The only check was `codec_config.is_dangerous`, which is a typo-catcher, not a boundary. No built-in crew uses the tool; it reaches a model only through a custom agent, and the agent builder ticks every tool by default. An agent that also has `web_fetch` can be steered by page text into running a command, so the shell needs a real boundary.

This change:

1. Runs every `shell_execute` command inside `/usr/bin/sandbox-exec` with a profile built from codec_sandbox's template (the python_exec one, not the Vibe agent profile).
2. Asks the owner first, through `codec_consent.local_consent_ok`, for any command `is_dangerous` flags. The `is_dangerous` check stays.
3. Refuses to run at all where `sandbox-exec` does not exist (Linux CI, any non-macOS machine), with a clear message. It never falls back to an unsandboxed shell.
4. Checks a custom agent's tool list against the known tool names when it is saved; agents saved earlier still load, with unknown names left out.
5. Writes one audit line per approval or refusal, with lengths and reasons only, never the command text.

## The profile (codec_sandbox)

`_write_sandbox_profile` now takes keyword arguments. With none it writes byte for byte the profile python_exec and the sandboxed skills always had (checked against origin/main for both network settings): `~/.codec/sandbox.sb`, writes in `~/.codec/skill_output`, `/private/tmp` and `/private/var/folders`, programs from `/usr`, `/opt/homebrew` and the Python framework.

New arguments, used only by the crew shell:

| argument | meaning |
|---|---|
| `home` | the folder whose `.ssh`, `.aws`, `.gnupg`, `.config/gh`, `Library/Keychains` and `.codec/{oauth_state.json, secret.key, secrets.enc.json}` are unreadable |
| `profile_path` | where the profile file is written |
| `output_dir` | the one folder, besides `/private/tmp`, the command may write |
| `allow_shell` | adds `/bin` to the programs that may start (`/bin/sh`, `ls`, `cat`, `echo`; `/usr/bin` is already covered by `/usr`), drops the `/private/var/folders` write and allows writing `/dev/null` |

Folder names are escaped for the profile language, so a quote or backslash in a path cannot end a string and add a rule (a no-op for ordinary paths, so the default profile is unchanged).

The crew shell profile, in full:

- `(deny default)`; reading is allowed except the secret folders and files above (the template's rule: last match wins).
- Writes: the output folder (`~/codec-workspace/agent-shell`, created on first use), `/private/tmp`, `/dev/null`. Nothing else: not the home folder, not `~/.codec`, not the repo, not `/private/var/folders`.
- Programs: `/bin`, `/usr` (with `/usr/local`), `/opt/homebrew`, the Python framework.
- No network: outbound and inbound denied (also loopback).
- Mach lookups: the system logger only (the template's rule), so no Apple Events to other apps, no Keychain service, no clipboard, no user-name lookups.

Why `/private/tmp` and not the per-user temp folder: the brief says "the output folder and /tmp". The command gets `TMPDIR=/private/tmp`. `/private/tmp` is shared with every other program the owner runs, so a command can change files other programs leave there (python_exec writes its snippet there before running it in its own sandbox); narrowing that to a per-call temp folder is a possible follow-up.

Reading stays as broad as the template allows (everything but the secret folders). That is no wider than the agent's own `file_read` tool, which reads anything under the home folder.

## How one call runs

1. Empty command: refused.
2. No `/usr/bin/sandbox-exec`: refused, nothing starts.
3. `is_dangerous` flags the command:
   - consent gate off (`CONSENT_GATE_ENABLED=false`): refused, as before this change (a flagged command was always blocked; turning the gate off must not let it run unasked);
   - longer than 200 characters: refused, because the approval card shows the first 200 characters of the request and the owner must see the whole command he approves;
   - otherwise `codec_consent.local_consent_ok("shell_execute", cmd, "crew")` asks: strict consent (`destructive=True`), the PWA card and a content-free phone push, 60 s, then it counts as declined. Declined or no answer: refused.
4. The profile is written to a new file `~/.codec/crew_shell-*.sb` (a folder the sandbox cannot write, one file per call so two agents never share or race on one), the command runs as `sandbox-exec -f <profile> /bin/sh -c <cmd>` in the output folder, and the file is deleted afterwards.
5. The command gets only `PATH`, `HOME`, `TMPDIR=/private/tmp`, `LANG` and `TERM`: no inherited API keys or tokens. Input is empty. After 30 s the whole process group is stopped, so nothing it started in the background keeps running.
6. Output is cut as before (5,000 characters of stdout, 2,000 of stderr). When the kernel refused something ("Operation not permitted") a one-line hint tells the agent the rules.

`shell_execute` is added to `codec_consent._LOCAL_CONSENT_REQUIRED` so `local_consent_ok` asks for it. It is a built-in tool, not a skill: no skill has that name, so chat, voice, the wake word and MCP are unchanged, and it is not added to `_DESTRUCTIVE_BUILTINS` or `_HTTP_BLOCKED` (MCP never reaches it).

## Custom-agent tool lists (part 3)

- `POST /api/agents/custom/save`: `tools` must be a list of names from the list `/api/agents/tools` shows (the built-in tools plus the scanned skills, `codec_agents.known_tool_names`). An unknown name is refused with 400, `{"error": "Unknown tool name(s): a, b. Pick tools from the list /api/agents/tools gives.", "unknown_tools": [...]}`, and nothing is written. A missing `tools` saves an agent with no tools, as before.
- `GET /api/agents/custom/list` and the '@' picker (`routes/palette._agents`): an agent saved earlier loads with the names CODEC does not know left out (`codec_agents.known_tools_only`), and one `custom_agent_tools_dropped` audit line per agent and dropped set per process (the list is polled, so it does not repeat). The saved file is not changed: if a skill that was removed comes back, the agent gets it again.
- Not changed: `POST /api/agents/run` with `crew: "custom"` takes its tool list from the request body, and `run_custom_agent` already leaves out names it does not know.

## Audit lines (part 4)

| event | when | extra |
|---|---|---|
| `local_consent_decision` (existing, `codec_consent`) | the owner approved or refused a flagged command | `asked_from: "crew"`, `decision`, `task_chars`; top-level `tool: "shell_execute"` |
| `shell_blocked` (existing name, new fields) | the tool refused by itself | `reason` (`empty`, `no_sandbox`, `consent_gate_off`, `too_long_to_show`, `sandbox_setup_failed`), `cmd_chars`; `outcome: "denied"`. It used to carry `cmd` (the first 200 characters); it no longer does |
| `custom_agent_tools_dropped` (new) | a saved agent named unknown tools | `agent_id`, `dropped`, `kept` |

Each approval or refusal writes exactly one of these lines. A command that runs without asking writes no extra line.

Not changed here, and still holding command text (open follow-ups, outside this item's scope):

- `tool_call`, written by `Agent.run` for every tool of every agent, keeps the first 200 characters of the tool input (for the shell, the command).
- `ask_user_question_emit`, written by `codec_ask_user.ask` for every question, keeps a preview of the question; the shell card's question contains the command, as the voice and wake cards contain the request.

## What custom agents can no longer do

- Reach the network from the shell: no `curl`, `git pull`, `pip install`, `npm install`, `ssh`, `ping`, not even to this Mac.
- Write anywhere but `~/codec-workspace/agent-shell`, `/tmp` and `/dev/null`: no edits in the home folder, Documents, repos or `~/.codec` (the `file_write` tool, with its own folder rules, is the way to write files elsewhere).
- Read `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`, the Keychains and the three `~/.codec` secret files.
- Start programs outside `/bin`, `/usr`, `/opt/homebrew` and the Python framework: Apple's `/usr/bin/git` and `/usr/bin/python3` shims fail (they start tools from the developer folders); Homebrew's `python3` and `node` work; nothing from `~/.nvm`, `~/.pyenv`, `~/bin` or `/Applications`.
- Talk to other apps, the clipboard or the Keychain service; `whoami` and `ls -l` show numbers instead of user names.
- See inherited environment variables (API keys, tokens).
- Run a flagged command without the owner's OK (60 s), or a flagged command longer than 200 characters at all.
- Start in the home folder: the working folder is now `~/codec-workspace/agent-shell`, so `ls` lists that folder (paths like `ls ~/Documents` still work, reading is allowed).
- Run the shell at all on a machine without `sandbox-exec`.
- Be saved with tool names CODEC does not know.

## Not changed

- python_exec and the sandboxed skills: same profile bytes, same file, same tests.
- The Vibe agent command sandbox (`codec_vibe_agent_cmd.profile`): untouched; its narrowing is P-028, a separate item.
- Chat, voice, wake word, Session and MCP consent paths.

## Tests

`tests/test_crew_shell_sandbox.py`. `codec_ask_user.ask` is patched for every test in the file (autouse), so no test reaches the owner's approval card.

- Everywhere: the profile builder (shell profile adds `/bin` and `/dev/null`, drops `/private/var/folders`; the default keeps them; a quote in a folder name cannot add a rule); the tool's decisions with the sandbox run stubbed (no `sandbox-exec` refuses and starts nothing; an ordinary command does not ask; a flagged command asks through `codec_consent` with `destructive=True`, `asked_from="crew"`, and a decline runs nothing; an allow runs it; gate off and too long are refused; every line has lengths only); custom-agent save and load through the real routes with a temp agents folder.
- macOS only (skipped where `sandbox-exec` does not exist), everything in a temp folder under the per-user temp folder (`getconf DARWIN_USER_TEMP_DIR`, not `/private/tmp`, which the profile may write), with a temp home holding a canary in `<temp home>/.ssh`: `echo`, `ls`, `pwd` and `/dev/null` work; reading the canary is refused by the kernel ("Operation not permitted"), also when the command hides the path from `is_dangerous` and when a flagged read is approved; an outbound connection to a local listener the test starts is refused (and the same command outside the sandbox reaches it, as a control); writes land only in the output folder and `/private/tmp`; an approved `rm -rf` outside the output folder changes nothing; no inherited secrets and no input; a command past the time limit is stopped with its background children; the per-call profile file is gone afterwards.
- `tests/test_full_product_audit.py::test_dangerous_shell_blocked` now declines through a patched `local_consent_ok` instead of reaching a real card.
- Unchanged and still passing: `tests/test_python_exec.py`, `tests/test_skill_sandbox.py`.

## Rollout and rollback

Takes effect after restarts of `codec-dashboard` and `codec-agent-runner` (the owner's action). Rollback: revert the PR. Custom agents then run the shell unsandboxed again, exactly as before, saved agents keep loading, and the `shell_execute` entry in `_LOCAL_CONSENT_REQUIRED` goes with the revert.
