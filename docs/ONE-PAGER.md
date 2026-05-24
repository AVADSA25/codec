# Sovereign AI Workstation — One-Pager

> **One line:** A voice-controlled AI agent that runs 100% on your Mac, controls
> the whole machine, and plugs into Claude / Cursor / VS Code as an MCP server —
> so any AI host can drive your computer, privately. Open source (MIT), powered
> by the **CODEC** engine. — [avadigital.ai](https://avadigital.ai) · [github.com/AVADSA25/codec](https://github.com/AVADSA25/codec)

---

## Problem

AI assistants still can't *use your computer*. ChatGPT and Claude talk; they don't
click your buttons, read your screen, or drive your apps. The agents that *do*
act (cloud "computer use", browser bots) ship your screen and keystrokes to
someone else's servers — a non-starter for regulated industries, EU privacy
regimes, and anyone handling sensitive work. And the orchestration frameworks
(CrewAI, LangChain) are heavy glue that still need *you* to wire up execution.

So the people who most want an agent that acts on their behalf — power users,
SMBs, regulated teams — are stuck choosing between **capable-but-cloud** and
**private-but-passive**.

## Solution

**Sovereign AI Workstation** turns a Mac into a private, voice-first AI workstation.
Speak to it; it sees the screen, clicks anywhere via a vision model, runs apps,
writes code, drafts messages, manages Google Workspace, and runs multi-agent
crews — all on-device by default. When it lacks a skill, it drafts one (Python)
and learns after human review.

The wedge competitors don't have: **CODEC is both an MCP client *and* an MCP
server.** It consumes tools from any MCP host, and exposes its own 76 skills,
memory, and voice pipeline back to Claude / Cursor / VS Code — even to *another*
CODEC. That makes the open protocol Anthropic standardized into agent-to-agent
plumbing, with each side keeping its data local.

**The moat is the combination** — local-first **+** voice **+** MCP-as-a-server
**+** self-writing skills **+** a ~795-line zero-dependency agent runtime. No
single competitor (Open Interpreter, Aider, Goose, Continue, Cursor) has all five.

## Why now

- **MCP standardized (late 2024)** and is now supported by Claude, Cursor, and VS
  Code. Being a first-class MCP *server* — not just a client — is the timing
  insight: the hosts exist; the private execution layer they can call does not.
- **Local LLMs hit production quality in 2025** (Qwen 3.x, Llama 3.3 on Apple
  Silicon via MLX) — a private agent is finally fast enough to be the default,
  not a compromise.
- **EU AI Act (in force 2026) + GDPR** make "where does the data go?" a buying
  question. Local-first is now a *compliance* feature, not just a values one.

## Market

- **macOS-native power users & prosumers** — developers, traders, founders,
  creators who live in their Mac and want hands-free leverage.
- **SMBs & regulated EU teams** wanting private agent infrastructure they own,
  not a cloud seat that exfiltrates their work.
- **Enterprise** procurement that requires on-device processing + an audit trail.
- Go-to-market: open-source engine for distribution & trust → paid signed Mac app
  for the people who don't want to assemble the local stack → enterprise setup.

## Traction

*(as of 2026-05-24)*

- **95 GitHub stars, 9 forks** — up from ~22 → ~51 earlier in the quarter (repo is ~2 months old, created 2026-03-24).
- **543 commits**, shipping daily; **~10 versioned CHANGELOG releases** (v1.3 → v2.3) in two months.
- **9 shipped products**, **76 skills**, **12 agent crews**, **1,300+ automated tests**, ~67K lines of Python.
- **Integrations live:** Claude / Cursor / VS Code (MCP, both directions), Google Workspace, iMessage, Telegram, Whisper STT, Kokoro TTS, local vision.
- **Depth signals investors notice:** HMAC-signed audit log, OAuth 2.1 MCP HTTP transport, Touch ID + TOTP auth, a full Phase-1 security/reliability/investor-readiness audit being burned down PR-by-PR.

## Business model

- **Open source (MIT)** — the CODEC engine stays free forever. Distribution + trust.
- **Paid Mac app (coming)** — signed, notarized, one-click install + managed setup
  + optional cloud-LLM tier for non-technical users. _[Mickael to confirm tier shape + price band.]_
- **Enterprise** — deployment, integration, custom skills, support.

## Team

- **AVA Digital LLC** — [avadigital.ai](https://avadigital.ai). _[Mickael to add founder bio: background, prior ventures, why-this-team.]_
- Currently a small core team (3 contributors on the repo) shipping at high velocity.

## The ask

_[Mickael to complete — e.g., "Raising $X pre-seed to fund the paid Mac app
launch, EU enterprise pilots, and the next 3 product pillars," or "Seeking YC
S26 + design partners among EU SMBs." Keep to one sentence.]_

---

<sub>This one-pager cites live repo metrics that drift over time; numbers are accurate as of the date noted. Confidential / personal details (founder bio, raise amount, pricing) are intentionally left as placeholders because this file lives in a public repo — fill a private copy before sending to investors. See `docs/HANDOFF-MICKAEL.md`.</sub>
