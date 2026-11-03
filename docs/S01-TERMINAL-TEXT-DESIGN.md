# S-01: no outside text inside a Terminal `do script`

Follow-up of P-091 (#511). Skills only, plus one small root module.

## What

Three built-in skills put outside text into an AppleScript
`tell application "Terminal" to do script "..."` string, so a quote in that
text could end the AppleScript literal and then the shell quoting inside
Terminal, and run commands as the owner:

| Skill | Outside text | Today |
|---|---|---|
| `network_info` | Wi-Fi network name, api.ipify.org reply | Terminal script built on every call, MCP included |
| `translate` | the request text and the model's reply | local path, non-Latin targets |
| `file_search` | the request text and the found paths | local path |

After this change no outside text goes into an AppleScript source string or a
shell command line:

- **`translate` and `file_search`** write the text to a new private file and
  have Terminal `cat` it. The AppleScript holds only constant commands and the
  file and folder paths, each through `shlex.quote` and then
  `codec_applescript.escape`. The title lines and the translate "Press any key
  to close..." pause are unchanged (the title lines are written into the file
  ahead of the text, the pause stays in the command).
- **`network_info`** drops its Terminal window. The skill already returns the
  full text, and the window never opened: the script held `\033` inside an
  AppleScript literal, which does not compile (`osacompile`: "Expected '"' but
  found unknown token", -2741), so `osascript` failed silently behind
  `DEVNULL`. Nothing the owner sees changes.

`file_search`'s script had the same `\033` compile error, so its window never
opened either, although its reply says "opened in Terminal". With the title
line now in the file (no backslash escape in the AppleScript), the window opens
as the reply says. This is the one visible change.

## New module: `codec_terminal_text.py`

- `write_private(text, header="") -> (folder, path)`: a fresh folder from
  `tempfile.mkdtemp(prefix="codec_show_")` (0700) holding one file `text.txt`
  created with `O_CREAT | O_EXCL | O_NOFOLLOW`, mode 0600. `header` is the
  skill's own constant title (it may hold the colour escape); `text` goes
  through `clean()` first.
- `clean(text)`: control characters other than newline and tab become `?`, so
  outside text cannot send terminal escape sequences (colour, title, cursor
  moves that hide lines) to the window it is printed in.
- `remove(folder)`: deletes `text.txt` and the folder; never raises.
- `sweep()`: deletes leftover `codec_show_*` folders older than
  `LEFTOVER_SECONDS` (600): real folders this user owns that hold nothing but
  `text.txt`. Called by every `write_private`.
- `ROOT = None` means the system temp folder (the per-user `$TMPDIR` on
  macOS); tests point it at `tmp_path`.

**When the file goes:** the Terminal command deletes the file and its folder
right after `cat` prints it (`cat F; rm -f F; rmdir D`). If `osascript` cannot
start, the skill deletes them at once. If Terminal never runs the command, the
next `write_private` call deletes the folder once it is 10 minutes old.

The `osascript` call stays in each skill, so the P-091 guard
(`tests/test_skill_sinks.py`) keeps reading it in CI: the two path pieces count
as escaped through `codec_applescript`, and no allowlist entry is needed.

## Data and API

No config, schema, audit event, `codec_config` list, sandbox or identity
change. Skill return values are unchanged. `_show_in_terminal` in `translate`
now receives the body joined with real newlines (it is a file, not an
AppleScript literal).

## Migration

None. Built-in skills are hash-pinned: `skills/.manifest.json` is regenerated
in the same commit. The owner restarts `codec-dashboard`, `open-codec` and
`codec-mcp-http` to load the new skill code.

## Tests

`tests/test_skill_terminal_text.py`:

- per skill, hostile text (`'`, `"`, `\`, `$(touch X)`, backticks,
  `; rm -rf ~`, a newline then `end tell`, an escape sequence, and a 10 KB
  version) through the skill with `subprocess.Popen` / `subprocess.run`,
  `requests.get` and `codec_llm.call` faked: the hostile text is in no argv
  element of the `osascript` call (and of no call at all for
  `network_info`), only in the private file; the AppleScript literal decodes
  to a command that only `cat`s, removes that file and folder; the touch
  targets never appear. `file_search`'s query reaches `mdfind -name` as one
  argv item, which is the search itself (no shell).
- the module: modes 0700 / 0600, `clean`, `sweep` removes only old, owned,
  one-file `codec_show_*` folders, and a failed `osascript` start leaves no
  folder.
- `tests/test_skill_sinks.py` passes with the three KNOWN GAP entries removed
  from `tests/skill_sink_allowlist.json`.

`tests/test_mcp_all_tools.py` (macOS only, real skills) adds `file_search` to
its skip list next to `memory_search` and `clipboard`: its window now opens and
a suite run must not open Terminal windows.

## Rollback

Revert the commit and regenerate the manifest (the revert carries the old
manifest), then restart the three services.
