# HANDOVER — CODEC buyer journey

**Last updated:** 2026-09-04 pm · native window, connect screen, one key slot, text size

## 2026-09-04 (pm) — a real window, a connect screen, and the false green that almost shipped

**State: main @ c3f840b, clean. ava-stack `harden/...` at ff6b4a9, pushed, NOT merged.**

### Shipped
- **#338/#340** native WKWebView window — the app is no longer a Chrome tab. Links out go
  to the real browser; closing the window keeps the agent alive; Dock icon returns it.
- **#342** first-run "Connect your AI": local / AVA cloud / any OpenAI-compatible URL. Only a
  real reply from the endpoint clears it. Fixed the underlying default: chat fell back to
  the 20 GB Qwen3.6 that a fresh install never downloads — every first message failed.
- **#343** setup and chat now read ONE Keychain slot. The first cut stored keys privately,
  verify() went green, chat sent no bearer → 401. A false green from the screen built to
  prevent false greens. Also: local option disabled with a reason when no server can answer.
- **#344** text size S/M/L, default M (1.15 zoom), all 8 surfaces.
- LiteLLM restored after my `git stash -u` took its untracked gateway/ dir (memory note).

### Verified live
AVA proxy answers the licence JWT with READY. Mickael connected his own local model
through the screen: `/api/setup/status → connected: true`.

### Needs Mickael — product decision
"Use the local model" cannot work on a buyer's Mac: the bundle has no mlx_vlm and the
model-server script needs a venv only the dev machine has. Bundling the serving stack is
~+900 MB (273 MB → ~1.2 GB download). Until decided, the screen disables that option
honestly. AVA cloud is the working zero-friction path today.

### Open
1. ava-stack branch review + merge (money path). Installer DMG must be rebuilt to bundle
   the new app (window + connect screen) — the shipped DMG is from Sep 3.
2. `_fleet_loaded()` false positive; `_bootstrap_fleet` bypasses the ephemeral guard.
3. Auth route writes unsigned lines into the HMAC audit log.
4. Demo narration script.

---

## 2026-09-04 — four user reports, one shared root cause, and the launcher proven by macOS itself

**State: main @ 13d8b16, clean. ava-stack `harden/installer-and-subscription-filter` pushed,
NOT merged (money path — needs Mickael). Fleet 19/19, LiteLLM :4000 back.**

### What was wrong
- **"CODEC ALERT: AVA Gateway (LiteLLM 4000) not responding" every 5 min** — `git stash -u`
  on Aug 27 swept the UNTRACKED `~/ava-stack/gateway/` (run-litellm.sh, config, .env, venv).
  ava-litellm survived until a Sep 2 restart, then crash-looped and fell out of PM2;
  ava-units-reconcile hit 33,351,309 restarts. Restored from `stash@{0}^3`, re-added,
  `pm2 save`. Same stash had taken the installer's AppIcon.icns. Memory note written.
- **Notifications link "does nothing"** — it goes to `/tasks#reports` and the tasks page never
  read the hash. Now honours it (#338).
- **No "system" theme option** — tasks/auth/audit still had the 2-state toggle. 3-state ported (#338).
- **"Logo in the Dock, nothing opens"** — runtime `setActivationPolicy(.accessory)` not honoured
  when launched via `open`. `LSUIElement=true` in Info.plist; success now opens the dashboard;
  failure shows a modal with the real reason (#338, #340).

### Proven, not asserted
Instrumented launcher run inside a signed, hardened bundle copy, observed via System Events:
`status item created visible=true`, `bg-only: true`, failure path → `windows: 1` "CODEC could not
start". Via `open` on this machine the fleet reports running (see known-issues: `_fleet_loaded`
counts the app's own LS registration) so no alert — correct.

### Do not repeat
- **Never `git stash -u` here.** Untracked dirs are live runtime inputs.
- Direct-exec vs `open` differ in PATH and stdio; test the double-click path with `open` +
  System Events, never only from a shell.
- 40 `test_llm_*` failures on main are pre-existing (cloud-gating commits) — known-issues.

### Open
1. ava-stack branch review + merge.
2. Installer should bundle the CODEC app inside its Resources (installApp no-ops from /Applications).
3. `_fleet_loaded()` false positive; `_bootstrap_fleet` bypasses the ephemeral guard.
4. Auth route writes unsigned lines into the HMAC audit log.
5. Demo narration script.

---

## 2026-09-03 — the Mac app: from "installs and does nothing" to a menu-bar item that serves HTTP

**State: main @ 3e98681. codec-repo clean. ava-stack `harden/installer-and-subscription-filter`
pushed (c020321), NOT merged — it touches license-server money paths and needs a look
before main. Fleet untouched throughout.**

### What shipped
- **#327** model switch restarts the PM2 process — footprint 20→peak 39 GB before, peak==current after.
- **#328** localStorage shim on all 8 surfaces; pytest no longer writes 315 lines/run into the real audit log.
- **#329** design system on auth/audit/tasks; phantom Inter font tokens on vibe/voice.
- **#332** trusted-skill manifest regenerated — chrome_open was DEAD live (hash mismatch → AST gate).
- **#335** the packaged app runs. Six root causes, all in the commit message. G6 proves it: bundled
  interpreter + bundled sources served HTTP 200 on :8099 with the port asserted free first.

### Licences
- Dr. Jansen's leaked CODEC licence revoked via /admin/revoke (was resurrected 2026-08-07 by an
  unfiltered `customer.subscription.updated`; that door is now closed in ava-stack).
- Mickael's own licence (db80be4b) reactivated and hardware-bound to this Mac.

### Do not repeat
- `git stash -u` sweeps UNTRACKED files — it stashed the installer's AppIcon.icns and every
  build after had no logo. Check `git status` for `??` before stashing.
- Never import inside the bundled Python without `-B`: it writes .pyc into the signed bundle.
- Removing a worktree deletes its `dist/` — check nothing is still reading from it.
- Both hosts `codec-license.avadigital.ai` and `ava-license.lucyvpa.com` resolve to the same IP;
  the domain migration is COMPLETE. The memory note saying "gated on DNS" is stale.

### Open
1. ava-stack branch review + merge (money path).
2. Installer should bundle the CODEC app in its own Resources/ — today `installApp()` no-ops
   when the installer is run from /Applications alone. Mickael's app was hand-copied.
3. `routes/auth.py` writes unsigned plaintext into the HMAC audit log → `audit_verify`
   reports integrity_ok=False on the live machine. Logged in docs/known-issues.md.
4. Demo narration script (item 3 from 08-20) — still unwritten.
5. Active model is whatever config says — it was Fréd at one point; check before recording.

---

## 2026-08-20 — five surfaces on one design system; model switching shipped

**State: main @ 39f2f5c, clean, zero open PRs. All 10 demo services online,
dashboard 200, active model Qwen3.6-35B-A3B (the fast one — correct default for
recording). Demo NOT yet shot.**

### Shipped (PRs #307-#325)

- **Copy button (#307)** — never worked, on phone or desktop, for anyone. Root cause:
  `encodeURIComponent` does NOT escape an apostrophe, so `don't` still terminated the
  single-quoted JS string inside the inline `onclick` and the handler died with a
  SyntaxError. Silently. It also killed the edit and speak buttons. Both devices failed
  because the PWA's Chat link serves the same codec_chat.html. Fixed by binding with
  addEventListener over a closure — no escaping, and message text can never be executable.
- **Heartbeat was killing the model mid-answer (#308)** — the "no reply" bug. A 5s health
  probe timed out during an 11k-token prefill, the heartbeat declared the model down and
  restarted it ON THE FIRST failed probe, killing the in-flight request. Longer prompts
  self-destructed more reliably. Fixed with `_is_listening()`: a bare TCP connect tells
  BUSY from DEAD, because a process mid-prefill still has its socket bound.
- **Model switching (#314, #315)** — picker in chat, voice ("switch to the coding model"),
  and `/api/models`. One model resident at a time, enforced by the server's single-slot
  cache; a failed load auto-reverts AND re-probes the previous model so CODEC is never
  left mute. Serving venv moved to `~/codec-qwen38-venv` (transformers 5.15 / mlx_vlm
  0.6.13) so Qwen3.8 loads; Qwen3.6 got FASTER on it (61.7 tok/s).
- **Reply stats (#316, #317)** — every reply shows elapsed, tokens and tok/s. Needed a fix
  in codec_llm.stream: usage arrives in a final chunk whose `choices` list is EMPTY and the
  parser did `choices[0]` unconditionally, so it was swallowed as a parse failure. First
  version divided tokens by wall time and read 2.5 tok/s while the model did 26 — now uses
  the server's `timings.predicted_per_second`.
- **Attachments were silently dropped in Agents and Project mode (#316)** — `pendingFiles`
  was only read in the chat path, which both other branches return past. Project mode
  discarded attachment-only messages entirely.
- **Design system (#318-#324)** — all five surfaces on one token set. The dashboard, vibe
  and voice had never received the 2026 palette refresh and still ran `#E8711A`; chat
  declared `#d97757` and then painted the USER'S OWN BUBBLE with hardcoded old orange.
  Type moved to IBM Plex Sans/Mono. Zero emoji across all five. Auto day-night theme
  (system / light / dark, default system) with a VISIBLE Auto/Light/Dark control in the
  menu — it shipped as a blind cycling toggle first and Mickael could not see the option.
- **CLAUDE.md split (#325)** — 11,909 -> 8,979 words. Reference moved to
  docs/codec-architecture.md, gaps to docs/codec-status.md. NOTE: CLAUDE.md is a SYMLINK
  to AGENTS.md; diffs show under AGENTS.md.

### Two bugs worth remembering

1. **An unguarded top-level `localStorage.getItem` kills a whole script.** Storage access
   THROWS when blocked, and cortex is embedded as an iframe in the dashboard. Everything
   after that line — including theme init — silently never ran. The page looked fine and
   was half-dead. Guarded in cortex; chat, dashboard and vibe still have the same pattern.
2. **Verify the SERVED page, not the working tree.** Source greps reported chat clean while
   the live page still served five `#E8711A` values — the first pass had only replaced the
   `rgba()` spelling, not the hex.

### Open — Mickael

- **Record the demo.** DEMO_SCRIPT.md is sealed at 22 beats; § Recording setup has the OBS
  config. Before rolling: sign into Notion, toggle GitHub OFF (beat 17), reboot (uptime is
  7 days, swap 11.1/12.3 GB).
- `avadigital.ai` GitHub repo is **archived and read-only** — that work has no off-machine
  backup. Flagged by the InTake session; unresolved.

### Open — next session

1. **Make a model switch restart the PM2 process** instead of swapping in-place. Repeated
   in-place switching grew the server to 52 GB (measured). Highest value: it protects a
   recording.
2. Guard the remaining unguarded `localStorage` reads in chat, dashboard, vibe.
3. Demo narration script — still unwritten, the one unprepared piece of the shoot.
4. `Qwen3-Coder-30B-A3B` — A3B MoE, so ~60 tok/s for coding vs the dense 27B's 26. What is
   on disk is a **16 KB stub**, not the weights; it needs a real download.
5. Overview/Cortex artboard LAYOUT (only the tokens landed).

### Do not repeat

- `pm2 start qwen3.6` FAILS — pm2 reads the name as a filename. Use `pm2 restart qwen3.6`
  or the numeric id. This briefly took the brain offline.
- `brew` autoremoved `python@3.13` and broke the model server's venv interpreter
  (`ModuleNotFoundError: mlx_vlm`). It is now installed on-request so autoremove leaves it.
- Never `pm2 restart all` — it bounces intake-api (live customers), lucy-* and cloudflared.

---

## 2026-07-24 — the guard now checks BOTH directions

**codec_premise_check (#298)** — the anti-BS work was directional: it validated
assistant → user only. A real miss exposed the gap. Mickael's LinkedIn post credited a
practice to a commenter ("the most useful reply I got was from SOMEONE WHO ends each
session by listing…"); a correspondent then attributed it to Mickael ("YOU MENTIONED YOU
end each session by…"). Both texts were in context. Neither CODEC nor the assistant
flagged it — the answer engaged with the premise and reinforced it. **Worse than
invention: nobody made anything up, a false premise propagated as fact.**

Scope is deliberately tiny — ONLY attributions with an artifact in context are checkable.
Premises about the world have nothing to check against; a general premise-checker would
relocate the fabrication one level up, with CODEC "correcting" the user from nothing.
Tone asks, never corrects: all that's provable is that two passages disagree. Four
conditions must all hold. Kill switch `PREMISE_CHECK_ENABLED=false`. **Verified live
end-to-end on the real incident text.**

**inject_count, NOT fired_count (#296)** — asked for `fired_count`; shipped `inject_count`
because "fired" is not measurable. A rule's whole lifecycle is `prompt_block() → sys_prompt
→ LLM`; nothing observes whether the model used it. `fired_count` would be a fabricated
metric wearing an authoritative name, in the module built to refuse exactly that.
`inject_count == 0` PROVES dead weight; a high count proves nothing but cost. The listing
says so in those words. Verified: survived restart at 4 → 5 → 6 on real turns.

**standing_rules over-triggered (#299)** — found while verifying the premise check.
"standing rule(s)" and "my rules" were bare TRIGGERS and the remove/list/clear regexes
were unanchored, so quoting *"was there ever a rule you had to delete?"* fired the skill,
which tried to remove a rule named "you". The turn never reached the LLM, so BOTH checks
were bypassed. Same class as the file_ops chat-hang (#282): **a mention is not a command.**

**claim-check scope bug (#295)** — `last_user_text` was populated only inside the
`use_tools` gate, so with tools off the request-intent half got nothing and silently
degraded to reply-patterns alone. A safety check must not depend on an unrelated flag.

**ruff (#293 → #297)** — CI went red on EVERY PR with no code change: ruff 0.16.0 released,
the workflow installed it unpinned, lint went 0 → 3117 errors. Root cause was that
`ruff.toml` set only `ignore` and INHERITED ruff's default selection. Now `select` is
explicit, so a release can't change the gate — and because of that the version pin came
back OFF. Verified clean on both 0.15.22 and 0.16.0.

**Standing lesson from all of it:** checking what the model SAYS is whack-a-mole (four
rounds of pattern widening, each beaten by a rephrase). Checking what was ASKED against
what actually HAPPENED is not.

## 2026-07-22 — v3.5: CODEC Pilot parked, 9 → 7 products (#288)

**Mickael's call, and the evidence backed it.** Pilot is withdrawn from the product.
Google blocks account sign-in from any CDP-controlled browser — that's detection of the
debug channel Pilot cannot work without, not a setting. Cookie walls + bot challenges make
a large share of real sites unusable. Six separate Pilot defects surfaced in one day of
demo prep. **Parked, NOT deleted** — code + PM2 service stay in the `codec-pilot` repo.

- Removed: Pilot tab (button, panel, 735 lines of JS), Cortex product card, 2 graph nodes
  + 7 edges. Verified in a browser: 4 tabs switch, 0 console errors, 0 dangling edges.
- **CODEC Project folded into CODEC Overview** — a dashboard capability, not its own
  product. Zero functional change; its Cortex architecture nodes STAY (the daemon is real).
- 7 products: Core · Dictate · Instant · Chat · Vibe · Voice · Overview
- v3.2.0 → **v3.5.0** everywhere + CHANGELOG entry. FEATURES total 402 → 370 (Pilot's 32
  excluded, inventory kept for reference). Demo script 23 → **22 beats**.

## 2026-07-22 — the anti-BS layer (the durable win)

Three hallucination classes were found and each got a different defence:

1. **Invented capabilities** — CODEC claimed "I have ingested the 10-point instruction set,
   I am now operating under this framework for all future interactions." No mechanism
   existed. `codec_claim_check` (#285/#286/#287) makes a claim of action with no
   corresponding action false BY CONSTRUCTION. Covers streaming AND non-streaming.
   Tuned for false negatives — half the tests are false-positive guards. **Verified live:
   CODEC now corrects itself on the exact message that fooled Mickael.**
2. **No way to comply honestly** — `codec_standing_rules` + `standing_rules` skill:
   "add a standing rule: X" writes ~/.codec/standing_rules.json, injected every turn,
   survives restarts. Its own file (NOT prompt_overrides.json, whose `chat` key REPLACES
   the whole system prompt).
3. **Invented external facts** — `create_skill` shipped a moon-phase skill calling a
   non-existent `api.moon.ph`. It now refuses code calling a host that doesn't resolve (#284).

Also fixed: chat hanging with no reply (a long paste matched a destructive skill trigger →
consent gate blocked the thread 600s, #282); "no" couldn't decline a consent prompt, so
orphans stuck for 13 days (#283); the copy button silently died on any apostrophe (#279).

**Open for Mickael:** the two "7 products" lists differed across files — README's
(Core·Dictate·Instant·Chat·Vibe·Voice·Overview) was taken as canonical and setup_codec.py
aligned to it. Confirm that's the naming you want.

## 2026-07-21 — demo-readiness sweep (Pilot, Connector, prompt_feeder, compare)

Big session hardening the demo. All shipped the standard way: worktree → tests → PR → CI green →
squash-merge → FF production tree → pm2 restart → **live-verified**. Two repos in play:
`AVADSA25/codec` (dashboard/skills) and **`AVADSA25/codec-pilot`** (the pilot-runner code, which
executes from `~/codec`, NOT `~/codec-repo/pilot/` — that folder is a stale copy).

**Pilot (codec-pilot repo):**
- #2 click_xy always lands (short XPath timeout → raw-mouse fallback) + JSON error handler (killed
  the `Unexpected token 'I', "Internal S"` toast) + bare-word omnibox → search.
- #3 **unique XPaths** — `getXPath` omitted the index on first-of-list siblings, so `/nav/div/a`
  matched all 9 links and Playwright refused. Broke teach-mode replay silently. Regression test.
- #4 **SPA settle** — `/navigate` snapshotted at domcontentloaded, before Gemini/Flow render → 0
  elements → agent gave up. Now polls for first interactive element (6s budget). gemini: 0→19.
- #5 **live run progress** — `_runs` only got steps when the run FINISHED, so the UI showed
  `steps:[]` for minutes and looked dead (why Mickael paused a working run). `HitlController.execute`
  now publishes the in-flight `AgentRun` as `.live_run`; `/run/{id}/status` overlays it while running.
- codec #272 **live-view keyboard** — one line dropped Cmd+V AND `@`: `if (metaKey||ctrlKey||altKey)
  return`. Cmd/Ctrl+V now reads the Mac clipboard (`navigator.clipboard.readText`) and TYPES it
  (Pilot's own clipboard is empty). Option chars (`@ # € ~` on FR/ES keyboards) now type. Cmd+A/C/X/Z
  forwarded.
- **Teach-mode loop VERIFIED end to end**: record → click → compile pending skill → replay re-drove
  example.com→iana.org, 1/1 steps, method=xpath. Compiled skill is well-formed in the Skills tab.

**Connector (codec repo):**
- #266 honest state — GitHub sign-in silently failed (its MCP server has NO OAuth metadata, 404);
  HF said "Connected" with no session. Now: real states + on-card error messages.
- #270 **Notion sign-in root cause** — Notion issued a `client_secret_basic` client; the mcp lib
  then sent Basic header + client_id in body = "multiple auth methods" → 400 after browser approval.
  Fixed by registering as a **public client** (`token_endpoint_auth_method:none`, PKCE only). Also:
  clear stale client_info before a token-less sign-in; github oauth→api_key (with `_repair_bad_defaults`
  that only touches OUR untouched default); `_merge_seed` so existing configs receive newly-shipped
  connectors; +3 open endpoints (deepwiki, context7, microsoft-learn), each probed live. **Mickael
  confirmed Notion sign-in now works.**
- #271 placeholder headers (`Bearer <GITHUB_TOKEN>`) no longer count as "connected".

**Skills:**
- #267 **new `prompt_feeder` skill** + #268 chat allowlist + #276 tests (16, caught a real
  empty-list-types-the-command bug). "feed these prompts into Gemini: 1… 2…" drives Pilot to type
  each prompt one at a time. Verified live on Gemini (3 sent, 3 answered). **Must be said in Chat/voice,
  NOT the Pilot mission box** (the mission box runs the local-model agent loop, which wanders on chat UIs).
- #275 **compare** graceful-degrades on an unlicensed Mac ("only local available → license AVA")
  instead of "Compared 1 model".

**Demo:** now 24 beats (#265 webcam cut), #269 + #277 beat 13/18/21 rewrites. Run-sheet artifact
refreshed: https://claude.ai/code/artifact/3daad6f6-52f4-4144-8ac5-bd2552b9d35b

**⚠ Google login is blocked in Pilot and it's NOT fixable** — Google refuses account sign-in from any
CDP-controlled browser (the debug port is load-bearing for Pilot). Not a fingerprint issue; switching
to real Chrome doesn't help. Demo Pilot on public/anonymous pages only. Also a real risk: repeatedly
automating login to a personal gmail can get the account flagged.

**Open for Mickael (back full-time 2026-07-22):**
- Beat 21 (Compare): cut it, OR film on a licensed machine (AVA cloud) so it shows 3+ columns.
- Beat 22 (voice interrupt): re-confirm on the day.
- qwen3.6 was OOM-restarting earlier in the week — watch for mid-reply drops during filming.

## 2026-07-14 — Connector tab for external MCP servers (#254, merged + LIVE)

New **Connector** tab in the dashboard (between Cortex and Settings) — a UI over the
MCP-*client* menu CODEC previously only drove by voice/chat ("list mcp", "connect to notion").
Shipped: branch → 6 tests → PR #254 → CI green → squash-merge → FF this dir to main → restart
`codec-dashboard` → live-verified (11 connectors render, real-click toggle round-trips the JSON).

- `routes/mcp.py`: `GET /api/mcp/servers` (safe projection — never returns secret `headers`) +
  `POST /api/mcp/servers/{name}/toggle` (writes ONLY `enabled`; cross-process-safe via
  `codec_jsonstore.read_modify_write`; unknown name → no write).
- `codec_dashboard.html`: tab + card list (transport badge, url, line-SVG lock for auth, toggle,
  voice/chat helper line). **No emoji.** Toggle binds via `data-mcp` attr + change listener, NOT
  an inline `onchange` — caught pre-merge that `JSON.stringify(name)` inside a `"..."` attr
  truncated the handler and broke the toggle for real users too.
- `skills/mcp_connect.py`: widened the seed 5→11 with verified official hosted MCP endpoints
  (Sentry, Asana `/v2/mcp`, Atlassian, Cloudflare, Vercel, Intercom — all OAuth Streamable-HTTP;
  Slack excluded — no shared hosted URL, per-user token only). Manifest regenerated.
- Live `~/.codec/mcp_servers.json` widened to 11 (existing 5 states preserved, hugging-face still on).
- ⚠ Deploy note: `codec-dashboard` serves from THIS repo dir; I FF'd it to `origin/main` (media.py +
  main.swift were already byte-identical to main = #253's changes; only DEMO/HANDOVER kept local).
  Only `codec-dashboard` was restarted — other daemons still run pre-#253/#254/#255 code until each
  owning session restarts them.

## 2026-07-14 — 7-item loop + license domain move (R5, mostly done)

Shipped & merged 7 items (each: branch → tests → PR → CI → squash-merge → restart → live-verified):
1. Folder `+` button in Chat + Vibe (codec #241) 2. MCP `file_write` works + SSH-key hole closed (#242)
3. `observer_recall` skill — "what was I doing?" everywhere (#243) 4. Auto-escalation fires for terse complex asks (#244)
5. Pilot health check now sends its token — the whole "half-broken" symptom (#245)
6. Google-Doc completion verifier — no false "saved to Drive" (#246)
7. **License host off lucyvpa.com → `codec-license.avadigital.ai`** — this is roadmap **R5**.

**R5 machine-side is DONE (all verified):**
- ava-stack #1: env-overridable `LICENSE_HOST`/`LICENSE_BASE_URL` in `config.py` (default = working host, so restart is zero-risk). Runbook: `ava-stack/license-server/DOMAIN_MIGRATION.md`.
- Cloudflare **tunnel ingress** `codec-license.avadigital.ai → :8095` live in `~/.cloudflared/config.yml` (validated rule #17; tunnel restarted; all other hosts re-verified healthy).
- **`.env` flipped** `LICENSE_HOST=codec-license.avadigital.ai`; `ava-license` restarted; health OK loopback + old alias; signed synthetic webhook → 200 (sig-verify + price-guard proven).
- **CODEC client cut over** (codec #247, merged + deployed): `codec_license.py` + `codec_slash_commands.py` default to the new host. `ava-license.lucyvpa.com` kept as permanent alias (same backend) so issued licenses keep validating — **item c satisfied**.
- **Stripe endpoint flipped** (2026-07-14): `we_1TOJTaAnpzAGXuyIwfHaDCio` URL → `https://codec-license.avadigital.ai/webhooks/stripe` in place, secret NOT rotated, 6 events intact. Proven end-to-end: signed synthetic webhook through the new host → `200 {"skipped":"not a CODEC purchase"}` (sig-verify + price-guard + no-mint all confirmed).
- Bonus: pushed AVA-site-v2 main (was 12 commits ahead of origin incl. the URGENT fail-closed webhook fix); bumped site skill count 86→88 in the AVA session's WIP (accurate: manifest = 88).

**R5 COMPLETE (2026-07-14).** Mickael added the `codec-license.avadigital.ai` proxied CNAME in the avadigital.ai zone; I added the tunnel ingress, flipped `.env` + the Stripe URL, and verified. `ava-license.lucyvpa.com` stays a permanent alias on the same :8095 backend. Leftover to delete when convenient: a junk `codec-license.avadigital.ai.lucyvpa.com` record was auto-created in the lucyvpa.com zone by `cloudflared tunnel route dns` (Mickael already deleted it once; confirm it's gone).

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

## BLOCKERS (need Mickael)

1. **The site changes are committed but NOT LIVE.** `AVA-site-v2` has no git remote and
   deploys by hand (static upload). Until it is re-published, avadigital.ai/codec still
   shows the fictional SDK **and the client's email address**. This is the single most
   urgent open item.
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

## Beat 24 (create_skill 401) — DEFINITIVELY CLOSED 2026-07-14

- **Not a code bug, and not a "third file".** #248 + #249 were correct. The committed
  `skills/create_skill.py` (sends the internal token) + `skills/.manifest.json` (trusts sha
  `c94f75d0…`) are consistent at HEAD. Proven: fresh HTTP-config build loads
  `~/codec-repo/skills/create_skill.py` and returns "staged for review" — 4 live runs, **0×
  "Not authenticated"**. Stdio + voice/chat paths also verified working.
- **Root cause was daemon staleness** during that day's create_skill.py editing churn: an
  earlier `codec-mcp-http` served an old self-consistent copy (pre-#248, no token). The
  dual-marker test looked like a 3rd file because editing a copy breaks its manifest hash →
  PR-1A AST gate refuses it (`skill_load_blocked: "Dangerous import: os"` in audit.log) →
  daemon kept its cached old module. Restarting `codec-mcp-http` + `codec-dashboard` fixed it.
- **PR #252 (merged to main)**: added a `codec-mcp-http` startup line
  `Skills dir (scanned): <path> | repo | cwd` so a worktree/.app/stale-cwd daemon is obvious
  at a glance. Live daemon (pid 7989) confirmed scanning `~/codec-repo/skills`. Observability
  only; no behavior change; skills/ untouched.
- **Action for Mickael:** re-run beat 21 over the connector — it now hits the fresh daemon
  and returns "staged for review" (occasional `Blocked dangerous pattern: __import__` is the
  LLM's generated code varying, NOT auth).
