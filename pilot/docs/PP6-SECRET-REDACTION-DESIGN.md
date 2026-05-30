# PP-6 — Secret redaction in persisted traces

**Closes:** Pilot audit **P-13** (text typed into password/secret fields was stored verbatim
in `trace.json` + re-embedded in compiled skills). See
`codec-repo/docs/audits/PHASE-1-PILOT-AUDIT.md`.
**Repo:** `~/codec/` (Pilot).

## What & Fix

The agent recorded `type` actions with the raw `text` (passwords, tokens) into the durable
trace. `redact_typed_secret(action, el)` returns a copy of a `type` action with the text
replaced by `<redacted:secret>` when the target is a credential field — detected by input
`type="password"` or a secret hint (`password`/`token`/`otp`/`cvv`/`pin`/`ssn`/…) in the
element name/placeholder/HTML-name. It's applied at record time in the agent loop **after**
the live `type_xpath` (which still uses the real text), so only the persisted action is
redacted. Compiled skills therefore never carry the credential.

**Residual:** non-field-typed secrets (e.g. a token pasted into a generic search box) aren't
detected; and screencast frames may still capture credential screens (P-13 b — a separate
follow-up). Covers the common password/token-field case.

## Tests (`tests/test_phase12_secret_redaction.py`, pytest, no browser)

password-input redacted, secret-named field redacted, normal field untouched, non-type
action untouched. 4 tests.
