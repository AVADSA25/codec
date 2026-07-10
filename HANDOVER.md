# HANDOVER — CODEC buyer journey

**Last updated:** 2026-07-10 · session: buyer-journey audit + R1 "stop the lies" + R2 "open the store"

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

## What R2 shipped (2026-07-10)

The store has a door. **All four money defects were fixed BEFORE the button went in.**

| Fix | Evidence |
|-----|----------|
| Unpaid checkouts minted licenses (`payment_status` never checked) | fails closed now; mutation-tested |
| A failed delivery email was swallowed with a 200 — no retry, no alert, and any Stripe retry returned early **without re-sending** | retries 3x, logs `email_failed` + ALERT, raises so Stripe's 3-day backoff retries; the retry re-sends. Paid-but-undelivered self-heals |
| Refunds never revoked — `refunded` was a status no code could set | `charge.refunded` handled; **the live endpoint wasn't even subscribed to it** — subscribed |
| Year-2 renewal lockout: subscription bills forever, JWT died at 365 days | `invoice.payment_succeeded` + `billing_reason=subscription_cycle` re-mints, extends, emails new key. First invoice explicitly excluded |

- 24 tests pass (13 new, in `test_money_paths.py`). Each guard mutation-tested.
- Stripe Payment Link **`plink_1TrcDiAnpzAGXuyI2wymB1pR`** → https://buy.stripe.com/8x200i4M58xBfwrbMX6Vq0n
  Verified: live mode, recurring yearly, $99 USD, and the price **exactly matches** the one
  `_session_is_codec_purchase()` filters on. A purchase through it will mint.
- Buy button + honest terms (one Mac / one year / renews / key emailed immediately) on
  `site/codec.html`, replacing "Launching Q3 2026 · Notify me".
- `ava-license` restarted; `/health` ok; all guards confirmed present in the running process.

## THE ONE TEST STILL OWED

Nobody has ever completed a real CODEC purchase. I cannot — it needs a card. **Buy it once
with your own card** through the link above. Expected: key email arrives within a minute,
download button works. Then say the word and I will refund it via Stripe and verify the
licence flips to `refunded` — which also proves the refund-revocation path end to end.

## What R3 shipped (2026-07-10)

**The paid Mac app now actually starts the fleet — and the email points at the installer.**

- **Fleet start** (codec PR #235, merged). The app launched, logged "no services started",
  and exited — a stub. The engineering all existed (launchd generator, install script,
  first_run.py, all tested); three wires were unconnected: nothing called first_run;
  build_app.sh never bundled first_run.py/launchd; and the installer read the service list
  via `node` (a buyer has none). All fixed. Found by testing: every service's `cwd` was the
  build machine's repo path — would fail on a buyer's Mac; build now strips it and fails if
  any dev path survives. 23 new tests, each mutation-tested; full suite 2497 passed.
  Verified by building a real bundle and generating 15 valid LaunchAgents **with node removed
  from PATH**, all pointing at the bundled interpreter + Resources/app.
- **Email → installer** (ava-stack `fix/r1-license-email-dead-link`, `831fce0`). `CODEC_DMG_URL`
  pointed at `Sovereign-AI-Workstation-<v>.dmg` — the app-only *Sparkle update* DMG (and the
  May-25 stub). Now points at the INSTALLER via stable `/releases/latest/download/CODEC-Installer.dmg`.
  Live service restarted; config verified. Test asserts it's the installer, never the update DMG.
- **Stub guard** (same commit). `installer-gui/build-app.sh` now REFUSES to bundle a CODEC app
  that lacks `start_fleet` / `first_run.py` / `services.json` / `launchd`. Verified it passes a
  fresh app and rejects the May-25 stub. Last backstop before a broken app reaches a buyer.

## THE APPLE BLOCKER (only Mickael can clear it)

A signed **and notarized** installer cannot be produced until your Apple agreement is renewed.
`xcrun notarytool` (profile `ava-codec`) returns **HTTP 403 — "A required agreement is missing
or has expired."** The Account Holder must sign in to **developer.apple.com → Account → Review
Agreement** and accept the updated Apple Developer Program License Agreement. Everything else is
ready: Developer ID cert in keychain ✓, Swift 6.3 toolchain ✓, notary creds stored ✓.

### Release runbook (once the agreement is accepted)
Run on your Mac (Apple Silicon). ~15–40 min incl. Python bundling + two notarization round-trips.
```
# 1. Build + sign + notarize + staple the CODEC app (also emits the app-only update DMG + appcast)
~/codec-repo/packaging/macos/release_macos.sh \
    --identity 1A32571CF3EE6724531EC4C25AD7C7026626F28F --with-python

# 2. Build + sign + notarize + staple the INSTALLER, bundling that app (guard blocks a stub)
CODEC_APP="$HOME/dist/Sovereign AI Workstation.app" \
    ~/ava-stack/installer-gui/build-app.sh --sign
#   -> ~/ava-stack/installer-gui/dist/CODEC-Installer.dmg

# 3. Publish BOTH assets to a codec-updates release so the URLs resolve:
gh release upload v3.2.0 \
    "$HOME/ava-stack/installer-gui/dist/CODEC-Installer.dmg" \
    "$HOME/dist/Sovereign-AI-Workstation-3.2.0.dmg" --repo AVADSA25/codec-updates --clobber
```
Then the license email's download link (already live) resolves. Tell this session and I'll
verify the URL serves 200 and do the $99 end-to-end purchase test.

## BLOCKERS (need Mickael)

1. **The site changes are committed but NOT LIVE.** `AVA-site-v2` has no git remote and
   deploys by hand (static upload). Since we last looked the truthful /codec rewrite DID go
   live (fiction + client email gone — re-verified); what's still unpublished is the **Buy
   button** commit `52b31dd`. Hold it until R3 is buyer-testable (per the InTake session's
   advice, which I agreed with).
2. ~~R2 needs a money decision~~ — **done** (approved 2026-07-10). Payment Link created and
   wired. Still not visible to buyers until the site is republished (blocker 1).
3. **Concurrent session warning:** another Claude session is committing in `AVA-site-v2`
   (`fe39df2 "HANDOVER: final InTake SPA deployed"` landed between my two commits).
   Coordinate before working there.

## Next (R2 → R6, from the audit roadmap)

- ~~**R2 Open the store**~~ — SHIPPED, pending deploy + the one real test purchase.
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
