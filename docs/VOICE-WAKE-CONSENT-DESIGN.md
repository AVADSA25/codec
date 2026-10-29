# Wake word and voice call ask before high-power skills (P-080, P-030 part 1)

Owner decision D-17 (decision board, 29 Oct 2026): "wake word and voice ask before the
six high-power skills", plus per-call consent for the browser and screen skills.

## What

- `codec_consent.local_consent_needed(skill, task)` decides; `local_consent_ok(skill,
  task, asked_from)` asks. One helper for the wake word (`asked_from="wake"`), the
  voice call (`"voice"`) and the chat consent check, not a second one.
- Asks for: every skill `is_destructive_skill` already names (terminal, pm2_control,
  process_manager, python_exec, ax_control, imessage_send, file_write, pilot, ...),
  except `file_ops` list and read (decided by the skill's own `_parse_action`); plus a
  separate local set `_LOCAL_CONSENT_REQUIRED` = chrome_fill, chrome_click_cdp,
  mouse_control. Never asks for weather, time, music, timer and the rest.
- The local set is NOT added to `_DESTRUCTIVE_BUILTINS`, `_HTTP_BLOCKED` or
  `_HTTP_CONSENT_REQUIRED`: `mcp_allowed` would then refuse those three over MCP,
  where they already ask per call. No section 10 list is edited.
- The question goes through `codec_ask_user.ask(destructive=True, timeout=60)`: the
  PWA card plus a phone push. Strict consent (P-078) applies. Decline, 60 s without an
  answer or any error cancels with `LOCAL_CANCELLED` and the skill's `run()` is never
  called. Each decision writes one `local_consent_decision` audit line (skill,
  asked_from, decision, task length; never the text).
- Wake word (`codec.py`): `run_skill(..., consent=_wake_consent)`; `run_skill` asks
  before each fall-through candidate. CODEC says "That needs your OK..." first. While
  a wake question waits, a second wake command is refused aloud instead of only
  being logged; the dispatch lock is free again within 60 s.
- Voice call (`VoicePipeline.dispatch_skill`): same notice, then the ask in an
  executor thread. A spoken answer is not taken: the call loop awaits this turn, so
  the next utterance is not read until the ask ends. The answer comes from the PWA
  card or the phone.
- Chat: `chat_consent_ok` also asks for the three browser and screen skills.

## Not in this PR

- P-030 part 2 (crew and custom-agent `shell_execute` in a macOS sandbox, which needs a
  `codec_sandbox` profile change) and part 3 (custom-agent tool list validation): a
  separate PR.
- P-087 (time limit on skill runs) is not built yet, so there is no limit to start
  after the ask.

## Cost

"Restart the dashboard", "text X", "kill Chrome" by voice or wake word now wait for a
tap. With no PWA open and no phone push answered, the action is cancelled after 60 s
(fails closed).

## Rollback

`git revert`. Wake and voice then run these skills without asking, as before.
Takes effect after restarts of `open-codec` and `codec-dashboard` (owner's action).
