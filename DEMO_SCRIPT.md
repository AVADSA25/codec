# CODEC Live Demo — SEALED FINAL (25 beats)

> The locked recording scenario. 15 (Apr) → 22 → 25, now **sealed** and renumbered
> in **performance order** (run top to bottom). Each beat: exact **TRY** line +
> **EXPECT** + status. The 20 "hidden moats" that aren't separate beats live in
> **§ Name-drop moats** — say them *during* the beat noted.
>
> Status: ✅ verified · ⚠️ needs fix before filming · 🔑 needs Google/Notion auth ·
> 🧑 you run (voice/hardware) · 🖥️ software (Claude can pre-verify).
>
> **Last sealed:** 2026-07-14 · **Last status refresh:** 2026-07-16.

## Act 1 — It hears you, acts, and types for you

| # | Feature | TRY (live) | EXPECT | Status |
|---|---|---|---|---|
| 1 | Wake word → Calendar + Task | "Hey CODEC — add 'edit and upload demo video' to my calendar tomorrow 3pm, and to my to-do list." | Google Calendar event tmrw 15:00 + a Google Task. *(Re-open Calendar+Tasks later = the "trust receipt".)* | 🧑🔑 |
| 2 | Voice app control | "Open Time Magazine in Safari." | Safari opens time.com | 🧑 |
| 3 | **F5 Live Cursor Typing** 🆕 | Click into any text field / search bar → hold **F5** → talk normally | Your words stream in live, in place — a free local SuperWhisper (CODEC Dictate) | 🧑 |
| 4 | Instant Read Aloud (Kokoro) | Select a paragraph → Read Aloud | CODEC reads the selection in a natural voice | 🧑 |
| 5 | Vision → draft email reply | Open a client email in Gmail → "Look at my screen and draft a reply." | A polished, context-aware draft appears | 🧑🔑 |
| 6 | Instant Translate (UA→EN) | Select a Ukrainian message → Translate | Instant English in place | 🧑 |

## Act 2 — It sees and controls the machine (pure vision)

| # | Feature | TRY (live) | EXPECT | Status |
|---|---|---|---|---|
| 7 | **Vision Mouse Control (UI-TARS)** — showstopper | On Cloudflare DNS: "Click the DNS button." | Cursor moves to the button and clicks — pixel coords, no accessibility API | ✅ |
| 8 | Live webcam vision (PIP) | "CODEC, look through my webcam — what do you see?" | Describes the live view | ⚠️ works but **out of focus** — Swift fix pending |
| 9 | Think mode + reveal reasoning | Ask a logic trap (rate / car-wash) with Think on → "Reveal train of thought" | Correct answer + readable reasoning panel | ✅ |

## Act 3 — Autonomous agents (work while you watch)

| # | Feature | TRY (live) | EXPECT | Status |
|---|---|---|---|---|
| 10 | Deep Research crew | Agent mode: "Research the top 5 Notion competitors and summarize." | Crew launches, streams progress for a few minutes | ✅🖥️ |
| 11 | **Project mode** | Project: "Research Notion, Figma and Linear; draft a personalized Head-of-Growth outreach email to each; save as Gmail drafts." Approve the plan once. | Approve once → runs autonomously → 3 Gmail drafts appear | ✅ |
| 12 | Chat auto-escalation | Chat: "plan and build me a 5-page competitor report with charts." | CODEC offers "Promote to Project mode?" | ✅ *(fixed #244)* |
| 13 | **Vibe** live coding | Vibe: "Build me a snake game." | Code writes itself live in Monaco; preview plays | ✅ |
| 14 | **Pilot** — teach by doing + self-healing replay | Pilot: record "check the price of a MacBook on Amazon" once → replay | Records your clicks → compiles a zero-LLM skill → replays; self-heals if the page changed | ⚠️ **navigation ✅ confirmed live** (#245 health-token fix, #251 CSRF fix) — but the live view still isn't click-through-able (clicking it opens a static screenshot). **Rebuild in progress**: click/type/scroll straight into the headless browser. Until it lands, drive Pilot via a typed mission or the Quick Actions element-index click, not by clicking the stream. |

## Act 4 — The nerve center & oversight

| # | Feature | TRY (live) | EXPECT | Status |
|---|---|---|---|---|
| 15 | Cortex neural map | Open Cortex *(leave it on a 2nd screen all demo)* | Pulsing zones update live as tools fire | 🧑 |
| 16 | Forensic audit log (16 categories) | Open Audit → filter by category | Every vision click / clipboard hit / LLM call logged locally — zero hidden telemetry | 🧑 |
| 17 | Deep Research → real **Google Doc** | Open the doc from beat 10 | A real docs.google.com doc: report, citations, native tables | 🔑 verifier live (#246); needs one run + Google auth |

## Act 5 — Bidirectional MCP (the differentiator)

| # | Feature | TRY (live) | EXPECT | Status |
|---|---|---|---|---|
| 18 | **CODEC as MCP server** (Claude drives your Mac) | Claude Desktop (CODEC connector on): "Research the 3 best noise-cancelling headphones, then use CODEC to save the summary to my Desktop, add a calendar reminder, and dim my office lights." | Claude thinks; CODEC acts locally: file saved, reminder set, lights dim | 🧑 set the CODEC connector to **Always Allow** in Claude's settings first (done) — was blocking on a per-call approval prompt, not a CODEC bug |
| 19 | **CODEC as MCP client / Connector tab** (CODEC drives other servers) | Connector tab: toggle a server on → **Sign in** → say "list tools on notion" | Card shows **Connected**, with a **Disconnect** option; CODEC lists the server's tools | ⚠️ **OAuth sign-in itself works** (confirmed live with Notion) but the tab doesn't show connected/disconnect state and tokens don't survive a restart (in-memory only). **Full rebuild in progress**: proper inactive → sign-in → Connected → Disconnect states + persistent Keychain-backed tokens + real styling. |
| 20 | Observer recall | "Hey CODEC, what was I doing 20 minutes ago?" | Recalls Safari/Time, the Gmail draft, etc. from its buffer | ✅ **fully fixed** — routing (#256), real capture via NSWorkspace instead of the TCC-blocked osascript path (#258), and it now tells you honestly when a question ("today between 11am and 1pm") is outside its ~10-min rolling window instead of guessing (#260) |

## Act 6 — Self-improvement, voice duplex, finale

| # | Feature | TRY (live) | EXPECT | Status |
|---|---|---|---|---|
| 21 | **Self-improve live** | "CODEC, create a new skill that tells me the current moon phase." | CODEC **talks first** — briefs what it's about to build and asks "Build it?" — then on confirm generates it, stages it in the Skills tab, and explains the outcome in chat | ✅ **fully conversational now** (#257) — no more silent builds. Approve the staged skill in the **Skills** tab to activate it. *(External Claude-MCP-connector path to create_skill still 401s — separate non-demo bug, handed off.)* |
| 22 | **Compare across models** | "Compare across models: what's the best programming language for a beginner?" | Side-by-side answers from multiple models | ⚠️ only local answered on dev Mac — **verify on licensed machine** |
| 23 | Voice call + interrupt (RMS duplex) | Start a voice call → interrupt CODEC mid-sentence | It cuts instantly — true duplex, not turn-based | 🧑 worked once already — re-confirm on the day |
| 24 | Phone Touch ID remote | On phone at `codec.avadigital.ai` → Touch ID → issue a command | Mac Studio at home executes it | 🧑 |
| 25 | **FINALE** — Hue + Spotify | "CODEC, turn off the lights." then "CODEC, play music on Spotify." | Lights dim, music starts. Cut. | 🧑 |

## § Name-drop moats — say these *during* the beat (the hidden gems, no extra action)

| Moat | Say it during | One-liner |
|---|---|---|
| **Five-pillar moat** | Opening | "Local-first + voice + MCP server *and* client + self-writing skills + zero-dependency runtime — no other project combines all five." |
| Pure-vision (UI-TARS) | 7 | "No accessibility APIs — it screenshots, a vision model finds pixel coords, it clicks like a human eye. Works on any legacy app or Flash frame." |
| 3-agent zero-dep concurrency | 10 | "Up to 3 background agents on a custom sub-800-line thread pool — no LangChain, no CrewAI." |
| Plan-hash tamper + R/W grants | 11 | "The plan is sha256-hashed on approval — if the agent alters its own goals mid-run it auto-aborts. Reads and writes are glob-sandboxed to `~/codec-projects/`." |
| Self-healing replay + HITL takeover | 14 | "Replay falls back XPath → CSS → local-LLM rescue; and if it hits a captcha you pause, take the wheel through the live stream, and hand back." |
| CCF compression + temporal memory | 15 / 20 | "~65% token reduction; an FTS5 SQLite fact store with valid_from/valid_until — tell it your plans changed and it supersedes the old fact." |
| Watchdog + blocked_on_qwen recovery | 15 / 16 | "A watchdog kills zombie >500MB/<0.5%-CPU processes; if the local model drops mid-project, the agent auto-resumes the moment the port recovers." |
| AppKit overlay over fullscreen | any notification | "A native NSPanel floats status over *any* fullscreen app — watch it appear while I'm full-screen." |
| Proactive nudges → iMessage/Telegram | 20 | "The observer can nudge you — a doc you've dwelled on — to a macOS banner, iMessage, or Telegram. The desktop agent reaches your pocket." |
| Ed25519 signed self-updates | settings | "Updates verify an Ed25519-signed Sparkle appcast against an embedded key — hardened from execution all the way to updates." |

## Fix-before-filming (2 rebuilds in progress, 1 hardware check)
- **#14 Pilot live view** — click/type/scroll straight into the browser stream (currently click-through opens a screenshot instead of controlling the page). Building.
- **#19 Connector tab** — connected/disconnect state + persistent tokens (OAuth itself already works). Building.
- **#8 webcam** — exposure fix shipped, but the softness looks like the **Anker C200 lens/hardware** itself, not code. Check the lens is clean/unobstructed before re-testing.
- *(Fully done since last cut: #12 auto-escalation, #14-navigation, #17 GDoc verifier, #20 observer recall — routing + real capture + honesty on out-of-range asks, #21 self-improve now conversational.)*

## Pre-record checklist
- Chrome tabs L→R: `opencodec.org` → GitHub → Gmail → WhatsApp Web → Cloudflare → CODEC Chat
- An empty text field / Notes open for the **F5 live-typing** beat (3)
- Safari: Time Magazine article open
- Claude Desktop: CODEC MCP connector active (Act 5)
- Phone: `codec.avadigital.ai` loaded, Touch ID ready
- Notion connector signed in already (Connector tab); Philips Hue reachable; Spotify authorized (finale)
- Claude Desktop: CODEC connector set to **Always Allow** (Settings → Connectors) so beat 18 doesn't stall on a per-call approval prompt
- Vision Mouse tested 10× on the Cloudflare DNS button
- Observer running with a populated buffer
- Cortex map open on a 2nd screen (persistent visual anchor)
- Fresh Deep-Research + Project chat windows open

## Director's notes
- Act 1 is the emotional hook — promise (1), deliver, and F5 (3) is an unexpected early "wow".
- Showstopper risk: 7 (Vision Mouse) — if it fails live, skip to 9.
- Core "works while you watch" trio: 11 Project, 13 Vibe, 14 Pilot.
- Act 5 is THE differentiator: 18 (server) + 19 (client) back-to-back = bidirectional MCP.
- Save Hue + Spotify (25) for the very last frame.
