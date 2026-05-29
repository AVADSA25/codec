# PP-10 — Destructive-action default-deny

**Closes:** Pilot audit **P-7** (HITL default-deny for destructive actions) + **P-10**
(replay re-executes irreversible actions) — their default-deny core. **Repo:** `~/codec/`.

## What & Fix
An autonomous run (no human present) could click "Pay"/"Place order"/"Delete"/"Transfer"
on its own, and replay would re-execute such a click (e.g. order placed twice).

- `classify_destructive(action, el)` — True for a `click` whose element name/role reads as
  an irreversible/financial action (targeted verb list: pay/buy/place order/checkout/delete/
  transfer/withdraw/wire/authorize/confirm payment/…; deliberately NOT generic
  submit/search, to avoid over-blocking ordinary automations).
- `guard_action(action, el)` raises `DestructiveActionBlocked` by default; opt in with
  `PILOT_ALLOW_DESTRUCTIVE=1` (read live). Wired into the **agent loop** click branch.
- **Replay** blocks a destructive click with a `blocked_destructive` ReplayStep unless
  opted in — so re-running a recorded automation can't silently re-trigger a payment.

Richer HITL-approval (pause + per-action approve instead of hard-block) is the natural
follow-up; this is the safe default-deny floor.

## Tests (`tests/test_phase16_destructive_guard.py`)
classifier flags financial/delete clicks + ignores benign/non-clicks; guard blocks by
default, allows when opted in, allows benign. 5 tests; native test_phase5 replay still green.
