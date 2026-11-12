# Vibe agent: rules for "Always allow" — design

Status: built in one pull request (plan item P-048, board card E-11), **left open for the owner's word**. It touches an AGENTS.md section 10 zone: the command approval rules (`.vibe/project.json:allowed_commands`, "a way to run a command without the owner's click"). The sandbox profile (`codec_vibe_agent_cmd.profile`) is not changed.

## Why

Today "Always allow here" stores one exact command. `git diff`, `git diff --stat`, `git diff HEAD~1 -- src/app.py` are three cards, and every new file name is one more. The owner wants to say once "the agent may run `git diff` with any arguments", while a trick like `git diff; curl x` still shows the card.

## What changes, in one paragraph

Next to the always-allowed commands, the owner can add a **rule** such as `git diff *`. When the agent asks to run a command, CODEC first runs the danger check exactly as today, then looks for the exact stored command as today, then tries the rules. A rule lets the command run without a card only when the command is the rule's words followed by plain arguments, with nothing a shell would treat as a second command or an expansion. A command that runs under a rule runs in the same sandbox, **with no network**, and the run card shows a line naming the rule. Anything else shows the card as before.

## Rule syntax

```
WORD [WORD ...] *
```

- One to six literal words, one space between words, then a space and `*`. The `*` stands for "any further arguments, or none". It is required, and it is the only place a `*` may be.
- The first word is a program name: letters, digits and `. _ + -`, starting with a letter or digit, at most 64 characters. No `/` (no path of any kind), no `=` (no `NAME=value` in front).
- Later words: letters, digits and `. _ : = @ % + , / -`. No quotes, `~`, `^`, `$`, wildcards or anything else.
- At most 200 characters. The editor also accepts the card's notation `run_check(git diff *)` and stores it as `git diff *`.
- Stored as written, one entry per rule: `{"rule": "git diff *"}`.

Examples: `git diff *`, `git log --oneline *`, `ls *`, `wc -l *`, `tsc --noEmit *`.

## The matcher

`codec_vibe_agent_rules.match(rules, command, project)` answers the first rule that allows the command, or nothing. It runs after `check_command` (500 characters, one line, `is_dangerous`), which still refuses first; a rule never skips it. Every step below must pass, otherwise there is no match and the card is shown:

1. **Only plain characters.** The whole command is printable ASCII and spaces. A tab, a new line, an escaped new line, a NUL, any control character and any character outside ASCII (look-alike letters such as a Cyrillic `і`, a full-width `；`, a no-break or zero-width space) means no match.
2. **No shell control or expansion anywhere, quoted or not.** None of `;` `&` `|` `` ` `` `$` `>` `<` `(` `)` `\` `*` `?` `[` `]` `{` `}` `!` `#`. That covers `;`, `&&`, `||`, pipes, background `&`, `$(...)`, `${...}`, `$VAR`, backticks, redirections, sub-shells, globs, brace expansion, history and comments. A `~` or `^` is allowed only straight after a letter or digit (`HEAD~1`, `HEAD^`), where bash never expands it; `~/x`, `--x=~/y` and `a:~` mean no match.
3. **Split without a shell.** The command is split with `shlex.split` (POSIX rules). With the characters left after step 2, that gives the same words bash does: quotes are removed, nothing is expanded. A quote that is not closed means no match.
4. **Starts with the rule's words, as written and as split.** The text must begin with the rule's words joined by single spaces, followed by a space or the end; and the first split words must equal the rule's words. `git  diff` (two spaces), `"git" diff`, `git diff" x"`, `git difftool`, `gitdiff`, `env git diff`, `GIT_X=1 git diff` and `git -C .. diff` do not match `git diff *`.
5. **The program is a program outside the project.** The first word is looked up on the PATH the command will get (the same `command_env` the sandbox runner builds), the way bash does: a PATH entry that is empty or relative is looked up in the project folder, and a hit there means no match; a hit whose real path is inside the project means no match; no hit means no match. The real path's file name is checked against the refusal list again, so a link named `foo` that leads to `node` does not match.
6. **The environment cannot run code before the command.** If the command's environment holds `BASH_ENV`, `ENV`, `SHELLOPTS`, `BASHOPTS`, `PS4` or an exported bash function (`BASH_FUNC_*`), no rule matches.

A matched command runs through the same `_run_command` (sandbox, watchdog, time limit, output limits) with `network=False`. The web flag stays per approval: a command that needs the web needs a card (or an exact always-allowed entry with the web ticked).

What a rule does **not** check: what the program does with its arguments. A rule says "I trust this program with any arguments". That is why the editor refuses the programs that run code or other commands given in their arguments, and why the sandbox stays the boundary. The command's output goes back to the run's model as it does today, including on a cloud run, so a rule like `cat *` lets the agent read what the sandbox lets it read (the system and this project, not the home folder) without a card.

## What the editor refuses

`codec_vibe_agent_rules.check_rule` refuses, with a plain reason the page shows:

- no word before the `*`, a bare `*`, no ` *` at the end, a `*` anywhere else, two spaces, more than six words, more than 200 characters, a character outside the lists above;
- a first word that is a path (`./build.sh`, `scripts/x`, `/usr/bin/git`), names a file or folder at the top of the project, ends like a script (`.sh`, `.py`, `.js`, `.mjs`, `.cjs`, `.ts`, `.rb`, `.pl`, `.php`, `.lua`, `.command`, ...), or cannot be found on the command PATH, or is found inside the project;
- a first word on the refusal list, compared in lower case (the Mac's disk ignores case) and also with a version ending removed (`python3.12`, `pip3`, `node18`, `gcc-13`):
  - shells: `sh`, `bash`, `zsh`, `dash`, `ksh`, `csh`, `tcsh`, `fish`, `pwsh`, `nu`, `xonsh`, `busybox`, and the rest of that family;
  - bash builtins and keywords (bash runs them itself, not from the PATH): `eval`, `exec`, `source`, `command`, `builtin`, `trap`, `export`, `echo`, `cd`, `time`, `if`, `for`, ...;
  - interpreters: `node`, `deno`, `bun`, `python`, `python3`, `pypy`, `ipython`, `ruby`, `perl`, `php`, `lua`, `osascript`, `swift`, `java`, `R`, `Rscript`, `julia`, `awk`, `sed`, `m4`, `dc`, `gdb`, `lldb`, `vim`, `emacs`, `ed`, ...;
  - package managers: `npm`, `npx`, `yarn`, `pnpm`, `corepack`, `pip`, `pipx`, `uv`, `uvx`, `poetry`, `pipenv`, `pdm`, `hatch`, `conda`, `gem`, `bundle`, `cargo`, `composer`, `brew`, `nix`, ...;
  - build and test runners and compilers (they run the project's own files, which the agent can write): `make`, `cmake`, `ninja`, `just`, `task`, `tox`, `nox`, `pytest`, `jest`, `vitest`, `mocha`, `playwright`, `tsx`, `ts-node`, `vite`, `webpack`, `eslint`, `prettier`, `mypy`, `pylint`, `pre-commit`, `gradle`, `mvn`, `rake`, `xcodebuild`, `xcrun`, `go`, `rustc`, `gcc`, `clang`, `cc`, `javac`, `docker`, ...;
  - programs that run another command or code given in an argument: `env`, `sudo`, `su`, `doas`, `nohup`, `nice`, `timeout`, `xargs`, `parallel`, `watch`, `caffeinate`, `script`, `open`, `launchctl`, `ssh`, `rsync`, `find`, `fd`, `tar`, `zip`, `rg` (its `--pre`), `sort` (its `--compress-program`), `sdiff`, `diff3`, `man`, `nc`, `socat`, `openssl`, `sqlite3`, `psql`, `mysql`, `wget`, `defaults`, `shortcuts`, `scutil`, `security`, ...;
  - programs that delete or move files past the checkpoint: `rm`, `rmdir`, `unlink`, `mv`, `dd`, `shred`, `truncate`;
- `git` with no second word, or a second word that is not one of its read-only commands: `status`, `diff`, `log`, `show`, `blame`, `annotate`, `describe`, `rev-parse`, `rev-list`, `ls-files`, `ls-tree`, `shortlog`, `name-rev`, `merge-base`, `cherry`, `range-diff`, `diff-tree`, `diff-files`, `diff-index`, `check-ignore`, `check-attr`, `count-objects`, `whatchanged`, `show-ref`. So `git *`, `git -c x *`, `git config *`, `git fetch *` (its `--upload-pack`), `git grep *` (its `-O`), `git rebase *` (its `-x`), `git branch *` (`-D`) are refused;
- words that `is_dangerous` flags, as in `rm -rf *` or `git log --output=/etc/passwd *`.

The full lists are in `codec_vibe_agent_rules.py` (`REFUSED`, `GIT_READ_ONLY`). They are a floor: making one of these a rule is the owner's call and needs a code change. An exact command (`npm test`, `pytest -q`) can still be stored with "Always allow here", as today.

Stored rules are checked again every time they are read (the static part: syntax, lists, git, danger), so a rule written into `project.json` by hand, or one a later version of the list refuses, is ignored.

## Storage

- `.vibe/project.json:allowed_rules`, a list of `{"rule": "..."}`, at most 20 (the newest 20 are kept, like `allowed_commands`). No database change.
- Written only by `codec_vibe_projects.allow_rule` / `forget_rule`, under the project lock, which the routes call on the owner's click. The agent cannot write `.vibe` (`safe_rel`), its commands cannot either (the sandbox denies writes to `.vibe`), and nothing in a run, a skill, a playbook or a file calls `allow_rule`.
- Audit: `vibe_agent_allow` with `action` `allow_rule` or `forget_rule` and `network` false (never the rule text); `vibe_agent_command` with `decision` `rule` for a command a rule let run (the program name only, as today).

## Routes

All under `/api/vibe/projects/{pid}/agent`, all answering 403 to anything not from this Mac (`_is_remote_request`, like approve, list and forget), 404 when the agent or projects are switched off:

| route | what |
|---|---|
| `GET /rules` | the project's rules |
| `POST /rules` `{rule}` | adds one; 400 with the reason when the editor refuses it |
| `POST /rules/forget` `{rule}` | removes one; 404 when it is not on the list |

## The page

The box at the end of a run card that lists "Commands allowed here without asking" (drawn after a run that used `run_check`) also lists the rules under "Rules: commands that start with these words run without asking, with no web", each with **Forget**, and a small form to add one (placeholder `git diff *`). A refused rule shows the server's reason. A command that ran under a rule shows the line "Ran without a card: your rule git diff * allows it (no web)." in the run's timeline. Every text goes in with `textContent`.

## Tests

`tests/test_vibe_allow_rules.py`:

- the rule syntax: accepted rules and their stored form; refused shapes (bare `*`, no `*`, `*` in the middle, two spaces, tabs, too many words, paths, quotes, `$`, non-ASCII);
- the refusal list: at least `npm`, `npx`, `yarn`, `pnpm`, `node`, `python`, `python3`, `python3.12`, `pip`, `pip3`, `uv`, `bash`, `sh`, `zsh`, `env`, `sudo`, `make`, upper-case forms, builtins, script names, project files, `git` outside its read-only commands, dangerous words;
- the matcher: positive cases (`git diff`, `git diff --stat`, `git diff HEAD~1 -- src/a.py`, quoted arguments) and many bypass variants in the style of `tests/test_dangerous_command.py`: every control and expansion character in many positions, escaped new lines, look-alike and invisible characters, spacing and quoting tricks around the rule's words, a program found inside the project or in a relative PATH entry, a link to an interpreter, an environment with `BASH_ENV` or an exported function;
- in a run (the sandbox replaced by the recording fake of `tests/test_vibe_agent_cmd_run.py`): a matching command runs with no card and `network=False`, with the note and the `rule` audit; a non-matching one shows the card; a dangerous command is still refused before any rule; an exact stored entry still wins (with its web flag); a rule written by hand into `project.json` that the editor would refuse is ignored;
- the routes: add, list, forget, 400 with the reason, 403 from the tunnel, the cap of 20;
- the page wiring: the rules list and form use `el()` / `textContent`, post to `/rules` and `/rules/forget`, and no `innerHTML`;
- one real-sandbox test (macOS only, marker `vibe-run-test`): a command that runs under a rule still cannot write outside the project.

## What stays the same

The sandbox profile and runner (`codec_vibe_agent_cmd.py`), the danger check, the exact "Always allow here" entries and their web flag, the approval card, the 403 for anything not from this Mac, and the rule that a run started away from the Mac has no `run_check` at all.

## Rollback

Revert the pull request and restart `codec-dashboard`. The `allowed_rules` key left in `project.json` files is ignored by the old code (it reads only `allowed_commands`, and its writes keep unknown keys), so commands a rule allowed show the card again. Nothing else to undo.
