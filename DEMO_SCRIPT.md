# CODEC Live Demo — Full Flow (25 beats)

> The locked recording scenario. Originally 15 steps (April 2026), expanded to 22,
> now 25. Each beat has an exact **TRY** line to run live and an **EXPECT** so you
> can confirm or flag it. Status legend:
> ✅ done/works · ⚠️ half-built (fix before filming) · 🟡 seen once, re-confirm ·
> 🔴 never run · ❌ not real yet (build before filming) · 🆕 new beat.

## Act 1 — It hears you and keeps its word (trust loop)

| # | Feature | TRY (live) | EXPECT | Status |
|---|---|---|---|---|
| 1 | Wake word + Calendar + Tasks | "Hey CODEC — add 'edit and upload demo video' to my calendar tomorrow 3pm, and to my to-do list." | Event on Google Calendar tomorrow 15:00 + a Google Task, both created | 🟡 |
| 2 | Voice app control | "Open Time Magazine in Safari." | Safari opens time.com | 🟡 |
| 3 | Instant Read Aloud (Kokoro TTS) | Select a paragraph → trigger Read Aloud | CODEC reads the selection in a natural voice | 🟡 |
| 4 | Vision + draft reply | Open a client email in Gmail → "Look at my screen and draft a reply." | A polished, context-aware draft appears | 🟡 |
| 5 | Trust receipt | Open Calendar + Tasks | The event + task from beat 1 are really there | 🟡 |
| 6 | Instant Translate (UA→EN) | Select a Ukrainian WhatsApp message → Translate | Instant English translation in place | 🟡 |

## Act 2 — It sees and controls the machine

| # | Feature | TRY (live) | EXPECT | Status |
|---|---|---|---|---|
| 7 | **Vision Mouse Control** (showstopper) | On Cloudflare DNS: "Click the DNS button." | Cursor moves to the button and clicks it | ✅ done, worked |
| 8 | Live webcam access (PIP) | "CODEC, look through my webcam — what do you see?" | CODEC describes the live webcam view | 🔴 |
| 9 | Think mode + reveal | Ask a logic prompt (rate-trap / car-wash) with Think on → click "Reveal train of thought" | Correct answer + a readable reasoning panel | ✅ |

## Act 3 — Autonomous & agentic (works while you watch)

| # | Feature | TRY (live) | EXPECT | Status |
|---|---|---|---|---|
| 10 | Deep Research crew | Agent mode: "Research the top 5 Notion competitors and summarize." | "Runs a few minutes" — crew launches, streams progress | 🟡 |
| 11 | **Project mode** | Project: "Research Notion, Figma and Linear; draft a personalized Head-of-Growth outreach email to each; save them as Gmail drafts." Approve the plan once. | Approve once → runs autonomously → 3 Gmail drafts appear | ✅ verified |
| 12 | Chat auto-escalation | In chat, type a big multi-step ask (e.g. "plan and build me a 5-page competitor report with charts"). | CODEC offers "Promote to Project mode?" | 🔴 |
| 13 | **Vibe** live coding | Vibe: "Build me a snake game." | Code writes itself live in Monaco; preview plays | ✅ |
| 14 | **Pilot** — teach & replay | Pilot: record "check the price of a MacBook on Amazon" once → replay | Records the run, compiles a reusable skill, replays it | ⚠️ half-broken — FIX FIRST |

## Act 4 — The nerve center & the payoff

| # | Feature | TRY (live) | EXPECT | Status |
|---|---|---|---|---|
| 15 | Cortex neural map | Open Cortex | Pulsing zones; narrate each subsystem | 🟡 |
| 16 | Audit log (16 categories) | Open Audit → filter by category | Every action from the demo is logged | 🟡 |
| 17 | Deep Research → **Google Doc** | Open the doc from beat 10 | A real Google Doc: report, citations, native tables | ❌ only writes local .md — BUILD FIRST |

## Act 5 — Claude gets superpowers + bidirectional MCP

| # | Feature | TRY (live) | EXPECT | Status |
|---|---|---|---|---|
| 18 | **CODEC as MCP server** (for Claude) | In Claude Desktop (CODEC connector on): "Research the 3 best noise-cancelling headphones, then use CODEC to save the summary to my Desktop, add a calendar reminder, and dim my office lights." | Claude thinks, CODEC acts locally: file saved, reminder set, lights dim | 🔴 |
| 23 | **CODEC as MCP client** 🆕 (bidirectional punch — right after 18) | "CODEC, connect to my [Notion] MCP and create a page titled 'Demo notes'." | CODEC calls out to the external MCP server and does it | 🆕 BUILD |
| 19 | Observer recall | "Hey CODEC, what was I doing 20 minutes ago?" | Recalls Safari/Time, the Gmail draft, etc. from its buffer | 🟡 |

## Act 6 — Voice, self-improvement, and the finale

| # | Feature | TRY (live) | EXPECT | Status |
|---|---|---|---|---|
| 24 | **Self-improve live** 🆕 | "CODEC, create a new skill that tells me the current moon phase." | CODEC writes itself a new skill on camera, then uses it | 🆕 |
| 25 | **Compare** 🆕 | "Compare across models: what's the best programming language for a beginner?" | Side-by-side answers from multiple models | 🆕 |
| 20 | Voice call + interrupt | Start a voice call → interrupt CODEC mid-sentence | It cuts instantly (proves real-time control) | 🔴 |
| 21 | Phone Touch ID remote | On phone at `codec.avadigital.ai` → Touch ID → issue a command | Mac Studio at home executes it | 🔴 |
| 22 | **FINALE** — Hue + Spotify | "CODEC, turn off the lights." then "CODEC, play music on Spotify." | Lights dim, music starts. Cut. | 🔴 |

## Build-before-filming (not test items — make them real)
- **#14 Pilot** — half-broken; fix the teach/replay.
- **#17 Google Doc** — currently writes a local `.md`; build the real Drive-doc deliverable + a completion verifier.
- **#23 MCP client** — new capability (CODEC → other MCP servers).
- **#24 / #25** — wire the demo phrasing (skills exist: `create_skill`, `self_improve`, `compare`).

## Test order (resume here)
Software beats I can help verify headlessly: **11 ✅, 13 ✅**, then **10, 12, 23, 24, 25, 17**.
Hardware/voice beats only Mickael can run: **1–6, 7 ✅, 8, 9 ✅, 18, 19, 20, 21, 22.**

## Pre-Record Checklist
- Chrome tabs L→R: `opencodec.org` → GitHub → Gmail → WhatsApp Web → Cloudflare → CODEC Chat
- Safari: Time Magazine article open
- Claude Desktop: CODEC MCP connector active (Act 5)
- Phone: `codec.avadigital.ai` loaded, Touch ID ready
- Philips Hue reachable; Spotify authorized (finale)
- Vision Mouse tested 10× on the Cloudflare DNS button
- Observer running with a buffer
- Fresh Deep-Research + Project chat windows open

## Director's Notes
- Trust loop (1→5) is the emotional hook — promise, then deliver.
- Showstopper risk: 7 (Vision Mouse) — if it fails live, skip to 9.
- Core wow: the "works while you watch" trio (11 Project, 13 Vibe, 14 Pilot).
- Act 5 is the differentiator: 18 (server) + 23 (client) back-to-back = bidirectional MCP.
- Save Hue + Spotify (22) for the very last frame.
