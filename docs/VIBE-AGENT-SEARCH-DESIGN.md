# Vibe agent: list by glob, search by regex, files-only and count, a file map that reads no file (P-066)

Date 2026-11-03. Owner's go for the 25 Oct audit plan: "go go build i trust ur judgment here" (3 Nov 2026).
Code: `codec_vibe_agent_proto.py` (the `search` arguments, two prompt lines), `codec_vibe_agent.py` (`list_files`
and `search`), a new `codec_vibe_agent_search.py` (glob matching and the regex child), `codec_vibe_projects.list_files`.
The command sandbox (`codec_vibe_agent_cmd.profile`) is not changed.

## Why

1. The agent can list a folder but not "every .js file under src", so it lists everything and reads the names.
2. `search` is plain text only, and after 40 matches it answers "narrow the search", even when the model only wants
   to know which files mention a name, or how many times.
3. The project map sent with every step comes from `list_files`, which reads and hashes every file of the project
   (up to 200 files of 1 MB) to report a name and a size. The hash is thrown away.

Regex was left out on purpose (docs/VIBE-AGENT-DESIGN.md, section 3): Python's `re` cannot be stopped, so a pattern
like `(a+)+$` could hang the dashboard. That stays true: the pattern never runs in the dashboard process.

## What changes

**list_files.** `@@ list_files [folder or glob]`. An argument with `*`, `?` or `[` is a glob; anything else is a
folder, as before. Rules: `/` separates folders; `**` is any number of folders, none included; `*`, `?` and `[...]`
match inside one name; a glob without `/` matches the file name in any folder (`*.css`); case does not matter. The
glob must be at most 200 characters, relative, with no `..`, backslash or control character. Matching is pure
Python: one step per path segment, each name compared with `fnmatch.fnmatchcase`, whose translation has no
catastrophic backtracking (CPython 3.9 and later). Secret-named files stay hidden; the 200-line cap is unchanged.

**search.** `@@ search [--regex] [--files] [--count] "text" [PATH or glob]`.
- Flags are bare words before or after the text. The grep spellings `-E`/`-P` (regex), `-l` (files) and `-c`
  (count) are read too, `-r`, `-R`, `-n` and `-i` are accepted and change nothing (search is always recursive,
  numbered and any case), and short groups such as `-rl` work. A quoted text that looks like a flag (`"-l"`) stays
  the text. `--count` wins over `--files`.
- The call's arguments gain `regex` (true or false) and `mode` (`lines`, `files` or `count`).
- `lines`: as today, 40 matching lines, then "(more matches exist: narrow the search)".
- `files`: the name of every file with a match, no cap message (a project has at most 200 files).
- `count`: matching lines per file and the total. Every line is counted.
- PATH may be a file, a folder or a glob.

**The regex child** (`codec_vibe_agent_search.run_regex`). The dashboard never compiles the model's pattern.
- argv is fixed: `[sys.executable, "-I", "-c", CHILD_CODE]` plus `EXTRA_ARGV`, which is empty in the product and
  carries the `vibe-run-test` marker in tests. No model text in argv, no shell.
- stdin carries the pattern, the mode, the cap and the files as JSON; stdout is one JSON line; stderr is dropped.
- Empty environment, working folder `/`, its own process group (`start_new_session`).
- Limits set between fork and exec (the same pattern as `codec_vibe_agent_cmd._limits`): `RLIMIT_CPU` 2 s soft,
  3 s hard; `RLIMIT_AS` 512 MB; no core file. macOS refuses any `RLIMIT_AS` below the address space a process
  reserves at start (about 400 GB on the Studio), so there the call fails and is skipped; the memory bound on the
  Mac is the input cap plus the kill.
- After 2 s (wall clock) the whole process group is killed with SIGKILL, before the child is reaped (so the group
  id cannot have been reused), then it is reaped.
- At most 8 MB of text goes in; files after that are not searched and the result says so.
- The pattern is case-insensitive, like plain search. A pattern that does not compile answers "That is not a valid
  regular expression: <reason>". A pattern stopped by the clock or the CPU limit answers that the search took longer
  than 2 seconds, was stopped, and to use a simpler pattern or plain text.
- It is not a command: the child runs fixed code that only reads JSON and runs `re`, so it needs no click and no
  sandbox profile. The model controls only the pattern string, and `re` cannot run code.

**The file map.** `vp.list_files` takes names and sizes from the folder walk (`os.stat`) instead of reading and
hashing every file. The names are the same: the same walk, the same skipped folders, the same 1 MB limit, a file
that cannot be read is still left out (`os.access`), and more than 200 files still raises the 413 that ends a run
(P-064). The per-step map, `list_files`, `search`, the failed-edit advice and the page's project info all use it.
Checkpoints still hash (`_scan` is unchanged).

**Prompt.** Two tool lines change and one explanation line is added; the system prompt grows by at most 120
estimated tokens (characters / 3.5, the agent's own estimate). `FORMAT_HELP` shows the new forms.

No new audit event: `vibe_agent_step` already records the tool name, success and time.

## Test plan

- Parser: flags before and after the text, grep spellings, ignored flags, short groups, a quoted flag-like text,
  count over files, errors.
- Glob: `src/**/*.js` returns only matching names; `**` with no folder; a slash-less glob in any folder; case;
  refused globs (`..`, `/x`, too long).
- Search: files-only and count with more than 40 matches answer no "narrow the search"; regex with a group, an
  invalid regex, regex under a glob path; secret files never sent to the child.
- Regex child: `(a+)+$` against a 100 KB line ends within 3 s with the message, the process group is gone, and
  `pgrep -f` finds no marker; with the wall clock lifted, `RLIMIT_CPU` alone stops it; the spawn uses the fixed argv,
  `env={}`, `stdin` data, `start_new_session` and the limits function; the limits function sets `RLIMIT_CPU` (and
  `RLIMIT_AS` where the system allows it).
- File map: on a 200-file project the map and `list_files` call no hash and do not call `_scan`; names equal the
  hashing scan's names on a project with skipped folders, a too-large file and an ignored name.
- Prompt growth measured against the pinned size of the old prompt.
- Live: `tools/vibe_agent_eval.py --bar --baseline <newest>` when the local model is free.

## Rollback

Revert the PR. Nothing is stored in a new format: the call arguments live only in memory and in the run log text,
and `list_files` returns the same shape.
