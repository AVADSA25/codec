# Flash text skips the shell-danger check (R-04)

Owner decision, board card R-04 (12 Nov 2026): "Drop the shell-danger check for
Flash chat text (keep it on every path that runs a command)."

## What was wrong

Home's Flash box (`codec_dashboard.html` `sendCmd`) posts to `POST /api/command`.
That route ran `codec_config.is_dangerous` on the text before anything else, and
the check treats a backtick or `$(` as shell command substitution. "Reply with
one inline `code` word" came back "Command blocked: matches a dangerous
pattern". The text never reaches a shell: the route sends it to the chat skill
matcher (`routes.chat._try_skill`) and then to the model. Deep Chat
(`/api/chat`) runs the same matcher with no such check.

## Change

- The Flash page sends `source: "flash"` in its `/api/command` body.
- `/api/command` skips `is_dangerous` only when `source == "flash"`. Every
  other source keeps the 403: the heartbeat's drafted tasks (`source:
  "heartbeat"`), a caller with no source (default `"pwa"`, so a tab opened
  before this change is still refused until it reloads), and any other caller.

## Why a client-chosen source is enough

Sending `source: "flash"` gives a caller what `/api/chat` already gives it: the
text goes to the same skill matcher and the model. The gates on the paths that
run a command are unchanged:

- the `terminal` skill runs `is_dangerous` on its own command and refuses;
- `_try_skill` asks for strict consent before a destructive skill
  (`codec_consent.chat_consent_ok`);
- the heartbeat checks a drafted task itself before posting it
  (`codec_heartbeat._is_dangerous`), and `/api/command` checks it again;
- `routes/vibe_exec`, `codec_vibe_runner`, `codec_session`, the crew shell and
  the Vibe agent's command rules are not touched.

## Tests

`tests/test_flash_shell_check.py`: a Flash request with a backtick and one with
`$(` reach a fake model; a heartbeat request and a no-source request with the
same characters get 403 and never reach it; the terminal skill still refuses
both and runs nothing; the Flash page sends `source: 'flash'`.

## Rollback

Revert the commit. No stored state, schema or config changes.
