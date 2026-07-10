# CODEC — Buyer-Journey Deep Review (verified audit)

**Date:** 2026-07-10 · **Method:** 7 parallel scoped reviewers + 30 adversarial verifiers (37 agents, 878 tool calls, ~26 min) · **Scope:** public face + full buyer journey (opencodec.org, avadigital.ai/codec, ava-license server, delivery, install, first run) — NOT the desktop app internals.

## Executive verdict

**CODEC the open-source project is real and verifiable. CODEC the business does not exist yet — and the one page that can take money sells a product that was never built.**

The OSS claims on opencodec.org check out against the repo (v3.2, 76 skills, 400 features, 2,000+ tests — all verified). But the €25/€99 license has **zero public purchase path**; the live license server has minted 4 licenses ever, all revoked (3 self-tests + the leaked InTake one). avadigital.ai/codec — the only page wired to a cart — describes a fictional TypeScript SDK ("Codec 0.9.4", `npx codec init`) and embeds a client identity in its code sample. And a buyer who somehow pays receives an email whose download button **404s**, pointing at an installer that was **never published**, which installs an app build that is currently **a stub that exits on launch**, on an update feed **frozen since May 25** while main shipped 141 fixes.

Verification: **30 of 30 adversarially-checked critical/high findings CONFIRMED, 0 refuted** (11 more crit/high unchecked due to the 30-verification cap, marked unverified).

## Scoreboard

| Area | Verdict | Findings |
|---|---|---|
| 1. POSITIONING & COPY | **half-built** | 12 (3 crit · 3 high) |
| 2. PURCHASE & ORDER FLOW | **half-built** | 11 (3 crit · 2 high) |
| 3. LICENSE LIFECYCLE (ava-license service + codec app client) | **half-built** | 12 (2 crit · 5 high) |
| 4. DELIVERY -> FIRST RUN | **broken** | 13 (3 crit · 4 high) |
| 5. Cross-product hygiene (CODEC vs InTake vs AVA separation) | **half-built** | 12 (1 crit · 5 high) |
| 6. WEB QUALITY HOLISTIC (opencodec.org live + source) | **half-built** | 12 (1 crit · 4 high) |
| 7. JOURNEY SEAMS (land → understand → buy → pay → license → download → install → first run → updates → support) | **broken** | 10 (3 crit · 2 high) |

**Totals: 82 findings — 16 critical · 25 high · 26 medium · 15 low.** Kinds: 15 copy-lie · 13 island · 10 broken · 7 facade · 5 half-built · 18 risk · 14 ux.

## The three patterns (CODEC's version of InTake's disease)

1. **A live backend selling nothing.** The full revenue pipeline (Stripe webhook → key mint → email) runs in production — and no public page links to it. The only money link on opencodec.org is a PayPal donation; the $99 tier on avadigital.ai says "Q3 2026 · Notify me". The store has no door. *(POS-02, PF-01, LIC-1, WEB-01, JS-01)*
2. **Four public surfaces, four different products.** opencodec.org body says v3.2/free; its own `<head>` says v2.0/234 features; the README sells €10/mo–€99/yr at a page that doesn't sell it; avadigital.ai/codec sells a fictional TypeScript agent SDK with fabricated stats — plus client identity (dr@jansen.de, "Dr. Stephan Jansen", clinic URL) leaking into CODEC/AVA public assets. *(POS-01/-06/-07, PF-03, LIC-7, XP-6/-10, JS-03/-05, WEB-02)*
3. **After payment, every seam dead-ends.** Download button → 404. Installer DMG → never published. Shipped app build → stub that exits. Update feed → frozen 6+ weeks behind main. Revocation → never re-checked by the client (the leaked license still works until 2027). Renewal → charges forever, key dies at 365 days. *(POS-03, D1–D7, LIC-3/-4, PF-04, JS-02/-06/-07)*

## The automation gap — every step that today requires Mickael manually

1. **Every sale**: no public buy button — manually create + send a Stripe checkout link.
2. **Every delivery**: license email's download button 404s — manually send a working DMG link.
3. **Every release**: build, sign, notarize, hand-upload DMG + appcast (feed hand-fed; frozen since May 25).
4. **Failed license emails**: no retry, no alert — manually call `/admin/resend/{id}` with the admin token.
5. **Refunds**: webhook never revokes — manual DB surgery per refund.
6. **Machine transfer**: no unbind endpoint — buyer with a new Mac dead-ends at 409 until manual fix.
7. **Copy consistency**: 4 surfaces × stats/pricing kept in sync by hand (currently 4 different stories).
8. **Site deploys**: opencodec.org is hand-published from Replit; the local repo is 3 months stale.
9. **€500 Workstation Setup**: fully manual fulfilment ("kickoff within 24 hours" promised).
10. **Waitlist**: "Notify me" → generic contact form → manual tracking.
11. **Support**: different unverified address on each surface — every stuck buyer becomes a Mickael email.

## Sprint roadmap

| # | Sprint | What ships | Size |
|---|---|---|---|
| R1 | **Stop the lies** | Fix the license-email 404 (point `CODEC_DMG_URL` at the real release asset; test via `/admin/resend`). Take down or rewrite the fictional avadigital.ai/codec content. Remove client identity from all public assets. Reconcile pricing to ONE true story everywhere (README, site head+body, JSON-LD $0 claim). Fix false commands in docs (`codec search`, `--list-skills`). | 1–2 d |
| R2 | **Open the store** | One public Buy section wired to a Stripe Payment Link for the exact price the license server filters. State terms (1 Mac / 1 year / renewal). Fix `payment_status` check, refund-revocation, email-fail alert+retry, year-2 renewal lockout. One live test purchase end-to-end + refund. | 2–3 d |
| R3 | **Deliver what's paid** | Publish the installer properly; fix the stub app build (it exits on launch); fix installer permission-target bug; wire the cloud-first LLM config; un-freeze updates (ship 3.2.x with the 141 commits); fix the OSS `--update` path; self-serve license rebind. | 3–5 d |
| R4 | **Enforce the license** | Runtime revalidation (heartbeat) so revocation works; real hardware binding at runtime; rate-limit + POST the JWT; admin mint/unbind endpoints; decide the paid-gating story (feed DMG is public + unlocked today). | 2–4 d |
| R5 | **Separate the brands** | Move license infra off lucyvpa.com → codec.avadigital.ai; fail-closed sibling webhook; remove legacy onlyfriend-ai webhook; split Resend sender identities; fix LUCY default sender; no-emoji sweep in transactional email. | 1–2 d |
| R6 | **Measure & polish the web** | Analytics (conversion is unmeasured); perf (1MB imgur logo, uncompressed bundles); www DNS; real `h1`; SEO/OG/sitemap fixes; make the repo the deploy source of truth (kill hand-Replit-publish); favicon; pinch-zoom. | 1–2 d |

---

# Full findings by area

Legend: severity · kind · verification (✓ = independently confirmed by an adversarial verifier instructed to refute; "unverified" = not checked (verification capped at 30) — treat as probable but unconfirmed).


## 1. POSITIONING & COPY — verdict: **half-built**

The OSS story on opencodec.org is mostly true and current (live body says v3.2 · 76 skills · 400 features · 2,000+ tests — all verified against codec-repo: VERSION=3.2.0, FEATURES.md, 2,044 test functions, 12 crews, AES-256-GCM, live YouTube demo, 101 GitHub stars). But the COMMERCIAL story is a wreck: the intended brand "Sovereign AI Workstation" appears 0 times on opencodec.org (title says "Open-Source AI Command Layer"; the €25/€99 license, its terms (1 Mac, 1 year, auto-renew) and any buy button appear on NO public page — the only money link on opencodec.org is a PayPal donation — while the license server runs live in production with 0 real customers (4 licenses in DB, all revoked: 3 self-tests at $99 USD, 1 leaked €25 InTake). The license email's download button 404s. Meanwhile avadigital.ai/codec — the page wired to an actual Stripe cart — describes a FICTIONAL product ("Codec 0.9.4", "npx codec init", TypeScript agent SDK, 368 features/75 skills/940+ tests, telegram/voice-sip layers) and embeds client identity dr@jansen.de in its code sample. Four public surfaces carry four contradictory sets of version/stats/pricing. 30-second test: a stranger understands the free OSS tool instantly, but has no path from "buy" to anything.

**Manual-Mickael steps in this area:**
- Selling a CODEC software license today: no public buy button exists on any page, so every sale requires Mickael to manually create and send a Stripe checkout link for the CODEC price ID.
- After any license purchase, Mickael must manually send the buyer a working DMG link — the license email's 'Download CODEC.dmg' button points to https://avadigital.ai/codec/download which returns 404.
- Publishing each DMG build is manual: run /Users/mickaelfarina/ava-stack/installer-gui/build-app.sh, sign/notarize, then hand-upload the DMG + appcast.xml to the AVADSA25/codec-updates GitHub release (main codec repo releases carry zero binary assets).
- Re-sending failed or missed license emails requires Mickael to call POST /admin/resend/{license_id} on the license server with the admin bearer token.
- opencodec.org content is edited in Replit, not the local repo at /Users/mickaelfarina/Documents/Claude/Projects/ava-web-template — Mickael must manually keep the local tree, the deployed body, and the stale <head>/JSON-LD in sync (they currently diverge three ways).
- Keeping stats consistent (version, skills, features, tests, MCP tool count) across README badges, FEATURES.md, opencodec.org and avadigital.ai/codec is a manual sweep — currently four different sets of numbers are public.
- Fulfilling the €500 'Codec — Workstation Setup' cart purchase on avadigital.ai/codec is a hands-on Mickael service ('kickoff within 24 hours' is promised on the page).
- 'Notify me' for the $99/yr 'CODEC for Mac' tier routes to the generic homepage contact form (index.html#contact) — Mickael must manually track and re-contact that waitlist.

### POS-01 · CRITICAL · copy-lie · ✓ CONFIRMED
**avadigital.ai/codec sells a fictional product — TypeScript SDK, npx installer, fabricated stats**

This is the page wired to a real Stripe cart (€500 'Workstation Setup', data-cart-add='codec', site/cart.js). A buyer researches a TypeScript agent framework that does not exist, then pays €500 — guaranteed expectation mismatch, refund risk, and trust damage if anyone diffs the page against the public GitHub repo.

**Fix:** Rewrite avadigital.ai/codec from the real product: reuse the (verified-true) opencodec.org v3.2 copy — Python, 76 skills, 400 features, 9 products, DMG install. Delete the npx terminal, the reception.ts sample, and the 368/940/58K stat block; replace with README-consistent numbers.

**Evidence:** `Live https://avadigital.ai/codec shows terminal 'Codec 0.9.4' + '$ npx codec init' + '✔ Installed 75 skills · 368 features across 8 domains', a 40-line 'agents/reception.ts — TypeScript · Codec 0.9.4' code sample ('import { Agent, Skill } from \'codec\'', voice model 'eleven/jenny'), stats '368 Features / 75 Skills / 940+ Tests / 58K+ Lines TypeScript + Python', and an architecture with 'telegram / voice-sip / slack' interface layers. Source: /Users/mickaelfarina/Documents/Claude/Projects/AVA-site-v2/AVA Digital Design System/site/codec.html:809 ('Codec 0.9.4'), :812 ('npx codec init'), :814, :855-876, :883, :913-915, :1214-1215, :1227. Shipped truth: /Users/mickaelfarina/codec-repo is a Python 3.10 app, VERSION=3.2.0, FEATURES.md:3 says '400 features · 76 skills · 2000+ tests'; no npm package, no TypeScript SDK, no telegram/SIP interfaces exist in the repo.`

### POS-02 · CRITICAL · island · ✓ CONFIRMED
**Paid license has a live backend but zero public surface — no page sells or even mentions it**

Zero real customers can exist: there is literally no journey from any public page to the checkout that the license server listens for. The audited '€25 license' business does not exist publicly — revenue path is dead on arrival.

**Fix:** Add a Buy section to opencodec.org (or a working avadigital.ai/codec#buy) with a Stripe Payment Link for the CODEC STRIPE_PRICE_ID, stating exactly: price, 1 Mac, 1 year, auto-renew, what the paid build adds over OSS. One page, one button.

**Evidence:** `License server is live (/Users/mickaelfarina/ava-stack/license-server/main.py:1-13: Stripe webhook → mint → email, hardware-bound via /api/v1/activate main.py:119-128, 1-year expiry). But no public page mentions the software license, its price, or terms: opencodec.org's only monetization link is 'Support ❤️' → paypal.me/avadsa25 (live bundle footer; local App.tsx:1856); avadigital.ai/codec's paid tier says 'CODEC for Mac $99 /year … Launching Q3 2026 · Notify me' (link = index.html#contact, codec page text lines 211-221 of extracted copy). licenses.db: 4 licenses, ALL revoked — 3 self-tests at 9900 USD cents, 1 leaked €25 InTake license (the 2026-07-07 incident).`

### POS-03 · CRITICAL · broken · ✓ CONFIRMED
**License email's 'Download CODEC.dmg' button 404s — buyer dead-ends after paying**

Every future paying customer receives a receipt email whose Step 1 is a dead link — they cannot install what they bought without emailing Mickael. Money taken, journey broken.

**Fix:** Point CODEC_DMG_URL env at the codec-updates release asset URL (or create a 301 at avadigital.ai/codec/download → that asset). Then send yourself a test license via /admin/resend and click the button.

**Evidence:** `/Users/mickaelfarina/ava-stack/license-server/email_sender.py:35-37 renders 'Download CODEC.dmg' → CODEC_DMG_URL; config.py:27 defaults it to https://avadigital.ai/codec/download which returns HTTP 404 (curl verified 2026-07-10). The real DMG exists elsewhere: github.com/AVADSA25/codec-updates release v3.2.0 asset 'Sovereign-AI-Workstation-3.2.0.dmg' (129,562,244 bytes).`

### POS-04 · HIGH · copy-lie · ✓ CONFIRMED
**Client identity in public assets: dr@jansen.de in /codec code sample, client-jansen.jpg + clinic URL on homepage**

Direct violation of the brand rule 'never name clients in public assets'. A client (Dr. Jansen — Lucy's owner) is identifiable from the CODEC sales page and homepage; GDPR/professional-relationship risk and it crosses product contexts (InTake client on a CODEC asset).

**Fix:** Replace the sample email with owner: 'you@example.com' (site/codec.html:1230), rename the asset/key ('client-dental-de'), drop the outbound clinic URL, keep 'Dr. J.' only with the client's written consent.

**Evidence:** `site/codec.html:1230 code sample: calendar({ provider: 'google', owner: 'dr@jansen.de' }) — live page serves it Cloudflare-obfuscated (data-cfemail decodes to dr@jansen.de). Live https://avadigital.ai homepage 'Trusted by' data: { key: 'jansen', name: 'Dr. J.', cat: 'Dental Clinic · Germany', img: '../assets/client-jansen.jpg', url: 'https://jansen-gelnhausen.de/' } — the marquee links to the client's own site.`

### POS-05 · HIGH · island · ✓ CONFIRMED
**opencodec.org claims 'ships as signed, notarized DMG — DMG for quick start' but offers no DMG link anywhere**

The advertised 'quick start' path is unreachable: a stranger told 'DMG for quick start' cannot find any DMG from the landing page or the main repo — the flagship distribution claim is a journey island.

**Fix:** Add a 'Download DMG' button on opencodec.org hero + quickstart pointing at github.com/AVADSA25/codec-updates/releases/latest, and attach the DMG (or a link) to the main repo's release notes.

**Evidence:** `Live bundle (https://opencodec.org/assets/index-CWlSL47R.js): 'v3.2 ships as a signed, notarized macOS .app / DMG — not just a git clone' and '→ Both paths supported — DMG for quick start, git for power users'. The page's only external hrefs are the GitHub const, https://avadigital.ai and https://paypal.me/avadsa25 — zero links to any .dmg. Main repo github.com/AVADSA25/codec has 11 releases, all with empty assets; the DMG only exists in the separate codec-updates repo nobody is pointed to.`

### POS-06 · HIGH · copy-lie · ✓ CONFIRMED
**Pricing story contradicts across four surfaces: €10/mo–€99/yr 'get it now' vs $99/yr 'Q3 2026' vs 'completely free' vs $99 actually charged**

A buyer who follows README's 'Get it' lands on a page that says the app doesn't launch until Q3 2026 — while structured data tells Google the product costs $0 and is 'completely free', undercutting any future paid pitch. Currency flip-flop (€ vs $) reads as unserious.

**Fix:** Pick ONE price+currency+availability statement. Fix README.md:837 to match reality ('paid Mac app launching Q3 2026 — join the list' or make it purchasable now), and rewrite the opencodec.org FAQ entry to 'The engine is free & MIT; a paid convenience app/license is available' once the buy page exists.

**Evidence:** `/Users/mickaelfarina/codec-repo/README.md:837: 'Paid Mac app — €10/month or €99/year… [Get it → avadigital.ai]' (present tense, euros). Live avadigital.ai/codec: 'CODEC for Mac $99 /year … Launching Q3 2026 · Notify me' (dollars, not purchasable). Live opencodec.org JSON-LD (index.html:60-66): price '0' USD; FAQ (index.html:121-127): 'Is CODEC free? Yes. CODEC is completely free and open-source under the MIT license.' license-server/config.py:20 comment: '$99/yr license'; licenses.db test charges: 9900 usd.`

### POS-07 · MEDIUM · copy-lie · unverified
**Live opencodec.org <head> contradicts its own body: v2.0/234 features/60 skills in meta vs v3.2/400/76 on page**

Google snippets, social cards (OG/Twitter also say 'v2.0'/'free & open source') and AI crawlers all quote three-versions-old numbers; anyone comparing snippet to page sees the site contradicting itself within one URL.

**Fix:** Update title/meta/OG/JSON-LD in the deployed index.html to v3.2.0, 400 features, 76 skills, 70 MCP tools — and put 'Sovereign AI Workstation' in the title while you're in there.

**Evidence:** `Live https://opencodec.org index.html:8-9: title 'CODEC — Open-Source AI Command Layer for macOS' + meta description 'CODEC v2.0 — free, open-source… 234 features, 60 skills, 12 autonomous agent crews'; JSON-LD index.html:65 softwareVersion '1.5.0', featureList 'MCP server with 43 tools'. The rendered body (bundle) says 'v3.2 · 9 Products · 76 Skills · 400 Features · 2,000+ Tests', 'MCP Server — 70 tools', footer 'v3.2.0'.`

### POS-08 · MEDIUM · risk · unverified
**Local 'source of truth' for opencodec.org is stale (v2.0-era) — deployed site is edited elsewhere**

Any fix made in the repo listed as the site source would silently roll the live site back three versions; the real source lives only in Replit, so the site is one bad publish away from regression and unauditable in git.

**Fix:** Pull the current Replit project state back into ava-web-template (or make the repo the deploy source), then delete/mark the stale tree so no one edits v2.0 copy again.

**Evidence:** `/Users/mickaelfarina/Documents/Claude/Projects/ava-web-template/sources/opencodec.org/artifacts/codec-landing/src/App.tsx:753-758 renders badges 'v2.0 / 60 Skills / 234 Features', footer :1860 'v1.5.0', and has no Pilot/Project/DMG sections — while the deployed bundle at opencodec.org serves v3.2 copy with Pilot (num 08), Project (num 09), Ed25519 updater sections. The deployed bundle even differs in detail ('Qwen 3.6' vs local 'Qwen 3.5', bundle hash index-CWlSL47R.js).`

### POS-09 · MEDIUM · ux · unverified
**Product brand 'Sovereign AI Workstation' appears zero times on opencodec.org; three surfaces use three names**

The 30-second test half-fails: a stranger on opencodec.org never meets the product name Mickael actually sells under; searching 'Sovereign AI Workstation' will find the DMG filename and avadigital.ai but not the flagship site — brand equity split three ways.

**Fix:** One naming decision, applied everywhere: 'Sovereign AI Workstation — powered by the open-source CODEC engine' in the opencodec.org title/hero/JSON-LD and the GitHub repo description.

**Evidence:** `grep 'Sovereign' over the live opencodec.org bundle = 0 matches; hero: 'Open-Source Intelligent Command Layer for macOS'. GitHub repo description: 'Open-Source Intelligent Command Layer'. Meanwhile README.md:5-7: 'Sovereign AI Workstation — the product brand… CODEC is the engine', and avadigital.ai/codec hero: 'Codec is the Sovereign AI Workstation'.`

### POS-10 · MEDIUM · copy-lie · unverified
**Marketplace commands on opencodec.org don't exist: 'codec search / codec install' has no binary**

Users copy the advertised one-command install and get zsh 'command not found: codec' — the exact 'broken step' the bar forbids, on a flagship v2.0 feature.

**Fix:** Either ship a tiny 'codec' wrapper script in install.sh (one-line dispatch to codec_marketplace.py) or change the page copy to the python3 form.

**Evidence:** `Live bundle marketplace section: 'codec search pomodoro' and 'codec install bitcoin-price' (also local App.tsx:1334-1336). Actual CLI per /Users/mickaelfarina/codec-repo/codec_marketplace.py:6-7: 'python3 codec_marketplace.py install <skill-name>' / 'python3 codec_marketplace.py search <query>'; install.sh creates no 'codec' executable or alias (grep 'alias codec|bin/codec|ln -s' = none).`

### POS-11 · LOW · broken · unverified
**Copy-pasteable one-liner on opencodec.org fails: missing 'cd codec'**

The 'Also available via terminal' path errors with 'no such file or directory' for anyone who pastes it — minor but it's an install command on a product page.

**Fix:** Change to 'git clone https://github.com/AVADSA25/codec.git && cd codec && ./install.sh' in the deployed page source.

**Evidence:** `Live bundle, DMG section example: 'git clone https://github.com/AVADSA25/codec.git && ./install.sh' → './install.sh' runs in the parent dir, not the cloned repo (correct 3-line version appears earlier on the same page: clone / cd codec / ./install.sh).`

### POS-12 · LOW · copy-lie · unverified
**avadigital.ai/codec hotkey copy is wrong ('Hold Cmd+R', 'F5 live typing') and 'free' saturates client-facing sales copy**

A user who 'holds Cmd+R' refreshes their browser instead of dictating — the page teaches a broken gesture for the product's signature feature; 'free' phrasing breaches the house style rule on the most commercial page.

**Fix:** Correct to 'hold the right ⌘ key' and align F-key claims with the shipped defaults; swap 'Free' for 'open-source (MIT)' phrasing per brand rules.

**Evidence:** `site/codec.html:10 and :788: 'Hold Cmd+R, speak, paste. Press F5 for real-time live typing.' Actual trigger is holding the RIGHT ⌘ key — pynput Key.cmd_r in /Users/mickaelfarina/codec-repo/codec_dictate.py:456,471 ('cmd_r' = right command, not Cmd+R, which is browser refresh); F-keys per opencodec.org are F13/F18/F16 (F5/F8/F9 laptop mode). Same file uses 'Free' repeatedly in sales copy (:788 'Free. MIT-licensed.', tier card 'CODEC OSS — Free', :834 'Free. MIT-licensed. No subscription.') — the brand rule says the word 'free' never appears in client copy.`


## 2. PURCHASE & ORDER FLOW — verdict: **half-built**

The purchase flow is two disconnected halves. HALF A (works): avadigital.ai sells "Codec — Private AI Workstation (Setup)" €500 one-time via a client cart -> /bff/cart/checkout -> Stripe (server-side prices, live pages /checkout, /pricing, /order/thanks all 200) and a genuinely hardened Pages webhook (signature+replay window, payment_status=='paid' gate, idempotent on event id, buyer confirmation email + internal alert, dispute logging) — but fulfillment is "we'll reach out within 24 hours", i.e. manual install. HALF B (island): the actual product license — Stripe "CODEC License — Standard", $99 USD/year recurring, LIVE and active — is minted by the pm2 ava-license server (verified up: env=production, public https://ava-license.lucyvpa.com healthy, unsigned webhook POST correctly 400s, all 11 required env keys present). The 2026-07-07 price-filter guard is REAL, fail-closed, and I ran its 6 tests live: 6/6 pass; production events show it already skipped 2 non-CODEC checkouts. But NOTHING sells that license: opencodec.org says free/MIT/price:0 with only GitHub+PayPal links, avadigital.ai/codec's $99 tier says "Launching Q3 2026 · Notify me", and the €500 cart product is a different price id in mode=payment which the license server explicitly skips. Even if a license were sold manually, the email's "Download CODEC.dmg" button 404s (DMG is built on disk but hosted nowhere), and year-2 renewals silently expire the JWT. Note: the audit brief's "€25 license" premise is wrong — €25/mo is Lucy/InTake pricing; the CODEC license in Stripe is $99/yr. Licenses ever issued: 4, all revoked (incl. the incident leak) — zero active paying customers today.

**Manual-Mickael steps in this area:**
- Selling the $99/yr CODEC license at all: no buy button exists on any public page — Mickael must manually create a Stripe Checkout session or payment link (subscription mode, the exact STRIPE_PRICE_ID) and send it to each buyer
- Delivering the app: the license email's download link 404s and CODEC-Installer.dmg (ava-stack/installer-gui/dist/) is not hosted anywhere — Mickael must send the DMG to each buyer by hand
- Failed license emails: webhook returns 200 even when Resend fails, Stripe never retries, no alert is sent — Mickael must notice the error row in licenses.db events and call POST /admin/resend/{license_id} with the admin token
- Refunds: license server has no charge.refunded handler — Mickael must refund in the Stripe dashboard AND manually POST /admin/revoke/{license_id}
- Year-2 renewals: subscription auto-renews but no code re-mints or extends the JWT — Mickael must manually mint and email a fresh key to every renewing subscriber
- €500 Workstation Setup fulfillment: personally reach out within 24h, perform the on-site/remote install, then activate the buyer's pending_provision entitlement in /console/tenants
- Disputes: charge.dispute.created is only console.error-logged in the Pages webhook — manual review in the Stripe dashboard

### PF-01 · CRITICAL · island · ✓ CONFIRMED
**The CODEC license product has zero purchase path — entire license pipeline is an island**

A stranger cannot buy the product CODEC's production licensing backend sells — every license sale requires Mickael to hand-craft a Stripe checkout link. The 'paid macOS product' has no self-serve revenue path at all.

**Fix:** Either wire a real buy CTA (Stripe Checkout link for STRIPE_PRICE_ID, subscription mode) into avadigital.ai/codec and opencodec.org, or consciously mark the license pipeline pre-launch and align all copy to Q3 2026.

**Evidence:** `Stripe (live): price_1TOJ1NAnpzAGXuyIdpUKHtZe = 'CODEC License — Standard', $99/yr recurring, active, livemode:true; pm2 'ava-license' online serving it. But grep of both site sources for 'buy.stripe.com|price_1TOJ1N' returns nothing; live https://opencodec.org has only GitHub/PayPal links (JSON-LD: '"price": "0"'); live https://avadigital.ai/codec $99 tier CTA is 'Launching Q3 2026 · Notify me' (span[data-i18n="codec.mac.launch_note"]). The only purchasable item, cart id 'codec' (functions/_lib/catalog.js:47-51, price_1TgX0D..., recurring:false), produces mode=payment which the license server skips: stripe_handler.py:197 'if obj.get("mode") != "subscription"' plus a different price id fails the guard.`

### PF-02 · CRITICAL · broken · ✓ CONFIRMED
**License email's 'Download CODEC.dmg' button 404s — buyer pays, cannot install**

Every buyer who receives a license email hits a dead download link at Step 1 — journey dead-ends and they must email Mickael. Buyer lost money until manual rescue.

**Fix:** Host CODEC-Installer.dmg (Cloudflare R2/Pages asset) and add a /codec/download redirect, or point CODEC_DMG_URL at a working URL; add a smoke check that the URL returns 200.

**Evidence:** `config.py:27 + production .env: CODEC_DMG_URL=https://avadigital.ai/codec/download -> verified 'HTTP/2 404' (curl -I). email_sender.py:35-38 renders that URL as the Step-1 download button; admin /admin/resend re-sends the same dead link. A built installer exists at /Users/mickaelfarina/ava-stack/installer-gui/dist/CODEC-Installer.dmg but no /codec/download route exists in the site's _redirects.`

### PF-03 · CRITICAL · copy-lie · ✓ CONFIRMED (verifier adjusted severity → high)
**README sells 'Paid Mac app — €10/month or €99/year — Get it → avadigital.ai' — product doesn't exist there**

Public claim on the shipped repo is false today: a reader clicking 'Get it' finds nothing to buy, and the advertised monthly plan and EUR pricing don't exist anywhere — trust damage plus a guaranteed support email.

**Fix:** Change README to match reality ('$99/year, launching Q3 2026 — join the list at avadigital.ai/codec') or ship the buy path first; pick one currency.

**Evidence:** `/Users/mickaelfarina/codec-repo/README.md:837: '**Paid Mac app — €10/month or €99/year.** A signed, notarized, one-click install... [Get it → avadigital.ai](https://avadigital.ai)'. Reality: avadigital.ai/codec says 'Launching Q3 2026 · Notify me'; Stripe has exactly one CODEC license price — $99 USD/year (no €10/month price exists; license server config holds a single STRIPE_PRICE_ID).`

### PF-04 · HIGH · broken · ✓ CONFIRMED
**Year-2 renewal lockout: subscription renews forever but the license JWT dies at 365 days**

A customer whose card is charged for year 2 gets a dead key: activation/heartbeat 401s and the client drops to readonly ('paid build not activated') while Stripe keeps billing them — money taken, product locked.

**Fix:** On invoice.payment_succeeded for a known subscription, re-mint the JWT with a new exp, update expires_at/jwt_token in the DB, and email the fresh key (idempotent per invoice id).

**Evidence:** `licenses.py:81-82 mints exp = now+365d; stripe_handler.py:164-183 handles invoice.payment_succeeded by only flipping status ('active') — expires_at is never extended and no new JWT is minted or emailed. email_sender.py:51-53 promises 'expires in 1 year. Your subscription renews automatically unless you cancel it.' licenses.validate() (licenses.py:69-78) rejects expired tokens, and codec_license.py validates exp offline against the cached public key.`

### PF-05 · HIGH · ux · ✓ CONFIRMED
**Monetization story contradicts itself across every public surface**

Fails the 30-second bar: a stranger cannot tell what CODEC costs or what the €/$ buys (OSS vs setup vs license). Four different price stories for one product invite chargebacks and 'you said free' disputes.

**Fix:** Write one pricing truth (OSS free tier / $99-yr native app Q3-2026 / €500 done-for-you setup), apply it to opencodec.org, avadigital.ai/codec, README.md and the JSON-LD offers block; remove the bare word 'free' per brand rule.

**Evidence:** `Live opencodec.org JSON-LD: '"price": "0"' and FAQ 'Is CODEC free? — Yes. CODEC is completely free and open-source under the MIT license.'; live avadigital.ai/codec hero copy: 'Free. MIT-licensed. No subscription.' (and uses the banned word 'free' in client copy) — while the same page lists a $99/yr paid tier, the cart sells a €500 setup, README sells €10/€99, and the shipped client enforces licensing (codec_license.py:299 'paid build not activated — enter a license key').`

### PF-06 · MEDIUM · risk · unverified
**License server mints on checkout.session.completed without checking payment_status**

With any async payment method (SEPA/Sofort — likely for EU buyers) or a session completing unpaid, a signed 1-year JWT is emailed before funds settle; if payment later fails the buyer keeps a working key until subscription-status events catch up (and offline validation never rechecks).

**Fix:** Mirror the Pages webhook: mint only when payment_status == 'paid'; handle checkout.session.async_payment_succeeded for late settlement.

**Evidence:** `stripe_handler.py:195-211 issues the license for any subscription-mode session passing the price guard — obj.payment_status is never read. The sibling Pages webhook does it correctly: functions/bff/stripe/webhook.js:193 'if (obj.payment_status !== "paid") ... ignored: unpaid'.`

### PF-07 · MEDIUM · half-built · unverified
**Refunds never revoke a CODEC license — 'refunded' status is documented but unreachable**

A refunded buyer keeps a fully active license for up to a year; revocation is a manual /admin/revoke Mickael must remember. There is also no public refund policy page to point to.

**Fix:** Handle charge.refunded / customer.subscription.deleted-on-refund in stripe_handler: look up by customer/subscription id, set status 'refunded'; add a refund policy paragraph wherever the buy CTA lands.

**Evidence:** `db.py:17 schema comment lists status 'active | past_due | canceled | refunded', but stripe_handler.py handles only checkout.session.completed, customer.subscription.updated/deleted and invoice events — no charge.refunded handler, and grep shows nothing ever writes 'refunded'. The Pages webhook's charge.refunded clawback explicitly ignores non-Units payments (webhook.js:345-347 'refund_not_units').`

### PF-08 · MEDIUM · half-built · unverified
**Failed license email is invisible: 200 to Stripe (no retry) and no alert to anyone**

A buyer pays and silently receives nothing; nobody is notified until they complain to Mickael. Exactly the class of silent failure that already happened once.

**Fix:** On email failure, send an internal alert (Resend to ORDER_ALERT_TO equivalent) or return 500 so Stripe retries the webhook and the idempotent path re-attempts the email.

**Evidence:** `stripe_handler.py:152-157 catches the email exception, logs to the SQLite events table only, and the webhook still returns the minted row (HTTP 200), so Stripe never retries; the retry-on-webhook path is also dead because line 109-112 returns the existing license without re-attempting email. Proven in production: events row 2026-04-20 '{"stage": "email", "error": "The ava-digital.com domain is not verified..."}'. Contrast webhook.js:147-152 which emails an internal ORDER_ALERT_TO for every order.`

### PF-09 · MEDIUM · risk · unverified
**CODEC licensing runs on Lucy's domain (ava-license.lucyvpa.com) — product-context mixing**

Violates the standing rule 'never mix product/client contexts (CODEC vs InTake vs AVA) in ... licenses'; a paying CODEC customer sees Lucy infrastructure in their activation traffic, and any Lucy-driven DNS/cert change silently breaks all CODEC activations and heartbeats.

**Fix:** Serve the license API on a CODEC/AVA domain (e.g. license.opencodec.org or avadigital.ai subdomain) via the existing tunnel; keep the old host as an alias during migration since the URL is baked into shipped clients.

**Evidence:** `codec_license.py:50 'PUBKEY_URL_DEFAULT = "https://ava-license.lucyvpa.com/public-key"' and :108 base default 'https://ava-license.lucyvpa.com' — shipped in the CODEC client. lucyvpa.com is Lucy's (client-agent) domain per site copy 'always on lucyvpa.com'. Endpoint verified live (health OK).`

### PF-10 · LOW · ux · unverified
**The only purchasable CODEC item (€500 setup) has fully manual fulfillment and no scheduling step**

Buy-to-running depends entirely on Mickael responding within 24h — acceptable for a boutique install service, but it is currently CODEC's only working purchase, so the whole product's buyer journey has a human SPOF and no calendar link to self-schedule the install.

**Fix:** Add a booking link (the 'introduction call' Calendly) to the confirmation email and /order/thanks so the buyer schedules the install immediately instead of waiting for outreach.

**Evidence:** `webhook.js:135 buyer email promises 'We'll reach out within 24 hours to kick things off.'; webhook.js:270-273 comment: entitlements written as 'pending_provision' and 'Operator activates from /console/tenants'. catalog.js:47-51 confirms this €500 one-time item is the only 'codec' product for sale.`

### PF-11 · LOW · ux · unverified
**License email subject uses an emoji, against the no-emoji product rule**

First paid touchpoint breaks the product's own design rule; minor trust/polish inconsistency with the otherwise sober brand voice.

**Fix:** Drop the emoji: 'Your CODEC license is ready'.

**Evidence:** `email_sender.py:18: subject = "Your CODEC license is ready 🎉" (party-popper emoji) — the standing CODEC rule is no smartphone emoji anywhere, plain text or line-SVG only.`


## 3. LICENSE LIFECYCLE (ava-license service + codec app client) — verdict: **half-built**

The license backend is real and live (pm2 ava-license online, ENV=production, RS256-signed JWTs, UUIDv4 ids, post-incident price guard with tests) — keys are cryptographically unguessable and unforgeable, and the installer wizard correctly activates, hardware-binds, and writes edition=paid + ava.license_key that codec_license.py actually reads. But the lifecycle is a chain of islands: no public page sells the license the server mints (Stripe live price is $99/yr USD; README says €10/mo-€99/yr; avadigital.ai sells a €500 one-time setup whose checkout the webhook deliberately skips; the audit premise of €25 matches nothing), the license email's download button 404s, the shipped app never talks to the license server after install so revocation/cancellation/hardware-binding are unenforceable, and every failure path (email bounce, new Mac, refund, manual sale) ends in Mickael hand-editing SQLite or running curl with the admin token. Database ground truth: 4 licenses ever issued, all 4 revoked, zero ever activated or heartbeat-seen — the end-to-end journey has never once completed with a real buyer.

**Manual-Mickael steps in this area:**
- Selling a license at all: no public checkout links the CODEC $99/yr Stripe price (opencodec.org has no buy path, JSON-LD price:0; avadigital.ai/codec only sells the €500 setup) — Mickael must hand-create and send a Stripe checkout/payment link for every license sale.
- Delivering the app: CODEC_DMG_URL (https://avadigital.ai/codec/download) returns 404 — Mickael must manually transfer the 130MB CODEC-Installer.dmg (exists only locally at /Users/mickaelfarina/ava-stack/installer-gui/dist/) to each buyer.
- €500 Workstation Setup buyers get no license automatically (webhook skips non-subscription and non-CODEC-price sessions; db shows skipped_non_codec_checkout events) — and there is NO /admin/mint endpoint, so Mickael must SSH in and hand-run Python (licenses.mint + db.insert_license) to issue one.
- Email failure after payment: send failure is only written to the SQLite events table (no alert, no retry queue) — Mickael must notice it himself and run curl POST /admin/resend/{license_id} with the ADMIN_TOKEN. This already happened for real on 2026-04-20 (Resend: domain not verified).
- New Mac / machine transfer: activation returns 409 'license already bound to a different Mac' and no unbind endpoint exists (admin API = list/revoke/resend only) — Mickael must hand-edit licenses.db (UPDATE licenses SET hardware_uuid=NULL).
- Refund without subscription cancel: stripe_handler.py handles no charge.refunded/charge.dispute events — Mickael must manually curl POST /admin/revoke/{license_id}.
- Enforcing a revocation: the shipped client never re-contacts the license server, so /admin/revoke has zero effect on a running install — Mickael's only real kill switch is rotating the RSA keypair, which would brick every legitimate customer at next pubkey refresh.
- Support and deliverability: every license question lands at support@avadigital.ai (reply-to) answered personally; Resend domain verification for license@avadigital.ai must be manually maintained (its one failure silently swallowed a license email).

### LIC-1 · CRITICAL · island · ✓ CONFIRMED
**No public checkout sells the license the live server mints — entire lifecycle unreachable by a stranger**

The mint→email→activate machinery (live in production) can never be triggered by a buyer. Revenue path for the paid app is zero without Mickael manually creating checkout links; the README's 'Get it' promise dead-ends.

**Fix:** Create a Stripe Checkout/Payment Link for the CODEC license price and put a buy button on opencodec.org + avadigital.ai/codec; align README pricing (€10/mo needs its own Stripe price or must be removed).

**Evidence:** `Live Stripe price (license-server .env STRIPE_PRICE_ID=price_1TOJ1NAnpzAGXuyIdpUKHtZe) is recurring $99/yr USD (unit_amount 9900, interval year, livemode). https://opencodec.org JSON-LD declares "price": "0" and copy says 'You can download it from GitHub'; https://avadigital.ai/codec sells only 'Codec — Workstation Setup €500 · Add to cart' and states 'Free. MIT-licensed. No subscription.' No page links a checkout for the $99/yr price. /Users/mickaelfarina/codec-repo/README.md:837 (public repo) says 'Paid Mac app — €10/month or €99/year… [Get it → avadigital.ai]' — nothing at that destination sells it. licenses.db: 4 licenses ever, all revoked, none activated.`

### LIC-2 · CRITICAL · broken · ✓ CONFIRMED
**License email download button 404s — paid buyer cannot get the app**

Every license email ever sent (and every /admin/resend) sends the buyer to a dead link at Step 1. Money taken, no product — guaranteed 'email Mickael' for 100% of buyers.

**Fix:** Host the notarized DMG (R2/S3/GitHub Release), point CODEC_DMG_URL at it, and add a smoke test that curls the URL for 200 before the server starts.

**Evidence:** `/Users/mickaelfarina/ava-stack/license-server/email_sender.py:35 renders `<a href="{CODEC_DMG_URL}">Download CODEC.dmg`; production .env sets CODEC_DMG_URL=https://avadigital.ai/codec/download; `curl -I` returns HTTP 404 (no redirect). The only built DMG lives locally at /Users/mickaelfarina/ava-stack/installer-gui/dist/CODEC-Installer.dmg (May 25).`

### LIC-3 · HIGH · facade · ✓ CONFIRMED
**Revocation and subscription-cancel are DB-only — shipped app never re-checks, revoked keys work up to 1 year**

Revoking a leaked/refunded/canceled license (as was needed in the 2026-07-07 incident) has no effect on any installed copy until the JWT's 1-year exp. Stripe payment_failed/canceled status flips are equally cosmetic. There is no working kill switch.

**Fix:** Wire a periodic client check-in: call /api/v1/heartbeat from the existing scheduler/heartbeat daemon and downgrade license_state to readonly when server says revoked/canceled (keep the 7-day offline grace).

**Evidence:** `/Users/mickaelfarina/ava-stack/license-server/main.py:142-168 (heartbeat) and :202-209 (admin_revoke) exist, but grep across /Users/mickaelfarina/codec-repo and the staged app bundle finds zero callers of /api/v1/heartbeat or /api/v1/status; codec_ava_client.py:77 verify_license() claims 'called at startup' but has no callers. codec_license.py:301-311 validates only signature+expiry offline against a cached public key.`

### LIC-4 · HIGH · island · ✓ CONFIRMED
**Hardware binding enforced only inside the installer wizard — copying config.json unlocks unlimited Macs**

One paid key + a copied ~/.codec/config.json = fully working CODEC on any number of Macs, forever offline (pubkey is cached on disk). The email's claim 'This key is bound to your Mac' (email_sender.py:51) is only true for buyers who politely use the wizard twice.

**Fix:** Have the client send its IOPlatformUUID with the heartbeat (LIC-3) and enforce the 409 mismatch server-side; grace-period the response so a legitimate restore isn't bricked.

**Evidence:** `Binding happens once, in the installer: /Users/mickaelfarina/ava-stack/installer-gui/CODECInstaller/Sources/CODECInstaller/ActivationView.swift:95 POSTs /api/v1/activate with hardware_uuid. The app itself never checks hardware: codec_license.py license_state() (lines 294-318) verifies only signature/expiry; server-side mismatch check main.py:158 sits in the never-called heartbeat. All 4 DB licenses show hardware_uuid=NULL.`

### LIC-5 · HIGH · half-built · ✓ CONFIRMED
**No admin mint or unbind endpoints — the only mint path is a Stripe subscription webhook with the exact $99/yr price**

Every white-glove sale, comp license, machine transfer, or price experiment requires SSH + hand-run Python / raw SQLite against the production DB — high error risk on a live payments system and pure Mickael-bottleneck.

**Fix:** Add POST /admin/mint {email, tier, days} and POST /admin/unbind/{license_id} behind the existing ADMIN_TOKEN; both are ~15 lines each reusing licenses.mint/db helpers.

**Evidence:** `/Users/mickaelfarina/ava-stack/license-server/main.py:192-225 — admin surface is exactly list/revoke/resend. stripe_handler.py:197-198 skips mode!=subscription; :203 skips non-CODEC prices (db events show 2 skipped_non_codec_checkout). The €500 'Workstation Setup' checkout on avadigital.ai therefore mints nothing, and a new Mac cannot be unbound (main.py:119-123 hard 409).`

### LIC-6 · HIGH · risk · ✓ CONFIRMED (verifier adjusted severity → medium)
**Paid-but-no-email failure has no alert and no retry — already happened once in production**

Customer is charged, license is minted, key never arrives, and nobody is notified — silent until an angry email. Violates Mickael's own no-silent-failures ops rule.

**Fix:** On email failure, fire an operator alert (Telegram/iMessage hook already exists in codec stack) and add a retry-on-startup sweep for licenses with an unsent-email error event.

**Evidence:** `/Users/mickaelfarina/ava-stack/license-server/stripe_handler.py:152-157 — email send failure after a successful charge only does log.exception + db.log_event('error'). events table contains a real occurrence: 2026-04-20 'The ava-digital.com domain is not verified' (Resend). No monitoring reads the events table; email_sender.py:3-4 says 'retry via admin panel' but no panel exists, only raw curl.`

### LIC-7 · HIGH · copy-lie · ✓ CONFIRMED
**Public pricing claims contradict the shipped Stripe reality (€10/mo and €99/yr do not exist; only $99/yr USD does)**

A stranger reading the public README is promised prices that cannot be paid, in a currency that differs from what Stripe would charge — trust damage and a public claim untrue of the product today.

**Fix:** Pick one price story (e.g. $99/yr), create/retire Stripe prices to match, and update README.md:837, the email copy, and avadigital.ai/codec in the same commit.

**Evidence:** `/Users/mickaelfarina/codec-repo/README.md:837 (public repo AVADSA25/codec): 'Paid Mac app — €10/month or €99/year.' Stripe live account has one CODEC price: USD $99/year recurring (price_1TOJ1NAnpzAGXuyIdpUKHtZe); no monthly price exists. License email (email_sender.py:52) says 'Your subscription renews automatically'; avadigital.ai/codec says 'No subscription'; license-server/config.py:20 comment says '$99/yr'.`

### LIC-8 · MEDIUM · copy-lie · unverified
**PRIVACY.md makes false claims about license handling (Keychain storage, periodic validation)**

The public privacy policy (in a repo marketed on sovereignty/privacy) misdescribes where the license secret lives and what phones home — exactly the claims a privacy-conscious buyer will verify against the open source.

**Fix:** Either move the JWT into codec_keychain and implement the heartbeat (LIC-3), or correct both PRIVACY files to describe plaintext-config storage and pubkey-fetch-only traffic.

**Evidence:** `/Users/mickaelfarina/codec-repo/PRIVACY.md:21 lists 'license JWT' under 'macOS Keychain — service codec.*', but codec_keychain.py contains zero license references (grep -c 'licen' = 0) and the installer writes the JWT plaintext to ~/.codec/config.json (SetupView.swift:121-127) and ~/.codec/license.jwt (ActivationView.swift:151-157). docs/PRIVACY.md:37 claims license 'periodic validation' traffic to ava-license.lucyvpa.com — no code performs periodic validation (only unauthenticated pubkey fetch, codec_license.py:152).`

### LIC-9 · MEDIUM · risk · unverified
**License infrastructure lives on lucyvpa.com — client-adjacent brand baked into the public repo, installer, and privacy docs**

Violates the never-mix-product/client-contexts rule in public assets; a paying CODEC customer sees their sovereign workstation phoning an unrelated-looking domain, and a domain/tunnel problem on the Lucy side takes CODEC licensing down with it.

**Fix:** Serve the license API from license.avadigital.ai (CNAME to the same tunnel), change the two hardcoded defaults, and keep lucyvpa.com as a redirect during transition (old clients only fetch the pubkey, which stays valid from cache).

**Evidence:** `/Users/mickaelfarina/codec-repo/codec_license.py:50 PUBKEY_URL_DEFAULT = 'https://ava-license.lucyvpa.com/public-key' (public repo); InstallerState.swift:48-50 hardcodes the same; docs/PRIVACY.md:37 names it as the endpoint CODEC talks to. Lucy is a client-project brand (Dr. Jansen's agent) per house rules; the domain also carries ava-proxy for the same tunnel.`

### LIC-10 · MEDIUM · risk · unverified
**License JWT sent as a GET query parameter; no rate limiting; /health publicly discloses customer count**

The key that IS the product leaks into logs and intermediary analytics; the public health endpoint tells any prospect or competitor exactly how many licenses have ever been sold (currently 4).

**Fix:** Make /status a POST (or read the JWT from an Authorization header), drop licenses_total from /health (or gate it behind ADMIN_TOKEN), and add simple per-IP rate limits at the Cloudflare layer.

**Evidence:** `/Users/mickaelfarina/ava-stack/license-server/main.py:171-172 `GET /api/v1/status(license_jwt: str)` puts the full license secret in the URL (mirrored by codec_ava_client.py:89-92 params=), so it lands in uvicorn/Cloudflare access logs. No rate limiting on any endpoint. main.py:54-60 /health returns licenses_total to anyone — live response today: {"ok":true,"env":"production","licenses_total":4}.`

### LIC-11 · LOW · island · unverified
**Installer persists ~/.codec/license.jwt that nothing reads, with a comment describing binding code that does not exist**

Dead artifact invites future code to trust the wrong file, and the misleading comment hides the fact that client-side binding was never built (see LIC-4).

**Fix:** Delete the persistLicense() write (SetupView's config.json write is the real channel) and fix the comment.

**Evidence:** `/Users/mickaelfarina/ava-stack/installer-gui/CODECInstaller/Sources/CODECInstaller/ActivationView.swift:151-157 writes ~/.codec/license.jwt ('Persist for CODEC daemon to pick up'); grep across codec-repo and the staged app bundle finds zero readers (codec_license.py reads only config.json keys, lines 102-104). ActivationView.swift:136-137 comment claims the hardware UUID 'matches what CODEC's codec_ava_client.py uses for hardware binding' — codec_ava_client.py contains no hardware binding.`

### LIC-12 · LOW · ux · unverified
**License email subject uses an emoji, violating the CODEC no-emoji rule**

Off-brand first touch for a product positioned as a serious sovereign workstation; also mildly worse spam-filter profile for a transactional email carrying the paid key.

**Fix:** Change subject to 'Your CODEC license is ready' (and keep the body emoji-free, which it already is).

**Evidence:** `/Users/mickaelfarina/ava-stack/license-server/email_sender.py:18: subject = "Your CODEC license is ready 🎉" — house rule: no smartphone emoji anywhere in CODEC surfaces; this is the first thing every buyer sees.`


## 4. DELIVERY -> FIRST RUN — verdict: **broken**

The paid buyer journey dead-ends three separate times: the license email's download button 404s (https://avadigital.ai/codec/download), the installer DMG it should point at exists only on Mickael's disk (never uploaded anywhere), and even a buyer who somehow gets the DMG installs an app whose entry point logs "no services started" and exits 0 while the installer tells them "CODEC is running. Try the F13 hotkey". The license backend itself (activation server, hardware binding, signed JWT, notarized installer, Sparkle update route) is real and live — but the last mile from "activated" to "CODEC actually running" was never wired, the update feed has been frozen at the May 25 build for 141 fix-commits, and the OSS ./install.sh --update path always rolls itself back because it calls a smoke-test file that doesn't exist. licenses.db currently holds 4 licenses, all revoked — so no active customer is stranded today, but the first real buyer hits every one of these walls.

**Manual-Mickael steps in this area:**
- Send the installer DMG to each buyer by hand (email/WeTransfer) — the emailed link 404s and the only copy is /Users/mickaelfarina/ava-stack/installer-gui/dist/CODEC-Installer.dmg on his Mac
- Create/host the avadigital.ai/codec/download page or file — the route referenced by CODEC_DMG_URL has never existed in the site
- Re-send any failed license email via curl POST /admin/resend/{license_id} with the ADMIN_TOKEN — there is no admin UI
- Mint a license manually for any one-time (mode=payment) sale — the webhook only mints for subscription-mode checkouts and silently skips everything else
- Cut every product release by hand: bump VERSION, run packaging/macos/release_macos.sh or installer-gui/build-app.sh --sign, create the GitHub release on AVADSA25/codec-updates, upload DMG + appcast.xml (not done since 2026-05-25)
- Rebind a license when a buyer replaces their Mac — activation returns 409 'already bound to a different Mac' and the only admin endpoints are revoke/resend, so it is manual sqlite surgery on licenses.db
- Walk each buyer past the silent first-run failure (app launches and exits with no UI, no error) — there is no crash reporting or telemetry; support = reply-to license email / mikarina@avadigital.ai
- Keep the ava-license and ava-proxy PM2 services running on his own machine — buyer activation and the cloud-LLM path die whenever that host is down

### D1 · CRITICAL · broken · ✓ CONFIRMED
**License email's download link is a 404 — buyer pays and cannot download**

Step 1 of the paid onboarding email is dead. Every real buyer's journey stops at the first click after payment; only recourse is replying to the email.

**Fix:** Host CODEC-Installer.dmg (e.g. attach it to the AVADSA25/codec-updates GitHub release or R2) and either create the /codec/download redirect on avadigital.ai or change CODEC_DMG_URL in the license-server .env to the real URL, then restart pm2 ava-license.

**Evidence:** `/Users/mickaelfarina/ava-stack/license-server/email_sender.py:35 renders 'Download CODEC.dmg' pointing at CODEC_DMG_URL; /Users/mickaelfarina/ava-stack/license-server/config.py:27 and the live .env both set it to https://avadigital.ai/codec/download; curl returns 404 (custom 'Page not found — AVA Digital' page, re-verified twice 2026-07-10). No page or redirect for 'codec/download' exists anywhere in /Users/mickaelfarina/Documents/Claude/Projects/AVA-site-v2/ (grep: zero hits).`

### D2 · CRITICAL · facade · ✓ CONFIRMED
**Shipped paid app is a stub: launches, logs 'no services started', exits — while installer claims 'CODEC is running'**

Even with the DMG in hand, a buyer activates a license, grants permissions, and gets an app that does literally nothing — F13 and the wake word are dead. This is the product-level lie at the end of the funnel.

**Fix:** Wire the launcher to the already-built W5 modules: call packaging/macos/first_run.py (which invokes install_launchagents.sh + fetch_models.py) on first open, then start the fleet; or have the Swift installer's SetupView invoke them. All pieces exist and are tested — nothing calls them (grep shows zero callers of first_run outside its own tests).

**Evidence:** `Shipped bundle '/Users/mickaelfarina/ava-stack/installer-gui/dist/dmg_staging/Sovereign AI Workstation.app/Contents/Resources/codec_app_main.py' main(): '_log("fleet start deferred to W5-3 (launchd); no services started"); return 0'. Current repo copy /Users/mickaelfarina/codec-repo/packaging/macos/launcher/codec_app_main.py:110-113 is identical — still deferred today. Installer DoneView (/Users/mickaelfarina/ava-stack/installer-gui/CODECInstaller/Sources/CODECInstaller/SetupView.swift:232) tells the buyer: 'CODEC is running. Try the F13 hotkey to toggle voice, or say "Hey CODEC".'`

### D3 · CRITICAL · island · ✓ CONFIRMED
**The installer DMG was never published anywhere — nothing to download even if the link worked**

The delivery artifact is an island on one laptop. Fixing the 404 (D1) is impossible until this file is hosted; a disk failure loses the shipped build entirely.

**Fix:** Upload CODEC-Installer.dmg to the codec-updates GitHub release (or R2/avadigital.ai) and point CODEC_DMG_URL at it. The signing/notarization work is already done — this is purely an upload.

**Evidence:** `CODEC-Installer.dmg (130,529,697 bytes, built May 25, signed 'Developer ID Application: AVA Digital L.L.C', notarized, stapler-validated) exists only at /Users/mickaelfarina/ava-stack/installer-gui/dist/. GitHub API for AVADSA25/codec-updates shows releases v3.1.0/v3.2.0 contain only appcast.xml + the app-only update DMG 'Sovereign-AI-Workstation-3.2.0.dmg' (0 downloads); the installer DMG is in no release, and neither opencodec.org nor avadigital.ai links any .dmg.`

### D4 · HIGH · half-built · ✓ CONFIRMED (verifier adjusted severity → medium)
**Auto-update machinery is live but the feed has been frozen since May 25 — 141 fix-commits never shipped**

Any buyer runs the May 25 build forever; the in-app update check truthfully reports 'up to date' because no newer release exists. The team's own commit messages describe that build as needing a stability overhaul.

**Fix:** Establish a release cadence: bump VERSION, run packaging/macos/release_macos.sh, publish DMG + regenerated appcast.xml to codec-updates. Consider a CI job so releases stop depending on a manual local run.

**Evidence:** `Appcast at github.com/AVADSA25/codec-updates (fetched live) has exactly one item: 3.2.0, pubDate 'Mon, 25 May 2026'. git log in /Users/mickaelfarina/codec-repo shows VERSION last bumped 2026-05-25 (13f2bdd) and 141 commits since, including 9aeee7c 'comprehensive CODEC stability overhaul — wake word, voice, draft, screenshot, identity' and 619f528 security-dialog/mouse fixes. routes/update.py + codec_update (Ed25519-verified) are shipped and polling.`

### D5 · HIGH · broken · ✓ CONFIRMED
**Installer requests macOS permissions for the wrong app — grants attach to the installer bundle, not the workstation app**

The mandatory permissions step secures the wrong binary: on first real run the workstation app would still lack mic/accessibility/screen-recording, so voice, hotkeys and vision fail again after the buyer already 'granted' everything.

**Fix:** Move permission prompting into the installed app's own first run (packaging/macos/first_run.py already implements exactly this with deep links per W5-6 design), or launch the installed app to trigger its own TCC prompts; drop the hard gate in the installer.

**Evidence:** `/Users/mickaelfarina/ava-stack/installer-gui/CODECInstaller/Sources/CODECInstaller/PermissionsView.swift:101-127 calls AVCaptureDevice.requestAccess, AXIsProcessTrustedWithOptions and CGRequestScreenCaptureAccess from the installer process (bundle id com.avadigital.codec.installer per build-app.sh); macOS TCC grants are per-bundle-id, so 'Sovereign AI Workstation.app' receives none of them. The wizard hard-blocks progress until all three are granted (PermissionsView.swift:74 '.disabled(!allGranted)').`

### D6 · HIGH · facade · ✓ CONFIRMED
**'Cloud-first' default LLM is unwired — installer config feeds a module only the Compare feature uses, and no local model is bundled**

Even after fixing D2, a paid buyer's default chat/voice pipeline points at a local Qwen server that doesn't exist on their Mac, and the live ava-proxy (health 200) is never called by the main pipeline — no working LLM out of the box.

**Fix:** Route the main pipeline through codec_ava_client.ava_chat when config edition=paid and ava.enabled, falling back to local when a model exists — or bundle/fetch the model via fetch_models.py during setup (W5-5 exists, uncalled).

**Evidence:** `SetupView.swift:122-127 writes ava.proxy_url + default_cloud_model='gemini-2.5-flash-lite' ('customers use Gemini via AVA proxy by default' per its comment, model download skipped). In /Users/mickaelfarina/codec-repo, grep shows 'default_cloud_model' is read only by codec_ava_client.py, whose sole importer is codec_compare.py; codec_ava_client.py's own docstring: 'Nothing in this file auto-wires anything.' Main chat/voice runs codec_session.qwen_* against a local MLX server, and the 130MB DMG contains no model.`

### D7 · HIGH · broken · ✓ CONFIRMED
**Documented OSS update command always fails and rolls itself back — calls a smoke test that doesn't exist**

Every user of the advertised update path ('Update: ./install.sh --update', printed at install end) gets a guaranteed '⚠️ Some smoke checks failed. Rolling back...' — updates are impossible via the documented route.

**Fix:** Point install.sh at the real smoke entry (scripts/smoke.py exists) or restore codec_smoke_test.py; add a CI check that files referenced by install.sh exist.

**Evidence:** `/Users/mickaelfarina/codec-repo/install.sh:63 runs 'python3 codec_smoke_test.py' in --update mode; the file exists neither locally (ls: No such file) nor in the public repo (raw.githubusercontent.com/AVADSA25/codec/main/codec_smoke_test.py → 404, while the public install.sh line 63 still calls it). Failure branch (install.sh:66-69) does 'git reset --hard $ROLLBACK_COMMIT' and exits 1.`

### D8 · MEDIUM · copy-lie · unverified
**Live site troubleshooting instructs commands that don't exist (codec.py --list-skills / --mcp)**

A stuck first-run user follows official troubleshooting into 'unrecognized' behavior, deepening the impression the product is broken.

**Fix:** Rewrite the Troubleshooting entries against real entry points (pm2 status, codec_mcp_http on 8091, dashboard on 8090) — the deployed page already fixed the dashboard port, so only these two commands remain wrong.

**Evidence:** `Live bundle https://opencodec.org/assets/index-CWlSL47R.js contains 'Run: python3 codec.py --list-skills to verify they're discovered' and 'the MCP server is started: python3 codec.py --mcp'. /Users/mickaelfarina/codec-repo/codec.py has no argparse/CLI handling (its only 'argv' hits are inside an embedded AppleScript string at lines 608-615); MCP actually starts via codec_mcp.py / codec_mcp_http.py (port 8091 per codec_mcp_http.py:16 — which the same page's marketing copy correctly states).`

### D9 · MEDIUM · risk · unverified
**License is only minted for subscription-mode checkouts — a one-time payment sale would charge the buyer and deliver nothing**

The payment→delivery seam fails closed but silently: a legitimate one-time buyer gets charged with zero delivery and no alert to Mickael.

**Fix:** Either support mode=payment for the CODEC price ID in stripe_handler.handle(), or alert (email/log-based notification) whenever a checkout containing STRIPE_PRICE_ID is skipped.

**Evidence:** `/Users/mickaelfarina/ava-stack/license-server/stripe_handler.py:196-198: 'if obj.get("mode") != "subscription": return {"ok": True, "skipped": ...}'; and _issue_license_for_session raises 'missing subscription id' (lines 104-106). No public page currently sells the €25 license at all (avadigital.ai/pricing offers only 'Codec €500 install'; opencodec.org has no purchase path), so any ad-hoc Stripe Payment Link used to sell it would be one-time mode → silent skip, money taken, no email.`

### D10 · MEDIUM · island · unverified
**Two disconnected packaging pipelines; the tested first-run/launchd/model machinery (W5-3/5/6) has zero callers in what ships**

The designed first-run experience (model fetch, launchd fleet, TCC deep-links per docs/W5-6-FIRST-RUN-DESIGN.md) exists as dead code; the shipped installer reimplements a thinner, partly wrong version (see D2, D5, D6). Every future release risks maintaining two divergent installers.

**Fix:** Pick one canonical pipeline (HANDOFF-MICKAEL.md already flags this decision): have the Swift wizard shell out to first_run.py, or fold the wizard into packaging/macos and retire installer-gui's duplicate logic.

**Evidence:** `/Users/mickaelfarina/codec-repo/packaging/macos/ contains first_run.py, launchd/install_launchagents.sh, fetch_models.py, uninstall_codec.sh, release_macos.sh (all with passing tests); repo-wide grep finds no caller of first_run outside its tests, and docs/HANDOFF-MICKAEL.md §1 states the codec-repo pipeline 'has NEVER produced an artifact' while what ships is the separate ~/ava-stack/installer-gui Swift bootstrapper that invokes none of it.`

### D11 · MEDIUM · ux · unverified
**No self-serve license rebind — buyer who replaces their Mac dead-ends at 409**

Any Mac upgrade or clean reinstall on new hardware locks a paying customer out until Mickael hand-edits licenses.db.

**Fix:** Add POST /admin/rebind/{license_id} (clear hardware_uuid) at minimum; ideally a customer-facing 'deactivate this Mac' via the JWT.

**Evidence:** `/Users/mickaelfarina/ava-stack/license-server/main.py:119-123 rejects activation from a second machine ('license already bound to a different Mac', 409); the only admin endpoints are /admin/revoke and /admin/resend (main.py:202-225) — no unbind/rebind exists, and the license email (email_sender.py:51) tells buyers 'bound to your Mac on first activation' with no migration path.`

### D12 · LOW · risk · unverified
**Local opencodec.org source is stale relative to the live deploy — a redeploy would regress live fixes**

The tree the audit (and future edits) treats as the site source would reintroduce the wrong dashboard port and any other regressions if redeployed.

**Fix:** Pull the current Replit/deploy source back into this folder (or delete it and document the real source of truth) before the next site edit.

**Evidence:** `/Users/mickaelfarina/Documents/Claude/Projects/ava-web-template/sources/opencodec.org/artifacts/codec-landing/src/App.tsx:1793 says 'Ensure FastAPI is running on port 8765', but the live bundle (index-CWlSL47R.js) says 'localhost:8090' / 'port 8090' and contains zero '8765' hits — the deployed site is newer than this checkout.`

### D13 · LOW · risk · unverified
**Paid app DMG is publicly downloadable via the update feed; 'paid' gating rests solely on an installer-written config flag**

Anyone can fetch the paid bundle and run it as an unenforced OSS build. Largely by design (engine is MIT), but it means the €25 buys only the installer convenience — worth stating honestly in the offer copy.

**Fix:** Accept and document it (open-core positioning), or gate the update-DMG release assets behind a token-authenticated redirect if the paid bundle is meant to be buyer-only.

**Evidence:** `Appcast enclosure URL github.com/AVADSA25/codec-updates/releases/download/v3.2.0/Sovereign-AI-Workstation-3.2.0.dmg returns 200 unauthenticated; /Users/mickaelfarina/codec-repo/codec_license.py:6-9 documents that without edition:"paid" in ~/.codec/config.json the build 'is NEVER enforced — full local features'.`


## 5. Cross-product hygiene (CODEC vs InTake vs AVA separation) — verdict: **half-built**

The 2026-07-07 leak is genuinely fixed on the CODEC side: _session_is_codec_purchase() fails closed on every error path, is wired into handle(), the regression test passes 6/6 against live config, the guard has already skipped 2 real non-CODEC checkouts in production (events log 2026-07-07 and 2026-07-09), and the leaked license is revoked in the DB. But the CLASS is only half-fixed. (1) The sibling handler on the same Stripe account — avadigital.ai/bff/stripe/webhook, verified enabled and subscribed to checkout.session.completed — deliberately fails OPEN: every paid checkout it can't classify becomes an "AVA Digital order" with an agency onboarding email, so a CODEC buyer gets a second, contradictory email promising human outreach. (2) Revocation is theater: the client never calls activate/heartbeat/status, so the revoked dental-client license still validates offline until 2027-07-07 and the email's "bound to your Mac" claim is false. (3) CODEC licensing and proxy run on lucyvpa.com — the Lucy client-product domain — and public PRIVACY.md tells buyers so. (4) Live avadigital.ai names Dr. Stephan Jansen. (5) The license email's download button 404s, so even a clean CODEC purchase dead-ends. Brand-email separation is otherwise decent (CODEC sends as license@avadigital.ai, Lucy as noreply@lucyvpa.com, no dental branding in CODEC templates), and the public codec GitHub repo contains no client names.

**Manual-Mickael steps in this area:**
- Spot wrongly-skipped CODEC purchases himself: fail-closed skips write only a skipped_non_codec_checkout DB event with no alert; recovery is a manual POST /admin/resend/{license_id} with the ADMIN_TOKEN (stripe_handler.py:43-44 explicitly relies on this).
- Revoke or re-send any license via raw curl with ADMIN_TOKEN (main.py /admin/revoke, /admin/resend) — no UI, and revocation currently has no effect on a running client anyway.
- Personally honor 'we'll reach out within 24 hours' that the AVA order-confirmation email auto-promises on EVERY paid checkout on the shared account (alert lands at mikarina@avadigital.ai, webhook.js:149), including self-serve CODEC buyers who shouldn't need outreach.
- Upload/host the CODEC DMG at https://avadigital.ai/codec/download — the URL in every license email returns 404 today, so post-payment fulfillment requires him to email the build manually.
- Register/rotate Stripe webhook endpoints and per-product STRIPE_PRICE_ID by hand; rotating the CODEC price in Stripe without updating license-server/.env silently turns the guard into 'skip every purchase'.
- Track which Resend account each product sends from in his head — no registry maps product→Resend account/domain, so a new sender can land on the wrong brand account (Lucy and SENIL Villas already share one).

### XP-1 · CRITICAL · broken · ✓ CONFIRMED
**License email download button 404s — every paid CODEC buyer dead-ends at Step 1**

A buyer pays 25 EUR, receives the license email, clicks Step 1, gets a 404. Journey is dead without emailing Mickael — fails the core bar and invites disputes/refunds.

**Fix:** Host the signed DMG at that URL (or point CODEC_DMG_URL at the real artifact, e.g. a GitHub release / R2 bucket) and add a smoke test that HEADs the URL before the server starts.

**Evidence:** `email_sender.py:35 renders the 'Download CODEC.dmg' button from CODEC_DMG_URL; license-server/.env and config.py:27 set it to https://avadigital.ai/codec/download; live check: curl -> HTTP 404 (verified 2026-07-10).`

### XP-2 · HIGH · risk · unverified
**Sibling webhook on shared Stripe account fails OPEN: CODEC buyers get an 'AVA Digital order' agency email too**

Every CODEC checkout fires BOTH handlers: buyer gets the self-serve license email from 'CODEC License' AND an agency order email promising human outreach in 24h — contradictory cross-product messaging, plus a false fulfillment obligation on Mickael. Same class as the 2026-07-07 incident, opposite direction, still live.

**Fix:** In webhook.js's order branch, skip sessions whose line items match the CODEC STRIPE_PRICE_ID (or any allow-listed sibling product price) — mirror the license server's price filter; fail closed to 'record only, no email' for unknown prices.

**Evidence:** `AVA-site-v2/AVA Digital Design System/functions/bff/stripe/webhook.js:201-204: 'EVERYTHING else that is paid is a product ORDER: recorded + confirmation-emailed'; :129 subject 'Your AVA Digital order is confirmed'; :135 'We'll reach out within 24 hours to kick things off'. Stripe API (read-only, license-server key): endpoint https://avadigital.ai/bff/stripe/webhook is ENABLED and subscribed to checkout.session.completed on the same account as the CODEC price; live POST probe returns 400 'invalid signature' (configured, not 503).`

### XP-3 · HIGH · island · unverified
**Revocation/activation backend never called by client — revoked leaked license still works until 2027**

The incident 'remediation' (admin_revoked 2026-07-07 14:20) has no client-side effect: the InTake dentist — and any future revoked/chargeback buyer — keeps a fully working CODEC license for up to a year. Also makes the email claim in XP-4 false and lets one key run on unlimited Macs.

**Fix:** Wire codec_license.license_state() to also consult /api/v1/status (or heartbeat) when online — treat status revoked/canceled as hard-invalid; call /api/v1/activate once on first activation to actually bind hardware.

**Evidence:** `Server: main.py:92 /api/v1/activate (hardware bind), :142 /heartbeat, :202 /admin/revoke. Client: codec_license.py license_state() verifies the JWT purely offline (no server status call anywhere); codec_ava_client.py:77 verify_license() ('called at startup') has ZERO callers (grep across repo: only its own definition). DB (read-only): license 39d83782-... dr-jansen@t-online.de status=revoked, expires 2027-07-07; JWT remains signature-valid and unexpired.`

### XP-4 · HIGH · copy-lie · unverified
**License email claims 'bound to your Mac on first activation' — nothing performs binding**

Public claim to every buyer is untrue today; one license is shareable across unlimited machines, and buyers may fear a re-install penalty that doesn't exist.

**Fix:** Either implement activation binding client-side (see XP-3) or soften the email copy until it exists.

**Evidence:** `email_sender.py:51: 'This key is bound to your Mac on first activation and expires in 1 year.' No code in codec-repo calls /api/v1/activate (grep: zero client callers); licenses table shows bound=0 for all rows.`

### XP-5 · HIGH · risk · unverified
**CODEC paid infrastructure lives on the Lucy client-product domain (lucyvpa.com), documented to buyers**

Violates the 'never mix product/client contexts in licenses or billing' rule: every CODEC license check and cloud prompt transits a domain branded for Lucy (a client-facing agent). Buyers inspecting traffic or PRIVACY.md see an unexplained third brand; CODEC availability is coupled to another product's domain lifecycle.

**Fix:** Stand up license.avadigital.ai / proxy.avadigital.ai CNAMEs on the same tunnel, flip the two defaults + PRIVACY.md, keep lucyvpa.com endpoints as temporary aliases for deployed clients.

**Evidence:** `codec_license.py:50 PUBKEY_URL_DEFAULT='https://ava-license.lucyvpa.com/public-key' and :108; codec_ava_client.py:12 proxy_url 'https://ava-proxy.lucyvpa.com'; public docs shipped in the PUBLIC repo (github.com/AVADSA25/codec): docs/PRIVACY.md:30,37 and PRIVACY.md:41 name ava-proxy.lucyvpa.com / ava-license.lucyvpa.com / codec-mcp.lucyvpa.com.`

### XP-6 · HIGH · facade · unverified
**Client named on live avadigital.ai: 'Dr. Stephan Jansen' testimonial + 'Dr. J.' card that links to his practice**

Direct breach of the 'never name clients in public assets' brand rule; the 'Dr. J.' abbreviation is a facade since the card links straight to jansen-gelnhausen.de identifying the client anyway.

**Fix:** Either get written consent and name him consistently, or genuinely anonymize: drop the outbound link/screenshot and the full name in the review attribution ('Dental practice owner, Germany').

**Evidence:** `Live https://avadigital.ai (fetched 2026-07-10): blockquote figcaption 'Dr. Stephan Jansen' (rvw-by, 'Trustpilot · May 2026'); client card { key: 'jansen', name: 'Dr. J.', cat: 'Dental Clinic · Germany', url: 'https://jansen-gelnhausen.de/' } with img ../assets/client-jansen.jpg. Source: AVA-site-v2/replit-current/client/src/components/ShowcaseCarousel.tsx:140-144.`

### XP-7 · MEDIUM · risk · unverified
**LUCY's default sender identity hard-codes the Jansen practice — cross-CLIENT leak waiting for tenant #2**

Any Lucy tenant deployed without RESEND_FROM set sends ITS patients email as 'Zahnarztpraxis Dr. Jansen' — the exact identity-mixing class as the 2026-07-07 incident, between two clients. The shared generic env name invites cross-service config bleed on a shared host.

**Fix:** Make from_email a required per-tenant config with no client-named fallback (fail closed: refuse to send), and rename the vars LUCY_RESEND_FROM / CODEC_RESEND_FROM.

**Evidence:** `LUCY/api/services/email_service.py:20: DEFAULT_FROM = os.getenv('RESEND_FROM', 'Lucy – Zahnarztpraxis Dr. Jansen <noreply@lucyvpa.com>') — used as default from_email in 5+ send functions (:253,:293,:343,:397,:455). Same env-var name RESEND_FROM is also used by the CODEC license server (license-server/config.py:23).`

### XP-8 · MEDIUM · risk · unverified
**Shared Resend accounts across brands and across clients (suppression/reputation/rate-limit bleed)**

One brand's bounces/complaints suppress or throttle another's transactional mail: an InTake/agency campaign hiccup can block CODEC license delivery (account-level suppression list and rate limits), and the Jansen practice shares deliverability fate with SENIL Villas.

**Fix:** Split into per-brand Resend accounts (or at minimum per-domain audiences + monitoring); document product->account mapping; never co-locate two clients on one account.

**Evidence:** `Read-only GET /domains per key: license-server key -> ['avadigital.ai:verified']; InTake key -> ['avadigital.ai:verified'] (same account as CODEC license mail + AVA portal order mail); LUCY key -> ['lucyvpa.com:verified','senilluxuriousparosvillas.com:verified'] (one account shared by TWO unrelated clients: dental practice + villa rental).`

### XP-9 · MEDIUM · risk · unverified
**Legacy third-party 'onlyfriend-ai' webhook endpoint still registered on the business Stripe account**

An unrelated external GCP project sits one toggle away from receiving payment events (customer emails, amounts) for all AVA products; it also signals the account predates and exceeds the current 4-product mental model.

**Fix:** Delete the endpoint in the Stripe dashboard (it's disabled — removal is zero-risk) and inventory any other legacy artifacts on the account (payment links, old prices).

**Evidence:** `Stripe API (read-only) webhook_endpoints list: 'https://us-central1-onlyfriend-ai.cloudfunctions.net/stripeWebhook | disabled | events: payment_intent.succeeded' registered on the same account that bills CODEC/InTake/AVA.`

### XP-10 · MEDIUM · copy-lie · unverified
**The two public faces contradict each other: opencodec.org says 'completely free', AVA sells 25 EUR licenses, config says $99/yr**

A 25-EUR buyer who finds opencodec.org concludes they paid for something advertised as completely free (refund/chargeback fuel); a prospect on opencodec.org never learns a paid tier exists. Three conflicting price stories across public assets.

**Fix:** Pick one narrative: on opencodec.org add a 'Free OSS vs Paid (cloud proxy, license)' section linking to the avadigital.ai checkout; fix the stale $99/yr comment in config.py.

**Evidence:** `opencodec.org source artifacts/codec-landing/index.html:125 (FAQ JSON-LD): 'CODEC is completely free and open-source under the MIT license'; :62 schema.org price '0' USD. Meanwhile the license flow sells subscriptions (email_sender.py:52 'Your subscription renews automatically') and license-server/config.py:20 comments the price id as 'price_... for $99/yr license'. Repo IS public+MIT (github.com/AVADSA25/codec), so 'free' is true of the OSS build only — no page explains the paid edition split.`

### XP-11 · MEDIUM · island · unverified
**opencodec.org is a journey island — zero links to the paid product or avadigital.ai**

The discovery site cannot hand a stranger to either the free download or the 25-EUR purchase — the buy journey and the public face never connect (avadigital.ai only mentions opencodec.org once, inside a team bio).

**Fix:** Add two CTAs to the landing page: 'Get the OSS build (GitHub)' and 'Get the supported edition (avadigital.ai)'; cross-link back from the AVA Codec section.

**Evidence:** `All hrefs in artifacts/codec-landing/index.html resolve to: /favicon.svg, fonts.googleapis.com, fonts.gstatic.com, i.imgur.com/RbrQ7Bt.png, https://opencodec.org/ — no avadigital.ai, no checkout, no GitHub link despite FAQ:125 saying 'You can download it from GitHub'.`

### XP-12 · LOW · ux · unverified
**Emoji in CODEC license email subject violates the no-emoji product rule**

Breaks the stated CODEC brand rule (no smartphone emoji anywhere) in the single most important buyer-facing message; also slightly raises spam-filter risk for a transactional email.

**Fix:** Subject: 'Your CODEC license is ready' — plain text.

**Evidence:** `email_sender.py:18: subject = 'Your CODEC license is ready \U0001F389' (party-popper emoji).`


## 6. WEB QUALITY HOLISTIC (opencodec.org live + source) — verdict: **half-built**

The brochure site itself works: HTTP/2, TTFB ~0.5s, all 7 external links resolve (GitHub 200, PayPal, YouTube, avadigital.ai, imgur), robots.txt + sitemap + OG/Twitter meta + JSON-LD present, responsive breakpoints (900/768/480px) exist, images have alt text. But as the public face of a PAID product it is half-built: there is no purchase surface at all while a live Stripe-to-license backend idles (the only money link is a PayPal donation), the live page simultaneously claims three different versions (title v2.0, JSON-LD 1.5.0, rendered body v3.2) and three feature counts (234/50+/400), deploys are hand-clicked in Replit with the audited source clone 3 months behind production (it contains none of the live v3.2 content), there is zero analytics, the logo is a 1MB PNG hotlinked from imgur, JS/CSS ship uncompressed with cache-control:private, www.opencodec.org doesn't resolve, and the page has no h1. A stranger CAN understand what CODEC is in 30 seconds — but the site tells them it's free, and there is no path from that page to a €25 license without emailing Mickael.

**Manual-Mickael steps in this area:**
- Deploying the site: hand-click 'Publish' inside the Replit workspace ('Published your App' auto-commits); no CI config (no firebase/netlify/wrangler/gh-actions anywhere), and the local clone at ava-web-template/sources/opencodec.org has not been synced since 2026-04-09.
- Syncing site copy with each CODEC release: version and feature numbers are hand-edited in three separate places (index.html title/meta, index.html JSON-LD, App.tsx hero badges + footer) — today they show v2.0, v1.5.0, and v3.2 simultaneously; sitemap lastmod (2026-03-31) is also hand-maintained and stale.
- Handling every stuck visitor: both support paths on the page are mailto:mikarina@avadigital.ai (footer 'Contact' + Troubleshooting section), so any non-developer who can't git-clone emails Mickael.
- Selling a license: the site has no checkout link, so every sale requires Mickael to manually send a Stripe payment link (the ava-license webhook backend then does the rest).
- Shipping a downloadable installer: installer-gui/build-app.sh must be run and the artifact uploaded to GitHub releases by hand — release v3.2.0 (2026-05-29) shipped with assets: [].
- Changing the logo/favicon/OG-logo: assets are hotlinked from Mickael's personal i.imgur.com account, so any brand-asset change is a manual imgur re-upload plus URL edit in two repos.
- Knowing whether the site converts: zero analytics — the only signal is manually checking GitHub stars/traffic and PayPal notifications.

### WEB-01 · CRITICAL · facade · ✓ CONFIRMED
**Paid license backend has zero surface on the public site — and the site tells Google the product costs $0**

A stranger cannot go from the public face to a purchased license at all — the entire production checkout/license/email backend is unreachable from the web. Simultaneously, Google rich results and the FAQ snippet assert price $0/free, directly contradicting the €25 license business; any buyer who later pays can point at the site's own structured data saying it's free.

**Fix:** Decide the positioning once: if licenses are sold, add a pricing/license section with a Stripe Payment Link (the ava-license webhook already handles checkout.session.completed) and change the JSON-LD Offer to the real price; if the free/OSS positioning is intentional, the license server's CODEC product line has no acquisition channel and that's a business-level gap to close.

**Evidence:** `Live Stripe→license machine exists: /Users/mickaelfarina/ava-stack/license-server/main.py:71 `@app.post("/webhooks/stripe")`; stripe_handler.py:92 `_issue_license_for_session`; config.py:20 `STRIPE_PRICE_ID = _env("STRIPE_PRICE_ID")  # price_... for $99/yr license`. Full external-URL inventory of the live JS bundle (assets/index-CWlSL47R.js): avadigital.ai, github.com/AVADSA25/codec, i.imgur.com/RbrQ7Bt.png, paypal.me/avadsa25, youtube.com/embed/OEXxvxA0_AE — no checkout, no pricing, no license page. Meanwhile live HTML declares `"offers": {"price": "0", "priceCurrency": "USD"}` (JSON-LD) and FAQ schema answers 'Is CODEC free?' with 'Yes. CODEC is completely free and open-source'; the only money link is footer `<a href="https://paypal.me/avadsa25">Support ❤️</a>` (App.tsx:1856).`

### WEB-02 · HIGH · copy-lie · unverified
**Live page contradicts itself: three versions and three feature counts served at once**

The Google SERP snippet and social cards advertise v2.0/234-features while the page a visitor lands on says v3.2/400 — reads as an unmaintained or careless product within the first 30 seconds, and stale FAQ/SoftwareApplication rich results keep serving wrong claims.

**Fix:** Make version/skill/feature counts a single build-time constant injected into index.html meta, JSON-LD, and the hero badges; update as part of the release checklist.

**Evidence:** `Live HTML head (curl https://opencodec.org): meta description = 'CODEC v2.0 — free, open-source ... 234 features, 60 skills' (line 9); JSON-LD `"softwareVersion": "1.5.0"` (line 65) and featureList '50+ built-in skills' (line 73). Rendered body from live bundle: hero badges `children:"v3.2"`, `"9 Products"`, `"76 Skills"`, `"400 Features"`, footer `" · v3.2.0 · MIT License"`, troubleshooting 'quick fixes for CODEC v3.2'. GitHub release confirms product is v3.2.0 (2026-05-29).`

### WEB-03 · HIGH · risk · unverified
**Deploy pipeline is a hand-clicked Replit Publish; audited source is 3 months behind production and cannot rebuild it**

The real site source lives only inside a Replit workspace; the repo on Mickael's machine (the one this audit was pointed at) is stale and cannot reproduce production. A Replit account/workspace problem makes the site unrecoverable, and any 'fix the site' work done in this repo would silently regress live v3.2 content back to v1.5.

**Fix:** Pull the Replit workspace state back into the repo, make the repo the source of truth, and add a scripted deploy (even a one-line replit deploy or a GitHub Action) so publish is reproducible.

**Evidence:** `Local repo last commit: d7c3a59 2026-04-09 'Published your App' (git log in ava-web-template/sources/opencodec.org). grep for the live strings '400 Features|v3.2|76 Skills|9 Products' in local App.tsx = 0 matches; local footer still says 'v1.5.0' (App.tsx:1860) and 'Qwen 3.5' (line 545) where live says 'Qwen 3.6'. Deploy config is only .replit (`deploymentTarget = "autoscale"`); no CI/firebase/netlify/wrangler config exists anywhere in the tree.`

### WEB-04 · HIGH · ux · unverified
**Performance: 1MB imgur-hotlinked logo + 360KB uncompressed, uncacheable render-blocking bundles**

~2.5MB initial load on a page whose LCP element is a 1MB third-party logo; every repeat visit re-downloads all 360KB of JS/CSS because private forbids caching; imgur hotlinking is rate-limited/blocked in some regions and imgur can purge the image — killing logo, favicon, touch icon, and OG logo in one stroke.

**Fix:** Serve a ~20KB optimized logo (and favicon) from /public on the site's own domain, enable gzip/br compression and `cache-control: public, max-age=31536000, immutable` for /assets, trim font weights to 3-4, add loading="lazy" to the YouTube iframe.

**Evidence:** `Logo https://i.imgur.com/RbrQ7Bt.png content-length: 1,039,613 bytes, used as nav+hero+footer logo (App.tsx:4 LOGO_URL), PNG favicon AND apple-touch-icon (index.html:37-38) and JSON-LD org logo. Assets: JS 269,486 B and CSS 90,910 B — no `content-encoding` header even with `Accept-Encoding: gzip, br`, and `cache-control: private` on hashed immutable assets (and homepage). Render-blocking Google Fonts stylesheet loads Inter 7 weights + italic axis + JetBrains Mono 3 weights (24KB CSS alone); YouTube iframe (`src:"https://www.youtube.com/embed/OEXxvxA0_AE"`) has no loading="lazy".`

### WEB-05 · HIGH · island · unverified
**Installer is built locally but never shipped: GitHub release has zero assets, web journey is git-clone-only**

The 'buy → running on my Mac' journey requires Terminal, git, and Python — the signed installer that exists on disk never reaches a buyer. Non-developer buyers (the people who'd pay €25 instead of cloning MIT source) dead-end and must email Mickael.

**Fix:** Run installer-gui/build-app.sh as part of the release, upload the artifact to the GitHub release, and put a 'Download for macOS' button in the hero next to 'Get Started'.

**Evidence:** `/Users/mickaelfarina/ava-stack/installer-gui/ contains CODECInstaller, build-app.sh, dist/. GitHub API for latest release: tag v3.2.0, published 2026-05-29, `assets: []`. The live site's entire install path is the quickstart terminal block starting `git clone https://github.com/AVADSA25/codec.git` (present in live bundle); no .dmg/.pkg/download URL exists anywhere in the bundle's link inventory.`

### WEB-06 · MEDIUM · broken · unverified
**www.opencodec.org does not resolve**

Anyone who types www., or any tool/email client that auto-prepends it, gets a browser DNS error — a dead site instead of the product page. HSTS includeSubDomains makes a future half-configured www worse.

**Fix:** Add a www CNAME/ALIAS in DNS pointing at the Replit deployment and 301 it to the apex.

**Evidence:** ``curl https://www.opencodec.org` → 'curl: (6) Could not resolve host: www.opencodec.org'. Apex works (HTTP/2 200, HSTS with includeSubDomains) and http→https 301 works.`

### WEB-07 · MEDIUM · risk · unverified
**Zero analytics anywhere — conversion is completely unmeasured**

No data on visits, referrers, GitHub click-through, PayPal clicks, or which sections hold attention — every marketing decision about CODEC's only public face is a guess (flying blind on conversion).

**Fix:** Add a privacy-friendly one-liner (Plausible or umami script tag in index.html) — fits the product's privacy-first positioning; track outbound GitHub/download clicks as events.

**Evidence:** `grep for analytics|plausible|gtag|umami|posthog|fathom|matomo|mixpanel|segment|hotjar|goatcounter across artifacts/codec-landing/src/, index.html AND the live production bundle assets/index-CWlSL47R.js = 0 matches in all.`

### WEB-08 · MEDIUM · ux · unverified
**Page has no h1 at all — hero title is a <p>**

Weakens ranking for the exact queries the meta keywords target ('AI command layer macOS') and breaks the heading landmark for screen-reader users; heading tree starts at h2.

**Fix:** Make the hero subtitle an <h1> (visually styled the same) and keep section titles as h2.

**Evidence:** `Local App.tsx headings: 27 <h2>, 1 <h3>, 0 <h1>; hero is `<p className="hero-subtitle">Open-Source Intelligent Command Layer for macOS</p>` (App.tsx:748). Live bundle confirms: jsx("h1") occurrences = 0, h2 = 31.`

### WEB-09 · LOW · ux · unverified
**SEO plumbing decay: sitemap is 12 fragment URLs with stale lastmod, unknown paths return 200, og:image dimensions lie**

Crawlers drop URL fragments so the sitemap is effectively one URL with a stale date; soft-404s let junk paths into the index; the OG mismatch causes cropped social cards on some platforms.

**Fix:** Reduce sitemap to the homepage with a build-stamped lastmod, serve a real 404 (or meta noindex shell) for unknown paths, and regenerate the OG image at 1200x630 or fix the declared dims.

**Evidence:** `Live sitemap.xml: 13 entries of which 12 are anchors of the homepage (e.g. `<loc>https://opencodec.org/#what</loc>`), all `<lastmod>2026-03-31</lastmod>` (3+ months stale). `curl -w '%{http_code}' https://opencodec.org/xyz123` → 200 (soft-404 SPA fallback). index.html declares og:image 1200x630 (lines 24-25) but the actual opengraph.jpg is 1280x720 (sips).`

### WEB-10 · LOW · ux · unverified
**Favicon is a blank orange rectangle; icon fallbacks depend on the 1MB imgur file**

The browser tab shows an anonymous orange square (weak brand recall among many tabs); pinning to iOS homescreen downloads 1MB from a third-party host that may block hotlinking.

**Fix:** Export the CODEC mark into favicon.svg + a 180x180 apple-touch-icon.png served from /public.

**Evidence:** `Live /favicon.svg is exactly `<svg ...><rect width="180" height="180" rx="36" fill="#FF3C00"/></svg>` — no glyph or letterform. PNG favicon and apple-touch-icon both point to https://i.imgur.com/RbrQ7Bt.png (index.html:37-38), the same 1,039,613-byte file.`

### WEB-11 · LOW · ux · unverified
**Pinch-zoom disabled on mobile (maximum-scale=1)**

Blocks pinch-to-zoom on iOS/Android — WCAG 1.4.4 failure and a real annoyance on the dense terminal-demo sections of the page.

**Fix:** Drop `maximum-scale=1` from the viewport meta.

**Evidence:** `index.html line 5 (identical on live): `<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1" />`. Responsive breakpoints otherwise exist (live CSS has @media max-width 900/768/480px).`

### WEB-12 · LOW · ux · unverified
**Emoji throughout the public UI contradicts the CODEC no-emoji brand rule**

The public face violates the product's own visual standard — the app UI is emoji-free by decree while the marketing page leads its main CTA with ⚡, undercutting the 'sovereign workstation' seriousness.

**Fix:** Replace CTA/footer emoji with text or the existing line-SVG icon set used in the app; feature-grid emoji can become small inline SVGs.

**Evidence:** `Live bundle: primary CTA `children:"⚡ Get Started"`, footer `"Support ❤️"`, feature icons rendered as emoji text nodes (🎙️, 🧠, 📅, 🔐, ₿ — App.tsx lines 545-549, 587, 638). Mickael's standing rule: no smartphone emoji anywhere in CODEC surfaces; line-SVG icons or plain text.`


## 7. JOURNEY SEAMS (land → understand → buy → pay → license → download → install → first run → updates → support) — verdict: **broken**

The MIDDLE of the pipeline is genuinely built and wired: Stripe webhook → price-guarded mint (stripe_handler.py) → license email with JWT (email_sender.py) → installer wizard that activates against the live server (ActivationView.swift calls /api/v1/activate, hardware-binds) → writes ~/.codec/config.json with edition=paid + ava.license_key which codec_license.py actually enforces → a live Ed25519-signed update feed (codec-updates appcast, HTTP 200). Server is live in production (ava-license.lucyvpa.com/health: env=production, licenses_total=4). But BOTH outer seams are missing or broken, so no stranger can complete the journey: (1) ENTRY — no public page anywhere sells the CODEC license. opencodec.org sells the product as "free, MIT, price 0" with zero buy path; avadigital.ai/codec says the paid app is "Launching Q3 2026 · Notify me"; README says "Get it → avadigital.ai" which loops back to that. The only live payment surface is the €500 "Workstation Setup" cart item — a payment-mode checkout the license webhook deliberately skips. (2) DELIVERY — the license email's only download button, https://avadigital.ai/codec/download, returns HTTP 404. BUY-WHILE-ASLEEP TIMELINE: Path A (opencodec.org visitor) never finds a way to pay — installs the free OSS build, €0 revenue, no email captured. Path B (avadigital.ai/pricing → cart → €500 checkout): card charged, Stripe receipt sent, then nothing — webhook logs skipped_non_codec_checkout, no license, no download, no scheduling link; buyer waits until Mickael wakes and fulfills everything by hand. Path C (Mickael manually sent a subscription payment link before sleeping): webhook mints and emails the license automatically within minutes — the one seam that works unattended — then the buyer clicks "Download CODEC.dmg", hits 404, and is stuck until morning; if Resend hiccups, the email is silently lost (log-only, no retry). Every path today ends in "wait for Mickael".

**Manual-Mickael steps in this area:**
- SELL: create and send a Stripe SUBSCRIPTION checkout/payment link for the CODEC license by hand for every buyer — no public page links one, and stripe_handler.py only mints for subscription-mode sessions matching STRIPE_PRICE_ID (anything else is silently skipped).
- DELIVER DOWNLOAD: send every paying buyer a working DMG link by hand — the license email's download button (https://avadigital.ai/codec/download) is a 404; the working DMG lives unreferenced at github.com/AVADSA25/codec-updates releases.
- FULFILL €500 CART PURCHASES: notice the Stripe payment, contact the buyer, schedule the personalized install, and issue their license by hand — the webhook skips payment-mode sessions and the admin API has no /admin/issue endpoint (only revoke/resend), so minting means a manual DB insert or a replayed subscription checkout.
- RECOVER LOST LICENSE EMAILS: watch the ava-license logs/db for Resend failures and manually POST /admin/resend/{license_id} — email failure after payment is log-only, with no retry and no alert (stripe_handler.py:152-158).
- SUPPORT: personally answer mikarina@avadigital.ai (opencodec.org), support@avadigital.ai (license email reply-to), and the avadigital.ai contact form — there is no help center or paid-app docs; docs link points at the OSS GitHub README.
- CANCEL/REFUND: license email says 'renews automatically unless you cancel' but links no Stripe customer portal — buyer must email, and Mickael cancels/refunds in the Stripe dashboard by hand.
- SHIP UPDATES: rebuild, sign, notarize the DMG and publish the GitHub release + appcast.xml by hand — feed has been frozen at 3.2.0 since May 25 while main kept shipping (commits Jul 9-10).
- UNBIND HARDWARE: a buyer who replaces/reinstalls their Mac gets HTTP 409 'license already bound to a different Mac' from /api/v1/activate — no self-serve unbind exists; Mickael must clear hardware_uuid in licenses.db by hand.
- TRIAGE 'NOTIFY ME' LEADS: the Q3-2026 native-app 'Notify me →' button is just an anchor to the avadigital.ai contact form — no mailing list; every lead is a manual inbox reply.

### JS-01 · CRITICAL · facade · ✓ CONFIRMED
**No public surface sells the CODEC license — live production license backend has zero entry point**

The €25 license cannot be bought by anyone, ever, without Mickael hand-crafting a Stripe payment link. Revenue is capped at manual 1:1 sales; the production license server, purchase guard, and email pipeline serve traffic that cannot exist.

**Fix:** Add a Buy page/button (opencodec.org and avadigital.ai/codec) that links a Stripe Checkout/payment link for the exact subscription STRIPE_PRICE_ID; remove or date-correct the 'Launching Q3 2026' block.

**Evidence:** `License server LIVE: curl https://ava-license.lucyvpa.com/health → {"ok":true,"env":"production","licenses_total":4}; full pipeline in /Users/mickaelfarina/ava-stack/license-server/stripe_handler.py:195-211 mints only for subscription-mode checkouts of STRIPE_PRICE_ID. But no page can start that checkout: opencodec.org source (/Users/mickaelfarina/Documents/Claude/Projects/ava-web-template/sources/opencodec.org/artifacts/codec-landing/src/App.tsx, full file) has no buy/pricing element — hero CTAs lines 762-767 go to GitHub and #quickstart, footer 1852-1857 offers only GitHub/mailto/PayPal-donate. Live avadigital.ai/codec paid tier reads "Launching Q3 2026 · Notify me →" with href="index.html#contact" (fetched page). codec-repo/README.md:837 sends buyers to avadigital.ai ("Get it → avadigital.ai"), which loops to that same 'Q3 2026' wall.`

### JS-02 · CRITICAL · island · ✓ CONFIRMED
**License email download button 404s — paying buyer dead-ends immediately after payment**

Every automated license delivery (the ONLY automated seam) hands the buyer a dead link as Step 1. Buyer paid, cannot download, must email Mickael — guaranteed support ticket or refund on 100% of unattended sales.

**Fix:** Point CODEC_DMG_URL at the live GitHub release asset (or deploy a real /codec/download redirect on avadigital.ai); add a smoke test that HEADs CODEC_DMG_URL at license-server startup.

**Evidence:** `/Users/mickaelfarina/ava-stack/license-server/email_sender.py:35 renders `<a href="{CODEC_DMG_URL}">Download CODEC.dmg</a>`; .env sets CODEC_DMG_URL=https://avadigital.ai/codec/download (config.py:27 same default); curl → HTTP 404 (AVA 'Page not found' template). A working signed DMG exists but is unreferenced: https://github.com/AVADSA25/codec-updates/releases/download/v3.2.0/Sovereign-AI-Workstation-3.2.0.dmg → HTTP 200.`

### JS-03 · CRITICAL · copy-lie · ✓ CONFIRMED
**Public pages claim CODEC is entirely free / owned forever while the shipped product is a 1-year auto-renewing license that degrades to read-only**

Buyer trust: whichever way a stranger reads it, one public claim is false — either they think there is nothing to buy (kills the sale) or a paying customer discovers 'own forever' actually means an expiring, machine-bound subscription (refund/chargeback material under EU consumer rules).

**Fix:** Split the story explicitly on both sites: 'OSS build: free forever (MIT)' vs 'Paid Mac app: €X/yr subscription, 1 Mac, auto-renews'; delete 'own forever' for Codec on /pricing and the price:0 JSON-LD once the paid tier is on sale.

**Evidence:** `Live https://opencodec.org head JSON-LD: "offers": {"price": "0"} and FAQ "Is CODEC free? … Yes. CODEC is completely free and open-source" (fetched HTML lines 60-126). Live avadigital.ai/codec hero: "Free. MIT-licensed. No subscription." Live avadigital.ai/pricing: "You own the agent forever — even if the monthly plan ends (Lucy, InTake and Codec…)". Versus shipped truth: email_sender.py:51-53 "expires in 1 year. Your subscription renews automatically"; codec-repo/codec_license.py:10-12 invalid/expired license → gated features disabled (read-only).`

### JS-04 · HIGH · island · unverified
**€500 'Codec — Workstation Setup' checkout takes money but the license pipeline is wired to ignore it**

The single live self-serve way to pay for CODEC today produces no license, no download, no scheduling — only a Stripe receipt. A buyer who pays €500 overnight gets silence until Mickael manually notices and fulfills; the CODEC-side log records it as skipped_non_codec_checkout.

**Fix:** Have the cart webhook trigger a CODEC-specific 'what happens next' email + notify Mickael for codec items, or add the setup price ID to an allowed list that mints a license alongside the manual install.

**Evidence:** `Live avadigital.ai cart: /site/cart.js line 19 `'codec': { n: 'Codec — Workstation Setup', l: '€500' … }`; catalog source (/Users/mickaelfarina/Documents/Claude/Projects/AVA-site-v2/AVA Digital Design System/functions/_lib/catalog.js:47-51) price_1TgX0DAnpzAGXuyI7bsHNpli recurring:false → checkout.js sets mode=payment. License server: stripe_handler.py:196-198 `if obj.get("mode") != "subscription": return {"skipped"…}` and :203 price-ID guard — so this purchase can never mint a license or send the CODEC welcome email.`

### JS-05 · HIGH · copy-lie · unverified
**Three conflicting public prices/currencies for the same paid app across the journey**

A stranger comparing the README, opencodec.org and avadigital.ai sees no coherent offer — currency and amount change per page, which reads as unmaintained or untrustworthy at the exact moment of purchase intent.

**Fix:** Pick one price+currency, update README.md:837, the avadigital.ai/codec tier card, and /pricing in the same commit; make the Stripe price the single source of truth.

**Evidence:** `codec-repo/README.md:837: "Paid Mac app — €10/month or €99/year"; live avadigital.ai/codec Tier 02: "$99 /year" (USD); live avadigital.ai/pricing: "Codec Installation from €500"; license-server config.py:20 comment: "price_... for $99/yr license". Audit premise says €25.`

### JS-06 · MEDIUM · facade · unverified
**"Bound to your Mac" is only enforced at installer time — runtime never checks binding and /api/v1/heartbeat has zero callers**

One JWT pasted into ~/.codec/config.json activates unlimited Macs; revocation/cancellation only bites after the 7-day offline grace if the pubkey fetch is the sole server touchpoint. The heartbeat endpoint is built-but-never-wired.

**Fix:** Have the app call /api/v1/heartbeat (with hardware_uuid) on its existing startup license path and honor 409/revoked; or drop the 'bound to your Mac' claim from the email.

**Evidence:** `email_sender.py:51: "This key is bound to your Mac on first activation". Binding happens only in the installer (installer-gui ActivationView.swift POSTs /api/v1/activate with hardware_uuid). Runtime enforcement (codec-repo/codec_license.py:17-19, 152-236) is offline RS256 signature verification only — no activate/heartbeat/status call with hardware; repo-wide grep for api/v1/activate|api/v1/heartbeat|hardware_uuid finds only codec_ava_client.py:91, which calls /api/v1/status without hardware. Server endpoint main.py:142-168 (/api/v1/heartbeat, hardware mismatch 409) is never called by any shipped client.`

### JS-07 · MEDIUM · half-built · unverified
**Update channel frozen at 3.2.0 (May 25) while main kept shipping — paid buyers silently stuck 6+ weeks behind**

The one well-built late seam (Ed25519-verified auto-update, codec_update.py wired into the dashboard) delivers nothing because releases are manual and stale; buyers keep bugs already fixed on main, and the version chaos (2.0 vs 1.5.x vs 3.2.0) undermines changelog/support conversations.

**Fix:** Cut a release from current main (build-app.sh → sign → upload + appcast) and unify the version string shown on opencodec.org with the VERSION file; add a scheduled reminder/CI check when main is N commits ahead of the appcast.

**Evidence:** `Live appcast https://github.com/AVADSA25/codec-updates/releases/latest/download/appcast.xml: single item 3.2.0, pubDate "Mon, 25 May 2026". codec-repo has active commits Jul 9-10 2026 (38d071a fix: health endpoint…, 0de03d3 docs: v1.5.1 changelog). Site badges meanwhile advertise "v2.0" and "v1.5.0" (App.tsx:753-757, 1860) — three version identities on one journey.`

### JS-08 · MEDIUM · risk · unverified
**Support contact is a different address on every surface, none verified — stuck buyers may mail a void**

The post-purchase rescue path (the buyer's only exit from the 404 download, JS-02) depends on mailboxes that may not exist; a bounced support email after a paid dead-end converts a fixable ticket into a chargeback.

**Fix:** Send test mail to mikarina@ and support@ (manual step below); standardize one support address across opencodec.org footer, troubleshooting, and RESEND_REPLY_TO.

**Evidence:** `opencodec.org App.tsx:1811 & 1855: mikarina@avadigital.ai (looks like a typo of Mickael/Farina); license email reply-to default support@avadigital.ai (config.py:24, RESEND_REPLY_TO set in .env); avadigital.ai exposes only /contact form and privacy@avadigital.ai. Domain MX exists (smtp.google.com) but mailbox existence is unverifiable from the repo.`

### JS-09 · LOW · risk · unverified
**Paid 'Sovereign AI Workstation' DMG is publicly downloadable and runs fully unlocked without a license**

Anyone who finds the appcast URL (embedded in the shipped app, DEFAULT_FEED_URL in codec_update.py:39) gets the paid build gratis with all gated features. Consistent with open-core intent (MIT repo), but it makes the paid tier's only exclusive value the cloud proxy + support — worth a deliberate decision rather than an accident.

**Fix:** Either accept and document it (open-core), or gate release assets (private repo + signed URLs in the license email) — the email is the right delivery point once JS-02 is fixed.

**Evidence:** `Repo AVADSA25/codec-updates is public (api.github.com: private:false); release DMG URL returns HTTP 200 unauthenticated. The app-only DMG ships without the installer wizard's config write, and codec-repo/codec_license.py:294-295 fail-open: no `edition:"paid"` in config → mode "oss", never enforced (GATED_FEATURES lines 54-60 all allowed).`

### JS-10 · LOW · ux · unverified
**Hero 'Watch Demo' button skips the demo video and lands on the install checklist**

The 30-second-understanding path breaks: a visitor clicking the promise of a demo gets a dependency checklist (Python 3.10+, Whisper, sox), the strongest bounce trigger on the page.

**Fix:** Change the href to #intro-video (one-line fix in App.tsx:765).

**Evidence:** `App.tsx:765-767: `<a href="#quickstart" className="btn-secondary">▶ Watch Demo</a>` — #quickstart is the Installation/prereqs section (line 1681); the actual demo video lives in #intro-video (YouTube embed OEXxvxA0_AE, line 798-816, video confirmed live via oEmbed HTTP 200).`


---
## Method

7 parallel scoped reviewers (one per area) reading actual code/content + curling live pages; every finding cites file:line or URL+quote. All 41 critical/high findings were queued for adversarial verification by independent agents instructed to refute them; the first 30 (all criticals first) were checked: **30 CONFIRMED, 0 REFUTED**; the remaining 11 are marked unverified. 37 agents total, 878 tool calls, ~3.3M tokens, 26 minutes. Reviewers were barred from printing secret values and from any mutating operation.
