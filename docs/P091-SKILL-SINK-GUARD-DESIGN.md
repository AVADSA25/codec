# P-091: guard test for interpreter sinks in pinned skills

## What

A CI test, `tests/test_skill_sinks.py`, that reads every `skills/*.py` with
`ast` and fails when text reaches a command through formatting that does not
go through the P-007 helper (`codec_applescript`). Reviewed exceptions live in
`tests/skill_sink_allowlist.json`. No product code changes: no skill, no
registry, no manifest.

## Why

`skills/.manifest.json` pins each built-in skill's bytes, and a pinned skill
skips `is_dangerous_skill_code` when it loads (`SkillRegistry.load`). The pin
proves the bytes did not change, not that the code is safe. P-007 escaped the
osascript sinks known on 25 Oct; nothing stops the next one. A new
`f'tell application "{x}"'` line would ship behind a regenerated hash with no
automated review.

## The rule

A **sink** is a call to `subprocess.run / Popen / call / check_call /
check_output / getoutput / getstatusoutput`, `os.system / os.popen`, the
`os.exec*` and `os.spawn*` families, or `asyncio.create_subprocess_exec /
_shell` (module aliases and `from ... import` names included). Its command is
the first argument (all arguments for the exec/spawn families). For a command
that starts with `osascript`, `bash`, `sh` or `zsh`, the `input=` text (stdin)
counts too.

**Built text** is an f-string, `%` formatting, `.format()`, `+` or
`"sep".join(...)`. Each element of an argv list is checked, as is a list joined
to another list.

A **piece** of built text is safe when it is a literal (a conditional between
literals included), a call to `codec_applescript.escape` or
`codec_applescript.notification_script`, a call to a function in the same file
whose every `return` is one of those, or a name, list or nested string built
only from safe pieces.

**Following text:** a name is followed to every assignment, `+=` and `for`
loop that binds it in its function, enclosing functions and the module. A
parameter that reaches a command is followed to the calls of that function in
the same file (wrappers such as `def _osa(script)`). A piece that is a
parameter, an attribute, a subscript or any other call is not safe.

**Not covered** (stated in the test docstring and here): methods called
through `self`, text handed to a helper in another module, and values whose
safety depends on their type (an `int` in an f-string). The last kind goes in
the allowlist with that reason.

## Data: `tests/skill_sink_allowlist.json`

```json
{"_comment": "...", "sites": [{"file": "skills/volume.py", "line": "<stripped line text>", "reason": "..."}]}
```

The key is the file plus the stripped text of the line where the unsafe piece
enters the string (Python 3.11 to 3.14 report the same line for a piece inside
a multi-line f-string). Line text, not line number, so code moving above it
does not break the test; an entry covers every identical line in that file. A
reason of four words or more is required; the test also fails on duplicate and
stale entries, so the file cannot rot. Entries for sinks that are not safe are
marked `KNOWN GAP, follow-up` and listed in the PR.

## Output

The census test prints, even without `-s`: today's osascript call sites in
`skills/` and across the repo outside `tests/` (the audit's GP05-6 counted 71
in 34 files repo-wide), the number of sink calls in `skills/`, the pieces that
go through `codec_applescript`, and how many built lines are reviewed or new.
On a failure it prints, per new line, the call it reaches and the exact JSON
entry to add after review.

## Migration

None. The allowlist is written from today's tree (after P-007) with a reason
per line. When a parallel PR adds or changes a sink, its CI fails on this test
until the line is escaped or reviewed.

## Test plan

- The census test passes on today's tree and prints the counts.
- A temporary copy of the pinned `volume.py` (hash checked against the
  manifest) with one new `f'tell application "{app_name}" to quit'` osascript
  call produces exactly that one new finding.
- One case per rule: 15 snippets that must be flagged (inline, variable, `%`,
  `.format`, `+`, `+=`, join, command list in a variable, wrapper parameter,
  loop over built scripts, `os.system`, aliased module, `from` import, osascript
  stdin, escaped-then-changed) and 9 that must pass (inline and stored escape,
  imported alias, same-file wrapper, `notification_script`, literal-only
  pieces, plain argv items, constant script, f-string outside the command).
- The allowlist test: fields present, reasons given, no duplicates, no stale
  lines.

## Rollback

Delete `tests/test_skill_sinks.py` and `tests/skill_sink_allowlist.json`.
Nothing at runtime reads either file, so no service restart is involved.
