# HANDOVER — CODEC buyer journey

**Last updated:** 2026-07-10 · session: buyer-journey audit + R1 "stop the lies"

## State

The audit is done and merged. **R1 is complete and verified.** Nothing further ships
without a decision from Mickael (see BLOCKERS).

- Audit report: [artifact](https://claude.ai/code/artifact/0736616b-12d5-4582-a96c-bc8b66e70779)
  · findings in `docs/audits/2026-07-10-CODEC-BUYER-JOURNEY-AUDIT.md` (PR #233, merged)
- 82 findings · 16 critical · 30/30 adversarially-verified confirmed, 0 refuted.

## What R1 shipped (all verified, not claimed)

| # | Fix | Where | Evidence |
|---|-----|-------|----------|
| 1 | License email's download button 404'd | `ava-stack` branch `fix/r1-license-email-dead-link` | now points at the real v3.2.0 DMG; live `ava-license` restarted, `/health` ok |
| 2 | Emoji in the license email subject | same | `test_license_email.py` asserts it stays clean |
| 3 | **Leak-guard test was dead** — `test_codec_purchase_guard.py` could not collect (no `stripe` in system py, no `pytest` in venv) | same | `.venv/bin/python -m pytest` → 11 passed |
| 4 | **`pytest` in license-server fired a LIVE webhook** — `test_webhook_live.py` was a script that minted a real license on import | same | renamed `check_webhook_live.py`; `pytest.ini` pins `python_files=test_*.py` |
| 5 | Client's email `dr@jansen.de` live in a code sample on avadigital.ai/codec | `AVA-site-v2` branch `fix/r1-remove-client-identity` | removed |
| 6 | avadigital.ai/codec sold a fictional TypeScript SDK (`npx codec init`, "Codec 0.9.4", 368 features, 940 tests, telegram/voice-sip/slack, `codec run --agent lucy`) | same branch | rewritten from verified facts: Python, v3.2, 86 skills, 400 features, 2,000+ tests, real skill sample. €500 cart preserved |
| 7 | README sold a `€10/month` plan that never existed; `Get it →` led to no purchase path | codec repo, PR #234 (merged) | now `$99/year`, `Details →` |
| 8 | FEATURES.md claimed 76 skills; manifest has 86 | PR #234 | corrected + guarded |
| 9 | Nothing checked the marketing numbers | PR #234 | `tests/test_public_claims_true.py` — skill count == manifest, version == VERSION, test-count claim backed by real tests, phantom €10/month can never return. Guard proven to bite. |

**Two audit findings I checked and rejected:** the Cmd+R / F5 hotkey copy is accurate
(`README.md:103`), and the install one-liner already contains `cd codec`. Both were
unverified low findings. Don't "fix" them.

## BLOCKERS (need Mickael)

1. **The site changes are committed but NOT LIVE.** `AVA-site-v2` has no git remote and
   deploys by hand (static upload). Until it is re-published, avadigital.ai/codec still
   shows the fictional SDK **and the client's email address**. This is the single most
   urgent open item.
2. **R2 needs a money decision** — creating the Stripe Payment Link and putting a Buy
   button live is outward-facing; not done autonomously.
3. **Concurrent session warning:** another Claude session is committing in `AVA-site-v2`
   (`fe39df2 "HANDOVER: final InTake SPA deployed"` landed between my two commits).
   Coordinate before working there.

## Next (R2 → R6, from the audit roadmap)

- **R2 Open the store** (2–3d): one Buy button wired to a Stripe Payment Link matching the
  price the license server filters on; state terms (1 Mac / 1 year / renews); check
  `payment_status` before minting; revoke on refund; alert+retry on email failure; fix the
  year-2 renewal lockout (subscription renews forever, JWT dies at 365 days). Then buy it
  yourself end-to-end and refund it.
- **R3 Deliver what's paid for** (3–5d): publish the installer where the email points; fix
  the app build that logs "no services started" and exits; fix the installer's permission
  target; unfreeze the update feed (frozen since May 25, 141 commits behind); self-serve
  license rebind.
- **R4 Make the license mean something** (2–4d): runtime revalidation (revocation is
  DB-only — the leaked license works until 2027); real hardware binding at run time;
  rate-limit validation; stop sending the JWT as a URL query param; admin mint/unbind.
- **R5 Separate the brands** (1–2d): move license infra off `lucyvpa.com` (a client's
  domain); make the sibling Stripe webhook fail closed; remove the legacy
  `onlyfriend-ai` webhook; split Resend sender identities.
- **R6 Measure the web** (1–2d): analytics (conversion is entirely unmeasured); 1MB
  hotlinked imgur logo; `www` doesn't resolve; page has no `h1`; make the repo the deploy
  source of truth (opencodec.org is hand-published from Replit, repo copy 3 months stale).

## Open threads

- opencodec.org's `<head>` still tells Google **v2.0 / 234 features / 60 skills / price $0**
  while its body says v3.2. A plain `curl` sees only the stale numbers. Not fixed — that
  site's source is stale relative to its Replit deploy (see R6).
- Decision taken 2026-07-10: **keep the Dr. Jansen Trustpilot testimonial and "Trusted by"
  card as-is**; only the code-sample email leak was removed.
