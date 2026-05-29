# PP-8 — Forensic audit trail

**Closes:** Pilot audit **P-12** (Pilot was invisible to any audit log). **Repo:** `~/codec/`.

## Fix
`pilot/audit.py` — `audit(event, **fields)` appends a JSON line `{ts, source:"pilot",
event, …}` to `~/.codec/pilot_audit.log` (0600). A SEPARATE log from the parent's
`~/.codec/audit.log` on purpose: avoids the parent's HMAC signing (PR-2E) + cross-process
flock (PR-4E) that Pilot (a separate repo) can't cleanly participate in. Never raises.

Wired emits: `skill_approved` / `skill_rejected` (skill writes — the highest-value forensic
events), `run_started` (autonomous run kickoff), `navigate` (browsing logged-in sites).

## Tests (`tests/test_phase14_audit.py`)
audit() writes parseable JSONL with ts/event/fields; never raises on a bad path;
approve_pending emits `skill_approved`. 3 tests.
