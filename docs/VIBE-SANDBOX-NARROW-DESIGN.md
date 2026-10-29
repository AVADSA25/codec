# Vibe sandbox: narrower reads, protected hook folders — design

Status: built in one pull request (plan item P-028). **Owner decision D-23, 29 Oct 2026:** "Yes, narrowing only, never widening." The command sandbox (`codec_vibe_agent_cmd.profile`) and the agent's file tools are AGENTS.md section 10 zones; this change only takes rights away, it adds none.

## Why

The 25 Oct audit ran commands inside the Vibe command sandbox and found two gaps:

1. **Reads.** The profile opened the whole of `~/.local` as a "toolchain" folder. That folder also holds app logins and keys: on this Mac `~/.local/share/opencode/auth.json` and `~/.local/state/ibkr/order-preview-key` were readable from a sandboxed command. A command the owner stored with "Always allow" and the web box ticked could send such a file out.
2. **Writes that run later.** A command could write `.git/hooks/pre-commit` or `.git/config` in the project, and the agent's file tools could write `.vscode/tasks.json`, `.husky/pre-commit` or `.github/workflows/*.yml`. Nothing runs at that moment, but git, the editor or GitHub runs that code later, outside the sandbox, when the owner commits, opens the folder or pushes.

## What changes

### 1. Reads in `~/.local` (the sandbox profile)

`.local` is removed from `TOOLCHAINS` and replaced by the parts of it that interpreters need:

| Folder | Why it stays readable |
|---|---|
| `~/.local/bin` | where pipx, uv and others put the commands people type |
| `~/.local/lib` | Python's own user packages (`pip install --user`) for Pythons that keep them there |
| `~/.local/pipx/{venvs,shared,py}` | pipx's tool environments, older layout (the one on this Mac) |
| `~/.local/share/pipx/{venvs,shared,py}` | the same, newer layout |
| `~/.local/share/uv/{python,tools}` | the Pythons uv installs and uv's tool environments |

Everything else in `~/.local/share` and all of `~/.local/state` is now closed, like the rest of the home folder. That includes `~/.local/share/uv/credentials` (where `uv auth login` keeps its passwords), which is why uv gets two subfolders and not its whole folder. File names and sizes can still be seen, as before (`file-read-metadata` on the home folder is unchanged); the contents cannot.

### 2. Writes into `.git` (the sandbox profile)

A new rule, placed after the rule that allows writing in the project (in a sandbox profile the later rule wins), refuses writes to:

- `<project>/.git/hooks` and everything in it;
- `<project>/.git/config`;
- `<project>/.git` itself.

The third line matters: without it a command could rename `.git` away, change the config inside the renamed folder and rename it back, or build a whole `.git` elsewhere and move it in. Tried in the real sandbox before writing the code: renaming `.git`, moving a file onto `.git/config`, a hard link to `.git/config`, a symbolic link, and the same paths in capitals (`.GIT/CONFIG`, the Mac's disk ignores case) are all refused. The rest of `.git` stays writable, so `git add` and `git commit` still work inside a command; `git config`, `git init` and anything that installs hooks no longer do.

### 3. Folders the agent's file tools may not change (`codec_vibe_projects`)

`agent_write` and `agent_delete` (the agent's `write_file`, `edit_file` and `delete_file` tools) now go through `_agent_rel`: the usual `safe_rel` check, then a refusal when any part of the path is one of

`.vscode`, `.idea`, `.husky`, `.devcontainer`, `.github`, `.mcp.json`, `.claude`

(compared without regard to case, at any depth). These hold editor tasks, git hooks, CI workflows, container set-up and AI-tool settings: files other programs run or obey later. The model is told "The agent may not change '<name>' … The owner can edit it in the editor." and goes on with its task.

`safe_rel` itself is not changed, so the owner's own edits in the editor (`write_file`), restore and the other writers keep working on these paths exactly as before. `.git`, `.vibe`, `node_modules` and the secret-looking names stay refused for everyone, as today.

## What an agent command can no longer do

- read anything in `~/.local/share` or `~/.local/state` outside the folders in the table above;
- write `.git/hooks/*` or `.git/config`, create `.git`, or rename, replace or remove the `.git` entry;
- (agent file tools) write or delete anything under the seven names above.

Things that may stop working inside a command, accepted by D-23: a tool installed under another `~/.local/share` folder (for example mise, or Claude Code's own program); `git init`, `git config`, `husky install`, `pre-commit install`. The command then fails with "Operation not permitted" and the agent tells the owner, as for every other sandbox refusal. Nothing falls back to an unsandboxed run.

## Not in this change (follow-ups for the owner to decide)

- **The web box** still opens ports 80 and 443 to any address, including machines on the local network. Narrowing that is a separate plan item (egress).
- **A sandboxed command can still write** `.vscode`, `.husky`, `.github` and the others; only the agent's file tools are blocked from them. Closing them in the profile too would be another narrowing.
- **Chat mode** (`apply_blocks`, the reply blocks the page applies when an answer ends) goes through `safe_rel`, so it can still write those seven names. The plan item kept this change to the agent's tools.
- Git folders of submodules and extra worktrees (`.git/modules/*/hooks`, `.git/worktrees/*`) and other tool folders (`.cursor`, `.zed`, `.codex`, `.gemini`, `.windsurf`) are not on the lists.

## Tests

All in `tests/test_vibe_agent_cmd.py`. The sandbox tests run only on a Mac with `sandbox-exec`, like the existing ones, and build the profile for a made-up home folder under pytest's temporary folder, so no real file under `~/.local` is read or written. Every command carries the `vibe-run-test` marker.

| Rule | Test |
|---|---|
| `~/.local/share`, `~/.local/state` and uv's saved logins closed, `~/.local/bin` open | `test_of_dot_local_only_the_toolchain_folders_are_readable` (canary files in each; only the `bin` one is printed, three refusals) |
| pipx and uv interpreters still start | `test_pipx_and_uv_style_interpreters_under_dot_local_still_start` (a real venv with `pyvenv.cfg` in the new and the old pipx home, run through a console script linked from `~/.local/bin`, and a copied interpreter in `~/.local/share/uv/python/<version>`; each prints its own `sys.prefix`. Checked by hand: with those folders closed, Python stops with "Failed to import the site module") |
| `.git/hooks`, `.git/config` and the `.git` entry closed, the rest of `.git` open | `test_a_command_cannot_write_gits_hooks_or_config_nor_move_the_git_folder` (direct writes, capitals, hard link, move onto the config, renaming `.git`; then, with no `.git`, creating one or moving a prepared one in) |
| agent tools refuse the seven names, the owner's edit still works, `.git` refused for both | `test_the_agents_file_tools_refuse_folders_that_run_code_later_but_the_owner_may_edit_them` |

The 21 tests that were already in the file pass unchanged (no line of them was edited). That is the proof nothing was widened; a second check compared the profile built by the old and the new code line by line: the only line removed is the whole-`~/.local` allow, and every line added is either a part of `~/.local` inside the read list or the new `.git` refusal.

## Rollback

Revert the pull request and restart `codec-dashboard`. The change only narrows reads and writes; no project file, checkpoint, setting or `allowed_commands` entry is touched or rewritten, so nothing needs to be migrated back.

## When it takes effect

The profile is written by the running `codec-dashboard` for each command, so the change applies after a `codec-dashboard` restart (the owner's action).
