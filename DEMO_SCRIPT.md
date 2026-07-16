# CODEC Live Demo — SEALED FINAL (24 beats)

> The locked recording scenario. 15 (Apr) → 22 → 25 → **24** (webcam cut). Sealed
> and renumbered in **performance order** (run top to bottom). Each beat: exact
> **TRY** line + **EXPECT** + status. The "hidden moats" that aren't separate
> beats live in **§ Name-drop moats** — say them *during* the beat noted.
>
> Status: ✅ verified · ⚠️ needs a decision/check before filming ·
> 🔑 needs Google auth · 🧑 you run (voice/hardware).
>
> **Last sealed:** 2026-07-16.

## Act 1 — It hears you, acts, and types for you

| # | Feature | TRY (live) | EXPECT | Status |
|---|---|---|---|---|
| 1 | Wake word → Calendar + Task | "Hey CODEC — add 'edit and upload demo video' to my calendar tomorrow 3pm, and to my to-do list." | Google Calendar event tmrw 15:00 + a Google Task. *(Re-open Calendar+Tasks later = the "trust receipt".)* | 🧑🔑 |
| 2 | Voice app control | "Open Time Magazine in Safari." | Safari opens time.com | 🧑 |
| 3 | **F5 Live Cursor Typing** | Click into any text field / search bar → hold **F5** → talk normally | Your words stream in live, in place — a free local SuperWhisper (CODEC Dictate) | 🧑 |
| 4 | Instant Read Aloud (Kokoro) | Select a paragraph → Read Aloud | CODEC reads the selection in a natural voice | 🧑 |
| 5 | Vision → draft email reply | Open a client email in Gmail → "Look at my screen and draft a reply." | A polished, context-aware draft appears | 🧑🔑 |
| 6 | Instant Translate (UA→EN) | Select a Ukrainian message → Translate | Instant English in place | 🧑 |

## Act 2 — It sees and controls the machine (pure vision)

| # | Feature | TRY (live) | EXPECT | Status |
|---|---|---|---|---|
| 7 | **Vision Mouse Control (UI-TARS)** — showstopper | On Cloudflare DNS: "Click the DNS button." | Cursor moves to the button and clicks — pixel coords, no accessibility API | ✅ |
| 8 | Think mode + reveal reasoning | Ask a logic trap (rate / car-wash) with Think on → "Reveal train of thought" | Correct answer + readable reasoning panel | ✅ |

## Act 3 — Autonomous agents (work while you watch)

| # | Feature | TRY (live) | EXPECT | Status |
|---|---|---|---|---|
| 9 | Deep Research crew | Agent mode: "Research the top 5 Notion competitors and summarize." | Crew launches, streams progress for a few minutes | ✅ |
| 10 | **Project mode** | Project: "Research Notion, Figma and Linear; draft a personalized Head-of-Growth outreach email to each; save as Gmail drafts." Approve the plan once. | Approve once → runs autonomously → 3 Gmail drafts appear | ✅ |
| 11 | Chat auto-escalation | Chat: "plan and build me a 5-page competitor report with charts." | CODEC offers "Start as Project?" + asks the clarifying questions | ✅ |
| 12 | **Vibe** live coding | Vibe: "Build me a snake game." | Code writes itself live in Monaco; preview plays | ✅ |
| 13 | **Pilot** — teach by doing + self-healing replay | Pilot tab → **click directly on the live view** to drive the browser → Record → replay | You click what you see; CODEC compiles those clicks into a reusable skill and replays it, self-healing if the page changed | ✅ click-through live (verified: a click at (297,207) resolved to the link and navigated example.com → iana.org) |

## Act 4 — The nerve center & oversight

| # | Feature | TRY (live) | EXPECT | Status |
|---|---|---|---|---|
| 14 | Cortex neural map | Open Cortex *(leave it on a 2nd screen all demo)* | Pulsing zones update live as tools fire | 🧑 |
| 15 | Forensic audit log (16 categories) | Open Audit → filter by category | Every vision click / clipboard hit / LLM call logged locally — zero hidden telemetry | 🧑 |
| 16 | Deep Research → real **Google Doc** | Open the doc from beat 9 | A real docs.google.com doc: report, citations, native tables | ✅ confirmed working |

## Act 5 — Bidirectional MCP (the differentiator)

| # | Feature | TRY (live) | EXPECT | Status |
|---|---|---|---|---|
| 17 | **CODEC as MCP server** (Claude drives your Mac) | Claude Desktop (CODEC connector on): "Research the 3 best noise-cancelling headphones, then use CODEC to save the summary to my Desktop, add a calendar reminder, and dim my office lights." | Claude thinks; CODEC acts locally: file saved, reminder set, lights dim | ✅ confirmed working (connector set to **Always Allow**) |
| 18 | **Connector tab** (CODEC drives other MCP servers) | Connector tab → toggle Notion on → **Sign in** → say "list tools on notion" | Card flips to **● Connected** with a **Disconnect** option; CODEC lists Notion's tools | ✅ full state machine + Keychain-persistent tokens. **Sign in once more** — the old token was in-memory and is gone; this one survives restarts |
| 19 | Observer recall | "Hey CODEC, what was I doing 20 minutes ago?" | "Over the last 9 minutes, you were in Claude the whole time. You touched 4 files: …" — conversational, and honest if you ask outside its ~10-min window | ✅ |

## Act 6 — Self-improvement, voice duplex, finale

| # | Feature | TRY (live) | EXPECT | Status |
|---|---|---|---|---|
| 20 | **Self-improve live** | "CODEC, create a new skill that tells me the current moon phase." | CODEC briefs what it'll build and asks "Build it?" → generates → stages it in the **Skills** tab → explains the outcome. Approve it there to activate | ✅ conversational |
| 21 | **Compare across models** | "Compare across models: what's the best programming language for a beginner?" | Side-by-side answers from several models | ⚠️ **DECIDE** — on the dev Mac only the local model answered, so it just reads like a normal chat reply. Only keep this beat if the demo machine actually shows 3+ models side by side; otherwise cut it — it makes no point with one column |
| 22 | Voice call + interrupt (RMS duplex) | Start a voice call → interrupt CODEC mid-sentence | It cuts instantly — true duplex, not turn-based | 🧑 worked once — re-confirm on the day |
| 23 | Phone Touch ID remote | On phone at `codec.avadigital.ai` → Touch ID → issue a command | Mac Studio at home executes it | 🧑 |
| 24 | **FINALE** — Hue + Spotify | "CODEC, turn off the lights." then "CODEC, play music on Spotify." | Lights dim, music starts. Cut. | 🧑 |

## § Name-drop moats — say these *during* the beat (the hidden gems, no extra action)

| Moat | Say it during | One-liner |
|---|---|---|
| **Five-pillar moat** | Opening | "Local-first + voice + MCP server *and* client + self-writing skills + zero-dependency runtime — no other project combines all five." |
| Pure-vision (UI-TARS) | 7 | "No accessibility APIs — it screenshots, a vision model finds pixel coords, it clicks like a human eye. Works on any legacy app or Flash frame." |
| 3-agent zero-dep concurrency | 9 | "Up to 3 background agents on a custom sub-800-line thread pool — no LangChain, no CrewAI." |
| Plan-hash tamper + R/W grants | 10 | "The plan is sha256-hashed on approval — if the agent alters its own goals mid-run it auto-aborts. Reads and writes are glob-sandboxed to `~/codec-projects/`." |
| Self-healing replay + HITL takeover | 13 | "Your click is recorded as an element, not a pixel — so replay survives the page moving. It falls back XPath → CSS → local-LLM rescue; hit a captcha and you take the wheel, then hand back." |
| CCF compression + temporal memory | 14 / 19 | "~65% token reduction; an FTS5 SQLite fact store with valid_from/valid_until — tell it your plans changed and it supersedes the old fact." |
| Watchdog + blocked_on_qwen recovery | 14 / 15 | "A watchdog kills zombie >500MB/<0.5%-CPU processes; if the local model drops mid-project, the agent auto-resumes the moment the port recovers." |
| AppKit overlay over fullscreen | any notification | "A native NSPanel floats status over *any* fullscreen app — watch it appear while I'm full-screen." |
| Proactive nudges → iMessage/Telegram | 19 | "The observer can nudge you — a doc you've dwelled on — to a macOS banner, iMessage, or Telegram. The desktop agent reaches your pocket." |
| Ed25519 signed self-updates | settings | "Updates verify an Ed25519-signed Sparkle appcast against an embedded key — hardened from execution all the way to updates." |

## Cut from the demo
- **Live webcam vision** (ex-beat 8) — removed 2026-07-16. The capture was soft (the Anker C200's lens, not code) and it landed as a gadget rather than a capability. Nothing else depends on it.

## Decide before filming
- **Beat 18** — sign in to Notion once in the Connector tab (it persists now).
- **Beat 21 (Compare)** — does the demo machine show 3+ models side by side? If not, cut it.
- **Beat 22** — re-confirm voice interrupt.

## Pre-record checklist
- Chrome tabs L→R: `opencodec.org` → GitHub → Gmail → WhatsApp Web → Cloudflare → CODEC Chat
- An empty text field / Notes open for the **F5 live-typing** beat (3)
- Safari: Time Magazine article open
- Claude Desktop: CODEC connector active **and set to Always Allow** (Act 5) — otherwise beat 17 stalls on a per-call approval prompt
- Phone: `codec.avadigital.ai` loaded, Touch ID ready
- Notion signed in (Connector tab, beat 18); Philips Hue reachable; Spotify authorized (finale)
- Vision Mouse tested 10× on the Cloudflare DNS button
- Observer running with a populated buffer
- Cortex map open on a 2nd screen (persistent visual anchor)
- Fresh Deep-Research + Project chat windows open

## Director's notes
- Act 1 is the emotional hook — promise (1), deliver, and F5 (3) is an unexpected early "wow".
- Showstopper risk: 7 (Vision Mouse) — if it fails live, skip to 8.
- Core "works while you watch" trio: 10 Project, 12 Vibe, 13 Pilot.
- Act 5 is THE differentiator: 17 (server) + 18 (client) back-to-back = bidirectional MCP.
- Save Hue + Spotify (24) for the very last frame.
