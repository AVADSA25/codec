# Session command approvals on the phone — design (P-060)

Status: approved 3 Nov 2026 (the owner on finishing the 25 Oct audit plan: "go go build i trust ur judgment here"). Built the same day.
Audit findings BH-2 (confirmed, high) and GAP-511 (part).

## Problem (checked 3 Nov 2026)

- The Terminal session (`codec_session.Session`) is its own `python3.13` process: `codec_agent.run_session_in_terminal` writes a launcher that imports `codec_session` and runs it in Terminal.
- Before a command runs, `_cmd_preview` and `_danger_preview` showed a tkinter dialog on the Mac and called `_register_remote_approval`, which wrote the request into `routes._shared._pending_approvals`. That dict lives in the memory of the session process. The dashboard has its own, empty copy, so the Inbox and `/api/approvals` never showed the request, an Allow from the phone could only get a 404, and the session waited 60 s and denied. It also wrote an Inbox report ("CODEC — Approval needed") with no buttons.
- `tests/test_inbox.py` fills the dict by hand, so the hop between the two processes was never tested.

## Design (one plan)

1. **Ask through `codec_ask_user`.** Both dialogs call `codec_ask_user.ask(question, options=["Allow", "Deny"], timeout=60, destructive=True, destructive_verb="allow", asked_from="session")` on a worker thread (`_PhoneApproval` in `codec_session.py`), in place of `_register_remote_approval`, `_check_remote_approval` and `_cleanup_approval`, which are deleted. ask_user keeps the question in `~/.codec/pending_questions.json`, a file every process reads: the Inbox lists it under Needs you with Allow and Deny buttons, ask() sends the content-free `approval` push, and the dashboard's `POST /api/agents/answer/{id}` writes the answer that ask() picks up within a second.
2. **Only the exact Allow label approves** (case and outer spaces ignored, as `codec_consent.mcp_http_consent_ok` does). ask()'s strict mode also lets through other replies that hold the word ("allow it please"), so they deny, like Deny, a typed refusal and the timeout.
3. **The Mac dialog stays** and decides beside the phone. It looks at the worker every 300 ms and closes on the phone's answer. A click records the Mac's answer on the same question (`submit_answer(..., answered_via="dialog")`), so the card leaves the Inbox and a later phone answer is refused (409). **The first answer wins, and the question record decides which was first**: if the phone's answer reached the record before the click did, the phone's answer stands. If both land in the same instant (`submit_answer` checks the status before it takes the file lock, so both can be written), the command runs only if both said Allow (`decided_by` `both`).
4. **ask_user switched off or failing turns the phone side off.** With `ASKUSER_ENABLED=false` nothing is asked; if ask() raises, nothing listens to the phone. Neither ever approves: the Mac dialog decides alone, as before this change, and with no click the command is denied when the dialog's clock runs out. This is how done-when (1) "ask_user disabled, timeout and any error deny" and (3) "the dialog still works when no phone answers" fit together.
5. **Timeout:** the question waits 60 s (the old dialog's time). The dialog's own clock runs 3 s longer, so ask_user records the timeout first.
6. **Correlation:** the worker sets `codec_audit._correlation_id_var` in its own thread, so `ask_user_question_emit`, `_answer` / `_timeout` and the new decision line share one `correlation_id`; the session finds its question by it.
7. **Unchanged:** the dialogs' look; without tkinter the preview still runs the command and the danger dialog still denies, and nothing is asked; `run_code`'s `command_flagged` / `command_approved` / `command_denied` lines; `routes/_shared._pending_approvals`, which `routes/approvals.py` and the Inbox still read until a separate removal; `codec_ask_user`, the routes and the page.

## Data and audit

- `asked_from` gets the value `session` and `answered_via` the value `dialog` (written only by the session process; the HTTP endpoint still accepts only `pwa` and `voice`). Both are existing fields: the audit envelope and `pending_questions.json` keep their shape.
- New event `session_approval_decision`, source `codec-session`: `decision` (`allowed`|`denied`), `decided_by` (`phone`|`mac`|`both`|`none`), `reason` (`allow`|`deny`|`reply`|`closed`|`timeout`), `phone` (`asked`|`disabled`|`error`), `dangerous`, `cmd_hash` (pairs with `command_flagged`), `pending_question_id`. Never the command text.
- The question holds the command and its explanation, so the phone can judge it. It is stored where every ask_user question is: `pending_questions.json`, `notifications.json`, and its first 200 characters in `ask_user_question_emit` (secret-redacted). The push carries only the kind.

## Files

Changed: `codec_session.py`, `codec_audit.py` (two comments), `docs/codec-architecture.md` (the new event and values).
New: this doc, `tests/test_session_phone_approvals.py`, `tests/session_tk_fake.py` (a stand-in for tkinter, so no test opens a window on the owner's screen).

## Tests

- Both dialogs ask with exactly the arguments above; the question shows the command; `routes._shared` is never imported.
- Only "Allow" approves: Deny, "allow it please", the timeout, ask_user off and ask() raising all deny.
- The dialog works with no phone answer (Allow and Deny clicks), closes the question, and the phone's later answer gets 409; a phone answer closes the dialog and a later click does nothing; a phone answer that reaches the record a moment before the click wins.
- Two processes: a child runs the session dialog with ask_user's files in tmp_path; the parent serves `codec_dashboard.app` under TestClient on the same files, finds the question in `/api/inbox` under Needs you with Allow, answers it through the POST endpoint, and the child returns Allow; Deny and the timeout deny; each outcome is in the child's audit log; the `approval` push is attempted with a payload that holds no command text (`codec_push._post` replaced).
- No test writes `~/.codec`, opens a real window or sends a push.

## Rollout

`codec_session` is imported fresh by each new Terminal session, so no service restart is needed: after the merge and a fast-forward of `~/codec-repo`, the next session uses it. A session already open keeps the old code until it is closed. Live check: in a new session ask for something that needs approval (for example "make a folder p060-test on my Desktop"), see the card in the Inbox and the push on the phone, tap Allow; tap Deny on a second; let a third run out; click Allow on the Mac for a fourth and see the card leave the phone.

## Rollback

Revert the commit; the next session uses the old code. Nothing to migrate: answered questions leave `pending_questions.json` after 24 h.
