# CODEC — Privacy & Data Handling

> **Plain-English summary:** CODEC is **local-first**. By default, everything — your voice, your screen, your files, your conversation memory — is processed **on your Mac** and never leaves it. Data only leaves your machine when *you* turn on a feature that needs the network (a cloud LLM, a messaging bridge, web search, the remote dashboard), and this document says exactly what each of those sends and to whom.
>
> ⚠️ **Not legal advice.** This is a good-faith engineering disclosure. Before the paid tier launches, it should be reviewed by a privacy lawyer (tracked in `docs/HANDOFF-MICKAEL.md`).

---

## 1. What stays on your Mac (the default)

These are processed locally and never transmitted unless you explicitly route them through a cloud feature in §2:

| Data | Where it's processed | Where it's stored | Retention |
|---|---|---|---|
| Microphone audio (voice, dictation, wake word) | local Whisper STT | not persisted (transient buffers) | discarded after transcription |
| Screen captures / OCR ("check my screen") | local `screencapture` + Vision/Qwen-VL | temp file, deleted after OCR | per-use; nothing kept |
| Conversation + task memory | SQLite | `~/.codec/memory.db` | until you delete; `cleanup(retention_days=90)` available |
| Audit log (every action CODEC takes) | local | `~/.codec/audit.log` (0600, HMAC-signed, secrets redacted) | 30-day rotation |
| Skills, plugins, agent workspaces | local | `~/.codec/` | until you delete |
| Clipboard, recent-file metadata, idle time (observer) | local, RAM only | `collections.deque`, wiped on restart | ≤10 min ring buffer |

The local LLM (Qwen via MLX), STT (Whisper), and TTS (Kokoro) all run on-device.

## 2. What leaves your Mac — only when you enable it

Each of these is **off by default** or requires explicit configuration. When active, here's the data flow:

| Feature | What's sent | To whom | When |
|---|---|---|---|
| **Cloud LLM fallback** (paid tier) | your prompt + relevant context + tool outputs | Anthropic (Claude) / OpenAI (GPT) via the AVA proxy (`ava-proxy.lucyvpa.com`) | only if you select a cloud model |
| **MCP HTTP transport** (claude.ai connector) | prompts + tool inputs/outputs for the tools claude.ai invokes | Anthropic (claude.ai) over a Cloudflare tunnel | only while you've connected claude.ai |
| **Web search** | your search query | DuckDuckGo and/or Serper (if a Serper key is set) | per search-skill call |
| **Google Workspace** (Docs/Sheets/Gmail/Calendar/etc.) | the content of the operation you request | Google APIs (OAuth, your own account) | per Google-skill call |
| **iMessage / Telegram bridges** | the message you send + the reply | Apple / Telegram (your own accounts) | per outbound message |
| **Twilio bridge** (if configured) | SMS content + recipient number | Twilio | per outbound SMS |
| **Remote PWA / voice from phone** | dashboard traffic | your own Cloudflare tunnel (Zero Trust auth) → your Mac | only while the tunnel runs |
| **License check** (paid tier) | license key (JWT) | AVA license server (`ava-license.lucyvpa.com`) | periodic validation |

CODEC does **not** include analytics, telemetry, tracking SDKs, or ad identifiers. The macOS `PrivacyInfo.xcprivacy` manifest (`packaging/macos/`) declares `NSPrivacyTracking=false` and no collected data types.

## 3. Third-party processors (when the relevant feature is used)

DuckDuckGo / Serper (search) · Cloudflare (tunnel transport) · Anthropic and/or OpenAI (cloud LLM, only if configured) · Google (Workspace OAuth) · Apple (iMessage) · Telegram · Twilio (if configured) · AVA Digital LLC (license + LLM proxy for the paid tier). Each processes only the data the corresponding feature sends, per §2.

## 4. GDPR (paid tier) — Art. 13 disclosures

- **Controller:** AVA Digital LLC (contact: `privacy@avadigital.ai`).
- **Categories of personal data:** account/license identifiers; any personal data *you* choose to include in prompts, messages, or files you ask CODEC to act on. CODEC does not independently collect special-category data.
- **Legal basis:** performance of the service contract (Art. 6(1)(b)) for license + paid features; legitimate interest (Art. 6(1)(f)) for local operation; consent (Art. 6(1)(a)) for any optional cloud feature you enable.
- **Recipients:** the processors in §3, only for the features you activate.
- **International transfers:** cloud LLM / proxy may process data in the US (Anthropic/OpenAI) — covered by the relevant SCCs/DPA where applicable.
- **Retention:** local data per §1 (you control deletion); license records per the AVA backend's policy.
- **Your rights:** access, rectification, erasure, restriction, portability, and objection — exercise via `privacy@avadigital.ai`. Most local data you can also delete directly (`~/.codec/`, the dashboard's data controls).

## 5. EU AI Act — Art. 50 transparency

CODEC is an AI system that interacts with you directly. You are always informed you are interacting with an AI (it is the explicit purpose of the product). CODEC does not generate deep-fakes or manipulate users; agent actions that touch your files, apps, or external services are gated behind explicit consent (strict-consent for destructive operations) and recorded in the local audit log.

## 6. Children
CODEC is not directed at children under 16 and should not be used to process children's personal data.

## 7. Changes
Material changes to this policy will be noted in `CHANGELOG.md` and dated here. Last updated: 2026-05-24.
